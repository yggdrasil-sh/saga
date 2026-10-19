"""Foederation 2.3 E2/E3: Direkt-Chats und Anrufe ueber Servergrenzen mit zwei eigenen Saga-Instanzen A und B (eigene
Datenbanken und Ports, wie tests/foederation2.py). Die Apps sind In-Process-Clients mit PyNaCl (tests/chatlib.py, saga-chat/v2).

Geprueft: Suche + Beginnen (Heim = Server der beginnenden Person, chat_ref), Nachrichtenanfrage (still: keine Benachrichtigung,
kein Klingeln), Annehmen, Senden in beide Richtungen (synchron zum Heim, fseq, Fan-out), Empfaenger exakt (409), Heim offline ->
Warteschlange beim Absender -> spaeter zugestellt, Wiederholung/Duplikat (msg_id), gefaelschter Absender, Umsortieren, Luecke
(beim Heim nachgeholt), Ketten-Epoche nach "Backup zurueckgespielt", Totmann (eine abgewiesene Zeile blockiert den Ausgangskorb
nicht), Anhaenge (beim Absender, Vorschau vorab, Abruf bei Bedarf), Loeschen (Nachricht, Chat fuer alle), Frist ueber das Heim,
KI standardmaessig aus, Sperren je Person/Instanz, aeltere Apps (Hinweis statt kaputt), signierte Geraete-Liste, Anrufe
(saga-call/v2: Klingeln, Annehmen, Signale, Auflegen, Anfrage klingelt nicht, Besetzt), Kontoloeschung, Trennen (schreibgeschuetzt,
Anrufe enden). Kompatibilitaet: FED_B_APP=<Saga 2.1/2.2> -> keine Chats ueber Servergrenzen, sinnvolle Fehlermeldung, keine
Serverfehler. Raeumt auf (Datenbanken geloescht)."""
import http.client, json, os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402  (Start mit Kompat-Stand, Admin-Helfer, connect)
import chatlib as C  # noqa: E402
from saga import fedsig  # noqa: E402

T.I["a"].update(port=8295, admin=8294, db="saga_fca", url="http://127.0.0.1:8295", host="127.0.0.1:8295", state="/tmp/fedchat-a", allow="localhost:8297")
T.I["b"].update(port=8297, admin=8296, db="saga_fcb", url="http://localhost:8297", host="localhost:8297", state="/tmp/fedchat-b", allow="127.0.0.1:8295")
HA, HB = T.I["a"]["host"], T.I["b"]["host"]
ok, hreq, wait, jd, sql, py = T.ok, T.hreq, T.wait, T.jd, T.sql, T.py
COMPAT = F2.COMPAT


def api(k, uid, m, p, body=None, v2=True):
    hdr = {"X-Saga": "1"}
    if v2:
        hdr["X-Saga-Chat"] = "2"
    s, _, d = hreq(k, m, p, body=body, hdr=hdr, cookie=T.ck(k, uid))
    return s, jd(d)


def raw(k, uid, m, p, data, ctype="application/octet-stream"):
    s, h, d = hreq(k, m, p, raw=data, hdr={"X-Saga": "1", "X-Saga-Chat": "2", "Content-Type": ctype}, cookie=T.ck(k, uid))
    return s, h, d


class Events:
    """Live-Ereignisse (/api/events) eines Kontos im Hintergrund mitschreiben (wie eine App)."""

    def __init__(self, k, uid):
        i = T.I[k]
        self.c = http.client.HTTPConnection("127.0.0.1", i["port"], timeout=60)
        self.c.request("GET", "/api/events", headers={"Host": i["host"], "Accept": "text/event-stream", "Cookie": T.ck(k, uid)})
        self.r, self.ev = self.c.getresponse(), []
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

    def of(self, typ, mid):
        return [d for t, d in list(self.ev) if t == typ and isinstance(d, dict) and d.get("id") == mid]

    def close(self):
        try:
            self.c.sock.shutdown(2); self.c.close()
        except Exception:  # noqa: BLE001
            pass


class App:
    """Nachrichtenliste einer App: laedt bei jedem chat.message-Ereignis nur NEUERE Nachrichten (after=) und uebernimmt den
    Zustellstand bekannter Nachrichten nur aus dem Ereignis (failed bzw. bestaetigt) - so arbeiten Web, Android, iOS, Linux."""

    def __init__(self, user, cid, ev):
        self.u, self.cid, self.ev, self.msgs, self.seen, self.stop = user, cid, ev, {}, 0, False
        s, r = api(user.k, user.uid, "GET", f"/api/chats/{cid}/messages?device={user.dev['id']}")
        for x in r.get("messages") or []:
            self.msgs[x["id"]] = {"seq": x["seq"], "fstate": x.get("fstate")}
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):                     # wie die App: sofort auf jedes Ereignis reagieren
        while not self.stop:
            try:
                self.pump()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.01)

    def pump(self):
        evs = [d for t, d in list(self.ev.ev) if t == "chat.message" and isinstance(d, dict) and d.get("chat_id") == self.cid]
        for d in evs[self.seen:]:
            m = self.msgs.get(d.get("id"))
            if m is not None and m.get("fstate"):
                m["fstate"] = "failed" if d.get("failed") else None
            last = max([x["seq"] for x in self.msgs.values()] or [0])
            s, r = api(self.u.k, self.u.uid, "GET", f"/api/chats/{self.cid}/messages?device={self.u.dev['id']}&after={last}")
            for x in r.get("messages") or []:
                self.msgs.setdefault(x["id"], {"seq": x["seq"], "fstate": x.get("fstate")})
        self.seen = len(evs)
        return self.msgs


class User:
    """App eines Kontos: Identitaet, ein Geraet, Zertifikate v2, signierte Geraete-Liste."""

    def __init__(self, k, uid):
        self.k, self.uid = k, uid
        self.ident, self.dev = C.identity(), C.device()
        s, d = api(k, uid, "PUT", "/api/chat/identity", C.setup_body(uid, self.ident, self.dev, ops=1, mem=8 << 20))
        ok(f"{k.upper()}: {uid} richtet Chats ein ({s})", s == 200)
        self.certs()

    def certs(self):
        s, me = api(self.k, self.uid, "GET", "/api/chat/v2/me")
        self.handle = me["handle"]
        body = {"handle_sig": C.b64(C.handle_sig(self.handle, self.ident)), "devices": {self.dev["id"]: C.b64(C.device_sig_v2(self.handle, self.ident, self.dev))}}
        s, me = api(self.k, self.uid, "PUT", "/api/chat/v2/cert", body)
        ver = ((me.get("device_set") or {}).get("ver") or 0) + 1
        s, me = api(self.k, self.uid, "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(self.handle, self.ident, ver, me["device_set_want"])})
        ok(f"{self.k.upper()}: {self.uid} Zertifikate v2 + signierte Geraete-Liste ({s})", s == 200 and not me["need"] and me["device_set_ok"])
        return me

    def keys(self, handles, fresh=False):
        s, d = api(self.k, self.uid, "GET", "/api/chat/v2/keys?handles=" + ",".join(handles) + ("&fresh=1" if fresh else ""))
        return d["keys"]

    def msg(self, ch, text, handles=None, files=None, mid=None, reply_to=None, ai=None, extra_keys=None, body=None):
        """Nachricht fuer alle Mitglieder des Chats verschluesseln (saga-chat/v2) -> (Anfrage-Body, Schluessel K)."""
        handles = handles or [m["handle"] for m in ch["members"]]
        ents = self.keys(handles)
        rc = C.recipients_v2([e for e in ents.values() if e])
        rc.update(extra_keys or {})
        b = body or {"v": 1, "type": "text", "text": text}
        if files:
            b["files"] = files
        m = C.encrypt_v2(ch["ref"], self.handle, self.dev, rc, json.dumps(b), reply_to=reply_to, ai=ai, mid=mid)
        m["v"] = 2
        m["files"] = [f["id"] for f in files or []]
        return m

    def send(self, ch, text, **kw):
        m = self.msg(ch, text, **kw)
        s, d = api(self.k, self.uid, "POST", f"/api/chats/{ch['id']}/messages", m)
        return s, d, m

    def chats(self):
        return api(self.k, self.uid, "GET", "/api/chats?device=" + self.dev["id"])[1]["chats"]

    def chat(self, ref):
        return next((c for c in self.chats() if c.get("ref") == ref), None)

    def msgs(self, cid):
        s, d = api(self.k, self.uid, "GET", f"/api/chats/{cid}/messages?calls=1&device={self.dev['id']}")
        return d.get("messages") or [] if s == 200 else []

    def read(self, ch, m):
        return json.loads(C.decrypt_v2(ch["ref"], m, m["key"], *((self.dev["enc_pk"], self.dev["enc_sk"]) if m["key_for"] == "device"
                                                                   else (self.ident["backup_pk"], self.ident["backup_sk"]))))

    def texts(self, ch):
        out = []
        for m in self.msgs(ch["id"]):
            if m["kind"] == "msg" and m.get("key"):
                try:
                    out.append(self.read(ch, m).get("text"))
                except Exception:  # noqa: BLE001
                    out.append("?")
        return out


def m2_id(u, ch):
    """Kennung einer Nachricht der anderen Person im Chat."""
    return next(m["id"] for m in u.msgs(ch["id"]) if m["kind"] == "msg" and m["sender_handle"] != u.handle)


def notifs(k, uid):
    return [n for n in api(k, uid, "GET", "/api/notifications")[1].get("items", []) if n.get("kind") == "chat.message"]


def own_priv(k):
    return fedsig.unb64u(py(k, "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))"))


def signed_post(k_to, priv, k_from, path, body):
    s, h, d, _, _ = T.signed(k_to, priv, T.I[k_from]["url"], "POST", path, body=json.dumps(body).encode())
    return s, jd(d)


def fed_log(k, ev):
    return [r[0] for r in sql(k, "SELECT detail FROM fed_log WHERE event=? ORDER BY id", (ev,))]


# =====================================================================================================
def part_chat(A, B, pa, pb):
    anna, bert = User("a", "fa-anna"), User("b", "fb-bert")
    ok("Handles ~x@host", anna.handle.endswith("@" + HA) and bert.handle.endswith("@" + HB))
    # --- Suche + Beginnen ---------------------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "GET", "/api/chat/people?q=bert")
    hit = next((x for x in d.get("remote") or [] if x["handle"] == bert.handle), None)
    ok(f"A: Chat-Suche findet Bert (Adressbuch) mit Handle ({[x.get('label') for x in d.get('remote') or []]})", bool(hit) and hit["host"] == HB)
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Direkt-Chat mit Bert begonnen ({s})", s == 200 and ch.get("v") == 2 and ch.get("home_self") and ch["ref"].endswith("@" + HA))
    ok("A: Chat-Kopf: Name aus dem Adressbuch, Herkunft, KI aus", ch.get("name") == "Bert Gast" and ch["peer"]["host"] == HB
       and ch["peer"]["handle"] == bert.handle and ch.get("ai") is False and not ch.get("readonly"))
    s, d2 = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle})
    ok("A: erneut beginnen -> derselbe Chat", s == 200 and d2["chat"]["id"] == ch["id"])
    bc = wait(lambda: bert.chat(ch["ref"]), 10)
    ok("B: Chat erscheint als Nachrichtenanfrage", bool(bc) and bc.get("request") and bc.get("state") == "request" and not bc.get("home_self")
       and bc["peer"]["handle"] == anna.handle)
    s, d = api("b", "fb-bert", "GET", "/api/chats")
    ok("B: Anfrage zaehlt nicht als ungelesen, requests=1", d.get("requests") == 1)
    # --- Senden (Heim = A) --------------------------------------------------------------------------------------------
    s, d, m1 = anna.send(ch, "Hallo Bert!")
    ok(f"A: Nachricht an Bert ({s}, fseq {d.get('fseq')})", s == 200 and d.get("fseq") == 1)
    ok("B: Nachricht angekommen und lesbar", wait(lambda: bert.texts(bc) == ["Hallo Bert!"], 15))
    ok("B: Anfrage -> keine Benachrichtigung", notifs("b", "fb-bert") == [])
    s, d = api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}/messages?device={anna.dev['id']}")
    ok("A: eigene Nachricht mit fseq, sender_handle, chat_ref im Verlauf", s == 200 and d["messages"][-1]["fseq"] == 1
       and d["messages"][-1]["sender_handle"] == anna.handle)
    m = anna.msg(ch, "falsche Empfaenger", handles=[anna.handle])
    s, d = api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/messages", m)
    ok(f"A: Empfaenger fehlen -> 409 mit Handles ({s})", s == 409 and sorted(d.get("handles") or []) == sorted([anna.handle, bert.handle]))
    bad = dict(anna.msg(ch, "x"), sig=C.b64(b"\0" * 64))
    ok("A: falsche Signatur -> 400", api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/messages", bad)[0] == 400)
    # --- Annehmen, Antworten (B -> Heim A synchron) ---------------------------------------------------------------------
    s, d = api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
    ok(f"B: Anfrage angenommen ({s})", s == 200 and d["chat"]["state"] == "ok" and not d["chat"]["request"])
    ok("A: Heim kennt die Annahme", wait(lambda: sql("a", "SELECT state FROM chat_member WHERE chat_id=? AND uid LIKE '@%'", (ch["id"],)) == [["ok"]], 10))
    s, d, m2 = anna.send(ch, "Schoen, dass du da bist")
    ok("B: jetzt mit Benachrichtigung", wait(lambda: len(notifs("b", "fb-bert")) == 1, 15))
    bc = bert.chat(ch["ref"])
    ev = Events("b", "fb-bert")
    time.sleep(0.5)
    app = App(bert, bc["id"], ev)
    s, d, m3 = bert.send(bc, "Hallo Anna, Gruesse von B", reply_to=m2["id"])
    ok(f"B: Antwort ueber das Heim ({s}, fseq {d.get('fseq')})", s == 200 and d.get("fseq") == 3 and not d.get("pending"))
    ok("A: Antwort angekommen", wait(lambda: anna.texts(ch)[-1:] == ["Hallo Anna, Gruesse von B"], 15))
    ok("B: eigene Nachricht bestaetigt (fseq, nicht ausstehend)",
       wait(lambda: [(x["fseq"], x.get("fstate")) for x in bert.msgs(bc["id"]) if x["id"] == m3["id"]] == [(3, None)], 15))
    # Fehler 2.3.0: der Server von B legte die Nachricht als 'pending' an und meldete sie sofort (chat.message) - die App
    # lud sie mit ⏳; die synchrone Bestaetigung des Heims kam ohne Ereignis -> die App blieb auf "wird zugestellt"
    ok(f"B: Live-Ereignis meldet die Bestaetigung ({[sorted(x) for x in ev.of('chat.message', m3['id'])]})",
       wait(lambda: app.msgs.get(m3["id"], {}).get("fstate", "x") is None, 10))
    app.stop = True
    ev.close()
    ok("B: Verlauf in fseq-Reihenfolge", [x["fseq"] for x in bert.msgs(bc["id"]) if x["kind"] == "msg"] == [1, 2, 3])
    # --- Tippen, Gelesen lokal ------------------------------------------------------------------------------------------
    ok("B: Tippen (verwerfbar) ok", api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/typing")[0] == 200)
    s, d = api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/read", {"seq": 10 ** 9})
    ok("B: Gelesen bleibt lokal", s == 200 and not sql("a", "SELECT 1 FROM fed_outbox WHERE payload LIKE '%read%'"))
    return anna, bert, ch, bc


def part_robust(A, B, pa, pb, anna, bert, ch, bc):
    privb, priva = own_priv("b"), own_priv("a")
    # --- Heim offline -> Warteschlange beim Absender ---------------------------------------------------------------------
    T.procs["a"].terminate(); T.procs["a"].wait(10)
    s, d, mq = bert.send(bc, "Waehrend A weg ist")
    ok(f"B: Heim offline -> angenommen, wartet ({s}, {d})", s == 200 and d.get("pending") is True)
    ok("B: Nachricht als ausstehend sichtbar", [x.get("fstate") for x in bert.msgs(bc["id"]) if x["id"] == mq["id"]] == ["pending"])
    s, d, mq2 = bert.send(bc, "Und noch eine")
    ok("B: zweite wartet hinter der ersten (Reihenfolge)", s == 200 and d.get("pending"))
    F2.start("a")
    sql("b", "UPDATE fed_outbox SET next_try=0")
    ok("A: beide Nachrichten in Reihenfolge angekommen", wait(lambda: anna.texts(ch)[-2:] == ["Waehrend A weg ist", "Und noch eine"], 40))
    ok("B: beide bestaetigt", wait(lambda: [x.get("fstate") for x in bert.msgs(bc["id"]) if x["id"] in (mq["id"], mq2["id"])] == [None, None], 30))
    # --- Duplikat / Wiederholung ----------------------------------------------------------------------------------------
    ents = anna.keys([anna.handle, bert.handle])
    body = {"op": "send", "ref": ch["ref"], "msg": dict({k: mq[k] for k in ("id", "ts", "sender_handle", "sender_device", "body_ct", "sig", "reply_to", "ai")}),
            "keys": mq["keys"], "files": [], "sender_keys": ents[bert.handle]}
    s, d = signed_post("a", privb, "b", "/fed/v2/chat", body)
    ok(f"Heim: gleiche Nachricht nochmal -> idempotent ({s}, {d})", s == 200 and d.get("dup"))
    m_other = bert.msg(bc, "anderer Inhalt, gleiche Kennung", mid=mq["id"])
    body2 = dict(body, msg={k: m_other[k] for k in ("id", "ts", "sender_handle", "sender_device", "body_ct", "sig", "reply_to", "ai")}, keys=m_other["keys"])
    s, d = signed_post("a", privb, "b", "/fed/v2/chat", body2)
    ok(f"Heim: gleiche Kennung, anderer Inhalt -> 409 + Pruefprotokoll ({s})", s == 409 and fed_log("a", "chat.replay"))
    # --- gefaelschter Absender (B gibt sich als Anna aus) -------------------------------------------------------------
    fake = anna.msg(ch, "Ich bin Anna (nicht)")
    body3 = dict(body, msg={k: fake[k] for k in ("id", "ts", "sender_handle", "sender_device", "body_ct", "sig", "reply_to", "ai")}, keys=fake["keys"])
    s, d = signed_post("a", privb, "b", "/fed/v2/chat", body3)
    ok(f"Heim: fremder Absender-Handle -> 403 + Pruefprotokoll ({s})", s == 403 and fed_log("a", "chat.forged"))
    body4 = dict(body, msg=dict(body["msg"], id=C.hexid()))
    s, d = signed_post("a", privb, "b", "/fed/v2/chat", body4)
    ok(f"Heim: Signatur passt nicht zur Kennung -> 403 ({s})", s == 403)
    # --- B gibt sich als Heim aus (Ereignis fuer einen Chat mit Heim A) -------------------------------------------------
    evm = {"id": C.hexid(), "type": "chat.msg", "ref": ch["ref"], "fseq": 99, "msg": body["msg"], "keys": {}}
    s, d = signed_post("a", privb, "b", "/fed/v2/events", {"events": [evm]})
    ok(f"A: Ereignis von Nicht-Heim -> ignoriert ({d})", s == 200 and d["results"] == ["ignored"])
    # --- Umsortieren / Luecke (A = Heim schickt an B) -------------------------------------------------------------------
    cur = sql("b", "SELECT fseq FROM chat WHERE id=?", (bc["id"],))[0][0]
    mo = anna.msg(ch, "alt nachgeschoben")
    item = {"fseq": cur, "msg": {k: mo[k] for k in ("id", "ts", "sender_handle", "sender_device", "body_ct", "sig", "reply_to", "ai")},
            "keys": {k: v for k, v in mo["keys"].items() if k.endswith("@" + HB)}, "sender_keys": ents[anna.handle]}
    s, d = signed_post("b", priva, "a", "/fed/v2/events", {"events": [dict(item, id=C.hexid(), type="chat.msg", ref=ch["ref"])]})
    ok(f"B: aeltere fseq mit neuer Nachricht -> abgewiesen + Pruefprotokoll ({d})", d["results"] == ["ignored"] and fed_log("b", "chat.reorder"))
    # Luecke: das Heim hat fseq cur+1 (wirklich gespeichert), schickt aber nur cur+2 -> B holt cur+1 nach
    sql("a", "UPDATE fed_peer SET status='active'")                   # (nichts aendern - nur Lesbarkeit)
    py("a", f"""
from saga import fed_chat as FC
from db import connect
import time
with connect() as c, c:
    ch = FC.by_ref(c, {ch['ref']!r}, lock=True)
    c.execute("UPDATE chat SET fseq=fseq+1 WHERE id=?", (ch["id"],))
print(json.dumps(True))""")
    s, d, mg = anna.send(ch, "nach der Luecke")
    ok("B: Luecke erkannt, beim Heim nachgeholt, dann zugestellt", wait(lambda: bert.texts(bc)[-1:] == ["nach der Luecke"], 60) and fed_log("b", "chat.gap"))
    # --- Ketten-Epoche: A spielt ein "Backup" zurueck (Kette und fseq springen zurueck) -----------------------------------
    T.procs["a"].terminate(); T.procs["a"].wait(10)
    n0 = sql("b", "SELECT fseq FROM chat WHERE id=?", (bc["id"],))[0][0]
    sql("a", "UPDATE fed_chain_out SET seq=1 WHERE chain LIKE 'chat:%'")
    sql("a", "UPDATE chat SET fseq=1 WHERE id=?", (ch["id"],))
    sql("a", "UPDATE setting SET v='\"alt\"' WHERE k='fed_epoch_mark'")   # Marke passt nicht mehr zur Datei: wie nach Restore
    F2.start("a")
    ok("A: neue Ketten-Epoche nach 'Zurueckspielen'", wait(lambda: fed_log("a", "chain.epoch.new"), 15))
    s, d, me_ = anna.send(ch, "nach dem Backup")
    ok(f"B: nach neuer Epoche angenommen statt still verworfen (fseq {d.get('fseq')} < {n0})",
       wait(lambda: bert.texts(bc)[-1:] == ["nach dem Backup"], 30) and fed_log("b", "chain.epoch") and fed_log("b", "chat.epoch"))
    # --- Totmann: eine Zeile, die B dauerhaft abweist, blockiert den Ausgangskorb nicht --------------------------------------
    T.procs["a"].terminate(); T.procs["a"].wait(10)
    sql("a", "INSERT INTO fed_outbox (peer_id, payload, created, next_try) VALUES (?, '{\"type\": \"directory.changed\", \"id\": \"' || ? || "
             "'\", \"pad\": \"' || repeat('x', 3000000) || '\"}', ?, 0)", (pa, C.hexid(), time.time()))
    F2.start("a")
    s, d, mt = anna.send(ch, "trotz Giftpille")
    ok("B: neue Nachricht kommt trotz abgewiesener Zeile im Ausgangskorb an", wait(lambda: bert.texts(bc)[-1:] == ["trotz Giftpille"], 40))
    ok("A: normaler Neustart -> keine weitere Epoche (Pruefer)", len(fed_log("a", "chain.epoch.new")) == 1)
    ok("A: abgewiesene Zeile wartet mit Fehlversuch", wait(lambda: (sql("a", "SELECT attempts FROM fed_outbox WHERE payload LIKE '%pad%'") or [[0]])[0][0] >= 1, 30))
    sql("a", "DELETE FROM fed_outbox WHERE payload LIKE '%pad%'")


def part_more(A, B, pa, pb, anna, bert, ch, bc):
    # --- Anhaenge: beim Absender (B), Vorschau vorab, Original bei Bedarf -------------------------------------------------
    small, key_s = C.encrypt_file_v2(bc["ref"], b"klein" * 100)
    large, key_l = C.encrypt_file_v2(bc["ref"], os.urandom(400 * 1024))
    s1, _, d1 = raw("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/files", small)
    s2, _, d2 = raw("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/files", large)
    f1, f2 = jd(d1)["id"], jd(d2)["id"]
    s, d, mf = bert.send(bc, "zwei Anhaenge", files=[{"id": f1, "k": C.b64(key_s), "n": "a.txt"}, {"id": f2, "k": C.b64(key_l), "n": "b.bin"}])
    ok(f"B: Nachricht mit Anhaengen ({s})", s == 200)
    ok("A: kleiner Anhang vorab geholt (Zwischenkopie)", wait(lambda: sql("a", "SELECT cached FROM chat_file_fed WHERE id=?", (f1,)) == [[1]], 20))
    ok("A: grosser Anhang noch nicht geholt", sql("a", "SELECT cached FROM chat_file_fed WHERE id=?", (f2,)) == [[0]])
    # Pruefer: angekuendigte Groesse muss stimmen (sonst koennte ein boeser Server als "Vorschaubild" Riesendateien unterschieben)
    real = sql("a", "SELECT size FROM chat_file WHERE id=?", (f2,))[0][0]
    sql("a", "UPDATE chat_file SET size=1000 WHERE id=?", (f2,))
    s, *_ = hreq("a", "GET", f"/api/chat/files/{f2}", hdr={"X-Saga": "1"}, cookie=T.ck("a", "fa-anna"))
    ok(f"A: Anhang mit falscher Groesse wird nicht uebernommen ({s})", s == 404 and sql("a", "SELECT cached FROM chat_file_fed WHERE id=?", (f2,)) == [[0]])
    sql("a", "UPDATE chat_file SET size=? WHERE id=?", (real, f2))
    s, h, data = hreq("a", "GET", f"/api/chat/files/{f2}", hdr={"X-Saga": "1"}, cookie=T.ck("a", "fa-anna"))
    ok("A: grosser Anhang bei Bedarf geholt, entschluesselbar", s == 200 and C.decrypt_file_v2(ch["ref"], data, key_l) and
       sql("a", "SELECT cached FROM chat_file_fed WHERE id=?", (f2,)) == [[1]])
    s, *_ = hreq("a", "GET", f"/api/chat/files/{f1}", hdr={"X-Saga": "1"}, cookie=T.ck("a", "fa-carl"))
    ok("A: fremdes Konto bekommt den Anhang nicht", s == 404)
    s, d = signed_post("b", own_priv("a"), "a", "/fed/v2/chat", {"op": "typing"})  # (kein Datei-Endpunkt mit POST)
    s, h, dd, _, _ = T.signed("b", own_priv("a"), T.I["a"]["url"], "GET", f"/fed/v2/chat/files/{C.hexid()[:24]}")
    ok("B: unbekannte Datei -> 404", s == 404)
    # --- Loeschen ------------------------------------------------------------------------------------------------------
    s, d, mx = anna.send(ch, "gleich weg")
    wait(lambda: "gleich weg" in bert.texts(bc), 15)
    ok("A: eigene Nachricht fuer alle loeschen", api("a", "fa-anna", "DELETE", f"/api/chats/{ch['id']}/messages/{mx['id']}")[0] == 200)
    ok("B: auch dort weg", wait(lambda: "gleich weg" not in bert.texts(bc), 15))
    ok("B: eigene Nachricht loeschen ok, Annas (schon weg) -> 404", api("b", "fb-bert", "DELETE", f"/api/chats/{bc['id']}/messages/{mf['id']}")[0] == 200
       and api("b", "fb-bert", "DELETE", f"/api/chats/{bc['id']}/messages/{mx['id']}")[0] == 404)
    ok("B: fremde Nachricht loeschen -> 403", api("b", "fb-bert", "DELETE", f"/api/chats/{bc['id']}/messages/{m2_id(bert, bc)}")[0] == 403)
    ok("A: Bert hat seine Nachricht (mit Anhaengen) geloescht -> weg, Zwischenkopien weg",
       wait(lambda: "zwei Anhaenge" not in anna.texts(ch) and not sql("a", "SELECT 1 FROM chat_file WHERE id IN (?,?)", (f1, f2)), 15))
    # --- Frist ueber das Heim -------------------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "PATCH", f"/api/chats/{bc['id']}", {"retention_s": 86400})
    ok(f"B: Frist 1 Tag (beim Heim gesetzt) ({s})", s == 200 and d["chat"]["retention_s"] == 86400)
    ok("A: Frist uebernommen", wait(lambda: api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"]["retention_s"] == 86400, 15))
    s, d, mr = anna.send(ch, "mit Frist")
    ok("B: neue Nachricht laeuft ab (strengere Frist gilt lokal)", wait(lambda: any(x.get("expires_at") for x in bert.msgs(bc["id"]) if x["id"] == mr["id"]), 15))
    # --- KI standardmaessig aus -------------------------------------------------------------------------------------------
    ai_msg = anna.msg(ch, "ich tu so, als waere ich die KI", ai="0" * 64)
    ok("A: KI-Antwort ohne erlaubte KI -> 403", api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/messages", ai_msg)[0] == 403)
    # KI-Schalter ueber das Heim (B schaltet ein - hier direkt als signierter Auftrag, die KI selbst ist nicht eingerichtet)
    s, d = signed_post("a", own_priv("b"), "b", "/fed/v2/chat", {"op": "meta", "ref": ch["ref"], "actor": bert.handle, "ai": True})
    ok(f"Heim: KI-Schalter von Bert angenommen ({s})", s == 200)
    ok("A + B: KI an, Systemzeile 'KI eingeschaltet' bei beiden",
       wait(lambda: api("b", "fb-bert", "GET", f"/api/chats/{bc['id']}")[1]["chat"]["ai"] is True, 15)
       and any(m["kind"] == "sys" and json.loads(m["body_ct"]).get("on") is True for m in anna.msgs(ch["id"]))
       and wait(lambda: any(m["kind"] == "sys" for m in bert.msgs(bc["id"])), 10))
    s, d = signed_post("a", own_priv("b"), "b", "/fed/v2/chat", {"op": "meta", "ref": ch["ref"], "actor": anna.handle, "ai": False})
    ok("Heim: KI-Schalter im Namen einer Person des Heims -> abgewiesen", s == 404)
    api("a", "fa-anna", "PUT", f"/api/chats/{ch['id']}/ai", {"allowed": False})
    ok("A: KI wieder aus (Heim), bei B uebernommen", wait(lambda: api("b", "fb-bert", "GET", f"/api/chats/{bc['id']}")[1]["chat"]["ai"] is False, 15))
    # --- aeltere App: Hinweis statt kaputt --------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "GET", "/api/chats", v2=False)
    old = next((x for x in d["chats"] if x.get("ref") == ch["ref"]), {})
    ok("alte App: foederierter Chat mit Hinweis im Namen", old.get("fed_hint") and "aktualisieren" in old.get("name", ""))
    s, d = api("b", "fb-bert", "GET", f"/api/chats/{bc['id']}/messages", v2=False)
    ok("alte App: keine v2-Nachrichten, Hinweis", s == 200 and d["messages"] == [] and d.get("fed_hint"))
    s, d = api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/messages", {"id": C.hexid()}, v2=False)
    ok("alte App: Senden -> 400 mit Hinweis", s == 400 and "aktualisieren" in d.get("error", ""))
    ok("alte App: Empfaenger -> 400 mit Hinweis", api("b", "fb-bert", "GET", f"/api/chats/{bc['id']}/recipients", v2=False)[0] == 400)
    # --- Schluesselwechsel: Live-Ereignis + neue Geraete-Liste ------------------------------------------------------------
    d2 = C.device()
    s, _ = api("b", "fb-bert", "POST", "/api/chat/devices", C.device_api("fb-bert", bert.ident, d2, "Zweitgeraet"))
    ok("A: Schluesselwechsel -> Zwischenspeicher veraltet", wait(lambda: sql("a", "SELECT stale FROM fed_chat_keys WHERE handle=?", (bert.handle,)) == [[1]], 20))
    s, me = api("b", "fb-bert", "GET", "/api/chat/v2/me")
    ok("B: neues Geraet -> Zertifikat v2 fehlt", me["need"] and me["missing"] == [d2["id"]])
    s, me = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"devices": {d2["id"]: C.b64(C.device_sig_v2(bert.handle, bert.ident, d2))}})
    ok("B: mit Zertifikat -> Geraete-Liste muss neu signiert werden", me["need"] and not me["device_set_ok"] and len(me["device_set_want"]) == 2)
    ver = me["device_set"]["ver"]
    s, _ = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(bert.handle, bert.ident, ver, me["device_set_want"])})
    ok("B: gleiche Version der Geraete-Liste -> 409", s == 409)
    s, _ = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(bert.handle, bert.ident, ver + 1, [bert.dev["id"]])})
    ok("B: Geraete-Liste, die nicht den aktiven Geraeten entspricht -> 409", s == 409)
    s, me = api("b", "fb-bert", "PUT", "/api/chat/v2/cert", {"device_set": C.devices_sig_v2(bert.handle, bert.ident, ver + 1, me["device_set_want"])})
    ok("B: neue Geraete-Liste", s == 200 and me["device_set_ok"])
    e = wait(lambda: (anna.keys([bert.handle], fresh=True).get(bert.handle) or {}) if len((anna.keys([bert.handle], fresh=True).get(bert.handle) or {}).get("devices", [])) == 2 else None, 20)
    ok("A: Bert mit zwei Geraeten, signierte Liste", bool(e) and len(e["dset"]["ids"]) == 2)
    # Widerruf: B entfernt das Zweitgeraet, signiert die Liste NICHT neu -> Server meldet widerrufen; boeser Server koennte es
    # verschweigen: dann zeigt die signierte Liste (Version) das Geraet noch - der Client nimmt nur Geraete, die in der Liste stehen
    # UND nicht widerrufen sind (nie mehr Empfaenger, als die Person selbst unterschrieben hat)
    api("b", "fb-bert", "DELETE", f"/api/chat/devices/{d2['id']}")
    e2 = wait(lambda: next((x for x in (anna.keys([bert.handle], fresh=True).get(bert.handle) or {}).get("devices", []) if x["id"] == d2["id"] and x["revoked"]), None), 20)
    ok("A: entferntes Geraet gilt als widerrufen", bool(e2))
    forged = dict(e, dset=dict(e["dset"], ids=[x for x in e["dset"]["ids"] if x != bert.dev["id"]]))
    ok("Geraete-Liste ohne gueltige Signatur wird verworfen", py("a", f"""
from saga import fed_keys
e = fed_keys.check_entry({bert.handle!r}, {forged!r})
print(json.dumps(e is not None and 'dset' not in e))"""))
    me = bert.certs()


def part_requests(A, B, pa, pb, anna, bert, ch, bc):
    carl, olga = User("a", "fa-carl"), User("b", "fb-olga")
    # Carl -> Olga: Anfrage, Olga lehnt ab -> Carls Nachrichten kommen nicht mehr an (Carl erfaehrt es nicht)
    s, d = api("a", "fa-carl", "POST", "/api/chats", {"kind": "dm", "handle": olga.handle})
    cc = d.get("chat")
    ok(f"A: Carl beginnt Chat mit Olga ({s})", s == 200)
    oc = wait(lambda: olga.chat(cc["ref"]), 10)
    s, d, _ = carl.send(cc, "Hallo Olga?")
    ok("B: Olga sieht die Anfrage mit Nachricht", wait(lambda: olga.texts(oc) == ["Hallo Olga?"], 15))
    s, d = api("b", "fb-olga", "POST", f"/api/chats/{oc['id']}/request", {"action": "decline"})
    ok("B: Olga lehnt ab -> Chat verschwindet aus ihrer Liste", s == 200 and olga.chat(cc["ref"]) is None)
    ok("A: Heim kennt die Ablehnung", wait(lambda: sql("a", "SELECT state FROM chat_member WHERE chat_id=? AND uid LIKE '@%'", (cc["id"],)) == [["declined"]], 10))
    s, d, mc = carl.send(cc, "Noch da?")
    ok("A: Carl sendet weiter (merkt nichts)", s == 200)
    time.sleep(2)
    ok("B: abgelehnt -> nichts mehr zugestellt", not sql("b", "SELECT 1 FROM chat_msg WHERE id=?", (mc["id"],)))
    # Pruefer-Fund 6: stellt das Heim trotz Ablehnung weiter zu, verwirft B (nichts gespeichert, kein Ereignis, kein Vorababruf)
    fseq_b = sql("b", "SELECT fseq FROM chat WHERE ref=?", (cc["ref"],))[0][0]
    sql("a", "UPDATE chat_member SET state='ok' WHERE chat_id=? AND uid LIKE '@%'", (cc["id"],))
    s, d, mt = carl.send(cc, "Trotzdem?")
    sql("a", "UPDATE chat_member SET state='declined' WHERE chat_id=? AND uid LIKE '@%'", (cc["id"],))
    ok("A: Heim stellt (absichtlich) trotzdem zu", s == 200 and wait(lambda: not sql("a", "SELECT 1 FROM fed_outbox"), 15))
    time.sleep(1)
    ok("B: abgelehnt -> Nachricht verworfen (nicht gespeichert, Stand unveraendert, keine Meldung)",
       not sql("b", "SELECT 1 FROM chat_msg WHERE id=?", (mt["id"],)) and sql("b", "SELECT fseq FROM chat WHERE ref=?", (cc["ref"],))[0][0] == fseq_b
       and not notifs("b", "fb-olga"))
    ok("A: Anruf in abgelehnter Anfrage klingelt nicht", api("a", "fa-carl", "POST", "/api/calls", {"id": C.hexid(), "chat_id": cc["id"], "media": "audio",
                                                                                            "device": carl.dev["id"], "signals": {olga.dev["id"]: C.b64(os.urandom(80))}})[0] == 409)
    # Olga oeffnet den Chat doch noch (selbst beginnen): wieder da, Nachrichten nachgeholt
    s, d = api("b", "fb-olga", "POST", "/api/chats", {"kind": "dm", "handle": carl.handle})
    ok("B: Olga beginnt selbst -> derselbe Chat, angenommen", s == 200 and d["chat"]["ref"] == cc["ref"] and d["chat"]["state"] == "ok")
    ok("B: verpasste Nachrichten nachgeholt (auch die verworfene)", wait(lambda: olga.texts(d["chat"]) == ["Hallo Olga?", "Noch da?", "Trotzdem?"], 20))
    # Sperren je Instanz: Olga sperrt A -> neue Anfragen von A kommen nicht an
    s, d = api("b", "fb-olga", "POST", f"/api/chats/{d['chat']['id']}/request", {"action": "block", "scope": "instance"})
    ok("B: Olga sperrt den Server A", s == 200 and d.get("blocked"))
    s, d = api("b", "fb-olga", "GET", "/api/chat/blocks")
    ok("B: Sperrliste zeigt den Server", [x["target"] for x in d["blocks"]] == [HA])
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": olga.handle})
    ok("A: Anna beginnt Chat mit Olga (still gesperrt)", s == 200)
    time.sleep(1)
    ok("B: keine Anfrage bei Olga, Pruefprotokoll", olga.chat(d["chat"]["ref"]) is None and fed_log("b", "chat.blocked"))
    s, _ = api("b", "fb-olga", "POST", "/api/chats", {"kind": "dm", "handle": anna.handle})
    ok("B: selbst gesperrt -> Olga kann A nicht anschreiben (Hinweis)", s == 403)
    ok("B: entsperren", api("b", "fb-olga", "DELETE", f"/api/chat/blocks?target={HA}")[1]["deleted"] == 1)
    # Sperren je Person
    s, d = api("a", "fa-carl", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle})
    cb = wait(lambda: bert.chat(d["chat"]["ref"]), 10)
    s, d2 = api("b", "fb-bert", "POST", f"/api/chats/{cb['id']}/request", {"action": "block"})
    ok("B: Bert sperrt Carl (Person)", s == 200 and sql("b", "SELECT target FROM fed_chat_block WHERE uid='fb-bert'") == [[carl.handle]])
    # Pruefer-Fund 6 beim Heim: Anna Berg (B, Heim) beginnt mit Carl und sperrt ihn -> Carls Nachrichten verwirft das Heim
    annb = User("b", "fb-anna")
    s, d = api("b", "fb-anna", "POST", "/api/chats", {"kind": "dm", "handle": carl.handle})
    hc = d.get("chat") or {}
    s1, _, _ = annb.send(hc, "Hallo Carl")
    cch = wait(lambda: carl.chat(hc.get("ref")), 10)
    ok(f"A: Carl sieht die Anfrage von Anna B ({s}, {s1})", s == 200 and s1 == 200 and cch)
    s, _, m1 = carl.send(cch, "Hallo zurueck")
    ok(f"B (Heim): Carls Antwort kommt an ({s})", s == 200 and wait(lambda: sql("b", "SELECT 1 FROM chat_msg WHERE id=?", (m1["id"],)), 10))
    s, _ = api("b", "fb-anna", "POST", f"/api/chats/{hc['id']}/request", {"action": "block"})
    ok(f"B: Anna B sperrt Carl im eigenen Chat ({s})", s == 200)
    s, d, m2 = carl.send(cch, "Darf ich noch?")
    ok(f"A: Heim meldet Ablehnung -> nicht zugestellt ({s})", s == 409 and "declined" in d.get("error", ""))
    ok("B (Heim): verworfen, nicht gespeichert, Pruefprotokoll", not sql("b", "SELECT 1 FROM chat_msg WHERE id=?", (m2["id"],)) and fed_log("b", "chat.declined"))
    ok("A: Nachricht als nicht zugestellt markiert", sql("a", "SELECT fstate FROM chat_msg WHERE id=?", (m2["id"],)) == [["failed"]])
    # Anfrage-Rate je Absender
    return carl, olga


def part_calls(A, B, pa, pb, anna, bert, ch, bc):
    def ring_body(caller, callee_entry):
        cid = C.hexid()
        sig = {}
        for d in callee_entry["devices"]:
            if d["revoked"]:
                continue
            body = json.dumps({"t": "offer", "sdp": "v=0", "media": "audio", "fp": []})
            sig[d["id"]] = C.seal_signal_v2(cid, caller.handle, caller.dev, bert.handle, d["id"], C.unb64(d["enc_pub"], 32), body)
        return {"id": cid, "chat_id": ch["id"], "media": "audio", "device": caller.dev["id"], "signals": sig}
    e = anna.keys([bert.handle], fresh=True)[bert.handle]
    b = ring_body(anna, e)
    s, d = api("a", "fa-anna", "POST", "/api/calls", b)
    ok(f"A: Anna ruft Bert an (saga-call/v2) ({s})", s == 200 and d["call"]["state"] == "ringing" and d["call"].get("v") == 2)
    cid = b["id"]
    s, d = api("b", "fb-bert", "GET", "/api/calls/active")
    rc = next((x for x in d.get("calls", []) if x["id"] == cid), None)
    ok("B: es klingelt bei Bert", bool(rc) and rc["role"] == "callee" and rc["peer"]["handle"] == anna.handle)
    s, d = api("b", "fb-bert", "GET", f"/api/calls/{cid}?device={bert.dev['id']}")
    body, sig = C.open_signal_v2(d["signals"][0]["sealed"], bert.dev["enc_pk"], bert.dev["enc_sk"])
    ok("B: Angebot entschluesselt + Signatur (Handles) gueltig", json.loads(body)["t"] == "offer" and
       C.verify_signal_v2(cid, anna.handle, anna.dev["id"], bert.handle, bert.dev["id"], body, sig, anna.dev["sign_pk"]))
    ans = C.seal_signal_v2(cid, bert.handle, bert.dev, anna.handle, anna.dev["id"], anna.dev["enc_pk"], json.dumps({"t": "answer", "sdp": "v=0"}))
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/answer", {"device": bert.dev["id"], "signals": {anna.dev["id"]: ans}})
    ok(f"B: angenommen ({s})", s == 200 and d["call"]["state"] == "active")
    s, d = api("a", "fa-anna", "GET", f"/api/calls/{cid}?device={anna.dev['id']}")
    ok("A: aktiv, Antwort fuer Annas Geraet da", d["call"]["state"] == "active" and d["call"]["callee_device"] == bert.dev["id"]
       and any(C.open_signal_v2(x["sealed"], anna.dev["enc_pk"], anna.dev["enc_sk"])[0].find("answer") > 0 for x in d["signals"]))
    ice = C.seal_signal_v2(cid, anna.handle, anna.dev, bert.handle, bert.dev["id"], bert.dev["enc_pk"], json.dumps({"t": "ice", "c": []}))
    s, d = api("a", "fa-anna", "POST", f"/api/calls/{cid}/signal", {"device": anna.dev["id"], "signals": {bert.dev["id"]: ice}})
    ok("A -> B: ICE-Signal", s == 200)
    s, d = api("b", "fb-bert", "GET", f"/api/calls/{cid}?device={bert.dev['id']}&after=0")
    ok("B: Signal angekommen", len(d["signals"]) >= 2)
    ice2 = C.seal_signal_v2(cid, bert.handle, bert.dev, anna.handle, anna.dev["id"], anna.dev["enc_pk"], json.dumps({"t": "ice", "c": []}))
    ok("B -> A: ICE-Signal", api("b", "fb-bert", "POST", f"/api/calls/{cid}/signal", {"device": bert.dev["id"], "signals": {anna.dev["id"]: ice2}})[0] == 200)
    ok("Lebenszeichen", api("b", "fb-bert", "POST", f"/api/calls/{cid}/alive", {})[1]["state"] == "active")
    ok("A: Anna legt auf", api("a", "fa-anna", "POST", f"/api/calls/{cid}/end", {"reason": "hangup", "connected": True})[0] == 200)
    ok("B: Anruf auch dort beendet + Eintrag im Chat", wait(lambda: api("b", "fb-bert", "GET", f"/api/calls/{cid}")[1]["call"]["state"] == "ended", 10)
       and wait(lambda: any(m["kind"] == "call" for m in bert.msgs(bc["id"])), 10))
    # Bert ruft an, Anna lehnt ab -> beide beendet, bei Bert "abgelehnt"
    ea = bert.keys([anna.handle], fresh=True)[anna.handle]
    cid2 = C.hexid()
    sig = {d["id"]: C.seal_signal_v2(cid2, bert.handle, bert.dev, anna.handle, d["id"], C.unb64(d["enc_pub"], 32), json.dumps({"t": "offer"}))
           for d in ea["devices"] if not d["revoked"]}
    s, d = api("b", "fb-bert", "POST", "/api/calls", {"id": cid2, "chat_id": bc["id"], "media": "video", "device": bert.dev["id"], "signals": sig})
    ok(f"B: Bert ruft Anna an (Gegenrichtung) ({s})", s == 200)
    ok("A: Anna lehnt ab", wait(lambda: api("a", "fa-anna", "POST", f"/api/calls/{cid2}/decline", {})[0] == 200, 5))
    ok("B: abgelehnt", wait(lambda: api("b", "fb-bert", "GET", f"/api/calls/{cid2}")[1]["call"]["reason"] == "declined", 10))
    # falsche Geraete -> 409 mit Geraeten
    s, d = api("a", "fa-anna", "POST", "/api/calls", {"id": C.hexid(), "chat_id": ch["id"], "media": "audio", "device": anna.dev["id"],
                                                     "signals": {C.hexid(): C.b64(os.urandom(90))}})
    ok(f"A: Anruf an falsche Geraete -> 409 mit Geraeten ({s})", s == 409 and isinstance(d.get("devices"), list))
    # gefaelschtes Ende von B fuer einen fremden Anruf
    s, d = signed_post("a", own_priv("b"), "b", "/fed/v2/call", {"op": "end", "id": C.hexid(), "actor": bert.handle})
    ok("A: Ende fuer unbekannten Anruf -> folgenlos", s == 200)
    # laufender Anruf + Trennen
    b3 = ring_body(anna, anna.keys([bert.handle], fresh=True)[bert.handle])
    s, d = api("a", "fa-anna", "POST", "/api/calls", b3)
    return b3["id"]


def part_gone(A, B, pa, pb, anna, bert, ch, bc, carl, olga, ringing):
    # Kontoloeschung: Olga (B) wird geloescht -> der Chat mit Carl verschwindet bei A
    oc = olga.chats()
    sql("b", "INSERT INTO fed_chat_block (uid, target, created) VALUES ('fb-olga', ?, 1) ON CONFLICT DO NOTHING", (HA,))
    py("b", "import app\napp.delete_user_data('fb-olga')\nfrom db import connect\nwith connect() as c, c:\n    c.execute(\"DELETE FROM users WHERE username='fb-olga'\")\nprint(json.dumps(True))")
    refs = [x["ref"] for x in oc if x.get("ref")]
    ok("A: Chats mit der geloeschten Person beendet", wait(lambda: not any(carl.chat(r) for r in refs), 20))
    ok("B: Sperrliste der geloeschten Person mit weg", sql("b", "SELECT COUNT(*) FROM fed_chat_block WHERE uid='fb-olga'") == [[0]])
    # Chat fuer alle loeschen (vom Teilnehmer aus)
    ab = User("b", "fb-anna2")
    s, d = api("a", "fa-carl", "POST", "/api/chats", {"kind": "dm", "handle": ab.handle})
    cb = d["chat"]
    s, d = api("b", "fb-anna2", "DELETE", f"/api/chats/{wait(lambda: ab.chat(cb['ref']), 10)['id']}?scope=all")
    ok(f"B: Chat mit Carl fuer alle geloescht ({s})", s == 200 and d.get("removed"))
    ok("A: auch beim Heim weg", wait(lambda: carl.chat(cb["ref"]) is None, 15))
    # Trennen: Chats schreibgeschuetzt, laufender Anruf endet
    _, html = B.post({"action": "disconnect", "id": str(pb)})
    ok("A: getrennt -> Chat schreibgeschuetzt (lesbar)", wait(lambda: (anna.chat(ch["ref"]) or {}).get("readonly"), 15))
    ok("B: Chat schreibgeschuetzt, Verlauf noch da", (bert.chat(bc["ref"]) or {}).get("readonly") and len(bert.texts(bc)) > 3)
    s, d, _ = bert.send(bert.chat(bc["ref"]), "nach dem Trennen")
    ok(f"B: Senden -> 403 mit Hinweis ({s})", s == 403 and "schreibgeschützt" in d.get("error", ""))
    ok("A: laufender Anruf beendet", wait(lambda: api("a", "fa-anna", "GET", f"/api/calls/{ringing}")[1]["call"]["state"] == "ended", 10))
    # Pruefer: wieder verbunden (hier per Datenbank) -> neuer Chat moeglich, der alte bleibt schreibgeschuetzt lesbar
    sql("a", "UPDATE fed_peer SET status='active'")
    sql("b", "UPDATE fed_peer SET status='active'")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle})
    nc = d.get("chat") or {}
    ok(f"A: nach neuem Verbinden neuer Chat statt des schreibgeschuetzten ({s})", s == 200 and nc.get("ref") not in (None, ch["ref"])
       and not nc.get("readonly") and (anna.chat(ch["ref"]) or {}).get("readonly"))


def part_compat(A, B, pa, pb):
    ok("A: Gegenstelle ohne chat2", wait(lambda: sql("a", "SELECT caps_at FROM fed_peer")[0][0] is not None, 20)
       and "chat2" not in sql("a", "SELECT caps FROM fed_peer")[0][0])
    anna = User("a", "fa-anna")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": "~abcdefghijklmnop@" + HB})
    ok(f"A -> alte Instanz: Chat nicht moeglich, verstaendlicher Hinweis ({s})", s == 403 and HB in d.get("error", ""))
    s, d = api("a", "fa-anna", "GET", "/api/chat/people?q=bert")
    ok("A: Chat-Suche ohne Fehler", s == 200)
    ok("A: Anruf-Konfiguration unveraendert", api("a", "fa-anna", "GET", "/api/calls/config")[0] == 200)


def run():
    try:
        A, B, pa, pb = F2.main()
        if COMPAT:
            part_compat(A, B, pa, pb)
        else:
            anna, bert, ch, bc = part_chat(A, B, pa, pb)
            part_robust(A, B, pa, pb, anna, bert, ch, bc)
            part_more(A, B, pa, pb, anna, bert, ch, bc)
            carl, olga = part_requests(A, B, pa, pb, anna, bert, ch, bc)
            ringing = part_calls(A, B, pa, pb, anna, bert, ch, bc)
            part_gone(A, B, pa, pb, anna, bert, ch, bc, carl, olga, ringing)
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\nFOEDERATION-CHAT {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
