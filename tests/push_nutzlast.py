"""Push-Nutzlast 2 und Glocke bei geloeschten Chat-Nachrichten (2.5, Entscheidung des Maintainers 8.11.2026).

Teil A - strukturierte Nutzlast (saga.push.message): neben title/body (unveraendert) v, kind, lang, n, group_key, chat;
actor NUR mit "Namen zeigen" auf einem verschluesselten Weg (ntfy / "Namen zeigen aus" -> kein actor, Satz ohne Namen);
Sprache des Abos (lang) statt der des Kontos; Groessengrenzen; Abo-Felder lang/caps (POST /api/push/subscriptions, GET
/api/push meldet payload/sub_caps).
Teil B - Glocke: Chat-Eintraege merken sich die gebuendelten Nachrichten (seqs); Loeschen fuer alle (einzeln, mehrere, Admin-
Grabstein, foederierter Weg drop_msg) zieht ab bzw. entfernt den leeren Eintrag; ?group=1 (count/counts/unread_groups)
stimmt danach; Live "notification.changed"; Push-Aktualisierung (update/removed) nur an Abos mit caps "update", nur bei
ungelesenem Eintrag, nie an ntfy; ohne Namen kein actor.
Fake-Empfaenger fuer Web Push in diesem Prozess (127.0.0.1:18295, entschluesselt RFC 8291). Wegwerf-Konten pn-*; raeumt auf.
Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, http.server, json, os, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import auth
from db import connect
from saga import chat, chat_grp, chat_prefs as CP, fed_chat, notify, push
import importlib
importlib.import_module("saga.calls")         # Glocke "verpasster Anruf" (RENDER) meldet sich beim Import an
from saga.core import set_user_lang

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
PW = "Nutzlast-Pass-1234"
FP = 18295
FAKE = f"http://127.0.0.1:{FP}"
res = []
got = []          # (pfad, nutzlast) der Fake-Empfaenger
n5xx = [0]


def ok(n, c, info=""):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    raw = None
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    if r.status >= 500:
        n5xx[0] += 1
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return r.status, data


# --- Web-Push-Empfaenger (wie ein Browser: RFC 8291 entschluesseln) ------------------------------------------------
KEYS = {}


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_keys(name):
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    sec = os.urandom(16)
    KEYS[name] = (priv, pub, sec)
    return b64u(pub), b64u(sec)


def decrypt(name, body):
    priv, ua_pub, sec = KEYS[name]
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ct = body[21 + idlen:]
    shared = priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    ikm = HKDF(hashes.SHA256(), 32, sec, b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    pt = AESGCM(cek).decrypt(nonce, ct, None).rstrip(b"\x00")
    return json.loads(pt[:-1])


class Fake(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        name = self.path.rsplit("/", 1)[-1]
        try:
            got.append((name, decrypt(name, raw) if name in KEYS else json.loads(raw), dict(self.headers)))
        except Exception as ex:  # noqa: BLE001
            got.append((name, {"error": str(ex)}, {}))
        self.send_response(201); self.end_headers()

    def log_message(self, *a):
        pass


def wait_push(name, pred, since=0, timeout=14.0):
    end = time.time() + timeout
    while time.time() < end:
        for g in got[since:]:
            if g[0] == name and pred(g[1]):
                return g
        time.sleep(0.2)
    return None


# --- SSE ---------------------------------------------------------------------------------------------------------
class Stream:
    def __init__(self, cookie):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
        self.r = self.c.getresponse()
        self.ev = []
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

    def wait(self, typ, pred=None, since=0, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if e[0] == typ and (pred is None or pred(e[1])):
                    return e[1]
            time.sleep(0.1)
        return None


# --- Daten -------------------------------------------------------------------------------------------------------
def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM push_queue WHERE uid LIKE 'pn-%'")
        c.execute("DELETE FROM push_sub WHERE uid LIKE 'pn-%'")
        c.execute("DELETE FROM notification WHERE uid LIKE 'pn-%' OR actor_uid LIKE 'pn-%'")
        c.execute("DELETE FROM chat WHERE created_by LIKE 'pn-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'pn-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'pn-%'")


def mkchat(kind, members, name=None):
    now = time.time()
    with connect() as c, c:
        dk = "|".join(sorted(members)) if kind == "dm" else None
        cid = c.execute("INSERT INTO chat (kind, dm_key, group_name, created_by, created) VALUES (?,?,?,?,?)",
                        (kind, dk, name, members[0], now)).lastrowid
        for u in members:
            c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
    return cid


def send(cid, sender, members):
    """Nachricht (ohne echten Ciphertext - der Server liest ihn nie) + Mitteilung wie saga.chat -> (id, seq)"""
    mid = os.urandom(16).hex()
    now = time.time()
    with connect() as c, c:
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, body_ct, sig) "
                        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING seq", (mid, cid, "msg", sender, "dev", int(now * 1000), now, "x", "x")).fetchone()[0]
        c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, cid))
        CP.on_message(c, cid, sender, seq, members)
    return mid, seq


def chat_rows(uid, cid):
    with connect() as c:
        return [(r[0], json.loads(r[1]), r[2]) for r in c.execute(
            "SELECT id, data, read_at FROM notification WHERE uid=? AND kind='chat.message' ORDER BY id", (uid,)).fetchall()
            if json.loads(r[1]).get("chat") == cid]


def bell(cookie, cid):
    s, d = req("GET", "/api/notifications?group=1", cookie)
    row = next((x for x in d.get("items", []) if x.get("group_key") == f"chat:{cid}"), None) if s == 200 else None
    return d, row


def add_sub(uid, name, kind="webpush", caps="update"):
    pub, sec = new_keys(name) if kind == "webpush" else (None, None)
    ep = f"{FAKE}/wp/{name}" if kind == "webpush" else f"{FAKE}/{name}"
    with connect() as c, c:
        return c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, created, caps) VALUES (?,?,?,?,?,?,?,?)",
                         (uid, kind, ep, pub, sec, name, time.time(), caps)).lastrowid


def set_names(cookie, on):
    return req("PUT", "/api/push/settings", cookie, {"names": on})[0]


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "push", "allowed_hosts")}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", FP), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        auth.save_setting("chat_enabled", True)
        auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True})
        auth.save_setting("allowed_hosts", ", ".join(x for x in (prev.get("allowed_hosts") or "", f"127.0.0.1:{FP}") if x))
        for u, n in (("pn-a", "Anna Nutzlast"), ("pn-b", "Bert Nutzlast"), ("pn-l", "L" * 200)):
            auth.create_user(u, PW, n)
        with connect() as c, c:
            c.execute("UPDATE users SET display_name=? WHERE username=?", ("L" * 200, "pn-l"))
        set_user_lang("pn-a", "de")
        A, Bk = ck("pn-a"), ck("pn-b")
        run(A, Bk)
    finally:
        for k, v in keep.items():
            if v is None:
                with connect() as c, c:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
            else:
                auth.save_setting(k, v)
        cleanup()
        srv.shutdown()
    ok("Keine Serverfehler (HTTP 5xx)", n5xx[0] == 0, n5xx[0])
    good = sum(res)
    print(f"PUSH-NUTZLAST {good}/{len(res)} - " + ("ALLES GRUEN" if good == len(res) else "FEHLER"), flush=True)
    sys.exit(0 if good == len(res) else 1)


def run(A, Bk):
    # =============================== Teil A: Nutzlast ================================================================
    s, d = req("GET", "/api/push", A)
    ok("GET /api/push meldet payload 2 und sub_caps", s == 200 and d["server"].get("payload") == 2 and d["server"].get("sub_caps") == ["update"], d.get("server"))
    tok = "ab" * 32
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "apns", "token": tok, "env": "sandbox", "name": "x", "lang": "xx"})
    ok("Abo mit unbekannter Sprache -> 400", s == 400, (s, d))
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "apns", "token": tok, "env": "sandbox", "name": "x", "caps": "update"})
    ok("Abo mit caps ohne Liste -> 400", s == 400, (s, d))
    s, d = req("POST", "/api/push/subscriptions", A, {"type": "apns", "token": tok, "env": "sandbox", "name": "x", "lang": "en",
                                                      "caps": ["update", "spaeter"]})
    with connect() as c:
        r = c.execute("SELECT lang, caps FROM push_sub WHERE uid='pn-a' AND kind='apns'").fetchone()
    ok("Abo mit lang/caps -> 201, gespeichert (unbekannte Faehigkeit weggelassen)", s == 201 and r == ("en", "update"), (s, r))
    s, _ = req("POST", "/api/push/subscriptions", A, {"type": "apns", "token": tok, "env": "sandbox", "name": "x"})
    with connect() as c:
        r = c.execute("SELECT lang, caps FROM push_sub WHERE uid='pn-a' AND kind='apns'").fetchone()
    ok("Erneuern ohne lang/caps behaelt beides", s == 200 and r == ("en", "update"), (s, r))
    s, _ = req("POST", "/api/push/subscriptions", A, {"type": "apns", "token": tok, "env": "sandbox", "name": "x", "lang": "", "caps": []})
    with connect() as c:
        r = c.execute("SELECT lang, caps FROM push_sub WHERE uid='pn-a' AND kind='apns'").fetchone()
        c.execute("DELETE FROM push_sub WHERE uid='pn-a'")
        c.commit()
    ok("lang \"\" und caps [] setzen zurueck (Sprache des Kontos, keine Faehigkeiten)", s == 200 and r == (None, ""), (s, r))

    dm = mkchat("dm", ["pn-a", "pn-b"])
    mem = ["pn-a", "pn-b"]
    ok("Namen zeigen an", set_names(A, True) == 200)
    for _ in range(3):
        send(dm, "pn-b", mem)
    with connect() as c:
        m = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="webpush")
        m_ntfy = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="ntfy")
        m_up = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="unifiedpush", has_keys=False)
        m_en = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="webpush", lang="en")
        m_bad = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="webpush", lang="xx")
    ok("Chat: title/body/url/tag wie bisher", m["body"] == "Neue Nachricht von Bert Nutzlast" and m["url"] == f"/chats#c{dm}"
       and m["tag"] == f"chat-{dm}" and m["kind"] == "chat.message" and isinstance(m["ts"], int), m)
    ok("Chat: v 2, lang de, actor, n 3, group_key chat:<id>, chat", m.get("v") == 2 and m.get("lang") == "de" and m.get("actor") == "Bert Nutzlast"
       and m.get("n") == 3 and m.get("group_key") == f"chat:{dm}" and m.get("chat") == dm, m)
    ok("ntfy: kein actor, Satz ohne Namen", "actor" not in m_ntfy and m_ntfy["body"] == "Neue Nachricht" and m_ntfy.get("n") == 3, m_ntfy)
    ok("UnifiedPush ohne Schluessel: kein actor", "actor" not in m_up and "Bert" not in json.dumps(m_up), m_up)
    ok("Sprache des Abos (en) statt der des Kontos", m_en.get("lang") == "en" and m_en["body"] != m["body"] and "Bert Nutzlast" in m_en["body"], m_en)
    ok("unbekannte Sprache -> Sprache des Kontos", m_bad.get("lang") == "de" and m_bad["body"] == m["body"], m_bad)

    ok("Namen zeigen aus", set_names(A, False) == 200)
    with connect() as c:
        m_off = push.message(c, "pn-a", "chat.message", actor="pn-b", data={"chat": dm}, sub_kind="webpush")
        call = "pn" + os.urandom(8).hex()
        now = time.time()
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, reason, created, ended) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?)", (call, dm, "pn-b", "pn-a", "dev", "video", "ended", "timeout", now, now))
        nid = notify.create(c, "pn-a", "call.missed", "pn-b", data={"chat": dm, "call": call})
        c.commit()
        mc_off = push.message(c, "pn-a", "call.missed", ref=nid, actor="pn-b", sub_kind="webpush")
    ok("Namen zeigen aus: kein actor, Satz und Nutzlast ohne Namen",
       "actor" not in m_off and m_off["body"] == "Neue Nachricht" and "Bert" not in json.dumps(m_off, ensure_ascii=False), m_off)
    ok("Verpasster Anruf ohne Namen: kein actor, n 1, group_key call:<chat>",
       "actor" not in mc_off and "Bert" not in json.dumps(mc_off, ensure_ascii=False) and mc_off.get("n") == 1
       and mc_off.get("group_key") == f"call:{dm}" and mc_off.get("chat") == dm, mc_off)
    set_names(A, True)
    with connect() as c:
        mc = push.message(c, "pn-a", "call.missed", ref=nid, actor="pn-b", sub_kind="webpush")
        mt = push.message(c, "pn-a", "test", sub_kind="webpush")
    ok("Verpasster Anruf mit Namen: actor (steht im Satz)", mc.get("actor") == "Bert Nutzlast" and "Bert Nutzlast" in mc["body"], mc)
    ok("Testmitteilung: v/lang, kein actor/n", mt.get("v") == 2 and mt.get("lang") == "de" and "actor" not in mt and "n" not in mt, mt)
    ok("Keine Seitentitel/Chat-Namen in der Nutzlast (kein page_title)", "page_title" not in m and "page_title" not in mc)

    # Groesse: sehr langer Anzeigename, Web Push (vor Verschluesselung) und APNs-Koerper
    dl = mkchat("dm", ["pn-a", "pn-l"])
    with connect() as c, c:
        CP.on_message(c, dl, "pn-l", 1, ["pn-a", "pn-l"])
    with connect() as c:
        ml = push.message(c, "pn-a", "chat.message", actor="pn-l", data={"chat": dl}, sub_kind="webpush")
    raw = json.dumps(ml, ensure_ascii=False, separators=(",", ":")).encode()
    apns = dict({"aps": {"alert": {"title": ml["title"], "body": ml["body"]}, "sound": "default", "thread-id": ml["tag"]}, "url": ml["url"],
                 "kind": ml["kind"]}, **{k: ml[k] for k in ("chat", "v", "lang", "actor", "n", "group_key") if k in ml})
    ok("Groesse: langer Name <= PAYLOAD_MAX (Web Push/UnifiedPush < 4 KB verschluesselt), APNs < 4 KB",
       len(raw) <= push.PAYLOAD_MAX and len(raw) + 103 < 4096 and len(json.dumps(apns).encode()) < 4096, (len(raw), len(json.dumps(apns))))

    # =============================== Teil B: Glocke ==================================================================
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid='pn-a'")
        c.execute("DELETE FROM push_queue WHERE uid='pn-a'")
    add_sub("pn-a", "upd")                       # nimmt Aktualisierungen an
    add_sub("pn-a", "old", caps="")              # aelteres Abo: nie Aktualisierungen
    add_sub("pn-a", "nt", kind="ntfy")           # ntfy (caps gesetzt): nie Aktualisierungen
    g = mkchat("free", ["pn-a", "pn-b"], "pn-team")
    es = Stream(A)
    time.sleep(0.5)
    msgs = [send(g, "pn-b", mem) for _ in range(3)]
    rows = chat_rows("pn-a", g)
    ok("Glocke: ein Eintrag, n 3, seqs = die drei Nachrichten",
       len(rows) == 1 and rows[0][1].get("n") == 3 and rows[0][1].get("seqs") == [s for _, s in msgs], rows)
    ok("Erste Mitteilung kommt an", wait_push("upd", lambda p: p.get("kind") == "chat.message" and not p.get("update")) is not None, got[-3:])
    nid = rows[0][0]

    mark, gmark = len(es.ev), len(got)
    s, d = req("DELETE", f"/api/chats/{g}/messages/{msgs[0][0]}", Bk)
    ok("Einzeln loeschen -> 200", s == 200, (s, d))
    rows = chat_rows("pn-a", g)
    ok("Eintrag bleibt (gleiche id), n 2, seqs ohne die geloeschte",
       len(rows) == 1 and rows[0][0] == nid and rows[0][1]["n"] == 2 and rows[0][1]["seqs"] == [msgs[1][1], msgs[2][1]], rows)
    d, row = bell(A, g)
    ok("?group=1: count 2, counts [2], unread 1, unread_groups 1", row and row["count"] == 2 and row["counts"] == [2]
       and d["unread"] == 1 and d["unread_groups"] == 1, (row, d.get("unread"), d.get("unread_groups")))
    ev = es.wait("notification.changed", lambda x: x.get("id") == nid, mark)
    ok("Live notification.changed: item (count 2), removed false, group_key, unread_groups",
       ev and ev["removed"] is False and ev["item"]["count"] == 2 and ev["group_key"] == f"chat:{g}" and ev["unread"] == 1
       and ev["unread_groups"] == 1, ev)
    up = wait_push("upd", lambda p: p.get("update") is True, gmark)
    p = up[1] if up else {}
    ok("Push-Aktualisierung: update, n 2, ohne removed, tag/group_key/chat, actor (Namen an)",
       p.get("kind") == "chat.message" and p.get("n") == 2 and "removed" not in p and p.get("tag") == f"chat-{g}"
       and p.get("group_key") == f"chat:{g}" and p.get("chat") == g and p.get("actor") == "Bert Nutzlast" and p.get("v") == 2, p)
    ok("Web Push: Aktualisierung mit eigenem Topic (ersetzt die Mitteilung beim Push-Dienst nicht), Urgency normal",
       up and up[2].get("Topic") == f"uchat-{g}" and up[2].get("Urgency") == "normal", up and up[2])
    time.sleep(1.5)
    ok("Abo ohne caps und ntfy: keine Aktualisierung",
       not any(x[0] in ("old", "nt") and (x[1].get("update") or x[1].get("message") == "") for x in got[gmark:]), got[gmark:])

    mark, gmark = len(es.ev), len(got)
    s, d = req("POST", f"/api/chats/{g}/messages/delete", Bk, {"ids": [msgs[1][0], msgs[2][0]]})
    ok("Mehrere loeschen -> deleted 2", s == 200 and len(d.get("deleted", [])) == 2, (s, d))
    ok("Eintrag leer -> entfernt", chat_rows("pn-a", g) == [])
    d, row = bell(A, g)
    ok("?group=1: keine Chat-Zeile, unread 0, unread_groups 0", row is None and d["unread"] == 0 and d["unread_groups"] == 0, d)
    ev = es.wait("notification.changed", lambda x: x.get("id") == nid and x.get("removed"), mark)
    ok("Live notification.changed: removed, item null, unread 0", ev and ev["item"] is None and ev["unread"] == 0 and ev["unread_groups"] == 0, ev)
    up = wait_push("upd", lambda p: p.get("removed") is True, gmark)
    p = up[1] if up else {}
    ok("Push-Aktualisierung: removed, n 0, body leer, kein actor", p.get("update") is True and p.get("n") == 0 and p.get("body") == ""
       and "actor" not in p and p.get("tag") == f"chat-{g}", p)

    # gelesener Eintrag: Glocke passt sich an, aber kein Push
    mm = [send(g, "pn-b", mem) for _ in range(2)]
    nid2 = chat_rows("pn-a", g)[0][0]
    req("POST", "/api/notifications/read", A, {"ids": [nid2]})
    wait_push("upd", lambda p: False, len(got), timeout=6.0)       # erste Mitteilung der neuen Nachrichten zustellen lassen
    mark, gmark = len(es.ev), len(got)
    req("DELETE", f"/api/chats/{g}/messages/{mm[0][0]}", Bk)
    rows = chat_rows("pn-a", g)
    ok("Gelesener Eintrag: n 1", len(rows) == 1 and rows[0][1]["n"] == 1 and rows[0][2] is not None, rows)
    ok("Gelesener Eintrag: Live-Ereignis ja", es.wait("notification.changed", lambda x: x.get("id") == nid2, mark) is not None)
    time.sleep(6)
    ok("Gelesener Eintrag: keine Push-Aktualisierung", not any(x[1].get("update") for x in got[gmark:]), got[gmark:])

    # Namen zeigen aus: Aktualisierung ohne actor
    set_names(A, False)
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid='pn-a'")
    mm = [send(g, "pn-b", mem) for _ in range(2)]
    wait_push("upd", lambda p: False, len(got), timeout=6.0)
    gmark = len(got)
    req("DELETE", f"/api/chats/{g}/messages/{mm[0][0]}", Bk)
    up = wait_push("upd", lambda p: p.get("update") is True, gmark)
    p = up[1] if up else {}
    ok("Namen zeigen aus: Aktualisierung ohne actor und ohne Namen", up and "actor" not in p and "Bert" not in json.dumps(p, ensure_ascii=False)
       and p.get("n") == 1 and p.get("body") == "Neue Nachricht", p)

    # Noch nicht zugestellte Mitteilung entfaellt, wenn der Eintrag leer wird
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid='pn-a'")
        c.execute("DELETE FROM push_sub WHERE uid='pn-a' AND name IN ('old', 'nt')")
    mid, seq = send(g, "pn-b", mem)
    with connect() as c, c:
        pend = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='pn-a' AND kind='chat.message' AND tries=0").fetchone()[0]
        chat._direct(c, ["pn-a", "pn-b"], "chat.deleted", {"chat_id": g, "id": mid, "seq": seq})
        left = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='pn-a' AND kind='chat.message' AND tries=0").fetchone()[0]
        upd = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='pn-a' AND kind=?", (push.UPDATE_KIND,)).fetchone()[0]
    ok("Wartende Mitteilung einer geloeschten Nachricht entfaellt, Aktualisierung vorgemerkt", pend >= 1 and left == 0 and upd >= 1, (pend, left, upd))

    # Weitere Loeschwege (gleiche Stelle saga.chat._direct): foederiert (drop_msg) und Admin-Grabstein (tomb)
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid='pn-a'")
    mm = [send(g, "pn-b", mem) for _ in range(3)]
    with connect() as c, c:
        fed_chat.drop_msg(c, {"id": g}, mm[0][0])
    r1 = chat_rows("pn-a", g)
    with connect() as c, c:
        chat_grp.tomb(c, chat._load(c, g), mm[1][0])
    r2 = chat_rows("pn-a", g)
    ok("Foederierter Loeschweg (drop_msg): n 3 -> 2", len(r1) == 1 and r1[0][1]["n"] == 2, r1)
    ok("Admin-Grabstein (tomb): n 2 -> 1, seqs nur noch die dritte", len(r2) == 1 and r2[0][1]["n"] == 1 and r2[0][1]["seqs"] == [mm[2][1]], r2)

    # Eintraege von vor 2.5 (ohne seqs) bleiben unberuehrt; Erwaehnungen werden ebenso abgezogen
    with connect() as c, c:
        c.execute("DELETE FROM notification WHERE uid='pn-a'")
        old_id = notify.create(c, "pn-a", "chat.message", "pn-b", data={"chat": g}, bundle=g)
    mid, seq = send(g, "pn-b", mem)
    with connect() as c, c:
        men = notify.create(c, "pn-a", "chat.mention", "pn-b", data={"chat": g, "seq": seq}, bundle=g)
    with connect() as c, c:
        chat._direct(c, mem, "chat.deleted", {"chat_id": g, "id": mid, "seq": seq})
        nrow = c.execute("SELECT data FROM notification WHERE id IN (SELECT id FROM notification WHERE uid='pn-a' AND kind='chat.message')").fetchone()
        mrow = c.execute("SELECT 1 FROM notification WHERE id=?", (men,)).fetchone()
    d0 = json.loads(nrow[0]) if nrow else {}
    ok("Alter Eintrag ohne seqs: Zaehler bleibt (nur die neue Nachricht abgezogen)", d0.get("n") == 1 and d0.get("seqs") == [] and old_id, d0)
    ok("Erwaehnung (chat.mention) mit dieser Nachricht: entfernt", mrow is None)


if __name__ == "__main__":
    main()
