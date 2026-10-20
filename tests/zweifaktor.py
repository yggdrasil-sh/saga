"""Zweiter Faktor (2.1): Passkeys (WebAuthn, Software-Authenticator tests/softauthn.py), Authenticator-App (TOTP),
Wiederherstellungscodes, Zwischenschritt nach dem Passwort, Ratenbegrenzung, Richtlinie "2FA verlangen", Admin-Login
mit 2FA und Zuruecksetzen, App-Anmeldung mit 2FA, Chat-Reauth mit Passkey/Code, Upgrade-Pfad (Sitzungen/Geraete bleiben).

Passkeys brauchen eine Adresse mit Rechnernamen - der Test-Stack hat eine IP in BASE_URL. Deshalb startet dieser Test
eine eigene Instanz mit BASE_URL=http://localhost:<port> (gleiche Datenbank) und prueft an der normalen Probe-Instanz
nur die Meldung "BASE_URL passt nicht fuer Passkeys". Wegwerf-Konten zf-*, raeumt auf.
Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET, Postgres ueber db.py)."""
import base64, hashlib, hmac, json, os, re, secrets, struct, subprocess, sys, time, urllib.parse, urllib.request
from itsdangerous import TimestampSigner
import auth
from db import connect
from standalone import Client
from softauthn import SoftAuthenticator, b64e

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
P, PA = int(os.environ.get("HK_2FA_PORT", "8097")), int(os.environ.get("HK_2FA_ADMIN_PORT", "8096"))
ORIGIN, AORIGIN = f"http://localhost:{P}", f"http://localhost:{PA}"
S2, A2 = urllib.parse.urlparse(f"http://127.0.0.1:{P}"), urllib.parse.urlparse(f"http://127.0.0.1:{PA}")
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD = os.environ.get("HK_2FA_STATE", "/tmp/hkstage-2fa")
LOG = os.path.join(SD, "server.log")
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Zweifaktor-Pass-123"
res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n + ("" if c or info is None else f" -> {info}"))


def ck(u, **extra):
    """Sitzung wie vor dem Update (ohne login_at): gilt, ist aber nicht 'frisch'."""
    return S.sign(base64.b64encode(json.dumps(dict({"uid": u, "name": u, "pwc": time.time() + 5}, **extra)).encode())).decode()


def client(user=None, base=S2, host=f"localhost:{P}", **extra):
    c = Client(base, host)
    if user:
        c.ck["saga"] = ck(user, **extra)
    return c


# --- TOTP im Test selbst (unabhaengig vom Server) ------------------------------------------------------------
def totp(secret, step):
    h = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** 6).zfill(6)


def step_now():
    """Aktueller 30-s-Schritt - kurz vor einer Grenze warten (Server und Test sollen denselben Schritt sehen)."""
    if time.time() % 30 > 26:
        time.sleep(30 - time.time() % 30 + 0.3)
    return int(time.time() // 30)


def next_code(sec, used):
    """Code fuer einen noch nicht benutzten Schritt im Fenster (+-1) -> (code, schritt); wartet notfalls."""
    while True:
        st = step_now()
        s = max(used["last"] + 1, st - 1)
        if s <= st + 1:
            used["last"] = s
            return totp(sec, s), s
        time.sleep(30 - time.time() % 30 + 0.3)


def snapshot_settings():
    with connect() as c:
        return dict(c.execute("SELECT k, v FROM setting").fetchall())


def restore_settings(snap):
    with connect() as c, c:
        for k in [r[0] for r in c.execute("SELECT k FROM setting").fetchall()]:
            if k not in snap:
                c.execute("DELETE FROM setting WHERE k=?", (k,))
        for k, v in snap.items():
            c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, v))
    auth._settings_cache["v"] = None


def settings_wait():
    time.sleep(5.5)          # Einstellungen werden im Server 5 s zwischengespeichert


def cleanup():
    with connect() as c, c:
        for t in ("device_login", "app_token", "user_kv", "app_code", "mfa_passkey", "mfa_totp", "mfa_recovery", "mfa_challenge",
                  "chat_identity", "chat_device", "note", "notification", "pair_code"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'zf-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'zf-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE '%zf-%' OR key LIKE 'pk:ip:%' OR key LIKE 'mfa:ip:%' OR key LIKE 'amfa:ip:%'")
    auth.save_setting("mfa", {"require": "none", "oidc_mfa": False, "oidc_acr": ""})


def start_server():
    """Eigene Instanz mit Rechnernamen in BASE_URL (Passkeys gehen nicht mit IP-Adressen)."""
    os.makedirs(SD, exist_ok=True)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)                 # nicht die Test-Hilfen (pgshim) laden
    env.update(PORT=str(P), ADMIN_PORT=str(PA), HOST="127.0.0.1", ADMIN_HOST="127.0.0.1", BASE_URL=ORIGIN, ADMIN_URL=AORIGIN,
               STATE_DIR=SD, CANONICAL_REDIRECT="false", SAGA_UPDATE_CHECK="0")
    p = subprocess.Popen([sys.executable, "run.py"], cwd=APP, env=env, stdout=open(LOG, "w"), stderr=subprocess.STDOUT)
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{P}/healthz", timeout=2)
            urllib.request.urlopen(f"http://127.0.0.1:{PA}/healthz", timeout=2)
            return p
        except Exception:  # noqa: BLE001 - startet noch
            time.sleep(0.5)
    return p


def unit():
    """Ohne Server: RFC-6238-Testwerte (fester Zeitpunkt), Fenster, Wiederverwendung, SSO-Mehr-Faktor (amr/acr)."""
    from saga import mfa
    sec = b"12345678901234567890"
    ok("RFC 6238: T=59 -> 287082, T=1111111109 -> 081804, T=1234567890 -> 005924",
       [mfa.totp_at(sec, t // 30) for t in (59, 1111111109, 1234567890)] == ["287082", "081804", "005924"])
    ok("Fester Zeitpunkt: Code passt (Schritt 1)", mfa.totp_match(sec, "287082", now=59) == 1)
    ok("+-1 Schritt: 30 s spaeter noch gueltig, 60 s spaeter nicht",
       mfa.totp_match(sec, "287 082", now=89) == 1 and mfa.totp_match(sec, "287082", now=119) is None)
    ok("Derselbe Schritt gilt nur einmal (after)", mfa.totp_match(sec, "287082", now=59, after=1) is None)
    ok("Unsinn (Buchstaben, 5 Ziffern) -> nein", mfa.totp_match(sec, "28708a", now=59) is None and mfa.totp_match(sec, "28708", now=59) is None)
    pol = {"oidc_acr": "gold, urn:test:mfa"}
    ok("SSO: amr pwd allein reicht nicht, pwd+otp / mfa / hwk schon",
       not mfa.oidc_mfa_ok({"amr": ["pwd"]}, pol) and mfa.oidc_mfa_ok({"amr": ["pwd", "otp"]}, pol)
       and mfa.oidc_mfa_ok({"amr": ["mfa"]}, pol) and mfa.oidc_mfa_ok({"amr": "hwk"}, pol))
    ok("SSO: erlaubter acr-Wert reicht, anderer nicht", mfa.oidc_mfa_ok({"acr": "gold"}, pol) and not mfa.oidc_mfa_ok({"acr": "silver"}, pol)
       and mfa.oidc_acr_values(pol) == "gold urn:test:mfa")
    ok("Origin wie im Browser (Standard-Port weg)", mfa.origin_of("https://Saga.Example.org:443/x") == "https://saga.example.org"
       and mfa.origin_of("http://localhost:8097/") == "http://localhost:8097")


def main():
    cleanup()
    unit()
    auth.create_user("zf-user", PW, "Zweifaktor Test")
    auth.create_user("zf-adm", PW, "Zweifaktor Admin", is_admin=True)
    auth.create_user("zf-sso", None, "SSO Test", source="oidc")
    auth.create_user("zf-adm2", PW, "Zweifaktor Admin 2", is_admin=True)
    auth.create_user("zf-plain", PW, "Zweifaktor ohne Faktor")
    srv = start_server()
    try:
        flows()
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except subprocess.TimeoutExpired:
            srv.kill()
    n = open(LOG, encoding="utf-8", errors="replace").read().count("Traceback")
    ok(f"Eigene Instanz: Serverfehler {n}", n == 0)


def flows():
    # --- Probe-Instanz (IP in BASE_URL): klare Meldung statt stillem Fehlschlag ---------------------------------
    c0 = Client(B, HOST); c0.ck["saga"] = ck("zf-user", login_at=int(time.time()))
    s, d = c0.api("GET", "/api/account/security")
    ok(f"BASE_URL mit IP: Passkeys aus, mit Erklaerung ({str(d.get('passkey_problem'))[:60]}…)", s == 200 and "IP" in str(d.get("passkey_problem")))
    s, d = c0.api("POST", "/api/account/passkeys/options")
    ok("... Passkey anlegen -> 409 mit derselben Meldung", s == 409 and "IP" in d.get("error", ""))
    ok("... Anmeldeseite ohne Passkey-Knopf", 'id="pk-box"' not in Client(B, HOST).req("GET", "/login")[2])

    # --- Eigene Instanz: Anmeldung ohne 2FA, Bestaetigen vor Aenderungen ----------------------------------------
    w = client()
    s, loc, html = w.req("GET", "/login")
    ok("Anmeldeseite: Passkey-Knopf + Autofill (autocomplete … webauthn)", 'id="pk-box"' in html and "username webauthn" in html
       and f'data-origin="{ORIGIN}"' in html)
    s, loc, _ = w.post("/login", {"username": "zf-user", "password": PW})
    ok(f"Ohne 2FA: Passwort -> direkt angemeldet ({loc})", s == 303 and loc == "/")
    s, d = w.api("GET", "/api/account/security")
    ok("Status: unterstuetzt, aus, keine Passkey-Probleme, frisch", s == 200 and d["supported"] and not d["enabled"] and not d["passkey_problem"] and d["fresh"])
    old = client("zf-user")                                  # Sitzung ohne frische Anmeldung (wie vor dem Update)
    s, d = old.api("POST", "/api/account/passkeys/options")
    ok("Nicht frisch: Passkey anlegen -> 403 reauth", s == 403 and d.get("reauth") is True)
    ok("Bestaetigen mit falschem Passwort -> 403", old.api("POST", "/api/account/security/confirm", {"password": "falsch-falsch-1"})[0] == 403)
    ok("Bestaetigen mit Passwort -> 200, danach frisch", old.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200
       and old.api("GET", "/api/account/security")[1]["fresh"])

    # --- Passkey anlegen ------------------------------------------------------------------------------------
    pk = SoftAuthenticator()
    s, d = w.api("POST", "/api/account/passkeys/options")
    opts = d.get("options", {})
    ok("Optionen: RP-ID localhost, auffindbar (residentKey required), Benutzerkennung nicht der Name",
       s == 200 and opts["rp"]["id"] == "localhost" and opts["authenticatorSelection"]["residentKey"] == "required"
       and opts["user"]["name"] == "zf-user" and base64.urlsafe_b64decode(opts["user"]["id"] + "==") != b"zf-user")
    s, d = w.api("POST", "/api/account/passkeys", {"credential": pk.create(opts, ORIGIN), "name": "Testgeraet"})
    codes = d.get("recovery") or []
    ok(f"Passkey gespeichert, erster Faktor -> 10 Wiederherstellungscodes ({codes[:1]})", s == 200 and len(codes) == 10
       and all(re.fullmatch(r"[a-z2-9]{4}(-[a-z2-9]{4}){3}", x) for x in codes))
    with connect() as c:
        hs = [r[0] for r in c.execute("SELECT hash FROM mfa_recovery WHERE uid='zf-user'").fetchall()]
        raw = json.dumps(c.execute("SELECT * FROM mfa_recovery WHERE uid='zf-user'").fetchall(), default=str)
    ok("Codes nur als Hash gespeichert", len(hs) == 10 and not any(x in raw or x.replace("-", "") in raw for x in codes))
    s, d = w.api("POST", "/api/account/passkeys/options")
    ok(f"M3b: zweiter Passkey trotz frischer Anmeldung erst nach Bestaetigung ({s})", s == 403 and d.get("reauth") is True, d)
    ok("M3b: ... Bestaetigung mit Passwort", w.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200)
    s, d = w.api("POST", "/api/account/passkeys/options")
    s, d = w.api("POST", "/api/account/passkeys", {"credential": pk.create(d["options"], "http://127.0.0.1:%d" % P), "name": "falsch"})
    ok(f"Falsche Adresse beim Anlegen -> klare Meldung mit BASE_URL ({d.get('error', '')[:70]}…)", s == 400 and "BASE_URL" in d.get("error", "")
       and ORIGIN in d.get("error", ""))
    s, d = w.api("POST", "/api/account/passkeys", {"credential": pk.create(opts, ORIGIN)})
    ok("Challenge nur einmal verwendbar", s == 400)
    s, d = w.api("GET", "/api/account/security")
    ok("Liste: 1 Passkey mit Name, an", s == 200 and d["enabled"] and [p["name"] for p in d["passkeys"]] == ["Testgeraet"] and d["recovery_left"] == 10)
    pid = d["passkeys"][0]["id"]
    ok("Umbenennen", w.api("PATCH", f"/api/account/passkeys/{pid}", {"name": "Laptop"})[0] == 200)
    ok("Fremdes Konto kann ihn nicht umbenennen/loeschen", client("zf-adm", login_at=int(time.time()), sec_at=int(time.time())).api("DELETE", f"/api/account/passkeys/{pid}")[0] == 404)

    # --- Anmeldung: Passwort + Passkey ------------------------------------------------------------------------
    w = client()
    s, loc, _ = w.post("/login", {"username": "zf-user", "password": PW})
    ok(f"Passwort richtig + 2FA aktiv -> Zwischenschritt ({loc})", s == 303 and loc == "/login/2fa")
    ok("Zwischenschritt ist keine Sitzung (API 401, Dashboard -> Anmeldung)", w.api("GET", "/api/notes")[0] == 401 and w.req("GET", "/")[1] == "/login")
    s, _, html = w.req("GET", "/login/2fa")
    ok("Seite Zwischenschritt: Passkey-Knopf + Feld fuer Wiederherstellungscode", s == 200 and 'data-mode="2fa"' in html and 'name="code"' in html)
    ok("Passkey-Endpunkte ohne X-Saga -> 400", w.req("POST", "/login/passkey/options", form={"mode": "2fa"})[0] == 400)
    s, d = w.api("POST", "/login/passkey/options", {"mode": "2fa"})
    o2 = d.get("options", {})
    ok("Optionen 2FA: nur die Passkeys des Kontos", s == 200 and [x["id"] for x in o2.get("allowCredentials", [])] == [b64e(next(iter(pk.creds)))])
    bad = pk.get(o2, "http://127.0.0.1:%d" % P)
    s, d = w.api("POST", "/login/passkey", {"credential": bad})
    ok("Antwort von falscher Adresse -> 401 mit BASE_URL-Hinweis", s == 401 and "BASE_URL" in d.get("error", ""))
    s, d = w.api("POST", "/login/passkey/options", {"mode": "2fa"})
    good = pk.get(d["options"], ORIGIN)
    s, d = w.api("POST", "/login/passkey", {"credential": good})
    ok(f"Passkey als zweiter Faktor -> angemeldet ({d.get('next')})", s == 200 and d.get("next") == "/" and w.api("GET", "/api/notes")[0] == 200)
    w2 = client(); w2.post("/login", {"username": "zf-user", "password": PW})
    w2.api("POST", "/login/passkey/options", {"mode": "2fa"})
    ok("Wiederholte (alte) Antwort -> abgewiesen", w2.api("POST", "/login/passkey", {"credential": good})[0] == 401)
    s, d = w2.api("POST", "/login/passkey/options", {"mode": "2fa"})
    ok("Kopierter Passkey (Zaehler steigt nicht) -> abgewiesen",
       w2.api("POST", "/login/passkey", {"credential": pk.get(d["options"], ORIGIN, count_step=0)})[0] == 401)

    # --- Anmeldung ohne Passwort (auffindbarer Passkey) ---------------------------------------------------------
    w3 = client(); w3.req("GET", "/login")
    s, d = w3.api("POST", "/login/passkey/options", {"mode": "login"})
    ol = d.get("options", {})
    ok("Ohne Passwort: keine Kontoliste, Benutzerpruefung verlangt", s == 200 and not ol.get("allowCredentials") and ol.get("userVerification") == "required")
    s, d = w3.api("POST", "/login/passkey", {"credential": pk.get(ol, ORIGIN, uv=False)})
    ok("... Geraet ohne Benutzerpruefung (PIN/Biometrie) -> abgewiesen", s == 401)
    s, d = w3.api("POST", "/login/passkey/options", {"mode": "login"})
    s, d = w3.api("POST", "/login/passkey", {"credential": pk.get(d["options"], ORIGIN)})
    ok(f"... mit Passkey angemeldet ({d.get('next')})", s == 200 and w3.api("GET", "/api/notes")[0] == 200)
    stranger = SoftAuthenticator(); stranger.create({"rp": {"id": "localhost"}, "user": {"id": "eA"}, "challenge": "eA"}, ORIGIN)
    w4 = client(); w4.req("GET", "/login")
    s, d = w4.api("POST", "/login/passkey/options", {"mode": "login"})
    s, d = w4.api("POST", "/login/passkey", {"credential": stranger.get(d["options"], ORIGIN)})
    ok("Unbekannter Passkey -> 401 (unknown, Browser kann ihn vergessen)", s == 401 and d.get("unknown") is True)

    # --- Authenticator-App (TOTP) -----------------------------------------------------------------------------
    s, d = w3.api("POST", "/api/account/totp/setup")
    ok(f"M3b: weiterer Faktor trotz frischer Anmeldung erst nach Bestaetigung (403 reauth) ({s})", s == 403 and d.get("reauth") is True, d)
    s, d = w3.api("POST", "/api/account/security/options")
    s, d = w3.api("POST", "/api/account/security/confirm", {"passkey": pk.get(d.get("options") or {}, ORIGIN)})
    ok(f"M3b: Bestaetigung mit dem Passkey von vor dieser Anmeldung ({s})", s == 200, d)
    s, d = w3.api("POST", "/api/account/totp/setup")
    key = (d.get("secret") or "").replace(" ", "")
    ok("TOTP einrichten: QR-Code als SVG (im Server erzeugt) + Schluessel + otpauth-Adresse",
       s == 200 and d.get("qr", "").startswith("data:image/svg+xml") and len(key) == 32 and d.get("uri", "").startswith("otpauth://totp/")
       and "secret=" + key in d["uri"] and "period=30" in d["uri"])
    with connect() as c:
        stored = c.execute("SELECT secret FROM mfa_totp WHERE uid='zf-user'").fetchone()[0]
    ok("Geheimnis verschluesselt gespeichert", stored.startswith("enc:") and key not in stored)
    sec = base64.b32decode(key)
    st = step_now()
    ok("Falscher Code -> abgewiesen", w3.api("POST", "/api/account/totp/confirm", {"code": "000000" if totp(sec, st) != "000000" else "111111"})[0] == 400)
    s, d = w3.api("POST", "/api/account/totp/confirm", {"code": totp(sec, st - 1)})
    ok("Bestaetigen mit Code (Schritt davor, +-1) -> eingerichtet, keine neuen Codes", s == 200 and d.get("recovery") is None)
    w5 = client(); s, loc, _ = w5.post("/login", {"username": "zf-user", "password": PW})
    s, loc, _ = w5.post("/login/2fa", {"code": totp(sec, st)})
    used = {"last": st}
    ok(f"Anmeldung: Passwort + Code -> angemeldet ({loc})", s == 303 and loc == "/" and w5.api("GET", "/api/notes")[0] == 200)
    w6 = client(); w6.post("/login", {"username": "zf-user", "password": PW})
    s, loc, html = w6.post("/login/2fa", {"code": totp(sec, st)})
    ok("Derselbe Code ein zweites Mal -> abgewiesen", s == 401 and "Der Code stimmt nicht" in html)
    s, loc, _ = w6.post("/login/2fa", {"code": codes[0].upper().replace("-", " ")})
    ok("Wiederherstellungscode (Gross/Leerzeichen egal) -> angemeldet", s == 303 and loc == "/")
    w7 = client(); w7.post("/login", {"username": "zf-user", "password": PW})
    ok("Wiederherstellungscode nur einmal", w7.post("/login/2fa", {"code": codes[0]})[0] == 401)
    ok("Status: 9 Codes uebrig", w5.api("GET", "/api/account/security")[1]["recovery_left"] == 9)

    # --- Ratenbegrenzung und Zwischenzustand -----------------------------------------------------------------
    with connect() as c, c:                    # der Fehlversuch von eben ("nur einmal") zaehlt mit - fuer den Test leeren
        n_before = c.execute("SELECT COUNT(*) FROM login_fail WHERE key LIKE 'mfa:ui:zf-user|%'").fetchone()[0]
        c.execute("DELETE FROM login_fail WHERE key LIKE 'mfa:%zf-user%'")
    ok(f"Fehlversuche beim zweiten Faktor werden gezaehlt ({n_before}), Erfolg setzt zurueck", n_before == 1)
    w8 = client(); w8.post("/login", {"username": "zf-user", "password": PW})
    sts = [w8.post("/login/2fa", {"code": "%06d" % (123450 + i)})[0] for i in range(5)]
    s, loc, html = w8.post("/login/2fa", {"code": totp(sec, st + 1)}, src="/login")
    ok(f"5 Fehlversuche, dann gesperrt (auch mit richtigem Code) {sts + [s]}", sts == [401] * 5 and s in (400, 429))
    w9 = client(); w9.post("/login", {"username": "zf-user", "password": PW})
    s, loc, html = w9.post("/login/2fa", {"code": totp(sec, st + 1)})
    ok("Sperre gilt fuer das Konto (neuer Browser, gleiche Adresse) -> 429", s == 429 and "Zu viele Fehlversuche" in html)
    with connect() as c, c:
        c.execute("DELETE FROM login_fail WHERE key LIKE 'mfa:%zf-user%'")
    wa = client()
    wa.ck["saga"] = S.sign(base64.b64encode(json.dumps({"mfa_u": "zf-user", "mfa_at": int(time.time()) - 400}).encode())).decode()
    ok("Zwischenschritt aelter als 5 Minuten -> zurueck zur Anmeldung", wa.req("GET", "/login/2fa")[1] == "/login")
    wb = client(); wb.post("/login", {"username": "zf-user", "password": PW})
    wc = client()                              # anderer Browser ohne dieses Cookie
    ok("Zwischenschritt an das Cookie gebunden (anderer Browser -> 400)", wc.post("/login/2fa", {"code": totp(sec, st + 1)}, src="/login")[0] == 400)

    # --- App-Anmeldung mit 2FA ----------------------------------------------------------------------------------
    ver = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).decode().rstrip("=")
    ap = client()
    s, loc, _ = ap.req("GET", f"/app/login?scheme=saga&challenge={chal}")
    ap.req("GET", loc)
    s, loc, _ = ap.post("/login", {"username": "zf-user", "password": PW}, src="/login?next=/app/handoff")
    ok(f"App: Passwort -> Zwischenschritt ({loc})", s == 303 and loc == "/login/2fa")
    code, _ = next_code(sec, used)
    s, loc, _ = ap.post("/login/2fa", {"code": code})
    ok(f"App: Code -> weiter zur Uebergabe ({loc})", s == 303 and loc == "/app/handoff")
    s, loc, html = ap.req("GET", "/app/handoff")
    m = re.search(r"saga://auth\?code=([A-Za-z0-9_-]+)", html)
    ok("App: frisch angemeldet -> Code fuer die App ohne Rueckfrage", s == 200 and m)
    s, _, raw = Client(S2, f"localhost:{P}").req("POST", "/app/exchange", js={"code": m.group(1) if m else "", "verifier": ver, "device": "zf-Handy"})
    dtok = json.loads(raw).get("device_token", "") if s == 200 else ""
    ok("App: Code einloesbar -> Geraete-Anmeldung", s == 200 and dtok.startswith("hkd_"))

    # --- Chat "Neu beginnen": Bestaetigen mit Passkey oder Code -----------------------------------------------------
    ch = client("zf-user")
    s, d = ch.api("GET", "/api/chat/me")
    ok(f"Chat: reauth=password, Methoden {d.get('reauth_methods')}", s == 200 and d.get("reauth") == "password"
       and set(d.get("reauth_methods") or []) == {"password", "passkey", "totp"})
    ok("Chat: falscher Code -> 403", ch.api("POST", "/api/chat/reauth", {"code": "000001"})[0] == 403)
    with connect() as c, c:
        c.execute("DELETE FROM login_fail WHERE key LIKE 'mfa:%zf-user%'")
    s, d = ch.api("POST", "/api/chat/reauth/options")
    s, d = ch.api("POST", "/api/chat/reauth", {"passkey": pk.get(d["options"], ORIGIN)})
    ok("Chat: Passkey -> 200, danach frisch", s == 200 and ch.api("GET", "/api/chat/me")[1].get("reauth") is None)
    ch2 = client("zf-user")
    s, d = ch2.api("POST", "/api/chat/reauth", {"code": next_code(sec, used)[0]})
    ok("Chat: Code der Authenticator-App -> 200", s == 200)

    # --- Neue Codes, Faktoren entfernen ---------------------------------------------------------------------------
    wr = client(); wr.post("/login", {"username": "zf-user", "password": PW}); wr.post("/login/2fa", {"code": next_code(sec, used)[0]})
    s, d = wr.api("POST", "/api/account/recovery")
    ok(f"M3b: neue Wiederherstellungscodes trotz frischer Anmeldung erst nach Bestaetigung ({s})", s == 403 and d.get("reauth") is True, d)
    ok("M3b: ... Bestaetigung mit Passwort", wr.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200)
    s, d = wr.api("POST", "/api/account/recovery")
    new = d.get("recovery") or []
    ok("Neue Wiederherstellungscodes (10), alte ungueltig", s == 200 and len(new) == 10 and set(new).isdisjoint(codes))
    wd = client(); wd.post("/login", {"username": "zf-user", "password": PW})
    ok("... alter Code geht nicht mehr", wd.post("/login/2fa", {"code": codes[1]})[0] == 401)
    ok("... neuer Code geht", wd.post("/login/2fa", {"code": new[0]})[:2] == (303, "/"))

    # --- Richtlinie: 2FA fuer alle verlangen -------------------------------------------------------------------------
    auth.save_setting("mfa", {"require": "all", "oidc_mfa": False, "oidc_acr": ""}); settings_wait()
    s, d = w5.api("DELETE", "/api/account/totp")
    ok(f"M3: Authenticator entfernen trotz frischer Anmeldung nur nach Bestaetigung (403 reauth) ({s})", s == 403 and d.get("reauth") is True, d)
    s, d = w5.api("DELETE", f"/api/account/passkeys/{pid}")
    ok(f"M3: ... ebenso Passkey loeschen ({s})", s == 403 and d.get("reauth") is True, d)
    s, d = w5.api("POST", "/api/account/security/confirm", {"password": "falsch-falsch-1"})
    s2, _ = w5.api("DELETE", "/api/account/totp")
    ok(f"M3: falsches Passwort bestaetigt nicht ({s}, {s2})", s == 403 and s2 == 403)
    s, d = w5.api("POST", "/api/account/security/confirm", {"password": PW})
    ok(f"M3: Bestaetigung mit Passwort ({s})", s == 200, d)
    s, d = w5.api("DELETE", "/api/account/totp")
    ok("Verlangt: Authenticator entfernen geht (Passkey bleibt)", s == 200 and d["passkeys"] and not d["totp"])
    s, d = w5.api("DELETE", f"/api/account/passkeys/{pid}")
    ok("Verlangt: letzten Faktor loeschen -> 409", s == 409)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('zf-adm','zf-adm','alt',?, 'hkd_alt', ?)",
                  (hashlib.sha256(b"hkd_zf-alt-geraet").hexdigest(), time.time()))
    oldsess = client("zf-adm")
    wn = client(); s, loc, _ = wn.post("/login", {"username": "zf-adm", "password": PW})
    ok(f"Verlangt, kein Faktor: angemeldet, aber zur Einrichtung ({loc})", s == 303 and loc == "/security")
    ok("... Dashboard/Seiten fuehren zur Einrichtung", wn.req("GET", "/")[1] == "/security" and wn.req("GET", "/notes")[1] == "/security")
    s, _, html = wn.req("GET", "/security")
    ok("... Einrichtungsseite erklaert es", s == 200 and 'data-need="1"' in html and "verlangt" in html)
    # Bughunt 2 (Android): /security?user=<Anmeldename> aus der App - anderes Konto im Browser -> deutlicher Hinweis
    s, _, html = wn.req("GET", "/security?user=zf-andere")
    ok("?user= anderes Konto: Hinweis mit Abmelden-und-neu-anmelden", s == 200 and 'id="sec-other"' in html and "zf-andere" in html
       and "/logout?next=/security" in html)
    s, _, html = wn.req("GET", "/security?user=ZF-ADM")
    ok("?user= eigenes Konto: kein Hinweis", s == 200 and 'id="sec-other"' not in html)
    s, loc, html = client().req("GET", "/security?user=zf-adm")
    ok(f"?user= ohne Sitzung: nur Anmeldeseite, kein Name ({loc})", s == 303 and loc == "/login?next=/security" and "zf-adm" not in (html or ""))
    s, loc, _ = client("zf-user").req("GET", "/logout?next=/security")
    ok(f"/logout?next=/security -> Anmeldung mit Ziel /security ({loc})", s == 303 and loc == "/login?bye=1&next=/security")
    ok("... nicht ausgesperrt (API geht)", wn.api("GET", "/api/notes")[0] == 200)
    ok("Upgrade-Pfad: bestehende Sitzung bleibt gueltig", oldsess.api("GET", "/api/notes")[0] == 200 and oldsess.req("GET", "/")[0] == 200)
    s, _, _ = Client(S2, f"localhost:{P}").req("POST", "/app/resume", js={"device_token": "hkd_zf-alt-geraet"})
    ok("Upgrade-Pfad: gekoppeltes Geraet bleibt angemeldet (/app/resume)", s == 200)
    ok("... und das App-Geraet von oben auch", Client(S2, f"localhost:{P}").req("POST", "/app/resume", js={"device_token": dtok})[0] == 200)
    s, d = wn.api("POST", "/api/account/totp/setup")
    asec = base64.b32decode(d["secret"].replace(" ", ""))
    ast = step_now()
    s, d = wn.api("POST", "/api/account/totp/confirm", {"code": totp(asec, ast)})
    ok("Admin richtet Authenticator ein -> Codes", s == 200 and len(d.get("recovery") or []) == 10)
    s, loc, _ = wn.req("GET", "/")
    ok("Danach: Dashboard ohne Umweg", s == 200)
    s, d = wn.api("GET", "/api/account/security")
    ok(f"M3b: in dieser Sitzung angelegte Authenticator-App wird nicht zum Bestaetigen angeboten ({d.get('methods')})",
       s == 200 and d.get("totp") and d.get("methods") == ["password"])
    apk = SoftAuthenticator()
    s, d = wn.api("POST", "/api/account/passkeys/options")
    ok(f"M3b: zweiter Faktor (Passkey nach TOTP) -> 403 reauth ({s})", s == 403 and d.get("reauth") is True, d)
    s, d = wn.api("POST", "/api/account/security/confirm", {"code": totp(asec, ast + 1)})
    ok(f"M3b: Code der eben eingerichteten Authenticator-App bestaetigt nichts ({s}: {str(d.get('error'))[:50]}…)",
       s == 403 and "nach dieser Anmeldung" in d.get("error", ""), d)
    ok("M3b: ... Passwort bestaetigt", wn.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200)
    s, d = wn.api("POST", "/api/account/passkeys/options")
    ok("Admin legt Passkey an", wn.api("POST", "/api/account/passkeys", {"credential": apk.create(d["options"], ORIGIN), "name": "Admin-Key"})[0] == 200)
    s, d = wn.api("POST", "/api/account/security/options")
    ok(f"M3b: Passkey-Optionen zum Bestaetigen ohne den eben angelegten Passkey -> 409 ({s})", s == 409, d)
    auth.save_setting("mfa", {"require": "none", "oidc_mfa": False, "oidc_acr": ""}); settings_wait()

    # --- Admin-Bereich: Anmeldung mit 2FA, Zuruecksetzen ---------------------------------------------------------------
    ad = Client(A2, f"localhost:{PA}")
    s, loc, _ = ad.post("/login", {"username": "zf-adm", "password": PW})
    ok(f"Admin: Passwort -> Zwischenschritt ({loc})", s == 303 and loc == "/login/2fa")
    ok("Admin: Zwischenschritt ist keine Sitzung", ad.req("GET", "/users")[1] == "/login")
    s, _, html = ad.req("GET", "/login/2fa")
    ok("Admin: Passkey moeglich (gleiche Domain wie BASE_URL)", s == 200 and 'id="pk-box"' in html and f'data-origin="{AORIGIN}"' in html)
    s, d = ad.api("POST", "/login/passkey/options")
    s, d = ad.api("POST", "/login/passkey", {"credential": apk.get(d["options"], AORIGIN)})
    ok("Admin: mit Passkey angemeldet", s == 200 and ad.req("GET", "/users")[0] == 200)
    ad2 = Client(A2, f"localhost:{PA}"); ad2.post("/login", {"username": "zf-adm", "password": PW})
    acode, _ = next_code(asec, {"last": ast})
    ok("Admin: falscher Code -> 401", ad2.post("/login/2fa", {"code": "000002" if acode != "000002" else "000003"})[0] == 401)
    s, loc, _ = ad2.post("/login/2fa", {"code": acode})
    ok(f"Admin: mit Code angemeldet ({loc})", s == 303 and loc == "/")
    uid_ = auth.get_user("zf-user")["id"]
    s, _, html = ad.req("GET", f"/users/{uid_}")
    ok("Admin: Benutzerseite zeigt 2FA mit Zuruecksetzen", s == 200 and 'value="mfa_reset"' in html and "Passkeys" in html)
    s, loc, _ = ad.post(f"/users/{uid_}", {"action": "mfa_reset"})
    with connect() as c:
        kv = c.execute("SELECT v FROM user_kv WHERE uid='zf-user' AND k='mfa_reset'").fetchone()
    ok("Admin: 2FA zurueckgesetzt, Protokoll im Konto (wer, wann)", s == 303
       and client("zf-user").api("GET", "/api/account/security")[1]["enabled"] is False and kv and json.loads(kv[0])["by"] == "zf-adm")
    ok("... im Admin-Bereich sichtbar", "Zuletzt zurückgesetzt" in ad.req("GET", f"/users/{uid_}")[2])
    wz = client(); s, loc, _ = wz.post("/login", {"username": "zf-user", "password": PW})
    ok("... Anmeldung danach nur mit Passwort", s == 303 and loc == "/")
    snap = snapshot_settings()                 # das Formular speichert alle allgemeinen Einstellungen - danach zurueck
    try:
        cur = auth.settings(fresh=True)
        s, loc, html = ad.post("/settings", {"mfa_require": "admins", "mfa_oidc_acr": "gold, <script>", "title": cur["title"],
                                             "local_login": "1", "publish_enabled": "1", "share_enabled": "1", "chat_enabled": "1",
                                             "group_prefix": cur["group_prefix"], "allowed_hosts": cur["allowed_hosts"]})
        st_ = auth.settings(fresh=True)["mfa"]
        ok(f"Admin: Richtlinie speichern (admins, acr gefiltert: {st_.get('oidc_acr')!r})", s == 303 and st_["require"] == "admins" and st_["oidc_acr"] == "gold")
        s, _, html = ad.req("GET", "/settings")
        ok("Admin: Einstellungsseite zeigt die Richtlinie", s == 200 and 'name="mfa_require"' in html and '<option value="admins" selected' in html)
    finally:
        restore_settings(snap)
    auth.save_setting("mfa", {"require": "none", "oidc_mfa": False, "oidc_acr": ""})

    m3b()

    # --- SSO-Konten: kein Saga-2FA ---------------------------------------------------------------------------------
    so = client("zf-sso", login_at=int(time.time()))
    s, d = so.api("GET", "/api/account/security")
    ok("SSO-Konto: nicht unterstuetzt (macht der Anbieter)", s == 200 and d["supported"] is False)
    ok("SSO-Konto: TOTP einrichten -> 403", so.api("POST", "/api/account/totp/setup")[0] == 403)
    policy_everywhere()


def forged_confirm(user, auth_, since):
    """Bestaetigung mit einem Passkey ohne die Optionen-Abfrage (die neue Passkeys ausfiltert): Challenge direkt in
    der Datenbank, Sitzung seit 'since' - so wie ein Angreifer die Antwort selbst bauen koennte."""
    cid, chal = secrets.token_urlsafe(18), secrets.token_bytes(32)
    with connect() as c, c:
        c.execute("INSERT INTO mfa_challenge (id, uid, kind, challenge, expires) VALUES (?,?,?,?,?)",
                  (cid, user, "confirm", b64e(chal), time.time() + 120))
    h = client(user, since=since, login_at=since, wa_c=cid)
    return h, h.api("POST", "/api/account/security/confirm", {"passkey": auth_.get({"challenge": b64e(chal), "rpId": "localhost"}, ORIGIN)})


def m3b():
    """2.4 (M3b): Eine frisch angemeldete, gekaperte Sitzung legt einen eigenen Passkey an, bestaetigt sich damit und
    entfernt die Faktoren des Opfers. Jetzt: weiterer Faktor/neue Codes nur nach strenger Bestaetigung; Faktoren, die
    nach dem Beginn der Sitzung angelegt wurden, bestaetigen nichts. Ersteinrichtung weiter mit frischer Anmeldung."""
    auth.create_user("zf-m3b", PW, "M3b Opfer")
    t0 = int(time.time()) - 5
    v = client(); s, loc, _ = v.post("/login", {"username": "zf-m3b", "password": PW})
    victim = SoftAuthenticator()
    s, d = v.api("POST", "/api/account/passkeys/options")
    s, d = v.api("POST", "/api/account/passkeys", {"credential": victim.create(d.get("options") or {}, ORIGIN), "name": "Opfer"})
    ok(f"M3b: Ersteinrichtung (noch kein Faktor) mit frischer Anmeldung allein ({s})", s == 200 and len(d.get("recovery") or []) == 10, d)
    vpid = (v.api("GET", "/api/account/security")[1].get("passkeys") or [{}])[0].get("id")
    s, d = v.api("GET", "/api/account/security")
    ok(f"M3b: Methoden ohne den in dieser Sitzung angelegten Passkey ({d.get('methods')})", d.get("methods") == ["password"])
    ok("M3b: Passkey-Optionen zum Bestaetigen -> 409 (kein aelterer Passkey)", v.api("POST", "/api/account/security/options")[0] == 409)
    _, (s, d) = forged_confirm("zf-m3b", victim, t0)
    ok(f"M3b: Bestaetigung mit Passkey von nach dem Sitzungsbeginn -> 403 ({s})", s == 403 and "nach dieser Anmeldung" in d.get("error", ""), d)
    s, d = v.api("POST", "/api/account/totp/setup")
    ok(f"M3b: Authenticator-App als zweiter Faktor -> 403 reauth ({s})", s == 403 and d.get("reauth") is True, d)
    ok("M3b: ... nach Bestaetigung mit Passwort", v.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200)
    s, d = v.api("POST", "/api/account/totp/setup")
    tsec = base64.b32decode((d.get("secret") or "").replace(" ", ""))
    tst = step_now()
    s, d = v.api("POST", "/api/account/totp/confirm", {"code": totp(tsec, tst)})
    ok(f"M3b: ... eingerichtet, keine neuen Codes ({s})", s == 200 and d.get("recovery") is None, d)
    tused = {"last": tst}

    # Angriff wie in der Probe: gekaperte, frisch angemeldete Sitzung (ohne Bestaetigung)
    time.sleep(0.2)
    now = round(time.time(), 3)
    h = client("zf-m3b", login_at=now, since=now)
    att = SoftAuthenticator()
    s1, d1 = h.api("POST", "/api/account/passkeys/options")
    s2, d2 = h.api("POST", "/api/account/passkeys", {"credential": att.create({"rp": {"id": "localhost"}, "user": {"id": "eA"}, "challenge": "eA"}, ORIGIN)})
    s3, d3 = h.api("POST", "/api/account/totp/setup")
    s4, d4 = h.api("POST", "/api/account/recovery")
    ok(f"M3b: gekaperte frische Sitzung: Passkey-Optionen/Anlegen, TOTP, neue Codes -> 403 reauth ({s1}, {s2}, {s3}, {s4})",
       [s1, s2, s3, s4] == [403] * 4 and all(x.get("reauth") is True for x in (d1, d2, d3, d4)))
    s, d = h.api("POST", "/api/account/security/options")
    s, d = h.api("POST", "/api/account/security/confirm", {"passkey": att.get({"challenge": d.get("options", {}).get("challenge", "eA"), "rpId": "localhost"}, ORIGIN)})
    ok(f"M3b: ... Bestaetigung mit fremdem Passkey -> 403 ({s})", s == 403)
    s5, _ = h.api("DELETE", "/api/account/totp")
    s6, _ = h.api("DELETE", f"/api/account/passkeys/{vpid}")
    st = client("zf-m3b").api("GET", "/api/account/security")[1]
    ok(f"M3b: ... Faktoren des Opfers bleiben ({s5}, {s6}, {[p['name'] for p in st.get('passkeys', [])]}, totp {st.get('totp')})",
       s5 == 403 and s6 == 403 and [p["name"] for p in st.get("passkeys", [])] == ["Opfer"] and st.get("totp") is True)
    with connect() as c:
        n_att = c.execute("SELECT COUNT(*) FROM mfa_passkey WHERE uid='zf-m3b'").fetchone()[0]
    ok("M3b: kein Passkey des Angreifers gespeichert", n_att == 1)

    # Rechtmaessig: Sitzung, die nach dem Einrichten der Faktoren begann, bestaetigt mit Passkey bzw. Code und legt
    # einen weiteren Passkey an
    old, (s, d) = forged_confirm("zf-m3b", victim, now)
    ok(f"M3b: spaetere Sitzung: Bestaetigung mit dem Passkey -> 200 ({s})", s == 200, d)
    s, d = old.api("GET", "/api/account/security")
    ok(f"M3b: ... Methoden mit Passkey und Authenticator-App ({d.get('methods')})", set(d.get("methods") or []) == {"password", "passkey", "totp"})
    s, d = old.api("POST", "/api/account/passkeys/options")
    s, d = old.api("POST", "/api/account/passkeys", {"credential": SoftAuthenticator().create(d.get("options") or {}, ORIGIN), "name": "Zweit"})
    ok(f"M3b: ... danach weiteren Passkey anlegen ({s})", s == 200 and d.get("recovery") is None, d)
    oc = client("zf-m3b", since=now, login_at=now)
    ok("M3b: spaetere Sitzung bestaetigt mit Code der Authenticator-App",
       oc.api("POST", "/api/account/security/confirm", {"code": next_code(tsec, tused)[0]})[0] == 200)
    nc = client("zf-m3b", since=t0, login_at=t0)
    s, d = nc.api("POST", "/api/account/security/confirm", {"code": next_code(tsec, tused)[0]})
    ok(f"M3b: ... Sitzung von vor der Authenticator-App nicht ({s})", s == 403 and "nach dieser Anmeldung" in d.get("error", ""), d)
    # Sitzung von vor dem Update (ohne since/login_at): ein darin angelegter Passkey bestaetigt in ihr nichts
    pre = client("zf-m3b")
    ok("M3b: alte Sitzung bestaetigt mit Passwort", pre.api("POST", "/api/account/security/confirm", {"password": PW})[0] == 200)
    na = SoftAuthenticator()
    s, d = pre.api("POST", "/api/account/passkeys/options")
    ok("M3b: ... legt einen Passkey an", pre.api("POST", "/api/account/passkeys", {"credential": na.create(d.get("options") or {}, ORIGIN), "name": "Alt"})[0] == 200)
    s, d = pre.api("POST", "/api/account/security/options")
    ok("M3b: ... Optionen zum Bestaetigen ohne diesen Passkey", s == 200 and b64e(next(iter(na.creds))) not in
       [x["id"] for x in d["options"].get("allowCredentials", [])])
    s, d = pre.api("POST", "/api/account/security/confirm", {"passkey": na.get(d["options"], ORIGIN, cred=b64e(next(iter(na.creds))))})
    ok(f"M3b: ... und bestaetigt damit nichts ({s})", s == 403 and "nach dieser Anmeldung" in d.get("error", ""), d)
    with connect() as c, c:
        c.execute("DELETE FROM login_fail WHERE key LIKE '%zf-m3b%'")


def admin_key(html):
    m = re.search(r'id="adm-mfa-key">([A-Z2-7 ]+)<', html)
    return base64.b32decode(m.group(1).replace(" ", "")) if m else b""


def app_handoff_start(c):
    """App-Anmeldung in einem Browser c starten -> Ziel nach /app/handoff (Location)."""
    ver = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).decode().rstrip("=")
    s, loc, _ = c.req("GET", f"/app/login?scheme=saga&challenge={chal}")
    return c.req("GET", loc) if loc == "/app/handoff" else (s, loc, "")


def policy_everywhere():
    """Richtlinie gilt konsequent (Bughunt 2): Admin-Bereich (Einrichtung dort, nicht ausgesperrt), neue App-Anmeldungen
    bei "alle"; bestehende Admin-Sitzungen, Geraete und Sitzungen bleiben. Standard "niemand" aendert nichts. Notfall per CLI."""
    auth.save_setting("mfa", {"require": "none", "oidc_mfa": False, "oidc_acr": ""}); settings_wait()
    # --- Standard (niemand): wie bisher -------------------------------------------------------------------------
    a0 = Client(A2, f"localhost:{PA}")
    s, loc, _ = a0.post("/login", {"username": "zf-adm2", "password": PW})
    ok(f"Standard: Admin ohne Faktor -> direkt im Admin-Bereich ({loc})", s == 303 and loc == "/" and a0.req("GET", "/users")[0] == 200)
    ok("Standard: Einrichtungsseite ohne Zwischenschritt -> Anmeldung", Client(A2, f"localhost:{PA}").req("GET", "/login/2fa/setup")[1] == "/login")
    pl = client("zf-plain", login_at=int(time.time()))
    ok("Standard: QR-Kopplung erzeugen geht ohne Faktor", pl.api("POST", "/api/settings/pair")[0] == 200)
    s, loc, _ = app_handoff_start(client("zf-plain"))
    ok(f"Standard: App-Uebergabe ohne Faktor (Rueckfrage, keine Einrichtung) ({s})", s == 200 and loc == "")
    with connect() as c, c:                    # Kopplungs-Code von VOR dem Einschalten der Richtlinie
        c.execute("INSERT INTO pair_code (hash, pid, uid, name, created, expires) VALUES (?,?,?,?,?,?)",
                  (hashlib.sha256(b"zf-pair-vorher-0123456789abcdef").hexdigest(), "zfpid1", "zf-plain", "zf-plain", time.time(), time.time() + 120))

    # --- "nur Admins" ---------------------------------------------------------------------------------------------
    auth.save_setting("mfa", {"require": "admins", "oidc_mfa": False, "oidc_acr": ""}); settings_wait()
    ok("Admins: bestehende Admin-Sitzung bleibt gueltig", a0.req("GET", "/users")[0] == 200)
    ad = Client(A2, f"localhost:{PA}")
    s, loc, _ = ad.post("/login", {"username": "zf-adm2", "password": PW})
    ok(f"Admins: Admin ohne Faktor -> Einrichtung im Admin-Bereich ({loc})", s == 303 and loc == "/login/2fa/setup")
    ok("... Zwischenschritt ist keine Admin-Sitzung", ad.req("GET", "/users")[1] == "/login" or ad.req("GET", "/")[1] == "/login")
    s, loc, _ = ad.post("/login", {"username": "zf-adm2", "password": PW})
    s, _, html = ad.req("GET", "/login/2fa/setup")
    key = admin_key(html)
    ok("... Seite: QR-Code (SVG) + Schluessel + Hinweis auf Passkeys der Hauptseite", s == 200 and "data:image/svg+xml" in html and len(key) == 20
       and 'id="adm-mfa-need"' in html and f"localhost:{P}/security" in html)
    s2, _, html2 = ad.req("GET", "/login/2fa/setup")
    ok("... Neu laden behaelt das Geheimnis (gescannter Code bleibt gueltig)", admin_key(html2) == key)
    st = step_now()
    s, loc, html = ad.post("/login/2fa/setup", {"code": "000000" if totp(key, st) != "000000" else "111111"})
    ok("... falscher Code -> 401, Seite mit Meldung, gleiches Geheimnis", s == 401 and "Der Code stimmt nicht" in html and admin_key(html) == key)
    s, loc, html = ad.post("/login/2fa/setup", {"code": totp(key, st)})
    codes = re.findall(r"<li>([a-z2-9]{4}(?:-[a-z2-9]{4}){3})</li>", html)
    ok(f"... richtiger Code -> eingerichtet, 10 Wiederherstellungscodes, angemeldet ({len(codes)})", s == 200 and len(codes) == 10
       and 'id="adm-mfa-codes"' in html and ad.req("GET", "/users")[0] == 200)
    ok("... Faktor gilt auch auf der Hauptseite", client("zf-adm2", login_at=int(time.time())).api("GET", "/api/account/security")[1].get("totp") is True)
    ad3 = Client(A2, f"localhost:{PA}")
    s, loc, _ = ad3.post("/login", {"username": "zf-adm2", "password": PW})
    ok(f"... naechste Admin-Anmeldung: normaler zweiter Schritt ({loc})", s == 303 and loc == "/login/2fa")
    s, loc, _ = ad3.post("/login/2fa", {"code": next_code(key, {"last": st})[0]})
    ok("... mit Code angemeldet", s == 303 and loc == "/" and ad3.req("GET", "/users")[0] == 200)
    ok("Admins: QR-Kopplung fuer Nicht-Admin ohne Faktor geht weiter", pl.api("POST", "/api/settings/pair")[0] == 200)
    # Zwischenschritt laeuft ab (15 Minuten)
    old = Client(A2, f"localhost:{PA}")
    old.ck["hk_admin"] = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest()).sign(
        base64.b64encode(json.dumps({"amfs_u": "zf-adm2", "amfs_at": int(time.time()) - 1000}).encode())).decode()
    ok("Admins: Einrichtungs-Schritt aelter als 15 Minuten -> Anmeldung", old.req("GET", "/login/2fa/setup")[1] == "/login")

    # --- "alle": neue App-Anmeldungen verlangen den Faktor, bestehende bleiben ------------------------------------------
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('zf-plain','zf-plain','alt',?, 'hkd_plain', ?)",
                  (hashlib.sha256(b"hkd_zf-plain-geraet").hexdigest(), time.time()))
    auth.save_setting("mfa", {"require": "all", "oidc_mfa": False, "oidc_acr": ""}); settings_wait()
    ok("Alle: bestehende Sitzung ohne Faktor -> API geht weiter", pl.api("GET", "/api/notes")[0] == 200)
    ok("Alle: gekoppeltes Geraet ohne Faktor bleibt (/app/resume)",
       Client(S2, f"localhost:{P}").req("POST", "/app/resume", js={"device_token": "hkd_zf-plain-geraet"})[0] == 200)
    s, d = pl.api("POST", "/api/settings/pair")
    ok(f"Alle: QR-Kopplung erzeugen ohne Faktor -> 403 mit Hinweis ({str(d.get('error'))[:50]}…)", s == 403 and d.get("mfa_setup") is True
       and "/security" in d.get("url", ""))
    s, _, raw = Client(S2, f"localhost:{P}").req("POST", "/app/pair", js={"code": "zf-pair-vorher-0123456789abcdef", "device": "zf-Tablet"})
    ok("Alle: alter Kopplungs-Code (vor der Richtlinie) -> 403, kein Geraet", s == 403 and "hkd_" not in raw)
    hb = client("zf-plain")
    s, loc, _ = app_handoff_start(hb)
    ok(f"Alle: App-Anmeldung mit Browser-Sitzung ohne Faktor -> Einrichtung ({loc})", s == 303 and loc == "/security")
    s, _, html = hb.req("GET", "/security")
    ok("... Einrichtungsseite mit Weiter zur App-Uebergabe", s == 200 and 'data-need="1"' in html and "/app/handoff" in html)
    hc = client()
    s, loc, _ = app_handoff_start(hc)
    s, loc, _ = hc.post("/login", {"username": "zf-plain", "password": PW}, src="/login?next=/app/handoff")
    ok(f"Alle: frische App-Anmeldung ohne Faktor -> Einrichtung statt Code ({loc})", s == 303 and loc == "/security")
    s, loc, html = hc.req("GET", "/app/handoff")
    ok("... /app/handoff gibt ohne Faktor keinen Code heraus", s == 303 and loc == "/security" and "code=" not in html)
    s, d = hc.api("POST", "/api/account/totp/setup")
    psec = base64.b32decode(d["secret"].replace(" ", "")); pst = step_now()
    ok("... Faktor einrichten", hc.api("POST", "/api/account/totp/confirm", {"code": totp(psec, pst)})[0] == 200)
    s, loc, html = hc.req("GET", "/app/handoff")
    ok(f"... danach Code fuer die App ({s})", s == 200 and re.search(r"saga://auth\?code=", html))
    ok("... und QR-Kopplung erlaubt", hc.api("POST", "/api/settings/pair")[0] == 200)
    sso = client("zf-sso", login_at=int(time.time()))
    ok("Alle: SSO-Konto (Faktor beim Anbieter) -> Kopplung geht", sso.api("POST", "/api/settings/pair")[0] == 200)

    # --- Notfall per Kommandozeile -------------------------------------------------------------------------------------
    env = dict(os.environ); env.pop("PYTHONPATH", None); env["STATE_DIR"] = SD
    r = subprocess.run([sys.executable, "run.py", "2fa-zuruecksetzen", "zf-adm2"], cwd=APP, env=env, capture_output=True, text=True, timeout=60)
    ok(f"CLI 2fa-zuruecksetzen: Faktor weg ({r.stdout.strip()[:60]})", r.returncode == 0 and "zurückgesetzt" in r.stdout
       and not client("zf-adm2", login_at=int(time.time())).api("GET", "/api/account/security")[1].get("enabled"))
    with connect() as c:
        kv = c.execute("SELECT v FROM user_kv WHERE uid='zf-adm2' AND k='mfa_reset'").fetchone()
    ok("... Protokoll im Konto (cli)", kv and json.loads(kv[0])["by"] == "cli")
    ae = Client(A2, f"localhost:{PA}")
    s, loc, _ = ae.post("/login", {"username": "zf-adm2", "password": PW})
    ok(f"... Admin-Anmeldung danach: Einrichtung statt Aussperren ({loc})", s == 303 and loc == "/login/2fa/setup")
    r = subprocess.run([sys.executable, "run.py", "2fa-richtlinie", "none"], cwd=APP, env=env, capture_output=True, text=True, timeout=60)
    ok(f"CLI 2fa-richtlinie none ({r.stdout.strip()[:40]})", r.returncode == 0 and auth.settings(fresh=True)["mfa"]["require"] == "none")
    r = subprocess.run([sys.executable, "run.py", "2fa-richtlinie", "jeder"], cwd=APP, env=env, capture_output=True, text=True, timeout=60)
    ok("CLI 2fa-richtlinie mit Unsinn -> Fehler, nichts geaendert", r.returncode != 0 and auth.settings(fresh=True)["mfa"]["require"] == "none")
    settings_wait()
    af = Client(A2, f"localhost:{PA}")
    s, loc, _ = af.post("/login", {"username": "zf-adm2", "password": PW})
    ok(f"... Richtlinie aus: Admin wieder direkt hinein ({loc})", s == 303 and loc == "/")


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
    print(f"\nZWEIFAKTOR {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)
