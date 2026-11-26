"""Bugs-DB 2026: Saga hinter Node64 (ipv64.net, PROXY64 als Reverse Proxy, TLS am Proxy) - Laborreihe.

Baut auf tests/a3_tunnel.py auf (echtes saga-media, echter gjallarhorn, Tunnel-Betrieb des eingebauten Knotens), nur
steht statt des nginx-Nachbaus ein echter Node64-Container davor. Node64 wird NICHT von dieser Reihe gestartet:
  podman/docker run -d --name n64 -p 127.0.0.1:<N64_UI>:8080 -p 127.0.0.1:<A3_BASE>:443 -v n64:/data \
      -e NODE64_ADMIN_USER=... -e NODE64_ADMIN_PASSWORD=... ghcr.io/ipv64net/node64:<version>
  Module cdn64 (PROXY64) und healthchecks an, API-Key in N64_KEY_FILE (Datei, nur der Key).
Umgebung: N64_URL (Oberflaeche/API, Standard https://127.0.0.1:18640), N64_KEY_FILE, N64_TARGET (Adresse, unter der
der Container Saga erreicht, z. B. das Gateway des Container-Netzes 10.88.0.1 - saga-media lauscht dann dort, HOST),
N64_PEER (Adresse des Node64-Containers, fuer die X-Forwarded-For-Pruefung), TRUSTED_PROXIES (Netz des Containers),
A3_BASE (Standard 18651: +0 = veroeffentlichter Port 443 von Node64, +1 saga-media, +2 Admin, +7 intern, +8 HORN_PORT),
N64_SSE_IDLE (Sekunden Ruhe vor dem zweiten Live-Ereignis, Standard 70), A3_SHOTS=<Ordner> (Admin -> Anrufe "Jetzt
pruefen" im Browser durch Node64), N64_BROWSER=1 (tests/browser_node64.js: Seite, EventSource, WebSocket im Browser),
N64_PUSH_IDLE=<Minuten> (Push-Verbindung der Android-App /api/push/stream ruhend durch Node64, z. B. 31; dann
N64_TIMEOUT im Laufskript hochsetzen). Die Docker-Labels (node64.proxy.*) prueft diese Reihe nicht.

Prueft: (a) Seite ueber Node64 mit TLS (selbst signiertes Zertifikat von Node64), (b) /healthz ueber Node64 und als
Health-Check von Node64, (c) Live-Kanal (SSE) ohne Pufferung, auch nach Ruhe > 60 s, (d) WebSocket-Tunnel /api/sfu/ws:
101, Zugang, zwei Geraete mit Medien ueber Node64 -> saga-media -> Knoten (TestA3Kette), (e) WebSockets am Proxy-Host
aus -> 426 mit Hinweis, wieder an -> 101. Dazu X-Forwarded-For (echte Adresse, gefaelschter Kopf verworfen)."""
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

os.environ.setdefault("A3_BASE", "18651")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import a3_tunnel as T  # noqa: E402

A_, auth, connect, ok, res = T.A_, T.auth, T.connect, T.ok, T.res
N64 = os.environ.get("N64_URL", "https://127.0.0.1:18640")
KEY = open(os.environ.get("N64_KEY_FILE", "/srv/saga-test/tmp/a2026/n64.key")).read().strip()
TARGET = os.environ.get("N64_TARGET", "10.88.0.1")
PEER = os.environ.get("N64_PEER", "")
TRUST = os.environ.get("TRUSTED_PROXIES", "10.88.0.0/16")
IDLE = int(os.environ.get("N64_SSE_IDLE") or 70)
CTX = ssl.create_default_context()
CTX.check_hostname, CTX.verify_mode = False, ssl.CERT_NONE        # nur Pruefstand (selbst signiert)
# saga-media lauscht an der Adresse, die der Container erreicht - direkte Testaufrufe gehen ebenfalls dorthin
A_.B_ = urllib.parse.urlparse(f"http://{TARGET}:{T.PORT}")
HOST_ID = {"id": 0}


def n64(method, path, body=None):
    rq = urllib.request.Request(N64 + "/api" + path, data=None if body is None else json.dumps(body).encode(), method=method,
                                headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json", "X-Agent-Actor": "saga-labor"})
    try:
        r = urllib.request.urlopen(rq, context=CTX, timeout=30)
        d = r.read().decode()
        return r.status, (json.loads(d) if d.strip().startswith(("{", "[")) else d)
    except urllib.error.HTTPError as e:
        d = e.read().decode()
        return e.code, (json.loads(d) if d.strip().startswith(("{", "[")) else d[:300])


def raw(port, path, extra="", body_max=65536):
    """HTTPS-Anfrage (HTTP/1.1) an Node64 -> (Status, Koepfe, Koerper, Zertifikat DER)."""
    s = T.tls_conn(port)
    der = s.getpeercert(binary_form=True)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: {T.NAME}:{port}\r\nConnection: close\r\n{extra}\r\n".encode())
    buf = b""
    try:
        while len(buf) < body_max:
            d = s.recv(65536)
            if not d:
                break
            buf += d
    except OSError:
        pass
    s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    st = int(lines[0].split()[1]) if lines and len(lines[0].split()) > 1 else 0
    hd = {ln.split(":", 1)[0].strip().lower(): ln.split(":", 1)[1].strip() for ln in lines[1:] if ":" in ln}
    return st, hd, body.decode("utf-8", "replace"), der


class TlsStream:
    """Live-Kanal (SSE) ueber Node64: Ereignisse mit Ankunftszeit."""

    def __init__(self, cookie):
        self.s = T.tls_conn(T.NGX)
        self.s.settimeout(None)
        self.s.sendall((f"GET /api/events HTTP/1.1\r\nHost: {T.NAME}:{T.NGX}\r\nAccept: text/event-stream\r\n"
                        f"Cookie: saga={cookie}\r\n\r\n").encode())
        self.ev, self.raw, self.head, self.first, self.t0, self.closed = [], [], "", None, time.time(), None
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        buf, body, cur, chunked, need = b"", b"", {}, None, None
        try:
            while True:
                d = self.s.recv(65536)
                if not d:
                    break
                if self.first is None:
                    self.first = time.time() - self.t0
                buf += d
                if not self.head:
                    if b"\r\n\r\n" not in buf:
                        continue
                    h, _, buf = buf.partition(b"\r\n\r\n")
                    self.head = h.decode("latin-1")
                    chunked = "chunked" in self.head.lower()
                if chunked:                                   # Chunks zusammensetzen
                    while True:
                        if need is None:
                            if b"\r\n" not in buf:
                                break
                            ln, _, buf = buf.partition(b"\r\n")
                            need = int(ln.split(b";")[0] or b"0", 16)
                        if len(buf) < need + 2:
                            break
                        body += buf[:need]
                        buf, need = buf[need + 2:], None
                else:
                    body, buf = body + buf, b""
                while b"\n" in body:
                    line, _, body = body.partition(b"\n")
                    line = line.decode("utf-8", "replace").rstrip("\r")
                    self.raw.append(line)
                    if line == "":
                        if "event" in cur:
                            self.ev.append((cur["event"], json.loads(cur.get("data") or "null"), time.time()))
                        cur = {}
                    elif not line.startswith(":"):
                        k, _, v = line.partition(":")
                        cur[k] = v[1:] if v.startswith(" ") else v
        except OSError:
            pass
        self.closed = time.time() - self.t0

    def wait(self, typ, pred, since, timeout):
        end = time.time() + timeout
        while time.time() < end:
            for e in self.ev[since:]:
                if typ in (None, e[0]) and isinstance(e[1], dict) and pred(e[1]):
                    return e
            time.sleep(0.02)
        return None

    def close(self):
        """Erst shutdown, dann auf den Lesefaden warten: sonst liest der noch blockierte Faden nach close() aus der
        naechsten Verbindung, die dieselbe Dateinummer bekommt (gesehen: Antwort ohne Anfang, BadStatusLine)."""
        import socket
        try:
            self.s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.th.join(5)
        try:
            self.s.close()
        except OSError:
            pass


def host_body(ws=True):
    return {"names": [T.NAME], "enabled": True, "scheme": "http", "target_host": TARGET, "target_port": T.PORT, "websockets": ws,
            "http3": False, "request_certificate": {"source": "self"}}       # Puffern/Zeitlimit/Upload-Grenze: Vorgaben von Node64


def setup_host():
    s, d = n64("POST", "/cdn64/hosts", host_body())
    ok("Node64: Proxy-Host saga.example.org -> Saga (http, WebSockets an, selbst signiertes Zertifikat) angelegt",
       s in (200, 201) and isinstance(d, dict) and d.get("id"), (s, d))
    HOST_ID["id"] = (d or {}).get("id") if isinstance(d, dict) else 0
    print("   Host:", json.dumps({k: (d or {}).get(k) for k in ("id", "websockets", "cert_id", "certificate_error", "http2", "force_ssl")}
                                 if isinstance(d, dict) else d, ensure_ascii=False))
    got = T.wait(lambda: (lambda r: r if r[0] == 200 else None)(raw(T.NGX, "/healthz")), 40, 1)
    return got


def set_ws(on):
    s, d = n64("PUT", f"/cdn64/hosts/{HOST_ID['id']}", {"websockets": on})
    ok(f"Node64: WebSockets am Proxy-Host {'an' if on else 'aus'} (API)", s == 200 and (d or {}).get("websockets") is on, (s, d))
    time.sleep(3)


def teil_a_b():
    got = setup_host()
    st, hd, body, der = got or raw(T.NGX, "/healthz")
    ok("(b) /healthz ueber Node64 (TLS): 200 ok", st == 200 and body.strip() == "ok", (st, body[:200]))
    pem = ssl.DER_cert_to_PEM_cert(der)
    r = subprocess.run(["openssl", "x509", "-noout", "-subject", "-issuer", "-ext", "subjectAltName"], input=pem, capture_output=True, text=True)
    print("   Zertifikat von Node64:", " | ".join(x.strip() for x in r.stdout.splitlines() if x.strip()))
    ok("(a) TLS endet an Node64: Zertifikat fuer saga.example.org (selbst signiert/lokal)", T.NAME in r.stdout, r.stdout)
    st, hd, body, _ = raw(T.NGX, "/login")
    ok("(a) Anmeldeseite ueber Node64: 200, Saga-Seite, HSTS von Saga", st == 200 and "Saga" in body and "strict-transport-security" in hd,
       (st, {k: hd.get(k) for k in ("server", "strict-transport-security", "content-type")}, body[:200]))
    st, hd, body, _ = raw(T.NGX, "/")
    ok("(a) Startseite ueber Node64: Umleitung zur Anmeldung bzw. Seite (kein 5xx)", st in (200, 302, 303, 307), (st, hd.get("location")))
    # echte Adresse: Node64 schickt X-Forwarded-For, Saga vertraut dem Container-Netz (TRUSTED_PROXIES)
    st, _, body, _ = raw(T.NGX, "/api/horn/whoami")
    ip = (json.loads(body) if body.strip().startswith("{") else {}).get("ip")
    st2, _, body2, _ = raw(T.NGX, "/api/horn/whoami", "X-Forwarded-For: 203.0.113.9\r\nX-Real-IP: 203.0.113.9\r\n")
    ip2 = (json.loads(body2) if body2.strip().startswith("{") else {}).get("ip")
    print(f"   whoami ueber Node64: {ip} (mit gefaelschtem Kopf: {ip2}), Node64-Container: {PEER or '?'}")
    ok("X-Forwarded-For: Saga sieht die Adresse des Besuchers, nicht die von Node64; gefaelschter Kopf wird verworfen",
       st == 200 and ip and ip != PEER and ip2 == ip, (ip, ip2, PEER))
    ok("kein Hinweis 'Proxy nicht in TRUSTED_PROXIES' im Log", "nicht in TRUSTED_PROXIES" not in T.log_text())
    # Health-Check von Node64: Knopf "Healthcheck fuer dieses Ziel anlegen" (API) und eigener HTTP-Check auf /healthz
    s, d = n64("POST", f"/cdn64/hosts/{HOST_ID['id']}/healthcheck", {})
    print("   Node64 'Healthcheck fuer dieses Ziel':", s, json.dumps(d, ensure_ascii=False)[:400])
    s2, c = n64("POST", "/checks", {"name": "Saga /healthz", "type": "http", "target": f"http://{TARGET}:{T.PORT}/healthz",
                                    "interval_seconds": 10, "retries": 1, "params": {"accept_status": "200-299"}})
    tok = (c or {}).get("token") if isinstance(c, dict) else None
    checks = [x for x in ((d or {}).get("token") if isinstance(d, dict) else None, tok) if x]
    for t_ in checks:
        n64("POST", f"/checks/{t_}/run", {})
    time.sleep(8)
    sts = {}
    for t_ in checks:
        s3, cd = n64("GET", f"/checks/{t_}")
        stt = (cd or {}).get("state") or {}
        sts[(cd or {}).get("name", t_)] = {"state": stt.get("state"), "msg": stt.get("last_message"), "target": (cd or {}).get("target")}
    print("   Node64-Checks:", json.dumps(sts, ensure_ascii=False)[:900])
    ok("(b) Node64-Health-Check auf /healthz (HTTP 200): up", (sts.get("Saga /healthz") or {}).get("state") == "up", sts)
    return checks


def teil_upload(p, mb=9):
    """Datei-Upload knapp unter ATTACH_MAX_MB (10) durch Node64 mit dessen Vorgaben (keine eigene Upload-Grenze gesetzt)."""
    data = os.urandom(mb * 1024 * 1024)
    bnd = "----saga" + secrets.token_hex(8)
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"gross.bin\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bnd}--\r\n".encode()
    s = T.tls_conn(T.NGX)
    s.settimeout(60)
    t0 = time.time()
    s.sendall((f"POST /api/files HTTP/1.1\r\nHost: {T.NAME}:{T.NGX}\r\nCookie: saga={p.k}\r\nX-Saga: 1\r\n"
               f"Content-Type: multipart/form-data; boundary={bnd}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
    out = b""
    try:
        while True:
            x = s.recv(65536)
            if not x:
                break
            out += x
    except OSError:
        pass
    s.close()
    head, _, rb = out.partition(b"\r\n\r\n")
    line = head.split(b"\r\n")[0].decode("latin-1") if head else ""
    print(f"   Upload {mb} MB durch Node64: {line} in {time.time() - t0:.1f} s, Antwort {rb[:160]!r}")
    ok(f"Upload {mb} MB (unter ATTACH_MAX_MB) durch Node64 mit dessen Vorgaben: 200", " 200" in line and b"/files/" in rb, (line, rb[:300]))


def teil_c(people):
    A, B = people[0], people[1]
    st = TlsStream(A.k)
    time.sleep(1.5)
    ok("(c) Live-Kanal ueber Node64: 200 text/event-stream, erste Bytes sofort (< 1,5 s)", " 200" in st.head.split("\r\n")[0]
       and "text/event-stream" in st.head.lower() and st.first is not None and st.first < 1.5, (st.head[:400], st.first))
    print("   SSE-Koepfe:", " | ".join(ln for ln in st.head.split("\r\n") if ln.lower().startswith(("http/", "content-type", "transfer-encoding",
                                                                                                         "content-encoding", "cache-control", "x-accel"))))
    s, d = A_.req("POST", "/api/chats", B.k, {"kind": "free", "members": [A.uid]})
    gid = d["chat"]["id"]
    lat = []
    for runde in (1, 2):
        if runde == 2:
            print(f"   {IDLE} s Ruhe auf dem Live-Kanal ...", flush=True)
            time.sleep(IDLE)
        m0, cid = len(st.ev), secrets.token_hex(16)
        t0 = time.time()
        s, d = A_.req("POST", "/api/calls", B.k, {"id": cid, "chat_id": gid, "media": "audio", "device": B.dev["id"], "caps": T.CAPS_WEB})
        print(f"   Runde {runde}: Anruf {s} topo={(d.get('call') or {}).get('topo') if isinstance(d, dict) else d}", flush=True)
        e = st.wait(None, lambda x, c=cid: x.get("call_id") == c, m0, 8)     # erstes Ereignis dieses Anrufs (call.roster/state)
        lat.append(round(e[2] - t0, 3) if e else None)
        A_.req("POST", f"/api/calls/{cid}/end", B.k, {"device": B.dev["id"]})
        time.sleep(0.5)
    print("   Ereignisse:", [e[0] for e in st.ev][:20], "Zeilen:", len(st.raw), st.raw[-3:])
    print("   Laufzeit Ereignis (Anruf startet -> erstes Anruf-Ereignis beim Gegenueber ueber Node64):", lat, "s")
    ok("(c) Ereignis kommt ueber Node64 binnen 2 s an (keine Pufferung)", lat[0] is not None and lat[0] < 2, lat)
    ok(f"(c) nach {IDLE} s Ruhe: Verbindung offen, Ereignis kommt binnen 2 s", lat[1] is not None and lat[1] < 2 and st.closed is None, (lat, st.closed))
    st.close()


def teil_d_e(media, node, people):
    T.flag(True, horn_from=3)
    for p in people:
        A_.req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": T.CAPS})
    n = T.wait(lambda: (lambda b: b if b and b.get("ctl") and b.get("status") == "ready" else None)(T.builtin()), 30)
    n = T.settled(lambda b: bool(b) and b.get("url") == T.BASE and bool(b.get("tunnel")) and bool(b.get("ws"))) if n else n
    ok("eingebauter Knoten im Tunnel-Betrieb (Adresse = Saga hinter Node64), bereit", bool(n) and n.get("tunnel") and n.get("url") == T.BASE, n)
    cid, topo, cands = T.candidates_for_call(people)
    ca = (cands.get(people[0].uid) or [{}])[0]
    ok("App (caps ws): Gruppenanruf ueber den Knoten, Kandidat ws true, url = Saga hinter Node64",
       topo == "horn" and ca.get("ws") is True and ca.get("url") == T.BASE, {"topo": topo, "cand": {k: ca.get(k) for k in ("url", "tcp", "tls", "ws")}})
    st, hd, s = T.ws_handshake(T.NGX)
    good = st == 101 and hd.get("sec-websocket-protocol") == "saga-tunnel.1"
    ok("(d) Node64 mit WebSockets: 101 saga-tunnel.1 bis zum Knoten", good, (st, hd, s if isinstance(s, str) else ""))
    if good:
        T.ws_send(s, 1, json.dumps({"t": "auth", "v": 1, "mode": "ice", "user": "1:x", "cred": "falsch"}).encode())
        op, d = T.ws_recv(s)
        r = json.loads(d) if op == 1 else {}
        op2, d2 = T.ws_recv(s)
        code = int.from_bytes(d2[:2], "big") if op2 == 8 and len(d2) >= 2 else 0
        ok("(d) falscher Zugang durch Node64: err auth, Close 4401 kommt an", r.get("code") == "auth" and code == 4401, (r, code))
        s.close()
    st, hd, s = T.ws_handshake(T.NGX, "saga-frames.1")
    ok("(d) saga-frames.1 (Rahmen-Weg der Browser): 101", st == 101 and hd.get("sec-websocket-protocol") == "saga-frames.1", (st, hd))
    if st == 101:
        s.close()
    toks = os.path.join(T.STATE, "tokens.json")
    devs = [{"audio": (cands.get(p.uid) or [{}])[0]["tokens"]["audio"], "video": (cands.get(p.uid) or [{}])[0]["tokens"]["video"]} for p in people[:2]]
    json.dump(devs, open(toks, "w"))
    env = dict(os.environ, A3_URL=T.BASE, A3_DIAL=f"127.0.0.1:{T.NGX}", A3_TOKENS=toks, A3_SECS=os.environ.get("N64_SECS", "20"), CGO_ENABLED="0",
               GOPROXY="direct", GOSUMDB="off", GOFLAGS="-mod=mod", GOTOOLCHAIN="local")
    r = subprocess.run(["go", "test", "-count=1", "-run", "TestA3Kette", "-v", "."], cwd=os.path.join(T.APP, "gjallarhorn"), capture_output=True,
                       text=True, env=env, timeout=600)
    info = [ln.strip() for ln in r.stdout.splitlines() if "tunnel_test.go" in ln or ln.startswith(("--- ", "ok", "FAIL"))]
    for ln in info:
        print("   ", ln)
    ok("(d) Anruf: zwei Geraete mit Saga-Token ueber Node64 -> saga-media -> Knoten (Weg ws), Medien in beide Richtungen",
       r.returncode == 0 and "--- PASS: TestA3Kette" in r.stdout, info or r.stdout[-1500:] + r.stderr[-800:])
    tu = T.wait(lambda: (lambda x: x if x.get("total", 0) >= 2 and x.get("ws_warn") is False else None)(T.state().get("tunnel") or {}), 12)
    ok("(d) Admin -> Anrufe: Tunnel gezaehlt, keine Warnung", bool(tu), T.state().get("tunnel"))
    if os.environ.get("A3_SHOTS"):
        _, _, ck, _ = T.adm("GET", "/calls")
        T.shot(ck, "node64-ws-an")                   # "Jetzt pruefen" im Browser durch Node64
    if os.environ.get("N64_BROWSER"):
        browser(people[0])
    # (e) WebSockets aus
    set_ws(False)
    st, hd, body = T.ws_handshake(T.NGX)
    j = json.loads(body) if isinstance(body, str) and body.strip().startswith("{") else {}
    print("   ohne WebSockets:", st, (body if isinstance(body, str) else "")[:300])
    ok("(e) Node64 ohne WebSockets: der Knoten antwortet 426 websocket_required mit Hinweis", st == 426 and j.get("error") == "websocket_required"
       and 'Node64: "WebSockets"' in j.get("text", ""), (st, hd, body if isinstance(body, str) else ""))  # 2462: Hinweis nennt den Node64-Schalter
    if st == 101 and not isinstance(body, str):
        body.close()
    time.sleep(6)
    tu = T.state().get("tunnel") or {}
    ok("(e) Admin-Zustand: Anfrage ohne Upgrade gezaehlt, Warnung an", tu.get("no_upgrade", 0) >= 1 and tu.get("ws_warn") is True, tu)
    if os.environ.get("A3_SHOTS"):
        _, _, ck, _ = T.adm("GET", "/calls")
        shot_nein(ck, "node64-ws-aus")
    set_ws(True)
    st, hd, s = T.ws_handshake(T.NGX)
    ok("(e) WebSockets wieder an: 101", st == 101, (st, hd))
    if st == 101:
        s.close()
    A_.req("POST", f"/api/calls/{cid}/end", people[0].k, {"device": people[0].dev["id"]})


def teil_push(p, minutes):
    """Eigene Push-Verbindung der Android-App (WebSocket /api/push/stream) durch Node64: bleibt eine ruhende Verbindung
    so lange offen, wie Saga es vom Proxy verlangt (ANLEITUNG Abschnitt 3: mindestens 30 Minuten)? Danach Lebenszeichen
    0x03 -> 0x04 und eine Test-Mitteilung."""
    import socket
    import push_verbindung as PV
    from websockets.sync.client import connect as ws_connect
    PV.B_ = A_.B_
    prev = auth.settings(fresh=True).get("push")
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True})
    auth._settings_cache["v"] = None
    time.sleep(5.6)                                   # Einstellungen werden zwischengespeichert
    try:
        did, bear = PV.device(p.uid, "Pixel Node64")
        ua = PV.UA()
        s1, d1 = PV.req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua.keys(), "name": "Pixel"}, hdr=bear)
        ok("Push: Abo 'stream' angelegt", s1 == 201, (s1, d1))
        sid = (d1 or {}).get("id")
        tcp = socket.create_connection(("127.0.0.1", T.NGX), timeout=10)
        w = ws_connect(f"wss://{T.NAME}:{T.NGX}/api/push/stream", sock=tcp, ssl=CTX, server_hostname=T.NAME,
                       additional_headers=bear, open_timeout=10, max_size=65536, ping_interval=None)
        t0 = time.time()
        print(f"   Push-Verbindung durch Node64 offen, {minutes} min Ruhe ...", flush=True)
        code = PV.closed_code(w, timeout=minutes * 60)
        ruhe = round((time.time() - t0) / 60, 1)
        ok(f"Push: ruhende WebSocket-Verbindung durch Node64 nach {ruhe} min noch offen", code is None, {"code": code, "min": ruhe})
        if code is None:
            w.send(bytes([3]))
            b = w.recv(timeout=8)
            ok("Push: Lebenszeichen 0x03 -> 0x04 nach der Ruhe", b == bytes([4]), b)
            PV.req("POST", f"/api/push/subscriptions/{sid}/test", None, hdr=bear)
            m = PV.recv_msg(w, 10)
            ok("Push: Test-Mitteilung kommt durch Node64 an", m is not None)
            w.close()
    finally:
        if prev is not None:
            auth.save_setting("push", prev)
        with connect() as c, c:
            c.execute("DELETE FROM device_login WHERE uid=?", (p.uid,))


def shot_nein(ck, name):
    """Admin -> Anrufe "Jetzt pruefen" bei ausgeschalteten WebSockets: erwartet "Nein" mit Hinweis (a3_bilder.js wertet
    "Ja" als OK - hier ist es umgekehrt)."""
    js = os.path.join(T.APP, "tests", "a3_bilder.js")
    env = dict(os.environ, A3_ADMIN=f"http://127.0.0.1:{T.ADMIN}", A3_COOKIE=ck, A3_OUT=os.path.join(os.environ["A3_SHOTS"], f"a3-admin-{name}.png"),
               A3_HOST_RULES=f"MAP {T.NAME} 127.0.0.1", A3_CLICK="1", NO_PROXY="*")
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=120)
    line = next((ln for ln in r.stdout.splitlines() if "Proxy reicht WebSocket durch?" in ln), "")
    print("  [bild] " + line[:200], flush=True)
    ok("(e) Admin -> Anrufe \"Jetzt pruefen\" im Browser durch Node64 ohne WebSockets: \"Nein\" mit Hinweis", "Nein" in line, line or r.stdout[-800:])


def browser(p):
    js = os.path.join(T.APP, "tests", "browser_node64.js")
    env = dict(os.environ, N64_BASE=T.BASE, N64_COOKIE=p.k, N64_HOST_RULES=f"MAP {T.NAME} 127.0.0.1", NO_PROXY="*")
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=120)
    for ln in r.stdout.splitlines():
        if ln.startswith(("OK  ", "FAIL", "   ")):
            print("  [browser] " + ln, flush=True)
            if ln.startswith(("OK  ", "FAIL")):
                res.append(ln.startswith("OK"))
    if r.returncode and "FAIL" not in r.stdout:
        ok("Browser-Pruefung", False, (r.stdout + r.stderr)[-1500:])


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
    for u, nm in T.USERS:
        auth.get_user(u) or auth.create_user(u, T.PW, nm)
    s, st = n64("GET", "/status")
    print("Node64:", s, json.dumps(st, ensure_ascii=False)[:300] if isinstance(st, dict) else st)
    media, node = T.build("saga-media", "media", "SAGA_MEDIA_BIN"), T.build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok("saga-media und gjallarhorn gebaut", bool(media and node))
    checks = []
    try:
        T.flag(True, horn_from=3)
        t = T.start(media, node, {"HOST": TARGET, "TRUSTED_PROXIES": TRUST})
        ok(f"Saga (saga-media an {TARGET}:{T.PORT}, BASE_URL {T.BASE}, TRUSTED_PROXIES {TRUST}) laeuft", t is not None, T.log_text()[-2000:])
        if t is not None:
            checks = teil_a_b()
            people = [A_.Person(u) for u, _ in T.USERS]
            teil_upload(people[0])
            teil_c(people[2:4])                  # eigene Konten: A/B brauchen danach frei fuer den Gruppenanruf
            teil_d_e(media, node, people)
            if os.environ.get("N64_PUSH_IDLE"):
                teil_push(people[4], float(os.environ["N64_PUSH_IDLE"]))
    finally:
        T.stop()
        if HOST_ID["id"]:
            print("   Node64: Host loeschen", n64("DELETE", f"/cdn64/hosts/{HOST_ID['id']}")[0])
        for t_ in checks:
            n64("DELETE", f"/checks/{t_}")
        errs = len(re.findall(r"Traceback|panic:|DATA RACE", T.log_text()))
        ok(f"Instanz: Serverfehler {errs}", errs == 0)
        if errs:
            print(T.log_text()[-5000:])
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE builtin=1")
        try:
            shutil.copy(os.path.join(T.STATE, "server.log"), os.environ.get("N64_LOGCOPY", "/tmp/node64-server.log"))
        except OSError:
            pass
        shutil.rmtree(T.STATE, ignore_errors=True)
    print(f"\nNODE64 {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
