"""Push-Relais fuer Apple-Mitteilungen (2.6, Bugs-DB 2262, Vertrag docs/PUSH-RELAY.md).

Fremde Saga-Server haben keinen APNs-Schluessel des Apple-Kontos, das die Store-App veroeffentlicht. Sie schicken ihre
Apple-Mitteilungen deshalb an ein Relais (Betreiber der App), Ende-zu-Ende verschluesselt fuer die App: das Relais sieht nur
Geraetetoken, Steuerkoepfe (push-type, priority, expiration, undurchsichtige collapse-id) und Chiffrat.

Hier: eigener Ed25519-Schluessel (setting push_relay, privater Teil verschluesselt wie der Foederationsschluessel),
Anmeldung beim Relais (POST /v1/servers, Domain-Nachweis ueber /.well-known/saga-push-relay), Zustand (setting
push_relay_state), Zustellung (send) mit Schutzschalter. Signiert wird mit saga.fedsig und Tag saga-relay/1.

Adresse: Umgebung SAGA_PUSH_RELAY_URL, sonst DEFAULT_URL (leer = aus, bis der Betreiber feststeht); "off" schaltet ab.
http nur fuer Loopback und nur ueber die Umgebung (Tests). Testwiederherstellung (SAGA_RESTORE_TEST): nie.
Admin: Schalter push.relay (Standard an). Dieses Modul importiert saga.push NICHT (push nutzt es)."""
import base64
import hashlib
import ipaddress
import json
import threading
import time
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

import auth
from db import connect as _db
from saga import fedsig
from saga.core import BASE_URL, E, RESTORE_TEST, SECRET, _json_load

DEFAULT_URL = ""                     # leer bis der Name des oeffentlichen Relais feststeht -> Relais aus
KEY_K, STATE_K = "push_relay", "push_relay_state"
STATES = ("ok", "down", "pending", "blocked", "auth")
SID_RE = r"s_[a-z2-7]{16}"
_lock = threading.Lock()
_key = {}
_warned = set()


def _warn(msg):
    if msg not in _warned:
        _warned.add(msg)
        print(f"push_relay: {msg}", flush=True)


def _loopback(host):
    if (host or "").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def url():
    """Adresse des Relais (ohne / am Ende) oder None (aus)."""
    if RESTORE_TEST:
        return None
    env = "SAGA_PUSH_RELAY_URL" in E
    raw = str(E.get("SAGA_PUSH_RELAY_URL") if env else DEFAULT_URL).strip()
    if not raw or raw.lower() == "off":
        return None
    pu = urlparse(raw)
    if pu.query or pu.fragment or pu.username or not pu.hostname:
        _warn("SAGA_PUSH_RELAY_URL ungueltig - Relais aus")
        return None
    if pu.scheme == "https" or (pu.scheme == "http" and env and _loopback(pu.hostname)):
        return raw.rstrip("/")
    _warn("SAGA_PUSH_RELAY_URL: nur https (http nur fuer Loopback) - Relais aus")
    return None


def enabled():
    """Relais eingeschaltet (Adresse da, Mitteilungen an, Admin-Schalter relay an)?"""
    p = auth.settings().get("push") or {}
    return bool(url()) and bool(p.get("enabled", True)) and bool(p.get("relay", True))


# --- Schluessel -------------------------------------------------------------------------------------------------------
def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("saga-push-relay|" + SECRET).encode()).digest()))


def _read(c, k, lock=False):
    r = c.execute("SELECT v FROM setting WHERE k=?" + (" FOR UPDATE" if lock else ""), (k,)).fetchone()
    return _json_load(r[0], {}) if r else {}


def _write(c, k, d):
    c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, json.dumps(d, ensure_ascii=False)))


def has_key():
    with _db() as c:
        return bool(_read(c, KEY_K).get("pub"))


def key(create=True):
    """-> (privat_roh, oeffentlich_roh) oder None. Beim ersten Bedarf erzeugt (mehrere Prozesse: wer zuerst schreibt,
    gewinnt). Laesst sich der gespeicherte Schluessel nicht mehr oeffnen (SECRET_KEY geaendert), wird ein neuer erzeugt
    (alter in push_relay_old) - der Server meldet sich dann neu an und bekommt eine neue Server-Kennung."""
    with _lock:
        if _key.get("k"):
            return _key["k"]
        with _db() as c, c:
            d = _read(c, KEY_K)
            if not d.get("pub"):
                if not create:
                    return None
                priv, pub = fedsig.new_key()
                c.execute("INSERT OR IGNORE INTO setting (k, v) VALUES (?, ?)",
                          (KEY_K, json.dumps({"pub": fedsig.b64u(pub), "priv": fedsig.seal_key(_fernet(), priv), "created": time.time()})))
                d = _read(c, KEY_K)
            try:
                priv = fedsig.open_key(_fernet(), d["priv"])
            except (InvalidToken, KeyError, TypeError, ValueError):
                _warn("Schluessel nicht lesbar (SECRET_KEY geaendert?) - neuer Schluessel, neue Anmeldung")
                _write(c, KEY_K + "_old", d)
                priv, pub = fedsig.new_key()
                d = {"pub": fedsig.b64u(pub), "priv": fedsig.seal_key(_fernet(), priv), "created": time.time()}
                _write(c, KEY_K, d)
        _key["k"] = (priv, fedsig.unb64u(d["pub"]))
        return _key["k"]


def forget_key_cache():
    _key.clear()


def config():
    """Gespeicherte Anmeldung: {pub, server_id, url, registered} (ohne privaten Schluessel)."""
    with _db() as c:
        d = _read(c, KEY_K)
    return {k: d.get(k) for k in ("pub", "server_id", "url", "registered", "created")}


def fingerprint():
    pub = config().get("pub")
    return fedsig.fingerprint(fedsig.unb64u(pub)) if pub else None


def well_known():
    """Antwort auf GET /.well-known/saga-push-relay (Domain-Nachweis) oder None (noch kein Schluessel -> 404)."""
    pub = config().get("pub")
    return {"v": 1, "key": pub} if pub else None


# --- Zustand -----------------------------------------------------------------------------------------------------------
def state():
    """{state, since, last_err, last_ok, topics, limits, stage, checked, alerted} - leer, solange nie benutzt."""
    with _db() as c:
        return _read(c, STATE_K)


def usable_for(topic):
    """Kann ein Abo mit diesem Topic ueber das Relais laufen? (Relais an; Topic bekannt oder die Liste noch unbekannt.)"""
    if not topic or not enabled():
        return False
    topics = state().get("topics")
    return not isinstance(topics, list) or topic in topics


def public_state():
    """Fuer /api/push (server.apns_relay): {state, topics} oder None (Relais aus)."""
    if not enabled():
        return None
    s = state()
    return {"state": s.get("state") if s.get("state") in STATES else "ok", "topics": s.get("topics") if isinstance(s.get("topics"), list) else []}


def send(token, env, topic, push_type, priority, expiration, collapse_id, rate, aps, saga):
    """Eine Mitteilung ueber das Relais -> (ergebnis, detail) wie push.send (ok | gone | fail | hold)."""
    return "fail", "Relais noch nicht angebunden"
