"""Seiten-Chat: Mitglieder hinzufuegen/entfernen ueber die Seitenfreigaben (2.5, pagechatmembers1).

POST /api/chats/{cid}/members im Seiten-Chat teilt die Seite (Rolle view, {role} waehlbar) - auch die erste Freigabe
einer noch nicht geteilten Seite (Eigentuemer, Chat mit 1 Mitglied); die Person ist danach Mitglied (Live chat.changed,
Mitteilung share.added). Ohne Teilen-Recht 403. DELETE entzieht eine direkte Freigabe; Zugriff ueber eine Gruppe oder eine
Seite darueber -> 409 mit Hinweis (nur fuer den Eigentuemer, Nicht-Eigentuemer 403 ohne Titel); Eigentuemer 409; ich
selbst 400. Felder can_manage/can_rename/can_admin, members[].removable/via. Gruppen-Chat bleibt 400. Chats v1 und
Stufe 3 (Handles). Wegwerf-Konten sm-*, Gruppe sm-team; raeumt auf."""
import json, os, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Stream)
import chat_admins as CA  # noqa: E402  (Konto mit Identitaet/Geraet)

res = []
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'sm-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'sm-%' OR created_by LIKE 'sm-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'sm-%')")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'sm-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'sm-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'sm-%'")


def mem(ch, u):
    return next((m for m in ch.get("members", []) if m.get("name") == u.name), {})


def shares(owner, nid):
    s, d = owner.api("GET", f"/api/notes/{nid}/shares")
    return {x["name"]: x["role"] for x in d.get("shares", [])} if s == 200 else {}


def q(x):
    return urllib.parse.quote(x or "x", safe="")


def series(label, mk, v3):
    pre = f"[{label}] "
    A, B, Cc, D, E, P = (CA.U(mk(), n + " " + label) for n in ("Anna", "Bert", "Cora", "Dora", "Emil", "Paul"))
    if v3:
        for u in (A, B, Cc, D, E, P):
            u.certs()
    grp = "sm-team" + ("3" if v3 else "1")
    with connect() as c, c:
        auth.ensure_group(c, grp)
    for u in (A, E):
        auth.set_user_groups(auth.get_user(u.uid)["id"], [grp])
    # --- Fall des Nutzers: eigene, noch nicht geteilte Seite, Seiten-Chat mit 1 Mitglied ---
    s, d = A.api("POST", "/api/notes", {"title": "sm-Dashboard"})
    nid = d["id"]
    s, d = A.api("POST", "/api/chats", {"kind": "page", "note_id": nid, "device": A.dev["id"]})
    ch = d.get("chat") or {}
    cid = ch.get("id")
    ok(pre + f"Seiten-Chat einer ungeteilten Seite: 1 Mitglied ({s})", s == 200 and ch.get("member_count") == 1, ch.get("member_count"))
    ok(pre + "Eigentuemer: can_manage true, can_rename/can_admin false",
       (ch.get("can_manage"), ch.get("can_rename"), ch.get("can_admin")) == (True, False, False), ch)
    ok(pre + "eigenes Mitglied: via owner, removable false", mem(ch, A).get("via") == "owner" and mem(ch, A).get("removable") is False, mem(ch, A))
    mark = B.ev.mark()
    s, d = A.api("POST", f"/api/chats/{cid}/members", {"user": A.ref(B)})
    ch = d.get("chat") or {}
    ok(pre + f"B hinzufuegen -> 200, 2 Mitglieder ({s})", s == 200 and ch.get("member_count") == 2 and bool(mem(ch, B)), d)
    ok(pre + "B: erste Freigabe der Seite, Rolle view", shares(A, nid) == {B.name: "view"}, shares(A, nid))
    ok(pre + "B: removable true, via share", mem(ch, B).get("removable") is True and mem(ch, B).get("via") == "share", mem(ch, B))
    if v3:
        ok(pre + "Stufe 3: andere Mitglieder nur als Handle", mem(ch, B).get("username") == mem(ch, B).get("handle") and B.uid not in json.dumps(ch))
    ok(pre + "B: Live-Ereignis chat.changed", bool(B.ev.wait("chat.changed", lambda x: (x or {}).get("chat_id") == cid, since=mark)), B.ev.types(mark))
    s, n = B.api("GET", "/api/notifications")
    ok(pre + "B: Mitteilung 'Seite geteilt' (share.added)", any(x.get("kind") == "share.added" for x in n.get("items", [])), n.get("items"))
    cb = B.chat(cid)
    ok(pre + "B sieht den Chat; can_manage false, keine removable-Felder",
       cb.get("can_manage") is False and all("removable" not in m for m in cb.get("members", [])), cb)
    s, d = A.api("POST", f"/api/chats/{cid}/members", {"user": A.ref(B)})
    ok(pre + f"B nochmals -> 409 ({s})", s == 409)
    # Rolle waehlbar; Handle '~x@host' dieses Servers wie {user}
    body = {"handle": Cc.handle, "role": "edit"} if v3 else {"user": A.ref(Cc), "role": "edit"}
    s, d = A.api("POST", f"/api/chats/{cid}/members", body)
    ok(pre + f"C mit role edit ({'Handle' if v3 else 'user'}) -> 200 ({s})", s == 200 and shares(A, nid).get(Cc.name) == "edit", d)
    s, d = A.api("POST", f"/api/chats/{cid}/members", {"user": A.ref(D), "role": "owner"})
    ok(pre + f"role owner -> 400 ({s})", s == 400)
    # ohne Teilen-Recht
    s, d = B.api("POST", f"/api/chats/{cid}/members", {"user": B.ref(D)})
    ok(pre + f"B (nur Lesen) fuegt hinzu -> 403 ({s})", s == 403 and D.name not in shares(A, nid))
    s, d = Cc.api("POST", f"/api/chats/{cid}/members", {"user": Cc.ref(D)})
    ok(pre + f"C (Bearbeiten, nicht Eigentuemer) fuegt hinzu -> 403 ({s})", s == 403)
    s, d = B.api("DELETE", f"/api/chats/{cid}/members/{q(mem(cb, Cc).get('username'))}")
    ok(pre + f"B entfernt C -> 403 ({s})", s == 403 and shares(A, nid).get(Cc.name) == "edit")
    # Zugriff ueber eine Gruppe
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "group", "target": grp, "role": "view"})
    ch = A.chat(cid)
    ok(pre + "E ueber die Gruppe Mitglied: via group, removable false",
       mem(ch, E).get("via") == "group" and mem(ch, E).get("removable") is False, mem(ch, E))
    s, d = A.api("DELETE", f"/api/chats/{cid}/members/{q(mem(ch, E).get('username'))}")
    ok(pre + f"E entfernen -> 409 mit Hinweis auf die Gruppe ({s})", s == 409 and grp in str(d.get("error")), d)
    s, d = A.api("POST", f"/api/chats/{cid}/members", {"user": A.ref(E)})
    ok(pre + f"E (schon ueber Gruppe Mitglied) hinzufuegen -> 409 ({s})", s == 409)
    # Eigentuemer / ich selbst
    s, d = A.api("DELETE", f"/api/chats/{cid}/members/{q(A.uid)}")
    ok(pre + f"Eigentuemer (ich) -> 400 nicht verlassbar ({s})", s == 400)
    s, d = Cc.api("DELETE", f"/api/chats/{cid}/members/{q(mem(Cc.chat(cid), A).get('username'))}")
    ok(pre + f"Eigentuemer entfernen (durch C) -> 403 ({s})", s == 403)
    # entfernen per direkter Freigabe
    mark = B.ev.mark()
    s, d = A.api("DELETE", f"/api/chats/{cid}/members/{q(mem(ch, B).get('username'))}")
    ok(pre + f"B entfernen -> 200, Freigabe entzogen ({s})", s == 200 and B.name not in shares(A, nid), d)
    ch = A.chat(cid)
    ok(pre + "B nicht mehr Mitglied", not mem(ch, B), [m.get("name") for m in ch.get("members", [])])
    s, _ = B.api("GET", f"/api/chats/{cid}")
    ok(pre + f"B: Chat jetzt 404 ({s})", s == 404)
    ok(pre + "B: Live-Ereignis chat.changed beim Entfernen", bool(B.ev.wait("chat.changed", lambda x: (x or {}).get("chat_id") == cid, since=mark)))
    # Zugriff ueber die Seite darueber
    s, d = A.api("POST", "/api/notes", {"title": "sm-Eltern-geheim"})
    par = d["id"]
    s, d = A.api("POST", "/api/notes", {"title": "sm-Kind", "parent_id": par})
    kid = d["id"]
    A.api("PUT", f"/api/notes/{par}/shares", {"type": "user", "target": A.ref(P), "role": "view"})
    A.api("PUT", f"/api/notes/{kid}/shares", {"type": "user", "target": A.ref(D), "role": "view"})
    s, d = A.api("POST", "/api/chats", {"kind": "page", "note_id": kid})
    kc = (d.get("chat") or {})
    kid_cid = kc.get("id")
    ok(pre + "Unterseite: P ueber Eltern via parent, removable false",
       mem(kc, P).get("via") == "parent" and mem(kc, P).get("removable") is False, mem(kc, P))
    s, d = A.api("DELETE", f"/api/chats/{kid_cid}/members/{q(mem(kc, P).get('username'))}")
    ok(pre + f"P entfernen -> 409 mit Hinweis auf die uebergeordnete Seite ({s})", s == 409 and "sm-Eltern-geheim" in str(d.get("error")), d)
    dc = D.chat(kid_cid)
    s, d = D.api("DELETE", f"/api/chats/{kid_cid}/members/{q(mem(dc, P).get('username'))}")
    ok(pre + f"D (nicht Eigentuemer) entfernt P -> 403 ohne Titel der Elternseite ({s})", s == 403 and "Eltern" not in json.dumps(d), d)
    ok(pre + "D sieht keine via/removable", all("via" not in m for m in dc.get("members", [])), dc.get("members"))
    # direkte Freigabe + Gruppe: nicht entfernbar (Zugriff bliebe)
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": A.ref(E), "role": "view"})
    ch = A.chat(cid)
    ok(pre + "E direkt + Gruppe: removable false", mem(ch, E).get("removable") is False and mem(ch, E).get("via") == "share", mem(ch, E))
    s, d = A.api("DELETE", f"/api/chats/{cid}/members/{q(mem(ch, E).get('username'))}")
    ok(pre + f"E (direkt + Gruppe) -> 409 ({s})", s == 409 and grp in str(d.get("error")) and shares(A, nid).get(E.name) == "view", d)
    # Gruppen-Chat unveraendert
    s, d = A.api("POST", "/api/chats", {"kind": "group", "group": grp})
    gid = (d.get("chat") or {}).get("id")
    s, d = A.api("POST", f"/api/chats/{gid}/members", {"user": A.ref(B)})
    ok(pre + f"Gruppen-Chat: hinzufuegen weiter 400 ({s})", s == 400)
    s, d = A.api("PATCH", f"/api/chats/{cid}/members/{q(mem(ch, E).get('username'))}", {"admin": True})
    ok(pre + f"Seiten-Chat: Admin vergeben weiter 400 ({s})", s == 400)


def run():
    mk = lambda: "sm-" + secrets.token_hex(6)                     # noqa: E731
    s, d = CT.req("GET", "/api/version")
    ok("/api/version: Feature pagechatmembers1", s == 200 and "pagechatmembers1" in d.get("features", []), d.get("features"))
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    series("v1", mk, False)
    auth.save_setting("chat_v3", True)
    time.sleep(WAIT)
    series("Stufe 3", mk, True)


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
    print(f"\nSEITENCHAT-MITGLIEDER {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
