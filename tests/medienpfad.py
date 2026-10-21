"""Medienpfad (2.4, Etappe E1): saga-media (media/, Go) vor uvicorn und die Ersatzwege ohne saga-media.

Startet eigene Instanzen auf derselben Datenbank (gleiches STATE_DIR fuer die Blobs):
  M  saga-media auf 8071 -> uvicorn intern 8072 (MEDIA_AUTHZ_TTL=3, ATTACH_MAX_MB=1, TRUSTED_PROXIES=10.9.9.9)
  P  nur Python auf 8073 (MEDIA_PY_STREAMS=2)
  X  nur Python auf 8074 mit MEDIA_ACCEL=nginx (X-Accel-Redirect)
  N  nginx auf 8075 vor X wie in der Anleitung (nur wenn nginx installiert ist und der Test als root laeuft)
  Y  nur Python auf 8076 mit MEDIA_ACCEL=caddy, davor C: Caddy auf 8077 (nur mit CADDY_BIN bzw. caddy im PATH)
Prueft: Range-Varianten, HEAD, 304/ETag, If-Range, Kopfzeilen wie bisher, Rechte (fremd, ohne X-Saga, ohne Anmeldung),
Rechteentzug nach hoechstens MEDIA_AUTHZ_TTL, geloeschte Dateien, Traversal/Symlink, /internal von aussen gesperrt,
SSE durch den Proxy, X-Forwarded-For nur von vertrauten Proxys, Upload-Grenzen unveraendert, Slowloris, 20 parallele
Abrufe (Bytes von saga-media, nicht von Python), Grenze gleichzeitiger Python-Stroeme, X-Accel-Redirect.
saga-media: SAGA_MEDIA_BIN, sonst "saga-media" im PATH, sonst mit "go build" aus media/ (fehlt beides: M-Teil uebersprungen).
Wegwerf-Konten md-*; raeumt auf."""
import base64, http.client, json, os, secrets, shutil, socket, subprocess, sys, tempfile, threading, time
from itsdangerous import TimestampSigner
import auth
from db import connect

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
PW = "Medien-Pass-1234"
TTL = 3
STATE = tempfile.mkdtemp(prefix="medtest-")
CHAT_DIR = os.path.join(STATE, "chat")
PORTS = {"M": 8071, "P": 8073, "X": 8074, "N": 8075, "Y": 8076, "C": 8077}
INTERNAL = 8072
res, procs = [], {}


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(k, m, p, cookie=None, hdr=None, raw=None, xsaga=True, timeout=30):
    c = http.client.HTTPConnection("127.0.0.1", PORTS[k], timeout=timeout)
    h = {"Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie
        if xsaga:
            h["X-Saga"] = "1"
    if raw is not None:
        h["Content-Type"] = "application/octet-stream"
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse()
    out = r.read()
    hd = {x.lower(): v for x, v in r.getheaders()}
    c.close()
    return r.status, out, hd


def wait(fn, t):
    end = time.time() + t
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def media_bin():
    b = os.environ.get("SAGA_MEDIA_BIN") or shutil.which("saga-media")
    if b:
        return b
    if shutil.which("go") and os.path.isdir(os.path.join(APP, "media")):
        out = os.path.join(STATE, "saga-media")
        r = subprocess.run(["go", "build", "-o", out, "."], cwd=os.path.join(APP, "media"), capture_output=True, text=True,
                           env=dict(os.environ, CGO_ENABLED="0"))
        if r.returncode == 0:
            return out
        print(r.stderr[-2000:], flush=True)
    return None


def env_of(k, **extra):
    e = {x: v for x, v in os.environ.items() if x not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                          "MEDIA_ACCEL", "HOST")}
    e.update(PORT=str(PORTS[k]), ADMIN_PORT="0", HOST="127.0.0.1", STATE_DIR=STATE, BASE_URL=f"http://127.0.0.1:{PORTS[k]}",
             CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0", PYTHONPATH=APP, ATTACH_MAX_MB="1",
             SECRET_KEY=SECRET, SESSION_SECRET=SECRET)
    e.update(extra)
    return e


def start(k, cmd, env):
    log = open(os.path.join(STATE, f"server-{k}.log"), "w")
    procs[k] = subprocess.Popen(cmd, env=env, cwd=APP, stdout=log, stderr=subprocess.STDOUT)


def healthy(k):
    try:
        return req(k, "GET", "/healthz", timeout=3)[0] == 200
    except OSError:
        return False


def stop_all():
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        try:
            p.wait(15)
        except subprocess.TimeoutExpired:
            p.kill()


def log_of(k):
    try:
        return open(os.path.join(STATE, f"server-{k}.log")).read()
    except OSError:
        return ""


# --- Testdaten direkt in der Datenbank (Chat ohne E2E-Einrichtung genuegt fuer Anhaenge) ------------------------------
def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE created_by LIKE 'md-%'")
        c.execute("DELETE FROM chat_file WHERE uid LIKE 'md-%'")
    for u in ("md-a", "md-b", "md-c"):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)


def blob(fid, data):
    os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
    with open(os.path.join(CHAT_DIR, fid), "wb") as f:
        f.write(data)


def setup():
    auth.bootstrap_admin()              # vor dem Start der Instanzen (sonst legen alle gleichzeitig das Admin-Konto an)
    cleanup()
    for u in ("md-a", "md-b", "md-c"):
        auth.create_user(u, PW, u)
    now = time.time()
    with connect() as c, c:
        cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', 'md-a', ?) RETURNING id", (now,)).fetchone()[0]
        for u in ("md-a", "md-b"):
            c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,?,?)", (cid, u, "admin" if u == "md-a" else "member", now))
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, body_ct, sig) "
                        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING seq", (secrets.token_hex(16), cid, "msg", "md-a", "d", int(now * 1000), now, "x", "x")).fetchone()[0]
    files = {}

    def add(name, size, attached=True):
        fid = secrets.token_urlsafe(18)
        data = os.urandom(size)
        blob(fid, data)
        with connect() as c, c:
            c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created, msg_seq) VALUES (?,?,?,?,?,?)",
                      (fid, cid, "md-a", size, now, seq if attached else None))
        files[name] = (fid, data)
    add("f1", 1048576 + 123)            # Nachricht, fuer A und B
    add("f2", 5000, attached=False)     # eigener Upload ohne Nachricht: nur A
    add("big", 48 << 20)                # fuer die Grenze gleichzeitiger Python-Stroeme
    add("gone", 2000)                   # wird geloescht
    add("rev", 3000)                    # Rechteentzug
    # Symlink im Blob-Verzeichnis (mit Datenbank-Zeile): nie ausliefern
    lid = secrets.token_urlsafe(18)
    os.symlink("/etc/hostname", os.path.join(CHAT_DIR, lid))
    with connect() as c, c:
        c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created, msg_seq) VALUES (?,?,?,?,?,?)", (lid, cid, "md-a", 10, now, seq))
    files["link"] = (lid, b"")
    return cid, files


# --- Pruefungen, die fuer jeden Weg gelten ----------------------------------------------------------------------------
def basics(k, A, B, Cc, files, ranges=True):
    fid, data = files["f1"]
    s, body, h = req(k, "GET", f"/api/chat/files/{fid}", A)
    ok(f"{k}: GET ganz -> 200, Inhalt gleich", s == 200 and body == data)
    ok(f"{k}: Kopfzeilen wie bisher (Cache immutable, sandbox, nosniff, attachment, octet-stream)",
       "immutable" in h.get("cache-control", "") and h.get("content-security-policy") == "sandbox; default-src 'none'"
       and h.get("x-content-type-options") == "nosniff" and h.get("content-disposition") == "attachment"
       and h.get("content-type", "").startswith("application/octet-stream"))
    ok(f"{k}: ETag = fid", h.get("etag") == f'"{fid}"')
    s, _, h2 = req(k, "GET", f"/api/chat/files/{fid}", A, {"If-None-Match": f'"{fid}"'})
    ok(f"{k}: If-None-Match -> 304", s == 304 and h2.get("etag") == f'"{fid}"')
    ok(f"{k}: If-None-Match W/ -> 304", req(k, "GET", f"/api/chat/files/{fid}", A, {"If-None-Match": f'W/"{fid}"'})[0] == 304)
    ok(f"{k}: If-None-Match anders -> 200", req(k, "GET", f"/api/chat/files/{fid}", A, {"If-None-Match": '"nein"'})[0] == 200)
    if ranges:
        n = len(data)
        for rg, a, b in (("bytes=0-99", 0, 99), ("bytes=-500", n - 500, n - 1), ("bytes=1000000-", 1000000, n - 1),
                         ("bytes=10-10", 10, 10), ("bytes=0-99999999", 0, n - 1)):
            s, body, h = req(k, "GET", f"/api/chat/files/{fid}", A, {"Range": rg})
            ok(f"{k}: Range {rg} -> 206 {a}-{b}", s == 206 and body == data[a:b + 1] and h.get("content-range") == f"bytes {a}-{b}/{n}"
               and h.get("content-length") == str(b - a + 1))
        s, _, h = req(k, "GET", f"/api/chat/files/{fid}", A, {"Range": f"bytes={n}-"})
        ok(f"{k}: Range hinter dem Ende -> 416", s == 416 and h.get("content-range") == f"bytes */{n}")
        s, body, h = req(k, "GET", f"/api/chat/files/{fid}", A, {"Range": "bytes=0-9", "If-Range": f'"{fid}"'})
        ok(f"{k}: If-Range passend -> 206", s == 206 and body == data[:10])
        s, body, _ = req(k, "GET", f"/api/chat/files/{fid}", A, {"Range": "bytes=0-9", "If-Range": '"alt"'})
        ok(f"{k}: If-Range anders -> 200 ganz", s == 200 and body == data)
        s, _, h = req(k, "HEAD", f"/api/chat/files/{fid}", A)
        ok(f"{k}: HEAD -> 200 mit Laenge", s == 200 and h.get("content-length") == str(n))
        if s != 200 or h.get("content-length") != str(n):
            print("     HEAD:", s, h, flush=True)
    ok(f"{k}: B (Mitglied) -> 200", req(k, "GET", f"/api/chat/files/{fid}", B)[0] == 200)
    ok(f"{k}: C (kein Mitglied) -> 404", req(k, "GET", f"/api/chat/files/{fid}", Cc)[0] == 404)
    ok(f"{k}: ohne Anmeldung -> 401", req(k, "GET", f"/api/chat/files/{fid}")[0] == 401)
    ok(f"{k}: ohne X-Saga (trotz gueltiger Sitzung) -> 401", req(k, "GET", f"/api/chat/files/{fid}", A, xsaga=False)[0] == 401)
    f2 = files["f2"][0]
    s, body, h = req(k, "GET", f"/api/chat/files/{f2}", A)
    ok(f"{k}: eigener Upload ohne Nachricht: A 200, no-store", s == 200 and body == files["f2"][1] and "no-store" in h.get("cache-control", ""))
    ok(f"{k}: ... B -> 404", req(k, "GET", f"/api/chat/files/{f2}", B)[0] == 404)
    ok(f"{k}: Symlink im Blob-Verzeichnis -> 404", req(k, "GET", f"/api/chat/files/{files['link'][0]}", A)[0] == 404)
    for p in ("/api/chat/files/..%2F..%2Fetc%2Fpasswd", "/api/chat/files/%2e%2e%2f%2e%2e%2fetc%2fhostname",
              f"/api/chat/files/{fid}/../{f2}", "/api/chat/files/../../etc/passwd", f"/api/chat/files/{fid}%00"):
        s, body, _ = req(k, "GET", p, A)
        ok(f"{k}: Traversal {p[:40]} -> kein Inhalt", s in (400, 404) and b"root:" not in body)
    s, _, _ = req(k, "GET", f"/internal/media/authz?fid={fid}", A, {"X-Saga-Media-Key": "0" * 64})
    ok(f"{k}: /internal/media/authz von aussen -> 404", s == 404)


def slow_reader(k, fid, A, got, hold):
    """Download beginnen, nur den Anfang lesen und die Verbindung halten (bis hold gesetzt ist)."""
    s = socket.create_connection(("127.0.0.1", PORTS[k]), timeout=60)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.sendall(f"GET /api/chat/files/{fid} HTTP/1.1\r\nHost: x\r\nCookie: saga={A}\r\nX-Saga: 1\r\n\r\n".encode())
    first = s.recv(64)
    got.append((time.time(), first[:12]))
    hold.wait(60)
    s.close()


def main():
    cid, files = setup()
    A, B, Cc = ck("md-a"), ck("md-b"), ck("md-c")   # nach dem Anlegen: pwc = jetzt + 5 s muss nach dem Konto-Zeitstempel liegen
    binp = media_bin()
    if binp:
        start("M", [binp, "--", sys.executable, "run.py"],
              env_of("M", MEDIA_INTERNAL_PORT=str(INTERNAL), MEDIA_AUTHZ_TTL=str(TTL), TRUSTED_PROXIES="10.9.9.9", MEDIA_STALL_TIMEOUT="5"))
    start("P", [sys.executable, "run.py"], env_of("P", MEDIA_PY_STREAMS="2"))
    start("X", [sys.executable, "run.py"], env_of("X", MEDIA_ACCEL="nginx"))
    cad = os.environ.get("CADDY_BIN") or shutil.which("caddy")
    if cad:
        start("Y", [sys.executable, "run.py"], env_of("Y", MEDIA_ACCEL="caddy"))
    for k in procs:
        up = wait(lambda: healthy(k), 60)
        ok(f"{k}: gestartet", up)
        if not up:
            print(log_of(k)[-3000:], flush=True)

    # --- Python ohne saga-media (P) -------------------------------------------------------------------------------
    basics("P", A, B, Cc, files)
    big = files["big"][0]
    got, hold = [], threading.Event()
    ts = [threading.Thread(target=slow_reader, args=("P", big, A, got, hold), daemon=True) for _ in range(2)]
    for t in ts:
        t.start()
    wait(lambda: len(got) == 2, 10)
    t3 = time.time()
    third = {}
    th = threading.Thread(target=lambda: third.update(r=req("P", "GET", f"/api/chat/files/{files['f2'][0]}", A, timeout=40)), daemon=True)
    th.start()
    time.sleep(1.5)
    ok("P: zwei Stroeme offen, dritter wartet (MEDIA_PY_STREAMS=2)", len(got) == 2 and "r" not in third)
    hold.set()
    th.join(35)
    ok("P: ... und laeuft nach Freigabe", third.get("r", (0,))[0] == 200 and time.time() - t3 >= 1.4)
    ok("P: /internal/media/authz ohne saga-media -> 404",
       req("P", "GET", f"/internal/media/authz?fid={files['f1'][0]}", A, {"X-Saga-Media-Key": ""})[0] == 404)

    # --- X-Accel-Redirect (X) -------------------------------------------------------------------------------------
    fid = files["f1"][0]
    s, body, h = req("X", "GET", f"/api/chat/files/{fid}", A)
    ok("X: X-Accel-Redirect statt Inhalt", s == 200 and h.get("x-accel-redirect") == f"/_saga_media/{fid}" and body == b""
       and "immutable" in h.get("cache-control", "") and h.get("etag") == f'"{fid}"')
    s, _, h = req("X", "GET", f"/api/chat/files/{fid}", Cc)
    ok("X: fremd -> 404 ohne X-Accel-Redirect", s == 404 and "x-accel-redirect" not in h)
    ok("X: ohne Anmeldung -> 401", req("X", "GET", f"/api/chat/files/{fid}")[0] == 401)

    # --- nginx vor X (Konfiguration wie in docs/ANLEITUNG.md, "Medienpfad") ----------------------------------------
    ng = shutil.which("nginx")
    if ng and os.geteuid() == 0:
        conf = os.path.join(STATE, "nginx.conf")
        tmp = {k: os.path.join(STATE, "ng-" + k) for k in ("body", "proxy", "fcgi", "uwsgi", "scgi")}
        open(conf, "w").write(f"""user root; worker_processes 1; daemon off; pid {STATE}/nginx.pid; error_log {STATE}/nginx-err.log;
events {{ worker_connections 64; }}
http {{
  access_log off; client_body_temp_path {tmp['body']}; proxy_temp_path {tmp['proxy']}; fastcgi_temp_path {tmp['fcgi']};
  uwsgi_temp_path {tmp['uwsgi']}; scgi_temp_path {tmp['scgi']};
  server {{
    listen 127.0.0.1:{PORTS['N']};
    location / {{ proxy_pass http://127.0.0.1:{PORTS['X']}; proxy_set_header Host $host; }}
    location /_saga_media/ {{
        internal;
        alias {CHAT_DIR}/;
        types {{ }}
        default_type application/octet-stream;
        add_header X-Content-Type-Options nosniff always;
        add_header Content-Security-Policy "sandbox; default-src 'none'" always;
    }}
  }}
}}
""")
        start("N", [ng, "-c", conf], dict(os.environ))
        ok("N: nginx gestartet", wait(lambda: healthy("N"), 20))
        s, body, h = req("N", "GET", f"/api/chat/files/{fid}", A)
        ok("N: nginx liefert per X-Accel-Redirect (Inhalt, Cache, sandbox, nosniff, attachment)", s == 200 and body == files["f1"][1]
           and "immutable" in h.get("cache-control", "") and h.get("content-security-policy") == "sandbox; default-src 'none'"
           and h.get("x-content-type-options") == "nosniff" and h.get("content-disposition") == "attachment"
           and "x-accel-redirect" not in h)
        s, body, _ = req("N", "GET", f"/api/chat/files/{fid}", A, {"Range": "bytes=0-99"})
        ok("N: Range -> 206", s == 206 and body == files["f1"][1][:100])
        ok("N: fremd -> 404", req("N", "GET", f"/api/chat/files/{fid}", Cc)[0] == 404)
        ok("N: /_saga_media/ von aussen -> 404", req("N", "GET", f"/_saga_media/{fid}", A)[0] == 404)
    else:
        print("HINWEIS: nginx fehlt (oder nicht root) - Pruefung mit echtem nginx uebersprungen", flush=True)

    # --- Caddy vor Y (Konfiguration wie in docs/ANLEITUNG.md, "Medienpfad") ----------------------------------------
    if cad:
        s, _, h = req("Y", "GET", f"/api/chat/files/{fid}", A)
        ok("Y: MEDIA_ACCEL=caddy -> X-Accel-Redirect /<fid>", s == 200 and h.get("x-accel-redirect") == f"/{fid}")
        conf = os.path.join(STATE, "Caddyfile")
        open(conf, "w").write(f"""{{
  admin off
  auto_https off
  storage file_system {STATE}/caddy
}}
http://127.0.0.1:{PORTS['C']} {{
  reverse_proxy 127.0.0.1:{PORTS['Y']} {{
    @accel header X-Accel-Redirect *
    handle_response @accel {{
      root * {CHAT_DIR}
      rewrite * {{rp.header.X-Accel-Redirect}}
      copy_response_headers {{
        include Cache-Control Content-Disposition
      }}
      header Content-Type application/octet-stream
      header X-Content-Type-Options nosniff
      header Content-Security-Policy "sandbox; default-src 'none'"
      file_server
    }}
  }}
}}
""")
        start("C", [cad, "run", "--config", conf, "--adapter", "caddyfile"], dict(os.environ, HOME=STATE, XDG_DATA_HOME=STATE, XDG_CONFIG_HOME=STATE))
        ok("C: Caddy gestartet", wait(lambda: healthy("C"), 20))
        s, body, h = req("C", "GET", f"/api/chat/files/{fid}", A)
        ok("C: Caddy liefert per handle_response (Inhalt, Cache, sandbox, nosniff, attachment)", s == 200 and body == files["f1"][1]
           and "immutable" in h.get("cache-control", "") and h.get("content-security-policy") == "sandbox; default-src 'none'"
           and h.get("x-content-type-options") == "nosniff" and h.get("content-disposition") == "attachment"
           and "x-accel-redirect" not in h)
        if s != 200:
            print("     Caddy:", s, h, body[:200], log_of("C")[-1500:], flush=True)
        s, body, _ = req("C", "GET", f"/api/chat/files/{fid}", A, {"Range": "bytes=0-99"})
        ok("C: Range -> 206", s == 206 and body == files["f1"][1][:100])
        ok("C: fremd -> 404", req("C", "GET", f"/api/chat/files/{fid}", Cc)[0] == 404)
        ok("C: Datei-Pfad direkt -> kein Inhalt", req("C", "GET", f"/{fid}", A)[1] != files["f1"][1])
    else:
        print("HINWEIS: Caddy fehlt (CADDY_BIN) - Pruefung mit echtem Caddy uebersprungen", flush=True)

    # --- saga-media (M) -------------------------------------------------------------------------------------------
    if not binp:
        print("HINWEIS: saga-media fehlt (SAGA_MEDIA_BIN/PATH/go) - Teil M uebersprungen", flush=True)
    else:
        basics("M", A, B, Cc, files)
        # Sicherheitsrunde 2 (S1b): Python kennzeichnet angemeldete Anfragen intern (X-Saga-Media-Auth), saga-media
        # entfernt den Kopf; ohne gueltige Sitzung fehlt er schon bei Python
        c0 = http.client.HTTPConnection("127.0.0.1", INTERNAL, timeout=10)
        c0.request("GET", "/api/chats/unread", headers={"Cookie": "saga=" + A, "X-Saga": "1"})
        r0 = c0.getresponse(); r0.read(); h_ok = r0.getheader("x-saga-media-auth"); c0.close()
        c0 = http.client.HTTPConnection("127.0.0.1", INTERNAL, timeout=10)
        c0.request("GET", "/api/chats/unread", headers={"Cookie": "saga=gefaelscht", "X-Saga": "1"})
        r0 = c0.getresponse(); r0.read(); h_bad = r0.getheader("x-saga-media-auth"); c0.close()
        _, _, hm = req("M", "GET", "/api/chats/unread", A)
        ok(f"M: Anmelde-Hinweis von Python nur bei gueltiger Sitzung ({h_ok}, {h_bad}), saga-media gibt ihn nicht weiter",
           h_ok == "1" and h_bad is None and "x-saga-media-auth" not in hm)
        L = log_of("M")
        ok("M: Dateien liefert saga-media (Zugriffszeilen), nicht Python", f"/api/chat/files/{fid} HTTP/1.1\" 206" in L and "[saga-media" in L)
        # 20 parallele Abrufe derselben Datei
        out = []

        def one():
            out.append(req("M", "GET", f"/api/chat/files/{fid}", A))
        th = [threading.Thread(target=one) for _ in range(20)]
        for t in th:
            t.start()
        for t in th:
            t.join(60)
        ok("M: 20 parallele Abrufe vollstaendig", len(out) == 20 and all(s == 200 and b == files["f1"][1] for s, b, _ in out))
        n_py = sum(1 for x in log_of("M").splitlines() if f"/api/chat/files/{fid} HTTP" in x and "[saga-media" not in x and '" 200' in x)
        ok("M: keine erfolgreichen Datei-Abrufe durch Python", n_py == 0)

        # Rechteentzug: wirkt spaetestens nach MEDIA_AUTHZ_TTL
        rev = files["rev"][0]
        ok("M: B holt rev", req("M", "GET", f"/api/chat/files/{rev}", B)[0] == 200)
        with connect() as c, c:
            c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid='md-b'", (time.time(), cid))
        t0 = time.time()
        gone = wait(lambda: req("M", "GET", f"/api/chat/files/{rev}", B)[0] == 404, TTL + 3)
        ok(f"M: Rechteentzug wirkt nach hoechstens {TTL} s (hier {time.time() - t0:.1f} s)", gone and time.time() - t0 <= TTL + 1.5)
        ok("M: Entzug gilt auch fuer andere Dateien (neue Abfrage)", req("M", "GET", f"/api/chat/files/{files['f2'][0]}", B)[0] == 404)
        with connect() as c, c:
            c.execute("UPDATE chat_member SET left_at=NULL WHERE chat_id=? AND uid='md-b'", (cid,))

        # Geloeschte Dateien: Blob weg -> sofort 404; Datenbank-Zeile weg -> nach TTL 404
        g = files["gone"][0]
        ok("M: gone vorher 200", req("M", "GET", f"/api/chat/files/{g}", A)[0] == 200)
        os.remove(os.path.join(CHAT_DIR, g))
        ok("M: Blob geloescht -> sofort 404", req("M", "GET", f"/api/chat/files/{g}", A)[0] == 404)
        blob(g, b"neu" * 100)
        ok("M: wieder da -> 200", req("M", "GET", f"/api/chat/files/{g}", A)[0] == 200)
        with connect() as c, c:
            c.execute("DELETE FROM chat_file WHERE id=?", (g,))
        ok(f"M: Datenbank-Zeile geloescht -> 404 nach hoechstens {TTL} s",
           wait(lambda: req("M", "GET", f"/api/chat/files/{g}", A)[0] == 404, TTL + 2))

        # SSE durch den Proxy: hello sofort, Ereignis ohne Pufferung
        sse = {"buf": b""}
        sc = socket.create_connection(("127.0.0.1", PORTS["M"]), timeout=10)
        sc.sendall(f"GET /api/events HTTP/1.1\r\nHost: x\r\nCookie: saga={B}\r\nAccept: text/event-stream\r\n\r\n".encode())

        def sse_read():
            try:
                while True:
                    d = sc.recv(65536)
                    if not d:
                        return
                    sse["buf"] += d
            except OSError:
                return
        threading.Thread(target=sse_read, daemon=True).start()
        ok("M: SSE hello durch den Proxy (< 3 s)", wait(lambda: b"event: hello" in sse["buf"], 3))
        t0 = time.time()
        s, _, _ = req("M", "POST", f"/api/chats/{cid}/typing", A)
        ok("M: SSE-Ereignis kommt sofort an (ungepuffert)", s == 200 and wait(lambda: b"chat.typing" in sse["buf"], 2))
        print(f"     SSE-Ereignis nach {(time.time() - t0) * 1000:.0f} ms", flush=True)
        sc.close()

        # Upload-Grenzen unveraendert (ATTACH_MAX_MB=1, durch den Proxy gestreamt)
        s, up, _ = req("M", "POST", f"/api/chats/{cid}/files", A, raw=os.urandom(500000))
        up = json.loads(up) if s == 200 else {}
        ok("M: Upload 500 KB -> 200", s == 200 and up.get("size") == 500000)
        s, body, _ = req("M", "GET", f"/api/chat/files/{up.get('id')}", A)
        ok("M: ... und ueber saga-media abrufbar", s == 200 and len(body) == 500000)
        s, _, _ = req("M", "POST", f"/api/chats/{cid}/files", A, raw=os.urandom(1200000))
        ok("M: Upload ueber ATTACH_MAX_MB -> 413", s == 413)

        # X-Forwarded-For nur von vertrauten Proxys (TRUSTED_PROXIES=10.9.9.9; die Tests kommen von 127.0.0.1)
        probe = secrets.token_hex(6)
        req("M", "GET", f"/healthz?p={probe}", hdr={"X-Forwarded-For": "6.6.6.6", "X-Forwarded-Proto": "https"})
        time.sleep(0.5)
        line = next((x for x in log_of("M").splitlines() if probe in x), "")
        ok("M: eingeschleustes X-Forwarded-For zaehlt nicht (Client = Gegenstelle)", "127.0.0.1" in line and "6.6.6.6" not in line)
        if "6.6.6.6" in line or "127.0.0.1" not in line:
            print("     Log:", line, flush=True)

        # Slowloris: Kopfzeilen kommen nie fertig -> Verbindung nach ReadHeaderTimeout (15 s) zu
        def closed_within(payload, limit):
            so = socket.create_connection(("127.0.0.1", PORTS["M"]), timeout=limit + 5)
            so.sendall(payload)
            t = time.time()
            try:
                while so.recv(4096):
                    pass
            except OSError:
                pass
            so.close()
            return time.time() - t
        dt = closed_within(b"GET /healthz HTTP/1.1\r\nHost: x\r\nX-Langsam: ", 20)
        ok(f"M: Slowloris (Kopf) -> zu nach {dt:.1f} s", dt <= 20)
        dt = closed_within(f"POST /api/chats/{cid}/files HTTP/1.1\r\nHost: x\r\nCookie: saga={A}\r\nX-Saga: 1\r\n"
                           f"Content-Type: application/octet-stream\r\nContent-Length: 100000\r\n\r\nabc".encode(), 15)
        ok(f"M: Upload stockt -> Antwort/zu nach {dt:.1f} s (MEDIA_STALL_TIMEOUT=5)", dt <= 15)

    stop_all()
    if binp:
        ok("M: beendet sich mit SIGTERM (beide Prozesse)", procs["M"].returncode == 0
           and not wait(lambda: healthy("M"), 1))
    # abgebrochener Upload (Slowloris-Pruefung) -> ClientDisconnect in Python: erwartet, wie ohne saga-media
    errs = sum(log_of(k).count("Traceback") - log_of(k).count("starlette.requests.ClientDisconnect") for k in procs if k not in ("N", "C"))
    ok(f"Serverfehler in den Instanzen: {errs}", errs == 0)
    if errs:
        for k in procs:
            print("\n".join(x for x in log_of(k).splitlines() if "Error" in x)[-2000:])


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:  # noqa: BLE001
        import traceback; traceback.print_exc(); ok(f"Abbruch: {ex}", False)
    finally:
        stop_all()
        try:
            cleanup()
        finally:
            if os.environ.get("MED_KEEP") != "1":
                shutil.rmtree(STATE, ignore_errors=True)
    print(f"\nMedienpfad {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
