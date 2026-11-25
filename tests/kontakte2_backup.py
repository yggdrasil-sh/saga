"""Adressbuch im persoenlichen Backup (2.6, Bugs-DB 2027a-3, saga/userbackup.py + contacts.book_export/book_restore).

Geprueft: daten.json -> contacts (Name fuer mich, Anrede, Grussform, Nummern, Mails; feste Adressen als Handle, nie ein
Anmeldename), chat_contacts weiter, LIESMICH-Zeile, Zaehler im Manifest, Landesvorwahl in settings; Einspielen ins eigene
Konto zweimal = keine Dubletten, fehlende Daten werden ergaenzt, leere Felder gefuellt (gesetzte bleiben); Einspielen in ein
fremdes Konto ohne gemeinsamen Chat ("nur gemeinsame Gruppen") haengt keine Saga-Adresse an - Kontakte mit eigenen Daten
werden manual ohne Saga-Adresse, leere verworfen, manuelle angelegt; has() bleibt falsch; Kollision mit vorhandenen Werten
uebersprungen; ohne Option contacts nichts. Wegwerf-Konten k2b-*, Gruppe k2b-team; raeumt auf."""
import json, os, secrets, sys, tempfile, time, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402
from saga import contacts, userbackup  # noqa: E402
from saga.core import local_ref  # noqa: E402

res = []
WAIT = 5.6
OPTS = {"folder": True, "trash": False, "versions": True, "tasks": False, "bookmarks": False, "contacts": True}


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)


class U(CT.Person):
    def __init__(self, uid, name, setup=True):
        auth.create_user(uid, None, name)
        super().__init__(uid, setup)
        self.name = name

    def api(self, m, p, b=None):
        return CT.req(m, p, self.k, b)

    def book(self):
        s, d = self.api("GET", "/api/contacts")
        return {(x.get("saga") or {}).get("ref") or f"#{x.get('own_name')}": x for x in d.get("contacts") or []} if s == 200 else None

    @property
    def ref(self):
        return local_ref(self.uid)


def rows(uid):
    with connect() as c:
        return (c.execute("SELECT COUNT(*) FROM contact WHERE uid=?", (uid,)).fetchone()[0],
                c.execute("SELECT COUNT(*) FROM contact_addr WHERE uid=?", (uid,)).fetchone()[0])


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'k2b-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'k2b-%' OR created_by LIKE 'k2b-%' OR group_name LIKE 'k2b-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'k2b-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'k2b-%'")
        c.execute("DELETE FROM contact WHERE uid LIKE 'k2b-%'")


def group(name, uids):
    with connect() as c, c:
        gid = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', ?)", (name, time.time())).lastrowid
        for u in uids:
            c.execute("INSERT INTO user_group (user_id, group_id) SELECT id, ? FROM users WHERE username=?", (gid, u))


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", True), ("search_username", False)):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    auth.settings(fresh=True)
    try:
        run()
    finally:
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nKONTAKTE2-BACKUP {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def backup(uid, tmp, n):
    p = os.path.join(tmp, f"b{n}.zip")
    counts = userbackup.build_zip(uid, dict(userbackup.DEFAULT, cats=dict(userbackup.CAT_DEFAULT)), p, 1 << 30, "Test")
    return p, counts


def run():
    mk = lambda: "k2b-" + secrets.token_hex(6)                    # noqa: E731
    A, B, Cc, D = U(mk(), "Alva Sicher"), U(mk(), "Bodo Sicher"), U(mk(), "Cilli Sicher"), U(mk(), "Dagmar Fremd")
    names = [A.uid, B.uid, Cc.uid, D.uid]
    group("k2b-team", [A.uid, B.uid, Cc.uid])                   # D in keiner gemeinsamen Gruppe
    tmp = tempfile.mkdtemp(prefix="k2b-", dir=os.environ.get("STATE_DIR", "/tmp"))
    for o in (B, Cc):
        s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": o.ref})
        ok(f"Direkt-Chat A-{o.name} ({s})", s == 200)
    bk = A.book()
    kb = bk[B.ref]
    s, d = A.api("PATCH", f"/api/contacts/{kb['id']}", {"ver": kb["ver"], "own_name": "Bodi", "form": "informal", "salutation": "Lieber Bodo",
                                                       "addrs": [{"kind": "e164", "value": "+491703330001", "type": "mobile"},
                                                                 {"kind": "email", "value": "bodo@sicher.example", "type": "home"}]})
    ok(f"A: Bodo mit Name fuer mich, Anrede, Nummer, Mail ({s})", s == 200)
    s, m = A.api("POST", "/api/contacts", {"own_name": "Mia Manuell", "form": "formal",
                                           "addrs": [{"kind": "e164", "value": "+491703330002"}, {"kind": "email", "value": "mia@manuell.example"}]})
    ok(f"A: manueller Kontakt ({s})", s == 201)
    A.api("PUT", "/api/contacts/settings", {"cc": "49"})
    before = rows(A.uid)

    # --- Export ---------------------------------------------------------------------------------------------------------
    p1, counts = backup(A.uid, tmp, 1)
    with zipfile.ZipFile(p1) as z:
        data = json.loads(z.read("daten.json"))
        readme = z.read("LIESMICH.txt").decode()
        man = json.loads(z.read("manifest.json"))
        raw = z.read("daten.json").decode()
    book = data.get("contacts") or []
    ok(f"daten.json: contacts ({len(book)}) und chat_contacts ({len(data.get('chat_contacts') or [])})", len(book) == 3
       and {x["ref"] for x in data.get("chat_contacts") or []} == {B.ref, Cc.ref}, book)
    xb = [x for x in book if x.get("own_name") == "Bodi"]
    ok("Eintrag: Felder und Adressen (feste als Handle)", xb and set(xb[0]) == {"own_name", "origin", "form", "salutation", "created", "updated", "addrs"}
       and xb[0]["form"] == "informal" and xb[0]["salutation"] == "Lieber Bodo" and xb[0]["origin"] == "chat"
       and xb[0]["addrs"][0] == {"kind": "saga", "value": B.ref, "type": None, "fixed": True}
       and {"kind": "e164", "value": "+491703330001", "type": "mobile", "fixed": False} in xb[0]["addrs"], xb)
    ok("Kein Anmeldename anderer Konten in daten.json", not [u for u in names if u != A.uid and u in raw])
    ok("LIESMICH nennt daten.json -> contacts", "daten.json → contacts" in readme)
    ok(f"Zaehler contacts im Manifest ({man['counts'].get('contacts')})", man["counts"].get("contacts") == 3 and counts.get("contacts") == 3)
    ok("Landesvorwahl in settings", (data.get("settings") or {}).get("contacts.cc") == "49")

    # --- ins eigene Konto: zweimal = keine Dubletten --------------------------------------------------------------------
    r1 = userbackup.import_zip(A.uid, p1, OPTS)
    r2 = userbackup.import_zip(A.uid, p1, OPTS)
    ok(f"Eigenes Konto zweimal eingespielt: nichts doppelt ({r1.get('address_book')}, {r2.get('address_book')})",
       rows(A.uid) == before and r1.get("address_book") == 0 and r2.get("address_book") == 0, (rows(A.uid), before))
    # Daten verloren -> Einspielen ergaenzt (leere Felder gefuellt, gesetzte bleiben)
    kb = A.book()[B.ref]
    A.api("PATCH", f"/api/contacts/{kb['id']}", {"ver": kb["ver"], "own_name": "Bodo neu", "form": None, "addrs": []})
    A.api("DELETE", f"/api/contacts/{m['id']}")
    A.api("PUT", "/api/contacts/settings", {"cc": None})
    r3 = userbackup.import_zip(A.uid, p1, OPTS)
    kb = A.book()[B.ref]
    ok(f"Einspielen ergaenzt Nummer/Mail, fuellt form, own_name bleibt ({r3.get('address_book')})", kb["own_name"] == "Bodo neu"
       and kb["form"] == "informal" and sorted(a["value"] for a in kb["addrs"] if not a["fixed"]) == ["+491703330001", "bodo@sicher.example"], kb)
    mm = [x for x in A.book().values() if x["own_name"] == "Mia Manuell"]
    ok("Manueller Kontakt wieder da (mit Typen, form)", mm and mm[0]["origin"] == "manual" and mm[0]["form"] == "formal"
       and sorted(a["value"] for a in mm[0]["addrs"]) == ["+491703330002", "mia@manuell.example"], mm)
    ok("Landesvorwahl wieder gesetzt", A.api("GET", "/api/contacts/settings")[1] == {"cc": "49"})
    n3 = rows(A.uid)
    r4 = userbackup.import_zip(A.uid, p1, OPTS)
    ok(f"Nochmal: keine Dubletten ({r4.get('address_book')})", rows(A.uid) == n3 and r4.get("address_book") == 0)

    # --- fremdes Konto ohne gemeinsamen Chat/Gruppe ---------------------------------------------------------------------
    s, d = D.api("POST", "/api/contacts", {"own_name": "Schon da", "addrs": [{"kind": "e164", "value": "+491703330002"}]})
    ok(f"D: Nummer von Mia schon vergeben ({s})", s == 201)
    r5 = userbackup.import_zip(D.uid, p1, OPTS)
    bd = D.book()
    with connect() as c:
        fx = c.execute("SELECT kind, value FROM contact_addr WHERE uid=? AND fixed=1", (D.uid,)).fetchall()
        has = contacts.has(c, D.uid, B.uid) or contacts.has(c, D.uid, Cc.uid)
        ccd = c.execute("SELECT COUNT(*) FROM chat_contact WHERE uid=?", (D.uid,)).fetchone()[0]
    ok(f"D: keine Saga-Adresse angehaengt, kein Chat-Kontakt, has() falsch ({r5.get('contacts')}, {r5.get('address_book')})",
       not fx and not has and ccd == 0, fx)
    xb = [x for x in bd.values() if x.get("own_name") == "Bodi"]
    ok("D: Bodos Kontakt (eigene Daten) als manual ohne Saga-Adresse", xb and xb[0]["origin"] == "manual" and xb[0]["saga"] is None
       and xb[0]["salutation"] == "Lieber Bodo" and len(xb[0]["addrs"]) == 2, xb)
    ok("D: Cillis leerer Kontakt verworfen", not any(Cc.ref in json.dumps(x) for x in bd.values()) and len(bd) == 3, sorted(bd))
    mm = [x for x in bd.values() if x.get("own_name") == "Mia Manuell"]
    ok("D: Mia angelegt, vergebene Nummer uebersprungen", mm and [a["value"] for a in mm[0]["addrs"]] == ["mia@manuell.example"], mm)
    n5 = rows(D.uid)
    userbackup.import_zip(D.uid, p1, OPTS)
    ok("D: zweimal = keine Dubletten", rows(D.uid) == n5, (rows(D.uid), n5))

    # --- ohne Option contacts: nichts -----------------------------------------------------------------------------------
    E = U(mk(), "Emma Leer", setup=False)
    names.append(E.uid)
    userbackup.import_zip(E.uid, p1, dict(OPTS, contacts=False))
    ok("Ohne Option contacts: kein Adressbuch", rows(E.uid) == (0, 0))

    # --- gebastelte Eintraege -------------------------------------------------------------------------------------------
    evil = [{"own_name": None, "addrs": [{"kind": "saga", "value": B.ref, "fixed": True}]},
            {"own_name": "Boese", "addrs": [{"kind": "saga", "value": B.ref, "fixed": True}, {"kind": "e164", "value": "keine Nummer"}]},
            {"own_name": "X" * 500, "form": "frech", "addrs": [{"kind": "email", "value": "a@b@c"}]}, "kaputt", None]
    with connect() as c, c:
        n = contacts.book_restore(c, E.uid, evil)
    with connect() as c:
        fx = c.execute("SELECT COUNT(*) FROM contact_addr WHERE uid=? AND (fixed=1 OR kind IN ('saga','fed'))", (E.uid,)).fetchone()[0]
        own = [r[0] for r in c.execute("SELECT own_name FROM contact WHERE uid=? ORDER BY id", (E.uid,))]
        bad = c.execute("SELECT COUNT(*) FROM contact_addr WHERE uid=?", (E.uid,)).fetchone()[0]
    ok(f"Gebastelt: keine feste Adresse ohne Chat, ungueltige Werte weg, Laenge gekuerzt ({n}, {own})", fx == 0 and bad == 0
       and own == ["Boese", "X" * 120])
    # --- Mail = genau ein Empfaenger (Bugs-DB 2399): Trenner, Steuer- (auch C1) und Formatzeichen werden uebergangen -----
    F = U(mk(), "Fritz Trenner", setup=False)
    names.append(F.uid)
    mails = ["a@x.example,b@y.example", "a@x.example;b@y.example", "a?cc=b@y.example", "a\x9b@x.example",
             "a\u200b@x.example", "a\u00ad@x.example", "gut@x.example"]
    with connect() as c, c:
        contacts.book_restore(c, F.uid, [{"own_name": "Trenner", "addrs": [{"kind": "email", "value": v} for v in mails]}])
    with connect() as c:
        got = [r[0] for r in c.execute("SELECT value FROM contact_addr WHERE uid=? AND kind='email'", (F.uid,))]
    ok(f"Backup: Mails mit Trenner/Steuer-/Formatzeichen uebergangen, gueltige bleibt ({len(got)})", got == ["gut@x.example"])
    with connect() as c:
        raw_all = json.dumps([list(r) for r in c.execute("SELECT kind, value, type FROM contact_addr WHERE uid LIKE 'k2b-%'")])
    ok("Kein Anmeldename im Adressbuch", not [u for u in names if u in raw_all])


if __name__ == "__main__":
    main()
