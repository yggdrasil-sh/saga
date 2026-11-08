"""Titelbild-Galerie (2.5, saga/covers.py): Auslieferung /covers/<id>.jpg (ohne Anmeldung, Cache, ETag/304, ?w=),
GET /api/covers, Pruefung beim Setzen (nur bekannte IDs), Zufallsbild beim Anlegen (Seite, Unterseite, Datenbank,
Vorlage ohne Titelbild, Startseite einer Galerie-Vorlage) und keins fuer Datenbank-Zeilen, cover: null, Duplikate,
Importe und mit ausgeschalteter Einstellung; Vorlage mit Titelbild -> das der Vorlage; veroeffentlichte Seite;
Admin: hochladen (Bildpruefung, Groesse, EXIF weg), ausblenden/einblenden, entfernen (weich/endgueltig), Nicht-Admin.

Wegwerf-Konten cg-*; raeumt am Ende auf (Seiten, Uploads, Admin-Aenderungen)."""
import base64
import hashlib
import http.client
import io
import json
import os
import secrets
import sys
import time
import urllib.parse
import zipfile

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD_ = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
SA = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILTIN = {c["id"]: c for c in json.load(open(os.path.join(ROOT, "static", "covers", "covers.json"), encoding="utf-8"))["covers"]}
PW = "Cg-Pass-12345"
res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:400]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h.update({"Cookie": "saga=" + cookie, "X-Saga": "1"})
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    heads = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    try:
        d = json.loads(d) if d[:1] in (b"[", b"{") else d
    except ValueError:
        pass
    return r.status, heads, d


class Admin:
    def __init__(self, aid):
        self.tok = "cg-csrf-" + secrets.token_hex(6)
        self.cookie = SA.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": self.tok}).encode())).decode()

    def _send(self, path, body, ctype):
        c = http.client.HTTPConnection(AD_.hostname, AD_.port, timeout=60)
        c.request("POST" if body is not None else "GET", path, body=body,
                  headers={"Cookie": "hk_admin=" + self.cookie, "Content-Type": ctype, "Accept-Language": "de"})
        r = c.getresponse(); data = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("hk_admin="):
                self.cookie = v.split(";", 1)[0][len("hk_admin="):]
        c.close()
        return r.status, data, {k.lower(): v for k, v in r.getheaders()}

    def get(self, path):
        return self._send(path, None, "text/plain")

    def form(self, path, form):
        return self._send(path, urllib.parse.urlencode(dict(form, csrf=self.tok)), "application/x-www-form-urlencoded")

    def upload(self, fields, files):
        bd = "cg" + secrets.token_hex(6)
        parts = [f"--{bd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                 for k, v in dict(fields, csrf=fields.get("csrf", self.tok)).items()]
        for name, data in files:
            parts.append(f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        return self._send("/covers/upload", b"".join(parts) + f"--{bd}--\r\n".encode(), f"multipart/form-data; boundary={bd}")


def jpeg(w=900, h=500, color=(30, 120, 200), gps=False):
    from PIL import Image
    im = Image.new("RGB", (w, h), color)
    b = io.BytesIO()
    if gps:
        ex = Image.Exif()
        ex[0x010F] = "CgKamera"                      # Make
        ex[0x8825] = {1: "N", 2: (52.0, 30.0, 0.0)}  # GPS-IFD
        im.save(b, "JPEG", exif=ex.tobytes())
    else:
        im.save(b, "JPEG")
    return b.getvalue()


def note(cookie, nid):
    return req("GET", f"/api/notes/{nid}", cookie)[2]


def new(cookie, **b):
    s, _, d = req("POST", "/api/notes", cookie, b)
    return d.get("id") if s == 200 and isinstance(d, dict) else None


def gallery_cover(n):
    return isinstance(n, dict) and isinstance(n.get("cover"), str) and n["cover"].startswith("/covers/") and n["cover"].endswith(".jpg")


def cleanup():
    with connect() as c, c:
        for t in ("note", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'cg-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cg-%'")


def main():
    cleanup()
    try:
        run()
    finally:
        cleanup()
        with connect() as c, c:                          # Uploads und Admin-Aenderungen an mitgelieferten Bildern zuruecknehmen
            for (cid,) in c.execute("SELECT id FROM cover_gallery WHERE created_by LIKE 'cg-%'").fetchall():
                try:
                    os.remove(os.path.join(os.environ.get("STATE_DIR", "/var/lib/saga"), "covers", cid + ".jpg"))
                except OSError:
                    pass
            c.execute("DELETE FROM cover_gallery WHERE created_by LIKE 'cg-%' OR builtin=1")
        try:                                             # Zwischenspeicher des Servers: Hochladen + Loeschen laedt neu
            auth.get_user("cg-adm") or auth.create_user("cg-adm", PW, "Admin Galerie", is_admin=True)
            adm = Admin("cg-adm")
            adm.upload({"title": "cg-reset"}, [("r.jpg", jpeg(color=tuple(secrets.randbelow(256) for _ in range(3))))])
            with connect() as c:
                r = c.execute("SELECT id FROM cover_gallery WHERE title='cg-reset'").fetchone()
            if r:
                adm.form("/covers", {"id": r[0], "action": "remove"})
        except Exception as ex:  # noqa: BLE001
            print("Aufraeumen (Admin):", ex)
        cleanup()
    print(f"\nTitelbild-Galerie {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    auth.create_user("cg-a", PW, "Cora Galerie")
    auth.create_user("cg-b", PW, "Bert Galerie")
    auth.create_user("cg-adm", PW, "Admin Galerie", is_admin=True)
    A, Bk = ck("cg-a"), ck("cg-b")

    # --- Auslieferung ---------------------------------------------------------------------------------------------
    s, h, d = req("GET", "/covers/hokusai-welle.jpg")
    ok(f"/covers/<id>.jpg ohne Anmeldung: 200 JPEG ({s}, {h.get('content-type')})", s == 200 and h.get("content-type") == "image/jpeg"
       and d[:2] == b"\xff\xd8")
    ok(f"Cache-Header lang und oeffentlich, ETag ({h.get('cache-control')})", "public" in (h.get("cache-control") or "")
       and "max-age=2592000" in (h.get("cache-control") or "") and bool(h.get("etag")))
    s2, _, _ = req("GET", "/covers/hokusai-welle.jpg", hdr={"If-None-Match": h.get("etag", "")})
    ok(f"If-None-Match -> 304 ({s2})", s2 == 304)
    s3, h3, d3 = req("GET", "/covers/hokusai-welle.jpg?w=384")
    ok(f"?w=384 verkleinert ({s3}, {len(d3)} < {len(d)} Bytes)", s3 == 200 and 0 < len(d3) < len(d) // 3 and h3.get("etag") != h.get("etag"))
    s4, _, d4 = req("GET", "/covers/hokusai-welle.jpg?w=2000")
    ok("?w=2000 (wie die Linux-App beim Titelbild) -> Original", s4 == 200 and len(d4) == len(d))
    for bad in ("/covers/unbekannt.jpg", "/covers/..%2Fcovers.json", "/covers/covers.json", "/covers/hokusai-welle.png", "/covers/README.md"):
        ok(f"{bad} -> 404", req("GET", bad)[0] == 404)

    # --- Liste ------------------------------------------------------------------------------------------------------
    ok("GET /api/covers ohne Anmeldung -> 401", req("GET", "/api/covers")[0] == 401)
    s, _, L = req("GET", "/api/covers", A)
    ids = [c["id"] for c in L.get("covers", [])] if s == 200 else []
    ok(f"GET /api/covers: alle {len(BUILTIN)} mitgelieferten, Gruppen in Reihenfolge, Einstellung an",
       s == 200 and set(BUILTIN) <= set(ids) and [g["id"] for g in L["groups"]][:4] == ["nordisch", "romantik", "holzschnitt", "landschaft"]
       and L.get("random_cover") is True, L if s != 200 else [g["id"] for g in L["groups"]])
    w = next((c for c in L.get("covers", []) if c["id"] == "hokusai-welle"), {})
    ok("Eintrag: url, thumb, Titel (de), Kuenstler, Quelle, pos", w.get("url") == "/covers/hokusai-welle.jpg" and w.get("thumb", "").startswith(w["url"] + "?w=")
       and w.get("title") == BUILTIN["hokusai-welle"]["title_de"] and w.get("artist") == "Katsushika Hokusai"
       and w.get("source_url", "").startswith("https://www.metmuseum.org/") and w.get("pos") == BUILTIN["hokusai-welle"]["pos"], w)
    s, _, Le = req("GET", "/api/covers", A, hdr={"Accept-Language": "en"})
    ok("Titel englisch bei Accept-Language en", any(c["id"] == "hokusai-welle" and c["title"] == BUILTIN["hokusai-welle"]["title_en"]
                                                    for c in Le.get("covers", [])))

    # --- Anlegen: zufaellig ------------------------------------------------------------------------------------------
    p1 = new(A, title="Seite eins")
    n1 = note(A, p1)
    cid1 = n1.get("cover", "")[8:-4]
    ok(f"Neue Seite: Galerie-Titelbild + empfohlene Position ({n1.get('cover')}, {n1.get('cover_pos')})",
       gallery_cover(n1) and cid1 in BUILTIN and n1.get("cover_pos") == BUILTIN[cid1]["pos"])
    seen = {n1.get("cover")}
    for i in range(6):
        seen.add(note(A, new(A, title=f"Zufall {i}")).get("cover"))
    ok(f"Zufall: verschiedene Bilder ({len(seen)} verschiedene bei 7 Seiten)", len(seen) >= 2)
    sub = new(A, title="Unterseite", parent_id=p1)
    ok("Unterseite: Galerie-Titelbild", gallery_cover(note(A, sub)))
    dbid = new(A, title="Liste", kind="database", parent_id=p1)
    ok("Datenbank: Galerie-Titelbild", gallery_cover(note(A, dbid)))
    row = new(A, title="Zeile", parent_id=dbid)
    ok("Datenbank-Zeile: kein Titelbild", row and not note(A, row).get("cover"), note(A, row))
    nn = new(A, title="Ohne", cover=None)
    ok("cover: null beim Anlegen -> keins (Schnellerfassung, Chat -> Seite, neue Vorlage)", nn and not note(A, nn).get("cover"))
    fx = new(A, title="Fest", cover="/covers/church-anden.jpg")
    n = note(A, fx)
    ok("cover: Galerie-Pfad beim Anlegen -> genau dieses, pos aus covers.json",
       n.get("cover") == "/covers/church-anden.jpg" and n.get("cover_pos") == BUILTIN["church-anden"]["pos"], n.get("cover_pos"))
    s, _, d = req("POST", "/api/notes", A, {"title": "x", "cover": "/covers/../static/x.jpg"})
    ok(f"cover: fremder Pfad beim Anlegen -> 400 ({s})", s == 400)

    # --- Setzen (PATCH) ---------------------------------------------------------------------------------------------
    s, _, _ = req("PATCH", f"/api/notes/{nn}", A, {"cover": "/covers/hokusai-roter-fuji.jpg", "cover_pos": 33})
    n = note(A, nn)
    ok(f"PATCH cover = Galerie-Pfad -> 200 ({s})", s == 200 and n.get("cover") == "/covers/hokusai-roter-fuji.jpg" and n.get("cover_pos") == 33)
    for bad in ("/covers/../x.jpg", "/covers/unbekannt.jpg", "/covers/hokusai-welle.jpg?w=1", "/covers/HOKUSAI-WELLE.jpg",
                "https://example.org/covers/hokusai-welle.jpg", "/static/covers/hokusai-welle.jpg", "hokusai-welle"):
        s, _, d = req("PATCH", f"/api/notes/{nn}", A, {"cover": bad})
        ok(f"PATCH cover {bad!r} -> abgelehnt ({s})", s == 400, d)
    ok("abgelehnt: Titelbild unveraendert", note(A, nn).get("cover") == "/covers/hokusai-roter-fuji.jpg")

    # --- Duplikate und Vorlagen ---------------------------------------------------------------------------------------
    s, _, d = req("POST", f"/api/notes/{nn}/duplicate", A, {})
    ok("Duplikat behaelt sein Titelbild", s == 200 and note(A, d["id"]).get("cover") == "/covers/hokusai-roter-fuji.jpg")
    plain = new(A, title="Ohne Bild", cover=None)
    s, _, d = req("POST", f"/api/notes/{plain}/duplicate", A, {})
    ok("Duplikat ohne Titelbild bleibt ohne", s == 200 and not note(A, d["id"]).get("cover"))
    tpl1 = new(A, title="Vorlage mit Bild", cover="/covers/turner-walfaenger.jpg")
    req("PATCH", f"/api/notes/{tpl1}", A, {"template": True})
    tpl2 = new(A, title="Vorlage ohne Bild", cover=None)
    req("PATCH", f"/api/notes/{tpl2}", A, {"template": True})
    s, _, d = req("POST", f"/api/notes/{tpl1}/duplicate", A, {})
    ok("Seite aus Vorlage mit Titelbild -> das der Vorlage", s == 200 and note(A, d["id"]).get("cover") == "/covers/turner-walfaenger.jpg")
    s, _, d = req("POST", f"/api/notes/{tpl2}/duplicate", A, {})
    ok("Seite aus Vorlage ohne Titelbild -> zufaellig", s == 200 and gallery_cover(note(A, d["id"])))
    s, _, d = req("POST", f"/api/notes/{tpl2}/duplicate", A, {"template": True})
    ok("Vorlage duplizieren (bleibt Vorlage) -> ohne", s == 200 and not note(A, d["id"]).get("cover"))
    etpl = new(A, title="Eintrag-Vorlage", parent_id=dbid, cover=None)
    req("PATCH", f"/api/notes/{etpl}", A, {"template": True})
    s, _, d = req("POST", f"/api/notes/{etpl}/duplicate", A, {"parent_id": dbid})
    ok("Eintrag aus Datenbank-Vorlage -> ohne", s == 200 and not note(A, d["id"]).get("cover"))

    # --- Galerie-Vorlagen ----------------------------------------------------------------------------------------------
    s, _, d = req("POST", "/api/gallery/reise/use", A, {"examples": False})
    if s == 200:
        root = note(A, d["root"])
        ok("Galerie-Vorlage Reiseplaner: festes Titelbild der Vorlage", root.get("cover") == "/covers/bierstadt-lake-louise.jpg", root.get("cover"))
        others = [note(A, i) for i in d["created"] if i != d["root"]]
        ok("Galerie-Vorlage: Unterseiten/Datenbanken ohne cover bleiben ohne", others and all(not o.get("cover") for o in others))
    else:
        ok(f"Galerie-Vorlage reise anlegen ({s})", False, d)
    s, _, d = req("POST", "/api/gallery/wiki/use", A, {"examples": False})
    ok("Galerie-Vorlage ohne cover: Startseite zufaellig", s == 200 and gallery_cover(note(A, d["root"])), d if s != 200 else None)

    # --- Import ------------------------------------------------------------------------------------------------------------
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Notizen/Erste.md", "# Erste\n\nText.\n")
        z.writestr("Notizen/Zweite.md", "Noch ein Text.\n")
    s, _, up = req("POST", "/api/import/upload?name=Notizen.zip&replace=1", A, raw=buf.getvalue())
    if s == 200:
        req("POST", f"/api/import/{up['token']}/analyze", A, {})
        s, _, r = req("POST", f"/api/import/{up['token']}/run", A, {"kind": "md"})
        st = {}
        for _ in range(240):
            st = req("GET", f"/api/import/runs/{r.get('run_id')}", A)[2]
            if isinstance(st, dict) and st.get("state") in ("done", "failed", "cancelled"):
                break
            time.sleep(0.25)
        with connect() as c:
            cv = c.execute("WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id) "
                           "SELECT n.cover FROM note n JOIN t ON n.id=t.id", (st.get("root") or 0,)).fetchall()
        ok(f"Markdown-Import: Seiten ohne Titelbild ({len(cv)} Seiten)", st.get("state") == "done" and len(cv) >= 2 and all(not x[0] for x in cv), st)
    else:
        ok(f"Import-Upload ({s})", False, up)

    # --- Einstellung ------------------------------------------------------------------------------------------------------
    s, _, d = req("GET", "/api/account/pages", A)
    ok("GET /api/account/pages: random_cover an (Standard)", s == 200 and d == {"random_cover": True}, d)
    ok("PUT ungueltig -> 400", req("PUT", "/api/account/pages", A, {"random_cover": "nein"})[0] == 400)
    s, _, d = req("PUT", "/api/account/pages", A, {"random_cover": False})
    ok("PUT random_cover false", s == 200 and d.get("random_cover") is False)
    ok("Einstellung aus: neue Seite ohne Titelbild", not note(A, new(A, title="Aus")).get("cover"))
    s, _, d = req("POST", f"/api/notes/{tpl2}/duplicate", A, {})
    ok("Einstellung aus: Seite aus Vorlage ohne Titelbild bleibt ohne", s == 200 and not note(A, d["id"]).get("cover"))
    ok("Einstellung gilt je Konto (B weiter an)", gallery_cover(note(Bk, new(Bk, title="Bert"))))
    req("PUT", "/api/account/pages", A, {"random_cover": True})
    ok("wieder an: Zufall", gallery_cover(note(A, new(A, title="An"))))

    # --- Veroeffentlichen ---------------------------------------------------------------------------------------------------
    s, _, _ = req("PUT", "/api/account/public-name", A, {"name": "cg-cora"})
    s, _, d = req("PUT", f"/api/notes/{fx}/publish", A, {})
    url = d.get("url") if isinstance(d, dict) else None
    if s == 200 and url:
        s, _, html = req("GET", urllib.parse.urlparse(url).path)
        ok("Veroeffentlichte Seite zeigt das Galerie-Titelbild", s == 200 and b'src="/covers/church-anden.jpg"' in html, s)
    else:
        ok(f"Veroeffentlichen ({s})", False, d)

    # --- Admin -------------------------------------------------------------------------------------------------------------
    adm, fake = Admin("cg-adm"), Admin("cg-a")
    s, page, _ = adm.get("/covers")
    ok(f"Admin /covers: Seite mit allen Bildern ({s})", s == 200 and page.count(b'class="adm-cov') >= len(BUILTIN))
    s, _, _ = adm.get("/covers/hokusai-welle.jpg?w=448")
    ok("Admin: Vorschaubild auf eigener Origin", s == 200)
    s, _, h = fake.upload({"title": "Fremd"}, [("a.jpg", jpeg())])
    with connect() as c:
        n_up = c.execute("SELECT COUNT(*) FROM cover_gallery WHERE builtin=0 AND created_by='cg-a'").fetchone()[0]
    ok(f"Nicht-Admin: Upload -> Anmeldung, nichts gespeichert ({s} {h.get('location')})", s in (303, 403) and n_up == 0)
    s, _, _ = fake.form("/covers", {"id": "hokusai-welle", "action": "hide"})
    ok("Nicht-Admin: Ausblenden wirkt nicht", any(c["id"] == "hokusai-welle" for c in req("GET", "/api/covers", A)[2]["covers"]))
    s, _, _ = adm.upload({"title": "Ohne CSRF", "csrf": "falsch"}, [("a.jpg", jpeg())])
    with connect() as c:
        n_up = c.execute("SELECT COUNT(*) FROM cover_gallery WHERE title='Ohne CSRF'").fetchone()[0]
    ok("Admin ohne gueltiges CSRF: nichts gespeichert", n_up == 0)

    s, _, _ = adm.upload({"title": "Meine Bucht", "artist": "Cora", "group": "Eigene", "pos": "30", "url": "https://example.org/bild",
                          "source": "eigenes Foto"}, [("bucht.jpg", jpeg(3200, 1800, gps=True))])
    with connect() as c:
        r = c.execute("SELECT id, title, artist, grp, pos, url, source FROM cover_gallery WHERE created_by='cg-adm' AND title='Meine Bucht'").fetchone()
    ok(f"Admin: Upload gespeichert ({s}, {r})", s == 303 and r and r[0].startswith("u-") and r[3] == "eigene" and r[4] == 30)
    up1 = r[0] if r else "u-x"
    s, h, img = req("GET", f"/covers/{up1}.jpg")
    from PIL import Image
    im = Image.open(io.BytesIO(img)) if s == 200 else None
    ok(f"Upload ausgeliefert: 1600 px breit, JPEG, ohne EXIF/GPS ({im.size if im else s})",
       im is not None and im.format == "JPEG" and im.size == (1600, 900) and not im.getexif() and b"CgKamera" not in img)
    L2 = req("GET", "/api/covers", A)[2]
    e = next((c for c in L2["covers"] if c["id"] == up1), {})
    ok("Upload in GET /api/covers (Titel, Kuenstler, Gruppe 'eigene' am Ende, pos 30, builtin false)",
       e.get("title") == "Meine Bucht" and e.get("artist") == "Cora" and e.get("group") == "eigene" and e.get("pos") == 30
       and e.get("builtin") is False and L2["groups"][-1]["id"] == "eigene", e)
    ok("Upload als Titelbild setzbar", req("PATCH", f"/api/notes/{plain}", A, {"cover": f"/covers/{up1}.jpg"})[0] == 200)
    s, _, _ = adm.upload({"title": "Kaputt"}, [("x.jpg", b"\xff\xd8\xff\xe0 kein Bild" * 50)])
    s2, _, _ = adm.upload({"title": "Text"}, [("x.txt", b"Hallo Welt" * 100)])
    s3, _, _ = adm.upload({"title": "Winzig"}, [("w.jpg", jpeg(20, 20))])
    with connect() as c:
        n_bad = c.execute("SELECT COUNT(*) FROM cover_gallery WHERE title IN ('Kaputt','Text','Winzig')").fetchone()[0]
    ok(f"Kein Bild / zu klein: abgelehnt ({s}, {s2}, {s3})", n_bad == 0)
    big = os.urandom(21 * 1024 * 1024)
    s, _, _ = adm.upload({"title": "Riesig"}, [("big.jpg", big)])
    with connect() as c:
        n_bad = c.execute("SELECT COUNT(*) FROM cover_gallery WHERE title='Riesig'").fetchone()[0]
    ok(f"Ueber 20 MB: abgelehnt ({s})", n_bad == 0)
    s, _, _ = adm.upload({"group": "eigene"}, [("zwei-a.jpg", jpeg(color=(200, 50, 50))), ("zwei-b.jpg", jpeg(color=(50, 200, 50)))])
    with connect() as c:
        two = [x[0] for x in c.execute("SELECT title FROM cover_gallery WHERE created_by='cg-adm' AND title LIKE 'zwei-%' ORDER BY title").fetchall()]
    ok(f"Mehrere Bilder auf einmal, Titel = Dateiname ({two})", two == ["zwei-a", "zwei-b"])

    # bearbeiten (mitgeliefert)
    adm.form("/covers", {"id": "dahl-vesuv", "action": "save", "title": "Vesuv (Admin)", "artist": "", "group": "nordisch", "pos": "12"})
    e = next((c for c in req("GET", "/api/covers", A)[2]["covers"] if c["id"] == "dahl-vesuv"), {})
    ok("Admin: Titel/Position eines mitgelieferten Bildes geaendert", e.get("title") == "Vesuv (Admin)" and e.get("pos") == 12
       and e.get("artist") == "Johan Christian Dahl", e)

    # ausblenden: alte Seite zeigt es weiter
    old = new(A, title="Nutzt Welle", cover="/covers/hokusai-welle.jpg")
    adm.form("/covers", {"id": "hokusai-welle", "action": "hide"})
    L3 = req("GET", "/api/covers", A)[2]
    ok("Ausgeblendet: nicht mehr in GET /api/covers", all(c["id"] != "hokusai-welle" for c in L3["covers"]))
    ok("Ausgeblendet: Seite zeigt es weiter, Datei ausgeliefert", note(A, old).get("cover") == "/covers/hokusai-welle.jpg"
       and req("GET", "/covers/hokusai-welle.jpg")[0] == 200)
    ok("Ausgeblendet: weiter setzbar auf Seiten, die es schon haben (PATCH gleicher Wert)",
       req("PATCH", f"/api/notes/{old}", A, {"cover": "/covers/hokusai-welle.jpg", "cover_pos": 40})[0] == 200)
    got = {note(A, new(A, title=f"Nach Ausblenden {i}")).get("cover") for i in range(12)}
    ok(f"Ausgeblendet: nicht mehr im Zufall ({len(got)} Bilder bei 12 Seiten)", "/covers/hokusai-welle.jpg" not in got)
    adm.form("/covers", {"id": "hokusai-welle", "action": "show"})
    ok("Einblenden: wieder in der Liste", any(c["id"] == "hokusai-welle" for c in req("GET", "/api/covers", A)[2]["covers"]))

    # alle ausgeblendet -> kein Zufallsbild
    vis = [c["id"] for c in req("GET", "/api/covers", A)[2]["covers"]]
    for cid in vis:
        adm.form("/covers", {"id": cid, "action": "hide"})
    s, _, L4 = req("GET", "/api/covers", A)
    ok("Alle ausgeblendet: Liste leer", s == 200 and L4["covers"] == [])
    ok("Alle ausgeblendet: neue Seite ohne Titelbild", not note(A, new(A, title="Leer")).get("cover"))
    for cid in vis:
        adm.form("/covers", {"id": cid, "action": "show"})
    ok("Alle wieder eingeblendet", len(req("GET", "/api/covers", A)[2]["covers"]) == len(vis))

    # entfernen: benutzt -> weich, unbenutzt -> 404
    s, _, _ = adm.form("/covers", {"id": up1, "action": "remove"})
    ok("Entfernen (benutzt): nicht mehr in der Liste", all(c["id"] != up1 for c in req("GET", "/api/covers", A)[2]["covers"]))
    ok("Entfernen (benutzt): Seite zeigt es weiter (200)", note(A, plain).get("cover") == f"/covers/{up1}.jpg" and req("GET", f"/covers/{up1}.jpg")[0] == 200)
    with connect() as c:
        z2 = c.execute("SELECT id FROM cover_gallery WHERE title='zwei-a'").fetchone()[0]
    with connect() as c, c:                              # Zufallsseiten oben koennen es bekommen haben
        c.execute("DELETE FROM note WHERE uid LIKE 'cg-%' AND cover=?", (f"/covers/{z2}.jpg",))
    adm.form("/covers", {"id": z2, "action": "remove"})
    ok("Entfernen (unbenutzt): Datei weg -> 404", req("GET", f"/covers/{z2}.jpg")[0] == 404)
    ok("Entfernen (unbenutzt): als Titelbild abgelehnt", req("PATCH", f"/api/notes/{plain}", A, {"cover": f"/covers/{z2}.jpg"})[0] == 400)
    adm.form("/covers", {"id": "hokusai-welle", "action": "remove"})
    ok("Mitgeliefertes Bild laesst sich nicht entfernen", req("GET", "/covers/hokusai-welle.jpg")[0] == 200
       and any(c["id"] == "hokusai-welle" for c in req("GET", "/api/covers", A)[2]["covers"]))
    req("PATCH", f"/api/notes/{plain}", A, {"cover": None})
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid='cg-a' AND cover=?", (f"/covers/{up1}.jpg",))
    adm.form("/covers", {"id": up1, "action": "remove"})
    ok("Entfernt + nicht mehr benutzt: endgueltig loeschen -> 404", req("GET", f"/covers/{up1}.jpg")[0] == 404)

    with open(os.environ.get("SAGA_SERVER_LOG", "/dev/null"), errors="replace") as f:
        log = f.read()
    if os.environ.get("SAGA_SERVER_LOG"):
        ok("Server-Log: Admin-Aktionen protokolliert", "admin: cg-adm laedt Titelbilder hoch" in log and "blendet Titelbild hokusai-welle aus" in log)


if __name__ == "__main__":
    main()
