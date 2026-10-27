"""2.5 Paket 1 Etappe 3: Datei-Eigenschaft in Datenbanken (Typ "files", saga/notes.py, saga/files.py).

1. Datenverlust-Schutz (zuerst): eine Datei, die NUR in note.props steht, ueberlebt files_gc; sichtbar fuer Geteilte
   (_file_visible) - ohne die Aenderung in files_gc/_file_visible/federation._file_for waere sie nach 24 h weg.
2. Schema/Werte: Typ files (max), Wert [{url, name}] nur mit eigenen Dateien des Eigentuemers, fremde/erfundene ->
   400, Liste begrenzt, Groesse/MIME/Bild beim Lesen (/rows, Einzelzeile), Upload ueber POST /api/files?note=<zeile>
   (auch fuer Bearbeitende: Datei gehoert dem Eigentuemer), Lesende sehen die Datei, Fremde nicht.
3. Lesepfade: CSV/Markdown (Dateiname), Duplizieren/Vorlage (gleiche Referenz), Quote, GC nach Entfernen.
Wegwerf-Konten fe-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET, STATE_DIR)."""
import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU = "fe-anna", "fe-bert", "fe-carl"
res = []
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00"
       b"\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, raw=None, ctype=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    data = raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    elif ctype:
        h["Content-Type"] = ctype
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    try:
        return r.status, json.loads(out) if out[:1] in (b"[", b"{") else out
    except ValueError:
        return r.status, out


def upload(u, data, name="bericht.pdf", note=None, field="file"):
    bd = "----fe" + os.urandom(6).hex()
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\n"
          f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bd}--\r\n".encode()
    return req("POST", "/api/files" + (f"?note={note}" if note else ""), u, raw=mp, ctype=f"multipart/form-data; boundary={bd}")


def fid_of(url):
    return url.rsplit("/", 1)[-1]


def gc_now():
    """files_gc wie der Hintergrund-Lauf, aber ohne Wartezeit (min_age=0) - nur Dateien der Test-Konten betroffen,
    weil fremde Dateien hier vorher als 'jung' markiert werden (created in der Zukunft) und danach zurueck."""
    from saga.files import files_gc
    with connect() as c, c:
        keep = c.execute("SELECT id, created FROM file WHERE uid NOT LIKE 'fe-%'").fetchall()
        c.execute("UPDATE file SET created=? WHERE uid NOT LIKE 'fe-%'", (time.time() + 3600,))
    try:
        return files_gc(min_age=0)
    finally:
        with connect() as c, c:
            for fid, cr in keep:
                c.execute("UPDATE file SET created=? WHERE id=?", (cr, fid))


def exists(fid):
    with connect() as c:
        return bool(c.execute("SELECT 1 FROM file WHERE id=?", (fid,)).fetchone())


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU, CU):
        auth.create_user(u, "Datei-Pass-1234", u.upper())
    time.sleep(5.6)
    try:
        run()
    finally:
        auth.save_setting("share_enabled", prev)
        cleanup()


def gc_part():
    """Teil 1: Datei nur in props (ohne Typ files direkt in die Datenbank geschrieben) - GC und Sichtbarkeit."""
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": "Ablage"})
    db = d["id"]
    row = req("POST", "/api/notes", body={"parent_id": db, "title": "Zeile mit Anhang"})[1]["id"]
    s, up = upload(A, b"%PDF-1.4 nur in props\n", "nur-props.pdf")
    ok(f"Upload ({s})", s == 200 and up.get("success") == 1, up)
    fid = fid_of(up["file"]["url"])
    s, up2 = upload(A, b"unbenutzt\n", "weg.txt")
    lost = fid_of(up2["file"]["url"])
    with connect() as c, c:
        c.execute("UPDATE note SET props=? WHERE id=?", (json.dumps({"anh": [{"url": f"/files/{fid}", "name": "nur-props.pdf"}]}), row))
    gc_now()
    ok("GC behaelt Datei, die nur in props steht", exists(fid))
    ok("GC loescht unbenutzte Datei (Gegenprobe)", not exists(lost))
    ok("Datei liegt noch auf der Platte", os.path.isfile(os.path.join(os.environ["STATE_DIR"], "files", fid)))
    # Sichtbarkeit: B mit Freigabe der Datenbank sieht die Datei, C ohne Freigabe nicht
    s, _ = req("PUT", f"/api/notes/{db}/shares", body={"type": "user", "target": BU, "role": "view"})
    ok(f"Datenbank an B freigegeben ({s})", s == 200)
    s, _ = req("GET", f"/files/{fid}", BU)
    ok(f"Geteilte Person sieht Datei aus props ({s})", s == 200)
    s, _ = req("GET", f"/files/{fid}", CU)
    ok(f"Fremde Person sieht sie nicht ({s})", s == 404)
    from saga import federation
    with connect() as c, c:
        c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)",
                  (db, A, "remote", "@fe-gast@b.example", "view", time.time()))
    fed_prev = auth.settings(fresh=True).get("fed_enabled", False)
    auth.save_setting("fed_enabled", True)
    try:
        r = federation._file_for("@fe-gast@b.example", fid, row, _FakeReq())
        ok("Foederation: _file_for liefert Datei aus props", r is not None and r[2] == "nur-props.pdf", r)
        r = federation._file_for("@fe-gast@b.example", lost, row, _FakeReq())
    finally:
        auth.save_setting("fed_enabled", fed_prev)
    ok("Foederation: fremde Datei-ID -> nichts", r is None)
    return db, row, fid


class _FakeReq:
    query_params = {}


def run():
    gc_part()
    typ_part()


def typ_part():
    """Teil 2/3: Typ files - Schema, Pruefung, Hochladen, Lesen, Export, Duplizieren, Entfernen + GC."""
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": "Belege"})
    db = d["id"]
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": {"props": [
        {"id": "anh", "name": "Anhänge", "type": "files", "max": 3},
        {"id": "eins", "name": "Foto", "type": "files", "max": 99},
        {"id": "n", "name": "Namen", "type": "formula", "expr": "prop(\"anh\").join(\" | \")"}]}})
    ok(f"Schema mit Datei-Spalten ({s})", s == 200, d)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1")
    sp = {p["id"]: p for p in d["schema"]["props"]}
    ok("max begrenzt (1..20, Standard 10)", sp["anh"]["max"] == 3 and sp["eins"]["max"] == 20, sp)
    row = req("POST", "/api/notes", body={"parent_id": db, "title": "Rechnung"})[1]["id"]
    s, up = upload(A, b"%PDF-1.4 rechnung\n", "rechnung.pdf", note=row)
    ok(f"Upload fuer die Zeile ({s})", s == 200 and up.get("success") == 1, up)
    s, im = upload(A, PNG, "bild.png", note=row, field="image")
    ok(f"Bild-Upload ({s})", s == 200, im)
    f1, f2 = up["file"]["url"], im["file"]["url"]
    s, d = req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": f1, "name": "rechnung.pdf"}, {"url": f2, "name": "bild.png"},
                                                              {"url": f1, "name": "doppelt"}]}})
    vals = (d.get("values") or {}).get("anh") or []
    ok(f"Werte gespeichert, Doppelte entfernt, Groesse/MIME/Bild in der Antwort ({s})", s == 200 and [x["url"] for x in vals] == [f1, f2]
       and vals[0]["size"] == 18 and vals[1]["image"] is True and vals[1]["mime"] == "image/png" and vals[0]["image"] is False, d)
    with connect() as c:
        st = json.loads(c.execute("SELECT props FROM note WHERE id=?", (row,)).fetchone()[0])
    ok("gespeichert nur {url, name}", st.get("anh") == [{"url": f1, "name": "rechnung.pdf"}, {"url": f2, "name": "bild.png"}], st)
    s, d = req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": "/files/" + "0" * 24, "name": "x"}]}})
    ok(f"erfundene Datei: 400 ({s})", s == 400, d)
    s, up_c = upload(CU, b"carl\n", "carl.txt")
    s, d = req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": up_c["file"]["url"], "name": "carl.txt"}]}})
    ok(f"fremde Datei (anderes Konto): 400 ({s})", s == 400, d)
    s, d = req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": "https://example.org/x.pdf", "name": "x"}]}})
    ok(f"externe URL: 400 ({s})", s == 400, d)
    many = [upload(A, b"x%d" % i, f"x{i}.txt")[1]["file"]["url"] for i in range(4)]
    s, d = req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": u, "name": "x"} for u in many]}})
    ok(f"mehr als max: 400 ({s})", s == 400 and "3" in str(d), d)
    s, d = req("GET", f"/api/notes/{db}/rows")
    r0 = d["rows"][0]
    ok("/rows: Datei-Werte mit Angaben, Formel mit Dateinamen", [x["name"] for x in r0["props"]["anh"]] == ["rechnung.pdf", "bild.png"]
       and r0["props"]["anh"][0]["size"] == 18 and r0["props"].get("n") == "rechnung.pdf | bild.png", r0)
    s, d = req("GET", f"/api/notes/{row}")
    ok(f"Einzelzeile mit Datei-Angaben ({s})", s == 200 and d["props"]["anh"][1]["image"] is True, d.get("props"))
    s, d = req("GET", f"/api/notes/{db}/rows" + "?q=rechnung.pdf")
    ok("Suche findet Dateinamen", s == 200 and d.get("matched") == 1, d.get("matched"))
    s, d = req("GET", f"/api/notes/{db}/rows" + "?" + urllib.parse.urlencode({"filters": json.dumps([{"pid": "anh", "op": "not_empty", "value": ""}])}))
    ok("Filter 'nicht leer'", s == 200 and d.get("matched") == 1, d)
    # Geteilt: Bert (Bearbeiten) laedt hoch - Datei gehoert Anna, Bert sieht sie; Carl nicht
    req("PUT", f"/api/notes/{db}/shares", body={"type": "user", "target": BU, "role": "edit"})
    s, ub = upload(BU, b"von bert\n", "bert.txt", note=row)
    with connect() as c:
        own = c.execute("SELECT uid FROM file WHERE id=?", (fid_of(ub["file"]["url"]),)).fetchone()[0]
    ok(f"Bearbeitende Person laedt hoch: Datei gehoert der Eigentuemerin ({s})", s == 200 and own == A, (s, own))
    s, d = req("PATCH", f"/api/notes/{row}", BU, {"props": {"anh": [{"url": f1, "name": "rechnung.pdf"}, {"url": ub["file"]["url"], "name": "bert.txt"}]}})
    ok(f"Bert setzt Werte ({s})", s == 200, d)
    s, _ = req("GET", f"/files/{fid_of(ub['file']['url'])}", BU)
    ok(f"Bert sieht die Datei ({s})", s == 200)
    s, _ = req("GET", f"/files/{fid_of(f1)}", CU)
    ok(f"Carl ohne Freigabe nicht ({s})", s == 404)
    # Export
    s, raw = req("GET", f"/api/notes/{db}/export.csv")
    ok(f"CSV: Dateinamen ({s})", s == 200 and "rechnung.pdf, bert.txt" in raw.decode("utf-8"), raw[:300])
    s, raw = req("GET", f"/api/notes/{row}/export")
    ok(f"Markdown: Dateinamen ({s})", s == 200 and "rechnung.pdf, bert.txt" in raw.decode("utf-8"), raw[:300])
    # Veroeffentlichen: Dateien nicht oeffentlich
    s, d = req("PUT", f"/api/notes/{db}/publish", body={"name": "fe" + os.urandom(4).hex()})
    if s == 200 and d.get("url"):
        c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
        c.request("GET", urllib.parse.urlparse(d["url"]).path, headers={"Host": HOST})
        html = c.getresponse().read().decode("utf-8", "replace")
        i = html.find("pub-db")
        ok("Veroeffentlicht: keine Datei-Spalte", "rechnung.pdf" not in html and "Anhänge" not in html and "Rechnung" in html,
           html[i:i + 800] if i >= 0 else html[-800:])
        req("DELETE", f"/api/notes/{db}/publish")
    else:
        ok("Veroeffentlichen moeglich", False, (s, d))
    # Duplizieren (gleiche Datenbank): Referenz bleibt
    s, d = req("POST", f"/api/notes/{row}/duplicate", body={"parent_id": db})
    s, d2 = req("GET", f"/api/notes/{d['id']}")
    ok(f"Duplizieren: gleiche Datei-Referenz ({s})", [x["url"] for x in d2["props"].get("anh", [])] == [f1, ub["file"]["url"]], d2.get("props"))
    # Entfernen + GC: Datei aus props weg -> nach GC geloescht; ueber die Kopie noch benutzt -> bleibt
    req("PATCH", f"/api/notes/{row}", body={"props": {"anh": [{"url": f2, "name": "bild.png"}]}})
    req("PATCH", f"/api/notes/{d['id']}", body={"props": {"anh": None}})
    gc_now()
    ok("GC: entfernte Datei weg, noch benutzte bleibt", not exists(fid_of(f1)) and exists(fid_of(f2)) and not exists(fid_of(ub["file"]["url"])),
       (exists(fid_of(f1)), exists(fid_of(f2))))
    s, d = req("GET", f"/api/notes/{row}")
    ok("fehlende Datei faellt beim Lesen weg (kein Fehler)", s == 200 and [x["url"] for x in d["props"]["anh"]] == [f2], d.get("props"))
    # Schema: Typ wechseln entfernt die Werte (wie bei anderen Typen)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1")
    sch = d["schema"]
    next(p for p in sch["props"] if p["id"] == "anh")["type"] = "text"
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    with connect() as c:
        st = json.loads(c.execute("SELECT props FROM note WHERE id=?", (row,)).fetchone()[0])
    ok("Typ files -> text: Liste faellt weg", "anh" not in st or isinstance(st["anh"], str), st)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'fe-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'fe-%' OR target LIKE 'fe-%' OR target LIKE '@fe-%'")
        c.execute("DELETE FROM file WHERE uid LIKE 'fe-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fe-%'")


if __name__ == "__main__":
    main()
    print(f"\nDATEI-EIGENSCHAFT {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
