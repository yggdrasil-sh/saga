"""2.5 A3 Huelle: Web-Anrufe der Mac-Huelle ueber den Medien-Tunnel - echte Kette nginx ("Websockets Support") -> saga-media
-> eingebauter Knoten im Tunnel-Betrieb, Chromium mit einer Attrappe der Huellen-Bruecke (window.webkit.messageHandlers.hkTunnel,
sagaHostInfo caps "tunnel"); die Bruecke reicht an den Go-Stellvertreter e2e/wstunnel (Protokoll saga-tunnel.1 v1) wie
die Huelle an HornTunnel. Browser-Teil: tests/browser_a3_huelle.js.

Geprueft: Kandidat tcp/tls false, ws true; A und B (mit Bruecke) telefonieren ueber den Weg ws (2 Leitungen, Bein-Weg ws am
Knoten, Diagnose transport ws, Bild/Ton in beide Richtungen, Frame-E2E ohne Verwerfen), Bruecken-Aufrufe open/listen/close
je Leitung, caps ws nur mit Bruecke; C ohne Bruecke (normaler Browser, ohne ws) tritt bei -> Saga schaltet den Anruf auf
direkt (Vertrag S10), alle drei verbunden, A schliesst seine Tunnel.
Die Pruefung "direkt: alle drei verbunden" zaehlt immer (seit S11, Commit "zaehlt wieder"); die fruehere Umgebungsvariable
A3H_STRICT (damals: nur mit =1 zaehlen, sonst nur Befund) gibt es nicht mehr und wirkt nicht - nicht mehr setzen.

  Ports 8551 (nginx), 8552 (saga-media), 8553 (Admin), 8555/8556 (wstunnel lokal/Steuerung), 8558 (intern), 8559 (HORN_PORT);
  andere Lage mit A3H_BASE (z. B. 8601 -> 8601..8609).
  Umgebung wie die anderen Reihen (env.sh, eigene DB). SAGA_MEDIA_BIN / GJALLARHORN_BIN / WSTUNNEL_BIN, sonst go build
  (A3_GOMOD = eigene Mod-Datei fuer gjallarhorn). SHOTS=<Ordner>: Bilder des Anruffensters."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_B = int(os.environ.get("A3H_BASE") or 8551)            # Ports der Reihe: A3H_BASE + 0..8 (Standard 8551)
NGX, PORT, ADMIN, TUN, TUNCTL, INTERNAL, HORN_PORT = _B, _B + 1, _B + 2, _B + 4, _B + 5, _B + 7, _B + 8
NAME = "saga.example.org"
BASE = f"https://{NAME}:{NGX}"
os.environ["HK_TEST_BASE"] = f"http://127.0.0.1:{PORT}"
os.environ["HK_TEST_ADMIN"] = f"http://127.0.0.1:{ADMIN}"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402

req, res = A_.req, A_.res
SECRET = os.environ.get("SESSION_SECRET") or os.environ["SECRET_KEY"]
STATE = tempfile.mkdtemp(prefix="a3h-")
CALLS = {"enabled": True, "stun": "", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "", "turn_password": "",
         "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
PW = "A3-Huelle-Pass-1234"
USERS = [("ca-h31", "Hanna Huelle"), ("ca-h32", "Henri Huelle"), ("ca-h33", "Hilde Browser")]
P = {"proc": None, "log": None, "ngx": None, "tun": None}


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:1500]), flush=True)


def gobuild(name, d, env_bin, pkg="."):
    b = os.environ.get(env_bin)
    if b:
        return b
    out = os.path.join(STATE, name)
    env = dict(os.environ, CGO_ENABLED="0", GOPROXY="direct", GOSUMDB="off", GOFLAGS="-mod=mod", GOTOOLCHAIN="local")
    cmd = ["go", "build", "-trimpath", "-o", out, pkg]
    if d == "gjallarhorn" and os.environ.get("A3_GOMOD"):
        cmd[2:2] = ["-modfile=" + os.environ["A3_GOMOD"]]
    r = subprocess.run(cmd, cwd=os.path.join(APP, d), capture_output=True, text=True, env=env)
    if r.returncode:
        print(r.stderr[-1500:])
    return None if r.returncode else out


def nginx_conf():
    """NPM-Nachbau mit "Websockets Support" (wie die Reihe a3_tunnel, eigene Ports und eigenes Zertifikat)."""
    d = os.path.join(STATE, "ngx")
    for sub in ("logs", "tmp"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    os.chmod(STATE, 0o755)                                 # nginx-Arbeiter laufen ohne root: Puffer fuer grosse Angebote
    os.chmod(d + "/tmp", 0o777)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=" + NAME,
                    "-addext", "subjectAltName=DNS:" + NAME, "-keyout", d + "/key.pem", "-out", d + "/cert.pem"], capture_output=True, check=True)
    t = d + "/tmp"
    conf = f"""worker_processes 1; pid {d}/nginx.pid; error_log {d}/logs/error.log warn; daemon on;
events {{ worker_connections 1024; }}
http {{
  access_log {d}/logs/access.log;
  client_body_temp_path {t}; proxy_temp_path {t}; fastcgi_temp_path {t}; uwsgi_temp_path {t}; scgi_temp_path {t};
  sendfile on; tcp_nopush on; tcp_nodelay on; keepalive_timeout 90s;
  proxy_connect_timeout 90s; proxy_send_timeout 90s; proxy_read_timeout 90s; client_max_body_size 2000m; client_body_buffer_size 1m;
  proxy_http_version 1.1; proxy_set_header Accept-Encoding ""; proxy_cache off;
  server {{
    listen 127.0.0.1:{NGX} ssl; server_name {NAME} 127.0.0.1;
    ssl_certificate {d}/cert.pem; ssl_certificate_key {d}/key.pem;
    location / {{
      proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $http_connection;
      proxy_set_header Host $host; proxy_set_header X-Forwarded-Scheme $scheme; proxy_set_header X-Forwarded-Proto $scheme;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; proxy_set_header X-Real-IP $remote_addr;
      proxy_pass http://127.0.0.1:{PORT};
    }}
  }}
}}
"""
    p = d + "/nginx.conf"
    open(p, "w").write(conf)
    return p


def start(media, node):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL", "SAGA_HORN")
           and not k.startswith(("HORN_", "SAGA_TLS", "TLS_"))}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT=str(ADMIN), ADMIN_HOST="127.0.0.1", HOST="127.0.0.1",
               STATE_DIR=STATE, BASE_URL=BASE, CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0",
               PYTHONPATH=APP, SECRET_KEY=os.environ["SECRET_KEY"], SESSION_SECRET=SECRET, GJALLARHORN_BIN=node,
               HORN_PORT=str(HORN_PORT), HORN_UDP="0", HORN_ICE_IPS="127.0.0.1", TRUSTED_PROXIES="127.0.0.1",
               SAGA_CF_API="http://127.0.0.1:9/client/v4")
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


def stop(p, t=25):
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(t)
        except subprocess.TimeoutExpired:
            p.kill()


def log_text():
    try:
        return open(os.path.join(STATE, "server.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def main():
    import db
    db.wait_ready()
    db.init_schema()
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(CALLS, horn=True, horn_builtin=True, horn_from=3))
    auth._settings_cache["v"] = None
    for u, nm in USERS:
        auth.create_user(u, PW, nm)
    media, node = gobuild("saga-media", "media", "SAGA_MEDIA_BIN"), gobuild("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    tunbin = gobuild("wstunnel", "gjallarhorn", "WSTUNNEL_BIN", "./e2e/wstunnel")
    ok("saga-media, gjallarhorn, wstunnel gebaut", bool(media and node and tunbin))
    ngx = nginx_conf()
    r = subprocess.run(["nginx", "-c", ngx], capture_output=True, text=True)
    P["ngx"] = ngx if r.returncode == 0 else None
    ok("nginx (NPM-Nachbau, Websockets an) gestartet", P["ngx"], r.stderr[-400:])
    rc = 1
    try:
        if not (media and node and tunbin and P["ngx"]):
            return
        t = start(media, node)
        ok(f"Instanz im Tunnel-Betrieb (BASE_URL {BASE}) laeuft", t is not None, log_text()[-2000:])
        if t is None:
            return
        t0 = time.time()
        while time.time() - t0 < 30 and "tunnel-betrieb: kein eigener tcp-port" not in log_text():
            time.sleep(0.3)
        time.sleep(2)
        ok("eingebauter Knoten im Tunnel-Betrieb", "tunnel-betrieb: kein eigener tcp-port" in log_text(), log_text()[-1500:])
        tlog = open(os.path.join(STATE, "wstunnel.log"), "a")
        P["tun"] = subprocess.Popen([tunbin, "-listen", f"127.0.0.1:{TUN}", "-url", f"wss://{NAME}:{NGX}/api/sfu/ws", "-dial", f"127.0.0.1:{NGX}",
                                     "-ctl", f"127.0.0.1:{TUNCTL}"], stdout=tlog, stderr=subprocess.STDOUT)
        time.sleep(0.5)
        env = dict(os.environ, SAGA_BASE=BASE, SAGA_USERS=",".join(u for u, _ in USERS), SAGA_PASS=PW, TUN_PORT=str(TUN),
                   TUN_CTL=f"127.0.0.1:{TUNCTL}", HOST_RULES=f"MAP {NAME} 127.0.0.1", NO_PROXY="*")
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env.pop(k, None)
        js = os.path.join(APP, "tests", "browser_a3_huelle.js")
        r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=900)
        for ln in r.stdout.splitlines():
            print("  [browser] " + ln, flush=True)
            if ln.startswith(("OK  ", "FAIL")):
                res.append(ln.startswith("OK"))
        ok("Browser-Teil beendet ohne Fehler", r.returncode == 0, (r.stdout[-800:] + r.stderr[-1500:]))
        lt = log_text()
        ok("Knoten-Log: Beine auf Weg ws (je Geraet Ton und Bild)", len(re.findall(r"bein auf .*weg=ws", lt)) >= 4,
           [ln for ln in lt.splitlines() if "bein auf" in ln][-8:])
        tl = open(os.path.join(STATE, "wstunnel.log"), encoding="utf-8", errors="replace").read()
        print("   wstunnel:", " | ".join(tl.strip().splitlines()[-6:])[:900])
    finally:
        stop(P["tun"], 5)
        stop(P["proc"])
        if P["log"]:
            P["log"].close()
        if P["ngx"]:
            subprocess.run(["nginx", "-c", P["ngx"], "-s", "stop"], capture_output=True)
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
        if os.environ.get("A3H_KEEP"):
            print("Zustand bleibt:", STATE)
        else:
            shutil.rmtree(STATE, ignore_errors=True)
        rc = 0 if all(res) else 1
        print(f"\nA3-HUELLE {sum(res)}/{len(res)}", "- ALLES GRUEN" if not rc else "- FEHLER")
    sys.exit(rc)


if __name__ == "__main__":
    main()
