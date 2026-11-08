"""BookOrbit Phase 1 (2.5, saga/bookorbit.py): Verbindung je Konto, Token-Pflege, Leseliste, Cover, Widget "Lesen".

Nie echtes Netz: ein kleines Fake-BookOrbit auf 127.0.0.1:BOOKORBIT_TEST_PORT (Standard 18645) - Login (nur "native"
liefert Tokens im Koerper), Refresh mit Rotation (altes Token danach ungueltig, Gnadenfrist hier 0 s = strenger als das
echte BookOrbit), Logout, POST /dashboard/widgets/batch, GET /books/<id>/thumbnail, eine Weiterleitung unter /redir.
Server mit kurzen Zeiten starten: SAGA_BOOKORBIT_CACHE_S=2 SAGA_BOOKORBIT_REFRESH_S=8 SAGA_BOOKORBIT_TICK_S=2.
Geprueft:
- SSRF: http ohne Freigabe, interne Adresse ohne Freigabe, private IP, Weiterleitung -> abgelehnt (Ziel nie erreicht).
- Login: falsches Passwort, Passwort-Anmeldung abgeschaltet (z. B. nur OIDC), Erfolg; nur das Refresh-Token gespeichert
  (verschluesselt), das Passwort nirgends (Datenbank-Abzug, Server-Log, STATE_DIR).
- Leseliste (Gerade am Lesen, Leseziel, Lesestraehne), Zwischenspeicher, Cover-Proxy (Bildpruefung, Cache-Header, nur mit
  dem eigenen Token: fremdes Buch 404, ohne Verbindung 409, ohne Anmeldung 401).
- Rotation: altes Token nach dem Erneuern ungueltig; gleichzeitige Erneuerung (Server-Abrufe parallel + Erneuerung aus
  einem ZWEITEN Prozess = Sperre in der Datenbank) verbrennt kein Token; Hintergrund-Erneuerung ohne Nutzung.
- 401 (Sitzung bei BookOrbit widerrufen) -> Zustand "expired", Widget-Hinweis; neu anmelden; Trennen meldet ab.
- Widget "reading": Varianten, geteilte Seite = jede Person ihre eigenen Daten, Veroeffentlichen/Foederation ohne
  Persoenliches, Bereinigung, Markdown-Text.
Wegwerf-Konten bo-a/bo-b, raeumt auf. Umgebung wie tests/widgets2.py (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, http.client, json, os, secrets, subprocess, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itsdangerous import TimestampSigner
import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import bookorbit, fed_pages, page_widgets  # noqa: E402

B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PORT = int(os.environ.get("BOOKORBIT_TEST_PORT", "18645"))
FAKE = f"http://127.0.0.1:{PORT}"
PW_A, PW_B = "Pw-Geheim-" + secrets.token_hex(6), "Pw-Bert-" + secrets.token_hex(6)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120
res = []
n500 = [0]


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:400]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, raw=False):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=40)
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
    ct = r.getheader("content-type", "")
    if raw:
        return r.status, out, dict(r.getheaders())
    if "json" in ct:
        try:
            out = json.loads(out)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        out = out.decode("utf-8", "replace")
    return r.status, out


# =====================================================================================================
# Fake-BookOrbit
# =====================================================================================================
class F:
    lock = threading.Lock()
    users = {"anna": {"pw": PW_A, "name": "Anna Buch", "books": {11: ("Der Nachtzug", ["Ida Feld"], 42.4, True), 12: ("Kurze Wege", [], 7, False)},
                      "goal": (12, 3), "streak": (4, 9, [True, False, True, True, True, True, True])},
             "bert": {"pw": PW_B, "name": "Bert Buch", "books": {21: ("Bergluft", ["Ole Stein"], 88, True)},
                      "goal": (None, 1), "streak": (0, 2, [False] * 7)}}
    sessions = {}            # sid -> {"user", "token", "revoked", "prev": {alt: (neu, zeit)}}
    access = {}              # zugriffstoken -> (sid, gueltig_bis)
    password_login = True
    access_ttl = 61.0        # Saga haelt das Zugriffstoken bis 60 s vor Ablauf -> hier ~1 s
    grace = 0.0              # echtes BookOrbit: 30 s (gleicher Nachfolger); hier streng
    delay = 0.0              # Refresh kuenstlich langsam (Rennen sichtbar machen)
    refreshes = 0
    reuse = 0                # altes (bereits getauschtes) Token vorgelegt
    logouts = 0
    redirect_target_hit = 0
    seen_bodies = []


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def _creds(sid):
    tok = secrets.token_hex(32)
    acc = "acc-" + secrets.token_hex(8)
    F.sessions[sid]["token"] = tok
    F.access[acc] = (sid, time.time() + F.access_ttl)
    return {"accessToken": acc, "accessTokenExpiresAt": _iso(time.time() + F.access_ttl), "refreshToken": tok,
            "refreshTokenExpiresAt": _iso(time.time() + 7 * 86400), "sessionId": sid}


class FH(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ct="application/json", extra=None):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        with F.lock:
            F.seen_bodies.append(raw)
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def user(self):
        a = (self.headers.get("Authorization") or "")[7:]
        with F.lock:
            hit = F.access.get(a)
            if not hit or hit[1] < time.time() or F.sessions[hit[0]]["revoked"]:
                return None
            return F.sessions[hit[0]]["user"]

    def do_POST(self):
        p = self.path
        if p.startswith("/redir/"):
            return self.send(302, {}, extra={"Location": FAKE + "/target" + p[6:]})
        if p.startswith("/target"):
            F.redirect_target_hit += 1
            return self.send(200, {})
        d = self.body()
        if p == "/api/v1/auth/login":
            if not F.password_login:
                return self.send(403, {"message": "Password authentication is disabled", "errorCode": "PASSWORD_AUTH_DISABLED", "statusCode": 403})
            u = F.users.get(d.get("username"))
            if not u or u["pw"] != d.get("password"):
                return self.send(401, {"message": "Invalid credentials", "statusCode": 401})
            with F.lock:
                sid = len(F.sessions) + 1
                F.sessions[sid] = {"user": d["username"], "token": None, "revoked": False, "prev": {}, "native": d.get("clientKind") == "native"}
                cr = _creds(sid)
            if d.get("clientKind") != "native":              # Web: Tokens als Cookie, nicht im Koerper
                return self.send(200, {"accessToken": cr["accessToken"], "accessTokenExpiresAt": cr["accessTokenExpiresAt"], "sessionId": sid})
            return self.send(200, dict(cr, user={"id": sid, "username": d["username"], "name": u["name"]}))
        if p == "/api/v1/auth/refresh":
            if F.delay:
                time.sleep(F.delay)
            tok = d.get("refreshToken") or ""
            with F.lock:
                F.refreshes += 1
                for sid, s in F.sessions.items():
                    if s["revoked"] or not s["native"]:
                        continue
                    if s["token"] == tok:
                        cr = _creds(sid)
                        s["prev"][tok] = (cr["refreshToken"], time.time())
                        return self.send(200, cr)
                    if tok in s["prev"]:
                        nxt, at = s["prev"][tok]
                        if time.time() - at <= F.grace and s["token"] == nxt:
                            acc = "acc-" + secrets.token_hex(8); F.access[acc] = (sid, time.time() + F.access_ttl)
                            return self.send(200, {"accessToken": acc, "accessTokenExpiresAt": _iso(time.time() + F.access_ttl),
                                                   "refreshToken": nxt, "refreshTokenExpiresAt": _iso(time.time() + 7 * 86400), "sessionId": sid})
                        F.reuse += 1
                        return self.send(401, {"message": "Unauthorized", "statusCode": 401})
            return self.send(401, {"message": "Unauthorized", "statusCode": 401})
        if p == "/api/v1/auth/logout":
            with F.lock:
                for s in F.sessions.values():
                    if s["token"] == d.get("refreshToken"):
                        s["revoked"] = True; F.logouts += 1
            return self.send(200, {})
        if p.startswith("/__revoke/"):
            with F.lock:
                for s in F.sessions.values():
                    if s["user"] == p[10:]:
                        s["revoked"] = True
            return self.send(200, {})
        if p == "/api/v1/dashboard/widgets/batch":
            un = self.user()
            if not un:
                return self.send(401, {"message": "Unauthorized", "statusCode": 401})
            u = F.users[un]
            items = []
            for w in d.get("widgets") or []:
                if w == "currently-reading":
                    data = {"books": [{"bookId": k, "title": v[0], "authors": v[1], "progress": v[2], "hasCover": v[3], "fileId": k * 10,
                                       "fileFormat": "EPUB", "readFileId": None, "readFileFormat": None, "readAlongFileId": None, "hasAudio": False}
                                      for k, v in u["books"].items()]}
                elif w == "reading-goal":
                    data = {"goalBooks": u["goal"][0], "completedBooks": u["goal"][1], "year": 2026}
                elif w == "reading-streak":
                    data = {"currentStreak": u["streak"][0], "longestStreak": u["streak"][1], "lastSevenDays": u["streak"][2]}
                else:
                    data = None
                items.append({"type": w, "data": data, "failed": data is None})
            return self.send(200, {"items": items})
        self.send(404, {"message": "Not Found", "statusCode": 404})

    def do_GET(self):
        p = self.path
        if p.startswith("/api/v1/books/") and p.endswith("/thumbnail"):
            un = self.user()
            if not un:
                return self.send(401, {"message": "Unauthorized", "statusCode": 401})
            bid = int(p.split("/")[4])
            b = F.users[un]["books"].get(bid)
            if not b or not b[3]:
                return self.send(404, {"message": f"No thumbnail for book {bid}", "statusCode": 404})
            return self.send(200, PNG if bid != 12 else b"<svg onload=alert(1)>", "image/jpeg")
        self.send(404, {"message": "Not Found", "statusCode": 404})


def cleanup():
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "note_publish", "public_name", "bookorbit_link"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'bo-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'bo-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'bo-%'")


def W(kind, cfg=None, bid=None, **kw):
    b = {"type": "widget", "data": dict({"v": 1, "kind": kind, "cfg": cfg or {}}, **kw)}
    if bid:
        b["id"] = bid
    return b


def save(A, nid, blocks):
    s, d = req("PATCH", f"/api/notes/{nid}", A, {"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    assert s == 200, (s, d)


def link(uid):
    with connect() as c:
        return c.execute("SELECT base_url, account, secret, state, refreshed FROM bookorbit_link WHERE uid=?", (uid,)).fetchone()


def wait_reading(ck_, pred, ms=8000):
    t0 = time.time()
    while True:
        s, r = req("GET", "/api/bookorbit/reading", ck_)
        if pred(r) or time.time() - t0 > ms / 1000:
            return s, r
        time.sleep(0.3)


# =====================================================================================================
def unit():
    d = page_widgets.clean_data({"kind": "reading", "cfg": {"show": "x", "limit": 99, "junk": 1}})
    ok("Bereinigung: unbekannte Variante -> current, Limit 3, Fremdes weg", d["cfg"] == {"show": "current", "limit": 3}, d)
    d = page_widgets.clean_data({"kind": "reading", "cfg": {"show": "streak", "limit": 5}})
    ok("Bereinigung: streak/5 bleibt", d["cfg"] == {"show": "streak", "limit": 5}, d)
    ok("reading ist eine vom Server gerechnete Art (KINDS2)", "reading" in page_widgets.KINDS2 and page_widgets.server_kind(d))
    w = page_widgets.compute(None, "@x@peer.example", "b", d)
    ok("Foederation: fremde Person -> no_access ohne Daten", w["error"] == "no_access" and w["data"] is None, w)
    w = page_widgets.compute(None, "bo-a", "b", d, public_owner="bo-a")
    ok("Veroeffentlichen: no_access", w["error"] == "no_access" and w["data"] is None, w)
    ok("Foederation: reading-Daten einer Gegenstelle werden verworfen",
       page_widgets.clean_remote_data("reading", {"state": "ok", "books": [{"title": "X"}]}) is None)
    x = fed_pages._w_widget_in({"kind": "reading", "value": 1, "data": {"state": "ok", "books": [{"title": "Leck"}]}})
    ok("Foederation: eingehendes W ohne data", x is not None and x.get("data") is None, x)
    for u, why in (("http://bookorbit.example.org", "http ohne Freigabe"), ("ftp://x.y", "Schema"), ("https://u:p@x.example", "Zugangsdaten"),
                   ("https://x.example/?a=1", "Query"), ("https://x.example:99999", "Port")):
        ok(f"norm_url lehnt ab: {why}", bookorbit.norm_url(u)[0] is None)
    ok("norm_url schneidet /api/v1 ab", bookorbit.norm_url("https://books.example.org/sub/api/v1/")[0] == "https://books.example.org/sub")
    data = {"state": "ok", "show": "current", "books": [{"title": "T1", "authors": ["A1"], "progress": 42.4}]}
    ok("Markdown-Text: Titel, Autor, Prozent", "T1 (A1) 42" in (page_widgets.value_text({"kind": "reading"}, {"data": data}, None) or ""))
    ok("Markdown-Text: ohne Verbindung nichts", page_widgets.value_text({"kind": "reading"}, {"data": {"state": "none"}}, None) is None)


def part_ssrf(A):
    # Port + 1: nie freigegeben (auch wenn der Server mit BOOKORBIT_URL = Fake laeuft, z. B. fuer den Browser-Test)
    for url, why in ((f"http://127.0.0.1:{PORT + 1}", "http ohne Freigabe"), (f"https://127.0.0.1:{PORT + 1}", "interne Adresse ohne Freigabe"),
                     ("https://10.1.2.3", "private IP"), ("https://169.254.169.254", "Metadaten-Adresse")):
        s, r = req("POST", "/api/bookorbit/connect", A, {"url": url, "username": "anna", "password": PW_A})
        ok(f"SSRF abgelehnt: {why} ({s})", s == 400, r)
    ok("dabei kein Aufruf beim Fake", not F.seen_bodies, len(F.seen_bodies))


def main():
    cleanup()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), FH); threading.Thread(target=srv.serve_forever, daemon=True).start()
    unit()
    for u in ("bo-a", "bo-b"):
        auth.create_user(u, "Bookorbit-Pass-123", u)
    A, Bk = ck("bo-a"), ck("bo-b")
    s, d = req("GET", "/api/version")
    ok("features bookorbit1", "bookorbit1" in d.get("features", []), d)
    s, d = req("GET", "/api/bookorbit/status")
    ok(f"ohne Anmeldung 401 ({s})", s == 401)
    s, d = req("GET", "/api/bookorbit/status", A)
    ok("Status: nicht verbunden", s == 200 and d["connected"] is False and d["state"] == "none", d)
    s, d = req("GET", "/api/bookorbit/reading", A)
    ok("Leseliste ohne Verbindung: state none", s == 200 and d == {"state": "none"}, d)
    prev_hosts = auth.settings(fresh=True).get("allowed_hosts") or ""
    try:
        part_ssrf(A)
        auth.save_setting("allowed_hosts", f"127.0.0.1:{PORT}")
        time.sleep(5.3)                                          # Einstellungen sind im Server 5 s zwischengespeichert
        s, r = req("POST", "/api/bookorbit/connect", A, {"url": FAKE + "/redir", "username": "anna", "password": PW_A})
        ok(f"Weiterleitung abgelehnt ({s}), Ziel nie erreicht", s == 400 and F.redirect_target_hit == 0, (r, F.redirect_target_hit))
        s, r = req("POST", "/api/bookorbit/connect", A, {"url": FAKE, "username": "anna", "password": "falsch"})
        ok(f"falsches Passwort -> 400 ({s})", s == 400 and "lehnt" in str(r), r)
        F.password_login = False
        s, r = req("POST", "/api/bookorbit/connect", A, {"url": FAKE, "username": "anna", "password": PW_A})
        ok(f"Passwort-Anmeldung abgeschaltet (nur OIDC) -> klare Meldung ({s})", s == 400 and "abgeschaltet" in str(r), r)
        F.password_login = True
        s, r = req("POST", "/api/bookorbit/connect", A, {"url": FAKE + "/api/v1/", "username": "anna", "password": PW_A})
        ok(f"verbunden ({s})", s == 200 and r.get("name") == "Anna Buch" and r.get("url") == FAKE, r)
        login = json.loads(F.seen_bodies[-1])
        ok("Login als nativer Client (Tokens im Koerper)", login.get("clientKind") == "native" and login.get("deviceLabel") == "Saga", login)
        L = link("bo-a")
        ok("gespeichert: Adresse, Name, Token verschluesselt (kein Klartext-Token)", L and L[0] == FAKE and L[1] == "Anna Buch" and L[3] == "ok"
           and L[2] and F.sessions[1]["token"] not in L[2], L)
        s, d = req("GET", "/api/bookorbit/status", A)
        ok("Status: verbunden als Anna Buch", d.get("connected") and d.get("state") == "ok" and d.get("name") == "Anna Buch", d)
        part_reading(A, Bk)
        part_rotation(A)
        part_parallel(A)
        part_widget(A, Bk)
        part_background()
        part_expired(A)
        part_password()
        part_disconnect(A)
    finally:
        auth.save_setting("allowed_hosts", prev_hosts)
        srv.shutdown()


def part_reading(A, Bk):
    s, d = req("GET", "/api/bookorbit/reading", A)
    books = d.get("books") or []
    ok(f"Gerade am Lesen: zwei Buecher in Reihenfolge ({s})", s == 200 and d.get("state") == "ok" and [b["id"] for b in books] == [11, 12], d)
    b = books[0] if books else {}
    ok("Buch: Titel, Autor, Fortschritt, Cover-URL ueber Saga, Format", b.get("title") == "Der Nachtzug" and b.get("authors") == ["Ida Feld"]
       and b.get("progress") == 42.4 and b.get("cover") == "/api/bookorbit/cover/11" and b.get("format") == "epub", b)
    ok("ohne Cover: cover null", len(books) > 1 and books[1]["cover"] is None, books)
    ok("Leseziel und Straehne", d.get("goal") == {"goal": 12, "done": 3, "year": 2026} and d.get("streak", {}).get("current") == 4
       and len(d["streak"]["last7"]) == 7, d)
    ok("keine BookOrbit-Adresse/Token in der Antwort", FAKE not in json.dumps(d) and "acc-" not in json.dumps(d))
    n = F.refreshes
    for _ in range(3):
        req("GET", "/api/bookorbit/reading", A)
    ok("Zwischenspeicher: kein erneuter Abruf/Refresh", F.refreshes == n)
    s, data, h = req("GET", "/api/bookorbit/cover/11", A, raw=True)
    ok(f"Cover: PNG mit privatem Cache-Header ({s})", s == 200 and data == PNG and h.get("content-type") == "image/png"
       and "private" in h.get("cache-control", "") and h.get("x-content-type-options") == "nosniff", (s, h))
    s, data, h = req("GET", "/api/bookorbit/cover/12", A, raw=True)
    ok(f"Cover: kein Bild (SVG/HTML) -> 404 ({s})", s == 404)
    s, data, h = req("GET", "/api/bookorbit/cover/21", A, raw=True)
    ok(f"Cover eines fremden Buchs (nicht in Annas Bibliothek) -> 404 ({s})", s == 404)
    s, _ = req("GET", "/api/bookorbit/cover/11", Bk)
    ok(f"Cover fuer Bert (nicht verbunden) -> 409 ({s})", s == 409)
    s, _ = req("GET", "/api/bookorbit/cover/11")
    ok(f"Cover ohne Anmeldung -> 401 ({s})", s == 401)
    for bad in ("0", "-1", "abc", "99999999999", "1%2F..%2Fx"):
        s, _ = req("GET", "/api/bookorbit/cover/" + bad, A)
        ok(f"Cover-ID {bad!r} abgelehnt ({s})", s in (400, 404))


def part_rotation(A):
    old = F.sessions[1]["token"]
    time.sleep(1.2)                                       # Zugriffstoken (Saga-Sicht) abgelaufen
    n = F.refreshes
    s, data, _ = req("GET", "/api/bookorbit/cover/11", A, raw=True)
    ok(f"nach Ablauf des Zugriffstokens: genau ein Refresh ({s})", s == 200 and F.refreshes == n + 1, (s, F.refreshes - n))
    new = F.sessions[1]["token"]
    ok("Refresh-Token rotiert und neu gespeichert", new != old and link("bo-a")[3] == "ok" and link("bo-a")[4])
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    c.request("POST", "/api/v1/auth/refresh", body=json.dumps({"refreshToken": old}), headers={"Content-Type": "application/json"})
    r = c.getresponse(); r.read()
    ok(f"altes Refresh-Token danach ungueltig ({r.status})", r.status == 401)
    F.reuse = 0


def part_parallel(A):
    """Zwei (und mehr) gleichzeitige Erneuerungen: Server-Threads und ein zweiter Prozess (Sperre in der Datenbank)."""
    F.delay = 0.4
    time.sleep(1.2)
    n = F.refreshes
    out = []
    ths = [threading.Thread(target=lambda: out.append(req("GET", "/api/bookorbit/cover/11", A, raw=True)[0])) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    ok(f"4 parallele Abrufe: alle ok, ein Refresh, nichts verbrannt ({out}, {F.refreshes - n})", out == [200] * 4 and F.refreshes == n + 1 and F.reuse == 0)
    # zweiter Prozess erneuert gleichzeitig mit dem Server (erzwungen) - die DB-Sperre muss beide nacheinander laufen lassen
    script = ("import sys; sys.path.insert(0, '.'); from saga import bookorbit; import threading\n"
              "r=[]\n"
              "def go():\n"
              "    try: bookorbit._refresh('bo-a', force=True); r.append('ok')\n"
              "    except bookorbit._Err as e: r.append(e.code)\n"
              "ts=[threading.Thread(target=go) for _ in range(3)]\n"
              "[t.start() for t in ts]; [t.join() for t in ts]; print(','.join(r))")
    time.sleep(1.2)
    p = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), env=dict(os.environ))
    out = []
    ths = [threading.Thread(target=lambda: out.append(req("GET", "/api/bookorbit/cover/11", A, raw=True)[0])) for _ in range(3)]
    time.sleep(0.2)
    for t in ths:
        t.start()
    po, pe = p.communicate(timeout=60)
    for t in ths:
        t.join()
    ok(f"zweiter Prozess + Server gleichzeitig: kein Token verbrannt (Prozess {po.decode().strip()}, Server {out}, reuse {F.reuse})",
       po.decode().strip() == "ok,ok,ok" and out == [200] * 3 and F.reuse == 0 and link("bo-a")[3] == "ok", pe.decode()[-400:])
    F.delay = 0.0
    time.sleep(2.2)
    s, d = req("GET", "/api/bookorbit/reading", A)
    ok("danach weiter verbunden und lesbar", d.get("state") == "ok" and d.get("books"), d)


def part_widget(A, Bk):
    page = req("POST", "/api/notes", A, {"title": "Leseecke"})[1]["id"]
    save(A, page, [W("reading", {"show": "current", "limit": 1}, bid="rc", title="LeseTitel"), W("reading", {"show": "goal"}, bid="rg"),
                   W("reading", {"show": "streak"}, bid="rs")])
    s, r = req("GET", f"/api/notes/{page}/widgets", A)
    w = (r.get("widgets") or {}) if isinstance(r, dict) else {}
    rc, rg, rs = w.get("rc") or {}, w.get("rg") or {}, w.get("rs") or {}
    ok(f"Widget current: 1 Buch + 1 weiteres ({s})", rc.get("kind") == "reading" and rc.get("error") is None and rc["data"]["state"] == "ok"
       and [b["title"] for b in rc["data"]["books"]] == ["Der Nachtzug"] and rc["data"]["more"] == 1 and rc["value"] == 2, rc)
    ok("Widget goal: 3 / 12", rg.get("data", {}).get("goal") == {"goal": 12, "done": 3, "year": 2026} and rg["value"] == 3 and rg["max"] == 12, rg)
    ok("Widget streak: 4 Tage", rs.get("data", {}).get("streak", {}).get("current") == 4 and rs["value"] == 4, rs)
    # geteilte Seite: Bert sieht seine eigenen Daten (erst: nicht verbunden)
    req("PUT", f"/api/notes/{page}/shares", A, {"type": "user", "target": "bo-b", "role": "view"})
    s, r = req("GET", f"/api/notes/{page}/widgets", Bk)
    raw = json.dumps(r, ensure_ascii=False)
    ok(f"geteilt, Bert nicht verbunden: state none, nichts von Anna ({s})", s == 200 and r["widgets"]["rc"]["data"]["state"] == "none"
       and "Nachtzug" not in raw and "Kurze Wege" not in raw, r)
    s, d = req("POST", "/api/bookorbit/connect", Bk, {"url": FAKE, "username": "bert", "password": PW_B})
    ok(f"Bert verbunden ({s})", s == 200, d)
    leak = False
    for _ in range(3):
        for who, c in (("a", A), ("b", Bk)):
            s, r = req("GET", f"/api/notes/{page}/widgets", c)
            raw = json.dumps(r, ensure_ascii=False)
            leak |= (who == "b" and ("Nachtzug" in raw or "Kurze" in raw)) or (who == "a" and "Bergluft" in raw)
    s, r = req("GET", f"/api/notes/{page}/widgets", Bk)
    ok("Leck-Test (3 Runden abwechselnd): jede Person nur ihre eigenen Buecher", not leak and
       [b["title"] for b in r["widgets"]["rc"]["data"]["books"]] == ["Bergluft"] and r["widgets"]["rg"]["data"]["goal"]["goal"] is None, r)
    s, _, _ = req("GET", "/api/bookorbit/cover/11", Bk, raw=True)
    ok(f"Bert: Annas Cover 11 nicht erreichbar ({s})", s == 404)
    s, _, _ = req("GET", "/api/bookorbit/cover/21", Bk, raw=True)
    ok(f"Bert: eigenes Cover 21 ({s})", s == 200)
    s, r = req("POST", f"/api/notes/{page}/widgets/preview", A, {"block": W("reading", {"show": "streak"}, bid="pv")})
    ok(f"Vorschau beim Einrichten ({s})", s == 200 and r["widget"]["data"]["streak"]["longest"] == 9, r)
    # Veroeffentlichen: nichts Persoenliches
    s, d = req("PUT", f"/api/notes/{page}/publish", A, {"slug": "leseecke", "name": "bo-anna"})
    url = urllib.parse.urlparse(str((d or {}).get("url") or "")).path if s == 200 else ""
    s, html = req("GET", url) if url else (s, "")
    html = str(html)
    ok(f"veroeffentlicht ({s}) ohne Widget, Titel, Buecher, Cover", s == 200 and "LeseTitel" not in html and "Nachtzug" not in html
       and "bookorbit" not in html.lower() and "Leseziel" not in html, html[:300])
    req("DELETE", f"/api/notes/{page}/publish", A)
    # Markdown-Export der eigenen Seite (eigene Daten)
    s, md, _ = req("GET", f"/api/notes/{page}/export", A, raw=True)
    md = md.decode("utf-8", "replace")
    ok(f"Markdown-Export: eigene Leseliste als Text ({s})", s == 200 and "Der Nachtzug" in md and "3 von 12" in md, md[:300])


def part_background():
    """Hintergrund: Token ohne Nutzung erneuert (Server mit SAGA_BOOKORBIT_REFRESH_S=8, TICK_S=2)."""
    sid = max(k for k, v in F.sessions.items() if v["user"] == "bert" and not v["revoked"])
    t0, n0 = F.sessions[sid]["token"], link("bo-b")[4]
    deadline = time.time() + 25
    while time.time() < deadline and F.sessions[sid]["token"] == t0:
        time.sleep(0.5)
    ok("Hintergrund-Erneuerung ohne Nutzung (Token rotiert, refreshed weiter)", F.sessions[sid]["token"] != t0 and (link("bo-b")[4] or 0) > (n0 or 0)
       and link("bo-b")[3] == "ok" and F.reuse == 0, (F.reuse, link("bo-b")))


def part_expired(A):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    c.request("POST", "/__revoke/anna", body=b"{}", headers={"Content-Type": "application/json"}); c.getresponse().read()
    s, d = wait_reading(A, lambda r: r.get("state") == "expired")
    ok(f"Sitzung bei BookOrbit widerrufen -> state expired ({s})", d.get("state") == "expired", d)
    L = link("bo-a")
    ok("abgelaufen: Token geloescht, Zeile bleibt (Adresse/Name fuer den Hinweis)", L and L[3] == "expired" and L[2] is None, L)
    s, d = req("GET", "/api/bookorbit/status", A)
    ok("Status: connected, state expired", d.get("connected") is True and d.get("state") == "expired", d)
    s, _ = req("GET", "/api/bookorbit/cover/11", A)
    ok(f"Cover bei abgelaufener Anmeldung -> 409 ({s})", s == 409)
    s, d = req("POST", "/api/bookorbit/connect", A, {"url": FAKE, "username": "anna", "password": PW_A})
    s2, d2 = wait_reading(A, lambda r: r.get("state") == "ok")
    ok(f"neu anmelden -> wieder ok ({s})", s == 200 and d2.get("state") == "ok", (d, d2))


def part_password():
    dump = subprocess.run(["/usr/lib/postgresql/16/bin/pg_dump", os.environ["DATABASE_URL"]], capture_output=True, timeout=120)
    ok("Datenbank-Abzug lesbar", dump.returncode == 0 and len(dump.stdout) > 1000, dump.stderr[-300:])
    ok("Passwort nirgends in der Datenbank", PW_A.encode() not in dump.stdout and PW_B.encode() not in dump.stdout)
    toks = [s["token"] for s in F.sessions.values() if s["token"]]
    ok("Refresh-Tokens nicht im Klartext in der Datenbank", not any(t.encode() in dump.stdout for t in toks))
    hit = []
    sd = os.environ.get("STATE_DIR") or ""
    for root, _, files in os.walk(sd) if sd else []:
        for f in files:
            pth = os.path.join(root, f)
            try:
                if os.path.getsize(pth) < 50 * 1024 * 1024:
                    with open(pth, "rb") as fh:
                        b = fh.read()
                    if PW_A.encode() in b or PW_B.encode() in b:
                        hit.append(pth)
            except OSError:
                pass
    ok(f"Passwort nirgends in STATE_DIR (Logs, Dateien) ({sd or '-'})", sd and not hit, hit)


def part_disconnect(A):
    lo = F.logouts
    s, d = req("POST", "/api/bookorbit/disconnect", A)
    ok(f"Trennen: Zeile weg, bei BookOrbit abgemeldet ({s})", s == 200 and d.get("logged_out") is True and link("bo-a") is None and F.logouts == lo + 1, d)
    s, d = req("GET", "/api/bookorbit/reading", A)
    ok("danach state none", d == {"state": "none"}, d)
    s, d = req("POST", "/api/bookorbit/disconnect", ck("bo-b"))
    ok("Bert getrennt", s == 200 and link("bo-b") is None)


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
    print(f"\nBOOKORBIT {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
