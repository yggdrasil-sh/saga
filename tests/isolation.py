"""Sicherheits-Regressionstest: Benutzer B darf an KEINE Daten von Benutzer A herankommen.

Laeuft im Container gegen die echte Datenbank mit Wegwerf-Benutzern (hk-test-*), raeumt danach auf:
    tools/run-tests.sh            (vom Mac aus)
Jeder Umbau am Berechtigungsmodell (zentrale Pruefung, Teilen) muss diesen Lauf bestehen.
"""
import base64, json, os, sqlite3, time, urllib.parse, urllib.request, uuid
from itsdangerous import TimestampSigner

BASE = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")       # tools/probe.sh: Probe-Instanz
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SIGN = TimestampSigner(os.environ["SESSION_SECRET"])
DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
FAILS = []


def cookie(uid):
    return SIGN.sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()


A, B = cookie("hk-test-a"), cookie("hk-test-b")


def call(m, path, who, body=None, ctype=None, raw=False):
    h = {"Host": HOST, "Cookie": "saga=" + who, "X-Saga": "1"}
    data = body
    if isinstance(body, dict):
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    if ctype:
        h["Content-Type"] = ctype
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=m, headers=h), timeout=15) as r:
            x = r.read()
            return r.status, (x if raw else json.loads(x or b"{}"))
    except urllib.error.HTTPError as e:
        x = e.read()
        try:
            return e.code, json.loads(x or b"{}")
        except ValueError:
            return e.code, {}


def check(name, cond):
    print(("OK  " if cond else "FAIL"), name)
    if not cond:
        FAILS.append(name)


def denied(name, status):
    check(f"B: {name} -> {status}", status in (403, 404))


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def upload(who):
    bd = uuid.uuid4().hex
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"t.png\"\r\n\r\n").encode() + PNG + f"\r\n--{bd}--\r\n".encode()
    return call("POST", "/api/files", who, body, ctype=f"multipart/form-data; boundary={bd}")[1]["file"]["url"]


def main():
    # --- A legt Daten an
    img = upload(A)
    P = call("POST", "/api/notes", A, {"title": "A-Geheim", "tags": ["geheimtag"]})[1]["id"]
    call("PATCH", f"/api/notes/{P}", A, {"body": json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "zauberwort-a"}},
                                                                       {"type": "image", "data": {"file": {"url": img}, "caption": ""}}]}), "format": "blocks"})
    C = call("POST", "/api/notes", A, {"title": "A-Kind", "parent_id": P})[1]["id"]
    D = call("POST", "/api/notes", A, {"kind": "database", "title": "A-DB"})[1]["id"]
    R = call("POST", "/api/notes", A, {"parent_id": D, "title": "A-Zeile", "props": {"status": "todo"}})[1]["id"]
    T = call("POST", "/api/notes", A, {"title": "A-Papierkorb"})[1]["id"]; call("DELETE", f"/api/notes/{T}", A)
    con = sqlite3.connect(DB)
    con.execute("INSERT INTO note_version (note_id, uid, title, body, format, created) VALUES (?,?,?,?,?,?)",
                (P, "hk-test-a", "alt", json.dumps({"blocks": []}), "blocks", time.time() - 3600)); con.commit()
    V = con.execute("SELECT id FROM note_version WHERE note_id=?", (P,)).fetchone()[0]
    snap = lambda: con.execute("SELECT id, title, body, props, schema, parent_id, deleted_at, tags FROM note WHERE uid='hk-test-a' ORDER BY id").fetchall()
    before = snap()

    # --- B greift an: lesen
    denied("Seite lesen", call("GET", f"/api/notes/{P}", B)[0])
    denied("Zeilen lesen", call("GET", f"/api/notes/{D}/rows", B)[0])
    denied("Rueckverweise lesen", call("GET", f"/api/notes/{P}/backlinks", B)[0])
    denied("Versionen auflisten", call("GET", f"/api/notes/{P}/versions", B)[0])
    denied("Version lesen", call("GET", f"/api/notes/{P}/versions/{V}", B)[0])
    denied("Export", call("GET", f"/api/notes/{P}/export", B, raw=True)[0])
    denied("Export mit Unterseiten", call("GET", f"/api/notes/{P}/export?sub=1", B, raw=True)[0])
    denied("Bild von A laden", call("GET", img, B, raw=True)[0])
    # --- B greift an: schreiben
    denied("Titel aendern", call("PATCH", f"/api/notes/{P}", B, {"title": "gehackt"})[0])
    denied("Inhalt aendern", call("PATCH", f"/api/notes/{P}", B, {"body": json.dumps({"blocks": []}), "format": "blocks"})[0])
    denied("Werte einer Zeile aendern", call("PATCH", f"/api/notes/{R}", B, {"props": {"status": "done"}})[0])
    denied("Schema aendern", call("PATCH", f"/api/notes/{D}", B, {"schema": {"props": [], "views": []}})[0])
    denied("Seite loeschen", call("DELETE", f"/api/notes/{P}", B)[0])
    denied("Seite verschieben", call("POST", f"/api/notes/{C}/move", B, {"parent_id": None, "index": 0})[0])
    denied("Seite duplizieren", call("POST", f"/api/notes/{P}/duplicate", B, {})[0])
    denied("Version wiederherstellen", call("POST", f"/api/notes/{P}/versions/{V}/restore", B)[0])
    denied("Papierkorb-Seite wiederherstellen", call("POST", f"/api/trash/{T}/restore", B)[0])
    denied("Papierkorb-Seite endgueltig loeschen", call("DELETE", f"/api/trash/{T}", B)[0])
    # --- B greift ueber EIGENE Seiten an
    s, d = call("POST", "/api/notes", B, {"title": "B-Kind unter A", "parent_id": P})
    denied("Unterseite unter A's Seite anlegen", s)
    s, d = call("POST", "/api/notes", B, {"title": "B-Zeile in A-DB", "parent_id": D, "props": {"status": "done"}})
    denied("Zeile in A's Datenbank anlegen", s)
    bp = call("POST", "/api/notes", B, {"title": "B-Seite"})[1]["id"]
    denied("eigene Seite unter A's Seite haengen (PATCH)", call("PATCH", f"/api/notes/{bp}", B, {"parent_id": P})[0])
    denied("eigene Seite unter A's Seite verschieben (move)", call("POST", f"/api/notes/{bp}/move", B, {"parent_id": P, "index": 0})[0])
    s = call("PATCH", f"/api/notes/{bp}", B, {"body": json.dumps({"blocks": [{"type": "image", "data": {"file": {"url": img}, "caption": ""}}]}), "format": "blocks"})[0]
    check(f"B: A's Bild in eigene Seite einbauen -> {s}", s == 400)
    s = call("PATCH", f"/api/notes/{bp}", B, {"cover": img})[0]
    check(f"B: A's Bild als eigenes Titelbild -> {s}", s == 400)
    bd = call("POST", "/api/notes", B, {"kind": "database", "title": "B-DB"})[1]["id"]
    sch = call("GET", f"/api/notes/{bd}", B)[1]["schema"]; sch["props"].append({"id": "rel", "name": "R", "type": "relation", "target": D})
    check("B: Relation auf A's Datenbank -> 400", call("PATCH", f"/api/notes/{bd}", B, {"schema": sch})[0] == 400)
    br = call("POST", "/api/notes", B, {"parent_id": bd, "title": "B-Zeile"})[1]["id"]
    # --- Listen / Suche zeigen nichts von A
    ids_b = {n["id"] for n in call("GET", "/api/notes", B)[1]}
    check("B: Seitenliste enthaelt nichts von A", not ids_b & {P, C, D, R, T})
    check("B: Suche findet A's Text nicht", call("POST", "/api/notes/search", B, {"q": "zauberwort-a"})[1] == [])
    check("B: Suche nach A's Tag leer", call("POST", "/api/notes/search", B, {"q": "", "tag": "geheimtag"})[1] == [])
    check("B: Papierkorb enthaelt nichts von A", all(n["id"] != T for n in call("GET", "/api/trash", B)[1]))
    # --- A's Daten sind unveraendert
    check("A's Daten nach allen Angriffen unveraendert", snap() == before)
    # --- A selbst kommt an alles heran (Gegenprobe)
    check("A: Seite lesen", call("GET", f"/api/notes/{P}", A)[0] == 200)
    check("A: Zeilen lesen", call("GET", f"/api/notes/{D}/rows", A)[0] == 200)
    check("A: Version lesen", call("GET", f"/api/notes/{P}/versions/{V}", A)[0] == 200)
    check("A: Export", call("GET", f"/api/notes/{P}/export?sub=1", A, raw=True)[0] == 200)
    check("A: eigenes Bild laden", call("GET", img, A, raw=True)[0] == 200)
    check("A: Papierkorb-Seite wiederherstellen", call("POST", f"/api/trash/{T}/restore", A)[0] == 200)
    # --- Aufraeumen
    con.execute("DELETE FROM note_version WHERE uid LIKE 'hk-test-%'"); con.execute("DELETE FROM note WHERE uid LIKE 'hk-test-%'")
    con.commit()
    import app
    app.files_gc(min_age=0)
    left = con.execute("SELECT (SELECT count(*) FROM note WHERE uid LIKE 'hk-test-%') + (SELECT count(*) FROM file WHERE uid LIKE 'hk-test-%')").fetchone()[0]
    check("Testdaten entfernt", left == 0)
    print(f"\n{'ALLES GRUEN' if not FAILS else str(len(FAILS)) + ' FEHLGESCHLAGEN'}")
    raise SystemExit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
