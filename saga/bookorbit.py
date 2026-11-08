"""BookOrbit (2.5, Phase 1): die eigene Leseliste aus BookOrbit in Saga zeigen - nur lesen (BookOrbit -> Saga).

BookOrbit (selbst gehostete E-Book-Bibliothek, REST /api/v1) kennt keine festen API-Schluessel: POST /auth/login
(Benutzer + Passwort) liefert ein kurzes Zugriffstoken (JWT, Standard 15 min) und ein Refresh-Token (64 Hex-Zeichen,
Standard 7 Tage, ROTIEREND: jedes POST /auth/refresh liefert ein neues, das alte gilt danach nur noch 30 s). Saga meldet
sich als "nativer" Client an (clientKind "native", deviceLabel "Saga"): dann kommen beide Tokens im JSON-Koerper statt
als Cookies, und refresh/logout nehmen das Token im Koerper ({"refreshToken": ...}).

- Pro Benutzer (wie saga.immich): Einstellungen -> Verbindungen -> BookOrbit. Adresse, Benutzer, Passwort -> einmal
  anmelden; gespeichert wird NUR das Refresh-Token (verschluesselt wie Kalender-Passwoerter, _cal_fernet), nie das
  Passwort. Tabelle bookorbit_link (Migration 35).
- Zugriffstoken nur im Speicher je Prozess. Erneuern atomar: Sperre je Benutzer (Thread-Sperre + SELECT ... FOR UPDATE in
  der Datenbank, auch ueber Prozesse hinweg), das neue Refresh-Token wird in derselben Transaktion gespeichert. Zwei
  gleichzeitige Abrufe erneuern also nacheinander - der zweite sieht schon das neue Token und verbrennt nichts.
  Faellt Saga zwischen Tausch und Commit aus, hilft die Gnadenfrist von BookOrbit (das alte Token liefert 30 s lang
  denselben Nachfolger).
- Hintergrund: etwa taeglich (SAGA_BOOKORBIT_REFRESH_S, Standard 20 h) wird das Token aller verbundenen Konten erneuert,
  damit es ohne Nutzung nicht nach 7 Tagen verfaellt. Lehnt BookOrbit das Token ab (401/400) -> Zustand "expired"
  (Token geloescht, "bitte neu anmelden").
- SSRF-Schutz wie Immich: oeffentliche Adressen nur https; interne nur fuer vom Admin freigegebene Hosts (allowed_hosts,
  BOOKORBIT_URL ist Vorschlag und automatisch frei); geprueft wird die IP der tatsaechlichen Verbindung (netguard), keine
  Weiterleitungen, Groessen- und Zeitlimits.
- Daten: POST /dashboard/widgets/batch (currently-reading, reading-goal, reading-streak). Die Einzel-Endpunkte
  GET /dashboard/widgets/<art> verlangen bei BookOrbit "App Attest" (nur die offizielle iOS-App) - der Sammelabruf nicht.
  Zwischenspeicher je Benutzer SAGA_BOOKORBIT_CACHE_S (Standard 180 s); bei Ausfall bis 6 h der letzte Stand (stale).
- Cover: GET /api/bookorbit/cover/<bookId> reicht das Vorschaubild (GET /books/<id>/thumbnail) mit dem Token der
  angemeldeten Person durch - der Browser spricht nie selbst mit BookOrbit. Bildpruefung wie Immich (_sniff_image).
- Vertrag fuer die Apps: docs/APP-API.md "BookOrbit"."""
import json
import re
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import auth
from db import connect as _db
from saga import netguard
from saga.calendars import _admin_hosts, _cal_fernet
from saga.core import E, RESTORE_TEST, _api_guard, _bad, _body, t
from saga.files import _sniff_image
from saga.netguard import Blocked, guarded_client

router = APIRouter()

BOOKORBIT_URL = (E.get("BOOKORBIT_URL") or "").rstrip("/")     # Vorschlag fuer das Adressfeld (und automatisch frei)
CACHE_S = float(E.get("SAGA_BOOKORBIT_CACHE_S") or 180)
STALE_S = 6 * 3600
REFRESH_S = float(E.get("SAGA_BOOKORBIT_REFRESH_S") or 20 * 3600)  # Hintergrund-Erneuerung, wenn so lange nicht erneuert
TICK_S = float(E.get("SAGA_BOOKORBIT_TICK_S") or 3600)
TIMEOUT = 15
BOOKS_MAX = 10
_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_LOGIN_RATE = (8, 60.0)                                         # Anmeldeversuche (bis BookOrbit) je Konto und Minute
# eigene Statuswerte von _call (keine HTTP-Codes): gesperrt (SSRF), Netzfehler, Weiterleitung, zu gross
BLOCKED, NETERR, REDIRECT, TOOBIG = 900, 901, 902, 903

_local = threading.local()
_mem = threading.Lock()
_ulocks = {}                 # uid -> threading.Lock (Erneuern nacheinander)
_flocks = {}                 # uid -> threading.Lock (Abruf der Leseliste nacheinander)
_access = {}                 # uid -> (zugriffstoken, gueltig_bis_epoch, basis)
_cache = {}                  # uid -> (zeit, daten)
_logins = {}                 # uid -> [zeitpunkte]


class _Err(Exception):
    """Zustand statt Daten: not_connected | expired | unavailable | blocked | denied"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _lock(d, uid):
    with _mem:
        lk = d.get(uid)
        if lk is None:
            lk = d[uid] = threading.Lock()
        return lk


def _forget(uid):
    with _mem:
        _access.pop(uid, None)
        _cache.pop(uid, None)


# --- Adresse und Netz (wie saga.immich) ------------------------------------------------------------------------------
def _allowed_hosts():
    hosts = set(_admin_hosts())
    if BOOKORBIT_URL:
        hosts.add(urlparse(BOOKORBIT_URL).netloc.lower())
    return hosts


def norm_url(url):
    """Adresse vereinheitlichen -> (basis, fehler). https://host[:port][/pfad], http nur fuer freigegebene Hosts.
    Ein angehaengtes /api/v1 bzw. /api wird abgeschnitten."""
    u = str(url or "").strip().rstrip("/")
    for suf in ("/api/v1", "/api"):
        if u.endswith(suf):
            u = u[: -len(suf)]
    if len(u) > 300:
        return None, t("Adresse zu lang")
    try:
        pu = urlparse(u)
        pu.port                                    # ungueltiger Port -> ValueError
    except ValueError:
        return None, t("Adresse ungültig")
    if pu.scheme not in ("http", "https") or not pu.hostname or pu.username or pu.password or pu.query or pu.fragment:
        return None, t("Adresse ungültig (z. B. https://bookorbit.example.org)")
    if pu.scheme == "http" and pu.netloc.lower() not in _allowed_hosts():
        return None, t("Nur https-Adressen erlaubt (http nur für vom Admin freigegebene interne Server)")
    return f"{pu.scheme}://{pu.netloc}{pu.path}", None


def _ip_ok(ip, netloc):
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global or auth.settings().get("allow_private_hosts") or netloc.lower() in _allowed_hosts()


def _call(base, method, path, body=None, token=None, max_bytes=1024 * 1024):
    """Ein Aufruf an BookOrbit (base + /api/v1 + path) -> (status, content_type, bytes). Status BLOCKED/NETERR/
    REDIRECT/TOOBIG statt HTTP-Code, wenn Saga selbst abbricht."""
    import httpx
    if not base or not norm_url(base)[0]:
        return BLOCKED, "", b""
    pu = urlparse(base)
    netloc = pu.netloc
    if RESTORE_TEST:
        return BLOCKED, "", b""
    try:                                           # 1) VOR dem Verbinden: alle Adressen des Namens pruefen
        infos = netguard.lookup(pu.hostname, pu.port or (443 if pu.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return NETERR, "", b""
    if not infos or not all(_ip_ok(i[4][0], netloc) for i in infos):
        return BLOCKED, "", b""
    cl = getattr(_local, "client", None)
    if cl is None:                                 # 2) BEIM Verbinden: genau die gepruefte IP (saga/netguard.py)
        cl = _local.client = guarded_client(
            lambda ip, host, port: _ip_ok(ip, f"{host}:{port}") or _ip_ok(ip, host), timeout=TIMEOUT, follow_redirects=False)
    h = {"Accept": "application/json, image/*", "User-Agent": "Saga"}
    if token:
        h["Authorization"] = "Bearer " + token
    try:
        with cl.stream(method, base + "/api/v1" + path, json=body, headers=h) as r:
            ns = r.extensions.get("network_stream")    # 3) NACH dem Verbinden: IP der tatsaechlichen Verbindung
            peer = ns.get_extra_info("server_addr") if ns else None
            if not peer or not _ip_ok(peer[0], netloc):
                _local.client = None
                return BLOCKED, "", b""
            if r.status_code in (301, 302, 303, 307, 308):
                return REDIRECT, "", b""
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return TOOBIG, "", b""
            return r.status_code, r.headers.get("content-type", ""), bytes(buf)
    except httpx.HTTPError as ex:
        _local.client = None
        return (BLOCKED if isinstance(ex.__cause__, Blocked) else NETERR), "", b""


def _json(data):
    try:
        v = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    return v if isinstance(v, dict) else None


def _exp(s, default):
    """ISO-Zeitpunkt (accessTokenExpiresAt/refreshTokenExpiresAt) -> Epoch."""
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return default


def _enc(s):
    return _cal_fernet().encrypt(s.encode()).decode()


def _dec(s):
    try:
        return _cal_fernet().decrypt(s.encode()).decode()
    except Exception:          # noqa: BLE001 - falscher Schluessel/kaputt: wie abgelaufen
        return None


# --- Token-Pflege ----------------------------------------------------------------------------------------------------
def _link(c, uid, lock=False):
    return c.execute("SELECT base_url, account, secret, state, refreshed, created FROM bookorbit_link WHERE uid=?"
                     + (" FOR UPDATE" if lock else ""), (uid,)).fetchone()


def _cached_access(uid):
    with _mem:
        hit = _access.get(uid)
    if hit and hit[1] - 60 > time.time():
        return hit
    return None


def _refresh(uid, stale=None, force=False, min_age=None):
    """Refresh-Token gegen ein neues tauschen (atomar je Benutzer) -> (zugriffstoken, gueltig_bis, basis).
    stale: Zugriffstoken, das BookOrbit eben abgelehnt hat (ein anderer Abruf hat vielleicht schon erneuert).
    min_age (Hintergrund): nur erneuern, wenn das letzte Erneuern laenger her ist. Fehler: _Err."""
    with _lock(_ulocks, uid):
        hit = _cached_access(uid)
        if hit and not force and hit[0] != stale:
            return hit
        expired = False
        with _db() as c, c:
            r = _link(c, uid, lock=True)
            if not r:
                raise _Err("not_connected")
            base, _, secret, state, refreshed, _ = r
            if state != "ok" or not secret:
                raise _Err("expired")
            if min_age is not None and refreshed and time.time() - refreshed < min_age:
                return hit
            tok = _dec(secret)
            st, _, data = _call(base, "POST", "/auth/refresh", {"refreshToken": tok}) if tok else (401, "", b"")
            d = _json(data) if st == 200 else None
            if st in (400, 401) or (st == 200 and not (d and _TOKEN.match(str(d.get("refreshToken") or ""))
                                                      and isinstance(d.get("accessToken"), str))):
                expired = True                     # Token ungueltig/abgelaufen/widerrufen -> neu anmelden
                c.execute("UPDATE bookorbit_link SET state='expired', secret=NULL, updated=? WHERE uid=?", (time.time(), uid))
            elif st != 200:
                raise _Err("blocked" if st == BLOCKED else "unavailable")
            else:
                now = time.time()
                c.execute("UPDATE bookorbit_link SET secret=?, refresh_exp=?, refreshed=?, updated=?, state='ok' WHERE uid=?",
                          (_enc(d["refreshToken"]), _exp(d.get("refreshTokenExpiresAt"), now + 7 * 86400), now, now, uid))
        if expired:
            _forget(uid)
            raise _Err("expired")
        acc = (d["accessToken"], _exp(d.get("accessTokenExpiresAt"), time.time() + 600), base)
        with _mem:
            _access[uid] = acc
        return acc


def _get_access(uid):
    return _cached_access(uid) or _refresh(uid)


def _api(uid, method, path, body=None, max_bytes=1024 * 1024):
    """Aufruf mit dem Zugriffstoken der Person (erneuert bei Bedarf einmal) -> (status, content_type, bytes)."""
    tok, _, base = _get_access(uid)
    st, ct, data = _call(base, method, path, body, tok, max_bytes)
    if st == 401:
        tok, _, base = _refresh(uid, stale=tok)
        st, ct, data = _call(base, method, path, body, tok, max_bytes)
    if st == BLOCKED:
        raise _Err("blocked")
    return st, ct, data


def refresh_all():
    """Hintergrund: Tokens aller verbundenen Konten erneuern, die laenger als REFRESH_S nicht erneuert wurden."""
    with _db() as c:
        uids = [r[0] for r in c.execute("SELECT uid FROM bookorbit_link WHERE state='ok' AND secret IS NOT NULL "
                                        "AND (refreshed IS NULL OR refreshed < ?)", (time.time() - REFRESH_S,)).fetchall()]
    n = 0
    for uid in uids:
        try:
            _refresh(uid, force=True, min_age=REFRESH_S)
            n += 1
        except _Err:
            pass
    return n


_thread = {"t": None, "stop": False, "ev": threading.Event()}


def _loop():
    while not _thread["stop"]:
        try:
            refresh_all()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg
            print(f"bookorbit: {type(ex).__name__}: {ex}", flush=True)
        _thread["ev"].wait(TICK_S)


def start():
    if RESTORE_TEST or _thread["t"] is not None:
        return
    _thread["stop"] = False
    _thread["ev"].clear()
    _thread["t"] = threading.Thread(target=_loop, name="bookorbit", daemon=True)
    _thread["t"].start()


def stop():
    _thread["stop"] = True
    _thread["ev"].set()
    _thread["t"] = None


def revoke_on_delete(uid):
    """Konto wird geloescht: bei BookOrbit abmelden (beste Muehe), Zeile entfernt saga.userdata."""
    try:
        with _db() as c:
            r = _link(c, uid)
        if r and r[2]:
            tok = _dec(r[2])
            if tok:
                _call(r[0], "POST", "/auth/logout", {"refreshToken": tok})
    except Exception:  # noqa: BLE001 - Abmelden ist Beiwerk
        pass
    _forget(uid)


# --- Leseliste -------------------------------------------------------------------------------------------------------
def _int(v, lo=0, hi=10 ** 9):
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _clean(items):
    """Antwort von POST /dashboard/widgets/batch -> eigene, bereinigte Form (nur bekannte Felder)."""
    by = {x.get("type"): x for x in items if isinstance(x, dict)} if isinstance(items, list) else {}
    out = {"books": None, "goal": None, "streak": None}
    cr = by.get("currently-reading") or {}
    if not cr.get("failed") and isinstance(cr.get("data"), dict) and isinstance(cr["data"].get("books"), list):
        books = []
        for b in cr["data"]["books"]:
            bid = _int(b.get("bookId"), 1, 2 ** 31 - 1) if isinstance(b, dict) else None
            if bid is None:
                continue
            p = b.get("progress")
            p = round(max(0.0, min(100.0, float(p))), 1) if isinstance(p, (int, float)) and not isinstance(p, bool) else 0.0
            authors = [str(a)[:120] for a in (b.get("authors") if isinstance(b.get("authors"), list) else []) if isinstance(a, str)][:5]
            fmt = b.get("fileFormat")
            books.append({"id": bid, "title": str(b.get("title") or "")[:300] or None, "authors": authors, "progress": p,
                          "cover": f"/api/bookorbit/cover/{bid}" if b.get("hasCover") is True else None,
                          "format": str(fmt)[:16].lower() if isinstance(fmt, str) and fmt else None})
            if len(books) >= BOOKS_MAX:
                break
        out["books"] = books
    g = by.get("reading-goal") or {}
    if not g.get("failed") and isinstance(g.get("data"), dict):
        gd = g["data"]
        out["goal"] = {"goal": _int(gd.get("goalBooks"), 1, 100000), "done": _int(gd.get("completedBooks"), 0, 10 ** 6) or 0,
                       "year": _int(gd.get("year"), 1900, 3000)}
    s = by.get("reading-streak") or {}
    if not s.get("failed") and isinstance(s.get("data"), dict):
        sd = s["data"]
        l7 = [x is True for x in (sd.get("lastSevenDays") if isinstance(sd.get("lastSevenDays"), list) else [])[:7]]
        out["streak"] = {"current": _int(sd.get("currentStreak"), 0, 10 ** 6) or 0, "longest": _int(sd.get("longestStreak"), 0, 10 ** 6) or 0,
                         "last7": l7}
    return out


def reading(uid):
    """Leseliste der Person -> {"state": "ok"|"none"|"expired"|"unavailable", ...}. Nie fremde Daten: alles je uid."""
    now = time.time()
    with _mem:
        hit = _cache.get(uid)
    if hit and now - hit[0] < CACHE_S:
        return hit[1]
    with _lock(_flocks, uid):                      # gleichzeitige Abrufe derselben Person: einer holt, der Rest wartet
        with _mem:
            hit = _cache.get(uid)
        if hit and time.time() - hit[0] < CACHE_S:
            return hit[1]
        with _db() as c:
            r = _link(c, uid)
        if not r:
            return {"state": "none"}
        if r[3] != "ok" or not r[2]:
            return {"state": "expired", "name": r[1]}
        try:
            st, _, data = _api(uid, "POST", "/dashboard/widgets/batch",
                               {"widgets": ["currently-reading", "reading-goal", "reading-streak"]}, 2 * 1024 * 1024)
            d = _json(data) if st == 200 else None
            if d is None or not isinstance(d.get("items"), list):
                raise _Err("unavailable")
        except _Err as e:
            if e.code == "expired":
                return {"state": "expired", "name": r[1]}
            if e.code == "not_connected":
                return {"state": "none"}
            if hit and time.time() - hit[0] < STALE_S and hit[1].get("state") == "ok":
                return dict(hit[1], stale=True)
            return {"state": "unavailable", "name": r[1]}
        out = dict(_clean(d["items"]), state="ok", name=r[1],
                   as_of=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        with _mem:
            if len(_cache) > 2000:
                _cache.clear()
            _cache[uid] = (time.time(), out)
        return out


def status(uid):
    with _db() as c:
        r = _link(c, uid)
    if not r:
        return {"available": True, "connected": False, "state": "none", "suggest": BOOKORBIT_URL or None}
    return {"available": True, "connected": True, "state": "ok" if r[3] == "ok" and r[2] else "expired", "name": r[1],
            "url": r[0], "since": r[5], "suggest": BOOKORBIT_URL or None}


# --- Endpunkte -------------------------------------------------------------------------------------------------------
@router.get("/api/bookorbit/status")
def api_status(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(status(uid), headers={"Cache-Control": "no-store"})


def _login_rate_ok(uid, count=False):
    """Hoechstens _LOGIN_RATE Anmeldungen je Konto und Minute; count=True: einen Versuch zaehlen (nur, wenn er BookOrbit
    wirklich erreicht hat - abgewiesene Adressen zaehlen nicht)."""
    now = time.time()
    with _mem:
        xs = [x for x in _logins.get(uid, []) if now - x < _LOGIN_RATE[1]]
        if count:
            xs.append(now)
        _logins[uid] = xs
        if len(_logins) > 5000:
            _logins.clear()
        return len(xs) < _LOGIN_RATE[0] or count


def connect(uid, url, username, password):
    """Einmal anmelden; gespeichert wird nur das Refresh-Token. -> (antwort, status)"""
    base, err = norm_url(url or BOOKORBIT_URL)
    if err:
        return {"error": err}, 400
    username = str(username or "").strip()
    if not username or len(username) > 100 or not isinstance(password, str) or not password or len(password) > 1024:
        return {"error": t("Benutzername und Passwort angeben")}, 400
    if not _login_rate_ok(uid):
        return {"error": t("Zu viele Anmeldeversuche – bitte eine Minute warten.")}, 429
    st, _, data = _call(base, "POST", "/auth/login",
                        {"username": username, "password": password, "clientKind": "native", "deviceLabel": "Saga"})
    password = None                                # noqa: F841 - Passwort nicht laenger als noetig halten
    if st != BLOCKED:
        _login_rate_ok(uid, count=True)
    d = _json(data) or {}
    if st == BLOCKED:
        return {"error": t("Diese Adresse zeigt auf einen internen Server, der nicht freigegeben ist")}, 400
    if st == 401:
        if d.get("errorCode") == "ACCOUNT_LOCKED":
            return {"error": t("BookOrbit hat das Konto nach zu vielen Fehlversuchen vorübergehend gesperrt")}, 400
        return {"error": t("BookOrbit lehnt Benutzername oder Passwort ab")}, 400
    if st == 403 and d.get("errorCode") == "PASSWORD_AUTH_DISABLED":
        return {"error": t("Anmeldung mit Passwort ist bei diesem BookOrbit abgeschaltet (z. B. nur Single Sign-on)")}, 400
    if st == 429:
        return {"error": t("BookOrbit: zu viele Anmeldeversuche – bitte später erneut")}, 429
    if st != 200:
        return {"error": t("BookOrbit unter dieser Adresse nicht erreichbar")}, 400      # Eingabe pruefen, kein Serverfehler
    rt, at = str(d.get("refreshToken") or ""), d.get("accessToken")
    if not _TOKEN.match(rt) or not isinstance(at, str) or not at:
        return {"error": t("Unter dieser Adresse antwortet kein BookOrbit")}, 400
    u = d.get("user") if isinstance(d.get("user"), dict) else {}
    name = str(u.get("name") or u.get("username") or username)[:120]
    now = time.time()
    old = None
    with _lock(_ulocks, uid):
        with _db() as c, c:
            r = _link(c, uid, lock=True)
            if r and r[2]:
                old = (r[0], r[2])
            c.execute("INSERT OR REPLACE INTO bookorbit_link (uid, base_url, account, secret, state, refresh_exp, created, refreshed, updated) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (uid, base, name, _enc(rt), "ok", _exp(d.get("refreshTokenExpiresAt"), now + 7 * 86400), now, now, now))
        _forget(uid)
        with _mem:
            _access[uid] = (at, _exp(d.get("accessTokenExpiresAt"), now + 600), base)
    if old:                                        # vorherige Verbindung bei BookOrbit abmelden (beste Muehe)
        tok = _dec(old[1])
        if tok:
            _call(old[0], "POST", "/auth/logout", {"refreshToken": tok})
    return {"ok": True, "name": name, "url": base}, 200


@router.post("/api/bookorbit/connect")
async def api_connect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    from starlette.concurrency import run_in_threadpool
    out, st = await run_in_threadpool(connect, uid, b.get("url"), b.get("username"), b.get("password"))
    if st != 200:
        return _bad(out["error"], st)
    return JSONResponse(out)


def disconnect(uid):
    with _lock(_ulocks, uid):
        with _db() as c, c:
            r = _link(c, uid, lock=True)
            c.execute("DELETE FROM bookorbit_link WHERE uid=?", (uid,))
        _forget(uid)
    logged_out = False
    if r and r[2]:
        tok = _dec(r[2])
        if tok:
            st, _, _ = _call(r[0], "POST", "/auth/logout", {"refreshToken": tok})
            logged_out = st == 200
    return {"ok": True, "logged_out": logged_out}


@router.post("/api/bookorbit/disconnect")
async def api_disconnect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    from starlette.concurrency import run_in_threadpool
    return JSONResponse(await run_in_threadpool(disconnect, uid))


@router.get("/api/bookorbit/reading")
async def api_reading(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    from starlette.concurrency import run_in_threadpool
    return JSONResponse(await run_in_threadpool(reading, uid), headers={"Cache-Control": "no-store"})


def cover(uid, book_id):
    try:
        st, _, data = _api(uid, "GET", f"/books/{book_id}/thumbnail", None, 2 * 1024 * 1024)
    except _Err as e:
        return None, e.code
    img = _sniff_image(data[:16]) if st == 200 else None
    return (data, img) if img else (None, "missing")


@router.get("/api/bookorbit/cover/{book_id}")
async def api_cover(book_id: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", book_id) or int(book_id) > 2 ** 31 - 1:
        return _bad(t("ungültig"))
    from starlette.concurrency import run_in_threadpool
    data, img = await run_in_threadpool(cover, uid, int(book_id))
    if data is None:
        if img in ("not_connected", "expired"):
            return _bad(t("BookOrbit nicht verbunden (Einstellungen → BookOrbit)"), 409)
        return _bad(t("Vorschau nicht verfügbar"), 404)
    return Response(content=data, media_type=img, headers={"Cache-Control": "private, max-age=3600",
                                                           "X-Content-Type-Options": "nosniff"})
