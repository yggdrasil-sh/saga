"""Gruppeninfo + Admin-Rollen (2.5, chatadm1) - Server-Seite: PATCH /api/chats/{cid}/members/{who} {admin}.

Geprueft fuer Chats v1 (lokal, Benutzername) und Stufe 3 (saga-chat/v2 mit Handles): Admin vergeben, entziehen, den letzten
Admin nicht entziehen (409, verstaendliche Meldung), sich selbst entziehen, wenn ein anderer Admin bleibt; Nicht-Admin 403;
Gruppen- und Seiten-Chat 400; unbekanntes Mitglied 404; ungueltige Angabe 400; Felder members[].admin, me_admin, can_manage,
can_admin, can_rename; der neue Admin darf verwalten; Live-Ereignis chat.changed an alle Mitglieder; Systemzeile type 'admin'
(Stufe 3) mit Handles und Namen statt Anmeldenamen; Feature chatadm1 in /api/version und in den Foederations-Faehigkeiten.
Wegwerf-Konten ca-*, Gruppe ca-team; raeumt auf."""
import json, os, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Stream)

res = []
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


class U:
    """Konto mit Identitaet und Geraet; lv = Chat-Stufe (X-Saga-Chat). Stufe 3: Zertifikate v2."""

    def __init__(self, uid, name, lv=3):
        self.uid, self.name, self.lv = uid, name, lv
        auth.create_user(uid, None, name)
        self.k = CT.ck(uid)
        self.ident, self.dev = C.identity(), C.device()
        s, d = self.api("PUT", "/api/chat/identity", C.setup_body(uid, self.ident, self.dev, ops=1, mem=8 << 20))
        assert s == 200, (s, d)
        self.ev = CT.Stream(self.k)
        self.handle = None

    def api(self, m, p, b=None):
        return CT.req(m, p, self.k, b, hdr={"X-Saga-Chat": str(self.lv)})

    def certs(self):
        s, me = self.api("GET", "/api/chat/v2/me")
        h = me["handle"]
        body = {"handle_sig": C.b64(C.handle_sig(h, self.ident)), "devices": {self.dev["id"]: C.b64(C.device_sig_v2(h, self.ident, self.dev))}}
        s, me = self.api("PUT", "/api/chat/v2/cert", body)
        ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
        self.api("PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(h, self.ident, ver, me["device_set_want"])})
        self.handle = h

    def ref(self, other):
        s, d = self.api("GET", "/api/chat/people?q=" + urllib.parse.quote(other.name))
        return next(u["username"] for u in d["users"] if u["name"] == other.name)

    def chat(self, cid):
        return self.api("GET", f"/api/chats/{cid}?device={self.dev['id']}")[1].get("chat") or {}

    def sys_lines(self, cid):
        s, d = self.api("GET", f"/api/chats/{cid}/messages?calls=1&device={self.dev['id']}")
        out = []
        for m in (d.get("messages") or []) if s == 200 else []:
            if m.get("kind") == "sys":
                try:
                    out.append(json.loads(m["body_ct"]))
                except ValueError:
                    pass
        return out


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ca-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'ca-%' OR dm_key LIKE 'ca-%' OR created_by LIKE 'ca-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'ca-%')")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'ca-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ca-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'ca-%'")


def mem_of(ch, u):
    return next((m for m in ch.get("members", []) if m.get("me") is False and m.get("name") == u.name) or
                (m for m in ch.get("members", []) if m.get("name") == u.name), {})


def flags(ch):
    return (ch.get("me_admin"), ch.get("can_manage"), ch.get("can_admin"), ch.get("can_rename"))


def series(label, A, B, Cc, D, v3):
    """Eine Reihe fuer freie Gruppen (v1 bzw. Stufe 3)."""
    pre = f"[{label}] "
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [A.ref(B), A.ref(Cc)], "device": A.dev["id"]})
    ch = d.get("chat") or {}
    cid = ch.get("id")
    ok(pre + f"freie Gruppe A (Admin), B, C ({s}, v={ch.get('v')})", s == 200 and (ch.get("v") == (3 if v3 else None)))
    ok(pre + "A: me_admin, can_manage, can_admin, can_rename = true", flags(ch) == (True, True, True, True), flags(ch))
    ok(pre + "members[].admin: nur A", [m["name"] for m in ch["members"] if m.get("admin")] == [A.name], ch["members"])
    cb = B.chat(cid)
    ok(pre + "B: me_admin/can_manage/can_admin false, can_rename true", flags(cb) == (False, False, False, True), flags(cb))
    bm = mem_of(ch, B)
    who_b = bm.get("username")
    if v3:
        ok(pre + "Mitglieder anderer nur als Handle (kein Anmeldename)", who_b == bm.get("handle") and B.uid not in json.dumps(ch), bm)
    # Nicht-Admin
    s, d = B.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(mem_of(cb, Cc)['username'])}", {"admin": True})
    ok(pre + f"B (kein Admin) kann niemanden zum Admin machen -> 403 ({s})", s == 403)
    # ungueltig / unbekannt
    s, _ = A.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(who_b)}", {"admin": "ja"})
    ok(pre + f"admin kein bool -> 400 ({s})", s == 400)
    s, _ = A.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(A.ref(D))}", {"admin": True})
    ok(pre + f"D ist kein Mitglied -> 404 ({s})", s == 404)
    # letzter Admin
    s, d = A.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(A.uid)}", {"admin": False})
    ok(pre + f"A entzieht sich selbst als letzter Admin -> 409 mit Meldung ({s})", s == 409 and "letzte Admin" in str(d.get("error")), d)
    # vergeben
    mark = B.ev.mark()
    s, d = A.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(who_b)}", {"admin": True})
    ch = d.get("chat") or {}
    ok(pre + f"A macht B zum Admin -> 200, Antwort mit aktueller Liste ({s})", s == 200 and mem_of(ch, B).get("admin") is True
       and len(ch.get("members", [])) == 3, d)
    ok(pre + "B: Live-Ereignis chat.changed", bool(B.ev.wait("chat.changed", lambda x: (x or {}).get("chat_id") == cid, since=mark)), B.ev.types(mark))
    s, d = A.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(who_b)}", {"admin": True})
    ok(pre + f"nochmals vergeben: idempotent 200 ({s})", s == 200)
    cb = B.chat(cid)
    ok(pre + "B: jetzt me_admin/can_manage/can_admin", flags(cb) == (True, True, True, True), flags(cb))
    # neuer Admin darf verwalten
    s, d = B.api("POST", f"/api/chats/{cid}/members", {"user": B.ref(D)})
    ok(pre + f"B (neuer Admin) fuegt D hinzu ({s})", s == 200)
    s, d = B.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(mem_of(cb, Cc)['username'])}", {"admin": True})
    ok(pre + f"B macht C zum Admin ({s})", s == 200 and mem_of(d.get("chat") or {}, Cc).get("admin") is True)
    # entziehen
    s, d = B.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(mem_of(cb, A)['username'])}", {"admin": False})
    ok(pre + f"B entzieht A die Admin-Rolle ({s})", s == 200 and mem_of(d.get("chat") or {}, A).get("admin") is False)
    ca = A.chat(cid)
    ok(pre + "A: kein Admin mehr (me_admin/can_manage false)", flags(ca) == (False, False, False, True), flags(ca))
    s, _ = A.api("DELETE", f"/api/chats/{cid}/members/{urllib.parse.quote(mem_of(ca, D)['username'])}")
    ok(pre + f"A (kein Admin mehr) kann D nicht entfernen -> 403 ({s})", s == 403)
    # sich selbst entziehen, wenn ein anderer Admin bleibt
    s, d = B.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(B.uid)}", {"admin": False})
    ok(pre + f"B entzieht sich selbst (C bleibt Admin) ({s})", s == 200 and (d.get("chat") or {}).get("me_admin") is False)
    cc = Cc.chat(cid)
    s, d = Cc.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(Cc.uid)}", {"admin": False})
    ok(pre + f"C (jetzt letzter Admin) kann sich nicht entziehen -> 409 ({s})", s == 409)
    # eigene Handle-Form (Stufe 3) als who
    if v3:
        s, d = Cc.api("PATCH", f"/api/chats/{cid}/members/{urllib.parse.quote(mem_of(cc, A)['handle'])}", {"admin": True})
        ok(pre + f"who als Handle '~x@host' ({s})", s == 200 and mem_of(d.get("chat") or {}, A).get("admin") is True)
        sysd = [x for x in A.sys_lines(cid) if x.get("type") == "admin"]
        ok(pre + f"Systemzeilen type 'admin' ({len(sysd)}): vergeben und entzogen, mit by/target", len(sysd) >= 5
           and any(x.get("on") is True for x in sysd) and any(x.get("on") is False for x in sysd)
           and all((x.get("target") or {}).get("handle") and (x.get("by") or {}).get("handle") for x in sysd), sysd[:2])
        txt = json.dumps(sysd)
        ok(pre + "Systemzeilen ohne Anmeldenamen", not any(u.uid in txt for u in (A, B, Cc, D)) and A.name in txt)
    else:
        sysd = [x for x in A.sys_lines(cid) if x.get("type") == "admin"]
        ok(pre + "Chats v1: keine Systemzeile (wie Hinzufuegen/Entfernen)", sysd == [])
    # Verlassen des letzten Admins: Nachfolger wie bisher
    s, _ = Cc.api("DELETE", f"/api/chats/{cid}/members/{urllib.parse.quote(Cc.uid)}")
    ch = B.chat(cid)
    adm = [m["name"] for m in ch.get("members", []) if m.get("admin")]
    ok(pre + f"Admin verlaesst die Gruppe -> es bleibt mindestens ein Admin ({adm})", s == 200 and len(adm) >= 1)
    return cid


def run():
    mk = lambda: "ca-" + secrets.token_hex(6)                     # noqa: E731
    s, d = CT.req("GET", "/api/version")
    ok("/api/version: Feature chatadm1", s == 200 and "chatadm1" in d.get("features", []), d.get("features"))
    from saga import federation as F
    ok("Foederation: Faehigkeit chatadm1 (CAPS2, Admin-Schalter chat)", "chatadm1" in F.CAPS2 and F.CAP_OPT.get("chatadm1") == "chat")
    # --- v1 ---
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    A, B, Cc, D = U(mk(), "Anna Admin"), U(mk(), "Bert Admin"), U(mk(), "Cora Admin"), U(mk(), "Dora Admin")
    with connect() as c, c:
        auth.ensure_group(c, "ca-team")
    for u in (A, B):
        auth.set_user_groups(auth.get_user(u.uid)["id"], ["ca-team"])
    series("v1", A, B, Cc, D, False)
    # Gruppen- und Seiten-Chat: 400
    s, d = A.api("POST", "/api/chats", {"kind": "group", "group": "ca-team"})
    gid = (d.get("chat") or {}).get("id")
    gch = d.get("chat") or {}
    ok(f"Gruppen-Chat: me_admin/can_manage/can_admin/can_rename false ({s})", s == 200 and flags(gch) == (False, False, False, False), flags(gch))
    ok("Gruppen-Chat: members[].admin false", all(m.get("admin") is False for m in gch.get("members", [])))
    s, d = A.api("PATCH", f"/api/chats/{gid}/members/{urllib.parse.quote(mem_of(gch, B).get('username', 'x'))}", {"admin": True})
    ok(f"Gruppen-Chat: Admin vergeben -> 400 ({s})", s == 400 and "Gruppe bzw. den Freigaben" in str(d.get("error")))
    s, d = A.api("POST", "/api/notes", {"title": "ca-Seite"})
    nid = d["id"]
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": B.uid, "role": "view"})
    s, d = A.api("POST", "/api/chats", {"kind": "page", "note_id": nid})
    pch = d.get("chat") or {}
    s, d = A.api("PATCH", f"/api/chats/{pch.get('id')}/members/{urllib.parse.quote(mem_of(pch, B).get('username', 'x'))}", {"admin": True})
    ok(f"Seiten-Chat: Admin vergeben -> 400 ({s}), can_rename false", s == 400 and pch.get("can_rename") is False)
    s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": A.ref(B)})
    dch = d.get("chat") or {}
    s, _ = A.api("PATCH", f"/api/chats/{dch.get('id')}/members/{urllib.parse.quote(B.uid)}", {"admin": True})
    ok(f"Direkt-Chat: Admin vergeben -> 400 ({s})", s == 400)
    s, _ = A.api("PATCH", "/api/chats/999999999/members/x", {"admin": True})
    ok(f"unbekannter Chat -> 404 ({s})", s == 404)
    # --- Stufe 3 ---
    auth.save_setting("chat_v3", True)
    time.sleep(WAIT)
    E, F_, G, H = U(mk(), "Emil Admin"), U(mk(), "Fritz Admin"), U(mk(), "Gina Admin"), U(mk(), "Hans Admin")
    for u in (E, F_, G, H):
        u.certs()
    series("Stufe 3", E, F_, G, H, True)


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
    print(f"\nCHAT-ADMINS {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
