"""Anrufe 2.5, L9 "1:1 -> Konferenz" - Gegenproben der Pruefung (saga/call_move.py): Atomaritaet, Rennen, Leaks.

Ergaenzt tests/anrufe_umzug.py (gleicher Aufbau: gekoppelter Knoten als Registereintrag, PyNaCl als Client):
- Fehler MITTEN in der Schreib-Transaktion (Klingeln wirft, im Prozess) -> kein Chat, keine Mitglieder, Anruf weiter
  1:1 im Direkt-Chat (callee gesetzt), kein Eintrag im alten 1:1, danach geht der Umzug normal.
- beide Personen klicken gleichzeitig "Person hinzufuegen" (verschiedene Neue): genau EIN neuer Chat, der Anruf zieht
  einmal um, beide 200, die zweite Anfrage laeuft wie L8 im neuen Chat (beide Neuen Mitglied), genau ein Eintrag
  "moved" im alten 1:1.
- Doppelklick derselben Person (zweimal dieselbe Neue): ein Chat, zweite Antwort ohne moved.
- Die Neue bekommt kein call.move (kein Verweis auf das 1:1), der neue Chat hat keinen Klartext-Namen.
- Auflegen der anderen Person gleichzeitig mit dem Umzug: entweder umgezogen oder beendet - nie halb (kein Chat ohne
  Anruf-Eintrag bzw. Anruf am alten Chat mit leerem callee).
Wegwerf-Konten ca-g*; raeumt auf."""
import asyncio
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
CAPS = HD.CAPS
USERS = (("ca-ga", "Gina Anfang"), ("ca-gb", "Gero Beide"), ("ca-gc", "Gesa Neu"), ("ca-gd", "Gitta Neu"), ("ca-ge", "Gunda Eins"),
         ("ca-gf", "Gert Zwei"), ("ca-gg", "Gabi Drei"), ("ca-gh", "Hanno Vier"), ("ca-gi", "Ines Fuenf"), ("ca-gj", "Jost Sechs"))
HD.NODE = "gp-knoten.example.org"


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls", "chat_v3")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("chat_v3", True)
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        HD.node(False)
        A_.cleanup()
    print(f"\nANRUFE-UMZUG-GEGENPROBE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def add(p, cid, uids):
    return req("POST", f"/api/calls/{cid}/add", p.k, {"uids": uids})


def crow(cid):
    with connect() as c:
        r = c.execute("SELECT chat_id, callee, kind, state FROM chat_call WHERE id=?", (cid,)).fetchone()
    return dict(zip(("chat_id", "callee", "kind", "state"), r)) if r else None


def free_of(*uids):
    ph = ",".join("?" * len(uids))
    with connect() as c:
        return [r[0] for r in c.execute(f"SELECT id FROM chat WHERE kind='free' AND created_by IN ({ph}) ORDER BY id", uids).fetchall()]


def members(cid):
    with connect() as c:
        return {u: role for u, role in c.execute("SELECT uid, role FROM chat_member WHERE chat_id=? AND left_at IS NULL", (cid,)).fetchall()}


def hjoin(p, cid):
    HD.fresh()
    return req("POST", f"/api/calls/{cid}/join", p.k, {"device": p.dev["id"], "role": "member", "caps": CAPS})


def dm_call(a, b, chat):
    cid, s, d = HD.hcall(a, chat)
    assert s == 200, (s, d)
    s, d = hjoin(b, cid)
    assert s == 200, (s, d)
    return cid


def both(f1, f2):
    """Zwei Anfragen moeglichst gleichzeitig (Schranke) -> (ergebnis1, ergebnis2)."""
    out, bar = [None, None], threading.Barrier(2)

    def go(i, f):
        bar.wait()
        out[i] = f()
    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate((f1, f2))]
    for x in ts:
        x.start()
    for x in ts:
        x.join(30)
    return out


def run():
    from saga import calls as CL, call_move as CM
    from saga import fed_calls, fed_chat                 # setzt CL.FED wie im Server
    assert CL.FED is fed_calls and fed_chat
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, H, I_, J = (P[u] for u, _ in USERS)
    for p in P.values():
        for dv in p.devs:
            HD.caps(p, CAPS, dev=dv)
    HD.setting(horn_all=True, horn_all_y=True)
    HD.node(True)

    # --- 1. Fehler mitten in der Schreib-Transaktion (im Prozess): nichts geaendert --------------------------------
    ab = A_.dm(A, B)
    cid = dm_call(A, B, ab)
    f0, l0 = free_of(A.uid, B.uid), len(HD.logs(ab, cid))
    orig = CL.invite_local

    def boom(*a, **kw):
        raise RuntimeError("Gegenprobe: Klingeln scheitert")
    CL.invite_local = boom
    try:
        try:
            asyncio.run(CM.move(A.uid, cid, ["ca-gc"]))
            raised = False
        except RuntimeError:
            raised = True
    finally:
        CL.invite_local = orig
    r = crow(cid)
    ok(f"Fehler nach Chat/Mitglieder/Umhaengen/Eintrag: alles zurueck ({raised}, {r}, {free_of(A.uid, B.uid)})",
       raised and r["chat_id"] == ab and r["callee"] == B.uid and r["state"] == "active" and free_of(A.uid, B.uid) == f0
       and len(HD.logs(ab, cid)) == l0)
    with connect() as c:
        orphan = c.execute("SELECT COUNT(*) FROM chat_member m JOIN chat ch ON ch.id=m.chat_id WHERE ch.kind='free' AND m.uid=?",
                           (Cc.uid,)).fetchone()[0]
    ok(f"... keine Mitgliedschaft der Neuen irgendwo ({orphan})", orphan == 0)
    k = req("GET", f"/api/calls/{cid}", A.k)[1].get("call") or {}
    ok(f"... Anruf-Objekt weiter 1:1 mit can_move ({k.get('dm')}, {k.get('can_move')})", k.get("dm") is True and k.get("can_move") is True)

    # --- 2. Die Neue: kein call.move, kein Klartext-Name; danach Umzug normal ---------------------------------------
    sc = A_.Stream(Cc.k)
    time.sleep(0.6)
    mc = sc.mark()
    s, d = add(A, cid, ["ca-gc"])
    nid = (d.get("moved") or {}).get("chat_id")
    ok(f"danach Umzug normal ({s}, {d.get('moved')})", s == 200 and nid and crow(cid)["chat_id"] == nid)
    ok("... Neue bekommt call.invite", sc.wait("call.invite", lambda x: x.get("call_id") == cid, mc))
    ok("... aber kein call.move (kein Verweis auf das 1:1)", sc.none("call.move", mc))
    with connect() as c:
        gn = c.execute("SELECT group_name, dm_key FROM chat WHERE id=?", (nid,)).fetchone()
    ok(f"... neuer Chat ohne Klartext-Name/dm_key ({gn})", gn == (None, None))
    kc = req("GET", f"/api/calls/{cid}", Cc.k)[1].get("call") or {}
    ok(f"... Anruf-Objekt der Neuen ohne from_chat/moved/dm ({sorted(x for x in kc if 'move' in x or x in ('dm', 'from_chat'))})",
       kc and not any(x in kc for x in ("moved", "from_chat", "dm", "can_move", "move_block")) and str(ab) not in str(kc.get("chat_id")))
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})
    req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup"})

    # --- 3. beide gleichzeitig (verschiedene Neue): genau ein Umzug ------------------------------------------------
    ef = A_.dm(E, F)
    cid2 = dm_call(E, F, ef)
    f0 = free_of(E.uid, F.uid)
    (s1, d1), (s2, d2) = both(lambda: add(E, cid2, ["ca-gc"]), lambda: add(F, cid2, ["ca-gd"]))
    new = [x for x in free_of(E.uid, F.uid) if x not in f0]
    mv = [d for d in (d1, d2) if d.get("moved")]
    r = crow(cid2)
    ok(f"gleichzeitig: beide 200, genau ein neuer Chat, ein moved ({s1}, {s2}, {new}, {len(mv)})",
       s1 == 200 and s2 == 200 and len(new) == 1 and len(mv) == 1 and r["chat_id"] == new[0] and r["callee"] == "")
    m = members(new[0]) if new else {}
    ok(f"... beide Neuen Mitglied, beide Personen Admin ({m})",
       m == {E.uid: "admin", F.uid: "admin", Cc.uid: "member", D.uid: "member"})
    lg = HD.logs(ef, cid2)
    ok(f"... genau ein Eintrag moved im alten 1:1 ({lg})", len(lg) == 1 and lg[0].get("moved") == new[0])
    req("POST", f"/api/calls/{cid2}/end", E.k, {"reason": "hangup"})
    req("POST", f"/api/calls/{cid2}/end", F.k, {"reason": "hangup"})

    # --- 4. Doppelklick derselben Person (dieselbe Neue) ----------------------------------------------------------
    gh = A_.dm(G, H)
    cid3 = dm_call(G, H, gh)
    f0 = free_of(G.uid, H.uid)
    (s1, d1), (s2, d2) = both(lambda: add(G, cid3, ["ca-gc"]), lambda: add(G, cid3, ["ca-gc"]))
    new = [x for x in free_of(G.uid, H.uid) if x not in f0]
    ok(f"Doppelklick: ein Chat, ein moved, zweite Antwort ohne Fehler ({s1}, {s2}, {new}, "
       f"{[bool(d.get('moved')) for d in (d1, d2)]})",
       len(new) == 1 and sorted(bool(d.get("moved")) for d in (d1, d2)) == [False, True] and {s1, s2} <= {200, 409, 429}
       and 200 in (s1, s2))
    req("POST", f"/api/calls/{cid3}/end", G.k, {"reason": "hangup"})
    req("POST", f"/api/calls/{cid3}/end", H.k, {"reason": "hangup"})

    # --- 5. Auflegen gleichzeitig mit dem Umzug: nie halb ----------------------------------------------------------
    ij = A_.dm(I_, J)
    cid4 = dm_call(I_, J, ij)
    f0 = free_of(I_.uid, J.uid)
    (s1, d1), (s2, d2) = both(lambda: add(I_, cid4, ["ca-gd"]), lambda: req("POST", f"/api/calls/{cid4}/end", J.k, {"reason": "hangup"}))
    time.sleep(0.5)
    new = [x for x in free_of(I_.uid, J.uid) if x not in f0]
    r = crow(cid4)
    moved = bool(d1.get("moved"))
    if moved:
        good = len(new) == 1 and r["chat_id"] == new[0] and r["callee"] == "" and members(new[0]).get(D.uid) == "member"
    else:
        good = not new and r["chat_id"] == ij and r["callee"] == J.uid and r["state"] == "ended" and s1 == 409
    ok(f"Auflegen gleichzeitig: {'umgezogen' if moved else 'beendet'} - stimmig ({s1}, {s2}, {r}, {new})", good)
    req("POST", f"/api/calls/{cid4}/end", I_.k, {"reason": "hangup"})


if __name__ == "__main__":
    main()
