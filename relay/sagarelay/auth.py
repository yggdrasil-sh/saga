"""Signaturpruefung der Server-Anfragen (RFC 9421 ueber saga/fedsig.py, Tag saga-relay/1) und Replay-Schutz."""
from saga import fedsig

TAG = getattr(fedsig, "RELAY_TAG", "saga-relay/1")
REG_KEYID = "reg"
ROTATE_CTX = "saga-relay-rotate/1"


class AuthError(Exception):
    """-> 401 {error:"auth"}; der Text geht nur ins Protokoll."""


def authority(request, cfg):
    return cfg.public_host or fedsig.authority(request.headers.get("host") or "")


def check(request, body, cfg, store, reg_pub=None):
    """-> (keyid, server-dict|None). reg_pub (Rohbytes): Anmeldung, keyid muss "reg" sein und mit diesem Schluessel
    signiert. Sonst keyid = server_id, Schluessel aus der Datenbank."""
    found = {}

    def lookup(keyid):
        if reg_pub is not None:
            if keyid != REG_KEYID:
                raise fedsig.SigError("keyid")
            return reg_pub
        s = store.get(keyid) if isinstance(keyid, str) and keyid.startswith("s_") else None
        if not s:
            raise fedsig.SigError("unknown keyid")
        found["s"] = s
        return fedsig.load_public(s["pub"])

    h = {k.lower(): v for k, v in request.headers.items()}
    try:
        keyid, nonce, _ = fedsig.verify_request(lookup, request.method, authority(request, cfg), request.url.path,
                                                request.url.query, h, body, tag=TAG)
    except fedsig.SigError as ex:
        raise AuthError(str(ex))
    if not store.nonce_fresh(keyid, nonce):
        raise AuthError("replay")
    return keyid, found.get("s")


def rotate_proof_ok(server_id, new_pub_b64u, proof_b64u):
    """proof = Ed25519 des NEUEN Schluessels ueber saga-relay-rotate/1|<server_id>|<new_pub>."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = fedsig.load_public(new_pub_b64u)
        sig = fedsig.unb64u(proof_b64u)
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, f"{ROTATE_CTX}|{server_id}|{new_pub_b64u}".encode())
        return True
    except (fedsig.SigError, InvalidSignature, ValueError, TypeError):
        return False
