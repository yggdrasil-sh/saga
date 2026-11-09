"""BookOrbit Phase 2 (2.5, saga/bookorbit_pages.py): Markierungen eines Buchs als Seite "Notizen: <Titel>".

Nie echtes Netz: ein kleines Fake-BookOrbit auf 127.0.0.1:BOOKORBIT_TEST_PORT (Standard 18645) mit Login/Refresh,
GET /annotations/books, GET /books/<id>, GET /books/<id>/annotations (seitenweise, wie BookOrbit: pageSize <= 100),
GET /books/<id>/bookmarks, GET /books/<id>/thumbnail.
Server starten mit: BOOKORBIT_URL=http://127.0.0.1:<port> (Vorschlag = automatisch freigegeben), SAGA_BOOKORBIT_ANN_MAX=250,
SAGA_BOOKORBIT_NOTES_RATE=40, SAGA_BOOKORBIT_CACHE_S=2.
Geprueft:
- Faehigkeit bookorbit2; ohne Anmeldung 401; ohne Verbindung 409 (state none).
- Buecherliste mit Anzahl, Cover ueber Saga, zugeordnete Seite.
- Seite anlegen: Titel/Icon, Kopf (Cover als eigene Datei, Autor, Link zu BookOrbit), Notiz zum Buch, je Kapitel eine
  Ueberschrift, Zitat-Bloecke mit Seite/Datum, Farbe aus der Palette, eigene Notiz als Absatz darunter, Lesezeichen.
- Fremdinhalt maskiert (HTML aus BookOrbit wird Text).
- Idempotenz: zweites Aktualisieren aendert nichts (Revision gleich).
- Eigene Ergaenzungen bleiben, bearbeitete Bloecke bleiben, in Saga geloeschte kommen nicht wieder, bei BookOrbit geloeschte
  bleiben stehen, unveraenderte werden nachgezogen, Neues landet beim eigenen Kapitel bzw. als neues Kapitel vor den
  Lesezeichen, eine spaeter geschriebene Notiz direkt unter ihr Zitat.
- Aktualisieren ueber page_id; Ziel-Elternseite nur eigene Seiten (fremde, Datenbank, unbekannt -> 404).
- Grenzen: seitenweiser Abruf (pageSize 100), Obergrenze SAGA_BOOKORBIT_ANN_MAX, Groesse der Seite, Textlaenge, Rate.
- Rechte: andere Person kommt weder an die Zuordnung noch an die Seite; Seite im Papierkorb -> neue Seite.
- Abgelaufene Verbindung -> 409 state expired; Buch nicht in der Bibliothek -> 404; ungueltige Angaben -> 400.
Wegwerf-Konten bos-a/bos-b, raeumt auf. Umgebung wie tests/bookorbit.py."""
import base64, http.client, json, os, secrets, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itsdangerous import TimestampSigner
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PORT = int(os.environ.get("BOOKORBIT_TEST_PORT", "18645"))
FAKE = f"http://127.0.0.1:{PORT}"
PW_A, PW_B = "Pw-A-" + secrets.token_hex(6), "Pw-B-" + secrets.token_hex(6)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:500]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"; data = json.dumps(body)
    c.request(m, p, body=data, headers=h)
    r = c.getresponse(); out = r.read()
    if r.status >= 500:
        n500[0] += 1
    try:
        out = json.loads(out)
    except ValueError:
        out = out.decode("utf-8", "replace")
    return r.status, out


# =====================================================================================================
# Fake-BookOrbit
# =====================================================================================================
def A(i, text, chapter, color="#FACC15", note=None, page=None, day="2026-10-0"):
    return {"id": i, "bookId": 0, "cfi": None, "jumpFileId": None, "pageno": page, "text": text, "color": color, "style": "highlight",
            "note": note, "chapterTitle": chapter, "origin": "web", "positionStatus": "exact", "chapterIndex": None,
            "highlightedAt": f"2026-10-{(i % 27) + 1:02d}T10:00:00.000Z", "createdAt": "2026-10-01T10:00:00.000Z", "starredAt": None}


class F:
    lock = threading.Lock()
    users = {"anna": {"pw": PW_A, "name": "Anna"}, "bert": {"pw": PW_B, "name": "Bert"}}
    access = {}             # token -> user
    refresh = {}            # refresh -> user
    books = {}              # user -> {bid: {...}}
    ann_calls = []          # (bid, page, pageSize)
    revoked = set()


def reset_books():
    F.books = {
        "anna": {
            31: {"title": "Das Meer der Worte", "authors": [{"id": 1, "name": "Ida Feld", "sortName": None}], "personalNote": "Lieblingsbuch\ndes Jahres",
                 "cover": True,
                 "ann": [A(101, "Am Anfang war das Wasser.", "Kapitel 1", "#FACC15", "Schoener Einstieg", 12),
                         A(102, "Zweite Stelle im ersten Kapitel.", "Kapitel 1", "#4ADE80", None, 14),
                         A(103, "Dritte Stelle, spaeter geloescht in Saga.", "Kapitel 1", "#F472B6", None, 15),
                         A(104, "Vierte Stelle, wird bei BookOrbit geaendert.", "Kapitel 2", "#38BDF8", None, 30),
                         A(105, "Fuenfte Stelle, wird bei BookOrbit geloescht.", "Kapitel 2", "#9CA3AF", None, 31),
                         A(106, "Sechste <img src=x onerror=alert(1)> & <script>x</script>", "Kapitel 2", "#C084FC", None, 33)],
                 "bm": [{"id": 501, "bookId": 31, "cfi": None, "title": "Wichtige Stelle", "positionSeconds": None, "createdAt": "2026-10-05T08:00:00.000Z",
                         "note": None, "updatedAt": "2026-10-05T08:00:00.000Z", "clientId": "x", "origin": "web", "chapterId": None}]},
            32: {"title": "Viele Markierungen", "authors": [], "personalNote": None, "cover": False,
                 "ann": [A(1000 + i, f"Stelle {i}", f"K{i // 50}", "#FACC15", None, i) for i in range(350)], "bm": []},
            33: {"title": "Lange Texte", "authors": [{"id": 2, "name": "Ole Stein", "sortName": None}], "personalNote": None, "cover": False,
                 "ann": [A(5000 + i, ("Wort " * 1200) + str(i), "Lang", "#FB923C", None, None) for i in range(100)], "bm": []},
        },
        "bert": {41: {"title": "Bergluft", "authors": [], "personalNote": None, "cover": False, "ann": [A(9001, "Bert liest", None)], "bm": []}},
    }


def _creds(user):
    rt, at = secrets.token_hex(32), "acc-" + secrets.token_hex(8)
    F.refresh[rt] = user; F.access[at] = user
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + s)) + ".000Z"
    return {"accessToken": at, "accessTokenExpiresAt": iso(900), "refreshToken": rt, "refreshTokenExpiresAt": iso(7 * 86400), "sessionId": 1}


class FH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ct="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def user(self):
        u = F.access.get((self.headers.get("Authorization") or "")[7:])
        return None if u in F.revoked else u

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            d = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            d = {}
        with F.lock:
            if self.path == "/api/v1/auth/login":
                u = F.users.get(d.get("username"))
                if not u or u["pw"] != d.get("password"):
                    return self.send(401, {"message": "Invalid credentials"})
                F.revoked.discard(d["username"])
                return self.send(200, dict(_creds(d["username"]), user={"username": d["username"], "name": u["name"]}))
            if self.path == "/api/v1/auth/refresh":
                u = F.refresh.pop(d.get("refreshToken"), None)
                if not u or u in F.revoked:
                    return self.send(401, {"message": "Unauthorized"})
                return self.send(200, _creds(u))
            if self.path == "/api/v1/auth/logout":
                F.refresh.pop(d.get("refreshToken"), None)
                return self.send(200, {})
            if self.path.startswith("/__revoke/"):
                F.revoked.add(self.path[10:])
                return self.send(200, {})
        self.send(404, {"message": "Not Found"})

    def do_GET(self):
        u = self.user()
        if not u:
            return self.send(401, {"message": "Unauthorized", "statusCode": 401})
        pu = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(pu.query))
        parts = pu.path.split("/")
        mine = F.books.get(u, {})
        if pu.path == "/api/v1/annotations/books":
            lim = int(q.get("limit") or 20)
            if lim > 50:
                return self.send(400, {"message": "limit must not be greater than 50"})
            out = [{"bookId": k, "bookTitle": v["title"], "author": (v["authors"][0]["name"] if v["authors"] else None), "count": len(v["ann"])}
                   for k, v in mine.items() if v["ann"]]
            if q.get("q"):
                out = [x for x in out if q["q"].lower() in x["bookTitle"].lower()]
            return self.send(200, out[:lim])
        if len(parts) >= 5 and parts[1:4] == ["api", "v1", "books"] and parts[4].isdigit():
            bid = int(parts[4]); b = mine.get(bid)
            if not b:
                return self.send(404, {"message": f"Book {bid} not found", "statusCode": 404})
            rest = parts[5:]
            if not rest:
                return self.send(200, {"id": bid, "title": b["title"], "authors": b["authors"], "personalNote": b["personalNote"],
                                       "description": "x" * 2000})
            if rest == ["annotations"]:
                page, size = int(q.get("page") or 1), int(q.get("pageSize") or 25)
                if size > 100:
                    return self.send(400, {"message": "pageSize must not be greater than 100"})
                with F.lock:
                    F.ann_calls.append((bid, page, size))
                items = [dict(x, bookId=bid) for x in b["ann"]][(page - 1) * size: page * size]
                return self.send(200, {"items": items, "total": len(b["ann"]), "page": page, "pageSize": size,
                                       "stats": {"chapters": []}})
            if rest == ["bookmarks"]:
                return self.send(200, b["bm"])
            if rest == ["thumbnail"]:
                return self.send(200, PNG, "image/png") if b["cover"] else self.send(404, {"message": "No thumbnail"})
        self.send(404, {"message": "Not Found"})


# =====================================================================================================
def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "bookorbit_link", "file"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'bos-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'bos-%'")


def page(ck_, nid):
    s, d = req("GET", f"/api/notes/{nid}", ck_)
    if s != 200:
        return s, {}, []
    return s, d, (json.loads(d.get("body") or "{}").get("blocks") or [])


def save(ck_, nid, blocks, rev=None):
    b = {"body": json.dumps({"blocks": blocks}), "format": "blocks"}
    if rev is not None:
        b["rev"] = rev
    return req("PATCH", f"/api/notes/{nid}", ck_, b)


def bo(b):
    return (b.get("data") or {}).get("bo") or {}


def find(blocks, **kw):
    return next((i for i, b in enumerate(blocks) if all(bo(b).get(k) == v for k, v in kw.items())), None)


def main():
    cleanup()
    reset_books()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), FH); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for u in ("bos-a", "bos-b"):
            auth.create_user(u, "Bookorbit-Pass-123", u)
        Ca, Cb = ck("bos-a"), ck("bos-b")
        s, d = req("GET", "/api/version")
        ok("Faehigkeit bookorbit2", "bookorbit2" in d.get("features", []), d)
        s, _ = req("GET", "/api/bookorbit/notes/books")
        ok(f"Buecherliste ohne Anmeldung 401 ({s})", s == 401)
        s, _ = req("POST", "/api/bookorbit/notes", None, {"book_id": 31})
        ok(f"Anlegen ohne Anmeldung 401 ({s})", s == 401)
        s, d = req("GET", "/api/bookorbit/notes/books", Ca)
        ok(f"ohne Verbindung: 409 state none ({s})", s == 409 and d.get("state") == "none", d)
        s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
        ok(f"Anlegen ohne Verbindung: 409 state none ({s})", s == 409 and d.get("state") == "none", d)
        s, d = req("POST", "/api/bookorbit/connect", Ca, {"url": FAKE, "username": "anna", "password": PW_A})
        ok(f"Anna verbunden ({s})", s == 200, d)
        part_list(Ca)
        nid = part_create(Ca)
        part_idem(Ca, nid)
        part_update(Ca, nid)
        part_page_id(Ca, Cb, nid)
        part_parent(Ca, Cb)
        part_limits(Ca)
        part_trash(Ca, nid)
        part_bad(Ca)
        part_expired(Ca)
        part_rate(Ca)
    finally:
        srv.shutdown()


def part_list(Ca):
    s, d = req("GET", "/api/bookorbit/notes/books", Ca)
    bs = {b["id"]: b for b in d.get("books") or []} if isinstance(d, dict) else {}
    ok(f"Buecherliste: drei Buecher mit Anzahl ({s})", s == 200 and set(bs) == {31, 32, 33} and bs[31]["count"] == 6 and bs[32]["count"] == 350, d)
    b = bs.get(31) or {}
    ok("Buch: Titel, Autor, Cover ueber Saga, noch keine Seite", b.get("title") == "Das Meer der Worte" and b.get("author") == "Ida Feld"
       and b.get("cover") == "/api/bookorbit/cover/31" and b.get("page_id") is None, b)
    ok("keine BookOrbit-Adresse/Token in der Liste", FAKE not in json.dumps(d) and "acc-" not in json.dumps(d))
    s, d = req("GET", "/api/bookorbit/notes/books?q=" + urllib.parse.quote("viele"), Ca)
    ok(f"Suche q ({s})", s == 200 and [b["id"] for b in d.get("books", [])] == [32], d)


def part_create(Ca):
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Seite angelegt ({s})", s == 200 and d.get("created") is True and d.get("added") == 6 and d.get("total") == 6
       and d.get("truncated") is False and isinstance(d.get("page_id"), int), d)
    nid = d.get("page_id")
    s, p, bl = page(Ca, nid)
    ok("Titel „Notizen: <Titel>“, Icon 📖", p.get("title") == "Notizen: Das Meer der Worte" and p.get("icon") == "📖", (p.get("title"), p.get("icon")))
    types = [b["type"] for b in bl]
    ok("Kopf: Cover als eigene Datei", types and types[0] == "image" and str(bl[0]["data"]["file"]["url"]).startswith("/files/"), bl[:1])
    info = bl[1]["data"]["text"] if len(bl) > 1 else ""
    ok("Kopf: Autor und Link zum Buch in BookOrbit", "Ida Feld" in info and f'href="{FAKE}/book/31"' in info and "In BookOrbit öffnen" in info, info)
    i = find(bl, x="bn")
    ok("Notiz zum Buch als eigener Abschnitt", i is not None and bl[i - 1]["type"] == "header" and "Lieblingsbuch<br>des Jahres" in bl[i]["data"]["text"],
       bl[i] if i is not None else bl)
    hs = [(b["data"]["text"], b["data"].get("level")) for b in bl if b["type"] == "header" and bo(b).get("c")]
    ok("je Kapitel eine Ueberschrift (Reihenfolge des Buchs)", hs == [("Kapitel 1", 2), ("Kapitel 2", 2)], hs)
    qs = [b for b in bl if b["type"] == "quote"]
    ok("sechs Zitat-Bloecke mit BookOrbit-ID", [bo(b).get("a") for b in qs] == [101, 102, 103, 104, 105, 106], [bo(b) for b in qs])
    q1 = qs[0]["data"] if qs else {}
    ok("Zitat: Text, Seite und Datum in der Unterschrift", q1.get("text") == "Am Anfang war das Wasser." and q1.get("caption") == "S. 12 · 21.10.2026", q1)
    cols = [((b.get("tunes") or {}).get("hkstyle") or {}).get("c") for b in qs]
    ok("Farben auf die Palette abgebildet", cols == ["yellow", "green", "pink", "blue", "gray", "purple"], cols)
    j = find(bl, a=101)
    ok("eigene Notiz als Absatz direkt unter dem Zitat", j is not None and bl[j + 1]["type"] == "paragraph" and bo(bl[j + 1]).get("n") == 101
       and bl[j + 1]["data"]["text"] == "Schoener Einstieg", bl[j + 1] if j is not None else None)
    x = qs[5]["data"]["text"] if len(qs) > 5 else ""
    ok("Fremdinhalt maskiert (kein HTML aus BookOrbit)", "<img" not in x and "<script" not in x and "&lt;img" in x and "&amp;" in x, x)
    k = find(bl, x="bmh")
    ok("Lesezeichen als eigener Abschnitt am Ende", k is not None and k == len(bl) - 2 and bo(bl[-1]).get("b") == 501
       and "Wichtige Stelle" in bl[-1]["data"]["text"], bl[-2:])
    s, d = req("GET", "/api/bookorbit/notes/books", Ca)
    ok("Liste kennt jetzt die Seite", {b["id"]: b["page_id"] for b in d.get("books", [])}.get(31) == nid, d)
    with connect() as c:
        kv = c.execute("SELECT v FROM user_kv WHERE uid='bos-a' AND k='bookorbit_page:31'").fetchone()
    ok("Zuordnung in user_kv (ohne Migration)", kv and json.loads(kv[0]).get("page") == nid and sorted(json.loads(kv[0])["a"]) == [101, 102, 103, 104, 105, 106], kv)
    return nid


def part_idem(Ca, nid):
    rev0 = page(Ca, nid)[1].get("rev")
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    rev1 = page(Ca, nid)[1].get("rev")
    ok(f"zweites Aktualisieren: nichts neu, Seite unveraendert ({s})", s == 200 and d.get("page_id") == nid and d.get("created") is False
       and d.get("added") == 0 and d.get("updated") == 0 and rev0 == rev1, (d, rev0, rev1))


def part_update(Ca, nid):
    s, p, bl = page(Ca, nid)
    i1 = find(bl, n=101)
    bl.insert(i1 + 1, {"type": "paragraph", "data": {"text": "Meine eigene Ergaenzung"}})
    i2 = find(bl, a=102)
    bl[i2]["data"]["text"] = "Von mir umformuliert"
    i3 = find(bl, a=103)
    del bl[i3]
    bl[find(bl, a=104)]["tunes"] = {"hkstyle": {"c": "red"}}      # Person faerbt ein Zitat um (Text bleibt)
    s, d = save(Ca, nid, bl, p.get("rev"))
    ok(f"eigene Aenderungen gespeichert ({s})", s == 200, d)
    a = F.books["anna"][31]["ann"]
    a.insert(2, A(107, "Neu im ersten Kapitel.", "Kapitel 1", "#FACC15", None, 16))
    a.append(A(108, "Ganz neues Kapitel.", "Kapitel 3", "#4ADE80", "mit Notiz", 50))
    next(x for x in a if x["id"] == 102)["text"] = "BookOrbit-Text geaendert (Saga-Block bearbeitet)"
    next(x for x in a if x["id"] == 104)["text"] = "Vierte Stelle, korrigiert."
    a[:] = [x for x in a if x["id"] != 105]
    next(x for x in a if x["id"] == 106)["note"] = "Spaeter notiert"
    F.books["anna"][31]["bm"].append(dict(F.books["anna"][31]["bm"][0], id=502, title="Zweites Lesezeichen", note="Hier weiter"))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Aktualisieren: 107, 108 und Notiz zu 106 neu, 104 nachgezogen ({s})", s == 200 and d.get("added") == 3 and d.get("updated") == 1
       and d.get("total") == 7, d)
    s, p, bl = page(Ca, nid)
    j = find(bl, n=101)
    ok("eigene Ergaenzung bleibt an ihrer Stelle", j is not None and bl[j + 1]["data"].get("text") == "Meine eigene Ergaenzung" and not bo(bl[j + 1]),
       bl[j + 1] if j is not None else None)
    ok("in Saga bearbeiteter Block bleibt (BookOrbit-Aenderung nicht uebernommen)", bl[find(bl, a=102)]["data"]["text"] == "Von mir umformuliert")
    ok("in Saga geloeschte Markierung kommt nicht wieder", find(bl, a=103) is None)
    ok("unveraenderter Block nachgezogen", bl[find(bl, a=104)]["data"]["text"] == "Vierte Stelle, korrigiert.", bl[find(bl, a=104)])
    ok("eigene Farbe des Zitats bleibt beim Nachziehen", (bl[find(bl, a=104)].get("tunes") or {}).get("hkstyle") == {"c": "red"},
       bl[find(bl, a=104)].get("tunes"))
    ok("bei BookOrbit geloeschte Markierung bleibt in Saga", find(bl, a=105) is not None)
    i107, ich2 = find(bl, a=107), next(i for i, b in enumerate(bl) if b["type"] == "header" and b["data"]["text"] == "Kapitel 2")
    ok("neue Markierung beim eigenen Kapitel (vor Kapitel 2)", i107 is not None and find(bl, a=102) < i107 < ich2, (i107, ich2))
    i108, ibm = find(bl, a=108), find(bl, x="bmh")
    ok("neues Kapitel mit Ueberschrift vor den Lesezeichen", i108 is not None and bl[i108 - 1]["type"] == "header"
       and bl[i108 - 1]["data"]["text"] == "Kapitel 3" and bo(bl[i108 + 1]).get("n") == 108 and i108 < ibm, bl[i108 - 1:i108 + 2] if i108 else bl)
    i106 = find(bl, a=106)
    ok("spaeter geschriebene Notiz direkt unter ihr Zitat", bo(bl[i106 + 1]).get("n") == 106 and bl[i106 + 1]["data"]["text"] == "Spaeter notiert",
       bl[i106 + 1])
    ok("neues Lesezeichen am Ende", bo(bl[-1]).get("b") == 502 and "Hier weiter" in bl[-1]["data"]["text"], bl[-1])
    ok("Kopf nicht doppelt", sum(1 for b in bl if bo(b).get("x") == "info") == 1 and sum(1 for b in bl if bo(b).get("x") == "cover") == 1)
    rev0 = p.get("rev")
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok("danach wieder idempotent", s == 200 and d.get("added") == 0 and d.get("updated") == 0 and page(Ca, nid)[1].get("rev") == rev0, d)
    # Block in eine Spalte verschoben (nicht mehr oberste Ebene): zaehlt als vorhanden, wird nicht neu angelegt
    s, p, bl = page(Ca, nid)
    i = find(bl, a=104)
    moved = bl.pop(i)
    bl.insert(i, {"type": "columns", "data": {"cols": [{"blocks": [moved]}, {"blocks": [{"type": "paragraph", "data": {"text": "daneben"}}]}]}})
    save(Ca, nid, bl, p.get("rev"))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    s2, p, bl = page(Ca, nid)
    ok("in Spalte verschobener Block wird nicht verdoppelt", s == 200 and d.get("added") == 0 and find(bl, a=104) is None
       and "Vierte Stelle" in json.dumps(bl, ensure_ascii=False), d)


def part_page_id(Ca, Cb, nid):
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"page_id": nid})
    ok(f"Aktualisieren ueber page_id ({s})", s == 200 and d.get("page_id") == nid and d.get("book_id") == 31 and d.get("added") == 0, d)
    other = req("POST", "/api/notes", Ca, {"title": "Eigene Seite"})[1]["id"]
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"page_id": other})
    ok(f"page_id einer Seite ohne BookOrbit -> 404 ({s})", s == 404, d)
    s, d = req("POST", "/api/bookorbit/connect", Cb, {"url": FAKE, "username": "bert", "password": PW_B})
    ok(f"Bert verbunden ({s})", s == 200, d)
    s, d = req("POST", "/api/bookorbit/notes", Cb, {"page_id": nid})
    ok(f"Bert: Annas Seite ueber page_id -> 404 ({s})", s == 404, d)
    s, d = req("POST", "/api/bookorbit/notes", Cb, {"book_id": 31})
    ok(f"Bert: Annas Buch (nicht in seiner Bibliothek) -> 404 ({s})", s == 404, d)
    s, d = req("GET", "/api/bookorbit/notes/books", Cb)
    ok("Bert sieht nur seine Buecher", s == 200 and [b["id"] for b in d.get("books", [])] == [41] and d["books"][0]["page_id"] is None, d)


def part_parent(Ca, Cb):
    mine = req("POST", "/api/notes", Ca, {"title": "Lesen"})[1]["id"]
    bert = req("POST", "/api/notes", Cb, {"title": "Berts Seite"})[1]["id"]
    req("PUT", f"/api/notes/{bert}/shares", Cb, {"type": "user", "target": "bos-a", "role": "edit"})
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33, "parent_id": bert})
    ok(f"Ziel: fremde (auch freigegebene) Seite -> 404 ({s})", s == 404, d)
    dbid = req("POST", "/api/notes", Ca, {"title": "DB", "kind": "database"})[1]["id"]
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33, "parent_id": dbid})
    ok(f"Ziel: Datenbank -> 404 ({s})", s == 404, d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33, "parent_id": 987654321})
    ok(f"Ziel: unbekannt -> 404 ({s})", s == 404, d)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM user_kv WHERE uid='bos-a' AND k='bookorbit_page:33'").fetchone()[0]
    ok("dabei nichts angelegt", n == 0)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 32, "parent_id": mine})
    s2, p, _ = page(Ca, d.get("page_id"))
    ok(f"Ziel: eigene Seite -> Unterseite ({s})", s == 200 and p.get("parent_id") == mine, (d, p.get("parent_id")))


def part_limits(Ca):
    calls = [c for c in F.ann_calls if c[0] == 32]
    ok("seitenweise abgerufen: pageSize 100, Obergrenze 250 -> drei Seiten", calls[-3:] == [(32, 1, 100), (32, 2, 100), (32, 3, 100)]
       and len([c for c in calls if c[1] == 4]) == 0, calls)
    with connect() as c:
        pid = json.loads(c.execute("SELECT v FROM user_kv WHERE uid='bos-a' AND k='bookorbit_page:32'").fetchone()[0])["page"]
    s, p, bl = page(Ca, pid)
    qs = [b for b in bl if b["type"] == "quote"]
    ok("Obergrenze: 250 Zitate, Kapitel-Ueberschriften K0..K4", len(qs) == 250 and
       [b["data"]["text"] for b in bl if b["type"] == "header"] == ["K0", "K1", "K2", "K3", "K4"], len(qs))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 32})
    ok(f"Antwort meldet total 350 und truncated ({s})", s == 200 and d.get("total") == 350 and d.get("truncated") is True and d.get("added") == 0, d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33})
    s2, p, bl = page(Ca, d.get("page_id"))
    qs = [b for b in bl if b["type"] == "quote"]
    ok(f"Groessengrenze der Seite: abgeschnitten statt Fehler ({s})", s == 200 and d.get("truncated") is True and 0 < d.get("added") < 100
       and len(p.get("body") or "") < 200000 and len(qs) == d.get("added"), (d, len(p.get("body") or "")))
    ok("Textlaenge je Markierung begrenzt", qs and all(len(b["data"]["text"]) <= 3000 for b in qs) and qs[0]["data"]["text"].endswith("…"),
       len(qs[0]["data"]["text"]) if qs else None)


def part_trash(Ca, nid):
    s, _ = req("DELETE", f"/api/notes/{nid}", Ca)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Seite im Papierkorb -> neue Seite mit allem ({s})", s == 200 and d.get("created") is True and d.get("page_id") != nid and d.get("added") == 7, d)


def part_bad(Ca):
    for body, why in (({"book_id": "31"}, "Text statt Zahl"), ({"book_id": 0}, "0"), ({"book_id": True}, "Wahrheitswert"), ({}, "nichts"),
                      ({"book_id": 31, "page_id": 1}, "beides"), ({"book_id": 31, "parent_id": "x"}, "parent_id Text"), ([], "Liste")):
        s, d = req("POST", "/api/bookorbit/notes", Ca, body)
        ok(f"ungueltig ({why}) -> 400 ({s})", s == 400, d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 999})
    ok(f"Buch nicht in der Bibliothek -> 404 ({s})", s == 404, d)


def part_expired(Ca):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    c.request("POST", "/__revoke/anna", body=b"{}", headers={"Content-Type": "application/json"}); c.getresponse().read()
    t0 = time.time()
    while True:
        s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
        if d.get("state") == "expired" or time.time() - t0 > 20:
            break
        time.sleep(1)
    ok(f"abgelaufene Verbindung -> 409 state expired ({s})", s == 409 and d.get("state") == "expired", d)
    s, d = req("GET", "/api/bookorbit/notes/books", Ca)
    ok(f"Buecherliste bei abgelaufener Verbindung -> 409 expired ({s})", s == 409 and d.get("state") == "expired", d)


def part_rate(Ca):
    st = [req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})[0] for _ in range(45)]
    ok("Rate je Konto begrenzt (429)", 429 in st, st[-5:])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        cleanup()
    ok(f"Serverfehler: {n500[0]}", n500[0] == 0)
    print(f"\nBOOKORBIT-SEITEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
