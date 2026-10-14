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
import json
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import auth
import db
from version import API_LEVEL, MIN_APP, VERSION

from saga import app_api, app_login, calendars, comments, dashboard, databases, files, immich, links, page_tools, pages, publish, sharing, tasks, web_auth
from saga import chat, i18n, live, notify
from saga.core import BASE_URL, E, HERE, PUBLIC_HOSTS, REQUEST_MAX, SECRET, SESSION_HOURS, BodyLimit, LangMiddleware, title
from saga.dashboard import _status_loop
from saga.files import files_gc, files_init, tmp_cleanup
from saga.notes import FILE_MAX

# Re-Exporte fuer admin.py ("import app as hk"), run.py, Tests (app.files_gc, app.delete_user_data)
# und tools/migrate_hlidskjalf.py (app._cal_fernet)
from saga.calendars import _cal_fernet  # noqa: F401
from saga.core import ADMIN_URL, ASSET_V, CA_FILE  # noqa: F401
from saga.dashboard import all_services, services_changed, visible_services  # noqa: F401
from saga.pages import svc_icon_path  # noqa: F401
from saga.userdata import delete_user_data, revoke_user_logins, user_usage  # noqa: F401
from saga.web_auth import EMAIL_RE, PROXY_WARN, _ip  # noqa: F401
__all__ = ["app", "files_gc", "ADMIN_URL", "ASSET_V", "BASE_URL", "CA_FILE", "EMAIL_RE", "HERE", "PROXY_WARN", "SECRET", "_cal_fernet", "_ip",
           "all_services", "delete_user_data", "revoke_user_logins", "services_changed", "svc_icon_path", "title", "user_usage",
           "visible_services"]


# --- App --------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app):
    await asyncio.to_thread(db.wait_ready)
    await asyncio.to_thread(db.init_schema)
    await asyncio.to_thread(auth.bootstrap_admin)
    files_init()
    task = asyncio.create_task(_status_loop())
    live.start()                                             # Live-Kanal (/api/events) und Anwesenheit

    async def _gc_loop():
        purged = 0.0
        while True:
            try:
                if time.time() - purged >= 86400:             # Benachrichtigungen aelter als 90 Tage: beim Start und taeglich
                    purged = time.time()
                    n = await asyncio.to_thread(notify.purge)
                    if n:
                        print(f"notify: {n} alte Benachrichtigung(en) entfernt", flush=True)
            except Exception as ex:
                print(f"notify-purge: {type(ex).__name__}: {ex}", flush=True)
            try:
                n = await asyncio.to_thread(chat.purge)        # abgelaufene Chat-Nachrichten, Anhaenge ohne Nachricht: stuendlich
                if n:
                    print(f"chat: {n} abgelaufene Nachricht(en) entfernt", flush=True)
            except Exception as ex:
                print(f"chat-purge: {type(ex).__name__}: {ex}", flush=True)
            await asyncio.sleep(3600)
            try:
                n = await asyncio.to_thread(files_gc)
                if n:
                    print(f"files-gc: {n} verwaiste Datei(en) entfernt", flush=True)
                await asyncio.to_thread(tmp_cleanup)          # liegengebliebene Zwischendateien (> 1 h)
            except Exception as ex:
                print(f"files-gc: {type(ex).__name__}: {ex}", flush=True)
    gc_task = asyncio.create_task(_gc_loop())
    yield
    live.stop()
    task.cancel()
    gc_task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(LangMiddleware, session_key="uid")      # vor SessionMiddleware registriert = laeuft danach (kennt die Sitzung)
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
# Eigene Domains nur fuer oeffentliche Seiten (z. B. blog.example.org -> Startseite fuer Besucher und /@name/...):
# dort werden "/", "/@..." und /static/ ausgeliefert, alles andere (Anmeldung, API) geht weiter auf BASE_URL.


def _public_path(path):
    return path == "/" or path.startswith("/@") or path.startswith("/static/")


@app.middleware("http")
async def canonical_host(request: Request, call_next):
    if CANONICAL and request.url.path != "/healthz":
        # Reverse Proxys, die den Host umschreiben, liefern den oeffentlichen Namen in X-Forwarded-Host.
        # Unkritisch: der Header entscheidet nur OB umgeleitet wird - das Ziel ist immer die feste BASE_URL.
        fwd = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        host = fwd or (request.headers.get("host") or "").lower()
        if host and host != _BASE_HOST and not (host in PUBLIC_HOSTS and _public_path(request.url.path)):
            q = ("?" + request.url.query) if request.url.query else ""
            return RedirectResponse(BASE_URL + request.url.path + q, status_code=308)
    return await call_next(request)


# Groesse von Anfragen begrenzen - als letzte registriert = laeuft zuerst (vor Sitzung, Umleitung, Endpunkten).
# Uploads: ATTACH_MAX_MB + Formular-Rahmen (etwas mehr als die Pruefung in files.py, die mit Text antwortet).
app.add_middleware(BodyLimit, default=REQUEST_MAX,
                   rules=[("POST", re.compile(r"/api/files"), max(REQUEST_MAX, FILE_MAX + 256 * 1024)),
                          ("POST", re.compile(r"/api/chats/[0-9]+/files"), max(REQUEST_MAX, FILE_MAX + 1024))])


# --- Routes -----------------------------------------------------------------
@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/api/version")
def api_version():
    """Fuer Apps und Monitoring: Server-Version, Schnittstellen-Stand und die aelteste App-Version, die alles kann."""
    return JSONResponse({"name": "Saga", "version": VERSION, "api": API_LEVEL, "min_app": MIN_APP, "schema": db.SCHEMA_VERSION},
                        headers={"Cache-Control": "no-store"})


@app.get("/i18n/{lang}.js")
def i18n_js(lang: str, v: str = ""):
    """Woerterbuch fuer das Front-End (window.SAGA_I18N). Mit passendem ?v= unbegrenzt zwischenspeicherbar."""
    if lang not in i18n.LANGS:
        return PlainTextResponse("not found", status_code=404)
    body = "window.SAGA_I18N=" + json.dumps(i18n.js_catalog(lang), ensure_ascii=False, separators=(",", ":")) + ";\n"
    cache = "public, max-age=31536000, immutable" if v == i18n.VERSION else "no-cache"
    return Response(body, media_type="text/javascript; charset=utf-8", headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"})


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

# --- Bereiche (Reihenfolge wie bisher: zuerst registrierte Pfade gewinnen) ---
app.include_router(web_auth.router)
app.include_router(app_login.router)
app.include_router(dashboard.router)
app.include_router(links.router)
app.include_router(tasks.router)
app.include_router(pages.router)
app.include_router(page_tools.router)
app.include_router(files.router)
app.include_router(databases.router)
app.include_router(immich.router)
app.include_router(calendars.router)
app.include_router(app_api.router)
app.include_router(publish.router)          # /@name/... (oeffentlich) und /api/notes/{nid}/publish, /api/account/public-name
app.include_router(sharing.router)          # Teilen: /api/shared, /api/notes/{nid}/shares, /api/share/targets
app.include_router(comments.router)         # Kommentare: /api/notes/{nid}/comments, /api/comments/{cid}...
app.include_router(notify.router)           # Benachrichtigungen: /api/notifications, .../read, .../settings
app.include_router(live.router)             # Live-Kanal: /api/events (SSE), /api/presence
app.include_router(chat.router)             # Chat (E2E): /chats, /api/chat/..., /api/chats/...
