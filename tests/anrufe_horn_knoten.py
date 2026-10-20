"""Gjallarhorn P3 (Saga-Steuerung, Konzept v2 §2.1-2.4, §2.6, §2.10): eingebauter Knoten unter saga-media, Medien-Token,
Kandidaten im Beitritt, topo=horn, Steuerkanal - Ende-zu-Ende mit echtem saga-media und echtem gjallarhorn.

Startet eine eigene Instanz auf derselben Datenbank: saga-media auf 8061 (uvicorn intern 8062) mit dem eingebauten Knoten
auf 8463 (HORN_TLS_SELF, nur TCP, Mitschnitt HORN_CAPTURE). saga-media/gjallarhorn: SAGA_MEDIA_BIN/GJALLARHORN_BIN, sonst
"go build" aus media/ bzw. gjallarhorn/ (fehlt Go: Test uebersprungen = FEHLER, er gehoert zur Reihe).
Geprueft: ohne Flag kein Knoten-Prozess; Flag an -> saga-media startet ihn, Steuerkanal verbunden, Register (eingebaut,
Adresse, bereit), /internal/horn/conf und /api/horn/ctl von aussen gesperrt; config.limits.horn (12/50, 25/100);
Gruppenanruf in einer Gruppe ueber der Mesh-Grenze -> topo horn mit Kandidat und Medien-Token (Ton/Bild, ohne UDP kein
"both"), Token ohne Konto-/Geraetenamen, kurze Lebensdauer; Einladung ohne horn-Geraet 'old'; Beitritt ohne Mesh-Angebote
(mit Angeboten 400), ohne horn 409 old; Teilnehmerliste mit hp; kleine Gruppe bleibt Mesh. Token am Knoten: gueltig,
Wiederverwendung (TURN und Bein), abgelaufen, anderer Knoten, veraenderte Nutzlast (anderer Anruf), fremder Schluessel,
entfernte Person (kick nach Verlassen), beendeter Anruf (close); POST /horn (frische Token, falscher Knoten, fremdes
Geraet); Grenze vom Admin gesenkt; Flag aus -> Knoten beendet, neue Gruppen wieder Mesh; Neustart mit Flag: Startzeit.
Danach der Browser-Test tests/browser_horn_knoten.js (5 Personen ueber den Knoten, Frame-E2E) und die Pruefung des
Mitschnitts am Knoten: jeder Rahmen traegt den E2E-Trailer, ffmpeg kann das Video nicht dekodieren.
Messwerte (PERF): Startzeiten, Beitritt, Token, CPU/RSS von saga-media, Python und Knoten. Wegwerf-Konten ca-k*."""
import base64
import glob
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import statistics
import struct
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT, INTERNAL, HORN_PORT = 8061, 8062, 8463
os.environ["HK_TEST_BASE"] = f"http://127.0.0.1:{PORT}"          # alle Anfragen der Helfer an die eigene Instanz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tools"))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

req, res = A_.req, A_.res


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:1500]), flush=True)
SECRET = os.environ["SESSION_SECRET"]
STATE = tempfile.mkdtemp(prefix="horntest-")
CAP = os.path.join(STATE, "capture")
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
CALLS = {"enabled": True, "stun": "", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "", "turn_password": "",
         "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
PW = "Horn-Knoten-Pass-1234"
USERS = [("ca-k1", "Anna Knoten"), ("ca-k2", "Bert Knoten"), ("ca-k3", "Cora Knoten"), ("ca-k4", "Dora Knoten"), ("ca-k5", "Emil Knoten"),
         ("ca-k6", "Fritz Alt")]
BUSERS = [("ca-kb1", "Ada Browser"), ("ca-kb2", "Ben Browser"), ("ca-kb3", "Cleo Browser"), ("ca-kb4", "Dirk Browser"), ("ca-kb5", "Eva Browser")]
PERF = {}
P = {"proc": None, "log": None}


# --- Instanz ---------------------------------------------------------------------------------------------------------
def build(name, d, env_bin):
    b = os.environ.get(env_bin)
    if b:
        return b
    if not shutil.which("go"):
        return None
    out = os.path.join(STATE, name)
    r = subprocess.run(["go", "build", "-trimpath", "-o", out, "."], cwd=os.path.join(APP, d), capture_output=True, text=True,
                       env=dict(os.environ, CGO_ENABLED="0"))
    if r.returncode:
        print(r.stderr[-3000:], flush=True)
        return None
    return out


def start(media, node):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL") and not k.startswith("HORN_")}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT="0", HOST="127.0.0.1", STATE_DIR=STATE,
               BASE_URL=f"http://127.0.0.1:{PORT}", CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0",
               PYTHONPATH=APP, SECRET_KEY=SECRET, SESSION_SECRET=SECRET, GJALLARHORN_BIN=node, HORN_PORT=str(HORN_PORT),
               HORN_NAME="localhost", HORN_TLS_SELF="1", HORN_UDP="0", HORN_ICE_IPS="127.0.0.1", HORN_CAPTURE=CAP, HORN_MAX_LEGS="48")
    P["log"] = open(os.path.join(STATE, "server.log"), "a")
    P["proc"] = subprocess.Popen([media, "--", sys.executable, "run.py"], env=env, cwd=APP, stdout=P["log"], stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 40:
        try:
            if req("GET", "/healthz")[0] == 200:
                return time.time() - t0
        except OSError:
            pass
        time.sleep(0.1)
    return None


def stop():
    p = P["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
    if P["log"]:
        P["log"].close()


def log_text():
    try:
        return open(os.path.join(STATE, "server.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def pids():
    """-> {media, python, node} aus /proc (Kinder von saga-media)."""
    out = {"media": P["proc"].pid}
    for d in glob.glob("/proc/[0-9]*"):
        try:
            st = open(d + "/stat").read()
            ppid = int(st.rsplit(") ", 1)[1].split()[1])
            cmd = open(d + "/cmdline", "rb").read().split(b"\0")
        except (OSError, ValueError, IndexError):
            continue
        if ppid != P["proc"].pid:
            continue
        if b"run.py" in cmd:
            out["python"] = int(d[6:])
        elif cmd and cmd[0].endswith(b"gjallarhorn"):
            out["node"] = int(d[6:])
    return out


def cpu_ticks(pid):
    try:
        f = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()
        return int(f[11]) + int(f[12])
    except (OSError, ValueError, IndexError):
        return None


def rss_mb(pid):
    try:
        for ln in open(f"/proc/{pid}/status"):
            if ln.startswith("VmRSS:"):
                return round(int(ln.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def cpu_pct(ps, secs):
    a = {k: cpu_ticks(v) for k, v in ps.items()}
    time.sleep(secs)
    b = {k: cpu_ticks(v) for k, v in ps.items()}
    hz = os.sysconf("SC_CLK_TCK")
    return {k: round((b[k] - a[k]) / hz / secs * 100, 2) for k in ps if a.get(k) is not None and b.get(k) is not None}


def port_open(p):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def node_row():
    with connect() as c:
        r = c.execute("SELECT id, name, url, builtin, state, seen, udp FROM horn_node WHERE builtin=1 AND removed IS NULL").fetchone()
    return dict(zip(("id", "name", "url", "builtin", "state", "seen", "udp"), r)) if r else None


def wait(fn, t, step=0.2):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def node_ready():
    n = node_row()
    return n if n and n["state"] == "ok" and n["seen"] and time.time() - n["seen"] < HN.READY_S and port_open(HORN_PORT) else None


def flag(on, **extra):
    auth.save_setting("calls", dict(CALLS, horn=on, **extra))
    auth._settings_cache["v"] = None


# --- Knoten direkt (HTTPS, selbst signiert) ---------------------------------------------------------------------------
def node_req(m, p, body=None):
    c = http.client.HTTPSConnection("127.0.0.1", HORN_PORT, timeout=15, context=ssl._create_unverified_context())
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers={"Content-Type": "application/json", "Host": "localhost"})
    r = c.getresponse()
    out = r.read()
    c.close()
    try:
        return r.status, json.loads(out)
    except ValueError:
        return r.status, out


def turn(tok):
    return node_req("POST", "/api/sfu/turn", {"token": tok})


def payload(tok):
    p = tok.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def ws_status(path, key=None):
    """Statuszeile eines WebSocket-Handshakes ueber saga-media (101 = angenommen)."""
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5)
    h = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
         f"Sec-WebSocket-Key: {base64.b64encode(secrets.token_bytes(16)).decode()}\r\nX-Saga: 1\r\n" + (f"X-Saga-Horn-Key: {key}\r\n" if key else "") + "\r\n")
    s.sendall(h.encode())
    try:
        line = s.recv(200).split(b"\r\n")[0].decode()
    except OSError:
        line = ""
    s.close()
    m = re.match(r"HTTP/1\.1 (\d+)", line)
    return int(m.group(1)) if m else 0


# --- Mitschnitt am Knoten: nur verschluesselte Rahmen ------------------------------------------------------------------
def rtp_packets(path):
    b = open(path, "rb").read()
    i, out = 0, []
    while i + 2 <= len(b):
        n = struct.unpack(">H", b[i:i + 2])[0]
        out.append(b[i + 2:i + 2 + n])
        i += 2 + n
    return out


def rtp_payload(p):
    if len(p) < 12:
        return None, None, None
    cc, x, m = p[0] & 0x0F, (p[0] >> 4) & 1, p[1] >> 7
    off = 12 + 4 * cc
    if x:
        off += 4 + 4 * struct.unpack(">H", p[off + 2:off + 4])[0]
    pad = p[-1] if p[0] & 0x20 else 0
    return p[off:len(p) - pad], m, struct.unpack(">I", p[4:8])[0]


def vp8_frames(pkts):
    """VP8-Rahmen aus RTP (RFC 7741): Descriptor ab, Teile bis zum Marker zusammensetzen."""
    frames, cur = [], b""
    for p in pkts:
        pl, m, _ = rtp_payload(p)
        if not pl:
            continue
        o = 1
        if pl[0] & 0x80:
            xb = pl[1]
            o = 2
            if xb & 0x80:
                o += 2 if pl[o] & 0x80 else 1
            if xb & 0x40:
                o += 1
            if xb & 0x30:
                o += 1
        if pl[0] & 0x10 and (pl[0] & 0x07) == 0:
            cur = b""
        cur += pl[o:]
        if m:
            frames.append(cur)
            cur = b""
    return frames


def capture_check():
    vids = glob.glob(os.path.join(CAP, "video-*.rtp"))
    auds = glob.glob(os.path.join(CAP, "audio-*.rtp"))
    vf = [f for p in vids for f in vp8_frames(rtp_packets(p))]
    af = [rtp_payload(p)[0] for a in auds for p in rtp_packets(a)]
    af = [x for x in af if x]
    trail = lambda fr: len(fr) > 30 and fr[-2] == 0x0C and fr[-1] < 16  # noqa: E731
    ok(f"Mitschnitt am Knoten: {len(vids)} Bild- und {len(auds)} Tonspuren, {len(vf)} Video- und {len(af)} Tonrahmen", len(vf) > 50 and len(af) > 50)
    ok("jeder Videorahmen traegt den E2E-Trailer (IV, 0x0C, kid) - nichts im Klartext durchgereicht",
       vf and sum(map(trail, vf)) == len(vf), sum(map(trail, vf)))
    ok("jeder Tonrahmen traegt den E2E-Trailer", af and sum(map(trail, af)) == len(af), sum(map(trail, af)))
    # Entropie der verschluesselten Teile (nach dem Kopf): nahezu 8 Bit je Byte
    body = b"".join(f[10:-30] for f in vf if len(f) > 200)[:400000]
    ent = 0.0
    if body:
        import math
        cnt = [0] * 256
        for x in body:
            cnt[x] += 1
        ent = -sum(c / len(body) * math.log2(c / len(body)) for c in cnt if c)
    ok(f"Inhalt der Videorahmen sieht zufaellig aus ({ent:.3f} Bit/Byte)", ent > 7.9)
    if not shutil.which("ffmpeg") or not vids:
        ok("ffmpeg vorhanden (Dekodierprobe)", False)
        return
    best = max(vids, key=lambda p: len(vp8_frames(rtp_packets(p))))
    fr = vp8_frames(rtp_packets(best))
    keyf = [f for f in fr if f and not (f[0] & 1)]
    w = h = 0
    if keyf and keyf[0][3:6] == b"\x9d\x01\x2a":
        w, h = struct.unpack("<H", keyf[0][6:8])[0] & 0x3FFF, struct.unpack("<H", keyf[0][8:10])[0] & 0x3FFF
    ivf = os.path.join(STATE, "mitschnitt.ivf")
    w, h = w or 160, h or 90
    with open(ivf, "wb") as f:
        f.write(b"DKIF" + struct.pack("<HHIHHIII", 0, 32, 0x30385056, w, h, 30, 1, len(fr)) + b"\0" * 4)
        for i, x in enumerate(fr):
            f.write(struct.pack("<IQ", len(x), i) + x)
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", ivf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-"], capture_output=True, timeout=120)
    n = len(r.stdout) // (w * h * 3)
    errs = len([x for x in r.stderr.decode(errors="replace").splitlines() if x.strip()])
    # Gegenprobe ueber den Inhalt: die Quelle ist eine einfarbige Flaeche (eine der Personenfarben, tests/browser_horn_knoten.js);
    # entschluesselt zeigte das Probenpixel in praktisch jedem Rahmen dieselbe davon, aus Chiffrat dekodiert hoechstens zufaellig
    per = [0] * 5
    for k in range(n):
        o = k * w * h * 3 + (int(h * 0.83) * w + int(w * 0.875)) * 3
        rr, gg, bb = r.stdout[o:o + 3]
        mx, mn = max(rr, gg, bb), min(rr, gg, bb)
        if mx - mn < 60:
            continue
        hue = ((gg - bb) / (mx - mn)) % 6 if mx == rr else (bb - rr) / (mx - mn) + 2 if mx == gg else (rr - gg) / (mx - mn) + 4
        hue = (hue * 60 + 360) % 360
        for q, x in enumerate((0, 72, 144, 216, 288)):
            per[q] += min(abs(hue - x), 360 - abs(hue - x)) < 12
    hits = max(per)
    PERF["mitschnitt_ffmpeg"] = {"rahmen": len(fr), "schluesselbilder": len(keyf), "ausgegeben": n, "fehlerzeilen": errs, "w": w, "h": h,
                                 "personenfarbe": hits}
    ok(f"Knoten kann den Inhalt nicht dekodieren: ffmpeg meldet {errs} Fehler, Personenfarbe in {hits} von {n} Bildern",
       len(fr) > 20 and errs > 0 and hits <= max(3, n * 0.3), PERF["mitschnitt_ffmpeg"])


# --- Ablauf -----------------------------------------------------------------------------------------------------------
def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    flag(False)
    for u, n in USERS + BUSERS:
        auth.create_user(u, PW, n)
    media, node = build("saga-media", "media", "SAGA_MEDIA_BIN"), build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok(f"saga-media und gjallarhorn gebaut ({bool(media)}, {bool(node)})", media and node)
    try:
        if media and node:
            run(media, node)
    finally:
        for x in A_.STREAMS:
            x.close()
        stop()
        errs = log_text().count("Traceback")
        ok(f"Instanz: Serverfehler {errs}", errs == 0)
        if errs:
            print(log_text()[-6000:])
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE builtin=1 AND name='localhost'")
        shutil.rmtree(STATE, ignore_errors=True)
    print("PERF " + json.dumps(PERF))
    print(f"\nANRUFE-HORN-KNOTEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run(media, node):
    t = start(media, node)
    ok(f"Instanz (saga-media + Saga) laeuft ({t and round(t, 2)} s)", t is not None)
    time.sleep(3)
    ps = pids()
    ok(f"ohne Flag: kein Knoten-Prozess, Port {HORN_PORT} zu", "node" not in ps and not port_open(HORN_PORT), ps)
    s, _ = req("GET", "/internal/horn/conf")
    ok(f"/internal/horn/conf von aussen gesperrt ({s})", s == 404)

    t0 = time.time()
    flag(True)
    n = wait(node_ready, 20)
    PERF["flag_an_bis_knoten_bereit_s"] = round(time.time() - t0, 2)
    ok(f"Flag an: saga-media startet den Knoten, Steuerkanal steht ({PERF['flag_an_bis_knoten_bereit_s']} s)", n is not None)
    if not n:
        print(log_text()[-4000:])
        return
    ok(f"Register: eingebauter Knoten {n['name']} ({n['url']}), ohne UDP", n["builtin"] and n["url"] == f"https://localhost:{HORN_PORT}" and not n["udp"], n)
    ps = pids()
    ok(f"Knoten ist Kindprozess von saga-media ({ps})", "node" in ps and "python" in ps)
    ok(f"/api/horn/ctl von aussen ohne Schluessel abgewiesen ({ws_status('/api/horn/ctl')})", ws_status("/api/horn/ctl") not in (101, 0))
    ok("... mit falschem Schluessel ebenso", ws_status("/api/horn/ctl", "f" * 64) not in (101, 0))
    s, h = node_req("GET", "/api/session")
    ok(f"Knoten antwortet auf HTTPS (mode node, {s})", s == 200 and h.get("mode") == "node" and h.get("dev") is False)
    PERF["leerlauf_cpu_pct"] = cpu_pct(ps, 5)
    PERF["rss_mb"] = {k: rss_mb(v) for k, v in ps.items()}

    people = {u: A_.Person(u) for u, _ in USERS}
    for u, p in people.items():
        s, _ = req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS24 if u == "ca-k6" else CAPS})
    A, B, Cc, D, Em, F = (people[u] for u, _ in USERS)
    s, cf = req("GET", "/api/calls/config", A.k)
    hl = (cf.get("limits") or {}).get("horn") or {}
    ok(f"config: features horn, limits.horn {hl}, Mesh-Grenzen unveraendert",
       "horn" in cf.get("features", []) and hl == {"video_tcp": 12, "audio_tcp": 50, "video_udp": 25, "audio_udp": 100}
       and cf["limits"]["endpoints_video"] == 4)

    # --- Gruppenanruf ueber den Knoten -----------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:]]})
    gid, cid = d["chat"]["id"], secrets.token_hex(16)
    t0 = time.time()
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": gid, "media": "video", "device": A.dev["id"], "caps": CAPS})
    PERF["start_ms"] = round((time.time() - t0) * 1000, 1)
    k = d.get("call") or {}
    cands = (k.get("horn") or {}).get("candidates") or []
    ok(f"Gruppe mit 6 Mitgliedern (Mesh-Grenze 4): topo horn, Grenze 12 Video ({s}, {k.get('topo')}, {k.get('limit')})",
       s == 200 and k.get("topo") == "horn" and k.get("limit") == 12)
    ok(f"Antwort mit einem Kandidaten (eingebauter Knoten) und Token fuer Ton- und Bild-Leitung ({[c['node'] for c in cands]})",
       len(cands) == 1 and cands[0]["node"] == "localhost" and cands[0]["url"] == n["url"] and set(cands[0]["tokens"]) == {"audio", "video"}
       and k["horn"]["e2e"] == "saga-horn/v1")
    tok = cands[0]["tokens"]
    pa, pv = payload(tok["audio"]), payload(tok["video"])
    ok("Token: Rollen/Rechte, Knoten, gleicher Anruf und Teilnehmer, eigenes Bein je Rolle, ohne UDP",
       pa["role"] == "audio" and pa["pub"] == ["a"] and pv["role"] == "video" and pv["pub"] == ["v", "s"] and pa["node"] == "localhost"
       and pa["call_ref"] == pv["call_ref"] and pa["peer_ref"] == pv["peer_ref"] == cands[0]["peer"] and pa["leg_ref"] != pv["leg_ref"]
       and not pa["udp_ok"] and pa["dev_class"] == "desktop" and pa["max_kbit"] == 4000, pa)
    ok(f"Token-Lebensdauer 60 s ({pa['exp'] - int(time.time())} s)", 55 <= pa["exp"] - time.time() <= 61)
    raw = json.dumps([pa, pv])
    ok("Token ohne Konto-, Geraete- oder Chatnamen und ohne Anruf-ID", not any(x in raw for x in (A.uid, A.dev["id"], cid, "Anna")), raw)
    with connect() as c:
        inv = dict(c.execute("SELECT who, state FROM chat_call_invite WHERE call_id=?", (cid,)).fetchall())
    ok(f"Einladungen: wer kein horn-Geraet hat, bekommt 'old' ({inv})", inv.get("ca-k6") == "old" and inv.get("ca-k2") == "ringing")

    offer = {A.dev["id"]: "x" * 80}
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS, "signals": {
        A.dev["id"]: base64.b64encode(secrets.token_bytes(64)).decode()}})
    ok(f"Beitritt mit Mesh-Angeboten -> 400 ({s})", s == 400)
    del offer
    lat = []
    for p in (B, Cc, D):
        t0 = time.time()
        s, d = req("POST", f"/api/calls/{cid}/join", p.k, {"device": p.dev["id"], "role": "member", "caps": CAPS})
        lat.append((time.time() - t0) * 1000)
        ok(f"{p.uid}: Beitritt ohne Angebote ({s}, Kandidaten {len(((d.get('call') or {}).get('horn') or {}).get('candidates') or [])})",
           s == 200 and d["call"]["topo"] == "horn" and len(d["call"]["horn"]["candidates"]) == 1)
    PERF["beitritt_ms"] = {"median": round(statistics.median(lat), 1), "max": round(max(lat), 1)}
    legs = d["call"]["legs"]
    hps = {x["device"]: x.get("hp") for x in legs}
    ok("Teilnehmerliste: jedes Geraet mit eigener Knoten-Kennung hp (= peer_ref im Token), ohne Geraete-ID",
       len(hps) == 4 and all(hps.values()) and len(set(hps.values())) == 4 and hps[A.dev["id"]] == pa["peer_ref"]
       and not any(dv in v for dv in hps for v in hps.values()), hps)
    s, d = req("POST", f"/api/calls/{cid}/join", F.k, {"device": F.dev["id"], "role": "member", "caps": CAPS24})
    ok(f"Geraet ohne horn (Saga 2.4) -> 409 old ({s}, {d.get('join_block') if isinstance(d, dict) else d})", s == 409 and d.get("join_block") == "old")
    s, d = req("GET", f"/api/calls/{cid}", F.k)
    ok("... und sieht den Anruf als nicht beitretbar (old)", s == 200 and not d["call"]["joinable"] and d["call"]["join_block"] == "old")

    # --- Token am Knoten --------------------------------------------------------------------------------------------
    s, d = turn(tok["audio"])
    ok(f"gueltiges Token -> TURN-Zugang ({s})", s == 200 and d.get("username"))
    s, d = turn(tok["audio"])
    ok(f"Wiederverwendung desselben Tokens -> 401 ({s}, {d})", s == 401)
    s, d = node_req("POST", "/api/sfu/leg", {"token": tok["video"], "path": "tcp", "offer": "v=0\r\n", "speaker": True, "tiles": 3})
    ok(f"Bein mit gueltigem Token: Token angenommen, nur das Angebot ungueltig ({s}, {d.get('error') if isinstance(d, dict) else d})",
       s == 400 and "angebot" in str(d))
    s, d = node_req("POST", "/api/sfu/leg", {"token": tok["video"], "path": "tcp", "offer": "v=0\r\n", "speaker": True, "tiles": 3})
    ok(f"... dasselbe Token ein zweites Mal -> 401 ({s}, {d})", s == 401 and "benutzt" in str(d))
    with connect() as c:
        hn = HN.node_by_id(c, n["id"])
    t0 = time.perf_counter()
    for _ in range(200):
        HN.token(hn, cid, A.dev["id"], "audio")
    PERF["token_us"] = round((time.perf_counter() - t0) / 200 * 1e6, 1)
    s, d = turn(HN.token(hn, cid, A.dev["id"], "audio", now=time.time() - 61))
    ok(f"abgelaufenes Token -> 401 ({s}, {d})", s == 401 and "abgelaufen" in str(d))
    s, d = turn(HN.token(dict(hn, name="horn-9.example.org"), cid, A.dev["id"], "audio"))
    ok(f"Token fuer einen anderen Knoten -> 401 ({s}, {d})", s == 401 and "anderen knoten" in str(d))
    good = HN.token(hn, cid, A.dev["id"], "audio")
    other = HN.token(hn, secrets.token_hex(16), A.dev["id"], "audio")
    forged = "h1." + other.split(".")[1] + "." + good.split(".")[2]
    s, d = turn(forged)
    ok(f"Nutzlast eines anderen Anrufs mit dieser Signatur -> 401 ({s})", s == 401)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fk = Ed25519PrivateKey.generate()
    p_ = good.split(".")[1]
    s, d = turn("h1." + p_ + "." + HN._b64u(fk.sign(("saga-horn-token/1." + p_).encode())))
    ok(f"mit fremdem Schluessel signiert -> 401 ({s})", s == 401)
    s, d = turn("h1." + p_ + "." + good.split(".")[2])
    ok(f"Kontrolle: das unveraenderte Token geht ({s})", s == 200)
    ok("Kennungen je Anruf verschieden (Token gilt nur fuer seinen Anruf)", payload(other)["call_ref"] != pa["call_ref"]
       and payload(other)["peer_ref"] != pa["peer_ref"])

    # --- frische Token, Wahl melden ---------------------------------------------------------------------------------
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"], "node": "localhost"})
    bt = ((d.get("horn") or {}).get("candidates") or [{}])[0].get("tokens") if s == 200 else None
    ok(f"POST /horn: frische Token fuer das eigene Geraet, Wahl gemerkt ({s})", s == 200 and bt and payload(bt["audio"])["jti"] != pa["jti"])
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": B.dev["id"], "node": "horn-9.example.org"})
    ok(f"POST /horn mit unbekanntem Knoten -> 409 ({s})", s == 409)
    s, d = req("POST", f"/api/calls/{cid}/horn", B.k, {"device": A.dev["id"]})
    ok(f"POST /horn fuer ein fremdes Geraet -> 409 ({s})", s == 409)
    s, d = req("POST", f"/api/calls/{cid}/horn", F.k, {"device": F.dev["id"]})
    ok(f"POST /horn ohne Endpunkt im Anruf -> 409 ({s})", s == 409)

    # --- Verlassen: kick ---------------------------------------------------------------------------------------------
    s, _ = req("POST", f"/api/calls/{cid}/leave", B.k, {"device": B.dev["id"]})
    time.sleep(0.6)
    s2, d2 = turn(bt["audio"])
    ok(f"Bert verlaesst den Anruf -> sein noch gueltiges Token gilt am Knoten nicht mehr ({s}, {s2}, {d2})", s == 200 and s2 == 401 and "entfernt" in str(d2))

    # --- Grenze vom Admin gesenkt -----------------------------------------------------------------------------------
    flag(True, horn_video=4)
    time.sleep(A_.WAIT)
    s, d = req("POST", f"/api/calls/{cid}/join", Em.k, {"device": Em.dev["id"], "role": "member", "caps": CAPS})
    ok(f"Admin senkt die Grenze (4 Video): fuenftes Geraet bei 3 + Emil passt, danach voll ({s})", s == 200)
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    ok(f"... Bert kommt zurueck -> voll ({s}, {d.get('join_block') if isinstance(d, dict) else ''})", s == 409 and d.get("join_block") == "full")
    flag(True)

    # --- Ende: close ---------------------------------------------------------------------------------------------------
    for p in (A, Cc, D, Em):
        req("POST", f"/api/calls/{cid}/end", p.k, {"device": p.dev["id"]})
    s, d = req("GET", f"/api/calls/{cid}", A.k)
    ok(f"Anruf beendet ({d['call']['state'] if s == 200 else s})", s == 200 and d["call"]["state"] == "ended")
    time.sleep(0.6)
    s, d = turn(HN.token(hn, cid, D.dev["id"], "audio"))
    ok(f"Raum am Knoten geschlossen: neues Token fuer den beendeten Anruf -> abgewiesen ({s}, {d})", s in (401, 404))

    # --- kleine Gruppe bleibt Mesh ------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": ["ca-k2", "ca-k3"]})
    cid2 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid2, "chat_id": d["chat"]["id"], "media": "video", "device": A.dev["id"], "caps": CAPS})
    ok(f"Gruppe mit 3 Mitgliedern: Mesh wie 2.4 ({d.get('call', {}).get('topo')}, keine Kandidaten)", s == 200 and d["call"]["topo"] == "mesh"
       and "horn" not in d["call"])
    req("POST", f"/api/calls/{cid2}/end", A.k, {"device": A.dev["id"]})

    # --- Browser: 5 Personen ueber den Knoten -------------------------------------------------------------------------
    browser(people, gid)

    # --- Flag aus: Knoten weg -----------------------------------------------------------------------------------------
    t0 = time.time()
    flag(False)
    gone = wait(lambda: not port_open(HORN_PORT) and "node" not in pids(), 20)
    PERF["flag_aus_bis_knoten_weg_s"] = round(time.time() - t0, 2)
    ok(f"Flag aus: saga-media beendet den Knoten ({PERF['flag_aus_bis_knoten_weg_s']} s)", gone)
    time.sleep(1)
    s, d = req("GET", f"/api/chats/{gid}", A.k)
    cid3 = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid3, "chat_id": gid, "media": "audio", "device": A.dev["id"], "caps": CAPS})
    ok(f"ohne Flag: dieselbe grosse Gruppe startet Mesh ({d.get('call', {}).get('topo')})", s == 200 and d["call"]["topo"] == "mesh")
    req("POST", f"/api/calls/{cid3}/end", A.k, {"device": A.dev["id"]})

    # --- Neustart mit Flag: Startzeit --------------------------------------------------------------------------------
    flag(True)
    stop()
    t0 = time.time()
    t = start(media, node)
    PERF["start_saga_s"] = t and round(t, 2)
    n2 = wait(node_ready_fresh(time.time()), 20, 0.05)
    PERF["start_knoten_bereit_s"] = round(time.time() - t0, 2)
    ok(f"Neustart: Saga {PERF['start_saga_s']} s, Knoten bereit nach {PERF['start_knoten_bereit_s']} s (gleicher Eintrag)",
       t is not None and n2 and n2["id"] == n["id"])


def node_ready_fresh(after):
    def f():
        x = node_ready()
        return x if x and x["seen"] >= after else None
    return f


def browser(people, gid):
    js = os.path.join(APP, "tests", "browser_horn_knoten.js")
    pw = os.environ.get("PLAYWRIGHT") or "/opt/npm-tools/node_modules/playwright"
    if os.environ.get("HORN_BROWSER") == "0":
        print("SKIP Browser-Test (HORN_BROWSER=0)")
        return
    if not shutil.which("node") or not os.path.exists(pw):
        ok("Browser-Test: node und Playwright vorhanden", False)
        return
    shutil.rmtree(CAP, ignore_errors=True)
    os.makedirs(CAP, exist_ok=True)
    env = dict(os.environ, SAGA_BASE=f"http://127.0.0.1:{PORT}", SAGA_USERS=",".join(u for u, _ in BUSERS), SAGA_PASS=PW, SAGA_GROUP=str(gid),
               PLAYWRIGHT=pw, PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"), PW_CHANNEL="chromium")
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    ps = pids()
    hz = os.sysconf("SC_CLK_TCK")
    samples, stop_ = [], []

    def sampler():
        while not stop_:
            samples.append((time.time(), {k: cpu_ticks(v) for k, v in ps.items()}, {k: rss_mb(v) for k, v in ps.items()}))
            time.sleep(0.5)
    import threading
    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    a = {k: cpu_ticks(v) for k, v in ps.items()}
    t0 = time.time()
    r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=900)
    secs = time.time() - t0
    b = {k: cpu_ticks(v) for k, v in ps.items()}
    stop_.append(1)
    th.join(2)
    m = re.search(r"^STEADY ([0-9.]+) ([0-9.]+)$", r.stdout, re.M)
    if m:
        w0, w1 = float(m.group(1)), float(m.group(2))
        win = [x for x in samples if w0 <= x[0] <= w1]
        if len(win) >= 2:
            (ta, ca, _), (tb, cb, _) = win[0], win[-1]
            PERF["anruf_5_cpu_pct"] = {k: round((cb[k] - ca[k]) / hz / (tb - ta) * 100, 1) for k in ps if ca.get(k) is not None and cb.get(k) is not None}
            PERF["anruf_5_rss_mb"] = {k: max((x[2].get(k) or 0) for x in win) for k in ps}
    print(r.stdout[-12000:], flush=True)
    if r.returncode:
        print(r.stderr[-4000:], flush=True)
    for ln in r.stdout.splitlines():
        if ln.startswith("OK  ") or ln.startswith("FAIL"):
            res.append(ln.startswith("OK"))
        if ln.startswith("PERF "):
            try:
                PERF["browser"] = json.loads(ln[5:])
            except ValueError:
                pass
        m = re.match(r"^CPU (\{.*\})$", ln)
        if m:
            PERF["browser_cpu_fenster"] = json.loads(m.group(1))
    ok(f"Browser-Test lief durch (rc {r.returncode})", r.returncode == 0)
    PERF["browser_lauf_cpu_pct"] = {k: round((b[k] - a[k]) / hz / secs * 100, 1) for k in ps if a.get(k) is not None and b.get(k) is not None}
    capture_check()


if __name__ == "__main__":
    main()
