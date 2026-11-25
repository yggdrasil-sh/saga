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
- 2.5: Abo-Art "stream" - eigene Verbindung der App zum Server (saga.push_stream), wenn auf dem Geraet kein
  UnifiedPush-Verteiler installiert ist. Zustellung wie Web Push (gleiche verschluesselte Nutzlast, TTL, Topic), nur
  dass der "Push-Dienst" Saga selbst ist: Ablage in push_stream_msg, sofort hinaus, wenn die App verbunden ist.
- 2.6 (Bugs-DB 2029-3): Push-Weiche - ob eine Mitteilung rausgeht, entscheidet saga.user_status.reachability() (Haken
  REACH, Art -> Kanal ueber KANAL): Nicht stoeren / Ruhezeit = route hold (Glocke ja, Mitteilung nein), beim Zustellen
  erneut geprueft; zurueckgehaltene Anrufe (chat_call.hold) nie als (VoIP-)Push. Endet Nicht stoeren (nicht die
  Ruhezeit), kommt genau eine Sammelmeldung status.digest ohne Namen (Zaehler user_kv 'status_held').
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
from saga import bus, i18n, netguard, notify, push_relay, push_stream, testclock, user_status
from saga.core import ASSET_V, BASE_URL, HERE, RESTORE_TEST, TZ, _api_guard, _bad, _body, _json_load, _tok_hash, account_lang, t, title
from saga.netguard import Blocked, guarded_client, is_public

router = APIRouter()

E = os.environ
TYPES = ("webpush", "ntfy", "unifiedpush", "apns", "apns-voip", "stream")
CATS = {"share": ("share.added", "share.role"), "comment": ("comment.new", "comment.reply", "comment.mention", "page.mention"),
        "assign": ("db.assigned",),                 # 2.5: in einer Datenbank-Zeile eingetragen
        "chat": ("chat.message", "chat.mention"), "publish": ("publish.withdrawn",), "call": ("call", "call.missed", "call.group"),
        "tasks": ("task.due",),                     # 2.5 (Paket 7): taegliche Zusammenfassung "heute faellig / ueberfaellig"
        "admin": ("backup.failed", "chat.report", "push.relay")}   # nur Admin-Konten (Backups, saga/backups.py; 2.5 Meldungen,
                                                                    # saga/chat_grp.py; 2.6 Push-Relais gestoert, saga/push_relay.py)
# 2.5 (saga.chat_prefs.push_ok, setzt sich beim Import ein): darf eine Chat-Mitteilung noch raus? Ohne Chat-Modul: alte Regel
def _chat_push_ok(c, cid, uid, kind):
    return bool(c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL AND muted=0", (cid, uid)).fetchone())


CHAT_PUSH_OK = _chat_push_ok
CALL_TTL = 60                        # klingelnder Anruf: Mitteilung nach einer Minute verwerfen (statt 4 h vorhalten)


def _call_ttl():
    """Lebensdauer einer Anruf-Mitteilung: so lange, wie der Anruf klingeln darf (Admin: bis 120 s), mindestens CALL_TTL."""
    try:                                  # wie saga.calls.ring_s (push bleibt unabhaengig von calls)
        ring = max(3, min(120, int((auth.settings().get("calls") or {}).get("ring_s") or 45)))
    except (TypeError, ValueError):
        ring = 45
    return max(CALL_TTL, ring + 10)


def _ring_late(created):
    """Auftrag eines klingelnden Anrufs aelter als die Klingeldauer (z. B. nach Wiederholungen): nicht mehr zustellen -
    ein spaeter VoIP-Push wuerde in CallKit einen Anruf zeigen, der nicht mehr klingelt."""
    return bool(created) and created + _call_ttl() < time.time()


KIND_CAT = {k: cat for cat, ks in CATS.items() for k in ks}
# 2.6 (Bugs-DB 2029-3): Sammelmeldung nach "Nicht stoeren" (status.digest) - an sich selbst, ohne Namen, kein abschaltbarer
# Bereich (Kategorie "status" gibt es in CATS nicht -> immer an); K-C traegt spaeter job.* in CATS ein (Kanal notify)
DIGEST_KIND = "status.digest"
KIND_CAT[DIGEST_KIND] = "status"
# Art -> Kanal fuer user_status.reachability(): call (klingelnde Anrufe), chat, mention, sonst notify (auch call.missed,
# Admin, Aufgaben, Auftraege)
KANAL = {"call": "call", "call.group": "call", "chat.message": "chat", "chat.mention": "mention",
         "comment.mention": "mention", "page.mention": "mention"}
HELD_K = "status_held"               # user_kv: waehrend Nicht stoeren zurueckgehaltene Mitteilungen (Zaehler, nur Zahlen)


def kanal_of(kind):
    return KANAL.get(kind, "notify")


def _reach(c, uid, actor, kind, call=None):
    """Standard fuer REACH: die eine Entscheidungsstelle saga.user_status.reachability() (Ruhezeit steckt dort)."""
    return user_status.reachability(c, uid, actor, kanal_of(kind), call=call)


# Haken (2029-3): REACH(c, uid, actor, kind, call=None) -> Reach(route, push, sound, why). Ersetzt die Ruhezeit-Pruefung in
# enqueue (push.quiet_now gilt nur noch, wenn Ruhezeiten NICHT als Nicht stoeren zaehlen - user_status.quiet_plain).
REACH = _reach
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
# 2.5 (Push-Nutzlast 2, Entscheidung des Maintainers 8.11.2026): maschinenlesbare Felder neben title/body (kind, actor, n,
# group_key, chat, lang) - Apps bauen den Satz in ihrer Sprache neu. Abos koennen beim Anlegen eine Sprache (lang) und
# Faehigkeiten (caps) mitschicken; "update": nimmt Aktualisierungen bereits gezeigter Mitteilungen an (UPDATE_KIND).
PAYLOAD_V = 2
SUB_CAPS = ("update",)
UPDATE_KIND = "notify.update"         # interner Auftrag: Glocken-Eintrag geaendert (geloeschte Chat-Nachricht) -> Geraet anpassen
UPDATE_TYPES = ("webpush", "unifiedpush", "stream", "apns")     # ntfy (Klartext ohne App) und VoIP: nie
ENDPOINT_MAX = 1024
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
APNS_RE = re.compile(r"[0-9a-fA-F]{64,200}")
TOPIC_RE = re.compile(r"[A-Za-z0-9.-]{3,200}")   # 2.6 (2262): Bundle-Bezeichner der App im APNs-Abo
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
PAD_STEPS = (512, 1024, 2048)        # 2.6 (2262, docs/PUSH-RELAY.md): Stufen fuers Auffuellen der Apple-Nutzlast
PAD_ROUND = 512                      # ueber der hoechsten Stufe: auf ein Vielfaches davon
E2E_MAX = 2400                       # innerer Text (JSON) einer Apple-Mitteilung hoechstens so gross
COLLAPSE_INFO = b"saga-collapse/1|"


def pad_size(n):
    """Laenge des Klartext-Datensatzes (Inhalt + Trenner 0x02 + Nullen) fuer n Byte Inhalt: kleinste Stufe aus
    PAD_STEPS, die n + 1 fasst, sonst aufgerundet auf ein Vielfaches von PAD_ROUND."""
    need = int(n) + 1
    for s in PAD_STEPS:
        if need <= s:
            return s
    return -(-need // PAD_ROUND) * PAD_ROUND


def e2e_kid(p256dh):
    """saga.k: erste 8 Zeichen von b64url(SHA-256(oeffentlicher Schluessel roh, 65 Byte)) - welcher App-Schluessel."""
    return b64u(hashlib.sha256(b64u_d(p256dh)).digest())[:8]


def e2e_collapse(auth_secret, tag):
    """Undurchsichtige apns-collapse-id: "c" + b64url(HMAC-SHA256(auth roh, "saga-collapse/1|" + tag))[:22].
    tag einer Aktualisierung ist "u" + tag. Gleicher tag -> gleiche Kennung, ohne den tag zu verraten."""
    import hmac
    return "c" + b64u(hmac.new(b64u_d(auth_secret), COLLAPSE_INFO + str(tag).encode(), hashlib.sha256).digest())[:22]


def encrypt(payload, p256dh, auth_secret, salt=None, as_key=None, pad_to=None):
    """Inhalt fuer EIN Browser-Abo verschluesseln -> Koerper der Anfrage (Kopf + ein Datensatz).
    salt/as_key nur fuer Testvektoren (sonst jedes Mal neu und zufaellig).
    pad_to (2.6): Laenge des Klartext-Datensatzes (Inhalt + 0x02 + Nullen, RFC 8188 Abschnitt 2) - verbirgt die Laenge des
    Inhalts; None = ohne Auffuellen wie bisher. Passt der Inhalt nicht hinein: ValueError."""
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
    pad = 0
    if pad_to is not None:
        if not isinstance(pad_to, int) or isinstance(pad_to, bool) or pad_to > rs - 16 or pad_to < len(payload) + 1:
            raise ValueError("Inhalt zu gross")
        pad = pad_to - len(payload) - 1
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02" + b"\x00" * pad, None)   # \x02 = letzter Datensatz, dann Nullen
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
    """Was der Server anbietet (Admin-Bereich -> Mitteilungen). apns/apns-voip (2.6, 2262): eigener Schluessel ODER
    Push-Relais eingeschaltet - welches Abo wirklich zugestellt werden kann, entscheidet _apns_route je Abo."""
    p = auth.settings().get("push") or {}
    on = bool(p.get("enabled", True))
    apns = on and (apns_cfg()["ready"] or push_relay.enabled())
    return {"enabled": on, "webpush": on and bool(p.get("webpush", True)), "ntfy": on and bool(p.get("ntfy", True)),
            "unifiedpush": on and bool(p.get("unifiedpush", True)), "apns": apns,
            "apns-voip": apns, "stream": on and bool(p.get("stream", True))}


def e2e_direct():
    """2.6 (2262): Abos mit Schluesseln bekommen auch auf dem eigenen APNs-Weg nur Chiffrat - Admin-Schalter, Standard AUS
    (bis zum Test auf einem echten Geraet; fehlt das Setting, z. B. nach dem Update, bleibt der eigene Weg Klartext wie
    bisher). Das Relais ist davon unabhaengig immer Ende-zu-Ende."""
    return bool((auth.settings().get("push") or {}).get("apns_e2e_direct", False))


def _apns_route(p256dh, auth_s, topic, cfg=None):
    """2.6 (2262, docs/PUSH-RELAY.md 5.4): Weg eines APNs-Abos -> "direct" | "relay" | None.
    (1) eigener Schluessel bereit und (topic leer oder = eigenes Bundle) -> direkt; (2) sonst Relais an, Abo mit Schluesseln
    und topic beim Relais -> Relais; (3) sonst None (nicht zustellbar - Abos ohne Schluessel nie ueber das Relais)."""
    cfg = cfg or apns_cfg()
    if cfg["ready"] and (not topic or topic == cfg.get("bundle_id")):
        return "direct"
    if p256dh and auth_s and topic and push_relay.usable_for(topic):
        return "relay"
    return None


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
    rows = c.execute("SELECT id, kind, p256dh, auth, topic FROM push_sub WHERE uid=? ORDER BY id", (uid,)).fetchall()
    cfg = apns_cfg() if any(r[1] in ("apns", "apns-voip") for r in rows) else None
    # 2.6 (2262): APNs-Abos nur mit Weg (eigener Schluessel bzw. Relais) - Apps 2.5 ohne Schluessel an einem Server ohne
    # eigenen Schluessel bekommen nichts (statt Fehler zu sammeln); die Geraeteliste zeigt den Hinweis
    return [r[0] for r in rows if srv.get(r[1]) and (r[1] not in ("apns", "apns-voip") or _apns_route(r[2], r[3], r[4], cfg))]


RING_KINDS = ("call", "call.group")   # klingelnde Anrufe (1:1 direkt bzw. ueber Knoten/Gruppe) - die einzigen Arten fuer VoIP


def _route(c, sids, kind):
    """Hat ein Geraet ein UnifiedPush-Abo, bekommt sein Abo "stream" (eigene Verbindung, 2.5) nichts (Umstieg ohne Doppelte).
    VoIP-Abos (iOS/macOS-App, PushKit) bekommen NUR klingelnde Anrufe (RING_KINDS: "call" und "call.group", 1:1 ueber
    Knoten mit dm: true ebenso wie Gruppenanrufe) - Apple verlangt zu jeder VoIP-Mitteilung einen Anruf in CallKit. Hat
    dasselbe Geraet ein VoIP-Abo, geht der Anruf nicht zusaetzlich ueber das normale APNs-Abo.
    Dass ein VoIP-Push nur rausgeht, wenn das Geraet wirklich klingeln soll, sichern die Ausloeser und message():
    - calls.ring_group schickt "call.group" nur an Eingeladene mit Einladung 'ringing' (nicht 'old'/'joined'/'declined'),
      nie an die anrufende Person, nie in grossen Chats (silent) oder stummgeschalteten Chats; Ruhezeiten und
      abgeschaltete Bereiche filtert enqueue.
    - 2.6 (Bugs-DB 2029-3): zurueckgehaltene Anrufe (chat_call.hold gesetzt: dnd/knock/again) gehen NIE als VoIP raus -
      enqueue und message() verlangen hold IS NULL (iOS muss jeden VoIP-Push in CallKit melden). Ob es klingelt,
      entscheidet calls.ring_gate (user_status.reachability); Gruppen-Einladungen (ohne hold je Person) prueft REACH.
    - message() prueft beim Zustellen erneut: "call" nur, solange der Anruf klingelt; "call.group" nur, solange die
      eigene Einladung 'ringing' ist (eigenes anderes Geraet beigetreten -> 'joined', abgelehnt, verpasst -> nichts),
      bei dm (1:1 ueber Knoten) zusaetzlich nur, solange der Anruf klingelt; beide nur innerhalb der Klingeldauer
      ab dem Auftrag (_ring_late).
      Sonst None -> der Auftrag entfaellt, es geht nichts raus (kein leerer Anruf in CallKit)."""
    if not sids:
        return sids
    rows = c.execute(f"SELECT id, kind, device_id FROM push_sub WHERE id IN ({','.join('?' * len(sids))})", tuple(sids)).fetchall()
    up_dev = {r[2] for r in rows if r[1] == "unifiedpush" and r[2] is not None}      # 2.5: Verteiler geht vor
    rows = [r for r in rows if not (r[1] == "stream" and r[2] in up_dev)]
    if kind not in RING_KINDS:
        return [r[0] for r in rows if r[1] != "apns-voip"]
    voip_dev = {r[2] for r in rows if r[1] == "apns-voip" and r[2] is not None}
    return [r[0] for r in rows if not (r[1] == "apns" and r[2] in voip_dev)]


def _call_id(data):
    return re.sub(r"[^0-9a-f]", "", str((data or {}).get("call") or ""))[:32]


def _held_key(since):
    return repr(float(since))


def _held(c, uid, kind, actor, ref, data):
    """2029-3: Mitteilung wegen Nicht stoeren zurueckgehalten (route hold, why dnd) -> fuer die Sammelmeldung zaehlen (nur
    Zahlen, nie Namen/Inhalte; user_kv 'status_held' {s: Beginn, m: Nachrichten, c: verpasste Anrufe, o: sonstige}).
    Die Zeile user_status wird geteilt gesperrt (FOR SHARE): das Ende von Nicht stoeren (FOR UPDATE, gleiche Transaktion wie
    die Sammelmeldung) wartet auf diese Transaktion - nichts geht zwischen Zaehlen und Melden verloren. War Nicht stoeren
    inzwischen zu Ende (wir haben auf die Sperre gewartet) -> None: neu entscheiden. Sonst True."""
    now = testclock.now()
    r = c.execute("SELECT since FROM user_status WHERE uid=? AND state='dnd' AND (until IS NULL OR until>?) FOR SHARE",
                  (uid, now)).fetchone()
    if not r:
        return None
    col = "m" if kind in ("chat.message", "chat.mention") else "c" if kind == "call.missed" else "o"
    if col == "c" and _held_call(c, ref, r[0]):
        return True                       # Bugs-DB 2365: zaehlt _missed_held_calls aus chat_call (auch ohne Glocke) - nicht doppelt
    n = (data or {}).get("attempts")      # Sammel-Push zum Anklopfen (calls.knock_summary): so viele Versuche
    _held_add(c, uid, _held_key(r[0]), col, n if col == "c" and isinstance(n, int) and not isinstance(n, bool) and 0 < n < 10000 else 1)
    return True


def _held_add(c, uid, s, col, n=1):
    """Bugs-DB 2364: Zaehler nicht sofort schreiben, sondern je Transaktion sammeln und kurz vor dem Commit in fester
    Reihenfolge (nach uid) schreiben (db.Conn.before_commit). Sonst sperrten zwei gleichzeitige Gruppen-Nachrichten mit
    gemeinsamen Empfaengern in Nicht stoeren die Zeilen user_kv(uid, 'status_held') in der Reihenfolge der Mitglieder
    ihres Chats - gegenlaeufig -> Verklemmung. Die geteilte Sperre auf user_status (FOR SHARE in _held) bleibt bis zum
    Commit: das Ende von Nicht stoeren wartet weiter auf diese Transaktion und sieht den Zaehler."""
    reg = getattr(c, "before_commit", None)
    if reg is None:                       # Verbindung ohne Haken (Werkzeuge): sofort schreiben
        _held_write(c, uid, s, {col: n})
        return
    pend = c.tx.get(HELD_K)
    if pend is None:
        pend = c.tx[HELD_K] = {}
        reg(lambda: _held_flush(c, pend))
    cnt = pend.setdefault((uid, s), {})
    cnt[col] = cnt.get(col, 0) + n


def _held_flush(c, pend):
    """Laeuft beim Commit ausserhalb des Savepoints von bus.fire - darum eigener Savepoint: ein Fehler beim Zaehlen kostet
    nur den Zaehler, nie die eigentliche Aktion (BUG-009, wie vor Bugs-DB 2364 im Zuhoerer)."""
    try:
        with c.savepoint():
            for key in sorted(pend):
                _held_write(c, key[0], key[1], pend[key])
    except Exception as ex:  # noqa: BLE001 - eine Benachrichtigung darf Speichern/Senden nie verhindern
        print(f"push: Sammelmeldung zaehlen: {type(ex).__name__}: {ex}", flush=True)
    pend.clear()


def _held_write(c, uid, s, inc):
    """user_kv 'status_held' von uid fuer den Zeitraum s um inc {m|c|o: n} erhoehen (anderer Zeitraum: neu beginnen)."""
    m, k, o = (int(inc.get(x) or 0) for x in ("m", "c", "o"))
    c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, ?, ?) ON CONFLICT (uid, k) DO UPDATE SET v = CASE "
              "WHEN user_kv.v::jsonb->>'s' = ? THEN (user_kv.v::jsonb || jsonb_build_object("
              "'m', COALESCE((user_kv.v::jsonb->>'m')::int, 0) + ?, 'c', COALESCE((user_kv.v::jsonb->>'c')::int, 0) + ?, "
              "'o', COALESCE((user_kv.v::jsonb->>'o')::int, 0) + ?))::text ELSE EXCLUDED.v END",
              (uid, HELD_K, json.dumps(dict({"s": s}, **{x: n for x, n in (("m", m), ("c", k), ("o", o)) if n})), s, m, k, o))


def _held_take(c, uid, s):
    """Noch nicht geschriebene Zaehler dieser Transaktion fuer uid/s herausnehmen (Ende von Nicht stoeren in derselben
    Transaktion wie das Zurueckhalten) -> {m, c, o} oder {}."""
    pend = getattr(c, "tx", {}).get(HELD_K)
    return pend.pop((uid, s), {}) if pend else {}


def _held_call(c, nid, since):
    """Gehoert die Glocke call.missed nid zu einem Anruf, den _missed_held_calls fuer dieses Nicht stoeren zaehlt (hold 'dnd',
    angelegt seit since)? Dann zaehlt der Anruf genau dort - auch wenn die Glocke vor dem Ende geloescht wird (Bugs-DB 2365)."""
    r = c.execute("SELECT data FROM notification WHERE id=?", (nid,)).fetchone() if nid else None
    cid = _call_id(_json_load(r[0], {})) if r else ""
    return bool(cid) and bool(c.execute("SELECT 1 FROM chat_call WHERE id=? AND hold='dnd' AND created>=?", (cid, since)).fetchone())


def enqueue(c, uid, kind, actor=None, ref=None, data=None, replaces=None):
    """Mitteilung fuer uid vormerken (eine Zeile je Abo) -> Anzahl. Nichts bei: an sich selbst, Bereich abgeschaltet,
    nicht erreichbar (REACH: Nicht stoeren / Ruhezeit - route hold: Glocke ja, Mitteilung nein; bei Nicht stoeren fuer die
    Sammelmeldung gezaehlt), Ruhezeit ohne Nicht stoeren (quiet_dnd aus), zurueckgehaltener Anruf (chat_call.hold), kein
    Abo, Grenze erreicht. replaces: gebuendelte Benachrichtigung ersetzt eine noch nicht zugestellte - dann wird der
    vorhandene Auftrag umgehaengt (keine zweite Mitteilung).
    Fuer den Chat: kind='chat.message', data={'chat': <kennung>} - NIE Inhalte."""
    if kind not in KIND_CAT or not uid or uid == actor:
        return 0
    if kind == "call.missed" and (data or {}).get("hold") in ("knock", "again"):
        return 0                          # 2029: Anklopfen - Glocke ohne Push, kein Kontingent; Push/Zaehlung macht knock_summary
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
    if not p["cats"].get(KIND_CAT[kind], True):
        return 0
    if quiet_now(p["quiet"], testclock.now()) and user_status.quiet_plain(c, uid):
        return 0                          # Ruhezeit zaehlt nicht als Nicht stoeren (quiet_dnd aus): alte Regel, ohne Ausnahmen
    if kind in RING_KINDS:
        # Klingelnder Anruf: entschieden hat calls.ring_gate (chat_call.hold) - zurueckgehalten = nie ein (VoIP-)Push
        cid = _call_id(data)
        k = c.execute("SELECT hold, callee FROM chat_call WHERE id=?", (cid,)).fetchone() if cid else None
        if k and k[0] is not None:
            return 0
        if kind == "call.group" and k and k[1] != uid and not REACH(c, uid, actor, kind, call=cid).push:
            return 0                      # Gruppen-Einladung (kein hold je Person): Nicht stoeren -> still, kein Push
    elif kind != DIGEST_KIND:
        r = REACH(c, uid, actor, kind)
        if not r.push and r.why == "dnd" and _held(c, uid, kind, actor, ref, data) is None:
            r = REACH(c, uid, actor, kind)          # Nicht stoeren endete, waehrend wir auf die Sperre warteten
        if not r.push:
            return 0                      # route hold: zustellen (Glocke), aber keine Mitteilung
    elif not REACH(c, uid, None, kind).push:
        return 0                          # Sammelmeldung in einer Ruhezeit / neuem Nicht stoeren: nur Live, nie mitgezaehlt
    info = auth.user_info(uid)
    if info is None:
        return 0
    subs = _route(c, _subs(c, uid, info.get("epoch"), srv), kind)
    if not subs or c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=?", (uid,)).fetchone()[0] >= QUEUE_MAX:
        return 0
    if not _rate_ok(_rate, uid, RATE_MIN):
        return 0
    now = time.time()
    d = json.dumps({k: v for k, v in (data or {}).items() if k in ("chat", "call")}         # nur Kennungen, nie Inhalte
                   | ({"digest": _digest_counts(data.get("digest"))} if kind == DIGEST_KIND and data else {}))
    if kind in ("chat.message", "chat.mention") and c.execute("SELECT 1 FROM push_queue WHERE uid=? AND kind=? AND data=? AND tries=0 LIMIT 1",
                                            (uid, kind, d)).fetchone():
        return 0                          # fuer diesen Chat wartet schon eine Mitteilung (wie das Buendeln der Glocke)
    for sid in subs:
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, ref, actor, data, created, next_at) VALUES (?,?,?,?,?,?,?,?)",
                  (uid, sid, kind, ref, actor, d, now, now))
    c.after_commit(wake)
    return len(subs)


def _on_notification(c, uid, id, kind, actor=None, replaces=None, data=None, **_):      # noqa: A002 - Name aus saga.notify
    if kind in ("chat.message", "chat.mention"):
        return                            # der Chat meldet selbst ueber "push.send" (mit Chat-Kennung, Stummschalten beachtet)
    d = data if isinstance(data, dict) else {}
    # 2029: nur die Merker der Glocke weiterreichen (hold: Anklopfen ohne Push; attempts: Sammel-Push calls.knock_summary) -
    # sie landen nicht im Auftrag (enqueue speichert nur chat/call), der Auftrag bleibt wie bisher ohne Kennungen
    enqueue(c, uid, kind, actor=actor, ref=id, replaces=replaces,
            data={k: d[k] for k in ("hold", "attempts") if k in d} or None)


def _on_send(c, uid, kind, actor=None, data=None, **_):
    enqueue(c, uid, kind, actor=actor, data=data)


def _digest_counts(d):
    """{messages, calls, other} - nur ganze Zahlen 0..9999 (sonst 0)."""
    d = d if isinstance(d, dict) else {}
    return {k: max(0, min(9999, d[k])) if isinstance(d.get(k), int) and not isinstance(d.get(k), bool) else 0
            for k in ("messages", "calls", "other")}


def _missed_held_calls(c, uid, since, end):
    """1:1-Anrufe, die waehrend Nicht stoeren zurueckgehalten wurden (chat_call.hold='dnd', calls.ring_gate) und schon zu
    Ende sind - die EINE Zaehlstelle fuer sie, mit oder ohne Glocken-Eintrag call.missed (_held zaehlt deren Glocke nicht,
    _held_call; geloeschte Glocke zaehlt so nicht doppelt, Bugs-DB 2365). Klingelt er beim Ende von Nicht stoeren noch, kommt
    seine Glocke spaeter als eigene Mitteilung. Nur wirklich verpasste: angenommen (answered gesetzt - z. B. ueber
    GET /api/calls/{id} trotz quiet) oder selbst abgelehnt zaehlt nicht."""
    return c.execute("SELECT COUNT(*) FROM chat_call WHERE callee=? AND hold='dnd' AND state='ended' AND created>=? AND created<=? "
                     "AND answered IS NULL AND COALESCE(reason, '')<>'declined'", (uid, since, end)).fetchone()[0]


def _on_dnd_end(c, uid, since, end, **_):
    """2029-3: Nicht stoeren (nicht Ruhezeit) ist zu Ende -> EINE Sammelmeldung status.digest ohne Namen ("Waehrend Nicht
    stoeren: 4 Nachrichten, 1 verpasster Anruf"), in derselben Transaktion wie die Zustandsaenderung: der Zaehler wird
    dabei geloescht (DELETE ... RETURNING) - genau einmal, auch bei zwei Geraeten gleichzeitig, mehreren Prozessen oder
    Absturz (dann gar nicht festgeschrieben, die Ablauf-Schleife bzw. der naechste Wechsel holt es nach). Nichts
    zurueckgehalten -> keine Meldung. Live status.digest an die eigenen Verbindungen; Mitteilung an die eigenen Geraete
    (REACH gilt: in einer Ruhezeit keine Mitteilung)."""
    r = c.execute("DELETE FROM user_kv WHERE uid=? AND k=? RETURNING v", (uid, HELD_K)).fetchone()
    h = _json_load(r[0], {}) if r else {}
    if h.get("s") != _held_key(since):
        h = {}                            # Rest eines frueheren Zeitraums (sollte es nicht geben): nicht mitzaehlen
    for x, n in _held_take(c, uid, _held_key(since)).items():
        h[x] = (h.get(x) if isinstance(h.get(x), int) else 0) + n
    end = end if isinstance(end, (int, float)) else testclock.now()
    held_calls = _missed_held_calls(c, uid, since, end) if prefs(c, uid)["cats"].get(KIND_CAT["call.missed"], True) else 0
    dg = _digest_counts({"messages": h.get("m"), "calls": (h.get("c") if isinstance(h.get("c"), int) else 0) + held_calls,
                         "other": h.get("o")})
    if not any(dg.values()):
        return
    bus.emit_after(c, "direct", {"uids": [uid], "typ": "status.digest",
                                 "data": dict(dg, since=round(since, 3), until=round(end, 3))})
    enqueue(c, uid, DIGEST_KIND, data={"digest": dg})


bus.on("notification.created", _on_notification)
bus.on("push.send", _on_send)
bus.on("status.dnd_end", _on_dnd_end)


def _has_cap(caps, cap):
    return cap in (caps or "").split(",")


def enqueue_update(c, uid, kind, chat, removed=False):
    """2.5: Glocken-Eintrag eines Chats hat sich geaendert (Nachricht fuer alle geloescht) -> Auftrag UPDATE_KIND an die Abos,
    die Aktualisierungen annehmen (caps 'update'). Den Text/Zaehler bestimmt message() beim Zustellen. removed: der Eintrag
    ist weg - eine noch nicht zugestellte Mitteilung dieses Chats entfaellt dann, wenn nichts Ungelesenes mehr da ist.
    Ruhezeiten und Ratenbegrenzung gelten nicht (still, ersetzt nur), Bereich abgeschaltet: nichts. -> Anzahl Auftraege."""
    if kind not in ("chat.message", "chat.mention") or not uid:
        return 0
    if removed and not notify.chat_unread(c, uid, kind, chat)[0]:
        c.execute("DELETE FROM push_queue WHERE uid=? AND kind=? AND data=? AND tries=0", (uid, kind, json.dumps({"chat": chat})))
    srv = server()
    if not srv["enabled"] or not prefs(c, uid)["cats"].get(KIND_CAT[kind], True):
        return 0
    info = auth.user_info(uid)
    if info is None:
        return 0
    sids = _route(c, _subs(c, uid, info.get("epoch"), srv), kind)
    if not sids:
        return 0
    subs = [r[0] for r in c.execute(f"SELECT id, kind, caps FROM push_sub WHERE id IN ({','.join('?' * len(sids))})", tuple(sids)).fetchall()
            if r[1] in UPDATE_TYPES and _has_cap(r[2], "update")]
    if not subs or c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=?", (uid,)).fetchone()[0] >= QUEUE_MAX:
        return 0
    now, d, n = time.time(), json.dumps({"chat": chat, "of": kind}), 0
    for sid in subs:
        if c.execute("SELECT 1 FROM push_queue WHERE sub_id=? AND kind=? AND data=? AND tries=0 LIMIT 1", (sid, UPDATE_KIND, d)).fetchone():
            continue                      # wartet schon - message() rechnet beim Zustellen den aktuellen Stand
        c.execute("INSERT INTO push_queue (uid, sub_id, kind, ref, actor, data, created, next_at) VALUES (?,?,?,?,?,?,?,?)",
                  (uid, sid, UPDATE_KIND, None, None, d, now, now))
        n += 1
    if n:
        c.after_commit(wake)
    return n


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
    if kind in ("comment.mention", "page.mention"):
        return t("Du wurdest erwähnt")
    if kind == "publish.withdrawn":
        return t("Ein Admin hat deine Veröffentlichung zurückgezogen")
    if kind == "db.assigned":
        return t("Du wurdest in einer Datenbank eingetragen")
    if kind == "task.due":
        return t("Aufgaben heute fällig oder überfällig")
    if kind == "call.missed":
        return t("Verpasster Anruf")
    if kind == "backup.failed":
        return t("Backup fehlgeschlagen")
    if kind == "login.locked":
        return t("Anmeldung gesperrt")
    if kind == "chat.report":
        return t("Neue Meldung im Chat")
    if kind == "push.relay":
        return t("Push-Relais gestört")
    return t("Neue Benachrichtigung")


def with_names(p, sub_kind, has_keys):
    """Namen nur, wenn die Person es will UND der Weg Ende-zu-Ende verschluesselt ist (Web Push, UnifiedPush mit
    Schluesseln) bzw. Apple (APNs). ntfy und UnifiedPush ohne Schluessel: nie."""
    return bool(p.get("names")) and (sub_kind in ("webpush", "apns", "apns-voip", "stream") or (sub_kind == "unifiedpush" and has_keys))


def _fields(msg, lang, actor=None, n=None, group_key=None, **more):
    """2.5 (Nutzlast 2): maschinenlesbare Felder dazu - actor NUR, wenn der Satz den Namen ohnehin enthaelt (also "Namen
    zeigen" an und der Weg verschluesselt, with_names) - Datenschutz exakt wie bisher. Fehlende Werte: Feld fehlt."""
    msg["v"], msg["lang"] = PAYLOAD_V, lang
    if actor and actor in (msg.get("body") or ""):
        msg["actor"] = actor
    if n is not None:
        msg["n"] = int(n)
    if group_key:
        msg["group_key"] = group_key
    msg.update({k: v for k, v in more.items() if v is not None})
    return msg


def _chat_id(data):
    return re.sub(r"[^A-Za-z0-9_.-]", "", str((data or {}).get("chat") or ""))[:64]


def _chat_body(kind, nm):
    if kind == "chat.mention":
        return t("{name} hat dich in einem Chat erwähnt", name=nm) if nm else t("Du wurdest in einem Chat erwähnt")
    return t("Neue Nachricht von {name}", name=nm) if nm else t("Neue Nachricht")


# 2029-3: Texte der Sammelmeldung (Kataloge saga/i18n, alle Sprachen; Pluralformen {n:...}). Nie Namen, nie Inhalte.
DIGEST_TEXT = {
    "messages": lambda n: t("{n} {n:Nachricht|Nachrichten}", n=n),
    "calls": lambda n: t("{n} {n:verpasster Anruf|verpasste Anrufe}", n=n),
    "other": lambda n: t("{n} {n:weitere Mitteilung|weitere Mitteilungen}", n=n),
    "join": lambda a, b: t("{a}, {b}##Aufzählung", a=a, b=b),
    "body": lambda items: t("Während „Nicht stören“: {items}", items=items),
}


def digest_text(dg):
    """{messages, calls, other} -> "Waehrend „Nicht stoeren“: 4 Nachrichten, 1 verpasster Anruf" (Sprache der Anfrage)."""
    parts = [DIGEST_TEXT[k](dg[k]) for k in ("messages", "calls", "other") if dg.get(k)]
    out = parts[0] if parts else ""
    for p in parts[1:]:
        out = DIGEST_TEXT["join"](out, p)
    return DIGEST_TEXT["body"](out)


def message(c, uid, kind, ref=None, actor=None, data=None, sub_kind="webpush", has_keys=True, created=None, lang=None):
    """-> {title, body, url, tag, kind, ts, v, lang, ...} oder None (Benachrichtigung weg / kein Zugriff mehr / jetzt nicht
    erreichbar). lang: Sprache des Abos (2.5, beim Anlegen von der App gemeldet) - sonst die des Kontos.
    2029-3: Nachpruefung beim Zustellen - REACH erneut (inzwischen Nicht stoeren/Ruhezeit -> None; ohne Ton -> silent),
    Anrufe nur mit chat_call.hold IS NULL."""
    names = with_names(prefs(c, uid), sub_kind, has_keys)
    lang = lang if lang in i18n.LANGS else account_lang(uid)
    silent = None
    if kind not in (UPDATE_KIND, "test") and kind not in RING_KINDS:
        rc = REACH(c, uid, actor, kind)   # auch status.digest (Bugs-DB 2365: Beschaeftigt "ohne Ton" -> silent)
        if not rc.push:
            return None
        silent = True if not rc.sound else None
    tok = i18n.set_lang(lang)
    thread = None
    try:
        if kind == DIGEST_KIND:
            dg = _digest_counts((data or {}).get("digest"))
            if not any(dg.values()):
                return None
            msg = {"title": title(), "body": digest_text(dg), "url": "/", "tag": "status-digest", "kind": kind,
                   "ts": int(created or time.time())}
            return _fields(msg, lang, None, sum(dg.values()), None, digest=dg, silent=silent)
        if kind == UPDATE_KIND:
            # 2.5: Glocken-Eintrag eines Chats geaendert (Nachricht geloescht) - aktueller Stand; n 0 = Eintrag weg (removed)
            of = (data or {}).get("of") if (data or {}).get("of") in ("chat.message", "chat.mention") else "chat.message"
            chat = _chat_id(data)
            if not chat.isdigit() or not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL",
                                                   (int(chat), uid)).fetchone():
                return None
            n, who = notify.chat_unread(c, uid, of, int(chat))
            if n and not CHAT_PUSH_OK(c, int(chat), uid, of):
                return None               # inzwischen stumm/ausgeblendet: nichts mehr anzeigen (Entfernen geht immer)
            if n and not REACH(c, uid, who, of).push:
                return None               # 2029-3: Nicht stoeren/Ruhezeit - auch keine stille Anzeige (Entfernen geht immer)
            nm = notify.names(c, [who]).get(who) if who and names and n else None
            msg = {"title": title(), "body": _chat_body(of, nm) if n else "", "url": "/chats#c" + chat, "tag": "chat-" + chat,
                   "kind": of, "ts": int(created or time.time())}
            return _fields(msg, lang, nm, n, notify.group_key(of, None, {"chat": int(chat)}), chat=int(chat), update=True,
                           removed=True if not n else None)
        if kind == "test":
            body, url, tag = t("Testmitteilung – Mitteilungen kommen an."), "/", "test"
            return _fields({"title": title(), "body": body, "url": url, "tag": tag, "kind": kind, "ts": int(created or time.time())}, lang)
        elif kind in ("chat.message", "chat.mention"):
            chat = _chat_id(data)
            if chat.isdigit() and not CHAT_PUSH_OK(c, int(chat), uid, kind):
                return None               # inzwischen nicht mehr Mitglied, stummgeschaltet oder ausgeblendet (saga.chat_prefs)
            nm = notify.names(c, [actor]).get(actor) if actor and names else None
            body = _chat_body(kind, nm)
            ref = ("c" + chat) if chat.isdigit() else chat          # wie die Glocke: /chats#c<id>
            url, tag = ("/chats#" + ref) if chat else "/chats", "chat-" + chat
            # Nutzlast 2: n = ungelesene Nachrichten dieses Chats in der Glocke (mindestens 1), group_key wie die Glocke
            n = (notify.chat_unread(c, uid, kind, int(chat))[0] or 1) if chat.isdigit() else 1
            return _fields({"title": title(), "body": body, "url": url, "tag": tag, "kind": kind, "ts": int(created or time.time())},
                           lang, nm, n, notify.group_key(kind, None, {"chat": int(chat)}) if chat.isdigit() else None,
                           chat=int(chat) if chat.isdigit() else None, silent=silent)
        elif kind == "call":
            # Klingelnder Anruf (saga.calls): nur solange er klingelt; Kennungen fuer die Apps (CallKit/ConnectionService)
            cid = _call_id(data)
            r = c.execute("SELECT state, media, chat_id, created, hold FROM chat_call WHERE id=? AND callee=?", (cid, uid)).fetchone()
            if not r or r[0] != "ringing" or r[4] is not None or _ring_late(created):
                return None               # 2029-3: zurueckgehalten (hold) -> nie ein (VoIP-)Push
            nm = notify.names(c, [actor]).get(actor) if actor and names else None
            video = r[1] == "video"
            body = (t("Videoanruf von {name}", name=nm) if video else t("Anruf von {name}", name=nm)) if nm else \
                (t("Eingehender Videoanruf") if video else t("Eingehender Anruf"))
            return _fields({"title": title(), "body": body, "url": f"/chats#call-{cid}", "tag": "call-" + cid, "kind": kind,
                            "ts": int(created or time.time()), "call": cid, "chat": r[2], "media": r[1], "caller": nm,
                            "ttl": max(5, int(r[3] + _call_ttl() - time.time()))}, lang, nm, 1)
        elif kind == "call.group":
            # 2.4: Gruppenanruf (saga.calls) - nur solange die Einladung klingelt; aeltere Apps zeigen es als normale
            # Mitteilung (url oeffnet den Chat), 2.4-Apps lassen es klingeln (call, chat, media, group)
            cid = _call_id(data)
            # 2.5: 1:1 ueber einen Knoten (callee gesetzt): Texte wie 1:1, dm: true (Apps zeigen den Namen der anrufenden Person)
            r = c.execute("SELECT k.state, k.media, k.chat_id, k.created, k.callee FROM chat_call k JOIN chat_call_invite i ON i.call_id=k.id "
                          "WHERE k.id=? AND i.who=? AND i.state='ringing' AND k.hold IS NULL", (cid, uid)).fetchone()
            if not r or r[0] == "ended" or (r[4] == uid and r[0] != "ringing") or _ring_late(created):
                return None               # 1:1 ueber Knoten: schon angenommen (anderes eigenes Geraet) - nicht mehr klingeln
            if r[4] != uid and not REACH(c, uid, actor, kind, call=cid).push:
                return None               # 2029-3: Gruppen-Einladung, inzwischen Nicht stoeren - kein Push (still)
            nm = notify.names(c, [actor]).get(actor) if actor and names else None
            video, dm = r[1] == "video", r[4] == uid
            if dm:
                body = (t("Videoanruf von {name}", name=nm) if video else t("Anruf von {name}", name=nm)) if nm else \
                    (t("Eingehender Videoanruf") if video else t("Eingehender Anruf"))
            else:
                body = (t("Gruppen-Videoanruf von {name}", name=nm) if video else t("Gruppenanruf von {name}", name=nm)) if nm else \
                    (t("Gruppen-Videoanruf – beitreten") if video else t("Gruppenanruf – beitreten"))
            return _fields({"title": title(), "body": body, "url": f"/chats#call-{cid}", "tag": "call-" + cid, "kind": kind,
                            "ts": int(created or time.time()), "call": cid, "chat": r[2], "media": r[1], "caller": nm, "group": True,
                            **({"dm": True} if dm else {}), "ttl": max(5, int(max(r[3], created or 0) + _call_ttl() - time.time()))},
                           lang, nm, 1)
        else:
            it = notify.item(c, uid, ref) if ref else None
            if not it or (kind != "publish.withdrawn" and not it["access"]):
                return None
            body = it["text"] if names else _anon(kind)
            nm, n = ((it.get("actor") or {}).get("name") if names else None), it.get("count") or 1
            url = it["url"] if (it["url"] or "").startswith("/") else "/"     # Admin-Bereich (Backups): eigener Port
            tag = (f"thread-{it['thread_id']}" if it.get("thread_id") else f"note-{it['note']['id']}" if it.get("note") else f"n-{ref}")
            thread = f"note-{it['note']['id']}" if it.get("note") and kind.startswith("comment.") else None
            if kind == "call.missed" and it.get("chat"):
                # Design-Runde (UX-30): verpasste Anrufe eines Chats ersetzen einander auf dem Geraet (gleicher tag /
                # apns-collapse-id) - mit Zahl, wenn es mehrere ungelesene sind
                gk = f"call:{it['chat']['id']}"
                n = max(1, sum(1 for _, k in notify._unread_keys(c, uid, 500) if k == gk))
                tag = f"missed-{it['chat']['id']}"
                if n > 1:
                    body = t("{n} verpasste Anrufe von {name}", n=n, name=nm) if nm else t("{n} verpasste Anrufe", n=n)
        msg = {"title": title(), "body": body, "url": url, "tag": tag, "kind": kind, "ts": int(created or time.time())}
        if thread:
            msg["thread"] = thread        # Design-Runde: APNs thread-id (Gruppe je Seite; tag ersetzt weiter je Unterhaltung)
        return _fields(msg, lang, nm, n, it.get("group_key"), silent=silent,
                       chat=((it.get("chat") or {}).get("id") if kind == "call.missed" and isinstance(it.get("chat"), dict) else None))
    finally:
        i18n._cur.reset(tok)


# =====================================================================================================
# Zustellen
# =====================================================================================================
SUB_COLS = "id, uid, kind, endpoint, p256dh, auth, secret, topic"     # 2.6 (2262): topic (Bundle der App, APNs)


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


def _topic(msg):
    """Topic beim Push-Dienst (neuere ersetzt eine noch nicht abgeholte gleiche). Aktualisierungen ("u" + tag) ersetzen die
    eigentliche Mitteilung NICHT - die kommt zuerst an, danach die Aktualisierung."""
    return re.sub(r"[^A-Za-z0-9_-]", "", ("u" if msg.get("update") else "") + (msg.get("tag") or ""))[:32]


def send(sub, msg):
    """Eine Mitteilung an EIN Abo -> (ergebnis, detail); ergebnis: ok | gone (Abo entfernen) | retry | fail."""
    import httpx
    sid, uid, kind, endpoint, p256dh, auth_s, secret, *more = sub
    if RESTORE_TEST:                                      # Testwiederherstellung: nichts nach aussen (saga/backups.py)
        return "fail", "SAGA_RESTORE_TEST"
    if kind in ("apns", "apns-voip"):
        return _send_apns(endpoint, msg, voip=kind == "apns-voip", keys=(p256dh, auth_s), topic=more[0] if more else None)
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > PAYLOAD_MAX:
        return "fail", "zu gross"
    if kind == "stream":                                              # 2.5: eigene Verbindung der App (wie ein Push-Dienst)
        try:
            body = encrypt(raw, p256dh, auth_s)
        except ValueError as ex:
            return "gone", str(ex)[:80]
        push_stream.deposit(sid, body, int(msg.get("ttl") or TTL), _topic(msg))
        return "ok", ""
    try:
        if kind == "webpush" or (kind == "unifiedpush" and p256dh and auth_s):
            h = {"TTL": str(msg.get("ttl") or TTL), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
                 "Urgency": "high" if msg["kind"] in ("chat.message", "chat.mention", "call", "call.group") and not msg.get("update") else "normal",
                 "Authorization": vapid_header(endpoint)}
            topic = _topic(msg)
            if topic:
                h["Topic"] = topic                                   # neuere ersetzt eine noch nicht abgeholte gleiche
            r = _client().post(endpoint, content=encrypt(raw, p256dh, auth_s), headers=h)
        elif kind == "unifiedpush":
            r = _client().post(endpoint, content=raw, headers={"Content-Type": "application/json", "TTL": str(msg.get("ttl") or TTL)})
        elif kind == "ntfy":
            if msg.get("update"):
                return "fail", "ntfy: keine Aktualisierungen"
            base, topic = _ntfy_split(endpoint)
            h = {"Content-Type": "application/json"}
            tok = auth._dec(secret) if secret else ""
            if tok:
                h["Authorization"] = "Bearer " + tok
            call = msg["kind"] in ("call", "call.group")              # Anruf: hoechste Prioritaet (ntfy 5), kurze Lebensdauer
            r = _client().post(base + "/", headers=h, content=json.dumps(
                {"topic": topic, "title": msg["title"], "message": msg["body"], "click": BASE_URL + msg["url"],
                 "tags": ["telephone_receiver" if call else "bell"], "priority": 5 if call else 2 if msg.get("silent") else 3},
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


LOC_TITLE, LOC_BODY, LOC_CALL = "SAGA_PUSH_TITLE", "SAGA_PUSH_BODY", "SAGA_PUSH_CALL"   # 2262: sichtbarer Teil nur Loc-Keys


def _apns_plan(msg, voip):
    """2.6 (2262, docs/PUSH-RELAY.md 5.3): EINE Planung fuer den eigenen APNs-Weg und das Relais ->
    (push_type, priority, ttl, collapse_tag, rate). Aktualisierungen (update) still im Hintergrund, Anrufe Stufe ring.
    Waechter: voip nur fuer klingelnde Anrufe (RING_KINDS) - sonst ValueError (nie VoIP fuer andere Arten, Kollegin)."""
    update = bool(msg.get("update")) and not voip
    ring = msg.get("kind") in RING_KINDS
    if voip:
        if not ring:
            raise ValueError("voip nur fuer klingelnde Anrufe")
        pt, prio, rate = "voip", 10, "ring"
    elif update:
        pt, prio, rate = "background", 5, "bg"
    else:
        pt, prio, rate = "alert", 10, "ring" if ring else "alert"
    return pt, prio, int(msg.get("ttl") or TTL), ("u" if update else "") + (msg.get("tag") or ""), rate


def _apns_aps(msg, push_type):
    """Sichtbarer Teil einer E2E-Mitteilung - ohne Inhalte (Namen/Texte nur im Chiffrat), ohne thread-id."""
    if push_type == "voip":
        return {}
    if push_type == "background":
        return {"content-available": 1}
    ring = msg.get("kind") in RING_KINDS
    aps = {"alert": {"title-loc-key": LOC_TITLE, "loc-key": LOC_CALL if ring else LOC_BODY}, "mutable-content": 1}
    if not msg.get("silent"):
        aps["sound"] = "default"
    if ring:
        aps["interruption-level"] = "time-sensitive"
    return aps


def _apns_e2e_body(p256dh, auth_s, msg, push_type, salt=None, as_key=None):
    """{aps, saga: {e2e, k, c}}: innerer Text = msg (kompaktes JSON), RFC 8291 mit Auffuellen. ValueError: zu gross /
    Schluessel kaputt."""
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    if len(raw) > E2E_MAX:
        raise OverflowError("zu gross")
    c = encrypt(raw, p256dh, auth_s, salt=salt, as_key=as_key, pad_to=pad_size(len(raw)))
    return {"aps": _apns_aps(msg, push_type), "saga": {"e2e": 1, "k": e2e_kid(p256dh), "c": b64u(c)}}


def _apns_plain_body(msg, push_type):
    """Bisheriger Klartext-Koerper (Abos ohne Schluessel, Apps 2.5) - unveraendert."""
    ids = {k: msg[k] for k in ("call", "chat", "media", "caller", "group", "dm", "v", "lang", "actor", "n", "group_key",
                               "update", "removed", "silent", "digest") if msg.get(k) is not None}
    if push_type == "voip":
        return dict({"aps": {}, "url": msg["url"], "kind": msg["kind"], "title": msg["title"], "body": msg["body"]}, **ids)
    if push_type == "background":
        # 2.5: Aktualisierung (Nachricht geloescht) still im Hintergrund - die App passt die gezeigte Mitteilung an bzw.
        # entfernt sie (tag/group_key); ohne alert, ohne Ton
        return dict({"aps": {"content-available": 1}, "url": msg["url"], "kind": msg["kind"], "tag": msg["tag"],
                     "title": msg["title"], "body": msg["body"]}, **ids)
    body = dict({"aps": {"alert": {"title": msg["title"], "body": msg["body"]}, "sound": "default", "thread-id": msg.get("thread") or msg["tag"]},
                 "url": msg["url"], "kind": msg["kind"]}, **ids)
    if msg.get("silent"):
        body["aps"].pop("sound")      # 2029-3: Beschaeftigt "ohne Ton" (user_status prefs.busy silent)
    if msg["kind"] in RING_KINDS:
        body["aps"]["interruption-level"] = "time-sensitive"
    return body


def _send_apns(endpoint, msg, voip=False, keys=None, topic=None):
    """APNs. voip: PushKit-Mitteilung (apns-push-type voip, Topic <bundle>.voip) fuer klingelnde Anrufe - die App meldet den
    Anruf sofort an CallKit (Pflicht bei iOS) und holt Zustand/Angebot ueber /api/calls/<id>.
    2.6 (2262): keys = (p256dh, auth) des Abos, topic = Bundle der App. Weg je Abo (_apns_route): eigener Schluessel oder
    Push-Relais (nur mit Schluesseln, dann immer E2E); direkt mit Schluesseln nur E2E, wenn push.apns_e2e_direct an ist (Standard aus)."""
    p256dh, auth_s = keys or (None, None)
    cfg = apns_cfg()
    route = _apns_route(p256dh, auth_s, topic, cfg)
    if route is None:
        return "fail", "APNs nicht eingerichtet"
    try:
        push_type, prio, ttl, ctag, rate = _apns_plan(msg, voip)
    except ValueError as ex:
        return "fail", str(ex)
    e2e = bool(p256dh and auth_s) and (route == "relay" or e2e_direct())
    try:
        body = _apns_e2e_body(p256dh, auth_s, msg, push_type) if e2e else _apns_plain_body(msg, push_type)
    except OverflowError:
        return "fail", "zu gross"
    except ValueError:
        return "gone", "Schluessel ungueltig"
    collapse = e2e_collapse(auth_s, ctag) if e2e else ctag[:64]          # E2E: undurchsichtig (ohne Klartext-tag)
    _, env, tok = (endpoint.split(":", 2) + ["", ""])[:3]
    if route == "relay":
        return push_relay.send(tok, env, topic + (".voip" if voip else ""), push_type, prio, int(time.time()) + ttl, collapse,
                               rate, body["aps"], body["saga"])
    return _send_apns_direct(cfg, env, tok, voip, body, push_type, prio, ttl, collapse)


def _send_apns_direct(cfg, env, tok, voip, body, push_type, prio, ttl, collapse):
    import httpx
    base = (cfg.get("url") or APNS_HOSTS.get(env) or APNS_HOSTS["production"]).rstrip("/")
    for attempt in (0, 1):
        try:
            h = {"authorization": "bearer " + _apns_token(cfg, renew=bool(attempt)), "apns-topic": cfg["bundle_id"] + (".voip" if voip else ""),
                 "apns-push-type": push_type, "apns-priority": str(prio), "apns-expiration": str(int(time.time()) + ttl),
                 "apns-collapse-id": collapse}     # Aktualisierung: eigene Kennung (wie Web-Push-Topic)
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
    """Ergebnis am Abo vermerken (fuer die Geraeteliste) - harte Fehler nacheinander zaehlen, zu viele: Abo entfernen.
    2.6 (2262): hold = Relais gestoert/Grenze - das Abo ist schuldlos (nur last_err, fails bleibt, nichts geloescht)."""
    if verdict == "hold":
        c.execute("UPDATE push_sub SET last_err=? WHERE id=?", (f"{detail} @{int(now)}"[:120], sid))
    elif verdict == "ok":
        c.execute("UPDATE push_sub SET last_ok=?, last_err=NULL, fails=0 WHERE id=?", (now, sid))
    elif verdict == "gone":
        c.execute("DELETE FROM push_queue WHERE sub_id=?", (sid,))
        c.execute("DELETE FROM push_sub WHERE id=?", (sid,))
        push_stream.drop_sub(sid)
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
        # 2.6 (2262): klingelnde Anrufe zuerst (nie hinter vielen anderen Mitteilungen)
        jobs = c.execute("UPDATE push_queue SET next_at=?, tries=tries+1 WHERE id IN (SELECT id FROM push_queue WHERE next_at<=? "
                         "ORDER BY (kind IN ('call', 'call.group')) DESC, next_at, id LIMIT ? FOR UPDATE SKIP LOCKED) "
                         "RETURNING id, uid, sub_id, kind, ref, actor, data, created, tries",
                         (now + LEASE, now, limit)).fetchall()
    jobs.sort(key=lambda j: (j[3] not in RING_KINDS, j[0]))
    for jid, uid, sid, kind, ref, actor, data, created, tries in jobs:
        sub = msg = None
        try:
            with _db() as c:
                row = c.execute(f"SELECT {SUB_COLS}, lang FROM push_sub WHERE id=? AND uid=?", (sid, uid)).fetchone()
                sub = tuple(row[:8]) if row else None
                msg = message(c, uid, kind, ref, actor, _json_load(data, {}), sub[2], bool(sub[4]), created, lang=row[8]) if sub else None
            verdict, detail = send(sub, msg) if msg else ("drop", "")
        except Exception as ex:  # noqa: BLE001 - ein kaputter Auftrag haelt die Warteschlange nicht auf
            print(f"push: Auftrag {jid}: {type(ex).__name__}: {ex}", flush=True)
            verdict, detail = "retry", type(ex).__name__
        now = time.time()
        with _db() as c, c:
            if verdict == "hold":
                # 2.6 (2262): Relais gestoert/Grenze - warten ohne Versuch zu zaehlen (tries zurueck: neue Chat-Nachrichten
                # buendeln weiter in diesen Auftrag); verworfen erst nach der Lebensdauer der Mitteilung
                if created + int((msg or {}).get("ttl") or TTL) < now + 1:
                    c.execute("DELETE FROM push_queue WHERE id=?", (jid,))
                else:
                    c.execute("UPDATE push_queue SET next_at=?, tries=GREATEST(tries-1, 0) WHERE id=?", (now + push_relay.take_retry(), jid))
            elif verdict == "retry" and tries < len(BACKOFF) + 1:
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
    return a + b + push_stream.cleanup()


_thread = {"t": None, "stop": False}


def _loop():
    last = 0.0
    while not _thread["stop"]:
        n = 0
        try:
            n = run_once()
            push_relay.tick()                   # 2.6 (2262): Admin-Glocke bei Stoerung, stuendlicher Abgleich mit dem Relais
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
    if RESTORE_TEST:
        return
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
    if kind == "stream":
        return t("Saga (eigene Verbindung)")
    host = (urlparse(endpoint).hostname or "").lower()
    for part, name in (("googleapis.com", "Google (Chrome/Android)"), ("mozilla", "Mozilla (Firefox)"),
                       ("push.apple.com", "Apple (Safari)"), ("notify.windows.com", "Microsoft (Edge)")):
        if part in host:
            return name
    return host


def _devices(c, uid, did=None):
    rows = c.execute("SELECT id, kind, endpoint, name, device_id, created, last_ok, last_err, fails, p256dh, auth, topic FROM push_sub "
                     "WHERE uid=? ORDER BY id", (uid,)).fetchall()
    cfg = apns_cfg() if any(r[1] in ("apns", "apns-voip") for r in rows) else None
    on, out = server()["enabled"], []
    for r in rows:
        d = {"id": r[0], "type": r[1], "name": r[3] or "", "where": _where(r[1], r[2]), "app": r[4] is not None,
             "this_device": bool(did and r[4] == did), "created": r[5], "last_ok": r[6],
             "last_error": (r[7] or "").split(" @")[0] or None, "fails": r[8],
             "encrypted": r[1] in ("webpush", "apns", "apns-voip", "stream") or (r[1] == "unifiedpush" and bool(r[9])),
             **({"connected": push_stream.connected(r[0])} if r[1] == "stream" else {})}
        if r[1] in ("apns", "apns-voip"):
            # 2.6 (2262): Weg (direct | relay | None = nicht zustellbar) und ob Ende-zu-Ende; ohne Weg und ohne Schluessel
            # ist es eine alte App an einem Server ohne eigenen APNs-Schluessel -> Hinweis "App aktualisieren"
            via = _apns_route(r[9], r[10], r[11], cfg) if on else None
            d["via"] = via
            d["e2e"] = bool(r[9] and r[10]) and (via == "relay" or (via == "direct" and e2e_direct()))
            if not via:
                d["hint"] = "app_update" if not (r[9] and r[10]) and push_relay.enabled() else "not_configured"
        out.append(d)
    return out


def _state(c, uid, did=None):
    srv = server()
    try:
        pub = vapid()[1] if srv["webpush"] else None
    except Exception as ex:  # noqa: BLE001
        print(f"push: VAPID: {type(ex).__name__}: {ex}", flush=True)
        pub = None
    a = apns_cfg()
    own = srv["enabled"] and a["ready"]                  # server.apns: wie bis 2.5 nur der eigene Schluessel
    relay = push_relay.public_state() if srv["enabled"] else None
    topics = ([a["bundle_id"]] if own else []) + [x for x in (relay or {}).get("topics") or [] if not (own and x == a["bundle_id"])]
    return {"server": {"enabled": srv["enabled"], "webpush": srv["webpush"] and bool(pub), "vapid_public": pub,
                       "ntfy": srv["ntfy"], "unifiedpush": srv["unifiedpush"], "apns": own,
                       "stream": srv["stream"], "stream_path": push_stream.PATH if srv["stream"] else None,
                       "apns_bundle": a.get("bundle_id") if own else None,
                       # 2.6 (2262, docs/PUSH-RELAY.md 5.1): Ende-zu-Ende fuer Apple, Bundles mit Weg, Zustand des Relais
                       "apns_e2e": 1, "apns_topics": topics, "apns_relay": relay,
                       "payload": PAYLOAD_V, "sub_caps": list(SUB_CAPS)},     # 2.5: Nutzlast 2, Abo-Felder lang/caps
            "settings": prefs(c, uid), "categories": [k for k in CATS if k != "admin" or (auth.user_info(uid) or {}).get("is_admin")],
            "kinds": CATS,
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
        infos = netguard.lookup(pu.hostname, port, proto=socket.IPPROTO_TCP)
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
    f = {"kind": kind, "name": _clean_name(b.get("name")), "p256dh": None, "auth": None, "secret": None, "lang": None, "caps": None,
         "topic": None}
    # 2.5: Sprache der App (de|en|es|ru|th, "" = Sprache des Kontos) und Faehigkeiten - fehlt das Feld, bleibt es beim Erneuern
    if "lang" in b:
        lg = b.get("lang")
        if lg not in (None, "") and lg not in i18n.LANGS:
            return None, (t("ungültige Angabe"), 400)
        f["lang"] = lg or ""
    if "caps" in b:
        cp = b.get("caps")
        if not isinstance(cp, list) or len(cp) > 20 or any(not isinstance(x, str) for x in cp):
            return None, (t("ungültige Angabe"), 400)
        f["caps"] = ",".join(x for x in SUB_CAPS if x in cp)      # unbekannte Faehigkeiten still weglassen
    keys = b.get("keys") if isinstance(b.get("keys"), dict) else {}
    p256dh, auth_s = keys.get("p256dh") or b.get("p256dh"), keys.get("auth") or b.get("auth")
    if kind in ("apns", "apns-voip"):                  # apns-voip: PushKit-Token der App (nur klingelnde Anrufe, 2.1)
        tok, env = str(b.get("token") or "").strip(), b.get("env") or "production"
        if not APNS_RE.fullmatch(tok) or not isinstance(env, str) or env not in APNS_HOSTS:
            return None, (t("ungültige Angabe"), 400)
        f["endpoint"] = f"{kind}:{env}:{tok.lower()}"
        # 2.6 (2262, docs/PUSH-RELAY.md 5.1): Schluessel der App (Ende-zu-Ende) und ihr Bundle - optional; fehlt ein Feld,
        # bleibt beim Erneuern der gespeicherte Wert (keep_keys)
        if p256dh or auth_s:
            if not _keys_ok(p256dh, auth_s):
                return None, (t("ungültige Angabe"), 400)
            f["p256dh"], f["auth"] = str(p256dh).strip(), str(auth_s).strip()
        else:
            f["keep_keys"] = True
        if "topic" in b and b.get("topic") not in (None, ""):
            if not isinstance(b.get("topic"), str) or not TOPIC_RE.fullmatch(b["topic"]):
                return None, (t("ungültige Angabe"), 400)
            f["topic"] = b["topic"]
        return f, None
    if kind == "stream":                               # 2.5: Endpunkt vergibt der Server (einer je Geraete-Anmeldung)
        if not _keys_ok(p256dh, auth_s):
            return None, (t("ungültige Angabe"), 400)
        f["p256dh"], f["auth"], f["endpoint"] = str(p256dh).strip(), str(auth_s).strip(), ""
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
    unifiedpush: {type, endpoint, keys?, name}; apns: {type, token, env: production|sandbox, name, keys?, topic?} (2.6: keys
    und topic fuer Ende-zu-Ende/Push-Relais, docs/PUSH-RELAY.md)."""
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
    if f["kind"] == "stream":
        if not did:                                                   # nur Apps mit Geraete-Anmeldung
            return _bad(t("Nur für Apps mit Geräte-Anmeldung."), 400)
        f["endpoint"] = f"stream:{did}"
    elif f["kind"] not in ("apns", "apns-voip") and not await run_in_threadpool(_resolves_ok, f["endpoint"]):
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
            if f.get("keep_keys"):                                    # 2.6: APNs-Abo ohne keys -> Schluessel bleiben
                c.execute("UPDATE push_sub SET secret=COALESCE(?, secret), name=COALESCE(NULLIF(?, ''), name), device_id=?, created=?, fails=0, last_err=NULL "
                          "WHERE id=?", (f["secret"], f["name"], dev, now, sid))
            else:
                c.execute("UPDATE push_sub SET p256dh=?, auth=?, secret=COALESCE(?, secret), name=COALESCE(NULLIF(?, ''), name), device_id=?, created=?, fails=0, last_err=NULL "
                          "WHERE id=?", (f["p256dh"], f["auth"], f["secret"], f["name"], dev, now, sid))
            if f["topic"]:
                c.execute("UPDATE push_sub SET topic=? WHERE id=?", (f["topic"], sid))
            if f["lang"] is not None:
                c.execute("UPDATE push_sub SET lang=? WHERE id=?", (f["lang"] or None, sid))
            if f["caps"] is not None:
                c.execute("UPDATE push_sub SET caps=? WHERE id=?", (f["caps"], sid))
        else:
            sid = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, secret, name, device_id, created, lang, caps, topic) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, f["kind"], f["endpoint"], f["p256dh"], f["auth"], f["secret"], f["name"], dev, now,
                             f["lang"] or None, f["caps"] or "", f["topic"])).lastrowid
        devs = _devices(c, uid, did)
    if web and f["kind"] == "webpush":
        request.session["push_sub"] = sid                           # Abmelden in diesem Browser beendet das Abo
    d = next((x for x in devs if x["id"] == sid), None)
    if d and d.get("via") == "relay":
        push_relay.ensure_registered_async()      # 2.6 (2262): erstes iOS-Abo mit Schluesseln -> beim Relais anmelden
    out = {"id": sid, "device": d, "active": bool(srv.get(f["kind"])) and (f["kind"] not in ("apns", "apns-voip") or bool(d and d.get("via")))}
    if f["kind"] == "stream":
        out["stream_path"] = push_stream.PATH
    return JSONResponse(out, status_code=200 if old else 201)


@router.delete("/api/push/subscriptions/{sid}")
def api_push_unsubscribe(request: Request, sid: int):
    uid, _did = _who(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM push_sub WHERE id=? AND uid=?", (sid, uid)).rowcount
        if n:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (sid,))
    if n:
        push_stream.drop_sub(sid)
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
        row = c.execute(f"SELECT {SUB_COLS}, lang FROM push_sub WHERE id=? AND uid=?", (sid, uid)).fetchone()
        if not row:
            return None
        sub = tuple(row[:8])
        msg = message(c, uid, "test", sub_kind=sub[2], has_keys=bool(sub[4]), lang=row[8])
    if sub[2] in ("apns", "apns-voip") and not (server()["apns"] and _apns_route(sub[4], sub[5], sub[7])):
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


@router.get("/.well-known/saga-push-relay")
def well_known_relay():
    """2.6 (2262): Domain-Nachweis fuer das Push-Relais - {v: 1, key: <oeffentlicher Schluessel>}, ohne Schluessel 404.
    Unabhaengig von der Foederation; enthaelt nur den oeffentlichen Schluessel."""
    d = push_relay.well_known()
    if not d:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


@router.get("/sw.js")
def service_worker():
    """Service Worker fuer Web Push (Geltungsbereich "/": Klick auf eine Mitteilung oeffnet/fokussiert Saga)."""
    with open(os.path.join(HERE, "static", "sw.js"), "rb") as fh:
        body = fh.read().replace(b"__SAGA_V__", ASSET_V.encode())      # 2.4: Version fuer importScripts (Videos im Chat)
    # wasm-unsafe-eval: libsodium.js (WebAssembly) zum Entschluesseln von Videos im Service Worker (2.4)
    return Response(body, media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'"})
