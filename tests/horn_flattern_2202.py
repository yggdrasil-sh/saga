"""Bugs-DB 2202 "Flattern unter CPU-Last" - Messreihe mit zwei Knoten, CPU-Last im Browser und netem-Jitter.

Aufbau (Wiederverwendung von tests/b2_frames.py: nginx-NPM-Nachbau -> saga-media -> eingebauter Knoten im Tunnel-Betrieb):
- Knoten 1: eingebauter Knoten im Tunnel-Betrieb (ohne UDP, Browser nur ueber den Rahmen-Weg ws/frames) unter der
  Saga-Adresse https://saga.example.org:<FL_BASE>.
- Knoten 2: gekoppelter Knoten horn-1.example.org (eigener gjallarhorn-Prozess "node", TCP + UDP, Testzertifikat
  HORN_TLS_SELF, Adresse https://10.222.2.1:<port>, siehe horn1_start). Die Kopplung selbst prueft tests/horn_erweitern.py;
  hier wird ihr Ergebnis nachgebaut: Registereintrag mit dem Knotenschluessel, pair.json im Datenverzeichnis des Knotens
  (Steuerkanal ws://127.0.0.1:<saga-media>/api/horn/ctl).
- Browser A und B (Chromium) laufen per "ip netns exec" in einem eigenen Netz-Namensraum (nur IPv4) hinter einem
  veth-Paar mit tc netem (Jitter in beide Richtungen, FL_NETEM, Standard "delay 30ms 50ms") - gleich fuer BEIDE Knoten,
  denn nginx (Saga, Tunnel-Knoten) und horn-1 lauschen auf der Host-Seite des veth (10.222.2.1, nur von hier erreichbar). CPU-Last nur bei A:
  CDP Emulation.setCPUThrottlingRate (FL_CPU_RATE) und/oder cgroup v2 cpu.max fuer den ganzen Browser (FL_CG_MAX, z. B.
  "60000 100000" = 0,6 Kern). Browser B hat denselben Jitter, aber keine CPU-Last (Vergleich).
- Beide starten auf horn-1 (UDP; Browser mit Rahmen-Weg bekommen den Tunnel-Knoten nur als letzten Weg, siehe b2_frames
  teil0). Gemessen: Wechsel wegen "besser" (HKCalls.horn().switches, Knoten/Weg alle 2 s) waehrend FL_SECS unter Last.
- Stand: FL_STAND=fix (Web-Client dieses Stands) bzw. alt (Gegenprobe: static/calls.js und static/horn.js von FL_ALT_REV,
  Standard f0cd30f = vor 62722b1, per Playwright-Route ausgeliefert - beide Dateien unterscheiden sich von main nur im Fix).
  Erwartung fix: kein Hinweg wegen "besser" von horn-1 auf den Tunnel-Knoten (Flattern); alt: Messwert (MESS-Zeile), das
  Flattern tritt dort zufaellig auf (Messreihe 24.11., schwere Last: ohne Fix in 3 von 5 Laeufen, mit Fix in keinem von 5;
  leichte Last ohne Korrelation des Jitters: in keinem).

Schwere Last (wie in der Messreihe; FL_VID ...n = Blockrauschen im Kamerabild, damit der Kodierer an die CPU-Grenze kommt):
  FL_SECS=420 FL_CPU_RATE=4 FL_CG_MAX="25000 100000" FL_NETEM="delay 30ms 50ms 95%" FL_VID=1280x720x30n

Start (root, env.sh, eigene DB):
  FL_BASE=8700 FL_STAND=fix FL_SECS=300 FL_CPU_RATE=6 FL_CG_MAX="60000 100000" python tests/horn_flattern_2202.py
  Daneben ein zweiter Lauf (eigene DB!): FL_BASE=8720 -> Namensraum fl2202b, 10.222.3.0/30.
  Ports FL_BASE: +0 nginx, +1 saga-media, +2 Admin, +7 intern, +8 HORN_PORT (eingebaut, Tunnel), +10 horn-1 (TCP+UDP).
  FL_OUT=<datei.json>: Messwerte; SAGA_MEDIA_BIN / GJALLARHORN_BIN wie b2_frames. Wegwerf-Konten ca-fl*."""
import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

_B = int(os.environ.get("FL_BASE") or 8700)
os.environ["B2_BASE"] = str(_B)
_I = max(0, (_B - 8700) // 20)                         # zweiter Lauf daneben: FL_BASE=8720 -> fl2202b, 10.222.3.0/30
_S = "" if not _I else chr(ord("a") + _I)
NS, VH, VN, HOST_IP, NS_IP = "fl2202" + _S, f"fl2202{_S}h", f"fl2202{_S}n", f"10.222.{2 + _I}.1", f"10.222.{2 + _I}.2"
os.environ["B2_NGX_LISTEN"] = HOST_IP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import b2_frames as B2  # noqa: E402  (setzt HK_TEST_BASE/HK_TEST_ADMIN auf die eigenen Ports)

A_, auth, connect, res, ok = B2.A_, B2.auth, B2.connect, B2.res, B2.ok
APP, STATE, NAME = B2.APP, B2.STATE, B2.NAME
H1_NAME, H1_PORT = "horn-1.example.org", _B + 10
STAND = os.environ.get("FL_STAND") or "fix"
ALT_REV = os.environ.get("FL_ALT_REV") or "f0cd30f"
NETEM = os.environ.get("FL_NETEM") or "delay 30ms 50ms"
CG = "/sys/fs/cgroup/" + NS
PW = "Flattern-2202-Pass-1234"
USERS = [("ca-fl1", "Anna Flattern"), ("ca-fl2", "Bert Flattern")]
H1 = {"proc": None, "log": None}


def sh(cmd, check=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{cmd}: {r.stderr.strip()}")
    return r


def net_up():
    """Netz-Namensraum mit veth und netem in beide Richtungen (nur Adressen 10.222.2.0/30, kein Weg nach draussen)."""
    net_down()
    for c in (f"ip netns add {NS}", f"ip link add {VH} type veth peer name {VN}", f"ip link set {VN} netns {NS}",
              f"ip addr add {HOST_IP}/30 dev {VH}", f"ip link set {VH} up",
              f"ip netns exec {NS} ip addr add {NS_IP}/30 dev {VN}", f"ip netns exec {NS} ip link set {VN} up",
              f"ip netns exec {NS} ip link set lo up",
              # nur IPv4: sonst meldet Chromium fuer die IPv6-Schnittstellen icecandidateerror 701 (STUN an eine IPv4-Adresse),
              # und die UDP-Probe (ohne NAT kein srflx) gilt als gescheitert - der Anruf liefe ueber TCP statt UDP
              f"ip netns exec {NS} sysctl -qw net.ipv6.conf.all.disable_ipv6=1", f"ip netns exec {NS} sysctl -qw net.ipv6.conf.default.disable_ipv6=1"):
        sh(c, check=True)
    if shutil.which("nmcli"):
        sh(f"nmcli device set {VH} managed no")
    if NETEM != "0":
        sh(f"tc qdisc add dev {VH} root netem {NETEM}", check=True)
        sh(f"ip netns exec {NS} tc qdisc add dev {VN} root netem {NETEM}", check=True)
    r = sh(f"ip netns exec {NS} ping -c 5 -i 0.2 -q {HOST_IP}")
    m = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", r.stdout)
    ok(f"Netz-Namensraum {NS} mit netem '{NETEM}' (ping min/avg/max/mdev {m.groups() if m else '?'})", r.returncode == 0, r.stdout[-300:])
    # cgroup v2 fuer den Browser A (cpu.max setzt der Browser-Teil erst, wenn der Anruf steht)
    try:
        os.makedirs(CG, exist_ok=True)
        open(CG + "/cpu.max", "w").write("max 100000")
    except OSError as ex:
        print("cgroup nicht verfuegbar:", ex)


def net_down():
    sh(f"ip netns del {NS}")
    sh(f"ip link del {VH}")
    if os.path.isdir(CG):
        try:
            for pid in open(CG + "/cgroup.procs").read().split():
                try:
                    open("/sys/fs/cgroup/cgroup.procs", "w").write(pid)
                except OSError:
                    pass
            os.rmdir(CG)
        except OSError:
            pass


def horn1_start(node_bin):
    """Gekoppelter Knoten horn-1: Registereintrag + pair.json (Ergebnis der Kopplung), Prozess "node" mit UDP."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from saga import horn as HN
    data = os.path.join(STATE, "horn1")
    os.makedirs(data, mode=0o700, exist_ok=True)
    seed = os.urandom(32)
    sk = Ed25519PrivateKey.from_private_bytes(seed)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    open(os.path.join(data, "node.key"), "w").write(base64.b64encode(seed).decode() + "\n")
    os.chmod(os.path.join(data, "node.key"), 0o600)
    nid = secrets.token_hex(8)
    base = f"http://127.0.0.1:{B2.PORT}"
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (H1_NAME,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, v4, udp, state, created, port) VALUES (?,?,?,0,?,?,1,'neu',?,?)",
                  (nid, H1_NAME, pub, f"https://{H1_NAME}:{H1_PORT}", HOST_IP, time.time(), H1_PORT))
    json.dump({"node_id": nid, "saga_pub": HN.saga_pub(), "ctl_url": f"ws://127.0.0.1:{B2.PORT}/api/horn/ctl", "name": H1_NAME,
               "saga": base, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(os.path.join(data, "pair.json"), "w"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HORN_", "SAGA_", "B2_", "FL_")) and k not in ("PYTHONPATH",)}
    env.update(HORN_SAGA=base, HORN_NAME=H1_NAME, HORN_DATA=data, HORN_LISTEN=f"{HOST_IP}:{H1_PORT}", HORN_UDP="1", HORN_TLS_SELF="1",
               HORN_ICE_IPS=HOST_IP, HORN_PUBLIC_IP=HOST_IP, HORN_PROXY_PROTOCOL="0", HORN_MAX_LEGS="48",
               # Adresse mit IP statt Name: Chromium loest den STUN-Server der UDP-Probe (stun:<host>:<port>) ohne
               # --host-resolver-rules und ohne /etc/hosts auf - mit dem Namen meldet die Probe 701 und der Anruf liefe ueber TCP
               HORN_PUBLIC_URL=f"https://{HOST_IP}:{H1_PORT}")
    H1["log"] = open(os.path.join(STATE, "horn1.log"), "a")
    H1["proc"] = subprocess.Popen([node_bin, "node"], env=env, stdout=H1["log"], stderr=subprocess.STDOUT)
    t0 = time.time()
    n = None
    while time.time() - t0 < 30:
        with connect() as c:
            n = HN.node_named(c, H1_NAME)
        if n and n.get("state") == "ok" and (n.get("cert") or {}).get("state") == "self":
            break
        time.sleep(0.5)
    return n


def h1_log():
    try:
        return open(os.path.join(STATE, "horn1.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def alt_files():
    d = os.path.join(STATE, "alt")
    os.makedirs(d, exist_ok=True)
    for f in ("calls.js", "horn.js"):
        r = subprocess.run(["git", "-C", APP, "show", f"{ALT_REV}:static/{f}"], capture_output=True)
        if r.returncode:
            return None
        open(os.path.join(d, f), "wb").write(r.stdout)
    return d


def main():
    import db
    db.wait_ready()
    db.init_schema()
    if os.geteuid() != 0:
        ok("laeuft als root (Netz-Namensraum, netem, cgroup)", False)
        sys.exit(1)
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    auth.save_setting("calls", dict(B2.CALLS, horn=True, horn_builtin=True, horn_all=True, horn_all_y=True))
    auth._settings_cache["v"] = None
    for u, nm in USERS:
        auth.create_user(u, PW, nm)
    media, node = B2.gobuild("saga-media", "media", "SAGA_MEDIA_BIN"), B2.gobuild("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok("saga-media, gjallarhorn gebaut", bool(media and node))
    alt = alt_files() if STAND == "alt" else None
    if STAND == "alt":
        ok(f"Gegenprobe: calls.js/horn.js von {ALT_REV}", bool(alt) and "curRtts" not in open(os.path.join(alt, "horn.js")).read())
    out = os.environ.get("FL_OUT") or os.path.join(APP, ".st", f"flattern-{STAND}-{time.strftime('%H%M%S')}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    rc = 1
    try:
        net_up()
        ngx = B2.nginx_conf()
        r = subprocess.run(["nginx", "-c", ngx], capture_output=True, text=True)
        B2.P["ngx"] = ngx if r.returncode == 0 else None
        ok("nginx (NPM-Nachbau) auf 127.0.0.1 und der veth-Adresse", B2.P["ngx"], r.stderr[-400:])
        if not (media and node and B2.P["ngx"]) or (STAND == "alt" and not alt):
            return
        t = B2.start(media, node)
        ok(f"Instanz im Tunnel-Betrieb ({B2.BASE}) laeuft", t is not None, B2.log_text()[-2000:])
        if t is None:
            return
        t0 = time.time()
        while time.time() - t0 < 30 and "tunnel-betrieb: kein eigener tcp-port" not in B2.log_text():
            time.sleep(0.3)
        ok("eingebauter Knoten im Tunnel-Betrieb", "tunnel-betrieb: kein eigener tcp-port" in B2.log_text(), B2.log_text()[-1500:])
        n = horn1_start(node)
        from saga import horn as HN
        ok(f"gekoppelter Knoten {H1_NAME} verbunden und bereit (UDP {n and n.get('udp')}, Zertifikat {n and (n.get('cert') or {}).get('state')})",
           bool(n) and n.get("state") == "ok" and n.get("udp") and HN.ready(n), {"n": n, "log": h1_log()[-1500:]})
        rules = f"MAP {NAME} {HOST_IP}, MAP {H1_NAME} {HOST_IP}"
        env = dict(os.environ, SAGA_BASE=B2.BASE, SAGA_USERS=",".join(u for u, _ in USERS), SAGA_PASS=PW, HOST_RULES=rules, NO_PROXY="*",
                   FL_NS=NS, FL_CG=CG if os.path.isdir(CG) else "", FL_OUT=out, FL_H1=H1_NAME, FL_ALT_DIR=alt or "")
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            env.pop(k, None)
        js = os.path.join(APP, "tests", "browser_horn_flattern_2202.js")
        secs = int(os.environ.get("FL_SECS") or 300)
        r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=secs + 900)
        for ln in r.stdout.splitlines():
            print("  [browser] " + ln, flush=True)
            if ln.startswith(("OK  ", "FAIL")):
                res.append(ln.startswith("OK"))
        ok("Browser-Teil beendet ohne Fehler", r.returncode == 0, (r.stdout[-800:] + r.stderr[-1500:]))
        try:
            m = json.load(open(out))
        except (OSError, ValueError):
            m = None
        if m:
            a = m["A"]
            # Flattern = Hinweg wegen "besser" von horn-1 auf den Tunnel-Knoten (Weg ws, Rahmen); der Rueckweg auf UDP
            # (Bugs-DB 2201) ist gewollt und zaehlt getrennt
            hin = lambda x: [s for s in x["better_load"] if s.get("path") == "ws"]  # noqa: E731
            nb = len(hin(a))
            line = (f"Stand {STAND}: A unter Last {m['secs']} s (CPU-Drossel {m['cpu_rate'] or '-'}x, cpu.max {m['cg_max'] or '-'}, netem "
                    f"'{NETEM}'): {nb} Hinweg(e) UDP->Tunnel ({' '.join(s['from'][:6] + '>' + s['to'][:6] for s in a['better_load']) or '-'}), "
                    f"Knotenzeit {a['share']}, CPU-Grenze in {a['cpu_samples']}/{a['out_samples']} Sende-Proben, cgroup {m.get('cg') or '-'}; "
                    f"alle 'besser' A {len(a['better_load'])}, B {len(m['B']['better_load'])} (Hinweg {len(hin(m['B']))})")
            if STAND == "alt":                         # Flattern ist zufaellig (nicht in jedem Lauf) - nur Messwert
                print(f"MESS Gegenprobe ohne Fix ({'Flattern' if nb else 'kein Flattern in diesem Lauf'}) - " + line, flush=True)
            else:
                ok("mit Fix: kein Flattern (Hinweg UDP -> Tunnel wegen 'besser') - " + line, nb == 0 and not hin(m["B"]))
        print("   Beine horn-1:", len(re.findall(r"bein auf", h1_log())), "| Beine Tunnel-Knoten:", len(re.findall(r"bein auf", B2.log_text())))
    except Exception:
        import traceback
        traceback.print_exc()
        ok("Reihe ohne Ausnahme", False)
    finally:
        B2.stop(H1["proc"])
        if H1["log"]:
            H1["log"].close()
        B2.stop(B2.P["proc"])
        if B2.P["log"]:
            B2.P["log"].close()
        if B2.P["ngx"]:
            subprocess.run(["nginx", "-c", B2.P["ngx"], "-s", "stop"], capture_output=True)
        net_down()
        errs = len(re.findall(r"Traceback|panic:|DATA RACE", B2.log_text() + h1_log()))
        ok(f"Instanz und horn-1: Serverfehler {errs}", errs == 0)
        if errs:
            print(B2.log_text()[-4000:], h1_log()[-3000:])
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        A_.cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE builtin=1 OR name=?", (H1_NAME,))
        if os.environ.get("FL_KEEP"):
            print("Zustand bleibt:", STATE)
        else:
            shutil.rmtree(STATE, ignore_errors=True)
        rc = 0 if all(res) else 1
        print(f"\nHORN-FLATTERN-2202 ({STAND}) {sum(res)}/{len(res)}", "- ALLES GRUEN" if not rc else "- FEHLER")
    sys.exit(rc)


if __name__ == "__main__":
    main()
