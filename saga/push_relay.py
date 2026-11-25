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
import re
import threading
import time
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

import auth
from db import connect as _db
from saga import fedsig, notify
from saga.core import ADMIN_URL, BASE_URL, E, RESTORE_TEST, SECRET, _json_load, t

DEFAULT_URL = ""                     # leer bis der Name des oeffentlichen Relais feststeht -> Relais aus
KEY_K, STATE_K = "push_relay", "push_relay_state"
STATES = ("ok", "down", "pending", "blocked", "auth")
SID_RE = r"s_[a-z2-7]{16}"
_lock = threading.RLock()            # ensure_registered -> register -> key() (gleicher Faden)
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
    gewinnt). Jeder Aufruf vergleicht mit der Datenbank: hat ein anderer Prozess den Schluessel gewechselt (Admin
    "Schluessel erneuern", Abmelden, Wiederherstellung), gilt sofort der neue. Laesst sich der gespeicherte Schluessel nicht
    mehr oeffnen (SECRET_KEY geaendert), wird ein neuer erzeugt (alter in push_relay_old) - der Server meldet sich dann neu
    an und bekommt eine neue Server-Kennung."""
    with _lock:
        with _db() as c:
            d = _read(c, KEY_K)
        if _key.get("k") and d.get("pub") == _key.get("pub"):
            return _key["k"]
        with _db() as c, c:
            d = _read(c, KEY_K, lock=True)
            if not d.get("pub") or not d.get("priv"):
                if not create:
                    return None
                priv, pub = fedsig.new_key()
                nd = {"pub": fedsig.b64u(pub), "priv": fedsig.seal_key(_fernet(), priv), "created": time.time()}
                if d:                              # Zeile ohne Schluessel (z. B. nur Reste einer Anmeldung): ersetzen
                    _write(c, KEY_K, nd)
                else:
                    c.execute("INSERT OR IGNORE INTO setting (k, v) VALUES (?, ?)", (KEY_K, json.dumps(nd)))
                d = _read(c, KEY_K)
            try:
                priv = fedsig.open_key(_fernet(), d["priv"])
            except (InvalidToken, KeyError, TypeError, ValueError):
                _warn("Schluessel nicht lesbar (SECRET_KEY geaendert?) - neuer Schluessel, neue Anmeldung")
                _write(c, KEY_K + "_old", d)
                priv, pub = fedsig.new_key()
                d = {"pub": fedsig.b64u(pub), "priv": fedsig.seal_key(_fernet(), priv), "created": time.time()}
                _write(c, KEY_K, d)
        _key.update(k=(priv, fedsig.unb64u(d["pub"])), pub=d["pub"])
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



# --- Verbindung -------------------------------------------------------------------------------------------------------
CONNECT_S, READ_S = 3.0, 8.0
CB_MIN, CB_MAX = 15.0, 300.0         # Schutzschalter: nach einem Fehler 15 s "down", verdoppelt bis 300 s
ALERT_AFTER = 600                    # Admin-Glocke nach 10 min durchgehend "down" (pending/blocked sofort)
REREG_GAP = 3600                     # automatische Neuanmeldung nach 401 hoechstens einmal je Stunde
REG_GAP = 60                         # Anmeldung bei Bedarf hoechstens einmal je Minute (bei Fehlern)
REFRESH = 3600                       # GET /v1/info und /v1/servers/me stuendlich
HOLD_MAX = 3600
_cb = {"until": 0.0, "wait": CB_MIN}
_hold = {"until": 0.0}
_tries = {"reg": 0.0, "rereg": 0.0}
_http = {}


def _client():
    import httpx
    c = _http.get("c")
    if c is None:
        c = _http["c"] = httpx.Client(timeout=httpx.Timeout(READ_S, connect=CONNECT_S), follow_redirects=False,
                                      headers={"User-Agent": "Saga-Push-Relay-Client/1"})
    return c


class RelayDown(Exception):
    """Relais nicht erreichbar (Verbindung, Zeitueberschreitung, 502/503/504)."""


def _call(method, path, body=None, signed=True, keyid=None, priv=None):
    """-> (status, json-dict). Fehler der Verbindung: RelayDown."""
    import httpx
    base = url()
    if not base:
        raise RelayDown("aus")
    pu = urlparse(base)
    full = (pu.path.rstrip("/") + path) or "/"
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode() if body is not None else b""
    h = {"Accept": "application/json"}
    if body is not None:
        h["Content-Type"] = "application/json"
    if signed:
        if priv is None:
            priv = key()[0]
        sig, _ = fedsig.sign_request(priv, keyid, method, pu.netloc, full, "", raw, tag=fedsig.RELAY_TAG)
        h.update(sig)
    try:
        r = _client().request(method, f"{pu.scheme}://{pu.netloc}{full}", content=raw if body is not None or signed else None, headers=h)
    except (httpx.HTTPError, OSError) as ex:
        raise RelayDown(type(ex).__name__)
    if r.status_code in (502, 503, 504):
        raise RelayDown(f"HTTP {r.status_code}")
    try:
        d = r.json() if r.content else {}
    except ValueError:
        d = {}
    return r.status_code, d if isinstance(d, dict) else {}


def _down(why):
    now = time.time()
    _cb["until"] = now + _cb["wait"]
    _cb["wait"] = min(CB_MAX, _cb["wait"] * 2)
    _set(state="down", err=why)


def _up():
    _cb["until"], _cb["wait"] = 0.0, CB_MIN


def breaker_open():
    return _cb["until"] > time.time()


def retry_in():
    """Wartezeit fuer einen zurueckgehaltenen Auftrag (hold): bis der Schutzschalter bzw. die Grenze (429) wieder frei
    ist, mindestens 5 s, hoechstens 1 h."""
    left = max(_cb["until"], _hold["until"]) - time.time()
    return int(max(5, min(HOLD_MAX, left if left > 0 else 30)))


def reset():
    """Nur fuer Tests und "Jetzt pruefen": Schutzschalter und Sperren zuruecksetzen."""
    _up()
    _hold["until"] = 0.0
    _tries.update(reg=0.0, rereg=0.0)


# --- Zustand schreiben, Admin-Glocke -----------------------------------------------------------------------------------
def _set(state=None, err=None, **more):
    """Zustand fortschreiben (setting push_relay_state). Wechsel -> since = jetzt. Prueft die Admin-Glocke (einmal je
    Stoerung) und markiert sie bei Erholung als gelesen."""
    now = time.time()
    alert = clear = None
    with _db() as c, c:
        d = _read(c, STATE_K, lock=True)
        old = d.get("state")
        if state and state != old:
            d["state"], d["since"] = state, now
            if state == "ok":
                clear = bool(d.get("alerted"))
                d["alerted"] = False
        if err is not None:
            d["last_err"] = f"{str(err)[:100]} @{int(now)}"
        if state == "ok":
            if now - float(d.get("last_ok") or 0) < 60 and not more and old == "ok" and err is None:
                return d                   # nichts Neues - nicht bei jeder Mitteilung schreiben
            d["last_ok"] = now
        d.update({k: v for k, v in more.items() if v is not None})
        st = d.get("state")
        if st in ("pending", "blocked") or (st in ("down", "auth") and now - float(d.get("since") or now) >= ALERT_AFTER):
            if not d.get("alerted"):
                d["alerted"] = True
                alert = st
        _write(c, STATE_K, d)
        if alert:
            for u in [r[0] for r in c.execute("SELECT username FROM users WHERE is_admin=1 AND disabled=0 ORDER BY username")]:
                notify.create(c, u, "push.relay", None, data={"code": alert})
        if clear:
            c.execute("UPDATE notification SET read_at=? WHERE kind='push.relay' AND read_at IS NULL", (now,))
    return d


def tick():
    """Aus der Push-Schleife (alle paar Sekunden): Admin-Glocke auch dann, wenn gerade nichts gesendet wird; stuendlich
    /v1/info und /v1/servers/me."""
    if not enabled():
        return
    d = state()
    if d.get("state") in ("down", "auth") and not d.get("alerted") and time.time() - float(d.get("since") or time.time()) >= ALERT_AFTER:
        _set()
    if config().get("server_id") and time.time() - float(d.get("checked") or 0) > REFRESH and not breaker_open():
        try:
            refresh()
        except Exception as ex:  # noqa: BLE001 - nie die Push-Schleife aufhalten
            print(f"push_relay: refresh: {type(ex).__name__}", flush=True)


TEXT = {"down": lambda: t("Mitteilungen an iPhone/iPad: Das Push-Relais ist seit 10 Minuten nicht erreichbar. Die Glocke in Saga hat alles."),
        "auth": lambda: t("Mitteilungen an iPhone/iPad: Das Push-Relais nimmt die Anmeldung dieses Servers nicht an."),
        "pending": lambda: t("Mitteilungen an iPhone/iPad: Dieser Server wartet beim Push-Relais auf Freischaltung."),
        "blocked": lambda: t("Mitteilungen an iPhone/iPad: Dieser Server ist beim Push-Relais gesperrt.")}


def _notif(c, uid, item, d):
    """Glocke push.relay (nur Admins): Text beim Lesen in der Sprache des Admins, Link in den Admin-Bereich."""
    item["access"] = True
    item["system"] = True
    item["text"] = TEXT.get(d.get("code"), TEXT["down"])()
    item["title"] = t("Push-Relais")
    item["url"] = (ADMIN_URL + "/settings/push") if ADMIN_URL else None


notify.RENDER["push.relay"] = _notif


# --- Anmeldung -------------------------------------------------------------------------------------------------------
def _save_reg(sid, base, pub):
    """Server-Kennung speichern - nur zum Schluessel, mit dem angemeldet wurde (sonst hat ihn inzwischen jemand gewechselt)."""
    with _db() as c, c:
        k = _read(c, KEY_K, lock=True)
        if k.get("pub") != fedsig.b64u(pub):
            return False
        k.update(server_id=sid, url=base, registered=time.time())
        _write(c, KEY_K, k)
        return True


def _host():
    """Name fuer den Domain-Nachweis: der oeffentliche Name aus BASE_URL. SAGA_PUSH_RELAY_HOST ueberschreibt ihn (Tests,
    Server hinter einem anderen oeffentlichen Namen)."""
    return (E.get("SAGA_PUSH_RELAY_HOST") or "").strip().lower() or urlparse(BASE_URL).netloc or "localhost"


def _st_of(d):
    return {"active": "ok", "pending": "pending", "blocked": "blocked"}.get(str(d.get("state") or ""), "ok")


def register():
    """POST /v1/servers (keyid "reg", signiert mit dem eigenen Schluessel) -> server_id oder None. RelayDown nach aussen."""
    from version import VERSION
    priv, pub = key()
    base = url()
    st, d = _call("POST", "/v1/servers", {"v": 1, "pub": fedsig.b64u(pub), "host": _host(), "saga_version": VERSION},
                  keyid="reg", priv=priv)
    sid = str(d.get("server_id") or "")
    if st in (200, 201) and re.fullmatch(SID_RE, sid):
        if not _save_reg(sid, base, pub):
            return None
        _up()
        _set(state=_st_of(d), topics=d.get("topics") if isinstance(d.get("topics"), list) else None,
             limits=d.get("limits") if isinstance(d.get("limits"), dict) else None, stage=d.get("stage"), server_id=sid,
             checked=time.time())
        print(f"push_relay: angemeldet ({sid}, {d.get('state')})", flush=True)
        return sid
    if st == 403:
        _set(state="blocked" if d.get("error") == "blocked" else "pending", err=f"Anmeldung {st}")
    elif st == 429:
        _hold["until"] = time.time() + _retry_after(d)
        _set(err="Anmeldung 429")
    else:
        _set(state="auth", err=f"Anmeldung HTTP {st} {str(d.get('reason') or d.get('error') or '')[:40]}".strip())
    return None


def _retry_after(d):
    try:
        return max(1, min(HOLD_MAX, int(d.get("retry_after") or 60)))
    except (TypeError, ValueError):
        return 60


def ensure_registered(force=False):
    """Server-Kennung beim aktuellen Relais -> server_id oder None. Meldet sich bei Bedarf an (hoechstens einmal je
    Minute, ausser force). Andere Adresse als bei der letzten Anmeldung -> neu anmelden."""
    cfg = config()
    base = url()
    if not base:
        return None
    if cfg.get("server_id") and cfg.get("url") == base and not force:
        return cfg["server_id"]
    now = time.time()
    if not force and now - _tries["reg"] < REG_GAP:
        return None
    _tries["reg"] = now
    with _lock:
        cfg = config()
        if cfg.get("server_id") and cfg.get("url") == base and not force:
            return cfg["server_id"]
        try:
            return register()
        except RelayDown as ex:
            _down(f"Anmeldung: {ex}")
            return None


def ensure_registered_async():
    """Beim ersten iOS-Abo mit Schluesseln (im Hintergrund, die Anfrage wartet nicht)."""
    if enabled() and not config().get("server_id"):
        threading.Thread(target=ensure_registered, name="push-relay-reg", daemon=True).start()


def refresh():
    """GET /v1/info (Topics, Grenzen) und GET /v1/servers/me (Zustand, Stufe) -> Zustand. 401 -> Neuanmeldung (<= 1/h)."""
    sid = ensure_registered()
    try:
        st, info = _call("GET", "/v1/info", signed=False)
        more = {"checked": time.time()}
        if st == 200:
            more.update(topics=info.get("topics") if isinstance(info.get("topics"), list) else None,
                        max_payload=info.get("max_payload"), versions=info.get("v"))
        if not sid:
            _set(**more)
            return state()
        st, me = _call("GET", "/v1/servers/me", keyid=sid)
    except RelayDown as ex:
        _down(str(ex))
        return state()
    if st == 200:
        _up()
        _set(state=_st_of(me), stage=me.get("stage"), limits=me.get("limits") if isinstance(me.get("limits"), dict) else None,
             **more)
    elif st == 401:
        _set(state="auth", err="401", **more)
        _rereg()
    elif st == 403:
        _set(state="blocked" if me.get("error") == "blocked" else "pending", err="403", **more)
    else:
        _set(err=f"HTTP {st}", **more)
    return state()


def _rereg():
    if time.time() - _tries["rereg"] < REREG_GAP:
        return None
    _tries["rereg"] = time.time()
    print("push_relay: 401 - neue Anmeldung", flush=True)
    return ensure_registered(force=True)


def rotate():
    """Admin "Schluessel erneuern": neuer Schluessel, Nachweis, POST /v1/servers/me/rotate (alter Schluessel signiert).
    -> (True, "") oder (False, Grund). Ohne Anmeldung: nur neuer Schluessel."""
    old_priv, _old_pub = key()
    new_priv, new_pub = fedsig.new_key()
    sid = config().get("server_id")
    if sid and url():
        npub = fedsig.b64u(new_pub)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        proof = fedsig.b64u(Ed25519PrivateKey.from_private_bytes(new_priv).sign(f"saga-relay-rotate/1|{sid}|{npub}".encode()))
        try:
            st, d = _call("POST", "/v1/servers/me/rotate", {"new_pub": npub, "proof": proof}, keyid=sid, priv=old_priv)
        except RelayDown as ex:
            return False, str(ex)
        if st != 200:
            return False, f"HTTP {st} {d.get('error') or ''}".strip()
    with _db() as c, c:
        k = _read(c, KEY_K, lock=True)
        _write(c, KEY_K + "_old", k)
        k.update(pub=fedsig.b64u(new_pub), priv=fedsig.seal_key(_fernet(), new_priv), created=time.time())
        _write(c, KEY_K, k)
    forget_key_cache()
    return True, ""


def unregister():
    """Admin "Beim Relais abmelden": DELETE /v1/servers/me, Server-Kennung vergessen (Schluessel bleibt)."""
    sid = config().get("server_id")
    res = (True, "")
    if sid and url():
        try:
            st, d = _call("DELETE", "/v1/servers/me", keyid=sid)
            if st == 403:                          # gesperrter Server darf sich nicht abmelden (Relais: 403 blocked)
                _set(state="blocked" if d.get("error") == "blocked" else "pending", err="403")
                return False, str(d.get("error") or "403")
            if st not in (200, 401, 404):
                res = (False, f"HTTP {st}")
        except RelayDown as ex:
            res = (False, str(ex))
    with _db() as c, c:
        k = _read(c, KEY_K, lock=True)
        for x in ("server_id", "url", "registered"):
            k.pop(x, None)
        _write(c, KEY_K, k)
        d = _read(c, STATE_K, lock=True)
        for x in ("server_id", "stage", "limits", "state", "since", "alerted"):
            d.pop(x, None)
        _write(c, STATE_K, d)
    return res


# --- Zustellen -------------------------------------------------------------------------------------------------------
def send(token, env, topic, push_type, priority, expiration, collapse_id, rate, aps, saga):
    """Eine Mitteilung ueber das Relais -> (ergebnis, detail) wie push.send: ok | gone (Abo loeschen) | fail (Nutzlast
    falsch, zaehlt) | hold (Abo schuldlos, spaeter erneut - retry_in()). Nie Inhalte im Protokoll."""
    if not enabled():
        return "fail", "Relais aus"
    now = time.time()
    if breaker_open():
        tick()
        return "hold", "Relais gestoert"
    if _hold["until"] > now:
        return "hold", "Relais: Grenze"
    sid = ensure_registered()
    if not sid:
        s = state().get("state")
        return "hold", f"Relais: {s or 'nicht angemeldet'}"
    body = {"token": token, "env": env, "topic": topic, "push_type": push_type, "priority": int(priority),
            "expiration": int(expiration), "rate": rate, "aps": aps, "saga": saga}
    if collapse_id:
        body["collapse_id"] = collapse_id
    for attempt in (0, 1):
        try:
            st, d = _call("POST", "/v1/push", body, keyid=sid)
        except RelayDown as ex:
            _down(str(ex))
            return "hold", f"Relais gestoert ({ex})"
        if st == 401 and not attempt:
            _set(state="auth", err="401")
            sid = _rereg()
            if sid:
                continue
            return "hold", "Relais: Anmeldung abgelehnt"
        break
    reason = str(d.get("reason") or d.get("error") or "")[:40]
    if st == 200:
        _up()
        _set(state="ok")
        return "ok", ""
    if st == 410:
        return "gone", f"Relais 410 {reason}".strip()
    if st in (400, 413):
        return "fail", f"Relais {st} {reason}".strip()
    if st == 429:
        ra = _retry_after(d)
        if d.get("scope") in ("server", "apns", "register"):
            _hold["until"] = time.time() + ra          # betrifft alle Auftraege - sonst nur diesen (retry_in)
        _last_retry["s"] = ra
        return "hold", f"Relais 429 {d.get('scope') or ''}".strip()
    if st == 401:
        return "hold", "Relais: Anmeldung abgelehnt"
    if st == 403:
        _set(state="blocked" if d.get("error") == "blocked" else "pending", err="403")
        _hold["until"] = time.time() + 300
        return "hold", f"Relais 403 {d.get('error') or ''}".strip()
    _down(f"HTTP {st}")
    return "hold", f"Relais HTTP {st}"


_last_retry = {"s": 0}


def take_retry():
    """Wartezeit des letzten 429 (nur fuer diesen Auftrag) - einmal abholen, sonst retry_in()."""
    s, _last_retry["s"] = _last_retry["s"], 0
    return int(min(HOLD_MAX, s)) if s else retry_in()
