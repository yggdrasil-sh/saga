"""Anrufe (2.1, saga/calls.py) - Server-Seite, mit PyNaCl als Client (tests/chatlib.py).

Testvektoren saga-call/v1, Konfiguration (STUN, TURN mit zeitlich begrenzten Zugangsdaten bzw. festem Zugang, nur Relais),
Rechte (nur Mitglieder eines Direkt-Chats, eigenes Geraet, genaue Geraeteliste des Gegenuebers, Chats eingerichtet),
Klingeln (Live-Ereignis ohne Inhalt an das Gegenueber, Push "call" ueber ntfy mit Prioritaet 5 ohne Namen, APNs-VoIP mit
eigenem Topic, normales APNs dann nicht), Signale (versiegelt fuer das Zielgeraet, signiert vom Geraet, der Server sieht
weder SDP noch IP-Adressen - auch nicht im Log), Annehmen (erstes Geraet gewinnt, die anderen hoeren auf), Trickle-ICE in
beide Richtungen, Besetzt, Ablehnen, Abbrechen, Zeitlimit (verpasst: Chat-Eintrag + Benachrichtigung + Push), verlorene
Verbindung, Chat-Eintraege nur mit ?calls=1, Ratenbegrenzung, Admin-Schalter (Admin-Bereich), Kontoloeschung.
Wegwerf-Konten ca-*, Gruppe ca-team; raeumt auf."""
import base64, hashlib, hmac, http.client, http.server, json, os, re, secrets, socket, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import chatlib as C  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
PW = "Anruf-Pass-1234"
WAIT = 5.6
KDF = {"ops": 1, "mem": 8 << 20}
IP_MARK = "198.51.100.77"                       # Dokumentations-Adresse im ICE-Kandidaten: darf nirgends beim Server landen
SDP_MARK = "saga-anruf-sdp-marke"
FP = 18292
res, STREAMS = [], []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u, **extra):
    return S.sign(base64.b64encode(json.dumps(dict({"uid": u, "name": u, "pwc": time.time() + 5}, **extra)).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, base=None):
    b = base or B_
    c = http.client.HTTPConnection(b.hostname, b.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    data = None
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; data = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse(); out = r.read()
    try:
        out = json.loads(out)
    except ValueError:
        out = out.decode("utf-8", "replace")
    c.close()
    return r.status, out


class Stream:
    def __init__(self, cookie):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
        self.r = self.c.getresponse()
        self.ev, self.raw = [], []
        threading.Thread(target=self._run, daemon=True).start()
        STREAMS.append(self)

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                self.raw.append(line)
                if line == "":
                    if "event" in cur:
                        self.ev.append((cur["event"], json.loads(cur.get("data") or "null")))
                    cur = {}
                elif not line.startswith(":"):
                    k, _, v = line.partition(":")
                    cur[k] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001
            pass

    def mark(self):
        return len(self.ev)

    def wait(self, typ, pred=None, since=0, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ and (pred is None or pred(e[1])):
                    return e[1]
            time.sleep(0.05)
        return None

    def none(self, typ, since, secs=1.2):
        time.sleep(secs)
        return not [e for e in self.ev[since:] if e[0] == typ]

    def close(self):
        try:
            self.c.sock.shutdown(socket.SHUT_RDWR); self.c.close()
        except Exception:  # noqa: BLE001
            pass


# --- Attrappe fuer ntfy und APNs ---------------------------------------------------------------------------------
GOT, LOCK = [], threading.Lock()


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        with LOCK:
            GOT.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def got(pred, since, timeout=9.0):
    end = time.time() + timeout
    while time.time() < end:
        with LOCK:
            hits = [g for g in GOT[since:] if pred(g)]
        if hits:
            return hits[0]
        time.sleep(0.1)
    return None


def mark():
    with LOCK:
        return len(GOT)


# --- Personen mit Chat-Schluesseln -----------------------------------------------------------------------------
class Person:
    def __init__(self, uid, setup=True):
        self.uid, self.k = uid, ck(uid)
        self.ident, self.dev = C.identity(), C.device()
        self.devs = [self.dev]
        if setup:
            s, d = req("PUT", "/api/chat/identity", self.k, C.setup_body(uid, self.ident, self.dev, C.recovery_key(), **KDF))
            assert s == 200, (s, d)

    def add_device(self):
        dv = C.device()
        s, d = req("POST", "/api/chat/devices", self.k, C.device_api(self.uid, self.ident, dv, "Zweitgeraet"))
        assert s == 200, (s, d)
        self.devs.append(dv)
        return dv


def dm(a, b):
    s, d = req("POST", "/api/chats", a.k, {"kind": "dm", "user": b.uid, "device": a.dev["id"]})
    assert s == 200, (s, d)
    return d["chat"]["id"]


def offer_body(media="audio"):
    sdp = ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=" + SDP_MARK + "\r\nt=0 0\r\n"
           "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB:3D:17:6E:B4:2A:7C:4F:0D:2E:F1:A8:B3\r\n"
           "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 " + IP_MARK + "\r\na=mid:0\r\n")
    return json.dumps({"t": "offer", "sdp": sdp, "media": media, "fp": C.sdp_fingerprints(sdp)}, separators=(",", ":"))


def ice_body():
    return json.dumps({"t": "ice", "c": [{"candidate": f"candidate:1 1 udp 2122260223 {IP_MARK} 54321 typ srflx", "sdpMid": "0",
                                          "sdpMLineIndex": 0}]}, separators=(",", ":"))


def seal_for(frm, dev, cid, to, to_devs, body):
    return {d["id"]: C.seal_signal(cid, frm.uid, dev, to.uid, d["id"], d["enc_pk"], body) for d in to_devs}


def call(a, b, chat, media="audio", cid=None, devs=None, dev=None):
    cid = cid or secrets.token_hex(16)
    dev = dev or a.dev
    body = {"id": cid, "chat_id": chat, "media": media, "device": dev["id"],
            "signals": seal_for(a, dev, cid, b, b.devs if devs is None else devs, offer_body(media))}
    s, d = req("POST", "/api/calls", a.k, body)
    return cid, s, d


def open_ok(me, dev, cid, frm, frm_dev, s):
    """Signal wie ein Client oeffnen und pruefen -> body (dict) oder None."""
    body, sig = C.open_signal(s["sealed"], dev["enc_pk"], dev["enc_sk"])
    if not C.verify_signal(cid, frm.uid, frm_dev["id"], me.uid, dev["id"], body, sig, frm_dev["sign_pk"]):
        return None
    return json.loads(body)


def call_rows(cid):
    with connect() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT body_ct FROM chat_msg WHERE chat_id=? AND kind='call' ORDER BY seq", (cid,))]


def notifs(uid, kind="call.missed"):
    with connect() as c:
        return [json.loads(r[0]) for r in c.execute("SELECT data FROM notification WHERE uid=? AND kind=? ORDER BY id", (uid, kind))]


def plaintext_hits(*needles):
    hits = []
    with connect() as c:
        cols = c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' "
                         "AND data_type IN ('text', 'character varying')").fetchall()
        for t, col in cols:
            for nd in needles:
                if c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE strpos("{col}", ?) > 0', (nd,)).fetchone()[0]:
                    hits.append((t, col, nd))
    return hits


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ca-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "chat_identity", "chat_device", "chat_link", "chat_file", "push_sub", "push_queue", "device_login"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'ca-%'")
        c.execute("DELETE FROM chat_call WHERE caller LIKE 'ca-%' OR callee LIKE 'ca-%'")
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'ca-%' OR created_by LIKE 'ca-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'ca-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'ca-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'ca-%'")


def admin(path):
    """Admin-Bereich (eigener Port) mit Sitzung wie in tests/push.py -> (status, html)."""
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    cookie = sa.sign(base64.b64encode(json.dumps({"aid": "ca-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    c.request("GET", path, headers={"Cookie": "hk_admin=" + cookie, "Accept-Language": "de"})
    r = c.getresponse(); data = r.read().decode("utf-8", "replace"); c.close()
    return r.status, data


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls", "allowed_hosts")}
    with connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()
    keep_apns = r[0] if r else None
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "",
                                "turn_user": "", "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45})
    auth.save_setting("allowed_hosts", ", ".join(x for x in (prev.get("allowed_hosts"), f"127.0.0.1:{FP}") if x))
    for u, n in (("ca-a", "Anna Anruf"), ("ca-b", "Bert Anruf"), ("ca-c", "Cora Anruf"), ("ca-d", "Dora Anruf"), ("ca-e", "Emil Anruf"),
                 ("ca-f", "Fritz Anruf"), ("ca-g", "Gabi Anruf")):
        auth.create_user(u, PW, n)
    auth.create_user("ca-adm", PW, "Admin Anruf", is_admin=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(WAIT)
    try:
        run()
    finally:
        for x in STREAMS:
            x.close()
        srv.shutdown()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        with connect() as c, c:
            if keep_apns is None:
                c.execute("DELETE FROM setting WHERE k='apns'")
            else:
                c.execute("UPDATE setting SET v=? WHERE k='apns'", (keep_apns,))
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nANRUFE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    import call_vectors
    vr = call_vectors.check()
    ok(f"Testvektoren saga-call/v1 mit PyNaCl: {sum(g for _, g in vr)}/{len(vr)} ({[n for n, g in vr if not g]})", all(g for _, g in vr))

    A, B, Cc, D = Person("ca-a"), Person("ca-b"), Person("ca-c"), Person("ca-d")
    E_, F_ = Person("ca-e"), Person("ca-f", setup=False)
    B2 = B.add_device()
    ab, cb, da, ef = dm(A, B), dm(Cc, B), dm(D, A), dm(E_, F_)

    # --- Konfiguration ------------------------------------------------------------------------------------------
    ok("Ohne X-Saga: 401", req("GET", "/api/calls/config", None, hdr={"Cookie": "saga=" + A.k})[0] == 401)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Konfiguration: an, STUN aus den Einstellungen, ohne TURN, Klingeldauer ({s}, {cf})", s == 200 and cf["enabled"]
       and cf["ice_servers"] == [{"urls": ["stun:stun.example.org:3478"]}] and not cf["turn"] and not cf["relay_only"] and cf["ring_s"] == 45)
    sec = "turn-geheimnis-" + secrets.token_hex(4)
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], turn_urls="turns:turn.example.org:443?transport=tcp turn:turn.example.org:3478",
                                    turn_secret=sec, relay_only=True))
    with connect() as c:
        raw = c.execute("SELECT v FROM setting WHERE k='calls'").fetchone()[0]
    ok("TURN-Geheimnis verschluesselt gespeichert", sec not in raw and "enc:" in raw)
    time.sleep(WAIT)
    s, cf = req("GET", "/api/calls/config", A.k)
    t_ = (cf.get("ice_servers") or [{}, {}])[-1]
    exp, _, tag = str(t_.get("username", "")).partition(":")
    want = base64.b64encode(hmac.new(sec.encode(), t_.get("username", "").encode(), hashlib.sha1).digest()).decode()
    ok(f"TURN (REST): username <ablauf>:<hash>, credential = HMAC-SHA1 (coturn use-auth-secret) ({t_})",
       t_.get("urls") == ["turns:turn.example.org:443?transport=tcp", "turn:turn.example.org:3478"] and exp.isdigit()
       and 43000 < int(exp) - time.time() < 43300 and re.fullmatch(r"[0-9a-f]{16}", tag) and "ca-a" not in t_["username"] and t_.get("credential") == want)
    ok("... nur Relais (relay_only) gilt mit TURN", cf["relay_only"] and cf["turn"])
    s, cfb = req("GET", "/api/calls/config", B.k)
    ok("... je Person eigener (anonymer) Kennungs-Hash", cfb["ice_servers"][-1]["username"].split(":")[1] != tag)
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], turn_mode="static", turn_user="saga", turn_password="pw-statisch",
                                    relay_only=False, ring_s=45))
    time.sleep(WAIT)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok("TURN (fest): Benutzer/Passwort wie eingetragen", cf["ice_servers"][-1].get("username") == "saga" and cf["ice_servers"][-1].get("credential") == "pw-statisch")
    from saga import calls as CL
    good, bad = CL.urls("stun:a.example.org:3478, turn:b.example.org javascript:alert(1) turns:[2001:db8::1]:443?transport=tcp", ("stun", "turn", "turns"))
    ok(f"Adress-Pruefung: nur stun:/turn:/turns: ({good}, {bad})", good == ["stun:a.example.org:3478", "turn:b.example.org", "turns:[2001:db8::1]:443?transport=tcp"]
       and bad == ["javascript:alert(1)"])

    # --- Rechte -----------------------------------------------------------------------------------------------
    sA, sB, sC = Stream(A.k), Stream(B.k), Stream(Cc.k)
    time.sleep(0.5)
    _, s, d = call(Cc, B, ab)
    ok(f"Kein Mitglied des Chats -> 404 ({s})", s == 404)
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-b", "ca-c"]})
    free = d["chat"]["id"]
    _, s, d = call(A, B, free)
    # 2.4: in Gruppen-Chats startet POST /api/calls einen Gruppenanruf - nur fuer Geraete mit "multi" (2.4); ein
    # 2.1/2.3-Client (ohne caps) bekommt eine verstaendliche Meldung statt eines halben Anrufs
    ok(f"Gruppen-Chat mit 2.3-Client (ohne caps multi) -> 409 'braucht Saga 2.4' ({s})", s == 409 and "2.4" in d.get("error", ""))
    _, s, d = call(A, B, ab, dev=B.dev)
    ok(f"Fremdes Geraet als eigenes -> 403 ({s})", s == 403)
    _, s, d = call(A, B, ab, devs=[B.dev])
    ok(f"Angebot nicht fuer ALLE Geraete des Gegenuebers -> 409 mit Geraeteliste ({s})", s == 409 and {x["id"] for x in d.get("devices", [])} == {B.dev["id"], B2["id"]})
    _, s, d = call(E_, F_, ef, devs=[])
    ok(f"Gegenueber ohne Chat-Schluessel -> 409 mit Erklaerung ({s})", s == 409 and "keinem Gerät" in d.get("error", ""))
    body = {"id": secrets.token_hex(16), "chat_id": ab, "media": "audio", "device": A.dev["id"],
            "signals": {B.dev["id"]: "!!kaputt!!", B2["id"]: "QUFB"}}
    ok("Kaputte/zu kurze versiegelte Signale -> 400", req("POST", "/api/calls", A.k, body)[0] == 400)
    body["signals"] = {B.dev["id"]: base64.b64encode(os.urandom(30000)).decode(), B2["id"]: base64.b64encode(os.urandom(80)).decode()}
    ok("Zu grosses Signal -> 400", req("POST", "/api/calls", A.k, body)[0] == 400)
    ok("Unbekannte Medienart -> 400", req("POST", "/api/calls", A.k, dict(body, media="fax"))[0] == 400)

    # --- Push-Ziele fuer B: ntfy, APNs und APNs-VoIP am selben App-Geraet --------------------------------------------
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                serialization.NoEncryption()).decode()
    with connect() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('apns', ?)", (json.dumps({
            "enabled": True, "team_id": "ABCDE12345", "key_id": "KEY1234567", "bundle_id": "org.example.saga", "p8": auth._enc(pem),
            "url": f"http://127.0.0.1:{FP}"}),))
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('ca-b','ca-b','iPhone',?,?,?)",
                        (hashlib.sha256(os.urandom(16)).hexdigest(), "hkd_test", time.time())).lastrowid
        now = time.time()
        for kind, ep in (("ntfy", f"http://127.0.0.1:{FP}/ca-topic-b"), ("apns", "apns:production:" + "b1" * 32),
                         ("apns-voip", "apns-voip:production:" + "c2" * 32)):
            c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, device_id, created) VALUES ('ca-b',?,?,?,?,?)",
                      (kind, ep, kind, None if kind == "ntfy" else did, now))
    auth._settings_cache["v"] = None
    from saga import push as P
    with connect() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM push_sub WHERE uid='ca-b' ORDER BY id")]
        kinds = dict(c.execute("SELECT id, kind FROM push_sub WHERE uid='ca-b'").fetchall())
        r_call = sorted(kinds[i] for i in P._route(c, ids, "call"))
        r_msg = sorted(kinds[i] for i in P._route(c, ids, "chat.message"))
    ok(f"Push-Wege: Anruf -> ntfy + VoIP (nicht das normale APNs am selben Geraet) ({r_call})", r_call == ["apns-voip", "ntfy"])
    ok(f"... alles andere -> nie ueber VoIP ({r_msg})", r_msg == ["apns", "ntfy"])
    time.sleep(WAIT)

    # --- Klingeln -------------------------------------------------------------------------------------------------
    mA, mB, mC, m0 = sA.mark(), sB.mark(), sC.mark(), mark()
    cid, s, d = call(A, B, ab, media="video")
    ok(f"A ruft B an (Video): 200, Zustand klingelt ({s})", s == 200 and d["call"]["state"] == "ringing" and d["call"]["role"] == "caller"
       and d["call"]["peer"]["username"] == "ca-b")
    ev = sB.wait("call.ring", lambda x: x["call_id"] == cid, mB)
    ok(f"Live: call.ring an B mit Name/Medien, ohne Signal-Inhalt ({ev})", ev and ev["from"]["name"] == "Anna Anruf" and ev["media"] == "video"
       and ev["from_device"] == A.dev["id"] and "signals" not in ev and "sealed" not in ev)
    ok("... nicht an Unbeteiligte", sC.none("call.ring", mC, 0.5))
    g = got(lambda x: x[0] == "/" and json.loads(x[2]).get("topic") == "ca-topic-b", m0)
    j = json.loads(g[2]) if g else {}
    ok(f"Push ntfy: Prioritaet 5, ohne Namen, Link zum Anruf ({j})", j.get("priority") == 5 and j.get("message") in ("Eingehender Videoanruf", "Incoming video call")
       and j.get("click", "").endswith(f"/chats#call-{cid}") and "Anna" not in json.dumps(j))
    gv = got(lambda x: x[0] == "/3/device/" + "c2" * 32, m0)
    hv, pv = (gv[1], json.loads(gv[2])) if gv else ({}, {})
    ok(f"Push APNs-VoIP: apns-push-type voip, Topic <bundle>.voip, kurze Frist, Kennungen ({hv.get('apns-topic')}, {pv})",
       hv.get("apns-push-type") == "voip" and hv.get("apns-topic") == "org.example.saga.voip" and pv.get("call") == cid and pv.get("chat") == ab
       and pv.get("media") == "video" and pv.get("caller") == "Anna Anruf" and int(hv.get("apns-expiration", "0")) - time.time() < 75)
    ok("... normales APNs am selben Geraet klingelt nicht doppelt", not got(lambda x: x[0] == "/3/device/" + "b1" * 32, m0, 1.0))
    s, act = req("GET", "/api/calls/active", B.k)
    ok("B: /api/calls/active zeigt den klingelnden Anruf", [x["id"] for x in act["calls"]] == [cid] and act["calls"][0]["role"] == "callee"
       and 0 < act["calls"][0]["expires_in"] <= 45)
    ok("Unbeteiligte sehen den Anruf nicht (404)", req("GET", f"/api/calls/{cid}", Cc.k)[0] == 404)
    s, g1 = req("GET", f"/api/calls/{cid}?device={B.dev['id']}", B.k)
    sig1 = g1["signals"][0] if g1.get("signals") else None
    b1 = open_ok(B, B.dev, cid, A, A.dev, sig1) if sig1 else None
    ok(f"B (Geraet 1) holt das Angebot: oeffnen + Signatur von A pruefen, Fingerabdruck in signierter SDP ({s})",
       b1 and b1["t"] == "offer" and b1["fp"] == C.sdp_fingerprints(b1["sdp"]) and SDP_MARK in b1["sdp"])
    s, g2 = req("GET", f"/api/calls/{cid}?device={B2['id']}", B.k)
    ok("B (Geraet 2) bekommt ein eigenes, nur fuer Geraet 2 versiegeltes Angebot",
       len(g2["signals"]) == 1 and open_ok(B, B2, cid, A, A.dev, g2["signals"][0]) and g2["signals"][0]["sealed"] != sig1["sealed"])
    ok("... das Angebot fuer Geraet 2 laesst sich mit Geraet 1 nicht oeffnen", _fails(lambda: C.open_signal(g2["signals"][0]["sealed"], B.dev["enc_pk"], B.dev["enc_sk"])))
    forged = {"sealed": C.seal_signal(cid, A.uid, Cc.dev, B.uid, B.dev["id"], B.dev["enc_pk"], offer_body())}
    ok("Gefaelschtes Signal (fremder Signierschluessel) faellt bei der Pruefung durch", open_ok(B, B.dev, cid, A, A.dev, forged) is None)
    s, g3 = req("GET", f"/api/calls/{cid}?device={A.dev['id']}", B.k)
    ok("Fremdes Geraet in ?device= -> keine Signale", s == 200 and g3["signals"] == [])
    # Trickle-ICE vor dem Annehmen: an alle Geraete von B
    s, d = req("POST", f"/api/calls/{cid}/signal", A.k, {"device": A.dev["id"], "signals": seal_for(A, A.dev, cid, B, B.devs, ice_body())})
    ev = sB.wait("call.signal", lambda x: x["call_id"] == cid and x["to_device"] == B.dev["id"], mB)
    ok(f"ICE vor dem Annehmen an beide Geraete von B, live versiegelt ({s}, {d})", s == 200 and d["n"] == 2 and ev
       and (open_ok(B, B.dev, cid, A, A.dev, ev) or {}).get("t") == "ice")
    s, d = req("POST", f"/api/calls/{cid}/signal", B.k, {"device": B.dev["id"], "signals": seal_for(B, B.dev, cid, A, [A.dev], ice_body())})
    ok(f"B darf vor dem Annehmen keine Signale senden ({s})", s == 403)
    _, s2, d2 = call(A, B, ab)
    ok(f"A ruft waehrenddessen erneut an -> 409 ({s2})", s2 == 409 and "telefonierst" in d2.get("error", ""))
    # Bughunt 2 (server, S4): B ruft zurueck, waehrend A's Anruf noch klingelt -> Hinweis + Kennung statt "telefonierst"
    _, s2, d2 = call(B, A, ab)
    ok(f"B ruft A zurueck, waehrend A's Anruf klingelt -> 409 'ruft dich gerade an' + incoming ({s2}, {d2})",
       s2 == 409 and d2.get("incoming") == cid and "telefonierst" not in d2.get("error", ""))

    # --- Annehmen --------------------------------------------------------------------------------------------------
    mA, mB = sA.mark(), sB.mark()
    ans = json.dumps({"t": "answer", "sdp": "v=0\r\na=fingerprint:sha-256 AA:BB\r\n", "fp": ["sha-256 AA:BB"]})
    s, d = req("POST", f"/api/calls/{cid}/answer", B.k, {"device": B.dev["id"], "signals": seal_for(B, B.dev, cid, A, [A.dev], ans)})
    ok(f"B nimmt auf Geraet 1 an ({s})", s == 200 and d["call"]["state"] == "active" and d["call"]["callee_device"] == B.dev["id"])
    ok("... Anruf-Objekt mit Server-Uhr now (2.5, Dauer ab answered fuer alle gleich)", abs(d["call"].get("now", 0) - time.time()) < 5
       and 0 <= d["call"]["now"] - d["call"]["answered"] < 5)
    ev = sA.wait("call.signal", lambda x: x["call_id"] == cid, mA)
    ok("A bekommt die Antwort live (versiegelt fuer A, von B signiert)", ev and ev["from_device"] == B.dev["id"]
       and (open_ok(A, A.dev, cid, B, B.dev, ev) or {}).get("t") == "answer")
    st = sB.wait("call.state", lambda x: x["call_id"] == cid and x["state"] == "active", mB)
    ok("call.state active an B (andere Geraete/Tabs hoeren auf zu klingeln) und A", st and st["device"] == B.dev["id"]
       and sA.wait("call.state", lambda x: x["call_id"] == cid and x["state"] == "active", mA))
    s, d = req("POST", f"/api/calls/{cid}/answer", B.k, {"device": B2["id"], "signals": seal_for(B, B2, cid, A, [A.dev], ans)})
    ok(f"Zweites Geraet nimmt danach an -> 409 ({s})", s == 409)
    s, _ = req("POST", f"/api/calls/{cid}/signal", B.k, {"device": B2["id"], "signals": seal_for(B, B2, cid, A, [A.dev], ice_body())})
    ok(f"Zweites Geraet darf keine Signale senden ({s})", s == 403)
    s, _ = req("POST", f"/api/calls/{cid}/signal", A.k, {"device": A.dev["id"], "signals": seal_for(A, A.dev, cid, B, [B2], ice_body())})
    ok(f"A darf nach dem Annehmen nur noch an das annehmende Geraet senden ({s})", s == 400)
    mA, mB = sA.mark(), sB.mark()
    s1, _ = req("POST", f"/api/calls/{cid}/signal", A.k, {"device": A.dev["id"], "signals": seal_for(A, A.dev, cid, B, [B.dev], ice_body())})
    s2, _ = req("POST", f"/api/calls/{cid}/signal", B.k, {"device": B.dev["id"], "signals": seal_for(B, B.dev, cid, A, [A.dev], ice_body())})
    e1 = sB.wait("call.signal", lambda x: x["call_id"] == cid and x["from_device"] == A.dev["id"], mB)
    e2 = sA.wait("call.signal", lambda x: x["call_id"] == cid and x["from_device"] == B.dev["id"], mA)
    ok(f"Trickle-ICE in beide Richtungen ({s1}, {s2})", s1 == 200 and s2 == 200 and e1 and e2
       and (open_ok(B, B.dev, cid, A, A.dev, e1) or {}).get("t") == "ice" and (open_ok(A, A.dev, cid, B, B.dev, e2) or {}).get("t") == "ice")
    ok("Unbeteiligte koennen keine Signale senden (404)", req("POST", f"/api/calls/{cid}/signal", Cc.k, {"device": Cc.dev["id"], "signals": {}})[0] == 404)
    ok("Lebenszeichen: /alive -> active", req("POST", f"/api/calls/{cid}/alive", A.k, {})[1].get("state") == "active")

    # Server sieht weder SDP noch IP-Adressen
    hits = plaintext_hits(IP_MARK, SDP_MARK, "candidate:1", "a=fingerprint")
    ok(f"Server sieht keine SDP/IP/Kandidaten: Suche in ALLEN Textspalten ({hits})", hits == [])
    log = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8", errors="replace").read()
        ok("... und nicht im Server-Log (auch keine Signal-Inhalte)", IP_MARK not in txt and SDP_MARK not in txt and "candidate:" not in txt)
        lines = [ln for ln in txt.splitlines() if "/api/calls" in ln]
        ok(f"Zugriffs-Log der Signalisierung ohne IP-Adressen ({lines[:1]})", lines and not [ln for ln in lines if re.search(r"\d+\.\d+\.\d+\.\d+:\d+", ln)])

    # --- Besetzt --------------------------------------------------------------------------------------------------
    mC = sC.mark()
    c2, s, d = call(Cc, B, cb)
    ok(f"C ruft B an, waehrend B telefoniert -> 409 besetzt ({s})", s == 409 and d.get("busy"))
    ok("... Eintrag 'busy' im Chat C-B, Benachrichtigung 'Verpasster Anruf' fuer B",
       [r["status"] for r in call_rows(cb)] == ["busy"] and any(n.get("call") == c2 for n in notifs("ca-b")))
    ok("... und kein Klingeln bei B", not [e for e in sB.ev if e[0] == "call.ring" and e[1]["call_id"] == c2])

    # --- Auflegen -------------------------------------------------------------------------------------------------
    mA, mB = sA.mark(), sB.mark()
    time.sleep(1.1)
    s, _ = req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup", "connected": True})
    e = sB.wait("call.state", lambda x: x["call_id"] == cid and x["state"] == "ended", mB)
    ok(f"A legt auf: call.state ended an beide ({s}, {e})", s == 200 and e and e["status"] == "ended"
       and sA.wait("call.state", lambda x: x["call_id"] == cid and x["state"] == "ended", mA))
    rows = call_rows(ab)
    ok(f"Chat-Eintrag: Videoanruf beendet mit Dauer ({rows})", rows and rows[-1]["status"] == "ended" and rows[-1]["media"] == "video"
       and rows[-1]["dur"] >= 1 and rows[-1]["from"] == "ca-a" and rows[-1]["to"] == "ca-b")
    s, ms = req("GET", f"/api/chats/{ab}/messages?device={B.dev['id']}&calls=1", B.k)
    cm = [m for m in ms["messages"] if m["kind"] == "call"]
    ok("Nachrichten mit ?calls=1: Eintrag kind='call' (Metadaten, ohne Schluessel)", cm and cm[-1]["key"] is None and json.loads(cm[-1]["body_ct"])["call"] == cid)
    s, ms0 = req("GET", f"/api/chats/{ab}/messages?device={B.dev['id']}", B.k)
    ok("... ohne ?calls=1 (aeltere Apps) nicht dabei", all(m["kind"] == "msg" for m in ms0["messages"]))
    s, ch = req("GET", f"/api/chats/{ab}?device={B.dev['id']}", B.k)
    ok("... zaehlt nicht als ungelesene Nachricht", ch["chat"]["unread"] == 0)
    ok("Nach dem Ende: keine Signale mehr (409)", req("POST", f"/api/calls/{cid}/signal", A.k, {"device": A.dev["id"],
                                                     "signals": seal_for(A, A.dev, cid, B, [B.dev], ice_body())})[0] == 409)
    ok("Zweites Auflegen ist harmlos", req("POST", f"/api/calls/{cid}/end", B.k, {})[1].get("already"))

    # --- Ablehnen -------------------------------------------------------------------------------------------------
    n0 = len(notifs("ca-b"))
    mA = sA.mark()
    c3, s, _ = call(A, B, ab)
    ok("A ruft erneut an (Audio)", s == 200)
    ok("A selbst darf nicht annehmen/ablehnen", req("POST", f"/api/calls/{c3}/decline", A.k, {})[0] == 409)
    s, _ = req("POST", f"/api/calls/{c3}/decline", B.k, {"reason": "declined"})
    e = sA.wait("call.state", lambda x: x["call_id"] == c3 and x["state"] == "ended", mA)
    ok(f"B lehnt ab: A erfaehrt es live (declined), Eintrag 'declined', keine Verpasst-Meldung ({e})", s == 200 and e and e["reason"] == "declined"
       and call_rows(ab)[-1]["status"] == "declined" and len(notifs("ca-b")) == n0)

    # --- Abbrechen beim Klingeln -> verpasst ------------------------------------------------------------------------
    sD = Stream(D.k)
    time.sleep(0.3)
    c4, s, _ = call(D, A, da)
    s, _ = req("POST", f"/api/calls/{c4}/end", D.k, {"reason": "hangup"})
    e = sA.wait("call.state", lambda x: x["call_id"] == c4 and x["state"] == "ended", mA)
    ok(f"D bricht beim Klingeln ab: bei A 'verpasst' + Benachrichtigung ({e})", e and e["reason"] == "cancelled" and e["status"] == "missed"
       and call_rows(da)[-1]["status"] == "missed" and any(n.get("call") == c4 for n in notifs("ca-a")))
    with connect() as c:
        from saga import notify as N
        it = [x for x in N._render(c, "ca-a", c.execute("SELECT id, kind, actor_uid, note_id, comment_id, data, created, read_at FROM notification "
                                                         "WHERE uid='ca-a' AND kind='call.missed' ORDER BY id DESC LIMIT 1").fetchall())]
    ok(f"Benachrichtigung: 'Verpasster Anruf von Dora Anruf', Link in den Chat ({it[0]['text'] if it else None})",
       it and it[0]["text"] in ("Verpasster Anruf von Dora Anruf", "Missed call from Dora Anruf") and it[0]["url"] == f"/chats#c{da}" and it[0]["access"])

    # --- Zeitlimit (Hintergrund im Server) --------------------------------------------------------------------------
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], ring_s=3))
    time.sleep(WAIT)
    m0, mA, mB = mark(), sA.mark(), sB.mark()
    c5, s, d = call(A, B, ab)
    ok(f"Klingeldauer 3 s (Admin) ({s})", s == 200 and d["call"]["ring_s"] == 3)
    e = sB.wait("call.state", lambda x: x["call_id"] == c5 and x["state"] == "ended", mB, timeout=12)
    ok(f"Niemand nimmt ab: nach der Klingeldauer verpasst (live an beide) ({e})", e and e["reason"] == "timeout" and e["status"] == "missed"
       and sA.wait("call.state", lambda x: x["call_id"] == c5 and x["state"] == "ended", mA, timeout=2))
    ok("... Chat-Eintrag 'missed', Benachrichtigung fuer B", call_rows(ab)[-1]["status"] == "missed" and any(n.get("call") == c5 for n in notifs("ca-b")))
    s, d = req("POST", f"/api/calls/{c5}/answer", B.k, {"device": B.dev["id"], "signals": seal_for(B, B.dev, c5, A, [A.dev], ans)})
    ok(f"Annehmen nach dem Zeitlimit -> 409 ({s})", s == 409)
    gm = got(lambda x: x[0] == "/3/device/" + "b1" * 32 and json.loads(x[2]).get("kind") == "call.missed", m0)
    ok("Push 'Verpasster Anruf' ueber normales APNs (nicht VoIP)", gm and gm[1].get("apns-push-type") == "alert"
       and not got(lambda x: x[0] == "/3/device/" + "c2" * 32 and json.loads(x[2]).get("kind") == "call.missed", m0, 0.5))
    with connect() as c:
        late = P.message(c, "ca-b", "call", actor="ca-a", data={"call": c5}, sub_kind="webpush")
    ok("Push-Auftrag fuer einen vorbeigegangenen Anruf wird verworfen (kein spaetes Klingeln)", late is None)
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], ring_s=45))
    time.sleep(WAIT)

    # --- Verbindung verloren (Lebenszeichen bleiben aus) ----------------------------------------------------------
    c6, s, _ = call(Cc, B, cb)
    # Bughunt 2 (server, S4): Anruf-Mitteilung lebt so lange, wie der Anruf klingeln darf (Admin bis 120 s)
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], ring_s=100))
    auth._settings_cache["v"] = None
    with connect() as c:
        pm = P.message(c, "ca-b", "call", actor="ca-c", data={"call": c6}, sub_kind="webpush") or {}
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], ring_s=45))
    auth._settings_cache["v"] = None
    ok(f"Klingeldauer 100 s -> Lebensdauer der Anruf-Mitteilung > 100 s ({pm.get('ttl')})", (pm.get("ttl") or 0) > 100)
    req("POST", f"/api/calls/{c6}/answer", B.k, {"device": B2["id"], "signals": seal_for(B, B2, c6, Cc, [Cc.dev], ans)})
    n = CL.tick(time.time() + CL.ALIVE_GAP + CL.ORPHAN + 5)        # anderer Prozess als der Server: erst nach der Schonfrist
    with connect() as c:
        st = c.execute("SELECT state, reason FROM chat_call WHERE id=?", (c6,)).fetchone()
    ok(f"Ohne Lebenszeichen: Anruf endet ('lost') ({n}, {st})", st == ("ended", "lost") and call_rows(cb)[-1]["status"] == "ended")
    dur = call_rows(cb)[-1].get("dur")
    ok(f"... Dauer bis zum letzten Lebenszeichen, nicht bis zur Erkennung ({dur})", dur is not None and dur < 10)

    # --- Geraet waehrend des Anrufs entfernt (Review) ----------------------------------------------------------------
    G = Person("ca-g")
    gb = dm(G, B)
    c8, s, _ = call(G, B, gb)
    s1, _ = req("POST", f"/api/calls/{c8}/answer", B.k, {"device": B2["id"], "signals": seal_for(B, B2, c8, G, [G.dev], ans)})
    s2, _ = req("DELETE", f"/api/chat/devices/{B2['id']}", B.k)
    s3, _ = req("POST", f"/api/calls/{c8}/signal", B.k, {"device": B2["id"], "signals": seal_for(B, B2, c8, G, [G.dev], ice_body())})
    ok(f"Entferntes Geraet darf nicht weiter signalisieren ({s}, {s1}, {s2}, {s3})", s == 200 and s1 == 200 and s2 == 200 and s3 in (403, 409))
    CL.tick()
    with connect() as c:
        st = c.execute("SELECT state, reason FROM chat_call WHERE id=?", (c8,)).fetchone()
    ok(f"... und der Anruf endet ('gone') ({st})", st == ("ended", "gone"))
    B.devs = [B.dev]
    c9, s, _ = call(G, B, gb)
    s4, _ = req("POST", f"/api/calls/{c9}/answer", B.k, {"device": B2["id"], "signals": seal_for(B, B2, c9, G, [G.dev], ans)})
    ok(f"Entferntes Geraet kann nicht annehmen ({s}, {s4})", s == 200 and s4 == 403)
    req("POST", f"/api/calls/{c9}/end", G.k, {})

    # --- Ratenbegrenzung --------------------------------------------------------------------------------------------
    codes = []
    ec = dm(E_, Cc)
    for _ in range(4):
        x, s, _ = call(E_, Cc, ec, devs=[Cc.dev])
        codes.append(s)
        if s == 200:
            req("POST", f"/api/calls/{x}/end", E_.k, {})
    ok(f"Klingel-Spam: hoechstens 3 Anrufe je Minute an dieselbe Person ({codes})", codes[:3] == [200, 200, 200] and codes[3] == 429)

    # --- Admin-Bereich ---------------------------------------------------------------------------------------------
    s, html = admin("/calls")                          # 2.5: eigene Seite "Anrufe" (Abschnitt 1:1-Anrufe)
    ok(f"Admin: Karte 'Anrufe' mit STUN/TURN, Geheimnis nicht im Formular ({s})", s == 200 and 'name="calls_enabled"' in html
       and 'name="calls_turn_urls"' in html and "pw-statisch" not in html)
    m = re.search(r'<input[^>]*name="calls_turn_secret"[^>]*>', html) if isinstance(html, str) else None
    m2 = re.search(r'<input[^>]*name="calls_turn_password"[^>]*>', html) if isinstance(html, str) else None
    ok("Admin (2.4): TURN-Geheimnis/-Passwort als Passwortfelder, leer (nie im HTML)", m and m2 and 'type="password"' in m.group(0)
       and 'type="password"' in m2.group(0) and 'value=""' in m.group(0) and 'value=""' in m2.group(0) and (sec or "x") not in html)
    stun = auth.settings(fresh=True)["calls"]["stun"]
    ok(f"Admin (2.4): Datenschutz-Hinweis zum STUN-Server mit Anleitung ({stun})", (not stun) or ("adm-stun-note" in html
       and "IP-Adresse der Anrufenden" in html and "TURN über Port 443" in html))
    import admin as AD
    before = json.dumps(auth.settings(fresh=True)["calls"], sort_keys=True)
    err = AD._calls_save({"calls_enabled": "1", "calls_stun": "stun:ok.example.org javascript:x", "calls_ring_s": "45"})
    ok(f"Admin: ungueltige STUN-Adresse -> Fehler, nichts gespeichert ({err})", err and "javascript:x" in err
       and json.dumps(auth.settings(fresh=True)["calls"], sort_keys=True) == before)
    err = AD._calls_save({"calls_enabled": "1", "calls_stun": "", "calls_turn_urls": "turn:t.example.org:3478", "calls_turn_mode": "rest",
                          "calls_turn_secret": "", "calls_ring_s": "500"})
    cc = auth.settings(fresh=True)["calls"]
    ok(f"Admin: leeres Geheimnis behaelt das alte, Klingeldauer begrenzt, STUN leer erlaubt ({err})", err is None and cc["turn_secret"] == sec
       and cc["ring_s"] == 120 and cc["stun"] == "" and cc["turn_urls"] == "turn:t.example.org:3478")
    AD._calls_save({"calls_enabled": "1", "calls_turn_urls": "turn:t.example.org:3478", "calls_turn_clear": "1", "calls_ring_s": "45"})
    cc = auth.settings(fresh=True)["calls"]
    ok("Admin: 'TURN-Zugangsdaten loeschen' leert Geheimnis, Benutzer und Passwort", not cc["turn_secret"] and not cc["turn_password"] and not cc["turn_user"])

    # --- Abschalten (Admin) -----------------------------------------------------------------------------------------
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], enabled=False))
    time.sleep(WAIT)
    _, s, d = call(D, A, da)
    ok(f"Anrufe aus: Anrufen 403, Konfiguration enabled=false ({s})", s == 403 and req("GET", "/api/calls/config", A.k)[1]["enabled"] is False)
    auth.save_setting("calls", dict(auth.settings(fresh=True)["calls"], enabled=True))
    auth.save_setting("chat_enabled", False)
    time.sleep(WAIT)
    ok("Chats aus: auch keine Anrufe (403)", call(D, A, da)[1] == 403)
    auth.save_setting("chat_enabled", True)
    time.sleep(WAIT)

    # --- Kontoloeschung ---------------------------------------------------------------------------------------------
    c7, s, _ = call(D, A, da)
    from saga.userdata import delete_user_data
    delete_user_data("ca-d")
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM chat_call WHERE caller='ca-d' OR callee='ca-d'").fetchone()[0]
    ok(f"Konto D geloescht: keine Anrufe mehr ({s}, {left})", s == 200 and left == 0)
    sD.close()


def _fails(fn):
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


if __name__ == "__main__":
    main()
