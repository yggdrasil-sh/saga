"""Videos im Chat (2.4, Etappe E2): fortsetzbarer Upload im Format saga-file/v2, Range-Abruf, Medien-Token, Grenzen.

Startet eigene Instanzen auf derselben Datenbank (gleiches STATE_DIR):
  M  saga-media auf 8061 -> uvicorn intern 8062 (MEDIA_AUTHZ_TTL=3)  - Bytes ueber saga-media
  P  nur Python auf 8063                                              - Ersatzweg ohne saga-media
Je Weg: /api/chat/me (limits), Upload anlegen (ungueltige Groesse, Grenze VIDEO_MAX_MB, Kontingent mit laufenden Uploads,
hoechstens 2 gleichzeitig), Teile hochladen, Abbruch mitten im Teil und Fortsetzen ab HEAD Upload-Offset, falsche Stelle (409),
Content-Range/Length falsch, Teil zu gross, fremder Upload, ohne Anmeldung, falscher Kopf beim Abschluss (verworfen),
Abschluss -> Anhang fmt=2, Nachricht mit dem Anhang, Range-Varianten auf dem v2-Blob mit Entschluesselung je Block
(wie die Apps: Klartext-Bereich -> Bloecke), Medien-Token (ohne Cookie, falsche Datei, veraendert, Rechteentzug nach
hoechstens MEDIA_AUTHZ_TTL), Admin-Grenzen (chat_quota_mb, chat_video_max_mb), Aufraeumen verfallener Uploads (purge),
Kontoloeschung. saga-media: SAGA_MEDIA_BIN, sonst "saga-media" im PATH, sonst go build (fehlt alles: M uebersprungen).
Wegwerf-Konten vd-*; raeumt auf."""
import base64, http.client, json, os, secrets, shutil, socket, subprocess, sys, tempfile, time
from itsdangerous import TimestampSigner

STATE = tempfile.mkdtemp(prefix="vidtest-")
os.environ["STATE_DIR"] = STATE                       # vor dem Import von saga.* (CHAT_DIR fuer purge im Testprozess)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
import chatlib as C  # noqa: E402
from db import connect  # noqa: E402
from saga import file_v2 as FV  # noqa: E402

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = os.environ["SESSION_SECRET"]
S = TimestampSigner(SECRET)
PW = "Video-Pass-1234"
TTL = 3
CHAT_DIR = os.path.join(STATE, "chat")
PORTS = {"M": 8061, "P": 8063}
INTERNAL = 8062
res, procs = [], {}


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u, did=None):
    d = {"uid": u, "name": u, "pwc": time.time() + 5, "lo": secrets.token_hex(6)}
    if did is not None:
        d["did"] = did
    return S.sign(base64.b64encode(json.dumps(d).encode())).decode()


def req(k, m, p, cookie=None, hdr=None, raw=None, xsaga=True, timeout=60, js=None):
    c = http.client.HTTPConnection("127.0.0.1", PORTS[k], timeout=timeout)
    h = {"Accept-Language": "de"}
    if cookie:
        h["Cookie"] = "saga=" + cookie
        if xsaga:
            h["X-Saga"] = "1"
    if js is not None:
        raw = json.dumps(js).encode()
        h["Content-Type"] = "application/json"
    elif raw is not None:
        h["Content-Type"] = "application/octet-stream"
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse()
    out = r.read()
    hd = {x.lower(): v for x, v in r.getheaders()}
    c.close()
    return r.status, out, hd


def jd(b):
    try:
        return json.loads(b or b"{}")
    except ValueError:
        return {}


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
             VIDEO_MAX_MB="4", CHAT_QUOTA_MB="12", SECRET_KEY=SECRET, SESSION_SECRET=SECRET)
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


USERS = ("vd-a", "vd-b", "vd-c")


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE created_by LIKE 'vd-%'")
        c.execute("DELETE FROM chat_file WHERE uid LIKE 'vd-%'")
        c.execute("DELETE FROM chat_upload WHERE uid LIKE 'vd-%'")
    for u in USERS:
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)
    for k in ("chat_quota_mb", "chat_video_max_mb"):
        auth.save_setting(k, 0)


def setup():
    auth.bootstrap_admin()
    cleanup()
    for u in USERS:
        auth.create_user(u, PW, u)
    now = time.time()
    with connect() as c, c:
        cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', 'vd-a', ?) RETURNING id", (now,)).fetchone()[0]
        for u in ("vd-a", "vd-b"):
            c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,?,?)", (cid, u, "admin" if u == "vd-a" else "member", now))
    return cid


def attach(cid, fid, sender="vd-a"):
    """Anhang einer (Attrappen-)Nachricht zuordnen - wie nach POST /messages (E2E-Inhalt ist hier egal)."""
    now = time.time()
    with connect() as c, c:
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, body_ct, sig) "
                        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING seq", (secrets.token_hex(16), cid, "msg", sender, "d", int(now * 1000), now, "x", "x")).fetchone()[0]
        c.execute("UPDATE chat_file SET msg_seq=? WHERE id=?", (seq, fid))


def put_part(k, upid, cookie, data, a, size, hdr=None):
    h = {"Content-Range": f"bytes {a}-{a + len(data) - 1}/{size}"}
    h.update(hdr or {})
    return req(k, "PUT", f"/api/chat/uploads/{upid}", cookie, h, raw=data)


def broken_put(k, upid, cookie, data, a, size, send):
    """Teil ankuendigen, nur send Bytes schicken und die Verbindung schliessen (Netzabbruch)."""
    so = socket.create_connection(("127.0.0.1", PORTS[k]), timeout=10)
    so.sendall((f"PUT /api/chat/uploads/{upid} HTTP/1.1\r\nHost: x\r\nCookie: saga={cookie}\r\nX-Saga: 1\r\n"
                f"Content-Type: application/octet-stream\r\nContent-Range: bytes {a}-{a + len(data) - 1}/{size}\r\n"
                f"Content-Length: {len(data)}\r\n\r\n").encode() + data[:send])
    time.sleep(0.4)
    so.close()
    time.sleep(0.6)


def head_off(k, upid, cookie):
    s, _, h = req(k, "HEAD", f"/api/chat/uploads/{upid}", cookie)
    return s, int(h.get("upload-offset", "-1")), int(h.get("upload-length", "-1"))


def plain_range(k, fid, cookie, key, size, a, b, hdr=None):
    """Wie eine App: Klartext a..b -> Bloecke -> Ciphertext-Range holen -> Bloecke entschluesseln -> Ausschnitt."""
    i, j, fr, to = C.f2_range(size, a, b)
    s, head, _ = req(k, "GET", f"/api/chat/files/{fid}", cookie, {"Range": "bytes=0-47", **(hdr or {})})
    if s != 206:
        return None
    s, body, h = req(k, "GET", f"/api/chat/files/{fid}", cookie, {"Range": f"bytes={fr}-{to}", **(hdr or {})})
    if s != 206:
        return None
    out = b""
    for n in range(i, j + 1):
        off = C.f2_block_off(n) - fr
        out += C.f2_open(head, n, body[off:off + C.F2_CS + 16], key)
    return out[a - i * C.F2_CS:b - i * C.F2_CS + 1]


def path_upload(k, cid, A, B, Cc):
    """Upload, Fortsetzen, Fehlerfaelle, Range und Entschluesselung fuer einen Weg (M oder P)."""
    s, b, _ = req(k, "GET", "/api/chat/me", A)
    lim = jd(b).get("limits") or {}
    ok(f"{k}: /api/chat/me limits (file2, video_max 4 MB, upload_part 8 MiB, cs, quota 12 MB)", s == 200 and lim.get("file2") is True
       and lim.get("video_max") == 4 << 20 and lim.get("upload_part") == 8 << 20 and lim.get("file2_cs") == FV.CS
       and lim.get("quota") == 12 << 20 and lim.get("uploads_max") == 2 and isinstance(lim.get("used"), int) and lim.get("file_max") == 1 << 20)
    plain = os.urandom(6 * C.F2_CS + 12345)
    blob, key = C.encrypt_file_f2(plain)
    size = len(blob)
    # --- Anlegen: Pruefungen ----------------------------------------------------------------------------------------
    ok(f"{k}: Groesse, die kein v2-Blob sein kann -> 400", req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": 63, "fmt": 2})[0] == 400
       and req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": 48 + C.F2_CS + 16 + 8, "fmt": 2})[0] == 400)
    ok(f"{k}: fmt 1 -> 400", req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": size, "fmt": 1})[0] == 400)
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": FV.cipher_size((4 << 20) + 1), "fmt": 2})
    ok(f"{k}: ueber VIDEO_MAX_MB -> 413 mit Hinweis", s == 413 and "4 MB" in jd(b).get("error", ""))
    ok(f"{k}: fremder Chat -> 404", req(k, "POST", f"/api/chats/{cid}/uploads", Cc, js={"size": size, "fmt": 2})[0] == 404)
    ok(f"{k}: ohne Anmeldung -> 401", req(k, "POST", f"/api/chats/{cid}/uploads", None, js={"size": size, "fmt": 2})[0] == 401)
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": size, "fmt": 2})
    up = jd(b)
    upid = up.get("upload", "")
    ok(f"{k}: Upload angelegt (offset 0, part_max, Verfall 24 h)", s == 200 and up.get("offset") == 0 and up.get("size") == size
       and up.get("part_max") == 8 << 20 and 23 * 3600 < up.get("expires", 0) - time.time() <= 24 * 3600 and up.get("url") == f"/api/chat/uploads/{upid}")
    ok(f"{k}: HEAD am Anfang -> Offset 0", head_off(k, upid, A) == (204, 0, size))
    ok(f"{k}: B (anderes Konto) HEAD -> 404", head_off(k, upid, B)[0] == 404)
    ok(f"{k}: B PUT -> 404", put_part(k, upid, B, blob[:1000], 0, size)[0] == 404)
    ok(f"{k}: PUT ohne Anmeldung -> 401", put_part(k, upid, None, blob[:1000], 0, size)[0] == 401)
    ok(f"{k}: PUT ohne X-Saga -> 401", req(k, "PUT", f"/api/chat/uploads/{upid}", A, {"Content-Range": f"bytes 0-9/{size}"},
                                         raw=blob[:10], xsaga=False)[0] == 401)
    # --- Teile, Abbruch, Fortsetzen -------------------------------------------------------------------------------------
    p1 = 300000
    s, b, h = put_part(k, upid, A, blob[:p1], 0, size)
    ok(f"{k}: Teil 1 -> 200 offset", s == 200 and jd(b) == {"offset": p1, "done": False} and h.get("upload-offset") == str(p1))
    s, b, h = put_part(k, upid, A, blob[:1000], 0, size)
    ok(f"{k}: falsche Stelle -> 409 mit offset", s == 409 and jd(b).get("offset") == p1 and h.get("upload-offset") == str(p1))
    ok(f"{k}: Content-Range Gesamtgroesse falsch -> 400", put_part(k, upid, A, blob[p1:p1 + 10], p1, size + 1)[0] == 400)
    ok(f"{k}: Content-Range kaputt -> 400", req(k, "PUT", f"/api/chat/uploads/{upid}", A, {"Content-Range": "bytes x-y/z"}, raw=b"1")[0] == 400)
    ok(f"{k}: Content-Length passt nicht zum Bereich -> 400",
       req(k, "PUT", f"/api/chat/uploads/{upid}", A, {"Content-Range": f"bytes {p1}-{p1 + 99}/{size}"}, raw=blob[p1:p1 + 50])[0] == 400)
    ok(f"{k}: Teil groesser als part_max -> 413", req(k, "PUT", f"/api/chat/uploads/{upid}", A,
                                                     {"Content-Range": f"bytes {p1}-{p1 + (8 << 20)}/{size}", "Content-Length": "1"}, raw=b"1")[0] in (400, 413))
    ok(f"{k}: nach Fehlern unveraendert", head_off(k, upid, A)[1] == p1)
    part = blob[p1:p1 + 400000]
    broken_put(k, upid, A, part, p1, size, 150000)
    s, off, ln = head_off(k, upid, A)
    ok(f"{k}: Abbruch mitten im Teil -> HEAD zeigt den geschriebenen Stand ({off})", s == 204 and p1 < off <= p1 + 150000 and ln == size)
    s, b, _ = put_part(k, upid, A, blob[off:off + 500000], off, size)
    ok(f"{k}: fortgesetzt ab Upload-Offset", s == 200 and jd(b).get("offset") == off + 500000)
    if s != 200 or jd(b).get("offset") != off + 500000:
        print("     ", s, b[:300], off, head_off(k, upid, A), flush=True)
    off += 500000
    s, b, h = put_part(k, upid, A, blob[off:], off, size)
    d = jd(b)
    ok(f"{k}: letzter Teil -> Anhang {{id, size, fmt 2}}", s == 200 and d.get("id") == upid and d.get("size") == size and d.get("fmt") == 2
       and d.get("done") is True)
    fid = d.get("id")
    with connect() as c:
        r = c.execute("SELECT size, fmt, msg_seq FROM chat_file WHERE id=?", (fid,)).fetchone()
        nu = c.execute("SELECT COUNT(*) FROM chat_upload WHERE id=?", (fid,)).fetchone()[0]
    ok(f"{k}: chat_file fmt=2, Upload-Eintrag weg, Teil-Datei verschoben", r == (size, 2, None) and nu == 0
       and not os.path.exists(os.path.join(CHAT_DIR, "up", fid)) and os.path.getsize(os.path.join(CHAT_DIR, fid)) == size)
    ok(f"{k}: PUT nach Abschluss -> 404", put_part(k, upid, A, b"x", 0, size)[0] == 404)
    s, _, h = req(k, "HEAD", f"/api/chat/uploads/{upid}", A)
    ok(f"{k}: HEAD nach Abschluss -> fertig (Offset = Groesse, Upload-Done)", s == 204 and h.get("upload-offset") == str(size)
       and h.get("upload-done") == "1")
    # --- Abruf: ganzer Blob, Range, Entschluesselung je Block ----------------------------------------------------------
    s, body, h = req(k, "GET", f"/api/chat/files/{fid}", A)
    ok(f"{k}: eigener Anhang (ohne Nachricht) ganz -> entschluesselbar", s == 200 and C.decrypt_file_f2(body, key) == plain)
    ok(f"{k}: B ohne Nachricht -> 404", req(k, "GET", f"/api/chat/files/{fid}", B)[0] == 404)
    attach(cid, fid)
    for a, b in ((0, 0), (0, 99), (C.F2_CS - 10, C.F2_CS + 10), (2 * C.F2_CS + 5, len(plain) - 1), (len(plain) - 1, len(plain) - 1)):
        ok(f"{k}: Klartext {a}-{b} ueber Range + Bloecke (B)", plain_range(k, fid, B, key, len(plain), a, b) == plain[a:b + 1])
    s, body, h = req(k, "GET", f"/api/chat/files/{fid}", B, {"Range": "bytes=-16"})
    ok(f"{k}: Range -16 (Ende) -> 206, Accept-Ranges", s == 206 and body == blob[-16:] and h.get("accept-ranges") == "bytes"
       and h.get("content-range") == f"bytes {size - 16}-{size - 1}/{size}")
    s, body, h = req(k, "GET", f"/api/chat/files/{fid}", B, {"Range": "bytes=0-9,20-29"})
    ok(f"{k}: mehrere Bereiche -> erster Bereich (Python: multipart)", s == 206 and (body == blob[:10] or (k == "P" and
                                                                                   h.get("content-type", "").startswith("multipart/byteranges"))))
    ok(f"{k}: hinter dem Ende -> 416", req(k, "GET", f"/api/chat/files/{fid}", B, {"Range": f"bytes={size}-"})[0] == 416)
    ok(f"{k}: C (kein Mitglied) -> 404", req(k, "GET", f"/api/chat/files/{fid}", Cc, {"Range": "bytes=0-9"})[0] == 404)
    # --- Medien-Token ---------------------------------------------------------------------------------------------------
    s, b, _ = req(k, "POST", f"/api/chat/files/{fid}/token", B)
    tk = jd(b)
    ok(f"{k}: Medien-Token (15 min)", s == 200 and tk.get("token", "").startswith("m2.") and tk.get("ttl") == 900)
    try:   # 2.4 (Pruefteam S2): undurchsichtig - weder Benutzername noch fid lesbar
        raw = base64.urlsafe_b64decode(tk["token"][3:] + "=" * (-len(tk["token"][3:]) % 4))
    except Exception:  # noqa: BLE001
        raw = b""
    ok(f"{k}: Medien-Token undurchsichtig (kein Benutzername/keine fid lesbar)", len(raw) > 28 and b"vd-b" not in raw
       and fid.encode() not in raw and "vd-b" not in tk["token"])
    T = {"X-Saga-Media-Token": tk.get("token", "")}
    s, body, _ = req(k, "GET", f"/api/chat/files/{fid}", None, dict(T, Range="bytes=0-47"))
    ok(f"{k}: Abruf nur mit Token (ohne Cookie, ohne X-Saga) -> 206", s == 206 and body == blob[:48])
    ok(f"{k}: Klartext ueber Token", plain_range(k, fid, None, key, len(plain), 100, 300000, T) == plain[100:300001])
    ok(f"{k}: Token fuer andere Datei -> 401", req(k, "GET", f"/api/chat/files/{files_other[k]}", None, T)[0] == 401)
    bad = tk.get("token", "")[:-2] + ("AA" if not tk.get("token", "").endswith("AA") else "BB")
    ok(f"{k}: veraendertes Token -> 401", req(k, "GET", f"/api/chat/files/{fid}", None, {"X-Saga-Media-Token": bad})[0] == 401)
    ok(f"{k}: Token fuer fremde Datei nicht ausstellbar (C) -> 404", req(k, "POST", f"/api/chat/files/{fid}/token", Cc)[0] == 404)
    ok(f"{k}: Token mit Token holen -> 401/404", req(k, "POST", f"/api/chat/files/{fid}/token", None, T)[0] in (401, 404))
    # Rechteentzug: B verlaesst den Chat -> spaetestens nach MEDIA_AUTHZ_TTL kein Abruf mehr (auch mit Token)
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid='vd-b'", (time.time(), cid))
    t0 = time.time()
    gone = wait(lambda: req(k, "GET", f"/api/chat/files/{fid}", None, dict(T, Range="bytes=0-9"))[0] == 404, TTL + 3)
    ok(f"{k}: Entzug wirkt auch mit Token nach hoechstens {TTL} s (hier {time.time() - t0:.1f} s)", gone and time.time() - t0 <= TTL + 1.5)
    with connect() as c, c:
        c.execute("UPDATE chat_member SET left_at=NULL WHERE chat_id=? AND uid='vd-b'", (cid,))
    # --- falscher Kopf beim Abschluss -----------------------------------------------------------------------------------
    junk = bytearray(C.encrypt_file_f2(os.urandom(5000))[0])
    junk[0:4] = b"XXXX"
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": len(junk), "fmt": 2})
    u2 = jd(b).get("upload", "")
    s, b, _ = put_part(k, u2, A, bytes(junk), 0, len(junk))
    ok(f"{k}: falscher Kopf -> 400, Upload verworfen", s == 400 and head_off(k, u2, A)[0] == 404
       and not os.path.exists(os.path.join(CHAT_DIR, "up", u2)) and not os.path.exists(os.path.join(CHAT_DIR, u2)))
    # --- Abbrechen (DELETE) ---------------------------------------------------------------------------------------------
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": size, "fmt": 2})
    u3 = jd(b).get("upload", "")
    put_part(k, u3, A, blob[:1000], 0, size)
    s, _, _ = req(k, "DELETE", f"/api/chat/uploads/{u3}", A)
    ok(f"{k}: DELETE bricht ab (Eintrag und Teil-Datei weg)", s == 200 and head_off(k, u3, A)[0] == 404
       and wait(lambda: not os.path.exists(os.path.join(CHAT_DIR, "up", u3)), 3))
    return fid, size


files_other = {}


def limits_part(cid, A):
    """Kontingent (laufende Uploads zaehlen), hoechstens 2 gleichzeitig, Admin-Werte, Verfall (purge)."""
    k = "P"
    with connect() as c, c:
        c.execute("DELETE FROM chat_upload WHERE uid='vd-a'")
        c.execute("DELETE FROM chat_file WHERE uid='vd-a'")
    big = FV.cipher_size(4 << 20)
    ids = []
    for i in range(2):
        s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": big, "fmt": 2})
        ids.append(jd(b).get("upload"))
        ok(f"P: Upload {i + 1} von 2 gleichzeitig", s == 200)
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": FV.cipher_size(1000), "fmt": 2})
    ok("P: dritter gleichzeitiger Upload -> 429", s == 429)
    s, b, _ = req(k, "GET", "/api/chat/me", A)
    ok("P: used zaehlt laufende Uploads", (jd(b).get("limits") or {}).get("used") == 2 * big)
    req(k, "DELETE", f"/api/chat/uploads/{ids[1]}", A)
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": big, "fmt": 2})
    ids[1] = jd(b).get("upload")
    # Kontingent 12 MB: 2 x 4 MB laufen, ein v1-Anhang von 900 KB passt, weitere 4 MB nicht
    req(k, "DELETE", f"/api/chat/uploads/{ids[1]}", A)
    with connect() as c, c:
        for i in range(4):
            c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created) VALUES (?,?,?,?,?)",
                      (secrets.token_urlsafe(18), cid, "vd-a", 2 << 20, time.time()))
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": big, "fmt": 2})
    ok("P: Kontingent (fertige Anhaenge + laufende Uploads) -> 413", s == 413 and "12 MB" in jd(b).get("error", ""))
    auth.save_setting("chat_quota_mb", 40)
    auth.save_setting("chat_video_max_mb", 2)
    time.sleep(5.5)                                   # Einstellungen sind 5 s zwischengespeichert
    s, b, _ = req(k, "GET", "/api/chat/me", A)
    lim = jd(b).get("limits") or {}
    ok("P: Admin-Werte gelten (quota 40 MB, video_max 2 MB)", lim.get("quota") == 40 << 20 and lim.get("video_max") == 2 << 20)
    ok("P: Admin-video_max -> 413", req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": big, "fmt": 2})[0] == 413)
    s, b, _ = req(k, "POST", f"/api/chats/{cid}/uploads", A, js={"size": FV.cipher_size(2 << 20), "fmt": 2})
    ok("P: ... innerhalb -> 200", s == 200)
    u = jd(b).get("upload")
    put_part(k, u, A, b"\0" * 1000, 0, FV.cipher_size(2 << 20))
    # Verfall: Upload abgelaufen -> purge entfernt Eintrag und Teil-Datei; Teil-Datei ohne Eintrag (> 1 h) auch
    with connect() as c, c:
        c.execute("UPDATE chat_upload SET expires=? WHERE id=?", (time.time() - 1, u))
    orphan = os.path.join(CHAT_DIR, "up", secrets.token_urlsafe(18))
    open(orphan, "wb").write(b"x")
    os.utime(orphan, (time.time() - 7200, time.time() - 7200))
    from saga import chat as CHM
    CHM.purge()
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_upload WHERE id=?", (u,)).fetchone()[0]
    ok("P: purge: verfallener Upload samt Teil-Datei weg, verwaiste Teil-Datei weg", n == 0 and not os.path.exists(os.path.join(CHAT_DIR, "up", u))
       and not os.path.exists(orphan))
    ok("P: verfallener Upload -> HEAD 404", head_off(k, u, A)[0] == 404)
    for k2 in ("chat_quota_mb", "chat_video_max_mb"):
        auth.save_setting(k2, 0)


def main():
    A, B, Cc = ck("vd-a"), ck("vd-b"), ck("vd-c")
    cid = setup()
    # je Weg eine weitere Datei (fuer "Token fuer andere Datei")
    for k in PORTS:
        fid = secrets.token_urlsafe(18)
        os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
        open(os.path.join(CHAT_DIR, fid), "wb").write(b"other")
        with connect() as c, c:
            c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created) VALUES (?,?,?,?,?)", (fid, cid, "vd-a", 5, time.time()))
        attach(cid, fid)
        files_other[k] = fid
    binp = media_bin()
    if binp:
        start("M", [binp, "--", sys.executable, "run.py"],
              env_of("M", MEDIA_INTERNAL_PORT=str(INTERNAL), MEDIA_AUTHZ_TTL=str(TTL), MEDIA_STALL_TIMEOUT="5"))
    start("P", [sys.executable, "run.py"], env_of("P"))
    for k in list(procs):
        up = wait(lambda: healthy(k), 60)
        ok(f"{k}: gestartet", up)
        if not up:
            print(log_of(k)[-3000:], flush=True)
    path_upload("P", cid, A, B, Cc)
    if binp:
        fid, size = path_upload("M", cid, A, B, Cc)
        L = log_of("M")
        ok("M: Uploads und Abrufe liefen ueber saga-media (Zugriffszeilen)", "PUT /api/chat/uploads/" in L and "[saga-media" in L
           and f"GET /api/chat/files/{fid} HTTP/1.1\" 206" in L)
        n_py = sum(1 for x in L.splitlines() if "PUT /api/chat/uploads/" in x and "[saga-media" not in x and '" 200' in x)
        ok("M: keine erfolgreichen Upload-Teile durch Python", n_py == 0)
    else:
        print("HINWEIS: saga-media fehlt - Teil M uebersprungen", flush=True)
    limits_part(cid, A)
    # Kontoloeschung: laufende Uploads des Kontos weg
    s, b, _ = req("P", "POST", f"/api/chats/{cid}/uploads", ck("vd-b"), js={"size": FV.cipher_size(1000), "fmt": 2})
    ub = jd(b).get("upload", "")
    put_part("P", ub, ck("vd-b"), b"\0" * 10, 0, FV.cipher_size(1000))
    from saga.userdata import delete_user_data
    delete_user_data("vd-b", drop_user=True)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_upload WHERE uid='vd-b'").fetchone()[0]
    ok("Kontoloeschung: Uploads und Teil-Dateien weg", s == 200 and n == 0 and wait(lambda: not os.path.exists(os.path.join(CHAT_DIR, "up", ub)), 3))
    stop_all()
    errs = sum(log_of(k).count("Traceback") for k in procs)
    ok(f"Serverfehler in den Instanzen: {errs}", errs == 0)
    if errs:
        for k in procs:
            print("\n".join(x for x in log_of(k).splitlines() if "Error" in x or "Traceback" in x)[-3000:])


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
            if os.environ.get("VID_KEEP") != "1":
                shutil.rmtree(STATE, ignore_errors=True)
    print(f"\nVideos {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
