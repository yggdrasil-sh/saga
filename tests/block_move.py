"""Saga 2.5 P6-4: Bloecke auf eine andere Seite verschieben (POST /api/notes/{a}/blocks/move).

Rechte (Bearbeiten auf beiden Seiten, sonst 403/404), Kinder (Spalten/Ausklappbar wandern mit), Kommentare (note_id,
Eigentuemer, umbenannte Block-IDs), Dateien (gleicher Eigentuemer: Verweis bleibt; anderer Eigentuemer: Kopie fuer den
Eigentuemer des Ziels, Kontingent), Signaturen (Schaltflaeche mit Vorlage), Versionen beider Seiten, veraltete Revision
(409, nichts geaendert), Gegenrichtung (Rueckgaengig), Position (after/at), gesperrte Seite, foederierte Ziele (verboten).
Wegwerf-Konten bm-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import auth  # noqa: E402
from db import connect  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return r.status, data


def upload(cookie, note=None):
    bd = "bm-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"t.png\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + PNG + f"\r\n--{bd}--\r\n".encode()
    h = {"Content-Type": f"multipart/form-data; boundary={bd}"}
    if note:
        h["X-Saga-Note"] = str(note)
    s, d = req("POST", "/api/files", cookie, raw_body=mp, hdr=h)
    return (d.get("file") or {}).get("url") if isinstance(d, dict) else None


def P(i, text):
    return {"id": i, "type": "paragraph", "data": {"text": text}}


def page(cookie, title, blocks):
    s, d = req("POST", "/api/notes", cookie, {"title": title})
    nid = d["id"]
    s, d = req("PATCH", f"/api/notes/{nid}", cookie, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)
    return nid


def get(cookie, nid):
    s, d = req("GET", f"/api/notes/{nid}", cookie)
    return d if s == 200 else None


def ids(d):
    return [b["id"] for b in json.loads(d["body"])["blocks"]]


def move(cookie, a, body):
    cur = get(cookie, a)
    body = dict(body)
    body.setdefault("rev_a", cur["rev"] if cur else 0)
    return req("POST", f"/api/notes/{a}/blocks/move", cookie, body)


def versions(nid):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=?", (nid,)).fetchone()[0]


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'bm-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "file"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'bm-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'bm-%' OR target LIKE 'bm-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'bm-%' OR uid LIKE 'bm-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'bm-%'")


def main():
    cleanup()
    prev_en = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u, n in (("bm-a", "Anna"), ("bm-b", "Bert"), ("bm-c", "Cora")):
        auth.create_user(u, "Move-Pass-1234", n)
    A, Bk, Ck = ck("bm-a"), ck("bm-b"), ck("bm-c")
    time.sleep(5.6)                                  # Einstellungen sind im Server zwischengespeichert

    # --- gleicher Eigentuemer: Kinder, Kommentare, Datei, Versionen, Position ----------------------------------------
    img = upload(A)
    ok("Bild hochgeladen", bool(img))
    src = page(A, "Quelle", [P("p1", "eins"), {"id": "cl", "type": "columns", "data": {"cols": [{"blocks": [P("k1", "links")]}, {"blocks": [P("k2", "rechts")]}]}},
                             {"id": "tg", "type": "toggle", "data": {"text": "auf", "blocks": [P("t1", "innen")]}},
                             {"id": "im", "type": "image", "data": {"file": {"url": img}, "caption": "Bild"}}, P("p9", "bleibt")])
    dst = page(A, "Ziel", [P("z1", "erster"), P("z2", "zweiter")])
    s, cm = req("POST", f"/api/notes/{src}/comments", A, {"body": "zum Toggle-Inhalt", "block_id": "t1"})
    ok(f"Kommentar am inneren Block ({s})", s == 200)
    s, cm2 = req("POST", f"/api/notes/{src}/comments", A, {"body": "zu p9", "block_id": "p9"})
    va, vb = versions(src), versions(dst)
    s, d = move(A, src, {"ids": ["cl", "tg", "im", "k1"], "to": dst, "after": "z1"})
    ok(f"Verschieben (gleicher Eigentuemer) -> {s} {d if s != 200 else ''}", s == 200 and d.get("files_copied") == 0)
    a, b = get(A, src), get(A, dst)
    ok(f"Quelle ohne die Bloecke: {ids(a)}", ids(a) == ["p1", "p9"])
    ok(f"Ziel: nach z1 eingefuegt, Reihenfolge der Quelle: {ids(b)}", ids(b) == ["z1", "cl", "tg", "im", "z2"])
    bb = json.loads(b["body"])["blocks"]
    ok("Kinder wandern mit (Spalten samt k1, Toggle samt t1)",
       [x["id"] for x in bb[1]["data"]["cols"][0]["blocks"]] == ["k1"] and bb[2]["data"]["blocks"][0]["id"] == "t1")
    ok("Bild-Verweis unveraendert (gleicher Eigentuemer)", bb[3]["data"]["file"]["url"] == img)
    ok(f"Revisionen beider Seiten +1 ({d.get('rev_a')}/{d.get('rev_b')})", d.get("rev_a") == a["rev"] and d.get("rev_b") == b["rev"])
    ok(f"Versionen beider Seiten gesichert ({va}->{versions(src)}, {vb}->{versions(dst)})", versions(src) > va and versions(dst) > vb)
    with connect() as c:
        r = c.execute("SELECT note_id, owner_uid, block_id FROM note_comment WHERE id=?", (cm["comment"]["id"],)).fetchone()
        r2 = c.execute("SELECT note_id FROM note_comment WHERE id=?", (cm2["comment"]["id"],)).fetchone()
    ok(f"Kommentar am inneren Block wandert mit ({r})", tuple(r) == (dst, "bm-a", "t1"))
    ok("Kommentar an nicht verschobenem Block bleibt", r2[0] == src)
    s, lst = req("GET", f"/api/notes/{dst}/comments", A)
    ok("Kommentar in der Zielseite sichtbar", s == 200 and any("Toggle-Inhalt" in (x.get("body") or "") for t in lst.get("threads", lst if isinstance(lst, list) else [])
                                                                for x in ([t] + t.get("replies", []) if isinstance(t, dict) else [])) or "Toggle-Inhalt" in json.dumps(lst))

    # --- Rueckgaengig (Gegenrichtung, after = alter Vorgaenger), Anfang ---------------------------------------------
    s, d = move(A, dst, {"ids": ["cl", "tg", "im"], "to": src, "after": "p1"})
    ok(f"Gegenrichtung -> {s}", s == 200 and ids(get(A, src)) == ["p1", "cl", "tg", "im", "p9"] and ids(get(A, dst)) == ["z1", "z2"])
    s, d = move(A, src, {"ids": ["p9"], "to": dst, "at": "start"})
    ok(f"at=start -> {ids(get(A, dst))}", s == 200 and ids(get(A, dst))[0] == "p9")
    s, d = move(A, src, {"ids": ["k2"], "to": dst, "after": "z2"})
    ok("Block aus einer Spalte heraus", s == 200 and "k2" in ids(get(A, dst)) and "k2" not in json.dumps(json.loads(get(A, src)["body"])))

    # --- ID-Kollision: neue ID, Kommentar folgt ----------------------------------------------------------------------
    s1 = page(A, "Kollision A", [P("dup", "aus A")])
    s2 = page(A, "Kollision B", [P("dup", "schon da")])
    s, cmk = req("POST", f"/api/notes/{s1}/comments", A, {"body": "an dup", "block_id": "dup"})
    s, d = move(A, s1, {"ids": ["dup"], "to": s2})
    new = (d.get("renamed") or {}).get("dup")
    ok(f"Kollision -> neue ID ({new})", s == 200 and new and new != "dup" and ids(get(A, s2)) == ["dup", new])
    with connect() as c:
        r = c.execute("SELECT note_id, block_id FROM note_comment WHERE id=?", (cmk["comment"]["id"],)).fetchone()
    ok("Kommentar folgt der neuen ID", tuple(r) == (s2, new))

    # --- veraltete Revision: 409, nichts geaendert --------------------------------------------------------------------
    p1 = page(A, "Rev A", [P("r1", "x"), P("r2", "y")])
    p2 = page(A, "Rev B", [P("q1", "z")])
    cur = get(A, p1)
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": p2, "rev_a": cur["rev"] - 1})
    ok(f"veraltete rev_a -> {s}", s == 409 and d.get("rev_a") == cur["rev"])
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": p2, "rev_a": cur["rev"], "rev_b": get(A, p2)["rev"] + 5})
    ok(f"veraltete rev_b -> {s}", s == 409)
    ok("nach 409 beide unveraendert", ids(get(A, p1)) == ["r1", "r2"] and ids(get(A, p2)) == ["q1"])
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["fehlt"], "to": p2, "rev_a": cur["rev"]})
    ok(f"unbekannte Block-ID -> {s}", s == 404)
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": p1, "rev_a": cur["rev"]})
    ok(f"Ziel = Quelle -> {s}", s == 400)
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": p2, "rev_a": cur["rev"], "after": "gibtsnicht"})
    ok(f"unbekannter after-Block -> {s}, nichts geaendert", s == 404 and ids(get(A, p1)) == ["r1", "r2"])
    for bad in ({"ids": [], "to": p2}, {"ids": ["<x>"], "to": p2}, {"ids": ["r1"], "to": "abc"}, {"ids": "r1", "to": p2}):
        s, _ = req("POST", f"/api/notes/{p1}/blocks/move", A, dict(bad, rev_a=cur["rev"]))
        ok(f"Unsinn {bad} -> {s}", s == 400)
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": -5, "rev_a": cur["rev"]})
    ok(f"foederiertes Ziel (negative ID) -> {s} federated", s == 400 and d.get("federated") is True)
    s, d = req("POST", f"/api/notes/-5/blocks/move", A, {"ids": ["r1"], "to": p2, "rev_a": 1})
    ok(f"foederierte Quelle -> {s}", s in (400, 404))
    req("PATCH", f"/api/notes/{p2}", A, {"locked": True})
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": p2, "rev_a": cur["rev"]})
    ok(f"gesperrtes Ziel -> {s}", s == 423)
    req("PATCH", f"/api/notes/{p2}", A, {"locked": False})
    db = req("POST", "/api/notes", A, {"title": "DB", "kind": "database"})[1]["id"]
    s, d = req("POST", f"/api/notes/{p1}/blocks/move", A, {"ids": ["r1"], "to": db, "rev_a": cur["rev"]})
    ok(f"Ziel Datenbank -> {s}", s == 400)

    # --- Rechte: B ohne / mit Lesen / mit Bearbeiten ------------------------------------------------------------------
    pa = page(A, "A geteilt", [P("a1", "von A"), P("a2", "zweiter")])
    pb = page(Bk, "B eigen", [P("b1", "von B")])
    s, _ = req("POST", f"/api/notes/{pa}/blocks/move", Bk, {"ids": ["a1"], "to": pb, "rev_a": get(A, pa)["rev"]})
    ok(f"B ohne Freigabe auf A -> {s}", s == 404)
    req("PUT", f"/api/notes/{pa}/shares", A, {"type": "user", "target": "bm-b", "role": "view"})
    s, _ = req("POST", f"/api/notes/{pa}/blocks/move", Bk, {"ids": ["a1"], "to": pb, "rev_a": get(A, pa)["rev"]})
    ok(f"B mit Lesen auf A -> {s}", s == 403)
    req("PUT", f"/api/notes/{pa}/shares", A, {"type": "user", "target": "bm-b", "role": "comment"})
    s, _ = req("POST", f"/api/notes/{pb}/blocks/move", Bk, {"ids": ["b1"], "to": pa, "rev_a": get(Bk, pb)["rev"]})
    ok(f"B mit Kommentieren als Ziel -> {s}", s == 403)
    s, _ = req("POST", f"/api/notes/{pb}/blocks/move", Ck, {"ids": ["b1"], "to": pa, "rev_a": get(Bk, pb)["rev"]})
    ok(f"C ohne Zugriff auf beide -> {s}", s == 404)
    ok("nach allen Ablehnungen unveraendert", ids(get(A, pa)) == ["a1", "a2"] and ids(get(Bk, pb)) == ["b1"])

    # --- anderer Eigentuemer: Datei wird fuer den Eigentuemer des Ziels kopiert ---------------------------------------
    req("PUT", f"/api/notes/{pa}/shares", A, {"type": "user", "target": "bm-b", "role": "edit"})
    imgb = upload(Bk)
    pb2 = page(Bk, "B mit Bild", [P("x1", "text"), {"id": "bi", "type": "image", "data": {"file": {"url": imgb}, "caption": ""}},
                                  {"id": "mm", "type": "code", "data": {"code": "graph TD; A-->B", "language": "mermaid",
                                                                        "render": {"png": imgb, "h": "0123456789abcdef"}}}])
    s, cmb = req("POST", f"/api/notes/{pb2}/comments", Bk, {"body": "Bildkommentar", "block_id": "bi"})
    s, d = req("POST", f"/api/notes/{pb2}/blocks/move", Bk, {"ids": ["bi", "mm"], "to": pa, "rev_a": get(Bk, pb2)["rev"], "after": "a1"})
    ok(f"B verschiebt in As Seite -> {s} kopiert {d.get('files_copied') if isinstance(d, dict) else d}", s == 200 and d.get("files_copied") == 1)
    ba = json.loads(get(A, pa)["body"])["blocks"]
    url = ba[1]["data"]["file"]["url"] if len(ba) > 1 and ba[1].get("type") == "image" else ""
    with connect() as c:
        fo = c.execute("SELECT uid FROM file WHERE id=?", (url.rsplit("/", 1)[-1],)).fetchone()
        old = c.execute("SELECT uid FROM file WHERE id=?", (imgb.rsplit("/", 1)[-1],)).fetchone()
        r = c.execute("SELECT note_id, owner_uid FROM note_comment WHERE id=?", (cmb["comment"]["id"],)).fetchone()
    ok(f"Kopie gehoert A ({url} -> {fo})", url != imgb and fo and fo[0] == "bm-a")
    ok("Original bleibt bei B", old and old[0] == "bm-b")
    ok("Vorschaubild zeigt auf dieselbe Kopie", ba[2]["data"]["render"]["png"] == url)
    ok(f"Kommentar: Seite und Eigentuemer umgeschrieben ({r})", tuple(r) == (pa, "bm-a"))
    s, raw = req("GET", url, A)
    ok(f"A kann die Kopie laden ({s})", s == 200)

    # --- Kontingent des Ziels: 507, nichts geaendert ------------------------------------------------------------------
    pb3 = page(Bk, "B Kontingent", [{"id": "bq", "type": "image", "data": {"file": {"url": imgb}, "caption": ""}}])
    with connect() as c, c:
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  ("f" * 24, "bm-a", "gross", "application/octet-stream", 10 ** 12, 0, time.time()))
    before = ids(get(A, pa))
    s, d = req("POST", f"/api/notes/{pb3}/blocks/move", Bk, {"ids": ["bq"], "to": pa, "rev_a": get(Bk, pb3)["rev"]})
    ok(f"Kontingent voll -> {s}", s == 507 and d.get("quota") is True)
    ok("nach 507 beide unveraendert", ids(get(A, pa)) == before and ids(get(Bk, pb3)) == ["bq"])
    with connect() as c, c:
        c.execute("DELETE FROM file WHERE id=?", ("f" * 24,))

    # --- Schaltflaeche mit Vorlage: Signatur nur bei gleichem Eigentuemer ---------------------------------------------
    dbx = req("POST", "/api/notes", A, {"title": "Aufgaben", "kind": "database"})[1]["id"]
    tpl = req("POST", "/api/notes", A, {"title": "Vorlage", "parent_id": dbx})[1]["id"]
    req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    btn = {"id": "bt", "type": "buttons", "data": {"buttons": [{"label": "Neu", "action": "db", "target": dbx, "tpl": tpl}]}}
    q1 = page(A, "Knopf A", [btn, P("k0", "x")])
    q2 = page(A, "Knopf Ziel", [P("k9", "y")])
    sig1 = json.loads(get(A, q1)["body"])["blocks"][0]["data"]["buttons"][0].get("sig")
    s, d = move(A, q1, {"ids": ["bt"], "to": q2})
    sig2 = json.loads(get(A, q2)["body"])["blocks"][-1]["data"]["buttons"][0].get("sig")
    ok(f"Schaltflaeche: neu signiert fuer das Ziel ({bool(sig1)} -> {bool(sig2)})", s == 200 and sig1 and sig2 and sig1 != sig2)
    s, d = move(A, q2, {"ids": ["bt"], "to": pa})       # pa gehoert A, B darf bearbeiten - A verschiebt: bleibt signiert
    ok("Schaltflaeche in andere eigene Seite: signiert", s == 200 and d.get("unsigned") == 0)

    print(f"\n{sum(res)}/{len(res)} OK")
    auth.save_setting("share_enabled", prev_en)
    cleanup()
    print("ALLES GRUEN" if all(res) else "FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
