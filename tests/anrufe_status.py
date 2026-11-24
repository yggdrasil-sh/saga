"""Anruf-Weiche (2.6, Bugs-DB 2029-2, saga/calls.py ring_gate) - eigener Status wirkt auf Anrufe. Server-Seite mit PyNaCl
als Client (tests/chatlib.py), Push ueber eine APNs-Attrappe (wie tests/anrufe_voip.py).

Geprueft: verfuegbar klingelt wie bisher (call.ring + VoIP-Push) · Nicht stoeren: Anruf angelegt (chat_call.hold='dnd'),
kein call.ring, kein Push, call.quiet nur an die eigenen Geraete (why dnd, from), Anrufer sieht "klingelt", fehlt in
/api/calls/active der angerufenen Person, GET /api/calls/{id} quiet: true, Abbrechen -> verpasst wie immer ·
Wiederholungsanruf derselben Person binnen 180 s klingelt (why repeat), nach 181 s wieder still · Ausnahme (Person)
klingelt · Gruppenanruf: stille Einladung (silent + quiet) ohne Push fuer die Person mit Nicht stoeren, die anderen wie
bisher · Im Anruf: ohne "waiting1" auf allen aktiven Geraeten besetzt wie bisher (auch wenn nur ein Geraet es meldet) ·
mit "waiting1": Anklopfen (200, waiting, call.ring waiting:true, hold knock, kein Push) · "Nicht nerven": weitere Versuche
derselben Person sofort besetzt mit again (eigener Text), je Versuch ein Chat-Eintrag und eine Glocke ohne Push, andere
Person darf einmal anklopfen · in_call busy -> besetzt · nach dem Gespraech genau ein Push je anrufender Person (n aus den
Versuchen), auch spaeter nicht noch einmal · 1:1 ueber Knoten mit Nicht stoeren: hold dnd, call.quiet mit dm, keine
Einladung, kein Push · features waiting1 · klingelnder (noch nicht angenommener) Anruf: weiter besetzt, nicht "im Anruf"
(kein Anklopfen) · im Prozess: user_status.call_changed (status.call, 2029-4) nach Annehmen/Ende mit beiden Konten,
IN_CALL nur fuer angenommene Anrufe; ring_gate fuer Gruppen, Personen anderer Server und unbekannte Art · Ende zu Ende
"im Anruf" bei Chat-Partnern (chat.status in_call/available live; mit Online-Status aus kein Ereignis) · eine Uhr: Anruf-
und Push-Weiche entscheiden mit verstellter Testuhr gleich (Nicht stoeren mit Ende, Wiederholungsanruf, Sammelmeldung).
Wegwerf-Konten ca-s*; raeumt auf."""
import http.server
import json
import os
import secrets
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_horn_dm as HD  # noqa: E402
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga.core import local_ref  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
FP = int(os.environ.get("HK_TEST_FAKE_APNS", "18461"))
CAPS = ["reneg", "want", "bye", "screen2", "multi"]
WAIT = CAPS + ["waiting1"]
TOK = {"voip": "e1" * 32, "apns": "e2" * 32}
USERS = [("ca-sa", "Anna Status"), ("ca-sb", "Ben Status"), ("ca-sc", "Cora Status"), ("ca-sd", "Dora Status"),
         ("ca-se", "Emil Status"), ("ca-sf", "Frieda Status"), ("ca-sg", "Gabi Status"), ("ca-sh", "Hanna Status")]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": False}


def hits(tok, since, kind=None):
    with A_.LOCK:
        out = [g for g in A_.GOT[since:] if g[0] == "/3/device/" + tok]
    out = [(g[1], json.loads(g[2] or b"{}")) for g in out]
    return [x for x in out if kind is None or x[1].get("kind") == kind]


def hold(cid):
    with connect() as c:
        r = c.execute("SELECT hold, state, reason FROM chat_call WHERE id=?", (cid,)).fetchone()
    return tuple(r) if r else None


def status(p, **b):
    return req("PUT", "/api/account/status", p.k, b)


def sprefs(p, **b):
    return req("PUT", "/api/account/status/prefs", p.k, b)


def end(p, cid):
    return req("POST", f"/api/calls/{cid}/end", p.k, {"reason": "hangup"})[0]


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    with connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()
    keep_apns = r[0] if r else None
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", CALLS)
    auth._settings_cache["v"] = None
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), A_.Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    apns_setting()
    time.sleep(A_.WAIT)                                  # Einstellungen im Server-Prozess (Zwischenspeicher)
    try:
        run()
        in_process()
        clock_part()
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
    print(f"\nANRUFE-STATUS {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def apns_setting():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                                serialization.NoEncryption()).decode()
    with connect() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('apns', ?)", (json.dumps({
            "enabled": True, "team_id": "ABCDE12345", "key_id": "KEY1234567", "bundle_id": "org.example.saga", "p8": auth._enc(pem),
            "url": f"http://127.0.0.1:{FP}"}),))


def apns_setup(B):
    with connect() as c, c:
        import anrufe_voip as V
        iphone = V.device(c, B.uid, "iPhone")
        V.sub(c, B.uid, "apns-voip", TOK["voip"], iphone)
        V.sub(c, B.uid, "apns", TOK["apns"], iphone)


def answer(B, dev, cid, frm):
    ans = json.dumps({"t": "answer", "sdp": "v=0\r\na=fingerprint:sha-256 AA:BB\r\n", "fp": ["sha-256 AA:BB"]})
    return req("POST", f"/api/calls/{cid}/answer", B.k, {"device": dev["id"], "signals": A_.seal_for(B, dev, cid, frm, [frm.dev], ans)})


def run():
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, H = (P[u] for u, _ in USERS)
    apns_setup(B)
    ab, cb, db_, eb, fb, gb, hb = (A_.dm(x, B) for x in (A, Cc, D, E, F, G, H))
    sB, sA = A_.Stream(B.k), A_.Stream(A.k)
    time.sleep(0.5)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"config: features mit waiting1 ({s})", s == 200 and "waiting1" in (cf.get("features") or []))

    # --- verfuegbar: wie bisher ---------------------------------------------------------------------------------------
    m0, mB = A_.mark(), sB.mark()
    cid, s, d = A_.call(F, B, fb)
    ev = sB.wait("call.ring", lambda x: x["call_id"] == cid, mB)
    ok(f"verfuegbar: klingelt (200, call.ring ohne waiting, hold leer) ({s}, {hold(cid)})",
       s == 200 and ev and "waiting" not in ev and hold(cid)[0] is None and not (d.get("call") or {}).get("quiet"))
    ok("... VoIP-Push an das iPhone", A_.got(lambda x: x[0] == "/3/device/" + TOK["voip"], m0))
    end(F, cid)

    # --- Nicht stoeren: still ----------------------------------------------------------------------------------------
    s, v = status(B, state="dnd")
    ok(f"Ben: Nicht stoeren ({s}, {v.get('state')})", s == 200 and v.get("state") == "dnd")
    m0, mB, mA = A_.mark(), sB.mark(), sA.mark()
    c1, s, d = A_.call(A, B, ab)
    call = d.get("call") or {}
    ok(f"dnd: Anruf angelegt, Anna sieht klingelt ohne quiet ({s}, {call.get('state')}, {hold(c1)})",
       s == 200 and call.get("state") == "ringing" and not call.get("quiet") and hold(c1) == ("dnd", "ringing", None))
    q = sB.wait("call.quiet", lambda x: x["call_id"] == c1, mB)
    ok(f"... call.quiet an Bens Geraete (why dnd, from Anna, ring_s) ({q})",
       q and q.get("why") == "dnd" and (q.get("from") or {}).get("name") == "Anna Status" and q.get("chat_id") == ab and q.get("ring_s") == 45
       and q.get("kind") == "dm")
    ok("... kein call.ring bei Ben", not [e for e in sB.ev[mB:] if e[0] == "call.ring" and e[1].get("call_id") == c1])
    ok("... call.quiet nicht an Anna", sA.none("call.quiet", mA, 0.3))
    time.sleep(2.0)
    ok(f"... kein Push (weder VoIP noch APNs zu diesem Anruf) ({len(hits(TOK['voip'], m0))}, {[x[1].get('chat') for x in hits(TOK['apns'], m0)]})",
       not hits(TOK["voip"], m0) and not [x for x in hits(TOK["apns"], m0) if x[1].get("call") == c1 or x[1].get("chat") == ab])
    s, act = req("GET", "/api/calls/active", B.k)
    ok(f"... fehlt in Bens /api/calls/active ({s})", s == 200 and c1 not in [x["id"] for x in act.get("calls") or []])
    s, act = req("GET", "/api/calls/active", A.k)
    ok("... bei Anna in /api/calls/active", c1 in [x["id"] for x in act.get("calls") or []])
    s, g = req("GET", f"/api/calls/{c1}", B.k)
    ok(f"... GET /api/calls/{{id}} fuer Ben: quiet true ({s}, {(g.get('call') or {}).get('quiet')})", s == 200 and g["call"].get("quiet") is True)
    end(A, c1)
    rows = [x for x in A_.call_rows(ab) if x.get("call") == c1]
    ok(f"... Abbrechen: verpasst im Chat ({[x.get('status') for x in rows]})", [x.get("status") for x in rows] == ["missed"])

    # Wiederholungsanruf binnen 180 s
    m0, mB = A_.mark(), sB.mark()
    c2, s, d = A_.call(A, B, ab)
    ev = sB.wait("call.ring", lambda x: x["call_id"] == c2, mB)
    ok(f"Wiederholungsanruf binnen 180 s: klingelt trotz Nicht stoeren ({s}, {hold(c2)})", s == 200 and ev and hold(c2)[0] is None)
    ok("... mit VoIP-Push", A_.got(lambda x: x[0] == "/3/device/" + TOK["voip"], m0))
    end(A, c2)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET created=created-181 WHERE id=?", (c1,))
    mB = sB.mark()
    c3, s, d = A_.call(A, B, ab)
    ok(f"... nach 181 s wieder still ({s}, {hold(c3)})", s == 200 and hold(c3)[0] == "dnd" and sB.wait("call.quiet", lambda x: x["call_id"] == c3, mB))
    end(A, c3)

    # Ausnahme
    s, _ = sprefs(B, exceptions=[{"type": "user", "ref": local_ref(H.uid)}])
    mB = sB.mark()
    c4, s2, _ = A_.call(H, B, hb)
    ok(f"Ausnahme (Person): klingelt bei Nicht stoeren ({s}, {s2}, {hold(c4)})",
       s == 200 and s2 == 200 and hold(c4)[0] is None and sB.wait("call.ring", lambda x: x["call_id"] == c4, mB))
    end(H, c4)
    sprefs(B, exceptions=[])

    # Gruppenanruf: stille Einladung
    # Gruppe startet Dora: Anna hat eben angerufen - ihre Einladung waere ein Wiederholungsanruf (klingelt)
    for p in (D, B, G):
        R_.caps_put(p, p.dev)
    s, d = req("POST", "/api/chats", D.k, {"kind": "free", "members": [B.uid, G.uid]})
    gid = d["chat"]["id"]
    sG = A_.Stream(G.k)
    time.sleep(0.4)
    m0, mB, mG = A_.mark(), sB.mark(), sG.mark()
    g1 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", D.k, {"id": g1, "chat_id": gid, "media": "audio", "device": D.dev["id"], "caps": CAPS})
    eb_ = sB.wait("call.invite", lambda x: x["call_id"] == g1, mB)
    eg = sG.wait("call.invite", lambda x: x["call_id"] == g1, mG)
    ok(f"Gruppe: Ben (Nicht stoeren) stille Einladung silent+quiet ({s}, {eb_ and (eb_.get('silent'), eb_.get('quiet'))})",
       s == 200 and eb_ and eb_.get("silent") is True and eb_.get("quiet") is True)
    ok(f"... Gabi wie bisher (laut, ohne quiet) ({eg and (eg.get('silent'), eg.get('quiet'))})", eg and eg.get("silent") is False and "quiet" not in eg)
    s, gg = req("GET", f"/api/calls/{g1}", B.k)
    ok(f"... Anruf-Objekt fuer Ben: silent + quiet, Einladung klingelt ({(gg.get('call') or {}).get('silent')}, {(gg.get('call') or {}).get('invite')})",
       s == 200 and gg["call"].get("silent") is True and gg["call"].get("quiet") is True and gg["call"].get("invite") == "ringing")
    time.sleep(2.0)
    ok(f"... kein Push an Ben ({len(hits(TOK['voip'], m0))}, {len(hits(TOK['apns'], m0))})",
       not hits(TOK["voip"], m0) and not [x for x in hits(TOK["apns"], m0) if x[1].get("chat") == gid])
    req("POST", f"/api/calls/{g1}/end", D.k, {})
    s, v = status(B, state="available")
    ok(f"Ben wieder verfuegbar ({s})", s == 200 and v.get("state") == "available")

    # --- Im Anruf: Anklopfen -------------------------------------------------------------------------------------------
    B2 = B.add_device()
    mB = sB.mark()
    R_.caps_put(B, B.dev, WAIT)
    R_.caps_put(B, B2, WAIT)
    mc, s, d = A_.call(G, B, gb)
    f1, s1, d1 = A_.call(F, B, fb)
    ok(f"Anruf klingelt noch (nicht angenommen): besetzt wie bisher, kein Anklopfen ({s1}, {d1.get('again')}, {hold(f1)})",
       s1 == 409 and d1.get("busy") is True and "again" not in d1 and hold(f1)[0] is None)
    R_.caps_put(B, B.dev, CAPS)
    R_.caps_put(B, B2, [])
    s2, d2 = answer(B, B.dev, mc, G)
    ok(f"Gespraech Gabi - Ben laeuft ({s}, {s2})", s == 200 and s2 == 200)
    e1, s, d = A_.call(E, B, eb)
    ok(f"im Anruf, Geraete ohne waiting1: besetzt wie bisher, ohne again ({s}, {d.get('busy')}, {d.get('again')}, {hold(e1)})",
       s == 409 and d.get("busy") is True and "again" not in d and hold(e1) == (None, "ended", "busy"))
    R_.caps_put(B, B.dev, WAIT)
    e2, s, d = A_.call(E, B, eb)
    ok(f"... nur ein Geraet meldet waiting1: weiter besetzt ({s}, {d.get('busy')})", s == 409 and d.get("busy") is True and "again" not in d)
    R_.caps_put(B, B2, WAIT)
    m0, mB = A_.mark(), sB.mark()
    k1, s, d = A_.call(Cc, B, cb)
    ev = sB.wait("call.ring", lambda x: x["call_id"] == k1, mB)
    ok(f"alle Geraete waiting1: Anklopfen (200, waiting, hold knock) ({s}, {(d.get('call') or {}).get('waiting')}, {hold(k1)})",
       s == 200 and (d.get("call") or {}).get("waiting") is True and hold(k1) == ("knock", "ringing", None))
    ok(f"... call.ring mit waiting:true ({ev and ev.get('waiting')})", ev and ev.get("waiting") is True)
    s, g = req("GET", f"/api/calls/{k1}", B.k)
    ok("... Anruf-Objekt fuer Ben: waiting", s == 200 and g["call"].get("waiting") is True)
    end(Cc, k1)
    k2, s, d = A_.call(Cc, B, cb)
    ok(f"Nicht nerven: zweiter Versuch sofort besetzt mit again ({s}, {d.get('again')}, {d.get('error')}, {hold(k2)})",
       s == 409 and d.get("busy") is True and d.get("again") is True and "telefoniert noch" in (d.get("error") or "")
       and hold(k2) == ("again", "ended", "busy"))
    k3, s, d = A_.call(Cc, B, cb)
    ok(f"... dritter Versuch ebenso ({s}, {d.get('again')})", s == 409 and d.get("again") is True)
    st = [x.get("status") for x in A_.call_rows(cb) if x.get("call") in (k1, k2, k3)]
    ok(f"... je Versuch ein Chat-Eintrag ({st})", st == ["missed", "busy", "busy"])
    bells = [n for n in A_.notifs(B.uid) if n.get("call") in (k1, k2, k3)]
    ok(f"... je Versuch eine Glocke mit hold ({[n.get('hold') for n in bells]})", [n.get("hold") for n in bells] == ["knock", "again", "again"])
    k4, s, d = A_.call(D, B, db_)
    ok(f"andere Person darf einmal anklopfen ({s}, {hold(k4)})", s == 200 and hold(k4)[0] == "knock")
    end(D, k4)
    sprefs(B, in_call="busy")
    f2, s, d = A_.call(F, B, fb)
    ok(f"in_call busy: besetzt statt Anklopfen ({s}, {d.get('again')}, {hold(f2)})", s == 409 and "again" not in d and hold(f2)[0] is None)
    sprefs(B, in_call="knock")
    time.sleep(2.5)
    knock_chats = {cb, db_}
    ok(f"waehrend des Gespraechs: kein Push zu den Anklopf-Versuchen ({[x[1].get('chat') for x in hits(TOK['apns'], m0)]})",
       not [x for x in hits(TOK["apns"], m0, "call.missed") if x[1].get("chat") in knock_chats]
       and not [x for x in hits(TOK["voip"], m0) if x[1].get("chat") in knock_chats])
    m1 = A_.mark()
    end(G, mc)
    time.sleep(3.0)
    sums = [x[1] for x in hits(TOK["apns"], m1, "call.missed") if x[1].get("chat") in knock_chats]
    ok(f"nach dem Gespraech: genau ein Push je anrufender Person, n aus den Versuchen "
       f"({sorted((x.get('chat'), x.get('n')) for x in sums)})",
       sorted((x.get("chat"), x.get("n")) for x in sums) == sorted([(cb, 3), (db_, 1)]))
    with connect() as c:
        flag = [json.loads(r[0]).get("sum") for r in c.execute("SELECT data FROM notification WHERE uid=? AND kind='call.missed' AND data LIKE '%\"hold\"%'",
                                                               (B.uid,)).fetchall()]
    ok(f"... Versuche als gesammelt markiert ({flag})", flag and all(flag))
    e3, s, d = A_.call(E, B, eb)
    ok(f"danach klingelt es wieder normal ({s}, {hold(e3)})", s == 200 and hold(e3)[0] is None)
    end(E, e3)
    time.sleep(2.5)
    again = [x for x in hits(TOK["apns"], m1, "call.missed") if x[1].get("chat") in knock_chats]
    ok(f"... kein zweiter Sammel-Push ({len(again)})", len(again) == 2)

    # --- 1:1 ueber Knoten mit Nicht stoeren -----------------------------------------------------------------------------
    HD.setting(horn=True, horn_all=True)
    HD.node(True)
    for dv in (B.dev, B2):
        HD.caps(B, HD.CAPS + ["waiting1"], dv)
    HD.caps(H, HD.CAPS)
    status(B, state="dnd")
    m0, mB = A_.mark(), sB.mark()
    h1, s, d = HD.hcall(H, hb)
    q = sB.wait("call.quiet", lambda x: x["call_id"] == h1, mB)
    ok(f"1:1 ueber Knoten + Nicht stoeren: angelegt, hold dnd ({s}, {(d.get('call') or {}).get('dm')}, {hold(h1)})",
       s == 200 and (d.get("call") or {}).get("dm") is True and hold(h1) == ("dnd", "ringing", None))
    ok(f"... call.quiet mit dm, keine Einladung ({q})", q and q.get("dm") is True
       and not [e for e in sB.ev[mB:] if e[0] == "call.invite" and e[1].get("call_id") == h1])
    time.sleep(2.0)
    ok("... kein Push", not hits(TOK["voip"], m0) and not [x for x in hits(TOK["apns"], m0) if x[1].get("chat") == hb])
    s, act = req("GET", "/api/calls/active", B.k)
    ok("... fehlt in Bens /api/calls/active", h1 not in [x["id"] for x in act.get("calls") or []])
    end(H, h1)
    status(B, state="available")
    HD.setting(horn=False)
    in_call_part(G, B, Cc, gb)               # Gabi: Anna/Emil/Frieda haben die Paar-Grenze (3/min) schon fast erreicht


def in_call_part(A, B, Cc, ab):
    """2029 Ende zu Ende: calls.changed -> user_status.call_changed -> SHOWN_HOOKS -> chat_status.status_changed -> Live
    chat.status bei Chat-Partnern. A (hier Gabi) ruft Ben an, Ben nimmt an -> Cora (Chat mit Ben, beide Online-Status an) sieht
    "im Anruf", nach dem Ende wieder verfuegbar. Ben mit Online-Status aus: Cora bekommt gar nichts (auch kein Ereignis,
    dessen Zeitpunkt den Anruf verriete)."""
    sC = A_.Stream(Cc.k)
    time.sleep(0.5)
    s, pr = req("POST", "/api/chat/presence", Cc.k, {"users": [B.uid]})
    ok(f"im Anruf: Cora sieht Bens Status ueber den gemeinsamen Chat ({s}, {pr.get('status')})",
       s == 200 and (pr.get("status") or {}).get(B.uid) == {"s": "available"})
    is_b = lambda st: (lambda x: x.get("username") == B.uid and (x.get("status") or {}).get("s") == st)     # noqa: E731
    mC = sC.mark()
    i1, s, _ = A_.call(A, B, ab)
    ok(f"... klingelnd: noch nicht im Anruf (kein chat.status bei Cora) ({s})", s == 200 and sC.none("chat.status", mC, 1.0))
    s2, _ = answer(B, B.dev, i1, A)
    e = sC.wait("chat.status", is_b("in_call"), mC)
    ok(f"Ben nimmt an: Cora bekommt live chat.status in_call ({s2}, {e})", s2 == 200 and e is not None and e["status"] == {"s": "in_call"})
    s, pr = req("POST", "/api/chat/presence", Cc.k, {"users": [B.uid]})
    ok(f"... presence zeigt dasselbe ({pr.get('status')})", (pr.get("status") or {}).get(B.uid) == {"s": "in_call"})
    mC = sC.mark()
    end(A, i1)
    e = sC.wait("chat.status", is_b("available"), mC)
    ok(f"Ende: Cora bekommt live chat.status available ({e})", e is not None and e["status"] == {"s": "available"})
    s, _ = req("PUT", "/api/chat/privacy", B.k, {"online": False})
    time.sleep(1.5)
    mC = sC.mark()
    i2, s1, _ = A_.call(A, B, ab)
    s2, _ = answer(B, B.dev, i2, A)
    time.sleep(2.0)
    s, pr = req("POST", "/api/chat/presence", Cc.k, {"users": [B.uid]})
    mid = (pr.get("status") or {}).get(B.uid)
    end(A, i2)
    time.sleep(2.5)
    evs = [(t_, x) for t_, x in sC.ev[mC:] if t_ in ("chat.status", "chat.presence") and x.get("username") == B.uid]
    ok(f"Ben Online-Status aus: Anruf angenommen ({s1}, {s2}), presence ohne im Anruf ({mid})", s1 == 200 and s2 == 200 and mid == {"s": "available"})
    ok(f"... Cora bekommt waehrend Anruf und Ende kein Ereignis zu Ben ({evs})", not evs)
    req("PUT", "/api/chat/privacy", B.k, {"online": True})
    sC.close()


def in_process():
    """call_changed und Weiche im Prozess (eigene Datenbank-Zeilen)."""
    from saga import calls as CL, user_status as US
    got = []
    orig = US.call_changed
    US.call_changed = lambda c, uid, in_call=None: got.append(uid)
    cid = secrets.token_hex(16)
    try:
        now = time.time()
        with connect() as c, c:
            ch = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm', ?, ?, ?) RETURNING id",
                           ("ca-sa|ca-sx", "ca-sa", now)).fetchone()[0]
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b) "
                      "VALUES (?,?,?,?,?,?,'ringing',?,?,?)", (cid, ch, "ca-sa", "ca-sx", "d" * 32, "audio", now, now, now))
            ring = (CL._talking(c, "ca-sx"), CL._busy_other(c, "ca-sx", ""))
            c.execute("UPDATE chat_call SET state='active', answered=? WHERE id=?", (now, cid))
            act = (CL._talking(c, "ca-sx"), US.IN_CALL is CL._talking)
            CL._finish(c, CL._load(c, cid), "hangup", time.time())
            # Pruefer: zurueckgehaltener Anruf (hold dnd) - klingelnd nicht besetzt, angenommen besetzt wie jeder andere
            q = secrets.token_hex(16)
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, hold) "
                      "VALUES (?,?,?,?,?,?,'ringing',?,?,?,'dnd')", (q, ch, "ca-sa", "ca-sx", "d" * 32, "audio", now, now, now))
            held = (CL._busy(c, "ca-sx"), CL._busy_other(c, "ca-sx", ""), CL._busy(c, "ca-sa"))
            c.execute("UPDATE chat_call SET state='active', answered=? WHERE id=?", (now, q))
            held_act = (CL._busy(c, "ca-sx"), CL._busy_other(c, "ca-sx", ""), CL._talking(c, "ca-sx"),
                        CL._talk_since(c, "ca-sx") is not None)
            c.execute("DELETE FROM chat_call WHERE id=?", (q,))
        ok(f"klingelnd: besetzt, aber nicht im Anruf; angenommen: im Anruf (IN_CALL = _talking) ({ring}, {act})",
           ring == (False, True) and act == (True, True))
        ok(f"hold dnd klingelnd: Angerufene nicht besetzt, Anrufende schon; angenommen: besetzt und im Anruf ({held}, {held_act})",
           held == (False, False, True) and held_act == (True, True, True, True))
        ok(f"call_changed nach dem Ende fuer beide Konten ({got})", sorted(got) == ["ca-sa", "ca-sx"])
        with connect() as c:
            g = CL.ring_gate(c, "ca-sa", "ca-sb", "", "group")
            try:
                CL.ring_gate(c, "ca-sa", "ca-sb", "", "fax")
                bad = False
            except ValueError:
                bad = True
            rg = CL.ring_gate(c, "@~abcdefghijklmnop@b.example", "ca-sa", "", "dm")
        ok(f"ring_gate: Gruppe verfuegbar -> ring, unbekannte Art -> ValueError, Person anderer Server -> ring remote ({g}, {rg})",
           g.route == "ring" and bad and rg == ("ring", None, "remote"))
    finally:
        US.call_changed = orig
        with connect() as c, c:
            c.execute("DELETE FROM chat_call WHERE id=?", (cid,))
            c.execute("DELETE FROM chat WHERE dm_key='ca-sa|ca-sx'")


def clock_part():
    """Eine Uhr (Bugs-DB 2029): Anruf-Weiche (calls.ring_gate) und Push-Weiche (push.REACH/enqueue) entscheiden mit derselben
    Testuhr; Anruf-Zeitstempel (chat_call.created) laufen auf ihr (Wiederholungsanruf, Sammelmeldung). Im Prozess, Uhr einen
    Tag voraus und angehalten - die Echtzeit wuerde jeweils anders entscheiden."""
    from saga import calls as CL, push as P, testclock as TC, user_status as US
    U, X = "ca-sb", "ca-se"
    TC.set_clock({"freeze": True})
    TC.set_clock({"set": time.time() + 86400})
    ch = None

    def both(tag):
        with connect() as c:                         # ohne Commit: vorgemerkte Auftraege/Zaehler werden verworfen
            g = CL.ring_gate(c, U, X, "", "dm")
            r = P.REACH(c, U, X, "chat.message")
            n = P.enqueue(c, U, "chat.message", actor=X, data={"chat": "uhr-" + tag})
        return g.route, g.why, r.push, n

    try:
        with connect() as c, c:
            US.set_status(c, U, {"state": "dnd", "dur": 600})
            ch = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', ?, ?) RETURNING id", (X, TC.now())).fetchone()[0]
        a = both("a")
        ok(f"Testuhr 1 Tag voraus, Nicht stoeren 10 min: Anruf still UND keine Mitteilung ({a})",
           a[0] == "silent" and a[1] == "dnd" and a[2] is False and a[3] == 0)
        TC.set_clock({"advance": 601})
        b = both("b")
        ok(f"Testuhr ueber das Ende hinaus: Anruf klingelt UND Mitteilung geht raus ({b})",
           b[0] == "ring" and b[1] == "available" and b[2] is True and b[3] >= 1)
        # Wiederholungsanruf und Sammelmeldung: chat_call.created auf derselben Uhr (wie calls/fed_calls ihn anlegen)
        with connect() as c, c:
            v, _ = US.set_status(c, U, {"state": "dnd"})
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, ended, hold) "
                      "VALUES (?,?,?,?,?,'audio','ended','timeout',?,?,'dnd')", (secrets.token_hex(16), ch, X, U, "e" * 32, TC.now(), TC.now()))
        TC.set_clock({"advance": 100})
        r1 = both("c")
        with connect() as c:
            mh = P._missed_held_calls(c, U, v["since"], TC.now())
        TC.set_clock({"advance": 81})
        r2 = both("d")
        ok(f"Wiederholungsanruf nach 100 s klingelt, Mitteilung bleibt zurueckgehalten; nach 181 s wieder still ({r1}, {r2})",
           r1[:2] == ("ring", "repeat") and r1[2] is False and r2[:2] == ("silent", "dnd"))
        ok(f"Sammelmeldung zaehlt den zurueckgehaltenen Anruf im Zeitraum (gleiche Uhr wie since) ({mh})", mh == 1)
    finally:
        TC.set_clock({"reset": True})
        with connect() as c, c:
            if ch is not None:
                c.execute("DELETE FROM chat WHERE id=?", (ch,))
            c.execute("DELETE FROM user_kv WHERE uid=? AND k='status_held'", (U,))
            US.set_status(c, U, {"state": "available"})


if __name__ == "__main__":
    main()
