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
from version import API_LEVEL, BUILD, MIN_APP, VERSION, channel

from saga import app_api, app_login, calendars, comments, dashboard, databases, files, immich, links, page_tools, pages, publish, sharing, tasks, web_auth
from saga import ai, ai_chat, avatars, calls, chat, chat_files, federation, horn, horn_ops, i18n, live, mfa, notify, push, push_stream
from saga import fed_calls, fed_chat, fed_dir, fed_keys, fed_pages      # Foederation 2.3: Adressbuch, Schluessel, Chats, Anrufe, Seiten
from saga import chat_status                     # 2.4: Gelesen-Bestaetigungen und Online-Status (haengt sich in chat, live, fed_chat ein)
from saga import chat_x3                         # 2.5: Reaktionen, Bearbeiten, Sprachnachrichten (haengt sich in chat, fed_chat ein)
from saga.core import BASE_URL, E, HERE, PUBLIC_HOSTS, REQUEST_MAX, SECRET, SESSION_HOURS, BodyLimit, Compress, LangMiddleware, MEDIA_FRONT, MediaAuthHint, SecHeaders, title
from saga.dashboard import _status_loop
from saga.files import files_gc, files_init, tmp_cleanup
from saga.notes import FILE_MAX
from saga import backups, graph, nextcloud, pageindex, siteicons, userbackup

# Re-Exporte fuer admin.py ("import app as hk"), run.py, Tests (app.files_gc, app.delete_user_data)
# und tools/migrate_hlidskjalf.py (app._cal_fernet)
from saga.calendars import _cal_fernet  # noqa: F401
from saga.core import ADMIN_URL, ASSET_V, CA_FILE  # noqa: F401
from saga.dashboard import all_services, services_changed, visible_services  # noqa: F401
from saga.siteicons import svc_icon_path  # noqa: F401
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
    push.start()                                             # Mitteilungen aufs Handy/Desktop (Warteschlange)
    push_stream.start()                                      # 2.5: eigene Push-Verbindung der Apps (/api/push/stream)
    federation.start()                                       # Foederation: Ausgangskorb (Ereignisse an andere Instanzen)
    calls.start()                                            # Anrufe: Klingeldauer, verlorene Verbindungen (2.1)
    pageindex.start()                                        # Suchtext/Verweise/Vorschaubilder abgleichen (2.2)
    backups.start()                                          # verschluesselte Backups auf WebDAV/Nextcloud (2.3, Zeitplan)
    userbackup.start()                                       # persoenliche Backups je Konto (2.3, Zeitplan)

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
                await asyncio.to_thread(chat.convert_v3)       # 2.5: bestehende Chats auf Stufe 3 umstellen (wenn bereit)
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
    backups.stop()
    userbackup.stop()
    pageindex.stop()
    calls.stop()
    push_stream.stop()
    push.stop()
    live.stop()
    federation.stop()
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
# gzip fuer JSON/Text/JS/CSS ab 1 KB (saga.core.Compress) - nicht fuer den Live-Kanal/KI-Stream (text/event-stream),
# Bilder, Dateien, Zip, Teilantworten. Hier registriert = laeuft innerhalb von canonical_host/BodyLimit.
app.add_middleware(Compress)
if MEDIA_FRONT:                                            # 2.4: saga-media davor - angemeldete Anfragen kennzeichnen (S1b)
    app.add_middleware(MediaAuthHint)
class _Static(StaticFiles):
    """Bughunt P05: /static/...?v=<aktueller Fingerabdruck> unbegrenzt zwischenspeichern (der Fingerabdruck aendert sich
    mit jeder Datei in static/), sonst bei jeder Nutzung nachfragen (304) - vorher galt die heuristische Cache-Zeit."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        r = super().file_response(full_path, stat_result, scope, status_code)
        qs = scope.get("query_string") or b""
        if r.status_code == 200 and (b"v=" + ASSET_V.encode()) in qs.split(b"&"):
            r.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            r.headers.setdefault("Cache-Control", "no-cache")
        return r


app.mount("/static", _Static(directory=os.path.join(HERE, "static")), name="static")

# Aufrufe ueber andere Hostnamen (interne IP/DNS, alter Name) auf BASE_URL umleiten.
# Verhindert Login-Fehler (mismatching_state), weil Cookies an genau einen Host gebunden sind.
CANONICAL = E.get("CANONICAL_REDIRECT", "true").lower() in ("1", "true", "yes", "on")
_BASE_HOST = urlparse(BASE_URL).netloc.lower()
# Eigene Domains nur fuer oeffentliche Seiten (z. B. blog.example.org -> Startseite fuer Besucher und /@name/...):
# dort werden "/", "/@..." und /static/ ausgeliefert, alles andere (Anmeldung, API) geht weiter auf BASE_URL.


def _public_path(path):
    # /api/avatar/: Profilbilder auf veroeffentlichten Seiten (nur, wenn die Person es erlaubt hat - prueft saga.avatars)
    # /lang: Sprachumschalter der oeffentlichen 404-Seite (setzt nur das Sprach-Cookie dieser Domain)
    return (path in ("/", "/lang") or path.startswith("/@") or path.startswith("/static/")
            or path.startswith("/api/avatar/"))


@app.middleware("http")
async def canonical_host(request: Request, call_next):
    # /internal/...: nur saga-media ueber 127.0.0.1 (mit internem Schluessel) - nie umleiten, sonst bekommt z. B. die
    # Abfrage /internal/horn/conf nur 308 und der eingebaute Knoten startet nie
    if CANONICAL and request.url.path != "/healthz" and not request.url.path.startswith("/internal/"):
        # Reverse Proxys, die den Host umschreiben, liefern den oeffentlichen Namen in X-Forwarded-Host.
        # Unkritisch: der Header entscheidet nur OB umgeleitet wird - das Ziel ist immer die feste BASE_URL.
        fwd = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
        host = fwd or (request.headers.get("host") or "").lower()
        if host and host != _BASE_HOST and not (host in PUBLIC_HOSTS and _public_path(request.url.path)):
            q = ("?" + request.url.query) if request.url.query else ""
            return RedirectResponse(BASE_URL + request.url.path + q, status_code=308)
    return await call_next(request)


# 2.5 Gjallarhorn: connect-src der HTML-Seiten um die Adressen der Knoten ergaenzen (nur mit Feature-Flag calls.horn)
app.add_middleware(horn.NodeCsp)
app.add_middleware(SecHeaders)          # Bughunt Sicherheit V2/V3: HSTS, Permissions-Policy, X-Frame-Options


# Groesse von Anfragen begrenzen - als letzte registriert = laeuft zuerst (vor Sitzung, Umleitung, Endpunkten).
# Uploads: ATTACH_MAX_MB + Formular-Rahmen (etwas mehr als die Pruefung in files.py, die mit Text antwortet).
app.add_middleware(BodyLimit, default=REQUEST_MAX,
                   rules=[("POST", re.compile(r"/api/files"), max(REQUEST_MAX, FILE_MAX + 256 * 1024)),
                          ("POST", re.compile(r"/api/chats/[0-9]+/files"), max(REQUEST_MAX, FILE_MAX + 1024)),
                          ("PUT", re.compile(r"/api/chat/uploads/[A-Za-z0-9_-]+"), chat.UPLOAD_PART + 1024),     # 2.4 ohne saga-media
                          ("POST", re.compile(r"/fed/v2/notes/[0-9]+/files"), max(REQUEST_MAX, FILE_MAX + 64 * 1024)),
                          ("PUT", re.compile(r"/api/account/avatar"), max(REQUEST_MAX, avatars.UPLOAD_MAX + 64 * 1024)),
                          ("POST", re.compile(r"/api/account/backup/restore/upload"), userbackup.HARD_MAX_MB * 1024 * 1024 + 1024 * 1024)])
# 2.5: eigene Push-Verbindung der Apps (WebSocket /api/push/stream) - als letzte registriert = ganz aussen, ohne die
# Middlewares und das Routing darunter (spart Speicher je ruhender Verbindung, saga.push_stream.Front)
app.add_middleware(push_stream.Front)


# Bughunt D11: unbekannte Adressen im Browser bekommen die gestaltete 404 statt rohem JSON (API und Apps unveraendert)
from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: E402
from fastapi.exception_handlers import http_exception_handler as _http_exc  # noqa: E402

_JSON_404 = ("/api/", "/fed/", "/internal/", "/static/", "/.well-known/", "/i18n/")


@app.exception_handler(_StarletteHTTPException)
async def _html_404(request: Request, exc):
    if (exc.status_code == 404 and request.method in ("GET", "HEAD") and not request.url.path.startswith(_JSON_404)
            and "text/html" in (request.headers.get("accept") or "")):
        try:
            return publish.not_found()
        except Exception:  # noqa: BLE001 - lieber die schlichte Antwort als ein 500
            pass
    return await _http_exc(request, exc)


# --- Routes -----------------------------------------------------------------
@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/api/version")
def api_version():
    """Fuer Apps und Monitoring: Server-Version, Schnittstellen-Stand und die aelteste App-Version, die alles kann.
    version = SemVer ohne Build-Kennung (z. B. "2.2.0" oder "2.2.0-rc.7"), build = Kurz-SHA bei Testing-Builds,
    channel = stable | rc | testing."""
    return JSONResponse({"name": "Saga", "version": VERSION, "build": BUILD, "channel": channel(), "api": API_LEVEL,
                         "min_app": MIN_APP, "schema": db.SCHEMA_VERSION},
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
app.include_router(mfa.router)              # Zweiter Faktor: /security, /api/account/security|passkeys|totp|recovery
app.include_router(app_login.router)
app.include_router(dashboard.router)
app.include_router(links.router)
app.include_router(graph.router)                 # 2.5: Graph der Seiten (wie Obsidian)
app.include_router(tasks.router)
# Foederation zuerst: faengt nur NEGATIVE Seiten-/Kommentar-IDs (Seiten anderer Instanzen, Konverter "fedid") ab, dazu
# /.well-known/saga, /fed/v1/... und /api/fed/... - alle positiven IDs laufen unveraendert zu pages/comments
app.include_router(federation.router)
app.include_router(fed_pages.router)         # Teilen vollstaendig (2.3): /fed/v2/notes/..., /fed/v2/live, /api/notes/{-id}/rows|versions|export|move
app.include_router(pages.router)
app.include_router(siteicons.router)          # automatische Kachel-Symbole: /api/services/favicon/{key}
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
app.include_router(chat_files.router)       # Videos im Chat (2.4): /api/chats/{cid}/uploads, /api/chat/uploads/{id}, Medien-Token
app.include_router(push.router)             # Mitteilungen: /api/push..., /sw.js
app.include_router(ai.router)               # KI-Assistent: /api/ai/status, /api/ai/ask, /api/ai/page/{nid}, /api/ai/history
app.include_router(ai_chat.router)          # KI im Chat (nur auf Zuruf): /api/chats/{cid}/ai
app.include_router(avatars.router)          # Profilbilder: /api/avatar/{schluessel}, /api/account/avatar
app.include_router(calls.router)            # Anrufe (WebRTC, Signalisierung ueber den Live-Kanal): /api/calls...
app.include_router(horn.router)             # Gjallarhorn (2.5): Steuerkanal /api/horn/ctl, /internal/horn/conf (saga-media)
app.include_router(horn_ops.router)         # Gjallarhorn (2.5 P6): Kopplung /api/horn/whoami, /api/horn/pair
app.include_router(fed_dir.router)          # Adressbuch (2.3): /fed/v2/directory, /api/fed/people, /api/account/federation
app.include_router(fed_keys.router)         # Schluesselverzeichnis + Krypto v2 (2.3): /fed/v2/keys, /api/chat/v2/...
app.include_router(fed_chat.router)         # Chats + Anrufe ueber Servergrenzen (2.3): /fed/v2/chat..., /fed/v2/call, /api/chat/blocks
app.include_router(chat_status.router)      # Gelesen/Online (2.4): /api/chat/privacy, /api/chat/presence, /api/chats/{cid}/receipts
app.include_router(chat_x3.router)          # 2.5: PUT /api/chats/{cid}/messages/{mid}/reaction, GET /api/chats/{cid}/reactions
assert fed_calls.router is fed_chat.router
app.include_router(nextcloud.router)         # Nextcloud-Dateien in Seiten (2.3): /api/settings/nextcloud, /api/nextcloud/..., /api/notes/{nid}/nc
app.include_router(userbackup.router)        # Persoenliche Backups (2.3): /api/account/backup...
