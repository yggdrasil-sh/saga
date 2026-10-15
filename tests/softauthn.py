"""Software-Authenticator NUR fuer Tests (nicht im Produktcode): spielt Browser + Passkey-Geraet nach.

create() beantwortet Optionen von navigator.credentials.create (Attestation "none", ES256), get() die von
navigator.credentials.get - jeweils als JSON wie PublicKeyCredential.toJSON() im Browser. Mit origin/rp_id lassen sich
falsche Adressen nachstellen, mit uv=False ein Geraet ohne Benutzerpruefung, mit count_step=0 ein kopierter Schluessel.
Braucht cryptography und cbor2 (beides kommt mit py_webauthn)."""
import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    def __init__(self, uv=True):
        self.uv, self.creds = uv, {}           # Kennung (bytes) -> {key, rp, uh, count}

    def create(self, options, origin, rp_id=None):
        rp = rp_id or options["rp"]["id"]
        key = ec.generate_private_key(ec.SECP256R1())
        cid = os.urandom(32)
        n = key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        flags = 0x01 | (0x04 if self.uv else 0) | 0x40                      # UP, UV, AT (Schluessel folgt)
        auth_data = (hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + b"\0" * 16
                     + struct.pack(">H", len(cid)) + cid + cose)
        client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": origin, "crossOrigin": False}).encode()
        self.creds[cid] = {"key": key, "rp": rp, "uh": options["user"]["id"], "count": 0}
        return {"id": b64e(cid), "rawId": b64e(cid), "type": "public-key", "authenticatorAttachment": "platform",
                "clientExtensionResults": {},
                "response": {"clientDataJSON": b64e(client), "transports": ["internal", "hybrid"],
                             "attestationObject": b64e(cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data}))}}

    def get(self, options, origin, cred=None, rp_id=None, uv=None, count_step=1):
        allow = [c["id"] for c in options.get("allowCredentials") or []]
        cid = b64d(cred) if cred else next(c for c in self.creds if not allow or b64e(c) in allow)
        c = self.creds[cid]
        rp = rp_id or options.get("rpId") or c["rp"]
        c["count"] += count_step
        flags = 0x01 | (0x04 if (self.uv if uv is None else uv) else 0)
        auth_data = hashlib.sha256(rp.encode()).digest() + bytes([flags]) + struct.pack(">I", c["count"])
        client = json.dumps({"type": "webauthn.get", "challenge": options["challenge"], "origin": origin, "crossOrigin": False}).encode()
        sig = c["key"].sign(auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64e(cid), "rawId": b64e(cid), "type": "public-key", "authenticatorAttachment": "platform",
                "clientExtensionResults": {},
                "response": {"clientDataJSON": b64e(client), "authenticatorData": b64e(auth_data), "signature": b64e(sig),
                             "userHandle": c["uh"]}}
