"""Bugs-DB 2174: Gruppenanruf - ein Geraet meldet seine Anruf-Faehigkeiten (PUT /api/calls/caps) erst NACH dem Start
(frisch verbunden: beim Start Einladung 'old'). Solange es noch klingelt, wird die Einladung 'ringing' und es klingelt wie
beim Start (saga/calls.py ring_late): call.invite an die Person (still/laut wie ring_group), since = Zeitpunkt der
Meldung (volle Klingeldauer wie am Geraet; grosse stille Gruppe: bleibt der Beginn, Liste "none" wie beim Start),
call.invites an die Teilnehmenden. Kein Doppelklingeln (gleiche Meldung erneut, zweites Geraet),
echte Alt-Geraete (Meldung ohne "multi") bleiben 'old', nach der Klingeldauer bzw. nach dem Ende nichts mehr; Anruf ueber
Knoten (topo horn): nur "horn"/"horn_ring" hebt die Einladung (nur "multi" reicht nicht).
Bugs-DB 2185: ein Fehler beim Nachklingeln gibt kein 500, die naechste gleiche Meldung holt das Klingeln nach.
Wegwerf-Konten ca-n*; raeumt auf."""
import os
import secrets
import sys
import threading
import time

import psycopg

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_raum as R_  # noqa: E402
import auth  # noqa: E402
from db import DATABASE_URL, connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = R_.CAPS
OLD_APP = ["reneg", "want", "bye"]                  # 2.3-App: meldet Faehigkeiten, aber kein "multi"


def cfg(**kw):
    base = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
            "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": False}
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
    for u, n in (("ca-na", "Anna Beispiel"), ("ca-nb", "Bert Beispiel"), ("ca-nc", "Cora Spaet"), ("ca-nd", "Dora Alt"),
                 ("ca-ne", "Emil Zuspaet"), ("ca-nf", "Frieda Knoten")):
        auth.create_user(u, A_.PW, n)
    for i in range(8):
        auth.create_user(f"ca-ns{i}", A_.PW, f"Stille {i:02d}")
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
    print(f"\nANRUFE-NACHMELDEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def st(lst):
    return {x["who"]: x["state"] for x in (lst or [])}


def get(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return d.get("call") or {}


def raw(cid, who):
    with connect() as c:
        r = c.execute("SELECT state, created FROM chat_call_invite WHERE call_id=? AND who=?", (cid, who)).fetchone()
    return tuple(r) if r else (None, None)


def start(a, gid):
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", a.k, {"id": cid, "chat_id": gid, "media": "audio", "device": a.dev["id"], "caps": CAPS})
    return cid, s, d.get("call") or {}


def group(a, members):
    s, d = req("POST", "/api/chats", a.k, {"kind": "free", "members": members})
    assert s == 200, (s, d)
    return d["chat"]["id"]


def end(a, cid):
    req("POST", f"/api/calls/{cid}/end", a.k, {"reason": "hangup", "device": a.dev["id"]})


def run():
    A, B, Cc, D, E, F = (A_.Person(u) for u in ("ca-na", "ca-nb", "ca-nc", "ca-nd", "ca-ne", "ca-nf"))
    for p in (A, B):
        R_.caps_put(p, p.dev)
    R_.caps_put(D, D.dev, OLD_APP)                  # D: alte App (Faehigkeiten ohne "multi"); C, E, F: noch nichts gemeldet
    sA, sC, sD, sE, sF = A_.Stream(A.k), A_.Stream(Cc.k), A_.Stream(D.k), A_.Stream(E.k), A_.Stream(F.k)
    time.sleep(0.5)

    # --- Start: C (frisch verbunden, ohne Meldung) und D (alte App) bekommen 'old' -------------------------------------
    gid = group(A, ["ca-nb", "ca-nc", "ca-nd"])
    mC, mD = sC.mark(), sD.mark()
    g1, s, call = start(A, gid)
    inv = st(call.get("invites"))
    ok(f"Start: B klingelt, C (noch keine Meldung) und D (alte App) 'old' ({s}, {inv})",
       s == 200 and inv.get("ca-nb") == "ringing" and inv.get("ca-nc") == "old" and inv.get("ca-nd") == "old")
    ok("... C klingelt nicht (kein call.invite)", sC.none("call.invite", mC, 0.8))

    # --- C meldet "multi" nach: Einladung ringing, Klingeln, Liste an A ----------------------------------------------
    mA, mC = sA.mark(), sC.mark()
    t0 = time.time()
    s = R_.caps_put(Cc, Cc.dev)
    ev = sC.wait("call.invite", lambda x: x["call_id"] == g1, mC)
    ok(f"C meldet die Faehigkeiten nach ({s}) -> call.invite an C (Gruppe, laut wie der Start: silent false) ({ev})",
       s == 200 and ev and ev.get("kind") == "group" and ev.get("silent") is False and ev.get("ring_s") == 45)
    rs, rc = raw(g1, "ca-nc")
    ok(f"... Tabelle: 'ringing', created = Zeitpunkt der Meldung (volle Klingeldauer wie am Geraet) ({rs}, {round((rc or 0) - t0, 3)})",
       rs == "ringing" and t0 - 0.01 <= (rc or 0) <= time.time() and rc > call["created"])
    cc = get(Cc, g1)
    ok(f"... C: eigenes invite 'ringing', invite_since = Meldung ({cc.get('invite')}, {cc.get('invite_since')})",
       cc.get("invite") == "ringing" and abs((cc.get("invite_since") or 0) - rc) < 0.01)
    evA = sA.wait("call.invites", lambda x: x["call_id"] == g1 and st(x["invites"]).get("ca-nc") == "ringing", mA)
    ok("... call.invites an A (C 'klingelt')", evA is not None)
    ok("... Anruf-Objekt bei A: C nicht mehr unter old", "ca-nc" not in [x.get("who") for x in get(A, g1).get("old") or []])

    # --- kein Doppelklingeln: gleiche Meldung erneut, zweites Geraet mit "multi" --------------------------------------
    mC = sC.mark()
    R_.caps_put(Cc, Cc.dev)
    dv2 = Cc.add_device()
    s2 = R_.caps_put(Cc, dv2)
    ok(f"Gleiche Meldung erneut + zweites Geraet meldet ({s2}): kein zweites call.invite", sC.none("call.invite", mC, 1.2))

    # --- echte Alt-Geraete: Meldung ohne "multi" aendert nichts ---------------------------------------------------------
    mD = sD.mark()
    s = R_.caps_put(D, D.dev, OLD_APP + ["screen2"])
    ok(f"D (alte App) meldet geaenderte Faehigkeiten ohne 'multi' ({s}): bleibt 'old', klingelt nicht",
       s == 200 and raw(g1, "ca-nd")[0] == "old" and sD.none("call.invite", mD, 0.8) and st(get(A, g1).get("invites")).get("ca-nd") == "old")
    end(A, g1)

    # --- nach der Klingeldauer bzw. nach dem Ende: nichts ---------------------------------------------------------------
    gid2 = group(A, ["ca-nb", "ca-ne"])
    g2, s, call = start(A, gid2)
    ok(f"Zweiter Anruf: E 'old' ({s}, {st(call.get('invites')).get('ca-ne')})", s == 200 and st(call.get("invites")).get("ca-ne") == "old")
    with connect() as c, c:
        c.execute("UPDATE chat_call_invite SET created=? WHERE call_id=? AND who='ca-ne'", (time.time() - 60, g2))
    mE = sE.mark()
    s = R_.caps_put(E, E.dev)
    ok(f"E meldet nach Ablauf der Klingeldauer ({s}): bleibt 'old', klingelt nicht", s == 200 and raw(g2, "ca-ne")[0] == "old"
       and sE.none("call.invite", mE, 0.8))
    end(A, g2)

    # --- keine Rechteausweitung: wer den Chat verlassen hat bzw. Anruf, den ein anderer Server fuehrt (lead): nichts ----
    g6, s, call = start(A, gid2)
    with connect() as c, c:
        c.execute("UPDATE chat_device SET caps='' WHERE uid='ca-ne'")
        c.execute("UPDATE chat_call_invite SET state='old' WHERE call_id=? AND who='ca-ne'", (g6,))
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid='ca-ne'", (time.time(), gid2))
    mE = sE.mark()
    s = R_.caps_put(E, E.dev)
    ok(f"Chat verlassen: E meldet nach ({s}) -> bleibt 'old', klingelt nicht", raw(g6, "ca-ne")[0] == "old" and sE.none("call.invite", mE, 0.8))
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=NULL WHERE chat_id=? AND uid='ca-ne'", (gid2,))
        c.execute("UPDATE chat_device SET caps='' WHERE uid='ca-ne'")
        c.execute("UPDATE chat_call SET lead=999999 WHERE id=?", (g6,))
    mE = sE.mark()
    s = R_.caps_put(E, E.dev)
    ok(f"Anruf eines anderen Servers (lead): E meldet nach ({s}) -> bleibt 'old', klingelt nicht",
       s == 200 and raw(g6, "ca-ne")[0] == "old" and sE.none("call.invite", mE, 0.8))
    with connect() as c, c:
        c.execute("UPDATE chat_call SET lead=NULL WHERE id=?", (g6,))
    end(A, g6)

    gid3 = group(A, ["ca-nb", "ca-nf"])
    g3, s, call = start(A, gid3)
    end(A, g3)
    ok(f"Dritter Anruf beendet ({get(A, g3).get('state')})", get(A, g3).get("state") == "ended")
    mF = sF.mark()
    R_.caps_put(F, F.dev, ["reneg", "multi"])
    ok("F meldet nach dem Ende: klingelt nicht", sF.none("call.invite", mF, 0.8) and raw(g3, "ca-nf")[0] == "old")

    # --- Anruf ueber Knoten (topo horn): nur "multi" reicht nicht, "horn" hebt die Einladung -----------------------------
    F2 = F.add_device()
    g4, s, call = start(A, gid3)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET topo='horn' WHERE id=?", (g4,))
        c.execute("UPDATE chat_device SET caps='' WHERE uid='ca-nf'")
        c.execute("UPDATE chat_call_invite SET state='old' WHERE call_id=? AND who='ca-nf'", (g4,))
    mF = sF.mark()
    R_.caps_put(F, F.dev, CAPS)
    ok("Knoten-Anruf: F meldet nur 'multi' -> bleibt 'old', klingelt nicht", sF.none("call.invite", mF, 0.8) and raw(g4, "ca-nf")[0] == "old")
    s = R_.caps_put(F, F2, CAPS + ["horn", "ws"])
    ev = sF.wait("call.invite", lambda x: x["call_id"] == g4, mF)
    ok(f"... zweites Geraet meldet 'horn' ({s}) -> 'ringing' + call.invite", s == 200 and ev and raw(g4, "ca-nf")[0] == "ringing")
    with connect() as c, c:
        c.execute("UPDATE chat_call SET topo='mesh' WHERE id=?", (g4,))
    end(A, g4)

    # --- grosse Gruppe (stiller Start): Nachmelden klingelt still, Liste bleibt "none" wie beim Start ------------------
    G = A_.Person("ca-ns0")
    sG = A_.Stream(G.k)
    time.sleep(0.3)
    gid5 = group(A, [f"ca-ns{i}" for i in range(8)])
    g5, s, call = start(A, gid5)
    ok(f"Grosse Gruppe (9 Mitglieder): G ohne Meldung 'old' ({s}, {st(call.get('invites')).get('ca-ns0')})",
       s == 200 and st(call.get("invites")).get("ca-ns0") == "old")
    mG = sG.mark()
    R_.caps_put(G, G.dev)
    ev = sG.wait("call.invite", lambda x: x["call_id"] == g5, mG)
    rs, rc = raw(g5, "ca-ns0")
    ok(f"... G meldet nach: call.invite still (silent true), Tabelle 'ringing' mit created = Beginn ({ev and ev.get('silent')}, {rs})",
       ev and ev.get("silent") is True and rs == "ringing" and abs((rc or 0) - call["created"]) < 0.01)
    ok("... Liste bei A: G 'none' (stiller Start, nie laut geklingelt)", st(get(A, g5).get("invites")).get("ca-ns0") == "none")
    end(A, g5)

    # --- Bugs-DB 2179: Wettlauf mit dem Anrufende. Eine Transaktion haelt die Anruf-Zeile gesperrt (wie _finish bzw.
    # Beitreten/Annehmen), die Meldung kommt genau dazwischen. Ohne Ende: klingelt nach der Freigabe; mit Ende: nichts -------
    for ended in (False, True):                         # B ruft an (A hat schon 6 Anrufe je Minute, CALL_RATE)
        with connect() as c, c:                         # F ohne Meldung: beim Start 'old', kein call.invite
            c.execute("UPDATE chat_device SET caps='' WHERE uid='ca-nf'")
        mF, out = sF.mark(), {}
        g7, s, call = start(B, gid3)
        ok(f"2179: Anruf von B, F 'old' ({s}, {st(call.get('invites')).get('ca-nf')})", s == 200 and st(call.get("invites")).get("ca-nf") == "old")
        th = threading.Thread(target=lambda: out.update(s=R_.caps_put(F, F.dev, CAPS)))
        with connect() as c:
            c.execute("SELECT id FROM chat_call WHERE id=? FOR UPDATE", (g7,))
            if ended:                                   # wie _finish: Zustand, klingelnde Einladungen verpasst
                c.execute("UPDATE chat_call SET state='ended', reason='hangup', ended=? WHERE id=?", (time.time(), g7))
                c.execute("UPDATE chat_call_invite SET state='missed' WHERE call_id=? AND state='ringing'", (g7,))
            th.start()
            th.join(1.0)
            waited = th.is_alive()                     # neu: die Meldung wartet auf die Sperre des Anrufs
            c.commit()
        th.join(15)
        if ended:
            ok(f"2179: F meldet nach, waehrend der Anruf beendet wird ({out.get('s')}, wartete {waited}) -> bleibt 'old', "
               "klingelt nicht", out.get("s") == 200 and raw(g7, "ca-nf")[0] == "old" and sF.none("call.invite", mF, 1.2))
        else:
            ev = sF.wait("call.invite", lambda x: x["call_id"] == g7, mF)
            ok(f"2179: F meldet nach, waehrend der Anruf gesperrt ist ({out.get('s')}, wartete {waited}) -> klingelt nach der "
               "Freigabe", out.get("s") == 200 and ev is not None and raw(g7, "ca-nf")[0] == "ringing")
            end(B, g7)

    # --- Bugs-DB 2185: Fehler beim Nachklingeln (hier per Trigger erzwungen). Vorher: PUT /api/calls/caps -> 500 und die
    # gleiche Meldung danach aenderte nichts mehr (Faehigkeiten schon gespeichert) - es klang nie. Jetzt: 200, die Meldung
    # bleibt gespeichert, die naechste (gleiche) Meldung holt das Klingeln nach ------------------------------------------------
    with connect() as c, c:
        c.execute("UPDATE chat_device SET caps='' WHERE uid='ca-nf'")
    with psycopg.connect(DATABASE_URL, autocommit=True) as pg:      # DDL direkt (db.translate laesst CREATE aus)
        pg.execute("CREATE OR REPLACE FUNCTION t2185_fehler() RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 't2185'; END $$ "
                   "LANGUAGE plpgsql")
        pg.execute("DROP TRIGGER IF EXISTS t2185_fehler ON chat_call_invite")
        pg.execute("CREATE TRIGGER t2185_fehler BEFORE UPDATE ON chat_call_invite FOR EACH ROW "
                   "WHEN (NEW.who = 'ca-nf' AND NEW.state = 'ringing') EXECUTE FUNCTION t2185_fehler()")
    try:
        mF = sF.mark()
        g8, s, call = start(B, gid3)
        ok(f"2185: Anruf von B, F 'old' ({s}, {st(call.get('invites')).get('ca-nf')})", s == 200 and st(call.get("invites")).get("ca-nf") == "old")
        s = R_.caps_put(F, F.dev, CAPS)
        with connect() as c:
            kept = c.execute("SELECT caps FROM chat_device WHERE id=?", (F.dev["id"],)).fetchone()[0]
        r8, quiet = raw(g8, "ca-nf")[0], sF.none("call.invite", mF, 0.8)
        ok(f"2185: Fehler beim Nachklingeln -> Meldung trotzdem {s} (vorher 500), Faehigkeiten gespeichert, bleibt 'old' "
           f"({kept}, {r8}, still {quiet})", s == 200 and '"multi"' in (kept or "") and r8 == "old" and quiet)
        with psycopg.connect(DATABASE_URL, autocommit=True) as pg:
            pg.execute("DROP TRIGGER IF EXISTS t2185_fehler ON chat_call_invite")
        s = R_.caps_put(F, F.dev, CAPS)
        ev = sF.wait("call.invite", lambda x: x["call_id"] == g8, mF)
        ok(f"2185: gleiche Meldung erneut ({s}) -> klingelt jetzt", s == 200 and ev is not None and raw(g8, "ca-nf")[0] == "ringing")
        mF = sF.mark()
        s = R_.caps_put(F, F.dev, CAPS)
        ok(f"2185: dritte gleiche Meldung ({s}) -> kein zweites call.invite", s == 200 and sF.none("call.invite", mF, 1.0))
        end(B, g8)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as pg:
            pg.execute("DROP TRIGGER IF EXISTS t2185_fehler ON chat_call_invite")
            pg.execute("DROP FUNCTION IF EXISTS t2185_fehler()")


if __name__ == "__main__":
    main()
