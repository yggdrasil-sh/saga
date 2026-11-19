"""LDAP-Testserver (Attrappe) fuer tests/sso_ldap.py und tests/avatare.py - ersetzt das OpenLDAP-Testbild aus
tools/stack-remote.sh (osixia/openldap: Basis dc=example,dc=org, Admin cn=admin,<basis>). Im Speicher (ldaptor), mit
Bind, Suche, Anlegen, Loeschen, Aendern und StartTLS (Zertifikat aus der Test-CA). Kein Schema - die Reihen legen nur
inetOrgPerson/groupOfNames/organizationalUnit an. Nur fuer Tests, nur auf 127.0.0.1 bzw. im Netz-Namensraum.
Braucht ldaptor/twisted/pyOpenSSL (umgebung.sh einrichten: pip --target $UMG/pylib). Aufruf:
  HK_LDAP_ADMIN_PW=... PYTHONPATH=$UMG/pylib python ldap_attrappe.py --port 389 --cert c.pem --key k.pem"""
import argparse, os, sys

from twisted.internet import reactor, ssl
from twisted.internet.protocol import ServerFactory
from twisted.python import log
from twisted.python.components import registerAdapter
from ldaptor.inmemory import ReadOnlyInMemoryLDAPEntry
from ldaptor.interfaces import IConnectedLDAPEntry
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap.ldapserver import LDAPServer

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=389)
ap.add_argument("--bind", default="127.0.0.1")
ap.add_argument("--base", default="dc=example,dc=org")
ap.add_argument("--cert", required=True)
ap.add_argument("--key", required=True)
ap.add_argument("--debug", action="store_true")
A = ap.parse_args()
PW = os.environ["HK_LDAP_ADMIN_PW"]
STARTTLS = pureldap.LDAPStartTLSRequest.oid


class Server(LDAPServer):
    debug = A.debug

    def extendedRequest_StartTLS(self, data, reply):
        # Antwort noch im Klartext, danach TLS auf derselben Verbindung (RFC 4511 4.14)
        reply(pureldap.LDAPStartTLSResponse(resultCode=0, responseName=STARTTLS))
        self.transport.startTLS(self.factory.tls)
        print("ldap: StartTLS", flush=True)
        return None

    extendedRequest_StartTLS.oid = STARTTLS

    def handle_LDAPBindRequest(self, request, controls, reply):
        print(f"ldap: bind {request.dn!r}", flush=True)
        return super().handle_LDAPBindRequest(request, controls, reply)


class Factory(ServerFactory):
    protocol = Server

    def __init__(self, root, tls):
        self.root, self.tls = root, tls

    def buildProtocol(self, addr):
        p = self.protocol()
        p.factory = self
        return p


registerAdapter(lambda f: f.root, Factory, IConnectedLDAPEntry)


class Entry(ReadOnlyInMemoryLDAPEntry):
    """Wie OpenLDAP: der Wert aus dem RDN (uid=..., cn=...) steht auch als Attribut im Eintrag (sonst findet
    (uid=x) nichts, wenn der Client ihn beim Anlegen nicht mitschickt)."""

    def addChild(self, rdn, attributes):
        attributes = {k: set(v) for k, v in attributes.items()}
        r = rdn.getText() if hasattr(rdn, "getText") else (rdn.decode() if isinstance(rdn, bytes) else str(rdn))
        for part in r.split("+"):
            k, v = part.split("=", 1)
            key = next((x for x in attributes if (x.decode() if isinstance(x, bytes) else x).lower() == k.lower()), k.encode())
            vals = attributes.setdefault(key, set())
            if not any((x.decode() if isinstance(x, bytes) else x).lower() == v.lower() for x in vals):
                vals.add(v.encode())
        return super().addChild(rdn, attributes)


def tree():
    base = A.base.encode()
    dc = A.base.split(",")[0].split("=", 1)[1].encode()
    root = Entry(base, {b"objectClass": [b"top", b"dcObject", b"organization"], b"dc": [dc], b"o": [b"Test"]})
    root.addChild(b"cn=admin", {b"objectClass": [b"simpleSecurityObject", b"organizationalRole"], b"cn": [b"admin"],
                                b"description": [b"LDAP administrator"], b"userPassword": [PW.encode()]})
    return root


if __name__ == "__main__":
    if A.debug:
        log.startLogging(sys.stdout)
    tls = ssl.DefaultOpenSSLContextFactory(A.key, A.cert)
    reactor.listenTCP(A.port, Factory(tree(), tls), interface=A.bind)
    print(f"ldap-attrappe: ldap://{A.bind}:{A.port} Basis {A.base} (StartTLS)", flush=True)
    reactor.run()
