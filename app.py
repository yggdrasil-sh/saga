"""Saga - Dashboard, Seiten/Notizen, Aufgaben, Kalender, Lesezeichen.

Benutzer: lokale Konten, optional LDAP/Active Directory und OIDC (SSO) - eingerichtet im Admin-Bereich
(eigener Port, admin.py). Dienste legt der Admin an; ein Benutzer sieht einen Dienst, wenn er in der
Gruppe <Gruppen-Kennung><slug> ist (Standard: s_<slug>), in einer der Zusatzgruppen, oder wenn der Dienst
"fuer alle" freigegeben ist. Optional werden Dienste aus LDAP-Gruppenbeschreibungen uebernommen
(FreeIPA-Stil "Text || Name|URL|icon|Kategorie"). Dazu kommen die eigenen Lesezeichen jedes Benutzers.

Jeder Benutzer verwaltet seinen Bereich selbst: Seiten, Aufgaben, Lesezeichen, Kalender (CalDAV),
Immich, App-Schluessel. Daten: PostgreSQL (db.py), Dateien in STATE_DIR.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import secrets
import base64
import time
import zipfile
from contextlib import asynccontextmanager, closing
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from html import escape as html_escape, unescape as html_unescape
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool

import auth
import db
from version import API_LEVEL, MIN_APP, VERSION
import hkcal
from db import connect as _db

E = os.environ
SECRET = E.get("SECRET_KEY") or E.get("SESSION_SECRET") or ""
_missing = []
if not E.get("BASE_URL"):
    _missing.append("BASE_URL")
if len(SECRET) < 32 or SECRET.startswith("CHANGEME"):
    _missing.append("SECRET_KEY (mindestens 32 Zeichen, z. B. openssl rand -hex 32)")
if E.get("SEARXNG_URL") and E.get("SEARXNG_SECRET", "CHANGEME") in ("", "CHANGEME"):
    _missing.append("SEARXNG_SECRET (Websuche aktiv)")
if _missing:
    raise SystemExit("Saga: fehlende/ungesetzte Konfiguration: " + ", ".join(_missing))
BASE_URL = E["BASE_URL"].rstrip("/")
ADMIN_URL = (E.get("ADMIN_URL") or "").rstrip("/")       # Link "Admin-Bereich" im Benutzermenue (nur fuer Admins)
CACHE_TTL = int(E.get("CACHE_TTL", "60"))
STATE_DIR = E.get("STATE_DIR", "/var/lib/saga")
STATUS_INTERVAL = int(E.get("STATUS_INTERVAL", "60"))
SESSION_HOURS = int(E.get("SESSION_HOURS", "12"))
CA_FILE = E.get("CA_FILE", "/etc/ssl/certs/ca-certificates.crt")
SEARXNG_URL = E.get("SEARXNG_URL", "").rstrip("/")
SEARCH_LANGUAGE = E.get("SEARCH_LANGUAGE", "de-DE")
SEARCH_SAFESEARCH = E.get("SEARCH_SAFESEARCH", "0")
try:                                   # Zeitzone fuer serverseitig formatierte Zeiten (z.B. Export)
    TZ = ZoneInfo(E.get("TIMEZONE", "Europe/Berlin"))
except Exception:
    TZ = timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))


def _asset_version():
    """Fingerabdruck aller Dateien in static/ (inkl. Unterordner) -> ?v=... an CSS/JS-URLs.
    Jede Aenderung ergibt eine neue URL, Browser laden dann garantiert die neue Version."""
    h = hashlib.sha256()
    root = os.path.join(HERE, "static")
    for d, dirs, files in sorted(os.walk(root)):
        dirs.sort()
        for name in sorted(files):
            p = os.path.join(d, name)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:10]


ASSET_V = _asset_version()
user_info = auth.user_info


def title():
    return auth.settings().get("title") or "Saga"


# --- Dienste (Admin) und Lesezeichen (je Benutzer) ---------------------------------------------------
_svc_cache = {"t": 0.0, "data": {}}


def services_changed():
    """Vom Admin-Bereich aufgerufen: Dienste sofort neu laden."""
    _svc_cache["t"] = 0.0


def all_services():
    """Alle Dienste (Schluessel -> Dienst), CACHE_TTL Sekunden zwischengespeichert.
    Schluessel = Zugangsgruppe (Kennung + slug), damit bleiben Favoriten beim Umbenennen erhalten."""
    if time.time() - _svc_cache["t"] < CACHE_TTL:
        return _svc_cache["data"]
    st = auth.settings()
    prefix = (st.get("group_prefix") or "").strip().lower()
    data = {}
    if st["ldap"].get("enabled") and st["ldap"].get("services_from_description"):
        try:
            data.update(auth.ldap_services(prefix or "s_"))
        except Exception as ex:  # noqa: BLE001 - LDAP gerade weg: bisherige LDAP-Dienste behalten
            print(f"ldap-services: {type(ex).__name__}: {ex}", flush=True)
            data.update({k: v for k, v in _svc_cache["data"].items() if v.get("source") == "ldap"})
    with _db() as c:
        rows = c.execute("SELECT id, slug, name, url, icon, category, extra_groups, everyone, check_status "
                         "FROM service ORDER BY sort, name").fetchall()
    for r in rows:
        key = prefix + r[1]
        try:
            extra = [str(g).lower() for g in json.loads(r[6] or "[]") if str(g).strip()]
        except ValueError:
            extra = []
        data[key] = {"key": key, "id": r[0], "slug": r[1], "groups": [key] + extra, "everyone": bool(r[7]),
                     "name": r[2], "url": r[3], "host": urlparse(r[3]).netloc, "icon": r[4] or "",
                     "category": r[5] or "Dienste", "source": "local", "check": bool(r[8])}
    _svc_cache.update(t=time.time(), data=data)
    return data


def visible_services(info):
    g = info["groups"]
    return {k: s for k, s in all_services().items() if s.get("everyone") or g.intersection(s["groups"])}


BOOKMARK_MAX = 500


def bookmarks(uid):
    with _db() as c:
        rows = c.execute("SELECT id, name, url, icon, category FROM bookmark WHERE uid=? ORDER BY sort, id", (uid,)).fetchall()
    return [{"key": f"bm{r[0]}", "id": r[0], "name": r[1], "url": r[2], "host": urlparse(r[2]).netloc, "icon": r[3] or "",
             "category": r[4] or "Lesezeichen", "own": True, "check": False} for r in rows]


def visible_tiles(uid, info=None):
    """Dienste + eigene Lesezeichen (Schluessel -> Kachel)."""
    info = info or user_info(uid)
    if info is None:
        return {}
    out = dict(visible_services(info))
    for b in bookmarks(uid):
        out[b["key"]] = b
    return out


# --- Status-Checks (nur Admin-Dienste mit Statuspruefung - nie Lesezeichen: der Server ruft keine
#     Adressen auf, die Benutzer selbst eintragen) ------------------------------------------------------
_status = {}  # key -> {"state": up|warn|down, "code": int|None, "ms": int|None}


async def _status_loop():
    tls = ssl.create_default_context(cafile=CA_FILE) if os.path.isfile(CA_FILE) else ssl.create_default_context()
    async with httpx.AsyncClient(timeout=6, follow_redirects=False, verify=tls,
                                 headers={"User-Agent": "Saga-Status/1"}) as cl:
        while True:
            try:
                svcs = {k: s for k, s in (await asyncio.to_thread(all_services)).items() if s.get("check")}

                async def one(key, url):
                    t0 = time.monotonic()
                    try:
                        r = await cl.get(url)
                        ms = int((time.monotonic() - t0) * 1000)
                        state = "down" if r.status_code >= 500 else ("warn" if ms > 2500 else "up")
                        _status[key] = {"state": state, "code": r.status_code, "ms": ms}
                    except Exception:  # Timeout, TLS, DNS ...
                        _status[key] = {"state": "down", "code": None, "ms": None}

                await asyncio.gather(*(one(k, s["url"]) for k, s in svcs.items()))
                for k in list(_status):
                    if k not in svcs:
                        _status.pop(k, None)
            except Exception as ex:  # DB/LDAP weg o.ae. - naechste Runde
                print(f"status-loop: {type(ex).__name__}: {ex}", flush=True)
            await asyncio.sleep(STATUS_INTERVAL)


# --- Favoriten --------------------------------------------------------------
def favs(uid):
    with _db() as c:
        return [r[0] for r in c.execute("SELECT grp FROM fav WHERE uid=? ORDER BY ts", (uid,))]


def toggle_fav(uid, grp):
    with _db() as c, c:
        if c.execute("DELETE FROM fav WHERE uid=? AND grp=?", (uid, grp)).rowcount:
            return False
        c.execute("INSERT INTO fav VALUES (?,?,?)", (uid, grp, time.time()))
        return True


# --- Account ----------------------------------------------------------------
SOURCE_LABELS = {"local": "Lokales Konto", "ldap": "Verzeichnis (LDAP/AD)", "oidc": "Single Sign-On (OIDC)"}


def _fmt_day(t):
    return datetime.fromtimestamp(t, TZ).strftime("%d.%m.%Y") if t else None


def account_ctx(info):
    return {
        "mail": info["mail"], "source": SOURCE_LABELS.get(info["source"], info["source"]),
        "local": info["source"] == "local", "pw_changed": _fmt_day(info["pw_changed"]),
        "last_login": _fmt_day(info["last_login"]), "groups": sorted(info["groups"]),
        "admin_url": ADMIN_URL if info["is_admin"] else "",
    }


# --- App --------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app):
    await asyncio.to_thread(db.wait_ready)
    await asyncio.to_thread(db.init_schema)
    await asyncio.to_thread(auth.bootstrap_admin)
    files_init()
    task = asyncio.create_task(_status_loop())

    async def _gc_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                n = await asyncio.to_thread(files_gc)
                if n:
                    print(f"files-gc: {n} verwaiste Datei(en) entfernt", flush=True)
            except Exception as ex:
                print(f"files-gc: {type(ex).__name__}: {ex}", flush=True)
    gc_task = asyncio.create_task(_gc_loop())
    yield
    task.cancel()
    gc_task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET,
    session_cookie="saga",
    https_only=BASE_URL.startswith("https://"),
    same_site="lax",
    max_age=SESSION_HOURS * 3600,
)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")

# Aufrufe ueber andere Hostnamen (interne IP/DNS, alter Name) auf BASE_URL umleiten.
# Verhindert Login-Fehler (mismatching_state), weil Cookies an genau einen Host gebunden sind.
CANONICAL = E.get("CANONICAL_REDIRECT", "true").lower() in ("1", "true", "yes", "on")
_BASE_HOST = urlparse(BASE_URL).netloc.lower()


@app.middleware("http")
async def canonical_host(request: Request, call_next):
    if CANONICAL and request.url.path != "/healthz":
        # Reverse Proxys, die den Host umschreiben, liefern den oeffentlichen Namen in X-Forwarded-Host.
        # Unkritisch: der Header entscheidet nur OB umgeleitet wird - das Ziel ist immer die feste BASE_URL.
        fwd = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        host = fwd or (request.headers.get("host") or "").lower()
        if host and host != _BASE_HOST:
            q = ("?" + request.url.query) if request.url.query else ""
            return RedirectResponse(BASE_URL + request.url.path + q, status_code=308)
    return await call_next(request)


tmpl = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                   autoescape=select_autoescape(["html"]))

SEC_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://cdn.jsdelivr.net data: blob:; "
                               "style-src 'self'; script-src 'self'; connect-src 'self'; "
                               "frame-src 'self' https://www.youtube-nocookie.com; "        # Einbetten-Block: nur YouTube ohne Cookies
                               "manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _session_user(request):
    """Angemeldeter Benutzer der Sitzung - None, wenn gesperrt/geloescht oder das Passwort seitdem geaendert wurde."""
    uid = request.session.get("uid")
    if not uid:
        return None
    info = user_info(uid)
    if info is None or info["epoch"] > float(request.session.get("pwc") or 0):
        request.session.clear()
        return None
    return uid


def _api_guard(request):
    """Session + Custom-Header (erzwingt CORS-Preflight -> kein Cross-Site-Request)."""
    if request.headers.get("x-saga") != "1" and request.headers.get("x-hlidskjalf") != "1":   # x-hlidskjalf: aeltere Apps
        return None
    return _session_user(request)


def _set_session(request, uid, name, keep=None):
    info = user_info(uid) or {}
    request.session.clear()
    request.session.update(uid=uid, name=name or uid, pwc=info.get("epoch") or 0, **(keep or {}))


# --- Routes -----------------------------------------------------------------
@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/api/version")
def api_version():
    """Fuer Apps und Monitoring: Server-Version, Schnittstellen-Stand und die aelteste App-Version, die alles kann."""
    return JSONResponse({"name": "Saga", "version": VERSION, "api": API_LEVEL, "min_app": MIN_APP, "schema": db.SCHEMA_VERSION},
                        headers={"Cache-Control": "no-store"})


@app.get("/manifest.webmanifest")
def manifest():
    t = title()
    return JSONResponse({
        "name": t, "short_name": t, "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#0f141c", "theme_color": "#0f141c",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }, media_type="application/manifest+json")


# --- Anmeldung, Registrierung, Abmeldung -------------------------------------------------------------
# Erlaubte Ruecksprungziele nach dem Login - feste Positivliste statt beliebiger Pfade
# (verhindert Open-Redirect ueber praeparierte Login-Links)
NEXT_ALLOWED = {"/", "/notes", "/app/handoff"}
EMAIL_RE = re.compile(r"[^@\s]{1,100}@[^@\s]{1,150}\.[^@\s]{2,40}")


def _next(v):
    return v if v in NEXT_ALLOWED else "/"


def _ip(request):
    return (request.client.host if request.client else "") or "?"


def _csrf(request):
    t = request.session.get("csrf")
    if not t:
        t = request.session["csrf"] = secrets.token_urlsafe(24)
    return t


def _csrf_ok(request, form):
    return secrets.compare_digest(str(form.get("csrf") or ""), str(request.session.get("csrf") or "-"))


def _auth_opts():
    st = auth.settings()
    o = st["oidc"]
    return {"pw_login": bool(st.get("local_login", True) or (st["ldap"].get("enabled") and st["ldap"].get("password_login", True))),
            "sso": bool(o.get("enabled") and o.get("issuer") and o.get("client_id")), "sso_label": o.get("label") or "Mit SSO anmelden",
            "registration": bool(st.get("registration")), "ldap": bool(st["ldap"].get("enabled"))}


def _auth_page(request, name, status=200, **kw):
    html = tmpl.get_template(name).render(title=title(), v=ASSET_V, csrf=_csrf(request), opts=_auth_opts(), **kw)
    return HTMLResponse(html, status_code=status, headers=SEC_HEADERS)


def _start_session(request, user, id_token=None):
    nxt = _next(request.session.get("next", "/"))
    auth._ui_cache.pop(user["username"], None)
    _set_session(request, user["username"], user["display_name"] or user["username"],
                 {"id_token": id_token} if id_token and len(id_token) < 2500 else None)
    return RedirectResponse(nxt, status_code=303)


@app.get("/login")
def login(request: Request):
    nxt = _next(request.query_params.get("next", request.session.get("next", "/")))
    if _session_user(request):
        return RedirectResponse(nxt, status_code=303)
    request.session["next"] = nxt
    o = _auth_opts()
    if o["sso"] and not o["pw_login"] and not request.query_params.get("bye"):
        return RedirectResponse("/login/sso", status_code=303)       # nur SSO eingerichtet: direkt weiter
    return _auth_page(request, "login.html", bye=bool(request.query_params.get("bye")))


@app.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = str(form.get("username") or "").strip()[:100]
    if not _csrf_ok(request, form):
        return _auth_page(request, "login.html", 400, error="Die Seite war zu lange offen – bitte noch einmal anmelden.", username=username)
    if not _auth_opts()["pw_login"]:
        return _auth_page(request, "login.html", 403, error="Anmeldung mit Passwort ist abgeschaltet.")
    allow_local = bool(auth.settings().get("local_login", True))
    user, err = await run_in_threadpool(auth.authenticate, username, str(form.get("password") or ""), _ip(request), allow_local)
    if not user:
        return _auth_page(request, "login.html", 401, error=err, username=username)
    return _start_session(request, user)


@app.get("/login/sso")
async def login_sso(request: Request):
    cl = await run_in_threadpool(auth.oidc_client)
    if cl is None:
        return RedirectResponse("/login", status_code=303)
    request.session.setdefault("next", "/")
    try:
        return await cl.authorize_redirect(request, BASE_URL + "/auth/callback")
    except Exception as ex:  # noqa: BLE001 - Anbieter nicht erreichbar / falsch eingerichtet
        print(f"sso: {type(ex).__name__}: {ex}", flush=True)
        return _auth_page(request, "login.html", 502, error="Der Anmeldedienst (SSO) ist gerade nicht erreichbar.")


@app.get("/auth/callback")
async def callback(request: Request):
    cl = await run_in_threadpool(auth.oidc_client)
    if cl is None:
        return RedirectResponse("/login", status_code=303)
    try:
        token = await cl.authorize_access_token(request)
    except Exception as ex:  # noqa: BLE001 - state passt nicht, Code abgelaufen, Anbieter weg ...
        print(f"sso-callback: {type(ex).__name__}: {ex}", flush=True)
        return _auth_page(request, "login.html", 400, error="SSO-Anmeldung fehlgeschlagen – bitte noch einmal versuchen.")
    claims = dict(token.get("userinfo") or {})
    gclaim = auth.settings()["oidc"].get("groups_claim")
    if gclaim and auth._claim(claims, gclaim) is None:          # Gruppen stehen bei manchen Anbietern nur im userinfo-Endpunkt
        try:
            extra = dict(await cl.userinfo(token=token))
            extra.pop("sub", None)
            claims.update(extra)
        except Exception:  # noqa: BLE001
            pass
    user, err = await run_in_threadpool(auth.oidc_user, claims)
    if not user:
        return _auth_page(request, "login.html", 403, error=err)
    auth.touch_login(user["username"])
    return _start_session(request, user, token.get("id_token"))


REG_MAX_PER_IP = 5      # Registrierungen je Stunde und Adresse


@app.get("/register")
def register(request: Request):
    if not _auth_opts()["registration"]:
        return RedirectResponse("/login", status_code=303)
    return _auth_page(request, "register.html", f={})


@app.post("/register")
async def register_post(request: Request):
    if not _auth_opts()["registration"]:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    f = {k: str(form.get(k) or "").strip()[:200] for k in ("username", "name", "email")}

    def fail(msg, code=400):
        return _auth_page(request, "register.html", code, error=msg, f=f)
    if not _csrf_ok(request, form):
        return fail("Die Seite war zu lange offen – bitte noch einmal versuchen.")
    pw, pw2 = str(form.get("password") or ""), str(form.get("password2") or "")
    if pw != pw2:
        return fail("Die Passwörter stimmen nicht überein.")
    if f["email"] and not EMAIL_RE.fullmatch(f["email"]):
        return fail("Die E-Mail-Adresse sieht nicht richtig aus.")
    ip = _ip(request)
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", ("reg:" + ip, time.time() - 3600)).fetchone()[0]
    if n >= REG_MAX_PER_IP:
        return fail("Zu viele Registrierungen von dieser Adresse – bitte später erneut versuchen.", 429)
    u = auth.norm_username(f["username"])
    if u and _auth_opts()["ldap"]:
        try:
            taken = await run_in_threadpool(auth.ldap_exists, u)
        except Exception:  # noqa: BLE001 - Verzeichnis weg: lieber nicht registrieren
            return fail("Registrierung gerade nicht möglich (Verzeichnisdienst nicht erreichbar).", 503)
        if taken:
            return fail("Diesen Benutzernamen gibt es schon.")
    user, err = await run_in_threadpool(auth.create_user, f["username"], pw, f["name"], f["email"])
    if err:
        return fail(err)
    with _db() as c, c:
        c.execute("INSERT INTO login_fail (key, ts) VALUES (?,?)", ("reg:" + ip, time.time()))
    auth.touch_login(user["username"])
    print(f"register: neues Konto {user['username']}", flush=True)
    return _start_session(request, user)


@app.get("/logout")
async def logout(request: Request):
    id_token = request.session.get("id_token")
    request.session.clear()
    if id_token:
        cl = await run_in_threadpool(auth.oidc_client)
        if cl is not None:
            try:
                end = (await cl.load_server_metadata()).get("end_session_endpoint")
            except Exception:  # noqa: BLE001
                end = None
            if end:
                q = {"post_logout_redirect_uri": BASE_URL + "/login?bye=1", "client_id": auth.settings()["oidc"]["client_id"],
                     "id_token_hint": id_token}
                return RedirectResponse(f"{end}?{urlencode(q)}", status_code=303)
    return RedirectResponse("/login?bye=1", status_code=303)


# --- Eigenes Konto -----------------------------------------------------------------------------------
@app.get("/api/account")
def api_account(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    i = user_info(uid)
    return JSONResponse({"username": i["username"], "name": i["name"], "email": i["mail"], "source": i["source"],
                         "source_label": SOURCE_LABELS.get(i["source"], i["source"]), "groups": sorted(i["groups"]),
                         "local_groups": sorted(i["local_groups"]), "is_admin": i["is_admin"],
                         "admin_url": ADMIN_URL if i["is_admin"] else "", "pw_changed": i["pw_changed"],
                         "can_edit": i["source"] == "local"})


@app.patch("/api/account")
async def api_account_update(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    i = user_info(uid)
    if i["source"] != "local":
        return _bad("Name und E-Mail kommen von deinem Anmeldedienst und werden dort geändert.", 403)
    b = await _body(request) or {}
    name, email = str(b.get("name") or "").strip()[:120], str(b.get("email") or "").strip()[:200]
    if email and not EMAIL_RE.fullmatch(email):
        return _bad("Die E-Mail-Adresse sieht nicht richtig aus.")
    with _db() as c, c:
        c.execute("UPDATE users SET display_name=?, email=? WHERE username=?", (name, email, uid))
    auth._ui_cache.pop(uid, None)
    request.session["name"] = name or uid
    return JSONResponse({"ok": True, "name": name or uid})


@app.post("/api/account/password")
async def api_account_password(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    old, new = str(b.get("old") or ""), str(b.get("new") or "")
    u = auth.get_user(uid)
    if u["source"] != "local":
        return _bad("Dein Passwort verwaltet dein Anmeldedienst.", 403)
    if auth.too_many(uid, _ip(request)):
        return _bad("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.", 429)
    if not u["password_hash"] or not await run_in_threadpool(auth.verify_password, old, u["password_hash"]):
        auth.note_fail(uid, _ip(request))
        return _bad("Das bisherige Passwort stimmt nicht.", 403)
    p = auth.password_problem(new)
    if p:
        return _bad(p)
    await run_in_threadpool(auth.set_password, uid, new)
    request.session["pwc"] = time.time()              # diese Sitzung bleibt, alle anderen Browser-Sitzungen enden
    return JSONResponse({"ok": True})


# =====================================================================================
# App-Anmeldung (iOS/macOS/Linux) ueber das System-Anmeldefenster.
# Ablauf wie OAuth mit PKCE: /app/login?challenge=... -> Login -> /app/handoff -> saga://auth?code=...
# -> POST /app/exchange {code, verifier}. Code: einmalig, 2 Minuten; ohne passendes Geheimnis wertlos.
# =====================================================================================

APP_CODE_TTL = 120
_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
# Rueckkehr-Schema der App: saga://  (aeltere Apps unter dem frueheren Namen: hlidskjalf:// - Standard ohne Angabe)
APP_SCHEMES = {"saga", "hlidskjalf"}


def _appcode_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS app_code (
        hash TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT, challenge TEXT NOT NULL, expires REAL NOT NULL)""")


@app.get("/app/login")
def app_login(request: Request, challenge: str = "", scheme: str = "hlidskjalf"):
    if not _CHALLENGE_RE.fullmatch(challenge) or scheme not in APP_SCHEMES:
        return PlainTextResponse("ungueltige Anfrage", status_code=400)
    resp = RedirectResponse("/app/handoff" if _session_user(request) else "/login?next=/app/handoff", status_code=303)
    # eigenes Cookie: die Anmeldung (callback) leert die Sitzung, dieses bleibt erhalten
    resp.set_cookie("hk_app_ch", challenge, max_age=600, httponly=True, secure=BASE_URL.startswith("https"), samesite="lax", path="/app")
    resp.set_cookie("hk_app_sc", scheme, max_age=600, httponly=True, secure=BASE_URL.startswith("https"), samesite="lax", path="/app")
    return resp


@app.get("/app/handoff")
def app_handoff(request: Request):
    uid = _session_user(request)
    ch = request.cookies.get("hk_app_ch", "")
    if not uid:
        return RedirectResponse("/login?next=/app/handoff", status_code=303)
    if not _CHALLENGE_RE.fullmatch(ch):
        return PlainTextResponse("Anmeldung bitte in der App neu starten.", status_code=400)
    code = secrets.token_urlsafe(32)
    with _db() as c, c:
        _appcode_db(c)
        c.execute("DELETE FROM app_code WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO app_code (hash, uid, name, challenge, expires) VALUES (?,?,?,?,?)",
                  (hashlib.sha256(code.encode()).hexdigest(), uid, request.session.get("name") or uid, ch, time.time() + APP_CODE_TTL))
    scheme = request.cookies.get("hk_app_sc", "hlidskjalf")
    resp = RedirectResponse((scheme if scheme in APP_SCHEMES else "hlidskjalf") + "://auth?code=" + code, status_code=303)
    resp.delete_cookie("hk_app_ch", path="/app")
    resp.delete_cookie("hk_app_sc", path="/app")
    return resp


@app.post("/app/exchange")
async def app_exchange(request: Request):
    """Einmal-Code + Geheimnis (PKCE) -> Sitzung fuer die App."""
    b = await _body(request) or {}
    code, verifier = str(b.get("code") or ""), str(b.get("verifier") or "")
    if not (20 <= len(code) <= 100 and 43 <= len(verifier) <= 128):
        return _bad("ungueltige Anfrage")
    with _db() as c, c:
        _appcode_db(c)
        h = hashlib.sha256(code.encode()).hexdigest()
        r = c.execute("SELECT uid, name, challenge, expires FROM app_code WHERE hash=?", (h,)).fetchone()
        c.execute("DELETE FROM app_code WHERE hash=?", (h,))            # einmalig - auch bei Fehlversuch verbraucht
    if not r or r[3] < time.time():
        return _bad("Code ungueltig oder abgelaufen", 401)
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    if not secrets.compare_digest(calc, r[2]):
        return _bad("Code ungueltig oder abgelaufen", 401)
    if user_info(r[0], fresh=True) is None:
        return _bad("Kein Zugriff", 403)
    _set_session(request, r[0], r[1])
    out = {"ok": True, "name": r[1]}
    out.update(_mint_device(r[0], r[1], b))
    return JSONResponse(out)


def _mint_device(uid, uname, b):
    """Geraete-Anmeldung anlegen (Sitzung spaeter ohne Anmeldefenster erneuern), optional zugleich App-Schluessel."""
    out = {}
    dev = re.sub(r"\s+", " ", str(b.get("device") or "")).strip()[:60]
    if not dev:
        return out
    tok = "hkd_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        _devlogin_db(c)
        old = [x[0] for x in c.execute("SELECT id FROM device_login WHERE uid=? ORDER BY COALESCE(last_used, created) DESC", (uid,)).fetchall()]
        _drop_devices(c, uid, old[DEVICE_LOGIN_MAX - 1:])     # zu viele -> die am laengsten unbenutzten fallen weg
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES (?,?,?,?,?,?,?)",
                        (uid, uname, dev, _tok_hash(tok), tok[:10], time.time(), time.time())).lastrowid
        out["device_token"] = tok
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
    return n


def _devlogin_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS device_login (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, uname TEXT, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL, created REAL NOT NULL, last_used REAL)""")


@app.post("/app/resume")
async def app_resume(request: Request):
    """Geraete-Schluessel -> neue Sitzung. Prueft jedes Mal frisch (bei LDAP im Verzeichnis): gesperrt/entfernt -> kein Zugriff (und Schluessel weg)."""
    b = await _body(request) or {}
    tok = str(b.get("device_token") or "")
    key = str(b.get("app_key") or "")
    if not tok.startswith("hkd_") or len(tok) > 120:
        return _bad("ungueltige Anfrage", 401)
    with _db() as c, c:
        _devlogin_db(c)
        r = c.execute("SELECT id, uid, uname FROM device_login WHERE hash=?", (_tok_hash(tok),)).fetchone()
    if not r:
        return _bad("Geraet abgemeldet - bitte neu anmelden", 401)
    if user_info(r[1], fresh=True) is None:
        with _db() as c, c:
            _drop_devices(c, r[1], [r[0]])
        return _bad("Kein Zugriff", 403)
    with _db() as c, c:
        c.execute("UPDATE device_login SET last_used=? WHERE id=?", (time.time(), r[0]))
        if key.startswith("hk_") and len(key) <= 120:      # die App nennt ihren Widget-Schluessel -> dem Geraet zuordnen
            _tok_db(c)
            c.execute("UPDATE app_token SET device_id=? WHERE hash=? AND uid=? AND device_id IS NULL"
                      " AND NOT EXISTS (SELECT 1 FROM app_token o WHERE o.device_id=?)", (r[0], _tok_hash(key), r[1], r[0]))
    _set_session(request, r[1], r[2] or r[1])
    return JSONResponse({"ok": True, "name": r[2] or r[1]})


@app.post("/app/device")
async def app_device(request: Request):
    """Schon angemeldete App (gueltige Sitzung, aber noch kein Geraete-Schluessel) -> Schluessel ohne neue Anmeldung."""
    uid = _api_guard(request)
    if not uid:
        return _bad("Bitte anmelden", 401)
    b = await _body(request) or {}
    out = _mint_device(uid, request.session.get("name") or uid, b)
    if not out:
        return _bad("Geraetename fehlt")
    return JSONResponse(out)


# --- Anmeldung einer App per QR-Code -------------------------------------------------------------------
# Im Web (angemeldet) einen Kopplungs-Code erzeugen -> QR-Code mit <BASE_URL>/app/pair?c=<code>. Die App scannt ihn,
# uebernimmt daraus auch die Server-Adresse und tauscht den Code gegen eine Geraete-Anmeldung. Einmalig, 2 Minuten.
PAIR_TTL = 120


@app.post("/api/settings/pair")
def api_pair_new(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    import segno
    code, pid, now = secrets.token_urlsafe(24), secrets.token_urlsafe(9), time.time()
    with _db() as c, c:
        c.execute("DELETE FROM pair_code WHERE expires < ?", (now - 3600,))
        if c.execute("SELECT COUNT(*) FROM pair_code WHERE uid=? AND expires > ? AND used_at IS NULL", (uid, now)).fetchone()[0] >= 5:
            return _bad("Zu viele offene QR-Codes – bitte kurz warten.", 429)
        c.execute("INSERT INTO pair_code (hash, pid, uid, name, created, expires) VALUES (?,?,?,?,?,?)",
                  (_tok_hash(code), pid, uid, request.session.get("name") or uid, now, now + PAIR_TTL))
    link = f"{BASE_URL}/app/pair?c={code}"
    return JSONResponse({"id": pid, "link": link, "expires_in": PAIR_TTL,
                         "qr": segno.make(link, error="m").svg_data_uri(scale=6, border=2, dark="#000000", light="#ffffff")},
                        headers={"Cache-Control": "no-store"})


@app.get("/api/settings/pair/{pid}")
def api_pair_status(pid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = c.execute("SELECT used_by, expires FROM pair_code WHERE pid=? AND uid=?", (pid[:40], uid)).fetchone()
    if not r:
        return _bad("not found", 404)
    return JSONResponse({"used_by": r[0], "expired": r[1] < time.time() and not r[0]}, headers={"Cache-Control": "no-store"})


@app.get("/app/pair", response_class=HTMLResponse)
def app_pair_page(request: Request, c: str = ""):
    """QR-Code mit der normalen Kamera gescannt: Seite mit "In der App oeffnen" (der Code wird hier nicht verbraucht)."""
    ok = bool(re.fullmatch(r"[A-Za-z0-9_-]{20,64}", c))
    q = urlencode({"u": BASE_URL, "c": c}) if ok else ""
    return _auth_page(request, "pair.html", ok=ok, links=[("saga", f"saga://pair?{q}"), ("hlidskjalf", f"hlidskjalf://pair?{q}")])


@app.post("/app/pair")
async def app_pair(request: Request):
    """Kopplungs-Code aus dem QR-Code -> Sitzung + Geraete-Anmeldung (wie /app/exchange)."""
    b = await _body(request) or {}
    code = str(b.get("code") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", code):
        return _bad("ungueltige Anfrage")
    dev = re.sub(r"\s+", " ", str(b.get("device") or "")).strip()[:60] or "App"
    now = time.time()
    with _db() as c, c:
        n = c.execute("UPDATE pair_code SET used_by=?, used_at=? WHERE hash=? AND used_at IS NULL AND expires > ?",
                      (dev, now, _tok_hash(code), now)).rowcount          # einmalig - auch bei gleichzeitigen Versuchen
        r = c.execute("SELECT uid, name FROM pair_code WHERE hash=?", (_tok_hash(code),)).fetchone() if n else None
    if not r:
        return _bad("QR-Code ungueltig, schon benutzt oder abgelaufen – bitte im Web einen neuen erzeugen.", 401)
    if user_info(r[0], fresh=True) is None:
        return _bad("Kein Zugriff", 403)
    _set_session(request, r[0], r[1])
    out = {"ok": True, "name": r[1], "server": BASE_URL}
    out.update(_mint_device(r[0], r[1], dict(b, device=dev)))
    print(f"app-pair: {r[0]} -> {dev}", flush=True)
    return JSONResponse(out)


@app.post("/app/forget")
async def app_forget(request: Request):
    """Abmelden in der App: diesen Geraete-Schluessel loeschen."""
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


@app.get("/api/settings/devices")
def api_dev_list(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _devlogin_db(c)
        rows = c.execute("SELECT id, name, prefix, created, last_used FROM device_login WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return JSONResponse([{"id": r[0], "name": r[1], "prefix": r[2], "created": r[3], "last_used": r[4]} for r in rows])


@app.delete("/api/settings/devices/{did}")
def api_dev_delete(did: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _devlogin_db(c)
        n = _drop_devices(c, uid, [did])
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


@app.get("/api/settings/apps")
def api_apps(request: Request):
    """Eine Liste: angemeldete Geraete (mit ihrem Widget-/Teilen-Schluessel) + von Hand erzeugte Schluessel."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _devlogin_db(c)
        _tok_db(c)
        devs = c.execute("SELECT id, name, created, last_used FROM device_login WHERE uid=? ORDER BY COALESCE(last_used, created) DESC", (uid,)).fetchall()
        toks = c.execute("SELECT id, name, prefix, created, last_used, device_id FROM app_token WHERE uid=? ORDER BY id", (uid,)).fetchall()
    ids = {d[0] for d in devs}
    keyof = {t[5]: {"id": t[0], "prefix": t[2], "created": t[3], "last_used": t[4]} for t in toks if t[5] in ids}
    return JSONResponse({
        "devices": [{"id": d[0], "name": d[1], "created": d[2], "last_used": d[3], "key": keyof.get(d[0])} for d in devs],
        "keys": [{"id": t[0], "name": t[1], "prefix": t[2], "created": t[3], "last_used": t[4]} for t in toks if t[5] not in ids],
    })


@app.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request):
    """Eigene Seite fuer die Notizen (Seitenbaum + Editor im ganzen Fenster)."""
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login?next=/notes", status_code=303)
    # ?embed=1: schlanke Ansicht fuer das Peek-Fenster - NUR diese Variante darf eingebettet werden,
    # und nur von Saga selbst (frame-ancestors 'self'); alles andere bleibt 'none'.
    embed = request.query_params.get("embed") == "1"
    html = tmpl.get_template("notes.html").render(title=title(), name=request.session.get("name", uid), v=ASSET_V, embed=embed,
                                                  admin_url=ADMIN_URL if user_info(uid)["is_admin"] else "")
    headers = dict(SEC_HEADERS)
    if embed:
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return HTMLResponse(html, headers=headers)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    info = user_info(uid)
    mine = visible_tiles(uid, info)
    fav_keys = [k for k in favs(uid) if k in mine]
    cats = {}
    for s in sorted(mine.values(), key=lambda s: (s["category"].lower(), s["name"].lower())):
        if s["key"] not in fav_keys:
            cats.setdefault(s["category"], []).append(s)
    html = tmpl.get_template("index.html").render(
        title=title(), name=request.session.get("name", uid), uid=uid,
        favorites=[mine[k] for k in fav_keys], categories=cats, acct=account_ctx(info),
        search_enabled=bool(SEARXNG_URL), v=ASSET_V,
    )
    return HTMLResponse(html, headers=SEC_HEADERS)


@app.get("/api/status")
def api_status(request: Request):
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    vis = visible_services(user_info(uid))
    return JSONResponse({k: v for k, v in _status.items() if k in vis},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/fav")
async def api_fav(request: Request):
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        grp = str((await request.json()).get("group", "")).lower()
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if grp not in await asyncio.to_thread(visible_tiles, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"fav": await asyncio.to_thread(toggle_fav, uid, grp)})


# --- Eigene Lesezeichen ---------------------------------------------------------------------------
_ICON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def _bm_input(b, cur=None):
    """-> (werte, fehler). cur = bisherige Werte (PATCH)."""
    cur = cur or {}
    name = str(b.get("name", cur.get("name", "")) or "").strip()[:80]
    url = str(b.get("url", cur.get("url", "")) or "").strip()[:500]
    icon = str(b.get("icon", cur.get("icon", "")) or "").strip().lower()[:64]
    cat = re.sub(r"\s+", " ", str(b.get("category", cur.get("category", "")) or "").strip())[:40] or "Lesezeichen"
    if url and "://" not in url:
        url = "https://" + url
    pu = urlparse(url)
    try:
        pu.port                                   # "https://javascript:alert(1)" -> ungueltiger Port
        host_ok = bool(pu.hostname) and re.fullmatch(r"[a-z0-9.\-]+|[0-9a-f:.]+", pu.hostname or "")
    except ValueError:
        host_ok = False
    if pu.scheme not in ("http", "https") or not host_ok or pu.username or pu.password:
        return None, "Bitte eine Adresse mit http:// oder https:// angeben."
    if not name:
        name = pu.netloc
    if icon and not _ICON_RE.fullmatch(icon):
        return None, "Symbol: nur der Name aus selfh.st/icons (z. B. nextcloud)."
    return {"name": name, "url": url, "icon": icon, "category": cat}, None


@app.get("/api/bookmarks")
def api_bookmarks(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(bookmarks(uid), headers={"Cache-Control": "no-store"})


@app.post("/api/bookmarks")
async def api_bookmark_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    v, err = _bm_input(await _body(request) or {})
    if err:
        return _bad(err)
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM bookmark WHERE uid=?", (uid,)).fetchone()[0] >= BOOKMARK_MAX:
            return _bad(f"Höchstens {BOOKMARK_MAX} Lesezeichen.")
        srt = (c.execute("SELECT MAX(sort) FROM bookmark WHERE uid=?", (uid,)).fetchone()[0] or 0) + 1
        bid = c.execute("INSERT INTO bookmark (uid, name, url, icon, category, sort, created) VALUES (?,?,?,?,?,?,?)",
                        (uid, v["name"], v["url"], v["icon"], v["category"], srt, time.time())).lastrowid
    return JSONResponse({"id": bid, "key": f"bm{bid}", **v})


@app.patch("/api/bookmarks/{bid}")
async def api_bookmark_update(bid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = c.execute("SELECT name, url, icon, category FROM bookmark WHERE id=? AND uid=?", (bid, uid)).fetchone()
    if not r:
        return _bad("not found", 404)
    v, err = _bm_input(await _body(request) or {}, dict(zip(("name", "url", "icon", "category"), r)))
    if err:
        return _bad(err)
    with _db() as c, c:
        c.execute("UPDATE bookmark SET name=?, url=?, icon=?, category=? WHERE id=? AND uid=?",
                  (v["name"], v["url"], v["icon"], v["category"], bid, uid))
    return JSONResponse({"id": bid, "key": f"bm{bid}", **v})


@app.delete("/api/bookmarks/{bid}")
def api_bookmark_delete(bid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM bookmark WHERE id=? AND uid=?", (bid, uid)).rowcount
        c.execute("DELETE FROM fav WHERE uid=? AND grp=?", (uid, f"bm{bid}"))
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


# --- Websuche (SearXNG) -----------------------------------------------------
_search_client = httpx.AsyncClient(timeout=10, follow_redirects=False,
                                   headers={"User-Agent": "Saga/1"})
_last_search = {}  # uid -> monotonic timestamp (einfaches Ratelimit)


def _http_url(u):
    return isinstance(u, str) and urlparse(u).scheme in ("http", "https")


@app.post("/api/search")
async def api_search(request: Request):
    # POST statt GET: Suchbegriffe landen so nicht im Access-Log (Datenschutz)
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not SEARXNG_URL:
        return JSONResponse({"error": "Websuche ist nicht eingerichtet"}, status_code=404)
    try:
        body = await request.json()
        q = str(body.get("q", "")).strip()[:200]
        page = max(1, min(int(body.get("page", 1)), 10))
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if not q:
        return JSONResponse({"error": "leere Suche"}, status_code=400)
    now = time.monotonic()
    if now - _last_search.get(uid, 0) < 0.7:
        return JSONResponse({"error": "zu schnell"}, status_code=429)
    _last_search[uid] = now
    try:
        r = await _search_client.post(SEARXNG_URL + "/search", data={
            "q": q, "format": "json", "pageno": page,
            "language": SEARCH_LANGUAGE, "safesearch": SEARCH_SAFESEARCH})
        r.raise_for_status()
        d = r.json()
    except Exception as ex:
        print(f"search: {type(ex).__name__}: {ex}", flush=True)
        return JSONResponse({"error": "Suche gerade nicht erreichbar"}, status_code=502)

    results = []
    for x in d.get("results") or []:
        if not _http_url(x.get("url")):
            continue  # keine javascript:/data:-Links ins Dashboard
        results.append({
            "title": (x.get("title") or x["url"])[:200],
            "url": x["url"],
            "host": urlparse(x["url"]).netloc,
            "content": (x.get("content") or "")[:400],
            "engines": (x.get("engines") or [x.get("engine")])[:3],
        })
    answers = []
    for a in d.get("answers") or []:
        t = a if isinstance(a, str) else (a.get("answer") if isinstance(a, dict) else None)
        if t:
            answers.append(str(t)[:300])
    return JSONResponse({
        "query": q, "page": page, "results": results[:20],
        "answers": answers[:3],
        "suggestions": [str(s)[:100] for s in (d.get("suggestions") or [])][:6],
        "corrections": [str(s)[:100] for s in (d.get("corrections") or [])][:3],
        "unresponsive": [u[0] for u in (d.get("unresponsive_engines") or []) if u][:5],
    }, headers={"Cache-Control": "no-store"})


# --- Aufgaben & Notizen (pro Benutzer) --------------------------------------
# Sicherheitsprinzip: JEDE Abfrage filtert auf die uid aus der Session.
# Fremde oder unbekannte IDs liefern 404 (nicht 403), damit nicht einmal
# sichtbar wird, ob es die ID gibt.
TASK_MAX_LEN, TASK_MAX_COUNT = 500, 500
NOTE_TITLE_MAX, NOTE_BODY_MAX = 200, 20000
NOTE_MAX_COUNT = int(E.get("NOTE_MAX_COUNT", "5000"))   # Seiten pro Konto (inkl. Papierkorb)
PRIOS = {1, 2, 3}                                    # 1 hoch, 2 normal, 3 niedrig
REPEATS = {"daily", "weekdays", "weekly", "monthly", "yearly"}
TASK_COLS = "id, text, done, due, prio, repeat"


def _due(v):
    """'' / None -> None, 'YYYY-MM-DD' -> str, sonst ValueError."""
    if v in (None, ""):
        return None
    return date.fromisoformat(str(v)).isoformat()


def _prio(v):
    p = int(v)
    if p not in PRIOS:
        raise ValueError
    return p


def _repeat(v):
    if v in (None, "", "none"):
        return None
    if v not in REPEATS:
        raise ValueError
    return v


def _client_today(b):
    """'Heute' aus Sicht des Browsers (Zeitzone des Benutzers), sonst Server-Datum."""
    try:
        t = date.fromisoformat(str(b.get("today")))
        if abs((t - date.today()).days) <= 1:   # Plausibilitaet: max. 1 Tag Zeitzonen-Versatz
            return t
    except (ValueError, TypeError):
        pass
    return date.today()


def next_due(due_iso, repeat, anchor_iso, today):
    """Naechstes Faelligkeitsdatum einer wiederkehrenden Aufgabe.

    - Monatlich/jaehrlich richtet sich nach dem Tag des Ursprungsdatums (anchor):
      31.01. -> 28./29.02. -> 31.03. (kein schleichendes Wandern auf den 28.)
    - Ueberfaellige Wiederholungen stapeln sich nicht: es wird bis zum ersten
      Termin >= heute vorgespult.
    """
    d = date.fromisoformat(due_iso) if due_iso else today
    a = date.fromisoformat(anchor_iso) if anchor_iso else d

    def step(x):
        if repeat == "daily":
            return x + timedelta(days=1)
        if repeat == "weekdays":
            x += timedelta(days=1)
            while x.weekday() >= 5:
                x += timedelta(days=1)
            return x
        if repeat == "weekly":
            return x + timedelta(weeks=1)
        if repeat == "monthly":
            y, m = (x.year + 1, 1) if x.month == 12 else (x.year, x.month + 1)
            return date(y, m, min(a.day, monthrange(y, m)[1]))
        if repeat == "yearly":
            y = x.year + 1
            return date(y, a.month, min(a.day, monthrange(y, a.month)[1]))
        raise ValueError(repeat)

    n = step(d)
    for _ in range(5000):          # Schutz gegen Endlosschleife bei absurden Daten
        if n >= today:
            break
        n = step(n)
    return n.isoformat()


def _task_row(r):
    return {"id": r[0], "text": r[1], "done": bool(r[2]), "due": r[3], "prio": r[4], "repeat": r[5]}


def _note_row(r):
    return {"id": r[0], "title": r[1], "body": r[2], "updated": r[3]}


async def _body(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else None
    except Exception:
        return None


def _bad(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


@app.get("/api/tasks")
def api_tasks(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        open_ = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=0 "
                          "ORDER BY prio, due IS NULL, due, created", (uid,)).fetchall()
        done = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=1 "
                         "ORDER BY done_at DESC LIMIT 50", (uid,)).fetchall()
    return JSONResponse({"open": [_task_row(r) for r in open_], "done": [_task_row(r) for r in done]},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/tasks")
async def api_task_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    text = str(b.get("text", "")).strip()[:TASK_MAX_LEN]
    if not text:
        return _bad("leere Aufgabe")
    try:
        due = _due(b.get("due"))
        prio = _prio(b.get("prio", 2))
        rep = _repeat(b.get("repeat"))
    except (ValueError, TypeError):
        return _bad("ungueltige Angabe (Datum, Prioritaet oder Wiederholung)")
    if rep and not due:                      # Wiederholung braucht ein Datum: ab heute
        due = _client_today(b).isoformat()
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM task WHERE uid=?", (uid,)).fetchone()[0] >= TASK_MAX_COUNT:
            return _bad(f"maximal {TASK_MAX_COUNT} Aufgaben", 409)
        cur = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                        (uid, text, due, prio, rep, due if rep else None, time.time()))
        return JSONResponse({"id": cur.lastrowid})


def _task_done(c, uid, tid, want, done, text, due, prio, rep, anchor, next_id, today):
    """Abhaken / wieder oeffnen - gemeinsam fuer Web, App und Widget. Liefert (next_id, Faelligkeit der Folgeaufgabe)."""
    if bool(want) == bool(done):
        return next_id, None
    nd = None
    if want:
        if rep:   # Wiederkehrend: Folgeaufgabe anlegen
            nd = next_due(due, rep, anchor, today)
            next_id = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                                (uid, text, nd, prio, rep, anchor, time.time())).lastrowid
        c.execute("UPDATE task SET done=1, done_at=? WHERE id=? AND uid=?", (time.time(), tid, uid))
    else:
        if next_id:  # Haken zurueckgenommen: noch offene, automatisch erzeugte Folgeaufgabe entfernen
            c.execute("DELETE FROM task WHERE id=? AND uid=? AND done=0", (next_id, uid))
            next_id = None
        c.execute("UPDATE task SET done=0, done_at=NULL WHERE id=? AND uid=?", (tid, uid))
    return next_id, nd


@app.patch("/api/tasks/{tid}")
async def api_task_update(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    with _db() as c, c:
        row = c.execute("SELECT text, done, due, prio, repeat, rep_anchor, next_id FROM task WHERE id=? AND uid=?",
                        (tid, uid)).fetchone()
        if not row:
            return _bad("not found", 404)
        text, done, due, prio, rep, anchor, next_id = row
        out = {"ok": True}

        # --- Felder aendern
        try:
            if "text" in b:
                text = str(b["text"]).strip()[:TASK_MAX_LEN]
                if not text:
                    return _bad("leere Aufgabe")
            if "due" in b:
                due = _due(b["due"])
                if rep:
                    anchor = due
            if "prio" in b:
                prio = _prio(b["prio"])
            if "repeat" in b:
                new_rep = _repeat(b["repeat"])
                if new_rep and not due:
                    due = _client_today(b).isoformat()
                if new_rep != rep:
                    anchor = due if new_rep else None
                rep = new_rep
        except (ValueError, TypeError):
            return _bad("ungueltige Angabe (Datum, Prioritaet oder Wiederholung)")

        # --- Abhaken / wieder oeffnen
        if "done" in b:
            next_id, nd = _task_done(c, uid, tid, b["done"], done, text, due, prio, rep, anchor, next_id, _client_today(b))
            if nd:
                out["next_due"] = nd

        c.execute("UPDATE task SET text=?, due=?, prio=?, repeat=?, rep_anchor=?, next_id=? WHERE id=? AND uid=?",
                  (text, due, prio, rep, anchor, next_id, tid, uid))
    return JSONResponse(out)


@app.delete("/api/tasks/{tid}")
def api_task_delete(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM task WHERE id=? AND uid=?", (tid, uid)).rowcount
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


@app.post("/api/tasks/clear-done")
def api_task_clear(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM task WHERE uid=? AND done=1", (uid,)).rowcount
    return JSONResponse({"deleted": n})


# --- Notizen als Seiten (Notion-Stil) ----------------------------------------
# Inhalt: Editor.js-Bloecke als JSON (format="blocks") oder alter Klartext (format="text").
# Seiten koennen Unterseiten haben (parent_id). Alles strikt pro Benutzer.
NOTE_BLOCKS_MAX = 200_000          # Zeichen JSON pro Seite
NOTE_ICON_MAX = 8
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "mark", "code", "a", "br", "time"}
_ALLOWED_CLASSES = {"mark": {"cdx-marker"}, "code": {"inline-code"}, "time": {"hk-date", "hk-remind"}}
_TIME_ATTR = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")      # @-Datum im Text: <time datetime="2026-10-10">


_NOTE_HREF = re.compile(r"/notes#[1-9][0-9]{0,8}")


class _Sanitizer(HTMLParser):
    """Positivliste fuer Inline-HTML in Bloecken. Alles andere wird zu Text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            return
        a = dict(attrs)
        keep = ""
        if tag == "a":
            href = (a.get("href") or "").strip()
            if _NOTE_HREF.fullmatch(href):          # interner Seitenverweis, exakt /notes#<zahl>
                keep = f' href="{href}"'
            elif urlparse(href).scheme in ("http", "https", "mailto"):
                keep = f' href="{html_escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
            else:
                return
        if tag == "time":
            dt = (a.get("datetime") or "").strip()
            if not _TIME_ATTR.fullmatch(dt):
                return
            keep = f' datetime="{dt}"'
        cls = [c for c in (a.get("class") or "").split() if c in _ALLOWED_CLASSES.get(tag, set())]
        if cls:
            keep += f' class="{" ".join(cls)}"'
        if tag == "br":
            self.out.append("<br>")
            return
        self.out.append(f"<{tag}{keep}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(html_escape(data, quote=False))

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_inline(s):
    p = _Sanitizer()
    p.feed(s)
    p.close()
    return p.result()


def _sanitize_value(v, key=None):
    if isinstance(v, str):
        return v if key == "code" else sanitize_inline(v)     # Code-Bloecke sind Klartext (Textarea)
    if isinstance(v, list):
        return [_sanitize_value(x, key) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_value(x, k) for k, x in v.items()}
    return v


MAX_NEST, MAX_JSON_DEPTH = 8, 48


def _iter_blocks(blocks, depth=0):
    """Alle Bloecke inkl. verschachtelter (Spalten, Toggles) - fuer Pruefungen, Export, Vorschau."""
    if depth > MAX_NEST:
        raise ValueError("zu tief verschachtelt")
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        yield b
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            for col in d.get("cols") if isinstance(d.get("cols"), list) else []:
                if isinstance(col, dict):
                    yield from _iter_blocks(col.get("blocks"), depth + 1)
        elif b.get("type") == "toggle":
            yield from _iter_blocks(d.get("blocks"), depth + 1)


def _json_depth_ok(obj, limit=MAX_JSON_DEPTH):
    """Iterativ (ohne Rekursion) pruefen, dass JSON nicht absurd tief verschachtelt ist."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if d > limit:
            return False
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)
    return True


def clean_blocks(raw):
    """JSON-String pruefen und bereinigen -> (json_string, plaintext). ValueError bei Unsinn."""
    if len(raw) > NOTE_BLOCKS_MAX:
        raise ValueError("Seite zu gross")
    d = json.loads(raw)
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError("kein Block-Dokument")
    if not _json_depth_ok(d):
        raise ValueError("zu tief verschachtelt")
    blocks = []
    for b in d["blocks"]:
        if not isinstance(b, dict) or not isinstance(b.get("type"), str) or not isinstance(b.get("data"), dict):
            raise ValueError("ungueltiger Block")
        blocks.append({k: (_sanitize_value(v) if k == "data" else v) for k, v in b.items() if k in ("id", "type", "data")})
    blocks = _flatten_nested_columns(blocks)
    doc = {"time": d.get("time"), "version": d.get("version"), "blocks": blocks}
    return json.dumps(doc, ensure_ascii=False), blocks_text(blocks)


def _flatten_nested_columns(blocks, nested=False):
    """Spalten in Spalten/Toggles kann der Editor nicht darstellen -> innere Spalten-Bloecke aufloesen:
    ihr Inhalt (Spalte 1, dann 2, ...) rueckt an ihre Stelle. Nichts geht verloren."""
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            cols = [c for c in (d.get("cols") or []) if isinstance(c, dict)]
            for c in cols:
                c["blocks"] = _flatten_nested_columns(c.get("blocks") if isinstance(c.get("blocks"), list) else [], True)
            if nested:
                for c in cols:
                    out.extend(c["blocks"])
                continue
        elif b.get("type") == "toggle" and isinstance(d.get("blocks"), list):
            d["blocks"] = _flatten_nested_columns(d["blocks"], True)
        out.append(b)
    return out


def _strip_tags(s):
    return html_unescape(re.sub(r"<[^>]*>", " ", s))


def blocks_text(blocks):
    """Klartext aus Bloecken (fuer Suche und Vorschau)."""
    parts = []

    def walk(v, key=None):
        if isinstance(v, str):
            if key == "code":                 # Code-Bloecke woertlich durchsuchbar lassen
                parts.append(v)
            elif key not in ("style", "level", "alignment", "language", "checked", "url", "extension",
                             "size", "emoji", "withBorder", "withBackground", "stretched", "withHeadings", "open"):
                parts.append(_strip_tags(v))
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("type", "id", "tunes"):     # technische Felder verschachtelter Bloecke
                    walk(x, k)

    for b in blocks:
        walk(b.get("data", {}))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _note_text(fmt, body):
    if fmt == "blocks":
        try:
            return blocks_text(json.loads(body).get("blocks", []))
        except (ValueError, AttributeError):
            return ""
    return body or ""


TAG_MAX_LEN, TAG_MAX_COUNT = 30, 20
_TAG_RE = re.compile(r"[\w\- .äöüÄÖÜß]+")


def _tags(v):
    """Liste von Tags -> bereinigt, ohne Dubletten (Gross/Klein egal), max. 20 a 30 Zeichen."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("Tags muessen eine Liste sein")
    out, seen = [], set()
    for x in v:
        s = re.sub(r"\s+", " ", str(x)).strip().lstrip("#")[:TAG_MAX_LEN]
        if not s:
            continue
        if not _TAG_RE.fullmatch(s):
            raise ValueError("unerlaubte Zeichen im Tag")
        if s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    if len(out) > TAG_MAX_COUNT:
        raise ValueError(f"maximal {TAG_MAX_COUNT} Tags")
    return out


def _tags_load(s):
    try:
        v = json.loads(s) if s else []
        return [str(x) for x in v] if isinstance(v, list) else []
    except ValueError:
        return []


def _own_image(c, uid, url):
    """True, wenn url = /files/<id> ein eigenes, am Inhalt als Bild erkanntes Upload ist."""
    m = _FILE_URL.fullmatch(str(url or ""))
    r = m and c.execute("SELECT is_image FROM file WHERE id=? AND uid=?", (m.group(1), uid)).fetchone()
    return bool(r and r[0])


def _icon(v, c=None, uid=None):
    """Emoji (max. 8 Zeichen) oder eigenes hochgeladenes Bild (/files/<id>)."""
    s = str(v or "").strip()
    if s.startswith("/files/"):
        if c is None or not _own_image(c, uid, s):
            raise ValueError("Icon-Bild muss ein eigenes hochgeladenes Bild sein")
        return s
    s = s[:NOTE_ICON_MAX]
    if any(ch in s for ch in "<>&\"'"):
        raise ValueError
    return s


def _check_parent(c, uid, nid, parent):
    """parent muss eigene Seite sein und darf nicht nid selbst oder eine Unterseite von nid sein."""
    if parent is None:
        return None
    parent = int(parent)
    if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (parent, uid)).fetchone():
        raise LookupError
    if nid is not None:
        sub = {r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=?) SELECT id FROM t",
            (nid, uid))}
        if parent in sub:
            raise ValueError("Seite kann nicht unter sich selbst oder eine eigene Unterseite verschoben werden")
    return parent


NOTE_META_COLS = "id, title, icon, parent_id, updated, tags, pinned, is_template, kind"


def _note_meta(r):
    return {"id": r[0], "title": r[1], "icon": r[2] or "", "parent_id": r[3], "updated": r[4], "tags": _tags_load(r[5]), "pinned": bool(r[6]), "template": bool(r[7]), "kind": r[8] or "page"}


@app.get("/api/notes")
def api_notes(request: Request):
    """Seitenbaum (ohne Inhalte)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _purge_old(c, uid)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, archived, locked FROM note WHERE uid=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                         (uid,)).fetchall()
    return JSONResponse([dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10])) for r in rows], headers={"Cache-Control": "no-store"})


@app.get("/api/notes/{nid}")
def api_note_get(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        r = c.execute(f"SELECT {NOTE_META_COLS}, body, format, schema, props, cover, cover_pos, created, wide, rev, archived, locked FROM note WHERE id=? AND uid=? AND deleted_at IS NULL",
                      (nid, owner)).fetchone()
    if not r:
        return _bad("not found", 404)
    d = _note_meta(r)
    d.update(body=r[9], format=r[10] or "text", props=_json_load(r[12], {}), cover=r[13] or "",
             cover_pos=50.0 if r[14] is None else r[14], created=r[15], wide=bool(r[16]), rev=r[17], archived=bool(r[18]), locked=bool(r[19]))
    if d["kind"] == "database":
        d["schema"] = _json_load(r[11], {"props": []})
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


@app.post("/api/notes")
async def api_note_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request) or {}
    now = time.time()
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (owner,)).fetchone()[0] >= NOTE_MAX_COUNT:
            return _bad(f"maximal {NOTE_MAX_COUNT} Seiten (inkl. Papierkorb)", 409)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
            icon = _icon(b.get("icon"), c, owner)
            tags = _tags(b.get("tags"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError):
            return _bad("ungueltige Angabe")
        title = str(b.get("title", "")).strip()[:NOTE_TITLE_MAX]
        kind = "database" if b.get("kind") == "database" else "page"
        schema = json.dumps(_default_schema(), ensure_ascii=False) if kind == "database" else None
        props = None
        if parent and b.get("props"):
            ps = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (parent, owner)).fetchone()
            if ps:
                try:
                    props = json.dumps(_clean_props(_json_load(ps[0], {"props": []}), b["props"], check=_rel_check(c, owner)), ensure_ascii=False)
                except (ValueError, TypeError) as ex:
                    return _bad(str(ex) or "ungueltige Werte")
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, json.dumps({"blocks": []}), "blocks", icon, parent, now,
                         json.dumps(tags, ensure_ascii=False), kind, schema, props, now, now))
        if props and ps:                                   # zweiseitige Relationen: Gegenseite nachziehen
            _rel_sync(c, owner, _json_load(ps[0], {"props": []}), cur.lastrowid, {}, json.loads(props))
        return JSONResponse({"id": cur.lastrowid, "updated": now})


@app.patch("/api/notes/{nid}")
async def api_note_update(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    sets, args = [], []
    with _db() as c, c:
        old = c.execute("SELECT title, body, format, rev, locked FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not old:
            return _bad("not found", 404)
        if old[4] and b.get("locked") is not False and ("body" in b or "title" in b):     # gesperrt: Inhalt/Titel bleiben unveraendert
            return JSONResponse({"error": "Seite ist gesperrt – erst entsperren"}, status_code=423)
        # Konflikterkennung: wer Titel/Inhalt speichert und die gekannte Revision mitschickt, ueberschreibt
        # keine zwischenzeitliche Aenderung (anderer Tab, Peek, spaeter: andere Person). Ohne 'rev' wie bisher.
        content = "body" in b or "title" in b
        if content and b.get("rev") is not None:
            try:
                want = int(b["rev"])
            except (TypeError, ValueError):
                return _bad("ungueltige Revision")
            if want != old[3]:
                return JSONResponse({"error": "conflict", "rev": old[3]}, status_code=409)
        try:
            if "pinned" in b:
                sets.append("pinned=?"); args.append(1 if b["pinned"] else 0)
            if "template" in b:
                sets.append("is_template=?"); args.append(1 if b["template"] else 0)
            if "wide" in b:
                sets.append("wide=?"); args.append(1 if b["wide"] else 0)
            if "locked" in b:
                sets.append("locked=?"); args.append(1 if b["locked"] else 0)
            if "schema" in b:
                me = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
                if me[0] != "database":
                    raise ValueError("nur Datenbanken haben ein Schema")
                new_schema = _clean_schema(b["schema"])
                old_s = _json_load((c.execute("SELECT schema FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone() or [None])[0], {"props": []})
                if old_s.get("locked") and new_schema.get("locked"):     # gesperrt: nur Entsperren ist erlaubt
                    strip = lambda s: json.dumps([s.get("props"), s.get("views"), s.get("title_name")], sort_keys=True)
                    if strip(new_schema) != strip(_clean_schema(old_s)):
                        raise ValueError("Die Datenbank ist gesperrt – zum Ändern erst unter „Ansicht“ entsperren")
                old_by = {p["id"]: p for p in old_s.get("props", [])}
                old_rel = {k: p.get("target") for k, p in old_by.items() if p.get("type") == "relation"}
                for rp in new_schema["props"]:          # Relation nur auf eigene, aktive Datenbanken
                    if rp["type"] == "relation" and old_rel.get(rp["id"]) != rp["target"] and not c.execute(
                            "SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (rp["target"], owner)).fetchone():
                        raise ValueError("Ziel-Datenbank der Relation nicht gefunden")
                # Zweiseitige Relationen: Kopplung (pair) stellt NUR der Server her - vom Browser nur uebernehmen, was schon so bestand
                raw_by = {str(p.get("id")): p for p in b["schema"].get("props", []) if isinstance(p, dict)}
                my_title = c.execute("SELECT title FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                for rp in new_schema["props"]:
                    if rp["type"] != "relation":
                        continue
                    prev = old_by.get(rp["id"]) or {}
                    if prev.get("pair") and prev.get("target") == rp["target"]:
                        rp["pair"] = prev["pair"]                # bestehende Kopplung bleibt, egal was der Browser schickt
                    else:
                        rp.pop("pair", None)
                    if prev.get("pair") and prev.get("target") != rp["target"]:      # Ziel gewechselt: alte Gegenspalte entfernen
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], True)
                    want = raw_by.get(rp["id"], {}).get("two_way")
                    if want is True and not rp.get("pair") and rp["target"] != nid:
                        rp["pair"] = _rel_link(c, owner, nid, my_title, rp)
                    elif want is False and rp.get("pair"):
                        _rel_unlink(c, owner, nid, rp["target"], rp.pop("pair"), True)
                new_ids = {p["id"] for p in new_schema["props"]}
                for k, prev in old_by.items():                                    # Spalte geloescht: Gegenseite bleibt, nur entkoppelt
                    if k not in new_ids and prev.get("type") == "relation" and prev.get("pair"):
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], False)
                sets.append("schema=?"); args.append(json.dumps(new_schema, ensure_ascii=False))
                # Werte, die nach Typ-/Optionsaenderung nicht mehr passen, aus allen Zeilen entfernen
                # (Mehrfachauswahl: nur geloeschte Optionen herausfiltern, nicht die ganze Liste verwerfen)
                by_id = {p["id"]: p for p in new_schema["props"]}
                for rid, rp in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=?", (owner, nid)).fetchall():
                    old_p, keep = _json_load(rp, {}), {}
                    for k, v in old_p.items():
                        pr = by_id.get(k)
                        if pr and pr["type"] == "multi_select" and isinstance(v, list):
                            v = [x for x in v if x in {o["id"] for o in pr.get("options", [])}]
                        try:
                            keep.update(_clean_props(new_schema, {k: v}, check=_rel_check(c, owner)))   # Relation: nur IDs aus dem (neuen) Ziel
                        except (ValueError, TypeError):
                            pass
                    if keep != old_p:
                        c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(keep, ensure_ascii=False), rid, owner))
            if "props" in b:
                ps = c.execute("SELECT d.schema, n.props FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                               "WHERE n.id=? AND n.uid=? AND d.kind='database'", (nid, owner)).fetchone()
                if not ps:
                    raise ValueError("Seite ist keine Datenbank-Zeile")
                merged = _clean_props(_json_load(ps[0], {"props": []}), b["props"], _json_load(ps[1], {}), check=_rel_check(c, owner))
                _rel_sync(c, owner, _json_load(ps[0], {"props": []}), nid, _json_load(ps[1], {}), merged)
                sets.append("props=?"); args.append(json.dumps(merged, ensure_ascii=False))
            if "title" in b:
                sets.append("title=?"); args.append(str(b["title"]).strip()[:NOTE_TITLE_MAX])
            if "icon" in b:
                sets.append("icon=?"); args.append(_icon(b["icon"], c, owner))
            if "cover" in b:
                cv = b["cover"]
                if cv in (None, ""):
                    sets.append("cover=?"); args.append(None)
                elif _own_image(c, owner, cv):
                    sets.append("cover=?"); args.append(cv)
                else:
                    raise ValueError("Titelbild muss ein eigenes hochgeladenes Bild sein")
            if "cover_pos" in b:
                sets.append("cover_pos=?"); args.append(max(0.0, min(100.0, float(b["cover_pos"]))))
            if "tags" in b:
                sets.append("tags=?"); args.append(json.dumps(_tags(b["tags"]), ensure_ascii=False))
            if "body" in b:
                fmt = b.get("format", "blocks")
                if fmt == "blocks":
                    body, _ = clean_blocks(str(b["body"]))
                    _check_file_refs(owner, json.loads(body)["blocks"])
                elif fmt == "text":
                    body = str(b["body"])
                    if len(body) > NOTE_BODY_MAX:
                        return _bad(f"Notiz zu lang (max. {NOTE_BODY_MAX} Zeichen)", 413)
                else:
                    raise ValueError("unbekanntes Format")
                sets += ["body=?", "format=?"]; args += [body, fmt]
            if "parent_id" in b:
                newpar = _check_parent(c, owner, nid, b["parent_id"])
                if newpar is not None:          # wie beim Verschieben: keine normale Seite in eine Datenbank
                    cur_par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                    if c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (newpar, owner)).fetchone()[0] == "database" and cur_par != newpar:
                        raise ValueError("Seiten koennen nicht in eine Datenbank verschoben werden")
                sets.append("parent_id=?"); args.append(newpar)
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(str(ex) if str(ex) else "ungueltige Angabe", 413 if "gross" in str(ex) else 400)
        if not sets:
            return _bad("nichts zu aendern")
        now = time.time()
        if "body" in b:
            _snapshot(c, owner, nid, old, now)
        if content:
            sets.append("rev=rev+1")
        c.execute(f"UPDATE note SET {', '.join(sets)}, updated=? WHERE id=? AND uid=?", (*args, now, nid, owner))
    return JSONResponse({"ok": True, "updated": now, "rev": old[3] + (1 if content else 0)})


@app.post("/api/notes/{nid}/move")
async def api_note_move(nid: int, request: Request):
    """Seite im Baum verschieben: neue Eltern (oder oberste Ebene) und Position unter den Geschwistern.
    Die Geschwister werden danach sauber durchnummeriert (sort = 1..n)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request) or {}
    try:
        index = max(0, int(b.get("index", 0)))
    except (TypeError, ValueError):
        return _bad("ungueltige Position")
    with _db() as c, c:
        me = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        try:
            parent = _check_parent(c, owner, nid, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(str(ex) or "ungueltige Angabe")
        if parent is not None:
            pk = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (parent, owner)).fetchone()[0]
            if pk == "database" and me[0] != parent:
                return _bad("Seiten koennen nicht in eine Datenbank verschoben werden")
        sib = [r[0] for r in c.execute(
            "SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id<>? AND "
            + ("parent_id=?" if parent is not None else "parent_id IS NULL") + " ORDER BY sort NULLS FIRST, created",
            (owner, nid, parent) if parent is not None else (owner, nid))]
        sib.insert(min(index, len(sib)), nid)
        c.execute("UPDATE note SET parent_id=? WHERE id=? AND uid=?", (parent, nid, owner))
        c.executemany("UPDATE note SET sort=? WHERE id=? AND uid=?", [(i + 1, x, owner) for i, x in enumerate(sib)])
    return JSONResponse({"ok": True, "parent_id": parent, "index": sib.index(nid)})


@app.post("/api/notes/{nid}/archive")
async def api_note_archive(nid: int, request: Request):
    """Archivieren (archived=true) samt aktiver Unterseiten - gemeinsamer Zeitstempel, damit Zurueckholen genau
    diese Seiten zurueckbringt. Archivierte Seiten fehlen in Baum und Suche, bleiben aber per Link erreichbar."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid
    b = await _body(request) or {}
    on = bool(b.get("archived", True))
    with _db() as c, c:
        me = c.execute("SELECT archived, parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if on:
            if me[0] is not None:
                return JSONResponse({"ok": True, "archived": 0})
            now = time.time()
            n = sum(c.execute("UPDATE note SET archived=? WHERE id=? AND uid=? AND archived IS NULL", (now, i, owner)).rowcount for i in ids)
            return JSONResponse({"ok": True, "archived": n})
        if me[0] is None:
            return JSONResponse({"ok": True, "restored": 0})
        n = sum(c.execute("UPDATE note SET archived=NULL WHERE id=? AND uid=? AND archived=?", (i, owner, me[0])).rowcount for i in ids)
        if me[1]:             # Eltern noch archiviert (und keine Datenbank): Seite nach oben holen, sonst waere sie unsichtbar
            par = c.execute("SELECT archived, kind FROM note WHERE id=? AND uid=?", (me[1], owner)).fetchone()
            if par and par[0] is not None and par[1] != "database":
                c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, owner))
        return JSONResponse({"ok": True, "restored": n})


@app.delete("/api/notes/{nid}")
def api_note_delete(nid: int, request: Request):
    """Verschiebt die Seite samt aktiver Unterseiten in den Papierkorb (gemeinsamer Zeitstempel)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c, c:
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if not ids:
            return _bad("not found", 404)
        now = time.time()
        c.executemany("UPDATE note SET deleted_at=? WHERE id=? AND uid=?", [(now, i, owner) for i in ids])
    return JSONResponse({"deleted": len(ids), "trash": True})


@app.post("/api/notes/search")
async def api_note_search(request: Request):
    """Volltextsuche in Titel, Tags und Inhalt (POST: Suchbegriff landet nicht im Log).
    'tag' (exakt, Gross/Klein egal) filtert zusaetzlich nach einem Tag."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    q = str(b.get("q", "")).strip().lower()[:100]
    tag = str(b.get("tag", "")).strip().lower()[:TAG_MAX_LEN]
    if not q and not tag:
        return JSONResponse([])
    hits = []
    with _db() as c:
        for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND archived IS NULL ORDER BY updated DESC", (uid,)):
            d = _note_meta(r)
            tags_l = [x.lower() for x in d["tags"]]
            if tag and tag not in tags_l:
                continue
            if q:
                text = _note_text(r[10], r[9])
                i = text.lower().find(q)
                if not (q in (r[1] or "").lower() or i >= 0 or any(q in x for x in tags_l)):
                    continue
                if i >= 0:
                    s = max(0, i - 40)
                    d["snippet"] = ("…" if s else "") + text[s:i + len(q) + 60] + ("…" if i + len(q) + 60 < len(text) else "")
            hits.append(d)
            if len(hits) >= 50:
                break
    return JSONResponse(hits, headers={"Cache-Control": "no-store"})


@app.get("/api/notes/{nid}/backlinks")
def api_note_backlinks(nid: int, request: Request):
    """Eigene Seiten, die auf diese Seite verweisen (Rueckverweise)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        # Im gespeicherten JSON steht der Link als href=\"/notes#<id>\" - der Backslash vor dem
        # Anfuehrungszeichen grenzt die Zahl ab (#1 trifft nicht #12).
        rows = c.execute(f"SELECT {NOTE_META_COLS} FROM note WHERE uid=? AND id<>? AND format='blocks' AND deleted_at IS NULL "
                         "AND instr(body, ?) > 0 ORDER BY updated DESC",
                         (owner, nid, f'/notes#{nid}\\"')).fetchall()
    return JSONResponse([_note_meta(r) for r in rows], headers={"Cache-Control": "no-store"})


@app.get("/api/services")
def api_services(request: Request):
    """Dienste, die der Benutzer sehen darf, und seine eigenen Lesezeichen (own=true) - fuer Apps und Verweise in Seiten."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    out = [{"name": s["name"], "url": s["url"], "host": s["host"], "category": s["category"], "icon": s.get("icon") or "",
            "own": bool(s.get("own")), "key": s["key"]} for s in visible_tiles(uid).values()]
    out.sort(key=lambda s: (s["category"].lower(), s["name"].lower()))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


# Dienst-Symbole (selfh.st, wie die Kacheln im Web) fuer die Apps: einmal vom CDN holen, als PNG ablegen.
# Die Apps brauchen so kein Internet und kein SVG; ein fehlendes Symbol wird einen Tag lang nicht erneut gesucht.
SVC_ICON_DIR = os.path.join(STATE_DIR, "svc-icons")
SVC_ICON_CDN = (E.get("ICON_CDN", "https://cdn.jsdelivr.net/gh/selfhst/icons")).rstrip("/")   # leer = keine Symbole aus dem Internet
_svc_icon_lock = threading.Lock()


def _svc_icon_fetch(name):
    if not SVC_ICON_CDN:
        return None
    with httpx.Client(timeout=10, follow_redirects=False) as cl:
        r = cl.get(f"{SVC_ICON_CDN}/png/{name}.png")
        if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) < 2_000_000:
            return r.content
        r = cl.get(f"{SVC_ICON_CDN}/webp/{name}.webp")
        if r.status_code == 200 and r.content[8:12] == b"WEBP" and len(r.content) < 2_000_000:
            im = Image.open(io.BytesIO(r.content))
            im.thumbnail((256, 256))
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    return None


def svc_icon_path(name):
    """-> (pfad, None) oder (None, http-status). Einmal vom CDN holen und als PNG ablegen."""
    os.makedirs(SVC_ICON_DIR, exist_ok=True)
    path, miss = os.path.join(SVC_ICON_DIR, name + ".png"), os.path.join(SVC_ICON_DIR, name + ".missing")
    with _svc_icon_lock:
        if not os.path.isfile(path):
            if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400:
                return None, 404
            try:
                data = _svc_icon_fetch(name)
            except (httpx.HTTPError, OSError, ValueError):
                return None, 502                        # nicht merken: CDN kurz weg
            if not data:
                open(miss, "w").close()
                return None, 404
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
    return path, None


@app.get("/api/services/icon/{name}")
def api_service_icon(name: str, request: Request):
    if not _session_user(request) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return PlainTextResponse("not found", status_code=404)
    path, code = svc_icon_path(name)
    if not path:
        return PlainTextResponse("not found", status_code=code)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=604800"})


# --- Papierkorb ----------------------------------------------------------------
TRASH_DAYS = 30
VERSION_EVERY, VERSION_MAX = 600, 50     # hoechstens alle 10 Minuten eine Sicherung, max. 50 pro Seite


def _purge_ids(c, uid, ids):
    c.executemany("DELETE FROM note_version WHERE uid=? AND note_id=?", [(uid, i) for i in ids])
    c.executemany("DELETE FROM note WHERE uid=? AND id=?", [(uid, i) for i in ids])


def _purge_old(c, uid):
    old = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NOT NULL AND deleted_at < ?",
                                   (uid, time.time() - TRASH_DAYS * 86400))]
    if old:
        _purge_ids(c, uid, old)


def _trash_batch(c, uid, nid):
    """Seite im Papierkorb + alle mit ihr zusammen geloeschten Unterseiten (gleicher Zeitstempel)."""
    r = c.execute("SELECT deleted_at FROM note WHERE id=? AND uid=? AND deleted_at IS NOT NULL", (nid, uid)).fetchone()
    if not r:
        return None, []
    ids = [x[0] for x in c.execute(
        "WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id "
        "WHERE n.uid=? AND n.deleted_at=?) SELECT id FROM t", (nid, uid, r[0]))]
    return r[0], ids


@app.get("/api/trash")
def api_trash(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _purge_old(c, uid)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, deleted_at FROM note n WHERE uid=? AND deleted_at IS NOT NULL "
                         "AND NOT EXISTS (SELECT 1 FROM note p WHERE p.id=n.parent_id AND p.uid=n.uid AND p.deleted_at=n.deleted_at) "
                         "ORDER BY deleted_at DESC", (uid,)).fetchall()
        out = []
        for r in rows:
            d = _note_meta(r)
            _, ids = _trash_batch(c, uid, r[0])
            d.update(deleted_at=r[9], subpages=len(ids) - 1, purge_at=r[9] + TRASH_DAYS * 86400)
            out.append(d)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.post("/api/trash/{nid}/restore")
def api_trash_restore(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _, ids = _trash_batch(c, uid, nid)
        if not ids:
            return _bad("not found", 404)
        c.executemany("UPDATE note SET deleted_at=NULL WHERE id=? AND uid=?", [(i, uid) for i in ids])
        # Elternseite weg oder selbst im Papierkorb -> auf oberste Ebene
        par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, uid)).fetchone()[0]
        if par and not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (par, uid)).fetchone():
            c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, uid))
    return JSONResponse({"restored": len(ids)})


@app.delete("/api/trash/{nid}")
def api_trash_purge(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _, ids = _trash_batch(c, uid, nid)
        if not ids:
            return _bad("not found", 404)
        _purge_ids(c, uid, ids)
    return JSONResponse({"purged": len(ids)})


@app.post("/api/trash/empty")
def api_trash_empty(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        ids = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NOT NULL", (uid,))]
        _purge_ids(c, uid, ids)
    return JSONResponse({"purged": len(ids)})


# --- Versionsverlauf -------------------------------------------------------------
def _snapshot(c, uid, nid, old, now, force=False):
    """Alten Stand (title, body, format) sichern - hoechstens alle VERSION_EVERY Sekunden."""
    last = c.execute("SELECT MAX(created) FROM note_version WHERE uid=? AND note_id=?", (uid, nid)).fetchone()[0]
    if not force and last and now - last < VERSION_EVERY:
        return
    c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created) VALUES (?,?,?,?,?,?)",
              (nid, uid, old[0], old[1], old[2], now))
    c.execute("DELETE FROM note_version WHERE uid=? AND note_id=? AND id NOT IN "
              "(SELECT id FROM note_version WHERE uid=? AND note_id=? ORDER BY created DESC LIMIT ?)",
              (uid, nid, uid, nid, VERSION_MAX))


@app.get("/api/notes/{nid}/versions")
def api_versions(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        rows = c.execute("SELECT id, title, created, length(body) FROM note_version WHERE uid=? AND note_id=? "
                         "ORDER BY created DESC", (owner, nid)).fetchall()
    return JSONResponse([{"id": r[0], "title": r[1] or "", "created": r[2], "size": r[3] or 0} for r in rows],
                        headers={"Cache-Control": "no-store"})


@app.get("/api/notes/{nid}/versions/{vid}")
def api_version_get(nid: int, vid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        r = c.execute("SELECT v.title, v.body, v.format, v.created FROM note_version v JOIN note n ON n.id=v.note_id AND n.uid=v.uid "
                      "WHERE v.id=? AND v.note_id=? AND v.uid=? AND n.deleted_at IS NULL", (vid, nid, owner)).fetchone()
    if not r:
        return _bad("not found", 404)
    return JSONResponse({"title": r[0] or "", "created": r[3], "text": _note_text(r[2], r[1])[:5000]},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/notes/{nid}/versions/{vid}/restore")
def api_version_restore(nid: int, vid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c, c:
        cur = c.execute("SELECT title, body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        v = c.execute("SELECT title, body, format FROM note_version WHERE id=? AND note_id=? AND uid=?", (vid, nid, owner)).fetchone()
        if not cur or not v:
            return _bad("not found", 404)
        now = time.time()
        _snapshot(c, owner, nid, cur, now, force=True)       # aktueller Stand bleibt als Version erhalten
        c.execute("UPDATE note SET title=?, body=?, format=?, updated=?, rev=rev+1 WHERE id=? AND uid=?", (v[0], v[1], v[2], now, nid, owner))
    return JSONResponse({"ok": True, "updated": now})


# --- Markdown-Export -------------------------------------------------------------
def _md_inline(s, link):
    s = re.sub(r"<br\s*/?>", "  \n", s or "")
    s = re.sub(r"</?(b|strong)>", "**", s)
    s = re.sub(r"</?(i|em)>", "*", s)
    s = re.sub(r"</?s>", "~~", s)
    s = re.sub(r'<code[^>]*>(.*?)</code>', r"`\1`", s, flags=re.S)
    s = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>', lambda m: f"[{m.group(2)}]({link(html_unescape(m.group(1)))})", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return html_unescape(s)


def blocks_to_md(blocks, link=lambda h: h):
    out = []

    def items(lst, style, depth):
        for i, it in enumerate(lst or [], 1):
            if isinstance(it, str):
                it = {"content": it, "items": []}
            pre = "  " * depth + ("- [x] " if style == "checklist" and (it.get("meta") or {}).get("checked") else
                                  "- [ ] " if style == "checklist" else f"{i}. " if style == "ordered" else "- ")
            out.append(pre + _md_inline(it.get("content", ""), link))
            items(it.get("items"), style, depth + 1)

    for b in blocks:
        t, d = b.get("type"), b.get("data") or {}
        if t == "header":
            out += ["#" * min(max(int(d.get("level", 2)), 1), 6) + " " + _md_inline(d.get("text", ""), link), ""]
        elif t == "list":
            items(d.get("items"), d.get("style", "unordered"), 0); out.append("")
        elif t == "quote":
            out += ["> " + l for l in _md_inline(d.get("text", ""), link).split("\n")]
            if d.get("caption"):
                out.append("> — " + _md_inline(d["caption"], link))
            out.append("")
        elif t == "code":
            lang = re.sub(r"[^a-z0-9+-]", "", str(d.get("language") or "").lower())[:20]
            out += ["```" + lang, d.get("code", ""), "```", ""]
        elif t == "delimiter":
            out += ["---", ""]
        elif t == "table":
            rows = d.get("content") or []
            if rows:
                cells = [[_md_inline(str(x), link).replace("|", "\\|").replace("\n", " ") for x in r] for r in rows]
                w = max(len(r) for r in cells)
                cells = [r + [""] * (w - len(r)) for r in cells]
                head, body = (cells[0], cells[1:]) if d.get("withHeadings") else ([""] * w, cells)
                out += ["| " + " | ".join(head) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in body] + [""]
        elif t == "image":
            out += [f"![{_md_inline(d.get('caption', ''), link)}]({link((d.get('file') or {}).get('url', ''))})", ""]
        elif t == "attaches":
            f = d.get("file") or {}
            out += [f"[📎 {d.get('title') or f.get('name') or 'Datei'}]({link(f.get('url', ''))})", ""]
        elif t == "columns":
            for col in d.get("cols") if isinstance(d.get("cols"), list) else []:
                sub = blocks_to_md(col.get("blocks") or [], link).strip() if isinstance(col, dict) else ""
                if sub:
                    out += [sub, ""]
        elif t == "toggle":
            inner = (blocks_to_md(d["blocks"], link).strip() if isinstance(d.get("blocks"), list)
                     else _md_inline(d.get("text", ""), link))
            out += [f"<details><summary>{_md_inline(d.get('title', ''), link)}</summary>", "", inner, "", "</details>", ""]
        elif t == "callout":
            out += ["> " + (d.get("emoji") or "💡") + " " + _md_inline(d.get("text", ""), link).replace("\n", "\n> "), ""]
        else:
            txt = _md_inline(d.get("text", ""), link) if "text" in d else blocks_text([b])
            if txt.strip():
                out += [txt, ""]
    return "\n".join(out).strip() + "\n"


def _disposition(name):
    """Download-Dateiname: HTTP-Header sind Latin-1 -> ASCII-Ersatz + RFC 5987 (UTF-8) fuer moderne Browser."""
    ascii_name = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def _safe_name(s):
    s = re.sub(r"[^\w äöüÄÖÜß.\-]+", "_", s or "Unbenannt").strip(" ._") or "Unbenannt"
    return s[:80]


@app.get("/api/notes/{nid}/export")
def api_note_export(nid: int, request: Request, sub: int = 0):
    """Seite als Markdown (.md) oder mit allen Unterseiten als ZIP (Ordnerstruktur)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        rows = {r[0]: r for r in c.execute("SELECT id, title, icon, parent_id, body, format, kind, schema, props, created, updated FROM note "
                                           "WHERE uid=? AND deleted_at IS NULL", (owner,))}
    if nid not in rows:
        return _bad("not found", 404)

    def val_of(p, row, pv):
        if p["type"] == "calc":
            return _calc_value(p, pv)
        return {"created": row[9], "edited": row[10]}.get(p["type"], pv.get(p["id"]))

    def fmt_val(p, v):
        if v is None:
            return ""
        if p["type"] in AUTO_TYPES:
            return datetime.fromtimestamp(v, TZ).strftime("%d.%m.%Y %H:%M")
        if p["type"] == "relation":
            return ", ".join((rows[x][1] or "Unbenannt") for x in v if x in rows)
        if p["type"] in ("number", "calc"):
            v = round(v, 2) if isinstance(v, float) else v
            return f"{v} %" if p.get("fmt") == "percent" else f"{v} €" if p.get("fmt") == "euro" else str(v)
        if p["type"] in ("select", "multi_select"):
            names = {o["id"]: o["name"] for o in p.get("options", [])}
            return ", ".join(names.get(x, "") for x in (v if isinstance(v, list) else [v]))
        if p["type"] == "checkbox":
            return "✓" if v else ""
        return str(v)

    def md_of(r, link):
        head = f"# {(r[2] + ' ') if r[2] and not r[2].startswith('/files/') else ''}{r[1] or 'Unbenannt'}\n\n"
        par = rows.get(r[3])
        if r[6] == "database":           # Datenbank -> Markdown-Tabelle aller Zeilen
            props = _json_load(r[7], {"props": []}).get("props", [])
            cells = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
            tname = _json_load(r[7], {}).get("title_name") or "Name"
            lines = [f"| {cells(tname)} | " + " | ".join(cells(p["name"]) for p in props) + " |", "|---|" + "---|" * len(props)]
            for x in sorted((x for x in rows.values() if x[3] == r[0]), key=lambda x: x[1] or ""):
                pv = _json_load(x[8], {})
                lines.append(f"| [{cells(x[1] or 'Unbenannt')}]({link('/notes#' + str(x[0]))}) | "
                             + " | ".join(cells(fmt_val(p, val_of(p, x, pv))) for p in props) + " |")
            return head + "\n".join(lines) + "\n"
        if par and par[6] == "database":  # Zeile -> Eigenschaften als Liste unter dem Titel
            pv = _json_load(r[8], {})
            lst = [f"- **{p['name']}:** {fmt_val(p, val_of(p, r, pv))}"
                   for p in _json_load(par[7], {"props": []}).get("props", []) if val_of(p, r, pv) not in (None, "", [])]
            if lst:
                head += "\n".join(lst) + "\n\n"
        if r[5] == "blocks":
            try:
                return head + blocks_to_md(json.loads(r[4]).get("blocks", []), link)
            except ValueError:
                return head
        return head + (r[4] or "") + "\n"

    ext_link = lambda h: (BASE_URL + h) if h.startswith(("/notes#", "/files/")) else h
    if not sub:
        r = rows[nid]
        return Response(md_of(r, ext_link).encode(), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": _disposition(_safe_name(r[1]) + ".md")})
    # ZIP: Seite + Unterseiten, Verweise innerhalb des Exports als relative Links
    paths, used = {}, set()

    def place(i, folder):
        name, k = _safe_name(rows[i][1]), 1
        while f"{folder}{name}.md".lower() in used:
            k += 1; name = f"{_safe_name(rows[i][1])} ({k})"
        used.add(f"{folder}{name}.md".lower()); paths[i] = f"{folder}{name}.md"
        for ch in sorted((x for x in rows.values() if x[3] == i), key=lambda x: x[1] or ""):
            place(ch[0], f"{folder}{name}/")
    place(nid, "")
    # Bilder/Dateien der exportierten Seiten mit einpacken (Ordner _dateien)
    fids = set()
    for i in paths:
        fids |= set(_FILE_URL.findall(rows[i][4] or ""))
    fpaths = {}
    if fids:
        with _db() as c:
            for fid, name in c.execute(f"SELECT id, name FROM file WHERE uid=? AND id IN ({','.join('?' * len(fids))})", (owner, *fids)):
                fpaths[fid] = f"_dateien/{fid[:8]}_{_safe_name(name)}"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, path in paths.items():
            def rel(h, here=path):
                m = re.fullmatch(r"/notes#(\d+)", h)
                if m and int(m.group(1)) in paths:
                    return quote(os.path.relpath(paths[int(m.group(1))], os.path.dirname(here) or "."))
                m = _FILE_URL.fullmatch(h)
                if m and m.group(1) in fpaths:
                    return quote(os.path.relpath(fpaths[m.group(1)], os.path.dirname(here) or "."))
                return ext_link(h)
            z.writestr(path, md_of(rows[i], rel))
        for fid, fp in fpaths.items():
            try:
                z.write(os.path.join(FILES_DIR, fid), fp)
            except FileNotFoundError:
                pass
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": _disposition(_safe_name(rows[nid][1]) + ".zip")})


# --- Dateien & Bilder in Seiten ----------------------------------------------------
# Ablage: STATE_DIR/files/<id> (zufaellige 24-Hex-ID), Metadaten in Tabelle 'file'.
# Bilder (am INHALT erkannt) werden angezeigt, alles andere nur als Download.
FILES_DIR = os.path.join(STATE_DIR, "files")
FILE_MAX = int(E.get("ATTACH_MAX_MB", "10")) * 1024 * 1024
FILE_QUOTA = int(E.get("ATTACH_QUOTA_MB", "500")) * 1024 * 1024
_FILE_URL = re.compile(r"/files/([0-9a-f]{24})")
_IMG_MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
              (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif")]


def _sniff_image(head):
    for sig, mime in _IMG_MAGIC:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def files_init():
    os.makedirs(FILES_DIR, mode=0o700, exist_ok=True)
    with _db() as c, c:
        c.execute("""CREATE TABLE IF NOT EXISTS file (
            id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
            size INTEGER NOT NULL, is_image INTEGER NOT NULL, created REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS file_uid ON file (uid)")


def files_gc(min_age=86400):
    """Dateien loeschen, die in keiner Seite und keiner Version ihres Besitzers mehr vorkommen
    (erst nach min_age Sekunden - frisch hochgeladene Bilder sind evtl. noch nicht gespeichert)."""
    removed = 0
    with _db() as c, c:
        for fid, uid in c.execute("SELECT id, uid FROM file WHERE created < ?", (time.time() - min_age,)).fetchall():
            ref = f"/files/{fid}"
            used = c.execute("SELECT 1 FROM note WHERE uid=? AND (instr(body, ?) > 0 OR icon=? OR cover=?) LIMIT 1",
                             (uid, ref, ref, ref)).fetchone() or \
                c.execute("SELECT 1 FROM note_version WHERE uid=? AND instr(body, ?) > 0 LIMIT 1", (uid, ref)).fetchone()
            if not used:
                c.execute("DELETE FROM file WHERE id=?", (fid,))
                try:
                    os.remove(os.path.join(FILES_DIR, fid))
                except FileNotFoundError:
                    pass
                _drop_thumbs(fid)
                removed += 1
    return removed


def _check_file_refs(uid, blocks):
    """Bild-/Datei-Bloecke duerfen nur auf EIGENE hochgeladene Dateien zeigen."""
    ids = set()
    for b in _iter_blocks(blocks):                        # auch in Spalten und Toggles
        if b.get("type") in ("image", "attaches"):
            url = ((b.get("data") or {}).get("file") or {}).get("url", "")
            m = _FILE_URL.fullmatch(str(url))
            if not m:
                raise ValueError("Bild/Datei muss aus Saga hochgeladen sein")
            ids.add(m.group(1))
    if ids:
        with _db() as c:
            own = {r[0] for r in c.execute(f"SELECT id FROM file WHERE uid=? AND id IN ({','.join('?' * len(ids))})", (uid, *ids))}
        if ids - own:
            raise ValueError("unbekannte Datei")


@app.post("/api/files")
async def api_file_upload(request: Request):
    """Upload aus den Editor.js-Werkzeugen (Formularfeld 'image' oder 'file')."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if int(request.headers.get("content-length") or 0) > FILE_MAX + 64 * 1024:
        return JSONResponse({"success": 0, "error": f"Datei zu gross (max. {FILE_MAX // 1048576} MB)"}, status_code=413)
    form = await request.form()
    up = form.get("image") or form.get("file")
    if up is None or not hasattr(up, "read"):
        return JSONResponse({"success": 0, "error": "keine Datei"}, status_code=400)
    data = await up.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        return JSONResponse({"success": 0, "error": f"Datei zu gross (max. {FILE_MAX // 1048576} MB)"}, status_code=413)
    if not data:
        return JSONResponse({"success": 0, "error": "leere Datei"}, status_code=400)
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", os.path.basename(up.filename or "datei"))[:120] or "datei"
    img = _sniff_image(data[:16])
    if "image" in form and not img:
        return JSONResponse({"success": 0, "error": "kein unterstütztes Bild (PNG, JPEG, GIF, WebP)"}, status_code=415)
    with _db() as c, c:
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if used + len(data) > FILE_QUOTA:
            return JSONResponse({"success": 0, "error": f"Speicherplatz voll (max. {FILE_QUOTA // 1048576} MB)"}, status_code=507)
        fid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fid, uid, name, img or "application/octet-stream", len(data), 1 if img else 0, time.time()))
    ext = name.rsplit(".", 1)[-1].lower()[:10] if "." in name else ""
    return JSONResponse({"success": 1, "file": {"url": f"/files/{fid}", "name": name, "size": len(data),
                                               "extension": ext, "title": name}})


THUMB_W = (160, 320, 640, 1280, 2000)           # erlaubte Breiten fuer ?w=
THUMB_DIR = os.path.join(FILES_DIR, "thumbs")
_thumb_sem = threading.Semaphore(1)
_FILE_H = {"Cache-Control": "private, max-age=31536000, immutable",   # Datei-IDs sind zufaellig, Inhalt aendert sich nie
           "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer"}


def _thumb_w(request):
    try:
        w = int(request.query_params.get("w") or 0)
    except ValueError:
        return 0
    return min((x for x in THUMB_W if x >= w), default=THUMB_W[-1]) if w > 0 else 0


def _thumb(fid, path, mime, w):
    """Verkleinerte Fassung, einmal erzeugt und auf der Platte zwischengespeichert: Fotos/Grafiken als WebP,
    animierte GIFs wieder als (kleineres) animiertes GIF - das spielen auch die Apps ab.
    None = Original ausliefern (schon klein genug, kein Rasterbild, kein Gewinn, Fehler)."""
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        return None
    base = os.path.join(THUMB_DIR, f"{fid}-{w}")
    for ext in (".webp", ".gif"):
        if os.path.isfile(base + ext):
            return base + ext
    if os.path.isfile(base + ".orig"):               # schon geprueft: Original ist besser
        return None
    with _thumb_sem:                                 # immer nur EIN Bild auf einmal (Speicher: Container hat 1 GB)
        for ext in (".webp", ".gif"):                # hat ein anderer Aufruf es inzwischen erzeugt?
            if os.path.isfile(base + ext):
                return base + ext
        return _make_thumb(path, base, w)


# Wird in einem eigenen, speicherbegrenzten Prozess ausgefuehrt: ein riesiges Foto oder GIF kann so
# hoechstens diesen Prozess sprengen, nie den Server (der Container hat nur 1 GB).
_THUMB_CODE = r"""
import sys
from PIL import Image, ImageSequence
path, tmp, w = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.MAX_IMAGE_PIXELS = 50_000_000
with Image.open(path) as im:
    if im.width <= w:
        print("orig"); sys.exit()
    anim = getattr(im, "is_animated", False) and im.format in ("GIF", "WEBP", "PNG")
    size = (w, max(1, round(im.height * w / im.width)))
    if anim:
        if getattr(im, "n_frames", 1) * size[0] * size[1] > 20_000_000:
            print("orig"); sys.exit()
        frames, durs = [], []
        for fr in ImageSequence.Iterator(im):
            frames.append(fr.convert("RGBA").resize(size, Image.LANCZOS)); durs.append(fr.info.get("duration", im.info.get("duration", 100)) or 100)
        frames[0].save(tmp, "GIF", save_all=True, append_images=frames[1:], duration=durs, loop=im.info.get("loop", 0), disposal=2)
        print(".gif")
    else:
        if im.format in ("JPEG", "MPO"):
            im.draft("RGB", size)                    # gleich verkleinert entpacken: schnell und sparsam
        alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im.convert("RGBA" if alpha else "RGB").resize(size, Image.LANCZOS).save(tmp, "WEBP", quality=82, method=4)
        print(".webp")
"""


def _thumb_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (450 << 20, 450 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))


def _make_thumb(path, base, w):
    os.makedirs(THUMB_DIR, mode=0o700, exist_ok=True)
    tmp = f"{base}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        r = subprocess.run([sys.executable, "-c", _THUMB_CODE, path, tmp, str(w)], capture_output=True, text=True,
                           timeout=90, preexec_fn=_thumb_limits)
        ext = r.stdout.strip()
        if r.returncode != 0 or ext not in (".webp", ".gif") or os.path.getsize(tmp) >= os.path.getsize(path):
            open(base + ".orig", "w").close()            # zu gross/kaputt/kein Gewinn -> Original (nicht nochmal versuchen)
            return None
        os.replace(tmp, base + ext)
        return base + ext
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _thumb_mime(tp):
    return "image/gif" if tp.endswith(".gif") else "image/webp"


def _drop_thumbs(fid):
    for w in THUMB_W:
        for ext in (".webp", ".gif", ".orig"):
            try:
                os.remove(os.path.join(THUMB_DIR, f"{fid}-{w}{ext}"))
            except OSError:
                pass


def _image_response(fid, path, mime, request):
    """Bild ausliefern - mit ?w=<Breite> verkleinert (Galerie, Symbole, Widgets, Bilder im Text)."""
    w = _thumb_w(request)
    tp = _thumb(fid, path, mime, w) if w else None
    h = dict(_FILE_H, **{"Content-Disposition": "inline"})
    return FileResponse(tp, media_type=_thumb_mime(tp), headers=h) if tp else FileResponse(path, media_type=mime, headers=h)


@app.get("/files/{fid}")
def file_get(fid: str, request: Request):
    """Nur fuer den Besitzer (Session-Cookie). Bilder inline (auf Wunsch verkleinert), alles andere nur als Download."""
    uid = _session_user(request)
    if not uid or not re.fullmatch(r"[0-9a-f]{24}", fid):
        return PlainTextResponse("not found", status_code=404)
    with _db() as c:
        r = c.execute("SELECT name, mime, is_image FROM file WHERE id=? AND uid=?", (fid, uid)).fetchone()
    path = os.path.join(FILES_DIR, fid)
    if not r or not os.path.isfile(path):
        return PlainTextResponse("not found", status_code=404)
    if r[2]:
        return _image_response(fid, path, r[1], request)
    return FileResponse(path, media_type="application/octet-stream", headers=dict(_FILE_H, **{"Content-Disposition": _disposition(r[0])}))


# --- Vorlagen ---------------------------------------------------------------------
def _map_props(c, owner, src_parent, dst_parent, props_json):
    """Eigenschaften einer Vorlage in eine ANDERE Datenbank uebertragen: nach Name + Typ der Spalte,
    Auswahl-Optionen nach Namen. Was es im Ziel nicht gibt, faellt weg (statt falscher IDs)."""
    def schema(nid):
        if not nid:
            return []
        row = c.execute("SELECT schema, kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
        return (_json_load(row[0], {}) or {}).get("props", []) if row and row[1] == "database" else []
    src, dst = schema(src_parent), schema(dst_parent)
    vals = _json_load(props_json, {}) or {}
    if not dst or not isinstance(vals, dict):
        return None
    out = {}
    by_id = {p.get("id"): p for p in src}
    for pid, v in vals.items():
        sp = by_id.get(pid)
        if not sp:
            continue
        dp = next((p for p in dst if p.get("name", "").strip().lower() == sp.get("name", "").strip().lower() and p.get("type") == sp.get("type")), None)
        if not dp or dp.get("type") in ("relation", "created", "edited"):
            continue
        if dp.get("type") in ("select", "multi_select"):
            names = {o.get("id"): o.get("name", "").strip().lower() for o in sp.get("options", [])}
            dst_ids = {o.get("name", "").strip().lower(): o.get("id") for o in dp.get("options", [])}
            ids = v if isinstance(v, list) else [v]
            mapped = [dst_ids[names[i]] for i in ids if i in names and names[i] in dst_ids]
            if not mapped:
                continue
            v = mapped if dp["type"] == "multi_select" else mapped[0]
        out[dp["id"]] = v
    return json.dumps(out, ensure_ascii=False) if out else None


@app.post("/api/notes/{nid}/duplicate")
async def api_note_duplicate(nid: int, request: Request):
    """Kopie einer eigenen Seite (Vorlage -> neue Seite). Unterseiten werden nicht kopiert."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request) or {}
    now = time.time()
    with _db() as c, c:
        r = c.execute("SELECT title, icon, tags, body, format, kind, schema, props, cover, cover_pos, wide, parent_id "
                      "FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        if c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (owner,)).fetchone()[0] >= NOTE_MAX_COUNT:
            return _bad(f"maximal {NOTE_MAX_COUNT} Seiten (inkl. Papierkorb)", 409)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        title = str(b.get("title", r[0] or "")).strip()[:NOTE_TITLE_MAX]
        props = r[7]
        if parent != r[11]:                     # andere Datenbank (z. B. Vorlagen-Datenbank) -> Eigenschaften nach Name/Typ zuordnen
            props = _map_props(c, owner, r[11], parent, r[7])
        # Alles kopieren, was die Seite ausmacht: auch Titelbild (+ Position) und volle Breite
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, cover, cover_pos, wide, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, r[3], r[4], r[1], parent, now, r[2], r[5] or "page", r[6], props, r[8], r[9], r[10], now, now))
        return JSONResponse({"id": cur.lastrowid, "updated": now})


# --- Datenbanken (Notion-Stil) ------------------------------------------------------
# Eine Datenbank ist eine Seite mit kind='database' und einem Schema (Eigenschaften).
# Ihre Zeilen sind normale Unterseiten mit props = {eigenschaft_id: wert}.
PROP_TYPES = {"text", "number", "select", "multi_select", "date", "checkbox", "url", "created", "edited", "relation", "calc"}
CALC_PROP_OPS = {"div", "pct", "mul", "add", "sub"}     # "Berechnet": A / B, A in % von B, A * B, A + B, A - B (nie gespeichert)


def _calc_value(p, pv):
    """Wert einer Rechen-Eigenschaft aus zwei Zahlen-Eigenschaften der Zeile (oder None)."""
    def num(k):
        v = pv.get(k)
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None
    a, b, op = num(p.get("a")), num(p.get("b")), p.get("op", "div")
    if op in ("add", "sub"):
        if a is None and b is None:
            return None
        a, b = a or 0.0, b or 0.0
    elif a is None or b is None:
        return None
    if op in ("div", "pct"):
        if b == 0:
            return None
        v = a / b * (100 if op == "pct" else 1)
    else:
        v = a * b if op == "mul" else a + b if op == "add" else a - b
    return round(v, 4)
AUTO_TYPES = {"created", "edited"}          # Zeitstempel der Zeile, nie gespeicherte Werte
NUM_FMT, NUM_SHOW = {"percent", "euro"}, {"bar", "ring"}
OPT_COLORS = {"gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}
_PID = re.compile(r"[a-z0-9_]{1,24}")


def _default_schema():
    return {"props": [
        {"id": "status", "name": "Status", "type": "select", "options": [
            {"id": "todo", "name": "Offen", "color": "gray"}, {"id": "doing", "name": "In Arbeit", "color": "blue"},
            {"id": "done", "name": "Erledigt", "color": "green"}]},
        {"id": "datum", "name": "Datum", "type": "date"}],
        "views": [{"id": "tabelle", "name": "Tabelle", "type": "table"},
                  {"id": "board", "name": "Board", "type": "board", "group_by": "status"}]}


VIEW_TYPES = {"table", "board", "list", "gallery", "calendar"}
CALC_OPS = {"count", "filled", "empty", "pct_filled", "unique", "sum", "avg", "median", "min", "max", "range",
            "checked", "unchecked", "pct_checked", "earliest", "latest"}       # Fusszeile der Tabelle (je Spalte)
FILTER_OPS = {"contains", "not_contains", "is", "is_not", "eq", "gt", "lt", "before", "after",
              "is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month"}


def _clean_views(raw, props):
    """Ansichten (max. 10): Typ, Gruppierung (nur Auswahl), Sortierung, Filter - nur auf existierende
    Eigenschaften. Verweise auf geloeschte Spalten fallen dadurch automatisch weg."""
    pids = {p["id"]: p for p in props}
    out, seen = [], set()
    for v in (raw if isinstance(raw, list) else [])[:10]:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("id", ""))
        if not _PID.fullmatch(vid) or vid in seen:
            raise ValueError("ungueltige Ansicht")
        seen.add(vid)
        view = {"id": vid, "name": str(v.get("name", "")).strip()[:40] or "Ansicht",
                "type": v.get("type") if v.get("type") in VIEW_TYPES else "table"}
        g = v.get("group_by")
        if g in pids and pids[g]["type"] == "select":
            view["group_by"] = g
        # Sortierung: bis zu 3 Stufen ("sorts"); altes Einzelfeld "sort" wird uebernommen
        raw_sorts = v.get("sorts") if isinstance(v.get("sorts"), list) else ([v["sort"]] if isinstance(v.get("sort"), dict) else [])
        sorts = [{"pid": s["pid"], "dir": "desc" if s.get("dir") == "desc" else "asc"} for s in raw_sorts[:3]
                 if isinstance(s, dict) and (s.get("pid") == "title" or s.get("pid") in pids)]
        if sorts:
            view["sorts"] = sorts
        db_ = v.get("date_by")                               # Kalender: nach welchem Datum
        if db_ in pids and pids[db_]["type"] in ("date", "created", "edited"):
            view["date_by"] = db_
        gr = v.get("group")                                  # Gruppierung fuer Tabelle/Liste
        if gr in pids and pids[gr]["type"] == "select":
            view["group"] = gr
        if isinstance(v.get("hidden"), list):                # ausgeblendete Eigenschaften
            hid = [h for h in v["hidden"] if h in pids]
            if hid:
                view["hidden"] = hid
        if isinstance(v.get("widths"), dict):                # Spaltenbreiten (px)
            w = {k: max(60, min(int(x), 800)) for k, x in v["widths"].items()
                 if (k == "title" or k in pids) and isinstance(x, (int, float))}
            if w:
                view["widths"] = w
        if isinstance(v.get("order"), list):                 # Reihenfolge der Eigenschaften in dieser Ansicht
            order = list(dict.fromkeys(x for x in v["order"] if x in pids))
            if order:
                view["order"] = order
        if v.get("wrap") is True:
            view["wrap"] = True
        if v.get("open") in ("center", "page", "left"):    # Eintraege oeffnen als (Standard: Seitenansicht rechts)
            view["open"] = v["open"]
        if v.get("ext") is True:                            # Kalender-Ansicht: externe Termine einblenden
            view["ext"] = True
        if v.get("card") in ("s", "l"):
            view["card"] = v["card"]
        fl = [{"pid": f["pid"], "op": f["op"], "value": "" if f.get("value") is None else str(f["value"])[:200]}
              for f in (v.get("filters") or [])[:100]
              if isinstance(f, dict) and (f.get("pid") == "title" or f.get("pid") in pids) and f.get("op") in FILTER_OPS][:10]   # erst pruefen, dann begrenzen
        if fl:
            view["filters"] = fl
        cr = [{"pid": f["pid"], "op": f["op"], "value": "" if f.get("value") is None else str(f["value"])[:200], "color": f["color"]}
              for f in (v.get("colors") or [])[:100]          # bedingte Farbe: wie Filter + erlaubte Farbe
              if isinstance(f, dict) and (f.get("pid") == "title" or f.get("pid") in pids) and f.get("op") in FILTER_OPS
              and f.get("color") in OPT_COLORS][:10]
        if cr:
            view["colors"] = cr
        if isinstance(v.get("calcs"), dict):                 # Berechnung je Spalte (Fusszeile)
            cc = {k: op for k, op in list(v["calcs"].items())[:40] if (k == "title" or k in pids) and op in CALC_OPS}
            if cc:
                view["calcs"] = cc
        out.append(view)
    return out or [{"id": "tabelle", "name": "Tabelle", "type": "table"}]


def _clean_schema(raw):
    """Schema pruefen: max. 30 Eigenschaften, eindeutige IDs, bekannte Typen, Optionen fuer Auswahlen."""
    if not isinstance(raw, dict) or not isinstance(raw.get("props"), list):
        raise ValueError("ungueltiges Schema")
    out, seen = [], set()
    for p in raw["props"][:30]:
        pid, typ = str(p.get("id", "")), str(p.get("type", ""))
        if not _PID.fullmatch(pid) or pid in seen or typ not in PROP_TYPES:
            raise ValueError("ungueltige Eigenschaft")
        seen.add(pid)
        prop = {"id": pid, "name": str(p.get("name", "")).strip()[:60] or "Eigenschaft", "type": typ}
        if typ in ("select", "multi_select"):
            opts, oseen = [], set()
            for o in (p.get("options") or [])[:50]:
                oid = str(o.get("id", ""))
                if not _PID.fullmatch(oid) or oid in oseen:
                    raise ValueError("ungueltige Option")
                oseen.add(oid)
                col = o.get("color") if o.get("color") in OPT_COLORS else "gray"
                opts.append({"id": oid, "name": str(o.get("name", "")).strip()[:40] or "Option", "color": col})
            prop["options"] = opts
        if typ == "relation":                       # Ziel-Datenbank (Besitz prueft der Aufrufer)
            tg = p.get("target")
            if not (isinstance(tg, int) or (isinstance(tg, str) and tg.isdigit())):
                raise ValueError("Relation braucht eine Ziel-Datenbank")
            prop["target"] = int(tg)
            if isinstance(p.get("pair"), str) and _PID.fullmatch(p["pair"]):
                prop["pair"] = p["pair"]                # Gegenspalte (zweiseitig) - stellt nur der Server her
        if typ == "calc":                           # Rechnung: Zahlen-Spalten pruefen wir nach der Schleife
            prop["op"] = p.get("op") if p.get("op") in CALC_PROP_OPS else "div"
            for k in ("a", "b"):
                if isinstance(p.get(k), str) and _PID.fullmatch(p[k]):
                    prop[k] = p[k]
        if typ in ("number", "calc"):
            if p.get("fmt") in NUM_FMT:
                prop["fmt"] = p["fmt"]
            if p.get("show") in NUM_SHOW:
                prop["show"] = p["show"]
        if isinstance(p.get("width"), (int, float)):
            prop["width"] = max(60, min(int(p["width"]), 600))
        out.append(prop)
    nums = {p["id"] for p in out if p["type"] == "number"}
    for p in out:                                    # Rechnung nur aus vorhandenen Zahlen-Spalten
        if p["type"] == "calc":
            for k in ("a", "b"):
                if p.get(k) not in nums:
                    p.pop(k, None)
    res = {"props": out, "views": _clean_views(raw.get("views"), out)}
    tn = re.sub(r"\s+", " ", str(raw.get("title_name") or "")).strip()[:60]
    if tn and tn != "Name":                          # eigener Name der ersten Spalte
        res["title_name"] = tn
    if raw.get("locked") is True:                    # gesperrt: Spalten/Ansichten unveraenderlich, Eintraege frei
        res["locked"] = True
    return res


def _rel_check(c, uid):
    """Pruefer fuer Relationen: nur eigene, aktive Eintraege, die wirklich in der Ziel-Datenbank liegen."""
    def check(target, ids):
        if not target or not ids:
            return set()
        return {r[0] for r in c.execute(
            f"SELECT id FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
            (uid, target, *ids))}
    return check


def _db_schema(c, uid, dbid):
    r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (dbid, uid)).fetchone()
    return _json_load(r[0], {"props": [], "views": []}) if r else None


def _rel_mirror_set(c, uid, target_db, row_id, pid, other_id, add):
    """In Zeile row_id (muss in target_db liegen) die Relation pid um other_id ergaenzen bzw. davon loesen."""
    r = c.execute("SELECT props FROM note WHERE id=? AND uid=? AND parent_id=? AND deleted_at IS NULL", (row_id, uid, target_db)).fetchone()
    if not r:
        return
    props = _json_load(r[0], {})
    vals = [x for x in (props.get(pid) or []) if isinstance(x, int) and not isinstance(x, bool)]
    if add and other_id not in vals and len(vals) < 50:
        vals.append(other_id)
    elif not add and other_id in vals:
        vals.remove(other_id)
    else:
        return
    if vals:
        props[pid] = vals
    else:
        props.pop(pid, None)
    c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(props, ensure_ascii=False), row_id, uid))


def _rel_sync(c, uid, schema, row_id, old_props, new_props):
    """Zweiseitige Relationen: hinzugefuegte/entfernte Verknuepfungen auf der Gegenseite nachziehen."""
    for p in schema.get("props", []):
        if p.get("type") != "relation" or not p.get("pair"):
            continue
        old, new = set(old_props.get(p["id"]) or []), set(new_props.get(p["id"]) or [])
        for t in new - old:
            _rel_mirror_set(c, uid, p["target"], t, p["pair"], row_id, True)
        for t in old - new:
            _rel_mirror_set(c, uid, p["target"], t, p["pair"], row_id, False)


def _rel_link(c, uid, nid, title, rp):
    """Gegenspalte in der Ziel-Datenbank anlegen und bestehende Verknuepfungen nachtragen; liefert deren ID."""
    ts = _db_schema(c, uid, rp["target"])
    if ts is None:
        raise ValueError("Ziel-Datenbank der Relation nicht gefunden")
    if len(ts.get("props", [])) >= 30:
        raise ValueError("Die Ziel-Datenbank hat schon 30 Eigenschaften")
    used = {p["id"] for p in ts["props"]}
    mid = next(x for x in ("r" + secrets.token_hex(4) for _ in range(50)) if x not in used)
    ts["props"].append({"id": mid, "name": (title or "Verknüpft")[:60], "type": "relation", "target": nid, "pair": rp["id"]})
    c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(ts, ensure_ascii=False), rp["target"], uid))
    for row_id, pr in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL", (uid, nid)).fetchall():
        for t in _json_load(pr, {}).get(rp["id"]) or []:
            _rel_mirror_set(c, uid, rp["target"], t, mid, row_id, True)
    return mid


def _rel_unlink(c, uid, nid, target, mpid, delete):
    """Gegenspalte mpid in target loesen: delete=True entfernt sie samt Werten, sonst nur die Kopplung."""
    ts = _db_schema(c, uid, target)
    if ts is None:
        return
    mp = next((p for p in ts.get("props", []) if p.get("id") == mpid and p.get("type") == "relation" and p.get("target") == nid), None)
    if not mp:
        return
    if delete:
        ts["props"] = [p for p in ts["props"] if p is not mp]
        for row_id, pr in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=?", (uid, target)).fetchall():
            d = _json_load(pr, {})
            if mpid in d:
                d.pop(mpid)
                c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(d, ensure_ascii=False), row_id, uid))
    else:
        mp.pop("pair", None)
    c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(_clean_schema(ts), ensure_ascii=False), target, uid))


def _clean_props(schema, raw, base=None, check=None):
    """Zeilenwerte gegen das Schema pruefen; unbekannte Eigenschaften werden verworfen."""
    if not isinstance(raw, dict):
        raise ValueError("ungueltige Werte")
    out = dict(base or {})
    by_id = {p["id"]: p for p in schema.get("props", [])}
    for pid, v in raw.items():
        p = by_id.get(pid)
        if not p:
            continue
        t = p["type"]
        if t in AUTO_TYPES or t == "calc":   # automatisch/berechnet - nie gespeichert, nicht faelschbar
            out.pop(pid, None)
            continue
        if v in (None, "", []):
            out.pop(pid, None)
            continue
        if t == "text":
            v = str(v)[:2000]
        elif t == "number":
            v = float(v)
            if v != v or abs(v) > 1e15:
                raise ValueError("ungueltige Zahl")
            v = int(v) if v == int(v) else v
        elif t == "select":
            if v not in {o["id"] for o in p.get("options", [])}:
                raise ValueError("unbekannte Option")
        elif t == "multi_select":
            ok_ids = {o["id"] for o in p.get("options", [])}
            if not isinstance(v, list) or any(x not in ok_ids for x in v):
                raise ValueError("unbekannte Option")
            v = list(dict.fromkeys(v))[:20]
        elif t == "date":
            v = date.fromisoformat(str(v)).isoformat()
        elif t == "checkbox":
            v = bool(v)
            if not v:
                out.pop(pid, None)
                continue
        elif t == "relation":
            if not isinstance(v, list):
                raise ValueError("Relation muss eine Liste sein")
            ids = list(dict.fromkeys(int(x) for x in v if not isinstance(x, bool)))[:50]
            if any(not 0 < x < 2 ** 63 for x in ids):   # passt nicht in SQLite -> sonst OverflowError (500)
                raise ValueError("ungueltige Eintrags-ID")
            if check is not None:                   # fremde/erfundene IDs still verwerfen
                ok = check(p.get("target"), ids)
                ids = [x for x in ids if x in ok]
            if not ids:
                out.pop(pid, None)
                continue
            v = ids
        elif t == "url":
            v = str(v).strip()[:500]
            if urlparse(v).scheme not in ("http", "https", "mailto"):
                raise ValueError("URL muss mit http(s):// oder mailto: beginnen")
        out[pid] = v
    return out


def _json_load(s, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except ValueError:
        return default


def _first_image(body, fmt):
    """Erstes Bild im Seiteninhalt (Galerie-Vorschau, wenn die Zeile kein Titelbild hat)."""
    if fmt != "blocks":
        return ""
    doc = _json_load(body, {})
    try:
        blocks = list(_iter_blocks(doc.get("blocks", []) if isinstance(doc, dict) else []))
    except ValueError:
        return ""
    for b in blocks:
        if b.get("type") == "image":
            u = str(((b.get("data") or {}).get("file") or {}).get("url", ""))
            if _FILE_URL.fullmatch(u):
                return u
    return ""


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


# --- Immich (eigene Fotos als Bilder, Titelbilder, Icons) ----------------------------
# Jede Person traegt IHRE Immich-Adresse + IHREN API-Schluessel ein (verschluesselt wie Kalender-Passwoerter).
# Schutz gegen SSRF (Server ruft benutzerdefinierte Adressen auf):
#  - oeffentliche Adressen: nur https; interne Adressen (privat/loopback/link-local ...) nur, wenn der Admin
#    den Host freigibt (IMMICH_URL = Vorschlag und automatisch frei, weitere in IMMICH_ALLOWED_HOSTS)
#  - geprueft wird die IP der TATSAECHLICHEN Verbindung (nicht nur der Name) -> auch DNS-Tricks werden abgefangen
#  - keine Weiterleitungen, Groessen- und Zeitlimits
# Ausgewaehlte Bilder werden nach Saga KOPIERT (Titelbilder muessen eigene Dateien sein; Seiten bleiben heil).
IMMICH_URL = (E.get("IMMICH_URL") or "").rstrip("/")        # Vorschlag fuer das Adressfeld


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
_IMMICH_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_immich_local = __import__("threading").local()


def _immich_allowed_hosts():
    hosts = set(_admin_hosts())
    if IMMICH_URL:
        hosts.add(urlparse(IMMICH_URL).netloc.lower())
    return hosts


def _immich_norm(url):
    """Adresse vereinheitlichen -> (basis, fehler). Erlaubt: https://host[:port][/pfad], http nur fuer freigegebene Hosts."""
    u = str(url or "").strip().rstrip("/")
    if u.endswith("/api"):
        u = u[:-4]
    if len(u) > 300:
        return None, "Adresse zu lang"
    try:
        pu = urlparse(u)
    except ValueError:
        return None, "Adresse ungueltig"
    if pu.scheme not in ("http", "https") or not pu.hostname or pu.username or pu.password or pu.query or pu.fragment:
        return None, "Adresse ungueltig (z. B. https://immich.example.de)"
    freigegeben = pu.netloc.lower() in _immich_allowed_hosts()
    if pu.scheme == "http" and not freigegeben:
        return None, "Nur https-Adressen erlaubt (http nur fuer vom Admin freigegebene interne Server)"
    return f"{pu.scheme}://{pu.netloc}{pu.path}", None


def _immich_ip_ok(ip, netloc):
    """Interne Ziele nur fuer vom Admin freigegebene Hosts."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global or auth.settings().get("allow_private_hosts") or netloc.lower() in _immich_allowed_hosts()


def _immich_db(c):
    c.execute("CREATE TABLE IF NOT EXISTS immich_key (uid TEXT PRIMARY KEY, secret TEXT NOT NULL, name TEXT, created REAL)")
    if "base_url" not in [r[1] for r in c.execute("PRAGMA table_info(immich_key)")]:
        c.execute("ALTER TABLE immich_key ADD COLUMN base_url TEXT")


def _immich_key(uid):
    """-> (adresse, schluessel) oder None"""
    with _db() as c:
        _immich_db(c)
        r = c.execute("SELECT secret, base_url FROM immich_key WHERE uid=?", (uid,)).fetchone()
    if not r:
        return None
    try:
        return (r[1] or IMMICH_URL, _cal_fernet().decrypt(r[0].encode()).decode())
    except Exception:
        return None


def _immich(conn, method, path, body=None, params=None, max_bytes=2 * 1024 * 1024):
    """Ein Aufruf an Immich -> (status, content_type, bytes). conn = (adresse, schluessel)."""
    import httpx
    base, key = conn
    netloc = urlparse(base).netloc
    if not base or not _immich_norm(base)[0]:
        return 400, "", b""
    # 1) VOR dem Verbinden: alle Adressen des Namens pruefen - interne Ziele ohne Freigabe gar nicht erst anfassen
    import socket
    pu = urlparse(base)
    try:
        infos = socket.getaddrinfo(pu.hostname, pu.port or (443 if pu.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return 502, "", b""
    if not infos or not all(_immich_ip_ok(i[4][0], netloc) for i in infos):
        return 403, "", b""
    cl = getattr(_immich_local, "client", None)
    if cl is None:
        cl = _immich_local.client = httpx.Client(timeout=15, follow_redirects=False)
    try:
        # 2) NACH dem Verbinden: IP der tatsaechlichen Verbindung (gegen Umstellen des Namens dazwischen)
        with cl.stream(method, base + "/api" + path, params=params, json=body,
                       headers={"x-api-key": key, "Accept": "application/json, image/*"}) as r:
            ns = r.extensions.get("network_stream")                 # IP der tatsaechlichen Verbindung pruefen
            peer = ns.get_extra_info("server_addr") if ns else None
            if not peer or not _immich_ip_ok(peer[0], netloc):
                _immich_local.client = None                          # Verbindung nicht weiterverwenden
                return 403, "", b""
            if r.status_code in (301, 302, 303, 307, 308):
                return 502, "", b""
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return 413, "", b""
            return r.status_code, r.headers.get("content-type", ""), bytes(buf)
    except httpx.HTTPError:
        _immich_local.client = None
        return 502, "", b""


def _immich_guard(request):
    uid = _api_guard(request)
    if not uid:
        return None, None, _bad("unauthorized", 401)
    conn = _immich_key(uid)
    if not conn:
        return uid, None, _bad("Immich nicht verbunden (Einstellungen → Immich)", 409)
    return uid, conn, None


@app.get("/api/settings/immich")
def api_immich_settings(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _immich_db(c)
        r = c.execute("SELECT name, base_url FROM immich_key WHERE uid=?", (uid,)).fetchone()
    return JSONResponse({"available": True, "connected": bool(r), "name": r[0] if r else None,
                         "url": (r[1] or IMMICH_URL) if r else None, "suggest": IMMICH_URL or None})


@app.post("/api/settings/immich")
async def api_immich_connect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    base, err = _immich_norm(b.get("url") or IMMICH_URL)
    if err:
        return _bad(err)
    key = str(b.get("apikey") or "").strip()
    if not key or len(key) > 200 or not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        return _bad("API-Schluessel fehlt oder ist ungueltig")
    st, _, data = await asyncio.to_thread(_immich, (base, key), "GET", "/users/me")
    if st == 403 and not data:
        return _bad("Diese Adresse zeigt auf einen internen Server, der nicht freigegeben ist", 400)
    if st in (401, 403):
        return _bad("Immich lehnt den Schluessel ab - Rechte: album.read, asset.read, asset.view, asset.download, user.read", 400)
    if st != 200:
        return _bad("Immich unter dieser Adresse nicht erreichbar", 502)
    try:
        me = json.loads(data)
        name = str(me.get("name") or me.get("email") or "")[:120]
    except ValueError:
        return _bad("Unter dieser Adresse antwortet kein Immich", 502)
    with _db() as c, c:
        _immich_db(c)
        c.execute("INSERT OR REPLACE INTO immich_key (uid, secret, name, created, base_url) VALUES (?,?,?,?,?)",
                  (uid, _cal_fernet().encrypt(key.encode()).decode(), name, time.time(), base))
    return JSONResponse({"ok": True, "name": name, "url": base})


@app.delete("/api/settings/immich")
def api_immich_disconnect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _immich_db(c)
        c.execute("DELETE FROM immich_key WHERE uid=?", (uid,))
    return JSONResponse({"ok": True})


@app.get("/api/immich/albums")
def api_immich_albums(request: Request):
    uid, key, err = _immich_guard(request)
    if err:
        return err
    st, _, data = _immich(key, "GET", "/albums")
    if st != 200:
        return _bad("Immich: Alben nicht lesbar", 502)
    out = [{"id": a.get("id"), "name": str(a.get("albumName") or "")[:200], "count": a.get("assetCount") or 0,
            "thumb": a.get("albumThumbnailAssetId")} for a in json.loads(data) if isinstance(a, dict)]
    out.sort(key=lambda a: a["name"].lower())
    return JSONResponse({"albums": out})


@app.post("/api/immich/assets")
async def api_immich_assets(request: Request):
    """Fotos: Zeitleiste (neueste zuerst), ein Album, oder Suche nach Bildinhalt (q)."""
    uid, key, err = _immich_guard(request)
    if err:
        return err
    b = await _body(request) or {}
    page = max(1, min(500, int(b.get("page") or 1)))
    q = str(b.get("q") or "").strip()[:200]
    album = str(b.get("album") or "")
    body = {"page": page, "size": 60, "type": "IMAGE"}
    if album:
        if not _IMMICH_ID.match(album):
            return _bad("ungueltiges Album")
        body["albumIds"] = [album]
    if q:
        body["query"] = q
        st, _, data = await asyncio.to_thread(_immich, key, "POST", "/search/smart", body, None, 8 * 1024 * 1024)
    else:
        body["order"] = "desc"
        st, _, data = await asyncio.to_thread(_immich, key, "POST", "/search/metadata", body, None, 8 * 1024 * 1024)
    if st != 200:
        return _bad("Immich: Suche fehlgeschlagen", 502)
    a = (json.loads(data).get("assets") or {})
    items = [{"id": x.get("id"), "name": str(x.get("originalFileName") or "")[:200], "mime": x.get("originalMimeType"),
              "date": x.get("fileCreatedAt")} for x in a.get("items") or [] if isinstance(x, dict) and _IMMICH_ID.match(str(x.get("id") or ""))]
    return JSONResponse({"items": items, "next": page + 1 if a.get("nextPage") else None})


@app.get("/api/immich/thumb/{aid}")
def api_immich_thumb(aid: str, request: Request):
    uid, key, err = _immich_guard(request)
    if err:
        return err
    if not _IMMICH_ID.match(aid):
        return _bad("ungueltig")
    st, ct, data = _immich(key, "GET", f"/assets/{aid}/thumbnail", params={"size": "thumbnail"})
    img = _sniff_image(data[:16]) if st == 200 else None
    if not img:
        return _bad("Vorschau nicht verfuegbar", 404)
    return Response(content=data, media_type=img, headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/immich/import")
async def api_immich_import(request: Request):
    """Foto aus Immich als eigene Datei uebernehmen -> /files/<id>. GIFs im Original (Animation), sonst Vorschaugroesse."""
    uid, key, err = _immich_guard(request)
    if err:
        return err
    b = await _body(request) or {}
    aid = str(b.get("id") or "")
    if not _IMMICH_ID.match(aid):
        return _bad("ungueltig")
    st, _, info = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}")
    if st != 200:
        return _bad("Foto nicht gefunden", 404)
    meta = json.loads(info)
    mime = str(meta.get("originalMimeType") or "")
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", str(meta.get("originalFileName") or "immich"))[:120] or "immich"
    if mime == "image/gif" or b.get("original"):
        st, _, data = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}/original", None, None, FILE_MAX)
    else:
        st, _, data = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}/thumbnail", None, {"size": "preview"}, FILE_MAX)
        name = name.rsplit(".", 1)[0] + ".jpg"
    if st == 413:
        return _bad(f"Bild zu gross (max. {FILE_MAX // 1048576} MB)", 413)
    img = _sniff_image(data[:16]) if st == 200 else None
    if not img:
        return _bad("kein unterstuetztes Bild (PNG, JPEG, GIF, WebP)", 415)
    if img != "image/gif" and name.endswith(".jpg") and img == "image/webp":
        name = name[:-4] + ".webp"
    with _db() as c, c:
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if used + len(data) > FILE_QUOTA:
            return _bad(f"Speicherplatz voll (max. {FILE_QUOTA // 1048576} MB)", 507)
        fid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fid, uid, name, img, len(data), 1, time.time()))
    return JSONResponse({"url": f"/files/{fid}", "name": name, "size": len(data), "mime": img})


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
        raise hkcal.CalError("Gespeichertes App-Passwort nicht lesbar – bitte neu eingeben")
    return _cal_public(r), pw


@app.get("/api/settings/calendars")
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


@app.post("/api/settings/calendars/test")
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
        return _bad("Adresse, Benutzer und App-Passwort angeben")
    try:
        cals = await run_in_threadpool(hkcal.discover, base, user, pw)
    except hkcal.CalError as ex:
        return _bad(str(ex))
    return JSONResponse({"calendars": cals})


@app.post("/api/settings/calendars")
async def api_cal_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    base, user, pw = _cal_input(b)
    if not (base and user and pw):
        return _bad("Adresse, Benutzer und App-Passwort angeben")
    try:
        found = await run_in_threadpool(hkcal.discover, base, user, pw)      # prueft Zugang + liefert gueltige Kalender
    except hkcal.CalError as ex:
        return _bad(str(ex))
    sel = _cal_selection(found, b.get("calendars"))
    now = time.time()
    with _db() as c, c:
        _cal_db(c)
        if c.execute("SELECT COUNT(*) FROM cal_account WHERE uid=?", (uid,)).fetchone()[0] >= CAL_MAX_ACCOUNTS:
            return _bad(f"Höchstens {CAL_MAX_ACCOUNTS} Kalender-Zugänge")
        cur = c.execute("INSERT INTO cal_account (uid, label, base_url, username, secret, calendars, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, str(b.get("label") or "").strip()[:60], base, user, _cal_fernet().encrypt(pw.encode()).decode(),
                         json.dumps(sel, ensure_ascii=False), now, now))
    _cal_cache.clear()
    return JSONResponse({"id": cur.lastrowid, "calendars": sel})


@app.patch("/api/settings/calendars/{aid}")
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
        return _bad("Bitte das App-Passwort neu eingeben")
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


@app.delete("/api/settings/calendars/{aid}")
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
            errors.append({"account": aid, "error": str(ex) or "nicht lesbar"}); continue
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


@app.get("/api/calendar/events")
def api_cal_events(request: Request, start: str = "", end: str = "", fresh: int = 0):
    """Termine aller angezeigten Kalender im Zeitraum [start, end) (Datum JJJJ-MM-TT, max. 62 Tage)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return _bad("start/end als JJJJ-MM-TT angeben")
    if not 0 < (d1 - d0).days <= CAL_MAX_DAYS:
        return _bad(f"Zeitraum 1 bis {CAL_MAX_DAYS} Tage")
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
        raise hkcal.CalError("Dieser Kalender ist nur lesbar")
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
        return _bad("ungueltige Angaben")
    _cal_cache.clear()
    return JSONResponse({"ok": True, "href": href, "etag": etag})


@app.post("/api/calendar/events")
async def api_cal_event_add(request: Request):
    return await _cal_write(request, "add")


@app.patch("/api/calendar/events")
async def api_cal_event_edit(request: Request):
    return await _cal_write(request, "edit")


@app.post("/api/calendar/events/delete")
async def api_cal_event_delete(request: Request):
    return await _cal_write(request, "delete")


# =====================================================================================
# App-Zugangsschluessel (iOS-Teilen-Erweiterung, Widget): eng begrenzte Rechte, nur als Hash gespeichert
# =====================================================================================
APP_TOKEN_MAX, APP_CAPTURE_MAX = 10, 20000


def _tok_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS app_token (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
        prefix TEXT NOT NULL, created REAL NOT NULL, last_used REAL)""")
    c.execute("CREATE TABLE IF NOT EXISTS user_kv (uid TEXT NOT NULL, k TEXT NOT NULL, v TEXT, PRIMARY KEY (uid, k))")


def _tok_hash(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def _app_uid(request):
    """NUR Bearer-Schluessel (keine Sitzung): liefert den Benutzer oder None."""
    hdr = request.headers.get("authorization", "")
    if not hdr.startswith("Bearer hk_") or len(hdr) > 120:
        return None
    with _db() as c, c:
        _tok_db(c)
        r = c.execute("SELECT id, uid, last_used FROM app_token WHERE hash=?", (_tok_hash(hdr[7:].strip()),)).fetchone()
        if not r:
            return None
        if not r[2] or time.time() - r[2] > 300:          # "zuletzt benutzt" grob mitfuehren
            c.execute("UPDATE app_token SET last_used=? WHERE id=?", (time.time(), r[0]))
    return r[1] if user_info(r[1]) is not None else None


@app.get("/api/settings/tokens")
def api_tok_list(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _tok_db(c)
        rows = c.execute("SELECT id, name, prefix, created, last_used FROM app_token WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return JSONResponse([{"id": r[0], "name": r[1], "prefix": r[2], "created": r[3], "last_used": r[4]} for r in rows])


@app.post("/api/settings/tokens")
async def api_tok_add(request: Request):
    """Neuen Schluessel erzeugen - der Klartext wird NUR in dieser Antwort gezeigt."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:60] or "iPhone"
    tok = "hk_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        _tok_db(c)
        if c.execute("SELECT COUNT(*) FROM app_token WHERE uid=?", (uid,)).fetchone()[0] >= APP_TOKEN_MAX:
            return _bad(f"Höchstens {APP_TOKEN_MAX} Schlüssel")
        cur = c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?,?,?,?,?)",
                        (uid, name, _tok_hash(tok), tok[:9], time.time()))
    return JSONResponse({"id": cur.lastrowid, "name": name, "token": tok, "prefix": tok[:9]})


@app.delete("/api/settings/tokens/{tid}")
def api_tok_delete(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _tok_db(c)
        n = c.execute("DELETE FROM app_token WHERE id=? AND uid=?", (tid, uid)).rowcount
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


def _inbox(c, uid):
    """Seite '📥 Eingang' (oberste Ebene) - wird bei Bedarf angelegt bzw. neu angelegt, wenn geloescht."""
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='inbox'", (uid,)).fetchone()
    if r and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (int(r[0]), uid)).fetchone():
        return int(r[0])
    now = time.time()
    nid = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                    (uid, "Eingang", json.dumps({"blocks": []}), "blocks", "📥", None, now, now, now)).lastrowid
    c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'inbox', ?)", (uid, str(nid)))
    return nid


@app.post("/api/app/capture")
async def api_app_capture(request: Request):
    """Aus der Teilen-Erweiterung: als Seite im Eingang oder als Aufgabe ablegen."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    kind = b.get("kind") if b.get("kind") in ("page", "task") else "page"
    title = re.sub(r"\s+", " ", str(b.get("title") or "")).strip()[:200]
    text = str(b.get("text") or "").strip()[:APP_CAPTURE_MAX]
    url = str(b.get("url") or "").strip()[:2000]
    if url and not re.fullmatch(r"https?://[^\s<>\"]+", url):
        return _bad("ungültiger Link")
    if not (title or text or url):
        return _bad("nichts zum Speichern")
    if kind == "task":
        txt = (title or text.split("\n")[0] or url)[:TASK_MAX_LEN - (len(url) + 1 if url and url not in (title or text) else 0)]
        if url and url not in txt:
            txt = (txt + " " + url).strip()
        try:
            due = _due(b.get("due"))                  # Siri/Kurzbefehle: optional faellig am
        except (ValueError, TypeError):
            return _bad("ungültiges Datum")
        with _db() as c, c:
            cur = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                            (uid, txt[:TASK_MAX_LEN], due, 2, None, None, time.time()))
        return JSONResponse({"ok": True, "kind": "task", "id": cur.lastrowid})
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    blocks = []
    if url:
        blocks.append({"type": "paragraph", "data": {"text": f'🔗 <a href="{esc(url)}">{esc(url)}</a>'}})
    for para in [p for p in re.split(r"\n\s*\n", text) if p.strip()][:200]:
        blocks.append({"type": "paragraph", "data": {"text": esc(para.strip()).replace("\n", "<br>")}})
    body, _ = clean_blocks(json.dumps({"blocks": blocks}))         # derselbe Bereiniger wie im Editor
    now = time.time()
    with _db() as c, c:
        _tok_db(c)
        parent = _inbox(c, uid)
        nid = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                        (uid, title or (url or text)[:80] or "Geteilt", body, "blocks", "🔗" if url else "📝", parent, now, now, now)).lastrowid
    return JSONResponse({"ok": True, "kind": "page", "id": nid, "url": f"/notes#{nid}"})


@app.get("/api/app/today")
def api_app_today(request: Request):
    """Fuer das Widget: Termine heute/morgen + offene Aufgaben (faellig zuerst)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    today = datetime.now(TZ).date()
    try:
        events, errors = _cal_collect(uid, today, today + timedelta(days=2))
    except Exception:
        events, errors = [], [{"error": "Kalender nicht lesbar"}]
    with _db() as c:
        rows = c.execute("SELECT id, text, due, prio FROM task WHERE uid=? AND done=0 "
                         "ORDER BY (due IS NULL), due, prio, created LIMIT 20", (uid,)).fetchall()
    tasks = [{"id": r[0], "text": r[1], "due": r[2], "prio": r[3], "overdue": bool(r[2] and r[2] < today.isoformat())} for r in rows]
    slim = [{k: e[k] for k in ("summary", "start", "end", "allday", "location", "color", "calendar")} for e in events]
    return JSONResponse({"date": today.isoformat(), "events": slim, "tasks": tasks, "errors": [x.get("error") for x in errors]},
                        headers={"Cache-Control": "no-store"})


# --- Widgets: eng begrenzt (nur eigene Daten; lesen = Titel/Zeilen, schreiben = Aufgabe abhaken / Haekchen-Feld) ---
@app.get("/api/app/pages")
def api_app_pages(request: Request):
    """Fuer die Widget-Auswahl: Seiten + Datenbanken (mit ihren Ansichten) - nur Titel, Symbol, Eltern."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        rows = c.execute("SELECT id, title, icon, kind, parent_id, schema, cover, cover_pos FROM note WHERE uid=? AND deleted_at IS NULL "
                         "AND is_template=0 AND archived IS NULL ORDER BY sort NULLS FIRST, created LIMIT 5000", (uid,)).fetchall()
    out = []
    for r in rows:
        o = {"id": r[0], "title": r[1] or "", "icon": r[2] or "", "kind": r[3] or "page", "parent_id": r[4],
             "cover": bool(r[6]), "cover_pos": r[7] if r[7] is not None else 50}
        if r[3] == "database":
            o["views"] = [{"id": v.get("id"), "name": v.get("name"), "type": v.get("type")}
                          for v in _json_load(r[5], {}).get("views", []) if isinstance(v, dict)]
        out.append(o)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/api/app/page-image/{nid}/{which}")
def api_app_page_image(nid: int, which: str, request: Request):
    """Widget "Seite": NUR Titelbild bzw. Symbol-Bild einer eigenen Seite (kein Zugriff auf andere Dateien).
    Ohne Titelbild: erstes Bild im Inhalt (wie die Galerie)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    if which not in ("cover", "icon"):
        return _bad("not found", 404)
    with _db() as c:
        r = c.execute("SELECT cover, icon, body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()
        url = "" if not r else (r[1] if which == "icon" else (r[0] or _first_image(r[2], r[3])))
        m = _FILE_URL.fullmatch(str(url or ""))
        f = m and c.execute("SELECT mime FROM file WHERE id=? AND uid=? AND is_image=1", (m.group(1), uid)).fetchone()
    path = os.path.join(FILES_DIR, m.group(1)) if f else ""
    if not f or not os.path.isfile(path):
        return _bad("not found", 404)
    w = _thumb_w(request)
    tp = _thumb(m.group(1), path, f[0], w) if w else None
    return FileResponse(tp or path, media_type=_thumb_mime(tp) if tp else f[0],
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.get("/api/app/rows/{nid}")
def api_app_rows(nid: int, request: Request):
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    d = _db_rows(uid, nid)
    return JSONResponse(d, headers={"Cache-Control": "no-store"}) if d else _bad("not found", 404)


@app.post("/api/app/tasks/{tid}/done")
async def api_app_task_done(tid: int, request: Request):
    """Widget: Aufgabe abhaken (done=true) oder wieder oeffnen - dieselbe Logik wie im Web (Folgeaufgaben)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    with _db() as c, c:
        row = c.execute("SELECT text, done, due, prio, repeat, rep_anchor, next_id FROM task WHERE id=? AND uid=?", (tid, uid)).fetchone()
        if not row:
            return _bad("not found", 404)
        text, done, due, prio, rep, anchor, next_id = row
        next_id, nd = _task_done(c, uid, tid, bool(b.get("done", True)), done, text, due, prio, rep, anchor, next_id, _client_today(b))
        c.execute("UPDATE task SET next_id=? WHERE id=? AND uid=?", (next_id, tid, uid))
    return JSONResponse({"ok": True, "next_due": nd})


@app.post("/api/app/rows/{nid}/check")
async def api_app_row_check(nid: int, request: Request):
    """Widget: NUR ein Haekchen-Feld einer eigenen Datenbank-Zeile setzen."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    pid = str(b.get("pid") or "")
    with _db() as c, c:
        ps = c.execute("SELECT d.schema, n.props FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                       "WHERE n.id=? AND n.uid=? AND d.kind='database' AND n.deleted_at IS NULL", (nid, uid)).fetchone()
        if not ps:
            return _bad("not found", 404)
        schema = _json_load(ps[0], {"props": []})
        if not any(p.get("id") == pid and p.get("type") == "checkbox" for p in schema.get("props", [])):
            return _bad("kein Häkchen-Feld")
        merged = _clean_props(schema, {pid: bool(b.get("value"))}, _json_load(ps[1], {}))
        c.execute("UPDATE note SET props=?, updated=? WHERE id=? AND uid=?", (json.dumps(merged, ensure_ascii=False), time.time(), nid, uid))
    return JSONResponse({"ok": True})


@app.get("/api/notes/{nid}/rows")
def api_db_rows(nid: int, request: Request):
    """Zeilen einer Datenbank (aktive Unterseiten) mit ihren Werten."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    d = _db_rows(uid, nid)      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    return JSONResponse(d, headers={"Cache-Control": "no-store"}) if d else _bad("not found", 404)


def _db_rows(owner, nid):
    """Schema + aktive Zeilen einer Datenbank (None, wenn nicht vorhanden / nicht meine)."""
    with _db() as c:
        db = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not db:
            return None
        rows = c.execute("SELECT id, title, icon, props, updated, created, cover, body, format FROM note WHERE uid=? AND parent_id=? "
                         "AND deleted_at IS NULL AND is_template=0 ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
        tpls = c.execute("SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND is_template=1 "
                         "ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()      # Vorlagen der Datenbank: keine Zeilen (wie Notion)
    return {"schema": _json_load(db[0], {"props": []}), "templates": [{"id": t[0], "title": t[1] or "", "icon": t[2] or ""} for t in tpls],
            "rows": [{"id": r[0], "title": r[1] or "", "icon": r[2] or "", "props": _json_load(r[3], {}),
                      "updated": r[4], "created": r[5], "preview": r[6] or _first_image(r[7], r[8])} for r in rows]}


# --- Konto loeschen (Admin-Bereich): ALLE Daten des Benutzers entfernen -------------------------------
# Wichtig: uid = Benutzername. Blieben Daten liegen, bekaeme ein spaeter neu angelegtes Konto mit
# gleichem Namen fremde Seiten, Kalender-Zugaenge usw.
USER_TABLES = ("task", "note", "note_version", "bookmark", "fav", "device_login", "app_token", "user_kv",
               "immich_key", "cal_account", "app_code")


def delete_user_data(uid):
    with _db() as c, c:
        fids = [r[0] for r in c.execute("SELECT id FROM file WHERE uid=?", (uid,)).fetchall()]
        c.execute("DELETE FROM file WHERE uid=?", (uid,))
        counts = {t: c.execute(f"DELETE FROM {t} WHERE uid=?", (uid,)).rowcount for t in USER_TABLES}
    for fid in fids:
        try:
            os.remove(os.path.join(FILES_DIR, fid))
        except OSError:
            pass
        _drop_thumbs(fid)
    _cal_cache.pop(uid, None)
    counts["file"] = len(fids)
    return counts


def user_usage(uid):
    """Kurzuebersicht fuer den Admin-Bereich (nur Anzahlen, keine Inhalte)."""
    with _db() as c:
        q = lambda sql: c.execute(sql, (uid,)).fetchone()[0]  # noqa: E731
        return {"pages": q("SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL"),
                "tasks": q("SELECT COUNT(*) FROM task WHERE uid=? AND done=0"),
                "bookmarks": q("SELECT COUNT(*) FROM bookmark WHERE uid=?"),
                "files_mb": round(q("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?") / 1048576, 1),
                "devices": q("SELECT COUNT(*) FROM device_login WHERE uid=?"),
                "tokens": q("SELECT COUNT(*) FROM app_token WHERE uid=?"),
                "calendars": q("SELECT COUNT(*) FROM cal_account WHERE uid=?"),
                "immich": q("SELECT COUNT(*) FROM immich_key WHERE uid=?")}


def revoke_user_logins(uid):
    """Alle App-Anmeldungen und App-Schluessel widerrufen (Browser-Sitzungen enden ueber pw_changed)."""
    with _db() as c, c:
        a = c.execute("DELETE FROM device_login WHERE uid=?", (uid,)).rowcount
        b = c.execute("DELETE FROM app_token WHERE uid=?", (uid,)).rowcount
    return a + b
