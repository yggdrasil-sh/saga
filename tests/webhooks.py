"""Webhooks (2.5, Paket 1 E6) gegen einen laufenden Test-Server mit lokalem Empfaenger.

Server mit SAGA_WEBHOOK_DEBOUNCE=1 und SAGA_WEBHOOK_RETRY_SCALE=0.02 starten (Wartezeiten kurz). Prueft: Anlegen (Bestaetigung,
Geheimnis einmal), Ereignisse page.created/updated/trashed/restored, row.created/updated (geaenderte Eigenschaften),
row.assigned, comment.created, task.created/completed, ping; HMAC-Signatur; Autosave zusammengefasst; duenne Nutzlast
ohne Inhalt und ohne Anmeldenamen; Rechte bei Zustellung (Freigabe entzogen -> nichts), Ereignisfilter, roots des Tokens;
Wiederholung mit Backoff und Protokoll; keine Weiterleitungen; Abschalten nach 20 Fehlschlaegen + Mitteilung;
Widerruf des Tokens schaltet ab; SSRF: interne Adressen ohne Freigabe (Anlegen und bei jeder Zustellung), http nur mit
Freigabe, Zugangsdaten/fremde Ports in der URL; Admin-Schalter."""
import base64, hashlib, hmac, json, os, sqlite3, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itsdangerous import TimestampSigner

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
UA, UB = "hk-test-a", "hk-test-b"


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "sec_at": int(time.time())}).encode())).decode()


A, X = ck(UA), ck(UB)


def call(m, p, cookie=None, b=None, bearer=None):
    h = {"Host": HOST, "Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            raw = r.read(); return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {}), raw.decode()
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {}), raw.decode(errors="replace")


# --- lokaler Empfaenger ---------------------------------------------------------------------------------------------
GOT = []
MODE = {"code": 200}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        GOT.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "t": time.time()})
        code = MODE["code"]
        self.send_response(code)
        if code in (301, 302, 307, 308):
            self.send_header("Location", f"http://127.0.0.1:{PORT}/umgeleitet")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{PORT}/hook"


def db():
    return sqlite3.connect(DB)


def setting(k, v):
    cn = db()
    if v is None:
        cn.execute("DELETE FROM setting WHERE k=?", (k,))
    else:
        cn.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?,?)", (k, json.dumps(v)))
    cn.commit()


res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:500]}")


def wait(pred, sec=12):
    t0 = time.time()
    while time.time() - t0 < sec:
        hit = [g for g in GOT if pred(g)]
        if hit:
            return hit
        time.sleep(0.1)
    return []


def ev(g):
    return json.loads(g["body"])


def of(kind, nid=None, path="/hook"):
    return lambda g: g["path"] == path and ev(g)["type"] == kind and (nid is None or ev(g)["data"].get("note_id") == nid or ev(g)["data"].get("task_id") == nid)


API = {"enabled": True, "webhooks": True, "mcp": True, "webhook_http": True, "rate": 1000, "rate_write": 1000}
try:
    cn = db()
    cn.execute("UPDATE users SET display_name='Anna Hook' WHERE username=?", (UA,))
    cn.execute("UPDATE users SET display_name='Bert Hook' WHERE username=?", (UB,)); cn.commit()
    setting("allowed_hosts", None); setting("allow_private_hosts", False)
    setting("api", API); time.sleep(5.5)

    # --- SSRF beim Anlegen -----------------------------------------------------------------------------------
    st, d, _ = call("POST", "/api/account/webhooks", A, {"url": URL, "events": ["page.created"]})
    ok("Interne Adresse (fremder Port) ohne Freigabe -> 400", st == 400, d)
    for bad in ("ftp://example.org/x", "https://user:pw@example.org/x", "https://example.org:8443/x", "javascript:alert(1)", "https://", "x" * 600):
        st, d, _ = call("POST", "/api/account/webhooks", A, {"url": bad, "events": ["page.created"]})
        ok(f"Ungueltige Adresse abgelehnt: {bad[:30]}", st == 400, d)
    for host in ("10.0.0.1", "169.254.169.254", "[::1]", "localhost", "0.0.0.0", "[::ffff:127.0.0.1]"):
        st, d, _ = call("POST", "/api/account/webhooks", A, {"url": f"https://{host}/x", "events": ["page.created"]})
        ok(f"Interne Adresse {host} -> 400", st == 400, d)
    setting("api", dict(API, webhook_http=False)); time.sleep(5.5)
    st, d, _ = call("POST", "/api/account/webhooks", A, {"url": "http://example.org/x", "events": ["page.created"]})
    ok("http:// ohne Admin-Freigabe -> 400", st == 400 and "https" in d.get("error", ""), d)
    setting("api", API)
    setting("allowed_hosts", f"127.0.0.1:{PORT}"); time.sleep(5.5)

    # --- Anlegen ---------------------------------------------------------------------------------------------
    st, d, _ = call("POST", "/api/account/webhooks", A, {"url": URL, "name": "Lokal", "events": ["page.created", "page.updated", "page.trashed",
                                                         "page.restored", "row.created", "row.updated", "row.assigned", "comment.created",
                                                         "task.created", "task.completed"]})
    ok("Hook angelegt, Geheimnis einmal", st == 200 and d.get("secret", "").startswith("whsec_"), d)
    HID, SEC = d["id"], d["secret"]
    st, d, raw = call("GET", "/api/account/webhooks", A)
    ok("Liste ohne Geheimnis", st == 200 and SEC not in raw and any(h["id"] == HID for h in d["hooks"]))
    ok("In der DB nur verschluesselt", SEC not in (db().execute("SELECT secret_enc FROM webhook WHERE id=?", (HID,)).fetchone()[0]))

    # --- Ereignisse ------------------------------------------------------------------------------------------
    P = call("POST", "/api/notes", A, {"title": "Hook-Seite"})[1]["id"]
    g = wait(of("page.created", P))
    ok("page.created zugestellt", bool(g), [ev(x) for x in GOT][-3:])
    if g:
        x = g[0]; h = x["headers"]
        tpart, vpart = dict(p.split("=", 1) for p in h["saga-signature"].split(",")).values()
        mac = hmac.new(SEC.encode(), f"{tpart}.".encode() + x["body"], hashlib.sha256).hexdigest()
        ok("HMAC-Signatur stimmt, Kopfzeilen Saga-Event/Saga-Delivery", vpart == mac and h["saga-event"] == "page.created"
           and h["saga-delivery"].startswith("evt_") and abs(int(tpart) - time.time()) < 60, h)
        e = ev(x)
        ok("Nutzlast duenn: Titel, Handle, kein Inhalt, kein Anmeldename", e["data"]["title"] == "Hook-Seite" and e["data"]["actor"]["handle"].startswith("~")
           and e["data"]["actor"]["name"] == "Anna Hook" and UA not in x["body"].decode() and "body" not in e["data"], e)
    GOT.clear()
    rev = 0
    for i in range(5):                                         # Autosave: 5 Speichervorgaenge schnell hintereinander
        rr = call("PATCH", f"/api/notes/{P}", A, {"body": json.dumps({"blocks": [{"id": "a1", "type": "paragraph", "data": {"text": f"Stand {i}"}}]}), "format": "blocks"})
    time.sleep(3.5)
    ups = [x for x in GOT if ev(x)["type"] == "page.updated"]
    ok("Autosave zusammengefasst (5 Speichern -> 1 page.updated)", len(ups) == 1 and "Stand" not in ups[0]["body"].decode(), [ev(x) for x in GOT])

    # Datenbank: row.created, row.updated (geaenderte Eigenschaften), row.assigned
    D = call("POST", "/api/notes", A, {"title": "Hook-DB", "kind": "database"})[1]["id"]
    s = call("GET", f"/api/notes/{D}", A)[1]["schema"]; s["props"].append({"id": "wer", "name": "Wer", "type": "person"})
    call("PATCH", f"/api/notes/{D}", A, {"schema": s})
    call("PUT", f"/api/notes/{D}/shares", A, {"type": "user", "target": UB, "role": "edit"})
    Z = call("POST", "/api/notes", A, {"parent_id": D, "title": "Zeile", "props": {"status": "todo"}})[1]["id"]
    ok("row.created", bool(wait(of("row.created", Z))) and ev(wait(of("row.created", Z))[0])["data"].get("db_id") == D)
    bref = next(p["ref"] for p in call("GET", f"/api/notes/{D}/people", A)[1]["people"] if not p.get("me"))
    GOT.clear()
    call("PATCH", f"/api/notes/{Z}", A, {"props": {"status": "doing", "wer": [bref]}})
    gu = wait(of("row.updated", Z)); ga = wait(of("row.assigned", Z))
    ok("row.updated mit geaenderten Eigenschaften", bool(gu) and set(ev(gu[0])["data"]["changed"]) >= {"status", "wer"}, [ev(x) for x in GOT])
    ok("row.assigned (Person als Eigenschaft, kein Name im Klartext ausser Anzeigename)", bool(ga) and UB not in ga[0]["body"].decode())

    # Kommentar, Aufgaben
    call("POST", f"/api/notes/{P}/comments", X if False else A, {"block_id": "", "body": "Kommentar-Text"})
    gc = wait(of("comment.created", P))
    ok("comment.created ohne Kommentartext", bool(gc) and "Kommentar-Text" not in gc[0]["body"].decode())
    tk = call("POST", "/api/tasks", A, {"text": "Hook-Aufgabe"})[1]["id"]
    call("PATCH", f"/api/tasks/{tk}", A, {"done": True})
    ok("task.created/task.completed", bool(wait(of("task.created", tk))) and bool(wait(of("task.completed", tk))))

    # Papierkorb und zurueck
    call("DELETE", f"/api/notes/{P}", A)
    ok("page.trashed", bool(wait(of("page.trashed", P))))
    call("POST", f"/api/trash/{P}/restore", A)
    ok("page.restored", bool(wait(of("page.restored", P))))

    # ping + Protokoll
    GOT.clear()
    st, d, _ = call("POST", f"/api/account/webhooks/{HID}/test", A)
    ok("Test-Ping", st == 200 and bool(wait(lambda g: ev(g)["type"] == "ping")))
    time.sleep(0.5)
    log = next(h for h in call("GET", "/api/account/webhooks", A)[1]["hooks"] if h["id"] == HID)["deliveries"]
    ok("Protokoll mit Status ok und Code 200", any(x["type"] == "ping" and x["status"] == "ok" and x["code"] == 200 for x in log), log[:3])

    # --- Rechte bei Zustellung -------------------------------------------------------------------------------
    st, d, _ = call("POST", "/api/account/webhooks", X, {"url": URL + "?b", "events": ["page.updated", "row.updated"]})
    HB = d["id"]
    GOT.clear()
    call("PATCH", f"/api/notes/{Z}", A, {"props": {"status": "done"}})
    ok("Konto B (Freigabe) bekommt row.updated", bool(wait(lambda g: g["path"] == "/hook?b" and ev(g)["type"] == "row.updated")))
    P2 = call("POST", "/api/notes", A, {"title": "Privat"})[1]["id"]
    GOT.clear()
    call("PATCH", f"/api/notes/{P2}", A, {"body": json.dumps({"blocks": [{"id": "x", "type": "paragraph", "data": {"text": "x"}}]}), "format": "blocks"})
    time.sleep(3)
    ok("Konto B bekommt nichts zu Seiten ohne Leserecht", not [g for g in GOT if g["path"] == "/hook?b"])
    # Freigabe entzogen zwischen Ereignis und Zustellung -> verworfen
    cn = db(); cn.execute("UPDATE webhook SET active=1 WHERE id=?", (HB,)); cn.commit()
    GOT.clear()
    call("PATCH", f"/api/notes/{Z}", A, {"props": {"status": "todo"}})
    call("DELETE", f"/api/notes/{D}/shares", A, {"type": "user", "target": UB})
    time.sleep(3)
    ok("Freigabe vor der Zustellung entzogen -> nicht gesendet (dropped)", not [g for g in GOT if g["path"] == "/hook?b"]
       and db().execute("SELECT COUNT(*) FROM webhook_delivery WHERE hook_id=? AND status='dropped'", (HB,)).fetchone()[0] >= 1)

    # --- Fehler, Wiederholung, keine Weiterleitung -----------------------------------------------------------
    MODE["code"] = 500; GOT.clear()
    call("POST", f"/api/account/webhooks/{HID}/test", A)
    ok("Fehler 500: wird wiederholt (Backoff)", len(wait(lambda g: ev(g)["type"] == "ping" and len([x for x in GOT if ev(x)["type"] == "ping"]) >= 3, 20)) > 0)
    d1 = db().execute("SELECT attempts, status, last_code FROM webhook_delivery WHERE hook_id=? AND kind='ping' ORDER BY id DESC LIMIT 1", (HID,)).fetchone()
    ok("Protokoll: Versuche gezaehlt, Status pending/failed, Code 500", d1[0] >= 2 and d1[2] == 500, d1)
    MODE["code"] = 302; GOT.clear()
    call("POST", f"/api/account/webhooks/{HID}/test", A)
    time.sleep(2.5)
    ok("Weiterleitung wird nicht verfolgt", not [g for g in GOT if g["path"] == "/umgeleitet"] and any(g["path"] == "/hook" for g in GOT))
    MODE["code"] = 200
    cn = db(); cn.execute("UPDATE webhook_delivery SET status='failed' WHERE hook_id=? AND status='pending'", (HID,))
    cn.execute("UPDATE webhook SET fail_streak=19 WHERE id=?", (HID,)); cn.commit()
    MODE["code"] = 500
    call("POST", f"/api/account/webhooks/{HID}/test", A)
    time.sleep(3)
    r = db().execute("SELECT active, fail_streak FROM webhook WHERE id=?", (HID,)).fetchone()
    nt = db().execute("SELECT COUNT(*) FROM notification WHERE uid=? AND kind='webhook.disabled'", (UA,)).fetchone()[0]
    ok("Nach 20 Fehlschlaegen abgeschaltet + Mitteilung", r[0] == 0 and nt >= 1, (r, nt))
    MODE["code"] = 200
    ok("Wieder einschalten", call("PATCH", f"/api/account/webhooks/{HID}", A, {"active": True})[0] == 200)

    # --- Rebinding / Freigabe zurueckgenommen: Pruefung bei jeder Zustellung ---------------------------------
    setting("allowed_hosts", None); time.sleep(5.5); GOT.clear()
    call("POST", f"/api/account/webhooks/{HID}/test", A)
    time.sleep(2.5)
    d1 = db().execute("SELECT last_error FROM webhook_delivery WHERE hook_id=? ORDER BY id DESC LIMIT 1", (HID,)).fetchone()
    ok("Freigabe weg: bei der Zustellung erneut geprueft -> nicht gesendet", not GOT and bool(d1[0]), d1)
    # IP-Pruefung beim Verbinden (netguard, gegen DNS-Rebinding) - unabhaengig von der Port-Pruefung, hier im Prozess
    sys.path.insert(0, os.getcwd())
    from saga import webhooks as W
    W.check_url = lambda u, cf=None: (u, None)
    code, err = W._send(URL, {"Content-Type": "application/json"}, b"{}")
    ok("Zustellung an interne IP ohne Freigabe: Verbindung verweigert (netguard)", code is None and "intern" in err.lower() and not GOT, (code, err))
    setting("allowed_hosts", f"127.0.0.1:{PORT}"); time.sleep(5.5)
    cn = db(); cn.execute("UPDATE webhook_delivery SET status='failed' WHERE hook_id=? AND status='pending'", (HID,)); cn.commit()

    # --- ueber die API (Token, Bereich webhooks, roots) -------------------------------------------------------
    st, d, _ = call("POST", "/api/account/api-tokens", A, {"name": "Hooks", "scopes": ["read:pages", "webhooks"], "roots": [P]})
    TK = d["token"]
    st, d, _ = call("POST", "/api/v1/webhooks", bearer=TK, b={"url": URL + "?tok", "events": ["row.updated"]})
    ok("API: Ereignis ohne passenden Bereich -> 400", st == 400, d)
    st, d, _ = call("POST", "/api/v1/webhooks", bearer=TK, b={"url": URL + "?tok", "events": ["page.updated"]})
    ok("API: Hook mit Token angelegt", st == 201, d); HT = d.get("id")
    GOT.clear()
    call("PATCH", f"/api/notes/{P}", A, {"body": json.dumps({"blocks": [{"id": "a1", "type": "paragraph", "data": {"text": "neu"}}]}), "format": "blocks"})
    call("PATCH", f"/api/notes/{P2}", A, {"body": json.dumps({"blocks": [{"id": "a2", "type": "paragraph", "data": {"text": "neu"}}]}), "format": "blocks"})
    time.sleep(3)
    tg = [ev(g)["data"]["note_id"] for g in GOT if g["path"] == "/hook?tok"]
    ok("roots des Tokens: nur Seiten im freigegebenen Baum", tg == [P], tg)
    st, d, _ = call("GET", f"/api/v1/webhooks/{HT}/deliveries", bearer=TK)
    ok("API: Protokoll lesbar", st == 200 and d["deliveries"] and d["deliveries"][0]["status"] == "ok", d)
    tid = next(x["id"] for x in call("GET", "/api/account/api-tokens", A)[1]["tokens"] if x["prefix"] == TK[:12])
    call("DELETE", f"/api/account/api-tokens/{tid}", A)
    ok("Token widerrufen -> Hook aus", db().execute("SELECT active FROM webhook WHERE id=?", (HT,)).fetchone()[0] == 0)

    # --- Admin-Schalter --------------------------------------------------------------------------------------
    setting("api", dict(API, webhooks=False)); time.sleep(5.5); GOT.clear()
    call("POST", f"/api/account/webhooks/{HID}/test", A)
    time.sleep(2)
    ok("Webhooks aus: nichts wird gesendet, Anlegen -> 403", not GOT and call("POST", "/api/account/webhooks", A, {"url": URL, "events": ["ping"]})[0] in (400, 403))
    setting("api", API); time.sleep(5.5)
    ok("Fremden Hook loeschen -> 404", call("DELETE", f"/api/account/webhooks/{HID}", X)[0] == 404)
finally:
    cn = db()
    for q in ("DELETE FROM note_share WHERE owner_uid LIKE ?", "DELETE FROM note_comment WHERE uid LIKE ?", "DELETE FROM note WHERE uid LIKE ?",
              "DELETE FROM task WHERE uid LIKE ?", "DELETE FROM api_token WHERE uid LIKE ?", "DELETE FROM webhook WHERE uid LIKE ?",
              "DELETE FROM notification WHERE uid LIKE ?"):
        try:
            cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError:
            pass
    for k in ("api", "allowed_hosts", "allow_private_hosts"):
        cn.execute("DELETE FROM setting WHERE k=?", (k,))
    cn.execute("UPDATE users SET display_name=username WHERE username IN (?, ?)", (UA, UB))
    cn.commit()
    srv.shutdown()
print(f"\nWEBHOOKS {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
