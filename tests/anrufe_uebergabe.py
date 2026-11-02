"""2.5 Anruf-Uebergabe Phase 1 (docs/anruf-uebergabe-hinzufuegen.md L1-L5, L11; Kaskade mit Hysterese) - Server-Seite.

Ohne echten Knoten: ein gekoppelter Knoten als Registereintrag (Lastmeldung frisch, Zertifikat "self") gilt als bereit -
geprueft wird die Steuerung in Saga (Kennungen, Token, Rollen, Bedingungen), nicht die Medien (Labor: browser_horn_pingpong.js).

Geprueft:
- Feature handover2, Teilnehmerliste/Kandidaten/Join-Antwort mit gen, hp = peer_ref(gen) = peer im Kandidaten = peer_ref im
  Token (L1).
- Hin und Her ueber Knoten (1:1, horn_all): 10 Runden A1 <-> A2 mit kurzer Pause, jede Runde neue Kennung je Geraet (keine
  Sperre am Knoten durch das alte kick), am Ende 2 Endpunkte, kein 429 (eigener Topf handover 20/min, L5).
- Schneller Rueckwechsel < 3 s (B2/L2): verspaeteter Rueckfall {by, gen} -> stale, zurueckgekehrtes Geraet bleibt.
- Wiedereintritt eines noch aktiven Geraets (L5): 200 statt 409 already, neue Generation, bleibt Hauptgeraet; sein eigenes
  altes Verlassen (gen) -> stale.
- Parallele Uebernahme desselben Geraets -> 409 handover_busy (L2); ein liegengebliebenes handover-Bein wird nach dem Abgang
  zum Begleitgeraet + call.mod {action mute, why handover}.
- Begleitgeraet befoerdern (L3): /horn ohne Ton-Recht, POST /promote -> member (altes Hauptgeraet companion), Antwort und
  /horn mit Ton-Recht; already; fremdes Geraet 409.
- Voller Mesh-Anruf (L4/E6): takeover true bei join_block full, Hinzufuegen 409 full, Uebernahme 200.
- Rate: handover-Topf greift (429) erst nach 20/min.
- L11 (Kopie eines foederierten 1:1): caller_device/callee_device aus der Liste des fuehrenden Servers (in-process).
- Kaskade (Labor: pendelt): voller Hub-Knoten behaelt die Bruecke (Hysterese), Abbau nach FULL_HOLD bzw. sofort beim Leeren
  (in-process, horn_bridge.reconcile).
Wegwerf-Konten ca-ho*."""
import base64
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_all": True}
NODE = "ho-knoten.example.org"
USERS = [("ca-hoa", "Anna Wechsel"), ("ca-hob", "Bert Wechsel"), ("ca-hoc", "Cora Wechsel"), ("ca-hod", "Dora Wechsel")]


def node(on=True, name=NODE, load=None):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        if on:
            nid = secrets.token_hex(8)
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                      "VALUES (?,?,?,0,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                      (nid, name, "dGVzdC1rbm90ZW4taG8tMDAwMDAwMDAwMDAwMDAwMDA=", f"https://{name}:8443", False, HN.VERSION,
                       json.dumps(load or {"cpu": 0.05, "legs": 0, "max_legs": 48, "state": "ok"}), time.time(), time.time(),
                       json.dumps({"mode": "alpn", "state": "self"})))
            return nid


def fresh():
    with connect() as c, c:
        c.execute("UPDATE horn_node SET seen=? WHERE name LIKE 'ho-%'", (time.time(),))


def payload(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def cands(d):
    return ((d.get("horn") or (d.get("call") or {}).get("horn") or {}).get("candidates") or [])


def legs(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return {x["device"]: x for x in (d.get("call") or {}).get("legs", [])} if s == 200 else {}


def hjoin(p, dev, cid, role, rep=None, cs=CAPS):
    fresh()
    b = {"device": dev["id"], "role": role, "caps": cs}
    if rep:
        b["replaces"] = rep["id"]
    return req("POST", f"/api/calls/{cid}/join", p.k, b)


def leave(p, cid, dev, **kw):
    return req("POST", f"/api/calls/{cid}/leave", p.k, dict({"device": dev["id"]}, **kw))


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", CALLS)
    auth._settings_cache["v"] = None
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    time.sleep(A_.WAIT)
    try:
        run()
        bridge()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE name LIKE 'ho-%'")
        A_.cleanup()
    print(f"\nANRUFE-UEBERGABE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    A, B, Cc, D = (A_.Person(u) for u, _ in USERS)
    A1 = A.dev
    A2, A3 = A.add_device(), A.add_device()
    for p in (A, B, Cc, D):
        for dv in p.devs:
            req("PUT", "/api/calls/caps", p.k, {"device": dv["id"], "caps": CAPS})
    nid = node(True)
    ab = A_.dm(A, B)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"config: Feature handover2, handover_s 15 ({cf.get('features')})", "handover2" in (cf.get("features") or []) and cf["limits"]["handover_s"] == 15)

    # --- 1:1 ueber Knoten, Kennungen mit Generation -----------------------------------------------------------------
    fresh()
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": ab, "media": "audio", "device": A1["id"], "caps": CAPS, "via": "horn"})
    ok(f"1:1 ueber Knoten gestartet ({s}, {(d.get('call') or {}).get('topo')})", s == 200 and d["call"]["topo"] == "horn")
    s, d = hjoin(B, B.dev, cid, "member")
    ok(f"B nimmt an (join) mit gen in der Antwort ({s}, {d.get('gen')})", s == 200 and isinstance(d.get("gen"), int) and d["gen"] > 0)
    lg = legs(A, cid)
    x = lg.get(A1["id"]) or {}
    ok(f"Teilnehmerliste: gen je Endpunkt, hp = peer_ref(cid, knoten, geraet, gen) ({x.get('gen')})",
       all(isinstance(y.get("gen"), int) and y["gen"] > 0 for y in lg.values()) and x.get("hp") == HN.peer_ref(cid, nid, A1["id"], x["gen"])
       and x["hp"] != HN.peer_ref(cid, nid, A1["id"]))
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": A1["id"]})
    c0 = cands(d)[0] if cands(d) else {}
    ok(f"Kandidat: peer und gen, peer_ref im Token = peer ({c0.get('gen')})",
       c0.get("gen") == x.get("gen") and c0.get("peer") == x.get("hp") and payload(c0["tokens"]["audio"])["peer_ref"] == c0["peer"])

    # --- Hin und Her: 10 Runden ------------------------------------------------------------------------------------
    sa = A_.Stream(A.k)
    time.sleep(0.6)
    refs = {A1["id"]: {x["hp"]}, A2["id"]: set()}
    cur, nxt = A1, A2
    good, t0, n429 = 0, time.time(), 0
    for i in range(10):
        g_old = legs(A, cid)[cur["id"]]["gen"]
        s, d = hjoin(A, nxt, cid, "handover", cur)
        if s == 429:
            n429 += 1
        me = next((y for y in (d.get("call") or {}).get("legs", []) if y["device"] == nxt["id"]), {})
        new_ref = (cands(d)[0] if cands(d) else {}).get("peer")
        fresh_ref = new_ref and new_ref not in refs[nxt["id"]] and new_ref == me.get("hp")
        s1, d1 = leave(A, cid, cur, reason="handover", gen=g_old)        # altes Geraet: handoverOut
        s2, d2 = leave(A, cid, cur, reason="handover", by=nxt["id"], gen=g_old)   # Rueckfall des neuen Geraets (3 s)
        lg = legs(A, cid)
        if (s == 200 and me.get("role") == "handover" and fresh_ref and s1 == 200 and not d1.get("stale") and d2.get("already")
                and set(lg) == {B.dev["id"], nxt["id"]} and lg[nxt["id"]]["role"] == "member"):
            good += 1
        else:
            print("  Runde", i, s, d.get("error"), me.get("role"), fresh_ref, s1, d1, d2, {k[:6]: v["role"] for k, v in lg.items()}, flush=True)
        if new_ref:
            refs[nxt["id"]].add(new_ref)
        cur, nxt = nxt, cur
        time.sleep(0.3 if i % 2 else 1.0)
    ok(f"Hin und Her ueber Knoten: 10 Runden, jede mit neuer Kennung, alter Rueckfall 'already' ({good}/10, {time.time() - t0:.1f} s)", good == 10)
    ok(f"... kein 429 bei 10 Uebernahmen in einer Minute ({n429})", n429 == 0)
    ok(f"... 5 verschiedene Kennungen je Geraet ({len(refs[A1['id']])}, {len(refs[A2['id']])})", len(refs[A1["id"]]) == 6 and len(refs[A2["id"]]) == 5)
    with connect() as c:
        st = c.execute("SELECT state FROM chat_call WHERE id=?", (cid,)).fetchone()[0]
    ok(f"... Anruf laeuft, zwei Endpunkte ({st}, {len(legs(A, cid))})", st == "active" and len(legs(A, cid)) == 2)

    # --- Schneller Rueckwechsel < 3 s (B2) --------------------------------------------------------------------------
    X, Y = cur, nxt                                     # X fuehrt
    gx = legs(A, cid)[X["id"]]["gen"]
    s, d = hjoin(A, Y, cid, "handover", X)
    gy = d.get("gen")
    s1, _ = leave(A, cid, X, reason="handover", gen=gx)
    s2, d2 = hjoin(A, X, cid, "handover", Y)           # X holt den Anruf sofort zurueck
    gx2 = d2.get("gen")
    s3, d3 = leave(A, cid, X, reason="handover", by=Y["id"], gen=gx)   # verspaeteter Rueckfall von Y (3 s)
    lg = legs(A, cid)
    ok(f"Rueckwechsel < 3 s: verspaeteter Rueckfall {{by, gen}} -> stale, X bleibt ({s}, {s1}, {s2}, {d3})",
       (s, s1, s2) == (200, 200, 200) and d3.get("stale") is True and X["id"] in lg and gx2 != gx)
    s4, d4 = leave(A, cid, Y, reason="handover", gen=gy)
    lg = legs(A, cid)
    ok(f"... Y gibt ab: X Hauptgeraet, zwei Endpunkte ({s4}, {[v['role'] for v in lg.values()]})",
       s4 == 200 and set(lg) == {B.dev["id"], X["id"]} and lg[X["id"]]["role"] == "member")
    s5, d5 = leave(A, cid, X, reason="handover", by=Y["id"])
    ok(f"... Rueckfall mit inaktivem by -> stale ({d5})", d5.get("stale") is True and X["id"] in legs(A, cid))

    # --- Wiedereintritt eines noch aktiven Geraets (L5) -------------------------------------------------------------
    gx = legs(A, cid)[X["id"]]["gen"]
    hpx = legs(A, cid)[X["id"]]["hp"]
    s, d = hjoin(A, Y, cid, "handover", X)
    gy = d.get("gen")
    s2, d2 = hjoin(A, X, cid, "handover", Y)           # X ist noch im Anruf (sein Verlassen ist unterwegs)
    lg = legs(A, cid)
    ok(f"Wiedereintritt statt 409 already: neue Generation, neue Kennung, bleibt Hauptgeraet ({s2}, {d2.get('join_block')}, "
       f"{lg.get(X['id'], {}).get('role')})", s2 == 200 and d2.get("gen") != gx and lg[X["id"]]["hp"] != hpx and lg[X["id"]]["role"] == "member")
    s3, d3 = leave(A, cid, X, reason="handover", gen=gx)
    ok(f"... altes Verlassen von X (alte gen) -> stale ({d3})", d3.get("stale") is True and X["id"] in legs(A, cid))
    leave(A, cid, Y, reason="handover", gen=gy)
    ok("... Y gibt wieder ab", set(legs(A, cid)) == {B.dev["id"], X["id"]})
    s, d = req("POST", f"/api/calls/{cid}/join", A.k, {"device": X["id"], "role": "member", "caps": CAPS})
    ok(f"ohne handover: weiterhin 409 already ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "already")

    # --- Parallele Uebernahme, liegengebliebenes handover-Bein --------------------------------------------------------
    s, d = hjoin(A, Y, cid, "handover", X)
    s2, d2 = hjoin(A, A3, cid, "handover", X)
    ok(f"zweite gleichzeitige Uebernahme desselben Geraets -> 409 handover_busy ({s}, {s2}, {d2.get('join_block')})",
       s == 200 and s2 == 409 and d2.get("join_block") == "handover_busy" and d2.get("legs"))
    with connect() as c, c:                             # Y haengt (z. B. abgestuerzt): aelter als handover_s
        c.execute("UPDATE chat_call_leg SET joined=joined-60 WHERE call_id=? AND device=?", (cid, Y["id"]))
    s3, d3 = hjoin(A, A3, cid, "handover", X)
    ok(f"... nach handover_s gilt das alte handover-Bein als liegengeblieben: Uebernahme geht ({s3})", s3 == 200)
    m = sa.mark()
    leave(A, cid, X, reason="handover")
    lg = legs(A, cid)
    ok(f"... X geht: A3 fuehrt, Y wird Begleitgeraet ({ {k[:6]: v['role'] for k, v in lg.items()} })",
       lg.get(A3["id"], {}).get("role") == "member" and lg.get(Y["id"], {}).get("role") == "companion" and X["id"] not in lg)
    ev = sa.wait("call.mod", lambda e: e.get("call_id") == cid and e.get("device") == Y["id"], m)
    ok(f"... call.mod an A: Mikrofon aus fuer Y, why handover ({ev})", ev and ev.get("action") == "mute" and ev.get("why") == "handover")

    # --- Begleitgeraet befoerdern (L3) ---------------------------------------------------------------------------
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": Y["id"]})
    pa = payload(cands(d)[0]["tokens"]["audio"]) if cands(d) else {}
    ok(f"Begleitgeraet: Ton-Token ohne Senderecht ({pa.get('pub')})", s == 200 and isinstance(pa.get("pub"), list) and "a" not in pa["pub"])
    m = sa.mark()
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/promote", A.k, {"device": Y["id"]})
    pa = payload(cands(d)[0]["tokens"]["audio"]) if cands(d) else {}
    lg = {x["device"]: x for x in (d.get("call") or {}).get("legs", [])}
    ok(f"promote: Y member, A3 companion, Antwort mit Ton-Recht ({s}, {pa.get('pub')}, { {k[:6]: v['role'] for k, v in lg.items()} })",
       s == 200 and "a" in (pa.get("pub") or []) and lg.get(Y["id"], {}).get("role") == "member" and lg.get(A3["id"], {}).get("role") == "companion")
    ok("... gleiche Kennungen wie vorher (kein neues Bein am Knoten noetig, leg_ref gleich)", pa.get("peer_ref") == lg[Y["id"]].get("hp")
       and pa.get("leg_ref") == HN.leg_ref(cid, nid, Y["id"], "audio"))
    ok("... call.roster an A", sa.wait("call.roster", lambda e: e.get("call_id") == cid and any(
        x["device"] == Y["id"] and x["role"] == "member" for x in e["legs"]), m) is not None)
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": Y["id"]})
    s2, d2 = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": A3["id"]})
    ok("... POST /horn: Y mit Ton-Recht, A3 (jetzt Begleitgeraet) ohne",
       "a" in payload(cands(d)[0]["tokens"]["audio"])["pub"] and "a" not in payload(cands(d2)[0]["tokens"]["audio"])["pub"])
    s, d = req("POST", f"/api/calls/{cid}/promote", A.k, {"device": Y["id"]})
    ok(f"... schon Hauptgeraet: already ({d})", s == 200 and d.get("already") is True)
    s, d = req("POST", f"/api/calls/{cid}/promote", B.k, {"device": Y["id"]})
    s2, d2 = req("POST", f"/api/calls/{cid}/promote", A.k, {"device": B.dev["id"]})
    ok(f"... fremdes Geraet -> 409 ({s}, {s2})", s == 409 and s2 == 409)

    req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup"})

    # --- Voller Mesh-Anruf (L4/E6) ------------------------------------------------------------------------------
    for p in (A, B, Cc, D):
        for dv in p.devs:
            req("PUT", "/api/calls/caps", p.k, {"device": dv["id"], "caps": CAPS24})
    s, gd = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-hob", "ca-hoc", "ca-hod"]})
    gid = gd["chat"]["id"]
    g1 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "video", "device": A1["id"], "caps": CAPS24})
    ok(f"Mesh-Gruppenanruf (Video, Grenze 4) ({s}, {(d.get('call') or {}).get('topo')})", s == 200 and d["call"]["topo"] == "mesh")
    inside = [(A, A1)]
    for p in (B, Cc, D):
        s, d = R_.join(p, p.dev, g1, inside, caps=CAPS24)
        inside.append((p, p.dev))
    ok(f"... vier Endpunkte ({s}, {len(legs(A, g1))})", s == 200 and len(legs(A, g1)) == 4)
    s, d = req("GET", f"/api/calls/{g1}", A.k)
    k = d.get("call") or {}
    ok(f"voll: joinable false, join_block full, takeover true ({k.get('joinable')}, {k.get('join_block')}, {k.get('takeover')})",
       k.get("joinable") is False and k.get("join_block") == "full" and k.get("takeover") is True)
    s, d = req("GET", f"/api/calls/{g1}", B.k)
    ok("... takeover nur mit eigenem Geraet im Anruf (B: auch true, B ist dabei)", d["call"].get("takeover") is True)
    others = [(p, dv) for p, dv in inside if p is not A]
    s, d = R_.join(A, A2, g1, others, role="companion", caps=CAPS24)
    ok(f"... Hinzufuegen (Begleitgeraet) -> 409 full ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "full")
    s, d = R_.join(A, A2, g1, others, role="handover", replaces=A1, caps=CAPS24)
    ok(f"... Hierher uebernehmen -> 200 (ersetztes Geraet zaehlt nicht) ({s}, {d.get('error')})", s == 200)
    g_a1 = legs(A, g1)[A1["id"]]["gen"]
    s, d = leave(A, g1, A1, reason="handover", gen=g_a1)
    lg = legs(A, g1)
    ok(f"... A1 geht: wieder vier Endpunkte, A2 Mitglied ({len(lg)}, {lg.get(A2['id'], {}).get('role')})",
       len(lg) == 4 and lg[A2["id"]]["role"] == "member")
    s, d = leave(A, g1, A1, reason="handover", by=A2["id"], gen=g_a1)
    ok(f"... Rueckfall im Mesh (nach handover_s/2): already ({d})", d.get("already") is True)

    # --- Rate: eigener Topf (nach den Uebernahmen oben: 20 je Minute) ------------------------------------------------
    others = [(p, dv) for p, dv in inside if p is not A]
    s, d = R_.join(A, A3, g1, others, role="handover", replaces=A2, caps=CAPS24)
    n = 1 if s == 200 else 0
    for i in range(25):
        s, d = req("POST", f"/api/calls/{g1}/promote", A.k, {"device": (A3 if i % 2 == 0 else A2)["id"]})
        if s == 429:
            break
        n += s == 200
    ok(f"Uebernahmen: eigener Topf handover, 20 je Minute ({n} weitere bis zum ersten 429, {s})", s == 429 and 0 < n < 20)
    for p in (A, B, Cc, D):
        req("POST", f"/api/calls/{g1}/end", p.k, {"reason": "hangup"})

    # --- L11: Kopie eines foederierten 1:1 zieht die Hauptgeraete nach (in-process) ---------------------------------
    from saga import fed_calls as FCL
    cx = secrets.token_hex(16)
    with connect() as c, c:
        now = time.time()
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, callee_device, media, state, created, answered, seen_a, "
                  "seen_b, kind, v, lead) VALUES (?,?,?,?,?,?,'audio','active',?,?,?,?,'dm',3,?)",
                  (cx, ab, A.uid, B.uid, A1["id"], B.dev["id"], now, now, now, now, 999999))
        for dv, who in ((A1, A.uid), (A2, A.uid), (B.dev, B.uid)):
            c.execute("INSERT INTO chat_call_leg (call_id, device, who, role, state, joined, seen) VALUES (?,?,?,'member','active',?,?)",
                      (cx, dv["id"], who, now, now))
        call = {"id": cx, "chat_id": ab, "caller": A.uid, "callee": B.uid, "caller_device": A1["id"], "callee_device": B.dev["id"],
                "kind": "dm", "lead": 999999, "peer_id": None}
        FCL._dm_devices(c, call, {"caller_device": A2["id"], "callee_device": "f" * 32})
        r = c.execute("SELECT caller_device, callee_device FROM chat_call WHERE id=?", (cx,)).fetchone()
        c.execute("DELETE FROM chat_call WHERE id=?", (cx,))
    ok(f"L11: Kopie uebernimmt caller_device vom fuehrenden Server, unbekanntes Geraet nicht ({r[0][:6]}, {r[1][:6]})",
       r[0] == A2["id"] and r[1] == B.dev["id"])


def bridge():
    """Kaskade mit Hysterese (Labor 2.11.: Bruecke pendelt bei einem Knoten an der Lastgrenze)."""
    from saga import horn_bridge as HB
    ok("usable(): voller, laufender Knoten zaehlt bis FULL_HOLD, dann nicht mehr; getrennt sofort nicht",
       HB.usable({"a"}, {"a", "b"}, 100.0, full={}) == {"a", "b"}
       and HB.usable({"a"}, {"a", "b"}, 100.0 + HB.FULL_HOLD + 1, full={"b": 100.0}) == {"a"}
       and HB.usable({"a"}, {"a"}, 100.0, full={"b": 99.0}) == {"a"})
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name LIKE 'ho-%'")
    n1, n2 = node(True, "ho-hub.example.org"), node(True, "ho-rand.example.org")
    A = A_.Person("ca-hoa", setup=False)
    with connect() as c:
        chat_id = c.execute("SELECT id FROM chat WHERE created_by=? ORDER BY id LIMIT 1", (A.uid,)).fetchone()[0]
    cid = secrets.token_hex(16)
    now = time.time()
    with connect() as c, c:
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, answered, seen_a, seen_b, kind, v, topo) "
                  "VALUES (?,?,?,'','x','audio','active',?,?,?,?,'group',3,'horn')", (cid, chat_id, A.uid, now, now, now, now))
        for i, nd in enumerate((n1, n1, n2)):
            c.execute("INSERT INTO chat_call_leg (call_id, device, who, role, state, joined, seen, node) VALUES (?,?,?,'member','active',?,?,?)",
                      (cid, secrets.token_hex(16), A.uid, now + i, now, nd))
    HB._HUB.clear()
    HB._BR.clear()
    HB._FULL.clear()

    def want():
        fresh()
        return [w for w in HB.reconcile().values() if w["call"] == cid]

    def load(cpu):
        with connect() as c, c:
            c.execute("UPDATE horn_node SET load=CAST(? AS JSONB) WHERE id=?", (json.dumps({"cpu": cpu, "legs": 2, "max_legs": 48, "state": "ok"}), n1))

    def ready1():
        with connect() as c:
            return HN.ready(HN.node_by_id(c, n1))
    try:
        w = want()
        ok(f"Kaskade: zwei Knoten -> eine Bruecke Rand -> Hub ({[(x['dial'][:4], x['hub'][:4]) for x in w]})",
           len(w) == 1 and w[0]["hub"] == n1 and w[0]["dial"] == n2)
        load(0.95)
        a = len(want())
        time.sleep(2.1)
        b = len(want())
        ok(f"... Hub ueber der Lastgrenze (nicht bereit): Bruecke bleibt (Hysterese) ({ready1()}, {a}, {b})", not ready1() and a == 1 and b == 1)
        HB._FULL[n1] = time.time() - HB.FULL_HOLD - 1    # Dauerlast seit laengerem
        ok(f"... nach FULL_HOLD ({HB.FULL_HOLD:.0f} s) Dauerlast: Bruecke wird abgebaut", want() == [])
        load(0.1)
        ok("... Last wieder normal: Bruecke wieder da", len(want()) == 1 and n1 not in HB._FULL)
        load(0.95)
        n_up = len(want())
        with connect() as c, c:
            c.execute("UPDATE horn_node SET drain=? WHERE id=?", (time.time(), n1))
        ok(f"... voll und dann Leeren: sofort kein Mittelpunkt mehr auf dem Hub ({n_up})", n_up == 1 and not [x for x in want() if x["hub"] == n1])
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM chat_call WHERE id=?", (cid,))
            c.execute("DELETE FROM horn_node WHERE name LIKE 'ho-%'")


if __name__ == "__main__":
    main()
