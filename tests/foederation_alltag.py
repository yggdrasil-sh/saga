"""Chat-Alltag ueber Servergrenzen (2.5, chatmention1) mit drei eigenen Saga-Instanzen A, B, C (eigene Datenbanken und Ports).

Geprueft: Faehigkeit chatmention1 ausgehandelt; freie Gruppe (Heim A) mit Personen von B und C: Erwaehnung vom Heim aus
(Bert auf B bekommt chat.mention, Cleo auf B chat.message), Erwaehnung von einem Teilnehmer-Server (B -> Heim A -> C:
Dora bekommt chat.mention, Anna chat.message), notify "mentions" auf einem anderen Server (nur die Erwaehnung meldet sich),
ausgeblendeter Gruppen-Chat kommt bei Erwaehnung von einem anderen Server zurueck; Missbrauch: Erwaehnung eines
Nicht-Mitglieds (Handle eines Kontos von C, das nicht in der Gruppe ist) wird auf allen Servern ignoriert, ein boesartiger
Teilnehmer-Server, der mentions mit fremden Handles in den Auftrag schreibt, bewirkt nichts (Heim prueft); Live-Ereignis
chat.message mit mentions (Handles); keine Anmeldenamen, kein Klartext. Raeumt auf (Datenbanken geloescht)."""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)

T.I["a"].update(port=8641, admin=8640, db="saga_fala", url="http://127.0.0.1:8641", host="127.0.0.1:8641", state="/tmp/fedall-a",
                allow="localhost:8643, localhost:8645")
T.I["b"].update(port=8643, admin=8642, db="saga_falb", url="http://localhost:8643", host="localhost:8643", state="/tmp/fedall-b", allow="127.0.0.1:8641")
T.I["c"] = dict(T.I["b"], port=8645, admin=8644, db="saga_falc", url="http://localhost:8645", host="localhost:8645", state="/tmp/fedall-c",
                allow="127.0.0.1:8641", secret=T.I["b"]["secret"][::-1])
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
wait, sql = T.wait, T.sql


def ok(n, c, info=""):
    T.ok(n + (f"  -- {str(info)[:400]}" if info and not c else ""), c)
api, User = FC.api, FC.User
SECRET = "Geheimtext Nordturm 42"


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


def caps_ok(k, host, cap):
    r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
    return bool(r) and cap in json.loads(r[0][0])


def notifs(k, uid, cid):
    return [r[0] for r in sql(k, "SELECT kind FROM notification WHERE uid=? AND data LIKE ? ORDER BY id", (uid, '{"chat": %d, %%' % cid))]


def clear(k, uid):
    sql(k, "DELETE FROM notification WHERE uid=?", (uid,))


def setup():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("la-anna", "Anna Berg", "anna")], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("lb-bert", "Bert Gast", "bert"), ("lb-cleo", "Cleo Team", "cleo")], groups=[]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("lc-dora", "Dora Dritt", "dora"), ("lc-emil", "Emil Aussen", "emil")], groups=[]))
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    connect("a", "b", adm["a"], adm["b"])
    connect("a", "c", adm["a"], adm["c"])


def part():
    ok("chatmention1 ausgehandelt (A<->B, A<->C)", wait(lambda: caps_ok("a", HB, "chatmention1") and caps_ok("a", HC, "chatmention1")
                                                         and caps_ok("b", HA, "chatmention1") and caps_ok("c", HA, "chatmention1"), 25))
    anna, bert, cleo, dora, emil = User("a", "la-anna"), User("b", "lb-bert"), User("b", "lb-cleo"), User("c", "lc-dora"), User("c", "lc-emil")
    s, d = api("a", "la-anna", "POST", "/api/chats", {"kind": "free", "handles": [bert.handle, cleo.handle, dora.handle], "device": anna.dev["id"]})
    g = d.get("chat") or {}
    ok(f"A: freie Gruppe mit Bert, Cleo (B) und Dora (C) ({s})", s == 200 and g.get("v") == 2, d)
    loc = {}
    for u, k in ((bert, "b"), (cleo, "b"), (dora, "c")):
        x = wait(lambda: u.chat(g["ref"]), 15)
        api(k, u.uid, "POST", f"/api/chats/{x['id']}/request", {"action": "accept"})
        loc[u.uid] = x["id"]
    time.sleep(1.5)
    ga = anna.chat(g["ref"])
    ok("alle haben angenommen", ga and ga.get("member_count") == 4)
    for k, u in (("a", "la-anna"), ("b", "lb-bert"), ("b", "lb-cleo"), ("c", "lc-dora")):
        clear(k, u)

    # 1) Erwaehnung vom Heim aus
    m = anna.msg(ga, "@Bert " + SECRET)
    m["mentions"] = [bert.handle]
    s, d = api("a", "la-anna", "POST", f"/api/chats/{ga['id']}/messages", m)
    ok(f"A sendet mit mentions [Bert] ({s})", s == 200, d)
    ok("B: Bert bekommt chat.mention", wait(lambda: notifs("b", "lb-bert", loc["lb-bert"]) == ["chat.mention"], 15), notifs("b", "lb-bert", loc["lb-bert"]))
    ok("B: Cleo bekommt chat.message", wait(lambda: notifs("b", "lb-cleo", loc["lb-cleo"]) == ["chat.message"], 10), notifs("b", "lb-cleo", loc["lb-cleo"]))
    ok("C: Dora bekommt chat.message", wait(lambda: notifs("c", "lc-dora", loc["lc-dora"]) == ["chat.message"], 10), notifs("c", "lc-dora", loc["lc-dora"]))
    ok("B: mention_seq bei Bert", wait(lambda: (bert.chat(g["ref"]) or {}).get("mention_seq", 0) > 0, 5))

    # 2) Teilnehmer B -> Heim A -> C; Dora hat notify "mentions" und den Chat ausgeblendet; Emil (C) ist kein Mitglied
    s, d = api("c", "lc-dora", "PATCH", f"/api/chats/{loc['lc-dora']}", {"notify": "mentions"})
    ok(f"C: Dora notify mentions ({s})", s == 200)
    s, d = api("c", "lc-dora", "PATCH", f"/api/chats/{loc['lc-dora']}", {"hidden": True})
    ok(f"C: freie Gruppe (verlassbar) laesst sich nicht ausblenden -> 400 ({s})", s == 400)
    # Rueckkehr bei Erwaehnung ueber Servergrenzen: Zustand wie bei einem ausgeblendeten Gruppen-/Seiten-Chat setzen
    sql("c", "INSERT INTO chat_pref (chat_id, uid, hide_at) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE SET hide_at=EXCLUDED.hide_at",
        (loc["lc-dora"], "lc-dora", time.time()))
    for k, u in (("a", "la-anna"), ("b", "lb-bert"), ("b", "lb-cleo"), ("c", "lc-dora"), ("c", "lc-emil")):
        clear(k, u)
    gb = bert.chat(g["ref"])
    m = bert.msg(gb, "kein Ping")
    s, d = api("b", "lb-bert", "POST", f"/api/chats/{gb['id']}/messages", m)
    time.sleep(2.5)
    ok(f"B -> A: ohne Erwaehnung - Dora (mentions, ausgeblendet) still ({s})", s == 200 and notifs("c", "lc-dora", loc["lc-dora"]) == [])
    ok("C: ausgeblendet bleibt ausgeblendet", (api("c", "lc-dora", "GET", f"/api/chats/{loc['lc-dora']}")[1].get("chat") or {}).get("hidden") is True)
    m = bert.msg(gb, "@Dora @Emil schaut mal")
    m["mentions"] = [dora.handle, emil.handle, bert.handle, "~aaaaaaaaaaaaaaaa@" + HC]
    s, d = api("b", "lb-bert", "POST", f"/api/chats/{gb['id']}/messages", m)
    ok(f"B sendet mit mentions [Dora, Emil (kein Mitglied), sich selbst, unbekannt] ({s})", s == 200, d)
    ok("C: Dora bekommt chat.mention trotz notify mentions + ausgeblendet", wait(lambda: notifs("c", "lc-dora", loc["lc-dora"]) == ["chat.mention"], 15),
       notifs("c", "lc-dora", loc["lc-dora"]))
    ok("C: Doras Chat kommt zurueck (nicht mehr ausgeblendet)", wait(lambda: (api("c", "lc-dora", "GET", f"/api/chats/{loc['lc-dora']}")[1].get("chat") or {})
                                                                 .get("hidden") is False, 5))
    ok("C: Emil (kein Mitglied) bekommt nichts", sql("c", "SELECT COUNT(*) FROM notification WHERE uid='lc-emil'")[0][0] == 0)
    ok("A: Anna bekommt chat.message (nicht erwaehnt)", wait(lambda: notifs("a", "la-anna", ga["id"]) == ["chat.message"], 10), notifs("a", "la-anna", ga["id"]))
    ok("B: Bert (Absender) bekommt nichts", notifs("b", "lb-bert", loc["lb-bert"]) == [])
    fan = [r[0] for r in sql("a", "SELECT payload FROM fed_outbox") if "mentions" in (r[0] or "")]
    ok("A (Heim) gibt nur Mitglieder-Handles weiter (kein Emil, kein Unbekannter)", all(emil.handle not in x and "~aaaaaaaaaaaaaaaa" not in x for x in fan))

    # 3) Missbrauch: boesartiger Teilnehmer-Server schreibt fremde Handles direkt in den Auftrag
    clear("c", "lc-emil")
    clear("c", "lc-dora")
    F2.py("b", f"""
from saga import federation as F, fed_chat as FC, chat
from db import connect
import json
with connect() as c:
    ch = FC.by_ref(c, {g['ref']!r})
    p = F.peer_by(c, "host", {HA!r})
print(json.dumps(FC.sync_call(p, {{"op": "send", "ref": ch["ref"], "msg": {{"id": "0" * 32}}, "mentions": [{emil.handle!r}]}})[0]))
""")
    time.sleep(1)
    ok("Auftrag mit kaputter Nachricht + fremden mentions: Emil bekommt nichts", sql("c", "SELECT COUNT(*) FROM notification WHERE uid='lc-emil'")[0][0] == 0)

    # 4) Datenschutz
    names = ("la-anna", "lb-bert", "lb-cleo", "lc-dora", "lc-emil")
    ev = []
    for k in T.I:
        ev += [r[0] for r in sql(k, "SELECT payload FROM fed_outbox")]
    ok("Foederations-Ereignisse ohne Anmeldenamen und Klartext", not any(n in (x or "") for x in ev for n in names) and not any(SECRET in (x or "") for x in ev))
    hits = [k for k in T.I if sql(k, "SELECT COUNT(*) FROM chat_msg WHERE strpos(body_ct, ?) > 0", (SECRET,))[0][0]]
    ok("kein Klartext in chat_msg", not hits)


def run():
    try:
        setup()
        part()
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
    print(f"\nFOEDERATION-ALLTAG {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
