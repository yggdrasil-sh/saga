"""Foederation (2.0), unterste Schicht: Instanz-Schluessel (Ed25519) und HTTP Message Signatures (RFC 9421).

Kryptografie nur aus der Bibliothek "cryptography" (Ed25519, SHA-256, Fernet) - hier wird nichts selbst gerechnet,
nur die Signaturbasis nach RFC 9421 Abschnitt 2.5 zusammengesetzt. Bewusst eng: genau EIN Label ("saga"), genau
eine feste Liste von Komponenten, nur alg="ed25519", nur sf-strings ohne Sonderzeichen. Alles andere wird abgewiesen.

Anfrage:  ("@method" "@authority" "@path" "@query" "content-digest" "saga-actor")
          ;created;expires;nonce;keyid;alg;tag="saga-fed/1"
Antwort:  ("@status" "content-digest" "saga-nonce");created;keyid;alg;tag="saga-fed-resp/1"
          (saga-nonce = Nonce der Anfrage - bindet die Antwort an genau diese Anfrage)
Content-Digest nach RFC 9530 (sha-256), immer gesetzt (auch fuer leere Koerper)."""
import base64
import hashlib
import json
import re
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

LABEL = "saga"
REQ_COMPONENTS = ("@method", "@authority", "@path", "@query", "content-digest", "saga-actor")
RESP_COMPONENTS = ("@status", "content-digest", "saga-nonce")
REQ_TAG, RESP_TAG = "saga-fed/1", "saga-fed-resp/1"
SKEW = 300                                       # erlaubte Abweichung der Uhren (Sekunden)
NONCE_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
_SF = re.compile(r'[\x20-\x21\x23-\x5b\x5d-\x7e]{1,300}')     # sf-string ohne " und \
_INPUT = re.compile(r'saga=\(((?:"[a-z@-]+"(?: "[a-z@-]+")*)?)\)((?:;[a-z]+=(?:[0-9]{1,12}|"[^"\\]{0,300}"))*)')
_PARAM = re.compile(r';([a-z]+)=([0-9]{1,12}|"[^"\\]{0,300}")')
_SIG = re.compile(r"saga=:([A-Za-z0-9+/]{86}==):")
_DIGEST = re.compile(r"sha-256=:([A-Za-z0-9+/]{43}=):")


class SigError(Exception):
    """Signatur ungueltig - der Text ist nur fuer Protokoll/Tests, nach aussen geht ein neutraler Fehler."""


def b64(b):
    return base64.b64encode(b).decode()


def b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- Schluessel -------------------------------------------------------------------------------------------
def new_key():
    """-> (privat_roh, oeffentlich_roh) - je 32 Byte."""
    k = Ed25519PrivateKey.generate()
    return (k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()),
            k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def public_of(priv_raw):
    return Ed25519PrivateKey.from_private_bytes(priv_raw).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def load_public(s):
    """Oeffentlicher Schluessel aus Base64url (32 Byte) -> Rohbytes oder SigError."""
    try:
        raw = unb64u(s)
    except (ValueError, TypeError):
        raise SigError("key encoding")
    if len(raw) != 32:
        raise SigError("key length")
    Ed25519PublicKey.from_public_bytes(raw)
    return raw


def fingerprint(pub_raw):
    """Gut vorlesbar: SHA256: + Base32 der ersten 20 Byte von SHA-256(Schluessel), in Vierergruppen."""
    s = base64.b32encode(hashlib.sha256(pub_raw).digest()[:20]).decode()
    return "SHA256:" + "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def seal_key(fernet, priv_raw):
    return fernet.encrypt(priv_raw).decode()


def open_key(fernet, token):
    return fernet.decrypt(token.encode())


# --- Hilfen ----------------------------------------------------------------------------------------------
def digest(body):
    return "sha-256=:" + b64(hashlib.sha256(body or b"").digest()) + ":"


def digest_ok(header, body):
    m = _DIGEST.fullmatch((header or "").strip())
    return bool(m) and secrets.compare_digest(m.group(1), b64(hashlib.sha256(body or b"").digest()))


def actor_header(actor):
    """{name, display, groups} -> Wert der Kopfzeile Saga-Actor ('-' ohne Person)."""
    if not actor:
        return "-"
    return b64u(json.dumps(actor, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())


def read_actor(value):
    """Saga-Actor -> dict oder None ('-'). Fehler: SigError (Inhalt pruefen die Aufrufer)."""
    v = (value or "").strip()
    if v == "-":
        return None
    if not v or len(v) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+", v):
        raise SigError("actor encoding")
    try:
        d = json.loads(unb64u(v))
    except (ValueError, TypeError, RecursionError):
        raise SigError("actor json")
    if not isinstance(d, dict):
        raise SigError("actor json")
    return d


def _sf(s):
    s = str(s)
    if not _SF.fullmatch(s):
        raise SigError("bad sf-string")
    return '"' + s + '"'


def _params(components, **p):
    out = "(" + " ".join(f'"{c}"' for c in components) + ")"
    for k, v in p.items():
        out += f";{k}=" + (str(int(v)) if isinstance(v, int) else _sf(v))
    return out


def _base(values, params):
    """values: [(komponente, wert)] in Reihenfolge -> Signaturbasis (RFC 9421 2.5)."""
    lines = []
    for k, v in values:
        v = str(v)
        if "\n" in v or "\r" in v:
            raise SigError("newline in component")
        lines.append(f'"{k}": {v}')
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines).encode()


def authority(netloc):
    """Host[:Port] klein, Standard-Ports weg (wie fuer @authority vorgeschrieben)."""
    h = (netloc or "").strip().lower()
    for sfx in (":443", ":80"):
        if h.endswith(sfx):
            h = h[: -len(sfx)]
    return h


def _req_values(method, auth_, path, query, dig, actor):
    return [("@method", method.upper()), ("@authority", authority(auth_)), ("@path", path or "/"),
            ("@query", "?" + (query or "")), ("content-digest", dig), ("saga-actor", actor)]


# --- Anfragen ----------------------------------------------------------------------------------------------
def sign_request(priv_raw, keyid, method, netloc, path, query, body, actor=None, now=None):
    """-> (kopfzeilen, nonce). body: Bytes (b'' ohne Koerper)."""
    now = int(now or time.time())
    nonce = secrets.token_urlsafe(18)
    dig = digest(body)
    act = actor_header(actor)
    params = _params(REQ_COMPONENTS, created=now, expires=now + SKEW, nonce=nonce, keyid=keyid, alg="ed25519", tag=REQ_TAG)
    base = _base(_req_values(method, netloc, path, query, dig, act), params)
    sig = Ed25519PrivateKey.from_private_bytes(priv_raw).sign(base)
    return {"Content-Digest": dig, "Saga-Actor": act, "Signature-Input": f"{LABEL}={params}",
            "Signature": f"{LABEL}=:{b64(sig)}:"}, nonce


def parse_input(value, components, tag):
    """Signature-Input pruefen -> (params-text, {param: wert}). Streng: ein Label, feste Komponenten, Pflicht-Parameter."""
    v = (value or "").strip()
    m = _INPUT.fullmatch(v)
    if not m:
        raise SigError("signature-input syntax")
    comps = tuple(x.strip('"') for x in m.group(1).split(" ")) if m.group(1) else ()
    if comps != tuple(components):
        raise SigError("covered components")
    p = {}
    for k, raw in _PARAM.findall(m.group(2)):
        if k in p:
            raise SigError("duplicate param")
        p[k] = int(raw) if raw[0] != '"' else raw[1:-1]
    if p.get("alg") != "ed25519" or p.get("tag") != tag or not isinstance(p.get("created"), int) or not isinstance(p.get("keyid"), str):
        raise SigError("params")
    return v[len(LABEL) + 1:], p


def verify_request(pub_lookup, method, own_authority, path, query, headers, body, now=None):
    """Eingehende Anfrage pruefen. pub_lookup(keyid) -> oeffentlicher Rohschluessel (oder SigError).
    -> (keyid, nonce, actor-dict|None). Fehler: SigError. Die Nonce merkt sich der Aufrufer (Replay)."""
    now = int(now or time.time())
    params, p = parse_input(headers.get("signature-input"), REQ_COMPONENTS, REQ_TAG)
    if not isinstance(p.get("expires"), int) or not isinstance(p.get("nonce"), str) or not NONCE_RE.fullmatch(p["nonce"]):
        raise SigError("params")
    if abs(now - p["created"]) > SKEW or p["expires"] < now or p["expires"] - p["created"] > SKEW or p["expires"] <= p["created"]:
        raise SigError("time")
    m = _SIG.fullmatch((headers.get("signature") or "").strip())
    if not m:
        raise SigError("signature syntax")
    dig = headers.get("content-digest") or ""
    if not digest_ok(dig, body):
        raise SigError("digest")
    act = (headers.get("saga-actor") or "").strip()
    pub = pub_lookup(p["keyid"])
    base = _base(_req_values(method, own_authority, path, query, dig.strip(), act), params)
    try:
        Ed25519PublicKey.from_public_bytes(pub).verify(base64.b64decode(m.group(1)), base)
    except (InvalidSignature, ValueError):
        raise SigError("signature")
    return p["keyid"], p["nonce"], read_actor(act)


# --- Antworten ---------------------------------------------------------------------------------------------
def sign_response(priv_raw, keyid, status, body, nonce, now=None):
    now = int(now or time.time())
    dig = digest(body)
    params = _params(RESP_COMPONENTS, created=now, keyid=keyid, alg="ed25519", tag=RESP_TAG)
    base = _base([("@status", int(status)), ("content-digest", dig), ("saga-nonce", nonce)], params)
    sig = Ed25519PrivateKey.from_private_bytes(priv_raw).sign(base)
    return {"Content-Digest": dig, "Saga-Nonce": nonce, "Signature-Input": f"{LABEL}={params}", "Signature": f"{LABEL}=:{b64(sig)}:"}


def verify_response(pub_raw, keyid, status, headers, body, nonce, now=None):
    """Antwort des Gegenuebers pruefen (gepinnter Schluessel, gleiche keyid, unsere Nonce). Fehler: SigError."""
    now = int(now or time.time())
    params, p = parse_input(headers.get("signature-input"), RESP_COMPONENTS, RESP_TAG)
    if p["keyid"] != keyid:
        raise SigError("keyid")
    if abs(now - p["created"]) > SKEW:
        raise SigError("time")
    if (headers.get("saga-nonce") or "") != nonce:
        raise SigError("nonce")
    m = _SIG.fullmatch((headers.get("signature") or "").strip())
    dig = (headers.get("content-digest") or "").strip()
    if not m or not digest_ok(dig, body):
        raise SigError("digest")
    base = _base([("@status", int(status)), ("content-digest", dig), ("saga-nonce", nonce)], params)
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(base64.b64decode(m.group(1)), base)
    except (InvalidSignature, ValueError):
        raise SigError("signature")
