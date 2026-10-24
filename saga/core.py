"""Grundlagen fuer alle Bereiche: Konfiguration, Vorlagen, Sitzung, Anfrage-Hilfen."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import zlib
from datetime import timezone
from zoneinfo import ZoneInfo
from fastapi.responses import JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
import auth
from db import clean_str
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
# Testwiederherstellung auf einem zweiten Server (saga/backups.py): ein zurueckgespieltes Backup IST dieselbe Instanz
# (Foederations-Schluessel in der Datenbank) - daher keine ausgehenden Verbindungen: Foederation, Push, Backups und
# Versionshinweis bleiben aus, Websuche ebenso; Kalender, KI, Immich und Avatare sperrt saga/netguard.py (liest dieselbe
# Variable selbst, weil hkcal.py es ohne saga.core nutzt).
RESTORE_TEST = E.get("SAGA_RESTORE_TEST", "").strip().lower() in ("1", "true", "yes", "on")
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
# Medienpfad (2.4): saga-media (media/, Go) steht vor uvicorn und setzt beim Start SAGA_MEDIA_KEY (Geheimnis fuer
# /internal/media/authz, saga/chat.py) und SAGA_INTERNAL_PORT. Das Geheimnis nicht an Kindprozesse weitergeben.
MEDIA_KEY = E.pop("SAGA_MEDIA_KEY", "")
MEDIA_FRONT = bool(MEDIA_KEY and E.get("SAGA_INTERNAL_PORT"))
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


# --- Sprache: Konto-Einstellung (user_kv 'lang') > Sprach-Cookie (Umschalter) > Standardsprache des Servers
#     > Accept-Language > Englisch ---
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
        lang = (i18n.norm(request.cookies.get(i18n.COOKIE))                     # Umschalter dieses Browsers
                or i18n.from_header(request.headers.get("accept-language", "")))
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
    def _lookup(uid, header, cookie, hint=""):
        """-> (Sprache, ausdruecklich gewaehlte Sprache: Konto, sonst Cookie). hint = Sprache der App (?lang=):
        nach der Wahl der Person (Konto, Umschalter), vor Server-Standard und Browser."""
        try:
            ul = user_lang(uid) if uid else ""
            return i18n.resolve(ul, server_lang(), header, cookie or hint), ul or cookie
        except Exception:  # noqa: BLE001 - Datenbank gerade weg: Cookie bzw. Browser-Sprache reicht
            return i18n.resolve("", "", header, cookie or hint), cookie

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        uid = (scope.get("session") or {}).get(self.key)
        hdrs = scope.get("headers") or ()
        header = next((v.decode("latin-1") for k, v in hdrs if k == b"accept-language"), "")
        raw_ck = next((v.decode("latin-1") for k, v in hdrs if k == b"cookie"), "")
        cookie = i18n.cookie_from_header(raw_ck)
        path = scope.get("path", "")
        # Apps oeffnen Anmeldung/Kopplung mit ?lang=de|en|es: gilt fuer diese Browser-Sitzung (Sitzungs-Cookie, auch
        # ueber die Weiterleitungen /app/login -> /login -> 2FA -> /app/handoff), unbekannte Werte zaehlen nicht
        qhint = _app_lang_param(scope) if _APP_LANG_PATHS.match(path) else ""
        hint = qhint or i18n.cookie_from_header(raw_ck, i18n.HINT_COOKIE)
        if path.startswith("/static/"):
            lang, pref = i18n.resolve("", "", header, cookie), cookie          # Dateien brauchen keine Konto-Sprache
        elif _lang_is_cached(uid):
            lang, pref = self._lookup(uid, header, cookie, hint)
        else:                                                     # Datenbank fragen: im Thread, nie im Event-Loop
            lang, pref = await asyncio.to_thread(self._lookup, uid, header, cookie, hint)
        if qhint:
            send = _with_cookie(send, f"{i18n.HINT_COOKIE}={qhint}; Path=/; HttpOnly; SameSite=Lax"
                                      + ("; Secure" if BASE_URL.startswith("https://") else ""))
        token, tok_ui = i18n.set_lang(lang), i18n.set_ui(pref, _here(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            i18n._cur.reset(token)
            i18n._ui.reset(tok_ui)


_APP_LANG_PATHS = re.compile(r"/(login(/.*)?|register|security|app/.*)\Z")


def _app_lang_param(scope):
    from urllib.parse import parse_qs
    try:
        q = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    except ValueError:
        return ""
    v = (q.get("lang") or [""])[0]
    return i18n.norm(v) if len(v) <= 8 else ""


def _with_cookie(send, value):
    """send-Wrapper: Set-Cookie an den Kopf der Antwort haengen."""
    async def wrapped(msg):
        if msg["type"] == "http.response.start":
            msg = dict(msg, headers=list(msg.get("headers") or []) + [(b"set-cookie", value.encode("latin-1"))])
        await send(msg)
    return wrapped


def _here(scope):
    """Adresse der laufenden Anfrage als Ruecksprung fuer den Sprachumschalter. Ohne Abfrage bei /app/handoff (?go=
    ist ein Einmal-Code der Bestaetigung) und bei sehr langen Adressen."""
    path = scope.get("path") or "/"
    q = (scope.get("query_string") or b"").decode("latin-1")
    if q and path != "/app/handoff" and len(path) + len(q) < 600:
        return path + "?" + q
    return path


def safe_next(v, default="/"):
    """Ruecksprungziel des Sprachumschalters: nur eine Adresse auf diesem Server (/..., kein //host, kein \\)."""
    v = str(v or "")
    if not v.startswith("/") or v.startswith("//") or "\\" in v or len(v) > 1000 or any(ord(ch) < 33 for ch in v):
        return default
    return v


def lang_switch_response(request, uid, secure):
    """GET /lang?l=de|en|es|''&next=/pfad (Hauptseite und Admin-Bereich): Sprach-Cookie setzen bzw. ('' = automatisch)
    loeschen, zurueck zur Seite. Angemeldet und im Konto eine feste Sprache eingestellt: die Konto-Sprache mit
    aendern - sonst haette der Umschalter keine sichtbare Wirkung (das Konto hat Vorrang)."""
    from starlette.responses import RedirectResponse
    lang = i18n.norm(request.query_params.get("l"))
    if uid:
        try:
            if user_lang(uid):
                set_user_lang(uid, lang)
        except Exception:  # noqa: BLE001 - Datenbank gerade weg: dann nur das Cookie
            pass
    resp = RedirectResponse(safe_next(request.query_params.get("next")), status_code=303,
                            headers={"Cache-Control": "no-store"})
    if lang:
        resp.set_cookie(i18n.COOKIE, lang, max_age=i18n.COOKIE_MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")
    else:
        resp.delete_cookie(i18n.COOKIE, path="/")
    return resp


# --- Sprachumschalter in den Vorlagen (Anmeldeseiten, Admin-Bereich, oeffentliche Fehlerseiten) --------------------
def _switch_label():
    """Beschriftung des Umschalters - ausser auf Englisch zusaetzlich 'Language', damit man ihn in fremder Sprache findet."""
    s = t("Sprache")
    return s if i18n.get_lang() == "en" else s + " · Language"


def lang_switch(cls=""):
    """Auswahlmenue -> GET /lang. static/lang.js schickt die Auswahl sofort ab; ohne JavaScript bleibt der Knopf sichtbar."""
    from markupsafe import Markup, escape
    cur, here = i18n._ui.get()
    lbl = escape(_switch_label())
    opts = [f'<option value=""{" selected" if not cur else ""}>{escape(t("Automatisch (Browser)"))}</option>']
    for code, name in i18n.NAMES.items():
        opts.append(f'<option value="{code}" lang="{code}"{" selected" if cur == code else ""}>{escape(name)}</option>')
    return Markup(f'<form class="lang-sw{" " + escape(cls) if cls else ""}" method="get" action="/lang">'
                  f'<input type="hidden" name="next" value="{escape(here)}">'
                  f'<label class="lang-sw-l" title="{lbl}"><span aria-hidden="true">🌐</span>'
                  f'<select class="lang-sw-sel" name="l" aria-label="{lbl}">{"".join(opts)}</select></label>'
                  f'<button class="btn lang-sw-go" type="submit">{escape(t("Sprache wechseln"))}</button></form>')


def lang_links():
    """Dasselbe als Links (oeffentliche Fehlerseiten: dort laufen keine Skripte)."""
    from urllib.parse import urlencode
    from markupsafe import Markup, escape
    here, cur = i18n._ui.get()[1], i18n.get_lang()
    items = []
    for code, name in i18n.NAMES.items():
        href = "/lang?" + urlencode({"l": code, "next": here})
        att = ' aria-current="true"' if code == cur else ""
        items.append(f'<a href="{escape(href)}" lang="{code}" hreflang="{code}"{att}>{escape(name)}</a>')
    return Markup(f'<nav class="pub-lang" aria-label="{escape(_switch_label())}"><span aria-hidden="true">🌐</span> '
                  + " · ".join(items) + "</nav>")


def install_lang_ui(env):
    env.globals.update(lang_switch=lang_switch, lang_links=lang_links)


install_lang_ui(tmpl)


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
        except Exception as e:
            # BaseHTTPMiddleware (z. B. canonical_host) reicht Fehler aus dem Endpunkt als ExceptionGroup weiter
            if not _too_large(e):
                raise
            if not st["started"]:
                st["started"] = st["replaced"] = True
                await _send_413(send)


def _too_large(e):
    """True, wenn e ein BodyTooLarge ist oder eine (verschachtelte) ExceptionGroup nur aus solchen."""
    if isinstance(e, BodyTooLarge):
        return True
    subs = getattr(e, "exceptions", None)                  # (Base)ExceptionGroup
    return isinstance(subs, tuple) and bool(subs) and all(_too_large(x) for x in subs)


# --- Kompression (gzip) fuer Antworten ---------------------------------------
# Eigene kleine Middleware statt starlette.GZipMiddleware: die komprimiert jeden Inhaltstyp (auch JPEG/PNG/Zip,
# Teilantworten 206 fuer Video-Spulen) und ihr Verhalten haengt von der Starlette-Version ab (requirements ohne Pin).
# Hier: nur Text/JSON/JS/CSS/SVG, nie text/event-stream (Live-Kanal, KI-Stream) oder x-ndjson, nie bei
# Content-Encoding/Content-Range/206/204/304, erst ab GZIP_MIN Bytes. Gestreamte Antworten (StaticFiles in 64-KB-Stuecken)
# werden stueckweise mit Z_SYNC_FLUSH komprimiert - jedes Stueck kommt sofort beim Client an.
GZIP_MIN = max(0, int(E.get("GZIP_MIN_BYTES", "1024")))
GZIP_LEVEL = min(9, max(0, int(E.get("GZIP_LEVEL", "5"))))   # 0 = aus
_GZ_TYPES = ("application/json", "text/", "application/javascript", "application/manifest+json", "image/svg+xml",
             "application/xml", "application/activity+json", "application/ld+json")
_GZ_NEVER = ("text/event-stream", "application/x-ndjson")


def _gz_wanted(scope):
    for k, v in scope.get("headers") or ():
        if k == b"accept-encoding":
            for part in v.decode("latin-1").lower().split(","):
                name, _, q = part.strip().partition(";")
                if name.strip() in ("gzip", "*") and q.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00", "q=0.000"):
                    return True
    return False


def _gz_ok(msg):
    if msg.get("status", 200) in (204, 206, 304) or msg.get("status", 200) < 200:
        return False
    ctype = ""
    for k, v in msg.get("headers") or ():
        k = k.lower()
        if k in (b"content-encoding", b"content-range"):
            return False
        if k == b"content-type":
            ctype = v.decode("latin-1").lower()
    return ctype.startswith(_GZ_TYPES) and not ctype.startswith(_GZ_NEVER)


def _weak_inm(scope):
    """If-None-Match vergleicht nach RFC 9110 schwach: 'W/' vor den Tags entfernen, bevor die Endpunkte (StaticFiles,
    Profilbilder: exakter Vergleich) pruefen - so gibt ein gepacktes W/"x" weiter 304 wie "x"."""
    hs = scope.get("headers") or ()
    if not any(k == b"if-none-match" and b"W/" in v for k, v in hs):
        return scope
    fix = [(k, b",".join(p.strip()[2:] if p.strip().startswith(b"W/") else p.strip() for p in v.split(b",")) if k == b"if-none-match" else v)
           for k, v in hs]
    return dict(scope, headers=fix)


def _gz_bytes(body, level):
    z = zlib.compressobj(level, zlib.DEFLATED, 31)
    return z.compress(body) + z.flush()


async def _gz_whole(body, level):
    """Ganze Antwort packen. Grosse Antworten (Seitenbaum: mehrere MB) mit Stufe 1 (halb so viel CPU, kaum groesser)
    und in einem Thread - zlib gibt dabei den GIL frei, die Ereignisschleife bleibt fuer andere Anfragen frei."""
    if len(body) < 65536:
        return _gz_bytes(body, level)
    return await asyncio.to_thread(_gz_bytes, body, min(level, 1) if len(body) >= 524288 else level)


class Compress:
    """ASGI-Middleware: gzip fuer passende Antworten, wenn der Client es anbietet (Accept-Encoding)."""

    def __init__(self, app, minimum=GZIP_MIN, level=GZIP_LEVEL):
        self.app, self.minimum, self.level = app, minimum, level

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = _weak_inm(scope)
        if scope["type"] != "http" or self.level <= 0 or scope.get("method") == "HEAD" or not _gz_wanted(scope):
            return await self.app(scope, receive, send)
        st = {"start": None, "mode": None, "z": None, "buf": [], "n": 0}   # mode: None = noch offen, "plain", "gzip"

        def headers(start, length=None):
            h = [(k, v) for k, v in start.get("headers") or () if k.lower() not in (b"content-length", b"vary")]
            # gepackte Fassung ist nicht byte-gleich mit der ungepackten: ETag schwach (W/) - Caches/Proxys mischen sie
            # so nicht als identisch; If-None-Match vergleicht ohnehin schwach (s. oben, _weak_inm)
            h = [(k, b"W/" + v if k.lower() == b"etag" and not v.startswith(b"W/") else v) for k, v in h]
            vary = [v for k, v in start.get("headers") or () if k.lower() == b"vary"]
            parts = [p.strip() for v in vary for p in v.decode("latin-1").split(",") if p.strip()]
            if "accept-encoding" not in [p.lower() for p in parts]:
                parts.append("Accept-Encoding")
            h += [(b"content-encoding", b"gzip"), (b"vary", ", ".join(parts).encode("latin-1"))]
            if length is not None:
                h.append((b"content-length", str(length).encode()))
            return dict(start, headers=h)

        async def snd(msg):
            t = msg.get("type")
            if t == "http.response.start":
                st["start"] = msg
                if not _gz_ok(msg):
                    st["mode"] = "plain"
                    await send(msg)
                return
            if t != "http.response.body" and st["mode"] is None and st["start"] is not None:
                st["mode"] = "plain"                         # z. B. http.response.pathsend: unveraendert weiterreichen
                await send(st["start"])
                for b in st["buf"]:
                    await send({"type": "http.response.body", "body": b, "more_body": True})
                st["buf"] = []
            if t != "http.response.body" or st["mode"] == "plain" or st["start"] is None:
                return await send(msg)
            body, more = msg.get("body") or b"", msg.get("more_body", False)
            if st["mode"] is None:
                # Sammeln bis zum Ende oder 64 KB: kleine/ganze Antworten bekommen eine Content-Length,
                # Antworten in mehreren Stuecken (BaseHTTPMiddleware, StaticFiles) werden nicht kleinteilig komprimiert
                st["buf"].append(body)
                st["n"] += len(body)
                if more and st["n"] < 65536:
                    return
                body, st["buf"] = b"".join(st["buf"]), []
                if not more:
                    if len(body) < self.minimum:
                        st["mode"] = "plain"
                        await send(st["start"])
                        return await send({"type": "http.response.body", "body": body})
                    data = await _gz_whole(body, self.level)
                    st["mode"] = "gzip"
                    await send(headers(st["start"], len(data)))
                    return await send({"type": "http.response.body", "body": data})
                st["mode"], st["z"] = "gzip", zlib.compressobj(self.level, zlib.DEFLATED, 31)
                await send(headers(st["start"]))         # Laenge unbekannt -> chunked
            z = st["z"]
            data = z.compress(body) + (z.flush(zlib.Z_SYNC_FLUSH) if more else z.flush())
            await send({"type": "http.response.body", "body": data, "more_body": more})

        await self.app(scope, receive, snd)
        if st["mode"] is None and st["start"] is not None:   # Endpunkt endete ohne letztes Body-Stueck
            await send(st["start"])
            await send({"type": "http.response.body", "body": b"".join(st["buf"])})


SEC_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://cdn.jsdelivr.net data: blob:; "
                               "style-src 'self'; script-src 'self'; connect-src 'self'; "
                               "frame-src 'self' https://www.youtube-nocookie.com; "        # Einbetten-Block: nur YouTube ohne Cookies
                               "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


# Bughunt Sicherheit V2/V3: Kopfzeilen fuer alle Antworten (App und Admin, saga.core.SecHeaders)
# - HSTS nur hinter https, ohne includeSubDomains/preload (Selbsthoster haben oft andere Dienste unter derselben Domain);
#   abschaltbar mit HSTS=false (z. B. wenn TLS spaeter wieder wegfallen kann)
# - Permissions-Policy: Kamera/Mikrofon/Bildschirm teilen/Vollbild/Bild-im-Bild/Autoplay nur fuer Saga selbst (Anrufe,
#   Sprachnachrichten); Vollbild/Bild-im-Bild/encrypted-media zusaetzlich fuer den YouTube-Einbetten-Block
# - X-Frame-Options passend zu frame-ancestors der CSP ('none' -> DENY, 'self' -> SAMEORIGIN fuer das Peek-Fenster)
HSTS = BASE_URL.startswith("https://") and E.get("HSTS", "true").strip().lower() not in ("0", "false", "no", "off")
_YT = '"https://www.youtube-nocookie.com"'
PERMISSIONS_POLICY = ("camera=(self), microphone=(self), display-capture=(self), autoplay=(self), "
                      f"fullscreen=(self {_YT}), picture-in-picture=(self {_YT}), encrypted-media=(self {_YT}), "
                      "geolocation=(), payment=(), usb=(), serial=(), hid=(), midi=(), magnetometer=(), "
                      "xr-spatial-tracking=(), browsing-topics=()")


class SecHeaders:
    """ASGI: HSTS, Permissions-Policy und X-Frame-Options ergaenzen (vorhandene Werte eines Endpunkts bleiben)."""

    def __init__(self, app, hsts=None):
        self.app = app
        self.hsts = HSTS if hsts is None else hsts

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def snd(m):
            if m["type"] == "http.response.start":
                hs = list(m.get("headers") or [])
                have = {k.lower() for k, _ in hs}
                if self.hsts and b"strict-transport-security" not in have:
                    hs.append((b"strict-transport-security", b"max-age=31536000"))
                if b"permissions-policy" not in have:
                    hs.append((b"permissions-policy", PERMISSIONS_POLICY.encode()))
                if b"x-frame-options" not in have:
                    csp = next((v for k, v in hs if k.lower() == b"content-security-policy"), b"")
                    if b"frame-ancestors 'none'" in csp:
                        hs.append((b"x-frame-options", b"DENY"))
                    elif b"frame-ancestors 'self'" in csp:
                        hs.append((b"x-frame-options", b"SAMEORIGIN"))
                m = dict(m, headers=hs)
            await send(m)
        return await self.app(scope, receive, snd)


def _session_user(request):
    """Angemeldeter Benutzer der Sitzung - None, wenn gesperrt/geloescht oder das Passwort seitdem geaendert wurde."""
    uid = request.session.get("uid")
    if not uid:
        return None
    info = user_info(uid)
    if info is None or info["epoch"] > float(request.session.get("pwc") or 0):
        request.session.clear()
        return None
    did = request.session.get("did")
    if did is not None and not device_alive(uid, did):      # Geraet abgemeldet: dessen Sitzung endet sofort (BUG-002)
        request.session.clear()
        return None
    mark_authed(request)
    return uid


def mark_authed(request):
    """Anfrage ist angemeldet (gueltige Sitzung bzw. App-Schluessel): MediaAuthHint meldet es saga-media."""
    try:
        request.scope.setdefault("state", {})["saga_auth"] = True
    except Exception:  # noqa: BLE001 - nur ein Hinweis fuer den Leerlauf
        pass


class MediaAuthHint:
    """2.4 (Sicherheitsrunde 2, S1b): hinter saga-media den internen Antwortkopf X-Saga-Media-Auth: 1 setzen, wenn die
    Anfrage angemeldet war. saga-media laesst die Verbindung dann laenger ruhen und entfernt den Kopf; ein beliebiger
    Cookie-/Authorization-Kopf des Clients reicht dafuer nicht mehr."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send2(msg):
            # gueltig signierte Sitzung (SessionMiddleware hat sie geprueft, ungueltige leert _session_user) oder eine
            # Pruefung im Endpunkt (Sitzung bzw. App-Schluessel, mark_authed)
            if msg["type"] == "http.response.start" and ((scope.get("state") or {}).get("saga_auth")
                                                         or (scope.get("session") or {}).get("uid")):
                msg = dict(msg, headers=[*msg.get("headers", []), (b"x-saga-media-auth", b"1")])
            await send(msg)
        return await self.app(scope, receive, send2)


DEVICE_CHECK_TTL = 5.0
_dev_seen = {}                   # (uid, did) -> Zeitpunkt der letzten erfolgreichen Pruefung


def device_alive(uid, did):
    """Gibt es die Geraete-Anmeldung dieser Sitzung noch? Kurz zwischengespeichert; forget_device() beim Abmelden."""
    key = (uid, str(did))
    now = time.time()
    if now - _dev_seen.get(key, 0) < DEVICE_CHECK_TTL:
        return True
    try:
        from db import connect
        with connect() as c:
            alive = c.execute("SELECT 1 FROM device_login WHERE id=? AND uid=?", (int(did), uid)).fetchone() is not None
    except (TypeError, ValueError, OverflowError):
        alive = False
    if alive:
        if len(_dev_seen) > 5000:
            _dev_seen.clear()
        _dev_seen[key] = now
    else:
        _dev_seen.pop(key, None)
    return alive


_TAB_RE = re.compile(r"[A-Za-z0-9_-]{8,40}")


def origin_of(request, tab=None):
    """Herkunft einer Anfrage: Geraet (App-Sitzung) bzw. Browser-Sitzung, im Web mit Tab: '<sitzung>#<tab>' (Kopf
    X-Saga-Tab bzw. beim Live-Kanal ?tab=). Live-Kanal: eigene Aenderungen gehen an die anderen Tabs/Geraete/Sitzungen
    desselben Kontos, nur nicht an den ausloesenden Tab zurueck (BUG-032); ohne Tab-Kennung an keinen Tab dieser Sitzung."""
    did = request.session.get("did")
    if did is not None:
        return f"d{did}"
    o = request.session.get("lo")             # bei der Anmeldung vergeben (_set_session)
    # aeltere Sitzungen ohne Kennung: alle zusammen wie bisher (eigene Aenderungen gehen an keine von ihnen zurueck)
    o = o if isinstance(o, str) else "s:" + str(request.session.get("uid"))
    tab = tab if tab is not None else request.headers.get("x-saga-tab")
    return f"{o}#{tab}" if tab and _TAB_RE.fullmatch(tab) else o


_forget_hooks = []


def on_forget_device(fn):
    """fn(uid, did) beim Abmelden eines Geraets (2.5: eigene Push-Verbindung sofort schliessen)."""
    if fn not in _forget_hooks:
        _forget_hooks.append(fn)


def forget_device(uid, did=None):
    """Zwischenspeicher fuer abgemeldete Geraete leeren (did=None: alle Geraete des Kontos)."""
    for k in [k for k in _dev_seen if k[0] == uid and (did is None or k[1] == str(did))]:
        _dev_seen.pop(k, None)
    for fn in _forget_hooks:
        try:
            fn(uid, did)
        except Exception:  # noqa: BLE001
            pass


def _api_guard(request):
    """Session + Custom-Header (erzwingt CORS-Preflight -> kein Cross-Site-Request)."""
    if request.headers.get("x-saga") != "1" and request.headers.get("x-hlidskjalf") != "1":   # x-hlidskjalf: aeltere Apps
        return None
    return _session_user(request)


def _set_session(request, uid, name, keep=None):
    info = user_info(uid) or {}
    request.session.clear()
    # since: Beginn der Sitzung (2.4, M3b: danach angelegte Faktoren bestaetigen in dieser Sitzung nichts)
    request.session.update(uid=uid, name=name or uid, pwc=info.get("epoch") or 0, lo=secrets.token_hex(6),
                           since=round(time.time(), 3), **(keep or {}))


def _no_const(name):
    raise ValueError(f"JSON: {name} nicht erlaubt")


def clean_json(v, depth=0):
    """Eingaben zentral bereinigen (BUG-007): NUL weg, einzelne Surrogate -> U+FFFD (auch in Schluesseln)."""
    if isinstance(v, str):
        return clean_str(v)
    if depth > 64:
        raise ValueError("JSON zu tief verschachtelt")
    if isinstance(v, list):
        return [clean_json(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {clean_str(k): clean_json(x, depth + 1) for k, x in v.items()}
    return v


def parse_json(raw):
    """JSON wie json.loads, aber ohne NaN/Infinity (-> ValueError) und mit bereinigten Texten."""
    return clean_json(json.loads(raw, parse_constant=_no_const))


async def _body(request):
    try:
        b = parse_json(await request.body())
        return b if isinstance(b, dict) else None
    except Exception:
        return None


def user_error(ex, fallback):
    """Meldung einer Ausnahme fuer die Antwort: eigene (uebersetzte) Texte ja, rohe Python-Meldungen nein (BUG-084)."""
    msg = str(ex)
    return msg if msg and i18n.is_message(msg) else fallback


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


# =====================================================================================
# Schnellere JSON-Antworten (2.2): orjson statt json.dumps fuer JSONResponse - gilt fuer alle Endpunkte (auch fastapi.
# responses.JSONResponse, dieselbe Klasse). Ausgabe wie bisher: UTF-8 ohne \u-Ersatz, ohne Leerzeichen, Schluessel in
# Einfuegereihenfolge, gleiche Zeichen-Maskierung. Einziger Unterschied in den Bytes: sehr kleine Kommazahlen
# (|x| < 0.0001) schreibt orjson als 0.00001 statt 1e-05 - derselbe Zahlenwert fuer jeden JSON-Leser. Was orjson nicht
# kann (Schluessel, die keine Zeichenketten sind; Ganzzahlen ueber 64 Bit; fremde Typen), geht unveraendert an den alten
# Weg - samt dessen Fehlern. SAGA_ORJSON=0 schaltet ab.
# =====================================================================================
def _install_orjson():
    from starlette.responses import JSONResponse as _JR
    try:
        import orjson
    except ImportError:
        return False
    if E.get("SAGA_ORJSON", "1") == "0" or getattr(_JR, "_saga_orjson", False):
        return False
    std = _JR.render

    def render(self, content):
        try:
            return orjson.dumps(content)
        except TypeError:                     # orjson.JSONEncodeError ist ein TypeError
            return std(self, content)
    _JR.render, _JR._saga_orjson, _JR._saga_std_render = render, True, std
    return True


ORJSON = _install_orjson()


# --- Anmeldenamen anderer Konten nicht zeigen (2.4, Testrunde W4) -------------------------------------------------------
# Andere Konten sehen nie den Anmeldenamen (er waere die halbe Anmeldung). Statt dessen: Anzeigename, sonst oeffentlicher
# Name, sonst ein pseudonymer Handle ('~' + 16 Zeichen, Format wie bei der Foederation). Der Handle ist hier eine
# lokale Kennung: HMAC(SECRET_KEY, Benutzername) - stabil, nicht umkehrbar, ohne Tabelle und ohne Folgen fuer die
# Foederation (der foederierte Handle in fed_handle bleibt davon getrennt). Schnittstellen liefern fuer andere lokale
# Konten diesen Handle als Kennung (Feld "username" bzw. "target"); Anfragen nehmen Handle oder - wie bisher - den
# Benutzernamen an (resolve_ref). Ausnahme: Mitglieder eines GEMEINSAMEN Chats und Anruf-Teilnehmer - Chats v1 und
# saga-call v1 binden Schluessel und Signaturen an den Anmeldenamen (Oberflaeche zeigt ihn trotzdem nicht).
HANDLE_RE = re.compile(r"~[a-z2-7]{16}")
_A32 = "abcdefghijklmnopqrstuvwxyz234567"
_REF_KEY = hashlib.sha256(b"saga-local-ref/v1\0" + SECRET.encode()).digest()
_REV = {"t": 0.0, "n": -1, "m": {}}


def local_ref(uid):
    d = hmac.new(_REF_KEY, str(uid).encode(), hashlib.sha256).digest()
    n = int.from_bytes(d[:10], "big")
    return "~" + "".join(_A32[(n >> (5 * i)) & 31] for i in range(16))


def handles(c, uids):
    """Benutzername -> pseudonymer Handle (lokale Konten)."""
    return {u: local_ref(u) for u in set(uids) if isinstance(u, str) and u and not u.startswith("@")}


def ref_of(c, uid, viewer):
    """Kennung eines Kontos fuer den Betrachter: eigenes Konto und fremde Personen ('@...') unveraendert, andere lokale
    Konten als Handle."""
    if not uid or uid == viewer or str(uid).startswith("@") or not viewer or str(viewer).startswith("@"):
        return uid                     # fremde Instanz als Betrachter: saga.federation schreibt Personen selbst um
    return local_ref(uid)


def resolve_ref(c, x):
    """Handle ('~…') -> Benutzername (aktive Konten); sonst unveraendert klein (Benutzername wie bisher, '@name@host')."""
    x = str(x or "").strip()
    if not HANDLE_RE.fullmatch(x):
        return x.lower()
    now = time.time()
    if x not in _REV["m"] or now - _REV["t"] > 60:
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if x not in _REV["m"] and (n != _REV["n"] or now - _REV["t"] > 60):
            _REV.update(t=now, n=n, m={local_ref(r[0]): r[0] for r in c.execute("SELECT username FROM users WHERE disabled=0")})
    u = _REV["m"].get(x, "")
    if u and not c.execute("SELECT 1 FROM users WHERE username=? AND disabled=0", (u,)).fetchone():
        return ""
    return u


def subtitles(c, uids, lb=None):
    """2.4 (Linux-Runde 2, N2): unterscheidbares Merkmal fuer die Unterzeile (gleichnamige Personen): oeffentlicher Name,
    wenn er nicht schon der Name ist, sonst der Handle - nie der Anmeldename. lb: labels(c, uids), falls schon da."""
    uids = sorted({u for u in uids if isinstance(u, str) and u and not u.startswith("@")})
    if not uids:
        return {}
    lb = lb if lb is not None else labels(c, uids)
    ph = ",".join("?" * len(uids))
    pub = {r[0]: r[1] for r in c.execute(f"SELECT uid, name FROM public_name WHERE uid IN ({ph})", tuple(uids))}
    out = {}
    for u in uids:
        name, ref = lb.get(u) or local_ref(u), local_ref(u)
        out[u] = pub[u] if pub.get(u) and pub[u] != name else ("" if name == ref else ref)
    return out


def username_lookup():
    """Admin-Schalter "Suche nach Anmeldename" (2.4, Sicherheitsrunde 2; Standard an): Personensuche und Eingaben
    (Chat beginnen, Mitglied hinzufuegen, Teilen) nehmen dann auch den GENAUEN Anmeldenamen eines beliebigen Kontos an.
    Aus: nur Handle, Anzeigename und oeffentlicher Name - der Anmeldename anderer laesst sich nicht erraten/pruefen."""
    return bool(auth.settings().get("search_username", True))


def legacy_sql(a="m1", b="m2"):
    """SQL-Bedingung (2.5): gemeinsamer Chat, in dem sich die Mitglieder a und b mit Anmeldenamen kennen - Chat v1 oder
    Altbestand eines umgestellten Chats (beide waren schon vor dem Umstellpunkt v3_at Mitglied). Alias ch = chat."""
    return (f"(ch.v=1 OR (ch.v3_seq IS NOT NULL AND {a}.joined<=COALESCE(ch.v3_at, 0) AND {b}.joined<=COALESCE(ch.v3_at, 0)))")


def knows(c, viewer, other):
    """Kennt viewer den Anmeldenamen von other ohnehin? Gemeinsamer Chat (v1: Mitglieder bekommen ihn fuer die
    Schluessel) oder eine eigene Freigabe an other (aus der Zeit vor 2.4 bzw. per Anmeldename angelegt). 2.5: Chats im
    Format v2 (Stufe 3, foederiert) zaehlen nicht (dort nur Handles) - ausser fuer Mitglieder des Altbestands v1
    (legacy_sql)."""
    return bool(c.execute("SELECT 1 FROM chat_member m1 JOIN chat_member m2 ON m2.chat_id=m1.chat_id JOIN chat ch ON ch.id=m1.chat_id "
                          "WHERE m1.uid=? AND m2.uid=? AND " + legacy_sql() + " LIMIT 1", (viewer, other)).fetchone() or
                c.execute("SELECT 1 FROM note_share WHERE owner_uid=? AND target_type='user' AND target=? LIMIT 1",
                          (viewer, other)).fetchone())


def resolve_input(c, viewer, x):
    """Eingabe einer Person durch viewer -> Benutzername ('' = unbekannt bzw. nicht erlaubt; beides gleich behandeln).
    Handle ('~…') und '@name@host' wie resolve_ref; ein Anmeldename nur, wenn es der eigene ist, viewer die Person schon
    kennt (knows) oder der Admin die Suche nach Anmeldenamen erlaubt - sonst waere jede Eingabe ein Orakel dafuer, ob es
    ein Konto gibt (Sicherheitsrunde 2, H1/H2)."""
    raw = str(x or "").strip()
    u = resolve_ref(c, raw)
    if not u or u.startswith("@") or HANDLE_RE.fullmatch(raw) or u == viewer:
        return u
    return u if (username_lookup() or knows(c, viewer, u)) else ""


def labels(c, uids):
    """Benutzername -> Name zum Zeigen: Anzeigename (nicht Anmeldename/E-Mail), sonst oeffentlicher Name, sonst Handle.
    Unbekannte (geloeschte) Konten fehlen."""
    uids = sorted({u for u in uids if isinstance(u, str) and u and not u.startswith("@")})
    if not uids:
        return {}
    ph = ",".join("?" * len(uids))
    rows = c.execute(f"SELECT u.username, u.display_name, u.email, p.name FROM users u LEFT JOIN public_name p ON p.uid=u.username "
                     f"WHERE u.username IN ({ph})", tuple(uids)).fetchall()
    out = {}
    for u, disp, mail, pub in rows:
        d = (disp or "").strip()
        out[u] = d if d and d.lower() != u.lower() and d.lower() != (mail or "").strip().lower() else (pub or local_ref(u))
    return out
