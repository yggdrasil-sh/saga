"""Teilen mit Personen und Gruppen + Kommentare (1.3).

Zuerst Isolation: Konto B sieht und aendert ohne Freigabe NICHTS von A - ueber jeden Endpunkt der Bestandsliste
(Seiten, Unterseiten, Datenbank-Zeilen, Dateien, Suche, Export, Versionen, Papierkorb, Freigaben, Kommentare) und mit
jeder ID-Variante. Danach jede Rolle (Lesen, Kommentieren, Bearbeiten) mit genau ihren Rechten; Entzug, Gruppenwechsel,
Abschalten im Admin und Kontoloeschung wirken sofort; Grenzen (2000 Zeichen, 30 Kommentare/Minute, 200 Freigaben);
Seitenleiste mit 500 Seiten als Mengen-Abfrage.
Wegwerf-Konten sh-*, Gruppen sh-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, hashlib, http.client, io, json, os, sys, time, urllib.parse, zipfile
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Share-Pass-1234"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
res = []
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_body=None):
    """-> (status, daten: JSON, Text oder Bytes)"""
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    ct = r.getheader("content-type") or ""
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, data


def st(*a, **k):
    return req(*a, **k)[0]


def upload(cookie, note=None):
    bd = "sh-test"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"t.png\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + PNG + f"\r\n--{bd}--\r\n".encode()
    h = {"Content-Type": f"multipart/form-data; boundary={bd}"}
    if note:
        h["X-Saga-Note"] = str(note)
    s, d = req("POST", "/api/files", cookie, raw_body=mp, hdr=h)
    return s, (d.get("file") or {}).get("url") if isinstance(d, dict) else None


def blocks(*bl):
    return json.dumps({"blocks": list(bl)})


def P(text, bid=None):
    b = {"type": "paragraph", "data": {"text": text}}
    if bid:
        b["id"] = bid
    return b


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'sh-%'").fetchall()]
    for u in users:
        delete_user_data(u)                         # auch Dateien auf der Platte
    with connect() as c, c:
        for t in ("task", "note", "note_version", "user_kv", "file", "note_publish", "public_name"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'sh-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sh-%' OR target LIKE 'sh-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'sh-%' OR uid LIKE 'sh-%'")
        c.execute("DELETE FROM note_comment_seen WHERE uid LIKE 'sh-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sh-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'sh-%'")


def gid_of(u):
    return auth.get_user(u)["id"]


def denied(name, s):
    ok(f"B ohne Freigabe: {name} -> {s}", s in (403, 404))


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en, prev_scope = prev.get("share_enabled", True), prev.get("share_group_scope", False)
    auth.save_setting("share_enabled", True); auth.save_setting("share_group_scope", False)
    auth.create_user("sh-a", PW, "Anna Teilt")
    auth.create_user("sh-b", PW, "Bert Liest")
    auth.create_user("sh-c", PW, "Cora")
    auth.create_user("sh-off", PW, "Gesperrt Person")
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=1 WHERE username='sh-off'")
    A, Bk, Ck = ck("sh-a"), ck("sh-b"), ck("sh-c")
    time.sleep(WAIT)
    nid = lambda d: d["id"]

    # --- A legt Daten an -------------------------------------------------------------------------------------
    s, img = upload(A)
    ok(f"A: Bild hochgeladen ({s})", s == 200 and img)
    root = nid(req("POST", "/api/notes", A, {"title": "Projekt Teilen", "icon": "📁", "tags": ["geheimtag-sh"]})[1])
    req("PATCH", f"/api/notes/{root}", A, {"body": blocks(P("zauberwort-sh steht hier", "blk1"), P("zweiter Block", "blk2"),
                                                          {"type": "image", "data": {"file": {"url": img}, "caption": ""}}), "format": "blocks"})
    child = nid(req("POST", "/api/notes", A, {"title": "Kapitel", "parent_id": root})[1])
    grand = nid(req("POST", "/api/notes", A, {"title": "Abschnitt", "parent_id": child})[1])
    arch = nid(req("POST", "/api/notes", A, {"title": "Altes Kapitel", "parent_id": root})[1])
    req("POST", f"/api/notes/{arch}/archive", A, {"archived": True})
    tpl = nid(req("POST", "/api/notes", A, {"title": "Vorlage drin", "parent_id": root})[1])
    req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    dbid = nid(req("POST", "/api/notes", A, {"title": "Aufgabenliste", "kind": "database", "parent_id": root})[1])
    row1 = nid(req("POST", "/api/notes", A, {"title": "Zeile eins", "parent_id": dbid, "props": {"status": "todo"}})[1])
    row2 = nid(req("POST", "/api/notes", A, {"title": "Zeile zwei", "parent_id": dbid, "props": {"status": "done"}})[1])
    rowarch = nid(req("POST", "/api/notes", A, {"title": "Zeile archiviert", "parent_id": dbid})[1])
    req("POST", f"/api/notes/{rowarch}/archive", A, {"archived": True})
    dbtpl = nid(req("POST", "/api/notes", A, {"title": "Zeilen-Vorlage", "parent_id": dbid})[1])
    req("PATCH", f"/api/notes/{dbtpl}", A, {"template": True})
    priv = nid(req("POST", "/api/notes", A, {"title": "Privat von A"})[1])
    req("PATCH", f"/api/notes/{priv}", A, {"body": blocks(P(f'Link auf <a href="/notes#{root}">Projekt</a>')), "format": "blocks"})
    privdb = nid(req("POST", "/api/notes", A, {"title": "Private DB", "kind": "database"})[1])
    trash = nid(req("POST", "/api/notes", A, {"title": "Weg damit", "parent_id": root})[1])
    req("DELETE", f"/api/notes/{trash}", A)
    with connect() as c, c:
        c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created) VALUES (?,?,?,?,?,?)",
                  (root, "sh-a", "alt", blocks(), "blocks", time.time() - 3600))
        ver = c.execute("SELECT id FROM note_version WHERE note_id=? AND uid='sh-a'", (root,)).fetchone()[0]
    s, d = req("POST", f"/api/notes/{root}/comments", A, {"block_id": "blk1", "body": "Notiz an mich selbst"})
    ok(f"A: Kommentar auf eigener, nicht geteilter Seite ({s})", s == 200 and d["comment"]["body"] == "Notiz an mich selbst")
    acm = d["comment"]["id"] if s == 200 else 0

    def snap():
        with connect() as c:
            return (c.execute("SELECT id, title, body, props, schema, parent_id, deleted_at, archived, tags, icon, cover, pinned, locked, is_template "
                              "FROM note WHERE uid='sh-a' ORDER BY id").fetchall(),
                    c.execute("SELECT COUNT(*) FROM note WHERE uid='sh-b'").fetchone()[0],
                    c.execute("SELECT id, body, resolved_at FROM note_comment WHERE owner_uid='sh-a' ORDER BY id").fetchall(),
                    c.execute("SELECT COUNT(*) FROM note_share WHERE owner_uid='sh-a'").fetchone()[0],
                    c.execute("SELECT COUNT(*) FROM file WHERE uid='sh-a'").fetchone()[0])
    before = snap()

    # --- 1. Isolation: B ohne Freigabe -------------------------------------------------------------------------
    all_a = [root, child, grand, arch, tpl, dbid, row1, row2, rowarch, dbtpl, priv, privdb, trash]
    for i in all_a:
        denied(f"GET /api/notes/{i}", st("GET", f"/api/notes/{i}", Bk))
    for i in (0, 9223372036854775807, 999999999999):
        s = st("GET", f"/api/notes/{i}", Bk)
        ok(f"B: unbekannte ID {i} -> {s} (kein Serverfehler)", s == 404)
    ok("B: ungueltige ID 'abc' -> 422/404", st("GET", "/api/notes/abc", Bk) in (404, 422))
    denied("Zeilen der Datenbank", st("GET", f"/api/notes/{dbid}/rows", Bk))
    denied("Rueckverweise", st("GET", f"/api/notes/{root}/backlinks", Bk))
    denied("Versionen", st("GET", f"/api/notes/{root}/versions", Bk))
    denied("Version lesen", st("GET", f"/api/notes/{root}/versions/{ver}", Bk))
    denied("Version wiederherstellen", st("POST", f"/api/notes/{root}/versions/{ver}/restore", Bk))
    denied("Export", st("GET", f"/api/notes/{root}/export", Bk))
    denied("Export mit Unterseiten", st("GET", f"/api/notes/{root}/export?sub=1", Bk))
    denied("Bild von A", st("GET", img, Bk))
    denied("Titel aendern", st("PATCH", f"/api/notes/{root}", Bk, {"title": "gehackt"}))
    denied("Inhalt aendern", st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(), "format": "blocks"}))
    denied("Zeilenwerte aendern", st("PATCH", f"/api/notes/{row1}", Bk, {"props": {"status": "done"}}))
    denied("Schema aendern", st("PATCH", f"/api/notes/{dbid}", Bk, {"schema": {"props": [], "views": []}}))
    denied("Favorit setzen", st("PATCH", f"/api/notes/{root}", Bk, {"pinned": True}))
    denied("Unterseite anlegen", st("POST", "/api/notes", Bk, {"title": "x", "parent_id": root}))
    denied("Zeile anlegen", st("POST", "/api/notes", Bk, {"title": "x", "parent_id": dbid, "props": {"status": "done"}}))
    denied("Loeschen", st("DELETE", f"/api/notes/{root}", Bk))
    denied("Archivieren", st("POST", f"/api/notes/{child}/archive", Bk, {"archived": True}))
    denied("Verschieben", st("POST", f"/api/notes/{child}/move", Bk, {"parent_id": None, "index": 0}))
    denied("Duplizieren", st("POST", f"/api/notes/{root}/duplicate", Bk, {}))
    denied("Papierkorb wiederherstellen", st("POST", f"/api/trash/{trash}/restore", Bk))
    denied("Papierkorb endgueltig loeschen", st("DELETE", f"/api/trash/{trash}", Bk))
    denied("Veroeffentlichen", st("PUT", f"/api/notes/{root}/publish", Bk, {}))
    denied("Freigaben lesen", st("GET", f"/api/notes/{root}/shares", Bk))
    denied("Freigabe anlegen (sich selbst)", st("PUT", f"/api/notes/{root}/shares", Bk, {"type": "user", "target": "sh-b", "role": "edit"}))
    denied("Freigabe loeschen", st("DELETE", f"/api/notes/{root}/shares?type=user&target=sh-c", Bk))
    denied("Kommentare lesen", st("GET", f"/api/notes/{root}/comments", Bk))
    denied("Kommentar schreiben", st("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "hallo"}))
    denied("Antwort auf A's Unterhaltung", st("POST", f"/api/notes/{root}/comments", Bk, {"thread_id": acm, "body": "hallo"}))
    denied("A's Kommentar bearbeiten", st("PATCH", f"/api/comments/{acm}", Bk, {"body": "x"}))
    denied("A's Kommentar loeschen", st("DELETE", f"/api/comments/{acm}", Bk))
    denied("A's Unterhaltung erledigen", st("POST", f"/api/comments/{acm}/resolve", Bk, {"resolved": True}))
    denied("A's Unterhaltung als gelesen", st("POST", f"/api/comments/{acm}/seen", Bk))
    for i in (0, 9223372036854775807):
        ok(f"B: unbekannte Kommentar-ID {i} -> 404", st("PATCH", f"/api/comments/{i}", Bk, {"body": "x"}) == 404
           and st("DELETE", f"/api/comments/{i}", Bk) == 404)
    s, _ = upload(Bk, note=root)
    denied("Upload in A's Seite", s)
    bp = nid(req("POST", "/api/notes", Bk, {"title": "B eigene Seite"})[1])
    denied("Kommentar ueber eigene Seite auf A's Unterhaltung", st("POST", f"/api/notes/{bp}/comments", Bk, {"thread_id": acm, "body": "x"}))
    denied("eigene Seite unter A's Seite haengen", st("PATCH", f"/api/notes/{bp}", Bk, {"parent_id": root}))
    ok("B: A's Bild in eigene Seite -> 400",
       st("PATCH", f"/api/notes/{bp}", Bk, {"body": blocks({"type": "image", "data": {"file": {"url": img}, "caption": ""}}), "format": "blocks"}) == 400)
    ids_b = {n["id"] for n in req("GET", "/api/notes", Bk)[1]}
    ok("B: Seitenbaum ohne A", not ids_b & set(all_a))
    ok("B: /api/shared leer", req("GET", "/api/shared", Bk)[1] == [])
    ok("B: Suche findet A's Text nicht", req("POST", "/api/notes/search", Bk, {"q": "zauberwort-sh"})[1] == [])
    ok("B: Suche nach A's Tag leer", req("POST", "/api/notes/search", Bk, {"q": "", "tag": "geheimtag-sh"})[1] == [])
    ok("B: Papierkorb ohne A", all(n["id"] != trash for n in req("GET", "/api/trash", Bk)[1]))
    ok("A's Daten nach allen Versuchen unveraendert", snap()[0] == before[0] and snap()[2:] == before[2:])

    # --- 2. Freigabe "Lesen" an B ------------------------------------------------------------------------------
    s, d = req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "view"})
    ok(f"A teilt mit B (Lesen) -> {s}", s == 200 and any(x["target"] == "sh-b" and x["role"] == "view" and x["name"] == "Bert Liest" for x in d["shares"]))
    s, d = req("GET", f"/api/notes/{root}", Bk)
    ok(f"B liest: role=view, Eigentuemer-Name ({s})", s == 200 and d.get("role") == "view" and d["owner"]["name"] == "Anna Teilt"
       and d["owner"]["me"] is False and d.get("shared") is True and "zauberwort-sh" in d["body"])
    ok("B: Veroeffentlichungs-Details nur fuer den Eigentuemer", d.get("public_url") is None)
    ok("B: Kommentar-Zaehler sichtbar", d.get("comment_counts", {}).get("blk1") == 1 and d.get("comments_open") == 1)
    for i, name in ((child, "Unterseite"), (grand, "Unter-Unterseite"), (dbid, "Datenbank"), (row1, "Zeile")):
        s, d = req("GET", f"/api/notes/{i}", Bk)
        ok(f"B: {name} geerbt -> {s} {d.get('role') if isinstance(d, dict) else ''}", s == 200 and d.get("role") == "view")
    for i, name in ((arch, "archivierte Unterseite"), (tpl, "Vorlage"), (rowarch, "archivierte Zeile"), (dbtpl, "Zeilen-Vorlage"),
                    (priv, "andere private Seite"), (trash, "Seite im Papierkorb"), (privdb, "private Datenbank")):
        ok(f"B: {name} bleibt verborgen", st("GET", f"/api/notes/{i}", Bk) == 404)
    s, d = req("GET", f"/api/notes/{dbid}/rows", Bk)
    rids = {r["id"] for r in d.get("rows", [])} if s == 200 else set()
    ok(f"B: Zeilen ohne archivierte, ohne Vorlagen ({s})", s == 200 and rids == {row1, row2} and d.get("templates") == [] and d.get("role") == "view")
    sh = req("GET", "/api/shared", Bk)[1]
    shid = {x["id"]: x for x in sh}
    ok(f"B: /api/shared: Wurzel + geerbte Seiten ({len(sh)})", root in shid and shid[root]["root"] is True and shid[root]["role"] == "view"
       and shid[root]["owner"]["name"] == "Anna Teilt" and child in shid and not shid[child]["root"] and grand in shid and dbid in shid
       and not {arch, tpl, rowarch, dbtpl, priv, trash, privdb} & set(shid))
    hits = req("POST", "/api/notes/search", Bk, {"q": "zauberwort-sh"})[1]
    ok("B: Suche findet geteilte Seite (mit Rolle/Eigentuemer)", [h["id"] for h in hits] == [root] and hits[0].get("role") == "view")
    ok("B: Suche nach Tag der geteilten Seite", [h["id"] for h in req("POST", "/api/notes/search", Bk, {"q": "", "tag": "geheimtag-sh"})[1]] == [root])
    ok("B: Suche findet private Seite von A nicht", req("POST", "/api/notes/search", Bk, {"q": "Privat von A"})[1] == [])
    s, d = req("GET", f"/api/notes/{root}/backlinks", Bk)
    ok(f"B: Rueckverweise ohne A's private Seite ({s})", s == 200 and all(x["id"] != priv for x in d))
    ok("A: Rueckverweis von privater Seite sichtbar", any(x["id"] == priv for x in req("GET", f"/api/notes/{root}/backlinks", A)[1]))
    s, d = req("GET", img, Bk)
    ok(f"B: Bild der geteilten Seite -> {s}", s == 200)
    s, md = req("GET", f"/api/notes/{root}/export", Bk)
    ok(f"B: Export (Lesen) -> {s}", s == 200 and "zauberwort-sh" in (md if isinstance(md, str) else md.decode()))
    s, z = req("GET", f"/api/notes/{root}/export?sub=1", Bk)
    names = zipfile.ZipFile(io.BytesIO(z)).namelist() if s == 200 else []
    ok(f"B: ZIP-Export nur sichtbare Seiten ({len(names)})", s == 200 and any("Kapitel" in n for n in names)
       and not any("Altes Kapitel" in n or "Vorlage drin" in n or "Weg damit" in n for n in names))
    for name, (m, p, b) in {
            "Titel aendern": ("PATCH", f"/api/notes/{root}", {"title": "x"}),
            "Inhalt aendern": ("PATCH", f"/api/notes/{root}", {"body": blocks(), "format": "blocks"}),
            "Zeilenwerte": ("PATCH", f"/api/notes/{row1}", {"props": {"status": "done"}}),
            "Unterseite anlegen": ("POST", "/api/notes", {"title": "x", "parent_id": root}),
            "Kommentieren": ("POST", f"/api/notes/{root}/comments", {"block_id": "blk1", "body": "x"}),
            "Erledigen": ("POST", f"/api/comments/{acm}/resolve", {"resolved": True}),
            "Versionen": ("GET", f"/api/notes/{root}/versions", None),
            "Loeschen": ("DELETE", f"/api/notes/{child}", None),
            "Verschieben": ("POST", f"/api/notes/{child}/move", {"parent_id": None, "index": 0}),
            "Archivieren": ("POST", f"/api/notes/{child}/archive", {"archived": True}),
            "Duplizieren": ("POST", f"/api/notes/{root}/duplicate", {}),
            "Freigaben lesen": ("GET", f"/api/notes/{root}/shares", None),
            "Veroeffentlichen": ("PUT", f"/api/notes/{root}/publish", {}),
    }.items():
        s = st(m, p, Bk, b)
        ok(f"B (Lesen): {name} -> {s}", s == 403)
    s, _ = upload(Bk, note=root)
    ok(f"B (Lesen): Upload in die Seite -> {s}", s == 403)
    s, d = req("GET", f"/api/notes/{root}/comments", Bk)
    ok(f"B (Lesen): Kommentare lesen ({s})", s == 200 and d["can_comment"] is False and d["threads"][0]["comments"][0]["author"]["name"] == "Anna Teilt")
    ok("C (ohne Freigabe) sieht weiter nichts", st("GET", f"/api/notes/{root}", Ck) == 404 and req("GET", "/api/shared", Ck)[1] == [])
    ok("A: Seitenbaum markiert geteilte Seite", next(n for n in req("GET", "/api/notes", A)[1] if n["id"] == root).get("shared") is True)

    # --- 3. Kommentieren -------------------------------------------------------------------------------------------
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "comment"})
    ok("Rolle geaendert (eine Freigabe, nicht zwei)", len(req("GET", f"/api/notes/{root}/shares", A)[1]["shares"]) == 1)
    s, d = req("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk2", "body": "Frage zu Block 2:\nhttps://example.org/x"})
    ok(f"B (Kommentieren): neue Unterhaltung ({s})", s == 200 and d["comment"]["author"]["me"] is True)
    bth = d["comment"]["id"] if s == 200 else 0
    s, d = req("POST", f"/api/notes/{child}/comments", Bk, {"block_id": "", "body": "Kommentar zur Unterseite"})
    ok(f"B: Kommentar auf geerbter Unterseite ({s})", s == 200)
    s, d = req("POST", f"/api/notes/{root}/comments", Bk, {"thread_id": acm, "body": "Antwort von B"})
    ok(f"B: Antwort in A's Unterhaltung ({s})", s == 200)
    brep = d["comment"]["id"] if s == 200 else 0
    s, d = req("POST", f"/api/notes/{root}/comments", Bk, {"thread_id": brep, "body": "Antwort auf Antwort"})
    ok(f"Antwort auf eine Antwort landet in derselben Unterhaltung ({s})", s == 200 and d.get("thread_id") == acm)
    ok("B: Titel aendern bleibt verboten", st("PATCH", f"/api/notes/{root}", Bk, {"title": "x"}) == 403)
    ok("B: A's Kommentar bearbeiten -> 403", st("PATCH", f"/api/comments/{acm}", Bk, {"body": "x"}) == 403)
    ok("B: A's Kommentar loeschen -> 403", st("DELETE", f"/api/comments/{acm}", Bk) == 403)
    s, d = req("PATCH", f"/api/comments/{brep}", Bk, {"body": "Antwort von B (bearbeitet)"})
    ok(f"B: eigenen Kommentar bearbeiten ({s})", s == 200 and d["comment"]["edited"])
    ok("Leerer Kommentar -> 400", st("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "   "}) == 400)
    ok("Kommentar > 2000 Zeichen -> 400", st("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "x" * 2001}) == 400)
    ok("Unsinnige Block-ID -> 400", st("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "<script>", "body": "x"}) == 400)
    ok("Antwort auf Unterhaltung einer anderen Seite -> 400/404",
       st("POST", f"/api/notes/{child}/comments", Bk, {"thread_id": acm, "body": "x"}) in (400, 404))
    s, d = req("GET", f"/api/notes/{root}/comments", A)
    th = {t["id"]: t for t in d["threads"]} if s == 200 else {}
    ok(f"A: sieht B's Unterhaltung ungelesen, eigene mit Antwort ungelesen ({s})", th.get(bth, {}).get("unread") is True
       and th.get(acm, {}).get("unread") is True and len(th.get(acm, {}).get("comments", [])) == 3)
    ok("Klartext: Zeilenumbruch bleibt, kein HTML", th.get(bth, {}).get("comments", [{}])[0].get("body") == "Frage zu Block 2:\nhttps://example.org/x")
    ok("A: Zaehler zeigt 2 offene Unterhaltungen + Ungelesene", (lambda n: n["comments_open"] == 2 and n["comments_unread"] == 2
                                                                   and n["comment_counts"] == {"blk1": 1, "blk2": 1})(req("GET", f"/api/notes/{root}", A)[1]))
    ok("A: als gelesen markieren", st("POST", f"/api/comments/{bth}/seen", A) == 200
       and req("GET", f"/api/notes/{root}", A)[1]["comments_unread"] == 1)
    s, d = req("POST", f"/api/comments/{bth}/resolve", Bk, {"resolved": True})
    ok(f"B: Unterhaltung erledigt ({s})", s == 200 and d.get("resolved_at"))
    d = req("GET", f"/api/notes/{root}/comments", A)[1]
    ok("Erledigte fehlen ohne Filter, Anzahl gemeldet", all(t["id"] != bth for t in d["threads"]) and d["resolved_count"] == 1)
    d = req("GET", f"/api/notes/{root}/comments?resolved=1", A)[1]
    t2 = next((t for t in d["threads"] if t["id"] == bth), {})
    ok("Filter 'Erledigte zeigen': mit Erledigt von wem", t2.get("resolved_at") and t2.get("resolved_by", {}).get("name") == "Bert Liest")
    ok("Zaehler am Block nur offene", req("GET", f"/api/notes/{root}", A)[1]["comment_counts"] == {"blk1": 1})
    req("POST", f"/api/comments/{bth}/resolve", A, {"resolved": False})
    ok("Wieder geoeffnet", req("GET", f"/api/notes/{root}", A)[1]["comments_open"] == 2)
    # Block entfernt: Unterhaltung bleibt, als "Block entfernt" erkennbar
    req("PATCH", f"/api/notes/{root}", A, {"body": blocks(P("zauberwort-sh steht hier", "blk1"),
                                                          {"type": "image", "data": {"file": {"url": img}, "caption": ""}}), "format": "blocks"})
    t3 = next((t for t in req("GET", f"/api/notes/{root}/comments", A)[1]["threads"] if t["id"] == bth), {})
    ok("Block geloescht -> Unterhaltung bleibt, block_exists=false", t3 and t3.get("block_exists") is False)
    ok("Eigentuemer darf fremden Kommentar loeschen (Moderation)", st("DELETE", f"/api/comments/{brep}", A) == 200)

    # --- 4. Bearbeiten ------------------------------------------------------------------------------------------------
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "edit"})
    n = req("GET", f"/api/notes/{root}", Bk)[1]
    ok("B: role=edit", n["role"] == "edit")
    with connect() as c, c:                          # letzte Sicherung > 10 Minuten her -> naechstes Speichern sichert
        c.execute("UPDATE note_version SET created=created-7200 WHERE note_id=?", (root,))
        nver = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=? AND uid='sh-a'", (root,)).fetchone()[0]
    s, d = req("PATCH", f"/api/notes/{root}", Bk, {"title": "Projekt Teilen (von B)", "rev": n["rev"],
                                                   "body": blocks(P("zauberwort-sh steht hier", "blk1"), P("B schreibt mit")), "format": "blocks"})
    ok(f"B (Bearbeiten): Titel + Inhalt mit rev ({s})", s == 200)
    with connect() as c:
        r = c.execute("SELECT uid, title, updated_by FROM note WHERE id=?", (root,)).fetchone()
        nver2 = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=? AND uid='sh-a'", (root,)).fetchone()[0]
        nver_b = c.execute("SELECT COUNT(*) FROM note_version WHERE uid='sh-b'").fetchone()[0]
    ok("Seite gehoert weiter A, zuletzt geaendert von B", r[0] == "sh-a" and r[1] == "Projekt Teilen (von B)" and r[2] == "sh-b")
    ok(f"Version beim Eigentuemer ({nver} -> {nver2}), keine bei B", nver2 == nver + 1 and nver_b == 0)
    s, d = req("PATCH", f"/api/notes/{root}", A, {"title": "A schreibt", "rev": n["rev"]})
    ok(f"Konflikt fuer A nennt B ({s}: {d.get('by') if isinstance(d, dict) else ''})", s == 409 and d.get("by") == "Bert Liest")
    ok("B: Tags, Symbol, Breite", st("PATCH", f"/api/notes/{root}", Bk, {"tags": ["geheimtag-sh", "neu"], "icon": "🗂️", "wide": True}) == 200)
    ok("B: Zeilenwert aendern", st("PATCH", f"/api/notes/{row1}", Bk, {"props": {"status": "doing"}}) == 200)
    s, d = req("POST", "/api/notes", Bk, {"title": "Neues Kapitel von B", "parent_id": root})
    with connect() as c:
        owner_new = c.execute("SELECT uid FROM note WHERE id=?", (d.get("id", 0),)).fetchone() if s == 200 else None
    ok(f"B: Unterseite anlegen -> gehoert A ({s})", s == 200 and owner_new and owner_new[0] == "sh-a")
    bchild = d.get("id") if s == 200 else 0
    s, d = req("POST", "/api/notes", Bk, {"title": "Zeile von B", "parent_id": dbid, "props": {"status": "todo"}})
    ok(f"B: Datenbank-Zeile anlegen ({s})", s == 200)
    s, url = upload(Bk, note=root)
    with connect() as c:
        fo = c.execute("SELECT uid FROM file WHERE id=?", (url.rsplit("/", 1)[1],)).fetchone() if url else None
    ok(f"B: Bild hochladen -> Datei gehoert A ({s})", s == 200 and fo and fo[0] == "sh-a")
    ok("B: hochgeladenes Bild als Titelbild", st("PATCH", f"/api/notes/{root}", Bk, {"cover": url, "cover_pos": 30}) == 200)
    ok("B: hochgeladenes Bild im Inhalt", st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("zauberwort-sh"), {"type": "image", "data": {"file": {"url": url}, "caption": ""}}), "format": "blocks"}) == 200)
    s, burl = upload(Bk)
    ok("B: eigenes (B-)Bild als Titelbild von A's Seite -> 400", st("PATCH", f"/api/notes/{root}", Bk, {"cover": burl}) == 400)
    ok("B: eigenes (B-)Bild im Inhalt von A's Seite -> 400",
       st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks({"type": "image", "data": {"file": {"url": burl}, "caption": ""}}), "format": "blocks"}) == 400)
    sch = req("GET", f"/api/notes/{dbid}", Bk)[1]["schema"]
    sch["views"][0]["sorts"] = [{"pid": "title", "dir": "desc"}]
    ok("B: Ansicht der Datenbank aendern (Schema)", st("PATCH", f"/api/notes/{dbid}", Bk, {"schema": sch}) == 200)
    sch3 = json.loads(json.dumps(sch)); sch3["locked"] = True
    ok("B: Datenbank sperren nur Eigentuemer -> 403", st("PATCH", f"/api/notes/{dbid}", Bk, {"schema": sch3}) == 403)
    sch2 = json.loads(json.dumps(sch)); sch2["props"].append({"id": "rel", "name": "R", "type": "relation", "target": privdb})
    ok("B: Relation auf A's private Datenbank -> 403/400", st("PATCH", f"/api/notes/{dbid}", Bk, {"schema": sch2}) in (400, 403))
    for name, body in (("Favorit", {"pinned": True}), ("Vorlage", {"template": True}), ("Sperren", {"locked": True}), ("Verschieben", {"parent_id": None})):
        ok(f"B (Bearbeiten): {name} nur Eigentuemer -> 403", st("PATCH", f"/api/notes/{child}", Bk, body) == 403)
    for name, (m, p, b) in {"Loeschen": ("DELETE", f"/api/notes/{bchild}", None), "Verschieben": ("POST", f"/api/notes/{child}/move", {"parent_id": None, "index": 0}),
                            "Archivieren": ("POST", f"/api/notes/{child}/archive", {"archived": True}),
                            "Duplizieren": ("POST", f"/api/notes/{root}/duplicate", {}), "Freigaben lesen": ("GET", f"/api/notes/{root}/shares", None),
                            "Freigabe weitergeben": ("PUT", f"/api/notes/{root}/shares", {"type": "user", "target": "sh-c", "role": "view"}),
                            "Veroeffentlichen": ("PUT", f"/api/notes/{root}/publish", {})}.items():
        ok(f"B (Bearbeiten): {name} -> 403", st(m, p, Bk, b) == 403)
    # 2.3: Versionen ab 'Bearbeiten' (ansehen + wiederherstellen; die Fassungen bleiben bei A, Verweise maskiert)
    s, vl = req("GET", f"/api/notes/{root}/versions", Bk)
    ok(f"B (Bearbeiten): Versionen ansehen ({s}, {len(vl) if isinstance(vl, list) else vl})", s == 200 and isinstance(vl, list) and any(v["id"] == ver for v in vl))
    ok("B (Bearbeiten): Fassung lesen", st("GET", f"/api/notes/{root}/versions/{ver}", Bk) == 200)
    with connect() as c:
        nv0 = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=? AND uid='sh-a'", (root,)).fetchone()[0]
    ok("B (Bearbeiten): Fassung wiederherstellen", st("POST", f"/api/notes/{root}/versions/{ver}/restore", Bk) == 200)
    with connect() as c:
        rv = c.execute("SELECT updated_by FROM note WHERE id=?", (root,)).fetchone()[0]
        nv1 = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=? AND uid='sh-a'", (root,)).fetchone()[0]
    ok(f"Wiederhergestellt von B, vorheriger Stand als Version bei A ({nv0} -> {nv1})", rv == "sh-b" and nv1 == nv0 + 1)
    ok("B: eigene Seite unter A's geteilte Seite haengen -> 403/404", st("PATCH", f"/api/notes/{bp}", Bk, {"parent_id": root}) in (403, 404))
    ok("B: Seite unter gesperrter Seite: Inhalt gesperrt (423)",
       st("PATCH", f"/api/notes/{child}", A, {"locked": True}) == 200 and st("PATCH", f"/api/notes/{child}", Bk, {"title": "x"}) == 423)
    req("PATCH", f"/api/notes/{child}", A, {"locked": False})

    # --- 5. Gruppen -------------------------------------------------------------------------------------------------------
    with connect() as c, c:
        auth.ensure_group(c, "sh-team"); auth.ensure_group(c, "sh-andere")
    auth.set_user_groups(gid_of("sh-a"), ["sh-team"])
    gpage = nid(req("POST", "/api/notes", A, {"title": "Team-Seite"})[1])
    ok("Gruppe, in der A nicht ist -> 400", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "group", "target": "sh-andere", "role": "view"}) == 400)
    s, d = req("PUT", f"/api/notes/{gpage}/shares", A, {"type": "group", "target": "sh-team", "role": "comment"})
    ok(f"An eigene Gruppe geteilt ({s})", s == 200 and d["shares"][0]["type"] == "group")
    ok("C nicht in der Gruppe -> 404", st("GET", f"/api/notes/{gpage}", Ck) == 404)
    auth.set_user_groups(gid_of("sh-c"), ["sh-team"])
    s, d = req("GET", f"/api/notes/{gpage}", Ck)
    ok(f"C tritt der Gruppe bei -> sofort Zugriff ({s})", s == 200 and d["role"] == "comment")
    auth.set_user_groups(gid_of("sh-c"), [])
    ok("C verlaesst die Gruppe -> sofort 404", st("GET", f"/api/notes/{gpage}", Ck) == 404 and req("GET", "/api/shared", Ck)[1] == [])
    auth.set_ext_groups(gid_of("sh-a"), ["sh-ext"]); auth.set_ext_groups(gid_of("sh-c"), ["sh-ext"])
    req("PUT", f"/api/notes/{gpage}/shares", A, {"type": "group", "target": "sh-ext", "role": "view"})
    ok("LDAP/SSO-Gruppe (user_ext_group) gilt ebenso", req("GET", f"/api/notes/{gpage}", Ck)[1].get("role") == "view")
    auth.set_ext_groups(gid_of("sh-c"), [])
    ok("... und endet ebenso sofort", st("GET", f"/api/notes/{gpage}", Ck) == 404)
    req("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-c", "role": "view"})
    auth.set_user_groups(gid_of("sh-c"), ["sh-team"])
    ok("Hoechste Rolle aus Person + Gruppe gilt", req("GET", f"/api/notes/{gpage}", Ck)[1].get("role") == "comment")
    req("PUT", f"/api/notes/{child}/shares", A, {"type": "user", "target": "sh-c", "role": "edit"})
    ok("Hoechste Rolle in der Kette: Unterseite edit, Wurzel nicht geteilt",
       req("GET", f"/api/notes/{grand}", Ck)[1].get("role") == "edit" and st("GET", f"/api/notes/{root}", Ck) == 404)
    inh = req("GET", f"/api/notes/{grand}/shares", A)[1]
    ok("Freigaben-Dialog nennt geerbte Freigaben", any(x["note_id"] == root and x["target"] == "sh-b" for x in inh.get("inherited", []))
       and any(x["note_id"] == child for x in inh.get("inherited", [])))
    req("DELETE", f"/api/notes/{child}/shares?type=user&target=sh-c", A)
    auth.set_user_groups(gid_of("sh-c"), [])

    # --- 6. Personensuche, Grenzen, Admin-Einstellungen ---------------------------------------------------------------------
    s, d = req("GET", "/api/share/targets?q=sh", A)
    ok(f"Suche: unter 3 Zeichen keine Liste ({s})", s == 200 and d["users"] == [])
    d = req("GET", "/api/share/targets?q=sh-", A)[1]
    us = {u["username"]: u for u in d["users"]}
    ok(f"Suche: Konten mit Name, ohne mich, ohne gesperrte ({sorted(us)})", "sh-b" in us and us["sh-b"]["name"] == "Bert Liest" and "sh-a" not in us
       and "sh-off" not in us and {g["name"] for g in d["groups"]} >= {"sh-team"} and "sh-andere" not in {g["name"] for g in d["groups"]})
    ok("Suche nach Anzeigename", any(u["username"] == "sh-c" for u in req("GET", "/api/share/targets?q=cora", A)[1]["users"]))
    ok("Teilen mit sich selbst -> 400", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-a", "role": "view"}) == 400)
    ok("Teilen mit unbekanntem Konto -> 400", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-niemand", "role": "view"}) == 400)
    ok("Teilen mit gesperrtem Konto -> 400", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-off", "role": "view"}) == 400)
    ok("Unbekannte Rolle -> 400", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-b", "role": "owner"}) == 400)
    ok("Vorlage teilen -> 400", st("PUT", f"/api/notes/{tpl}/shares", A, {"type": "user", "target": "sh-b", "role": "view"}) == 400)
    auth.save_setting("share_group_scope", True); time.sleep(WAIT)
    auth.set_user_groups(gid_of("sh-b"), ["sh-team"])
    us = {u["username"] for u in req("GET", "/api/share/targets?q=sh-", A)[1]["users"]}
    ok(f"Nur gemeinsame Gruppen: Suche zeigt B, nicht C ({sorted(us)})", us == {"sh-b"})
    ok("Nur gemeinsame Gruppen: Teilen mit C -> 403", st("PUT", f"/api/notes/{priv}/shares", A, {"type": "user", "target": "sh-c", "role": "view"}) == 403)
    ok("Nur gemeinsame Gruppen: Teilen mit B erlaubt", st("PUT", f"/api/notes/{priv}/shares", A, {"type": "user", "target": "sh-b", "role": "view"}) == 200)
    req("DELETE", f"/api/notes/{priv}/shares?type=user&target=sh-b", A)
    auth.save_setting("share_group_scope", False); auth.set_user_groups(gid_of("sh-b"), [])
    auth.save_setting("share_enabled", False); time.sleep(WAIT)
    ok("Teilen abgeschaltet: B sieht nichts mehr (Seite, Liste, Bild)", st("GET", f"/api/notes/{root}", Bk) == 404
       and req("GET", "/api/shared", Bk)[1] == [] and st("GET", url, Bk) == 404)
    ok("Teilen abgeschaltet: neue Freigabe -> 403", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-b", "role": "view"}) == 403)
    ok("Teilen abgeschaltet: Eigentuemer arbeitet normal, Kommentare an sich selbst gehen",
       st("GET", f"/api/notes/{root}", A) == 200 and st("POST", f"/api/notes/{root}/comments", A, {"block_id": "blk1", "body": "solo"}) == 200)
    auth.save_setting("share_enabled", True); time.sleep(WAIT)
    ok("Wieder an: Freigabe gilt wieder (auch das Bild)", req("GET", f"/api/notes/{root}", Bk)[1].get("role") == "edit" and st("GET", url, Bk) == 200)
    with connect() as c, c:
        for i in range(199 - len(req("GET", f"/api/notes/{gpage}/shares", A)[1]["shares"]) + 1):
            c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                      (gpage, "sh-a", "user", f"sh-dummy{i}", "view", time.time(), "sh-a"))
    ok("Hoechstens 200 Freigaben je Seite -> 409", st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-b", "role": "view"}) == 409)
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE target LIKE 'sh-dummy%'")
    codes = [st("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": f"Flut {i}"}) for i in range(31)]
    ok(f"Hoechstens 30 Kommentare je Minute (danach 429): {codes.count(200)}x 200", codes.count(429) >= 1 and 429 not in codes[:24])

    # --- 7. Seitenleiste mit 500 Seiten: Mengen-Abfrage -------------------------------------------------------------------
    big = nid(req("POST", "/api/notes", A, {"title": "Viele Seiten"})[1])
    now = time.time()
    with connect() as c, c:
        parents = [big]
        for i in range(500):
            par = parents[i // 5] if i // 5 < len(parents) else big
            parents.append(c.execute("INSERT INTO note (uid, title, body, format, parent_id, created, updated) VALUES (?,?,?,?,?,?,?)",
                                     ("sh-a", f"Seite {i}", blocks(), "blocks", par, now, now)).lastrowid)
    req("PUT", f"/api/notes/{big}/shares", A, {"type": "user", "target": "sh-c", "role": "view"})
    t0 = time.time(); sh = req("GET", "/api/shared", Ck)[1]; dt = time.time() - t0
    ok(f"500 geteilte Seiten in {dt * 1000:.0f} ms ({len(sh)} Eintraege)", len({x["id"] for x in sh} & set(parents)) == 501 and dt < 1.5)
    t0 = time.time(); s = st("GET", f"/api/notes/{parents[-1]}", Ck); dt = time.time() - t0
    ok(f"Tiefe Unterseite (Kette ~5 Ebenen) lesen in {dt * 1000:.0f} ms", s == 200 and dt < 1.0)

    # --- 7b. Pruefer-Nachtrag: Dateien nur ueber sichtbare Seiten, Relationen, Personensuche, Gruppe geloescht ----------------
    ok("Bild nicht mehr in einer geteilten Seite -> fuer B 404", st("GET", img, Bk) == 404)
    req("PATCH", f"/api/notes/{grand}", A, {"body": blocks(P("tief unten"), {"type": "image", "data": {"file": {"url": img}, "caption": ""}}), "format": "blocks"})
    ok("Bild in tiefer (geerbter) Unterseite -> fuer B wieder sichtbar", st("GET", img, Bk) == 200)
    s, pimg = upload(A)
    req("PATCH", f"/api/notes/{priv}", A, {"body": blocks({"type": "image", "data": {"file": {"url": pimg}, "caption": ""}}), "format": "blocks"})
    ok("Bild nur in A's privater Seite -> B und C 404", st("GET", pimg, Bk) == 404 and st("GET", pimg, Ck) == 404 and st("GET", pimg, A) == 200)
    prow = nid(req("POST", "/api/notes", A, {"title": "Geheime Zeile", "parent_id": privdb})[1])
    sch = req("GET", f"/api/notes/{dbid}", A)[1]["schema"]
    sch["props"].append({"id": "relp", "name": "Rel", "type": "relation", "target": privdb})
    ok("A: Relation der geteilten Datenbank auf private Datenbank", st("PATCH", f"/api/notes/{dbid}", A, {"schema": sch}) == 200
       and st("PATCH", f"/api/notes/{row2}", A, {"props": {"relp": [prow]}}) == 200)
    ok("B (Bearbeiten): Zeilenwert mit Relation auf nicht geteilte Zeile -> 200", st("PATCH", f"/api/notes/{row1}", Bk, {"props": {"relp": [prow]}}) == 200)
    with connect() as c:
        p1 = json.loads(c.execute("SELECT props FROM note WHERE id=?", (row1,)).fetchone()[0] or "{}")
    ok("... nicht geteilte Zeile still verworfen", "relp" not in p1)
    st("PATCH", f"/api/notes/{row2}", Bk, {"props": {"relp": [prow]}})
    with connect() as c:
        p2 = json.loads(c.execute("SELECT props FROM note WHERE id=?", (row2,)).fetchone()[0] or "{}")
    ok("... schon verknuepfte Zeile bleibt erhalten", p2.get("relp") == [prow])
    ok("B: Ziel der Relation bleibt verborgen", st("GET", f"/api/notes/{prow}", Bk) == 404 and st("GET", f"/api/notes/{privdb}/rows", Bk) == 404)
    with connect() as c, c:
        c.execute("UPDATE users SET email='cora-geheim@example.org' WHERE username='sh-c'")
    s, raw = req("GET", "/api/share/targets?q=cora", A)
    ok("Personensuche: nur Benutzername + Anzeigename (+ Profilbild-Adresse), keine E-Mail, kein E-Mail-Hash",
       s == 200 and all(set(u) == {"username", "name", "avatar"} for u in raw["users"]) and "example.org" not in json.dumps(raw)
       and hashlib.sha256(b"cora-geheim@example.org").hexdigest() not in json.dumps(raw))
    codes = [st("GET", "/api/share/targets?q=sh", Ck) for _ in range(62)]
    ok(f"Personensuche: Rate-Limit ({codes.count(429)}x 429)", 429 in codes and 429 not in codes[:59])
    with connect() as c, c:
        auth.ensure_group(c, "sh-weg")
    auth.set_user_groups(gid_of("sh-a"), ["sh-team", "sh-weg"])
    st("PUT", f"/api/notes/{gpage}/shares", A, {"type": "group", "target": "sh-weg", "role": "view"})
    from saga import sharing
    with connect() as c, c:
        c.execute("DELETE FROM groups WHERE name='sh-weg'")
        sharing.drop_group(c, "sh-weg")
        ng = c.execute("SELECT COUNT(*) FROM note_share WHERE target_type='group' AND target='sh-weg'").fetchone()[0]
    ok("Gruppe geloescht -> Freigaben an die Gruppe entfernt", ng == 0)

    # --- 7c. Nachbesserung: Titel nicht geteilter Seiten, parent_id, Kommentar geloescht, Relationen, Veroeffentlichen -------
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "edit"})
    secret = "Privat von A"
    lnk_p, lnk_c = f'<a href="/notes#{priv}">{secret}</a>', f'<a href="/notes#{child}">Kapitel</a>'
    req("PATCH", f"/api/notes/{root}", A, {"body": blocks(P("zauberwort-sh steht hier", "blk1"), P(f"Siehe {lnk_p} und {lnk_c}", "blk3"),
                                                          {"type": "list", "data": {"style": "unordered", "items": [f"Punkt {lnk_p}"]}}), "format": "blocks"})
    req("PATCH", f"/api/notes/{row1}", A, {"body": blocks(P(f"Zeile verweist auf {lnk_p}")), "format": "blocks"})
    ok("A: eigene Seite zeigt den Titel im Verweis", secret in req("GET", f"/api/notes/{root}", A)[1]["body"])
    s, n = req("GET", f"/api/notes/{root}", Bk)
    nb = json.dumps(json.loads(n["body"]), ensure_ascii=False).replace('\\"', '"') if s == 200 else ""
    ok(f"B: GET Seite ohne fremden Titel, Platzhalter statt dessen ({s})", s == 200 and secret not in json.dumps(n, ensure_ascii=False)
       and nb.count(f'<a href="/notes#{priv}">Seite ohne Zugriff</a>') == 2 and lnk_c in nb)
    s, n1 = req("GET", f"/api/notes/{row1}", Bk)
    ok(f"B: GET Zeile ohne fremden Titel ({s})", s == 200 and secret not in json.dumps(n1, ensure_ascii=False))
    s, d = req("GET", f"/api/notes/{dbid}/rows", Bk)
    ok(f"B: Zeilen ohne fremden Titel, auch nicht von Relationszielen ({s})", s == 200 and secret not in json.dumps(d, ensure_ascii=False)
       and "Geheime Zeile" not in json.dumps(d, ensure_ascii=False))
    ok("B: Zeile mit Relation auf private Zeile ohne deren Titel", "Geheime Zeile" not in json.dumps(req("GET", f"/api/notes/{row2}", Bk)[1], ensure_ascii=False))
    ok("B: Suche nach dem fremden Titel findet nichts", req("POST", "/api/notes/search", Bk, {"q": secret})[1] == []
       and req("POST", "/api/notes/search", Bk, {"q": "Privat"})[1] == [])
    hits = req("POST", "/api/notes/search", Bk, {"q": "Siehe"})[1]
    ok(f"B: Suchtreffer (Ausschnitt) ohne fremden Titel ({len(hits)})", [h["id"] for h in hits] == [root] and secret not in json.dumps(hits, ensure_ascii=False))
    s, md = req("GET", f"/api/notes/{root}/export", Bk)
    md = md if isinstance(md, str) else md.decode()
    ok(f"B: Markdown-Export ohne fremden Titel ({s})", s == 200 and secret not in md and "Seite ohne Zugriff" in md)
    s, z = req("GET", f"/api/notes/{root}/export?sub=1", Bk)
    zf = zipfile.ZipFile(io.BytesIO(z)) if s == 200 else None
    ok(f"B: ZIP-Export ohne fremden Titel ({s})", zf is not None and not any(secret in n or secret in zf.read(n).decode("utf-8", "replace") for n in zf.namelist()))
    s, bl = req("GET", f"/api/notes/{child}/backlinks", Bk)
    ok(f"B: Rueckverweise ohne fremden Titel ({s})", s == 200 and any(x["id"] == root for x in bl) and secret not in json.dumps(bl, ensure_ascii=False))
    ok("B: Rueckverweise der privaten Seite -> 404", st("GET", f"/api/notes/{priv}/backlinks", Bk) == 404)
    with connect() as c, c:                          # Kommentar-Grenze (30/Minute) aus den Abschnitten davor nicht mitzaehlen
        c.execute("UPDATE note_comment SET created=created-120 WHERE uid LIKE 'sh-%'")
    s, d = req("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk3", "body": f"siehe /notes#{priv}"})
    ok(f"Kommentar mit Seitenverweis bleibt Klartext ohne Titel ({s})", s == 200
       and secret not in json.dumps(req("GET", f"/api/notes/{root}/comments", Bk)[1], ensure_ascii=False))
    lcm = d["comment"]["id"] if s == 200 else 0
    # B speichert den maskierten Inhalt (mit eigenem Zusatz) -> A sieht seinen Titel weiter
    bb = json.loads(n["body"]); bb["blocks"].append(P("Zusatz von B"))
    s, _ = req("PATCH", f"/api/notes/{root}", Bk, {"body": json.dumps(bb), "format": "blocks", "rev": n["rev"]})
    na = req("GET", f"/api/notes/{root}", A)[1]
    ok(f"B speichert: Verweistexte fuer A unveraendert ({s})", s == 200 and na["body"].count(f">{secret}</a>") == 2
       and "Seite ohne Zugriff" not in na["body"] and "Zusatz von B" in na["body"] and ">Kapitel</a>" in na["body"])
    with connect() as c:
        ok("... auch in der Datenbank kein Platzhalter", "Seite ohne Zugriff" not in c.execute("SELECT body FROM note WHERE id=?", (root,)).fetchone()[0])
    n = req("GET", f"/api/notes/{root}", Bk)[1]
    bb = json.loads(n["body"]); bb["blocks"] = [x for x in bb["blocks"] if x.get("type") != "list"]
    s, _ = req("PATCH", f"/api/notes/{root}", Bk, {"body": json.dumps(bb), "format": "blocks", "rev": n["rev"]})
    na = req("GET", f"/api/notes/{root}", A)[1]
    ok(f"B loescht einen Verweis: bleibt geloescht, der andere bleibt mit Titel ({s})", s == 200 and na["body"].count(f">{secret}</a>") == 1)
    n = req("GET", f"/api/notes/{row1}", Bk)[1]
    s, _ = req("PATCH", f"/api/notes/{row1}", Bk, {"body": n["body"].replace("Zeile verweist", "Zeile (B) verweist"), "format": "blocks", "rev": n["rev"]})
    ok(f"B speichert Zeile: Titel im Verweis fuer A erhalten ({s})", s == 200 and f">{secret}</a>" in req("GET", f"/api/notes/{row1}", A)[1]["body"])
    # Verweise in Tabelle, Spalten, Toggle, Callout (mit Formatierung im Linktext); B verschiebt Bloecke und aendert Text drumherum
    lnk_f = f'<a href="/notes#{priv}"><b>{secret}</b> <i>fett</i></a>'
    deep = [{"id": "d1", "type": "table", "data": {"withHeadings": False, "content": [[f"Zelle {lnk_p}", "x"]]}},
            {"id": "d2", "type": "columns", "data": {"cols": [{"blocks": [P(f"Spalte {lnk_f}", "d2a")]}, {"blocks": [P("leer", "d2b")]}]}},
            {"id": "d3", "type": "toggle", "data": {"text": f"Toggle {lnk_p}", "blocks": [P(f"innen {lnk_p}", "d3a")]}},
            {"id": "d4", "type": "callout", "data": {"emoji": "💡", "text": f"Hinweis {lnk_f}"}}]
    req("PATCH", f"/api/notes/{child}", A, {"body": blocks(*deep), "format": "blocks"})
    before = json.loads(req("GET", f"/api/notes/{child}", A)[1]["body"])["blocks"]
    n = req("GET", f"/api/notes/{child}", Bk)[1]
    bb = json.loads(n["body"])
    ok("B: Verweise in Tabelle/Spalten/Toggle/Callout maskiert", secret not in n["body"] and n["body"].count("Seite ohne Zugriff") == 5)
    bb["blocks"].reverse(); bb["blocks"][0]["data"]["text"] = "Neu vorn: " + bb["blocks"][0]["data"]["text"]
    s, _ = req("PATCH", f"/api/notes/{child}", Bk, {"body": json.dumps(bb), "format": "blocks", "rev": n["rev"]})
    after = json.loads(req("GET", f"/api/notes/{child}", A)[1]["body"])["blocks"]
    ok(f"B verschiebt/aendert: alle Verweistexte fuer A wie vorher, Formatierung erhalten ({s})", s == 200
       and [x["data"] for x in after[1:]] == [x["data"] for x in reversed(before)][1:]
       and after[0]["data"]["text"] == "Neu vorn: " + before[-1]["data"]["text"])
    # Freigabe aendert sich zwischen Lesen und Speichern: B hat den Platzhalter geladen, darf priv jetzt sehen
    n = req("GET", f"/api/notes/{child}", Bk)[1]
    req("PUT", f"/api/notes/{priv}/shares", A, {"type": "user", "target": "sh-b", "role": "view"})
    s, _ = req("PATCH", f"/api/notes/{child}", Bk, {"body": n["body"], "format": "blocks", "rev": n["rev"]})
    with connect() as c:
        cb = c.execute("SELECT body FROM note WHERE id=?", (child,)).fetchone()[0]
    ok(f"Ziel inzwischen freigegeben: trotzdem kein Platzhalter in der DB ({s})", s == 200 and "Seite ohne Zugriff" not in cb
       and json.loads(cb)["blocks"] == after)
    req("DELETE", f"/api/notes/{priv}/shares?type=user&target=sh-b", A)
    req("PATCH", f"/api/notes/{child}", A, {"body": blocks(), "format": "blocks"})
    # parent_id: nur Unterseite geteilt -> Elternseite bleibt verborgen
    req("PUT", f"/api/notes/{grand}/shares", A, {"type": "user", "target": "sh-c", "role": "view"})
    s, g = req("GET", f"/api/notes/{grand}", Ck)
    ok(f"C: geteilte Unterseite ohne parent_id der unsichtbaren Elternseite ({s})", s == 200 and g.get("parent_id") is None
       and req("GET", f"/api/notes/{grand}", A)[1]["parent_id"] == child and req("GET", f"/api/notes/{grand}", Bk)[1]["parent_id"] == child)
    ok("C: Suchtreffer ohne parent_id", all(h.get("parent_id") is None for h in req("POST", "/api/notes/search", Ck, {"q": "tief unten"})[1]))
    req("DELETE", f"/api/notes/{grand}/shares?type=user&target=sh-c", A)
    # Kommentar geloescht: erster Kommentar mit fremden Antworten
    th = req("POST", f"/api/notes/{root}/comments", A, {"block_id": "blk1", "body": "Erster von A"})[1]["comment"]["id"]
    rb = req("POST", f"/api/notes/{root}/comments", Bk, {"thread_id": th, "body": "Antwort von B bleibt"})[1]["comment"]["id"]
    s, d = req("DELETE", f"/api/comments/{th}", A)
    t5 = next((x for x in req("GET", f"/api/notes/{root}/comments", Bk)[1]["threads"] if x["id"] == th), {})
    cm5 = t5.get("comments", [])
    ok(f"Erster Kommentar mit fremder Antwort -> 'Kommentar geloescht', Antwort bleibt ({s})", s == 200 and d.get("soft") is True
       and len(cm5) == 2 and cm5[0]["deleted"] is True and cm5[0]["body"] == "" and cm5[1]["body"] == "Antwort von B bleibt" and not cm5[1]["deleted"])
    ok("Geloeschten Kommentar bearbeiten -> 409", st("PATCH", f"/api/comments/{th}", A, {"body": "wieder da"}) == 409)
    ok("Zaehler zaehlt die Unterhaltung weiter", req("GET", f"/api/notes/{root}", A)[1]["comment_counts"].get("blk1", 0) >= 1)
    ok("Letzte Antwort geloescht -> Unterhaltung ganz weg", st("DELETE", f"/api/comments/{rb}", Bk) == 200
       and all(x["id"] != th for x in req("GET", f"/api/notes/{root}/comments?resolved=1", A)[1]["threads"]))
    with connect() as c:
        ok("... auch in der Datenbank", c.execute("SELECT COUNT(*) FROM note_comment WHERE id=? OR thread_id=?", (th, th)).fetchone()[0] == 0)
    th = req("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "Selbstgespraech"})[1]["comment"]["id"]
    req("POST", f"/api/notes/{root}/comments", Bk, {"thread_id": th, "body": "noch ich"})
    s, d = req("DELETE", f"/api/comments/{th}", Bk)
    ok(f"Ohne fremde Antworten: ganze Unterhaltung weg wie bisher ({s})", s == 200 and d.get("deleted") == 2 and d.get("soft") is False)
    th = req("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "Frage von B"})[1]["comment"]["id"]
    req("POST", f"/api/notes/{root}/comments", A, {"thread_id": th, "body": "Antwort von A"})
    ok("Nicht-Eigentuemer: ganze Unterhaltung (?all=1) -> 403", st("DELETE", f"/api/comments/{th}?all=1", Bk) == 403)
    s, d = req("DELETE", f"/api/comments/{th}", Bk)
    ok(f"B loescht eigenen ersten Kommentar mit Antwort von A -> bleibt als geloescht ({s})", s == 200 and d.get("soft") is True)
    s, d = req("DELETE", f"/api/comments/{th}?all=1", A)
    with connect() as c:
        left_c = c.execute("SELECT COUNT(*) FROM note_comment WHERE id=? OR thread_id=?", (th, th)).fetchone()[0]
    ok(f"Eigentuemer loescht die ganze Unterhaltung (?all=1) ({s})", s == 200 and d.get("deleted") == 2 and left_c == 0)
    req("DELETE", f"/api/comments/{lcm}", Bk)
    # Zweiseitige Relation: Gegenspalte nur in Zeilen, die B bearbeiten darf
    ydb = nid(req("POST", "/api/notes", A, {"title": "Ziel-DB", "kind": "database"})[1])
    y1, y2, y3 = (nid(req("POST", "/api/notes", A, {"title": f"Ziel {k}", "parent_id": ydb})[1]) for k in (1, 2, 3))
    req("PUT", f"/api/notes/{y1}/shares", A, {"type": "user", "target": "sh-b", "role": "view"})
    req("PUT", f"/api/notes/{y3}/shares", A, {"type": "user", "target": "sh-b", "role": "edit"})
    sch = req("GET", f"/api/notes/{dbid}", A)[1]["schema"]
    sch["props"].append({"id": "rel2", "name": "Ziele", "type": "relation", "target": ydb, "two_way": True})
    ok("A: zweiseitige Relation anlegen", st("PATCH", f"/api/notes/{dbid}", A, {"schema": sch}) == 200)
    mid = next((p["id"] for p in req("GET", f"/api/notes/{ydb}", A)[1]["schema"]["props"] if p.get("pair") == "rel2"), None)

    def mirror(y):
        with connect() as c:
            return json.loads(c.execute("SELECT props FROM note WHERE id=?", (y,)).fetchone()[0] or "{}").get(mid) or []
    st("PATCH", f"/api/notes/{row1}", A, {"props": {"rel2": [y2]}})
    ok(f"A verknuepft row1 -> Ziel 2 (Gegenspalte {mid})", mid and mirror(y2) == [row1])
    s = st("PATCH", f"/api/notes/{row1}", Bk, {"props": {"rel2": [y1, y3]}})
    ok(f"B: Gegenspalte nur in Zeile mit 'Bearbeiten' (Ziel 3), nicht in Ziel 1 (Lesen) ({s})", s == 200 and mirror(y3) == [row1] and mirror(y1) == [])
    ok("... Ziel 2 (nicht sichtbar) bleibt verknuepft, Gegenspalte unveraendert", mirror(y2) == [row1])
    s = st("PATCH", f"/api/notes/{row1}", Bk, {"props": {"rel2": []}})
    ok(f"B entfernt: Gegenspalte nur in Ziel 3 geaendert ({s})", s == 200 and mirror(y3) == [] and mirror(y2) == [row1] and mirror(y1) == [])
    # Veroeffentlichen: Hinweis, wenn mit "Bearbeiten" geteilt
    ok("Veroeffentlichen-Dialog: shared_edit an geteilter Seite (auch Unterseite und Elternseite)",
       req("GET", f"/api/notes/{root}/publish", A)[1].get("shared_edit") is True and req("GET", f"/api/notes/{child}/publish", A)[1].get("shared_edit") is True
       and req("GET", f"/api/notes/{ydb}/publish", A)[1].get("shared_edit") is True)
    ok("... nicht an Seite ohne Bearbeiter", req("GET", f"/api/notes/{gpage}/publish", A)[1].get("shared_edit") is False
       and req("GET", f"/api/notes/{priv}/publish", A)[1].get("shared_edit") is False)
    s, d = req("GET", "/api/share/targets?q=sh-", A)
    req("DELETE", f"/api/notes/{y1}/shares?type=user&target=sh-b", A); req("DELETE", f"/api/notes/{y3}/shares?type=user&target=sh-b", A)
    ok(f"Personensuche: 2 Zeichen leer, 3 Zeichen Treffer ({s})", s == 200 and d["users"] and req("GET", "/api/share/targets?q=co", A)[1]["users"] == [])

    # --- 7d. Nachbesserung Runde 2: Konflikt ohne Datenverlust, Autoren, tatsaechliche Rolle, Ungelesenes, Laenge, Platzhalter ----
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "edit"})
    n = req("GET", f"/api/notes/{root}", Bk)[1]
    s = st("PATCH", f"/api/notes/{root}", A, {"body": blocks(P("Fassung von A", "blk1")), "format": "blocks", "rev": n["rev"]})
    s2, d = req("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("Fassung von B", "blk1")), "format": "blocks", "rev": n["rev"]})
    ok(f"Konflikt: B bekommt 409 mit Name ({s}/{s2})", s == 200 and s2 == 409 and d.get("by") == "Anna Teilt")
    s = st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("Fassung von B", "blk1")), "format": "blocks", "overwrite": True})
    with connect() as c:
        v = c.execute("SELECT body, author FROM note_version WHERE note_id=? ORDER BY created DESC, id DESC LIMIT 1", (root,)).fetchone()
    ok(f"'Meine Version behalten': A's Fassung als Version gesichert, mit Autor ({s})", s == 200 and "Fassung von A" in v[0] and v[1] == "sh-a"
       and "Fassung von B" in req("GET", f"/api/notes/{root}", A)[1]["body"])
    n = req("GET", f"/api/notes/{root}", A)[1]
    s = st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("veraltet", "blk1")), "format": "blocks", "rev": n["rev"] - 1, "overwrite": True})
    ok(f"overwrite mit veralteter Revision: trotzdem 409 (rev-Pruefung gilt) ({s})", s == 409
       and "Fassung von B" in req("GET", f"/api/notes/{root}", A)[1]["body"])
    st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("Fassung B2", "blk1")), "format": "blocks"})
    s = st("PATCH", f"/api/notes/{root}", A, {"body": blocks(P("zauberwort-sh steht hier", "blk1")), "format": "blocks", "overwrite": True})
    vl = req("GET", f"/api/notes/{root}/versions", A)[1]
    with connect() as c:
        top = c.execute("SELECT id, body FROM note_version WHERE note_id=? ORDER BY created DESC, id DESC LIMIT 1", (root,)).fetchone()
    ok(f"A ueberschreibt B: B's Fassung als Version, Liste nennt 'Bert Liest' ({s})", s == 200 and "Fassung B2" in top[1]
       and next(x for x in vl if x["id"] == top[0]).get("author", {}).get("name") == "Bert Liest"
       and all(x.get("author") is None or x["author"]["name"] != "Anna Teilt" for x in vl))
    # Angelegt von / zuletzt geaendert von
    s, d = req("POST", "/api/notes", Bk, {"title": "Von B angelegt", "parent_id": root})
    bnew = d.get("id") if s == 200 else 0
    na_ = req("GET", f"/api/notes/{bnew}", A)[1]
    ok(f"A sieht 'Angelegt von Bert' ({s})", (na_.get("created_by") or {}).get("name") == "Bert Liest"
       and req("GET", f"/api/notes/{root}", A)[1].get("created_by") is None)
    ok("B sieht 'angelegt von mir' (me) und zuletzt geaendert", (req("GET", f"/api/notes/{bnew}", Bk)[1].get("created_by") or {}).get("me") is True
       and (req("GET", f"/api/notes/{root}", Bk)[1].get("updated_by") or {}).get("name") == "Anna Teilt")
    # Tatsaechliche Rolle im Teilen-Dialog: direkt "Kommentieren", ueber Gruppe "Bearbeiten"
    with connect() as c, c:
        auth.ensure_group(c, "sh-g2")
    auth.set_user_groups(gid_of("sh-a"), ["sh-team", "sh-g2"]); auth.set_user_groups(gid_of("sh-b"), ["sh-g2"])
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "comment"})
    stt = req("PUT", f"/api/notes/{root}/shares", A, {"type": "group", "target": "sh-g2", "role": "edit"})[1]
    bs = next(x for x in stt["shares"] if x["target"] == "sh-b")
    gs = next(x for x in stt["shares"] if x["target"] == "sh-g2")
    ok(f"Dialog: B direkt 'comment', tatsaechlich 'edit' ueber sh-g2 ({bs.get('effective')})", bs["role"] == "comment" and bs.get("effective") == "edit"
       and bs["via"][0]["type"] == "group" and bs["via"][0]["name"] == "sh-g2" and bs["via"][0]["role"] == "edit")
    ok(f"Gruppe mit Mitgliederzahl ({gs.get('members')})", gs.get("members") == 2 and gs.get("effective") == "edit")
    req("PUT", f"/api/notes/{child}/shares", A, {"type": "user", "target": "sh-b", "role": "view"})
    cs = next(x for x in req("GET", f"/api/notes/{child}/shares", A)[1]["shares"] if x["target"] == "sh-b")
    ok("Unterseite: auch Freigaben darueber zaehlen (Gruppe + direkte Freigabe der Elternseite)", cs.get("effective") == "edit"
       and {(x["type"], x["role"]) for x in cs["via"]} >= {("group", "edit"), ("user", "comment")}
       and any(x["type"] == "user" and x["title"] == "Projekt Teilen (von B)" for x in cs["via"]))
    req("DELETE", f"/api/notes/{child}/shares?type=user&target=sh-b", A)
    req("DELETE", f"/api/notes/{root}/shares?type=group&target=sh-g2", A)
    auth.set_user_groups(gid_of("sh-b"), []); auth.set_user_groups(gid_of("sh-a"), ["sh-team"])
    with connect() as c, c:
        c.execute("DELETE FROM groups WHERE name='sh-g2'")
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "edit"})
    # Ungelesene Kommentare ausserhalb der Seite: Punkt im Baum, "Mit mir geteilt", Startseite
    with connect() as c, c:
        c.execute("UPDATE note_comment SET created=created-120 WHERE uid LIKE 'sh-%'")
        c.execute("DELETE FROM note_comment_seen WHERE uid LIKE 'sh-%'")
        for (tid,) in c.execute("SELECT id FROM note_comment WHERE owner_uid='sh-a' AND thread_id IS NULL").fetchall():
            for u in ("sh-a", "sh-b"):
                c.execute("INSERT INTO note_comment_seen (uid, thread_id, seen_at) VALUES (?,?,?)", (u, tid, time.time()))
    ok("Ohne neue Kommentare kein Punkt", not any(p.get("unread") for p in req("GET", "/api/notes", A)[1]))
    th = req("POST", f"/api/notes/{root}/comments", Bk, {"block_id": "blk1", "body": "Neu von B"})[1]["comment"]["id"]
    pa = {p["id"]: p for p in req("GET", "/api/notes", A)[1]}
    ok("A: Punkt an der eigenen Seite im Baum (Kommentar von B)", pa[root].get("unread") is True and not pa[child].get("unread"))
    s, html = req("GET", "/", A)
    ok(f"A: Startseite zeigt 'Neue Kommentare' am Knopf Seiten ({s})", s == 200 and 'class="btn-badge"' in html and "💬 1<" in html)
    ok("B: eigener Kommentar ist fuer B nicht neu", not next(p for p in req("GET", "/api/shared", Bk)[1] if p["id"] == root).get("unread"))
    req("POST", f"/api/comments/{th}/seen", A)
    ok("A: gelesen -> Punkt weg", not next(p for p in req("GET", "/api/notes", A)[1] if p["id"] == root).get("unread")
       and "btn-badge" not in req("GET", "/", A)[1])
    req("POST", f"/api/notes/{root}/comments", A, {"thread_id": th, "body": "Antwort von A"})
    ok("B: Antwort von A -> Punkt in 'Mit mir geteilt'", next(p for p in req("GET", "/api/shared", Bk)[1] if p["id"] == root).get("unread") is True)
    req("POST", f"/api/comments/{th}/resolve", Bk, {"resolved": True})
    ok("Erledigt -> kein Punkt mehr", not next(p for p in req("GET", "/api/shared", Bk)[1] if p["id"] == root).get("unread"))
    d = req("GET", f"/api/notes/{root}", A)[1]
    ok(f"Seite meldet erledigte Unterhaltungen ({d.get('comments_resolved')})", d.get("comments_resolved", 0) >= 1)
    d = req("GET", "/api/share/info", Bk)[1]
    ok(f"/api/share/info fuer B: an, Freigaben erhalten ({d})", d.get("enabled") is True and d.get("received", 0) >= 1)
    # i18n-Kontext '##' in Python wie T() im Browser: nur am Schluessel abschneiden, nie in einer Uebersetzung
    from saga import i18n
    i18n.CATALOG["en"]["Xtest##Kontext"] = "A ## B"
    try:
        ok("i18n '##': de 'Lesen', en 'Can view', Uebersetzung mit '##' bleibt, '##' am Anfang bleibt",
           i18n.t_lang("de", "Lesen##Rolle") == "Lesen" and i18n.t_lang("en", "Lesen##Rolle") == "Can view"
           and i18n.t_lang("en", "Xtest##Kontext") == "A ## B" and i18n.t_lang("de", "Xtest##Kontext") == "Xtest"
           and i18n.t_lang("es", "## Kopf") == "## Kopf")
    finally:
        i18n.CATALOG["en"].pop("Xtest##Kontext", None)
    # Laengenpruefung fuer Nicht-Eigentuemer erst nach dem Wiedereinsetzen der Verweistexte
    many = " ".join([lnk_p] * 2500)
    base = blocks(P(many, "L1"), P("x", "L2"))
    pad = 199_000 - len(base)
    lang = nid(req("POST", "/api/notes", A, {"title": "Lange Seite", "parent_id": root})[1])
    s = st("PATCH", f"/api/notes/{lang}", A, {"body": blocks(P(many, "L1"), P("x" * pad, "L2")), "format": "blocks"})
    n = req("GET", f"/api/notes/{lang}", Bk)[1]
    ok(f"Lange Seite: A speichert, B bekommt die maskierte (laengere) Fassung ({s}: {len(n['body'])})", s == 200 and len(n["body"]) > 200_000)
    s = st("PATCH", f"/api/notes/{lang}", Bk, {"body": n["body"], "format": "blocks", "rev": n["rev"]})
    with connect() as c:
        lb = c.execute("SELECT body FROM note WHERE id=?", (lang,)).fetchone()[0]
    ok(f"B speichert die maskierte Fassung zurueck: kein 413, Titel wieder da ({s})", s == 200 and lb.count(f">{secret}</a>") == 2500 and len(lb) <= 200_000)
    n = req("GET", f"/api/notes/{lang}", Bk)[1]
    bb = json.loads(n["body"]); bb["blocks"].append(P("y" * 12_000))
    ok("... wird es nach dem Wiedereinsetzen zu gross: 413", st("PATCH", f"/api/notes/{lang}", Bk, {"body": json.dumps(bb), "format": "blocks", "rev": n["rev"]}) == 413)
    req("DELETE", f"/api/notes/{lang}", A)
    # Kopierter Platzhalter an neuer Stelle: in der DB der aktuelle Titel (nur Seiten desselben Eigentuemers), B sieht ihn nie
    cpriv = nid(req("POST", "/api/notes", Ck, {"title": "Cora geheim"})[1])
    for ph in ("Seite ohne Zugriff", "Page without access"):
        n = req("GET", f"/api/notes/{grand}", Bk)[1]
        s = st("PATCH", f"/api/notes/{grand}", Bk, {"body": blocks(P(f'Kopie <a href="/notes#{priv}">{ph}</a> und <a href="/notes#{cpriv}">{ph}</a>', "k1")),
                                                     "format": "blocks", "rev": n["rev"]})
        with connect() as c:
            gb = c.execute("SELECT body FROM note WHERE id=?", (grand,)).fetchone()[0]
        bview = req("GET", f"/api/notes/{grand}", Bk)[1]
        ok(f"Kopierter Platzhalter ({ph}): DB hat A's Titel, fremder Titel nie, B sieht Platzhalter ({s})", s == 200
           and f'notes#{priv}\\">{secret}</a>' in gb and "Cora geheim" not in gb and ("Seite ohne Zugriff" in gb or "Page without access" in gb)
           and secret not in json.dumps(bview, ensure_ascii=False) and "Cora geheim" not in json.dumps(bview, ensure_ascii=False))
    req("PATCH", f"/api/notes/{grand}", A, {"body": blocks(P("tief unten")), "format": "blocks"})
    req("DELETE", f"/api/notes/{cpriv}", Ck)
    st("PATCH", f"/api/notes/{root}", Bk, {"body": blocks(P("zauberwort-sh steht hier", "blk1")), "format": "blocks"})   # zuletzt geaendert: B

    # --- 7e. Versionsflut: erzwungene Sicherungen von Bearbeitern gedrosselt, Fassungen des Eigentuemers bleiben ----------
    flut = nid(req("POST", "/api/notes", A, {"title": "Flut", "parent_id": root})[1])
    st("PATCH", f"/api/notes/{flut}", A, {"body": blocks(P("Stand von A " + "a" * 500, "f1")), "format": "blocks"})
    now = time.time()
    with connect() as c, c:
        c.execute("DELETE FROM note_version WHERE note_id=?", (flut,))
        for i in range(12):
            c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
                      (flut, "sh-a", f"eigen {i}", blocks(), "blocks", now - 10_000 + i, "sh-a" if i % 2 else None))
        for i in range(45):
            c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
                      (flut, "sh-a", f"B alt {i}", blocks(), "blocks", now - 5_000 + i, "sh-b"))

    def vers():
        with connect() as c:
            return c.execute("SELECT author, created, body FROM note_version WHERE note_id=? ORDER BY created DESC", (flut,)).fetchall()
    codes = []
    for i in range(30):                              # manipulierter Client: jedes Mal overwrite bzw. leeren/fuellen
        b = {"body": blocks(P(f"B {i} " + ("b" * 600 if i % 2 else ""), "f1")), "format": "blocks"}
        if i % 3 == 0:
            b["overwrite"] = True
        codes.append(st("PATCH", f"/api/notes/{flut}", Bk, b))
    v = vers()
    fresh = [x for x in v if x[1] > now - 60]
    own = [x for x in v if x[0] in (None, "sh-a")]
    ok(f"Flut: 30 Speichern von B -> {len(fresh)} neue Sicherungen (A's Stand + hoechstens 1 von B) ({set(codes)})", set(codes) == {200}
       and len(fresh) <= 2 and sum(1 for x in fresh if x[0] == "sh-b") <= 1 and any(x[0] == "sh-a" and "Stand von A" in x[2] for x in fresh))
    titles = {x["title"] for x in req("GET", f"/api/notes/{flut}/versions", A)[1]}
    ok(f"Kuerzen auf 50: die neuesten 10 Fassungen des Eigentuemers bleiben ({len(own)} von {len(v)})", len(own) >= 10
       and {f"eigen {i}" for i in range(3, 12)} <= titles)
    # vorheriger Stand von jemand anderem: immer sichern, auch innerhalb der Drossel
    st("PATCH", f"/api/notes/{flut}", A, {"body": blocks(P("A wieder", "f1")), "format": "blocks"})
    s = st("PATCH", f"/api/notes/{flut}", Bk, {"body": blocks(P("B danach", "f1")), "format": "blocks", "overwrite": True})
    ok(f"Fremder Stand (A) wird trotz Drossel gesichert ({s})", s == 200 and vers()[0][0] == "sh-a" and "A wieder" in vers()[0][2]
       )
    # Speichern ohne rev (aeltere App, Zeile umbenennen) ueber den Stand einer anderen Person: vorher sichern
    st("PATCH", f"/api/notes/{row2}", A, {"title": "Zeile zwei (A)"})
    with connect() as c, c:
        c.execute("DELETE FROM note_version WHERE note_id=?", (row2,))
    s = st("PATCH", f"/api/notes/{row2}", Bk, {"title": "Zeile zwei (B)"})
    with connect() as c:
        rv = c.execute("SELECT title, author FROM note_version WHERE note_id=? ORDER BY created DESC", (row2,)).fetchall()
    ok(f"Ohne rev ueber fremden Stand: Sicherung mit Autor ({s}: {rv})", s == 200 and len(rv) == 1 and (rv[0][0], rv[0][1]) == ("Zeile zwei (A)", "sh-a"))
    st("PATCH", f"/api/notes/{row2}", Bk, {"title": "Zeile zwei (B2)"})
    with connect() as c:
        n2 = c.execute("SELECT COUNT(*) FROM note_version WHERE note_id=?", (row2,)).fetchone()[0]
    ok(f"Eigener Stand ohne rev: keine weitere Sicherung ({n2})", n2 == 1)
    st("PATCH", f"/api/notes/{row2}", A, {"title": "Zeile zwei"})
    req("DELETE", f"/api/notes/{flut}", A)

    # --- 8. Entzug, endgueltiges Loeschen, Kontoloeschung -----------------------------------------------------------------
    s, d = req("DELETE", f"/api/notes/{root}/shares?type=user&target=sh-b", A)
    ok(f"Freigabe entzogen ({s})", s == 200 and d["shares"] == [])
    ok("Entzug wirkt sofort: Seite, Unterseite, Zeilen, Bild, Kommentare, Liste, Suche",
       st("GET", f"/api/notes/{root}", Bk) == 404 and st("GET", f"/api/notes/{grand}", Bk) == 404 and st("GET", f"/api/notes/{dbid}/rows", Bk) == 404
       and st("GET", url, Bk) == 404 and st("GET", f"/api/notes/{root}/comments", Bk) == 404 and req("GET", "/api/shared", Bk)[1] == []
       and req("POST", "/api/notes/search", Bk, {"q": "zauberwort-sh"})[1] == [])
    ok("Nach Entzug: eigenen Kommentar nicht mehr bearbeitbar", st("PATCH", f"/api/comments/{bth}", Bk, {"body": "y"}) == 404)
    gone = nid(req("POST", "/api/notes", A, {"title": "Kurzlebig"})[1])
    req("PUT", f"/api/notes/{gone}/shares", A, {"type": "user", "target": "sh-c", "role": "comment"})
    req("POST", f"/api/notes/{gone}/comments", Ck, {"block_id": "", "body": "bald weg"})
    req("DELETE", f"/api/notes/{gone}", A)
    ok("Seite im Papierkorb: fuer Geteilte sofort weg", st("GET", f"/api/notes/{gone}", Ck) == 404)
    req("DELETE", f"/api/trash/{gone}", A)
    with connect() as c:
        ok("Endgueltig geloescht -> Freigaben und Kommentare der Seite weg",
           c.execute("SELECT (SELECT COUNT(*) FROM note_share WHERE note_id=?) + (SELECT COUNT(*) FROM note_comment WHERE note_id=?)",
                     (gone, gone)).fetchone()[0] == 0)
    req("PUT", f"/api/notes/{root}/shares", A, {"type": "user", "target": "sh-b", "role": "comment"})
    from saga.userdata import delete_user_data
    delete_user_data("sh-b")
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username='sh-b'")
        left_sh = c.execute("SELECT COUNT(*) FROM note_share WHERE target='sh-b'").fetchone()[0]
        anon = c.execute("SELECT COUNT(*) FROM note_comment WHERE id=? AND uid IS NULL", (bth,)).fetchone()[0]
    ok("Konto B geloescht: Freigaben an B weg, B's Kommentare anonym", left_sh == 0 and anon == 1)
    with connect() as c:
        ub = c.execute("SELECT updated_by FROM note WHERE id=?", (root,)).fetchone()[0]
    ok(f"Konto B geloescht: 'zuletzt geaendert von B' auf A's Seite entfernt ({ub})", ub is None)
    with connect() as c:
        cb = c.execute("SELECT created_by FROM note WHERE id=?", (bnew,)).fetchone()
        va = c.execute("SELECT COUNT(*) FROM note_version WHERE uid='sh-a' AND author='sh-b'").fetchone()[0]
    ok(f"... ebenso 'Angelegt von B' und B als Autor gesicherter Fassungen ({cb}, {va})", cb is not None and cb[0] is None and va == 0)
    t4 = next((t for t in req("GET", f"/api/notes/{root}/comments", A)[1]["threads"] if t["id"] == bth), {})
    ok("Anonymer Kommentar zeigt 'Gelöschtes Konto'", t4.get("comments", [{}])[0].get("author", {}).get("name") == "Gelöschtes Konto")
    auth.create_user("sh-b", PW, "Neue Person gleicher Name"); time.sleep(0.2)
    ok("Neues Konto mit gleichem Namen erbt keine Freigaben", st("GET", f"/api/notes/{root}", ck("sh-b")) == 404)
    req("PUT", f"/api/notes/{gpage}/shares", A, {"type": "user", "target": "sh-c", "role": "view"})
    delete_user_data("sh-a")
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username='sh-a'")
        left = c.execute("SELECT (SELECT COUNT(*) FROM note_share WHERE owner_uid='sh-a') + (SELECT COUNT(*) FROM note_comment WHERE owner_uid='sh-a')").fetchone()[0]
    ok("Konto A geloescht: A's Freigaben und Kommentare auf A's Seiten weg", left == 0 and req("GET", "/api/shared", Ck)[1] == [])
    ok("Nichts von A zurueckgeblieben", snap()[0] == [] and snap()[4] == 0)

    auth.save_setting("share_enabled", prev_en); auth.save_setting("share_group_scope", prev_scope)
    cleanup()
    print(f"\nTEILEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
