"""Funktions-Regressionstest: alle Seiten-Endpunkte fuer den EIGENEN Benutzer (Gegenstueck zu isolation.py).
Wegwerf-Benutzer hk-test-a, raeumt auf. Ausfuehren: tools/run-tests.sh (Produktion) oder tools/probe.sh (vor dem Ausrollen)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); A = S.sign(base64.b64encode(json.dumps({"uid": "hk-test-a"}).encode())).decode()
def call(m, p, b=None, raw=False):
    h = {"Host": urllib.parse.urlparse(os.environ["BASE_URL"]).netloc, "Cookie": "saga=" + A, "X-Saga": "1", "Content-Type": "application/json"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=15) as r:
            x = r.read(); return r.status, (x if raw else json.loads(x))
    except urllib.error.HTTPError as e: return e.code, {}
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
blk = lambda t: json.dumps({"blocks": [{"type": "paragraph", "data": {"text": t}}]})
def main():
    P = call("POST", "/api/notes", {"title": "Eltern"})[1].get("id"); K = call("POST", "/api/notes", {"title": "Kind", "parent_id": P})[1].get("id")
    ok("Seite + Unterseite anlegen", P and K)
    g = call("GET", f"/api/notes/{P}")[1]; ok("Seite lesen (mit Revision)", "rev" in g)
    ok("Inhalt speichern mit Revision", call("PATCH", f"/api/notes/{P}", {"body": blk(f'siehe <a href="/notes#{K}">Kind</a>'), "format": "blocks", "rev": g["rev"]})[0] == 200)
    ok("Veraltete Revision -> 409", call("PATCH", f"/api/notes/{P}", {"body": blk("x"), "format": "blocks", "rev": g["rev"]})[0] == 409)
    ok("Titel/Icon/Tags/Favorit", call("PATCH", f"/api/notes/{P}", {"title": "Eltern2", "icon": "🌟", "tags": ["a"], "pinned": True})[0] == 200)
    ok("Rueckverweise", any(x["id"] == P for x in call("GET", f"/api/notes/{K}/backlinks")[1]))
    ok("Verschieben", call("POST", f"/api/notes/{K}/move", {"parent_id": None, "index": 0})[0] == 200)
    ok("Duplizieren", call("POST", f"/api/notes/{P}/duplicate", {"title": "Kopie"})[1].get("id"))
    c, md = call("GET", f"/api/notes/{P}/export", raw=True); ok("Export", c == 200 and b"Eltern2" in md)
    call("PATCH", f"/api/notes/{P}", {"body": blk("Fassung 2"), "format": "blocks"})
    vs = call("GET", f"/api/notes/{P}/versions")[1]; ok("Versionen", isinstance(vs, list) and len(vs) > 0)
    ok("Version lesen + wiederherstellen", vs and call("GET", f"/api/notes/{P}/versions/{vs[0]['id']}")[0] == 200
       and call("POST", f"/api/notes/{P}/versions/{vs[0]['id']}/restore")[0] == 200)
    D = call("POST", "/api/notes", {"kind": "database", "title": "DB"})[1]["id"]; Z = call("POST", "/api/notes", {"kind": "database", "title": "Ziel"})[1]["id"]
    s = call("GET", f"/api/notes/{D}")[1]["schema"]; s["props"].append({"id": "rel", "name": "Rel", "type": "relation", "target": Z, "two_way": True})
    ok("Schema mit zweiseitiger Relation", call("PATCH", f"/api/notes/{D}", {"schema": s})[0] == 200)
    s = call("GET", f"/api/notes/{D}")[1]["schema"]; s["props"][0]["name"] = "Status neu"
    ok("Schema aendern bei vorhandener Relation (Regression 2026-10-07)", call("PATCH", f"/api/notes/{D}", {"schema": s})[0] == 200)
    z = call("POST", "/api/notes", {"parent_id": Z, "title": "z"})[1]["id"]
    r = call("POST", "/api/notes", {"parent_id": D, "title": "Zeile", "props": {"status": "doing", "rel": [z]}})[1]["id"]
    ok("Datenbank-Zeilen", call("GET", f"/api/notes/{D}/rows")[1]["rows"][0]["props"].get("rel") == [z])
    pair = next(p for p in call("GET", f"/api/notes/{D}")[1]["schema"]["props"] if p["id"] == "rel").get("pair")
    ok("Relation: Gegenseite synchron", pair and call("GET", f"/api/notes/{z}")[1]["props"].get(pair) == [r])
    ok("Loeschen (Papierkorb)", call("DELETE", f"/api/notes/{P}")[0] == 200 and call("GET", f"/api/notes/{P}")[0] == 404)
try:
    main()
finally:
    cn = sqlite3.connect(DB); cn.execute("delete from note where uid like ?", ("hk-test-%",)); cn.execute("delete from note_version where uid like ?", ("hk-test-%",)); cn.commit()
print(f"\nFUNKTIONSTEST {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
