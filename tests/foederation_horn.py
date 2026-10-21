"""Foederation 2.5 Gjallarhorn P9/P10: Gruppenanrufe ueber Knoten mit zwei eigenen Saga-Instanzen A (Heim) und B, jeweils
mit einem gekoppelten Knoten (gjallarhorn node, Registereintrag + pair.json wie nach "Erweitern", echter Steuerkanal).
Apps sind In-Process-Clients (tests/foederation_chat.py); Medien laufen hier nicht (dafuer tests/browser_fed_horn.js).

Geprueft:
- Faehigkeit horn: B schaltet das Flag erst nach dem Verbinden ein, der Admin-Knopf "Faehigkeiten jetzt abgleichen"
  (Seite Anrufe) macht horn sofort auf beiden Seiten bekannt.
- Fall a (B hat einen eigenen Knoten, A teilt nicht): Anrufstart auf A ueber Knoten (A fragt B nach bereiten Knoten),
  Bert (B) bekommt nur Kandidaten auf dem Knoten von B, waehlt ihn; A traegt 'f:<B>:<name>' ein und baut die Kaskade:
  Auftrag horn_cascade an B, B gibt seinem Knoten das dial-Token, Knoten B waehlt Knoten A an (TLS, beide pruefen ein
  Saga-Token, Knotenschluessel ueber die Kanalbindung) - Bruecke an beiden Knoten "up".
- Fall b (B ohne bereiten Knoten, A teilt): Bert bekommt Kandidaten auf dem Knoten von A mit Token von A und Kontingent
  (q, 10 Teilnehmende, 20000 kbit), Wahl wird bei A eingetragen. Kontingent (Teilnehmende = 1) erschoepft: Ben bekommt
  keine Kandidaten, klare Meldung. Erlaubt-Liste aus: kein Teilen. Notaus: Knoten A meldet das Kontingent als aus, keine
  Kaskade/Token mehr.
- Fall c (B ohne Knoten, A teilt nicht): Mesh-Rueckfall mit klarer Meldung (call.horn_mesh) auf A und B.
Kompatibilitaet: FED_B_APP=<Saga 2.4> -> B meldet kein horn, Gruppenanruf bleibt still im Mesh (kein Hinweis), keine
Serverfehler. Raeumt auf (Datenbanken, Knoten-Ordner)."""
import base64, json, os, re, secrets, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)
import chatlib as C  # noqa: E402
from nacl.signing import SigningKey  # noqa: E402

APP = T.APP
sys.path.insert(0, APP)
from version import VERSION  # noqa: E402

T.I["a"].update(port=8695, admin=8694, db="saga_fha", url="http://127.0.0.1:8695", host="127.0.0.1:8695", state="/tmp/fedhorn-a",
                allow="localhost:8697")
T.I["b"].update(port=8697, admin=8696, db="saga_fhb", url="http://localhost:8697", host="localhost:8697", state="/tmp/fedhorn-b",
                allow="127.0.0.1:8695, 127.0.0.1:8481")   # 8481: Knoten von A (Kaskade - interne Adresse nur mit Freigabe)
ok, hreq, wait, jd, sql = T.ok, T.hreq, T.wait, T.jd, T.sql
api, User = FC.api, FC.User
OLD = bool(os.environ.get("FED_B_APP"))
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
ICE = os.environ.get("HORN_TEST_ICE", "127.0.0.1")
STATE = tempfile.mkdtemp(prefix="fedhorn-")
NODES = {}                               # name -> {"proc", "dir", "id", "k", "port", "log"}
PERF = {}


def calls_cfg(k, horn=True, **kw):
    cfg = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
           "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": horn, "horn_builtin": False, "horn_from": 3}
    cfg.update(kw)
    F2.py(k, f"import auth\nauth.save_setting('calls', {cfg!r})\nprint(1)")


def connect(k1, k2, A1, A2):
    _, html = A1.post({"action": "invite"})
    code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
    fp = jd(hreq(k1, "GET", "/.well-known/saga")[2])["fingerprint"]
    A2.post({"action": "preview", "url": T.I[k1]["url"], "code": code})
    A2.post({"action": "connect", "url": T.I[k1]["url"], "code": code, "fingerprint": fp})
    p1 = sql(k1, "SELECT id FROM fed_peer WHERE host=?", (T.I[k2]["host"],))[0][0]
    _, html = A1.post({"action": "confirm", "id": str(p1)})
    ok(f"{k1.upper()} <-> {k2.upper()} verbunden ({T.flash(html)[:30]})", "bestätigt" in T.flash(html))


def calls_post(adm, form):
    f = dict(form)
    f["csrf"] = adm.csrf("/calls")
    return adm.req("POST", "/calls", f)


def calls_state(adm):
    s, _, d = adm.req("GET", "/api/calls/state")
    return json.loads(d) if s == 200 else {}


def node_of(adm, name):
    return next((n for n in calls_state(adm).get("nodes", []) if n["name"] == name), {})


def has_cap(k, host, cap):
    r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
    return bool(r) and cap in json.loads(r[0][0] or "[]")


def gobuild():
    out = os.path.join(STATE, "gjallarhorn")
    r = subprocess.run(["go", "build", "-trimpath", "-ldflags", f"-X main.version={VERSION}", "-o", out, "."], cwd=os.path.join(APP, "gjallarhorn"),
                       capture_output=True, text=True, env=dict(os.environ, CGO_ENABLED="0"))
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
        return None
    return out


def node_start(k, name, port, share=False, binary=None):
    """Knoten fuer Instanz k wie nach dem Koppeln: Schluessel, pair.json, Registereintrag; dann gjallarhorn node."""
    d = os.path.join(STATE, name)
    os.makedirs(d, exist_ok=True)
    seed = secrets.token_bytes(32)
    with open(os.path.join(d, "node.key"), "w") as f:
        f.write(base64.b64encode(seed).decode() + "\n")
    os.chmod(os.path.join(d, "node.key"), 0o600)
    pub = base64.b64encode(bytes(SigningKey(seed).verify_key)).decode()
    sp = F2.py(k, "from saga import horn\nprint(json.dumps(horn.saga_pub()))")
    nid = secrets.token_hex(8)
    url = f"https://127.0.0.1:{port}"
    F2.py(k, f"""
import time
from db import connect
with connect() as c, c:
    c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, share, version, state, created, port) VALUES (?,?,?,0,?,0,?,?,'neu',?,?)",
              ({nid!r}, {name!r}, {pub!r}, {url!r}, {bool(share)!r}, {VERSION!r}, time.time(), {port}))
print(1)""")
    base = T.I[k]["url"]
    with open(os.path.join(d, "pair.json"), "w") as f:
        json.dump({"node_id": nid, "saga_pub": sp, "ctl_url": base.replace("http://", "ws://") + "/api/horn/ctl", "name": name, "saga": base,
                   "at": "test"}, f)
    env = {x: v for x, v in os.environ.items() if not x.startswith(("HORN_", "SAGA_")) and x not in ("PYTHONPATH",)}
    env.update(HORN_MODE="node", HORN_SAGA=base, HORN_NAME=name, HORN_LISTEN=f":{port}", HORN_PUBLIC_URL=url, HORN_TLS_SELF="1", HORN_UDP="0",
               HORN_ICE_IPS=ICE, HORN_DATA=d, HORN_MAX_LEGS="48", HORN_MAX_PER_IP="48")
    log = open(os.path.join(d, "node.log"), "a")
    p = subprocess.Popen([binary or BIN[0], "node"], env=env, stdout=log, stderr=subprocess.STDOUT)
    NODES[name] = {"proc": p, "dir": d, "id": nid, "k": k, "port": port, "log": log, "pub": pub}
    return nid


def node_stop_all():
    for n in NODES.values():
        if n["proc"].poll() is None:
            n["proc"].terminate()
    for n in NODES.values():
        try:
            n["proc"].wait(10)
        except subprocess.TimeoutExpired:
            n["proc"].kill()
        n["log"].close()


def node_log(name):
    try:
        return open(os.path.join(NODES[name]["dir"], "node.log")).read()
    except OSError:
        return ""


def payload(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def cands(d):
    return ((d.get("call") or {}).get("horn") or d.get("horn") or {}).get("candidates") or []


USERS_A = [("fa-anna", "Anna Berg", "anna"), ("fa-ada", "Ada Heim", "ada")]
USERS_B = [("fb-bert", "Bert Gast", "bert"), ("fb-ben", "Ben Gast", "ben")]


def setup(horn_b=False):
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=USERS_A, groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=USERS_B, groups=[]))
    calls_cfg("a")
    calls_cfg("b", horn=horn_b)                        # B schaltet horn erst nach dem Verbinden ein (Abgleich)
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    connect("a", "b", adm["a"], adm["b"])
    return adm


BIN = [None]


def main():
    rc = 1
    try:
        BIN[0] = gobuild()
        ok("gjallarhorn gebaut", bool(BIN[0]))
        adm = setup()
        HA, HB = T.I["a"]["host"], T.I["b"]["host"]
        # --- Faehigkeit horn und Abgleich ---------------------------------------------------------------------------
        ok("vor dem Abgleich: A kennt horn bei B nicht", not has_cap("a", HB, "horn"))
        if not OLD:
            calls_cfg("b")
            time.sleep(6)                              # Einstellungen 5 s zwischengespeichert
            s, _, html = calls_post(adm["b"], {"action": "caps"})
            ok(f"B: Knopf \"Faehigkeiten jetzt abgleichen\" ({s})", s in (302, 303))
            ok("horn auf beiden Seiten bekannt (sofort, ohne 6 h zu warten)", wait(lambda: has_cap("a", HB, "horn") and has_cap("b", HA, "horn"), 15))
        else:
            calls_post(adm["a"], {"action": "caps"})
            time.sleep(3)
            ok("2.4: B meldet kein horn", not has_cap("a", HB, "horn"))
        # --- Knoten -------------------------------------------------------------------------------------------------
        node_start("a", "horn-a.test", 8481, share=False)
        if not OLD:
            node_start("b", "horn-b.test", 8482)
        ok("Knoten A bereit (Steuerkanal, Zertifikat, Version)", wait(lambda: node_of(adm["a"], "horn-a.test").get("status") == "ready", 30))
        if not OLD:
            ok("Knoten B bereit", wait(lambda: node_of(adm["b"], "horn-b.test").get("status") == "ready", 30))
        # --- Personen und Gruppe (Heim A) ---------------------------------------------------------------------------
        anna, ada = User("a", "fa-anna"), User("a", "fa-ada")
        bert, ben = User("b", "fb-bert"), User("b", "fb-ben")
        s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": ["fa-ada"], "handles": [bert.handle, ben.handle],
                                                          "device": anna.dev["id"]})
        ch = d.get("chat") or {}
        ok(f"A: Gruppe ueber zwei Server ({s}, v{ch.get('v')})", s == 200 and ch.get("v") == 2)
        bc = wait(lambda: bert.chat(ch["ref"]), 15)
        nc = wait(lambda: ben.chat(ch["ref"]), 15)
        for u, cc in ((bert, bc), (ben, nc)):
            api(u.k, u.uid, "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
        for u in (anna, ada, bert, ben):
            api(u.k, u.uid, "PUT", "/api/calls/caps", {"device": u.dev["id"], "caps": CAPS})
        if OLD:
            part_old(anna, bert, ch, bc)
        else:
            part_a(adm, anna, bert, ch, bc)
            part_b(adm, anna, bert, ben, ch, bc, nc)
            part_c(adm, anna, bert, ch, bc)
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        node_stop_all()
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        for name in NODES:
            lg = node_log(name)
            if "panic" in lg:
                ok(f"Knoten {name}: kein panic", False)
                print(lg[-3000:])
        for k in T.I:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        shutil.rmtree(STATE, ignore_errors=True)
    print("PERF " + json.dumps(PERF))
    print(f"\nFOEDERATION-HORN {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


def start_call(u, ch):
    cid = C.hexid()
    t0 = time.time()
    s, d = api(u.k, u.uid, "POST", "/api/calls", {"id": cid, "chat_id": ch["id"], "media": "video", "device": u.dev["id"], "caps": CAPS})
    PERF.setdefault("start_ms", []).append(round((time.time() - t0) * 1000))
    return cid, s, d


def end_call(cid, *users):
    for u in users:
        api(u.k, u.uid, "POST", f"/api/calls/{cid}/end", {"reason": "hangup"})
    return wait(lambda: all(not x for x in (sql(k, "SELECT 1 FROM chat_call WHERE id=? AND state<>'ended'", (cid,)) for k in T.I)), 15)


def pick(u, cid, node):
    return api(u.k, u.uid, "POST", f"/api/calls/{cid}/horn", {"device": u.dev["id"], "node": node})


def part_a(adm, anna, bert, ch, bc):
    """Fall a: B hat einen eigenen Knoten, A teilt nicht -> Kaskade Knoten B -> Knoten A."""
    cid, s, d = start_call(anna, ch)
    ok(f"a: Anna startet ueber Knoten (A fragt B nach bereiten Knoten) ({s}, {d.get('call', {}).get('topo')})",
       s == 200 and d["call"]["topo"] == "horn" and [c["node"] for c in cands(d)] == ["horn-a.test"])
    s, d2 = pick(anna, cid, "horn-a.test")
    ok(f"a: Anna waehlt Knoten A ({s})", s == 200)
    ok("a: Kopie auf B ueber Knoten (Teilnehmerliste mit topo)", wait(lambda: (sql("b", "SELECT topo FROM chat_call WHERE id=?", (cid,)) or [[None]])[0][0] == "horn", 10))
    t0 = time.time()
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/join", {"device": bert.dev["id"], "role": "member", "caps": CAPS})
    PERF["beitritt_b_ms"] = round((time.time() - t0) * 1000)
    cs = cands(d)
    ok(f"a: Bert tritt auf B bei, nur Kandidaten auf dem Knoten von B ({s}, {[c['node'] for c in cs]}, {PERF['beitritt_b_ms']} ms)",
       s == 200 and [c["node"] for c in cs] == ["horn-b.test"] and ((d["call"].get("horn") or {}).get("fed") or {}).get("why") == "noshare")
    tok = payload(cs[0]["tokens"]["audio"]) if cs else {}
    ok("a: Token fuer den eigenen Knoten von B ohne Kontingent", tok.get("node") == "horn-b.test" and "q" not in tok)
    s, d = pick(bert, cid, "horn-b.test")
    ok(f"a: Bert waehlt Knoten B, B meldet es dem Heim ({s})", s == 200)
    pb = sql("a", "SELECT id FROM fed_peer WHERE host=?", (T.I["b"]["host"],))[0][0]
    ok("a: A traegt den fremden Knoten am Endpunkt ein", sql("a", "SELECT node FROM chat_call_leg WHERE call_id=? AND device=?", (cid, bert.dev["id"]))[0][0]
       == f"f:{pb}:horn-b.test")
    t0 = time.time()

    def up():
        na, nb = node_of(adm["a"], "horn-a.test"), node_of(adm["b"], "horn-b.test")
        ba = [v for v in (na.get("bridges") or {}).values() if v.get("st") == "up"]
        bb = [v for v in (nb.get("bridges") or {}).values() if v.get("st") == "up"]
        return ba and bb and ba[0].get("peer") == "horn-b.test" and bb[0].get("peer") == "horn-a.test"
    got = wait(up, 25, 0.5)
    PERF["kaskade_bis_up_s"] = round(time.time() - t0, 1)
    ok(f"a: Kaskade Knoten B -> Knoten A steht an beiden Knoten ({PERF['kaskade_bis_up_s']} s, Lastmeldung alle 5 s)", bool(got))
    if not got:
        print(node_log("horn-a.test")[-1500:], node_log("horn-b.test")[-1500:], flush=True)
    ok("a: Kaskaden-Anzeige im Admin-Bereich (Kaskaden: 1)", "Kaskaden: 1" in adm["a"].req("GET", "/calls")[2])
    ok("a: Knoten pruefen beide Seiten (Log: annehmend/waehlend)", "annehmend" in node_log("horn-a.test") and "waehlend" in node_log("horn-b.test"))
    # Boese: das Heim will den Knoten von B an eine interne Adresse schicken (nicht freigegeben) -> 403
    bad = {"op": "horn_cascade", "id": cid, "bridge": "bruecke-boese-1", "node": "horn-b.test", "url": "https://10.11.12.13:443", "peer": "horn-a.test",
           "peer_pub": NODES["horn-a.test"]["pub"], "token": "c1.x.y-" + "z" * 20}
    s, d = FC.signed_post("b", FC.own_priv("a"), "a", "/fed/v2/call", bad)
    ok(f"a: Kaskade an eine interne Adresse -> 403 ({s})", s == 403)
    ok("a: Anruf endet auf A und B", end_call(cid, anna, bert))


def part_b(adm, anna, bert, ben, ch, bc, nc):
    """Fall b: B ohne bereiten Knoten, A teilt -> Bert direkt am Knoten von A; Kontingent, Erlaubt-Liste, Notaus."""
    nb = node_of(adm["b"], "horn-b.test")
    calls_post(adm["b"], {"action": "drain", "id": nb["id"]})
    ok("b: Knoten B geleert (nicht bereit)", wait(lambda: node_of(adm["b"], "horn-b.test").get("status") in ("draining", "draining_upd"), 10))
    na = node_of(adm["a"], "horn-a.test")
    calls_post(adm["a"], {"action": "share", "id": na["id"]})
    ok("b: Knoten A teilt", wait(lambda: node_of(adm["a"], "horn-a.test").get("share") is True, 5))

    def csp_b():
        st, h, _ = hreq("b", "GET", "/chats", cookie=T.ck("b", "fb-bert"))
        return "https://127.0.0.1:8481" in (h.get("content-security-policy") or "")
    ok("b: A schickt B die Adresse des geteilten Knotens (hello), B erlaubt sie in connect-src", wait(csp_b, 25, 1))

    def cors():
        import http.client as hc
        import ssl
        cn = hc.HTTPSConnection("127.0.0.1", 8481, timeout=5, context=ssl._create_unverified_context())
        cn.request("OPTIONS", "/api/sfu/leg", headers={"Origin": T.I["b"]["url"], "Access-Control-Request-Method": "POST"})
        r = cn.getresponse()
        r.read()
        return r.getheader("Access-Control-Allow-Origin") == T.I["b"]["url"]
    ok("b: Knoten A nimmt Seiten von B an (CORS, origins ueber den Steuerkanal)", wait(cors, 10, 1))
    cid, s, d = start_call(anna, ch)
    ok(f"b: Anruf ueber Knoten ({s}, {d.get('call', {}).get('topo')})", s == 200 and d["call"]["topo"] == "horn")
    pick(anna, cid, "horn-a.test")
    wait(lambda: (sql("b", "SELECT topo FROM chat_call WHERE id=?", (cid,)) or [[None]])[0][0] == "horn", 10)
    s, d = api("b", "fb-bert", "POST", f"/api/calls/{cid}/join", {"device": bert.dev["id"], "role": "member", "caps": CAPS})
    cs = cands(d)
    tok = payload(cs[0]["tokens"]["video"]) if cs else {}
    pb = sql("a", "SELECT id FROM fed_peer WHERE host=?", (T.I["b"]["host"],))[0][0]
    ok(f"b: Bert bekommt den geteilten Knoten von A ({s}, {[c['node'] for c in cs]})", s == 200 and [c["node"] for c in cs] == ["horn-a.test"]
       and cs[0].get("home") is True)
    ok(f"b: Token von A mit Kontingent fuer B (q, 10 Teilnehmende, 20000 kbit): {tok.get('q_legs')}/{tok.get('q_kbit')}",
       tok.get("node") == "horn-a.test" and bool(tok.get("q")) and tok.get("q_legs") == 10 and tok.get("q_kbit") == 20000)
    s, d = pick(bert, cid, "horn-a.test")
    na_id = na["id"]
    ok(f"b: Wahl bei A eingetragen (Endpunkt auf Knoten A) ({s})", s == 200 and sql("a", "SELECT node FROM chat_call_leg WHERE call_id=? AND device=?",
                                                                                   (cid, bert.dev["id"]))[0][0] == na_id)
    # Kontingent: hoechstens 1 Teilnehmender von B
    adm["a"].post({"action": "horn_share", "id": str(pb), "allowed": "1", "legs": "1", "mbit": ""})
    s, d = api("b", "fb-ben", "POST", f"/api/calls/{cid}/join", {"device": ben.dev["id"], "role": "member", "caps": CAPS})
    blk = (d.get("call") or {}).get("horn") or {}
    ok(f"b: Kontingent erschoepft -> Ben ohne Kandidaten, klare Meldung ({s}: {blk.get('error', '')[:60]})",
       s == 200 and not blk.get("candidates") and "Kontingent" in (blk.get("error") or "") and (blk.get("fed") or {}).get("why") == "quota")
    s, d = pick(ben, cid, "horn-a.test")
    ok(f"b: Ben kann den Knoten von A auch direkt nicht waehlen ({s})", s == 409)
    # Erlaubt-Liste aus
    adm["a"].post({"action": "horn_share", "id": str(pb), "legs": "", "mbit": ""})
    s, d = api("b", "fb-ben", "POST", f"/api/calls/{cid}/horn", {"device": ben.dev["id"]})
    ok(f"b: B nicht mehr erlaubt -> kein Teilen ({s}, {((d.get('horn') or {}).get('fed') or {}).get('why')})",
       s == 200 and not (d.get("horn") or {}).get("candidates") and ((d.get("horn") or {}).get("fed") or {}).get("why") == "noshare")
    html = adm["a"].req("GET", "/federation")[2]
    ok("b: Foederationsseite zeigt Teilen je Server (Auftragsverarbeitung, Notaus)", "Gjallarhorn-Knoten für" in html and "Auftragsverarbeitung" in html
       and 'name="off"' in html)
    # Notaus
    adm["a"].post({"action": "horn_share", "id": str(pb), "allowed": "1", "off": "1", "legs": "", "mbit": ""})
    q = tok.get("q")

    def off():
        return ((node_of(adm["a"], "horn-a.test").get("quotas") or {}).get(q) or {}).get("off") is True
    ok("b: Notaus -> Knoten A meldet das Kontingent von B als aus", bool(wait(off, 15)))
    s, d = api("b", "fb-ben", "POST", f"/api/calls/{cid}/horn", {"device": ben.dev["id"]})
    ok(f"b: nach Notaus keine Token mehr ({((d.get('horn') or {}).get('fed') or {}).get('why')})",
       s == 200 and not (d.get("horn") or {}).get("candidates") and ((d.get("horn") or {}).get("fed") or {}).get("why") == "off")
    adm["a"].post({"action": "horn_share", "id": str(pb), "allowed": "1", "legs": "", "mbit": ""})
    ok("b: Anruf endet", end_call(cid, anna, bert, ben))
    calls_post(adm["a"], {"action": "unshare", "id": na_id})


def part_c(adm, anna, bert, ch, bc):
    """Fall c: B ohne Knoten (geleert), A teilt nicht -> Mesh mit klarer Meldung."""
    ok("c: Knoten A teilt nicht mehr", wait(lambda: node_of(adm["a"], "horn-a.test").get("share") is False, 5))
    cid, s, d = start_call(anna, ch)
    hm = (d.get("call") or {}).get("horn_mesh") or ""
    ok(f"c: Mesh-Rueckfall ({s}, {d.get('call', {}).get('topo')}) mit Meldung: {hm[:70]}", s == 200 and d["call"]["topo"] == "mesh"
       and T.I["b"]["host"] in hm and "Knoten" in hm)
    bcall = wait(lambda: (api("b", "fb-bert", "GET", f"/api/calls/{cid}")[1] or {}).get("call"), 10) or {}
    ok("c: B sieht dieselbe Meldung (Kopie des Anrufs)", bcall.get("topo") == "mesh" and bcall.get("horn_mesh") == hm)
    ok("c: Anruf endet", end_call(cid, anna))


def part_old(anna, bert, ch, bc):
    cid, s, d = start_call(anna, ch)
    ok(f"2.4: Gruppenanruf mit B (2.4) bleibt still im Mesh ({s}, {d.get('call', {}).get('topo')}, ohne Hinweis)",
       s == 200 and d["call"]["topo"] == "mesh" and not d["call"].get("horn_mesh"))
    wait(lambda: sql("b", "SELECT 1 FROM chat_call WHERE id=?", (cid,)), 10)   # Kopie auf B angelegt (sonst kaeme das Ende vor ihr an)
    lg = open(os.path.join(T.I["b"]["state"], "server.log")).read()
    ok("2.4: B bekommt keine horn-Auftraege (kein 'bad op' im Protokoll)", "bad op" not in lg)
    ok("2.4: Anruf endet", end_call(cid, anna))


if __name__ == "__main__":
    main()
