"""Mitteilungen aufs Handy/Desktop (1.7): Web Push, ntfy, UnifiedPush, APNs - mit Fake-Empfaengern im Test.

Fake-Empfaenger (in diesem Prozess): 127.0.0.1:18290 nimmt Web Push (/wp/...), UnifiedPush (/up/...) und ntfy (JSON an
"/") an, 127.0.0.1:18291 spielt APNs (/3/device/<token>; HTTP/1.1 - httpx nutzt HTTP/2 nur mit TLS). Web-Push-Koerper
werden hier wie im Browser entschluesselt (RFC 8291), VAPID- und APNs-Token gegen die oeffentlichen Schluessel geprueft.
Geprueft: Testvektor RFC 8291, VAPID, Anmelden/Erneuern/Wechsel des Kontos, Pruefung der Adressen (SSRF, http nur
freigegeben), Zustellung bei Teilen/Kommentar, Text ohne Seitentitel/Inhalte, ntfy nie mit Namen, "ohne Namen",
Bereiche, Ruhezeiten, 410 -> Abo weg, 503 -> Wiederholung, harte Fehler -> Abo weg, Testmitteilung + Grenze,
Isolation, Grenzen, App-Abos (Geraete-Schluessel, enden mit der Geraete-Anmeldung), Chat-Schnittstelle (ohne Inhalt),
Abmelden im Browser, "ueberall abmelden", Zugriff beim Zustellen, Admin-Seite (Wege, APNs-Schluessel), APNs-Zustellung,
Kontoloeschung, keine Texte im Server-Log. Wegwerf-Konten ps-*; raeumt auf."""
import base64, hashlib, http.client, http.server, json, os, re, secrets, struct, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import auth
from db import connect
from saga import bus, push
from saga.core import _tok_hash, set_user_lang

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD_ = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
SA = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
PW = "Push-Pass-1234"
WAIT = 5.6
FP, FA = 18290, 18291
FAKE = f"http://127.0.0.1:{FP}"
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u, **extra):
    return S.sign(base64.b64encode(json.dumps(dict({"uid": u, "name": u, "pwc": time.time() + 5}, **extra)).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_resp=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    raw = None
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return (r.status, data, r) if raw_resp else (r.status, data)


def admin(path, form=None, aid="ps-adm", files=None):
    tok = "ps-csrf-" + secrets.token_hex(6)
    cookie = SA.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": tok}).encode())).decode()
    c = http.client.HTTPConnection(AD_.hostname, AD_.port, timeout=30)
    h = {"Cookie": "hk_admin=" + cookie, "Accept-Language": "de"}
    body = None
    if form is not None:
        bnd = "----ps" + secrets.token_hex(8)
        parts = []
        for k, v in dict(form, csrf=tok).items():
            parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fn, data) in (files or {}).items():
            parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
        body = b"".join(parts) + f"--{bnd}--\r\n".encode()
        h["Content-Type"] = "multipart/form-data; boundary=" + bnd
    c.request("POST" if form is not None else "GET", path, body=body, headers=h)
    r = c.getresponse(); data = r.read().decode("utf-8", "replace"); loc = r.getheader("location"); c.close()
    if r.status == 303 and loc and form is not None:                  # Meldung (flash) steht auf der Folgeseite
        c = http.client.HTTPConnection(AD_.hostname, AD_.port, timeout=30)
        cookie2 = r.getheader("set-cookie", "").split(";")[0] or "hk_admin=" + cookie
        c.request("GET", loc, headers={"Cookie": cookie2, "Accept-Language": "de"})
        r2 = c.getresponse(); data = r2.read().decode("utf-8", "replace"); c.close()
    return r.status, data


# =====================================================================================================
# Empfaenger-Seite von Web Push (wie im Browser)
# =====================================================================================================
P256 = ec.SECP256R1()


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class UA:
    """Ein "Browser": Schluesselpaar + auth-Geheimnis."""

    def __init__(self):
        self.key = ec.generate_private_key(P256)
        self.pub = self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)

    def keys(self):
        return {"p256dh": b64u(self.pub), "auth": b64u(self.auth)}

    def decrypt(self, body):
        salt, rs, n = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
        as_pub, ct = body[21:21 + n], body[21 + n:]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(P256, as_pub))
        ikm = HKDF(hashes.SHA256(), 32, salt=self.auth, info=b"WebPush: info\x00" + self.pub + as_pub).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
        pt = AESGCM(cek).decrypt(nonce, ct, None).rstrip(b"\x00")
        assert pt.endswith(b"\x02") and rs == 4096
        return json.loads(pt[:-1])


def jwt_ok(tok, pub_raw):
    h, c, s = tok.split(".")
    sig = b64d(s)
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(P256, pub_raw).verify(der, (h + "." + c).encode(), ec.ECDSA(hashes.SHA256()))
    except Exception:
        return None
    return json.loads(b64d(h)), json.loads(b64d(c))


# =====================================================================================================
# Fake-Empfaenger
# =====================================================================================================
GOT = []                         # (port, pfad, kopfzeilen, koerper)
STATUS = {}                      # pfad -> [status, ...] (der Reihe nach; letzter bleibt)
LOCK = threading.Lock()


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        with LOCK:
            GOT.append((self.server.server_port, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
            lst = STATUS.get(self.path) or [200 if self.server.server_port == FA else 201]
            code = lst.pop(0) if len(lst) > 1 else lst[0]
            STATUS[self.path] = lst
        out = b""
        if self.server.server_port == FA and code != 200:
            out = json.dumps({"reason": "Unregistered" if code == 410 else "BadTopic"}).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve(port):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", port), Fake)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def mark():
    with LOCK:
        return len(GOT)


def wait_got(pred, since, timeout=9.0):
    end = time.time() + timeout
    while time.time() < end:
        with LOCK:
            hits = [g for g in GOT[since:] if pred(g)]
        if hits:
            return hits[0]
        time.sleep(0.1)
    return None


def none_got(pred, since, secs=6.5):
    time.sleep(secs)
    with LOCK:
        return not [g for g in GOT[since:] if pred(g)]


def path_is(p):
    return lambda g: g[1] == p


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ps-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification", "device_login", "push_sub", "push_queue"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'ps-%'")
        c.execute("DELETE FROM notification WHERE actor_uid LIKE 'ps-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'ps-%' OR target LIKE 'ps-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'ps-%' OR uid LIKE 'ps-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ps-%'")


def sub_rows(uid):
    with connect() as c:
        return c.execute("SELECT id, kind, endpoint, device_id, fails, last_err FROM push_sub WHERE uid=? ORDER BY id", (uid,)).fetchall()


def vector_test():
    as_key = ec.derive_private_key(int.from_bytes(b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), P256)
    out = push.encrypt(b"When I grow up, I want to be a watermelon",
                       "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                       "BTBZMqHH6r4Tts7J_aSIgg", salt=b64d("DGv6ra1nlYgDCS1FRnbzlw"), as_key=as_key)
    want = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
            "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
    ok("RFC 8291 Anhang A: Testvektor stimmt", b64u(out) == want)
    ua = UA()
    body = push.encrypt(json.dumps({"x": "äöü ✓"}).encode(), b64u(ua.pub), b64u(ua.auth))
    ok("Verschluesseln -> Entschluesseln (Empfaenger-Seite, zufaelliges Salz/Schluessel)", ua.decrypt(body) == {"x": "äöü ✓"})
    b2 = push.encrypt(b"{}", b64u(ua.pub), b64u(ua.auth))
    ok("jedes Mal neues Salz und neuer Absender-Schluessel", body[:16] != b2[:16] and body[21:86] != b2[21:86])
    try:
        push.encrypt(b"{}", "AAAA", b64u(ua.auth)); bad = False
    except ValueError:
        bad = True
    ok("kaputter Empfaenger-Schluessel -> ValueError", bad)
    hdr = push.vapid_header("https://push.example.org/abc/def")
    m = re.fullmatch(r"vapid t=([\w-]+\.[\w-]+\.[\w-]+), k=([\w-]+)", hdr)
    j = m and jwt_ok(m.group(1), b64d(m.group(2)))
    ok(f"VAPID: Signatur gueltig, aud = Ursprung, exp <= 24 h, sub gesetzt ({j and j[1]})",
       j and j[0]["alg"] == "ES256" and j[1]["aud"] == "https://push.example.org" and time.time() < j[1]["exp"] <= time.time() + 86400 and j[1]["sub"])
    return m.group(2) if m else None


def main():
    cleanup()
    serve(FP); serve(FA)
    vpub = vector_test()
    auth.create_user("ps-a", PW, "Anna Push")
    auth.create_user("ps-b", PW, "Bert Push")
    auth.create_user("ps-adm", PW, "Admin Push", is_admin=True)
    set_user_lang("ps-a", "de"); set_user_lang("ps-b", "en")
    prev_hosts = auth.settings(fresh=True)["allowed_hosts"]
    prev_push, prev_apns = auth.settings()["push"], auth.settings()["apns"]
    auth.save_setting("allowed_hosts", ", ".join(x for x in (prev_hosts, f"127.0.0.1:{FP}") if x))
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True})
    time.sleep(WAIT)
    A, Bk = ck("ps-a"), ck("ps-b")
    try:
        run(A, Bk, vpub)
    finally:
        auth.save_setting("allowed_hosts", prev_hosts)
        auth.save_setting("push", prev_push)
        with connect() as c, c:
            c.execute("UPDATE setting SET v=? WHERE k='apns'", (json.dumps(prev_apns),))
        cleanup()
    print(f"\nPUSH {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(A, Bk, vpub):
    # --- Service Worker, CSP, Grundzustand ---------------------------------------------------------------
    s, body, r = req("GET", "/sw.js", raw_resp=True)
    # 2.4 (Videos im Chat): fetch-Handler nur fuer /_m/ (alles andere geht unveraendert ans Netz, Static Routing)
    ok(f"/sw.js: 200, JavaScript, Service-Worker-Allowed /, fetch-Handler nur fuer /_m/ ({s})",
       s == 200 and "javascript" in r.getheader("content-type", "") and r.getheader("service-worker-allowed") == "/"
       and "showNotification" in body and body.count("'fetch'") == 1 and "u.pathname.indexOf('/_m/') !== 0" in body
       and "source: 'network'" in body and "__SAGA_V__" not in body)
    s, _, r = req("GET", "/", A, raw_resp=True)
    ok("CSP der Seiten erlaubt Worker nur von 'self'", "worker-src 'self'" in (r.getheader("content-security-policy") or ""))
    ok("GET /api/push ohne Anmeldung -> 401", req("GET", "/api/push")[0] == 401)
    s, st = req("GET", "/api/push", A)
    ok(f"GET /api/push: Server bietet Web Push (VAPID wie in der Datenbank), ntfy, UnifiedPush; APNs aus ({s})",
       s == 200 and st["server"]["webpush"] and st["server"]["vapid_public"] == vpub and st["server"]["ntfy"]
       and st["server"]["unifiedpush"] and not st["server"]["apns"] and st["devices"] == [])
    ok("Standard: alle Bereiche an, Namen an, keine Ruhezeit",
       all(st["settings"]["cats"].values()) and set(st["categories"]) == {"share", "comment", "chat", "publish", "call"}
       and st["settings"]["names"] and not st["settings"]["quiet"]["on"])

    # --- Anmelden: Pruefungen ---------------------------------------------------------------------------
    ua = UA()
    ep = f"{FAKE}/wp/a1"
    s, d, r = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": ep, "keys": ua.keys(), "name": "Firefox auf Linux"}, raw_resp=True)
    sid = d.get("id") if isinstance(d, dict) else None
    ok(f"Web Push anmelden -> 201, Geraet in der Liste ({s}, {d})", s == 201 and sid and d["device"]["type"] == "webpush"
       and d["device"]["name"] == "Firefox auf Linux" and d["device"]["encrypted"])
    web_cookie = (r.getheader("set-cookie") or "").split(";")[0].split("=", 1)[-1]
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": ep, "keys": ua.keys(), "name": ""})
    ok(f"gleiche Adresse noch einmal -> 200, gleiches Abo, Name bleibt ({s})", s == 200 and d["id"] == sid and d["device"]["name"] == "Firefox auf Linux")
    bad = [
        ({"type": "webpush", "endpoint": ep + "x", "keys": {"p256dh": "AAAA", "auth": b64u(ua.auth)}}, 400),
        ({"type": "webpush", "endpoint": ep + "x", "keys": {"p256dh": b64u(ua.pub), "auth": "AAAA"}}, 400),
        ({"type": "webpush", "endpoint": "ftp://127.0.0.1/x", "keys": ua.keys()}, 400),
        ({"type": "webpush", "endpoint": "http://push.example.org/x", "keys": ua.keys()}, 400),        # http nur freigegeben
        ({"type": "webpush", "endpoint": "https://10.11.12.13/x", "keys": ua.keys()}, 400),            # intern ohne Freigabe
        ({"type": "webpush", "endpoint": "https://user:pw@push.example.org/x", "keys": ua.keys()}, 400),
        ({"type": "fax", "endpoint": ep}, 400),
        ({"type": "ntfy", "url": f"{FAKE}/a/b?x=1"}, 400),
        ({"type": "ntfy", "url": f"{FAKE}/bad topic"}, 400),
        ({"type": "apns", "token": "xyz"}, 400),
        ({"type": "apns", "token": "ab" * 32, "env": "beta"}, 400),
    ]
    codes = [req("POST", "/api/push/subscriptions", A, b)[0] for b, _ in bad]
    ok(f"ungueltige Anmeldungen abgewiesen (Schluessel, Schema, http, intern, Zugangsdaten, Art, ntfy, APNs) {codes}",
       codes == [c for _, c in bad])
    ok("nichts davon gespeichert", len(sub_rows("ps-a")) == 1)
    # Pruefung (unabhaengig): weitere interne Schreibweisen (IPv6, IPv4-in-IPv6, Dezimal, localhost, 0.0.0.0, Link-local)
    sneaky = ["https://[::1]/x", "https://[::ffff:127.0.0.1]/x", "https://2130706433/x", "https://localhost/x",
              "https://0.0.0.0/x", "https://169.254.169.254/latest", "https://[fe80::1]/x", "https://127.0.0.2/x"]
    codes = [req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": u, "keys": ua.keys()})[0] for u in sneaky]
    codes += [req("POST", "/api/push/subscriptions", A, {"type": "ntfy", "url": "https://[::1]/topic"})[0],
              req("POST", "/api/push/subscriptions", A, {"type": "unifiedpush", "endpoint": "https://192.168.1.1/up"})[0]]
    ok(f"interne Ziele in anderen Schreibweisen abgewiesen {codes}", codes == [400] * len(codes) and len(sub_rows("ps-a")) == 1)
    with connect() as c, c:                     # Freigabe nachtraeglich entzogen: Zustellung -> harter Fehler, keine Wiederholung
        bid = c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, created) VALUES ('ps-a', 'ntfy', ?, 'blk', ?)",
                        (f"{FAKE}/blocked-topic", time.time())).lastrowid
    hosts_now = auth.settings(fresh=True)["allowed_hosts"]
    auth.save_setting("allowed_hosts", ", ".join(h for h in re.split(r"[,\s]+", hosts_now) if h and h != f"127.0.0.1:{FP}"))
    auth.settings(fresh=True)
    try:
        r_blk = push.test_send("ps-a", bid)
    finally:
        auth.save_setting("allowed_hosts", hosts_now)
        auth.settings(fresh=True)
        with connect() as c, c:
            c.execute("DELETE FROM push_sub WHERE id=?", (bid,))
    ok(f"Ziel nicht (mehr) freigegeben -> 'fail' statt endloser Wiederholung ({r_blk})", r_blk == ("fail", "Ziel nicht freigegeben"))

    # --- Zustellung: Teilen -------------------------------------------------------------------------------
    pg = req("POST", "/api/notes", Bk, {"title": "Streng geheimer Titel", "icon": "🔐"})[1]["id"]
    m = mark()
    req("PUT", f"/api/notes/{pg}/shares", Bk, {"type": "user", "target": "ps-a", "role": "comment"})
    g = wait_got(path_is("/wp/a1"), m)
    msg = ua.decrypt(g[3]) if g else {}
    ok(f"Teilen -> verschluesselte Mitteilung an A ({msg})", msg.get("body") == "Bert Push hat eine Seite mit dir geteilt (Kommentieren)"
       and msg.get("kind") == "share.added" and msg.get("url") == f"/notes#{pg}" and msg.get("tag") == f"note-{pg}")
    ok("... kein Seitentitel, kein Inhalt im Klartext und in der Mitteilung", g and b"geheim" not in g[3] and "geheim" not in json.dumps(msg))
    h = g[2] if g else {}
    jw = re.fullmatch(r"vapid t=(\S+), k=(\S+)", h.get("authorization", ""))
    jt = jw and jwt_ok(jw.group(1), b64d(jw.group(2)))
    ok(f"... Kopfzeilen: aes128gcm, TTL, Urgency, Topic, VAPID (aud {jt and jt[1]['aud']})",
       h.get("content-encoding") == "aes128gcm" and int(h.get("ttl", 0)) > 0 and h.get("urgency") == "normal"
       and h.get("topic") == f"note-{pg}" and jt and jt[1]["aud"] == FAKE)
    time.sleep(0.5)
    ok("... Erfolg am Geraet vermerkt (last_ok)", req("GET", "/api/push", A)[1]["devices"][0]["last_ok"])
    # Annas eigene Seite, mit Bert geteilt (Kommentieren): Kommentare von Bert benachrichtigen Anna (comment.new)
    pgA = req("POST", "/api/notes", A, {"title": "Annas geheime Notizen"})[1]["id"]
    req("PUT", f"/api/notes/{pgA}/shares", A, {"type": "user", "target": "ps-b", "role": "comment"})

    # --- ntfy: nie mit Namen, Zugangstoken --------------------------------------------------------------
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "ntfy", "url": f"{FAKE}/ps-topic-a", "token": "tk_geheim.123", "name": "Handy"})
    nid = d.get("id") if isinstance(d, dict) else None
    ok(f"ntfy anmelden -> 201, als 'ohne Namen' markiert ({s})", s == 201 and nid and not d["device"]["encrypted"])
    with connect() as c:
        sec = c.execute("SELECT secret FROM push_sub WHERE id=?", (nid,)).fetchone()[0]
    ok("... Zugangstoken verschluesselt gespeichert", sec and "tk_geheim" not in sec and sec.startswith("enc:"))
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "b0", "body": "Kommentar-Inhalt XYZ"})
    gw = wait_got(path_is("/wp/a1"), m)
    gn = wait_got(lambda g: g[1] == "/" and g[0] == FP, m)
    mw = ua.decrypt(gw[3]) if gw else {}
    jn = json.loads(gn[3]) if gn else {}
    ok(f"Kommentar -> Web Push mit Name ({mw.get('body')})", mw.get("body") == "Bert Push hat deine Seite kommentiert" and mw.get("tag", "").startswith("thread-"))
    ok(f"... ntfy: Topic, Titel, Text OHNE Namen, Klick-Adresse, Token ({jn})",
       jn.get("topic") == "ps-topic-a" and jn.get("message") == "Neuer Kommentar auf deiner Seite" and "Bert" not in json.dumps(jn)
       and jn.get("click", "").endswith(f"/notes#{pgA}:b0") and gn[2].get("authorization") == "Bearer tk_geheim.123")
    ok("... kein Kommentar-Inhalt unterwegs", gn and b"XYZ" not in gn[3] and "XYZ" not in json.dumps(mw))

    # --- Einstellungen: ohne Namen, Bereiche, Ruhezeiten ----------------------------------------------------
    bads = [{"cats": {"share": "ja"}}, {"cats": {"spam": True}}, {"names": 1}, {"quiet": {"from": "25:00"}}, {"quiet": {"tz": "../etc/passwd"}},
            {"foo": 1}, {}]
    ok("ungueltige Einstellungen -> 400", all(req("PUT", "/api/push/settings", A, b)[0] == 400 for b in bads))
    s, d = req("PUT", "/api/push/settings", A, {"names": False})
    ok("Namen aus -> gespeichert", s == 200 and d["settings"]["names"] is False)
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "b7", "body": "Noch einer"})
    gw = wait_got(path_is("/wp/a1"), m)
    ok(f"... Web Push jetzt ohne Namen ({gw and ua.decrypt(gw[3]).get('body')})", gw and ua.decrypt(gw[3]).get("body") == "Neuer Kommentar auf deiner Seite")
    req("PUT", "/api/push/settings", A, {"names": True, "cats": {"comment": False}})
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "b8", "body": "Stumm"})
    ok("Bereich Kommentare aus -> keine Mitteilung (Glocke hat es trotzdem)", none_got(lambda g: g[0] == FP, m, 4))
    req("PUT", "/api/push/settings", A, {"cats": {"comment": True}})
    now = time.gmtime()
    fr, to = time.strftime("%H:%M", time.gmtime(time.time() - 3600)), time.strftime("%H:%M", time.gmtime(time.time() + 3600))
    s, d = req("PUT", "/api/push/settings", A, {"quiet": {"on": True, "from": fr, "to": to, "tz": "UTC"}})
    ok(f"Ruhezeit {fr}-{to} UTC gespeichert ({s})", s == 200 and d["settings"]["quiet"] == {"on": True, "from": fr, "to": to, "tz": "UTC"})
    ok("quiet_now: ueber Mitternacht und gleiche Zeiten", push.quiet_now({"on": True, "from": "22:00", "to": "07:00", "tz": "UTC"}, 1700000000 - 1700000000 % 86400 + 3600)
       and not push.quiet_now({"on": True, "from": "22:00", "to": "07:00", "tz": "UTC"}, 1700000000 - 1700000000 % 86400 + 12 * 3600)
       and not push.quiet_now({"on": True, "from": "08:00", "to": "08:00", "tz": "UTC"}) and now)
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "b9", "body": "Nachts"})
    ok("in der Ruhezeit keine Mitteilung", none_got(lambda g: g[0] == FP, m, 4))
    req("PUT", "/api/push/settings", A, {"quiet": {"on": False}})
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a'").fetchone()[0]
    ok(f"Warteschlange leer ({left})", left == 0)

    # --- Testmitteilung, Grenze -----------------------------------------------------------------------------
    m = mark()
    s, d = req("POST", f"/api/push/subscriptions/{sid}/test", A)
    g = wait_got(path_is("/wp/a1"), m, 3)
    ok(f"Testmitteilung -> zugestellt ({s}, {d}, {g and ua.decrypt(g[3]).get('body')})",
       s == 200 and d["ok"] and g and ua.decrypt(g[3])["body"] == "Testmitteilung – Mitteilungen kommen an.")
    ok("... gleich noch einmal -> 429", req("POST", f"/api/push/subscriptions/{sid}/test", A)[0] == 429)
    ok("... fremdes/unbekanntes Abo -> 404", req("POST", f"/api/push/subscriptions/{sid}/test", Bk)[0] == 404
       and req("POST", "/api/push/subscriptions/999999999/test", A)[0] == 404)
    s, d = req("POST", "/api/push/subscriptions", Bk, {"type": "ntfy", "url": f"{FAKE}/ps-topic-b", "name": "Bert"}, hdr={"Accept-Language": "de"})
    pga = req("POST", "/api/notes", A, {"title": "Fuer Bert"})[1]["id"]
    m = mark()
    req("PUT", f"/api/notes/{pga}/shares", A, {"type": "user", "target": "ps-b", "role": "view"})
    gn = wait_got(lambda g: g[1] == "/" and json.loads(g[3]).get("topic") == "ps-topic-b", m)
    ok(f"Text in der Sprache des KONTOS von B (en), ohne Token kein Authorization-Kopf ({gn and json.loads(gn[3])})",
       s == 201 and gn and json.loads(gn[3])["message"] == "Someone shared a page with you" and "authorization" not in gn[2])

    # --- Isolation -----------------------------------------------------------------------------------------
    sb, db_ = req("GET", "/api/push", Bk)
    ok("B sieht A's Geraete nicht (nur das eigene)", sb == 200 and [x["name"] for x in db_["devices"]] == ["Bert"])
    ok("B kann A's Abo nicht loeschen (404) und nicht per Adresse abmelden",
       req("DELETE", f"/api/push/subscriptions/{sid}", Bk)[0] == 404
       and req("POST", "/api/push/unsubscribe", Bk, {"endpoint": ep})[1]["removed"] == 0 and len(sub_rows("ps-a")) == 2)
    ok("ohne X-Saga-Kopf (Cross-Site) -> 401", req("GET", "/api/push", None, hdr={"Cookie": "saga=" + A})[0] == 401)

    # --- abgelaufen (410), Wiederholung (503), harte Fehler --------------------------------------------------
    ug, ur = UA(), UA()
    gid = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/gone", "keys": ug.keys()})[1]["id"]
    rid = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/retry", "keys": ur.keys()})[1]["id"]
    STATUS["/wp/gone"] = [410]
    STATUS["/wp/retry"] = [503, 201]
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c1", "body": "Fuer alle Geraete"})
    wait_got(path_is("/wp/gone"), m); wait_got(path_is("/wp/retry"), m)
    time.sleep(0.8)
    ids = [r[0] for r in sub_rows("ps-a")]
    ok(f"410 Gone -> Abo entfernt ({ids})", gid not in ids and rid in ids)
    with connect() as c:
        q = c.execute("SELECT tries, next_at FROM push_queue WHERE sub_id=?", (rid,)).fetchone()
        le = c.execute("SELECT last_err FROM push_sub WHERE id=?", (rid,)).fetchone()[0]
    ok(f"503 -> Auftrag bleibt, naechster Versuch spaeter ({q}, {le})", q and q[0] == 1 and q[1] > time.time() + 20 and le.startswith("HTTP 503"))
    m = mark()
    with connect() as c, c:
        c.execute("UPDATE push_queue SET next_at=0 WHERE sub_id=?", (rid,))
    push.wake()
    g = wait_got(path_is("/wp/retry"), m)
    time.sleep(0.8)
    with connect() as c:
        q = c.execute("SELECT COUNT(*) FROM push_queue WHERE sub_id=?", (rid,)).fetchone()[0]
        le = c.execute("SELECT last_err, fails FROM push_sub WHERE id=?", (rid,)).fetchone()
    ok(f"... zweiter Versuch zugestellt, Auftrag weg, Fehler zurueckgesetzt ({q}, {le})",
       g and ur.decrypt(g[3])["kind"] == "comment.new" and q == 0 and le == (None, 0))
    STATUS["/wp/retry"] = [400]
    with connect() as c, c:
        c.execute("UPDATE push_sub SET fails=? WHERE id=?", (push.FAIL_MAX - 1, rid))
    s, d = req("POST", f"/api/push/subscriptions/{rid}/test", A)
    ok(f"harter Fehler (400) als {push.FAIL_MAX}. in Folge -> Abo entfernt ({d})", s == 200 and not d["ok"] and d["error"] == "HTTP 400"
       and rid not in [r[0] for r in sub_rows("ps-a")])

    # --- Konto-Wechsel im selben Browser, Grenzen ------------------------------------------------------------
    uo = UA()
    oid = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/shared", "keys": uo.keys()})[1]["id"]
    s, d = req("POST", "/api/push/subscriptions", Bk, {"type": "webpush", "endpoint": f"{FAKE}/wp/shared", "keys": uo.keys()})
    ok(f"gleiche Browser-Adresse von B angemeldet -> gehoert jetzt B, bei A weg ({s})",
       s == 201 and d["id"] != oid and oid not in [r[0] for r in sub_rows("ps-a")])
    # ntfy/UnifiedPush: die Adresse kann man abschreiben -> nie von einem anderen Konto uebernehmen (Haertung v2)
    s, d = req("POST", "/api/push/subscriptions", Bk, {"type": "ntfy", "url": f"{FAKE}/ps-topic-a"})
    ok(f"ntfy-Topic eines anderen Kontos -> 409, bleibt bei A ({s})", s == 409 and nid in [r[0] for r in sub_rows("ps-a")])
    s0, d0 = req("POST", "/api/push/subscriptions", A, {"type": "unifiedpush", "endpoint": f"{FAKE}/up/a-web"})
    s, d = req("POST", "/api/push/subscriptions", Bk, {"type": "unifiedpush", "endpoint": f"{FAKE}/up/a-web"})
    ok(f"UnifiedPush-Adresse eines anderen Kontos -> 409, bleibt bei A ({s0}, {s})",
       s0 == 201 and s == 409 and d0["id"] in [r[0] for r in sub_rows("ps-a")])
    req("DELETE", f"/api/push/subscriptions/{d0['id']}", A)
    with connect() as c, c:
        for i in range(push.SUB_MAX - len(sub_rows("ps-b"))):
            c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, created) VALUES ('ps-b', 'ntfy', ?, 'x', ?)", (f"{FAKE}/fill{i}", time.time()))
    s, d = req("POST", "/api/push/subscriptions", Bk, {"type": "ntfy", "url": f"{FAKE}/too-many"})
    ok(f"hoechstens {push.SUB_MAX} Geraete je Konto -> 409 ({s})", s == 409)
    with connect() as c, c:
        c.execute("DELETE FROM push_sub WHERE uid='ps-b' AND endpoint LIKE ?", (f"{FAKE}/fill%",))
    codes = [req("POST", "/api/push/subscriptions", Bk, {"type": "ntfy", "url": f"{FAKE}/r{i}"})[0] for i in range(push.REG_MIN + 1)]
    ok(f"Anmeldungen je Minute begrenzt -> 429 ({codes[-3:]})", 429 in codes)
    with connect() as c, c:
        c.execute("DELETE FROM push_sub WHERE uid='ps-b' AND endpoint LIKE ?", (f"{FAKE}/r%",))

    # --- App: Geraete-Schluessel, UnifiedPush (mit/ohne Verschluesselung), endet mit der Geraete-Anmeldung -------
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('ps-a','ps-a','Pixel',?,?,?)",
                        (_tok_hash(tok), tok[:8], time.time())).lastrowid
    BEAR = {"Authorization": "Bearer " + tok}
    uu = UA()
    s1, d1 = req("POST", "/api/push/subscriptions", None, {"type": "unifiedpush", "endpoint": f"{FAKE}/up/enc", "keys": uu.keys(), "name": "Pixel"}, hdr=BEAR)
    s2, d2 = req("POST", "/api/push/subscriptions", None, {"type": "unifiedpush", "endpoint": f"{FAKE}/up/plain", "name": "Pixel (alt)"}, hdr=BEAR)
    ok(f"App (Geraete-Schluessel): UnifiedPush mit und ohne Schluessel angemeldet ({s1}, {s2})",
       s1 == 201 and s2 == 201 and d1["device"]["this_device"] and d1["device"]["encrypted"] and not d2["device"]["encrypted"])
    ok("... haengt an der Geraete-Anmeldung", {r[3] for r in sub_rows("ps-a") if r[1] == "unifiedpush"} == {did})
    ok("falscher Geraete-Schluessel -> 401", req("GET", "/api/push", None, hdr={"Authorization": "Bearer hkd_falsch"})[0] == 401)
    m = mark()
    req("PUT", f"/api/notes/{pg}/shares", Bk, {"type": "user", "target": "ps-a", "role": "edit"})
    ge, gp = wait_got(path_is("/up/enc"), m), wait_got(path_is("/up/plain"), m)
    me = uu.decrypt(ge[3]) if ge else {}
    mp = json.loads(gp[3]) if gp else {}
    ok(f"UnifiedPush verschluesselt (mit Namen) ({me.get('body')})", me.get("body") == "Bert Push hat deine Rolle geändert: Bearbeiten" and ge[2].get("content-encoding") == "aes128gcm")
    ok(f"UnifiedPush ohne Schluessel: Klartext OHNE Namen ({mp.get('body')})", mp.get("body") == "Deine Rolle auf einer geteilten Seite hat sich geändert" and "Bert" not in gp[3].decode())
    with connect() as c, c:
        c.execute("DELETE FROM device_login WHERE id=?", (did,))
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c2", "body": "nach App-Abmeldung"})
    wait_got(path_is("/wp/a1"), m)
    time.sleep(0.5)
    ok("Geraete-Anmeldung weg -> App-Abos entfernt, nichts mehr an sie", not [r for r in sub_rows("ps-a") if r[1] == "unifiedpush"]
       and not [g for g in GOT[m:] if g[1].startswith("/up/")])

    # --- Chat-Schnittstelle (feature/chat): ohne Inhalt ---------------------------------------------------------
    m = mark()
    with connect() as c, c:
        bus.fire("push.send", c, uid="ps-a", kind="chat.message", actor="ps-b", data={"chat": "c-42", "text": "GEHEIMER CHATTEXT"})
    g = wait_got(path_is("/wp/a1"), m)
    mc = ua.decrypt(g[3]) if g else {}
    gn = wait_got(lambda g: g[1] == "/" and json.loads(g[3]).get("topic") == "ps-topic-a", m)
    ok(f"chat.message -> 'Neue Nachricht von …', Link zum Chat, Topic je Chat ({mc})",
       mc.get("body") == "Neue Nachricht von Bert Push" and mc.get("url") == "/chats#c-42" and mc.get("tag") == "chat-c-42" and g[2].get("urgency") == "high")
    ok("... ntfy: 'Neue Nachricht' ohne Namen; Chat-Inhalt nirgends (auch nicht in der Warteschlange)",
       gn and json.loads(gn[3])["message"] == "Neue Nachricht" and b"GEHEIM" not in gn[3] and "GEHEIM" not in json.dumps(mc))
    with connect() as c, c:
        n = push.enqueue(c, "ps-a", "chat.message", actor="ps-a", data={"chat": "x"})
    ok("an sich selbst: nichts", n == 0)
    req("PUT", "/api/push/settings", A, {"cats": {"chat": False}})
    with connect() as c, c:
        n = push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "x"})
    ok("Bereich Chat aus: nichts", n == 0)
    req("PUT", "/api/push/settings", A, {"cats": {"chat": True}})

    # --- Zugriff beim Zustellen erneut geprueft --------------------------------------------------------------
    from saga import notify
    with connect() as c, c:
        nn = notify.create(c, "ps-a", "share.added", "ps-b", pga + 0, data={"role": "view"})
        c.execute("DELETE FROM push_queue WHERE uid='ps-a' AND ref=?", (nn,))
    with connect() as c:
        own = push.message(c, "ps-a", "share.added", ref=nn)          # A's eigene Seite pga: Zugriff ja
    pgx = req("POST", "/api/notes", Bk, {"title": "Kurz geteilt"})[1]["id"]
    with connect() as c, c:
        nx = notify.create(c, "ps-a", "share.added", "ps-b", pgx, data={"role": "view"})
        c.execute("DELETE FROM push_queue WHERE uid='ps-a' AND ref=?", (nx,))
    with connect() as c:
        gone = push.message(c, "ps-a", "share.added", ref=nx)
    ok("nach Entzug (bzw. nie Zugriff) keine Mitteilung; mit Zugriff schon", own and gone is None)

    # --- Abmelden im Browser, "ueberall abmelden" ---------------------------------------------------------------
    ok("Anmelde-Antwort setzt die Sitzung (Abo gehoert zu diesem Browser)", bool(web_cookie))
    s, _, r = req("GET", "/logout", None, hdr={"Cookie": "saga=" + web_cookie}, raw_resp=True)
    ok(f"Abmelden in diesem Browser -> Browser-Abo weg, ntfy bleibt ({s})",
       s == 303 and sid not in [x[0] for x in sub_rows("ps-a")] and nid in [x[0] for x in sub_rows("ps-a")])
    u2 = UA()
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/a2", "keys": u2.keys()})
    w2 = d["id"]
    with connect() as c, c:
        c.execute("UPDATE users SET logout_at=? WHERE username='ps-a'", (time.time() + 1,))
    auth._ui_cache.pop("ps-a", None)
    with connect() as c, c:
        n = push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "c-1"})
    ids = [x[0] for x in sub_rows("ps-a")]
    ok(f"'Ueberall abmelden' -> aeltere Browser-Abos entfernt, ntfy bleibt ({n})", w2 not in ids and nid in ids and n == 1)
    with connect() as c, c:
        c.execute("UPDATE users SET logout_at=NULL WHERE username='ps-a'")
    auth._ui_cache.pop("ps-a", None)

    # --- Admin: Wege abschalten, APNs einrichten ----------------------------------------------------------------
    s, html = admin("/settings/push")
    ok(f"Admin-Seite Mitteilungen: zeigt VAPID-Schluessel, Zaehler, APNs aus ({s})", s == 200 and vpub in html and "Angemeldete Geräte" in html)
    s, html = admin("/settings/push", {"enabled": "1", "ntfy": "1", "unifiedpush": "1", "apns_enabled": "1", "team_id": "ABCDE12345",
                                       "key_id": "KEY1234567", "bundle_id": "org.example.saga"}, files={"p8": ("AuthKey.p8", b"kein schluessel")})
    ok("ungueltige .p8-Datei -> Fehler, nichts gespeichert", "kein gültiger .p8-Schlüssel" in html and not auth.settings(fresh=True)["apns"]["p8"])
    key = ec.generate_private_key(P256)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    s, html = admin("/settings/push", {"enabled": "1", "ntfy": "1", "unifiedpush": "1", "apns_enabled": "1", "team_id": "ABCDE12345",
                                       "key_id": "KEY1234567", "bundle_id": "org.example.saga"}, files={"p8": ("AuthKey.p8", pem)})
    st = auth.settings(fresh=True)
    with connect() as c:
        raw = c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()[0]
    ok("gueltige .p8 -> gespeichert, verschluesselt; Web Push aus", "Gespeichert." in html and st["apns"]["p8"].strip() == pem.decode().strip()
       and "PRIVATE KEY" not in raw and st["push"]["webpush"] is False)
    with connect() as c, c:                                       # Test: APNs-Attrappe statt Apple (nur ueber die Datenbank setzbar)
        v = json.loads(c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()[0]); v["url"] = f"http://127.0.0.1:{FA}"
        c.execute("UPDATE setting SET v=? WHERE k='apns'", (json.dumps(v),))
    time.sleep(WAIT)
    s, st = req("GET", "/api/push", A)
    ok("GET /api/push: APNs bereit (Bundle-ID), Web Push aus", st["server"]["apns"] and st["server"]["apns_bundle"] == "org.example.saga"
       and not st["server"]["webpush"] and st["server"]["vapid_public"] is None)
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/x", "keys": UA().keys()})
    ok(f"Web Push abgeschaltet -> Anmeldung 403 ({s})", s == 403)
    tok2 = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('ps-a','ps-a','iPhone',?,?,?)", (_tok_hash(tok2), tok2[:8], time.time()))
    good, dead = "a1" * 32, "dead" * 16
    s1, d1 = req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": good, "env": "sandbox", "name": "iPhone"}, hdr={"Authorization": "Bearer " + tok2})
    s2, _ = req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": dead, "name": "altes iPhone"}, hdr={"Authorization": "Bearer " + tok2})
    ok(f"APNs-Geraetetoken angemeldet ({s1}, {s2})", s1 == 201 and s2 == 201 and d1["active"] and d1["device"]["where"] == "Apple (sandbox)")
    STATUS[f"/3/device/{dead}"] = [410]
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c3", "body": "an das iPhone"})
    ga = wait_got(path_is(f"/3/device/{good}"), m)
    wait_got(path_is(f"/3/device/{dead}"), m)
    h = ga[2] if ga else {}
    pa = json.loads(ga[3]) if ga else {}
    j = ga and jwt_ok(h.get("authorization", "x x.y.z").split(" ")[1], key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
    ok(f"APNs: Anbieter-Token ES256 (kid, iss) gueltig ({j})", j and j[0] == {"alg": "ES256", "kid": "KEY1234567"} and j[1]["iss"] == "ABCDE12345")
    ok(f"APNs: Kopfzeilen und Inhalt ({pa})", pa and h.get("apns-topic") == "org.example.saga" and h.get("apns-push-type") == "alert"
       and h.get("apns-priority") == "10" and pa["aps"]["alert"]["body"] == "Bert Push hat deine Seite kommentiert" and pa["url"].startswith(f"/notes#{pgA}"))
    time.sleep(0.6)
    ok("APNs 410 Unregistered -> Token entfernt", f"apns:production:{dead}" not in [x[2] for x in sub_rows("ps-a")]
       and f"apns:sandbox:{good}" in [x[2] for x in sub_rows("ps-a")])
    ok("ntfy wurde ebenfalls benachrichtigt (alle Wege parallel)", wait_got(lambda g: g[1] == "/" and json.loads(g[3]).get("topic") == "ps-topic-a", m, 1))

    # --- Aufraeumen, Kontoloeschung, Log -------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, created, next_at) VALUES ('ps-a', 0, 'comment.new', ?, ?)", (time.time() - 2 * 86400, time.time() + 9e9))
    push.cleanup()
    with connect() as c:
        old = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a' AND created < ?", (time.time() - 86400,)).fetchone()[0]
    ok("Auftraege aelter als 24 h werden verworfen", old == 0)
    with connect() as c, c:
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, actor, created, next_at) VALUES ('ps-b', 0, 'chat.message', 'ps-a', ?, ?)", (time.time(), time.time() + 9e9))
    s, _ = admin(f"/users/{auth.get_user('ps-a')['id']}", {"action": "delete", "confirm": "ps-a"})
    with connect() as c:
        n1 = c.execute("SELECT COUNT(*) FROM push_sub WHERE uid='ps-a'").fetchone()[0]
        n2 = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a' OR actor='ps-a'").fetchone()[0]
    ok(f"Kontoloeschung: Abos und Auftraege weg, als Absender anonymisiert ({n1}, {n2})", n1 == 0 and n2 == 0)
    log = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8", errors="replace").read()
        ok("Server-Log enthaelt keine Mitteilungstexte, Adressen oder Token",
           not any(x in txt for x in ("hat deine Seite kommentiert", "Neue Nachricht von", "tk_geheim", "ps-topic-a", "/wp/a1", "GEHEIM")))
    with connect() as c, c:
        c.execute("DELETE FROM push_queue WHERE uid LIKE 'ps-%'")

    # --- VAPID-Schluessel nicht lesbar (z. B. SECRET_KEY falsch): alter Eintrag wird aufgehoben, nicht vernichtet ------
    with connect() as c:
        orig = c.execute("SELECT v FROM setting WHERE k='push_vapid'").fetchone()[0]
    broken = json.dumps({"pub": "x", "priv": "enc:kaputt", "created": 1})
    try:
        with connect() as c, c:
            c.execute("UPDATE setting SET v=? WHERE k='push_vapid'", (broken,))
        push._vapid.clear()
        _k, newpub = push.vapid()
        with connect() as c:
            kept = c.execute("SELECT v FROM setting WHERE k='push_vapid_old'").fetchone()
        ok("VAPID nicht lesbar -> neuer Schluessel, alter Eintrag als push_vapid_old aufgehoben", newpub and newpub != "x" and kept and kept[0] == broken)
    finally:
        with connect() as c, c:
            c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('push_vapid', ?)", (orig,))
            c.execute("DELETE FROM setting WHERE k='push_vapid_old'")
        push._vapid.clear()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        cleanup()
        raise
