"""2.5 Graph (GET /api/graph): Knoten = eigene und freigegebene Seiten, Kanten = Verweise (l), Unterseiten (c),
Relationen (r), Tags getrennt; Papierkorb, Archiv und Vorlagen fehlen; fremde, nicht freigegebene Seiten tauchen nie auf
(auch nicht als Ziel eines Verweises). Wegwerf-Konten gr-*; raeumt auf."""
import base64, http.client, json, os, sys, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    c.request(m, p, body=json.dumps(body) if body is not None else None,
              headers={"Host": HOST, "Cookie": "saga=" + cookie, "X-Saga": "1", "Content-Type": "application/json"})
    r = c.getresponse(); d = r.read()
    try:
        d = json.loads(d)
    except ValueError:
        pass
    c.close()
    return r.status, d


def cleanup():
    from saga.userdata import delete_user_data
    for u in ("gr-a", "gr-b"):
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'gr-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'gr-%' OR target LIKE 'gr-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'gr-%'")


def link(nid):
    return {"type": "paragraph", "data": {"text": f'siehe <a href="/notes#{nid}">Seite</a>'}}


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    auth.create_user("gr-a", "Graph-Pass-12345", "Anna Graph")
    auth.create_user("gr-b", "Graph-Pass-12345", "Bert Graph")
    A, Bk = ck("gr-a"), ck("gr-b")
    time.sleep(5.6)
    try:
        st, d = req("GET", "/api/graph", A)
        ok("leerer Graph", st == 200 and d["nodes"] == [] and d["edges"] == [], (st, d))
        p1 = req("POST", "/api/notes", A, {"title": "Projekt"})[1]["id"]
        p2 = req("POST", "/api/notes", A, {"title": "Notizen", "parent_id": p1})[1]["id"]
        p3 = req("POST", "/api/notes", A, {"title": "Ideen"})[1]["id"]
        lone = req("POST", "/api/notes", A, {"title": "Einsam"})[1]["id"]
        secret = req("POST", "/api/notes", Bk, {"title": "Berts Geheimnis"})[1]["id"]
        req("PATCH", f"/api/notes/{p3}", A, {"format": "blocks", "body": json.dumps({"blocks": [link(p1), link(secret)]}), "tags": ["plan", "x"]})
        req("PATCH", f"/api/notes/{p1}", A, {"tags": ["plan"]})
        trash = req("POST", "/api/notes", A, {"title": "Weg"})[1]["id"]
        req("DELETE", f"/api/notes/{trash}", A)
        tpl = req("POST", "/api/notes", A, {"title": "Muster"})[1]["id"]
        req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
        # Datenbank mit Relation auf sich selbst
        db = req("POST", "/api/notes", A, {"kind": "database", "title": "Aufgaben"})[1]["id"]
        sch = req("GET", f"/api/notes/{db}", A)[1]["schema"]
        sch["props"].append({"id": "dep", "name": "Hängt ab von", "type": "relation", "target": db})
        req("PATCH", f"/api/notes/{db}", A, {"schema": sch})
        r1 = req("POST", "/api/notes", A, {"parent_id": db, "title": "Zeile 1"})[1]["id"]
        r2 = req("POST", "/api/notes", A, {"parent_id": db, "title": "Zeile 2"})[1]["id"]
        st, d = req("PATCH", f"/api/notes/{r2}", A, {"props": {"dep": [r1]}})
        ok("Relation gesetzt", st == 200, (st, d))
        time.sleep(3)                                   # pageindex gleicht ab (oder note_stale greift)
        st, d = req("GET", "/api/graph", A)
        ids = {n[0] for n in d["nodes"]}
        ed = {(e[0], e[1], e[2]) for e in d["edges"]}
        ok("Knoten: eigene Seiten, Datenbank, Zeilen", {p1, p2, p3, lone, db, r1, r2} <= ids, ids)
        ok("Papierkorb und Vorlage fehlen", trash not in ids and tpl not in ids, ids)
        ok("fremde Seite fehlt (auch als Verweisziel)", secret not in ids and not any(secret in (e[0], e[1]) for e in d["edges"]), d["edges"])
        ok("Verweis Ideen -> Projekt", (p3, p1, "l") in ed, ed)
        ok("Unterseite Projekt -> Notizen", (p1, p2, "c") in ed, ed)
        ok("Relation zwischen den Zeilen (einmal, beidseitig)", (min(r1, r2), max(r1, r2), "r") in ed, ed)
        ok("Zeilen haengen an der Datenbank", (db, r1, "c") in ed and (db, r2, "c") in ed, ed)
        tg = dict((t[0], set(t[1])) for t in d["tags"])
        ok("Tags mit Seiten", tg.get("plan") == {p1, p3} and tg.get("x") == {p3}, tg)
        kinds = {n[0]: n[3] for n in d["nodes"]}
        ok("Arten", kinds[db] == "database" and kinds[r1] == "row" and kinds[p1] == "page", kinds)
        ok("eigen-Merker", all(n[4] == 1 for n in d["nodes"]), d["nodes"][:3])
        # Freigabe: Bert sieht Projekt + Unterseite, nicht Ideen; Verweise zu Unsichtbarem fehlen
        req("PUT", f"/api/notes/{p1}/shares", A, {"type": "user", "target": "gr-b", "role": "view"})
        st, d = req("GET", "/api/graph", Bk)
        ids = {n[0] for n in d["nodes"]}
        ok("Bert: eigene + freigegebene Seiten", {secret, p1, p2} <= ids and p3 not in ids and db not in ids, ids)
        own = {n[0]: n[4] for n in d["nodes"]}
        ok("Bert: freigegebene Seiten nicht 'eigen'", own[p1] == 0 and own[secret] == 1, own)
        ok("Bert: Unterseiten-Kante sichtbar, Verweis von Ideen nicht", any(e[:2] == [p1, p2] for e in d["edges"])
           and not any(p3 in e[:2] for e in d["edges"]), d["edges"])
        ok("Bert: keine Tags fremder unsichtbarer Seiten", all(p3 not in t[1] for t in d["tags"]), d["tags"])
        st, _ = req("GET", "/api/graph", "kaputt")
        ok("ohne Anmeldung 401", st == 401, st)
        # Archiv fehlt
        st, dd = req("POST", f"/api/notes/{lone}/archive", A, {"archived": True})
        ok("archivieren", st == 200, (st, dd))
        st, d = req("GET", "/api/graph", A)
        ok("archivierte Seite fehlt", lone not in {n[0] for n in d["nodes"]}, [n[0] for n in d["nodes"]])
    finally:
        cleanup()
        auth.save_setting("share_enabled", prev)
    print()
    print("GRAPH %d/%d - %s" % (sum(res), len(res), "ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


main()
