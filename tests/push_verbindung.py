"""Eigene Push-Verbindung der Apps (2.5, saga.push_stream): WebSocket /api/push/stream als weiteres Push-Ziel.

Geprueft: Anmeldung (Geraete-Schluessel, falsch/fehlend -> 4401, ohne Abo -> 4404), Abo "stream" (nur mit
Geraete-Anmeldung, Endpunkt vom Server, Geraeteliste), Lebenszeichen 0x03 -> 0x04, der Server sendet im Leerlauf NICHTS
(kein eigener Ping, gemessen auf der rohen Verbindung), Zustellung sofort (gleiche RFC-8291-Nutzlast wie Web Push, mit
Namen), Bestaetigung loescht, Warteschlange bei Trennung (Nachholen beim Aufbau, unbestaetigt -> noch einmal), Topic
ersetzt, abgelaufene (TTL) nicht, neuere Verbindung desselben Geraets -> 4409, Abo entfernt -> 4404, Geraet abgemeldet
-> 4401, Weg vom Admin abgeschaltet -> 4404/403, UnifiedPush-Abo desselben Geraets geht vor, Bremse beim Aufbau (4429),
Isolation (fremdes Abo), keine Texte im Server-Log. Wegwerf-Konten pv-*; raeumt auf."""
import base64, http.client, json, os, secrets, socket, struct, sys, time, urllib.parse
from itsdangerous import TimestampSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from websockets.sync.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
import auth
from db import connect
from saga.core import _tok_hash, set_user_lang

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
WS = f"ws://{B_.hostname}:{B_.port}/api/push/stream"
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
PW = "Push-Pass-1234"
P256 = ec.SECP256R1()
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None):
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
    return r.status, data


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class UA:
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
        pt = AESGCM(cek).decrypt(nonce, ct, None)
        assert pt.endswith(b"\x02") and rs == 4096
        return json.loads(pt[:-1])


def device(uid, name):
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?,?,?,?,?,?)",
                        (uid, uid, name, _tok_hash(tok), tok[:8], time.time())).lastrowid
    return did, {"Authorization": "Bearer " + tok}


def wsc(hdr=None):
    return ws_connect(WS, additional_headers=hdr or {}, open_timeout=10, max_size=65536)


def closed_code(w, timeout=6.0):
    """Wartet auf das Schliessen -> Code (None = offen geblieben)."""
    t0 = time.time()
    try:
        while time.time() - t0 < timeout:
            w.recv(timeout=timeout)
    except TimeoutError:
        return None
    except ConnectionClosed as ex:
        return ex.rcvd.code if ex.rcvd else -1
    return None


def recv_msg(w, timeout=8.0):
    """-> (kennung, koerper) einer Mitteilung (0x01) oder None."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            b = w.recv(timeout=max(0.1, timeout - (time.time() - t0)))
        except (TimeoutError, ConnectionClosed):
            return None
        if isinstance(b, bytes) and b and b[0] == 1:
            return struct.unpack("!Q", b[1:9])[0], b[9:]
    return None


def ack(w, *mids):
    w.send(bytes([2]) + b"".join(struct.pack("!Q", m) for m in mids))


def rows(sid):
    with connect() as c:
        return c.execute("SELECT id, topic, expires FROM push_stream_msg WHERE sub_id=? ORDER BY id", (sid,)).fetchall()


def raw_ws(hdr):
    """Rohe WebSocket-Verbindung (ohne Bibliothek): sieht JEDES Byte vom Server, auch Ping-Rahmen."""
    s = socket.create_connection((B_.hostname, B_.port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    h = "".join(f"{k}: {v}\r\n" for k, v in hdr.items())
    s.sendall(f"GET /api/push/stream HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n{h}\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    head, rest = buf.split(b"\r\n\r\n", 1)
    return s, head, rest


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'pv-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "device_login", "push_sub", "push_queue"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'pv-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'pv-%' OR target LIKE 'pv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'pv-%'")


def main():
    cleanup()
    auth.create_user("pv-a", PW, "Anna Strom")
    auth.create_user("pv-b", PW, "Bert Strom")
    set_user_lang("pv-a", "de"); set_user_lang("pv-b", "de")
    prev = auth.settings(fresh=True)["push"]
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True})
    time.sleep(5.6)                              # Einstellungen werden zwischengespeichert
    try:
        run(ck("pv-a"), ck("pv-b"))
    finally:
        auth.save_setting("push", prev)
        cleanup()
    print(f"\nPUSH-VERBINDUNG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(A, Bk):
    t_start = time.time()
    st, info = req("GET", "/api/push", A)
    ok(f"/api/push: Weg 'stream' angeboten, Pfad genannt ({info.get('server', {}).get('stream_path')})",
       st == 200 and info["server"]["stream"] is True and info["server"]["stream_path"] == "/api/push/stream")

    # --- Anmeldung ---------------------------------------------------------------------------------------
    with wsc() as w:
        ok("ohne Geraete-Schluessel -> 4401", closed_code(w) == 4401)
    with wsc({"Authorization": "Bearer hkd_falsch"}) as w:
        ok("falscher Geraete-Schluessel -> 4401", closed_code(w) == 4401)
    did, BEAR = device("pv-a", "Pixel")
    with wsc(BEAR) as w:
        ok("Geraet ohne Abo 'stream' -> 4404", closed_code(w) == 4404)

    ua = UA()
    s0, d0 = req("POST", "/api/push/subscriptions", A, {"type": "stream", "keys": ua.keys(), "name": "Browser"})
    ok(f"Abo 'stream' nur fuer Apps mit Geraete-Anmeldung ({s0})", s0 == 400)
    s0, _ = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": {"p256dh": "AAAA", "auth": "BBBB"}}, hdr=BEAR)
    ok(f"Abo 'stream' ohne gueltige Schluessel -> 400 ({s0})", s0 == 400)
    s1, d1 = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua.keys(), "name": "Pixel"}, hdr=BEAR)
    sid = d1.get("id")
    dev = d1.get("device") or {}
    ok(f"Abo 'stream' angelegt ({s1}, {dev.get('where')}, verschluesselt, dieses Geraet, Pfad)",
       s1 == 201 and dev.get("type") == "stream" and dev.get("encrypted") and dev.get("this_device") and dev.get("where") == "Saga (eigene Verbindung)"
       and d1.get("stream_path") == "/api/push/stream")
    with connect() as c:
        ep = c.execute("SELECT endpoint, device_id FROM push_sub WHERE id=?", (sid,)).fetchone()
    ok(f"Endpunkt vergibt der Server, haengt an der Geraete-Anmeldung ({ep[0]})", ep == (f"stream:{did}", did))
    s2, d2 = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua.keys(), "name": "Pixel"}, hdr=BEAR)
    ok(f"zweites Mal: dasselbe Abo (200, gleiche id) ({s2})", s2 == 200 and d2.get("id") == sid)

    # --- Leerlauf: der Server sendet nichts (kein eigener Ping) - rohe Verbindung eines zweiten Geraets -----
    didB, BEARB = device("pv-b", "Tablet")
    ub = UA()
    sB = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ub.keys(), "name": "Tablet"}, hdr=BEARB)[1]["id"]
    raw, head, rest = raw_ws(BEARB)
    raw.setblocking(False)
    ok(f"rohe Verbindung: 101 Switching Protocols ({head.split(b'\r\n')[0].decode()})", head.startswith(b"HTTP/1.1 101"))
    raw_t0 = time.time()

    # --- Lebenszeichen, Zustellung, Bestaetigung ------------------------------------------------------------
    with wsc(BEAR) as w:
        t0 = time.time()
        w.send(bytes([3]))
        pong = w.recv(timeout=5)
        ok(f"Ping 0x03 -> 0x04 ({(time.time() - t0) * 1000:.0f} ms)", pong == bytes([4]))
        w.send("hallo")                          # Text: still uebergehen
        w.send(bytes([3]))
        ok("unbekannter Text-Rahmen wird uebergangen, Verbindung bleibt", w.recv(timeout=5) == bytes([4]))
        pg = req("POST", "/api/notes", Bk, {"title": "Geheimer Titel QQQ"})[1]["id"]
        t0 = time.time()
        req("PUT", f"/api/notes/{pg}/shares", Bk, {"type": "user", "target": "pv-a", "role": "comment"})
        m = recv_msg(w, 8)
        lat = time.time() - t0
        msg = ua.decrypt(m[1]) if m else {}
        ok(f"Teilen -> Mitteilung sofort ueber die Verbindung ({lat:.2f} s): {msg.get('body')}",
           m and msg.get("body", "").startswith("Bert Strom hat eine Seite mit dir geteilt") and "QQQ" not in json.dumps(msg) and lat < 6)
        ok("... liegt bis zur Bestaetigung in der Ablage", [r[0] for r in rows(sid)] == [m[0]] if m else False)
        ack(w, m[0] if m else 0)
        w.send(bytes([3])); w.recv(timeout=5)
        time.sleep(0.3)
        ok("Bestaetigung (0x02 + Kennung) loescht sie", rows(sid) == [])
        with connect() as c:
            lo = c.execute("SELECT last_ok FROM push_sub WHERE id=?", (sid,)).fetchone()[0]
        ok("... und vermerkt 'zuletzt zugestellt' am Abo", lo and lo > t0 - 1)
        dv = [d for d in req("GET", "/api/push", None, hdr=BEAR)[1]["devices"] if d["id"] == sid]
        ok("Geraeteliste: verbunden = true", dv and dv[0].get("connected") is True)
        # Testmitteilung (sofort, ohne Warteschlange) kommt auch an
        r = req("POST", f"/api/push/subscriptions/{sid}/test", None, hdr=BEAR)
        m = recv_msg(w, 6)
        ok(f"Testmitteilung ueber die Verbindung ({r[1].get('result')})", r[0] == 200 and m and ua.decrypt(m[1]).get("kind") == "test")
        ack(w, m[0] if m else 0)
    time.sleep(0.5)
    dv = [d for d in req("GET", "/api/push", None, hdr=BEAR)[1]["devices"] if d["id"] == sid]
    ok("Geraeteliste nach dem Trennen: verbunden = false", dv and dv[0].get("connected") is False)

    # --- Warteschlange: getrennt -> ablegen, beim Aufbau nachholen ---------------------------------------
    pgA = req("POST", "/api/notes", A, {"title": "Annas Seite QQQ"})[1]["id"]
    req("PUT", f"/api/notes/{pgA}/shares", A, {"type": "user", "target": "pv-b", "role": "comment"})
    req("POST", f"/api/notes/{pgA}/comments", Bk, {"block_id": "b1", "body": "Kommentar-Inhalt ZZZ"})
    t0 = time.time()
    while time.time() - t0 < 8 and not rows(sid):
        time.sleep(0.2)
    pend = rows(sid)
    ok(f"getrennt: Mitteilung wird abgelegt ({len(pend)})", len(pend) == 1)
    ok("... mit Ablauf nach TTL (4 h wie Web Push)", pend and 4 * 3600 - 60 < pend[0][2] - time.time() <= 4 * 3600)
    with wsc(BEAR) as w:
        m = recv_msg(w, 5)
        ok(f"beim Aufbau nachgeholt ({ua.decrypt(m[1]).get('body') if m else None})",
           m and m[0] == pend[0][0] and "kommentiert" in ua.decrypt(m[1]).get("body", "") and "ZZZ" not in json.dumps(ua.decrypt(m[1])))
        # NICHT bestaetigen
    with wsc(BEAR) as w:
        m2 = recv_msg(w, 5)
        ok("unbestaetigt -> beim naechsten Aufbau noch einmal (gleiche Kennung)", m and m2 and m2[0] == m[0])
        ack(w, m2[0] if m2 else 0)
        w.send(bytes([3])); w.recv(timeout=5)
    time.sleep(0.3)
    ok("... nach Bestaetigung leer", rows(sid) == [])

    # Topic ersetzt eine noch nicht abgeholte gleiche (zwei Testmitteilungen, Abstand > 5 s)
    req("POST", f"/api/push/subscriptions/{sid}/test", None, hdr=BEAR)
    time.sleep(5.2)
    req("POST", f"/api/push/subscriptions/{sid}/test", None, hdr=BEAR)
    pend = rows(sid)
    ok(f"Topic ersetzt die noch nicht abgeholte gleiche ({len(pend)} abgelegt, Topic {pend[0][1] if pend else None})",
       len(pend) == 1 and pend[0][1] == "test")
    # abgelaufen (TTL) -> nicht mehr zustellen
    with connect() as c, c:
        c.execute("UPDATE push_stream_msg SET expires=? WHERE sub_id=?", (time.time() - 1, sid))
    with wsc(BEAR) as w:
        ok("abgelaufene Mitteilung wird nicht nachgeholt", recv_msg(w, 2.5) is None)
    from saga import push_stream
    push_stream.cleanup()
    ok("Aufraeumen entfernt Abgelaufene", rows(sid) == [])

    # --- Isolation: Kennung eines fremden Abos bestaetigen loescht nichts --------------------------------
    req("POST", f"/api/push/subscriptions/{sB}/test", None, hdr=BEARB)
    rb = rows(sB)
    with wsc(BEAR) as w:
        ack(w, *[r[0] for r in rb])
        w.send(bytes([3])); w.recv(timeout=5)
    time.sleep(0.3)
    ok("fremde Kennung bestaetigen loescht nichts (Isolation)", rb and rows(sB) == rb)

    # --- zwei Verbindungen desselben Geraets: die neuere uebernimmt --------------------------------------
    w1 = wsc(BEAR)
    w1.send(bytes([3])); w1.recv(timeout=5)
    w2 = wsc(BEAR)
    ok("neuere Verbindung desselben Geraets -> die alte bekommt 4409", closed_code(w1) == 4409)
    w2.send(bytes([3]))
    ok("... die neue bleibt", w2.recv(timeout=5) == bytes([4]))
    # Abo entfernt (Geraeteliste) -> 4404
    req("DELETE", f"/api/push/subscriptions/{sid}", A)
    ok("Abo entfernt -> Verbindung schliesst mit 4404", closed_code(w2) == 4404)
    sid = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua.keys(), "name": "Pixel"}, hdr=BEAR)[1]["id"]

    # --- UnifiedPush desselben Geraets geht vor (Umstieg ohne doppelte Mitteilungen) ---------------------
    with connect() as c, c:
        up = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, device_id, created) VALUES ('pv-a','unifiedpush',"
                       "'https://up.invalid/x',?,?,'Pixel',?,?)", (b64u(ua.pub), b64u(ua.auth), did, time.time())).lastrowid
    with wsc(BEAR) as w:
        req("PUT", f"/api/notes/{pg}/shares", Bk, {"type": "user", "target": "pv-a", "role": "edit"})
        time.sleep(3)
        with connect() as c:
            q = c.execute("SELECT sub_id FROM push_queue WHERE uid='pv-a'").fetchall()
        ok("Geraet mit UnifiedPush-Abo: nichts ueber die eigene Verbindung", recv_msg(w, 2) is None and rows(sid) == [] and sid not in [x[0] for x in q])
    with connect() as c, c:
        c.execute("DELETE FROM push_queue WHERE sub_id=?", (up,))
        c.execute("DELETE FROM push_sub WHERE id=?", (up,))

    # --- Admin schaltet den Weg ab -----------------------------------------------------------------------
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": False})
    time.sleep(5.6)
    with wsc(BEAR) as w:
        ok("Weg abgeschaltet -> 4404", closed_code(w) == 4404)
    s3, _ = req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua.keys()}, hdr=BEAR)
    ok(f"... Anmelden -> 403 ({s3})", s3 == 403)
    ok("... /api/push meldet stream = false", req("GET", "/api/push", A)[1]["server"]["stream"] is False)
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True})
    time.sleep(5.6)

    # --- Geraet abgemeldet -> 4401 sofort --------------------------------------------------------------------
    w = wsc(BEAR)
    w.send(bytes([3])); w.recv(timeout=5)
    st, _ = req("DELETE", f"/api/settings/devices/{did}", A)
    ok(f"Geraet abgemeldet (Einstellungen -> Geraete, {st}) -> Verbindung schliesst mit 4401", closed_code(w, 8) == 4401)
    w.close()

    # --- Bremse beim Aufbau --------------------------------------------------------------------------------
    did3, BEAR3 = device("pv-a", "Spam")
    codes = []
    for _ in range(14):
        with wsc(BEAR3) as w:
            codes.append(closed_code(w, 3))
    ok(f"Bremse: hoechstens 12 Aufbauten je Minute und Geraet, danach 4429 ({codes[-3:]})", codes[:12] == [4404] * 12 and codes[12:] == [4429, 4429])

    # --- Leerlauf der rohen Verbindung: kein einziges Byte vom Server ------------------------------------------
    wait = 25 - (time.time() - raw_t0)
    if wait > 0:
        time.sleep(wait)
    got = rest
    try:
        while True:
            d = raw.recv(4096)
            if not d:
                break
            got += d
    except BlockingIOError:
        pass
    idle = time.time() - raw_t0
    # nur Mitteilungen an Bert (Testmitteilung, Freigabe) - je eine 0x01 in einem Binaer-Rahmen, kein Ping-Rahmen (0x89)
    frames = []
    i = 0
    while i + 2 <= len(got):
        op, ln = got[i] & 0x0F, got[i + 1] & 0x7F
        j = i + 2
        if ln == 126:
            ln = struct.unpack("!H", got[j:j + 2])[0]; j += 2
        frames.append((op, got[j] if ln else None))
        i = j + ln
    ok(f"Leerlauf {idle:.0f} s: der Server sendet nichts von sich aus - nur die abgelegten Mitteilungen, kein Ping ({frames})",
       idle >= 25 and frames and all(f == (2, 1) for f in frames) and len(frames) == len(rows(sB)))
    raw.close()

    # --- Server-Log: keine Inhalte -------------------------------------------------------------------------
    log = open(os.path.join(os.environ.get("STATE_DIR", "/tmp"), "server.log"), encoding="utf-8", errors="replace").read()
    ok("Server-Log ohne Titel/Kommentar-Inhalte", "QQQ" not in log and "ZZZ" not in log)
    print(f"(Dauer {time.time() - t_start:.0f} s)")


if __name__ == "__main__":
    main()
