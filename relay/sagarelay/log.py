"""Protokoll: eine JSON-Zeile je Anfrage auf stdout (Rotation macht Docker/journald).

Nie: Geraetetoken, Nutzlast (aps/saga), Chiffrat, IP-Adressen im Klartext. Bei Fehlern tok_h = 8 Hex aus
HMAC(Tagessalz, Token) - so lassen sich Fehler eines Geraets an einem Tag zusammenzaehlen, aber nicht zum Token
zurueckrechnen (das Tagessalz liegt nur im Speicher und wechselt um Mitternacht UTC)."""
import hashlib
import hmac
import json
import secrets
import sys
import threading
import time

OUT = sys.stdout
_lock = threading.Lock()
_salt = {"day": None, "key": b""}
ALLOWED = ("ts", "ev", "server_id", "push_type", "rate", "prio", "status", "apns_reason", "ms", "tok_h", "ip_h", "state",
           "stage", "err", "scope")


def _day_key():
    d = time.strftime("%Y-%m-%d", time.gmtime())
    with _lock:
        if _salt["day"] != d:
            _salt["day"], _salt["key"] = d, secrets.token_bytes(32)
        return _salt["key"]


def tok_h(token):
    return hmac.new(_day_key(), ("tok|" + str(token)).encode(), hashlib.sha256).hexdigest()[:8]


def ip_h(ip):
    return hmac.new(_day_key(), ("ip|" + str(ip)).encode(), hashlib.sha256).hexdigest()[:8]


def emit(**f):
    """Nur Felder aus ALLOWED, Werte kurz - schuetzt davor, versehentlich etwas Fremdes zu protokollieren."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for k in ALLOWED:
        v = f.get(k)
        if v is None or k == "ts":
            continue
        rec[k] = v if isinstance(v, (int, float, bool)) else str(v)[:80]
    line = json.dumps(rec, separators=(",", ":"), ensure_ascii=True)
    with _lock:
        try:
            OUT.write(line + "\n")
            OUT.flush()
        except (OSError, ValueError):
            pass
