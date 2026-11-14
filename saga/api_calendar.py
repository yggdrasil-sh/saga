"""Saga-Kalender (CalDAV-Zugaenge des Kontos) ueber API-Token (2.5, MCP-Wunsch 1531): Kalender auflisten, Termine lesen,
anlegen, aendern, loeschen - ueber dieselben Funktionen wie /api/calendar/events im Web (saga.calendars: _cal_target,
_cal_collect, hkcal.save_event/patch_event/delete_event, Zwischenspeicher leeren). Bereiche read:calendar / write:calendar.

- Keine Zugangsdaten nach aussen: weder Adresse noch Benutzername noch Passwort; Kalender heissen "<zugang>-<kennung>"
  (Kennung = Hash der Kalender-Adresse), Termine haben eine undurchsichtige Kennung "event" (Zugang, Kalender, Name der
  Termin-Datei relativ zum Kalender, ETag) - die Kalender-Adresse selbst steht nicht darin.
- Serien: list_events liefert Vorkommen mit recurring: true und series (UID). Aendern/Loeschen einer Serie nur mit
  scope "series" (Aendern: nur Titel/Ort/Beschreibung; Loeschen: die ganze Serie). Einzelne Vorkommen aendert Saga nicht
  (das Web auch nicht) - klare Ablehnung mit Hinweis.
- Netz: hkcal prueft jede Adresse (netguard, interne Hosts nur mit Admin-Freigabe), Zeit- und Groessenlimits wie im Web."""
import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import hkcal
from saga import calendars as C
from saga.api_v1 import Fail
from saga.core import TZ, t
from saga.page_widgets import tz_ok

EVENTS_MAX = 300


def _cal_key(aid, href):
    return f"{aid}-{hashlib.sha256(href.encode()).hexdigest()[:10]}"


def _tz(name):
    if name in (None, ""):
        return TZ
    if not isinstance(name, str) or not tz_ok(name):
        raise Fail(400, t("Unbekannte Zeitzone"))
    return ZoneInfo(name)


def _accounts(uid):
    """[(aid, label, [kalender])] - ohne Passwort (das liest erst _target)."""
    from db import connect as _db
    with _db() as c:
        C._cal_db(c)
        rows = c.execute("SELECT id, label, base_url, username, calendars FROM cal_account WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return [(r[0], r[1] or "", C._cal_public(r)["calendars"]) for r in rows]


def calendars_list(tok):
    out = []
    for aid, label, cals in _accounts(tok.uid):
        for cal in cals:
            out.append({"calendar": _cal_key(aid, cal["href"]), "account": aid, "account_label": label, "name": cal.get("name") or "",
                        "color": cal.get("color") or "", "writable": cal.get("writable") is not False, "shown": bool(cal.get("show"))})
    return 200, {"calendars": out, "timezone": str(getattr(TZ, "key", "UTC"))}


def _find_cal(tok, key):
    if not isinstance(key, str) or "-" not in key or len(key) > 40:
        raise Fail(404, t("Kalender nicht gefunden – list_calendars nennt die Kennungen."))
    for aid, _label, cals in _accounts(tok.uid):
        for cal in cals:
            if _cal_key(aid, cal["href"]) == key:
                return aid, cal
    raise Fail(404, t("Kalender nicht gefunden – list_calendars nennt die Kennungen."))


def _event_id(aid, cal_href, href, etag):
    rel = href[len(cal_href):] if href and href.startswith(cal_href) else None
    if rel is None:
        return None
    raw = json.dumps([aid, _cal_key(aid, cal_href), rel, etag or ""], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _event_ref(tok, v):
    """Kennung -> (aid, kalender, href, etag). Nur eigene Zugaenge und ausgewaehlte Kalender."""
    try:
        aid, key, rel, etag = json.loads(base64.urlsafe_b64decode(str(v) + "=" * (-len(str(v)) % 4)).decode())
        if not isinstance(rel, str) or not isinstance(etag, str) or "/" in rel or not rel or rel in (".", ".."):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError, AttributeError):
        raise Fail(400, t("Ungültige Termin-Kennung – list_events nennt sie (Feld event)."))
    a2, cal = _find_cal(tok, key)
    if a2 != aid:
        raise Fail(404, "not found")
    return aid, cal, cal["href"] + rel, etag


def _when(v, tz, allday):
    """Eingabe JJJJ-MM-TT bzw. JJJJ-MM-TTThh:mm[:ss][+hh:mm] -> Text fuer hkcal (ohne Zone: in tz)."""
    if not isinstance(v, str) or not v.strip():
        raise Fail(400, t("Ungültige Zeitangabe"))
    v = v.strip()
    try:
        if allday:
            return date.fromisoformat(v[:10]).isoformat()
        d = datetime.fromisoformat(v)
    except ValueError:
        raise Fail(400, t("Ungültige Zeitangabe"))
    if isinstance(d, datetime) and d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    # in UTC weitergeben (wie das Web, toISOString): ein fester Offset wuerde als DTSTART;TZID="UTC+02:00" ohne VTIMEZONE
    # gespeichert und beim Lesen als schwebende Zeit in der Server-Zone gedeutet (falsche Uhrzeit bei anderer timezone)
    return d.astimezone(timezone.utc).isoformat()


def events_list(tok, a):
    tz = _tz(a.get("timezone"))
    try:
        d0, d1 = date.fromisoformat(str(a.get("start"))), date.fromisoformat(str(a.get("end")))
    except ValueError:
        raise Fail(400, t("start/end als JJJJ-MM-TT angeben"))
    if not 0 < (d1 - d0).days <= C.CAL_MAX_DAYS:
        raise Fail(400, t("Zeitraum 1 bis {n} Tage", n=C.CAL_MAX_DAYS))
    only = _find_cal(tok, a["calendar"]) if a.get("calendar") else None
    # Server-Zeitzone und gewuenschte Zone koennen sich um einen Tag verschieben: einen Tag mehr holen, dann genau filtern
    evs, errors = C._cal_collect(tok.uid, d0 - timedelta(days=1), d1 + timedelta(days=1), bool(a.get("fresh")))
    lo, hi = datetime.combine(d0, datetime.min.time(), tz), datetime.combine(d1, datetime.min.time(), tz)
    out = []
    for e in evs:
        if only and (e.get("account") != only[0] or e.get("cal") != only[1]["href"]):
            continue
        if e.get("allday"):
            s, en = date.fromisoformat(e["start"][:10]), date.fromisoformat(e["end"][:10])
            if not (s < d1 and en > d0):
                continue
            start, end = e["start"][:10], e["end"][:10]
        else:
            s, en = datetime.fromisoformat(e["start"]).astimezone(tz), datetime.fromisoformat(e["end"]).astimezone(tz)
            if not (s < hi and en > lo):
                continue
            start, end = s.isoformat(), en.isoformat()
        x = {"event": _event_id(e["account"], e["cal"], e.get("href"), e.get("etag")), "calendar": _cal_key(e["account"], e["cal"]),
             "calendar_name": e.get("calendar") or "", "summary": e.get("summary") or "", "start": start, "end": end,
             "allday": bool(e.get("allday")), "writable": bool(e.get("writable", True))}
        for k in ("location", "description"):
            if e.get(k):
                x[k] = e[k]
        if e.get("series"):
            x["recurring"] = True
            x["series"] = e.get("uid") or ""
        if x["event"] is None:
            x["writable"] = False
            x.pop("event")
        out.append(x)
        if len(out) >= EVENTS_MAX:
            break
    res = {"events": out, "timezone": str(getattr(tz, "key", tz)), "start": d0.isoformat(), "end": d1.isoformat()}
    if len(out) >= EVENTS_MAX:
        res["more"] = t("Mehr als {n} Termine – Zeitraum verkleinern.", n=EVENTS_MAX)
    if errors:
        res["errors"] = [{"calendar": x.get("calendar") or "", "error": x.get("error") or ""} for x in errors]
    return 200, res


def _target(tok, aid, cal):
    """Wie im Web: eigener Zugang, ausgewaehlter und beschreibbarer Kalender (saga.calendars._cal_target)."""
    try:
        return C._cal_target(tok.uid, {"account": aid, "cal": cal["href"]})
    except LookupError:
        raise Fail(404, "not found")
    except hkcal.CalError as ex:
        raise Fail(400, str(ex))


def _ev_fields(a, tz, partial=False):
    out = {}
    allday = a.get("allday")
    if allday is not None and not isinstance(allday, bool):
        raise Fail(400, t("„allday“ muss true oder false sein."))
    for k in ("summary", "location", "description"):
        if a.get(k) is not None:
            if not isinstance(a[k], str):
                raise Fail(400, t("ungültige Angabe"))
            out[k] = a[k]
    if allday is not None:
        out["allday"] = allday
    for k in ("start", "end"):
        if a.get(k) is not None:
            out[k] = _when(a[k], tz, bool(allday) if allday is not None else len(str(a[k]).strip()) == 10)
    if not partial:
        if not out.get("start"):
            raise Fail(400, t("„{k}“ fehlt.", k="start"))
        if "allday" not in out:
            out["allday"] = len(str(a["start"]).strip()) == 10
    return out


def _cal_err(ex):
    if isinstance(ex, hkcal.SeriesError):
        hint = t("Mit scope „series“: Titel/Ort/Beschreibung der ganzen Serie ändern bzw. die ganze Serie löschen.")
        return Fail(400, str(ex) + " " + hint)
    return Fail(409 if isinstance(ex, hkcal.Conflict) else 400, str(ex))


def event_create(tok, a):
    aid, cal = _find_cal(tok, a.get("calendar"))
    acc, pw, cal = _target(tok, aid, cal)
    ev = _ev_fields(a, _tz(a.get("timezone")))
    try:
        href, etag = hkcal.save_event(cal["href"], acc["username"], pw, ev)
    except hkcal.CalError as ex:
        raise _cal_err(ex)
    C._cal_cache.clear()
    return 201, {"ok": True, "event": _event_id(aid, cal["href"], href, etag)}


def _scope(a):
    s = a.get("scope")
    if s not in (None, "event", "series"):
        raise Fail(400, t("scope: event oder series"))
    return s == "series"


def event_update(tok, a):
    aid, cal, href, etag = _event_ref(tok, a.get("event"))
    acc, pw, cal = _target(tok, aid, cal)
    ch = _ev_fields(a, _tz(a.get("timezone")), partial=True)
    if not ch:
        raise Fail(400, t("nichts zu ändern"))
    try:
        href, etag = hkcal.patch_event(cal["href"], acc["username"], pw, href, etag or None, ch, _scope(a), TZ)
    except hkcal.CalError as ex:
        raise _cal_err(ex)
    C._cal_cache.clear()
    return 200, {"ok": True, "event": _event_id(aid, cal["href"], href, etag)}


def event_delete(tok, a):
    aid, cal, href, etag = _event_ref(tok, a.get("event"))
    acc, pw, cal = _target(tok, aid, cal)
    try:
        hkcal.delete_event(cal["href"], acc["username"], pw, href, etag or None, _scope(a))
    except hkcal.CalError as ex:
        raise _cal_err(ex)
    C._cal_cache.clear()
    return 200, {"ok": True}
