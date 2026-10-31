"""Chat-Standard B (chat_grp) x Chat-Alltag A+C (chat_prefs) - Wechselwirkungen nach dem Zusammenfuehren (2.5).

Geprueft (Chats v1 und Stufe 3):
- perm_send 'admins' + Erwaehnung durch ein Nicht-Admin-Mitglied: 403, keine Mitteilung (auch nicht chat.mention), keine
  mention_seq; Admin erwaehnt -> chat.mention.
- Gesperrtes Konto (block2) erwaehnt mich in einer Gruppe: keine Mitteilung, keine mention_seq-Wiederkehr eines
  ausgeblendeten Chats wird nicht verlangt (nur: keine Mitteilung); andere Mitglieder bekommen ihre Mitteilung.
- Ausgeblendeter Chat (hidden, chat_prefs) + angeheftete Nachrichten (msgpin1): pins/can_pin weiter im Chat-Objekt,
  Anheften durch andere bringt den Chat nicht zurueck und meldet nichts.
- Admin-Grabstein (moddel1) + Medienuebersicht ?has=file: Grabstein (auch mit tombs=1) nicht als Datei.
- chat.report (Admin-Mitteilung) kommt auch an, wenn der gemeldete Chat beim Admin stumm/notify none ist.
- PATCH mit Feldern beider Bereiche zugleich (perm_send + pinned + notify)."""
import json, os, secrets, socket, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402
import chat_gruppen2 as G  # noqa: E402  (Klasse U: Senden v1/Format v2)

res = []
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cw-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cw-%' OR dm_key LIKE 'cw-%' OR created_by LIKE 'cw-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cw-%')")
        c.execute("DELETE FROM chat_report WHERE reporter LIKE 'cw-%' OR reporter IS NULL")
        c.execute("DELETE FROM notification WHERE uid LIKE 'cw-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cw-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cw-%'")


def notifs(uid, cid=None):
    with connect() as c:
        rows = c.execute("SELECT kind, data FROM notification WHERE uid=? ORDER BY id", (uid,)).fetchall()
    out = []
    for k, d in rows:
        try:
            d = json.loads(d or "{}")
        except ValueError:
            d = {}
        if cid is None or d.get("chat") == cid:
            out.append(k)
    return out


def clear(*us):
    with connect() as c, c:
        for u in us:
            c.execute("DELETE FROM notification WHERE uid=?", (u.uid,))


def mention_seq(cid, u):
    with connect() as c:
        r = c.execute("SELECT mention_seq FROM chat_pref WHERE chat_id=? AND uid=?", (cid, u.uid)).fetchone()
    return r[0] if r else 0


def ref_of(ch, u):
    m = next(x for x in ch["members"] if x["name"] == u.name)
    return m.get("handle") or m.get("ref")


def say(u, ch, txt, mentions=None):
    return u.send(ch, {"v": 1, "type": "text", "text": txt}, extra={"mentions": mentions} if mentions is not None else None)


def series(pre, A, B, Cc, Adm, v3):
    pre = f"[{pre}] "
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [A.ref(B), A.ref(Cc)], "device": A.dev["id"]})
    ch = d.get("chat") or {}
    cid = ch.get("id")
    ok(pre + f"freie Gruppe ({s}, v={ch.get('v')})", s == 200 and ch.get("v") == (3 if v3 else None))
    # --- PATCH mit Feldern beider Bereiche ---------------------------------------------------------------------------------
    s, d = A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "admins", "pinned": True, "notify": "mentions"})
    c2 = d.get("chat") or {}
    ok(pre + f"PATCH perm_send + pinned + notify zugleich ({s})", s == 200 and c2.get("perm_send") == "admins" and c2.get("pinned") is True
       and c2.get("notify") == "mentions", {k: c2.get(k) for k in ("perm_send", "pinned", "notify")})
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"perm_send": "all", "pinned": True})
    ok(pre + f"Mitglied: perm_send -> 403, nichts uebernommen (auch nicht pinned) ({s})", s == 403 and B.chat(cid).get("pinned") is False)
    A.api("PATCH", f"/api/chats/{cid}", {"pinned": False, "notify": "all"})
    # --- perm_send admins + Erwaehnung durch Nicht-Admin --------------------------------------------------------------------
    ch = A.chat(cid)
    clear(A, B, Cc)
    s, d, _ = say(B, B.chat(cid), "@Anna @Cora", mentions=[ref_of(ch, A), ref_of(ch, Cc)])
    ok(pre + f"perm_send admins: Nicht-Admin erwaehnt -> 403 ({s})", s == 403, d)
    ok(pre + "abgelehnt: keine Mitteilung, keine mention_seq", notifs(A.uid, cid) == [] and notifs(Cc.uid, cid) == []
       and mention_seq(cid, A) == 0 and mention_seq(cid, Cc) == 0, (notifs(A.uid, cid), notifs(Cc.uid, cid)))
    s, d, _ = say(A, A.chat(cid), "@Cora", mentions=[ref_of(ch, Cc)])
    ok(pre + f"Admin erwaehnt Cora -> chat.mention ({s})", s == 200 and notifs(Cc.uid, cid) == ["chat.mention"] and notifs(B.uid, cid) == ["chat.message"],
       (notifs(Cc.uid, cid), notifs(B.uid, cid)))
    A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "all"})
    # --- gesperrtes Konto erwaehnt mich ---------------------------------------------------------------------------------------
    s, d = Cc.api("POST", "/api/chat/blocks", {"target": Cc.ref(B)})
    ok(pre + f"Cora sperrt Bert ({s})", s == 200, d)
    clear(A, B, Cc)
    ms0 = mention_seq(cid, Cc)
    s, d, _ = say(B, B.chat(cid), "@Cora @Anna", mentions=[ref_of(ch, Cc), ref_of(ch, A)])
    ok(pre + f"Bert (gesperrt) schreibt in der Gruppe ({s})", s == 200, d)
    ok(pre + "Cora: keine Mitteilung vom gesperrten Absender (auch keine Erwaehnung)", notifs(Cc.uid, cid) == [], notifs(Cc.uid, cid))
    ok(pre + "Cora: mention_seq unveraendert", mention_seq(cid, Cc) == ms0)
    ok(pre + "Anna (nicht gesperrt): chat.mention", notifs(A.uid, cid) == ["chat.mention"], notifs(A.uid, cid))
    Cc.api("DELETE", "/api/chat/blocks?target=" + Cc.ref(B))
    clear(Cc)
    say(B, B.chat(cid), "@Cora wieder", mentions=[ref_of(ch, Cc)])
    ok(pre + "entsperrt: Cora bekommt die Erwaehnung wieder", notifs(Cc.uid, cid) == ["chat.mention"], notifs(Cc.uid, cid))
    # --- Admin-Grabstein + Medienuebersicht -------------------------------------------------------------------------------
    fid = Cc.upload(cid)
    s, d, mf = Cc.send(Cc.chat(cid), {"v": 1, "type": "text", "text": "Bild", "files": [{"id": fid}]}, files=[fid])
    fid2 = B.upload(cid)
    s2, _, mf2 = B.send(B.chat(cid), {"v": 1, "type": "text", "text": "Bild 2", "files": [{"id": fid2}]}, files=[fid2])
    has = [x["id"] for x in A.msgs(cid, "&has=file")]
    ok(pre + f"?has=file: beide Anhang-Nachrichten ({s}, {s2})", mf["id"] in has and mf2["id"] in has, has)
    s, d = A.api("DELETE", f"/api/chats/{cid}/messages/{mf['id']}")
    ok(pre + f"Admin loescht Coras Nachricht -> Grabstein ({s})", s == 200 and d.get("by_admin") is True, d)
    for q in ("&has=file", "&has=file&tombs=1"):
        lst = A.msgs(cid, q)
        ok(pre + f"{q}: Grabstein nicht als Datei, andere bleibt", not any(x["id"] == mf["id"] for x in lst) and any(x["id"] == mf2["id"] for x in lst)
           and all(x.get("kind") == "msg" for x in lst), [(x["id"][:6], x.get("kind")) for x in lst])
    ok(pre + "tombs=1 ohne has: Grabstein da", any(x["id"] == mf["id"] and x.get("kind") == "tomb" for x in A.msgs(cid, "&tombs=1")))
    # --- ausgeblendeter Chat + Admin-Pins (Gruppen-Chat einer Benutzergruppe) ------------------------------------------
    nm = "cw-team" + ("3" if v3 else "1")
    with connect() as c, c:
        auth.ensure_group(c, nm)
    for u in (A, B, Adm):
        auth.set_user_groups(auth.get_user(u.uid)["id"], [nm])
    time.sleep(0.3)
    g = A.api("POST", "/api/chats", {"kind": "group", "group": nm, "device": A.dev["id"]})[1]["chat"]
    gid = g["id"]
    s, d, mp = say(A, A.chat(gid), "wichtig")
    s, d = B.api("PATCH", f"/api/chats/{gid}", {"hidden": True})
    ok(pre + f"Bert blendet Gruppen-Chat aus ({s})", s == 200 and (d.get("chat") or {}).get("hidden") is True)
    clear(B)
    s, d = Adm.api("POST", f"/api/chats/{gid}/pins/{mp['id']}")
    ok(pre + f"Saga-Admin heftet an ({s})", s == 200 and mp["id"] in (d.get("pins") or []), d)
    bg = B.chat(gid)
    ok(pre + "ausgeblendet: pins/can_pin weiter im Chat-Objekt, bleibt ausgeblendet", bg.get("hidden") is True and mp["id"] in (bg.get("pins") or [])
       and "can_pin" in bg, {k: bg.get(k) for k in ("hidden", "pins", "can_pin")})
    ok(pre + "ausgeblendet: Anheften meldet nichts, Chat fehlt in /api/chats", notifs(B.uid, gid) == []
       and gid not in [x["id"] for x in B.api("GET", "/api/chats")[1]["chats"]])
    s, d = B.api("POST", f"/api/chats/{gid}/pins/{mp['id']}")
    ok(pre + f"Bert loest/heftet im ausgeblendeten Chat (idempotent) ({s})", s == 200)
    # --- chat.report trotz Stummschaltung beim Admin -----------------------------------------------------------------------
    Adm.api("PATCH", f"/api/chats/{gid}", {"muted_until": -1, "notify": "none"})
    clear(Adm)
    s, d, mr = say(B, B.chat(gid), "Werbung")
    s, d = A.api("POST", "/api/chat/report", {"chat": gid, "msgs": [mr["id"]], "reason": "spam"})
    ok(pre + f"Meldung ({s})", s == 200, d)
    ok(pre + "Admin (Chat stumm, notify none): chat.report kommt trotzdem, keine chat.message", "chat.report" in notifs(Adm.uid)
       and "chat.message" not in notifs(Adm.uid), notifs(Adm.uid))
    from saga import chat_grp, push  # noqa: F401 - chat_grp traegt notify.RENDER["chat.report"] ein (wie im Server)
    with connect() as c:
        nid = c.execute("SELECT id FROM notification WHERE uid=? AND kind='chat.report' ORDER BY id DESC LIMIT 1", (Adm.uid,)).fetchone()
        pm = push.message(c, Adm.uid, "chat.report", ref=nid[0] if nid else None)        # wie die Warteschlange: ref = Benachrichtigung
    ok(pre + "Push-Text fuer chat.report (nicht durch Chat-Stummschaltung unterdrueckt)", bool(pm), (nid, pm))


def run():
    mk = lambda: "cw-" + secrets.token_hex(6)                     # noqa: E731
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    A, B, Cc = G.U(mk(), "Anna Wechsel"), G.U(mk(), "Bert Wechsel"), G.U(mk(), "Cora Wechsel")
    Adm = G.U(mk(), "Saga Wechseladmin", admin=True)
    series("v1", A, B, Cc, Adm, False)
    auth.save_setting("chat_v3", True)
    time.sleep(WAIT)
    E, F_, H = G.U(mk(), "Emil Wechsel"), G.U(mk(), "Fritz Wechsel"), G.U(mk(), "Hans Wechsel")
    Adm3 = G.U(mk(), "Saga Wechseladmin3", admin=True)
    for u in (E, F_, H, Adm3):
        u.certs()
    series("Stufe 3", E, F_, H, Adm3, True)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("search_username", True)):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nCHAT-WECHSELWIRKUNG {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    socket.setdefaulttimeout(60)
    main()
