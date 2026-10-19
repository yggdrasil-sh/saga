#!/usr/bin/env python3
"""Testvektoren fuer das Chat-Krypto-Format saga-chat/v2 (tests/vectors/chat_v2.json) erzeugen bzw. pruefen.

  python3 tools/chat_vectors_v2.py gen      Datei neu schreiben (feste Seeds/Nonces; nur bei Formataenderung)
  python3 tools/chat_vectors_v2.py check    Datei pruefen: PyNaCl (tests/chatlib.py) UND Server-Referenz (saga/chat_v2.py)

Gegenprobe mit libsodium.js + static/chat-crypto.js: node tools/chat_vectors_v2.mjs; im Browser prueft die Chat-Seite sie
in den Playwright-Tests. Web, iOS/macOS, Android und Linux pruefen ihre Umsetzung gegen DIESELBE Datei (CI-Pflicht).
crypto_box_seal ist zufaellig: die Datei enthaelt versiegelte Werte, die jede Umsetzung OEFFNEN koennen muss.
Abschnitt "reject": Faelle, die jede Umsetzung ABLEHNEN muss. Abschnitt "tofu": Regeln fuer den Vertrauensspeicher."""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, ROOT)
import chatlib as C  # noqa: E402
import nacl  # noqa: E402
import nacl.bindings as nb  # noqa: E402
from saga import chat_v2 as SV  # noqa: E402  (eigenstaendig, ohne Saga-Konfiguration)

PATH = os.path.join(ROOT, "tests", "vectors", "chat_v2.json")
HA, HB = "~abcdefghijklmnop@a.example", "~qrstuvwxyz234567@b.example:8443"
REF = "00112233445566778899aabbccddeeff@a.example"
DEV_ID = "0123456789abcdef0123456789abcdef"


def seed(tag):
    return C.gh(("saga-chat-v2-vector-seed:" + tag).encode())


def keys():
    ia = C.identity(seed("ident-a"), seed("backup-a"))
    ib = C.identity(seed("ident-b"), seed("backup-b"))
    dev = C.device(DEV_ID, seed("enc-a"), seed("sign-a"))
    devb = C.device("fedcba9876543210fedcba9876543210", seed("enc-b"), seed("sign-b"))
    return ia, ib, dev, devb


def message(dev, tag, body, reply_to=None, ai=None):
    plain = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    K, nonce, ts, mid = seed(tag + "-K"), seed(tag + "-nonce")[:24], 1767225600123, C.gh(("mid:" + tag).encode())[:16].hex()
    m = C.encrypt_v2(REF, HA, dev, {}, plain, reply_to=reply_to, ai=ai, mid=mid, ts=ts, key=K, nonce=nonce)
    return {"chat_ref": REF, "id": mid, "sender_handle": HA, "sender_device": dev["id"], "ts": ts, "reply_to": reply_to, "ai": ai,
            "body_json": plain, "key": C.b64(K), "nonce": C.b64(nonce),
            "ad_hex": C.msg_ad_v2(REF, mid, HA, dev["id"], ts, reply_to, ai).hex(), "body_ct": m["body_ct"],
            "sig_msg_hex": C.sig_msg_v2(REF, mid, HA, dev["id"], ts, reply_to, ai, m["body_ct"]).hex(), "sig": m["sig"]}


def gen():
    ia, ib, dev, devb = keys()
    m1 = message(dev, "m1", {"v": 1, "type": "text", "text": "Hallo Bert – über Servergrenzen! 👋"}, reply_to="ffeeddccbbaa99887766554433221100")
    ai_text = "Gern – hier ist die Zusammenfassung."
    m2 = message(dev, "m2", {"v": 1, "type": "text", "text": ai_text}, ai=hashlib.sha256(ai_text.encode()).hexdigest())
    rcpt = {f"dev:{devb['id']}@b.example:8443": devb["enc_pk"], "backup:" + HB: ib["backup_pk"]}
    sealed = {r: C.b64(nb.crypto_box_seal(C.unb64(m1["key"]), pk)) for r, pk in rcpt.items()}
    fdata = "Anhang über Servergrenzen äöü\n".encode()
    blob, fkey = C.encrypt_file_v2(REF, fdata, key=seed("Kf"), nonce=seed("fnonce")[:24])
    hmsg = C.handle_msg(HA, ia["ident_pk"], ia["backup_pk"])
    dmsg = C.device_msg_v2(HA, dev["id"], dev["enc_pk"], dev["sign_pk"])
    v1dev = C.sign(C.device_msg("anna", dev["id"], dev["enc_pk"], dev["sign_pk"]), ia["ident_sk"])
    ds_ids = [dev["id"], "ffffffffffffffffffffffffffffffff"]
    dset = C.devices_sig_v2(HA, ia, 3, ds_ids)
    out = {
        "format": "saga-chat/v2",
        "note": ("Erzeugt mit tools/chat_vectors_v2.py (PyNaCl %s). B64 = Standard-Base64 mit Padding; *_hex = Hex. Format: saga/chat_v2.py, "
                 "docs/APP-API-FED.md. Lokale Chats bleiben saga-chat/v1 (tests/chat_vectors.json)." % nacl.__version__),
        "lp": {"fields": ["saga-chat/v2/handle", HA, ""], "hex": C.lp("saga-chat/v2/handle", HA, "").hex()},
        "identity": {"handle": HA, "ident_seed_hex": seed("ident-a").hex(), "ident_pk": C.b64(ia["ident_pk"]), "ident_sk": C.b64(ia["ident_sk"]),
                     "backup_seed_hex": seed("backup-a").hex(), "backup_pk": C.b64(ia["backup_pk"]), "backup_sk": C.b64(ia["backup_sk"])},
        "handle_cert": {"handle": HA, "msg_hex": hmsg.hex(), "sig": C.b64(C.handle_sig(HA, ia))},
        "device": {"id": dev["id"], "enc_seed_hex": seed("enc-a").hex(), "enc_pk": C.b64(dev["enc_pk"]), "enc_sk": C.b64(dev["enc_sk"]),
                   "sign_seed_hex": seed("sign-a").hex(), "sign_pk": C.b64(dev["sign_pk"]), "sign_sk": C.b64(dev["sign_sk"]),
                   "msg_hex": dmsg.hex(), "fed_sig": C.b64(C.device_sig_v2(HA, ia, dev))},
        "device_set": {"handle": HA, "ver": 3, "ids": dset["ids"], "msg_hex": C.devices_msg_v2(HA, 3, ds_ids).hex(), "sig": dset["sig"]},
        "recipient": {"identity": {"handle": HB, "ident_seed_hex": seed("ident-b").hex(), "backup_seed_hex": seed("backup-b").hex(),
                                   "ident_pk": C.b64(ib["ident_pk"]), "backup_pk": C.b64(ib["backup_pk"])},
                      "device": {"id": devb["id"], "enc_seed_hex": seed("enc-b").hex(), "sign_seed_hex": seed("sign-b").hex(),
                                 "enc_pk": C.b64(devb["enc_pk"])},
                      "ids": sorted(rcpt), "sealed": sealed, "message": "m1"},
        "messages": {"m1": m1, "m2": m2},
        "file": {"chat_ref": REF, "ad_hex": C.lp("saga-chat/v2/file", REF).hex(), "plain_b64": C.b64(fdata), "key": C.b64(fkey),
                 "nonce": C.b64(seed("fnonce")[:24]), "blob": C.b64(blob)},
        "safety": {"handle_a": HA, "ident_a": C.b64(ia["ident_pk"]), "handle_b": HB, "ident_b": C.b64(ib["ident_pk"]),
                   "number": C.safety_number_v2(HA, ia["ident_pk"], HB, ib["ident_pk"])},
        "reject": [
            {"case": "handle_cert_other_handle", "what": "handle_cert", "handle": HB, "sig": C.b64(C.handle_sig(HA, ia))},
            {"case": "device_cert_v1_as_v2", "what": "device_cert", "handle": HA, "fed_sig": C.b64(v1dev)},
            {"case": "device_cert_other_handle", "what": "device_cert", "handle": HB, "fed_sig": C.b64(C.device_sig_v2(HA, ia, dev))},
            {"case": "message_other_chat_ref", "what": "message", "message": "m1", "chat_ref": "ffffffffffffffffffffffffffffffff@a.example"},
            {"case": "message_other_sender", "what": "message", "message": "m1", "sender_handle": HB},
            {"case": "message_ai_removed", "what": "message", "message": "m2", "ai": None},
            {"case": "message_ts_changed", "what": "message", "message": "m1", "ts": m1["ts"] + 1},
            {"case": "device_set_other_ids", "what": "device_set", "handle": HA, "ver": 3, "ids": [dev["id"]], "sig": dset["sig"]},
            {"case": "device_set_other_ver", "what": "device_set", "handle": HA, "ver": 4, "ids": dset["ids"], "sig": dset["sig"]},
            {"case": "device_set_other_handle", "what": "device_set", "handle": HB, "ver": 3, "ids": dset["ids"], "sig": dset["sig"]},
        ],
        "tofu": {
            "key": "handle (vollstaendig, '~x@host')",
            "note": ("ver = Version der signierten Geraete-Liste (device_set). Eine Warnung bleibt bestehen, bis die Person sie "
                     "bestaetigt (changed bleibt gespeichert); eine aeltere Geraete-Liste als schon gesehen wird abgelehnt."),
            "rules": [
                {"step": "first_seen", "stored": None, "seen": "ident_a", "ver": 1, "result": "trust",
                 "store": {"ident": "ident_a", "verified": False, "dver": 1}},
                {"step": "same", "stored": {"ident": "ident_a", "verified": False, "dver": 1}, "seen": "ident_a", "ver": 1, "result": "trust"},
                {"step": "same_newer_devices", "stored": {"ident": "ident_a", "verified": False, "dver": 1}, "seen": "ident_a", "ver": 2,
                 "result": "trust", "store": {"ident": "ident_a", "verified": False, "dver": 2}},
                {"step": "device_set_rollback", "stored": {"ident": "ident_a", "verified": False, "dver": 2}, "seen": "ident_a", "ver": 1,
                 "result": "reject"},
                {"step": "changed", "stored": {"ident": "ident_a", "verified": False, "dver": 2}, "seen": "ident_b", "ver": 1, "result": "warn",
                 "store": {"ident": "ident_b", "verified": False, "changed": True, "old": "ident_a", "dver": 1}},
                {"step": "changed_again", "stored": {"ident": "ident_b", "verified": False, "changed": True, "old": "ident_a", "dver": 1},
                 "seen": "ident_b", "ver": 1, "result": "warn"},
                {"step": "changed_acknowledged", "stored": {"ident": "ident_b", "verified": False, "changed": False, "old": "ident_a", "dver": 1},
                 "seen": "ident_b", "ver": 1, "result": "trust"},
                {"step": "changed_verified", "stored": {"ident": "ident_a", "verified": True, "dver": 1}, "seen": "ident_b", "ver": 1,
                 "result": "block"},
                {"step": "bad_cert", "stored": None, "seen": "invalid handle_cert", "result": "reject"},
            ]},
    }
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("geschrieben:", PATH)


def check(path=PATH):
    """-> Liste [(name, ok)] - PyNaCl-Referenz und Server-Referenz (saga/chat_v2.py)"""
    v = json.load(open(path, encoding="utf-8"))
    res = []

    def ok(n, c):
        res.append((n, bool(c)))
    i, hc, d, rc, f, s = v["identity"], v["handle_cert"], v["device"], v["recipient"], v["file"], v["safety"]
    ok("format", v.get("format") == "saga-chat/v2")
    ok("lp", C.lp(*v["lp"]["fields"]).hex() == v["lp"]["hex"] == SV.lp(*v["lp"]["fields"]).hex())
    ia = C.identity(bytes.fromhex(i["ident_seed_hex"]), bytes.fromhex(i["backup_seed_hex"]))
    ok("identity keys", C.b64(ia["ident_pk"]) == i["ident_pk"] and C.b64(ia["backup_pk"]) == i["backup_pk"] and C.b64(ia["ident_sk"]) == i["ident_sk"])
    ok("handle cert msg", C.handle_msg(hc["handle"], ia["ident_pk"], ia["backup_pk"]).hex() == hc["msg_hex"]
       == SV.handle_msg(hc["handle"], ia["ident_pk"], ia["backup_pk"]).hex())
    ok("handle cert sig (PyNaCl)", C.b64(C.handle_sig(hc["handle"], ia)) == hc["sig"])
    ok("handle cert verify (Server)", SV.verify_handle(hc["handle"], i["ident_pk"], i["backup_pk"], hc["sig"]))
    dev = C.device(d["id"], bytes.fromhex(d["enc_seed_hex"]), bytes.fromhex(d["sign_seed_hex"]))
    ok("device keys", C.b64(dev["enc_pk"]) == d["enc_pk"] and C.b64(dev["sign_pk"]) == d["sign_pk"] and C.b64(dev["sign_sk"]) == d["sign_sk"])
    ok("device cert v2 msg", C.device_msg_v2(i["handle"], d["id"], dev["enc_pk"], dev["sign_pk"]).hex() == d["msg_hex"]
       == SV.device_msg(i["handle"], d["id"], dev["enc_pk"], dev["sign_pk"]).hex())
    ok("device cert v2 sig", C.b64(C.device_sig_v2(i["handle"], ia, dev)) == d["fed_sig"]
       and SV.verify_device(i["handle"], i["ident_pk"], d["id"], d["enc_pk"], d["sign_pk"], d["fed_sig"]))
    for name, m in v["messages"].items():
        enc = C.encrypt_v2(m["chat_ref"], m["sender_handle"], dev, {}, m["body_json"], reply_to=m["reply_to"], ai=m["ai"], mid=m["id"], ts=m["ts"],
                           key=C.unb64(m["key"]), nonce=C.unb64(m["nonce"]))
        ok(f"{name} ad", C.msg_ad_v2(m["chat_ref"], m["id"], m["sender_handle"], m["sender_device"], m["ts"], m["reply_to"], m["ai"]).hex()
           == m["ad_hex"] == SV.msg_ad(m["chat_ref"], m["id"], m["sender_handle"], m["sender_device"], m["ts"], m["reply_to"], m["ai"]).hex())
        ok(f"{name} encrypt", enc["body_ct"] == m["body_ct"])
        ok(f"{name} sig", enc["sig"] == m["sig"] and C.sig_msg_v2(m["chat_ref"], m["id"], m["sender_handle"], m["sender_device"], m["ts"], m["reply_to"],
                                                                 m["ai"], m["body_ct"]).hex() == m["sig_msg_hex"])
        ok(f"{name} verify (Server)", SV.verify_message(d["sign_pk"], m["chat_ref"], m))
        raw = C.unb64(m["body_ct"])
        pt = nb.crypto_aead_xchacha20poly1305_ietf_decrypt(raw[24:], bytes.fromhex(m["ad_hex"]), raw[:24], C.unb64(m["key"]))
        ok(f"{name} open", json.loads(pt) == json.loads(m["body_json"]))
    m2 = v["messages"]["m2"]
    ok("m2 ai = sha256(text)", m2["ai"] == hashlib.sha256(json.loads(m2["body_json"])["text"].encode()).hexdigest())
    ib = C.identity(bytes.fromhex(rc["identity"]["ident_seed_hex"]), bytes.fromhex(rc["identity"]["backup_seed_hex"]))
    devb = C.device(rc["device"]["id"], bytes.fromhex(rc["device"]["enc_seed_hex"]), bytes.fromhex(rc["device"]["sign_seed_hex"]))
    host_b = rc["identity"]["handle"].split("@", 1)[1]
    ok("recipient ids", sorted(rc["ids"]) == sorted([f"dev:{devb['id']}@{host_b}", "backup:" + rc["identity"]["handle"]])
       and SV.device_recipient(devb["id"], host_b) in rc["ids"] and SV.backup_recipient(rc["identity"]["handle"]) in rc["ids"])
    mm = v["messages"][rc["message"]]
    want = json.loads(mm["body_json"])
    ok("open sealed (device)", json.loads(C.decrypt_v2(mm["chat_ref"], mm, rc["sealed"][f"dev:{devb['id']}@{host_b}"], devb["enc_pk"], devb["enc_sk"])) == want)
    ok("open sealed (backup)", json.loads(C.decrypt_v2(mm["chat_ref"], mm, rc["sealed"]["backup:" + rc["identity"]["handle"]],
                                                       ib["backup_pk"], ib["backup_sk"])) == want)
    ok("file", C.b64(C.encrypt_file_v2(f["chat_ref"], C.unb64(f["plain_b64"]), C.unb64(f["key"]), C.unb64(f["nonce"]))[0]) == f["blob"]
       and C.decrypt_file_v2(f["chat_ref"], C.unb64(f["blob"]), C.unb64(f["key"])) == C.unb64(f["plain_b64"])
       and SV.file_ad(f["chat_ref"]).hex() == f["ad_hex"])
    ok("safety number v2", C.safety_number_v2(s["handle_a"], C.unb64(s["ident_a"]), s["handle_b"], C.unb64(s["ident_b"])) == s["number"]
       == C.safety_number_v2(s["handle_b"], C.unb64(s["ident_b"]), s["handle_a"], C.unb64(s["ident_a"]))
       == SV.safety_number(s["handle_a"], C.unb64(s["ident_a"]), s["handle_b"], C.unb64(s["ident_b"])))
    ds = v["device_set"]
    ok("device set msg", C.devices_msg_v2(ds["handle"], ds["ver"], ds["ids"]).hex() == ds["msg_hex"] == SV.devices_msg(ds["handle"], ds["ver"], ds["ids"]).hex())
    ok("device set sig", C.devices_sig_v2(ds["handle"], ia, ds["ver"], ds["ids"])["sig"] == ds["sig"]
       and SV.verify_devices(ds["handle"], i["ident_pk"], {"ver": ds["ver"], "ids": ds["ids"], "sig": ds["sig"]}))
    for r in v["reject"]:
        if r["what"] == "device_set":
            ok(f"reject: {r['case']}", not SV.verify_devices(r["handle"], i["ident_pk"], {"ver": r["ver"], "ids": r["ids"], "sig": r["sig"]}))
            continue
        if r["what"] == "handle_cert":
            good = SV.verify_handle(r["handle"], i["ident_pk"], i["backup_pk"], r["sig"])
        elif r["what"] == "device_cert":
            good = SV.verify_device(r["handle"], i["ident_pk"], d["id"], d["enc_pk"], d["sign_pk"], r["fed_sig"])
        else:
            m = dict(v["messages"][r["message"]])
            for k in ("sender_handle", "ai", "ts"):
                if k in r:
                    m[k] = r[k]
            good = SV.verify_message(d["sign_pk"], r.get("chat_ref", m["chat_ref"]), m)
        ok(f"reject: {r['case']}", not good)
    ok("tofu rules present", len(v["tofu"]["rules"]) >= 4)
    for r in v["tofu"]["rules"]:
        if r["step"] == "bad_cert":
            continue
        got = C.tofu_v2(r["stored"], r["seen"], r.get("ver"))
        ok(f"tofu: {r['step']}", got[0] == r["result"] and (got[1] or None) == (r.get("store") or None))
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    res = check()
    for n, good in res:
        print("OK  " if good else "FAIL", n)
    print(f"VEKTOREN v2 {sum(g for _, g in res)}/{len(res)}", "- ALLES GRUEN" if all(g for _, g in res) else "- FEHLER")
    sys.exit(0 if all(g for _, g in res) else 1)
