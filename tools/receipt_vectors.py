#!/usr/bin/env python3
"""Testvektoren fuer Gelesen-Bestaetigungen (2.4, saga/chat_status.py; tests/vectors/receipts.json) erzeugen bzw. pruefen.

  python3 tools/receipt_vectors.py gen      Datei neu schreiben (feste Seeds; nur bei Formataenderung)
  python3 tools/receipt_vectors.py check    Datei pruefen (PyNaCl, tests/chatlib.py)

Gegenprobe mit libsodium.js + static/chat-crypto.js: node tools/receipt_vectors.mjs (laeuft in tests/chat_v2_vektoren.py).
iOS/macOS, Android und Linux pruefen ihre Umsetzung gegen DIESELBE Datei. crypto_box_seal ist zufaellig: "sealed" muss
jede Umsetzung mit dem Backup-Schluessel des Gegenuebers OEFFNEN koennen; die Signatur (Ed25519) ist fest.
Format: inner = JSON {"v":1,"dev":<geraet>,"body":<JSON-Text {id,n,ts}>,"sig":B64}, sealed = B64(crypto_box_seal(inner,
backup_pk)), sig = Ed25519(LP("saga-chat/v1/receipt", str(chat_id), benutzername, geraet, body)) bzw. v2 mit
LP("saga-chat/v2/receipt", chat_ref, handle, geraet, body)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import chatlib as C  # noqa: E402

PATH = os.path.join(ROOT, "tests", "vectors", "receipts.json")
DEV = "0123456789abcdef0123456789abcdef"
CASES = [
    {"name": "v1", "v2": False, "chat": "42", "reader": "anna", "body": {"id": "aa" * 16, "n": 17, "ts": 1792444800000}},
    {"name": "v2", "v2": True, "chat": "00112233445566778899aabbccddeeff@a.example", "reader": "~abcdefghijklmnop@a.example",
     "body": {"id": "bb" * 16, "n": 3, "ts": 1792444801000}},
]


def seed(tag):
    return C.gh(("saga-receipt-vector-seed:" + tag).encode())


def keys():
    dev = C.device(DEV, seed("enc-reader"), seed("sign-reader"))
    viewer = C.identity(seed("ident-viewer"), seed("backup-viewer"))
    return dev, viewer


def gen():
    dev, viewer = keys()
    out = {"format": "saga-receipt/v1", "device": {"id": DEV, "sign_seed_hex": seed("sign-reader").hex(), "sign_pk": C.b64(dev["sign_pk"])},
           "viewer": {"backup_seed_hex": seed("backup-viewer").hex(), "backup_pk": C.b64(viewer["backup_pk"])}, "cases": [], "reject": []}
    for cs in CASES:
        bs = json.dumps(cs["body"], separators=(",", ":"))
        msg = C.receipt_msg(cs["chat"], cs["reader"], DEV, bs, cs["v2"])
        sig = C.sign(msg, dev["sign_sk"])
        sealed = C.seal_receipt(cs["chat"], cs["reader"], dev, cs["body"], viewer["backup_pk"], cs["v2"])
        out["cases"].append({"name": cs["name"], "v2": cs["v2"], "chat": cs["chat"], "reader": cs["reader"], "body": bs,
                             "msg_hex": msg.hex(), "sig": C.b64(sig), "sealed": sealed})
    c0 = out["cases"][1]
    out["reject"] = [
        {"why": "anderer Chat", "v2": True, "chat": "ffeeddccbbaa99887766554433221100@a.example", "reader": c0["reader"], "body": c0["body"], "sig": c0["sig"]},
        {"why": "anderer Leser", "v2": True, "chat": c0["chat"], "reader": "~qrstuvwxyz234567@b.example", "body": c0["body"], "sig": c0["sig"]},
        {"why": "v1 statt v2", "v2": False, "chat": c0["chat"], "reader": c0["reader"], "body": c0["body"], "sig": c0["sig"]},
        {"why": "Position geaendert", "v2": True, "chat": c0["chat"], "reader": c0["reader"], "body": c0["body"].replace('"n":3', '"n":4'), "sig": c0["sig"]},
    ]
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("geschrieben:", PATH)


def check():
    """-> [(name, ok)]"""
    v = json.load(open(PATH, encoding="utf-8"))
    dev, viewer = keys()
    res = [("format", v["format"] == "saga-receipt/v1"),
           ("schluessel", v["device"]["sign_pk"] == C.b64(dev["sign_pk"]) and v["viewer"]["backup_pk"] == C.b64(viewer["backup_pk"]))]
    for cs in v["cases"]:
        msg = C.receipt_msg(cs["chat"], cs["reader"], DEV, cs["body"], cs["v2"])
        res.append((cs["name"] + " lp", msg.hex() == cs["msg_hex"]))
        res.append((cs["name"] + " sig fest", C.b64(C.sign(msg, dev["sign_sk"])) == cs["sig"]))
        try:
            d, body, sig = C.open_receipt(cs["sealed"], viewer["backup_pk"], viewer["backup_sk"])
            good = d == DEV and body == cs["body"] and C.verify_receipt(cs["chat"], cs["reader"], d, body, sig, dev["sign_pk"], cs["v2"])
        except Exception:  # noqa: BLE001
            good = False
        res.append((cs["name"] + " oeffnen + pruefen", good))
    for r in v["reject"]:
        res.append(("ablehnen: " + r["why"], not C.verify_receipt(r["chat"], r["reader"], DEV, r["body"], C.unb64(r["sig"], 64), dev["sign_pk"], r["v2"])))
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    else:
        rs = check()
        for n, g in rs:
            print(("OK  " if g else "FAIL"), n)
        sys.exit(0 if all(g for _, g in rs) else 1)
