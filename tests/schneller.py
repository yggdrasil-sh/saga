"""Schneller (Phase 1): gzip fuer JSON/Text (nicht Live-Kanal, Bilder, Teilantworten), Papierkorb-Index und -Frist,
getrennte Grenzen fuer Seiten und Datenbank-Eintraege (Probe-Instanz)."""
import base64, gzip, http.client, json, os, sqlite3, sys, time, urllib.parse
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A = ck("hk-test-a"); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
U = urllib.parse.urlparse(B)


def raw(m, p, b=None, gz=True, extra=None, read=True):
    """-> (status, headers{klein}, rohe bytes, entpackte bytes)"""
    c = http.client.HTTPConnection(U.hostname, U.port, timeout=20)
    h = {"Host": HOST, "Cookie": "saga=" + A, "X-Saga": "1"}
    if gz:
        h["Accept-Encoding"] = "gzip"
    if b is not None:
        h["Content-Type"] = "application/json"
    h.update(extra or {})
    c.request(m, p, body=json.dumps(b) if b is not None else None, headers=h)
    r = c.getresponse()
    hd = {k.lower(): v for k, v in r.getheaders()}
    if not read:
        return r.status, hd, r, c
    data = r.read(); c.close()
    return r.status, hd, data, gzip.decompress(data) if hd.get("content-encoding") == "gzip" else data


def call(m, p, b=None):
    s, _, _, d = raw(m, p, b, gz=False)
    try:
        return s, json.loads(d)
    except ValueError:
        return s, None


res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    ids = [call("POST", "/api/notes", {"title": f"Schnell-Testseite {i} " + "x" * 40})[1]["id"] for i in range(30)]
    # --- gzip ---------------------------------------------------------------------------------------------------
    s, h, data, plain = raw("GET", "/api/notes")
    s2, h2, data2, _ = raw("GET", "/api/notes", gz=False)
    ok(f"Seitenbaum mit gzip ({len(data)} statt {len(plain)} Bytes)", s == 200 and h.get("content-encoding") == "gzip" and len(data) < len(plain))
    ok("gzip: Vary: Accept-Encoding, Content-Length stimmt", "accept-encoding" in h.get("vary", "").lower() and int(h.get("content-length", -1)) == len(data))
    ok("gzip: gleicher Inhalt wie ohne", json.loads(plain) == json.loads(data2) and {i for i in ids} <= {p["id"] for p in json.loads(plain)})
    ok("Ohne Accept-Encoding: unkomprimiert", s2 == 200 and "content-encoding" not in h2)
    s, h, data, _ = raw("GET", "/api/notes", extra={"Accept-Encoding": "gzip;q=0"})
    ok("Accept-Encoding gzip;q=0: unkomprimiert", s == 200 and "content-encoding" not in h)
    s, h, data, _ = raw("GET", "/api/version")
    ok(f"Kleine Antwort (< 1 KB, {len(data)} B): unkomprimiert", s == 200 and "content-encoding" not in h)
    s, h, data, plain = raw("GET", "/static/pages.js")
    _, h0, data0, _ = raw("GET", "/static/pages.js", gz=False)
    ok(f"Statisches JS gzip ({len(data)} statt {len(data0)} Bytes), gleicher Inhalt", h.get("content-encoding") == "gzip" and plain == data0)
    et, et0 = h.get("etag", ""), h0.get("etag", "")
    ok(f"gzip: ETag schwach (W/), ungepackt stark ({et}, {et0})", et.startswith('W/"') and et0.startswith('"') and et == "W/" + et0)
    s, h, _, _ = raw("GET", "/static/pages.js", extra={"If-None-Match": et})
    s0, _, _, _ = raw("GET", "/static/pages.js", gz=False, extra={"If-None-Match": et0})
    s1, _, _, _ = raw("GET", "/static/pages.js", gz=False, extra={"If-None-Match": et})
    ok(f"If-None-Match: W/-Tag (gepackt) und starkes Tag -> 304, auch gemischt ({s}, {s0}, {s1})", s == 304 and s0 == 304 and s1 == 304)
    s, _, _, _ = raw("GET", "/static/pages.js", extra={"If-None-Match": 'W/"anders", ' + et})
    ok(f"If-None-Match mit mehreren Tags -> 304 ({s})", s == 304)
    s, _, _, _ = raw("GET", "/static/pages.js", extra={"If-None-Match": 'W/"anders"'})
    ok(f"If-None-Match fremdes Tag -> 200 ({s})", s == 200)
    s, h, data, _ = raw("GET", "/static/icon-192.png")
    ok("PNG: nicht komprimiert", s == 200 and "content-encoding" not in h and data[:4] == b"\x89PNG")
    s, h, data, _ = raw("GET", "/static/pages.js", extra={"Range": "bytes=0-99"})
    ok("Teilantwort (Range, 206): nicht komprimiert", s == 206 and "content-encoding" not in h and len(data) == 100)
    s, h, data, _ = raw("HEAD", "/static/pages.js")
    ok("HEAD: nicht komprimiert", s == 200 and "content-encoding" not in h)
    # Live-Kanal: kein gzip, erstes Ereignis kommt sofort
    t0 = time.time()
    s, h, r, c = raw("GET", "/api/events", extra={"Accept": "text/event-stream"}, read=False)
    first = r.fp.readline()
    ok(f"Live-Kanal: text/event-stream ohne gzip, sofort ({(time.time() - t0) * 1000:.0f} ms)",
       s == 200 and "content-encoding" not in h and h.get("content-type", "").startswith("text/event-stream") and first and time.time() - t0 < 5)
    c.close()
    # Fehlerantwort (404 JSON) bleibt lesbar
    s, h, data, plain = raw("GET", "/api/notes/999999999")
    ok("404 bleibt JSON", s == 404 and json.loads(plain) is not None)
    # zu grosse Anfrage: 413 weiterhin (BodyLimit vor gzip)
    c = http.client.HTTPConnection(U.hostname, U.port, timeout=20)      # nur Kopfzeilen: Server lehnt sofort ab
    c.putrequest("POST", "/api/notes"); c.putheader("Host", HOST); c.putheader("Cookie", "saga=" + A); c.putheader("X-Saga", "1")
    c.putheader("Accept-Encoding", "gzip"); c.putheader("Content-Type", "application/json"); c.putheader("Content-Length", str(3 * 1024 * 1024))
    c.endheaders(); r = c.getresponse(); body = r.read(); c.close()
    ok("Zu grosse Anfrage: weiterhin 413 (JSON)", r.status == 413 and b"too large" in body)
    # --- Papierkorb ---------------------------------------------------------------------------------------------
    cn = sqlite3.connect(DB)
    idx = cn.execute("SELECT indexdef FROM pg_indexes WHERE indexname='note_trash'").fetchone()
    ok("Index note_trash vorhanden (Teilindex)", idx and "deleted_at IS NOT NULL" in idx[0])
    old, new = ids[0], ids[1]
    call("DELETE", f"/api/notes/{old}"); call("DELETE", f"/api/notes/{new}")
    cn.execute("UPDATE note SET deleted_at=? WHERE id=?", (time.time() - 40 * 86400, old)); cn.commit()
    tr = {p["id"] for p in call("GET", "/api/trash")[1]}
    ok("Papierkorb-Liste: abgelaufene Seite endgueltig weg, frische noch da", old not in tr and new in tr)
    ok("Abgelaufene Seite auch in der Datenbank weg", cn.execute("SELECT 1 FROM note WHERE id=?", (old,)).fetchone() is None)
    ok("Seitenbaum ohne Papierkorb", not {old, new} & {p["id"] for p in call("GET", "/api/notes")[1]})
    # --- Grenzen: NOTE_MAX_COUNT / NOTE_ROW_MAX_COUNT (Funktion direkt, mit kleinen Grenzen) ---------------------
    os.environ["NOTE_MAX_COUNT"] = "5"; os.environ["NOTE_ROW_MAX_COUNT"] = "8"
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    from db import connect
    from saga import notes
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid='hk-test-a'")
    s, d = call("POST", "/api/notes", {"title": "DB", "kind": "database"}); dbid = d["id"]
    pages = [call("POST", "/api/notes", {"title": f"P{i}"})[1]["id"] for i in range(3)]
    rows = [call("POST", "/api/notes", {"title": f"R{i}", "parent_id": dbid})[1]["id"] for i in range(7)]
    with connect() as c:
        ok("Grenzen: 4 Seiten + 7 Eintraege (Summe > 5): weitere Seite erlaubt", notes.note_limit_error(c, "hk-test-a", None) is None)
        ok("Grenzen: weiterer Eintrag erlaubt (7 < 8)", notes.note_limit_error(c, "hk-test-a", dbid) is None)
    call("POST", "/api/notes", {"title": "P3"})
    rows.append(call("POST", "/api/notes", {"title": "R7", "parent_id": dbid})[1]["id"])
    with connect() as c:
        e1, e2 = notes.note_limit_error(c, "hk-test-a", None), notes.note_limit_error(c, "hk-test-a", dbid)
        ok(f"Grenzen: 5 Seiten -> keine weitere Seite ({e1})", e1 and "5" in e1)
        ok(f"Grenzen: 8 Eintraege -> kein weiterer Eintrag ({e2})", e2 and "8" in e2)
    call("DELETE", f"/api/notes/{pages[0]}"); call("DELETE", f"/api/notes/{rows[0]}")
    with connect() as c:
        ok("Grenzen: Papierkorb zaehlt nicht (Seite + Eintrag wieder moeglich)",
           notes.note_limit_error(c, "hk-test-a", None) is None and notes.note_limit_error(c, "hk-test-a", dbid) is None)
        ok("Grenzen: unter NOTE_MAX_COUNT insgesamt immer erlaubt (wie bisher)", notes.note_limit_error(c, "hk-test-unbekannt", None) is None)
finally:
    cn = sqlite3.connect(DB)
    try: cn.execute("DELETE FROM note WHERE uid LIKE ?", ("hk-test-%",))
    except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nSCHNELLER {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
