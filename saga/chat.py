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
import binascii
import hashlib
import json
import os
import re
import secrets
import struct
import time
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
import auth
from db import connect as _db
from saga import bus, notify
from saga.core import (ADMIN_URL, ASSET_V, E, SEC_HEADERS, STATE_DIR, _api_guard, _bad, _body, _session_user, remember_ui_lang, t, title,
                       tmpl, user_info)
from saga.notes import FILE_MAX, note_chain
from saga.sharing import REMOTE_TYPES, Access, enabled as share_enabled, group_scope, groups_of, names

router = APIRouter()

CHAT_DIR = os.path.join(STATE_DIR, "chat")
MSG_MAX = 64 * 1024                    # Ciphertext je Nachricht (B64)
MSG_RATE = 60                          # Nachrichten je Minute und Konto
DEVICES_MAX = 10                       # aktive Geraete je Konto
MEMBERS_MAX = 50                       # Mitglieder je Chat (darueber spaeter MLS)
FILES_PER_MSG = 10
FILE_RATE = 30                         # Uploads je Minute
QUOTA = max(1, int(E.get("CHAT_QUOTA_MB", "1024"))) * 1024 * 1024    # Anhaenge je Konto
LINK_TTL = 600                         # Geraete-Kopplung: 10 Minuten
PAGE = 50
SKEW = 600                             # erlaubte Abweichung der Client-Zeit (ts) in Sekunden
RETENTIONS = (0, 3600, 86400, 7 * 86400, 30 * 86400, 90 * 86400, 365 * 86400)
SEAL_LEN = 32 + 48                     # crypto_box_seal(32-Byte-Schluessel)
_HEX32 = re.compile(r"[0-9a-f]{32}")
REAUTH_SEC = 300                       # "Neu beginnen": Passwort oder Anmeldung hoechstens 5 Minuten alt
_FID = re.compile(r"[A-Za-z0-9_-]{22,40}")
_hits = {}                             # (art, uid) -> [zeiten] fuer Tipp-/Upload-/Such-Bremsen


# --- Hilfen -------------------------------------------------------------------------------------------------
def enabled():
    return bool(auth.settings().get("chat_enabled", True))


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
C_COLS = "id, kind, dm_key, group_name, note_id, created_by, created, retention_s, title_seq, last_seq, last_at"


def _row(r):
    return dict(zip(("id", "kind", "dm_key", "group_name", "note_id", "created_by", "created", "retention_s", "title_seq",
                     "last_seq", "last_at"), r)) if r else None


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
    """Wer gehoert gerade zum Chat? group/page: berechnet; dm/free: None (feste Mitglieder)."""
    if ch["kind"] == "group":
        return set(notify.group_members(c, ch["group_name"]))
    if ch["kind"] == "page":
        return page_members(c, ch["note_id"])
    return None


def members(c, cid):
    return [r[0] for r in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL ORDER BY joined, uid", (cid,))]


def sync(c, ch):
    """Mitglieder von group/page-Chats an Gruppe/Freigaben angleichen -> aktuelle Mitglieder."""
    want = entitled(c, ch)
    cur = members(c, ch["id"])
    if want is None:
        return cur
    now = time.time()
    changed = False
    for u in sorted(want - set(cur)):
        c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE "
                  "SET left_at=NULL, joined=EXCLUDED.joined, last_read=GREATEST(chat_member.last_read, ?)", (ch["id"], u, now, ch["last_seq"]))
        changed = True
    gone = [u for u in cur if u not in want]
    for u in gone:
        c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid=?", (now, ch["id"], u))
        changed = True
    if changed:
        _changed(c, ch["id"], set(cur) | want)
    return sorted(want, key=lambda u: (u not in cur, u))


def _member(c, cid, uid):
    r = c.execute("SELECT role, muted, last_read, joined FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (cid, uid)).fetchone()
    return dict(zip(("role", "muted", "last_read", "joined"), r)) if r else None


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


def _direct(c, uids, typ, data):
    """Live-Ereignis (ohne Inhalt) an diese Konten - nach dem Commit (saga.live, Ereignis-Art 'direct')."""
    uids = sorted({u for u in uids if u})
    if uids:
        bus.emit_after(c, "direct", {"uids": uids, "typ": typ, "data": data})


def _changed(c, cid, uids=None):
    _direct(c, uids if uids is not None else members(c, cid), "chat.changed", {"chat_id": cid})


def may_contact(c, uid, other):
    """Darf uid mit other einen Chat beginnen? Aktives Konto; mit "nur gemeinsame Gruppen" (Teilen) nur dann."""
    r = c.execute("SELECT disabled FROM users WHERE username=?", (other,)).fetchone()
    if not r or r[0] or other == uid:
        return False
    return not group_scope() or bool(groups_of(c, uid) & groups_of(c, other))


# --- Erneute Anmeldung fuer "Neu beginnen" ---------------------------------------------------------------------
# Neu beginnen wirft alle Geraete hinaus und ersetzt die Schluessel - wer nur eine (gestohlene) Sitzung hat, soll das
# nicht koennen. Verlangt: Passwort (Feld password, lokal/LDAP) ODER eine Anmeldung bzw. Passwort-Bestaetigung dieser
# Sitzung, die hoechstens REAUTH_SEC alt ist (SSO-Konten ohne Passwort: frische Anmeldung ueber /login/sso?reauth=1).
def _reauth_need(request, uid):
    """-> None (frisch genug) | 'password' (Passwort eingeben) | 'login' (neu anmelden, z. B. SSO)."""
    now = time.time()
    if request.session.get("uid") == uid and not request.headers.get("authorization", "").startswith("Bearer "):
        fresh = max(int(request.session.get("login_at") or 0), int(request.session.get("chat_reauth_at") or 0))
        if 0 <= now - fresh <= REAUTH_SEC:
            return None
    u = auth.get_user(uid)
    return "password" if u and (u["password_hash"] or u["source"] == "ldap") else "login"


def _check_password(request, uid, pw):
    if not isinstance(pw, str) or not pw or len(pw) > 200:
        return False
    ip = (request.client.host if request.client else "") or "?"
    user, _err = auth.authenticate(uid, pw, ip, True, area="chat")
    return bool(user and user["username"] == uid)


@router.post("/api/chat/reauth")
async def api_reauth(request: Request):
    """{password} -> diese Sitzung gilt REAUTH_SEC lang als frisch angemeldet (fuer "Neu beginnen")."""
    from starlette.concurrency import run_in_threadpool
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if not await run_in_threadpool(_check_password, request, uid, (b or {}).get("password")):
        print(f"chat: {uid} Passwort-Bestaetigung abgewiesen", flush=True)
        return _bad(t("Passwort falsch."), 403)
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
    return _no_store({
        "enabled": enabled(), "uid": uid, "name": nm.get(uid, uid), "identity": ident,
        "devices": [{"id": d["id"], "name": d["name"], "created": d["created"], "last_seen": d["last_seen"],
                     "revoked": bool(d["revoked_at"]), "current": d["id"] == device, "enc_pub": d["enc_pub"],
                     "sign_pub": d["sign_pub"], "sig": d["sig"]} for d in devs],
        "links": n_link, "reauth": _reauth_need(request, uid),
        "limits": {"msg_max": MSG_MAX, "file_max": FILE_MAX, "members_max": MEMBERS_MAX, "devices_max": DEVICES_MAX,
                   "files_per_msg": FILES_PER_MSG, "retention_max": retention_max(), "retentions": list(RETENTIONS)}})


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
        return _bad(t("ungueltige Anfrage"))
    with _db() as c:
        had = _identity(c, uid) is not None
    if had and b.get("reset") is True:
        need = _reauth_need(request, uid)
        if need and not (need == "password" and await run_in_threadpool(_check_password, request, uid, b.get("password"))):
            print(f"chat: {uid} Neu beginnen ohne frische Anmeldung abgewiesen", flush=True)
            msg = t("Zum Neubeginnen bitte dein Passwort eingeben.") if need == "password" else \
                t("Zum Neubeginnen bitte neu anmelden – danach hast du 5 Minuten Zeit.")
            return JSONResponse({"error": msg, "reauth": need}, status_code=403, headers={"Cache-Control": "no-store"})
    ident_pub, backup_pub, backup_sig = b.get("ident_pub"), b.get("backup_pub"), b.get("backup_sig")
    bundle = _valid_bundle(b.get("bundle"))
    dev = _device_in(b.get("device"))
    if b64d(ident_pub, 32) is None or b64d(backup_pub, 32) is None or not bundle or not dev:
        return _bad(t("ungueltige Angabe"))
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
        return _bad(t("ungueltige Angabe"))
    with _db() as c, c:
        ident = _identity(c, uid)
        if not ident:
            return _bad("not found", 404)
        if not verify(ident["ident_pub"], b.get("sig"), lp("saga-chat/v1/bundle-update", uid, bundle)):
            return _bad(t("Ungültige Signatur."), 403)
        c.execute("UPDATE chat_identity SET bundle=?, updated=? WHERE uid=?", (bundle, time.time(), uid))
    return _no_store({"ok": True})


@router.post("/api/chat/devices")
async def api_device_add(request: Request):
    """Geraet anmelden (nach Wiederherstellung): {id, name, enc_pub, sign_pub, sig} - sig vom Identitaetsschluessel."""
    uid, err = _me(request)
    if err:
        return err
    d = _device_in(await _body(request))
    if not d:
        return _bad(t("ungueltige Angabe"))
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
        return _bad(t("ungueltige Angabe"))
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
        return _bad(t("ungueltige Angabe"))
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
    """Konten, mit denen uid einen Chat hat oder hatte."""
    return {r[0] for r in c.execute("SELECT DISTINCT m2.uid FROM chat_member m1 JOIN chat_member m2 ON m2.chat_id=m1.chat_id "
                                    "WHERE m1.uid=?", (uid,))}


@router.get("/api/chat/keys")
def api_keys(request: Request, users: str = ""):
    """Oeffentliche Schluessel: ?users=a,b (hoechstens 60) - nur eigene, von Chat-Partnern oder Konten, mit denen man einen
    Chat beginnen darf. Enthaelt entfernte Geraete (revoked) - fuer die Pruefung alter Signaturen."""
    uid, err = _me(request)
    if err:
        return err
    want = [u.strip().lower() for u in users.split(",") if u.strip()][:60]
    with _db() as c:
        known = _known(c, uid) | {uid}
        ok = [u for u in want if u in known or may_contact(c, uid, u)]
        return _no_store({"users": _keys(c, ok)})


def _like(q):
    return re.sub(r"([\\%_])", r"\\\1", q)


@router.get("/api/chat/people")
def api_people(request: Request, q: str = ""):
    """Personensuche fuer neue Unterhaltungen (wie beim Teilen: ab 3 Zeichen, hoechstens 10) + passende eigene Gruppen."""
    uid, err = _me(request)
    if err:
        return err
    q = re.sub(r"\s+", " ", q or "").strip().lower()[:60]
    if not _rate("people", uid, 60):
        return _bad(t("zu schnell"), 429)
    if len(q) < 3:
        return _no_store({"users": [], "groups": []})
    with _db() as c:
        mine = sorted(groups_of(c, uid))
        pat = _like(q)
        sql = ("SELECT u.username, u.display_name, (i.uid IS NOT NULL) FROM users u LEFT JOIN chat_identity i ON i.uid=u.username "
               "WHERE u.disabled=0 AND u.username<>? AND (u.username LIKE ? OR lower(u.display_name) LIKE ?)")
        args = [uid, pat + "%", "%" + pat + "%"]
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
            rows = c.execute(sql + " ORDER BY (u.username=?) DESC, u.username LIMIT 10", (*args, q)).fetchall()
    return _no_store({"users": [{"username": r[0], "name": r[1] or r[0], "ready": bool(r[2])} for r in rows],
                      "groups": [{"name": g} for g in mine if q in g][:10]})


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
        return _bad(t("ungueltige Angabe"))
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
    r = c.execute("SELECT last_seen FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (device, uid)).fetchone()
    if not r:
        return None
    if (r[0] or 0) < time.time() - 60:
        c.execute("UPDATE chat_device SET last_seen=? WHERE id=?", (time.time(), device))
    return device


def _msgs(c, uid, cid, device, where="", args=(), limit=PAGE, desc=True):
    """Nachrichten mit dem fuer mich versiegelten Schluessel (Geraet, sonst Backup). Nur, was fuer mich verschluesselt ist."""
    bk = "backup:" + uid
    dv = device or "-"
    rows = c.execute(
        f"SELECT {M_COLS}, (SELECT sealed FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient=?), "
        "(SELECT sealed FROM chat_msg_key k WHERE k.msg_seq=m.seq AND k.recipient=?) FROM chat_msg m "
        "WHERE m.chat_id=? AND (m.expires_at IS NULL OR m.expires_at>?) AND EXISTS (SELECT 1 FROM chat_msg_key k "
        f"WHERE k.msg_seq=m.seq AND k.recipient IN (?,?)) {where} ORDER BY m.seq {'DESC' if desc else 'ASC'} LIMIT ?",
        (dv, bk, cid, time.time(), dv, bk, *args, limit)).fetchall()
    return rows


def _msg_dict(r, nm):
    key, via = (r[11], "device") if r[11] else (r[12], "backup")
    return {"seq": r[0], "id": r[1], "kind": r[2], "sender": {"username": r[3], "name": nm.get(r[3]) or t("Gelöschtes Konto")} if r[3] else None,
            "sender_device": r[4], "ts": r[5], "created": r[6], "expires_at": r[7], "body_ct": r[8], "sig": r[9],
            "reply_to": r[10], "key": key, "key_for": via}


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
                     "AND (m.expires_at IS NULL OR m.expires_at>?)", (cid, last_read, uid, time.time())).fetchone()[0]


def _chat_dict(c, uid, ch, mem, m, device, full=False):
    nm = names(c, list(mem) + ([ch["dm_key"].split("|")[0], ch["dm_key"].split("|")[-1]] if ch["dm_key"] else []))
    d = {"id": ch["id"], "kind": ch["kind"], "name": _name(c, uid, ch, mem, nm), "group": ch["group_name"], "note_id": ch["note_id"],
         "created": ch["created"], "last_at": ch["last_at"] or ch["created"], "last_seq": ch["last_seq"], "retention_s": ch["retention_s"],
         "muted": bool(m["muted"]), "role": m["role"], "last_read": m["last_read"], "unread": _unread(c, uid, ch["id"], m["last_read"]),
         "members": [{"username": u, "name": nm.get(u, u), "me": u == uid} for u in mem], "member_count": len(mem)}
    if ch["kind"] == "dm":
        a, _, b = ch["dm_key"].partition("|")
        d["peer"] = {"username": b if a == uid else a, "name": d["name"]}
    if ch["kind"] == "free" and ch["title_seq"]:
        rows = _msgs(c, uid, ch["id"], device, "AND m.seq=?", (ch["title_seq"],), 1)
        if rows:
            nmt = names(c, [rows[0][3]])
            devs, ids = _senders(c, rows)
            d["title_msg"] = dict(_msg_dict(rows[0], nmt), device=devs.get(rows[0][4]), ident_pub=ids.get(rows[0][3]))
    if full:
        d["max_members"] = MEMBERS_MAX
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
    """Meine Chats (neueste zuerst) mit Ungelesen-Zahl; Gruppen-/Seiten-Chats werden dabei mit Gruppe/Freigaben abgeglichen."""
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
                           "ON m.chat_id=ch.id WHERE m.uid=? AND m.left_at IS NULL ORDER BY COALESCE(ch.last_at, ch.created) DESC LIMIT 500",
                           (uid,)).fetchall():
            ch = _row(r)
            out.append(_chat_dict(c, uid, ch, members(c, ch["id"]), _member(c, ch["id"], uid), dev))
    return _no_store({"chats": out, "unread": sum(x["unread"] for x in out if not x["muted"])})


def unread_total(c, uid):
    """Ungelesene Nachrichten in nicht stummgeschalteten Chats (Knopf "Chats" auf Dashboard/Seiten)."""
    if not enabled():
        return 0
    return c.execute(
        "SELECT COUNT(*) FROM chat_msg x JOIN chat_member m ON m.chat_id=x.chat_id AND m.uid=? AND m.left_at IS NULL AND m.muted=0 "
        "WHERE x.kind='msg' AND x.seq>m.last_read AND (x.sender_uid IS NULL OR x.sender_uid<>?) AND (x.expires_at IS NULL OR x.expires_at>?)",
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
        return _bad(t("ungueltige Anfrage"))
    kind, now = b.get("kind"), time.time()
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM chat WHERE created_by=? AND created>?", (uid, now - 3600)).fetchone()[0] >= 60:
            return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
        ch = None
        if kind == "dm":
            other = str(b.get("user") or "").strip().lower()[:100]
            if not may_contact(c, uid, other):
                return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
            key = "|".join(sorted([uid, other]))
            ch = _row(c.execute(f"SELECT {C_COLS} FROM chat WHERE dm_key=?", (key,)).fetchone())
            if not ch:
                cid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created) VALUES ('dm',?,?,?)", (key, uid, now)).lastrowid
                for u in (uid, other):
                    c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
                ch = _load(c, cid)
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
                ch = _load(c, cid)
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
                ch = _load(c, cid)
        elif kind == "free":
            want = b.get("members")
            if not isinstance(want, list) or len(want) > MEMBERS_MAX:
                return _bad(t("ungueltige Angabe"))
            want = sorted({str(x).strip().lower()[:100] for x in want} - {uid})
            if len(want) + 1 > MEMBERS_MAX:
                return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
            bad = [u for u in want if not may_contact(c, uid, u)]
            if bad or not want:
                return _bad(t("Mit diesem Konto ist kein Chat möglich.") if bad else t("ungueltige Angabe"), 400)
            cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free',?,?)", (uid, now)).lastrowid
            c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,'admin',?)", (cid, uid, now))
            for u in want:
                c.execute("INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (cid, u, now))
            ch = _load(c, cid)
            _changed(c, cid)
        else:
            return _bad(t("ungueltige Angabe"))
        mem = sync(c, ch)
        m = _member(c, ch["id"], uid)
        if not m:
            return _bad("not found", 404)
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
    """{muted: bool} (nur fuer mich) und/oder {retention_s: 0|3600|...} (fuer alle; gilt fuer neue Nachrichten)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungueltige Anfrage"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if "muted" in b:
            c.execute("UPDATE chat_member SET muted=? WHERE chat_id=? AND uid=?", (1 if b["muted"] else 0, ch["id"], uid))
            _direct(c, [uid], "chat.changed", {"chat_id": ch["id"]})
        if "retention_s" in b:
            v = b["retention_s"]
            if not isinstance(v, int) or isinstance(v, bool) or v not in RETENTIONS:
                return _bad(t("ungueltige Angabe"))
            mx = retention_max()
            if mx and (v == 0 or v > mx):
                return _bad(t("Höchstens {n} Tage (Vorgabe des Servers).", n=mx // 86400))
            c.execute("UPDATE chat SET retention_s=? WHERE id=?", (v, ch["id"]))
            _changed(c, ch["id"], mem)
        ch = _load(c, ch["id"])
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, _member(c, ch["id"], uid), None, full=True)})


@router.post("/api/chats/{cid}/members")
async def api_member_add(cid: int, request: Request):
    """Freie Gruppe: Person hinzufuegen {user} (nur Ersteller/Admin). Sieht nur Nachrichten ab jetzt."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    who = str(b.get("user") or "").strip().lower()[:100]
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if ch["kind"] != "free":
            return _bad(t("Mitglieder ergeben sich hier aus der Gruppe bzw. den Freigaben der Seite."), 400)
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
        _changed(c, ch["id"], set(mem) | {who})
        mem = members(c, ch["id"])
        return _no_store({"chat": _chat_dict(c, uid, ch, mem, m, None, full=True)})


def _drop_chat(c, cid):
    """Chat ganz loeschen -> Datei-ids (Blobs nach dem Commit entfernen)."""
    fids = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE chat_id=?", (cid,))]
    c.execute("DELETE FROM chat_file WHERE chat_id=?", (cid,))
    c.execute("DELETE FROM chat WHERE id=?", (cid,))              # Mitglieder, Nachrichten, Schluessel: ON DELETE CASCADE
    return fids


def _rm_blobs(c, fids):
    def go():
        for f in fids:
            try:
                os.remove(os.path.join(CHAT_DIR, f))
            except OSError:
                pass
    if fids:
        c.after_commit(go)


@router.delete("/api/chats/{cid}/members/{who}")
def api_member_remove(cid: int, who: str, request: Request):
    """Freie Gruppe verlassen (who = ich) bzw. Person entfernen (Admin). Letzte Person geht -> Chat geloescht."""
    uid, err = _me(request)
    if err:
        return err
    who = who.strip().lower()
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
        rest = members(c, ch["id"])
        if not rest:
            _rm_blobs(c, _drop_chat(c, ch["id"]))
        elif not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'", (ch["id"],)).fetchone():
            c.execute("UPDATE chat_member SET role='admin' WHERE chat_id=? AND uid=?", (ch["id"], rest[0]))
        _changed(c, ch["id"], set(mem))
    return _no_store({"ok": True})


def recipients(c, mem):
    """Empfaenger fuer neue Nachrichten: je Mitglied mit Chat-Schluesseln dessen aktive Geraete + Backup-Schluessel."""
    keys = _keys(c, mem)
    nm = names(c, mem)
    out, want = [], set()
    for u in mem:
        k = keys.get(u)
        e = {"username": u, "name": nm.get(u, u), "ready": bool(k)}
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
        out, _ = recipients(c, mem)
    return _no_store({"chat_id": ch["id"], "recipients": out})


@router.get("/api/chats/{cid}/messages")
def api_messages(cid: int, request: Request, device: str = "", before: int = 0, after: int = 0, limit: int = PAGE):
    """Nachrichten (aelteste zuerst) mit dem fuer mich versiegelten Schluessel. ?before=<seq> aeltere, ?after=<seq> neuere."""
    uid, err = _me(request)
    if err:
        return err
    limit = max(1, min(200, limit))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        dev = _my_device(c, uid, device)
        where, args, desc = "AND m.kind='msg'", (), True
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
        devs, ids = _senders(c, rows)
        return _no_store({"chat_id": ch["id"], "messages": [_msg_dict(r, nm) for r in rows], "more": more,
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
        return _bad(t("ungueltige Anfrage"))
    mid, dev, body_ct, sig = str(b.get("id") or ""), str(b.get("sender_device") or ""), b.get("body_ct"), b.get("sig")
    kind = b.get("kind") or "msg"
    reply_to = b.get("reply_to") or None
    keys, files = b.get("keys"), b.get("files") or []
    try:
        ts = int(b.get("ts"))
    except (TypeError, ValueError, OverflowError):
        ts = 0
    if not 0 < ts < 2 ** 53:              # sonst OverflowError bei ts / 1000 (500) bzw. BIGINT-Ueberlauf
        ts = 0
    now = time.time()
    if not _HEX32.fullmatch(mid) or kind not in ("msg", "title") or not isinstance(keys, dict) or not isinstance(files, list):
        return _bad(t("ungueltige Angabe"))
    if not isinstance(body_ct, str) or len(body_ct) > MSG_MAX:
        return _bad(t("Nachricht zu groß."), 413)
    if b64d(body_ct) is None or len(b64d(body_ct)) < 24 + 16 or b64d(sig, 64) is None:
        return _bad(t("ungueltige Angabe"))
    if abs(ts / 1000 - now) > SKEW:
        return _bad(t("Die Uhrzeit dieses Geräts weicht zu stark ab."), 400)
    if reply_to is not None and not (isinstance(reply_to, str) and _HEX32.fullmatch(reply_to)):
        return _bad(t("ungueltige Angabe"))
    if len(files) > FILES_PER_MSG or any(not isinstance(f, str) or not _FID.fullmatch(f) for f in files):
        return _bad(t("ungueltige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid)
        if err:
            return err
        if len(mem) > MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=MEMBERS_MAX), 409)
        if kind == "title" and ch["kind"] != "free":
            return _bad(t("ungueltige Angabe"))
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
            return _bad(t("ungueltige Angabe"))
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
        if files:
            c.execute(f"UPDATE chat_file SET msg_seq=? WHERE id IN ({','.join('?' * len(files))})", (seq, *files))
        if kind == "title":
            old = ch["title_seq"]
            c.execute("UPDATE chat SET title_seq=? WHERE id=?", (seq, ch["id"]))
            if old:
                c.execute("DELETE FROM chat_msg WHERE seq=?", (old,))
            _changed(c, ch["id"], mem)
        else:
            c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, ch["id"]))
            c.execute("UPDATE chat_member SET last_read=? WHERE chat_id=? AND uid=?", (seq, ch["id"], uid))
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
        r = c.execute("SELECT seq, sender_uid FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg'", (mid, ch["id"])).fetchone()
        if not r:
            return _bad("not found", 404)
        if r[1] != uid:
            return _bad(t("Nur eigene Nachrichten lassen sich löschen."), 403)
        fids = [x[0] for x in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (r[0],))]
        c.execute("DELETE FROM chat_file WHERE msg_seq=?", (r[0],))
        c.execute("DELETE FROM chat_msg WHERE seq=?", (r[0],))
        _rm_blobs(c, fids)
        _direct(c, mem, "chat.deleted", {"chat_id": ch["id"], "id": mid, "seq": r[0]})
    return _no_store({"ok": True})


@router.post("/api/chats/{cid}/read")
async def api_read(cid: int, request: Request):
    """Gelesen bis {seq}; Benachrichtigungen dieses Chats werden mit gelesen. Live: chat.read an meine anderen Tabs/Geraete."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    try:
        seq = int(b.get("seq") or 0)
    except (TypeError, ValueError):
        return _bad(t("ungueltige Angabe"))
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
    return _no_store({"ok": True, "last_read": seq, "unread": total})


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
        nm = names(c, [uid])
        _direct(c, [u for u in mem if u != uid], "chat.typing", {"chat_id": ch["id"], "user": {"username": uid, "name": nm.get(uid, uid)}})
    return _no_store({"ok": True})


# --- Anhaenge (clientseitig verschluesselt; der Server sieht nur einen Blob) ---------------------------------------
@router.post("/api/chats/{cid}/files")
async def api_file_up(cid: int, request: Request):
    """Rohdaten (application/octet-stream) = nonce24 || xchacha20poly1305(datei). -> {id}. Ohne Nachricht nach 1 h geloescht."""
    uid, err = _me(request)
    if err:
        return err
    if int(request.headers.get("content-length") or 0) > FILE_MAX + 64:
        return _bad(t("Datei zu gross (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    if not _rate("file", uid, FILE_RATE):
        return _bad(t("zu schnell"), 429)
    data = await request.body()
    if len(data) > FILE_MAX + 64:
        return _bad(t("Datei zu gross (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    if len(data) < 24 + 16:
        return _bad(t("ungueltige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = _chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        used = c.execute("SELECT COALESCE(SUM(size), 0) FROM chat_file WHERE uid=?", (uid,)).fetchone()[0]
        if used + len(data) > QUOTA:
            return _bad(t("Speicher für Chat-Anhänge voll ({n} MB).", n=QUOTA // 1048576), 413)
        fid = secrets.token_urlsafe(18)
        os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(CHAT_DIR, fid)
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created) VALUES (?,?,?,?,?)", (fid, ch["id"], uid, len(data), time.time()))
    return _no_store({"id": fid, "size": len(data)})


@router.get("/api/chat/files/{fid}")
def api_file_get(fid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not enabled() or not _FID.fullmatch(fid):
        return _bad("not found", 404)
    with _db() as c:
        r = c.execute("SELECT f.chat_id, f.uid, f.msg_seq FROM chat_file f WHERE f.id=?", (fid,)).fetchone()
        if not r or not _member(c, r[0], uid) or (r[2] is None and r[1] != uid):
            return _bad("not found", 404)
        if r[2] is not None and not c.execute("SELECT 1 FROM chat_msg WHERE seq=? AND (expires_at IS NULL OR expires_at>?)",
                                              (r[2], time.time())).fetchone():
            return _bad("not found", 404)
    try:
        with open(os.path.join(CHAT_DIR, fid), "rb") as f:
            data = f.read()
    except OSError:
        return _bad("not found", 404)
    return Response(data, media_type="application/octet-stream",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "sandbox; default-src 'none'", "Content-Disposition": "attachment"})


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
        c.execute(f"DELETE FROM chat_msg WHERE seq IN ({ph})", tuple(seqs))
    for (cid,) in c.execute("SELECT id FROM chat WHERE kind='page' AND NOT EXISTS (SELECT 1 FROM note n WHERE n.id=chat.note_id)").fetchall():
        fids += _drop_chat(c, cid)                         # Seite endgueltig geloescht (Papierkorb geleert): Seiten-Chat auch
    orphan = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE msg_seq IS NULL AND created<?", (now - 3600,))]
    if orphan:
        c.execute(f"DELETE FROM chat_file WHERE id IN ({','.join('?' * len(orphan))})", tuple(orphan))
    c.execute("DELETE FROM chat_link WHERE expires<?", (now - 60,))
    _rm_blobs(c, fids + orphan)
    return len(seqs)


def delete_account(c, uid):
    """Kontoloeschung: Identitaet, Geraete, Kopplungen, Mitgliedschaften, gesendete Nachrichten und Anhaenge des Kontos;
    Direkt-Chats mit dem Konto und leere freie Gruppen ganz. Blobs werden nach dem Commit entfernt. -> Anzahl Nachrichten."""
    devs = [r[0] for r in c.execute("SELECT id FROM chat_device WHERE uid=?", (uid,))]
    rcpt = devs + ["backup:" + uid]
    for i in range(0, len(rcpt), 500):
        part = rcpt[i:i + 500]
        c.execute(f"DELETE FROM chat_msg_key WHERE recipient IN ({','.join('?' * len(part))})", tuple(part))
    fids = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE uid=? OR msg_seq IN (SELECT seq FROM chat_msg WHERE sender_uid=?)", (uid, uid))]
    if fids:
        c.execute(f"DELETE FROM chat_file WHERE id IN ({','.join('?' * len(fids))})", tuple(fids))
    c.execute("UPDATE chat SET title_seq=NULL WHERE title_seq IN (SELECT seq FROM chat_msg WHERE sender_uid=?)", (uid,))
    n = c.execute("DELETE FROM chat_msg WHERE sender_uid=?", (uid,)).rowcount
    for (cid,) in c.execute("SELECT id FROM chat WHERE kind='page' AND note_id IN (SELECT id FROM note WHERE uid=?)", (uid,)).fetchall():
        fids += _drop_chat(c, cid)                         # Seiten-Chats eigener Seiten (die Seiten gehen mit)
    chats = [r[0] for r in c.execute("SELECT chat_id FROM chat_member WHERE uid=?", (uid,))]
    c.execute("DELETE FROM chat_member WHERE uid=?", (uid,))
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
    for tbl in ("chat_identity", "chat_device", "chat_link"):
        c.execute(f"DELETE FROM {tbl} WHERE uid=?", (uid,))
    _rm_blobs(c, fids)
    return n


def counts(c, uid):
    """Admin, Benutzer-Detail: nur Anzahlen."""
    return {"chats": c.execute("SELECT COUNT(*) FROM chat_member WHERE uid=? AND left_at IS NULL", (uid,)).fetchone()[0],
            "chat_devices": c.execute("SELECT COUNT(*) FROM chat_device WHERE uid=? AND revoked_at IS NULL", (uid,)).fetchone()[0]}


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
    remember_ui_lang(uid, request)
    info = user_info(uid)
    html = tmpl.get_template("chats.html").render(title=title(), name=request.session.get("name", uid), v=ASSET_V, uid=uid,
                                                  enabled=enabled(), admin_url=ADMIN_URL if info and info["is_admin"] else "")
    headers = dict(SEC_HEADERS)
    headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("script-src 'self'", "script-src 'self' 'wasm-unsafe-eval'")
    return HTMLResponse(html, headers=headers)
