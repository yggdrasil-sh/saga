"""Attrappen fuer die Handbuch-Bilder (tools/screens-handbuch.py, Saga 2.5): laufen im Netz-Namensraum der Testumgebung
(tools/testumgebung, Art server oder knoten) und nur dort - nie im echten Netz.

- Weiterleitung (TCP): Anzeige-Hosts wie saga.example oder bookorbit.example zeigen per /etc/hosts des Laufs (UMG_HOSTS) auf
  eigene Adressen 127.0.1.x; auf deren Port 80 reicht forward() an den Port des Dienstes weiter. So stehen in den Bildern
  saga.example, admin.verein.example, cloud.saga.example statt 127.0.0.1:<port> - im Browser und im Server.
- Nextcloud: tests/fakedav.py (Nextcloud-Art) mit gezeichneter Vorschau fuer PDFs und Demo-Dateien; das App-Passwort setzt
  nextcloud_start() direkt (kein Login Flow - die Adresse waere sonst die der Attrappe).
- BookOrbit: kleine Attrappe der REST-API /api/v1 (Anmeldung, Leseliste, Markierungen, Inhaltsverzeichnis, Cover) mit
  erfundenen Buechern.
Alle Bilder sind gezeichnet (Pillow), alle Inhalte erfunden."""
import math
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


# =====================================================================================================
# Zeichnen
# =====================================================================================================
_FONTS = {}


def font(size, bold=False):
    """TrueType-Schrift des Systems (fc-match), sonst die eingebaute."""
    key = (size, bold)
    if key not in _FONTS:
        try:
            path = subprocess.run(["fc-match", "-f", "%{file}", "Adwaita Sans:bold" if bold else "Adwaita Sans"],
                                  capture_output=True, text=True, timeout=10).stdout.strip()
            _FONTS[key] = ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001 - ohne fc-match: eingebaute Schrift
            _FONTS[key] = ImageFont.load_default(size)
    return _FONTS[key]


def png(im):
    b = io.BytesIO()
    im.save(b, "PNG", optimize=True)
    return b.getvalue()


def gradient(w, h, top, bottom):
    im = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(im)
    for y in range(h):
        f = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3)))
    return im


def avatar(initials, c1, c2, size=512):
    """Profilbild: Initialen auf Verlauf (keine Gesichter)."""
    im = gradient(size, size, c1, c2)
    d = ImageDraw.Draw(im)
    f = font(int(size * 0.38), True)
    d.text((size / 2, size / 2), initials, font=f, fill=(255, 255, 255), anchor="mm")
    return png(im)


def tulips(w=1600, h=1200):
    """Foto-Ersatz fuers Profilbild: Himmel, Wiese, Tulpen (gezeichnet)."""
    im = gradient(w, h, (120, 170, 230), (205, 225, 245))
    d = ImageDraw.Draw(im)
    d.rectangle([0, int(h * 0.55), w, h], fill=(104, 160, 84))
    for i in range(9):
        x = int(w * (0.08 + i * 0.105))
        y = int(h * (0.58 + (i % 3) * 0.06))
        d.line([(x, y), (x, h)], fill=(52, 110, 50), width=10)
        d.ellipse([x - 55, y - 70, x + 55, y + 40], fill=(232, 96, 104) if i % 2 else (240, 150, 70))
    d.ellipse([int(w * 0.75), 80, int(w * 0.75) + 170, 250], fill=(255, 236, 150))
    return png(im)


def cover_art(motif, c1, c2, w=1600, h=600):
    """Titelbild (gezeichnet, flache Formen - klein als PNG): Verlauf und ein einfaches Motiv."""
    im = gradient(w, h, c1, c2)
    d = ImageDraw.Draw(im)
    lt = lambda c, f: tuple(min(255, int(x + (255 - x) * f)) for x in c)  # noqa: E731
    dk = lambda c, f: tuple(int(x * (1 - f)) for x in c)  # noqa: E731
    if motif == "meer":
        d.ellipse([w * 0.72, h * 0.12, w * 0.72 + 150, h * 0.12 + 150], fill=(255, 236, 160))
        for k in range(4):
            y = int(h * (0.55 + k * 0.1))
            d.rectangle([0, y, w, h], fill=lt((60, 130, 190), 0.12 * (3 - k)))
        d.polygon([(0, h), (0, h * 0.86), (w * 0.4, h * 0.8), (w * 0.7, h * 0.9), (w, h * 0.84), (w, h)], fill=(236, 214, 170))
    elif motif == "huegel":
        d.ellipse([w * 0.12, h * 0.15, w * 0.12 + 130, h * 0.15 + 130], fill=(255, 240, 190))
        d.ellipse([-w * 0.2, h * 0.55, w * 0.7, h * 1.6], fill=dk(c2, 0.25))
        d.ellipse([w * 0.35, h * 0.62, w * 1.3, h * 1.7], fill=dk(c2, 0.4))
    elif motif == "blumen":
        d.rectangle([0, int(h * 0.62), w, h], fill=(110, 165, 90))
        for i in range(12):
            x, y = int(w * (0.05 + i * 0.08)), int(h * (0.6 + (i % 3) * 0.08))
            d.line([(x, y), (x, h)], fill=(60, 120, 60), width=8)
            d.ellipse([x - 34, y - 40, x + 34, y + 24], fill=[(236, 110, 120), (246, 186, 80), (190, 120, 210)][i % 3])
    elif motif == "punkte":
        for i in range(9):
            r = 40 + (i % 4) * 22
            x, y = int(w * (0.08 + i * 0.11)), int(h * (0.3 + (i % 3) * 0.2))
            d.ellipse([x - r, y - r, x + r, y + r], fill=lt(c1, 0.35 + 0.1 * (i % 3)))
    elif motif == "streifen":
        for i in range(-4, 14):
            x = i * 140
            d.polygon([(x, h), (x + 70, h), (x + 70 + h * 0.6, 0), (x + h * 0.6, 0)], fill=lt(c1, 0.18))
    elif motif == "teller":                                          # Gericht von oben (Rezepte)
        cx, cy, r = w // 2, h // 2, int(h * 0.42)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(250, 248, 242))
        d.ellipse([cx - r + 26, cy - r + 26, cx + r - 26, cy + r - 26], fill=c2)
        for i in range(14):
            a, rr = i * 2.4, (r - 70) * (0.25 + 0.75 * ((i * 37) % 10) / 10)
            x, y = cx + int(rr * math.cos(a)), cy + int(rr * math.sin(a))
            d.ellipse([x - 18, y - 18, x + 18, y + 18], fill=lt(c1, 0.15))
    elif motif == "balken":                                          # bunte senkrechte Streifen (Lernplan)
        cols = [(236, 214, 170), (214, 92, 80), (240, 170, 90), (90, 150, 210), (110, 170, 110), (240, 200, 70), (150, 100, 190)]
        for i in range(16):
            d.rectangle([i * w // 16, 0, (i + 1) * w // 16, h], fill=cols[(i * 3) % len(cols)])
    elif motif == "haus":
        d.rectangle([0, int(h * 0.75), w, h], fill=dk(c2, 0.2))
        x0 = int(w * 0.42)
        d.rectangle([x0, int(h * 0.42), x0 + 300, int(h * 0.78)], fill=(250, 244, 230))
        d.polygon([(x0 - 30, int(h * 0.44)), (x0 + 150, int(h * 0.16)), (x0 + 330, int(h * 0.44))], fill=(196, 92, 70))
        d.rectangle([x0 + 125, int(h * 0.58), x0 + 175, int(h * 0.78)], fill=(120, 80, 60))
    return png(im)


def book_cover(title, author, c1, c2, w=600, h=900):
    im = gradient(w, h, c1, c2)
    d = ImageDraw.Draw(im)
    d.rectangle([30, 30, w - 30, h - 30], outline=(255, 255, 255), width=4)
    words, lines, cur = title.split(), [], ""
    for wd in words:
        if len(cur + " " + wd) > 14 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    lines.append(cur)
    y = 200
    for ln in lines:
        d.text((w / 2, y), ln, font=font(58, True), fill=(255, 255, 255), anchor="mm")
        y += 78
    d.text((w / 2, h - 120), author, font=font(38), fill=(255, 255, 255), anchor="mm")
    # kleiner Leuchtturm/Wellen als Motiv
    d.polygon([(w / 2 - 40, h - 210), (w / 2 + 40, h - 210), (w / 2 + 22, y + 40), (w / 2 - 22, y + 40)], fill=(255, 255, 255))
    for k in range(3):
        d.arc([60, h - 260 + k * 18, w - 60, h - 160 + k * 18], 200, 340, fill=(255, 255, 255), width=4)
    return png(im)


def doc_page(title, lines, w=850, h=1100):
    """Vorschau einer PDF-Seite (Nextcloud-Attrappe)."""
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((70, 90), title, font=font(40, True), fill=(30, 30, 30))
    d.line([(70, 150), (w - 70, 150)], fill=(180, 180, 180), width=2)
    y = 190
    for ln in lines:
        d.text((70, y), ln, font=font(26), fill=(60, 60, 60))
        y += 46
    for k in range(10):
        d.rectangle([70, y + 20 + k * 38, w - 70 - (k % 3) * 90, y + 34 + k * 38], fill=(222, 222, 222))
    return png(im)


def tiny_pdf(text):
    """Kleines, gueltiges PDF (eine Seite, ein Satz) - Inhalt fuer den Download der Attrappe."""
    s = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode("latin-1", "replace")
    s += b"\n%" + b"Demo " * 9000          # Fuelltext (Kommentar): Groesse wie ein echter Vertrag
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(s)).encode() + b" >>\nstream\n" + s + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


# =====================================================================================================
# TCP-Weiterleitung (Anzeige-Host:80 -> Dienst-Port)
# =====================================================================================================
def _pump(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d:
                break
            b.sendall(d)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def forward(listen_ip, listen_port, target_port, target_ip="127.0.0.1"):
    """listen_ip:listen_port -> target_ip:target_port (Threads, auch WebSocket/SSE)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((listen_ip, listen_port))
    srv.listen(128)

    def run():
        while True:
            c, _ = srv.accept()
            try:
                t = socket.create_connection((target_ip, target_port), 10)
            except OSError:
                c.close()
                continue
            for x, y in ((c, t), (t, c)):
                threading.Thread(target=_pump, args=(x, y), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return srv


# =====================================================================================================
# Nextcloud (tests/fakedav.py)
# =====================================================================================================
NC_FILES = [
    ("Familie/Urlaub 2027/Mietvertrag Ferienwohnung Möwe.pdf", "application/pdf",
     ("Mietvertrag Ferienwohnung Möwe", ["Ferienwohnung Möwe, Strandweg 7, Ostseebad", "Mieter: Familie Sommer",
                                         "Zeitraum: 24.07. – 07.08.2027", "Preis: 1.260 € (Anzahlung 300 €)"])),
    ("Familie/Urlaub 2027/Packliste.pdf", "application/pdf", ("Packliste", ["Badesachen, Handtücher", "Sonnencreme", "Fahrradhelme"])),
    ("Familie/Schule/Elternbrief Klassenfahrt.pdf", "application/pdf", ("Elternbrief Klassenfahrt", ["Klasse 9b", "Abfahrt 12. Mai"])),
]


def nextcloud_start(port, login, app_pw):
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import fakedav  # noqa: E402 - aus tests/
    previews = {}
    orig = fakedav.make_handler

    def handler(st):                                      # Vorschau: gezeichnete Seite statt 1x1-PNG
        H = orig(st)

        class P(H):
            def handle_one(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/index.php/core/preview" and self._authed():
                    fid = (urllib.parse.parse_qs(u.query).get("fileId") or [""])[0]
                    if fid in previews:
                        return self._send(200, previews[fid], "image/png")
                return H.handle_one(self)
            do_GET = do_POST = do_PUT = do_DELETE = do_PROPFIND = do_MKCOL = do_MOVE = do_HEAD = do_SEARCH = handle_one
        return P

    fakedav.make_handler = handler
    srv = fakedav.start(port, True)
    st = srv.store
    st.pw[login] = app_pw
    ids = {}
    for path, mime, (title, lines) in NC_FILES:
        f = st.root + "/" + path
        with st.lock:
            parts = f.split("/")
            for i in range(len(parts) - 1, 1, -1):
                d = "/".join(parts[:i])
                if d.startswith(st.root) and d not in st.dirs:
                    st.dirs.add(d)
                    st.touch(d)
            st.files[f] = tiny_pdf(title)
            st.touch(f)
            st.mtimes[f] = time.time() - 3600 * 26
            fid = st.fid(f)
        ids[path] = fid
        previews[str(fid)] = doc_page(title, lines)
    return srv, ids, fakedav.UID


# =====================================================================================================
# BookOrbit
# =====================================================================================================
def _ann(i, text, chapter, color="#FACC15", note=None, page=None, day=1):
    return {"id": i, "bookId": 0, "cfi": None, "jumpFileId": None, "pageno": page, "text": text, "color": color, "style": "highlight",
            "note": note, "chapterTitle": chapter, "origin": "web", "positionStatus": "exact", "chapterIndex": None,
            "highlightedAt": f"2026-11-{day:02d}T20:15:00.000Z", "createdAt": f"2026-11-{day:02d}T20:15:00.000Z", "starredAt": None}


def _toc(*labels):
    return [{"label": x, "href": f"Text/k{i}.xhtml"} for i, x in enumerate(labels, 1)]


BOOKS = {
    31: {"title": "Das Leuchtfeuer von Hiddensee", "authors": ["Marta Ohlsen"], "progress": 64.0,
         "note": "Von Ben zum Geburtstag. Im Urlaub an der Ostsee noch einmal lesen!", "colors": ((38, 84, 124), (112, 168, 196)),
         "toc": _toc("1 Ankunft im Nebel", "2 Der alte Wärter", "3 Sturmflut", "4 Briefe aus Stralsund", "5 Heimkehr"),
         "ann": [_ann(101, "Der Nebel lag so dicht über dem Hafen, dass man die Möwen nur hörte.", "1 Ankunft im Nebel", "#FACC15", None, 12, 2),
                 _ann(102, "Wer ein Licht hütet, wird selbst nie ganz dunkel.", "1 Ankunft im Nebel", "#4ADE80",
                      "Schöner Satz – für Claras Abi-Karte?", 19, 3),
                 _ann(103, "Er zählte die Stufen jeden Abend, als könnte eine fehlen.", "2 Der alte Wärter", "#FACC15", None, 41, 6),
                 _ann(104, "Das Meer nimmt nichts übel, aber es vergisst auch nichts.", "3 Sturmflut", "#F472B6",
                      "Erinnert mich an Oma Lenas Geschichten.", 77, 9),
                 _ann(105, "Am Morgen nach dem Sturm roch die ganze Insel nach Salz und Holz.", "3 Sturmflut", "#38BDF8", None, 83, 9)],
         "bm": [{"id": 501, "bookId": 31, "cfi": None, "title": "Hier weiterlesen", "positionSeconds": None,
                 "createdAt": "2026-11-12T21:30:00.000Z", "note": None, "updatedAt": "2026-11-12T21:30:00.000Z", "clientId": "x",
                 "origin": "web", "chapterId": None}]},
    32: {"title": "Gärtnern mit Kindern", "authors": ["Jonas Feld"], "progress": 23.0, "note": None, "colors": ((70, 120, 60), (170, 200, 110)),
         "toc": _toc("Säen", "Gießen", "Ernten"),
         "ann": [_ann(201, "Kinder lernen Geduld am schnellsten bei Radieschen.", "Säen", "#4ADE80", None, 8, 14)], "bm": []},
    33: {"title": "Kleine Geschichte der Ostsee", "authors": ["Ida Brenner"], "progress": 100.0, "note": None, "read": "2026-10-20T09:00:00.000Z",
         "colors": ((130, 80, 50), (210, 160, 110)), "toc": _toc("Eis", "Hanse", "Bäder"),
         "ann": [_ann(301, "Die Hanse war ein Netz aus Kontoren, nicht aus Mauern.", "Hanse", "#FACC15", None, 55, 1)], "bm": []},
}


class _BO:
    lock = threading.Lock()
    users = {}
    access, refresh = {}, {}
    covers = {}


def _creds(user):
    rt, at = secrets.token_hex(32), "acc-" + secrets.token_hex(8)
    _BO.refresh[rt] = user
    _BO.access[at] = user
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + s)) + ".000Z"  # noqa: E731
    return {"accessToken": at, "accessTokenExpiresAt": iso(900), "refreshToken": rt, "refreshTokenExpiresAt": iso(7 * 86400), "sessionId": 1}


class _BOH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ct="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def user(self):
        return _BO.access.get((self.headers.get("Authorization") or "")[7:])

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            d = {}
        p = urllib.parse.urlparse(self.path).path
        with _BO.lock:
            if p == "/api/v1/auth/login":
                u = _BO.users.get(d.get("username"))
                if not u or u["pw"] != d.get("password"):
                    return self.send(401, {"message": "Invalid credentials"})
                return self.send(200, dict(_creds(d["username"]), user={"username": d["username"], "name": u["name"]}))
            if p == "/api/v1/auth/refresh":
                u = _BO.refresh.pop(d.get("refreshToken"), None)
                return self.send(200, _creds(u)) if u else self.send(401, {"message": "Unauthorized"})
            if p == "/api/v1/auth/logout":
                _BO.refresh.pop(d.get("refreshToken"), None)
                return self.send(200, {})
        if not self.user():
            return self.send(401, {"message": "Unauthorized"})
        if p == "/api/v1/dashboard/widgets/batch":
            cur = [b for b in BOOKS.items() if b[1]["progress"] < 100]
            return self.send(200, {"items": [
                {"type": "currently-reading", "failed": False, "data": {"books": [
                    {"bookId": k, "title": v["title"], "authors": v["authors"], "progress": v["progress"], "hasCover": True, "fileFormat": "EPUB"}
                    for k, v in cur]}},
                {"type": "reading-goal", "failed": False, "data": {"goalBooks": 12, "completedBooks": 9, "year": 2026}},
                {"type": "reading-streak", "failed": False, "data": {"currentStreak": 4, "longestStreak": 11,
                                                                     "lastSevenDays": [True, False, True, True, True, True, False]}}]})
        if p == "/api/v1/books/query":
            rules = ((d.get("filter") or {}).get("rules")) or []
            want = next((r.get("value") for r in rules if r.get("field") == "readStatus"), None)
            xs = [(k, v) for k, v in BOOKS.items() if not want or v.get("read")]
            if d.get("q"):
                xs = [(k, v) for k, v in xs if d["q"].lower() in v["title"].lower()]
            items = [{"id": k, "status": "ready", "title": v["title"], "authors": v["authors"],
                      "readStatus": {"status": "read", "source": "manual", "startedAt": None, "finishedAt": v["read"], "updatedAt": v["read"]}
                      if v.get("read") else None} for k, v in xs]
            return self.send(200, {"items": items, "total": len(items), "page": 0, "size": 50})
        self.send(404, {"message": "Not Found"})

    def do_GET(self):
        if not self.user():
            return self.send(401, {"message": "Unauthorized", "statusCode": 401})
        pu = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(pu.query))
        parts = pu.path.split("/")
        if pu.path == "/api/v1/annotations/books":
            out = [{"bookId": k, "bookTitle": v["title"], "author": v["authors"][0], "count": len(v["ann"])} for k, v in BOOKS.items() if v["ann"]]
            if q.get("q"):
                out = [x for x in out if q["q"].lower() in x["bookTitle"].lower()]
            return self.send(200, out[:int(q.get("limit") or 20)])
        if pu.path == "/api/v1/books/search":
            out = [{"id": k, "title": v["title"], "seriesName": None, "authors": v["authors"], "libraryId": 1, "libraryName": "Bibliothek"}
                   for k, v in BOOKS.items() if (q.get("q") or "").lower() in v["title"].lower()]
            return self.send(200, out[:int(q.get("limit") or 10)])
        if len(parts) == 6 and parts[1:4] == ["api", "v1", "epub"] and parts[4].isdigit() and parts[5] == "info":
            b = BOOKS.get(int(parts[4]))
            if not b:
                return self.send(404, {"message": "Book not found"})
            return self.send(200, {"containerPath": "OEBPS/content.opf", "rootPath": "OEBPS/", "spine": [], "manifest": [], "optionalFiles": [],
                                   "toc": {"label": "Inhalt", "children": b["toc"]}, "metadata": {}, "coverPath": None})
        if len(parts) >= 5 and parts[1:4] == ["api", "v1", "books"] and parts[4].isdigit():
            bid = int(parts[4])
            b = BOOKS.get(bid)
            if not b:
                return self.send(404, {"message": f"Book {bid} not found", "statusCode": 404})
            rest = parts[5:]
            if not rest:
                return self.send(200, {"id": bid, "title": b["title"], "authors": [{"id": 1, "name": a, "sortName": None} for a in b["authors"]],
                                       "personalNote": b["note"], "description": ""})
            if rest == ["annotations"]:
                page, size = int(q.get("page") or 1), int(q.get("pageSize") or 25)
                items = [dict(x, bookId=bid) for x in b["ann"]][(page - 1) * size: page * size]
                return self.send(200, {"items": items, "total": len(b["ann"]), "page": page, "pageSize": size, "stats": {"chapters": []}})
            if rest == ["bookmarks"]:
                return self.send(200, b["bm"])
            if rest == ["thumbnail"]:
                if bid not in _BO.covers:
                    _BO.covers[bid] = book_cover(b["title"], b["authors"][0], *b["colors"])
                return self.send(200, _BO.covers[bid], "image/png")
        self.send(404, {"message": "Not Found"})


def bookorbit_start(port, user, pw, name):
    _BO.users[user] = {"pw": pw, "name": name}
    srv = ThreadingHTTPServer(("127.0.0.1", port), _BOH)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- Wetter-Attrappe (Open-Meteo-Format: /v1/forecast, /v1/search) - Werte erfunden, gleichbleibend ---------------------
class _WXH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from datetime import date, timedelta
        u = urllib.parse.urlsplit(self.path)
        if u.path.endswith("/search"):
            d = {"results": [{"name": "Lübeck", "admin1": "Schleswig-Holstein", "country": "Deutschland", "latitude": 53.87, "longitude": 10.69},
                             {"name": "Lübeck", "admin1": "Brandenburg", "country": "Deutschland", "latitude": 51.93, "longitude": 13.93}]}
        else:
            t0 = date.today()
            d = {"current": {"temperature_2m": 21.4, "weather_code": 2, "wind_speed_10m": 12},
                 "daily": {"time": [(t0 + timedelta(days=i)).isoformat() for i in range(8)],
                           "temperature_2m_max": [24, 25, 20, 18, 21, 22, 23, 21], "temperature_2m_min": [14, 15, 13, 12, 12, 13, 14, 13],
                           "weather_code": [1, 0, 3, 61, 2, 1, 0, 2], "precipitation_probability_max": [10, 0, 30, 80, 20, 10, 0, 10]}}
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def weather_start(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _WXH)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- KI-Attrappe (OpenAI-kompatibel: /v1/models, /v1/chat/completions mit stream) - feste, erfundene Antworten ------------
KI_ANSWERS = [
    ("sommerfest", "Für das Sommerfest fehlen laut der Seite „Sommerfest“ noch **Getränke**, **Grillkohle** und **zwei "
                   "Biertischgarnituren** [1]. Oma Lena bringt den Apfelkuchen mit, Ben kümmert sich ums Grillgut [1]. "
                   "Soll ich daraus Aufgaben anlegen?"),
    ("", "Dazu finde ich in deinen Seiten nichts Genaues."),
]


class _KIH(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, d):
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "familie-ki", "object": "model"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        q = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == "user").lower()
        text = next(a for k, a in KI_ANSWERS if k in q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for i in range(0, len(text), 12):
            ch = {"choices": [{"index": 0, "delta": {"content": text[i:i + 12]}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(ch, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.02)
        self.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        self.wfile.flush()
        self.close_connection = True


def ki_start(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _KIH)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
