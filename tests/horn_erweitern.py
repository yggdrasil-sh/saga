"""Gjallarhorn P6-P8 (Konzept v2 §2.2-2.9, §3.4, §4.1): Erweitern/Kopplung, Steuerkanal gekoppelter Knoten, Lastmeldung,
Waechter-Update (signiertes Binaer), Leeren, Entfernen, Cloudflare-DNS, ACME DNS-01 ueber Saga, Admin-Seite "Anrufe".

Eigene Instanz auf derselben Datenbank: saga-media auf 8071 (uvicorn intern 8072), Admin 8073. Nur lokale Attrappen, keine
echten Netzaufrufe: Fake-Cloudflare (18310, SAGA_CF_API), Fake-ACME (gjallarhorn/tools/fakeacme, dns-01 gegen den
Fake-Cloudflare), Release-Server (18311) mit einem TESTSCHLUESSEL (wird hier erzeugt, nie gespeichert; die Binaere sind
mit -X main.releaseKeyOverride gebaut). Der weitere Knoten horn-2.example.org entsteht wie bei Sven auf CT150: das
systemd-Deploy-Skript aus dem Admin-Bereich laeuft mit HORN_ROOT (Simulation ohne systemd, SYSTEMCTL=true), danach
startet der Test die ExecStart-Zeile der erzeugten Unit (gjallarhorn watch) mit der erzeugten Umgebung.

Geprueft:
- Kopplung: Code 32 Byte, nur SHA-256 in der Datenbank, einmalig (zweiter Versuch 403), abgelaufen (410), falscher Name
  (403), unbekannt (403), falsche Signatur (400), unerreichbar (424, Neuversuch nur mit demselben Knotenschluessel),
  Deploy-Skripte (Image, Ports, kein Cloudflare-Token, Signaturpruefung per openssl), whoami.
- Steuerkanal: falscher Knotenschluessel 4401, wiederholte hello-Nachricht (alte Challenge) 4401, Wiederholung/Luecke der
  Folgenummer trennt, gesperrter Schluessel 4410, welcome mit Zielversion/solo/Zertifikatsweg, Lastmeldung im Register,
  ACME-TXT nur fuer den eigenen Namen.
- DNS (Fake): Token-Pruefung (gueltig, zu viele Zonen, inaktiv, nur lesen), verschluesselt gespeichert, maskiert angezeigt,
  A mit proxied=false/TTL 120/Kommentar saga-horn:<id>, Nachfuehren bei neuer Adresse, Aufraeumen beim Entfernen (nur
  eigene Kommentare), TXT fuer DNS-01 gesetzt und wieder geloescht.
- Ende zu Ende: Kopplung des zweiten gjallarhorn-Prozesses mit Live-Fortschritt bis "Fertig", Zertifikat per DNS-01 vom
  Fake-ACME, Betrieb nur mit externem Knoten (eingebauter Schalter aus), Gruppenanruf mit 5 Browsern ueber horn-2,
  Leeren (Umzugsereignis an die Clients, keine neuen Beine), Update: falsche Signatur abgelehnt, richtige uebernommen
  (Neustart durch den Waechter erst ohne Beine, neue Version im Register), Entfernen (Schluessel weg, Waechter startet
  nicht mehr, DNS aufgeraeumt).
Wegwerf-Konten ca-e*."""
import base64
import glob
import hashlib
import http.client
import http.server
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT, INTERNAL, ADMIN, HORN0, HORN2 = 8071, 8072, 8073, 8473, 8474
CFP, RELP = 18310, 18311
os.environ["HK_TEST_BASE"] = f"http://127.0.0.1:{PORT}"
os.environ["SAGA_CF_API"] = f"http://127.0.0.1:{CFP}/client/v4"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402

REL_SEED = os.urandom(32)                                          # Testschluessel (nur in diesem Lauf)
REL_PUB = base64.b64encode(Ed25519PrivateKey.from_private_bytes(REL_SEED).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
os.environ["SAGA_HORN_RELEASE_PUB"] = REL_PUB
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import cloudflare as CF  # noqa: E402
from saga import horn as HN  # noqa: E402
from version import VERSION  # noqa: E402

req, res = A_.req, A_.res
SECRET = os.environ["SESSION_SECRET"]
STATE = tempfile.mkdtemp(prefix="hornp6-")
NAME = "horn-2.example.org"
PW = "Horn-Erweitern-Pass-1234"
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CALLS = {"enabled": True, "stun": "", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "", "turn_password": "",
         "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_builtin": False}
USERS = [("ca-e1", "Anna Erweitern"), ("ca-e2", "Bert Erweitern"), ("ca-e3", "Cora Erweitern"), ("ca-e4", "Dora Erweitern"),
         ("ca-e5", "Emil Erweitern"), ("ca-e6", "Fritz Erweitern")]
BUSERS = [("ca-eb1", "Ada Knoten2"), ("ca-eb2", "Ben Knoten2"), ("ca-eb3", "Cleo Knoten2"), ("ca-eb4", "Dirk Knoten2"), ("ca-eb5", "Eva Knoten2")]
PROCS = {}
PERF = {}


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:1500]), flush=True)


def wait(fn, t, step=0.25):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


# --- Fake-Cloudflare -------------------------------------------------------------------------------------------------
CFD = {"zones": {"z1": "example.org", "z2": "example.net"}, "rec": {}, "log": []}
TOK = {"good": "cftest-good-" + secrets.token_hex(12), "multi": "cftest-multi-" + secrets.token_hex(12),
       "off": "cftest-off-" + secrets.token_hex(12), "ro": "cftest-ro-" + secrets.token_hex(12)}
TOKZ = {TOK["good"]: ["z1"], TOK["multi"]: ["z1", "z2"], TOK["off"]: ["z1"], TOK["ro"]: ["z1"]}
CFL = threading.Lock()


class FakeCF(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _out(self, code, d):
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _err(self, code, msg):
        self._out(code, {"success": False, "errors": [{"code": code, "message": msg}], "result": None})

    def _handle(self, m):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/_txt":
            with CFL:
                vals = [r["content"] for r in CFD["rec"].values() if r["type"] == "TXT" and r["name"] == q.get("name")]
            return self._out(200, vals)
        tok = (self.headers.get("Authorization") or "")[7:]
        with CFL:
            CFD["log"].append((m, u.path, tok[:12]))
        if tok not in TOKZ:
            return self._err(401, "Invalid API Token")
        p = u.path.removeprefix("/client/v4")
        if p == "/user/tokens/verify":
            return self._out(200, {"success": True, "result": {"id": "x", "status": "disabled" if tok == TOK["off"] else "active"}})
        if p == "/zones":
            return self._out(200, {"success": True, "result": [{"id": z, "name": CFD["zones"][z]} for z in TOKZ[tok]]})
        mm = re.fullmatch(r"/zones/(\w+)/dns_records(?:/(\w+))?", p)
        if not mm or mm.group(1) not in TOKZ[tok]:
            return self._err(404, "not found")
        zid, rid = mm.groups()
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"null") if n else None
        with CFL:
            if m == "GET":
                rs = [r for r in CFD["rec"].values() if r["zone"] == zid and (not q.get("name") or r["name"] == q["name"])
                      and (not q.get("type") or r["type"] == q["type"]) and ("comment.exact" not in q or r.get("comment") == q["comment.exact"])]
                return self._out(200, {"success": True, "result": rs})
            if tok == TOK["ro"]:
                return self._err(403, "Authentication error")
            if m == "POST":
                rid = secrets.token_hex(8)
                CFD["rec"][rid] = dict(body, id=rid, zone=zid)
                return self._out(200, {"success": True, "result": CFD["rec"][rid]})
            if rid not in CFD["rec"]:
                return self._err(404, "record not found")
            if m == "PATCH":
                CFD["rec"][rid].update(body)
                return self._out(200, {"success": True, "result": CFD["rec"][rid]})
            if m == "DELETE":
                CFD["rec"].pop(rid)
                return self._out(200, {"success": True, "result": {"id": rid}})
        return self._err(405, "method")

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")


def recs(typ=None, name=None):
    with CFL:
        return [dict(r) for r in CFD["rec"].values() if (typ is None or r["type"] == typ) and (name is None or r["name"] == name)]


# --- Release-Server (Testschluessel) ------------------------------------------------------------------------------------
REL = {}                                                         # Pfad -> Bytes


class FakeRel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = REL.get(urllib.parse.urlparse(self.path).path)
        if b is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def sign_rel(ver, data, arch="linux-amd64"):
    msg = f"gjallarhorn-release/1\n{ver}\n{arch}\n{hashlib.sha256(data).hexdigest()}\n".encode()
    return (base64.b64encode(Ed25519PrivateKey.from_private_bytes(REL_SEED).sign(msg)).decode() + "\n").encode()


def serve(handler, port):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --- Bauen, Instanz ----------------------------------------------------------------------------------------------------
def gobuild(d, out, ldflags=""):
    r = subprocess.run(["go", "build", "-trimpath", "-ldflags", ldflags, "-o", out, "."], cwd=os.path.join(APP, d), capture_output=True,
                       text=True, env=dict(os.environ, CGO_ENABLED="0"))
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
        return None
    return out


def gobuild_tool(path, out):
    r = subprocess.run(["go", "build", "-trimpath", "-o", out, path], cwd=os.path.join(APP, "gjallarhorn"), capture_output=True, text=True,
                       env=dict(os.environ, CGO_ENABLED="0"))
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
        return None
    return out


def start_saga(media, node0):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL") and not k.startswith("HORN_")}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT=str(ADMIN), ADMIN_HOST="127.0.0.1", HOST="127.0.0.1",
               STATE_DIR=STATE, BASE_URL=f"http://127.0.0.1:{PORT}", ADMIN_URL=f"http://127.0.0.1:{ADMIN}", CANONICAL_REDIRECT="false",
               SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0", PYTHONPATH=APP, SECRET_KEY=SECRET, SESSION_SECRET=SECRET,
               GJALLARHORN_BIN=node0, HORN_PORT=str(HORN0), HORN_NAME="localhost", HORN_TLS_SELF="1", HORN_UDP="0", HORN_ICE_IPS="127.0.0.1",
               SAGA_CF_API=os.environ["SAGA_CF_API"], SAGA_HORN_RELEASE_PUB=REL_PUB)
    log = open(os.path.join(STATE, "server.log"), "a")
    PROCS["saga"] = (subprocess.Popen([media, "--", sys.executable, "run.py"], env=env, cwd=APP, stdout=log, stderr=subprocess.STDOUT), log)
    return wait(lambda: _up(PORT, "/healthz") and _up(ADMIN, "/healthz"), 40, 0.1)


def _up(port, path):
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        c.request("GET", path)
        return c.getresponse().status == 200
    except OSError:
        return False


def stop(key):
    p = PROCS.pop(key, None)
    if not p:
        return
    proc, log = p
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(25)
        except subprocess.TimeoutExpired:
            proc.kill()
    if log:
        log.close()


def text(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def children(pid):
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            st = open(d + "/stat").read()
            if int(st.rsplit(") ", 1)[1].split()[1]) == pid:
                out.append((int(d[6:]), open(d + "/cmdline", "rb").read().split(b"\0")))
        except (OSError, ValueError, IndexError):
            continue
    return out


def builtin_pid():
    return next((p for p, cmd in children(PROCS["saga"][0].pid) if cmd and cmd[0].endswith(b"gjallarhorn")), 0)


# --- Admin-Bereich --------------------------------------------------------------------------------------------------
def admin_cookie():
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    return sa.sign(base64.b64encode(json.dumps({"aid": "ca-eadm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def adm(m, path, body=None, form=None, lang="de"):
    c = http.client.HTTPConnection("127.0.0.1", ADMIN, timeout=60)
    h = {"Cookie": "hk_admin=" + admin_cookie(), "Accept-Language": lang, "X-CSRF-Token": "x"}
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"
        data = json.dumps(body)
    if form is not None:
        h["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(dict(form, csrf="x"))
    c.request(m, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read().decode("utf-8", "replace")
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def pair_post(body):
    return req("POST", "/api/horn/pair", None, body, {"X-Saga": "1"})


def pair_body(tok, name, sk=None, **kw):
    sk = sk or Ed25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return dict({"token": tok, "name": name, "node_pub": pub, "sig": base64.b64encode(sk.sign(f"horn-pair/1\n{tok}\n{name}".encode())).decode(),
                 "v4": "127.0.0.1", "v6": "", "port": 1, "udp": False, "version": VERSION, "cores": 2, "link_mbit": 100}, **kw), sk


def node_entry(name, sk, nid=None):
    """Gekoppelten Knoten direkt im Register anlegen (Steuerkanal-Tests ohne echten Knoten)."""
    nid = nid or secrets.token_hex(8)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, state, created, port) VALUES (?,?,?,0,?,'neu',?,443)",
                  (nid, name, pub, f"https://{name}", time.time()))
    return nid, pub


# --- Steuerkanal-Client (Attrappe eines Knotens) -----------------------------------------------------------------------
def ctl_open():
    from websockets.sync.client import connect as wsc
    return wsc(f"ws://127.0.0.1:{PORT}/api/horn/ctl", additional_headers={"X-Saga": "1"}, open_timeout=5, proxy=None)


def ctl_hello(ws, sk, name, nonce=None, **kw):
    ch = json.loads(ws.recv(5))
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    n = nonce or ch["nonce"]
    ws.send(json.dumps(dict({"t": "hello", "proto": "horn-ctl/1", "name": name, "url": f"https://{name}", "pub": pub,
                             "sig": base64.b64encode(sk.sign(f"horn-ctl/1 hello\n{n}".encode())).decode(), "version": VERSION,
                             "udp": False, "max_legs": 48, "cert": {"mode": "dns01", "state": "self"}}, **kw)))
    return ch


def ctl_recv(ws, t=5):
    try:
        return json.loads(ws.recv(t))
    except Exception as ex:  # noqa: BLE001
        return {"closed": getattr(getattr(ex, "rcvd", None), "code", None) or type(ex).__name__}


def ctl_code(ws):
    """Schliesscode nach Ende der Verbindung."""
    try:
        while True:
            ws.recv(5)
    except Exception as ex:  # noqa: BLE001
        rc = getattr(ex, "rcvd", None)
        return rc.code if rc else type(ex).__name__


# --- Ablauf -----------------------------------------------------------------------------------------------------------
def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls", "horn_cf")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(CALLS, horn=False))
    CF.clear()
    for u, n in USERS + BUSERS:
        auth.create_user(u, PW, n)
    auth.create_user("ca-eadm", PW, "Admin Erweitern", is_admin=True)
    cfs, rels = serve(FakeCF, CFP), serve(FakeRel, RELP)
    ok_go = bool(shutil.which("go"))
    ok(f"Go vorhanden ({ok_go})", ok_go)
    try:
        if ok_go:
            run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k in list(PROCS):
            stop(k)
        cfs.shutdown()
        rels.shutdown()
        errs = text(os.path.join(STATE, "server.log")).count("Traceback")
        ok(f"Instanz: Serverfehler {errs}", errs == 0)
        if errs:
            print(text(os.path.join(STATE, "server.log"))[-6000:])
        for k, v in keep.items():
            if k == "horn_cf":
                CF.clear()
            elif v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM users WHERE username='ca-eadm'")
            c.execute("DELETE FROM horn_node WHERE name LIKE '%.example.org' OR (builtin=1 AND name='localhost')")
            c.execute("DELETE FROM horn_pair WHERE name LIKE '%.example.org'")
            c.execute("DELETE FROM horn_log WHERE name LIKE '%.example.org'")
        shutil.rmtree(STATE, ignore_errors=True)
    print("PERF " + json.dumps(PERF))
    print(f"\nHORN-ERWEITERN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    media = gobuild("media", os.path.join(STATE, "saga-media"))
    lf = f"-X main.releaseKeyOverride={REL_PUB}"
    node0 = gobuild("gjallarhorn", os.path.join(STATE, "gjallarhorn"), lf + f" -X main.version={VERSION}")
    alt = gobuild("gjallarhorn", os.path.join(STATE, "gjallarhorn-alt"), lf + f" -X main.version={VERSION}-alt")
    acme = gobuild_tool("./tools/fakeacme", os.path.join(STATE, "fakeacme"))
    ok("saga-media, gjallarhorn (aktuell + alt, Testschluessel) und Fake-ACME gebaut", media and node0 and alt and acme)
    if not (media and node0 and alt and acme):
        return
    t = start_saga(media, node0)
    ok(f"Instanz laeuft (Saga + Admin, {t})", t)
    if not t:
        print(text(os.path.join(STATE, "server.log"))[-3000:])
        return
    dns_check()
    pairing_api()
    control_channel()
    builtin_switch()
    e2e(node0, alt, acme)
    admin_pages()


def pairing_api():
    s, d = req("GET", "/api/horn/whoami", None, None, {"X-Saga": "1"})
    ok(f"whoami: eigene Adresse ({s}, {d})", s == 200 and d.get("ip") == "127.0.0.1")
    s, p = adm("POST", "/api/calls/pair", {"name": "horn-7", "share": False, "udp": True})
    tok = p.get("token", "") if isinstance(p, dict) else ""
    raw = base64.urlsafe_b64decode(tok[4:] + "=" * (-len(tok[4:]) % 4)) if tok.startswith("hkh_") else b""
    ok(f"Erweitern: Kopplungs-Code mit 32 Byte, 30 min, Name mit Domain ({s}, {p.get('name') if isinstance(p, dict) else p})",
       s == 200 and len(raw) == 32 and p["name"] == "horn-7.example.org" and 1790 < p["expires"] - time.time() <= 1800)
    with connect() as c:
        rows = c.execute("SELECT token_hash, name, used, saga FROM horn_pair WHERE name='horn-7.example.org'").fetchall()
        dump = json.dumps([list(r) for r in c.execute("SELECT * FROM horn_pair").fetchall()], default=str)
    ok("Datenbank: nur SHA-256 des Codes, gebunden an Name und Saga-Adresse", rows and rows[0][0] == hashlib.sha256(tok.encode()).hexdigest()
       and tok not in dump and tok[4:] not in dump and rows[0][3] == f"http://127.0.0.1:{PORT}")
    dk, sd = p.get("script_docker", ""), p.get("script_systemd", "")
    ok("Deploy-Skript Docker: compose + .env, Image mit Saga-Version, 443/tcp + 443/udp, Waechter, Code, kein curl | sh",
       f"ghcr.io/yggdrasil-sh/gjallarhorn:{VERSION}" in dk and '"443:443/tcp"' in dk and '"443:443/udp"' in dk and 'command: ["watch"]' in dk
       and f"HORN_PAIR={tok}" in dk and "HORN_NAME=horn-7.example.org" in dk and "docker compose up -d" in dk and "| sh" not in dk)
    ok("Deploy-Skript systemd: Binaer + Signatur (openssl, Release-Schluessel), Unit mit watch, eigener Benutzer",
       "openssl pkeyutl -verify" in sd and "BEGIN PUBLIC KEY" in sd and "ExecStart=/opt/gjallarhorn/gjallarhorn watch" in sd
       and "useradd --system" in sd and "CAP_NET_BIND_SERVICE" in sd and f"HORN_PAIR={tok}" in sd)
    ok("kein Cloudflare-Token im Skript", not any(x in dk + sd for x in TOK.values()))
    s, d = pair_post(pair_body("hkh_" + "A" * 43, "horn-7.example.org")[0])
    ok(f"unbekannter Code -> 403 ({s}, {d.get('code') if isinstance(d, dict) else d})", s == 403 and d.get("code") == "token")
    b, _ = pair_body(tok, "horn-7.example.org")
    b["sig"] = base64.b64encode(b"\0" * 64).decode()
    s, d = pair_post(b)
    ok(f"falsche Signatur -> 400 ({s})", s == 400 and d.get("code") == "sig")
    s, d = pair_post(pair_body(tok, "horn-8.example.org")[0])
    ok(f"falscher Name -> 403 ({s}, {d.get('code')})", s == 403 and d.get("code") == "name")
    sk1 = Ed25519PrivateKey.generate()
    s, d = pair_post(pair_body(tok, "horn-7.example.org", sk1, port=1)[0])
    ok(f"Knoten nicht erreichbar -> 424, der Code bleibt fuer diesen Knotenschluessel ({s}, {d.get('code')})", s == 424 and d.get("code") == "reach")
    s, d = pair_post(pair_body(tok, "horn-7.example.org")[0])
    ok(f"... anderer Knotenschluessel mit demselben Code -> 403 ({s})", s == 403 and d.get("code") == "token")
    s, d = adm("GET", f"/api/calls/pair/{p['id']}")
    ok(f"Fortschritt: Knoten gemeldet, 443/TCP nicht erreichbar ({s})", s == 200 and any(x["k"] == "tcp" and x["s"] == "err" for x in d["steps"]))
    s, p2 = adm("POST", "/api/calls/pair", {"name": "horn-6.example.org"})
    with connect() as c, c:
        c.execute("UPDATE horn_pair SET expires=? WHERE id=?", (time.time() - 1, p2["id"]))
    s, d = pair_post(pair_body(p2["token"], "horn-6.example.org")[0])
    ok(f"abgelaufener Code -> 410 ({s}, {d.get('code')})", s == 410 and d.get("code") == "expired")
    s, d = adm("POST", "/api/calls/pair", {"name": "horn-7.example.org"})
    ok(f"Name schon vergeben (offener Code) -> 400 ({s})", s == 400 and d.get("code") == "name_used")
    s, d = adm("POST", "/api/calls/pair", {"name": "127.0.0.1"})
    ok(f"ungueltiger Name -> 400 ({s})", s == 400 and d.get("code") == "name")
    s, _ = adm("POST", "/calls", form={"action": "pair_cancel", "id": p["id"]})
    s, d = pair_post(pair_body(tok, "horn-7.example.org", sk1, port=1)[0])
    ok(f"abgebrochener Code gilt nicht mehr ({s})", s == 410)
    srvlog = text(os.path.join(STATE, "server.log"))
    ok("Server-Log ohne Kopplungs-Code", tok not in srvlog and p2["token"] not in srvlog)


def control_channel():
    sk, wrong = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    nid, pub = node_entry("horn-5.example.org", sk)
    ws = ctl_open()
    ctl_hello(ws, wrong, "horn-5.example.org")
    cc = ctl_code(ws)
    ok(f"Steuerkanal: fremder Knotenschluessel -> 4401 ({cc})", cc == 4401)
    ws = ctl_open()
    ch = ctl_hello(ws, sk, "horn-5.example.org")
    w = ctl_recv(ws)
    ok(f"registrierter Schluessel -> welcome mit Zielversion, solo, Zertifikatsweg ({w.get('t')}, {w.get('want')}, {w.get('cert')})",
       w.get("t") == "welcome" and w.get("node_id") == nid and w.get("want") == VERSION and w.get("solo") is True and w.get("cert") == "dns01"
       and w.get("saga_pub") == HN.saga_pub())
    ws.send(json.dumps({"t": "load", "n": 1, "load": {"cpu": 0.12, "mem": 1, "up": 18.0, "down": 9.0, "legs": 0, "calls": 0, "udp_share": 0,
                                                     "fwd_p95_us": 3.1, "drops": 0, "max_legs": 48, "state": "ok", "boese": "x" * 50}}))
    time.sleep(0.5)
    st = live("horn-5.example.org")
    ok(f"Lastmeldung im Register (nur bekannte Felder, Upload fuer die Auslastung) ({st.get('cpu')}, {st.get('up')})",
       st.get("ctl") and st.get("up") == 18.0 and st.get("cpu") == 12)
    s_, raw = adm("GET", "/api/calls/state")
    ok("... unbekannte Felder verworfen", "boese" not in json.dumps(raw))
    ws.send(json.dumps({"t": "acme_txt", "n": 2, "rid": "r1", "op": "set", "name": "_acme-challenge.horn-9.example.org", "value": "A" * 43}))
    r = ctl_recv(ws)
    while r.get("t") == "peers":
        r = ctl_recv(ws)
    ok(f"ACME-TXT fuer einen fremden Namen abgelehnt ({r})", r.get("t") == "acme_txt" and r.get("rid") == "r1" and not r.get("ok"))
    ws.send(json.dumps({"t": "acme_txt", "n": 3, "rid": "r2", "op": "set", "name": "_acme-challenge.horn-5.example.org", "value": "B" * 43}))
    r = ctl_recv(ws)
    t5 = recs("TXT", "_acme-challenge.horn-5.example.org")
    ok(f"ACME-TXT fuer den eigenen Namen gesetzt, mit Kommentar ({r}, {t5})", r.get("ok") and len(t5) == 1 and t5[0]["content"] == "B" * 43
       and t5[0]["comment"] == "saga-horn:" + nid)
    ws.send(json.dumps({"t": "acme_txt", "n": 4, "rid": "r3", "op": "del", "name": "_acme-challenge.horn-5.example.org"}))
    r = ctl_recv(ws)
    ok(f"... und wieder geloescht ({r})", r.get("ok") and not recs("TXT", "_acme-challenge.horn-5.example.org"))
    ws.send(json.dumps({"t": "load", "n": 4, "load": {"legs": 0}}))
    cc = ctl_code(ws)
    ok(f"Wiederholung einer Folgenummer -> Verbindung zu ({cc})", cc == 1000)
    time.sleep(0.4)
    ok("... Knoten danach als getrennt gefuehrt", not live("horn-5.example.org").get("ctl"))
    ws = ctl_open()
    ctl_hello(ws, sk, "horn-5.example.org", nonce=ch["nonce"])
    cc = ctl_code(ws)
    ok(f"Wiederholung einer alten hello-Nachricht (alte Challenge) -> 4401 ({cc})", cc == 4401)
    ws = ctl_open()
    ctl_hello(ws, sk, "horn-5.example.org")
    ctl_recv(ws)
    ws.send(json.dumps({"t": "load", "n": 5, "load": {"legs": 0}}))
    cc = ctl_code(ws)
    ok(f"Luecke in der Folgenummer -> Verbindung zu ({cc})", cc == 1000)
    ws = ctl_open()
    ctl_hello(ws, sk, "horn-4.example.org")
    cc = ctl_code(ws)
    ok(f"richtiger Schluessel, anderer Name -> 4409 ({cc})", cc == 4409)
    with connect() as c, c:
        c.execute("INSERT INTO horn_ban (pub, node_id, at) VALUES (?,?,?)", (pub, nid, time.time()))
    ws = ctl_open()
    ctl_hello(ws, sk, "horn-5.example.org")
    cc = ctl_code(ws)
    ok(f"gesperrter Schluessel -> 4410 ({cc})", cc == 4410)
    with connect() as c, c:
        c.execute("DELETE FROM horn_ban WHERE pub=?", (pub,))
        c.execute("DELETE FROM horn_node WHERE id=?", (nid,))
    # Leistung: Lastmeldung klein, Steuerkanal billig
    ld = {"cpu": 0.123, "mem": 123456789, "up": 18.25, "down": 9.5, "legs": 24, "calls": 2, "peers": 12, "udp_share": 0.5,
          "fwd_p95_us": 3.25, "drops": 0, "max_legs": 48, "state": "ok"}
    PERF["lastmeldung_bytes"] = len(json.dumps({"t": "load", "n": 123456, "load": ld}, separators=(",", ":")))
    ok(f"Lastmeldung bleibt klein ({PERF['lastmeldung_bytes']} B, Ziel ~200)", PERF["lastmeldung_bytes"] <= 260)


def dns_check():
    def save(tok, zone=""):
        r = adm("POST", "/calls", form={"action": "cf_save", "cf_token": tok, **({"cf_zone": zone} if zone else {})})
        auth._settings_cache["v"] = None                  # Einstellungen der anderen Instanz frisch lesen
        return r
    s, html = save(TOK["off"])
    ok(f"Cloudflare: inaktiver Token nicht gespeichert ({s})", not CF.configured())
    save(TOK["ro"])
    ok("Cloudflare: Token ohne Schreibrecht (Probe-Eintrag scheitert) nicht gespeichert", not CF.configured())
    s, html = save(TOK["multi"])
    ok(f"Cloudflare: Token sieht 2 Zonen -> Zone waehlen ({s})", s == 200 and 'name="cf_zone"' in html and "zu viele Rechte" in html)
    save(TOK["multi"], "z1")
    c1 = CF.conf()
    ok(f"... mit gewaehlter Zone gespeichert, Warnung zu viele Rechte ({c1.get('zone')}, {c1.get('check')})", c1.get("zone") == "example.org"
       and (c1.get("check") or {}).get("warn") == "zones")
    save(TOK["good"])
    c2 = CF.conf()
    with connect() as c:
        raw = c.execute("SELECT v FROM setting WHERE k='horn_cf'").fetchone()[0]
    ok("gueltiger Token: verschluesselt gespeichert (enc:), Zone example.org, DNS bearbeiten geprueft",
       c2.get("token") == TOK["good"] and TOK["good"] not in raw and '"enc:' in raw and (c2.get("check") or {}).get("dns_edit"))
    s, html = adm("GET", "/calls")
    ok("Admin zeigt den Token nur maskiert", s == 200 and "••••••••" + TOK["good"][-4:] in html and TOK["good"] not in html
       and "Token gültig · Zone example.org · Rechte: DNS bearbeiten ✓" in html)
    ok("Probe-Eintrag wieder geloescht", not recs("TXT"))
    # Eintraege ueber die Hilfsfunktionen (ohne Knoten) - nur eigene Kommentare
    nid = "testdns" + secrets.token_hex(4)
    CF.upsert(nid, "horn-3.example.org", "A", "192.0.2.10")
    with CFL:
        CFD["rec"]["fremd"] = {"id": "fremd", "zone": "z1", "type": "A", "name": "horn-3.example.org", "content": "192.0.2.99", "comment": "anderes"}
    a = recs("A", "horn-3.example.org")
    own = [r for r in a if r.get("comment") == "saga-horn:" + nid]
    ok(f"A-Eintrag: graue Wolke, TTL 120, Kommentar saga-horn:<id> ({own})", len(own) == 1 and own[0]["proxied"] is False and own[0]["ttl"] == 120)
    CF.upsert(nid, "horn-3.example.org", "A", "192.0.2.11")
    own = [r for r in recs("A", "horn-3.example.org") if r.get("comment") == "saga-horn:" + nid]
    ok("Nachfuehren aendert den Eintrag (kein zweiter)", len(own) == 1 and own[0]["content"] == "192.0.2.11")
    n = CF.delete_all(nid)
    ok(f"Aufraeumen loescht nur eigene Eintraege ({n})", n == 1 and [r["id"] for r in recs("A", "horn-3.example.org")] == ["fremd"])
    with CFL:
        CFD["rec"].pop("fremd", None)


def builtin_switch():
    auth.save_setting("calls", dict(CALLS, horn_builtin=True))
    pid = wait(builtin_pid, 20)
    ok(f"Schalter eingebauter Knoten an: saga-media startet ihn ({pid})", pid)
    n0 = wait(lambda: next((x for x in HN.status() if x["builtin"] and x["ready"]), None), 20)
    ok("... er ist bereit", n0)
    auth.save_setting("calls", dict(CALLS, horn_builtin=False))
    gone = wait(lambda: not builtin_pid(), 20)
    ok(f"Schalter aus: eingebauter Knoten beendet ({gone})", gone)
    ok("... und nicht mehr bereit", not any(x["builtin"] and x["ready"] for x in HN.status()))


def e2e(node0, alt, acme_bin):
    root = os.path.join(STATE, "ct150")
    os.makedirs(root)
    # Release-Server: Deploy-Skript bekommt das "alte" Binaer (als Saga-Version signiert), das Update kommt spaeter
    ab = open(alt, "rb").read()
    REL[f"/script/v{VERSION}/gjallarhorn-linux-amd64"] = ab
    REL[f"/script/v{VERSION}/gjallarhorn-linux-amd64.sig"] = sign_rel(VERSION, ab)
    nb = open(node0, "rb").read()
    REL[f"/upd/v{VERSION}/gjallarhorn-linux-amd64"] = nb
    REL[f"/upd/v{VERSION}/gjallarhorn-linux-amd64.sig"] = sign_rel(VERSION + "-falsch", nb)   # erst falsche Signatur
    # Fake-ACME
    acme_log = open(os.path.join(STATE, "fakeacme.log"), "w")
    ap = subprocess.Popen([acme_bin, "-listen", "127.0.0.1:0", "-txt-url", f"http://127.0.0.1:{CFP}/_txt?name=%s"], stdout=subprocess.PIPE,
                          stderr=acme_log, text=True)
    PROCS["acme"] = (ap, acme_log)
    aport = int(ap.stdout.readline().split()[1])

    s, p = adm("POST", "/api/calls/pair", {"name": "horn-2", "share": False, "udp": True})
    ok(f"Erweitern horn-2 ({s})", s == 200)
    script = os.path.join(root, "deploy.sh")
    open(script, "w").write(p["script_systemd"])
    t0 = time.time()
    r = subprocess.run(["sh", script], env=dict(os.environ, HORN_ROOT=root, HORN_RELEASE_BASE=f"http://127.0.0.1:{RELP}/script/v{VERSION}",
                                                SYSTEMCTL="true"), capture_output=True, text=True, timeout=60)
    unit = text(os.path.join(root, "etc/systemd/system/gjallarhorn.service"))
    envf = text(os.path.join(root, "etc/gjallarhorn/gjallarhorn.env"))
    ok(f"systemd-Skript (simuliert): Signatur geprueft, Binaer, Umgebung, Unit ({r.returncode} {r.stderr[-300:]})",
       r.returncode == 0 and os.access(os.path.join(root, "opt/gjallarhorn/gjallarhorn"), os.X_OK) and "ExecStart=/opt/gjallarhorn/gjallarhorn watch" in unit
       and f"HORN_PAIR={p['token']}" in envf and oct(os.stat(os.path.join(root, "etc/gjallarhorn/gjallarhorn.env")).st_mode)[-3:] == "600")
    bad = dict(REL)
    REL[f"/script/v{VERSION}/gjallarhorn-linux-amd64.sig"] = sign_rel(VERSION + "-x", ab)
    r2 = subprocess.run(["sh", script], env=dict(os.environ, HORN_ROOT=os.path.join(STATE, "ct-bad"),
                                                 HORN_RELEASE_BASE=f"http://127.0.0.1:{RELP}/script/v{VERSION}", SYSTEMCTL="true"),
                        capture_output=True, text=True, timeout=60)
    ok(f"systemd-Skript bricht bei falscher Signatur ab ({r2.returncode})", r2.returncode != 0 and "Signatur" in r2.stderr
       and not os.path.exists(os.path.join(STATE, "ct-bad/opt/gjallarhorn/gjallarhorn")))
    REL.update(bad)
    exe = re.search(r"^ExecStart=(\S+) (\S+)$", unit, re.M)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HORN_", "SAGA_")) and k not in ("PYTHONPATH",)}
    for ln in envf.splitlines():
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            env[k] = v
    data = os.path.join(root, "var/lib/gjallarhorn")
    env.update(HORN_DATA=data, HORN_LISTEN=f":{HORN2}", HORN_ICE_IPS="127.0.0.1", HORN_ACME_URL=f"http://127.0.0.1:{aport}/dir",
               HORN_ACME_DNS_WAIT="0", HORN_UPDATE_BASE=f"http://127.0.0.1:{RELP}/upd", HORN_UPDATE_RETRY="2s", HORN_MAX_LEGS="48")
    nlog = open(os.path.join(STATE, "horn2.log"), "w")
    wp = subprocess.Popen([root + exe.group(1), exe.group(2)], env=env, stdout=nlog, stderr=subprocess.STDOUT)
    PROCS["horn2"] = (wp, nlog)

    def done():
        s, d = adm("GET", f"/api/calls/pair/{p['id']}")
        return d if s == 200 and d.get("done") else None
    st = wait(done, 60, 0.5)
    PERF["kopplung_bis_fertig_s"] = round(time.time() - t0, 2)
    s, last = adm("GET", f"/api/calls/pair/{p['id']}")
    steps = {x["k"]: x["s"] for x in last.get("steps", [])}
    ok(f"Live-Fortschritt bis Fertig: {steps} ({PERF['kopplung_bis_fertig_s']} s)", st and steps.get("paired") == "ok" and steps.get("dns") == "ok"
       and steps.get("cert") == "ok" and steps.get("tcp") == "ok" and steps.get("udp") == "ok")
    if not st:
        print(text(os.path.join(STATE, "horn2.log"))[-5000:])
        return
    with connect() as c:
        n = HN.node_named(c, NAME)
    ok(f"Register: horn-2 mit eigener Adresse, UDP, Port, Zertifikat dns01 ({n and n['url']}, {n and n['cert']})",
       n and n["url"] == f"https://{NAME}:{HORN2}" and n["v4"] == "127.0.0.1" and n["udp"] and (n["cert"] or {}).get("state") == "ok"
       and (n["cert"] or {}).get("mode") == "dns01")
    a = [x for x in recs("A", NAME)]
    ok(f"DNS: A-Eintrag fuer horn-2 (graue Wolke, Kommentar) ({a})", len(a) == 1 and a[0]["content"] == "127.0.0.1" and a[0]["proxied"] is False
       and a[0]["comment"] == "saga-horn:" + n["id"])
    ok("DNS-01: TXT nach der Ausstellung wieder geloescht", not recs("TXT"))
    al = json.loads(urllib_get(f"http://127.0.0.1:{aport}/_log"))
    ok(f"Fake-ACME: Zertifikat per dns-01 ausgestellt ({al})", al["issued"] >= 1 and any(x.startswith("dns-01 ok=true") for x in al["log"]))
    cert = ssl_cert(HORN2, NAME)
    ok(f"Knoten liefert das ACME-Zertifikat fuer seinen Namen ({cert})", cert == "Saga Test-ACME CA")
    leak = TOK["good"] in text(os.path.join(STATE, "horn2.log")) or any(TOK["good"] in text(f) for f in glob.glob(data + "/**", recursive=True)
                                                                         if os.path.isfile(f))
    ok("Cloudflare-Token nie beim Knoten (Daten, Log, Umgebung)", not leak and TOK["good"] not in json.dumps(env))
    s, d = pair_post(pair_body(p["token"], NAME)[0])
    ok(f"Kopplungs-Code nach der Kopplung wertlos -> 403 ({s}, {d.get('code')})", s == 403 and d.get("code") in ("used", "token"))
    ok("Kopplungs-Code nicht im Knotenverzeichnis gespeichert", not any(p["token"] in text(f) for f in glob.glob(data + "/**", recursive=True)
                                                                        if os.path.isfile(f)))
    upd = wait(lambda: (lambda x: x if x.get("state") == "fehler" else None)((node2() or {}).get("upd") or {}), 20)
    ok(f"Update: falsche Signatur abgelehnt, Knoten meldet den Fehler ({upd})", upd and "signatur" in (upd.get("err") or ""))
    ok("... kein Leeren wegen eines abgelehnten Updates", not (node2() or {}).get("drain") and not os.path.exists(os.path.join(data, "bin/next")))
    s, html = adm("GET", "/calls")
    ok("Admin: horn-2 in der Tabelle (Bereit, TCP ✓ · UDP ✓, Version)", s == 200 and NAME in html and "TCP ✓ · UDP ✓" in html and f"{VERSION}-alt" in html)

    # --- Anruf ueber horn-2 (nur externe Knoten) --------------------------------------------------------------------
    people = {u: A_.Person(u) for u, _ in USERS}
    for p_ in people.values():
        req("PUT", "/api/calls/caps", p_.k, {"device": p_.dev["id"], "caps": CAPS})
    A, B, Cc = people["ca-e1"], people["ca-e2"], people["ca-e3"]
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:]]})
    gid = d["chat"]["id"]
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    cands = ((d.get("call") or {}).get("horn") or {}).get("candidates") or []
    ok(f"Gruppenanruf nur mit externem Knoten: topo horn, Kandidat horn-2 mit UDP ({s}, {[c['node'] for c in cands]})",
       s == 200 and d["call"]["topo"] == "horn" and len(cands) == 1 and cands[0]["node"] == NAME and cands[0]["udp"]
       and set(cands[0]["tokens"]) == {"audio", "video", "both"})
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    ok(f"Beitritt Bert ({s})", s == 200)
    sa, sb = A_.Stream(A.k), A_.Stream(B.k)
    time.sleep(0.8)
    ma, mb = sa.mark(), sb.mark()
    s, _ = adm("POST", "/calls", form={"action": "drain", "id": n["id"]})
    ev_a = sa.wait("call.horn_move", lambda x: x.get("call_id") == cid, ma, 8)
    ev_b = sb.wait("call.horn_move", lambda x: x.get("call_id") == cid, mb, 8)
    ok(f"Leeren: Umzugsereignis an alle im Anruf ({ev_a})", ev_a and ev_b and ev_a.get("node") == NAME and ev_a.get("why") == "drain")
    ok("... Knoten meldet draining", wait(lambda: live(NAME).get("status") == "draining", 12))
    s, d = req("POST", f"/api/calls/{cid}/join", Cc.k, {"device": Cc.dev["id"], "role": "member", "caps": CAPS})
    ok(f"... kein anderer Knoten bereit: neuer Beitritt -> 409 node ({s}, {d.get('join_block') if isinstance(d, dict) else d})",
       s == 409 and d.get("join_block") == "node")
    s, html = adm("GET", "/calls")
    ok("... Admin: Status 'Wird geleert' und Hinweis 'oft ausgelastet'", "Wird geleert" in html and "zuletzt oft ausgelastet" in html)
    adm("POST", "/calls", form={"action": "undrain", "id": n["id"]})
    for x in (A, B):
        req("POST", f"/api/calls/{cid}/end", x.k, {"device": x.dev["id"]})

    # Browser: 5 Personen ueber horn-2 (Medien wirklich ueber den zweiten Prozess), dann die Admin-Seite mit horn-2
    browser(n)
    admin_browser()

    # --- Update: jetzt mit richtiger Signatur --------------------------------------------------------------------------
    cpid0 = [pid for pid, _ in children(wp.pid)]
    REL[f"/upd/v{VERSION}/gjallarhorn-linux-amd64.sig"] = sign_rel(VERSION, nb)
    t0 = time.time()
    newv = wait(lambda: (lambda x: x.get("version") == VERSION and x.get("ctl"))(live(NAME)), 60, 0.5)
    PERF["update_bis_neue_version_s"] = round(time.time() - t0, 2)
    cpid1 = [pid for pid, _ in children(wp.pid)]
    ok(f"Update: signiertes Binaer geladen, Neustart ohne Beine durch den Waechter, neue Version {VERSION} ({PERF['update_bis_neue_version_s']} s)",
       newv and wp.poll() is None and cpid0 != cpid1 and os.path.exists(os.path.join(data, "bin/current")))
    ok("... Docker-Socket nie benoetigt (Waechter laeuft weiter, gleicher Prozess)", wp.poll() is None)

    # --- Entfernen ---------------------------------------------------------------------------------------------------
    s, html = adm("POST", "/calls", form={"action": "remove", "id": n["id"]})
    gone = wait(lambda: not os.path.exists(os.path.join(data, "node.key")) and os.path.exists(os.path.join(data, "removed")), 15)
    ok("Entfernen: signiertes remove, Knoten loescht Schluessel und Kopplung", gone and not os.path.exists(os.path.join(data, "pair.json")))
    ok("... DNS aufgeraeumt", not recs("A", NAME))
    with connect() as c:
        ban = c.execute("SELECT 1 FROM horn_ban WHERE pub=?", (n["pub"],)).fetchone()
    ok("... Schluessel gesperrt, Knoten nicht mehr im Register", ban and not node2())
    time.sleep(3)
    ok("... Waechter startet den Knoten nicht mehr (kein Kindprozess)", wp.poll() is None and not children(wp.pid))
    s, html = adm("GET", "/calls")
    ok("... Admin-Seite ohne horn-2", NAME not in html.split("Prüfprotokoll")[0])
    stop("horn2")


def live(name):
    """Zustand eines Knotens aus Sicht der Saga-Instanz (Admin-API; Steuerverbindungen leben nur dort)."""
    s, d = adm("GET", "/api/calls/state")
    return next((x for x in (d.get("nodes") or []) if x["name"] == name), {}) if s == 200 else {}


def node2():
    with connect() as c:
        r = HN.node_named(c, NAME)
    return r


def urllib_get(u):
    c = http.client.HTTPConnection(urllib.parse.urlparse(u).hostname, urllib.parse.urlparse(u).port, timeout=10)
    c.request("GET", urllib.parse.urlparse(u).path)
    return c.getresponse().read().decode()


def ssl_cert(port, name):
    ctx = ssl._create_unverified_context()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with ctx.wrap_socket(raw, server_hostname=name) as s:
            der = s.getpeercert(binary_form=True)
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    return x509.load_der_x509_certificate(der).issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value


def browser(n):
    js = os.path.join(APP, "tests", "browser_horn_knoten.js")
    pw = os.environ.get("PLAYWRIGHT") or "/opt/npm-tools/node_modules/playwright"
    if os.environ.get("HORN_BROWSER") == "0":
        print("SKIP Browser-Test (HORN_BROWSER=0)")
        return
    if not shutil.which("node") or not os.path.exists(pw):
        ok("Browser-Test: node und Playwright vorhanden", False)
        return
    env = dict(os.environ, SAGA_BASE=f"http://127.0.0.1:{PORT}", SAGA_USERS=",".join(u for u, _ in BUSERS), SAGA_PASS=PW,
               SAGA_NODE=NAME, SAGA_HOST_RULES=f"MAP {NAME} 127.0.0.1", PLAYWRIGHT=pw, DEBUG=os.environ.get("HORN_DEBUG_BROWSER", ""),
               PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"), PW_CHANNEL="chromium")
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=900)
    print(r.stdout[-8000:], flush=True)
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
        print("--- horn2.log ---\n" + text(os.path.join(STATE, "horn2.log"))[-6000:], flush=True)
    for ln in r.stdout.splitlines():
        if ln.startswith("OK  ") or ln.startswith("FAIL"):
            res.append(ln.startswith("OK"))
    ok(f"Browser: Gruppenanruf mit 5 Personen ueber horn-2 lief durch (rc {r.returncode})", r.returncode == 0)


def admin_pages():
    texts = ["Gjallarhorn – große Gruppenanrufe", "Knoten", "Erweitern", "Name des neuen Knotens", "Für verbundene Server teilen",
             "UDP 443 versuchen (empfohlen)", "Skript erzeugen", "DNS (Cloudflare)", "Grenzen", "Video je Anruf", "Nur Ton je Anruf",
             "Kontingent je verbundenem Server", "Eingebauten Knoten für Anrufe nutzen", "Fähigkeiten jetzt abgleichen",
             "1:1-Anrufe (TURN, optional)", 'name="calls_turn_urls"', "Läuft der Knoten bei einem Anbieter"]
    s, html = adm("GET", "/calls")
    miss = [x for x in texts if x not in html]
    ok(f"Admin 'Anrufe' (de): alle Abschnitte aus §4.1 ({s}, fehlt {miss})", s == 200 and not miss)
    words = {"en": "large group calls", "es": "llamadas", "ru": "Gjallarhorn", "th": "Gjallarhorn"}
    for lg, w in words.items():
        s, h = adm("GET", "/calls", lang=lg)
        ok(f"Admin 'Anrufe' ({lg}) rendert ({s})", s == 200 and w in h and 'lang="' + lg + '"' in h)
    s, html = adm("GET", "/settings")
    ok("Allgemein: Verweis auf die Seite Anrufe statt TURN-Feldern", s == 200 and 'href="/calls"' in html and 'name="calls_turn_urls"' not in html)
    s, _ = adm("POST", "/calls", form={"action": "limits", "horn_video": "8", "horn_audio": "30", "share_legs": "5", "share_mbit": "15"})
    cc = auth.settings(fresh=True)["calls"]
    ok(f"Grenzen gespeichert (8/30, Kontingent 5/15) ({s})", cc.get("horn_video") == 8 and cc.get("horn_audio") == 30 and cc.get("share_legs") == 5
       and cc.get("share_mbit") == 15 and HN.limits(None) == (8, 30))
    s, _ = adm("POST", "/calls", form={"action": "turn", "calls_enabled": "1", "calls_turn_urls": "turns:turn.example.org:443?transport=tcp",
                                       "calls_ring_s": "45"})
    cc = auth.settings(fresh=True)["calls"]
    ok("TURN-Abschnitt speichert, ohne Gjallarhorn-Schalter zu verlieren", cc.get("turn_urls") == "turns:turn.example.org:443?transport=tcp"
       and cc.get("horn") is True and cc.get("horn_video") == 8)
    s, _ = adm("POST", "/calls", form={"action": "caps"})
    ok(f"Knopf 'Faehigkeiten jetzt abgleichen' ({s})", s == 303)
    auth.save_setting("calls", dict(CALLS))
    if not PERF.get("admin_browser"):
        admin_browser()


def admin_browser():
    """Screenshots (Browser): de hell/dunkel, Handy, alle Sprachen - mit verbundenem horn-2 und einem getrennten Knoten."""
    PERF["admin_browser"] = 1
    sk = Ed25519PrivateKey.generate()
    nid, _ = node_entry("horn-3.example.org", sk)
    with connect() as c, c:
        c.execute("UPDATE horn_node SET state='getrennt', seen=?, version=?, v4='192.0.2.33', checks=CAST(? AS JSONB) WHERE id=?",
                  (time.time() - 3600, VERSION, json.dumps({"v4": True, "v6": False, "udp": False, "err": {"v6": "timeout"}, "at": time.time() - 3700}), nid))
    js = os.path.join(APP, "tests", "browser_horn_admin.js")
    pw = os.environ.get("PLAYWRIGHT") or "/opt/npm-tools/node_modules/playwright"
    if not shutil.which("node") or not os.path.exists(pw):
        ok("Admin-Browser-Test: node und Playwright vorhanden", False)
        return
    env = dict(os.environ, SAGA_ADMIN=f"http://127.0.0.1:{ADMIN}", SAGA_ADMIN_COOKIE=admin_cookie(), PLAYWRIGHT=pw,
               PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"),
               SHOTS=os.environ.get("SHOTS") or os.path.join(STATE, "shots"))
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=600)
    print(r.stdout[-6000:], flush=True)
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
    for ln in r.stdout.splitlines():
        if ln.startswith("OK  ") or ln.startswith("FAIL"):
            res.append(ln.startswith("OK"))
    ok(f"Admin-Browser-Test lief durch (rc {r.returncode})", r.returncode == 0)
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE id=?", (nid,))
        c.execute("DELETE FROM horn_pair WHERE name='horn-9.example.org'")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(1))
    main()
