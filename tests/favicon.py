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
Konto. Wegwerf-Konten fav-*; raeumt auf und stellt die Einstellung wieder her."""
import base64, http.client, io, json, os, re, struct, sys, threading, time, urllib.parse
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
P = {"a": 18311, "b": 18312, "int": 18313, "c": 18314, "e": 18315, "f": 18316}
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
RU = "fav-r" + os.urandom(3).hex()   # Ratenlimit liegt im Speicher des Servers: je Lauf ein neues Konto
USERS = ("fav-a", "fav-b", RU, "fav-adm")
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
}


def handler(name):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path.split("?")[0]
            HITS[(name, path)] = HITS.get((name, path), 0) + 1
            v = SITES[name].get(path)
            if v is None:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            if isinstance(v[0], int):
                self.send_response(v[0]); self.send_header("Location", v[1]); self.send_header("Content-Length", "0"); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", v[0]); self.send_header("Content-Length", str(len(v[1])))
            self.end_headers(); self.wfile.write(v[1])
    return H


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
           "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:a00:1::1", "255.255.255.255"]
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


def main():
    srv = [serve("a", "127.0.0.2"), serve("b", "127.0.0.3"), serve("int", "127.0.0.1"), serve("c", "127.0.0.2"),
           serve("e", "127.0.0.2"), serve("f", "127.0.0.2")]
    fakecdn.start(CDN)
    cleanup()
    for u in USERS:
        auth.create_user(u, "Fav-Pass-12345", u, is_admin=u == "fav-adm")
    try:
        units()
        server()
    finally:
        cleanup()
        for s in srv:
            s.shutdown()
    print(f"\nFAVICON {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
