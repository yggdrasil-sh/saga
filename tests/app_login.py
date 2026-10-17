"""App-Anmeldung (PKCE-artig): /app/login -> /app/handoff -> /app/exchange (Probe-Instanz)."""
import json, os, base64, hashlib, secrets, sqlite3, sys, time, urllib.request, urllib.parse, http.client
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
REAL = os.environ.get("HK_TEST_REALUSER", "hk-test-a")              # vorhandenes Konto fuer den Erfolgsfall
GONE = "hk-test-gone"                                               # gibt es nicht (oder nicht mehr)
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u, "name": u}).encode())).decode()
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc; U = urllib.parse.urlparse(B)
def req(m, p, cookies=None, body=None, hdr=None):
    c = http.client.HTTPConnection(U.hostname, U.port, timeout=20)
    h = {"Host": HOST, "Content-Type": "application/json"}
    if cookies: h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    h.update(hdr or {})
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); raw = r.read()
    return r.status, dict(r.getheaders()), r.msg.get_all("set-cookie") or [], raw
res = []
def ok(n, cond): res.append(bool(cond)); print(("OK  " if cond else "FAIL"), n)
ver = lambda: secrets.token_urlsafe(48)
chal = lambda v: base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
import re
def handoff(cookies):
    """Bestaetigungsseite holen, dann den Link "Anmelden" (mit der neuen Sitzung) aufrufen."""
    s, h, sc, raw = req("GET", "/app/handoff", cookies)
    m = re.search(r'/app/handoff\?go=([A-Za-z0-9_-]+)', raw.decode("utf-8", "replace"))
    if s != 200 or not m:
        return s, h, sc, raw
    sess = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("saga=")), cookies.get("saga"))
    return req("GET", "/app/handoff?go=" + m.group(1), dict(cookies, saga=sess))
def flow(v, user=REAL):
    s, h, sc, _ = req("GET", "/app/login?scheme=saga&challenge=" + chal(v), {"saga": ck(user)})
    chc = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("hk_app_ch=")), "")
    scc = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("hk_app_sc=")), "")
    s2, h2, _, _ = handoff({"saga": ck(user), "hk_app_ch": chc, "hk_app_sc": scc})
    loc = h2.get("location", "")
    return s, h.get("location"), s2, loc, (loc.split("code=", 1)[1] if "code=" in loc else "")
ok("Ungueltiger Pruefwert -> 400", req("GET", "/app/login?challenge=kurz")[0] == 400)
ok("Unbekanntes Rueckkehr-Schema -> 400", req("GET", "/app/login?scheme=javascript&challenge=" + chal(ver()))[0] == 400)
_s, _h, _sc, _ = req("GET", "/app/login?challenge=" + chal(ver()), {"saga": ck(REAL)})
_ch = next((x.split(";")[0].split("=", 1)[1] for x in _sc if x.startswith("hk_app_ch=")), "")
ok("Aeltere Apps ohne Schema -> hlidskjalf://", handoff({"saga": ck(REAL), "hk_app_ch": _ch})[1].get("location", "").startswith("hlidskjalf://auth?code="))
# Rueckfrage vor der Uebergabe (N2): ohne Klick auf "Anmelden" kein Code
s, h, sc, raw = req("GET", "/app/handoff", {"saga": ck(REAL), "hk_app_ch": _ch})
ok("Handoff zeigt erst eine Rueckfrage (200, kein Code)", s == 200 and "location" not in {k.lower() for k in h} and b"/app/handoff?go=" in raw)
s, h, _, raw = req("GET", "/app/handoff?go=" + "x" * 24, {"saga": ck(REAL), "hk_app_ch": _ch})
ok("Falscher Bestaetigungs-Wert -> wieder Rueckfrage, kein Code", s == 200 and "auth?code=" not in h.get("location", ""))
# Login-CSRF (M1): Anmelde-Endpunkte der Apps nur mit Content-Type application/json
for p, bd in (("/app/pair", {"code": "x" * 32}), ("/app/exchange", {"code": "x" * 32, "verifier": "y" * 43}),
              ("/app/resume", {"device_token": "hkd_x"}), ("/app/forget", {"device_token": "hkd_x"})):
    s, _, sc, _ = req("POST", p, body=bd, hdr={"Content-Type": "text/plain"})
    ok(f"{p} mit text/plain (fremdes Formular) -> 415, keine Sitzung", s == 415 and not any(x.startswith("saga=") for x in sc))
ok("application/json; charset=utf-8 (Android) wird angenommen", req("POST", "/app/exchange", body={"code": "x" * 32, "verifier": "y" * 43},
                                                                  hdr={"Content-Type": "application/json; charset=utf-8"})[0] == 401)
ok("Aeltere Apps: Kopfzeile X-Hlidskjalf gilt weiter", req("GET", "/api/notes", {"saga": ck(REAL)}, hdr={"X-Hlidskjalf": "1"})[0] == 200)
s, loc, s2, loc2, code = flow(v := ver())
ok(f"/app/login -> {loc}", s == 303 and loc == "/app/handoff")
ok(f"/app/handoff -> saga://auth?code=…", s2 == 303 and loc2.startswith("saga://auth?code=") and len(code) > 30)
# Bughunt 2 (Android): Kontowechsel in der App - prompt=login zeigt trotz Browser-Sitzung die Anmeldemaske
s, h, sc, _ = req("GET", "/app/login?scheme=saga&prompt=login&challenge=" + chal(ver()), {"saga": ck(REAL)})
ok(f"prompt=login mit Sitzung -> Anmeldemaske statt Uebergabe ({h.get('location')})", s == 303 and h.get("location") == "/login?next=/app/handoff&prompt=login"
   and any(x.startswith("hk_app_t=") for x in sc))
s, h, _, raw = req("GET", "/login?next=/app/handoff&prompt=login", {"saga": ck(REAL)})
ok(f"/login?prompt=login mit Sitzung -> Formular ({s})", s == 200 and b'type="password"' in raw)
ok("/login ohne prompt mit Sitzung -> weiter wie bisher", req("GET", "/login?next=/app/handoff", {"saga": ck(REAL)})[0] == 303)
s, h, sc, raw = req("POST", "/app/exchange", body={"code": code, "verifier": ver()})
ok("Falsches Geheimnis -> 401", s == 401)
ok("Code nach Fehlversuch verbraucht", req("POST", "/app/exchange", body={"code": code, "verifier": v})[0] == 401)
_, _, _, _, code = flow(v := ver())
s, h, sc, raw = req("POST", "/app/exchange", body={"code": code, "verifier": v})
sess = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("saga=")), "")
ok(f"Richtiges Geheimnis -> 200 + Sitzung ({json.loads(raw).get('name')})", s == 200 and sess)
s, _, _, raw = req("GET", "/api/notes", {"saga": sess}, hdr={"X-Saga": "1"})
ok(f"Sitzung funktioniert ({len(json.loads(raw))} Seiten)", s == 200)
ok("Code zweimal -> 401", req("POST", "/app/exchange", body={"code": code, "verifier": v})[0] == 401)
cn = sqlite3.connect(DB); v2 = ver(); old = secrets.token_urlsafe(32)
cn.execute("INSERT INTO app_code (hash, uid, name, challenge, expires) VALUES (?,?,?,?,?)", (hashlib.sha256(old.encode()).hexdigest(), REAL, REAL, chal(v2), time.time() - 1)); cn.commit()
ok("Abgelaufener Code -> 401", req("POST", "/app/exchange", body={"code": old, "verifier": v2})[0] == 401)
ok("Handoff ohne App-Start (kein Pruefwert) -> 400", req("GET", "/app/handoff", {"saga": ck(REAL)})[0] == 400)
s, h, _, _ = req("GET", "/app/handoff", {"hk_app_ch": chal(ver())}); ok("Handoff ohne Anmeldung -> Login", s == 303 and h.get("location", "").startswith("/login"))
s, loc, s2, loc2, code = flow(v := ver(), user=GONE)
ok(f"Unbekannter Benutzer -> zur Anmeldung, kein Code ({loc}, {loc2})", loc.startswith("/login") and loc2.startswith("/login") and not code)
ok("Unsinnige Eingaben -> 400", req("POST", "/app/exchange", body={"code": "x", "verifier": "y"})[0] == 400)
# Geraete-Anmeldung: bleibt angemeldet, ohne erneutes Anmeldefenster
_, _, _, _, code = flow(v := ver())
s, h, sc, raw = req("POST", "/app/exchange", body={"code": code, "verifier": v, "device": "Test-Mac", "app_key": True})
d = json.loads(raw); dt = d.get("device_token", "")
ok(f"Anmeldung mit Geraet -> Geraete-Schluessel + App-Schluessel ({dt[:6]}…, {d.get('app_key', '')[:5]}…)", s == 200 and dt.startswith("hkd_") and d.get("app_key", "").startswith("hk_"))
s, h, sc, raw = req("POST", "/app/resume", body={"device_token": dt})
sess2 = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("saga=")), "")
ok("Sitzung erneuern ohne Anmeldefenster -> 200 + Sitzung", s == 200 and sess2)
ok("Erneuerte Sitzung funktioniert", req("GET", "/api/notes", {"saga": sess2}, hdr={"X-Saga": "1"})[0] == 200)
ok("Falscher Geraete-Schluessel -> 401", req("POST", "/app/resume", body={"device_token": "hkd_" + secrets.token_urlsafe(32)})[0] == 401)
ok("Unsinn -> 401", req("POST", "/app/resume", body={"device_token": "abc"})[0] == 401)
s, _, _, raw = req("GET", "/api/settings/devices", {"saga": sess2}, hdr={"X-Saga": "1"})
devs = json.loads(raw); mine = [x for x in devs if x["name"] == "Test-Mac"]
ok(f"Geraet in den Einstellungen gelistet ({len(devs)})", s == 200 and mine and "hash" not in mine[0])
ok("Fremde Person kann es nicht entfernen", req("DELETE", f"/api/settings/devices/{mine[0]['id']}", {"saga": ck("hk-test-b")}, hdr={"X-Saga": "1"})[0] == 404)
ok("Entfernen (widerrufen)", req("DELETE", f"/api/settings/devices/{mine[0]['id']}", {"saga": sess2}, hdr={"X-Saga": "1"})[0] == 200)
ok("Sitzung des widerrufenen Geraets endet sofort (BUG-002)", req("GET", "/api/notes", {"saga": sess2}, hdr={"X-Saga": "1"})[0] == 401)
ok("Widerrufenes Geraet -> 401", req("POST", "/app/resume", body={"device_token": dt})[0] == 401)
_, _, _, _, code = flow(v := ver())
dt2 = json.loads(req("POST", "/app/exchange", body={"code": code, "verifier": v, "device": "Test-Mac 2"})[3]).get("device_token", "")
req("POST", "/app/forget", body={"device_token": dt2})
ok("Abmelden in der App loescht den Geraete-Schluessel", req("POST", "/app/resume", body={"device_token": dt2})[0] == 401)
cn.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('hk-test-gone','x','weg',?, 'hkd_x', 0)", (hashlib.sha256(b"hkd_gesperrt").hexdigest(),)); cn.commit()
ok("Benutzer gibt es nicht (mehr) -> 403, Schluessel geloescht", req("POST", "/app/resume", body={"device_token": "hkd_gesperrt"})[0] == 403
   and not cn.execute("SELECT 1 FROM device_login WHERE uid='hk-test-gone'").fetchone())
_, _, _, _, code = flow(v := ver())
dt5 = json.loads(req("POST", "/app/exchange", body={"code": code, "verifier": v, "device": "Test-Mac 5"})[3]).get("device_token", "")
_, _, sc, _ = req("POST", "/app/resume", body={"device_token": dt5})
sess3 = next((x.split(";")[0].split("=", 1)[1] for x in sc if x.startswith("saga=")), "")
s, _, _, raw = req("POST", "/app/device", {"saga": sess3}, body={"device": "Test-Mac 3"}, hdr={"X-Saga": "1"})
dt3 = json.loads(raw).get("device_token", "") if s == 200 else ""
ok("Schon angemeldet -> Geraete-Schluessel ohne neue Anmeldung", dt3.startswith("hkd_") and req("POST", "/app/resume", body={"device_token": dt3})[0] == 200)
ok("Ohne Sitzung kein Geraete-Schluessel", req("POST", "/app/device", body={"device": "Test-Mac 4"}, hdr={"X-Saga": "1"})[0] == 401)
ok("Ohne Kopfzeile kein Geraete-Schluessel", req("POST", "/app/device", {"saga": sess3}, body={"device": "Test-Mac 4"})[0] == 401)
cn.execute("DELETE FROM app_token WHERE name LIKE 'Test-Mac%'"); cn.execute("DELETE FROM device_login WHERE name LIKE 'Test-Mac%'")
cn.execute("DELETE FROM app_code"); cn.commit()
print(f"\nAPP-LOGIN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER"); sys.exit(0 if all(res) else 1)
