"""Foederation 2.3 E8: "boeser Server" - drei eigene Saga-Instanzen A, B, C (eigene Datenbanken und Ports). A ist mit B und C
verbunden. C verhaelt sich protokollwidrig: zuerst als echte Saga-Instanz, die mit ihrem echten Schluessel gefaelschte Auftraege
und Ereignisse schickt, danach ersetzt durch einen nachgebauten Server mit demselben Schluessel, der falsch, zu gross, zu langsam
oder mit gefaelschten Daten antwortet. B ist Zuschauer: was C tut, darf B nicht aufhalten.

Geprueft (Erwartung: abgewiesen bzw. sichtbar gemacht, keine Serverfehler, keine Blockade anderer Instanzen):
- falsche Signatur, Replay, veraenderter Koerper; gefaelschte Absender (eigene Konten von A, Personen von B), C gibt sich als Heim
  eines Chats von A aus; Absender-Signatur falsch; Wiederholung mit anderer Signatur; Umsortieren (alte fseq), Unterdruecken
  (fseq-Luecke -> beim Heim nachgefragt), Geister-Mitglieder (sichtbar mit Herkunft aus dem Handle), Schluesseltausch: neue
  Identitaet sichtbar (TOFU in den Apps), Schluessel einer Person von B ueber C -> nur der Abruf bei B zaehlt;
- Flut: Nachrichten vom Heim (Rate je Instanz, 'retry' statt Verwerfen), Mitgliederlisten, Namensaufloesung (resolve), Hochladen
  (Grenze je fremder Person), Personensuche (WebFinger), Ereignis-Sendungen (429 signiert mit Retry-After) - B laeuft weiter;
- Zugriff mit negativen IDs ausserhalb der Freigabe, ID-Umrechnungs-Tricks, Rueckfall auf Protokoll 1 (sichtbar, Wartendes
  geht nicht verloren), Adressbuch-Spoofing (fremde Namen/Hosts, Trennpunkte), Opt-out und Trennen entfernen Namen;
- nachgebauter Server: zu grosse und falsch signierte Antworten, ID-Tricks in "Mit mir geteilt", gefaelschtes Adressbuch,
  langsame Antworten (Slowloris gegen den Express-Pool und den Arbeiter) - B wird trotzdem sofort beliefert;
- Sperren in Gruppen trifft die einladende Person (nicht irgendein anderes Mitglied); Zwischenkopien fremder Anhaenge sind
  je Instanz begrenzt (zuletzt benutzte bleiben, Rest wird bei Bedarf neu geholt).
Kompatibilitaet: FED_B_APP=<Saga 2.1/2.2> -> B spricht Protokoll 1; geprueft wird dann Teilen/Lesen statt Chat mit B.
Raeumt auf (Datenbanken geloescht)."""
import http.server, json, os, re, secrets, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)
import foederation_gruppen as FG  # noqa: E402  (connect, sys_lines)
import chatlib as C  # noqa: E402
from saga import fedsig  # noqa: E402

T.I["a"].update(port=8595, admin=8594, db="saga_fba", url="http://127.0.0.1:8595", host="127.0.0.1:8595", state="/tmp/fedboese-a",
                allow="localhost:8597, localhost:8599")
T.I["b"].update(port=8597, admin=8596, db="saga_fbb", url="http://localhost:8597", host="localhost:8597", state="/tmp/fedboese-b", allow="127.0.0.1:8595")
T.I["c"] = dict(T.I["b"], port=8599, admin=8598, db="saga_fbc", url="http://localhost:8599", host="localhost:8599", state="/tmp/fedboese-c",
                allow="127.0.0.1:8595", secret=secrets.token_hex(32))
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
ok, hreq, wait, jd, sql = T.ok, T.hreq, T.wait, T.jd, T.sql
api, User = FC.api, FC.User
COMPAT = F2.COMPAT
_env_f2 = F2.env_of


def env_of(k):
    e = _env_f2(k)
    e.update(ATTACH_MAX_MB="1", FED_CHAT_CACHE_MB="1")              # Zwischenkopien: Grenze je Instanz ~2 MB (2 x Anhang-Hoechstgroesse)
    if k == "a":
        e.update(FED_RATE="300", FED_CHAT_IN_RATE="60", FED_UPLOAD_QUOTA_MB="1", FED_PEER_SLOTS="2", FED_EXPRESS_WORKERS="4")
    return e


F2.env_of = env_of


def log_of(k, ev):
    return [r[0] for r in sql(k, "SELECT detail FROM fed_log WHERE event=? ORDER BY id", (ev,))]


def pid_of(k, host):
    return sql(k, "SELECT id FROM fed_peer WHERE host=?", (host,))[0][0]


def as_c(m, path, query="", body=None, actor=None, tamper=None, priv=None):
    """Anfrage an A, signiert mit dem Schluessel von C -> (status, kopfzeilen, json/bytes, nonce)."""
    raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
    s, h, d, nonce, _ = T.signed("a", priv or PRIV["c"], T.I["c"]["url"], m, path, query, raw, actor=actor, tamper=tamper)
    return s, h, jd(d), nonce


def events_c(evs):
    s, h, d, _ = as_c("POST", "/fed/v2/events", body={"events": evs})
    return s, d


def ev(typ, **kw):
    return dict(kw, type=typ, id=C.hexid())


def msg_fields(m):
    return {k: m[k] for k in ("id", "ts", "sender_handle", "sender_device", "body_ct", "sig", "reply_to", "ai")}


PRIV = {}


class ResetUser(User):
    """App, die fuer ein Konto mit schon eingerichteten Chats neu beginnt (neue Identitaet, reset)."""

    def __init__(self, k, uid):
        self.k, self.uid = k, uid
        self.ident, self.dev = C.identity(), C.device()
        s, d = api(k, uid, "PUT", "/api/chat/identity", dict(C.setup_body(uid, self.ident, self.dev, ops=1, mem=8 << 20), reset=True,
                                                                         password="Fed-User-Pass-123"))
        ok(f"{k.upper()}: {uid} beginnt neu (neue Identitaet, {s})", s == 200)
        self.certs()


def sync_home(x):
    """C als Heim hat die gefaelschten Nummern "verbraucht": eigene Zaehlung angleichen (sonst gelten echte Nachrichten
    danach bei A als umsortiert)."""
    f = sql("a", "SELECT fseq FROM chat WHERE id=?", (x.ga["id"],))[0][0]
    sql("c", "UPDATE chat SET fseq=GREATEST(fseq, ?) WHERE ref=?", (f, x.gc["ref"]))


def main():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna"), ("fa-carl", "Carl Ohne", None),
                                                              ("fa-lisa", "Lisa Lokal", None)], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert")], groups=[("fbteam", ["fb-bert"])]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("fc-dora", "Dora Dritt", "dora"), ("fc-eve", "Bert Gast", "bert"),
                                                              ("fc-mal", "Bert Gast · " + HB, None), ("fc-otto", "Otto Opt", "otto"),
                                                              ("fc-gus", "Gus Zweit", None)], groups=[]))
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    FG.connect("a", "b", adm["a"], adm["b"])
    FG.connect("a", "c", adm["a"], adm["c"])
    for k in ("a", "b", "c"):
        PRIV[k] = FC.own_priv(k)
    ok("Faehigkeiten ausgehandelt (A-C chat2, A-B " + ("Protokoll 1" if COMPAT else "chat2") + ")",
       wait(lambda: FG.caps_ok("a", HC, "chat2") and FG.caps_ok("c", HA, "chat2") and (COMPAT or FG.caps_ok("a", HB, "chat2")), 30))
    return adm


# =====================================================================================================
class Ctx:
    pass


def setup(adm):
    x = Ctx()
    x.pb, x.pc = pid_of("a", HB), pid_of("a", HC)
    x.anna, x.carl, x.lisa = User("a", "fa-anna"), User("a", "fa-carl"), User("a", "fa-lisa")
    x.dora, x.eve, x.gus = User("c", "fc-dora"), User("c", "fc-eve"), User("c", "fc-gus")
    x.bert = None if COMPAT else User("b", "fb-bert")
    # Direkt-Chat Anna (A, Heim) <-> Dora (C)
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": x.dora.handle, "device": x.anna.dev["id"]})
    x.ad = d.get("chat") or {}
    dc = wait(lambda: x.dora.chat(x.ad.get("ref")), 15)
    api("c", "fc-dora", "POST", f"/api/chats/{dc['id']}/request", {"action": "accept"})
    s, d, _ = x.anna.send(x.ad, "Hallo Dora")
    ok(f"Direkt-Chat A <-> C ({s})", s == 200 and wait(lambda: "Hallo Dora" in x.dora.texts(x.dora.chat(x.ad["ref"])), 15))
    # Direkt-Chat Anna <-> Bert (Zuschauer)
    if not COMPAT:
        s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": x.bert.handle, "device": x.anna.dev["id"]})
        x.ab = d.get("chat") or {}
        bc = wait(lambda: x.bert.chat(x.ab.get("ref")), 15)
        api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
        s, d, _ = x.anna.send(x.ab, "Hallo Bert")
        ok("Direkt-Chat A <-> B", wait(lambda: "Hallo Bert" in x.bert.texts(x.bert.chat(x.ab["ref"])), 15))
    # Gruppe mit Heim C (Dora), Mitglieder Anna und Carl (A)
    s, d = api("c", "fc-dora", "POST", "/api/chats", {"kind": "free", "handles": [x.anna.handle, x.carl.handle], "device": x.dora.dev["id"]})
    x.gc = d.get("chat") or {}
    ok(f"C: Gruppe mit Heim C ({s})", s == 200 and x.gc.get("home_self"))
    ga = wait(lambda: x.anna.chat(x.gc.get("ref")), 15)
    api("a", "fa-anna", "POST", f"/api/chats/{ga['id']}/request", {"action": "accept"})
    s, d, _ = x.dora.send(x.gc, "Gruppe von C")
    ok("A: Nachricht der Gruppe mit Heim C angekommen", wait(lambda: "Gruppe von C" in x.anna.texts(x.anna.chat(x.gc["ref"])), 15))
    x.ga = x.anna.chat(x.gc["ref"])
    # Seiten: "Geheim" nur fuer Bert (B), "Offen" fuer Dora (C, bearbeiten); Dora teilt "Von Dora" mit Anna
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Geheim", "body": T.blocks(T.P("nur fuer B", "g1"))})
    x.geheim = d["id"]
    s1, _ = api("a", "fa-anna", "PUT", f"/api/notes/{x.geheim}/shares", {"type": "remote", "target": "@bert@" + HB, "role": "view"})
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Offen", "body": T.blocks(T.P("fuer C", "o1"))})
    x.offen = d["id"]
    s2, _ = api("a", "fa-anna", "PUT", f"/api/notes/{x.offen}/shares", {"type": "remote", "target": "@dora@" + HC, "role": "edit"})
    s, d = api("c", "fc-dora", "POST", "/api/notes", {"title": "Von Dora", "body": T.blocks(T.P("von C", "d1"))})
    x.vondora = d["id"]
    s3, _ = api("c", "fc-dora", "PUT", f"/api/notes/{x.vondora}/shares", {"type": "remote", "target": "@anna@" + HA, "role": "view"})
    ok(f"Seiten geteilt ({s1}, {s2}, {s3})", (s1, s2, s3) == (200, 200, 200) and wait(lambda: sql("b", "SELECT 1 FROM fed_inbound")
                                                                                     and sql("a", "SELECT 1 FROM fed_inbound"), 15))
    x.rgeheim_b = -(x.geheim * 1024 + pid_of("b", HA))
    return x


def bystander(x, label, within=8.0):
    """B darf nicht aufgehalten werden: Bert liest die mit ihm geteilte Seite (B -> A) und - mit Protokoll 2 - erreicht ihn
    eine neue Nachricht von Anna in kurzer Zeit."""
    t0 = time.time()
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{x.rgeheim_b}")
    good, why = s == 200 and d.get("title") == "Geheim", f"Seite {s}"
    if not COMPAT and good:
        txt = "Zuschauer " + secrets.token_hex(3)
        s2, d2, _ = x.anna.send(x.ab, txt)
        good, why = s2 == 200 and bool(wait(lambda: txt in x.bert.texts(x.bert.chat(x.ab["ref"])), within, 0.3)), f"Chat {s2} {str(d2)[:80]}"
    dt = time.time() - t0
    ok(f"{label}: B unbeeintraechtigt ({dt:.1f} s, {why})", good and dt < within + 2)


# =====================================================================================================
def part_sig(x):
    s, h, d, _ = as_c("GET", "/fed/v1/status", priv=fedsig.new_key()[0])
    ok(f"falscher Schluessel (keyid von C) -> 401 + Pruefprotokoll ({s})", s == 401 and wait(lambda: log_of("a", "sig.rejected"), 3))
    s, h, d, nonce, hdr = T.signed("a", PRIV["c"], T.I["c"]["url"], "GET", "/fed/v1/status")
    s2 = hreq("a", "GET", "/fed/v1/status", hdr=dict(hdr))[0]
    ok(f"Replay (gleiche Signatur) -> 401 + Pruefprotokoll ({s}, {s2})", s == 200 and s2 == 401 and log_of("a", "sig.replay"))
    body = json.dumps({"events": []}).encode()
    hdr, _ = fedsig.sign_request(PRIV["c"], T.I["c"]["url"], "POST", HA, "/fed/v2/events", "", body)
    s3 = hreq("a", "POST", "/fed/v2/events", raw=json.dumps({"events": [ev("x")]}).encode(), hdr=dict(hdr, **{"Content-Type": "application/json"}))[0]
    ok(f"veraenderter Koerper (Digest) -> 401 ({s3})", s3 == 401)
    s, h, d, _ = as_c("GET", "/fed/v1/status", actor={"name": "ADMIN", "groups": []})
    ok(f"ungueltiger Ausloeser (Anmeldename-Form) -> 400 ({s})", s == 400)


def part_forge(x):
    anna, dora, carl = x.anna, x.dora, x.carl
    ents = anna.keys([anna.handle, dora.handle])
    # --- Heim A: C schickt eine Nachricht "von Anna" bzw. "von Bert" ---------------------------------------------------------
    fake = anna.msg(x.ad, "Ich bin Anna (nicht)")
    body = {"op": "send", "ref": x.ad["ref"], "msg": msg_fields(fake), "keys": fake["keys"], "files": [], "sender_keys": ents[anna.handle]}
    s, h, d, _ = as_c("POST", "/fed/v2/chat", body=body)
    ok(f"Heim A: Absender = Konto von A -> 403 + Pruefprotokoll ({s})", s == 403 and log_of("a", "chat.forged"))
    bh = x.bert.handle if x.bert else "~" + "b" * 16 + "@" + HB
    s, h, d, _ = as_c("POST", "/fed/v2/chat", body=dict(body, msg=dict(body["msg"], sender_handle=bh)))
    ok(f"Heim A: Absender = Person von B -> abgewiesen ({s})", s in (400, 403))
    good = dora.msg(x.ad, "echt")
    bad = dict(msg_fields(good), body_ct=C.b64(os.urandom(64)))
    s, h, d, _ = as_c("POST", "/fed/v2/chat", body={"op": "send", "ref": x.ad["ref"], "msg": bad, "keys": good["keys"], "files": [],
                                                    "sender_keys": ents[dora.handle]})
    ok(f"Heim A: Signatur passt nicht zum Inhalt -> 403 ({s})", s == 403)
    n0 = len(anna.msgs(x.ad["id"]))
    # --- C gibt sich als Heim eines Chats von A aus -------------------------------------------------------------------------
    s, d = events_c([ev("chat.msg", ref=x.ad["ref"], fseq=99, msg=msg_fields(good), keys={})])
    ok(f"Ereignis fuer Chat mit Heim A -> ignoriert ({d})", s == 200 and d["results"] == ["ignored"])
    s, d = events_c([ev("chat.members", ref=x.ad["ref"], kind="free", members=[{"handle": dora.handle}])])
    ok(f"Mitgliederliste fuer Chat mit Heim A -> ignoriert + Pruefprotokoll ({d})", d["results"] in (["ok"], ["ignored"])
       and any("not home" in z for z in log_of("a", "chat.forged")))
    ok("A: Direkt-Chat unveraendert", len(anna.msgs(x.ad["id"])) == n0)
    # --- Gruppe mit Heim C: gefaelschter Absender, Signatur, Wiederholung, Reihenfolge -----------------------------------------
    cur = lambda: sql("a", "SELECT fseq FROM chat WHERE id=?", (x.ga["id"],))[0][0]  # noqa: E731
    f0 = cur()
    m = dora.msg(x.gc, "als Carl")
    as_carl = dict(msg_fields(m), sender_handle=carl.handle)
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=f0 + 1, msg=as_carl, keys=m["keys"], files=[])])
    ok(f"Heim C: Nachricht 'von Carl' (Konto von A, Geraet von Dora) -> ignoriert ({d['results']})", d["results"] == ["ignored"]
       and (log_of("a", "chat.badsig") or log_of("a", "chat.forged")))
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=cur() + 1, msg=dict(msg_fields(m), sender_handle=bh), keys=m["keys"], files=[])])
    ok(f"Heim C: Absender ohne Mitgliedschaft -> ignoriert ({d['results']})", d["results"] == ["ignored"])
    sync_home(x)
    texts0 = anna.texts(x.ga)
    s, d, mreal = dora.send(x.gc, "echte Nachricht")
    ok("A: echte Nachricht von C angekommen", wait(lambda: anna.texts(x.ga)[-1:] == ["echte Nachricht"], 15))
    other = dora.msg(x.gc, "anderer Inhalt", mid=mreal["id"])
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=cur() + 1, msg=msg_fields(other), keys=other["keys"], files=[])])
    ok(f"Wiederholung: gleiche Kennung, andere Signatur -> ignoriert + Pruefprotokoll ({d['results']})", d["results"] == ["ignored"]
       and log_of("a", "chat.replay"))
    nmsg = len(anna.msgs(x.ga["id"]))
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=cur() + 1, msg=msg_fields(mreal), keys=mreal["keys"], files=[])])
    ok(f"Wiederholung derselben Nachricht -> keine Doppelung ({d['results']})", len(anna.msgs(x.ga["id"])) == nmsg)
    old = dora.msg(x.gc, "alt nachgeschoben")
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=max(1, cur() - 1), msg=msg_fields(old), keys=old["keys"], files=[])])
    ok(f"Umsortieren (alte fseq) -> ignoriert + Pruefprotokoll ({d['results']})", d["results"] == ["ignored"] and log_of("a", "chat.reorder"))
    skip = dora.msg(x.gc, "nach unterdrueckter")
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=cur() + 2, msg=msg_fields(skip), keys=skip["keys"], files=[])])
    ok(f"Unterdruecken (fseq-Luecke) -> 'retry', Luecke beim Heim nachgefragt ({d['results']})", d["results"] == ["retry"]
       and wait(lambda: log_of("a", "chat.gap"), 10))
    sync_home(x)
    ok("A: keine der gefaelschten Nachrichten im Verlauf", [t_ for t_ in anna.texts(x.ga) if t_ not in texts0] == ["echte Nachricht"])
    # --- Geister-Mitglieder ---------------------------------------------------------------------------------------------------
    ghost_b, ghost_x = "~" + "g" * 16 + "@" + HB, "~" + "h" * 16 + "@evil.example"
    mem = [{"handle": dora.handle, "name": "Dora Dritt", "role": "admin"}, {"handle": anna.handle}, {"handle": carl.handle},
           {"handle": ghost_b, "name": "Bert Gast"}, {"handle": ghost_x, "name": "Geist · " + HA}]
    s, d = events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem,
                        sys={"added": [{"handle": ghost_b, "name": "Bert Gast", "host": HA}], "by": {"handle": dora.handle, "name": "Dora"}})])
    ch = api("a", "fa-anna", "GET", f"/api/chats/{x.ga['id']}")[1].get("chat") or {}
    hosts = {mm.get("handle"): mm.get("host") for mm in ch.get("members", [])}
    ok(f"Geister-Mitglieder sichtbar mit Herkunft aus dem Handle ({d['results']}, {sorted(hosts.values())})", d["results"] == ["ok"]
       and hosts.get(ghost_b) == HB and hosts.get(ghost_x) == "evil.example")
    names = {mm.get("handle"): mm.get("name") for mm in ch.get("members", [])}
    ok(f"Name eines Geists ohne vorgetaeuschte Herkunft ({names.get(ghost_x)!r})", "·" not in (names.get(ghost_x) or "·"))
    sysl = FG.sys_lines(anna, x.ga["id"])
    ok("Systemzeile nennt die Herkunft aus dem Handle (nicht die Angabe des Heims)",
       any(p_.get("handle") == ghost_b and p_.get("host") == HB for z in sysl for p_ in z.get("added", [])))
    # --- Schluessel einer Person von B ueber C (A ist mit B verbunden) --------------------------------------------------------
    if not COMPAT:
        sql("a", "DELETE FROM fed_chat_keys WHERE handle=?", (x.bert.handle,))
        mem2 = mem[:3] + [{"handle": x.bert.handle, "name": "Bert Gast"}]
        events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem2)])
        fi, fd = C.identity(), C.device()
        fake_e = {"v": 2, "handle": x.bert.handle, "ident_pub": C.b64(fi["ident_pk"]), "backup_pub": C.b64(fi["backup_pk"]),
                  "sig": C.b64(C.handle_sig(x.bert.handle, fi)),
                  "devices": [{"id": fd["id"], "enc_pub": C.b64(fd["enc_pk"]), "sign_pub": C.b64(fd["sign_pk"]),
                               "fed_sig": C.b64(C.device_sig_v2(x.bert.handle, fi, fd))}]}
        rc = C.recipients_v2([e for e in anna.keys([anna.handle, carl.handle]).values() if e])
        fm = C.encrypt_v2(x.gc["ref"], x.bert.handle, fd, rc, json.dumps({"v": 1, "type": "text", "text": "falscher Bert"}))
        s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=cur() + 1, msg=msg_fields(fm), keys=fm["keys"], files=[], sender_keys=fake_e)])
        ok(f"Heim C liefert eigene Schluessel fuer Bert (B): nur der Abruf bei B zaehlt -> abgewiesen ({d['results']})",
           d["results"] == ["ignored"] and "falscher Bert" not in anna.texts(x.ga) and log_of("a", "chat.badsig"))
        e_b = sql("a", "SELECT data FROM fed_chat_keys WHERE handle=?", (x.bert.handle,))
        ok("A: Schluessel von Bert stammen von B (nicht von C)", bool(e_b) and json.loads(e_b[0][0])["ident_pub"] == C.b64(x.bert.ident["ident_pk"]))
        sync_home(x)
    events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem[:3])])          # Geister wieder raus
    # --- Schluesseltausch von Dora (C erzeugt eine neue Identitaet): sichtbar fuer TOFU in den Apps --------------------------------
    old_ip = anna.keys([dora.handle], fresh=True)[dora.handle]["ident_pub"]
    nd = ResetUser("c", "fc-dora")
    new = wait(lambda: (anna.keys([dora.handle], fresh=True).get(dora.handle) or {}).get("ident_pub") != old_ip, 15)
    e_new = anna.keys([dora.handle]).get(dora.handle) or {}
    ok("Schluesseltausch: neue Identitaet an A geliefert (die Apps warnen per TOFU), Zertifikat stimmt",
       bool(new) and e_new.get("ident_pub") == C.b64(nd.ident["ident_pk"]))
    ok("A: Live-Ereignis chat.keys.changed bzw. veralteter Zwischenspeicher fuer Dora",
       bool(sql("a", "SELECT 1 FROM fed_chat_keys WHERE handle=?", (dora.handle,))))
    x.dora = nd


def part_ids(x):
    act = {"name": "dora", "display": "Dora", "groups": []}
    s, h, d, _ = as_c("GET", f"/fed/v1/notes/{x.geheim}", actor=act)
    ok(f"negative ID ausserhalb der Freigabe: Seite fuer B -> 404 ({s})", s == 404)
    s, h, d, _ = as_c("GET", f"/fed/v1/notes/{x.geheim}", actor={"name": "bert", "display": "Bert", "groups": ["fbteam"]})
    ok(f"Name und Gruppe einer Person von B vorgetaeuscht -> 404 ({s})", s == 404)
    for path in (f"/fed/v1/notes/{x.geheim}/comments", f"/fed/v2/notes/{x.geheim}/versions", f"/fed/v2/notes/{x.geheim}",
                 f"/fed/v2/notes/{x.geheim}/rows", f"/fed/v2/notes/{x.geheim}/export"):
        s, h, d, _ = as_c("GET", path, actor=act)
        ok(f"{path.split('/', 3)[-1]} ausserhalb der Freigabe -> 4xx ({s})", 400 <= s < 500)
    s, h, d, _ = as_c("POST", f"/fed/v2/notes/{x.geheim}/children", body={"title": "eingeschleust"}, actor=act)
    ok(f"Unterseite unter fremder Seite -> 4xx ({s})", 400 <= s < 500)
    for nid in (0, -1, 2 ** 63, 2 ** 64 + x.offen):
        s, h, d, _ = as_c("GET", f"/fed/v1/notes/{nid}", actor=act)
        ok(f"ID {nid} -> 4xx ({s})", 400 <= s < 500)
    s, h, d, _ = as_c("GET", f"/fed/v1/notes/{x.offen}", actor=act)
    ok(f"freigegebene Seite fuer Dora -> 200 ({s})", s == 200)
    # Umrechnung beim Heim (Anna auf A): negative IDs, die keine gueltige Instanz/ID ergeben
    for nid in (-1, -1024, -(5 * 1024), -(2 ** 43 * 1024 + x.pc), -999999999999999999, -(x.vondora * 1024 + 1023)):
        s, d = api("a", "fa-anna", "GET", f"/api/notes/{nid}")
        ok(f"Heim: /api/notes/{nid} -> 4xx ({s})", 400 <= s < 500)
    s, d = api("a", "fa-anna", "GET", f"/api/notes/{-(x.vondora * 1024 + x.pc)}")
    ok(f"Heim: gueltige negative ID -> 200 ({s})", s == 200)
    s, d = api("a", "fa-anna", "GET", "/api/fed/files/-1/" + "0" * 24)
    ok(f"Heim: Datei mit unsinniger ID -> 4xx ({s})", 400 <= s < 500)


def part_rename(x):
    """2.5 (W02/W03): person.renamed und actor.handle - C kann nur eigene Personen umbenennen bzw. zusammenfuehren, nie
    Personen von A oder B; ein Name, der hier schon einer anderen Person von C gehoert, wird nicht uebernommen."""
    q = lambda *a: [tuple(r) for r in sql(*a)]  # noqa: E731
    bh = x.bert.handle.split("@")[0] if x.bert else "~" + "b" * 16
    dh = x.dora.handle.split("@")[0]
    shares = lambda nid: q("a", "SELECT target, role FROM note_share WHERE note_id=? AND target_type='remote'", (nid,))  # noqa: E731
    g0, o0 = shares(x.geheim), shares(x.offen)
    s, d = api("c", "fc-dora", "GET", "/api/fed/shared")
    ok(f"C: Dora fragt echt an -> A kennt Handle und Namen ({s})", s == 200 and wait(
        lambda: q("a", "SELECT principal FROM fed_alias WHERE peer_id=? AND handle=?", (x.pc, dh)) == [(f"@dora@{HC}",)], 10))
    s, d = events_c([ev("person.renamed", old="bert", new="mallory", handle=bh)])
    ok(f"C benennt 'bert' um: trifft nur Personen von C ({d.get('results')})", s == 200 and shares(x.geheim) == g0
       and not q("a", "SELECT 1 FROM note_share WHERE target LIKE '@mallory@%'") and not q("a", "SELECT 1 FROM fed_alias WHERE peer_id=? AND principal LIKE ?", (x.pb, "@mallory@%")))
    bad = [("bert@" + HB, "x1"), ("bert", "Bad Name"), ("bert", "~short"), ("bert", "a" * 40), ("bert", "bert"), ("bert", "~" + "z" * 16),
           ("anna@" + HA, "anna"), ("bert", "dora@" + HA)]
    s, d = events_c([ev("person.renamed", old=o, new=n, handle=bh) for o, n in bad] + [ev("person.renamed", old="bert", new="neu", handle="bert"),
                                                                                         ev("person.renamed", old="bert", new="neu")])
    ok(f"ungueltige Namen/Handles -> ignoriert ({d.get('results')})", s == 200 and set(d.get("results") or []) == {"ignored"})
    hx = "~" + "x" * 16
    s, d = events_c([ev("person.renamed", old="otto", new="dora", handle=hx), ev("person.renamed", old="dora", new="dorax", handle=hx)])
    ok(f"Name einer anderen Person von C uebernehmen -> ignoriert + Pruefprotokoll ({d.get('results')})",
       d.get("results") == ["ignored", "ignored"] and shares(x.offen) == o0 and log_of("a", "person.conflict"))
    s, h, d, _ = as_c("GET", "/fed/v1/shared", actor={"name": "dora", "display": "Dora", "groups": [], "handle": hx})
    ok(f"actor.handle: Doras Namen mit fremdem Handle -> nicht zusammengefuehrt ({s})", s == 200 and shares(x.offen) == o0
       and not q("a", "SELECT 1 FROM fed_alias WHERE peer_id=? AND handle=?", (x.pc, hx)))
    s, h, d, _ = as_c("GET", "/fed/v1/shared", actor={"name": "eve", "display": "Eve", "groups": [], "handle": bh})
    ok(f"actor.handle: Handle einer Person von B ueber C -> nur unter C ({s})", s == 200 and shares(x.geheim) == g0
       and q("a", "SELECT principal FROM fed_alias WHERE peer_id=? AND handle=?", (x.pb, bh)) in ([], [(f"@bert@{HB}",)]))
    s, h, d, _ = as_c("GET", "/fed/v1/shared", actor={"name": "~" + "y" * 16, "display": "Y", "groups": [], "handle": hx})
    ok(f"actor.handle: Name ist ein anderer Handle -> nicht zusammengefuehrt ({s})",
       not q("a", "SELECT 1 FROM fed_alias WHERE peer_id=? AND principal=?", (x.pc, "@~" + "y" * 16 + "@" + HC)))
    # echtes Umbenennen auf C bleibt moeglich (und wieder zurueck)
    for name in ("dorab", "dora"):
        s, d = api("c", "fc-dora", "PUT", "/api/account/public-name", {"name": name})
        ok(f"C: Dora heisst {name} -> Freigabe bei A folgt ({s})", s == 200 and wait(lambda: shares(x.offen) == [(f"@{name}@{HC}", o0[0][1])], 15))
    bystander(x, "person.renamed")


def part_flood(x):
    # --- Nachrichten vom Heim: Rate je Instanz ('retry' statt Verwerfen) --------------------------------------------------------
    res = []
    for _ in range(4):
        s, d = events_c([ev("chat.msg", ref="f" * 32 + "@" + HC, fseq=1, msg={}) for _ in range(20)])
        res += d.get("results", []) if isinstance(d, dict) else []
    ok(f"Flut chat.msg: nach der Rate 'retry' statt verworfen ({res.count('ignored')}x ignored, {res.count('retry')}x retry)",
       res.count("retry") >= 15 and res.count("ignored") <= 60 and any("msg in" in z for z in log_of("a", "chat.rate")))
    res = []
    for _ in range(16):
        s, d = events_c([ev("chat.members", ref="e" * 32 + "@" + HC, kind="free", members=[]) for _ in range(20)])
        res += d.get("results", []) if isinstance(d, dict) else []
    ok(f"Flut chat.members: Rate je Instanz ({res.count('retry')}x retry)", res.count("retry") >= 15 and any("members in" in z for z in log_of("a", "chat.rate")))
    codes = [as_c("POST", "/fed/v2/chat", body={"op": "resolve", "names": ["anna"], "groups": []})[0] for _ in range(62)]
    ok(f"Flut resolve: Rate je Instanz ({codes.count(429)}x 429)", 429 in codes and 429 not in codes[:59] and any("resolve" in z for z in log_of("a", "chat.rate")))
    # --- Hochladen: Grenze je fremder Person ---------------------------------------------------------------------------------------
    up = []
    for i in range(3):
        s, h, d, _ = as_c("POST", f"/fed/v2/notes/{x.offen}/files", f"name=f{i}.bin", body=os.urandom(450 * 1024),
                          actor={"name": "dora", "display": "Dora", "groups": []})
        up.append(s)
    ok(f"Flut Hochladen: Grenze je fremder Person ({up})", up[0] == 200 and up[-1] == 507)
    # --- Personensuche (WebFinger) und Ereignis-Sendungen -------------------------------------------------------------------------
    wf = [as_c("GET", "/.well-known/webfinger", "resource=" + "acct%3Ax" + secrets.token_hex(3) + "%40" + HA.replace(":", "%3A"))[0] for _ in range(125)]
    ok(f"Flut WebFinger: 429 nach der Rate ({wf.count(429)}x)", 429 in wf and 429 not in wf[:100])
    bystander(x, "waehrend der Flut")
    last = None
    for _ in range(130):
        last = as_c("POST", "/fed/v2/events", body={"events": []})
        if last[0] == 429:
            break
    s, h, d, nonce = last
    ok(f"Flut Ereignisse: 429 signiert mit Retry-After ({s}, Retry-After {h.get('retry-after')})", s == 429 and h.get("saga-nonce") == nonce
       and bool(h.get("signature")) and 0 < int(h.get("retry-after") or 0) <= 61)
    ok("A antwortet den eigenen Konten weiter", api("a", "fa-anna", "GET", "/api/chats")[0] == 200)
    bystander(x, "nach der Flut")
    # Gegenstelle bremst: der Ausgangskorb fuer sie zerfaellt nicht in Einzelversuche, sondern wartet je Instanz
    r = F2.py("c", f"""
import time
from db import connect
from saga import federation as F
with connect() as c, c:
    p = F.peer_by(c, 'host', {HA!r})
    ids = [F._enqueue(c, p['id'], {{'type': 'test.noop', 'n': i}}, express=False) for i in range(30)]
    c.execute("UPDATE fed_outbox SET next_try=? WHERE id IN (" + ",".join(map(str, ids)) + ")", (time.time() + 3600,))
    rows = c.execute("SELECT id, peer_id, payload, attempts, created, chain FROM fed_outbox WHERE id IN (" + ",".join(map(str, ids)) + ") ORDER BY id").fetchall()
calls = []
_orig = F._post_events_r
F._post_events_r = lambda p_, evs, timeout=F.TIMEOUT: (calls.append(len(evs)), _orig(p_, evs, timeout))[1]
c0 = time.time()
n = F._deliver_peer(p['id'], rows, c0)
with connect() as c:
    st = c.execute("SELECT attempts, next_try, last_error FROM fed_outbox WHERE id IN (" + ",".join(map(str, ids)) + ")").fetchall()
    c.execute("DELETE FROM fed_outbox WHERE id IN (" + ",".join(map(str, ids)) + ")")
    c.commit()
print(json.dumps([n, len(st), max(r[0] for r in st), min(r[1] for r in st) - c0, calls, F.backoff_until(p['id']) > c0]))""")
    ok(f"C bei 429 von A: eine Anfrage, dann wartet alles fuer A (Retry-After) - keine Einzelversuche, keine Fehlversuche ({r})",
       r[0] == 0 and r[1] == 30 and r[2] == 0 and r[3] >= 1 and r[4] == [20] and r[5])


def part_dir(x):
    """Adressbuch-Spoofing und Opt-out (echter Server C)."""
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=bert%20gast")
    hits = d.get("people") or []
    by = {(h_["host"], h_["handle"].split("@")[1]) for h_ in hits}
    ok(f"Suche 'Bert Gast': Herkunft immer aus dem Handle ({[h_['label'] for h_ in hits]})", hits and all(a == b for a, b in by)
       and {h_["host"] for h_ in hits} >= ({HC} if COMPAT else {HB, HC}))
    ok("Namen anderer Server ohne vorgetaeuschte Herkunft (kein Trennpunkt im Namen)", all("·" not in h_["name"] for h_ in hits)
       and all(h_["label"].count("·") <= 2 for h_ in hits))
    tg = {h_["target"] for h_ in hits}
    ok("gleicher oeffentlicher Name auf C ist eine andere Person als auf B", ("@bert@" + HC) in tg and (COMPAT or ("@bert@" + HB) in tg))
    # Opt-out: Otto (C) will nicht mehr im Adressbuch stehen -> Name bei A weg (kam nur aus dem Adressbuch)
    ok("A: Otto im Adressbuch (Name gespeichert)", wait(lambda: sql("a", "SELECT 1 FROM fed_person WHERE principal=?", ("@otto@" + HC,)), 15))
    s, d = api("c", "fc-otto", "PUT", "/api/account/federation", {"directory": False})
    ok(f"C: Otto schaltet Adressbuch ab ({s})", s == 200)
    ok("A: Otto aus Adressbuch und Namensliste entfernt (Opt-out)", wait(lambda: not sql("a", "SELECT 1 FROM fed_directory WHERE pub='otto'")
                                                                     and not sql("a", "SELECT 1 FROM fed_person WHERE principal=?", ("@otto@" + HC,)), 30))
    s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=otto")
    ok("A: Otto nicht mehr auffindbar", d.get("people") == [])


def part_downgrade(x):
    s, h, d, _ = as_c("POST", "/fed/v2/hello", body={"protocol": 1, "protocols": [1], "capabilities": []})
    ok(f"C meldet Protokoll 1 (Rueckfall) -> sichtbar im Pruefprotokoll ({s})", s == 200 and wait(lambda: log_of("a", "peer.downgrade"), 5)
       and sql("a", "SELECT protocol FROM fed_peer WHERE id=?", (x.pc,))[0][0] == 1)
    s, d = events_c([ev("chat.msg", ref=x.gc["ref"], fseq=1, msg={})])
    ok(f"Chat-Ereignis nach dem Rueckfall -> abgewiesen ({s})", s in (200, 404) and (s != 200 or d["results"] in (["ignored"],)))
    s, h, d, _ = as_c("POST", "/fed/v1/events", body={"events": [ev("chat.msg", ref=x.gc["ref"], fseq=1, msg={})]})
    ok(f"Chat-Ereignis ueber Protokoll 1 -> ignoriert ({d})", s == 200 and d.get("results") == ["ignored"])
    s, d, mq = x.anna.send(x.ad, "nach dem Rueckfall")
    ok(f"A: Senden an C nach dem Rueckfall - kein Serverfehler ({s})", s < 500)
    s, d = api("a", "fa-anna", "GET", "/api/chats")
    ok("A: Chatliste weiter erreichbar", s == 200)
    caps = F2.py("c", "from saga import federation as F\nprint(json.dumps(F.CAPS + F.CAPS2))")
    s, h, d, _ = as_c("POST", "/fed/v2/hello", body={"protocol": 2, "protocols": [1, 2], "capabilities": caps})
    ok("C meldet wieder Protokoll 2", s == 200 and wait(lambda: FG.caps_ok("a", HC, "chat2"), 10))
    sql("a", "UPDATE fed_outbox SET next_try=0")
    s, d, mq2 = x.anna.send(x.ad, "wieder da")
    ok("C: Nachrichten nach dem Rueckfall kommen an (nichts still verloren)",
       wait(lambda: "wieder da" in x.dora.texts(x.dora.chat(x.ad["ref"])), 40))


def part_block(x):
    """Sperren in Gruppen trifft die einladende Person: Gruppe mit Heim A von Anna mit Eve und Gus (C); Anna geht, die neue
    Admin-Person laedt Lisa (A) ein; Lisa sperrt."""
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "handles": [x.eve.handle, x.gus.handle], "device": x.anna.dev["id"]})
    g = d.get("chat") or {}
    for u in (x.eve, x.gus):
        cc = wait(lambda: u.chat(g.get("ref")), 15)
        api("c", u.uid, "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
    s, d = api("a", "fa-anna", "DELETE", f"/api/chats/{g['id']}/members/fa-anna")
    adm_uid = wait(lambda: (sql("a", "SELECT uid FROM chat_member WHERE chat_id=? AND role='admin' AND left_at IS NULL", (g["id"],)) or [[None]])[0][0], 10)
    admin = x.eve if adm_uid and adm_uid.endswith(x.eve.handle.split("@")[0] + "@" + HC) else x.gus
    other = x.gus if admin is x.eve else x.eve
    ok(f"A: Anna verlaesst ihre Gruppe, Admin wird eine Person von C ({s}, {adm_uid})", s == 200 and bool(adm_uid))
    cc = wait(lambda: admin.chat(g["ref"]), 10)
    ok("C: Admin-Rolle angekommen", wait(lambda: (admin.chat(g["ref"]) or {}).get("role") == "admin", 15))
    s, d = api("c", admin.uid, "POST", f"/api/chats/{cc['id']}/members", {"handle": x.lisa.handle})
    ok(f"C: Admin laedt Lisa (A) ein ({s})", s == 200)
    lc = wait(lambda: x.lisa.chat(g["ref"]), 15)
    ok("A: Lisa hat eine Anfrage, eingeladen von der Admin-Person (nicht der Erstellerin)", bool(lc) and lc.get("request")
       and (lc.get("invited_by") or {}).get("host") == HC and (lc.get("invited_by") or {}).get("remote") is True)
    s, d = api("a", "fa-lisa", "POST", f"/api/chats/{lc['id']}/request", {"action": "block", "scope": "person"})
    blk = [r[0] for r in sql("a", "SELECT target FROM fed_chat_block WHERE uid='fa-lisa'")]
    ok(f"Sperren trifft genau die einladende Person ({s}, {blk})", s == 200 and blk == [admin.handle] and other.handle not in blk)
    # Eingeladen von einer Person desselben Servers: nur ablehnen (kein Sperren einer fremden Person)
    s, d = api("a", "fa-carl", "GET", "/api/chats")
    ok("A: Carl (eingeladen von Dora, C) - Einladung nennt Dora", any((c_.get("invited_by") or {}).get("name") == "Dora Dritt" for c_ in d.get("chats", [])))
    # Das Heim nennt als Einladende eine Person von A, die gar nicht (angenommenes) Mitglied ist (Carl: nur Anfrage) - damit
    # liesse sich das Sperren verhindern: zaehlt nicht, es bleibt die Erstellerin (Dora). Mit Anna (Mitglied) gilt es.
    mem = [{"handle": x.dora.handle, "name": "Dora Dritt", "role": "admin"}, {"handle": x.anna.handle}, {"handle": x.carl.handle}]
    inv_of = lambda: (sql("a", "SELECT i.by_uid FROM chat_invite i JOIN chat ch ON ch.id=i.chat_id WHERE ch.ref=? AND i.uid='fa-lisa'",
                          (x.gc["ref"],)) or [[None]])[0][0]
    s, d = events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem + [{"handle": x.lisa.handle}],
                        sys={"added": [{"handle": x.lisa.handle}], "by": {"handle": x.carl.handle, "name": "Carl Ohne"}})])
    lg = wait(lambda: x.lisa.chat(x.gc["ref"]), 10) or {}
    ok(f"Heim nennt Nicht-Mitglied von A als Einladende -> zaehlt nicht, Sperren bleibt moeglich ({d.get('results')}, {inv_of()})",
       d.get("results") == ["ok"] and (inv_of() or "").startswith("@") and (lg.get("invited_by") or {}).get("remote") is True)
    events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem)])
    s, d = events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem + [{"handle": x.lisa.handle}],
                        sys={"added": [{"handle": x.lisa.handle}], "by": {"handle": x.anna.handle, "name": "Anna Berg"}})])
    ok(f"eingeladen von Anna (Mitglied, A) -> nur ablehnen ({inv_of()})", d.get("results") == ["ok"] and inv_of() == "fa-anna")
    events_c([ev("chat.members", ref=x.gc["ref"], kind="free", members=mem)])


def part_cache(x):
    """Zwischenkopien fremder Anhaenge: Grenze je Instanz (FED_CHAT_CACHE_MB, mindestens 2 Anhaenge), zuletzt benutzte bleiben."""
    dc = x.dora.chat(x.ad["ref"])
    fids = []
    for i in range(3):
        blob = C.encrypt_file_v2(x.ad["ref"], os.urandom(900 * 1024))[0]
        s, h, raw = FC.raw("c", "fc-dora", "POST", f"/api/chats/{dc['id']}/files", blob)
        fid = (jd(raw) or {}).get("id") if s == 200 else None
        if fid:
            x.dora.send(dc, f"Anhang {i}", files=[{"id": fid}])
            fids.append(fid)
    ok(f"C: drei Anhaenge (je ~0.9 MB) gesendet ({len(fids)})", len(fids) == 3)
    got = []
    for fid in fids:
        wait(lambda: sql("a", "SELECT 1 FROM chat_file WHERE id=?", (fid,)), 15)
        s, h, data = hreq("a", "GET", f"/api/chat/files/{fid}", hdr={"X-Saga": "1", "X-Saga-Chat": "2"}, cookie=T.ck("a", "fa-anna"))
        got.append((s, len(data)))
        time.sleep(1.1)
    cached = [r[0] for r in sql("a", "SELECT id FROM chat_file_fed WHERE cached=1")]
    ok(f"A: alle Anhaenge abrufbar ({[g_[0] for g_ in got]})", all(g_[0] == 200 and g_[1] > 900 * 1024 for g_ in got))
    ok(f"A: Zwischenkopien je Instanz begrenzt - der am laengsten unbenutzte ist weg ({len(cached)} von 3)",
       fids[0] not in cached and fids[2] in cached and log_of("a", "chat.cache"))
    s, h, data = hreq("a", "GET", f"/api/chat/files/{fids[0]}", hdr={"X-Saga": "1", "X-Saga-Chat": "2"}, cookie=T.ck("a", "fa-anna"))
    ok(f"A: entfernte Zwischenkopie wird bei Bedarf neu geholt ({s})", s == 200 and len(data) > 900 * 1024)


# =====================================================================================================
# Nachgebauter boeser Server C (gleicher Schluessel, gleicher Port)
# =====================================================================================================
class Evil(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass                                             # (A bricht zu grosse/langsame Antworten ab: Verbindung zurueckgesetzt)


MODE = {"slow": set(), "shared": None, "directory": None, "badsig": set(), "hits": []}


class EvilHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _nonce(self):
        m = re.search(r'nonce="([A-Za-z0-9_-]{16,64})"', self.headers.get("Signature-Input") or "")
        return m.group(1) if m else "x" * 24

    def _reply(self, status, data, slow=False, badsig=False):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        h = fedsig.sign_response(PRIV["c"] if not badsig else fedsig.new_key()[0], T.I["c"]["url"], status, body, self._nonce())
        self.send_response(status)
        for k, v in h.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if slow:                                    # Slowloris: ein Byte je halbe Sekunde
                for i in range(len(body)):
                    self.wfile.write(body[i:i + 1])
                    self.wfile.flush()
                    time.sleep(0.5)
            else:
                self.wfile.write(body)
        except OSError:
            pass

    def _route(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        path = self.path.split("?")[0]
        MODE["hits"].append(path)
        slow = any(path.startswith(p) for p in MODE["slow"])
        bad = any(path.startswith(p) for p in MODE["badsig"])
        if path == "/fed/v1/shared" and MODE["shared"] is not None:
            return self._reply(200, MODE["shared"], slow, bad)
        if path == "/fed/v2/directory" and MODE["directory"] is not None:
            return self._reply(200, MODE["directory"], slow, bad)
        if path in ("/fed/v2/events", "/fed/v1/events"):
            try:
                evs = json.loads(raw).get("events") or []
            except ValueError:
                evs = []
            return self._reply(200, {"results": ["ok"] * len(evs)}, slow, bad)
        if path == "/fed/v2/chat/msgs":
            return self._reply(200, {"items": [], "more": False, "top": 0}, slow, bad)
        if path == "/fed/v2/hello":
            return self._reply(200, {"protocol": 2, "protocols": [1, 2], "capabilities": MODE.get("caps") or []}, slow, bad)
        return self._reply(404, {"error": "not found"}, slow, bad)

    do_GET = do_POST = do_PATCH = do_DELETE = _route


def part_evil(x):
    MODE["caps"] = F2.py("c", "from saga import federation as F\nprint(json.dumps(F.CAPS + F.CAPS2))")
    T.procs["c"].terminate()
    T.procs["c"].wait(10)
    srv = Evil(("127.0.0.1", T.I["c"]["port"]), EvilHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # --- zu grosse Antwort ------------------------------------------------------------------------------------------------
        MODE["shared"] = [{"id": i, "title": "x" * 1000} for i in range(1, 6000)]
        t0 = time.time()
        s, d = api("a", "fa-anna", "GET", "/api/fed/shared")
        ok(f"zu grosse Antwort (6 MB) -> verworfen, C als nicht erreichbar gemeldet ({s}, {d.get('unreachable')}, {time.time() - t0:.1f} s)",
           s == 200 and HC in (d.get("unreachable") or []) and time.time() - t0 < 15)
        # --- ID-Tricks in "Mit mir geteilt" ------------------------------------------------------------------------------------
        MODE["shared"] = [{"id": v, "title": "T", "parent_id": pv} for v, pv in
                          ((-5, None), (0, None), (2 ** 60, None), ("12", None), (1.5, None), (True, None), (None, None), (7, -3), (8, 2 ** 62))]
        s, d = api("a", "fa-anna", "GET", "/api/fed/shared")
        ids = sorted(i["id"] for i in d.get("items", []) if (i.get("remote") or {}).get("host") == HC)
        ok(f"ID-Tricks: nur gueltige IDs, auf C umgerechnet ({ids})", s == 200 and ids == sorted([-(7 * 1024 + x.pc), -(8 * 1024 + x.pc)])
           and all(i.get("parent_id") is None for i in d.get("items", []) if (i.get("remote") or {}).get("host") == HC))
        # --- falsch signierte Antwort ------------------------------------------------------------------------------------------
        MODE["badsig"] = {"/fed/v1/notes", "/fed/v2/notes"}
        s, d = api("a", "fa-anna", "GET", f"/api/notes/{-(7 * 1024 + x.pc)}")
        ok(f"falsch signierte Antwort -> 'nicht erreichbar' statt Inhalt ({s})", s == 502)
        # --- gefaelschtes Adressbuch -------------------------------------------------------------------------------------------
        hx = lambda ch_: "~" + ch_ * 16  # noqa: E731
        MODE["directory"] = {"full": True, "more": False, "next": 5, "cfg": "x", "enabled": True, "entries": [
            {"handle": hx("a"), "name": "Anna Berg", "pub": "anna"}, {"handle": hx("b"), "name": "Bert Gast · " + HB},
            {"handle": "bert", "name": "kein Handle"}, {"handle": "~KURZ", "name": "x"}, {"handle": hx("c"), "name": "‮evil‬ Name"},
            {"handle": hx("d"), "name": "y" * 5000}, {"handle": hx("e"), "name": "Pub@Fremd", "pub": "x@" + HB}, {"handle": hx("f")}]}
        events_c([ev("directory.changed", full=True)])
        ok("gefaelschtes Adressbuch geholt", wait(lambda: sql("a", "SELECT count(*) FROM fed_directory WHERE peer_id=? AND handle=?", (x.pc, hx("a")))[0][0] == 1, 15))
        rows = sql("a", "SELECT handle, name, pub FROM fed_directory WHERE peer_id=?", (x.pc,))
        ok(f"nur gueltige Handles, Namen bereinigt ({len(rows)} Eintraege)", all(re.fullmatch(r"~[a-z2-7]{16}", r[0]) for r in rows)
           and all("‮" not in r[1] and "·" not in r[1] and len(r[1]) <= 120 for r in rows) and not any(r[2] and "@" in r[2] for r in rows))
        s, d = api("a", "fa-anna", "GET", "/api/fed/people?q=anna%20berg")
        lab = [(h_["host"], h_["target"]) for h_ in d.get("people", [])]
        ok(f"'Anna Berg' von C erscheint mit Herkunft C, nicht als Anna von A ({lab})", lab == [(HC, "@anna@" + HC)])
        # --- langsame Antworten (Slowloris) gegen Express-Pool und Arbeiter ------------------------------------------------------
        MODE["slow"] = {"/fed/v2/events", "/fed/v2/chat/msgs", "/fed/v2/directory", "/fed/v1/events"}
        MODE["hits"].clear()
        cur = sql("a", "SELECT fseq FROM chat WHERE id=?", (x.ga["id"],))[0][0]
        evs = []
        for i in range(6):                     # (die Luecke wird vor der Signatur erkannt - der Inhalt ist hier egal)
            junk = {"id": C.hexid(), "ts": int(time.time() * 1000), "sender_handle": x.dora.handle, "sender_device": x.dora.dev["id"],
                    "body_ct": C.b64(os.urandom(40)), "sig": C.b64(os.urandom(64)), "reply_to": None, "ai": None}
            evs.append(ev("chat.msg", ref=x.gc["ref"], fseq=cur + 2 + i, msg=junk, keys={"backup:" + x.anna.handle: C.b64(os.urandom(80))},
                          files=[]))
        s, d = events_c(evs)
        events_c([ev("directory.changed", full=True)])
        for i in range(4):
            x.anna.send(x.ad, f"an den langsamen Server {i}")
        ok(f"Luecken gemeldet, Abrufe beim langsamen Server laufen ({d.get('results')}, "
           f"{sql('a', 'SELECT protocol, caps, status, opts FROM fed_peer WHERE id=?', (x.pc,))}, {cur})",
           s == 200 and d.get("results") == ["retry"] * 6 and wait(lambda: MODE["hits"], 10))
        time.sleep(1.5)
        bystander(x, "waehrend C langsam antwortet", within=8.0)
        t0 = time.time()
        s, d, _ = x.anna.send(x.ad, "noch eine an C")
        ok(f"Senden an den langsamen Server blockiert die Person nicht ({s}, {time.time() - t0:.1f} s)", s == 200 and time.time() - t0 < 8)
        ok("A: Startseite/Chatliste antworten", T._health("a") and api("a", "fa-anna", "GET", "/api/chats")[0] == 200)
        MODE["slow"] = set()
    finally:
        srv.shutdown()
        srv.server_close()


def part_disconnect(x, adm):
    n_before = sql("a", "SELECT count(*) FROM fed_person WHERE peer_id=?", (x.pc,))[0][0]
    _, html = adm["a"].post({"action": "disconnect", "id": str(x.pc)})
    ok(f"A: Verbindung zu C getrennt ({T.flash(html)[:30]})", "getrennt" in T.flash(html))
    rest = [r[0] for r in sql("a", "SELECT principal FROM fed_person WHERE peer_id=?", (x.pc,))]
    ok(f"A: Namen von C nur noch, wo sie hier vorkommen ({n_before} -> {len(rest)})", len(rest) < n_before
       and ("@" + x.dora.handle) in rest and not sql("a", "SELECT 1 FROM fed_directory WHERE peer_id=?", (x.pc,)))
    if not COMPAT:                                      # Zwischenkopien von C lassen sich nicht mehr neu holen: sie bleiben
        r = F2.py("a", "import os\nfrom db import connect\nfrom saga import chat, fed_chat\n"
                       "q = lambda: [r[0] for r in connect().__enter__().execute('SELECT id FROM chat_file_fed WHERE cached=1').fetchall()]\n"
                       "ids = q()\nfor i in ids: os.utime(os.path.join(chat.CHAT_DIR, i), (1, 1))\n"
                       "fed_chat._st['t'] = 0\nfed_chat.periodic()\nn = fed_chat.cache_trim(" + str(x.pc) + ")\nleft = q()\n"
                       "print(json.dumps([len(ids), len(left), n, all(os.path.isfile(os.path.join(chat.CHAT_DIR, i)) for i in left)]))")
        ok(f"A: Zwischenkopien von Anhaengen des getrennten Servers bleiben (kein Ablauf, keine Grenze) {r}",
           r and r[0] >= 1 and r[1] == r[0] and r[2] == 0 and r[3])
    ok("A: Direkt-Chat mit Dora schreibgeschuetzt, Verlauf lesbar mit Namen", api("a", "fa-anna", "GET", f"/api/chats/{x.ad['id']}")[1]["chat"].get("readonly")
       and x.anna.texts(x.ad)[:1] == ["Hallo Dora"])
    bystander(x, "nach dem Trennen von C")


def run():
    try:
        adm = main()
        x = setup(adm)
        part_sig(x)
        part_ids(x)
        part_rename(x)
        part_forge(x)
        part_block(x)
        part_dir(x)
        if not COMPAT:
            part_cache(x)
        part_downgrade(x)
        time.sleep(62)                                   # Zeitfenster der Raten von C bei A neu
        part_flood(x)
        time.sleep(62)
        part_evil(x)
        part_disconnect(x, adm)
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
    print(f"\nFOEDERATION-BOESE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
