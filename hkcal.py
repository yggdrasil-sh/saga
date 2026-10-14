"""Minimaler CalDAV-Client fuer Saga (Nextcloud, mailcow/SOGo, Radicale, iCloud, ...).

Sicherheit: Der Server ruft Adressen ab, die ein Benutzer eingegeben hat. Damit darueber keine internen
Dienste erreichbar sind (SSRF), wird JEDE Adresse geprueft - die eingegebene, jede Umleitung und jeder
Verweis (href) aus Server-Antworten: nur https (Tests: optional http) und nur Hosts, die HOST_OK erlaubt
(Standard: oeffentliche Adressen; interne nur mit Freigabe im Admin-Bereich - setzt saga/calendars.py).
Dazu Zeit- und Groessenlimits. Geschrieben wird nur mit ETag (If-Match / If-None-Match).
"""
import os
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from saga import netguard
from saga.i18n import t

_ENV_HOSTS = {h.strip().lower() for h in os.environ.get("CALDAV_ALLOWED_HOSTS", "").split(",") if h.strip()}
HOST_OK = lambda host, hostport: host in _ENV_HOSTS or hostport in _ENV_HOSTS      # noqa: E731 - saga/calendars.py setzt die echte Pruefung
# Vom Admin freigegeben (interne Adressen erlaubt)? Ohne DNS - fuer die Pruefung beim Verbinden (saga/calendars.py setzt sie)
HOST_FREE = lambda host, hostport: host in _ENV_HOSTS or hostport in _ENV_HOSTS     # noqa: E731
SCHEMES = {"https", "http"} if os.environ.get("CALDAV_ALLOW_HTTP") == "1" else {"https"}   # http nur fuer Tests
TIMEOUT, MAX_BYTES, MAX_REDIRECTS = 12.0, 5 * 1024 * 1024, 3
NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav", "a": "http://apple.com/ns/ical/", "cs": "http://calendarserver.org/ns/"}


_local = threading.local()


def _ip_ok(ip, host, port):
    """Beim Verbinden: oeffentliche IP oder vom Admin freigegebener Host (gegen DNS-Rebinding nach check_url)."""
    host = (host or "").lower()
    return netguard.is_public(ip) or HOST_FREE(host, f"{host}:{port}") or HOST_FREE(host, host)


def _client():
    """Eine Verbindung je Thread wiederverwenden (Keep-Alive): spart den TLS-Aufbau bei jeder Anfrage.
    Verbunden wird nur mit der beim Aufbau gepruefften IP (saga/netguard.py)."""
    c = getattr(_local, "client", None)
    if c is None:
        c = _local.client = netguard.guarded_client(_ip_ok, timeout=TIMEOUT, follow_redirects=False)
    return c


class CalError(Exception):
    """Fehler mit verstaendlicher Meldung fuer den Benutzer."""


def check_url(url):
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    hostport = host + (f":{p.port}" if p.port else "")
    if p.scheme not in SCHEMES:
        raise CalError(t("Nur https-Adressen sind erlaubt"))
    if p.username or p.password:
        raise CalError(t("Zugangsdaten gehören nicht in die Adresse"))
    if not host or not HOST_OK(host, hostport):
        raise CalError(t("Server „{host}“ ist nicht erreichbar oder nicht freigegeben (interne Server gibt der Admin frei)", host=host))
    return url


def _req(method, url, auth, body=None, headers=None):
    """Anfrage ohne automatische Umleitungen; jede Umleitung wird selbst geprueft."""
    h = {"User-Agent": "Saga-CalDAV/1"}
    h.update(headers or {})
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        try:
            if True:
                cl = _client()
                with cl.stream(method, url, auth=auth, content=body, headers=h) as r:
                    data = b""
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            raise CalError(t("Antwort des Kalender-Servers zu groß"))
                    status, loc, rh = r.status_code, r.headers.get("location"), dict(r.headers)
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError, httpx.ConnectError) as ex:
            # Gegenseite hat die offene Verbindung geschlossen -> einmal mit frischer Verbindung wiederholen
            _local.client = None
            if getattr(_local, "retried", False):
                _local.retried = False
                raise CalError(t("Kalender-Server nicht erreichbar ({why})", why=type(ex).__name__))
            _local.retried = True
            continue
        except httpx.HTTPError as ex:
            raise CalError(t("Kalender-Server nicht erreichbar ({why})", why=type(ex).__name__))
        _local.retried = False
        if status in (301, 302, 303, 307, 308) and loc:
            url = urljoin(url, loc)
            continue
        if status == 401:
            raise CalError(t("Anmeldung abgelehnt – Benutzer oder App-Passwort falsch"))
        if status == 403:
            raise CalError(t("Keine Berechtigung (App-Passwort mit Kalender-/DAV-Recht?)"))
        return status, data, rh, url
    raise CalError(t("Zu viele Umleitungen"))


def _xml(data):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise CalError(t("Unerwartete Antwort des Kalender-Servers"))


def _propfind(url, auth, props, depth="0"):
    body = ('<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
            'xmlns:a="http://apple.com/ns/ical/" xmlns:cs="http://calendarserver.org/ns/"><d:prop>' + props + '</d:prop></d:propfind>')
    status, data, _, final = _req("PROPFIND", url, auth, body.encode(), {"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
    if status not in (200, 207):
        raise CalError(t("Kalender-Server antwortet mit {status}", status=status))
    return _xml(data), final


def _href(base, el):
    """Verweis aus einer Antwort -> absolute, GEPRUEFTE Adresse."""
    if el is None or not (el.text or "").strip():
        return None
    return check_url(urljoin(base, el.text.strip()))


def discover(base_url, user, password):
    """Kalender des Kontos finden: Principal -> Kalender-Home -> Kalender (nur solche mit Terminen)."""
    auth = (user, password)
    base_url = check_url(base_url if base_url.endswith("/") else base_url + "/")
    root, final = _propfind(base_url, auth, "<d:current-user-principal/><c:calendar-home-set/>")
    home = _href(final, root.find(".//c:calendar-home-set/d:href", NS))
    if not home:
        principal = _href(final, root.find(".//d:current-user-principal/d:href", NS))
        if principal:
            proot, pfinal = _propfind(principal, auth, "<c:calendar-home-set/>")
            home = _href(pfinal, proot.find(".//c:calendar-home-set/d:href", NS))
    home = home or final
    root, final = _propfind(home, auth, "<d:resourcetype/><d:displayname/><a:calendar-color/>"
                                        "<c:supported-calendar-component-set/><d:current-user-privilege-set/>", depth="1")
    cals = []
    for resp in root.findall("d:response", NS):
        if resp.find(".//d:resourcetype/c:calendar", NS) is None:
            continue
        comps = [c.get("name") for c in resp.findall(".//c:supported-calendar-component-set/c:comp", NS)]
        if comps and "VEVENT" not in comps:
            continue
        href = _href(final, resp.find("d:href", NS))
        privs = [p.tag.split("}")[-1] for p in resp.findall(".//d:current-user-privilege-set/d:privilege/*", NS)]
        name = (resp.findtext(".//d:displayname", default="", namespaces=NS) or "").strip() or href.rstrip("/").rsplit("/", 1)[-1]
        color = (resp.findtext(".//a:calendar-color", default="", namespaces=NS) or "").strip()[:7]
        cals.append({"href": href, "name": name[:80], "color": color if color.startswith("#") else "",
                     "writable": not privs or any(p in ("write", "write-content", "all") for p in privs)})
    if not cals:
        raise CalError(t("Keine Kalender gefunden"))
    return cals


def _ical():
    import icalendar   # erst bei Bedarf laden (schnellerer Start)
    return icalendar


def _as_dt(v, tz):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=tz)
    return datetime.combine(v, dtime.min, tz)


def events(cal_url, user, password, start, end, tz=timezone.utc):
    """Termine (inkl. aufgeloester Serientermine) im Zeitraum [start, end)."""
    import recurring_ical_events
    ical = _ical()
    check_url(cal_url)
    fmt = lambda d: d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    body = ('<?xml version="1.0" encoding="utf-8"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{fmt(start)}" end="{fmt(end)}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
    status, data, _, final = _req("REPORT", cal_url, (user, password), body.encode(), {"Depth": "1", "Content-Type": "application/xml; charset=utf-8"})
    if status not in (200, 207):
        raise CalError(t("Termine nicht lesbar ({status})", status=status))
    out = []
    for resp in _xml(data).findall("d:response", NS):
        raw = resp.findtext(".//c:calendar-data", default="", namespaces=NS)
        if not raw.strip():
            continue
        href = _href(final, resp.find("d:href", NS))
        etag = (resp.findtext(".//d:getetag", default="", namespaces=NS) or "").strip()
        try:
            cal = ical.Calendar.from_ical(raw)
            series = any(c.get("RRULE") for c in cal.walk("VEVENT"))
            for ev in recurring_ical_events.of(cal).between(start, end):
                s, e = ev.get("DTSTART").dt, (ev.get("DTEND").dt if ev.get("DTEND") else None)
                allday = not isinstance(s, datetime)
                if e is None:
                    e = s + (timedelta(days=1) if allday else timedelta(hours=1))
                out.append({"uid": str(ev.get("UID", "")), "href": href, "etag": etag, "series": series,
                            "summary": str(ev.get("SUMMARY", "") or "")[:300], "location": str(ev.get("LOCATION", "") or "")[:300],
                            "description": str(ev.get("DESCRIPTION", "") or "")[:2000], "allday": allday,
                            "start": s.isoformat() if allday else _as_dt(s, tz).isoformat(),
                            "end": e.isoformat() if allday else _as_dt(e, tz).isoformat()})
        except Exception:   # ein kaputter Termin soll nicht den ganzen Kalender verhindern
            continue
    out.sort(key=lambda x: x["start"])
    return out


def _vevent(ev, uid):
    ical = _ical()
    cal = ical.Calendar(); cal.add("prodid", "-//Saga//DE"); cal.add("version", "2.0")
    v = ical.Event(); v.add("uid", uid)
    _set_fields(v, ev)
    cal.add_component(v)
    return cal.to_ical()


def _load_single(href, auth, etag):
    """Vorhandenen Termin laden; Serien (RRULE/RDATE/Ausnahmen) werden NICHT angefasst."""
    status, data, rh, _ = _req("GET", href, auth)
    if status == 404:
        raise CalError(t("Termin gibt es nicht mehr – bitte neu laden"))
    if status != 200:
        raise CalError(t("Termin nicht lesbar ({status})", status=status))
    cur = (rh.get("etag") or "").strip()
    if etag and cur and cur != etag:
        raise CalError(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    cal = _ical().Calendar.from_ical(data)
    evs = list(cal.walk("VEVENT"))
    if len(evs) != 1 or any(evs[0].get(k) for k in ("RRULE", "RDATE", "RECURRENCE-ID")):
        raise CalError(t("Serientermine bitte direkt im Kalender (mailcow/Nextcloud) bearbeiten"))
    return cal, evs[0], cur or etag


def _set_fields(v, ev):
    """Nur Titel, Zeit, Ort, Beschreibung setzen - alles andere (Erinnerungen, Teilnehmer, ...) bleibt."""
    ical = _ical()
    for k in ("SUMMARY", "LOCATION", "DESCRIPTION", "DTSTART", "DTEND", "DURATION", "DTSTAMP"):
        if k in v:
            del v[k]
    v.add("dtstamp", datetime.now(timezone.utc))
    v.add("summary", (ev.get("summary") or t("Termin"))[:300])
    if ev.get("location"):
        v.add("location", str(ev["location"])[:300])
    if ev.get("description"):
        v.add("description", str(ev["description"])[:2000])
    if ev.get("allday"):
        s = date.fromisoformat(str(ev["start"])[:10]); e = date.fromisoformat(str(ev.get("end") or ev["start"])[:10])
        v.add("dtstart", s); v.add("dtend", e if e > s else s + timedelta(days=1))
    else:
        try:
            s = datetime.fromisoformat(str(ev["start"])); e = datetime.fromisoformat(str(ev.get("end") or ev["start"]))
        except ValueError:
            raise CalError(t("Ungültige Zeitangabe"))
        if not s.tzinfo or not e.tzinfo:
            raise CalError(t("Zeitangabe ohne Zeitzone"))
        v.add("dtstart", s); v.add("dtend", e if e > s else s + timedelta(hours=1))
    return ical


def save_event(cal_url, user, password, ev, href=None, etag=None):
    """Neu anlegen (href=None) oder aendern (mit ETag). Liefert (href, neues ETag)."""
    auth = (user, password)
    if href:
        check_url(href)
        if not href.startswith(check_url(cal_url)):
            raise CalError(t("Termin gehört nicht zu diesem Kalender"))
        cal, v, cur = _load_single(href, auth, etag)       # vorhandenen Termin aendern statt neu bauen
        _set_fields(v, ev)
        body, headers = cal.to_ical(), ({"If-Match": cur} if cur else {})
    else:
        uid = str(uuid.uuid4()); href = urljoin(check_url(cal_url if cal_url.endswith("/") else cal_url + "/"), uid + ".ics")
        body, headers = _vevent(ev, uid), {"If-None-Match": "*"}
    headers["Content-Type"] = "text/calendar; charset=utf-8"
    status, _, rh, _ = _req("PUT", href, auth, body, headers)
    if status == 412:
        raise CalError(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    if status not in (200, 201, 204):
        raise CalError(t("Termin nicht gespeichert ({status})", status=status))
    return href, rh.get("etag", "")


def delete_event(cal_url, user, password, href, etag=None):
    check_url(href)
    if not href.startswith(check_url(cal_url)):
        raise CalError(t("Termin gehört nicht zu diesem Kalender"))
    _, _, cur = _load_single(href, (user, password), etag)    # Serien nicht loeschen, ETag pruefen
    etag = cur or etag
    status, _, _, _ = _req("DELETE", href, (user, password), None, {"If-Match": etag} if etag else {})
    if status == 412:
        raise CalError(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    if status not in (200, 204, 404):
        raise CalError(t("Termin nicht gelöscht ({status})", status=status))
