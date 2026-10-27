"""Symbole der Kacheln (Dienste und Lesezeichen) als PNG fuer Web und Apps.

- Gesetztes Symbol (Name aus selfh.st/icons): einmal vom CDN holen, als PNG in STATE_DIR/svc-icons ablegen
  (GET /api/services/icon/<name>, Route in saga/pages.py).
- Lesezeichen ohne Symbol (GET /api/services/favicon/<kachel-schluessel>, nur das eigene Konto; Dienste bekommen seit
  2.5 kein automatisches Symbol): zuerst selfh.st nach dem Domainnamen raten (youtube.com -> "youtube",
  jellyfin.example.org -> "jellyfin"), sonst das Favicon der Seite (<link rel=icon|apple-touch-icon> aus dem HTML-Kopf,
  dann /favicon.ico), mit Pillow in ein PNG <= 256 px umgerechnet und je Host in STATE_DIR/site-icons abgelegt. Nicht
  Gefundenes wird einen Tag lang nicht erneut gesucht, nach 5xx/403/429 (Bot-Schutz) nur 1 h. Klappt es nicht, laden
  Browser und Apps das Favicon direkt von der Seite (icon_direct, saga.core).

SSRF: Abrufe der Seiten laufen ueber saga.netguard (DNS einmal aufloesen, jede Adresse pruefen, genau mit der
gepruefften IP verbinden) - nur oeffentliche Adressen (keine privaten, Loopback, Link-Local, CGNAT, Multicast ...),
nur http/https auf den Standardports, ohne Zugangsdaten, hoechstens 3 Weiterleitungen (jede neu geprueft), Zeit- und
Groessenlimits, nur unkomprimiert, nur Rasterbilder (SVG nie). Abrufe je Konto begrenzt (FAVICON_RATE je 10 Minuten).
Abschaltbar: FAVICONS=0 (Umgebung) oder Admin-Einstellung "Favicons laden"; ICON_CDN= (leer) schaltet selfh.st ab
(beim Raten gehen Namensteile der Hosts, auch interner wie "nextcloud", als Dateiname an das CDN).

Threadpool: Wartende Anfragen halten keinen Thread fest. Jede Sperre (je Name, je Host, hoechstens 4 Seiten- bzw.
CDN-Abrufe gleichzeitig) wird nur kurz versucht (LOCK_WAIT); ist sie belegt, kommt sofort 503 mit no-store - die
Clients zeigen den Buchstaben und fragen spaeter erneut. Faellt das CDN bzw. ein Host mit einem Netzfehler aus
(Zeitlimit, nicht erreichbar), merkt sich Saga das DOWN_TTL lang im Speicher (503, ohne erneuten Versuch).
Aufraeumen (stuendlich aus app.py, cleanup()): .missing nach 7 Tagen, Zwischendateien nach 1 h, Favicons der Seiten
nach 30 Tagen (dann neu geholt).

Nur fuer Tests: SAGA_FAVICON_TEST_PUBLIC=127.0.0.2,127.0.0.3 - diese Loopback-Adressen (nur 127.x ausser 127.0.0.1,
alles andere wird ignoriert) gelten als oeffentlich und duerfen jeden Port nutzen.
"""
import hashlib
import io
import ipaddress
import os
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse, Response

from saga.core import E, ICON_CDN, STATE_DIR, _session_user
from saga.dashboard import favicons_on, visible_tiles
from saga.netguard import Blocked, guarded_client, is_public, request_checker

router = APIRouter()

ICON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
LOCK_WAIT = 0.75                                  # Sekunden: so lange auf eine Sperre warten, dann 503
DOWN_TTL = 300.0                                  # CDN bzw. Host nach einem Netzfehler 5 Minuten lang nicht versuchen
BUSY = 503                                        # belegt oder gerade nicht erreichbar: Cache-Control no-store

_down_lock = threading.Lock()
_down = {}                                        # "cdn" bzw. Host-Schluessel -> bis (time.monotonic())


def is_down(key):
    with _down_lock:
        t = _down.get(key)
        if t is not None and t <= time.monotonic():
            _down.pop(key, None)
            t = None
        return t is not None


def mark_down(key, ttl=None):
    with _down_lock:
        now = time.monotonic()
        if len(_down) > 5000:
            for k in [k for k, v in _down.items() if v <= now]:
                _down.pop(k, None)
        _down[key] = now + (DOWN_TTL if ttl is None else ttl)


class _Try:
    """with _Try(lock) as got: - Sperre bzw. Semaphore hoechstens LOCK_WAIT Sekunden versuchen."""

    def __init__(self, lock, wait=None):
        self.lock, self.wait, self.got = lock, LOCK_WAIT if wait is None else wait, False

    def __enter__(self):
        self.got = self.lock.acquire(timeout=self.wait)
        return self.got

    def __exit__(self, *a):
        if self.got:
            self.lock.release()


def _stripe(locks, s):
    return locks[int(hashlib.sha256(s.encode()).hexdigest()[:4], 16) % len(locks)]


# =====================================================================================================
# selfh.st (gesetzter Name)
# =====================================================================================================
SVC_ICON_DIR = os.path.join(STATE_DIR, "svc-icons")
SVC_ICON_CDN = ICON_CDN                         # leer = keine Symbole aus dem Internet
_svc_locks = [threading.Lock() for _ in range(64)]   # je Name (gestreift), nicht eine Sperre fuer alle
_cdn_sem = threading.BoundedSemaphore(4)          # hoechstens 4 CDN-Abrufe gleichzeitig


def _svc_icon_fetch(name):
    if not SVC_ICON_CDN:
        return None
    with httpx.Client(timeout=httpx.Timeout(6.0, connect=3.0), follow_redirects=False) as cl:
        r = cl.get(f"{SVC_ICON_CDN}/png/{name}.png")
        if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) < 2_000_000:
            return r.content
        r = cl.get(f"{SVC_ICON_CDN}/webp/{name}.webp")
        if r.status_code == 200 and r.content[8:12] == b"WEBP" and len(r.content) < 2_000_000:
            from PIL import Image
            im = Image.open(io.BytesIO(r.content))
            im.thumbnail((256, 256))
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    return None


def svc_icon_path(name):
    """-> (pfad, None) oder (None, http-status): 404 = gibt es nicht (einen Tag gemerkt), 503 = belegt oder CDN gerade
    nicht erreichbar (DOWN_TTL im Speicher gemerkt, nicht auf der Platte). Einmal vom CDN holen und als PNG ablegen."""
    path, miss = os.path.join(SVC_ICON_DIR, name + ".png"), os.path.join(SVC_ICON_DIR, name + ".missing")
    if os.path.isfile(path):
        return path, None
    if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400:
        return None, 404
    if not SVC_ICON_CDN:
        return None, 404
    if is_down("cdn"):
        return None, BUSY
    with _Try(_stripe(_svc_locks, name)) as got:
        if not got:
            return None, BUSY
        if os.path.isfile(path):                        # waehrend des Wartens geholt
            return path, None
        if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400:
            return None, 404
        if is_down("cdn"):
            return None, BUSY
        with _Try(_cdn_sem) as slot:
            if not slot:
                return None, BUSY
            try:
                data = _svc_icon_fetch(name)
            except (httpx.HTTPError, OSError, ValueError) as ex:
                mark_down("cdn")                        # nicht auf der Platte merken: CDN nur kurz weg
                print(f"siteicons: CDN nicht erreichbar ({type(ex).__name__}), {DOWN_TTL:g} s Pause", flush=True)
                return None, BUSY
        os.makedirs(SVC_ICON_DIR, exist_ok=True)
        if not data:
            open(miss, "w").close()
            return None, 404
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path, None


# =====================================================================================================
# selfh.st nach dem Domainnamen raten
# =====================================================================================================
_GENERIC = frozenset(("www", "www2", "m", "mobile", "app", "apps", "web", "my", "home", "portal", "login", "auth", "sso",
                      "id", "account", "accounts", "api", "static", "cdn", "go", "de", "en", "es", "ru", "th"))
_SLD = frozenset(("co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go", "gv", "nom"))


def _is_ip(h):
    try:
        ipaddress.ip_address(h.strip("[]"))
        return True
    except ValueError:
        return False


def guess_names(host):
    """Kandidaten fuer selfh.st aus dem Hostnamen, hoechstens zwei: erst der erste Teil einer Subdomain
    (jellyfin.example.org -> jellyfin), dann der Name vor der Endung (www.youtube.com -> youtube, bbc.co.uk -> bbc)."""
    h = (host or "").strip().lower().rstrip(".")
    if not h or _is_ip(h):
        return []
    labels = [x for x in h.split(".") if x]
    while len(labels) > 2 and labels[0] in ("www", "www2", "m"):
        labels.pop(0)
    if len(labels) == 1:
        main, sub = labels[0], []
    elif len(labels) >= 3 and labels[-2] in _SLD and len(labels[-1]) == 2:
        main, sub = labels[-3], labels[:-3]
    else:
        main, sub = labels[-2], labels[:-2]
    out = []
    for n in ([sub[0]] if sub and sub[0] not in _GENERIC else []) + [main]:
        if ICON_RE.fullmatch(n) and n not in out and not n.startswith("xn--"):
            out.append(n)
    return out[:2]


# =====================================================================================================
# Favicon der Seite (SSRF-geschuetzt)
# =====================================================================================================
SITE_ICON_DIR = os.path.join(STATE_DIR, "site-icons")
HTML_MAX = 1 << 20                                # HTML-Kopf hoechstens 1 MB
IMG_MAX = 512 << 10                               # Bild hoechstens 512 KB
FETCH_TIMEOUT = 5.0                               # je Abruf (Verbindung 3 s)
TOTAL_TIMEOUT = 12.0                              # alle Abrufe einer Seite zusammen
MAX_CANDIDATES = 4                                # Bilder aus dem HTML-Kopf, dazu /favicon.ico
MISS_TTL = 86400                                  # kein Favicon (404 o. ae.): einen Tag nicht erneut suchen
BLOCKED_TTL = 3600                                # 5xx/403/429 (Bot-Schutz, Ueberlast): nur 1 h (Inhalt "blocked")
RATE_N = max(1, int(E.get("FAVICON_RATE", "30")))  # Seitenabrufe je Konto ...
RATE_PER = 600.0                                  # ... in 10 Minuten
PORTS = (None,)                                   # nur der Standardport des Schemas (80 bzw. 443)
_UA = "Mozilla/5.0 (compatible; Saga-Favicon/1)"


def _test_public():
    """SAGA_FAVICON_TEST_PUBLIC (nur Tests): Loopback-Adressen 127.x ausser 127.0.0.1, sonst nichts."""
    out = set()
    for x in E.get("SAGA_FAVICON_TEST_PUBLIC", "").split(","):
        try:
            a = ipaddress.ip_address(x.strip())
        except ValueError:
            continue
        if a.version == 4 and a.is_loopback and str(a) != "127.0.0.1":
            out.add(str(a))
    if out:
        print(f"siteicons: SAGA_FAVICON_TEST_PUBLIC aktiv ({', '.join(sorted(out))}) - nur fuer Tests!", flush=True)
    return frozenset(out)


TEST_PUBLIC = _test_public()


def _ip_ok(ip, _host, _port):
    return is_public(ip) or str(ip).split("%")[0] in TEST_PUBLIC


def url_ok(url, ports=PORTS):
    """Vorpruefung einer Adresse (die eigentliche Pruefung macht request_checker bei jeder Anfrage)."""
    try:
        pu = urlparse(url)
        port = pu.port
    except ValueError:
        return False
    if pu.scheme not in ("http", "https") or not pu.hostname or pu.username or pu.password:
        return False
    default = {"http": 80, "https": 443}[pu.scheme]
    if pu.hostname in TEST_PUBLIC:
        return True
    return ports is None or port in ports or port == default


class _Head(HTMLParser):
    """<link rel=icon|apple-touch-icon href sizes type> aus dem HTML-Kopf (bis </head> bzw. <body>), <base href>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.base, self.done = [], None, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "body":
            self.done = True
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href") and self.base is None:
            self.base = a["href"].strip()
        if tag != "link" or not a.get("href"):
            return
        rel = set(a.get("rel", "").lower().split())
        if not rel & {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}:
            return
        self.links.append((rel, a["href"].strip(), a.get("sizes", "").lower(), a.get("type", "").lower()))

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


def icon_candidates(html, base_url):
    """-> Bildadressen, beste zuerst (groesste sinnvolle; SVG und data: nie)."""
    p = _Head()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - kaputtes HTML: was bis dahin gefunden wurde
        pass
    base = urljoin(base_url, p.base) if p.base else base_url
    scored = []
    for i, (rel, href, sizes, typ) in enumerate(p.links):
        if "svg" in typ or href.lower().split("?")[0].endswith(".svg") or href.lower().startswith("data:"):
            continue
        size = 0
        for m in re.finditer(r"(\d{1,4})x(\d{1,4})", sizes):
            size = max(size, min(int(m.group(1)), int(m.group(2))))
        if not size:
            size = 180 if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel else 32
        score = size if size <= 512 else 512 - size / 10000      # zu gross: hinter 512, aber vor kleinen
        u = urljoin(base, href)
        if urlparse(u).scheme in ("http", "https"):
            scored.append((-score, i, u))
    out = []
    for _s, _i, u in sorted(scored):
        if u not in out:
            out.append(u)
    return out[:MAX_CANDIDATES]


def _ico_ok(data, limit=2048):
    """ICO: Pillow dekodiert beim Oeffnen sofort das groesste Bild (IcoImageFile._open -> load), die Groesse im
    ICO-Kopf (<= 256) sagt nichts ueber ein eingebettetes PNG/BMP - Abmessungen aller Eintraege vorher pruefen
    (sonst entpackt ein 400-KB-ICO ein 10000x10000-PNG, ~400 MB)."""
    n = int.from_bytes(data[4:6], "little")
    if not 0 < n <= 256 or len(data) < 6 + 16 * n:
        return False
    for i in range(n):
        off = int.from_bytes(data[6 + 16 * i + 12:6 + 16 * i + 16], "little")
        sub = data[off:off + 24]
        if sub[:8] == b"\x89PNG\r\n\x1a\n":
            if len(sub) < 24:
                return False
            w, h = int.from_bytes(sub[16:20], "big"), int.from_bytes(sub[20:24], "big")
        else:                                       # BMP (DIB): Breite, Hoehe (doppelt: Bild + Maske)
            if len(sub) < 12:
                return False
            w = abs(int.from_bytes(sub[4:8], "little", signed=True))
            h = abs(int.from_bytes(sub[8:12], "little", signed=True)) // 2
        if not (0 < w <= limit and 0 < h <= limit):
            return False
    return True


def to_png(data):
    """Rasterbild (PNG, ICO, JPEG, GIF, WebP, BMP) -> PNG <= 256 px, sonst None. SVG/HTML nie."""
    if not data or len(data) > IMG_MAX:
        return None
    head = data[:512].lstrip().lower()
    if head.startswith(b"<") or b"<svg" in head:
        return None
    if data[:4] == b"\x00\x00\x01\x00" and not _ico_ok(data):
        return None
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data), formats=("PNG", "ICO", "JPEG", "GIF", "WEBP", "BMP"))
        if im.format == "ICO":
            sizes = sorted(im.ico.sizes(), key=lambda s: s[0] * s[1])
            if not sizes:
                return None
            im = im.ico.getimage(sizes[-1])
        w, h = im.size
        if w < 1 or h < 1 or w * h > 2048 * 2048:           # Dekompressionsbombe
            return None
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((256, 256), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - kein (gueltiges) Bild
        return None


def _scheme_only(req):
    """Nur fuer Tests (ports=None): jeder Port, aber weiter nur http/https ohne Zugangsdaten."""
    if req.url.scheme not in ("http", "https") or req.url.userinfo:
        raise httpx.UnsupportedProtocol("nicht erlaubt")


def _client(ip_ok, ports):
    hook = request_checker(ports, TEST_PUBLIC) if ports is not None else _scheme_only
    return guarded_client(ip_ok, follow_redirects=True, max_redirects=3, event_hooks={"request": [hook]},
                          timeout=httpx.Timeout(FETCH_TIMEOUT, connect=3.0),
                          headers={"User-Agent": _UA, "Accept-Encoding": "identity",
                                   "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.5"})


def _blocked_status(code):
    """Antworten, hinter denen meist ein Bot-Schutz oder eine kurze Stoerung steckt (Browser bekommen das Bild)."""
    return code >= 500 or code in (403, 429)


def _get(cl, url, limit, deadline, cut=False, seen=None):
    """-> (bytes, content-type, endgueltige Adresse) oder None. Nur 200, nur unkomprimiert, hoechstens limit Bytes
    (cut=True: laengere Antworten abschneiden - fuer den HTML-Kopf; sonst verwerfen). seen: Liste, in die der
    Status-Code jeder Antwort kommt."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    with cl.stream("GET", url, timeout=httpx.Timeout(min(FETCH_TIMEOUT, left), connect=min(3.0, left))) as r:
        if seen is not None:
            seen.append(r.status_code)
        if r.status_code != 200:
            return None
        if r.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            return None
        try:
            if not cut and int(r.headers.get("content-length") or 0) > limit:
                return None
        except ValueError:
            return None
        buf = bytearray()
        for part in r.iter_raw():
            buf += part
            if len(buf) > limit:
                return (bytes(buf[:limit]), r.headers.get("content-type", ""), str(r.url)) if cut else None
            if time.monotonic() > deadline:
                return None
        return bytes(buf), r.headers.get("content-type", ""), str(r.url)


def _net_down(ex):
    """Netzfehler, nach dem der Host eine Weile nicht erneut versucht wird (statt einen Tag "nicht gefunden"):
    Zeitlimit (auch DNS, saga/netguard.py) oder Netz/Host nicht erreichbar. Abgewiesene Verbindungen, unbekannte
    Namen und gesperrte Adressen sind eine Antwort - die merkt sich .missing."""
    if isinstance(ex, httpx.TimeoutException):
        return True
    if isinstance(ex, httpx.ConnectError) and not isinstance(ex.__cause__, Blocked) and "nicht freigegeben" not in str(ex):
        s = str(ex).lower()
        return "unreachable" in s or "no route" in s or "errno 101" in s or "errno 113" in s
    return False


def fetch_favicon(url, ip_ok=_ip_ok, ports=PORTS):
    """Favicon der Seite zu url -> PNG-Bytes oder None. ip_ok/ports nur fuer Tests austauschbar (Standard: nur
    oeffentliche IPs, nur Standardports). Die Pruefung gilt fuer jede Verbindung, auch nach Weiterleitungen."""
    return _fetch(url, ip_ok, ports)[0]


def _fetch(url, ip_ok=_ip_ok, ports=PORTS):
    """-> (PNG-Bytes oder None, Netzfehler, gesperrt). Netzfehler beim Abruf der Seite selbst (_net_down): sofort
    aufhoeren, /favicon.ico desselben Hosts kaeme genauso wenig an. gesperrt: ohne Bild, und mindestens eine Antwort war
    5xx/403/429 (Bot-Schutz) - das merkt sich site_icon_path nur BLOCKED_TTL lang."""
    if not url_ok(url, ports):
        return None, False, False
    pu = urlparse(url)
    root = f"{pu.scheme}://{pu.netloc}/"
    deadline = time.monotonic() + TOTAL_TIMEOUT
    try:
        cl = _client(ip_ok, ports)
    except RuntimeError:
        return None, False, False
    seen = []
    try:
        cands, base = [], root
        try:
            got = _get(cl, root, HTML_MAX, deadline, cut=True, seen=seen)
        except (httpx.HTTPError, OSError, ValueError) as ex:
            if _net_down(ex):
                return None, True, False
            got = None
        if got:
            data, ct, base = got
            if ct.split(";")[0].strip().lower() in ("text/html", "application/xhtml+xml"):
                m = re.search(r"charset=([\w.-]+)", ct, re.I)
                try:
                    html = data.decode(m.group(1) if m else "utf-8", errors="replace")
                except LookupError:
                    html = data.decode("utf-8", errors="replace")
                cands = icon_candidates(html, base)
        urls = []
        for u in cands + [urljoin(base, "/favicon.ico"), urljoin(root, "/favicon.ico")]:
            if u not in urls:
                urls.append(u)
        for u in urls:
            if time.monotonic() > deadline:
                break
            try:
                got = _get(cl, u, IMG_MAX, deadline, seen=seen)
            except (httpx.HTTPError, OSError, ValueError):
                continue
            if got and "svg" not in got[1].lower() and not got[1].lower().startswith("text/html"):
                png = to_png(got[0])
                if png:
                    return png, False, False
        return None, False, any(_blocked_status(c) for c in seen)
    finally:
        cl.close()


# =====================================================================================================
# Kachel -> Symbol (Zwischenspeicher je Host)
# =====================================================================================================
_rl_lock = threading.Lock()
_rate = {}
_locks = [threading.Lock() for _ in range(64)]
_sem = threading.BoundedSemaphore(4)              # hoechstens 4 Seitenabrufe gleichzeitig (Threadpool nicht leeren)


def rate_ok(uid):
    now = time.time()
    with _rl_lock:
        lst = [x for x in _rate.get(uid, ()) if now - x < RATE_PER]
        if len(lst) >= RATE_N:
            _rate[uid] = lst
            return False
        lst.append(now)
        _rate[uid] = lst
        if len(_rate) > 5000:
            for k in [k for k, v in _rate.items() if not v or now - v[-1] > RATE_PER][:2500]:
                _rate.pop(k, None)
        return True


def _miss_fresh(miss):
    """Fehl-Merker noch gueltig? Gewoehnlich MISS_TTL, nach Bot-Schutz/5xx (Inhalt "blocked") nur BLOCKED_TTL."""
    try:
        age = time.time() - os.path.getmtime(miss)
    except OSError:
        return False
    if age >= MISS_TTL:
        return False
    if age < BLOCKED_TTL:
        return True
    try:
        with open(miss) as f:
            return f.read(16).strip() != "blocked"
    except OSError:
        return False


def _host_key(url):
    pu = urlparse(url)
    return hashlib.sha256(f"{pu.scheme}://{(pu.hostname or '').lower()}:{pu.port or ''}".encode()).hexdigest()[:32]


def site_icon_path(url, uid):
    """-> (pfad, None) oder (None, http-status) fuer eine Kachel ohne gesetztes Symbol: 404 kein Bild, 429 Ratenlimit,
    503 belegt bzw. CDN/Host gerade nicht erreichbar (spaeter erneut)."""
    try:
        pu = urlparse(url)
        host = (pu.hostname or "").lower()
        pu.port
    except ValueError:
        return None, 404
    if pu.scheme not in ("http", "https") or not host:
        return None, 404
    k = _host_key(url)
    base = os.path.join(SITE_ICON_DIR, k)
    png, ref, miss = base + ".png", base + ".ref", base + ".missing"
    with _Try(_locks[int(k[:2], 16) % len(_locks)]) as got:
        if not got:
            return None, BUSY                     # derselbe Host wird gerade gesucht
        if os.path.isfile(ref):                   # frueher geraten: selfh.st-Name
            try:
                name = open(ref).read().strip()
            except OSError:
                name = ""
            if ICON_RE.fullmatch(name):
                p, code = svc_icon_path(name)
                if p or code == BUSY:
                    return p, code
        if os.path.isfile(png):
            return png, None
        if _miss_fresh(miss):
            return None, 404
        cdn_busy = False
        if SVC_ICON_CDN:
            for n in guess_names(host):
                p, code = svc_icon_path(n)
                if p:
                    os.makedirs(SITE_ICON_DIR, exist_ok=True)
                    with open(ref + ".tmp", "w") as f:
                        f.write(n)
                    os.replace(ref + ".tmp", ref)
                    return p, None
                cdn_busy = cdn_busy or code == BUSY
        if not favicons_on():
            return (None, BUSY) if cdn_busy else (None, 404)   # nicht merken: beim Einschalten sofort suchen
        if not url_ok(url):
            return None, 404                      # nicht merken: der Schluessel gilt fuer alle Konten mit diesem Host,
            #                                       eine Adresse mit Zugangsdaten (user@host) darf ihn nicht sperren
        if is_down(k):
            return None, BUSY
        with _Try(_sem) as slot:                  # erst den Platz, dann das Ratenlimit (Wartende verbrauchen keins)
            if not slot:
                return None, BUSY
            if not rate_ok(uid):
                return None, 429
            try:
                data, neterr, blocked = _fetch(url)
            except Exception as ex:  # noqa: BLE001 - nie einen 500er fuer ein Symbol
                print(f"siteicons: {type(ex).__name__}", flush=True)
                data, neterr, blocked = None, False, False
        if neterr:
            mark_down(k)                          # Host gerade nicht erreichbar: nur im Speicher, nicht einen Tag lang
            return None, BUSY
        os.makedirs(SITE_ICON_DIR, exist_ok=True)
        if not data:
            if cdn_busy:                          # selfh.st kam nicht zu Wort: spaeter erneut raten
                return None, BUSY
            with open(miss, "w") as f:            # Bot-Schutz/5xx: nur BLOCKED_TTL, sonst MISS_TTL (_miss_fresh)
                f.write("blocked" if blocked else "")
            return None, 404
        with open(png + ".tmp", "wb") as f:
            f.write(data)
        os.replace(png + ".tmp", png)
        return png, None


MISS_KEEP = 7 * 86400                             # .missing: nach 7 Tagen weg (gilt ohnehin nur MISS_TTL)
SITE_KEEP = 30 * 86400                            # Favicons der Seiten: nach 30 Tagen neu holen
TMP_KEEP = 3600


def cleanup(now=None):
    """Stuendlich (app.py, _gc_loop): alte .missing, liegengebliebene .tmp und Favicons der Seiten (nach SITE_KEEP neu
    geholt). selfh.st-Symbole (svc-icons/*.png) bleiben. -> Anzahl geloeschter Dateien."""
    now = time.time() if now is None else now
    n = 0
    for d, site in ((SVC_ICON_DIR, False), (SITE_ICON_DIR, True)):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for f in names:
            p = os.path.join(d, f)
            if f.endswith(".missing"):
                keep = MISS_KEEP
            elif f.endswith(".tmp"):
                keep = TMP_KEEP
            elif site and (f.endswith(".png") or f.endswith(".ref")):
                keep = SITE_KEEP
            else:
                continue
            try:
                if now - os.path.getmtime(p) <= keep:
                    continue
                lock = _locks[int(f[:2], 16) % len(_locks)] if site and re.match(r"[0-9a-f]{2}", f) else None
                if lock is not None and not lock.acquire(timeout=LOCK_WAIT):
                    continue                      # wird gerade gesucht bzw. ausgeliefert: naechstes Mal
                try:
                    os.unlink(p)
                    n += 1
                finally:
                    if lock is not None:
                        lock.release()
            except OSError:
                pass
    return n


def _favicon(key, request):
    uid = _session_user(request)
    tile = visible_tiles(uid).get(key) if uid and len(key) <= 200 else None
    if not tile or tile.get("icon") or not tile.get("own"):     # nur Lesezeichen (2.5): Dienste nur mit festem Symbol
        return None, 404
    return site_icon_path(tile.get("url") or "", uid)


@router.get("/api/services/favicon/{key}")
async def api_service_favicon(key: str, request: Request):
    """Automatisches Symbol einer Kachel - nur fuer angemeldete Konten, die diese Kachel sehen (eigenes Lesezeichen
    bzw. freigegebener Dienst). ?h= in der Adresse (tile_icon_url) aendert sich mit dem Host.
    503 (no-store): belegt oder CDN/Host gerade nicht erreichbar - Buchstaben zeigen, spaeter erneut versuchen."""
    path, code = await run_in_threadpool(_favicon, key, request)
    if not path:
        return PlainTextResponse({404: "not found", 429: "too many requests"}.get(code, "busy"), status_code=code,
                                 headers={"Cache-Control": "private, max-age=600" if code == 404 else "no-store"})
    try:                                   # klein (<= 256 px): ganz lesen - das Aufraeumen kann die Datei sonst
        with open(path, "rb") as f:        # zwischen Pfad und Senden loeschen (500)
            data = f.read()
    except OSError:
        return PlainTextResponse("busy", status_code=503, headers={"Cache-Control": "no-store"})
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
