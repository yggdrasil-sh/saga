"""Anrufe 2.4, Etappe E0 (saga-call v3 im Body, saga/calls.py) - Server-Seite mit PyNaCl als Client (tests/chatlib.py).

Geprueft: GET /api/calls/config meldet body_v 3, features (reneg, pip_hint, ab E4/E5 multi und group) und limits; die
Foederations-Faehigkeit calls3 ist ab E4 gesetzt (Raum: tests/anrufe_raum.py). Faehigkeiten-Hinweis caps beim Anrufen/Annehmen (gefiltert, begrenzt, in GET
sichtbar, nicht bei Unbeteiligten). Neuverhandlung: mehrere offer/answer waehrend des Gespraechs in beide Richtungen,
gleichzeitige Angebote (Glare - der Server reicht beide weiter, die Clients loesen es per Perfect Negotiation), Reihenfolge
(seq) bleibt. ICE-Restart-Signalfluss: Angebot mit neuem ice-ufrag, Kandidaten mit usernameFragment vor der Antwort,
Antwort, Abruf per ?after= (ohne Live-Kanal). Kompatibilitaet v2<->v3: Anruf ohne caps (2.1/2.3-Client) wie bisher, Body
ohne pc/n wird unveraendert weitergereicht, lokale Anrufe tragen weiter KEIN "v" (Web: v === 2 heisst foederiert).
Grenzen: Signal-Budget waechst mit der Gespraechsdauer (sig_budget), harte Obergrenze, Rate je Geraet, Groesse.
Migration 10: Spalten v/topo/kind/caps_a/caps_b mit Standardwerten. Der Server sieht keine SDP (auch nicht im Log).
Wegwerf-Konten ca-*; raeumt auf (wie tests/anrufe.py)."""
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402  (Helfer: Person, dm, Stream, req, seal_for, ...)
import auth  # noqa: E402
import chatlib as C  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
SDP_MARK = "saga-v3-sdp-marke"


def sdp(ufrag, extra=""):
    return ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=" + SDP_MARK + "\r\nt=0 0\r\n"
            "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB:3D:17:6E:B4:2A:7C:4F:0D:2E:F1:A8:B3\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=ice-ufrag:" + ufrag + "\r\na=ice-pwd:x" + ufrag + "x\r\n" + extra)


def body(t, n, pc, ufrag="uf1", **kw):
    s = sdp(ufrag)
    return json.dumps(dict({"t": t, "sdp": s, "fp": C.sdp_fingerprints(s), "n": n, "pc": pc, "caps": ["reneg", "want", "bye"]}, **kw),
                      separators=(",", ":"))


def sig(frm, dev, cid, to, to_dev, b):
    return {to_dev["id"]: C.seal_signal(cid, frm.uid, dev, to.uid, to_dev["id"], to_dev["enc_pk"], b)}


def post_sig(frm, dev, cid, to, to_dev, b):
    return req("POST", f"/api/calls/{cid}/signal", frm.k, {"device": dev["id"], "signals": sig(frm, dev, cid, to, to_dev, b)})


def fetch(me, dev, cid, frm, frm_dev, after=0):
    """-> [(seq, body-dict)] fuer mein Geraet (geoeffnet und geprueft wie ein Client)"""
    s, d = req("GET", f"/api/calls/{cid}?device={dev['id']}&after={after}", me.k)
    out = []
    for x in d.get("signals", []):
        b = A_.open_ok(me, dev, cid, frm, frm_dev, x)
        out.append((x["seq"], b))
    return out


def start(a, b, chat, caps=("reneg", "want", "bye"), media="video"):
    cid = secrets.token_hex(16)
    pc = lambda d: ":".join(sorted([a.dev["id"], d["id"]]))  # noqa: E731
    sigs = {}
    for d in b.devs:
        sigs.update(sig(a, a.dev, cid, b, d, body("offer", 1, pc(d)) if caps is not None else A_.offer_body(media)))
    pl = {"id": cid, "chat_id": chat, "media": media, "device": a.dev["id"], "signals": sigs}
    if caps is not None:
        pl["caps"] = list(caps)
    s, d = req("POST", "/api/calls", a.k, pl)
    return cid, s, d


def answer(b, cid, a, caps=("reneg", "want")):
    pc = ":".join(sorted([a.dev["id"], b.dev["id"]]))
    pl = {"device": b.dev["id"], "signals": sig(b, b.dev, cid, a, a.dev, body("answer", 1, pc) if caps is not None
                                                  else json.dumps({"t": "answer", "sdp": "v=0"}))}
    if caps is not None:
        pl["caps"] = list(caps)
    return req("POST", f"/api/calls/{cid}/answer", b.k, pl)


def end(a, cid):
    req("POST", f"/api/calls/{cid}/end", a.k, {"reason": "hangup", "connected": True})


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "",
                                "turn_user": "", "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45})
    for u, n in (("ca-v3a", "Anna Drei"), ("ca-v3b", "Bert Drei"), ("ca-v3c", "Cora Drei")):
        auth.create_user(u, A_.PW, n)
    time.sleep(A_.WAIT)
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
    print(f"\nANRUFE-V3 {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    import db
    from saga import calls as CL, federation as F
    A, B, Cc = A_.Person("ca-v3a"), A_.Person("ca-v3b"), A_.Person("ca-v3c")
    ab = A_.dm(A, B)

    # --- Migration 10, Konfiguration -------------------------------------------------------------------------------
    with connect() as c:
        cols = {r[0]: r[1] for r in c.execute("SELECT column_name, column_default FROM information_schema.columns "
                                              "WHERE table_schema='public' AND table_name='chat_call'").fetchall()}
        ver = db.schema_version(c)
    ok(f"Migration 10: chat_call hat v/topo/kind/caps_a/caps_b ({sorted(k for k in cols if k in ('v', 'topo', 'kind', 'caps_a', 'caps_b'))})",
       all(k in cols for k in ("v", "topo", "kind", "caps_a", "caps_b")))
    ok(f"SCHEMA_VERSION >= 10, Datenbank auf dem Stand ({db.SCHEMA_VERSION}, Datenbank {ver})", db.SCHEMA_VERSION >= 10 and 10 in db.MIGRATIONS
       and ver == db.SCHEMA_VERSION)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Konfiguration: body_v 3, features reneg + pip_hint + screen2 + multi + group ({cf.get('body_v')}, {cf.get('features')})",
       s == 200 and cf.get("body_v") == 3 and set(cf.get("features") or []) >= {"reneg", "pip_hint", "screen2", "multi", "group"}
       and cf.get("format") == "saga-call/v1")
    lim = cf.get("limits") or {}
    ok(f"... limits: Endpunkte 4/6 (Mesh, E4/E5), Signal-Budget, Rate, Groesse ({lim})",
       lim.get("endpoints_video") == 4 and lim.get("endpoints_audio") == 6 and lim.get("signals_max") == CL.SIGNALS_MAX
       and lim.get("signals_per_min") == CL.SIG_PER_MIN and lim.get("signals_hard") == CL.SIGNALS_HARD and lim.get("sig_rate") == CL.SIG_RATE
       and lim.get("sealed_max") == CL.SEALED_MAX)
    ok("Foederation: Faehigkeit calls3 angeboten (E4/E5), calls2 bleibt", "calls3" in F.CAPS2 and "calls2" in F.CAPS2)

    # --- Faehigkeiten-Hinweis ---------------------------------------------------------------------------------------
    sA = A_.Stream(A.k)
    time.sleep(0.5)
    cid = secrets.token_hex(16)
    pc_ab = ":".join(sorted([A.dev["id"], B.dev["id"]]))
    bad_caps = ["reneg", "want", "RENEG", "<script>", "x" * 40, 7, "reneg", "bye"] + ["c%d" % i for i in range(30)]
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": ab, "media": "video", "device": A.dev["id"],
                                           "signals": sig(A, A.dev, cid, B, B.dev, body("offer", 1, pc_ab)), "caps": bad_caps})
    cc = (d.get("call") or {}).get("caps") or {}
    ok(f"Anrufen mit caps: gefiltert (nur [a-z0-9_], ohne Doppelte, hoechstens 16) ({s}, {cc.get('caller')})",
       s == 200 and cc.get("caller", [])[:3] == ["reneg", "want", "bye"] and len(cc["caller"]) == 16 and cc.get("callee") == [])
    ok("Lokaler Anruf traegt weiter KEIN v (Web: v === 2 heisst foederiert)", "v" not in d.get("call", {}))
    with connect() as c:
        row = c.execute("SELECT v, topo, kind FROM chat_call WHERE id=?", (cid,)).fetchone()
    ok(f"Neuer Anruf: v=1, topo=p2p, kind=dm ({row})", tuple(row) == (1, "p2p", "dm"))
    s, g = req("GET", f"/api/calls/{cid}", B.k)
    ok("B sieht die caps des Anrufers", g["call"]["caps"]["caller"][:3] == ["reneg", "want", "bye"])
    ok("Unbeteiligte: 404", req("GET", f"/api/calls/{cid}", Cc.k)[0] == 404)
    s, d = answer(B, cid, A)
    ok(f"Annehmen mit caps -> beim Anrufer sichtbar ({s}, {d.get('call', {}).get('caps')})", s == 200 and d["call"]["caps"]["callee"] == ["reneg", "want"])
    first = fetch(A, A.dev, cid, B, B.dev)
    ok("Antwort (n=1, pc, caps) im signierten Body beim Anrufer", first and first[-1][1]["t"] == "answer" and first[-1][1]["n"] == 1
       and first[-1][1]["pc"] == pc_ab and first[-1][1]["caps"] == ["reneg", "want", "bye"])
    last_a = max(x[0] for x in first)

    # --- Neuverhandlung in beide Richtungen ------------------------------------------------------------------------
    mA = sA.mark()
    codes = []
    for n in range(2, 8):
        frm, fdev, to, tdev = (B, B.dev, A, A.dev) if n % 2 == 0 else (A, A.dev, B, B.dev)
        codes.append(post_sig(frm, fdev, cid, to, tdev, body("offer", n, pc_ab, "r%d" % n))[0])
        codes.append(post_sig(to, tdev, cid, frm, fdev, body("answer", n, pc_ab, "r%d" % n))[0])
    ok(f"Neuverhandlung: 6 Runden offer/answer waehrend des Gespraechs, abwechselnd beide Seiten ({codes})", codes == [200] * 12)
    ga = fetch(A, A.dev, cid, B, B.dev, last_a)
    gb = fetch(B, B.dev, cid, A, A.dev, 0)
    seq_a = [x[0] for x in ga]
    ok(f"... beim Anrufer: alle offer/answer des Gegenuebers in Reihenfolge ({[(b['t'], b['n']) for _, b in ga]})",
       [(b["t"], b["n"]) for _, b in ga] == [("offer", 2), ("answer", 3), ("offer", 4), ("answer", 5), ("offer", 6), ("answer", 7)]
       and seq_a == sorted(seq_a) and all(b for _, b in ga))
    ok("... beim Angerufenen ebenso (inkl. erstem Angebot)", [(b["t"], b["n"]) for _, b in gb][-6:] ==
       [("answer", 2), ("offer", 3), ("answer", 4), ("offer", 5), ("answer", 6), ("offer", 7)])
    ev = sA.wait("call.signal", lambda x: x["call_id"] == cid, mA)
    ok(f"... live (call.signal) ohne Inhalt ausser dem versiegelten Text ({sorted(ev or {})})",
       ev and set(ev) == {"call_id", "seq", "from_device", "to_device", "sealed"})
    # Glare: beide bieten gleichzeitig an - der Server reicht beide weiter
    s1 = post_sig(A, A.dev, cid, B, B.dev, body("offer", 8, pc_ab, "ga"))[0]
    s2 = post_sig(B, B.dev, cid, A, A.dev, body("offer", 8, pc_ab, "gb"))[0]
    ga2, gb2 = fetch(A, A.dev, cid, B, B.dev, seq_a[-1]), fetch(B, B.dev, cid, A, A.dev, max(x[0] for x in gb))
    ok("Glare: gleichzeitige Angebote beider Seiten werden beide zugestellt (Aufloesung im Client, hoeflich = groessere device_id)",
       s1 == s2 == 200 and ga2[-1][1]["t"] == "offer" and gb2[-1][1]["t"] == "offer")

    # --- ICE-Restart-Signalfluss ------------------------------------------------------------------------------------
    after_b = gb2[-1][0]
    cand = lambda uf, i: {"candidate": f"candidate:{i} 1 udp 2122260223 10.0.0.{i} 5{i}000 typ host", "sdpMid": "0", "sdpMLineIndex": 0,  # noqa: E731
                          "usernameFragment": uf}
    st = [post_sig(A, A.dev, cid, B, B.dev, body("offer", 9, pc_ab, "neu9", restart=True))[0],
          post_sig(A, A.dev, cid, B, B.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": [cand("neu9", 1), cand("neu9", 2)]}))[0]]
    gb3 = fetch(B, B.dev, cid, A, A.dev, after_b)
    ok(f"ICE-Restart: Angebot (restart, neuer ice-ufrag) und Kandidaten mit usernameFragment kommen in Reihenfolge an ({st})",
       st == [200, 200] and [b["t"] for _, b in gb3] == ["offer", "ice"] and gb3[0][1].get("restart") is True
       and "a=ice-ufrag:neu9" in gb3[0][1]["sdp"] and gb3[1][1]["c"][0]["usernameFragment"] == "neu9")
    st = [post_sig(B, B.dev, cid, A, A.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": [cand("neub", 3)]}))[0],   # Kandidat VOR der Antwort
          post_sig(B, B.dev, cid, A, A.dev, body("answer", 9, pc_ab, "neub"))[0]]
    ga3 = fetch(A, A.dev, cid, B, B.dev, ga2[-1][0])
    ok("... Gegenrichtung: Kandidat vor der Antwort, dann Antwort (n=9) - ohne Live-Kanal per ?after abholbar",
       st == [200, 200] and [(b["t"], b.get("n")) for _, b in ga3] == [("ice", None), ("answer", 9)])
    st = post_sig(B, B.dev, cid, A, A.dev, json.dumps({"t": "state", "pc": pc_ab, "mic": True, "cam": True, "screen": False, "pip": True,
                                                        "want": {"video": "small"}, "path": "relay-tcp"}))[0]
    st2 = post_sig(B, B.dev, cid, A, A.dev, json.dumps({"t": "bye", "pc": pc_ab, "reason": "hangup"}))[0]
    ga4 = fetch(A, A.dev, cid, B, B.dev, ga3[-1][0])
    ok("state (pip, want, path) und bye werden wie jedes Signal weitergereicht", st == st2 == 200
       and ga4[0][1]["want"] == {"video": "small"} and ga4[1][1]["t"] == "bye")
    # --- E3: Bildschirm v2 (zweite Videospur) - realistisch grosses Angebot mit 3 m-Zeilen, tracks/msid, screen_mid -------
    big = "".join(f"a=rtpmap:{96 + i} VP8/90000\r\na=rtcp-fb:{96 + i} nack pli\r\na=extmap:{i % 14 + 1} urn:ietf:params:rtp-hdrext:x{i}\r\n" for i in range(70))
    s3 = sdp("scr1", "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:1\r\n" + big + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:2\r\na=sendrecv\r\n"
             "a=msid:scrstream scrtrack\r\n" + big)
    b3 = json.dumps({"t": "offer", "pc": pc_ab, "n": 10, "sdp": s3, "fp": C.sdp_fingerprints(s3), "caps": ["reneg", "want", "bye", "screen2"],
                     "media": "video", "tracks": {"0": "mic", "1": "cam", "2": "screen"}, "msid": {"0": "camstream", "1": "camstream", "2": "scrstream"}},
                    separators=(",", ":"))
    sealed_len = len(sig(A, A.dev, cid, B, B.dev, b3)[B.dev["id"]])
    st = [post_sig(A, A.dev, cid, B, B.dev, b3)[0],
          post_sig(A, A.dev, cid, B, B.dev, json.dumps({"t": "state", "pc": pc_ab, "mic": True, "cam": True, "screen": True, "pip": False,
                                                        "want": {"video": "large"}, "tracks": {"0": "mic", "1": "cam", "2": "screen"},
                                                        "msid": {"2": "scrstream"}, "screen_mid": "2", "screen_audio": True}))[0]]
    gb4 = fetch(B, B.dev, cid, A, A.dev, gb3[-1][0])
    ok(f"Bildschirm v2: Angebot mit 3 m-Zeilen ({len(s3)} Zeichen SDP, versiegelt {sealed_len} <= {CL.SEALED_MAX}) und state mit "
       f"screen_mid/tracks/msid kommen unveraendert an ({st})",
       st == [200, 200] and sealed_len <= CL.SEALED_MAX and len(s3) > 8000 and gb4[0][1]["tracks"]["2"] == "screen"
       and gb4[1][1]["screen_mid"] == "2" and gb4[1][1]["msid"] == {"2": "scrstream"})
    hits = A_.plaintext_hits(SDP_MARK, "neu9", "10.0.0.1")
    ok(f"Server sieht keine SDP/Kandidaten (nirgends in der Datenbank) ({hits})", not hits)

    # --- Grenzen -------------------------------------------------------------------------------------------------------
    now = time.time()
    call = {"answered": None}
    ok("sig_budget: vor der Annahme Grundbudget", CL.sig_budget(call, now) == CL.SIGNALS_MAX)
    ok("sig_budget: + SIG_PER_MIN je angefangener Gespraechsminute",
       CL.sig_budget({"answered": now - 1}, now) == CL.SIGNALS_MAX + CL.SIG_PER_MIN
       and CL.sig_budget({"answered": now - 10 * 60 - 1}, now) == CL.SIGNALS_MAX + 11 * CL.SIG_PER_MIN)
    ok("sig_budget: harte Obergrenze", CL.sig_budget({"answered": now - 24 * 3600}, now) == CL.SIGNALS_HARD)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET nsig=?, answered=? WHERE id=?", (CL.SIGNALS_MAX + 50, now - 5 * 60, cid))
    s = post_sig(A, A.dev, cid, B, B.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": []}))[0]
    ok(f"Langes Gespraech (5 min): mehr als das alte Limit 600 erlaubt ({s})", s == 200)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (CL.SIGNALS_MAX + 6 * CL.SIG_PER_MIN, cid))
    s = post_sig(A, A.dev, cid, B, B.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": []}))[0]
    ok(f"... Budget erschoepft -> 429 ({s})", s == 429)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET nsig=?, answered=? WHERE id=?", (CL.SIGNALS_HARD, now - 6 * 3600, cid))
    s = post_sig(B, B.dev, cid, A, A.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": []}))[0]
    ok(f"... harte Obergrenze auch bei sehr langem Gespraech -> 429 ({s})", s == 429)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET nsig=10, answered=? WHERE id=?", (now, cid))
    big = {B.dev["id"]: __import__("base64").b64encode(os.urandom(CL.SEALED_MAX)).decode()}
    ok("Zu grosses Signal (Angebot mit vielen Spuren) -> 400", req("POST", f"/api/calls/{cid}/signal", A.k, {"device": A.dev["id"], "signals": big})[0] == 400)
    codes = [post_sig(A, A.dev, cid, B, B.dev, json.dumps({"t": "ice", "pc": pc_ab, "c": []}))[0] for _ in range(CL.SIG_RATE + 3)]
    ok(f"Rate je Geraet bleibt ({CL.SIG_RATE}/min) -> danach 429 ({codes.count(200)}x 200, {codes.count(429)}x 429)",
       codes.count(200) <= CL.SIG_RATE and codes[-1] == 429)
    ok("Fremdes Geraet kann nicht signalisieren (403)", post_sig(A, B.dev, cid, B, B.dev, json.dumps({"t": "ice"}))[0] == 403)
    end(A, cid)
    ok("Nach dem Ende: keine Neuverhandlung mehr (409)", post_sig(B, B.dev, cid, A, A.dev, body("offer", 20, pc_ab))[0] == 409)

    # --- Kompatibilitaet: 2.1/2.3-Client ohne caps ------------------------------------------------------------------------
    # (anderes anrufendes Geraet: die Rate von A ist nach der Grenzen-Pruefung fuer diese Minute erschoepft)
    cb = A_.dm(Cc, B)
    cid2, s, d = start(Cc, B, cb, caps=None, media="audio")
    ok(f"Anruf ohne caps (2.3-Client): wie bisher, caps leer ({s})", s == 200 and d["call"]["caps"] == {"caller": [], "callee": []})
    s, act = req("GET", "/api/calls/active", B.k)
    ok("/api/calls/active traegt caps", s == 200 and [x["caps"] for x in act["calls"] if x["id"] == cid2] == [{"caller": [], "callee": []}])
    s, d = answer(B, cid2, Cc, caps=None)
    ok("Annehmen ohne caps (2.3-Client): aktiv, caps leer", s == 200 and d["call"]["state"] == "active" and d["call"]["caps"]["callee"] == [])
    s = post_sig(Cc, Cc.dev, cid2, B, B.dev, A_.ice_body())[0]
    gb = fetch(B, B.dev, cid2, Cc, Cc.dev, 0)
    ok("Body ohne pc/n (2.3) wird unveraendert weitergereicht", s == 200 and gb[-1][1] == json.loads(A_.ice_body()))
    # gemischt: 2.4-Anrufer, 2.3-Angerufener (Neuverhandlung nur, wenn reneg gemeldet - das entscheidet der Client)
    s, g = req("GET", f"/api/calls/{cid2}", Cc.k)
    ok("Gemischt: der 2.4-Client erkennt am Hinweis, dass das Gegenueber nichts meldet (callee caps leer)", g["call"]["caps"]["callee"] == [])
    end(Cc, cid2)


if __name__ == "__main__":
    main()
