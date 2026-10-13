"""Externe Kalender (CalDAV: mailcow/SOGo, Nextcloud, Radicale ...) je Benutzer."""
import hashlib
import json
import re
import time
from datetime import date, datetime
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
import auth
import hkcal
from db import connect as _db
from fastapi import APIRouter
from saga.core import E, SECRET, TZ, _api_guard, _bad, _body, _json_load, t

router = APIRouter()


# =====================================================================================
# Externe Kalender (CalDAV: mailcow/SOGo, Nextcloud) - Zugaenge je Benutzer, App-Passwort verschluesselt
# =====================================================================================
CAL_MAX_ACCOUNTS, CAL_CACHE_S, CAL_MAX_DAYS = 5, 90, 62
_cal_cache = {}
# Dauerhafte Threads fuer CalDAV-Abrufe: so bleibt je Thread die Verbindung zu mailcow offen (Keep-Alive)
from concurrent.futures import ThreadPoolExecutor as _TPE
_cal_pool = _TPE(max_workers=6, thread_name_prefix="caldav")
CAL_STALE_S = 1800                                       # bis 30 Min. alte Termine sofort zeigen, im Hintergrund erneuern
_cal_refreshing = set()


def _admin_hosts():
    """Im Admin-Bereich freigegebene interne Hosts (Kalender, Immich) - Komma-getrennt, auch host:port."""
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _cal_host_ok(host, hostport):
    """CalDAV: oeffentliche Server immer; interne nur mit Freigabe im Admin-Bereich."""
    import ipaddress
    import socket
    allowed = _admin_hosts()
    if auth.settings().get("allow_private_hosts") or host in allowed or hostport in allowed:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for i in infos:
        try:
            a = ipaddress.ip_address(i[4][0].split("%")[0])
        except ValueError:
            return False
        if getattr(a, "ipv4_mapped", None):
            a = a.ipv4_mapped
        if not a.is_global:
            return False
    return bool(infos)


hkcal.HOST_OK = _cal_host_ok


def _cal_fernet():
    from cryptography.fernet import Fernet
    import base64 as _b64
    seed = E.get("CAL_SECRET_KEY") or ("hk-cal|" + SECRET)      # eigener Schluessel empfohlen
    return Fernet(_b64.urlsafe_b64encode(hashlib.sha256(seed.encode()).digest()))


def _cal_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS cal_account (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, label TEXT, base_url TEXT NOT NULL,
        username TEXT NOT NULL, secret TEXT NOT NULL, calendars TEXT NOT NULL DEFAULT '[]', created REAL, updated REAL)""")


def _cal_public(r):
    """Fuer den Browser - OHNE Passwort."""
    return {"id": r[0], "label": r[1] or "", "base_url": r[2], "username": r[3], "calendars": _json_load(r[4], [])}


def _cal_selection(found, wanted):
    """Nur Kalender uebernehmen, die der Server beim Verbinden wirklich geliefert hat; Farbe nur #rrggbb."""
    by = {c["href"]: c for c in found}
    out = []
    for w in wanted if isinstance(wanted, list) else []:
        f = by.get(w.get("href")) if isinstance(w, dict) else None
        if not f or any(x["href"] == f["href"] for x in out):
            continue
        col = w.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(w.get("color") or "")) else (f["color"] or "#4184e4")
        out.append({"href": f["href"], "name": (str(w.get("name") or "").strip() or f["name"])[:80], "color": col,
                    "show": bool(w.get("show", True)), "writable": bool(f["writable"])})
    return out


def _cal_account(c, uid, aid):
    """Zugang inkl. entschluesseltem Passwort (nur serverseitig verwenden)."""
    r = c.execute("SELECT id, label, base_url, username, calendars, secret FROM cal_account WHERE id=? AND uid=?", (aid, uid)).fetchone()
    if not r:
        raise LookupError
    try:
        pw = _cal_fernet().decrypt(r[5].encode()).decode()
    except Exception:
        raise hkcal.CalError(t("Gespeichertes App-Passwort nicht lesbar – bitte neu eingeben"))
    return _cal_public(r), pw


@router.get("/api/settings/calendars")
def api_cal_list(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _cal_db(c)
        rows = c.execute("SELECT id, label, base_url, username, calendars FROM cal_account WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return JSONResponse({"accounts": [_cal_public(r) for r in rows], "allowed": sorted(_admin_hosts()), "private": bool(auth.settings().get("allow_private_hosts"))})


def _cal_input(b):
    base = str(b.get("base_url") or "").strip()[:500]
    user = str(b.get("username") or "").strip()[:200]
    pw = str(b.get("password") or "")[:500]
    return base, user, pw


@router.post("/api/settings/calendars/test")
async def api_cal_test(request: Request):
    """Verbindung pruefen und Kalender auflisten (speichert nichts). Ohne Passwort: das gespeicherte des Zugangs."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    base, user, pw = _cal_input(b)
    if not pw and b.get("id"):
        try:
            with _db() as c:
                _cal_db(c)
                acc, pw = _cal_account(c, uid, int(b["id"]))
            base, user = base or acc["base_url"], user or acc["username"]
        except (LookupError, ValueError, TypeError):
            return _bad("not found", 404)
        except hkcal.CalError as ex:
            return _bad(str(ex))
    if not (base and user and pw):
        return _bad(t("Adresse, Benutzer und App-Passwort angeben"))
    try:
        cals = await run_in_threadpool(hkcal.discover, base, user, pw)
    except hkcal.CalError as ex:
        return _bad(str(ex))
    return JSONResponse({"calendars": cals})


@router.post("/api/settings/calendars")
async def api_cal_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    base, user, pw = _cal_input(b)
    if not (base and user and pw):
        return _bad(t("Adresse, Benutzer und App-Passwort angeben"))
    try:
        found = await run_in_threadpool(hkcal.discover, base, user, pw)      # prueft Zugang + liefert gueltige Kalender
    except hkcal.CalError as ex:
        return _bad(str(ex))
    sel = _cal_selection(found, b.get("calendars"))
    now = time.time()
    with _db() as c, c:
        _cal_db(c)
        if c.execute("SELECT COUNT(*) FROM cal_account WHERE uid=?", (uid,)).fetchone()[0] >= CAL_MAX_ACCOUNTS:
            return _bad(t("Höchstens {n} Kalender-Zugänge", n=CAL_MAX_ACCOUNTS))
        cur = c.execute("INSERT INTO cal_account (uid, label, base_url, username, secret, calendars, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, str(b.get("label") or "").strip()[:60], base, user, _cal_fernet().encrypt(pw.encode()).decode(),
                         json.dumps(sel, ensure_ascii=False), now, now))
    _cal_cache.clear()
    return JSONResponse({"id": cur.lastrowid, "calendars": sel})


@router.patch("/api/settings/calendars/{aid}")
async def api_cal_update(aid: int, request: Request):
    """Kalenderauswahl/Farben/Name aendern; optional neues Passwort/Adresse/Benutzer (wird dann neu geprueft)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    try:
        with _db() as c:
            _cal_db(c)
            acc, old_pw = _cal_account(c, uid, aid)
    except LookupError:
        return _bad("not found", 404)
    except hkcal.CalError:
        acc, old_pw = None, ""
        with _db() as c:
            r = c.execute("SELECT id, label, base_url, username, calendars FROM cal_account WHERE id=? AND uid=?", (aid, uid)).fetchone()
            acc = _cal_public(r) if r else None
        if not acc:
            return _bad("not found", 404)
    base, user, pw = _cal_input(b)
    base, user, use_pw = base or acc["base_url"], user or acc["username"], pw or old_pw
    if not use_pw:
        return _bad(t("Bitte das App-Passwort neu eingeben"))
    try:
        found = await run_in_threadpool(hkcal.discover, base, user, use_pw)
    except hkcal.CalError as ex:
        return _bad(str(ex))
    sel = _cal_selection(found, b["calendars"] if "calendars" in b else acc["calendars"])
    with _db() as c, c:
        c.execute("UPDATE cal_account SET label=?, base_url=?, username=?, secret=?, calendars=?, updated=? WHERE id=? AND uid=?",
                  (str(b.get("label", acc["label"]) or "").strip()[:60], base, user, _cal_fernet().encrypt(use_pw.encode()).decode(),
                   json.dumps(sel, ensure_ascii=False), time.time(), aid, uid))
    _cal_cache.clear()
    return JSONResponse({"ok": True, "calendars": sel})


@router.delete("/api/settings/calendars/{aid}")
def api_cal_delete(aid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _cal_db(c)
        n = c.execute("DELETE FROM cal_account WHERE id=? AND uid=?", (aid, uid)).rowcount
    _cal_cache.clear()
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


def _cal_collect(uid, d0, d1, fresh=False):
    """Termine aller angezeigten Kalender des Benutzers in [d0, d1) - fuer Kalender-API und App-Widget.
    Kalender werden GLEICHZEITIG abgefragt (vorher nacheinander: ~4 s bei mailcow)."""
    start, end = d0.isoformat(), d1.isoformat()      # fuer den Cache-Schluessel
    t0, t1 = datetime.combine(d0, datetime.min.time(), TZ), datetime.combine(d1, datetime.min.time(), TZ)
    with _db() as c:
        _cal_db(c)
        ids = [r[0] for r in c.execute("SELECT id FROM cal_account WHERE uid=? ORDER BY id", (uid,))]
    out, errors, jobs = [], [], []
    for aid in ids:
        try:
            with _db() as c:
                acc, pw = _cal_account(c, uid, aid)
        except (LookupError, hkcal.CalError) as ex:
            errors.append({"account": aid, "error": str(ex) or t("nicht lesbar")}); continue
        for cal in acc["calendars"]:
            if cal.get("show"):
                jobs.append((aid, acc, pw, cal))

    def load(job, key):
        aid, acc, pw, cal = job
        evs = hkcal.events(cal["href"], acc["username"], pw, t0, t1, TZ)
        _cal_cache[key] = (time.time(), evs)
        return evs

    def refresh_later(job, key):                         # im Hintergrund nachladen (Fehler still - alter Stand bleibt)
        if key in _cal_refreshing:
            return
        _cal_refreshing.add(key)
        def run():
            try:
                load(job, key)
            except Exception:
                pass
            finally:
                _cal_refreshing.discard(key)
        _cal_pool.submit(run)

    def fetch(job):
        aid, acc, pw, cal = job
        key = (uid, aid, cal["href"], start, end)
        hit = _cal_cache.get(key)
        if hit and not fresh:
            age = time.time() - hit[0]
            if age < CAL_CACHE_S:
                return job, hit[1], None
            if age < CAL_STALE_S:                         # sofort antworten, frische Daten fuer das naechste Mal holen
                refresh_later(job, key)
                return job, hit[1], None
        try:
            return job, load(job, key), None
        except hkcal.CalError as ex:
            if hit:                                       # mailcow gerade nicht erreichbar: letzten Stand zeigen
                return job, hit[1], None
            return job, None, str(ex)

    if True:
        for (aid, acc, pw, cal), evs, err in _cal_pool.map(fetch, jobs):
            if err:
                errors.append({"account": aid, "calendar": cal["name"], "error": err}); continue
            for e in evs:
                out.append(dict(e, account=aid, cal=cal["href"], calendar=cal["name"], color=cal["color"], writable=cal.get("writable", True)))
    out.sort(key=lambda x: x["start"])
    return out, errors


@router.get("/api/calendar/events")
def api_cal_events(request: Request, start: str = "", end: str = "", fresh: int = 0):
    """Termine aller angezeigten Kalender im Zeitraum [start, end) (Datum JJJJ-MM-TT, max. 62 Tage)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return _bad(t("start/end als JJJJ-MM-TT angeben"))
    if not 0 < (d1 - d0).days <= CAL_MAX_DAYS:
        return _bad(t("Zeitraum 1 bis {n} Tage", n=CAL_MAX_DAYS))
    out, errors = _cal_collect(uid, d0, d1, fresh)
    return JSONResponse({"events": out, "errors": errors}, headers={"Cache-Control": "no-store"})


def _cal_target(uid, b):
    """Zugang + Kalender fuer Schreibzugriffe pruefen: eigener Zugang, ausgewaehlter UND beschreibbarer Kalender."""
    try:
        aid = int(b.get("account"))
    except (TypeError, ValueError):
        raise LookupError
    with _db() as c:
        _cal_db(c)
        acc, pw = _cal_account(c, uid, aid)
    cal = next((x for x in acc["calendars"] if x["href"] == b.get("cal")), None)
    if not cal:
        raise LookupError
    if cal.get("writable") is False:
        raise hkcal.CalError(t("Dieser Kalender ist nur lesbar"))
    return acc, pw, cal


def _cal_event_in(b):
    return {k: b.get(k) for k in ("summary", "start", "end", "allday", "location", "description")}


async def _cal_write(request, mode):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    try:
        acc, pw, cal = _cal_target(uid, b)
        if mode == "add":
            href, etag = await run_in_threadpool(hkcal.save_event, cal["href"], acc["username"], pw, _cal_event_in(b))
        elif mode == "edit":
            href, etag = await run_in_threadpool(hkcal.save_event, cal["href"], acc["username"], pw, _cal_event_in(b),
                                                 str(b.get("href") or ""), str(b.get("etag") or "") or None)
        else:
            await run_in_threadpool(hkcal.delete_event, cal["href"], acc["username"], pw, str(b.get("href") or ""), str(b.get("etag") or "") or None)
            href, etag = None, None
    except LookupError:
        return _bad("not found", 404)
    except hkcal.CalError as ex:
        return _bad(str(ex))
    except (KeyError, TypeError, ValueError):
        return _bad(t("ungueltige Angaben"))
    _cal_cache.clear()
    return JSONResponse({"ok": True, "href": href, "etag": etag})


@router.post("/api/calendar/events")
async def api_cal_event_add(request: Request):
    return await _cal_write(request, "add")


@router.patch("/api/calendar/events")
async def api_cal_event_edit(request: Request):
    return await _cal_write(request, "edit")


@router.post("/api/calendar/events/delete")
async def api_cal_event_delete(request: Request):
    return await _cal_write(request, "delete")
