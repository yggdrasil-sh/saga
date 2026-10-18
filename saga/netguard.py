"""Schutz gegen SSRF ueber DNS-Rebinding fuer Abrufe benutzerdefinierter Adressen (CalDAV, Immich).

Vorher wurde der Name geprueft (getaddrinfo) und danach von httpx ein ZWEITES Mal aufgeloest - dazwischen kann
ein boesartiger DNS-Server auf eine interne Adresse umschalten. Hier wird beim Verbindungsaufbau EINMAL aufgeloest,
jede Adresse geprueft und genau mit der gepruefften IP verbunden ("Pinning"). TLS (SNI, Zertifikat) laeuft weiter
gegen den Namen aus der URL. Keine Abhaengigkeit von anderen Saga-Modulen (hkcal nutzt es ebenfalls).
"""
import ipaddress
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


class Blocked(httpcore.ConnectError):
    """Ziel-Adresse nicht erlaubt (intern ohne Freigabe)."""


class GuardBackend(httpcore.SyncBackend):
    """ip_ok(ip, host, port) -> bool. Alle Adressen des Namens muessen erlaubt sein; verbunden wird mit der ersten."""

    def __init__(self, ip_ok):
        super().__init__()
        self.ip_ok = ip_ok

    def connect_tcp(self, host, port, *args, **kw):
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


def guarded_client(ip_ok, transport_kw=None, **kw):
    """httpx.Client, dessen Verbindungen ueber GuardBackend laufen. transport_kw: z. B. {"verify": ssl-kontext}
    (httpx beachtet verify am Client nicht, wenn ein eigener Transport gesetzt ist)."""
    tr = httpx.HTTPTransport(**(transport_kw or {}))
    pool = getattr(tr, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):          # httpcore-Interna geaendert: lieber gar nicht verbinden
        raise RuntimeError("netguard: httpcore ohne _network_backend - SSRF-Schutz nicht moeglich")
    pool._network_backend = GuardBackend(ip_ok)
    return httpx.Client(transport=tr, **kw)
