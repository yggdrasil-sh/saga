"""2.5 A3 "Anrufe der Apps ueber die Saga-Adresse" (Medien-Tunnel /api/sfu/ws, Tunnel-Betrieb des eingebauten Knotens)
- mit echtem saga-media, echtem gjallarhorn und nginx als Nachbau von Nginx Proxy Manager (TLS am Proxy).

Teil 0 (ohne Server): hello ws/tunnel, Zertifikat "tunnel" nur fuer den eingebauten, tunnel_possible, Kandidat tcp/tls/ws
(horn_web, der echte Pfad), calldiag nimmt transport ws.
Teil 1: BASE_URL https mit DNS-Namen hinter nginx, ohne SAGA_TLS, ohne HORN_NAME und Zertifikat -> der eingebaute Knoten
startet im Tunnel-Betrieb (kein eigener TCP-Port), gilt als bereit, Adresse = Saga-Ursprung, ice_tcp aus, ws an.
Gruppenanruf -> Kandidat tcp false, tls false, ws true. Ueber nginx ("Websockets Support" an): 101 mit saga-tunnel.1,
Zugang per erster Nachricht (falsch -> err auth); ohne "Websockets Support": 426 mit Hinweis, Zaehler no_upgrade, Admin
"Wohl nicht ...". Zwei Geraete (pion, Go-Test TestA3Kette) mit den Medien-Token von Saga ueber die ganze Kette, Weg ws,
Medien in beide Richtungen, Zaehler im Bein und im Admin-Bereich (offene Tunnel, Fenster). Admin -> Anrufe: Kasten
"Anrufe ueber die Saga-Adresse (Tunnel)", keine Port-Tabelle fuer den Knoten, CSP erlaubt die Pruefung. Web: Knoten
ohne TLS nur mit UDP (calls.js). Grenze je Adresse kommt aus X-Forwarded-For (TRUSTED_PROXIES).
Teil 2: mit HORN_NAME + HORN_TLS_SELF (eigener Name) bleibt alles wie bisher (tcp/tls an), ws zusaetzlich.

S10 (Pruefung F1): Geraete ohne caps "ws" (Browser, aeltere Apps) sehen den Tunnel-Knoten nicht - Start -> Mesh, 1:1 mit
horn_all -> fallback node; Beitritt ohne "ws" in einen Anruf auf dem Tunnel-Knoten -> Anruf direkt, 409 topo mesh.
  Ports aus A3_BASE (Standard 8521): +0 nginx (Websockets an), +3 nginx (aus), +1 saga-media, +2 Admin, +7 intern,
  +8 HORN_PORT. nginx aus gjallarhorn/e2e/npm-nginx.conf.tmpl (Zertifikat selbst erzeugt, Ordner im STATE).
  Umgebung wie die anderen Reihen (env.sh, eigene DB). SAGA_MEDIA_BIN / GJALLARHORN_BIN, sonst go build. A3_SHOTS=<Ordner>:
  Bild Admin -> Anrufe (tests/a3_bilder.js)."""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_B = int(os.environ.get("A3_BASE") or 8521)
NGX, NGX_OFF, PORT, ADMIN, INTERNAL, HORN_PORT = _B, _B + 3, _B + 1, _B + 2, _B + 7, _B + 8
NAME = "saga.example.org"
BASE = f"https://{NAME}:{NGX}"
os.environ["HK_TEST_BASE"] = f"http://127.0.0.1:{PORT}"
os.environ["HK_TEST_ADMIN"] = f"http://127.0.0.1:{ADMIN}"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

req, res = A_.req, A_.res
SECRET = os.environ.get("SESSION_SECRET") or os.environ["SECRET_KEY"]
STATE = tempfile.mkdtemp(prefix="a3-")
NGINX = os.path.join(STATE, "ngx", "nginx.conf")
CAPS_WEB = ["reneg", "want", "bye", "screen2", "multi", "horn"]   # Browser bzw. aeltere App: ohne Tunnel
CAPS = CAPS_WEB + ["ws"]                                          # App mit Medien-Tunnel (Vertrag S10)
CALLS = {"enabled": True, "stun": "", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "", "turn_password": "",
         "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
PW = "A3-Tunnel-Pass-1234"
USERS = [("ca-a31", "Anna Tunnel"), ("ca-a32", "Bert Tunnel"), ("ca-a33", "Cora Tunnel"), ("ca-a34", "Dora Tunnel"), ("ca-a35", "Emil Tunnel")]
P = {"proc": None, "log": None, "ngx": False}


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:1500]), flush=True)


def build(name, d, env_bin):
    b = os.environ.get(env_bin)
    if b:
        return b
    out = os.path.join(STATE, name)
    env = dict(os.environ, CGO_ENABLED="0", GOPROXY="direct", GOSUMDB="off", GOFLAGS="-mod=mod", GOTOOLCHAIN="local")
    cmd = ["go", "build", "-trimpath", "-o", out, "."]
    if d == "gjallarhorn" and os.environ.get("A3_GOMOD"):
        cmd[2:2] = ["-modfile=" + os.environ["A3_GOMOD"]]
    r = subprocess.run(cmd, cwd=os.path.join(APP, d), capture_output=True, text=True, env=env)
    if r.returncode:
        print(r.stderr[-1500:])
    return None if r.returncode else out


def start(media, node, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL", "SAGA_HORN")
           and not k.startswith(("HORN_", "SAGA_TLS", "TLS_"))}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT=str(ADMIN), ADMIN_HOST="127.0.0.1", HOST="127.0.0.1",
               STATE_DIR=STATE, BASE_URL=BASE, CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0",
               PYTHONPATH=APP, SECRET_KEY=os.environ["SECRET_KEY"], SESSION_SECRET=SECRET, GJALLARHORN_BIN=node,
               HORN_PORT=str(HORN_PORT), HORN_UDP="0", HORN_ICE_IPS="127.0.0.1", TRUSTED_PROXIES="127.0.0.1",
               SAGA_CF_API="http://127.0.0.1:9/client/v4")
    env.update(extra or {})
    P["log"] = open(os.path.join(STATE, "server.log"), "a")
    P["proc"] = subprocess.Popen([media, "--", sys.executable, "run.py"], env=env, cwd=APP, stdout=P["log"], stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 40:
        try:
            if req("GET", "/healthz")[0] == 200:
                return time.time() - t0
        except OSError:
            pass
        time.sleep(0.2)
    return None


def stop():
    p = P["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(25)
        except subprocess.TimeoutExpired:
            p.kill()
    if P["log"]:
        P["log"].close()
    P["proc"] = None


def log_text():
    try:
        return open(os.path.join(STATE, "server.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def flag(on, **extra):
    auth.save_setting("calls", dict(CALLS, horn=on, horn_builtin=True, **extra))
    auth._settings_cache["v"] = None


def adm(method, path, form=None):
    from itsdangerous import TimestampSigner
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    ck = sa.sign(base64.b64encode(json.dumps({"aid": "ca-a3-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    body, hd = None, {"Cookie": "hk_admin=" + ck, "X-Saga": "1", "Accept-Language": "de-DE"}
    if form is not None:
        body = json.dumps(form) if isinstance(form, dict) else form
    c = http.client.HTTPConnection("127.0.0.1", ADMIN, timeout=30)
    c.request(method, path, body=body, headers=hd)
    r = c.getresponse()
    d = r.read().decode("utf-8", "replace")
    hdrs = dict(r.getheaders())
    c.close()
    return r.status, d, ck, hdrs


def state():
    s, d, _, _ = adm("GET", "/api/calls/state")
    return json.loads(d) if s == 200 else {}


def builtin(st=None):
    return next((n for n in (st or state()).get("nodes") or [] if n.get("builtin")), None)


def wait(fn, t, step=0.3):
    t0 = time.time()
    v = None
    while time.time() - t0 < t:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return v


def tls_conn(port):
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE           # nur Pruefstand (selbst signiert)
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    return ctx.wrap_socket(s, server_hostname=NAME)


def ws_handshake(port, protos="saga-tunnel.1", xff=None):
    """WebSocket-Handschlag ueber nginx -> (Status, Koepfe, Koerper bzw. offener Socket)."""
    s = tls_conn(port)
    key = base64.b64encode(secrets.token_bytes(16)).decode()
    extra = f"X-Forwarded-For: {xff}\r\n" if xff else ""
    s.sendall((f"GET /api/sfu/ws HTTP/1.1\r\nHost: {NAME}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {protos}\r\n{extra}\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        d = s.recv(4096)
        if not d:
            break
        buf += d
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    st = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 0
    hd = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
    if st != 101:
        try:
            s.settimeout(2)
            while len(rest) < int(hd.get("content-length", "0") or 0):
                d = s.recv(4096)
                if not d:
                    break
                rest += d
        except OSError:
            pass
        s.close()
        return st, hd, rest.decode("utf-8", "replace")
    return st, hd, s


def ws_send(s, op, data):
    m = secrets.token_bytes(4)
    n = len(data)
    hdr = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big"))
    s.sendall(hdr + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))


def ws_recv(s):
    def rd(n):
        b = b""
        while len(b) < n:
            d = s.recv(n - len(b))
            if not d:
                raise OSError("zu")
            b += d
        return b
    h = rd(2)
    n = h[1] & 0x7F
    if n == 126:
        n = int.from_bytes(rd(2), "big")
    elif n == 127:
        n = int.from_bytes(rd(8), "big")
    return h[0] & 0x0F, rd(n)


def https_get(port, path):
    s = tls_conn(port)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {NAME}:{port}\r\nConnection: close\r\n\r\n".encode())
    buf = b""
    try:
        while True:
            d = s.recv(65536)
            if not d:
                break
            buf += d
    except OSError:
        pass
    s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    return int(head.split()[1]), body.decode("utf-8", "replace")


# --- Teil 0 -----------------------------------------------------------------------------------------------------------
def teil0():
    h = HN._clean_hello({"name": NAME, "url": BASE, "ws": True, "tunnel": True, "ice_tcp": False})
    h2 = HN._clean_hello({"name": NAME, "url": BASE})
    ok("hello: ws/tunnel uebernommen, aeltere Knoten ohne Felder: aus", h["ws"] and h["tunnel"] and not h2["ws"] and not h2["tunnel"])
    ok("Zertifikat 'tunnel' gilt nur fuer den eingebauten", HN.cert_ok({"builtin": 1, "cert": {"state": "tunnel"}})
       and not HN.cert_ok({"builtin": 0, "cert": {"state": "tunnel"}}))
    old = os.environ.get("BASE_URL")
    try:
        verdict = {}
        for u in (BASE, "https://saga.example.org", "http://saga.example.org", "https://127.0.0.1:8521", "https://localhost", "https://[::1]"):  # noqa: E501
            os.environ["BASE_URL"] = u
            verdict[u] = HN.tunnel_possible()
        ok("tunnel_possible nur mit https und DNS-Namen", verdict == {BASE: True, "https://saga.example.org": True, "http://saga.example.org": False,
           "https://127.0.0.1:8521": False, "https://localhost": False, "https://[::1]": False}, verdict)
    finally:
        if old is None:
            os.environ.pop("BASE_URL", None)
        else:
            os.environ["BASE_URL"] = old
    HN._LIVE["a3t"] = {"tunnel": True, "ws": True, "ice_tcp": False}
    HN._LIVE["a3o"] = {}
    n1, n2, n3 = {"id": "a3t", "builtin": 1}, {"id": "a3t", "builtin": 0}, {"id": "a3o", "builtin": 1}
    ok("Kandidat: Tunnel-Betrieb tcp/tls aus, ws an; aelterer Knoten tcp/tls an, ws aus",
       (HN.ice_tcp(n1), HN.tls_ok(n1), HN.ws_ok(n1)) == (False, False, True) and HN.tls_ok(n2) and (HN.ice_tcp(n3), HN.tls_ok(n3), HN.ws_ok(n3)) == (True, True, False))
    HN._LIVE.pop("a3t")
    HN._LIVE.pop("a3o")
    # Pruefer A3: Kaskade - der Knoten im Tunnel-Betrieb kann keine Bruecke annehmen (seine Adresse endet am Proxy), er ist
    # Mittelpunkt nur ohne anderen eigenen Knoten im Anruf (auch nicht als bisheriger), waehlen kann er
    from saga import horn_bridge as HB
    pl = {"mehr": HB.plan([("a3t", 3), ("h1", 1)], None, {"a3t"}), "bisher": HB.plan([("a3t", 1), ("h1", 1)], "a3t", {"a3t"}),
          "allein": HB.plan([("a3t", 2), ("f:7:x", 1)], None, {"a3t"}), "ohne": HB.plan([("a3t", 3), ("h1", 1)], None)}
    ok("Kaskade: Tunnel-Knoten nicht Mittelpunkt, wenn ein anderer eigener Knoten dabei ist",
       pl == {"mehr": ("h1", ["a3t"]), "bisher": ("h1", ["a3t"]), "allein": ("a3t", ["f:7:x"]), "ohne": ("a3t", ["h1"])}, pl)
    from saga import calldiag
    ok("calldiag: transport ws erlaubt", "ws" in calldiag.TRANSPORTS)
    js = open(os.path.join(APP, "static", "calls.js"), encoding="utf-8").read()
    ok("Web: Knoten ohne TLS nur mit UDP (kein TURN-TLS-Versuch)", "cand.tls === false" in js and "if (noTls) throw" in js)


# --- Teil 1 -----------------------------------------------------------------------------------------------------------
def candidates_for_call(people):
    A, others = people[0], people[1:]
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [p.uid for p in others]})
    gid, cid = d["chat"]["id"], secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    call = d.get("call") or {}
    cands = {A.uid: (call.get("horn") or {}).get("candidates") or []}
    B = others[0]
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    cands[B.uid] = (((d.get("call") or {}).get("horn")) or {}).get("candidates") or []
    return cid, call.get("topo"), cands


def s10(people, cid):
    """Vertrag S10: Geraete ohne "ws" bekommen den Tunnel-Knoten nicht; gemischter Anruf faellt auf direkt zurueck."""
    A, B, Cc, D, E = people
    for p in (Cc, D, E):
        req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS_WEB})
    s, d = req("POST", "/api/chats", Cc.k, {"kind": "free", "members": [p.uid for p in people if p is not Cc]})
    gid2, cid2 = d["chat"]["id"], secrets.token_hex(16)
    s, d = req("POST", "/api/calls", Cc.k, {"id": cid2, "chat_id": gid2, "media": "video", "device": Cc.dev["id"], "caps": CAPS_WEB})
    ok("Browser (ohne ws) startet Gruppenanruf: Mesh wie c512bad (Tunnel-Knoten nicht bereit)", s == 200 and (d.get("call") or {}).get("topo") == "mesh",
       (s, (d.get("call") or {}).get("topo")))
    req("POST", f"/api/calls/{cid2}/end", Cc.k, {"device": Cc.dev["id"]})
    s, d = req("GET", "/api/calls/config", Cc.k)
    ok("config.horn_ready fuer Geraete ohne Angabe: kein bereiter Knoten", s == 200 and not d.get("horn_ready"), d.get("horn_ready"))
    # Beitritt ohne ws in den laufenden Anruf auf dem Tunnel-Knoten -> direkt
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"]})
    ok("App im Anruf bekommt weiter den Tunnel-Knoten (/horn)", s == 200 and len(((d.get("horn") or {}).get("candidates")) or []) == 1, s)
    s, d = req("POST", f"/api/calls/{cid}/join", D.k, {"device": D.dev["id"], "role": "member", "caps": CAPS_WEB})
    s2, d2 = req("GET", f"/api/calls/{cid}", A.k)
    ok("aeltere App ohne ws/meshjoin tritt bei: 409 join_block node wie bisher, Anruf bleibt auf dem Knoten",
       s == 409 and d.get("join_block") == "node" and (d2.get("call") or {}).get("topo") == "horn", (s, d, (d2.get("call") or {}).get("topo")))
    # S11: Browser (meshjoin) - sein Bein steht vor call.topo in der Liste, Antwort mit offer_to (nur groessere IDs)
    st = A_.Stream(A.k)
    time.sleep(0.5)
    m0 = st.mark()
    s, d = req("POST", f"/api/calls/{cid}/join", E.k, {"device": E.dev["id"], "role": "member", "caps": CAPS_WEB + ["meshjoin"]})
    call = d.get("call") or {}
    act = sorted(x["device"] for x in call.get("legs") or [] if x.get("state", "active") == "active")
    ok("Browser (meshjoin) tritt Anruf auf dem Tunnel-Knoten bei: 200, Anruf direkt, offer_to = groessere Geraete-IDs",
       s == 200 and call.get("topo") == "mesh" and d.get("offer_to") == [x for x in act if x > E.dev["id"]] and E.dev["id"] in act,
       (s, call.get("topo"), d.get("offer_to"), act, E.dev["id"]))
    st.wait("call.topo", since=m0, timeout=6)
    evs = [(i, e) for i, e in enumerate(st.ev[m0:]) if e[0] in ("call.roster", "call.topo")]
    i_topo = next((i for i, e in evs if e[0] == "call.topo"), None)
    i_list = next((i for i, e in evs if e[0] == "call.roster" and any(x.get("device") == E.dev["id"] for x in (e[1] or {}).get("legs") or [])), None)
    st.close()
    ok("Liste mit dem neuen Geraet kommt vor call.topo (alle wenden \"Angebot an groessere ID\" auf dieselbe Liste an)",
       i_topo is not None and i_list is not None and i_list < i_topo, [e[0] for _, e in evs])
    s, d = req("GET", f"/api/calls/{cid}", A.k)
    ok("Anruf laeuft jetzt direkt (topo mesh), das Geraet steht in der Liste", (d.get("call") or {}).get("topo") == "mesh"
       and any(x["device"] == E.dev["id"] for x in (d.get("call") or {}).get("legs") or []), (d.get("call") or {}).get("topo"))
    req("POST", f"/api/calls/{cid}/end", A.k, {"device": A.dev["id"]})
    # 1:1 mit horn_all: ohne ws Rueckfall direkt, mit ws ueber den Knoten
    flag(True, horn_from=3, horn_all=True)
    wait(lambda: req("GET", "/api/calls/config", Cc.k)[1].get("horn_dm"), 15)      # Einstellungen im Server zwischengespeichert
    dm = A_.dm(Cc, D)
    s, d = req("POST", "/api/calls", Cc.k, {"id": secrets.token_hex(16), "chat_id": dm, "media": "audio", "device": Cc.dev["id"],
                                            "caps": CAPS_WEB, "via": "horn"})
    ok("1:1 ueber Knoten ohne ws: 409 fallback node (direkt wie bisher)", s == 409 and d.get("horn_block") == "node", (s, d))
    flag(True, horn_from=3)


def teil1(media, node):
    flag(True, horn_from=3)
    t = start(media, node)
    ok(f"Instanz (saga-media + Saga, ohne SAGA_TLS, BASE_URL {BASE}) laeuft", t is not None, log_text()[-2000:])
    if t is None:
        return
    n = wait(lambda: (lambda b: b if b and b.get("ctl") and b.get("status") == "ready" else None)(builtin()), 30)
    ok("eingebauter Knoten ohne Namen/Zertifikat: startet (Tunnel-Betrieb), verbunden, bereit", bool(n), (builtin(), log_text()[-2500:]))
    if not n:
        return
    ok("Adresse = Saga-Ursprung, Tunnel-Betrieb, ws an, kein ICE-TCP", n["url"] == BASE and n.get("tunnel") and n.get("ws") and n.get("ice_tcp") is False, n)
    ok("Zertifikat 'tunnel' (keins noetig), Log nennt den Tunnel-Betrieb", (n.get("cert") or {}).get("state") == "tunnel"
       and "tunnel-betrieb: kein eigener tcp-port" in log_text(), n.get("cert"))
    with socket.socket() as so:
        so.settimeout(1)
        closed = so.connect_ex(("127.0.0.1", HORN_PORT)) != 0
    ok(f"kein eigener TCP-Port des Knotens ({HORN_PORT} zu)", closed)
    # Kandidat
    people = [A_.Person(u) for u, _ in USERS]
    for p in people:
        req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS})
    cid, topo, cands = candidates_for_call(people)
    ca = (cands.get(people[0].uid) or [{}])[0]
    ok("App (caps ws): Gruppenanruf ueber den Knoten, Kandidat url = Saga, tcp false, tls false, ws true, udp false",
       topo == "horn" and ca.get("url") == BASE and ca.get("tcp") is False and ca.get("tls") is False and ca.get("ws") is True and ca.get("udp") is False,
       {"topo": topo, "cand": {k: ca.get(k) for k in ("url", "tcp", "tls", "ws", "udp")}})
    # 426 ohne "Websockets Support", vor jeder Pruefung
    st, hd, body = ws_handshake(NGX_OFF)
    j = json.loads(body) if body.strip().startswith("{") else {}
    ok("nginx ohne \"Websockets Support\": 426 websocket_required mit Hinweis", st == 426 and j.get("error") == "websocket_required"
       and "Websockets Support" in j.get("text", ""), (st, body[:300]))
    st, body = https_get(NGX, "/api/sfu/ws")
    ok("GET ohne Upgrade auch ueber den richtigen Proxy: 426", st == 426, (st, body[:200]))
    # 101 mit Websockets an, Unterprotokoll, Zugang
    st, hd, s = ws_handshake(NGX, "saga-frames.1")
    ok("nur saga-frames.1: 400 subprotocol (reserviert)", st == 400 and "subprotocol" in (s if isinstance(s, str) else ""), (st, s if isinstance(s, str) else ""))
    st, hd, s = ws_handshake(NGX)
    good = st == 101 and hd.get("sec-websocket-protocol") == "saga-tunnel.1"
    ok("nginx mit \"Websockets Support\": 101 saga-tunnel.1 bis zum Knoten", good, (st, hd))
    if good:
        ws_send(s, 1, json.dumps({"t": "auth", "v": 1, "mode": "ice", "user": "1:x", "cred": "falsch"}).encode())
        op, d = ws_recv(s)
        r = json.loads(d) if op == 1 else {}
        op2, d2 = ws_recv(s)
        code = int.from_bytes(d2[:2], "big") if op2 == 8 and len(d2) >= 2 else 0
        ok("falscher Zugang: err auth, Close 4401", r.get("t") == "err" and r.get("code") == "auth" and code == 4401, (r, code))
        s.close()
    time.sleep(6)                                          # Lastmeldung alle 5 s
    tu = state().get("tunnel") or {}
    ok("Admin-Zustand: Tunnel-Betrieb, Anfragen ohne Upgrade gezaehlt, Warnung", tu.get("mode") == "tunnel" and tu.get("no_upgrade", 0) >= 2
       and tu.get("auth_fail", 0) >= 1 and tu.get("ws_warn") is True, tu)
    _, html, _, _ = adm("GET", "/calls")
    i = html.find('id="tunnel"')
    card = html[i:html.find("</div>", i)] if i >= 0 else ""
    ok("Admin -> Anrufe: Kasten \"Anrufe über die Saga-Adresse (Tunnel)\" mit Warnung \"Wohl nicht\"",
       "Anrufe über die Saga-Adresse (Tunnel)" in card and "Wohl nicht" in card and "Websockets Support" in card, card[:1200])
    # Medien ueber die ganze Kette (pion, Go-Test) mit den Token von Saga
    toks = os.path.join(STATE, "tokens.json")
    devs = []
    for p in people[:2]:
        c = (cands.get(p.uid) or [{}])[0]
        devs.append({"audio": c["tokens"]["audio"], "video": c["tokens"]["video"]})
    json.dump(devs, open(toks, "w"))
    env = dict(os.environ, A3_URL=BASE, A3_DIAL=f"127.0.0.1:{NGX}", A3_TOKENS=toks, A3_SECS="6", CGO_ENABLED="0", GOPROXY="direct",
               GOSUMDB="off", GOFLAGS="-mod=mod", GOTOOLCHAIN="local")
    cmd = ["go", "test", "-count=1", "-run", "TestA3Kette", "-v", "."]
    if os.environ.get("A3_GOMOD"):
        cmd[2:2] = ["-modfile=" + os.environ["A3_GOMOD"]]
    r = subprocess.run(cmd, cwd=os.path.join(APP, "gjallarhorn"), capture_output=True, text=True, env=env, timeout=600)
    info = [ln.strip() for ln in r.stdout.splitlines() if "tunnel_test.go" in ln or ln.startswith(("--- ", "ok", "FAIL"))]
    ok("zwei Geraete mit Saga-Token ueber nginx -> saga-media -> Knoten (Weg ws): verbunden, Medien in beide Richtungen",
       r.returncode == 0 and "--- PASS: TestA3Kette" in r.stdout, info or r.stdout[-1500:] + r.stderr[-800:])
    for ln in info:
        print("   ", ln)
    tu = wait(lambda: (lambda x: x if x.get("total", 0) >= 4 else None)(state().get("tunnel") or {}), 12)
    ok("Admin-Zustand: Tunnel gezaehlt (seit dem Start >= 4), letzter Tunnel nach der Warnung -> keine Warnung mehr",
       bool(tu) and tu.get("ws_warn") is False and tu.get("last_open"), tu)
    # Proxy-Check-CSP und Bild
    s_, html, ck, hdrs = adm("GET", "/calls")
    csp = hdrs.get("content-security-policy", "")
    ok("Admin -> Anrufe: CSP erlaubt die Pruefung gegen die Saga-Adresse (https + wss)", f"connect-src 'self' {BASE} wss://{NAME}:{NGX}" in csp, csp)
    ok("Knotenzeile: Weg \"Tunnel ✓\", keine Port-Tabelle fuer den Knoten", "Tunnel ✓" in html and f'id="n-{n["id"]}"' not in html)
    if os.environ.get("A3_SHOTS"):
        shot(ck, "nachher")
    # Grenze je echter Adresse (X-Forwarded-For vom vertrauten Proxy; nginx haengt die eigene Gegenstelle an)
    s10(people, cid)


def shot(ck, name):
    js = os.path.join(APP, "tests", "a3_bilder.js")
    env = dict(os.environ, A3_ADMIN=f"http://127.0.0.1:{ADMIN}", A3_COOKIE=ck, A3_OUT=os.path.join(os.environ["A3_SHOTS"], f"a3-admin-{name}.png"),
               A3_HOST_RULES=f"MAP {NAME} 127.0.0.1", A3_CLICK="1", NO_PROXY="*")
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=120)
    for ln in r.stdout.splitlines():
        if ln.startswith(("OK  ", "FAIL")):
            print("  [bild] " + ln, flush=True)
            res.append(ln.startswith("OK"))
    if r.returncode:
        ok("Bild " + name, False, (r.stdout + r.stderr)[-1500:])


# --- Teil 2: eigener Name - wie bisher, ws zusaetzlich -----------------------------------------------------------------
def teil2(media, node):
    stop()
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE builtin=1")
    t = start(media, node, {"HORN_NAME": "localhost", "HORN_TLS_SELF": "1", "HORN_LISTEN": f"127.0.0.1:{HORN_PORT}"})
    ok("Teil 2: Instanz mit eigenem Namen fuer den Knoten (HORN_TLS_SELF) laeuft", t is not None)
    n = wait(lambda: (lambda b: b if b and b.get("ctl") else None)(builtin()), 30)
    ok("eigener Name: kein Tunnel-Betrieb, eigener Port, ICE-TCP an, ws zusaetzlich", bool(n) and not n.get("tunnel") and n.get("ice_tcp") is True
       and n.get("ws") is True and n.get("url") == f"https://localhost:{HORN_PORT}", n)
    st, hd, s = ws_handshake(NGX)
    ok("Geraete-HTTP des Knotens auch hier ueber die Saga-Adresse (101)", st == 101, (st, hd))
    if st == 101:
        s.close()


def main():
    import db
    db.wait_ready()
    db.init_schema()
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.get_user("ca-a3-adm") or auth.create_user("ca-a3-adm", secrets.token_urlsafe(12), "Admin A3", is_admin=True)
    for u, nm in USERS:
        auth.create_user(u, PW, nm)
    teil0()
    media, node = build("saga-media", "media", "SAGA_MEDIA_BIN"), build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok("saga-media und gjallarhorn gebaut", bool(media and node))
    nd = os.path.dirname(NGINX)
    os.makedirs(nd, exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", os.path.join(nd, "key.pem"),
                    "-out", os.path.join(nd, "cert.pem"), "-days", "2", "-subj", "/CN=" + NAME, "-addext", f"subjectAltName=DNS:{NAME},IP:127.0.0.1"],
                   capture_output=True)
    tpl = open(os.path.join(APP, "gjallarhorn", "e2e", "npm-nginx.conf.tmpl"), encoding="utf-8").read()
    open(NGINX, "w").write(tpl.replace("{DIR}", nd).replace("{NGX_OFF}", str(NGX_OFF)).replace("{NGX}", str(NGX))
                           .replace("{UPSTREAM}", f"127.0.0.1:{PORT}"))
    started_ngx = subprocess.run(["nginx", "-c", NGINX], capture_output=True, text=True)
    P["ngx"] = started_ngx.returncode == 0
    ok("nginx (NPM-Nachbau) gestartet", P["ngx"], started_ngx.stderr[-400:])
    try:
        if media and node and P["ngx"]:
            teil1(media, node)
            teil2(media, node)
    finally:
        stop()
        if P["ngx"]:
            subprocess.run(["nginx", "-c", NGINX, "-s", "stop"], capture_output=True)
        errs = len(re.findall(r"Traceback|panic:|DATA RACE", log_text()))
        ok(f"Instanz: Serverfehler {errs}", errs == 0)
        if errs:
            print(log_text()[-5000:])
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE builtin=1")
        shutil.rmtree(STATE, ignore_errors=True)
    print(f"\nA3-TUNNEL {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
