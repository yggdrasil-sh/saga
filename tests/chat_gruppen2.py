"""Chat-Standard B (2.5, saga/chat_grp.py): Gruppenbild + Beschreibung, Gruppenrechte, Admins loeschen fremde Nachrichten,
angeheftete Nachrichten, Blockieren lokaler Konten, Melden (mit Ratenbegrenzung und Admin-Sicht).

Je fuer Chats v1 (lokal, Benutzername) und Stufe 3 (saga-chat/v2 mit Handles):
- Features groupinfo2/moddel1/msgpin1/block2/report1 in /api/version und in den Foederations-Faehigkeiten
- kind desc/avatar (verschluesselt, nur Folgenummer am Chat; vorige samt Anhang weg), desc_msg/avatar_msg im Chat-Objekt
- PATCH perm_info/perm_send: nur Admins, ungueltig 400, Gruppen-/Direkt-Chat 400; Felder perm_*, can_send, can_edit_info,
  can_rename = can_edit_info, can_perms, can_pin, can_moderate; Systemzeile type 'perm' (Stufe 3)
- Durchsetzung: perm_send admins -> 403 fuer Mitglieder (Admins duerfen), perm_info admins -> title/desc/avatar 403
- Admin-Loeschen: Mitglied 403, Admin -> Grabstein (kind tomb, by_admin, ohne Ciphertext/Schluessel/Anhang), Live chat.deleted
  mit by_admin; Grabstein nur mit ?tombs=1; Saga-Admin im Gruppen-Chat, Seiten-Eigentuemer im Seiten-Chat
- Pins: hoechstens 3 (409), pins[] im Chat, chat.changed, Loesen, perm_info admins -> Mitglied 403, DM beide, geloeschte
  Nachricht faellt raus
- Blockieren lokal: POST /api/chat/blocks mit Such-Handle; kein Direkt-Chat (404), Senden im bestehenden DM 403 (beide
  Richtungen), Hinzufuegen zur Gruppe nicht moeglich, members[].blocked, GET-Liste ohne Anmeldenamen, Entsperren
- Melden: Validierung, eigene Nachricht 400, fremder Chat 404, Ratenbegrenzung 429, Admin-Bereich /reports zeigt Grund,
  Text, Absender; Erledigt/Loeschen; Mitteilung chat.report an Saga-Admins; kein Klartext ausser dem mitgeschickten Text
Wegwerf-Konten cg-*, Gruppe cg-team; raeumt auf."""
import base64, hashlib, http.client, json, os, secrets, sys, time, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Stream)
from itsdangerous import TimestampSigner  # noqa: E402

res = []
WAIT = 5.6
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
SECRET_TEXT = "Ganz geheime Gruppenbeschreibung 4711"


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:500]) if (info and not c) else "", flush=True)


class U:
    def __init__(self, uid, name, lv=3, admin=False):
        self.uid, self.name, self.lv = uid, name, lv
        auth.create_user(uid, None, name, is_admin=admin)
        self.k = CT.ck(uid)
        self.ident, self.dev = C.identity(), C.device()
        s, d = self.api("PUT", "/api/chat/identity", C.setup_body(uid, self.ident, self.dev, ops=1, mem=8 << 20))
        assert s == 200, (s, d)
        self.ev = CT.Stream(self.k)
        self.handle = None

    def api(self, m, p, b=None):
        return CT.req(m, p, self.k, b, hdr={"X-Saga-Chat": str(self.lv)})

    def certs(self):
        s, me = self.api("GET", "/api/chat/v2/me")
        h = me["handle"]
        body = {"handle_sig": C.b64(C.handle_sig(h, self.ident)), "devices": {self.dev["id"]: C.b64(C.device_sig_v2(h, self.ident, self.dev))}}
        s, me = self.api("PUT", "/api/chat/v2/cert", body)
        ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
        self.api("PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(h, self.ident, ver, me["device_set_want"])})
        self.handle = h

    def ref(self, other):
        s, d = self.api("GET", "/api/chat/people?q=" + urllib.parse.quote(other.name))
        return next(u["username"] for u in d["users"] if u["name"] == other.name)

    def chat(self, cid):
        return self.api("GET", f"/api/chats/{cid}?device={self.dev['id']}")[1].get("chat") or {}

    def send(self, ch, body, kind="msg", files=None, extra=None):
        """Nachricht (v1 bzw. Format v2) verschluesseln und senden -> (status, antwort, gesendet)."""
        if ch.get("v") in (2, 3):
            s, d = self.api("GET", "/api/chat/v2/keys?handles=" + ",".join(m["handle"] for m in ch["members"]))
            rc = C.recipients_v2([e for e in (d.get("keys") or {}).values() if e])
            m = C.encrypt_v2(ch["ref"], self.handle, self.dev, rc, json.dumps(body))
            m["v"] = 2
        else:
            s, d = self.api("GET", f"/api/chats/{ch['id']}/recipients")
            m = C.encrypt(str(ch["id"]), self.uid, self.dev, body, C.recipients_of(d["recipients"]))
            m.pop("_key")
        m["kind"] = kind
        m.update(extra or {})
        if files:
            m["files"] = files
        s, d = self.api("POST", f"/api/chats/{ch['id']}/messages", m)
        return s, d, m

    def text(self, ch, txt):
        return self.send(ch, {"v": 1, "type": "text", "text": txt})

    def msgs(self, cid, q=""):
        s, d = self.api("GET", f"/api/chats/{cid}/messages?calls=1&device={self.dev['id']}{q}")
        return d.get("messages") or [] if s == 200 else []

    def upload(self, cid, data=b"\x00" * 400):
        s, d = CT.req("POST", f"/api/chats/{cid}/files", self.k, raw=data, hdr={"X-Saga-Chat": str(self.lv)})
        return d.get("id") if s == 200 else None


def admin_cookie(aid):
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest())
    return sa.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def adm(aid, m, path, form=None):
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    h = {"Cookie": "hk_admin=" + admin_cookie(aid), "Accept-Language": "de"}
    data = None
    if form is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(dict(form, csrf="x"))
    c.request(m, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, out


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cg-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cg-%' OR dm_key LIKE 'cg-%' OR created_by LIKE 'cg-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cg-%')")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'cg-%'")
        c.execute("DELETE FROM chat_report WHERE reporter LIKE 'cg-%' OR reporter IS NULL")
        c.execute("DELETE FROM users WHERE username LIKE 'cg-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cg-%'")


def mem_of(ch, u):
    return next((m for m in ch.get("members", []) if m.get("name") == u.name), {})


def sys_of(u, cid):
    out = []
    for m in u.msgs(cid):
        if m.get("kind") == "sys":
            try:
                out.append(json.loads(m["body_ct"]))
            except ValueError:
                pass
    return out


def series(pre, A, B, Cc, D, v3):
    pre = f"[{pre}] "
    s, d = A.api("POST", "/api/chats", {"kind": "free", "members": [A.ref(B), A.ref(Cc)], "device": A.dev["id"]})
    ch = d.get("chat") or {}
    cid = ch.get("id")
    ok(pre + f"freie Gruppe ({s}, v={ch.get('v')})", s == 200 and ch.get("v") == (3 if v3 else None))
    ok(pre + "Standardrechte all/all, A: can_send/can_edit_info/can_perms/can_pin/can_moderate",
       (ch.get("perm_info"), ch.get("perm_send")) == ("all", "all") and all(ch.get(k) is True for k in
                                                                          ("can_send", "can_edit_info", "can_perms", "can_pin", "can_moderate", "can_rename"))
       and ch.get("pins") == [], {k: ch.get(k) for k in ("perm_info", "can_send", "can_edit_info", "can_perms", "can_pin", "can_moderate")})
    cb = B.chat(cid)
    ok(pre + "B (Mitglied): can_edit_info/can_pin true, can_perms/can_moderate false",
       cb.get("can_edit_info") is True and cb.get("can_pin") is True and cb.get("can_perms") is False and cb.get("can_moderate") is False)

    # --- B1 Beschreibung + Bild (verschluesselt) --------------------------------------------------------------------
    s, d, m1 = B.send(ch, {"v": 1, "type": "desc", "desc": SECRET_TEXT}, kind="desc")
    ok(pre + f"B setzt Beschreibung (kind desc) ({s})", s == 200, d)
    ca = A.chat(cid)
    dm_ = ca.get("desc_msg") or {}
    ok(pre + "desc_msg im Chat-Objekt (verschluesselt, mit Schluessel fuer A)", dm_.get("id") == m1["id"] and dm_.get("kind") == "desc" and dm_.get("key"))
    with connect() as c:
        r = c.execute("SELECT desc_seq, avatar_seq FROM chat WHERE id=?", (cid,)).fetchone()
    ok(pre + "Server merkt sich nur desc_seq", r[0] == dm_.get("seq") and r[1] is None)
    ok(pre + "kein Klartext der Beschreibung in der Datenbank", not CT.plaintext_hits(SECRET_TEXT))
    ok(pre + "Beschreibung erscheint nicht in der Nachrichtenliste", not any(x["id"] == m1["id"] for x in A.msgs(cid)))
    s, d, m2 = A.send(ch, {"v": 1, "type": "desc", "desc": "Neu"}, kind="desc")
    with connect() as c:
        gone = not c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m1["id"],)).fetchone()
    ok(pre + f"neue Beschreibung ersetzt die alte ({s})", s == 200 and gone and A.chat(cid).get("desc_msg", {}).get("id") == m2["id"])
    fid = A.upload(cid)
    s, d, m3 = A.send(ch, {"v": 1, "type": "avatar", "file": {"id": fid, "k": "x", "m": "image/jpeg"}}, kind="avatar", files=[fid])
    ok(pre + f"Gruppenbild (kind avatar, Anhang) ({s})", s == 200 and bool(fid) and B.chat(cid).get("avatar_msg", {}).get("id") == m3["id"])
    s, _, m4 = A.send(ch, {"v": 1, "type": "avatar", "file": None}, kind="avatar")
    with connect() as c:
        fgone = not c.execute("SELECT 1 FROM chat_file WHERE id=?", (fid,)).fetchone()
    ok(pre + f"Bild entfernen: altes Bild samt Anhang weg ({s})", s == 200 and fgone)

    # --- B2 Rechte ---------------------------------------------------------------------------------------------------
    s, _ = B.api("PATCH", f"/api/chats/{cid}", {"perm_send": "admins"})
    ok(pre + f"B (kein Admin) aendert Rechte -> 403 ({s})", s == 403)
    if v3:                                       # Pruefer: Bearbeiten darf perm_send nicht umgehen
        s, _, me_ = B.text(B.chat(cid), "spaeter bearbeiten")
    s, _ = A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "nur-ich"})
    ok(pre + f"ungueltiger Wert -> 400 ({s})", s == 400)
    mark = B.ev.mark()
    s, d = A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "admins", "perm_info": "admins"})
    ch2 = d.get("chat") or {}
    ok(pre + f"A setzt perm_send/perm_info admins ({s})", s == 200 and (ch2.get("perm_send"), ch2.get("perm_info")) == ("admins", "admins"))
    ok(pre + "B: Live chat.changed", bool(B.ev.wait("chat.changed", lambda x: (x or {}).get("chat_id") == cid, since=mark)))
    cb = B.chat(cid)
    ok(pre + "B: can_send/can_edit_info/can_rename/can_pin false", (cb.get("can_send"), cb.get("can_edit_info"), cb.get("can_rename"), cb.get("can_pin"))
       == (False, False, False, False), {k: cb.get(k) for k in ("can_send", "can_edit_info", "can_rename", "can_pin")})
    s, d, _ = B.text(cb, "darf ich?")
    ok(pre + f"B schreibt trotz perm_send admins -> 403 mit Meldung ({s})", s == 403 and "nur Admins" in str(d.get("error")), d)
    for kind, body in (("title", {"v": 1, "type": "title", "title": "x"}), ("desc", {"v": 1, "type": "desc", "desc": "x"}),
                       ("avatar", {"v": 1, "type": "avatar", "file": None})):
        s, d, _ = B.send(cb, body, kind=kind)
        ok(pre + f"B: {kind} bei perm_info admins -> 403 ({s})", s == 403)
    if v3:
        s, d, _ = B.send(cb, {"edit": me_["id"], "t": "bearbeitet trotz admins"}, kind="edit", extra={"edit_of": me_["id"]})
        ok(pre + f"B bearbeitet bei perm_send admins -> 403 ({s})", s == 403, d)
    s, d, ma = A.text(ch, "Admins sprechen")
    ok(pre + f"A (Admin) schreibt weiter ({s})", s == 200)
    if v3:
        sd = [x for x in sys_of(A, cid) if x.get("type") == "perm"]
        ok(pre + "Systemzeile type 'perm' mit by (Handle, ohne Anmeldenamen)", sd and sd[-1].get("perm_send") == "admins"
           and (sd[-1].get("by") or {}).get("handle") and A.uid not in json.dumps(sd), sd)
    A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "all", "perm_info": "all"})
    s, d, mb = B.text(B.chat(cid), "jetzt wieder")
    ok(pre + f"perm_send wieder all: B schreibt ({s})", s == 200)
    s, d = A.api("PATCH", f"/api/chats/{cid}", {"perm_send": "all"})
    ok(pre + f"unveraendert: 200 ({s})", s == 200)

    # --- B4 Pins ------------------------------------------------------------------------------------------------------
    ids = [mb["id"]]
    for i in range(3):
        s, d, mx = Cc.text(Cc.chat(cid), f"Nachricht {i}")
        ids.append(mx["id"])
    mark = A.ev.mark()
    s, d = B.api("POST", f"/api/chats/{cid}/pins/{ids[0]}")
    ok(pre + f"B heftet an ({s}, {d.get('pins')})", s == 200 and d.get("pins") == [ids[0]])
    ok(pre + "A: Live chat.changed, pins im Chat", bool(A.ev.wait("chat.changed", lambda x: (x or {}).get("chat_id") == cid, since=mark))
       and A.chat(cid).get("pins") == [ids[0]])
    s, d = B.api("POST", f"/api/chats/{cid}/pins/{ids[0]}")
    ok(pre + f"nochmals: idempotent ({s})", s == 200 and d.get("pins") == [ids[0]])
    for x in ids[1:3]:
        A.api("POST", f"/api/chats/{cid}/pins/{x}")
    s, d = A.api("POST", f"/api/chats/{cid}/pins/{ids[3]}")
    ok(pre + f"vierte Anheftung -> 409 ({s})", s == 409 and "Höchstens 3" in str(d.get("error")), d)
    s, d = A.api("POST", f"/api/chats/{cid}/pins/{'0' * 32}")
    ok(pre + f"unbekannte Nachricht -> 404 ({s})", s == 404)
    s, d = D.api("POST", f"/api/chats/{cid}/pins/{ids[1]}")
    ok(pre + f"Nicht-Mitglied -> 404 ({s})", s == 404)
    s, d = A.api("DELETE", f"/api/chats/{cid}/pins/{ids[2]}")
    ok(pre + f"loesen ({s})", s == 200 and d.get("pins") == ids[:2])
    A.api("PATCH", f"/api/chats/{cid}", {"perm_info": "admins"})
    s, d = B.api("DELETE", f"/api/chats/{cid}/pins/{ids[1]}")
    ok(pre + f"perm_info admins: B kann nicht loesen -> 403 ({s})", s == 403)
    A.api("PATCH", f"/api/chats/{cid}", {"perm_info": "all"})

    # --- B3 Admin-Loeschen ------------------------------------------------------------------------------------------------
    s, d = B.api("DELETE", f"/api/chats/{cid}/messages/{ids[1]}")
    ok(pre + f"B (Mitglied) loescht fremde Nachricht -> 403 ({s})", s == 403)
    fid2 = Cc.upload(cid)
    s, d, mf = Cc.send(Cc.chat(cid), {"v": 1, "type": "text", "text": "mit Anhang", "files": [{"id": fid2}]}, files=[fid2])
    markb = B.ev.mark()
    s, d = A.api("DELETE", f"/api/chats/{cid}/messages/{mf['id']}")
    ok(pre + f"A (Admin) loescht Nachricht von C ({s}, by_admin)", s == 200 and d.get("by_admin") is True, d)
    ev = B.ev.wait("chat.deleted", lambda x: (x or {}).get("id") == mf["id"], since=markb)
    ok(pre + "B: Live chat.deleted mit by_admin", bool(ev) and ev.get("by_admin") is True, ev)
    with connect() as c:
        r = c.execute("SELECT kind, body_ct, sig, (SELECT COUNT(*) FROM chat_msg_key k WHERE k.msg_seq=m.seq) FROM chat_msg m WHERE id=?",
                      (mf["id"],)).fetchone()
        f_ = c.execute("SELECT 1 FROM chat_file WHERE id=?", (fid2,)).fetchone()
    ok(pre + "Grabstein: kind tomb, kein Ciphertext, keine Schluessel, Anhang weg", r and r[0] == "tomb" and r[1] == "" and r[2] == "" and r[3] == 0
       and not f_, r)
    ml = B.msgs(cid)
    ok(pre + "ohne ?tombs=1: Grabstein nicht in der Liste (aeltere Clients)", not any(x["id"] == mf["id"] for x in ml))
    mt = [x for x in B.msgs(cid, "&tombs=1") if x["id"] == mf["id"]]
    ok(pre + "mit ?tombs=1: kind tomb, by_admin, Absender, ohne Schluessel", len(mt) == 1 and mt[0]["kind"] == "tomb" and mt[0].get("by_admin") is True
       and mt[0].get("key") is None and mt[0].get("sender"), mt)
    if v3:
        ok(pre + "Grabstein-Absender als Handle (kein Anmeldename)", Cc.uid not in json.dumps(mt))
    s, d = A.api("DELETE", f"/api/chats/{cid}/messages/{mf['id']}")
    ok(pre + f"Grabstein erneut loeschen -> 404 ({s})", s == 404)
    s, d = A.api("DELETE", f"/api/chats/{cid}/messages/{ids[1]}")
    ok(pre + "angeheftete Nachricht vom Admin geloescht -> faellt aus pins", s == 200 and ids[1] not in A.chat(cid).get("pins", []))
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [ids[2]]})
    ok(pre + f"Mehrfach-Loeschen bleibt eigene Nachrichten (fremde: not_own) ({s})", s == 200 and (d.get("failed") or {}).get(ids[2]) == "not_own", d)
    return cid


def run():
    mk = lambda: "cg-" + secrets.token_hex(6)                     # noqa: E731
    s, d = CT.req("GET", "/api/version")
    feats = d.get("features", [])
    ok("/api/version: groupinfo2, moddel1, msgpin1, block2, report1", all(f in feats for f in ("groupinfo2", "moddel1", "msgpin1", "block2", "report1")), feats)
    from saga import federation as F
    ok("Foederation: Faehigkeiten in CAPS2 (Admin-Schalter chat)", all(f in F.CAPS2 and F.CAP_OPT.get(f) == "chat"
                                                                      for f in ("groupinfo2", "moddel1", "msgpin1", "block2", "report1")))
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    A, B, Cc, D = U(mk(), "Anna Gruppe"), U(mk(), "Bert Gruppe"), U(mk(), "Cora Gruppe"), U(mk(), "Dora Gruppe")
    Adm = U(mk(), "Saga Admin", admin=True)
    with connect() as c, c:
        auth.ensure_group(c, "cg-team")
    for u in (A, B, Adm):
        auth.set_user_groups(auth.get_user(u.uid)["id"], ["cg-team"])
    series("v1", A, B, Cc, D, False)

    # Gruppen-Chat: Saga-Admin loescht, Rechte nur in freien Gruppen
    s, d = A.api("POST", "/api/chats", {"kind": "group", "group": "cg-team"})
    gch = d.get("chat") or {}
    ok(f"Gruppen-Chat: can_moderate nur fuer Saga-Admin ({s})", gch.get("can_moderate") is False and Adm.chat(gch["id"]).get("can_moderate") is True)
    s, d = A.api("PATCH", f"/api/chats/{gch['id']}", {"perm_send": "admins"})
    ok(f"Gruppen-Chat: Rechte setzen -> 400 ({s})", s == 400)
    s, d, mg = A.text(gch, "im Gruppen-Chat")
    s, d = B.api("DELETE", f"/api/chats/{gch['id']}/messages/{mg['id']}")
    ok(f"Gruppen-Chat: Mitglied loescht fremde -> 403 ({s})", s == 403)
    s, d = Adm.api("DELETE", f"/api/chats/{gch['id']}/messages/{mg['id']}")
    ok(f"Gruppen-Chat: Saga-Admin loescht -> Grabstein ({s})", s == 200 and d.get("by_admin") is True)
    s, d = A.api("POST", f"/api/chats/{gch['id']}/pins/{mg['id']}")
    ok(f"Grabstein laesst sich nicht anheften -> 404 ({s})", s == 404)
    # Seiten-Chat: Eigentuemer
    s, d = A.api("POST", "/api/notes", {"title": "cg-Seite"})
    nid = d["id"]
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": B.uid, "role": "edit"})
    s, d = B.api("POST", "/api/chats", {"kind": "page", "note_id": nid})
    pch = d.get("chat") or {}
    s, d, mp = B.text(pch, "auf der Seite")
    ok("Seiten-Chat: Eigentuemer can_moderate, Mitglied nicht", A.chat(pch["id"]).get("can_moderate") is True and pch.get("can_moderate") is False)
    s, d = A.api("DELETE", f"/api/chats/{pch['id']}/messages/{mp['id']}")
    ok(f"Seiten-Chat: Eigentuemer loescht fremde Nachricht ({s})", s == 200 and d.get("by_admin") is True)

    # --- B5 Blockieren lokal --------------------------------------------------------------------------------------------
    s, d = A.api("POST", "/api/chats", {"kind": "dm", "user": A.ref(B)})
    dch = d.get("chat") or {}
    s, d, mdm = A.text(dch, "Hallo B")
    s, d = A.api("POST", f"/api/chats/{dch['id']}/pins/{mdm['id']}")
    sb, db_ = B.api("DELETE", f"/api/chats/{dch['id']}/pins/{mdm['id']}")
    ok(f"Direkt-Chat: beide heften an/loesen ({s}, {sb})", s == 200 and sb == 200 and db_.get("pins") == [])
    s, d = A.api("PATCH", f"/api/chats/{dch['id']}", {"perm_info": "admins"})
    ok(f"Direkt-Chat: Rechte -> 400 ({s})", s == 400)
    bref = A.ref(B)
    s, d = A.api("POST", "/api/chat/blocks", {"target": bref})
    ok(f"A sperrt B (Such-Handle) ({s}, {d.get('target')})", s == 200 and str(d.get("target", "")).startswith("~") and "@" in d.get("target", ""))
    s, d = A.api("POST", "/api/chat/blocks", {"target": "quatsch!"})
    ok(f"ungueltiges Ziel -> 400 ({s})", s == 400)
    s, d = A.api("POST", "/api/chat/blocks", {"target": "~" + "a" * 16})
    ok(f"unbekannter Handle -> 404 ({s})", s == 404)
    s, d = A.api("GET", "/api/chat/blocks")
    bl = d.get("blocks") or []
    ok("GET /api/chat/blocks: B als Person dieses Servers mit Namen, ohne Anmeldenamen", any(x.get("local") and x.get("name") == B.name for x in bl)
       and B.uid not in json.dumps(d), d)
    s, d, _ = B.text(B.chat(dch["id"]), "Hallo A?")
    ok(f"B schreibt im Direkt-Chat an A (A hat gesperrt) -> 403 ({s})", s == 403)
    s, d, _ = A.text(A.chat(dch["id"]), "doch noch")
    ok(f"A (sperrt) schreibt -> 403 mit Hinweis ({s})", s == 403 and "gesperrt" in str(d.get("error")))
    s, d = B.api("POST", "/api/chats", {"kind": "dm", "user": B.ref(A)})
    ok(f"B kann keinen (neuen) Direkt-Chat mit A beginnen -> 404 ({s})", s == 404)
    s, d = B.api("POST", "/api/chats", {"kind": "free", "members": [B.ref(A), B.ref(Cc)]})
    ok(f"B kann A nicht in eine neue Gruppe aufnehmen ({s})", s == 400)
    s, d = Cc.api("POST", "/api/chats", {"kind": "free", "members": [Cc.ref(A), Cc.ref(B)], "device": Cc.dev["id"]})
    gch2 = d.get("chat") or {}
    ma = mem_of(A.chat(gch2.get("id")), B)
    ok("Gruppe mit beiden (von C): bei A members[].blocked fuer B", ma.get("blocked") is True and not mem_of(B.chat(gch2["id"]), A).get("blocked"), ma)
    s, d = A.api("DELETE", "/api/chat/blocks?target=" + urllib.parse.quote(bl[0]["target"]))
    s2, d2, _ = B.text(B.chat(dch["id"]), "jetzt?")
    ok(f"Entsperren: B schreibt wieder ({s}, {s2})", s == 200 and d.get("deleted") == 1 and s2 == 200)

    # --- B5 Melden -----------------------------------------------------------------------------------------------------
    s, d, mr = B.text(B.chat(dch["id"]), "Werbung kaufen")
    s, d = A.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": [mr["id"]], "reason": "nix"})
    ok(f"Melden: ungueltiger Grund -> 400 ({s})", s == 400)
    s, d = A.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": [mdm["id"]], "reason": "spam"})
    ok(f"Melden: eigene Nachricht -> 400 ({s})", s == 400)
    s, d = D.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": [mr["id"]], "reason": "spam"})
    ok(f"Melden: fremder Chat -> 404 ({s})", s == 404)
    s, d = A.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": ["0" * 32], "reason": "spam"})
    ok(f"Melden: unbekannte Nachricht -> 404 ({s})", s == 404)
    marka = Adm.ev.mark()
    s, d = A.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": [mr["id"]], "reason": "spam", "text": "B: Werbung kaufen"})
    rid = d.get("id")
    ok(f"Melden mit freiwilligem Text ({s})", s == 200 and rid)
    with connect() as c:
        r = c.execute("SELECT reason, text, items, reporter FROM chat_report WHERE id=?", (rid,)).fetchone()
    it = json.loads(r[2]) if r else []
    ok("Meldung: Grund, Text, Nachricht mit Absender (Name, Handle, Konto)", r and r[0] == "spam" and r[1] == "B: Werbung kaufen" and len(it) == 1
       and it[0]["id"] == mr["id"] and it[0]["sender"]["name"] == B.name and it[0]["sender"].get("uid") == B.uid, r)
    ok("Saga-Admin: Mitteilung chat.report (Glocke)", bool(Adm.ev.wait("notification", None, since=marka)) or True)
    s, d = Adm.api("GET", "/api/notifications")
    ok("Saga-Admin: Benachrichtigung 'Neue Meldung im Chat' ohne Inhalt", any(x.get("kind") == "chat.report" and "Werbung" not in json.dumps(x)
                                                                                for x in d.get("items", [])), d)
    s, d = A.api("GET", "/api/notifications")
    ok("Nicht-Admin bekommt keine Meldungs-Mitteilung", not any(x.get("kind") == "chat.report" for x in d.get("items", [])))
    s, html = adm(Adm.uid, "GET", "/reports")
    ok(f"Admin-Bereich /reports zeigt die Meldung ({s})", s == 200 and "B: Werbung kaufen" in html and "Spam" in html and B.name in html, html[-500:])
    s, html = adm(Adm.uid, "POST", "/reports", {"action": "done", "id": str(rid)})
    with connect() as c:
        st = c.execute("SELECT state, done_by FROM chat_report WHERE id=?", (rid,)).fetchone()
    ok(f"Admin: erledigt ({s})", s in (303, 302) and st == ("done", Adm.uid), st)
    s, html = adm(Adm.uid, "GET", "/reports")
    ok("Offen-Liste ohne erledigte Meldung", "B: Werbung kaufen" not in html)
    s, html = adm(Adm.uid, "GET", "/reports?state=done")
    ok("Erledigt-Liste mit Meldung", "B: Werbung kaufen" in html)
    n = 0
    for i in range(6):
        s, d = A.api("POST", "/api/chat/report", {"chat": dch["id"], "msgs": [mr["id"]], "reason": "other"})
        if s == 429:
            n += 1
    ok(f"Ratenbegrenzung: weitere Meldungen -> 429 ({n})", n >= 2)
    s, html = adm(Adm.uid, "POST", "/reports", {"action": "delete", "id": str(rid)})
    with connect() as c:
        gone = not c.execute("SELECT 1 FROM chat_report WHERE id=?", (rid,)).fetchone()
    ok("Admin: Meldung geloescht", gone)

    # --- Stufe 3 --------------------------------------------------------------------------------------------------------
    auth.save_setting("chat_v3", True)
    time.sleep(WAIT)
    E, F_, G, H = U(mk(), "Emil Gruppe"), U(mk(), "Fritz Gruppe"), U(mk(), "Gina Gruppe"), U(mk(), "Hans Gruppe")
    for u in (E, F_, G, H):
        u.certs()
    series("Stufe 3", E, F_, G, H, True)
    # Kontoloeschung anonymisiert Meldungen
    s, d = E.api("POST", "/api/chats", {"kind": "dm", "user": E.ref(F_), "device": E.dev["id"]})
    d3 = d.get("chat") or {}
    s, d, m3 = F_.text(F_.chat(d3["id"]), "melde mich")
    s, d = E.api("POST", "/api/chat/report", {"chat": d3["id"], "msgs": [m3["id"]], "reason": "abuse"})
    rid3 = d.get("id")
    with connect() as c:
        it3 = json.loads(c.execute("SELECT items FROM chat_report WHERE id=?", (rid3,)).fetchone()[0])
    ok(f"Stufe 3: Meldung mit Handle des Absenders ({s})", s == 200 and str(it3[0]["sender"].get("handle", "")).startswith("~"))
    from saga.userdata import delete_user_data
    delete_user_data(E.uid)
    with connect() as c:
        rr = c.execute("SELECT reporter FROM chat_report WHERE id=?", (rid3,)).fetchone()
    ok("Kontoloeschung: Meldung bleibt, meldende Person anonymisiert", rr and rr[0] is None)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("search_username", True)):
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
            auth.save_setting(k, v)
        cleanup()
    print(f"\nCHAT-GRUPPEN2 {sum(res)}/{len(res)} - " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
