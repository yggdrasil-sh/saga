#!/usr/bin/env python3
"""Testvektoren fuer die Anruf-Signalisierung saga-call/v1 (tests/call_vectors.json) erzeugen bzw. pruefen - mit PyNaCl.

  python3 tools/call_vectors.py gen      Datei neu schreiben (feste Seeds -> gleiche Ausgabe; nur bei Formataenderung)
  python3 tools/call_vectors.py check    Datei gegen PyNaCl pruefen (Exit 1 bei Abweichung)

Gegenprobe mit libsodium.js (static/chat-crypto.js): node tools/call_vectors.mjs. Die Apps (Swift/Kotlin/Linux) pruefen
ihre Umsetzung gegen dieselbe Datei. crypto_box_seal ist zufaellig: die Datei enthaelt versiegelte Signale, die jede
Umsetzung OEFFNEN und PRUEFEN koennen muss, dazu eine Faelschung (falsches Zielgeraet), die abgelehnt werden muss.
Format: saga/calls.py (Kopf) und docs/ANLEITUNG.md ("Anrufe")."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import chatlib as C  # noqa: E402
import nacl  # noqa: E402

PATH = os.path.join(ROOT, "tests", "call_vectors.json")
SDP = ("v=0\r\no=- 4611731400430051336 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1\r\n"
       "a=fingerprint:sha-256 4A:AD:B9:B1:3F:82:18:3B:54:02:12:DF:3E:5D:49:6B:19:E5:7C:AB:3D:17:6E:B4:2A:7C:4F:0D:2E:F1:A8:B3\r\n"
       "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nc=IN IP4 0.0.0.0\r\na=setup:actpass\r\na=mid:0\r\na=sendrecv\r\na=rtpmap:111 opus/48000/2\r\n"
       "m=video 9 UDP/TLS/RTP/SAVPF 96\r\nc=IN IP4 0.0.0.0\r\na=setup:actpass\r\na=mid:1\r\na=sendrecv\r\na=rtpmap:96 VP8/90000\r\n")


def seed(tag):
    return C.gh(("saga-call-vector-seed:" + tag).encode())


def gen():
    a, b = "anna", "bert"
    dev_a = C.device("a0a1a2a3a4a5a6a7a8a9aaabacadaeaf", seed("enc-a"), seed("sign-a"))
    dev_b = C.device("b0b1b2b3b4b5b6b7b8b9babbbcbdbebf", seed("enc-b"), seed("sign-b"))
    call_id = "c0ffee00c0ffee00c0ffee00c0ffee00"
    offer = json.dumps({"t": "offer", "sdp": SDP, "media": "video", "fp": C.sdp_fingerprints(SDP)}, ensure_ascii=False, separators=(",", ":"))
    ice = json.dumps({"t": "ice", "c": [{"candidate": "candidate:1 1 udp 2122260223 e0a1b2c3-0000-4000-8000-000000000000.local 54321 typ host",
                                         "sdpMid": "0", "sdpMLineIndex": 0}]}, separators=(",", ":"))
    sig = C.sign(C.signal_msg(call_id, a, dev_a["id"], b, dev_b["id"], offer), dev_a["sign_sk"])
    sig_ice = C.sign(C.signal_msg(call_id, a, dev_a["id"], b, dev_b["id"], ice), dev_a["sign_sk"])
    out = {
        "format": "saga-call/v1",
        "note": "Erzeugt mit tools/call_vectors.py (PyNaCl %s). B64 = Standard-Base64 mit Padding. sig = Ed25519 ueber signal_msg; "
                "sealed = crypto_box_seal(inner, Ziel-enc_pk). Jede Umsetzung muss sealed oeffnen, inner/sig gleich erhalten und pruefen; "
                "forged = korrekt versiegelt, aber fuer ein anderes Zielgeraet signiert -> Pruefung muss scheitern." % nacl.__version__,
        "caller": {"uid": a, "device": dev_a["id"], "enc_seed_hex": seed("enc-a").hex(), "sign_seed_hex": seed("sign-a").hex(),
                   "enc_pk": C.b64(dev_a["enc_pk"]), "sign_pk": C.b64(dev_a["sign_pk"])},
        "callee": {"uid": b, "device": dev_b["id"], "enc_seed_hex": seed("enc-b").hex(), "sign_seed_hex": seed("sign-b").hex(),
                   "enc_pk": C.b64(dev_b["enc_pk"]), "sign_pk": C.b64(dev_b["sign_pk"])},
        "call_id": call_id,
        "offer": {"body": offer, "fingerprints": C.sdp_fingerprints(SDP),
                  "signal_msg_hex": C.signal_msg(call_id, a, dev_a["id"], b, dev_b["id"], offer).hex(), "sig": C.b64(sig),
                  "inner": C.signal_inner(offer, sig), "sealed": C.seal_signal(call_id, a, dev_a, b, dev_b["id"], dev_b["enc_pk"], offer)},
        "ice": {"body": ice, "sig": C.b64(sig_ice), "sealed": C.seal_signal(call_id, a, dev_a, b, dev_b["id"], dev_b["enc_pk"], ice)},
        "forged": {"body": ice, "to_device_signed": "ffffffffffffffffffffffffffffffff",
                   "sealed": C.seal_signal(call_id, a, dev_a, b, "ffffffffffffffffffffffffffffffff", dev_b["enc_pk"], ice)},
    }
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("geschrieben:", PATH)


def check(path=PATH):
    """-> Liste [(name, ok)]"""
    v = json.load(open(path, encoding="utf-8"))
    res = []

    def ok(n, c):
        res.append((n, bool(c)))
    ca, ce, cid = v["caller"], v["callee"], v["call_id"]
    da = C.device(ca["device"], bytes.fromhex(ca["enc_seed_hex"]), bytes.fromhex(ca["sign_seed_hex"]))
    db = C.device(ce["device"], bytes.fromhex(ce["enc_seed_hex"]), bytes.fromhex(ce["sign_seed_hex"]))
    ok("call device keys", C.b64(da["sign_pk"]) == ca["sign_pk"] and C.b64(db["enc_pk"]) == ce["enc_pk"])
    o = v["offer"]
    ok("call signal_msg", C.signal_msg(cid, ca["uid"], ca["device"], ce["uid"], ce["device"], o["body"]).hex() == o["signal_msg_hex"])
    ok("call sig (Ed25519, deterministisch)", C.b64(C.sign(bytes.fromhex(o["signal_msg_hex"]), da["sign_sk"])) == o["sig"])
    ok("call inner", C.signal_inner(o["body"], C.unb64(o["sig"])) == o["inner"])
    ok("call sdp fingerprints", C.sdp_fingerprints(json.loads(o["body"])["sdp"]) == o["fingerprints"] == json.loads(o["body"])["fp"])
    body, sig = C.open_signal(o["sealed"], db["enc_pk"], db["enc_sk"])
    ok("call open offer", body == o["body"] and C.b64(sig) == o["sig"])
    ok("call verify offer", C.verify_signal(cid, ca["uid"], ca["device"], ce["uid"], ce["device"], body, sig, da["sign_pk"]))
    ok("call verify fails for other call", not C.verify_signal("0" * 32, ca["uid"], ca["device"], ce["uid"], ce["device"], body, sig, da["sign_pk"]))
    body, sig = C.open_signal(v["ice"]["sealed"], db["enc_pk"], db["enc_sk"])
    ok("call open+verify ice", body == v["ice"]["body"] and C.b64(sig) == v["ice"]["sig"]
       and C.verify_signal(cid, ca["uid"], ca["device"], ce["uid"], ce["device"], body, sig, da["sign_pk"]))
    body, sig = C.open_signal(v["forged"]["sealed"], db["enc_pk"], db["enc_sk"])
    ok("call forged (other target device) rejected", not C.verify_signal(cid, ca["uid"], ca["device"], ce["uid"], ce["device"], body, sig, da["sign_pk"]))
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    res = check()
    for n, good in res:
        print("OK  " if good else "FAIL", n)
    print(f"VEKTOREN {sum(g for _, g in res)}/{len(res)}", "- ALLES GRUEN" if all(g for _, g in res) else "- FEHLER")
    sys.exit(0 if all(g for _, g in res) else 1)
