"""Lesezeichen aus der Teilen-Erweiterung: POST /api/app/bookmarks, GET /api/app/bookmarks/categories (App-Schluessel)."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, cookie=None, b=None, bearer=None, lang=None):
    h = {"Host": HOST, "Content-Type": "application/json"}
    if cookie: h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer: h["Authorization"] = "Bearer " + bearer
    if lang: h["Accept-Language"] = lang
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            raw = r.read(); return r.status, json.loads(raw), raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {}), raw.decode(errors="replace")
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    T = call("POST", "/api/settings/tokens", A, {"name": "Testgeraet"})[1].get("token", "")
    TB = call("POST", "/api/settings/tokens", X, {"name": "Testgeraet B"})[1].get("token", "")
    ok("Schluessel fuer A und B", T.startswith("hk_") and TB.startswith("hk_"))
    ok("/api/bookmarks mit App-Schluessel weiter 401", call("GET", "/api/bookmarks", bearer=T)[0] == 401
       and call("POST", "/api/bookmarks", bearer=T, b={"url": "https://example.org"})[0] == 401)
    c, d, _ = call("GET", "/api/app/bookmarks/categories", bearer=T, lang="de")
    ok("Kategorien leer, Standard 'Lesezeichen'", c == 200 and d.get("categories") == [] and d.get("default") == "Lesezeichen")
    ok("Standard in der Sprache der Anfrage", call("GET", "/api/app/bookmarks/categories", bearer=T, lang="en")[1].get("default") == "Bookmarks")
    call("POST", "/api/bookmarks", A, {"name": "Wiki", "url": "https://wiki.example.org", "category": "Arbeit"})
    c, d, _ = call("POST", "/api/app/bookmarks", bearer=T, b={"name": "Beispiel", "url": "https://example.org/a?b=1", "category": "Lesen"})
    ok("Anlegen per App-Schluessel", c == 200 and d.get("key", "").startswith("bm") and d.get("category") == "Lesen" and d.get("icon") == "")
    c, d, _ = call("POST", "/api/app/bookmarks", bearer=T, b={"url": "example.net"})
    ok("Ohne Name/Kategorie: Host als Name, Standard-Kategorie", c == 200 and d.get("name") == "example.net" and d.get("url") == "https://example.net")
    lst = call("GET", "/api/bookmarks", A, lang="de")[1]
    ok("Erscheint unter Diensten (Sitzung)", any(b["name"] == "Beispiel" and b["category"] == "Lesen" for b in lst))
    c, d, raw = call("GET", "/api/app/bookmarks/categories", bearer=T, lang="de")
    ok(f"Kategorien ohne Doppelte, ohne Adressen ({d.get('categories')})", d.get("categories") == ["Arbeit", "Lesen", "Lesezeichen"] and "example" not in raw)
    ok("Anderes Konto sieht fremde Kategorien nicht", call("GET", "/api/app/bookmarks/categories", bearer=TB)[1].get("categories") == [])
    ok("javascript:-Adresse abgelehnt", call("POST", "/api/app/bookmarks", bearer=T, b={"url": "javascript:alert(1)"})[0] == 400)
    ok("Ungueltiges Symbol abgelehnt", call("POST", "/api/app/bookmarks", bearer=T, b={"url": "https://example.org", "icon": "../x"})[0] == 400)
    ok("Ohne Schluessel bzw. falscher Schluessel -> 401", call("POST", "/api/app/bookmarks", b={"url": "https://example.org"})[0] == 401
       and call("GET", "/api/app/bookmarks/categories", bearer="hk_" + "x" * 43)[0] == 401)
    ok("Browser-Sitzung an App-Endpunkten -> 401", call("POST", "/api/app/bookmarks", A, {"url": "https://example.org"})[0] == 401)
    bid = next(b["id"] for b in lst if b["name"] == "Beispiel")
    ok("Kein Aendern/Loeschen per App-Schluessel", call("PATCH", f"/api/bookmarks/{bid}", bearer=T, b={"name": "x"})[0] == 401
       and call("DELETE", f"/api/bookmarks/{bid}", bearer=T)[0] == 401 and call("DELETE", f"/api/app/bookmarks/{bid}", bearer=T)[0] in (404, 405))
    tid = call("GET", "/api/settings/tokens", A)[1][0]["id"]; call("DELETE", f"/api/settings/tokens/{tid}", A)
    ok("Widerrufener Schluessel -> 401", call("POST", "/api/app/bookmarks", bearer=T, b={"url": "https://example.org"})[0] == 401)
finally:
    cn = sqlite3.connect(DB)
    for q in ("DELETE FROM bookmark WHERE uid LIKE ?", "DELETE FROM app_token WHERE uid LIKE ?"):
        try: cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nAPP-LESEZEICHEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
