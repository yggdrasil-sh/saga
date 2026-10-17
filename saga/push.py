"""Mitteilungen aufs Handy/Desktop (1.7): Web Push (Browser, auch iOS-PWA), ntfy / UnifiedPush, APNs (vorbereitet).

Nur AUSGEHEND vom Server - kein eigener Port, kein eingehender Dienst.
- Abos (push_sub) je Geraet/Browser: webpush (Endpunkt + Schluessel des Browsers), ntfy (eigene Topic-URL, optional
  Zugangstoken - verschluesselt gespeichert), unifiedpush (Endpunkt aus der Verteiler-App, optional mit Schluesseln wie
  Web Push), apns (Geraetetoken der iOS-/macOS-App). App-Abos haengen an der Geraete-Anmeldung (device_login) und
  verschwinden mit ihr; Browser-Abos enden beim Abmelden in diesem Browser und mit "ueberall abmelden"/Passwortwechsel.
- Ausloeser: saga.notify legt eine Benachrichtigung an und meldet "notification.created" (bus.fire, gleiche
  Transaktion) -> hier je Abo eine Zeile in push_queue. Ein Hintergrund-Thread stellt zu (Wiederholungen mit
  Wartezeit, abgelaufene Abos - 404/410 - werden entfernt, alte Auftraege nach 24 h verworfen).
- Chat (feature/chat) ruft spaeter OHNE Inhalt auf:  bus.fire("push.send", c, uid=<empfaenger>, kind="chat.message",
  actor=<absender>, data={"chat": <chat-kennung>})  oder direkt push.enqueue(...). Text: "Neue Nachricht von {name}".
  Stummschalten je Chat entscheidet der Chat selbst (dann gar nicht aufrufen).
- Inhalt minimal: Satz wie in der Glocke ("Jonas hat eine Seite mit dir geteilt"), nie Seitentitel, Kommentar- oder
  Chat-Inhalte. Option "ohne Namen" je Person; ntfy und unverschluesseltes UnifiedPush bekommen NIE Namen (der
  Vermittlungsserver sieht den Text im Klartext). Zustellung prueft den Zugriff erneut (nach Entzug: keine Mitteilung).
- Einstellungen je Person (user_kv 'push'): Bereiche (Teilen, Kommentare, Chat, Veroeffentlichung), Namen, Ruhezeiten.
- Verschluesselung Web Push: RFC 8291 (aes128gcm) und VAPID RFC 8292 mit der Bibliothek cryptography (ECDH P-256,
  HKDF-SHA256, AES-128-GCM, ECDSA) - keine eigene Kryptografie. VAPID-Schluessel wird beim ersten Bedarf erzeugt und
  (privater Teil verschluesselt) in setting 'push_vapid' gespeichert.
- Ziele: Web Push / ntfy / UnifiedPush ueber saga.netguard (SSRF-Schutz: interne Ziele nur mit Admin-Freigabe,
  http nur fuer freigegebene Hosts). APNs: feste Apple-Adressen, HTTP/2 (httpx + h2), Token-Anmeldung (ES256 mit .p8)."""
import base64
import hashlib
import json
import os
import re
import struct
import threading
import time
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, available_timezones

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

import auth
from db import connect as _db
from saga import bus, i18n, notify
from saga.core import BASE_URL, HERE, TZ, _api_guard, _bad, _body, _json_load, _tok_hash, account_lang, t, title
from saga.netguard import Blocked, guarded_client, is_public

router = APIRouter()

E = os.environ
TYPES = ("webpush", "ntfy", "unifiedpush", "apns", "apns-voip")
CATS = {"share": ("share.added", "share.role"), "comment": ("comment.new", "comment.reply", "comment.mention"),
        "chat": ("chat.message",), "publish": ("publish.withdrawn",), "call": ("call", "call.missed")}
CALL_TTL = 60                        # klingelnder Anruf: Mitteilung nach einer Minute verwerfen (statt 4 h vorhalten)


def _call_ttl():
    """Lebensdauer einer Anruf-Mitteilung: so lange, wie der Anruf klingeln darf (Admin: bis 120 s), mindestens CALL_TTL."""
    try:                                  # wie saga.calls.ring_s (push bleibt unabhaengig von calls)
        ring = max(3, min(120, int((auth.settings().get("calls") or {}).get("ring_s") or 45)))
    except (TypeError, ValueError):
        ring = 45
    return max(CALL_TTL, ring + 10)


KIND_CAT = {k: cat for cat, ks in CATS.items() for k in ks}
SUB_MAX = 20                         # Abos je Konto
QUEUE_MAX = 300                      # offene Auftraege je Konto (darueber: keine neuen, die Glocke hat alles)
RATE_MIN = 30                        # Mitteilungen je Konto und Minute (Rest nur in der Glocke)
REG_MIN = 15                         # Anmeldungen von Geraeten je Konto und Minute
TEST_GAP = 5.0                       # Testmitteilung je Abo hoechstens alle 5 s
BACKOFF = (30, 120, 600, 1800, 3600)     # Wartezeit vor dem 2., 3., ... Versuch
MAX_AGE = 86400                      # Auftraege aelter als 24 h verwerfen
FAIL_MAX = 5                         # so viele harte Fehler nacheinander -> Abo entfernen
LEASE = 120                          # ein Prozess "haelt" einen Auftrag waehrend der Zustellung (mehrere Prozesse: SKIP LOCKED)
TTL = 4 * 3600                       # so lange halten Push-Dienste eine Mitteilung fuer ein ausgeschaltetes Geraet vor
PAYLOAD_MAX = 3000
ENDPOINT_MAX = 1024
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
APNS_RE = re.compile(r"[0-9a-fA-F]{64,200}")
APNS_HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
DEF_PREFS = {"cats": {k: True for k in CATS}, "names": True,
             "quiet": {"on": False, "from": "22:00", "to": "07:00", "tz": ""}}
CONTACT = E.get("PUSH_CONTACT") or (BASE_URL if BASE_URL.startswith("https://") else "mailto:saga@localhost")


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_d(s):
    s = str(s or "").strip().replace("+", "-").replace("/", "_").rstrip("=")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# =====================================================================================================
# Web Push: Verschluesselung (RFC 8291, aes128gcm nach RFC 8188) und VAPID (RFC 8292)
# =====================================================================================================
def encrypt(payload, p256dh, auth_secret, salt=None, as_key=None):
    """Inhalt fuer EIN Browser-Abo verschluesseln -> Koerper der Anfrage (Kopf + ein Datensatz).
    salt/as_key nur fuer Testvektoren (sonst jedes Mal neu und zufaellig)."""
    ua_pub = b64u_d(p256dh)
    secret = b64u_d(auth_secret)
    if len(ua_pub) != 65 or ua_pub[0] != 4 or len(secret) != 16:
        raise ValueError("ungueltige Schluessel")
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)
    as_key = as_key or ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, salt=secret, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    rs = 4096
    if len(payload) > rs - 17:
        raise ValueError("Inhalt zu gross")
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)          # \x02 = letzter Datensatz, ohne Auffuellen
    return salt + struct.pack("!I", rs) + bytes([len(as_pub)]) + as_pub + ct


def _jwt_es256(key, header, claims):
    seg = b64u(json.dumps(header, separators=(",", ":")).encode()) + "." + b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(seg.encode(), ec.ECDSA(hashes.SHA256())))
    return seg + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


_vapid = {}
_vapid_lock = threading.Lock()


def vapid(_again=True):
    """-> (privater Schluessel, oeffentlicher Schluessel b64url). Beim ersten Bedarf erzeugt (mehrere Prozesse: wer zuerst
    schreibt, gewinnt). Laesst sich der gespeicherte Schluessel nicht mehr entschluesseln (SECRET_KEY geaendert), wird
    ein neuer erzeugt - Browser-Abos melden sich dann neu an (alte werden nach Fehlern entfernt)."""
    with _vapid_lock:
        if _vapid:
            return _vapid["key"], _vapid["pub"]
        with _db() as c, c:
            r = c.execute("SELECT v FROM setting WHERE k='push_vapid'").fetchone()
            if not r:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
                pub = b64u(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
                c.execute("INSERT OR IGNORE INTO setting (k, v) VALUES ('push_vapid', ?)",
                          (json.dumps({"pub": pub, "priv": auth._enc(pem), "created": time.time()}),))
                r = c.execute("SELECT v FROM setting WHERE k='push_vapid'").fetchone()
        d = _json_load(r[0], {})
        pem = auth._dec(d.get("priv"))
        try:
            key = serialization.load_pem_private_key(pem.encode(), None) if pem else None
        except ValueError:
            key = None
        if key is None:
            if not _again:
                raise RuntimeError("VAPID-Schluessel nicht lesbar")
            print("push: VAPID-Schluessel nicht lesbar (SECRET_KEY geaendert?) - neuer Schluessel", flush=True)
            with _db() as c, c:                 # alten Schluessel aufheben (SECRET_KEY zurueck -> von Hand wiederherstellbar)
                c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('push_vapid_old', ?)", (r[0],))
                c.execute("DELETE FROM setting WHERE k='push_vapid'")
        else:
            _vapid.update(key=key, pub=d.get("pub"))
            return key, d.get("pub")
    return vapid(False)


_vjwt = {}


def vapid_header(endpoint):
    """Authorization-Kopf fuer einen Push-Dienst (aud = Ursprung des Endpunkts), 12 h gueltig, 1 h zwischengespeichert."""
    key, pub = vapid()
    pu = urlparse(endpoint)
    aud = f"{pu.scheme}://{pu.netloc}"
    hit = _vjwt.get(aud)
    if not hit or hit[0] < time.time():
        tok = _jwt_es256(key, {"typ": "JWT", "alg": "ES256"}, {"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": CONTACT})
        if len(_vjwt) > 200:
            _vjwt.clear()
        hit = _vjwt[aud] = (time.time() + 3600, tok)
    return f"vapid t={hit[1]}, k={pub}"


# =====================================================================================================
# APNs (iOS/macOS-App) - nur vorbereitet: Admin traegt Team-ID, Key-ID, Bundle-ID und den .p8-Schluessel ein
# =====================================================================================================
def apns_cfg():
    """Einstellungen (p8 entschluesselt) und ob alles da ist."""
    a = dict(auth.settings().get("apns") or {})
    a["ready"] = bool(a.get("enabled") and a.get("team_id") and a.get("key_id") and a.get("bundle_id") and a.get("p8"))
    return a


def apns_key_ok(pem):
    """.p8 pruefen: privater EC-Schluessel auf P-256 (wie von Apple ausgegeben)."""
    try:
        k = serialization.load_pem_private_key(str(pem).strip().encode(), None)
    except (ValueError, TypeError):
        return False
    return isinstance(k, ec.EllipticCurvePrivateKey) and isinstance(k.curve, ec.SECP256R1)


_apns_jwt = {}


def _apns_token(cfg, renew=False):
    """Anbieter-Token (ES256, iss = Team-ID, kid = Key-ID). Apple: hoechstens alle 20 Min neu, hoechstens 60 Min alt."""
    sig = hashlib.sha256((cfg["team_id"] + "|" + cfg["key_id"] + "|" + cfg["p8"]).encode()).hexdigest()
    hit = _apns_jwt.get("v")
    if renew or not hit or hit[0] != sig or time.time() - hit[1] > 40 * 60:
        key = serialization.load_pem_private_key(cfg["p8"].strip().encode(), None)
        now = int(time.time())
        hit = (sig, now, _jwt_es256(key, {"alg": "ES256", "kid": cfg["key_id"]}, {"iss": cfg["team_id"], "iat": now}))
        _apns_jwt["v"] = hit
    return hit[2]


# =====================================================================================================
# Einstellungen: Server (Admin) und je Person
# =====================================================================================================
def server():
    """Was der Server anbietet (Admin-Bereich -> Mitteilungen)."""
    p = auth.settings().get("push") or {}
    on = bool(p.get("enabled", True))
    return {"enabled": on, "webpush": on and bool(p.get("webpush", True)), "ntfy": on and bool(p.get("ntfy", True)),
            "unifiedpush": on and bool(p.get("unifiedpush", True)), "apns": on and apns_cfg()["ready"],
            "apns-voip": on and apns_cfg()["ready"]}


def prefs(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='push'", (uid,)).fetchone()
    d = _json_load(r[0] if r else None, {})
    out = json.loads(json.dumps(DEF_PREFS))
    out["cats"].update({k: bool(v) for k, v in (d.get("cats") or {}).items() if k in CATS})
    if isinstance(d.get("names"), bool):
        out["names"] = d["names"]
    q = d.get("quiet") if isinstance(d.get("quiet"), dict) else {}
    out["quiet"].update({k: q[k] for k in ("on", "from", "to", "tz") if k in q})
    return out


@lru_cache(maxsize=1)
def _zones():
    return frozenset(available_timezones())


def quiet_now(q, now=None):
    """Liegt der Zeitpunkt in den Ruhezeiten (Ortszeit der Person; ueber Mitternacht moeglich)?"""
    if not q.get("on"):
        return False
    try:
        tz = ZoneInfo(q["tz"]) if q.get("tz") in _zones() else TZ
    except Exception:  # noqa: BLE001
        tz = TZ
    d = datetime.fromtimestamp(now or time.time(), tz)
    m = d.hour * 60 + d.minute
    f = int(q["from"][:2]) * 60 + int(q["from"][3:])
    to = int(q["to"][:2]) * 60 + int(q["to"][3:])
    if f == to:
        return False
    return f <= m < to if f < to else (m >= f or m < to)


# =====================================================================================================
# Ziele pruefen (SSRF): oeffentliche Adressen immer; interne und http nur fuer vom Admin freigegebene Hosts
# =====================================================================================================
def _admin_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _host_free(host, port=None):
    hosts = _admin_hosts()
    return bool(auth.settings().get("allow_private_hosts")) or host in hosts or (port and f"{host}:{port}" in hosts)


def _ip_ok(ip, host, port):
    return is_public(ip) or _host_free(str(host).lower(), port)


def check_url(url):
    """-> (normalisierte Adresse, None) oder (None, Fehlertext). https; http nur fuer freigegebene interne Hosts."""
    u = str(url or "").strip()
    if not u or len(u) > ENDPOINT_MAX:
        return None, t("Adresse fehlt oder ist zu lang")
    try:
        pu = urlparse(u)
        port = pu.port
    except ValueError:
        return None, t("Adresse ungültig")
    if pu.scheme not in ("https", "http") or not pu.hostname or pu.username or pu.password or pu.fragment:
        return None, t("Adresse ungültig")
    if pu.scheme == "http" and not _host_free(pu.hostname.lower(), port):
        return None, t("Nur https-Adressen erlaubt (http nur für vom Admin freigegebene interne Server)")
    return u, None


_local = threading.local()


def _client():
    """httpx-Client mit SSRF-Schutz (je Thread; Verbindungen bleiben offen)."""
    cl = getattr(_local, "cl", None)
    if cl is None:
        cl = _local.cl = guarded_client(_ip_ok, timeout=10, follow_redirects=False)
    return cl


def _apns_client():
    cl = getattr(_local, "apns", None)
    if cl is None:
        import httpx
        cl = _local.apns = httpx.Client(http2=True, timeout=10, follow_redirects=False)
    return cl


# =====================================================================================================
# Auftraege anlegen (in der Transaktion des Ausloesers)
# =====================================================================================================
_wake = threading.Event()
_rl_lock = threading.Lock()
_rate, _reg, _tests = {}, {}, {}


def _rate_ok(store, key, n, per=60.0):
    now = time.time()
    with _rl_lock:
        lst = [x for x in store.get(key, ()) if now - x < per]
        if len(lst) >= n:
            store[key] = lst
            return False
        lst.append(now)
        store[key] = lst
        if len(store) > 5000:                       # alte Schluessel wegraeumen
            for k in [k for k, v in store.items() if not v or now - v[-1] > per][:2500]:
                store.pop(k, None)
        return True


def wake():
    _wake.set()


def _subs(c, uid, epoch, srv):
    """Aktive Abos eines Kontos -> [id]. Browser-Abos von vor "ueberall abmelden"/Passwortwechsel und App-Abos ohne
    Geraete-Anmeldung werden dabei entfernt."""
    c.execute("DELETE FROM push_sub WHERE uid=? AND kind='webpush' AND device_id IS NULL AND created < ?", (uid, epoch or 0))
    c.execute("DELETE FROM push_sub WHERE uid=? AND device_id IS NOT NULL AND NOT EXISTS "
              "(SELECT 1 FROM device_login d WHERE d.id=push_sub.device_id AND d.uid=push_sub.uid)", (uid,))
    return [r[0] for r in c.execute("SELECT id, kind FROM push_sub WHERE uid=? ORDER BY id", (uid,)).fetchall() if srv.get(r[1])]


def _route(c, sids, kind):
    """VoIP-Abos (iOS/macOS-App, PushKit) bekommen NUR klingelnde Anrufe - Apple verlangt zu jeder VoIP-Mitteilung einen
    Anruf in CallKit. Hat dasselbe Geraet ein VoIP-Abo, geht der Anruf nicht zusaetzlich ueber das normale APNs-Abo."""
    if not sids:
        return sids
    rows = c.execute(f"SELECT id, kind, device_id FROM push_sub WHERE id IN ({','.join('?' * len(sids))})", tuple(sids)).fetchall()
    if kind != "call":
        return [r[0] for r in rows if r[1] != "apns-voip"]
    voip_dev = {r[2] for r in rows if r[1] == "apns-voip" and r[2] is not None}
    return [r[0] for r in rows if not (r[1] == "apns" and r[2] in voip_dev)]


def enqueue(c, uid, kind, actor=None, ref=None, data=None, replaces=None):
    """Mitteilung fuer uid vormerken (eine Zeile je Abo) -> Anzahl. Nichts bei: an sich selbst, Bereich abgeschaltet,
    Ruhezeit, kein Abo, Grenze erreicht. replaces: gebuendelte Benachrichtigung ersetzt eine noch nicht zugestellte -
    dann wird der vorhandene Auftrag umgehaengt (keine zweite Mitteilung).
    Fuer den Chat: kind='chat.message', data={'chat': <kennung>} - NIE Inhalte."""
    if kind not in KIND_CAT or not uid or uid == actor:
        return 0
    srv = server()
    if not srv["enabled"]:
        return 0
    if replaces:
        n = c.execute("UPDATE push_queue SET ref=?, actor=? WHERE uid=? AND kind=? AND ref=? AND tries=0",
                      (ref, actor, uid, kind, replaces)).rowcount
        if n:
            c.after_commit(wake)
            return n
    p = prefs(c, uid)
    if not p["cats"].get(KIND_CAT[kind], True) or quiet_now(p["quiet"]):
        return 0
    info = auth.user_info(uid)
    if info is None:
        return 0
    subs = _route(c, _subs(c, uid, info.get("epoch"), srv), kind)
    if not subs or c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=?", (uid,)).fetchone()[0] >= QUEUE_MAX:
        return 0
    if not _rate_ok(_rate, uid, RATE_MIN):
        return 0
    now = time.time()
    d = json.dumps({k: v for k, v in (data or {}).items() if k in ("chat", "call")})        # nur Kennungen, nie Inhalte
    if kind == "chat.message" and c.execute("SELECT 1 FROM push_queue WHERE uid=? AND kind=? AND data=? AND tries=0 LIMIT 1",
                                            (uid, kind, d)).fetchone():
        return 0                          # fuer diesen Chat wartet schon eine Mitteilung (wie das Buendeln der Glocke)
    for sid in subs:
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, ref, actor, data, created, next_at) VALUES (?,?,?,?,?,?,?,?)",
                  (uid, sid, kind, ref, actor, d, now, now))
    c.after_commit(wake)
    return len(subs)


def _on_notification(c, uid, id, kind, actor=None, replaces=None, **_):      # noqa: A002 - Name aus saga.notify
    if kind == "chat.message":
        return                            # der Chat meldet selbst ueber "push.send" (mit Chat-Kennung, Stummschalten beachtet)
    enqueue(c, uid, kind, actor=actor, ref=id, replaces=replaces)


def _on_send(c, uid, kind, actor=None, data=None, **_):
    enqueue(c, uid, kind, actor=actor, data=data)


bus.on("notification.created", _on_notification)
bus.on("push.send", _on_send)


# =====================================================================================================
# Text der Mitteilung (beim Zustellen, in der Sprache des Kontos, Zugriff erneut geprueft)
# =====================================================================================================
def _anon(kind):
    if kind == "share.added":
        return t("Jemand hat eine Seite mit dir geteilt")
    if kind == "share.role":
        return t("Deine Rolle auf einer geteilten Seite hat sich geändert")
    if kind == "comment.new":
        return t("Neuer Kommentar auf deiner Seite")
    if kind == "comment.reply":
        return t("Neue Antwort in einer Unterhaltung")
    if kind == "comment.mention":
        return t("Du wurdest erwähnt")
    if kind == "publish.withdrawn":
        return t("Ein Admin hat deine Veröffentlichung zurückgezogen")
    if kind == "call.missed":
        return t("Verpasster Anruf")
    return t("Neue Benachrichtigung")


def with_names(p, sub_kind, has_keys):
    """Namen nur, wenn die Person es will UND der Weg Ende-zu-Ende verschluesselt ist (Web Push, UnifiedPush mit
    Schluesseln) bzw. Apple (APNs). ntfy und UnifiedPush ohne Schluessel: nie."""
    return bool(p.get("names")) and (sub_kind in ("webpush", "apns", "apns-voip") or (sub_kind == "unifiedpush" and has_keys))


def message(c, uid, kind, ref=None, actor=None, data=None, sub_kind="webpush", has_keys=True, created=None):
    """-> {title, body, url, tag, kind, ts} oder None (Benachrichtigung weg / kein Zugriff mehr)."""
    names = with_names(prefs(c, uid), sub_kind, has_keys)
    tok = i18n.set_lang(account_lang(uid))
    try:
        if kind == "test":
            body, url, tag = t("Testmitteilung – Mitteilungen kommen an."), "/", "test"
        elif kind == "chat.message":
            chat = re.sub(r"[^A-Za-z0-9_.-]", "", str((data or {}).get("chat") or ""))[:64]
            if chat.isdigit() and not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL AND muted=0",
                                                (int(chat), uid)).fetchone():
                return None               # inzwischen nicht mehr Mitglied oder stummgeschaltet
            nm = notify.names(c, [actor]).get(actor) if actor and names else None
            body = t("Neue Nachricht von {name}", name=nm) if nm else t("Neue Nachricht")
            ref = ("c" + chat) if chat.isdigit() else chat          # wie die Glocke: /chats#c<id>
            url, tag = ("/chats#" + ref) if chat else "/chats", "chat-" + chat
        elif kind == "call":
            # Klingelnder Anruf (saga.calls): nur solange er klingelt; Kennungen fuer die Apps (CallKit/ConnectionService)
            cid = re.sub(r"[^0-9a-f]", "", str((data or {}).get("call") or ""))[:32]
            r = c.execute("SELECT state, media, chat_id, created FROM chat_call WHERE id=? AND callee=?", (cid, uid)).fetchone()
            if not r or r[0] != "ringing":
                return None
            nm = notify.names(c, [actor]).get(actor) if actor and names else None
            video = r[1] == "video"
            body = (t("Videoanruf von {name}", name=nm) if video else t("Anruf von {name}", name=nm)) if nm else \
                (t("Eingehender Videoanruf") if video else t("Eingehender Anruf"))
            return {"title": title(), "body": body, "url": f"/chats#call-{cid}", "tag": "call-" + cid, "kind": kind,
                    "ts": int(created or time.time()), "call": cid, "chat": r[2], "media": r[1], "caller": nm,
                    "ttl": max(5, int(r[3] + _call_ttl() - time.time()))}
        else:
            it = notify.item(c, uid, ref) if ref else None
            if not it or (kind != "publish.withdrawn" and not it["access"]):
                return None
            body = it["text"] if names else _anon(kind)
            url = it["url"] or "/"
            tag = (f"thread-{it['thread_id']}" if it.get("thread_id") else f"note-{it['note']['id']}" if it.get("note") else f"n-{ref}")
        return {"title": title(), "body": body, "url": url, "tag": tag, "kind": kind, "ts": int(created or time.time())}
    finally:
        i18n._cur.reset(tok)


# =====================================================================================================
# Zustellen
# =====================================================================================================
SUB_COLS = "id, uid, kind, endpoint, p256dh, auth, secret"


def _verdict(code):
    if code in (200, 201, 202, 204):
        return "ok", ""
    if code in (404, 410):
        return "gone", f"HTTP {code}"
    if code == 429 or code >= 500:
        return "retry", f"HTTP {code}"
    return "fail", f"HTTP {code}"


def _ntfy_split(url):
    """https://ntfy.example.org[/pfad]/<topic> -> (Server, Topic)"""
    pu = urlparse(url)
    base, _, topic = pu.path.rstrip("/").rpartition("/")
    return f"{pu.scheme}://{pu.netloc}{base}", topic


def send(sub, msg):
    """Eine Mitteilung an EIN Abo -> (ergebnis, detail); ergebnis: ok | gone (Abo entfernen) | retry | fail."""
    import httpx
    sid, uid, kind, endpoint, p256dh, auth_s, secret = sub
    if kind in ("apns", "apns-voip"):
        return _send_apns(endpoint, msg, voip=kind == "apns-voip")
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > PAYLOAD_MAX:
        return "fail", "zu gross"
    try:
        if kind == "webpush" or (kind == "unifiedpush" and p256dh and auth_s):
            h = {"TTL": str(msg.get("ttl") or TTL), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                 "Urgency": "high" if msg["kind"] in ("chat.message", "call") else "normal", "Authorization": vapid_header(endpoint)}
            topic = re.sub(r"[^A-Za-z0-9_-]", "", msg.get("tag") or "")[:32]
            if topic:
                h["Topic"] = topic                                   # neuere ersetzt eine noch nicht abgeholte gleiche
            r = _client().post(endpoint, content=encrypt(raw, p256dh, auth_s), headers=h)
        elif kind == "unifiedpush":
            r = _client().post(endpoint, content=raw, headers={"Content-Type": "application/json", "TTL": str(msg.get("ttl") or TTL)})
        elif kind == "ntfy":
            base, topic = _ntfy_split(endpoint)
            h = {"Content-Type": "application/json"}
            tok = auth._dec(secret) if secret else ""
            if tok:
                h["Authorization"] = "Bearer " + tok
            call = msg["kind"] == "call"                              # Anruf: hoechste Prioritaet (ntfy 5), kurze Lebensdauer
            r = _client().post(base + "/", headers=h, content=json.dumps(
                {"topic": topic, "title": msg["title"], "message": msg["body"], "click": BASE_URL + msg["url"],
                 "tags": ["telephone_receiver" if call else "bell"], "priority": 5 if call else 3},
                ensure_ascii=False).encode())
        else:
            return "gone", "unbekannt"
    except Blocked:
        return "fail", "Ziel nicht freigegeben"
    except (httpx.HTTPError, OSError) as ex:
        if isinstance(ex.__cause__, Blocked):                         # httpx verpackt httpcore-Fehler (wie saga.immich)
            return "fail", "Ziel nicht freigegeben"
        return "retry", type(ex).__name__
    except ValueError as ex:                                          # Schluessel des Abos kaputt
        return "gone", str(ex)[:80]
    return _verdict(r.status_code)


def _send_apns(endpoint, msg, voip=False):
    """APNs. voip: PushKit-Mitteilung (apns-push-type voip, Topic <bundle>.voip) fuer klingelnde Anrufe - die App meldet den
    Anruf sofort an CallKit (Pflicht bei iOS) und holt Zustand/Angebot ueber /api/calls/<id>."""
    import httpx
    cfg = apns_cfg()
    if not cfg["ready"]:
        return "fail", "APNs nicht eingerichtet"
    _, env, tok = (endpoint.split(":", 2) + ["", ""])[:3]
    base = (cfg.get("url") or APNS_HOSTS.get(env) or APNS_HOSTS["production"]).rstrip("/")
    ids = {k: msg[k] for k in ("call", "chat", "media", "caller") if msg.get(k) is not None}
    if voip:
        body = dict({"aps": {}, "url": msg["url"], "kind": msg["kind"], "title": msg["title"], "body": msg["body"]}, **ids)
    else:
        body = dict({"aps": {"alert": {"title": msg["title"], "body": msg["body"]}, "sound": "default", "thread-id": msg["tag"]},
                     "url": msg["url"], "kind": msg["kind"]}, **ids)
        if msg["kind"] == "call":
            body["aps"]["interruption-level"] = "time-sensitive"
    ttl = int(msg.get("ttl") or TTL)
    for attempt in (0, 1):
        try:
            h = {"authorization": "bearer " + _apns_token(cfg, renew=bool(attempt)), "apns-topic": cfg["bundle_id"] + (".voip" if voip else ""),
                 "apns-push-type": "voip" if voip else "alert", "apns-priority": "10", "apns-expiration": str(int(time.time()) + ttl),
                 "apns-collapse-id": msg["tag"][:64]}
            r = _apns_client().post(f"{base}/3/device/{tok}", json=body, headers=h)
        except (httpx.HTTPError, OSError) as ex:
            return "retry", type(ex).__name__
        except ValueError:
            return "fail", "APNs-Schluessel ungueltig"
        if r.status_code == 200:
            return "ok", ""
        try:
            reason = str((r.json() or {}).get("reason") or "")[:60]
        except ValueError:
            reason = ""
        if r.status_code == 403 and reason == "ExpiredProviderToken" and not attempt:
            continue
        if r.status_code == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
            return "gone", f"APNs {r.status_code} {reason}".strip()
        v, d = _verdict(r.status_code)
        return ("retry" if v == "gone" else v), f"APNs {r.status_code} {reason}".strip()
    return "fail", "APNs ExpiredProviderToken"


def _record(c, sid, verdict, detail, now):
    """Ergebnis am Abo vermerken (fuer die Geraeteliste) - harte Fehler nacheinander zaehlen, zu viele: Abo entfernen."""
    if verdict == "ok":
        c.execute("UPDATE push_sub SET last_ok=?, last_err=NULL, fails=0 WHERE id=?", (now, sid))
    elif verdict == "gone":
        c.execute("DELETE FROM push_queue WHERE sub_id=?", (sid,))
        c.execute("DELETE FROM push_sub WHERE id=?", (sid,))
        print(f"push: Abo {sid} entfernt ({detail})", flush=True)
    else:
        c.execute("UPDATE push_sub SET last_err=?, fails=fails+1 WHERE id=?", (f"{detail} @{int(now)}"[:120], sid))
        if verdict == "fail" and (c.execute("SELECT fails FROM push_sub WHERE id=?", (sid,)).fetchone() or [0])[0] >= FAIL_MAX:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (sid,))
            c.execute("DELETE FROM push_sub WHERE id=?", (sid,))
            print(f"push: Abo {sid} nach {FAIL_MAX} Fehlern entfernt ({detail})", flush=True)


def run_once(limit=20):
    """Faellige Auftraege holen (mehrere Prozesse: SKIP LOCKED + Frist) und zustellen -> Anzahl bearbeitet."""
    now = time.time()
    with _db() as c, c:
        c.execute("DELETE FROM push_queue WHERE created < ?", (now - MAX_AGE,))
        jobs = c.execute("UPDATE push_queue SET next_at=?, tries=tries+1 WHERE id IN (SELECT id FROM push_queue WHERE next_at<=? "
                         "ORDER BY next_at, id LIMIT ? FOR UPDATE SKIP LOCKED) RETURNING id, uid, sub_id, kind, ref, actor, data, created, tries",
                         (now + LEASE, now, limit)).fetchall()
    for jid, uid, sid, kind, ref, actor, data, created, tries in jobs:
        sub = None
        try:
            with _db() as c:
                sub = c.execute(f"SELECT {SUB_COLS} FROM push_sub WHERE id=? AND uid=?", (sid, uid)).fetchone()
                msg = message(c, uid, kind, ref, actor, _json_load(data, {}), sub[2], bool(sub[4]), created) if sub else None
            verdict, detail = send(sub, msg) if msg else ("drop", "")
        except Exception as ex:  # noqa: BLE001 - ein kaputter Auftrag haelt die Warteschlange nicht auf
            print(f"push: Auftrag {jid}: {type(ex).__name__}: {ex}", flush=True)
            verdict, detail = "retry", type(ex).__name__
        now = time.time()
        with _db() as c, c:
            if verdict == "retry" and tries < len(BACKOFF) + 1:
                c.execute("UPDATE push_queue SET next_at=? WHERE id=?", (now + BACKOFF[tries - 1], jid))
            else:
                c.execute("DELETE FROM push_queue WHERE id=?", (jid,))
            if sub and verdict != "drop":
                _record(c, sid, verdict, detail, now)
    return len(jobs)


def cleanup():
    """Stuendlich: alte Auftraege, App-Abos ohne Geraete-Anmeldung."""
    with _db() as c, c:
        a = c.execute("DELETE FROM push_queue WHERE created < ?", (time.time() - MAX_AGE,)).rowcount
        b = c.execute("DELETE FROM push_sub WHERE device_id IS NOT NULL AND NOT EXISTS "
                      "(SELECT 1 FROM device_login d WHERE d.id=push_sub.device_id AND d.uid=push_sub.uid)").rowcount
    return a + b


_thread = {"t": None, "stop": False}


def _loop():
    last = 0.0
    while not _thread["stop"]:
        n = 0
        try:
            n = run_once()
            if time.time() - last > 3600:
                last = time.time()
                cleanup()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg: spaeter weiter
            print(f"push: {type(ex).__name__}: {ex}", flush=True)
            time.sleep(5)
        if not n:
            _wake.wait(5.0)
            _wake.clear()


def start():
    if _thread["t"] is None:
        _thread["stop"] = False
        _thread["t"] = threading.Thread(target=_loop, name="push", daemon=True)
        _thread["t"].start()


def stop():
    _thread["stop"] = True
    _wake.set()
    _thread["t"] = None


def delete_account(c, uid):
    """Kontoloeschung: Abos und Auftraege weg (auch ueber USER_TABLES), als Absender anonymisieren."""
    q = c.execute("DELETE FROM push_queue WHERE uid=?", (uid,)).rowcount
    n = c.execute("DELETE FROM push_sub WHERE uid=?", (uid,)).rowcount
    c.execute("UPDATE push_queue SET actor=NULL WHERE actor=?", (uid,))
    return {"push_sub": n, "push_queue": q}


# =====================================================================================================
# Endpunkte
# =====================================================================================================
def _who(request):
    """-> (uid, device_id) - Sitzung (Web; Apps nach /app/resume: device_id aus der Sitzung) oder Geraete-Schluessel
    'Authorization: Bearer hkd_...' (Apps)."""
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Bearer hkd_") and len(hdr) <= 130:
        with _db() as c:
            r = c.execute("SELECT uid, id FROM device_login WHERE hash=?", (_tok_hash(hdr[7:].strip()),)).fetchone()
        return (r[0], r[1]) if r and auth.user_info(r[0]) is not None else (None, None)
    uid = _api_guard(request)
    did = request.session.get("did") if uid else None
    return uid, (int(did) if str(did or "").isdigit() else None)


def _where(kind, endpoint):
    """Kurzer Hinweis fuer die Geraeteliste (Dienst, ohne geheime Teile der Adresse)."""
    if kind in ("apns", "apns-voip"):
        return ("Apple VoIP (" if kind == "apns-voip" else "Apple (") + (endpoint.split(":")[1] if endpoint.count(":") >= 2 else "") + ")"
    host = (urlparse(endpoint).hostname or "").lower()
    for part, name in (("googleapis.com", "Google (Chrome/Android)"), ("mozilla", "Mozilla (Firefox)"),
                       ("push.apple.com", "Apple (Safari)"), ("notify.windows.com", "Microsoft (Edge)")):
        if part in host:
            return name
    return host


def _devices(c, uid, did=None):
    rows = c.execute("SELECT id, kind, endpoint, name, device_id, created, last_ok, last_err, fails, p256dh FROM push_sub "
                     "WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return [{"id": r[0], "type": r[1], "name": r[3] or "", "where": _where(r[1], r[2]), "app": r[4] is not None,
             "this_device": bool(did and r[4] == did), "created": r[5], "last_ok": r[6],
             "last_error": (r[7] or "").split(" @")[0] or None, "fails": r[8],
             "encrypted": r[1] in ("webpush", "apns", "apns-voip") or (r[1] == "unifiedpush" and bool(r[9]))} for r in rows]


def _state(c, uid, did=None):
    srv = server()
    try:
        pub = vapid()[1] if srv["webpush"] else None
    except Exception as ex:  # noqa: BLE001
        print(f"push: VAPID: {type(ex).__name__}: {ex}", flush=True)
        pub = None
    a = apns_cfg()
    return {"server": {"enabled": srv["enabled"], "webpush": srv["webpush"] and bool(pub), "vapid_public": pub,
                       "ntfy": srv["ntfy"], "unifiedpush": srv["unifiedpush"], "apns": srv["apns"],
                       "apns_bundle": a.get("bundle_id") if srv["apns"] else None},
            "settings": prefs(c, uid), "categories": list(CATS), "kinds": CATS,
            "devices": _devices(c, uid, did), "limit": SUB_MAX}


@router.get("/api/push")
def api_push(request: Request):
    """Alles fuer die Einstellungen: was der Server kann, eigene Einstellungen, eigene Geraete."""
    uid, did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        return JSONResponse(_state(c, uid, did), headers={"Cache-Control": "no-store"})


@router.put("/api/push/settings")
async def api_push_settings(request: Request):
    """{cats: {share|comment|chat|publish: bool}, names: bool, quiet: {on, from: 'HH:MM', to: 'HH:MM', tz}} - auch teilweise."""
    uid, _did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if not b or any(k not in ("cats", "names", "quiet") for k in b):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        p = prefs(c, uid)
        cats = b.get("cats", {})
        if not isinstance(cats, dict) or any(k not in CATS or not isinstance(v, bool) for k, v in cats.items()):
            return _bad(t("ungültige Angabe"))
        p["cats"].update(cats)
        if "names" in b:
            if not isinstance(b["names"], bool):
                return _bad(t("ungültige Angabe"))
            p["names"] = b["names"]
        q = b.get("quiet", {})
        if not isinstance(q, dict) or any(k not in ("on", "from", "to", "tz") for k in q):
            return _bad(t("ungültige Angabe"))
        # je Fehler die passende Meldung; Listen/Objekte statt Text -> 400 statt TypeError (BUG-041)
        if "on" in q and not isinstance(q["on"], bool):
            return _bad(t("ungültige Angabe"))
        if any(k in q and not (isinstance(q[k], str) and TIME_RE.fullmatch(q[k])) for k in ("from", "to")):
            return _bad(t("Ruhezeiten: Uhrzeit als HH:MM angeben"))
        if "tz" in q and q["tz"] not in ("", None) and (not isinstance(q["tz"], str) or q["tz"] not in _zones()):
            return _bad(t("Ruhezeiten: unbekannte Zeitzone"))
        p["quiet"].update({k: (v or "") if k == "tz" else v for k, v in q.items()})
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'push', ?)", (uid, json.dumps(p)))
        return JSONResponse(_state(c, uid))


def _clean_name(s):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(s or "")).strip()[:80]


def _resolves_ok(url):
    """Vorab (fuer eine klare Meldung): alle Adressen des Namens erlaubt? Beim Senden prueft netguard noch einmal."""
    import socket
    pu = urlparse(url)
    port = pu.port or (443 if pu.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(pu.hostname, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    return bool(infos) and all(_ip_ok(i[4][0], pu.hostname.lower(), port) for i in infos)


def _keys_ok(p256dh, auth_s):
    try:
        pub, sec = b64u_d(p256dh), b64u_d(auth_s)
        if len(pub) != 65 or pub[0] != 4 or len(sec) != 16:
            return False
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pub)
        return True
    except (ValueError, TypeError):
        return False


def _parse_sub(b, srv):
    """Anfrage -> (felder, None) oder (None, (fehler, code))."""
    kind = b.get("type")
    if not isinstance(kind, str) or kind not in TYPES:
        return None, (t("ungültige Angabe"), 400)
    if kind not in ("apns", "apns-voip") and not srv[kind]:
        return None, (t("Diese Art von Mitteilungen ist auf diesem Server abgeschaltet."), 403)
    f = {"kind": kind, "name": _clean_name(b.get("name")), "p256dh": None, "auth": None, "secret": None}
    keys = b.get("keys") if isinstance(b.get("keys"), dict) else {}
    p256dh, auth_s = keys.get("p256dh") or b.get("p256dh"), keys.get("auth") or b.get("auth")
    if kind in ("apns", "apns-voip"):                  # apns-voip: PushKit-Token der App (nur klingelnde Anrufe, 2.1)
        tok, env = str(b.get("token") or "").strip(), b.get("env") or "production"
        if not APNS_RE.fullmatch(tok) or not isinstance(env, str) or env not in APNS_HOSTS:
            return None, (t("ungültige Angabe"), 400)
        f["endpoint"] = f"{kind}:{env}:{tok.lower()}"
        return f, None
    url, err = check_url(b.get("endpoint") or b.get("url"))
    if err:
        return None, (err, 400)
    f["endpoint"] = url
    if kind == "ntfy":
        pu = urlparse(url)
        base, topic = _ntfy_split(url)
        if pu.query or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", topic or ""):
            return None, (t("ntfy: vollständige Topic-Adresse angeben, z. B. https://ntfy.sh/mein-geheimes-topic"), 400)
        tok = str(b.get("token") or "").strip()
        if tok and not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", tok):
            return None, (t("ungültige Angabe"), 400)
        f["secret"] = auth._enc(tok) if tok else None
        return f, None
    if kind == "webpush" or p256dh or auth_s:
        if not _keys_ok(p256dh, auth_s):
            return None, (t("ungültige Angabe"), 400)
        f["p256dh"], f["auth"] = str(p256dh).strip(), str(auth_s).strip()
    return f, None


@router.post("/api/push/subscriptions")
async def api_push_subscribe(request: Request):
    """Geraet/Browser anmelden (oder erneuern - gleiche Adresse = gleiches Abo; gehoerte es einem anderen Konto, wechselt
    es zu diesem - nur bei webpush/apns: die Adresse erzeugt dort der Browser bzw. Apple fuer GENAU dieses Geraet. ntfy-Topics
    und UnifiedPush-Adressen kann man abschreiben; die eines anderen Kontos -> 409). webpush: {type, endpoint, keys: {p256dh, auth}, name}; ntfy: {type, url, token?, name};
    unifiedpush: {type, endpoint, keys?, name}; apns: {type, token, env: production|sandbox, name}."""
    from starlette.concurrency import run_in_threadpool
    uid, did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    srv = server()
    if not srv["enabled"]:
        return _bad(t("Mitteilungen sind auf diesem Server abgeschaltet."), 403)
    f, err = _parse_sub(b, srv)
    if err:
        return _bad(*err)
    if f["kind"] not in ("apns", "apns-voip") and not await run_in_threadpool(_resolves_ok, f["endpoint"]):
        return _bad(t("Ziel nicht erreichbar oder nicht freigegeben (interne Server gibt der Admin frei)."))
    if not _rate_ok(_reg, uid, REG_MIN):
        return _bad(t("Zu viele Versuche – bitte kurz warten."), 429)
    web = not request.headers.get("authorization", "").startswith("Bearer ")
    dev = did if (f["kind"] != "webpush" or did) else None          # App-Abos haengen an der Geraete-Anmeldung
    now = time.time()
    with _db() as c, c:
        old = c.execute("SELECT id, uid FROM push_sub WHERE endpoint=?", (f["endpoint"],)).fetchone()
        if old and old[1] != uid and f["kind"] not in ("webpush", "apns", "apns-voip"):
            return _bad(t("Diese Adresse ist schon bei einem anderen Konto angemeldet."), 409)
        if old and old[1] != uid:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (old[0],))
            c.execute("DELETE FROM push_sub WHERE id=?", (old[0],))
            old = None
        if not old and c.execute("SELECT COUNT(*) FROM push_sub WHERE uid=?", (uid,)).fetchone()[0] >= SUB_MAX:
            return _bad(t("Höchstens {n} Geräte – bitte zuerst eines entfernen.", n=SUB_MAX), 409)
        if old:
            sid = old[0]
            c.execute("UPDATE push_sub SET p256dh=?, auth=?, secret=COALESCE(?, secret), name=COALESCE(NULLIF(?, ''), name), device_id=?, created=?, fails=0, last_err=NULL "
                      "WHERE id=?", (f["p256dh"], f["auth"], f["secret"], f["name"], dev, now, sid))
        else:
            sid = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, secret, name, device_id, created) VALUES (?,?,?,?,?,?,?,?,?)",
                            (uid, f["kind"], f["endpoint"], f["p256dh"], f["auth"], f["secret"], f["name"], dev, now)).lastrowid
        devs = _devices(c, uid, did)
    if web and f["kind"] == "webpush":
        request.session["push_sub"] = sid                           # Abmelden in diesem Browser beendet das Abo
    d = next((x for x in devs if x["id"] == sid), None)
    return JSONResponse({"id": sid, "device": d, "active": bool(srv.get(f["kind"]))}, status_code=200 if old else 201)


@router.delete("/api/push/subscriptions/{sid}")
def api_push_unsubscribe(request: Request, sid: int):
    uid, _did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM push_sub WHERE id=? AND uid=?", (sid, uid)).rowcount
        if n:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (sid,))
    return JSONResponse({"ok": True}) if n else _bad(t("Nicht gefunden"), 404)


@router.post("/api/push/unsubscribe")
async def api_push_unsubscribe_ep(request: Request):
    """Browser schaltet ab (kennt nur seine Adresse): {endpoint} - nur eigene Abos."""
    uid, _did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    ep = str((b or {}).get("endpoint") or "")[:ENDPOINT_MAX]
    with _db() as c, c:
        ids = [r[0] for r in c.execute("SELECT id FROM push_sub WHERE uid=? AND endpoint=?", (uid, ep)).fetchall()]
        for i in ids:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (i,))
            c.execute("DELETE FROM push_sub WHERE id=?", (i,))
    return JSONResponse({"ok": True, "removed": len(ids)})


def test_send(uid, sid):
    """Testmitteilung sofort (ohne Warteschlange) -> (ergebnis, detail) oder None (kein solches Abo)."""
    with _db() as c:
        sub = c.execute(f"SELECT {SUB_COLS} FROM push_sub WHERE id=? AND uid=?", (sid, uid)).fetchone()
        if not sub:
            return None
        msg = message(c, uid, "test", sub_kind=sub[2], has_keys=bool(sub[4]))
    if sub[2] in ("apns", "apns-voip") and not server()["apns"]:
        return "fail", "APNs nicht eingerichtet"
    if sub[2] == "apns-voip":                       # jede VoIP-Mitteilung muss in CallKit ein Anruf werden - Test per Anruf
        return "fail", "VoIP: mit einem Testanruf pruefen"
    verdict, detail = send(sub, msg)
    with _db() as c, c:
        _record(c, sid, verdict, detail, time.time())
    return verdict, detail


@router.post("/api/push/subscriptions/{sid}/test")
async def api_push_test(request: Request, sid: int):
    """Testmitteilung an dieses Geraet -> {ok, result, error}. Hoechstens alle 5 Sekunden je Geraet."""
    from starlette.concurrency import run_in_threadpool
    uid, _did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not _rate_ok(_tests, (uid, sid), 1, TEST_GAP):
        return _bad(t("Zu viele Versuche – bitte kurz warten."), 429)
    r = await run_in_threadpool(test_send, uid, sid)
    if r is None:
        return _bad(t("Nicht gefunden"), 404)
    return JSONResponse({"ok": r[0] == "ok", "result": r[0], "error": r[1] or None})


@router.get("/sw.js")
def service_worker():
    """Service Worker fuer Web Push (Geltungsbereich "/": Klick auf eine Mitteilung oeffnet/fokussiert Saga)."""
    with open(os.path.join(HERE, "static", "sw.js"), "rb") as fh:
        body = fh.read()
    return Response(body, media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'self'"})
