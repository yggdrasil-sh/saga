"""Adressbuch-Modell (2.6, Bugs-DB 2027a-1, saga/contacts.py, Tabellen contact / contact_addr, Migration 38).

Geprueft: Upgrade 37 -> 38 mit 2.5-Bestand (mit "seed" vorher gegen einen 2.5-Server angelegt: Direkt-Chats, freie Gruppe,
ausgeblendeter Kontakt, Personen anderer Server) - je sichtbarem chat_contact genau ein Kontakt, ausgeblendete ohne,
zweiter Start legt nichts an; dazu dasselbe nachgestellt (Merker zurueck). Neuer Direkt-Chat -> Kontakt beidseitig;
alte DELETE: leerer Kontakt weg, mit Nummer bleibt; neuer Chat nach Loeschen -> neuer leerer Kontakt; Konto loeschen
(beide Richtungen), drop_host, drop_remote (leer weg, mit Nummer bleibt als manual ohne Saga-/Fed-Adresse); has() wird
durch das Adressbuch nicht wahr; kein Anmeldename im Adressbuch; USER_TABLES; Live contacts.changed.

Aufruf: python tests/kontakte2_modell.py [seed]. Wegwerf-Konten k2-* (Bestand k2u-*); raeumt auf."""
import json, os, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Person, Stream)
from saga import contacts  # noqa: E402
from saga.core import local_ref  # noqa: E402

res, SEEN = [], []
WAIT = 5.6
FERN = "fern-k2.example"


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

    def chat_contacts(self):
        s, d = self.api("GET", "/api/chat/contacts")
        return {k["ref"]: k for k in d.get("contacts") or []} if s == 200 else None

    def dm(self, other):
        return self.api("POST", "/api/chats", {"kind": "dm", "user": local_ref(other.uid)})

    def free(self, *others):
        return self.api("POST", "/api/chats", {"kind": "free", "members": [local_ref(o.uid) for o in others]})

    @property
    def ref(self):
        return local_ref(self.uid)


def book(uid):
    """-> {fester Wert oder '#id': {id, origin, own_name, addrs: [(kind, value, fixed)]}}"""
    out = {}
    with connect() as c:
        rows = c.execute("SELECT id, origin, own_name, form, salutation FROM contact WHERE uid=? ORDER BY id", (uid,)).fetchall()
        for cid, origin, own, form, sal in rows:
            ad = [(r[0], r[1], bool(r[2])) for r in c.execute(
                "SELECT kind, value, fixed FROM contact_addr WHERE contact_id=? ORDER BY fixed DESC, pos, id", (cid,))]
            fx = [v for k, v, f in ad if f]
            out[fx[0] if fx else f"#{cid}"] = {"id": cid, "origin": origin, "own_name": own, "form": form, "salutation": sal, "addrs": ad}
    return out


def add_number(uid, cid, num):
    with connect() as c, c:
        c.execute("INSERT INTO contact_addr (contact_id, uid, kind, value, type, fixed, pos, created) VALUES (?,?,?,?,'mobile',0,1,?)",
                  (cid, uid, "e164", num, time.time()))


def cleanup(pfx):
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE ?", (pfx + "%",)).fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE ? OR created_by LIKE ?", (pfx + "%", pfx + "%"))
        c.execute("DELETE FROM users WHERE username LIKE ?", (pfx + "%",))
        c.execute("DELETE FROM chat_contact WHERE uid LIKE ?", (pfx + "%",))
        if c.execute("SELECT to_regclass('contact')").fetchone()[0]:          # Bestand (seed) laeuft noch auf Schema 37
            c.execute("DELETE FROM contact WHERE uid LIKE ?", (pfx + "%",))


def fern(n):
    return "~" + (chr(ord("a") + n) * 16) + "@" + FERN


# --- Bestand fuer den Upgrade-Test (gegen den 2.5-Server, Schema 37) ----------------------------------------------------
def seed():
    cleanup("k2u-")
    mk = lambda: "k2u-" + secrets.token_hex(5)                    # noqa: E731
    A, B, Cc, D, E = (U(mk(), n) for n in ("Alma Bestand", "Bruno Bestand", "Cora Bestand", "Dora Bestand", "Emil Bestand"))
    s1, _ = A.dm(B)
    s2, _ = A.dm(Cc)
    s3, _ = A.free(D, E)
    s4, _ = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(Cc.ref))
    with connect() as c, c:
        now = time.time()
        for i, hid in ((0, 0), (1, 0), (2, 1)):
            c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,?,NULL,?,?,?)",
                      (A.uid, fern(i), f"Fern {i}", now - 100 + i, now - 100 + i, hid))
        sv = c.execute("SELECT MAX(v) FROM schema_version").fetchone()[0]
        n = c.execute("SELECT COUNT(*) FROM chat_contact WHERE uid LIKE 'k2u-%'").fetchone()[0]
    print(f"Bestand angelegt: {s1} {s2} {s3} {s4}, Schema {sv}, chat_contact {n}", flush=True)
    good = (s1, s2, s3, s4) == (200, 200, 200, 200) and sv == 37 and n >= 11
    print("SEED " + ("OK" if good else "FEHLER"))
    sys.exit(0 if good else 1)


def check_upgrade():
    with connect() as c:
        owners = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'k2u-%'").fetchall()]
        sv = c.execute("SELECT MAX(v) FROM schema_version").fetchone()[0]
        mig = [r[0] for r in c.execute("SELECT v FROM schema_version WHERE v>=37 ORDER BY v")]
    if not owners:
        print("(kein 2.5-Bestand k2u-* - Upgrade-Test nur nachgestellt; vorher 'seed' gegen einen 2.5-Server)", flush=True)
        return
    ok(f"Upgrade: Schema 38 ({sv}, {mig})", sv == 38 and 38 in mig)
    with connect() as c:
        vis = {(r[0], r[1]) for r in c.execute("SELECT uid, ref FROM chat_contact WHERE uid LIKE 'k2u-%' AND hidden=0")}
        hid = {(r[0], r[1]) for r in c.execute("SELECT uid, ref FROM chat_contact WHERE uid LIKE 'k2u-%' AND hidden<>0")}
        fx = [(r[0], r[1]) for r in c.execute("SELECT uid, value FROM contact_addr WHERE uid LIKE 'k2u-%' AND fixed=1")]
        per = c.execute("SELECT COUNT(*) FROM contact_addr a WHERE uid LIKE 'k2u-%' AND fixed=1 AND "
                        "(SELECT COUNT(*) FROM contact_addr b WHERE b.contact_id=a.contact_id) <> 1").fetchone()[0]
        kinds = {r[0]: r[1] for r in c.execute("SELECT value, kind FROM contact_addr WHERE uid LIKE 'k2u-%'")}
        nc = c.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE origin='chat' AND own_name IS NULL AND form IS NULL AND salutation IS NULL) "
                       "FROM contact WHERE uid LIKE 'k2u-%'").fetchone()
        flag = c.execute("SELECT 1 FROM setting WHERE k='contact_fill'").fetchone()
    ok(f"Upgrade: je sichtbarem chat_contact genau ein Kontakt ({len(vis)} sichtbar, {len(fx)} Kontakte)",
       set(fx) == vis and len(fx) == len(vis) and per == 0 and nc[0] == nc[1] == len(vis), (sorted(vis - set(fx)), sorted(set(fx) - vis)))
    ok(f"Upgrade: ausgeblendete ({len(hid)}) ohne Kontakt", hid and not (hid & set(fx)))
    ok("Upgrade: Fed-Kontakte als kind fed, lokale als saga", all(kinds[v] == ("fed" if "@" in v else "saga") for _, v in fx))
    ok("Upgrade: Merker contact_fill gesetzt", bool(flag))
    ok("Upgrade: zweiter Start legt nichts an (Merker)", contacts.book_fill_once() is None)
    with connect() as c, c:
        n2 = contacts.book_fill(c)
    ok("Upgrade: Fuellen ohne Merker legt auch nichts Neues an (idempotent)", n2 == 0, n2)
    cleanup("k2u-")


def simulated_upgrade(A, B):
    """Bestand nachstellen: chat_contact ohne Adressbuch (wie vor Migration 38), Merker weg, Fuellen."""
    with connect() as c, c:
        now = time.time()
        for i, hid in ((3, 0), (4, 1)):
            c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,'',NULL,?,?,?)",
                      (A.uid, fern(i), now, now, hid))
        c.execute("DELETE FROM setting WHERE k='contact_fill'")
    n = contacts.book_fill_once()
    bk = book(A.uid)
    ok(f"Nachgestellt: Fuellen uebernimmt den sichtbaren, nicht den ausgeblendeten ({n})", fern(3) in bk and fern(4) not in bk
       and bk[fern(3)]["origin"] == "chat" and bk[fern(3)]["addrs"] == [("fed", fern(3), True)], bk)
    ok("Nachgestellt: zweiter Lauf nichts Neues", contacts.book_fill_once() is None)
    with connect() as c, c:
        c.execute("DELETE FROM chat_contact WHERE uid=? AND ref IN (?,?)", (A.uid, fern(3), fern(4)))
        c.execute("DELETE FROM contact WHERE id=?", (bk[fern(3)]["id"],))


def main():
    if sys.argv[1:] == ["seed"]:
        return seed()
    cleanup("k2-")
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("search_username", False)):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        check_upgrade()
        run()
    finally:
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup("k2-")
        cleanup("k2u-")
    print(f"\nKONTAKTE2-MODELL {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    mk = lambda: "k2-" + secrets.token_hex(6)                    # noqa: E731  Marker = Anmeldename
    A, B, Cc, D, E = (U(mk(), n) for n in ("Anke Buch", "Benno Buch", "Carla Buch", "Dirk Buch", "Ella Buch"))
    F = U(mk(), "Fiona Fremd", setup=False)
    names = [x.uid for x in (A, B, Cc, D, E, F)]
    simulated_upgrade(A, B)
    st = CT.Stream(A.k)
    time.sleep(0.5)

    # --- neuer Direkt-Chat -> Kontakt beidseitig -----------------------------------------------------------------------
    mark = st.mark()
    s, d = A.dm(B)
    ok(f"Direkt-Chat A-B ({s})", s == 200)
    ba, bb = book(A.uid), book(B.uid)
    ok("A hat B im Adressbuch (origin chat, feste Saga-Adresse, leer)", B.ref in ba and ba[B.ref]["origin"] == "chat"
       and ba[B.ref]["addrs"] == [("saga", B.ref, True)] and ba[B.ref]["own_name"] is None, ba)
    ok("B hat A im Adressbuch (beidseitig)", A.ref in bb and len(bb) == 1, bb)
    ev = st.wait("contacts.changed", lambda x: x.get("id") == ba[B.ref]["id"], since=mark)
    ok("Live contacts.changed {id} an A", ev == {"id": ba[B.ref]["id"]}, ev)
    kc = A.chat_contacts()
    ok("GET /api/chat/contacts: contact_id = Adressbuch-ID", kc[B.ref].get("contact_id") == ba[B.ref]["id"], kc)
    s, d = A.api("GET", "/api/chat/people?q=" + urllib.parse.quote("Benno"))
    hit = [u for u in d.get("users") or [] if u.get("username") == B.ref]
    ok("people: Kontakt-Treffer mit contact_id", hit and hit[0].get("contact") and hit[0].get("contact_id") == ba[B.ref]["id"], d)
    s, d = A.dm(B)
    ok("Gleicher Direkt-Chat noch einmal: kein zweiter Kontakt", len([k for k in book(A.uid) if k == B.ref]) == 1 and len(book(B.uid)) == 1)

    # --- freie Gruppe: alle gegenseitig ---------------------------------------------------------------------------------
    s, d = A.free(Cc, D)
    ok(f"Freie Gruppe A, C, D ({s})", s == 200)
    ok("C hat A und D, D hat A und C", set(book(Cc.uid)) == {A.ref, D.ref} and set(book(D.uid)) == {A.ref, Cc.ref})

    # --- alte DELETE: leerer Kontakt weg --------------------------------------------------------------------------------
    old_id = book(A.uid)[B.ref]["id"]
    mark = st.mark()
    s, d = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(B.ref))
    ok(f"Alte API: B aus Kontakten entfernen ({s})", s == 200 and B.ref not in A.chat_contacts())
    ok("... leerer Kontakt ist aus dem Adressbuch weg", B.ref not in book(A.uid))
    ok("... Live contacts.changed {id, deleted}", st.wait("contacts.changed", lambda x: x.get("id") == old_id and x.get("deleted"), since=mark))
    ok("... B behaelt A (andere Richtung unberuehrt)", A.ref in book(B.uid))
    # neuer Chat nach dem Loeschen -> neuer leerer Kontakt
    s, d = A.free(B, E)
    nb = book(A.uid).get(B.ref)
    ok(f"Neuer gemeinsamer Chat ({s}) -> neuer leerer Kontakt (neue ID)", nb and nb["id"] != old_id and nb["own_name"] is None
       and nb["addrs"] == [("saga", B.ref, True)] and B.ref in A.chat_contacts(), nb)

    # --- alte DELETE: Kontakt mit Nummer bleibt -------------------------------------------------------------------------
    add_number(A.uid, nb["id"], "+491701112233")
    s, d = A.api("DELETE", "/api/chat/contacts/" + urllib.parse.quote(B.ref))
    kb = book(A.uid).get(B.ref)
    ok(f"Alte API mit Nummer ({s}): raus aus Chat-Kontakten, bleibt im Adressbuch (mit Saga-Adresse)",
       s == 200 and B.ref not in A.chat_contacts() and kb and kb["id"] == nb["id"] and ("e164", "+491701112233", False) in kb["addrs"], kb)
    s, d = A.free(B, D)
    ok("Neuer Chat: wieder sichtbar, kein zweiter Kontakt", B.ref in A.chat_contacts() and book(A.uid)[B.ref]["id"] == nb["id"]
       and len([1 for v in book(A.uid).values() if ("saga", B.ref, True) in v["addrs"]]) == 1)

    # --- has(): das Adressbuch vergibt keine Rechte --------------------------------------------------------------------
    with connect() as c, c:
        now = time.time()
        cid = c.execute("INSERT INTO contact (uid, origin, own_name, created, updated) VALUES (?, 'manual', 'Fiona', ?, ?)",
                        (A.uid, now, now)).lastrowid
        c.execute("INSERT INTO contact_addr (contact_id, uid, kind, value, fixed, pos, created) VALUES (?,?,?,?,1,0,?)",
                  (cid, A.uid, "saga", F.ref, now))
    with connect() as c:
        ok("has(): Adressbuch-Eintrag mit Saga-Adresse macht niemanden zum Kontakt", not contacts.has(c, A.uid, F.uid)
           and contacts.has(c, A.uid, B.uid))
    ok("Eintrag ohne chat_contact erscheint nicht in /api/chat/contacts", F.ref not in A.chat_contacts())
    with connect() as c, c:
        c.execute("DELETE FROM contact WHERE id=?", (cid,))
    s, d = A.api("POST", "/api/contacts", {"own_name": "Fiona", "addrs": [{"kind": "saga", "value": F.ref}]})
    ok(f"POST /api/contacts mit Saga-Adresse -> 400 ({s})", s == 400 and F.ref not in json.dumps(d))

    # --- Weichen -------------------------------------------------------------------------------------------------------
    with connect() as c:
        bid = book(A.uid)[B.ref]["id"]
        ok("Weiche owned(): nur eigene IDs", contacts.owned(c, A.uid, [bid, book(B.uid)[A.ref]["id"], 10 ** 12]) == {bid})
        ok("Weiche contact_for(): Saga-Adresse und Nummer", contacts.contact_for(c, A.uid, "saga", B.ref) == bid
           and contacts.contact_for(c, A.uid, "e164", "+491701112233") == bid and contacts.contact_for(c, B.uid, "e164", "+491701112233") is None)
        ad = contacts.addresses(c, A.uid, bid)
        ok("Weiche addresses(): feste zuerst, fremde ID leer", ad[0]["fixed"] and ad[0]["kind"] == "saga" and len(ad) == 2
           and contacts.addresses(c, B.uid, bid) == [], ad)

    # --- Konto loeschen (beide Richtungen) ------------------------------------------------------------------------------
    from saga.userdata import USER_TABLES, delete_user_data
    ok("USER_TABLES enthaelt contact_addr und contact", "contact_addr" in USER_TABLES and "contact" in USER_TABLES)
    eid = book(A.uid)[E.ref]["id"]
    add_number(A.uid, eid, "+491709998877")
    did = book(A.uid)[D.ref]["id"]
    nd = len(book(D.uid))
    delete_user_data(D.uid, drop_user=True)
    ok(f"Konto D geloescht: Ds eigenes Adressbuch weg ({nd} vorher)", nd >= 2 and book(D.uid) == {})
    ok("... leerer Kontakt D bei A und C weg", D.ref not in book(A.uid) and D.ref not in book(Cc.uid)
       and did not in [v["id"] for v in book(A.uid).values()])
    delete_user_data(E.uid, drop_user=True)
    ke = [v for v in book(A.uid).values() if v["id"] == eid]
    ok("Konto E geloescht: Kontakt mit Nummer bleibt als manual ohne Saga-Adresse", ke and ke[0]["origin"] == "manual"
       and ke[0]["addrs"] == [("e164", "+491709998877", False)], ke)
    ok("... E nicht mehr in /api/chat/contacts", E.ref not in A.chat_contacts())

    # --- drop_host / drop_remote ----------------------------------------------------------------------------------------
    with connect() as c, c:
        now = time.time()
        for i in (5, 6, 7, 8):
            c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,'',NULL,?,?,0)",
                      (A.uid, fern(i), now, now))
        n = contacts.book_ensure(c, [(A.uid, fern(i)) for i in (5, 6, 7, 8)])
    ok(f"book_ensure: Fed-Kontakte angelegt ({n})", n == 4 and all(book(A.uid)[fern(i)]["addrs"] == [("fed", fern(i), True)] for i in (5, 6, 7, 8)))
    add_number(A.uid, book(A.uid)[fern(6)]["id"], "+491705550006")
    add_number(A.uid, book(A.uid)[fern(8)]["id"], "+491705550008")
    i6, i8 = book(A.uid)[fern(6)]["id"], book(A.uid)[fern(8)]["id"]
    with connect() as c, c:
        contacts.drop_remote(c, fern(5))
        contacts.drop_remote(c, fern(6))
    bk = book(A.uid)
    k6 = [v for v in bk.values() if v["id"] == i6]
    ok("drop_remote: leerer Fed-Kontakt weg, mit Nummer bleibt ohne Fed-Adresse", fern(5) not in bk and fern(6) not in bk and k6
       and k6[0]["origin"] == "manual" and k6[0]["addrs"] == [("e164", "+491705550006", False)], bk)
    with connect() as c, c:
        contacts.drop_host(c, FERN)
    bk = book(A.uid)
    k8 = [v for v in bk.values() if v["id"] == i8]
    ok("drop_host: leer weg, mit Nummer bleibt ohne Fed-Adresse", fern(7) not in bk and k8 and k8[0]["addrs"] == [("e164", "+491705550008", False)]
       and not any(FERN in v for v in bk))
    with connect() as c:
        ok("drop_host: kein chat_contact der Instanz", not c.execute("SELECT 1 FROM chat_contact WHERE ref LIKE ?", ("%@" + FERN,)).fetchone())

    # --- kein Anmeldename im Adressbuch ---------------------------------------------------------------------------------
    with connect() as c:
        txt = json.dumps([list(r) for r in c.execute("SELECT kind, value, type FROM contact_addr WHERE uid LIKE 'k2-%'")] +
                         [list(r) for r in c.execute("SELECT own_name, form, salutation FROM contact WHERE uid LIKE 'k2-%'")], default=str)
        own = [r[0] for r in c.execute("SELECT DISTINCT uid FROM contact_addr WHERE uid LIKE 'k2-%'")]
    leak = [u for u in names if u in txt]
    ok(f"Kein Anmeldename in contact_addr/contact (ausser der Besitzer-Spalte uid, {len(own)} Konten)", not leak, leak)
    # Chats v1 zeigen Mitgliedern Anmeldenamen fuer die Schluessel (gewollt, anmeldenamen_v3) - hier zaehlen die Kontakt-Endpunkte
    seen = [(w, src, body) for w, src, body in SEEN if "/api/chat/contacts" in src or "/api/chat/people" in src or "/api/contacts" in src]
    leak = [(src, u) for u in names for w, src, body in seen if u != w and u in body]
    ok(f"Kein Anmeldename anderer Konten in {len(seen)} Antworten der Kontakt-Endpunkte", seen and not leak, leak[:3])

    # --- Konto A loeschen: nichts bleibt -------------------------------------------------------------------------------
    st.close()
    delete_user_data(A.uid, drop_user=True)
    with connect() as c:
        ok("Konto A geloescht: keine contact/contact_addr-Zeilen mehr", not c.execute("SELECT 1 FROM contact WHERE uid=?", (A.uid,)).fetchone()
           and not c.execute("SELECT 1 FROM contact_addr WHERE uid=? OR value=?", (A.uid, A.ref)).fetchone())


if __name__ == "__main__":
    main()
