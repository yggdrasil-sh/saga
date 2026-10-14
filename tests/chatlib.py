"""Chat-Kryptografie Format v1 in Python (PyNaCl = libsodium) - Gegenstueck zu static/chat-crypto.js.

Hilfsmodul (keine Testreihe) fuer tests/chat.py, tools/chat_vectors.py und die Browser-Pruefung; zugleich Referenz fuer die
Linux-App. Nur libsodium-Primitive: crypto_sign (Ed25519), crypto_box/crypto_box_seal (X25519), crypto_pwhash (Argon2id13),
crypto_aead_xchacha20poly1305_ietf, crypto_generichash (BLAKE2b). PyNaCl gepinnt: 1.6.2."""
import base64
import json
import struct
import time

import nacl.bindings as nb
import nacl.hash
import nacl.pwhash
import nacl.utils
from nacl.encoding import RawEncoder

V = "saga-chat/v1/"
OPS, MEM = 2, 67108864
A32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def rnd(n):
    return nacl.utils.random(n)          # randombytes_buf


def b64(b):
    return base64.b64encode(b).decode()


def unb64(s, n=None):
    r = base64.b64decode(s, validate=True)
    if n is not None and len(r) != n:
        raise ValueError("length")
    return r


def lp(*fields):
    out = b""
    for f in fields:
        b = f.encode("utf-8") if isinstance(f, str) else bytes(f)
        out += struct.pack(">I", len(b)) + b
    return out


def gh(data, n=32):
    return nacl.hash.blake2b(data, digest_size=n, encoder=RawEncoder)


def hexid():
    return rnd(16).hex()


def sign(msg, sk):
    return nb.crypto_sign(msg, sk)[:64]


def verify(sig, msg, pk):
    try:
        nb.crypto_sign_open(sig + msg, pk)
        return True
    except Exception:  # noqa: BLE001 - BadSignatureError
        return False


# --- Schluessel -------------------------------------------------------------------------------------------
def identity(ident_seed=None, backup_seed=None):
    ipk, isk = nb.crypto_sign_seed_keypair(ident_seed) if ident_seed else nb.crypto_sign_keypair()
    bpk, bsk = nb.crypto_box_seed_keypair(backup_seed) if backup_seed else nb.crypto_box_keypair()
    return {"ident_pk": ipk, "ident_sk": isk, "backup_pk": bpk, "backup_sk": bsk}


def device(did=None, enc_seed=None, sign_seed=None):
    epk, esk = nb.crypto_box_seed_keypair(enc_seed) if enc_seed else nb.crypto_box_keypair()
    spk, ssk = nb.crypto_sign_seed_keypair(sign_seed) if sign_seed else nb.crypto_sign_keypair()
    return {"id": did or hexid(), "enc_pk": epk, "enc_sk": esk, "sign_pk": spk, "sign_sk": ssk}


def backup_msg(uid, backup_pk):
    return lp(V + "backup", uid, backup_pk)


def device_msg(uid, did, enc_pk, sign_pk):
    return lp(V + "device", uid, did, enc_pk, sign_pk)


def device_api(uid, ident, dev, name="Testgeraet"):
    """Geraet fuer die API ({id, name, enc_pub, sign_pub, sig})."""
    return {"id": dev["id"], "name": name, "enc_pub": b64(dev["enc_pk"]), "sign_pub": b64(dev["sign_pk"]),
            "sig": b64(sign(device_msg(uid, dev["id"], dev["enc_pk"], dev["sign_pk"]), ident["ident_sk"]))}


# --- Wiederherstellungsschluessel und Buendel -----------------------------------------------------------------
def base32(data):
    bits = val = 0
    out = ""
    for x in data:
        val = (val << 8) | x
        bits += 8
        while bits >= 5:
            out += A32[(val >> (bits - 5)) & 31]
            bits -= 5
    if bits:
        out += A32[(val << (5 - bits)) & 31]
    return out


def norm_recovery(s):
    r = "".join(str(s).upper().split()).replace("-", "").replace("0", "O").replace("1", "I").replace("8", "B")
    return r if len(r) == 32 and all(ch in A32 for ch in r) else None


def recovery_key(raw=None):
    k = base32(raw or rnd(20))
    return "-".join(k[i:i + 4] for i in range(0, 32, 4))


def recovery_kdf(rk, salt, ops=OPS, mem=MEM):
    return nacl.pwhash.argon2id.kdf(32, norm_recovery(rk).encode(), salt, opslimit=ops, memlimit=mem)


def bundle_plain(ident_sk, backup_sk):
    return json.dumps({"v": 1, "ident_sk": b64(ident_sk), "backup_sk": b64(backup_sk)}, separators=(",", ":"))


def seal_bundle(uid, rk, ident, salt=None, nonce=None, ops=OPS, mem=MEM, plain=None):
    salt, nonce = salt or rnd(16), nonce or rnd(24)
    key = recovery_kdf(rk, salt, ops, mem)
    ct = nb.crypto_aead_xchacha20poly1305_ietf_encrypt((plain or bundle_plain(ident["ident_sk"], ident["backup_sk"])).encode(),
                                                       lp(V + "bundle", uid), nonce, key)
    return json.dumps({"v": 1, "kdf": "argon2id13", "ops": ops, "mem": mem, "salt": b64(salt), "nonce": b64(nonce), "ct": b64(ct)}, separators=(",", ":"))


def parse_plain(pt):
    d = json.loads(pt)
    isk, bsk = unb64(d["ident_sk"], 64), unb64(d["backup_sk"], 32)
    return {"ident_sk": isk, "ident_pk": isk[32:], "backup_sk": bsk, "backup_pk": nb.crypto_scalarmult_base(bsk)}


def open_bundle(uid, rk, bundle):
    d = json.loads(bundle) if isinstance(bundle, str) else bundle
    key = recovery_kdf(rk, unb64(d["salt"], 16), d["ops"], d["mem"])
    pt = nb.crypto_aead_xchacha20poly1305_ietf_decrypt(unb64(d["ct"]), lp(V + "bundle", uid), unb64(d["nonce"], 24), key)
    return parse_plain(pt)


def setup_body(uid, ident, dev, rk=None, ops=OPS, mem=MEM, name="Testgeraet"):
    """Body fuer PUT /api/chat/identity."""
    rk = rk or recovery_key()
    return {"ident_pub": b64(ident["ident_pk"]), "backup_pub": b64(ident["backup_pk"]),
            "backup_sig": b64(sign(backup_msg(uid, ident["backup_pk"]), ident["ident_sk"])),
            "bundle": seal_bundle(uid, rk, ident, ops=ops, mem=mem), "device": device_api(uid, ident, dev, name)}


# --- Nachrichten ----------------------------------------------------------------------------------------------
def msg_ad(chat_id, mid, sender, dev_id, ts, reply_to):
    return lp(V + "msg", str(chat_id), mid, sender, dev_id, str(ts), reply_to or "")


def sig_msg(chat_id, mid, sender, dev_id, ts, reply_to, body_ct):
    return lp(V + "sig", str(chat_id), mid, sender, dev_id, str(ts), reply_to or "", gh(unb64(body_ct)))


def encrypt(chat_id, sender, dev, body, recipients, reply_to=None, mid=None, ts=None, key=None, nonce=None, plain=None):
    """recipients: {empfaenger-id: x25519_pk} -> Body fuer POST /api/chats/{id}/messages."""
    mid = mid or hexid()
    ts = int(time.time() * 1000) if ts is None else ts
    key, nonce = key or rnd(32), nonce or rnd(24)
    pt = (plain if plain is not None else json.dumps(body)).encode()
    ct = nb.crypto_aead_xchacha20poly1305_ietf_encrypt(pt, msg_ad(chat_id, mid, sender, dev["id"], ts, reply_to), nonce, key)
    body_ct = b64(nonce + ct)
    return {"id": mid, "ts": ts, "sender_device": dev["id"], "body_ct": body_ct, "reply_to": reply_to,
            "sig": b64(sign(sig_msg(chat_id, mid, sender, dev["id"], ts, reply_to, body_ct), dev["sign_sk"])),
            "keys": {r: b64(nb.crypto_box_seal(key, pk)) for r, pk in recipients.items()}, "_key": key}


def recipients_of(api_recipients):
    """Antwort von /recipients -> {empfaenger: pk}"""
    out = {}
    for r in api_recipients:
        if r.get("ready"):
            out["backup:" + r["username"]] = unb64(r["backup_pub"], 32)
            for d in r["devices"]:
                out[d["id"]] = unb64(d["enc_pub"], 32)
    return out


def decrypt(chat_id, m, pk, sk):
    key = nb.crypto_box_seal_open(unb64(m["key"], 80), pk, sk)
    raw = unb64(m["body_ct"])
    pt = nb.crypto_aead_xchacha20poly1305_ietf_decrypt(raw[24:], msg_ad(chat_id, m["id"], m["sender"]["username"], m["sender_device"],
                                                                          m["ts"], m.get("reply_to")), raw[:24], key)
    return json.loads(pt)


def encrypt_file(chat_id, data, key=None, nonce=None):
    key, nonce = key or rnd(32), nonce or rnd(24)
    return nonce + nb.crypto_aead_xchacha20poly1305_ietf_encrypt(data, lp(V + "file", str(chat_id)), nonce, key), key


def decrypt_file(chat_id, blob, key):
    return nb.crypto_aead_xchacha20poly1305_ietf_decrypt(blob[24:], lp(V + "file", str(chat_id)), blob[:24], key)


def link_code(uid, did, enc_pk, sign_pk):
    h = gh(lp(V + "link", uid, did, enc_pk, sign_pk))
    # 20 Ziffern: ein kurzer Code liesse sich vom Server mit eigenen Schluesseln nachbauen (grinding)
    return " ".join("%05d" % (int.from_bytes(h[i * 5:i * 5 + 5], "big") % 100000) for i in range(4))


def safety_number(uid_a, ident_a, uid_b, ident_b):
    a, b = (uid_a.encode(), ident_a), (uid_b.encode(), ident_b)
    if a[0] > b[0]:
        a, b = b, a
    h = gh(lp(V + "safety", a[0], a[1], b[0], b[1]), 64)
    return " ".join("%05d" % (int.from_bytes(h[i * 5:i * 5 + 5], "big") % 100000) for i in range(12))
