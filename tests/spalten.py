"""Spalten in Spalten werden beim Speichern aufgeloest (Probe-Instanz)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
ck = TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": "hk-test-a"}).encode())).decode()
H = {"Host": urllib.parse.urlparse(os.environ["BASE_URL"]).netloc, "Cookie": "saga=" + ck, "X-Saga": "1", "Content-Type": "application/json"}
def call(m, p, b=None):
    with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=H), timeout=20) as r: return json.loads(r.read())
P = lambda t: {"type": "paragraph", "data": {"text": t}}
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    nid = call("POST", "/api/notes", {"title": "Spaltentest"})["id"]
    inner = {"type": "columns", "data": {"cols": [{"blocks": [P("ToDos")]}, {"blocks": [P("Rechts innen")]}]}}
    body = {"blocks": [{"type": "columns", "data": {"cols": [{"blocks": [P("Kalender"), inner]}, {"blocks": [P("Galerie")]}]}},
                       {"type": "toggle", "data": {"title": "T", "blocks": [inner]}}, P("Unten")]}
    call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(body), "format": "blocks"})
    got = json.loads(call("GET", f"/api/notes/{nid}")["body"])["blocks"]
    c1 = [x["data"].get("text") or x["type"] for x in got[0]["data"]["cols"][0]["blocks"]]
    c2 = [x["data"].get("text") or x["type"] for x in got[0]["data"]["cols"][1]["blocks"]]
    tg = [x["data"].get("text") or x["type"] for x in got[1]["data"]["blocks"]]
    ok(f"Spalten in Spalte aufgeloest: Spalte 1 = {c1}, Spalte 2 = {c2}", c1 == ["Kalender", "ToDos", "Rechts innen"] and c2 == ["Galerie"])
    ok(f"Spalten im Toggle aufgeloest: {tg}", tg == ["ToDos", "Rechts innen"])
    ok("Normale Spalten bleiben", got[0]["type"] == "columns" and got[2]["data"]["text"] == "Unten")
finally:
    cn = sqlite3.connect(DB); cn.execute("DELETE FROM note WHERE uid='hk-test-a'"); cn.commit()
print(f"\nSPALTEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
