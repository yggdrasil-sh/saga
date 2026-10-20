#!/usr/bin/env python3
"""Testvektoren fuer Chat-Stufe 3 (tests/vectors/chat_v3.json): Reaktionen, Bearbeiten, Ablehnfaelle, Vertrauens-Uebernahme.

  python3 tools/chat_vectors_v3.py gen      Datei neu schreiben (feste Seeds/Nonces; nur bei Formataenderung)
  python3 tools/chat_vectors_v3.py check    Datei pruefen: PyNaCl (tests/chatlib.py) UND Server-Referenz (saga/chat_v3.py)

Gegenprobe mit libsodium.js + static/chat-crypto.js: node tools/chat_vectors_v3.mjs. Web, iOS/macOS, Android und Linux
pruefen ihre Umsetzung gegen DIESELBE Datei (CI-Pflicht). Format: saga/chat_v3.py; Nachrichten selbst sind saga-chat/v2.
Abschnitt "reject": Faelle, die jede Umsetzung ABLEHNEN muss. Abschnitt "tofu_migrate": Uebernahme trust:<benutzer> ->
trust2:<handle> (nur bei gleichem Identitaetsschluessel)."""
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
from saga import chat_v2 as SV2, chat_v3 as SV  # noqa: E402  (eigenstaendig, ohne Saga-Konfiguration)

PATH = os.path.join(ROOT, "tests", "vectors", "chat_v3.json")
HA, HB = "~abcdefghijklmnop@a.example", "~qrstuvwxyz234567@a.example"
REF = "0123456789abcdef0123456789abcdef@a.example"
REF2 = "fedcba9876543210fedcba9876543210@a.example"
DEV_A, DEV_B = "0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"
TS0 = 1767225600123                   # Original (ms)
CREATED0 = 1767225600.5               # Serverzeit des Originals (s)


def seed(tag):
    return C.gh(("saga-chat-v3-vector-seed:" + tag).encode())


def keys():
    ia = C.identity(seed("ident-a"), seed("backup-a"))
    ib = C.identity(seed("ident-b"), seed("backup-b"))
    da = C.device(DEV_A, seed("enc-a"), seed("sign-a"))
    db = C.device(DEV_B, seed("enc-b"), seed("sign-b"))
    return ia, ib, da, db


def message(dev, handle, tag, body, ts, ref=REF, reply_to=None, ai=None, kind=None, edit_of=None, created=None):
    plain = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    K, nonce, mid = seed(tag + "-K"), seed(tag + "-nonce")[:24], C.gh(("mid:" + tag).encode())[:16].hex()
    m = C.encrypt_v2(ref, handle, dev, {}, plain, reply_to=reply_to, ai=ai, mid=mid, ts=ts, key=K, nonce=nonce)
    out = {"chat_ref": ref, "id": mid, "sender_handle": handle, "sender_device": dev["id"], "ts": ts, "reply_to": reply_to, "ai": ai,
           "body_json": plain, "key": C.b64(K), "nonce": C.b64(nonce),
           "ad_hex": C.msg_ad_v2(ref, mid, handle, dev["id"], ts, reply_to, ai).hex(), "body_ct": m["body_ct"],
           "sig_msg_hex": C.sig_msg_v2(ref, mid, handle, dev["id"], ts, reply_to, ai, m["body_ct"]).hex(), "sig": m["sig"]}
    if kind:
        out["kind"] = kind
    if edit_of:
        out["edit_of"] = edit_of
    if created is not None:
        out["created"] = created
    return out


def reaction(K, mid, handle, dev, emoji, ts, tag, ref=REF):
    r = C.encrypt_reaction(K, ref, mid, handle, dev, emoji, ts=ts, nonce=seed(tag + "-nonce")[:24])
    body = json.dumps({"e": emoji, "ts": ts}, ensure_ascii=False, separators=(",", ":"))
    return dict(r, chat_ref=ref, msg_id=mid, msg_key=C.b64(K), emoji=emoji, nonce=C.b64(seed(tag + "-nonce")[:24]), body_json=body,
                react_key_hex=C.react_key(K, ref, mid).hex(), ad_hex=C.react_ad(ref, mid, handle, dev["id"]).hex(),
                sig_msg_hex=C.react_sig_msg(ref, mid, handle, dev["id"], ts, r["ct"]).hex())


def raw_reaction(K, mid, handle, dev, body, ts, tag, ref=REF):
    """Reaktion mit beliebigem (falschem) Inhalt, aber korrekt verschluesselt und signiert -> Ablehnung erst beim Inhalt"""
    nonce = seed(tag + "-nonce")[:24]
    ct = C.b64(nonce + nb.crypto_aead_xchacha20poly1305_ietf_encrypt(body.encode(), C.react_ad(ref, mid, handle, dev["id"]), nonce,
                                                                     C.react_key(K, ref, mid)))
    return {"by": handle, "dev": dev["id"], "ts": ts, "ct": ct, "sig": C.b64(C.sign(C.react_sig_msg(ref, mid, handle, dev["id"], ts, ct), dev["sign_sk"])),
            "body_json": body}


def gen():
    ia, ib, da, db = keys()
    m0 = message(da, HA, "m0", {"v": 1, "type": "text", "text": "Treffen wir uns um 8?"}, TS0, created=CREATED0)
    ai_text = "Gern – kurz zusammengefasst."
    m1 = message(da, HA, "m1", {"v": 1, "type": "text", "text": ai_text}, TS0 + 5, ai=hashlib.sha256(ai_text.encode()).hexdigest(),
                 created=CREATED0 + 1)
    K0, K1 = C.unb64(m0["key"]), C.unb64(m1["key"])
    r1 = reaction(K0, m0["id"], HB, db, "👍", TS0 + 60000, "r1")
    r2 = reaction(K0, m0["id"], HB, db, "👩🏽‍💻", TS0 + 61000, "r2")          # ein Graphem aus mehreren Codepunkten
    r0 = reaction(K0, m0["id"], HB, db, "", TS0 + 62000, "r0")                # zurueckgenommen
    e1 = message(da, HA, "e1", {"edit": m0["id"], "t": "Treffen wir uns um 9?"}, TS0 + 120000, kind="edit", edit_of=m0["id"],
                 created=CREATED0 + 120)
    e_b = message(db, HB, "eb", {"edit": m0["id"], "t": "Untergeschoben"}, TS0 + 130000, kind="edit", edit_of=m0["id"], created=CREATED0 + 130)
    e_x = message(da, HA, "ex", {"edit": m1["id"], "t": "Treffen wir uns um 10?"}, TS0 + 140000, kind="edit", edit_of=m0["id"], created=CREATED0 + 140)
    e_ai = message(da, HA, "eai", {"edit": m1["id"], "t": "KI-Text geaendert"}, TS0 + 150000, kind="edit", edit_of=m1["id"], created=CREATED0 + 150)
    e_late = message(da, HA, "elate", {"edit": m0["id"], "t": "Zu spaet"}, TS0 + 90000000, kind="edit", edit_of=m0["id"],
                     created=CREATED0 + SV.EDIT_WINDOW + 1)
    r_ts = raw_reaction(K0, m0["id"], HB, db, json.dumps({"e": "👍", "ts": TS0 + 1}, separators=(",", ":")), TS0 + 63000, "rts")
    r_long = raw_reaction(K0, m0["id"], HB, db, json.dumps({"e": "👍" * 9, "ts": TS0 + 64000}, ensure_ascii=False, separators=(",", ":")),
                          TS0 + 64000, "rlong")
    r_key = reaction(K1, m0["id"], HB, db, "❤️", TS0 + 65000, "rkey")         # Schluessel einer anderen Nachricht
    out = {
        "format": "saga-chat/v3",
        "note": ("Erzeugt mit tools/chat_vectors_v3.py (PyNaCl %s). B64 = Standard-Base64 mit Padding; *_hex = Hex. Format: saga/chat_v3.py; "
                 "Nachrichten (auch Bearbeitungen) sind saga-chat/v2 (tests/vectors/chat_v2.json). Geraete-Zertifikate wie v2." % nacl.__version__),
        "identities": {
            "a": {"handle": HA, "ident_seed_hex": seed("ident-a").hex(), "backup_seed_hex": seed("backup-a").hex(), "ident_pk": C.b64(ia["ident_pk"])},
            "b": {"handle": HB, "ident_seed_hex": seed("ident-b").hex(), "backup_seed_hex": seed("backup-b").hex(), "ident_pk": C.b64(ib["ident_pk"])}},
        "devices": {
            "a": {"id": da["id"], "enc_seed_hex": seed("enc-a").hex(), "sign_seed_hex": seed("sign-a").hex(), "sign_pk": C.b64(da["sign_pk"])},
            "b": {"id": db["id"], "enc_seed_hex": seed("enc-b").hex(), "sign_seed_hex": seed("sign-b").hex(), "sign_pk": C.b64(db["sign_pk"])}},
        "messages": {"m0": m0, "m1": m1},
        "reactions": {"r1": r1, "r2": r2, "r0": r0},
        "edit": {"original": "m0", "message": e1, "text": "Treffen wir uns um 9?"},
        "edits_bad": {"eb": e_b, "ex": e_x, "eai": e_ai, "elate": e_late},
        "limits": {"emoji_max_bytes": SV.EMOJI_MAX, "edit_window_s": SV.EDIT_WINDOW, "edit_max": SV.EDIT_MAX},
        "reject": [
            {"case": "react_other_message", "what": "reaction", "reaction": "r1", "msg_id": m1["id"]},
            {"case": "react_other_handle", "what": "reaction", "reaction": "r1", "by": HA},
            {"case": "react_other_device", "what": "reaction", "reaction": "r1", "dev": DEV_A},
            {"case": "react_ts_changed", "what": "reaction", "reaction": "r1", "ts": r1["ts"] + 1},
            {"case": "react_other_chat_ref", "what": "reaction", "reaction": "r1", "chat_ref": REF2},
            {"case": "react_signed_by_other_device", "what": "reaction", "reaction": "r1", "sign_pk": "a"},
            {"case": "react_key_of_other_message", "what": "reaction_open", "reaction": dict(r_key, msg_key=m0["key"]), "msg": "m0"},
            {"case": "react_body_ts_mismatch", "what": "reaction_body", "reaction": r_ts, "msg": "m0"},
            {"case": "react_emoji_too_long", "what": "reaction_body", "reaction": r_long, "msg": "m0"},
            {"case": "edit_other_sender", "what": "edit", "edit": "eb", "original": "m0", "reason": "other_sender"},
            {"case": "edit_wrong_edit_of", "what": "edit", "edit": "ex", "original": "m0", "reason": "edit_of_mismatch"},
            {"case": "edit_ai_original", "what": "edit", "edit": "eai", "original": "m1", "reason": "ai_original"},
            {"case": "edit_window", "what": "edit", "edit": "elate", "original": "m0", "reason": "window"},
            {"case": "edit_other_chat_ref", "what": "edit_sig", "edit": "e1", "chat_ref": REF2},
        ],
        "tofu_migrate": {
            "note": ("Nur solange es keinen Eintrag trust2:<handle> gibt (sonst gilt tofu_v2). v1 = trust:<benutzer> {ident, verified, changed, "
                     "old?}, seen = selbst gepruefter v2-Identitaetsschluessel, ver = Version der signierten Geraete-Liste."),
            "rules": [
                {"step": "no_v1", "v1": None, "seen": "ident_a", "ver": 2, "result": "trust", "store": {"ident": "ident_a", "verified": False, "dver": 2}},
                {"step": "same_ident", "v1": {"ident": "ident_a", "verified": False, "changed": False}, "seen": "ident_a", "ver": 2,
                 "result": "trust", "store": {"ident": "ident_a", "verified": False, "dver": 2}},
                {"step": "same_ident_verified", "v1": {"ident": "ident_a", "verified": True, "changed": False}, "seen": "ident_a", "ver": 3,
                 "result": "trust", "store": {"ident": "ident_a", "verified": True, "dver": 3}},
                {"step": "same_ident_changed", "v1": {"ident": "ident_a", "verified": False, "changed": True, "old": "ident_b"}, "seen": "ident_a",
                 "ver": 1, "result": "warn", "store": {"ident": "ident_a", "verified": False, "dver": 1, "changed": True, "old": "ident_b"}},
                {"step": "other_ident", "v1": {"ident": "ident_a", "verified": False, "changed": False}, "seen": "ident_b", "ver": 1,
                 "result": "warn", "store": {"ident": "ident_b", "verified": False, "changed": True, "old": "ident_a", "dver": 1}},
                {"step": "other_ident_verified", "v1": {"ident": "ident_a", "verified": True, "changed": False}, "seen": "ident_b", "ver": 1,
                 "result": "block"},
            ]},
    }
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("geschrieben:", PATH)


def check(path=PATH):
    """-> Liste [(name, ok)] - PyNaCl-Referenz (tests/chatlib.py) und Server-Referenz (saga/chat_v3.py)"""
    v = json.load(open(path, encoding="utf-8"))
    res = []

    def ok(n, c):
        res.append((n, bool(c)))
    ok("format", v.get("format") == "saga-chat/v3")
    ids = {k: C.identity(bytes.fromhex(x["ident_seed_hex"]), bytes.fromhex(x["backup_seed_hex"])) for k, x in v["identities"].items()}
    devs = {k: C.device(x["id"], bytes.fromhex(x["enc_seed_hex"]), bytes.fromhex(x["sign_seed_hex"])) for k, x in v["devices"].items()}
    ok("identity/device keys", all(C.b64(ids[k]["ident_pk"]) == x["ident_pk"] for k, x in v["identities"].items())
       and all(C.b64(devs[k]["sign_pk"]) == x["sign_pk"] for k, x in v["devices"].items()))
    by_handle = {x["handle"]: k for k, x in v["identities"].items()}
    dev_of = {x["id"]: k for k, x in v["devices"].items()}
    msgs = dict(v["messages"], e1=v["edit"]["message"], **v["edits_bad"])

    def msg_ok(name, m):
        d = devs[dev_of[m["sender_device"]]]
        enc = C.encrypt_v2(m["chat_ref"], m["sender_handle"], d, {}, m["body_json"], reply_to=m["reply_to"], ai=m["ai"], mid=m["id"], ts=m["ts"],
                           key=C.unb64(m["key"]), nonce=C.unb64(m["nonce"]))
        ok(f"{name}: v2 encrypt/sig", enc["body_ct"] == m["body_ct"] and enc["sig"] == m["sig"]
           and C.msg_ad_v2(m["chat_ref"], m["id"], m["sender_handle"], m["sender_device"], m["ts"], m["reply_to"], m["ai"]).hex() == m["ad_hex"])
        ok(f"{name}: verify (Server v2)", SV2.verify_message(C.b64(d["sign_pk"]), m["chat_ref"], m))
    for name, m in msgs.items():
        msg_ok(name, m)
    for name, r in v["reactions"].items():
        K = C.unb64(r["msg_key"])
        ok(f"{name}: react key", C.react_key(K, r["chat_ref"], r["msg_id"]).hex() == r["react_key_hex"] == SV.react_key(K, r["chat_ref"], r["msg_id"]).hex())
        ok(f"{name}: react ad", C.react_ad(r["chat_ref"], r["msg_id"], r["by"], r["dev"]).hex() == r["ad_hex"]
           == SV.react_ad(r["chat_ref"], r["msg_id"], r["by"], r["dev"]).hex())
        d = devs[dev_of[r["dev"]]]
        enc = C.encrypt_reaction(K, r["chat_ref"], r["msg_id"], r["by"], d, r["emoji"], ts=r["ts"], nonce=C.unb64(r["nonce"]))
        ok(f"{name}: encrypt", enc["ct"] == r["ct"] and enc["sig"] == r["sig"])
        ok(f"{name}: sig msg", C.react_sig_msg(r["chat_ref"], r["msg_id"], r["by"], r["dev"], r["ts"], r["ct"]).hex() == r["sig_msg_hex"]
           == SV.react_sig_msg(r["chat_ref"], r["msg_id"], r["by"], r["dev"], r["ts"], r["ct"]).hex())
        ok(f"{name}: verify (PyNaCl + Server)", C.verify_reaction(r["chat_ref"], r["msg_id"], r, d["sign_pk"])
           and SV.verify_reaction(C.b64(d["sign_pk"]), r["chat_ref"], r["msg_id"], r))
        pt = C.decrypt_reaction(K, r["chat_ref"], r["msg_id"], r)
        ok(f"{name}: open + body", pt == r["body_json"] and SV.react_body_ok(pt, r["ts"]) == r["emoji"])
        ok(f"{name}: reactor = Handle mit Identitaet", r["by"] in by_handle)
    ed, orig = v["edit"]["message"], v["messages"][v["edit"]["original"]]
    raw = C.unb64(ed["body_ct"])
    body = json.loads(nb.crypto_aead_xchacha20poly1305_ietf_decrypt(raw[24:], bytes.fromhex(ed["ad_hex"]), raw[:24], C.unb64(ed["key"])))
    ok("edit: kind/edit_of/Inhalt", ed.get("kind") == "edit" and ed["edit_of"] == orig["id"] and body == {"edit": orig["id"], "t": v["edit"]["text"]})
    ok("edit: gueltig (Server-Referenz edit_check)", SV.edit_check(orig, ed, body) is None)
    m = v["messages"]
    for r in v["reject"]:
        w = r["what"]
        if w == "reaction":
            x = dict(v["reactions"][r["reaction"]])
            for k in ("by", "dev", "ts"):
                if k in r:
                    x[k] = r[k]
            ref, mid = r.get("chat_ref", x["chat_ref"]), r.get("msg_id", x["msg_id"])
            pk = devs[r["sign_pk"]]["sign_pk"] if "sign_pk" in r else devs[dev_of[x["dev"]]]["sign_pk"] if x["dev"] in dev_of else b"\0" * 32
            good = C.verify_reaction(ref, mid, x, pk) or SV.verify_reaction(C.b64(pk), ref, mid, x)
            if "dev" in r:                          # Geraet getauscht: auch mit dessen Schluessel ungueltig
                good = good or C.verify_reaction(ref, mid, x, devs[dev_of[r["dev"]]]["sign_pk"])
        elif w == "reaction_open":
            x, mm = r["reaction"], m[r["msg"]]
            try:
                C.decrypt_reaction(C.unb64(mm["key"]), mm["chat_ref"], mm["id"], x)
                good = True
            except Exception:  # noqa: BLE001  (Authentifizierung schlaegt fehl)
                good = False
        elif w == "reaction_body":
            x, mm = r["reaction"], m[r["msg"]]
            d = devs[dev_of[x["dev"]]]
            sig_ok = C.verify_reaction(mm["chat_ref"], mm["id"], x, d["sign_pk"])
            pt = C.decrypt_reaction(C.unb64(mm["key"]), mm["chat_ref"], mm["id"], x)
            good = sig_ok and pt == x["body_json"] and SV.react_body_ok(pt, x["ts"]) is not None
        elif w == "edit":
            e, o = msgs[r["edit"]], m[r["original"]]
            rw = C.unb64(e["body_ct"])
            b = json.loads(nb.crypto_aead_xchacha20poly1305_ietf_decrypt(rw[24:], bytes.fromhex(e["ad_hex"]), rw[:24], C.unb64(e["key"])))
            why = SV.edit_check(o, e, b)
            ok(f"reject: {r['case']} (Grund {r['reason']})", why == r["reason"])
            continue
        else:                                       # edit_sig: Signatur gilt nur fuer die eigene chat_ref
            e = msgs[r["edit"]]
            good = SV2.verify_message(C.b64(devs[dev_of[e["sender_device"]]]["sign_pk"]), r["chat_ref"], e)
        ok(f"reject: {r['case']}", not good)
    for r in v["tofu_migrate"]["rules"]:
        got = SV.tofu_migrate(r["v1"], r["seen"], r.get("ver"))
        ok(f"tofu_migrate: {r['step']}", got[0] == r["result"] and (got[1] or None) == (r.get("store") or None))
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    res = check()
    for n, good in res:
        print("OK  " if good else "FAIL", n)
    print(f"VEKTOREN v3 {sum(g for _, g in res)}/{len(res)}", "- ALLES GRUEN" if all(g for _, g in res) else "- FEHLER")
    sys.exit(0 if all(g for _, g in res) else 1)
