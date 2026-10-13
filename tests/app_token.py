"""App-Zugangsschluessel + /api/app/* (Probe-Instanz)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, cookie=None, b=None, bearer=None):
    h = {"Host": HOST, "Content-Type": "application/json"}
    if cookie: h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer: h["Authorization"] = "Bearer " + bearer
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            raw = r.read(); return r.status, json.loads(raw), raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] == b"{" or raw[:1] == b"[" else {}), raw.decode(errors="replace")
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    c, d, _ = call("POST", "/api/settings/tokens", A, {"name": "Mein iPhone"}); T = d.get("token", "")
    ok(f"Schluessel erzeugt ({d.get('prefix')}…)", c == 200 and T.startswith("hk_") and len(T) > 40)
    c, d, raw = call("GET", "/api/settings/tokens", A); ok("Liste ohne Klartext und ohne Hash", c == 200 and T not in raw and "hash" not in raw and d[0]["name"] == "Mein iPhone")
    row = sqlite3.connect(DB).execute("SELECT hash FROM app_token WHERE uid='hk-test-a'").fetchone()[0]; ok("In der DB nur der Hash", T not in row and len(row) == 64)
    c, d, _ = call("POST", "/api/app/capture", bearer=T, b={"kind": "page", "title": "Artikel", "url": "https://example.org/a?b=1&c=2", "text": "Zitat <script>alert(1)</script>\n\nZweiter Absatz"})
    ok("Teilen als Seite", c == 200 and d.get("kind") == "page")
    pg = call("GET", f"/api/notes/{d['id']}", A)[1]; inbox = call("GET", f"/api/notes/{pg['parent_id']}", A)[1]
    ok(f"Liegt im Eingang ({inbox.get('icon')} {inbox.get('title')})", inbox.get("title") == "Eingang" and inbox.get("icon") == "📥")
    ok("Eingeschleuster Code unschaedlich, Link erhalten", "<script>" not in pg["body"] and "&lt;script&gt;" in pg["body"] and 'href=\\"https://example.org/a?b=1&amp;c=2\\"' in pg["body"])
    ok("javascript:-Link abgelehnt", call("POST", "/api/app/capture", bearer=T, b={"url": "javascript:alert(1)"})[0] == 400)
    c, d, _ = call("POST", "/api/app/capture", bearer=T, b={"kind": "task", "title": "Rückruf Müller", "url": "https://example.org/x"})
    ok("Teilen als Aufgabe", c == 200 and d.get("kind") == "task")
    c, d, _ = call("POST", "/api/app/capture", bearer=T, b={"kind": "task", "title": "Siri-Aufgabe", "due": "2026-12-24"})
    ok("Aufgabe mit Faelligkeit (Kurzbefehl)", c == 200 and any(t["text"] == "Siri-Aufgabe" and t["due"] == "2026-12-24" for t in call("GET", "/api/tasks", A)[1]["open"]))
    ok("Ungueltiges Datum abgelehnt", call("POST", "/api/app/capture", bearer=T, b={"kind": "task", "title": "x", "due": "morgen"})[0] == 400)
    c, d, _ = call("GET", "/api/app/today", bearer=T); ok(f"Heute: {len(d.get('tasks', []))} Aufgabe(n), {len(d.get('events', []))} Termine", c == 200 and any("Rückruf Müller" in t["text"] for t in d["tasks"]))
    ok("Schluessel an normalen Endpunkten -> 401", call("GET", "/api/notes", bearer=T)[0] == 401 and call("GET", "/api/settings/tokens", bearer=T)[0] == 401)
    ok("Browser-Anmeldung an App-Endpunkten -> 401", call("GET", "/api/app/today", A)[0] == 401 and call("POST", "/api/app/capture", A, {"title": "x"})[0] == 401)
    ok("Falscher Schluessel -> 401", call("GET", "/api/app/today", bearer="hk_" + "x" * 43)[0] == 401)
    call("DELETE", f"/api/notes/{inbox['id']}", A)
    c, d, _ = call("POST", "/api/app/capture", bearer=T, b={"title": "Nach Loeschen"}); np = call("GET", f"/api/notes/{d['id']}", A)[1]
    ok("Eingang geloescht -> neu angelegt", np["parent_id"] != inbox["id"] and call("GET", f"/api/notes/{np['parent_id']}", A)[1]["title"] == "Eingang")
    tid = call("GET", "/api/settings/tokens", A)[1][0]["id"]
    ok("Anderer Benutzer kann Schluessel nicht widerrufen", call("DELETE", f"/api/settings/tokens/{tid}", X)[0] == 404)
    ok("Widerrufen", call("DELETE", f"/api/settings/tokens/{tid}", A)[0] == 200 and call("GET", "/api/app/today", bearer=T)[0] == 401)
    for i in range(10): call("POST", "/api/settings/tokens", A, {"name": f"t{i}"})
    ok("Hoechstens 10 Schluessel", call("POST", "/api/settings/tokens", A, {"name": "zuviel"})[0] == 400)
finally:
    cn = sqlite3.connect(DB)
    for q in ("DELETE FROM note WHERE uid LIKE ?", "DELETE FROM task WHERE uid LIKE ?", "DELETE FROM app_token WHERE uid LIKE ?", "DELETE FROM user_kv WHERE uid LIKE ?"):
        try: cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nAPP-SCHLUESSEL {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
