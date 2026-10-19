#!/usr/bin/env python3
"""Testvektoren fuer das Datei-Format saga-file/v2 (tests/vectors/file_v2.json) erzeugen bzw. pruefen.

  python3 tools/file_vectors_v2.py gen      Datei neu schreiben (feste Schluessel/Praefixe; nur bei Formataenderung)
  python3 tools/file_vectors_v2.py check    Datei pruefen: PyNaCl (tests/chatlib.py) und Server-Kopfpruefung (saga/file_v2.py)

Gegenprobe libsodium.js + static/chat-crypto.js: node tools/file_vectors_v2.mjs. Web, iOS/macOS, Android und Linux pruefen
ihre Umsetzung gegen DIESELBE Datei. Damit die Datei klein bleibt, steht der Klartext nicht drin: plain[k] = k mod 251
(fuer k = 0 .. size-1). Je Fall: Kopf (hex), AD und Nonce des ersten und letzten Blocks (hex), erster und letzter
Ciphertext-Block (hex, gekuerzt auf die ersten/letzten 64 Bytes plus volle Laenge) und SHA-256 des ganzen Blobs.
Abschnitt "reject": Veraenderungen, die jede Umsetzung beim Entschluesseln ABLEHNEN muss. Abschnitt "ranges":
Klartext-Bereich -> Bloecke und Ciphertext-Bereich."""
import hashlib
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
import chatlib as C  # noqa: E402
import nacl  # noqa: E402
import nacl.exceptions  # noqa: E402
from saga import file_v2 as FV  # noqa: E402

PATH = os.path.join(ROOT, "tests", "vectors", "file_v2.json")
CS = C.F2_CS
SIZES = {"empty": 0, "one_byte": 1, "small": 100, "cs_minus_1": CS - 1, "exact_1cs": CS, "cs_plus_1": CS + 1,
         "exact_2cs": 2 * CS, "rest_block": 2 * CS + 5}


def seed(tag, n=32):
    return C.gh(("saga-file-v2-vector:" + tag).encode())[:n]


def plain(n):
    return bytes(k % 251 for k in range(n))


def case(name, size):
    key, prefix = seed(name + "-K"), seed(name + "-prefix", 16)
    blob, _ = C.encrypt_file_f2(plain(size), key=key, prefix=prefix)
    n = C.f2_blocks(size)
    first = blob[C.f2_block_off(0):min(C.f2_block_off(0) + CS + 16, len(blob))]
    last = blob[C.f2_block_off(n - 1):]
    return {"size": size, "key": C.b64(key), "prefix_hex": prefix.hex(), "cs": CS, "blocks": n, "blob_size": len(blob),
            "header_hex": blob[:48].hex(), "ad0_hex": C.f2_ad(0, n, CS, size).hex(), "nonce0_hex": C.f2_nonce(prefix, 0).hex(),
            "ad_last_hex": C.f2_ad(n - 1, n, CS, size).hex(), "nonce_last_hex": C.f2_nonce(prefix, n - 1).hex(),
            "block0_len": len(first), "block0_head_hex": first[:64].hex(), "block0_tail_hex": first[-64:].hex(),
            "block_last_len": len(last), "block_last_head_hex": last[:64].hex(), "block_last_tail_hex": last[-64:].hex(),
            "blob_sha256": hashlib.sha256(blob).hexdigest()}


def build(c):
    """Blob eines Falls aus den Angaben neu erzeugen."""
    return C.encrypt_file_f2(plain(c["size"]), key=C.unb64(c["key"], 32), prefix=bytes.fromhex(c["prefix_hex"]))[0]


def mutate(blob, op):
    """Veraenderung aus dem Abschnitt reject anwenden."""
    b = bytearray(blob)
    k = op["op"]
    if k == "swap_blocks":
        i, j = op["i"], op["j"]
        bi, bj = C.f2_block_off(i), C.f2_block_off(j)
        x, y = bytes(b[bi:bi + CS + 16]), bytes(b[bj:bj + CS + 16])
        b[bi:bi + CS + 16], b[bj:bj + CS + 16] = y, x
    elif k == "drop_last_block":
        n = C.f2_blocks(op["size"])
        del b[C.f2_block_off(n - 1):]
    elif k == "drop_last_block_fix_size":           # Kopf auf die kleinere Groesse "repariert" - Endmarke/N passen nicht
        n = C.f2_blocks(op["size"])
        del b[C.f2_block_off(n - 1):]
        b[16:24] = struct.pack(">Q", (n - 1) * CS)
    elif k == "set_size":
        b[16:24] = struct.pack(">Q", op["value"])
    elif k == "set_cs":
        b[8:12] = struct.pack(">I", op["value"])
    elif k == "set_byte":
        b[op["offset"]] = op["value"]
    elif k == "flip":
        b[op["offset"]] ^= 1
    elif k == "truncate":
        del b[op["length"]:]
    else:
        raise ValueError(k)
    return bytes(b)


REJECT = [
    {"name": "Bloecke vertauscht", "case": "rest_block", "op": "swap_blocks", "i": 0, "j": 1},
    {"name": "letzter Block fehlt", "case": "rest_block", "op": "drop_last_block", "size": 2 * CS + 5},
    {"name": "letzter Block fehlt, Kopf angepasst", "case": "rest_block", "op": "drop_last_block_fix_size", "size": 2 * CS + 5},
    {"name": "abgeschnitten mitten im Block", "case": "exact_2cs", "op": "truncate", "length": 48 + CS + 16 + 1000},
    {"name": "Groesse im Kopf falsch", "case": "small", "op": "set_size", "value": 99},
    {"name": "Blockgroesse im Kopf falsch", "case": "cs_plus_1", "op": "set_cs", "value": 131072},
    {"name": "falsche Kennung", "case": "small", "op": "set_byte", "offset": 0, "value": 0x58},
    {"name": "falsche Version", "case": "small", "op": "set_byte", "offset": 4, "value": 3},
    {"name": "Praefix veraendert", "case": "small", "op": "flip", "offset": 30},
    {"name": "Ciphertext veraendert", "case": "cs_plus_1", "op": "flip", "offset": 48 + 100},
    {"name": "Tag des letzten Blocks veraendert", "case": "rest_block", "op": "flip", "offset": 48 + 2 * (CS + 16) + 5 + 15},
    {"name": "leere Datei ohne Block", "case": "empty", "op": "truncate", "length": 48},
]
RANGES = [(0, 0), (0, CS - 1), (CS - 1, CS), (CS, 2 * CS - 1), (100, 2 * CS + 4), (2 * CS, 2 * CS + 4), (2 * CS + 4, 2 * CS + 4)]


def gen():
    cases = {n: case(n, s) for n, s in SIZES.items()}
    size = SIZES["rest_block"]
    out = {"format": "saga-file/v2",
           "note": ("Erzeugt mit tools/file_vectors_v2.py (PyNaCl %s). Format: saga/file_v2.py, docs/APP-API.md (Videos im Chat). "
                    "Klartext: plain[k] = k mod 251. B64 = Standard-Base64 mit Padding; *_hex = Hex." % nacl.__version__),
           "cs": CS, "header_len": 48, "tag_len": 16,
           "lp_example": {"fields_hex": ["%s" % b"saga-file/v2".hex(), struct.pack(">Q", 0).hex()],
                          "hex": C.lp(b"saga-file/v2", struct.pack(">Q", 0)).hex()},
           "cases": cases,
           "reject": REJECT,
           "ranges": [{"size": size, "a": a, "b": b, "first_block": C.f2_range(size, a, b)[0], "last_block": C.f2_range(size, a, b)[1],
                       "cipher_from": C.f2_range(size, a, b)[2], "cipher_to": C.f2_range(size, a, b)[3]} for a, b in RANGES]}
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("geschrieben:", PATH)


def check():
    """-> Liste (name, ok)."""
    with open(PATH, encoding="utf-8") as f:
        v = json.load(f)
    res = [("format", v["format"] == "saga-file/v2" and v["cs"] == CS)]
    res.append(("lp", C.lp(*[bytes.fromhex(x) for x in v["lp_example"]["fields_hex"]]).hex() == v["lp_example"]["hex"]))
    blobs = {}
    for name, c in v["cases"].items():
        blob = build(c)
        blobs[name] = blob
        n = c["blocks"]
        res.append((f"{name}: Kopf", blob[:48].hex() == c["header_hex"] and C.f2_header(c["size"], bytes.fromhex(c["prefix_hex"])).hex() == c["header_hex"]))
        res.append((f"{name}: AD/Nonce", C.f2_ad(0, n, CS, c["size"]).hex() == c["ad0_hex"] and C.f2_nonce(bytes.fromhex(c["prefix_hex"]), 0).hex() == c["nonce0_hex"]
                    and C.f2_ad(n - 1, n, CS, c["size"]).hex() == c["ad_last_hex"]
                    and C.f2_nonce(bytes.fromhex(c["prefix_hex"]), n - 1).hex() == c["nonce_last_hex"]))
        last = blob[C.f2_block_off(n - 1):]
        res.append((f"{name}: Bloecke", len(last) == c["block_last_len"] and last[:64].hex() == c["block_last_head_hex"]
                    and last[-64:].hex() == c["block_last_tail_hex"] and blob[48:48 + 64].hex() == c["block0_head_hex"]))
        res.append((f"{name}: SHA-256 + Groesse", hashlib.sha256(blob).hexdigest() == c["blob_sha256"] and len(blob) == c["blob_size"]
                    == FV.cipher_size(c["size"])))
        res.append((f"{name}: entschluesseln", C.decrypt_file_f2(blob, C.unb64(c["key"], 32)) == plain(c["size"])))
        res.append((f"{name}: Server-Kopfpruefung", (FV.check_blob(blob[:48], len(blob)) or {}).get("size") == c["size"]))
    for r in v["reject"]:
        bad = mutate(blobs[r["case"]], r)
        try:
            C.decrypt_file_f2(bad, C.unb64(v["cases"][r["case"]]["key"], 32))
            good = False
        except (ValueError, nacl.exceptions.CryptoError):
            good = True
        res.append((f"abgelehnt: {r['name']}", good))
    for r in v["ranges"]:
        res.append((f"Bereich {r['a']}-{r['b']}", list(C.f2_range(r["size"], r["a"], r["b"])) == [r["first_block"], r["last_block"], r["cipher_from"], r["cipher_to"]]))
    # Einzelner Block aus einem Bereich (wie beim Streaming): nur Kopf + Block i
    c = v["cases"]["rest_block"]
    blob = blobs["rest_block"]
    i = 1
    blk = blob[C.f2_block_off(i):C.f2_block_off(i) + CS + 16]
    res.append(("Block 1 allein entschluesselt", C.f2_open(blob[:48], i, blk, C.unb64(c["key"], 32)) == plain(c["size"])[CS:2 * CS]))
    return res


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "gen":
        gen()
    rs = check()
    for n, good in rs:
        print(("OK  " if good else "FAIL"), n)
    print(f"{sum(g for _, g in rs)}/{len(rs)} OK – " + ("ALLES GRUEN" if all(g for _, g in rs) else "FEHLER"))
    sys.exit(0 if all(g for _, g in rs) else 1)
