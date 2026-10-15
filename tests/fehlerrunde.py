"""Regressionstests der Fehlerrunde nach dem Bughunt 2.0 (BUG-xxx aus der Triage).

Wegwerf-Konten fr-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, contextlib, http.client, io, json, os, socket, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Fehler-Pass-1234"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u, **extra):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, **extra}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw_body=None):
    """-> (status, daten: JSON, Text oder Bytes)"""
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw_body = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw_body, headers=h)
    r = c.getresponse(); data = r.read()
    ct = r.getheader("content-type") or ""
    if "json" in ct:
        try:
            data = json.loads(data)
        except ValueError:
            pass
    elif ct.startswith("text/"):
        data = data.decode("utf-8", "replace")
    return r.status, data


def raw(m, p, cookie, text):
    """Roher JSON-Text (z. B. mit \\u0000 oder Infinity, das json.dumps so nicht erzeugt)."""
    return req(m, p, cookie, hdr={"Content-Type": "application/json"}, raw_body=text.encode())


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'fr-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("task", "note", "note_version", "user_kv", "device_login", "app_token"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'fr-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'fr-%' OR target LIKE 'fr-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fr-%'")


def schema_of(cookie, nid):
    return req("GET", f"/api/notes/{nid}", cookie)[1]["schema"]


def t_bug001(A, Bk):
    """Veraltetes Schema loescht keine Spalten/Optionen, die inzwischen jemand anderes angelegt hat."""
    db = req("POST", "/api/notes", A, {"title": "FR Datenbank", "kind": "database"})[1]["id"]
    s, _ = req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "fr-b", "role": "edit"})
    ok(f"BUG-001: Datenbank mit B geteilt ({s})", s == 200)
    s, d = req("PATCH", f"/api/notes/{db}", A, {"schema": {"props": [
        {"id": "pa", "name": "Alt", "type": "text"},
        {"id": "ps", "name": "Status", "type": "select", "options": [{"id": "o1", "name": "Offen", "color": "gray"}]}]}})
    ok(f"BUG-001: Schema-PATCH liefert srev ({s}, {d})", s == 200 and isinstance(d.get("srev"), int))
    stale = schema_of(A, db)                                  # Stand von A (Tab offen)
    row = req("POST", "/api/notes", A, {"title": "Zeile 1", "parent_id": db})[1]["id"]
    # B legt Spalte und Option an und fuellt beide
    sb = schema_of(Bk, db)
    sb["props"].append({"id": "pb", "name": "Von B", "type": "text"})
    next(p for p in sb["props"] if p["id"] == "ps")["options"].append({"id": "o2", "name": "Fertig", "color": "green"})
    s, _ = req("PATCH", f"/api/notes/{db}", Bk, {"schema": sb})
    s2, _ = req("PATCH", f"/api/notes/{row}", Bk, {"props": {"pb": "Wert von B", "ps": "o2"}})
    ok(f"BUG-001: B legt Spalte/Option an und fuellt sie ({s}, {s2})", s == 200 and s2 == 200)
    # A benennt mit veraltetem Schema eine Spalte um
    stale["props"][0]["name"] = "Alt umbenannt"
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": stale})
    now = schema_of(A, db)
    ids = [p["id"] for p in now["props"]]
    opts = [o["id"] for o in next(p for p in now["props"] if p["id"] == "ps")["options"]]
    ok(f"BUG-001: Umbenennen mit altem Stand ({s}) behaelt Spalte/Option von B: {ids} {opts}",
       s == 200 and ids == ["pa", "ps", "pb"] and opts == ["o1", "o2"] and now["props"][0]["name"] == "Alt umbenannt")
    props = req("GET", f"/api/notes/{row}", A)[1]["props"]
    ok(f"BUG-001: Werte von B bleiben erhalten: {props}", props.get("pb") == "Wert von B" and props.get("ps") == "o2")
    # Mit aktuellem Stand loescht A die Spalte von B ausdruecklich -> weg
    cur = schema_of(A, db)
    cur["props"] = [p for p in cur["props"] if p["id"] != "pb"]
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": cur})
    ok(f"BUG-001: mit aktuellem Stand ausdruecklich geloescht ({s})",
       s == 200 and [p["id"] for p in schema_of(A, db)["props"]] == ["pa", "ps"] and "pb" not in req("GET", f"/api/notes/{row}", A)[1]["props"])
    # Eigene neue Spalte im selben (nicht neu geladenen) Stand wieder loeschen: klappt (Web/Linux laden nicht nach)
    mine = schema_of(A, db)
    mine["props"].append({"id": "pm", "name": "Meine", "type": "text"})
    req("PATCH", f"/api/notes/{db}", A, {"schema": mine})
    mine["props"] = [p for p in mine["props"] if p["id"] != "pm"]
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": mine})
    ok(f"BUG-001: eigene Spalte ohne Neuladen wieder geloescht ({s})", s == 200 and "pm" not in [p["id"] for p in schema_of(A, db)["props"]])
    # Aeltere Clients ohne srev: Schema ohne srev/_since -> eigene Spalten weiter loeschbar
    plain = {"props": [p for p in schema_of(A, db)["props"] if p["id"] != "pa"]}
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": plain})
    ok(f"BUG-001: ohne srev (aeltere App) eigene Spalte geloescht ({s})", s == 200 and "pa" not in [p["id"] for p in schema_of(A, db)["props"]])
    ok("BUG-001: kein Anmeldename im Schema", "fr-a" not in json.dumps(schema_of(A, db)) and "fr-b" not in json.dumps(schema_of(A, db)))


def t_bug002(A, Bk):
    """Geraet abmelden beendet dessen Sitzung sofort."""
    from saga.app_login import _devlogin_db
    with connect() as c, c:
        _devlogin_db(c)
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES (?,?,?,?,?,?,?)",
                        ("fr-a", "fr-a", "FR Testgeraet", "fr-hash-" + str(time.time()), "hkd_frtest", time.time(), time.time())).lastrowid
    D = ck("fr-a", did=did)
    ok("BUG-002: Geraete-Sitzung gilt", req("GET", "/api/notes", D)[0] == 200)
    s, _ = req("DELETE", f"/api/settings/devices/{did}", A)
    ok(f"BUG-002: Geraet abgemeldet ({s})", s == 200)
    s, _ = req("GET", "/api/notes", D)
    ok(f"BUG-002: Sitzung des abgemeldeten Geraets -> {s}", s == 401)
    ok("BUG-002: Browser-Sitzung (ohne Geraet) bleibt", req("GET", "/api/notes", A)[0] == 200)
    ok("BUG-002: unbekanntes Geraet in der Sitzung -> 401", req("GET", "/api/notes", ck("fr-a", did=987654321))[0] == 401)


def t_bug007(A, Bk):
    """NUL, einzelne Surrogate, NaN/Infinity: kein 500 mehr."""
    nid = req("POST", "/api/notes", A, {"title": "FR Eingabe"})[1]["id"]
    s, _ = raw("PATCH", f"/api/notes/{nid}", A, '{"title": "a\\u0000b"}')
    ok(f"BUG-007: Titel mit NUL -> {s}", s == 200 and req("GET", f"/api/notes/{nid}", A)[1]["title"] == "ab")
    s, _ = raw("PATCH", f"/api/notes/{nid}", A, '{"title": "Halb \\ud83d x"}')
    ok(f"BUG-007: Titel mit halbem Emoji -> {s}", s == 200 and req("GET", f"/api/notes/{nid}", A)[1]["title"] == "Halb \ufffd x")
    s, _ = raw("POST", "/api/tasks", A, '{"text": "x", "prio": Infinity}')
    ok(f"BUG-007: Aufgabe mit Infinity -> {s}", s == 400)
    s, _ = raw("POST", "/api/tasks", A, '{"text": "x", "prio": NaN}')
    ok(f"BUG-007: Aufgabe mit NaN -> {s}", s == 400)
    s, _ = raw("POST", "/api/notes/search", A, '{"q": "a\\u0000b"}')
    ok(f"BUG-007: Suche mit NUL -> {s}", s < 500)
    s, _ = req("GET", "/api/share/targets?q=ab%00c", A)
    ok(f"BUG-007: Query-Parameter mit %00 -> {s}", s < 500)
    s, _ = raw("POST", "/api/notes", A, '{"title": "\\udc00"}')
    s2, _ = raw("POST", "/api/notes", A, '{"title": "x", "tags": ["\\ud800"]}')
    ok(f"BUG-007: Seite mit Surrogat in Titel bzw. Schlagwort -> {s}, {s2}", s == 200 and s2 < 500)


class Stream:
    """Kleiner SSE-Leser (wie tests/live.py): Ereignisse in self.ev [(typ, daten)]."""

    def __init__(self, cookie):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
        self.r = self.c.getresponse()
        self.ev, self.status = [], self.r.status
        if self.status == 200:
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

    def wait(self, typ, pred=None, since=0, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ and (pred is None or pred(e[1])):
                    return e[1]
            time.sleep(0.05)
        return None

    def close(self):
        try:
            self.c.sock.shutdown(socket.SHUT_RDWR)
            self.c.close()
        except Exception:  # noqa: BLE001
            pass


def t_bug032_033(A, Bk):
    """Live: Aenderung desselben Kontos auf anderem Geraet kommt an, nicht an die ausloesende Sitzung; Teilbaum geloescht."""
    A1, A2 = ck("fr-a", lo="fr-sitz-1"), ck("fr-a", lo="fr-sitz-2")
    pg = req("POST", "/api/notes", A1, {"title": "FR Live"})[1]["id"]
    sub = req("POST", "/api/notes", A1, {"title": "FR Live Unterseite", "parent_id": pg})[1]["id"]
    s2, s1 = Stream(A2), Stream(A1)
    ok(f"BUG-032: zwei Sitzungen verbunden ({s1.status}, {s2.status})", s1.wait("hello") is not None and s2.wait("hello") is not None)
    req("POST", "/api/presence", A2, {"note_id": pg})
    req("POST", "/api/presence", A2, {"note_id": sub})
    time.sleep(1.1)
    req("POST", "/api/presence", A1, {"note_id": pg})
    m1, m2 = len(s1.ev), len(s2.ev)
    s, d = req("PATCH", f"/api/notes/{pg}", A1, {"title": "FR Live neu"})
    nc = s2.wait("note.changed", lambda x: x["id"] == pg, since=m2)
    ok(f"BUG-032: andere Sitzung desselben Kontos bekommt note.changed ({nc})", s == 200 and nc and nc["rev"] == d["rev"])
    ok("BUG-032: ausloesende Sitzung bekommt es nicht", s1.wait("note.changed", lambda x: x["id"] == pg, since=m1, timeout=1.5) is None)
    m2 = len(s2.ev)
    s, _ = req("DELETE", f"/api/notes/{pg}", A1)
    g = s2.wait("note.changed", lambda x: x["id"] == sub and x.get("gone"), since=m2)
    ok(f"BUG-033: Papierkorb -> auch Unterseite 'gone' ({s}, {g})", s == 200 and g is not None)
    m2 = len(s2.ev)
    s, _ = req("POST", f"/api/trash/{pg}/restore", A1)
    r = s2.wait("note.changed", lambda x: x["id"] == pg and not x.get("gone"), since=m2)
    ok(f"BUG-033: Wiederherstellen -> note.changed ({s}, {r})", s == 200 and r is not None)
    s1.close(); s2.close()


def t_bug009(A, Bk):
    """Fehler eines Bus-Zuhoerers rollt nur dessen Teil zurueck; kaputte Transaktion meldet beim Commit einen Fehler."""
    from saga import bus

    def boom(c, **_):
        c.execute("SELECT * FROM gibt_es_nicht_fr")
    bus.on("fr.test", boom)
    with connect() as c, c:
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("fr-a", "fr-bus", "1"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):   # erwartete Fehlerausgabe
            bus.fire("fr.test", c)
    with connect() as c:
        got = c.execute("SELECT v FROM user_kv WHERE uid='fr-a' AND k='fr-bus'").fetchone()
    ok(f"BUG-009: Zuhoerer-Fehler -> Aenderung trotzdem gespeichert ({got})", got == ("1",))
    raised = False
    try:
        with connect() as c, c:
            c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,?)", ("fr-a", "fr-bus2", "1"))
            try:
                c.execute("SELECT * FROM gibt_es_nicht_fr")
            except Exception:  # noqa: BLE001 - absichtlich verschluckt
                pass
    except RuntimeError:
        raised = True
    with connect() as c:
        got = c.execute("SELECT v FROM user_kv WHERE uid='fr-a' AND k='fr-bus2'").fetchone()
    ok(f"BUG-009: kaputte Transaktion -> Fehler statt still zurueckgerollt ({raised}, {got})", raised and got is None)


def t_bug008(A, Bk):
    from saga.core import parse_json
    ok("BUG-008: Gegenstellen-JSON mit halbem Emoji wird bereinigt", parse_json('{"t": "Halb \\ud83d", "x": ["\\u0000a"]}') == {"t": "Halb \ufffd", "x": ["a"]})
    try:
        parse_json('{"n": NaN}')
        ok("BUG-008: NaN abgelehnt", False)
    except ValueError:
        ok("BUG-008: NaN abgelehnt", True)


def t_bug038_083_039_090(A, Bk):
    s, d = req("POST", "/api/notes", A, {"title": "A\u202etxet\nzeile2\u200b\x01"})
    ti = req("GET", f"/api/notes/{d['id']}", A)[1]["title"] if s == 200 else None
    ok(f"BUG-038: Titel ohne Bidi-/Steuerzeichen ({ti!r})", ti == "Atxet zeile2")
    s, d = req("POST", "/api/notes", A, {"title": "Familie \U0001F468\u200d\U0001F469"})
    ok("BUG-038: ZWJ in Emoji-Folgen bleibt", req("GET", f"/api/notes/{d['id']}", A)[1]["title"] == "Familie \U0001F468\u200d\U0001F469")
    s, d = req("POST", "/api/notes", A, {"title": None})
    ok(f"BUG-083: Titel null -> leer ({s})", s == 200 and req("GET", f"/api/notes/{d['id']}", A)[1]["title"] == "")
    ok("BUG-083: Titel als Liste -> 400", req("POST", "/api/notes", A, {"title": ["x"]})[0] == 400)
    ok("BUG-083: Titel als Wahrheitswert -> 400", req("PATCH", f"/api/notes/{d['id']}", A, {"title": True})[0] == 400)
    ok("BUG-083: Aufgabe mit Text null -> 400 (nicht 'None')", req("POST", "/api/tasks", A, {"text": None})[0] == 400)
    ok("BUG-083: KI-Frage als Liste -> 400", req("POST", "/api/ai/ask", A, {"q": ["x"]})[0] in (400, 403, 404))
    ok("BUG-083: Anwesenheit mit note_id 1.5/true -> 400",
       req("POST", "/api/presence", A, {"note_id": 1.5})[0] == 400 and req("POST", "/api/presence", A, {"note_id": True})[0] == 400)
    s, d = req("PATCH", f"/api/notes/{d['id']}", A, {"parent_id": "x"})
    ok(f"BUG-084: keine rohe Python-Meldung ({s}, {d})", s == 400 and "invalid literal" not in json.dumps(d))
    ok(f"BUG-085: Umlaute in deutschen Meldungen ({d})", d.get("error") == "ungültige Angabe")
    pg = req("POST", "/api/notes", A, {"title": "FR Klartext"})[1]["id"]
    body = json.dumps({"blocks": [{"id": "k1", "type": "paragraph", "data": {"text": "a<b c Ende"}},
                                  {"id": "k2", "type": "paragraph", "data": {"text": "Mail <bob@x.de> Ende <script>x</script><b>fett</b>"}},
                                  {"id": "k3", "type": "paragraph", "data": {"text": '<a href="/notes#1234567890123">lang</a>'}}]})
    req("PATCH", f"/api/notes/{pg}", A, {"body": body, "format": "blocks"})
    bl = json.loads(req("GET", f"/api/notes/{pg}", A)[1]["body"])["blocks"]
    ok(f"BUG-039: '<' + Buchstabe bleibt Text ({bl[0]['data']['text']!r})", bl[0]["data"]["text"] == "a&lt;b c Ende")
    ok(f"BUG-039: <bob@x.de> bleibt, <script> weg, <b> bleibt ({bl[1]['data']['text']!r})",
       "&lt;bob@x.de&gt; Ende" in bl[1]["data"]["text"] and "<script" not in bl[1]["data"]["text"] and "<b>fett</b>" in bl[1]["data"]["text"])
    ok(f"BUG-090: lange Seiten-ID im Verweis bleibt ({bl[2]['data']['text']!r})", 'href="/notes#1234567890123"' in bl[2]["data"]["text"])


def t_bug040(A, Bk):
    from saga import ai
    req("POST", "/api/notes", A, {"title": "Zebrafinken Futterplan"})
    ctx, srcs = ai.gather("fr-a", "Zebrafinken", 8000)
    ok(f"BUG-040: Seite nur mit Titel wird gefunden ({srcs})", any("Zebrafinken" in (s.get("title") or "") for s in srcs))


def t_bug041(A, Bk):
    codes = [req("PUT", "/api/push/settings", A, b) for b in (
        {"quiet": {"tz": ["Europe/Berlin"]}}, {"quiet": {"tz": {"a": 1}}}, {"quiet": {"from": ["08:00"]}}, {"quiet": {"on": "yes"}},
        {"quiet": {"tz": "Mars/Olympus"}})]
    ok(f"BUG-041: Push-Einstellungen mit Liste/Objekt -> 400 statt 500 ({[c[0] for c in codes]})", all(c[0] == 400 for c in codes))
    ok(f"BUG-041: passende Meldungen ({[c[1].get('error') for c in codes]})",
       codes[3][1].get("error") == "ungültige Angabe" and "Zeitzone" in codes[4][1].get("error", "") and "HH:MM" in codes[2][1].get("error", ""))
    s, _ = req("POST", "/api/push/subscriptions", A, {"type": ["webpush"]})
    ok(f"BUG-041: Abo mit Art als Liste -> kein 500 ({s})", s < 500)


def t_bug046(A, Bk):
    from saga.app_login import _devlogin_db
    with connect() as c, c:
        _devlogin_db(c)
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created, last_used) VALUES (?,?,?,?,?,?,?)",
                        ("fr-a", "fr-a", "FR Geraet", "fr-hash46-" + str(time.time()), "hkd_fr46", time.time(), time.time())).lastrowid
    d = req("GET", "/api/settings/apps", ck("fr-a", did=did))[1]
    mine = [x for x in d.get("devices", []) if x["id"] == did]
    ok(f"BUG-046: Geraeteliste kennzeichnet dieses Geraet ({mine})", mine and mine[0]["current"] is True and mine[0]["prefix"] == "hkd_fr46")
    ok("BUG-046: aus dem Browser kein Geraet als aktuell markiert", not any(x["current"] for x in req("GET", "/api/settings/apps", A)[1]["devices"]))


def t_bug049_092(A, Bk):
    A1, A2 = ck("fr-a", lo="fr-db-1"), ck("fr-a", lo="fr-db-2")
    db = req("POST", "/api/notes", A1, {"title": "FR Live-DB", "kind": "database"})[1]["id"]
    row = req("POST", "/api/notes", A1, {"title": "Zeile", "parent_id": db})[1]["id"]
    s2 = Stream(A2)
    s2.wait("hello")
    req("POST", "/api/presence", A2, {"note_id": db})
    m = len(s2.ev)
    req("PATCH", f"/api/notes/{row}", A1, {"title": "Zeile neu"})
    e = s2.wait("note.changed", lambda x: x["id"] == db, since=m)
    ok(f"BUG-049: Zeile geaendert -> note.changed an Betrachter der Datenbank ({e})", e is not None and e.get("row") == row)
    m = len(s2.ev)
    req("POST", "/api/notes", A1, {"title": "Zeile 2", "parent_id": db})
    ok("BUG-049: neue Zeile -> note.changed an Betrachter der Datenbank", s2.wait("note.changed", lambda x: x["id"] == db, since=m) is not None)
    s2.close()
    pg = req("POST", "/api/notes", A, {"title": "FR Versionen"})[1]["id"]
    body = json.dumps({"blocks": [{"id": "v1", "type": "paragraph", "data": {"text": "Absatz eins"}},
                                  {"id": "v2", "type": "paragraph", "data": {"text": "Absatz zwei " + "x" * 500}}]})
    req("PATCH", f"/api/notes/{pg}", A, {"body": body, "format": "blocks"})
    req("PATCH", f"/api/notes/{pg}", A, {"body": json.dumps({"blocks": []}), "format": "blocks"})     # stark gekuerzt -> Version
    vs = req("GET", f"/api/notes/{pg}/versions", A)[1]
    ok(f"BUG-092: eigene Fassung als 'me' gekennzeichnet ({[v.get('by') for v in vs]})", vs and vs[0].get("by") == "me")
    txt = req("GET", f"/api/notes/{pg}/versions/{vs[0]['id']}", A)[1].get("text", "") if vs else ""
    ok(f"BUG-092: Vorschau trennt Absaetze ({txt[:30]!r})", txt.startswith("Absatz eins\nAbsatz zwei"))


# --- weitere Tests oberhalb einfuegen ----------------------------------------------------------------------


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en, prev_scope = prev.get("share_enabled", True), prev.get("share_group_scope", False)
    auth.save_setting("share_enabled", True); auth.save_setting("share_group_scope", False)
    auth.create_user("fr-a", PW, "Fiona Fehler")
    auth.create_user("fr-b", PW, "Bruno Fehler")
    A, Bk = ck("fr-a"), ck("fr-b")
    time.sleep(WAIT)
    try:
        for name, fn in [(n, f) for n, f in globals().items() if n.startswith("t_bug") and callable(f)]:
            try:
                fn(A, Bk)
            except Exception as ex:  # noqa: BLE001 - ein Test bricht nicht alle ab
                ok(f"{name}: Ausnahme {type(ex).__name__}: {ex}", False)
    finally:
        auth.save_setting("share_enabled", prev_en); auth.save_setting("share_group_scope", prev_scope)
        cleanup()
    print(f"\nFEHLERRUNDE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
