"""Anrufe 2.5, L7 "Erneut klingeln" (saga/calls.py) - Server-Seite mit PyNaCl als Client (tests/chatlib.py).

Geprueft: Gruppenanruf-Objekt traegt invites (Mitglieder ohne Geraet im Anruf, ohne mich) mit Zustand ringing | declined |
missed | left | none | removed | old, since, Reihenfolge (klingelt, abgelehnt, nicht angenommen, …, dann Name),
invites_more; "ringing" nur innerhalb der Klingeldauer ab dem letzten Klingeln (danach missed, auch das eigene invite,
invite_since), Ereignis call.invites an Konten mit Geraet im Anruf (Beitritt, Ablehnen, Klingeln, Abgang, Entfernen,
Ablauf der Klingeldauer ueber tick), nicht an Mitglieder ausserhalb. /invite antwortet mit invites; entfernte Person
darf nur der Gastgeber erneut klingeln lassen (403 sonst, nichts geklingelt), Rate wie bisher (429), Begrenzung (50,
Rest als Zahl), grosse stille Gruppe (Start: none, gezieltes Klingeln laut: silent false + Push), Nachrichtenanfrage
klingelt nie, fremdes Mitglied (Prinzipal eines anderen Servers) bekommt "ringing" mit Handle, 1:1 ohne invites.
Wegwerf-Konten ca-k*; raeumt auf."""
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = R_.CAPS
FAR = "@~abcdefghijklmnop@peer.example"          # Mitglied auf einem anderen Server (Dokumentations-Host)


def cfg(**kw):
    base = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
            "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True}
    base.update(kw)
    auth.save_setting("calls", base)
    auth._settings_cache["v"] = None


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    cfg()
    for u, n in (("ca-ka", "Anna Beispiel"), ("ca-kb", "Bert Beispiel"), ("ca-kc", "Cora Beispiel"), ("ca-kd", "Dora Alt"),
                 ("ca-ke", "Emil Beispiel"), ("ca-kf", "Frieda Beispiel"), ("ca-kr", "Rita Rate")):
        auth.create_user(u, A_.PW, n)
    for i in range(10):
        auth.create_user(f"ca-ks{i}", A_.PW, f"Stille {i:02d}")
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
        with connect() as c, c:
            c.execute("DELETE FROM chat_member WHERE uid=?", (FAR,))
        A_.cleanup()
    print(f"\nANRUFE-KLINGELN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def st(lst):
    return {x["who"]: x["state"] for x in (lst or [])}


def get(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return d.get("call") or {}


def inv_ev(stream, cid, since, pred=None, timeout=6.0):
    return stream.wait("call.invites", lambda x: x["call_id"] == cid and (pred is None or pred(x)), since, timeout)


def run():
    from saga import calls as CL
    from saga import fed_calls                     # setzt CL.FED wie im Server
    assert CL.FED is fed_calls
    A, B, Cc, D, E, F, Rr = (A_.Person(u) for u in ("ca-ka", "ca-kb", "ca-kc", "ca-kd", "ca-ke", "ca-kf", "ca-kr"))
    for p in (A, B, Cc, E, F, Rr):
        R_.caps_put(p, p.dev)                       # D: nur alte App (kein "multi")
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-kb", "ca-kc", "ca-kd", "ca-ke", "ca-kf", "ca-kr"]})
    gid = d["chat"]["id"]
    sA, sB, sC, sD = A_.Stream(A.k), A_.Stream(B.k), A_.Stream(Cc.k), A_.Stream(D.k)
    time.sleep(0.5)

    # --- Start: Liste, Zustaende, Reihenfolge ----------------------------------------------------------------------
    g1 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": g1, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    call = d.get("call") or {}
    inv = call.get("invites") or []
    ok(f"Start: invites ohne mich, alle anderen Mitglieder ({s}, {[(x['who'], x['state']) for x in inv]})",
       s == 200 and "ca-ka" not in st(inv) and set(st(inv)) == {"ca-kb", "ca-kc", "ca-kd", "ca-ke", "ca-kf", "ca-kr"} and call.get("invites_more") == 0)
    ok("... 2.4-Geraete klingeln, Dora (alte App) 'old', zuletzt", st(inv).get("ca-kb") == "ringing" and st(inv)["ca-kd"] == "old" and inv[-1]["who"] == "ca-kd")
    rng = [x["name"] for x in inv if x["state"] == "ringing"]
    ok(f"... klingelnde nach Name sortiert, since = Beginn, Name + kein Anmeldename ({rng})",
       rng == sorted(rng, key=str.casefold) and all(abs(x["since"] - call["created"]) < 1 for x in inv if x["state"] == "ringing")
       and all(x["name"] != x["who"] for x in inv))
    cb = get(B, g1)
    ok(f"B (klingelt) sieht sich nicht, eigenes invite 'ringing' + invite_since ({cb.get('invite')}, {cb.get('invite_since')})",
       "ca-kb" not in st(cb.get("invites")) and "ca-ka" not in st(cb.get("invites")) and cb.get("invite") == "ringing"
       and abs((cb.get("invite_since") or 0) - call["created"]) < 1)

    # --- Beitritt: Liste ohne B, Ereignis an A (im Anruf), nicht an D (nicht dabei) --------------------------------
    mA, mD = sA.mark(), sD.mark()
    s, d = R_.join(B, B.dev, g1, [(A, A.dev)])
    ok(f"B tritt bei ({s}), Antwort-invites ohne A und B", s == 200 and not {"ca-ka", "ca-kb"} & set(st(d["call"].get("invites"))))
    ev = inv_ev(sA, g1, mA, lambda x: "ca-kb" not in st(x["invites"]))
    ok(f"call.invites an A nach dem Beitritt (ohne B, rv) ({bool(ev)})", ev and isinstance(ev.get("rv"), float) and "ca-kc" in st(ev["invites"]))
    ok("... nicht an D (Mitglied ohne Geraet im Anruf)", sD.none("call.invites", mD, 0.5))

    # --- Ablehnen -------------------------------------------------------------------------------------------------
    mA, mB = sA.mark(), sB.mark()
    t0 = time.time()
    s, _ = req("POST", f"/api/calls/{g1}/decline", Cc.k, {"reason": "declined"})
    ev = inv_ev(sA, g1, mA, lambda x: st(x["invites"]).get("ca-kc") == "declined")
    evb = inv_ev(sB, g1, mB, lambda x: st(x["invites"]).get("ca-kc") == "declined")
    cc = next((x for x in (ev or {}).get("invites", []) if x["who"] == "ca-kc"), {})
    ok(f"C lehnt ab -> call.invites an A und B (declined, since = jetzt) ({s}, {cc})", s == 200 and ev and evb and abs((cc.get("since") or 0) - t0) < 2)
    ok("... Reihenfolge: klingelt vor abgelehnt", [x["state"] for x in ev["invites"]].index("declined") > max(
        [i for i, x in enumerate(ev["invites"]) if x["state"] == "ringing"] or [-1]))

    # --- Klingeldauer vorbei: missed (Liste und eigenes invite), DB bleibt ringing -----------------------------------
    with connect() as c, c:
        c.execute("UPDATE chat_call_invite SET created=? WHERE call_id=? AND who='ca-ke'", (time.time() - 60, g1))
    ca = get(A, g1)
    ee = next((x for x in ca["invites"] if x["who"] == "ca-ke"), {})
    ce = get(E, g1)
    with connect() as c:
        raw = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who='ca-ke'", (g1,)).fetchone()[0]
    ok(f"Klingeldauer vorbei: E 'missed' seit created + ring_s ({ee}), eigenes invite 'missed' ({ce.get('invite')}), Tabelle '{raw}'",
       ee.get("state") == "missed" and abs(ee["since"] - (time.time() - 15)) < 3 and ce.get("invite") == "missed" and raw == "ringing")
    ok("... E nicht mehr im Abschnitt 'klingelt' (Reihenfolge ringing, declined, missed)",
       [x["state"] for x in ca["invites"] if x["state"] in ("ringing", "declined", "missed")]
       == sorted([x["state"] for x in ca["invites"] if x["state"] in ("ringing", "declined", "missed")], key=CL.INV_ORDER.get))
    mA = sA.mark()
    CL._OWN.add(g1)
    CL._XLAST[0] = time.time() - 100                # Ablauf faellt in das Fenster dieses Durchlaufs (Test-Prozess)
    n = CL.tick()
    ok(f"tick erkennt den Ablauf (Test-Prozess, {n} beendet) - Anruf laeuft weiter", get(A, g1).get("state") == "active")

    # --- Erneut klingeln (Mitglied, nicht Gastgeber) ---------------------------------------------------------------
    mE, mA = A_.Stream(E.k), sA.mark()
    me0 = 0
    time.sleep(0.3)
    s, d = req("POST", f"/api/calls/{g1}/invite", B.k, {"uids": ["ca-ke", "ca-kc"]})
    ok(f"B klingelt E und C erneut: {{ok, n, invites}} ({s}, n={d.get('n')}, {st(d.get('invites'))})",
       s == 200 and d.get("n") == 2 and st(d.get("invites")).get("ca-ke") == "ringing" and st(d["invites"]).get("ca-kc") == "ringing"
       and "invites_more" in d)
    evi = mE.wait("call.invite", lambda x: x["call_id"] == g1, me0)
    ok(f"... E bekommt call.invite (laut) ({evi and evi.get('silent')})", evi and evi["silent"] is False)
    ce = get(E, g1)
    ok(f"... E: invite 'ringing', invite_since neu (> Start) ({ce.get('invite_since')})",
       ce.get("invite") == "ringing" and (ce.get("invite_since") or 0) > ce["created"] + 0.5)
    ok("... call.invites an A", inv_ev(sA, g1, mA, lambda x: st(x["invites"]).get("ca-ke") == "ringing") is not None)

    # --- Abgang: left ------------------------------------------------------------------------------------------------
    s, _ = R_.join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev)])
    mA = sA.mark()
    req("POST", f"/api/calls/{g1}/leave", Cc.k, {"device": Cc.dev["id"]})
    ev = inv_ev(sA, g1, mA, lambda x: st(x["invites"]).get("ca-kc") == "left")
    ok(f"C tritt bei und verlaesst -> 'left' (war dabei) im Ereignis ({s}, {bool(ev)})", s == 200 and ev)


    # --- Entfernen: nur der Gastgeber klingelt erneut ----------------------------------------------------------------
    s, _ = R_.join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev)])
    mA = sA.mark()
    s2, _ = req("POST", f"/api/calls/{g1}/moderate", A.k, {"device": A.dev["id"], "target": Cc.dev["id"], "action": "remove"})
    ev = inv_ev(sA, g1, mA, lambda x: st(x["invites"]).get("ca-kc") == "removed")
    ok(f"Gastgeber entfernt C -> 'removed' (Ereignis) ({s}, {s2}, {bool(ev)})", s == 200 and s2 == 200 and ev)
    mC = sC.mark()
    s, d = req("POST", f"/api/calls/{g1}/invite", B.k, {"uids": ["ca-kc"]})
    with connect() as c:
        raw = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who='ca-kc'", (g1,)).fetchone()[0]
    ok(f"B (kein Gastgeber) darf C nicht erneut klingeln lassen: 403 mit Text, bleibt 'removed' ({s}, {d.get('error')}, {raw})",
       s == 403 and "begonnen" in (d.get("error") or "") and d.get("removed") is True and raw == "removed")
    ok("... C klingelt nicht", sC.none("call.invite", mC, 0.6))
    s, d = R_.join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev)])
    ok(f"... C kann weiterhin nicht beitreten ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "removed")
    s, d = req("POST", f"/api/calls/{g1}/invite", B.k, {"uids": ["ca-ke", "ca-kc"]})
    ok(f"... auch nicht gemischt mit anderen (alles abgelehnt, E nicht geklingelt) ({s})", s == 403)
    s, d = req("POST", f"/api/calls/{g1}/invite", A.k, {"uids": ["ca-kc"]})
    ok(f"Gastgeber klingelt C erneut ({s}, {st(d.get('invites')).get('ca-kc')})", s == 200 and st(d.get("invites")).get("ca-kc") == "ringing")
    s, d = R_.join(Cc, Cc.dev, g1, [(A, A.dev), (B, B.dev)])
    ok(f"... C kann wieder beitreten ({s})", s == 200)
    req("POST", f"/api/calls/{g1}/leave", Cc.k, {"device": Cc.dev["id"]})

    # --- Nachrichtenanfrage, spaeter hinzugekommen (none), Mitglied anderer Server ------------------------------------
    with connect() as c, c:
        c.execute("UPDATE chat_member SET state='req' WHERE chat_id=? AND uid='ca-kf'", (gid,))
        c.execute("DELETE FROM chat_call_invite WHERE call_id=? AND who='ca-kr'", (g1,))   # wie spaeter dazugekommen
        c.execute("INSERT INTO chat_member (chat_id, uid, role, joined, state) VALUES (?,?,'member',?,'ok') ON CONFLICT DO NOTHING",
                  (gid, FAR, time.time()))
    ca = get(A, g1)
    ok(f"Mitglied mit offener Nachrichtenanfrage nicht in der Liste; spaeter dazugekommen: 'none' ohne since ({st(ca['invites'])})",
       "ca-kf" not in st(ca["invites"]) and st(ca["invites"]).get("ca-kr") == "none"
       and next(x for x in ca["invites"] if x["who"] == "ca-kr")["since"] is None)
    s, d = req("POST", f"/api/calls/{g1}/invite", A.k, {"uids": ["ca-kf"]})
    ok(f"... Nachrichtenanfrage klingelt auch per /invite nicht ({s}, n={d.get('n')})", s == 200 and d.get("n") == 0)
    far = next((x for x in ca["invites"] if x["who"] == FAR), {})
    ok(f"Mitglied anderer Server: 'none' mit Handle und Herkunft ({far})", far.get("state") == "none" and far.get("handle") == FAR[1:]
       and far.get("host") == "peer.example")
    s, d = req("POST", f"/api/calls/{g1}/invite", A.k, {"uids": [FAR]})
    far = next((x for x in d.get("invites") or [] if x["who"] == FAR), {})
    ok(f"... Klingeln (Auftrag an den anderen Server) -> sofort 'ringing' ({s}, n={d.get('n')}, {far.get('state')})",
       s == 200 and d.get("n") == 1 and far.get("state") == "ringing")

    # --- Rate ------------------------------------------------------------------------------------------------------
    codes = [req("POST", f"/api/calls/{g1}/invite", Rr.k, {"uids": ["ca-ke"]})[0] for _ in range(CL.CALL_RATE + 1)]
    s, d = req("POST", f"/api/calls/{g1}/invite", Rr.k, {"uids": ["ca-ke"]})
    ok(f"Rate wie bisher: {CL.CALL_RATE} je Minute, dann 429 mit Text ({codes}, {d.get('error')})",
       codes[:CL.CALL_RATE] == [200] * CL.CALL_RATE and codes[-1] == 429 and s == 429 and "warten" in (d.get("error") or ""))

    # --- Begrenzung (im Test-Prozess) ----------------------------------------------------------------------------
    keep = CL.INVITES_MAX
    try:
        CL.INVITES_MAX = 2
        with connect() as c:
            lst, more = CL.invites(c, CL._load(c, g1), "ca-ka")
    finally:
        CL.INVITES_MAX = keep
    ok(f"Begrenzung: {len(lst)} Eintraege + invites_more {more} (zuerst klingelnde)", len(lst) == 2 and more >= 3 and lst[0]["state"] == "ringing")

    # --- Pruefrunde 1: wer den Chat verlassen hat (Endpunkt bis zum naechsten tick), bekommt kein call.invites mehr
    # (am Ende, kurz: der tick des Servers entfernt B sonst aus dem Anruf)
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid='ca-kb'", (time.time(), gid))
    mA, mB = sA.mark(), sB.mark()
    s, _ = req("POST", f"/api/calls/{g1}/decline", E.k, {"reason": "declined"})
    ev = inv_ev(sA, g1, mA, lambda x: st(x["invites"]).get("ca-ke") == "declined")
    ok(f"Ex-Mitglied mit Endpunkt: kein call.invites (A schon) ({s}, {bool(ev)})", s == 200 and ev and sB.none("call.invites", mB, 0.6))
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=NULL WHERE chat_id=? AND uid='ca-kb'", (gid,))

    # --- Ablauf live: Klingeldauer 4 s, Ereignis ueber tick des Servers ---------------------------------------------
    for p in (A, B):
        req("POST", f"/api/calls/{g1}/end", p.k, {"reason": "hangup", "device": p.dev["id"]})
    cfg(ring_s=4)
    time.sleep(5.2)                                 # Server haelt Einstellungen 5 s vor (auth.settings) - sonst rechnet ein
    g2 = secrets.token_hex(16)                      # tick noch mit 45 s und das Fenster des Ablaufs faellt aus (wackelte)
    s, d = req("POST", "/api/calls", A.k, {"id": g2, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    s2, _ = R_.join(B, B.dev, g2, [(A, A.dev)])
    mA = sA.mark()
    ev = inv_ev(sA, g2, mA, lambda x: st(x["invites"]).get("ca-ke") == "missed", timeout=10)
    ok(f"Klingeldauer (4 s) vorbei -> call.invites (missed) ohne Zutun, Anruf laeuft ({s}, {s2}, {bool(ev)})",
       s == 200 and s2 == 200 and ev and get(A, g2).get("state") == "active")
    with connect() as c:
        nb = [x for x in A_.notifs("ca-ke") if x.get("call") == g2]
    ok(f"... 'Verpasster Anruf' erst beim Ende wie bisher ({len(nb)})", not nb)
    for p in (A, B):
        req("POST", f"/api/calls/{g2}/end", p.k, {"reason": "hangup", "device": p.dev["id"]})
    nb = [x for x in A_.notifs("ca-ke") if x.get("call") == g2]
    ok(f"... nach dem Ende: E verpasst ({len(nb)})", len(nb) == 1)
    cfg()

    # --- Grosse stille Gruppe ----------------------------------------------------------------------------------------
    S = [A_.Person(f"ca-ks{i}") for i in range(3)]
    for p in S:
        R_.caps_put(p, p.dev)
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [f"ca-ks{i}" for i in range(10)]})
    gid2 = d["chat"]["id"]
    sS = A_.Stream(S[0].k)
    time.sleep(0.4)
    g3 = secrets.token_hex(16)
    m0 = sS.mark()
    s, d = req("POST", "/api/calls", A.k, {"id": g3, "chat_id": gid2, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    inv = (d.get("call") or {}).get("invites") or []
    ok(f"Stille Gruppe (11 Mitglieder): Start -> 'none' (nie geklingelt), ohne 2.4-Geraet 'old' ({s}, {sorted(set(st(inv).values()))}, {len(inv)})",
       s == 200 and len(inv) == 10 and {st(inv)[f"ca-ks{i}"] for i in range(3)} == {"none"} and {st(inv)[f"ca-ks{i}"] for i in range(3, 10)} == {"old"})
    e0 = sS.wait("call.invite", lambda x: x["call_id"] == g3, m0)
    ok("... Start klingelt still (silent true)", e0 and e0["silent"] is True and get(S[0], g3).get("silent") is True)
    m0 = sS.mark()
    s, d = req("POST", f"/api/calls/{g3}/invite", A.k, {"uids": ["ca-ks0"]})
    e1 = sS.wait("call.invite", lambda x: x["call_id"] == g3, m0)
    ok(f"Gezielt klingeln: ringing, call.invite laut, Anruf-Objekt silent false ({s}, {st(d.get('invites')).get('ca-ks0')})",
       s == 200 and st(d.get("invites")).get("ca-ks0") == "ringing" and e1 and e1["silent"] is False and get(S[0], g3).get("silent") is False)
    from saga import push
    with connect() as c:
        msg = push.message(c, "ca-ks0", "call.group", actor="ca-ka", data={"call": g3})
    ok("... Push 'call.group' fuer die geklingelte Person", msg is not None and msg.get("group") is True)
    ok("... die anderen bleiben 'none'", st(d["invites"]).get("ca-ks1") == "none" and d["invites"][0]["who"] == "ca-ks0")
    req("POST", f"/api/calls/{g3}/end", A.k, {"reason": "hangup", "device": A.dev["id"]})

    # --- 1:1 bleibt ohne invites -----------------------------------------------------------------------------------
    dmc = A_.dm(A, B)
    cid, s, _ = A_.call(A, B, dmc)
    c1 = get(A, cid)
    ok(f"1:1-Anruf ohne invites ({s}, {c1.get('kind')})", s == 200 and c1 and "invites" not in c1 and "invite_since" not in c1)
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})


if __name__ == "__main__":
    main()
