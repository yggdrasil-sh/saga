"""Gjallarhorn P4 (Frame-E2E, docs/HORN-E2E.md) - Server-Seite: Feature-Flag und Schluesselverteilung fkey/fkey-ack im
vorhandenen versiegelten Signalweg (saga/calls.py), mit PyNaCl als Client (tests/chatlib.py).

Geprueft: Flag calls.horn aus (Standard) -> keine Faehigkeit horn (GET /api/calls/config features, kein e2e, Foederation
caps2 ohne horn); Flag an -> features mit horn, e2e saga-horn/v1, caps2 mit horn. Gruppenanruf (Raum, 4 Geraete von drei
Personen, eines als Begleitgeraet): jedes Geraet verteilt seinen Frame-Schluessel als fkey an ALLE anderen in EINER
Anfrage (versiegelt je Zielgeraet, signiert), Empfaenger oeffnen, pruefen die Signatur und lesen n/kid/key; fkey-ack
gebuendelt zurueck; Beitritt eines weiteren Geraets: nur der Neue bekommt die geratschten Schluessel; Verlassen:
Neuschluessel nur an die Verbliebenen - ein fkey an das gegangene Geraet wird abgewiesen (400), ebenso an ein Geraet
ausserhalb des Anrufs. Der Server sieht keinen Schluessel (Datenbank-Suche nach Hex/B64) und keinen Typ. Messung:
Neuschluessel-Runde (jedes Geraet an alle anderen) mit Laufzeit. Wegwerf-Konten ca-h*; raeumt auf."""
import base64
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import anrufe as A_  # noqa: E402  (Helfer: Person, req, ...)
import auth  # noqa: E402
import chatlib as C  # noqa: E402
import horn_e2e_vectors as HV  # noqa: E402  (Referenz: Ratsche, fkey-Pruefung)

ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
PERF = {}
KEYS = []                                             # alle verteilten Schluessel (fuer die Datenbank-Suche)


def fkey(n, k, s):
    KEYS.append(k)
    return json.dumps({"t": "fkey", "n": n, "kid": n % 16, "key": base64.b64encode(k).decode(), "s": s}, separators=(",", ":"))


def send(frm, fdev, cid, targets, body_of):
    """Ein POST /signal an mehrere Zielgeraete: {zielgeraet: versiegelt}. targets = [(person, geraet)]"""
    sig = {d["id"]: C.seal_signal(cid, frm.uid, fdev, to.uid, d["id"], d["enc_pk"], body_of(to, d)) for to, d in targets}
    return req("POST", f"/api/calls/{cid}/signal", frm.k, {"device": fdev["id"], "signals": sig})


def join(p, dv, cid, role, legs_now):
    """Beitreten wie im Mesh 2.4 (Angebote an alle Endpunkte; der Knoten-Weg ohne Mesh-SDP kommt mit P3)."""
    offer = lambda to, td: json.dumps({"t": "offer", "pc": ":".join(sorted([dv["id"], td["id"]])), "n": 1, "caps": CAPS, "sdp": "v=0", "fp": []})  # noqa: E731
    sig = {d["id"]: C.seal_signal(cid, p.uid, dv, to.uid, d["id"], d["enc_pk"], offer(to, d)) for to, d in legs_now}
    return req("POST", f"/api/calls/{cid}/join", p.k, {"device": dv["id"], "role": role, "caps": CAPS, "signals": sig})


def inbox(me, dev, cid, peers, after=0):
    """Signale fuer mein Geraet, geoeffnet und gegen das Geraete-Zertifikat des Absenders geprueft -> [(von, body)]"""
    s, d = req("GET", f"/api/calls/{cid}?device={dev['id']}&after={after}", me.k)
    out = []
    for x in d.get("signals", []):
        frm, fdev = peers[x["from_device"]]
        b, sg = C.open_signal(x["sealed"], dev["enc_pk"], dev["enc_sk"])
        if C.verify_signal(cid, frm.uid, fdev["id"], me.uid, dev["id"], b, sg, fdev["sign_pk"]):
            out.append((x["from_device"], json.loads(b), x["seq"]))
    return out


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(CALLS))
    for u, n in (("ca-ha", "Anna Horn"), ("ca-hb", "Bert Horn"), ("ca-hc", "Cora Horn"), ("ca-hd", "Dora Horn"), ("ca-hx", "Xaver Fremd")):
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
    print("PERF " + json.dumps(PERF))
    print(f"\nANRUFE-HORN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def flag(on):
    auth.save_setting("calls", dict(CALLS, horn=on))
    auth._settings_cache["v"] = None
    time.sleep(A_.WAIT)


def run():
    from saga import calls as CL, federation as F
    A, B, Cc, D, X = (A_.Person(u) for u in ("ca-ha", "ca-hb", "ca-hc", "ca-hd", "ca-hx"))
    A2 = A.add_device()
    for p in (A, B, Cc, D):
        for d in p.devs:
            req("PUT", "/api/calls/caps", p.k, {"device": d["id"], "caps": CAPS})

    # --- Feature-Flag --------------------------------------------------------------------------------------------------
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Flag aus (Standard): keine Faehigkeit horn, kein e2e ({cf.get('features')})", s == 200 and "horn" not in cf["features"] and "e2e" not in cf)
    ok("Flag aus: Foederation meldet kein horn (caps2)", "horn" not in F.caps2() and not CL.horn_on())
    flag(True)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Flag an: features mit horn, e2e saga-horn/v1 ({cf.get('features')}, {cf.get('e2e')})",
       s == 200 and "horn" in cf["features"] and cf.get("e2e") == "saga-horn/v1" and cf["features"][:5] == list(CL.FEATURES))
    ok("Flag an: Foederation meldet horn (caps2, Admin-Schalter calls)", "horn" in F.caps2() and F.CAP_OPT.get("horn") == "calls")
    ok("Geraete-Faehigkeit horn wird als Hinweis angenommen", "horn" in CL.caps_out(CL.caps_in(CAPS)))

    # --- Gruppenanruf: Anna, Bert, Cora + Annas Laptop (Begleitgeraet) ----------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-hb", "ca-hc", "ca-hd"]})
    gid, cid = d["chat"]["id"], secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    ok(f"Gruppenanruf gestartet ({s})", s == 200 and d["call"]["kind"] == "group")
    ends = [(A, A.dev)]
    for p, dv, role in ((B, B.dev, "member"), (Cc, Cc.dev, "member"), (A, A2, "companion")):
        s, d = join(p, dv, cid, role, list(ends))
        ok(f"{p.uid}: tritt bei ({role}, {s})", s == 200)
        ends.append((p, dv))
    peers = {dv["id"]: (p, dv) for p, dv in ends + [(D, D.dev)]}
    sess = {dv["id"]: secrets.token_hex(8) for _, dv in ends + [(D, D.dev)]}
    own = {dv["id"]: (0, secrets.token_bytes(32)) for _, dv in ends}
    t0 = time.time()
    for p, dv in ends:
        n, k = own[dv["id"]]
        s, r = send(p, dv, cid, [x for x in ends if x[1] is not dv], lambda to, td: fkey(n, k, sess[dv["id"]]))
        ok(f"{p.uid}/{dv['id'][:6]}: fkey an alle anderen in einer Anfrage ({s}, n={r.get('n')})", s == 200 and r.get("n") == 3)
    PERF["start_4_geraete_ms"] = round((time.time() - t0) * 1000)
    after = {}
    good = True
    for p, dv in ends:
        got = [(f, b) for f, b, q in inbox(p, dv, cid, peers) if b.get("t") == "fkey"]
        after[dv["id"]] = max([q for _, _, q in inbox(p, dv, cid, peers)] or [0])
        keys = {f: HV.parse_fkey(b) for f, b in got}
        good &= len(keys) == 3 and all(v and v["key"] == own[f][1] and v["n"] == 0 and v["s"] == sess[f] for f, v in keys.items())
    ok("Jedes Geraet hat die fkey der drei anderen: geoeffnet, Signatur geprueft, Schluessel gleich", good)
    acks = {f: json.dumps({"t": "fkey-ack", "n": 0, "s": sess[f], "ok": True}) for f in own}
    s, r = send(B, B.dev, cid, [x for x in ends if x[1] is not B.dev], lambda to, td: acks[td["id"]])
    ok(f"fkey-ack gebuendelt an drei Sender ({s})", s == 200 and r.get("n") == 3)
    a_ack = [b for f, b, q in inbox(A, A.dev, cid, peers, after[A.dev["id"]]) if b.get("t") == "fkey-ack"]
    ok("... bei Anna angekommen", len(a_ack) == 1 and a_ack[0]["n"] == 0 and a_ack[0]["s"] == sess[A.dev["id"]])

    # --- Beitritt: Dora; nur sie bekommt die geratschten Schluessel ----------------------------------------------------
    s, d = join(D, D.dev, cid, "member", list(ends))
    ok(f"Dora tritt bei ({s}, {len(d.get('call', {}).get('legs', []))} Geraete)", s == 200 and len(d["call"]["legs"]) == 5)
    good = True
    for p, dv in ends:
        n, k = own[dv["id"]]
        own[dv["id"]] = (n + 1, HV.ratchet(k))
        s, r = send(p, dv, cid, [(D, D.dev)], lambda to, td, dv=dv: fkey(own[dv["id"]][0], own[dv["id"]][1], sess[dv["id"]]))
        good &= s == 200
    ok("Ratsche: jeder Sender schickt nur Dora sein K' (n=1)", good)
    dk = {f: HV.parse_fkey(b) for f, b, q in inbox(D, D.dev, cid, peers) if b.get("t") == "fkey"}
    ok("Dora: vier geratschte Schluessel, keiner der alten", len(dk) == 4 and all(v["n"] == 1 and v["key"] == own[f][1] for f, v in dk.items()))
    ok("Bert bekommt bei der Ratsche keine neuen fkey (leitet selbst ab)",
       not [b for f, b, q in inbox(B, B.dev, cid, peers, after[B.dev["id"]]) if b.get("t") == "fkey"])
    k_d = secrets.token_bytes(32)
    s, r = send(D, D.dev, cid, ends, lambda to, td: fkey(0, k_d, sess[D.dev["id"]]))
    ok(f"Dora verteilt ihren eigenen Schluessel an alle ({s}, n={r.get('n')})", s == 200 and r.get("n") == 4)

    # --- Verlassen: Cora; Neuschluessel nur an die Verbliebenen ----------------------------------------------------------
    s, d = req("POST", f"/api/calls/{cid}/leave", Cc.k, {"device": Cc.dev["id"]})
    ok(f"Cora verlaesst den Anruf ({s})", s == 200)
    rest = [x for x in ends if x[1] is not Cc.dev] + [(D, D.dev)]
    t0 = time.time()
    good = True
    for p, dv in rest:
        nk = secrets.token_bytes(32)
        n = (own.get(dv["id"]) or (0, None))[0] + 1
        own[dv["id"]] = (n, nk)
        s, r = send(p, dv, cid, [x for x in rest if x[1] is not dv], lambda to, td: fkey(n, nk, sess[dv["id"]]))
        good &= s == 200 and r.get("n") == len(rest) - 1
    PERF["neuschluessel_4_sender_ms"] = round((time.time() - t0) * 1000)
    ok(f"Neuschluessel: vier Sender an je drei Verbliebene ({PERF['neuschluessel_4_sender_ms']} ms)", good)
    s, r = send(A, A.dev, cid, [(Cc, Cc.dev)], lambda to, td: fkey(9, secrets.token_bytes(32), sess[A.dev["id"]]))
    ok(f"fkey an das gegangene Geraet -> abgewiesen ({s})", s == 400)
    s, r = send(A, A.dev, cid, [(X, X.dev)], lambda to, td: fkey(9, secrets.token_bytes(32), sess[A.dev["id"]]))
    ok(f"fkey an ein Geraet ausserhalb des Anrufs -> abgewiesen ({s})", s == 400)
    s, r = send(Cc, Cc.dev, cid, [(A, A.dev)], lambda to, td: fkey(9, secrets.token_bytes(32), sess[Cc.dev["id"]]))
    ok(f"Gegangenes Geraet kann keine fkey mehr senden ({s})", s in (403, 409))

    # --- Datenschutz -------------------------------------------------------------------------------------------------
    needles = [k.hex() for k in KEYS] + [base64.b64encode(k).decode() for k in KEYS] + ['"t":"fkey"', "fkey-ack"]
    ok(f"Server sieht keinen Frame-Schluessel und keinen Signaltyp ({len(KEYS)} Schluessel gesucht)", not A_.plaintext_hits(*needles))
    for p, _ in ends:
        req("POST", f"/api/calls/{cid}/end", p.k, {})
    req("POST", f"/api/calls/{cid}/end", D.k, {})
    flag(False)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok("Flag wieder aus: horn verschwindet", "horn" not in cf["features"])


if __name__ == "__main__":
    main()
