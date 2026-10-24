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
    TESTLAB_ROLLEN     nur diese Rollen anlegen, z. B. "a" (Standard alle)
    TESTLAB_A_NAME     Name von Server A (Standard a), z. B. a2 bei erschoepftem Let's-Encrypt-Limit
    TESTLAB_ZERT       Zwischenspeicher der TLS-Zertifikate (Standard ~/saga-testlab/zertifikate, nur fuer dich lesbar);
                       "aus" = immer neu beantragen
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
A_NAME = os.environ.get("TESTLAB_A_NAME", "a")   # anderer Name fuer A, z. B. bei Let's-Encrypt-Limit
DNS = {"a": (A_NAME, "horn-" + A_NAME), "b": ("b",), "c": ("horn-b",)}


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
    nur = [r.strip() for r in os.environ.get("TESTLAB_ROLLEN", "").split(",") if r.strip()]   # z. B. "a" = nur Server A
    if nur:
        out = {k: v for k, v in out.items() if k in nur}
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


# --- Zwischenspeicher fuer TLS-Zertifikate ---------------------------------------------------------------------------
# Let's Encrypt stellt je Namensliste hoechstens 5 Zertifikate pro Woche aus. Nach einem Labor werden die Zertifikate
# von A (Traefik acme.json) und B (Caddy-Daten) auf diesen Rechner geholt und beim naechsten "up" vor dem Start des
# Proxys wieder hochgeladen. Die Startskripte warten darauf (Marke /opt/saga-test/zert-ok, hoechstens 5 min).
# Wiederverwendet wird hoechstens ZERT_TAGE nach dem Holen (gueltig sind sie 90 Tage, der Proxy erneuert ab 30 Tagen Rest).
ZERT_TAGE = 60
ZERT = {"a": ("/opt/saga-test/le", "acme.json"), "b": ("/opt/saga-test/caddy", "caddy.tgz")}


def zert_dir():
    v = os.environ.get("TESTLAB_ZERT", "~/saga-testlab/zertifikate")
    if v == "aus":
        return None
    p = Path(os.path.expanduser(v))
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def zert_host(role):
    return f"{DNS['a'][0] if role == 'a' else 'b'}.{env('TESTLAB_DOMAIN')}"


def zert_file(role):
    d = zert_dir()
    return d / f"{zert_host(role)}-{ZERT[role][1]}" if d else None


def zert_push(role):
    """Gespeicherte Zertifikate hochladen (wenn frisch genug) und den Start freigeben. -> True, wenn hochgeladen."""
    f, (rdir, _) = zert_file(role), ZERT[role]
    sent = False
    if f and f.exists() and time.time() - f.stat().st_mtime < ZERT_TAGE * 86400:
        data = f.read_bytes()
        if role == "a":
            r = ssh(role, f"mkdir -p {rdir} && umask 077 && cat > {rdir}/acme.json && chmod 600 {rdir}/acme.json", inp=data, check=False)
        else:
            r = ssh(role, f"mkdir -p {rdir} && tar xzf - -C {rdir}", inp=data, check=False)
        sent = r.returncode == 0
    ssh(role, "touch /opt/saga-test/zert-ok", check=False)
    return sent


def zert_pull(role):
    """Nach dem Start: Zertifikate holen, sobald der Proxy eins hat (hoechstens 2 min warten)."""
    f, (rdir, _) = zert_file(role), ZERT[role]
    if not f:
        return
    host = zert_host(role)
    for _ in range(12):
        if role == "a":
            r = ssh(role, f"cat {rdir}/acme.json", check=False)
            raw = r.stdout or b""
            try:
                certs = [c for v in json.loads(raw or b"{}").values() for c in (v or {}).get("Certificates") or []]
            except ValueError:
                certs = []
            ok = any((c.get("domain") or {}).get("main") == host for c in certs)
        else:
            ok = ssh(role, f"ls {rdir}/caddy/certificates/*/{host}/{host}.crt", check=False).returncode == 0
            raw = ssh(role, f"tar czf - -C {rdir} .", check=False).stdout if ok else b""
        if ok and raw:
            old = f.read_bytes() if f.exists() else None
            if raw != old:
                fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(raw)
                print(f"  {role}: Zertifikat fuer {host} zwischengespeichert")
            return
        time.sleep(10)
    print(f"  {role}: noch kein Zertifikat fuer {host} - nicht zwischengespeichert")


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
        ud = (HERE / f"cloud-init-{role}.yaml").read_text().replace("a.@@DOMAIN@@", f"{A_NAME}.@@DOMAIN@@").replace("@@DOMAIN@@", dom).replace("@@CHANNEL@@", ch)
        s = hz("POST", "/servers", {"name": f"{LABEL}-{role}", "server_type": typ, "image": "debian-12", "location": loc,
                                    "ssh_keys": [key], "user_data": ud, "labels": {LABEL: "", "rolle": role},
                                    "public_net": {"enable_ipv4": True, "enable_ipv6": True}})["server"]
        v4, v6 = s["public_net"]["ipv4"]["ip"], s["public_net"]["ipv6"]["ip"].split("/")[0] + "1"
        for host in DNS[role]:
            for typ_, ip in (("A", v4), ("AAAA", v6)):
                cf("POST", f"/zones/{zid}/dns_records", {"type": typ_, "name": f"{host}.{dom}", "content": ip, "ttl": 60,
                                                          "proxied": False, "comment": LABEL})
        print(f"  {role}: {s['name']} {typ} {v4} {v6} -> {', '.join(h + '.' + dom for h in DNS[role])}")
    # Zertifikate hochladen, sobald SSH geht (die Startskripte warten darauf, hoechstens 5 min)
    zert = [r for r in types() if r in ZERT]
    t0 = time.time()
    while zert and time.time() - t0 < 280:
        time.sleep(10)
        for role in list(zert):
            if ssh(role, "true", check=False).returncode == 0:
                print(f"  {role}: " + ("Zertifikat aus dem Zwischenspeicher hochgeladen" if zert_push(role) else "kein Zertifikat im Zwischenspeicher - wird neu beantragt"))
                zert.remove(role)
    print("Warte auf cloud-init (Fertig-Signal /var/lib/saga-test/ready) ...")
    todo, t0 = set(types()), time.time()
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
    for role in types():
        if role in ZERT:
            zert_pull(role)


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
    time.sleep(6)                                      # Einstellung "Foederation an" gilt im laufenden Saga nach 5 s
    rc, out, err = _saga_py("b", f"import sys; u='https://{A_NAME}.{dom}'; c=sys.stdin.readline().strip(); i=F.preview(u); "
                                 "F.connect(u, c, i['fingerprint']); print('ok', i['fingerprint'][:8])", inp=(code + "\n").encode())
    print(f"  b: Anfrage an A {out or 'FEHLER ' + err[-400:]}")
    if rc:
        sys.exit(1)
    rc, out, err = _saga_py("a", f"from db import connect\nwith connect() as c:\n    r = c.execute('SELECT id FROM fed_peer WHERE host=?', ('b.{dom}',)).fetchone()\n"
                                 "print(F.confirm(r[0], 'testlabor'))")
    print(f"  a: bestaetigt {out or 'FEHLER ' + err[-400:]}")


def cmd_dm_test():
    """Browser-Test 1:1 ueber Knoten ueber Servergrenzen (tests/browser_fed_horn_dm.js) auf C: Wegwerf-Konten c1 (A) und
    c2 (B) mit zufaelligem Passwort (nur ueber stdin), Test vom Branch TESTLAB_CHANNEL (Standard testing)."""
    dom, ch = env("TESTLAB_DOMAIN"), os.environ.get("TESTLAB_CHANNEL", "testing")
    pw, sx = secrets.token_urlsafe(18), secrets.token_hex(2)     # neue Konten je Lauf (vorhandene behalten ihr Passwort)
    ua, ub = f"c1x{sx}", f"c2x{sx}"
    for role, user in (("a", ua), ("b", ub)):
        r = ssh(role, f"cd /opt/saga-test/saga && docker compose exec -T saga python run.py benutzer-anlegen {user}", inp=(pw + "\n").encode(),
                check=False)
        print(f"  {role}: Konto {user} " + ("ok" if r.returncode == 0 else "FEHLER " + (r.stderr or b"").decode()[-300:]))
    script = (f"set -e; cd /opt/saga-test; curl -fsSL https://raw.githubusercontent.com/yggdrasil-sh/saga/{ch}/tests/browser_fed_horn_dm.js -o fed-dm.cjs; "
              f"read -r P; SAGA_PASS=\"$P\" SAGA_A=https://{A_NAME}.{dom} SAGA_B=https://b.{dom} SAGA_USER_A={ua} SAGA_USER_B={ub} "
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
