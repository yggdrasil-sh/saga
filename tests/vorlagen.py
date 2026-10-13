"""Vorlage in andere Datenbank: Icon, Titelbild (+Position), volle Breite, Inhalt, Eigenschaften nach Namen."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
ck = TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": "hk-test-a"}).encode())).decode()
H = {"Host": urllib.parse.urlparse(os.environ["BASE_URL"]).netloc, "Cookie": "saga=" + ck, "X-Saga": "1", "Content-Type": "application/json"}
def call(m, p, b=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=H), timeout=20) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{m} {p} -> {e.code}: {e.read().decode()[:200]}")
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    def mkdb(title, opts):
        d = call("POST", "/api/notes", {"kind": "database", "title": title})["id"]
        sch = call("GET", f"/api/notes/{d}")["schema"]
        sch["props"] = [{"id": "st" + title[0].lower(), "name": "Status", "type": "select", "options": [{"id": f"{title[0].lower()}{i}", "name": n, "color": "gray"} for i, n in enumerate(opts)]},
                        {"id": "nr" + title[0].lower(), "name": "Nummer", "type": "number"}, {"id": "nur" + title[0].lower(), "name": "Nur " + title, "type": "text"}]
        call("PATCH", f"/api/notes/{d}", {"schema": sch}); return d
    vor = mkdb("Vorlagen", ["Erledigt", "Offen"]); ziel = mkdb("Ziel", ["Offen", "Erledigt"])
    tpl = call("POST", "/api/notes", {"parent_id": vor, "title": "Meine Vorlage", "icon": "🧪", "props": {"stv": "v1", "nrv": 42, "nurv": "weg"}})["id"]
    # kleines PNG hochladen (Titelbild muss ein eigenes Bild sein)
    import zlib, struct
    def chunk(k, d): return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00" + b"\xff\x00\x00" * 2 + b"\x00" + b"\x00\xff\x00" * 2)) + chunk(b"IEND", b"")
    bd = "hk-test"; mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"t.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + png + f"\r\n--{bd}--\r\n".encode()
    hh = dict(H); hh["Content-Type"] = f"multipart/form-data; boundary={bd}"
    with urllib.request.urlopen(urllib.request.Request(B + "/api/files", data=mp, method="POST", headers=hh), timeout=20) as r: cover = json.loads(r.read())["file"]["url"]
    call("PATCH", f"/api/notes/{tpl}", {"cover": cover, "cover_pos": 30, "wide": True,
                                         "body": json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "Vorlagentext"}}]}), "format": "blocks"})
    t = call("GET", f"/api/notes/{tpl}")
    new = call("POST", f"/api/notes/{tpl}/duplicate", {"parent_id": ziel, "title": "Neu aus Vorlage"})["id"]
    n = call("GET", f"/api/notes/{new}")
    ok(f"Icon {n['icon']!r}", n["icon"] == "🧪")
    ok(f"Titelbild uebernommen ({n.get('cover') or '-'}), Position {n.get('cover_pos')}", n.get("cover") == t.get("cover") and n.get("cover") and n.get("cover_pos") == 30)
    ok("Volle Breite uebernommen", n.get("wide") is True)
    ok("Inhalt uebernommen", "Vorlagentext" in n["body"])
    ok(f"Eigenschaften nach Namen: {n['props']}", n["props"].get("stz") == "z0" and n["props"].get("nrz") == 42 and "nurz" not in n["props"] and "stv" not in n["props"])
    same = call("GET", f"/api/notes/{call('POST', f'/api/notes/{tpl}/duplicate', {'parent_id': vor})['id']}")
    ok("Gleiche Datenbank: Eigenschaften 1:1", same["props"].get("stv") == "v1" and same["props"].get("nurv") == "weg")
    # Als Vorlage markiert: keine Zeile der Datenbank mehr, steht unter "templates"; die Kopie ist eine normale Zeile
    call("PATCH", f"/api/notes/{tpl}", {"template": True})
    rw = call("GET", f"/api/notes/{vor}/rows")
    ok("Vorlage nicht unter den Zeilen", tpl not in [r["id"] for r in rw["rows"]] and same["id"] in [r["id"] for r in rw["rows"]])
    ok(f"Vorlage unter templates: {rw.get('templates')}", [t["id"] for t in rw.get("templates", [])] == [tpl] and rw["templates"][0]["title"] == "Meine Vorlage")
    kopie = call("POST", f"/api/notes/{tpl}/duplicate", {"parent_id": vor})["id"]
    ok("Kopie einer Vorlage ist eine Zeile", kopie in [r["id"] for r in call("GET", f"/api/notes/{vor}/rows")["rows"]])
    # relative Datumsfilter werden gespeichert, unbekannte verworfen
    sch = call("GET", f"/api/notes/{vor}")["schema"]
    sch["props"].append({"id": "dt", "name": "Datum", "type": "date"})
    sch["views"] = [{"id": "w", "name": "Woche", "type": "list", "filters": [{"pid": "dt", "op": "this_week", "value": ""}, {"pid": "dt", "op": "today", "value": ""},
                     {"pid": "dt", "op": "this_month", "value": ""}, {"pid": "dt", "op": "next_year", "value": ""}]}]
    call("PATCH", f"/api/notes/{vor}", {"schema": sch})
    ops = [f["op"] for f in call("GET", f"/api/notes/{vor}")["schema"]["views"][0].get("filters", [])]
    ok(f"Relative Datumsfilter: {ops}", ops == ["this_week", "today", "this_month"])
finally:
    cn = sqlite3.connect(DB); cn.execute("DELETE FROM note WHERE uid='hk-test-a'")
    try: cn.execute("DELETE FROM file WHERE uid='hk-test-a'")
    except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nVORLAGEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
