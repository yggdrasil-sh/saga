#!/usr/bin/env python3
"""Testvektoren fuer Apple-Mitteilungen Ende-zu-Ende und das Push-Relais (saga-push-e2e/1, docs/PUSH-RELAY.md).

  python3 tools/push_e2e_vectors.py gen      tests/vectors/push_e2e.json neu schreiben (feste Seeds -> gleiche Ausgabe)
  python3 tools/push_e2e_vectors.py check    Datei gegen diese unabhaengige Umsetzung pruefen (Exit 1 bei Abweichung)

Bewusst unabhaengig von saga/push.py (nur "cryptography"): RFC 8291 (aes128gcm, RFC 8188) mit Auffuellen, Planung der
Apple-Koepfe (push-type, priority, Ratenstufe), sichtbarer aps-Teil (nur Loc-Keys), saga.k, undurchsichtige collapse-id,
Besitznachweis beim Schluesselwechsel am Relais. Gegenproben: tests/push_relais_vertrag.py (gegen saga.push/saga.fedsig),
die iOS-App (Tests/Logic/PushE2ETests.swift mit einer Kopie dieser Datei) und das Relais (prueft aps/Topics)."""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "vectors", "push_e2e.json")
FORMAT = "saga-push-e2e/1"
RS = 4096
PAD_STEPS, PAD_ROUND, E2E_MAX = (512, 1024, 2048), 512, 2400
COLLAPSE_INFO = "saga-collapse/1|"
ROTATE_INFO = "saga-relay-rotate/1|"
TTL = 4 * 3600
RING_KINDS = ("call", "call.group")
LOC = {"title": "SAGA_PUSH_TITLE", "body": "SAGA_PUSH_BODY", "call": "SAGA_PUSH_CALL"}
P256 = ec.SECP256R1()
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
RFC8291_BODY = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")
NOW = 1798761600                                       # feste "Jetzt"-Zeit der Faelle (expiration = NOW + ttl)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s):
    s = str(s)
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def h32(tag):
    return hashlib.sha256(("saga-push-vector:" + tag).encode()).digest()


def p256_key(tag):
    return ec.derive_private_key(int.from_bytes(h32(tag), "big") % (P256_N - 1) + 1, P256)


def raw_pub(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def scalar(key):
    return key.private_numbers().private_value.to_bytes(32, "big")


# --- RFC 8291 / RFC 8188 ------------------------------------------------------------------------------------------------
def _keys(ua_pub, as_pub, shared, auth, salt):
    ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    return cek, nonce


def pad_size(n):
    need = n + 1
    for s in PAD_STEPS:
        if need <= s:
            return s
    return -(-need // PAD_ROUND) * PAD_ROUND


def encrypt(plain, ua_pub, auth, salt, as_key, pad_to=None):
    as_pub = raw_pub(as_key)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(P256, ua_pub))
    cek, nonce = _keys(ua_pub, as_pub, shared, auth, salt)
    rec = plain + b"\x02"
    if pad_to is not None:
        assert len(plain) + 1 <= pad_to <= RS - 16
        rec += b"\x00" * (pad_to - len(rec))
    return salt + struct.pack("!I", RS) + bytes([len(as_pub)]) + as_pub + AESGCM(cek).encrypt(nonce, rec, None)


def decrypt(body, ua_key, auth):
    """-> (inhalt, laenge des klartext-datensatzes). Fehler: ValueError."""
    if len(body) < 21 + 65 + 17:
        raise ValueError("zu kurz")
    salt, rs, n = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    if n != 65 or rs < 18:
        raise ValueError("kopf")
    as_pub, ct = body[21:21 + n], body[21 + n:]
    if len(ct) > rs:
        raise ValueError("mehr als ein datensatz")
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(P256, as_pub))
    cek, nonce = _keys(raw_pub(ua_key), as_pub, shared, auth, salt)
    rec = AESGCM(cek).decrypt(nonce, ct, None)               # InvalidTag -> Fehler des Aufrufers
    plain = rec.rstrip(b"\x00")
    if not plain.endswith(b"\x02"):
        raise ValueError("kein letzter datensatz")
    return plain[:-1], len(rec)


# --- Planung (wie saga.push._apns_plan) und sichtbarer Teil -------------------------------------------------------------
def plan(msg, voip):
    """-> (push_type, priority, ttl, collapse_tag, rate)"""
    update = bool(msg.get("update")) and not voip
    ring = msg.get("kind") in RING_KINDS
    if voip:
        assert ring, "voip nur fuer klingelnde Anrufe"
        pt, prio, rate = "voip", 10, "ring"
    elif update:
        pt, prio, rate = "background", 5, "bg"
    else:
        pt, prio, rate = "alert", 10, "ring" if ring else "alert"
    return pt, prio, int(msg.get("ttl") or TTL), ("u" if update else "") + (msg.get("tag") or ""), rate


def aps_for(msg, push_type):
    if push_type == "voip":
        return {}
    if push_type == "background":
        return {"content-available": 1}
    ring = msg.get("kind") in RING_KINDS
    aps = {"alert": {"title-loc-key": LOC["title"], "loc-key": LOC["call" if ring else "body"]}, "mutable-content": 1}
    if not msg.get("silent"):
        aps["sound"] = "default"
    if ring:
        aps["interruption-level"] = "time-sensitive"
    return aps


def kid(ua_pub):
    return b64u(hashlib.sha256(ua_pub).digest())[:8]


def collapse(auth, tag):
    return "c" + b64u(hmac.new(auth, (COLLAPSE_INFO + tag).encode(), hashlib.sha256).digest())[:22]


def plain_of(msg):
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()


# --- Faelle ------------------------------------------------------------------------------------------------------------
def _msgs():
    long_body = "Neue Nachricht von Mira Beispiel – " + "ä" * 300
    return [
        ("chat", False, {"title": "Saga", "body": "Neue Nachricht von Mira Beispiel", "url": "/chats#c12", "tag": "chat-12",
                         "kind": "chat.message", "ts": NOW, "v": 2, "lang": "de", "actor": "Mira Beispiel", "n": 3,
                         "group_key": "chat:12", "chat": 12}),
        ("kommentar", False, {"title": "Saga", "body": "Mira Beispiel hat „Reiseplan“ kommentiert", "url": "/n/abc#c5",
                              "tag": "thread-5", "kind": "comment.new", "ts": NOW, "thread": "note-abc", "v": 2, "lang": "de",
                              "actor": "Mira Beispiel", "n": 1, "group_key": "note:abc"}),
        ("anruf_voip", True, {"title": "Saga", "body": "Anruf von Mira Beispiel", "url": "/chats#call-0123456789abcdef",
                              "tag": "call-0123456789abcdef", "kind": "call", "ts": NOW, "call": "0123456789abcdef", "chat": 12,
                              "media": "audio", "caller": "Mira Beispiel", "ttl": 55, "v": 2, "lang": "de",
                              "actor": "Mira Beispiel", "n": 1}),
        ("gruppenanruf_alert", False, {"title": "Saga", "body": "Gruppen-Videoanruf von Mira Beispiel",
                                       "url": "/chats#call-fedcba9876543210", "tag": "call-fedcba9876543210", "kind": "call.group",
                                       "ts": NOW, "call": "fedcba9876543210", "chat": 7, "media": "video", "caller": "Mira Beispiel",
                                       "group": True, "ttl": 50, "v": 2, "lang": "de", "actor": "Mira Beispiel", "n": 1}),
        ("aktualisierung", False, {"title": "Saga", "body": "", "url": "/chats#c12", "tag": "chat-12", "kind": "chat.message",
                                   "ts": NOW, "v": 2, "lang": "de", "n": 0, "group_key": "chat:12", "chat": 12, "update": True,
                                   "removed": True}),
        ("sammelmeldung_still", False, {"title": "Saga", "body": "Während „Nicht stören“: 4 Nachrichten, 1 verpasster Anruf",
                                        "url": "/", "tag": "status-digest", "kind": "status.digest", "ts": NOW, "v": 2, "lang": "de",
                                        "n": 5, "digest": {"messages": 4, "calls": 1, "other": 0}, "silent": True}),
        ("test", False, {"title": "Saga", "body": "Testmitteilung – Mitteilungen kommen an.", "url": "/", "tag": "test",
                         "kind": "test", "ts": NOW, "v": 2, "lang": "de"}),
        ("auftrag_kollegin", False, {"title": "Saga", "body": "Auftrag erledigt", "url": "/", "tag": "job-1", "kind": "job.done",
                                     "ts": NOW, "v": 2, "lang": "de", "actor": "svc:kollegin"}),
        ("gross_1024", False, {"title": "Saga", "body": long_body, "url": "/chats#c99", "tag": "chat-99",
                               "kind": "chat.message", "ts": NOW, "v": 2, "lang": "de", "n": 1, "chat": 99}),
        ("gross_2048", False, {"title": "Saga", "body": "ü" * 700, "url": "/chats#c98", "tag": "chat-98",
                               "kind": "chat.message", "ts": NOW, "v": 2, "lang": "de", "n": 1, "chat": 98}),
        ("gross_2560", False, {"title": "Saga", "body": ("Ö" * 1140), "url": "/", "tag": "n-1", "kind": "comment.reply",
                               "ts": NOW, "v": 2, "lang": "de", "n": 1}),
    ]


def build():
    ua = p256_key("ua")
    ua_pub = raw_pub(ua)
    auth = h32("auth")[:16]
    out = {"format": FORMAT,
           "note": "Erzeugt von tools/push_e2e_vectors.py gen - nicht von Hand aendern. Vertrag: docs/PUSH-RELAY.md.",
           "params": {"rs": RS, "pad_steps": list(PAD_STEPS), "pad_round": PAD_ROUND, "e2e_max": E2E_MAX, "ttl_default": TTL,
                      "collapse_info": COLLAPSE_INFO, "rotate_info": ROTATE_INFO, "now": NOW, "loc_keys": LOC,
                      "ring_kinds": list(RING_KINDS)},
           "keys": {"ua_priv": b64u(scalar(ua)), "ua_pub": b64u(ua_pub), "auth": b64u(auth), "k": kid(ua_pub)}}
    # RFC 8291 Anhang A (ohne Auffuellen) - unveraendert
    rfc_ua = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
    rfc_as = ec.derive_private_key(int.from_bytes(unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), P256)
    out["rfc8291"] = {"plain": "When I grow up, I want to be a watermelon", "ua_pub": rfc_ua, "ua_priv": "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
                      "auth": "BTBZMqHH6r4Tts7J_aSIgg", "salt": "DGv6ra1nlYgDCS1FRnbzlw", "as_priv": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
                      "body": b64u(encrypt(b"When I grow up, I want to be a watermelon", unb64u(rfc_ua), unb64u("BTBZMqHH6r4Tts7J_aSIgg"),
                                           unb64u("DGv6ra1nlYgDCS1FRnbzlw"), rfc_as))}
    rua = ec.derive_private_key(int.from_bytes(unb64u(out["rfc8291"]["ua_priv"]), "big"), P256)
    assert b64u(raw_pub(rua)) == rfc_ua and decrypt(unb64u(out["rfc8291"]["body"]), rua, unb64u(out["rfc8291"]["auth"]))[0] == \
        out["rfc8291"]["plain"].encode() and out["rfc8291"]["body"] == RFC8291_BODY
    cases = []
    for name, voip, msg in _msgs():
        plain = plain_of(msg)
        assert len(plain) <= E2E_MAX, (name, len(plain))
        pt, prio, ttl, ctag, rate = plan(msg, voip)
        salt, as_key = h32("salt:" + name)[:16], p256_key("as:" + name)
        pad = pad_size(len(plain))
        body = encrypt(plain, ua_pub, auth, salt, as_key, pad)
        aps = aps_for(msg, pt)
        saga = {"e2e": 1, "k": kid(ua_pub), "c": b64u(body)}
        cases.append({"name": name, "subscription": "apns-voip" if voip else "apns", "msg": msg, "plain": plain.decode(),
                      "plain_len": len(plain), "pad_to": pad, "salt": b64u(salt), "as_priv": b64u(scalar(as_key)),
                      "push_type": pt, "priority": prio, "ttl": ttl, "expiration": NOW + ttl, "rate": rate,
                      "collapse_tag": ctag, "collapse_id": collapse(auth, ctag), "aps": aps,
                      "apns_body": {"aps": aps, "saga": saga}, "body_len": len(body)})
    out["cases"] = cases
    out["pad"] = [[n, pad_size(n)] for n in (0, 1, 100, 510, 511, 512, 1022, 1023, 1024, 2046, 2047, 2048, 2400)]
    out["collapse"] = [{"tag": t, "collapse_id": collapse(auth, t)} for t in ("chat-12", "uchat-12", "job-1", "job-2", "call-0123456789abcdef")]
    # Schluesselwechsel am Relais: Besitznachweis des neuen Schluessels (Ed25519 ist deterministisch)
    new = Ed25519PrivateKey.from_private_bytes(h32("rotate-new"))
    new_pub = b64u(new.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    sid = "s_abcdefghijklmnop"
    msgb = (ROTATE_INFO + sid + "|" + new_pub).encode()
    out["rotate"] = {"server_id": sid, "new_priv_seed": b64u(h32("rotate-new")), "new_pub": new_pub, "signed": msgb.decode(),
                     "proof": b64u(new.sign(msgb))}
    # Faelle, die die App verwerfen muss (Platzhalter zeigen)
    good = unb64u(cases[0]["apns_body"]["saga"]["c"])
    flip = bytearray(good)
    flip[-1] ^= 1
    other = p256_key("andere-app")
    out["bad"] = [
        {"name": "chiffrat_veraendert", "c": b64u(bytes(flip)), "k": kid(ua_pub)},
        {"name": "anderer_schluessel", "c": b64u(encrypt(b'{"kind":"test"}', raw_pub(other), auth, h32("salt:x")[:16], p256_key("as:x"), 512)),
         "k": kid(ua_pub)},
        {"name": "ohne_trenner", "c": b64u(_no_delim(ua_pub, auth)), "k": kid(ua_pub)},
        {"name": "abgeschnitten", "c": b64u(good[:60]), "k": kid(ua_pub)},
    ]
    return out


def _no_delim(ua_pub, auth):
    """Datensatz ohne 0x02 (nur Nullen am Ende) - muss abgewiesen werden."""
    as_key = p256_key("as:nodelim")
    salt = h32("salt:nodelim")[:16]
    as_pub = raw_pub(as_key)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(P256, ua_pub))
    cek, nonce = _keys(ua_pub, as_pub, shared, auth, salt)
    return salt + struct.pack("!I", RS) + bytes([65]) + as_pub + AESGCM(cek).encrypt(nonce, b'{"kind":"test"}' + b"\x00" * 20, None)


def check(v):
    """-> Liste der Abweichungen (leer = alles stimmt)."""
    bad = []
    want = build()
    for k in ("format", "params", "keys", "rfc8291", "pad", "collapse", "rotate"):
        if v.get(k) != want[k]:
            bad.append(f"{k}: weicht ab")
    if [c["name"] for c in v.get("cases", [])] != [c["name"] for c in want["cases"]]:
        bad.append("cases: andere Faelle")
    ua = ec.derive_private_key(int.from_bytes(unb64u(v["keys"]["ua_priv"]), "big"), P256)
    auth = unb64u(v["keys"]["auth"])
    for c, w in zip(v.get("cases", []), want["cases"]):
        if c != w:
            bad.append(f"{c['name']}: weicht von der Erzeugung ab")
        try:
            plain, rec = decrypt(unb64u(c["apns_body"]["saga"]["c"]), ua, auth)
        except Exception as ex:  # noqa: BLE001
            bad.append(f"{c['name']}: entschluesseln: {type(ex).__name__}")
            continue
        if plain.decode() != c["plain"] or json.loads(plain) != c["msg"] or rec != c["pad_to"]:
            bad.append(f"{c['name']}: Inhalt/Auffuellen falsch")
        if c["pad_to"] not in PAD_STEPS and c["pad_to"] % PAD_ROUND:
            bad.append(f"{c['name']}: pad_to keine Stufe")
        if any(x in json.dumps(c["apns_body"]["aps"], ensure_ascii=False) for x in ("Mira", "Beispiel", "Reiseplan", "Auftrag")):
            bad.append(f"{c['name']}: Klartext im sichtbaren Teil")
        if c["push_type"] == "voip" and c["msg"]["kind"] not in RING_KINDS:
            bad.append(f"{c['name']}: voip fuer {c['msg']['kind']}")
        if len(json.dumps(c["apns_body"], separators=(",", ":"))) > (5120 if c["push_type"] == "voip" else 4096):
            bad.append(f"{c['name']}: APNs-Koerper zu gross")
    for b in v.get("bad", []):
        try:
            decrypt(unb64u(b["c"]), ua, auth)
            bad.append(f"bad {b['name']}: wurde angenommen")
        except Exception:  # noqa: BLE001 - jeder Fehler ist hier richtig
            pass
    r = v["rotate"]
    try:
        Ed25519PublicKey.from_public_bytes(unb64u(r["new_pub"])).verify(unb64u(r["proof"]), r["signed"].encode())
    except InvalidSignature:
        bad.append("rotate: Nachweis ungueltig")
    return bad


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "gen":
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(build(), f, ensure_ascii=False, indent=1)
            f.write("\n")
        print("geschrieben:", PATH)
        return 0
    with open(PATH, encoding="utf-8") as f:
        bad = check(json.load(f))
    for b in bad:
        print("FAIL", b)
    print("ALLES GRUEN" if not bad else f"{len(bad)} Abweichungen")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
