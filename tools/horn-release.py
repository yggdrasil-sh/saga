#!/usr/bin/env python3
"""Gjallarhorn-Release-Signatur (Saga 2.5, Paket P6; Gegenstueck gjallarhorn/release.go).

  python3 tools/horn-release.py keygen                 neues Ed25519-Paar: "secret: ..." (CI-Secret HORN_RELEASE_KEY)
                                                       und "pub: ..." (in gjallarhorn/release.pub eintragen)
  HORN_RELEASE_KEY=... python3 tools/horn-release.py sign <version> <datei> [<arch>]
                                                       schreibt <datei>.sig; arch Standard aus dem Dateinamen
                                                       (gjallarhorn-linux-amd64 -> linux-amd64)
  python3 tools/horn-release.py verify <pub> <version> <datei> [<arch>]

Signiert wird "gjallarhorn-release/1\\n<version>\\n<arch>\\n<sha256 hex>\\n"; .sig = Base64 der 64 Byte.
Den geheimen Schluessel nie ins Repo, nie in Logs: nur als GitHub-Secret (Actions) bzw. offline."""
import base64
import hashlib
import os
import re
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

DOMAIN = "gjallarhorn-release/1"


def msg(ver, arch, data):
    return f"{DOMAIN}\n{ver}\n{arch}\n{hashlib.sha256(data).hexdigest()}\n".encode()


def arch_of(path, arch):
    if arch:
        return arch
    m = re.search(r"(linux-(?:amd64|arm64))$", os.path.basename(path))
    if not m:
        sys.exit("arch angeben (linux-amd64 | linux-arm64)")
    return m.group(1)


def pub_b64(sk):
    return base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def main(a):
    if a[:1] == ["keygen"]:
        seed = os.urandom(32)
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        print("secret:", base64.b64encode(seed).decode())
        print("pub:", pub_b64(sk))
        return 0
    if a[:1] == ["sign"] and len(a) in (3, 4):
        seed = base64.b64decode(os.environ.get("HORN_RELEASE_KEY", ""), validate=True)
        if len(seed) != 32:
            sys.exit("HORN_RELEASE_KEY: 32 Byte Base64 erwartet")
        sk = Ed25519PrivateKey.from_private_bytes(seed)
        data = open(a[2], "rb").read()
        sig = sk.sign(msg(a[1], arch_of(a[2], a[3] if len(a) == 4 else ""), data))
        with open(a[2] + ".sig", "w") as f:
            f.write(base64.b64encode(sig).decode() + "\n")
        print(f"{a[2]}.sig (pub {pub_b64(sk)})")
        return 0
    if a[:1] == ["verify"] and len(a) in (4, 5):
        pk = Ed25519PublicKey.from_public_bytes(base64.b64decode(a[1]))
        data = open(a[3], "rb").read()
        sig = base64.b64decode(open(a[3] + ".sig").read().strip())
        pk.verify(sig, msg(a[2], arch_of(a[3], a[4] if len(a) == 5 else ""), data))
        print("ok")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
