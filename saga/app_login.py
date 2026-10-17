"""Apps: Anmeldung ueber den Browser, Geraete-Anmeldung, Kopplung per QR-Code, Liste 'Apps & Geraete'."""
import hashlib
import re
import secrets
import base64
import time
from urllib.parse import urlencode
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from db import clean_line, connect as _db
from fastapi import APIRouter
from saga.core import (APP_TOKEN_MAX, ASSET_V, BASE_URL, SEC_HEADERS, _api_guard, _bad, _body, _session_user, _set_session, _tok_db,
                       _tok_hash, forget_device, t, title, tmpl, user_info)
from saga.web_auth import _auth_page

router = APIRouter()


# =====================================================================================
# App-Anmeldung (iOS/macOS/Linux) ueber das System-Anmeldefenster.
# Ablauf wie OAuth mit PKCE: /app/login?challenge=... -> Login -> /app/handoff -> saga://auth?code=...
# -> POST /app/exchange {code, verifier}. Code: einmalig, 2 Minuten; ohne passendes Geheimnis wertlos.
# =====================================================================================

APP_CODE_TTL = 120
_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
# Rueckkehr-Schema der App: saga://  (aeltere Apps unter dem frueheren Namen: hlidskjalf:// - Standard ohne Angabe)
APP_SCHEMES = {"saga", "hlidskjalf"}


def _json_only(request):
    """Anmelde-Endpunkte der Apps nur mit Content-Type: application/json. Ein fremdes Web-Formular (text/plain,
    urlencoded) kann diesen Typ nicht ohne CORS-Preflight senden -> kein Login-/Logout-CSRF. Alle Apps senden JSON."""
    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    return None if ct == "application/json" else _bad(t("ungültige Anfrage"), 415)


def _appcode_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS app_code (
        hash TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT, challenge TEXT NOT NULL, expires REAL NOT NULL)""")


@router.get("/app/login")
def app_login(request: Request, challenge: str = "", scheme: str = "hlidskjalf", prompt: str = ""):
    """prompt=login (App: Konto wechseln): eine bestehende Browser-Sitzung zaehlt nicht - Anmeldemaske zeigen. Die alte
    Sitzung bleibt bis zur neuen Anmeldung bestehen (kein Abmelden per fremdem Link)."""
    if not _CHALLENGE_RE.fullmatch(challenge) or scheme not in APP_SCHEMES:
        return PlainTextResponse(t("ungültige Anfrage"), status_code=400)
    relogin = prompt == "login"
    logged_in = bool(_session_user(request)) and not relogin
    resp = RedirectResponse("/app/handoff" if logged_in else "/login?next=/app/handoff" + ("&prompt=login" if relogin else ""),
                            status_code=303)
    # eigenes Cookie: die Anmeldung (callback) leert die Sitzung, dieses bleibt erhalten
    sec = BASE_URL.startswith("https")
    resp.set_cookie("hk_app_ch", challenge, max_age=600, httponly=True, secure=sec, samesite="lax", path="/app")
    resp.set_cookie("hk_app_sc", scheme, max_age=600, httponly=True, secure=sec, samesite="lax", path="/app")
    if logged_in:
        resp.delete_cookie("hk_app_t", path="/app")
    else:
        # Beginn dieses Ablaufs ohne Sitzung: meldet man sich danach frisch an, entfaellt die Rueckfrage
        resp.set_cookie("hk_app_t", str(int(time.time())), max_age=600, httponly=True, secure=sec, samesite="lax", path="/app")
    return resp


# Rueckfrage vor der Uebergabe an die App: sonst koennte eine fremde Seite /app/login mit IHRER Challenge oeffnen
# und der angemeldete Browser gaebe den Code ungefragt an irgendeine App mit demselben URL-Schema weiter.
# Bewusst ein Link (GET) statt Formular: die CSP form-action 'self' wuerde die Weiterleitung auf saga:// blockieren.
# (Steht hier statt in templates/, solange es nur diese kleine Seite ist.)
_HANDOFF_TPL = """{% extends "_auth_base.html" %}
{% block pagetitle %}{{ head }}{% endblock %}
{% block content %}
<h2 class="auth-h">{{ head }}</h2>
<p class="auth-note">{{ note }}</p>
<a class="btn btn-primary auth-btn" href="/app/handoff?go={{ go }}">{{ button }}</a>
<a class="btn auth-btn" href="/app/handoff?cancel=1">{{ cancel }}</a>
<p class="auth-foot">{{ foot }}</p>
{% endblock %}"""

# Ohne Rueckfrage nur direkt nach einer frischen Anmeldung (Formular/SSO) IN diesem Ablauf: /app/login lief ohne
# Sitzung (Cookie hk_app_t), danach hat man sich gerade selbst angemeldet (login_at in der Sitzung, hoechstens 2 Min. alt)
HANDOFF_FRESH = 120


def _fresh_login(request):
    try:
        t0, at = int(request.cookies.get("hk_app_t") or 0), int(request.session.get("login_at") or 0)
    except (TypeError, ValueError):
        return False
    return bool(t0 and at) and t0 <= at <= time.time() + 5 and time.time() - at <= HANDOFF_FRESH


def _app_back(request, query):
    """Zurueck in die App (Schema aus dem Ablauf) und Ablauf-Cookies loeschen"""
    scheme = request.cookies.get("hk_app_sc", "hlidskjalf")
    resp = RedirectResponse((scheme if scheme in APP_SCHEMES else "hlidskjalf") + "://auth?" + query, status_code=303)
    for k in ("hk_app_ch", "hk_app_sc", "hk_app_t"):
        resp.delete_cookie(k, path="/app")
    return resp


# Direkt nach einer frischen Anmeldung kommt man ueber eine Weiterleitungskette von einem Formular (POST /login).
# Endet diese Kette auf saga://, blockiert die CSP form-action 'self' die Navigation (die App bekaeme nie den Code).
# Deshalb hier eine kleine Seite, die die Kette beendet und dann per Meta-Refresh (plus Knopf) in die App springt.
_BACK_TPL = """{% extends "_auth_base.html" %}
{% block pagetitle %}{{ head }}{% endblock %}
{% block content %}
<meta http-equiv="refresh" content="0;url={{ url }}">
<h2 class="auth-h">{{ head }}</h2>
<p class="auth-note">{{ note }}</p>
<a class="btn btn-primary auth-btn" href="{{ url }}">{{ button }}</a>
{% endblock %}"""


def _app_back_page(request, query):
    """Wie _app_back, aber als Seite statt Weiterleitung (beendet eine Formular-Weiterleitungskette)"""
    scheme = request.cookies.get("hk_app_sc", "hlidskjalf")
    url = (scheme if scheme in APP_SCHEMES else "hlidskjalf") + "://auth?" + query
    html = tmpl.from_string(_BACK_TPL).render(
        title=title(), v=ASSET_V, url=url, head=t("Zurück zur App"),
        note=t("Du bist angemeldet. Die App öffnet sich gleich – sonst tippe auf den Knopf."), button=t("App öffnen"))
    resp = HTMLResponse(html, headers=SEC_HEADERS)
    for k in ("hk_app_ch", "hk_app_sc", "hk_app_t"):
        resp.delete_cookie(k, path="/app")
    return resp


def _handoff_confirm(request, uid):
    go = request.session["handoff"] = secrets.token_urlsafe(18)
    html = tmpl.from_string(_HANDOFF_TPL).render(
        title=title(), v=ASSET_V, go=go, head=t("App anmelden"),
        note=t("Eine App möchte sich mit deinem Konto „{name}“ anmelden.", name=request.session.get("name") or uid),
        button=t("Anmelden"), cancel=t("Abbrechen"),
        foot=t("Nur bestätigen, wenn du die Anmeldung gerade selbst in der App gestartet hast."))
    return HTMLResponse(html, headers=SEC_HEADERS)


@router.get("/app/handoff")
def app_handoff(request: Request, go: str = "", cancel: str = ""):
    uid = _session_user(request)
    ch = request.cookies.get("hk_app_ch", "")
    if cancel:
        # Abbrechen: zurueck in die App (die App sieht error=cancelled statt code) - ohne laufenden Ablauf aufs Dashboard
        request.session.pop("handoff", None)
        return _app_back(request, "error=cancelled") if _CHALLENGE_RE.fullmatch(ch) else RedirectResponse("/", status_code=303)
    if not uid:
        return RedirectResponse("/login?next=/app/handoff", status_code=303)
    if not _CHALLENGE_RE.fullmatch(ch):
        return PlainTextResponse(t("Anmeldung bitte in der App neu starten."), status_code=400)
    want = request.session.pop("handoff", None)
    fresh = _fresh_login(request)
    request.session.pop("login_at", None)          # gilt nur fuer genau eine Uebergabe
    if not fresh and (not want or not re.fullmatch(r"[A-Za-z0-9_-]{20,40}", go) or not secrets.compare_digest(go, want)):
        return _handoff_confirm(request, uid)
    code = secrets.token_urlsafe(32)
    with _db() as c, c:
        _appcode_db(c)
        c.execute("DELETE FROM app_code WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO app_code (hash, uid, name, challenge, expires) VALUES (?,?,?,?,?)",
                  (hashlib.sha256(code.encode()).hexdigest(), uid, request.session.get("name") or uid, ch, time.time() + APP_CODE_TTL))
    # frisch angemeldet: kam ueber eine Formular-Weiterleitungskette -> Seite statt Weiterleitung (CSP form-action)
    return _app_back_page(request, "code=" + code) if fresh else _app_back(request, "code=" + code)


@router.post("/app/exchange")
async def app_exchange(request: Request):
    """Einmal-Code + Geheimnis (PKCE) -> Sitzung fuer die App."""
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request) or {}
    code, verifier = str(b.get("code") or ""), str(b.get("verifier") or "")
    if not (20 <= len(code) <= 100 and 43 <= len(verifier) <= 128):
        return _bad(t("ungültige Anfrage"))
    with _db() as c, c:
        _appcode_db(c)
        h = hashlib.sha256(code.encode()).hexdigest()
        r = c.execute("SELECT uid, name, challenge, expires FROM app_code WHERE hash=?", (h,)).fetchone()
        c.execute("DELETE FROM app_code WHERE hash=?", (h,))            # einmalig - auch bei Fehlversuch verbraucht
    if not r or r[3] < time.time():
        return _bad(t("Code ungültig oder abgelaufen"), 401)
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if not secrets.compare_digest(calc, r[2]):
        return _bad(t("Code ungültig oder abgelaufen"), 401)
    if user_info(r[0], fresh=True) is None:
        return _bad(t("Kein Zugriff"), 403)
    _set_session(request, r[0], r[1])
    out = {"ok": True, "name": r[1]}
    out.update(_mint_device(r[0], r[1], b))
    if out.get("_did"):
        request.session["did"] = out.pop("_did")      # Geraet dieser Sitzung (Passwortwechsel meldet es nicht ab)
    return JSONResponse(out)


def _mint_device(uid, uname, b):
    """Geraete-Anmeldung anlegen (Sitzung spaeter ohne Anmeldefenster erneuern), optional zugleich App-Schluessel."""
    out = {}
    dev = re.sub(r"\s+", " ", clean_line(b.get("device") if isinstance(b.get("device"), str) else "")).strip()[:60]
    if not dev:
        return out
    tok = "hkd_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        _devlogin_db(c)
        old = [x[0] for x in c.execute("SELECT id FROM device_login WHERE uid=? ORDER BY COALESCE(last_used, created) DESC", (uid,)).fetchall()]
        _drop_devices(c, uid, old[DEVICE_LOGIN_MAX - 1:])     # zu viele -> die am laengsten unbenutzten fallen weg
        taken = {x[0] for x in c.execute("SELECT name FROM device_login WHERE uid=?", (uid,)).fetchall()}
        base_name, n = dev, 2
        while dev in taken and n < 100:           # gleich benannte Geraete unterscheidbar machen (BUG-046)
            dev = f"{base_name[:54]} ({n})"
            n += 1
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES (?,?,?,?,?,?,?)",
                        (uid, uname, dev, _tok_hash(tok), tok[:10], time.time(), time.time())).lastrowid
        out["device_token"] = tok
        out["_did"] = did
        if b.get("app_key") is True:              # zugleich App-Schluessel fuer Widget/Teilen/Menueleiste (wenn noch Platz)
            _tok_db(c)
            if c.execute("SELECT COUNT(*) FROM app_token WHERE uid=?", (uid,)).fetchone()[0] < APP_TOKEN_MAX:
                key = "hk_" + secrets.token_urlsafe(32)
                c.execute("INSERT INTO app_token (uid, name, hash, prefix, created, device_id) VALUES (?,?,?,?,?,?)",
                          (uid, f"{dev} (automatisch)", _tok_hash(key), key[:9], time.time(), did))
                out["app_key"] = key
    return out


# --- Geraete-Anmeldung: Apps bleiben angemeldet (die Sitzung selbst gilt nur 8 Stunden) -----------------
DEVICE_LOGIN_MAX = 20


def _drop_devices(c, uid, ids):
    """Geraete-Anmeldungen loeschen - samt dem App-Schluessel, der zu diesem Geraet gehoert (Widget/Teilen)."""
    _tok_db(c)
    n = 0
    for x in ids:
        c.execute("DELETE FROM app_token WHERE uid=? AND device_id=?", (uid, x))
        n += c.execute("DELETE FROM device_login WHERE id=? AND uid=?", (x, uid)).rowcount
        forget_device(uid, x)                     # dessen Sitzung endet sofort (BUG-002)
    return n


def _devlogin_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS device_login (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, uname TEXT, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL, created REAL NOT NULL, last_used REAL)""")


@router.post("/app/resume")
async def app_resume(request: Request):
    """Geraete-Schluessel -> neue Sitzung. Prueft jedes Mal frisch (bei LDAP im Verzeichnis): gesperrt/entfernt -> kein Zugriff (und Schluessel weg)."""
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request) or {}
    tok = str(b.get("device_token") or "")
    key = str(b.get("app_key") or "")
    if not tok.startswith("hkd_") or len(tok) > 120:
        return _bad(t("ungültige Anfrage"), 401)
    with _db() as c, c:
        _devlogin_db(c)
        r = c.execute("SELECT id, uid, uname FROM device_login WHERE hash=?", (_tok_hash(tok),)).fetchone()
    if not r:
        return _bad(t("Geraet abgemeldet - bitte neu anmelden"), 401)
    if user_info(r[1], fresh=True) is None:
        with _db() as c, c:
            _drop_devices(c, r[1], [r[0]])
        return _bad(t("Kein Zugriff"), 403)
    with _db() as c, c:
        c.execute("UPDATE device_login SET last_used=? WHERE id=?", (time.time(), r[0]))
        if key.startswith("hk_") and len(key) <= 120:      # die App nennt ihren Widget-Schluessel -> dem Geraet zuordnen
            _tok_db(c)
            c.execute("UPDATE app_token SET device_id=? WHERE hash=? AND uid=? AND device_id IS NULL"
                      " AND NOT EXISTS (SELECT 1 FROM app_token o WHERE o.device_id=?)", (r[0], _tok_hash(key), r[1], r[0]))
    _set_session(request, r[1], r[2] or r[1], {"did": r[0]})
    return JSONResponse({"ok": True, "name": r[2] or r[1]})


@router.post("/app/device")
async def app_device(request: Request):
    """Schon angemeldete App (gueltige Sitzung, aber noch kein Geraete-Schluessel) -> Schluessel ohne neue Anmeldung."""
    uid = _api_guard(request)
    if not uid:
        return _bad(t("Bitte anmelden"), 401)
    b = await _body(request) or {}
    out = _mint_device(uid, request.session.get("name") or uid, b)
    if not out:
        return _bad(t("Geraetename fehlt"))
    request.session["did"] = out.pop("_did")
    return JSONResponse(out)


# --- Anmeldung einer App per QR-Code -------------------------------------------------------------------
# Im Web (angemeldet) einen Kopplungs-Code erzeugen -> QR-Code mit <BASE_URL>/app/pair?c=<code>. Die App scannt ihn,
# uebernimmt daraus auch die Server-Adresse und tauscht den Code gegen eine Geraete-Anmeldung. Einmalig, 2 Minuten.
PAIR_TTL = 120


@router.post("/api/settings/pair")
def api_pair_new(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    import segno
    code, pid, now = secrets.token_urlsafe(24), secrets.token_urlsafe(9), time.time()
    with _db() as c, c:
        c.execute("DELETE FROM pair_code WHERE expires < ?", (now - 3600,))
        if c.execute("SELECT COUNT(*) FROM pair_code WHERE uid=? AND expires > ? AND used_at IS NULL", (uid, now)).fetchone()[0] >= 5:
            return _bad(t("Zu viele offene QR-Codes – bitte kurz warten."), 429)
        c.execute("INSERT INTO pair_code (hash, pid, uid, name, created, expires) VALUES (?,?,?,?,?,?)",
                  (_tok_hash(code), pid, uid, request.session.get("name") or uid, now, now + PAIR_TTL))
    link = f"{BASE_URL}/app/pair?c={code}"
    return JSONResponse({"id": pid, "link": link, "expires_in": PAIR_TTL,
                         "qr": segno.make(link, error="m").svg_data_uri(scale=6, border=2, dark="#000000", light="#ffffff")},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/settings/pair/{pid}")
def api_pair_status(pid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = c.execute("SELECT used_by, expires FROM pair_code WHERE pid=? AND uid=?", (pid[:40], uid)).fetchone()
    if not r:
        return _bad("not found", 404)
    return JSONResponse({"used_by": r[0], "expired": r[1] < time.time() and not r[0]}, headers={"Cache-Control": "no-store"})


@router.get("/app/pair", response_class=HTMLResponse)
def app_pair_page(request: Request, c: str = ""):
    """QR-Code mit der normalen Kamera gescannt: Seite mit "In der App oeffnen" (der Code wird hier nicht verbraucht)."""
    ok = bool(re.fullmatch(r"[A-Za-z0-9_-]{20,64}", c))
    q = urlencode({"u": BASE_URL, "c": c}) if ok else ""
    return _auth_page(request, "pair.html", ok=ok, links=[("saga", f"saga://pair?{q}"), ("hlidskjalf", f"hlidskjalf://pair?{q}")])


@router.post("/app/pair")
async def app_pair(request: Request):
    """Kopplungs-Code aus dem QR-Code -> Sitzung + Geraete-Anmeldung (wie /app/exchange)."""
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request) or {}
    code = str(b.get("code") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", code):
        return _bad(t("ungültige Anfrage"))
    dev = re.sub(r"\s+", " ", clean_line(b.get("device") if isinstance(b.get("device"), str) else "")).strip()[:60] or "App"
    now = time.time()
    with _db() as c, c:
        n = c.execute("UPDATE pair_code SET used_by=?, used_at=? WHERE hash=? AND used_at IS NULL AND expires > ?",
                      (dev, now, _tok_hash(code), now)).rowcount          # einmalig - auch bei gleichzeitigen Versuchen
        r = c.execute("SELECT uid, name FROM pair_code WHERE hash=?", (_tok_hash(code),)).fetchone() if n else None
    if not r:
        return _bad(t("QR-Code ungültig, schon benutzt oder abgelaufen – bitte im Web einen neuen erzeugen."), 401)
    if user_info(r[0], fresh=True) is None:
        return _bad(t("Kein Zugriff"), 403)
    _set_session(request, r[0], r[1])
    out = {"ok": True, "name": r[1], "server": BASE_URL}
    out.update(_mint_device(r[0], r[1], dict(b, device=dev)))
    if out.get("_did"):
        request.session["did"] = out.pop("_did")
    print(f"app-pair: {r[0]} -> {dev}", flush=True)
    return JSONResponse(out)


@router.post("/app/forget")
async def app_forget(request: Request):
    """Abmelden in der App: diesen Geraete-Schluessel loeschen."""
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request) or {}
    tok = str(b.get("device_token") or "")
    if tok.startswith("hkd_") and len(tok) <= 120:
        with _db() as c, c:
            _devlogin_db(c)
            r = c.execute("SELECT id, uid FROM device_login WHERE hash=?", (_tok_hash(tok),)).fetchone()
            if r:
                _drop_devices(c, r[1], [r[0]])
    request.session.clear()
    return JSONResponse({"ok": True})


@router.get("/api/settings/devices")
def api_dev_list(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _devlogin_db(c)
        rows = c.execute("SELECT id, name, prefix, created, last_used FROM device_login WHERE uid=? ORDER BY id", (uid,)).fetchall()
    me = request.session.get("did")
    return JSONResponse([{"id": r[0], "name": r[1], "prefix": r[2], "created": r[3], "last_used": r[4], "current": r[0] == me} for r in rows])


@router.delete("/api/settings/devices/{did}")
def api_dev_delete(did: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _devlogin_db(c)
        n = _drop_devices(c, uid, [did])
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


@router.get("/api/settings/apps")
def api_apps(request: Request):
    """Eine Liste: angemeldete Geraete (mit ihrem Widget-/Teilen-Schluessel) + von Hand erzeugte Schluessel."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _devlogin_db(c)
        _tok_db(c)
        devs = c.execute("SELECT id, name, created, last_used, prefix FROM device_login WHERE uid=? ORDER BY COALESCE(last_used, created) DESC", (uid,)).fetchall()
        toks = c.execute("SELECT id, name, prefix, created, last_used, device_id FROM app_token WHERE uid=? ORDER BY id", (uid,)).fetchall()
    ids = {d[0] for d in devs}
    keyof = {t[5]: {"id": t[0], "prefix": t[2], "created": t[3], "last_used": t[4]} for t in toks if t[5] in ids}
    me = request.session.get("did")
    # current: das Geraet dieser Sitzung; prefix: Anfang des Geraete-Schluessels - unterscheidet gleich benannte Geraete (BUG-046)
    return JSONResponse({
        "devices": [{"id": d[0], "name": d[1], "created": d[2], "last_used": d[3], "key": keyof.get(d[0]), "current": d[0] == me,
                     "prefix": d[4]} for d in devs],
        "keys": [{"id": t[0], "name": t[1], "prefix": t[2], "created": t[3], "last_used": t[4]} for t in toks if t[5] not in ids],
    })
