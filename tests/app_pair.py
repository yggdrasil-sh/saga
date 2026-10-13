"""App-Anmeldung per QR-Code: Code im Web erzeugen -> App tauscht ihn gegen Geraete-Anmeldung (einmalig, 2 Minuten)."""
import base64, http.client, json, os, sqlite3, sys, time, urllib.parse
from itsdangerous import TimestampSigner
B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u, "name": u}).encode())).decode()  # noqa: E731
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


def req(m, p, cookie=None, body=None, api=False, key=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=20)
    h = {"Host": HOST, "Content-Type": "application/json"}
    if key:
        h["Authorization"] = "Bearer " + key
    if cookie:
        h["Cookie"] = "saga=" + cookie
    if api:
        h["X-Saga"] = "1"
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); raw = r.read()
    sc = [x.split(";")[0].split("=", 1)[1] for x in (r.msg.get_all("set-cookie") or []) if x.startswith("saga=")]
    try:
        d = json.loads(raw)
    except ValueError:
        d = raw.decode("utf-8", "replace")
    return r.status, d, (sc[0] if sc else "")


A, X = ck("hk-test-a"), ck("hk-test-b")
try:
    ok("Ohne Anmeldung kein Code", req("POST", "/api/settings/pair", api=True)[0] == 401)
    s, d, _ = req("POST", "/api/settings/pair", A, api=True)
    ok(f"Code erzeugen -> QR (SVG) + Link ({s})", s == 200 and d["qr"].startswith("data:image/svg+xml") and "/app/pair?c=" in d["link"])
    code = urllib.parse.parse_qs(urllib.parse.urlparse(d["link"]).query)["c"][0]
    ok("Link zeigt auf BASE_URL", d["link"].startswith(os.environ["BASE_URL"].rstrip("/") + "/app/pair?c="))
    ok("Status: noch nicht benutzt", req("GET", f"/api/settings/pair/{d['id']}", A, api=True)[1] == {"used_by": None, "expired": False})
    ok("Andere Person sieht den Status nicht", req("GET", f"/api/settings/pair/{d['id']}", X, api=True)[0] == 404)
    s, html, _ = req("GET", "/app/pair?c=" + code)
    ok("Kamera-Seite: Knopf 'In der App oeffnen', Code dabei nicht verbraucht", s == 200 and "saga://pair?" in html and "hlidskjalf://pair?" in html)
    ok("Falscher Code -> 401", req("POST", "/app/pair", body={"code": "x" * 32, "device": "Test-iPhone"})[0] == 401)
    s, d2, sess = req("POST", "/app/pair", body={"code": code, "device": "Test-iPhone QR", "app_key": True})
    ok(f"Code einloesen -> Geraete-Schluessel + Sitzung ({s})", s == 200 and d2.get("device_token", "").startswith("hkd_") and sess
       and d2.get("server") == os.environ["BASE_URL"].rstrip("/") and d2.get("app_key", "").startswith("hk_"))
    ok("Sitzung funktioniert", req("GET", "/api/notes", sess, api=True)[0] == 200)
    ok("Geraete-Schluessel funktioniert", req("POST", "/app/resume", body={"device_token": d2["device_token"]})[0] == 200)
    ok("Code nur einmal", req("POST", "/app/pair", body={"code": code, "device": "Zweitgeraet"})[0] == 401)
    ok("Status: benutzt von Test-iPhone QR", req("GET", f"/api/settings/pair/{d['id']}", A, api=True)[1].get("used_by") == "Test-iPhone QR")
    # Eine Liste: Geraet mit seinem Widget-Schluessel; von Hand erzeugte Schluessel extra
    ap = req("GET", "/api/settings/apps", A, api=True)[1]
    dv = next((x for x in ap["devices"] if x["name"] == "Test-iPhone QR"), None)
    ok("Geraeteliste: Geraet mit Widget-Schluessel", dv and dv["key"] and dv["key"]["prefix"] == d2["app_key"][:9])
    ok("Automatischer Schluessel steht nicht extra", not any(k["name"] == "Test-iPhone QR (automatisch)" for k in ap["keys"]))
    ok("Widget-Schluessel funktioniert", req("GET", "/api/app/today", key=d2["app_key"])[0] == 200)
    mk = req("POST", "/api/settings/tokens", A, body={"name": "Test-iPhone Hand"}, api=True)[1]
    ap = req("GET", "/api/settings/apps", A, api=True)[1]
    ok("Von Hand erzeugt -> unter 'Weitere Schluessel'", any(k["id"] == mk["id"] for k in ap["keys"]))
    s, dd, _ = req("POST", "/api/settings/pair", A, api=True)
    cc = urllib.parse.parse_qs(urllib.parse.urlparse(dd["link"]).query)["c"][0]
    d5 = req("POST", "/app/pair", body={"code": cc, "device": "Test-iPhone Alt"})[1]
    ok("App nennt beim Erneuern ihren Schluessel -> dem Geraet zugeordnet",
       req("POST", "/app/resume", body={"device_token": d5["device_token"], "app_key": mk["token"]})[0] == 200
       and next(x for x in req("GET", "/api/settings/apps", A, api=True)[1]["devices"] if x["name"] == "Test-iPhone Alt")["key"]["id"] == mk["id"])
    req("POST", "/app/resume", body={"device_token": d5["device_token"], "app_key": d2["app_key"]})
    ok("Fremder Schluessel wird nicht umgehaengt", next(x for x in req("GET", "/api/settings/apps", A, api=True)[1]["devices"]
                                                      if x["name"] == "Test-iPhone QR")["key"]["prefix"] == d2["app_key"][:9])
    ok("Geraet abmelden", req("DELETE", f"/api/settings/devices/{dv['id']}", A, api=True)[0] == 200)
    ok("-> sein Widget-Schluessel ist mit weg", req("GET", "/api/app/today", key=d2["app_key"])[0] == 401)
    req("POST", "/app/forget", body={"device_token": d5["device_token"]})
    ok("Abmelden in der App nimmt den Schluessel mit", req("GET", "/api/app/today", key=mk["token"])[0] == 401)
    s, d3, _ = req("POST", "/api/settings/pair", A, api=True)
    c3 = urllib.parse.parse_qs(urllib.parse.urlparse(d3["link"]).query)["c"][0]
    cn = sqlite3.connect("x"); cn.execute("UPDATE pair_code SET expires=? WHERE pid=?", (time.time() - 1, d3["id"]))
    ok("Abgelaufener Code -> 401", req("POST", "/app/pair", body={"code": c3, "device": "Spaet"})[0] == 401)
    ok("Status: abgelaufen", req("GET", f"/api/settings/pair/{d3['id']}", A, api=True)[1].get("expired") is True)
    s, d4, _ = req("POST", "/api/settings/pair", ck("hk-test-gone"), api=True)
    ok("Unbekanntes Konto bekommt keinen Code", s == 401)
finally:
    cn = sqlite3.connect("x")
    cn.execute("DELETE FROM pair_code WHERE uid LIKE 'hk-test-%'")
    cn.execute("DELETE FROM device_login WHERE name LIKE 'Test-iPhone%'")
    cn.execute("DELETE FROM app_token WHERE name LIKE 'Test-iPhone%'")
print(f"\nAPP-PAIR {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
