"""BookOrbit Phase 2 (2.5, saga/bookorbit_pages.py): Markierungen als Seiten "Buecher -> Buch -> Kapitel".

Nie echtes Netz: ein kleines Fake-BookOrbit auf 127.0.0.1:BOOKORBIT_TEST_PORT (Standard 18645) mit Login/Refresh,
GET /annotations/books, GET /books/<id>, GET /books/<id>/annotations (seitenweise, wie BookOrbit: pageSize <= 100),
GET /books/<id>/bookmarks, GET /books/<id>/thumbnail, GET /epub/<id>/info (toc), GET /books/search (limit <= 20, Liste),
POST /books/query (Filter readStatus, Sortierung finishedAt).
Server starten mit: BOOKORBIT_URL=http://127.0.0.1:<port> (Vorschlag = automatisch freigegeben), SAGA_BOOKORBIT_ANN_MAX=250,
SAGA_BOOKORBIT_NOTES_RATE=40, SAGA_BOOKORBIT_CH_MAX=20, SAGA_BOOKORBIT_CACHE_S=2.
Geprueft:
- Faehigkeiten bookorbit2/bookorbit3; ohne Anmeldung 401; ohne Verbindung 409 (state none).
- Liste: Anzahl, Cover ueber Saga, zugeordnete Seite; Suche auch ueber die ganze Bibliothek (Buch ohne Markierungen, count 0);
  Filter "Gelesen" (neueste zuerst, read_at, auch mit Suche); ungueltiger Filter 400.
- Aufraeumen: alte Notizseiten auf oberster Ebene wandern einmalig in die Sammelseite "Buecher" (📚, Einleitung,
  Unterseiten-Block als Cover-Galerie, neue Buecher vorne im Seitenbaum); selbst einsortierte bleiben; alte Einseiten-Seite wird umgebaut (Abschnitte samt eigener
  Texte in Kapitelseiten, Titel bleibt, nichts doppelt, nichts verloren).
- Buchseite: Titel = Buchtitel, Icon 📖, in der Sammelseite, Kopf, Notiz zum Buch, Kapitel-Liste (Teile eingerueckt, Anzahl),
  Markierungen ohne Kapitel unter "Ohne Kapitel", Lesezeichen am Ende.
- Kapitelseiten aus dem TOC (Tiefe 2 als Unter-Unterseiten, tiefere zum Vorfahren), Reihenfolge wie im Buch, Kopfzeile
  "← Buch · Kapitel n von m", Platzhalter wenn leer, Markierungen im richtigen Kapitel (auch andere Schreibweise),
  Fremdinhalt maskiert (Zitate und Kapitelnamen), Obergrenze CH_MAX, Buch ohne TOC -> Kapitel aus Markierungen.
- Idempotenz (keine Revision aendert sich), eigene Texte bleiben, bearbeitete Bloecke bleiben, in Saga geloeschte
  Markierungen/Kapitelseiten kommen nicht wieder, neue TOC-Kapitel kommen an die richtige Stelle, Platzhalter weicht
  (nur unveraendert), bearbeitete Kapitel-Liste bekommt nur Neues, Spalten-Verschiebung verdoppelt nichts.
- Sammelseite: gemerkt, umbenannt/verschoben respektiert, geloescht -> neu.
- Aktualisieren ueber page_id (Buch- und Kapitelseite); Ziel-Elternseite nur eigene Seiten (fremde, Datenbank, unbekannt -> 404).
- Grenzen: seitenweiser Abruf, ANN_MAX, Groesse der Seite, Textlaenge, Rate. Rechte: andere Person sieht/aendert nichts.
- Abgelaufene Verbindung -> 409 state expired; Buch nicht in der Bibliothek -> 404; ungueltige Angaben -> 400.
Wegwerf-Konten bos-a/bos-b, raeumt auf. Umgebung wie tests/bookorbit.py."""
import base64, hashlib, http.client, json, os, secrets, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itsdangerous import TimestampSigner
import auth
from db import connect

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PORT = int(os.environ.get("BOOKORBIT_TEST_PORT", "18645"))
CH_MAX = int(os.environ.get("SAGA_BOOKORBIT_CH_MAX", "150"))
FAKE = f"http://127.0.0.1:{PORT}"
PW_A, PW_B = "Pw-A-" + secrets.token_hex(6), "Pw-B-" + secrets.token_hex(6)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:600]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=120)
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


def ckey(t):
    return "c" + hashlib.sha256(t.encode()).hexdigest()[:12]


def hh(*parts):
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()[:16]


# =====================================================================================================
# Fake-BookOrbit
# =====================================================================================================
def A(i, text, chapter, color="#FACC15", note=None, page=None):
    return {"id": i, "bookId": 0, "cfi": None, "jumpFileId": None, "pageno": page, "text": text, "color": color, "style": "highlight",
            "note": note, "chapterTitle": chapter, "origin": "web", "positionStatus": "exact", "chapterIndex": None,
            "highlightedAt": f"2026-10-{(i % 27) + 1:02d}T10:00:00.000Z", "createdAt": "2026-10-01T10:00:00.000Z", "starredAt": None}


def T(label, *kids):
    d = {"label": label, "href": "Text/" + hashlib.md5(label.encode()).hexdigest()[:6] + ".xhtml"}
    if kids:
        d["children"] = list(kids)
    return d


class F:
    lock = threading.Lock()
    users = {"anna": {"pw": PW_A, "name": "Anna"}, "bert": {"pw": PW_B, "name": "Bert"}}
    access = {}             # token -> user
    refresh = {}            # refresh -> user
    books = {}              # user -> {bid: {...}}
    ann_calls = []          # (bid, page, pageSize)
    bad_calls = []          # Aufrufe ausserhalb der Grenzen von BookOrbit
    revoked = set()


def reset_books():
    F.books = {
        "anna": {
            31: {"title": "Das Meer der Worte", "authors": [{"id": 1, "name": "Ida Feld", "sortName": None}], "personalNote": "Lieblingsbuch\ndes Jahres",
                 "cover": True, "read": "2026-10-01T09:00:00.000Z",
                 "toc": [T("Vorwort"), T("Teil Eins", T("Kapitel 1"), T("Kapitel 2", T("Abschnitt 2.1"))), T("Kapitel 3"), T("Nachwort"),
                         T("Anhang & <i>Quellen</i>")],
                 "ann": [A(101, "Am Anfang war das Wasser.", "Kapitel 1", "#FACC15", "Schoener Einstieg", 12),
                         A(102, "Zweite Stelle im ersten Kapitel.", "Kapitel 1", "#4ADE80", None, 14),
                         A(103, "Dritte Stelle, spaeter geloescht in Saga.", "Kapitel 1", "#F472B6", None, 15),
                         A(104, "Vierte Stelle, wird bei BookOrbit geaendert.", "Kapitel 2", "#38BDF8", None, 30),
                         A(105, "Fuenfte Stelle, wird bei BookOrbit geloescht.", "Kapitel 2", "#9CA3AF", None, 31),
                         A(106, "Sechste <img src=x onerror=alert(1)> & <script>x</script>", "Kapitel 2", "#C084FC", None, 33),
                         A(110, "Andere Schreibweise des Kapitels.", "kapitel 2", "#FACC15", None, 34),
                         A(111, "Tiefer im Inhaltsverzeichnis.", "Abschnitt 2.1", "#FACC15", None, 35),
                         A(109, "Ohne Kapitel markiert.", None, "#FACC15", None, 99)],
                 "bm": [{"id": 501, "bookId": 31, "cfi": None, "title": "Wichtige Stelle", "positionSeconds": None, "createdAt": "2026-10-05T08:00:00.000Z",
                         "note": None, "updatedAt": "2026-10-05T08:00:00.000Z", "clientId": "x", "origin": "web", "chapterId": None}]},
            32: {"title": "Viele Markierungen", "authors": [], "personalNote": None, "cover": False,
                 "ann": [A(1000 + i, f"Stelle {i}", f"K{i // 50}", "#FACC15", None, i) for i in range(350)], "bm": []},
            34: {"title": "Gleichzeitig", "authors": [], "personalNote": None, "cover": True, "slow": 1.0,
                 "ann": [A(8001, "Zwei Prozesse auf einmal", "K")], "bm": []},
            33: {"title": "Lange Texte", "authors": [{"id": 2, "name": "Ole Stein", "sortName": None}], "personalNote": None, "cover": False,
                 "ann": [A(5000 + i, ("Wort " * 1200) + str(i), "Lang", "#FB923C", None, None) for i in range(100)], "bm": []},
            35: {"title": "Altbestand", "authors": [], "personalNote": None, "cover": False, "toc": [T("Kapitel A"), T("Kapitel B"), T("Kapitel C")],
                 "ann": [A(3501, "Alte Stelle A", "Kapitel A"), A(3502, "Alte Stelle B", "Kapitel B"), A(3503, "Alte Stelle ohne", None),
                         A(3504, "Neu in C", "Kapitel C")], "bm": []},
            36: {"title": "Einsortiert", "authors": [], "personalNote": None, "cover": False, "ann": [A(3651, "Stelle E", "E1")], "bm": []},
            **{60 + n: {"title": f"Abbruch {n}", "authors": [], "personalNote": None, "cover": False,
                        "toc": [T("Kapitel A"), T("Kapitel B"), T("Kapitel C")],
                        "ann": [A(6000 + 10 * n + 1, f"Stelle A{n}", "Kapitel A"), A(6000 + 10 * n + 2, f"Stelle B{n}", "Kapitel B"),
                                A(6000 + 10 * n + 3, f"Neu C{n}", "Kapitel C")], "bm": []} for n in range(1, 6)},
            37: {"title": "Ohne Markierungen", "authors": [{"id": 3, "name": "Pia Lind", "sortName": None}], "personalNote": None, "cover": False,
                 "read": "2026-11-01T09:00:00.000Z", "toc": [T("Eins"), T("Zwei")], "ann": [], "bm": []},
            38: {"title": "Riesiges Inhaltsverzeichnis", "authors": [], "personalNote": None, "cover": False,
                 "toc": [T(f"Teil {i}") for i in range(CH_MAX + 10)], "ann": [], "bm": []},
        },
        "bert": {41: {"title": "Bergluft", "authors": [], "personalNote": None, "cover": False, "ann": [A(9001, "Bert liest", None)], "bm": []}},
    }


def _creds(user):
    rt, at = secrets.token_hex(32), "acc-" + secrets.token_hex(8)
    F.refresh[rt] = user; F.access[at] = user
    iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + s)) + ".000Z"
    return {"accessToken": at, "accessTokenExpiresAt": iso(900), "refreshToken": rt, "refreshTokenExpiresAt": iso(7 * 86400), "sessionId": 1}


def _card(bid, b):
    return {"id": bid, "status": "ready", "title": b["title"], "authors": [a["name"] for a in b["authors"]] + ["<b>X</b>"][:0],
            "readStatus": {"status": "read", "source": "manual", "startedAt": None, "finishedAt": b["read"], "updatedAt": b["read"]} if b.get("read") else None}


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
        if self.path == "/api/v1/books/query":
            u = self.user()
            if not u:
                return self.send(401, {"message": "Unauthorized"})
            size = ((d.get("pagination") or {}).get("size")) or 50
            rules = ((d.get("filter") or {}).get("rules")) or []
            if size > 200 or not isinstance(d.get("sort", []), list):
                F.bad_calls.append(self.path)
                return self.send(400, {"message": "bad query"})
            want = next((r.get("value") for r in rules if r.get("field") == "readStatus" and r.get("operator") == "includesAny"), None)
            mine = F.books.get(u, {})
            xs = [(k, v) for k, v in mine.items() if not want or ("read" in want and v.get("read"))]
            if d.get("q"):
                xs = [(k, v) for k, v in xs if d["q"].lower() in v["title"].lower()]
            if (d.get("sort") or [{}])[0].get("field") == "finishedAt":
                xs.sort(key=lambda kv: kv[1].get("read") or "", reverse=(d["sort"][0].get("dir") == "desc"))
            return self.send(200, {"items": [_card(k, v) for k, v in xs][:size], "total": len(xs), "page": 0, "size": size})
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
        if pu.path == "/api/v1/books/search":
            lim = int(q.get("limit") or 10)
            if lim > 20 or not q.get("q"):
                F.bad_calls.append(self.path)
                return self.send(400, {"message": "limit must not be greater than 20"})
            out = [{"id": k, "title": v["title"], "seriesName": None, "authors": [a["name"] for a in v["authors"]], "libraryId": 1,
                    "libraryName": "Bibliothek"} for k, v in sorted(mine.items(), key=lambda kv: kv[1]["title"]) if q["q"].lower() in v["title"].lower()]
            return self.send(200, out[:lim])
        if len(parts) == 6 and parts[1:4] == ["api", "v1", "epub"] and parts[4].isdigit() and parts[5] == "info":
            b = mine.get(int(parts[4]))
            if not b:
                return self.send(404, {"message": "Book not found"})
            if "toc" not in b:
                return self.send(400, {"message": "Not an EPUB"})
            return self.send(200, {"containerPath": "OEBPS/content.opf", "rootPath": "OEBPS/", "spine": [], "manifest": [], "optionalFiles": [],
                                   "toc": {"label": "Table of Contents", "children": b["toc"]}, "metadata": {}, "coverPath": None})
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
                if b.get("slow"):
                    time.sleep(b["slow"])                # liegt in der Sperre (Cover beim Anlegen) -> Wettlauf sichtbar
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


def gal(blocks):
    """Index des Unterseiten-Blocks (Widget-Art subpages) oder None."""
    return next((i for i, b in enumerate(blocks) if b.get("type") == "widget" and (b.get("data") or {}).get("kind") == "subpages"), None)


def find(blocks, **kw):
    return next((i for i, b in enumerate(blocks) if all(bo(b).get(k) == v for k, v in kw.items())), None)


def kids(pid, uid="bos-a"):
    with connect() as c:
        return [(r[0], r[1], r[2]) for r in c.execute("SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL "
                                                      "ORDER BY sort NULLS FIRST, created, id", (uid, pid)).fetchall()]


def kv(bid, uid="bos-a"):
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, f"bookorbit_page:{bid}")).fetchone()
    return json.loads(r[0]) if r else {}


def home():
    with connect() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid='bos-a' AND k='bookorbit_notes_home'").fetchone()
    return json.loads(r[0]) if r else {}


def parent_of(nid):
    with connect() as c:
        r = c.execute("SELECT parent_id FROM note WHERE id=?", (nid,)).fetchone()
    return r[0] if r else "?"


def revs(ids):
    with connect() as c:
        return {i: c.execute("SELECT rev FROM note WHERE id=?", (i,)).fetchone()[0] for i in ids}


def all_pages(bid):
    k = kv(bid)
    return [k["page"]] + list(k.get("ch", {}).values())


def text_of(bl):
    return json.dumps(bl, ensure_ascii=False)


def main():
    cleanup()
    reset_books()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), FH); threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for u in ("bos-a", "bos-b"):
            auth.create_user(u, "Bookorbit-Pass-123", u)
        Ca, Cb = ck("bos-a"), ck("bos-b")
        s, d = req("GET", "/api/version")
        ok("Faehigkeiten bookorbit2 und bookorbit3", {"bookorbit2", "bookorbit3"} <= set(d.get("features", [])), d)
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
        legacy = setup_legacy(Ca)
        part_list(Ca, legacy)
        nid = part_create(Ca)
        part_idem(Ca)
        part_update(Ca, nid)
        part_legacy(Ca, legacy)
        part_crash(Ca)
        part_search_read(Ca)
        part_page_id(Ca, Cb, nid)
        part_parent(Ca, Cb)
        part_limits(Ca)
        part_parallel(Ca)
        part_home(Ca)
        part_convert(Ca)
        part_trash(Ca, nid)
        part_bad(Ca)
        ok("BookOrbit nie ausserhalb seiner Grenzen aufgerufen (search limit <= 20, query size <= 200)", not F.bad_calls, F.bad_calls)
        part_expired(Ca)
        part_rate(Ca)
    finally:
        srv.shutdown()


# --- alte Einseiten-Notizseiten (Stand bis 9.11.) ---------------------------------------------------------------------
def setup_legacy(Ca):
    def q(i, text, k):
        return {"type": "quote", "data": {"text": text, "caption": "", "alignment": "left", "bo": {"k": k, "a": i, "h": hh(text, "")}}}
    kA, kB = ckey("Kapitel A"), ckey("Kapitel B")
    old = req("POST", "/api/notes", Ca, {"title": "Notizen: Altbestand", "icon": "📖", "cover": None})[1]["id"]
    save(Ca, old, [{"type": "paragraph", "data": {"text": "Alt · <a href=\"x\">In BookOrbit öffnen</a>", "bo": {"k": "_head", "x": "info"}}},
                   {"type": "header", "data": {"text": "Kapitel A", "level": 2, "bo": {"k": kA, "c": 1}}},
                   q(3501, "Alte Stelle A", kA),
                   {"type": "paragraph", "data": {"text": "Eigener Gedanke zu A"}},
                   {"type": "header", "data": {"text": "Kapitel B", "level": 2, "bo": {"k": kB, "c": 1}}},
                   q(3502, "Alte Stelle B", kB),
                   {"type": "paragraph", "data": {"text": "Schlussgedanke zum Buch"}},
                   q(3503, "Alte Stelle ohne", "c0"),
                   {"type": "header", "data": {"text": "Lesezeichen", "level": 2, "bo": {"k": "_bm", "x": "bmh"}}},
                   {"type": "paragraph", "data": {"text": "🔖 Alt", "bo": {"k": "_bm", "b": 3601, "h": "0"}}}])
    shelf = req("POST", "/api/notes", Ca, {"title": "Meine Lektuere", "cover": None})[1]["id"]
    old2 = req("POST", "/api/notes", Ca, {"title": "Notizen: Einsortiert", "parent_id": shelf, "cover": None})[1]["id"]
    kE = ckey("E1")
    save(Ca, old2, [{"type": "paragraph", "data": {"text": "Info", "bo": {"k": "_head", "x": "info"}}},
                    {"type": "header", "data": {"text": "E1", "level": 2, "bo": {"k": kE, "c": 1}}}, q(3651, "Stelle E", kE)])
    with connect() as c, c:
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("bos-a", "bookorbit_page:35",
                  json.dumps({"page": old, "a": [3501, 3502, 3503], "n": [], "b": [3601], "x": ["info"]})))
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("bos-a", "bookorbit_page:36",
                  json.dumps({"page": old2, "a": [3651], "n": [], "b": [], "x": ["info"]})))
    return {"old": old, "old2": old2, "shelf": shelf}


def part_list(Ca, legacy):
    s, d = req("GET", "/api/bookorbit/notes/books", Ca)
    bs = {b["id"]: b for b in d.get("books") or []} if isinstance(d, dict) else {}
    ok(f"Buecherliste: Buecher mit Markierungen und Anzahl ({s})", s == 200 and set(bs) == {31, 32, 33, 34, 35, 36, 61, 62, 63, 64, 65} and bs[31]["count"] == 9
       and bs[32]["count"] == 350, d)
    b = bs.get(31) or {}
    ok("Buch: Titel, Autor, Cover ueber Saga, noch keine Seite", b.get("title") == "Das Meer der Worte" and b.get("author") == "Ida Feld"
       and b.get("cover") == "/api/bookorbit/cover/31" and b.get("page_id") is None, b)
    ok("alte Seiten sind zugeordnet", bs.get(35, {}).get("page_id") == legacy["old"] and bs.get(36, {}).get("page_id") == legacy["old2"], bs.get(35))
    ok("keine BookOrbit-Adresse/Token in der Liste", FAKE not in json.dumps(d) and "acc-" not in json.dumps(d))
    h = home()
    s2, p, bl = page(Ca, h.get("page"))
    ok("Aufraeumen: Sammelseite „Bücher“ 📚 auf oberster Ebene angelegt und gemerkt", s2 == 200 and p.get("title") == "Bücher" and p.get("icon") == "📚"
       and p.get("parent_id") is None and h.get("moved") is True, (h, p.get("title")))
    ok("Aufraeumen: alte Seite von oberster Ebene in die Sammelseite", parent_of(legacy["old"]) == h.get("page"), parent_of(legacy["old"]))
    ok("Aufraeumen: selbst einsortierte Seite bleibt, wo sie ist", parent_of(legacy["old2"]) == legacy["shelf"], parent_of(legacy["old2"]))
    g = bl[gal(bl)]["data"] if gal(bl) is not None else {}
    ok("Sammelseite: Einleitung (grau, „vorne dazu“) und Unterseiten-Galerie (Kartenbild Inhalt, Größe m), keine Liste mehr",
       find(bl, x="intro") == 0 and "hk-c-gray" in bl[0]["data"]["text"] and "vorne dazu" in bl[0]["data"]["text"] and gal(bl) == 1
       and g.get("size") == "m" and g.get("cfg") == {"view": "gallery", "image": "content"} and find(bl, x="books") is None
       and "gal" in (h.get("x") or []) and not h.get("listed"), (bl, h))
    s3, w = req("GET", f"/api/notes/{h.get('page')}/widgets", Ca)
    wd = ((w.get("widgets") or {}).get(bl[gal(bl)]["id"]) or {}).get("data") or {} if gal(bl) is not None and isinstance(w, dict) else {}
    ok(f"Galerie zeigt die eingeraeumte Buchseite (selbst einsortierte nicht, liegt anderswo) ({s3})",
       [x["id"] for x in wd.get("items") or []] == [legacy["old"]], w)
    s, d = req("GET", "/api/bookorbit/notes/books?q=" + urllib.parse.quote("viele"), Ca)
    ok(f"Suche q ({s})", s == 200 and [b["id"] for b in d.get("books", [])] == [32], d)
    s, d = req("GET", "/api/bookorbit/notes/books?filter=alles", Ca)
    ok(f"unbekannter Filter -> 400 ({s})", s == 400, d)


def part_create(Ca):
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Buch angelegt ({s})", s == 200 and d.get("created") is True and d.get("added") == 9 and d.get("total") == 9 and d.get("chapters") == 7
       and d.get("chapters_added") == 7 and d.get("truncated") is False and isinstance(d.get("page_id"), int), d)
    nid = d.get("page_id")
    s, p, bl = page(Ca, nid)
    ok("Buchseite: Titel = Buchtitel, Icon 📖, in der Sammelseite", p.get("title") == "Das Meer der Worte" and p.get("icon") == "📖"
       and p.get("parent_id") == home().get("page"), (p.get("title"), p.get("icon"), p.get("parent_id")))
    ok("Kopf: Cover als eigene Datei (links, 25 %)", bl and bl[0]["type"] == "image" and str(bl[0]["data"]["file"]["url"]).startswith("/files/")
       and bl[0]["data"].get("width") == 25, bl[:1])
    info = bl[1]["data"]["text"] if len(bl) > 1 else ""
    ok("Kopf: Autor und Link zum Buch in BookOrbit", "Ida Feld" in info and f'href="{FAKE}/book/31"' in info and "In BookOrbit öffnen" in info, info)
    i = find(bl, x="bn")
    ok("Notiz zum Buch als eigener Abschnitt", i is not None and bl[i - 1]["type"] == "header" and "Lieblingsbuch<br>des Jahres" in bl[i]["data"]["text"], bl)
    order = [bo(b).get("x") or ("a%d" % bo(b)["a"] if bo(b).get("a") else bo(b).get("b")) for b in bl]
    ok("Buchseite: Kopf, Notiz, Kapitel, Ohne Kapitel, Lesezeichen", order == ["cover", "info", "bnh", "bn", "chh", "chl", "mh", "a109", "bmh", 501], order)
    ok("Abschnitt „Kapitel“ und „Ohne Kapitel“ als H2", bl[4]["data"]["text"] == "Kapitel" and bl[6]["data"]["text"] == "Ohne Kapitel"
       and bl[4]["data"]["level"] == 2, (bl[4], bl[6]))
    top = kids(nid)
    ok("Kapitelseiten in Buchreihenfolge (Tiefe 1), Icon 📑", [x[1] for x in top] == ["Vorwort", "Teil Eins", "Kapitel 3", "Nachwort", "Anhang & <i>Quellen</i>"]
       and all(x[2] == "📑" for x in top), top)
    ch = {x[1]: x[0] for x in top}
    sub = kids(ch.get("Teil Eins"))
    ok("Teil mit Kapiteln darunter (Tiefe 2), tiefere Eintraege ohne eigene Seite", [x[1] for x in sub] == ["Kapitel 1", "Kapitel 2"], sub)
    ch.update({x[1]: x[0] for x in sub})
    lst = bl[5]["data"]["items"]
    ok("Kapitel-Liste: Verweise in Buchreihenfolge, Teil eingerueckt", [it["content"].split('"')[1] for it in lst] ==
       [f"/notes#{ch[k]}" for k in ("Vorwort", "Teil Eins", "Kapitel 3", "Nachwort", "Anhang & <i>Quellen</i>")]
       and [it["content"].split('"')[1] for it in lst[1]["items"]] == [f"/notes#{ch['Kapitel 1']}", f"/notes#{ch['Kapitel 2']}"], lst)
    ok("Kapitel-Liste: Anzahl klein/grau dahinter", "3 Markierungen" in lst[1]["items"][0]["content"] and "hk-c-gray" in lst[1]["items"][0]["content"]
       and "5 Markierungen" in lst[1]["items"][1]["content"] and "Markierung" not in lst[0]["content"], lst[1])
    ok("Kapitelname aus BookOrbit maskiert", "&lt;i&gt;Quellen" in lst[4]["content"] and "<i>" not in lst[4]["content"] and "&amp;" in lst[4]["content"], lst[4])
    s, p1, b1 = page(Ca, ch["Kapitel 1"])
    nav = b1[0]["data"]["text"] if b1 else ""
    ok("Kapitelseite: Kopfzeile „← Buch · Teil · Kapitel n von m“", find(b1, x="back") == 0 and f'href="/notes#{nid}"' in nav
       and f'href="/notes#{ch["Teil Eins"]}"' in nav and "Kapitel 3 von 7" in nav and "←" in nav and "hk-c-gray" in nav, nav)
    ok("Kapitel 1: drei Zitate in Reihenfolge, Notiz darunter", [bo(b).get("a") for b in b1 if b["type"] == "quote"] == [101, 102, 103]
       and bo(b1[2]).get("n") == 101 and b1[2]["data"]["text"] == "Schoener Einstieg", b1)
    q1 = b1[1]["data"]
    ok("Zitat: Text, Seite und Datum in der Unterschrift, Farbe", q1.get("text") == "Am Anfang war das Wasser." and q1.get("caption") == "S. 12 · 21.10.2026"
       and b1[1].get("tunes", {}).get("hkstyle") == {"c": "yellow", "bg": True}, b1[1])
    s, p2, b2 = page(Ca, ch["Kapitel 2"])
    ok("Kapitel 2: auch andere Schreibweise und tieferer TOC-Eintrag", [bo(b).get("a") for b in b2 if b["type"] == "quote"] == [104, 105, 106, 110, 111], b2)
    x = next((b["data"]["text"] for b in b2 if bo(b).get("a") == 106), "")
    ok("Fremdinhalt maskiert (kein HTML aus BookOrbit)", "<img" not in x and "<script" not in x and "&lt;img" in x and "&amp;" in x, x)
    s, p3, b3 = page(Ca, ch["Kapitel 3"])
    ok("Kapitel ohne Markierungen: Kopfzeile und Platzhalter (grau)", [bo(b).get("x") for b in b3] == ["back", "ph"] and "hk-c-gray" in b3[1]["data"]["text"]
       and "Noch keine Markierungen" in b3[1]["data"]["text"] and "Kapitel 5 von 7" in b3[0]["data"]["text"], b3)
    s, d = req("GET", "/api/bookorbit/notes/books", Ca)
    ok("Liste kennt jetzt die Buchseite", {b["id"]: b["page_id"] for b in d.get("books", [])}.get(31) == nid, d)
    k = kv(31)
    ok("Zuordnung in user_kv: Buchseite, Kapitelseiten, v2", k.get("page") == nid and k.get("v") == 2 and len(k.get("ch", {})) == 7
       and sorted(k["a"]) == [101, 102, 103, 104, 105, 106, 109, 110, 111], k)
    s, p, hb = page(Ca, home()["page"])
    ok("Sammelseite: neue Buchseite steht im Seitenbaum vorne (Galerie zeigt sie zuerst), Galerie unveraendert",
       kids(home()["page"])[0][0] == nid and gal(hb) == 1 and len([b for b in hb if b.get("type") == "widget"]) == 1, (kids(home()["page"]), hb))
    return nid


def part_idem(Ca):
    ids = all_pages(31) + [home()["page"]]
    r0 = revs(ids)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    r1 = revs(ids)
    ok(f"zweites Aktualisieren: nichts neu, keine Seite geaendert ({s})", s == 200 and d.get("created") is False and d.get("added") == 0
       and d.get("updated") == 0 and d.get("chapters_added") == 0 and r0 == r1, (d, r0, r1))


def chap(nid, title):
    flat = []
    for x in kids(nid):
        flat.append(x)
        flat.extend(kids(x[0]))
    return next((x[0] for x in flat if x[1] == title), None)


def part_update(Ca, nid):
    k1, k2, k3, vw, nw = (chap(nid, t) for t in ("Kapitel 1", "Kapitel 2", "Kapitel 3", "Vorwort", "Nachwort"))
    s, p, bl = page(Ca, k1)
    bl.insert(find(bl, n=101) + 1, {"type": "paragraph", "data": {"text": "Meine eigene Ergaenzung"}})
    bl[find(bl, a=102)]["data"]["text"] = "Von mir umformuliert"
    del bl[find(bl, a=103)]
    s, d = save(Ca, k1, bl, p.get("rev"))
    ok(f"eigene Aenderungen in Kapitel 1 gespeichert ({s})", s == 200, d)
    s, p, bl = page(Ca, k2)
    bl[find(bl, a=104)]["tunes"] = {"hkstyle": {"c": "red"}}
    save(Ca, k2, bl, p.get("rev"))
    s, p, bl = page(Ca, vw)
    bl[find(bl, x="ph")]["data"]["text"] = "Meine Gedanken zum Vorwort"       # Platzhalter ueberschrieben
    save(Ca, vw, bl, p.get("rev"))
    s, _ = req("DELETE", f"/api/notes/{nw}", Ca)
    ok(f"Kapitelseite „Nachwort“ geloescht ({s})", s == 200)
    b = F.books["anna"][31]
    a = b["ann"]
    a.insert(3, A(107, "Neu im ersten Kapitel.", "Kapitel 1", "#FACC15", None, 16))
    a.append(A(108, "Endlich etwas in Kapitel 3.", "Kapitel 3", "#4ADE80", "mit Notiz", 50))
    a.append(A(113, "Erste Markierung im Vorwort.", "Vorwort", "#4ADE80", None, 2))
    a.append(A(112, "Im geloeschten Nachwort.", "Nachwort", "#4ADE80", None, 90))
    next(x for x in a if x["id"] == 102)["text"] = "BookOrbit-Text geaendert (Saga-Block bearbeitet)"
    next(x for x in a if x["id"] == 104)["text"] = "Vierte Stelle, korrigiert."
    a[:] = [x for x in a if x["id"] != 105]
    next(x for x in a if x["id"] == 106)["note"] = "Spaeter notiert"
    b["bm"].append(dict(b["bm"][0], id=502, title="Zweites Lesezeichen", note="Hier weiter"))
    b["toc"].insert(1, T("Prolog"))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Aktualisieren: 107, 108, 113 und Notiz zu 106 neu, 104 nachgezogen, Prolog neu ({s})", s == 200 and d.get("added") == 4
       and d.get("updated") == 1 and d.get("chapters_added") == 1 and d.get("chapters") == 7, d)
    s, p, bl = page(Ca, k1)
    j = find(bl, n=101)
    ok("eigene Ergaenzung bleibt an ihrer Stelle", j is not None and bl[j + 1]["data"].get("text") == "Meine eigene Ergaenzung" and not bo(bl[j + 1]), bl)
    ok("in Saga bearbeiteter Block bleibt", bl[find(bl, a=102)]["data"]["text"] == "Von mir umformuliert")
    ok("in Saga geloeschte Markierung kommt nicht wieder", find(bl, a=103) is None)
    ok("neue Markierung in ihrem Kapitel", find(bl, a=107) is not None and find(bl, a=107) > find(bl, a=102), [bo(x) for x in bl])
    s, p, bl = page(Ca, k2)
    ok("unveraenderter Block nachgezogen, eigene Farbe bleibt", bl[find(bl, a=104)]["data"]["text"] == "Vierte Stelle, korrigiert."
       and bl[find(bl, a=104)].get("tunes", {}).get("hkstyle") == {"c": "red"}, bl[find(bl, a=104)])
    ok("bei BookOrbit geloeschte Markierung bleibt in Saga", find(bl, a=105) is not None)
    i106 = find(bl, a=106)
    ok("spaeter geschriebene Notiz direkt unter ihr Zitat", bo(bl[i106 + 1]).get("n") == 106 and bl[i106 + 1]["data"]["text"] == "Spaeter notiert", bl[i106 + 1])
    s, p, bl = page(Ca, k3)
    ok("Platzhalter (unveraendert) weicht der ersten Markierung", find(bl, x="ph") is None and [bo(x).get("a") or bo(x).get("n") or bo(x).get("x") for x in bl]
       == ["back", 108, 108], bl)
    s, p, bl = page(Ca, vw)
    ok("ueberschriebener Platzhalter bleibt als eigener Text, Zitat darueber", [x["data"].get("text") if bo(x).get("x") == "ph" else bo(x).get("a") or bo(x).get("x")
                                                                                for x in bl] == ["back", 113, "Meine Gedanken zum Vorwort"], bl)
    ok("geloeschte Kapitelseite kommt nicht wieder, ihre Markierung nirgends", chap(nid, "Nachwort") is None
       and "Im geloeschten Nachwort" not in "".join(text_of(page(Ca, x)[2]) for x in all_pages(31)), kids(nid))
    top = [x[1] for x in kids(nid)]
    ok("neues TOC-Kapitel an der richtigen Stelle", top == ["Vorwort", "Prolog", "Teil Eins", "Kapitel 3", "Anhang & <i>Quellen</i>"], top)
    s, p, bl = page(Ca, nid)
    lst = bl[find(bl, x="chl")]["data"]["items"]
    ok("Kapitel-Liste neu aufgebaut (Prolog, ohne Nachwort)", len(lst) == 5 and f"/notes#{chap(nid, 'Prolog')}" in lst[1]["content"]
       and f"/notes#{nw}" not in text_of(lst), lst)
    ok("neues Lesezeichen am Ende der Buchseite", bo(bl[-1]).get("b") == 502 and "Hier weiter" in bl[-1]["data"]["text"], bl[-1])
    ok("Kopf nicht doppelt", sum(1 for b in bl if bo(b).get("x") == "info") == 1 and sum(1 for b in bl if bo(b).get("x") == "cover") == 1)
    s, p, bp = page(Ca, chap(nid, "Prolog"))
    ok("neue Kapitelseite: Kopfzeile „Kapitel 2 von 7“ und Platzhalter", [bo(x).get("x") for x in bp] == ["back", "ph"] and "Kapitel 2 von 7" in bp[0]["data"]["text"], bp)
    s, p, bv = page(Ca, vw)
    ok("Kopfzeile der anderen Kapitel nachgezogen (Kapitel 3 von 7 -> 4 von 7)", "Kapitel 4 von 7" in page(Ca, k1)[2][0]["data"]["text"],
       page(Ca, k1)[2][0])
    ids = all_pages(31)
    r0 = revs(ids)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok("danach wieder idempotent", s == 200 and d.get("added") == 0 and d.get("updated") == 0 and revs(ids) == r0, d)
    # Kapitel-Liste von der Person bearbeitet: nur Neues kommt dazu
    s, p, bl = page(Ca, nid)
    i = find(bl, x="chl")
    bl[i]["data"]["items"].append({"content": "Mein eigener Eintrag", "meta": {}, "items": []})
    save(Ca, nid, bl, p.get("rev"))
    b["toc"].append(T("Glossar"))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    s2, p, bl = page(Ca, nid)
    lst = bl[find(bl, x="chl")]["data"]["items"]
    ok("bearbeitete Kapitel-Liste: eigener Eintrag bleibt, neues Kapitel angefuegt", s == 200 and d.get("chapters_added") == 1
       and lst[-2]["content"] == "Mein eigener Eintrag" and f"/notes#{chap(nid, 'Glossar')}" in lst[-1]["content"] and len(lst) == 7, lst)
    # Block in eine Spalte verschoben (nicht mehr oberste Ebene): zaehlt als vorhanden, wird nicht neu angelegt
    s, p, bl = page(Ca, k2)
    i = find(bl, a=104)
    moved = bl.pop(i)
    bl.insert(i, {"type": "columns", "data": {"cols": [{"blocks": [moved]}, {"blocks": [{"type": "paragraph", "data": {"text": "daneben"}}]}]}})
    save(Ca, k2, bl, p.get("rev"))
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    s2, p, bl = page(Ca, k2)
    ok("in Spalte verschobener Block wird nicht verdoppelt", s == 200 and d.get("added") == 0 and find(bl, a=104) is None
       and text_of(bl).count("Vierte Stelle") == 1, d)


def part_legacy(Ca, legacy):
    old = legacy["old"]
    s, p0, bl0 = page(Ca, old)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 35})
    ok(f"alte Einseiten-Seite umgebaut ({s})", s == 200 and d.get("created") is False and d.get("page_id") == old and d.get("added") == 1
       and d.get("chapters") == 3 and d.get("chapters_added") == 3, d)
    s, p, bl = page(Ca, old)
    ok("alte Seite: Titel bleibt", p.get("title") == "Notizen: Altbestand", p.get("title"))
    ok("alte Seite: keine Kapitel-Ueberschriften mehr, Rest bleibt", not any(bo(b).get("c") for b in bl) and find(bl, a=3503) is not None
       and find(bl, b=3601) is not None and find(bl, x="chl") is not None and find(bl, x="mh") is None, [bo(b) for b in bl])
    ch = {x[1]: x[0] for x in kids(old)}
    ok("Kapitelseiten A, B, C in Reihenfolge", list(ch) == ["Kapitel A", "Kapitel B", "Kapitel C"], ch)
    s, p, ba = page(Ca, ch.get("Kapitel A"))
    ok("Abschnitt A samt eigenem Text in die Kapitelseite", [bo(b).get("x") or bo(b).get("a") or b["data"].get("text") for b in ba]
       == ["back", 3501, "Eigener Gedanke zu A"], ba)
    s, p, bc = page(Ca, ch.get("Kapitel C"))
    ok("neues Kapitel C bekommt die neue Markierung", [bo(b).get("x") or bo(b).get("a") for b in bc] == ["back", 3504], bc)
    allt = "".join(text_of(page(Ca, x)[2]) for x in all_pages(35))
    ok("nichts verloren, nichts doppelt", all(allt.count(t) == 1 for t in ("Alte Stelle A", "Alte Stelle B", "Alte Stelle ohne", "Eigener Gedanke zu A",
                                                                         "Schlussgedanke zum Buch", "🔖 Alt")), allt[:300])
    ok("eigener Text hinter dem letzten Kapitel bleibt auf der Buchseite", "Schlussgedanke zum Buch" in text_of(bl)
       and "Schlussgedanke" not in text_of(page(Ca, ch.get("Kapitel B"))[2]), [b["data"].get("text") for b in bl])
    ids = all_pages(35)
    r0 = revs(ids)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 35})
    ok("umgebaute Seite: danach idempotent", s == 200 and d.get("added") == 0 and d.get("chapters_added") == 0 and revs(ids) == r0, d)


CRASH = [("created", 1), ("created", 3), ("copied", 1), ("copied", 2), ("cut", 1)]


def part_crash(Ca):
    """Umbau einer alten Seite an jeder Stelle abgebrochen (Testhaken bp._STEP wirft RuntimeError), danach normaler Lauf:
    jeder eigene Text genau einmal, keine doppelten Kapitelseiten, danach idempotent."""
    import subprocess
    for n, (step, nth) in enumerate(CRASH, 1):
        bid = 60 + n
        kA, kB = ckey("Kapitel A"), ckey("Kapitel B")
        old = req("POST", "/api/notes", Ca, {"title": f"Notizen: Abbruch {n}", "cover": None})[1]["id"]
        qa = {"type": "quote", "data": {"text": f"Stelle A{n}", "caption": "", "alignment": "left", "bo": {"k": kA, "a": 6000 + 10 * n + 1, "h": "0"}}}
        qb = {"type": "quote", "data": {"text": f"Stelle B{n}", "caption": "", "alignment": "left", "bo": {"k": kB, "a": 6000 + 10 * n + 2, "h": "0"}}}
        save(Ca, old, [{"type": "paragraph", "data": {"text": "Info", "bo": {"k": "_head", "x": "info"}}},
                       {"type": "header", "data": {"text": "Kapitel A", "level": 2, "bo": {"k": kA, "c": 1}}}, qa,
                       {"type": "paragraph", "data": {"text": f"Eigenes A{n}"}},
                       {"type": "header", "data": {"text": "Kapitel B", "level": 2, "bo": {"k": kB, "c": 1}}}, qb,
                       {"type": "paragraph", "data": {"text": f"Eigenes B{n}"}}, dict(qb, data=dict(qb["data"], text=f"Zweite B{n}", bo=dict(qb["data"]["bo"], a=6000 + 10 * n + 4))),
                       {"type": "paragraph", "data": {"text": f"Schluss{n}"}}])
        with connect() as c, c:
            c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("bos-a", f"bookorbit_page:{bid}",
                      json.dumps({"page": old, "a": [6000 + 10 * n + 1, 6000 + 10 * n + 2, 6000 + 10 * n + 4], "n": [], "b": [], "x": ["info"]})))
        script = ("import sys, json; sys.path.insert(0, '.'); from saga import bookorbit_pages as bp\n"
                  f"cnt = [0]\n"
                  f"def st(name):\n    if name == {step!r}:\n        cnt[0] += 1\n        if cnt[0] == {nth}: raise RuntimeError('Abbruch')\n"
                  "bp._STEP = st\n"
                  f"try: print(json.dumps(bp.build('bos-a', {bid})))\n"
                  "except bp.Fail as f: print(json.dumps({'error': f.msg, 'status': f.status}))")
        pr = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=120,
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=dict(os.environ))
        out = (pr.stdout.decode().strip().splitlines() or ["{}"])[-1]
        ok(f"Abbruch nach {step} #{nth}: Lauf bricht ab", '"status": 502' in out, (out, pr.stderr.decode()[-300:]))
        edited = (step, nth) == ("copied", 1)
        if edited:                                  # Pruefer: nach dem Abbruch bearbeitet die Person die Buchseite weiter
            s0, p0, bl0 = page(Ca, old)
            for b in bl0:
                if b.get("type") == "paragraph" and b["data"].get("text") == f"Eigenes A{n}":
                    b["data"]["text"] = f"Eigenes A{n} – nachträglich geändert"
            save(Ca, old, bl0, p0.get("rev"))
        s1, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": bid})
        ids = all_pages(bid)
        r0 = revs(ids)
        s2, d2 = req("POST", "/api/bookorbit/notes", Ca, {"book_id": bid})
        allt = "".join(text_of(page(Ca, x)[2]) for x in all_pages(bid))
        names = [x[1] for x in kids(old)]
        ok(f"Abbruch nach {step} #{nth}: naechster Lauf macht fertig, nichts verloren, nichts doppelt ({s1}, {s2})",
           s1 == 200 and s2 == 200 and names == ["Kapitel A", "Kapitel B", "Kapitel C"] and kv(bid).get("v") == 2
           and all(allt.count(x) == 1 for x in (f"Stelle A{n}", f"Stelle B{n}", f"Zweite B{n}", f"Eigenes B{n}", f"Schluss{n}", f"Neu C{n}")
                   + ((f"Eigenes A{n} – nachträglich geändert",) if edited else (f"Eigenes A{n}",)))
           and d2.get("added") == 0 and revs(ids) == r0, (d, d2, names, kv(bid).get("v"), [allt.count(x) for x in (f"Stelle A{n}", f"Eigenes A{n}", f"Eigenes B{n}", f"Schluss{n}")]))
        s3, p, bl = page(Ca, old)
        ok(f"Abbruch nach {step} #{nth}: Schlussgedanke bleibt auf der Buchseite, Abschnitte draussen",
           f"Schluss{n}" in text_of(bl) and not any(bo(b).get("c") for b in bl), [b["data"].get("text") for b in bl])


def part_search_read(Ca):
    s, d = req("GET", "/api/bookorbit/notes/books?q=" + urllib.parse.quote("ohne"), Ca)
    b = next((x for x in d.get("books", []) if x["id"] == 37), None) if isinstance(d, dict) else None
    ok(f"Suche findet Buch ohne Markierungen (ganze Bibliothek, count 0) ({s})", s == 200 and b and b["count"] == 0 and b["author"] == "Pia Lind"
       and b["page_id"] is None, d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 37})
    ok(f"Buch ohne Markierungen als Seite: Kapitel aus dem TOC ({s})", s == 200 and d.get("created") and d.get("added") == 0 and d.get("chapters") == 2, d)
    nid = d.get("page_id")
    ok("Kapitelseiten Eins, Zwei mit Platzhalter", [x[1] for x in kids(nid)] == ["Eins", "Zwei"]
       and find(page(Ca, kids(nid)[0][0])[2], x="ph") is not None, kids(nid))
    s, d = req("GET", "/api/bookorbit/notes/books?filter=read", Ca)
    bs = d.get("books", []) if isinstance(d, dict) else []
    ok(f"Filter Gelesen: neueste zuerst, auch ohne Markierungen ({s})", s == 200 and d.get("filter") == "read" and [x["id"] for x in bs] == [37, 31]
       and bs[0]["read_at"].startswith("2026-11-01") and bs[0]["page_id"] == nid and bs[1]["count"] == 12 and bs[0]["count"] == 0, d)
    s, d = req("GET", "/api/bookorbit/notes/books?filter=read&q=" + urllib.parse.quote("meer"), Ca)
    ok(f"Filter Gelesen mit Suche ({s})", s == 200 and [x["id"] for x in d.get("books", [])] == [31], d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 38})
    ok(f"Obergrenze Kapitelseiten ({CH_MAX}) -> truncated ({s})", s == 200 and d.get("chapters") == CH_MAX and d.get("truncated") is True
       and len(kids(d.get("page_id"))) == CH_MAX, d)


def part_page_id(Ca, Cb, nid):
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"page_id": nid})
    ok(f"Aktualisieren ueber page_id (Buchseite) ({s})", s == 200 and d.get("page_id") == nid and d.get("book_id") == 31 and d.get("added") == 0, d)
    k1 = chap(nid, "Kapitel 1")
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"page_id": k1})
    ok(f"Aktualisieren ueber page_id (Kapitelseite) ({s})", s == 200 and d.get("page_id") == nid and d.get("book_id") == 31, d)
    other = req("POST", "/api/notes", Ca, {"title": "Eigene Seite"})[1]["id"]
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"page_id": other})
    ok(f"page_id einer Seite ohne BookOrbit -> 404 ({s})", s == 404, d)
    s, d = req("POST", "/api/bookorbit/connect", Cb, {"url": FAKE, "username": "bert", "password": PW_B})
    ok(f"Bert verbunden ({s})", s == 200, d)
    ids = all_pages(31)
    r0 = revs(ids)
    for x in (nid, k1):
        s, d = req("POST", "/api/bookorbit/notes", Cb, {"page_id": x})
        ok(f"Bert: Annas Seite ueber page_id -> 404 ({s})", s == 404, d)
        s, d = req("GET", f"/api/notes/{x}", Cb)
        ok(f"Bert: Annas Seite nicht lesbar ({s})", s in (403, 404), d)
    s, d = req("POST", "/api/bookorbit/notes", Cb, {"book_id": 31})
    ok(f"Bert: Annas Buch (nicht in seiner Bibliothek) -> 404 ({s})", s == 404, d)
    s, d = req("GET", "/api/bookorbit/notes/books", Cb)
    ok("Bert sieht nur seine Buecher", s == 200 and [b["id"] for b in d.get("books", [])] == [41] and d["books"][0]["page_id"] is None, d)
    ok("Annas Seiten dabei unveraendert", revs(ids) == r0)


def part_parent(Ca, Cb):
    mine = req("POST", "/api/notes", Ca, {"title": "Lesen"})[1]["id"]
    bert = req("POST", "/api/notes", Cb, {"title": "Berts Seite"})[1]["id"]
    req("PUT", f"/api/notes/{bert}/shares", Cb, {"type": "user", "target": "bos-a", "role": "edit"})
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33, "parent_id": bert})
    ok(f"Ziel: fremde (auch freigegebene) Seite -> 404 ({s})", s == 404, d)
    ok("Berts Seite hat keine Unterseiten bekommen", kids(bert, "bos-b") == [])
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
    ok(f"Ziel: eigene Seite -> Buchseite darunter ({s})", s == 200 and p.get("parent_id") == mine, (d, p.get("parent_id")))


def part_limits(Ca):
    calls = [c for c in F.ann_calls if c[0] == 32]
    ok("seitenweise abgerufen: pageSize 100, Obergrenze 250 -> drei Seiten", calls[-3:] == [(32, 1, 100), (32, 2, 100), (32, 3, 100)]
       and len([c for c in calls if c[1] == 4]) == 0, calls)
    pid = kv(32)["page"]
    ch = kids(pid)
    ok("ohne TOC: Kapitelseiten aus den Markierungen (K0..K4)", [x[1] for x in ch] == ["K0", "K1", "K2", "K3", "K4"], ch)
    n = sum(1 for x in ch for b in page(Ca, x[0])[2] if b["type"] == "quote")
    ok("Obergrenze: 250 Zitate verteilt", n == 250, n)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 32})
    ok(f"Antwort meldet total 350 und truncated ({s})", s == 200 and d.get("total") == 350 and d.get("truncated") is True and d.get("added") == 0, d)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 33})
    lang = kids(d.get("page_id"))
    s2, p, bl = page(Ca, lang[0][0]) if lang else (0, {}, [])
    qs = [b for b in bl if b["type"] == "quote"]
    ok(f"Groessengrenze der Kapitelseite: abgeschnitten statt Fehler ({s})", s == 200 and d.get("truncated") is True and 0 < d.get("added") < 100
       and len(p.get("body") or "") < 200000 and len(qs) == d.get("added"), (d, len(p.get("body") or "")))
    ok("Textlaenge je Markierung begrenzt", qs and all(len(b["data"]["text"]) <= 3000 for b in qs) and qs[0]["data"]["text"].endswith("…"),
       len(qs[0]["data"]["text"]) if qs else None)


def part_parallel(Ca):
    """Zwei Prozesse legen dasselbe Buch gleichzeitig an (Server + eigener Prozess): Sperre in der Datenbank -> eine Seite."""
    import subprocess
    script = ("import sys, json; sys.path.insert(0, '.'); from saga import bookorbit_pages as bp\n"
              "try: print(json.dumps(bp.build('bos-a', 34)))\n"
              "except bp.Fail as f: print(json.dumps({'error': f.msg}))")
    pr = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=dict(os.environ))
    time.sleep(0.3)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 34})
    po, pe = pr.communicate(timeout=60)
    try:
        d2 = json.loads(po.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        d2 = {"raw": po.decode()[-300:], "err": pe.decode()[-300:]}
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM note WHERE uid='bos-a' AND title='Gleichzeitig' AND deleted_at IS NULL").fetchone()[0]
        k = c.execute("SELECT COUNT(*) FROM note WHERE uid='bos-a' AND title='K' AND deleted_at IS NULL").fetchone()[0]
    ok(f"zwei Prozesse gleichzeitig: genau eine Buch- und eine Kapitelseite ({s}, {n}, {k})", s == 200 and n == 1 and k == 1
       and d.get("page_id") == d2.get("page_id") and sorted([d.get("created"), d2.get("created")]) == [False, True], (d, d2))


def part_home(Ca):
    h = home()["page"]
    shelf = req("POST", "/api/notes", Ca, {"title": "Regal", "cover": None})[1]["id"]
    req("PATCH", f"/api/notes/{h}", Ca, {"title": "Meine Buecher"})
    req("POST", f"/api/notes/{h}/move", Ca, {"parent_id": shelf, "index": 0})
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 36})
    s2, p, _ = page(Ca, h)
    ok(f"Sammelseite umbenannt und verschoben: bleibt so ({s})", s == 200 and home()["page"] == h and p.get("title") == "Meine Buecher"
       and p.get("parent_id") == shelf, (home(), p.get("title"), p.get("parent_id")))
    s, _ = req("DELETE", f"/api/notes/{h}", Ca)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 37})
    h2 = home()["page"]
    s2, p, bl = page(Ca, h2)
    ok(f"Sammelseite geloescht -> neu angelegt, Buch wieder darin ({s})", s == 200 and h2 != h and p.get("title") == "Bücher" and p.get("parent_id") is None
       and parent_of(d.get("page_id")) == h2 and gal(bl) is not None and find(bl, x="books") is None, (d, h2, p.get("title")))


def part_convert(Ca):
    """Bestehende Sammelseite (Stand bis 9.11.: Einleitung + Liste) -> einmalig Unterseiten-Galerie (2.5, 9.11. abends)."""
    from saga import bookorbit_pages as BP
    intro_old = BP._intro("de", BP.INTRO_OLD)

    def setup(edit=None):
        h = req("POST", "/api/notes", Ca, {"title": "Bücher", "icon": "📚", "cover": None})[1]["id"]
        bp = [req("POST", "/api/notes", Ca, {"title": f"Altbuch {i}", "parent_id": h, "cover": None})[1]["id"] for i in range(3)]
        own = req("POST", "/api/notes", Ca, {"title": "Eigene Notiz", "parent_id": h, "cover": None})[1]["id"]
        items = [{"content": BP._san(BP._link(x, f"Altbuch {i}")), "meta": {}, "items": []} for i, x in reversed(list(enumerate(bp)))]
        li = {"type": "list", "data": {"style": "unordered", "meta": {}, "items": items, "bo": {"k": "_home", "x": "books", "h": BP._hl(items)}}}
        if edit == "items":
            li["data"]["items"] = items + [{"content": "Eigener Eintrag", "meta": {}, "items": []}]
        blocks = [{"type": "paragraph", "data": {"text": intro_old, "bo": {"k": "_home", "x": "intro", "h": BP._hn(intro_old)}}}]
        blocks += [] if edit == "deleted" else [li]
        blocks.append({"type": "paragraph", "data": {"text": "Mein Schlusswort"}})
        save(Ca, h, blocks)
        with connect() as c, c:
            c.execute("DELETE FROM user_kv WHERE uid='bos-a' AND k LIKE 'bookorbit_page:7%'")
            for i, x in enumerate(bp):
                c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("bos-a", f"bookorbit_page:7{i}", json.dumps({"page": x, "v": 2})))
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?,?,?)", ("bos-a", "bookorbit_notes_home",
                      json.dumps({"page": h, "moved": True, "x": ["intro", "books"], "listed": bp})))
        return h, bp, own

    def run():                                   # wie am Ende jedes Anlegens/Aktualisierens (ohne die Rate zu belasten)
        return 200 if BP._home("bos-a", "de") == home().get("page") else 500

    h, bp, own = setup()
    s = run()
    _, _, bl = page(Ca, h)
    hv = home()
    ok(f"Umstellung: unveraenderte Liste -> Unterseiten-Galerie an derselben Stelle, Einleitung neu, eigener Text bleibt ({s})",
       s == 200 and [b["type"] for b in bl] == ["paragraph", "widget", "paragraph"] and gal(bl) == 1 and "vorne dazu" in bl[0]["data"]["text"]
       and bl[2]["data"]["text"] == "Mein Schlusswort" and "gal" in hv.get("x", []) and hv.get("listed") == [], (bl, hv))
    order = [k[0] for k in kids(h)]
    ok("Umstellung: Buchseiten einmalig neueste zuerst, eigene Seite behaelt ihren Platz", order == [bp[2], bp[1], bp[0], own], (order, bp, own))
    revs0 = revs([h])
    run()
    ok("Umstellung idempotent (keine neue Revision, Reihenfolge bleibt)", revs([h]) == revs0 and [k[0] for k in kids(h)] == order, (revs([h]), revs0))
    req("POST", f"/api/notes/{bp[0]}/move", Ca, {"parent_id": h, "index": 0})
    _, _, bl = page(Ca, h)
    save(Ca, h, [b for b in bl if b.get("type") != "widget"])
    run()
    _, _, bl = page(Ca, h)
    ok("Galerie geloescht -> kommt nicht wieder; selbst umsortierte Reihenfolge bleibt", gal(bl) is None and kids(h)[0][0] == bp[0], (bl, kids(h)))
    h, bp, own = setup("items")
    run()
    _, _, bl = page(Ca, h)
    ok("Umstellung: bearbeitete Liste bleibt (wird nicht mehr nachgezogen), Galerie davor",
       [b["type"] for b in bl] == ["paragraph", "widget", "list", "paragraph"] and "Eigener Eintrag" in text_of(bl[2]), bl)
    h, bp, own = setup("deleted")
    run()
    _, _, bl = page(Ca, h)
    ok("Umstellung: Liste war geloescht -> keine Galerie aufgedraengt", gal(bl) is None and "gal" in home().get("x", []), (bl, home()))
    h, bp, own = setup()                         # Sammelseite vor der Umstellung selbst geloescht: Liste anzeigen legt sie nicht neu an
    req("DELETE", f"/api/notes/{h}", Ca)
    with connect() as c:
        n0 = c.execute("SELECT COUNT(*) FROM note WHERE uid='bos-a' AND parent_id IS NULL AND deleted_at IS NULL").fetchone()[0]
    s, _ = req("GET", "/api/bookorbit/notes/books", Ca)
    with connect() as c:
        n1 = c.execute("SELECT COUNT(*) FROM note WHERE uid='bos-a' AND parent_id IS NULL AND deleted_at IS NULL").fetchone()[0]
    ok(f"Umstellung: geloeschte Sammelseite kommt beim Anzeigen der Liste nicht wieder ({s})", s == 200 and n1 == n0
       and home().get("page") == h, (n0, n1, home()))
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid='bos-a' AND k LIKE 'bookorbit_page:7%'")


def part_trash(Ca, nid):
    s, _ = req("DELETE", f"/api/notes/{nid}", Ca)
    s, d = req("POST", "/api/bookorbit/notes", Ca, {"book_id": 31})
    ok(f"Buchseite im Papierkorb -> neue Buchseite mit allem ({s})", s == 200 and d.get("created") is True and d.get("page_id") != nid
       and d.get("added") == 12 and d.get("chapters_added") == 9, d)


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
    st = [req("GET", "/api/bookorbit/notes/books?q=x", Ca)[0] for _ in range(65)]
    ok("Liste/Suche je Konto begrenzt (429 nach 60 je Minute)", 429 in st and st.count(429) <= 65 - 50, st[-5:])


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
