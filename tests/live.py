"""Live-Kanal + Benachrichtigungen (1.4).

SSE-Verbindung (Kopfzeilen, hello, Heartbeat), Ereignisse nur an Berechtigte (B sieht ohne Freigabe nichts von A; nach
Entzug keine Ereignisse mehr), Benachrichtigungen je Art (share.added, share.role, comment.new, comment.reply,
publish.withdrawn ueber den Admin-Bereich), nie an sich selbst, Buendeln, Gelesen (einzeln/alle, andere Tabs), Einstellungen
je Art, "Kein Zugriff mehr" nach Entzug, Aufbewahrung (90 Tage), Kontoloeschung (Admin-Bereich: Verbindung endet,
Benachrichtigungen weg, Ausloeser anonymisiert), Anwesenheit (Zugriff, bearbeitet gerade, Ablauf nach ~45 s),
Verbindungsgrenze (5 je Konto, Abmelden gibt Plaetze frei), Last-Event-ID (Nachholen / reset), Geraete-Schluessel.
Wegwerf-Konten lv-*, Gruppe lv-team; raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, HK_TEST_ADMIN, BASE_URL,
SESSION_SECRET)."""
import base64, hashlib, http.client, json, os, re, secrets, socket, sys, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import notify
from saga.core import _tok_hash

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
AD_ = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
SA = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
PW = "Live-Pass-1234"
WAIT = 5.6                       # Einstellungen sind im Server 5 s zwischengespeichert
res = []
STREAMS = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    raw = None
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if body is not None:
        h["Content-Type"] = "application/json"; raw = json.dumps(body)
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    c.close()
    return r.status, data


def admin(path, form, aid="lv-adm"):
    """Formular an den Admin-Bereich (eigene Sitzung mit CSRF-Token)."""
    tok = "lv-csrf-" + secrets.token_hex(6)
    cookie = SA.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": tok}).encode())).decode()
    c = http.client.HTTPConnection(AD_.hostname, AD_.port, timeout=30)
    body = urllib.parse.urlencode(dict(form, csrf=tok))
    c.request("POST" if form else "GET", path, body=body if form else None,
              headers={"Cookie": "hk_admin=" + cookie, "Content-Type": "application/x-www-form-urlencoded", "Accept-Language": "de"})
    r = c.getresponse(); data = r.read().decode("utf-8", "replace"); c.close()
    return r.status, data


class Stream:
    """SSE-Leser in einem Thread: alle Ereignisse in self.ev [(typ, daten, id)], Heartbeats zaehlen."""

    def __init__(self, cookie=None, hdr=None, last=None, qlast=None):
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
        h = {"Host": HOST, "Accept": "text/event-stream", "Accept-Language": "de"}
        if cookie:
            h["Cookie"] = "saga=" + cookie
        if last:
            h["Last-Event-ID"] = last
        h.update(hdr or {})
        self.c.request("GET", "/api/events" + ("?last=" + urllib.parse.quote(qlast) if qlast else ""), headers=h)
        self.r = self.c.getresponse()
        self.status, self.headers = self.r.status, {k.lower(): v for k, v in self.r.getheaders()}
        self.ev, self.pings, self.closed = [], 0, False
        if self.status == 200:
            threading.Thread(target=self._run, daemon=True).start()
            STREAMS.append(self)
        else:
            self.body = self.r.read(); self.closed = True

    def _run(self):
        cur = {}
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if "event" in cur or "data" in cur:
                        self.ev.append((cur.get("event", "message"), json.loads(cur.get("data") or "null"), cur.get("id")))
                    cur = {}
                elif line.startswith(":"):
                    if line.startswith(": ping"):
                        self.pings += 1
                else:
                    k, _, v = line.partition(":")
                    cur[k] = v[1:] if v.startswith(" ") else v
        except Exception:  # noqa: BLE001 - Verbindung beendet
            pass
        self.closed = True

    def mark(self):
        return len(self.ev)

    def find(self, typ, pred=None, since=0):
        for e in self.ev[since:]:
            if e[0] == typ and (pred is None or pred(e[1])):
                return e
        return None

    def wait(self, typ, pred=None, since=0, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            e = self.find(typ, pred, since)
            if e:
                return e[1]
            if self.closed and not self.find(typ, pred, since):
                break
            time.sleep(0.05)
        e = self.find(typ, pred, since)
        return e[1] if e else None

    def quiet(self, since, secs=1.5, types=None):
        """Kommt in secs Sekunden nichts (bzw. nichts dieser Arten)?"""
        time.sleep(secs)
        return [e for e in self.ev[since:] if types is None or e[0] in types]

    def last_id(self):
        ids = [e[2] for e in self.ev if e[2]]
        return ids[-1] if ids else None

    def close(self):
        try:
            self.c.sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.c.close()
        except Exception:  # noqa: BLE001
            pass


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'lv-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "note_version", "user_kv", "note_publish", "public_name", "notification", "device_login"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'lv-%'")
        c.execute("DELETE FROM notification WHERE actor_uid LIKE 'lv-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'lv-%' OR target LIKE 'lv-%'")
        c.execute("DELETE FROM note_comment WHERE owner_uid LIKE 'lv-%' OR uid LIKE 'lv-%'")
        c.execute("DELETE FROM note_comment_seen WHERE uid LIKE 'lv-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'lv-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'lv-%'")


def notes_of(cookie, **q):
    s, d = req("GET", "/api/notifications?" + urllib.parse.urlencode(q), cookie)
    return d if s == 200 else {"items": [], "unread": -1}


def blocks(*texts):
    return json.dumps({"blocks": [{"id": f"b{i}", "type": "paragraph", "data": {"text": t}} for i, t in enumerate(texts)]})


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    prev_en, prev_scope = prev.get("share_enabled", True), prev.get("share_group_scope", False)
    auth.save_setting("share_enabled", True); auth.save_setting("share_group_scope", False)
    auth.create_user("lv-a", PW, "Anna Live")
    auth.create_user("lv-b", PW, "Bert Live")
    auth.create_user("lv-c", PW, "Cora Live")
    auth.create_user("lv-adm", PW, "Admin Live", is_admin=True)
    with connect() as c, c:
        auth.ensure_group(c, "lv-team")
    auth.set_user_groups(auth.get_user("lv-a")["id"], ["lv-team"])
    auth.set_user_groups(auth.get_user("lv-c")["id"], ["lv-team"])
    A, Bk, Ck = ck("lv-a"), ck("lv-b"), ck("lv-c")
    time.sleep(WAIT)
    for k in (A, Bk, Ck):                     # Texte fuer Apps ('text') in der Konto-Sprache
        req("PUT", "/api/account/lang", k, {"lang": "de"})

    pg = req("POST", "/api/notes", A, {"title": "Live Projekt", "icon": "🛰"})[1]["id"]
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": blocks("Erster Stand"), "format": "blocks", "rev": 0})
    rev = d.get("rev")
    pg2 = req("POST", "/api/notes", A, {"title": "Team Seite"})[1]["id"]

    # --- Verbindung ------------------------------------------------------------------------------------------
    ok("Ohne Anmeldung: 401", Stream().status == 401)
    ok("Falscher Geraete-Schluessel: 401", Stream(hdr={"Authorization": "Bearer hkd_" + "x" * 40}).status == 401)
    sa = Stream(A)
    ok(f"SSE: 200 text/event-stream, no-cache, X-Accel-Buffering: no ({sa.status} {sa.headers.get('content-type')})",
       sa.status == 200 and sa.headers.get("content-type", "").startswith("text/event-stream")
       and "no-cache" in sa.headers.get("cache-control", "") and sa.headers.get("x-accel-buffering") == "no")
    h = sa.wait("hello", timeout=4)
    ok(f"hello sofort ({h})", h and h.get("boot") and h.get("ping"))
    sb = Stream(Bk)
    sc = Stream(Ck)
    ok("B und C verbunden", sb.wait("hello") is not None and sc.wait("hello") is not None)

    # --- B ohne Freigabe sieht nichts -------------------------------------------------------------------------
    mb = sb.mark()
    s, pa = req("POST", "/api/presence", A, {"note_id": pg})
    ok(f"A: Anwesenheit auf eigener Seite ({s}, {[u['username'] for u in pa.get('users', [])]})", s == 200 and [u["username"] for u in pa["users"]] == ["lv-a"]
       and pa["users"][0]["me"] is True and pa.get("rev") == rev)
    ok("B: Anwesenheit ohne Zugriff -> 404", req("POST", "/api/presence", Bk, {"note_id": pg})[0] == 404)
    ok("B: Anwesenheit auf nicht vorhandener Seite -> 404", req("POST", "/api/presence", Bk, {"note_id": 2 ** 62})[0] == 404)
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": blocks("Zweiter Stand geheim"), "format": "blocks", "rev": rev}); rev = d.get("rev")
    req("POST", f"/api/notes/{pg}/comments", A, {"block_id": "b0", "body": "Eigener Kommentar von A"})
    ma = sa.mark()
    ok(f"B ohne Freigabe: kein einziges Ereignis ({[e[0] for e in sb.quiet(mb, 1.5)]})", sb.quiet(mb, 0.1) == [])
    ok("A: keine Benachrichtigung an sich selbst", notes_of(A)["unread"] == 0 and not sa.find("notification", since=0))
    ok("A: eigener Kommentar -> comments.changed an A (Zahlen)", sa.wait("comments.changed", lambda d: d["note_id"] == pg, since=0) is not None)
    ok("A: eigene Speicherung -> kein note.changed an A", sa.find("note.changed", since=0) is None)

    # --- Teilen: share.added + shared.changed -----------------------------------------------------------------
    mb = sb.mark()
    s, _ = req("PUT", f"/api/notes/{pg}/shares", A, {"type": "user", "target": "lv-b", "role": "comment"})
    ok(f"A teilt mit B ({s})", s == 200)
    ok("B: shared.changed live", sb.wait("shared.changed", since=mb) is not None)
    boot_, n_ = sb.find("shared.changed", since=mb)[2].split("-", 1)
    b_before = f"{boot_}-{int(n_) - 1}"               # Last-Event-ID von B kurz vor der Freigabe (Nachholen nach Entzug, unten)
    n = sb.wait("notification", lambda d: d.get("item", {}).get("kind") == "share.added", since=mb)
    it = (n or {}).get("item") or {}
    ok(f"B: notification share.added live (Titel, Rolle, wer) ({it.get('title')}, {it.get('role')}, {(it.get('actor') or {}).get('name')})",
       it.get("title") == "Live Projekt" and it.get("role") == "comment" and it["actor"]["name"] == "Anna Live" and it["access"] and n["unread"] == 1
       and it["note"]["icon"] == "🛰" and it["url"] == f"/notes#{pg}")
    ok(f"... Text fuer Apps: '{it.get('text')}'", it.get("text") == "Anna Live hat eine Seite mit dir geteilt (Kommentieren)")
    lst = notes_of(Bk)
    ok("B: GET /api/notifications zeigt sie (ungelesen 1)", lst["unread"] == 1 and len(lst["items"]) == 1 and lst["items"][0]["kind"] == "share.added")
    ok("A: kein Ereignis fuer die eigene Freigabe", sa.find("notification", since=ma) is None and sa.find("shared.changed", since=ma) is None)
    ok("Fremde Benachrichtigungen: C sieht keine von B", notes_of(Ck)["items"] == [])

    # --- Anwesenheit + note.changed ----------------------------------------------------------------------------
    ma = sa.mark()
    s, pb = req("POST", "/api/presence", Bk, {"note_id": pg, "editing": True})
    ok(f"B: Anwesenheit mit Zugriff ({s}; {sorted(u['username'] for u in pb.get('users', []))})", s == 200
       and sorted(u["username"] for u in pb["users"]) == ["lv-a", "lv-b"])
    p = sa.wait("presence", lambda d: d["note_id"] == pg and any(u["username"] == "lv-b" for u in d["users"]), since=ma)
    ok(f"A: presence live – Bert bearbeitet gerade ({p})", p and any(u["username"] == "lv-b" and u["editing"] and u["name"] == "Bert Live" and not u["me"]
                                                                    for u in p["users"]) and any(u["me"] for u in p["users"]))
    s, d = req("POST", "/api/presence", Bk, {"note_id": pg})
    ok(f"Zu schnelle Anwesenheitsmeldung (zweiter Tab): uebersprungen, kein Fehler ({s}, {d})", s == 200 and d == {"skipped": True})
    mb = sb.mark()
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": blocks("Dritter Stand"), "format": "blocks", "rev": rev}); rev = d.get("rev")
    nc = sb.wait("note.changed", lambda d: d["id"] == pg, since=mb)
    ok(f"B (anwesend): note.changed {{id, rev, by}} ({nc})", nc and nc["rev"] == rev and nc["by"]["name"] == "Anna Live" and nc["by"]["username"] == "lv-a"
       and not nc["gone"])
    ok("C (nicht anwesend, ohne Zugriff): nichts", sc.find("note.changed") is None and sc.find("presence") is None)

    # --- Kommentare: comment.new / comment.reply / Buendeln ------------------------------------------------------
    ma, mb = sa.mark(), sb.mark()
    long_txt = "Bitte pruefen: " + "x" * 200
    s, cm = req("POST", f"/api/notes/{pg}/comments", Bk, {"block_id": "b0", "body": long_txt})
    th = cm.get("thread_id")
    n = sa.wait("notification", lambda d: d.get("item", {}).get("kind") == "comment.new", since=ma)
    it = (n or {}).get("item") or {}
    ok(f"A: comment.new live (Bert, Auszug <= 120 Zeichen + …) ({len(it.get('excerpt', ''))})",
       it.get("actor", {}).get("name") == "Bert Live" and it.get("excerpt", "").startswith("Bitte pruefen") and len(it["excerpt"]) == 121
       and it.get("block_id") == "b0" and it.get("url") == f"/notes#{pg}:b0" and it.get("thread_id") == th)
    cc = sa.wait("comments.changed", lambda d: d["note_id"] == pg, since=ma)
    ok(f"A: comments.changed mit eigenen Zahlen ({cc})", cc and cc["counts"]["open"] == 2 and cc["counts"]["unread"] == 1 and cc["counts"]["blocks"].get("b0") == 2)
    cb = sb.wait("comments.changed", lambda d: d["note_id"] == pg, since=mb)
    ok(f"B: comments.changed mit B's Zahlen (nur A's Unterhaltung ungelesen) ({cb})", cb and cb["counts"]["unread"] == 1 and cb["counts"]["open"] == 2)
    ok("B: keine Benachrichtigung fuer den eigenen Kommentar", sb.find("notification", lambda d: d.get("item", {}).get("kind", "").startswith("comment"), since=mb) is None)
    mb = sb.mark()
    req("POST", f"/api/notes/{pg}/comments", A, {"thread_id": th, "body": "Antwort eins"})
    r1 = sb.wait("notification", lambda d: d.get("item", {}).get("kind") == "comment.reply", since=mb)
    ok(f"B: comment.reply live (Anna, Auszug) ({(r1 or {}).get('item', {}).get('excerpt')})", r1 and r1["item"]["actor"]["name"] == "Anna Live"
       and r1["item"]["excerpt"] == "Antwort eins" and r1["item"]["count"] == 1)
    mb2 = sb.mark()
    req("POST", f"/api/notes/{pg}/comments", A, {"thread_id": th, "body": "Antwort zwei"})
    r2 = sb.wait("notification", lambda d: d.get("item", {}).get("kind") == "comment.reply", since=mb2)
    ok(f"B: zweite Antwort gebuendelt (n=2, ersetzt die erste) ({(r2 or {}).get('item', {}).get('count')})",
       r2 and r2["item"]["count"] == 2 and r2["replaces"] == r1["item"]["id"] and r2["item"]["excerpt"] == "Antwort zwei")
    lst = notes_of(Bk)
    replies = [x for x in lst["items"] if x["kind"] == "comment.reply"]
    ok(f"B: Liste: eine Benachrichtigung fuer die Unterhaltung, ungelesen 2 gesamt ({len(replies)}, {lst['unread']})",
       len(replies) == 1 and replies[0]["count"] == 2 and lst["unread"] == 2)
    ok("A: Antworten in eigener Unterhaltung -> keine Benachrichtigung an A selbst",
       not [x for x in notes_of(A)["items"] if x["kind"] == "comment.reply"])

    # --- Gelesen ----------------------------------------------------------------------------------------------
    sb2 = Stream(Bk)                                  # zweiter Tab von B
    sb2.wait("hello")
    m2 = sb2.mark()
    share_id = [x["id"] for x in lst["items"] if x["kind"] == "share.added"][0]
    s, d = req("POST", "/api/notifications/read", Bk, {"ids": [share_id]})
    ok(f"B: eine gelesen -> ungelesen 1 ({d})", s == 200 and d["unread"] == 1)
    rd = sb2.wait("notification", lambda d: d.get("read") == [share_id], since=m2)
    ok("B: anderer Tab bekommt 'gelesen' live", rd and rd["unread"] == 1)
    ok("B: ?unread=1 nur ungelesene", [x["kind"] for x in notes_of(Bk, unread=1)["items"]] == ["comment.reply"])
    s, d = req("POST", "/api/notifications/read", Bk, {"all": True})
    ok("B: alle gelesen -> 0", s == 200 and d["unread"] == 0 and sb2.wait("notification", lambda d: d.get("read") == "all", since=m2))
    ok("Gelesen: fremde IDs aendern nichts", req("POST", "/api/notifications/read", Ck, {"ids": [share_id]})[1]["unread"] == 0
       and notes_of(Bk)["items"][-1]["read"] is True)
    ok("Gelesen: ungueltige Angabe -> 400", req("POST", "/api/notifications/read", Bk, {"ids": "x"})[0] == 400)
    sb2.close()
    p = notes_of(Bk, limit=1)
    ok(f"Seiten: ?limit=1 -> more + next ({p.get('more')}, {p.get('next')})", p["more"] and p["next"] and len(p["items"]) == 1)
    p2 = notes_of(Bk, limit=1, before=p["next"])
    ok("... ?before=next liefert die naechste", p2["items"] and p2["items"][0]["id"] < p["next"])

    # --- Einstellungen je Art ----------------------------------------------------------------------------------
    s, st = req("GET", "/api/notifications/settings", A)
    ok(f"Einstellungen: alle Arten an ({[k['kind'] for k in st.get('kinds', [])]})", s == 200 and all(k["on"] for k in st["kinds"])
       and [k["kind"] for k in st["kinds"]] == list(notify.KINDS))
    s, st = req("PUT", "/api/notifications/settings", A, {"comment.new": False})
    ok("A: comment.new aus", s == 200 and not next(k for k in st["kinds"] if k["kind"] == "comment.new")["on"])
    ok("Unbekannte Art -> 400", req("PUT", "/api/notifications/settings", A, {"comment.foo": False})[0] == 400)
    ok("Kein Wahrheitswert -> 400", req("PUT", "/api/notifications/settings", A, {"share.added": "nein"})[0] == 400)
    u0, ma = notes_of(A)["unread"], sa.mark()
    req("POST", f"/api/notes/{pg}/comments", Bk, {"block_id": "b1", "body": "Neue Unterhaltung"})
    time.sleep(1.2)
    ok("A (comment.new aus): keine neue Benachrichtigung", notes_of(A)["unread"] == u0
       and sa.find("notification", since=ma) is None and sa.find("comments.changed", since=ma) is not None)
    req("PUT", "/api/notifications/settings", A, {"settings": {"comment.new": True}})

    # --- Rolle aendern, Gruppe -----------------------------------------------------------------------------------
    mb = sb.mark()
    req("PUT", f"/api/notes/{pg}/shares", A, {"type": "user", "target": "lv-b", "role": "edit"})
    n = sb.wait("notification", lambda d: d.get("item", {}).get("kind") == "share.role", since=mb)
    ok(f"B: share.role live (Bearbeiten) ({(n or {}).get('item', {}).get('text')})", n and n["item"]["role"] == "edit"
       and n["item"]["text"] == "Anna Live hat deine Rolle geändert: Bearbeiten")
    mb = sb.mark()
    req("PUT", f"/api/notes/{pg}/shares", A, {"type": "user", "target": "lv-b", "role": "edit"})
    time.sleep(0.8)
    ok("Gleiche Rolle noch einmal: keine Benachrichtigung", sb.find("notification", since=mb) is None)
    mc, ma = sc.mark(), sa.mark()
    s, _ = req("PUT", f"/api/notes/{pg2}/shares", A, {"type": "group", "target": "lv-team", "role": "view"})
    n = sc.wait("notification", lambda d: d.get("item", {}).get("kind") == "share.added", since=mc)
    ok(f"Gruppe: Mitglied C bekommt share.added + shared.changed ({s})", n and n["item"]["title"] == "Team Seite" and n["item"]["role"] == "view"
       and sc.wait("shared.changed", since=mc) is not None)
    time.sleep(0.5)
    ok("Gruppe: A (Mitglied, aber Ausloeser) bekommt nichts", sa.find("notification", since=ma) is None and sa.find("shared.changed", since=ma) is None)
    ok("Gruppe: B (kein Mitglied) bekommt nichts", sb.find("notification", lambda d: d.get("item", {}).get("note", {}) and d["item"]["note"]["id"] == pg2) is None)

    # --- Last-Event-ID ------------------------------------------------------------------------------------------
    last = sb.last_id()
    sb.close()
    time.sleep(0.6)
    req("PUT", f"/api/notes/{pg}/shares", A, {"type": "user", "target": "lv-b", "role": "comment"})   # waehrend B getrennt ist
    time.sleep(0.6)
    sb = Stream(Bk, last=last)
    n = sb.wait("notification", lambda d: d.get("item", {}).get("kind") == "share.role", timeout=4)
    ok(f"Last-Event-ID: verpasste Benachrichtigung nachgeholt ({last})", n and n["item"]["role"] == "comment" and sb.find("reset") is None)
    sq = Stream(Bk, qlast=last)                       # Browser nach "bye": neue EventSource mit ?last=
    ok("?last= (neue EventSource nach bye) holt ebenso nach", sq.wait("notification", lambda d: d.get("item", {}).get("kind") == "share.role", timeout=4)
       and sq.find("reset") is None)
    sq.close()
    sx = Stream(Bk, last="deadbeef-1")
    ok("Last-Event-ID aus anderem Serverlauf -> reset", sx.wait("reset", timeout=4) is not None)
    sx.close()

    # --- Entzug: danach keine Ereignisse mehr ---------------------------------------------------------------------
    mb = sb.mark()
    req("DELETE", f"/api/notes/{pg}/shares?type=user&target=lv-b", A)
    ok("Entzug: B bekommt shared.changed", sb.wait("shared.changed", since=mb) is not None)
    mb = sb.mark()
    req("POST", "/api/presence", A, {"note_id": pg})
    s, d = req("PATCH", f"/api/notes/{pg}", A, {"body": blocks("Vierter Stand nach Entzug"), "format": "blocks", "rev": rev}); rev = d.get("rev")
    req("POST", f"/api/notes/{pg}/comments", A, {"block_id": "b0", "body": "Nach dem Entzug"})
    got = [e[0] for e in sb.quiet(mb, 1.5)]
    ok(f"Nach Entzug: B bekommt keine Ereignisse zur Seite ({got})", got == [])
    ok("Nach Entzug: Anwesenheit -> 404", req("POST", "/api/presence", Bk, {"note_id": pg})[0] == 404)
    lst = notes_of(Bk)
    gone = [x for x in lst["items"] if x["kind"] in ("share.added", "share.role", "comment.reply")]
    ok(f"Nach Entzug: Liste zeigt 'Kein Zugriff mehr', keinen Titel/Auszug/Link ({[x['title'] for x in gone]})",
       gone and all(not x["access"] and x["title"] == "Kein Zugriff mehr" and x["note"] is None and "excerpt" not in x and x["url"] is None for x in gone))
    raw = json.dumps(lst, ensure_ascii=False)
    ok("... und nirgends steht der Titel oder Kommentartext", "Live Projekt" not in raw and "Antwort eins" not in raw and "Antwort zwei" not in raw
       and "🛰" not in raw)
    sr = Stream(Bk, last=b_before)                   # Nachholen ab VOR der Freigabe - jetzt ohne Zugriff
    sr.wait("hello")
    sr.wait("shared.changed", timeout=3)             # seitenunabhaengige Ereignisse kommen weiter
    time.sleep(0.8)
    raw = json.dumps([e[:2] for e in sr.ev], ensure_ascii=False)
    page_ev = [e[0] for e in sr.ev if (e[0] in ("comments.changed", "presence") and e[1].get("note_id") == pg)
               or (e[0] == "note.changed" and e[1].get("id") == pg and not e[1].get("gone"))
               or (e[0] == "notification" and (e[1].get("item") or {}).get("access"))]
    ok(f"Nach Entzug: Last-Event-ID holt nichts zur Seite nach (Rechte beim Nachholen) ({page_ev})",
       sr.find("shared.changed") is not None and sr.find("reset") is None and page_ev == []
       and "Live Projekt" not in raw and "Antwort eins" not in raw and "Antwort zwei" not in raw and "Bitte pruefen" not in raw)
    sr.close()

    # --- Fremde Seite (Cross-Site/Nachbar-Subdomain): EventSource mit Cookie abgewiesen ------------------------------
    ok("SSE: Sec-Fetch-Site cross-site -> 403", Stream(A, hdr={"Sec-Fetch-Site": "cross-site"}).status == 403)
    ok("SSE: Sec-Fetch-Site same-site -> 403", Stream(A, hdr={"Sec-Fetch-Site": "same-site"}).status == 403)
    so = Stream(A, hdr={"Sec-Fetch-Site": "same-origin"})
    ok("SSE: Sec-Fetch-Site same-origin -> 200", so.status == 200 and so.wait("hello") is not None)
    so.close()

    # --- Verbindungsgrenze ------------------------------------------------------------------------------------------
    more = [Stream(Ck) for _ in range(5)]
    bye = sc.wait("bye", timeout=4)
    ok(f"Grenze: 6. Verbindung schliesst die aelteste mit bye/limit ({bye})", bye and bye["reason"] == "limit")
    time.sleep(0.5)
    ok("... die aelteste ist danach zu, die anderen offen", sc.closed and not any(x.closed for x in more))
    for x in more[1:]:
        x.close()
    time.sleep(1.5)
    again = [Stream(Ck) for _ in range(4)]
    time.sleep(1.0)
    ok("Getrennte Verbindungen geben ihren Platz frei (4 neue, kein bye)", not any(x.find("bye") for x in [more[0]] + again))
    s, html = admin("/settings", {})
    m = re.search(r"(?:Aktive Live-Verbindungen|Active live connections|Conexiones en directo activas): (\d+)", html)
    ok(f"Admin 'Allgemein': Anzahl aktiver Live-Verbindungen ({m.group(1) if m else html[:80]})", s == 200 and m and int(m.group(1)) >= 7)
    for x in again:
        x.close()
    sc = more[0]

    # --- Geraete-Schluessel -------------------------------------------------------------------------------------------
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?,?,?,?,?,?)",
                  ("lv-c", "Cora Live", "Testgeraet", _tok_hash(tok), tok[:8], time.time()))
    sd = Stream(hdr={"Authorization": "Bearer " + tok})
    ok("App: Geraete-Schluessel (Bearer hkd_...) -> hello", sd.status == 200 and sd.wait("hello") is not None)
    md = sd.mark()
    req("POST", f"/api/notes/{pg2}/comments", A, {"block_id": "", "body": "Fuer das Team"})
    ok("App: bekommt comments.changed fuer geteilte Seite", sd.wait("comments.changed", lambda d: d["note_id"] == pg2, since=md) is not None)
    sd.close()

    # --- Admin zieht Veroeffentlichung zurueck -> publish.withdrawn (live) ------------------------------------------
    with connect() as c, c:
        c.execute("INSERT INTO note_publish (note_id, uid, slug, created, updated, active) VALUES (?,?,?,?,?,1)", (pg2, "lv-a", "lv-team-seite", time.time(), time.time()))
    ma = sa.mark()
    s, _ = admin("/published", {"action": "withdraw", "id": str(pg2)})
    n = sa.wait("notification", lambda d: d.get("item", {}).get("kind") == "publish.withdrawn", since=ma)
    ok(f"Admin zieht zurueck: A bekommt publish.withdrawn live, ohne Namen des Admins ({s}, {(n or {}).get('item', {}).get('text')})",
       n and n["item"]["actor"] is None and n["item"]["title"] == "Team Seite" and n["item"]["text"] == "Ein Admin hat deine Veröffentlichung zurückgezogen")

    # --- Seite geloescht, waehrend jemand sie offen hat -> note.changed {gone} ohne Namen -----------------------------
    pg3 = req("POST", "/api/notes", A, {"title": "Kurzlebig"})[1]["id"]
    req("PUT", f"/api/notes/{pg3}/shares", A, {"type": "user", "target": "lv-c", "role": "view"})
    req("POST", "/api/presence", Ck, {"note_id": pg3})
    mc = sc.mark()
    req("DELETE", f"/api/notes/{pg3}", A)
    g = sc.wait("note.changed", lambda d: d["id"] == pg3, since=mc)
    ok(f"Geloeschte Seite: Anwesende bekommen 'gone', ohne Namen ({g})", g and g["gone"] is True and g["by"] is None and g["rev"] is None)

    # --- Anwesenheit laeuft ab (~45 s), Heartbeat ------------------------------------------------------------------
    req("POST", "/api/presence", Ck, {"note_id": pg2})
    time.sleep(1.1)
    ma = sa.mark()
    s, d = req("POST", "/api/presence", A, {"note_id": pg2})
    ok(f"A sieht C auf der Team-Seite ({[u['username'] for u in d.get('users', [])]})", s == 200 and "lv-c" in [u["username"] for u in d["users"]])
    t0, p = time.time(), None
    while p is None and time.time() - t0 < 62:      # A meldet sich weiter (wie die offene Seite alle 20 s), C nicht mehr
        p = sa.wait("presence", lambda d: d["note_id"] == pg2 and "lv-c" not in [u["username"] for u in d["users"]], since=ma, timeout=15)
        if p is None:
            req("POST", "/api/presence", A, {"note_id": pg2})
    ok(f"Anwesenheit von C laeuft ohne Meldung ab ({time.time() - t0:.0f} s)", p is not None and 40 <= time.time() - t0 <= 58
       and [u["username"] for u in p["users"]] == ["lv-a"])
    ok(f"Heartbeat-Kommentar kommt (A: {sa.pings} ping)", sa.pings >= 1)

    # --- Aufbewahrung 90 Tage --------------------------------------------------------------------------------------
    now = time.time()
    with connect() as c, c:
        c.execute("INSERT INTO notification (uid, kind, actor_uid, note_id, data, created) VALUES ('lv-a','share.added','lv-b',?, '{}', ?)", (pg, now - 91 * 86400))
        c.execute("INSERT INTO notification (uid, kind, actor_uid, note_id, data, created) VALUES ('lv-a','share.added','lv-b',?, '{}', ?)", (pg, now - 89 * 86400))
    n = notify.purge()
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM notification WHERE uid='lv-a' AND created < ?", (now - 80 * 86400,)).fetchone()[0]
    ok(f"Aufbewahrung: aelter als 90 Tage geloescht, 89 Tage bleibt ({n}, {left})", n >= 1 and left == 1)

    # --- Kontoloeschung im Admin-Bereich ------------------------------------------------------------------------------
    with connect() as c:
        nb = c.execute("SELECT COUNT(*) FROM notification WHERE uid='lv-b'").fetchone()[0]
    mb = sb.mark()
    s, _ = admin(f"/users/{auth.get_user('lv-b')['id']}", {"action": "delete", "confirm": "lv-b"})
    bye = sb.wait("bye", since=mb, timeout=6)
    ok(f"Konto B geloescht: Live-Verbindung endet sofort (bye/account) ({s}, {bye})", bye and bye["reason"] == "account")
    time.sleep(0.3)
    ok("... Verbindung ist zu", sb.closed)
    with connect() as c:
        n_b = c.execute("SELECT COUNT(*) FROM notification WHERE uid='lv-b'").fetchone()[0]
        n_act = c.execute("SELECT COUNT(*) FROM notification WHERE actor_uid='lv-b'").fetchone()[0]
        n_data = c.execute("SELECT COUNT(*) FROM notification WHERE data LIKE '%lv-b%'").fetchone()[0]
    ok(f"... Benachrichtigungen von B weg ({nb} -> {n_b}), B als Ausloeser anonymisiert ({n_act}, {n_data})", nb > 0 and n_b == 0 and n_act == 0 and n_data == 0)
    it = [x for x in notes_of(A)["items"] if x["kind"] == "comment.new"]
    ok(f"... A sieht 'Gelöschtes Konto' ({it[0]['text'] if it else None})", it and it[0]["actor"] is None and it[0]["text"] == "Gelöschtes Konto hat deine Seite kommentiert")
    auth.create_user("lv-b", PW, "Neue Bert")
    ok("Neues Konto mit gleichem Namen erbt keine Benachrichtigungen", notes_of(ck("lv-b"))["items"] == [])

    for x in list(STREAMS):
        x.close()
    auth.save_setting("share_enabled", prev_en); auth.save_setting("share_group_scope", prev_scope)
    cleanup()
    print(f"\nLIVE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        for x in list(STREAMS):
            x.close()
        cleanup()
        raise
