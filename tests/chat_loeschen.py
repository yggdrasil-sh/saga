"""Chats archivieren und loeschen (2.1): PATCH /api/chats/{cid} {archived}, DELETE /api/chats/{cid}?scope=me|all.

Archiv (zaehlt nicht zu Ungelesen, kommt mit neuer Nachricht zurueck, stumm + archiviert bleibt archiviert), "fuer mich
loeschen" (Server liefert alte Nachrichten/Anhaenge/Anruf-Eintraege nicht mehr aus, die fuer die Person versiegelten
Schluessel sind weg, die anderen behalten alles; Direkt-Chat verschwindet und kommt leer wieder; Name der freien Gruppe
bleibt), "fuer alle loeschen" (Rechte je Chat-Art; Direkt-Chat/freie Gruppe: alles weg inkl. Dateien, Reaktionen als
Nachrichten, KI-Belege, Lesestaende, Anrufe, Benachrichtigungen; Gruppen-/Seiten-Chat: nur der Verlauf), Live-Ereignisse
chat.cleared / chat.removed, Kontoloeschung. Wegwerf-Konten cl-*, Gruppe cl-team; raeumt auf."""
import os, sys, time
import auth
from db import connect
from saga import chat as CH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: Person, req, ck, Stream)

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cl-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cl-%' OR dm_key LIKE 'cl-%' OR created_by LIKE 'cl-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cl-%')")
        for t in ("note", "user_kv", "notification", "chat_identity", "chat_device", "chat_file", "chat_pref"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'cl-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'cl-%' OR target LIKE 'cl-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cl-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cl-%'")


def q1(sql, *a):
    with connect() as c:
        return c.execute(sql, a).fetchone()[0]


def chats(p):
    return {x["id"]: x for x in CT.req("GET", f"/api/chats?device={p.dev['id']}", p.k)[1]["chats"]}


def unread(p):
    return CT.req("GET", "/api/chats/unread", p.k)[1]["unread"]


def texts(p, cid):
    s, d = p.read(cid, calls=1)
    return [(p.open(cid, m)["text"] if m["kind"] == "msg" else "#call") for m in d.get("messages", [])] if s == 200 else None


def my_keys(p, cid):
    return q1("SELECT COUNT(*) FROM chat_msg_key k JOIN chat_msg m ON m.seq=k.msg_seq WHERE m.chat_id=? AND k.recipient IN (?,?)",
              cid, p.dev["id"], "backup:" + p.uid)


def notifs(uid, cid):
    return q1("SELECT COUNT(*) FROM notification WHERE uid=? AND kind IN ('chat.message','call.missed') AND data LIKE ?",
              uid, '{"chat": %d, %%' % cid)


def call_entry(cid, caller, dev):
    """Anruf-Eintrag (kind='call') + beendeter Anruf wie saga/calls.py, direkt in der Datenbank."""
    now = time.time()
    with connect() as c, c:
        cid_ = C.hexid()
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, ended) "
                  "VALUES (?,?,?,?,?,'audio','ended','missed',?,?)", (cid_, cid, caller, "x", dev, now, now))
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, body_ct, sig, size) "
                        "VALUES (?,?,'call',?,?,?,?,?,'',0) RETURNING seq", (C.hexid(), cid, caller, dev, int(now * 1000), now,
                                                                          '{"v":1,"type":"call","status":"missed"}')).fetchone()[0]
        c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, cid))
    return cid_


def upload(p, cid, data=b"Anhang" * 50):
    blob, key = C.encrypt_file(str(cid), data)
    d = CT.req("POST", f"/api/chats/{cid}/files", p.k, raw=blob)[1]
    return {"id": d["id"], "k": C.b64(key), "n": "a.bin", "m": "application/octet-stream", "s": len(data)}


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False)):
        auth.save_setting(k, v)
    for u, n, adm in (("cl-a", "Anna Loesch", True), ("cl-b", "Bert Loesch", False), ("cl-c", "Cora Loesch", False), ("cl-d", "Dora Loesch", False)):
        auth.create_user(u, CT.PW, n, is_admin=adm)
    with connect() as c, c:
        auth.ensure_group(c, "cl-team")
    for u in ("cl-a", "cl-b", "cl-c"):
        auth.set_user_groups(auth.get_user(u)["id"], ["cl-team"])
    time.sleep(CT.WAIT)
    try:
        run()
    finally:
        for x in CT.STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        cleanup()
    print(f"\nCHAT-LOESCHEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    A, B, Cc = CT.Person("cl-a"), CT.Person("cl-b"), CT.Person("cl-c")
    Dk = CT.ck("cl-d")
    sB, sA = CT.Stream(B.k), CT.Stream(A.k)
    time.sleep(0.5)

    # --- Direkt-Chat: Archiv -------------------------------------------------------------------------------------
    s, d = CT.req("POST", "/api/chats", A.k, {"kind": "dm", "user": "cl-b"})
    dm = d["chat"]["id"]
    ok(f"Chatliste: archived/cleared_seq/delete_for_all ({s})", s == 200 and d["chat"]["archived"] is False
       and d["chat"]["cleared_seq"] == 0 and d["chat"]["delete_for_all"] == "chat")
    A.send(dm, "Hallo Bert 1")
    A.send(dm, "Hallo Bert 2")
    f1 = upload(B, dm)
    s, _, bm = B.send(dm, "Mit Anhang", files=[f1])
    ok(f"Nachrichten + Anhang im Direkt-Chat ({s})", s == 200)
    A.send(dm, "Noch eine fuer Bert")
    ok("B hat 1 Ungelesene (nach dem eigenen Senden)", chats(B)[dm]["unread"] == 1 and unread(B) == 1)
    ok("archived: kein bool -> 400", CT.req("PATCH", f"/api/chats/{dm}", B.k, {"archived": "ja"})[0] == 400)
    m0 = sB.mark()
    s, d = CT.req("PATCH", f"/api/chats/{dm}", B.k, {"archived": True})
    ok(f"B archiviert ({s})", s == 200 and d["chat"]["archived"] is True and d["chat"]["archived_at"])
    ok("Live chat.changed an B", sB.wait("chat.changed", lambda x: x.get("chat_id") == dm and x.get("archived") is True, m0) is not None)
    ok("Archivierter Chat: in der Liste (archived), zaehlt nicht zu Ungelesen",
       chats(B)[dm]["archived"] and CT.req("GET", "/api/chats", B.k)[1]["unread"] == 0 and unread(B) == 0)
    ok("A sieht den Chat nicht archiviert", chats(A)[dm]["archived"] is False)
    A.send(dm, "Bist du da?")
    ok("Neue Nachricht holt den Chat aus dem Archiv (Ungelesen zaehlt wieder)", chats(B)[dm]["archived"] is False and unread(B) == 2)
    CT.req("PATCH", f"/api/chats/{dm}", B.k, {"archived": True, "muted": True})
    A.send(dm, "Stumm?")
    ok("Stumm + archiviert bleibt bei neuer Nachricht archiviert", chats(B)[dm]["archived"] is True)
    CT.req("PATCH", f"/api/chats/{dm}", B.k, {"archived": False, "muted": False})
    ok("Aus Archiv holen", chats(B)[dm]["archived"] is False)
    B.send(dm, "Selbst geschrieben")
    call_entry(dm, "cl-a", A.dev["id"])
    ok("B hat Benachrichtigungen fuer den Chat", notifs("cl-b", dm) > 0)

    # --- Rechte -------------------------------------------------------------------------------------------------
    ok("Nicht-Mitglied: loeschen -> 404 (me und all)", CT.req("DELETE", f"/api/chats/{dm}?scope=me", Dk)[0] == 404
       and CT.req("DELETE", f"/api/chats/{dm}?scope=all", Cc.k)[0] == 404)
    ok("Nicht-Mitglied: archivieren -> 404", CT.req("PATCH", f"/api/chats/{dm}", Cc.k, {"archived": True})[0] == 404)
    ok("Unbekannter scope -> 400", CT.req("DELETE", f"/api/chats/{dm}?scope=alle", B.k)[0] == 400)
    ok("Ohne X-Saga -> 401", CT.req("DELETE", f"/api/chats/{dm}", None, hdr={"Cookie": "saga=" + B.k})[0] == 401)

    # --- "Fuer mich loeschen" (Direkt-Chat) -------------------------------------------------------------------------
    before_a = texts(A, dm)
    keys_a = my_keys(A, dm)
    ok(f"vorher: B hat Schluessel ({my_keys(B, dm)}), sieht {len(texts(B, dm))} Eintraege", my_keys(B, dm) >= 6 and len(texts(B, dm)) == 8)
    m0, a0 = sB.mark(), sA.mark()
    s, d = CT.req("DELETE", f"/api/chats/{dm}?scope=me", B.k)
    ok(f"B loescht fuer sich ({s}, {d})", s == 200 and d["scope"] == "me" and d["hidden"] is True and d["cleared_seq"] > 0)
    ok("Live chat.cleared (scope me) an B", sB.wait("chat.cleared", lambda x: x.get("chat_id") == dm and x.get("scope") == "me", m0) is not None)
    time.sleep(0.4)
    ok("... nicht an A", "chat.cleared" not in sA.types(a0) and "chat.removed" not in sA.types(a0))
    ok("Direkt-Chat verschwindet aus Bs Liste", dm not in chats(B))
    ok("B bekommt keine alten Nachrichten/Anrufe mehr (auch ohne Geraet, mit Backup)", texts(B, dm) == []
       and CT.req("GET", f"/api/chats/{dm}/messages?calls=1", B.k)[1]["messages"] == [])
    ok("Bs versiegelte Schluessel (Geraet + backup) sind geloescht", my_keys(B, dm) == 0)
    ok("Bs Benachrichtigungen dieses Chats weg, nichts ungelesen", notifs("cl-b", dm) == 0 and unread(B) == 0)
    ok("A unveraendert: alle Nachrichten, alle Schluessel", texts(A, dm) == before_a and my_keys(A, dm) == keys_a)
    ok("Anhang: fuer B 404, fuer A weiter da", CT.req("GET", f"/api/chat/files/{f1['id']}", B.k)[0] == 404
       and CT.req("GET", f"/api/chat/files/{f1['id']}", A.k)[0] == 200)
    ok("B kann den Chat noch direkt abrufen (cleared_seq gesetzt)", CT.req("GET", f"/api/chats/{dm}", B.k)[1]["chat"]["cleared_seq"] == d["cleared_seq"])
    s, _, _ = A.send(dm, "Neu nach dem Loeschen")
    ok("Neue Nachricht: Direkt-Chat taucht bei B wieder auf - nur mit der neuen Nachricht",
       s == 200 and dm in chats(B) and texts(B, dm) == ["Neu nach dem Loeschen"] and chats(B)[dm]["unread"] == 1)
    CT.req("DELETE", f"/api/chats/{dm}?scope=me", B.k)
    ok("erneut geleert -> weg", dm not in chats(B))
    s, d = CT.req("POST", "/api/chats", B.k, {"kind": "dm", "user": "cl-a"})
    ok("B oeffnet den Direkt-Chat wieder bewusst (neu beginnen) -> in der Liste, leer", s == 200 and d["chat"]["id"] == dm
       and dm in chats(B) and texts(B, dm) == [])

    # --- Freie Gruppe: fuer mich, Rechte, fuer alle ----------------------------------------------------------------
    sC = CT.Stream(Cc.k)
    time.sleep(0.3)
    s, d = CT.req("POST", "/api/chats", A.k, {"kind": "free", "members": ["cl-b", "cl-c"]})
    fg = d["chat"]["id"]
    A.send(fg, "Wanderfreunde", kind="title")
    f2 = upload(A, fg)
    A.send(fg, "Treffpunkt", files=[f2])
    s, _, bmsg = B.send(fg, "Bin dabei")
    ok("delete_for_all: Admin A 'chat', Mitglied B None", chats(A)[fg]["delete_for_all"] == "chat" and chats(B)[fg]["delete_for_all"] is None)
    ok("Normales Mitglied B: fuer alle loeschen -> 403", CT.req("DELETE", f"/api/chats/{fg}?scope=all", B.k)[0] == 403
       and q1("SELECT COUNT(*) FROM chat_msg WHERE chat_id=?", fg) >= 3)
    s, d = CT.req("DELETE", f"/api/chats/{fg}?scope=me", B.k)
    cb = chats(B)[fg]
    ok(f"B leert fuer sich: archiviert, keine alten Nachrichten, Name weiter lesbar ({s})", s == 200 and d["archived"] is True
       and cb["archived"] and texts(B, fg) == [] and cb.get("title_msg") and B.open(fg, cb["title_msg"])["title"] == "Wanderfreunde")
    ok("C sieht weiter alles", texts(Cc, fg) == ["Treffpunkt", "Bin dabei"])
    Cc.send(fg, "Hallo zusammen")
    ok("Neue Nachricht holt die Gruppe fuer B aus dem Archiv", chats(B)[fg]["archived"] is False and texts(B, fg) == ["Hallo zusammen"])
    with connect() as c, c:                                    # KI im Chat erlaubt + Beleg einer KI-Antwort
        c.execute("INSERT INTO chat_ai (chat_id, by_uid, at) VALUES (?,?,?)", (fg, "cl-a", time.time()))
        c.execute("INSERT INTO chat_ai_reply (id, chat_id, uid, created, sha256, msg_id) VALUES (?,?,?,?,?,?)",
                  (C.hexid(), fg, "cl-b", time.time(), "0" * 64, bmsg["id"]))
    CT.req("PATCH", f"/api/chats/{fg}", Cc.k, {"archived": True})
    seqs = [r for r in [q1("SELECT COUNT(*) FROM chat_msg WHERE chat_id=?", fg)]]
    ok(f"vorher: Nachrichten, Datei, KI, Benachrichtigungen ({seqs})", seqs[0] >= 4 and os.path.exists(os.path.join(CH.CHAT_DIR, f2["id"]))
       and notifs("cl-c", fg) > 0)
    b0, c0 = sB.mark(), sC.mark()
    s, d = CT.req("DELETE", f"/api/chats/{fg}?scope=all", A.k)
    ok(f"Admin A loescht die Gruppe fuer alle ({s}, {d})", s == 200 and d["removed"] is True)
    ev = sB.wait("chat.removed", lambda x: x.get("chat_id") == fg, b0)
    ok(f"Live chat.removed an B mit Namen ({ev})", ev and ev["by"]["username"] == "cl-a" and ev["by"]["name"] == "Anna Loesch")
    ok("... und an C", sC.wait("chat.removed", lambda x: x.get("chat_id") == fg, c0) is not None)
    left = {t: q1(f"SELECT COUNT(*) FROM {t} WHERE chat_id=?", fg) for t in ("chat_member", "chat_msg", "chat_file", "chat_ai", "chat_ai_reply",
                                                                             "chat_pref", "chat_call")}
    left["chat"] = q1("SELECT COUNT(*) FROM chat WHERE id=?", fg)
    left["keys"] = q1("SELECT COUNT(*) FROM chat_msg_key k WHERE NOT EXISTS (SELECT 1 FROM chat_msg m WHERE m.seq=k.msg_seq)")
    ok(f"Alles weg: Chat, Mitglieder/Lesestaende, Nachrichten, Schluessel, Dateien, KI, Archiv ({left})", not any(left.values()))
    ok("Blob der Datei geloescht", not os.path.exists(os.path.join(CH.CHAT_DIR, f2["id"])))
    ok("Benachrichtigungen fuer B und C weg", notifs("cl-b", fg) == 0 and notifs("cl-c", fg) == 0)
    ok("Fuer alle: 404 fuer Mitglieder, nicht mehr in den Listen", CT.req("GET", f"/api/chats/{fg}", B.k)[0] == 404
       and fg not in chats(B) and fg not in chats(Cc))

    # --- Direkt-Chat fuer alle (jede der beiden Personen) -------------------------------------------------------------
    callid = call_entry(dm, "cl-a", A.dev["id"])
    f3 = upload(A, dm)
    A.send(dm, "Letzte Nachricht", files=[f3])
    a0 = sA.mark()
    s, d = CT.req("DELETE", f"/api/chats/{dm}?scope=all", B.k)
    ok(f"B loescht den Direkt-Chat fuer beide ({s})", s == 200 and d["removed"] is True)
    ev = sA.wait("chat.removed", lambda x: x.get("chat_id") == dm, a0)
    ok("A bekommt chat.removed von B", ev and ev["by"]["username"] == "cl-b" and ev["kind"] == "dm")
    ok("Direkt-Chat, Anrufe, Anhaenge weg", q1("SELECT COUNT(*) FROM chat WHERE id=?", dm) == 0
       and q1("SELECT COUNT(*) FROM chat_call WHERE id=?", callid) == 0 and q1("SELECT COUNT(*) FROM chat_file WHERE id=?", f3["id"]) == 0
       and not os.path.exists(os.path.join(CH.CHAT_DIR, f3["id"])))
    s, d = CT.req("POST", "/api/chats", A.k, {"kind": "dm", "user": "cl-b"})
    ok("Neuer Direkt-Chat A-B ist leer (neue Kennung)", s == 200 and d["chat"]["id"] != dm and texts(A, d["chat"]["id"]) == [])

    # --- Gruppen-Chat (Saga-Gruppe): fuer alle nur Saga-Admins, nur der Verlauf ----------------------------------------
    s, d = CT.req("POST", "/api/chats", B.k, {"kind": "group", "group": "cl-team"})
    gc = d["chat"]["id"]
    f4 = upload(B, gc)
    B.send(gc, "Team-Info", files=[f4])
    A.send(gc, "Danke")
    ok("delete_for_all: Saga-Admin A 'history', B None", chats(A)[gc]["delete_for_all"] == "history" and chats(B)[gc]["delete_for_all"] is None)
    ok("B (kein Saga-Admin): Verlauf fuer alle -> 403", CT.req("DELETE", f"/api/chats/{gc}?scope=all", B.k)[0] == 403)
    s, d = CT.req("DELETE", f"/api/chats/{gc}?scope=me", Cc.k)
    ok("C leert fuer sich: archiviert, A/B behalten den Verlauf", s == 200 and chats(Cc)[gc]["archived"] and texts(Cc, gc) == []
       and texts(B, gc) == ["Team-Info", "Danke"])
    b0 = sB.mark()
    s, d = CT.req("DELETE", f"/api/chats/{gc}?scope=all", A.k)
    ok(f"Saga-Admin A loescht den Verlauf fuer alle ({s}, {d})", s == 200 and d["removed"] is False and d["deleted"] == 2)
    ev = sB.wait("chat.cleared", lambda x: x.get("chat_id") == gc and x.get("scope") == "all", b0)
    ok("Live chat.cleared (scope all, by A) an B", ev and ev["by"]["username"] == "cl-a")
    ok("Chat bleibt mit allen Mitgliedern, Verlauf + Datei weg", q1("SELECT COUNT(*) FROM chat WHERE id=?", gc) == 1
       and {m["username"] for m in chats(B)[gc]["members"]} == {"cl-a", "cl-b", "cl-c"} and texts(B, gc) == []
       and q1("SELECT COUNT(*) FROM chat_msg WHERE chat_id=?", gc) == 0 and not os.path.exists(os.path.join(CH.CHAT_DIR, f4["id"])))
    ok("cleared_seq fuer alle gesetzt (Hinweis \"Verlauf geloescht\")", chats(B)[gc]["cleared_seq"] > 0)
    s, _, _ = B.send(gc, "Weiter geht's")
    ok("Danach normal weiter: C (archiviert) bekommt die neue Nachricht, Archiv aufgehoben",
       s == 200 and texts(Cc, gc) == ["Weiter geht's"] and chats(Cc)[gc]["archived"] is False)

    # --- Seiten-Chat: fuer alle nur der Eigentuemer der Seite -----------------------------------------------------------
    nid = CT.req("POST", "/api/notes", B.k, {"title": "Loesch-Seite"})[1]["id"]
    CT.req("PUT", f"/api/notes/{nid}/shares", B.k, {"type": "user", "target": "cl-a", "role": "view"})
    s, d = CT.req("POST", "/api/chats", A.k, {"kind": "page", "note_id": nid})
    pc = d["chat"]["id"]
    A.send(pc, "Zur Seite")
    ok("delete_for_all: Eigentuemer B 'history', A (Saga-Admin, nicht Eigentuemer) None",
       chats(B)[pc]["delete_for_all"] == "history" and chats(A)[pc]["delete_for_all"] is None)
    ok("A: Verlauf fuer alle -> 403", CT.req("DELETE", f"/api/chats/{pc}?scope=all", A.k)[0] == 403)
    s, d = CT.req("DELETE", f"/api/chats/{pc}?scope=all", B.k)
    ok(f"Eigentuemer B loescht den Verlauf ({s})", s == 200 and d["removed"] is False and texts(A, pc) == []
       and q1("SELECT COUNT(*) FROM chat WHERE id=?", pc) == 1)

    # --- Verlassen der freien Gruppe weiter moeglich, Kontoloeschung ------------------------------------------------------
    s, d = CT.req("POST", "/api/chats", A.k, {"kind": "free", "members": ["cl-b", "cl-c"]})
    fg2 = d["chat"]["id"]
    A.send(fg2, "Zweite Gruppe")
    CT.req("DELETE", f"/api/chats/{fg2}?scope=me", B.k)
    s, _ = CT.req("DELETE", f"/api/chats/{fg2}/members/cl-b", B.k)
    ok("Freie Gruppe: fuer mich leeren + verlassen", s == 200 and fg2 not in chats(B))
    CT.req("PATCH", f"/api/chats/{fg2}", Cc.k, {"archived": True})
    ok("C hat Archiv-Eintraege", q1("SELECT COUNT(*) FROM chat_pref WHERE uid='cl-c'") > 0)
    from saga.userdata import delete_user_data
    delete_user_data("cl-c")
    ok("Kontoloeschung: chat_pref des Kontos weg", q1("SELECT COUNT(*) FROM chat_pref WHERE uid='cl-c'") == 0)


if __name__ == "__main__":
    main()
