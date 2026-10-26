"""Schutz gegen SSRF ueber DNS-Rebinding fuer Abrufe benutzerdefinierter Adressen (CalDAV, Immich).

Vorher wurde der Name geprueft (getaddrinfo) und danach von httpx ein ZWEITES Mal aufgeloest - dazwischen kann
ein boesartiger DNS-Server auf eine interne Adresse umschalten. Hier wird beim Verbindungsaufbau EINMAL aufgeloest,
jede Adresse geprueft und genau mit der gepruefften IP verbunden ("Pinning"). TLS (SNI, Zertifikat) laeuft weiter
gegen den Namen aus der URL. Keine Abhaengigkeit von anderen Saga-Modulen (hkcal nutzt es ebenfalls).
"""
import concurrent.futures
import ipaddress
import os
import socket
import threading

import httpcore
import httpx


def is_public(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.version == 6:                                    # eingebettete IPv4 (NAT64 64:ff9b::/96, 6to4 2002::/16) mitpruefen
        if a in ipaddress.ip_network("::/96"):            # IPv4-kompatibel (::127.0.0.1, veraltet): gilt in Python als global
            return False
        emb = [a.sixtofour] if a.sixtofour else []
        if a in ipaddress.ip_network("64:ff9b::/96"):
            emb.append(ipaddress.IPv4Address(int(a) & 0xFFFFFFFF))
        if any(not x.is_global for x in emb):
            return False
    return a.is_global and not a.is_multicast         # 224.0.0.0/4 und ff00::/8 gelten in Python teils als global


# Testwiederherstellung auf einem zweiten Server (SAGA_RESTORE_TEST=1, saga/backups.py): ein zurueckgespieltes Backup
# enthaelt die Zugangsdaten der echten Instanz (Kalender, KI, Immich, Avatare ...) - ueber netguard geht dann nichts raus.
RESTORE_TEST = os.environ.get("SAGA_RESTORE_TEST", "").strip().lower() in ("1", "true", "yes", "on")


class Blocked(httpcore.ConnectError):
    """Ziel-Adresse nicht erlaubt (intern ohne Freigabe)."""


# DNS mit Zeitlimit: socket.getaddrinfo kennt keines (der Resolver wartet je nach resolv.conf 5 s x Versuche x Server) -
# ohne Ausgang bzw. bei einem haengenden DNS-Server hielte jeder Abruf so lange einen Thread des Threadpools fest.
# Aufgeloest wird in eigenen Threads; wer laenger als DNS_TIMEOUT wartet, bekommt httpcore.ConnectTimeout (httpx:
# ConnectTimeout). Der haengende Aufruf laeuft im Hintergrund zu Ende; hoechstens DNS_WORKERS gleichzeitig, weitere
# warten in der Schlange - auch dort zaehlt das Zeitlimit ab dem Einreihen.
DNS_TIMEOUT = 3.0
DNS_WORKERS = 8
_dns_pool = None
_dns_lock = threading.Lock()


def _pool():
    global _dns_pool
    with _dns_lock:
        if _dns_pool is None:
            _dns_pool = concurrent.futures.ThreadPoolExecutor(DNS_WORKERS, thread_name_prefix="netguard-dns")
        return _dns_pool


def resolve(host, port, timeout=DNS_TIMEOUT):
    """getaddrinfo(host, port, SOCK_STREAM) mit Zeitlimit -> Liste wie getaddrinfo.
    OSError/UnicodeError wie getaddrinfo; Zeitueberschreitung: httpcore.ConnectTimeout."""
    fut = _pool().submit(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()                                      # noch in der Schlange: gar nicht erst aufloesen
        raise httpcore.ConnectTimeout(f"{host}: DNS-Zeitlimit ({timeout:g} s)") from None


def lookup(host, port=None, timeout=None, **kw):
    """socket.getaddrinfo mit Zeitlimit fuer Vorab-Pruefungen (gleiche Argumente; Zeitueberschreitung: socket.gaierror
    EAI_AGAIN - die Aufrufer fangen OSError bzw. gaierror wie bei "nicht aufloesbar"). Bughunt 3: ai, immich, calendars,
    push, nextcloud, fed_chat, fed_horn und horn_ops loesten ohne Zeitlimit auf."""
    timeout = DNS_TIMEOUT if timeout is None else timeout
    fut = _pool().submit(socket.getaddrinfo, host, port, **kw)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise socket.gaierror(socket.EAI_AGAIN, f"{host}: DNS-Zeitlimit ({timeout:g} s)") from None


# Ausnahme fuer die Testwiederherstellung: das LESEN vom eingetragenen Backup-Ziel (run.py backup-holen, restore
# --schluessel, backup-pruefen) - nur fuer dessen Host(s) und nur mit diesen Methoden (S3: GET auch fuer Listen).
RESTORE_READ_METHODS = frozenset(("GET", "HEAD", "PROPFIND"))


class GuardBackend(httpcore.SyncBackend):
    """ip_ok(ip, host, port) -> bool. Alle Adressen des Namens muessen erlaubt sein; verbunden wird mit der ersten.
    restore_hosts: Hosts, die trotz SAGA_RESTORE_TEST erreichbar bleiben (nur der lesende Backup-Client setzt sie)."""

    def __init__(self, ip_ok, restore_hosts=None):
        super().__init__()
        self.ip_ok = ip_ok
        self.restore_hosts = frozenset(str(h).lower() for h in (restore_hosts or ()) if h)

    def connect_tcp(self, host, port, *args, **kw):
        if RESTORE_TEST and str(host).lower() not in self.restore_hosts:
            raise Blocked(f"Ziel {host}: SAGA_RESTORE_TEST=1")
        try:
            ct = kw.get("timeout", args[0] if args else None)       # Verbindungs-Zeitlimit des Clients, falls kuerzer
            infos = resolve(host, port, min(DNS_TIMEOUT, ct) if ct else DNS_TIMEOUT)
        except (OSError, UnicodeError) as ex:
            raise httpcore.ConnectError(str(ex))
        ips = []
        for i in infos:
            ip = i[4][0]
            if not self.ip_ok(ip, host, port):
                raise Blocked(f"Ziel {host} ({ip}) nicht freigegeben")
            if ip not in ips:
                ips.append(ip)
        if not ips:
            raise httpcore.ConnectError(f"{host}: keine Adresse")
        last = None
        for ip in ips[:3]:                                # wie create_connection: naechste Adresse, wenn eine nicht antwortet
            try:
                return super().connect_tcp(ip, port, *args, **kw)
            except httpcore.ConnectError as ex:
                last = ex
        raise last


class _ReadOnlyTransport(httpx.BaseTransport):
    """Testwiederherstellung: nur lesende Methoden an die freigegebenen Hosts, alles andere wie gesperrt."""

    def __init__(self, inner, hosts):
        self.inner = inner
        self.hosts = hosts

    def handle_request(self, request):
        host = (request.url.host or "").lower()
        if request.method.upper() not in RESTORE_READ_METHODS or host not in self.hosts:
            raise httpx.ConnectError(f"Ziel {host}: SAGA_RESTORE_TEST=1 ({request.method} nicht freigegeben)",
                                     request=request) from Blocked(f"Ziel {host}: SAGA_RESTORE_TEST=1")
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


def guarded_client(ip_ok, transport_kw=None, restore_ok=False, restore_hosts=(), **kw):
    """httpx.Client, dessen Verbindungen ueber GuardBackend laufen. transport_kw: z. B. {"verify": ssl-kontext}
    (httpx beachtet verify am Client nicht, wenn ein eigener Transport gesetzt ist).
    restore_ok=True (nur der lesende Backup-Client der Kommandozeile): bei SAGA_RESTORE_TEST=1 bleiben GET/HEAD/PROPFIND
    an restore_hosts (das eingetragene Backup-Ziel) erlaubt; ohne restore_ok bleibt alles gesperrt."""
    tr = httpx.HTTPTransport(**(transport_kw or {}))
    pool = getattr(tr, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):          # httpcore-Interna geaendert: lieber gar nicht verbinden
        raise RuntimeError("netguard: httpcore ohne _network_backend - SSRF-Schutz nicht moeglich")
    hosts = frozenset(str(h).lower() for h in (restore_hosts or ()) if h) if restore_ok else frozenset()
    pool._network_backend = GuardBackend(ip_ok, hosts)
    if RESTORE_TEST and hosts:
        tr = _ReadOnlyTransport(tr, hosts)
    return httpx.Client(transport=tr, **kw)


def request_checker(ports=(None, 80, 443), any_port=()):
    """event_hook fuer httpx: jede Anfrage (auch nach Weiterleitungen) nur http/https, nur diese Ports (None = Standardport
    des Schemas) und ohne Zugangsdaten in der URL. any_port: Hosts, fuer die jeder Port gilt (nur Tests)."""
    def check(req):
        u = req.url
        if u.scheme not in ("http", "https") or u.userinfo or (u.port not in ports and u.host not in any_port):
            raise httpx.UnsupportedProtocol("nicht erlaubt")
    return check


def net_why(ex):
    """Netzfehler in Worten statt des Ausnahmenamens (Testbericht 2.5 Nr. 018: "ConnectError" in der Meldung)."""
    s = str(ex)
    if "CERTIFICATE_VERIFY_FAILED" in s or "SSL" in s or "TLS" in s:
        return "TLS-Zertifikat oder -Verbindung fehlerhaft"
    if isinstance(ex, httpx.ConnectError):
        return "Verbindung fehlgeschlagen – Name, Adresse und Port prüfen"
    if isinstance(ex, (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)):
        return "Verbindung abgebrochen"
    if isinstance(ex, httpx.TooManyRedirects):
        return "zu viele Weiterleitungen"
    return "Netzwerkfehler"
