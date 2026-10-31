"""Umstellauftrag Chat v1 -> Stufe 3 (2.5, Konzept A4.3-A4.5, saga.chat.convert_v3) - Server-Seite, PyNaCl als Client.

Geprueft: ohne Handle-Zertifikat aller Mitglieder keine Umstellung; solange ein aktives Geraet nicht chat3 meldet (aeltere
App, uebernimmt "ueberprueft" nicht) keine Umstellung - Geraete, die laenger als 30 Tage nicht da waren, halten sie nicht
auf; nach Umstellung v=3 mit chat_ref, v3_seq = letzte Nachricht v1, Systemzeile {"type":"crypto","v":3} ohne Namen;
beliebig oft ausfuehrbar; Altbestand: alte Nachrichten bleiben v1 lesbar (Absender mit Benutzernamen, Geraetedaten v1),
neue im Format v2 mit Handles; wer nach der Umstellung beitritt, sieht keinen Anmeldenamen (auch nicht im Altbestand);
knows() nur fuer Mitglieder von vor der Umstellung; Senden v1 in einen umgestellten Chat -> 409; Stichtag (chat_v3_days):
Umstellung trotz aelterer Geraete mit Hinweis "Sicherheitsnummer bitte neu pruefen" fuer Apps der Stufe 2; Handles in
Chats v1 fuer Clients der Stufe 3 (Vertrauens-Uebernahme); Kontoloeschung raeumt Reaktionen und Bearbeitungs-Verweise auf.
2.5 Standard an: ohne gespeicherten Wert ist chat_v3 an (Standardwert, /api/chat/me, Faehigkeit chat3), ausdruecklich
false bleibt aus (kein Umstellen, keine Reaktionen); Gruppen- und Seiten-Chats mit Mitgliedern ohne Chat-Identitaet
werden umgestellt (eingerichtete Konten ohne Zertifikat halten auf); Abbruch mitten im Lauf (Transaktion je Chat) und
Neustart (Merker zurueckgesetzt) - der naechste Lauf stellt den Rest um; Zeitbudget: Lauf mit Budget 0 macht nach
einem Stapel Schluss und der naechste dort weiter; viele Chats (300 v1-Chats) in einem Lauf mit Zeitangabe.
Wegwerf-Konten cu-*, Gruppe cu3-*; setzt chat_v3 waehrend des Tests und stellt den gespeicherten Zustand wieder her."""
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
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cu3-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'cu-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cu3-%'")


def v_of(cid):
    with connect() as c:
        r = c.execute("SELECT v, ref, v3_seq, v3_forced, last_seq FROM chat WHERE id=?", (cid,)).fetchone()
    return dict(zip(("v", "ref", "v3_seq", "forced", "last_seq"), r)) if r else {}


def setv3(on, days=None):
    auth.save_setting("chat_v3", on)
    if days is not None:
        auth.save_setting("chat_v3_days", days)
    time.sleep(WAIT)


def stored(k):
    with connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def unset(k):
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k=?", (k,))
    auth._settings_cache["v"] = None


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3_days", "chat_v3_since", "search_username")}
    keep_v3 = stored("chat_v3")                                       # gespeicherte Zeile (None = Standardwert)
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("chat_v3", False), ("chat_v3_days", 90),
                 ("chat_v3_since", time.time())):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
        run_default()
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        if keep_v3 is None:
            unset("chat_v3")
        else:
            with connect() as c, c:
                c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", ("chat_v3", keep_v3))
            auth._settings_cache["v"] = None
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


def v1_chat(owner, kind, **kw):
    """Chat v1 anlegen (Stufe 3 kurz aus) -> Chat-Objekt."""
    d = owner.api("POST", "/api/chats", dict(kind=kind, **kw))[1]["chat"]
    return d


def run_default():
    """2.5: chat_v3 Standard an; ausdruecklich aus bleibt aus; Gruppen-/Seiten-Chats; Abbruch/Neustart; viele Chats."""
    # --- Standardwert ------------------------------------------------------------------------------------------------
    unset("chat_v3")
    time.sleep(WAIT)
    ok("Standardwert chat_v3 = an (auth.DEFAULTS)", auth.DEFAULTS["chat_v3"] is True)
    ok("Ohne gespeicherten Wert: Einstellung an, Stufe 3 fuer neue Chats", auth.settings(fresh=True)["chat_v3"] is True and CH.v3_new())
    A = P("cu-gina", "Gina Um")
    s, me = A.api("GET", "/api/chat/me")
    ok("Ohne gespeicherten Wert: /api/chat/me meldet chat3, react, edit, voice", s == 200
       and {"chat3", "react", "edit", "voice"} <= set(me.get("features") or []), me.get("features"))
    from saga import federation as F
    ok("Ohne gespeicherten Wert: Faehigkeit chat3 in der Foederation", "chat3" in F.caps2())
    B = P("cu-hans", "Hans Um")
    A.seen(lv=3), B.seen(lv=3)
    dn = A.api("POST", "/api/chats", {"kind": "dm", "user": "cu-hans"})[1]["chat"]
    ok("Neuer Direkt-Chat entsteht gleich in Stufe 3", v_of(dn["id"])["v"] == 3, v_of(dn["id"]))
    ok("Neuer Chat: x3_ok (Reaktionen, Bearbeiten, Sprachnachrichten)", A.chat(dn["id"]).get("x3_ok") is True, A.chat(dn["id"]))

    # --- ausdruecklich aus bleibt aus ----------------------------------------------------------------------------------
    setv3(False)
    ok("Ausdruecklich aus: gespeichert und wirksam", stored("chat_v3") == "false" and not CH.v3_new())
    Ii = P("cu-ida", "Ida Um")
    Ii.seen(lv=3)
    di = A.api("POST", "/api/chats", {"kind": "dm", "user": "cu-ida"})[1]["chat"]
    A.send(di["id"], "v1 bleibt")
    ok("Ausdruecklich aus: neuer Chat v1, Auftrag stellt nichts um", v_of(di["id"])["v"] == 1 and CH.convert_v3(limit=100000) == 0
       and v_of(di["id"])["v"] == 1)
    ac = A.chat(di["id"])
    ok("Ausdruecklich aus: x3_ok false mit x3_why setting", ac.get("x3_ok") is False and ac.get("x3_why") == "setting", ac)
    s, me = A.api("GET", "/api/chat/me")
    ok("Ausdruecklich aus: keine Faehigkeiten chat3/react", not {"chat3", "react"} & set(me.get("features") or []), me.get("features"))
    ok("Ausdruecklich aus: Wert bleibt nach erneutem Laden (kein Standard ueberschreibt)", auth.settings(fresh=True)["chat_v3"] is False)

    # --- Gruppen- und Seiten-Chat mit Mitglied ohne Chat-Identitaet -----------------------------------------------------
    J = P("cu-jule", "Jule Um", certs=False)                          # hat Chat nie eingerichtet (keine Identitaet)
    with connect() as c, c:
        c.execute("DELETE FROM chat_device WHERE uid='cu-jule'")
        c.execute("DELETE FROM chat_identity WHERE uid='cu-jule'")
        auth.ensure_group(c, "cu3-team")
    for u in ("cu-gina", "cu-hans", "cu-jule"):
        auth.set_user_groups(auth.get_user(u)["id"], ["cu3-team"])
    s, gd = A.api("POST", "/api/chats", {"kind": "group", "group": "cu3-team"})
    gc = gd.get("chat") or {}
    A.send(gc["id"], "Gruppe alt")
    nid = A.api("POST", "/api/notes", {"title": "Umstellung Seite"})[1]["id"]
    A.api("PUT", f"/api/notes/{nid}/shares", {"type": "group", "target": "cu3-team", "role": "view"})
    pc = A.api("POST", "/api/chats", {"kind": "page", "note_id": nid})[1]["chat"]
    A.send(pc["id"], "Seite alt")
    ok("Ausgang: Gruppen- und Seiten-Chat v1 (Stufe 3 aus)", v_of(gc["id"])["v"] == 1 and v_of(pc["id"])["v"] == 1)
    # eingerichtetes Konto ohne Zertifikat (nur Geraet vor 2.3) haelt auf; Konto ohne Identitaet nicht
    K = P("cu-karl", "Karl Um", certs=False)
    fk = A.api("POST", "/api/chats", {"kind": "free", "members": ["cu-karl"]})[1]["chat"]
    A.send(fk["id"], "Karl alt")
    unset("chat_v3")                                                  # Update auf 2.5 ohne gespeicherten Wert
    time.sleep(WAIT)
    ok("Nach dem Zuruecksetzen wieder an (Standard)", CH.v3_new())
    CH.convert_v3(limit=100000)
    ok("Gruppen-Chat umgestellt (Mitglied ohne Chat-Identitaet haelt nicht auf)", v_of(gc["id"])["v"] == 3, v_of(gc["id"]))
    ok("Seiten-Chat umgestellt", v_of(pc["id"])["v"] == 3, v_of(pc["id"]))
    ok("Chat mit eingerichtetem Konto ohne Handle-Zertifikat bleibt v1", v_of(fk["id"])["v"] == 1)
    ok("Direkt-Chat von vorhin (Ida, chat3) jetzt umgestellt", v_of(di["id"])["v"] == 3)
    gm = A.msgs(gc["id"])
    ok("Gruppe: Verlauf v1 bleibt lesbar", any(x.get("kind") != "sys" and A.open(gc["id"], x).get("text") == "Gruppe alt" for x in gm["messages"]
                                                if not x.get("sender_handle")), len(gm["messages"]))
    ok("Gruppe: x3_ok nach der Umstellung", A.chat(gc["id"]).get("x3_ok") is True)
    gj = J.api("GET", f"/api/chats/{gc['id']}")
    ok("Jule (ohne Identitaet) bleibt Mitglied und sieht den Chat in Stufe 3", gj[0] == 200 and (gj[1].get("chat") or {}).get("v") == 3, gj)
    K.certs()
    K.seen(lv=3)
    CH.convert_v3(limit=100000)
    ok("Karl mit Zertifikat -> umgestellt", v_of(fk["id"])["v"] == 3)

    # --- Alte App ohne v2: weiter Hinweis, nichts geht verloren ------------------------------------------------------
    L = P("cu-lena", "Lena Um")
    setv3(False)
    dl = A.api("POST", "/api/chats", {"kind": "dm", "user": "cu-lena"})[1]["chat"]
    A.send(dl["id"], "fuer Lena")
    with connect() as c, c:
        c.execute("UPDATE chat_device SET caps='[]' WHERE uid='cu-lena'")    # Lena hat nur eine alte App
    unset("chat_v3")
    auth.save_setting("chat_v3_days", 0)
    time.sleep(WAIT)
    CH.convert_v3(limit=100000)
    ok("Stichtag: Chat mit alter App umgestellt (forced)", v_of(dl["id"])["v"] == 3 and v_of(dl["id"])["forced"] == 1)
    s, lo = req("GET", f"/api/chats/{dl['id']}", L.k)                 # App ohne X-Saga-Chat (2.1/2.2)
    ok("App ohne v2: fed_hint 'App aktualisieren'", s == 200 and "aktualisieren" in ((lo.get("chat") or {}).get("fed_hint") or ""), lo)
    lm = L.msgs(dl["id"], lv=3)
    ok("Nach dem Update: Verlauf v1 lesbar", any(L.open(dl["id"], x).get("text") == "fuer Lena" for x in lm["messages"]
                                                  if x.get("kind") != "sys" and not x.get("sender_handle")), len(lm["messages"]))
    auth.save_setting("chat_v3_days", 90)

    # --- Abbruch mitten im Lauf und Neustart --------------------------------------------------------------------------
    setv3(False)
    many = []
    others = [P(f"cu-m{i:02d}", f"M{i} Um") for i in range(3)]
    for o in others:
        o.seen(lv=3)
    for i in range(300):                                              # viele v1-Chats (freie Gruppen, gleiche Mitglieder)
        with connect() as c, c:
            cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free',?,?)", ("cu-gina", time.time())).lastrowid
            for u in ["cu-gina"] + [o.uid for o in others]:
                c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, time.time()))
        many.append(cid)
    unset("chat_v3")
    time.sleep(WAIT)
    real = CH._convert_ids
    calls = {"n": 0}

    def boom(ids, now, deadline):                                     # zweiter Stapel bricht ab (Container-Neustart o. ae.)
        calls["n"] += 1
        if calls["n"] == 2:
            real(ids[:5], now, deadline)
            raise RuntimeError("Abbruch")
        return real(ids, now, deadline)
    CH._convert_ids = boom
    try:
        CH.convert_v3(limit=100)
        crashed = False
    except RuntimeError:
        crashed = True
    finally:
        CH._convert_ids = real
    vs = [v_of(i)["v"] for i in many]
    ok(f"Abbruch: Teil umgestellt, Rest v1, nichts halb ({vs.count(3)} von 300)", crashed and 0 < vs.count(3) < 300 and set(vs) <= {1, 3}, vs.count(3))
    with connect() as c:
        half = c.execute(f"SELECT COUNT(*) FROM chat WHERE id IN ({','.join('?' * len(many))}) AND ((v=3) <> (ref IS NOT NULL) "
                         "OR (v=3 AND NOT EXISTS (SELECT 1 FROM chat_msg m WHERE m.chat_id=chat.id AND m.kind='sys')))", tuple(many)).fetchone()[0]
    ok("Abbruch: jeder umgestellte Chat vollstaendig (ref + Systemzeile)", half == 0, half)
    CH._conv["after"] = 0                                             # Neustart: Merker im Speicher weg
    t0 = time.time()
    n = CH.convert_v3(limit=100)
    dt = time.time() - t0
    ok(f"Neustart: naechster Lauf stellt den Rest um ({n} in {dt:.1f} s)", all(v_of(i)["v"] == 3 for i in many), [v_of(i)["v"] for i in many].count(1))
    ok("Viele Chats: 300 Chats in einem Lauf unter dem Zeitbudget", dt < CH.CONVERT_BUDGET_S, dt)
    with connect() as c:
        dup = c.execute(f"SELECT COUNT(*) FROM chat_msg WHERE kind='sys' AND chat_id IN ({','.join('?' * len(many))})", tuple(many)).fetchone()[0]
    ok("Keine doppelte Systemzeile nach dem Neustart", dup == 300, dup)

    # --- Zeitbudget: Budget 0 -> ein Stapel, naechster Lauf macht weiter ------------------------------------------------
    setv3(False)
    more = []
    for i in range(7):
        with connect() as c, c:
            cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free',?,?)", ("cu-gina", time.time())).lastrowid
            for u in ("cu-gina", others[0].uid):
                c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, time.time()))
        more.append(cid)
    unset("chat_v3")
    time.sleep(WAIT)
    CH._conv["after"] = 0
    with connect() as c:
        blocked = c.execute("SELECT COUNT(*) FROM chat WHERE v=1 AND ref IS NULL AND home_peer IS NULL AND id<?", (min(more),)).fetchone()[0]
    n1 = CH.convert_v3(limit=blocked + 3, budget=0)
    n2 = CH.convert_v3(limit=blocked + 3, budget=0)
    ok(f"Zeitbudget: erster Lauf ein Stapel ({n1}), zweiter macht weiter ({n2})", n1 == 3 and n2 == 4 and all(v_of(i)["v"] == 3 for i in more), (n1, n2, blocked))


if __name__ == "__main__":
    main()
