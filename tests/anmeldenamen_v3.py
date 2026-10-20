"""Chat-Stufe 3 (2.5, Konzept "Krypto v3 ueber Handles", A6): in lokalen Chats der Stufe 3 erfaehrt niemand den
Anmeldenamen eines anderen Kontos - weder in HTTP-Antworten noch in Live-Ereignissen (/api/events, mitgeschnitten).

Konten mit Markern 'an-<16 hex>' (Anzeigenamen ohne Marker). Durchlaufen: Direkt-Chat, freie Gruppe (hinzufuegen, entfernen),
Saga-Gruppe, Seiten-Chat, Nachrichten (saga-chat/v2 mit chat_ref und Handles), Antwort, Gelesen (v2-Bestaetigung), Tippen,
Online-Status per Handle, Verlauf fuer alle loeschen, Chat fuer alle loeschen, Schluesselwechsel (chat.keys.changed lokal), Schluessel nur fuer
Chat-Partner, knows() ohne Stufe-3-Chats, Kompat-Sicht v: 2 fuer Clients der Stufe 2, Hinweis fuer aeltere.
Geprueft wird JEDE Antwort und JEDES Ereignis je Konto: kein Marker eines anderen Kontos - auch nicht in Base64-dekodierten
Feldern; Anrufe 1:1 und Gruppe (saga-call/v2 lokal). Noch nicht (folgt, siehe CHANGELOG): KI-Antwort, Reaktion, Bearbeiten,
Sprachnachricht, Altbestand nach Umstellung.
Wegwerf-Konten an-*, Gruppe an3-*; schaltet die Einstellung chat_v3 nur fuer den Test ein und raeumt auf."""
import base64, binascii, json, os, re, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Stream)
import push as PT  # noqa: E402  (tests/push.py: Fake-Empfaenger, Web-Push-Schluessel)

PUSH_PORT = 18297

res = []
SEEN = []                     # (konto, quelle, text) - alle Antworten und Ereignisse
WAIT = 5.6                    # Einstellungen sind 5 s zwischengespeichert
B64 = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)


class U:
    """Ein Konto mit Identitaet, einem Geraet, Zertifikaten v2 und Live-Mitschnitt. lv = Chat-Stufe (X-Saga-Chat)."""

    def __init__(self, uid, name, lv=3):
        self.uid, self.name, self.lv = uid, name, lv
        auth.create_user(uid, None, name)
        self.k = CT.ck(uid)
        self.ident, self.dev = C.identity(), C.device()
        s, d = self.api("PUT", "/api/chat/identity", C.setup_body(uid, self.ident, self.dev, ops=1, mem=8 << 20))
        assert s == 200, (s, d)
        self.me = self.certs()
        self.handle = self.me["handle"]
        self.ev = CT.Stream(self.k)
        self.ua = PT.UA()                       # Web Push (Ende-zu-Ende) - Nutzlast mit Namen, wird mitgeschnitten
        s, _ = self.api("POST", "/api/push/subscriptions", {"type": "webpush", "endpoint": f"http://127.0.0.1:{PUSH_PORT}/wp/{uid}",
                                                            "keys": self.ua.keys(), "name": "Test"})
        assert s == 201, s

    def api(self, m, p, b=None, lv=None):
        s, d = CT.req(m, p, self.k, b, hdr={"X-Saga-Chat": str(self.lv if lv is None else lv)})
        SEEN.append((self.uid, f"{m} {p}", d if isinstance(d, str) else json.dumps(d, ensure_ascii=False) if not isinstance(d, bytes)
                     else d.decode("utf-8", "replace")))
        return s, d

    def certs(self, devs=None):
        s, me = self.api("GET", "/api/chat/v2/me")
        h = me["handle"]
        devs = devs or [self.dev]
        body = {"handle_sig": C.b64(C.handle_sig(h, self.ident)), "devices": {d["id"]: C.b64(C.device_sig_v2(h, self.ident, d)) for d in devs}}
        s, me = self.api("PUT", "/api/chat/v2/cert", body)
        ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
        s, me = self.api("PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(h, self.ident, ver, me["device_set_want"])})
        assert s == 200 and not me["need"], (s, me)
        return me

    def ref(self, other):
        """Kennung aus der Personensuche (pseudonymer Such-Handle)"""
        s, d = self.api("GET", "/api/chat/people?q=" + urllib.parse.quote(other.name))
        return next(u["username"] for u in d["users"] if u["name"] == other.name)

    def keys(self, handles, fresh=False):
        s, d = self.api("GET", "/api/chat/v2/keys?handles=" + ",".join(handles) + ("&fresh=1" if fresh else ""))
        return d.get("keys") or {}

    def chat(self, cid, lv=None):
        return self.api("GET", f"/api/chats/{cid}?device={self.dev['id']}", lv=lv)[1].get("chat") or {}

    def send(self, ch, text, reply_to=None):
        ents = self.keys([m["handle"] for m in ch["members"]])
        rc = C.recipients_v2([e for e in ents.values() if e])
        m = C.encrypt_v2(ch["ref"], self.handle, self.dev, rc, json.dumps({"v": 1, "type": "text", "text": text}), reply_to=reply_to)
        m["v"] = 2
        s, d = self.api("POST", f"/api/chats/{ch['id']}/messages", m)
        return s, d, m

    def msgs(self, cid, lv=None):
        s, d = self.api("GET", f"/api/chats/{cid}/messages?calls=1&device={self.dev['id']}", lv=lv)
        return d if s == 200 else {}

    def open(self, ch, m):
        pk, sk = (self.dev["enc_pk"], self.dev["enc_sk"]) if m["key_for"] == "device" else (self.ident["backup_pk"], self.ident["backup_sk"])
        return json.loads(C.decrypt_v2(ch["ref"], m, m["key"], pk, sk))

    def wait(self, typ, pred=None, since=0, timeout=6.0):
        return self.ev.wait(typ, pred, since, timeout)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'an-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'an3-%' OR dm_key LIKE 'an-%' OR created_by LIKE 'an-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'an-%')")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'an-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'an-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'an3-%'")


def decoded(text):
    """Base64-Felder einer Antwort dekodiert (Systemzeilen, Kopfzeilen von Signaturen) - fuer den Marker-Scan."""
    out = []
    for tok in B64.findall(text or ""):
        try:
            out.append(base64.b64decode(tok + "=" * (-len(tok) % 4), validate=False).decode("utf-8", "ignore"))
        except (binascii.Error, ValueError):
            pass
    return "\n".join(out)


def pushes(u):
    """Entschluesselte Web-Push-Nutzlasten an u (Fake-Empfaenger)."""
    with PT.LOCK:
        got = [b for port, path, _, b in PT.GOT if port == PUSH_PORT and path == f"/wp/{u.uid}"]
    return [json.dumps(u.ua.decrypt(b), ensure_ascii=False) for b in got]


def hits_of(u, others):
    rx = re.compile("|".join(re.escape(o.uid) for o in others))
    items = [(src, txt) for who, src, txt in SEEN if who == u.uid] + [("push", x) for x in pushes(u)]
    items += [("event " + t, json.dumps(d, ensure_ascii=False)) for t, d in list(u.ev.ev)]
    return items, [(src, (rx.search(txt) or rx.search(decoded(txt))).group(0), txt[:300]) for src, txt in items
                   if rx.search(txt) or rx.search(decoded(txt))]


def scan(people):
    """Je Konto: kein Marker (Anmeldename) eines ANDEREN Kontos in Antworten oder Ereignissen."""
    for u in people:
        items, hits = hits_of(u, [x for x in people if x is not u])
        ok(f"Marker-Scan {u.name}: {len(items)} Antworten/Ereignisse ohne Anmeldenamen anderer Konten", not hits, hits[:3])


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "search_username")}
    keep["allowed_hosts"] = prev.get("allowed_hosts")
    keep["push"] = prev.get("push")
    PT.serve(PUSH_PORT)
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("chat_v3", True), ("search_username", True),
                 ("allowed_hosts", ", ".join(x for x in (prev.get("allowed_hosts"), f"127.0.0.1:{PUSH_PORT}") if x)),
                 ("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True})):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nANMELDENAMEN v3 {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    mk = lambda: "an-" + secrets.token_hex(8)                     # noqa: E731  Marker = Anmeldename
    A, B, Cc, D = U(mk(), "Anna Dreier"), U(mk(), "Bert Dreier"), U(mk(), "Cora Dreier"), U(mk(), "Dora Dreier")
    E = U(mk(), "Emil Zweier", lv=2)                               # App 2.3/2.4 (X-Saga-Chat: 2): Kompat-Sicht
    people = [A, B, Cc, D, E]
    with connect() as c, c:
        auth.ensure_group(c, "an3-team")
    for u in (A, B, Cc):
        auth.set_user_groups(auth.get_user(u.uid)["id"], ["an3-team"])
    s, me = A.api("GET", "/api/chat/me")
    ok("GET /api/chat/me: Faehigkeit chat3 (Einstellung an)", s == 200 and "chat3" in me.get("features", []), me.get("features"))

    # --- Direkt-Chat ------------------------------------------------------------------------------------------------------
    rb = A.ref(B)
    s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": rb, "device": A.dev["id"]})
    dm = d.get("chat") or {}
    other = next((m for m in dm.get("members", []) if not m.get("me")), {})
    ok("Direkt-Chat: Stufe 3 (v 3, chat_ref, nicht foederiert)", s == 200 and dm.get("v") == 3 and re.fullmatch(r"[0-9a-f]{32}@.+", dm.get("ref") or "")
       and dm.get("fed") is False and dm.get("home_self") is True, dm)
    ok("Direkt-Chat: Mitglied und Gegenueber als Handle (username = handle), Name ohne Anmeldename",
       other.get("username") == other.get("handle") == B.handle and other.get("name") == "Bert Dreier" and other.get("ref") == rb
       and (dm.get("peer") or {}).get("username") == B.handle and dm["peer"].get("host"), (other, dm.get("peer")))
    ok("Direkt-Chat: eigener Eintrag mit eigenem Namen und my_handle", any(m.get("me") and m["username"] == A.uid for m in dm["members"])
       and dm.get("my_handle") == A.handle)
    s, keys = A.api("GET", "/api/chat/keys?users=" + rb)
    ok("Schluessel v1 (/api/chat/keys) gibt es fuer Partner nur aus Chats v1 - Stufe 3 zaehlt nicht", s == 200 and keys == {"users": {}}, keys)
    k2 = A.keys([B.handle, D.handle])
    ok("Schluessel v2: Chat-Partner ja, Konto ohne gemeinsamen Chat nein", (k2.get(B.handle) or {}).get("handle") == B.handle
       and k2.get(D.handle) is None, k2)

    # Nachricht + Antwort (Format v2), Live-Ereignis mit Handle
    mkA, mkB = A.ev.mark(), B.ev.mark()
    s, r1, m1 = A.send(dm, "Hallo Bert, Stufe drei")
    ok("A sendet (v2-Format ueber chat_ref, eigener Server als Heim)", s == 200 and r1.get("seq") and r1.get("fseq") == 1, r1)
    e = B.wait("chat.message", lambda x: x.get("chat_id") == dm["id"], mkB)
    ok("B: chat.message mit Absender als Handle", bool(e) and e.get("sender") == A.handle, e)
    e = A.wait("chat.message", lambda x: x.get("chat_id") == dm["id"], mkA)
    ok("A: eigenes chat.message mit eigenem Benutzernamen", bool(e) and e.get("sender") == A.uid, e)
    bd = B.msgs(dm["id"])
    bm = next((x for x in bd.get("messages", []) if x["id"] == m1["id"]), {})
    ok("B: liest die Nachricht (Absender = Handle, keine v1-Geraetedaten)", bm.get("sender", {}).get("username") == A.handle
       and bm.get("sender_handle") == A.handle and B.open(dm, bm)["text"] == "Hallo Bert, Stufe drei" and bd.get("devices") == {} and bd.get("idents") == {}, bd)
    s, r2, m2 = B.send(B.chat(dm["id"]), "Hallo Anna", reply_to=m1["id"])
    ad = A.msgs(dm["id"])
    am = next((x for x in ad.get("messages", []) if x["id"] == m2["id"]), {})
    ok("A: Antwort von B mit Handle, eigene Nachricht mit eigenem Namen", s == 200 and am.get("sender", {}).get("username") == B.handle
       and am.get("reply_to") == m1["id"] and any(x["id"] == m1["id"] and x["sender"]["username"] == A.uid for x in ad["messages"]), ad)

    # Tippen
    mkB = B.ev.mark()
    A.api("POST", f"/api/chats/{dm['id']}/typing")
    e = B.wait("chat.typing", lambda x: x.get("chat_id") == dm["id"], mkB)
    ok("B: chat.typing nennt A als Handle", bool(e) and e["user"]["username"] == A.handle and e["user"]["name"] == "Anna Dreier", e)

    # Gelesen (Bestaetigung v2, versiegelt fuer den Backup-Schluessel von A)
    mkA = A.ev.mark()
    ka = A.keys([A.handle])[A.handle]
    sealed = C.seal_receipt(dm["ref"], B.handle, B.dev, {"id": m1["id"], "n": r1["seq"], "ts": int(time.time() * 1000)}, C.unb64(ka["backup_pub"]), v2=True)
    s, d = B.api("POST", f"/api/chats/{dm['id']}/read", {"seq": r2["seq"], "receipt": {"keys": {"backup:" + A.handle: sealed}}})
    ok("B: gelesen + Bestaetigung im v2-Format", s == 200 and d.get("receipt") == "ok", d)
    e = A.wait("chat.receipt", lambda x: x.get("chat_id") == dm["id"], mkA)
    ok("A: chat.receipt von B als Handle", bool(e) and e["receipts"][0]["from"] == B.handle and e["receipts"][0].get("handle") == B.handle, e)
    s, d = A.api("GET", f"/api/chats/{dm['id']}/receipts")
    dev, body, sig = C.open_receipt(d["receipts"][0]["sealed"], A.ident["backup_pk"], A.ident["backup_sk"])
    ok("A: GET /receipts (from = Handle), Signatur v2 mit Handle von B gueltig", s == 200 and d["receipts"][0]["from"] == B.handle
       and C.verify_receipt(dm["ref"], B.handle, dev, body, sig, B.dev["sign_pk"], v2=True), d)

    # Online-Status per Handle (Antwort unter der angefragten Kennung); per Anmeldename kein Orakel
    s, d = A.api("POST", "/api/chat/presence", {"users": [B.handle]})
    ok("Online-Status: B per Handle abonniert, Antwort unter dem Handle", s == 200 and (d.get("users") or {}).get(B.handle, {}).get("online") is True, d)
    s, d = A.api("POST", "/api/chat/presence", {"users": [B.uid]}, lv=3)
    ok("Online-Status: per Anmeldename (nur Stufe-3-Chat gemeinsam) keine Auskunft", s == 200 and d.get("users") == {}, d)
    mkA = A.ev.mark()
    B.ev.close()
    e = A.wait("chat.presence", lambda x: x.get("username") == B.handle, mkA, timeout=40)
    ok("chat.presence nennt B als Handle (B offline)", bool(e) and e.get("online") is False, A.ev.types(mkA))
    B.ev = CT.Stream(B.k)
    time.sleep(0.5)

    # Kompat-Sicht und Hinweis
    s, d = B.api("GET", f"/api/chats/{dm['id']}", lv=2)
    ok("Client der Stufe 2 (App 2.3/2.4): derselbe Chat als v 2 mit ref und Handles", s == 200 and d["chat"]["v"] == 2 and d["chat"]["ref"] == dm["ref"]
       and not d["chat"].get("fed_hint"), d)
    s, d = B.api("GET", f"/api/chats/{dm['id']}", lv=0)
    s2, d2 = B.api("GET", f"/api/chats/{dm['id']}/messages?device={B.dev['id']}", lv=0)
    ok("Client ohne X-Saga-Chat (App 2.1/2.2): Hinweis statt Nachrichten", s == 200 and "2.5" in (d["chat"].get("fed_hint") or "")
       and s2 == 200 and d2["messages"] == [] and "2.5" in d2.get("fed_hint", ""), (d, d2))

    # Schluesselwechsel: neues Geraet von B -> chat.keys.changed an A (lokal, ohne Foederation)
    mkA = A.ev.mark()
    d2v = C.device()
    s, _ = B.api("POST", "/api/chat/devices", C.device_api(B.uid, B.ident, d2v))
    B.certs([B.dev, d2v])
    e = A.wait("chat.keys.changed", lambda x: x.get("handle") == B.handle, mkA)
    ok("A: chat.keys.changed {handle} beim neuen Geraet von B", s == 200 and bool(e), A.ev.types(mkA))
    kb = A.keys([B.handle], fresh=True).get(B.handle) or {}
    ok("A: frische Schluessel von B mit beiden Geraeten", len([x for x in kb.get("devices", []) if not x.get("revoked")]) == 2, kb)

    # --- Freie Gruppe: anlegen, schreiben, hinzufuegen, entfernen ----------------------------------------------------------
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [rb, A.ref(Cc)], "device": A.dev["id"]})
    fg = d.get("chat") or {}
    ok("Freie Gruppe: Stufe 3, Mitglieder als Handles", s == 200 and fg.get("v") == 3
       and sorted(m["username"] for m in fg["members"] if not m["me"]) == sorted([B.handle, Cc.handle]), fg)
    s, _, _ = A.send(fg, "Hallo Gruppe")
    ok("Freie Gruppe: Nachricht an alle", s == 200)
    s, d = A.api("POST", f"/api/chats/{fg['id']}/members", {"user": A.ref(D)})
    ok("Freie Gruppe: D per Such-Handle hinzugefuegt", s == 200 and any(m["username"] == D.handle for m in d["chat"]["members"]), d)
    s, d = A.api("DELETE", f"/api/chats/{fg['id']}/members/" + urllib.parse.quote(Cc.handle))
    fg2 = A.chat(fg["id"])
    ok("Freie Gruppe: C per Handle aus members[] entfernt", s == 200 and not any(m["username"] == Cc.handle for m in fg2.get("members", [])), (d, fg2))
    s, d = A.api("POST", f"/api/chats/{fg['id']}/members", {"handle": Cc.handle})
    ok("Freie Gruppe: C per {handle} (Konto dieses Servers) wieder hinzugefuegt", s == 200 and any(m["username"] == Cc.handle for m in d["chat"]["members"]), d)
    s, _, _ = D.send(D.chat(fg["id"]), "Hallo von Dora")
    for u in (A, B, D):
        u.msgs(fg["id"])
        u.api("GET", "/api/chats?device=" + u.dev["id"])

    # --- Saga-Gruppe und Seiten-Chat ---------------------------------------------------------------------------------------
    s, d = A.api("POST", "/api/chats", {"kind": "group", "group": "an3-team"})
    gr = d.get("chat") or {}
    ok("Saga-Gruppe: Stufe 3", s == 200 and gr.get("v") == 3 and len(gr.get("members", [])) == 3, gr)
    A.send(gr, "Gruppe drei")
    Cc.msgs(gr["id"])
    nid = A.api("POST", "/api/notes", {"title": "Seite drei"})[1]["id"]
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": rb, "role": "edit"})
    s, d = A.api("POST", "/api/chats", {"kind": "page", "note_id": nid})
    pg = d.get("chat") or {}
    ok("Seiten-Chat: Stufe 3", s == 200 and pg.get("v") == 3, pg)
    A.send(pg, "Seite drei")
    B.msgs(pg["id"])
    mkB = B.ev.mark()
    s, d = A.api("DELETE", f"/api/chats/{pg['id']}?scope=all")
    e = B.wait("chat.cleared", lambda x: x.get("chat_id") == pg["id"], mkB)
    ok("Seiten-Chat: Verlauf fuer alle geloescht, chat.cleared nennt A als Handle", s == 200 and bool(e) and e["by"]["username"] == A.handle, (d, e))

    # --- knows(): Stufe-3-Chats verraten keinen Anmeldenamen als Eingabe ---------------------------------------------------
    auth.save_setting("search_username", False)
    time.sleep(WAIT)
    s1, b1 = A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": D.uid, "role": "view"})
    s2, b2 = A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": "an-niemand0000000", "role": "view"})
    ok("knows(): Partner nur aus Stufe-3-Chats per Anmeldename wie ein unbekanntes Konto", s1 == s2 == 400 and b1 == b2, (s1, b1, s2, b2))
    auth.save_setting("search_username", True)

    # --- Kompat-Sicht: App 2.4 (Stufe 2) in einem Stufe-3-Chat ------------------------------------------------------------
    s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": A.ref(E)})
    de = d.get("chat") or {}
    ec = E.chat(de["id"])
    ok("Stufe 2 sieht den Chat als v 2 (fed false, home_self), Gegenueber als Handle", ec.get("v") == 2 and ec.get("ref") == de["ref"]
       and ec.get("home_self") is True and (ec.get("peer") or {}).get("handle") == A.handle and ec["peer"]["username"] == A.handle, ec)
    s, _, m3 = E.send(ec, "Gruss aus App 2.4")
    am = next((x for x in A.msgs(de["id"]).get("messages", []) if x["id"] == m3["id"]), {})
    ok("Stufe 2 sendet im v2-Format, A liest", s == 200 and A.open(A.chat(de["id"]), am)["text"] == "Gruss aus App 2.4", am)
    s, rc = E.api("GET", f"/api/chats/{de['id']}/recipients")
    ok("Empfaenger (recipients_v2) ohne Anmeldenamen: andere als Handle", s == 200 and rc.get("v") == 2
       and {x["username"] for x in rc["recipients_v2"] if not x["me"]} == {A.handle}, rc)

    # --- Anrufe (saga-call/v2 lokal): 1:1 und Gruppe ---------------------------------------------------------------------
    cid1 = C.hexid()
    sig = {d["id"]: C.seal_signal_v2(cid1, A.handle, A.dev, B.handle, d["id"], d["enc_pk"], json.dumps({"t": "offer", "sdp": "x"}))
           for d in (B.dev, d2v)}
    mkB = B.ev.mark()
    s, d = A.api("POST", "/api/calls", {"id": cid1, "chat_id": dm["id"], "media": "audio", "device": A.dev["id"], "signals": sig, "caps": ["multi"]})
    ok("Anruf 1:1 in Stufe 3: v 2, Gegenueber als Handle", s == 200 and d["call"].get("v") == 2 and d["call"]["peer"]["username"] == B.handle
       and d["call"]["peer"]["handle"] == B.handle, d)
    e = B.wait("call.ring", lambda x: x.get("call_id") == cid1, mkB)
    ok("B: call.ring nennt A als Handle", bool(e) and e["from"]["username"] == A.handle and e["from"]["handle"] == A.handle and e.get("v") == 2, e)
    s, d = B.api("GET", f"/api/calls/{cid1}?device={B.dev['id']}")
    sg = (d.get("signals") or [{}])[0]
    o = C.open_signal_v2(sg.get("sealed"), B.dev["enc_pk"], B.dev["enc_sk"]) if sg.get("sealed") else None
    ok("B: Anruf als v 2 mit A als Handle; Angebot nach saga-call/v2 mit Handles gueltig", s == 200 and d["call"]["v"] == 2
       and d["call"]["peer"]["username"] == A.handle and o is not None
       and C.verify_signal_v2(cid1, A.handle, A.dev["id"], B.handle, B.dev["id"], o[0], o[1], A.dev["sign_pk"]), (d, o))
    mkA = A.ev.mark()
    s, _ = B.api("POST", f"/api/calls/{cid1}/decline", {"reason": "declined"})
    e = A.wait("call.state", lambda x: x.get("call_id") == cid1 and x.get("state") == "ended", mkA)
    ok("A: call.state ended, by = B als Handle", s == 200 and bool(e) and e.get("by") == B.handle, e)
    log = next((x for x in A.msgs(dm["id"]).get("messages", []) if x["kind"] == "call"), {})
    lb = json.loads(log.get("body_ct") or "{}")
    ok("Anruf-Eintrag im Chat nur mit Handles", lb.get("from") == A.handle and lb.get("to") == B.handle, lb)
    cid2 = C.hexid()
    B.api("PUT", "/api/calls/caps", {"device": B.dev["id"], "caps": ["multi"]})      # Geraet kann Gruppenanrufe (2.4)
    mkB = B.ev.mark()
    s, d = A.api("POST", "/api/calls", {"id": cid2, "chat_id": fg["id"], "media": "audio", "device": A.dev["id"], "caps": ["multi"]})
    ok("Gruppenanruf in Stufe 3: v 2", s == 200 and d["call"].get("v") == 2 and d["call"]["kind"] == "group", d)
    e = B.wait("call.invite", lambda x: x.get("call_id") == cid2, mkB)
    ok("B: call.invite nennt A als Handle", bool(e) and e["from"]["username"] == A.handle, e)
    e = B.wait("call.roster", lambda x: x.get("call_id") == cid2, mkB)
    ok("B: call.roster mit A als Handle (who, handle, host)", bool(e) and e["legs"][0]["who"] == A.handle and e["legs"][0]["handle"] == A.handle
       and e["legs"][0].get("host"), e)
    s, d = B.api("GET", f"/api/calls/{cid2}?device={B.dev['id']}")
    ok("B: Gruppenanruf - Teilnehmer und Starter als Handle", s == 200 and d["call"]["from"]["username"] == A.handle
       and all(x["who"] == A.handle for x in d["call"]["legs"]), d)
    s, d = A.api("GET", f"/api/calls/{cid2}?device={A.dev['id']}")
    ok("A: eigener Endpunkt mit eigenem Namen (me)", s == 200 and d["call"]["legs"][0]["who"] == A.uid and d["call"]["legs"][0]["me"], d)
    A.api("POST", f"/api/calls/{cid2}/end", {"device": A.dev["id"]})
    for u in (A, B, D):
        u.api("GET", "/api/calls/active")

    # --- Direkt-Chat fuer alle loeschen -----------------------------------------------------------------------------------
    mkB = B.ev.mark()
    s, d = A.api("DELETE", f"/api/chats/{dm['id']}?scope=all")
    e = B.wait("chat.removed", lambda x: x.get("chat_id") == dm["id"], mkB)
    ok("Direkt-Chat fuer alle geloescht, chat.removed nennt A als Handle", s == 200 and bool(e) and e["by"]["username"] == A.handle, (d, e))

    # --- alle Listen einmal ansehen, dann der Scan --------------------------------------------------------------------------
    for u in people:
        u.api("GET", "/api/chats?device=" + u.dev["id"])
        u.api("GET", "/api/notifications")
    time.sleep(1.0)
    end = time.time() + 20
    while time.time() < end and not all(pushes(u) for u in (A, B, D)):
        time.sleep(0.5)
    pa = pushes(B)
    ok(f"Push: Nutzlasten mitgeschnitten (B: {len(pa)}), mit Namen statt Anmeldenamen", pa and any("Anna Dreier" in x for x in pa), pa[:3])
    scan(people)

    # --- Gegenprobe: der Scan findet den Marker, wo er erwartet ist (Chat v1, Einstellung aus) -----------------------------
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    F, G = U(mk(), "Fritz Einser"), U(mk(), "Gina Einser")
    s, d = F.api("POST", "/api/chats", {"kind": "dm", "user": F.ref(G)})
    ok("Einstellung aus: neuer Chat bleibt v1 (Kennung v1 = Anmeldename, dokumentierte Ausnahme)", s == 200 and d["chat"].get("v") is None
       and any(m["username"] == G.uid for m in d["chat"]["members"]), d)
    ok("Gegenprobe: der Scan findet den Anmeldenamen im Chat v1", bool(hits_of(F, [G])[1]))


if __name__ == "__main__":
    main()
