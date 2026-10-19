"""Chat-Krypto-Format saga-chat/v2 (2.3) - Server-Referenz fuer Chats ueber Servergrenzen.

v1 (saga/chat.py) bindet Schluessel und Nachrichten an den lokalen Anmeldenamen und die lokale chat_id - das geht ueber
Instanzgrenzen nicht (Login-Namen bleiben intern, chat_ids sind je Server). v2 bindet an den stabilen, pseudonymen
foederierten Handle ('~' + 16 Zeichen '@' host, saga.federation.own_handle) und eine globale Chat-Kennung chat_ref.
Lokale Chats bleiben v1 - unveraendert.

  B64 = Standard-Base64 mit Padding; LP(f1..fn) = je Feld uint32_be(len) || bytes (Strings UTF-8); V2 = "saga-chat/v2/"
  handle          '~' + 16 Zeichen [a-z2-7] + '@' + host (host ggf. mit :port), z. B. '~abcdefghijklmnop@b.example'
  chat_ref        32 Hex-Zeichen + '@' + Host des Heimservers des Chats
  Handle-Zert.    sig = sign_detached(LP(V2+"handle", handle, ident_pk, backup_pk), ident_sk)
  Geraete-Zert.   fed_sig = sign_detached(LP(V2+"device", handle, device_id, enc_pk, sign_pk), ident_sk)
  Nachricht       body_ct = B64(nonce24 || xchacha20poly1305_ietf(inhalt, AD, nonce, K)),
                  AD  = LP(V2+"msg", chat_ref, msg_id, sender_handle, sender_device, ts, reply_to|"", ai|"")
                  sig = sign_detached(LP(V2+"sig", chat_ref, msg_id, sender_handle, sender_device, ts, reply_to|"", ai|"",
                        generichash32(raw(body_ct))), device_sign_sk)
                  ai = "" oder Hex-SHA-256 des Textes einer KI-Antwort (mitsigniert)
  Empfaenger      K mit crypto_box_seal je Geraet ('dev:<device_id>@<host>') und je Mitglied ('backup:<handle>')
  Datei           AD = LP(V2+"file", chat_ref)
  Sicherheitsnr.  beide (handle, ident_pk) nach handle (UTF-8-Bytes) sortiert; h = generichash64(LP(V2+"safety", h_a, i_a,
                  h_b, i_b)); 12 Bloecke: je 5 Bytes big-endian mod 100000, fuenfstellig
Testvektoren: tests/vectors/chat_v2.json (tools/chat_vectors_v2.py, Gegenproben node tools/chat_vectors_v2.mjs und
Browser). Hier nur, was der Server braucht: Zertifikate pruefen (cryptography, kompatibel zu crypto_sign_detached),
Nachrichten-Signaturen pruefen, Sicherheitsnummer (Referenz)."""
import base64
import binascii
import hashlib
import re
import struct

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

V2 = "saga-chat/v2/"
HANDLE_RE = re.compile(r"~[a-z2-7]{16}@[a-z0-9.:-]{1,260}")
CHAT_REF_RE = re.compile(r"[0-9a-f]{32}@[a-z0-9.:-]{1,260}")
DEVICE_RE = re.compile(r"[0-9a-f]{32}")


# Eigenstaendig (ohne Saga-Konfiguration): auch tools/chat_vectors_v2.py und die Testreihe laden dieses Modul direkt.
def lp(*fields):
    out = b""
    for f in fields:
        b = f.encode("utf-8") if isinstance(f, str) else bytes(f)
        out += struct.pack(">I", len(b)) + b
    return out


def b64d(s, n=None):
    if not isinstance(s, str) or not s or len(s) > 200000:
        return None
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if n is None or len(raw) == n else None


def verify(pub_b64, sig_b64, msg):
    """Ed25519-Signatur (crypto_sign_detached) pruefen."""
    pk, sig = b64d(pub_b64, 32), b64d(sig_b64, 64)
    if pk is None or sig is None:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(pk).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def gh(data, n=32):
    return hashlib.blake2b(data, digest_size=n).digest()          # = crypto_generichash ohne Schluessel


def handle_msg(handle, ident_pk, backup_pk):
    return lp(V2 + "handle", handle, ident_pk, backup_pk)


def device_msg(handle, device_id, enc_pk, sign_pk):
    return lp(V2 + "device", handle, device_id, enc_pk, sign_pk)


def msg_ad(chat_ref, msg_id, sender_handle, sender_device, ts, reply_to=None, ai=None):
    return lp(V2 + "msg", chat_ref, msg_id, sender_handle, sender_device, str(ts), reply_to or "", ai or "")


def sig_msg(chat_ref, msg_id, sender_handle, sender_device, ts, reply_to, ai, body_ct):
    return lp(V2 + "sig", chat_ref, msg_id, sender_handle, sender_device, str(ts), reply_to or "", ai or "",
              gh(b64d(body_ct) or b""))


def file_ad(chat_ref):
    return lp(V2 + "file", chat_ref)


def device_recipient(device_id, host):
    return f"dev:{device_id}@{host}"


def backup_recipient(handle):
    return "backup:" + handle


def verify_handle(handle, ident_pub, backup_pub, sig):
    pk, bk = b64d(ident_pub, 32), b64d(backup_pub, 32)
    return bool(HANDLE_RE.fullmatch(handle or "") and pk and bk and verify(ident_pub, sig, handle_msg(handle, pk, bk)))


def verify_device(handle, ident_pub, device_id, enc_pub, sign_pub, fed_sig):
    ek, sk = b64d(enc_pub, 32), b64d(sign_pub, 32)
    return bool(DEVICE_RE.fullmatch(device_id or "") and ek and sk and verify(ident_pub, fed_sig, device_msg(handle, device_id, ek, sk)))


def verify_message(sign_pub, chat_ref, m):
    """m: {id, ts, sender_handle, sender_device, reply_to, ai, body_ct, sig}"""
    return verify(sign_pub, m.get("sig"), sig_msg(chat_ref, m["id"], m["sender_handle"], m["sender_device"], m["ts"], m.get("reply_to"),
                                                  m.get("ai"), m["body_ct"]))


def safety_number(handle_a, ident_a, handle_b, ident_b):
    a, b = (handle_a.encode(), bytes(ident_a)), (handle_b.encode(), bytes(ident_b))
    if a[0] > b[0]:
        a, b = b, a
    h = gh(lp(V2 + "safety", a[0], a[1], b[0], b[1]), 64)
    return " ".join("%05d" % (int.from_bytes(h[i * 5:i * 5 + 5], "big") % 100000) for i in range(12))
