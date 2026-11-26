"""2.6 (Bugs-DB 1530): eigene Logos bei Lesezeichen. Das Logo ist ein hochgeladenes Bild (/api/files, Feld image) und steht
wie bei Seiten-Symbolen als /files/<id> im Feld icon des Lesezeichens (keine Migration).

Prueft: Anlegen/Aendern mit eigenem Logo, Anzeige ueber icon_url (/api/bookmarks, /api/services fuer die Apps, Heute-
Kacheln im HTML) = /api/bookmarks/<id>/logo?v=<kurz-id> als PNG (laengste Kante 160, Seitenverhaeltnis bleibt, erstes Bild
bei GIF), nur fuer das eigene Konto (fremd/ohne Sitzung 404); fremde Datei-ID, Nicht-Bild, SVG (auch als Bild markiert),
unbekannte ID, Pfad-Tricks -> 400; Logo > 2 MB -> 400, Upload > ATTACH_MAX_MB -> 413, kein Bild -> 415; Ersetzen (v
wechselt), unveraendert zurueckgeschicktes icon (Apps) bleibt, Entfernen (zurueck zum automatischen Symbol); Aufraeumen
(files_gc) laesst das Logo stehen und loescht das ersetzte; App-Schluessel (POST /api/app/bookmarks) mit Logo; Einspielen
eines persoenlichen Backups nimmt Logo (neue Datei-ID) und Symbolnamen mit, Unsinn nicht.
Wegwerf-Konten lz-*; raeumt auf. Umgebung wie tests/favicon.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, hashlib, http.client, io, json, os, random, sys, tempfile, time, urllib.parse, zipfile
from itsdangerous import TimestampSigner
from PIL import Image
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, X, R = "lz-a", "lz-b", "lz-r"
USERS = (A, X, R)
res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {info}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, user=None, body=None, raw=None, ctype=None, hdr=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST}
    if user:
        h["Cookie"] = "saga=" + ck(user); h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body).encode()
    elif raw is not None:
        h["Content-Type"] = ctype or "application/octet-stream"
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read(); hd = {k.lower(): v for k, v in r.getheaders()}; c.close()
    if "json" in hd.get("content-type", ""):
        data = json.loads(data)
    return r.status, hd, data


def upload(user, data, name="logo.png", field="image"):
    bd = "----lz" + os.urandom(8).hex()
    raw = (f"--{bd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
           f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bd}--\r\n".encode()
    st, _, d = req("POST", "/api/files", user, raw=raw, ctype=f"multipart/form-data; boundary={bd}")
    return st, (d.get("file") or {}).get("url") if isinstance(d, dict) else None, d


def img(fmt, w, h, color=(200, 30, 30, 255), **kw):
    b = io.BytesIO(); Image.new("RGBA" if fmt in ("PNG", "WEBP", "GIF") else "RGB", (w, h), color if fmt != "JPEG" else color[:3]).save(b, fmt, **kw)
    return b.getvalue()


def gif_anim():
    fr = [Image.new("RGB", (60, 60), c) for c in ((255, 0, 0), (0, 255, 0))]
    b = io.BytesIO(); fr[0].save(b, "GIF", save_all=True, append_images=fr[1:], duration=100, loop=0); return b.getvalue()


def noise_png(n_bytes):
    side = int((n_bytes / 4) ** 0.5) + 8
    rnd = random.Random(1530)
    im = Image.frombytes("RGBA", (side, side), rnd.randbytes(side * side * 4))
    b = io.BytesIO(); im.save(b, "PNG", compress_level=0); return b.getvalue()


SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64"/></svg>'


def bm(user, bid):
    return next((b for b in req("GET", "/api/bookmarks", user)[2] if b["id"] == bid), None)


def cleanup():
    from saga.userdata import delete_user_data
    for u in USERS:
        try:
            delete_user_data(u)
        except Exception:
            pass
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username = ANY(?)", (list(USERS),))


def units():
    from saga.dashboard import tile_icon_url
    fid = "ab" * 12
    ok("Baustein: Logo eines Lesezeichens -> /api/bookmarks/<id>/logo?v=<8>",
       tile_icon_url({"own": True, "id": 7, "key": "bm7", "icon": f"/files/{fid}", "url": "https://x.example"}) == f"/api/bookmarks/7/logo?v={fid[:8]}")
    ok("Baustein: Dienst mit /files/... bekommt kein Bild", tile_icon_url({"own": False, "key": "svc", "icon": f"/files/{fid}"}) == "")
    ok("Baustein: selfh.st-Name unveraendert", tile_icon_url({"own": True, "id": 7, "key": "bm7", "icon": "openwrt"}) == "/api/services/icon/openwrt")


def server():
    for u in (A, X):
        req("POST", "/api/first-steps", u, {"answer": "no"})
    st, u1, d = upload(A, img("PNG", 400, 200))
    ok(f"PNG hochgeladen ({st})", st == 200 and u1 and u1.startswith("/files/"), d)
    fid1 = u1.rsplit("/", 1)[1]
    st, _, d = req("POST", "/api/bookmarks", A, {"name": "NAS", "url": "https://nas.example.org", "icon": u1, "category": "Haus"})
    ok(f"Lesezeichen mit eigenem Logo angelegt ({st})", st == 200 and d.get("icon") == u1, d)
    bid = d.get("id")
    b = bm(A, bid)
    want = f"/api/bookmarks/{bid}/logo?v={fid1[:8]}"
    ok(f"/api/bookmarks: icon_url zeigt auf das Logo ({b and b.get('icon_url')})", b and b["icon_url"] == want and b["icon"] == u1)
    svc = next((s for s in req("GET", "/api/services", A)[2] if s.get("key") == f"bm{bid}"), None)
    ok("/api/services (Apps): gleiches icon_url, beginnt mit /api/", svc and svc.get("icon_url") == want)
    st, hd, raw = req("GET", want, A)
    im = Image.open(io.BytesIO(raw)) if st == 200 else None
    ok(f"Logo als PNG 160x80 (Seitenverhaeltnis bleibt) ({st}, {im and im.size}, {hd.get('content-type')})",
       st == 200 and hd.get("content-type") == "image/png" and im.format == "PNG" and im.size == (160, 80))
    ok("Logo: privat zwischengespeichert, nosniff", "private" in hd.get("cache-control", "") and hd.get("x-content-type-options") == "nosniff")
    st2, hd2, raw2 = req("GET", want, A)
    ok("Logo: zweiter Abruf aus dem Zwischenspeicher, gleiche Bytes", st2 == 200 and raw2 == raw)
    ok("Logo: anderes Konto -> 404", req("GET", want, X)[0] == 404)
    ok("Logo: ohne Sitzung -> 404", req("GET", want)[0] == 404)
    ok("Logo: fremde Datei direkt (/files) fuer anderes Konto weiter 404", req("GET", u1, X)[0] == 404)
    st, hd, html = req("GET", "/dashboard", A)
    ok(f"Heute-Kachel zeigt das Logo ({st})", st == 200 and f'src="{want.replace("&", "&amp;")}"' in html.decode())
    # fremde Datei-ID, Nicht-Bild, SVG, Pfad-Tricks
    st, ux, _ = upload(X, img("PNG", 64, 64, (0, 0, 255, 255)))
    st1, _, d1 = req("POST", "/api/bookmarks", A, {"url": "https://a.example.org", "icon": ux})
    st2, _, d2 = req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": ux})
    ok(f"Fremde Datei-ID abgelehnt (anlegen {st1}, aendern {st2})", st1 == 400 and st2 == 400 and "Logo" in str(d1.get("error", "")), d1)
    ok("Nach Ablehnung bleibt das alte Logo", bm(A, bid)["icon"] == u1)
    st, ut, _ = upload(A, b"kein Bild, nur Text", "notiz.txt", field="file")
    ok(f"Nicht-Bild (Datei-Upload) als Logo abgelehnt", st == 200 and req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": ut})[0] == 400)
    ok("SVG als Bild-Upload abgelehnt (415, wie ueberall in Saga)", upload(A, SVG, "x.svg")[0] == 415)
    fsvg = os.urandom(12).hex()
    with connect() as c, c:                 # wie ein bereinigtes SVG aus einem Import (is_image=1, image/svg+xml)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fsvg, A, "x.svg", "image/svg+xml", len(SVG), 1, time.time()))
    ok("Importiertes SVG (als Bild markiert) als Logo abgelehnt", req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": f"/files/{fsvg}"})[0] == 400)
    bad = [f"/files/{'0' * 24}", "/files/../etc/passwd", f"/files/{fid1}x", f"/files/{fid1}?w=1", "/files/", f"https://x.example/files/{fid1}"]
    codes = [req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": v})[0] for v in bad]
    ok(f"Unbekannte ID/Pfad-Tricks abgelehnt ({codes})", all(c == 400 for c in codes))
    # Formate und Grenzen
    for fmt, data in (("JPEG", img("JPEG", 90, 300)), ("WebP", img("WEBP", 50, 50)), ("GIF animiert", gif_anim())):
        slug = fmt.split()[0].lower()
        st, u, _ = upload(A, data, "l." + slug)
        st2, _, _ = req("POST", "/api/bookmarks", A, {"url": f"https://{slug}.example.org", "icon": u})
        bx = next((x for x in req("GET", "/api/bookmarks", A)[2] if x["icon"] == u), None)
        s3, _, r3 = req("GET", bx["icon_url"], A) if bx else (0, 0, b"")
        sz = Image.open(io.BytesIO(r3)).size if s3 == 200 else None
        exp = {"JPEG": (48, 160), "WebP": (50, 50), "GIF animiert": (60, 60)}[fmt]
        ok(f"{fmt}: als Logo erlaubt, PNG {sz}", st == 200 and st2 == 200 and s3 == 200 and r3[:8] == b"\x89PNG\r\n\x1a\n" and sz == exp)
    big = noise_png(2 * 1024 * 1024 + 300_000)
    st, ub, _ = upload(A, big, "gross.png")
    st2, _, d2 = req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": ub})
    ok(f"Logo > 2 MB abgelehnt (Upload {st}, {len(big)} B -> {st2})", st == 200 and st2 == 400 and "2 MB" in str(d2.get("error", "")), d2)
    huge = b"\x89PNG\r\n\x1a\n" + b"\0" * (int(os.environ.get("ATTACH_MAX_MB", "10")) * 1048576 + 10)
    ok("Upload ueber ATTACH_MAX_MB -> 413", upload(A, huge)[0] == 413)
    # Ersetzen, unveraendert zurueckschicken, Entfernen
    st, u2, _ = upload(A, img("PNG", 100, 100, (0, 160, 0, 255)))
    st, _, d = req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": u2})
    b = bm(A, bid)
    ok(f"Ersetzen: neues Logo, v wechselt ({b['icon_url']})", st == 200 and b["icon"] == u2 and b["icon_url"] != want
       and b["icon_url"].endswith("v=" + u2.rsplit("/", 1)[1][:8]))
    st, _, _ = req("PATCH", f"/api/bookmarks/{bid}", A, {"name": "NAS2", "url": b["url"], "category": "Haus", "icon": u2})
    ok("Unveraendert zurueckgeschicktes icon (wie die Apps) bleibt", st == 200 and bm(A, bid)["icon"] == u2)
    st, _, _ = req("PATCH", f"/api/bookmarks/{bid}", A, {"name": "NAS3"})
    ok("Aendern ohne icon laesst das Logo stehen", st == 200 and bm(A, bid)["icon"] == u2)
    # Aufraeumen: ersetztes Logo weg, aktuelles bleibt
    from saga.files import files_gc
    with connect() as c, c:
        c.execute("UPDATE file SET created=? WHERE uid=?", (time.time() - 3 * 86400, A))
    files_gc()
    with connect() as c:
        have = {r[0] for r in c.execute("SELECT id FROM file WHERE uid=?", (A,))}
    f2 = u2.rsplit("/", 1)[1]
    ok("files_gc: aktuelles Logo bleibt, ersetztes wird geloescht", f2 in have and fid1 not in have, (f2 in have, fid1 in have))
    ok("Logo nach files_gc weiter abrufbar", req("GET", bm(A, bid)["icon_url"], A)[0] == 200)
    old_url = bm(A, bid)["icon_url"]
    st, _, _ = req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": ""})
    b = bm(A, bid)
    ok(f"Entfernen: zurueck zum automatischen Symbol ({b['icon_url']})", st == 200 and b["icon"] == "" and "/logo" not in b["icon_url"])
    ok("Entferntes Logo: alte Adresse -> 404", req("GET", old_url, A)[0] == 404)
    st, _, _ = req("PATCH", f"/api/bookmarks/{bid}", A, {"icon": u2})
    req("DELETE", f"/api/bookmarks/{bid}", A)
    ok("Geloeschtes Lesezeichen: Logo -> 404", st == 200 and req("GET", old_url, A)[0] == 404)
    # App-Schluessel (Teilen-Erweiterung)
    tok = req("POST", "/api/settings/tokens", A, {"name": "Testgeraet"})[2].get("token", "")
    hb = {"Authorization": "Bearer " + tok}
    st1, _, d1 = req("POST", "/api/app/bookmarks", body={"url": "https://app.example.org", "icon": u2}, hdr=hb)
    st2, _, _ = req("POST", "/api/app/bookmarks", body={"url": "https://app2.example.org", "icon": ux}, hdr=hb)
    ok(f"App-Schluessel: eigenes Logo ok ({st1}), fremdes 400 ({st2})", st1 == 200 and d1.get("icon") == u2 and st2 == 400)


def restore():
    """Persoenliches Backup einspielen (saga.userbackup.import_zip direkt, Kategorie Lesezeichen)."""
    from saga import userbackup
    old_logo, old_svg = os.urandom(12).hex(), os.urandom(12).hex()
    logo = img("PNG", 80, 80)
    data = {"format": userbackup.FORMAT, "notes": [],
            "files": [{"id": old_logo, "path": f"files/{old_logo}", "name": "l.png", "mime": "image/png"},
                      {"id": old_svg, "path": f"files/{old_svg}", "name": "x.txt", "mime": "text/plain"}],
            "bookmarks": [{"name": "Mit Logo", "url": "https://l.example.org", "icon": f"/files/{old_logo}", "category": "Haus"},
                          {"name": "Mit Symbol", "url": "https://s.example.org", "icon": "openwrt"},
                          {"name": "Fremde ID", "url": "https://f.example.org", "icon": f"/files/{'1' * 24}"},
                          {"name": "Kein Bild", "url": "https://k.example.org", "icon": f"/files/{old_svg}"},
                          {"name": "Unsinn", "url": "https://u.example.org", "icon": "../x"}]}
    ev = json.dumps(data).encode()
    sums = {"daten.json": {"size": len(ev), "sha256": hashlib.sha256(ev).hexdigest()}}
    members = {f"files/{old_logo}": logo, f"files/{old_svg}": b"nur text"}
    for k, v in members.items():
        sums[k] = {"size": len(v), "sha256": hashlib.sha256(v).hexdigest()}
    fd, zp = tempfile.mkstemp(suffix=".zip"); os.close(fd)
    try:
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("daten.json", ev)
            for k, v in members.items():
                z.writestr(k, v)
            z.writestr("manifest.json", json.dumps({"format": userbackup.FORMAT, "files": sums}))
        r = userbackup.import_zip(R, zp, {"folder": False, "bookmarks": True})
    finally:
        os.remove(zp)
    with connect() as c:
        got = {n: i for n, i in c.execute("SELECT name, icon FROM bookmark WHERE uid=?", (R,))}
    lg = got.get("Mit Logo", "")
    ok(f"Backup: Logo mit neuer Datei-ID eingespielt ({lg})", r.get("bookmarks") == 5 and lg.startswith("/files/") and old_logo not in lg)
    ok(f"Backup: Symbolname mit, fremde ID/Nicht-Bild/Unsinn leer ({got})",
       got.get("Mit Symbol") == "openwrt" and got.get("Fremde ID") == "" and got.get("Kein Bild") == "" and got.get("Unsinn") == "")
    with connect() as c:
        bid = c.execute("SELECT id FROM bookmark WHERE uid=? AND name='Mit Logo'", (R,)).fetchone()[0]
    b = bm(R, bid)
    ok("Backup: eingespieltes Logo wird angezeigt", b and req("GET", b["icon_url"], R)[0] == 200)


def main():
    cleanup()
    for u in USERS:
        auth.create_user(u, "Lz-Pass-12345", u)
    try:
        units()
        server()
        restore()
    finally:
        cleanup()
    print(f"\nLESEZEICHEN-LOGO {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
