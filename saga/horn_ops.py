"""Gjallarhorn-Knotenbetrieb (Saga 2.5, Pakete P6-P8; Konzept v2 §2.3-2.9, §3.4, §4.1): "Erweitern" (Kopplung mit
Deploy-Skript), Rueckpruefung der Erreichbarkeit, Leeren, Teilen, Entfernen, DNS (Cloudflare) und Zertifikate (ACME-TXT
fuer DNS-01) sowie der Zustand fuer die Admin-Seite "Anrufe".

Kopplung:
  1. Admin: pair_create(name, share, udp) -> Kopplungs-Code "hkh_" + 32 Byte (Base64url), 30 min gueltig, einmalig, in
     horn_pair nur als SHA-256, gebunden an Knotenname und Saga-Adresse. Dazu Deploy-Skript (Docker Compose bzw.
     systemd ohne Docker) - vollstaendig sichtbar, kein curl | sh.
  2. Knoten: GET /api/horn/whoami (je IPv4/IPv6), dann POST /api/horn/pair {token, name, node_pub, sig, v4, v6, port, udp,
     version, cores, link_mbit}. sig = Ed25519(node_sk, "horn-pair/1\\n" + token + "\\n" + name).
  3. Saga: Code pruefen (unbekannt/benutzt 403, abgelaufen 410, falscher Name 403), an den ersten Knotenschluessel binden,
     Erreichbarkeit zurueckpruefen (443/TCP je Adresse: TLS + GET /api/horn/echo mit signierter Antwort; UDP: STUN-Binding).
     Klappt keine Adresse: 424 (der Knoten versucht es mit demselben Schluessel erneut, z. B. nach Firewall-Freigabe).
     Sonst: Knoten anlegen, Code verbrauchen, DNS A/AAAA nur fuer gepruefte Adressen, Antwort {node_id, saga_pub, ctl_url,
     name}. Pruefprotokoll (horn_log) und Benachrichtigung aller Admins.
Entfernen: Schluessel sperren (horn_ban), signiertes remove an den Knoten, Steuerverbindung zu, Endpunkte vom Knoten
loesen und Umzug melden (call.horn_move), DNS aufraeumen (nur Eintraege mit Kommentar saga-horn:<node_id>).
Der Cloudflare-Token verlaesst Saga nie: Knoten bekommen fuer DNS-01 nur den TXT-Eintrag ihres eigenen Namens."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import struct
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import connect as _db
import auth
from saga import cloudflare, horn, notify
from saga.core import BASE_URL, HERE, t
from version import VERSION

router = APIRouter()

PAIR_TTL = 1800                          # Kopplungs-Code 30 min
TOKEN_PREFIX = "hkh_"
IMAGE = os.environ.get("SAGA_HORN_IMAGE", "ghcr.io/yggdrasil-sh/gjallarhorn")
RELEASE_BASE = "https://github.com/yggdrasil-sh/saga/releases/download"
NAME_RE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")
LABEL_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
CHECK_TIMEOUT = 4.0
RL_MAX = 5000                            # Schluessel im Rate-Limit-Speicher (danach die am laengsten unbenutzten verdraengen)
_rl = OrderedDict()
_rl_lock = threading.Lock()


def _rate(kind, key, n, per=60.0):
    """Hoechstens n Ereignisse je (kind, key) in per Sekunden. Voll: die am laengsten unbenutzten Schluessel fallen weg,
    nie der ganze Speicher (viele Quelladressen duerfen die Kopplungs-Bremse nicht zuruecksetzen)."""
    now = time.time()
    k = (kind, key)
    with _rl_lock:
        h = [x for x in _rl.get(k, ()) if now - x < per]
        ok = len(h) < n
        _rl[k] = h + [now] if ok else h
        _rl.move_to_end(k)
        while len(_rl) > RL_MAX:
            _rl.popitem(last=False)
    return ok


def _json(d, status=200):
    return JSONResponse(d, status_code=status, headers={"Cache-Control": "no-store"})


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def log(c, event, node_id=None, name=None, detail=""):
    c.execute("INSERT INTO horn_log (ts, node_id, name, event, detail) VALUES (?,?,?,?,?)",
              (time.time(), node_id, name, event, str(detail)[:300]))


def _notify_admins(c, code, name):
    try:
        for (u,) in c.execute("SELECT username FROM users WHERE is_admin=1 AND disabled=0").fetchall():
            notify.create(c, u, "horn.node", None, data={"code": code, "name": name})
    except Exception as ex:  # noqa: BLE001 - nur Hinweis
        print(f"horn: Admin-Benachrichtigung: {type(ex).__name__}", flush=True)


def _notif_render(c, uid, item, d):
    """Benachrichtigung horn.node (Kopplung, Entfernen): Text in der Sprache des Admins, Link in den Admin-Bereich."""
    from saga.core import ADMIN_URL
    from saga.i18n import t
    name = str((d or {}).get("name") or "")
    code = (d or {}).get("code")
    item["access"] = True
    item["system"] = True
    item["text"] = (t("Knoten {name} gekoppelt", name=name) if code == "paired" else t("Knoten {name} entfernt", name=name))
    item["title"] = t("Gjallarhorn")
    item["url"] = (ADMIN_URL + "/calls") if ADMIN_URL else None


notify.RENDER["horn.node"] = _notif_render


# --- Konfiguration: Release-Schluessel, Image --------------------------------------------------------------------------
def release_pub():
    """Oeffentlicher Release-Schluessel (Base64) aus gjallarhorn/release.pub bzw. SAGA_HORN_RELEASE_PUB - "" = keiner."""
    v = os.environ.get("SAGA_HORN_RELEASE_PUB", "").strip()
    if not v:
        try:
            with open(os.path.join(HERE, "gjallarhorn", "release.pub"), encoding="utf-8") as f:
                v = next((ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")), "")
        except OSError:
            v = ""
    try:
        return v if len(base64.b64decode(v, validate=True)) == 32 else ""
    except ValueError:
        return ""


def _pub_pem(b64):
    raw = base64.b64decode(b64)
    der = bytes.fromhex("302a300506032b6570032100") + raw          # SubjectPublicKeyInfo Ed25519
    return "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----"


def image_digest():
    """Digest des Knoten-Images, falls bekannt: SAGA_HORN_IMAGE_DIGEST=sha256:... bzw. Datei gjallarhorn/image.digest
    (vom Release-Bau, eine Zeile). Sonst ""."""
    dg = os.environ.get("SAGA_HORN_IMAGE_DIGEST", "").strip()
    if not dg:
        try:
            with open(os.path.join(HERE, "gjallarhorn", "image.digest"), encoding="utf-8") as f:
                dg = f.read(200).strip()
        except OSError:
            dg = ""
    return dg if re.fullmatch(r"sha256:[0-9a-f]{64}", dg) else ""


def image_ref():
    """Image fuer Knoten: gepinnt per Digest, falls bekannt (image_digest), sonst Tag = Saga-Version."""
    dg = image_digest()
    ref = f"{IMAGE}:{VERSION}"
    return (ref + "@" + dg) if dg else ref


def saga_origin():
    u = urlparse(BASE_URL)
    return f"{u.scheme}://{u.netloc}"


def saga_warn():
    """Nr. 009: Saga-Adresse, die ein Knoten auf einem anderen Server nicht erreichen kann (http, localhost, interne/
    Loopback-Adresse, Name ohne Punkt) - Warnung im Dialog Erweitern und im Skript. "" = in Ordnung."""
    u = urlparse(BASE_URL)
    host = (u.hostname or "").lower()
    local = not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")) or "." not in host.strip("[]")
    try:
        a = ipaddress.ip_address(host.strip("[]"))
        local = local or not a.is_global
    except ValueError:
        pass
    if u.scheme != "https" or local:
        return t("Achtung: Die Saga-Adresse {saga} ist für einen Knoten auf einem anderen Server vermutlich nicht erreichbar (nur https mit öffentlichem Namen). BASE_URL prüfen – sonst koppelt der Knoten nicht.",  # noqa: E501
                 saga=saga_origin())
    return ""


def suffix():
    """Domain fuer neue Knotennamen: Cloudflare-Zone, sonst die Saga-Domain ohne erstes Label."""
    z = cloudflare.conf().get("zone") or ""
    if z:
        return z
    h = (urlparse(BASE_URL).hostname or "").lower()
    try:
        ipaddress.ip_address(h)
        return ""                                         # Saga ueber eine IP-Adresse: keine Domain vorschlagen
    except ValueError:
        pass
    parts = h.split(".")
    return ".".join(parts[1:]) if len(parts) > 2 else h


def norm_name(v):
    v = str(v or "").strip().lower().rstrip(".")
    if v and "." not in v and LABEL_RE.fullmatch(v) and suffix():
        v = v + "." + suffix()
    return v if NAME_RE.fullmatch(v) else ""


def suggest(c):
    used = {r[0] for r in c.execute("SELECT name FROM horn_node WHERE removed IS NULL").fetchall()}
    used |= {r[0] for r in c.execute("SELECT name FROM horn_pair WHERE used IS NULL AND expires > ?", (time.time(),)).fetchall()}
    for i in range(1, 100):
        n = f"horn-{i}"
        if not any(u.split(".")[0] == n for u in used):
            return n
    return "horn-" + secrets.token_hex(2)


# --- Kopplung -----------------------------------------------------------------------------------------------------
class PairError(Exception):
    pass


def pair_create(name, share=False, udp=True, by="admin"):
    """Kopplungs-Code und Deploy-Skript fuer einen neuen Knoten -> dict (token nur hier, nie gespeichert)."""
    fq = norm_name(name)
    if not fq:
        raise PairError("name")
    if fq == (urlparse(BASE_URL).hostname or "").lower():
        raise PairError("name_saga")
    now = time.time()
    with _db() as c, c:
        if c.execute("SELECT 1 FROM horn_node WHERE name=? AND removed IS NULL", (fq,)).fetchone() or \
           c.execute("SELECT 1 FROM horn_pair WHERE name=? AND used IS NULL AND expires > ?", (fq, now)).fetchone():
            raise PairError("name_used")
        if c.execute("SELECT COUNT(*) FROM horn_pair WHERE used IS NULL AND expires > ?", (now,)).fetchone()[0] >= 10:
            raise PairError("too_many")
        c.execute("DELETE FROM horn_pair WHERE expires < ?", (now - 7 * 86400,))
        tok = TOKEN_PREFIX + _b64u(secrets.token_bytes(32))
        pid = secrets.token_hex(8)
        c.execute("INSERT INTO horn_pair (token_hash, name, share, expires, id, saga, udp, created, progress) "
                  "VALUES (?,?,?,?,?,?,?,?,CAST(? AS JSONB))",
                  (hashlib.sha256(tok.encode()).hexdigest(), fq, bool(share), now + PAIR_TTL, pid, saga_origin(), bool(udp), now,
                   json.dumps({"wait": now})))
        log(c, "pair.created", None, fq, f"von {by}")
    print(f"horn: Kopplungs-Code fuer {fq} erzeugt (von {by}, gueltig 30 min)", flush=True)
    return {"id": pid, "token": tok, "name": fq, "expires": now + PAIR_TTL, "share": bool(share), "udp": bool(udp),
            **deploy(fq, tok, udp)}


def deploy(name, tok, udp=True):
    """Deploy-Skripte (Docker Compose und systemd/Debian). Beide enthalten den einmaligen Code, keinen Cloudflare-Token."""
    saga = saga_origin()
    warn = saga_warn()
    env = (f"# Gjallarhorn-Knoten {name} für {saga} (Saga {VERSION})\n"
           f"# Einmaliger Kopplungs-Code, 30 min gültig – danach wertlos.\n" + (f"# {warn}\n" if warn else "") +
           f"HORN_SAGA={saga}\nHORN_PAIR={tok}\nHORN_NAME={name}\nHORN_UDP={'1' if udp else '0'}\n")
    pin = "" if image_digest() else ("    # Image nur per Tag (Digest unbekannt) – zum Festschreiben SAGA_HORN_IMAGE_DIGEST in Saga setzen\n")
    ports = '      - "443:443/tcp"\n' + ('      - "443:443/udp"\n' if udp else "")
    compose = ("services:\n  gjallarhorn:\n"
               f"    image: {image_ref()}\n" + pin +
               "    command: [\"watch\"]\n"
               "    restart: unless-stopped\n"
               "    env_file: .env\n"
               "    ports:\n" + ports +
               "    volumes:\n      - gjallarhorn:/data\n"
               "    stop_grace_period: 30s\n"
               "volumes:\n  gjallarhorn: {}\n")
    docker = ("#!/bin/sh\n# Gjallarhorn-Knoten " + name + " (Docker). Auf dem neuen Server ausführen.\nset -eu\n" + _warn_sh(warn) +
              "mkdir -p gjallarhorn && cd gjallarhorn\n"
              "umask 077\ncat > .env <<'EOF'\n" + env + "EOF\n"
              "cat > docker-compose.yml <<'EOF'\n" + compose + "EOF\n"
              "docker compose up -d\ndocker compose logs --tail 20\n")
    return {"env": env, "compose": compose, "script_docker": docker, "script_systemd": systemd_script(name, tok, udp), "warn": warn}


def _warn_sh(warn):
    """Warnung im Skript (stderr, mit einfachen Anfuehrungszeichen sicher gequotet)."""
    return ("echo '" + warn.replace("'", "'\\''") + "' >&2\n") if warn else ""


def systemd_script(name, tok, udp=True):
    """Debian/systemd ohne Docker (wie auf einem kleinen vServer oder LXC): Binaer aus dem GitHub-Release, Signatur mit
    dem Release-Schlüssel geprüft (openssl), eigener Benutzer, Unit mit Waechter. HORN_ROOT/HORN_RELEASE_BASE/
    SYSTEMCTL nur fuer Tests (Simulation ohne systemd)."""
    pub = release_pub()
    saga = saga_origin()
    head = ("#!/bin/sh\n# Gjallarhorn-Knoten " + name + " ohne Docker (Debian 12+, systemd). Als root ausführen.\n"
            "# Braucht curl und openssl. Das Binär kommt aus dem GitHub-Release und wird gegen den Release-Schlüssel geprüft.\n"
            "set -eu\n" + _warn_sh(saga_warn()))
    if not pub:
        return head + ("echo 'Saga kennt noch keinen Release-Schlüssel (gjallarhorn/release.pub) – die systemd-Variante ist "
                       "erst mit signierten Releases möglich. Bitte die Docker-Variante nutzen.' >&2\nexit 1\n")
    return head + f"""VER={VERSION}
ROOT=${{HORN_ROOT:-}}
BASE=${{HORN_RELEASE_BASE:-{RELEASE_BASE}/v$VER}}
case "$(uname -m)" in x86_64|amd64) ARCH=linux-amd64 ;; aarch64|arm64) ARCH=linux-arm64 ;; *) echo "Architektur $(uname -m) nicht unterstützt" >&2; exit 1 ;; esac
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
curl -fsSL -o "$T/gjallarhorn" "$BASE/gjallarhorn-$ARCH"
curl -fsSL -o "$T/gjallarhorn.sig" "$BASE/gjallarhorn-$ARCH.sig"
cat > "$T/release.pem" <<'EOF'
{_pub_pem(pub)}
EOF
printf 'gjallarhorn-release/1\\n%s\\n%s\\n%s\\n' "$VER" "$ARCH" "$(sha256sum "$T/gjallarhorn" | cut -c1-64)" > "$T/msg"
base64 -d "$T/gjallarhorn.sig" > "$T/sig.bin"
openssl pkeyutl -verify -pubin -inkey "$T/release.pem" -rawin -in "$T/msg" -sigfile "$T/sig.bin" >/dev/null || {{ echo "Signatur passt nicht – Abbruch" >&2; exit 1; }}
install -d -m 755 "$ROOT/opt/gjallarhorn" "$ROOT/etc/systemd/system"
install -d -m 700 "$ROOT/var/lib/gjallarhorn" "$ROOT/etc/gjallarhorn"
install -m 755 "$T/gjallarhorn" "$ROOT/opt/gjallarhorn/gjallarhorn"
umask 077
cat > "$ROOT/etc/gjallarhorn/gjallarhorn.env" <<'EOF'
# Gjallarhorn-Knoten {name} für {saga} (Saga {VERSION}). Einmaliger Kopplungs-Code, 30 min gültig – danach wertlos.
HORN_SAGA={saga}
HORN_PAIR={tok}
HORN_NAME={name}
HORN_UDP={'1' if udp else '0'}
HORN_DATA=/var/lib/gjallarhorn
EOF
umask 022
if [ -z "$ROOT" ]; then id gjallarhorn >/dev/null 2>&1 || useradd --system --home-dir /var/lib/gjallarhorn --shell /usr/sbin/nologin gjallarhorn
  chown -R gjallarhorn: /var/lib/gjallarhorn; fi
cat > "$ROOT/etc/systemd/system/gjallarhorn.service" <<'EOF'
[Unit]
Description=Gjallarhorn-Knoten für Saga ({name})
After=network-online.target
Wants=network-online.target

[Service]
User=gjallarhorn
EnvironmentFile=/etc/gjallarhorn/gjallarhorn.env
ExecStart=/opt/gjallarhorn/gjallarhorn watch
Restart=always
RestartSec=2
KillSignal=SIGTERM
TimeoutStopSec=40
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/gjallarhorn
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
${{SYSTEMCTL:-systemctl}} daemon-reload
${{SYSTEMCTL:-systemctl}} enable --now gjallarhorn
echo "Gjallarhorn gestartet: journalctl -u gjallarhorn -f"
"""


def _addr_ok(ip, fam, src):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if a.version != fam or a.is_multicast or a.is_unspecified or a.is_link_local:
        return False
    if a.is_loopback:                                     # nur, wenn auch die Anfrage von hier kommt (Tests, gleicher Host)
        try:
            return ipaddress.ip_address(src).is_loopback
        except ValueError:
            return False
    return True


def echo_check(name, pub_b64, ip, port, timeout=CHECK_TIMEOUT, detail=None):
    """443/TCP pruefen: TLS (ohne SNI, Zertifikat egal) + GET /api/horn/echo; die Antwort muss mit dem Knotenschluessel
    signiert sein. -> (ok, Fehlercode): timeout | unreachable_tcp | tls | http | signatur. Netzdetails (Fehlerart,
    Antwortanfang) nur in detail (Liste, fuer das Admin-Pruefprotokoll), nie an den Aufrufer der Kopplung."""
    note = detail.append if isinstance(detail, list) else (lambda x: None)
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    nonce = _b64u(secrets.token_bytes(18))
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                       # Zertifikat kommt evtl. erst nach der Kopplung (DNS-01)
    ctx.set_alpn_protocols(["http/1.1"])
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=None) as s:
                s.settimeout(timeout)
                s.sendall(f"GET /api/horn/echo?n={nonce} HTTP/1.1\r\nHost: {name}\r\nConnection: close\r\n\r\n".encode())
                buf = b""
                while len(buf) < 16384:
                    x = s.recv(4096)
                    if not x:
                        break
                    buf += x
    except (socket.timeout, TimeoutError) as ex:
        note(f"{ip}: {type(ex).__name__}")
        return False, "timeout"
    except ssl.SSLError as ex:
        note(f"{ip}: {type(ex).__name__} {str(ex)[:80]}")
        return False, "tls"
    except OSError as ex:
        note(f"{ip}: {type(ex).__name__}")
        return False, "unreachable_tcp"
    head, _, body = buf.partition(b"\r\n\r\n")
    if not head.startswith(b"HTTP/1.1 200"):
        note(f"{ip}: {head[:40].decode('latin-1', 'replace')}")
        return False, "http"
    try:
        d = json.loads(body.decode())
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(
            base64.b64decode(d.get("sig") or ""), f"horn-echo/1\n{nonce}\n{name}".encode())
    except (ValueError, InvalidSignature, TypeError, AttributeError):
        return False, "signatur"
    return True, ""


def udp_check(ip, port, timeout=1.5, tries=3):
    """443/UDP pruefen: STUN Binding Request (RFC 5389) - die UDP-Weiche des Knotens beantwortet ihn selbst."""
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    tid = secrets.token_bytes(12)
    req = struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid
    try:
        with socket.socket(fam, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            for _ in range(tries):
                s.sendto(req, (ip, int(port)))
                try:
                    while True:
                        b, _ = s.recvfrom(2048)
                        if len(b) >= 20 and b[:2] == b"\x01\x01" and b[8:20] == tid:
                            return True
                except socket.timeout:
                    continue
    except OSError:
        return False
    return False


def reach(name, pub, v4, v6, port, udp, src="", timeout=CHECK_TIMEOUT):
    """Erreichbarkeit je Adresse -> {v4: bool|None, v6: bool|None, udp: bool|None, err: {...}, at}. None = keine Adresse."""
    out = {"v4": None, "v6": None, "udp": None, "err": {}, "at": time.time()}
    res = {}
    det = []

    def one(fam, ip):
        if not ip:
            return
        if not _addr_ok(ip, fam, src or ip):
            res[fam] = (False, "adresse")
            return
        res[fam] = echo_check(name, pub, ip, port, timeout, det)

    th = [threading.Thread(target=one, args=(4, v4)), threading.Thread(target=one, args=(6, v6))]
    for x in th:
        x.start()
    for x in th:
        x.join()
    for fam in (4, 6):
        if fam in res:
            out[f"v{fam}"] = res[fam][0]
            if res[fam][1]:
                out["err"][f"v{fam}"] = res[fam][1]
    if udp:
        ips = [ip for fam, ip in ((4, v4), (6, v6)) if ip and out[f"v{fam}"]]
        out["udp"] = any(udp_check(ip, port) for ip in ips) if ips else False
    if det:
        out["detail"] = "; ".join(det)[:300]
    return out


def _progress(c, pid, **kw):
    r = c.execute("SELECT progress FROM horn_pair WHERE id=?", (pid,)).fetchone()
    p = dict(r[0] or {}) if r and isinstance(r[0], dict) else {}
    p.update(kw)
    c.execute("UPDATE horn_pair SET progress=CAST(? AS JSONB) WHERE id=?", (json.dumps(p), pid))


@router.get("/api/horn/whoami")
def api_whoami(request: Request):
    """Fuer Knoten beim Koppeln und bei dynamischer IP: die Adresse, von der die Anfrage kommt (je Adressfamilie)."""
    ip = request.client.host if request.client else ""
    if not _rate("whoami", ip, 30):
        return _json({"error": "rate"}, 429)
    return _json({"ip": ip})


def _bad(code, status, msg):
    return _json({"error": msg, "code": code}, status)


@router.post("/api/horn/pair")
async def api_pair(request: Request):
    from starlette.concurrency import run_in_threadpool
    ip = request.client.host if request.client else ""
    if not _rate("pair", ip, 20, 600):
        return _bad("rate", 429, "zu viele Versuche")
    try:
        b = await request.json()
    except ValueError:
        b = None
    if not isinstance(b, dict):
        return _bad("bad", 400, "ungültig")
    return await run_in_threadpool(pair_finish, b, ip)


def pair_finish(b, src=""):
    """Kopplung abschliessen (siehe Modulkopf) -> JSONResponse."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    tok = str(b.get("token") or "")[:100]
    name = str(b.get("name") or "").strip().lower()[:253]
    pub = str(b.get("node_pub") or "")[:60]
    try:
        raw = base64.b64decode(pub, validate=True)
        Ed25519PublicKey.from_public_bytes(raw).verify(base64.b64decode(str(b.get("sig") or ""), validate=True),
                                                       f"horn-pair/1\n{tok}\n{name}".encode())
    except (ValueError, InvalidSignature):
        return _bad("sig", 400, "Signatur ungültig")
    h = hashlib.sha256(tok.encode()).hexdigest()
    now = time.time()
    with _db() as c, c:
        r = c.execute("SELECT name, share, expires, used, id, saga, udp, pub FROM horn_pair WHERE token_hash=? FOR UPDATE", (h,)).fetchone()
        if not r or (r[7] and r[7] != pub):
            return _bad("token", 403, "Kopplungs-Code unbekannt")
        if r[3]:
            return _bad("used", 403, "Kopplungs-Code schon benutzt")
        if r[2] < now:
            return _bad("expired", 410, "Kopplungs-Code abgelaufen")
        if r[0] != name:
            log(c, "pair.bad_name", None, r[0], name)
            return _bad("name", 403, "Name passt nicht zum Kopplungs-Code")
        if r[5] and r[5] != saga_origin():
            return _bad("saga", 403, "Kopplungs-Code für eine andere Saga-Adresse")
        if c.execute("SELECT 1 FROM horn_ban WHERE pub=?", (pub,)).fetchone() or \
           c.execute("SELECT 1 FROM horn_node WHERE pub=? AND removed IS NULL", (pub,)).fetchone():
            return _bad("pub", 403, "Knotenschlüssel gesperrt oder schon gekoppelt")
        c.execute("UPDATE horn_pair SET pub=? WHERE token_hash=?", (pub, h))
        _progress(c, r[4], node=now)
        pid, share, want_udp = r[4], bool(r[1]), bool(r[6])
    v4 = str(b.get("v4") or "")[:45]
    v6 = str(b.get("v6") or "")[:45]
    try:
        port = int(b.get("port") or 443)
    except (TypeError, ValueError):
        port = 443
    port = port if 0 < port < 65536 else 443
    udp_has = bool(b.get("udp")) and want_udp
    rc = reach(name, pub, v4, v6, port, udp_has, src)
    with _db() as c, c:
        _progress(c, pid, tcp=rc, port=port)
        if not (rc["v4"] or rc["v6"]):
            log(c, "pair.unreachable", None, name, json.dumps({**rc["err"], **({"detail": rc["detail"]} if rc.get("detail") else {})}))
            # nur Fehlercodes an den Knoten (Netzdetails der geprueften Adresse stehen im Pruefprotokoll des Admin-Bereichs)
            why = {k: v for k, v in rc["err"].items() if k in ("v4", "v6")}
            return _json({"error": f"Knoten über 443/TCP nicht erreichbar ({', '.join(f'{k}: {v}' for k, v in why.items()) or 'keine Adresse'})",
                          "code": "reach", "reach": why}, 424)
        r = c.execute("SELECT used FROM horn_pair WHERE token_hash=? FOR UPDATE", (h,)).fetchone()
        if not r or r[0]:
            return _bad("used", 403, "Kopplungs-Code schon benutzt")
        nid = secrets.token_hex(8)
        c.execute("DELETE FROM horn_node WHERE name=? AND removed IS NOT NULL", (name,))
        url = f"https://{name}" + ("" if port == 443 else f":{port}")
        try:
            link = max(0, min(100000, int(b.get("link_mbit") or 0))) or None
        except (TypeError, ValueError):
            link = None
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, v4, v6, udp, share, version, state, link_mbit, created, port, checks) "
                  "VALUES (?,?,?,0,?,?,?,?,?,?,'neu',?,?,?,CAST(? AS JSONB))",
                  (nid, name, pub, url, v4 if rc["v4"] else None, v6 if rc["v6"] else None, bool(rc["udp"]), share,
                   str(b.get("version") or "")[:40], link, now, port, json.dumps(dict(rc, ctl=False))))
        c.execute("UPDATE horn_pair SET used=?, node_id=? WHERE token_hash=?", (now, nid, h))
        _progress(c, pid, paired=time.time(), udp=rc["udp"])
        log(c, "node.paired", nid, name, f"v4={'ok' if rc['v4'] else '-'} v6={'ok' if rc['v6'] else '-'} udp={rc['udp']}")
        _notify_admins(c, "paired", name)
    dns = dns_sync(nid)
    with _db() as c, c:
        _progress(c, pid, dns=dns)
    print(f"horn: Knoten {name} gekoppelt ({nid}; v4 {rc['v4']}, v6 {rc['v6']}, UDP {rc['udp']}; DNS {dns.get('state')})", flush=True)
    ws = "wss" if BASE_URL.startswith("https://") else "ws"
    return _json({"node_id": nid, "saga_pub": horn.saga_pub(), "ctl_url": f"{ws}://{urlparse(BASE_URL).netloc}/api/horn/ctl", "name": name})


# --- DNS (Cloudflare) -----------------------------------------------------------------------------------------------
def _node(c, nid):
    return horn.node_by_id(c, nid)


def dns_sync(nid):
    """A/AAAA des Knotens nachfuehren (nur gepruefte Adressen) -> {state: ok|skip|zone|err, err?}."""
    with _db() as c:
        n = _node(c, nid)
    if not n or n["builtin"]:
        return {"state": "skip"}
    if not cloudflare.configured():
        return {"state": "skip"}
    if not cloudflare.in_zone(n["name"]):
        return {"state": "zone"}
    try:
        ids = {"A": cloudflare.upsert(nid, n["name"], "A", n.get("v4") or ""),
               "AAAA": cloudflare.upsert(nid, n["name"], "AAAA", n.get("v6") or "")}
    except cloudflare.CFError as ex:
        with _db() as c, c:
            log(c, "dns.error", nid, n["name"], str(ex))
        return {"state": "err", "err": str(ex)}
    with _db() as c, c:
        c.execute("UPDATE horn_node SET dns_ids=CAST(? AS JSONB) WHERE id=?", (json.dumps({k: v for k, v in ids.items() if v}), nid))
        log(c, "dns.set", nid, n["name"], f"A={n.get('v4') or '-'} AAAA={n.get('v6') or '-'}")
    return {"state": "ok"}


def on_addr(nid, v4, v6):
    """Knoten meldet neue Adressen (dynamische IP): erst zurueckpruefen, dann speichern und DNS nachfuehren."""
    with _db() as c:
        n = _node(c, nid)
    if not n or n["builtin"]:
        return
    rc = reach(n["name"], n["pub"], v4, v6, n.get("port") or 443, bool(n["udp"]))
    if not (rc["v4"] or rc["v6"]):
        with _db() as c, c:
            log(c, "addr.unreachable", nid, n["name"], f"{v4} {v6}")
        return
    with _db() as c, c:
        c.execute("UPDATE horn_node SET v4=?, v6=?, checks=CAST(? AS JSONB) WHERE id=?",
                  (v4 if rc["v4"] else None, v6 if rc["v6"] else None, json.dumps(dict(rc, ctl=nid in horn._CONNS)), nid))
        log(c, "addr.changed", nid, n["name"], f"{v4 or '-'} {v6 or '-'}")
    dns_sync(nid)


ACME_VAL = re.compile(r"[A-Za-z0-9_-]{43}")


def acme_txt(nid, op, name, value):
    """DNS-01 fuer einen Knoten: TXT _acme-challenge.<Name dieses Knotens> setzen bzw. loeschen -> (ok, Fehler)."""
    with _db() as c:
        n = _node(c, nid)
    if not n or n["builtin"]:
        return False, "knoten"
    want = "_acme-challenge." + n["name"]
    if name.lower().rstrip(".") != want:
        with _db() as c, c:
            log(c, "acme.bad_name", nid, n["name"], name[:100])
        return False, "nur _acme-challenge des eigenen Namens"
    if not cloudflare.configured():
        return False, "kein Cloudflare-Token"
    try:
        if op == "set":
            if not ACME_VAL.fullmatch(value):
                return False, "wert"
            cloudflare.upsert(nid, want, "TXT", value)
        elif op == "del":
            cloudflare.delete_all(nid, want, "TXT")
        else:
            return False, "op"
    except cloudflare.CFError as ex:
        return False, str(ex)[:200]
    with _db() as c, c:
        log(c, "acme." + op, nid, n["name"], "")
    return True, ""


horn.HOOKS.update(acme_txt=acme_txt, addr=on_addr)


def dns_needed(n):
    """Ohne Token: Eintraege zum Abtippen und Live-Pruefung per DNS-Abfrage."""
    out = []
    try:
        infos = socket.getaddrinfo(n["name"], None, type=socket.SOCK_STREAM)
        got = {i[4][0] for i in infos}
    except (OSError, UnicodeError):
        got = set()
    for typ, ip in (("A", n.get("v4")), ("AAAA", n.get("v6"))):
        if ip:
            out.append({"type": typ, "name": n["name"], "content": ip, "ok": ip in got})
    return out


# --- Betrieb: Leeren, Teilen, Pruefen, Entfernen ----------------------------------------------------------------------
def set_drain(nid, on, by="admin"):
    with _db() as c, c:
        n = _node(c, nid)
        if not n:
            return False
        c.execute("UPDATE horn_node SET drain=? WHERE id=?", (time.time() if on else None, nid))
        log(c, "node.drain" if on else "node.undrain", nid, n["name"], f"von {by}")
        if on:
            horn.emit_move(c, nid, "drain")
    horn.send(nid, {"t": "drain", "on": bool(on)})
    horn.broadcast_peers()
    return True


def set_share(nid, on, by="admin"):
    with _db() as c, c:
        n = _node(c, nid)
        if not n:
            return False
        c.execute("UPDATE horn_node SET share=? WHERE id=?", (bool(on), nid))
        log(c, "node.share", nid, n["name"], f"{'an' if on else 'aus'} von {by}")
    if horn.HOOKS.get("share_changed"):                # P10: verbundene Server erfahren die Adressen (hello, CSP)
        threading.Thread(target=horn.HOOKS["share_changed"], daemon=True).start()
    return True


def check(nid):
    """Port-Pruefung (Admin "Port pruefen" / "Erneut pruefen") -> gespeichertes Ergebnis."""
    with _db() as c:
        n = _node(c, nid)
    if not n:
        return None
    if n["builtin"]:
        rc = {"v4": None, "v6": None, "udp": bool(n["udp"]) or None, "err": {}, "at": time.time()}
        u = urlparse(n.get("url") or "")
        if u.hostname:
            try:
                ips = {i[4][0] for i in socket.getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)}
            except (OSError, UnicodeError):
                ips = set()
            for ip in ips:
                fam = 6 if ":" in ip else 4
                if rc[f"v{fam}"] is None:
                    ok, err = echo_check(n["name"], n["pub"], ip, u.port or 443)
                    rc[f"v{fam}"] = ok
                    if err:
                        rc["err"][f"v{fam}"] = err
    else:
        rc = reach(n["name"], n["pub"], n.get("v4") or "", n.get("v6") or "", n.get("port") or 443, True, "127.0.0.1"
                   if any(ipaddress.ip_address(x).is_loopback for x in (n.get("v4"), n.get("v6")) if x) else "")
    rc["ctl"] = n["id"] in horn._CONNS
    with _db() as c, c:
        c.execute("UPDATE horn_node SET checks=CAST(? AS JSONB) WHERE id=?", (json.dumps(rc), nid))
    return rc


def remove(nid, by="admin"):
    """Entfernen: sperren, signiertes remove, Umzug melden, Endpunkte loesen, DNS aufraeumen -> {dns: n|err} oder None."""
    with _db() as c, c:
        n = _node(c, nid)
        if not n or n["builtin"]:
            return None
        now = time.time()
        moved = horn.emit_move(c, nid, "removed")
        c.execute("UPDATE chat_call_leg SET node=NULL WHERE node=? AND state='active'", (nid,))
        c.execute("UPDATE horn_node SET removed=?, state='entfernt' WHERE id=?", (now, nid))
        c.execute("INSERT INTO horn_ban (pub, node_id, at) VALUES (?,?,?) ON CONFLICT (pub) DO NOTHING", (n["pub"], nid, now))
        log(c, "node.removed", nid, n["name"], f"von {by}, {moved} Anruf(e) ziehen um")
        _notify_admins(c, "removed", n["name"])
    horn.send(nid, {"t": "remove", "sig": horn.sign_remove(nid, n["name"])})
    horn.close_conn(nid, 4410)
    horn._LIVE.pop(nid, None)
    res = {"moved": moved}
    if cloudflare.configured():
        try:
            res["dns"] = cloudflare.delete_all(nid)
            with _db() as c, c:
                log(c, "dns.removed", nid, n["name"], str(res["dns"]))
        except cloudflare.CFError as ex:
            res["dns_err"] = str(ex)
            with _db() as c, c:
                log(c, "dns.error", nid, n["name"], str(ex))
    print(f"horn: Knoten {n['name']} entfernt (von {by}; {moved} Anruf(e) ziehen um)", flush=True)
    return res


def pair_cancel(pid):
    with _db() as c, c:
        c.execute("UPDATE horn_pair SET expires=? WHERE id=? AND used IS NULL", (time.time() - 1, pid))


def pair_status(pid):
    """Live-Fortschritt fuer den Dialog "Erweitern" (Admin-Seite fragt alle 2 s, solange er offen ist)."""
    with _db() as c:
        r = c.execute("SELECT name, expires, used, node_id, progress, udp FROM horn_pair WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        n = _node(c, r[3]) if r[3] else None
    p = r[4] if isinstance(r[4], dict) else {}
    now = time.time()
    out = {"name": r[0], "expires": r[1], "expired": not r[2] and r[1] < now, "steps": [], "port": int(p.get("port") or 443)}
    st = out["steps"]
    st.append({"k": "wait", "s": "ok" if p.get("node") else "run"})
    if p.get("node"):
        st.append({"k": "paired", "s": "ok" if r[2] else ("err" if p.get("tcp") else "run")})
    if r[2]:
        d = (p.get("dns") or {}).get("state")
        st.append({"k": "dns", "s": {"ok": "ok", "skip": "skip", "zone": "warn", "err": "err"}.get(d, "run")})
        cs = ((n or {}).get("cert") or {}).get("state")
        st.append({"k": "cert", "s": "ok" if cs in ("ok", "self") else ("err" if ((n or {}).get("cert") or {}).get("err") else "run")})
    tcp = p.get("tcp") or {}
    if tcp:
        st.append({"k": "tcp", "s": "ok" if (tcp.get("v4") or tcp.get("v6")) else "err", "v4": tcp.get("v4"), "v6": tcp.get("v6")})
    if r[2]:
        st.append({"k": "udp", "s": "ok" if p.get("udp") else ("off" if not r[5] else "no")})
        conn = bool(n and n["id"] in horn._CONNS)
        done = conn and all(x["s"] in ("ok", "skip", "warn", "no", "off") for x in st)
        st.append({"k": "done", "s": "ok" if done else "run"})
        out["done"] = done
    return out


def caps_sync():
    """Admin-Knopf "Faehigkeiten jetzt abgleichen": sofort hello an alle verbundenen Server -> Anzahl."""
    from saga import federation
    if not federation.enabled():
        return 0
    with _db() as c:
        ids = [p["id"] for p in federation.peers(c) if p["status"] == "active"]
    for pid in ids:
        federation.peer_submit(pid, federation.hello, pid)
    return len(ids)


# --- Admin-Zustand -----------------------------------------------------------------------------------------------------
def _status(n, now):
    """Status-Schluessel fuer die Tabelle (Texte in templates/admin/calls.html)."""
    if n["builtin"] and not horn.builtin_on():
        return "off", None
    connected = n["id"] in horn._CONNS
    upd = n.get("upd") or {}
    ld = (horn._LIVE.get(n["id"]) or {}).get("load") or n.get("load") or {}
    if not connected:
        if n["state"] == "neu":
            return "waiting", None
        return "down", n.get("seen")
    if upd.get("state") in ("neustart", "laden") and upd.get("want"):
        return "updating", None
    if ld.get("state") == "draining" or n.get("drain"):
        return ("draining_upd" if upd.get("state") == "leeren" else "draining"), None
    if not horn.cert_ok(n):
        return "nocert", None
    if horn.util(n) >= horn.FULL:
        return "full", None
    return "ready", None


def busy_hint(ns, now=None):
    """Hinweis "oft ausgelastet": 7 Tage am Stueck ueber 70 % (Mehrheit der Messungen) oder zuletzt "voll"."""
    now = now or time.time()
    days = [time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400)) for i in range(1, 8)]
    for n in ns:
        st = n.get("stats") or {}
        if (st.get("full") or 0) > now - 7 * 86400:
            return True
        d = st.get("d") or {}
        if all(k in d and d[k][1] and d[k][0] * 2 > d[k][1] for k in days):
            return True
    return False


def admin_state():
    now = time.time()
    with _db() as c:
        ns = horn.nodes(c)
        stats = {r[0]: r[1] for r in c.execute("SELECT id, stats FROM horn_node WHERE removed IS NULL").fetchall()}
        checks = {r[0]: r[1] for r in c.execute("SELECT id, checks FROM horn_node WHERE removed IS NULL").fetchall()}
        pend = [{"id": r[0], "name": r[1], "expires": r[2]} for r in
                c.execute("SELECT id, name, expires FROM horn_pair WHERE used IS NULL AND expires > ? ORDER BY created", (now,)).fetchall()]
        logs = [{"ts": r[0], "name": r[1], "event": r[2], "detail": r[3]} for r in
                c.execute("SELECT ts, name, event, detail FROM horn_log ORDER BY id DESC LIMIT 20").fetchall()]
        rekey = horn.rekey_nodes(c)
    out = []
    for n in ns:
        ld = (horn._LIVE.get(n["id"]) or {}).get("load") or n.get("load") or {}
        st, since = _status(n, now)
        n2 = dict(n, stats=stats.get(n["id"]) or {}, checks=checks.get(n["id"]) or {})
        ck = n2["checks"]
        out.append({"id": n["id"], "name": n["name"], "builtin": bool(n["builtin"]), "status": st, "since": since,
                    "tcp": bool(ck.get("v4") or ck.get("v6")) if ck else (n["id"] in horn._CONNS),
                    "udp": bool(n["udp"]), "cpu": round(100 * float(ld.get("cpu") or 0)), "up": round(float(ld.get("up") or 0), 1),
                    "link": n.get("link_mbit"), "util": round(100 * horn.util(n)), "peers": int(ld.get("peers") or 0),
                    "legs": int(ld.get("legs") or 0), "version": n.get("version") or "", "share": bool(n["share"]),
                    "drain": bool(n.get("drain")), "checks": ck, "cert": n.get("cert") or {}, "upd": n.get("upd") or {},
                    "ctl": n["id"] in horn._CONNS, "v4": n.get("v4") or "", "v6": n.get("v6") or "", "port": n.get("port") or 443,
                    "url": n.get("url") or "", "dns": [] if cloudflare.configured() or n["builtin"] else dns_needed(n),
                    "stats": n2["stats"],
                    # P9/P10: Bruecken (Kaskade) und Kontingente verbundener Server laut letzter Lastmeldung
                    "bridges": (horn._LIVE.get(n["id"]) or {}).get("bridges") or {}, "quotas": (horn._LIVE.get(n["id"]) or {}).get("quotas") or {}})
    cf = cloudflare.conf()
    return {"nodes": out, "pending": pend, "log": logs, "hint": busy_hint(out, now), "release_key": bool(release_pub()), "rekey": rekey,
            "cf": {"set": bool(cf.get("token")), "mask": cloudflare.mask(cf.get("token") or ""), "zone": cf.get("zone") or "",
                   "check": cf.get("check") or {}},
            "suffix": suffix(), "image": image_ref(), "saga": saga_origin(), "saga_warn": saga_warn(), "builtin_on": horn.builtin_on(),
            "horn_on": horn.horn_on(), "horn_all": bool((auth.settings().get("calls") or {}).get("horn_all"))}
