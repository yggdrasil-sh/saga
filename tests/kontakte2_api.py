"""Adressbuch-API (2.6, Bugs-DB 2027a-2, saga/contact_book.py, Feature contacts2) - Vertrag docs/APP-API.md "Kontakte".

Geprueft: Feature contacts2 (/api/version, /api/chat/me), 401 ohne X-Saga, Anlegen/Lesen/Aendern/Loeschen, 404 fuer fremde
und unbekannte IDs, 409 bei veraltetem ver (mit aktuellem Kontakt), feste Saga-Adresse weder aenderbar noch loeschbar,
E.164-Faelle (+49 170 …, 0049 …, 0170 … mit/ohne Landesvorwahl aus Anfrage oder Konto-Einstellung, (0), zu kurz/lang,
Buchstaben), Mail-Faelle, doppelter Wert -> 409 nur mit contact_id, Grenzen (10 Nummern/Mails, 2000 manuelle), Suche (Name,
Mail, Ziffern), Name fuer mich nur im Adressbuch (nicht in Chats, nicht bei der Gegenseite), form bleibt nach Chat-Verkehr
null, DELETE neu blendet den Chat-Kontakt aus (alte Apps: chat.contacts), Live contacts.changed nur an eigene Verbindungen,
alte API unveraendert + contact_id, Status-Ausnahme {type: contact} weiter 400, kein fed_*-Modul liest das Adressbuch,
Marker-Scan (keine Anmeldenamen in Antworten; Nummern/Mails nie im Server-Log), 429 (lesen 60, schreiben 30 je Minute).

Server-Log: K2_LOG oder STATE_DIR/server.log. Wegwerf-Konten k2a-*; raeumt auf."""
import glob, json, os, re, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chat as CT  # noqa: E402
from saga.core import local_ref  # noqa: E402

res, SEEN = [], []
WAIT = 5.6
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.environ.get("K2_LOG") or os.path.join(os.environ.get("STATE_DIR", "/data"), "server.log")
SECRETS = []                                     # Nummern/Mails, die nie im Log stehen duerfen


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)


class U(CT.Person):
    def __init__(self, uid, name, setup=True):
        auth.create_user(uid, None, name)
        super().__init__(uid, setup)
        self.name = name

    def api(self, m, p, b=None, hdr=None):
        s, d = CT.req(m, p, self.k, b, hdr=hdr)
        SEEN.append((self.uid, f"{m} {p}", json.dumps(d, ensure_ascii=False) if not isinstance(d, (str, bytes)) else str(d)))
        return s, d

    def post(self, **b):
        return self.api("POST", "/api/contacts", b)

    def get(self, cid):
        return self.api("GET", f"/api/contacts/{cid}")

    def patch(self, cid, **b):
        return self.api("PATCH", f"/api/contacts/{cid}", b)

    def lst(self, q=""):
        s, d = self.api("GET", "/api/contacts" + ("?q=" + urllib.parse.quote(q) if q else ""))
        return d if s == 200 else None

    def chat_contacts(self):
        s, d = self.api("GET", "/api/chat/contacts")
        return {k["ref"]: k for k in d.get("contacts") or []} if s == 200 else None

    @property
    def ref(self):
        return local_ref(self.uid)


def num(v, **extra):
    return dict({"kind": "e164", "value": v}, **extra)


def mail(v, **extra):
    return dict({"kind": "email", "value": v}, **extra)


def vals(d, kind=None):
    return [a["value"] for a in (d or {}).get("addrs") or [] if kind is None or a["kind"] == kind]


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'k2a-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'k2a-%' OR created_by LIKE 'k2a-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'k2a-%'")
        c.execute("DELETE FROM contact WHERE uid LIKE 'k2a-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'k2a-%'")


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
    print(f"\nKONTAKTE2-API {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    mk = lambda: "k2a-" + secrets.token_hex(6)                    # noqa: E731  Marker = Anmeldename
    A, B, Cc = U(mk(), "Anton Adress"), U(mk(), "Berta Adress"), U(mk(), "Conrad Adress")
    E, M = U(mk(), "Erik Nummer", setup=False), U(mk(), "Mona Mail", setup=False)     # Pruefungen (eigene Rate je Konto)
    names = [A.uid, B.uid, Cc.uid, E.uid, M.uid]
    log0 = os.path.getsize(LOG) if os.path.exists(LOG) else 0

    s, d = CT.req("GET", "/api/version")
    ok("Feature contacts2 in /api/version", "contacts2" in (d.get("features") or []) and "contacts1" in d.get("features"))
    s, d = CT.req("GET", "/api/chat/me", A.k)
    ok("Feature contacts2 in /api/chat/me", "contacts2" in (d.get("features") or []), d.get("features"))
    s, d = CT.req("GET", "/api/contacts", A.k, hdr={"X-Saga": "0"})
    ok(f"Ohne X-Saga -> 401 ({s})", s == 401)
    d = A.lst()
    ok("Anfangs leer {contacts: [], total: 0}", d == {"contacts": [], "total": 0}, d)
    ok("Cache-Control: no-store", (CT.req.last_headers.get("cache-control") or "") == "no-store")

    # --- Anlegen --------------------------------------------------------------------------------------------------------
    sa, sb = CT.Stream(A.k), CT.Stream(B.k)
    time.sleep(0.5)
    ma, mb = sa.mark(), sb.mark()
    SECRETS.extend(["1701234567", "mara.muster", "Muster@"])
    s, m1 = A.post(own_name="  Mara   Muster ", form="formal", salutation="Sehr geehrte Frau Muster",
                   addrs=[num("+49 170 1234567", type="mobile"), mail("Mara.Muster@Example.ORG", type="work")])
    ok(f"POST -> 201 manual ({s})", s == 201 and m1.get("origin") == "manual" and m1.get("own_name") == "Mara Muster"
       and m1.get("name") == "Mara Muster" and m1.get("saga") is None and m1.get("ver") == 1 and m1.get("form") == "formal", m1)
    ok("Normalisiert: +491701234567, Domain klein, Typen", vals(m1) == ["+491701234567", "Mara.Muster@example.org"]
       and [a["type"] for a in m1["addrs"]] == ["mobile", "work"] and not any(a["fixed"] for a in m1["addrs"]), m1)
    ok("Felder des Kontakt-Objekts", set(m1) >= {"id", "ver", "origin", "name", "own_name", "saga", "form", "salutation", "addrs",
                                                  "last_at", "created", "updated"} and m1["last_at"] is None, sorted(m1))
    ok("Live contacts.changed {id} an A", sa.wait("contacts.changed", lambda x: x == {"id": m1["id"]}, since=ma))
    time.sleep(0.5)
    ok("... nicht an B", not any(e[0] == "contacts.changed" for e in sb.ev[mb:]), sb.ev[mb:])
    s, d = M.post(own_name="", addrs=[num("+491709990000")])
    ok(f"own_name Pflicht ({s})", s == 400)
    s, d = M.post(own_name="Ohne Adresse", addrs=[])
    ok(f"mindestens eine Adresse ({s})", s == 400)
    s, d = M.post(own_name="x" * 121, addrs=[num("+491709990000")])
    ok(f"own_name > 120 -> 400 ({s})", s == 400)
    s, d = M.post(own_name="Y", salutation="y" * 81, addrs=[num("+491709990000")])
    ok(f"salutation > 80 -> 400 ({s})", s == 400)
    s, d = M.post(own_name="Y", form="du", addrs=[num("+491709990000")])
    ok(f"form nur informal|formal ({s})", s == 400)
    s, d = M.post(own_name="Y", addrs=[num("+491709990000", type="fax")])
    ok(f"type nur mobile|home|work|other ({s})", s == 400)
    s, d = M.post(own_name="Y", addrs=[{"kind": "fed", "value": "~aaaaaaaaaaaaaaaa@x.example"}])
    ok(f"kind fed -> 400 ({s})", s == 400)

    # --- E.164 ----------------------------------------------------------------------------------------------------------
    def e164(v, cc=None, want=None):
        b = {"own_name": "Nummer", "addrs": [num(v)]}
        if cc is not None:
            b["cc"] = cc
        s, d = E.api("POST", "/api/contacts", b)
        if s == 201:
            E.api("DELETE", f"/api/contacts/{d['id']}")
        return s, (vals(d) or [None])[0] if s == 201 else d
    SECRETS.extend(["1702223333", "1703334444", "1704445555", "1705556666"])
    ok("'0049 170 2223333' -> +491702223333", e164("0049 170 2223333") == (201, "+491702223333"))
    s, d = e164("0170 3334444")
    ok(f"'0170 …' ohne Landesvorwahl -> 400 ({s}), Text ohne Nummer", s == 400 and "3334444" not in json.dumps(d), d)
    ok("'0170 …' mit cc in der Anfrage -> +49…", e164("0170/333-4444", cc="49") == (201, "+491703334444"))
    s, d = E.api("GET", "/api/contacts/settings")
    ok("Landesvorwahl anfangs null", s == 200 and d == {"cc": None}, d)
    s, d = E.api("PUT", "/api/contacts/settings", {"cc": "4x"})
    ok(f"Landesvorwahl ungueltig -> 400 ({s})", s == 400)
    s, d = E.api("PUT", "/api/contacts/settings", {"cc": "+49"})
    ok("Landesvorwahl +49 -> '49'", s == 200 and d == {"cc": "49"} and E.api("GET", "/api/contacts/settings")[1] == {"cc": "49"}, d)
    ok("'(0170) 444.5555' mit Konto-Einstellung -> +491704445555", e164("(0170) 444.5555") == (201, "+491704445555"))
    ok("'+49 (0) 170 5556666' -> +491705556666", e164("+49 (0) 170 5556666") == (201, "+491705556666"))
    ok("cc in der Anfrage schlaegt die Einstellung ('43')", e164("0660 1234567", cc="43") == (201, "+436601234567"))
    s, d = E.api("PUT", "/api/contacts/settings", {"cc": None})
    ok("Landesvorwahl entfernen", s == 200 and d == {"cc": None})
    for bad in ("+49 12", "+49 1234 5678 9012 345", "+49 170 ABC1234", "+0170 1234567", "", "++491701234567", "0"):
        s, d = e164(bad, cc="49" if bad == "0" else None)
        ok(f"Nummer {bad!r} -> 400 ({s})", s == 400)

    # --- E-Mail ---------------------------------------------------------------------------------------------------------
    def em(v):
        s, d = M.post(own_name="Mail", addrs=[mail(v)])
        if s == 201:
            M.api("DELETE", f"/api/contacts/{d['id']}")
        return s, (vals(d) or [None])[0] if s == 201 else d
    ok("Mail ' Ina@Beispiel.DE ' -> 'Ina@beispiel.de'", em(" Ina@Beispiel.DE ") == (201, "Ina@beispiel.de"))
    for bad in ("ohne-at", "a@b@c.de", "@x.de", "x@", "x y@z.de", "x@z..de", "x@" + "z" * 250 + ".de"):
        s, _ = em(bad)
        ok(f"Mail {bad[:30]!r} -> 400 ({s})", s == 400)

    # --- doppelter Wert, Grenzen ----------------------------------------------------------------------------------------
    s, d = A.post(own_name="Doppelt", addrs=[num("+491701234567")])
    ok(f"Doppelte Nummer -> 409 mit contact_id, ohne Wert ({s})", s == 409 and d.get("contact_id") == m1["id"] and "1234567" not in json.dumps(d)
       and set(d) == {"error", "contact_id"}, d)
    s, d = A.post(own_name="Doppelt", addrs=[mail("Mara.Muster@EXAMPLE.org")])
    ok(f"Gleiche Mail (Domain anders geschrieben) -> 409 ({s})", s == 409 and d.get("contact_id") == m1["id"])
    s, d = B.post(own_name="Bei B", addrs=[num("+491701234567")])
    ok(f"Gleiche Nummer bei einem anderen Konto erlaubt ({s})", s == 201)
    bm = d
    s, d = M.post(own_name="Viele", addrs=[num(f"+4917010000{i:02d}") for i in range(11)])
    ok(f"11 Nummern -> 400 ({s})", s == 400)
    s, d = M.post(own_name="Viele", addrs=[mail(f"m{i}@viel.example") for i in range(11)])
    ok(f"11 Mails -> 400 ({s})", s == 400)
    s, d = M.post(own_name="Zehn", addrs=[num(f"+4917010000{i:02d}") for i in range(10)] + [mail(f"m{i}@viel.example") for i in range(10)])
    ok(f"10 Nummern + 10 Mails erlaubt ({s})", s == 201 and len(d.get("addrs") or []) == 20)
    M.api("DELETE", f"/api/contacts/{d.get('id')}")
    with connect() as c, c:
        now = time.time()
        c.execute("INSERT INTO contact (uid, origin, own_name, created, updated) SELECT ?, 'manual', 'Masse', ?, ? FROM generate_series(1, 1999)",
                  (Cc.uid, now, now))
    s, d = Cc.post(own_name="Nummer 2000", addrs=[num("+491707770000")])
    s2, d2 = Cc.post(own_name="Nummer 2001", addrs=[num("+491707770001")])
    ok(f"Grenze 2000 manuelle Kontakte ({s}, {s2})", s == 201 and s2 == 400, d2)
    with connect() as c, c:
        c.execute("DELETE FROM contact WHERE uid=?", (Cc.uid,))

    # --- Lesen, 404 -----------------------------------------------------------------------------------------------------
    s, d = A.get(m1["id"])
    ok(f"GET /api/contacts/{{id}} ({s})", s == 200 and d == m1, d)
    for who, path, m in ((B, f"/api/contacts/{m1['id']}", "GET"), (B, f"/api/contacts/{m1['id']}", "PATCH"),
                         (B, f"/api/contacts/{m1['id']}", "DELETE"), (A, "/api/contacts/987654321", "GET"),
                         (A, f"/api/contacts/{bm['id']}", "GET")):
        s, d = who.api(m, path, {"ver": 1, "own_name": "X"} if m == "PATCH" else None)
        ok(f"{m} fremde/unbekannte ID -> 404 ({s})", s == 404 and "Mara" not in json.dumps(d))
    s, d = A.get(m1["id"])
    ok("... Kontakt unveraendert", d == m1)

    # --- Aendern, 409 ---------------------------------------------------------------------------------------------------
    mid = {a["value"]: a["id"] for a in m1["addrs"]}
    s, d = A.patch(m1["id"], own_name="Mara M.")
    ok(f"PATCH ohne ver -> 400 ({s})", s == 400)
    s, d = A.patch(m1["id"], ver=7, own_name="Mara M.")
    ok(f"PATCH mit altem ver -> 409 mit aktuellem Kontakt ({s})", s == 409 and (d.get("contact") or {}).get("ver") == 1 and d.get("error"), d)
    SECRETS.append("1706667777")
    s, m2 = A.patch(m1["id"], ver=1, own_name="Mara M.", form=None,
                    addrs=[mail("Mara.Muster@Example.Org", type="home"), num("0170 666 7777", type="work")], cc="49")
    ok(f"PATCH -> 200, ver 2 ({s})", s == 200 and m2.get("ver") == 2 and m2.get("own_name") == "Mara M." and m2.get("form") is None
       and m2.get("salutation") == "Sehr geehrte Frau Muster", m2)
    ok("addrs ersetzt: gleiche Mail behaelt id (Typ neu), alte Nummer weg, neue dazu",
       vals(m2) == ["Mara.Muster@example.org", "+491706667777"] and m2["addrs"][0]["id"] == mid["Mara.Muster@example.org"]
       and m2["addrs"][0]["type"] == "home" and "+491701234567" not in vals(m2), m2)
    s, d = A.patch(m1["id"], ver=2, own_name=None)
    ok(f"Manueller Kontakt: own_name nicht leeren ({s})", s == 400)
    s, d = A.patch(m1["id"], ver=2, addrs=[])
    ok(f"Manueller Kontakt: nicht ohne Adresse ({s})", s == 400)
    s, d = A.post(own_name="Wieder frei", addrs=[num("+491701234567")])
    ok(f"Entfernte Nummer ist wieder frei ({s})", s == 201)
    s, d3 = A.patch(d["id"], ver=1, addrs=[num("+491706667777")])
    ok(f"PATCH auf vergebene Nummer -> 409 ({s})", s == 409 and d3.get("contact_id") == m1["id"], d3)
    A.api("DELETE", f"/api/contacts/{d['id']}")

    # --- Kontakt aus einem Chat -----------------------------------------------------------------------------------------
    s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": B.ref})
    dm = (d.get("chat") or {}).get("id")
    ok(f"Direkt-Chat A-B ({s})", s == 200 and dm)
    lst = A.lst()
    kb = [x for x in lst["contacts"] if (x.get("saga") or {}).get("ref") == B.ref]
    ok("Kontakt aus dem Chat: origin chat, name live, saga-Objekt, feste Adresse", kb and kb[0]["origin"] == "chat" and kb[0]["name"] == "Berta Adress"
       and kb[0]["own_name"] is None and kb[0]["saga"]["remote"] is False and kb[0]["saga"]["ready"] is True
       and set(kb[0]["saga"]) == {"ref", "remote", "host", "sub", "avatar", "ready", "gone"}
       and kb[0]["addrs"] == [{"id": kb[0]["addrs"][0]["id"], "kind": "saga", "value": B.ref, "type": None, "fixed": True}]
       and kb[0]["last_at"], kb)
    ok("Zuletzt zuerst (Chat-Kontakt vor dem aelteren manuellen), total", lst["contacts"][0]["id"] == kb[0]["id"] and lst["total"] == len(lst["contacts"]) == 2, lst)
    k = kb[0]
    ok("contact_id in /api/chat/contacts", A.chat_contacts()[B.ref].get("contact_id") == k["id"])
    s, d = A.patch(k["id"], ver=k["ver"], addrs=[])
    ok(f"PATCH addrs [] laesst die feste Adresse ({s})", s == 200 and vals(d) == [B.ref], d)
    s, d = A.patch(k["id"], ver=d["ver"], addrs=[{"kind": "saga", "value": Cc.ref}])
    ok(f"Feste Adresse nicht aenderbar (andere Saga-Adresse -> 400) ({s})", s == 400 and Cc.ref not in json.dumps(d))
    k = A.get(k["id"])[1]
    SECRETS.append("1708889999")
    s, k = A.patch(k["id"], ver=k["ver"], own_name="Bertie", salutation="Liebe Berta",
                   addrs=k["addrs"] + [num("+49 170 888 9999", type="mobile")])
    ok(f"Chat-Kontakt: Name fuer mich, Grussform, Nummer (feste Adresse zurueckgeschickt) ({s})", s == 200 and k["name"] == "Bertie"
       and vals(k) == [B.ref, "+491708889999"] and k["addrs"][0]["fixed"], k)
    ok("Name fuer mich nicht in /api/chat/contacts", A.chat_contacts()[B.ref]["name"] == "Berta Adress")
    s, d = A.api("GET", "/api/chat/people?q=Bertie")
    ok("... und nicht in people", "Bertie" not in json.dumps(d))
    s, d, _ = B.send(dm, "Hallo du, wie geht's?")
    s2, d2, _ = A.send(dm, "Gut, danke!")
    kb2 = [x for x in B.lst()["contacts"] if (x.get("saga") or {}).get("ref") == A.ref]
    ok(f"Nach Chat-Verkehr ({s}, {s2}): form bleibt null (beide Seiten)", A.get(k["id"])[1]["form"] is None and kb2 and kb2[0]["form"] is None
       and kb2[0]["salutation"] is None, kb2)
    ok("Gegenseite B sieht nichts von As Feldern", not any(x in json.dumps(B.lst()) + json.dumps(B.chat_contacts())
                                                         for x in ("Bertie", "Liebe Berta", "1708889999")))
    s, d = A.api("GET", "/api/contacts/settings")
    ok("/api/contacts/settings kollidiert nicht mit /{id}", s == 200 and "cc" in d)

    # --- Suche ----------------------------------------------------------------------------------------------------------
    ids = lambda q: [x["id"] for x in A.lst(q)["contacts"]]  # noqa: E731
    ok("Suche: own_name (bertie)", ids("bertie") == [k["id"]])
    ok("Suche: Live-Name (berta adr)", ids("berta adr") == [k["id"]])
    ok("Suche: Mail (muster@ex)", ids("muster@ex") == [m1["id"]])
    ok("Suche: Ziffern (888 99)", ids("888 99") == [k["id"]])
    ok("Suche: national (0170 6667)", ids("0170 6667") == [m1["id"]])
    ok("Suche: nichts (zzz), total 0", A.lst("zzz") == {"contacts": [], "total": 0})
    s, d = A.api("GET", "/api/contacts?limit=1&offset=1")
    ok("limit/offset", s == 200 and [x["id"] for x in d["contacts"]] == [m1["id"]] and d["total"] == 2, d)

    # --- Loeschen (neu) -> alte Liste -----------------------------------------------------------------------------------
    ma = sa.mark()
    s, d = A.api("DELETE", f"/api/contacts/{k['id']}")
    ok(f"DELETE -> {{ok: true}} ({s})", s == 200 and d == {"ok": True})
    ok("... GET -> 404, nicht in der Liste", A.get(k["id"])[0] == 404 and k["id"] not in ids(""))
    ok("... B aus /api/chat/contacts (ausgeblendet)", B.ref not in A.chat_contacts())
    ok("... Live chat.contacts {ref, hidden} fuer alte Apps", sa.wait("chat.contacts", lambda x: x == {"ref": B.ref, "hidden": True}, since=ma))
    ok("... Live contacts.changed {id, deleted}", sa.wait("contacts.changed", lambda x: x == {"id": k["id"], "deleted": True}, since=ma))
    ok("... B behaelt A", A.ref in B.chat_contacts())
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [B.ref, Cc.ref]})
    kn = [x for x in A.lst()["contacts"] if (x.get("saga") or {}).get("ref") == B.ref]
    ok(f"Neuer gemeinsamer Chat ({s}) -> wieder da, leer (neue ID)", kn and kn[0]["id"] != k["id"] and kn[0]["own_name"] is None
       and vals(kn[0]) == [B.ref], kn)
    ok("Nummer des geloeschten Kontakts ist wieder frei", A.post(own_name="Neu", addrs=[num("+491708889999")])[0] == 201)
    sa.close()
    sb.close()

    # --- alte API unveraendert ------------------------------------------------------------------------------------------
    cc = A.chat_contacts()[B.ref]
    ok("Alte API: Felder wie 2.5 + contact_id", set(cc) == {"ref", "name", "sub", "avatar", "remote", "ready", "gone", "last_at", "contact_id"}, sorted(cc))

    # --- Status-Ausnahme contact weiter 400 -----------------------------------------------------------------------------
    s, d = A.api("PUT", "/api/account/status/prefs", {"exceptions": [{"type": "contact", "id": kn[0]["id"]}]})
    ok(f"Status-Ausnahme {{type: contact}} weiter 400 ({s})", s == 400)

    # --- Struktur: Foederation liest das Adressbuch nie ----------------------------------------------------------------
    rx = re.compile(r"contact_addr|contact_book|\bFROM contact\b|\bJOIN contact\b|\bINTO contact\b|\bUPDATE contact\b|book_export|book_restore")
    bad = [os.path.basename(p) for p in sorted(glob.glob(os.path.join(ROOT, "saga", "fed*.py"))) + [os.path.join(ROOT, "saga", "federation.py")]
           if rx.search(open(p, encoding="utf-8").read())]
    ok("Kein fed_*-Modul (und federation.py) liest contact/contact_addr/contact_book", not bad, bad)

    # --- Marker-Scan ----------------------------------------------------------------------------------------------------
    seen = [(w, src, body) for w, src, body in SEEN if "/api/chat/contacts" in src or "/api/chat/people" in src or "/api/contacts" in src]
    leak = [(src, u) for u in names for w, src, body in seen if u in body]
    ok(f"Kein Anmeldename in {len(seen)} Antworten der Kontakt-Endpunkte (auch nicht der eigene)", len(seen) > 50 and not leak, leak[:3])
    time.sleep(1)
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8", errors="replace") as f:
            f.seek(log0)
            txt = f.read()
        hits = [x for x in SECRETS + ["888%2099", "muster%40ex", "0170%206667"] if x.lower() in txt.lower()]
        ok(f"Nummern/Mails (auch Suchbegriffe) nie im Server-Log ({len(txt)} Zeichen neu)", not hits and "/api/contacts?***" in txt, hits)
    else:
        ok(f"Server-Log vorhanden ({LOG})", False)

    # --- 429 ------------------------------------------------------------------------------------------------------------
    st = [A.api("GET", "/api/contacts/settings")[0] for _ in range(62)]
    ok(f"Lesen: 429 nach 60 je Minute ({st.count(200)}x 200)", 429 in st and st[-1] == 429 and set(st) == {200, 429}, st[-5:])
    s, d = B.api("GET", "/api/contacts")
    ok("... anderes Konto nicht betroffen", s == 200)
    st = [B.api("PUT", "/api/contacts/settings", {"cc": "49"})[0] for _ in range(32)]
    ok(f"Schreiben: 429 nach 30 je Minute ({st.count(200)}x 200)", st[-1] == 429 and st.count(200) <= 30, st[-5:])
    s, d = A.api("GET", "/api/contacts/settings")
    ok("429-Text uebersetzbar, ohne Werte", s == 429 and d.get("error"))


if __name__ == "__main__":
    main()
