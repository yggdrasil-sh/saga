"""Push-Relais Ende-zu-Ende (2.6, Bugs-DB 2262-7): Saga-Server -> echtes Relais (relay/sagarelay als eigener Prozess) ->
Fake-APNs. Dazwischen ein mitschneidender Durchreicher (Server -> Relais), damit alles, was das Relais bekommt, geprueft
werden kann. Ein zweites Fake-APNs spielt den eigenen APNs-Weg des Servers (eigener Schluessel, Vorrang je Topic).

Der Saga-Server unter HK_TEST_BASE muss mit SAGA_PUSH_RELAY_URL=http://127.0.0.1:<FR_PORT> und
SAGA_PUSH_RELAY_HOST=saga.example.org laufen (Domain-Nachweis: das Relais ruft /.well-known/saga-push-relay ueber
RELAY_VERIFY_TEST_BASE = HK_TEST_BASE mit Host-Kopf saga.example.org ab). Dieser Prozess nutzt dieselbe Umgebung.

Ports: FR_PORT (18630, Durchreicher = Adresse des Relais fuer den Server), +1 Relais, +2 Fake-APNs des Relais, +3 Fake-APNs
des eigenen Wegs. Geprueft: Anmeldung mit Domain-Nachweis (active), Weg je Abo (eigener Schluessel fuer sein Bundle, fremdes
Bundle ueber das Relais, alte App ohne Schluessel nie ueber das Relais), Kanarien (Chattext, Seitentitel, Anzeigename,
Anmeldename, Auftragstitel) nie Server->Relais, Relais->APNs, im Relais-Protokoll oder in der Relais-SQLite (auch Base64),
push-type/priority/expiration = _apns_plan fuer alle Arten, collapse-id je tag gleich und je Auftrag verschieden ohne
Klartext, thread nur im Chiffrat, nie voip fuer Arten der Kollegin (job.*), sichtbares aps nur Loc-Keys (RT-30, EV-24),
entschluesselter Inhalt = message(), Testmitteilung, 410 -> Abo weg. Wegwerf-Konten fr-*."""
import base64, http.client, http.server, json, os, secrets, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push as T  # noqa: E402  (tests/push.py: req, ck, UA, jwt_ok)
import auth  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from db import connect  # noqa: E402
from saga import push, push_relay  # noqa: E402
from saga.core import _tok_hash  # noqa: E402

FR = int(os.environ.get("FR_PORT", "18630"))
RP, AP1, AP2 = FR + 1, FR + 2, FR + 3
B1, B2 = "org.example.saga", "org.example.saga2"            # B1: eigener Schluessel des Servers UND Relais; B2: nur Relais
TEAM, KID = "TEAMRELAY1", "KEYRELAY01"
CANARY = ("Kanarie-Chattext-7Q", "Kanarie-Seitentitel-3Z", "Kanarie-Anzeigename-9X", "fr-kanarielogin", "Kanarie-Auftrag-5W")
WAIT = 5.6
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


# --- Mitschnitt ---------------------------------------------------------------------------------------------------------
LOCK = threading.Lock()
TAP = []            # (methode, pfad, koepfe, koerper, status) Server -> Relais
APNS = {AP1: [], AP2: []}   # je Fake-APNs: {tok, headers, body, team}
STATUS = {}         # Token-Praefix -> (status, reason)
RELAY_PUBS = {}


class Tap(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _fwd(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        h = {k: v for k, v in self.headers.items() if k.lower() not in ("connection", "content-length")}
        c = http.client.HTTPConnection("127.0.0.1", RP, timeout=20)
        c.request(self.command, self.path, body=body if body or self.command in ("POST", "PUT") else None, headers=h)
        r = c.getresponse(); data = r.read(); c.close()
        with LOCK:
            TAP.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body, r.status))
        self.send_response(r.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _fwd


class FakeAPNs(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    pubs = {}

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        port = self.server.server_port
        tok = self.path.rsplit("/", 1)[-1]
        jwt = (self.headers.get("authorization") or "x x").split(" ", 1)[-1]
        j = T.jwt_ok(jwt, self.pubs[port])
        with LOCK:
            APNS[port].append({"tok": tok, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "jwt": j})
        code, reason = STATUS.get(tok[:4], (200, ""))
        out = json.dumps({"reason": reason}).encode() if code != 200 else b""
        self.send_response(code if j else 403)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve(port, h):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", port), h)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def p8():
    k = ec.generate_private_key(ec.SECP256R1())
    return k, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def raw_pub(k):
    return k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# --- Relais starten ------------------------------------------------------------------------------------------------------
class RelayHarness:
    """Startet das echte relay/ (python -m sagarelay) mit Fake-APNs (AP1) und Test-.p8; Protokoll in eine Datei."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="fakerelay-", dir=os.environ.get("STATE_DIR") or None)
        k, pem = p8()
        FakeAPNs.pubs[AP1] = raw_pub(k)
        with open(os.path.join(self.dir, "apns.p8"), "wb") as f:
            f.write(pem)
        cfg = {"teams": [{"team_id": TEAM, "key_id": KID, "p8_file": "apns.p8", "bundles": [B1, B2]}], "registration": "verify",
               "sandbox": True, "apns_timeout": 3, "operator": "Beispiel-Betreiber, relay.example.org"}
        with open(os.path.join(self.dir, "relay.json"), "w") as f:
            json.dump(cfg, f)
        self.db = os.path.join(self.dir, "relay.db")
        self.log = os.path.join(self.dir, "relay.log")
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "relay") + os.pathsep + ROOT, RELAY_LISTEN="127.0.0.1", RELAY_PORT=str(RP),
                   RELAY_CONFIG=os.path.join(self.dir, "relay.json"), RELAY_DB=self.db, RELAY_APNS_URL=f"http://127.0.0.1:{AP1}",
                   RELAY_VERIFY_TEST_BASE=os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
        self.fh = open(self.log, "wb")
        self.p = subprocess.Popen([sys.executable, "-m", "sagarelay"], cwd=ROOT, env=env, stdout=self.fh, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                c = http.client.HTTPConnection("127.0.0.1", RP, timeout=2); c.request("GET", "/v1/info"); c.getresponse().read(); c.close()
                break
            except OSError:
                time.sleep(0.1)

    def stop(self):
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.fh.close()

    def text(self):
        with open(self.log, "rb") as f:
            log = f.read()
        blob = b""
        for fn in os.listdir(self.dir):
            if fn.startswith("relay.db"):
                with open(os.path.join(self.dir, fn), "rb") as f:
                    blob += f.read()
        # Inhalt aller Tabellen - in einem Unterprozess mit -I (tests/pgshim ersetzt sqlite3 in diesem Prozess)
        dump = subprocess.run([sys.executable, "-I", "-c", "import sqlite3, sys\ncon = sqlite3.connect(sys.argv[1])\n"
                               "for (t,) in con.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall():\n"
                               "    [print(t, repr(r)) for r in con.execute('SELECT * FROM ' + t).fetchall()]", self.db],
                              capture_output=True, timeout=30).stdout
        return log, blob + dump


def variants(s):
    b = s.encode()
    out = {b, base64.b64encode(b), base64.urlsafe_b64encode(b).rstrip(b"=")}
    for i in range(3):          # Base64 mit Versatz (Kanarie mitten in einem laengeren Text)
        x = base64.b64encode(b"\0" * i + b)[4:-4]
        out.add(x)
        out.add(x.replace(b"+", b"-").replace(b"/", b"_"))
    return [v for v in out if len(v) >= 6]


def leaks(blob):
    return [c for c in CANARY if any(v in blob for v in variants(c))]


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'fr-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "device_login", "push_sub", "push_queue"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'fr-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'fr-%' OR target LIKE 'fr-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'fr-%' OR uid LIKE 'fr-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fr-%'")


def wait(pred, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.15)
    return None


def got(port, tok, since=0):
    with LOCK:
        return [g for g in APNS[port][since:] if g["tok"] == tok]


def sub_tuple(endpoint):
    with connect() as c:
        r = c.execute(f"SELECT {push.SUB_COLS} FROM push_sub WHERE endpoint=?", (endpoint,)).fetchone()
    return tuple(r) if r else None


def check_plan(name, g, inner, voip):
    pt, prio, ttl, ctag, rate = push._apns_plan(inner, voip)
    h = g["headers"]
    exp = int(h.get("apns-expiration") or -1)
    # Bugs-DB 2511: stille Hintergrund-Pushes (background) nur sofort - apns-expiration 0, sonst jetzt + ttl
    exp_ok = exp == 0 if pt == "background" else abs(exp - (time.time() + ttl)) < 30
    good = h.get("apns-push-type") == pt and h.get("apns-priority") == str(prio) and exp_ok
    return good, (h.get("apns-push-type"), h.get("apns-priority"), exp if exp <= 0 else exp - int(time.time()), pt, prio, ttl)


def main():
    cleanup()
    serve(FR, Tap)
    serve(AP1, FakeAPNs)
    serve(AP2, FakeAPNs)
    rel = RelayHarness()
    prev = {k: auth.settings(fresh=True).get(k) for k in ("push", "apns")}
    with connect() as c:
        prev_rows = {k: (c.execute("SELECT v FROM setting WHERE k=?", (k,)).fetchone() or [None])[0]
                     for k in ("apns", "push_relay", "push_relay_state")}
    try:
        run(rel)
    finally:
        rel.stop()
        with connect() as c, c:
            for k, v in prev_rows.items():
                if v is None:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
                else:
                    c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, v))
        auth.save_setting("push", prev["push"] or {})
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nFAKERELAY {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(rel):
    ok(f"Relais laeuft (relay/sagarelay, Port {RP}), Adresse fuer Server/Test: {push_relay.url()}", rel.p.poll() is None and push_relay.url() == f"http://127.0.0.1:{FR}")
    # eigener APNs-Schluessel fuer B1 (Fake-APNs AP2), Relais an
    k2, pem2 = p8()
    FakeAPNs.pubs[AP2] = raw_pub(k2)
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k IN ('push_relay', 'push_relay_state', 'push_relay_old')")
    push_relay.forget_key_cache()
    push_relay.reset()
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True, "relay": True,
                               "apns_e2e_direct": True})
    auth.save_setting("apns", {"enabled": True, "team_id": "OWNTEAM001", "key_id": "OWNKEY0001", "bundle_id": B1, "p8": pem2.decode(),
                               "url": f"http://127.0.0.1:{AP2}"})
    time.sleep(WAIT)
    auth.create_user("fr-a", T.PW, "Anna Relais")
    auth.create_user("fr-kanarielogin", T.PW, "Kanarie-Anzeigename-9X")
    A, Bk = T.ck("fr-a"), T.ck("fr-kanarielogin")
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('fr-a','fr-a','iPhone',?,?,?)",
                  (_tok_hash(tok), tok[:8], time.time()))
    bearer = {"Authorization": "Bearer " + tok}
    app_r, app_d = T.UA(), T.UA()                 # Schluessel der App fuer B2 (Relais) bzw. B1 (eigener Weg)
    TR, TV, TD, TOLD = "7a" * 32, "7b" * 32, "7c" * 32, "7d" * 32
    s1, d1 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TR, "env": "sandbox", "keys": app_r.keys(), "topic": B2}, hdr=bearer)
    s2, d2 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns-voip", "token": TV, "env": "sandbox", "keys": app_r.keys(), "topic": B2}, hdr=bearer)
    s3, d3 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TD, "env": "sandbox", "keys": app_d.keys(), "topic": B1}, hdr=bearer)
    ok(f"Abos: fremdes Bundle -> Relais, eigenes Bundle -> eigener Schluessel ({d1['device'].get('via')}, {d2['device'].get('via')}, {d3['device'].get('via')})",
       (s1, s2, s3) == (201, 201, 201) and d1["device"]["via"] == "relay" and d2["device"]["via"] == "relay" and d3["device"]["via"] == "direct"
       and d1["device"]["e2e"] and d3["device"]["e2e"])
    sid = wait(lambda: push_relay.config().get("server_id") and push_relay.state().get("state") == "ok" and push_relay.config()["server_id"])
    st = push_relay.state()
    ok(f"Anmeldung beim ersten Abo ueber das Relais: Domain-Nachweis -> aktiv ({sid}, {st.get('state')}, Stufe {st.get('stage')}, {st.get('topics')})",
       bool(sid) and st.get("stage") == "neu" and st.get("topics") == [B1, B2])
    with LOCK:
        reg = [x for x in TAP if x[1] == "/v1/servers" and x[0] == "POST"]
    rb = json.loads(reg[0][3]) if reg else {}
    ok(f"Anmeldung: keyid reg, Tag saga-relay/1, Host aus SAGA_PUSH_RELAY_HOST ({rb.get('host')})",
       reg and 'keyid="reg"' in reg[0][2].get("signature-input", "") and 'tag="saga-relay/1"' in reg[0][2].get("signature-input", "")
       and rb.get("host") == "saga.example.org" and rb.get("pub") == push_relay.config()["pub"])
    s, stp = T.req("GET", "/api/push", A)
    ok(f"/api/push: apns (eigener Schluessel) true, apns_topics [B1, B2], apns_relay ok ({stp['server'].get('apns_relay')})",
       stp["server"]["apns"] is True and stp["server"]["apns_topics"] == [B1, B2] and stp["server"]["apns_relay"] == {"state": "ok", "topics": [B1, B2]})
    s4, d4 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TOLD, "env": "sandbox"}, hdr=bearer)
    ok(f"alte App ohne Schluessel: eigener Weg (Bundle des Servers), nie Relais ({d4['device'].get('via')})", s4 == 201 and d4["device"]["via"] == "direct")

    # --- Server-Pipeline: Teilen (Seitentitel-Kanarie) und Kommentar (Chattext-Kanarie) von "Kanarie-Anzeigename" ---------
    m1, m2 = len(APNS[AP1]), len(APNS[AP2])
    pg = T.req("POST", "/api/notes", Bk, {"title": "Kanarie-Seitentitel-3Z"})[1]["id"]
    T.req("PUT", f"/api/notes/{pg}/shares", Bk, {"type": "user", "target": "fr-a", "role": "comment"})
    g = wait(lambda: got(AP1, TR, m1))
    gd = wait(lambda: got(AP2, TD, m2))
    b = json.loads(g[0]["body"]) if g else {}
    inner = app_r.decrypt(push.b64u_d(b["saga"]["c"])) if b.get("saga") else {}
    ok(f"Teilen ueber das Relais: APNs-Koerper genau {{aps, saga}}, Team-Token des Relais ({inner.get('kind')}, {inner.get('body')})",
       set(b) == {"aps", "saga"} and g[0]["jwt"] and g[0]["jwt"][1]["iss"] == TEAM and inner.get("kind") == "share.added"
       and "Kanarie-Anzeigename-9X" in inner.get("body", ""))
    good, why = check_plan("share", g[0], inner, False) if g else (False, None)
    ok(f"... push-type/priority/expiration = _apns_plan ({why})", good)
    ok(f"... Topic {B2}, collapse-id = HMAC(auth, tag), kein Klartext-tag", g and g[0]["headers"].get("apns-topic") == B2
       and g[0]["headers"].get("apns-collapse-id") == push.e2e_collapse(app_r.keys()["auth"], inner.get("tag", ""))
       and inner.get("tag", "x") not in g[0]["headers"].get("apns-collapse-id", ""))
    bd = json.loads(gd[0]["body"]) if gd else {}
    ok(f"eigener Schluessel Vorrang fuer sein Bundle: B1-Abo ueber eigenen Weg (E2E), nicht ueber das Relais ({gd and gd[0]['headers'].get('apns-topic')})",
       gd and set(bd) == {"aps", "saga"} and gd[0]["headers"].get("apns-topic") == B1 and gd[0]["jwt"][1]["iss"] == "OWNTEAM001"
       and not got(AP1, TD) and app_d.decrypt(push.b64u_d(bd["saga"]["c"])).get("kind") == "share.added")
    ok("alte App (ohne Schluessel) am eigenen Weg: Klartext wie bisher, nie beim Relais", wait(lambda: got(AP2, TOLD, m2)) and not got(AP1, TOLD))
    m1 = len(APNS[AP1])
    pga = T.req("POST", "/api/notes", A, {"title": "Kanarie-Seitentitel-3Z von Anna"})[1]["id"]
    T.req("PUT", f"/api/notes/{pga}/shares", A, {"type": "user", "target": "fr-kanarielogin", "role": "comment"})
    T.req("POST", f"/api/notes/{pga}/comments", Bk, {"block_id": "k1", "body": "Kanarie-Chattext-7Q steht im Kommentar"})
    g = wait(lambda: [x for x in got(AP1, TR, m1) if app_r.decrypt(push.b64u_d(json.loads(x["body"])["saga"]["c"])).get("kind", "").startswith("comment")])
    inner_c = app_r.decrypt(push.b64u_d(json.loads(g[0]["body"])["saga"]["c"])) if g else {}
    good, why = check_plan("comment", g[0], inner_c, False) if g else (False, None)
    ok(f"Kommentar ueber das Relais: entschluesselt, thread nur im Chiffrat, Plan stimmt ({inner_c.get('kind')}, {inner_c.get('thread')}, {why})",
       good and inner_c.get("thread") and "thread-id" not in json.dumps(json.loads(g[0]["body"])["aps"]))
    with connect() as c:
        nid = c.execute("SELECT id FROM notification WHERE uid='fr-a' AND kind=? ORDER BY id DESC LIMIT 1", (inner_c.get("kind"),)).fetchone()
        mm = push.message(c, "fr-a", inner_c.get("kind"), nid[0] if nid else None, "fr-kanarielogin", {}, "apns", True, inner_c.get("ts"), lang=None)
    ok("... Inhalt = message() (gleiche Felder)", mm and all(mm.get(k) == inner_c.get(k) for k in ("title", "body", "url", "tag", "kind", "thread", "v")))

    # --- alle Arten direkt (Testprozess, gleicher Code): Planung, collapse, nie voip fuer job.* --------------------------
    sub_a, sub_v = sub_tuple(f"apns:sandbox:{TR}"), sub_tuple(f"apns-voip:sandbox:{TV}")
    now = int(time.time())
    base = {"title": "Saga", "v": 2, "lang": "de", "ts": now}
    kinds = [
        ("chat", False, dict(base, body="Neue Nachricht von Kanarie-Anzeigename-9X", url="/chats#c5", tag="chat-5", kind="chat.message", chat=5, n=2, actor="Kanarie-Anzeigename-9X")),
        ("anruf_voip", True, dict(base, body="Anruf von Kanarie-Anzeigename-9X", url="/chats#call-" + "ab" * 16, tag="call-" + "ab" * 16, kind="call", call="ab" * 16, chat=5, media="audio", caller="Kanarie-Anzeigename-9X", ttl=55, n=1)),
        ("gruppe_voip", True, dict(base, body="Gruppenanruf von Kanarie-Anzeigename-9X", url="/chats#call-" + "cd" * 16, tag="call-" + "cd" * 16, kind="call.group", call="cd" * 16, chat=6, media="video", group=True, ttl=50)),
        ("anruf_alert", False, dict(base, body="Anruf von Kanarie-Anzeigename-9X", url="/chats#call-" + "ef" * 16, tag="call-" + "ef" * 16, kind="call", call="ef" * 16, ttl=55)),
        ("verpasst", False, dict(base, body="Verpasster Anruf", url="/chats#c5", tag="missed-5", kind="call.missed", chat=5)),
        ("sammel", False, dict(base, body="Während „Nicht stören“: 3 Nachrichten", url="/", tag="status-digest", kind="status.digest", digest={"messages": 3, "calls": 0, "other": 0}, silent=True)),
        ("test", False, dict(base, body="Testmitteilung – Mitteilungen kommen an.", url="/", tag="test", kind="test")),
        ("aktualisierung", False, dict(base, body="", url="/chats#c5", tag="chat-5", kind="chat.message", update=True, removed=True, n=0)),
        ("auftrag1", False, dict(base, body="Kanarie-Auftrag-5W erledigt", url="/", tag="job-1", kind="job.done", actor="svc:kollegin")),
        ("auftrag2", False, dict(base, body="Kanarie-Auftrag-5W fertig", url="/", tag="job-2", kind="job.done", actor="svc:kollegin")),
        ("auftrag1b", False, dict(base, body="Kanarie-Auftrag-5W nochmal", url="/", tag="job-1", kind="job.ask", actor="svc:kollegin")),
    ]
    coll, plans, alerts, inner_ok = {}, [], [], 0
    for name, voip, msg in kinds:
        m1 = len(APNS[AP1])
        v = push.send(sub_v if voip else sub_a, msg)
        g = wait(lambda: got(AP1, TV if voip else TR, m1), 6)
        if v[0] != "ok" or not g:
            plans.append((name, v, "nicht angekommen"))
            continue
        b = json.loads(g[0]["body"])
        inner_ok += app_r.decrypt(push.b64u_d(b["saga"]["c"])) == msg
        good, why = check_plan(name, g[0], msg, voip)
        if not good:
            plans.append((name, why))
        coll[name] = g[0]["headers"].get("apns-collapse-id")
        alerts.append((name, b["aps"], g[0]["headers"].get("apns-topic")))
    ok(f"alle Arten: zugestellt, push-type/priority/expiration = _apns_plan ({plans or len(kinds)})", not plans)
    ok(f"alle Arten: entschluesselt = gesendete Mitteilung ({inner_ok}/{len(kinds)})", inner_ok == len(kinds))
    ok(f"collapse-id: gleich je tag (job-1), verschieden je Auftrag (job-1/job-2), Aktualisierung eigene ({coll.get('auftrag1')}, {coll.get('auftrag2')})",
       coll.get("auftrag1") == coll.get("auftrag1b") and coll.get("auftrag1") != coll.get("auftrag2") and coll.get("chat") != coll.get("aktualisierung")
       and all(v and v.startswith("c") and len(v) == 23 and "job" not in v and "chat" not in v for v in coll.values()))
    loc = {"title-loc-key", "loc-key"}
    vis = [(n, a) for n, a, _ in alerts if a and "alert" in a and (set(a["alert"]) != loc or set(a) - {"alert", "mutable-content", "sound", "interruption-level"})]
    ok(f"sichtbarer Teil nur Loc-Keys und Schalter (RT-30, EV-24) ({vis})", not vis
       and all(a == {} for n, a, _ in alerts if n.endswith("voip")) and [a for n, a, _ in alerts if n == "aktualisierung"] == [{"content-available": 1}])
    ok("Anrufe: VoIP-Topic .voip, ohne VoIP-Abo als alert mit SAGA_PUSH_CALL + time-sensitive",
       all(t == B2 + ".voip" for n, _, t in alerts if n.endswith("voip"))
       and any(n == "anruf_alert" and a["alert"]["loc-key"] == "SAGA_PUSH_CALL" and a.get("interruption-level") == "time-sensitive" for n, a, _ in alerts))
    ok("stille Sammelmeldung ohne sound, sonst sound default",
       any(n == "sammel" and "sound" not in a for n, a, _ in alerts) and any(n == "chat" and a.get("sound") == "default" for n, a, _ in alerts))
    # nie voip fuer die Kollegin: Waechter in der Planung und in _route
    m1 = len(APNS[AP1])
    vj = push.send(sub_v, dict(base, body="Kanarie-Auftrag-5W", url="/", tag="job-3", kind="job.done", actor="svc:kollegin"))
    time.sleep(1.5)
    with connect() as c:
        sids = [r[0] for r in c.execute("SELECT id FROM push_sub WHERE uid='fr-a'").fetchall()]
        rt = push._route(c, sids, "job.done")
        routed = {r[0] for r in c.execute(f"SELECT kind FROM push_sub WHERE id IN ({','.join('?' * len(rt))})", tuple(rt)).fetchall()}
    ok(f"nie voip fuer Arten der Kollegin: VoIP-Abo -> fail ohne Zustellung, _route nimmt nur apns ({vj}, {sorted(routed)})",
       vj[0] == "fail" and not got(AP1, TV, m1) and "apns-voip" not in routed and "apns" in routed)
    # 2.6 (2270 K-C): echte Auftraege ueber message() (Nutzlast 3, saga.ai_notify) - aps ohne category/title/body, alles
    # Weitere (detail, actions, Kennungen) nur im Chiffrat; Auftragstitel = Kanarie (Positivkontrolle nach dem Entschluesseln)
    from saga import ai_notify, ai_plan, jobs
    from saga.actor import acting
    with connect() as c, c:                     # Bereich jobs aus: der Server stellt diesen Auftrag nicht selbst zu
        pk = (c.execute("SELECT v FROM user_kv WHERE uid='fr-a' AND k='push'").fetchone() or [None])[0]
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES ('fr-a', 'push', ?)",
                  (json.dumps(dict(json.loads(pk or "{}"), cats={"jobs": False})),))
    jid = jobs.create("fr-a", ai_plan.KIND, title="Kanarie-Auftrag-5W", steps=[{"tool": "create_task", "args": {}}])
    with acting(None), connect() as c, c:
        jobs._to(c, jid, "wartet_ok", "laeuft", jobs.now() - 300)
        jobs._to(c, jid, "laeuft", "fehlgeschlagen", jobs.now(), reason="konflikt")
    with connect() as c:
        row = c.execute(f"SELECT {push.SUB_COLS}, lang, caps, device_id FROM push_sub WHERE endpoint=?", (f"apns:sandbox:{TR}",)).fetchone()
        info = dict(push.sub_info(row), caps=("update", "detail", "actions"))
        mj = push.message(c, "fr-a", "job.failed", data={"job": jid}, sub_kind="apns", has_keys=True, sub=info)
    m1 = len(APNS[AP1])
    vj = push.send(sub_a, mj) if mj else ("kein message()", "")
    g = wait(lambda: got(AP1, TR, m1), 6)
    bj = json.loads(g[0]["body"]) if g else {}
    ij = app_r.decrypt(push.b64u_d(bj["saga"]["c"])) if g else {}
    ok(f"Auftrag (job.failed) ueber das Relais: E2E, aps nur Loc-Keys ohne category, Inhalt = message() ({vj})",
       vj[0] == "ok" and info["e2e"] and ij == mj and set(bj) == {"aps", "saga"} and "category" not in bj.get("aps", {}) and
       set(bj.get("aps", {}).get("alert", {})) == {"title-loc-key", "loc-key"} and bj["aps"].get("mutable-content") == 1)
    ok("... detail/Kennungen nur im Chiffrat (Positivkontrolle Titel im entschluesselten t1), collapse undurchsichtig",
       (ij.get("detail") or {}).get("t1", "").endswith("Kanarie-Auftrag-5W") and ij.get("job") == jid and ij.get("v") == 3 and
       g[0]["headers"].get("apns-collapse-id") == push.e2e_collapse(row[5], f"job-{jid}") and
       "Kanarie-Auftrag-5W" not in json.dumps(bj["aps"]) and "job" not in g[0]["headers"].get("apns-collapse-id", "job"))
    with connect() as c, c:
        if pk is None:
            c.execute("DELETE FROM user_kv WHERE uid='fr-a' AND k='push'")
        else:
            c.execute("UPDATE user_kv SET v=? WHERE uid='fr-a' AND k='push'", (pk,))
    _ = ai_notify
    # Testmitteilung ueber die API (Server-Prozess)
    m1 = len(APNS[AP1])
    s, d = T.req("POST", f"/api/push/subscriptions/{d1['id']}/test", A)
    g = wait(lambda: got(AP1, TR, m1), 6)
    ok(f"Testmitteilung ueber das Relais ({s}, {d})", s == 200 and d.get("ok") and g and app_r.decrypt(push.b64u_d(json.loads(g[0]["body"])["saga"]["c"]))["kind"] == "test")
    # Groesse: innerer Text > E2E_MAX -> fail, nichts beim Relais
    with LOCK:
        nt = len(TAP)
    v = push.send(sub_a, dict(base, body="x" * 2500, url="/", tag="big", kind="test"))
    with LOCK:
        nt2 = len(TAP)
    ok(f"zu gross (E2E_MAX) -> fail, nichts ans Relais ({v})", v == ("fail", "zu gross") and nt2 == nt)
    # 410 vom Relais -> Abo weg
    TDEAD = "dead" * 16
    s, d = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TDEAD, "env": "sandbox", "keys": app_r.keys(), "topic": B2}, hdr=bearer)
    STATUS["dead"] = (410, "Unregistered")
    v = push.send(sub_tuple(f"apns:sandbox:{TDEAD}"), dict(base, body="x", url="/", tag="t", kind="test"))
    with connect() as c, c:
        push._record(c, d["id"], v[0], v[1], time.time())
        left = c.execute("SELECT COUNT(*) FROM push_sub WHERE id=?", (d["id"],)).fetchone()[0]
    ok(f"Relais 410 (APNs Unregistered) -> gone, Abo geloescht ({v}, {left})", v[0] == "gone" and left == 0)
    # fremdes Topic, das das Relais nicht kennt -> kein Weg
    s, d = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": "7e" * 32, "env": "sandbox", "keys": app_r.keys(),
                                                           "topic": "org.example.unbekannt"}, hdr=bearer)
    ok(f"Bundle, das das Relais nicht kennt -> kein Weg ({d.get('device', {}).get('via')})", s == 201 and d["device"]["via"] is None)

    # --- Kanarien: nirgends beim Relais ------------------------------------------------------------------------------------
    time.sleep(1.0)
    with LOCK:
        tap = b"".join(json.dumps(x[2]).encode() + x[1].encode() + x[3] for x in TAP)
        apb = b"".join(json.dumps(x["headers"]).encode() + x["body"] for x in APNS[AP1])
    log, sq = rel.text()
    ok(f"Mitschnitt vollstaendig ({len(TAP)} Anfragen Server->Relais, {len(APNS[AP1])} Relais->APNs, Protokoll {len(log)} B, SQLite {len(sq)} B)",
       len(TAP) >= 10 and len(APNS[AP1]) >= 10 and log and sq)
    for where, blob in (("Server->Relais", tap), ("Relais->APNs", apb), ("Relais-Protokoll", log), ("Relais-SQLite", sq)):
        ok(f"keine Kanarie {where} (auch Base64): {leaks(blob)}", not leaks(blob))
    ok("Kanarien waren wirklich unterwegs (im Chiffrat, App entschluesselt sie)", "Kanarie-Anzeigename-9X" in inner.get("body", "")
       and "Kanarie-Auftrag-5W" in json.dumps(kinds, ensure_ascii=False))
    ok("Relais-Protokoll/SQLite ohne Geraetetoken", not any(t.encode() in log + sq for t in (TR, TV)))
    ok("Server->Relais: nur /v1/info, /v1/servers, /v1/servers/me, /v1/push", {x[1] for x in TAP} <= {"/v1/info", "/v1/servers", "/v1/servers/me", "/v1/push"})


if __name__ == "__main__":
    main()
