#!/usr/bin/env python3
"""Saga-Testlabor auf Hetzner Cloud (stuendlich abgerechnet) mit DNS bei Cloudflare.

    python3 testlab.py plan        Servertypen und Preise je Stunde anzeigen (legt nichts an)
    python3 testlab.py up          Server A, B, C anlegen, DNS setzen, auf cloud-init warten
    python3 testlab.py koppeln     Schalter auf A und B setzen, externen Knoten C an B koppeln (geteilt)
    python3 testlab.py foederieren Foederation A <-> B ohne Admin-Oberflaeche (Code nur von Prozess zu Prozess)
    python3 testlab.py dm-test     Browser-Test 1:1 ueber Knoten A <-> B auf C (Wegwerf-Konten c1/c2)
    python3 testlab.py status      Server mit dem Label und ihren Zustand zeigen
    python3 testlab.py ssh <a|b|c> [befehl]
    python3 testlab.py down        ALLES mit dem Label loeschen (Server, SSH-Schluessel, DNS) und den Rest auflisten

Umgebung (nichts davon steht im Repo):
    HCLOUD_TOKEN       API-Token des Hetzner-Projekts (nur das Testprojekt!)
    CF_API_TOKEN       Cloudflare-Token, nur DNS-Bearbeitung der Test-Zone
    TESTLAB_DOMAIN     Test-Zone, z. B. example-test.org -> a., b., horn-a., horn-b.
    TESTLAB_SSH_KEY    oeffentlicher Test-Schluessel (Standard ~/.ssh/saga-test.pub, privat ohne .pub)
    TESTLAB_LOCATION   Standard fsn1    TESTLAB_CHANNEL  Standard testing
    TESTLAB_TYPES      Standard a=cax11,b=cax11,c=cax31
Alles, was das Skript anlegt, traegt das Label saga-test (DNS: Kommentar saga-test).
"""
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LABEL = "saga-test"
HERE = Path(__file__).resolve().parent
ROLES = ("a", "b", "c")
DNS = {"a": ("a", "horn-a"), "b": ("b",), "c": ("horn-b",)}


def env(k, default=None):
    v = os.environ.get(k, default)
    if v is None:
        sys.exit(f"{k} fehlt (siehe Kopf von testlab.py)")
    return v


def api(base, token, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: HTTP {e.code} {e.read()[:300].decode(errors='replace')}")


def hz(method, path, body=None):
    return api("https://api.hetzner.cloud/v1", env("HCLOUD_TOKEN"), method, path, body)


def cf(method, path, body=None):
    return api("https://api.cloudflare.com/client/v4", env("CF_API_TOKEN"), method, path, body)


def zone_id():
    r = cf("GET", f"/zones?name={env('TESTLAB_DOMAIN')}")["result"]
    if not r:
        sys.exit("Test-Zone bei Cloudflare nicht gefunden")
    return r[0]["id"]


def types():
    out = dict(a="cax11", b="cax11", c="cax31")
    for part in os.environ.get("TESTLAB_TYPES", "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def key_paths():
    pub = Path(os.path.expanduser(os.environ.get("TESTLAB_SSH_KEY", "~/.ssh/saga-test.pub")))
    if not pub.exists():
        sys.exit(f"{pub} fehlt - einmalig: ssh-keygen -t ed25519 -N '' -C saga-test -f {pub.with_suffix('')}")
    return pub, pub.with_suffix(""), pub.parent / "saga-test_known_hosts"


def servers():
    return {s["labels"].get("rolle"): s for s in hz("GET", f"/servers?label_selector={LABEL}")["servers"]}


def ssh(role, *cmd, inp=None, check=True):
    s = servers().get(role) or sys.exit(f"Server {role} gibt es nicht")
    _, priv, kh = key_paths()
    args = ["ssh", "-i", str(priv), "-o", f"UserKnownHostsFile={kh}", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", f"root@{s['public_net']['ipv4']['ip']}", *cmd]
    return subprocess.run(args, input=inp, capture_output=inp is not None or bool(cmd), check=check)


def cmd_plan():
    loc = os.environ.get("TESTLAB_LOCATION", "fsn1")
    st = {t["name"]: t for t in hz("GET", "/server_types?per_page=50")["server_types"]}
    total = 0.0
    for role, name in types().items():
        t = st.get(name) or sys.exit(f"Servertyp {name} unbekannt")
        p = next((float(x["price_hourly"]["gross"]) for x in t["prices"] if x["location"] == loc), None)
        if p is None:
            sys.exit(f"{name} gibt es in {loc} nicht")
        total += p
        lo = next((x for x in t.get("locations") or [] if x.get("name") == loc), None)
        ok = "nicht in " + loc if lo is None else "verfuegbar" if lo.get("available", True) else "AKTUELL NICHT VERFUEGBAR"
        print(f"  {role}: {name:7} {t['cores']} vCPU, {t['memory']:g} GB, {t['architecture']}  {p:.4f} EUR/h  {ok}")
    print(f"  zusammen etwa {total:.3f} EUR/h brutto in {loc} (zzgl. IPv4 je Server)")


def cmd_up():
    if servers():
        sys.exit("Es gibt schon Server mit dem Label - erst 'down' oder 'status'")
    dom, ch, loc = env("TESTLAB_DOMAIN"), os.environ.get("TESTLAB_CHANNEL", "testing"), os.environ.get("TESTLAB_LOCATION", "fsn1")
    pub, _, kh = key_paths()
    kh.unlink(missing_ok=True)
    key = hz("POST", "/ssh_keys", {"name": f"{LABEL}-{int(time.time())}", "public_key": pub.read_text().strip(),
                                   "labels": {LABEL: ""}})["ssh_key"]["id"]
    zid = zone_id()
    for role, typ in types().items():
        ud = (HERE / f"cloud-init-{role}.yaml").read_text().replace("@@DOMAIN@@", dom).replace("@@CHANNEL@@", ch)
        s = hz("POST", "/servers", {"name": f"{LABEL}-{role}", "server_type": typ, "image": "debian-12", "location": loc,
                                    "ssh_keys": [key], "user_data": ud, "labels": {LABEL: "", "rolle": role},
                                    "public_net": {"enable_ipv4": True, "enable_ipv6": True}})["server"]
        v4, v6 = s["public_net"]["ipv4"]["ip"], s["public_net"]["ipv6"]["ip"].split("/")[0] + "1"
        for host in DNS[role]:
            for typ_, ip in (("A", v4), ("AAAA", v6)):
                cf("POST", f"/zones/{zid}/dns_records", {"type": typ_, "name": f"{host}.{dom}", "content": ip, "ttl": 60,
                                                          "proxied": False, "comment": LABEL})
        print(f"  {role}: {s['name']} {typ} {v4} {v6} -> {', '.join(h + '.' + dom for h in DNS[role])}")
    print("Warte auf cloud-init (Fertig-Signal /var/lib/saga-test/ready) ...")
    todo, t0 = set(ROLES), time.time()
    while todo and time.time() - t0 < 1200:
        time.sleep(20)
        for role in sorted(todo):
            r = ssh(role, "test -f /var/lib/saga-test/ready && echo ok || { test -f /var/lib/saga-test-failed && echo kaputt; }",
                    check=False)
            out = (r.stdout or b"").decode().strip()
            if out in ("ok", "kaputt"):
                print(f"  {role}: {'bereit' if out == 'ok' else 'FEHLER - Log: /var/log/saga-test-start.log bzw. cloud-init'}")
                todo.discard(role)
    if todo:
        sys.exit(f"Nach 20 min nicht fertig: {', '.join(sorted(todo))}")


def cmd_koppeln():
    dom = env("TESTLAB_DOMAIN")
    flags = ["calls.horn=1", "calls.horn_all=1", "calls.horn_from=3", "chat_v3=1"]
    for role, extra in (("a", ["calls.horn_builtin=1"]), ("b", ["calls.horn_builtin=0"])):
        r = ssh(role, "cd /opt/saga-test/saga && docker compose exec -T saga python run.py einstellen " + " ".join(flags + extra),
                check=False)
        print(f"  {role}: " + (r.stdout or b"").decode().strip().replace("\n", " | ") + (r.stderr or b"").decode().strip())
    # Deploy-Skript mit dem einmaligen Code direkt von B nach C reichen (nicht lokal speichern, nicht ausgeben)
    r = ssh("b", f"cd /opt/saga-test/saga && docker compose exec -T saga python run.py horn-koppeln horn-b.{dom} --teilen",
            check=False)
    if r.returncode != 0:
        sys.exit("Kopplungs-Code auf B fehlgeschlagen: " + (r.stderr or b"").decode().strip())
    r2 = ssh("c", "cd /opt/saga-test/knoten && sh", inp=r.stdout, check=False)
    print("  c: Knoten gestartet" if r2.returncode == 0 else "  c: FEHLER " + (r2.stderr or b"").decode().strip()[-400:])
    print("Im Admin von B (SSH-Tunnel, Zugang: /root/saga-test/zugang.txt) muss horn-b gleich 'Bereit' zeigen.")


_PY_INIT = "import db; db.wait_ready(30); db.init_schema(); from saga import federation as F; F.set_enabled(True); "


def _saga_py(role, code, inp=None):
    """Python im Saga-Container von role (stdin = inp) -> (rc, stdout, stderr). Ausgaben bleiben im Prozess."""
    r = ssh(role, "cd /opt/saga-test/saga && docker compose exec -T saga python -c " + shlex.quote(_PY_INIT + code),
            inp=inp if inp is not None else b"", check=False)
    return r.returncode, (r.stdout or b"").decode().strip(), (r.stderr or b"").decode().strip()


def cmd_foederieren():
    """Foederation A <-> B ohne Admin-Oberflaeche: Einladungscode auf A, Verbindungsanfrage von B (Fingerprint aus der
    Vorschau), Bestaetigung auf A. Der Code geht nur von Prozess zu Prozess (nicht ausgegeben)."""
    dom = env("TESTLAB_DOMAIN")
    rc, code, err = _saga_py("a", "print(F.make_invite('testlabor'))")
    if rc or not code:
        sys.exit("Einladung auf A fehlgeschlagen: " + err[-400:])
    rc, out, err = _saga_py("b", f"import sys; u='https://a.{dom}'; c=sys.stdin.readline().strip(); i=F.preview(u); "
                                 "F.connect(u, c, i['fingerprint']); print('ok', i['fingerprint'][:8])", inp=(code + "\n").encode())
    print(f"  b: Anfrage an A {out or 'FEHLER ' + err[-400:]}")
    if rc:
        sys.exit(1)
    rc, out, err = _saga_py("a", f"from db import connect; c=connect(); r=c.execute('SELECT id FROM fed_peer WHERE host=?', ('b.{dom}',)).fetchone(); "
                                 "print(F.confirm(r[0], 'testlabor'))")
    print(f"  a: bestaetigt {out or 'FEHLER ' + err[-400:]}")


def cmd_dm_test():
    """Browser-Test 1:1 ueber Knoten ueber Servergrenzen (tests/browser_fed_horn_dm.js) auf C: Wegwerf-Konten c1 (A) und
    c2 (B) mit zufaelligem Passwort (nur ueber stdin), Test vom Branch TESTLAB_CHANNEL (Standard testing)."""
    dom, ch = env("TESTLAB_DOMAIN"), os.environ.get("TESTLAB_CHANNEL", "testing")
    pw = secrets.token_urlsafe(18)
    for role, user in (("a", "c1"), ("b", "c2")):
        r = ssh(role, f"cd /opt/saga-test/saga && docker compose exec -T saga python run.py benutzer-anlegen {user}", inp=(pw + "\n").encode(),
                check=False)
        print(f"  {role}: Konto {user} " + ("ok" if r.returncode == 0 else "FEHLER " + (r.stderr or b"").decode()[-300:]))
    script = (f"set -e; cd /opt/saga-test; curl -fsSL https://raw.githubusercontent.com/yggdrasil-sh/saga/{ch}/tests/browser_fed_horn_dm.js -o fed-dm.cjs; "
              f"read -r P; SAGA_PASS=\"$P\" SAGA_A=https://a.{dom} SAGA_B=https://b.{dom} SAGA_USER_A=c1 SAGA_USER_B=c2 "
              "CHROMIUM=/usr/bin/chromium PLAYWRIGHT=/opt/saga-test/clients/node_modules/playwright-core SHOTS=/opt/saga-test/shots "
              "node fed-dm.cjs 2>&1 | tail -40")
    r = ssh("c", "sh -c " + shlex.quote(script), inp=(pw + "\n").encode(), check=False)
    sys.stdout.write((r.stdout or b"").decode()); sys.stderr.write((r.stderr or b"").decode()[-1500:])


def cmd_status():
    for role, s in sorted(servers().items()):
        print(f"  {role}: {s['name']} {s['server_type']['name']} {s['status']} {s['public_net']['ipv4']['ip']}")
    if not servers():
        print("  keine Server mit dem Label")


def cmd_down():
    for s in servers().values():
        hz("DELETE", f"/servers/{s['id']}")
        print(f"  Server {s['name']} geloescht")
    for k in hz("GET", f"/ssh_keys?label_selector={LABEL}")["ssh_keys"]:
        hz("DELETE", f"/ssh_keys/{k['id']}")
        print(f"  SSH-Schluessel {k['name']} geloescht")
    zid = zone_id()
    for rec in cf("GET", f"/zones/{zid}/dns_records?per_page=100&comment.exact={LABEL}")["result"]:
        cf("DELETE", f"/zones/{zid}/dns_records/{rec['id']}")
        print(f"  DNS {rec['type']} {rec['name']} geloescht")
    key_paths()[2].unlink(missing_ok=True)
    time.sleep(5)
    print("Uebrig mit dem Label:")
    left = 0
    for kind in ("servers", "ssh_keys", "primary_ips", "volumes", "floating_ips"):
        for x in hz("GET", f"/{kind}?label_selector={LABEL}")[kind]:
            left += 1
            print(f"  {kind}: {x['name']} ({x.get('status', '')})")
    for rec in cf("GET", f"/zones/{zid}/dns_records?per_page=100&comment.exact={LABEL}")["result"]:
        left += 1
        print(f"  dns: {rec['type']} {rec['name']}")
    print("  nichts" if not left else "  (Server im Loeschen verschwinden binnen einer Minute - 'status' erneut pruefen)")


if __name__ == "__main__":
    a = sys.argv[1:]
    c = a[0] if a else ""
    if c == "plan":
        cmd_plan()
    elif c == "up":
        cmd_up()
    elif c == "koppeln":
        cmd_koppeln()
    elif c == "foederieren":
        cmd_foederieren()
    elif c == "dm-test":
        cmd_dm_test()
    elif c == "status":
        cmd_status()
    elif c == "down":
        cmd_down()
    elif c == "ssh" and len(a) >= 2 and a[1] in ROLES:
        r = ssh(a[1], *a[2:], check=False) if a[2:] else None
        if r is None:
            _, priv, kh = key_paths()
            os.execvp("ssh", ["ssh", "-i", str(priv), "-o", f"UserKnownHostsFile={kh}",
                              f"root@{servers()[a[1]]['public_net']['ipv4']['ip']}"])
        sys.stdout.write((r.stdout or b"").decode()); sys.stderr.write((r.stderr or b"").decode())
        sys.exit(r.returncode)
    else:
        sys.exit(__doc__)
