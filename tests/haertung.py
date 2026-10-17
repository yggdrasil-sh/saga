"""Haertung nach dem Team-Review: Groessengrenze (413), scrypt-Begrenzung, Teil-Commit/Revision beim Speichern,
Sicherheitsnetz fuer Versionen, CalDAV-Passwort bei geaenderter Adresse, Pinning gegen DNS-Rebinding,
Passwortwechsel meldet Apps ab, OIDC-sub, getrennte Fehlversuch-Zaehler; QA-Nachbesserung (SSO-Verknuepfung loesen,
App-Anmeldung ohne Rueckfrage nach frischer Anmeldung, Abbrechen zurueck in die App, Zwischendateien). Wegwerf-Konten hd-*, raeumt auf.
Umgebung wie die anderen Tests (HK_TEST_BASE, HK_TEST_ADMIN, BASE_URL, SESSION_SECRET, Postgres ueber db.py)."""
import re
import base64, http.client, json, os, select, socket, sys, threading, time, urllib.parse
import httpx
from itsdangerous import TimestampSigner
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW, PW2 = "Haertung-Pass-123", "Haertung-Neu-456"
RAD, RADPW = "http://127.0.0.1:5232/", os.environ.get("HK_RAD_PW", "geheim-app-pw-123")
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


def ck(u, **extra):
    return S.sign(base64.b64encode(json.dumps(dict({"uid": u, "name": u, "pwc": time.time() + 5}, **extra)).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, base=B):
    c = http.client.HTTPConnection(base.hostname, base.port, timeout=30)
    h = {"Host": HOST if base is B else base.netloc, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; h["X-Saga"] = "1"
        body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=body, headers=h)
    r = c.getresponse(); raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw.decode("utf-8", "replace")


def declared(path, n, base=B):
    """Nur Kopfzeilen mit grosser Content-Length schicken - der Server muss sofort ablehnen."""
    c = http.client.HTTPConnection(base.hostname, base.port, timeout=15)
    c.putrequest("POST", path, skip_host=True)
    c.putheader("Host", HOST if base is B else base.netloc); c.putheader("Content-Type", "application/json")
    c.putheader("Content-Length", str(n)); c.endheaders()
    return c.getresponse().status


def chunked(path, total, ctype="application/x-www-form-urlencoded", method="POST", cookie=None, prefix=b""):
    """Ohne Content-Length (chunked) viel schicken; nach jedem Stueck nachsehen, ob schon eine Antwort da ist."""
    s = socket.create_connection((B.hostname, B.port), timeout=15)
    extra = f"Cookie: saga={cookie}\r\nX-Saga: 1\r\n" if cookie else ""
    s.sendall((f"{method} {path} HTTP/1.1\r\nHost: {HOST}\r\nContent-Type: {ctype}\r\n{extra}Transfer-Encoding: chunked\r\n\r\n").encode())
    piece, sent, data = b"a" * 65536, 0, b""
    try:
        if prefix:
            s.sendall(b"%x\r\n" % len(prefix) + prefix + b"\r\n")
        while sent < total:
            s.sendall(b"%x\r\n" % len(piece) + piece + b"\r\n"); sent += len(piece)
            if select.select([s], [], [], 0.02)[0]:
                break
        else:
            s.sendall(b"0\r\n\r\n")
    except OSError:
        pass
    try:
        while b"\r\n" not in data:
            d = s.recv(4096)
            if not d:
                break
            data += d
    except OSError:
        pass
    s.close()
    line = data.split(b"\r\n", 1)[0].split()
    return int(line[1]) if len(line) > 1 and line[1].isdigit() else 0, sent


def cleanup():
    with connect() as c, c:
        for t in ("task", "note", "note_version", "device_login", "app_token", "user_kv", "cal_account", "file", "pair_code", "bookmark"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'hd-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'hd-%'")
        c.execute("DELETE FROM login_fail WHERE key LIKE '%hd-%'")


def raw(m, p, cookies=None):
    """GET mit mehreren Cookies -> (Status, Location, Set-Cookie-Liste, Text)"""
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    c.request(m, p, headers=h)
    r = c.getresponse(); body = r.read().decode("utf-8", "replace")
    return r.status, r.getheader("location") or "", r.msg.get_all("set-cookie") or [], body


def qa_nachbesserung():
    """Nachbesserung nach der QA: N2 kurze scrypt-Wartezeit, N3 SSO-Verknuepfung loesen (Admin),
    N4 App-Anmeldung ohne Rueckfrage direkt nach frischer Anmeldung + Abbrechen zurueck in die App, N8 Zwischendateien."""
    import base64 as b64, hashlib, secrets
    from standalone import Client
    from saga import files

    # --- N2 ------------------------------------------------------------------------------------------------
    ok(f"scrypt: Wartezeit auf einen freien Platz kurz ({auth.KDF_WAIT} s)", 0 < auth.KDF_WAIT <= 2)

    # --- N3: SSO-Verknuepfung loesen -------------------------------------------------------------------------
    auth.create_user("hd-adm", PW, "Admin", is_admin=True)
    auth.create_user("hd-sso2", None, "SSO 2", source="oidc")
    auth._ext_user("hd-sso2", "oidc", "sub-alt", "", "", [], {})
    adm = Client(AD)
    ok("Admin-Anmeldung", adm.post("/login", {"username": "hd-adm", "password": PW})[0] == 303)
    sid = auth.get_user("hd-sso2")["id"]
    s, _, html = adm.req("GET", f"/users/{sid}")
    ok("Benutzerseite zeigt Knopf mit Rueckfrage", s == 200 and 'value="unlink"' in html and "SSO-Verknüpfung lösen" in html and "data-confirm" in html)
    lid = auth.get_user("hd-user")["id"]
    ok("Lokales Konto: kein Knopf", 'value="unlink"' not in adm.req("GET", f"/users/{lid}")[2])
    ok("Neuer Anbieter (andere sub) -> vorher abgewiesen", auth._ext_user("hd-sso2", "oidc", "sub-neu", "", "", [], {})[0] is None)
    s, loc, _ = adm.post(f"/users/{sid}", {"action": "unlink"})
    ok(f"Verknuepfung loesen -> {s}, ext_id leer", s == 303 and not auth.get_user("hd-sso2")["ext_id"])
    u, _ = auth._ext_user("hd-sso2", "oidc", "sub-neu", "", "", [], {})
    ok("Danach: naechste SSO-Anmeldung verknuepft neu (gleiches Konto)", u and u["id"] == sid and u["ext_id"] == "sub-neu")
    s, _, _ = adm.post(f"/users/{lid}", {"action": "unlink"})
    ok("Lokales Konto loesen -> abgelehnt, nichts geaendert", s == 303 and auth.get_user("hd-user")["source"] == "local")

    # --- N4: App-Anmeldung ---------------------------------------------------------------------------------
    def chal():
        v = secrets.token_urlsafe(48)
        return v, b64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    now = int(time.time())
    v, ch = chal()
    w = Client(B, HOST)
    s, loc, _ = w.req("GET", f"/app/login?scheme=saga&challenge={ch}")
    ok(f"Ohne Sitzung: /app/login -> Anmeldung ({loc}), Ablauf-Beginn gemerkt", s == 303 and loc.startswith("/login") and "hk_app_t" in w.ck)
    s, loc, _ = w.post("/login", {"username": "hd-user", "password": PW}, src="/login?next=/app/handoff")
    ok(f"Anmelden im Formular -> {loc}", s == 303 and loc == "/app/handoff")
    s, loc, html = w.req("GET", "/app/handoff")
    m = re.search(r'content="0;url=(saga://auth\?code=[A-Za-z0-9_-]+)"', html or "")
    ok(f"Frisch angemeldet -> ohne Rueckfrage, Seite mit Sprung in die App (CSP form-action: keine Weiterleitung)",
       s == 200 and not loc and m and 'href="' + m.group(1) + '"' in html)
    code = m.group(1).split("code=", 1)[1] if m else ""
    s, d = req("POST", "/app/exchange", None, {"code": code, "verifier": v})
    ok("Code einloesbar", s == 200)
    v, ch = chal()
    w.req("GET", f"/app/login?scheme=saga&challenge={ch}")
    s, loc, html = w.req("GET", "/app/handoff")
    ok("Zweite App-Anmeldung in derselben Sitzung -> wieder Rueckfrage", s == 200 and "/app/handoff?go=" in html and not loc)
    s, loc, _ = w.req("GET", "/app/handoff?cancel=1")
    ok(f"Abbrechen -> zurueck in die App ({loc})", s == 303 and loc == "saga://auth?error=cancelled" and "hk_app_ch" not in w.ck)
    ok("Abbrechen ohne laufende App-Anmeldung -> Dashboard", w.req("GET", "/app/handoff?cancel=1")[:2] == (303, "/"))
    _, ch = chal()
    base = {"hk_app_ch": ch, "hk_app_sc": "saga"}
    for name, sess, t0 in (("Anmeldung aelter als 2 Minuten", now - 300, now - 400),
                           ("Anmeldung vor Beginn des Ablaufs", now - 30, now),
                           ("Ablauf mit bestehender Sitzung (kein Beginn gemerkt)", now, None)):
        cks = dict(base, saga=ck("hd-user", login_at=sess))
        if t0:
            cks["hk_app_t"] = str(t0)
        s, loc, _, body = raw("GET", "/app/handoff", cks)
        ok(f"{name} -> Rueckfrage", s == 200 and not loc and "/app/handoff?go=" in body)
    s, loc, _, body = raw("GET", "/app/handoff", dict(base, hk_app_t=str(now - 5), saga=ck("hd-user", login_at=now - 3)))
    ok("Frische Anmeldung (Sitzungs-Cookie) -> direkt (Sprung-Seite)", s == 200 and "url=saga://auth?code=" in body)
    with connect() as c, c:
        c.execute("DELETE FROM app_code WHERE uid='hd-user'")

    # --- N8: Zwischendateien --------------------------------------------------------------------------------
    os.makedirs(files.UPLOAD_TMP, exist_ok=True)
    old, new = os.path.join(files.UPLOAD_TMP, "hd-alt.tmp"), os.path.join(files.UPLOAD_TMP, "hd-neu.tmp")
    for p in (old, new):
        open(p, "w").write("x")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    n = files.tmp_cleanup()
    ok(f"Aufraeumen: alte Zwischendatei weg, frische bleibt ({n})", not os.path.exists(old) and os.path.exists(new))
    os.remove(new)
    A = ck("hd-user")
    fid = os.urandom(12).hex()
    os.makedirs(files.FILES_DIR, exist_ok=True)
    with open(os.path.join(files.FILES_DIR, fid), "wb") as f:
        f.write(os.urandom(6 * 1024 * 1024))
    with connect() as c, c:
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fid, "hd-user", "gross.bin", "application/octet-stream", 6 * 1024 * 1024, 0, time.time()))
    s, d = req("POST", "/api/notes", A, {"title": "Export-Abbruch"})
    pid = d["id"]
    req("PATCH", f"/api/notes/{pid}", A, {"body": json.dumps({"blocks": [{"type": "attaches", "data": {"file": {"url": f"/files/{fid}", "name": "gross.bin"}}}]})})
    zips = lambda: [x for x in os.listdir(files.UPLOAD_TMP) if x.startswith("export-")]
    before = set(zips())
    sk = socket.create_connection((B.hostname, B.port), timeout=30)
    sk.sendall((f"GET /api/notes/{pid}/export?sub=1 HTTP/1.1\r\nHost: {HOST}\r\nCookie: saga={A}\r\nX-Saga: 1\r\n\r\n").encode())
    first = sk.recv(65536)
    sk.close()                                          # Download abgebrochen
    left = set()
    for _ in range(20):
        time.sleep(0.5)
        left = set(zips()) - before
        if not left:
            break
    ok(f"ZIP-Export abgebrochen -> Zwischendatei geloescht ({first[:12]!r}, uebrig {len(left)})", first.startswith(b"HTTP/1.1 200") and not left)
    with connect() as c, c:
        c.execute("DELETE FROM file WHERE id=?", (fid,))
    try:
        os.remove(os.path.join(files.FILES_DIR, fid))
    except OSError:
        pass


def main():
    cleanup()
    auth.create_user("hd-user", PW, "Haertung")
    A = ck("hd-user")

    # --- H1: Groesse von Anfragen ----------------------------------------------------------------------
    ok("Ohne Anmeldung: Content-Length 50 MB an /app/pair -> 413", declared("/app/pair", 50 * 1024 * 1024) == 413)
    ok("Content-Length 3 MB an PATCH-faehigen Seiten-Pfad -> 413", declared("/api/notes/1", 3 * 1024 * 1024) == 413)
    st, sent = chunked("/login", 8 * 1024 * 1024)
    ok(f"Ohne Content-Length (chunked) an /login -> abgelehnt ({st}) nach {sent // 1024} KB", st in (400, 413) and sent <= 3 * 1024 * 1024)
    st, sent = chunked("/app/pair", 8 * 1024 * 1024, "application/json")
    ok(f"chunked an /app/pair -> 413 nach {sent // 1024} KB", st == 413 and sent <= 3 * 1024 * 1024)
    ok("Admin-Port: Content-Length 2 MB -> 413", declared("/login", 2 * 1024 * 1024, AD) == 413)
    # Bughunt 2 (server, S3): Endpunkte, die request.body() selbst lesen, bekamen BodyTooLarge als ExceptionGroup -> 500
    st, sent = chunked("/api/account/avatar", 12 * 1024 * 1024, "image/png", "PUT", A)
    ok(f"chunked Profilbild ueber der Grenze -> 413 statt 500 ({st}, {sent // 1024} KB)", st == 413)
    st, sent = chunked("/api/account/avatar", 12 * 1024 * 1024, "multipart/form-data; boundary=x", "PUT", A,
                       b'--x\r\nContent-Disposition: form-data; name="image"; filename="a.png"\r\nContent-Type: image/png\r\n\r\n')
    ok(f"chunked Profilbild (multipart) ueber der Grenze -> 413 ({st})", st == 413)
    ok("Server laeuft weiter", req("GET", "/healthz")[0] == 200)
    s, d = req("POST", "/api/notes", A, {"title": "Gross, aber erlaubt", "kind": "page"})
    nid = d.get("id") if isinstance(d, dict) else None
    big = json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "x" * 150_000}}]})
    ok("Seite mit 150 000 Zeichen speichern geht weiter (unter der Grenze)", req("PATCH", f"/api/notes/{nid}", A, {"body": big})[0] == 200)

    # --- M5/N1/M6: Speichern ---------------------------------------------------------------------------
    s, d = req("POST", "/api/notes", A, {"title": "DB", "kind": "database"})
    dbid = d["id"]
    sch = req("GET", f"/api/notes/{dbid}", A)[1]["schema"]
    neu = dict(sch, props=sch["props"] + [{"id": "hdx", "name": "Neu", "type": "text"}])
    s, _ = req("PATCH", f"/api/notes/{dbid}", A, {"schema": neu, "icon": "<b>"})
    after = req("GET", f"/api/notes/{dbid}", A)[1]["schema"]
    ok(f"Schema + ungueltiges Symbol -> {s}, Schema NICHT halb gespeichert", s == 400 and all(p["id"] != "hdx" for p in after["props"]))
    s, _ = req("PATCH", f"/api/notes/{dbid}", A, {"schema": neu})
    ok("Schema allein -> gespeichert", s == 200 and any(p["id"] == "hdx" for p in req("GET", f"/api/notes/{dbid}", A)[1]["schema"]["props"]))

    s, d = req("POST", "/api/notes", A, {"title": "Rev"})
    pid = d["id"]
    long_doc = json.dumps({"blocks": [{"type": "paragraph", "data": {"text": "Wichtiger Text " * 80}}]})
    s1, d1 = req("PATCH", f"/api/notes/{pid}", A, {"body": long_doc, "rev": 0})
    s2, d2 = req("PATCH", f"/api/notes/{pid}", A, {"body": long_doc, "rev": 0})
    ok(f"Revision: erster Stand 200 (rev {d1.get('rev')}), zweiter mit alter rev -> 409", s1 == 200 and s2 == 409 and d2.get("rev") == 1)
    req("PATCH", f"/api/notes/{pid}", A, {"body": long_doc.replace("Wichtiger", "Wichtiger!"), "rev": 1})
    with connect() as c:
        n0 = c.execute("SELECT COUNT(*) FROM note_version WHERE uid='hd-user' AND note_id=?", (pid,)).fetchone()[0]
    s3, _ = req("PATCH", f"/api/notes/{pid}", A, {"body": json.dumps({"blocks": []}), "rev": 2})
    with connect() as c:
        rows = c.execute("SELECT body FROM note_version WHERE uid='hd-user' AND note_id=? ORDER BY created DESC", (pid,)).fetchall()
    ok(f"Seite geleert (kurz nach der letzten Sicherung) -> Version erzwungen ({n0} -> {len(rows)})",
       s3 == 200 and len(rows) == n0 + 1 and "Wichtiger!" in rows[0][0])

    # --- M4: scrypt begrenzt ---------------------------------------------------------------------------
    slots, wait = auth._KDF_SLOTS, auth.KDF_WAIT
    auth._KDF_SLOTS, auth.KDF_WAIT = threading.BoundedSemaphore(1), 0.2
    auth._KDF_SLOTS.acquire()
    try:
        u, err = auth.authenticate("hd-user", "irgendwas-falsch", "10.77.0.1")
        with connect() as c:
            nf = c.execute("SELECT COUNT(*) FROM login_fail WHERE key LIKE '%hd-user%'").fetchone()[0]
        ok(f"Alle Rechen-Plaetze belegt -> sofort Hinweis statt Warten, kein Fehlversuch ({err})", u is None and err and nf == 0)
    finally:
        auth._KDF_SLOTS.release()
        auth._KDF_SLOTS, auth.KDF_WAIT = slots, wait
    ok("Danach wieder normal", auth.authenticate("hd-user", PW, "10.77.0.1")[0] is not None)

    # --- M11: Fehlversuch-Zaehler -----------------------------------------------------------------------
    for _ in range(8):
        auth.note_fail("hd-lock", "10.77.0.2")
    ok("8 Fehlversuche von einer Adresse -> dort gesperrt", auth.too_many("hd-lock", "10.77.0.2"))
    ok("... von einer anderen Adresse nicht (kein Aussperren durch Fremde)", not auth.too_many("hd-lock", "10.77.0.3"))
    ok("... und der Admin-Port zaehlt getrennt", not auth.too_many("hd-lock", "10.77.0.2", "admin"))
    auth.clear_fails("hd-lock")
    ok("Erfolgreiche Anmeldung leert die Benutzer-Zaehler", not auth.too_many("hd-lock", "10.77.0.2"))

    # --- M9: Passwortwechsel meldet Apps ab ---------------------------------------------------------------
    with connect() as c, c:
        d1 = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('hd-user','hd-user','Dieses',?, 'hkd_', 0)", ("h1" * 32,)).lastrowid
        d2 = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('hd-user','hd-user','Anderes',?, 'hkd_', 0)", ("h2" * 32,)).lastrowid
        c.execute("INSERT INTO app_token (uid, name, hash, prefix, created, device_id) VALUES ('hd-user','zu Dieses',?, 'hk_', 0, ?)", ("t1" * 32, d1))
        c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES ('hd-user','von Hand',?, 'hk_', 0)", ("t2" * 32,))
    s, d = req("POST", "/api/account/password", ck("hd-user", did=d1), {"old": PW, "new": PW2})
    with connect() as c:
        devs = [r[0] for r in c.execute("SELECT id FROM device_login WHERE uid='hd-user'").fetchall()]
        toks = [r[0] for r in c.execute("SELECT name FROM app_token WHERE uid='hd-user'").fetchall()]
    ok(f"Passwortwechsel: andere Geraete + Schluessel abgemeldet, eigenes Geraet bleibt ({d})",
       s == 200 and devs == [d1] and toks == ["zu Dieses"] and d.get("devices_revoked") == 2)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('hd-user','hd-user','Noch eins',?, 'hkd_', 0)", ("h3" * 32,))
    s, d = req("POST", "/api/account/password", ck("hd-user"), {"old": PW2, "new": PW, "logout_devices": False})
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM device_login WHERE uid='hd-user'").fetchone()[0]
    ok("Mit logout_devices=false bleiben die Apps angemeldet", s == 200 and n == 2 and d.get("devices_revoked") == 0)

    # --- M10: OIDC-Kennung (sub) ------------------------------------------------------------------------
    auth.create_user("hd-sso", None, "SSO", source="oidc")
    u1, _ = auth._ext_user("hd-sso", "oidc", "sub-eins", "", "", [], {})
    u2, e2 = auth._ext_user("hd-sso", "oidc", "sub-zwei", "", "", [], {})
    u3, _ = auth._ext_user("hd-sso", "oidc", "sub-eins", "", "", [], {})
    ok("OIDC: erste Anmeldung verknuepft sub, andere sub mit gleichem Namen -> abgewiesen", u1 and u1["ext_id"] == "sub-eins" and u2 is None and e2 and u3)
    orig = auth.settings
    auth.settings = lambda fresh=False: dict(orig(), oidc=dict(orig()["oidc"], username_claim="email"))
    try:
        u, e = auth.oidc_user({"sub": "s-x", "email": "hd-mail@example.org", "email_verified": False})
    finally:
        auth.settings = orig
    ok("OIDC: Benutzername aus nicht bestaetigter E-Mail -> abgewiesen", u is None and e and not auth.get_user("hd-mail"))

    # --- M2: CalDAV-Passwort bei geaenderter Adresse ------------------------------------------------------
    X = ck("hk-test-b")
    httpx.request("MKCALENDAR", f"{RAD}test/hd/", auth=("test", RADPW), content='<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" '
                  'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>HD</d:displayname></d:prop></d:set></c:mkcalendar>')
    s, d = req("POST", "/api/settings/calendars", X, {"base_url": RAD, "username": "test", "password": RADPW, "label": "hd"})
    aid = d.get("id") if isinstance(d, dict) else None
    ok(f"Kalender-Zugang angelegt ({s} {d if s != 200 else ''})", s == 200 and aid)
    s, d = req("PATCH", f"/api/settings/calendars/{aid}", X, {"base_url": "http://127.0.0.1:18283/", "username": "test"})
    ok(f"Andere Adresse ohne Passwort -> abgelehnt ({s})", s == 400 and "neu eingeben" in str(d))
    s, d = req("PATCH", f"/api/settings/calendars/{aid}", X, {"base_url": RAD, "username": "jemand"})
    ok(f"Anderer Benutzer ohne Passwort -> abgelehnt ({s})", s == 400 and "neu eingeben" in str(d))
    s, d = req("POST", "/api/settings/calendars/test", X, {"id": aid, "base_url": "http://127.0.0.1:18283/"})
    ok(f"Verbindungstest an andere Adresse mit gespeichertem Passwort -> abgelehnt ({s})", s == 400 and "neu eingeben" in str(d))
    s, _ = req("PATCH", f"/api/settings/calendars/{aid}", X, {"base_url": RAD, "username": "test", "label": "hd2"})
    ok(f"Gleiche Adresse ohne Passwort -> weiter moeglich ({s})", s == 200)
    req("DELETE", f"/api/settings/calendars/{aid}", X, hdr={"X-Saga": "1"})

    # --- M3: Verbindung nur mit gepruefter IP ------------------------------------------------------------
    from saga import netguard
    import hkcal
    url = f"http://{B.hostname}:{B.port}/healthz"
    try:
        netguard.guarded_client(lambda ip, h, p: False, timeout=5).get(url)
        blocked = False
    except httpx.ConnectError as ex:
        blocked = isinstance(ex.__cause__, netguard.Blocked)
    ok("Pinning: nicht erlaubte IP -> Verbindung wird gar nicht aufgebaut", blocked)
    ok("Pinning: erlaubte IP -> normal", netguard.guarded_client(lambda ip, h, p: True, timeout=5).get(url).status_code == 200)
    ok("hkcal: interne IP ohne Freigabe nicht erlaubt, oeffentliche schon",
       not hkcal._ip_ok("10.0.0.5", "intern.example", 443) and hkcal._ip_ok("1.1.1.1", "cal.example", 443))

    # --- Sprache: Konto-Sprache fuer gespeicherte Standardnamen, Kalenderfehler in der Sprache der Anfrage ------------
    import hashlib
    from saga import i18n
    from saga.calendars import _cal_fernet
    auth.create_user("hd-es", PW, "Es")
    tok = "hk_" + "e" * 40
    with connect() as c, c:
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES ('hd-es', 'lang', 'es')")
        c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES ('hd-es','Teilen',?, 'hk_', 0)",
                  (hashlib.sha256(tok.encode()).hexdigest(),))
        c.execute("INSERT INTO cal_account (uid, label, base_url, username, secret, calendars, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                  ("hd-es", "x", RAD, "test", _cal_fernet().encrypt(b"falsches-passwort").decode(),
                   json.dumps([{"href": RAD + "test/hd/", "name": "HD", "color": "#ff0000", "show": True, "writable": True}]), 0, 0))
    s, d = req("POST", "/api/app/capture", None, {"title": "Geteilt", "text": "x"}, hdr={"Authorization": "Bearer " + tok, "Accept-Language": "de"})
    with connect() as c:
        inbox = c.execute("SELECT title FROM note WHERE uid='hd-es' AND parent_id IS NULL").fetchone()
    ok(f"Teilen aus der App (Anfrage deutsch, Konto spanisch) -> Eingang heisst {inbox[0] if inbox else '-'}",
       s == 200 and inbox and inbox[0] == i18n.t_lang("es", "Eingang") and inbox[0] != "Eingang")
    s, d = req("GET", "/api/calendar/events?start=2026-10-01&end=2026-10-31", ck("hd-es"))
    err = (d.get("errors") or [{}])[0].get("error", "") if isinstance(d, dict) else ""
    ok(f"Kalenderfehler in der Sprache des Kontos ({err})", s == 200 and err == i18n.t_lang("es", "Anmeldung abgelehnt – Benutzer oder App-Passwort falsch"))
    s, d = req("POST", "/api/bookmarks", ck("hd-es"), {"name": "Bsp", "url": "https://example.org"})
    s, d = req("GET", "/api/bookmarks", ck("hd-es"))
    ok(f"Standard-Kategorie in der Anzeigesprache ({d[0]['category'] if isinstance(d, list) and d else '-'})",
       isinstance(d, list) and d and d[0]["category"] == i18n.t_lang("es", "Lesezeichen"))

    # --- Kleinigkeiten ------------------------------------------------------------------------------------
    from saga.userdata import USER_TABLES
    ok("pair_code wird beim Loeschen eines Kontos mit entfernt", "pair_code" in USER_TABLES)

    qa_nachbesserung()
    cleanup()
    print(f"\nHAERTUNG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
