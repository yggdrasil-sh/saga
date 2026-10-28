"""Saga 2.5 P3 Stufe 2: gleichzeitig bearbeiten ueber WS /api/collab (saga/collab.py) gegen einen laufenden Server.

Braucht HK_TEST_BASE, SESSION_SECRET, BASE_URL, DATABASE_URL (Konten hk-test-a/b), Server mit kurzen Fristen
(COLLAB_MAT_DELAY=0.5 COLLAB_COMPACT_IDLE=3). Optional SAGA_RESTART_CMD: Shell-Befehl, der den Server neu startet
(fuer den Neustart mitten im Fuzzing; ohne: dieser Teil entfaellt mit Hinweis).

Geprueft: Schalter aus (WS 4403, features ohne live1, PATCH wie Stufe 1); Anmeldung (ohne -> 4401, fremder Origin ->
4400, Geraete-Schluessel); sub/sync/update/ack, Awareness (Name/Farbe vom Server, nie Anmeldename); Konvergenz-Fuzzing
mit 3 Clients (zufaellige Operationen, Verzoegerung, Umordnung, Duplikate, Server-Neustart) -> gleiche Zustaende, gleiche
Materialisierung, clean_blocks-fest; Ablehnung (Script-Tag/unerlaubtes Element, fremde Schaltflaechen-Signatur,
Uebergroesse, unbekannter Knotentyp, fremde client_id, Luecke); nur lesen (Rolle view, gesperrte Seite, anderes Schema);
Rechte-Entzug waehrend der Sitzung (bye); Titel-Leck (Verweis auf Seite ohne Zugriff: kein Titel im Zustand);
JSON-Schreiber waehrend Live (App-PATCH ohne merge, PATCH mit merge, Version wiederherstellen, blocks/move) kommen als
Update bei allen an; Verdichtung; Materialisierung (rev+1, note.saved). Wegwerf-Seiten; raeumt auf."""
import base64, http.client, json, os, random, subprocess, sys, time, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
from itsdangerous import TimestampSigner  # noqa: E402
from pycrdt import Doc, XmlElement, XmlFragment, XmlText  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from collab_client import Client  # noqa: E402
from saga import collab_model as M  # noqa: E402
from saga.pages import clean_blocks  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
WS = f"ws://{B.hostname}:{B.port}/api/collab"
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BB = "hk-test-a", "hk-test-b"
res = []
made = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, hdr=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=30)
    h = {"Cookie": "saga=" + ck(u), "X-Saga": "1", "Content-Type": "application/json"}
    h.update(hdr or {})
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); d = r.read(); c.close()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    return r.status, d


def live(on):
    auth.save_setting("notes", {"live": bool(on)})
    time.sleep(5.2)                      # Einstellungen sind 5 s zwischengespeichert


def page(blocks, title="Live-Test", u=A):
    st, d = req("POST", "/api/notes", u, {"title": title})
    nid = d["id"]; made.append((u, nid))
    st, d = req("PATCH", f"/api/notes/{nid}", u, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert st == 200, d
    return nid


def body_of(nid, u=A):
    st, d = req("GET", f"/api/notes/{nid}", u)
    return json.loads(d["body"])["blocks"] if st == 200 else None, d


def client(u=A, **kw):
    return Client(WS, {"Cookie": "saga=" + ck(u)}, **kw)


def subscribe(c, nid, **kw):
    c.sub(nid, **kw)
    h = c.wait("HELLO", nid)
    s2 = c.wait("SYNC2", nid)
    return (json.loads(h[2][0]) if h and h[0] == "HELLO" else None), s2


def frag(d):
    return d.get("prosemirror", type=XmlFragment)


def text_of(d, i):
    el = list(frag(d).children)[i]
    return next(x for x in el.children if isinstance(x, XmlText))


def wait_for(pred, t=6.0):
    end = time.time() + t
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:  # noqa: BLE001 - Zustand gerade im Umbau
            pass
        time.sleep(0.1)
    return False


def settle(cs, nid, t=4.0):
    """Warten, bis alle Clients denselben Zustand haben."""
    end = time.time() + t
    while time.time() < end:
        st = [json.dumps(M.read_pm(c.docs[nid]), sort_keys=True) for c in cs]
        if len(set(st)) == 1:
            return True
        time.sleep(0.1)
    return False


def same(a, b):
    return json.dumps(M._num(a), sort_keys=True) == json.dumps(M._num(b), sort_keys=True)


P = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}   # noqa: E731


def _put(d, pm_node, index=None):
    """PM-Knoten (Schema v1) als Block ins Doc einfuegen (wie ein Editor es taete)."""
    f = frag(d)
    M._put_node(f, pm_node, len(f.children) if index is None else index)

try:
    # --- Schalter aus -------------------------------------------------------------------------------------------------
    live(False)
    st, v = req("GET", "/api/version")
    ok("Schalter aus: features ohne live1", "live1" not in v["features"])
    c0 = client()
    m = c0.wait(("CLOSED", "HELLO"), timeout=5)
    ok(f"Schalter aus: WS schliesst mit 4403 ({m})", m and m[0] == "CLOSED" and m[2][0] == 4403)
    nid0 = page([P("a", "x")])
    st, d = req("PATCH", f"/api/notes/{nid0}", A, {"body": json.dumps({"blocks": [P("a", "y")]}), "format": "blocks", "rev": 0})
    ok("Schalter aus: PATCH mit alter rev ohne merge -> 409 wie bisher", st == 409)
    with connect() as c:
        ok("Schalter aus: kein CRDT-Zustand angelegt", not c.execute("SELECT 1 FROM note_crdt WHERE note_id=?", (nid0,)).fetchone())

    live(True)
    st, v = req("GET", "/api/version")
    ok("Schalter an: features mit live1", "live1" in v["features"])

    # --- Anmeldung -----------------------------------------------------------------------------------------------------
    c0 = Client(WS, {})
    m = c0.wait(("CLOSED", "HELLO"), timeout=5)
    ok("ohne Anmeldung -> 4401", m and m[0] == "CLOSED" and m[2][0] == 4401)
    c0 = Client(WS, {"Cookie": "saga=" + ck(A)}, origin="https://boese.example")
    m = c0.wait(("CLOSED", "HELLO"), timeout=5)
    ok("fremder Origin -> 4400", m and m[0] == "CLOSED" and m[2][0] == 4400)
    c0 = Client(WS, {"Cookie": "saga=" + ck(A)}, origin=os.environ["BASE_URL"])
    m = c0.wait("HELLO", 0, timeout=5)
    ok("eigener Origin -> hello", m is not None and m[0] == "HELLO" and json.loads(m[2][0])["schema"]["hash"] == M.SCHEMA_HASH)
    c0.close()

    # --- Grundablauf ---------------------------------------------------------------------------------------------------
    nid = page([P("a", "Hallo"), P("b", "Zweiter")], "Grund")
    a, b = client(A), client(A)
    ha, _ = subscribe(a, nid)
    hb, _ = subscribe(b, nid)
    ok("sub: hello mit Rolle owner, schreibbar, gen", ha and ha["role"] == "owner" and not ha["ro"] and ha["gen"])
    ok("sync2: Zustand = Inhalt der Seite", same(a.blocks(nid)[0]["blocks"], [P("a", "Hallo"), P("b", "Zweiter")]) and a.blocks(nid)[1] == "Grund")
    a.edit(nid, lambda d: text_of(d, 0).insert(5, " Welt"), "r1")
    cid_a = a.docs[nid].client_id             # Kennung, unter der a geschrieben hat
    m = a.wait(("ACK", "REJECT"), nid)
    ok("update: ack mit ref", m and m[0] == "ACK" and m[2][0] == b"r1")
    ok("update kommt beim anderen an", wait_for(lambda: b.blocks(nid)[0]["blocks"][0]["data"]["text"] == "Hallo Welt"))
    time.sleep(1.5)
    bl, d = body_of(nid)
    ok(f"Materialisierung: note.body (rev {d['rev']})", bl and bl[0]["data"]["text"] == "Hallo Welt" and d["rev"] >= 2)
    # Awareness
    a.send("AW", nid, {"cursor": {"x": 1}, "name": "Fake", "username": "hk-test-a"})
    m = b.wait("AW", nid)
    st_ = json.loads(m[2][0])["state"] if m else {}
    ok(f"Awareness: Name/Farbe vom Server, kein Anmeldename ({st_})", st_ and st_["name"] != "Fake" and "username" not in st_
       and st_["cursor"] == {"x": 1} and st_["color"] and "hk-test-a" not in json.dumps(st_))
    # Titel live
    a.edit(nid, lambda d: d.get("title", type=__import__("pycrdt").Text).insert(5, "!"))
    time.sleep(1.5)
    ok("Titel live -> note.title", body_of(nid)[1]["title"] == "Grund!")

    # --- Ablehnung -----------------------------------------------------------------------------------------------------
    def rejected(c, fn, what, code=None):
        c.edit(nid, fn, "x")
        m = c.wait(("ACK", "REJECT"), nid)
        info = json.loads(m[2][0]) if m and m[0] == "REJECT" else {}
        good = m and m[0] == "REJECT" and (code is None or info.get("code") == code)
        time.sleep(0.3)
        ok(f"reject: {what} ({info.get('code')})", good and same(c.blocks(nid)[0]["blocks"], b.blocks(nid)[0]["blocks"]))
    rejected(a, lambda d: frag(d).children.append(XmlElement("script", {"id": "evil"})), "unbekannter Knotentyp", "type")
    rejected(a, lambda d: list(frag(d).children)[0].attributes.__setitem__("onclick", "alert(1)"), "unbekanntes Attribut", "type")
    rejected(a, lambda d: text_of(d, 0).format(0, 2, {"script": {}}), "unbekannte Marke (Script)", "type")
    rejected(a, lambda d: _put(d, {"type": "paragraph", "attrs": {"id": "l1"}, "content": [
        {"type": "text", "text": "x", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}), "javascript:-Verweis", "sanitize")
    rejected(a, lambda d: _put(d, {"type": "buttons", "attrs": {"id": "bt", "data": {"buttons": [
        {"label": "x", "action": "db", "tpl": 5, "target": 6, "sig": "0" * 32}]}}}), "fremde Schaltflaechen-Signatur", "signature")
    rejected(a, lambda d: _put(d, {"type": "paragraph", "attrs": {"id": "big"}, "content": [{"type": "text", "text": "x" * 210000}]}),
             "Uebergroesse", "size")
    # Luecke: Update, das an einem nicht gesendeten haengt
    dd = Doc(); dd.apply_update(b.docs[nid].get_update()); s1 = dd.get_state(); text_of(dd, 1).insert(0, "1"); s2 = dd.get_state()
    text_of(dd, 1).insert(1, "2"); b.send("UPDATE", nid, dd.get_update(s2), b"z")
    m = b.wait(("ACK", "REJECT"), nid)
    ok("reject: Luecke (haengt an Unbekanntem) -> pending", m and m[0] == "REJECT" and json.loads(m[2][0])["code"] == "pending")
    b.docs[nid] = Doc(); b.docs[nid].apply_update(m[2][1])
    ok("nach reject: Zustand unveraendert, Seite unveraendert", body_of(nid)[0][0]["data"]["text"] == "Hallo Welt")

    # eigene Schaltflaeche mit Vorlage: Server signiert selbst (Korrektur-Update an alle)
    st, dbn = req("POST", "/api/notes", A, {"title": "DB", "kind": "database"}); made.append((A, dbn["id"]))
    st, tp = req("POST", "/api/notes", A, {"title": "Vorlage", "parent_id": dbn["id"], "template": True}); made.append((A, tp["id"]))
    a.edit(nid, lambda d: _put(d, {"type": "buttons", "attrs": {"id": "bt2", "data": {"buttons": [
        {"label": "Neu", "action": "db", "tpl": tp["id"], "target": dbn["id"]}]}}}), "s")
    m = a.wait(("ACK", "REJECT"), nid)
    time.sleep(0.5)
    bt = [x for x in b.blocks(nid)[0]["blocks"] if x["id"] == "bt2"]
    ok(f"eigene Vorlagen-Schaltflaeche: angenommen, Server-Signatur bei allen ({m and m[0]})",
       m and m[0] == "ACK" and bt and len(bt[0]["data"]["buttons"][0].get("sig") or "") == 32)

    # --- Nur lesen: Rolle view, Sperre, anderes Schema -------------------------------------------------------------------
    st, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": BB, "role": "view"})
    v = client(BB)
    hv, _ = subscribe(v, nid)
    ok("Rolle view: hello ro", hv and hv["ro"] and hv["role"] == "view")
    v.edit(nid, lambda d: text_of(d, 0).insert(0, "V"), "v")
    m = v.wait(("ACK", "REJECT"), nid)
    ok("Rolle view: Update abgelehnt (readonly)", m and m[0] == "REJECT" and json.loads(m[2][0])["code"] == "readonly")
    st, _ = req("PUT", f"/api/notes/{nid}/shares", A, {"type": "user", "target": BB, "role": "edit"})
    m = v.wait("HELLO", nid, timeout=8)
    ok("Freigabe auf edit hochgestuft -> hello mit ro false", m and not json.loads(m[2][0])["ro"])
    v.docs[nid] = Doc(); v.docs[nid].apply_update(a.docs[nid].get_update())
    v.edit(nid, lambda d: text_of(d, 1).insert(0, "B:"), "v2")
    m = v.wait(("ACK", "REJECT"), nid)
    ok("Rolle edit: Update angenommen", m and m[0] == "ACK")
    ok("... bei a angekommen", wait_for(lambda: same(a.blocks(nid)[0]["blocks"], v.blocks(nid)[0]["blocks"])))
    # fremde client_id: b (anderes Konto) schreibt unter der Kennung von a
    d2 = Doc(client_id=cid_a); d2.apply_update(v.docs[nid].get_update()); s0 = d2.get_state()
    text_of(d2, 1).insert(0, "Q"); v.send("UPDATE", nid, d2.get_update(s0), b"y")
    m = v.wait(("ACK", "REJECT"), nid)
    ok("reject: fremde client_id (Kennung eines anderen Kontos)", m and m[0] == "REJECT" and json.loads(m[2][0])["code"] == "client")
    v.docs[nid] = Doc(); v.docs[nid].apply_update(m[2][1])
    st, _ = req("PATCH", f"/api/notes/{nid}", A, {"locked": True})
    m = v.wait("HELLO", nid, timeout=8)
    ok("Seite gesperrt -> hello ro (locked)", m and json.loads(m[2][0])["ro"])
    v.edit(nid, lambda d: text_of(d, 1).insert(0, "Z"), "v3")
    m = v.wait(("ACK", "REJECT"), nid)
    ok("gesperrt: Update abgelehnt", m and m[0] == "REJECT")
    req("PATCH", f"/api/notes/{nid}", A, {"locked": False})
    sc = client(BB)
    hs, _ = subscribe(sc, nid, schema={"model": M.MODEL, "ver": 99, "hash": "x"})
    ok("anderes Schema (R1): nur lesen, why=schema", hs and hs["ro"] and hs["why"] == "schema")
    sc.close()
    # Rechte-Entzug
    st, _ = req("DELETE", f"/api/notes/{nid}/shares?type=user&target={BB}", A)
    if st != 200:
        st, _ = req("DELETE", f"/api/notes/{nid}/shares", A, {"type": "user", "target": BB})
    m = v.wait("BYE", nid, timeout=8)
    ok(f"Freigabe entzogen -> bye access ({st})", m and json.loads(m[2][0])["reason"] == "access")
    v.close()

    # --- Titel-Leck --------------------------------------------------------------------------------------------------------
    secret = page([P("s", "geheim")], "Geheimprojekt Zebra")
    leak = page([P("l", f'Siehe <a href="/notes#{secret}">Geheimprojekt Zebra</a> und mehr')], "Leck")
    req("PUT", f"/api/notes/{leak}/shares", A, {"type": "user", "target": BB, "role": "edit"})
    lb = client(BB)
    hl, s2 = subscribe(lb, leak)
    raw = s2[2][0] if s2 else b""
    ok("Titel-Leck: Zustand fuer Nicht-Eigentuemer ohne Titel der verdeckten Seite", raw and b"Zebra" not in raw and b"Geheim" not in raw)
    lb.edit(leak, lambda d: text_of(d, 0).insert(0, "B "), "l")
    lb.wait(("ACK", "REJECT"), leak)
    time.sleep(1.5)
    bl, _ = body_of(leak)
    ok("... Materialisierung behaelt den Verweistext (Eigentuemer-Sicht)", bl and 'Geheimprojekt Zebra</a>' in bl[0]["data"]["text"]
       and bl[0]["data"]["text"].startswith("B Siehe"))
    blb, _ = body_of(leak, BB)
    ok("... und GET fuer b maskiert weiter", blb and "Zebra" not in blb[0]["data"]["text"])
    lb.close()

    # --- JSON-Schreiber waehrend Live -----------------------------------------------------------------------------------
    jn = page([P("a", "Eins"), P("b", "Zwei"), P("c", "Drei")], "JSON")
    j1, j2 = client(A), client(A)
    subscribe(j1, jn); subscribe(j2, jn)
    cur = body_of(jn)[1]
    st, d = req("PATCH", f"/api/notes/{jn}", A, {"body": json.dumps({"blocks": [P("a", "Eins"), P("b", "Zwei (App)"), P("c", "Drei")]}),
                                                 "format": "blocks"}, hdr={"Authorization": ""})
    ok("App-PATCH ohne rev waehrend Live: 200", st == 200)
    ok("... kommt als Update bei allen an", wait_for(lambda: all(c.blocks(jn)[0]["blocks"][1]["data"]["text"] == "Zwei (App)" for c in (j1, j2))))
    # gleichzeitig: live in a, JSON-Merge in c
    j1.edit(jn, lambda d: text_of(d, 0).insert(4, " live"), "m")
    j1.wait("ACK", jn)
    r = body_of(jn)[1]["rev"]
    st, d = req("PATCH", f"/api/notes/{jn}", A, {"body": json.dumps({"blocks": [P("a", "Eins"), P("b", "Zwei (App)"), P("c", "Drei (merge)")]}),
                                                 "format": "blocks", "rev": r - 1 if r > 1 else r, "merge": 1})
    ok(f"PATCH mit merge waehrend Live: {st}", st == 200)
    want = ["Eins live", "Zwei (App)", "Drei (merge)"]
    ok("... Live-Text und Merge-Text beide erhalten (CRDT und body)",
       wait_for(lambda: all([x["data"]["text"] for x in c.blocks(jn)[0]["blocks"]] == want for c in (j1, j2)))
       and wait_for(lambda: [x["data"]["text"] for x in body_of(jn)[0]] == want))
    # Version wiederherstellen (vorher eine Version des jetzigen Stands sichern, dann weiterschreiben)
    req("POST", f"/api/notes/{jn}/versions/checkpoint", A, {"why": "test"})
    j2.edit(jn, lambda d: text_of(d, 2).insert(0, "spaeter "), "w")
    j2.wait("ACK", jn)
    wait_for(lambda: body_of(jn)[0][2]["data"]["text"].startswith("spaeter"))
    st, vs = req("GET", f"/api/notes/{jn}/versions")
    vs = vs if isinstance(vs, list) else []
    print(f"     {len(vs)} Versionen", flush=True)
    if st == 200 and vs:
        st, d = req("POST", f"/api/notes/{jn}/versions/{vs[-1]['id']}/restore")
        ok(f"Version wiederherstellen waehrend Live -> Update bei allen ({st})",
           st == 200 and wait_for(lambda: all(same(c.blocks(jn)[0]["blocks"], body_of(jn)[0]) for c in (j1, j2))))
    else:
        ok(f"Versionen vorhanden ({st})", False)
    # blocks/move nach einer anderen Seite
    tgt = page([P("t1", "Ziel")], "Ziel")
    r = body_of(jn)[1]["rev"]
    st, d = req("POST", f"/api/notes/{jn}/blocks/move", A, {"ids": [body_of(jn)[0][0]["id"]], "to": tgt, "rev_a": r})
    ok(f"blocks/move waehrend Live -> Block verschwindet bei allen ({st})",
       st == 200 and wait_for(lambda: all(same(c.blocks(jn)[0]["blocks"], body_of(jn)[0]) for c in (j1, j2)))
       and len(body_of(jn)[0]) == 2)
    j1.close(); j2.close()

    # --- Konvergenz-Fuzzing ------------------------------------------------------------------------------------------------
    rnd = random.Random(int(os.environ.get("SEED", "11")))
    fz = page([P(f"p{i}", f"Absatz {i}") for i in range(4)] + [
        {"id": "L", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [{"content": "eins", "meta": {}, "items": []}]}}], "Fuzz")
    cs = [client(A), client(A), client(A)]
    for c in cs:
        subscribe(c, fz)
    WORDS = ["Haus", "Baum", " ", "กิน", "\U0001F44D", "é", "x", "<b>", "&"]

    def rnd_op(d):
        f = frag(d)
        kids = list(f.children)
        k = rnd.random()
        if k < 0.55 and kids:
            el = rnd.choice([x for x in kids if x.tag in ("paragraph", "header")] or kids)
            ts = [x for x in el.children if isinstance(x, XmlText)]
            if not ts:
                return
            t = ts[0]
            s = "".join(x for x, _ in t.diff() if isinstance(x, str))     # Klartext (str(XmlText) waere XML)
            n = len(s.encode("utf-8"))
            pos = len(s[:rnd.randint(0, len(s))].encode("utf-8"))
            if rnd.random() < 0.7 or n == 0:
                t.insert(pos, rnd.choice(WORDS), rnd.choice([None, None, {"b": {}}, {"i": {}}, {"color": {"c": "red"}}]))
            else:
                i = rnd.randint(0, len(s) - 1)          # 1-3 Zeichen ab Zeichen i (Laenge in UTF-8-Bytes, pycrdt)
                p1 = len(s[:i].encode("utf-8")); p2 = p1 + len(s[i:i + rnd.randint(1, 3)].encode("utf-8"))
                if p2 > p1:
                    del t[p1:p2]
        elif k < 0.7:
            _put(d, {"type": "paragraph", "attrs": {"id": "n" + str(rnd.randint(0, 10 ** 9))}, "content": [{"type": "text", "text": "neu"}]},
                 rnd.randint(0, len(kids)))
        elif k < 0.8 and len(kids) > 2:
            del f.children[rnd.randint(0, len(kids) - 1)]
        elif k < 0.9 and kids:
            el = rnd.choice(kids)
            if el.tag == "paragraph":
                el.attributes["style"] = {"c": rnd.choice(["red", "blue", "green"])}
        elif kids:
            el = rnd.choice(kids)
            if el.tag == "header":
                el.attributes["level"] = rnd.randint(1, 4)

    restart = os.environ.get("SAGA_RESTART_CMD")
    rounds = 6
    for rd in range(rounds):
        for c in cs:
            c.hold = rnd.random() < 0.5             # verzoegert: fremde Updates sammeln
        for _ in range(12):
            c = rnd.choice(cs)
            c.edit(fz, rnd_op)
            time.sleep(rnd.random() * 0.03)
        time.sleep(0.6)
        for c in cs:
            if c.hold:
                c.hold = False
                n = len(c.held)
                order = list(range(n)); rnd.shuffle(order)
                c.release(fz, order=order, dup=rnd.random() < 0.5)       # umgeordnet, teils doppelt
            c.send("SYNC1", fz, c.docs[fz].get_state())                    # Abgleich (wie nach jeder Luecke)
        if restart and rd == rounds // 2:
            for c in cs:
                c.close()
            subprocess.run(restart, shell=True, check=False)
            for _ in range(60):
                try:
                    if req("GET", "/healthz")[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.5)
            old = cs
            cs = []
            for o in old:
                n_ = client(A); n_.docs[fz] = o.docs[fz]; n_.gen = getattr(o, "gen", None)
                n_.sub(fz, keep=True)
                n_.wait("SYNC2", fz)
                # eigene, beim Server evtl. nicht angekommene Aenderungen nachreichen
                n_.send("UPDATE", fz, n_.docs[fz].get_update())
                cs.append(n_)
        time.sleep(0.8)
    for c in cs:
        c.send("SYNC1", fz, c.docs[fz].get_state())
    conv = settle(cs, fz, 8)
    ok(f"Fuzzing: {len(cs)} Clients, {rounds} Runden ({'mit' if restart else 'OHNE'} Server-Neustart) -> gleiche Zustaende", conv)
    time.sleep(2.0)
    mat, _ = body_of(fz)
    mine = cs[0].blocks(fz)[0]["blocks"]
    ok("Fuzzing: Materialisierung = Zustand der Clients", mat is not None and same(mat, mine))
    ok("Fuzzing: Materialisierung clean_blocks-fest", same(json.loads(clean_blocks(json.dumps({"blocks": mat}))[0])["blocks"], mat))
    if not restart:
        print("     Hinweis: SAGA_RESTART_CMD fehlt - Neustart mitten im Fuzzing nicht geprueft", flush=True)
    for c in cs:
        c.close()

    # --- Verdichtung, Log ------------------------------------------------------------------------------------------------
    time.sleep(4.5)
    with connect() as c:
        n_log = c.execute("SELECT COUNT(*) FROM note_crdt_log WHERE note_id=?", (fz,)).fetchone()[0]
        st_ = c.execute("SELECT model, schema_ver, length(state), updated <= mat FROM note_crdt WHERE note_id=?", (fz,)).fetchone()
    ok(f"Verdichtung: Log leer nach Ruhe/Schliessen ({n_log}), Zustand mit Modell-Kennung {st_[:2]}",
       n_log == 0 and st_[0] == M.MODEL and st_[1] == M.SCHEMA_VER and st_[3])
    # Wiederoeffnen: gleicher Zustand (gen bleibt)
    r1 = client(A); h1, _ = subscribe(r1, fz)
    ok("Wiederoeffnen: Zustand aus note_crdt (gleiche gen, gleicher Inhalt)", same(r1.blocks(fz)[0]["blocks"], mat))
    r1.close()
finally:
    try:
        live(False)
    except Exception:  # noqa: BLE001
        pass
    for u, n in made:
        try:
            req("DELETE", f"/api/notes/{n}", u)
        except Exception:  # noqa: BLE001
            pass


print(f"{sum(res)}/{len(res)}", "ALLES GRUEN" if all(res) else "FEHLER")
sys.exit(0 if all(res) else 1)
