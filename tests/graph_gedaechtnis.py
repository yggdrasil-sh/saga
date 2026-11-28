"""Graph + Gedaechtnis (2.6, Bugs-DB 2595, docs/MEMORY.md 10): GET /api/graph liefert mit gebundenem Gedaechtnis
mem {db, now, rows: [[id, hits, last_hit]]} - nur Zeilen der EIGENEN gebundenen Datenbank, live aus mem_vec (nicht aus der
10-min-Spiegelung); ohne Bindung (oder Datenbank im Papierkorb) kein Feld; freigegebene Seiten anderer nie mit Zaehlern.
recall (REST und MCP) meldet memory.recalled {ids} nur an die Verbindungen der Besitzerin (alle Sitzungen), ohne Titel/
Inhalte; kein Ereignis ohne Treffer. Leistung: Graph mit 5 000 Knoten und 5 000 Gedaechtnis-Zeilen in einer Abfrage.
Wegwerf-Konten gm-*, erfundene Testdaten; raeumt auf. Embeddings aus (Rueckfall Trigramme).
Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> graph_gedaechtnis.py"""
import base64, http.client, json, os, secrets, sys, threading, time, urllib.parse

import db

db.wait_ready()
import auth  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from saga import api_struct, api_v1, apitok, memory  # noqa: E402
from saga.core import _tok_hash  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, B = "gm-anna", "gm-bert"
res = []
PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Abrufe", "type": "number"}, {"name": "Letzter Abruf", "type": "date"},
         {"name": "Art", "type": "select", "options": ["Fakt", "Schnappschuss"]}]


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, cookie=None, body=None, bearer=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer; h["MCP-Protocol-Version"] = "2025-06-18"
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); d = r.read()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    c.close()
    return r.status, d


def pg(sql, args=()):
    with db.connect() as c, c:
        return c.execute(sql, args).fetchall()


def token(uid, scopes):
    raw = apitok.PREFIX + secrets.token_urlsafe(32)
    pg("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires) VALUES (?,?,?,?,?,?,?,?)",
       (uid, "gm-test", _tok_hash(raw), raw[:12], json.dumps(apitok.norm_scopes(scopes)), None, time.time(), time.time() + 3600))
    return raw


def tok_obj(uid):
    return apitok.Tok(10 ** 9 + 25950, uid, "gm", "sga_gm", apitok.SCOPES, None, None)


class Stream:
    """Live-Kanal /api/events in einem Thread (wie tests/ki_persona.py)."""

    def __init__(self, cookie):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=120)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
        self.r = self.c.getresponse()
        self.ev = []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "event" in cur:
                        self.ev.append((cur["event"], json.loads(cur.get("data") or "null")))
                    cur = {}
                elif not line.startswith(":"):
                    k, _, v = line.partition(":")
                    cur[k] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001
            pass

    def wait(self, typ, since=0, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ:
                    return e[1]
            time.sleep(0.05)
        return None

    def all(self, typ):
        return [e[1] for e in self.ev if e[0] == typ]

    def close(self):
        try:
            self.c.sock.close()
        except Exception:  # noqa: BLE001
            pass


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, B):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)


def mkdb(uid, title, rows):
    tk = tok_obj(uid)
    dbid = api_struct.database_create(tk, {"title": title, "properties": PROPS})[1]["id"]
    ids = {}
    for t, text in rows:
        ids[t] = api_v1.row_create(tk, dbid, {"title": t, "properties": {"Inhalt": text}})[1]["id"]
    return dbid, ids


def graph(cookie):
    st, d = req("GET", "/api/graph", cookie)
    return d if st == 200 else {"_status": st, "_d": d}


def mrows(d):
    return {r[0]: r for r in ((d.get("mem") or {}).get("rows") or [])}


def part_shadow(CA, s1, mem, ids, other, ta):
    """2.6 (Bugs-DB 2601/2603, docs/MEMORY.md 10.4): core, snap, shadow, links, links_total, hits24, Ereignis mit schatten."""
    tk = tok_obj(A)
    core = api_v1.page_create(tk, {"title": "Ich-Kern (Graph-Test)", "markdown": "Kern"})[1]["id"]
    req("PUT", "/api/account/memory", CA, {"core": core})
    snap = memory.nap(A, {"stimmung": "ruhig", "gedanke": "Graph-Test Schnappschuss."}, tk)[1]["id"]
    m = graph(CA).get("mem") or {}
    ok("Graph: core = Ich-Kern (Knoten), snap = Schnappschuss-Zeile", m.get("core") == core and m.get("snap") == [snap], m)
    st, r = req("PUT", "/api/account/memory", CA, {"shadow": True})
    ok("Schattenmodus an", st == 200 and r["shadow"]["on"], (st, r))
    for _ in range(40):
        if not req("GET", "/api/account/memory", CA)[1]["shadow"]["running"]:
            break
        time.sleep(0.25)
    now = time.time()
    pg("DELETE FROM mem_link WHERE uid=?", (A,))
    pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft, gepinnt) VALUES "
       "(?,?,?,1.0,0.05,0,?,?,'hand',TRUE)", (*sorted((ids["Tomaten"], ids["Sauerteig"])), A, now, now))
    pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) VALUES (?,?,?,0.6,0.05,0,?,?,'gemeinsam')",
       (*sorted((ids["Tomaten"], other)), A, now - 60 * 86400, now))
    pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) VALUES (?,?,?,0.4,0.05,0,?,?,'aehnlich')",
       (*sorted((ids["Tomaten"], 9 * 10 ** 15)), A, now, now))
    pg("INSERT INTO mem_trace (uid, at, quelle, frage_hash, lim, neu) VALUES (?, ?, 'trigramm', 'h', 5, CAST(? AS JSONB))",
       (A, now - 3600, json.dumps([ids["Zugfahrt"]])))
    pg("INSERT INTO mem_trace (uid, at, quelle, frage_hash, lim, neu) VALUES (?, ?, 'trigramm', 'h', 5, CAST(? AS JSONB))",
       (A, now - 30 * 3600, json.dumps([ids["Zugfahrt"]])))
    m = graph(CA).get("mem") or {}
    L = m.get("links") or []
    t1, t2 = sorted((ids["Tomaten"], ids["Sauerteig"])), sorted((ids["Tomaten"], other))
    ok("Graph an: links nur zwischen Knoten, nach (a, b), [a, b, w_eff 3 Stellen, Herkunft, gepinnt], links_total",
       m.get("shadow") is True and [x[:2] for x in L] == sorted([t1, t2]) and m.get("links_total") == 2
       and [t1 + [1.0, "h", 1]] == [x for x in L if x[:2] == t1] and [t2 + [0.325, "g", 0]] == [x for x in L if x[:2] == t2], m)
    ok("Graph an: hits24 nur letzte 24 h ([id, n, last])", m.get("hits24") == [[ids["Zugfahrt"], 1, int(now - 3600)]], m.get("hits24"))
    n1 = len(s1.ev)
    st, r = req("POST", "/api/v1/memory/recall", body={"text": "Tomaten Sonne Wasser"}, bearer=ta)
    e = s1.wait("memory.recalled", since=n1)
    ok("Ereignis memory.recalled mit schatten (nur neu: Sauerteig ueber den gepinnten Link)", st == 200 and e and ids["Tomaten"] in e["ids"]
       and e.get("schatten") == [ids["Sauerteig"]] and "schatten" in r["ms"] and ids["Sauerteig"] not in [x["id"] for x in r["treffer"]], (e, r))
    h = {x[0]: x for x in (graph(CA).get("mem") or {}).get("hits24") or []}
    ok("Graph: Schatten-Treffer der Spur in hits24", ids["Sauerteig"] in h and h[ids["Sauerteig"]][1] == 1, h)
    st, r = req("PUT", "/api/account/memory", CA, {"shadow": False})
    m = graph(CA).get("mem") or {}
    ok("Graph aus: shadow false, ohne links/hits24", m.get("shadow") is False and "links" not in m and "hits24" not in m, m)


def main():
    cleanup()
    prev_share = auth.settings(fresh=True).get("share_enabled", True)
    prev_emb = auth.settings(fresh=True).get("ai_embed")
    auth.save_setting("share_enabled", True)
    auth.save_setting("ai_embed", dict(auth.DEFAULTS["ai_embed"]))       # Rueckfall Trigramme
    auth.create_user(A, "Graph-Mem-Pass-12345", "Anna Gedaechtnis")
    auth.create_user(B, "Graph-Mem-Pass-12345", "Bert Gedaechtnis")
    CA, CB = ck(A), ck(B)
    time.sleep(5.6)                                                   # Einstellungen sind im Server 5 s zwischengespeichert
    streams = []
    try:
        d = graph(CA)
        ok("ohne Gedaechtnis: kein Feld mem", "mem" not in d and "nodes" in d, d)
        mem, ids = mkdb(A, "Erinnerungen (Graph-Test)", [
            ("Tomaten", "Tomaten brauchen viel Sonne und gleichmaessig Wasser"),
            ("Zugfahrt", "Nachtzug nach Wien ist bequemer als der Flug"),
            ("Sauerteig", "Sauerteig braucht zwoelf Stunden Ruhe im Kuehlschrank")])
        other = api_v1.page_create(tok_obj(A), {"title": "Gartenplan (Graph-Test)", "markdown": "Tomaten und Basilikum"})[1]["id"]
        d = graph(CA)
        ok("Datenbank angelegt, noch nicht gebunden: kein Feld mem", "mem" not in d and mem in {n[0] for n in d["nodes"]}, d.get("mem"))
        st, r = req("PUT", "/api/account/memory", CA, {"db": mem})
        ok("Gedaechtnis binden: 200", st == 200 and (r.get("db") or {}).get("id") == mem, (st, r))
        d = graph(CA)
        m = d.get("mem") or {}
        ok("gebunden, nichts abgerufen: mem {db, now, rows: []}", m.get("db") == mem and m.get("rows") == []
           and isinstance(m.get("now"), int) and abs(m["now"] - time.time()) < 5, m)
        ok("2601/2603: core null, snap [], shadow false, ohne links/links_total/hits24", m.get("core") is None and m.get("snap") == []
           and m.get("shadow") is False and not {"links", "links_total", "hits24"} & set(m), m)
        # Live-Kanal: zwei Sitzungen von Anna, eine von Bert
        s1, s2, sb = Stream(CA), Stream(ck(A)), Stream(CB)
        streams += [s1, s2, sb]
        time.sleep(1.0)
        ta = token(A, ["memory"])
        st, r = req("POST", "/api/v1/memory/recall", body={"text": "Tomaten Sonne Wasser"}, bearer=ta)
        hit = [x["id"] for x in r.get("treffer") or []] if st == 200 else []
        ok("REST recall findet Tomaten (Trigramme)", st == 200 and ids["Tomaten"] in hit, (st, r))
        e1, e2 = s1.wait("memory.recalled"), s2.wait("memory.recalled")
        ok("Ereignis memory.recalled an Annas erste Sitzung, nur IDs der Treffer", e1 == {"ids": hit}, e1)
        ok("Ereignis auch an Annas zweite Sitzung", e2 == {"ids": hit}, e2)
        raw = json.dumps(e1 or {}, ensure_ascii=False)
        ok("Ereignis ohne Titel/Inhalt/Frage", "Tomaten" not in raw and "Sonne" not in raw and set((e1 or {}).keys()) == {"ids"}, raw)
        time.sleep(1.5)
        ok("Bert (anderes Konto) bekommt kein memory.recalled", sb.all("memory.recalled") == [], sb.all("memory.recalled"))
        d = graph(CA)
        mr = mrows(d)
        t0 = mr.get(ids["Tomaten"])
        ok("Graph: Zeile mit hits 1 und last_hit jetzt (ganzzahlig)", t0 is not None and t0[1] == 1 and isinstance(t0[2], int)
           and abs(t0[2] - time.time()) < 10, mr)
        ok("Graph: nur abgerufene Zeilen in rows", set(mr) == set(hit), (mr, hit))
        ok("Graph: keine Seite ausserhalb der Gedaechtnis-Datenbank in rows", other not in mr and mem not in mr, mr)
        # zweiter Abruf binnen 10 min: Spalte "Abrufe" bleibt 1 (gebuendelt), der Graph zeigt live 2
        n1 = len(s1.ev)
        req("POST", "/api/v1/memory/recall", body={"text": "Tomaten Sonne Wasser"}, bearer=ta)
        s1.wait("memory.recalled", since=n1)
        pid = {p["name"]: p["id"] for p in json.loads(pg("SELECT schema FROM note WHERE id=?", (mem,))[0][0])["props"]}
        col = json.loads(pg("SELECT props FROM note WHERE id=?", (ids["Tomaten"],))[0][0] or "{}").get(pid["Abrufe"])
        h2 = mrows(graph(CA)).get(ids["Tomaten"])
        ok("Graph liest live aus mem_vec (hits 2), Spalte noch gespiegelt 1", h2 and h2[1] == 2 and col == 1, (h2, col))
        # recall ohne Treffer: kein Ereignis
        n1 = len(s1.all("memory.recalled"))
        st, r = req("POST", "/api/v1/memory/recall", body={"text": "Quantenchromodynamik Gluonen"}, bearer=ta)
        time.sleep(1.5)
        ok("recall ohne Treffer: kein Ereignis", st == 200 and r.get("treffer") == [] and len(s1.all("memory.recalled")) == n1, (st, r))
        # MCP recall meldet ebenso
        tm = token(A, ["mcp", "memory"])
        n1 = len(s1.ev)                                   # Index in alle Ereignisse (wait zaehlt ab hier)
        st, r = req("POST", "/mcp", body={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "recall", "arguments":
                                          {"text": "Nachtzug Wien Flug"}}}, bearer=tm)
        e = s1.wait("memory.recalled", since=n1)
        ok("MCP recall: memory.recalled mit der Zugfahrt", st == 200 and not r["result"]["isError"] and e and ids["Zugfahrt"] in e["ids"], (st, r, e))
        ok("MCP recall: Graph zaehlt die Zugfahrt", (mrows(graph(CA)).get(ids["Zugfahrt"]) or [0, 0])[1] == 1)
        part_shadow(CA, s1, mem, ids, other, ta)
        # Bert: Annas Gedaechtnis-Datenbank freigegeben - Zeilen als Knoten, aber nie Zaehler
        st, r = req("PUT", f"/api/notes/{mem}/shares", CA, {"type": "user", "target": B, "role": "view"})
        ok("Freigabe an Bert", st == 200, (st, r))
        d = graph(CB)
        bn = {n[0]: n for n in d.get("nodes") or []}
        ok("Bert sieht Annas Zeilen als fremde Knoten", ids["Tomaten"] in bn and bn[ids["Tomaten"]][4] == 0, list(bn)[:10])
        ok("Bert ohne eigenes Gedaechtnis: kein Feld mem (keine Zaehler fremder Seiten)", "mem" not in d, d.get("mem"))
        bmem, bids = mkdb(B, "Notizen (Graph-Test B)", [("Fahrrad", "Fahrradkette alle 500 Kilometer oelen")])
        st, r = req("PUT", "/api/account/memory", CB, {"db": bmem})
        ok("Bert bindet eigenes Gedaechtnis", st == 200, (st, r))
        st, r = req("PUT", "/api/account/memory", CB, {"db": mem})
        ok("Bert kann Annas (freigegebene) Datenbank nicht binden: 403", st == 403, (st, r))
        tb = token(B, ["memory"])
        nA = len(s1.all("memory.recalled"))
        st, r = req("POST", "/api/v1/memory/recall", body={"text": "Fahrradkette oelen Kilometer"}, bearer=tb)
        eb = sb.wait("memory.recalled")
        ok("Berts recall: Ereignis an Bert", st == 200 and eb and eb["ids"] == [bids["Fahrrad"]], (st, r, eb))
        time.sleep(1.5)
        ok("Berts recall: kein Ereignis an Anna", len(s1.all("memory.recalled")) == nA and len(s2.all("memory.recalled")) == nA,
           s1.all("memory.recalled"))
        d = graph(CB)
        m = d.get("mem") or {}
        ok("Bert: mem nur mit seiner Datenbank und seiner Zeile (Annas Zeilen ohne Zaehler)",
           m.get("db") == bmem and set(mrows(d)) == {bids["Fahrrad"]} and not (set(mrows(d)) & set(ids.values())), m)
        ma = mrows(graph(CA))
        ok("Anna: Berts Zeile nie in ihrem mem", bids["Fahrrad"] not in ma and set(ma) <= set(ids.values()), ma)
        # Pruefer (pr-graph-mem): Bindung zeigt auf eine Datenbank, die einem nicht (mehr) gehoert -> nie Zaehler
        pg("UPDATE user_kv SET v=? WHERE uid=? AND k='memory.db'", (str(mem), B))
        d = graph(CB)
        ok("Bert: Bindung auf Annas Datenbank (nicht eigene) -> kein Feld mem", "mem" not in d, d.get("mem"))
        pg("UPDATE user_kv SET v=? WHERE uid=? AND k='memory.db'", (str(bmem), B))
        # Pruefer: abgerufene Zeile im Papierkorb -> nicht mehr in rows (andere bleiben)
        st, r = req("DELETE", f"/api/notes/{ids['Zugfahrt']}", CA)
        ma = mrows(graph(CA))
        ok("Anna: Zeile im Papierkorb nicht in rows, andere bleiben", st in (200, 204) and ids["Zugfahrt"] not in ma
           and ids["Tomaten"] in ma, (st, ma))
        # Gedaechtnis-Datenbank im Papierkorb bzw. Bindung entfernt: kein Feld
        req("DELETE", f"/api/notes/{bmem}", CB)
        ok("Bert: Datenbank im Papierkorb -> kein Feld mem", "mem" not in graph(CB), graph(CB).get("mem"))
        st, r = req("PUT", "/api/account/memory", CA, {"db": None})
        ok("Anna: Bindung entfernt -> kein Feld mem", st == 200 and "mem" not in graph(CA))
        ok("ohne Anmeldung 401", req("GET", "/api/graph", "kaputt")[0] == 401)
        # Leistung: 5 000 Gedaechtnis-Zeilen mit Zaehlern, Graph 5 000 Knoten (gekuerzt), eine Abfrage
        req("PUT", "/api/account/memory", CA, {"db": mem})
        now = time.time()
        with db.connect() as c, c:
            c.executemany("INSERT INTO note (uid, title, body, parent_id, created, updated) VALUES (?,?,?,?,?,?)",
                          [(A, f"Erinnerung {i:04d}", "", mem, now - 5000 + i, now - 5000 + i) for i in range(5000)])
            nids = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND parent_id=? AND title LIKE 'Erinnerung %'", (A, mem)).fetchall()]
            c.executemany("INSERT INTO mem_vec (note_id, uid, model, dim, vec, text_hash, hits, last_hit, updated) VALUES (?,?,'',0,?,'',?,?,?)",
                          [(nid, A, b"", 1 + i % 25, now - (i % 48) * 3600, now) for i, nid in enumerate(nids)])
        t1 = time.perf_counter(); st, d = req("GET", "/api/graph", CA); dt = (time.perf_counter() - t1) * 1000
        rows = mrows(d)
        ok("5 000 Zeilen: Graph gekuerzt auf 5 000 Knoten, rows nur fuer Knoten", st == 200 and d["truncated"] and len(d["nodes"]) == 5000
           and set(rows) <= {n[0] for n in d["nodes"]} and len(rows) > 4000, (st, len(d.get("nodes") or []), len(rows)))
        with db.connect() as c:
            nodes = {n[0]: n for n in d["nodes"]}
            t2 = time.perf_counter(); memory.graph_rows(c, A, nodes); dm = (time.perf_counter() - t2) * 1000
        print(f"INFO /api/graph mit 5 000 Knoten: {dt:.0f} ms, davon Gedaechtnis-Zaehler {dm:.1f} ms", flush=True)
        ok("Leistung: Gedaechtnis-Zaehler fuer 5 000 Zeilen <= 150 ms", dm <= 150, dm)
        ok("Leistung: /api/graph mit 5 000 Knoten <= 3 s", dt <= 3000, dt)
        # 2603: 25 000 Verknuepfungen (Obergrenze 20 000 im Graph, die staerksten)
        import random
        rnd = random.Random(2603)
        pairs = set()
        while len(pairs) < 25000:
            a, b = sorted(rnd.sample(nids, 2))
            pairs.add((a, b))
        with db.connect() as c, c:
            c.executemany("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) VALUES (?,?,?,?,?,0,?,?,'aehnlich')",
                          [(a, b, A, round(0.2 + 0.4 * rnd.random(), 4), 0.05, now, now) for a, b in sorted(pairs)])
        req("PUT", "/api/account/memory", CA, {"shadow": True})
        t1 = time.perf_counter(); st, d = req("GET", "/api/graph", CA); dt = (time.perf_counter() - t1) * 1000
        m = d.get("mem") or {}
        nodes = {n[0]: n for n in d["nodes"]}
        Lk = m.get("links") or []
        ok("25 000 Links: hoechstens 20 000, beide Enden Knoten, nach (a, b), die staerksten", st == 200 and len(Lk) <= 20000
           and all(x[0] in nodes and x[1] in nodes for x in Lk) and Lk == sorted(Lk, key=lambda x: (x[0], x[1]))
           and m.get("links_total", 0) >= len(Lk) and (m["links_total"] <= 20000 or min(x[2] for x in Lk) >= 0.2), (len(Lk), m.get("links_total")))
        with db.connect() as c:
            t2 = time.perf_counter(); memory.graph_rows(c, A, nodes); dm = (time.perf_counter() - t2) * 1000
        print(f"INFO /api/graph mit 25 000 Links: {dt:.0f} ms, davon Gedaechtnis-Teil {dm:.1f} ms", flush=True)
        ok("Leistung: Gedaechtnis-Teil mit 25 000 Links <= 300 ms, /api/graph <= 3 s", dm <= 300 and dt <= 3000, (dm, dt))
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok("Ablauf ohne Ausnahme", False, repr(ex))
    finally:
        for s_ in streams:
            s_.close()
        cleanup()
        auth.save_setting("share_enabled", prev_share)
        if prev_emb is not None:
            auth.save_setting("ai_embed", prev_emb)
    print()
    print("GRAPH-GEDAECHTNIS %d/%d - %s" % (sum(res), len(res), "ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


main()
