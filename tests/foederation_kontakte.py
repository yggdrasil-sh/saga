"""Kontakte ueber Servergrenzen (2.5, contacts1) mit drei eigenen Saga-Instanzen A, B, C (eigene Datenbanken und Ports).

Geprueft: Direkt-Chat A -> B (Person anderer Server als Kontakt, Kennung '~x@host', Herkunft, remote), Gegenseite erst nach
dem Annehmen (eine offene Nachrichtenanfrage macht niemanden zum Kontakt), freie Gruppe mit Personen anderer Server (alle
gegenseitig, auch die Kopie auf dem anderen Server), Hinzufuegen ueber das Heim, Kontakt bleibt nach "fuer alle loeschen",
people mischt den fremden Kontakt in remote ein (ohne Dubletten mit dem Adressbuch), kein Anmeldename; Instanz getrennt ->
deren Kontakte gone; Instanz entfernt -> geloescht. Raeumt auf (Datenbanken geloescht)."""
import json, os, re, sys, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)

T.I["a"].update(port=8621, admin=8620, db="saga_fka", url="http://127.0.0.1:8621", host="127.0.0.1:8621", state="/tmp/fedkon-a",
                allow="localhost:8623, localhost:8625")
T.I["b"].update(port=8623, admin=8622, db="saga_fkb", url="http://localhost:8623", host="localhost:8623", state="/tmp/fedkon-b", allow="127.0.0.1:8621")
T.I["c"] = dict(T.I["b"], port=8625, admin=8624, db="saga_fkc", url="http://localhost:8625", host="localhost:8625", state="/tmp/fedkon-c",
                allow="127.0.0.1:8621", secret=T.I["b"]["secret"][::-1])
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
ok, wait, sql = T.ok, T.wait, T.sql
api, User = FC.api, FC.User
SEEN = []


class FG:
    """Hilfen wie tests/foederation_gruppen.py (nicht importiert - das setzt beim Import eigene Ports)."""

    @staticmethod
    def connect(k1, k2, A1, A2):
        _, html = A1.post({"action": "invite"})
        code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
        fp = T.jd(T.hreq(k1, "GET", "/.well-known/saga")[2])["fingerprint"]
        A2.post({"action": "preview", "url": T.I[k1]["url"], "code": code})
        A2.post({"action": "connect", "url": T.I[k1]["url"], "code": code, "fingerprint": fp})
        p1 = sql(k1, "SELECT id FROM fed_peer WHERE host=?", (T.I[k2]["host"],))[0][0]
        _, html = A1.post({"action": "confirm", "id": str(p1)})
        ok(f"{k1.upper()} <-> {k2.upper()} verbunden", "bestätigt" in T.flash(html))
        return p1, sql(k2, "SELECT id FROM fed_peer WHERE host=?", (T.I[k1]["host"],))[0][0]

    @staticmethod
    def caps_ok(k, host, cap="chatgroups"):
        r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
        return bool(r) and cap in json.loads(r[0][0])


def kon(k, uid, q=""):
    s, d = api(k, uid, "GET", "/api/chat/contacts" + ("?q=" + urllib.parse.quote(q) if q else ""))
    SEEN.append((k, uid, json.dumps(d, ensure_ascii=False)))
    return {x["ref"]: x for x in (d.get("contacts") or [])} if s == 200 else None


def main():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("ka-anna", "Anna Berg", "anna"), ("ka-carl", "Carl Ohne", None)], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("kb-bert", "Bert Gast", "bert"), ("kb-cleo", "Cleo Team", "cleo")], groups=[]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("kc-dora", "Dora Dritt", "dora")], groups=[]))
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    pab, pba = FG.connect("a", "b", adm["a"], adm["b"])
    pac, pca = FG.connect("a", "c", adm["a"], adm["c"])
    return adm, pab, pba, pac, pca


def part(adm, pab, pba, pac, pca):
    ok("Faehigkeiten ausgehandelt", wait(lambda: FG.caps_ok("a", HB) and FG.caps_ok("a", HC) and FG.caps_ok("b", HA) and FG.caps_ok("c", HA), 25))
    anna, carl, bert, cleo, dora = User("a", "ka-anna"), User("a", "ka-carl"), User("b", "kb-bert"), User("b", "kb-cleo"), User("c", "kc-dora")
    s, d = api("a", "ka-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    dm = d.get("chat") or {}
    ok(f"A: Direkt-Chat mit Bert (B) ({s})", s == 200 and dm.get("v") == 2)
    k = kon("a", "ka-anna")
    kb = k.get(bert.handle) or {}
    ok(f"A: Bert als Kontakt (ref ~x@host, remote, host, nicht gone) ({kb})", kb.get("remote") is True and kb.get("host") == HB and not kb.get("gone")
       and kb.get("ready") is True and kb.get("sub") == HB)
    bc = wait(lambda: bert.chat(dm["ref"]), 15)
    ok("B: Nachrichtenanfrage - noch kein Kontakt", bool(bc) and bc.get("request") and kon("b", "kb-bert") == {})
    s, d = api("b", "kb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
    kb2 = kon("b", "kb-bert")
    ok(f"B: nach dem Annehmen ist Anna Kontakt ({s})", s == 200 and anna.handle in kb2 and kb2[anna.handle].get("host") == HA)

    # freie Gruppe ueber Servergrenzen: Heim A, Carl lokal, Cleo (B), Dora (C)
    s, d = api("a", "ka-anna", "GET", "/api/chat/people?q=Carl")
    carl_ref = ((d.get("users") or [{}])[0]).get("username", "")
    s, d = api("a", "ka-anna", "POST", "/api/chats", {"kind": "free", "members": [carl_ref], "handles": [cleo.handle], "device": anna.dev["id"]})
    gch = d.get("chat") or {}
    ok(f"A: Gruppe mit Carl und Cleo (B) ({s})", s == 200 and gch.get("v") == 2)
    ka = kon("a", "ka-anna")
    ok("A: Carl (lokal, Handle) und Cleo (B) als Kontakte", carl_ref in ka and cleo.handle in ka and not ka[carl_ref]["remote"])
    kcarl = kon("a", "ka-carl") or {}
    ok("A: Carl kennt Anna (lokal) und Cleo (B) - gegenseitig", cleo.handle in kcarl and any(not v["remote"] and v["name"] == "Anna Berg" for v in kcarl.values()))
    cc = wait(lambda: cleo.chat(gch["ref"]), 15)
    api("b", "kb-cleo", "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
    kc = wait(lambda: (lambda x: x if x and len(x) >= 2 else None)(kon("b", "kb-cleo")), 10) or {}
    ok(f"B: Cleo hat nach dem Annehmen Anna und Carl (Handles von A) ({sorted(kc)})", anna.handle in kc and carl.handle in kc
       and all(v.get("host") == HA for v in kc.values()))
    # Hinzufuegen ueber das Heim: Dora (C) kommt dazu; nach dem Annehmen kennt sie alle
    s, d = api("a", "ka-anna", "POST", f"/api/chats/{gch['id']}/members", {"handle": dora.handle})
    ok(f"A: Dora (C) hinzugefuegt ({s})", s == 200)
    ka = kon("a", "ka-anna")
    ok("A: Dora als Kontakt (Hinzufuegen)", dora.handle in ka and ka[dora.handle]["host"] == HC)
    dc = wait(lambda: dora.chat(gch["ref"]), 15)
    api("c", "kc-dora", "POST", f"/api/chats/{dc['id']}/request", {"action": "accept"})
    kd = wait(lambda: (lambda x: x if x and len(x) >= 3 else None)(kon("c", "kc-dora")), 10) or {}
    ok(f"C: Dora kennt Anna, Carl, Cleo ({len(kd)})", {anna.handle, carl.handle, cleo.handle} <= set(kd))
    ok("B: Cleo kennt jetzt auch Dora (chat.members vom Heim)", wait(lambda: dora.handle in (kon("b", "kb-cleo") or {}), 15))

    # Loeschen -> bleibt; people mischt ein
    s, d = api("a", "ka-anna", "DELETE", f"/api/chats/{dm['id']}?scope=all")
    ok(f"A: Direkt-Chat fuer alle geloescht ({s}), Bert bleibt Kontakt", s == 200 and bert.handle in kon("a", "ka-anna"))
    s, d = api("a", "ka-anna", "GET", "/api/chat/people?q=be")
    rm = [x for x in d.get("remote") or [] if x.get("handle") == bert.handle]
    ok(f"A: people (2 Zeichen) liefert Bert als Kontakt in remote, einmal ({len(rm)})", len(rm) == 1 and rm[0].get("contact") and rm[0].get("host") == HB)
    s, d = api("a", "ka-anna", "GET", "/api/chat/people?q=Bert")
    rm = [x for x in d.get("remote") or [] if x.get("handle") == bert.handle]
    ok(f"A: people (Adressbuch + Kontakt) ohne Dublette ({len(rm)})", len(rm) == 1)
    s, d = api("a", "ka-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ok(f"A: neuer Direkt-Chat ueber den Kontakt ({s})", s == 200 and (d.get("chat") or {}).get("v") == 2)

    # Trennen -> gone; Entfernen -> weg
    _, html = adm["a"].post({"action": "disconnect", "id": str(pac)})
    ok("A trennt C", "getrennt" in T.flash(html))
    kd = (kon("a", "ka-anna") or {}).get(dora.handle) or {}
    ok(f"A: Dora (getrennte Instanz) bleibt, aber gone ({kd.get('gone')}, ready {kd.get('ready')})", kd.get("gone") is True and kd.get("ready") is False)
    s, d = api("a", "ka-anna", "GET", "/api/chat/people?q=Dora")
    ok("A: getrennte Kontakte nicht in people", not any(x.get("handle") == dora.handle for x in d.get("remote") or []))
    ok("A: Bert (B, verbunden) nicht gone", not (kon("a", "ka-anna") or {}).get(bert.handle, {}).get("gone", True))
    adm["a"].post({"action": "remove", "id": str(pac)})
    ok("A: Instanz C entfernt -> Kontakte dort geloescht (alle Konten)", wait(lambda: not sql("a", "SELECT 1 FROM chat_contact WHERE ref LIKE ?", ("%@" + HC,)), 5)
       and dora.handle not in kon("a", "ka-anna"))
    # Datenschutz: keine Anmeldenamen in Kontakt-Antworten, Tabelle ohne Anmeldenamen
    names = ("ka-anna", "ka-carl", "kb-bert", "kb-cleo", "kc-dora")
    hits = [(k, u, n) for k, u, txt in SEEN for n in names if n != u and n in txt]
    ok(f"Kontakt-Antworten ohne Anmeldenamen anderer ({len(SEEN)} geprueft) {hits[:3] if hits else ''}", not hits)
    bad = [r for k in T.I for r in sql(k, "SELECT ref, name FROM chat_contact") if any(n in (r[0] + r[1]) for n in names)]
    ok("Tabellen ohne Anmeldenamen", not bad)
    ok("Kennungen sind Handles", all(re.fullmatch(r"~[a-z2-7]{16}(@.+)?", r[0]) for k in T.I for r in sql(k, "SELECT ref FROM chat_contact")))


def run():
    try:
        part(*main())
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\nFOEDERATION-KONTAKTE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
