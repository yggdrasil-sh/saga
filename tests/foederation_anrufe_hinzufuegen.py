"""Foederation 2.5 L8: Personen in einen laufenden Gruppenanruf holen (POST /api/calls/{id}/add) ueber Servergrenzen - DREI
eigene Saga-Instanzen A, B, C (eigene Datenbanken und Ports, wie tests/foederation_anrufe_raum.py). A ist mit B und mit C
verbunden, B und C nicht. Die Gruppe liegt auf A (Heim), A fuehrt den Anruf; Apps sind In-Process-Clients (PyNaCl).

Geprueft: Gruppe Anna (A) + Bert (B), Anna ruft an, Bert tritt bei (B). Anna holt
- Arne (Konto auf A, nicht im Chat): Mitglied, klingelt auf A;
- Berta (Konto auf B, Server schon im Anruf): Mitglied beim Heim, Einladung "ringing" beim fuehrenden Server, Antwort
  request (Nachrichtenanfrage); vorher klingelt nichts, nach Annahme der Anfrage klingelt es auf B (calls.ring_accepted),
  sie tritt bei;
- Dora (Konto auf C - C war bisher weder im Chat noch im Anruf): Mitglied beim Heim, C bekommt die Kopie des Anrufs
  (Teilnehmerliste mit ring), Dora sieht den Anruf und tritt nach Annahme bei (Angebote ueber A an Anna und Bert).
Kopie: Bert (B) wird Admin und holt Bodo (Konto auf B) - Mitglied ueber das Heim (A), Klingeln ueber den fuehrenden
Server; nimmt Bodo die Anfrage an, klingelt es und er tritt bei (5 Endpunkte). Bert (ohne Admin) vorher: 403. Keine Serverfehler. Ports ANRUF_ADD_FED_PORT (Standard 8743-8748).
Raeumt auf (Datenbanken geloescht)."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)
import chatlib as C  # noqa: E402

P0 = int(os.environ.get("ANRUF_ADD_FED_PORT", "8743"))
T.I["a"].update(port=P0, admin=P0 + 1, db="saga_fadd_a", url=f"http://127.0.0.1:{P0}", host=f"127.0.0.1:{P0}", state="/tmp/st-anruf-add/fed-a",
                allow=f"localhost:{P0 + 2}, localhost:{P0 + 4}")
T.I["b"].update(port=P0 + 2, admin=P0 + 3, db="saga_fadd_b", url=f"http://localhost:{P0 + 2}", host=f"localhost:{P0 + 2}",
                state="/tmp/st-anruf-add/fed-b", allow=f"127.0.0.1:{P0}")
T.I["c"] = dict(T.I["b"], port=P0 + 4, admin=P0 + 5, db="saga_fadd_c", url=f"http://localhost:{P0 + 4}", host=f"localhost:{P0 + 4}",
                state="/tmp/st-anruf-add/fed-c", allow=f"127.0.0.1:{P0}", secret=T.I["b"]["secret"][::-1])
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
ok, wait, sql = T.ok, T.wait, T.sql
api, User = FC.api, FC.User
CAPS = ["reneg", "want", "bye", "screen2", "multi"]


def caps(u):
    return api(u.k, u.uid, "PUT", "/api/calls/caps", {"device": u.dev["id"], "caps": CAPS})[0]


def body(t, n, a, b):
    s = ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=saga-fedadd\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\n"
         "a=mid:0\r\na=ice-ufrag:u" + str(n) + "\r\na=ice-pwd:xpwdx\r\n")
    return json.dumps({"t": t, "sdp": s, "fp": C.sdp_fingerprints(s), "n": n, "pc": ":".join(sorted([a["id"], b["id"]])), "caps": CAPS}, separators=(",", ":"))


def offers(cid, frm, targets):
    return {p.dev["id"]: C.seal_signal_v2(cid, frm.handle, frm.dev, p.handle, p.dev["id"], p.dev["enc_pk"], body("offer", 1, frm.dev, p.dev))
            for p in targets}


def got_offer(me, cid, frm):
    s, d = api(me.k, me.uid, "GET", f"/api/calls/{cid}?device={me.dev['id']}")
    for x in (d or {}).get("signals", []):
        if x["from_device"] == frm.dev["id"]:
            b, sg = C.open_signal_v2(x["sealed"], me.dev["enc_pk"], me.dev["enc_sk"])
            if C.verify_signal_v2(cid, frm.handle, frm.dev["id"], me.handle, me.dev["id"], b, sg, frm.dev["sign_pk"]):
                return json.loads(b)
    return None


def rung(k, n):
    """Hat Server k schon mehr als n-mal nach angenommener Chat-Anfrage geklingelt (Server-Log)?"""
    try:
        return open(os.path.join(T.I[k]["state"], "server.log"), encoding="utf-8", errors="replace").read().count("Chat-Anfrage angenommen - klingelt (ringing)") > n
    except OSError:
        return False


def act(u, cid):
    return next((x for x in api(u.k, u.uid, "GET", "/api/calls/active")[1].get("calls", []) if x["id"] == cid), None)


def connect(k1, k2, A1, A2):
    import re
    _, html = A1.post({"action": "invite"})
    code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
    fp = T.jd(T.hreq(k1, "GET", "/.well-known/saga")[2])["fingerprint"]
    A2.post({"action": "preview", "url": T.I[k1]["url"], "code": code})
    A2.post({"action": "connect", "url": T.I[k1]["url"], "code": code, "fingerprint": fp})
    p1 = sql(k1, "SELECT id FROM fed_peer WHERE host=?", (T.I[k2]["host"],))[0][0]
    _, html = A1.post({"action": "confirm", "id": str(p1)})
    ok(f"{k1.upper()} <-> {k2.upper()} verbunden", "bestätigt" in T.flash(html))


def has_cap(k, host, cap):
    r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
    return bool(r) and cap in (r[0][0] or "")


def setup():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna"), ("fa-arne", "Arne Neu", "arne")], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert"), ("fb-berta", "Berta Gast", "berta"),
                                                                ("fb-bodo", "Bodo Gast", "bodo")], groups=[]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("fc-dora", "Dora Dritt", "dora")], groups=[]))
    for k in T.I:
        F2.py(k, "import auth\nauth.save_setting('chat_enabled', True)\nauth.save_setting('share_group_scope', False)\n"
                 "auth.save_setting('calls', {'enabled': True, 'stun': 'stun:stun.example.org:3478', 'turn_urls': '', 'turn_mode': 'rest', "
                 "'turn_secret': '', 'turn_user': '', 'turn_password': '', 'turn_ttl': 43200, 'relay_only': False, 'ring_s': 45, 'horn': False})\nprint(1)")
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    connect("a", "b", adm["a"], adm["b"])
    connect("a", "c", adm["a"], adm["c"])


def accept(u, ref):
    cc = wait(lambda: u.chat(ref), 15)
    if cc:
        api(u.k, u.uid, "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
    return cc


def run():
    anna, arne = User("a", "fa-anna"), User("a", "fa-arne")
    bert, berta, bodo = User("b", "fb-bert"), User("b", "fb-berta"), User("b", "fb-bodo")
    dora = User("c", "fc-dora")
    for u in (anna, arne, bert, berta, bodo, dora):
        caps(u)
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": [], "handles": [bert.handle], "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Gruppe Anna + Bert (Heim A, v{ch.get('v')})", s == 200 and ch.get("v") == 2)
    accept(bert, ch["ref"])
    cid = C.hexid()
    s, d = api("a", "fa-anna", "POST", "/api/calls", {"id": cid, "chat_id": ch["id"], "media": "audio", "device": anna.dev["id"], "caps": CAPS})
    ok(f"A: Anna startet den Gruppenanruf (A fuehrt ihn) ({s}, can_add {d.get('call', {}).get('can_add')})", s == 200 and d["call"].get("can_add") is True)
    ok("B: Kopie, klingelt bei Bert", bool(wait(lambda: (act(bert, cid) or {}).get("invite") == "ringing", 8)))
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/join", {"device": bert.dev["id"], "role": "member", "caps": CAPS,
                                                                 "signals": offers(cid, bert, [anna])})
    ok(f"B: Bert tritt bei ({s})", s == 200 and len(d["call"]["legs"]) == 2)

    # --- Bert (kein Admin) auf der Kopie -----------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/add", {"uids": [bodo.handle]})
    ok(f"B: Bert (kein Admin) holt Bodo: 403 rights ({s}, {d.get('error')})", s == 403 and d.get("rights") is True)

    # --- lokal beim Heim ------------------------------------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/add", {"uids": [arne.handle]})
    ok(f"A: Anna holt Arne (Konto auf A): Mitglied + klingelt ({s}, {d.get('added')}, n={d.get('n')})", s == 200 and d.get("added") and d.get("n") == 1)
    ok("A: Arne sieht den Anruf (klingelt)", bool(wait(lambda: (act(arne, cid) or {}).get("invite") == "ringing", 5)))

    # --- Person auf B (Server schon im Anruf) -------------------------------------------------------------------------------
    t0 = time.time()
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/add", {"uids": [berta.handle]})
    inv = {x.get("handle") or x["who"]: x["state"] for x in d.get("invites") or []}
    ok(f"A: Anna holt Berta (Konto auf B): Mitglied, Einladung ringing, request ({s}, {d.get('added')}, {inv.get(berta.handle)}, "
       f"{d.get('request')}, {round((time.time() - t0) * 1000)} ms)",
       s == 200 and d.get("added") and inv.get(berta.handle) == "ringing" and d.get("request") == [berta.handle])
    time.sleep(1.0)
    ok("B: vor dem Annehmen der Chat-Anfrage klingelt nichts", not rung("b", 0))
    bt = accept(berta, ch["ref"])
    ok("B: Berta nimmt die Nachrichtenanfrage an", bool(bt))
    ok(f"B: dann klingelt es bei Berta (ring_accepted) ({(act(berta, cid) or {}).get('invite')})",
       bool(wait(lambda: rung("b", 0) and (act(berta, cid) or {}).get("invite") == "ringing", 8)))
    s, d = api("b", "fb-berta", "POST", f"/api/calls/{cid}/join", {"device": berta.dev["id"], "role": "member", "caps": CAPS,
                                                                  "signals": offers(cid, berta, [anna, bert])})
    ok(f"B: Berta tritt bei ({s}, {len((d.get('call') or {}).get('legs', []))} Endpunkte)", s == 200 and len(d["call"]["legs"]) == 3)
    ok("A: Annas Geraet bekommt Bertas Angebot (geprueft, Handle)", bool(wait(lambda: got_offer(anna, cid, berta), 5)))

    # --- Person auf C (Server bisher weder im Chat noch im Anruf) -------------------------------------------------------------
    t0 = time.time()
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/add", {"uids": [dora.handle]})
    ok(f"A: Anna holt Dora (Konto auf C, neuer Server): Mitglied ({s}, {d.get('added')}, {d.get('failed')})", s == 200 and d.get("added"))
    dc = accept(dora, ch["ref"])
    ok("C: Dora hat den Chat", bool(dc))
    cc = wait(lambda: act(dora, cid), 10)
    ok(f"C: Kopie des Anrufs auf C angelegt, Dora sieht ihn ({round((time.time() - t0) * 1000)} ms, invite {(cc or {}).get('invite')})", bool(cc))
    ok(f"C: es klingelt bei Dora nach dem Annehmen ({(act(dora, cid) or {}).get('invite')})",
       bool(wait(lambda: rung("c", 0) and (act(dora, cid) or {}).get("invite") == "ringing", 8)))
    s, d = api("c", "fc-dora", "POST", f"/api/calls/{cid}/join", {"device": dora.dev["id"], "role": "member", "caps": CAPS,
                                                                 "signals": offers(cid, dora, [anna, bert, berta])})
    ok(f"C: Dora tritt bei ({s}, {len((d.get('call') or {}).get('legs', []))} Endpunkte)", s == 200 and len(d["call"]["legs"]) == 4)
    ok("B: Berts Geraet bekommt Doras Angebot (C -> A -> B, B und C nicht verbunden)", bool(wait(lambda: got_offer(bert, cid, dora), 6)))

    # --- Kopie: Bert (B) wird Admin und holt Bodo (B) ---------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "PATCH", f"/api/chats/{ch['id']}/members/{bert.handle}", {"admin": True})
    ok(f"A: Bert wird Admin ({s})", s == 200)
    ok("B: Rolle auf der Kopie nachgezogen", bool(wait(lambda: (api("b", "fb-bert", "GET", f"/api/calls/{cid}")[1].get("call") or {}).get("can_add"), 8)))
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/add", {"uids": [bodo.handle]})
    ok(f"B: Bert holt Bodo (Konto auf B) ueber das Heim ({s}, {d.get('added')}, request {d.get('request')}, {d.get('error')})",
       s == 200 and d.get("added") == [bodo.handle] and d.get("request") == [bodo.handle])
    ok("A: Bodo beim Heim Mitglied", bool(wait(lambda: sql("a", "SELECT 1 FROM chat_member m JOIN chat c ON c.id=m.chat_id WHERE c.ref=? AND m.uid LIKE '@%' "
                                                                 "AND m.left_at IS NULL AND m.uid LIKE ?", (ch["ref"], "%" + bodo.handle.split("@")[0] + "%")), 5)))
    time.sleep(1.0)
    ok("B: vor Bodos Annahme klingelt nichts weiter", not rung("b", 1))
    accept(bodo, ch["ref"])
    ok(f"B: Bodo nimmt an - es klingelt ({(act(bodo, cid) or {}).get('invite')})",
       bool(wait(lambda: rung("b", 1) and (act(bodo, cid) or {}).get("invite") == "ringing", 8)))
    s, d = api("b", "fb-bodo", "POST", f"/api/calls/{cid}/join", {"device": bodo.dev["id"], "role": "member", "caps": CAPS,
                                                                 "signals": offers(cid, bodo, [anna, bert, berta, dora])})
    ok(f"B: Bodo tritt bei ({s}, {len((d.get('call') or {}).get('legs', []))} Endpunkte)", s == 200 and len(d["call"]["legs"]) == 5)
    api("a", "fa-anna", "POST", f"/api/calls/{cid}/end", {"reason": "hangup", "device": anna.dev["id"]})


def main():
    rc = 1
    try:
        setup()
        ok("Faehigkeit calls3 ausgehandelt (A-B, A-C)", wait(lambda: has_cap("a", HB, "calls3") and has_cap("b", HA, "calls3")
                                                           and has_cap("a", HC, "calls3") and has_cap("c", HA, "calls3"), 25))
        run()
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        if not os.environ.get("KEEP_DB"):
            for k in T.I:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
    print(f"\nFOEDERATION-ANRUFE-HINZUFUEGEN {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


if __name__ == "__main__":
    main()
