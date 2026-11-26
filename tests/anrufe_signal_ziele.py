"""2.6 (Bugs-DB 2434, Teil von 2263): Zielgrenze je Signal-Anfrage. Ein Geraet buendelt fkey/state an ALLE anderen
Endpunkte in EINER Anfrage (POST /api/calls/{id}/signal, docs/HORN-E2E.md §4) - bis 2.5 lehnte der Server jede Anfrage mit
mehr als chat.DEVICES_MAX (10, Geraete je Konto) Zielen ganz ab (400): ab 11 Zielen kein Frame-Schluessel, kein Bild/Ton.

Ohne echten Knoten: ein gekoppelter Knoten mit UDP als Registereintrag (Lastmeldung frisch, Zertifikat "self") gilt als
bereit (Grenze wie horn.LIMITS, Ton UDP 50) - geprueft wird die Signalisierung in Saga, nicht die Medien.

Geprueft:
- SIG_TARGETS_MAX folgt der Obergrenze ueber Knoten inkl. Testhaken (horn.LIMITS_TOP), 1:1 bleibt bei DEVICES_MAX (in-process).
- Knoten-Anruf (Ton, 13 Personen mit je 4 Geraeten): fkey an 16 Ziele in einer Anfrage -> 200, n 16, jedes Ziel oeffnet
  sein Signal (versiegelt, signiert); bei 50 Endpunkten (Ton-Grenze) fkey an 49 Ziele -> 200, n 49, alle bekommen es; dazu ein
  Geraet ausserhalb des Anrufs -> dropped (wie 2.5). Groesse der Anfrage (PERF).
- Formfehler weiter 400: mehr Ziele als SIG_TARGETS_MAX, leere Liste, Kennung kein Hex, Wert kein B64 bzw. zu kurz,
  kein einziges gueltiges Ziel.
- Budget greift weiter: pairs_budget fast erschoepft -> 429 fuer 49 Ziele, 200 fuer wenige.
- Mesh-Raum (ohne horn): Signal an 3 Endpunkte + 8 fremde Kennungen (11 Ziele) -> 200, dropped 8 (vorher 400).
- 1:1 ohne Raum: Anrufstart mit 11 Zielen -> 400 (Geraete einer Person, unveraendert).
Wegwerf-Konten ca-sz*; raeumt auf."""
import base64
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402  (Helfer: Person, req, ...)
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
import chatlib as C  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_all": True}
NODE = "sz-knoten.example.org"
USERS = [(f"ca-sz{i:02d}", f"Ziel Person {i}") for i in range(1, 14)]
DEVS = 4                                               # Geraete je Person (1 Hauptgeraet + 3 Begleitgeraete)
PERF = {}


def node(on=True):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (NODE,))
        if on:
            nid = secrets.token_hex(8)
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                      "VALUES (?,?,?,0,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                      (nid, NODE, "dGVzdC1rbm90ZW4tc3otMDAwMDAwMDAwMDAwMDAwMDA=", f"https://{NODE}:8443", True, HN.VERSION,
                       json.dumps({"cpu": 0.05, "legs": 0, "max_legs": 200, "state": "ok"}), time.time(), time.time(),
                       json.dumps({"mode": "alpn", "state": "self"})))
            return nid


def fresh():
    with connect() as c, c:
        c.execute("UPDATE horn_node SET seen=? WHERE name=?", (time.time(), NODE))


def fkey(n):
    return json.dumps({"t": "fkey", "n": n, "kid": n % 16, "key": base64.b64encode(secrets.token_bytes(32)).decode(), "s": "x"},
                      separators=(",", ":"))


def sealed(frm, fdev, cid, targets, body):
    return {d["id"]: C.seal_signal(cid, frm.uid, fdev, to.uid, d["id"], d["enc_pk"], body) for to, d in targets}


def send(frm, fdev, cid, sig):
    return req("POST", f"/api/calls/{cid}/signal", frm.k, {"device": fdev["id"], "signals": sig})


def got_from(me, dev, cid, frm, fdev, want):
    """Hat mein Geraet das Signal (Klartext want) von frm/fdev bekommen - geoeffnet und gegen die Signatur geprueft?"""
    s, d = req("GET", f"/api/calls/{cid}?device={dev['id']}&after=0", me.k)
    for x in (d.get("signals") or []) if s == 200 else []:
        if x["from_device"] != fdev["id"]:
            continue
        b, sg = C.open_signal(x["sealed"], dev["enc_pk"], dev["enc_sk"])
        if b == want and C.verify_signal(cid, frm.uid, fdev["id"], me.uid, dev["id"], b, sg, fdev["sign_pk"]):
            return True
    return False


def all_got(cid, frm, fdev, targets, want):
    miss = [d["id"][:6] for p, d in targets if not got_from(p, d, cid, frm, fdev, want)]
    return not miss, miss


def legs(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return {x["device"]: x for x in (d.get("call") or {}).get("legs", [])} if s == 200 else {}


def hjoin(p, dev, cid):
    fresh()
    return req("POST", f"/api/calls/{cid}/join", p.k, {"device": dev["id"], "role": "member", "caps": CAPS})


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(CALLS))
    auth._settings_cache["v"] = None
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    time.sleep(A_.WAIT)
    try:
        unit()
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        node(False)
        A_.cleanup()
    print("PERF " + json.dumps(PERF))
    print(f"\nANRUFE-SIGNAL-ZIELE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def unit():
    from saga import calls as CL, chat as CH, fed_calls as FCL
    top = HN.LIMITS_TOP                                    # 2263 Grenzwerte: Obergrenze des Testhakens (>= LIMITS)
    have = getattr(CL, "SIG_TARGETS_MAX", None)            # vor 2.6 nicht vorhanden (Vorher-Lauf faellt hier, ohne Abbruch)
    ok(f"SIG_TARGETS_MAX = Obergrenze ueber Knoten inkl. Testhaken ({have} = {top} >= LIMITS {HN.LIMITS})",
       have == top and top > CH.DEVICES_MAX and all(n <= top for va in HN.LIMITS.values() for n in va))
    v = base64.b64encode(secrets.token_bytes(64)).decode()
    m = lambda n: {secrets.token_hex(16): v for _ in range(n)}  # noqa: E731
    m11, mtop, mover = m(CH.DEVICES_MAX + 1), m(top), m(top + 1)
    ok("1:1 (_sealed_map Standard, foederiert _sealed): 11 Ziele -> abgelehnt, 10 -> ok",
       CL._sealed_map(m11) is None and FCL._sealed(m11) is None and CL._sealed_map(m(CH.DEVICES_MAX)) is not None)
    r1, d1 = CL._room_sealed(mtop, set(mtop))
    r2, _ = CL._room_sealed(mover, set(mover))
    ok(f"Raum (_room_sealed): {top} Ziele -> alle, {top + 1} -> Formfehler",
       r1 == mtop and d1 == [] and r2 is None)
    PERF["pairs_budget"] = {str(n): CL.pairs_budget({"answered": None}, n) for n in (17, 51, 101)}
    PERF["fkey_sturm_gehen"] = {str(n): 2 * (n - 1) * (n - 2) for n in (16, 50, 100)}


def run():
    from saga import calls as CL
    ps = [A_.Person(u) for u, _ in USERS]
    for p in ps:
        for _ in range(DEVS - 1):
            p.add_device()
        for dv in p.devs:
            req("PUT", "/api/calls/caps", p.k, {"device": dv["id"], "caps": CAPS})
    node(True)
    A = ps[0]
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:]]})
    gid = d["chat"]["id"]

    # --- Knoten-Anruf (Ton, UDP-Knoten: Grenze 50) ----------------------------------------------------------------
    fresh()
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    k = d.get("call") or {}
    ok(f"Gruppenanruf ueber Knoten (Ton, UDP) gestartet ({s}, {k.get('topo')}, Grenze {k.get('limit')})",
       s == 200 and k.get("topo") == "horn" and (k.get("limit") or 0) >= 50)
    # Reihenfolge: zuerst die Hauptgeraete (Personen), dann die Begleitgeraete - so sind schnell 17 Endpunkte da
    order = [(p, p.devs[0]) for p in ps] + [(p, d) for p in ps for d in p.devs[1:]]
    joined, bad = [order[0]], []
    for p, dv in order[1:17]:
        s, d = hjoin(p, dv, cid)
        (joined if s == 200 else bad).append((p, dv) if s == 200 else s)
    lg = legs(A, cid)
    ok(f"17 Endpunkte im Anruf ({len(lg)}, Fehler {bad[:3]})", len(lg) == 17 and not bad)

    frm, fdev = joined[5]
    tg = [(p, d) for p, d in joined if d["id"] != fdev["id"]]
    b = fkey(1)
    t0 = time.time()
    s, d = send(frm, fdev, cid, sealed(frm, fdev, cid, tg, b))
    PERF["signal16_ms"] = round((time.time() - t0) * 1000, 1)
    ok(f"fkey an 16 Ziele in einer Anfrage -> 200, n 16 ({s}, {d})", s == 200 and d.get("n") == 16 and not d.get("dropped"))
    good, miss = all_got(cid, frm, fdev, tg, b)
    ok(f"... alle 16 Ziele bekommen ihr Signal (versiegelt, signiert) (fehlt: {miss})", good)

    for p, dv in order[17:50]:
        s, d = hjoin(p, dv, cid)
        (joined if s == 200 else bad).append((p, dv) if s == 200 else s)
    lg = legs(A, cid)
    ok(f"50 Endpunkte im Anruf ({len(lg)}, Fehler {bad[:3]})", len(lg) == 50 and not bad)
    outside = order[50]
    ok("... ein Geraet bleibt draussen", outside[1]["id"] not in lg)

    frm, fdev = joined[-1]
    tg = [(p, d) for p, d in joined if d["id"] != fdev["id"]]
    b = fkey(2)
    sig = sealed(frm, fdev, cid, tg, b)
    body = {"device": fdev["id"], "signals": sig}
    PERF["body50_bytes"] = len(json.dumps(body))           # Name bleibt (Vergleich), seit 2263-Grenzwerten 49 Ziele
    PERF["sealed_fkey_b64"] = len(next(iter(sig.values())))
    t0 = time.time()
    s, d = send(frm, fdev, cid, sig)
    PERF["signal50_ms"] = round((time.time() - t0) * 1000, 1)
    ok(f"fkey an 49 Ziele in einer Anfrage -> 200, n 49 ({s}, n {d.get('n') if isinstance(d, dict) else d})",
       s == 200 and d.get("n") == 49 and not d.get("dropped"))
    good, miss = all_got(cid, frm, fdev, tg, b)
    ok(f"... alle 49 Ziele bekommen ihr Signal (fehlt: {miss})", good)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_call_signal WHERE call_id=? AND from_device=?", (cid, fdev["id"])).fetchone()[0]
    ok(f"... 49 Zeilen chat_call_signal ({n})", n == 49)

    b = fkey(3)
    sig = sealed(frm, fdev, cid, tg + [outside], b)
    s, d = send(frm, fdev, cid, sig)
    ok(f"49 Ziele + 1 Geraet ausserhalb -> 200, n 49, dropped [aussen] ({s}, {d.get('n')}, {d.get('dropped')})",
       s == 200 and d.get("n") == 49 and d.get("dropped") == [outside[1]["id"]])
    ok("... das Geraet draussen bekommt nichts", not got_from(outside[0], outside[1], cid, frm, fdev, b))

    # --- Formfehler weiter 400 ------------------------------------------------------------------------------------
    v = base64.b64encode(secrets.token_bytes(64)).decode()
    top = CL.SIG_TARGETS_MAX
    over = dict(sealed(frm, fdev, cid, tg[:5], b), **{secrets.token_hex(16): v for _ in range(top + 1 - 5)})
    s, d = send(frm, fdev, cid, over)
    ok(f"{len(over)} Ziele (mehr als SIG_TARGETS_MAX) -> 400 ({s})", s == 400)
    s, _ = send(frm, fdev, cid, {})
    ok(f"leere Zielliste -> 400 ({s})", s == 400)
    s, _ = send(frm, fdev, cid, dict(sealed(frm, fdev, cid, tg[:3], b), **{"kein-hex": v}))
    ok(f"Kennung kein Hex -> 400 ({s})", s == 400)
    s, _ = send(frm, fdev, cid, dict(sealed(frm, fdev, cid, tg[:3], b), **{tg[3][1]["id"]: "%%%"}))
    ok(f"Wert kein B64 -> 400 ({s})", s == 400)
    s, _ = send(frm, fdev, cid, dict(sealed(frm, fdev, cid, tg[:3], b), **{tg[3][1]["id"]: base64.b64encode(b"kurz").decode()}))
    ok(f"Wert zu kurz -> 400 ({s})", s == 400)
    s, _ = send(frm, fdev, cid, {secrets.token_hex(16): v for _ in range(20)})
    ok(f"20 Ziele, keines im Anruf -> 400 ({s})", s == 400)

    # --- Budget greift weiter -------------------------------------------------------------------------------------
    with connect() as c, c:
        call = CL._load(c, cid)
        nall = c.execute("SELECT COUNT(*) FROM chat_call_leg WHERE call_id=?", (cid,)).fetchone()[0]
        bud = CL.pairs_budget(call, nall)
        old = call["nsig"]
        c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (bud - 10, cid))
    PERF["budget51"] = bud
    s, _ = send(frm, fdev, cid, sealed(frm, fdev, cid, tg, fkey(4)))
    ok(f"Budget fast erschoepft ({bud}, {nall} Endpunkte): {len(tg)} Ziele -> 429 ({s})", s == 429)
    s, d = send(frm, fdev, cid, sealed(frm, fdev, cid, tg[:5], fkey(5)))
    ok(f"... 5 Ziele passen noch -> 200 ({s}, {d.get('n') if isinstance(d, dict) else d})", s == 200 and d.get("n") == 5)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (old, cid))
    req("POST", f"/api/calls/{cid}/end", A.k, {"device": A.dev["id"], "reason": "hangup"})
    for p, dv in joined[1:]:
        req("POST", f"/api/calls/{cid}/leave", p.k, {"device": dv["id"]})

    # --- Mesh-Raum (ohne horn): Grenze wie im Raum, die Liste der Endpunkte begrenzt -------------------------------
    mp = ps[:4]
    for p in mp:
        req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS24})
    s, gd = req("POST", "/api/chats", A.k, {"kind": "free", "members": [p.uid for p in mp[1:]]})
    mid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": mid, "chat_id": gd["chat"]["id"], "media": "audio", "device": A.dev["id"], "caps": CAPS24})
    ok(f"Mesh-Gruppenanruf ({s}, {(d.get('call') or {}).get('topo')})", s == 200 and (d.get("call") or {}).get("topo") == "mesh")
    inside = [(A, A.dev)]
    for p in mp[1:]:
        s, _ = R_.join(p, p.dev, mid, inside, caps=CAPS24)
        if s == 200:
            inside.append((p, p.dev))
    ok(f"... 4 Endpunkte ({len(legs(A, mid))})", len(legs(A, mid)) == 4)
    frm, fdev = inside[1]
    tg = [x for x in inside if x[1]["id"] != fdev["id"]]
    b = json.dumps({"t": "state", "mic": True}, separators=(",", ":"))
    foreign = {secrets.token_hex(16): v for _ in range(8)}
    s, d = send(frm, fdev, mid, dict(sealed(frm, fdev, mid, tg, b), **foreign))
    ok(f"Mesh: 3 Endpunkte + 8 fremde Kennungen (11 Ziele) -> 200, n 3, dropped 8 ({s}, {d})",
       s == 200 and d.get("n") == 3 and sorted(d.get("dropped") or []) == sorted(foreign))
    good, miss = all_got(mid, frm, fdev, tg, b)
    ok(f"... die 3 Endpunkte bekommen es (fehlt: {miss})", good)
    req("POST", f"/api/calls/{mid}/end", A.k, {"device": A.dev["id"], "reason": "hangup"})
    for p, dv in inside[1:]:
        req("POST", f"/api/calls/{mid}/leave", p.k, {"device": dv["id"]})

    # --- 1:1 ohne Raum: Geraete einer Person, Grenze unveraendert --------------------------------------------------
    P, Q = ps[11], ps[12]
    for p in (P, Q):
        for dv in p.devs:
            req("PUT", "/api/calls/caps", p.k, {"device": dv["id"], "caps": ["reneg", "want", "bye", "screen2"]})
    ch = A_.dm(P, Q)
    did = secrets.token_hex(16)
    sig = dict(A_.seal_for(P, P.dev, did, Q, Q.devs, A_.offer_body()), **{secrets.token_hex(16): v for _ in range(7)})
    s, d = req("POST", "/api/calls", P.k, {"id": did, "chat_id": ch, "media": "audio", "device": P.dev["id"], "signals": sig})
    ok(f"1:1-Anrufstart mit 11 Zielen -> 400 ({s}, {len(sig)})", s == 400 and len(sig) == 11)


if __name__ == "__main__":
    main()
