"""Automatische Kachel-Symbole (saga/siteicons.py): Lesezeichen/Dienste ohne gesetztes Symbol bekommen in /api/services
ein fertiges icon_url; /api/services/favicon/<schluessel> rät zuerst selfh.st nach dem Domainnamen (Fake-CDN
tests/fakecdn.py, ICON_CDN=http://127.0.0.1:18310 im Test-Server) und holt sonst das Favicon der Seite.

Der Test-Server laeuft mit SAGA_FAVICON_TEST_PUBLIC=127.0.0.2,127.0.0.3: nur diese Loopback-Adressen gelten dort als
oeffentlich (beliebiger Port) - dort laufen die Fake-Seiten. 127.0.0.1 (interner Dienst) bleibt gesperrt.

Bausteine ohne Server: Domain-Raten, Adress-/Port-Pruefung, gesperrte IP-Bereiche, Testvariable nur fuer 127.x (nicht
127.0.0.1), Abruf mit IP-Sperre (auch nach Weiterleitung), Auswahl im HTML-Kopf, SVG/zu gross verworfen, ICO -> PNG.
Ueber den Server: icon_url in /api/services und /api/bookmarks, Favicon aus dem HTML-Kopf (groesstes Bild, <= 256 px),
/favicon.ico (ICO), Zwischenspeicher, nur fuer wer die Kachel sieht, SSRF (127.0.0.1, 10.x, 169.254.x, ::1,
Weiterleitung auf intern), nur SVG -> kein Bild, Raten von selfh.st, Admin-Schalter "Favicons laden", Ratenlimit je
Konto. Threadpool: langsame Seiten (127.0.0.2:18350-18397, antworten erst nach 7 s) belegen hoechstens die 4
Abruf-Plaetze; weitere Anfragen bekommen nach kurzem Warten 503 (Cache-Control: no-store), der Server antwortet weiter;
danach ist der Host einige Minuten als "weg" gemerkt (503 sofort, kein erneuter Abruf, kein .missing). CDN nicht
erreichbar -> 503 und "weg" gemerkt. Aufraeumen alter .missing/.tmp/Favicons. DNS mit Zeitlimit (saga/netguard.py).
2.5 (nur Lesezeichen): Dienste ohne Symbol bekommen kein automatisches Symbol (icon_url leer, Favicon-Adresse 404, kein
icon_direct); Lesezeichen bekommen icon_direct (hoechstens 3 Adressen auf ihrem Host, http/https wie das Lesezeichen);
Bot-Schutz/5xx wird nur 1 h gemerkt (404 ohne Favicon 1 Tag); die CSP von Dashboard, Seiten und Chats erweitert img-src um
genau die Origins der eigenen Lesezeichen (Schalter aus: nichts). Der Rueckfall im Browser: tests/favicon_web.js.
Wegwerf-Konten fav-*; raeumt auf und stellt die Einstellung wieder her."""
import base64, http.client, io, json, os, re, struct, sys, threading, time, urllib.parse, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["SAGA_FAVICON_TEST_PUBLIC"] = "127.0.0.2,127.0.0.3,127.0.0.1,10.0.0.1"   # 127.0.0.1/10.x muessen ignoriert werden
from itsdangerous import TimestampSigner  # noqa: E402
from PIL import Image  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakecdn  # noqa: E402
from saga import siteicons as SI  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
CDN = os.environ.get("ICON_CDN", "http://127.0.0.1:18310")
P = {"a": 18311, "b": 18312, "int": 18313, "c": 18314, "e": 18315, "f": 18316, "g": 18317, "h": 18318}
SLOW = __import__("random").SystemRandom().sample(range(18350, 18398), 6)   # der Merker "weg" haelt 5 Minuten im
#                                                    Server: je Lauf zufaellige Ports (18350-18397) fuer die langsamen Seiten
SLOW_S = 7.0                                         # > FETCH_TIMEOUT (5 s): der Abruf laeuft ins Zeitlimit
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
RU = "fav-r" + os.urandom(3).hex()   # Ratenlimit liegt im Speicher des Servers: je Lauf ein neues Konto
RS = "fav-s" + os.urandom(3).hex()   # Threadpool-Test: eigenes Konto (eigenes Ratenlimit)
USERS = ("fav-a", "fav-b", RU, RS, "fav-adm")
res = []
HITS = {}                        # (server, pfad) -> Anzahl


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, user=None, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST}
    if user:
        h["Cookie"] = "saga=" + ck(user); h["X-Saga"] = "1"
    raw = None
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read(); hd = dict(r.getheaders()); c.close()
    if "json" in hd.get("content-type", ""):
        data = json.loads(data)
    return r.status, hd, data


def png(rgb, size):
    b = io.BytesIO(); Image.new("RGBA", (size, size), rgb + (255,)).save(b, "PNG"); return b.getvalue()


def ico(sizes):
    b = io.BytesIO(); Image.new("RGBA", (max(sizes), max(sizes)), (0, 200, 0, 255)).save(b, "ICO", sizes=[(s, s) for s in sizes])
    return b.getvalue()


SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64"/></svg>'
SITES = {
    "a": {"/": ("text/html; charset=utf-8",
                b'<!doctype html><html><head><title>A</title><link rel="icon" href="/small.png" sizes="16x16">'
                b'<link rel="icon" type="image/svg+xml" href="/i.svg"><link rel="apple-touch-icon" href="/touch.png">'
                b'</head><body><link rel="icon" href="/body.png"></body></html>'),
          "/small.png": ("image/png", png((0, 0, 255), 16)), "/touch.png": ("image/png", png((255, 0, 0), 300)),
          "/i.svg": ("image/svg+xml", SVG), "/body.png": ("image/png", png((1, 1, 1), 64))},
    "b": {"/": (302, "http://127.0.0.1:%d/" % P["int"])},
    "int": {"/": ("text/html", b'<html><head><link rel="icon" href="/x.png"></head></html>'), "/x.png": ("image/png", png((9, 9, 9), 32)),
            "/favicon.ico": ("image/x-icon", ico([16, 32]))},
    "c": {"/": ("text/html", b"<html><head><title>C</title></head><body>nichts</body></html>"),
          "/favicon.ico": ("image/vnd.microsoft.icon", ico([16, 48]))},
    "e": {"/": ("text/html", b'<html><head><link rel="icon" href="/x.svg"></head></html>'), "/x.svg": ("image/svg+xml", SVG),
          "/favicon.ico": ("image/x-icon", SVG)},
    "f": {"/": ("text/html", b'<html><head><link rel="shortcut icon" href="/f.png"></head></html>'), "/f.png": ("image/png", png((5, 6, 7), 64))},
    "g": {"*": 503},                                           # Bot-Schutz: jeder Abruf des Servers 503
    "h": {"/": ("text/html", b"<html><head><title>H</title></head></html>")},   # kein Favicon (404)
}


def handler(name):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            HITS[(name, path)] = HITS.get((name, path), 0) + 1
            if "*" in SITES[name]:
                self.send_response(SITES[name]["*"]); self.send_header("Content-Length", "0"); self.end_headers(); return
            v = SITES[name].get(path)
            if v is None:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            if isinstance(v[0], int):
                self.send_response(v[0]); self.send_header("Location", v[1]); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", v[0]); self.send_header("Content-Length", str(len(v[1])))
            self.end_headers(); self.wfile.write(v[1])
    return H


class Slow(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        port = self.server.server_address[1]
        HITS[("slow", port)] = HITS.get(("slow", port), 0) + 1
        time.sleep(SLOW_S)
        try:
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", "13")
            self.end_headers(); self.wfile.write(b"<html></html>")
        except OSError:                              # Client hat nach seinem Zeitlimit aufgelegt
            pass


def serve_slow(port):
    srv = ThreadingHTTPServer(("127.0.0.2", port), Slow)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def serve(name, ip):
    srv = ThreadingHTTPServer((ip, P[name]), handler(name))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def hits(name):
    return sum(v for (n, _p), v in HITS.items() if n == name)


def bm(user, url, icon=""):
    st, _h, d = req("POST", "/api/bookmarks", user, {"name": url[:40], "url": url, "icon": icon, "category": "Fav-Test"})
    assert st == 200, (st, d)
    return d["id"] if isinstance(d, dict) and "id" in d else d


def tiles(user):
    st, _h, d = req("GET", "/api/services", user)
    return {s["key"]: s for s in d} if st == 200 else {}


def icon_of(user, url):
    """Neues Lesezeichen ohne Symbol -> (status, headers, bytes) seines icon_url."""
    bid = bm(user, url)
    t = tiles(user).get(f"bm{bid}")
    if not t or not t.get("icon_url"):
        return None, {}, b""
    return req("GET", t["icon_url"], user)


def size_of(data):
    try:
        return Image.open(io.BytesIO(data)).size
    except Exception:
        return None


def cleanup():
    import shutil
    shutil.rmtree(SI.SITE_ICON_DIR, ignore_errors=True)       # Zwischenspeicher je Host (gleiches STATE_DIR wie der Server)
    from saga.userdata import delete_user_data
    for u in USERS:
        try:
            delete_user_data(u)
        except Exception:
            pass
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username = ANY(?)", (list(USERS),))


def units():
    print("--- Bausteine ohne Server")
    ok("Testvariable: nur 127.x ausser 127.0.0.1 (10.x und 127.0.0.1 ignoriert)", SI.TEST_PUBLIC == {"127.0.0.2", "127.0.0.3"})
    g = {h: SI.guess_names(h) for h in ("youtube.com", "www.youtube.com", "github.com", "jellyfin.example.org", "bbc.co.uk",
                                         "www.app.example.com", "10.1.2.3", "[::1]", "nextcloud", "login.example.org")}
    ok(f"Raten: {g}", g["youtube.com"] == ["youtube"] and g["www.youtube.com"] == ["youtube"] and g["github.com"] == ["github"]
       and g["jellyfin.example.org"] == ["jellyfin", "example"] and g["bbc.co.uk"] == ["bbc"] and g["10.1.2.3"] == []
       and g["[::1]"] == [] and g["nextcloud"] == ["nextcloud"] and g["login.example.org"] == ["example"]
       and g["www.app.example.com"] == ["example"])
    ok("Adressen: nur http/https, Standardport, ohne Zugangsdaten",
       SI.url_ok("https://example.com/") and SI.url_ok("http://example.com:80/x") and not SI.url_ok("https://example.com:8443/")
       and not SI.url_ok("http://example.com:443/") and not SI.url_ok("ftp://example.com/") and not SI.url_ok("http://u:p@example.com/")
       and not SI.url_ok("file:///etc/passwd") and not SI.url_ok("http://127.0.0.1:8099/") and SI.url_ok("http://127.0.0.2:1234/"))
    bad = ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1", "224.0.0.1", "0.0.0.0", "239.1.2.3", "ff0e::1",
           "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::127.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1", "255.255.255.255"]
    ok("Gesperrte Bereiche (privat, Loopback, Link-Local, CGNAT, Multicast, eingebettet)", not any(SI._ip_ok(i, "h", 80) for i in bad))
    ok("Oeffentlich und Test-Adresse erlaubt", SI._ip_ok("8.8.8.8", "h", 80) and SI._ip_ok("2606:4700::1111", "h", 443)
       and SI._ip_ok("127.0.0.2", "h", 1))
    n0 = hits("int")
    r1 = SI.fetch_favicon("http://127.0.0.1:%d/" % P["int"], ports=None)        # jeder Port: nur die IP-Sperre greift
    ok("Abruf auf 127.0.0.1 gesperrt (kein Zugriff, auch ohne Port-Sperre)", r1 is None and hits("int") == n0)
    r2 = SI.fetch_favicon("http://127.0.0.1:%d/" % P["int"], ip_ok=lambda *a: True, ports=None)
    ok("Gegenprobe: mit erlaubter IP kommt das Bild", r2 is not None and hits("int") > n0)
    n0 = hits("int")
    r3 = SI.fetch_favicon("http://127.0.0.3:%d/" % P["b"], ports=None)
    ok("Weiterleitung auf 127.0.0.1 gesperrt", r3 is None and hits("int") == n0 and hits("b") >= 1)
    c = SI.icon_candidates(SITES["a"]["/"][1].decode(), "http://x.example/sub/")
    ok(f"HTML-Kopf: groesstes zuerst, ohne SVG, nichts aus dem body ({c})",
       c == ["http://x.example/touch.png", "http://x.example/small.png"])
    c2 = SI.icon_candidates('<head><base href="/s/"><link rel="icon" href="data:image/png;base64,AAAA">'
                            '<link rel="ICON shortcut" href="f.ico" sizes="48x48 16x16"></head>', "https://y.example/a/b")
    ok(f"<base>, data: verworfen, sizes ({c2})", c2 == ["https://y.example/s/f.ico"])
    ok("SVG nie, auch als image/png getarnt", SI.to_png(SVG) is None and SI.to_png(b"  <svg></svg>") is None)
    big = os.urandom(SI.IMG_MAX + 10)
    ok("Zu gross verworfen", SI.to_png(big) is None)
    p = SI.to_png(ico([16, 48]))
    ok(f"ICO -> PNG mit groesster Stufe ({size_of(p)})", p and p[:8] == b"\x89PNG\r\n\x1a\n" and size_of(p) == (48, 48))
    p = SI.to_png(png((1, 2, 3), 400))
    ok(f"Grosses PNG auf 256 px ({size_of(p)})", size_of(p) == (256, 256))
    # Dekompressionsbombe: kleines PNG mit riesiger Angabe
    hdr = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 30000, 30000, 8, 6, 0, 0, 0)
    ok("Riesige Bildmasse verworfen", SI.to_png(hdr + b"\x00" * 64) is None)
    # ICO mit eingebettetem Riesen-PNG (Kopf sagt 256x256): Pillow wuerde beim Oeffnen alles entpacken
    W = 4000
    co = zlib.compressobj(9); idat = b"".join(co.compress(b"\x00" * (W * 4 + 1)) for _ in range(W)) + co.flush()
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    bigpng = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, W, 8, 6, 0, 0, 0)) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    bomb = struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(bigpng), 22) + bigpng
    t0 = time.monotonic(); r = SI.to_png(bomb)
    ok(f"ICO mit eingebettetem {W}x{W}-PNG verworfen, ohne zu entpacken ({time.monotonic() - t0:.2f} s)",
       len(bomb) < SI.IMG_MAX and r is None and time.monotonic() - t0 < 0.1)
    # Adresse mit Zugangsdaten: kein Abruf und keine Sperre des Hosts fuer andere Konten
    h = "fav-ui" + os.urandom(3).hex() + ".invalid"
    r = SI.site_icon_path(f"http://user@{h}/", "fav-a")
    ok("user@host: 404 ohne Fehl-Merker fuer den Host", r == (None, 404)
       and not os.path.exists(os.path.join(SI.SITE_ICON_DIR, SI._host_key(f"http://{h}/") + ".missing")))


def units_busy():
    print("--- Bausteine: CDN weg, Sperren mit Zeitlimit, Aufraeumen, DNS-Zeitlimit")
    import socket as _so
    from saga import netguard as NG
    old = SI.SVC_ICON_CDN
    nm = "favtest" + os.urandom(3).hex()
    try:
        SI._down.clear()
        SI.SVC_ICON_CDN = "http://127.0.0.2:9"                     # nichts hoert: Verbindung abgewiesen
        t0 = time.monotonic(); r1 = SI.svc_icon_path(nm)
        ok(f"CDN nicht erreichbar -> 503, als weg gemerkt ({r1})", r1 == (None, 503) and SI.is_down("cdn")
           and not os.path.exists(os.path.join(SI.SVC_ICON_DIR, nm + ".missing")))
        SI.SVC_ICON_CDN = CDN                                       # CDN wieder da - der Merker greift trotzdem
        n0 = len(fakecdn.HITS)
        t0 = time.monotonic(); r2 = SI.svc_icon_path(nm)
        ok(f"Merker 'weg': 503 sofort, kein Abruf ({r2}, {time.monotonic() - t0:.3f} s)",
           r2 == (None, 503) and len(fakecdn.HITS) == n0 and time.monotonic() - t0 < 0.1)
        SI._down.clear()
        r3 = SI.svc_icon_path(nm)
        ok(f"Nach Ablauf wieder gefragt: unbekannter Name 404 ({r3})", r3 == (None, 404) and len(fakecdn.HITS) > n0)
        # Sperre je Name belegt -> 503 nach LOCK_WAIT, andere Namen gehen weiter
        lk = SI._stripe(SI._svc_locks, nm + "x")
        lk.acquire()
        try:
            t0 = time.monotonic(); r4 = SI.svc_icon_path(nm + "x"); dt = time.monotonic() - t0
            other = next(n for n in ("github", "jellyfin", "nextcloud", "youtube") if SI._stripe(SI._svc_locks, n) is not lk)
            r5 = SI.svc_icon_path(other)
        finally:
            lk.release()
        ok(f"Sperre je Name belegt -> 503 nach {dt:.2f} s, anderer Name ({other}) geht weiter",
           r4 == (None, 503) and dt < SI.LOCK_WAIT + 0.5 and r5[0])
        # Seite: Host-Sperre belegt -> 503
        u = "http://127.0.0.2:%d/" % P["f"]
        k = SI._host_key(u)
        lk = SI._locks[int(k[:2], 16) % len(SI._locks)]
        lk.acquire()
        try:
            t0 = time.monotonic(); r6 = SI.site_icon_path(u, "fav-a"); dt = time.monotonic() - t0
        finally:
            lk.release()
        ok(f"Host-Sperre belegt -> 503 nach {dt:.2f} s", r6 == (None, 503) and dt < SI.LOCK_WAIT + 0.5)
    finally:
        SI.SVC_ICON_CDN = old
        SI._down.clear()
        for f in (nm + ".missing", nm + "x.missing"):
            try:
                os.unlink(os.path.join(SI.SVC_ICON_DIR, f))
            except OSError:
                pass
    # Aufraeumen: alte .missing/.tmp und alte Favicons weg, frische und selfh.st-Symbole bleiben
    os.makedirs(SI.SITE_ICON_DIR, exist_ok=True); os.makedirs(SI.SVC_ICON_DIR, exist_ok=True)
    old_t = time.time() - 40 * 86400
    files = {"site-old.missing": (SI.SITE_ICON_DIR, "ab" + "0" * 30 + ".missing", True),
             "site-new.missing": (SI.SITE_ICON_DIR, "ac" + "0" * 30 + ".missing", False),
             "site-old.png": (SI.SITE_ICON_DIR, "ad" + "0" * 30 + ".png", True),
             "site-old.tmp": (SI.SITE_ICON_DIR, "ae" + "0" * 30 + ".png.tmp", True),
             "svc-old.missing": (SI.SVC_ICON_DIR, "favtest-alt.missing", True),
             "svc-old.png": (SI.SVC_ICON_DIR, "favtest-alt.png", False)}
    for _n, (d, f, _gone) in files.items():
        open(os.path.join(d, f), "w").close()
        if _n != "site-new.missing":
            os.utime(os.path.join(d, f), (old_t, old_t))
    SI.cleanup()
    left = {n: os.path.exists(os.path.join(d, f)) for n, (d, f, _g) in files.items()}
    ok(f"Aufraeumen: alte .missing/.tmp/Favicons weg, neue und selfh.st bleiben ({left})",
       all(left[n] != g for n, (_d, _f, g) in files.items()))
    for _n, (d, f, _g) in files.items():
        try:
            os.unlink(os.path.join(d, f))
        except OSError:
            pass
    # DNS mit Zeitlimit: haengendes getaddrinfo -> ConnectTimeout nach DNS_TIMEOUT, nicht erst nach dem Resolver
    real = _so.getaddrinfo
    def hang(host, *a, **kw):
        if host == "haengt.invalid":
            time.sleep(6); raise OSError("zu spaet")
        return real(host, *a, **kw)
    _so.getaddrinfo = hang
    old_to = NG.DNS_TIMEOUT
    NG.DNS_TIMEOUT = 1.0
    try:
        import httpx
        t0 = time.monotonic()
        try:
            with NG.guarded_client(lambda *a: True, timeout=10) as cl:
                cl.get("http://haengt.invalid/")
            got = "keine Ausnahme"
        except httpx.ConnectTimeout:
            got = "ConnectTimeout"
        except Exception as ex:  # noqa: BLE001
            got = type(ex).__name__
        dt = time.monotonic() - t0
        ok(f"DNS haengt -> {got} nach {dt:.1f} s (Zeitlimit {NG.DNS_TIMEOUT:g} s)", got == "ConnectTimeout" and dt < 2.5)
        ok("Netzfehler 'DNS-Zeitlimit' gilt als Host weg", SI._net_down(httpx.ConnectTimeout("x")))
        with NG.guarded_client(lambda *a: True, timeout=5) as cl:
            st = cl.get("http://127.0.0.1:%d/" % P["int"]).status_code
        ok(f"Normale Aufloesung weiter ({st})", st == 200)
    finally:
        _so.getaddrinfo = real
        NG.DNS_TIMEOUT = old_to


def busy():
    print("--- Threadpool: langsame Seiten, 503 statt Warten, Merker 'weg'")
    ids = [bm(RS, "http://127.0.0.2:%d/" % p) for p in SLOW]
    twin = bm(RS, "http://127.0.0.2:%d/zwei" % SLOW[0])               # derselbe Host wie der erste
    tl = tiles(RS)
    urls = [tl[f"bm{i}"]["icon_url"] for i in ids]
    out = {}

    def get(tag, path, user=RS, delay=0.0):
        time.sleep(delay)
        t0 = time.monotonic()
        try:
            st, h, _d = req("GET", path, user)
        except Exception as ex:  # noqa: BLE001
            st, h = type(ex).__name__, {}
        out[tag] = (st, h.get("cache-control", ""), time.monotonic() - t0)

    th = [threading.Thread(target=get, args=(f"s{i}", u)) for i, u in enumerate(urls)]
    th.append(threading.Thread(target=get, args=("twin", tl[f"bm{twin}"]["icon_url"], RS, 0.3)))
    th += [threading.Thread(target=get, args=(f"api{j}", "/api/version", None, 0.4 + j * 0.4)) for j in range(4)]
    th.append(threading.Thread(target=get, args=("svc", "/api/services", "fav-a", 0.6)))
    t0 = time.monotonic()
    for t in th:
        t.start()
    for t in th:
        t.join(40)
    total = time.monotonic() - t0
    tiles_res = [out.get(f"s{i}") for i in range(len(urls))] + [out.get("twin")]
    fast = [r for r in tiles_res if r and r[2] < 2.0]
    slow = [r for r in tiles_res if r and r[2] >= 2.0]
    print(f"     Kacheln: {[(r[0], round(r[2], 1)) for r in tiles_res if r]}, gesamt {total:.1f} s", flush=True)
    ok(f"Alle Kachel-Anfragen 503 mit Cache-Control no-store ({[r and r[0] for r in tiles_res]})",
       all(r and r[0] == 503 and r[1] == "no-store" for r in tiles_res))
    ok(f"Hoechstens 4 warten auf den Abruf ({len(slow)}), die anderen kommen sofort ({len(fast)} in < 2 s)",
       len(slow) <= 4 and len(fast) >= len(tiles_res) - 4 and out.get("twin", (0, 0, 9))[2] < 2.0)
    api = [out.get(f"api{j}") for j in range(4)] + [out.get("svc")]
    ok(f"Server antwortet waehrenddessen ({[(r[0], round(r[2], 2)) for r in api if r]})",
       all(r and r[0] == 200 and r[2] < 1.5 for r in api))
    ok(f"Abrufe enden mit dem Zeitlimit, nicht mit der Seite ({total:.1f} s < {SLOW_S + 3:g} s)", total < SLOW_S + 3)
    # Merker "weg": die angefragten Hosts nicht erneut abrufen, kein .missing auf der Platte
    hit0 = {p: HITS.get(("slow", p), 0) for p in SLOW}
    tried = [i for i, p in enumerate(SLOW) if hit0[p]]
    t1 = time.monotonic()
    again = [req("GET", urls[i], RS) for i in tried]
    dt = time.monotonic() - t1
    ok(f"Merker 'weg': erneut 503 sofort ({[a[0] for a in again]}, {dt:.2f} s), kein neuer Abruf",
       all(a[0] == 503 and a[1].get("cache-control") == "no-store" for a in again) and dt < 3
       and all(HITS.get(("slow", SLOW[i]), 0) == hit0[SLOW[i]] for i in tried) and 1 <= len(tried) <= 4)
    ok("Kein Fehl-Merker (.missing) fuer gerade nicht erreichbare Hosts",
       not any(os.path.exists(os.path.join(SI.SITE_ICON_DIR, SI._host_key("http://127.0.0.2:%d/" % p) + ".missing")) for p in SLOW))


def server():
    print("--- Ueber den Server")
    pa, pc = "http://127.0.0.2:%d/" % P["a"], "http://127.0.0.2:%d/" % P["c"]
    st, h, d = icon_of("fav-a", pa + "irgendwo?x=1")
    ok(f"Favicon aus dem HTML-Kopf: {st} {h.get('content-type')} {size_of(d)}",
       st == 200 and h.get("content-type") == "image/png" and size_of(d) == (256, 256))
    ok("Cache-Header privat", "private" in h.get("cache-control", "") and "max-age" in h.get("cache-control", ""))
    ok("SVG und body-Links nie abgerufen", HITS.get(("a", "/i.svg"), 0) == 0 and HITS.get(("a", "/body.png"), 0) == 0)
    n = hits("a")
    bid = bm("fav-a", pa + "zweites")
    t = tiles("fav-a")[f"bm{bid}"]
    st2, _h, d2 = req("GET", t["icon_url"], "fav-a")
    ok("Zweites Lesezeichen desselben Hosts aus dem Zwischenspeicher", st2 == 200 and d2 == d and hits("a") == n)
    ok("Anderes Konto sieht das Symbol nicht (404)", req("GET", t["icon_url"], "fav-b")[0] == 404)
    ok("Ohne Sitzung 404", req("GET", t["icon_url"])[0] == 404)
    ok("Unbekannter Schluessel 404", req("GET", "/api/services/favicon/bm999999999", "fav-a")[0] == 404)
    st, _h, lst = req("GET", "/api/bookmarks", "fav-a")
    ok("/api/bookmarks liefert icon_url", st == 200 and all(b.get("icon_url", "").startswith("/api/services/favicon/bm") for b in lst))
    bid = bm("fav-a", "https://example.org/", icon="nextcloud")
    t = tiles("fav-a")[f"bm{bid}"]
    ok(f"Gesetztes Symbol: icon_url = selfh.st ({t['icon_url']})", t["icon_url"] == "/api/services/icon/nextcloud" and t["icon"] == "nextcloud")
    ok("Automatisches Symbol nicht fuer Kacheln mit gesetztem Symbol", req("GET", f"/api/services/favicon/bm{bid}", "fav-a")[0] == 404)
    st, _h, d = icon_of("fav-a", pc)
    ok(f"/favicon.ico (ICO) -> PNG {st} {size_of(d)}", st == 200 and size_of(d) == (48, 48))
    # SSRF ueber den Server
    n = hits("int")
    for u in ("http://127.0.0.1:%d/" % P["int"], "http://127.0.0.1/", "http://10.1.2.3/", "http://169.254.169.254/",
              "http://[::1]:%d/" % P["int"], "http://localhost:%d/" % P["int"], "http://127.0.0.3:%d/" % P["b"]):
        t0 = time.time()
        st, _h, _d = icon_of("fav-a", u)
        ok(f"SSRF {u} -> {st} ({time.time() - t0:.1f} s)", st == 404 and time.time() - t0 < 8)
    ok("Interner Dienst nie erreicht (auch nicht ueber Weiterleitung)", hits("int") == n)
    st, _h, _d = icon_of("fav-a", "http://127.0.0.2:%d/" % P["e"])
    ok(f"Nur SVG -> kein Symbol ({st})", st == 404)
    # selfh.st raten
    st, _h, d = icon_of("fav-a", "https://www.youtube.com/watch?v=abc")
    ok(f"Raten: youtube.com -> selfh.st youtube ({st})", st == 200 and d == fakecdn.ICONS["youtube"])
    st, _h, d = icon_of("fav-a", "https://jellyfin.beispiel.invalid/web/")
    ok(f"Raten: jellyfin.beispiel.invalid -> jellyfin ({st})", st == 200 and d == fakecdn.ICONS["jellyfin"])
    # Admin-Schalter
    import ki as K
    st, page = K.Admin("fav-adm").go("/settings")
    m = re.search(r'<input[^>]*name="favicons"[^>]*>', page or "")
    ok(f"Admin: Schalter 'Favicons laden' (Standard an) {st}", st == 200 and m and "checked" in m.group(0) and "Favicons laden" in page)
    prev = auth.settings(fresh=True).get("favicons", True)
    try:
        auth.save_setting("favicons", False); time.sleep(WAIT)
        n = hits("f")
        bid = bm("fav-b", "http://127.0.0.2:%d/" % P["f"])
        t = tiles("fav-b")[f"bm{bid}"]
        ok("Schalter aus: icon_url bleibt (selfh.st-Raten geht weiter)", t["icon_url"].startswith("/api/services/favicon/"))
        _st, h, _d = req("GET", "/", "fav-b")
        ok(f"Schalter aus: kein icon_direct, CSP ohne Lesezeichen-Origins ({img_src(h)})",
           t.get("icon_direct") == [] and img_src(h) == BASE_IMG)
        ok("Schalter aus: kein Abruf der Seite (404)", req("GET", t["icon_url"], "fav-b")[0] == 404 and hits("f") == n)
        st, _h, d = icon_of("fav-b", "https://github.com/")
        ok(f"Schalter aus: Raten von selfh.st weiter ({st})", st == 200 and d == fakecdn.ICONS["github"])
        auth.save_setting("favicons", True); time.sleep(WAIT)
        st, _h, d = req("GET", t["icon_url"], "fav-b")
        ok(f"Schalter wieder an: sofort gesucht (kein Fehl-Cache) {st} {size_of(d)}", st == 200 and size_of(d) == (64, 64) and hits("f") > n)
    finally:
        auth.save_setting("favicons", prev)
    # Ratenlimit je Konto (unbenutzte Ports: Verbindung sofort abgelehnt, zaehlt trotzdem)
    codes = []
    for i in range(SI.RATE_N + 2):
        codes.append(icon_of(RU, "http://127.0.0.2:%d/" % (19000 + i))[0])
    ok(f"Ratenlimit: {SI.RATE_N} Abrufe, dann 429 ({codes.count(404)}x404, {codes.count(429)}x429)",
       codes[:SI.RATE_N] == [404] * SI.RATE_N and codes[SI.RATE_N:] == [429, 429])
    st, _h, _d = icon_of("fav-a", "http://127.0.0.2:%d/" % 19500)
    ok(f"Ratenlimit gilt je Konto (anderes Konto: {st})", st == 404)


BASE_IMG = {"'self'", "https://cdn.jsdelivr.net", "data:", "blob:"}


def img_src(h):
    csp = h.get("content-security-policy", "")
    m = re.search(r"img-src ([^;]*)", csp)
    return set(m.group(1).split()) if m else set()


def lesezeichen_only():
    print("--- 2.5: automatische Symbole nur fuer Lesezeichen, icon_direct, CSP, Bot-Schutz 1 h")
    from saga.core import icon_origin
    cases = {"https://user:pw@Example.org:443/x?y": "https://example.org", "http://example.org:8080/": "http://example.org:8080",
             "https://bücher.example/": "https://xn--bcher-kva.example", "http://[::1]/": None, "javascript:alert(1)": None,
             "https://a_b.example/": None, "ftp://example.org/": None, "https://example.org.:80/": "https://example.org:80"}
    got = {u: icon_origin(u) for u in cases}
    ok(f"icon_origin: Schema, Host (IDN), Port, ohne Zugangsdaten, keine IPv6/Unterstriche ({got})", got == cases)
    import ki as K
    adm = K.Admin("fav-adm")
    slug = "favsvc" + os.urandom(3).hex()
    st, _p = adm.go("/services", {"action": "save", "name": "Fav Dienst", "url": "https://www.youtube.com/", "slug": slug,
                                  "category": "Fav-Test", "everyone": "on"})
    st2, _p = adm.go("/services", {"action": "save", "name": "Fav Dienst Symbol", "url": "http://127.0.0.2:%d/" % P["f"],
                                   "slug": slug + "i", "icon": "nextcloud", "category": "Fav-Test", "everyone": "on"})
    try:
        tl = tiles("fav-a")
        svc = next((x for x in tl.values() if x["name"] == "Fav Dienst"), None)
        svi = next((x for x in tl.values() if x["name"] == "Fav Dienst Symbol"), None)
        ok(f"Dienst ohne Symbol: icon_url leer, kein icon_direct ({st} {svc})",
           svc and svc["icon_url"] == "" and "icon_direct" not in svc and not svc["own"])
        ok("Dienst ohne Symbol: kein automatisches Symbol (Favicon-Adresse 404)",
           svc and req("GET", "/api/services/favicon/" + urllib.parse.quote(svc["key"], safe=""), "fav-a")[0] == 404)
        ok(f"Dienst mit Symbol: nur das feste Symbol, kein icon_direct ({st2} {svi and svi['icon_url']})",
           svi and svi["icon_url"] == "/api/services/icon/nextcloud" and "icon_direct" not in svi)
        _s, h, html = req("GET", "/", "fav-a")
        html = html.decode() if isinstance(html, bytes) else html
        ok("Dashboard: Dienst ohne Symbol zeigt den Buchstaben (kein <img>, kein data-d)",
           re.search(r'data-g="%s"[^>]*>\s*<a[^>]*>\s*<span class="ic">F</span>' % re.escape(svc["key"]), html) is not None)
    finally:
        with connect() as c:
            sids = [r[0] for r in c.execute("SELECT id FROM service WHERE slug IN (?, ?)", (slug, slug + "i")).fetchall()]
        for sid in sids:                                            # ueber den Admin: leert den Dienste-Cache
            adm.go("/services", {"action": "delete", "id": str(sid)})
    # Lesezeichen: icon_direct
    ub = "fav-b"
    urls = ["http://127.0.0.2:%d/pfad?q=1" % P["h"], "https://beispiel.invalid:443/x", "http://www.beispiel2.invalid:8080/a"]
    ids = [bm(ub, u) for u in urls]
    tl = tiles(ub)
    want = {"http://127.0.0.2:%d" % P["h"], "https://beispiel.invalid", "http://www.beispiel2.invalid:8080"}
    d0 = tl[f"bm{ids[0]}"].get("icon_direct")
    ok(f"Lesezeichen: icon_direct = apple-touch-icon, favicon.ico auf dem Host ({d0})",
       d0 == ["http://127.0.0.2:%d/apple-touch-icon.png" % P["h"], "http://127.0.0.2:%d/favicon.ico" % P["h"]])
    allok = all(1 <= len(tl[f"bm{i}"]["icon_direct"]) <= 3 and all(
        x.startswith(urllib.parse.urlparse(u).scheme + "://" + urllib.parse.urlparse(u).hostname)
        for x in tl[f"bm{i}"]["icon_direct"]) for i, u in zip(ids, urls))
    ok("icon_direct: hoechstens 3, gleiches Schema und gleicher Host wie das Lesezeichen", allok)
    _st, _h, lst = req("GET", "/api/bookmarks", ub)
    ok("/api/bookmarks liefert icon_direct", all(isinstance(b.get("icon_direct"), list) and b["icon_direct"] for b in lst))
    # CSP: genau die Origins der eigenen Lesezeichen
    with connect() as c:
        mine = {o for (u,) in c.execute("SELECT url FROM bookmark WHERE uid=?", (ub,)).fetchall()
                for o in [__import__("saga.core", fromlist=["x"]).icon_origin(u)] if o}
    for path in ("/", "/notes", "/chats"):
        _st, h, _d = req("GET", path, ub)
        extra = img_src(h) - BASE_IMG
        ok(f"CSP {path}: img-src + genau die eigenen Lesezeichen-Origins ({len(extra)})", want <= extra and extra == mine
           and "https:" not in img_src(h) and "*" not in img_src(h))
    _st, h, _d = req("GET", "/", "fav-a")
    ok("CSP anderer Konten ohne diese Origins", not (img_src(h) & want))
    _st, h, html = req("GET", "/", ub)
    html = html.decode() if isinstance(html, bytes) else html
    ok("Dashboard: <img> mit data-d (icon_direct) und referrerpolicy=no-referrer",
       'data-d="http://127.0.0.2:%d/apple-touch-icon.png http://127.0.0.2:%d/favicon.ico"' % (P["h"], P["h"]) in html
       and 'referrerpolicy="no-referrer"' in html)
    # Bot-Schutz (503) nur 1 h merken, 404 ohne Favicon 1 Tag
    def miss(port):
        return os.path.join(SI.SITE_ICON_DIR, SI._host_key("http://127.0.0.2:%d/" % port) + ".missing")
    def older(port, sec):
        t = time.time() - sec; os.utime(miss(port), (t, t))
    n = hits("g")
    st, _h, _d = icon_of(ub, "http://127.0.0.2:%d/" % P["g"])
    content = open(miss(P["g"])).read() if os.path.exists(miss(P["g"])) else None
    ok(f"Bot-Schutz 503: 404, Fehl-Merker 'blocked' ({st}, {content!r}, {hits('g') - n} Abrufe)",
       st == 404 and content == "blocked" and hits("g") > n)
    tg = [x for x in tiles(ub).values() if x["url"] == "http://127.0.0.2:%d/" % P["g"]][0]
    n = hits("g")
    ok("Bot-Schutz: innerhalb 1 h kein neuer Abruf", req("GET", tg["icon_url"], ub)[0] == 404 and hits("g") == n)
    older(P["g"], 3700)
    req("GET", tg["icon_url"], ub)
    ok(f"Bot-Schutz: nach 1 h erneut versucht ({hits('g') - n} Abrufe)", hits("g") > n)
    th = tl[f"bm{ids[0]}"]
    st, _h, _d = req("GET", th["icon_url"], ub)
    content = open(miss(P["h"])).read() if os.path.exists(miss(P["h"])) else None
    ok(f"Kein Favicon (404): Fehl-Merker gewoehnlich ({st}, {content!r})", st == 404 and content == "")
    older(P["h"], 3700)
    n = hits("h")
    ok("Kein Favicon: nach 1 h weiter gemerkt (kein Abruf)", req("GET", th["icon_url"], ub)[0] == 404 and hits("h") == n)
    older(P["h"], SI.MISS_TTL + 10)
    req("GET", th["icon_url"], ub)
    ok("Kein Favicon: nach einem Tag erneut versucht", hits("h") > n)
    ok("Bausteine: 5xx/403/429 gelten als Bot-Schutz, 404 nicht",
       SI._blocked_status(503) and SI._blocked_status(500) and SI._blocked_status(403) and SI._blocked_status(429)
       and not SI._blocked_status(404) and not SI._blocked_status(410))


def main():
    srv = [serve("a", "127.0.0.2"), serve("b", "127.0.0.3"), serve("int", "127.0.0.1"), serve("c", "127.0.0.2"),
           serve("e", "127.0.0.2"), serve("f", "127.0.0.2"), serve("g", "127.0.0.2"), serve("h", "127.0.0.2")] + [serve_slow(p) for p in SLOW]
    fakecdn.start(CDN)
    cleanup()
    for u in USERS:
        auth.create_user(u, "Fav-Pass-12345", u, is_admin=u == "fav-adm")
    try:
        units()
        units_busy()
        server()
        lesezeichen_only()
        busy()
    finally:
        cleanup()
        for s in srv:
            s.shutdown()
    print(f"\nFAVICON {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
