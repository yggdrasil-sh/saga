"""2.5 P3-E7: Live mitschreiben ueber Servergrenzen (collab1, saga/fed_collab.py) - zwei eigene Saga-Instanzen A
(Eigentuemer) und B (Heim), Geruest wie tests/foederation2.py, Clients wie tests/collab.py (pycrdt ueber WS /api/collab).

Anna (A) teilt eine Seite mit Bert (B, Bearbeiten) und Cleo (B, Bearbeiten); Olga (B) hat keinen Zugriff.
Geprueft:
- Faehigkeit collab1-<schema> nur mit notes.live an (beide Seiten), remote.caps der Seite traegt collab1.
- Bert (B) und Anna (A) schreiben gleichzeitig -> alle Zustaende gleich; Cleo (B, zweite Person auf dem Heim) sieht es;
  Awareness kommt in beide Richtungen an (Name/Farbe vom Server, kein Anmeldename).
- Materialisierter body auf A = erwarteter Inhalt.
- Rechte-Entzug auf A (Rolle Lesen -> nur lesen; Freigabe weg -> bye).
- Titel-Leck: Verweis auf eine Seite ohne Zugriff -> kein Titel in Rahmen an B.
- Verlorene Rueckmeldungen (FED_COLLAB_TEST_DROP) -> nach dem Abgleich (sync1) konsistent.
- Neustart von B waehrend der Sitzung; Neustart von A (Weiterleitung meldet sich neu an).
- Missbrauch: falsche Signatur, fremde Weiterleitung, fremde Seite, zu gross, Fluten.
- Schema-Abweichung (andere Faehigkeit) bzw. notes.live auf A aus -> kein collab1, Stufe 1 (merge1) funktioniert.
Ports: FEDLIVE_PORTS="a,a-admin,b,b-admin" (Standard 8650,8651,8652,8653). Raeumt auf (Datenbanken geloescht)."""
import base64, json, os, secrets, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
from collab_client import Client  # noqa: E402
from pycrdt import Doc, XmlFragment, XmlText  # noqa: E402
from saga import collab_model as M, fedsig  # noqa: E402

pa_, aa_, pb_, ab_ = (int(x) for x in os.environ.get("FEDLIVE_PORTS", "8650,8651,8652,8653").split(","))
T.I["a"].update(port=pa_, admin=aa_, db="saga_e7a", url=f"http://127.0.0.1:{pa_}", host=f"127.0.0.1:{pa_}", state="/tmp/st-e7a",
                allow=f"localhost:{pb_}")
T.I["b"].update(port=pb_, admin=ab_, db="saga_e7b", url=f"http://localhost:{pb_}", host=f"localhost:{pb_}", state="/tmp/st-e7b",
                allow=f"127.0.0.1:{pa_}")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
api, sql, wait = T.api, T.sql, T.wait
DROP = "/tmp/st-e7a/drop-live"
_env1 = T.env_of


def env_of(k):
    e = _env1(k)
    e.update(COLLAB_MAT_DELAY="0.5", COLLAB_COMPACT_IDLE="3", COLLAB_CHECK="3", FED_COLLAB_SYNC="2", FED_COLLAB_TTL="20")
    if k == "a":
        e["FED_COLLAB_TEST_DROP"] = DROP
    return e


T.env_of = F2.env_of = env_of


def ok(n, c, info=None):
    T.ok(n if c or info is None else f"{n} -> {str(info)[:400]}", c)


def ref(pid, rid):
    return -(rid * 1024 + pid)


def P(i, t):
    return {"id": i, "type": "paragraph", "data": {"text": t}}


def client(k, uid):
    return Client(f"ws://127.0.0.1:{T.I[k]['port']}/api/collab", {"Cookie": T.ck(k, uid), "Host": T.I[k]["host"]})


def subscribe(c, nid, **kw):
    c.sub(nid, **kw)
    h = c.wait(("HELLO", "ERROR", "BYE"), nid, timeout=15)
    if not h or h[0] != "HELLO":
        return (h[0] if h else None, json.loads(h[2][0]) if h and h[2] else None), None
    s2 = c.wait("SYNC2", nid, timeout=10)
    return json.loads(h[2][0]), s2


def frag(d):
    return d.get("prosemirror", type=XmlFragment)


def text_of(d, i):
    el = list(frag(d).children)[i]
    return next(x for x in el.children if isinstance(x, XmlText))


def pm(c, nid):
    with c.lock:
        d = Doc()
        d.apply_update(c.docs[nid].get_update())
    return M.read_pm(d)


def texts(c, nid):
    try:
        bl = c.blocks(nid)[0]["blocks"]
        return [b["data"].get("text") for b in bl]
    except Exception:  # noqa: BLE001
        return None


def _norm(c, n):
    """Zustandsvektor + Inhalt mit Seiten-IDs ohne Wert (A und B fuehren dieselben Eintraege, IDs je in eigener Form)."""
    import re
    with c.lock:
        sv = c.docs[n].get_state()
    from collab_client import rvu
    k, i = rvu(sv, 0)
    vec = []
    for _ in range(k):
        cid, i = rvu(sv, i)
        clk, i = rvu(sv, i)
        vec.append((cid, clk))
    sv = tuple(sorted(vec))
    txt = re.sub(r'"id": -?[0-9]+', '"id": 0', json.dumps(pm(c, n), sort_keys=True))
    return sv, re.sub(r"/notes#-?[0-9]+", "/notes#0", txt)


def settle(pairs, t=12.0):
    """pairs: [(client, nid)] - warten, bis alle denselben Stand haben (gleicher Zustandsvektor, gleicher Inhalt)."""
    end = time.time() + t
    while time.time() < end:
        try:
            st = {_norm(c, n) for c, n in pairs}
            if len(st) == 1:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def live_on(k, on):
    F2.setting(k, "notes", {"live": bool(on)})


def rehello(adm, pid):
    adm.post({"action": "refresh", "id": str(pid)})


def caps_of(k):
    return json.loads(sql(k, "SELECT caps FROM fed_peer")[0][0])


def body_a(nid):
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{nid}")
    return json.loads(d["body"])["blocks"] if s == 200 else None


def frames_all(c):
    """Alle bisher empfangenen Rohdaten (fuer Leck-Proben)."""
    out = []
    while True:
        try:
            out.append(c.inbox.get_nowait())
        except Exception:  # noqa: BLE001
            return out


def run():
    cl = []
    try:
        A, B, pa, pb = F2.main()
        # pa = ID von B bei A, pb = ID von A bei B
        F2.py("b", "import auth\nfor u, n in (('fb-cleo', 'Cleo Team'),):\n    auth.get_user(u) or auth.create_user(u, 'Fed-User-Pass-123', n)\n"
                   "from saga import publish\nprint(json.dumps(publish.set_name('fb-cleo', 'cleo')))")
        # --- Faehigkeit ---------------------------------------------------------------------------------------------
        cap = "collab1-" + M.SCHEMA_HASH
        ok("ohne notes.live: keine Faehigkeit collab1", cap not in caps_of("a") and cap not in caps_of("b"))
        live_on("a", True); live_on("b", True)
        time.sleep(5.3)
        rehello(A, pa); rehello(B, pb)
        ok("notes.live auf beiden: collab1 ausgehandelt", wait(lambda: cap in caps_of("a") and cap in caps_of("b"), 15))
        an, be, ce = "fa-anna", "fb-bert", "fb-cleo"
        nid = api("a", an, "POST", "/api/notes", {"title": "Live ueber Grenzen"})[1]["id"]
        api("a", an, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": json.dumps({"blocks": [P("b1", "Eins"), P("b2", "Zwei"), P("b3", "Drei")]})})
        for who in ("bert", "cleo"):
            s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@{who}@{HB}", "role": "edit"})
            ok(f"A: geteilt mit {who}@B ({s})", s == 200)
        ok("B: Freigaben angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 2, 20))
        r = ref(pa, nid)
        s, d = api("b", be, "GET", f"/api/notes/{r}")
        ok(f"B: Seite traegt caps collab1 ({(d.get('remote') or {}).get('caps')})", s == 200 and "collab1" in (d.get("remote") or {}).get("caps", []))

        # --- gleichzeitig schreiben -----------------------------------------------------------------------------------
        ca, cb, cc = client("a", an), client("b", be), client("b", ce)
        cl += [ca, cb, cc]
        ha, _ = subscribe(ca, nid)
        hb, s2b = subscribe(cb, r)
        hc, _ = subscribe(cc, r)
        ok(f"B: sub auf fremde Seite -> hello (live, bearbeiten) ({hb})", isinstance(hb, dict) and hb.get("role") == "edit" and not hb.get("ro"))
        ok("B: zweite Person auf dem Heim ebenfalls live", isinstance(hc, dict) and hc.get("role") == "edit")
        ok(f"B: Zustand wie beim Eigentuemer ({texts(cb, r)})", texts(cb, r) == ["Eins", "Zwei", "Drei"])
        ok("A/B: gleicher Zustandsvektor", settle([(ca, nid), (cb, r), (cc, r)]))
        for i in range(6):
            ca.edit(nid, lambda d, i=i: text_of(d, 0).insert(len(str(text_of(d, 0))), f" A{i}"))
            cb.edit(r, lambda d, i=i: text_of(d, 1).insert(0, f"B{i} "))
            cc.edit(r, lambda d, i=i: text_of(d, 2).insert(len(str(text_of(d, 2))), f" C{i}"))
            time.sleep(0.03)
        conv = settle([(ca, nid), (cb, r), (cc, r)], 15)
        ok(f"gleichzeitig A/B/B konvergiert ({texts(ca, nid)} | {texts(cb, r)})", conv, (texts(ca, nid), texts(cb, r), texts(cc, r)))
        t = texts(ca, nid) or ["", "", ""]
        ok("alle Eingaben erhalten", all(f"A{i}" in t[0] for i in range(6)) and all(f"B{i}" in t[1] for i in range(6))
           and all(f"C{i}" in t[2] for i in range(6)), t)
        # Awareness
        cb.send("AW", r, {"cursor": {"b": "b2"}})
        m = ca.wait("AW", nid, timeout=6)
        st = json.loads(m[2][0])["state"] if m else None
        ok(f"A: Awareness von Bert kommt an (Name vom Server) ({st})", isinstance(st, dict) and "Bert" in (st.get("name") or "") and st.get("cursor"))
        ok("A: kein Anmeldename in der Awareness", m and b"fb-bert" not in m[2][0])
        time.sleep(0.1)
        ca.send("AW", nid, {"cursor": {"b": "b1"}})
        m = cb.wait("AW", r, timeout=6, pred=lambda x: b"Anna" in x[2][0])
        ok(f"B: Awareness von Anna kommt an ({m and m[2][0][:120]})", bool(m) and b"fa-anna" not in m[2][0])
        m = cc.wait("AW", r, timeout=6, pred=lambda x: b"Bert" in x[2][0])
        ok("B: Cleo sieht Berts Awareness (gleiches Heim)", bool(m))
        # Materialisierung beim Eigentuemer
        exp = texts(ca, nid)
        ok("A: materialisierter body = Live-Stand", wait(lambda: [b["data"]["text"] for b in (body_a(nid) or [])] == exp, 10),
           (body_a(nid), exp))
        s, d = api("b", be, "GET", f"/api/notes/{r}")
        ok("B: GET (Stufe 1) zeigt denselben Stand", s == 200 and [b["data"]["text"] for b in json.loads(d["body"])["blocks"]] == exp)

        # --- missbraeuchliche Buendel -----------------------------------------------------------------------------------
        keys = F2.py("b", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))")
        privb, kid = fedsig.unb64u(keys), T.I["b"]["url"]
        actor = {"name": "bert", "display": "Bert Gast", "groups": []}

        def bundle(body, nid_=nid, act=actor, tamper=None):
            raw = json.dumps(body).encode()
            s, h, dd, *_ = T.signed("a", privb, kid, "POST", f"/fed/v2/notes/{nid_}/collab", body=raw, actor=act, tamper=tamper)
            return s, T.jd(dd)

        def flip(h):
            h["Signature"] = h["Signature"][:-6] + ("AAAAA=" if not h["Signature"].endswith("AAAAA=") else "BBBBB=")
        s, _ = bundle({"w": secrets.token_hex(16), "ops": [{"k": "s", "sv": base64.b64encode(b"\x00").decode()}]}, tamper=flip)
        ok(f"falsche Signatur -> 401 ({s})", s == 401)
        s, d = bundle({"w": secrets.token_hex(16), "ops": [{"k": "u", "u": base64.b64encode(b"\x00\x00").decode()}]})
        ok(f"unbekannte Weiterleitung ohne sub -> lost ({s}, {d})", s == 200 and d.get("lost") is True)
        secret = api("a", an, "POST", "/api/notes", {"title": "Geheimprojekt Zebra"})[1]["id"]
        s, d = bundle({"w": secrets.token_hex(16), "ops": [{"k": "sub", "schema": {"model": M.MODEL, "ver": M.SCHEMA_VER, "hash": M.SCHEMA_HASH}}]},
                      nid_=secret)
        ok(f"fremde Seite (ohne Freigabe) -> 404 ({s}, {d})", s == 404 and "sync2" not in d)
        s, d = bundle({"w": "x" * 40, "ops": [{"k": "s"}]})
        ok(f"kaputte Kennung -> 400 ({s})", s == 400)
        big = {"w": secrets.token_hex(16), "ops": [{"k": "u", "u": base64.b64encode(os.urandom(400 * 1024)).decode()}]}
        s, d = bundle(big)
        ok(f"zu gross -> 413 ({s})", s == 413)
        s, d = bundle({"w": secrets.token_hex(16), "ops": [{"k": "s"}] * 100})
        ok(f"zu viele Eintraege -> 400 ({s})", s == 400)
        # gueltige Anmeldung einer zweiten Weiterleitung, dann fremde Kennung (Olga) und Fluten
        w2 = secrets.token_hex(16)
        s, d = bundle({"w": w2, "ops": [{"k": "sub", "schema": {"model": M.MODEL, "ver": M.SCHEMA_VER, "hash": M.SCHEMA_HASH}}]})
        ok(f"eigene Anmeldung von Hand -> hello + sync2 ({s})", s == 200 and isinstance(d.get("hello"), dict) and d.get("sync2"))
        s, d = bundle({"w": w2, "ops": [{"k": "s", "sv": base64.b64encode(b"\x00").decode()}]}, act={"name": "olga", "display": "O", "groups": []})
        ok(f"Weiterleitung einer anderen Person -> 403 ({s})", s == 403)
        codes = [bundle({"w": w2, "ops": [{"k": "aw", "a": {"cursor": i}}]})[0] for i in range(40)]
        ok(f"Fluten -> 429 ({codes.count(429)} von 40)", 429 in codes)
        bad = Doc(); bad.get("evil", type=XmlFragment).children.append(XmlText("boese"))
        time.sleep(1.1)
        s, d = bundle({"w": w2, "ops": [{"k": "u", "u": base64.b64encode(bad.get_update()).decode(), "r": "x"}]})
        res = (d.get("res") or [{}])[0]
        ok(f"unerlaubte Wurzel -> abgelehnt (j) mit Vollzustand ({s}, {res})", s == 200 and res.get("j") and d.get("full"))
        bundle({"w": w2, "ops": [{"k": "x"}]})

        # --- Titel-Leck ------------------------------------------------------------------------------------------------
        frames_all(cb)
        ca.edit(nid, lambda d: text_of(d, 0).insert(0, "X"))
        api("a", an, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": json.dumps({"blocks": [
            P("b1", f'Siehe <a href="/notes#{secret}">Geheimprojekt Zebra</a>'), *body_a(nid)[1:]]}), "merge": 1,
            "rev": api("a", an, "GET", f"/api/notes/{nid}")[1]["rev"]})
        time.sleep(3)
        raw = b"".join(p for m in frames_all(cb) for p in (m[2] if isinstance(m[2], list) else []) if isinstance(p, bytes))
        ok(f"Titel-Leck: Verweis auf Seite ohne Zugriff ohne Titel in Rahmen an B ({len(raw)} Bytes)", raw and b"Zebra" not in raw and b"Geheim" not in raw)
        bl = cb.blocks(r)[0]["blocks"]
        ok(f"B: Verweis als negative ID ({bl[0]['data']['text'][:80]})", f"/notes#{ref(pa, secret)}" in bl[0]["data"]["text"])
        s, d = api("b", be, "GET", f"/api/notes/{r}")
        ok("B: GET maskiert weiter", s == 200 and "Zebra" not in d["body"])

        # --- verlorene Rueckmeldungen -> sync1 ----------------------------------------------------------------------------
        open(DROP, "w").close()
        for i in range(3):
            ca.edit(nid, lambda d, i=i: text_of(d, 2).insert(0, f"V{i} "))
            time.sleep(0.1)
        time.sleep(0.6)
        lost = texts(cb, r) != texts(ca, nid)
        os.remove(DROP)
        ok("Rueckmeldungen verworfen: B hinkt zunaechst hinterher", lost)
        ok("... nach dem periodischen Abgleich (sync1) konsistent", settle([(ca, nid), (cb, r), (cc, r)], 12), (texts(ca, nid), texts(cb, r)))

        # --- Rechte-Entzug ------------------------------------------------------------------------------------------------
        api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@cleo@{HB}", "role": "view"})
        m = cc.wait("HELLO", r, timeout=12, pred=lambda x: json.loads(x[2][0]).get("update"))
        ok(f"Rolle Lesen auf A -> Cleo nur lesen ({m and m[2][0]})", m and json.loads(m[2][0]).get("ro") is True)
        cc.edit(r, lambda d: text_of(d, 0).insert(0, "nein "), "ro1")
        m = cc.wait(("REJECT", "ACK"), r, timeout=8)
        ok(f"... Schreiben -> reject readonly ({m and m[0]})", m and m[0] == "REJECT" and json.loads(m[2][0])["code"] == "readonly")
        time.sleep(1)
        ok("... Eigentuemer hat nichts uebernommen", "nein" not in json.dumps(body_a(nid)))
        api("a", an, "DELETE", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@cleo@{HB}"})
        m = cc.wait("BYE", r, timeout=15)
        ok(f"Freigabe weg -> Cleo bekommt bye ({m and m[2][0]})", bool(m))
        time.sleep(0.5)
        cb.edit(r, lambda d: text_of(d, 1).insert(0, "noch "), "after")
        m = cb.wait(("ACK", "REJECT"), r, timeout=8)
        ok(f"Bert schreibt weiter ({m and m[0]})", m and m[0] == "ACK")

        # --- Neustart von B -----------------------------------------------------------------------------------------------
        for c in (cb, cc):
            c.close()
        T.procs["b"].terminate(); T.procs["b"].wait(15)
        ca.edit(nid, lambda d: text_of(d, 1).insert(0, "waehrendB "))
        F2.start("b")
        cb = client("b", be); cl.append(cb)
        hb, _ = subscribe(cb, r)
        ok(f"B nach Neustart: wieder live ({hb})", isinstance(hb, dict) and hb.get("role") == "edit")
        ok("... Stand inkl. Aenderung waehrend des Neustarts", settle([(ca, nid), (cb, r)], 10) and "waehrendB" in (texts(cb, r) or [""])[1])

        # --- Neustart von A ------------------------------------------------------------------------------------------------
        ca.close()
        T.procs["a"].terminate(); T.procs["a"].wait(15)
        F2.start("a")
        cb.edit(r, lambda d: text_of(d, 0).insert(0, "nachA "), "na")
        m = cb.wait(("ACK", "REJECT", "BYE"), r, timeout=15)
        ok(f"A neu gestartet: Weiterleitung meldet sich neu an, Update angenommen ({m and m[0]})", m and m[0] == "ACK")
        ca = client("a", an); cl.append(ca)
        subscribe(ca, nid)
        ok("... A und B wieder gleich", settle([(ca, nid), (cb, r)], 12) and "nachA" in (texts(ca, nid) or [""])[0],
           (texts(ca, nid), texts(cb, r), _norm(ca, nid)[0], _norm(cb, r)[0]))
        exp = texts(ca, nid)
        ok("A: materialisierter body = erwarteter Inhalt", wait(lambda: [b["data"]["text"].replace(f'<a href="/notes#{secret}">Geheimprojekt Zebra</a>', "")
                                                                    for b in (body_a(nid) or [])]
                                                                   == [x.replace(f'<a href="/notes#{secret}"></a>', "") for x in exp], 10),
           (body_a(nid), exp))

        # --- notes.live auf A aus -> Stufe 1 --------------------------------------------------------------------------------
        live_on("a", False)
        m = cb.wait(("BYE", "CLOSED"), r, timeout=20)
        ok(f"notes.live auf A aus -> B bekommt bye ({m and m[2][0]})", bool(m))
        rehello(A, pa)
        ok("... collab1 nicht mehr ausgehandelt", wait(lambda: cap not in caps_of("b"), 15))
        s, d = api("b", be, "GET", f"/api/notes/{r}")
        ok("... Seite ohne caps collab1", s == 200 and "collab1" not in (d.get("remote") or {}).get("caps", []))
        cb2 = client("b", be); cl.append(cb2)
        h, _ = subscribe(cb2, r)
        ok(f"... sub -> error federated (Stufe 1) ({h})", isinstance(h, tuple) and h[0] == "ERROR" and (h[1] or {}).get("code") == "federated")
        cur = d
        s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"format": "blocks", "rev": cur["rev"] - 1, "merge": 1,
                                                        "body": cur["body"].replace("Zwei", "Zwei (Stufe1)")})
        ok(f"... Stufe 1 (merge1) funktioniert weiter ({s})", s == 200 and "Stufe1" in json.dumps(body_a(nid)))
        # --- Schema-Abweichung: andere Kennung der Faehigkeit -> kein collab1 ------------------------------------------------
        live_on("a", True)
        time.sleep(5.3)
        rehello(A, pa)
        ok("live wieder an: collab1 ausgehandelt", wait(lambda: cap in caps_of("b"), 15))
        c0 = caps_of("b")
        sql("b", "UPDATE fed_peer SET caps=?, caps_at=? WHERE id=?",
            (json.dumps([x for x in c0 if x != cap] + ["collab1-0000000000000000"]), time.time(), pb))
        s, d = api("b", be, "GET", f"/api/notes/{r}")
        ok("Schema-Abweichung: Seite ohne caps collab1", s == 200 and "collab1" not in (d.get("remote") or {}).get("caps", []))
        h, _ = subscribe(client("b", be), r)
        ok("... sub -> federated (Stufe 1)", isinstance(h, tuple) and (h[1] or {}).get("code") == "federated")
        s, d = api("b", be, "PATCH", f"/api/notes/{r}", {"format": "blocks", "rev": d["rev"], "merge": 1, "body": d["body"].replace("Drei", "Drei!")})
        ok(f"... Stufe 1 funktioniert ({s})", s == 200)
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        for c in cl:
            c.close()
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        if not os.environ.get("FEDLIVE_KEEP"):
            for k in T.I:
                try:
                    T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
                except Exception as ex:  # noqa: BLE001
                    print("aufraeumen:", ex)
    res = T.res
    print(f"\nFOEDERATION-LIVE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
