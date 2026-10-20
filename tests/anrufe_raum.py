"""Anrufe 2.4, Etappen E4/E5 (Raum-Modell in saga/calls.py) - Server-Seite mit PyNaCl als Client (tests/chatlib.py).

Geprueft: Migration 12 (chat_call_leg, chat_call_invite, chat_call.lead, chat_device.caps), Konfiguration (features multi und
group, Grenzen 4/6, vom Admin nur zu senken). E4: 1:1-Anruf als Raum (v=3, zwei Endpunkte), weiteres eigenes Geraet tritt
als Begleitgeraet bei (Rolle companion - auch wenn "member" verlangt), Angebot an das Geraet des Gegenuebers versiegelt
zugestellt, Teilnehmerliste call.roster an beide Personen (alle ihre Geraete - "Anruf laeuft auf …"), Signale zwischen
allen Endpunkten, Begleitgeraet legt auf = verlaesst nur (Anruf laeuft weiter). Uebernahme (handover, replaces): das alte
Geraet verlaesst, callee_device wechselt, Anruf laeuft weiter, altes Geraet darf nicht mehr signalisieren. Rechte:
fremdes Geraet, Unbeteiligte, ein Endpunkt ohne multi (2.3/E0-Client) -> 409 "braucht Saga 2.4", 1:1-Anruf ohne multi
(v=1) wie bisher, Angebote fehlen -> 409 mit Liste, beendet. E5: Gruppenanruf in einer freien Gruppe (v=3, mesh, kind
group): klingelt bei Mitgliedern mit 2.4-Geraet (call.invite, Push "call.group"), wer keins hat: "braucht Saga 2.4"
(nicht eingeladen), /active und Chat zeigen den laufenden Anruf, Beitreten (ab zwei Personen aktiv), Mesh-Angebote an
alle, Grenze 4 Endpunkte mit Video (eigene Zusatzgeraete zaehlen mit) bzw. 6 nur Ton, Ablehnen (nur die eigene
Einladung), Einladen, Verlassen (laeuft weiter, solange >= 2 Personen dabei sind), Ende mit Chat-Eintrag (group, n),
verpasst fuer Eingeladene, besetzt im Gruppenanruf, kein Beitritt nach dem Ende, Nicht-Mitglied 404, ein Gruppenanruf je
Chat. Hintergrund: Endpunkt ohne Lebenszeichen verlaesst den Anruf, Gruppenanruf ohne Beitritt endet nach der
Klingeldauer, Mitglied entfernt -> raus. Messung: Signalisierung eines 4er-Mesh (Aufbau aller Angebote/Antworten/ICE)
mit Python-CPU des Servers. Der Server sieht keine SDP. Wegwerf-Konten ca-r*; raeumt auf."""
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402  (Helfer: Person, dm, Stream, req, ...)
import auth  # noqa: E402
import chatlib as C  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
SDP_MARK = "saga-raum-sdp-marke"
CAPS = ["reneg", "want", "bye", "screen2", "multi"]
PERF = {}


def sdp(tag):
    return ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=" + SDP_MARK + "\r\nt=0 0\r\n"
            "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB:3D:17:6E:B4:2A:7C:4F:0D:2E:F1:A8:B3\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=mid:0\r\na=ice-ufrag:" + tag + "\r\na=ice-pwd:x" + tag + "x\r\n")


def pc(a, b):
    return ":".join(sorted([a["id"], b["id"]]))


def body(t, n, p, **kw):
    s = sdp("u" + str(n))
    return json.dumps(dict({"t": t, "sdp": s, "fp": C.sdp_fingerprints(s), "n": n, "pc": p, "caps": CAPS}, **kw), separators=(",", ":"))


def seal(frm, fdev, cid, to, tdev, b):
    return C.seal_signal(cid, frm.uid, fdev, to.uid, tdev["id"], tdev["enc_pk"], b)


def offers(frm, fdev, cid, targets):
    """{zielgeraet: versiegeltes Angebot} fuer [(person, geraet)]."""
    return {d["id"]: seal(frm, fdev, cid, p, d, body("offer", 1, pc(fdev, d))) for p, d in targets}


def join(p, dev, cid, targets, role="member", replaces=None, caps=CAPS):
    b = {"device": dev["id"], "role": role, "caps": list(caps), "signals": offers(p, dev, cid, targets)}
    if replaces:
        b["replaces"] = replaces["id"]
    return req("POST", f"/api/calls/{cid}/join", p.k, b)


def sig(frm, fdev, cid, to, tdev, b):
    return req("POST", f"/api/calls/{cid}/signal", frm.k, {"device": fdev["id"], "signals": {tdev["id"]: seal(frm, fdev, cid, to, tdev, b)}})


def got(me, dev, cid, frm, fdev, after=0):
    """Signale fuer mein Geraet von frm/fdev (geoeffnet + geprueft) -> [(seq, body)]"""
    s, d = req("GET", f"/api/calls/{cid}?device={dev['id']}&after={after}", me.k)
    out = []
    for x in d.get("signals", []):
        if x["from_device"] != fdev["id"]:
            continue
        b, sg = C.open_signal(x["sealed"], dev["enc_pk"], dev["enc_sk"])
        if C.verify_signal(cid, frm.uid, fdev["id"], me.uid, dev["id"], b, sg, fdev["sign_pk"]):
            out.append((x["seq"], json.loads(b)))
    return out


def legs(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return d.get("call", {}).get("legs", []) if s == 200 else None


def caps_put(p, dev, caps=CAPS):
    return req("PUT", "/api/calls/caps", p.k, {"device": dev["id"], "caps": list(caps)})[0]


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "",
                                "turn_user": "", "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45})
    for u, n in (("ca-ra", "Anna Raum"), ("ca-rb", "Bert Raum"), ("ca-rc", "Cora Raum"), ("ca-rd", "Dora Raum"), ("ca-re", "Emil Alt"),
                 ("ca-rf", "Fritz Raum"), ("ca-rx", "Xaver Fremd"), ("ca-rg", "Gerd Mesh"), ("ca-rh", "Hanna Mesh"), ("ca-ri", "Ines Mesh"),
                 ("ca-rj", "Jan Mesh")):
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
    print(f"\nANRUFE-RAUM {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    import db
    from saga import calls as CL
    A, B, Cc, D, E, F, X = (A_.Person(u) for u in ("ca-ra", "ca-rb", "ca-rc", "ca-rd", "ca-re", "ca-rf", "ca-rx"))
    A2, B2 = A.add_device(), B.add_device()
    for p in (A, B, Cc, D, F):
        for d in p.devs:
            caps_put(p, d)

    # --- Migration 12, Konfiguration -------------------------------------------------------------------------------
    with connect() as c:
        tabs = {r[0] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                                         "AND table_name IN ('chat_call_leg','chat_call_invite')").fetchall()}
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='chat_call'").fetchall()}
        dcols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='chat_device'").fetchall()}
        ver = db.schema_version(c)
    ok(f"Migration 12: chat_call_leg, chat_call_invite, chat_call.lead, chat_device.caps ({sorted(tabs)}, Stand {ver})",
       tabs == {"chat_call_leg", "chat_call_invite"} and "lead" in cols and "caps" in dcols and 12 in db.MIGRATIONS and ver == db.SCHEMA_VERSION)
    ok("Restore: chat_call_leg/chat_call_invite stehen in _RESTORE_DROP", b"chat_call_leg" in db._RESTORE_DROP and b"chat_call_invite" in db._RESTORE_DROP)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Konfiguration: features multi + group, Grenzen 4 (Video) / 6 (Ton) ({cf.get('features')}, {cf.get('limits', {}).get('endpoints_video')}"
       f"/{cf.get('limits', {}).get('endpoints_audio')})", {"multi", "group"} <= set(cf.get("features") or [])
       and cf["limits"]["endpoints_video"] == 4 and cf["limits"]["endpoints_audio"] == 6)
    cur = auth.settings(fresh=True).get("calls") or {}
    auth.save_setting("calls", dict(cur, endpoints_video=3, endpoints_audio=9))
    auth._settings_cache["v"] = None
    time.sleep(A_.WAIT)                                # Einstellungen: Zwischenspeicher des Servers
    s, cf2 = req("GET", "/api/calls/config", A.k)
    ok(f"Admin kann nur senken: Video 3, Ton bleibt 6 ({cf2['limits']['endpoints_video']}/{cf2['limits']['endpoints_audio']})",
       cf2["limits"]["endpoints_video"] == 3 and cf2["limits"]["endpoints_audio"] == 6)
    import admin as AD                                 # Admin-Bereich: Felder "Hoechstens Geraete je Anruf"
    AD._calls_save({"calls_enabled": "1", "calls_stun": cur.get("stun") or "", "calls_ring_s": "45", "calls_endpoints_video": "9",
                    "calls_endpoints_audio": "1"})
    sv = auth.settings(fresh=True).get("calls") or {}
    AD._calls_save({"calls_enabled": "1", "calls_stun": cur.get("stun") or "", "calls_ring_s": "45", "calls_endpoints_video": "3",
                    "calls_endpoints_audio": "5"})
    sv2 = auth.settings(fresh=True).get("calls") or {}
    ok(f"Admin-Bereich: Grenzen gespeichert und begrenzt (9 -> 4, 1 -> 2; 3/5) ({sv.get('endpoints_video')}/{sv.get('endpoints_audio')}, "
       f"{sv2.get('endpoints_video')}/{sv2.get('endpoints_audio')})", sv.get("endpoints_video") == 4 and sv.get("endpoints_audio") == 2
       and sv2.get("endpoints_video") == 3 and sv2.get("endpoints_audio") == 5)
    auth.save_setting("calls", cur)
    auth._settings_cache["v"] = None
    time.sleep(A_.WAIT)
    ok("Geraete-Faehigkeiten melden: nur eigenes Geraet (fremdes 403)", caps_put(A, A.dev) == 200 and caps_put(A, B.dev) == 403)

    # --- E4: 1:1 als Raum, Begleitgeraet ----------------------------------------------------------------------------
    ab = A_.dm(A, B)
    sA, sB = A_.Stream(A.k), A_.Stream(B.k)
    time.sleep(0.5)
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": ab, "media": "video", "device": A.dev["id"], "caps": CAPS,
                                           "signals": {x["id"]: seal(A, A.dev, cid, B, x, body("offer", 1, pc(A.dev, x))) for x in B.devs}})
    ok(f"1:1 mit multi: Anruf ist ein Raum (v=3), ein Endpunkt (Anrufer) ({s})", s == 200 and len(d["call"]["legs"]) == 1
       and d["call"]["legs"][0]["device"] == A.dev["id"] and d["call"]["kind"] == "dm")
    with connect() as c:
        row = c.execute("SELECT v, topo, kind, lead FROM chat_call WHERE id=?", (cid,)).fetchone()
    ok(f"... Zeile: v=3, p2p, dm, lead NULL ({tuple(row)})", tuple(row) == (3, "p2p", "dm", None))
    s, d = join(A, A2, cid, [(B, B.dev)], role="companion")
    ok(f"Beitreten waehrend es klingelt -> 409 (erst nach dem Annehmen) ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "none")
    mB = sB.mark()
    s, d = req("POST", f"/api/calls/{cid}/answer", B.k, {"device": B.dev["id"], "caps": CAPS,
                                                          "signals": {A.dev["id"]: seal(B, B.dev, cid, A, A.dev, body("answer", 1, pc(A.dev, B.dev)))}})
    ok(f"B nimmt an: zwei Endpunkte, joinable fuer weitere eigene Geraete ({s}, {len(d['call']['legs'])}, {d['call']['joinable']})",
       s == 200 and len(d["call"]["legs"]) == 2 and d["call"]["joinable"] is True)
    ev = sB.wait("call.roster", lambda x: x["call_id"] == cid, mB)
    ok("call.roster an B (auch an seine anderen Geraete: 'Anruf laeuft auf …') mit Geraetenamen",
       ev and {x["device"] for x in ev["legs"]} == {A.dev["id"], B.dev["id"]} and all(x.get("dev_name") for x in ev["legs"]) and ev["joinable"])
    s, act = req("GET", "/api/calls/active", B.k)
    a1 = next((x for x in act.get("calls", []) if x["id"] == cid), None)
    ok("GET /api/calls/active: laufender Anruf mit Endpunkten und eigenen Geraeten (mine)", a1 and a1["mine"] == [B.dev["id"]] and a1["joinable"])
    s, chd = req("GET", f"/api/chats/{ab}?device={B2['id']}", B.k)
    run_ = (chd.get("chat", {}).get("call") or {}).get("running") or {}
    ok(f"Chat zeigt den laufenden Anruf (call.running) ({run_.get('id', '')[:8]}, n={run_.get('n')})", run_.get("id") == cid and run_.get("n") == 2)

    mA, mB = sA.mark(), sB.mark()
    s, d = join(A, A2, cid, [(B, B.dev)], role="member")
    me = [x for x in (d.get("call") or {}).get("legs", []) if x["device"] == A2["id"]]
    ok(f"A2 (zweites Geraet von A) tritt bei: als Begleitgeraet (companion), auch wenn member verlangt ({s}, {me and me[0]['role']})",
       s == 200 and me and me[0]["role"] == "companion")
    gb = got(B, B.dev, cid, A, A2)
    ok("B bekommt das Angebot von A2 (versiegelt, signiert von A2, pc = Paar A2/B)", gb and gb[-1][1]["t"] == "offer" and gb[-1][1]["pc"] == pc(A2, B.dev))
    ev = sB.wait("call.roster", lambda x: x["call_id"] == cid and len(x["legs"]) == 3, mB)
    ok("call.roster an B: drei Endpunkte, A2 companion", ev and any(x["device"] == A2["id"] and x["role"] == "companion" for x in ev["legs"]))
    ok("... und an A (eigene Geraete)", sA.wait("call.roster", lambda x: x["call_id"] == cid and len(x["legs"]) == 3, mA) is not None)
    s1 = sig(B, B.dev, cid, A, A2, body("answer", 1, pc(A2, B.dev)))[0]
    s2 = sig(A, A2, cid, B, B.dev, json.dumps({"t": "ice", "pc": pc(A2, B.dev), "c": []}))[0]
    s3 = sig(A, A2, cid, A, A.dev, json.dumps({"t": "state", "mic": False}))[0]
    ok(f"Signale zwischen allen Endpunkten (B->A2 Antwort, A2->B ICE, A2->A eigenes Geraet) ({s1},{s2},{s3})", (s1, s2, s3) == (200, 200, 200))
    ok("A bekommt die Antwort von B auf A2 nicht (nur A2)", not [b for _, b in got(A, A.dev, cid, B, B.dev) if b.get("pc") == pc(A2, B.dev)])
    s, d = sig(X, X.dev, cid, B, B.dev, json.dumps({"t": "ice"}))
    ok(f"Unbeteiligte: 404 ({s})", s == 404)
    s, d = join(A, A2, cid, [(B, B.dev)])
    ok(f"Schon im Anruf -> 409 ({s})", s == 409)
    s, d = join(B, B2, cid, [], role="companion")
    ok(f"Angebote fehlen (an A und A2? - Pflicht: alle Endpunkte anderer Personen) -> 409 mit Liste ({s})", s == 409 and len(d.get("legs") or []) == 3)
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": A2["id"], "caps": CAPS, "signals": {}})
    ok(f"Fremdes Geraet als eigenes -> 403 ({s})", s == 403)
    s, d = req("POST", f"/api/calls/{cid}/end", A.k, {"device": A2["id"], "reason": "hangup", "connected": True})
    ok(f"Begleitgeraet legt auf = verlaesst nur dieses Geraet ({s}, {d.get('left')})", s == 200 and d.get("left") == [A2["id"]])
    l_ = legs(A, cid)
    ok("... Anruf laeuft weiter mit A und B", l_ is not None and {x["device"] for x in l_} == {A.dev["id"], B.dev["id"]})
    ok("... verlassenes Geraet darf nicht mehr signalisieren (403)", sig(A, A2, cid, B, B.dev, json.dumps({"t": "ice"}))[0] == 403)
    s, al = req("POST", f"/api/calls/{cid}/alive", A.k, {"device": A2["id"]})
    ok(f"/alive mit dem verlassenen Geraet: leg left (Client beendet dort) ({al})", s == 200 and al.get("leg") == "left" and al.get("state") == "active")

    # --- Uebernahme ---------------------------------------------------------------------------------------------------
    mA = sA.mark()
    s, d = join(B, B2, cid, [(A, A.dev)], role="handover", replaces=B.dev)
    me = [x for x in (d.get("call") or {}).get("legs", []) if x["device"] == B2["id"]]
    ok(f"B2 uebernimmt (handover, replaces B1) ({s}, {me and me[0]['role']})", s == 200 and me and me[0]["role"] == "handover" and me[0].get("replaces") == B.dev["id"])
    ok("A bekommt das Angebot von B2", any(b["t"] == "offer" and b["pc"] == pc(A.dev, B2) for _, b in got(A, A.dev, cid, B, B2)))
    s0 = sig(B, B2, cid, B, B.dev, json.dumps({"t": "state", "ready": True, "replaces": B.dev["id"]}))[0]
    ok(f"B2 meldet dem alten Geraet 'ready' (eigenes Geraet, versiegelt) ({s0})", s0 == 200 and any(b.get("ready") for _, b in got(B, B.dev, cid, B, B2)))
    s, d = req("POST", f"/api/calls/{cid}/leave", B.k, {"device": B.dev["id"], "reason": "handover"})
    with connect() as c:
        st, cd = c.execute("SELECT state, callee_device FROM chat_call WHERE id=?", (cid,)).fetchone()
    ok(f"B1 verlaesst: Anruf laeuft weiter, callee_device = B2 ({s}, {st}, {cd == B2['id']})", s == 200 and st == "active" and cd == B2["id"])
    ev = sA.wait("call.state", lambda x: x["call_id"] == cid and x.get("device") == B2["id"], mA)
    ok("A bekommt call.state active mit dem neuen Geraet (moved: callee)", ev and ev.get("moved") == "callee")
    l_ = legs(A, cid)
    ok("... Endpunkte A + B2, B2 jetzt member", {x["device"]: x["role"] for x in l_} == {A.dev["id"]: "member", B2["id"]: "member"})
    ok("... B1 darf nicht mehr signalisieren", sig(B, B.dev, cid, A, A.dev, json.dumps({"t": "ice"}))[0] == 403)
    ok("... Signale A <-> B2 gehen", sig(A, A.dev, cid, B, B2, json.dumps({"t": "ice"}))[0] == 200 and sig(B, B2, cid, A, A.dev, json.dumps({"t": "ice"}))[0] == 200)
    # 2.3/E0-Client im Anruf: Raum gesperrt
    s, d = req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup", "connected": True})
    ok(f"Hauptgeraet legt auf (ohne device) -> Anruf endet fuer alle ({s})", s == 200 and legs(A, cid) == [])
    time.sleep(0.3)
    cid2 = secrets.token_hex(16)
    old_caps = ["reneg", "want", "bye"]
    s, d = req("POST", "/api/calls", A.k, {"id": cid2, "chat_id": ab, "media": "audio", "device": A.dev["id"], "caps": old_caps,
                                            "signals": {x["id"]: seal(A, A.dev, cid2, B, x, body("offer", 1, pc(A.dev, x))) for x in B.devs}})
    req("POST", f"/api/calls/{cid2}/answer", B.k, {"device": B.dev["id"], "caps": CAPS,
                                                   "signals": {A.dev["id"]: seal(B, B.dev, cid2, A, A.dev, body("answer", 1, pc(A.dev, B.dev)))}})
    with connect() as c:
        v = c.execute("SELECT v FROM chat_call WHERE id=?", (cid2,)).fetchone()[0]
    s, g = req("GET", f"/api/calls/{cid2}", B.k)
    ok(f"Anrufer ohne multi (E0/2.3-Client): v=1, nicht joinable (join_block old) ({v}, {g['call']['join_block']})",
       v == 1 and not g["call"]["joinable"] and g["call"]["join_block"] == "old")
    s, d = join(B, B2, cid2, [(A, A.dev)], role="companion")
    ok(f"... Beitreten -> 409 'braucht Saga 2.4' ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "old" and "2.4" in d.get("error", ""))
    s, d = join(B, B2, cid2, [(A, A.dev)], role="companion", caps=old_caps)
    ok(f"... beitretendes Geraet ohne multi -> 409 ({s})", s == 409 and d.get("join_block") == "old")
    ok("... 1:1 laeuft wie bisher (Signale A <-> B)", sig(A, A.dev, cid2, B, B.dev, json.dumps({"t": "ice"}))[0] == 200)
    req("POST", f"/api/calls/{cid2}/end", A.k, {"reason": "hangup"})

    # --- E5: Gruppenanruf -------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-rb", "ca-rc", "ca-rd", "ca-re", "ca-rf"]})
    gid = d["chat"]["id"]
    sC, sE = A_.Stream(Cc.k), A_.Stream(E.k)
    time.sleep(0.5)
    s, chd = req("GET", f"/api/chats/{gid}?device={A.dev['id']}", A.k)
    ok(f"Gruppen-Chat: call.group = true (Knopf), kein laufender Anruf ({chd['chat'].get('call')})",
       chd["chat"].get("call", {}).get("group") is True and chd["chat"]["call"]["running"] is None)
    g1 = secrets.token_hex(16)
    mB, mC, mE = sB.mark(), sC.mark(), sE.mark()
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    ok(f"Gruppenanruf starten (kind group, A allein, klingelt) ({s}, {d.get('call', {}).get('kind')}, {d.get('call', {}).get('state')})",
       s == 200 and d["call"]["kind"] == "group" and d["call"]["state"] == "ringing" and len(d["call"]["legs"]) == 1)
    with connect() as c:
        row = tuple(c.execute("SELECT v, topo, kind, callee FROM chat_call WHERE id=?", (g1,)).fetchone())
        inv = dict(c.execute("SELECT who, state FROM chat_call_invite WHERE call_id=?", (g1,)).fetchall())
    ok(f"... Zeile v=3, mesh, group ({row})", row == (3, "mesh", "group", ""))
    ok(f"... Einladungen: 2.4-Geraete klingeln, Emil (nur alte App) 'old' ({inv})",
       inv.get("ca-rb") == "ringing" and inv.get("ca-rc") == "ringing" and inv.get("ca-re") == "old")
    ev = sC.wait("call.invite", lambda x: x["call_id"] == g1, mC)
    ok("C bekommt call.invite (Gruppe, Absender, Klingeldauer, nicht still bei <= 8 Mitgliedern)",
       ev and ev["kind"] == "group" and ev["from"]["username"] == "ca-ra" and ev["silent"] is False and ev["members"] == 6)
    ok("E (ohne 2.4-Geraet) klingelt nicht", sE.none("call.invite", mE, 0.8))
    ok("... sieht aber die Teilnehmerliste (Hinweis im Chat) mit 'braucht Saga 2.4'", sE.wait("call.roster", lambda x: x["call_id"] == g1, mE) is not None
       and any(o["who"] == "ca-re" for o in req("GET", f"/api/calls/{g1}", E.k)[1]["call"]["old"]))
    from saga import push
    with connect() as c:
        msg = push.message(c, "ca-rc", "call.group", actor="ca-ra", data={"call": g1})
        msg_e = push.message(c, "ca-re", "call.group", actor="ca-ra", data={"call": g1})
    ok(f"Push 'call.group' (Text, Link /chats#call-…, group) nur fuer Eingeladene ({(msg or {}).get('body')})",
       msg and msg["url"] == f"/chats#call-{g1}" and msg.get("group") is True and msg_e is None)
    s, act = req("GET", "/api/calls/active", D.k)
    ok("D: /active zeigt den Gruppenanruf (joinable)", any(x["id"] == g1 and x["joinable"] and x["kind"] == "group" for x in act["calls"]))
    g2 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", B.k, {"id": g2, "chat_id": gid, "media": "audio", "device": B.dev["id"], "caps": CAPS})
    ok(f"Zweiter Gruppenanruf im selben Chat -> 409 mit laufendem ({s})", s == 409 and d.get("running") == g1)
    s, d = req("POST", "/api/calls", B.k, {"id": g2, "chat_id": gid, "media": "audio", "device": B.dev["id"], "caps": ["reneg"]})
    ok(f"2.3-Client startet Gruppenanruf -> 409 'braucht Saga 2.4' ({s})", s == 409)
    ok("Nicht-Mitglied: 404", req("GET", f"/api/calls/{g1}", X.k)[0] == 404 and join(X, X.dev, g1, [(A, A.dev)])[0] == 404)
    s, d = req("POST", f"/api/calls/{g1}/decline", F.k, {})
    with connect() as c:
        fst = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who='ca-rf'", (g1,)).fetchone()[0]
    ok(f"F lehnt ab: nur die eigene Einladung (declined), Anruf laeuft weiter ({fst})", s == 200 and fst == "declined"
       and req("GET", f"/api/calls/{g1}", A.k)[1]["call"]["state"] == "ringing")
    mA = sA.mark()
    t0 = time.time()
    s, d = join(B, B.dev, g1, [(A, A.dev)])
    ok(f"B tritt bei (Angebot an A) -> Anruf aktiv ({s}, {d.get('call', {}).get('state')})", s == 200 and d["call"]["state"] == "active")
    ok("A bekommt call.state active und das Angebot von B", sA.wait("call.state", lambda x: x["call_id"] == g1 and x["state"] == "active", mA)
       and any(b["t"] == "offer" for _, b in got(A, A.dev, g1, B, B.dev)))
    s, d = join(Cc, Cc.dev, g1, [(A, A.dev)])
    ok(f"C bietet nicht allen an -> 409 mit Liste ({s})", s == 409 and len(d.get("legs") or []) == 2)
    s, d = join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev)])
    ok(f"C tritt bei (Mesh: Angebote an A und B) ({s}, {len(d.get('call', {}).get('legs', []))} Endpunkte)", s == 200 and len(d["call"]["legs"]) == 3)
    ok("... A und B bekommen je ihr Angebot von C", got(A, A.dev, g1, Cc, Cc.dev) and got(B, B.dev, g1, Cc, Cc.dev))
    s, d = join(D, D.dev, g1, [(A, A.dev), (B, B.dev), (Cc, Cc.dev)])
    ok(f"D tritt bei: 4 Endpunkte (Grenze bei Video) ({s})", s == 200 and len(d["call"]["legs"]) == 4)
    PERF["beitritt_4_endpunkte_ms"] = round((time.time() - t0) * 1000)
    s, d = join(A, A2, g1, [(B, B.dev), (Cc, Cc.dev), (D, D.dev)], role="companion")
    ok(f"5. Endpunkt (eigenes Zusatzgeraet zaehlt mit) -> 409 voll ({s}, {d.get('join_block')}, limit {d.get('limit')})",
       s == 409 and d.get("join_block") == "full" and d.get("limit") == 4)
    s, g = req("GET", f"/api/calls/{g1}", F.k)
    ok(f"F sieht: nicht joinable (voll) ({g['call']['join_block']})", not g["call"]["joinable"] and g["call"]["join_block"] == "full")
    # Signal-Budget je Paar: viele Signale im 4er-Mesh gehen
    codes = set()
    for i in range(30):
        for (p1, d1), (p2, d2) in (((A, A.dev), (B, B.dev)), ((Cc, Cc.dev), (D, D.dev)), ((B, B.dev), (D, D.dev))):
            codes.add(sig(p1, d1, g1, p2, d2, json.dumps({"t": "ice", "pc": pc(d1, d2), "c": []}))[0])
    ok(f"Signal-Budget je Endpunkt-Paar: 90 Signale im 4er-Mesh ({codes})", codes == {200})
    # Busy: wer im Gruppenanruf ist, ist fuer 1:1-Anrufe besetzt
    bc = A_.dm(B, Cc)
    cid3 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", B.k, {"id": cid3, "chat_id": bc, "media": "audio", "device": B.dev["id"], "caps": CAPS,
                                            "signals": {x["id"]: seal(B, B.dev, cid3, Cc, x, body("offer", 1, pc(B.dev, x))) for x in Cc.devs}})
    ok(f"Im Gruppenanruf: eigener 1:1-Anruf -> 409 'telefonierst schon' ({s})", s == 409)
    # Einladen (erneut klingeln)
    mE = sE.mark()
    s, d = req("POST", f"/api/calls/{g1}/invite", A.k, {"uids": ["ca-rf", "ca-re", "ca-rb"]})
    ok(f"Erneut einladen: F klingelt wieder, E nicht (alte App), B ist schon dabei ({s}, n={d.get('n')})", s == 200 and d.get("n") == 1)
    # Verlassen
    s, d = req("POST", f"/api/calls/{g1}/leave", D.k, {"device": D.dev["id"]})
    ok(f"D verlaesst: Anruf laeuft weiter (3) ({s})", s == 200 and len(legs(A, g1)) == 3)
    s, d = join(F, F.dev, g1, [(A, A.dev), (B, B.dev), (Cc, Cc.dev)])
    ok(f"F tritt nach Ablehnen doch noch bei (jederzeit) ({s})", s == 200)
    req("POST", f"/api/calls/{g1}/end", F.k, {"device": F.dev["id"]})
    s, d = req("POST", f"/api/calls/{g1}/end", Cc.k, {})
    ok(f"C legt auf (ohne device: alle eigenen Geraete) ({s}, {d.get('left')})", s == 200 and d.get("left") == [Cc.dev["id"]])
    ok("... laeuft weiter mit A und B", req("GET", f"/api/calls/{g1}", A.k)[1]["call"]["state"] == "active")
    mA = sA.mark()
    s, d = req("POST", f"/api/calls/{g1}/leave", B.k, {"device": B.dev["id"]})
    ev = sA.wait("call.state", lambda x: x["call_id"] == g1 and x["state"] == "ended", mA)
    ok(f"B verlaesst: nur noch eine Person -> Ende fuer alle ({s}, {ev and ev.get('status')})", s == 200 and ev and ev["status"] == "ended")
    rows = [r for r in A_.call_rows(gid) if r.get("call") == g1]
    ok(f"Chat-Eintrag: group, n = 5 Personen, Dauer ({rows[-1] if rows else None})", rows and rows[-1].get("group") is True and rows[-1]["n"] == 5
       and rows[-1]["status"] == "ended" and rows[-1]["dur"] is not None)
    with connect() as c:
        inv = dict(c.execute("SELECT who, state FROM chat_call_invite WHERE call_id=?", (g1,)).fetchall())
    ok(f"Einladungen am Ende: dabei = joined, abgelehnt bleibt, alte App 'old' ({inv})", inv.get("ca-rb") == "joined" and inv.get("ca-re") == "old")
    s, d = join(D, D.dev, g1, [(A, A.dev)])
    ok(f"Nach dem Ende: Beitreten -> 409 beendet ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "ended")

    # --- Grenze nur Ton (6), Verpasst, Klingeldauer -------------------------------------------------------------------
    time.sleep(0.3)
    g3 = secrets.token_hex(16)
    req("POST", "/api/calls", A.k, {"id": g3, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    inside = [(A, A.dev)]
    codes = []
    for p, dv in ((B, B.dev), (Cc, Cc.dev), (D, D.dev), (F, F.dev), (A, A2)):
        s, d = join(p, dv, g3, [x for x in inside if x[0] is not p])
        codes.append(s)
        if s == 200:
            inside.append((p, dv))
    s, d = join(B, B2, g3, [x for x in inside if x[0] is not B])
    ok(f"Nur Ton: 6 Endpunkte (inkl. A2), der 7. -> voll ({codes}, {s}, limit {d.get('limit')})", codes == [200] * 5 and s == 409 and d.get("limit") == 6)
    with connect() as c, c:
        c.execute("UPDATE chat_call_leg SET seen=? WHERE call_id=? AND device=?", (time.time() - 3600, g3, F.dev["id"]))
    CL.tick()
    ok("Hintergrund: Endpunkt ohne Lebenszeichen verlaesst den Anruf", F.dev["id"] not in {x["device"] for x in legs(A, g3)})
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid='ca-rd'", (time.time(), gid))
    CL.tick()
    ok("Hintergrund: entferntes Mitglied verlaesst den laufenden Anruf", D.dev["id"] not in {x["device"] for x in legs(A, g3)})
    for p in (A, B, Cc):
        req("POST", f"/api/calls/{g3}/end", p.k, {})
    g4 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", Cc.k, {"id": g4, "chat_id": gid, "media": "video", "device": Cc.dev["id"], "caps": CAPS})
    ok(f"Gruppenanruf von C ({s})", s == 200)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET created=? WHERE id=?", (time.time() - 3600, g4))
    CL._OWN.add(g4)
    CL.tick()
    with connect() as c:
        st, rs = c.execute("SELECT state, reason FROM chat_call WHERE id=?", (g4,)).fetchone()
    nb = [n for n in A_.notifs("ca-ra") if n.get("call") == g4]
    ok(f"Gruppenanruf ohne Beitritt endet nach der Klingeldauer (timeout), Eingeladene: verpasst ({st}/{rs}, {len(nb)})",
       st == "ended" and rs == "timeout" and len(nb) == 1)
    rows = [r for r in A_.call_rows(gid) if r.get("call") == g4]
    ok("... Chat-Eintrag 'missed' (group)", rows and rows[-1]["status"] == "missed" and rows[-1].get("group"))

    # --- Messung: Signalisierung im 4er-Mesh (Server, ohne Browser) ----------------------------------------------------
    # eigene Personen (Anruf-Raten je Konto)
    G, H, I_, J = (A_.Person(u) for u in ("ca-rg", "ca-rh", "ca-ri", "ca-rj"))
    for p in (G, H, I_, J):
        caps_put(p, p.dev)
    s, d = req("POST", "/api/chats", G.k, {"kind": "free", "members": ["ca-rh", "ca-ri", "ca-rj"]})
    gid2 = d["chat"]["id"]
    g5 = secrets.token_hex(16)
    ppl = [(G, G.dev), (H, H.dev), (I_, I_.dev), (J, J.dev)]
    A, gid = G, gid2
    pid = os.environ.get("SAGA_SERVER_PID")
    c0 = _cpu(pid)
    t0 = time.time()
    req("POST", "/api/calls", A.k, {"id": g5, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    nsig = 0
    for i, (p, dv) in enumerate(ppl[1:], 1):
        s, d = join(p, dv, g5, [x for x in ppl[:i]])
        nsig += i
        for q, qd in ppl[:i]:                          # Antwort + 2x ICE + Zustand je Paar und Richtung
            sig(q, qd, g5, p, dv, body("answer", 1, pc(qd, dv)))
            for k in range(2):
                sig(q, qd, g5, p, dv, json.dumps({"t": "ice", "pc": pc(qd, dv), "c": [{"candidate": "candidate:1 1 udp 1 10.0.0.1 9 typ host"}]}))
                sig(p, dv, g5, q, qd, json.dumps({"t": "ice", "pc": pc(qd, dv), "c": [{"candidate": "candidate:1 1 udp 1 10.0.0.1 9 typ host"}]}))
            sig(q, qd, g5, p, dv, json.dumps({"t": "state", "pc": pc(qd, dv), "mic": True}))
            sig(p, dv, g5, q, qd, json.dumps({"t": "state", "pc": pc(qd, dv), "mic": True}))
            nsig += 7
    dt = time.time() - t0
    c1 = _cpu(pid)
    PERF["mesh4_signale"] = nsig
    PERF["mesh4_signalisierung_ms"] = round(dt * 1000)
    if c0 is not None and c1 is not None:
        PERF["mesh4_server_cpu_ms"] = round((c1 - c0) * 1000 / os.sysconf("SC_CLK_TCK"))
        PERF["mesh4_server_cpu_pct"] = round((c1 - c0) / os.sysconf("SC_CLK_TCK") / max(dt, 0.001) * 100, 1)
    ok(f"4er-Mesh: {nsig} Signale ueber den Server in {dt * 1000:.0f} ms (Ziel Aufbau <= 2 s)", dt <= 2.0 and len(legs(A, g5)) == 4)
    for p, _ in ppl:
        req("POST", f"/api/calls/{g5}/end", p.k, {})

    # --- Datenschutz --------------------------------------------------------------------------------------------------
    ok("Server sieht keine SDP (Datenbank)", not A_.plaintext_hits(SDP_MARK))


def _cpu(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            x = f.read().rsplit(")", 1)[1].split()
        return int(x[11]) + int(x[12])
    except (OSError, ValueError, IndexError, TypeError):
        return None


if __name__ == "__main__":
    main()
