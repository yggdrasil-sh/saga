"""Domain-Nachweis bei der Anmeldung: einmal https://<host>/.well-known/saga-push-relay abrufen, {v:1, key:<pub>} erwartet.

SSRF-Schutz: nur Hostnamen (keine IP-Literale), nur https, Port nur der aus host (Standard 443), jede aufgeloeste Adresse
muss oeffentlich sein, verbunden wird mit genau der gepruften Adresse (kein DNS-Rebinding), Zertifikat gegen den Namen,
keine Weiterleitungen, 5 s Gesamtzeit, hoechstens 4 KB Antwort. RELAY_VERIFY_TEST_BASE (nur Tests): Abruf ueber
http://127.0.0.1:<port> mit Host-Kopf, nur Loopback."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urlparse

PATH = "/.well-known/saga-push-relay"
TIMEOUT = 5.0
MAX = 4096
HOST_RE = re.compile(r"(?=.{1,253}(?::|$))(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}(?::[0-9]{1,5})?")


class VerifyError(Exception):
    pass


def norm_host(host):
    """host[:port] klein; nur DNS-Namen mit mindestens einem Punkt. -> (name, port) oder VerifyError."""
    h = str(host or "").strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(h):
        raise VerifyError("host")
    name, _, port = h.partition(":")
    port = int(port) if port else 443
    if not 0 < port < 65536:
        raise VerifyError("host")
    try:
        ipaddress.ip_address(name)
        raise VerifyError("host")         # IP-Literal (z. B. 1.2.3.4 passt sonst nicht, aber sicher ist sicher)
    except ValueError:
        pass
    return name, port


def public_addrs(name, port):
    """Alle Adressen aufloesen; eine nicht-oeffentliche -> VerifyError (kein Teil-Abruf)."""
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise VerifyError("dns")
    out = []
    for fam, _, _, _, sa in infos:
        ip = ipaddress.ip_address(sa[0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise VerifyError("address")
        out.append((fam, str(ip)))
    if not out:
        raise VerifyError("dns")
    return out


class _Pinned(http.client.HTTPSConnection):
    """HTTPS zu einer vorher geprueften Adresse, SNI/Zertifikat fuer den Namen."""

    def __init__(self, name, ip, fam, port, timeout):
        super().__init__(name, port, timeout=timeout, context=ssl.create_default_context())
        self._ip, self._fam = ip, fam

    def connect(self):
        s = socket.socket(self._fam, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect((self._ip, self.port))
            self.sock = self._context.wrap_socket(s, server_hostname=self.host)
        except BaseException:
            s.close()
            raise


def _read(conn, host_hdr, deadline):
    conn.putrequest("GET", PATH, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", host_hdr)
    conn.putheader("Accept", "application/json")
    conn.putheader("User-Agent", "saga-push-relay/1")
    conn.putheader("Connection", "close")
    conn.endheaders()
    r = conn.getresponse()
    if r.status != 200:
        raise VerifyError(f"http {r.status}")
    data = b""
    while len(data) <= MAX:
        if time.monotonic() > deadline:
            raise VerifyError("timeout")
        chunk = r.read(1024)
        if not chunk:
            break
        data += chunk
    if len(data) > MAX:
        raise VerifyError("size")
    return data


def fetch(host, test_base=""):
    """-> dict der Antwort (Pruefung des Schluessels macht check)."""
    name, port = norm_host(host)
    host_hdr = name if port == 443 else f"{name}:{port}"
    deadline = time.monotonic() + TIMEOUT
    if test_base:
        u = urlparse(test_base)
        if u.scheme != "http" or u.hostname not in ("127.0.0.1", "::1", "localhost"):
            raise VerifyError("test base")
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=TIMEOUT)
    else:
        fam, ip = public_addrs(name, port)[0]
        conn = _Pinned(name, ip, fam, port, TIMEOUT)
    try:
        data = _read(conn, host_hdr, deadline)
    except VerifyError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError) as ex:
        raise VerifyError(type(ex).__name__)
    finally:
        conn.close()
    try:
        d = json.loads(data)
    except ValueError:
        raise VerifyError("json")
    if not isinstance(d, dict):
        raise VerifyError("json")
    return d


def check(host, pub_b64u, test_base=""):
    """True, wenn host den Schluessel bestaetigt; sonst (False, grund)."""
    try:
        d = fetch(host, test_base)
    except VerifyError as ex:
        return False, str(ex)[:40]
    if d.get("v") != 1 or d.get("key") != pub_b64u:
        return False, "key"
    return True, ""
