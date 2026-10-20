"""Foederation 2.4 E4/E5: Anrufe als Raum ueber Servergrenzen (Cap calls3, saga/fed_calls.py) - DREI eigene Saga-Instanzen
A, B, C (eigene Datenbanken und Ports). A ist mit B und mit C verbunden, B und C NICHT miteinander: Signale zwischen B und
C laufen sternfoermig ueber den Server, der den Anruf fuehrt. Apps sind In-Process-Clients mit PyNaCl (tests/chatlib.py,
saga-call/v2 mit Handles, versiegelt je Zielgeraet).

Geprueft: Faehigkeit calls3 (A-B, A-C). E4: 1:1 Anna (A) -> Bert (B) als Raum (v3), zweites Geraet von Bert tritt auf B als
Begleitgeraet bei (B -> A: Auftrag join, Angebot an Annas Geraet zugestellt, Antwort zurueck A -> B), Teilnehmerliste auf
beiden Servern, Uebernahme (Bert verlaesst mit dem ersten Geraet: callee_device wechselt beim fuehrenden Server, Anruf
laeuft weiter), Annas zweites Geraet tritt mit role handover bei und uebernimmt. E5: Gruppenanruf in einer Gruppe ueber
drei Server (Heim A), gestartet von Bert auf B (A fuehrt ihn), klingelt bei Anna (A) und Dora (C), Dora tritt auf C bei
(Angebot C -> A -> B, Antwort B -> A -> C - B und C nicht verbunden; Laufzeit je Signal), Anna tritt bei, Grenze 4 Geraete
mit Video (Doras zweites Geraet -> voll), Teilnehmerliste auf allen Servern gespiegelt, Verlassen (laeuft weiter), Ende
(weniger als zwei Personen) auf allen Servern mit Chat-Eintrag. Boese: B schickt eine Teilnehmerliste fuer einen Anruf, den
A fuehrt (403), C meldet ein Signal fuer ein fremdes Geraet (403). Server sehen keine SDP.
2.5 Gjallarhorn P4 (Feature-Flag calls.horn auf allen drei Servern an): Faehigkeit horn ausgehandelt (A-B, A-C), Frame-
Schluessel fkey von Bert (B) an Anna (A) und Dora (C, ueber A) in einer Anfrage, fkey-ack von Dora zurueck (C -> A -> B),
versiegelt und signiert (saga-call/v2) - kein Server sieht einen Schluessel.
Kompatibilitaet: FED_B_23=1 mit FED_B_APP=<Saga 2.3> -> B kann nur calls2: 1:1 bleibt v1 (Beitreten weiterer Geraete:
409 "braucht Saga 2.4"), Gruppenanruf im Chat mit B: Knopf aus (group_block), POST -> 409, keine Serverfehler.
Raeumt auf (Datenbanken geloescht)."""
import base64, json, os, re, secrets, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)
import chatlib as C  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import horn_e2e_vectors as HV  # noqa: E402  (fkey pruefen)

T.I["a"].update(port=8595, admin=8594, db="saga_fra", url="http://127.0.0.1:8595", host="127.0.0.1:8595", state="/tmp/fedraum-a",
                allow="localhost:8597, localhost:8599")
T.I["b"].update(port=8597, admin=8596, db="saga_frb", url="http://localhost:8597", host="localhost:8597", state="/tmp/fedraum-b", allow="127.0.0.1:8595")
T.I["c"] = dict(T.I["b"], port=8599, admin=8598, db="saga_frc", url="http://localhost:8599", host="localhost:8599", state="/tmp/fedraum-c",
                allow="127.0.0.1:8595", secret=T.I["b"]["secret"][::-1])
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
ok, hreq, wait, jd, sql = T.ok, T.hreq, T.wait, T.jd, T.sql
api, User = FC.api, FC.User
B23 = os.environ.get("FED_B_23") == "1"
CAPS = ["reneg", "want", "bye", "screen2", "multi"]
SDP_MARK = "saga-fedraum-sdp-marke"
PERF = {}


def sdp(tag):
    return ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=" + SDP_MARK + "\r\nt=0 0\r\n"
            "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB:3D:17:6E:B4:2A:7C:4F:0D:2E:F1:A8:B3\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=ice-ufrag:" + tag + "\r\na=ice-pwd:x" + tag + "x\r\n")


def body(t, n, a, b, **kw):
    s = sdp("u" + str(n))
    return json.dumps(dict({"t": t, "sdp": s, "fp": C.sdp_fingerprints(s), "n": n, "pc": ":".join(sorted([a["id"], b["id"]])), "caps": CAPS}, **kw),
                      separators=(",", ":"))


def seal(cid, frm, fdev, to, tdev, b):
    return C.seal_signal_v2(cid, frm.handle, fdev, to.handle, tdev["id"], tdev["enc_pk"], b)


def offers(cid, frm, fdev, targets, t="offer"):
    return {d["id"]: seal(cid, frm, fdev, p, d, body(t, 1, fdev, d)) for p, d in targets}


def got(me, dev, cid, frm, fdev):
    """Geoeffnete und gepruefte Signale fuer mein Geraet von frm/fdev -> [body]."""
    s, d = api(me.k, me.uid, "GET", f"/api/calls/{cid}?device={dev['id']}")
    out = []
    for x in (d or {}).get("signals", []):
        if x["from_device"] != fdev["id"]:
            continue
        b, sg = C.open_signal_v2(x["sealed"], dev["enc_pk"], dev["enc_sk"])
        if C.verify_signal_v2(cid, frm.handle, fdev["id"], me.handle, dev["id"], b, sg, fdev["sign_pk"]):
            out.append(json.loads(b))
    return out


def call_of(u, cid):
    s, d = api(u.k, u.uid, "GET", f"/api/calls/{cid}")
    return d.get("call") if s == 200 else None


def legs_of(u, cid):
    c = call_of(u, cid)
    return sorted(x["device"] for x in (c or {}).get("legs", []))


def add_dev(u, name):
    """Weiteres Geraet einer App (Zertifikat v1 + v2, neue signierte Geraete-Liste)."""
    dv = C.device()
    s, d = api(u.k, u.uid, "POST", "/api/chat/devices", C.device_api(u.uid, u.ident, dv, name))
    assert s == 200, (s, d)
    s, me = api(u.k, u.uid, "PUT", "/api/chat/v2/cert", {"devices": {dv["id"]: C.b64(C.device_sig_v2(u.handle, u.ident, dv))}})
    ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
    s, me = api(u.k, u.uid, "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(u.handle, u.ident, ver, me["device_set_want"])})
    assert s == 200 and me.get("device_set_ok"), (s, me)
    return dv


def caps(u, dev):
    return api(u.k, u.uid, "PUT", "/api/calls/caps", {"device": dev["id"], "caps": CAPS})[0]


def connect(k1, k2, A1, A2):
    _, html = A1.post({"action": "invite"})
    code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
    fp = jd(hreq(k1, "GET", "/.well-known/saga")[2])["fingerprint"]
    A2.post({"action": "preview", "url": T.I[k1]["url"], "code": code})
    A2.post({"action": "connect", "url": T.I[k1]["url"], "code": code, "fingerprint": fp})
    p1 = sql(k1, "SELECT id FROM fed_peer WHERE host=?", (T.I[k2]["host"],))[0][0]
    _, html = A1.post({"action": "confirm", "id": str(p1)})
    ok(f"{k1.upper()} <-> {k2.upper()} verbunden ({T.flash(html)[:30]})", "bestätigt" in T.flash(html))


def setup():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna")], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert")], groups=[]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("fc-dora", "Dora Dritt", "dora")], groups=[]))
    for k in T.I:
        F2.py(k, "import auth\nauth.save_setting('calls', {'enabled': True, 'stun': 'stun:stun.example.org:3478', 'turn_urls': '', 'turn_mode': 'rest', "
                 "'turn_secret': '', 'turn_user': '', 'turn_password': '', 'turn_ttl': 43200, 'relay_only': False, 'ring_s': 45, 'horn': True})\nprint(1)")
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    connect("a", "b", adm["a"], adm["b"])
    connect("a", "c", adm["a"], adm["c"])


def has_cap(k, host, cap):
    r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
    return bool(r) and cap in json.loads(r[0][0])


# =====================================================================================================
def part_dm(anna, bert, dora):
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Direkt-Chat Anna -> Bert ({s})", s == 200 and ch.get("v") == 2)
    bc = wait(lambda: bert.chat(ch["ref"]), 15)
    api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
    cid = C.hexid()
    sig = {bert.dev["id"]: seal(cid, anna, anna.dev, bert, bert.dev, body("offer", 1, anna.dev, bert.dev))}
    s, d = api("a", "fa-anna", "POST", "/api/calls", {"id": cid, "chat_id": ch["id"], "media": "audio", "device": anna.dev["id"], "signals": sig, "caps": CAPS})
    ok(f"A: Anna ruft Bert an ({s})", s == 200 and d["call"]["state"] == "ringing")
    v = sql("a", "SELECT v FROM chat_call WHERE id=?", (cid,))[0][0]
    ok(f"A: Raum (v3), weil beide Server calls3 melden (v={v})", v == (1 if B23 else 3))
    ans = {anna.dev["id"]: seal(cid, bert, bert.dev, anna, anna.dev, body("answer", 1, anna.dev, bert.dev))}
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/answer", {"device": bert.dev["id"], "signals": ans, "caps": CAPS})
    ok(f"B: Bert nimmt an ({s})", s == 200 and d["call"]["state"] == "active")
    b2 = add_dev(bert, "Bert-Laptop")
    if not B23:
        caps(bert, b2)
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/join", {"device": b2["id"], "role": "companion", "caps": CAPS,
                                                                 "signals": offers(cid, bert, b2, [(anna, anna.dev), (bert, bert.dev)])})
    if B23:
        ok(f"2.3-Server B: kein Beitreten weiterer Geraete ({s})", s in (404, 405, 409))
        a2 = add_dev(anna, "Anna-Laptop")
        caps(anna, a2)
        s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": a2["id"], "role": "companion", "caps": CAPS,
                                                                     "signals": offers(cid, anna, a2, [(bert, bert.dev)])})
        ok(f"A: Annas zweites Geraet im 1:1 mit 2.3-Server -> 409 braucht Saga 2.4 ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "old")
        api("a", "fa-anna", "POST", f"/api/calls/{cid}/end", {"reason": "hangup"})
        return ch, bc
    ok(f"B: Berts Laptop tritt bei (Begleitgeraet, ueber A) ({s}, {d.get('call', {}).get('legs') and len(d['call']['legs'])})",
       s == 200 and len(d["call"]["legs"]) == 3 and any(x["device"] == b2["id"] and x["role"] == "companion" for x in d["call"]["legs"]))
    ok("A: Teilnehmerliste beim fuehrenden Server (3 Endpunkte)", wait(lambda: len(legs_of(anna, cid)) == 3, 5))
    off = wait(lambda: [b for b in got(anna, anna.dev, cid, bert, b2) if b["t"] == "offer"], 5)
    ok("A: Angebot von Berts Laptop bei Anna (versiegelt, signiert mit Handle)", bool(off) and off[0]["pc"] == ":".join(sorted([anna.dev["id"], b2["id"]])))
    t0 = time.time()
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": anna.dev["id"], "signals": {
        b2["id"]: seal(cid, anna, anna.dev, bert, b2, body("answer", 1, anna.dev, b2))}})
    PERF["signal_a_b_ms"] = round((time.time() - t0) * 1000)
    ok(f"A -> B: Antwort an Berts Laptop ({s}, {PERF['signal_a_b_ms']} ms)", s == 200)
    ok("B: Antwort angekommen und geprueft", bool(wait(lambda: [b for b in got(bert, b2, cid, anna, anna.dev) if b["t"] == "answer"], 5)))
    # Uebernahme: Berts erstes Geraet verlaesst, der Laptop fuehrt weiter
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/leave", {"device": bert.dev["id"], "reason": "handover"})
    ok(f"B: Berts erstes Geraet verlaesst ({s})", s == 200)
    k = wait(lambda: (lambda x: x if x and x["callee_device"] == b2["id"] else None)(call_of(anna, cid)), 5)
    ok("A: Anruf laeuft weiter, callee_device = Laptop (member)", bool(k) and k["state"] == "active"
       and any(x["device"] == b2["id"] and x["role"] == "member" for x in k["legs"]))
    ok("B: Liste gespiegelt [Anna, Laptop]", wait(lambda: legs_of(bert, cid) == sorted([anna.dev["id"], b2["id"]]), 5))
    # Annas zweites Geraet uebernimmt mit role handover
    a2 = add_dev(anna, "Anna-Laptop")
    caps(anna, a2)
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": a2["id"], "role": "handover", "replaces": anna.dev["id"], "caps": CAPS,
                                                                 "signals": offers(cid, anna, a2, [(bert, b2), (anna, anna.dev)])})
    ok(f"A: Annas Laptop tritt mit role handover bei ({s})", s == 200 and any(x["device"] == a2["id"] and x["role"] == "handover" and x.get("replaces") == anna.dev["id"]
                                                                              for x in d["call"]["legs"]))
    ok("B: Angebot von Annas Laptop beim Laptop von Bert", bool(wait(lambda: [b for b in got(bert, b2, cid, anna, a2) if b["t"] == "offer"], 5)))
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/leave", {"device": anna.dev["id"], "reason": "handover"})
    k = call_of(anna, cid)
    ok("A: Anna uebergibt - caller_device = Laptop, Anruf aktiv", s == 200 and k["state"] == "active" and k["caller_device"] == a2["id"])
    ok("B: Liste [Annas Laptop, Berts Laptop]", wait(lambda: legs_of(bert, cid) == sorted([a2["id"], b2["id"]]), 5))
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/end", {"reason": "hangup", "device": b2["id"]})
    ok("B legt auf -> auf A beendet", s == 200 and wait(lambda: call_of(anna, cid)["state"] == "ended", 10))
    anna.devs2 = [a2]
    bert.devs2 = [b2]
    return ch, bc


def part_group(anna, bert, dora):
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": [], "handles": [bert.handle, dora.handle], "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Gruppe ueber drei Server ({s}, v{ch.get('v')})", s == 200 and ch.get("v") == 2)
    bc = wait(lambda: bert.chat(ch["ref"]), 15)
    dc = wait(lambda: dora.chat(ch["ref"]), 15)
    for u, cc in ((bert, bc), (dora, dc)):
        api(u.k, u.uid, "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
    for u in (anna, bert, dora):
        if not (B23 and u is bert):
            caps(u, u.dev)
    s, d = api("b", "fb-bert", "GET", f"/api/chats/{bc['id']}?device={bert.dev['id']}")
    if B23:
        s, d = api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}?device={anna.dev['id']}")
        cl = (d.get("chat") or {}).get("call") or {}
        ok(f"A: Gruppenanruf im Chat mit 2.3-Server aus (group_block: {cl.get('group_block')})", cl.get("group") is False and "2.4" in (cl.get("group_block") or ""))
        s, d = api("a", "fa-anna", "POST", "/api/calls", {"id": C.hexid(), "chat_id": ch["id"], "media": "audio", "device": anna.dev["id"], "caps": CAPS})
        ok(f"A: POST Gruppenanruf -> 409 mit Hinweis ({s}, {d.get('error')})", s == 409 and "2.4" in d.get("error", ""))
        return
    cl = (d.get("chat") or {}).get("call") or {}
    ok(f"B: Gruppenanruf in diesem Chat moeglich (call.group {cl.get('group')})", cl.get("group") is True)
    cid = C.hexid()
    s, d = api("b", "fb-bert", "POST", "/api/calls", {"id": cid, "chat_id": bc["id"], "media": "video", "device": bert.dev["id"], "caps": CAPS})
    ok(f"B: Bert startet den Gruppenanruf - A (Heim) fuehrt ihn ({s})", s == 200 and d["call"]["kind"] == "group"
       and sql("b", "SELECT lead FROM chat_call WHERE id=?", (cid,))[0][0] is not None and sql("a", "SELECT lead FROM chat_call WHERE id=?", (cid,))[0][0] is None)
    act = lambda u: next((x for x in api(u.k, u.uid, "GET", "/api/calls/active")[1].get("calls", []) if x["id"] == cid), None)  # noqa: E731
    ok("A: klingelt bei Anna (Einladung ringing)", bool(wait(lambda: (act(anna) or {}).get("invite") == "ringing", 5)))
    ok("C: Kopie auf C, klingelt bei Dora", bool(wait(lambda: (act(dora) or {}).get("invite") == "ringing", 8)))
    # Dora tritt auf C bei: Angebot an Bert (B) - C und B sind nicht verbunden, alles ueber A
    t0 = time.time()
    s, d = api("c", "fc-dora", "POST", f"/api/calls/{cid}/join", {"device": dora.dev["id"], "role": "member", "caps": CAPS,
                                                                 "signals": offers(cid, dora, dora.dev, [(bert, bert.dev)])})
    PERF["beitritt_c_ueber_a_ms"] = round((time.time() - t0) * 1000)
    ok(f"C: Dora tritt bei ({s}, {PERF['beitritt_c_ueber_a_ms']} ms)", s == 200 and len(d["call"]["legs"]) == 2)
    ok("A: Anruf aktiv (zwei Personen)", wait(lambda: call_of(anna, cid)["state"] == "active", 5))
    ok("B: Angebot von Dora (C) ueber A angekommen", bool(wait(lambda: [b for b in got(bert, bert.dev, cid, dora, dora.dev) if b["t"] == "offer"], 5)))
    t0 = time.time()
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/signal", {"device": bert.dev["id"], "signals": {
        dora.dev["id"]: seal(cid, bert, bert.dev, dora, dora.dev, body("answer", 1, bert.dev, dora.dev))}})
    PERF["signal_b_ueber_a_nach_c_ms"] = round((time.time() - t0) * 1000)
    ok(f"B -> A -> C: Antwort an Dora ({s}, {PERF['signal_b_ueber_a_nach_c_ms']} ms, Ziel <= 300 ms)", s == 200 and PERF["signal_b_ueber_a_nach_c_ms"] <= 300)
    ok("C: Antwort von Bert angekommen und geprueft", bool(wait(lambda: [b for b in got(dora, dora.dev, cid, bert, bert.dev) if b["t"] == "answer"], 5)))
    # Anna tritt auf A bei (Angebote an Bert und Dora)
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": anna.dev["id"], "role": "member", "caps": CAPS,
                                                                 "signals": offers(cid, anna, anna.dev, [(bert, bert.dev), (dora, dora.dev)])})
    ok(f"A: Anna tritt bei ({s})", s == 200 and len(d["call"]["legs"]) == 3)
    ok("B und C: Angebot von Anna", bool(wait(lambda: [b for b in got(bert, bert.dev, cid, anna, anna.dev) if b["t"] == "offer"]
                                                      and [b for b in got(dora, dora.dev, cid, anna, anna.dev) if b["t"] == "offer"], 5)))
    three = sorted([anna.dev["id"], bert.dev["id"], dora.dev["id"]])
    ok("Teilnehmerliste auf A, B und C gespiegelt (3 Geraete)", wait(lambda: legs_of(anna, cid) == three and legs_of(bert, cid) == three and legs_of(dora, cid) == three, 8))
    # 2.5 Gjallarhorn P4: Frame-Schluessel ueber Servergrenzen (versiegelt im Auftrag signal, sternfoermig ueber A)
    kb, sb = secrets.token_bytes(32), secrets.token_hex(8)
    fk = json.dumps({"t": "fkey", "n": 0, "kid": 0, "key": base64.b64encode(kb).decode(), "s": sb}, separators=(",", ":"))
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/signal", {"device": bert.dev["id"], "signals": {
        anna.dev["id"]: seal(cid, bert, bert.dev, anna, anna.dev, fk), dora.dev["id"]: seal(cid, bert, bert.dev, dora, dora.dev, fk)}})
    ok(f"B: Bert verteilt fkey an Anna (A) und Dora (C) in einer Anfrage ({s})", s == 200)
    ka = wait(lambda: [HV.parse_fkey(b) for b in got(anna, anna.dev, cid, bert, bert.dev) if b.get("t") == "fkey"], 5)
    kd = wait(lambda: [HV.parse_fkey(b) for b in got(dora, dora.dev, cid, bert, bert.dev) if b.get("t") == "fkey"], 5)
    ok("A und C: fkey geoeffnet, Signatur (Handle) geprueft, Schluessel gleich", bool(ka and kd) and ka[0]["key"] == kb == kd[0]["key"] and kd[0]["s"] == sb)
    ack = json.dumps({"t": "fkey-ack", "n": 0, "s": sb, "ok": True})
    s, d = api("c", "fc-dora", "POST", f"/api/calls/{cid}/signal", {"device": dora.dev["id"], "signals": {bert.dev["id"]: seal(cid, dora, dora.dev, bert, bert.dev, ack)}})
    ok(f"C -> A -> B: fkey-ack von Dora ({s})", s == 200 and bool(wait(lambda: [b for b in got(bert, bert.dev, cid, dora, dora.dev) if b.get("t") == "fkey-ack"], 5)))
    ok("Kein Server sieht den Frame-Schluessel", all(not sql(x, "SELECT 1 FROM chat_call_signal WHERE sealed LIKE ? OR sealed LIKE ?",
                                                             ("%" + kb.hex() + "%", "%" + base64.b64encode(kb).decode()[:40] + "%")) for x in T.I))
    # Grenze 4 mit Video: Annas Laptop (4.), Doras zweites Geraet (5.) -> voll
    a2 = anna.devs2[0]
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/join", {"device": a2["id"], "role": "companion", "caps": CAPS,
                                                                 "signals": offers(cid, anna, a2, [(bert, bert.dev), (dora, dora.dev)])})
    ok(f"A: Annas Laptop als viertes Geraet ({s})", s == 200 and len(d["call"]["legs"]) == 4)
    d2 = add_dev(dora, "Dora-Tablet")
    caps(dora, d2)
    s, d = api("c", "fc-dora", "POST", f"/api/calls/{cid}/join", {"device": d2["id"], "role": "companion", "caps": CAPS,
                                                                 "signals": offers(cid, dora, d2, [(bert, bert.dev), (anna, anna.dev), (anna, a2)])})
    ok(f"C: fuenftes Geraet -> 409 voll (Grenze 4 mit Video, Zusatzgeraete zaehlen mit) ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "full")
    # Boese: B schickt eine Teilnehmerliste fuer einen Anruf, den A fuehrt; C signalisiert fuer ein fremdes Geraet
    s, _ = FC.signed_post("a", FC.own_priv("b"), "b", "/fed/v2/call", {"op": "roster", "id": cid, "ref": ch["ref"], "kind": "group", "media": "video",
                                                                       "state": "active", "legs": []})
    ok(f"Boese: B meldet eine Liste fuer A's Anruf -> 403 ({s})", s == 403 and len(legs_of(anna, cid)) == 4)
    s, _ = FC.signed_post("a", FC.own_priv("c"), "c", "/fed/v2/call", {"op": "signal", "id": cid, "actor": dora.handle, "device": bert.dev["id"],
                                                                       "signals": {anna.dev["id"]: "x" * 80}})
    ok(f"Boese: C signalisiert als Berts Geraet -> 403 ({s})", s == 403)
    # Verlassen und Ende
    s, d = api("c", "fc-dora", "POST", f"/api/calls/{cid}/leave", {"device": dora.dev["id"]})
    ok(f"C: Dora verlaesst - laeuft weiter ({s})", s == 200 and wait(lambda: dora.dev["id"] not in legs_of(anna, cid) and call_of(anna, cid)["state"] == "active", 5))
    api("a", "fa-anna", "POST", f"/api/calls/{cid}/leave", {"device": a2["id"]})
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/end", {"reason": "hangup", "device": bert.dev["id"]})
    ok("B geht - nur noch Anna -> Ende auf A", s == 200 and wait(lambda: call_of(anna, cid)["state"] == "ended", 5))
    ok("Ende auch auf B und C", wait(lambda: call_of(bert, cid)["state"] == "ended" and call_of(dora, cid)["state"] == "ended", 10))
    ent = [m for m in anna.msgs(ch["id"]) if m["kind"] == "call"]
    k = json.loads(ent[-1]["body_ct"]) if ent else {}
    ok(f"A: Chat-Eintrag Gruppenanruf (n={k.get('n')})", k.get("group") and k.get("status") == "ended" and k.get("n") == 3)
    ok("Server sehen keine SDP (keine Marke in Datenbank/Log)", all(not sql(x, "SELECT 1 FROM chat_call_signal WHERE sealed LIKE ?", ("%" + SDP_MARK + "%",))
                                                                   for x in T.I))


def main():
    rc = 1
    try:
        setup()
        ok("Faehigkeit calls3 ausgehandelt (A-C" + ("" if B23 else ", A-B") + ")",
           wait(lambda: has_cap("a", HC, "calls3") and has_cap("c", HA, "calls3") and (B23 or (has_cap("a", HB, "calls3") and has_cap("b", HA, "calls3"))), 25))
        if B23:
            ok("2.3-Server B meldet kein calls3", not has_cap("a", HB, "calls3"))
        ok("2.5: Faehigkeit horn ausgehandelt (A-C" + ("" if B23 else ", A-B") + ", Flag calls.horn an)",
           wait(lambda: has_cap("a", HC, "horn") and has_cap("c", HA, "horn") and (B23 or (has_cap("a", HB, "horn") and has_cap("b", HA, "horn"))), 25))
        if B23:
            ok("2.3-Server B meldet kein horn", not has_cap("a", HB, "horn"))
        if os.environ.get("BJS"):                      # Browser-Lauf (tests/browser_fed_raum.js) gegen dieselben drei Server
            import subprocess
            env = dict(os.environ, SAGA_A=T.I["a"]["url"], SAGA_B=T.I["b"]["url"], SAGA_C=T.I["c"]["url"], SAGA_USER_A="fa-anna",
                       SAGA_USER_B="fb-bert", SAGA_USER_C="fc-dora", SAGA_PASS="Fed-User-Pass-123")
            r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_fed_raum.js")], env=env, timeout=900)
            ok(f"Browser-Lauf browser_fed_raum.js (rc {r})", r == 0)
        else:
            anna, bert, dora = User("a", "fa-anna"), User("b", "fb-bert"), User("c", "fc-dora")
            part_dm(anna, bert, dora)
            if B23:
                anna.devs2 = []
            part_group(anna, bert, dora)
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        for k in T.I:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
    print("PERF " + json.dumps(PERF))
    print(f"\nFOEDERATION-ANRUFE-RAUM {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


if __name__ == "__main__":
    main()
