"""Anrufe 2.5, L9 "1:1 -> Konferenz" (saga/call_move.py, POST /api/calls/{id}/add im 1:1) - Server-Seite mit PyNaCl als Client.

Ohne echten Knoten (wie tests/anrufe_horn_dm.py: gekoppelter Knoten als Registereintrag) - geprueft wird die Steuerung in
Saga, nicht die Medien (die misst tests/browser_anruf_umzug.js).
Geprueft: Feature call_move; Anruf-Objekt can_move bzw. move_block/move_hint (klingelt noch: ringing, direkt: direct);
Fehler ohne jede Aenderung (kein Chat angelegt, Anruf bleibt 1:1): klingelt noch -> 409 ringing, unbekannt/gesperrt ->
404 mit failed, Person eines anderen Servers -> 409 fed (failed kind fed), nur wer schon dabei ist -> 409 already, voll
-> 409 full, Rate (Topf von /invite und /add) -> 429; Aussenstehende sehen den Anruf nicht (404). Umzug durch die
angerufene Person (E5): 200 mit moved {from_chat, chat_id} und call; neuer freier Chat mit beiden (Admin) + Neuen
(Mitglied); Anruf umgehaengt (chat_id, callee leer, kind group, topo horn, Beginn unveraendert, Raum am Knoten
bleibt, keine Beine neu); call.move an beide (alle Geraete, auch Zweitgeraet), call.roster mit dem neuen Chat ohne dm;
die Neue klingelt (call.invite Gruppe, Chatname mit den Namen, Push call.group ohne dm), sieht den Anruf und tritt bei;
altes 1:1: Eintrag "ended" mit moved und Dauer, kein laufender Anruf mehr; neuer Chat: laufender Anruf. Danach weiter
wie L8 im neuen Chat (Bert ist Admin, holt Dora - kein zweiter Umzug). Besetzt: die Neue telefoniert gerade (direkt) -
klingelt trotzdem wie bei L8 (Gruppenanruf), die beiden gelten weiter als besetzt. Ende: Eintrag im neuen Chat (group,
n=3, Dauer ab dem 1:1-Beginn). Stufe 3 (chat_v3): neuer Chat auch v3, Person ohne Handle-Zertifikat -> failed old;
Chats v1: neuer Chat v1. Mesh-Raum (1:1 nach Horn-Fix-Rueckfall topo mesh) zieht ebenso um. Direkter 1:1 (kind dm) ->
409 move_block direct mit Hinweis. Foederiertes 1:1 -> block fed (Hinweis). Keine Anmeldenamen anderer in Texten.
Wegwerf-Konten ca-u*; raeumt auf."""
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import anrufe_horn_dm as HD  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

ok, req, res = A_.ok, A_.req, A_.res
CAPS = HD.CAPS
USERS = (("ca-ua", "Anna Umzug"), ("ca-ub", "Bert Umzug"), ("ca-uc", "Cora Neu"), ("ca-ud", "Dora Neu"), ("ca-ue", "Emil Sperre"),
         ("ca-uf", "Frieda Fern"), ("ca-ug", "Gabi Gruen"), ("ca-uh", "Hugo Haupt"), ("ca-ui", "Ida Alt"), ("ca-uj", "Jan Eins"),
         ("ca-uk", "Kai Zwei"), ("ca-ur", "Rita Rate"), ("ca-us", "Sina Satt"))
HD.NODE = "um-knoten.example.org"


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
        with connect() as c, c:
            c.execute("DELETE FROM fed_chat_block WHERE uid LIKE 'ca-u%'")
        HD.node(False)
        A_.cleanup()
    print(f"\nANRUFE-UMZUG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def get(p, cid):
    s, d = req("GET", f"/api/calls/{cid}", p.k)
    return d.get("call") or {}


def add(p, cid, uids, **kw):
    return req("POST", f"/api/calls/{cid}/add", p.k, dict({"uids": uids}, **kw))


def crow(cid):
    with connect() as c:
        r = c.execute("SELECT chat_id, callee, kind, topo, state, answered, caller FROM chat_call WHERE id=?", (cid,)).fetchone()
    return dict(zip(("chat_id", "callee", "kind", "topo", "state", "answered", "caller"), r)) if r else None


def chat_row(cid):
    with connect() as c:
        r = c.execute("SELECT kind, v, created_by FROM chat WHERE id=?", (cid,)).fetchone()
        m = {u: role for u, role in c.execute("SELECT uid, role FROM chat_member WHERE chat_id=? AND left_at IS NULL", (cid,)).fetchall()}
    return (dict(zip(("kind", "v", "created_by"), r)), m) if r else (None, {})


def free_chats(uid):
    with connect() as c:
        return c.execute("SELECT COUNT(*) FROM chat WHERE kind='free' AND created_by=?", (uid,)).fetchone()[0]


def leg_devs(cid):
    with connect() as c:
        return sorted((r[0], r[1]) for r in c.execute("SELECT device, joined FROM chat_call_leg WHERE call_id=? AND state='active'", (cid,)))


def room(cid):
    from saga import horn
    with connect() as c:
        return horn.call_node(c, cid)


def no_login_names(*texts):
    return not any(u in (x or "") for x in texts for u, _ in USERS)


def hjoin(p, cid, dev=None):
    HD.fresh()
    return req("POST", f"/api/calls/{cid}/join", p.k, {"device": (dev or p.dev)["id"], "role": "member", "caps": CAPS})


def dm_call(a, b, chat, media="audio"):
    """1:1 ueber den Knoten, angenommen -> call id."""
    cid, s, d = HD.hcall(a, chat, media=media)
    assert s == 200, (s, d)
    s, d = hjoin(b, cid)
    assert s == 200, (s, d)
    return cid


def run():
    from saga import calls as CL, call_move as CM
    from saga import fed_calls, fed_chat                 # setzt CL.FED wie im Server
    assert CL.FED is fed_calls and fed_chat
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, H, I_, J, K, Rr, S = (P[u] for u, _ in USERS)
    A2 = A.add_device()
    for u, p in P.items():
        for dv in p.devs:
            HD.caps(p, CAPS, dev=dv)
    HD.setting(horn_all=True, horn_all_y=True)
    HD.node(True)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"Feature call_move in /api/calls/config ({s})", s == 200 and CM.FEATURE == "call_move" and "call_move" in cf.get("features", []))

    ab = A_.dm(A, B)
    v_ab = chat_row(ab)[0]["v"]
    sa, sb, sc = A_.Stream(A.k), A_.Stream(B.k), A_.Stream(Cc.k)
    time.sleep(0.8)

    # --- klingelt noch: kein Umzug ---------------------------------------------------------------------------------
    cid, s, d = HD.hcall(A, ab)
    k = d.get("call") or {}
    ok(f"1:1 ueber Knoten klingelt: can_move false, move_block ringing mit Text ({s}, {k.get('can_move')}, {k.get('move_block')})",
       s == 200 and k.get("dm") is True and k.get("can_move") is False and k.get("move_block") == "ringing" and k.get("move_hint"))
    n0 = free_chats(A.uid) + free_chats(B.uid)
    s, d = add(A, cid, ["ca-uc"])
    ok(f"... /add beim Klingeln: 409 ringing, nichts geaendert ({s}, {d.get('move_block')})",
       s == 409 and d.get("move_block") == "ringing" and d.get("dm") is True and crow(cid)["chat_id"] == ab and free_chats(A.uid) + free_chats(B.uid) == n0)
    s, d = hjoin(B, cid)
    ok(f"Bert nimmt an ({s})", s == 200 and d["call"]["state"] == "active")
    s, d = hjoin(A, cid, dev=A2)
    ok(f"Annas Zweitgeraet kommt als Begleitgeraet dazu ({s}, {[x['role'] for x in (d.get('call') or {}).get('legs') or []]})",
       s == 200 and sorted(x["role"] for x in d["call"]["legs"]) == ["companion", "member", "member"])
    k = get(B, cid)
    ok(f"angenommen: can_move fuer beide ({k.get('can_move')}, {get(A, cid).get('can_move')})", k.get("can_move") is True and get(A, cid).get("can_move") is True)

    # --- Fehler ohne Aenderung -------------------------------------------------------------------------------------
    s, d = add(Cc, cid, ["ca-ud"])
    ok(f"Aussenstehende: 404 ({s})", s == 404)
    with connect() as c, c:                               # legt den Handle an (Commit)
        hA = fed_chat.my_handle(c, A.uid)
    s, bd = req("POST", "/api/chat/blocks", E.k, {"target": hA})
    s1, d1 = add(A, cid, ["ca-ue"])
    s2, d2 = add(A, cid, ["ca-unbekannt-xyz"])
    ok(f"gesperrt bzw. unbekannt: 404 mit failed und Text ({s}, {s1}, {s2}, {d1.get('error')})",
       s1 == 404 and s2 == 404 and (d1.get("failed") or [{}])[0].get("kind") == "unreachable" and d1.get("error") and no_login_names(d1.get("error")))
    s, d = add(A, cid, ["~abcdefghijklmnop@b.example.org"])
    ok(f"Person eines anderen Servers: 409 fed, failed kind fed ({s}, {d.get('error')})",
       s == 409 and d.get("fed") is True and (d.get("failed") or [{}])[0].get("kind") == "fed")
    s, d = add(A, cid, ["ca-ub", "ca-ua"])
    ok(f"nur wer schon dabei ist: 409 already ({s})", s == 409 and d.get("already") is True)
    ok("... nach allen Fehlern: Anruf weiter 1:1 im Direkt-Chat, kein neuer Chat",
       crow(cid)["chat_id"] == ab and crow(cid)["callee"] == B.uid and free_chats(A.uid) + free_chats(B.uid) == n0)

    # --- Umzug durch die angerufene Person ---------------------------------------------------------------------------
    before = crow(cid)
    legs0, node0 = leg_devs(cid), room(cid)
    ma, mb, mc = sa.mark(), sb.mark(), sc.mark()
    t0 = time.time()
    s, d = add(B, cid, ["ca-uc"])
    dt = time.time() - t0
    mv = d.get("moved") or {}
    nid = mv.get("chat_id")
    k = d.get("call") or {}
    ok(f"Bert holt Cora: 200, moved, added, n=1, Cora klingelt ({s}, {mv}, {d.get('added')}, n={d.get('n')}, {dt * 1000:.0f} ms)",
       s == 200 and mv.get("from_chat") == ab and isinstance(nid, int) and nid != ab and d.get("added") == ["ca-uc"] and d.get("n") == 1
       and {x["state"] for x in d.get("invites") or [] if x.get("name") == "Cora Neu"} == {"ringing"})
    ok(f"... Anruf-Objekt danach: Gruppe ohne dm, neuer Chat, can_add (Bert Admin) ({k.get('chat_id')}, {k.get('dm')}, {k.get('can_add')})",
       k.get("chat_id") == nid and k.get("kind") == "group" and "dm" not in k and k.get("can_add") is True and "can_move" not in k)
    r = crow(cid)
    ok(f"... Anruf umgehaengt: chat_id, callee leer, topo horn, Beginn und Gastgeber gleich ({r})",
       r["chat_id"] == nid and r["callee"] == "" and r["kind"] == "group" and r["topo"] == "horn" and r["answered"] == before["answered"]
       and r["caller"] == A.uid and r["state"] == "active")
    ok(f"... Raum am Knoten und Beine unveraendert (keine Neuverbindung) ({node0 is not None})", room(cid) == node0 and node0 and leg_devs(cid) == legs0)
    cr, mem = chat_row(nid)
    ok(f"... neuer freier Chat: beide Admin, Cora Mitglied, Stufe wie das 1:1 ({cr}, {mem})",
       cr["kind"] == "free" and cr["v"] == v_ab and mem == {A.uid: "admin", B.uid: "admin", Cc.uid: "member"} and cr["created_by"] == B.uid)
    ev_a = sa.wait("call.move", lambda x: x.get("call_id") == cid, ma)
    ev_b = sb.wait("call.move", lambda x: x.get("call_id") == cid, mb)
    ok(f"call.move an beide ({bool(ev_a)}, {bool(ev_b)}, {ev_a})",
       ev_a and ev_b and ev_a["from_chat"] == ab and ev_a["chat_id"] == nid and ev_a.get("kind") == "group" and ev_a.get("by"))
    ro = sa.wait("call.roster", lambda x: x.get("call_id") == cid and x.get("chat_id") == nid, ma)
    ok(f"call.roster mit neuem Chat ohne dm, Liste unveraendert ({ro and ro.get('chat_id')}, {ro and ro.get('dm')})",
       ro and "dm" not in ro and len(ro.get("legs") or []) == 3)
    inv = sc.wait("call.invite", lambda x: x.get("call_id") == cid, mc)
    chg = sc.wait("chat.changed", lambda x: x.get("chat_id") == nid, mc)
    # Name einer freien Gruppe ist verschluesselt (chat None wie bei jedem Gruppenanruf in einer freien Gruppe) - der
    # Client zeigt die anrufende Person und "Gruppenanruf"
    ok(f"Cora: chat.changed + call.invite (Gruppe, kein dm, laut, von Anna) ({inv and inv.get('from')})",
       chg and inv and inv.get("kind") == "group" and "dm" not in inv and inv.get("silent") is False
       and (inv.get("from") or {}).get("name") == "Anna Umzug" and (inv.get("from") or {}).get("chat") is None)
    from saga import push
    with connect() as c:
        pm = push.message(c, Cc.uid, "call.group", actor=A.uid, data={"chat": nid, "call": cid})
    ok(f"Push an Cora: call.group ohne dm ({pm and pm.get('body')})", pm and pm.get("group") is True and not pm.get("dm") and pm["call"] == cid)
    lg = HD.logs(ab, cid)
    ok(f"altes 1:1: Eintrag ended mit moved und Dauer, ohne group ({lg})",
       len(lg) == 1 and lg[0]["status"] == "ended" and lg[0].get("moved") == nid and lg[0]["dur"] is not None and "group" not in lg[0])
    s, ch_old = req("GET", f"/api/chats/{ab}", A.k)
    s2, ch_new = req("GET", f"/api/chats/{nid}", Cc.k)
    ok(f"laufender Anruf: im 1:1 keiner mehr, im neuen Chat (auch fuer Cora) ({(ch_old.get('chat') or {}).get('call')}, "
       f"{((ch_new.get('chat') or {}).get('call') or {}).get('running')})",
       ((ch_old.get("chat") or {}).get("call") or {}).get("running") is None
       and (((ch_new.get("chat") or {}).get("call") or {}).get("running") or {}).get("id") == cid)
    kc = get(Cc, cid)
    ok(f"Cora sieht den Anruf: joinable, invite ringing ({kc.get('joinable')}, {kc.get('invite')})", kc.get("joinable") is True and kc.get("invite") == "ringing")
    s, d = hjoin(Cc, cid)
    ok(f"Cora tritt bei (Knoten, ohne Angebote): 3 Personen, 4 Geraete ({s}, {len((d.get('call') or {}).get('legs') or [])})",
       s == 200 and len(d["call"]["legs"]) == 4 and len({x["who"] for x in d["call"]["legs"]}) == 3)
    s, act = req("GET", "/api/calls/active", A.k)
    x = next((y for y in act.get("calls", []) if y["id"] == cid), {})
    ok(f"Anrufliste Anna: Gruppe im neuen Chat, ohne dm ({x.get('chat_id')}, {x.get('dm')})", x.get("chat_id") == nid and not x.get("dm"))

    # --- Danach wie L8 im neuen Chat; zweiter Umzug gibt es nicht ------------------------------------------------------
    sd = A_.Stream(D.k)
    time.sleep(0.5)
    md = sd.mark()
    s, d = add(B, cid, ["ca-ud"])
    ok(f"Bert (Admin im neuen Chat) holt Dora: wie L8, kein moved ({s}, {d.get('added')}, {d.get('moved')})",
       s == 200 and d.get("added") == ["ca-ud"] and "moved" not in d and crow(cid)["chat_id"] == nid and chat_row(nid)[1].get(D.uid) == "member")
    ok("... Dora klingelt", sd.wait("call.invite", lambda x: x.get("call_id") == cid, md))
    # Besetzt: die beiden gelten weiter als besetzt (Gruppenanruf mit eigenem Geraet)
    hb = A_.dm(H, B)
    cx, s, d = A_.call(H, B, hb)
    ok(f"direkter Anruf an Bert waehrend der Konferenz: besetzt ({s}, {d.get('busy')})", s == 409 and d.get("busy") is True)

    # --- Ende: Eintrag im neuen Chat --------------------------------------------------------------------------------
    req("POST", f"/api/calls/{cid}/leave", A.k, {"device": A2["id"]})
    for p in (Cc, B):
        req("POST", f"/api/calls/{cid}/leave", p.k, {"device": p.dev["id"]})
    r = crow(cid)
    lg = [x for x in A_.call_rows(nid) if x.get("call") == cid]
    ok(f"Ende (nur noch Anna): Eintrag im neuen Chat group, n=3, Dauer ab Beginn des 1:1 ({r['state']}, {lg})",
       r["state"] == "ended" and len(lg) == 1 and lg[0]["status"] == "ended" and lg[0].get("group") is True and lg[0].get("n") == 3
       and lg[0]["dur"] >= int(time.time() - before["answered"]) - 2)

    # --- Neue telefoniert gerade: klingelt trotzdem (Gruppenanruf, wie L8) ----------------------------------------------
    jk, gk = A_.dm(J, K), A_.dm(G, F)
    cid2 = dm_call(J, K, jk)
    cx = dm_call(G, F, gk)
    sf = A_.Stream(F.k)
    time.sleep(0.5)
    mf = sf.mark()
    s, d = add(J, cid2, ["ca-uf"])
    ok(f"Neue telefoniert gerade (1:1 ueber Knoten): Umzug geht, sie klingelt (Leiste) ({s}, n={d.get('n')})",
       s == 200 and d.get("moved") and sf.wait("call.invite", lambda x: x.get("call_id") == cid2, mf))
    for cc, p in ((cid2, J), (cx, G)):
        req("POST", f"/api/calls/{cc}/end", p.k, {"reason": "hangup"})

    # --- voll (Grenze ueber Knoten auf 2 gesenkt) ------------------------------------------------------------------
    us = A_.dm(S, Rr)
    HD.setting(horn_all=True, horn_all_y=True, horn_video=2)
    cid3 = dm_call(S, Rr, us, media="video")
    n0 = free_chats(S.uid)
    s, d = add(S, cid3, ["ca-uh"])
    ok(f"voll (Video, Grenze 2): 409 full mit seats und Text, nichts geaendert ({s}, {d.get('seats')})",
       s == 409 and d.get("full") is True and (d.get("seats") or {}).get("limit") == 2 and crow(cid3)["chat_id"] == us
       and free_chats(S.uid) == n0 and "voll" in (d.get("error") or ""))
    req("POST", f"/api/calls/{cid3}/end", S.k, {"reason": "hangup"})
    HD.setting(horn_all=True, horn_all_y=True)

    # --- Rate (Topf von /invite und /add) --------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", Rr.k, {"kind": "free", "members": ["ca-us"]})
    rid, g2 = d["chat"]["id"], secrets.token_hex(16)
    HD.fresh()
    req("POST", "/api/calls", Rr.k, {"id": g2, "chat_id": rid, "media": "audio", "device": Rr.dev["id"], "caps": CAPS})
    codes = [req("POST", f"/api/calls/{g2}/invite", Rr.k, {"uids": ["ca-us"]})[0] for _ in range(CL.CALL_RATE)]
    req("POST", f"/api/calls/{g2}/end", Rr.k, {"device": Rr.dev["id"]})
    cid4 = dm_call(Rr, S, us)                         # Paar Rr|S (oben S|Rr) - eigene Paar-Rate
    s, d = add(Rr, cid4, ["ca-uh"])
    ok(f"Rate: nach {CL.CALL_RATE} /invite 429 mit Text, nichts geaendert ({codes}, {s})",
       s == 429 and "warten" in (d.get("error") or "") and crow(cid4)["chat_id"] == us)
    req("POST", f"/api/calls/{cid4}/end", Rr.k, {"reason": "hangup"})

    # --- Stufe 3: Person ohne Handle-Zertifikat; Chats v1 ------------------------------------------------------------
    gh = A_.dm(G, H)
    if chat_row(gh)[0]["v"] == 3:
        cid5 = dm_call(G, H, gh)
        with connect() as c, c:
            keep_cert = c.execute("SELECT * FROM chat_fed_cert WHERE uid=?", (I_.uid,)).fetchall()
            c.execute("DELETE FROM chat_fed_cert WHERE uid=?", (I_.uid,))
        s, d = add(G, cid5, ["ca-ui"])
        ok(f"Stufe 3: Person ohne Handle-Zertifikat -> 409 failed old mit Name, nichts geaendert ({s}, {d.get('error')})",
           s == 409 and (d.get("failed") or [{}])[0].get("kind") == "old" and "Ida Alt" in (d.get("error") or "") and crow(cid5)["chat_id"] == gh)
        s, d = add(G, cid5, ["ca-ui", "ca-uk"])
        nid5 = (d.get("moved") or {}).get("chat_id")
        ok(f"... gemischt: Umzug mit Kai, Ida in failed; neuer Chat Stufe 3 ({s}, {chat_row(nid5)[0] if nid5 else None})",
           s == 200 and nid5 and chat_row(nid5)[0]["v"] == 3 and [f["kind"] for f in d.get("failed") or []] == ["old"]
           and d.get("added") == ["ca-uk"])
        req("POST", f"/api/calls/{cid5}/end", G.k, {"reason": "hangup"})
        with connect() as c, c:
            for row_ in keep_cert:
                c.execute(f"INSERT INTO chat_fed_cert VALUES ({','.join('?' * len(row_))}) ON CONFLICT DO NOTHING", tuple(row_))
    else:
        ok(f"Stufe 3 nicht aktiv (v={chat_row(gh)[0]['v']}) - Fall uebersprungen", True)
    auth.save_setting("chat_v3", False)
    auth._settings_cache["v"] = None
    time.sleep(5.5)
    ij = A_.dm(I_, J)
    ok(f"Chats v1: Direkt-Chat v1 ({chat_row(ij)[0]['v']})", chat_row(ij)[0]["v"] == 1)
    cid6 = dm_call(I_, J, ij)
    s, d = add(J, cid6, ["ca-ua"])
    nid6 = (d.get("moved") or {}).get("chat_id")
    ok(f"... Umzug: neuer Chat ebenfalls v1 (Format des Anrufs bleibt) ({s}, {chat_row(nid6)[0] if nid6 else None})",
       s == 200 and nid6 and chat_row(nid6)[0]["v"] == 1 and "v" not in d["call"])

    # --- Mesh-Raum (1:1 nach Rueckfall auf direkt, topo mesh) zieht ebenso um ------------------------------------------
    req("POST", f"/api/calls/{cid6}/end", I_.k, {"reason": "hangup"})
    time.sleep(61)                                    # PAIR_RATE I/J
    cid7 = dm_call(I_, J, ij)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET topo='mesh' WHERE id=?", (cid7,))
    s, d = add(I_, cid7, ["ca-ub"])
    ok(f"Mesh-Raum (topo mesh, callee gesetzt): Umzug wie ueber Knoten ({s}, {(d.get('call') or {}).get('topo')})",
       s == 200 and d.get("moved") and d["call"]["topo"] == "mesh" and crow(cid7)["callee"] == "")
    req("POST", f"/api/calls/{cid7}/end", I_.k, {"reason": "hangup"})

    # --- direkter 1:1 (kind dm): Hinweis -------------------------------------------------------------------------------
    ek = A_.dm(E, K)
    cd, s, d = A_.call(E, K, ek)
    k = d.get("call") or {}
    ok(f"direkter 1:1: can_move false, move_block direct mit Hinweis ({k.get('kind')}, {k.get('move_block')})",
       s == 200 and k.get("kind") == "dm" and k.get("can_move") is False and k.get("move_block") == "direct" and "Knoten" in (k.get("move_hint") or ""))
    s, d = add(E, cd, ["ca-ua"])
    ok(f"... /add: 409 dm, move_block direct, nichts angelegt ({s}, {d.get('error')})",
       s == 409 and d.get("dm") is True and d.get("move_block") == "direct" and free_chats(E.uid) == 0)
    req("POST", f"/api/calls/{cd}/end", E.k, {"reason": "hangup"})

    # --- Foederiertes 1:1: Hinweis (Regel; ohne zweiten Server) -------------------------------------------------------
    with connect() as c:
        b1 = CM.block(c, {"state": "active", "kind": "dm", "peer_id": 5})
        b2 = CM.block(c, {"state": "active", "kind": "group", "lead": 5, "peer_id": 5, "chat_id": ab, "callee": "@~x@b.example"})
    ok(f"foederiertes 1:1 (direkt bzw. ueber Knoten): move_block fed mit Hinweis ({b1}, {b2})",
       b1 == "fed" and b2 == "fed" and CM.hint("fed") and CM.hint("fed") != CM.hint("direct"))
    ok("keine Anmeldenamen in den Hinweisen", no_login_names(*[CM.hint(x) for x in ("direct", "fed", "ringing", "ended")]))


if __name__ == "__main__":
    main()
