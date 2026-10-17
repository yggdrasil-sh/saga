"""Profilbilder (2.1, saga/avatars.py): Upload mit Neu-Kodierung (EXIF weg, Ausrichtung, Groessen, WebP/JPEG/PNG),
Gravatar ueber eine Attrappe statt des Internets (SHA-256, d=404, Zwischenspeicher auch fuer "kein Bild", je Person und
Admin abschaltbar), SSRF-Schutz (netguard, Groesse, Zeit), Rechte (nur angemeldet / Geraete-Schluessel / mit Erlaubnis
oeffentlich, keine Aufzaehlung), Personen-Objekte (Teilen, Suche, Kommentare, Benachrichtigungen, Chat), Bild aus SSO
(OIDC "picture" ueber mockoidc) und LDAP (jpegPhoto), Konto loeschen, Foederation (zwei eigene Instanzen, signierter
Abruf, Zwischenspeicher, neu kodiert).

Die Attrappe (Gravatar + Bild-Server) laeuft in diesem Prozess auf 127.0.0.1:18284; der Probe-Server hat dafuer
GRAVATAR_URL=http://127.0.0.1:18284/avatar/ und den Host unter "Freigegebene interne Hosts" (tools/stack-remote.sh).
Wegwerf-Konten av-*, raeumt auf. Umgebung wie die anderen Tests."""
import base64, hashlib, http.client, http.server, io, json, os, re, secrets, socketserver, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
from PIL import Image
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
FAKE_PORT = int(os.environ.get("HK_FAKE_GRAVATAR_PORT", "18284"))
res = []
GMAIL, NOMAIL = "Grav.Anna@Example.org ", "niemand@example.org"
HITS = {}                      # Pfad (ohne Query) -> Anzahl Abrufe bei der Attrappe


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n + ("" if c or info is None else f" -> {info}"), flush=True)


# --- Testbilder ---------------------------------------------------------------------------------------------------
def jpeg_with_exif(w=300, h=200, orientation=6):
    """Obere Haelfte rot, untere blau; EXIF mit Kamera, GPS-Marke und Ausrichtung (6 = 90 Grad im Uhrzeigersinn drehen)."""
    im = Image.new("RGB", (w, h), (220, 20, 20))
    im.paste((20, 20, 220), (0, h // 2, w, h))
    ex = Image.Exif()
    ex[0x010F] = "GeheimKamera"           # Make
    ex[0x0131] = "Tarnkappe 1.0"          # Software
    ex[0x0112] = orientation              # Orientation
    ex[0x8825] = {1: "N", 2: (52.0, 31.0, 12.0)}   # GPS
    b = io.BytesIO(); im.save(b, "JPEG", exif=ex, quality=90)
    return b.getvalue()


def png(w=64, h=64, color=(30, 160, 90, 255), mode="RGBA"):
    b = io.BytesIO(); Image.new(mode, (w, h), color if mode == "RGBA" else color[:3]).save(b, "PNG")
    return b.getvalue()


def bomb_png():
    """PNG-Kopf mit 30000 x 30000 Pixeln (ohne echte Daten) - muss sofort abgewiesen werden."""
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


def has_exif(data):
    im = Image.open(io.BytesIO(data))
    return bool(im.getexif()) or b"Exif" in data or b"GeheimKamera" in data or "exif" in im.info


# --- Attrappe: Gravatar und Bild-Server ---------------------------------------------------------------------------
GRAV_IMG = None
HEIC = None                    # in unit() erzeugt (pillow-heif)


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, q = self.path.partition("?")
        HITS[path] = HITS.get(path, 0) + 1
        if path.startswith("/avatar/"):
            h = path.rsplit("/", 1)[-1]
            if h == hashlib.sha256(GMAIL.strip().lower().encode()).hexdigest() and "d=404" in q:
                return self._send(200, GRAV_IMG, "image/jpeg")
            return self._send(404, b"nope", "text/plain")
        if path == "/pic/ok.jpg":
            return self._send(200, jpeg_with_exif(400, 400, 1), "image/jpeg")
        if path == "/big":
            return self._send(200, b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024), "image/png")
        if path == "/slow":
            time.sleep(14)
            return self._send(200, png(), "image/png")
        if path == "/redirect-intern":
            self.send_response(302); self.send_header("Location", "http://169.254.169.254/latest/meta-data"); self.end_headers()
            return
        if path == "/notimage":
            return self._send(200, b"<html>kein Bild</html>", "text/html")
        return self._send(404, b"nope", "text/plain")

    def _send(self, code, body, ct):
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(body))); self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass


def start_fake():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", FAKE_PORT), Fake)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- HTTP an den Probe-Server ---------------------------------------------------------------------------------------
def ck(u):
    return "saga=" + S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, user=None, body=None, raw=None, hdr=None, ctype=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=40)
    h = {"Host": HOST, "Accept-Language": "de"}
    if user:
        h["Cookie"] = ck(user)
    if body is not None:
        raw = json.dumps(body).encode(); h["Content-Type"] = "application/json"; h["X-Saga"] = "1"
    if ctype:
        h["Content-Type"] = ctype
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


class Events:
    """Live-Ereignisse (/api/events) einer Person im Hintergrund mitschreiben."""

    def __init__(self, user):
        self.c = http.client.HTTPConnection(B.hostname, B.port, timeout=60)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": ck(user)})
        self.r, self.ev = self.c.getresponse(), []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "event" in cur:
                        self.ev.append((cur["event"], json.loads(cur.get("data") or "null")))
                    cur = {}
                elif not line.startswith(":"):
                    k, _, v = line.partition(":")
                    cur[k] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001
            pass

    def wait(self, typ, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in list(self.ev):
                if e[0] == typ:
                    return e[1]
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.c.sock.shutdown(2); self.c.close()
        except Exception:  # noqa: BLE001
            pass


def api(user, m, p, body=None):
    s, _, d = req(m, p, user, body=body, hdr={"X-Saga": "1"})
    try:
        return s, json.loads(d)
    except ValueError:
        return s, d


def img(url, user=None, accept="image/webp,image/*", hdr=None, s=None):
    p = url + (("&" if "?" in url else "?") + f"s={s}" if s else "")
    h = {"Accept": accept} if accept else {}
    h.update(hdr or {})
    return req("GET", p, user, hdr=h)


def upload(user, data, ctype="image/jpeg"):
    s, _, d = req("PUT", "/api/account/avatar", user, raw=data, ctype=ctype, hdr={"X-Saga": "1"})
    try:
        return s, json.loads(d)
    except ValueError:
        return s, d


def too_big(user, n):
    """Nur die Kopfzeilen mit Content-Length n senden - der Server muss sofort 413 antworten."""
    import socket
    so = socket.create_connection((B.hostname, B.port), timeout=20)
    so.sendall((f"PUT /api/account/avatar HTTP/1.1\r\nHost: {HOST}\r\nCookie: {ck(user)}\r\nX-Saga: 1\r\n"
                f"Content-Type: image/png\r\nContent-Length: {n}\r\n\r\n").encode() + b"\x89PNG\r\n\x1a\n")
    head = so.recv(200).decode("latin-1")
    so.close()
    m = re.match(r"HTTP/1\.[01] (\d+)", head)
    return int(m.group(1)) if m else 0


def upload_multipart(user, data, name="bild.jpg", ctype="image/jpeg"):
    bd = "avtest" + secrets.token_hex(4)
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() \
        + data + f"\r\n--{bd}--\r\n".encode()
    s, _, d = req("PUT", "/api/account/avatar", user, raw=mp, ctype=f"multipart/form-data; boundary={bd}", hdr={"X-Saga": "1"})
    try:
        return s, json.loads(d)
    except ValueError:
        return s, d


def snapshot_settings():
    with connect() as c:
        return dict(c.execute("SELECT k, v FROM setting").fetchall())


def restore_settings(snap):
    with connect() as c, c:
        for k in [r[0] for r in c.execute("SELECT k FROM setting").fetchall()]:
            if k not in snap:
                c.execute("DELETE FROM setting WHERE k=?", (k,))
        for k, v in snap.items():
            c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, v))
    auth._settings_cache["v"] = None


def settings_wait():
    time.sleep(5.5)          # Einstellungen werden im Server 5 s zwischengespeichert


def cleanup():
    with connect() as c, c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'av-%'").fetchall()]
    if users:
        from saga.userdata import delete_user_data
        for u in users:
            delete_user_data(u, drop_user=True)
    with connect() as c, c:
        c.execute("DELETE FROM avatar WHERE uid LIKE 'av-%'")
        c.execute("DELETE FROM avatar_img WHERE uid LIKE 'av-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE '%av-%'")


def db1(q, args=()):
    with connect() as c:
        return c.execute(q, args).fetchall()


# =====================================================================================================================
def unit():
    """Ohne Server: Neu-Kodierung, Schluessel, SSRF/Grenzen beim Abholen (gleiche Einstellungen wie der Server)."""
    from saga import avatars as A
    enc = A.encode(jpeg_with_exif())
    ok("Neu kodiert: 3 Groessen x (WebP + JPEG)", enc is not None and sorted(enc) == sorted((n, f) for n in (64, 128, 256) for f in ("webp", "std")))
    sizes = {k: Image.open(io.BytesIO(v[1])).size for k, v in (enc or {}).items()}
    ok("Quadratisch 64/128/256", all(sz == (k[0], k[0]) for k, sz in sizes.items()), sizes)
    ok("Keine Metadaten (EXIF, Kamera, GPS) in keiner Fassung", enc and not any(has_exif(v[1]) for v in enc.values()))
    ok("Formate: WebP und JPEG (ohne Transparenz)", enc and enc[(256, "webp")][0] == "image/webp" and enc[(256, "std")][0] == "image/jpeg"
       and Image.open(io.BytesIO(enc[(256, "std")][1])).format == "JPEG")
    im = Image.open(io.BytesIO(enc[(256, "webp")][1])).convert("RGB") if enc else None
    lp, rp = (im.getpixel((20, 128)), im.getpixel((236, 128))) if im else ((0, 0, 0), (0, 0, 0))
    ok(f"EXIF-Ausrichtung angewendet (links blau, rechts rot: {lp} {rp})", lp[2] > 150 and lp[0] < 90 and rp[0] > 150 and rp[2] < 90)
    e2 = A.encode(png(40, 40, (0, 0, 0, 0)))
    ok("Transparentes PNG bleibt PNG (std)", e2 and e2[(64, "std")][0] == "image/png" and Image.open(io.BytesIO(e2[(64, "std")][1])).mode == "RGBA")
    try:                                                # HEIC (iPhone-Fotos) ueber pillow-heif
        import pillow_heif
        pillow_heif.register_heif_opener()
        src = Image.open(io.BytesIO(jpeg_with_exif(240, 160, 1)))
        hb = io.BytesIO()
        src.save(hb, "HEIF", exif=src.info.get("exif"), quality=80)
        heic = hb.getvalue()
    except Exception as ex:  # noqa: BLE001
        heic = None
        print("HEIC nicht erzeugbar:", type(ex).__name__, ex)
    e3 = A.encode(heic) if heic else None
    ok("HEIC erkannt, gelesen und ohne Metadaten neu kodiert", heic and A.is_image(heic[:16]) == "heic" and e3
       and Image.open(io.BytesIO(e3[(128, "webp")][1])).size == (128, 128) and not any(has_exif(v[1]) for v in e3.values()))
    global HEIC
    HEIC = heic
    ok("Zu klein (8x8) -> abgewiesen", A.encode(png(8, 8)) is None)
    ok("Kein Bild (HTML) -> abgewiesen", A.encode(b"<html>x</html>") is None)
    ok("Kaputtes PNG (nur Kopf) -> abgewiesen", A.encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200) is None)
    t0 = time.time()
    ok("Dekompressionsbombe (30000x30000) -> abgewiesen, schnell", A.encode(bomb_png()) is None and time.time() - t0 < 10)
    # Schluessel
    k = A.key_local(42, "av-x")
    ok("Schluessel: u<id>.<mac>, Gegenprobe", re.fullmatch(r"u42\.[A-Za-z0-9_-]{16}", k) and A._parse(k) == ("u", 42, k.split(".")[1]))
    rk = A.key_remote("@bert@b.example")
    ok("Schluessel fremder Person: geprueft beim Lesen", A._parse(rk) == ("r", "@bert@b.example")
       and A._parse(rk[:-2] + ("AA" if not rk.endswith("AA") else "BB")) is None and A._parse("r" + rk[1:].split(".")[0] + ".x") is None)
    ok("Unsinn -> kein Schluessel", A._parse("../../etc") is None and A._parse("u0.AAAAAAAAAAAAAAAA") is None)
    ok("Gravatar-Hash: SHA-256 von klein/ohne Leerzeichen", A.gravatar_hash(" A@B.de ") == hashlib.sha256(b"a@b.de").hexdigest()
       and A.gravatar_hash("") is None)
    # SSRF / Grenzen (Freigaben wie im Admin-Bereich: nur 127.0.0.1:<Attrappe> u. a.)

    def fails(u):
        try:
            A.download(u)
            return False
        except A.FetchError:
            return True
    ok("SSRF: 127.0.0.1 auf anderem Port (Saga selbst) gesperrt", fails(f"http://{B.hostname}:{B.port}/healthz"))
    ok("SSRF: Cloud-Metadaten 169.254.169.254 gesperrt", fails("http://169.254.169.254/latest/meta-data"))
    ok("SSRF: privates Netz 10.x gesperrt", fails("http://10.255.255.1/x.png"))
    ok("SSRF: Weiterleitung auf interne Adresse gesperrt", fails(f"http://127.0.0.1:{FAKE_PORT}/redirect-intern"))
    ok("Nur http(s): file:// und ftp:// abgewiesen", fails("file:///etc/passwd") and fails("ftp://example.org/x.png"))
    ok("Zu gross (3 MB) -> abgebrochen", fails(f"http://127.0.0.1:{FAKE_PORT}/big"))
    t0 = time.time()
    ok(f"Zeitlimit greift ({time.time() - t0:.1f} s)", fails(f"http://127.0.0.1:{FAKE_PORT}/slow") and time.time() - t0 < 13)
    ok("404 -> kein Bild (None), freigegebene Attrappe erreichbar", A.download(f"http://127.0.0.1:{FAKE_PORT}/nichts") is None
       and A.download(f"http://127.0.0.1:{FAKE_PORT}/pic/ok.jpg")[:3] == b"\xff\xd8\xff")


def mk(u, name, email=""):
    auth.create_user(u, None, name, email)
    return auth.get_user(u)["id"]


def main():
    global GRAV_IMG
    GRAV_IMG = jpeg_with_exif(320, 320, 1)
    cleanup()
    start_fake()
    unit()
    auth.save_setting("avatar", {"gravatar": True, "oidc_picture": False, "ldap_photo": False})
    auth.save_setting("share_enabled", True)
    auth.save_setting("chat_enabled", True)
    auth.save_setting("publish_enabled", True)
    settings_wait()
    ia = mk("av-anna", "Anna Avatar", GMAIL)
    mk("av-bert", "Bert Ohnebild", NOMAIL)
    mk("av-cora", "Cora Ohnemail", "")
    mk("av-dora", "Dora Sieht", "dora@example.org")
    ghash = hashlib.sha256(GMAIL.strip().lower().encode()).hexdigest()
    gpath = "/avatar/" + ghash

    # --- Gravatar als Standard (ueber den Server, Attrappe) ------------------------------------------------------
    s, a = api("av-anna", "GET", "/api/account/avatar")
    ok(f"Anna: Gravatar gefunden ({s}, {a.get('source') if isinstance(a, dict) else a})", s == 200 and a["source"] == "gravatar" and a["url"])
    url = a.get("url") or ""
    ok("Adresse zeigt auf Saga, nicht auf Gravatar (u<id>.<mac>)", re.fullmatch(rf"/api/avatar/u{ia}\.[A-Za-z0-9_-]{{16}}\?v=\d+", url), url)
    ok("Attrappe: genau ein Abruf mit SHA-256 und d=404", HITS.get(gpath) == 1, HITS)
    s, h, d = img(url, "av-dora")
    ok(f"Abruf (andere angemeldete Person): WebP 128 ({s} {h.get('content-type')})", s == 200 and h.get("content-type") == "image/webp"
       and Image.open(io.BytesIO(d)).size == (128, 128))
    ok("Gravatar-Bild neu kodiert (kein EXIF)", s == 200 and not has_exif(d))
    ok("Kopfzeilen: ETag, private Cache-Control, nosniff, Vary Accept", h.get("etag") and "private" in h.get("cache-control", "")
       and h.get("x-content-type-options") == "nosniff" and "Accept" in h.get("vary", ""))
    s2, _, _ = img(url, "av-dora", hdr={"If-None-Match": h.get("etag", "")})
    ok(f"If-None-Match -> 304 ({s2})", s2 == 304)
    s, h, d = img(url, "av-dora", accept="*/*", s=64)
    ok(f"Ohne WebP im Accept: JPEG 64 ({h.get('content-type')})", s == 200 and h.get("content-type") == "image/jpeg" and Image.open(io.BytesIO(d)).size == (64, 64))
    s, h, d = img(url, "av-dora", s=300)
    ok("s=300 -> groesste Stufe 256", s == 200 and Image.open(io.BytesIO(d)).size == (256, 256))
    img(url, "av-anna"); img(url, "av-dora", s=256)
    ok("Zwischenspeicher: keine weiteren Abrufe bei Gravatar", HITS.get(gpath) == 1, HITS)
    s, b = api("av-bert", "GET", "/api/account/avatar")
    bpath = "/avatar/" + hashlib.sha256(NOMAIL.encode()).hexdigest()
    ok("Bert: kein Gravatar (404) -> Initialen", s == 200 and b["source"] is None and b["url"] is None and HITS.get(bpath) == 1)
    api("av-bert", "GET", "/api/account/avatar")
    s, acc = api("av-bert", "GET", "/api/account")
    ok("Auch 'kein Bild' wird zwischengespeichert (keine neue Anfrage)", HITS.get(bpath) == 1 and acc.get("avatar") is None)
    s, cst = api("av-cora", "GET", "/api/account/avatar")
    ok("Cora ohne E-Mail: kein Gravatar, keine Anfrage", cst["source"] is None and not cst["has_email"]
       and not any(k.startswith("/avatar/") and k not in (gpath, bpath) for k in HITS))
    s, acc = api("av-anna", "GET", "/api/account")
    ok("/api/account enthaelt die Adresse", acc.get("avatar") == url)

    # --- Rechte, keine Aufzaehlung ---------------------------------------------------------------------------------
    s, _, _ = img(url)
    ok(f"Ohne Anmeldung -> 404 ({s})", s == 404)
    key = url.split("/api/avatar/")[1].split("?")[0]
    mac = key.split(".")[1]
    bad = [f"u{ia}.{mac[:-1]}{'A' if mac[-1] != 'A' else 'B'}", f"u{ia + 1}.{mac}", f"u{ia}", "u1.AAAAAAAAAAAAAAAA", "av-anna", "1",
           "..%2F..%2Fetc%2Fpasswd", "r" + base64.urlsafe_b64encode(b"@x@y.z").decode().rstrip("=") + ".AAAAAAAAAAAAAAAA"]
    codes = [img("/api/avatar/" + k, "av-dora")[0] for k in bad]
    ok(f"Falsche/geratene Schluessel -> 404 ({codes})", all(c == 404 for c in codes))
    codes = {img(f"/api/avatar/u{i}.{mac}", "av-dora")[0] for i in range(1, 60) if i != ia}
    ok(f"Durchzaehlen der Konten (andere IDs, gleicher MAC) -> nur 404 ({codes})", codes <= {404})
    s, _, d = req("GET", "/api/avatars", "av-dora")
    ok("Keine Liste aller Profilbilder", s == 404)
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?,?,?,?,?,?)",
                  ("av-dora", "av-dora", "Test-Geraet", hashlib.sha256(tok.encode()).hexdigest(), tok[:8], time.time()))
    s, _, _ = img(url, hdr={"Authorization": "Bearer " + tok})
    ok(f"App mit Geraete-Schluessel (Bearer hkd_) -> 200 ({s})", s == 200)
    s, _, _ = img(url, hdr={"Authorization": "Bearer hkd_falsch"})
    ok("Falscher Geraete-Schluessel -> 404", s == 404)
    s, st = api("av-anna", "PATCH", "/api/account/avatar", {"public": True})
    s2, h2, _ = img(url)
    ok(f"Erlaubt fuer veroeffentlichte Seiten: ohne Anmeldung sichtbar ({s2}, {h2.get('cache-control')})", s == 200 and st["public"] and s2 == 200)
    api("av-anna", "PATCH", "/api/account/avatar", {"public": False})
    ok("Erlaubnis zurueck: ohne Anmeldung wieder 404", img(url)[0] == 404)
    s, _ = api("av-anna", "PATCH", "/api/account/avatar", {"public": "ja"})
    ok("PATCH mit Unsinn -> 400", s == 400)
    ok("Ohne X-Saga kein Upload (CSRF)", req("PUT", "/api/account/avatar", "av-anna", raw=png(), ctype="image/png")[0] == 401)

    # --- Personen-Objekte ueberall --------------------------------------------------------------------------------
    s, n = api("av-anna", "POST", "/api/notes", {"title": "Avatar-Seite"})
    nid = n["id"]
    s, _ = api("av-anna", "PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": "av-dora", "role": "comment"})
    s, st = api("av-anna", "GET", f"/api/notes/{nid}/shares")
    ok("Teilen-Dialog: Freigabe an Dora mit avatar-Feld (Dora: Gravatar unbekannt -> None oder Adresse)",
       s == 200 and any(x["target"] == "av-dora" and "avatar" in x for x in st["shares"]))
    s, d = api("av-dora", "GET", f"/api/notes/{nid}")
    base = url.split("?")[0]
    same = lambda u: (u or "").split("?")[0] == base  # noqa: E731
    ok("Seite fuer Dora: Eigentuemerin mit Profilbild-Adresse", s == 200 and same(d.get("owner", {}).get("avatar")))
    s, d = api("av-dora", "GET", "/api/share/targets?q=av-an")
    ok("Personensuche (Teilen): Treffer mit avatar", any(u["username"] == "av-anna" and same(u.get("avatar")) for u in d.get("users", [])))
    s, d = api("av-dora", "GET", "/api/chat/people?q=av-an")
    ok(f"Personensuche (Chat): Treffer mit avatar ({s})", s == 200 and any(u["username"] == "av-anna" and same(u.get("avatar")) for u in d.get("users", [])))
    s, cm = api("av-anna", "POST", f"/api/notes/{nid}/comments", {"body": "Hallo mit Bild", "block_id": ""})
    ok("Kommentar: Verfasserin mit avatar", s == 200 and same(cm["comment"]["author"].get("avatar")))
    s, th = api("av-dora", "GET", f"/api/notes/{nid}/comments")
    ok("Kommentare lesen (Dora): avatar der Verfasserin", s == 200 and th["threads"] and same(th["threads"][0]["comments"][0]["author"].get("avatar")))
    s, ns = api("av-dora", "GET", "/api/notifications")
    items = ns.get("items", []) if isinstance(ns, dict) else []
    ok("Benachrichtigung: Ausloeserin mit avatar", any(same((x.get("actor") or {}).get("avatar")) for x in items), [x.get("actor") for x in items][:3])
    s, pr = api("av-dora", "POST", "/api/presence", {"note_id": nid, "editing": False})
    s, pr = api("av-anna", "POST", "/api/presence", {"note_id": nid, "editing": True})
    ok("Anwesenheit: Personen mit avatar-Feld", s == 200 and all("avatar" in u for u in pr.get("users", []))
       and any(u["username"] == "av-anna" and same(u["avatar"]) for u in pr.get("users", [])))
    s, vs = api("av-dora", "PATCH", f"/api/notes/{nid}", {"title": "Avatar-Seite (Dora)"})
    s, d = api("av-anna", "GET", f"/api/notes/{nid}")
    ok("Seite: zuletzt geaendert von (Dora) mit avatar-Feld", "avatar" in (d.get("updated_by") or {}))

    # --- Eigenes Bild: Upload, Neu-Kodierung, Grenzen, Loeschen --------------------------------------------------
    s, st = upload_multipart("av-anna", jpeg_with_exif(1200, 700))
    ok(f"Upload (multipart, JPEG mit EXIF) ({s})", s == 200 and st["source"] == "upload" and st["upload"])
    url2 = st.get("url") or ""
    ok("Neue Version in der Adresse (?v=)", url2 and url2 != url and url2.split("?")[0] == url.split("?")[0])
    s, h, d = img(url2, "av-dora", accept="*/*", s=256)
    ok("Hochgeladenes Bild: 256er JPEG ohne EXIF", s == 200 and Image.open(io.BytesIO(d)).size == (256, 256) and not has_exif(d))
    with connect() as c:
        raw = [bytes(r[0]) for r in c.execute("SELECT data FROM avatar_img WHERE uid='av-anna' AND src='upload'").fetchall()]
    ok("Gespeichert nur neu kodiert (6 Fassungen, keine mit EXIF/Kamera)", len(raw) == 6 and not any(b"GeheimKamera" in x or b"Exif" in x for x in raw))
    s, h, _ = img(url2, "av-dora")
    ok("Passende Version: lange zwischenspeicherbar", "max-age=86400" in h.get("cache-control", ""))
    s, h, _ = img(url, "av-dora")
    ok("Alte Version in der Adresse: nur kurz", "max-age=600" in h.get("cache-control", ""))
    s, st = upload("av-anna", png(200, 200), "image/png")
    ok(f"Upload als Rohinhalt (Apps: Content-Type image/png) ({s})", s == 200 and st["source"] == "upload")
    # Bughunt 2 (server, S4): dasselbe Bild noch einmal -> Version bleibt (Clients laden nichts neu)
    s, st2 = upload("av-anna", png(200, 200), "image/png")
    ok(f"Dasselbe Bild noch einmal: Adresse/Version unveraendert ({st.get('url')} -> {st2.get('url')})", s == 200 and st2.get("url") == st.get("url"))
    # Bughunt 2 (linux, S4): Live-Ereignis avatar.changed an die Person selbst und alle, mit denen sie einen Chat teilt
    with connect() as c, c:
        chid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm', 'av-anna|av-dora', 'av-anna', ?) RETURNING id",
                         (time.time(),)).fetchone()[0]
        c.executemany("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", [(chid, "av-anna", time.time()), (chid, "av-dora", time.time())])
    sd, sa = Events("av-dora"), Events("av-anna")
    time.sleep(0.8)
    s, st3 = upload("av-anna", png(180, 180, (200, 40, 40, 255)), "image/png")
    ed, ea = sd.wait("avatar.changed"), sa.wait("avatar.changed")
    ok(f"Neues Bild: avatar.changed live an Chat-Partnerin und eigene Tabs mit neuer Adresse ({ed}, {ea})",
       ed and ea and ed.get("user") == "av-anna" and ed.get("avatar") == st3.get("url") != st.get("url") and ea.get("avatar") == st3.get("url"))
    m = len(sd.ev)
    upload("av-anna", png(180, 180, (200, 40, 40, 255)), "image/png")
    time.sleep(1.2)
    ok("... dasselbe Bild noch einmal: kein Ereignis", not [e for e in sd.ev[m:] if e[0] == "avatar.changed"])
    sd.close(); sa.close()
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE id=?", (chid,))
    s, st = upload("av-anna", HEIC or b"", "image/heic")
    ok(f"Upload HEIC (iPhone, ungeschnitten -> mittig zugeschnitten) ({s})", s == 200 and st["source"] == "upload"
       and img(st.get("url") or "/x", "av-dora", s=128)[0] == 200)
    s = too_big("av-anna", 6 * 1024 * 1024)
    ok(f"Zu gross (6 MB angekuendigt) -> 413 ohne den Inhalt zu lesen ({s})", s == 413)
    s, e = upload("av-anna", b"GIF89a-kein-bild", "image/gif")
    ok(f"Kaputtes Bild -> 415 ({s})", s == 415)
    s, e = upload("av-anna", b"hallo welt", "text/plain")
    ok(f"Falscher Typ -> 415 ({s})", s == 415)
    s, e = upload_multipart("av-anna", b"%PDF-1.4 kein bild", "x.pdf", "application/pdf")
    ok(f"PDF als Bild -> 415 ({s})", s == 415)
    s, st = api("av-anna", "DELETE", "/api/account/avatar")
    ok("Eigenes Bild loeschen -> wieder Gravatar", s == 200 and st["source"] == "gravatar" and not st["upload"]
       and not db1("SELECT 1 FROM avatar_img WHERE uid='av-anna' AND src='upload'"))

    # --- Gravatar abschalten (Person, Admin) -----------------------------------------------------------------------
    s, st = api("av-anna", "PATCH", "/api/account/avatar", {"gravatar": False})
    ok("Person schaltet Gravatar ab -> Initialen, Zwischenspeicher sofort weg",
       s == 200 and st["source"] is None and st["url"] is None and not db1("SELECT 1 FROM avatar_img WHERE uid='av-anna' AND src='gravatar'"))
    s, d = api("av-dora", "GET", f"/api/notes/{nid}")
    ok("... auch in Personen-Objekten (avatar None)", d.get("owner", {}).get("avatar") is None)
    api("av-anna", "PATCH", "/api/account/avatar", {"gravatar": True})
    auth.save_setting("avatar", {"gravatar": False, "oidc_picture": False, "ldap_photo": False})
    settings_wait()
    s, st = api("av-anna", "GET", "/api/account/avatar")
    ok("Admin schaltet Gravatar ab -> keine Adresse, kein Abruf", st["source"] is None and not st["gravatar_enabled"] and HITS.get(gpath) == 2)
    auth.save_setting("avatar", {"gravatar": True, "oidc_picture": False, "ldap_photo": False})
    settings_wait()
    s, st = api("av-anna", "GET", "/api/account/avatar")
    ok("Admin wieder an -> Gravatar", st["source"] == "gravatar")
    return nid, url


def published(nid, url):
    """Veroeffentlichte Seite: Profilbild nur mit Erlaubnis der Person (Standard aus)."""
    from saga import publish
    publish.set_name("av-anna", "av-anna-pub")
    s, d = api("av-anna", "PUT", f"/api/notes/{nid}/publish", {"slug": "avatar-test", "name": "av-anna-pub"})
    ok(f"Seite veroeffentlicht ({s})", s == 200)
    s, _, html = req("GET", "/@av-anna-pub/avatar-test")
    html = html.decode("utf-8", "replace")
    ok("Oeffentliche Seite ohne Erlaubnis: kein Profilbild", s == 200 and "/api/avatar/" not in html)
    api("av-anna", "PATCH", "/api/account/avatar", {"public": True})
    s, _, html = req("GET", "/@av-anna-pub/avatar-test")
    m = re.search(r'src="(/api/avatar/[^"]+)"', html.decode("utf-8", "replace"))
    ok("Mit Erlaubnis: Profilbild neben @name", s == 200 and m is not None)
    if m:
        s2, h2, _ = img(m.group(1).replace("&amp;", "&"))
        ok(f"... und ohne Anmeldung abrufbar ({s2})", s2 == 200)
    api("av-anna", "PATCH", "/api/account/avatar", {"public": False})
    api("av-anna", "DELETE", f"/api/notes/{nid}/publish")


def sso_and_ldap():
    """OIDC "picture" (mockoidc) und LDAP jpegPhoto (Test-LDAP), jeweils nur mit Admin-Schalter."""
    from sso_ldap import BASE_DN, sso_login
    oidc = {"enabled": True, "issuer": "http://mockoidc:8080/default", "client_id": "saga", "client_secret": "test-secret",
            "scopes": "openid profile email", "username_claim": "preferred_username", "name_claim": "name", "email_claim": "email",
            "groups_claim": "groups", "label": "SSO", "auto_create": True, "allowed_group": "", "admin_group": "", "verify_tls": True}
    auth.save_setting("oidc", oidc)
    auth.save_setting("avatar", {"gravatar": True, "oidc_picture": False, "ldap_photo": False})
    settings_wait()
    pic = f"http://127.0.0.1:{FAKE_PORT}/pic/ok.jpg"
    HITS.pop("/pic/ok.jpg", None)                      # unit() hat die Attrappe schon einmal gefragt
    c, s, loc = sso_login("av-sub-1", {"preferred_username": "av-sso", "name": "Sso Bild", "picture": pic})
    ok(f"SSO-Anmeldung ({s})", s == 303)
    s, st = api("av-sso", "GET", "/api/account/avatar")
    ok("Schalter aus: SSO-Bild nicht uebernommen", st.get("source") is None and HITS.get("/pic/ok.jpg", 0) == 0)
    auth.save_setting("avatar", {"gravatar": True, "oidc_picture": True, "ldap_photo": True})
    settings_wait()
    sso_login("av-sub-1", {"preferred_username": "av-sso", "name": "Sso Bild", "picture": pic})
    ok("Anmeldung merkt nur die Adresse (noch kein Abruf)", HITS.get("/pic/ok.jpg", 0) == 0
       and [tuple(r) for r in db1("SELECT sso_kind, sso_ref FROM avatar WHERE uid='av-sso'")] == [("oidc", pic)])
    s, st = api("av-sso", "GET", "/api/account/avatar")
    ok(f"SSO-Bild vom Server abgeholt ({st.get('source')})", st.get("source") == "sso" and HITS.get("/pic/ok.jpg") == 1)
    s, h, d = img(st.get("url") or "/x", "av-anna", accept="*/*")
    ok("SSO-Bild neu kodiert (JPEG ohne EXIF)", s == 200 and not has_exif(d))
    api("av-sso", "GET", "/api/account/avatar")
    ok("SSO-Bild zwischengespeichert", HITS.get("/pic/ok.jpg") == 1)
    sso_login("av-sub-2", {"preferred_username": "av-sso2", "name": "Sso Intern", "picture": f"http://{B.hostname}:{B.port}/healthz"})
    s, st = api("av-sso2", "GET", "/api/account/avatar")
    ok("SSRF: SSO-Bild auf interner Adresse wird nicht abgeholt (Initialen)", s == 200 and st.get("source") is None)
    sso_login("av-sub-3", {"preferred_username": "av-sso3", "name": "Sso Meta", "picture": f"http://127.0.0.1:{FAKE_PORT}/redirect-intern"})
    s, st = api("av-sso3", "GET", "/api/account/avatar")
    ok("SSRF: Weiterleitung auf Cloud-Metadaten wird nicht verfolgt", s == 200 and st.get("source") is None)
    sso_login("av-sub-4", {"preferred_username": "av-sso4", "name": "Sso Kaputt", "picture": "javascript:alert(1)"})
    ok("Unsinnige Bildadresse wird gar nicht gemerkt", not db1("SELECT 1 FROM avatar WHERE uid='av-sso4' AND sso_ref IS NOT NULL"))
    auth.save_setting("oidc", dict(oidc, enabled=False))

    # LDAP: jpegPhoto im Verzeichnis, Anmeldung mit LDAP-Passwort
    from ldap3 import Connection, Server, MODIFY_REPLACE
    lpw = "Ldap-Avatar-789"
    lc = Connection(Server("ldap://ldap.test", connect_timeout=5), f"cn=admin,{BASE_DN}", os.environ["HK_LDAP_ADMIN_PW"], auto_bind=True)
    lc.add(f"ou=people,{BASE_DN}", "organizationalUnit")
    dn = f"uid=av-ldap,ou=people,{BASE_DN}"
    lc.delete(dn)
    ok("LDAP: Testperson mit jpegPhoto angelegt", lc.add(dn, "inetOrgPerson", {"cn": "Ldap Foto", "sn": "Foto", "userPassword": lpw,
                                                                                "jpegPhoto": jpeg_with_exif(150, 150, 1)}))
    auth.save_setting("ldap", {"enabled": True, "preset": "openldap", "url": "ldap://ldap.test", "starttls": True, "verify_tls": False,
                               "bind_dn": f"cn=admin,{BASE_DN}", "bind_password": os.environ["HK_LDAP_ADMIN_PW"], "user_base": f"ou=people,{BASE_DN}",
                               "user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))", "attr_username": "uid", "attr_name": "cn",
                               "attr_mail": "mail", "group_mode": "search", "group_base": f"ou=people,{BASE_DN}", "group_filter": "(member={dn})",
                               "group_name_attr": "cn", "auto_create": True, "allowed_group": "", "admin_group": "", "password_login": True})
    auth.save_setting("local_login", True)
    settings_wait()
    from standalone import login
    u, s, _ = login("av-ldap", lpw)
    ok(f"LDAP-Anmeldung ({s})", s == 303)
    s, st = api("av-ldap", "GET", "/api/account/avatar")
    ok(f"LDAP-Foto uebernommen und neu kodiert ({st.get('source')})", st.get("source") == "sso" and st.get("sso") == "ldap"
       and not any(b"Exif" in bytes(r[0]) for r in db1("SELECT data FROM avatar_img WHERE uid='av-ldap'")))
    lc.modify(dn, {"jpegPhoto": [(MODIFY_REPLACE, [])]})
    login("av-ldap", lpw)
    s, st = api("av-ldap", "GET", "/api/account/avatar")
    ok("Foto im Verzeichnis entfernt -> nach der Anmeldung weg", st.get("source") is None)
    lc.delete(dn)
    auth.save_setting("ldap", {"enabled": False})


def deletion(url):
    from saga.userdata import delete_user_data
    upload("av-anna", png(100, 100, (200, 100, 0, 255), "RGB"), "image/png")
    ok("Vor dem Loeschen: Bilder vorhanden", len(db1("SELECT 1 FROM avatar_img WHERE uid='av-anna'")) >= 6)
    delete_user_data("av-anna", drop_user=True)
    ok("Konto geloescht: Profilbild, Gravatar-Zwischenspeicher und Einstellungen weg",
       not db1("SELECT 1 FROM avatar_img WHERE uid='av-anna'") and not db1("SELECT 1 FROM avatar WHERE uid='av-anna'"))
    ok("Alte Adresse -> 404", img(url, "av-dora")[0] == 404)


def federation():
    """Zwei eigene Instanzen (Hilfen aus tests/foederation.py, eigene Datenbanken): Bild einer Person der anderen Instanz -
    signiert abgeholt, neu kodiert, zwischengespeichert; Abruf hier nur angemeldet; beim Trennen weg."""
    import foederation as F
    from saga import fedsig
    F.I["a"].update(db="saga_ava", state="/tmp/avtest-a")
    F.I["b"].update(db="saga_avb", state="/tmp/avtest-b")
    HA, HB = F.I["a"]["host"], F.I["b"]["host"]
    try:
        for k in F.I:
            F.pg_admin(f"DROP DATABASE IF EXISTS {F.I[k]['db']} WITH (FORCE)")
            F.pg_admin(f"CREATE DATABASE {F.I[k]['db']}")
        F.py("a", F.SETUP.format(allow=F.I["a"]["allow"], users=[("fa-anna", "Anna Eigner", "anna")], groups=[]))
        F.py("b", F.SETUP.format(allow=F.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert")], groups=[]))
        F.start("a"); F.start("b")
        A, Bd = F.Admin("a"), F.Admin("b")
        ok("Foederation: Admins angemeldet", A.login() and Bd.login())
        for adm in (A, Bd):
            adm.post({"action": "enable", "on": "1"})
        _, html = A.post({"action": "invite"})
        m = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html)
        wk = F.jd(F.hreq("a", "GET", "/.well-known/saga")[2])
        Bd.post({"action": "connect", "url": F.I["a"]["url"], "code": m.group(1) if m else "", "fingerprint": wk["fingerprint"]})
        pa = F.sql("a", "SELECT id FROM fed_peer")[0][0]
        A.post({"action": "confirm", "id": str(pa)})
        ok("Foederation: verbunden", F.sql("b", "SELECT status FROM fed_peer")[0][0] == "active")
        s, h, d = F.hreq("a", "PUT", "/api/account/avatar", raw=jpeg_with_exif(500, 400), cookie=F.ck("a", "fa-anna"),
                         hdr={"X-Saga": "1", "Content-Type": "image/jpeg"})
        ok(f"A: Anna laedt ein Bild hoch ({s})", s == 200)

        ok("Ohne Signatur: /fed/v1/avatar -> 401", F.hreq("a", "GET", "/fed/v1/avatar/anna")[0] == 401)
        privb = fedsig.unb64u(F.py("b", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))"))
        s, h, d, nonce, _ = F.signed("a", privb, F.I["b"]["url"], "GET", "/fed/v1/avatar/anna")
        try:
            fedsig.verify_response(fedsig.unb64u(wk["key"]["public"]), F.I["a"]["url"], s, h, d, nonce); good = True
        except fedsig.SigError:
            good = False
        ok(f"Signierter Abruf: WebP 256, Antwort signiert ({s}, {len(d)} Bytes)", s == 200 and good and h.get("content-type") == "image/webp"
           and Image.open(io.BytesIO(d)).size == (256, 256) and len(d) <= 512 * 1024 and not has_exif(d))
        s, *_ = F.signed("a", privb, F.I["b"]["url"], "GET", "/fed/v1/avatar/niemand")
        ok("Unbekannter Name -> 404", s == 404)
        s, *_ = F.signed("a", privb, F.I["b"]["url"], "GET", "/fed/v1/avatar/fa-anna")
        ok("Anmeldename statt oeffentlichem Namen -> 404 (kein Anmeldename nach aussen)", s == 404)

        s, p = F.api("a", "fa-anna", "POST", "/api/notes", {"title": "Fed-Avatar"})
        s, _ = F.api("a", "fa-anna", "PUT", f"/api/notes/{p['id']}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
        F.wait(lambda: F.sql("b", "SELECT 1 FROM fed_inbound"))
        s, d = F.api("b", "fb-bert", "GET", "/api/fed/shared")
        owner = (d.get("items") or [{}])[0].get("owner") or {}
        url = owner.get("avatar") or ""
        ok(f"B: Eigentuemerin (fremd) mit Profilbild-Adresse dieser Instanz ({url[:40]})", url.startswith("/api/avatar/r") and HA not in url)
        s, h, data = F.hreq("b", "GET", url + "&s=128", hdr={"Accept": "image/webp"}, cookie=F.ck("b", "fb-bert"))
        ok(f"B: Bild ueber die Foederation abgeholt, neu kodiert ({s})", s == 200 and Image.open(io.BytesIO(data)).size == (128, 128))
        row1 = F.sql("b", "SELECT g_checked, g_found FROM avatar WHERE uid=?", (f"@anna@{HA}",))
        ok("B: zwischengespeichert (gefunden)", row1 and row1[0][1] == 1)
        F.hreq("b", "GET", url, hdr={"Accept": "*/*"}, cookie=F.ck("b", "fb-bert"))
        ok("B: zweiter Abruf ohne neue Anfrage bei A", F.sql("b", "SELECT g_checked FROM avatar WHERE uid=?", (f"@anna@{HA}",)) == [[row1[0][0]]]
           or F.sql("b", "SELECT g_checked FROM avatar WHERE uid=?", (f"@anna@{HA}",))[0][0] == row1[0][0])
        ok("B: ohne Anmeldung -> 404", F.hreq("b", "GET", url)[0] == 404)
        k = url.split("/api/avatar/")[1].split("?")[0]
        forged = "r" + base64.urlsafe_b64encode(f"@anna@{HB}".encode()).decode().rstrip("=") + "." + k.split(".")[1]
        ok("B: gefaelschter Schluessel (andere Person, alter MAC) -> 404", F.hreq("b", "GET", "/api/avatar/" + forged, cookie=F.ck("b", "fb-bert"))[0] == 404)
        pb = F.sql("b", "SELECT id FROM fed_peer")[0][0]
        Bd.post({"action": "disconnect", "id": str(pb)})
        ok("B: Verbindung getrennt -> zwischengespeicherte Bilder der Instanz weg",
           not F.sql("b", "SELECT 1 FROM avatar_img WHERE uid LIKE ?", (f"%@{HA}",)) and not F.sql("b", "SELECT 1 FROM avatar WHERE uid LIKE ?", (f"%@{HA}",)))
    finally:
        F.stop_all()
        for k in F.I:
            n, txt = F.server_errors(k)
            ok(f"Foederation {k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
            try:
                F.pg_admin(f"DROP DATABASE IF EXISTS {F.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
        res.extend(F.res)


if __name__ == "__main__":
    snap = snapshot_settings()
    try:
        nid, url = main()
        published(nid, url)
        sso_and_ldap()
        deletion(url)
    except Exception as ex:  # noqa: BLE001
        import traceback; traceback.print_exc(); ok(f"Abbruch: {ex}", False)
    finally:
        try:
            cleanup()
        finally:
            restore_settings(snap)
    try:
        federation()
    except Exception as ex:  # noqa: BLE001
        import traceback; traceback.print_exc(); ok(f"Abbruch Foederation: {ex}", False)
    print(f"\nAvatare {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
