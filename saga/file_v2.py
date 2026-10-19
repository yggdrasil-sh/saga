"""Datei-Format saga-file/v2 (2.4, Videos im Chat): blockweise verschluesselt, wahlfreier Zugriff (Range, Streaming).

Der Server rechnet hier nichts Kryptografisches - er prueft beim Abschluss eines Uploads nur den Kopf und die Groesse.
Ver- und Entschluesseln machen allein die Geraete (Schluessel K nur in der Ende-zu-Ende-verschluesselten Nachricht).

  Blob     = Kopf(48 B) || Block_0 || ... || Block_{N-1}
  Kopf     = "SGF2" || u8 version=2 || u8 flags=0 || u16 0 || u32 cs || u32 0 || u64 plain_size || np(24 B)
             np = nonce_prefix(16 B, zufaellig je Datei) || 8 x 0x00; alle Zahlen big-endian
  N        = max(1, ceil(plain_size / cs))   (auch eine leere Datei hat einen Block: nur das Tag)
  Block_i  = crypto_aead_xchacha20poly1305_ietf_encrypt(plain[i*cs : (i+1)*cs], AD_i, nonce_i, K)   (Laenge <= cs + 16)
  nonce_i  = nonce_prefix(16 B) || u64_be(i)
  AD_i     = LP("saga-file/v2", u64_be(i), u64_be(N), u32_be(cs), u64_be(plain_size), u8(last))   last = 1 nur fuer i = N-1
             LP(f1..fn) = je Feld u32_be(Laenge) || Bytes (wie saga-chat/v1 und v2)
  Groesse  = 48 + plain_size + 16 * N
  Bereich  Klartext-Byte x liegt in Block x // cs, dessen Ciphertext bei 48 + i * (cs + 16) beginnt (Laenge cs + 16,
           der letzte Block kuerzer). Das rechnet nur der Client; der Server liefert rohe Bytes (Range).

Gegen Abschneiden und Vertauschen: i, N, plain_size und die Endmarke stehen in jedem AD. Der Kopf selbst ist nicht
authentisiert, aber jedes Feld geht ueber das AD in jeden Block ein (falscher Kopf -> Entschluesselungsfehler).
Testvektoren: tests/vectors/file_v2.json (tools/file_vectors_v2.py, Gegenprobe node tools/file_vectors_v2.mjs). Beschreibung
fuer die Apps: docs/APP-API.md, Abschnitt "Videos im Chat"."""
import struct

MAGIC = b"SGF2"
VERSION = 2
HEADER = 48
TAG = 16
CS = 256 * 1024                       # Blockgroesse in 2.4 (Apps senden immer diese)
CS_MIN, CS_MAX = 64 * 1024, 1024 * 1024   # was der Server annimmt (Zweierpotenzen dazwischen; Reserve fuer spaeter)
_HEAD = struct.Struct(">4sBBHIIQ24s")


def blocks(plain_size, cs=CS):
    """Anzahl Bloecke N."""
    return max(1, -(-int(plain_size) // int(cs)))


def cipher_size(plain_size, cs=CS):
    """Groesse des Blobs zu einer Klartext-Groesse."""
    return HEADER + int(plain_size) + TAG * blocks(plain_size, cs)


def plain_max_for(cipher, cs=CS):
    """Groesste Klartext-Groesse, deren Blob hoechstens cipher Bytes hat (fuer Grenzen)."""
    n = max(1, -(-(int(cipher) - HEADER) // (cs + TAG)))
    return max(0, int(cipher) - HEADER - TAG * n)


def parse_header(raw):
    """48 Bytes -> {"cs", "size", "n", "prefix"} oder None (falsche Kennung/Version/Felder)."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < HEADER:
        return None
    magic, ver, flags, r16, cs, r32, size, np = _HEAD.unpack(bytes(raw[:HEADER]))
    if magic != MAGIC or ver != VERSION or flags or r16 or r32 or np[16:] != b"\0" * 8:
        return None
    if not (CS_MIN <= cs <= CS_MAX) or cs & (cs - 1):
        return None
    if size >= 1 << 50:
        return None
    return {"cs": cs, "size": size, "n": blocks(size, cs), "prefix": np[:16]}


def check_blob(raw_header, blob_size):
    """Passt der Kopf zur tatsaechlichen Groesse? -> Kopf-dict oder None."""
    h = parse_header(raw_header)
    if h is None or cipher_size(h["size"], h["cs"]) != int(blob_size):
        return None
    return h


def header(plain_size, prefix, cs=CS):
    """Kopf bauen (fuer Tests und Werkzeuge)."""
    if len(prefix) != 16:
        raise ValueError("prefix")
    return _HEAD.pack(MAGIC, VERSION, 0, 0, cs, 0, int(plain_size), bytes(prefix) + b"\0" * 8)
