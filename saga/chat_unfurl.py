"""Linkvorschau im Chat (2.5, Chat-Alltag C3, Feature unfurl1): GET /api/chat/unfurl?url=<adresse>.

Der ABSENDER-Client holt Titel/Beschreibung/Bild ueber den Server und legt die Vorschau verschluesselt in die Nachricht
(Bild als verschluesselter Anhang); Empfaenger laden nie etwas nach. Der Server merkt sich nichts (kein Zwischenspeicher, kein
Log der Adresse) und gibt nur zurueck: {url, title, desc, site, image?: {mime, w, h, b64}} oder {} (nichts Brauchbares,
Bot-Schutz, nicht erreichbar).

SSRF (saga.netguard): DNS einmal aufloesen, jede Adresse pruefen, genau mit ihr verbinden - nur oeffentliche Adressen (keine
privaten, Loopback, Link-Local, CGNAT, Multicast), nur http/https auf den Standardports, ohne Zugangsdaten, hoechstens
REDIRECTS Weiterleitungen (jede neu geprueft), Zeit- (TIMEOUT je Abruf, gesamt BUDGET) und Groessenlimit (HTML_MAX, Bild
IMG_MAX), nur text/html bzw. application/xhtml+xml, nur unkomprimiert; Vorschaubild nur PNG/JPEG/GIF/WebP, neu kodiert als
JPEG <= IMG_EDGE px in einem eigenen Prozess mit Speicher-/Zeitgrenze (wie saga.avatars). Ratenbegrenzung je Konto.
Admin-Schalter chat.unfurl (Einstellung chat_unfurl, Standard an); aus: 403.

Nur fuer Tests: SAGA_UNFURL_TEST_PUBLIC=127.0.0.2,... - diese Loopback-Adressen (nur 127.x ausser 127.0.0.1) gelten als
oeffentlich und duerfen jeden Port nutzen (wie SAGA_FAVICON_TEST_PUBLIC).
"""
import html as _html
import ipaddress
import json
import re
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import auth
from saga import chat
from saga.core import E, _bad, t
from saga.netguard import Blocked, guarded_client, is_public, request_checker

router = APIRouter()

TIMEOUT = 4.0                     # je Abruf (Verbindung + Lesen)
BUDGET = 8.0                      # Seite + Bild zusammen
HTML_MAX = 512 * 1024             # HTML: nur so viel lesen (Kopf reicht)
IMG_MAX = 2 * 1024 * 1024         # Bild: groessere verwerfen
IMG_EDGE = 400                    # Vorschaubild hoechstens so breit/hoch
REDIRECTS = 3
RATE_N, RATE_PER = 30, 60.0       # Abrufe je Konto und Minute
URL_MAX = 2000
TITLE_MAX, DESC_MAX, SITE_MAX = 200, 300, 80
PORTS = (None, 80, 443)
_UA = "Mozilla/5.0 (compatible; Saga-Linkvorschau/1)"
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_sem = threading.BoundedSemaphore(4)


def enabled():
    return bool(auth.settings().get("chat_unfurl", True))


def _test_public():
    out = set()
    for x in E.get("SAGA_UNFURL_TEST_PUBLIC", "").split(","):
        try:
            a = ipaddress.ip_address(x.strip())
        except ValueError:
            continue
        if a.version == 4 and a.is_loopback and str(a) != "127.0.0.1":
            out.add(str(a))
    if out:
        print(f"chat-unfurl: SAGA_UNFURL_TEST_PUBLIC aktiv ({', '.join(sorted(out))}) - nur fuer Tests!", flush=True)
    return frozenset(out)


TEST_PUBLIC = _test_public()


def _ip_ok(ip, _host, _port):
    return is_public(ip) or str(ip).split("%")[0] in TEST_PUBLIC


def url_ok(u):
    """Form der Adresse: http/https, Host, keine Zugangsdaten, Standardport (Testadressen: jeder Port)."""
    if not isinstance(u, str) or not u or len(u) > URL_MAX or any(ch in u for ch in "\r\n\t "):
        return False
    try:
        p = urlparse(u)
        port = p.port
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return False
    return port in PORTS or p.hostname in TEST_PUBLIC


class _Meta(HTMLParser):
    """og:/twitter:-Angaben, <title>, <meta name=description> aus dem Kopf (bis <body>)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self._in_title, self.done = {}, "", False, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "body":
            self.done = True
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            k = (a.get("property") or a.get("name") or "").strip().lower()
            if k and a.get("content") and k not in self.meta:
                self.meta[k] = a["content"]

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data):
        if self._in_title and not self.done and len(self.title) < 1000:
            self.title += data


def _clean(s, n):
    s = re.sub(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]+", " ", _html.unescape(str(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n - 1] + "…" if len(s) > n else s


def parse(raw, base):
    """HTML -> {title, desc, site, image_url} (alles Text, gekuerzt)."""
    p = _Meta()
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - kaputtes HTML: was bis dahin da ist
        pass
    m = p.meta
    title = _clean(m.get("og:title") or m.get("twitter:title") or p.title, TITLE_MAX)
    desc = _clean(m.get("og:description") or m.get("twitter:description") or m.get("description"), DESC_MAX)
    site = _clean(m.get("og:site_name"), SITE_MAX) or (urlparse(base).hostname or "")[:SITE_MAX]
    img = (m.get("og:image:secure_url") or m.get("og:image") or m.get("twitter:image") or "").strip()
    img = urljoin(base, img) if img else ""
    return {"title": title, "desc": desc, "site": site, "image_url": img if url_ok(img) else ""}


def _client():
    return guarded_client(_ip_ok, follow_redirects=True, max_redirects=REDIRECTS,
                          event_hooks={"request": [request_checker(PORTS, TEST_PUBLIC)]},
                          timeout=httpx.Timeout(TIMEOUT, connect=min(3.0, TIMEOUT)),
                          headers={"User-Agent": _UA, "Accept-Encoding": "identity",
                                   "Accept": "text/html,application/xhtml+xml;q=0.9,image/*;q=0.8"})


def _get(cl, url, limit, types, deadline, cut):
    """-> (bytes, content-type, endgueltige adresse) oder None. Nur 200, nur die erlaubten Typen, unkomprimiert."""
    left = deadline - time.monotonic()
    if left <= 0:
        return None
    with cl.stream("GET", url, timeout=httpx.Timeout(min(TIMEOUT, left), connect=min(3.0, left))) as r:
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
        if not any(ct == x or (x.endswith("/") and ct.startswith(x)) for x in types):
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
                if not cut:
                    return None
                buf = buf[:limit]
                break
            if time.monotonic() > deadline:
                return None
        return bytes(buf), r.headers.get("content-type", ""), str(r.url)


_IMG_CODE = r"""
import io, json, sys, warnings, base64
from PIL import Image, ImageOps
warnings.simplefilter("error", Image.DecompressionBombWarning)
Image.MAX_IMAGE_PIXELS = 25_000_000
try:
    edge = int(sys.argv[1])
    im = Image.open(io.BytesIO(sys.stdin.buffer.read()))
    if im.format not in ("PNG", "JPEG", "MPO", "GIF", "WEBP"):
        raise ValueError("format")
    im.seek(0); im.load()
    im = ImageOps.exif_transpose(im) or im
    im = im.convert("RGB")
    im.thumbnail((edge, edge))
    if min(im.size) < 8:
        raise ValueError("small")
    b = io.BytesIO(); im.info = {}; im.save(b, "JPEG", quality=78, optimize=True)
    print(json.dumps({"w": im.size[0], "h": im.size[1], "b64": base64.b64encode(b.getvalue()).decode()}))
except Exception as ex:
    print(json.dumps({"error": type(ex).__name__}))
"""


def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (400 << 20, 400 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))


def thumb(data):
    """Bild -> {mime, w, h, b64} (JPEG, ohne Metadaten) oder None."""
    if not data or len(data) > IMG_MAX:
        return None
    try:
        r = subprocess.run([sys.executable, "-c", _IMG_CODE, str(IMG_EDGE)], input=data, capture_output=True, timeout=15,
                           preexec_fn=_limits if sys.platform.startswith("linux") else None)
        d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0 or d.get("error") or not d.get("b64"):
        return None
    return {"mime": "image/jpeg", "w": int(d["w"]), "h": int(d["h"]), "b64": d["b64"]}


def fetch(url):
    """Vorschau zu url -> dict (leer = nichts). Netzfehler/gesperrte Adressen -> {}."""
    if not url_ok(url):
        return {}
    deadline = time.monotonic() + BUDGET
    try:
        cl = _client()
    except RuntimeError:
        return {}
    try:
        got = _get(cl, url, HTML_MAX, _HTML_TYPES, deadline, cut=True)
        if not got:
            return {}
        raw, ct, final = got
        m = re.search(r"charset=([A-Za-z0-9_.:-]{1,40})", ct or "")
        try:
            text = raw.decode(m.group(1) if m else "utf-8", errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        meta = parse(text, final)
        if not meta["title"] and not meta["desc"]:
            return {}
        out = {"url": url, "title": meta["title"], "desc": meta["desc"], "site": meta["site"]}
        if meta["image_url"]:
            try:
                g = _get(cl, meta["image_url"], IMG_MAX, ("image/png", "image/jpeg", "image/gif", "image/webp"), deadline, cut=False)
            except (httpx.HTTPError, OSError, Blocked):
                g = None
            im = thumb(g[0]) if g else None
            if im:
                out["image"] = im
        return out
    except (httpx.HTTPError, OSError, ValueError, Blocked):
        return {}
    finally:
        cl.close()


@router.get("/api/chat/unfurl")
async def api_unfurl(request: Request, url: str = ""):
    uid, err = chat._me(request)
    if err:
        return err
    if not enabled():
        return _bad(t("Linkvorschau ist auf diesem Server abgeschaltet."), 403)
    if not url_ok(url):
        return _bad(t("ungültige Angabe"))
    if not chat._rate("unfurl", uid, RATE_N, RATE_PER):
        return _bad(t("Zu viele Linkvorschauen in kurzer Zeit – bitte kurz warten."), 429)
    return chat._no_store(await run_in_threadpool(_limited, url))


def _limited(url):
    """Hoechstens 4 Abrufe gleichzeitig (im Threadpool); belegt -> leer statt warten."""
    if not _sem.acquire(timeout=0.5):
        return {}
    try:
        return fetch(url)
    finally:
        _sem.release()
