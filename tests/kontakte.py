"""Kontakte im Chat (2.5, contacts1, saga/contacts.py): Personen aus Direkt-Chats und freien Gruppen bleiben auffindbar.

Geprueft: automatisch eingetragen (Direkt-Chat, freie Gruppe, Mitglied hinzufuegen - gegenseitig), nicht bei Saga-Gruppen-
und Seiten-Chats; bleibt nach Chat loeschen/verlassen; "Aus Kontakten entfernen" (hidden) und wieder sichtbar durch einen
neuen gemeinsamen Chat; GET /api/chat/people mischt Kontakte vorne ein (1-2 Zeichen als Teilwort, ohne Dubletten) und
umgeht "nur gemeinsame Gruppen"; nie ein Anmeldename (Marker-Scan aller Antworten); Konto deaktiviert -> gone, geloescht
-> Eintraege weg (beide Richtungen); Backup (daten.json) und Einspielen; rueckwirkendes Fuellen idempotent; Feature.
Wegwerf-Konten ko-*, Gruppe ko-team; raeumt auf."""
import json, os, re, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Person)
from saga import contacts  # noqa: E402
from saga.core import local_ref  # noqa: E402

res, SEEN = [], []
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


class U(CT.Person):
    def __init__(self, uid, name, setup=True):
        auth.create_user(uid, None, name)
        super().__init__(uid, setup)
        self.name = name

    def api(self, m, p, b=None):
        s, d = CT.req(m, p, self.k, b)
        SEEN.append((self.uid, f"{m} {p}", json.dumps(d, ensure_ascii=False) if not isinstance(d, (str, bytes)) else str(d)))
        return s, d

    def contacts(self, q=""):
        s, d = self.api("GET", "/api/chat/contacts" + ("?q=" + urllib.parse.quote(q) if q else ""))
        return d.get("contacts") if s == 200 else None

    def refs(self, q=""):
        return [k["ref"] for k in self.contacts(q) or []]

    def people(self, q):
        return self.api("GET", "/api/chat/people?q=" + urllib.parse.quote(q))[1]

    def dm(self, other):
        return self.api("POST", "/api/chats", {"kind": "dm", "user": local_ref(other.uid)})

    @property
    def ref(self):
        return local_ref(self.uid)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ko-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'ko-%' OR dm_key LIKE 'ko-%' OR created_by LIKE 'ko-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ko-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'ko-%'")
        c.execute("DELETE FROM chat_contact WHERE uid LIKE 'ko-%'")


def group(name, uids):
    with connect() as c, c:
        gid = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', ?)", (name, time.time())).lastrowid
        for u in uids:
            c.execute("INSERT INTO user_group (user_id, group_id) SELECT id, ? FROM users WHERE username=?", (gid, u))


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("search_username", False)):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
    finally:
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nKONTAKTE {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    mk = lambda: "ko-" + secrets.token_hex(6)                    # noqa: E731  Marker = Anmeldename
    A, B, Cc, D = U(mk(), "Anja Kontakt"), U(mk(), "Bela Kontakt"), U(mk(), "Cleo Kontakt"), U(mk(), "Dino Kontakt")
    E, F, G, H = U(mk(), "Edda Kontakt"), U(mk(), "Fritz Kontakt"), U(mk(), "Gerd Kontakt"), U(mk(), "Hanna Fremd", setup=False)
    s, d = CT.req("GET", "/api/version")
    ok("Feature contacts1 in /api/version", "contacts1" in (d.get("features") or []), d.get("features"))

    # --- automatisch eintragen -------------------------------------------------------------------------------------------
    ok("Anfangs keine Kontakte", A.contacts() == [] and B.contacts() == [])
    s, d = A.dm(B)
    dm = d.get("chat") or {}
    ok(f"Direkt-Chat A-B ({s})", s == 200)
    ka = A.contacts()
    ok("A hat B als Kontakt (Handle, Name, Felder)", [k["ref"] for k in ka] == [B.ref] and ka[0]["name"] == "Bela Kontakt"
       and set(ka[0]) >= {"ref", "name", "sub", "avatar", "remote", "ready", "gone"} and ka[0]["ready"] and not ka[0]["gone"] and not ka[0]["remote"], ka)
    ok("B hat A als Kontakt (gegenseitig)", B.refs() == [A.ref])
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [Cc.ref, D.ref]})
    fr = d.get("chat") or {}
    ok(f"Freie Gruppe A, C, D ({s})", s == 200 and fr.get("kind") == "free")
    ok("A: C und D dazu", set(A.refs()) == {B.ref, Cc.ref, D.ref})
    ok("C: A und D (alle Mitglieder gegenseitig)", set(Cc.refs()) == {A.ref, D.ref})
    s, d = A.api("POST", f"/api/chats/{fr['id']}/members", {"user": E.ref})
    ok(f"Mitglied E hinzugefuegt ({s})", s == 200)
    ok("E: A, C, D als Kontakte; C und D kennen E", set(E.refs()) == {A.ref, Cc.ref, D.ref} and E.ref in Cc.refs() and E.ref in D.refs())
    # Saga-Gruppe und Seiten-Chat: keine Kontakte
    group("ko-team", [A.uid, F.uid])
    s, d = A.api("POST", "/api/chats", {"kind": "group", "group": "ko-team"})
    ok(f"Saga-Gruppen-Chat ({s})", s == 200 and (d.get("chat") or {}).get("kind") == "group")
    nid = A.api("POST", "/api/notes", {"title": "Kontakt-Seite"})[1]["id"]
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": G.ref, "role": "edit"})
    s, d = A.api("POST", "/api/chats", {"kind": "page", "note_id": nid})
    pg = d.get("chat") or {}
    G.api("GET", f"/api/chats/{pg.get('id')}")
    ok(f"Seiten-Chat ({s})", s == 200 and pg.get("kind") == "page")
    ok("Saga-Gruppen- und Seiten-Chat ergeben keine Kontakte", F.ref not in A.refs() and G.ref not in A.refs() and F.contacts() == [] and G.contacts() == [])

    # --- bleibt nach Loeschen/Verlassen ---------------------------------------------------------------------------------
    s, d = A.api("DELETE", f"/api/chats/{dm['id']}?scope=all")
    ok(f"Direkt-Chat fuer alle geloescht ({s})", s == 200 and d.get("removed"))
    s, d = Cc.api("DELETE", f"/api/chats/{fr['id']}/members/{urllib.parse.quote(Cc.uid)}")
    ok(f"C verlaesst die Gruppe ({s})", s == 200)
    ok("Kontakte bleiben (A: B; C: A, D, E)", B.ref in A.refs() and A.ref in B.refs() and set(Cc.refs()) == {A.ref, D.ref, E.ref})

    # --- Suche: Kontakte vorne, kurze Anfragen, keine Dubletten ----------------------------------------------------------
    p = A.people("be")
    us = p.get("users") or []
    ok("people: 2 Zeichen als Teilwort findet den Kontakt B (contact: true)", any(u["username"] == B.ref and u.get("contact") for u in us), us)
    ok("people: 2 Zeichen finden Nicht-Kontakte nicht als Teilwort (Fritz/Hanna fehlen)", not any(u["username"] in (F.ref, H.ref) for u in A.people("fr").get("users") or []))
    us = A.people("Kontakt").get("users") or []
    refs = [u["username"] for u in us]
    ok("people: Kontakte vor den uebrigen Treffern, ohne Dubletten", len(refs) == len(set(refs)) and refs[:4] and set(refs[:4]) == {B.ref, Cc.ref, D.ref, E.ref}
       and all(u.get("contact") for u in us[:4]) and not any(u.get("contact") for u in us[4:]), refs)
    ok("contacts?q= filtert (Teilwort, auch kurz)", A.refs("cl") == [Cc.ref] and A.refs("xyz") == [])
    ok("Sortierung nach last_at (zuletzt zuerst)", A.refs()[0] == E.ref, A.refs())

    # --- Aus Kontakten entfernen + wieder sichtbar ------------------------------------------------------------------------
    s, _ = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(B.ref))
    ok(f"DELETE /api/chat/contacts/{{ref}} ({s})", s == 200 and B.ref not in A.refs())
    s, _ = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(B.ref))
    s2, _ = A.api("DELETE", "/api/chat/contacts/~aaaaaaaaaaaaaaaa")
    ok(f"Zweites Mal bzw. unbekannt -> 404 ({s}, {s2})", s == 404 and s2 == 404)
    ok("Ausgeblendet: nicht in people (2 Zeichen), B sieht A weiter", not any(u["username"] == B.ref for u in A.people("be").get("users") or [])
       and A.ref in B.refs())
    s, _ = B.dm(A)
    ok(f"Neuer gemeinsamer Chat macht wieder sichtbar ({s})", s == 200 and B.ref in A.refs())

    # --- "nur gemeinsame Gruppen": Kontakte als Ausnahme --------------------------------------------------------------------
    auth.save_setting("share_group_scope", True)
    time.sleep(WAIT)
    try:
        us = A.people("Kontakt").get("users") or []
        ok("group_scope: Kontakte weiter gefunden, Nicht-Kontakte ausserhalb der Gruppe nicht", {B.ref, D.ref} <= {u["username"] for u in us}
           and G.ref not in {u["username"] for u in us}, [u["username"] for u in us])
        s, _ = A.dm(D)
        s2, _ = A.dm(G)
        ok(f"group_scope: Direkt-Chat mit Kontakt erlaubt ({s}), mit Nicht-Kontakt nicht ({s2})", s == 200 and s2 == 404)
        s, _ = H.api("POST", "/api/chats", {"kind": "dm", "user": A.ref})
        ok(f"group_scope: Kontakte gelten nur in eine Richtung (H kennt A nicht) ({s})", s == 404)
    finally:
        auth.save_setting("share_group_scope", False)
        time.sleep(WAIT)

    # --- deaktiviert / geloescht ------------------------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=1 WHERE username=?", (B.uid,))
    kb = next((k for k in A.contacts() if k["ref"] == B.ref), {})
    ok("Konto deaktiviert -> gone, nicht anschreibbar, ohne Bild", kb.get("gone") is True and kb.get("ready") is False and kb.get("avatar") is None, kb)
    ok("Deaktiviert: nicht in people", not any(u["username"] == B.ref for u in A.people("Bela").get("users") or []))
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=0 WHERE username=?", (B.uid,))
    ok("Wieder aktiv -> nicht mehr gone", not next((k for k in A.contacts() if k["ref"] == B.ref), {}).get("gone", True))
    from saga.userdata import delete_user_data
    delete_user_data(D.uid, drop_user=True)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_contact WHERE uid=? OR ref=?", (D.uid, D.ref)).fetchone()[0]
    ok("Konto geloescht -> Eintraege weg (beide Richtungen)", n == 0 and D.ref not in A.refs() and D.ref not in E.refs())

    # --- Backup / Export --------------------------------------------------------------------------------------------------
    from saga import userbackup
    with connect() as c:
        data, _, _ = userbackup.collect(c, A.uid, dict(userbackup.DEFAULT, cats=dict(userbackup.CAT_DEFAULT)))
    cc = data.get("chat_contacts") or []
    ok(f"Backup: chat_contacts in daten.json ({len(cc)})", {x["ref"] for x in cc} == set(A.refs()) and all(set(x) >= {"ref", "name", "last_at", "hidden"} for x in cc))
    with connect() as c, c:
        c.execute("DELETE FROM chat_contact WHERE uid=?", (A.uid,))
        n = contacts.restore(c, A.uid, cc + [{"ref": G.ref, "name": "x"}, {"ref": A.ref}, {"ref": "ko-boese"}],
                             may_local=lambda ref: True)
    ok(f"Einspielen: nur gueltige Kennungen, nie das eigene Konto ({n})", n == len(cc) + 1 and A.ref not in A.refs() and set(A.refs()) >= {x["ref"] for x in cc})

    # --- rueckwirkend (Migration 28) idempotent ----------------------------------------------------------------------------
    with connect() as c, c:
        c.execute("DELETE FROM chat_contact WHERE uid=?", (E.uid,))
        c.execute("UPDATE chat_contact SET hidden=1 WHERE uid=? AND ref=?", (Cc.uid, E.ref))
        contacts.backfill(c)
        contacts.backfill(c)
        rows = c.execute("SELECT ref, COUNT(*) FROM chat_contact WHERE uid=? GROUP BY ref", (E.uid,)).fetchall()
        hid = c.execute("SELECT hidden FROM chat_contact WHERE uid=? AND ref=?", (Cc.uid, E.ref)).fetchone()
    ok("Rueckwirkend: aus vorhandenen Chats, doppelt ausgefuehrt ohne Dubletten", {r[0] for r in rows} >= {A.ref} and all(r[1] == 1 for r in rows), rows)
    ok("Rueckwirkend: ausgeblendete bleiben ausgeblendet; Gruppen-/Seiten-Chats nicht", bool(hid) and hid[0] == 1
       and F.contacts() == [] and G.contacts() == [])

    # --- offene Anfrage auf einer Seite (z. B. fremdes Heim laedt ein): niemand wird Kontakt ----------------------------------
    with connect() as c, c:
        cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', ?, ?)", (F.uid, time.time())).lastrowid
        c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'ok')", (cid, F.uid, time.time()))
        c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'request')", (cid, G.uid, time.time()))
        contacts.link(c, cid)
        contacts.backfill(c)
        n = c.execute("SELECT COUNT(*) FROM chat_contact WHERE (uid=? AND ref=?) OR (uid=? AND ref=?)", (F.uid, G.ref, G.uid, F.ref)).fetchone()[0]
        c.execute("DELETE FROM chat WHERE id=?", (cid,))
    ok("Offene Anfrage: weder Eingeladene noch die anderen Mitglieder werden Kontakte (auch rueckwirkend)", n == 0, n)

    # --- Datenschutz ------------------------------------------------------------------------------------------------------
    s, _ = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(B.uid))
    ok(f"DELETE mit Anmeldenamen statt Handle -> 404 (kein Orakel) ({s})", s == 404)
    s, d = CT.req("GET", "/api/chat/contacts")
    ok(f"Ohne Anmeldung -> 401 ({s})", s == 401)
    users = [A, B, Cc, E, F, G, H]
    for u in users:
        rx = re.compile("|".join(re.escape(o.uid) for o in users + [D] if o is not u))
        # Kontakte, Personensuche und Chats (Chats v1 zeigen Mitgliedern den Anmeldenamen fuer die Schluessel - members[].username,
        # gewollt und in anmeldenamen_v3 fuer Stufe 3 geprueft; hier zaehlen nur die Kontakt-Endpunkte)
        hits = [(src, rx.search(txt).group(0)) for who, src, txt in SEEN if who == u.uid and ("/api/chat/contacts" in src or "/api/chat/people" in src)
                and rx.search(txt)]
        n = sum(1 for who, src, _ in SEEN if who == u.uid and ("/api/chat/contacts" in src or "/api/chat/people" in src))
        ok(f"Marker-Scan {u.name}: {n} Antworten von contacts/people ohne Anmeldenamen anderer Konten", not hits, hits[:3])
    with connect() as c:
        bad = c.execute("SELECT COUNT(*) FROM chat_contact WHERE ref LIKE 'ko-%' OR name LIKE 'ko-%'").fetchone()[0]
    ok("Tabelle: kein Anmeldename in ref/name", bad == 0)


if __name__ == "__main__":
    main()
