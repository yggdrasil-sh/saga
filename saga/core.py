"""Grundlagen fuer alle Bereiche: Konfiguration, Vorlagen, Sitzung, Anfrage-Hilfen."""
import asyncio
import hashlib
import json
import os
import time
from datetime import timezone
from zoneinfo import ZoneInfo
from fastapi.responses import JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
import auth
from saga import i18n

t = i18n.t                     # fuer die Bereiche: from saga.core import t

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
# Eigene Domains nur fuer oeffentliche Seiten (Veroeffentlichen): dort gibt es "/", /@... und /static/ (app.py),
# canonical der oeffentlichen Seiten zeigt dann auf diese Domain (publish.py)
PUBLIC_HOSTS = frozenset(h.strip().lower() for h in E.get("PUBLIC_HOSTS", "").split(",") if h.strip())
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
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Repo-/App-Verzeichnis (static/, templates/), nicht saga/


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


tmpl = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                   autoescape=select_autoescape(["html"]))
i18n.install(tmpl)


# --- Sprache: Konto-Einstellung (user_kv 'lang') > Standardsprache des Servers > Accept-Language > Englisch ---
_lang_cache = {}      # uid -> (zeit, sprache)


def user_lang(uid):
    """Im Konto gewaehlte Sprache ('' = automatisch). 60 s zwischengespeichert."""
    hit = _lang_cache.get(uid)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    from db import connect
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='lang'", (uid,)).fetchone()
    v = i18n.norm(r[0]) if r else ""
    if len(_lang_cache) > 2000:
        _lang_cache.clear()
    _lang_cache[uid] = (time.time(), v)
    return v


def set_user_lang(uid, lang):
    lang = i18n.norm(lang)
    from db import connect
    with connect() as c, c:
        if lang:
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'lang', ?)", (uid, lang))
        else:
            c.execute("DELETE FROM user_kv WHERE uid=? AND k='lang'", (uid,))
    _lang_cache[uid] = (time.time(), lang)
    return lang


def server_lang():
    return i18n.norm(auth.settings().get("default_lang"))


# --- Standardnamen -------------------------------------------------------------------------------------
# Entscheidung (Review, Mehrsprachigkeit):
#  - Namen, die der Server beim ANLEGEN vergibt und die danach normale, umbenennbare Benutzerdaten sind (Seite
#    "Eingang", Spalten/Optionen/Ansichten einer neuen Datenbank), werden in der KONTO-Sprache gespeichert
#    (sonst Server-Standard, sonst Sprache der Anfrage) - nicht mehr in der Sprache der zufaellig anfragenden App.
#  - Die Standard-Kategorien "Dienste"/"Lesezeichen" bleiben sprachneutral gespeichert (deutscher Schluessel wie
#    bisher, auch als DB-Standardwert) und werden bei der Ausgabe uebersetzt; erkannt wird jede Sprachfassung,
#    damit ein zurueckgeschriebenes "Bookmarks" ebenfalls passt.
STD_CATEGORIES = ("Dienste", "Lesezeichen")
_std_map = {}


def _std_keys():          # nie aufgerufen - nur damit tools/i18n.py die Schluessel als benutzt erkennt
    return t("Dienste"), t("Lesezeichen"), t("Eingang")      # "Eingang": app_api._inbox ueber t_account


_seen_cache = {}      # uid -> (zeit, zuletzt gemerkte Sprache der Weboberflaeche)


def seen_lang(uid):
    """Sprache, in der die Weboberflaeche dieses Kontos zuletzt angezeigt wurde (nur bei Konto-Sprache 'automatisch'
    gemerkt, siehe remember_ui_lang); '' = unbekannt. 60 s zwischengespeichert."""
    hit = _seen_cache.get(uid)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    from db import connect
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='lang_seen'", (uid,)).fetchone()
    v = i18n.norm(r[0]) if r else ""
    if len(_seen_cache) > 2000:
        _seen_cache.clear()
    _seen_cache[uid] = (time.time(), v)
    return v


def remember_ui_lang(uid, request):
    """Dashboard/Seiten aufgerufen: Bei Konto-Sprache 'automatisch' und ohne Server-Standard die Browser-Sprache
    merken. Damit bekommen Standardnamen auch bei Anfragen OHNE Browser-Sprache (API, Apps, Teilen-Ziel) die
    Sprache, die die Person in Saga sieht - statt Englisch. Schreibt nur bei einer Aenderung."""
    try:
        if user_lang(uid) or server_lang():
            return
        lang = i18n.from_header(request.headers.get("accept-language", ""))
        if not lang or seen_lang(uid) == lang:
            return
        from db import connect
        with connect() as c, c:
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'lang_seen', ?)", (uid, lang))
        _seen_cache[uid] = (time.time(), lang)
    except Exception:  # noqa: BLE001 - nur ein Komfort, die Seite soll deswegen nie scheitern
        pass


def account_lang(uid):
    """Sprache fuer gespeicherte Standardnamen: Konto-Sprache > Server-Standard > zuletzt gesehene Sprache der
    Weboberflaeche (Konto 'automatisch') > Sprache der Anfrage."""
    try:
        return user_lang(uid) or server_lang() or seen_lang(uid) or i18n.get_lang()
    except Exception:  # noqa: BLE001 - Datenbank gerade weg
        return i18n.get_lang()


def t_account(uid, key, **params):
    return i18n.t_lang(account_lang(uid), key, **params)


def std_name(s):
    """Standard-Kategorie (in irgendeiner Sprache gespeichert) -> Sprache der Anfrage; alles andere unveraendert."""
    if not _std_map:
        for k in STD_CATEGORIES:
            for lang in i18n.LANGS:
                _std_map[i18n.t_lang(lang, k)] = k
            _std_map[k] = k
    k = _std_map.get(s or "")
    return t(k) if k else s


def _lang_is_cached(uid):
    """Kann die Sprache ohne Datenbank bestimmt werden (Konto-Sprache und Einstellungen frisch im Zwischenspeicher)?"""
    now, st = time.time(), auth._settings_cache
    if st.get("v") is None or now - st.get("t", 0) >= 5:
        return False
    if not uid:
        return True
    hit = _lang_cache.get(uid)
    return bool(hit and now - hit[0] < 60)


class LangMiddleware:
    """Setzt die Sprache der Anfrage (i18n.t, Jinja _()). Muss INNERHALB der SessionMiddleware laufen
    (also vor ihr mit add_middleware registriert werden); session_key: 'uid' (Hauptseite) bzw. 'aid' (Admin)."""

    def __init__(self, app, session_key="uid"):
        self.app, self.key = app, session_key

    @staticmethod
    def _lookup(uid, header):
        try:
            return i18n.resolve(user_lang(uid) if uid else "", server_lang(), header)
        except Exception:  # noqa: BLE001 - Datenbank gerade weg: Browser-Sprache reicht
            return i18n.resolve("", "", header)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        uid = (scope.get("session") or {}).get(self.key)
        header = next((v.decode("latin-1") for k, v in scope.get("headers") or () if k == b"accept-language"), "")
        if scope.get("path", "").startswith("/static/"):
            lang = i18n.resolve("", "", header)                   # Dateien brauchen keine Konto-Sprache
        elif _lang_is_cached(uid):
            lang = self._lookup(uid, header)
        else:                                                     # Datenbank fragen: im Thread, nie im Event-Loop
            lang = await asyncio.to_thread(self._lookup, uid, header)
        token = i18n.set_lang(lang)
        try:
            await self.app(scope, receive, send)
        finally:
            i18n._cur.reset(token)


# --- Groesse von Anfragen begrenzen (vor jeder Anmeldung/Pruefung) -----------------------------------
# Ohne Grenze liest request.json()/form() beliebig viel in den Speicher (bzw. ins RAM-tmpfs /tmp).
# Allgemein REQUEST_MAX_MB (Standard 2 MB) - reicht auch fuer Seiten (NOTE_BLOCKS_MAX = 200k Zeichen JSON);
# Datei-Uploads bekommen ihre eigene Grenze (ATTACH_MAX_MB + Formular-Rahmen), siehe app.py.
REQUEST_MAX = max(1, int(E.get("REQUEST_MAX_MB", "2"))) * 1024 * 1024


class BodyTooLarge(Exception):
    """Anfrage groesser als erlaubt (wird in BodyLimit zu HTTP 413)."""


async def _send_413(send):
    body = b'{"success":0,"error":"request too large"}'
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class BodyLimit:
    """ASGI-Middleware: zu grosse Anfragen mit 413 ablehnen. Content-Length wird sofort geprueft; ohne
    (Transfer-Encoding: chunked) wird mitgezaehlt und beim Ueberschreiten abgebrochen. Antwortet der Endpunkt
    danach selbst (z. B. 400, weil _body den Abbruch als Fehler schluckt), wird daraus ebenfalls 413.
    rules: [(methode oder None, kompiliertes Muster fuer den Pfad, grenze)] - die erste passende gilt."""

    def __init__(self, app, default=REQUEST_MAX, rules=()):
        self.app, self.default, self.rules = app, default, tuple(rules)

    def limit(self, method, path):
        for m, rx, n in self.rules:
            if (m is None or m == method) and rx.fullmatch(path):
                return n
        return self.default

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        lim = self.limit(scope.get("method", "GET"), scope.get("path", ""))
        for k, v in scope.get("headers") or ():
            if k == b"content-length":
                try:
                    if int(v) > lim:
                        return await _send_413(send)
                except ValueError:
                    pass
        st = {"seen": 0, "over": False, "started": False, "replaced": False}

        async def rcv():
            if st["over"]:
                raise BodyTooLarge()
            msg = await receive()
            if msg.get("type") == "http.request":
                st["seen"] += len(msg.get("body") or b"")
                if st["seen"] > lim:
                    st["over"] = True
                    raise BodyTooLarge()
            return msg

        async def snd(msg):
            if st["replaced"]:
                return                                   # Antwort des Endpunkts verwerfen - 413 ist schon raus
            if st["over"] and not st["started"] and msg.get("type") == "http.response.start":
                st["started"] = st["replaced"] = True
                return await _send_413(send)
            if msg.get("type") == "http.response.start":
                st["started"] = True
            await send(msg)

        try:
            await self.app(scope, rcv, snd)
        except BodyTooLarge:
            if not st["started"]:
                st["started"] = st["replaced"] = True
                await _send_413(send)


SEC_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://cdn.jsdelivr.net data: blob:; "
                               "style-src 'self'; script-src 'self'; connect-src 'self'; "
                               "frame-src 'self' https://www.youtube-nocookie.com; "        # Einbetten-Block: nur YouTube ohne Cookies
                               "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
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


async def _body(request):
    try:
        b = await request.json()
        return b if isinstance(b, dict) else None
    except Exception:
        return None


def _bad(msg, code=400):
    return JSONResponse({"error": msg}, status_code=code)


def _json_load(s, default):
    try:
        v = json.loads(s) if s else default
        return v if isinstance(v, type(default)) else default
    except ValueError:
        return default


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
