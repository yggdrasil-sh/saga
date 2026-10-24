"""Schutz gegen SSRF ueber DNS-Rebinding fuer Abrufe benutzerdefinierter Adressen (CalDAV, Immich).

Vorher wurde der Name geprueft (getaddrinfo) und danach von httpx ein ZWEITES Mal aufgeloest - dazwischen kann
ein boesartiger DNS-Server auf eine interne Adresse umschalten. Hier wird beim Verbindungsaufbau EINMAL aufgeloest,
jede Adresse geprueft und genau mit der gepruefften IP verbunden ("Pinning"). TLS (SNI, Zertifikat) laeuft weiter
gegen den Namen aus der URL. Keine Abhaengigkeit von anderen Saga-Modulen (hkcal nutzt es ebenfalls).
"""
import ipaddress
import os
import socket

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
        emb = [a.sixtofour] if a.sixtofour else []
        if a in ipaddress.ip_network("64:ff9b::/96"):
            emb.append(ipaddress.IPv4Address(int(a) & 0xFFFFFFFF))
        if any(not x.is_global for x in emb):
            return False
    return a.is_global


# Testwiederherstellung auf einem zweiten Server (SAGA_RESTORE_TEST=1, saga/backups.py): ein zurueckgespieltes Backup
# enthaelt die Zugangsdaten der echten Instanz (Kalender, KI, Immich, Avatare ...) - ueber netguard geht dann nichts raus.
RESTORE_TEST = os.environ.get("SAGA_RESTORE_TEST", "").strip().lower() in ("1", "true", "yes", "on")


class Blocked(httpcore.ConnectError):
    """Ziel-Adresse nicht erlaubt (intern ohne Freigabe)."""


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
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
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
