"""Chat mit Ende-zu-Ende-Verschluesselung (1.5) - Server-Seite, mit PyNaCl als Client (tests/chatlib.py).

Testvektoren (PyNaCl), Einrichtung (Signaturpruefung Backup/Geraet, Buendel), Geraete (Grenze, entfernen, Kopplung mit
Kurzcode), Direkt-Chat (Senden/Empfangen ueber Geraet und Backup-Schluessel, Signaturen, genaue Empfaengerliste),
Isolation (fremde Chats, Dateien, Kopplungen), "Server sieht keinen Klartext" (Suche ueber alle Textspalten + Server-Log),
Live-Ereignisse ohne Inhalt, Benachrichtigungen (gebuendelt, gelesen, stumm), Loeschen, Gruppen-/Seiten-/freie Chats mit
Mitgliederwechsel, Aufbewahrung (Chat + Admin-Hoechstwert), Anhaenge (Blob, Zugriff, Aufraeumen), Grenzen/Rate-Limits,
Admin-Schalter, neu beginnen, Kontoloeschung. Wegwerf-Konten ch-*, Gruppe ch-team; raeumt auf."""
import base64, http.client, json, os, socket, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import chat as CH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import chatlib as C  # noqa: E402
import nacl.bindings as nb  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
PW = "Chat-Pass-1234"
WAIT = 5.6
KDF = {"ops": 1, "mem": 8 << 20}                 # schnelle Parameter fuer Tests (Server erlaubt ab 8 MiB)
SECRET_TEXT = "Streng geheim: Treffpunkt Nordtor 23 Uhr"
res, STREAMS = [], []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, raw=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    data = raw
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; data = json.dumps(body)
    if raw is not None:
        h["Content-Type"] = "application/octet-stream"
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse(); out = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    try:
        out = json.loads(out)
    except ValueError:
        pass
    c.close()
    req.last_headers = hdrs
    return r.status, out


class Stream:
    def __init__(self, cookie):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
        self.r = self.c.getresponse()
        self.ev, self.closed = [], False
        threading.Thread(target=self._run, daemon=True).start()
        STREAMS.append(self)

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
        self.closed = True

    def mark(self):
        return len(self.ev)

    def wait(self, typ, pred=None, since=0, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ and (pred is None or pred(e[1])):
                    return e[1]
            time.sleep(0.05)
        return None

    def types(self, since):
        return [e[0] for e in self.ev[since:]]

    def close(self):
        try:
            self.c.sock.shutdown(socket.SHUT_RDWR); self.c.close()
        except Exception:  # noqa: BLE001
            pass


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ch-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "notification", "chat_identity", "chat_device", "chat_link", "chat_file"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'ch-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'ch-%' OR target LIKE 'ch-%'")
        c.execute("DELETE FROM chat WHERE group_name LIKE 'ch-%' OR dm_key LIKE 'ch-%' OR created_by LIKE 'ch-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'ch-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'ch-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'ch-%'")


class Person:
    """Ein Konto mit Identitaet und einem Geraet (oder ohne Chat-Einrichtung)."""

    def __init__(self, uid, setup=True):
        self.uid, self.k = uid, ck(uid)
        self.ident, self.dev = C.identity(), C.device()
        self.rk = C.recovery_key()
        if setup:
            s, d = req("PUT", "/api/chat/identity", self.k, C.setup_body(uid, self.ident, self.dev, self.rk, **KDF))
            assert s == 200, (s, d)

    def send(self, cid, text, reply_to=None, files=None, kind="msg", extra=None, ts=None, dev=None, recipients=None):
        """Nachricht verschluesseln (Empfaenger vom Server) und senden -> (status, antwort, gesendet)."""
        dev = dev or self.dev
        rc = recipients if recipients is not None else C.recipients_of(req("GET", f"/api/chats/{cid}/recipients", self.k)[1]["recipients"])
        body = {"v": 1, "type": "title" if kind == "title" else "text", "text": text}
        if kind == "title":
            body["title"] = text
        if files:
            body["files"] = files
        m = C.encrypt(str(cid), self.uid, dev, body, rc, reply_to=reply_to, ts=ts)
        m.pop("_key")
        m["kind"] = kind
        if files:
            m["files"] = [f["id"] for f in files]
        m.update(extra or {})
        s, d = req("POST", f"/api/chats/{cid}/messages", self.k, m)
        return s, d, m

    def read(self, cid, device=True, **q):
        qs = urllib.parse.urlencode(dict(q, **({"device": self.dev["id"]} if device else {})))
        return req("GET", f"/api/chats/{cid}/messages?{qs}", self.k)

    def open(self, cid, m, device=True):
        pk, sk = (self.dev["enc_pk"], self.dev["enc_sk"]) if m["key_for"] == "device" else (self.ident["backup_pk"], self.ident["backup_sk"])
        return C.decrypt(str(cid), m, pk, sk)


def verify_msg(cid, m, d):
    """Signatur + Geraete-Zertifikat wie ein Client pruefen (Antwort von /messages: devices, idents)."""
    dv = d["devices"].get(m["sender_device"])
    ident = d["idents"].get(m["sender"]["username"])
    cert = C.verify(C.unb64(dv["sig"]), C.device_msg(dv["uid"], m["sender_device"], C.unb64(dv["enc_pub"]), C.unb64(dv["sign_pub"])), C.unb64(ident))
    sig = C.verify(C.unb64(m["sig"]), C.sig_msg(str(cid), m["id"], m["sender"]["username"], m["sender_device"], m["ts"], m.get("reply_to"), m["body_ct"]),
                   C.unb64(dv["sign_pub"]))
    return cert and sig


def plaintext_hits(*needles):
    """Alle Text-Spalten aller Tabellen nach Klartext durchsuchen -> [(tabelle, spalte, needle)]"""
    hits = []
    with connect() as c:
        cols = c.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public' "
                         "AND data_type IN ('text', 'character varying')").fetchall()
        for t, col in cols:
            for nd in needles:
                if c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE strpos("{col}", ?) > 0', (nd,)).fetchone()[0]:
                    hits.append((t, col, nd))
    return hits


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "chat_retention_max_days", "share_enabled", "share_group_scope")}
    for k, v in (("chat_enabled", True), ("chat_retention_max_days", 0), ("share_enabled", True), ("share_group_scope", False)):
        auth.save_setting(k, v)
    for u, n in (("ch-a", "Anna Chat"), ("ch-b", "Bert Chat"), ("ch-c", "Cora Chat"), ("ch-d", "Dora Chat")):
        auth.create_user(u, PW, n)
    with connect() as c, c:
        auth.ensure_group(c, "ch-team")
    for u in ("ch-a", "ch-b", "ch-c"):
        auth.set_user_groups(auth.get_user(u)["id"], ["ch-team"])
    time.sleep(WAIT)
    try:
        run()
    finally:
        for x in STREAMS:
            x.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        cleanup()
    print(f"\nCHAT {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    # --- Testvektoren (PyNaCl) ------------------------------------------------------------------------------------
    import chat_vectors
    vr = chat_vectors.check()
    ok(f"Testvektoren v1 mit PyNaCl: {sum(g for _, g in vr)}/{len(vr)} ({[n for n, g in vr if not g]})", all(g for _, g in vr))

    A0, Dk = ck("ch-a"), ck("ch-d")
    ok("Ohne X-Saga: 401", req("GET", "/api/chat/me", None, hdr={"Cookie": "saga=" + A0})[0] == 401)
    s, me = req("GET", "/api/chat/me", A0)
    ok(f"Noch nicht eingerichtet: identity null, Grenzen ({s})", s == 200 and me["identity"] is None and me["enabled"] and me["limits"]["devices_max"] == 10)
    s, _ = req("GET", "/chats", A0)
    csp = req.last_headers.get("content-security-policy", "")
    ok(f"/chats: 200, CSP erlaubt nur WebAssembly zusaetzlich ({s})", s == 200 and "script-src 'self' 'wasm-unsafe-eval'" in csp and "unsafe-inline" not in csp)
    req("GET", "/notes", A0)
    ok("/notes: CSP unveraendert (kein wasm-unsafe-eval)", "wasm-unsafe-eval" not in req.last_headers.get("content-security-policy", ""))

    # --- Einrichtung -----------------------------------------------------------------------------------------------
    ident, dev = C.identity(), C.device()
    body = C.setup_body("ch-a", ident, dev, **KDF)
    bad = dict(body, backup_sig=C.b64(C.sign(C.backup_msg("ch-b", ident["backup_pk"]), ident["ident_sk"])))
    ok("Backup-Signatur fuer anderes Konto -> 400", req("PUT", "/api/chat/identity", A0, bad)[0] == 400)
    other = C.identity()
    bad = dict(body, device=C.device_api("ch-a", other, dev))
    ok("Geraete-Zertifikat von fremdem Identitaetsschluessel -> 400", req("PUT", "/api/chat/identity", A0, bad)[0] == 400)
    ok("Buendel ohne Argon2id / zu kleiner Speicher -> 400", req("PUT", "/api/chat/identity", A0, dict(body, bundle=json.dumps(
        dict(json.loads(body["bundle"]), mem=1024))))[0] == 400)
    with connect() as c:
        ok("... nichts gespeichert", not c.execute("SELECT 1 FROM chat_identity WHERE uid='ch-a'").fetchone())
    A = Person("ch-a")
    ok("Einrichtung A: 200", True)
    ok("Zweite Einrichtung ohne reset -> 409", req("PUT", "/api/chat/identity", A.k, C.setup_body("ch-a", C.identity(), C.device(), **KDF))[0] == 409)
    s, me = req("GET", f"/api/chat/me?device={A.dev['id']}", A.k)
    ob = C.open_bundle("ch-a", A.rk.lower().replace("-", " "), me["identity"]["bundle"])
    ok("Buendel vom Server laesst sich mit dem Wiederherstellungsschluessel oeffnen (Identitaet + Backup)",
       ob["ident_pk"] == A.ident["ident_pk"] and ob["backup_sk"] == A.ident["backup_sk"])
    ok("... falscher Wiederherstellungsschluessel scheitert", _fails(lambda: C.open_bundle("ch-a", C.recovery_key(), me["identity"]["bundle"])))
    ok("me: aktuelles Geraet markiert", [d["current"] for d in me["devices"]] == [True])
    B, Cc = Person("ch-b"), Person("ch-c")

    # --- Geraete ---------------------------------------------------------------------------------------------------
    d2 = C.device()
    ok("Geraet mit Signatur von B -> 400", req("POST", "/api/chat/devices", A.k, C.device_api("ch-a", B.ident, d2))[0] == 400)
    ok("Zweites Geraet (nach Wiederherstellung selbst signiert) -> 200", req("POST", "/api/chat/devices", A.k, C.device_api("ch-a", ob, d2))[0] == 200)
    ok("Gleiches Geraet nochmal -> 409", req("POST", "/api/chat/devices", A.k, C.device_api("ch-a", A.ident, d2))[0] == 409)
    extra = []
    for _ in range(8):
        x = C.device(); extra.append(x["id"]); req("POST", "/api/chat/devices", A.k, C.device_api("ch-a", A.ident, x))
    ok("11. Geraet -> 409 (hoechstens 10)", req("POST", "/api/chat/devices", A.k, C.device_api("ch-a", A.ident, C.device()))[0] == 409)
    ok("Fremdes Geraet entfernen -> 404", req("DELETE", f"/api/chat/devices/{extra[0]}", B.k)[0] == 404)
    for x in extra:
        req("DELETE", f"/api/chat/devices/{x}", A.k)
    s, me = req("GET", "/api/chat/me", A.k)
    ok(f"Geraete entfernt (revoked) ({sum(not d['revoked'] for d in me['devices'])} aktiv)", sum(not d["revoked"] for d in me["devices"]) == 2)

    # --- Direkt-Chat ------------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "dm", "user": "ch-b"})
    cid = d["chat"]["id"]
    ok(f"A beginnt Direkt-Chat mit B ({s}, {d['chat']['name']})", s == 200 and d["chat"]["kind"] == "dm" and d["chat"]["name"] == "Bert Chat")
    ok("... nochmal = derselbe Chat", req("POST", "/api/chats", B.k, {"kind": "dm", "user": "ch-a"})[1]["chat"]["id"] == cid)
    ok("Chat mit sich selbst / unbekanntem Konto -> 404", req("POST", "/api/chats", A.k, {"kind": "dm", "user": "ch-a"})[0] == 404
       and req("POST", "/api/chats", A.k, {"kind": "dm", "user": "ch-nobody"})[0] == 404)
    for p in (f"/api/chats/{cid}", f"/api/chats/{cid}/messages", f"/api/chats/{cid}/recipients"):
        ok(f"C (kein Mitglied): GET {p.split(str(cid))[1] or '/'} -> 404", req("GET", p, Cc.k)[0] == 404)
    s, rc = req("GET", f"/api/chats/{cid}/recipients", A.k)
    rcp = C.recipients_of(rc["recipients"])
    ok(f"Empfaenger: Geraete von A (2) und B (1) + 2 Backup-Schluessel ({sorted(rcp)})",
       set(rcp) == {A.dev["id"], d2["id"], B.dev["id"], "backup:ch-a", "backup:ch-b"})
    sa, sb, sc = Stream(A.k), Stream(B.k), Stream(Cc.k)
    time.sleep(0.6)
    mb, mc = sb.mark(), sc.mark()
    s, d, sent = A.send(cid, SECRET_TEXT)
    ok(f"A sendet ({s})", s == 200 and d["seq"] > 0)
    ev = sb.wait("chat.message", lambda x: x["chat_id"] == cid, since=mb)
    ok(f"B: chat.message live, ohne Inhalt ({ev})", ev and set(ev) == {"chat_id", "seq", "id", "sender"} and ev["sender"] == "ch-a")
    ok("C: kein Chat-Ereignis", not [t for t in sc.types(mc) if t.startswith("chat.")])
    s, d = B.read(cid)
    m = d["messages"][-1] if d.get("messages") else {}
    ok(f"B liest ueber sein Geraet ({m.get('key_for')}) und entschluesselt", m.get("key_for") == "device" and B.open(cid, m)["text"] == SECRET_TEXT)
    ok("... Signatur + Geraete-Zertifikat von A gueltig", verify_msg(cid, m, d))
    s, d2b = B.read(cid, device=False)
    mb2 = d2b["messages"][-1]
    ok("B ohne Geraet: Schluessel fuer backup:ch-b, entschluesselt ebenso", mb2["key_for"] == "backup" and B.open(cid, mb2, False)["text"] == SECRET_TEXT)
    ok("Verfaelschter Zeitstempel -> Signatur ungueltig", not verify_msg(cid, dict(m, ts=m["ts"] + 1), d))
    hits = plaintext_hits(SECRET_TEXT, "Nordtor", base64.b64encode(SECRET_TEXT.encode()).decode()[:20], A.rk.replace("-", "")[:16])
    ok(f"Server sieht keinen Klartext: Suche in ALLEN Textspalten aller Tabellen ({hits})", hits == [])
    log = "/tmp/hkstage/server.log"
    if os.path.exists(log):
        ok("... und nicht im Server-Log", "Nordtor" not in open(log, encoding="utf-8", errors="replace").read())
    with connect() as c:
        row = c.execute("SELECT body_ct, size FROM chat_msg WHERE id=?", (sent["id"],)).fetchone()
    ok("... gespeichert ist nur Ciphertext (B64, Nonce + Tag)", row and C.unb64(row[0]) and len(C.unb64(row[0])) >= 24 + 16 + len(SECRET_TEXT))

    # Senden: genaue Empfaenger, Signatur, Grenzen
    rc_missing = {k: v for k, v in rcp.items() if k != B.dev["id"]}
    s, d, _ = A.send(cid, "fehlt", recipients=rc_missing)
    ok(f"Ein Empfaenger-Geraet fehlt -> 409 mit aktueller Liste ({s})", s == 409 and len(d.get("recipients", [])) == 2)
    s, d, _ = A.send(cid, "zu viel", recipients=dict(rcp, **{Cc.dev["id"]: Cc.dev["enc_pk"]}))
    ok("Fremdes Geraet (C) als Empfaenger -> 409", s == 409)
    s, _, _ = A.send(cid, "x", extra={"sig": C.b64(b"\0" * 64)})
    ok("Falsche Signatur -> 400", s == 400)
    s, _, _ = A.send(cid, "x", dev=B.dev)
    ok("Geraet eines anderen Kontos -> 403", s == 403)
    s, _, _ = A.send(cid, "x", ts=int(time.time() * 1000) - 3600 * 1000)
    ok("Zeitstempel 1 h daneben -> 400", s == 400)
    s, _, _ = A.send(cid, "x", ts=10 ** 30)
    ok(f"Riesiger Zeitstempel -> 400 statt Serverfehler ({s})", s == 400)
    s, _, _ = A.send(cid, "x", extra={"id": sent["id"]})
    ok("Gleiche Nachrichten-id -> 400 (Signatur passt nicht) bzw. 409", s in (400, 409))
    s, _, _ = A.send(cid, "y" * 70000)
    ok("Nachricht > 64 KB -> 413", s == 413)
    s, _, _ = A.send(cid, "Antwort", reply_to="0" * 32)
    ok("Antwort auf unbekannte Nachricht -> 400", s == 400)
    s, _, r2 = A.send(cid, "Antwort ok", reply_to=sent["id"])
    ok("Antwort auf vorhandene Nachricht -> 200", s == 200)
    ok("B kann keine Nachricht von A loeschen -> 403", req("DELETE", f"/api/chats/{cid}/messages/{r2['id']}", B.k)[0] == 403)
    ok("C kann nicht in den Chat senden -> 404", Cc.send(cid, "rein", recipients={})[0] == 404)

    # Benachrichtigungen
    s, n = req("GET", "/api/notifications", B.k)
    it = [x for x in n["items"] if x["kind"] == "chat.message"]
    ok(f"B: eine gebuendelte Benachrichtigung ohne Inhalt ({[(x['text'], x['title'], x['count']) for x in it]})",
       len(it) == 1 and it[0]["count"] == 2 and it[0]["text"] == "2 neue Nachrichten" and it[0]["url"] == f"/chats#c{cid}"
       and it[0]["title"] == "Anna Chat" and "excerpt" not in it[0] and SECRET_TEXT not in json.dumps(n))
    s, u = req("GET", "/api/chats/unread", B.k)
    ok(f"B: 2 ungelesen ({u})", u["unread"] == 2)
    s, lst = req("GET", f"/api/chats?device={B.dev['id']}", B.k)
    ok("B: Chat-Liste mit Ungelesen und Gegenueber", lst["chats"][0]["id"] == cid and lst["chats"][0]["unread"] == 2 and lst["chats"][0]["peer"]["username"] == "ch-a")
    mrk = sb.mark()
    s, d = req("POST", f"/api/chats/{cid}/read", B.k, {"seq": 10 ** 12})
    ok(f"B gelesen -> 0 ungelesen, Benachrichtigung gelesen ({d})", s == 200 and d["unread"] == 0 and req("GET", "/api/notifications", B.k)[1]["unread"] == 0)
    ok("... chat.read an eigene Tabs", sb.wait("chat.read", lambda x: x["chat_id"] == cid, since=mrk) is not None)
    req("PATCH", f"/api/chats/{cid}", B.k, {"muted": True})
    A.send(cid, "stumm?")
    ok("Stumm: keine Benachrichtigung, zaehlt nicht als ungelesen", req("GET", "/api/notifications", B.k)[1]["unread"] == 0
       and req("GET", "/api/chats/unread", B.k)[1]["unread"] == 0)
    req("PATCH", f"/api/chats/{cid}", B.k, {"muted": False})
    mrk = sb.mark()
    req("POST", f"/api/chats/{cid}/typing", A.k)
    ty = sb.wait("chat.typing", lambda x: x["chat_id"] == cid, since=mrk)
    ok(f"tippt gerade -> B ({ty})", ty and ty["user"]["username"] == "ch-a")
    mrk = sb.mark()
    s, _ = req("DELETE", f"/api/chats/{cid}/messages/{sent['id']}", A.k)
    ok("A loescht eigene Nachricht fuer alle", s == 200 and sb.wait("chat.deleted", lambda x: x["id"] == sent["id"], since=mrk) is not None)
    with connect() as c:
        gone = not c.execute("SELECT 1 FROM chat_msg WHERE id=?", (sent["id"],)).fetchone()
        nk = c.execute("SELECT COUNT(*) FROM chat_msg_key k LEFT JOIN chat_msg m ON m.seq=k.msg_seq WHERE m.seq IS NULL").fetchone()[0]
    ok(f"... Ciphertext und Schluessel weg ({gone}, verwaiste Schluessel {nk})", gone and nk == 0)
    ok("... B sieht sie nicht mehr", sent["id"] not in [x["id"] for x in B.read(cid)[1]["messages"]])

    # Geraet entfernen
    req("DELETE", f"/api/chat/devices/{d2['id']}", A.k)
    rcp2 = C.recipients_of(req("GET", f"/api/chats/{cid}/recipients", A.k)[1]["recipients"])
    ok("Entferntes Geraet bekommt keine Schluessel mehr", d2["id"] not in rcp2)
    ok("Senden mit entferntem Geraet -> 403", A.send(cid, "alt", dev=d2)[0] == 403)
    with connect() as c:
        ok("... seine versiegelten Schluessel sind geloescht", not c.execute("SELECT 1 FROM chat_msg_key WHERE recipient=?", (d2["id"],)).fetchone())

    # --- Kopplung neues Geraet (Kurzcode) ---------------------------------------------------------------------------
    nd = C.device()
    nd_api = {"device_id": nd["id"], "id": nd["id"], "name": "Neuer Browser", "enc_pub": C.b64(nd["enc_pk"]), "sign_pub": C.b64(nd["sign_pk"])}
    mrk = sa.mark()
    s, lk = req("POST", "/api/chat/link", A.k, nd_api)
    ok(f"Neues Geraet fragt Kopplung an ({s})", s == 200 and len(lk["id"]) == 32)
    ok("... chat.link an die anderen Geraete", sa.wait("chat.link", lambda x: x["id"] == lk["id"], since=mrk) is not None)
    pend = req("GET", "/api/chat/link", A.k)[1]["links"]
    code_old = C.link_code("ch-a", pend[0]["device_id"], C.unb64(pend[0]["enc_pub"]), C.unb64(pend[0]["sign_pub"]))
    ok(f"Kurzcode auf beiden Geraeten gleich ({code_old})", code_old == C.link_code("ch-a", nd["id"], nd["enc_pk"], nd["sign_pk"]))
    ok("... 20 Ziffern (gegen Nachbauen durch den Server)", len(code_old.replace(" ", "")) == 20 and code_old.replace(" ", "").isdigit())
    ok("... anderer Schluessel -> anderer Code", code_old != C.link_code("ch-a", nd["id"], C.device()["enc_pk"], nd["sign_pk"]))
    ok("B sieht A's Kopplung nicht", req("GET", f"/api/chat/link/{lk['id']}", B.k)[1]["state"] == "gone" and req("GET", "/api/chat/link", B.k)[1]["links"] == [])
    ok("Bestaetigung mit falscher Signatur -> 400", req("POST", f"/api/chat/link/{lk['id']}", A.k, {"sig": C.b64(b"\1" * 64), "sealed": C.b64(b"x" * 100)})[0] == 400)
    ok("Neues Geraet wartet (pending)", req("GET", f"/api/chat/link/{lk['id']}", A.k)[1]["state"] == "pending")
    sealed = C.b64(nb.crypto_box_seal(C.bundle_plain(A.ident["ident_sk"], A.ident["backup_sk"]).encode(), nd["enc_pk"]))
    sig = C.b64(C.sign(C.device_msg("ch-a", nd["id"], nd["enc_pk"], nd["sign_pk"]), A.ident["ident_sk"]))
    ok("Altes Geraet bestaetigt -> 200", req("POST", f"/api/chat/link/{lk['id']}", A.k, {"sig": sig, "sealed": sealed})[0] == 200)
    s, g = req("GET", f"/api/chat/link/{lk['id']}", A.k)
    got = C.parse_plain(nb.crypto_box_seal_open(C.unb64(g["sealed"]), nd["enc_pk"], nd["enc_sk"]))
    ok("Neues Geraet bekommt das Buendel einmalig und oeffnet es", g["state"] == "approved" and got["ident_pk"] == A.ident["ident_pk"]
       and req("GET", f"/api/chat/link/{lk['id']}", A.k)[1]["state"] == "gone")
    ok("... und ist sofort Empfaenger", nd["id"] in C.recipients_of(req("GET", f"/api/chats/{cid}/recipients", A.k)[1]["recipients"]))
    ok("Hoechstens 3 offene Kopplungen", [req("POST", "/api/chat/link", A.k, dict(nd_api, id=C.hexid(), device_id=None))[0] for _ in range(4)][-1] == 429)
    s, d = req("PUT", "/api/chat/bundle", A.k, {"bundle": C.seal_bundle("ch-a", C.recovery_key(), A.ident, **KDF), "sig": C.b64(b"\0" * 64)})
    ok("Neuer Wiederherstellungsschluessel ohne gueltige Signatur -> 403", s == 403)
    nb_ = C.seal_bundle("ch-a", A.rk, A.ident, **KDF)
    s, d = req("PUT", "/api/chat/bundle", A.k, {"bundle": nb_, "sig": C.b64(C.sign(C.lp(C.V + "bundle-update", "ch-a", nb_), A.ident["ident_sk"]))})
    ok("... mit Signatur des Identitaetsschluessels -> 200", s == 200)

    # --- Gruppen-Chat (Saga-Gruppe): Mitglieder folgen der Gruppe ----------------------------------------------------
    ok("D (nicht in der Gruppe) kann keinen Gruppen-Chat anlegen -> 403", req("POST", "/api/chats", Dk, {"kind": "group", "group": "ch-team"})[0] == 403)
    s, d = req("POST", "/api/chats", A.k, {"kind": "group", "group": "ch-team"})
    gid = d["chat"]["id"]
    ok(f"Gruppen-Chat ch-team: A, B, C ({[m['username'] for m in d['chat']['members']]})", s == 200 and d["chat"]["name"] == "ch-team"
       and {m["username"] for m in d["chat"]["members"]} == {"ch-a", "ch-b", "ch-c"})
    rc_old = C.recipients_of(req("GET", f"/api/chats/{gid}/recipients", A.k)[1]["recipients"])
    s, _, g1 = A.send(gid, "An alle im Team")
    ok("C liest die Gruppen-Nachricht", s == 200 and Cc.open(gid, Cc.read(gid)[1]["messages"][-1])["text"] == "An alle im Team")
    auth.set_user_groups(auth.get_user("ch-c")["id"], [])          # C verlaesst die Gruppe
    auth.set_user_groups(auth.get_user("ch-d")["id"], ["ch-team"])  # D kommt dazu (ohne Chat-Schluessel)
    ok("C nach Austritt aus der Gruppe: Chat weg (404)", req("GET", f"/api/chats/{gid}/messages", Cc.k)[0] == 404
       and gid not in [x["id"] for x in req("GET", "/api/chats", Cc.k)[1]["chats"]])
    s, d, _ = A.send(gid, "Ohne C", recipients=rc_old)
    ok("Senden mit alter Empfaengerliste (inkl. C) -> 409", s == 409 and "ch-c" not in [r["username"] for r in d["recipients"]])
    rr = req("GET", f"/api/chats/{gid}/recipients", A.k)[1]["recipients"]
    ok(f"D ist neues Mitglied, noch ohne Schluessel (ready=false) ({[(r['username'], r['ready']) for r in rr]})",
       {r["username"]: r["ready"] for r in rr} == {"ch-a": True, "ch-b": True, "ch-d": False})
    s, _, g2 = A.send(gid, "Neu ohne C")
    ok("Neue Nachricht geht nur an aktuelle Mitglieder (C hat keinen Schluessel dafuer)", s == 200 and _keys_of(g2["id"]).isdisjoint({Cc.dev["id"], "backup:ch-c"}))
    s, dl = req("GET", f"/api/chats/{gid}/messages", Dk)
    ok(f"D sieht alte Nachrichten nicht (nicht fuer D verschluesselt) ({len(dl.get('messages', []))})", s == 200 and dl["messages"] == [])

    # --- Seiten-Chat: Mitglieder = Zugriff auf die Seite -------------------------------------------------------------
    nid = req("POST", "/api/notes", A.k, {"title": "Chat Projektseite"})[1]["id"]
    req("PUT", f"/api/notes/{nid}/shares", A.k, {"type": "user", "target": "ch-b", "role": "view"})
    ok("C ohne Zugriff: Seiten-Chat anlegen -> 404", req("POST", "/api/chats", Cc.k, {"kind": "page", "note_id": nid})[0] == 404)
    s, d = req("POST", "/api/chats", B.k, {"kind": "page", "note_id": nid})
    pid = d["chat"]["id"]
    ok(f"B (Lesen) legt Seiten-Chat an: A + B, Name = Seitentitel ({d['chat']['name']})", s == 200 and d["chat"]["name"] == "Chat Projektseite"
       and {m["username"] for m in d["chat"]["members"]} == {"ch-a", "ch-b"})
    ok("... gleicher Chat beim zweiten Mal", req("POST", "/api/chats", A.k, {"kind": "page", "note_id": nid})[1]["chat"]["id"] == pid)
    req("PUT", f"/api/notes/{nid}/shares", A.k, {"type": "user", "target": "ch-c", "role": "comment"})
    ok("C bekommt Zugriff -> taucht im Seiten-Chat auf", pid in [x["id"] for x in req("GET", "/api/chats", Cc.k)[1]["chats"]])
    req("DELETE", f"/api/notes/{nid}/shares?type=user&target=ch-b", A.k)
    ok("B verliert Zugriff -> Seiten-Chat weg", req("GET", f"/api/chats/{pid}", B.k)[0] == 404
       and "ch-b" not in [r["username"] for r in req("GET", f"/api/chats/{pid}/recipients", A.k)[1]["recipients"]])

    # --- Freie Gruppe: Einladung, verschluesselter Name ------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ch-b", "ch-c"]})
    fid_ = d["chat"]["id"]
    ok(f"Freie Gruppe: A (Admin), B, C ({s})", s == 200 and d["chat"]["role"] == "admin" and d["chat"]["name"] is None)
    s, _, tm = A.send(fid_, "Wanderfreunde", kind="title")
    ok("Name als verschluesselte title-Nachricht", s == 200)
    ch_b = [x for x in req("GET", f"/api/chats?device={B.dev['id']}", B.k)[1]["chats"] if x["id"] == fid_][0]
    ok("B entschluesselt den Gruppennamen aus der Liste", B.open(fid_, ch_b["title_msg"])["title"] == "Wanderfreunde")
    ok("... Name steht nirgends im Klartext", plaintext_hits("Wanderfreunde") == [])
    ok("Title-Nachricht zaehlt nicht als ungelesen / erscheint nicht im Verlauf", ch_b["unread"] == 0 and B.read(fid_)[1]["messages"] == [])
    ok("C (kein Admin) kann niemanden hinzufuegen -> 403", req("POST", f"/api/chats/{fid_}/members", Cc.k, {"user": "ch-d"})[0] == 403)
    ok("A fuegt D hinzu", req("POST", f"/api/chats/{fid_}/members", A.k, {"user": "ch-d"})[0] == 200)
    ok("C verlaesst die Gruppe", req("DELETE", f"/api/chats/{fid_}/members/ch-c", Cc.k)[0] == 200 and req("GET", f"/api/chats/{fid_}", Cc.k)[0] == 404)
    ok("B kann D nicht entfernen -> 403", req("DELETE", f"/api/chats/{fid_}/members/ch-d", B.k)[0] == 403)
    ok("Direkt-Chat kann man nicht verlassen -> 400", req("DELETE", f"/api/chats/{cid}/members/ch-b", B.k)[0] == 400)

    # --- Aufbewahrung ------------------------------------------------------------------------------------------------
    ok("Ungueltige Frist -> 400", req("PATCH", f"/api/chats/{cid}", A.k, {"retention_s": 1234})[0] == 400)
    s, d = req("PATCH", f"/api/chats/{cid}", A.k, {"retention_s": 3600})
    ok("Frist 1 Stunde fuer den Chat", s == 200 and d["chat"]["retention_s"] == 3600)
    s, d, rm = A.send(cid, "verschwindet")
    ok(f"Neue Nachricht laeuft nach 1 h ab ({round(d['expires_at'] - d['created'])})", round(d["expires_at"] - d["created"]) == 3600)
    with connect() as c, c:
        c.execute("UPDATE chat_msg SET expires_at=? WHERE id=?", (time.time() - 1, rm["id"]))
    ok("Abgelaufen: sofort unsichtbar", rm["id"] not in [x["id"] for x in B.read(cid)[1]["messages"]])
    n = CH.purge()
    with connect() as c:
        ok(f"... und vom Aufraeumen geloescht ({n})", n >= 1 and not c.execute("SELECT 1 FROM chat_msg WHERE id=?", (rm["id"],)).fetchone())
    auth.save_setting("chat_retention_max_days", 7)
    time.sleep(WAIT)
    ok("Admin-Hoechstwert 7 Tage: unbegrenzt -> 400, 30 Tage -> 400", req("PATCH", f"/api/chats/{cid}", A.k, {"retention_s": 0})[0] == 400
       and req("PATCH", f"/api/chats/{cid}", A.k, {"retention_s": 30 * 86400})[0] == 400)
    req("PATCH", f"/api/chats/{gid}", A.k, {"retention_s": 86400})
    with connect() as c, c:
        c.execute("UPDATE chat_msg SET created=? WHERE id=?", (time.time() - 8 * 86400, g1["id"]))
    CH.purge()
    with connect() as c:
        ok("Aeltere Nachrichten als der Hoechstwert werden geloescht", not c.execute("SELECT 1 FROM chat_msg WHERE id=?", (g1["id"],)).fetchone())
    auth.save_setting("chat_retention_max_days", 0)
    time.sleep(WAIT)
    req("PATCH", f"/api/chats/{cid}", A.k, {"retention_s": 0})

    # --- Anhaenge ----------------------------------------------------------------------------------------------------
    data = "Geheimer Anhang Inhalt äöü".encode() * 50
    blob, fkey = C.encrypt_file(str(cid), data)
    s, up = req("POST", f"/api/chats/{cid}/files", A.k, raw=blob)
    ok(f"Verschluesselten Anhang hochladen ({s})", s == 200 and up["size"] == len(blob))
    ok("C (kein Mitglied) kann nicht hochladen -> 404", req("POST", f"/api/chats/{cid}/files", Cc.k, raw=blob)[0] == 404)
    ok("B kann den Anhang vor der Nachricht nicht holen -> 404", req("GET", f"/api/chat/files/{up['id']}", B.k)[0] == 404)
    s, _, fm = A.send(cid, "Mit Anhang", files=[{"id": up["id"], "k": C.b64(fkey), "n": "plan.txt", "m": "text/plain", "s": len(data)}])
    ok("Nachricht mit Anhang", s == 200)
    s, got = req("GET", f"/api/chat/files/{up['id']}", B.k)
    hdr = req.last_headers
    ok("B holt den Blob und entschluesselt ihn", s == 200 and C.decrypt_file(str(cid), got, fkey) == data and hdr.get("content-type") == "application/octet-stream"
       and "no-store" in hdr.get("cache-control", ""))
    ok("C -> 404", req("GET", f"/api/chat/files/{up['id']}", Cc.k)[0] == 404)
    ok("Dateiname/Inhalt nicht im Klartext gespeichert", plaintext_hits("plan.txt", "Geheimer Anhang") == []
       and b"Geheimer" not in open(os.path.join(CH.CHAT_DIR, up["id"]), "rb").read())
    ok("Anhang zweimal verwenden -> 400", A.send(cid, "nochmal", files=[{"id": up["id"], "k": "x"}])[0] == 400)
    req("DELETE", f"/api/chats/{cid}/messages/{fm['id']}", A.k)
    ok("Nachricht geloescht -> Anhang weg", req("GET", f"/api/chat/files/{up['id']}", A.k)[0] == 404 and not os.path.exists(os.path.join(CH.CHAT_DIR, up["id"])))
    s, up2 = req("POST", f"/api/chats/{cid}/files", A.k, raw=blob)
    with connect() as c, c:
        c.execute("UPDATE chat_file SET created=? WHERE id=?", (time.time() - 7200, up2["id"]))
    CH.purge()
    ok("Anhang ohne Nachricht nach 1 h aufgeraeumt", not os.path.exists(os.path.join(CH.CHAT_DIR, up2["id"])))
    ok("Anhang zu klein/kaputt -> 400", req("POST", f"/api/chats/{cid}/files", A.k, raw=b"abc")[0] == 400)

    # --- Rate-Limit ---------------------------------------------------------------------------------------------------
    rcl = C.recipients_of(req("GET", f"/api/chats/{cid}/recipients", A.k)[1]["recipients"])
    codes = [A.send(cid, f"n{i}", recipients=rcl)[0] for i in range(65)]
    ok(f"Hoechstens 60 Nachrichten je Minute -> 429 ({codes.count(200)}x 200)", 429 in codes and codes.count(200) <= 60)

    # --- Admin-Schalter -----------------------------------------------------------------------------------------------
    auth.save_setting("chat_enabled", False)
    time.sleep(WAIT)
    ok("Chats abgeschaltet: API 403, Ungelesen 0", req("GET", "/api/chats", A.k)[0] == 403 and req("GET", "/api/chats/unread", A.k)[1]["unread"] == 0)
    ok("... Seite /chats laedt weiter (mit Hinweis)", req("GET", "/chats", A.k)[0] == 200)
    auth.save_setting("chat_enabled", True)
    time.sleep(WAIT)

    # --- Neu beginnen (neue Identitaet) -------------------------------------------------------------------------------
    old_ident = B.ident["ident_pk"]
    B2 = Person("ch-b", setup=False)
    s, _ = req("PUT", "/api/chat/identity", B2.k, dict(C.setup_body("ch-b", B2.ident, B2.dev, **KDF), reset=True))
    k = req("GET", "/api/chat/keys?users=ch-b", A.k)[1]["users"]["ch-b"]
    ok("B beginnt neu: neuer Identitaetsschluessel, alte Geraete entfernt", s == 200 and C.unb64(k["ident_pub"]) != old_ident
       and [d["id"] for d in k["devices"] if not d["revoked"]] == [B2.dev["id"]])
    ok("... Sicherheitsnummer A<->B aendert sich", C.safety_number("ch-a", A.ident["ident_pk"], "ch-b", old_ident)
       != C.safety_number("ch-a", A.ident["ident_pk"], "ch-b", C.unb64(k["ident_pub"])))
    ok("Schluessel fremder Konten ohne Kontakt (C: nur ueber Chats) erreichbar, unbekannte fehlen",
       set(req("GET", "/api/chat/keys?users=ch-a,ch-zz", Cc.k)[1]["users"]) == {"ch-a"})

    # --- Kontoloeschung -----------------------------------------------------------------------------------------------
    from saga.userdata import delete_user_data
    delete_user_data("ch-b")
    with connect() as c, c:
        c.execute("DELETE FROM users WHERE username='ch-b'")              # wie im Admin-Bereich: Daten + Konto
    with connect() as c:
        left = {t: c.execute(q, ("ch-b",)).fetchone()[0] for t, q in (
            ("identity", "SELECT COUNT(*) FROM chat_identity WHERE uid=?"), ("devices", "SELECT COUNT(*) FROM chat_device WHERE uid=?"),
            ("member", "SELECT COUNT(*) FROM chat_member WHERE uid=?"), ("msgs", "SELECT COUNT(*) FROM chat_msg WHERE sender_uid=?"),
            ("keys", "SELECT COUNT(*) FROM chat_msg_key WHERE recipient='backup:' || ?"))}
        dm = c.execute("SELECT 1 FROM chat WHERE id=?", (cid,)).fetchone()
    ok(f"Konto B geloescht: Schluessel, Geraete, Mitgliedschaften, Nachrichten weg; Direkt-Chat weg ({left}, {bool(dm)})", not any(left.values()) and not dm)
    auth.create_user("ch-b", PW, "Neuer Bert")
    s, me = req("GET", "/api/chat/me", ck("ch-b"))
    ok("Neues Konto mit gleichem Namen: keine Chats, keine Schluessel", me["identity"] is None and req("GET", "/api/chats", ck("ch-b"))[1]["chats"] == [])


def _fails(fn):
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _keys_of(mid):
    with connect() as c:
        return {r[0] for r in c.execute("SELECT k.recipient FROM chat_msg_key k JOIN chat_msg m ON m.seq=k.msg_seq WHERE m.id=?", (mid,))}


if __name__ == "__main__":
    main()
