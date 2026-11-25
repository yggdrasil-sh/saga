"""Messwerkzeug 2263-2 "Anrufe mit 10+ Teilnehmern" (Gjallarhorn): Last ueber die ECHTE Saga - Konten, Geraete, Gruppe,
Anruf, Beitritt, versiegelte fkey/fkey-ack/state, /horn und /alive wie die Clients; Medien mit `gjallarhorn horn-bot`
(Wege ws/tcp/udp, Frame-E2E saga-horn/v1, Sprechmuster) und optional 1-6 echten Chromium (tests/browser_horn_gruppe.js).
Messplan: docs/2263-plan §4 (Szenarien M1-M8); die Messreihen selbst sind 2263-9.

Aufbau (Rig, wie tests/b2_frames.py): nginx (NPM-Nachbau, "Websockets Support", TLS) -> saga-media -> Saga (Python) und
eingebauter Knoten im Tunnel-Betrieb; --horn1: zusaetzlich ein gekoppelter Knoten horn-1 (TCP+UDP, eigener Prozess, wie
tests/horn_flattern_2202.py, hier auf 127.0.0.1). Eigene DB und Ports (REGELN), Konten hl63-*; raeumt auf.

Bedienung (root auf saga-test, /srv/saga-test/env.sh gesourct, im Repo-Wurzelordner):
  export DATABASE_URL="postgresql://saga@/saga_<rolle>?host=/tmp&port=55432"
  python tools/perf/horn_last.py --szenario M2 --n 12 --video 12 --secs 60 --out /srv/saga-test/tmp/<rolle>/m2.json
  python tools/perf/horn_last.py --szenario M1 --n 6           # nur gjallarhorn bench --sfu (ohne Saga)
  python tools/perf/horn_last.py --n 3 --browsers 1 --secs 30   # Selbsttest: 3 Bots + 1 Chromium ueber die echte Kette
Wichtige Schalter (alle mit Standard aus dem Szenario, einzeln ueberschreibbar):
  --base 8960          Ports: +0 nginx, +1 saga-media, +2 Admin, +7 intern, +8 eingebauter Knoten, +9 horn-1
  --n / --video        Bots gesamt / davon mit Kamera;  --path ws|tcp|udp|auto (Weg der Bots; udp/tcp nur mit --horn1,
                       auto = UDP wo der Knoten es bietet, sonst Tunnel - wie die Apps)
  --browsers k         k Chromium (Fake-Kamera mit Zeitcode) im selben Anruf; BR_EXE=<chromium> optional
  --sprechen MUSTER    z. B. "haupt:1,einwurf:2,gleichzeitig:4,gemurmel:0" (Rest still) - M6
  --kommen-gehen S     alle S Sekunden geht ein Bot, im naechsten Takt kommt einer (fkey-Sturm, M5)
  --horn1, --aufteilen Bots je zur Haelfte auf eingebauten Knoten und horn-1 (Kaskade, M4)
  --knoten-cpu "Q P"   cgroup cpu.max fuer den eingebauten Knoten (z. B. "100000 100000" = 1 Kern, M7; Ueberlauf M4)
  --cpuset             nginx/Saga/saga-media/Knoten auf Kerne 0-3, Bots und dieses Werkzeug 4-11, Browser 12-15 (§4)
  --leitung "up=1000&down=1000&rtt=150&loss=0.03"   Browser 1 hinter nsleitung (eigener Netz-Namensraum, M8)
  --eine-adresse       alle Bots hinter EINER Client-Adresse (gemeinsames NAT: HORN_MAX_PER_IP greift); sonst je Bot eine
                       eigene Adresse 198.18.x.y als X-Forwarded-For hinter nginx (vertrauter Proxy), wie echte Anschluesse
  --geraete-je-konto K  Geraete je Bot-Konto (Standard: 2, wenn n + Browser > 50 = MEMBERS_MAX, sonst 1). Das erste ist
                       Hauptgeraet, die weiteren sind Begleitgeraete (POST /api/chat/devices wie echte Zweitgeraete, eigene
                       Sitzung; Saga macht sie im Anruf zu "companion" - ohne Ton, horn-bot mic:false) - M3 "Ton 100"
  --browser-knoten L   mit --horn1: Browser fest je Knoten, je Browser ein Eintrag (eingebaut | horn1 | Knotenname), z. B.
                       "eingebaut,horn1"; Testhaken SAGA_TEST_HORN_PIN. Bild-Latenz dann getrennt nach gleichem Knoten /
                       ueber Bruecke (browsers.latency_same_ms / latency_bridge_ms); Bots messen das immer (bots.lat_*)
  --kein-override      Obergrenzen nicht anheben (sonst: Video-/Ton-Anruf mit mehr Geraeten als LIMITS -> Testhaken
                       SAGA_TEST_HORN_LIMITS, JSON rig.limits_override; Saga laeuft dann mit SAGA_TEST_HOOKS=1)
  --api-direkt         Saga-API/SSE direkt an saga-media statt ueber nginx (bis Bugs-DB 2438 der Standard)
  --teile10           fkey/state in Teilen zu 10 Zielen (nur fuer Server bis 2.5: hoechstens 10 Ziele je Anfrage, sonst
                       400, Bugs-DB 2434); Standard: alles in EINER Anfrage wie Web/Apps
  --no-e2e             Bots ohne Frame-E2E (Browser sehen ihre Medien dann nicht; Last ohne Krypto)
  --keep               Konten/Zustand nicht loeschen (Fehlersuche)
Ausgabe: JSON (--out, sonst stdout) - Vorlage fuer tools/perf/horn-2263.json: rig, cpu {python, media, node, horn1, bots}
(Mittel/Spitze je 0,5 s in % eines Kerns, Sekunden > 20 %, RSS MB), signals (Zeilen chat_call_signal, je Typ gesendet),
http (Anfragen, 429, Fehler), node (horn_node.load je Knoten, aswitch falls vorhanden), bots (horn-bot "sum": Beitritt,
erster Ton/Bild, Verlust, Einfrierer/min, Ton-Platzwechsel/min, Sprecher-Wechselzeit, E2E-Zaehler), fkey (Zeit bis zum
Schluesselwechsel nach jedem Abgang), browsers (Bild-Latenz p50/p95, fps Sprecher/Kacheln, Einfrierer/min, Tonluecken,
packetsLost; mit --browser-knoten latency_same_ms/latency_bridge_ms/latency_extra_ms), join (api_ms = Saga-API: Beitritt
+ Knotenwahl /horn; node_ms = horn-bot bis die Beine stehen; total_ms = beides bis "up"), bots.lat_same_ms/lat_bridge_ms
(Zeitcode Bot -> Bot im synthetischen Bild, nur mit Frame-E2E), fkey[].hidden (Paare ohne sichtbaren Platz, nicht gezaehlt).
SAGA_MEDIA_BIN / GJALLARHORN_BIN wie b2_frames (sonst go build)."""
import argparse
import base64
import glob
import http.client
import json
import os
import random
import re
import secrets
import shutil
import signal
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HZ = os.sysconf("SC_CLK_TCK")
PW = "Horn-Last-2263-Pass"
PREFIX = "hl63-"
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn", "ws"]   # App mit Medien-Tunnel (wie a3_tunnel)

# Szenarien §4 (Standardwerte; Schalter ueberschreiben)
SZENARIEN = {
    "M1": {"bench": True, "n": 6, "video": 6, "path": "tcp", "secs": 30},
    "M2": {"n": 12, "video": 12, "path": "ws", "secs": 60},
    "M3": {"n": 16, "video": 16, "path": "udp", "secs": 60, "horn1": True},
    "M4": {"n": 16, "video": 16, "path": "auto", "secs": 60, "horn1": True, "aufteilen": True},
    "M5": {"n": 16, "video": 16, "path": "ws", "secs": 300, "kommen_gehen": 10},
    "M6": {"n": 25, "video": 0, "path": "ws", "secs": 120, "sprechen": "haupt:1,einwurf:2,gleichzeitig:4,gemurmel:6"},
    "M7": {"n": 12, "video": 12, "path": "ws", "secs": 60, "knoten_cpu": "100000 100000"},
    "M8": {"n": 11, "video": 11, "path": "ws", "secs": 60, "browsers": 1, "leitung": "up=1000&down=1000&rtt=150&loss=0.03"},
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def pctl(v, q):
    v = sorted(x for x in v if isinstance(x, (int, float)))
    return round(v[min(len(v) - 1, int(q * len(v)))], 1) if v else None


# --- Prozesse: CPU/RSS (Wiederverwendung tools/perf/media.py Proc) -----------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from media import Proc  # noqa: E402


def children(pid):
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            st = open(d + "/stat").read()
            if int(st.rsplit(") ", 1)[1].split()[1]) == pid:
                out.append((int(d[6:]), open(d + "/cmdline", "rb").read().split(b"\0")))
        except (OSError, ValueError, IndexError):
            pass
    return out


class Sampler(threading.Thread):
    """Alle 0,5 s CPU (% eines Kerns) und RSS je Prozess; Mittel, Spitze, Sekunden > 20 %."""

    def __init__(self, pids):
        super().__init__(daemon=True)
        self.pids, self.s, self.stop_ = pids, {}, threading.Event()
        self.rss = {}

    def run(self):
        last = {}
        t0 = time.time()
        while not self.stop_.is_set():
            now = time.time()
            for k, pid in list(self.pids().items()) if callable(self.pids) else self.pids.items():
                try:
                    c = Proc(pid).cpu()
                    self.rss[k] = max(self.rss.get(k, 0), Proc(pid).mem())
                except (OSError, ValueError, IndexError):
                    continue
                if k in last and now > last[k][0]:
                    self.s.setdefault(k, []).append(round((c - last[k][1]) / (now - last[k][0]) * 100, 1))
                last[k] = (now, c)
            self.stop_.wait(max(0.05, 0.5 - (time.time() - now)))
        self.secs = time.time() - t0

    def result(self):
        out = {}
        for k, v in self.s.items():
            if v:
                out[k] = {"mean": round(statistics.mean(v), 1), "peak": max(v), "p95": pctl(v, 0.95),
                          "secs_over_20": round(sum(1 for x in v if x > 20) * 0.5, 1), "rss_mb": self.rss.get(k, 0), "samples": len(v)}
        return out


# --- HTTP an Saga (Zaehler fuer 429) -------------------------------------------------------------------------------
class Api:
    def __init__(self, base, host):
        u = urllib.parse.urlparse(base)
        self.h, self.p, self.host = u.hostname, u.port, host
        self.tls = ssl._create_unverified_context() if u.scheme == "https" else None   # nginx des Rigs: selbst signiert
        self.lock = threading.Lock()
        self.n, self.n429, self.err, self.by, self.bad = 0, 0, 0, {}, {}
        self.ips = {}                                   # Sitzung -> Adresse (X-Forwarded-For, wie verschiedene Anschluesse)

    def conn(self, timeout):
        if self.tls:
            return http.client.HTTPSConnection(self.h, self.p, timeout=timeout, context=self.tls)
        return http.client.HTTPConnection(self.h, self.p, timeout=timeout)

    def req(self, m, path, cookie=None, body=None, retry=True):
        h = {"Host": self.host, "Accept-Language": "de", "Content-Type": "application/json"}
        if cookie:
            h["Cookie"], h["X-Saga"] = "saga=" + cookie, "1"
            if cookie in self.ips:
                h["X-Forwarded-For"] = self.ips[cookie]
        data = json.dumps(body) if body is not None else None
        for _ in range(2):
            c = self.conn(30)
            try:
                c.request(m, path, body=data, headers=h)
                r = c.getresponse()
                raw = r.read()
            except OSError:
                with self.lock:
                    self.err += 1
                return 0, {}
            finally:
                c.close()
            key = m + " " + re.sub(r"/[0-9a-f]{32}", "/{id}", path.split("?")[0])
            with self.lock:
                self.n += 1
                self.by[key] = self.by.get(key, 0) + 1
                if r.status != 200:
                    sk = f"{key} {r.status}"
                    self.bad[sk] = self.bad.get(sk, 0) + 1
                if r.status == 429:
                    self.n429 += 1
                elif r.status >= 500:
                    self.err += 1
            try:
                out = json.loads(raw)
            except ValueError:
                out = {}
            if r.status == 429 and retry:
                time.sleep(min(5.0, float(r.getheader("Retry-After") or 1)))
                continue
            return r.status, out
        return r.status, out

    def stats(self):
        return {"requests": self.n, "429": self.n429, "errors": self.err, "by": dict(sorted(self.by.items())), "not_200": dict(sorted(self.bad.items()))}


class SSE(threading.Thread):
    """Live-Kanal einer Person (/api/events): ruft cb(typ, daten) je Ereignis."""

    def __init__(self, api, cookie, cb):
        super().__init__(daemon=True)
        self.api, self.cookie, self.cb, self.conn, self.ready = api, cookie, cb, None, threading.Event()

    def run(self):
        while True:
            try:
                self.conn = self.api.conn(600)
                hd = {"Host": self.api.host, "Accept": "text/event-stream", "Cookie": "saga=" + self.cookie}
                if self.cookie in self.api.ips:
                    hd["X-Forwarded-For"] = self.api.ips[self.cookie]
                self.conn.request("GET", "/api/events", headers=hd)
                r = self.conn.getresponse()
                self.ready.set()
                cur = {}
                while True:
                    ln = r.readline()
                    if not ln:
                        break
                    ln = ln.decode("utf-8", "replace").rstrip("\r\n")
                    if ln == "":
                        if "event" in cur:
                            try:
                                self.cb(cur["event"], json.loads(cur.get("data") or "null"))
                            except Exception as ex:  # noqa: BLE001 - Messung laeuft weiter
                                log("sse cb:", type(ex).__name__, ex)
                        cur = {}
                    elif not ln.startswith(":"):
                        k, _, v = ln.partition(":")
                        cur[k] = v[1:] if v.startswith(" ") else v
            except OSError:
                pass
            if getattr(self, "closed", False):
                return
            time.sleep(1)

    def close(self):
        self.closed = True
        try:
            self.conn.sock.shutdown(socket.SHUT_RDWR)
        except Exception:  # noqa: BLE001
            pass


# --- Schluessel (docs/HORN-E2E.md §4) -----------------------------------------------------------------------------
def ratchet(k):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"saga-horn-ratchet", info=b"").derive(k)


def b64(b):
    return base64.b64encode(b).decode()


def tok_pub(cand, kind):
    """Darf das Geraet laut Medien-Token (h1.<payload>.<sig>, saga/horn.py) kind ("a", "v", "s") senden?"""
    for r in ("both", "audio") if kind == "a" else ("both", "video"):
        t = ((cand or {}).get("tokens") or {}).get(r) or ""
        try:
            pl = t.split(".")[1]
            if kind in json.loads(base64.urlsafe_b64decode(pl + "=" * (-len(pl) % 4))).get("pub", []):
                return True
        except (IndexError, ValueError):
            continue
    return False


class Bot:
    def __init__(self, i, person, dev=None, k=None):
        self.i, self.p = i, person
        self.uid, self.dev = person.uid, dev or person.dev
        self.id = self.dev["id"]
        self.k = k or person.k   # Sitzung: Begleitgeraete haben eine eigene (wie ein zweites Geraet mit eigener Anmeldung)
        self.companion = dev is not None and dev is not person.dev
        self.mic = not self.companion
        self.key = None          # {"n", "k", "s"}
        self.pend = None         # wartender Wechsel {"n", "k", "need": set, "t"}
        self.up = False
        self.in_call = False
        self.acks = {}           # Ziel -> Body (gesammelt, <= 100 ms)
        self.peer_s = {}         # Sender -> Sitzung s (aus fkey)
        self.lock = threading.Lock()
        self.sse = None
        self.joined_ms = None    # Saga-API: Beitritt (+ /horn, wenn der Bot einen anderen Knoten waehlt)
        self.join_t0 = None
        self.total_ms = self.node_ms = None


class Last:
    def __init__(self, a):
        self.a = a
        self.res = {"szenario": a.szenario, "start": time.strftime("%Y-%m-%dT%H:%M:%S"), "args": {k: v for k, v in vars(a).items()}}
        self.bots, self.byid, self.roster, self.keys = [], {}, {}, {}
        self.events = {"err": [], "first": [], "genwait": [], "down": [], "warn": []}
        self.hb, self.hb_sum, self.hb_stats = None, None, {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(32)
        self.sent, self.recv = {}, {}
        self.cid = None
        self.stop = threading.Event()
        self.leaves = []

    # --- horn-bot -----------------------------------------------------------------------------------------------------
    def hb_start(self, exe):
        cmd = [exe, "horn-bot", "-insecure", "-resolve", f"{self.B2.NAME}=127.0.0.1,horn-1.example.org=127.0.0.1"]
        if self.a.cpuset:
            cmd = ["taskset", "-c", "4-11"] + cmd
        self.hb_log = open(os.path.join(self.state, "horn-bot.log"), "a")
        self.hb = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.hb_log, text=True, bufsize=1)
        threading.Thread(target=self.hb_read, daemon=True).start()

    def hb_send(self, **c):
        with self.lock:
            try:
                self.hb.stdin.write(json.dumps(c) + "\n")
                self.hb.stdin.flush()
            except (OSError, ValueError):
                pass

    def hb_read(self):
        for ln in self.hb.stdout:
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            ev = m.get("ev")
            if ev == "up":
                b = self.byid.get(m["id"])
                if b:
                    if b.join_t0 and b.total_ms is None:
                        b.total_ms, b.node_ms = round((time.time() - b.join_t0) * 1000, 1), m.get("ms")
                    b.up = True
                    self.pool.submit(self.key_start, b)
            elif ev == "stats":
                self.hb_stats[m["id"]] = m
            elif ev == "sum":
                self.hb_sum = m
            elif ev == "need":                         # Nachforderung (HORN-E2E §4): fkey-ack ok:false an den Sender
                b = self.byid.get(m["id"])
                if b and b.in_call and m.get("from") in self.roster:
                    self.need = getattr(self, "need", 0) + 1
                    s_ = b.peer_s.get(m["from"], "")
                    self.pool.submit(self.send, b, {m["from"]: {"t": "fkey-ack", "n": m.get("n", -1), "s": s_, "ok": False}})
            elif ev in self.events:
                self.events[ev].append(m)
                if ev == "err":
                    log("horn-bot:", m)
                b = self.byid.get(m.get("id"))
                if ev == "down" and b and b.in_call and not self.stop.is_set():
                    self.pool.submit(self.rebuild, b)

    # --- Signale --------------------------------------------------------------------------------------------------------
    def dev_keys(self, devs):
        """enc/sign-Schluessel fremder Geraete (Browser) ueber /api/chat/keys."""
        need = {self.roster[d]["who"] for d in devs if d not in self.keys and d in self.roster}
        if need:
            s, d = self.api.req("GET", "/api/chat/keys?users=" + ",".join(sorted(need)), self.bots[0].k)
            for u, v in ((d or {}).get("users") or {}).items():
                for x in (v or {}).get("devices") or []:
                    self.keys[x["id"]] = {"uid": u, "enc_pk": base64.b64decode(x["enc_pub"]), "sign_pk": base64.b64decode(x["sign_pub"])}

    def send(self, b, bodies):
        """bodies {zielgeraet: dict} versiegelt in EINER Anfrage (wie Web/Apps)."""
        import chatlib as C
        self.dev_keys(list(bodies))
        sig = {}
        for to, body in bodies.items():
            k = self.keys.get(to)
            if not k:
                continue
            sig[to] = C.seal_signal(self.cid, b.uid, b.dev, k["uid"], to, k["enc_pk"], json.dumps(body, separators=(",", ":")))
            t = body.get("t")
            with self.lock:
                self.sent[t] = self.sent.get(t, 0) + 1
        # Web/Apps schicken fkey/state an alle in EINER Anfrage (HORN-E2E §4); Server ab 2.6 nehmen im Raum bis zur
        # Anruf-Obergrenze (Bugs-DB 2434). --teile10 stueckelt fuer Server bis 2.5 (dort mehr als 10 Ziele -> 400).
        items = list(sig.items())
        step = 10 if self.a.teile10 else len(items)
        for i in range(0, len(items), max(1, step)):
            st, d = self.api.req("POST", f"/api/calls/{self.cid}/signal", b.k, {"device": b.id, "signals": dict(items[i:i + step])})
            if st == 200 and (d or {}).get("dropped"):
                with self.lock:
                    self.sig_dropped = getattr(self, "sig_dropped", 0) + len(d["dropped"])

    def others(self, b):
        return [d for d, x in self.roster.items() if x["state"] == "active" and d != b.id]

    def key_start(self, b):
        """Start: eigenes Bein steht - K zufaellig, n = 0, fkey an alle; dazu state."""
        with b.lock:
            b.key = {"n": 0, "k": os.urandom(32), "s": secrets.token_hex(8)}
            k = b.key
        self.hb_send(op="own", id=b.id, n=0, key=b64(k["k"]))
        os_ = self.others(b)
        self.send(b, {d: {"t": "fkey", "n": 0, "kid": 0, "key": b64(k["k"]), "s": k["s"]} for d in os_})
        self.send(b, {d: {"t": "state", "mic": b.mic, "cam": b.i < self.a.video} for d in os_})

    def on_roster(self, d):
        if d.get("call_id") != self.cid:
            return
        legs = {x["device"]: x for x in d.get("legs") or []}
        old = {k for k, v in self.roster.items() if v["state"] == "active"}
        self.roster = {k: {"who": x.get("who"), "state": x.get("state") or "active", "refs": [r for r in [x.get("hp")] + list((x.get("hpn") or {}).values()) if r]}
                       for k, x in legs.items()}
        now = {k for k, v in self.roster.items() if v["state"] == "active"}
        new, gone = now - old, old - now
        for b in self.bots:
            if not (b.in_call and b.key) or b.id in gone:
                continue
            for x in new - {b.id}:
                self.pool.submit(self.key_join, b, x)
            if gone - {b.id}:
                self.pool.submit(self.key_leave, b)

    def key_join(self, b, x):
        """Beitritt von x: neuesten (bzw. wartenden) Schluessel ratschen, n+1, sofort aktiv; fkey nur an x."""
        with b.lock:
            tgt = b.pend if b.pend else b.key
            tgt["k"], tgt["n"] = ratchet(tgt["k"]), tgt["n"] + 1
            n, k, s, pend = tgt["n"], tgt["k"], b.key["s"], bool(b.pend)
            if pend:
                b.pend["need"].add(x)
        if not pend:
            self.hb_send(op="own", id=b.id, n=n, key=b64(k))
        else:
            left = max(0, int((b.pend["t"] - time.time()) * 1000))
            self.hb_send(op="own", id=b.id, n=n, key=b64(k), after_ms=left)
        self.send(b, {x: {"t": "fkey", "n": n, "kid": n % 16, "key": b64(k), "s": s}})

    def key_leave(self, b):
        """Abgang: Abgaenge innerhalb von 0,5 s -> ein Wechsel; neues K, n+1, fkey an alle, Umschalten nach 1,5 s (frueher bei
        allen Quittungen)."""
        with b.lock:
            if getattr(b, "leave_t", 0) > time.time():
                return
            b.leave_t = time.time() + 0.5
        time.sleep(0.5)
        with b.lock:
            n = (b.pend or b.key)["n"] + 1
            k = os.urandom(32)
            os_ = self.others(b)
            b.pend = {"n": n, "k": k, "need": set(os_), "t": time.time() + 1.5}
            s = b.key["s"]
        self.hb_send(op="own", id=b.id, n=n, key=b64(k), after_ms=1500)
        lv = self.leaves[-1] if self.leaves else None
        if lv and time.time() - lv["t0"] < 3:
            lv["want"][b.id] = n                         # fuer genwait: neue Generation dieses Senders
        self.send(b, {d: {"t": "fkey", "n": n, "kid": n % 16, "key": b64(k), "s": s} for d in os_})
        threading.Timer(1.6, self.key_switched, (b, n)).start()

    def key_switched(self, b, n):
        with b.lock:
            if b.pend and b.pend["n"] == n:
                b.key = {"n": n, "k": b.pend["k"], "s": b.key["s"]}
                b.pend = None

    def on_signal(self, b, d):
        import chatlib as C
        if d.get("call_id") != self.cid or d.get("to_device") != b.id or not b.in_call:
            return
        frm = d.get("from_device")
        self.dev_keys([frm])
        k = self.keys.get(frm)
        if not k:
            return
        try:
            body, sg = C.open_signal(d["sealed"], b.dev["enc_pk"], b.dev["enc_sk"])
        except Exception:  # noqa: BLE001
            return
        if not C.verify_signal(self.cid, k["uid"], frm, b.uid, b.id, body, sg, k["sign_pk"]):
            return
        try:
            o = json.loads(body)
        except ValueError:
            return
        t = o.get("t")
        with self.lock:
            self.recv[str(t)] = self.recv.get(str(t), 0) + 1
        if t == "fkey" and isinstance(o.get("key"), str):
            refs = (self.roster.get(frm) or {}).get("refs")
            if not refs:
                self.refresh_roster()
                refs = (self.roster.get(frm) or {}).get("refs")
            self.hb_send(op="key", id=b.id, **{"from": frm}, refs=refs or [], n=int(o.get("n", 0)), key=o["key"], s=str(o.get("s", "")))
            with b.lock:
                b.peer_s[frm] = str(o.get("s", ""))
                first = not b.acks
                b.acks[frm] = {"t": "fkey-ack", "n": o.get("n"), "s": o.get("s"), "ok": True}
            if first:
                threading.Timer(0.1, self.flush_acks, (b,)).start()
        elif t == "fkey-ack" and o.get("ok") is False:
            # Nachforderung (HORN-E2E §4): neuesten Schluessel an diesen Empfaenger, nie einen aelteren
            with b.lock:
                k = b.pend or b.key
                k = dict(k, s=b.key["s"]) if k and b.key else None
            if k:
                self.nach = getattr(self, "nach", 0) + 1
                self.send(b, {frm: {"t": "fkey", "n": k["n"], "kid": k["n"] % 16, "key": b64(k["k"]), "s": k["s"]}})
        elif t == "fkey-ack":
            done = False
            with b.lock:
                if b.pend and o.get("n") == b.pend["n"]:
                    b.pend["need"].discard(frm)
                    done = not b.pend["need"]
                    n = b.pend["n"]
            if done:
                self.hb_send(op="switch", id=b.id)
                self.key_switched(b, n)

    def refresh_roster(self):
        s, d = self.api.req("GET", f"/api/calls/{self.cid}", self.bots[0].k)
        if s == 200:
            self.on_roster({"call_id": self.cid, "legs": ((d or {}).get("call") or {}).get("legs") or []})

    def flush_acks(self, b):
        with b.lock:
            acks, b.acks = b.acks, {}
        if acks:
            self.send(b, acks)

    def on_event(self, b, typ, d):
        if typ == "call.signal":
            self.on_signal(b, d)
        elif typ == "call.roster" and b is self.bots[0]:
            self.on_roster(d)

    # --- Ablauf ---------------------------------------------------------------------------------------------------------
    def pids(self):
        out = {}
        if self.B2.P.get("proc") and self.B2.P["proc"].poll() is None:
            out["media"] = self.B2.P["proc"].pid
            for pid, cmd in children(out["media"]):
                if b"run.py" in cmd:
                    out["python"] = pid
                elif cmd and cmd[0].endswith(b"gjallarhorn"):
                    out["node"] = pid
        if self.h1.get("proc") and self.h1["proc"].poll() is None:
            out["horn1"] = self.h1["proc"].pid
        if self.hb and self.hb.poll() is None:
            out["bots"] = self.hb.pid
        return out

    def join_bot(self, b, start=False):
        t0 = time.time()
        b.join_t0, b.total_ms, b.node_ms = t0, None, None
        if start:
            s, d = self.api.req("POST", "/api/calls", b.k, {"id": self.cid, "chat_id": self.gid, "media": "video" if self.a.video else "audio",
                                                              "device": b.id, "caps": CAPS})
        else:
            s, d = self.api.req("POST", f"/api/calls/{self.cid}/join", b.k, {"device": b.id, "role": "member", "caps": CAPS})
        call = (d or {}).get("call") or {}
        cands = (call.get("horn") or {}).get("candidates") or []
        b.joined_ms = round((time.time() - t0) * 1000, 1)
        if s != 200 or not cands:
            b.join_t0 = None
            self.events["err"].append({"id": b.id, "msg": f"Beitritt {s}: {d.get('error') or d.get('join_block')}"})
            log("Beitritt", b.uid, s, d.get("error"), d.get("join_block"))
            return False
        if start:
            self.res["call"] = {"topo": call.get("topo"), "limit": call.get("limit"), "e2e": (call.get("horn") or {}).get("e2e")}
        b.in_call = True
        c = self.pick(b, cands)
        if c is None:
            self.events["err"].append({"id": b.id, "msg": "kein passender Kandidat", "cands": [x.get("node") for x in cands]})
            return False
        if c is not cands[0] or self.a.aufteilen:              # Wahl an Saga melden (wie nach dem Messen) -> frische Token
            s, d = self.api.req("POST", f"/api/calls/{self.cid}/horn", b.k, {"device": b.id, "node": c["node"]})
            cc = [x for x in (((d or {}).get("horn") or {}).get("candidates") or []) if x["node"] == c["node"]]
            if cc:
                cc[0]["_path"] = c["_path"]
                c = cc[0]
            b.joined_ms = round((time.time() - t0) * 1000, 1)
        self.add_bot(b, c)
        return True

    def add_bot(self, b, c):
        """Bot an horn-bot: Ton nur, wenn das Token es erlaubt (Begleitgeraet: Saga gibt kein "a")."""
        b.mic = tok_pub(c, "a")
        if b.companion and b.mic:
            log("Achtung: Begleitgeraet", b.id[:8], "bekam ein Token mit Ton (Hauptgeraet war spaeter)")
        b.node = c["node"]
        self.hb_send(op="add", id=b.id, cand=c, path=c.get("_path") or self.a.path, video=b.i < self.a.video, tiles=self.a.tiles, device="desktop",
                     script=self.script_of(b.i) if b.mic else "still", e2e=not self.a.no_e2e, ip=b.ip, mic=b.mic)

    def pick(self, b, cands):
        p = self.a.path
        if p == "auto":                                    # wie die Apps: UDP, wo der Knoten es bietet, sonst Tunnel
            ok = [c for c in cands if c.get("udp") or c.get("ws")]
            for c in ok:
                c["_path"] = "udp" if c.get("udp") else "ws"
        else:
            ok = [c for c in cands if (p == "ws" and c.get("ws")) or (p == "udp" and c.get("udp")) or (p == "tcp" and c.get("tcp") is not False)]
        if not ok:
            return None
        for c in ok:
            c.setdefault("_path", p)
        if self.a.aufteilen and len({c["node"] for c in ok}) > 1:
            names = sorted({c["node"] for c in ok})
            want = names[b.i % len(names)]
            return [c for c in ok if c["node"] == want][0]
        return ok[0]

    def script_of(self, i):
        order = []
        for part in (self.a.sprechen or "").split(","):
            if ":" in part:
                nm, k = part.split(":", 1)
                order += [nm.strip()] * int(k or 0)
        return order[i] if i < len(order) else ("haupt" if i == 0 and not self.a.sprechen else "still")

    def leave_bot(self, b):
        t0 = time.time()
        b.in_call, b.up = False, False                     # vor dem Abmelden: "closed" vom Knoten ist dann kein Neuaufbau
        self.api.req("POST", f"/api/calls/{self.cid}/leave", b.k, {"device": b.id})
        self.hb_send(op="leave", id=b.id)
        b.key, b.pend = None, None
        # Zeit bis die Verbliebenen von jedem verbliebenen Bot den neuen Schluessel aufmachen (0,5 s Sammeln + Versand + <= 1,5 s)
        lv = {"t0": t0, "want": {}}
        self.leaves.append(lv)

        def gw():
            want = {d: n for d, n in lv["want"].items() if d in self.byid and self.byid[d].in_call}
            if want:
                self.hb_send(op="genwait", tag=f"gehen-{b.i}-{int(t0)}", t0=int(t0 * 1000), want=want, timeout_ms=10000)
        threading.Timer(1.2, gw).start()

    def rebuild(self, b):
        """Bein vom Knoten beendet: wie die Clients frische Kandidaten (/horn) holen und neu aufbauen (neue Sitzung s)."""
        self.rebuilds = getattr(self, "rebuilds", 0) + 1
        self.hb_send(op="leave", id=b.id)
        b.up, b.key, b.pend = False, None, None
        time.sleep(1)
        s, d = self.api.req("POST", f"/api/calls/{self.cid}/horn", b.k, {"device": b.id, "node": getattr(b, "node", None)})
        cands = ((d or {}).get("horn") or {}).get("candidates") or []
        c = self.pick(b, cands) if s == 200 else None
        if c is None:
            self.events["err"].append({"id": b.id, "msg": f"Neuaufbau: /horn {s}"})
            return
        self.add_bot(b, c)

    def alive_loop(self):
        k = 0
        while not self.stop.wait(1.0):
            k += 1
            for b in self.bots:
                if not b.in_call:
                    continue
                if k % 15 == b.i % 15:
                    self.pool.submit(self.api.req, "POST", f"/api/calls/{self.cid}/alive", b.k, {"device": b.id})
                if self.a.horn_every and k % self.a.horn_every == (b.i * 7) % self.a.horn_every:
                    self.pool.submit(self.api.req, "POST", f"/api/calls/{self.cid}/horn", b.k, {"device": b.id, "node": getattr(b, "node", None)})

    def churn_loop(self):
        gone = []
        while not self.stop.wait(self.a.kommen_gehen):
            if gone:
                b = gone.pop(0)
                self.pool.submit(self.join_bot, b)
            else:
                cand = [b for b in self.bots[1:] if b.in_call and b.up]
                if cand:
                    b = random.choice(cand)
                    gone.append(b)
                    self.pool.submit(self.leave_bot, b)

    def node_load(self):
        from db import connect
        with connect() as c:
            rows = c.execute("SELECT name, builtin, udp, load, state FROM horn_node WHERE removed IS NULL").fetchall()
        out = {}
        for nm, bi, udp, ld, st in rows:
            ld = ld if isinstance(ld, dict) else json.loads(ld or "{}")
            out[nm] = {"builtin": bool(bi), "udp": bool(udp), "state": st, "load": ld}
        return out

    def load_loop(self):
        """Lastmeldungen der Knoten alle 5 s (Reihe): util/cpu/legs/fwd_p95_us/drops und Bruecken (Kaskade Auf-/Abbau)."""
        self.loads, last = [], {}
        self.bridge_changes = 0
        while not self.stop.wait(5):
            try:
                nl = self.node_load()
            except Exception:  # noqa: BLE001
                continue
            row = {"t": round(time.time(), 1)}
            for nm, x in nl.items():
                ld = x["load"] or {}
                br = sorted((ld.get("bridges") or {}).keys())
                if nm in last and last[nm] != br:
                    self.bridge_changes += len(set(br) ^ set(last[nm]))
                last[nm] = br
                row[nm] = {k: ld.get(k) for k in ("cpu", "legs", "peers", "fwd_p95_us", "drops", "up", "down", "aswitch") if k in ld}
                row[nm]["bridges"] = len(br)
            self.loads.append(row)

    def sig_rows(self):
        """Zeilen chat_call_signal dieses Anrufs (bleiben bis zum Ende des Anrufs; die Folgenummer taugt nicht - sie laeuft
        ueber geloeschte Anrufe weiter)."""
        from db import connect
        with connect() as c:
            return c.execute("SELECT count(*) FROM chat_call_signal WHERE call_id=?", (self.cid,)).fetchone()[0]


# --- Rig ------------------------------------------------------------------------------------------------------------
def horn1_start(B2, node_bin, port, state):
    """Gekoppelter Knoten horn-1 auf 127.0.0.1 (TCP+UDP): Registereintrag + pair.json wie horn_flattern_2202."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from db import connect
    from saga import horn as HN
    name = "horn-1.example.org"
    data = os.path.join(state, "horn1")
    os.makedirs(data, mode=0o700, exist_ok=True)
    seed = os.urandom(32)
    pub = base64.b64encode(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    open(os.path.join(data, "node.key"), "w").write(base64.b64encode(seed).decode() + "\n")
    os.chmod(os.path.join(data, "node.key"), 0o600)
    nid = secrets.token_hex(8)
    base = f"http://127.0.0.1:{B2.PORT}"
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, v4, udp, state, created, port) VALUES (?,?,?,0,?,?,1,'neu',?,?)",
                  (nid, name, pub, f"https://{name}:{port}", "127.0.0.1", time.time(), port))
    json.dump({"node_id": nid, "saga_pub": HN.saga_pub(), "ctl_url": f"ws://127.0.0.1:{B2.PORT}/api/horn/ctl", "name": name,
               "saga": base, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(os.path.join(data, "pair.json"), "w"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HORN_", "SAGA_", "B2_")) and k != "PYTHONPATH"}
    env.update(HORN_SAGA=base, HORN_NAME=name, HORN_DATA=data, HORN_LISTEN=f"127.0.0.1:{port}", HORN_UDP="1", HORN_TLS_SELF="1",
               HORN_ICE_IPS="127.0.0.1", HORN_PUBLIC_IP="127.0.0.1", HORN_PROXY_PROTOCOL="0", HORN_PUBLIC_URL=f"https://127.0.0.1:{port}")
    for k in ("HORN_MAX_LEGS", "HORN_MAX_PER_IP"):
        if os.environ.get("HL_" + k):
            env[k] = os.environ["HL_" + k]
    lf = open(os.path.join(state, "horn1.log"), "a")
    p = subprocess.Popen([node_bin, "node"], env=env, stdout=lf, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 30:
        with connect() as c:
            n = HN.node_named(c, name)
        if n and n.get("state") == "ok":
            return p, n
        time.sleep(0.5)
    return p, None


def cleanup_accounts():
    from db import connect
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE ?", (PREFIX + "%",)).fetchall()]
    for u in users:
        try:
            delete_user_data(u, drop_user=True)
        except TypeError:
            delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "chat_identity", "chat_device", "chat_link", "push_sub", "push_queue", "device_login"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE ?", (PREFIX + "%",))
        c.execute("DELETE FROM chat_call WHERE caller LIKE ?", (PREFIX + "%",))
        c.execute("DELETE FROM chat WHERE created_by LIKE ?", (PREFIX + "%",))
        c.execute("DELETE FROM users WHERE username LIKE ?", (PREFIX + "%",))


def cgroup(name, pid, cpu_max):
    d = "/sys/fs/cgroup/" + name
    try:
        os.makedirs(d, exist_ok=True)
        open(d + "/cpu.max", "w").write(cpu_max)
        open(d + "/cgroup.procs", "w").write(str(pid))
        return d
    except OSError as ex:
        log("cgroup:", ex)
        return None


def cgroup_del(d):
    if not d:
        return
    try:
        for pid in open(d + "/cgroup.procs").read().split():
            open("/sys/fs/cgroup/cgroup.procs", "w").write(pid)
        os.rmdir(d)
    except OSError:
        pass


def gobuild_bin(B2):
    return B2.gobuild("saga-media", "media", "SAGA_MEDIA_BIN"), B2.gobuild("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")


# --- M1: Grundlast ohne Saga ------------------------------------------------------------------------------------------
def run_bench(a, node):
    d = tempfile.mkdtemp(prefix="hl63-bench-")
    os.makedirs(d + "/results")
    cmd = [node, "bench", "--sfu", "-n", str(a.n), "--video", str(a.video), "--path", a.path if a.path in ("tcp", "udp") else "tcp",
           "--secs", str(a.secs), "--data", d]
    if a.cpuset:
        cmd = ["taskset", "-c", "0-11"] + cmd
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.secs + 180)
    out = {"cmd": " ".join(cmd[cmd.index(node):]).replace(node, "gjallarhorn"), "rc": r.returncode, "stdout": r.stdout[-3000:]}
    fs = sorted(glob.glob(d + "/results/*.json"))
    if fs:
        out["result"] = json.load(open(fs[-1]))
    shutil.rmtree(d, ignore_errors=True)
    if r.returncode:
        out["stderr"] = r.stderr[-1500:]
    return out


# --- Testhaken: Obergrenzen und Browser je Knoten (Bugs-DB 2438) ---------------------------------------------------------
def limits_need(a, HN):
    """Obergrenzen fuer SAGA_TEST_HORN_LIMITS, wenn dieser Anruf (alle Geraete: Bots + Browser) ueber HN.LIMITS liegt -
    sonst None (Saga ohne Testhaken, Verhalten wie im Betrieb). Der Anruf zaehlt jedes Geraet gegen die Grenze seiner Art
    (Video-Anruf: Video-Grenze, auch fuer Geraete ohne Kamera); welcher Weg gilt, legt das erste Geraet fest - beide Wege
    werden angehoben, wo noetig."""
    n = a.n + a.browsers
    out = {}
    for w, (v, au) in HN.LIMITS.items():
        nv, na = (max(v, n), max(au, n)) if a.video else (v, max(au, n))
        if (nv, na) != (v, au):
            out[w] = [nv, max(nv, na)]
    if any(x > HN.LIMITS_TOP for va in out.values() for x in va):
        sys.exit(f"{n} Geraete: mehr als {HN.LIMITS_TOP} (LIMITS_TOP) gehen auch mit Testhaken nicht")
    return out or None


def browser_pins(a):
    """--browser-knoten "eingebaut,horn1" -> {konto: knoten} fuer SAGA_TEST_HORN_PIN (eingebaut = "@builtin")."""
    if not a.browser_knoten:
        return {}
    xs = [x.strip().lower() for x in a.browser_knoten.split(",") if x.strip()]
    if len(xs) != a.browsers:
        sys.exit(f"--browser-knoten: {len(xs)} Eintraege fuer {a.browsers} Browser")
    m = {"eingebaut": "@builtin", "builtin": "@builtin", "horn1": "horn-1.example.org", "horn-1": "horn-1.example.org"}
    out = {f"{PREFIX}w{i + 1}": m.get(x, x) for i, x in enumerate(xs)}
    if "horn-1.example.org" in out.values() and not a.horn1:
        sys.exit("--browser-knoten horn1 braucht --horn1")
    return out


# --- Hauptablauf --------------------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--szenario", default="")
    ap.add_argument("--base", type=int, default=int(os.environ.get("HL_BASE") or 8960))
    for k, typ in (("n", int), ("video", int), ("secs", int), ("browsers", int), ("tiles", int), ("kommen_gehen", float),
                   ("horn_every", int), ("join_gap", float), ("geraete_je_konto", int)):
        ap.add_argument("--" + k.replace("_", "-"), dest=k, type=typ, default=None)
    for k in ("path", "sprechen", "knoten_cpu", "leitung", "out", "browser_knoten"):
        ap.add_argument("--" + k.replace("_", "-"), dest=k, default=None)
    for k in ("horn1", "aufteilen", "cpuset", "no_e2e", "keep", "bench", "eine_adresse", "teile10", "kein_override", "api_direkt"):
        ap.add_argument("--" + k.replace("_", "-"), dest=k, action="store_true", default=None)
    a = ap.parse_args()
    base = dict(n=3, video=3, path="ws", secs=30, browsers=0, tiles=4, kommen_gehen=0, horn_every=50, join_gap=0.2, sprechen="",
                knoten_cpu="", leitung="", horn1=False, aufteilen=False, cpuset=False, no_e2e=False, keep=False, bench=False,
                eine_adresse=False, teile10=False, geraete_je_konto=0, browser_knoten="", kein_override=False, api_direkt=False)
    base.update(SZENARIEN.get(a.szenario.upper(), {}))
    for k, v in base.items():
        if getattr(a, k, None) is None:
            setattr(a, k, v)
    a.szenario = a.szenario.upper() or "eigen"

    # Rig-Module (setzen HK_TEST_BASE/HK_TEST_ADMIN beim Import)
    os.environ["B2_BASE"] = str(a.base)
    os.environ["BASE_URL"] = f"https://saga.example.org:{a.base}"
    if a.leitung:
        os.environ.pop("B2_NGX_LISTEN", None)
    sys.path.insert(0, os.path.join(APP, "tests"))
    sys.path.insert(0, APP)
    import b2_frames as B2  # noqa: E402
    import anrufe as A_  # noqa: E402
    import auth  # noqa: E402
    import db  # noqa: E402
    from saga import chat as CH, horn as HN  # noqa: E402 - Grenzen aus dem Server (nicht doppelt gepflegt)
    if not a.geraete_je_konto:                         # automatisch: Begleitgeraete erst, wenn die Gruppe sonst zu gross waere
        a.geraete_je_konto = 2 if a.n + a.browsers > CH.MEMBERS_MAX else 1
    a.konten = -(-a.n // a.geraete_je_konto)
    if not 1 <= a.geraete_je_konto <= CH.DEVICES_MAX:
        sys.exit(f"--geraete-je-konto 1-{CH.DEVICES_MAX} (DEVICES_MAX, Geraete je Konto)")
    if a.konten + a.browsers > CH.MEMBERS_MAX:
        sys.exit(f"hoechstens {CH.MEMBERS_MAX} Mitglieder je Gruppe (MEMBERS_MAX): {a.konten} Bot-Konten + {a.browsers} Browser - "
                 "--geraete-je-konto erhoehen")
    pins = browser_pins(a)
    over = None if a.kein_override else limits_need(a, HN)

    out = {"szenario": a.szenario, "args": {k: v for k, v in vars(a).items() if k != "out"}, "t": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "host": {"cpus": os.cpu_count()}}
    try:
        out["rev"] = subprocess.run(["git", "-C", APP, "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        pass
    media, node = gobuild_bin(B2)
    if not (media and node):
        sys.exit("go build fehlgeschlagen")
    if a.bench:
        out["bench"] = run_bench(a, node)
        shutil.rmtree(B2.STATE, ignore_errors=True)    # Bau-Ordner von b2_frames (nur M1, sonst raeumt der finally-Teil)
        emit(a, out)
        return
    hooks = {}
    if over or pins:                                   # Testhaken im Saga-Prozess (saga/horn.py, nur mit Token >= 32 Zeichen)
        hooks = {"SAGA_TEST_HOOKS": "1", "SAGA_TEST_TOKEN": "hl63-" + secrets.token_hex(16)}
        if over:
            hooks["SAGA_TEST_HORN_LIMITS"] = json.dumps(over)
        if pins:
            hooks["SAGA_TEST_HORN_PIN"] = json.dumps(pins)
        os.environ.update(hooks)
    for p in (a.base, a.base + 1, a.base + 2, a.base + 7, a.base + 8, a.base + 9):
        with socket.socket() as so:
            if so.connect_ex(("127.0.0.1", p)) == 0:
                sys.exit(f"Port {p} belegt")

    L = Last(a)
    L.B2, L.state, L.h1 = B2, B2.STATE, {}
    cg = None
    nsl = None
    brw = None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    keep = {}
    try:
        db.wait_ready()
        db.init_schema()
        cleanup_accounts()
        prev = auth.settings(fresh=True)
        keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
        auth.save_setting("chat_enabled", True)
        auth.save_setting("share_group_scope", False)
        auth.save_setting("calls", dict(B2.CALLS, horn=True, horn_builtin=True, horn_from=3))
        auth._settings_cache["v"] = None
        ngx = B2.nginx_conf()
        r = subprocess.run(["nginx", "-c", ngx], capture_output=True, text=True)
        if r.returncode:
            sys.exit("nginx: " + r.stderr[-400:])
        B2.P["ngx"] = ngx
        if B2.start(media, node) is None:
            sys.exit("Saga startet nicht: " + B2.log_text()[-1500:])
        t0 = time.time()
        while time.time() - t0 < 30 and "tunnel-betrieb: kein eigener tcp-port" not in B2.log_text():
            time.sleep(0.3)
        time.sleep(2)
        if a.horn1:
            L.h1["proc"], n1 = horn1_start(B2, node, a.base + 9, L.state)
            out["horn1"] = bool(n1)
            if not n1:
                log("horn-1 nicht bereit")
        ps = L.pids()
        if a.cpuset:
            ngx_pids = []
            try:
                m = int(open(os.path.join(L.state, "ngx", "nginx.pid")).read().strip())
                ngx_pids = [m] + [p for p, _ in children(m)]
            except (OSError, ValueError):
                pass
            for pid in [ps[k] for k in ("media", "python", "node", "horn1") if k in ps] + ngx_pids:
                subprocess.run(["taskset", "-a", "-p", "-c", "0-3", str(pid)], capture_output=True)
            try:                                       # das Werkzeug selbst (Versiegeln, Live-Kanaele) laeuft bei den Bots
                os.sched_setaffinity(0, set(range(4, 12)) & set(range(os.cpu_count() or 1)) or {0})
            except OSError as ex:
                log("cpuset:", ex)
        if a.knoten_cpu and "node" in ps:
            cg = cgroup(f"hl63-{a.base}-knoten", ps["node"], a.knoten_cpu)
        out["rig"] = {"base": B2.BASE, "pids": ps, "cpuset": a.cpuset, "knoten_cpu": a.knoten_cpu or None, "horn1": a.horn1,
                      "limits_override": over, "browser_pin": pins or None, "api": "direkt" if a.api_direkt else "nginx",
                      "konten": a.konten, "geraete_je_konto": a.geraete_je_konto, "begleitgeraete": a.n - a.konten}

        # Saga-API und Live-Kanaele wie die Clients ueber nginx (TLS, X-Forwarded-For je Geraet); --api-direkt: saga-media
        L.api = (Api(f"http://127.0.0.1:{B2.PORT}", f"{B2.NAME}:{B2.NGX}") if a.api_direkt
                 else Api(f"https://127.0.0.1:{B2.NGX}", f"{B2.NAME}:{B2.NGX}"))
        # Konten und Geraete: Bots 0..konten-1 sind Hauptgeraete, die weiteren Begleitgeraete (zweites Geraet je Konto)
        t0 = time.time()
        persons = []
        for j in range(a.konten):
            u = f"{PREFIX}b{j + 1:02d}"
            auth.create_user(u, None, f"Bot {j + 1}")
            persons.append(A_.Person(u))
        for i in range(a.n):
            p_ = persons[i % a.konten]
            if i < a.konten:
                b = Bot(i, p_)
            else:
                b = Bot(i, p_, p_.add_device(), A_.ck(p_.uid, dev=i))
            # eigene Client-Adresse je Geraet (Benchmark-Netz 198.18.0.0/15, RFC 2544) - --eine-adresse: alle hinter
            # EINER Adresse (gemeinsames NAT, Grenze HORN_MAX_PER_IP)
            b.ip = "" if a.eine_adresse else f"198.18.{i // 250}.{i % 250 + 1}"
            L.bots.append(b)
            L.byid[b.id] = b
            L.keys[b.id] = {"uid": b.uid, "enc_pk": b.dev["enc_pk"], "sign_pk": b.dev["sign_pk"]}
        wusers = [f"{PREFIX}w{i + 1}" for i in range(a.browsers)]
        for i, u in enumerate(wusers):
            auth.create_user(u, PW, f"Browser {i + 1}")
        out["setup_s"] = round(time.time() - t0, 1)
        L.api.ips = {b.k: b.ip for b in L.bots if b.ip}
        for b in L.bots:
            L.api.req("PUT", "/api/calls/caps", b.k, {"device": b.id, "caps": CAPS})
        lim = (over or {}).get("tcp", HN.LIMITS["tcp"])[0 if a.video else 1]
        if a.path in ("ws", "tcp") and a.n + a.browsers > lim:
            log(f"Achtung: {a.n} Bots + {a.browsers} Browser > Grenze {lim} (TCP/Tunnel) - die letzten bekommen 'voll'")
        s, d = L.api.req("POST", "/api/chats", L.bots[0].k, {"kind": "free", "members": [p_.uid for p_ in persons[1:]] + wusers})
        if s != 200:
            sys.exit(f"Gruppe: {s} {d}")
        L.gid, L.cid = d["chat"]["id"], secrets.token_hex(16)
        for b in L.bots:
            b.sse = SSE(L.api, b.k, lambda typ, d, b=b: L.on_event(b, typ, d))
            b.sse.start()
        for b in L.bots:
            b.sse.ready.wait(10)
        L.hb_start(node)
        sampler = Sampler(L.pids)
        sampler.start()
        t_join = time.time()
        if not L.join_bot(L.bots[0], start=True):
            sys.exit("Anruf startet nicht: " + json.dumps(L.events["err"])[:800])
        for b in L.bots[1:]:
            time.sleep(a.join_gap)
            L.pool.submit(L.join_bot, b)
        t0 = time.time()
        while time.time() - t0 < 60 and sum(1 for b in L.bots if b.up) + len({e.get("id") for e in L.events["err"]}) < a.n:
            time.sleep(0.3)
        jt = lambda k: {"p50": pctl([getattr(b, k) for b in L.bots], 0.5), "p95": pctl([getattr(b, k) for b in L.bots], 0.95)}  # noqa: E731
        out["join"] = {"all_up_s": round(time.time() - t_join, 1), "up": sum(1 for b in L.bots if b.up),
                       "api_ms": jt("joined_ms"), "node_ms": jt("node_ms"), "total_ms": jt("total_ms"),
                       "companions": sum(1 for b in L.bots if b.in_call and not b.mic)}
        log(f"{out['join']['up']}/{a.n} Bots verbunden in {out['join']['all_up_s']} s")

        if a.browsers:
            spec = {"base": B2.BASE, "users": wusers, "pass": PW, "call": L.cid, "secs": a.secs, "video": a.video > 0,
                    "nodes": [pins.get(u) for u in wusers] if pins else None,
                    "out": os.path.join(L.state, "browser.json"), "prof": os.path.join(L.state, "prof"), "host_rules": f"MAP {B2.NAME} 127.0.0.1"}
            if a.leitung:
                nsl, spec["eng"] = leitung(a, B2, L.state, node)
            env = dict(os.environ, HG_SPEC=json.dumps(spec), NO_PROXY="*")
            for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
                env.pop(k, None)
            cmd = ["node", os.path.join(APP, "tests", "browser_horn_gruppe.js")]
            if a.cpuset:
                cmd = ["taskset", "-c", "12-15"] + cmd
            brw = subprocess.Popen(cmd, env=env, stdout=open(os.path.join(L.state, "browser.log"), "a"), stderr=subprocess.STDOUT)
        threading.Thread(target=L.alive_loop, daemon=True).start()
        threading.Thread(target=L.load_loop, daemon=True).start()
        if a.kommen_gehen:
            threading.Thread(target=L.churn_loop, daemon=True).start()
        t_meas = time.time()
        if brw:
            brw.wait(timeout=a.secs + 600)
        else:
            time.sleep(a.secs)
        L.stop.set()
        L.hb_send(op="stats")
        time.sleep(2)
        sampler.stop_.set()
        sampler.join(3)
        out["secs"] = round(time.time() - t_meas, 1)
        out["cpu"] = sampler.result()
        out["signals"] = {"rows": L.sig_rows(), "sent_by_type": dict(L.sent), "recv_by_type": dict(L.recv),
                          "fkey_requests_sent": getattr(L, "need", 0), "fkey_requests_answered": getattr(L, "nach", 0), "dropped_by_server": getattr(L, "sig_dropped", 0)}
        out["http"] = L.api.stats()
        out["node"] = L.node_load()
        out["node_series"] = getattr(L, "loads", [])
        out["bridge_changes"] = getattr(L, "bridge_changes", 0)
        asw = {nm: (x["load"] or {}).get("aswitch") for nm, x in out["node"].items() if (x["load"] or {}).get("aswitch") is not None}
        if asw:
            out["node_aswitch"] = asw
        out["call"] = L.res.get("call")
        out["bots"] = L.hb_sum
        out["bot_stats"] = sorted(L.hb_stats.values(), key=lambda m: m["id"])
        out["fkey"] = L.events["genwait"]
        out["errors"] = L.events["err"][:50]
        out["node_429"] = sum(1 for e in L.events["err"] if ": 429" in str(e.get("msg")))   # Knoten: zu viele Verbindungen/Beine
        out["leaves"] = len(L.leaves)
        out["rebuilds"] = getattr(L, "rebuilds", 0)
        if brw:
            out["browser_rc"] = brw.returncode
            try:
                out["browsers"] = json.load(open(spec["out"]))
            except (OSError, ValueError):
                out["browsers"] = None
                out["browser_log"] = open(os.path.join(L.state, "browser.log"), errors="replace").read()[-3000:]
        out["latency"] = latency_sum(out)
        errs = len(re.findall(r"Traceback|panic:|DATA RACE", B2.log_text()))
        out["server_errors"] = errs
        if errs:
            out["server_log"] = B2.log_text()[-3000:]
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        L.stop.set()
        if brw and brw.poll() is None:                 # Abbruch/Zeitueberschreitung: Browser-Teil (Playwright schliesst Chromium)
            brw.terminate()
            try:
                brw.wait(15)
            except subprocess.TimeoutExpired:
                brw.kill()
        for b in L.bots:
            if b.sse:
                b.sse.close()
        if L.cid and L.bots:
            try:
                L.api.req("POST", f"/api/calls/{L.cid}/end", L.bots[0].k, {"device": L.bots[0].id, "reason": "hangup"}, retry=False)
            except Exception:  # noqa: BLE001
                pass
        if L.hb and L.hb.poll() is None:
            L.hb_send(op="quit")
            try:
                L.hb.wait(15)
            except subprocess.TimeoutExpired:
                L.hb.kill()
        if nsl:
            nsl[0].terminate()
            subprocess.run(["ip", "netns", "del", nsl[1]], capture_output=True)
        cgroup_del(cg)
        if L.h1.get("proc"):
            B2.stop(L.h1["proc"])
        B2.stop(B2.P.get("proc"))
        if B2.P.get("log"):
            B2.P["log"].close()
        if B2.P.get("ngx"):
            subprocess.run(["nginx", "-c", B2.P["ngx"], "-s", "stop"], capture_output=True)
        L.pool.shutdown(wait=False, cancel_futures=True)
        if not a.keep:
            try:
                for k, v in keep.items():
                    if v is not None:
                        auth.save_setting(k, v)
                auth._settings_cache["v"] = None
                cleanup_accounts()
                from db import connect
                with connect() as c, c:
                    c.execute("DELETE FROM horn_node WHERE builtin=1 OR name='horn-1.example.org'")
            except Exception as ex:  # noqa: BLE001
                log("Aufraeumen:", ex)
            shutil.rmtree(L.state, ignore_errors=True)
        else:
            log("Zustand bleibt:", L.state)
    emit(a, out)


def leitung(a, B2, state, node_bin):
    """M8: Browser 1 in eigenem Netz-Namensraum hinter nsleitung (Rate, Verlust, RTT) - wie tests/anrufe_rang345.py."""
    name = f"hl63n{a.base % 100}"
    # wie anrufe_rang345: ein veth-Paar IM Namensraum mit Adresse - ohne Schnittstelle ausser lo sammelt Chromium keine
    # Kandidaten fuer die lokale Schleife des Rahmen-Wegs (Fehler "frames: schleife")
    addr = f"10.78.{a.base % 100}.2/24"
    subprocess.run(["ip", "netns", "add", name], check=True)
    try:
        return _leitung(a, B2, state, node_bin, name, addr)
    except BaseException:                              # halb aufgebaut: eigenen Namensraum nicht stehen lassen
        subprocess.run(["ip", "netns", "del", name], capture_output=True)
        raise


def _leitung(a, B2, state, node_bin, name, addr):
    for cmd in (["ip", "-n", name, "link", "set", "lo", "up"],
                ["ip", "-n", name, "link", "add", "va", "type", "veth", "peer", "name", "vb"],
                ["ip", "-n", name, "addr", "add", addr, "dev", "va"], ["ip", "-n", name, "link", "set", "va", "up"],
                ["ip", "-n", name, "link", "set", "vb", "up"]):
        subprocess.run(cmd, check=True)
    nb = os.path.join(state, "nsleitung")
    subprocess.run(["go", "build", "-o", nb, "./e2e/nsleitung"], cwd=os.path.join(APP, "gjallarhorn"), check=True,
                   env=dict(os.environ, CGO_ENABLED="0", GOFLAGS="-mod=mod", GOTOOLCHAIN="local"))
    ctl = a.base + 5
    tcp = [str(B2.NGX)] + ([str(a.base + 9)] if a.horn1 else [])
    udp = [str(a.base + 9)] if a.horn1 else []
    cmd = [nb, "-ns", f"/run/netns/{name}", "-tcp", ",".join(tcp), "-ctl", f"127.0.0.1:{ctl}"] + (["-udp", ",".join(udp)] if udp else [])
    p = subprocess.Popen(cmd, stdout=open(os.path.join(state, "nsl.log"), "a"), stderr=subprocess.STDOUT)
    time.sleep(0.7)
    try:
        import urllib.request
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{ctl}/set?{a.leitung}", timeout=5).read()
    except OSError as ex:
        log("nsleitung:", ex)
    real = os.environ.get("BR_EXE") or subprocess.run(["node", "-e", "console.log(require('playwright').chromium.executablePath())"],
                                                     capture_output=True, text=True).stdout.strip()
    exe = os.path.join(state, f"chrome-{name}.sh")
    open(exe, "w").write(f"#!/bin/sh\nexec ip netns exec {name} {real} \"$@\"\n")
    os.chmod(exe, 0o755)
    return (p, name), {"i": 0, "exe": exe, "ctl": f"http://127.0.0.1:{ctl}"}


def latency_sum(out):
    """Zusatz-Latenz der Kaskade (2438): Bild-Laufzeit gleicher Knoten / ueber Bruecke - Bots (Zeitcode im synthetischen
    Bild, ohne Kodierer) und Browser (Zeitcode im Kamerabild, mit Kodierer/Dekodierer/Anzeige); extra = p50 Bruecke -
    p50 gleicher Knoten."""
    def one(same, bridge):
        if not (same or bridge):
            return None
        ok = (same or {}).get("n") and (bridge or {}).get("n")
        return {"same_ms": same, "bridge_ms": bridge, "extra_p50_ms": round(bridge["p50"] - same["p50"], 1) if ok else None}
    bs, br = out.get("bots") or {}, out.get("browsers") or {}
    return {"bots": one(bs.get("lat_same_ms"), bs.get("lat_bridge_ms")),
            "browsers": one(br.get("latency_same_ms"), br.get("latency_bridge_ms"))}


def emit(a, out):
    s = json.dumps(out, indent=1, default=str, ensure_ascii=False)
    if a.out:
        with open(a.out, "w") as f:
            f.write(s)
        log("Ergebnis:", a.out)
    else:
        print(s)


if __name__ == "__main__":
    main()
