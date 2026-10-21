"""Umstellauftrag Chat v1 -> Stufe 3 (2.5, Konzept A4.3-A4.5, saga.chat.convert_v3) - Server-Seite, PyNaCl als Client.

Geprueft: ohne Handle-Zertifikat aller Mitglieder keine Umstellung; solange ein aktives Geraet nicht chat3 meldet (aeltere
App, uebernimmt "ueberprueft" nicht) keine Umstellung - Geraete, die laenger als 30 Tage nicht da waren, halten sie nicht
auf; nach Umstellung v=3 mit chat_ref, v3_seq = letzte Nachricht v1, Systemzeile {"type":"crypto","v":3} ohne Namen;
beliebig oft ausfuehrbar; Altbestand: alte Nachrichten bleiben v1 lesbar (Absender mit Benutzernamen, Geraetedaten v1),
neue im Format v2 mit Handles; wer nach der Umstellung beitritt, sieht keinen Anmeldenamen (auch nicht im Altbestand);
knows() nur fuer Mitglieder von vor der Umstellung; Senden v1 in einen umgestellten Chat -> 409; Stichtag (chat_v3_days):
Umstellung trotz aelterer Geraete mit Hinweis "Sicherheitsnummer bitte neu pruefen" fuer Apps der Stufe 2; Handles in
Chats v1 fuer Clients der Stufe 3 (Vertrauens-Uebernahme); Kontoloeschung raeumt Reaktionen und Bearbeitungs-Verweise auf.
Wegwerf-Konten cu-*; schaltet chat_v3 nur fuer den Test ein."""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Person, Stream)
from saga import chat as CH, fed_chat  # noqa: E402,F401  (fed_chat setzt chat.FED - wie im Server)

req, res = CT.req, CT.res
assert fed_chat.on                                                    # (Import setzt chat.FED)


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)
WAIT = 5.6


class P(CT.Person):
    """Konto mit Identitaet v1 und Zertifikaten v2; lv = Chat-Stufe der App (X-Saga-Chat)."""

    def __init__(self, uid, name, lv=3, certs=True):
        auth.create_user(uid, None, name)
        super().__init__(uid)
        self.lv, self.name = lv, name
        if certs:
            self.certs()

    def api(self, m, p, b=None, lv=None):
        return req(m, p, self.k, b, hdr={"X-Saga-Chat": str(self.lv if lv is None else lv)})

    def certs(self):
        s, me = self.api("GET", "/api/chat/v2/me")
        self.handle = me["handle"]
        body = {"handle_sig": C.b64(C.handle_sig(self.handle, self.ident)), "devices": {self.dev["id"]: C.b64(C.device_sig_v2(self.handle, self.ident, self.dev))}}
        s, me = self.api("PUT", "/api/chat/v2/cert", body)
        ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
        s, me = self.api("PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(self.handle, self.ident, ver, me["device_set_want"])})
        assert s == 200 and not me["need"], (s, me)

    def seen(self, lv=None):
        """Eine Anfrage mit Geraet: die App meldet ihre Chat-Stufe (Stufe 3 -> Faehigkeit chat3 am Geraet)."""
        return self.api("GET", "/api/chats?device=" + self.dev["id"], lv=lv)[1]

    def chat(self, cid, lv=None):
        return self.api("GET", f"/api/chats/{cid}?device={self.dev['id']}", lv=lv)[1].get("chat") or {}

    def send_v2(self, ch, text):
        s, d = self.api("GET", "/api/chat/v2/keys?handles=" + ",".join(m["handle"] for m in ch["members"]))
        rc = C.recipients_v2([e for e in d["keys"].values() if e])
        m = C.encrypt_v2(ch["ref"], self.handle, self.dev, rc, json.dumps({"v": 1, "type": "text", "text": text}))
        m["v"] = 2
        s, r = self.api("POST", f"/api/chats/{ch['id']}/messages", m)
        return s, r, m

    def msgs(self, cid, lv=None):
        return self.api("GET", f"/api/chats/{cid}/messages?calls=1&device={self.dev['id']}", lv=lv)[1]


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cu-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'cu-%' OR created_by LIKE 'cu-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cu-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'cu-%'")


def v_of(cid):
    with connect() as c:
        r = c.execute("SELECT v, ref, v3_seq, v3_forced, last_seq FROM chat WHERE id=?", (cid,)).fetchone()
    return dict(zip(("v", "ref", "v3_seq", "forced", "last_seq"), r)) if r else {}


def setv3(on, days=None):
    auth.save_setting("chat_v3", on)
    if days is not None:
        auth.save_setting("chat_v3_days", days)
    time.sleep(WAIT)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "chat_v3_days", "chat_v3_since", "search_username")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("chat_v3", False), ("chat_v3_days", 90),
                 ("chat_v3_since", time.time())):
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
    print(f"\nUMSTELLUNG {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    A, B = P("cu-anna", "Anna Um"), P("cu-bert", "Bert Um")
    Cn = P("cu-cora", "Cora Um", certs=False)                        # (noch) ohne Handle-Zertifikat
    D = P("cu-dora", "Dora Um")
    # --- Altbestand v1 (Stufe 3 noch aus) ---------------------------------------------------------------------------
    dm = A.api("POST", "/api/chats", {"kind": "dm", "user": "cu-bert"})[1]["chat"]
    s, r0, m0 = A.send(dm["id"], "Alt und v1")
    fg = A.api("POST", "/api/chats", {"kind": "free", "members": ["cu-bert", "cu-cora"]})[1]["chat"]
    A.send(fg["id"], "Alt in der Gruppe")
    ok("Ausgang: beide Chats v1", v_of(dm["id"])["v"] == 1 and v_of(fg["id"])["v"] == 1)
    d = A.chat(dm["id"], lv=3)
    ok("Chat v1, Stufe 3: Mitglieder mit Handle (fuer die Vertrauens-Uebernahme)", d.get("v") is None
       and {m["username"]: m.get("handle") for m in d["members"]} == {"cu-anna": A.handle, "cu-bert": B.handle}, d.get("members"))
    ok("Chat v1, Stufe 2: ohne Handle (unveraendert)", all("handle" not in m for m in A.chat(dm["id"], lv=2)["members"]))

    setv3(True)
    with connect() as c, c:                                           # Faehigkeiten vergessen (frische Lage)
        c.execute("UPDATE chat_device SET caps='[]' WHERE uid LIKE 'cu-%'")
    ok("Auftrag: kein Geraet mit chat3 -> nichts umgestellt", CH.convert_v3(limit=100000) >= 0 and v_of(dm["id"])["v"] == 1)
    A.seen(lv=3)
    ok("Auftrag: nur A mit Saga 2.5 -> Direkt-Chat bleibt v1", CH.convert_v3(limit=100000) >= 0 and v_of(dm["id"])["v"] == 1)
    B.seen(lv=3)
    old = C.device()                                                   # altes, seit 40 Tagen nicht gesehenes Geraet von B
    B.api("POST", "/api/chat/devices", C.device_api(B.uid, B.ident, old))
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=?, created=? WHERE id=?", (time.time() - 40 * 86400, time.time() - 50 * 86400, old["id"]))
    n = CH.convert_v3(limit=100000)
    v = v_of(dm["id"])
    ok(f"Auftrag: alle aktiven Geraete chat3 (altes inaktives zaehlt nicht) -> Direkt-Chat umgestellt ({n})", v["v"] == 3
       and re.fullmatch(r"[0-9a-f]{32}@.+", v["ref"] or "") and v["v3_seq"] == r0["seq"] and not v["forced"], v)
    ok("Freie Gruppe mit Cora ohne Zertifikat bleibt v1", v_of(fg["id"])["v"] == 1)
    ok("Auftrag beliebig oft ausfuehrbar (zweiter Lauf: nichts mehr)", CH.convert_v3(limit=100000) == 0 and v_of(dm["id"]) == v)

    bm = B.msgs(dm["id"])
    sysl = [x for x in bm["messages"] if x["kind"] == "sys"]
    ok("Systemzeile crypto (Stufe 3) ohne Namen", len(sysl) == 1 and json.loads(sysl[0]["body_ct"]).get("type") == "crypto"
       and json.loads(sysl[0]["body_ct"]).get("level") == 3 and "cu-" not in sysl[0]["body_ct"], sysl)
    o0 = next(x for x in bm["messages"] if x["id"] == m0["id"])
    ok("Altbestand: alte Nachricht bleibt v1 (Absender mit Benutzernamen, Geraetedaten v1, lesbar)", o0["sender"]["username"] == "cu-anna"
       and not o0.get("sender_handle") and CT.verify_msg(dm["id"], o0, bm) and B.open(dm["id"], o0)["text"] == "Alt und v1", o0)
    bc = B.chat(dm["id"])
    ok("Chat jetzt Stufe 3 mit Umstellpunkt (v3.seq)", bc.get("v") == 3 and bc.get("v3", {}).get("seq") == r0["seq"]
       and bc["peer"]["username"] == A.handle, bc)
    s, r1, m1 = A.send_v2(A.chat(dm["id"]), "Neu und v2")
    nb = next((x for x in B.msgs(dm["id"])["messages"] if x["id"] == m1["id"]), {})
    ok("Neue Nachricht im Format v2 mit Handle", s == 200 and nb.get("sender_handle") == A.handle and nb["sender"]["username"] == A.handle, nb)
    s, d, mx = A.send(dm["id"], "v1 nach der Umstellung", recipients={})
    with connect() as c:
        stored = c.execute("SELECT 1 FROM chat_msg WHERE id=?", (mx["id"],)).fetchone()
    ok(f"Senden v1 in den umgestellten Chat abgewiesen ({s})", s in (400, 409) and not stored, d)

    # --- Gruppe: Cora bekommt ein Zertifikat, Gruppe wird umgestellt; Dora tritt danach bei ------------------------------
    Cn.certs()
    Cn.seen(lv=3)
    CH.convert_v3(limit=100000)
    ok("Gruppe umgestellt, sobald Cora bereit ist", v_of(fg["id"])["v"] == 3)
    s, d = A.api("POST", f"/api/chats/{fg['id']}/members", {"user": A.api("GET", "/api/chat/people?q=Dora%20Um")[1]["users"][0]["username"]})
    ok("Dora tritt nach der Umstellung bei", s == 200 and any(m["username"] == D.handle for m in d["chat"]["members"]), d)
    s, rd, md = A.send_v2(A.chat(fg["id"]), "Hallo Dora")
    s, rb, mb = B.send_v2(B.chat(fg["id"]), "Bert in der Gruppe")
    seen = json.dumps([D.chat(fg["id"]), D.msgs(fg["id"]), D.seen()])
    ok("Dora sieht keinen Anmeldenamen (auch keinen Altbestand)", not re.search(r"cu-(anna|bert|cora)", seen), re.findall(r"cu-\w+", seen)[:5])
    auth.save_setting("search_username", False)
    time.sleep(WAIT)
    nid = A.api("POST", "/api/notes", {"title": "Umstellung"})[1]["id"]
    s1, _ = A.api("PUT", f"/api/notes/{nid}/shares", {"type": "user", "target": "cu-bert", "role": "view"})
    s2, _ = D.api("POST", "/api/notes", {"title": "Dora"})
    nd = _["id"] if isinstance(_, dict) else 0
    s3, b3 = D.api("PUT", f"/api/notes/{nd}/shares", {"type": "user", "target": "cu-anna", "role": "view"})
    ok("knows(): Bert (Altbestand) per Anmeldename ja, Dora (nach der Umstellung beigetreten) nein", s1 == 200 and s3 == 400, (s1, s3, b3))
    auth.save_setting("search_username", True)

    # --- Stichtag: aelteres Geraet, Frist abgelaufen -> Umstellung mit Hinweis -----------------------------------------
    setv3(False)
    E = P("cu-emil", "Emil Um", lv=2)
    de = A.api("POST", "/api/chats", {"kind": "dm", "user": "cu-emil"})[1]["chat"]
    A.send(de["id"], "Hallo Emil")
    setv3(True)
    E.seen(lv=2)
    CH.convert_v3(limit=100000)
    ok("Frist laeuft: Emil hat nur eine App 2.4 -> bleibt v1", v_of(de["id"])["v"] == 1)
    setv3(True, days=0)
    CH.convert_v3(limit=100000)
    v = v_of(de["id"])
    ok("Stichtag erreicht -> umgestellt (forced)", v["v"] == 3 and v["forced"] == 1, v)
    ec = E.chat(de["id"], lv=2)
    ok("App 2.4: Hinweis 'Sicherheitsnummer bitte neu pruefen' im Namen, Chat als v 2", ec.get("v") == 2 and "Sicherheitsnummer" in (ec.get("name") or ""), ec.get("name"))
    ac = A.chat(de["id"], lv=3)
    ok("App 2.5: kein Hinweis im Namen, v3.forced gesetzt", "Sicherheitsnummer" not in (ac.get("name") or "") and ac.get("v3", {}).get("forced") is True, ac)
    em = E.msgs(de["id"], lv=2)
    ok("Systemzeile mit verify", any(x["kind"] == "sys" and json.loads(x["body_ct"]).get("verify") is True for x in em["messages"]), em)
    auth.save_setting("chat_v3_days", 90)

    # --- Kontoloeschung: Reaktionen und Verweise auf Bearbeitungen weg -------------------------------------------------
    with connect() as c, c:                                           # Gruppe bleibt nach der Loeschung bestehen
        seq = c.execute("SELECT seq FROM chat_msg WHERE id=?", (md["id"],)).fetchone()[0]
        bseq = c.execute("SELECT seq FROM chat_msg WHERE id=?", (mb["id"],)).fetchone()[0]
        c.execute("INSERT INTO chat_reaction (msg_seq, reactor, dev, ts, ct, sig, at) VALUES (?,?,?,?,?,?,?)",
                  (seq, B.handle, B.dev["id"], 1, "x", "x", time.time()))
        c.execute("UPDATE chat_msg SET edit_seq=?, edited_at=?, edit_n=1 WHERE seq=?", (bseq, time.time(), seq))
    from saga.userdata import delete_user_data
    delete_user_data("cu-bert")
    with connect() as c:
        rx = c.execute("SELECT COUNT(*) FROM chat_reaction WHERE reactor=?", (B.handle,)).fetchone()[0]
        es = c.execute("SELECT edit_seq FROM chat_msg WHERE seq=?", (seq,)).fetchone()
    ok("Kontoloeschung: Reaktionen des Kontos weg, Verweis auf seine Bearbeitung geloest", rx == 0 and es is not None and es[0] is None, (rx, es))


if __name__ == "__main__":
    main()
