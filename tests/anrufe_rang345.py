"""Messreihe Anruf-Rang 3-5 (Saga 2.5, Stufe 1 im Container): 1:1 und Gruppen bis 4 ueber den eingebauten Knoten
(Grundsatz Y) und ohne Horn (Mesh), mit einer engen Zugangsleitung je Geraet. Treiber fuer tests/browser_rang345.js.

Start (eigene Instanz, eigene Datenbank, Ports frei waehlbar):
    source <env mit DATABASE_URL, SESSION_SECRET, STATE_DIR>;  R345_PORT=8881 R345_HORN=18881 \\
    PYTHONPATH=tests/pgshim:. python tests/anrufe_rang345.py [--cases r3-frei,r5-4-eng] [--reps 3] [--list] [--sum]
    ... --check   Rang 4: zwei Laeufe (r4-voll, r5-voll) und Pruefung der Texte bei vollem Anruf -> ALLES GRUEN / FEHLER
Braucht: Go-Binaries gjallarhorn + saga-media (R345_BIN, Standard /tmp/r345/bin; CGO_ENABLED=0 -trimpath), nsleitung
(gjallarhorn/e2e/nsleitung), iproute2 (ip netns), coturn (turnserver, nur fuer Mesh mit enger Leitung), node + Playwright
(Chromium). Laeuft als root (Netz-Namensraum). Jeder Lauf schreibt sofort R345_RES/<fall>-<n>.json; vorhandene Laeufe
werden uebersprungen (neustartfest). Schwere Laeufe nehmen die Sperre /tmp/ws-mess.lock (flock), wenn vorhanden.

Aufbau: saga-media -> run.py (PORT) mit eingebautem Knoten (HORN_PORT, UDP an, ICE 127.0.0.1). Ein "enger" Browser laeuft
per "ip netns exec" in einem eigenen Namensraum ohne Weg nach draussen; nsleitung reicht dort Saga (TCP), den Knoten (TCP +
UDP) und TURN (UDP) mit Rate/RTT/Verlust an den Host weiter - eine Leitung je Richtung fuer alles, was das Geraet sendet.
Faelle (FAELLE): R3 1:1 ueber Knoten (frei, 1 Mbit/s, 3 % Verlust, RTT 150 ms, Netzwechsel, Langlauf), R4 3 und 4
Personen ueber Knoten (calls.horn_from 3; frei, einer eng, Geraetewechsel, Zweitgeraet teilt, voll mit horn_video 4),
R5 ohne Horn (calls.horn aus = Mesh; 2/3/4 frei und einer eng, Geraetewechsel, Teilen, voll).
Kennzahlen je Empfaenger: Aufbau Bild/Ton, Bild-Latenz (Zeitcode im Bild), fps, Einfrierer (freezeCount), Schichtwechsel
(Bildhoehe), Tonluecken (verdeckte Samples), Weg, Rueckfaelle, CPU je Browser. --sum gibt die Tabelle (Median, Spanne, n).
Wegwerf-Konten r345-*."""
import argparse
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
PORT = int(os.environ.get("R345_PORT", "8881"))
INTERNAL = PORT + 1
HORN_PORT = int(os.environ.get("R345_HORN", "18881"))
TURN_PORT = HORN_PORT + 2
CTL0 = HORN_PORT + 10                     # Steuerung der Leitungen (je Namensraum +i)
BIN = os.environ.get("R345_BIN", "/tmp/r345/bin")
WORK = os.environ.get("R345_WORK", "/tmp/r345")
RES = os.environ.get("R345_RES", os.path.join(WORK, "res"))
STATE = os.environ.get("STATE_DIR", os.path.join(WORK, "state"))
PW = "Rang345-Mess-Pass-2468"
USERS = [("r345-a", "Alma Mess"), ("r345-b", "Bodo Mess"), ("r345-c", "Cleo Mess"), ("r345-d", "Dina Mess"), ("r345-e", "Emil Mess")]
TURN_SECRET = "r345-nur-test-turn"
CHROME = os.environ.get("R345_CHROME", "")
P = {"proc": None, "log": None, "turn": None, "nsl": {}}

CALLS = {"enabled": True, "stun": "", "turn_urls": f"turn:127.0.0.1:{TURN_PORT}?transport=udp", "turn_mode": "rest",
         "turn_secret": TURN_SECRET, "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
HORN = dict(horn=True, horn_builtin=True, horn_all=True, horn_all_y=True, horn_from=3)
HORN_Y = dict(horn=True, horn_builtin=True)          # ab Werk mit Gjallarhorn: horn_all ohne Merker = an, ohne horn_from (L1)
MESH = dict(horn=False)

ENG1 = {"up": 1000, "down": 1000, "rtt": 40, "loss": 0, "qms": 300}       # "eng": 1 Mbit/s je Richtung
FREI = {"up": 0, "down": 0, "rtt": 0, "loss": 0, "qms": 300}


def F(fid, kind, n, cfg, eng=None, shape=None, event=None, secs=45, note="", media=None):
    return {"id": fid, "kind": kind, "n": n, "cfg": cfg, "eng": eng, "shape": shape or FREI, "event": event, "secs": secs, "note": note,
            "media": media}


HD = {"w": 1280, "h": 720, "fps": 24}     # Kamera 720p: will mehr als 1 Mbit/s (Standard 480x270 @ 20 fps bleibt darunter)


# eng: Index der Person mit eigener Leitung (Namensraum). Im 1:1 laeuft A immer ueber die Leitung (frei = ohne Grenzen),
# damit sich nur die Leitungswerte aendern.
FAELLE = [
    F("r3-frei", "dm", 2, HORN, 0),
    F("r3-1mbit", "dm", 2, HORN, 0, ENG1),
    F("r3-verlust3", "dm", 2, HORN, 0, {"up": 0, "down": 0, "rtt": 40, "loss": 0.03, "qms": 300}),
    F("r3-rtt150", "dm", 2, HORN, 0, {"up": 0, "down": 0, "rtt": 150, "loss": 0, "qms": 300}),
    F("r3-netzwechsel", "dm", 2, HORN, 0, {"up": 0, "down": 0, "rtt": 30, "loss": 0, "qms": 300}, {"type": "flap", "at": 15, "ms": 3000}, 50),
    F("r3-lang", "dm", 2, HORN, 0, {"up": 0, "down": 0, "rtt": 30, "loss": 0, "qms": 300}, None, 600, "10 min"),
    F("r3-frei-720", "dm", 2, HORN, 0, None, None, 45, "720p", HD),
    F("r3-1mbit-720", "dm", 2, HORN, 0, ENG1, None, 45, "720p", HD),
    F("r3-verlust3-720", "dm", 2, HORN, 0, {"up": 0, "down": 0, "rtt": 40, "loss": 0.03, "qms": 300}, None, 45, "720p", HD),
    F("r5-2-1mbit-720", "dm", 2, MESH, 1, ENG1, None, 45, "720p", HD),
    F("r4-3-frei", "group", 3, HORN),
    F("r4-4-frei", "group", 4, HORN),
    F("r4-4-eng", "group", 4, HORN, 3, ENG1),
    F("r4-wechsel", "group", 4, HORN, None, None, {"type": "handover", "at": 15, "who": 3}, 50),
    F("r4-teilen", "group", 3, HORN, None, None, {"type": "share", "at": 12, "who": 2}, 45),
    F("r4-voll", "group", 4, dict(HORN, horn_video=4), None, None, {"type": "full", "at": 10, "who": 3}, 35, "horn_video 4"),
    F("r4y-3-frei", "group", 3, HORN_Y, None, None, None, 45, "L1 ohne horn_from"),
    F("r4y-4-frei", "group", 4, HORN_Y, None, None, None, 45, "L1 ohne horn_from"),
    F("r4y-4-eng", "group", 4, HORN_Y, 3, ENG1, None, 45, "L1 ohne horn_from"),
    # Eingrenzung Bildluecke beim engen Teilnehmer (r4-4-eng): gleiche Leitung, Warteschlange 300 ms bzw. 60 ms, und 2 Mbit/s
    F("r4d-4-eng", "group", 4, HORN_Y, 3, ENG1, None, 60, "Diagnose q300"),
    F("r4d-4-eng-q60", "group", 4, HORN_Y, 3, dict(ENG1, qms=60), None, 60, "Diagnose q60"),
    F("r4d-4-eng-2m", "group", 4, HORN_Y, 3, dict(ENG1, up=2000, down=2000), None, 60, "Diagnose 2 Mbit/s"),
    F("r4d-4-eng-2m-klein", "group", 4, HORN_Y, 3, dict(ENG1, up=2000, down=2000), None, 60, "Diagnose 2 Mbit/s, wenig CPU",
      {"w": 320, "h": 180, "fps": 12}),
    F("r4d-4-frei-klein", "group", 4, HORN_Y, None, None, None, 60, "Diagnose frei, wenig CPU", {"w": 320, "h": 180, "fps": 12}),
    F("r5-2-frei", "dm", 2, MESH),
    F("r5-3-frei", "group", 3, MESH),
    F("r5-4-frei", "group", 4, MESH),
    F("r5-2-eng", "dm", 2, MESH, 1, ENG1),
    F("r5-3-eng", "group", 3, MESH, 2, ENG1),
    F("r5-4-eng", "group", 4, MESH, 3, ENG1),
    F("r5-wechsel", "group", 4, MESH, None, None, {"type": "handover", "at": 15, "who": 3}, 50),
    F("r5-teilen", "group", 3, MESH, None, None, {"type": "share", "at": 12, "who": 2}, 45),
    F("r5-voll", "group", 4, MESH, None, None, {"type": "full", "at": 10, "who": 3}, 35),
]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status
    except Exception:
        return None


# --- Instanz -----------------------------------------------------------------------------------------------------------
def start():
    if P["proc"] and P["proc"].poll() is None:
        return True
    os.makedirs(STATE, exist_ok=True)
    keep = ("PATH", "HOME", "DATABASE_URL", "SECRET_KEY", "SESSION_SECRET", "LANG", "TZ", "POSTGRES_HOST")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT="0", HOST="127.0.0.1", STATE_DIR=STATE,
               BASE_URL=f"http://127.0.0.1:{PORT}", CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1", SAGA_UPDATE_CHECK="0",
               PYTHONPATH=APP, GJALLARHORN_BIN=os.path.join(BIN, "gjallarhorn"), HORN_PORT=str(HORN_PORT), HORN_NAME="localhost",
               HORN_TLS_SELF="1", HORN_UDP="1", HORN_ICE_IPS="127.0.0.1", HORN_MAX_LEGS="48")
    P["log"] = open(os.path.join(STATE, "server.log"), "a")
    P["proc"] = subprocess.Popen([os.path.join(BIN, "saga-media"), "--", sys.executable, "run.py"], env=env, cwd=APP,
                                 stdout=P["log"], stderr=subprocess.STDOUT)
    open(os.path.join(WORK, "instanz.pid"), "w").write(str(P["proc"].pid))
    t0 = time.time()
    while time.time() - t0 < 60:
        if http_ok(f"http://127.0.0.1:{PORT}/healthz") == 200:
            log(f"Instanz laeuft ({round(time.time() - t0, 1)} s, PID {P['proc'].pid})")
            return True
        time.sleep(0.3)
    log("Instanz startet nicht")
    return False


def stop():
    p = P["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
    for x in list(P["nsl"].values()) + [P["turn"]]:
        if x and x.poll() is None:
            x.terminate()


def ns_clean():
    """Zum Schluss: die Netz-Namensraeume dieses Laufs wieder loeschen (stop() beendet nur die Prozesse)."""
    stop()
    for name in list(P["nsl"]):
        x = P["nsl"].pop(name)
        try:
            x.wait(5)
        except subprocess.TimeoutExpired:
            x.kill()
        subprocess.run(["ip", "netns", "del", name], capture_output=True)


def node_ready(after=0):
    from db import connect
    with connect() as c:
        r = c.execute("SELECT state, seen, udp FROM horn_node WHERE builtin=1 AND removed IS NULL").fetchone()
    return bool(r and r[0] == "ok" and (r[1] or 0) >= after)


def settings(cfg):
    import auth
    cur = dict(CALLS, **cfg)
    auth.save_setting("calls", cur)
    auth._settings_cache["v"] = None
    if cfg.get("horn"):
        t0 = time.time()
        while time.time() - t0 < 40 and not node_ready(t0 - 30):
            time.sleep(0.5)
        if not node_ready():
            log("WARNUNG: Knoten nicht bereit")
            return False
        time.sleep(2)
    else:
        time.sleep(3)                              # Clients lesen /api/calls/config beim Laden der Seite
    return True


def users():
    import auth
    import db
    db.wait_ready()
    db.init_schema()
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    for u, n in USERS:
        if not auth.get_user(u):
            auth.create_user(u, PW, n)


# --- TURN und Leitungen ------------------------------------------------------------------------------------------------
def turn():
    if P["turn"] and P["turn"].poll() is None:
        return
    exe = shutil.which("turnserver")
    if not exe:
        log("WARNUNG: kein turnserver (coturn) - Mesh mit enger Leitung geht nicht")
        return
    lf = open(os.path.join(WORK, "turn.log"), "a")
    P["turn"] = subprocess.Popen([exe, "-n", "--listening-ip=127.0.0.1", "--relay-ip=127.0.0.1", f"--listening-port={TURN_PORT}",
                                  "--min-port=41000", "--max-port=41999", "--use-auth-secret", f"--static-auth-secret={TURN_SECRET}",
                                  "--realm=r345", "--no-tls", "--no-dtls", "--no-cli", "--allow-loopback-peers", "--no-multicast-peers",
                                  "--log-file=stdout", "--simple-log", f"--pidfile={WORK}/turn.pid"], stdout=lf, stderr=subprocess.STDOUT)
    time.sleep(1)


def netns(i):
    name, addr = f"r345e{i + 1}", f"10.77.{i + 1}.2"
    have = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True).stdout.split()
    if name not in have:
        for cmd in (["ip", "netns", "add", name], ["ip", "-n", name, "link", "set", "lo", "up"],
                    ["ip", "-n", name, "link", "add", "va", "type", "veth", "peer", "name", "vb"],
                    ["ip", "-n", name, "addr", "add", addr + "/24", "dev", "va"], ["ip", "-n", name, "link", "set", "va", "up"],
                    ["ip", "-n", name, "link", "set", "vb", "up"]):
            subprocess.run(cmd, check=True)
    ctl = CTL0 + i
    x = P["nsl"].get(name)
    if not (x and x.poll() is None):
        lf = open(os.path.join(WORK, f"nsl-{name}.log"), "a")
        P["nsl"][name] = subprocess.Popen([os.path.join(BIN, "nsleitung"), "-ns", f"/run/netns/{name}", "-tcp", f"{PORT},{HORN_PORT}",
                                           "-udp", f"{HORN_PORT},{TURN_PORT}", "-ctl", f"127.0.0.1:{ctl}"], stdout=lf, stderr=subprocess.STDOUT)
        time.sleep(0.5)
    exe = os.path.join(WORK, f"chrome-{name}.sh")
    real = CHROME or subprocess.run(["node", "-e", "console.log(require('playwright').chromium.executablePath())"],
                                    capture_output=True, text=True, env=os.environ).stdout.strip()
    with open(exe, "w") as f:
        f.write(f"#!/bin/sh\nexec ip netns exec {name} {real} \"$@\"\n")
    os.chmod(exe, 0o755)
    return {"ns": name, "ctl": f"http://127.0.0.1:{ctl}", "exe": exe}


def shape(ns, s):
    q = "&".join(f"{k}={v}" for k, v in s.items())
    return http_ok(f"{ns['ctl']}/set?{q}") == 200


# --- Laeufe ------------------------------------------------------------------------------------------------------------
def run_case(fc, rep):
    out = os.path.join(RES, f"{fc['id']}-{rep}.json")
    if os.path.exists(out):
        return "da"
    # Saga begrenzt neue Anrufe je Konto auf CALL_RATE_H (40) je Stunde, im Speicher des Prozesses - die Anrufende ist
    # fast immer dieselbe Person: nach 25 Laeufen die Instanz neu starten
    P["runs"] = P.get("runs", 0) + 1
    if P["runs"] > 25:
        stop()
        P["runs"] = 1
    if not start():
        return "instanz"
    if not settings(fc["cfg"]):
        return "knoten"
    turn()
    spec = {"id": fc["id"], "rep": rep, "kind": fc["kind"], "n": fc["n"], "secs": fc["secs"], "event": fc["event"], "out": out,
            "users": [u for u, _ in USERS], "pass": PW, "base": f"http://127.0.0.1:{PORT}", "prof": os.path.join(WORK, "prof"),
            "horn": bool(fc["cfg"].get("horn")), "shape": fc["shape"], "note": fc["note"], "media": fc.get("media")}
    if fc["eng"] is not None:
        ns = netns(fc["eng"])
        if not shape(ns, fc["shape"]):
            return "leitung"
        spec["eng"] = dict(ns, i=fc["eng"])
    env = dict(os.environ, R345_SPEC=json.dumps(spec), PLAYWRIGHT_BROWSERS_PATH=os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        env.pop(k, None)
    cmd = ["node", os.path.join(APP, "tests", "browser_rang345.js")]
    if os.path.exists("/tmp/ws-mess.lock") or os.access("/tmp", os.W_OK):
        cmd = ["flock", "/tmp/ws-mess.lock"] + cmd
    t0 = time.time()
    lf = os.path.join(WORK, "logs", f"{fc['id']}-{rep}.log")
    os.makedirs(os.path.dirname(lf), exist_ok=True)
    with open(lf, "w") as f:
        r = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT, timeout=fc["secs"] + 420)
    if fc["eng"] is not None:
        shape(netns(fc["eng"]), FREI)
    ok = os.path.exists(out)
    log(f"{fc['id']}-{rep}: rc {r.returncode}, {round(time.time() - t0)} s, {'Ergebnis' if ok else 'OHNE Ergebnis'} ({lf})")
    return "ok" if ok else "fehler"


# --- Auswertung --------------------------------------------------------------------------------------------------------
KEYS = [("setup_v", "Aufbau Bild ms"), ("setup_a", "Aufbau Ton ms"), ("lag", "Latenz ms"), ("fps", "fps"), ("freeze", "Einfrierer/min"),
        ("freeze_s", "Standbild s/min"), ("layer", "Schichtwechsel/min"), ("conceal", "Ton verdeckt %"), ("gaps", "Tonluecken/min"),
        ("vgap", "Bildluecke max ms"), ("cpu", "CPU % je Browser"), ("sw", "Knotenwechsel"), ("fall", "Rueckfall direkt"),
        ("recover", "Erholung ms")]


def agg(vals):
    v = [x for x in vals if isinstance(x, (int, float))]
    if not v:
        return "-"
    m = statistics.median(v)
    f = (lambda x: str(round(x))) if max(abs(x) for x in v) >= 10 else (lambda x: str(round(x, 1)))
    return f"{f(m)} ({f(min(v))}–{f(max(v))}, n={len(v)})"


def summary():
    rows = {}
    for fpath in sorted(glob.glob(os.path.join(RES, "*.json"))):
        try:
            d = json.load(open(fpath))
        except ValueError:
            continue
        s = dict(d.get("sum") or {})
        ev = (d.get("spec") or {}).get("event") or {}
        if ev.get("type") == "handover":           # Bildluecke nur bei den anderen (altes Geraet endet, neues wartet erst)
            who = "ABCDE"[ev.get("who", 3)]
            s["vgap"] = max([v.get("vgap") or 0 for k, v in d["pages"].items() if not k.startswith(who)] or [0])
        r = rows.setdefault(d["id"], {k: [] for k, _ in KEYS})
        r.setdefault("_runs", 0)
        r["_runs"] += 1
        r.setdefault("_path", set()).add(str(s.get("path")))
        for k, _ in KEYS:
            if s.get(k) is not None:
                r[k].append(s[k])
    order = [fc["id"] for fc in FAELLE]
    print("| Fall | Läufe | Weg | " + " | ".join(n for _, n in KEYS) + " |")
    print("|" + "---|" * (len(KEYS) + 3))
    for fid in sorted(rows, key=lambda x: order.index(x) if x in order else 99):
        r = rows[fid]
        print(f"| {fid} | {r['_runs']} | {','.join(sorted(r['_path']))} | " + " | ".join(agg(r[k]) for k, _ in KEYS) + " |")


FULL_TXT = "alle 4 Plätze sind belegt (jedes Gerät zählt, auch ein zweites zum Teilen)"


def check():
    """--check: Rang 4 "volle Plaetze verstaendlich melden" - r4-voll (Knoten, horn_video 4) und r5-voll (Mesh) einmal
    laufen lassen und die Texte der Web-Anzeige pruefen. -> ALLES GRUEN / FEHLER"""
    global RES
    RES = os.path.join(WORK, "check")
    shutil.rmtree(RES, ignore_errors=True)
    os.makedirs(RES)
    users()
    res = []

    def ok(n, c, info=None):
        res.append(bool(c))
        print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, ensure_ascii=False)[:900]), flush=True)
    try:
        for fid in ("r5-voll", "r4-voll"):
            fc = next(x for x in FAELLE if x["id"] == fid)
            st = run_case(fc, 1)
            ok(f"{fid}: Lauf mit Ergebnis ({st})", st == "ok")
            if st != "ok":
                continue
            d = json.load(open(os.path.join(RES, f"{fid}-1.json")))
            tx, seats = d.get("texts") or {}, (d.get("event") or {}).get("seats") or {}
            ok(f"{fid}: 4 von 4 Plaetzen belegt", seats.get("legs") == 4 and seats.get("limit") == 4, seats)
            ok(f"{fid}: 5. Person - Leiste nennt den Grund (jedes Geraet zaehlt), kein Beitreten-Knopf",
               FULL_TXT in (tx.get("extra_bar") or "") and not tx.get("extra_bar_join"), tx)
            ja = tx.get("join_api") or {}
            ok(f"{fid}: Beitritt ueber die Schnittstelle 409 full mit verstaendlichem Text", ja.get("status") == 409 and ja.get("block") == "full"
               and FULL_TXT in (ja.get("error") or ""), ja)
            ok(f"{fid}: zweites Geraet - Leiste: weiteres eigenes Geraet erst, wenn jemand auflegt; Uebernehmen geht",
               "ein weiteres eigenes Gerät" in (tx.get("second_bar") or "") and "Hierher übernehmen" in (tx.get("second_bar") or ""), tx.get("second_bar"))
            rg = tx.get("ring_again") or []
            ok(f"{fid}: Erneut klingeln bei vollem Anruf gesperrt (mit Grund)", rg and all(x["disabled"] and "voll" in x["title"] for x in rg), rg)
            ok(f"{fid}: Person hinzufuegen meldet voll", "Der Anruf ist voll" in (tx.get("add_hint") or ""), tx.get("add_hint"))
            if fid == "r5-voll":
                ok("r5-voll: ohne Knoten Hinweis beim Start (Chat hat mehr Mitglieder als Plaetze)", "höchstens 4 Geräte möglich" in (tx.get("start_toast") or ""), tx.get("start_toast"))
            else:
                ok("r4-voll: ueber Knoten kein Mesh-Hinweis beim Start", "höchstens 4 Geräte möglich" not in (tx.get("start_toast") or ""), tx.get("start_toast"))
    finally:
        ns_clean()
    errs = open(os.path.join(STATE, "server.log"), errors="replace").read().count("Traceback")
    ok(f"Serverfehler: {errs}", errs == 0)
    print(f"\nRANG4-VOLL {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="Rang 4: Texte bei vollem Anruf pruefen (2 Laeufe)")
    ap.add_argument("--cases", default="")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sum", action="store_true")
    ap.add_argument("--keep", action="store_true", help="Instanz nach dem Lauf weiterlaufen lassen")
    a = ap.parse_args()
    if a.list:
        for fc in FAELLE:
            print(fc["id"], fc["kind"], fc["n"], "horn" if fc["cfg"].get("horn") else "mesh", fc["shape"], fc["event"], fc["secs"], fc["note"])
        return
    if a.sum:
        summary()
        return
    if a.check:
        check()
    os.makedirs(RES, exist_ok=True)
    want = [x for x in a.cases.split(",") if x]
    users()
    try:
        for fc in FAELLE:
            if want and fc["id"] not in want:
                continue
            reps = 1 if fc["id"] == "r3-lang" and not want else a.reps
            for rep in range(1, reps + 1):
                st = run_case(fc, rep)
                if st not in ("ok", "da"):
                    log(f"{fc['id']}-{rep}: {st}")
    finally:
        if not a.keep:
            ns_clean()
    summary()


if __name__ == "__main__":
    main()
