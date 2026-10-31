"""Chat-Alltag (2.5, Spezifikation A + C) - Server-Seite, PyNaCl als Client; Chats v1 (chat_v3 aus) und Stufe 3.

Geprueft: Features in /api/version (mute2, chatpin1, markunread1, chathide1, chatmention1, chatfmt1, chatmedia1, unfurl1 nur
mit Admin-Schalter); PATCH /api/chats/{cid}: muted_until (Zeitpunkt / -1 / 0) und muted (alt, abgeleitet, Spiegel
chat_member.muted), Ablauf (serverseitig, danach wieder Mitteilungen), notify all|mentions|none inkl. Push-Entscheidung
(Benachrichtigung, saga.push.message), "mentions" = nur Erwaehnung und Antworten auf eigene Nachrichten; ungueltige Angaben
400; pinned hoechstens 5 (409) und Sortierung (angeheftete oben, in Reihenfolge); unread_mark zaehlt als 1 (Chat, Zaehler)
und endet mit dem Lesen; hidden nur Gruppen-/Seiten-Chat (Direkt-Chat 400), fehlt in der Liste (?hidden=1 zeigt sie), keine
Mitteilung, kommt bei Erwaehnung bzw. beim Oeffnen zurueck; mentions nur Mitglieder (andere still ignoriert, Absender nie),
Live chat.message mit mentions (Kennungen, nie Anmeldenamen), ungueltiges Feld 400; ?has=file (Medienuebersicht), ?has=link
400; Linkvorschau GET /api/chat/unfurl gegen einen lokalen Testserver (127.0.0.2, SAGA_UNFURL_TEST_PUBLIC): Titel/
Beschreibung/Seite/Bild (JPEG neu kodiert, klein), nur text/html, Groessenlimit, Weiterleitung auf private Adresse (127.0.0.1)
gesperrt, direkte private Ziele leer, file:/Zugangsdaten/fremder Port 400, Admin-Schalter aus 403. Kein Klartext auf dem
Server. Wegwerf-Konten cl-*, Gruppe cl-team; raeumt auf.
Server-Umgebung: SAGA_UNFURL_TEST_PUBLIC=127.0.0.2 (sonst werden die Vorschau-Faelle uebersprungen und als FEHLER gezaehlt)."""
import http.server, json, os, socket, sys, threading, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402
import chat_v3_umstellung as U  # noqa: E402  (Konto mit Zertifikaten v2: P)
from saga import chat_prefs as CP, push  # noqa: E402

res = []
WAIT = 5.6
SECRET = "Treffpunkt Suedtor geheim 77"


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cl-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cl-%' OR dm_key LIKE 'cl-%' OR created_by LIKE 'cl-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cl-%')")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'cl-%'")
        c.execute("DELETE FROM notification WHERE uid LIKE 'cl-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cl-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cl-%'")


def notifs(uid, cid):
    with connect() as c:
        return [r[0] for r in c.execute("SELECT kind FROM notification WHERE uid=? AND data LIKE ? ORDER BY id", (uid, '{"chat": %d, %%' % cid))]


def clear_notifs(*uids):
    with connect() as c, c:
        for u in uids:
            c.execute("DELETE FROM notification WHERE uid=?", (u,))


def push_msg(uid, cid, kind="chat.message"):
    with connect() as c:
        return push.message(c, uid, kind, actor=None, data={"chat": cid})


class Chat:
    """Senden je nach Stufe: v1 (Person.send) bzw. v2 (encrypt_v2)."""

    def __init__(self, v3):
        self.v3 = v3

    def send(self, u, ch, text, mentions=None, reply_to=None):
        extra = {"mentions": mentions} if mentions is not None else {}
        if not self.v3:
            s, d, m = u.send(ch["id"], text, reply_to=reply_to, extra=extra)
            return s, d, m
        s, d = u.api("GET", "/api/chat/v2/keys?handles=" + ",".join(m["handle"] for m in ch["members"]))
        rc = C.recipients_v2([e for e in d["keys"].values() if e])
        m = C.encrypt_v2(ch["ref"], u.handle, u.dev, rc, json.dumps({"v": 1, "type": "text", "text": text}), reply_to=reply_to)
        m.update(v=2, **extra)
        s, r = u.api("POST", f"/api/chats/{ch['id']}/messages", m)
        return s, r, m


def ref_of(ch, u):
    """Kennung eines Mitglieds wie ein Client sie sieht (Stufe 3: Handle; v1: ref)."""
    m = next(x for x in ch["members"] if x["name"] == u.name)
    return m.get("handle") or m.get("ref")


def series(label, A, B, Cc, D, v3):
    pre = f"[{label}] "
    X = Chat(v3)
    rb = A.api("GET", "/api/chat/people?q=" + urllib.parse.quote(B.name))[1]["users"][0]["username"]
    dm = A.api("POST", "/api/chats", {"kind": "dm", "user": rb, "device": A.dev["id"]})[1]["chat"]
    ok(pre + f"Direkt-Chat (v={dm.get('v')})", dm.get("v") == (3 if v3 else None) and dm.get("muted_until") == 0 and dm.get("notify") == "all"
       and dm.get("pinned") is False and dm.get("hidden") is False and dm.get("can_hide") is False, dm)
    cid = dm["id"]
    # --- A1 Stummschalten mit Dauer ---------------------------------------------------------------------------------------
    for bad in ({"muted_until": "morgen"}, {"muted_until": True}, {"muted_until": -2}, {"notify": "laut"}, {"pinned": 1}, {"unread_mark": "ja"}):
        s, _ = B.api("PATCH", f"/api/chats/{cid}", bad)
        ok(pre + f"ungueltig {bad} -> 400 ({s})", s == 400)
    until = time.time() + 3600
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"muted_until": until})
    ch = d.get("chat") or {}
    ok(pre + f"muted_until in 1 h -> muted true, muted_until gesetzt ({s})", s == 200 and ch.get("muted") is True and abs(ch.get("muted_until", 0) - until) < 1, ch)
    clear_notifs(B.uid)
    X.send(A, dm, "stumm 1")
    ok(pre + "stumm: keine Benachrichtigung", notifs(B.uid, cid) == [], notifs(B.uid, cid))
    ok(pre + "stumm: Push-Entscheidung nein (saga.push.message)", push_msg(B.uid, cid) is None)
    with connect() as c, c:                        # Ablauf vorspulen
        c.execute("UPDATE chat_pref SET muted_until=? WHERE chat_id=? AND uid=?", (time.time() - 5, cid, B.uid))
    ok(pre + "Frist abgelaufen: Push-Entscheidung wieder ja (vor dem Aufraeumen)", push_msg(B.uid, cid) is not None)
    bc = B.chat(cid)
    ok(pre + "Frist abgelaufen: muted false (Liste raeumt auf, Spiegel 0)", bc.get("muted") is False and bc.get("muted_until") == 0, bc)
    with connect() as c:
        ok(pre + "chat_member.muted wieder 0", c.execute("SELECT muted FROM chat_member WHERE chat_id=? AND uid=?", (cid, B.uid)).fetchone()[0] == 0)
    X.send(A, dm, "laut 1")
    ok(pre + "nach Ablauf: Benachrichtigung chat.message", notifs(B.uid, cid) == ["chat.message"], notifs(B.uid, cid))
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"muted": True})
    ch = d.get("chat") or {}
    ok(pre + "alt: muted true -> muted_until -1 (fuer immer)", s == 200 and ch.get("muted") is True and ch.get("muted_until") == -1, ch)
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"muted_until": 0})
    ok(pre + "muted_until 0 -> aus", s == 200 and (d.get("chat") or {}).get("muted") is False)
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"muted_until": time.time() - 100})
    ok(pre + "Zeitpunkt in der Vergangenheit -> aus", s == 200 and (d.get("chat") or {}).get("muted") is False)
    # notify mentions
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"notify": "mentions"})
    ok(pre + "notify mentions", s == 200 and (d.get("chat") or {}).get("notify") == "mentions")
    clear_notifs(B.uid)
    X.send(A, dm, "ohne Erwaehnung")
    ok(pre + "notify mentions: normale Nachricht -> nichts", notifs(B.uid, cid) == [], notifs(B.uid, cid))
    s, _, mb = X.send(B, B.chat(cid), "Bs eigene")
    s, _, _ = X.send(A, dm, "Antwort auf B", reply_to=mb["id"])
    ok(pre + "notify mentions: Antwort auf eigene Nachricht -> chat.message", notifs(B.uid, cid) == ["chat.message"], notifs(B.uid, cid))
    clear_notifs(B.uid)
    mk = B.ev.mark()
    s, _, mm = X.send(A, dm, "@Bert schau", mentions=[ref_of(dm, B)])
    ok(pre + f"notify mentions: Erwaehnung -> chat.mention ({s})", s == 200 and notifs(B.uid, cid) == ["chat.mention"], notifs(B.uid, cid))
    pm_ = push_msg(B.uid, cid, "chat.mention") or {}
    ok(pre + "Push-Entscheidung chat.mention ja (Text 'erwaehnt')", any(w in str(pm_.get("body")) for w in ("erwähnt", "mentioned")), pm_)
    e = B.ev.wait("chat.message", lambda x: x.get("id") == mm["id"], since=mk)
    ok(pre + "Live chat.message mit mentions (Kennung, kein Anmeldename)", bool(e) and e.get("mentions") and B.uid not in json.dumps(e.get("mentions")), e)
    ok(pre + "mention_seq im Chat", B.chat(cid).get("mention_seq", 0) > 0)
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"notify": "none"})
    clear_notifs(B.uid)
    X.send(A, dm, "@Bert nochmal", mentions=[ref_of(dm, B)])
    ok(pre + "notify none: auch Erwaehnung -> nichts", notifs(B.uid, cid) == [] and push_msg(B.uid, cid, "chat.mention") is None)
    B.api("PATCH", f"/api/chats/{cid}", {"notify": "all"})
    s, _, _ = X.send(A, dm, "kaputt", mentions="Bert")
    ok(pre + f"mentions kein Array -> 400 ({s})", s == 400)
    # --- A3 als ungelesen markieren ---------------------------------------------------------------------------------------
    last = B.chat(cid)["last_seq"]
    B.api("POST", f"/api/chats/{cid}/read", {"seq": last})
    n0 = B.api("GET", "/api/chats/unread")[1]["unread"]
    s, d = B.api("PATCH", f"/api/chats/{cid}", {"unread_mark": True})
    ch = d.get("chat") or {}
    ok(pre + "unread_mark: zaehlt als 1 (Chat)", s == 200 and ch.get("unread_mark") is True and ch.get("unread") == 1, ch)
    ok(pre + "unread_mark: Zaehler +1", B.api("GET", "/api/chats/unread")[1]["unread"] == n0 + 1)
    lst = B.api("GET", "/api/chats")[1]
    ok(pre + "unread_mark: in /api/chats", next(x for x in lst["chats"] if x["id"] == cid)["unread"] == 1)
    B.api("POST", f"/api/chats/{cid}/read", {"seq": last})
    ch = B.chat(cid)
    ok(pre + "Lesen loescht die Markierung", ch.get("unread_mark") is False and ch.get("unread") == 0, ch)
    # --- A4 ausblenden ------------------------------------------------------------------------------------------------------
    s, _ = B.api("PATCH", f"/api/chats/{cid}", {"hidden": True})
    ok(pre + f"hidden im Direkt-Chat -> 400 ({s})", s == 400)
    nm = "cl-team" + ("3" if v3 else "1")
    with connect() as c, c:
        auth.ensure_group(c, nm)
    for u in (A, B, Cc):
        auth.set_user_groups(auth.get_user(u.uid)["id"], [nm])
    time.sleep(0.3)
    g = A.api("POST", "/api/chats", {"kind": "group", "group": nm, "device": A.dev["id"]})[1]["chat"]
    gid = g["id"]
    bg = B.chat(gid)
    ok(pre + "Gruppen-Chat: can_hide", bg.get("can_hide") is True)
    s, d = B.api("PATCH", f"/api/chats/{gid}", {"hidden": True})
    ok(pre + f"Gruppen-Chat ausblenden ({s})", s == 200 and (d.get("chat") or {}).get("hidden") is True)
    lst = B.api("GET", "/api/chats")[1]
    ok(pre + "ausgeblendet: fehlt in /api/chats, hidden_count 1", gid not in [x["id"] for x in lst["chats"]] and lst.get("hidden_count") == 1, lst.get("hidden_count"))
    ok(pre + "?hidden=1 zeigt ihn", [x["id"] for x in B.api("GET", "/api/chats?hidden=1")[1]["chats"]] == [gid])
    clear_notifs(B.uid)
    n0 = B.api("GET", "/api/chats/unread")[1]["unread"]
    X.send(A, g, "an alle")
    ok(pre + "ausgeblendet: keine Mitteilung, zaehlt nicht als ungelesen", notifs(B.uid, gid) == [] and B.api("GET", "/api/chats/unread")[1]["unread"] == n0)
    ok(pre + "ausgeblendet: Push-Entscheidung nein, Erwaehnung ja", push_msg(B.uid, gid) is None and push_msg(B.uid, gid, "chat.mention") is not None)
    X.send(A, g, "@Bert zurueck", mentions=[ref_of(g, B)])
    ok(pre + "Erwaehnung: Chat kommt zurueck + chat.mention", gid in [x["id"] for x in B.api("GET", "/api/chats")[1]["chats"]]
       and notifs(B.uid, gid) == ["chat.mention"], notifs(B.uid, gid))
    B.api("PATCH", f"/api/chats/{gid}", {"hidden": True})
    B.api("POST", "/api/chats", {"kind": "group", "group": nm, "device": B.dev["id"]})
    ok(pre + "Oeffnen (POST /api/chats) holt ihn zurueck", B.chat(gid).get("hidden") is False)
    B.api("PATCH", f"/api/chats/{gid}", {"hidden": True})
    s, d = B.api("PATCH", f"/api/chats/{gid}", {"hidden": False})
    ok(pre + "Wieder anzeigen (hidden false)", s == 200 and (d.get("chat") or {}).get("hidden") is False)
    # --- C1 Erwaehnung nur Mitglieder -----------------------------------------------------------------------------------
    clear_notifs(A.uid, B.uid, Cc.uid, D.uid)
    dref = A.api("GET", "/api/chat/people?q=" + urllib.parse.quote(D.name))[1]["users"][0]["username"]
    s, _, _ = X.send(A, g, "@Dora @Anna @Cora", mentions=[dref, ref_of(g, A), ref_of(g, Cc), D.uid, "~aaaaaaaaaaaaaaaa", "x" * 400])
    ok(pre + f"Erwaehnung von Nicht-Mitglied D: ignoriert ({s})", s == 200 and notifs(D.uid, gid) == [])
    ok(pre + "Erwaehnung des Absenders: nichts", notifs(A.uid, gid) == [])
    ok(pre + "Cora (Mitglied): chat.mention", notifs(Cc.uid, gid) == ["chat.mention"], notifs(Cc.uid, gid))
    ok(pre + "Bert (nicht erwaehnt): chat.message", notifs(B.uid, gid) == ["chat.message"], notifs(B.uid, gid))
    # --- A2 anheften --------------------------------------------------------------------------------------------------------
    ids = [gid, cid]
    for i in range(4):
        u = [Cc, D][i % 2]
        r = A.api("GET", "/api/chat/people?q=" + urllib.parse.quote(u.name))[1]["users"][0]["username"]
        f = A.api("POST", "/api/chats", {"kind": "free", "members": [r] + ([rb] if i >= 2 else []), "device": A.dev["id"]})[1]["chat"]
        ids.append(f["id"])
    for i, x in enumerate(ids[:5]):
        s, d = A.api("PATCH", f"/api/chats/{x}", {"pinned": True})
        ok(pre + f"anheften {i + 1}/5 ({s})", s == 200 and (d.get("chat") or {}).get("pinned") is True and (d.get("chat") or {}).get("pinned_at"))
        time.sleep(0.02)
    s, d = A.api("PATCH", f"/api/chats/{ids[5]}", {"pinned": True})
    ok(pre + f"6. anheften -> 409 mit Meldung ({s})", s == 409 and "5" in str(d.get("error")), d)
    X.send(A, A.chat(ids[5]), "neueste")
    order = [x["id"] for x in A.api("GET", "/api/chats")[1]["chats"]]
    ok(pre + "Sortierung: angeheftete oben in Reihenfolge, dann neueste", order[:5] == ids[:5] and order[5] == ids[5], (order[:6], ids))
    A.api("PATCH", f"/api/chats/{ids[0]}", {"pinned": False})
    s, _ = A.api("PATCH", f"/api/chats/{ids[5]}", {"pinned": True})
    ok(pre + f"nach Loesen wieder Platz ({s})", s == 200)
    # --- C4 Medien ----------------------------------------------------------------------------------------------------------
    s, d = A.api("GET", f"/api/chats/{cid}/messages?has=link")
    ok(pre + f"?has=link -> 400 ({s})", s == 400)
    s, d = A.api("GET", f"/api/chats/{cid}/messages?has=file&device={A.dev['id']}")
    ok(pre + f"?has=file: nur Nachrichten mit Anhaengen (hier keine) ({s})", s == 200 and d.get("messages") == [], d)
    return cid


# --- Linkvorschau --------------------------------------------------------------------------------------------------------
def png():
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.new("RGB", (900, 600), (200, 40, 40)).save(b, "PNG")
    return b.getvalue()


class H(http.server.BaseHTTPRequestHandler):
    PORT = 0
    IMG = b""

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):   # Server bricht zu grosse Antworten ab (gewollt)
            pass

    def do_GET(self):
        p = self.path
        base = f"http://127.0.0.2:{H.PORT}"
        if p == "/page":
            self._send(200, "text/html; charset=utf-8", ('<html><head><title>Fallback</title><meta property="og:title" content="Saga &amp; Freunde">'
                                                          '<meta property="og:description" content="Eine Testseite <b>ohne</b> HTML">'
                                                          '<meta property="og:site_name" content="Testseite"><meta property="og:image" content="/img.png">'
                                                          '</head><body>x</body></html>').encode())
        elif p == "/img.png":
            self._send(200, "image/png", H.IMG)
        elif p == "/plain":
            self._send(200, "text/html", b"<html><head><title>  Nur   Titel </title></head></html>")
        elif p == "/json":
            self._send(200, "application/json", b'{"title": "nein"}')
        elif p == "/private":
            self._send(302, "text/plain", b"", {"Location": f"http://127.0.0.1:{H.PORT}/page"})
        elif p == "/redir":
            self._send(302, "text/plain", b"", {"Location": f"{base}/page"})
        elif p == "/huge":
            self._send(200, "text/html", b"<html><head>" + b" " * (3 << 20) + b"<title>spaet</title></head></html>")
        elif p == "/bigimg":
            self._send(200, "text/html", f'<html><head><title>Grosses Bild</title><meta property="og:image" content="{base}/big.png"></head></html>'.encode())
        elif p == "/big.png":
            self._send(200, "image/png", b"\x89PNG" + b"0" * (3 << 20))
        elif p == "/svg":
            self._send(200, "text/html", f'<html><head><title>SVG</title><meta property="og:image" content="{base}/x.svg"></head></html>'.encode())
        elif p == "/x.svg":
            self._send(200, "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        else:
            self._send(404, "text/plain", b"nein")


def unfurl_tests(A):
    pre = "[unfurl] "
    if "127.0.0.2" not in os.environ.get("SAGA_UNFURL_TEST_PUBLIC", ""):
        ok(pre + "SAGA_UNFURL_TEST_PUBLIC=127.0.0.2 in der Umgebung (Server wie Test)", False)
        return
    H.IMG = png()
    srv = http.server.ThreadingHTTPServer(("127.0.0.2", 0), H)
    H.PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.2:{H.PORT}"
    q = lambda u: A.api("GET", "/api/chat/unfurl?url=" + urllib.parse.quote(u, safe=""))   # noqa: E731
    try:
        s, d = q(base + "/page")
        im = d.get("image") or {}
        ok(pre + f"Titel/Beschreibung/Seite aus og: ({s})", s == 200 and d.get("title") == "Saga & Freunde" and d.get("desc") == "Eine Testseite <b>ohne</b> HTML"
           and d.get("site") == "Testseite" and d.get("url") == base + "/page", d)
        ok(pre + "Bild: JPEG neu kodiert, hoechstens 400 px, klein", im.get("mime") == "image/jpeg" and max(im.get("w", 999), im.get("h", 999)) <= 400
           and 0 < len(im.get("b64", "")) < 60000, {k: v for k, v in im.items() if k != "b64"})
        s, d = q(base + "/plain")
        ok(pre + "nur <title> (Leerraum bereinigt), Seite = Host", s == 200 and d.get("title") == "Nur Titel" and d.get("site") == "127.0.0.2" and "image" not in d, d)
        s, d = q(base + "/json")
        ok(pre + f"nur text/html: JSON -> leer ({s})", s == 200 and d == {}, d)
        s, d = q(base + "/redir")
        ok(pre + "Weiterleitung (oeffentlich -> oeffentlich) folgt", s == 200 and d.get("title") == "Saga & Freunde", d)
        s, d = q(base + "/private")
        ok(pre + f"Weiterleitung auf private Adresse (127.0.0.1) gesperrt -> leer ({s})", s == 200 and d == {}, d)
        t0 = time.time()
        s, d = q(base + "/huge")
        ok(pre + f"Groessenlimit: Kopf hinter 512 KB -> leer, zuegig ({s}, {time.time() - t0:.1f} s)", s == 200 and d == {} and time.time() - t0 < 9, d)
        s, d = q(base + "/bigimg")
        ok(pre + "zu grosses Bild: Vorschau ohne Bild", s == 200 and d.get("title") == "Grosses Bild" and "image" not in d, d)
        s, d = q(base + "/svg")
        ok(pre + "SVG-Bild nie", s == 200 and "image" not in d, d)
        s, d = q(f"http://127.0.0.1:{H.PORT}/page")
        ok(pre + f"direkt private Adresse (127.0.0.1, fremder Port) -> 400 ({s})", s == 400)
        for u in ("http://127.0.0.1/page", "http://10.1.2.3/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/", "http://localhost/"):
            s, d = q(u)
            ok(pre + f"privates Ziel {u} -> leer ({s})", s == 200 and d == {}, d)
        for u in ("file:///etc/passwd", "ftp://example.org/", "http://user:pw@127.0.0.2/", "http://example.org:8080/", "javascript:alert(1)", "", "x" * 2100):
            s, _ = q(u)
            ok(pre + f"ungueltig {u[:40]!r} -> 400 ({s})", s == 400)
        ok(pre + "/api/version: unfurl1 (Schalter an)", "unfurl1" in CT.req("GET", "/api/version")[1].get("features", []))
        auth.save_setting("chat_unfurl", False)
        time.sleep(WAIT)
        s, d = q(base + "/page")
        ok(pre + f"Admin-Schalter aus -> 403 ({s}), Feature fehlt", s == 403 and "unfurl1" not in CT.req("GET", "/api/version")[1].get("features", []))
        auth.save_setting("chat_unfurl", True)
        time.sleep(WAIT)
    finally:
        srv.shutdown()
        srv.server_close()


def run():
    s, d = CT.req("GET", "/api/version")
    f = set(d.get("features", []))
    ok("/api/version: mute2 chatpin1 markunread1 chathide1 chatmention1 chatfmt1 chatmedia1",
       {"mute2", "chatpin1", "markunread1", "chathide1", "chatmention1", "chatfmt1", "chatmedia1"} <= f, sorted(f))
    from saga import federation as F
    ok("Foederation: Faehigkeit chatmention1 (Admin-Schalter chat)", "chatmention1" in F.CAPS2 and F.CAP_OPT.get("chatmention1") == "chat")
    ok("decide(): Reihenfolge stumm > none > Erwaehnung > ausgeblendet > mentions",
       CP.decide({"muted_until": -1, "notify": "all", "hide_at": None}, True, True) is None
       and CP.decide({"muted_until": 0, "notify": "none", "hide_at": None}, True, False) is None
       and CP.decide({"muted_until": 0, "notify": "all", "hide_at": 1.0}, True, False) == "chat.mention"
       and CP.decide({"muted_until": 0, "notify": "all", "hide_at": 1.0}, False, True) is None
       and CP.decide({"muted_until": 0, "notify": "mentions", "hide_at": None}, False, True) == "chat.message"
       and CP.decide({"muted_until": 0, "notify": "mentions", "hide_at": None}, False, False) is None
       and CP.decide({"muted_until": time.time() - 1, "notify": "all", "hide_at": None}, False, False) == "chat.message")
    # v1
    U.setv3(False)
    ps = [U.P(f"cl-{n.lower()}1", f"{n} Eins", certs=False) for n in ("Anna", "Bert", "Cora", "Dora")]
    for p in ps:
        p.ev = CT.Stream(p.k)
    series("v1", *ps, False)
    # Stufe 3
    U.setv3(True)
    qs = [U.P(f"cl-{n.lower()}3", f"{n} Drei") for n in ("Anna", "Bert", "Cora", "Dora")]
    for p in qs:
        p.ev = CT.Stream(p.k)
    series("Stufe 3", *qs, True)
    unfurl_tests(qs[0])
    hits = CT.plaintext_hits(SECRET, "@Bert schau", "Saga & Freunde")
    ok("kein Klartext von Nachrichten/Vorschauen in der Datenbank", not hits, hits)


def main():
    cleanup()
    keep = {k: U.stored(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "chat_unfurl", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("search_username", True), ("chat_unfurl", True)):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            if v is None:
                U.unset(k)
            else:
                auth.save_setting(k, json.loads(v))
        cleanup()
    print(f"\nCHAT-ALLTAG {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    socket.setdefaulttimeout(60)
    main()
