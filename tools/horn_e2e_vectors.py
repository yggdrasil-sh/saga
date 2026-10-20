#!/usr/bin/env python3
"""Testvektoren fuer den Frame-E2E von Gjallarhorn (saga-horn/v1, docs/HORN-E2E.md) erzeugen bzw. pruefen.

  python3 tools/horn_e2e_vectors.py gen      tests/vectors/horn_e2e.json neu schreiben (feste Seeds -> gleiche Ausgabe)
  python3 tools/horn_e2e_vectors.py check    Datei gegen diese unabhaengige Umsetzung pruefen (Exit 1 bei Abweichung)

Gegenproben: node tools/horn_e2e_vectors.mjs (static/frame-e2e.js mit WebCrypto) und tests/browser_horn_e2e.js (derselbe
Kern im Worker des Browsers). Die Apps pruefen ihre Ableitungen (HKDF, Ratsche, Runensiegel) und das Rahmenformat gegen
dieselbe Datei, siehe docs/HORN-E2E.md Abschnitt 10. Benoetigt: cryptography, PyNaCl."""
import hashlib
import json
import os
import struct
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from nacl.signing import SigningKey

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "tests", "vectors", "horn_e2e.json")
FRAME_SALT, RATCHET_SALT, INFO0 = b"saga-horn/v1/frame", b"saga-horn-ratchet", b"\0" * 128
IV, TAG, TRAILER, RING, WINDOW, AHEAD, GRACE, KEEP = 12, 16, 14, 16, 128, 4, 3000, 3
RUNES = "ᚠᚢᚦᚨᚱᚲᚷᚹᚺᚾᛁᛃᛇᛈᛉᛊᛏᛒᛖᛗᛚᛜᛞᛟ"
SID = "aaaaaaaaaaaaaaaa"                                   # Sitzung des Senders in den Empfaenger-Faellen


def h32(tag):
    return hashlib.sha256(("saga-horn-vector:" + tag).encode()).digest()


def stream(tag, n):
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(f"saga-horn-vector:{tag}:{i}".encode()).digest()
        i += 1
    return out[:n]


# --- Ableitungen und Rahmen (unabhaengig von static/frame-e2e.js) ------------------------------------------------------
def frame_key(k):
    return HKDF(algorithm=hashes.SHA256(), length=16, salt=FRAME_SALT, info=INFO0).derive(k)


def ratchet(k):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=RATCHET_SALT, info=b"").derive(k)


def header_len(kind, typ):
    if kind == "audio":
        return 1
    return {"key": 10, "delta": 3}.get(typ, -1)


def iv_saga(ssrc, ts, ctr):
    return struct.pack(">III", ssrc, ts, ctr)


def iv_fc(ssrc, ts, count):
    """IV, wie ihn ein FrameCryptor-Sender (webrtc-sdk/LiveKit) schreibt: dritter Teil = ts - (count % 0xFFFF)."""
    return struct.pack(">III", ssrc, ts, (ts - count % 0xFFFF) & 0xFFFFFFFF)


def encrypt(fk, data, h, kid, iv):
    ct = AESGCM(fk).encrypt(iv, data[h:], data[:h])
    return data[:h] + ct + iv + bytes([IV, kid])


def parse(data, h):
    if h < 0:
        return "codec"
    if len(data) < h + TAG + TRAILER:
        return "short"
    if data[-2] != IV or data[-1] >= RING:
        return "format"
    ivo = len(data) - TRAILER
    ssrc, ts, _ = struct.unpack(">III", data[ivo:ivo + IV])
    return {"kid": data[-1], "ivo": ivo, "ssrc": ssrc, "ts": ts}


def decrypt(fk, data, h, p):
    try:
        return data[:h] + AESGCM(fk).decrypt(data[p["ivo"]:p["ivo"] + IV], data[h:p["ivo"]], data[:h])
    except Exception:  # noqa: BLE001
        return None


class Window:
    """Die 128 zuletzt angenommenen RTP-Zeitstempel, serielle Arithmetik mod 2^32."""

    def __init__(self, size=WINDOW):
        self.size, self.vals, self.max = size, [], None

    def rel(self, ts):
        d = (ts - self.max) & 0xFFFFFFFF
        return 0 if d == 0 else (d if d < 0x80000000 else -(0x100000000 - d))

    def check(self, ts):
        if self.max is None:
            return True
        r = self.rel(ts)
        if r > 0:
            return True
        if r == 0 or ts in self.vals:
            return False
        if len(self.vals) < self.size:
            return True
        return -r < max((self.max - v) & 0xFFFFFFFF for v in self.vals)

    def accept(self, ts):
        if self.max is None or self.rel(ts) > 0:
            self.max = ts
        self.vals.append(ts)
        if len(self.vals) > self.size:
            self.vals.pop(0)


class Receiver:
    """Empfaenger-Seite fuer EINEN Sender (Schluesselwahl, Ratsche beim ersten Rahmen, Ablauf alter Schluessel)."""

    def __init__(self):
        self.keys, self.cand, self.newest, self.active = {}, {}, -1, -1

    def add(self, n, k):
        self.keys = {m: x for m, x in self.keys.items() if not (m != n and m % RING == n % RING)}
        self.keys[n] = {"K": k, "fk": frame_key(k), "until": float("inf"), "win": {}}
        self.newest = max(self.newest, n)

    def trim(self, now):
        self.keys = {m: x for m, x in self.keys.items() if m >= self.newest - KEEP + 1 and x["until"] > now}

    def decrypt(self, data, kind, typ, now):
        h = header_len(kind, typ)
        p = parse(data, h)
        if isinstance(p, str):
            return p
        if self.newest < 0:
            return "nokey"
        self.trim(now)
        hit = [(m, x) for m, x in self.keys.items() if m % RING == p["kid"]]
        cand = False
        if hit:
            kn, k = hit[0]
        else:
            j = (p["kid"] - self.newest % RING) % RING
            if not 1 <= j <= AHEAD or self.newest not in self.keys:
                return "nokey"
            kn = self.newest + j
            if kn not in self.cand:
                kk = self.keys[self.newest]["K"]
                for _ in range(j):
                    kk = ratchet(kk)
                self.cand[kn] = {"K": kk, "fk": frame_key(kk), "until": float("inf"), "win": {}}
            k, cand = self.cand[kn], True
        w = k["win"].get(p["ssrc"])
        if w and not w.check(p["ts"]):
            return "replay"
        out = decrypt(k["fk"], data, h, p)
        if out is None:
            return "auth"
        w = k["win"].setdefault(p["ssrc"], Window())
        w.accept(p["ts"])
        if cand:
            self.keys[kn] = self.cand.pop(kn)
            self.newest = max(self.newest, kn)
        if kn > self.active:
            self.active = kn
            for m, x in self.keys.items():
                if m < kn and x["until"] == float("inf"):
                    x["until"] = now + GRACE
        return out


def runes(call_id, pks):
    pks = sorted(set(pks))
    d = hashlib.sha256(call_id.encode() + b"".join(pks)).digest()
    v, idx = int.from_bytes(d[:5], "big"), []
    for _ in range(6):
        idx.append(v % 24)
        v //= 24
    return {"hash": d.hex(), "idx": idx, "runes": "".join(RUNES[i] for i in idx)}


def parse_fkey(b):
    if not isinstance(b, dict) or b.get("t") != "fkey":
        return None
    n, kid, s, key = b.get("n"), b.get("kid"), b.get("s"), b.get("key")
    if type(n) is not int or not 0 <= n <= 0x7FFFFFFF or kid != n % RING or not isinstance(s, str) or len(s) != 16 \
            or any(ch not in "0123456789abcdef" for ch in s) or not isinstance(key, str):
        return None
    import base64
    try:
        k = base64.b64decode(key, validate=True)
    except Exception:  # noqa: BLE001
        return None
    return {"n": n, "key": k, "s": s} if len(k) == 32 else None


# --- Erzeugen ------------------------------------------------------------------------------------------------------
VP8_KEY_HEAD = bytes.fromhex("502c009d012a0005d002")      # Rahmen-Tag (Schluesselbild) + Startcode + 1280x720
VP8_DELTA_HEAD = bytes.fromhex("310c00")
OPUS_TOC = bytes([0xFC])


def gen():
    ks = [h32("K0")]
    for _ in range(2):
        ks.append(ratchet(ks[-1]))
    keys = [{"n": i, "kid": i % RING, "K": k.hex(), "fk": frame_key(k).hex()} for i, k in enumerate(ks)]
    fk = [frame_key(k) for k in ks]
    S_V, S_A = 0x1A2B3C4D, 0x5E6F7081
    frames = []

    def fr(name, kind, typ, plain, n, ssrc, ts, ctr, mode="saga"):
        h = header_len(kind, typ)
        iv = iv_saga(ssrc, ts, ctr) if mode == "saga" else iv_fc(ssrc, ts, ctr)
        out = encrypt(fk[n], plain, h, n % RING, iv)
        d = {"name": name, "kind": kind, "type": typ, "h": h, "key_n": n, "ssrc": ssrc, "ts": ts, "ctr": ctr, "plain": plain.hex(),
             "iv": iv.hex(), "out": out.hex()}
        if mode != "saga":
            d["iv_mode"] = mode
        frames.append(d)
        return d

    vk = fr("vp8-schluesselbild", "video", "key", VP8_KEY_HEAD + stream("vp8key", 600), 0, S_V, 3000000, 0)
    vd = fr("vp8-zwischenbild", "video", "delta", VP8_DELTA_HEAD + stream("vp8delta", 180), 0, S_V, 3003000, 1)
    op = fr("opus", "audio", None, OPUS_TOC + stream("opus", 60), 0, S_A, 480000, 0)
    fr("vp8-zwischenbild-n1", "video", "delta", VP8_DELTA_HEAD + stream("vp8delta-n1", 90), 1, S_V, 3006000, 0)
    fr("opus-framecryptor-iv", "audio", None, OPUS_TOC + stream("opus-fc", 40), 0, S_A, 480960, 7, mode="fc")

    def mut(d, i, x=0x01):
        b = bytearray(bytes.fromhex(d["out"]))
        b[i] ^= x
        return b.hex()

    def setb(d, i, v):
        b = bytearray(bytes.fromhex(d["out"]))
        b[i] = v
        return b.hex()

    def step(d, expect, at=0, typ=None, data=None):
        return {"data": data or d["out"], "kind": d["kind"], "type": typ if typ is not None else d["type"], "at": at, "expect": expect,
                "plain": d["plain"]}

    def mk(name, plain, n, ssrc, ts, ctr, kind="video", typ="delta"):
        h = header_len(kind, typ)
        out = encrypt(fk[n], plain, h, n % RING, iv_saga(ssrc, ts, ctr))
        return {"out": out.hex(), "plain": plain.hex(), "kind": kind, "type": typ}

    vd2 = mk("vd2", VP8_DELTA_HEAD + stream("vd2", 50), 0, S_V, 3006000, 2)
    vn1 = mk("vn1", VP8_DELTA_HEAD + stream("vn1", 50), 1, S_V, 3009000, 0)
    vn2 = mk("vn2", VP8_DELTA_HEAD + stream("vn2", 50), 2, S_V, 3012000, 0)
    vk0_late = mk("vk0l", VP8_DELTA_HEAD + stream("vk0l", 50), 0, S_V, 3010500, 3)
    cases = [
        {"name": "annehmen-dann-wiederholung", "have": [0], "steps": [step(vd, "ok"), step({**vd2, "plain": vd2["plain"]}, "ok"), step(vd, "replay")]},
        {"name": "ohne-schluessel", "have": [], "steps": [step(vd, "nokey"), step(op, "nokey")]},
        {"name": "falscher-kid", "have": [0], "steps": [step(vd, "nokey", data=setb(vd, -1, 9))]},
        {"name": "kid-zeigt-auf-anderen-schluessel", "have": [0, 1], "steps": [step(vd, "auth", data=setb(vd, -1, 1))]},
        {"name": "aad-manipuliert", "have": [0], "steps": [step(vk, "auth", data=mut(vk, 4)), step(op, "auth", data=mut(op, 0, 0x80))]},
        {"name": "kopfart-vertauscht", "have": [0], "steps": [step(vd, "auth", typ="key")]},
        {"name": "chiffrat-manipuliert", "have": [0], "steps": [step(vd, "auth", data=mut(vd, 20))]},
        {"name": "tag-manipuliert", "have": [0], "steps": [step(vd, "auth", data=mut(vd, -TRAILER - 1))]},
        {"name": "iv-manipuliert", "have": [0], "steps": [step(vd, "auth", data=mut(vd, -TRAILER + 11))]},
        {"name": "iv-laenge-falsch", "have": [0], "steps": [step(vd, "format", data=setb(vd, -2, 16))]},
        {"name": "kid-ausserhalb-ring", "have": [0], "steps": [step(vd, "format", data=setb(vd, -1, 200))]},
        {"name": "zu-kurz", "have": [0], "steps": [step(op, "short", data=op["out"][:2 * (1 + TAG + TRAILER - 1)])]},
        {"name": "ratsche-beim-ersten-rahmen", "have": [0], "steps": [step(vn1, "ok", at=1000), step(vk0_late, "ok", at=3500),
                                                                      step(vd2, "nokey", at=4001)]},
        {"name": "ratsche-zwei-schritte", "have": [0], "steps": [step(vn2, "ok")]},
        {"name": "framecryptor-iv", "have": [0], "steps": [step(frames[4], "ok")]},
    ]
    # Fenster mit echten Rahmen: 130 Opus-Rahmen, #100 zuerst ausgelassen und spaet zugestellt, dann der erste erneut
    win = []
    for i in range(131):
        win.append(mk(f"w{i}", OPUS_TOC + stream(f"win{i}", 8), 0, S_A, (0xFFFFF000 + 960 * i) & 0xFFFFFFFF, i, kind="audio", typ=None))
    ws = [step(x, "ok") for i, x in enumerate(win) if i != 100] + [step(win[100], "ok"), step(win[100], "replay"), step(win[0], "replay"),
                                                                    step(win[2], "replay")]
    cases.append({"name": "fenster-128-ueber-zeitstempel-ueberlauf", "have": [0], "steps": ws})

    # Fenster abstrakt (nur Zeitstempel)
    a = 0xFFFFF000
    seq = [(a, True), (a + 3000, True), (a + 6000, True), (a + 3000, False), (a, False), (a + 4500, True)]
    t = a + 6000
    for _ in range(127):
        t += 3000
        seq.append((t, True))
    seq += [(a + 4500, False), (a + 1500, False), (t - 1500, True), (t - 1500, False), (t + 0x80000000, False), (t + 0x7FFFFFFF, True)]
    steps = [{"ts": x & 0xFFFFFFFF, "ok": y} for x, y in seq]

    devs = [SigningKey(h32(f"dev{i}")).verify_key.encode() for i in range(4)]
    cid = "c0ffee00c0ffee00c0ffee00c0ffee00"
    rn = []
    for name, ds in (("drei-geraete", devs[:3]), ("vier-geraete", devs), ("andere-reihenfolge-doppelt", [devs[2], devs[0], devs[1], devs[0]])):
        r = runes(cid, ds)
        rn.append({"name": name, "call_id": cid, "devices": [d.hex() for d in ds], **r})

    import base64
    fb = {"t": "fkey", "n": 1, "kid": 1, "key": base64.b64encode(ks[1]).decode(), "s": "0123456789abcdef"}
    inval = [dict(fb, kid=2), dict(fb, key=base64.b64encode(ks[1][:31]).decode()), dict(fb, s="0123456789ABCDEF"), dict(fb, n=-1),
             dict(fb, t="fkeys"), dict(fb, n=1.5), dict(fb, key="kein base64!")]
    out = {
        "format": "saga-horn/v1",
        "note": "Erzeugt mit tools/horn_e2e_vectors.py (cryptography + PyNaCl). Bytes als Hex. Rahmen = Kopf(h) || AES-128-GCM(FK, IV, "
                "AAD=Kopf, Rest) || Tag(16) || IV(12) || 0x0C || kid. FK = HKDF-SHA256(K, salt frame_salt, info 128 x 00, 16). "
                "Ratsche K' = HKDF-SHA256(K, salt ratchet_salt, info leer, 32). cases: Empfaenger-Faelle fuer EINEN Sender "
                "(Sitzung " + SID + "), have = vorab per fkey eingesetzte Schluessel (n aus keys), at = Uhr in ms, expect = ok | nokey | "
                "auth | replay | short | format. window.steps: nur Zeitstempel, ok = annehmen. Siehe docs/HORN-E2E.md.",
        "params": {"frame_salt": FRAME_SALT.decode(), "frame_info": "128 x 0x00", "ratchet_salt": RATCHET_SALT.decode(), "ratchet_info": "",
                   "iv_len": IV, "tag_len": TAG, "trailer_len": TRAILER, "kid_ring": RING, "window": WINDOW, "ratchet_ahead": AHEAD,
                   "switch_ms": 1500, "grace_ms": GRACE, "keep": KEEP, "header": {"opus": 1, "vp8_key": 10, "vp8_delta": 3}},
        "keys": keys,
        "frames": frames,
        "cases": cases,
        "window": {"size": WINDOW, "steps": steps},
        "runes": rn,
        "rune_alphabet": RUNES,
        "fkey": {"body": json.dumps(fb, separators=(",", ":")), "n": 1, "key_hex": ks[1].hex(), "invalid": inval},
    }
    # Selbstpruefung der Erwartungen gegen diese Umsetzung
    problems = check(out)
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"geschrieben: {PATH} ({os.path.getsize(PATH)} Byte)")


# --- Pruefen -------------------------------------------------------------------------------------------------------
def check(v):
    bad, n = [], 0

    def chk(name, cond):
        nonlocal n
        n += 1
        if not cond:
            bad.append("FAIL " + name)

    for i, x in enumerate(v["keys"]):
        k = bytes.fromhex(x["K"])
        if i:
            chk(f"ratsche n={x['n']}", ratchet(bytes.fromhex(v["keys"][i - 1]["K"])).hex() == x["K"])
        chk(f"fk n={x['n']}", frame_key(k).hex() == x["fk"] and x["kid"] == x["n"] % RING)
    fks = {x["n"]: bytes.fromhex(x["fk"]) for x in v["keys"]}
    for fr in v["frames"]:
        h = header_len(fr["kind"], fr["type"])
        chk(fr["name"] + ": Kopflaenge", h == fr["h"])
        iv = (iv_fc if fr.get("iv_mode") == "fc" else iv_saga)(fr["ssrc"], fr["ts"], fr["ctr"])
        chk(fr["name"] + ": IV", iv.hex() == fr["iv"])
        out = encrypt(fks[fr["key_n"]], bytes.fromhex(fr["plain"]), h, fr["key_n"] % RING, iv)
        chk(fr["name"] + ": verschluesseln", out.hex() == fr["out"])
        p = parse(out, h)
        chk(fr["name"] + ": entschluesseln", not isinstance(p, str) and decrypt(fks[fr["key_n"]], out, h, p) == bytes.fromhex(fr["plain"]))
        chk(fr["name"] + ": Laenge", len(out) == len(fr["plain"]) // 2 + TAG + TRAILER)
    for cs in v["cases"]:
        r = Receiver()
        for m in cs["have"]:
            r.add(m, bytes.fromhex(v["keys"][m]["K"]))
        for i, s in enumerate(cs["steps"]):
            res = r.decrypt(bytes.fromhex(s["data"]), s["kind"], s["type"], s["at"])
            got = res if isinstance(res, str) else ("ok" if res.hex() == s["plain"] else "falsch")
            chk(f"{cs['name']} #{i} ({got} statt {s['expect']})", got == s["expect"])
    w = Window(v["window"]["size"])
    for i, s in enumerate(v["window"]["steps"]):
        okk = w.check(s["ts"])
        if okk:
            w.accept(s["ts"])
        chk(f"fenster #{i}", okk == s["ok"])
    for rr in v["runes"]:
        got = runes(rr["call_id"], [bytes.fromhex(d) for d in rr["devices"]])
        chk("runen " + rr["name"], got == {"hash": rr["hash"], "idx": rr["idx"], "runes": rr["runes"]})
    chk("runen: Reihenfolge/Doppelte egal", v["runes"][0]["runes"] == v["runes"][2]["runes"])
    chk("runen: viertes Geraet aendert das Siegel", v["runes"][0]["hash"] != v["runes"][1]["hash"])
    pf = parse_fkey(json.loads(v["fkey"]["body"]))
    chk("fkey parsen", pf is not None and pf["n"] == v["fkey"]["n"] and pf["key"].hex() == v["fkey"]["key_hex"])
    for i, b in enumerate(v["fkey"]["invalid"]):
        chk(f"fkey ungueltig #{i}", parse_fkey(b) is None)
    if not bad:
        print(f"horn_e2e: {n} Pruefungen gruen")
    return bad


if __name__ == "__main__":
    if sys.argv[1:] == ["gen"]:
        gen()
    elif sys.argv[1:] == ["check"]:
        with open(PATH, encoding="utf-8") as f:
            probs = check(json.load(f))
        print("\n".join(probs) if probs else "ALLES GRUEN")
        sys.exit(1 if probs else 0)
    else:
        print(__doc__)
        sys.exit(2)
