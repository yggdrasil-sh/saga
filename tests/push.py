"""Mitteilungen aufs Handy/Desktop (1.7): Web Push, ntfy, UnifiedPush, APNs - mit Fake-Empfaengern im Test.

Fake-Empfaenger (in diesem Prozess): 127.0.0.1:18290 nimmt Web Push (/wp/...), UnifiedPush (/up/...) und ntfy (JSON an
"/") an, 127.0.0.1:18291 spielt APNs (/3/device/<token>; HTTP/1.1 - httpx nutzt HTTP/2 nur mit TLS). Web-Push-Koerper
werden hier wie im Browser entschluesselt (RFC 8291), VAPID- und APNs-Token gegen die oeffentlichen Schluessel geprueft.
Geprueft: Testvektor RFC 8291, VAPID, Anmelden/Erneuern/Wechsel des Kontos, Pruefung der Adressen (SSRF, http nur
freigegeben), Zustellung bei Teilen/Kommentar, Text ohne Seitentitel/Inhalte, ntfy nie mit Namen, "ohne Namen",
Bereiche, Ruhezeiten, 410 -> Abo weg, 503 -> Wiederholung, harte Fehler -> Abo weg, Testmitteilung + Grenze,
Isolation, Grenzen, App-Abos (Geraete-Schluessel, enden mit der Geraete-Anmeldung), Chat-Schnittstelle (ohne Inhalt),
Abmelden im Browser, "ueberall abmelden", Zugriff beim Zustellen, Admin-Seite (Wege, APNs-Schluessel), APNs-Zustellung,
Kontoloeschung, keine Texte im Server-Log. 2.6 (Bugs-DB 2029-3): Push-Weiche - Nicht stoeren = keine Mitteilung, Glocke ja,
Nachpruefung beim Zustellen, zurueckgehaltener Anruf (chat_call.hold) nie als Anruf-/VoIP-Mitteilung, Ausnahme, Beschaeftigt
ohne Ton, Ruhezeit (nicht gezaehlt; quiet_dnd aus = alte Regel); Sammelmeldung status.digest de/en/th, ohne Namen, genau
einmal (zwei Geraete gleichzeitig, kill -9 vor/nach dem Commit im Unterprozess --worker, Ablauf mit zwei Prozessen).
Wegwerf-Konten ps-*; raeumt auf."""
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
        for t in ("note", "note_version", "user_kv", "notification", "device_login", "push_sub", "push_queue", "user_status"):
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
    bp = push.encrypt(json.dumps({"x": "äöü ✓"}).encode(), b64u(ua.pub), b64u(ua.auth), pad_to=push.pad_size(30))
    ok("2262-1: mit Auffuellen (pad_to 512) -> Empfaenger entschluesselt, Laenge verborgen", ua.decrypt(bp) == {"x": "äöü ✓"}
       and len(bp) == 86 + 512 + 16)
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


# =====================================================================================================
# 2.6 (Bugs-DB 2029-3): Push-Weiche (REACH) und Sammelmeldung status.digest
# =====================================================================================================
DIG_DE = "Während „Nicht stören“: 4 Nachrichten, 2 verpasste Anrufe, 1 weitere Mitteilung"
DIG_EN = "While “Do not disturb” was on: 4 messages, 2 missed calls, 1 other notification"
DIG_TH = "ระหว่างโหมด “ห้ามรบกวน”: 4 ข้อความ สายที่ไม่ได้รับ 2 สาย การแจ้งเตือนอื่น 1 รายการ"


def held(uid):
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='status_held'", (uid,)).fetchone()
    return json.loads(r[0]) if r else None


def digests(uas, since):
    """Pfad -> [entschluesselte Sammelmeldungen] seit since."""
    with LOCK:
        got = list(GOT[since:])
    out = {}
    for path, u in uas.items():
        out[path] = [m for m in (u.decrypt(g[3]) for g in got if g[1] == path) if m.get("kind") == "status.digest"]
    return out


def wait_digests(uas, since, timeout=9.0, settle=2.5):
    """Warten, bis jedes Abo mindestens eine Sammelmeldung hat, dann settle Sekunden auf weitere (doppelte) warten."""
    end = time.time() + timeout
    while time.time() < end and not all(digests(uas, since).values()):
        time.sleep(0.2)
    time.sleep(settle)
    return digests(uas, since)


def worker(mode):
    """Unterprozess (diese Datei mit --worker <mode>): beendet Nicht stoeren von ps-a in einer Transaktion und stirbt per
    SIGKILL - rollback: vor dem Commit, commit: direkt danach (vor jeder Zustellung). -> Rueckgabecode."""
    import subprocess
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", mode], env=dict(os.environ), capture_output=True,
                       text=True, timeout=120)
    if p.returncode != -9:
        print((p.stdout + p.stderr)[-800:], flush=True)
    return p.returncode


def worker_main(mode):
    import signal
    from saga import user_status
    with connect() as c, c:
        user_status.set_status(c, "ps-a", {"state": "available"})
        if mode == "rollback":
            os.kill(os.getpid(), signal.SIGKILL)
    os.kill(os.getpid(), signal.SIGKILL)


def digest_part(A, Bk, ua, pgA, nn):
    from saga import notify
    ue, ut = UA(), UA()
    s1, d1 = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/dg-en", "keys": ue.keys(), "lang": "en"})
    s2, d2 = req("POST", "/api/push/subscriptions", A, {"type": "webpush", "endpoint": f"{FAKE}/wp/dg-th", "keys": ut.keys(), "lang": "th"})
    ok(f"2029-3: zwei weitere Geraete von A (Sprache en, th) ({s1}, {s2})", s1 == 201 and s2 == 201)
    UAS = {"/wp/a1": ua, "/wp/dg-en": ue, "/wp/dg-th": ut}
    is_dig = lambda g: g[1] in UAS and UAS[g[1]].decrypt(g[3]).get("kind") == "status.digest"     # noqa: E731
    t0 = time.time()
    with connect() as c, c:
        chat = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', 'ps-b', ?) RETURNING id", (t0,)).fetchone()[0]
    try:
        s, v = req("PUT", "/api/account/status", A, {"state": "dnd"})
        ok(f"A: Nicht stoeren ({s}, {v.get('effective')})", s == 200 and v["effective"] == "dnd")
        m0 = mark()
        cid_ring, cid_held = secrets.token_hex(16), secrets.token_hex(16)
        with connect() as c, c:
            for _ in range(3):
                bus.fire("push.send", c, uid="ps-a", kind="chat.message", actor="ps-b", data={"chat": str(chat)})
            bus.fire("push.send", c, uid="ps-a", kind="chat.mention", actor="ps-b", data={"chat": str(chat)})
            notify.create(c, "ps-a", "call.missed", "ps-b", data={"chat": chat, "call": "ab" * 16})
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, hold) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (cid_held, chat, "ps-b", "ps-a", "dev-b", "audio", "ended", time.time(), "dnd"))
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, hold) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (cid_ring, chat, "ps-b", "ps-a", "dev-b", "audio", "ringing", time.time(), "dnd"))
            # Pruefer (2029): zurueckgehalten, aber angenommen bzw. selbst abgelehnt - nicht verpasst, nicht gezaehlt
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, answered, hold) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(16), chat, "ps-b", "ps-a", "dev-b", "audio", "ended", "hangup",
                                                         time.time(), time.time(), "dnd"))
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, hold) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(16), chat, "ps-b", "ps-a", "dev-b", "audio", "ended", "declined",
                                                       time.time(), "dnd"))
            # Bugs-DB 2365: Glocke zum zurueckgehaltenen Anruf, waehrend Nicht stoeren geloescht -> der Anruf zaehlt genau einmal
            gone_bell = notify.create(c, "ps-a", "call.missed", "ps-b", data={"chat": chat, "call": cid_held})
        with connect() as c, c:
            c.execute("DELETE FROM notification WHERE id=?", (gone_bell,))
        req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "dg1", "body": "waehrend Nicht stoeren"})     # Server: comment.new
        with connect() as c:
            q = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a'").fetchone()[0]
            bell = c.execute("SELECT COUNT(*) FROM notification WHERE uid='ps-a' AND kind IN ('comment.new', 'call.missed') AND created>=?",
                             (t0,)).fetchone()[0]
            late = push.message(c, "ps-a", "share.added", ref=nn)
            vo = push.message(c, "ps-a", "call", actor="ps-b", data={"call": cid_ring}, created=time.time())
        h = held("ps-a")
        ok(f"route hold: keine Mitteilung (Warteschlange {q}), Glocke ja ({bell}), gezaehlt ohne Namen ({h})",
           q == 0 and bell == 2 and h and (h.get("m"), h.get("c"), h.get("o")) == (4, 1, 1) and "ps-b" not in json.dumps(h))
        ok("Nachpruefung beim Zustellen: waehrend Nicht stoeren keine Mitteilung (auch fuer aeltere Auftraege)", late is None)
        with connect() as c, c:
            n_call = push.enqueue(c, "ps-a", "call", actor="ps-b", data={"call": cid_ring})
        ok(f"zurueckgehaltener Anruf (hold dnd): kein Auftrag, beim Zustellen nichts - nie VoIP ({n_call}, {vo})", n_call == 0 and vo is None)
        with connect() as c, c:
            c.execute("UPDATE chat_call SET hold=NULL WHERE id=?", (cid_ring,))
        with connect() as c:
            vo = push.message(c, "ps-a", "call", actor="ps-b", data={"call": cid_ring}, created=time.time())
        ok("... gleicher Anruf ohne hold: Anruf-Mitteilung (die Weiche ist calls.ring_gate)", vo and vo["kind"] == "call" and vo["call"] == cid_ring)
        with connect() as c, c:
            c.execute("UPDATE chat_call SET state='ended', hold='knock' WHERE id=?", (cid_ring,))
        # Pruefer: Gruppen-Einladung (kein hold je Person) waehrend Nicht stoeren - kein Auftrag, beim Zustellen nichts, nicht gezaehlt.
        # Anrufer ps-adm: ps-b gilt hier als Wiederholungsanruf (cid_held oben, hold dnd) und kaeme durch (Matrix: 2. Anruf klingelt)
        cid_grp = secrets.token_hex(16)
        with connect() as c, c:
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, kind) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (cid_grp, chat, "ps-adm", "", "dev-adm", "audio", "ringing", time.time(), "group"))
            for w in ("ps-a", "ps-b"):
                c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?, ?, 'ringing', ?)", (cid_grp, w, time.time()))
        with connect() as c, c:
            n_grp = push.enqueue(c, "ps-a", "call.group", actor="ps-adm", data={"chat": chat, "call": cid_grp})
        with connect() as c:
            vg = push.message(c, "ps-a", "call.group", actor="ps-adm", data={"call": cid_grp}, created=time.time())
            vg_sub = push.message(c, "ps-a", "call.group", actor="ps-adm", data={"call": cid_grp}, sub_kind="apns-voip", created=time.time())
            vg_adm = push.message(c, "ps-b", "call.group", actor="ps-adm", data={"call": cid_grp}, sub_kind="apns-voip", created=time.time())
        hg = held("ps-a") or {}
        ok(f"Gruppen-Einladung waehrend Nicht stoeren: kein Auftrag, nie (VoIP-)Mitteilung, nicht gezaehlt ({n_grp}, {vg}, {vg_sub}, {hg})",
           n_grp == 0 and vg is None and vg_sub is None and (hg.get("m"), hg.get("c"), hg.get("o")) == (4, 1, 1))
        ok(f"... gleiche Einladung fuer ein Konto ohne Nicht stoeren: Anruf-Mitteilung ({vg_adm and vg_adm.get('kind')})",
           vg_adm is not None and vg_adm.get("kind") == "call.group" and vg_adm.get("call") == cid_grp)
        with connect() as c, c:
            c.execute("UPDATE chat_call SET state='ended' WHERE id=?", (cid_grp,))
        ok("waehrend Nicht stoeren nichts an die Geraete von A",
           none_got(lambda g: g[1] in UAS or (g[1] == "/" and b"ps-topic-a" in g[3]), m0, 1.5))

        # zwei Geraete beenden gleichzeitig -> genau eine Sammelmeldung je Abo
        m1 = mark()
        out = []
        ths = [threading.Thread(target=lambda: out.append(req("PUT", "/api/account/status", A, {"state": "available"})[0])) for _ in range(2)]
        [x.start() for x in ths]
        [x.join() for x in ths]
        dg = wait_digests(UAS, m1)
        de_, en_, th_ = dg["/wp/a1"], dg["/wp/dg-en"], dg["/wp/dg-th"]
        ok(f"zwei Geraete beenden gleichzeitig ({sorted(out)}): je Abo genau EINE Sammelmeldung ({[len(x) for x in dg.values()]})",
           sorted(out) == [200, 200] and all(len(x) == 1 for x in dg.values()))
        ok(f"Sammelmeldung de ({de_ and de_[0]['body']})", de_ and de_[0]["body"] == DIG_DE and de_[0]["lang"] == "de")
        ok(f"Sammelmeldung en ({en_ and en_[0]['body']})", en_ and en_[0]["body"] == DIG_EN and en_[0]["lang"] == "en")
        ok(f"Sammelmeldung th ({th_ and th_[0]['body']})", th_ and th_[0]["body"] == DIG_TH and th_[0]["lang"] == "th")
        allm = [x for v in dg.values() for x in v]
        blob = json.dumps(allm, ensure_ascii=False)
        ok(f"Nutzlast ohne Namen: kein actor/caller, Zahlen in digest, tag status-digest ({de_ and {k: de_[0][k] for k in ('digest', 'n', 'tag', 'url')}})",
           allm and all(not ({"actor", "caller", "chat", "call"} & set(x)) for x in allm) and "Bert" not in blob and "ps-b" not in blob
           and de_[0]["digest"] == {"messages": 4, "calls": 2, "other": 1} and de_[0]["n"] == 7 and de_[0]["tag"] == "status-digest"
           and de_[0]["url"] == "/")
        gn = wait_got(lambda g: g[1] == "/" and json.loads(g[3]).get("topic") == "ps-topic-a" and "Nicht stören" in json.loads(g[3]).get("message", ""), m1, 3)
        ok(f"ntfy: Sammelmeldung im Klartext, ohne Namen ({gn and json.loads(gn[3]).get('message')})",
           gn and json.loads(gn[3])["message"] == DIG_DE and b"Bert" not in gn[3])
        ok("Zaehler mit der Sammelmeldung geloescht", held("ps-a") is None)
        with connect() as c:
            back = push.message(c, "ps-a", "share.added", ref=nn)
        ok("nach Nicht stoeren: Mitteilungen wieder (Nachpruefung laesst durch)", back is not None and "silent" not in back)
        m2 = mark()
        req("PUT", "/api/account/status", A, {"state": "available"})
        req("PUT", "/api/account/status", A, {"state": "dnd"})
        req("PUT", "/api/account/status", A, {"state": "busy"})
        ok("noch einmal beendet / Nicht stoeren ohne Zurueckgehaltenes: keine Sammelmeldung", none_got(is_dig, m2, 3))

        # Beschaeftigt "ohne Ton", Ausnahme
        req("PUT", "/api/account/status/prefs", A, {"busy": "silent"})
        with connect() as c:
            sil = push.message(c, "ps-a", "share.added", ref=nn)
        ok(f"Beschaeftigt + busy silent: Mitteilung ohne Ton (silent {sil and sil.get('silent')})", sil and sil.get("silent") is True)
        # Bugs-DB 2365: Nicht stoeren endet in Beschaeftigt "ohne Ton" -> auch die Sammelmeldung ohne Ton
        req("PUT", "/api/account/status", A, {"state": "dnd"})
        with connect() as c, c:
            push.enqueue(c, "ps-a", "chat.message", actor="ps-adm", data={"chat": "dg-s"})
        m2s = mark()
        req("PUT", "/api/account/status", A, {"state": "busy"})
        dg = wait_digests(UAS, m2s)
        ok(f"Beschaeftigt ohne Ton: Sammelmeldung ohne Ton (silent) ({[[x.get('silent') for x in v] for v in dg.values()]})",
           all(len(x) == 1 and x[0].get("silent") is True for x in dg.values()))
        gs = wait_got(lambda g: g[1] == "/" and json.loads(g[3]).get("topic") == "ps-topic-a" and "Nicht stören" in json.loads(g[3]).get("message", ""), m2s, 3)
        ok(f"... ntfy leise (Prioritaet 2) ({gs and json.loads(gs[3]).get('priority')})", gs and json.loads(gs[3]).get("priority") == 2)
        req("PUT", "/api/account/status/prefs", A, {"busy": "show", "exceptions": [{"type": "user", "ref": auth_ref("ps-b")}]})
        req("PUT", "/api/account/status", A, {"state": "dnd"})
        with connect() as c, c:
            n_exc = push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "dg-x"})
            n_oth = push.enqueue(c, "ps-a", "chat.message", actor="ps-adm", data={"chat": "dg-y"})
            c.execute("DELETE FROM push_queue WHERE uid='ps-a'")
        ok(f"Nicht stoeren mit Ausnahme: Ausnahme kommt durch, andere nicht ({n_exc}, {n_oth})", n_exc >= 1 and n_oth == 0)
        req("PUT", "/api/account/status/prefs", A, {"exceptions": []})

        # kill -9 mitten im Beenden (vor dem Commit) -> nichts; danach genau einmal
        rc = worker("rollback")
        with connect() as c:
            st = c.execute("SELECT state FROM user_status WHERE uid='ps-a'").fetchone()[0]
            qd = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a' AND kind='status.digest'").fetchone()[0]
        h = held("ps-a") or {}
        ok(f"kill -9 vor dem Commit: Nicht stoeren bleibt, Zaehler bleibt, keine Sammelmeldung (rc {rc}, {st}, {h}, {qd})",
           rc == -9 and st == "dnd" and h.get("m") == 1 and not h.get("o") and not h.get("c") and qd == 0)
        m3 = mark()
        rc = worker("commit")
        dg = wait_digests(UAS, m3)
        ok(f"kill -9 direkt nach dem Commit: genau eine Sammelmeldung je Abo ({rc}, {[len(x) for x in dg.values()]}, "
           f"{dg['/wp/a1'] and dg['/wp/a1'][0]['body']})",
           rc == -9 and all(len(x) == 1 for x in dg.values()) and dg["/wp/a1"][0]["body"] == "Während „Nicht stören“: 1 Nachricht")
        m4 = mark()
        req("PUT", "/api/account/status", A, {"state": "available"})
        ok("... danach (Neustart, weiterer Wechsel) keine zweite", none_got(is_dig, m4, 3) and held("ps-a") is None)

        # Ablauf (until <= jetzt): Schleife des Servers und ein zweiter Prozess gleichzeitig -> genau einmal
        from saga import user_status
        req("PUT", "/api/account/status", A, {"state": "dnd", "dur": 600})
        with connect() as c, c:
            push.enqueue(c, "ps-a", "comment.reply", actor="ps-b", ref=nn)
        with connect() as c, c:
            c.execute("UPDATE user_status SET until=? WHERE uid='ps-a'", (time.time() - 1,))
        m5 = mark()
        n_exp = user_status.expire_once()
        dg = wait_digests(UAS, m5)
        ok(f"Ablauf von Nicht stoeren: genau eine Sammelmeldung je Abo ({n_exp}, {[len(x) for x in dg.values()]}, "
           f"{dg['/wp/a1'] and dg['/wp/a1'][0]['body']})",
           all(len(x) == 1 for x in dg.values()) and dg["/wp/a1"][0]["body"] == "Während „Nicht stören“: 1 weitere Mitteilung")

        # Ruhezeit: keine Sammelmeldung, nichts gezaehlt; quiet_dnd aus: alte Regel
        fr, to = time.strftime("%H:%M", time.gmtime(time.time() - 3600)), time.strftime("%H:%M", time.gmtime(time.time() + 3600))
        req("PUT", "/api/push/settings", A, {"quiet": {"on": True, "from": fr, "to": to, "tz": "UTC"}})
        with connect() as c, c:
            n_q = push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "dg-q"})
        ok(f"Ruhezeit: keine Mitteilung, nicht fuer eine Sammelmeldung gezaehlt ({n_q}, {held('ps-a')})", n_q == 0 and held("ps-a") is None)
        m6 = mark()
        req("PUT", "/api/account/status", A, {"state": "dnd"})
        with connect() as c, c:
            push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "dg-q"})
        req("PUT", "/api/account/status", A, {"state": "available"})
        ok("Nicht stoeren endet in der Ruhezeit: keine Mitteilung (nur Live), Zaehler weg", none_got(is_dig, m6, 3) and held("ps-a") is None)
        req("PUT", "/api/account/status/prefs", A, {"quiet_dnd": False, "exceptions": [{"type": "user", "ref": auth_ref("ps-b")}]})
        with connect() as c, c:
            n_plain = push.enqueue(c, "ps-a", "chat.message", actor="ps-b", data={"chat": "dg-p"})
        ok(f"quiet_dnd aus: Ruhezeit wie bisher (keine Mitteilung, auch nicht fuer Ausnahmen) ({n_plain})", n_plain == 0)
        req("PUT", "/api/account/status/prefs", A, {"quiet_dnd": True, "exceptions": []})
        req("PUT", "/api/push/settings", A, {"quiet": {"on": False}})
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM chat WHERE id=?", (chat,))
            c.execute("DELETE FROM push_queue WHERE uid='ps-a' AND kind='status.digest'")
        req("PUT", "/api/account/status", A, {"state": "available"})
        for d in (d1, d2):
            if isinstance(d, dict) and d.get("id"):
                req("DELETE", f"/api/push/subscriptions/{d['id']}", A)


def deadlock_part(A, Bk):
    """Bugs-DB 2364: zwei gleichzeitige Sender (verschiedene Chats) mit zwei gemeinsamen Empfaengern in Nicht stoeren, in
    gegenlaeufiger Reihenfolge - frueher sperrten beide user_kv(uid, 'status_held') kreuzweise (Verklemmung). Jetzt: kein
    Fehler, Zaehler stimmen."""
    for k in (A, Bk):
        req("PUT", "/api/account/status", k, {"state": "dnd"})
    errs, go = [], threading.Barrier(2)

    def sender(order, chat):
        try:
            with connect() as c, c:
                for i, u in enumerate(order):
                    push.enqueue(c, u, "chat.message", actor="ps-adm", data={"chat": chat})
                    if i == 0:
                        go.wait(5)                # beide haben den ersten Empfaenger gezaehlt
                        time.sleep(0.4)
        except Exception as ex:  # noqa: BLE001
            errs.append(f"{type(ex).__name__}: {ex}")

    try:
        ths = [threading.Thread(target=sender, args=(("ps-a", "ps-b"), "dl-1")),
               threading.Thread(target=sender, args=(("ps-b", "ps-a"), "dl-2"))]
        [x.start() for x in ths]
        [x.join(30) for x in ths]
        ha, hb = held("ps-a") or {}, held("ps-b") or {}
        ok(f"zwei Sender gleichzeitig, gegenlaeufige Reihenfolge: keine Verklemmung ({errs})", not errs and not any(x.is_alive() for x in ths))
        ok(f"... Zaehler stimmen (je 2 Nachrichten) ({ha.get('m')}, {hb.get('m')})", ha.get("m") == 2 and hb.get("m") == 2)
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM user_kv WHERE uid IN ('ps-a', 'ps-b') AND k='status_held'")
        for k in (A, Bk):
            req("PUT", "/api/account/status", k, {"state": "available"})


def flush_err_part(A):
    """Pruefer (Bugs-DB 2364): die Zaehler schreibt db.Conn.before_commit ausserhalb des Savepoints von bus.fire - ein Fehler
    dabei darf nur den Zaehler kosten, nie die eigentliche Aktion derselben Transaktion (BUG-009)."""
    req("PUT", "/api/account/status", A, {"state": "dnd"})
    orig = push._held_write

    def boom(*_a, **_k):
        raise RuntimeError("Pruefer: Zaehler kaputt")

    push._held_write = boom
    err = None
    try:
        try:
            with connect() as c, c:
                push.enqueue(c, "ps-a", "chat.message", actor="ps-adm", data={"chat": "fe-1"})
                c.execute("INSERT INTO user_kv (uid, k, v) VALUES ('ps-a', 'pruefer_fe', '1') ON CONFLICT (uid, k) DO UPDATE SET v='1'")
        except Exception as ex:  # noqa: BLE001
            err = f"{type(ex).__name__}: {ex}"
        push._held_write = orig
        with connect() as c:
            kept = c.execute("SELECT 1 FROM user_kv WHERE uid='ps-a' AND k='pruefer_fe'").fetchone() is not None
        ok(f"Fehler beim Zaehlen vor dem Commit: Aktion trotzdem gespeichert, nur kein Zaehler ({err}, {kept}, {held('ps-a')})",
           err is None and kept and held("ps-a") is None)
    finally:
        push._held_write = orig
        with connect() as c, c:
            c.execute("DELETE FROM user_kv WHERE uid='ps-a' AND k IN ('pruefer_fe', 'status_held')")
        req("PUT", "/api/account/status", A, {"state": "available"})


def knock_part(A, ua):
    """2029: Anklopfen - Glocke ohne Push ueber data.hold in enqueue (kein Auftrag, kein Kontingent, nicht gezaehlt);
    Sammel-Push calls.knock_summary waehrend noch aktivem Nicht stoeren: kein Push, die Versuche zaehlen genau einmal fuer
    die Sammelmeldung."""
    from saga import calls, notify
    req("PUT", "/api/account/status", A, {"state": "dnd"})
    t0 = time.time()
    with connect() as c, c:
        chat = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', 'ps-b', ?) RETURNING id", (t0,)).fetchone()[0]
    try:
        with connect() as c, c:
            for i in range(3):
                notify.create(c, "ps-a", "call.missed", "ps-b",
                              data={"chat": chat, "call": secrets.token_hex(16), "hold": "knock" if i == 0 else "again"})
        with connect() as c:
            q = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a' AND kind='call.missed'").fetchone()[0]
            bells = c.execute("SELECT COUNT(*) FROM notification WHERE uid='ps-a' AND kind='call.missed' AND created>=?", (t0,)).fetchone()[0]
        ok(f"Anklopfen: drei Glocken, kein Auftrag, nichts gezaehlt ({bells}, {q}, {held('ps-a')})", bells == 3 and q == 0 and held("ps-a") is None)
        with connect() as c, c:
            n1 = calls.knock_summary(c, "ps-a")
        with connect() as c:
            q = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='ps-a' AND kind='call.missed'").fetchone()[0]
        h = held("ps-a") or {}
        ok(f"Sammel-Push waehrend Nicht stoeren: kein Auftrag, die 3 Versuche einmal gezaehlt ({n1}, {q}, {h})",
           n1 == 1 and q == 0 and h.get("c") == 3)
        with connect() as c, c:
            n2 = calls.knock_summary(c, "ps-a")
        ok(f"... zweiter Lauf: nichts mehr, nicht noch einmal gezaehlt ({n2}, {held('ps-a')})", n2 == 0 and (held("ps-a") or {}).get("c") == 3)
        m = mark()
        req("PUT", "/api/account/status", A, {"state": "available"})
        dg = wait_digests({"/wp/a1": ua}, m)["/wp/a1"]
        ok(f"... Sammelmeldung mit genau 3 verpassten Anrufen ({dg and dg[0]['body']})",
           len(dg) == 1 and dg[0]["body"] == "Während „Nicht stören“: 3 verpasste Anrufe")
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM notification WHERE uid='ps-a' AND kind='call.missed' AND created>=?", (t0,))
            c.execute("DELETE FROM chat WHERE id=?", (chat,))
            c.execute("DELETE FROM user_kv WHERE uid='ps-a' AND k='status_held'")
        req("PUT", "/api/account/status", A, {"state": "available"})


def auth_ref(uid):
    from saga.core import local_ref
    return local_ref(uid)


def sub_req(body, hdr):
    """Anmelden mit Geduld: REG_MIN (15 je Minute und Konto) ist nach den Tests oben fast aufgebraucht."""
    for _ in range(8):
        s, d = req("POST", "/api/push/subscriptions", None, body, hdr=hdr)
        if s != 429:
            return s, d
        time.sleep(10)
    return s, d


def T_admin(path, form=None):
    return admin(path, form)


def e2e_part(A, Bk, pgA, tok2, key):
    """2.6 (Bugs-DB 2262-4/-5): APNs-Abo mit Schluesseln/Bundle, Geraeteliste via/e2e, /api/push, Domain-Nachweis."""
    from saga import push_relay
    # Bestand nach dem Update: Setting push ohne apns_e2e_direct -> Standard AUS (Koordinator: bis zum Geraetetest)
    pv0 = {k: v for k, v in auth.settings(fresh=True)["push"].items() if k != "apns_e2e_direct"}
    auth.save_setting("push", pv0)
    time.sleep(WAIT)                                              # Zwischenspeicher der Einstellungen im Server
    s, html = T_admin("/settings/push")
    ok("2262-5: ohne Setting ist 'Ende-zu-Ende verschluesseln' auf der Admin-Seite aus",
       s == 200 and 'name="apns_e2e_direct" value="1">' in html and 'name="apns_e2e_direct" value="1" checked' not in html)
    t0e = "e0" * 32
    app0 = UA()
    s, d = sub_req({"type": "apns", "token": t0e, "env": "sandbox", "name": "iPhone Bestand",
                    "keys": app0.keys(), "topic": "org.example.saga"}, hdr={"Authorization": "Bearer " + tok2})
    ok(f"2262-5: Standard ohne Setting -> Abo mit Schluesseln direkt, aber e2e false ({d.get('device')})",
       s == 201 and d["device"]["via"] == "direct" and d["device"]["e2e"] is False and push.e2e_direct() is False)
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c6", "body": "Kommentar im Bestand"})
    g = wait_got(path_is(f"/3/device/{t0e}"), m)
    b0 = json.loads(g[3]) if g else {}
    ok("2262-5: Standard ohne Setting -> eigener Weg Klartext wie bisher (kein saga, alert mit Text, thread-id)",
       g and "saga" not in b0 and b0.get("aps", {}).get("alert", {}).get("body") and b0.get("aps", {}).get("thread-id"))
    with connect() as c, c:
        c.execute("DELETE FROM push_sub WHERE endpoint=?", (f"apns:sandbox:{t0e}",))
    auth.save_setting("push", dict(pv0, apns_e2e_direct=True))   # ab hier E2E auf dem eigenen Weg eingeschaltet
    time.sleep(WAIT)
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k IN ('push_relay', 'push_relay_state', 'push_relay_old')")
    push_relay.forget_key_cache()
    s, d = req("GET", "/.well-known/saga-push-relay")
    ok(f"2262-4: /.well-known/saga-push-relay ohne Schluessel -> 404 ({s})", s == 404)
    _priv, pub = push_relay.key()
    s, d = req("GET", "/.well-known/saga-push-relay")
    ok(f"2262-4: /.well-known/saga-push-relay -> {{v:1, key}} (nur oeffentlicher Schluessel) ({s})",
       s == 200 and d == {"v": 1, "key": push.b64u(pub)})
    s, ver = req("GET", "/api/version")
    ok("2262-4: FEATURES pushrelay1", "pushrelay1" in (ver.get("features") or []))
    app = UA()
    bearer = {"Authorization": "Bearer " + tok2}
    t3 = "e3" * 32
    s, d = sub_req({"type": "apns", "token": t3, "env": "sandbox", "name": "iPhone E2E",
                                                         "keys": app.keys(), "topic": "org.example.saga"}, hdr=bearer)
    with connect() as c:
        row = c.execute("SELECT p256dh, auth, topic FROM push_sub WHERE endpoint=?", (f"apns:sandbox:{t3}",)).fetchone()
    ok(f"2262-4: APNs-Abo mit keys/topic gespeichert, Weg direkt, e2e ({s}, {d.get('device')})",
       s == 201 and row == (app.keys()["p256dh"], app.keys()["auth"], "org.example.saga") and d["active"]
       and d["device"]["via"] == "direct" and d["device"]["e2e"] is True)
    s, d = sub_req({"type": "apns", "token": t3, "env": "sandbox", "name": "iPhone E2E"}, hdr=bearer)
    with connect() as c:
        row2 = c.execute("SELECT p256dh, auth, topic FROM push_sub WHERE endpoint=?", (f"apns:sandbox:{t3}",)).fetchone()
    ok(f"2262-4: Erneuern ohne keys/topic -> Werte bleiben ({s})", s == 200 and row2 == row)
    app2 = UA()
    s, d = sub_req({"type": "apns", "token": t3, "env": "sandbox", "keys": app2.keys()}, hdr=bearer)
    with connect() as c:
        row3 = c.execute("SELECT p256dh, auth, topic FROM push_sub WHERE endpoint=?", (f"apns:sandbox:{t3}",)).fetchone()
    ok("2262-4: Schluesselwechsel (gleiches Token) -> neue Schluessel, gleiches Abo, Bundle bleibt",
       s == 200 and row3 == (app2.keys()["p256dh"], app2.keys()["auth"], "org.example.saga"))
    bad = [sub_req(dict({"type": "apns", "token": "e4" * 32}, **x), hdr=bearer)[0]
           for x in ({"keys": {"p256dh": "AAAA", "auth": "BBBB"}}, {"keys": {"p256dh": app.keys()["p256dh"]}}, {"topic": "x"},
                     {"topic": "org/example"}, {"topic": ["org.example.saga"]}, {"topic": "a" * 201})]
    ok(f"2262-4: kaputte keys / topic -> 400 ({bad})", bad == [400] * 6)
    t4 = "e5" * 32
    s, d = sub_req({"type": "apns-voip", "token": t4, "env": "sandbox", "keys": app2.keys(),
                                                         "topic": "org.example.fremd"}, hdr=bearer)
    ok(f"2262-4: fremdes Bundle ohne Relais -> angelegt, aber kein Weg (via null, inaktiv) ({d.get('device')})",
       s == 201 and d["device"]["via"] is None and not d["active"] and d["device"]["hint"] == "not_configured")
    s, st = req("GET", "/api/push", A)
    ok(f"2262-4: /api/push apns wie bisher, apns_e2e 1, apns_topics, apns_relay null ({st['server'].get('apns_topics')})",
       st["server"]["apns"] is True and st["server"]["apns_e2e"] == 1 and st["server"]["apns_topics"] == ["org.example.saga"]
       and st["server"]["apns_relay"] is None)
    dv = [x for x in st["devices"] if x["type"] in ("apns", "apns-voip")]
    ok("2262-4: devices[]: altes Abo ohne Schluessel direkt ohne e2e", any(x["via"] == "direct" and x["e2e"] is False for x in dv))
    with connect() as c, c:
        c.execute("DELETE FROM push_sub WHERE endpoint=?", (f"apns-voip:sandbox:{t4}",))

    # --- 2262-5: eigener APNs-Weg mit Schluesseln -> nur Chiffrat --------------------------------------------------------
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c4", "body": "Geheimer Kanarienvogel im Kommentar"})
    g = wait_got(path_is(f"/3/device/{t3}"), m)
    gp = wait_got(path_is(f"/3/device/{'a1' * 32}"), m)
    h = g[2] if g else {}
    body = json.loads(g[3]) if g else {}
    inner = app2.decrypt(push.b64u_d(body["saga"]["c"])) if body.get("saga") else {}
    plain = json.loads(gp[3]) if gp else {}
    raw = g[3].decode().replace(body["saga"]["c"], "") if g and body.get("saga") else ""      # Klartext ohne das Chiffrat
    ok(f"2262-5: APNs-Koerper nur {{aps, saga}}, sichtbar nur Loc-Keys ({body.get('aps')})",
       set(body) == {"aps", "saga"} and body["aps"] == {"alert": {"title-loc-key": "SAGA_PUSH_TITLE", "loc-key": "SAGA_PUSH_BODY"},
                                                        "mutable-content": 1, "sound": "default"}
       and body["saga"]["e2e"] == 1 and body["saga"]["k"] == push.e2e_kid(app2.keys()["p256dh"]))
    ok("2262-5: kein Name, kein Kanarienvogel, kein tag/thread im Klartext",
       raw and not any(x in raw for x in ("Bert", "Kanarienvogel", "kommentiert", "thread-", "note-", f"#{pgA}")))
    ok(f"2262-5: entschluesselt = Feld-Set wie Klartext-Weg ({inner.get('body')})",
       inner.get("kind") == "comment.new" and inner.get("body") == plain.get("aps", {}).get("alert", {}).get("body")
       and inner.get("url") == plain.get("url") and inner.get("v") == 2 and inner.get("tag"))
    ok(f"2262-5: Koepfe alert/10, Topic Bundle, collapse-id undurchsichtig = HMAC(auth, tag) ({h.get('apns-collapse-id')})",
       h.get("apns-push-type") == "alert" and h.get("apns-priority") == "10" and h.get("apns-topic") == "org.example.saga"
       and h.get("apns-collapse-id") == push.e2e_collapse(app2.keys()["auth"], inner.get("tag", "")) and plain.get("aps", {}).get("thread-id")
       and int(h.get("apns-expiration", 0)) > time.time() + 3600)
    pv = auth.settings(fresh=True)["push"]
    auth.save_setting("push", dict(pv, apns_e2e_direct=False))
    time.sleep(WAIT)
    m = mark()
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "c5", "body": "zweiter Kommentar"})
    g = wait_got(path_is(f"/3/device/{t3}"), m)
    b2 = json.loads(g[3]) if g else {}
    ok("2262-5: Schalter push.apns_e2e_direct aus -> eigener Weg wieder Klartext", "saga" not in b2 and b2.get("aps", {}).get("alert", {}).get("body"))
    auth.save_setting("push", pv)
    s, html = T_admin("/settings/push")
    ok(f"2262-6: Admin-Seite zeigt Bereich Push-Relais (ohne Adresse: keines festgelegt), Schalter Relais/E2E ({s})",
       s == 200 and 'id="relais"' in html and "SAGA_PUSH_RELAY_URL" in html and 'name="relay"' in html and 'name="apns_e2e_direct"' in html)
    s, html = T_admin("/settings/push", {"enabled": "1", "webpush": "1", "ntfy": "1", "unifiedpush": "1", "stream": "1", "apns_e2e_direct": "1",
                                         "apns_enabled": "1", "team_id": "ABCDE12345", "key_id": "KEY1234567", "bundle_id": "org.example.saga"})
    p2 = auth.settings(fresh=True)["push"]
    ok(f"2262-6: Speichern ohne Haken Relais -> relay aus, E2E an ({p2.get('relay')}, {p2.get('apns_e2e_direct')})",
       p2.get("relay") is False and p2.get("apns_e2e_direct") is True)
    s, html = T_admin("/settings/push", {"action": "relay_check"})
    ok("2262-6: 'Jetzt pruefen' ohne Relais-Adresse -> Hinweis, nichts gespeichert", "Kein Push-Relais festgelegt." in html
       and auth.settings(fresh=True)["push"].get("relay") is False)
    s, html = T_admin("/settings/push", {"enabled": "1", "webpush": "1", "ntfy": "1", "unifiedpush": "1", "stream": "1", "relay": "1",
                                         "apns_enabled": "1", "team_id": "ABCDE12345", "key_id": "KEY1234567", "bundle_id": "org.example.saga"})
    p3 = auth.settings(fresh=True)["push"]
    ok(f"2262-6: Speichern ohne Haken E2E -> apns_e2e_direct aus, relay an ({p3.get('relay')}, {p3.get('apns_e2e_direct')})",
       p3.get("relay") is True and p3.get("apns_e2e_direct") is False)
    auth.save_setting("push", pv)
    time.sleep(WAIT)
    return t3, app2


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
    # 2.4 (Videos im Chat): fetch-Handler nur fuer /_m/ und (2.5, Offline-Seite) Seiten-Navigationen - alles andere geht
    # unveraendert ans Netz (Static Routing)
    ok(f"/sw.js: 200, JavaScript, Service-Worker-Allowed /, fetch-Handler nur fuer /_m/ und Navigationen ({s})",
       s == 200 and "javascript" in r.getheader("content-type", "") and r.getheader("service-worker-allowed") == "/"
       and "showNotification" in body and body.count("'fetch'") == 1 and "u.pathname.indexOf('/_m/') !== 0" in body
       and "requestMode: 'navigate'" in body and "/static/offline.html" in body
       and "source: 'network'" in body and "__SAGA_V__" not in body)
    s, _, r = req("GET", "/", A, raw_resp=True)
    ok("CSP der Seiten erlaubt Worker nur von 'self'", "worker-src 'self'" in (r.getheader("content-security-policy") or ""))
    ok("GET /api/push ohne Anmeldung -> 401", req("GET", "/api/push")[0] == 401)
    s, st = req("GET", "/api/push", A)
    ok(f"GET /api/push: Server bietet Web Push (VAPID wie in der Datenbank), ntfy, UnifiedPush; APNs aus ({s})",
       s == 200 and st["server"]["webpush"] and st["server"]["vapid_public"] == vpub and st["server"]["ntfy"]
       and st["server"]["unifiedpush"] and not st["server"]["apns"] and st["devices"] == [])
    ok("Standard: alle Bereiche an, Namen an, keine Ruhezeit",
       all(st["settings"]["cats"].values()) and set(st["categories"]) == {"share", "comment", "chat", "publish", "call", "assign", "tasks"}
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

    # --- 2.6 (Bugs-DB 2029-3): Push-Weiche und Sammelmeldung -------------------------------------------------------
    digest_part(A, Bk, ua, pgA, nn)
    deadlock_part(A, Bk)
    flush_err_part(A)
    knock_part(A, ua)

    # --- Abmelden im Browser, "ueberall abmelden" ---------------------------------------------------------------
    ok("Anmelde-Antwort setzt die Sitzung (Abo gehoert zu diesem Browser)", bool(web_cookie))
    s, _, r = req("POST", "/logout", None, hdr={"Cookie": "saga=" + web_cookie, "X-Saga": "1"}, raw_resp=True)
    ok(f"Abmelden in diesem Browser -> Browser-Abo weg, ntfy bleibt ({s})",
       s == 200 and sid not in [x[0] for x in sub_rows("ps-a")] and nid in [x[0] for x in sub_rows("ps-a")])
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
    e2e_part(A, Bk, pgA, tok2, key)

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
    if sys.argv[1:2] == ["--worker"]:
        worker_main(sys.argv[2])
        sys.exit(3)
    try:
        main()
    except Exception:
        cleanup()
        raise
