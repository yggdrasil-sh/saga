"""2.5 "Ein Name, ein Port": Saga und der eingebaute Gjallarhorn-Knoten unter einem Namen und einem Port (saga-media
SAGA_TLS=1, media/einport.go + gjallarhorn/einport.go) - mit echtem saga-media und echtem gjallarhorn.

Teil 0 (ohne Server): cert_ok mit Ablauf (not_after), Bereinigung des einport-Zustands, Kandidatenfeld tcp.
Teil 1 (ohne Proxy, Variante B): HTTPS ueber den TLS-Eingang (ALPN http/1.1; h2 angeboten -> http/1.1), Client ohne ALPN
ueber den HTTP-Rueckfall des Knotens, Klartext abgewiesen, WebSocket /probe (Weiterleitung an den Knoten), Admin -> Anrufe
(Betriebsart, Zertifikat, ICE-TCP an), Anruf im Browser (Weg tcp ueber die Socket-Uebergabe), Zertifikat neu laden ohne
Neustart, Gegenprobe abgelaufenes Zertifikat (HTTPS zu, Admin "Abgelaufen"), Knoten abgeschossen -> Saga bleibt
erreichbar (auch ohne ALPN), Knoten kommt wieder.
Teil 2 (hinter einer TLS-Durchreiche mit PROXY-Kopf v2, Variante A; Mini-L4-Proxy im Test): echte Client-Adresse
(whoami, Fehlversuch je Adresse), Verbindung ohne Kopf abgewiesen + Hinweis im Admin-Bereich, kein ICE-TCP-Kandidat
(tcp false), Anruf im Browser ueber TURN-TLS ohne ICE-TCP-Wartezeit, Bild Admin -> Anrufe.
Teil 3: Gjallarhorn-Flag aus - der Knoten laeuft trotzdem (Steuerkanal, Zertifikat), Admin nennt es.

  Umgebung wie die anderen Reihen (env.sh); Ports aus HK_TEST_BASE/HK_TEST_ADMIN (Standard 8481/8480): intern +10001,
  TLS-Eingang +10002, Proxy +10003. SAGA_MEDIA_BIN / GJALLARHORN_BIN, sonst go build. EINPORT_BROWSER=0: ohne Browser.
  EINPORT_SHOTS=<Ordner>: Bilder. Name saga.einport.test (Chromium: --host-resolver-rules)."""
import asyncio
import base64
import datetime
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import urllib.parse

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
PORT = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8481")).port or 8481
ADMIN = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8480")).port or 8480
INTERNAL, TLSP, PXP = PORT + 10001, PORT + 10002, PORT + 10003
NAME = "saga.einport.test"
STATE = os.environ.get("STATE_DIR", "/tmp/st-einport")
SECRET = os.environ.get("SESSION_SECRET") or os.environ["SECRET_KEY"]
TLSD = os.path.join(STATE, "einport-tls")
CERT, KEY = os.path.join(TLSD, "cert.pem"), os.path.join(TLSD, "key.pem")
PW = "Einport-Pass-1234"
RUN = secrets.token_hex(2)
CLIENT = "192.0.2.77"                    # Adresse im PROXY-Kopf des Mini-Proxys (Dokumentationsnetz)
res, PERF = [], {}
P = {"proc": None, "log": None}


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL") + " " + n + ("" if c or info is None else " -> " + str(info)[:600]), flush=True)


# --- Zertifikat --------------------------------------------------------------------------------------------------------
def make_cert(days=60, name=NAME):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    k = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    nb, na = (now - datetime.timedelta(days=2), now - datetime.timedelta(minutes=5)) if days < 0 else (now - datetime.timedelta(hours=1), now + datetime.timedelta(days=days))
    c = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False).sign(k, hashes.SHA256()))
    os.makedirs(TLSD, exist_ok=True)
    with open(KEY + ".tmp", "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    with open(CERT + ".tmp", "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    os.replace(KEY + ".tmp", KEY)
    os.replace(CERT + ".tmp", CERT)
    return c.serial_number


# --- Instanz -----------------------------------------------------------------------------------------------------------
def build(name, d, env_bin):
    b = os.environ.get(env_bin)
    if b:
        return b
    if not shutil.which("go"):
        return None
    out = os.path.join(STATE, name)
    r = subprocess.run(["go", "build", "-trimpath", "-o", out, "."], cwd=os.path.join(APP, d), capture_output=True, text=True,
                       env=dict(os.environ, CGO_ENABLED="0"))
    return None if r.returncode else out


def start(media, node, proxy, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL", "SAGA_HORN") and not k.startswith(("HORN_", "SAGA_TLS", "TLS_"))}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT=str(ADMIN), ADMIN_HOST="127.0.0.1", HOST="127.0.0.1",
               STATE_DIR=STATE, BASE_URL=f"https://{NAME}:{PXP if proxy else TLSP}", CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1",
               SAGA_UPDATE_CHECK="0", PYTHONPATH=APP, SECRET_KEY=os.environ["SECRET_KEY"], SESSION_SECRET=SECRET,
               GJALLARHORN_BIN=node, SAGA_CF_API="http://127.0.0.1:9/client/v4", SAGA_TLS="1", TLS_LISTEN=f"127.0.0.1:{TLSP}",
               TLS_PROXY_PROTOCOL="1" if proxy else "0", SAGA_TLS_CERT=CERT, SAGA_TLS_KEY=KEY, SAGA_TLS_RELOAD_S="1",
               HORN_UDP="0", HORN_ICE_IPS="127.0.0.1")
    env.update(extra or {})
    P["log"] = open(os.path.join(STATE, "einport-server.log"), "a")
    P["proc"] = subprocess.Popen([media, "--", sys.executable, "run.py"], env=env, cwd=APP, stdout=P["log"], stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            st = state()
            if st and (st.get("einport") or {}).get("node"):
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


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
        return open(os.path.join(STATE, "einport-server.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def node_pid():
    """PID des eingebauten Knotens (Kind von saga-media)."""
    pp = P["proc"].pid
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                st = open(f"/proc/{d}/stat").read()
                cmd = open(f"/proc/{d}/cmdline").read()
            except OSError:
                continue
            if int(st.rsplit(")", 1)[1].split()[1]) == pp and "gjallarhorn" in cmd:
                return int(d)
    return 0


# --- Mini-L4-Proxy (TLS-Durchreiche mit PROXY-Kopf v2) ----------------------------------------------------------------
SIG = b"\r\n\r\n\x00\r\nQUIT\n"


class L4Proxy:
    def __init__(self, port, target, src):
        self.port, self.target, self.src, self.loop = port, target, src, None

    def _hdr(self, sport):
        return (SIG + bytes([0x21, 0x11]) + struct.pack(">H", 12) + ipaddress.ip_address(self.src).packed
                + ipaddress.ip_address("127.0.0.1").packed + struct.pack(">HH", sport, self.target))

    async def _pipe(self, r, w):
        try:
            while True:
                b = await r.read(65536)
                if not b:
                    break
                w.write(b)
                await w.drain()
        except Exception:  # noqa: BLE001 - Verbindung weg
            pass
        finally:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass

    async def _handle(self, cr, cw):
        try:
            tr, tw = await asyncio.open_connection("127.0.0.1", self.target)
        except OSError:
            cw.close()
            return
        tw.write(self._hdr(cw.get_extra_info("peername")[1]))
        await tw.drain()
        await asyncio.gather(self._pipe(cr, tw), self._pipe(tr, cw))

    def start(self):
        ready = threading.Event()

        def run():
            self.loop = asyncio.new_event_loop()
            srv = self.loop.run_until_complete(asyncio.start_server(self._handle, "127.0.0.1", self.port))
            ready.set()
            self.loop.run_forever()
            srv.close()
        threading.Thread(target=run, daemon=True).start()
        ready.wait(5)

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


# --- Clients -----------------------------------------------------------------------------------------------------------
def tls_ctx(alpn):
    c = ssl.create_default_context(cafile=CERT)
    if alpn:
        c.set_alpn_protocols(alpn)
    return c


def https(path, port, alpn=("http/1.1",), method="GET", body=b"", headers=None, timeout=10):
    """-> (status, Antwortkopf+Koerper als Text, ausgehandeltes ALPN) bzw. (0, Fehler, None)."""
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        t = tls_ctx(list(alpn) if alpn else None).wrap_socket(s, server_hostname=NAME)
    except (OSError, ssl.SSLError) as ex:
        return 0, str(ex), None
    hd = "".join(f"{k}: {v}\r\n" for k, v in (headers or {}).items())
    t.sendall(f"{method} {path} HTTP/1.1\r\nHost: {NAME}\r\nX-Saga: 1\r\nAccept-Language: de-DE\r\n{hd}Content-Length: {len(body)}\r\n"
              f"Connection: close\r\n\r\n".encode() + body)
    b, neg = b"", t.selected_alpn_protocol()
    try:
        while True:
            x = t.recv(65536)
            if not x:
                break
            b += x
    except (OSError, ssl.SSLError):
        pass
    t.close()
    txt = b.decode("utf-8", "replace")
    m = re.match(r"HTTP/1\.[01] (\d+)", txt)
    return (int(m.group(1)) if m else 0), txt, neg


def ws_echo(port):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    t = tls_ctx(["http/1.1"]).wrap_socket(s, server_hostname=NAME)
    k = base64.b64encode(os.urandom(16)).decode()
    origin = f"https://{NAME}:{port}"
    t.sendall(f"GET /probe HTTP/1.1\r\nHost: {NAME}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {k}\r\n"
              f"Sec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n".encode())
    head = t.recv(4096).decode("utf-8", "replace")
    acc = base64.b64encode(hashlib.sha1((k + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    if " 101 " not in head.split("\r\n")[0] or acc not in head:
        return False, head[:200]
    m, pl = os.urandom(4), b"einport"
    t.sendall(bytes([0x81, 0x80 | len(pl)]) + m + bytes(c ^ m[i % 4] for i, c in enumerate(pl)))
    r = t.recv(100)
    t.close()
    return r[2:] == pl, r


def closed_without_answer(port, data):
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(data)
    try:
        b = s.recv(100)
    except OSError:
        b = b""
    s.close()
    return b == b""


def adm_req(method, path, form=None):
    import http.client
    from itsdangerous import TimestampSigner
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    ck = sa.sign(base64.b64encode(json.dumps({"aid": "ca-ep-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    body, hd = None, {"Cookie": "hk_admin=" + ck, "X-Saga": "1", "Accept-Language": "de-DE"}
    if form is not None:
        body = urllib.parse.urlencode(dict(form, csrf="x"))
        hd["Content-Type"] = "application/x-www-form-urlencoded"
    c = http.client.HTTPConnection("127.0.0.1", ADMIN, timeout=30)
    c.request(method, path, body=body, headers=hd)
    r = c.getresponse()
    d = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, d, ck


def state():
    s, d, _ = adm_req("GET", "/api/calls/state")
    return json.loads(d) if s == 200 else {}


def wait(cond, secs=20, step=0.3):
    t0 = time.time()
    v = None
    while time.time() - t0 < secs:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return v


def ep():
    return state().get("einport") or {}


def builtin(st=None):
    return next((n for n in (st or state()).get("nodes") or [] if n.get("builtin")), None)


def settings(**calls):
    import auth
    cur = auth.settings(fresh=True).get("calls") or {}
    auth.save_setting("calls", {**cur, **calls})


def card(html):
    i = html.find('id="einport"')
    return html[i:html.find("</section>", i)] if i >= 0 else ""


# --- Browser -----------------------------------------------------------------------------------------------------------
def browser(port, want, users):
    if os.environ.get("EINPORT_BROWSER") == "0":
        print("SKIP Browser (EINPORT_BROWSER=0)")
        return
    js = os.path.join(APP, "tests", "browser_einport.js")
    pw = os.environ.get("PLAYWRIGHT") or "playwright"
    if not shutil.which("node"):
        ok("Browser: node vorhanden", False)
        return
    env = dict(os.environ, SAGA_BASE=f"https://{NAME}:{port}", SAGA_HOST_RULES=f"MAP {NAME} 127.0.0.1", SAGA_USERS=",".join(users),
               SAGA_PASS=PW, SAGA_WANT=want, PLAYWRIGHT=pw, PW_CHANNEL=os.environ.get("PW_CHANNEL", "chromium"), NO_PROXY="*")
    if os.environ.get("EINPORT_SHOTS"):
        env["SHOTS"] = os.environ["EINPORT_SHOTS"]
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=600)
    for ln in r.stdout.splitlines():
        if ln.startswith(("OK  ", "FAIL")):
            print("  [browser " + want + "] " + ln, flush=True)
            res.append(ln.startswith("OK"))
        elif ln.startswith("PERF "):
            PERF["anruf_" + want] = json.loads(ln[5:])
    if r.returncode and not any(ln.startswith("FAIL") for ln in r.stdout.splitlines()):
        ok("Browser " + want + " ohne Fehlerzeile", False, (r.stdout + r.stderr)[-1500:])


def users(tag):
    import auth
    out = []
    for i in range(3):
        u = f"ca-ep{RUN}{tag}{i}"
        auth.get_user(u) or auth.create_user(u, PW, f"Einport {tag}{i}")
        out.append(u)
    return out


def shot(name):
    """Admin -> Anrufe, Kasten "Betriebsart: Ein Port" (EINPORT_SHOTS)."""
    out = os.environ.get("EINPORT_SHOTS")
    if not out or not shutil.which("node"):
        return
    _, _, ck = adm_req("GET", "/calls")
    js = """const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
(async () => { const br = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  for (const scheme of ['light', 'dark']) {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: scheme, locale: 'de-DE', extraHTTPHeaders: { 'Accept-Language': 'de-DE' } });
    await ctx.addCookies([{ name: 'hk_admin', value: process.env.CK, domain: '127.0.0.1', path: '/' }]);
    const p = await ctx.newPage(); await p.goto(process.env.URL, { waitUntil: 'load' });
    const el = (await p.$('#einport')) || (await p.$('#horn'));
    await el.screenshot({ path: process.env.OUT + '-' + (scheme === 'dark' ? 'dunkel' : 'hell') + '.png' }); await ctx.close(); }
  await br.close(); })();"""
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, CK=ck, URL=f"http://127.0.0.1:{ADMIN}/calls", OUT=os.path.join(out, name), PLAYWRIGHT=os.environ.get("PLAYWRIGHT") or "playwright",
               NO_PROXY="*")
    r = subprocess.run(["node", "-e", js], env=env, capture_output=True, text=True, timeout=120)
    ok("Bild " + name, r.returncode == 0, r.stderr[-400:])


# --- Teile -------------------------------------------------------------------------------------------------------------
def teil0():
    from saga import horn as HN
    now = time.time()
    ok("cert_ok: ok ohne Ablauf", HN.cert_ok({"cert": {"state": "ok"}}))
    ok("cert_ok: ok mit Ablauf in der Zukunft", HN.cert_ok({"cert": {"state": "ok", "not_after": int(now) + 3600}}))
    ok("cert_ok: Gegenprobe abgelaufen (Pruefer-Fund: blieb bereit)", not HN.cert_ok({"cert": {"state": "ok", "not_after": int(now) - 60}}))
    ok("cert_ok: fehlt", not HN.cert_ok({"cert": {"state": "fehlt"}}))
    c = HN._clean_einport({"name": "/etc/passwd", "port": 99999, "proxy": "ja", "cert": "<b>", "cert_src": "x", "proxy_seen": -5, "node": 1})
    ok("einport-Zustand bereinigt (Name, Port, Wahrheitswerte, Aufzaehlungen)", c == {"name": "", "port": 443, "proxy": False, "node": False,
       "proxy_seen": None, "proxy_bad": None, "proxy_miss": False, "cert": "fehlt", "cert_src": "", "not_after": None, "h2": False}, c)
    HN._LIVE["x1"] = {"ice_tcp": False}
    ok("Kandidat: tcp false aus dem hello, alte Knoten ohne Feld: true", HN.ice_tcp({"id": "x1"}) is False and HN.ice_tcp({"id": "x2"}) is True)
    HN._LIVE.pop("x1", None)
    h = HN._clean_hello({"name": NAME, "url": f"https://{NAME}", "ice_tcp": False, "einport": True})
    h2 = HN._clean_hello({"name": NAME, "url": f"https://{NAME}"})
    ok("hello: ice_tcp/einport uebernommen, alte Knoten ICE-TCP an", h["ice_tcp"] is False and h["einport"] and h2["ice_tcp"] is True and not h2["einport"])


def teil1(media, node):
    make_cert(60)
    settings(enabled=True, horn=True, horn_builtin=True, horn_from=3)
    ok("Teil 1 (ohne Proxy): Instanz startet, Knoten uebernimmt (Uebergabe bereit)", start(media, node, False), log_text()[-1500:])
    st, body, alpn = https("/api/horn/whoami", TLSP)
    ok("HTTPS ueber den TLS-Eingang (ALPN http/1.1), Client-Adresse 127.0.0.1", st == 200 and alpn == "http/1.1" and '"127.0.0.1"' in body, body[-200:])
    st, body, alpn = https("/healthz", TLSP, alpn=("h2", "http/1.1"))
    ok("h2 angeboten -> http/1.1 (Stufe 1 ohne h2), HSTS", st == 200 and alpn == "http/1.1" and "strict-transport-security" in body.lower(), (alpn, body[:300]))
    st, body, alpn = https("/api/horn/whoami", TLSP, alpn=None)
    ok("Client ohne ALPN: TLS im Knoten, HTTP zurueck an Saga (200, Adresse 127.0.0.1)", st == 200 and alpn is None and '"127.0.0.1"' in body, body[-200:])
    st, body, _ = https("/api/horn/whoami", TLSP, headers={"X-Forwarded-For": "6.6.6.6"})
    ok("X-Forwarded-For vom Client zaehlt am TLS-Eingang nicht", st == 200 and "6.6.6.6" not in body, body[-120:])
    ok("Klartext-HTTP am TLS-Port: geschlossen ohne Antwort", closed_without_answer(TLSP, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n"))
    good, info = ws_echo(TLSP)
    ok("WebSocket /probe unter dem Saga-Ursprung (an den Knoten weitergeleitet)", good, info)
    st, body, _ = https("/internal/horn/conf", TLSP)
    ok("/internal/ von aussen weiter gesperrt", st == 404, st)
    s = state()
    e, b = s.get("einport") or {}, builtin(s) or {}
    ok("Admin-Zustand: Ein Port, Zertifikat ok (eigene Dateien), kein PROXY, Knoten bereit", e.get("cert") == "ok" and e.get("cert_src") == "own"
       and e.get("proxy") is False and e.get("node") and e.get("port") == TLSP and e.get("name") == NAME, e)
    ok("eingebauter Knoten: Adresse = Saga-Ursprung, ICE-TCP an (ohne Proxy), bereit", b.get("url") == f"https://{NAME}:{TLSP}" and b.get("ice_tcp") is True
       and b.get("status") == "ready", b)
    _, html, _ = adm_req("GET", "/calls")
    c = card(html)
    ok("Admin -> Anrufe: Kasten \"Betriebsart: Ein Port\" mit Zertifikat und PROXY aus", "Betriebsart: Ein Port" in c and "Gültig bis" in c and "TLS_PROXY_PROTOCOL=0" in c, c[:800])
    shot("einport-direkt")
    browser(TLSP, "tcp", users("b"))
    # Zertifikat neu laden ohne Neustart
    def served():
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname, c.verify_mode = False, ssl.CERT_NONE
        c.set_alpn_protocols(["http/1.1"])
        with c.wrap_socket(socket.create_connection(("127.0.0.1", TLSP), timeout=5), server_hostname=NAME) as t:
            return t.getpeercert(True)
    old = served()
    make_cert(70)
    new = wait(lambda: (lambda p: p if p != old else None)(served()), 10, 0.5)
    ok("Zertifikat neu geladen ohne Neustart (neue Verbindungen)", bool(new))
    # Gegenprobe: abgelaufen -> HTTPS zu, Admin nennt es
    make_cert(-1)
    e = wait(lambda: (lambda x: x if x.get("cert") == "abgelaufen" else None)(ep()), 10)
    st, body, _ = https("/healthz", TLSP)
    ok("Gegenprobe abgelaufenes Zertifikat: HTTPS zu, Admin \"abgelaufen\"", e and st == 0, (e, st, body[:120]))
    make_cert(60)
    ok("gueltiges Zertifikat zurueck: HTTPS wieder offen", wait(lambda: https("/healthz", TLSP)[0] == 200, 10))
    # Knoten abschiessen: Saga bleibt erreichbar, ohne ALPN bedient saga-media selbst; Knoten kommt wieder
    pid = node_pid()
    ok("Knoten-Prozess gefunden", pid > 0)
    if pid:
        os.kill(pid, signal.SIGKILL)
        gone = wait(lambda: not ep().get("node"), 10, 0.2)
        st1 = https("/api/horn/whoami", TLSP)[0]
        st2 = https("/api/horn/whoami", TLSP, alpn=None)[0]
        ok("Knoten weg: Uebergabe aus, HTTPS und Client ohne ALPN gehen weiter", gone and st1 == 200 and st2 == 200, (gone, st1, st2))
        back = wait(lambda: ep().get("node") and node_pid() not in (0, pid), 30, 0.5)
        ok("Knoten startet neu, Uebergabe wieder an", bool(back))
        ok("TURN-Weg nach Neustart: WebSocket /probe", ws_echo(TLSP)[0])
    stop()


def teil2(media, node):
    px = L4Proxy(PXP, TLSP, CLIENT)
    px.start()
    ok("Teil 2 (TLS-Durchreiche + PROXY v2): Instanz startet", start(media, node, True), log_text()[-1500:])
    st, body, _ = https("/api/horn/whoami", PXP)
    ok("echte Client-Adresse aus dem PROXY-Kopf (whoami)", st == 200 and f'"{CLIENT}"' in body, body[-200:])
    st, body, _ = https("/api/horn/whoami", PXP, alpn=None)
    ok("auch ueber den HTTP-Rueckfall im Knoten (ohne ALPN)", st == 200 and f'"{CLIENT}"' in body, body[-200:])
    # Fehlversuch zaehlt je echter Adresse
    u = users("f")[0]
    _, page, _ = https("/login", PXP)
    ck = re.search(r"(?im)^set-cookie: (saga=[^;]+)", page)
    cs = re.search(r'name="csrf" value="([^"]+)"', page)
    st, _, _ = https("/login", PXP, method="POST", body=urllib.parse.urlencode({"username": u, "password": "falsch", "csrf": cs.group(1) if cs else ""}).encode(),
                     headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": ck.group(1) if ck else ""})
    ok("falsches Passwort ueber den TLS-Eingang: 401", st == 401, st)
    from db import connect
    with connect() as c:
        keys = [r[0] for r in c.execute("SELECT key FROM login_fail WHERE key LIKE ?", (f"%{u}%",)).fetchall()]
    ok("Fehlversuch mit der echten Adresse vermerkt (nicht die des Proxys)", any(CLIENT in k for k in keys) and not any("127.0.0.1" in k for k in keys), keys)
    # direkt ohne Kopf: abgewiesen, Admin-Hinweis
    ok("Verbindung ohne PROXY-Kopf: abgewiesen", https("/healthz", TLSP)[0] == 0)
    e = wait(lambda: (lambda x: x if x.get("proxy_warn") else None)(ep()), 40, 1)
    _, html, _ = adm_req("GET", "/calls")
    ok("Admin: PROXY-Kopf Pflicht, abgewiesene Verbindung gemeldet", e and e.get("proxy") is True and "PROXY-Protokoll (v2) einschalten" in card(html),
       (e, card(html)[:600]))
    https("/healthz", PXP)
    e = wait(lambda: (lambda x: x if x.get("proxy_seen") and not x.get("proxy_warn") else None)(ep()), 40, 1)
    _, html, _ = adm_req("GET", "/calls")
    ok("Admin: PROXY-Kopf erkannt, Hinweis wieder weg", e and "Erkannt" in card(html) and "PROXY-Protokoll (v2) einschalten" not in card(html), card(html)[:600])
    b = builtin() or {}
    ok("hinter der Durchreiche: kein ICE-TCP (Kandidat tcp false), Adresse = Saga-Ursprung", b.get("ice_tcp") is False and b.get("url") == f"https://{NAME}:{PXP}", b)
    shot("einport-proxy")
    browser(PXP, "turn", users("a"))
    a = PERF.get("anruf_turn") or {}
    if a:
        worst = max([a.get("start_klick_bis_verbunden_ms") or 0] + (a.get("beitritt_klick_bis_verbunden_ms") or []))
        ok(f"TURN-TLS ohne ICE-TCP-Wartezeit (langsamster Aufbau {worst} ms < 3000)", 0 < worst < 3000, a)
    stop()
    px.stop()


def teil3(media, node):
    settings(horn=False)
    ok("Teil 3 (Gjallarhorn aus): Knoten laeuft trotzdem (Zertifikat), Uebergabe bereit", start(media, node, False), log_text()[-1500:])
    b = wait(lambda: (lambda x: x if x and x.get("ctl") else None)(builtin()), 20)
    ok("Steuerkanal des eingebauten auch ohne Flag verbunden (Zertifikat gemeldet)", b and (b.get("cert") or {}).get("state") == "ok", b)
    _, html, _ = adm_req("GET", "/calls")
    ok("Admin nennt: Knoten kuemmert sich nur um das Zertifikat", "nur um das Zertifikat" in card(html), card(html)[:600])
    ok("HTTPS geht", https("/healthz", TLSP)[0] == 200)
    stop()
    settings(horn=True)


def main():
    os.makedirs(STATE, exist_ok=True)
    open(os.path.join(STATE, "einport-server.log"), "w").close()
    import auth
    import db
    db.wait_ready()
    db.init_schema()
    auth.get_user("ca-ep-adm") or auth.create_user("ca-ep-adm", secrets.token_urlsafe(12), "Admin Einport", is_admin=True)
    teil0()
    media, node = build("saga-media", "media", "SAGA_MEDIA_BIN"), build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok("saga-media und gjallarhorn gebaut", bool(media and node))
    if media and node:
        try:
            teil1(media, node)
            teil2(media, node)
            teil3(media, node)
        finally:
            stop()
    errs = len(re.findall(r"Traceback|panic:|DATA RACE", log_text()))
    print("PERF " + json.dumps(PERF), flush=True)
    print(f"Serverfehler: {errs}")
    print("ALLES GRUEN" if res and all(res) and not errs else f"FEHLER ({res.count(False)} von {len(res)})")
    sys.exit(0 if res and all(res) and not errs else 1)


if __name__ == "__main__":
    main()
