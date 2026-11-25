"""Push-Relais weg (2.6, Bugs-DB 2262-7): Fake-Relais im Modus "dumm" (antwortet nach Vorgabe: ok, aus, Haenger, 503,
429 mit retry_after, 401, 403 blocked) - Zustellung laeuft hier im Testprozess (push.run_once), ohne Saga-Server.
Der eigene Saga-Server auf dieser Datenbank darf dabei NICHT laufen (seine Push-Schleife wuerde mitzustellen).

Umgebung: SAGA_PUSH_RELAY_URL=http://127.0.0.1:<RW_PORT> (Standard 18640), Fake-Web-Push auf RW_PORT+1.
Geprueft (docs/PUSH-RELAY.md 4, 9): Abos bleiben (fails 0, nur last_err), Auftraege hold (tries zurueck, Buendeln je Chat
geht weiter), Glocke vollstaendig, genau eine Admin-Glocke je Stoerung (10 min down, sofort bei blocked), bei Erholung
gelesen, /api/push apns_relay.state down, Web Push ohne Verzoegerung (run_once < 2 s bei offenem Schutzschalter),
Schutzschalter 15 s -> 30 s, 429 server/token/apns mit retry_after, 401 -> Neuanmeldung hoechstens 1x je Stunde, nach
Rueckkehr neuester Stand je Chat (n), abgelaufene Anrufe und Auftraege ueber der Lebensdauer verworfen. Wegwerf-Konten rw-*."""
import http.server, json, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push as T  # noqa: E402  (tests/push.py: UA)
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import push, push_relay  # noqa: E402

RW = int(os.environ.get("RW_PORT", "18640"))
WP = RW + 1
B1 = "org.example.saga"
SID = "s_aaaaaaaaaaaaaaaa"
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


MODE = {"push": "ok", "me": "ok", "regs": 0, "pushes": 0, "429": {}}
LOG = []
WEB = []
LOCK = threading.Lock()


class Dumb(http.server.BaseHTTPRequestHandler):
    """Fake-Relais "dumm": prueft nur, dass signiert ist, und antwortet nach MODE."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _out(self, code, d):
        raw = json.dumps(d).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        n = int(self.headers.get("content-length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        with LOCK:
            LOG.append(("GET", self.path, self.headers.get("signature-input")))
        if self.path == "/v1/info":
            return self._out(200, {"v": [1], "push_types": ["alert", "background", "voip"], "topics": [B1], "registration": "verify",
                                   "max_payload": 4096, "max_payload_voip": 5120})
        if self.path == "/v1/servers/me":
            m = MODE["me"]
            if m == "blocked":
                return self._out(403, {"error": "blocked"})
            return self._out(200, {"server_id": SID, "state": "active", "stage": "neu", "topics": [B1], "created": 1,
                                   "limits": {"ring": {"per_min": 30}, "alert": {"per_min": 120, "per_day": 5000}, "bg": {"per_min": 60}}})
        return self._out(404, {"error": "bad"})

    def do_POST(self):
        body = self._body()
        sig = self.headers.get("signature-input") or ""
        with LOCK:
            LOG.append(("POST", self.path, sig, body))
        if 'tag="saga-relay/1"' not in sig:
            return self._out(401, {"error": "auth", "reason": "signature"})
        if self.path == "/v1/servers":
            MODE["regs"] += 1
            return self._out(201, {"server_id": SID, "state": "active", "stage": "neu", "topics": [B1], "limits": {}})
        if self.path != "/v1/push":
            return self._out(404, {"error": "bad"})
        MODE["pushes"] += 1
        m = MODE["push"]
        if m == "hang":
            time.sleep(10)
            return self._out(200, {"ok": True})
        if m == "503":
            return self._out(503, {"error": "upstream"})
        if m == "429":
            return self._out(429, dict({"error": "rate", "tier": "neu"}, **MODE["429"]))
        if m == "401once":
            MODE["push"] = "ok"
            return self._out(401, {"error": "auth", "reason": "unknown"})
        if m == "401":
            return self._out(401, {"error": "auth", "reason": "unknown"})
        if m == "blocked":
            return self._out(403, {"error": "blocked"})
        return self._out(200, {"ok": True})


    def do_DELETE(self):
        self._body()
        if self.path == "/v1/servers/me" and MODE["me"] == "blocked":
            return self._out(403, {"error": "blocked"})
        return self._out(200, {"ok": True})


class FakeWeb(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self.rfile.read(n)
        with LOCK:
            WEB.append((time.time(), self.path))
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(port, h):
    s = http.server.ThreadingHTTPServer(("127.0.0.1", port), h)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def cleanup():
    with connect() as c, c:
        for t in ("user_kv", "notification", "device_login", "push_sub", "push_queue", "chat_member", "chat_call"):
            col = "callee" if t == "chat_call" else "uid"
            c.execute(f"DELETE FROM {t} WHERE {col} LIKE 'rw-%'")
        c.execute("DELETE FROM chat WHERE created_by LIKE 'rw-%'")
        c.execute("DELETE FROM notification WHERE kind='push.relay'")
        c.execute("DELETE FROM users WHERE username LIKE 'rw-%'")


def q_rows():
    with connect() as c:
        return c.execute("SELECT q.id, s.kind, q.kind, q.tries, q.next_at, q.data FROM push_queue q JOIN push_sub s ON s.id=q.sub_id "
                         "WHERE q.uid='rw-a' ORDER BY q.id").fetchall()


def sub_state():
    with connect() as c:
        return c.execute("SELECT kind, fails, last_err FROM push_sub WHERE uid='rw-a' ORDER BY id").fetchall()


def digest(n=1):
    push._rate.clear()                   # RATE_MIN (30 je Konto und Minute) gilt hier nicht - wir pruefen das Relais
    with connect() as c, c:
        return push.enqueue(c, "rw-a", "status.digest", data={"digest": {"messages": n, "calls": 0, "other": 0}})


def due():
    with connect() as c, c:
        c.execute("UPDATE push_queue SET next_at=0 WHERE uid='rw-a'")


def relay_pushes():
    with LOCK:
        return [x for x in LOG if x[1] == "/v1/push"]


def admin_bells():
    with connect() as c:
        return c.execute("SELECT id, read_at, data FROM notification WHERE uid='rw-adm' AND kind='push.relay' ORDER BY id").fetchall()


def main():
    if push_relay.url() != f"http://127.0.0.1:{RW}":
        print(f"SAGA_PUSH_RELAY_URL muss http://127.0.0.1:{RW} sein (ist {push_relay.url()})")
        sys.exit(2)
    cleanup()
    srv = serve(RW, Dumb)
    serve(WP, FakeWeb)
    prev = {k: auth.settings(fresh=True).get(k) for k in ("push", "allowed_hosts")}
    with connect() as c:
        prev_rows = {k: (c.execute("SELECT v FROM setting WHERE k=?", (k,)).fetchone() or [None])[0]
                     for k in ("apns", "push_relay", "push_relay_state")}
    try:
        run(srv)
    finally:
        with connect() as c, c:
            for k, v in prev_rows.items():
                if v is None:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
                else:
                    c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, v))
        auth.save_setting("push", prev["push"] or {})
        auth.save_setting("allowed_hosts", prev["allowed_hosts"] or "")
        cleanup()
    print(f"\nRELAIS-WEG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(srv):
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k IN ('push_relay', 'push_relay_state', 'push_relay_old', 'apns')")
    push_relay.forget_key_cache()
    push_relay.reset()
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True, "relay": True,
                               "apns_e2e_direct": True})
    auth.save_setting("allowed_hosts", f"127.0.0.1:{WP}")
    auth._settings_cache["v"] = None
    auth.create_user("rw-a", T.PW, "Rita Weg")
    auth.create_user("rw-adm", T.PW, "Admin Weg", is_admin=True)
    app = T.UA()
    now = time.time()
    with connect() as c, c:
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('rw-a','rw-a','iPhone','rwhash','hkd_rw',?)",
                        (now,)).lastrowid
        c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, device_id, created, topic) VALUES (?,?,?,?,?,?,?,?,?)",
                  ("rw-a", "apns", "apns:production:" + "9a" * 32, app.keys()["p256dh"], app.keys()["auth"], "iPhone", did, now, B1))
        w = T.UA()
        c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, created) VALUES (?,?,?,?,?,?,?)",
                  ("rw-a", "webpush", f"http://127.0.0.1:{WP}/wp/rw", w.keys()["p256dh"], w.keys()["auth"], "Browser", now))
        chat = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('group', 'rw-a', ?)", (now,)).lastrowid
        c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?, 'rw-a', ?)", (chat, now))

    # --- Normalbetrieb: Anmeldung bei Bedarf, ok --------------------------------------------------------------------
    digest(1)
    push.run_once()
    ok(f"ok: angemeldet (keyid reg, saga-relay/1) und zugestellt ({MODE['regs']} Anmeldung, {len(relay_pushes())} Push)",
       MODE["regs"] == 1 and len(relay_pushes()) == 1 and push_relay.state().get("state") == "ok" and not q_rows())

    # --- 503: hold, Abo schuldlos, Schutzschalter ------------------------------------------------------------------------
    MODE["push"] = "503"
    digest(2)
    t0 = time.time()
    push.run_once()
    rows, subs = q_rows(), sub_state()
    ok(f"503 -> hold: Auftrag wartet (tries 0), Abo fails 0 mit last_err, Web Push zugestellt ({[(r[1], r[3]) for r in rows]}, {subs})",
       [(r[1], r[3]) for r in rows] == [("apns", 0)] and all(s[1] == 0 for s in subs)
       and "Relais" in (dict((s[0], s[2]) for s in subs).get("apns") or "") and rows[0][4] > t0 + 10)
    ok("Schutzschalter offen (15 s), Zustand down", push_relay.breaker_open() and push_relay.state().get("state") == "down"
       and 13 <= push_relay._cb["until"] - t0 <= 17 and push_relay._cb["wait"] == 30)
    with connect() as c:
        st = push._state(c, "rw-a")
    ok(f"/api/push: server.apns_relay.state down ({st['server']['apns_relay']})", st["server"]["apns_relay"]["state"] == "down")
    n0 = len(relay_pushes())
    digest(3)
    due()
    t1 = time.time()
    push.run_once()
    dt = time.time() - t1
    ok(f"Schutzschalter offen: kein Netz zum Relais, run_once {dt:.2f} s < 2 s, Web Push zugestellt",
       len(relay_pushes()) == n0 and dt < 2 and WEB and WEB[-1][0] >= t1)

    # --- Haenger: nach 8 s hold, danach sofort ---------------------------------------------------------------------------
    push_relay.reset()
    MODE["push"] = "hang"
    due()
    t1 = time.time()
    push.run_once()
    dt = time.time() - t1
    ok(f"Haenger: Lesezeit 8 s, dann hold und Schutzschalter ({dt:.1f} s)", 7 <= dt < 12 and push_relay.breaker_open()
       and all(r[3] == 0 for r in q_rows() if r[1] == "apns"))
    digest(4)
    due()
    w0 = len(WEB)
    t1 = time.time()
    push.run_once()
    dt = time.time() - t1
    ok(f"... danach sofort: run_once {dt:.2f} s < 2 s, Web Push ohne Verzoegerung", dt < 2 and len(WEB) > w0)

    # --- Admin-Glocke: einmal je Stoerung nach 10 min ---------------------------------------------------------------------
    ok("vor 10 min: keine Admin-Glocke", not admin_bells())
    with connect() as c, c:
        d = json.loads(c.execute("SELECT v FROM setting WHERE k='push_relay_state'").fetchone()[0])
        d["since"] = time.time() - 601
        c.execute("UPDATE setting SET v=? WHERE k='push_relay_state'", (json.dumps(d),))
    push_relay.tick(); push_relay.tick()
    due(); push.run_once()
    b = admin_bells()
    ok(f"nach 10 min down: genau eine Admin-Glocke push.relay (auch nach weiteren Fehlern) ({len(b)})", len(b) == 1 and b[0][1] is None
       and json.loads(b[0][2]).get("code") == "down")
    with connect() as c:
        it = push.notify.item(c, "rw-adm", b[0][0]) if b else None
    ok(f"... Text ohne Inhalte, Link in den Admin-Bereich ({(it or {}).get('text')})", it and "Push-Relais" in it["text"])
    with connect() as c, c:
        mq = push.enqueue(c, "rw-adm", "push.relay", ref=b[0][0])
    ok(f"... Kategorie admin: Admin bekommt sie auch als Mitteilung (Weg: Web Push o. a.) ({mq})", "push.relay" in push.CATS["admin"])

    # --- Rueckkehr: neuester Stand je Chat, Glocke gelesen ---------------------------------------------------------------
    MODE["push"] = "503"
    with connect() as c, c:
        cid = c.execute("SELECT chat_id FROM chat_member WHERE uid='rw-a'").fetchone()[0]
        c.execute("DELETE FROM push_queue WHERE uid='rw-a'")
    for i in range(3):
        with connect() as c, c:
            c.execute("INSERT INTO notification (uid, kind, actor_uid, data, created) VALUES ('rw-a', 'chat.message', 'rw-adm', ?, ?)",
                      (json.dumps({"chat": cid, "n": 1}), time.time()))
            push.enqueue(c, "rw-a", "chat.message", actor="rw-adm", data={"chat": cid})
        if i == 0:
            push_relay.reset()
            due(); push.run_once()          # erster Versuch: 503 -> hold, tries wieder 0
    rows = [r for r in q_rows() if r[1] == "apns"]
    ok(f"waehrend der Stoerung: drei Chat-Nachrichten -> EIN wartender Auftrag je Abo (Buendeln ueber tries 0) ({[(r[2], r[3]) for r in rows]})",
       len(rows) == 1 and rows[0][3] == 0 and rows[0][2] == "chat.message")
    with connect() as c:
        bell = c.execute("SELECT COUNT(*) FROM notification WHERE uid='rw-a' AND kind='chat.message'").fetchone()[0]
    ok(f"Glocke vollstaendig ({bell} Eintraege)", bell == 3)
    # abgelaufener Anruf und Auftrag ueber der Lebensdauer
    with connect() as c, c:
        sid_a = c.execute("SELECT id FROM push_sub WHERE uid='rw-a' AND kind='apns'").fetchone()[0]
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created) VALUES (?,?,?,?,?,?,?,?)",
                  ("ab" * 8, cid, "rw-adm", "rw-a", "d1", "audio", "ringing", time.time() - 300))
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, actor, data, created, next_at) VALUES ('rw-a', ?, 'call', 'rw-adm', ?, ?, 0)",
                  (sid_a, json.dumps({"chat": cid, "call": "ab" * 8}), time.time() - 300))
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, data, created, next_at) VALUES ('rw-a', ?, 'status.digest', ?, ?, 0)",
                  (sid_a, json.dumps({"digest": {"messages": 9, "calls": 0, "other": 0}}), time.time() - push.TTL - 5))
    due(); push.run_once()
    kinds = sorted(r[2] for r in q_rows() if r[1] == "apns")
    ok(f"abgelaufener Anruf und Auftrag ueber TTL verworfen, Chat wartet ({kinds})", kinds == ["chat.message"])
    MODE["push"] = "ok"
    push_relay.reset()
    n0 = len(relay_pushes())
    due(); push.run_once()
    new = relay_pushes()[n0:]
    inner = app.decrypt(push.b64u_d(json.loads(new[0][3])["saga"]["c"])) if new else {}
    ok(f"Rueckkehr: genau eine Mitteilung fuer den Chat mit neuestem Stand (n {inner.get('n')})", len(new) == 1 and inner.get("n") == 3
       and inner.get("kind") == "chat.message" and not q_rows())
    b = admin_bells()
    ok(f"Erholung: Admin-Glocke als gelesen markiert, Zustand ok ({push_relay.state().get('state')})",
       len(b) == 1 and b[0][1] is not None and push_relay.state().get("state") == "ok" and not push_relay.state().get("alerted"))

    # --- 429 ------------------------------------------------------------------------------------------------------------
    for scope, ra in (("server", 7), ("token", 9), ("apns", 5)):
        push_relay.reset()
        MODE["push"], MODE["429"] = "429", {"scope": scope, "retry_after": ra}
        digest(1)
        due()
        t1 = time.time()
        push.run_once()
        r = [x for x in q_rows() if x[1] == "apns"]
        ok(f"429 scope {scope} retry_after {ra} -> hold, naechster Versuch nach {ra} s ({r and round(r[0][4] - t1, 1)})",
           len(r) == 1 and r[0][3] == 0 and ra - 1 <= r[0][4] - t1 <= ra + 2 and all(s[1] == 0 for s in sub_state()))
        with connect() as c, c:
            c.execute("DELETE FROM push_queue WHERE uid='rw-a'")
    ok("429: Schutzschalter bleibt zu (Relais erreichbar)", not push_relay.breaker_open())

    # --- 401: Neuanmeldung hoechstens einmal je Stunde ------------------------------------------------------------------
    push_relay.reset()
    MODE["push"] = "401once"
    r0 = MODE["regs"]
    digest(1); due(); push.run_once()
    ok(f"401 -> Neuanmeldung (gleicher Schluessel) und erneuter Versuch ok ({MODE['regs'] - r0} Anmeldung)",
       MODE["regs"] - r0 == 1 and not q_rows())
    MODE["push"] = "401"
    _hold_reset = dict(push_relay._tries)
    digest(1); due(); push.run_once()
    ok(f"zweites 401 in der Stunde -> hold ohne weitere Anmeldung ({MODE['regs'] - r0}, {push_relay.state().get('state')})",
       MODE["regs"] - r0 == 1 and [x[3] for x in q_rows() if x[1] == "apns"] == [0] and push_relay.state().get("state") == "auth"
       and _hold_reset["rereg"] > 0)
    with connect() as c, c:
        c.execute("DELETE FROM push_queue WHERE uid='rw-a'")

    # --- 403 blocked: sofort eine Admin-Glocke ----------------------------------------------------------------------------
    push_relay.reset()
    MODE["push"] = "ok"
    digest(1); due(); push.run_once()           # Erholung -> ok
    nb = len(admin_bells())
    MODE["push"] = "blocked"
    digest(1); due(); push.run_once()
    digest(1); due(); push.run_once()
    b = admin_bells()
    ok(f"403 blocked -> hold, sofort genau eine neue Admin-Glocke ({len(b) - nb}, {push_relay.state().get('state')})",
       len(b) - nb == 1 and json.loads(b[-1][2]).get("code") == "blocked" and push_relay.state().get("state") == "blocked"
       and all(s[1] == 0 for s in sub_state()) and [x[3] for x in q_rows() if x[1] == "apns"])
    MODE["me"] = "blocked"
    good, why = push_relay.unregister()
    ok(f"gesperrt: Abmelden -> 403 blocked als Zustand, nicht als Erfolg ({good}, {why})", not good and push_relay.state().get("state") == "blocked")
    MODE["me"] = "ok"
    with connect() as c, c:
        c.execute("DELETE FROM push_queue WHERE uid='rw-a'")

    # --- Relais aus (Port zu) ---------------------------------------------------------------------------------------------
    srv.shutdown(); srv.server_close()
    push_relay.reset()
    ne = digest(1); due()
    t1 = time.time()
    push.run_once()
    ok(f"Relais aus (Verbindung abgelehnt) -> hold, Schutzschalter, schnell ({time.time() - t1:.2f} s, {ne}, {q_rows()}, "
       f"{push_relay.breaker_open()}, {push_relay.state().get('state')})",
       time.time() - t1 < 4 and push_relay.breaker_open() and [x[3] for x in q_rows() if x[1] == "apns"] == [0]
       and all(s[1] == 0 for s in sub_state()))
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM push_sub WHERE uid='rw-a'").fetchone()[0]
    ok(f"nach allen Stoerungen: beide Abos noch da, fails 0 ({sub_state()})", n == 2 and all(s[1] == 0 for s in sub_state()))


if __name__ == "__main__":
    main()
