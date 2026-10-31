"""Chat mit Ende-zu-Ende-Verschluesselung (1.5): Direkt-, Gruppen- und Seiten-Chats.

Der Server sieht nur Ciphertext und die noetigen Metadaten (wer, wann, welcher Chat, Groesse, Empfaenger-Geraete). Alle
Schluessel entstehen auf den Geraeten (Browser: libsodium.js, Apps: libsodium/PyNaCl); hier liegen nur oeffentliche
Schluessel, das mit dem Wiederherstellungsschluessel verschluesselte Schluessel-Buendel, Ciphertexte und die je Empfaenger
versiegelten Inhaltsschluessel. Der Admin kann keine Inhalte lesen.

Krypto-Format v1 (nur libsodium-Primitive; genaue Beschreibung + Testvektoren: docs/ANLEITUNG.md, tests/chat_vectors.json)
  B64  = Standard-Base64 mit Padding; LP(f1..fn) = je Feld uint32_be(len) || bytes (Strings UTF-8)
  Identitaet      crypto_sign (Ed25519) je Konto; Backup-Schluessel crypto_box (X25519) je Konto,
                  backup_sig = sign_detached(LP("saga-chat/v1/backup", uid, backup_pk), ident_sk)
  Geraet          crypto_box (X25519, enc) + crypto_sign (Ed25519, sign), device_id = 32 Hex-Zeichen,
                  sig = sign_detached(LP("saga-chat/v1/device", uid, device_id, enc_pk, sign_pk), ident_sk)
  Buendel         {"v":1,"ident_sk","backup_sk"} mit crypto_aead_xchacha20poly1305_ietf, Schluessel = crypto_pwhash
                  (Argon2id13) aus dem Wiederherstellungsschluessel, AD = LP("saga-chat/v1/bundle", uid)
  Nachricht       K zufaellig; body_ct = B64(nonce24 || xchacha20poly1305_ietf(inhalt, AD, nonce, K)),
                  AD = LP("saga-chat/v1/msg", chat_id, msg_id, sender_uid, sender_device, ts, reply_to|"");
                  K mit crypto_box_seal fuer jedes aktive Geraet aller Mitglieder und fuer backup:<uid> je Mitglied;
                  sig = sign_detached(LP("saga-chat/v1/sig", chat_id, msg_id, sender_uid, sender_device, ts, reply_to|"",
                  generichash32(raw(body_ct))), device_sign_sk) - prueft auch der Server (nur mit Geraeteschluessel senden)
Der Server prueft Signaturen mit `cryptography` (Ed25519, kompatibel zu crypto_sign_detached) - keine eigene Krypto.

Arten: dm (zwei Konten), group (Saga-Gruppe: Mitglieder = Gruppe), free (freie Gruppe mit Einladung, Name verschluesselt
als Nachricht kind='title'), page (alle mit Zugriff auf die Seite laut sharing.Access). Mitglieder von group/page werden bei
jedem Zugriff abgeglichen; neue Nachrichten gehen nur an aktuelle Mitglieder (Senden verlangt genau deren Geraete)."""
import base64
import asyncio
import binascii
import contextvars
import hashlib
import json
import os
import re
import secrets
import struct
import time
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
import auth
from db import connect as _db
from saga import avatars, bus, contacts, file_v2, mfa, notify, sharing
from saga.core import (HANDLE_RE, labels, legacy_sql, local_ref, resolve_input, resolve_ref, subtitles, ADMIN_URL, ASSET_V, E, MEDIA_FRONT, MEDIA_KEY, SEC_HEADERS, icon_csp, STATE_DIR, _api_guard, _bad, _body, _session_user, remember_ui_lang, t, title,
                       tmpl, user_info)
from saga.notes import FILE_MAX, note_chain
from saga.sharing import REMOTE_TYPES, Access, enabled as share_enabled, group_scope, groups_of, names

router = APIRouter()

CHAT_DIR = os.path.join(STATE_DIR, "chat")
UP_DIR = os.path.join(CHAT_DIR, "up")          # 2.4: angefangene Uploads (saga-file/v2)
FED_DIR = os.path.join(CHAT_DIR, "fed")        # 2.4: lueckenhafte Zwischenkopien grosser Anhaenge anderer Server (Range)
MSG_MAX = 64 * 1024                    # Ciphertext je Nachricht (B64)
MSG_RATE = 60                          # Nachrichten je Minute und Konto
DEVICES_MAX = 10                       # aktive Geraete je Konto
MEMBERS_MAX = 50                       # Mitglieder je Chat (darueber spaeter MLS)
FILES_PER_MSG = 10                     # Anhaenge je Nachricht (files[] im verschluesselten Inhalt)
FILE_IDS_PER_MSG = 2 * FILES_PER_MSG   # Datei-Kennungen je Sende-Anfrage: je Bild Original + Vorschaubild (2.1)
FILE_RATE = 60                         # Uploads je Minute (Vorschaubilder zaehlen mit)
FILE_CACHE_MAX = 365 * 86400           # Ciphertext-Blobs aendern sich nie: so lange darf der Browser sie behalten
# Anhaenge je Konto (2.4: Standard 4 GB statt 1 GB, Videos) und groesste Videodatei (Klartext) - beides im Admin-Bereich
# aenderbar (Einstellungen -> Chats); die Umgebung gibt nur den Standard vor. Siehe quota() und video_max().
QUOTA_MB_DEFAULT = max(1, int(E.get("CHAT_QUOTA_MB", "4096") or 4096))
VIDEO_MAX_MB_DEFAULT = max(1, int(E.get("VIDEO_MAX_MB", "500") or 500))
QUOTA = QUOTA_MB_DEFAULT * 1024 * 1024       # (Kompatibilitaet; massgeblich ist quota())
# Videos im Chat (2.4 E2): fortsetzbarer Upload im Format saga-file/v2 (saga/file_v2.py). Die Bytes schreibt saga-media
# direkt auf die Platte (STATE_DIR/chat/up/<id>), Python legt nur Metadaten und Kontingent an (saga/chat_files.py).
UPLOAD_PART = 8 * 1024 * 1024          # hoechstens so viele Bytes je PUT
UPLOADS_PAR = 2                        # gleichzeitige Uploads je Konto
UPLOAD_TTL = 24 * 3600                 # angefangene Uploads verfallen danach
LINK_TTL = 600                         # Geraete-Kopplung: 10 Minuten
PAGE = 50
SKEW = 600                             # erlaubte Abweichung der Client-Zeit (ts) in Sekunden
RETENTIONS = (0, 3600, 86400, 7 * 86400, 30 * 86400, 90 * 86400, 365 * 86400)
SEAL_LEN = 32 + 48                     # crypto_box_seal(32-Byte-Schluessel)
AI_REPLY_TTL = 3600                    # (2.1) so lange kann eine KI-Antwort (Beleg in chat_ai_reply) gesendet werden
AI_GONE = ""                           # (2.1) chat_ai_reply.uid / chat_ai.by_uid nach Kontoloeschung (kein Personenbezug)
_HEX32 = re.compile(r"[0-9a-f]{32}")
REAUTH_SEC = 300                       # "Neu beginnen": Passwort oder Anmeldung hoechstens 5 Minuten alt
_FID = re.compile(r"[A-Za-z0-9_-]{22,40}")
_hits = {}                             # (art, uid) -> [zeiten] fuer Tipp-/Upload-/Such-Bremsen
# 2.3: Chats ueber Servergrenzen (saga.fed_chat setzt sich hier ein - kein zyklischer Import). Chats mit v=2 laufen dort.
FED = None
# 2.4: Anrufe (saga.calls setzt sich hier ein): Zusatzfeld "call" im Chat (Gruppenanruf moeglich, laufender Anruf)
CALLS = None
# Medienpfad (2.4): Anhaenge sollen nicht durch Python laufen. Im Container steht saga-media (media/, Go) vor uvicorn,
# liefert GET/HEAD /api/chat/files/{fid} selbst aus und fragt hier nur die Berechtigung ab (/internal/media/authz, mit
# dem Geheimnis SAGA_MEDIA_KEY, das saga-media beim Start erzeugt). Ohne saga-media: MEDIA_ACCEL=nginx|caddy (der Reverse
# Proxy liefert per X-Accel-Redirect aus) oder Python selbst mit Grenze fuer gleichzeitige Stroeme (MEDIA_PY_STREAMS).
MEDIA_ACCEL = (E.get("MEDIA_ACCEL") or "off").strip().lower()
if MEDIA_ACCEL not in ("nginx", "caddy"):
    MEDIA_ACCEL = "off"
# Pfad im X-Accel-Redirect: nginx eine internal-location (Standard /_saga_media/), Caddy direkt die Datei unter root
MEDIA_ACCEL_PREFIX = ("/" + (E.get("MEDIA_ACCEL_PREFIX") or ("/" if MEDIA_ACCEL == "caddy" else "/_saga_media/")).strip("/") + "/").replace("//", "/")
MEDIA_PY_STREAMS = max(1, int(E.get("MEDIA_PY_STREAMS", "4") or 4))
MEDIA_AUTHZ_TTL = 60                   # saga-media merkt sich eine Erlaubnis hoechstens so lange (Entzug wirkt danach)
_py_streams = {"sem": None}
# Kann der anfragende Client saga-chat/v2 (Kopfzeile X-Saga-Chat: 2)? Aeltere Apps sehen foederierte Chats nur mit Hinweis.
V2_CLIENT = contextvars.ContextVar("saga_chat_v2", default=False)
# 2.5: hoechste Chat-Stufe des Clients (X-Saga-Chat: 2 = Apps/Web 2.3-2.4, 3 = 2.5). Chats der Stufe 3 sehen Clients der
# Stufe 2 als v: 2 (gleiches Krypto-Format, Kompat-Sicht), aeltere nur mit Hinweis.
CHAT_LEVEL = contextvars.ContextVar("saga_chat_level", default=0)
_FULL_H = re.compile(r"(~[a-z2-7]{16})@([a-z0-9.:-]{1,260})")
# 2.4: Gelesen-Bestaetigungen und Online-Status (saga.chat_status setzt sich hier ein): RECEIPT(uid, chat, mitglied, beleg)
# -> Zustand fuer POST /api/chats/{cid}/read mit receipt; STATUS_ME(c, uid) -> Zusatzfelder fuer /api/chat/me
RECEIPT = None
STATUS_ME = None


# 2.4: Medien-Token (saga.chat_files setzt sich hier ein): (token, fid) -> uid oder None
TOKEN_USER = None
# 2.5: Reaktionen und Bearbeiten (saga.chat_x3 setzt sich hier ein): messages_extra, send_edit
X3 = None


# --- Hilfen -------------------------------------------------------------------------------------------------
def enabled():
    return bool(auth.settings().get("chat_enabled", True))


def _mb_setting(key, default):
    try:
        v = int(auth.settings().get(key) or 0)
    except (TypeError, ValueError):
        v = 0
    return (v if v > 0 else default) * 1024 * 1024


def quota():
    """Speicher fuer Chat-Anhaenge je Konto in Bytes (Admin: Einstellungen -> Chats, sonst CHAT_QUOTA_MB, Standard 4096)."""
    return _mb_setting("chat_quota_mb", QUOTA_MB_DEFAULT)


def video_max():
    """Groesste Datei im Format saga-file/v2 (Klartext, Bytes) - Admin oder VIDEO_MAX_MB (Standard 500)."""
    return _mb_setting("chat_video_max_mb", VIDEO_MAX_MB_DEFAULT)


def used_bytes(c, uid):
    """Belegt: fertige Anhaenge plus angekuendigte Groesse laufender Uploads (die zaehlen schon mit)."""
    return c.execute("SELECT COALESCE(SUM(size), 0) FROM chat_file WHERE uid=?", (uid,)).fetchone()[0] + \
        c.execute("SELECT COALESCE(SUM(size), 0) FROM chat_upload WHERE uid=? AND expires>?", (uid, time.time())).fetchone()[0]


def retention_max():
    """Admin-Hoechstwert in Sekunden (0 = unbegrenzt)."""
    try:
        return max(0, int(auth.settings().get("chat_retention_max_days") or 0)) * 86400
    except (TypeError, ValueError):
        return 0


def lp(*fields):
    out = b""
    for f in fields:
        b = f.encode("utf-8") if isinstance(f, str) else bytes(f)
        out += struct.pack(">I", len(b)) + b
    return out


def b64d(s, n=None, maxlen=None):
    """Standard-Base64 (mit Padding) -> bytes; None bei Fehler oder falscher Laenge."""
    if not isinstance(s, str) or (maxlen and len(s) > maxlen) or not s:
        return None
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return None
    if n is not None and len(raw) != n:
        return None
    return raw


def verify(pub_b64, sig_b64, msg):
    """Ed25519-Signatur (crypto_sign_detached) pruefen."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pk, sig = b64d(pub_b64, 32), b64d(sig_b64, 64)
    if pk is None or sig is None:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(pk).verify(sig, msg)
        return True
    except (InvalidSignature, ValueError):
        return False


def device_msg(uid, device_id, enc_pub, sign_pub):
    return lp("saga-chat/v1/device", uid, device_id, b64d(enc_pub, 32), b64d(sign_pub, 32))


def sig_msg(chat_id, msg_id, sender, device, ts, reply_to, body_ct):
    return lp("saga-chat/v1/sig", str(chat_id), msg_id, sender, device, str(ts), reply_to or "",
              hashlib.blake2b(b64d(body_ct) or b"", digest_size=32).digest())


def _rate(kind, uid, n, per=60.0):
    now = time.time()
    k = (kind, uid)
    h = [x for x in _hits.get(k, []) if now - x < per]
    if len(h) >= n:
        _hits[k] = h
        return False
    _hits[k] = h + [now]
    if len(_hits) > 20000:
        _hits.clear()
    return True


def _me(request):
    """-> (uid, fehler)"""
    lv = request.headers.get("x-saga-chat", "")
    lv = int(lv) if lv.isdigit() and len(lv) < 3 else 0
    CHAT_LEVEL.set(lv)
    V2_CLIENT.set(lv >= 2)
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    if not enabled():
        return None, _bad(t("Chats sind auf diesem Server abgeschaltet."), 403)
    return uid, None


def _no_store(d, code=200):
    return JSONResponse(d, status_code=code, headers={"Cache-Control": "no-store"})


def _valid_bundle(s):
    """Verschluesseltes Buendel (JSON-Text) pruefen -> Text oder None."""
    if not isinstance(s, str) or len(s) > 4096:
        return None
    try:
        d = json.loads(s)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("v") != 1 or d.get("kdf") != "argon2id13":
        return None
    ops, mem = d.get("ops"), d.get("mem")
    if not isinstance(ops, int) or not isinstance(mem, int) or not (1 <= ops <= 20) or not (8 << 20 <= mem <= 1 << 30):
        return None
    if b64d(d.get("salt"), 16) is None or b64d(d.get("nonce"), 24) is None or b64d(d.get("ct"), maxlen=3000) is None:
        return None
    return s


def _device_in(b):
    """{id, name, enc_pub, sign_pub, sig} pruefen (Form, nicht Signatur) -> dict oder None."""
    if not isinstance(b, dict):
        return None
    did = str(b.get("id") or b.get("device_id") or "")
    if not _HEX32.fullmatch(did) or b64d(b.get("enc_pub"), 32) is None or b64d(b.get("sign_pub"), 32) is None:
        return None
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(b.get("name") or "")).strip()[:80]
    return {"id": did, "name": name, "enc_pub": b["enc_pub"], "sign_pub": b["sign_pub"], "sig": b.get("sig")}


def _identity(c, uid):
    r = c.execute("SELECT ident_pub, backup_pub, backup_sig, bundle, created, updated FROM chat_identity WHERE uid=?", (uid,)).fetchone()
    return dict(zip(("ident_pub", "backup_pub", "backup_sig", "bundle", "created", "updated"), r)) if r else None


def _devices(c, uids, active=True):
    """uid -> [{id, name, enc_pub, sign_pub, sig, revoked_at}]"""
    uids = sorted({u for u in uids if u})
    if not uids:
        return {}
    out = {u: [] for u in uids}
    for r in c.execute(f"SELECT id, uid, name, enc_pub, sign_pub, sig, revoked_at, created, last_seen FROM chat_device WHERE uid IN "
                       f"({','.join('?' * len(uids))})" + (" AND revoked_at IS NULL" if active else "") + " ORDER BY created", tuple(uids)):
        out[r[1]].append({"id": r[0], "name": r[2], "enc_pub": r[3], "sign_pub": r[4], "sig": r[5], "revoked_at": r[6],
                          "created": r[7], "last_seen": r[8]})
    return out


def _keys(c, uids):
    """Oeffentliche Schluessel je Konto (ohne Buendel): {uid: {ident_pub, backup_pub, backup_sig, devices:[...]}|None}"""
    uids = sorted({u for u in uids if u})
    if not uids:
        return {}
    ids = {r[0]: r[1:] for r in c.execute(f"SELECT uid, ident_pub, backup_pub, backup_sig FROM chat_identity WHERE uid IN "
                                          f"({','.join('?' * len(uids))})", tuple(uids))}
    devs = _devices(c, list(ids), active=False)
    out = {}
    for u in uids:
        if u not in ids:
            out[u] = None
            continue
        out[u] = {"ident_pub": ids[u][0], "backup_pub": ids[u][1], "backup_sig": ids[u][2],
                  "devices": [{"id": d["id"], "enc_pub": d["enc_pub"], "sign_pub": d["sign_pub"], "sig": d["sig"],
                               "revoked": bool(d["revoked_at"])} for d in devs.get(u, [])]}
    return out


# --- Mitglieder ----------------------------------------------------------------------------------------------
C_COLS = ("id, kind, dm_key, group_name, note_id, created_by, created, retention_s, title_seq, last_seq, last_at, v, ref, home_peer, fseq, fstate, "
          "v3_seq, v3_at, v3_forced")


def _row(r):
    return dict(zip(("id", "kind", "dm_key", "group_name", "note_id", "created_by", "created", "retention_s", "title_seq",
                     "last_seq", "last_at", "v", "ref", "home_peer", "fseq", "fstate", "v3_seq", "v3_at", "v3_forced"), r)) if r else None


def fed_hint():
    return t("Chat mit einer Person eines anderen Saga-Servers – in dieser App-Version nicht lesbar. Bitte die App aktualisieren.")


def is_fed(ch):
    return bool(ch) and ch.get("v") == 2 and FED is not None


def is_v3(ch):
    """2.5 (Chat-Stufe 3): lokaler Chat im Format saga-chat/v2, gebunden an die Handles - Partner sehen keine Anmeldenamen."""
    return bool(ch) and ch.get("v") == 3 and FED is not None


def hfmt(ch):
    """Format saga-chat/v2 (foederiert oder Stufe 3): Senden, Empfaenger und Schluessel ueber saga.fed_chat."""
    return bool(ch) and ch.get("v") in (2, 3) and FED is not None


def v3_new():
    """Neue lokale Chats in Stufe 3 anlegen? (2.5, in Arbeit: Einstellung chat_v3, Standard aus)"""
    return FED is not None and bool(auth.settings().get("chat_v3"))


def v3_hint():
    return t("Diesen Chat bitte mit Saga 2.5 oder neuer öffnen – diese App-Version kann ihn nicht lesen.")


def person(c, u, viewer, ch):
    """Kennung eines Mitglieds fuer viewer: in Chats im Format v2 (Stufe 3 und foederiert) andere Konten als Handle '~x@host'
    (nie der Anmeldename), das eigene Konto und sonst unveraendert."""
    if not hfmt(ch) or not u or u == viewer or sharing.is_remote(u):
        return u
    return FED.my_handle(c, u)


def who_in(c, uid, x):
    """Eingabe einer Person an Endpunkten von Chats (Mitglied entfernen, hinzufuegen): Handle '~x@<dieser Server>' (2.5,
    members[].username in Stufe 3) -> Benutzername; sonst wie resolve_input."""
    m = _FULL_H.fullmatch(str(x or "").strip().lower())
    if m and FED is not None and FED.fedsig.authority(m.group(2)) == FED.F.SELF_HOST:
        return FED.F.uid_by_handle(c, m.group(1)) or ""
    return resolve_input(c, uid, x)


def handle_ready(c, uids):
    """Haben alle diese Konten ein Handle-Zertifikat zur aktuellen Identitaet (jedes Konto mit einem Geraet ab 2.3)?"""
    uids = sorted({u for u in uids if u and not sharing.is_remote(u)})
    if not uids:
        return False
    n = c.execute(f"SELECT COUNT(*) FROM chat_fed_cert f JOIN chat_identity i ON i.uid=f.uid AND i.ident_pub=f.ident_pub "
                  f"WHERE f.uid IN ({','.join('?' * len(uids))})", tuple(uids)).fetchone()[0]
    return n == len(uids)


def make_v3(c, ch, uids):
    """Neuer lokaler Chat (noch ohne Nachrichten): Stufe 3, wenn eingeschaltet und alle Mitglieder bereit sind (Frage 1 des
    Konzepts: sonst bleibt er v1 und wird spaeter umgestellt). -> Chat (ggf. aktualisiert)"""
    if not ch or ch.get("v") != 1 or ch.get("ref") or ch.get("last_seq") or not v3_new() or not handle_ready(c, uids):
        return ch
    ref = secrets.token_hex(16) + "@" + FED.F.SELF_HOST
    c.execute("UPDATE chat SET v=3, ref=? WHERE id=? AND v=1 AND ref IS NULL", (ref, ch["id"]))
    return _load(c, ch["id"])


# --- Umstellauftrag v1 -> Stufe 3 (2.5, Konzept A4.3) ------------------------------------------------------------
CONVERT_BATCH = 200                    # hoechstens so viele Chats je Lauf (je Chat eine Transaktion)
ACTIVE_S = 30 * 86400                  # Geraete, die so lange nicht da waren, halten die Umstellung nicht auf
V3_DAYS_DEFAULT = 90                   # Stichtag: spaetestens so viele Tage nach dem Einschalten (Admin aenderbar)
VERIFY_HINT_S = 30 * 86400             # so lange zeigen aeltere Apps nach einer Stichtag-Umstellung den Hinweis im Namen
_conv = {"after": 0}


def v3_deadline():
    """-> (seit, stichtag) in Sekunden; seit = erster Lauf mit eingeschalteter Stufe 3 (Einstellung chat_v3_since)."""
    st = auth.settings()
    since = st.get("chat_v3_since")
    if not isinstance(since, (int, float)) or since <= 0:
        since = time.time()
        auth.save_setting("chat_v3_since", since)
    try:
        days = max(0, int(st.get("chat_v3_days", V3_DAYS_DEFAULT)))
    except (TypeError, ValueError):
        days = V3_DAYS_DEFAULT
    return since, since + days * 86400


# Browser-Geraete (Name aus static/chat.js deviceName) halten die Umstellung nie auf: Das Web laedt beim naechsten Oeffnen
# ohnehin den aktuellen Client dieses Servers und kann dann Stufe 3. Nur Apps koennen veraltet sein.
_WEB_DEV = re.compile(r"^(Safari|Chrome|Firefox|Edge|Browser|Navegador|Браузер|เบราว์เซอร์)( · \S+)?$")


def blocking_devices(c, uids, now):
    """Aktive App-Geraete dieser Konten ohne chat3 -> [(uid, name)], neueste zuerst (halten die Umstellung auf)."""
    if not uids:
        return []
    ph = ",".join("?" * len(uids))
    rows = c.execute(f"SELECT uid, name FROM chat_device WHERE uid IN ({ph}) AND revoked_at IS NULL AND COALESCE(last_seen, created)>? "
                     "AND caps NOT LIKE '%\"chat3\"%' ORDER BY COALESCE(last_seen, created) DESC", (*uids, now - ACTIVE_S)).fetchall()
    return [(u, n) for u, n in rows if not _WEB_DEV.match((n or "").strip())]


def convert_check(c, uids, now, deadline):
    """Darf ein Chat mit diesen Mitgliedern umgestellt werden? -> (ja, per_stichtag). Alle brauchen ein Handle-Zertifikat;
    ohne Stichtag muessen ALLE aktiven Geraete chat3 melden (aeltere Apps uebernehmen "ueberprueft" nicht)."""
    if not handle_ready(c, uids):
        return False, False
    if not blocking_devices(c, uids, now):
        return True, False
    return (True, True) if now >= deadline else (False, False)


def convert_v3(limit=CONVERT_BATCH, now=None):
    """Bestehende lokale Chats v1 auf Stufe 3 umstellen (stuendlich; beliebig oft ausfuehrbar). Nichts wird neu
    verschluesselt: v3_seq = letzte Nachricht v1, danach gilt das Format v2. Systemzeile {"type":"crypto","level":3} ohne Namen
    (v ist dort die Version der Systemzeile; verify: true nach Stichtag),
    chat.changed. -> Anzahl umgestellter Chats."""
    if not v3_new():
        return 0
    now = now or time.time()
    _, deadline = v3_deadline()
    with _db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM chat WHERE v=1 AND ref IS NULL AND home_peer IS NULL AND id>? ORDER BY id LIMIT ?",
                                       (_conv["after"], limit)).fetchall()]
    _conv["after"] = ids[-1] if len(ids) == limit else 0      # naechster Lauf macht weiter (bzw. von vorn)
    n = 0
    for cid in ids:
        with _db() as c, c:
            ch = _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE id=? FOR UPDATE", (cid,)).fetchone())
            if not ch or ch["v"] != 1 or ch["ref"] or ch["home_peer"] is not None:
                continue
            mem = [u for u in members(c, cid) if not sharing.is_remote(u)]
            if not mem:
                continue
            go, forced = convert_check(c, mem, now, deadline)
            if not go:
                continue
            ref = secrets.token_hex(16) + "@" + FED.F.SELF_HOST
            c.execute("UPDATE chat SET v=3, ref=?, v3_seq=?, v3_at=?, v3_forced=? WHERE id=?", (ref, ch["last_seq"] or 0, now, 1 if forced else 0, cid))
            ch = _load(c, cid)
            FED.sysline(c, ch, None, {"type": "crypto", "level": 3, **({"verify": True} if forced else {})})
            _changed(c, cid, mem)
            n += 1
    if n:
        print(f"chat: {n} Chat(s) auf Stufe 3 umgestellt", flush=True)
    return n


def direct_person(c, ch, uids, typ, data, who, key):
    """Live-Ereignis, das eine Person nennt (data[key] = {...username} bzw. Kennung): in Stufe 3 je Empfaenger eigene Daten
    (andere als Handle, sich selbst mit Benutzernamen); sonst wie _direct."""
    uids = sorted({u for u in uids if u})
    if not hfmt(ch):
        _direct(c, uids, typ, data)
        return
    items = []
    for v in uids:
        p = person(c, who, v, ch)
        d = dict(data)
        d[key] = dict(d[key], username=p) if isinstance(d.get(key), dict) else p
        items.append((v, typ, d))
    if items:
        bus.emit_after(c, "direct_each", {"items": items})


def _load(c, cid):
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        return None
    if not 0 < cid < 2 ** 63:
        return None
    return _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE id=?", (cid,)).fetchone())


def page_members(c, nid):
    """Alle Konten mit Zugriff auf die Seite (Eigentuemer + Freigaben an Konten/Gruppen in der Kette) - frisch geprueft."""
    r = c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone()
    if not r:
        return set()
    owner = r[0]
    out = {owner}
    if not share_enabled():
        return out
    chain = note_chain(c, owner, nid)
    if not chain:
        return out
    cand = set()
    for typ, tg in c.execute(f"SELECT target_type, target FROM note_share WHERE owner_uid=? AND note_id IN ({','.join('?' * len(chain))})",
                             (owner, *chain)).fetchall():
        if typ in REMOTE_TYPES:            # Personen anderer Saga-Instanzen: Chats (und ihre Schluessel) bleiben auf dieser Instanz
            continue
        if typ == "user":
            cand.add(tg)
        else:
            cand.update(notify.group_members(c, tg))
    for u in sorted(cand - {owner}):
        if Access(c, u).role(nid):
            out.add(u)
    return out


def entitled(c, ch):
    """Wer gehoert gerade zum Chat? group/page: berechnet; dm/free: None (feste Mitglieder). 2.3: Seiten-Chat mit Personen
    anderer Server (Heim = Server der Seite) - zusaetzlich deren Prinzipale; Kopie eines Chats mit anderem Heim: None
    (die Mitglieder meldet das Heim, saga.fed_chat)."""
    if ch.get("home_peer") is not None:
        return None
    if ch["kind"] == "group":
        return set(notify.group_members(c, ch["group_name"]))
    if ch["kind"] == "page":
        want = page_members(c, ch["note_id"])
        if FED is not None:
            want |= FED.page_remote(c, ch)
        return want
    return None


def members(c, cid):
    return [r[0] for r in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL ORDER BY joined, uid", (cid,))]


def sync(c, ch):
    """Mitglieder von group/page-Chats an Gruppe/Freigaben angleichen -> aktuelle Mitglieder."""
    want = entitled(c, ch)
    cur = members(c, ch["id"])
    if want is None:
        return cur
    if FED is not None and any(sharing.is_remote(u) for u in want) and not is_fed(ch):
        FED.upgrade_page_chat(c, ch)                   # 2.3: Seiten-Chat bekommt Personen anderer Server -> saga-chat/v2
    now = time.time()
    changed = False
    added = sorted(want - set(cur))
    for u in added:
        c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE "
                  "SET left_at=NULL, joined=EXCLUDED.joined, last_read=GREATEST(chat_member.last_read, ?)", (ch["id"], u, now, ch["last_seq"]))
        changed = True
    gone = [u for u in cur if u not in want]
    for u in gone:
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid=?", (now, ch["id"], u))
        unhide(c, ch["id"], u)
        changed = True
    if changed:
        _changed(c, ch["id"], set(cur) | want)
        if is_fed(ch):                                 # 2.3: Heim meldet die neuen Mitglieder den anderen Servern
            FED.members_changed(c, ch, added, gone)
    return sorted(want, key=lambda u: (u not in cur, u))


def _member(c, cid, uid):
    r = c.execute("SELECT role, muted, last_read, joined, state FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (cid, uid)).fetchone()
    return dict(zip(("role", "muted", "last_read", "joined", "state"), r)) if r else None


def _pref(c, cid, uid):
    """Stand je Person (2.1, chat_pref): archiviert seit, "fuer mich geloescht" bis seq, Direkt-Chat ausgeblendet."""
    r = c.execute("SELECT archived, cleared_seq, hidden FROM chat_pref WHERE chat_id=? AND uid=?", (cid, uid)).fetchone()
    return {"archived": r[0], "cleared_seq": r[1] or 0, "hidden": bool(r[2])} if r else {"archived": None, "cleared_seq": 0, "hidden": False}


def may_delete_all(c, uid, ch, m):
    """Was darf uid "fuer alle" loeschen? 'chat' (Direkt-Chat: beide; freie Gruppe: Chat-Admins) - Chat samt Verlauf weg;
    'history' (Gruppen-Chat: Saga-Admins; Seiten-Chat: Eigentuemer der Seite) - nur der Verlauf, der Chat bleibt mit
    der Gruppe/Seite; sonst None."""
    if not m:
        return None
    if ch["kind"] == "dm":
        return "chat"
    if ch["kind"] == "free":
        return "chat" if m["role"] == "admin" else None
    if ch["kind"] == "group":
        u = user_info(uid)
        return "history" if u and u.get("is_admin") else None
    if ch["kind"] == "page" and ch["note_id"]:
        if is_fed(ch):                                 # 2.3: Seiten-Chat ueber Servergrenzen - Verlauf liegt auch dort
            return None
        return "history" if c.execute("SELECT 1 FROM note WHERE id=? AND uid=?", (ch["note_id"], uid)).fetchone() else None
    return None


def revive(c, cid, sender=None):
    """Neue Nachricht (2.1): Chat kommt aus dem Archiv - fuer den Absender und alle nicht stummgeschalteten Mitglieder
    (stumm + archiviert bleibt archiviert); ausgeblendete Direkt-Chats erscheinen wieder (leer bis auf das Neue)."""
    c.execute("UPDATE chat_pref SET archived=NULL WHERE chat_id=? AND archived IS NOT NULL AND (uid=? OR NOT EXISTS "
              "(SELECT 1 FROM chat_member m WHERE m.chat_id=chat_pref.chat_id AND m.uid=chat_pref.uid AND m.muted<>0))", (cid, sender or ""))
    c.execute("UPDATE chat_pref SET hidden=0 WHERE chat_id=? AND hidden<>0", (cid,))


def _chat_for(c, uid, cid, do_sync=True):
    """-> (chat, mitglied, aktuelle mitglieder, fehler). Ohne Mitgliedschaft 404 (den Chat "gibt es nicht")."""
    ch = _load(c, cid)
    if not ch:
        return None, None, None, _bad("not found", 404)
    mem = sync(c, ch) if do_sync else members(c, ch["id"])
    m = _member(c, ch["id"], uid)
    if not m:
        return None, None, None, _bad("not found", 404)
    return ch, m, mem, None


def _fresh(c, ch, mem):
    """Empfaenger ohne volle Angleichung: bei Seiten-/Gruppen-Chats nur, wer JETZT noch dazugehoert."""
    want = entitled(c, ch)
    return mem if want is None else [u for u in mem if u in want]


def _direct(c, uids, typ, data):
    """Live-Ereignis (ohne Inhalt) an diese Konten - nach dem Commit (saga.live, Ereignis-Art 'direct')."""
    uids = sorted({u for u in uids if u})
    if uids:
        bus.emit_after(c, "direct", {"uids": uids, "typ": typ, "data": data})


def _changed(c, cid, uids=None):
    _direct(c, uids if uids is not None else members(c, cid), "chat.changed", {"chat_id": cid})


def may_contact(c, uid, other):
    """Darf uid mit other einen Chat beginnen? Aktives Konto; mit "nur gemeinsame Gruppen" (Teilen) nur dann - oder (2.5)
    wenn other ein Kontakt von uid ist (man hat schon miteinander geschrieben, saga.contacts)."""
    r = c.execute("SELECT disabled FROM users WHERE username=?", (other,)).fetchone()
    if not r or r[0] or other == uid:
        return False
    return not group_scope() or bool(groups_of(c, uid) & groups_of(c, other)) or contacts.has(c, uid, other)


# --- Erneute Anmeldung fuer "Neu beginnen" ---------------------------------------------------------------------
# Neu beginnen wirft alle Geraete hinaus und ersetzt die Schluessel - wer nur eine (gestohlene) Sitzung hat, soll das
# nicht koennen. Verlangt: Passwort (Feld password, lokal/LDAP) ODER eine Anmeldung bzw. Passwort-Bestaetigung dieser
# Sitzung, die hoechstens REAUTH_SEC alt ist (SSO-Konten ohne Passwort: frische Anmeldung ueber /login/sso?reauth=1).
def _reauth_need(request, uid):
    """-> None (frisch genug) | 'password' (Passwort eingeben) | 'login' (neu anmelden, z. B. SSO)."""
    now = time.time()
    if request.session.get("uid") == uid and not request.headers.get("authorization", "").startswith("Bearer "):
        fresh = max(int(request.session.get("login_at") or 0), int(request.session.get("chat_reauth_at") or 0),
                    int(request.session.get("sec_at") or 0))
        if 0 <= now - fresh <= REAUTH_SEC:
            return None
    m = mfa.methods(uid, request)
    return "password" if m else "login"


def _check_reauth(request, uid, b):
    """Passwort (password), Code der Authenticator-App (code) oder Passkey (passkey: Antwort auf /api/chat/reauth/options)
    -> Fehlertext oder None. Mit Passkey/Code klappt es auch ohne Passwort (2.1)."""
    b = b or {}
    if not any(b.get(k) for k in ("password", "code", "passkey")):
        return t("Zum Neubeginnen bitte dein Passwort eingeben.")
    return mfa.confirm(request, uid, b, area="chat", kind="chat", key="wa_chat")


@router.post("/api/chat/reauth/options")
def api_reauth_options(request: Request):
    """Passkey-Optionen fuer die Bestaetigung vor "Neu beginnen" (navigator.credentials.get)."""
    uid, err = _me(request)
    if err:
        return err
    try:
        return _no_store({"options": mfa.auth_options(request, uid, kind="chat", key="wa_chat", before=mfa.sess_start(request)),
                          "origin": mfa.ORIGIN})
    except mfa.MfaError as ex:
        return _bad(str(ex), 409)


@router.post("/api/chat/reauth")
async def api_reauth(request: Request):
    """{password} | {code} | {passkey} -> diese Sitzung gilt REAUTH_SEC lang als frisch angemeldet (fuer "Neu beginnen")."""
    from starlette.concurrency import run_in_threadpool
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    msg = await run_in_threadpool(_check_reauth, request, uid, b)
    if msg:
        print(f"chat: {uid} Bestaetigung abgewiesen", flush=True)
        return _bad(msg, 403)
    request.session["chat_reauth_at"] = int(time.time())
    return _no_store({"ok": True, "valid": REAUTH_SEC})


# --- Identitaet und Geraete -------------------------------------------------------------------------------------
@router.get("/api/chat/me")
def api_me(request: Request, device: str = ""):
    """Eigener Stand: Identitaet (oeffentlich + verschluesseltes Buendel), Geraete, offene Kopplungen, Grenzen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        ident = _identity(c, uid)
        devs = _devices(c, [uid], active=False).get(uid, [])
        n_link = c.execute("SELECT COUNT(*) FROM chat_link WHERE uid=? AND approved IS NULL AND expires>?", (uid, time.time())).fetchone()[0]
        nm = names(c, [uid])
        used = used_bytes(c, uid)
        extra = STATUS_ME(c, uid) if STATUS_ME is not None else {}
    return _no_store({**extra,
        "enabled": enabled(), "uid": uid, "name": nm.get(uid, uid), "identity": ident,
        "devices": [{"id": d["id"], "name": d["name"], "created": d["created"], "last_seen": d["last_seen"],
                     "revoked": bool(d["revoked_at"]), "current": d["id"] == device, "enc_pub": d["enc_pub"],
                     "sign_pub": d["sign_pub"], "sig": d["sig"]} for d in devs],
        "links": n_link, "reauth": _reauth_need(request, uid), "reauth_methods": mfa.methods(uid, request),
        "limits": {"msg_max": MSG_MAX, "file_max": FILE_MAX, "members_max": MEMBERS_MAX, "devices_max": DEVICES_MAX,
                   "files_per_msg": FILES_PER_MSG, "file_ids_per_msg": FILE_IDS_PER_MSG, "retention_max": retention_max(), "retentions": list(RETENTIONS),
                   # 2.4 (Videos im Chat): saga-file/v2 mit fortsetzbarem Upload; video_max = groesste Datei (Klartext-Bytes),
                   # upload_part = hoechstens je PUT, file2_cs = Blockgroesse, quota/used = Chat-Speicher des Kontos (Bytes)
                   "file2": True, "video_max": video_max(), "upload_part": UPLOAD_PART, "file2_cs": file_v2.CS,
                   "uploads_max": UPLOADS_PAR, "quota": quota(), "used": used}})


def _add_device(c, uid, ident_pub, d):
    """Geraet mit gueltigem Zertifikat anlegen -> fehler oder None."""
    if not d or not verify(ident_pub, d.get("sig"), device_msg(uid, d["id"], d["enc_pub"], d["sign_pub"])):
        return _bad(t("Ungültige Signatur des Geräts."))
    if c.execute("SELECT 1 FROM chat_device WHERE id=?", (d["id"],)).fetchone():
        return _bad(t("Dieses Gerät ist schon angemeldet."), 409)
    if c.execute("SELECT COUNT(*) FROM chat_device WHERE uid=? AND revoked_at IS NULL", (uid,)).fetchone()[0] >= DEVICES_MAX:
        return _bad(t("Höchstens {n} Geräte je Konto – bitte ein altes Gerät entfernen.", n=DEVICES_MAX), 409)
    c.execute("INSERT INTO chat_device (id, uid, name, enc_pub, sign_pub, sig, created, last_seen) VALUES (?,?,?,?,?,?,?,?)",
              (d["id"], uid, d["name"], d["enc_pub"], d["sign_pub"], d["sig"], time.time(), time.time()))
    bus.fire("chat.keys.changed", c, uid=uid)          # 2.3 (saga.fed_keys): verbundene Instanzen holen die Schluessel neu
    return None


@router.put("/api/chat/identity")
async def api_identity(request: Request):
    """Einrichtung: {ident_pub, backup_pub, backup_sig, bundle, device:{id, name, enc_pub, sign_pub, sig}}.
    Gibt es schon eine Identitaet: 409 - ausser reset=true ("neu beginnen": alte Geraete abgemeldet, alte Nachrichten fuer
    dieses Konto unlesbar, Gespraechspartner sehen eine geaenderte Sicherheitsnummer). Neu beginnen verlangt eine frische
    Anmeldung (siehe _reauth_need) oder das Passwort im Feld password - sonst 403 {error, reauth: password|login}."""
    from starlette.concurrency import run_in_threadpool
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    with _db() as c:
        had = _identity(c, uid) is not None
    if had and b.get("reset") is True:
        need = _reauth_need(request, uid)
        bad = (await run_in_threadpool(_check_reauth, request, uid, b) if need == "password" else
               t("Zum Neubeginnen bitte neu anmelden – danach hast du 5 Minuten Zeit.")) if need else None
        if bad:
            print(f"chat: {uid} Neu beginnen ohne frische Anmeldung abgewiesen", flush=True)
            return JSONResponse({"error": bad, "reauth": need, "reauth_methods": mfa.methods(uid, request)}, status_code=403,
                                headers={"Cache-Control": "no-store"})
    ident_pub, backup_pub, backup_sig = b.get("ident_pub"), b.get("backup_pub"), b.get("backup_sig")
    bundle = _valid_bundle(b.get("bundle"))
    dev = _device_in(b.get("device"))
    if b64d(ident_pub, 32) is None or b64d(backup_pub, 32) is None or not bundle or not dev:
        return _bad(t("ungültige Angabe"))
    if not verify(ident_pub, backup_sig, lp("saga-chat/v1/backup", uid, b64d(backup_pub, 32))):
        return _bad(t("Ungültige Signatur des Backup-Schlüssels."))
    now = time.time()
    with _db() as c, c:
        old = _identity(c, uid)
        if old and b.get("reset") is not True:
            return _bad(t("Chats sind für dieses Konto schon eingerichtet."), 409)
        if old:
            olds = [r[0] for r in c.execute("SELECT id FROM chat_device WHERE uid=?", (uid,))]
            c.execute("UPDATE chat_device SET revoked_at=? WHERE uid=? AND revoked_at IS NULL", (now, uid))
            if olds:     # alte versiegelte Schluessel sind ohne die alten privaten Schluessel nutzlos
                c.execute(f"DELETE FROM chat_msg_key WHERE recipient IN ({','.join('?' * (len(olds) + 1))})", (*olds, "backup:" + uid))
            c.execute("DELETE FROM chat_link WHERE uid=?", (uid,))
            c.execute("DELETE FROM user_kv WHERE uid=? AND k='chat_trust'", (uid,))    # 2.5: alter Backup-Schluessel - unlesbar
            c.execute("UPDATE chat_identity SET ident_pub=?, backup_pub=?, backup_sig=?, bundle=?, updated=? WHERE uid=?",
                      (ident_pub, backup_pub, backup_sig, bundle, now, uid))
        else:
            c.execute("INSERT INTO chat_identity (uid, ident_pub, backup_pub, backup_sig, bundle, created, updated) VALUES (?,?,?,?,?,?,?)",
                      (uid, ident_pub, backup_pub, backup_sig, bundle, now, now))
        e = _add_device(c, uid, ident_pub, dev)
        if e is not None:
            c.rollback()
            return e
        mine = [r[0] for r in c.execute("SELECT DISTINCT chat_id FROM chat_member WHERE uid=? AND left_at IS NULL", (uid,))]
        for cid in mine:                                   # Gespraechspartner: Schluessel neu laden (Warnung bei Wechsel)
            _changed(c, cid)
        print(f"chat: {uid} richtet Chats ein" + (" (neu begonnen)" if old else ""), flush=True)
        return _no_store({"ok": True, "identity": _identity(c, uid)})


@router.put("/api/chat/bundle")
async def api_bundle(request: Request):
    """Neuer Wiederherstellungsschluessel: {bundle, sig} - sig = sign_detached(LP("saga-chat/v1/bundle-update", uid, bundle), ident_sk)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    bundle = _valid_bundle((b or {}).get("bundle"))
    if not bundle:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ident = _identity(c, uid)
        if not ident:
            return _bad("not found", 404)
        if not verify(ident["ident_pub"], b.get("sig"), lp("saga-chat/v1/bundle-update", uid, bundle)):
            return _bad(t("Ungültige Signatur."), 403)
        c.execute("UPDATE chat_identity SET bundle=?, updated=? WHERE uid=?", (bundle, time.time(), uid))
    return _no_store({"ok": True})


# --- Vertrauen sichern (2.5, Testrunde Web Nr. 063) ------------------------------------------------------------
# "Ueberprueft" und der TOFU-Stand (trust:/trust2: im Browser) gehen sonst bei der Wiederherstellung auf einem neuen
# Geraet verloren. Der Browser legt sie verschluesselt hier ab: AEAD (XChaCha20-Poly1305) mit einem Schluessel aus dem
# Backup-Schluessel (nur eigene Geraete kennen ihn, auch nach der Wiederherstellung) - der Server kann nichts lesen und
# kein "ueberprueft" unterschieben. user_kv 'chat_trust' (mit dem Konto geloescht, beim Neubeginnen verworfen).
TRUST_MAX = 256 * 1024


def _valid_trust(s):
    if not isinstance(s, str) or len(s) > TRUST_MAX:
        return None
    try:
        d = json.loads(s)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("v") != 1 or b64d(d.get("nonce"), 24) is None or not isinstance(d.get("ct"), str) \
            or len(d["ct"]) < 24 or b64d(d["ct"]) is None:
        return None
    return s


@router.get("/api/chat/trust")
async def api_trust_get(request: Request):
    """-> {blob: JSON-Text {v:1, nonce, ct} | null} - verschluesselter Vertrauens-Stand (static/chat-crypto.js sealTrust)."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='chat_trust'", (uid,)).fetchone()
    return _no_store({"blob": r[0] if r else None})


@router.put("/api/chat/trust")
async def api_trust_put(request: Request):
    """{blob} - ersetzt den verschluesselten Vertrauens-Stand (der Browser fuehrt vorher mit dem gespeicherten zusammen)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    blob = _valid_trust((b or {}).get("blob"))
    if not blob:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        if not _identity(c, uid):
            return _bad("not found", 404)
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'chat_trust', ?)", (uid, blob))
    return _no_store({"ok": True})


@router.post("/api/chat/devices")
async def api_device_add(request: Request):
    """Geraet anmelden (nach Wiederherstellung): {id, name, enc_pub, sign_pub, sig} - sig vom Identitaetsschluessel."""
    uid, err = _me(request)
    if err:
        return err
    d = _device_in(await _body(request))
    if not d:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ident = _identity(c, uid)
        if not ident:
            return _bad("not found", 404)
        e = _add_device(c, uid, ident["ident_pub"], d)
        if e is not None:
            return e
    return _no_store({"ok": True, "id": d["id"]})


@router.delete("/api/chat/devices/{did}")
def api_device_delete(did: str, request: Request):
    """Geraet entfernen: bekommt keine Schluessel mehr, seine versiegelten Schluessel werden geloescht."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        n = c.execute("UPDATE chat_device SET revoked_at=? WHERE id=? AND uid=? AND revoked_at IS NULL", (time.time(), did, uid)).rowcount
        if not n:
            return _bad("not found", 404)
        c.execute("DELETE FROM chat_msg_key WHERE recipient=?", (did,))
        _direct(c, [uid], "chat.device", {"id": did, "revoked": True})
        bus.fire("chat.keys.changed", c, uid=uid)
    return _no_store({"ok": True})


# --- Neues Geraet mit vorhandenem Geraet koppeln -----------------------------------------------------------------
# 1. neues Geraet: POST /api/chat/link {device_id, name, enc_pub, sign_pub} -> {id}; zeigt den Kurzcode
#    (generichash32(LP("saga-chat/v1/link", uid, device_id, enc_pk, sign_pk)), 4 Bloecke a 5 Bytes BE mod 10^5 = 20 Ziffern -
#    lang genug, dass der Server keine eigenen Schluessel mit gleichem Code erzeugen kann)
# 2. vorhandenes Geraet: GET /api/chat/link (offene Anfragen), zeigt denselben Code; nach Bestaetigung
#    POST /api/chat/link/{id} {sig (Geraete-Zertifikat), sealed = crypto_box_seal(buendel_klartext, enc_pk des neuen Geraets)}
# 3. neues Geraet: GET /api/chat/link/{id} -> {state: approved, sealed} (einmalig), oeffnet das Buendel.
def _link_row(r):
    return dict(zip(("id", "device_id", "name", "enc_pub", "sign_pub", "created", "expires", "sealed", "approved"), r))


L_COLS = "id, device_id, name, enc_pub, sign_pub, created, expires, sealed, approved"


@router.post("/api/chat/link")
async def api_link_new(request: Request):
    uid, err = _me(request)
    if err:
        return err
    d = _device_in(await _body(request))
    if not d:
        return _bad(t("ungültige Angabe"))
    now = time.time()
    with _db() as c, c:
        if not _identity(c, uid):
            return _bad("not found", 404)
        c.execute("DELETE FROM chat_link WHERE expires<?", (now,))
        if c.execute("SELECT COUNT(*) FROM chat_link WHERE uid=? AND approved IS NULL", (uid,)).fetchone()[0] >= 3:
            return _bad(t("Zu viele offene Anfragen – bitte kurz warten."), 429)
        if c.execute("SELECT 1 FROM chat_device WHERE id=?", (d["id"],)).fetchone():
            return _bad(t("Dieses Gerät ist schon angemeldet."), 409)
        lid = secrets.token_hex(16)
        c.execute("INSERT INTO chat_link (id, uid, device_id, name, enc_pub, sign_pub, created, expires) VALUES (?,?,?,?,?,?,?,?)",
                  (lid, uid, d["id"], d["name"], d["enc_pub"], d["sign_pub"], now, now + LINK_TTL))
        _direct(c, [uid], "chat.link", {"id": lid})
    return _no_store({"id": lid, "expires": now + LINK_TTL})


@router.get("/api/chat/link")
def api_link_list(request: Request):
    uid, err = _me(request)
    if err:
        return err
    with _db() as c:
        rows = c.execute(f"SELECT {L_COLS} FROM chat_link WHERE uid=? AND approved IS NULL AND expires>? ORDER BY created",
                         (uid, time.time())).fetchall()
    return _no_store({"links": [{k: v for k, v in _link_row(r).items() if k not in ("sealed", "approved")} for r in rows]})


@router.get("/api/chat/link/{lid}")
def api_link_get(lid: str, request: Request):
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        r = c.execute(f"SELECT {L_COLS} FROM chat_link WHERE id=? AND uid=?", (lid, uid)).fetchone()
        if not r:
            return _no_store({"state": "gone"})
        d = _link_row(r)
        if d["approved"]:
            c.execute("DELETE FROM chat_link WHERE id=?", (lid,))          # nur einmal abholbar
            return _no_store({"state": "approved", "sealed": d["sealed"]})
        if d["expires"] < time.time():
            c.execute("DELETE FROM chat_link WHERE id=?", (lid,))
            return _no_store({"state": "expired"})
    return _no_store({"state": "pending", "expires": d["expires"]})


@router.post("/api/chat/link/{lid}")
async def api_link_approve(lid: str, request: Request):
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    sealed = b.get("sealed")
    if b64d(sealed, maxlen=2048) is None:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        r = c.execute(f"SELECT {L_COLS} FROM chat_link WHERE id=? AND uid=? AND approved IS NULL AND expires>?", (lid, uid, time.time())).fetchone()
        ident = _identity(c, uid)
        if not r or not ident:
            return _bad("not found", 404)
        d = _link_row(r)
        e = _add_device(c, uid, ident["ident_pub"], {"id": d["device_id"], "name": d["name"], "enc_pub": d["enc_pub"],
                                                     "sign_pub": d["sign_pub"], "sig": b.get("sig")})
        if e is not None:
            return e
        c.execute("UPDATE chat_link SET sealed=?, approved=? WHERE id=?", (sealed, time.time(), lid))
        _direct(c, [uid], "chat.link", {"id": lid, "approved": True})
    return _no_store({"ok": True})


@router.delete("/api/chat/link/{lid}")
def api_link_delete(lid: str, request: Request):
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        n = c.execute("DELETE FROM chat_link WHERE id=? AND uid=?", (lid, uid)).rowcount
        if n:
            _direct(c, [uid], "chat.link", {"id": lid, "gone": True})
    return _no_store({"ok": True, "deleted": n})


# --- Schluessel, Personen, Seiten -----------------------------------------------------------------------------
def _known(c, uid):
    """Konten, mit denen uid einen Chat hat oder hatte (2.5: ohne Chats der Stufe 3 - dort gibt es Schluessel nur per Handle
    ueber /api/chat/v2/keys; umgestellte Chats mit Altbestand v1 zaehlen weiter)."""
    return {r[0] for r in c.execute("SELECT DISTINCT m2.uid FROM chat_member m1 JOIN chat_member m2 ON m2.chat_id=m1.chat_id "
                                    "JOIN chat ch ON ch.id=m1.chat_id WHERE m1.uid=? AND " + legacy_sql(), (uid,))}


@router.get("/api/chat/keys")
def api_keys(request: Request, users: str = ""):
    """Oeffentliche Schluessel: ?users=a,b (hoechstens 60; Benutzername oder 2.4 Handle '~…') - nur eigene und von
    Personen mit einem gemeinsamen Chat (auch frueher). Antwort je angefragter Kennung (Handle bleibt Handle); null =
    noch keine Chats eingerichtet. Unbekannte, nicht erlaubte und noch fremde Konten fehlen gleichermassen (2.4,
    Sicherheitsrunde 2: kein Orakel fuer Anmeldenamen - bis 2.3 auch Konten, mit denen man einen Chat beginnen darf;
    Clients holen Schluessel ohnehin erst fuer Mitglieder). Enthaelt entfernte Geraete (revoked) - fuer die Pruefung
    alter Signaturen."""
    uid, err = _me(request)
    if err:
        return err
    want = list(dict.fromkeys(u.strip() for u in users.split(",") if u.strip()))[:60]
    with _db() as c:
        known = _known(c, uid) | {uid}
        ask = {}
        for w in want:
            u = resolve_ref(c, w)                         # Handle -> Benutzername; sonst klein
            if u and u in known:
                ask[w if HANDLE_RE.fullmatch(w) else u] = u
        keys = _keys(c, list(ask.values()))
        return _no_store({"users": {w: keys.get(u) for w, u in ask.items()}})


def _like(q):
    return re.sub(r"([\\%_])", r"\\\1", q)


@router.get("/api/chat/people")
def api_people(request: Request, q: str = ""):
    """Personensuche fuer neue Unterhaltungen (wie beim Teilen, hoechstens 10) + passende eigene Gruppen. Ab 3 Zeichen
    Teilwort-Suche im Anzeige-/oeffentlichen Namen, bei 1-2 Zeichen nur genaue Treffer (z. B. "hd"); Anmeldename nur genau
    (sharing.name_match) - nie eine Liste aller Konten."""
    uid, err = _me(request)
    if err:
        return err
    q = re.sub(r"\s+", " ", q or "").strip().lower()[:60]
    if not _rate("people", uid, 60):
        return _bad(t("zu schnell"), 429)
    if not q:
        return _no_store({"users": [], "groups": []})
    with _db() as c:
        mine = sorted(groups_of(c, uid))
        sql = ("SELECT u.username, u.display_name, (i.uid IS NOT NULL) FROM users u LEFT JOIN chat_identity i ON i.uid=u.username "
               "WHERE u.disabled=0 AND u.username<>? AND " + sharing.name_match())
        args = [uid] + sharing.name_args(c, q)
        rows = []
        if group_scope():
            if mine:
                ph = ",".join("?" * len(mine))
                sql += (f" AND (EXISTS (SELECT 1 FROM user_group ug JOIN groups g ON g.id=ug.group_id WHERE ug.user_id=u.id AND g.name IN ({ph}))"
                        f" OR EXISTS (SELECT 1 FROM user_ext_group e WHERE e.user_id=u.id AND e.name IN ({ph})))")
                args += mine + mine
            else:
                sql = None
        if sql:
            rows = c.execute(sql + sharing.name_order() + " LIMIT 10", (*args, args[3])).fetchall()
        av = avatars.urls(c, [r[0] for r in rows]) if rows else {}
        lb = labels(c, [r[0] for r in rows]) if rows else {}
        sb = subtitles(c, [r[0] for r in rows], lb) if rows else {}
        # 2.5: passende Kontakte vorne (auch 1-2 Zeichen als Teilwort und ausserhalb "nur gemeinsame Gruppen" - man hat
        # schon miteinander geschrieben); nicht anschreibbare (gone) fehlen hier
        kn = contacts.entries(c, uid, q, limit=10, with_gone=False)
    # 2.3: Personen anderer Instanzen aus dem Adressbuch (saga.fed_dir) - Chats ueber Servergrenzen folgen mit Protokoll 2
    remote = sharing.DIR_SEARCH(uid, q, "chat") if sharing.DIR_SEARCH is not None else []
    # 2.4 (W4): Kennung = pseudonymer Handle (POST /api/chats nimmt ihn an), Name ohne Anmeldename
    users = [{"username": local_ref(r[0]), "name": lb.get(r[0]) or local_ref(r[0]), "sub": sb.get(r[0], ""), "ready": bool(r[2]),
              "avatar": av.get(r[0])} for r in rows]
    have = {x["ref"] for x in kn}
    users = [dict(username=k["ref"], name=k["name"], sub=k["sub"], ready=k["ready"], avatar=k["avatar"], contact=True)
             for k in kn if not k["remote"]] + [u for u in users if u["username"] not in have]
    remote = [{"type": "remote", "target": "@" + k["ref"], "handle": k["ref"], "name": k["name"], "host": k["host"], "pub": None,
               "label": f"{k['name']} · {k['host']}", "avatar": k["avatar"], "contact": True} for k in kn if k["remote"]] + \
             [x for x in remote if x.get("handle") not in have]
    return _no_store({"users": users,
                      "groups": [{"name": g} for g in mine if (q in g if len(q) >= sharing.SHORT_Q else q == g)][:10],
                      "remote": remote})


@router.get("/api/chat/contacts")
def api_contacts(request: Request, q: str = "", limit: int = 50):
    """2.5 (contacts1): eigene Kontakte - Personen aus Direkt-Chats und freien Gruppen, auch nach Loeschen/Verlassen des
    Chats. Zuletzt zuerst; q filtert (Teilwort, jede Laenge). Felder: ref (Kennung fuer POST /api/chats: lokal 'user', fremd
    'handle'), name, sub, avatar, remote, host (nur fremd), ready, gone, last_at. Nie Anmeldenamen."""
    uid, err = _me(request)
    if err:
        return err
    if not _rate("contacts", uid, 60):
        return _bad(t("zu schnell"), 429)
    with _db() as c:
        out = contacts.entries(c, uid, q, limit=max(1, min(int(limit or 50), contacts.LIST_MAX)))
    return _no_store({"contacts": out})


@router.delete("/api/chat/contacts/{ref:path}")
def api_contact_hide(ref: str, request: Request):
    """2.5: "Aus Kontakten entfernen" (ausgeblendet; ein neuer gemeinsamer Chat macht wieder sichtbar). Unbekannt -> 404."""
    uid, err = _me(request)
    if err:
        return err
    if not _rate("contacts", uid, 60):
        return _bad(t("zu schnell"), 429)
    ref = str(ref or "").strip().lower()[:300]
    with _db() as c, c:
        if not contacts.hide(c, uid, ref):
            return _bad("not found", 404)
        _direct(c, [uid], "chat.contacts", {"ref": ref, "hidden": True})    # andere eigene Geraete: Liste neu laden
    return _no_store({"ok": True})


def _note_info(c, uid, ids):
    """Titel/Icon nur fuer Seiten, die uid JETZT sehen darf."""
    acc = Access(c, uid)
    ok = [i for i in ids if acc.role(i)]
    if not ok:
        return {}
    return {r[0]: {"id": r[0], "title": r[1] or t("Unbenannt"), "icon": r[2] or ""} for r in c.execute(
        f"SELECT id, title, icon FROM note WHERE deleted_at IS NULL AND id IN ({','.join('?' * len(ok))})", tuple(ok))}


@router.get("/api/chat/notes")
def api_notes(request: Request, ids: str = ""):
    """Seitenverweise in Nachrichten ([[note:ID]]) aufloesen: Titel nur mit eigenem Zugriff, sonst fehlt der Eintrag."""
    uid, err = _me(request)
    if err:
        return err
    try:
        want = sorted({int(x) for x in ids.split(",") if x.strip()})[:100]
    except ValueError:
        return _bad(t("ungültige Angabe"))
    want = [i for i in want if 0 < i < 2 ** 63]
    with _db() as c:
        return _no_store({"notes": {str(k): v for k, v in _note_info(c, uid, want).items()}})


@router.get("/api/chat/pages")
def api_pages(request: Request, q: str = ""):
    """@-Verweis: eigene und mit mir geteilte Seiten, deren Titel q enthaelt (hoechstens 10)."""
    uid, err = _me(request)
    if err:
        return err
    q = (q or "").strip().lower()[:80]
    with _db() as c:
        acc = Access(c, uid)
        rows = c.execute("SELECT id, title, icon, uid FROM note WHERE deleted_at IS NULL AND archived IS NULL AND is_template=0 "
                         "AND lower(title) LIKE ? ORDER BY (uid=?) DESC, updated DESC LIMIT 60", ("%" + _like(q) + "%", uid)).fetchall()
        out = [{"id": r[0], "title": r[1] or t("Unbenannt"), "icon": r[2] or ""} for r in rows if acc.role(r[0])][:10]
    return _no_store({"pages": out})


# --- Chats ------------------------------------------------------------------------------------------------------
M_COLS = "m.seq, m.id, m.kind, m.sender_uid, m.sender_device, m.ts, m.created, m.expires_at, m.body_ct, m.sig, m.reply_to"


def _my_device(c, uid, device):
    """Eigenes aktives Geraet (Anfrage-Parameter device) oder None; merkt 'zuletzt gesehen' (hoechstens jede Minute)."""
    if not device or not _HEX32.fullmatch(device):
        return None
    r = c.execute("SELECT last_seen, caps FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (device, uid)).fetchone()
    if not r:
        return None
    if (r[0] or 0) < time.time() - 60:
        c.execute("UPDATE chat_device SET last_seen=? WHERE id=?", (time.time(), device))
    if CHAT_LEVEL.get() >= 3 and '"chat3"' not in (r[1] or ""):    # 2.5: Geraet kann Stufe 3 (fuer den Umstellauftrag)
        try:
            caps = [x for x in json.loads(r[1] or "[]") if isinstance(x, str)][:15]
        except ValueError:
            caps = []
        c.execute("UPDATE chat_device SET caps=? WHERE id=?", (json.dumps(caps + ["chat3"], separators=(",", ":")), device))
    return device


def _msgs(c, uid, cid, device, where="", args=(), limit=PAGE, desc=True):
    """Nachrichten mit dem fuer mich versiegelten Schluessel (Geraet, sonst Backup). Nur, was fuer mich verschluesselt ist -
    dazu Anruf-Eintraege (kind='call', Metadaten ohne Schluessel; nur wenn where sie zulaesst). Nichts bis zur Grenze
    "fuer mich geloescht" (chat_pref.cleared_seq; der Name einer freien Gruppe, kind='title', bleibt) und ohne die fuer mich
    ausgeblendeten (2.5, chat_hidden)."""
    bk = "backup:" + uid
    dv = device or "-"
    rows = c.execute(
        f"SELECT {M_COLS}, (SELECT sealed FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient=?), "
        "(SELECT sealed FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient=?), m.fseq, m.sender_handle, m.ai, m.fstate FROM chat_msg m "
        "WHERE m.chat_id=? AND (m.expires_at IS NULL OR m.expires_at>?) AND (m.kind IN ('call','sys') OR EXISTS (SELECT 1 FROM chat_msg_key k "
        "WHERE k.msg_seq=m.seq AND k.recipient IN (?,?))) AND (m.kind='title' OR m.seq > COALESCE((SELECT p.cleared_seq FROM chat_pref p "
        f"WHERE p.chat_id=m.chat_id AND p.uid=?), 0)) AND NOT EXISTS (SELECT 1 FROM chat_hidden h WHERE h.uid=? AND h.msg_seq=m.seq) "
        f"{where} ORDER BY m.seq {'DESC' if desc else 'ASC'} LIMIT ?",
        (dv, bk, cid, time.time(), dv, bk, uid, uid, *args, limit)).fetchall()
    return rows


def _msg_dict(r, nm):
    key, via = (r[11], "device") if r[11] else (r[12], "backup") if r[12] else (None, None)
    return {"seq": r[0], "id": r[1], "kind": r[2], "sender": {"username": r[3], "name": nm.get(r[3]) or t("Gelöschtes Konto"),
                                                         "avatar": nm.avatar(r[3]) if hasattr(nm, "avatar") else None} if r[3] else None,
            "sender_device": r[4], "ts": r[5], "created": r[6], "expires_at": r[7], "body_ct": r[8], "sig": r[9],
            "reply_to": r[10], "key": key, "key_for": via,
            **({"fseq": r[13], "sender_handle": r[14], "ai": r[15], "fstate": r[16]} if len(r) > 16 and (r[14] or r[2] == "sys") else {})}


def ai_marks(c, cid, msgs):
    """KI-Antworten (2.1) kennzeichnen: ai = {ref, sha256, by} - die Clients vergleichen sha256 mit dem entschluesselten Text.
    Gilt nur, wenn der Absender der Nachricht auch die Antwort angefordert hat. Nach einer Kontoloeschung ist der Beleg
    anonymisiert (uid ''): er gilt dann nur fuer eine Nachricht ohne Absender, by = None."""
    ids = [m["id"] for m in msgs if m.get("kind") == "msg"]
    if not ids:
        return
    got = {r[0]: r for r in c.execute(f"SELECT msg_id, id, sha256, uid, q_msg FROM chat_ai_reply WHERE chat_id=? AND msg_id IN "
                                      f"({','.join('?' * len(ids))})", (cid, *ids))}
    for m in msgs:
        r = got.get(m["id"])
        if not r:
            continue
        if m.get("sender") and m["sender"]["username"] == r[3]:
            m["ai"] = {"ref": r[1], "sha256": r[2], "by": r[3], "q_msg": r[4]}
        elif r[3] == AI_GONE and not m.get("sender"):
            m["ai"] = {"ref": r[1], "sha256": r[2], "by": None, "q_msg": r[4]}


def _senders(c, rows):
    """Geraete der Absender (auch entfernte) + aktuelle Identitaetsschluessel - zur Pruefung der Signaturen im Client."""
    dids = sorted({r[4] for r in rows if r[4]})
    devs, uids = {}, set()
    if dids:
        for r in c.execute(f"SELECT id, uid, enc_pub, sign_pub, sig, revoked_at FROM chat_device WHERE id IN ({','.join('?' * len(dids))})",
                           tuple(dids)):
            devs[r[0]] = {"uid": r[1], "enc_pub": r[2], "sign_pub": r[3], "sig": r[4], "revoked": bool(r[5])}
            uids.add(r[1])
    ids = {}
    if uids:
        ids = {r[0]: r[1] for r in c.execute(f"SELECT uid, ident_pub FROM chat_identity WHERE uid IN ({','.join('?' * len(uids))})",
                                             tuple(sorted(uids)))}
    return devs, ids


def _name(c, uid, ch, mem, nm):
    """Anzeigename: dm = Gegenueber, group = Gruppenname, page = Seitentitel (mit Zugriff), free = None (verschluesselt)."""
    if ch["kind"] == "dm":
        if is_fed(ch):
            other = next((u for u in mem if sharing.is_remote(u)), None)
            return FED.label_of(c, other)["name"] if other else t("Gelöschtes Konto")
        a, _, b = (ch["dm_key"] or "").partition("|")
        other = b if a == uid else a
        return nm.get(other) or t("Gelöschtes Konto")
    if ch["kind"] == "group":
        return ch["group_name"]
    if ch["kind"] == "page":
        n = _note_info(c, uid, [ch["note_id"]]).get(ch["note_id"])
        return n["title"] if n else t("Seite")
    return None


def _unread(c, uid, cid, last_read):
    return c.execute("SELECT COUNT(*) FROM chat_msg m WHERE m.chat_id=? AND m.kind='msg' AND m.seq>? AND (m.sender_uid IS NULL OR m.sender_uid<>?) "
                     "AND (m.expires_at IS NULL OR m.expires_at>?) AND NOT EXISTS (SELECT 1 FROM chat_hidden h WHERE h.uid=? AND h.msg_seq=m.seq)",
                     (cid, last_read, uid, time.time(), uid)).fetchone()[0]


def _chat_dict(c, uid, ch, mem, m, device, full=False):
    free = ch["kind"] == "free"
    adm = {r[0] for r in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'", (ch["id"],))} if free else set()
    nm = names(c, list(mem) + ([ch["dm_key"].split("|")[0], ch["dm_key"].split("|")[-1]] if ch["dm_key"] else []))
    sb = subtitles(c, [u for u in mem if u != uid]) if ch["kind"] != "dm" else {}    # 2.4 (N2): Unterzeile fuer Gleichnamige
    d = {"id": ch["id"], "kind": ch["kind"], "name": _name(c, uid, ch, mem, nm), "group": ch["group_name"], "note_id": ch["note_id"],
         "created": ch["created"], "last_at": ch["last_at"] or ch["created"], "last_seq": ch["last_seq"], "retention_s": ch["retention_s"],
         "muted": bool(m["muted"]), "role": m["role"], "last_read": m["last_read"], "unread": _unread(c, uid, ch["id"], m["last_read"]),
         # username: Chats v1 binden Schluessel an den Anmeldenamen (nur Mitglieder sehen ihn, die Oberflaeche zeigt ihn nicht);
         # ref (2.4): dieselbe Kennung wie in der Personensuche (pseudonymer Handle)
         # admin (2.5): Admin der freien Gruppe (group/page: immer false)
         "members": [{"username": u, "ref": u if (u == uid or u.startswith("@")) else local_ref(u), "name": nm.get(u, u), "me": u == uid,
                      "avatar": nm.avatar(u), "sub": sb.get(u, ""), "admin": u in adm} for u in mem], "member_count": len(mem),
         # KI im Chat (2.1, saga/ai_chat.py): in diesem Chat erlaubt? (ob der Server sie anbietet: /api/ai/status "chat")
         "ai": bool(c.execute("SELECT 1 FROM chat_ai WHERE chat_id=?", (ch["id"],)).fetchone())}
    # Archiv und Loeschen (2.1): nur fuer mich; delete_for_all = 'chat' | 'history' | None (was "Fuer alle loeschen" darf)
    p = _pref(c, ch["id"], uid)
    d.update(archived=p["archived"] is not None, archived_at=p["archived"], cleared_seq=p["cleared_seq"],
             delete_for_all=may_delete_all(c, uid, ch, m))
    # Gruppeninfo (2.5, chatadm1): me_admin = ich bin Admin der freien Gruppe; can_manage = Mitglieder hinzufuegen/entfernen,
    # can_admin = Admin vergeben/entziehen (PATCH .../members/{who}), can_rename = Namen setzen (kind 'title'). Foederiert
    # schraenkt saga.fed_chat ein (schreibgeschuetzt; Heim ohne chatadm1 -> can_admin false).
    me_admin = free and m["role"] == "admin"
    d.update(me_admin=me_admin, can_manage=me_admin, can_admin=me_admin, can_rename=free)
    if ch["kind"] == "dm":
        a, _, b = (ch["dm_key"] or "").partition("|")   # (2.3: alter foederierter Chat ohne Paar)
        d["peer"] = {"username": b if a == uid else a, "name": d["name"], "avatar": nm.avatar(b if a == uid else a)}
    if ch["kind"] == "free" and ch["title_seq"]:
        rows = _msgs(c, uid, ch["id"], device, "AND m.seq=?", (ch["title_seq"],), 1)
        if rows:
            nmt = names(c, [rows[0][3]])
            devs, ids = _senders(c, rows)
            d["title_msg"] = dict(_msg_dict(rows[0], nmt), device=devs.get(rows[0][4]), ident_pub=ids.get(rows[0][3]))
    if full:
        d["max_members"] = MEMBERS_MAX
        if CALLS is not None:                         # 2.4: Gruppenanruf moeglich? laufender Anruf in diesem Chat?
            CALLS.chat_extra(c, uid, ch, d)
    if not hfmt(ch) and FED is not None and CHAT_LEVEL.get() >= 3:
        for x in d["members"]:                        # 2.5: Handles im Chat v1 - fuer die Vertrauens-Uebernahme der Apps
            if not sharing.is_remote(x["username"]):
                x["handle"] = FED.my_handle(c, x["username"])
    if ch.get("v3_seq") is not None:                  # 2.5: umgestellt - Nachrichten bis v3_seq im Format v1
        d["v3"] = {"seq": ch["v3_seq"], "at": ch["v3_at"], "forced": bool(ch["v3_forced"])}
    if not hfmt(ch) and X3 is not None:               # 2.5: Chat v1 - keine Reaktionen; x3_why sagt warum
        X3.v1_flags(c, uid, ch, mem, d)
    if hfmt(ch):                                      # 2.3: Chat ueber Servergrenzen (saga.fed_chat); 2.5: Stufe 3 ebenso
        FED.chat_extra(c, uid, ch, d, mem, m)
        if ch.get("v3_forced") and CHAT_LEVEL.get() < 3 and time.time() - (ch.get("v3_at") or 0) < VERIFY_HINT_S:
            d["name"] = (d.get("name") or "") + " · " + t("Sicherheitsnummer bitte neu prüfen")     # aeltere Apps: deutlich im Namen
        if not V2_CLIENT.get():                       # aeltere App: Hinweis statt kaputter Nachrichten
            d["name"] = (d.get("name") or "") + " · " + t("bitte App aktualisieren")
            d["fed_hint"] = v3_hint() if is_v3(ch) else fed_hint()
    return d


def _candidates(c, uid):
    """Gruppen- und Seiten-Chats, zu denen uid gehoeren koennte (noch ohne Mitgliedschaft)."""
    out = []
    gs = sorted(groups_of(c, uid))
    if gs:
        out += [r[0] for r in c.execute(f"SELECT id FROM chat WHERE kind='group' AND group_name IN ({','.join('?' * len(gs))})", tuple(gs))]
    acc = Access(c, uid)
    for cid, nid in c.execute("SELECT id, note_id FROM chat WHERE kind='page' AND id NOT IN (SELECT chat_id FROM chat_member "
                              "WHERE uid=? AND left_at IS NULL) LIMIT 2000", (uid,)).fetchall():
        if acc.role(nid):
            out.append(cid)
    return out


@router.get("/api/chats")
def api_chats(request: Request, device: str = ""):
    """Meine Chats (neueste zuerst) mit Ungelesen-Zahl; Gruppen-/Seiten-Chats werden dabei mit Gruppe/Freigaben abgeglichen.
    Archivierte Chats sind dabei (archived=true), zaehlen aber nicht zu unread; "fuer mich geloeschte" Direkt-Chats fehlen,
    bis eine neue Nachricht kommt."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        dev = _my_device(c, uid, device)
        mine = [r[0] for r in c.execute("SELECT m.chat_id FROM chat_member m JOIN chat ch ON ch.id=m.chat_id WHERE m.uid=? "
                                        "AND m.left_at IS NULL AND ch.kind IN ('group','page')", (uid,))]
        for cid in sorted(set(mine) | set(_candidates(c, uid))):
            ch = _load(c, cid)
            if ch:
                sync(c, ch)
        out = []
        for r in c.execute(f"SELECT {', '.join('ch.' + x.strip() for x in C_COLS.split(','))} FROM chat ch JOIN chat_member m "
                           "ON m.chat_id=ch.id LEFT JOIN chat_pref p ON p.chat_id=ch.id AND p.uid=m.uid WHERE m.uid=? AND m.left_at IS NULL AND m.state<>'declined' "
                           "AND NOT (COALESCE(p.hidden, 0)<>0 AND ch.last_seq<=p.cleared_seq) "
                           "ORDER BY COALESCE(ch.last_at, ch.created) DESC LIMIT 500", (uid,)).fetchall():
            ch = _row(r)
            out.append(_chat_dict(c, uid, ch, members(c, ch["id"]), _member(c, ch["id"], uid), dev))
    return _no_store({"chats": out, "unread": sum(x["unread"] for x in out if not x["muted"] and not x["archived"] and not x.get("request")),
                      "requests": sum(1 for x in out if x.get("request"))})


def unread_total(c, uid):
    """Ungelesene Nachrichten in nicht stummgeschalteten, nicht archivierten Chats (Knopf "Chats" auf Dashboard/Seiten)."""
    if not enabled():
        return 0
    return c.execute(
        "SELECT COUNT(*) FROM chat_msg x JOIN chat_member m ON m.chat_id=x.chat_id AND m.uid=? AND m.left_at IS NULL AND m.muted=0 AND m.state='ok' "
        "WHERE x.kind='msg' AND x.seq>m.last_read AND (x.sender_uid IS NULL OR x.sender_uid<>?) AND (x.expires_at IS NULL OR x.expires_at>?) "
        "AND NOT EXISTS (SELECT 1 FROM chat_pref p WHERE p.chat_id=x.chat_id AND p.uid=m.uid AND p.archived IS NOT NULL)",
        (uid, uid, time.time())).fetchone()[0]


@router.get("/api/chats/unread")
def api_unread(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        return _no_store({"enabled": enabled(), "unread": unread_total(c, uid)})


@router.post("/api/chats")
async def api_chat_new(request: Request):
    """Neuer Chat: {kind:'dm', user} | {kind:'group', group} | {kind:'page', note_id} | {kind:'free', members:[...]}.
    dm/group/page gibt es je Paar/Gruppe/Seite nur einmal - dann kommt der vorhandene zurueck."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    kind, now = b.get("kind"), time.time()
    if kind == "dm" and b.get("handle") and FED is not None:         # 2.3: Person eines anderen Servers (Handle '~x@host')
        hn, _, hh = str(b.get("handle")).strip().lower().partition("@")
        with _db() as c:
            lu = FED.F.uid_by_handle(c, hn) if hh and FED.fedsig.authority(hh) == FED.F.SELF_HOST else None
        if not lu:
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(FED.create_dm, uid, b.get("handle"), str(b.get("device") or ""))
        b["user"] = lu                                                # eigener Server: wie bisher (lokaler Chat, v1)
    if FED is not None and kind == "free" and b.get("handles"):       # 2.3: freie Gruppe mit Personen anderer Server
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(FED.create_group, uid, b)
    if FED is not None and kind == "page":
        from starlette.concurrency import run_in_threadpool
        try:
            pnid = int(b.get("note_id") or 0)
        except (TypeError, ValueError):
            pnid = 0
        if pnid < 0:                                                  # 2.3: Seiten-Chat einer Seite eines anderen Servers
            return await run_in_threadpool(FED.open_remote_page_chat, uid, pnid, str(b.get("device") or ""))
        if pnid > 0:
            await run_in_threadpool(FED.prepare_page, pnid)          # Personen/Gruppen anderer Server vorher aufloesen (Netzwerk)
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM chat WHERE created_by=? AND created>?", (uid, now - 3600)).fetchone()[0] >= 60:
            return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
        ch = None
        if kind == "dm":
            other = resolve_input(c, uid, str(b.get("user") or "")[:100])  # 2.4: Handle ('~…'); Benutzername nur bekannt/erlaubt
            if not may_contact(c, uid, other):
                return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
            key = "|".join(sorted([uid, other]))
            ch = _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE dm_key=?", (key,)).fetchone())
            if not ch:
                cid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm',?,?,?)", (key, uid, now)).lastrowid
                for u in (uid, other):
                    c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
                ch = make_v3(c, _load(c, cid), [uid, other])     # 2.5: Stufe 3, wenn eingeschaltet und beide bereit
                _changed(c, cid)
        elif kind == "group":
            g = auth.norm_group(str(b.get("group") or "")) or ""
            if g not in groups_of(c, uid):
                return _bad(t("Gruppen-Chats gibt es nur für Gruppen, in denen du selbst bist."), 403)
            ch = _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE kind='group' AND group_name=?", (g,)).fetchone())
            if not ch:
                if len(notify.group_members(c, g, MEMBERS_MAX + 1)) > MEMBERS_MAX:
                    return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
                cid = c.execute("INSERT INTO chat (kind, group_name, created_by, created) VALUES ('group',?,?,?)", (g, uid, now)).lastrowid
                ch = make_v3(c, _load(c, cid), notify.group_members(c, g))
        elif kind == "page":
            try:
                nid = int(b.get("note_id") or 0)
            except (TypeError, ValueError):
                nid = 0
            if not (0 < nid < 2 ** 63) or not Access(c, uid).role(nid) or \
                    not c.execute("SELECT 1 FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone():
                return _bad("not found", 404)
            ch = _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE kind='page' AND note_id=?", (nid,)).fetchone())
            if not ch:
                if len(page_members(c, nid)) > MEMBERS_MAX:
                    return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
                cid = c.execute("INSERT INTO chat (kind, note_id, created_by, created) VALUES ('page',?,?,?)", (nid, uid, now)).lastrowid
                ch = make_v3(c, _load(c, cid), page_members(c, nid))
        elif kind == "free":
            want = b.get("members")
            if not isinstance(want, list) or len(want) > MEMBERS_MAX:
                return _bad(t("ungültige Angabe"))
            want = sorted({resolve_input(c, uid, str(x)[:100]) or "~" for x in want} - {uid})
            if len(want) + 1 > MEMBERS_MAX:
                return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
            bad = [u for u in want if not may_contact(c, uid, u)]
            if bad or not want:
                return _bad(t("Mit diesem Konto ist kein Chat möglich.") if bad else t("ungültige Angabe"), 400)
            cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free',?,?)", (uid, now)).lastrowid
            c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,'admin',?)", (cid, uid, now))
            for u in want:
                c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
            ch = make_v3(c, _load(c, cid), [uid, *want])
            _changed(c, cid)
        else:
            return _bad(t("ungültige Angabe"))
        if kind in ("dm", "free"):
            contacts.link(c, ch["id"])                   # 2.5: Kontakte gegenseitig (auch wieder sichtbar)
        mem = sync(c, ch)
        m = _member(c, ch["id"], uid)
        if not m:
            return _bad("not found", 404)
        # "fuer mich geloeschter" Direkt-Chat wird bewusst wieder geoeffnet: zurueck in die Liste (leer)
        c.execute("UPDATE chat_pref SET hidden=0 WHERE chat_id=? AND uid=? AND hidden<>0", (ch["id"], uid))
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, m, _my_device(c, uid, str(b.get("device") or "")), full=True)})


@router.get("/api/chats/{cid}")
def api_chat(cid: int, request: Request, device: str = ""):
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, m, _my_device(c, uid, device), full=True)})


@router.patch("/api/chats/{cid}")
async def api_chat_patch(cid: int, request: Request):
    """{muted: bool} / {archived: bool} (nur fuer mich) und/oder {retention_s: 0|3600|...} (fuer alle; gilt fuer neue
    Nachrichten). Archivierte Chats kommen bei einer neuen Nachricht von selbst zurueck (ausser stummgeschaltete)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    if "archived" in b and not isinstance(b["archived"], bool):
        return _bad(t("ungültige Angabe"))
    fed_retention, wait = None, False
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if "muted" in b:
            c.execute("UPDATE chat_member SET muted=? WHERE chat_id=? AND uid=?", (1 if b["muted"] else 0, ch["id"], uid))
            _direct(c, [uid], "chat.changed", {"chat_id": ch["id"]})
        if "archived" in b:
            c.execute("INSERT INTO chat_pref (chat_id, uid, archived) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE "
                      "SET archived=EXCLUDED.archived", (ch["id"], uid, time.time() if b["archived"] else None))
            _direct(c, [uid], "chat.changed", {"chat_id": ch["id"], "archived": b["archived"]})
        if "retention_s" in b:
            v = b["retention_s"]
            if not isinstance(v, int) or isinstance(v, bool) or v not in RETENTIONS:
                return _bad(t("ungültige Angabe"))
            mx = retention_max()
            if mx and (v == 0 or v > mx):
                return _bad(t("Höchstens {n} Tage (Vorgabe des Servers).", n=mx // 86400))
            if is_fed(ch):
                fed_retention = v                    # beim Heim (Netzwerk) - nach dieser Transaktion
            else:
                c.execute("UPDATE chat SET retention_s=? WHERE id=?", (v, ch["id"]))
                _changed(c, ch["id"], mem)
    if fed_retention is not None:
        from starlette.concurrency import run_in_threadpool
        e, wait = await run_in_threadpool(FED.set_meta, uid, ch, {"retention_s": fed_retention})
        if e is not None:
            return e
    with _db() as c, c:
        ch = _load(c, ch["id"])
        out = _chat_dict(c, uid, ch, mem if fed_retention is None else members(c, ch["id"]), _member(c, ch["id"], uid), None, full=True)
        if fed_retention is not None and wait:
            out["pending"] = True
        return _no_store({"chat": out})


@router.post("/api/chats/{cid}/members")
async def api_member_add(cid: int, request: Request):
    """Freie Gruppe: Person hinzufuegen {user} (nur Ersteller/Admin). Sieht nur Nachrichten ab jetzt."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    who = str(b.get("user") or "").strip().lower()[:100]
    if who and not b.get("handle"):                       # 2.4: Handle aus der Personensuche; Benutzername nur bekannt/erlaubt
        with _db() as c:
            who = who_in(c, uid, who)                     # 2.5: auch der Handle '~x@host' aus members[] (Stufe 3)
    if FED is not None:
        with _db() as c:
            fch = _load(c, cid)
            if is_v3(fch) and b.get("handle"):            # 2.5: Stufe 3 - Handle eines Kontos dieses Servers wie {user}
                m_ = _FULL_H.fullmatch(str(b.get("handle")).strip().lower())
                if m_ and FED.fedsig.authority(m_.group(2)) != FED.F.SELF_HOST:
                    from starlette.concurrency import run_in_threadpool   # Person eines anderen Servers: Gruppe wird foederiert
                    return await run_in_threadpool(FED.open_v3, uid, cid, str(b.get("handle")))
                who = who_in(c, uid, str(b.get("handle"))) or "~"
                b = dict(b, handle=None) if who != "~" else b
        if is_fed(fch) or b.get("handle"):                # 2.3: Gruppe ueber Servergrenzen (Aenderung ueber das Heim)
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(FED.group_add, uid, cid, who, str(b.get("handle") or ""))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if ch["kind"] != "free":
            return _bad(not_free_msg(), 400)
        if m["role"] != "admin":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if who in mem:
            return _bad(t("Ist schon Mitglied."), 409)
        if not may_contact(c, uid, who):
            return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
        if len(mem) >= MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
        c.execute("INSERT INTO chat_member (chat_id, uid, joined, last_read) VALUES (?,?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE "
                  "SET left_at=NULL, role='member', joined=EXCLUDED.joined, last_read=EXCLUDED.last_read", (ch["id"], who, time.time(), ch["last_seq"]))
        contacts.link(c, ch["id"], who=[who])             # 2.5: neues Mitglied <-> alle anderen
        _changed(c, ch["id"], set(mem) | {who})
        mem = members(c, ch["id"])
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, m, None, full=True)})


def member_target(c, uid, mem, who):
    """Person an den Mitglieder-Endpunkten (2.5) -> Mitglied (Benutzername bzw. Prinzipal '@~x@host') oder None. Nimmt den
    Wert aus members[].username (v1: Benutzername; Format v2: Handle bzw. Prinzipal) sowie Handles '~x' / '~x@host'."""
    who = str(who or "").strip()[:300]
    if who in mem:
        return who
    w = who.lower()
    if w in mem:
        return w
    m_ = _FULL_H.fullmatch(w)
    if m_ and FED is not None and FED.fedsig.authority(m_.group(2)) != FED.F.SELF_HOST:
        u = "@" + m_.group(1) + "@" + FED.fedsig.authority(m_.group(2))
        return u if u in mem else None
    u = who_in(c, uid, w)
    return u if u and u in mem else None


def set_admin(c, ch, u, on):
    """Rolle eines Mitglieds der freien Gruppe setzen (2.5). Sperrt die Chat-Zeile (zwei gleichzeitige Entzuege lassen
    nicht null Admins zurueck). -> 'ok' (geaendert) | 'same' (war schon so) | 'last' (letzter Admin) | 'gone'."""
    c.execute("SELECT id FROM chat WHERE id=? FOR UPDATE", (ch["id"],))
    r = c.execute("SELECT role FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (ch["id"], u)).fetchone()
    if not r:
        return "gone"
    if (r[0] == "admin") == bool(on):
        return "same"
    if not on and c.execute("SELECT COUNT(*) FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'",
                            (ch["id"],)).fetchone()[0] <= 1:
        return "last"
    c.execute("UPDATE chat_member SET role=? WHERE chat_id=? AND uid=?", ("admin" if on else "member", ch["id"], u))
    return "ok"


def last_admin_msg():
    return t("Das ist der letzte Admin der Gruppe – erst jemand anderen zum Admin machen.")


def not_free_msg():
    return t("Mitglieder ergeben sich hier aus der Gruppe bzw. den Freigaben der Seite.")


@router.patch("/api/chats/{cid}/members/{who}")
async def api_member_role(cid: int, who: str, request: Request):
    """Freie Gruppe (2.5, chatadm1): Admin vergeben/entziehen {admin: true|false} - nur Admins. Den letzten Admin entziehen
    -> 409; sich selbst entziehen geht, wenn ein anderer Admin bleibt. Antwort {chat} wie POST .../members."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None or not isinstance(b.get("admin"), bool):
        return _bad(t("ungültige Angabe"))
    on = b["admin"]
    if FED is not None:
        with _db() as c:
            fch = _load(c, cid)
        if is_fed(fch):                                   # Gruppe ueber Servergrenzen: Aenderung ueber das Heim
            from starlette.concurrency import run_in_threadpool
            return await run_in_threadpool(FED.group_admin, uid, cid, who, on)
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if ch["kind"] != "free":
            return _bad(not_free_msg(), 400)
        if m["role"] != "admin":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        tgt = member_target(c, uid, mem, who)
        if not tgt:
            return _bad("not found", 404)
        r = set_admin(c, ch, tgt, on)
        if r == "last":
            return _bad(last_admin_msg(), 409)
        if r == "gone":
            return _bad("not found", 404)
        if r == "ok":
            _changed(c, ch["id"], mem)
            if hfmt(ch):                                  # Systemzeile (kind 'sys', nur Handles/Namen) im Format v2/Stufe 3 -
                # Chats v1 kennen keine Systemzeilen (wie beim Hinzufuegen/Entfernen); dort reicht chat.changed
                FED.sysline(c, ch, None, {"type": "admin", "target": FED.who(c, tgt), "on": on, "by": FED.who(c, uid)})
        mem = members(c, ch["id"])
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, _member(c, ch["id"], uid), None, full=True)})


def _drop_chat(c, cid):
    """Chat ganz loeschen -> Datei-ids (Blobs nach dem Commit entfernen)."""
    fids = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE chat_id=?", (cid,))]
    c.execute("DELETE FROM chat_file WHERE chat_id=?", (cid,))
    c.execute("DELETE FROM chat WHERE id=?", (cid,))              # Mitglieder, Nachrichten, Schluessel: ON DELETE CASCADE
    return fids


def _rm_blobs(c, fids):
    def go():
        for f in fids:
            for d in (CHAT_DIR, FED_DIR):              # 2.4: auch lueckenhafte Zwischenkopien
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass
    if fids:
        c.after_commit(go)


def _rm_uploads(c, ids):
    """Angefangene Uploads (2.4): Teil-Dateien nach dem Commit entfernen."""
    def go():
        for u in ids:
            try:
                os.remove(os.path.join(UP_DIR, u))
            except OSError:
                pass
    if ids:
        c.after_commit(go)


@router.delete("/api/chats/{cid}/members/{who}")
def api_member_remove(cid: int, who: str, request: Request):
    """Freie Gruppe verlassen (who = ich) bzw. Person entfernen (Admin). Letzte Person geht -> Chat geloescht."""
    uid, err = _me(request)
    if err:
        return err
    who = who.strip().lower()
    if FED is not None:
        with _db() as c:
            fch = _load(c, cid)
            if hfmt(fch) and _FULL_H.fullmatch(who):     # 2.5: Format v2 nennt andere Mitglieder per Handle
                who = who_in(c, uid, who) or "~"
        if is_fed(fch):                                   # 2.3: Gruppe ueber Servergrenzen (Aenderung ueber das Heim)
            return FED.group_remove(uid, cid, who)
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if ch["kind"] != "free":
            return _bad(t("Diesen Chat kann man nicht verlassen – nur stummschalten."), 400)
        if who != uid and m["role"] != "admin":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if who not in mem:
            return _bad("not found", 404)
        c.execute("UPDATE chat_member SET left_at=?, role='member' WHERE chat_id=? AND uid=?", (time.time(), ch["id"], who))
        unhide(c, ch["id"], who)
        rest = members(c, ch["id"])
        if not rest:
            _rm_blobs(c, _drop_chat(c, ch["id"]))
        elif not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'", (ch["id"],)).fetchone():
            c.execute("UPDATE chat_member SET role='admin' WHERE chat_id=? AND uid=?", (ch["id"], rest[0]))
        _changed(c, ch["id"], set(mem))
    return _no_store({"ok": True})


# --- Chat loeschen (2.1): "fuer mich" oder "fuer alle" -----------------------------------------------------------
def _drop_notifs(c, uids, cid):
    """Benachrichtigungen dieses Chats (neue Nachricht, verpasster Anruf) fuer diese Konten entfernen."""
    pat = '{"chat": %d, %%' % cid
    for u in sorted(set(uids)):
        ids = [r[0] for r in c.execute("SELECT id FROM notification WHERE uid=? AND kind IN ('chat.message','call.missed') AND data LIKE ?",
                                       (u, pat))]
        if ids:
            c.execute(f"DELETE FROM notification WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
            bus.emit_after(c, "notification.read", {"uid": u, "ids": ids, "unread": notify.count_unread(c, u)})


def _floor(c, cid):
    """Grenze fuer "geloescht bis": sperrt die Chat-Zeile (FOR UPDATE wartet auf laufende Sende-Transaktionen, die per
    Fremdschluessel FOR KEY SHARE halten - deren Nachricht ist danach sichtbar und faellt nicht unbemerkt unter die
    Grenze) und nimmt das Groessere aus neuester Nachricht und chat.last_seq (die letzte Nachricht kann geloescht sein)."""
    last = c.execute("SELECT last_seq FROM chat WHERE id=? FOR UPDATE", (cid,)).fetchone()
    top = c.execute("SELECT COALESCE(MAX(seq), 0) FROM chat_msg WHERE chat_id=?", (cid,)).fetchone()[0]
    return max(top, (last[0] or 0) if last else 0)


def clear_for(c, uid, ch):
    """"Fuer mich loeschen": Grenze cleared_seq = neueste Nachricht; die fuer uid versiegelten Inhaltsschluessel (alle
    eigenen Geraete + backup:<uid>) dieser Nachrichten werden geloescht - der Server liefert sie nie mehr aus, und selbst
    eine alte Kopie des Ciphertexts laesst sich mit dem Konto nicht mehr oeffnen. Die Nachrichten der anderen bleiben.
    Direkt-Chat: aus der Liste, bis eine neue Nachricht kommt; sonst archiviert. Der Name einer freien Gruppe (kind
    'title') bleibt lesbar. -> (cleared_seq, Anzahl entfernter Schluessel)"""
    now = time.time()
    floor = _floor(c, ch["id"])
    rcpt = [r[0] for r in c.execute("SELECT id FROM chat_device WHERE uid=?", (uid,))] + ["backup:" + uid]
    n = c.execute(f"DELETE FROM chat_msg_key WHERE recipient IN ({','.join('?' * len(rcpt))}) AND msg_seq IN "
                  "(SELECT seq FROM chat_msg WHERE chat_id=? AND seq<=? AND kind<>'title')", (*rcpt, ch["id"], floor)).rowcount
    dm = ch["kind"] == "dm"
    c.execute("INSERT INTO chat_pref (chat_id, uid, archived, cleared_seq, cleared_at, hidden) VALUES (?,?,?,?,?,?) ON CONFLICT (chat_id, uid) "
              "DO UPDATE SET archived=EXCLUDED.archived, cleared_seq=GREATEST(chat_pref.cleared_seq, EXCLUDED.cleared_seq), "
              "cleared_at=EXCLUDED.cleared_at, hidden=EXCLUDED.hidden", (ch["id"], uid, None if dm else now, floor, now, 1 if dm else 0))
    c.execute("UPDATE chat_member SET last_read=GREATEST(last_read, ?) WHERE chat_id=? AND uid=?", (ch["last_seq"], ch["id"], uid))
    c.execute("DELETE FROM chat_hidden WHERE chat_id=? AND uid=? AND msg_seq<=?", (ch["id"], uid, floor))   # 2.5: liegt jetzt unter der Grenze
    _drop_notifs(c, [uid], ch["id"])
    return floor, n


def _wipe_history(c, ch):
    """Verlauf fuer alle loeschen (Gruppen-/Seiten-Chat, der Chat selbst bleibt): Nachrichten samt Schluesseln, Anhaenge,
    KI-Belege, beendete Anrufe. -> (Datei-ids, Anzahl Nachrichten)"""
    cid = ch["id"]
    now, top = time.time(), _floor(c, cid)
    # Grenze fuer alle Mitglieder: Clients zeigen "Der bisherige Verlauf wurde geloescht" statt "nicht lesbar"
    c.execute("INSERT INTO chat_pref (chat_id, uid, cleared_seq, cleared_at) SELECT ?, uid, ?, ? FROM chat_member WHERE chat_id=? "
              "AND left_at IS NULL ON CONFLICT (chat_id, uid) DO UPDATE SET cleared_seq=GREATEST(chat_pref.cleared_seq, EXCLUDED.cleared_seq), "
              "cleared_at=EXCLUDED.cleared_at", (cid, top, now, cid))
    fids = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE chat_id=? AND msg_seq IS NOT NULL", (cid,))]
    c.execute("DELETE FROM chat_file WHERE chat_id=? AND msg_seq IS NOT NULL", (cid,))
    c.execute("DELETE FROM chat_ai_reply WHERE chat_id=?", (cid,))
    c.execute("DELETE FROM chat_call WHERE chat_id=? AND state='ended'", (cid,))
    n = c.execute("DELETE FROM chat_msg WHERE chat_id=? AND kind<>'title'", (cid,)).rowcount    # Schluessel: ON DELETE CASCADE
    c.execute("UPDATE chat_member SET last_read=GREATEST(last_read, ?) WHERE chat_id=?", (ch["last_seq"], cid))
    return fids, n


@router.delete("/api/chats/{cid}")
def api_chat_delete(cid: int, request: Request, scope: str = "me"):
    """Chat loeschen. ?scope=me (Standard): Verlauf bis jetzt nur fuer mich (siehe clear_for), jede Chat-Art.
    ?scope=all: Direkt-Chat (beide) bzw. freie Gruppe (Chat-Admins) - Chat samt Nachrichten, Schluesseln, Anhaengen,
    Lesestaenden, KI-Belegen und Anruf-Eintraegen weg, alle Mitglieder bekommen chat.removed {chat_id, by}.
    Gruppen-Chat (Saga-Admins) bzw. Seiten-Chat (Eigentuemer): nur der Verlauf, chat.cleared {chat_id, scope:'all', by}.
    Ohne Recht 403, ohne Mitgliedschaft 404."""
    uid, err = _me(request)
    if err:
        return err
    if scope not in ("me", "all"):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if scope == "me":
            floor, n = clear_for(c, uid, ch)
            _direct(c, [uid], "chat.cleared", {"chat_id": ch["id"], "scope": "me", "cleared_seq": floor})
            print(f"chat: Chat {ch['id']} fuer {uid} geleert ({n} Schluessel entfernt)", flush=True)
            return _no_store({"ok": True, "scope": "me", "removed": False, "cleared_seq": floor,
                              "archived": ch["kind"] != "dm", "hidden": ch["kind"] == "dm"})
        mode = may_delete_all(c, uid, ch, m)
        if not mode:
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if is_fed(ch):
            c.rollback()
            return FED.delete_all(uid, ch)
        by = {"username": uid, "name": names(c, [uid]).get(uid, uid)}
        everyone = set(mem) | set(members(c, ch["id"]))
        _drop_notifs(c, everyone, ch["id"])
        if mode == "chat":
            _rm_blobs(c, _drop_chat(c, ch["id"]))       # Mitglieder, Nachrichten, Schluessel, Anrufe, KI, chat_pref: CASCADE
            direct_person(c, ch, everyone, "chat.removed", {"chat_id": ch["id"], "kind": ch["kind"], "by": by}, uid, "by")
            print(f"chat: Chat {ch['id']} ({ch['kind']}) von {uid} fuer alle geloescht", flush=True)
            return _no_store({"ok": True, "scope": "all", "removed": True})
        fids, n = _wipe_history(c, ch)
        _rm_blobs(c, fids)
        direct_person(c, ch, everyone, "chat.cleared", {"chat_id": ch["id"], "scope": "all", "by": by}, uid, "by")
        print(f"chat: Verlauf von Chat {ch['id']} ({ch['kind']}) von {uid} fuer alle geloescht ({n} Nachrichten)", flush=True)
        return _no_store({"ok": True, "scope": "all", "removed": False, "deleted": n})


def recipients(c, mem):
    """Empfaenger fuer neue Nachrichten: je Mitglied mit Chat-Schluesseln dessen aktive Geraete + Backup-Schluessel."""
    keys = _keys(c, mem)
    nm = names(c, mem)
    out, want = [], set()
    for u in mem:
        k = keys.get(u)
        e = {"username": u, "name": nm.get(u, u), "ready": bool(k), "avatar": nm.avatar(u)}
        if k:
            devs = [d for d in k["devices"] if not d["revoked"]]
            e.update(ident_pub=k["ident_pub"], backup_pub=k["backup_pub"], backup_sig=k["backup_sig"],
                     devices=[{x: d[x] for x in ("id", "enc_pub", "sign_pub", "sig")} for d in devs])
            want.add("backup:" + u)
            want.update(d["id"] for d in devs)
        out.append(e)
    return out, want


@router.get("/api/chats/{cid}/recipients")
def api_recipients(cid: int, request: Request):
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if hfmt(ch):
            if not V2_CLIENT.get():
                return _bad(v3_hint() if is_v3(ch) else fed_hint(), 400)
            return FED.recipients(c, uid, ch, mem)
        out, _ = recipients(c, mem)
    return _no_store({"chat_id": ch["id"], "recipients": out})


@router.get("/api/chats/{cid}/messages")
def api_messages(cid: int, request: Request, device: str = "", before: int = 0, after: int = 0, limit: int = PAGE, calls: int = 0):
    """Nachrichten (aelteste zuerst) mit dem fuer mich versiegelten Schluessel. ?before=<seq> aeltere, ?after=<seq> neuere.
    ?calls=1: auch Anruf-Eintraege (kind='call', body_ct = JSON-Metadaten im Klartext, ohne Schluessel/Signatur; 2.1)."""
    uid, err = _me(request)
    if err:
        return err
    limit = max(1, min(200, limit))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        dev = _my_device(c, uid, device)
        if hfmt(ch) and not V2_CLIENT.get():            # aeltere App: keine v2-Nachrichten, nur der Hinweis
            return _no_store({"chat_id": ch["id"], "messages": [], "more": False, "devices": {}, "idents": {}, "last_read": m["last_read"],
                              "last_seq": ch["last_seq"], "fed_hint": v3_hint() if is_v3(ch) else fed_hint()})
        where, args, desc = ("AND m.kind IN ('msg','call','sys')" if calls and hfmt(ch) else "AND m.kind IN ('msg','call')" if calls
                             else "AND m.kind='msg'"), (), True
        if after > 0:
            where, args, desc = where + " AND m.seq>?", (min(after, 2 ** 63 - 1),), False
        elif before > 0:
            where, args = where + " AND m.seq<?", (min(before, 2 ** 63 - 1),)
        rows = _msgs(c, uid, ch["id"], dev, where, args, limit + 1, desc)
        more = len(rows) > limit
        rows = rows[:limit]
        if desc:
            rows.reverse()
        nm = names(c, [r[3] for r in rows])
        v3 = hfmt(ch)
        # Format v2 (2.5, Stufe 3 und foederiert): Geraete-/Identitaetsdaten v1 (mit Benutzername) nur fuer Altbestand ohne
        # sender_handle - den hat der Betrachter mit seinem Schluessel ohnehin schon gesehen (_msgs liefert nur Nachrichten
        # mit Schluessel)
        devs, ids = _senders(c, [r for r in rows if not r[14]] if v3 else rows)
        msgs = [_msg_dict(r, nm) for r in rows]
        if v3:
            for x in msgs:
                if x.get("sender_handle") and x.get("sender") and x["sender"]["username"] != uid and not sharing.is_remote(x["sender"]["username"]):
                    x["sender"] = dict(x["sender"], username=x["sender_handle"])
        if not hfmt(ch):                                # v2: KI-Hash ist mitsigniert (Feld ai), kein Server-Beleg
            ai_marks(c, ch["id"], msgs)
        elif X3 is not None:                            # 2.5: Reaktionen (rx) und neueste Bearbeitung am Original
            X3.messages_extra(c, uid, ch, msgs, dev)
        return _no_store({"chat_id": ch["id"], "messages": msgs, "more": more,
                          "devices": devs, "idents": ids, "last_read": m["last_read"], "last_seq": ch["last_seq"]})


@router.post("/api/chats/{cid}/messages")
async def api_send(cid: int, request: Request):
    """Senden: {id, ts, sender_device, body_ct, sig, reply_to?, kind?('msg'|'title'), keys:{empfaenger: sealed}, files:[ids]}.
    keys muss GENAU die aktuellen Empfaenger abdecken (aktive Geraete + backup:<uid> aller Mitglieder mit Schluesseln) -
    sonst 409 mit {recipients} (Mitglieder/Geraete haben sich geaendert: neu versiegeln und erneut senden)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    if FED is not None:
        with _db() as c:
            fch = _load(c, cid)
        if hfmt(fch):                                     # 2.3: Chat ueber Servergrenzen (saga-chat/v2); 2.5: Stufe 3
            if not V2_CLIENT.get() and b.get("v") != 2:
                return _bad(v3_hint() if is_v3(fch) else fed_hint(), 400)
            from starlette.concurrency import run_in_threadpool
            if b.get("kind") == "edit" and X3 is not None:     # 2.5: Nachricht bearbeiten (saga.chat_x3)
                return await run_in_threadpool(X3.send_edit, uid, cid, b)
            return await run_in_threadpool(FED.send, uid, cid, b)
    mid, dev, body_ct, sig = str(b.get("id") or ""), str(b.get("sender_device") or ""), b.get("body_ct"), b.get("sig")
    kind = b.get("kind") or "msg"
    reply_to = b.get("reply_to") or None
    keys, files = b.get("keys"), b.get("files") or []
    ai_ref = b.get("ai_reply")                             # (2.1) Beleg einer KI-Antwort (POST /api/chats/{cid}/ai)
    if ai_ref is not None and not (isinstance(ai_ref, str) and _HEX32.fullmatch(ai_ref)):
        return _bad(t("ungültige Angabe"))
    try:
        ts = int(b.get("ts"))
    except (TypeError, ValueError, OverflowError):
        ts = 0
    if not 0 < ts < 2 ** 53:              # sonst OverflowError bei ts / 1000 (500) bzw. BIGINT-Ueberlauf
        ts = 0
    now = time.time()
    if not _HEX32.fullmatch(mid) or kind not in ("msg", "title") or not isinstance(keys, dict) or not isinstance(files, list):
        return _bad(t("ungültige Angabe"))
    if not isinstance(body_ct, str) or len(body_ct) > MSG_MAX:
        return _bad(t("Nachricht zu groß."), 413)
    if b64d(body_ct) is None or len(b64d(body_ct)) < 24 + 16 or b64d(sig, 64) is None:
        return _bad(t("ungültige Angabe"))
    if abs(ts / 1000 - now) > SKEW:
        return _bad(t("Die Uhrzeit dieses Geräts weicht zu stark ab."), 400)
    if reply_to is not None and not (isinstance(reply_to, str) and _HEX32.fullmatch(reply_to)):
        return _bad(t("ungültige Angabe"))
    if len(files) > FILE_IDS_PER_MSG or any(not isinstance(f, str) or not _FID.fullmatch(f) for f in files):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if c.execute("SELECT v FROM chat WHERE id=? FOR SHARE", (ch["id"],)).fetchone()[0] != 1:     # 2.5: gerade umgestellt
            return _no_store({"error": t("Die Mitglieder oder ihre Geräte haben sich geändert – bitte erneut senden."), "recipients": [],
                              "chat_changed": True}, 409)
        if len(mem) > MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
        if kind == "title" and ch["kind"] != "free":
            return _bad(t("ungültige Angabe"))
        d = c.execute("SELECT sign_pub FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (dev, uid)).fetchone()
        if not d:
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        if not verify(d[0], sig, sig_msg(ch["id"], mid, uid, dev, ts, reply_to, body_ct)):
            return _bad(t("Ungültige Signatur."), 400)
        if c.execute("SELECT COUNT(*) FROM chat_msg WHERE sender_uid=? AND created>?", (uid, now - 60)).fetchone()[0] >= MSG_RATE:
            return _bad(t("Zu viele Nachrichten in kurzer Zeit – bitte kurz warten."), 429)
        if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (mid,)).fetchone():
            return _bad(t("Diese Nachricht gibt es schon."), 409)
        if reply_to and not c.execute("SELECT 1 FROM chat_msg WHERE id=? AND chat_id=?", (reply_to, ch["id"])).fetchone():
            return _bad(t("ungültige Angabe"))
        if ai_ref and (kind != "msg" or not c.execute(
                "SELECT 1 FROM chat_ai_reply WHERE id=? AND chat_id=? AND uid=? AND msg_id IS NULL AND created>? FOR UPDATE",   # sperrt: Beleg nur einmal
                (ai_ref, ch["id"], uid, now - AI_REPLY_TTL)).fetchone()):
            return _bad(t("Diese KI-Antwort gibt es nicht (mehr) oder sie wurde schon gesendet."), 409)
        rc, want = recipients(c, mem)
        if set(keys) != want or any(b64d(v, SEAL_LEN) is None for v in keys.values()):
            return _no_store({"error": t("Die Mitglieder oder ihre Geräte haben sich geändert – bitte erneut senden."), "recipients": rc}, 409)
        if files:
            ok = c.execute(f"SELECT COUNT(*) FROM chat_file WHERE chat_id=? AND uid=? AND msg_seq IS NULL AND id IN ({','.join('?' * len(files))})",
                           (ch["id"], uid, *files)).fetchone()[0]
            if ok != len(set(files)):
                return _bad(t("Anhang nicht gefunden."), 400)
        eff, mx = ch["retention_s"], retention_max()
        eff = min(eff, mx) if eff and mx else (eff or mx)
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING seq",
                        (mid, ch["id"], kind, uid, dev, ts, now, now + eff if eff else None, body_ct, sig, reply_to, len(body_ct))).fetchone()[0]
        c.executemany("INSERT INTO chat_msg_key (msg_seq, recipient, sealed) VALUES (?,?,?)", [(seq, k, v) for k, v in sorted(keys.items())])
        if ai_ref:
            c.execute("UPDATE chat_ai_reply SET msg_id=? WHERE id=?", (mid, ai_ref))
        if files:
            c.execute(f"UPDATE chat_file SET msg_seq=? WHERE id IN ({','.join('?' * len(files))})", (seq, *files))
        if kind == "title":
            old = ch["title_seq"]
            c.execute("UPDATE chat SET title_seq=? WHERE id=?", (seq, ch["id"]))
            if old:                                        # alter Titel samt Anhaengen weg (sonst zaehlen sie weiter)
                gone = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (old,))]
                c.execute("DELETE FROM chat_file WHERE msg_seq=?", (old,))
                c.execute("DELETE FROM chat_msg WHERE seq=?", (old,))
                _rm_blobs(c, gone)
            _changed(c, ch["id"], mem)
        else:
            c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, ch["id"]))
            c.execute("UPDATE chat_member SET last_read=? WHERE chat_id=? AND uid=?", (seq, ch["id"], uid))
            revive(c, ch["id"], uid)                       # (2.1) aus dem Archiv holen, ausgeblendeten Direkt-Chat zeigen
            contacts.touch(c, ch["id"], uid, now)          # 2.5: Kontakte "zuletzt zuerst"
            muted = {r[0] for r in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL AND muted<>0", (ch["id"],))}
            for u in mem:
                if u != uid and u not in muted:
                    notify.create(c, u, "chat.message", uid, data={"chat": ch["id"]}, bundle=ch["id"])
                    # Handy/Desktop (saga.push): nur die Chat-Kennung, nie Inhalt; stummgeschaltete Chats nie
                    bus.fire("push.send", c, uid=u, kind="chat.message", actor=uid, data={"chat": ch["id"]})
            _direct(c, mem, "chat.message", {"chat_id": ch["id"], "seq": seq, "id": mid, "sender": uid})
        print(f"chat: Nachricht in Chat {ch['id']} ({len(body_ct)} Zeichen Ciphertext, {len(keys)} Empfaenger)", flush=True)
    return _no_store({"ok": True, "seq": seq, "created": now, "expires_at": now + eff if eff else None})


@router.delete("/api/chats/{cid}/messages/{mid}")
def api_msg_delete(cid: int, mid: str, request: Request):
    """Eigene Nachricht fuer alle loeschen: Ciphertext, Schluessel und Anhaenge weg."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if hfmt(ch):
            c.rollback()
            return FED.delete_msg(uid, ch, mid)
        mem = _fresh(c, ch, mem)                 # Seiten-/Gruppen-Chat: gerade Entzogene bekommen nichts mehr (BUG-088)
        r = c.execute("SELECT seq, sender_uid FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg'", (mid, ch["id"])).fetchone()
        if not r:
            return _bad("not found", 404)
        if r[1] != uid:
            return _bad(t("Nur eigene Nachrichten lassen sich löschen."), 403)
        fids = [x[0] for x in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (r[0],))]
        c.execute("DELETE FROM chat_file WHERE msg_seq=?", (r[0],))
        c.execute("DELETE FROM chat_ai_reply WHERE msg_id=?", (mid,))     # Beleg einer KI-Antwort geht mit (2.1)
        c.execute("DELETE FROM chat_msg WHERE seq=?", (r[0],))
        _rm_blobs(c, fids)
        _direct(c, mem, "chat.deleted", {"chat_id": ch["id"], "id": mid, "seq": r[0]})
    return _no_store({"ok": True})


# --- Mehrfachauswahl (2.5): mehrere eigene Nachrichten fuer alle loeschen, Nachrichten nur fuer mich ausblenden ------
BULK_DELETE_MAX = 100              # POST .../messages/delete: hoechstens so viele ids
HIDE_MAX = 500                     # POST .../hide: hoechstens so viele ids


def _ids_in(b, most):
    """{"ids": [mid, ...]} -> (ids ohne Doppelte, fehler)"""
    ids = b.get("ids") if isinstance(b, dict) else None
    if not isinstance(ids, list) or not ids:
        return None, _bad(t("ungültige Angabe"))
    if len(ids) > most:
        return None, _bad(t("Höchstens {n} Nachrichten auf einmal.", n=most), 400)
    out = []
    for x in ids:
        if not isinstance(x, str) or not _HEX32.fullmatch(x):
            return None, _bad(t("ungültige Angabe"))
        if x not in out:
            out.append(x)
    return out, None


def _status_of(resp):
    """Antwort von FED.delete_msg -> Grund fuer failed (None = geloescht)."""
    code = getattr(resp, "status_code", 200)
    return None if code == 200 else "not_found" if code == 404 else "not_own" if code == 403 else "error"


@router.post("/api/chats/{cid}/messages/delete")
async def api_msg_delete_many(cid: int, request: Request):
    """Mehrere eigene Nachrichten fuer alle loeschen: {"ids": [mid, ...]} (hoechstens BULK_DELETE_MAX). Gleiche Regeln und
    Live-Ereignisse (chat.deleted je Nachricht) wie DELETE .../messages/{mid}; Format v2 (Stufe 3, foederiert) ueber
    FED.delete_msg. -> {"deleted": [mid...], "failed": {mid: "not_found" | "not_own" | "error"}}"""
    uid, err = _me(request)
    if err:
        return err
    ids, err = _ids_in(await _body(request), BULK_DELETE_MAX)
    if err:
        return err
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_delete_many, uid, cid, ids)


def _delete_many(uid, cid, ids):
    deleted, failed = [], {}
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if hfmt(ch):
            c.rollback()
        else:
            mem = _fresh(c, ch, mem)             # wie beim Einzel-Loeschen (BUG-088)
            rows = {r[0]: (r[1], r[2]) for r in c.execute(
                f"SELECT id, seq, sender_uid FROM chat_msg WHERE chat_id=? AND kind='msg' AND id IN ({','.join('?' * len(ids))})",
                (ch["id"], *ids))}
            seqs, fids = [], []
            for mid in ids:
                r = rows.get(mid)
                if not r:
                    failed[mid] = "not_found"
                elif r[1] != uid:
                    failed[mid] = "not_own"
                else:
                    seqs.append(r[0])
                    deleted.append(mid)
            if seqs:
                ph = ",".join("?" * len(seqs))
                fids = [x[0] for x in c.execute(f"SELECT id FROM chat_file WHERE msg_seq IN ({ph})", tuple(seqs))]
                c.execute(f"DELETE FROM chat_file WHERE msg_seq IN ({ph})", tuple(seqs))
                c.execute(f"DELETE FROM chat_ai_reply WHERE msg_id IN ({','.join('?' * len(deleted))})", tuple(deleted))
                c.execute(f"DELETE FROM chat_msg WHERE seq IN ({ph})", tuple(seqs))
                _rm_blobs(c, fids)
                for mid in deleted:
                    _direct(c, mem, "chat.deleted", {"chat_id": ch["id"], "id": mid, "seq": rows[mid][0]})
            return _no_store({"deleted": deleted, "failed": failed})
    for mid in ids:                              # Format v2: je Nachricht eigene Transaktion (Heim bzw. Warteschlange)
        why = _status_of(FED.delete_msg(uid, ch, mid))
        if why:
            failed[mid] = why
        else:
            deleted.append(mid)
    return _no_store({"deleted": deleted, "failed": failed})


def unhide(c, cid, uid=None):
    """Ausgeblendete Nachrichten (chat_hidden) eines Kontos in einem Chat vergessen - beim Austritt bzw. "fuer mich
    loeschen". uid=None: fuer alle (Chat selbst geht per ON DELETE CASCADE)."""
    if uid is None:
        c.execute("DELETE FROM chat_hidden WHERE chat_id=?", (cid,))
    else:
        c.execute("DELETE FROM chat_hidden WHERE chat_id=? AND uid=?", (cid, uid))


@router.post("/api/chats/{cid}/hide")
async def api_hide(cid: int, request: Request):
    """Nachrichten nur fuer mich ausblenden (auf allen eigenen Geraeten): {"ids": [mid, ...]} (hoechstens HIDE_MAX).
    Nachrichten-Listen lassen sie fuer mich weg; Live chat.hidden {chat_id, ids} an meine Geraete. Unbekannte ids zaehlen
    nicht. -> {"hidden": n} (n = davon jetzt ausgeblendet, auch schon vorher ausgeblendete)"""
    uid, err = _me(request)
    if err:
        return err
    ids, err = _ids_in(await _body(request), HIDE_MAX)
    if err:
        return err
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        rows = c.execute(f"SELECT seq, id FROM chat_msg WHERE chat_id=? AND kind IN ('msg','call') AND id IN ({','.join('?' * len(ids))})",
                         (ch["id"], *ids)).fetchall()
        now = time.time()
        for seq, mid in rows:
            c.execute("INSERT INTO chat_hidden (uid, chat_id, msg_seq, msg_id, at) VALUES (?,?,?,?,?) ON CONFLICT (uid, msg_seq) DO NOTHING",
                      (uid, ch["id"], seq, mid, now))
        got = [r[1] for r in rows]
        if got:
            _direct(c, [uid], "chat.hidden", {"chat_id": ch["id"], "ids": got})
    return _no_store({"hidden": len(got)})


@router.post("/api/chats/{cid}/read")
async def api_read(cid: int, request: Request):
    """Gelesen bis {seq}; Benachrichtigungen dieses Chats werden mit gelesen. Live: chat.read an meine anderen Tabs/Geraete.
    2.4: {receipt: {keys: {'backup:<konto|handle>': versiegelt}}} dazu - Gelesen-Bestaetigung fuer die anderen Mitglieder
    (saga.chat_status, docs/APP-API.md); Antwort dann mit receipt: ok|off|none|rate|bad."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    try:
        seq = int(b.get("seq") or 0)
    except (TypeError, ValueError):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        seq = max(m["last_read"], min(seq, ch["last_seq"]))
        c.execute("UPDATE chat_member SET last_read=? WHERE chat_id=? AND uid=?", (seq, ch["id"], uid))
        ids = [r[0] for r in c.execute("SELECT id FROM notification WHERE uid=? AND kind='chat.message' AND read_at IS NULL AND data LIKE ?",
                                       (uid, '{"chat": %d, %%' % ch["id"]))]
        if ids:
            c.execute(f"UPDATE notification SET read_at=? WHERE id IN ({','.join('?' * len(ids))})", (time.time(), *ids))
            bus.emit_after(c, "notification.read", {"uid": uid, "ids": ids, "unread": notify.count_unread(c, uid)})
        _direct(c, [uid], "chat.read", {"chat_id": ch["id"], "seq": seq})
        total = unread_total(c, uid)
    out = {"ok": True, "last_read": seq, "unread": total}
    if RECEIPT is not None and "receipt" in b:
        from starlette.concurrency import run_in_threadpool
        out["receipt"] = await run_in_threadpool(RECEIPT, uid, ch, m, b.get("receipt"))
    return _no_store(out)


@router.post("/api/chats/{cid}/typing")
def api_typing(cid: int, request: Request):
    """"tippt gerade" an die anderen Mitglieder (hoechstens alle 2,5 s, ohne Inhalt)."""
    uid, err = _me(request)
    if err:
        return err
    if not _rate("typing:%d" % cid, uid, 1, 2.5):
        return _no_store({"skipped": True})
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        mem = _fresh(c, ch, mem)                 # Seiten-/Gruppen-Chat: gerade Entzogene bekommen nichts mehr (BUG-088)
        if uid not in mem:
            return _bad("not found", 404)
        if is_fed(ch) and not ch["fstate"] and m.get("state") == "ok":
            FED.typing(uid, ch)                  # an die anderen Server: direkt, verwerfbar
        nm = names(c, [uid])
        direct_person(c, ch, [u for u in mem if u != uid], "chat.typing", {"chat_id": ch["id"], "user": {"username": uid, "name": nm.get(uid, uid)}},
                      uid, "user")
    return _no_store({"ok": True})


# --- Anhaenge (clientseitig verschluesselt; der Server sieht nur einen Blob) ---------------------------------------
@router.post("/api/chats/{cid}/files")
async def api_file_up(cid: int, request: Request):
    """Rohdaten (application/octet-stream) = nonce24 || xchacha20poly1305(datei). -> {id}. Ohne Nachricht nach 1 h geloescht."""
    uid, err = _me(request)
    if err:
        return err
    if int(request.headers.get("content-length") or 0) > FILE_MAX + 64:
        return _bad(t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    if not _rate("file", uid, FILE_RATE):
        return _bad(t("zu schnell"), 429)
    data = await request.body()
    if len(data) > FILE_MAX + 64:
        return _bad(t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    if len(data) < 24 + 16:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if used_bytes(c, uid) + len(data) > quota():
            return _bad(t("Speicher für Chat-Anhänge voll ({n} MB).", n=quota() // 1048576), 413)
        fid = secrets.token_urlsafe(18)
        os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(CHAT_DIR, fid)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created) VALUES (?,?,?,?,?)", (fid, ch["id"], uid, len(data), time.time()))
    return _no_store({"id": fid, "size": len(data)})


def _req_uid(request, fid):
    """Anmeldung fuer einen Anhang: Medien-Token (2.4, Kopf X-Saga-Media-Token, an Datei und Sitzung gebunden) oder wie
    bisher Sitzung/App-Schluessel mit X-Saga."""
    tok = request.headers.get("x-saga-media-token")
    if tok:
        return TOKEN_USER(tok, fid) if TOKEN_USER is not None else None
    return _api_guard(request)


def _file_access(fid, request, part=None):
    """Darf der Anfragende den Anhang holen? -> (fehler-antwort, None, 0) oder (None, Cache-Control, Sekunden bis zum Ablauf).
    Gemeinsam fuer den Abruf hier und die Abfrage von saga-media. Liegt der Anhang bei einem anderen Server (2.3),
    wird er jetzt geholt (Zwischenkopie). 2.4: grosse Anhaenge eines Servers mit chatfile2 werden nicht ganz geholt -
    part (dict) bekommt dann {"size": Groesse} (lueckenhafte Zwischenkopie, Bereiche holt der Abruf selbst)."""
    uid = _req_uid(request, fid)
    if not uid:
        return _bad("unauthorized", 401), None, 0
    if not enabled() or not _FID.fullmatch(fid):
        return _bad("not found", 404), None, 0
    left = MEDIA_AUTHZ_TTL
    with _db() as c:
        r = c.execute("SELECT f.chat_id, f.uid, f.msg_seq FROM chat_file f WHERE f.id=?", (fid,)).fetchone()
        if not r or not _member(c, r[0], uid) or (r[2] is None and r[1] != uid):
            return _bad("not found", 404), None, 0
        cache = "private, no-store"                   # noch keiner Nachricht zugeordnet (eigener Upload): nicht zwischenspeichern
        if r[2] is not None:
            x = c.execute("SELECT expires_at, kind FROM chat_msg WHERE seq=? AND (expires_at IS NULL OR expires_at>?)", (r[2], time.time())).fetchone()
            if not x or (x[1] != "title" and r[2] <= _pref(c, r[0], uid)["cleared_seq"]):    # (2.1) "fuer mich geloescht"
                return _bad("not found", 404), None, 0
            # Ciphertext unter dieser Kennung aendert sich nie (Schluessel nur in der verschluesselten Nachricht):
            # privat und unveraenderlich zwischenspeichern, hoechstens bis zum Ablauf der Nachricht (2.1, Chat-Bilder)
            age = int(min(FILE_CACHE_MAX, (x[0] - time.time()) if x[0] else FILE_CACHE_MAX))
            if age > 0:
                cache = f"private, max-age={age}, immutable"
            if x[0]:
                left = max(0, min(left, int(x[0] - time.time())))
    path = os.path.join(CHAT_DIR, fid)
    if FED is not None:
        if not os.path.isfile(path):
            ranged = FED.ranged(fid)                  # 2.4: grosser Anhang eines Servers mit chatfile2 -> Bereiche bei Bedarf
            if ranged:
                if part is not None:
                    part.update(ranged)
                    return None, cache, left
                return _bad("not found", 404), None, 0
            FED.fetch_file(fid)                       # 2.3: Anhang liegt beim Absender-Server - jetzt holen (Zwischenkopie)
        else:
            FED.touch(fid)                            # zuletzt benutzt (Zwischenkopien werden je Instanz begrenzt)
    if not os.path.isfile(path) or os.path.islink(path):    # nur echte Dateien im Blob-Verzeichnis (wie saga-media)
        return _bad("not found", 404), None, 0
    return None, cache, left


def _file_headers(cache, fid):
    return {"Cache-Control": cache, "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'",
            "Content-Disposition": "attachment", "ETag": f'"{fid}"'}


def _inm_match(request, fid):
    inm = request.headers.get("if-none-match") or ""
    return any(p.strip() == "*" or p.strip().removeprefix("W/") == f'"{fid}"' for p in inm.split(",")) if inm else False


class _PyFile(FileResponse):
    """Ausliefern durch Python (ohne saga-media und ohne MEDIA_ACCEL): hoechstens MEDIA_PY_STREAMS gleichzeitig, weitere
    warten bis zu 30 s (Galerien mit vielen Bildern), danach 503."""

    async def __call__(self, scope, receive, send):
        sem = _py_streams["sem"]
        if sem is None:
            sem = _py_streams["sem"] = asyncio.Semaphore(MEDIA_PY_STREAMS)
        try:
            await asyncio.wait_for(sem.acquire(), 30)
        except asyncio.TimeoutError:
            await Response("busy", 503, headers={"Retry-After": "2", "Cache-Control": "no-store"})(scope, receive, send)
            return
        try:
            await super().__call__(scope, receive, send)
        finally:
            sem.release()


def media_mode():
    """-> (art, grenze) fuer den Admin-Bereich: saga-media | nginx | caddy | python."""
    if MEDIA_FRONT:
        return "saga-media", 0
    if MEDIA_ACCEL != "off":
        return MEDIA_ACCEL, 0
    return "python", MEDIA_PY_STREAMS


@router.api_route("/api/chat/files/{fid}", methods=["GET", "HEAD"])
def api_file_get(fid: str, request: Request):
    part = {}
    err, cache, _ = _file_access(fid, request, part)
    if err:
        return err
    h = _file_headers(cache, fid)
    if _inm_match(request, fid):
        return Response(status_code=304, headers={"Cache-Control": cache, "ETag": h["ETag"]})
    if part:                                       # 2.4: lueckenhafte Zwischenkopie - fehlende Bereiche jetzt holen
        return FED.serve_range(fid, part, request, h)
    if MEDIA_ACCEL != "off":
        # Reverse Proxy liefert die Datei selbst (nginx: internal-location, Caddy: handle_response) - siehe Anleitung
        return Response(status_code=200, media_type="application/octet-stream",
                        headers=dict(h, **{"X-Accel-Redirect": MEDIA_ACCEL_PREFIX + fid, "Accept-Ranges": "bytes"}))
    return _PyFile(os.path.join(CHAT_DIR, fid), media_type="application/octet-stream", headers=h)


@router.get("/internal/media/authz")
def api_media_authz(request: Request, fid: str = ""):
    """Nur fuer saga-media (gleicher Container, Geheimnis im Kopf; von aussen sperrt saga-media /internal/). Prueft genau
    wie der Abruf - die Anfrage traegt Cookie/Authorization/X-Saga/Host des Clients. Kein Inhalt, nur die Entscheidung."""
    if not MEDIA_KEY or not secrets.compare_digest(request.headers.get("x-saga-media-key", ""), MEDIA_KEY):
        return _bad("not found", 404)
    part = {}
    err, cache, left = _file_access(fid, request, part)
    if err:
        return err
    out = {"ok": True, "ttl": left, "headers": _file_headers(cache, fid)}
    if part:
        out["fed"] = part                          # 2.4: Bereiche einer lueckenhaften Zwischenkopie (saga-media fragt je Bereich)
    return _no_store(out)


# --- Aufraeumen, Kontoloeschung ---------------------------------------------------------------------------------
def purge(c=None):
    """Abgelaufene Nachrichten (Frist des Chats bzw. Admin-Hoechstwert), Anhaenge ohne Nachricht (> 1 h), alte Kopplungen.
    -> Anzahl geloeschter Nachrichten. Stuendlich aus app.py."""
    if c is None:
        with _db() as c2, c2:
            return purge(c2)
    now = time.time()
    mx = retention_max()
    cond, args = "(expires_at IS NOT NULL AND expires_at<?)", [now]
    if mx:
        cond += " OR created<?"
        args.append(now - mx)
    seqs = [r[0] for r in c.execute(f"SELECT seq FROM chat_msg WHERE {cond} LIMIT 5000", tuple(args))]
    fids = []
    if seqs:
        ph = ",".join("?" * len(seqs))
        fids = [r[0] for r in c.execute(f"SELECT id FROM chat_file WHERE msg_seq IN ({ph})", tuple(seqs))]
        c.execute(f"DELETE FROM chat_file WHERE msg_seq IN ({ph})", tuple(seqs))
        c.execute(f"UPDATE chat SET title_seq=NULL WHERE title_seq IN ({ph})", tuple(seqs))
        c.execute(f"DELETE FROM chat_ai_reply WHERE msg_id IN (SELECT id FROM chat_msg WHERE seq IN ({ph}))", tuple(seqs))
        c.execute(f"DELETE FROM chat_msg WHERE seq IN ({ph})", tuple(seqs))
    # KI-Belege (2.1): nie gesendete nach Ablauf, und solche, deren Nachricht es nicht mehr gibt (Nachrichten ohne Beleg-
    # Bezug geloescht, z. B. mit dem Chat per CASCADE oder von aelteren Versionen) - kein Hash bleibt ohne Nachricht liegen
    c.execute("DELETE FROM chat_ai_reply WHERE msg_id IS NULL AND created<?", (now - AI_REPLY_TTL,))
    c.execute("DELETE FROM chat_ai_reply r WHERE r.msg_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM chat_msg m WHERE m.id=r.msg_id)")
    for (cid,) in c.execute("SELECT id FROM chat WHERE kind='page' AND home_peer IS NULL AND NOT EXISTS (SELECT 1 FROM note n "
                            "WHERE n.id=chat.note_id)").fetchall():
        if FED is not None:
            FED.chat_dropping(c, _load(c, cid))            # 2.3: Seiten-Chat ueber Servergrenzen - die anderen Server raeumen auch
        fids += _drop_chat(c, cid)                         # Seite endgueltig geloescht (Papierkorb geleert): Seiten-Chat auch
    orphan = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE msg_seq IS NULL AND created<?", (now - 3600,))]
    if orphan:
        c.execute(f"DELETE FROM chat_file WHERE id IN ({','.join('?' * len(orphan))})", tuple(orphan))
    c.execute("DELETE FROM chat_link WHERE expires<?", (now - 60,))
    _rm_blobs(c, fids + orphan)
    # 2.4: verfallene Uploads und Teil-Dateien ohne Eintrag (Chat geloescht, Backup zurueckgespielt)
    ups = [r[0] for r in c.execute("DELETE FROM chat_upload WHERE expires<? RETURNING id", (now,))]
    try:
        names_ = [f for f in os.listdir(UP_DIR) if _FID.fullmatch(f)]
    except OSError:
        names_ = []
    if names_:
        known = {r[0] for r in c.execute("SELECT id FROM chat_upload")}
        for f in names_:
            try:
                old = now - os.stat(os.path.join(UP_DIR, f)).st_mtime > 3600
            except OSError:
                continue
            if f not in known and old:
                ups.append(f)
    _rm_uploads(c, ups)
    return len(seqs)


def delete_account(c, uid):
    """Kontoloeschung: Identitaet, Geraete, Kopplungen, Mitgliedschaften, gesendete Nachrichten und Anhaenge des Kontos;
    Direkt-Chats mit dem Konto und leere freie Gruppen ganz. Blobs werden nach dem Commit entfernt. -> Anzahl Nachrichten."""
    if FED is not None:
        FED.account_gone(c, uid)                      # 2.3: foederierte Chats bei den anderen Servern beenden
    devs = [r[0] for r in c.execute("SELECT id FROM chat_device WHERE uid=?", (uid,))]
    rcpt = devs + ["backup:" + uid]
    for i in range(0, len(rcpt), 500):
        part = rcpt[i:i + 500]
        c.execute(f"DELETE FROM chat_msg_key WHERE recipient IN ({','.join('?' * len(part))})", tuple(part))
    fids = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE uid=? OR msg_seq IN (SELECT seq FROM chat_msg WHERE sender_uid=?)", (uid, uid))]
    if fids:
        c.execute(f"DELETE FROM chat_file WHERE id IN ({','.join('?' * len(fids))})", tuple(fids))
    c.execute("UPDATE chat SET title_seq=NULL WHERE title_seq IN (SELECT seq FROM chat_msg WHERE sender_uid=?)", (uid,))
    c.execute("DELETE FROM chat_ai_reply WHERE msg_id IN (SELECT id FROM chat_msg WHERE sender_uid=?)", (uid,))
    # 2.5 (Stufe 3): Reaktionen des Kontos (je Handle) weg; Verweise anderer Nachrichten auf seine Bearbeitungen loesen
    h = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
    if h and FED is not None:
        c.execute("DELETE FROM chat_reaction WHERE reactor=?", (FED.FK.full_handle(h[0]),))
    c.execute("UPDATE chat_msg SET edit_seq=NULL, edited_at=NULL, edit_n=0 WHERE edit_seq IN (SELECT seq FROM chat_msg WHERE sender_uid=?)", (uid,))
    n = c.execute("DELETE FROM chat_msg WHERE sender_uid=?", (uid,)).rowcount
    # KI im Chat (2.1): nie gesendete Belege weg; Belege, deren Nachricht bleibt, ohne Personenbezug behalten (die
    # Kennzeichnung als KI-Antwort bleibt gueltig). Wer die KI in einem Chat erlaubt hat, wird vergessen.
    c.execute("DELETE FROM chat_ai_reply WHERE uid=? AND (msg_id IS NULL OR NOT EXISTS (SELECT 1 FROM chat_msg m WHERE m.id=chat_ai_reply.msg_id))",
              (uid,))
    c.execute("UPDATE chat_ai_reply SET uid=? WHERE uid=?", (AI_GONE, uid))
    c.execute("UPDATE chat_ai SET by_uid=? WHERE by_uid=?", (AI_GONE, uid))
    for (cid,) in c.execute("SELECT id FROM chat WHERE kind='page' AND home_peer IS NULL AND note_id IN (SELECT id FROM note WHERE uid=?)",
                            (uid,)).fetchall():
        if FED is not None:
            FED.chat_dropping(c, _load(c, cid))
        fids += _drop_chat(c, cid)                         # Seiten-Chats eigener Seiten (die Seiten gehen mit)
    chats = [r[0] for r in c.execute("SELECT chat_id FROM chat_member WHERE uid=?", (uid,))]
    c.execute("DELETE FROM chat_member WHERE uid=?", (uid,))
    c.execute("DELETE FROM chat_pref WHERE uid=?", (uid,))                         # Archiv / "fuer mich geloescht" (2.1)
    c.execute("DELETE FROM chat_hidden WHERE uid=?", (uid,))                       # "fuer mich ausgeblendet" (2.5)
    contacts.drop_account(c, uid)                                                  # 2.5: Kontakte (eigene + das Konto bei anderen)
    for cid in chats:
        ch = _load(c, cid)
        if not ch:
            continue
        if ch["kind"] == "dm" or (ch["kind"] == "free" and not members(c, cid)):
            fids += _drop_chat(c, cid)
        else:
            if ch["kind"] == "free" and not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'",
                                                      (cid,)).fetchone():
                rest = members(c, cid)
                c.execute("UPDATE chat_member SET role='admin' WHERE chat_id=? AND uid=?", (cid, rest[0]))
            _changed(c, cid)
    c.execute("UPDATE chat SET created_by=NULL WHERE created_by=?", (uid,))
    c.execute("DELETE FROM chat_call WHERE caller=? OR callee=?", (uid, uid))      # Anrufe (2.1)
    c.execute("DELETE FROM chat_call_leg WHERE who=?", (uid,))                     # 2.4: Endpunkte/Einladungen in Gruppenanrufen
    c.execute("DELETE FROM chat_call_invite WHERE who=?", (uid,))
    _rm_uploads(c, [r[0] for r in c.execute("DELETE FROM chat_upload WHERE uid=? RETURNING id", (uid,))])   # 2.4
    for tbl in ("chat_identity", "chat_device", "chat_link"):
        c.execute(f"DELETE FROM {tbl} WHERE uid=?", (uid,))
    _rm_blobs(c, fids)
    return n


def counts(c, uid):
    """Admin, Benutzer-Detail: nur Anzahlen (2.5: dazu v3 = Stand der Umstellung auf Stufe 3, siehe v3_state)."""
    return {"chats": c.execute("SELECT COUNT(*) FROM chat_member WHERE uid=? AND left_at IS NULL", (uid,)).fetchone()[0],
            "chat_devices": c.execute("SELECT COUNT(*) FROM chat_device WHERE uid=? AND revoked_at IS NULL", (uid,)).fetchone()[0],
            "v3": v3_state(c, uid)}


def v3_state(c, uid, now=None):
    """Bughunt 3 (Admin -> Benutzer -> Chats): warum Chats dieser Person noch nicht auf Stufe 3 sind - wie x3_why
    'devices' im Chat-Objekt (saga.chat_x3.v1_flags), zusammengefasst ueber alle ihre lokalen Chats v1.
    -> None (Stufe 3 aus) oder {v1: Anzahl, mine: [Namen eigener Geraete ohne 2.5], nocert: eigenes Handle fehlt,
    others: Anzahl anderer Personen, die noch aufhalten, until: Stichtag, soon: Umstellung beim naechsten Lauf}."""
    if not v3_new():
        return None
    ids = [r[0] for r in c.execute("SELECT ch.id FROM chat ch JOIN chat_member m ON m.chat_id=ch.id AND m.uid=? AND m.left_at IS NULL "
                                   "WHERE ch.v=1 AND ch.ref IS NULL AND ch.home_peer IS NULL", (uid,)).fetchall()]
    _, until = v3_deadline()
    if not ids:
        return {"v1": 0, "mine": [], "nocert": False, "others": 0, "until": int(until), "soon": False}
    now = now or time.time()
    mem = sorted({r[0] for r in c.execute(f"SELECT uid FROM chat_member WHERE chat_id IN ({','.join('?' * len(ids))}) AND left_at IS NULL",
                                          tuple(ids)).fetchall() if not sharing.is_remote(r[0])} | {uid})
    cert = {r[0] for r in c.execute(f"SELECT f.uid FROM chat_fed_cert f JOIN chat_identity i ON i.uid=f.uid AND i.ident_pub=f.ident_pub "
                                    f"WHERE f.uid IN ({','.join('?' * len(mem))})", tuple(mem)).fetchall()}
    nocert = set(mem) - cert
    old = blocking_devices(c, mem, now) if now < until else []
    mine = [(n or "").strip()[:80] or t("Gerät ohne Namen") for u, n in old if u == uid][:20]
    others = len(({u for u, _ in old} | nocert) - {uid})
    return {"v1": len(ids), "mine": mine, "nocert": uid in nocert, "others": others, "until": int(until),
            "soon": not nocert and not old}


# --- Benachrichtigungen (saga.notify): Titel/Link erst beim Lesen, nur fuer aktuelle Mitglieder --------------------
def _notif(c, uid, item, d):
    ch = _load(c, d.get("chat"))
    m = _member(c, ch["id"], uid) if ch else None
    item["access"] = bool(m)
    if not m:
        item["title"] = t("Kein Zugriff mehr")
        return
    mem = members(c, ch["id"])
    item["chat"] = {"id": ch["id"], "kind": ch["kind"]}
    item["url"] = f"/chats#c{ch['id']}"
    item["title"] = _name(c, uid, ch, mem, names(c, mem)) or t("Gruppen-Chat")


notify.RENDER["chat.message"] = _notif


# --- Seite /chats -------------------------------------------------------------------------------------------------
@router.get("/chats", response_class=HTMLResponse)
def chats_page(request: Request):
    """Chats im eigenen Tab. Nur diese Seite darf WebAssembly kompilieren ('wasm-unsafe-eval' fuer libsodium.js) -
    die uebrige CSP bleibt gleich (kein eval, keine fremden Skripte)."""
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login?next=/chats", status_code=303)
    if (go := mfa.setup_redirect(request, uid)) is not None:
        return go
    remember_ui_lang(uid, request)
    info = user_info(uid)
    html = tmpl.get_template("chats.html").render(title=title(), name=request.session.get("name", uid), v=ASSET_V, uid=uid,
                                                  enabled=enabled(), admin_url=ADMIN_URL if info and info["is_admin"] else "",
                                                  me_avatar=avatars.my_url(uid))
    headers = icon_csp(SEC_HEADERS, uid)             # Einstellungen -> Lesezeichen: Symbole direkt von der Seite
    headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'") \
        + "; media-src 'self' blob:"          # 2.4: Videos (Service Worker /_m/..., ohne ihn als Blob)
    return HTMLResponse(html, headers=headers)
