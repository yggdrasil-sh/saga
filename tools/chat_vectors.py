#!/usr/bin/env python3
"""Testvektoren fuer das Chat-Krypto-Format v1 (tests/chat_vectors.json) erzeugen bzw. pruefen - mit PyNaCl (libsodium).

  python3 tools/chat_vectors.py gen      Datei neu schreiben (feste Seeds/Nonces -> gleiche Ausgabe; nur bei Formataenderung)
  python3 tools/chat_vectors.py check    Datei gegen PyNaCl pruefen (Exit 1 bei Abweichung)

Gegenprobe mit libsodium.js: node tools/chat_vectors.mjs; im Browser prueft die Chat-Seite sie in den Playwright-Tests.
Die Apps (Swift/Kotlin/Linux) pruefen ihre Umsetzung gegen dieselbe Datei. crypto_box_seal ist zufaellig (Wegwerf-
Schluessel): dafuer enthaelt die Datei versiegelte Werte, die jede Umsetzung OEFFNEN koennen muss."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import chatlib as C  # noqa: E402
import nacl  # noqa: E402
import nacl.bindings as nb  # noqa: E402

PATH = os.path.join(ROOT, "tests", "chat_vectors.json")


def seed(tag):
    return C.gh(("saga-chat-vector-seed:" + tag).encode())          # 32 Bytes, reproduzierbar


def gen():
    uid, uid_b = "anna", "bert"
    ident = C.identity(seed("ident"), seed("backup"))
    dev = C.device("0123456789abcdef0123456789abcdef", seed("enc"), seed("sign"))
    ident_b = C.identity(seed("ident-b"), seed("backup-b"))
    rk = C.recovery_key(seed("recovery")[:20])
    salt, bnonce = seed("salt")[:16], seed("bnonce")[:24]
    plain = C.bundle_plain(ident["ident_sk"], ident["backup_sk"])
    bundle = C.seal_bundle(uid, rk, ident, salt=salt, nonce=bnonce, plain=plain)
    body = {"v": 1, "type": "text", "text": "Hallo Bert – grüß dich! 👋 [[note:42]]", "notes": [42]}
    body_json = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    K, nonce, ts, mid, chat_id, reply = seed("K"), seed("nonce")[:24], 1760000000123, "fedcba9876543210fedcba9876543210", "7", "00112233445566778899aabbccddeeff"
    m = C.encrypt(chat_id, uid, dev, None, {dev["id"]: dev["enc_pk"], "backup:" + uid: ident["backup_pk"]}, reply_to=reply, mid=mid, ts=ts,
                  key=K, nonce=nonce, plain=body_json)
    fdata = "Anhang-Inhalt äöü\n".encode()
    blob, fkey = C.encrypt_file(chat_id, fdata, key=seed("Kf"), nonce=seed("fnonce")[:24])
    dmsg = C.device_msg(uid, dev["id"], dev["enc_pk"], dev["sign_pk"])
    out = {
        "format": "saga-chat/v1",
        "note": "Erzeugt mit tools/chat_vectors.py (PyNaCl %s). B64 = Standard-Base64 mit Padding; *_hex = Hex. Alle Felder siehe docs." % nacl.__version__,
        "lp": {"fields": ["a", "bc", "", "ä"], "hex": C.lp("a", "bc", "", "ä").hex()},
        "base32": {"bytes_hex": seed("recovery")[:20].hex(), "text": C.base32(seed("recovery")[:20])},
        "identity": {"uid": uid, "ident_seed_hex": seed("ident").hex(), "ident_pk": C.b64(ident["ident_pk"]), "ident_sk": C.b64(ident["ident_sk"]),
                     "backup_seed_hex": seed("backup").hex(), "backup_pk": C.b64(ident["backup_pk"]), "backup_sk": C.b64(ident["backup_sk"]),
                     "backup_msg_hex": C.backup_msg(uid, ident["backup_pk"]).hex(),
                     "backup_sig": C.b64(C.sign(C.backup_msg(uid, ident["backup_pk"]), ident["ident_sk"]))},
        "device": {"id": dev["id"], "enc_seed_hex": seed("enc").hex(), "enc_pk": C.b64(dev["enc_pk"]), "enc_sk": C.b64(dev["enc_sk"]),
                   "sign_seed_hex": seed("sign").hex(), "sign_pk": C.b64(dev["sign_pk"]), "sign_sk": C.b64(dev["sign_sk"]),
                   "msg_hex": dmsg.hex(), "sig": C.b64(C.sign(dmsg, ident["ident_sk"]))},
        "recovery": {"key": rk, "normalized": C.norm_recovery(rk), "input_variant": rk.lower().replace("-", " "),
                     "salt": C.b64(salt), "ops": C.OPS, "mem": C.MEM, "kdf_hex": C.recovery_kdf(rk, salt).hex(),
                     "nonce": C.b64(bnonce), "ad_hex": C.lp(C.V + "bundle", uid).hex(), "plain": plain, "bundle": bundle},
        "message": {"chat_id": chat_id, "id": mid, "sender": uid, "device": dev["id"], "ts": ts, "reply_to": reply,
                    "body_json": body_json, "key": C.b64(K), "nonce": C.b64(nonce),
                    "ad_hex": C.msg_ad(chat_id, mid, uid, dev["id"], ts, reply).hex(), "body_ct": m["body_ct"],
                    "sig_msg_hex": C.sig_msg(chat_id, mid, uid, dev["id"], ts, reply, m["body_ct"]).hex(), "sig": m["sig"],
                    "sealed_device": m["keys"][dev["id"]], "sealed_backup": m["keys"]["backup:" + uid]},
        "file": {"chat_id": chat_id, "plain_b64": C.b64(fdata), "key": C.b64(fkey), "nonce": C.b64(seed("fnonce")[:24]), "blob": C.b64(blob)},
        "link": {"uid": uid, "device_id": dev["id"], "enc_pk": C.b64(dev["enc_pk"]), "sign_pk": C.b64(dev["sign_pk"]),
                 "code": C.link_code(uid, dev["id"], dev["enc_pk"], dev["sign_pk"]),
                 "sealed_bundle": C.b64(nb.crypto_box_seal(plain.encode(), dev["enc_pk"]))},
        "safety": {"uid_a": uid, "ident_a": C.b64(ident["ident_pk"]), "uid_b": uid_b, "ident_b": C.b64(ident_b["ident_pk"]),
                   "number": C.safety_number(uid, ident["ident_pk"], uid_b, ident_b["ident_pk"])},
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
    i, d, r, m, f, lk, s = v["identity"], v["device"], v["recovery"], v["message"], v["file"], v["link"], v["safety"]
    uid = i["uid"]
    ok("lp", C.lp(*v["lp"]["fields"]).hex() == v["lp"]["hex"])
    ok("base32", C.base32(bytes.fromhex(v["base32"]["bytes_hex"])) == v["base32"]["text"])
    ident = C.identity(bytes.fromhex(i["ident_seed_hex"]), bytes.fromhex(i["backup_seed_hex"]))
    ok("identity keys", C.b64(ident["ident_pk"]) == i["ident_pk"] and C.b64(ident["ident_sk"]) == i["ident_sk"]
       and C.b64(ident["backup_pk"]) == i["backup_pk"] and C.b64(ident["backup_sk"]) == i["backup_sk"])
    ok("backup_sig", C.b64(C.sign(bytes.fromhex(i["backup_msg_hex"]), ident["ident_sk"])) == i["backup_sig"]
       and C.backup_msg(uid, ident["backup_pk"]).hex() == i["backup_msg_hex"])
    dev = C.device(d["id"], bytes.fromhex(d["enc_seed_hex"]), bytes.fromhex(d["sign_seed_hex"]))
    ok("device keys", C.b64(dev["enc_pk"]) == d["enc_pk"] and C.b64(dev["sign_pk"]) == d["sign_pk"] and C.b64(dev["sign_sk"]) == d["sign_sk"])
    ok("device cert", C.device_msg(uid, d["id"], dev["enc_pk"], dev["sign_pk"]).hex() == d["msg_hex"]
       and C.b64(C.sign(bytes.fromhex(d["msg_hex"]), ident["ident_sk"])) == d["sig"])
    ok("recovery normalize", C.norm_recovery(r["key"]) == r["normalized"] == C.norm_recovery(r["input_variant"]))
    ok("recovery kdf (argon2id13)", C.recovery_kdf(r["key"], C.unb64(r["salt"]), r["ops"], r["mem"]).hex() == r["kdf_hex"])
    ok("bundle seal", C.seal_bundle(uid, r["key"], ident, salt=C.unb64(r["salt"]), nonce=C.unb64(r["nonce"]), plain=r["plain"]) == r["bundle"])
    ob = C.open_bundle(uid, r["input_variant"], r["bundle"])
    ok("bundle open", ob["ident_pk"] == ident["ident_pk"] and ob["backup_pk"] == ident["backup_pk"])
    enc = C.encrypt(m["chat_id"], m["sender"], dev, None, {}, reply_to=m["reply_to"], mid=m["id"], ts=m["ts"], key=C.unb64(m["key"]),
                    nonce=C.unb64(m["nonce"]), plain=m["body_json"])
    ok("message ad", C.msg_ad(m["chat_id"], m["id"], m["sender"], m["device"], m["ts"], m["reply_to"]).hex() == m["ad_hex"])
    ok("message encrypt", enc["body_ct"] == m["body_ct"])
    ok("message sig", enc["sig"] == m["sig"] and C.sig_msg(m["chat_id"], m["id"], m["sender"], m["device"], m["ts"], m["reply_to"], m["body_ct"]).hex() == m["sig_msg_hex"])
    api = {"id": m["id"], "ts": m["ts"], "sender": {"username": m["sender"]}, "sender_device": m["device"], "body_ct": m["body_ct"], "reply_to": m["reply_to"]}
    ok("message open (device)", C.decrypt(m["chat_id"], dict(api, key=m["sealed_device"]), dev["enc_pk"], dev["enc_sk"]) == json.loads(m["body_json"]))
    ok("message open (backup)", C.decrypt(m["chat_id"], dict(api, key=m["sealed_backup"]), ident["backup_pk"], ident["backup_sk"]) == json.loads(m["body_json"]))
    ok("file", C.b64(C.encrypt_file(f["chat_id"], C.unb64(f["plain_b64"]), C.unb64(f["key"]), C.unb64(f["nonce"]))[0]) == f["blob"]
       and C.decrypt_file(f["chat_id"], C.unb64(f["blob"]), C.unb64(f["key"])) == C.unb64(f["plain_b64"]))
    ok("link code", C.link_code(lk["uid"], lk["device_id"], C.unb64(lk["enc_pk"]), C.unb64(lk["sign_pk"])) == lk["code"])
    ok("link sealed bundle", nb.crypto_box_seal_open(C.unb64(lk["sealed_bundle"]), dev["enc_pk"], dev["enc_sk"]).decode() == r["plain"])
    ok("safety number", C.safety_number(s["uid_a"], C.unb64(s["ident_a"]), s["uid_b"], C.unb64(s["ident_b"])) == s["number"]
       == C.safety_number(s["uid_b"], C.unb64(s["ident_b"]), s["uid_a"], C.unb64(s["ident_a"])))
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    res = check()
    for n, good in res:
        print("OK  " if good else "FAIL", n)
    print(f"VEKTOREN {sum(g for _, g in res)}/{len(res)}", "- ALLES GRUEN" if all(g for _, g in res) else "- FEHLER")
    sys.exit(0 if all(g for _, g in res) else 1)
