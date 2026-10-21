"""Gjallarhorn P5 (Web-Client, Konzept v2 §1.2, §2.6, §3.2, §4): Knotenwahl und Live-Umschalten auf Server-Seite,
Gastgeber-Rechte, Ereigniskanal am Knoten - mit echtem saga-media, eingebautem Knoten und einem zweiten, lokal
gestarteten Knoten-Prozess (127.0.0.1:8464 mit UDP, Saga-Schluessel fest per HORN_SAGA_PUB, Registereintrag wie nach
dem Koppeln). Danach der Browser-Test tests/browser_horn_web.js (6 Personen, Last-N, Sprecherwechsel, Bildschirm, Live-
Umschalten zwischen beiden Knoten, Abgang mit neuem Schluessel, Gastgeber, Screenshots; HORN_PERF=1: zusaetzlich 10
Personen mit 9 Kacheln, CPU und Speicher ueber 10 min).

Geprueft (ohne Browser): config.horn_ready; Teilnehmerliste mit hpn (Kennung je Knoten, hpn[Knoten des Anrufs] = hp);
ohne Kaskade nur der Knoten des Anrufs; Wechsel auf einen anderen Knoten nur, wenn der des Anrufs ausfaellt (Leeren per
SIGUSR1 -> Lastmeldung draining) oder das Geraet ihn meidet (avoid); danach ist der neue Knoten der des Anrufs (Mehrheit
unter den bereiten); Beitritt waehrend des Leerens landet auf dem anderen Knoten; Knoten B prueft Saga-Token ohne
Steuerkanal; Ereigniskanal ueber den Knoten (hello mit peers). Gastgeber: nur wer begonnen hat (403 sonst), mute -> call.mod
an die Person, remove -> Endpunkt weg (Grund removed), Beitritt gesperrt (join_block removed) bis zur neuen Einladung.
Wegwerf-Konten ca-w*."""
import http.client
import json
import os
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe_horn_knoten as K  # noqa: E402  (setzt HK_TEST_BASE auf die eigene Instanz)
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

req, res, ok = A_.req, K.res, K.ok
APP = K.APP
B_PORT, B_NAME = 8464, "127.0.0.1"     # Adresse statt Name: STUN (UDP-Probe) loest .localhost nicht auf; 127.0.0.2 antwortet ueber UDP von 127.0.0.1
B_STATE = tempfile.mkdtemp(prefix="horn-b-")
PW = K.PW
USERS = [("ca-w1", "Wanda Gastgeberin"), ("ca-w2", "Wim Zwei"), ("ca-w3", "Wera Drei"), ("ca-w4", "Wolf Vier"), ("ca-w5", "Wiebke Fuenf")]
BUSERS = [("ca-wb1", "Ada Web"), ("ca-wb2", "Ben Web"), ("ca-wb3", "Cleo Web"), ("ca-wb4", "Dirk Web"), ("ca-wb5", "Eva Web"), ("ca-wb6", "Finn Mobil")]
PUSERS = [("ca-wp1", "Gina Last"), ("ca-wp2", "Hugo Last"), ("ca-wp3", "Ida Last"), ("ca-wp4", "Jan Last")]
CAPS = K.CAPS
PERF = {}
NB = {"proc": None, "id": None, "alive": False, "log": None}


# --- zweiter Knoten ---------------------------------------------------------------------------------------------------
def b_row(state="ok", load=0.2):
    """Registereintrag fuer Knoten B wie nach dem Koppeln (P6) - Lastmeldung haelt der Test frisch (alive_b)."""
    nid = NB["id"] or secrets.token_hex(8)
    NB["id"] = nid
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (B_NAME,))
        # P6: bereit nur mit passender Version und Zertifikat ("self" = Test-Zertifikat, wie in horn_erweitern)
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                  "VALUES (?,?,?,0,?,?,?,?,CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                  (nid, B_NAME, "dGVzdC1rbm90ZW4tYi0wMDAwMDAwMDAwMDAwMDAwMDA=", f"https://{B_NAME}:{B_PORT}", True, HN.VERSION, state,
                   json.dumps({"cpu": load, "legs": 0, "max_legs": 48, "state": state}), time.time(), time.time(),
                   json.dumps({"mode": "alpn", "state": "self"})))
    return nid


def alive_b():
    """Solange Knoten B laeuft: Lastmeldung (seen) frisch halten - wie es der Steuerkanal (P6) taete."""
    while NB["alive"]:
        if NB["proc"] is not None and NB["proc"].poll() is not None:
            NB["alive"] = False
            with connect() as c, c:
                c.execute("UPDATE horn_node SET state='getrennt' WHERE id=?", (NB["id"],))
            break
        with connect() as c, c:
            c.execute("UPDATE horn_node SET seen=? WHERE id=? AND state='ok'", (time.time(), NB["id"]))
        time.sleep(2)


def start_b(node):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HORN_") and k not in ("PYTHONPATH",)}
    env.update(HORN_MODE="node", HORN_LISTEN=f":{B_PORT}", HORN_NAME=B_NAME, HORN_TLS_SELF="1", HORN_UDP="1", HORN_ICE_IPS="127.0.0.1",
               HORN_DATA=B_STATE, HORN_SAGA_PUB=HN.saga_pub(), HORN_SAGA_ORIGIN=f"http://127.0.0.1:{K.PORT}", HORN_MAX_LEGS="48")
    NB["log"] = open(os.path.join(B_STATE, "node.log"), "a")
    NB["proc"] = subprocess.Popen([node, "node"], env=env, stdout=NB["log"], stderr=subprocess.STDOUT)
    up = K.wait(lambda: K.port_open(B_PORT), 15, 0.1)
    print("Knoten B: Saga-Schluessel", env["HORN_SAGA_PUB"][:12], flush=True)
    NB["alive"] = bool(up)
    threading.Thread(target=alive_b, daemon=True).start()
    return up


def stop_b():
    NB["alive"] = False
    p = NB["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
    if NB["log"]:
        NB["log"].close()
        NB["log"] = None


def b_req(m, p, body=None):
    c = http.client.HTTPSConnection("127.0.0.1", B_PORT, timeout=15, context=ssl._create_unverified_context())
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers={"Content-Type": "application/json", "Host": B_NAME})
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def node_pid():
    return K.pids().get("node")


def drain_a(on):
    """Eingebauten Knoten leeren (SIGUSR1) bzw. wieder freigeben (SIGUSR2); Saga merkt es an der Lastmeldung (<= 5 s)."""
    pid = node_pid()
    os.kill(pid, signal.SIGUSR1 if on else signal.SIGUSR2)
    want = "draining" if on else "ok"

    def seen():
        st = HN.status()
        a = next((x for x in st if x["builtin"]), None)
        return a and (a.get("load") or {}).get("state") == want and a
    return K.wait(seen, 25, 0.2)


def cands_of(d):
    return [c["node"] for c in ((d.get("horn") or (d.get("call") or {}).get("horn") or {}).get("candidates") or [])]


# --- Ablauf -----------------------------------------------------------------------------------------------------------
def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    K.flag(False)
    for u, n in USERS + BUSERS + PUSERS:
        auth.create_user(u, PW, n)
    media, node = K.build("saga-media", "media", "SAGA_MEDIA_BIN"), K.build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok(f"saga-media und gjallarhorn gebaut ({bool(media)}, {bool(node)})", media and node)
    try:
        if media and node:
            run(media, node)
    finally:
        for x in A_.STREAMS:
            x.close()
        stop_b()
        K.stop()
        errs = K.log_text().count("Traceback")
        ok(f"Instanz: Serverfehler {errs}", errs == 0)
        if errs:
            print(K.log_text()[-6000:])
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE (builtin=1 AND name='localhost') OR name=?", (B_NAME,))
        if not all(res) and os.environ.get("STATE_DIR"):    # Protokolle fuer die Fehlersuche behalten
            for f, n in ((os.path.join(K.STATE, "server.log"), "horn-web-server.log"), (os.path.join(B_STATE, "node.log"), "horn-web-knoten-b.log")):
                if os.path.exists(f):
                    shutil.copy(f, os.path.join(os.environ["STATE_DIR"], n))
        shutil.rmtree(K.STATE, ignore_errors=True)
        shutil.rmtree(B_STATE, ignore_errors=True)
    print("PERF " + json.dumps(PERF))
    print(f"\nANRUFE-HORN-WEB {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(media, node):
    # alle Browser kommen von 127.0.0.1: mit 10 Personen (HORN_PERF) mehr als 12 Beine je Adresse
    t = K.start(media, node, {"HORN_MAX_PER_IP": "40"} if os.environ.get("HORN_PERF") == "1" else None)
    ok(f"Instanz laeuft ({t and round(t, 2)} s)", t is not None)
    K.flag(True)
    n = K.wait(K.node_ready, 25)
    ok("eingebauter Knoten bereit", n is not None)
    if not n:
        print(K.log_text()[-4000:])
        return
    b_row()
    ok(f"Knoten B ({B_NAME}:{B_PORT}) gestartet, Saga-Schluessel fest (ohne Steuerkanal)", start_b(node))
    s, h = b_req("GET", "/api/session")
    ok(f"Knoten B antwortet (mode node, {s})", s == 200 and h.get("mode") == "node" and h.get("name") == B_NAME)
    server_tests()
    if os.environ.get("HORN_BROWSER") != "0":
        browser()
    else:
        print("SKIP Browser-Test (HORN_BROWSER=0)")


def server_tests():
    people = {u: A_.Person(u) for u, _ in USERS}
    for p in people.values():
        req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS})
    A, B, Cc, D, E = (people[u] for u, _ in USERS)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"config: horn_ready ({cf.get('horn_ready')})", s == 200 and cf.get("horn_ready") is True)
    # CSP der Chat-Seite: Geraete duerfen beide Knoten ansprechen (Beine, Ereigniskanal, Messung); der Browser-Test
    # umgeht die CSP nur fuer seinen Messpunkt (AudioWorklet) - hier wird sie selbst geprueft
    time.sleep(10.5)                                   # CSP-Zusatz wird hoechstens alle 10 s neu gebaut
    c = http.client.HTTPConnection("127.0.0.1", K.PORT, timeout=15)
    c.request("GET", "/chats", headers={"Cookie": "saga=" + A.k, "Host": f"127.0.0.1:{K.PORT}"})
    r = c.getresponse()
    csp = r.getheader("Content-Security-Policy") or ""
    r.read()
    c.close()
    cs = next((x for x in csp.split(";") if x.strip().startswith("connect-src")), "")
    ok("CSP connect-src der Chat-Seite: beide Knoten (https und wss)", all(u in cs for u in (
        f"https://localhost:{K.HORN_PORT}", f"wss://localhost:{K.HORN_PORT}", f"https://{B_NAME}:{B_PORT}", f"wss://{B_NAME}:{B_PORT}")), cs)
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:]]})
    gid, cid = d["chat"]["id"], secrets.token_hex(16)
    sa = A_.Stream(A.k)
    sd = A_.Stream(D.k)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    k = d.get("call") or {}
    ok(f"Start: topo horn, nur der Knoten des Anrufs ({cands_of(d)}), Gastgeberin host", s == 200 and k.get("topo") == "horn"
       and cands_of(d) == ["localhost"] and k.get("host") is True and k["horn"]["candidates"][0]["here"] is True)
    for p in (B, Cc):
        s, d = req("POST", f"/api/calls/{cid}/join", p.k, {"device": p.dev["id"], "role": "member", "caps": CAPS})
        ok(f"{p.uid}: Beitritt auf dem Knoten des Anrufs ({cands_of(d)}), nicht Gastgeber", s == 200 and cands_of(d) == ["localhost"]
           and d["call"].get("host") is False)
    legs = d["call"]["legs"]
    hpn_ok = all(set(x.get("hpn") or {}) == {"localhost", B_NAME} and x["hpn"]["localhost"] == x["hp"] and x["hpn"][B_NAME] != x["hp"] for x in legs)
    ok("Teilnehmerliste: hpn je Knoten (eingebauter = hp, Knoten B eigene Kennung)", hpn_ok, legs[0])
    tok = d["call"]["horn"]["candidates"][0]["peer"]
    ok("eigene Kennung im Kandidaten = hpn des eigenen Endpunkts", tok == next(x for x in legs if x["device"] == Cc.dev["id"])["hpn"]["localhost"])
    # Wechsel: ohne Ausfall nur der Knoten des Anrufs
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"], "node": B_NAME})
    ok(f"Wechsel auf Knoten B, solange der des Anrufs bereit ist -> 409 ({s})", s == 409)
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"], "avoid": ["localhost"]})
    c0 = (d.get("horn") or {}).get("candidates") or []
    ok(f"avoid=[localhost]: Kandidat Knoten B, noch nicht 'hier' ({[(c['node'], c['here']) for c in c0]})",
       s == 200 and [c["node"] for c in c0] == [B_NAME] and c0[0]["here"] is False and c0[0]["url"] == f"https://{B_NAME}:{B_PORT}")
    pb = K.payload(c0[0]["tokens"]["audio"])
    pu = K.payload(c0[0]["tokens"].get("both") or "x.e30.x")
    ok("Knoten B meldet UDP: Kandidat udp, Token fuer den UDP-Weg (both, udp_ok)", c0[0]["udp"] is True and pu.get("role") == "both" and pu.get("udp_ok") is True, pu)
    ok("Token fuer Knoten B: anderer Knoten, eigene Kennungen", pb["node"] == B_NAME and pb["peer_ref"] == c0[0]["peer"]
       and pb["peer_ref"] == next(x for x in legs if x["device"] == B.dev["id"])["hpn"][B_NAME])
    s, x = b_req("POST", "/api/sfu/turn", {"token": c0[0]["tokens"]["video"]})
    ok(f"Knoten B prueft das Saga-Token ohne Steuerkanal (TURN-Zugang {s})", s == 200 and x.get("username"), x)
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"], "node": B_NAME, "avoid": ["localhost"]})
    ok(f"mit avoid: Endpunkt auf Knoten B eingetragen ({s})", s == 200 and cands_of(d) == [B_NAME])
    with connect() as c:
        nodes = dict(c.execute("SELECT device, node FROM chat_call_leg WHERE call_id=? AND state='active'", (cid,)).fetchall())
    ok("Register: B auf Knoten B, die anderen auf dem eingebauten", nodes[B.dev["id"]] == NB["id"] and nodes[A.dev["id"]] != NB["id"])
    s, d = req("POST", f"/api/calls/{cid}/horn", Cc.k, {"device": Cc.dev["id"]})
    ok(f"ein Geraet gewechselt: Knoten des Anrufs bleibt der mit den meisten ({cands_of(d)})", cands_of(d) == ["localhost"])
    # Leeren des eingebauten Knotens: Saga bietet Knoten B an (dort ist schon jemand -> 'hier')
    t0 = time.time()
    a = drain_a(True)
    PERF["leeren_bis_register_ms"] = round((time.time() - t0) * 1000)   # Datenbank (hoechstens alle 10 s); Saga selbst: naechste Lastmeldung
    ok(f"SIGUSR1: eingebauter Knoten meldet draining (im Register nach {PERF['leeren_bis_register_ms']} ms), nicht mehr bereit", a and not a["ready"], a)
    s, d = req("POST", f"/api/calls/{cid}/horn", Cc.k, {"device": Cc.dev["id"]})
    c1 = (d.get("horn") or {}).get("candidates") or []
    ok(f"waehrend des Leerens: Kandidat Knoten B, 'hier' ({[(c['node'], c['here']) for c in c1]})",
       [c["node"] for c in c1] == [B_NAME] and c1[0]["here"] is True)
    s, d = req("POST", f"/api/calls/{cid}/horn", Cc.k, {"device": Cc.dev["id"], "node": B_NAME})
    ok(f"Wechsel auf Knoten B erlaubt ({s})", s == 200)
    s, d = req("POST", f"/api/calls/{cid}/join", D.k, {"device": D.dev["id"], "role": "member", "caps": CAPS})
    ok(f"Beitritt waehrend des Leerens: Knoten B ({cands_of(d)})", s == 200 and cands_of(d) == [B_NAME])
    s, d = req("POST", f"/api/calls/{cid}/horn", D.k, {"device": D.dev["id"], "node": "localhost"})
    ok(f"zurueck auf den geleerten Knoten -> 409 ({s})", s == 409)
    a = drain_a(False)
    ok("SIGUSR2: eingebauter Knoten wieder bereit", a and a["ready"], a)
    s, d = req("POST", f"/api/calls/{cid}/horn", A.k, {"device": A.dev["id"]})
    ok(f"nach dem Leeren: Knoten des Anrufs ist jetzt der mit den meisten bereiten Endpunkten (B: 3 von 4) -> {cands_of(d)}",
       cands_of(d) == [B_NAME])
    # Ereigniskanal am Knoten B (Saga-Token): Bein anlegen geht hier nicht ohne Browser - hello eines unbekannten Beins 404
    s, _ = b_req("GET", "/api/sfu/leg/0000000000000000/events")
    ok(f"Ereigniskanal: unbekanntes Bein -> 404 ({s})", s == 404)
    # --- Gastgeber: stummschalten, entfernen --------------------------------------------------------------------------
    s, d = req("POST", f"/api/calls/{cid}/moderate", B.k, {"device": B.dev["id"], "target": Cc.dev["id"], "action": "mute"})
    ok(f"stummschalten durch Nicht-Gastgeber -> 403 ({s})", s == 403)
    s, d = req("POST", f"/api/calls/{cid}/moderate", A.k, {"device": A.dev["id"], "target": A.dev["id"], "action": "mute"})
    ok(f"sich selbst -> 400 ({s})", s == 400)
    s, d = req("POST", f"/api/calls/{cid}/moderate", A.k, {"device": A.dev["id"], "target": D.dev["id"], "action": "kick"})
    ok(f"unbekannte Aktion -> 400 ({s})", s == 400)
    m0 = sd.mark()
    s, d = req("POST", f"/api/calls/{cid}/moderate", A.k, {"device": A.dev["id"], "target": D.dev["id"], "action": "mute"})
    ev = sd.wait("call.mod", lambda x: x.get("call_id") == cid, since=m0)
    ok(f"Gastgeberin schaltet D stumm ({s}): call.mod an D mit Name", s == 200 and ev and ev["action"] == "mute" and ev["device"] == D.dev["id"]
       and ev["by"] == "Wanda Gastgeberin", ev)
    ma = sa.mark()
    s, d = req("POST", f"/api/calls/{cid}/moderate", A.k, {"device": A.dev["id"], "target": D.dev["id"], "action": "remove"})
    ev = sd.wait("call.mod", lambda x: x.get("action") == "remove", since=m0)
    ros = sa.wait("call.roster", lambda x: x.get("call_id") == cid and D.dev["id"] not in [y["device"] for y in x.get("legs") or []], since=ma)
    ok(f"Gastgeberin entfernt D ({s}): call.mod remove, Teilnehmerliste ohne D", s == 200 and ev and ros)
    with connect() as c:
        r = c.execute("SELECT state, reason FROM chat_call_leg WHERE call_id=? AND device=?", (cid, D.dev["id"])).fetchone()
    ok(f"Endpunkt von D: left/removed ({r})", tuple(r) == ("left", "removed"))
    s, d = req("POST", f"/api/calls/{cid}/join", D.k, {"device": D.dev["id"], "role": "member", "caps": CAPS})
    ok(f"D kann nicht wieder beitreten (409 removed: {s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "removed")
    s, d = req("GET", f"/api/calls/{cid}", D.k)
    ok(f"Anruf-Objekt fuer D: joinable false, removed ({d['call'].get('join_block')})", not d["call"]["joinable"] and d["call"]["join_block"] == "removed")
    s, d = req("POST", f"/api/calls/{cid}/invite", A.k, {"uids": [D.uid]})
    s2, d2 = req("POST", f"/api/calls/{cid}/join", D.k, {"device": D.dev["id"], "role": "member", "caps": CAPS})
    ok(f"nach erneuter Einladung darf D wieder beitreten ({s}, {s2})", s == 200 and s2 == 200)
    req("POST", f"/api/calls/{cid}/end", A.k, {"device": A.dev["id"]})
    for p in (B, Cc, D):
        req("POST", f"/api/calls/{cid}/leave", p.k, {"device": p.dev["id"]})
    sa.close()
    sd.close()


def browser():
    js = os.path.join(APP, "tests", "browser_horn_web.js")
    pw = os.environ.get("PLAYWRIGHT") or "/opt/npm-tools/node_modules/playwright"
    if not shutil.which("node") or not os.path.exists(pw):
        ok("Browser-Test: node und Playwright vorhanden", False)
        return
    users = BUSERS + (PUSERS if os.environ.get("HORN_PERF") == "1" else [])
    env = dict(os.environ, SAGA_BASE=f"http://127.0.0.1:{K.PORT}", SAGA_USERS=",".join(u for u, _ in users), SAGA_PASS=PW,
               NODE_A_PID=str(node_pid() or ""), NODE_B_PID=str(NB["proc"].pid), NODE_B=B_NAME, PLAYWRIGHT=pw,
               PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"), PW_CHANNEL="chromium",
               SHOTS=os.environ.get("SHOTS") or os.path.join(os.environ.get("STATE_DIR") or K.STATE, "shots"))
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    logf = os.path.join(os.environ.get("STATE_DIR") or K.STATE, "browser_horn_web.log")
    with open(logf, "w") as lf:                        # laufend mitschreiben (bricht der Lauf ab, bleibt das Protokoll)
        try:
            rc = subprocess.run(["node", js], env=env, stdout=lf, stderr=subprocess.STDOUT, timeout=int(os.environ.get("HORN_WEB_TIMEOUT") or 1300)).returncode
        except subprocess.TimeoutExpired:
            rc = -1
    out = open(logf, encoding="utf-8", errors="replace").read()
    print(out[-20000:], flush=True)
    for ln in out.splitlines():
        if ln.startswith("OK  ") or ln.startswith("FAIL"):
            res.append(ln.startswith("OK"))
        if ln.startswith("PERF "):
            try:
                PERF["browser"] = json.loads(ln[5:])
            except ValueError:
                pass
    ok(f"Browser-Test lief durch (rc {rc})", rc == 0)
    a = node_pid()
    ok(f"eingebauter Knoten laeuft noch bzw. wieder (PID {a})", a is not None)


if __name__ == "__main__":
    main()
