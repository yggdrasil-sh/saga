"""VoIP-Push (PushKit, Abo apns-voip) fuer klingelnde Anrufe beider Arten (saga/push.py _route, message).

Seit calls.horn_all laufen auch 1:1-Anrufe ueber Knoten und klingeln per Push-Art "call.group" mit dm: true. Geprueft:
_route: "call" und "call.group" -> VoIP-Abo, das normale APNs-Abo desselben Geraets nicht; Geraet ohne VoIP-Abo (iPad)
bekommt APNs; alle anderen Arten nie ueber VoIP. Zustellung an eine APNs-Attrappe: 1:1 ueber Knoten -> genau ein VoIP-Push
(Topic .voip, kind call.group, dm, call, chat, media, caller, title, body, url), kein Alert am selben iPhone; Gruppenanruf
klingelnd -> VoIP mit group ohne dm; Einladung 'old' (Geraet ohne multi) und grosser Chat (still) -> nichts; nach dem
Annehmen bzw. bei verspaetetem Auftrag liefert message() nichts (kein leerer CallKit-Anruf). Wegwerf-Konten ca-v*."""
import http.server
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_horn_dm as HD  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
FP = int(os.environ.get("HK_TEST_FAKE_APNS", "18461"))
CAPS23 = ["reneg", "want", "bye", "screen2"]
TOK = {"voip": "d1" * 32, "apns": "d2" * 32, "ipad": "d3" * 32, "c-voip": "d4" * 32, "d-voip": "d5" * 32}
USERS = [("ca-va", "Vera Anruf"), ("ca-vb", "Volker Anruf"), ("ca-vc", "Vicky Anruf"), ("ca-vd", "Vito Alt")] + \
        [(f"ca-vx{i}", f"Viele {i}") for i in range(8)]


def sub(c, uid, kind, tok, did):
    c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, device_id, created) VALUES (?,?,?,?,?,?)",
              (uid, kind, f"{kind}:production:{tok}", kind, did, time.time()))


def device(c, uid, name):
    import hashlib
    return c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?,?,?,?,?,?)",
                     (uid, uid, name, hashlib.sha256(os.urandom(16)).hexdigest(), "hkd_test", time.time())).lastrowid


def hits(tok, since):
    with A_.LOCK:
        return [g for g in A_.GOT[since:] if g[0] == "/3/device/" + tok]


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    with connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()
    keep_apns = r[0] if r else None
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), A_.Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        run()
    finally:
        for x in A_.STREAMS:
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
        HD.node(False)
        A_.cleanup()
    print(f"\nANRUFE-VOIP {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from saga import push as P
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                serialization.NoEncryption()).decode()
    with connect() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('apns', ?)", (json.dumps({
            "enabled": True, "team_id": "ABCDE12345", "key_id": "KEY1234567", "bundle_id": "org.example.saga", "p8": auth._enc(pem),
            "url": f"http://127.0.0.1:{FP}"}),))
    P_ = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D = (P_[u] for u in ("ca-va", "ca-vb", "ca-vc", "ca-vd"))
    for u, p in P_.items():
        HD.caps(p, CAPS23 if u == "ca-vd" else HD.CAPS)
    with connect() as c, c:
        iphone, ipad = device(c, B.uid, "iPhone"), device(c, B.uid, "iPad")
        sub(c, B.uid, "apns-voip", TOK["voip"], iphone)
        sub(c, B.uid, "apns", TOK["apns"], iphone)
        sub(c, B.uid, "apns", TOK["ipad"], ipad)                    # iPad ohne VoIP-Abo (z. B. aeltere App)
        sub(c, Cc.uid, "apns-voip", TOK["c-voip"], device(c, Cc.uid, "iPhone"))
        sub(c, D.uid, "apns-voip", TOK["d-voip"], device(c, D.uid, "iPhone"))
    HD.setting(horn=True, horn_all=True)                            # wartet auch die 5 s Zwischenspeicher ab
    HD.node(True)

    # --- _route -----------------------------------------------------------------------------------------------------
    with connect() as c:
        kinds = dict(c.execute("SELECT id, endpoint FROM push_sub WHERE uid=?", (B.uid,)).fetchall())
        ids = sorted(kinds)

        def route(k):
            return sorted(kinds[i].split(":")[0] + ("/ipad" if TOK["ipad"] in kinds[i] else "") for i in P._route(c, ids, k))
        r_call, r_grp = route("call"), route("call.group")
        r_other = {k: route(k) for k in ("chat.message", "call.missed", "comment.new", "share.added", "test")}
    ok(f"_route call: VoIP + iPad-APNs, nicht das APNs am iPhone ({r_call})", r_call == ["apns-voip", "apns/ipad"])
    ok(f"_route call.group: genauso ({r_grp})", r_grp == ["apns-voip", "apns/ipad"])
    ok(f"_route: andere Arten nie ueber VoIP ({r_other})", all(v == ["apns", "apns/ipad"] for v in r_other.values()))

    # --- 1:1 ueber Knoten: genau ein VoIP-Push ------------------------------------------------------------------------
    ab = A_.dm(A, B)
    m0 = A_.mark()
    cid, s, d = HD.hcall(A, ab, media="video")
    ok(f"1:1 ueber Knoten angelegt (kind group, dm) ({s}, {(d.get('call') or {}).get('dm')})", s == 200 and d["call"].get("dm") is True)
    g = A_.got(lambda x: x[0] == "/3/device/" + TOK["voip"], m0)
    hv, pv = (g[1], json.loads(g[2])) if g else ({}, {})
    ok(f"VoIP-Push: push-type voip, Topic .voip, kurze Frist ({hv.get('apns-push-type')}, {hv.get('apns-topic')})",
       hv.get("apns-push-type") == "voip" and hv.get("apns-topic") == "org.example.saga.voip"
       and 0 < int(hv.get("apns-expiration", "0")) - time.time() < 75 and hv.get("apns-collapse-id") == "call-" + cid)
    want = ("kind", "title", "body", "url", "call", "chat", "media", "caller", "group", "dm")
    ok(f"VoIP-Body: Vertrag {want} vollstaendig ({pv})",
       all(k in pv for k in want) and pv["kind"] == "call.group" and pv["call"] == cid and pv["chat"] == ab and pv["media"] == "video"
       and pv["dm"] is True and pv["group"] is True and pv["caller"] == "Vera Anruf" and pv["url"] == f"/chats#call-{cid}"
       and "Gruppen" not in pv["body"] and pv["aps"] == {})
    time.sleep(2.5)
    ok(f"... genau einer am iPhone-VoIP ({len(hits(TOK['voip'], m0))})", len(hits(TOK["voip"], m0)) == 1)
    ok(f"... kein zusaetzlicher Alert am selben iPhone ({len(hits(TOK['apns'], m0))})", not hits(TOK["apns"], m0))
    gi = hits(TOK["ipad"], m0)
    pi = json.loads(gi[0][2]) if gi else {}
    ok(f"... iPad ohne VoIP-Abo: normale Mitteilung (time-sensitive) ({len(gi)}, {(pi.get('aps') or {}).get('interruption-level')})",
       len(gi) == 1 and gi[0][1].get("apns-push-type") == "alert" and pi["aps"].get("interruption-level") == "time-sensitive")
    HD.fresh()
    s, _ = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": HD.CAPS})
    with connect() as c:
        pm = P.message(c, B.uid, "call.group", actor=A.uid, data={"chat": ab, "call": cid}, sub_kind="apns-voip")
    ok(f"angenommen (anderes eigenes Geraet): message() -> nichts, kein spaeter VoIP-Push ({s}, {pm})", s == 200 and pm is None)
    req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup"})
    cid, s, d = HD.hcall(A, ab)
    with connect() as c:
        late = P.message(c, B.uid, "call.group", actor=A.uid, data={"chat": ab, "call": cid}, sub_kind="apns-voip",
                         created=time.time() - 300)
        now_ = P.message(c, B.uid, "call.group", actor=A.uid, data={"chat": ab, "call": cid}, sub_kind="apns-voip", created=time.time())
    ok(f"verspaeteter Auftrag (aelter als die Klingeldauer): nichts; frischer: Anruf ({late}, {now_ and now_.get('kind')})",
       late is None and now_ and now_["dm"] is True)
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})

    # --- Gruppenanruf klingelnd ----------------------------------------------------------------------------------------
    time.sleep(1.0)
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [B.uid, Cc.uid, D.uid]})
    gid = d["chat"]["id"]
    m0 = A_.mark()
    g1 = HD.secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": HD.CAPS})
    with connect() as c:
        inv = dict(c.execute("SELECT who, state FROM chat_call_invite WHERE call_id=?", (g1,)).fetchall())
    ok(f"Gruppenanruf: klingelt bei B und C, D (ohne multi) 'old' ({s}, {inv})",
       s == 200 and inv.get(B.uid) == "ringing" and inv.get(Cc.uid) == "ringing" and inv.get(D.uid) == "old")
    g = A_.got(lambda x: x[0] == "/3/device/" + TOK["c-voip"], m0)
    pv = json.loads(g[2]) if g else {}
    ok(f"Gruppenanruf -> VoIP an C: kind call.group, group, ohne dm, call/media/caller ({pv})",
       g and g[1].get("apns-push-type") == "voip" and pv.get("kind") == "call.group" and pv.get("group") is True and "dm" not in pv
       and pv.get("call") == g1 and pv.get("media") == "audio" and pv.get("chat") == gid and pv.get("caller") == "Vera Anruf"
       and pv.get("title") and pv.get("body") and pv.get("url"))
    time.sleep(2.5)
    ok(f"... B: VoIP am iPhone, kein Alert dort ({len(hits(TOK['voip'], m0))}, {len(hits(TOK['apns'], m0))})",
       len(hits(TOK["voip"], m0)) == 1 and not hits(TOK["apns"], m0))
    ok(f"... D (Einladung 'old'): kein VoIP-Push ({len(hits(TOK['d-voip'], m0))})", not hits(TOK["d-voip"], m0))
    with connect() as c:
        pd = P.message(c, D.uid, "call.group", actor=A.uid, data={"chat": gid, "call": g1}, sub_kind="apns-voip")
    ok(f"... message() fuer 'old' -> nichts ({pd})", pd is None)
    req("POST", f"/api/calls/{g1}/end", A.k, {})

    # --- grosser Chat: still, kein Push ---------------------------------------------------------------------------------
    time.sleep(1.0)
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS if u != A.uid]})
    gid2 = d["chat"]["id"]
    m0 = A_.mark()
    g2 = HD.secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g2, "chat_id": gid2, "media": "audio", "device": A.dev["id"], "caps": HD.CAPS})
    ok(f"grosser Chat ({len(USERS)} Mitglieder): Anruf angelegt ({s})", s == 200)
    time.sleep(3.0)
    ok(f"... kein Push an B/C (weder VoIP noch APNs) ({[len(hits(TOK[k], m0)) for k in ('voip', 'apns', 'ipad', 'c-voip')]})",
       not any(hits(TOK[k], m0) for k in ("voip", "apns", "ipad", "c-voip")))
    with connect() as c:
        q = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid IN (?,?) AND kind='call.group'", (B.uid, Cc.uid)).fetchone()[0]
    ok(f"... und kein Auftrag in der Warteschlange ({q})", q == 0)
    req("POST", f"/api/calls/{g2}/end", A.k, {})


if __name__ == "__main__":
    main()
