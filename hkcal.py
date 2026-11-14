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


class Conflict(CalError):
    """ETag passt nicht (Termin inzwischen woanders geaendert)."""


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


class SeriesError(CalError):
    """Termin ist (Teil) einer Serie - die Weboberflaeche aendert/loescht Serien nicht."""


def _is_series(evs):
    return len(evs) != 1 or any(evs[0].get(k) for k in ("RRULE", "RDATE", "RECURRENCE-ID"))


def _load_single(href, auth, etag, series=False):
    """Vorhandenen Termin laden; Serien (RRULE/RDATE/Ausnahmen) werden NICHT angefasst - ausser series=True (MCP, nur
    ausdruecklich): dann (cal, [alle VEVENTs], etag, ist_serie)."""
    status, data, rh, _ = _req("GET", href, auth)
    if status == 404:
        raise CalError(t("Termin gibt es nicht mehr – bitte neu laden"))
    if status != 200:
        raise CalError(t("Termin nicht lesbar ({status})", status=status))
    cur = (rh.get("etag") or "").strip()
    if etag and cur and cur != etag:
        raise Conflict(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    try:
        cal = _ical().Calendar.from_ical(data)
    except ValueError:
        raise CalError(t("Unerwartete Antwort des Kalender-Servers"))
    evs = list(cal.walk("VEVENT"))
    if series:
        if not evs:
            raise CalError(t("Unerwartete Antwort des Kalender-Servers"))
        return cal, evs, cur or etag, _is_series(evs)
    if _is_series(evs):
        raise SeriesError(t("Serientermine bitte direkt im Kalender (mailcow/Nextcloud) bearbeiten"))
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
        raise Conflict(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    if status not in (200, 201, 204):
        raise CalError(t("Termin nicht gespeichert ({status})", status=status))
    return href, rh.get("etag", "")


def _current(v, tz=timezone.utc):
    """Felder eines VEVENT wie in _set_fields (summary, location, description, allday, start, end). Zeiten ohne Zone
    gelten wie beim Lesen (events) in tz."""
    s = v.get("DTSTART").dt if v.get("DTSTART") else None
    e = v.get("DTEND").dt if v.get("DTEND") else None
    if s is None:
        raise CalError(t("Unerwartete Antwort des Kalender-Servers"))
    allday = not isinstance(s, datetime)
    if e is None:
        e = s + (v.get("DURATION").dt if v.get("DURATION") else (timedelta(days=1) if allday else timedelta(hours=1)))
    if not allday:
        s, e = _as_dt(s, tz), _as_dt(e, tz)
    return {"summary": str(v.get("SUMMARY", "") or ""), "location": str(v.get("LOCATION", "") or ""),
            "description": str(v.get("DESCRIPTION", "") or ""), "allday": allday, "start": s.isoformat(), "end": e.isoformat(),
            "_s": s, "_e": e}


TEXT_FIELDS = ("summary", "location", "description")


def _set_texts(v, ch):
    """Nur Titel/Ort/Beschreibung eines VEVENT setzen (Zeiten, Erinnerungen, Teilnehmer bleiben unberuehrt)."""
    for k in TEXT_FIELDS:
        if k in ch:
            if k.upper() in v:
                del v[k.upper()]
            val = str(ch[k] or "")[:2000 if k == "description" else 300]
            if val or k == "summary":
                v.add(k, val or t("Termin"))
    if "DTSTAMP" in v:
        del v["DTSTAMP"]
    v.add("dtstamp", datetime.now(timezone.utc))


def _is_day(v):
    return len(str(v).strip()) <= 10


def _patch_allday(ch, old_allday):
    """Ganztaegig nach der Aenderung (patch_event). Ohne allday entscheidet das Format von start (sonst end) wie bei
    create_event - aber nie mit Verlust: Uhrzeit bei einem ganztaegigen Termin -> Termin mit Uhrzeit; nur ein Datum bei
    einem Termin mit Uhrzeit -> Fehler (die Uhrzeit fiele weg). start und end muessen zusammenpassen."""
    if "allday" in ch:
        allday = bool(ch["allday"])
    else:
        probe = ch["start"] if "start" in ch else ch["end"]
        if not old_allday and _is_day(probe):
            raise CalError(t("Termin mit Uhrzeit: start/end mit Uhrzeit angeben – für ganztägig allday: true."))
        allday = old_allday and _is_day(probe)
    for k in ("start", "end"):
        if k in ch and _is_day(ch[k]) != allday:
            raise CalError(t("Ganztägig: start/end als JJJJ-MM-TT angeben – für einen Termin mit Uhrzeit allday: false.") if allday
                           else t("Termin mit Uhrzeit: start/end mit Uhrzeit angeben – für ganztägig allday: true."))
    return allday


def patch_event(cal_url, user, password, href, etag, ch, series=False, tz=timezone.utc, day_tz=None):
    """Termin teilweise aendern (MCP): nur die Schluessel in ch (summary, location, description, start, end, allday).
    Einzeltermin: fehlendes Ende behaelt die Dauer. Serie nur mit series=True und nur Texte (in allen VEVENTs der
    Serie, auch Ausnahmen) - Zeiten von Serien und einzelne Vorkommen aendert Saga nicht. -> (href, neues ETag).
    Wechsel ganztaegig <-> mit Uhrzeit (2.5): siehe _patch_allday; day_tz = Zone fuer die Tage eines Termins (Anfrage)."""
    auth = (user, password)
    check_url(href)
    if not href.startswith(check_url(cal_url)):
        raise CalError(t("Termin gehört nicht zu diesem Kalender"))
    cal, evs, cur, is_series = _load_single(href, auth, etag, series=True)
    if is_series and not series:
        raise SeriesError(t("Serientermine bitte direkt im Kalender (mailcow/Nextcloud) bearbeiten"))
    if is_series:
        if any(k in ch for k in ("start", "end", "allday")):
            raise SeriesError(t("Zeiten einer Serie bitte direkt im Kalender ändern – hier gehen nur Titel, Ort und Beschreibung."))
        for v in evs:
            _set_texts(v, ch)
    elif not any(k in ch for k in ("start", "end", "allday")):
        _set_texts(evs[0], ch)                    # nur Texte: Zeiten bleiben woertlich (auch TZID)
    else:
        v = evs[0]
        old = _current(v, tz)
        new = {k: old[k] for k in ("summary", "location", "description", "allday", "start", "end")}
        new.update({k: ch[k] for k in ch if k in new and k not in ("allday", "start", "end")})
        new["allday"] = allday = _patch_allday(ch, old["allday"])
        if allday == old["allday"]:
            new.update({k: ch[k] for k in ("start", "end") if k in ch})
            if "start" in ch and "end" not in ch:                      # Dauer behalten
                if allday:
                    d0 = date.fromisoformat(str(new["start"])[:10])
                    new["end"] = (d0 + (old["_e"] - old["_s"])).isoformat()
                else:
                    try:
                        s0 = datetime.fromisoformat(str(new["start"]))
                    except ValueError:
                        raise CalError(t("Ungültige Zeitangabe"))
                    new["end"] = (s0 + (old["_e"].astimezone(timezone.utc) - old["_s"].astimezone(timezone.utc))).isoformat()  # echte Dauer (Sommerzeit)
        elif allday:
            # Termin -> ganztaegig (2.5, mcp2): Tage, die der Termin beruehrt (in day_tz, wie im Web die Datumsfelder);
            # ohne end so viele Tage ab dem (neuen) Beginn - nie das alte Ende mit Uhrzeit stehen lassen
            zs, ze = old["_s"].astimezone(day_tz or tz), old["_e"].astimezone(day_tz or tz)
            last = (ze - timedelta(microseconds=1)).date() if ze > zs else zs.date()
            d0 = date.fromisoformat(str(ch["start"])[:10]) if "start" in ch else zs.date()
            new["start"] = d0.isoformat()
            new["end"] = str(ch["end"])[:10] if "end" in ch else (d0 + timedelta(days=(last - zs.date()).days + 1)).isoformat()
        else:
            # ganztaegig -> Termin mit Uhrzeit: Beginn mit Uhrzeit ist Pflicht (keine geratene Uhrzeit), Ende sonst +1 h
            if "start" not in ch:
                raise CalError(t("Ganztägig → mit Uhrzeit: start mit Uhrzeit angeben (JJJJ-MM-TTThh:mm)."))
            try:
                s0 = datetime.fromisoformat(str(ch["start"]))
            except ValueError:
                raise CalError(t("Ungültige Zeitangabe"))
            new["start"] = ch["start"]
            new["end"] = ch["end"] if "end" in ch else (s0 + timedelta(hours=1)).isoformat()
        _set_fields(v, new)
    body, headers = cal.to_ical(), ({"If-Match": cur} if cur else {})
    headers["Content-Type"] = "text/calendar; charset=utf-8"
    status, _, rh, _ = _req("PUT", href, auth, body, headers)
    if status == 412:
        raise Conflict(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    if status not in (200, 201, 204):
        raise CalError(t("Termin nicht gespeichert ({status})", status=status))
    return href, rh.get("etag", "")


def delete_event(cal_url, user, password, href, etag=None, series=False):
    """Termin loeschen (ETag geprueft). Serien nur mit series=True (MCP, ausdruecklich) - dann die ganze Serie."""
    check_url(href)
    if not href.startswith(check_url(cal_url)):
        raise CalError(t("Termin gehört nicht zu diesem Kalender"))
    if series:
        _, _, cur, _s = _load_single(href, (user, password), etag, series=True)
    else:
        _, _, cur = _load_single(href, (user, password), etag)    # Serien nicht loeschen, ETag pruefen
    etag = cur or etag
    status, _, _, _ = _req("DELETE", href, (user, password), None, {"If-Match": etag} if etag else {})
    if status == 412:
        raise Conflict(t("Der Termin wurde inzwischen woanders geändert – bitte neu laden"))
    if status not in (200, 204, 404):
        raise CalError(t("Termin nicht gelöscht ({status})", status=status))
