"""Archivieren: Seite + Unterseiten aus Baum/Suche nehmen und genau so zurueckholen (Probe-Instanz)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, b=None, who=None):
    h = {"Host": HOST, "Content-Type": "application/json", "Cookie": "saga=" + (who or A), "X-Saga": "1"}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
def tree(): return {p["id"]: p for p in call("GET", "/api/notes")[1]}
def search(q): return [h["id"] for h in call("POST", "/api/notes/search", {"q": q})[1]]
try:
    a = call("POST", "/api/notes", {"title": "Projekt Zebra"})[1]["id"]
    b = call("POST", "/api/notes", {"title": "Zebra Unterseite", "parent_id": a})[1]["id"]
    c = call("POST", "/api/notes", {"title": "Zebra Enkel", "parent_id": b})[1]["id"]
    ok("Vorher: in der Suche", set(search("zebra")) >= {a, b, c})
    st, d = call("POST", f"/api/notes/{a}/archive", {"archived": True})
    t = tree(); ok(f"Archiviert mit Unterseiten ({d.get('archived')})", st == 200 and d.get("archived") == 3 and all(t[i]["archived"] for i in (a, b, c)))
    ok("Nicht mehr in der Suche", not set(search("zebra")) & {a, b, c})
    ok("Per Link weiter erreichbar (archived=true)", call("GET", f"/api/notes/{a}")[1].get("archived") is True)
    ok("Andere Person: kein Zugriff", call("POST", f"/api/notes/{a}/archive", {"archived": False}, who=X)[0] == 404 and tree()[a]["archived"])
    st, d = call("POST", f"/api/notes/{a}/archive", {"archived": False}); t = tree()
    ok(f"Zurueckgeholt mit Unterseiten ({d.get('restored')})", d.get("restored") == 3 and not any(t[i]["archived"] for i in (a, b, c)))
    # Unterseite vorher einzeln archiviert -> bleibt beim Zurueckholen der Eltern archiviert
    call("POST", f"/api/notes/{c}/archive", {"archived": True}); call("POST", f"/api/notes/{a}/archive", {"archived": True})
    call("POST", f"/api/notes/{a}/archive", {"archived": False}); t = tree()
    ok("Einzeln archivierte Unterseite bleibt im Archiv", not t[a]["archived"] and not t[b]["archived"] and t[c]["archived"])
    call("POST", f"/api/notes/{c}/archive", {"archived": False})
    # Unterseite zurueckholen, waehrend die Eltern archiviert sind -> nach oben
    call("POST", f"/api/notes/{a}/archive", {"archived": True})
    call("POST", f"/api/notes/{b}/archive", {"archived": False}); t = tree()
    ok("Unterseite ohne Eltern zurueck -> oberste Ebene", not t[b]["archived"] and t[b]["parent_id"] is None and t[a]["archived"])
    ok("Enkel kommt mit (gleicher Zeitstempel)", not t[c]["archived"] and t[c]["parent_id"] == b)
    ok("Doppelt archivieren ist harmlos", call("POST", f"/api/notes/{a}/archive", {"archived": True})[1].get("archived") == 0)
finally:
    cn = sqlite3.connect(DB)
    try: cn.execute("DELETE FROM note WHERE uid LIKE ?", ("hk-test-%",))
    except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nARCHIV {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
