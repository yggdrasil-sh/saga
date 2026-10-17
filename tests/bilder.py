"""Verkleinerte Bilder (?w=): WebP-Vorschau, animiertes GIF bleibt animiert, kleine Bilder im Original,
fremde Dateien bleiben gesperrt, Widget-Bild ebenfalls verkleinert, Aufraeumen entfernt Vorschauen."""
import base64, io, json, os, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
from PIL import Image
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def ck(uid): return TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()
H = {"Host": HOST, "Cookie": "saga=" + ck("hk-test-a"), "X-Saga": "1"}
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
def raw(p, h=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, headers=h or H), timeout=60) as r: return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e: return e.code, e.headers, e.read()
def upload(data, name):
    bd = "hk-test"; mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bd}--\r\n".encode()
    hh = dict(H, **{"Content-Type": f"multipart/form-data; boundary={bd}"})
    with urllib.request.urlopen(urllib.request.Request(B + "/api/files", data=mp, method="POST", headers=hh), timeout=20) as r: return json.loads(r.read())["file"]["url"]
try:
    b = io.BytesIO(); im = Image.new("RGB", (1600, 900)); px = im.load()
    for x in range(0, 1600, 3):
        for y in range(0, 900, 3): px[x, y] = (x % 256, y % 256, (x * y) % 256)       # Rauschen -> grosse PNG
    im.save(b, "PNG"); png = b.getvalue()
    u = upload(png, "foto.png"); fid = u.rsplit("/", 1)[1]
    st, h, d = raw(u)
    ok(f"Original: {h.get('Content-Type')}, {len(d)//1024} KB, {h.get('Cache-Control')}", st == 200 and d == png and "immutable" in (h.get("Cache-Control") or ""))
    st, h, d = raw(u + "?w=320")
    t = Image.open(io.BytesIO(d)) if st == 200 else None
    ok(f"?w=320 -> {h.get('Content-Type')} {t and t.size}, {len(d)//1024} KB", st == 200 and h.get("Content-Type") == "image/webp" and t.size == (320, 180) and len(d) < len(png))
    st2, h2, d2 = raw(u + "?w=300")
    ok("?w=300 wird auf die naechste Stufe (320) gerundet und kommt aus dem Zwischenspeicher", st2 == 200 and d2 == d)
    st, h, d = raw(u + "?w=2000")
    ok(f"?w=2000 groesser als das Bild -> Original ({h.get('Content-Type')})", st == 200 and d == png)
    st, h, d = raw(u + "?w=abc")
    ok("Unsinn bei w -> Original", st == 200 and d == png)
    # animiertes GIF bleibt animiert (als kleineres GIF)
    frames = [Image.new("RGB", (800, 400), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    b = io.BytesIO(); frames[0].save(b, "GIF", save_all=True, append_images=frames[1:], duration=120, loop=0); gif = b.getvalue()
    g = upload(gif, "anim.gif")
    st, h, d = raw(g + "?w=160")
    t = Image.open(io.BytesIO(d)) if st == 200 else None
    ok(f"Animiertes GIF ?w=160 -> {h.get('Content-Type')} {t and t.size}, {t and getattr(t, 'n_frames', 1)} Bilder",
       st == 200 and h.get("Content-Type") == "image/gif" and t.size == (160, 80) and getattr(t, "n_frames", 1) == 3)
    # fremde Person: auch die Vorschau ist gesperrt
    st, h, d = raw(u + "?w=320", dict(H, Cookie="saga=" + ck("hk-test-b")))
    ok(f"Andere Person: kein Zugriff auf die Vorschau ({st})", st == 404)
    # Kopieren/Einfuegen von Bildbloecken (2.1): /api/files/copy - gleiche Referenz im eigenen Konto, sonst Kopie fuer den
    # Eigentuemer der Zielseite; Quelle sehen + Ziel bearbeiten noetig
    def js(m, p, body=None, who="hk-test-a"):
        h = dict(H, Cookie="saga=" + ck(who), **{"Content-Type": "application/json"})
        rq = urllib.request.Request(B + p, data=json.dumps(body).encode() if body is not None else None, method=m, headers=h)
        try:
            with urllib.request.urlopen(rq, timeout=20) as r: return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
    st, d = js("POST", "/api/files/copy", {"url": u})
    ok(f"Einfuegen in eigene Seite: gleiche Referenz ({st})", st == 200 and d.get("url") == u and d.get("copied") is False)
    st, nb = js("POST", "/api/notes", {"title": "Fremde Seite"}, who="hk-test-b")
    st, d = js("POST", "/api/files/copy", {"url": u, "note": nb["id"]})
    ok(f"Fremde Seite ohne Bearbeiten-Recht -> abgelehnt ({st})", st in (403, 404))
    js("PUT", f"/api/notes/{nb['id']}/shares", {"type": "user", "target": "hk-test-a", "role": "edit"}, who="hk-test-b")
    st, d = js("POST", "/api/files/copy", {"url": u, "note": nb["id"]})
    ok(f"Fremde Seite mit Bearbeiten-Recht: eigene Kopie fuer deren Eigentuemer ({st})", st == 200 and d.get("copied") is True and d.get("url") != u)
    st2, h2, d2 = raw(d.get("url", "/x"), dict(H, Cookie="saga=" + ck("hk-test-b")))
    ok("Eigentuemer der Zielseite sieht die Kopie (gleicher Inhalt)", st2 == 200 and d2 == png)
    ok("Datei, die ich nicht sehen darf -> 404", js("POST", "/api/files/copy", {"url": u}, who="hk-test-b")[0] == 404)
    ok("Unsinnige Angabe -> 400", js("POST", "/api/files/copy", {"url": "/files/../etc"})[0] == 400)
    # Aufraeumen entfernt Vorschauen mit
    sys.path.insert(0, "/tmp/hkapp"); os.environ.setdefault("STATE_DIR", os.path.dirname(DB))
    td = os.path.join(os.path.dirname(DB), "files", "thumbs")
    ok("Vorschau liegt auf der Platte", any(f.startswith(fid) for f in os.listdir(td)))
    cn = sqlite3.connect(DB); cn.execute("UPDATE file SET created=0 WHERE uid='hk-test-a'"); cn.commit()
    import app as A
    A.files_gc(min_age=60)
    ok("Datei-Aufraeumen entfernt auch die Vorschauen", not any(f.startswith(fid) for f in os.listdir(td)))
finally:
    cn = sqlite3.connect(DB); cn.execute("DELETE FROM note WHERE uid IN ('hk-test-a', 'hk-test-b')")
    try: cn.execute("DELETE FROM note_share WHERE owner_uid='hk-test-b'")
    except sqlite3.OperationalError: pass
    try: cn.execute("DELETE FROM file WHERE uid IN ('hk-test-a', 'hk-test-b')")
    except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nBILDER {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
