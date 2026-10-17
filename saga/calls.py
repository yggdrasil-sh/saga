"""Audio-/Video-Anrufe (2.1): 1:1 aus dem Direkt-Chat, WebRTC zwischen den Geraeten - ohne zusaetzlichen Port.

Signalisierung ueber den vorhandenen Live-Kanal (SSE, saga.live, Ereignis-Art 'direct') und normale POSTs; die Medien
laufen direkt Geraet <-> Geraet (DTLS-SRTP), bei Bedarf ueber einen TURN-Server, den der Admin eintraegt (Saga selbst
oeffnet keinen Port). Der Server vermittelt nur: wer ruft wen, welches Geraet nimmt an, Klingeldauer, Ende.

Ende-zu-Ende (Format saga-call/v1, Testvektoren tests/call_vectors.json, Gegenstueck static/chat-crypto.js):
  Jede Signalisierungs-Nachricht (SDP-Angebot/-Antwort, ICE-Kandidat, Zustand) ist ein JSON-Text `body` mit "t"
  (offer | answer | ice | state | ...). Das sendende Chat-Geraet signiert
      sig = sign_detached(LP("saga-call/v1/signal", call_id, from_uid, from_device, to_uid, to_device, body), device_sign_sk)
  und versiegelt {"v":1,"body":body,"sig":B64(sig)} mit crypto_box_seal fuer den X25519-Schluessel des ZIELgeraets.
  Der Server sieht nur Kennungen und Groessen - keine SDP, keine IP-Adressen/Kandidaten, keine DTLS-Fingerabdruecke.
  Das Zielgeraet prueft Geraete-Zertifikat (Identitaetsschluessel, TOFU wie im Chat) und Signatur; da die SDP samt
  a=fingerprint signiert ist, kann der Server keinen Man-in-the-Middle einschleusen (DTLS prueft den Fingerabdruck).

Ablauf: POST /api/calls (Angebot je Geraet des Gegenuebers) -> call.ring an das Gegenueber + Push (Art "call") ->
POST /api/calls/{id}/answer (Antwort fuer das anrufende Geraet; die anderen Geraete hoeren auf zu klingeln) ->
POST /api/calls/{id}/signal (ICE, Trickle) -> POST /api/calls/{id}/end. Ohne Annahme nach ring_s Sekunden: verpasst
(Eintrag im Chat + Benachrichtigung). Waehrend des Gespraechs melden sich beide Seiten mit /alive; bleibt das aus,
endet der Anruf ("lost"). Live-Ereignisse: call.ring {call_id, chat_id, from, from_device, media, ring_s},
call.signal {call_id, seq, from_device, to_device, sealed}, call.state {call_id, state, reason, device}.

Im Chat bleibt ein Eintrag chat_msg kind='call' mit Metadaten (unverschluesselt, nur was der Server ohnehin weiss:
Art, Ergebnis, Dauer) - Apps/Clients bekommen ihn nur mit ?calls=1 (aeltere Apps sehen ihn nicht)."""
import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from fastapi import APIRouter, Request

import auth
from db import connect as _db
from saga import bus, chat, notify
from saga.core import SECRET, _bad, _body, t
from saga.sharing import names

router = APIRouter()

MEDIA = ("audio", "video")
SEALED_MAX = 24 * 1024                 # versiegelte Signal-Nachricht (B64) - SDP mit Video ~6 KB
SIGNALS_MAX = 600                      # Signal-Nachrichten je Anruf
SIG_RATE = 240                         # je Geraet und Minute
CALL_RATE, CALL_RATE_H = 6, 40         # neue Anrufe je Konto: je Minute / je Stunde
PAIR_RATE = 3                          # an dieselbe Person je Minute
ALIVE_GAP = 45                         # so lange ohne /alive -> Verbindung gilt als verloren
MAX_S = 12 * 3600                      # laengster Anruf
KEEP_S = 600                           # beendete Anrufe (Zustand + Signale) so lange aufheben, dann weg
TICK = 2.0
_HEX32 = re.compile(r"[0-9a-f]{32}")
_URL = re.compile(r"(stuns?|turns?):[A-Za-z0-9.\-\[\]:]{1,253}(\?transport=(udp|tcp))?")
_hits = {}
_S = {"task": None}
_OWN = set()                           # Anrufe, die DIESER Prozess vermittelt (Live-Ereignisse gehen an seine Verbindungen)
ORPHAN = 30                            # fremde/verwaiste Anrufe erst so viel spaeter beenden (Neustart, zweiter Prozess)


# --- Einstellungen --------------------------------------------------------------------------------------------------
def cfg():
    return dict(auth.settings().get("calls") or {})


def enabled():
    return bool(cfg().get("enabled", True)) and chat.enabled()


def ring_s():
    try:
        return max(3, min(120, int(cfg().get("ring_s") or 45)))
    except (TypeError, ValueError):
        return 45


def urls(s, kinds):
    """Leerzeichen-/Komma-getrennte ICE-Adressen pruefen -> (gueltige, ungueltige)."""
    good, bad = [], []
    for u in (x for x in re.split(r"[\s,]+", str(s or "")) if x):
        (good if _URL.fullmatch(u) and u.split(":", 1)[0] in kinds else bad).append(u)
    return good, bad


def ice_servers(uid, now=None):
    """RTCIceServer-Liste fuer diese Person: STUN, dazu TURN mit Zugangsdaten. REST-Variante (draft-uberti-behave-turn-rest,
    coturn use-auth-secret): username = "<ablauf>:<kennung>", credential = B64(HMAC-SHA1(secret, username)). Die Kennung
    ist ein Hash (der TURN-Server erfaehrt keinen Kontonamen)."""
    c = cfg()
    out = []
    stun, _ = urls(c.get("stun"), ("stun", "stuns"))
    if stun:
        out.append({"urls": stun})
    turn, _ = urls(c.get("turn_urls"), ("turn", "turns"))
    if turn:
        if c.get("turn_mode") == "static":
            if c.get("turn_user") and c.get("turn_password"):
                out.append({"urls": turn, "username": c["turn_user"], "credential": c["turn_password"]})
        elif c.get("turn_secret"):
            try:
                ttl = max(600, min(7 * 86400, int(c.get("turn_ttl") or 43200)))
            except (TypeError, ValueError):
                ttl = 43200
            tag = hmac.new(("saga-turn|" + SECRET).encode(), uid.encode(), hashlib.sha256).hexdigest()[:16]
            user = f"{int(now or time.time()) + ttl}:{tag}"
            cred = base64.b64encode(hmac.new(c["turn_secret"].encode(), user.encode(), hashlib.sha1).digest()).decode()
            out.append({"urls": turn, "username": user, "credential": cred})
    return out


def has_turn():
    c = cfg()
    turn, _ = urls(c.get("turn_urls"), ("turn", "turns"))
    return bool(turn and ((c.get("turn_mode") == "static" and c.get("turn_user") and c.get("turn_password"))
                          or (c.get("turn_mode") != "static" and c.get("turn_secret"))))


# --- Hilfen ---------------------------------------------------------------------------------------------------------
def _rate(kind, key, n, per=60.0):
    now = time.time()
    k = (kind, key)
    h = [x for x in _hits.get(k, []) if now - x < per]
    if len(h) >= n:
        _hits[k] = h
        return False
    _hits[k] = h + [now]
    if len(_hits) > 20000:
        _hits.clear()
    return True


def _me(request):
    uid, err = chat._me(request)
    if err:
        return None, err
    if not enabled():
        return None, _bad(t("Anrufe sind auf diesem Server abgeschaltet."), 403)
    return uid, None


C_COLS = "id, chat_id, caller, callee, caller_device, callee_device, media, state, reason, created, answered, ended, seen_a, seen_b, nsig"


def _row(r):
    return dict(zip([x.strip() for x in C_COLS.split(",")], r)) if r else None


def _load(c, cid, lock=False):
    if not isinstance(cid, str) or not _HEX32.fullmatch(cid):
        return None
    return _row(c.execute(f"SELECT {C_COLS} FROM chat_call WHERE id=?" + (" FOR UPDATE" if lock else ""), (cid,)).fetchone())


def _mine(c, uid, cid, lock=False):
    """Anruf, an dem uid beteiligt ist -> (anruf, fehler). Sonst 404 (den Anruf "gibt es nicht")."""
    call = _load(c, cid, lock)
    if not call or uid not in (call["caller"], call["callee"]):
        return None, _bad("not found", 404)
    return call, None


def _active_devices(c, uid):
    return {r[0] for r in c.execute("SELECT id FROM chat_device WHERE uid=? AND revoked_at IS NULL", (uid,))}


def _my_device(c, uid, did):
    return isinstance(did, str) and _HEX32.fullmatch(did) and bool(
        c.execute("SELECT 1 FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (did, uid)).fetchone())


def _sealed_map(m, allowed=None):
    """{geraet: versiegelt} pruefen -> dict oder None."""
    if not isinstance(m, dict) or not m or len(m) > chat.DEVICES_MAX:
        return None
    for k, v in m.items():
        if not _HEX32.fullmatch(str(k)) or (allowed is not None and k not in allowed):
            return None
        raw = chat.b64d(v, maxlen=SEALED_MAX) if isinstance(v, str) else None
        if raw is None or len(raw) < 48 + 2:
            return None
    return m


def _dict(c, call, uid, nm=None):
    me_caller = call["caller"] == uid
    peer = call["callee"] if me_caller else call["caller"]
    nm = nm or names(c, [peer])
    now = time.time()
    return {"id": call["id"], "chat_id": call["chat_id"], "media": call["media"], "state": call["state"], "reason": call["reason"],
            "role": "caller" if me_caller else "callee", "peer": {"username": peer, "name": nm.get(peer) or t("Gelöschtes Konto"),
                                                                    "avatar": nm.avatar(peer)},
            "caller_device": call["caller_device"], "callee_device": call["callee_device"], "created": call["created"],
            "answered": call["answered"], "ended": call["ended"], "ring_s": ring_s(),
            "expires_in": max(0, round(call["created"] + ring_s() - now, 1)) if call["state"] == "ringing" else None}


def _emit(c, uids, typ, data):
    chat._direct(c, uids, typ, data)


def _busy(c, uid):
    """Laeuft fuer uid gerade ein Anruf (klingelt oder Gespraech)?"""
    return bool(c.execute("SELECT 1 FROM chat_call WHERE state IN ('ringing','active') AND (caller=? OR callee=?) LIMIT 1",
                          (uid, uid)).fetchone())


def _log(c, call, status, now, end=None):
    """Eintrag im Chat (chat_msg kind='call'): nur Metadaten, die der Server ohnehin kennt. end: Gespraechsende fuer die
    Dauer (verlorene Verbindung: letztes Lebenszeichen statt Zeitpunkt der Erkennung)."""
    ch = chat._load(c, call["chat_id"])
    if not ch:
        return None
    dur = max(0, int((end or now) - call["answered"])) if call["answered"] and status == "ended" else None
    body = json.dumps({"v": 1, "type": "call", "call": call["id"], "media": call["media"], "status": status, "dur": dur,
                       "from": call["caller"], "to": call["callee"]}, separators=(",", ":"))
    eff, mx = ch["retention_s"], chat.retention_max()
    eff = min(eff, mx) if eff and mx else (eff or mx)
    mid = secrets.token_hex(16)
    seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size) "
                    "VALUES (?,?,'call',?,?,?,?,?,?,'',NULL,0) RETURNING seq",
                    (mid, ch["id"], call["caller"], call["caller_device"], int(call["created"] * 1000), now, now + eff if eff else None,
                     body)).fetchone()[0]
    c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, ch["id"]))
    _emit(c, chat.members(c, ch["id"]), "chat.message", {"chat_id": ch["id"], "seq": seq, "id": mid, "sender": call["caller"], "kind": "call"})
    return seq


def _finish(c, call, reason, now, by=None):
    """Anruf beenden (in der Transaktion, Zeile gesperrt bzw. Zustand schon geprueft): Zustand, Chat-Eintrag, verpasst,
    Ereignis an beide. reason: hangup | declined | busy | missed | cancelled | failed | lost | timeout | gone."""
    n = c.execute("UPDATE chat_call SET state='ended', reason=?, ended=? WHERE id=? AND state<>'ended'", (reason, now, call["id"])).rowcount
    if not n:
        return False
    was = call["state"]
    if was == "ringing":
        status = {"declined": "declined", "busy": "busy"}.get(reason, "missed")
    else:
        status = "failed" if reason == "failed" and not call.get("_connected") else "ended"
    seen = [x for x in (call.get("seen_a"), call.get("seen_b")) if x]
    _log(c, call, status, now, min(seen) if reason == "lost" and seen else None)
    if status in ("missed", "busy"):
        notify.create(c, call["callee"], "call.missed", call["caller"], data={"chat": call["chat_id"], "call": call["id"]})
    _emit(c, [call["caller"], call["callee"]], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "ended",
                                                               "reason": reason, "status": status, "by": by})
    print(f"call: {call['id'][:8]} beendet ({was} -> {reason}/{status})", flush=True)
    return True


# --- Endpunkte ------------------------------------------------------------------------------------------------------
@router.get("/api/calls/config")
def api_config(request: Request):
    """Fuer Browser und Apps: an/aus, ICE-Server (mit frischen TURN-Zugangsdaten), Richtlinie, Klingeldauer."""
    uid, err = chat._me(request)
    if err:
        return err
    on = enabled()
    c = cfg()
    return chat._no_store({"enabled": on, "ice_servers": ice_servers(uid) if on else [], "relay_only": bool(c.get("relay_only")) and has_turn(),
                           "turn": has_turn(), "stun": bool(urls(c.get("stun"), ("stun", "stuns"))[0]), "ring_s": ring_s(),
                           "alive_s": 15, "max_s": MAX_S, "format": "saga-call/v1"})


@router.get("/api/calls/active")
def api_active(request: Request):
    """Laufende/klingelnde Anrufe der Person (nach dem Laden einer Seite, App aus dem Hintergrund)."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c:
        rows = [_row(r) for r in c.execute(f"SELECT {C_COLS} FROM chat_call WHERE state IN ('ringing','active') AND (caller=? OR callee=?) "
                                           "ORDER BY created DESC LIMIT 5", (uid, uid)).fetchall()]
        now = time.time()
        rows = [r for r in rows if not (r["state"] == "ringing" and r["created"] + ring_s() < now)]
        nm = names(c, [r["caller"] for r in rows] + [r["callee"] for r in rows])
        return chat._no_store({"calls": [_dict(c, r, uid, nm) for r in rows]})


@router.post("/api/calls")
async def api_call(request: Request):
    """Anrufen: {id, chat_id, media:'audio'|'video', device, signals:{geraet_des_gegenuebers: versiegeltes Angebot}}.
    signals muss GENAU die aktiven Chat-Geraete des Gegenuebers abdecken - sonst 409 mit {devices}."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    cid, media, dev = str(b.get("id") or ""), b.get("media"), str(b.get("device") or "")
    try:
        chat_id = int(b.get("chat_id") or 0)
    except (TypeError, ValueError):
        chat_id = 0
    if not _HEX32.fullmatch(cid) or media not in MEDIA or not chat_id:
        return _bad(t("ungültige Angabe"))
    now = time.time()
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, chat_id, do_sync=False)
        if err:
            return err
        if ch["kind"] != "dm":
            return _bad(t("Anrufe gibt es nur im Direkt-Chat."), 400)
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        a, _, z = (ch["dm_key"] or "").partition("|")
        peer = z if a == uid else a
        if peer not in mem or not chat.may_contact(c, uid, peer):
            return _bad(t("Diese Person ist nicht erreichbar."), 404)
        devs = _active_devices(c, peer)
        if not devs:
            return _bad(t("{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.",
                          name=names(c, [peer]).get(peer) or peer), 409)
        sig = _sealed_map(b.get("signals"))
        if sig is None:
            return _bad(t("ungültige Angabe"))
        if set(sig) != devs:
            keys = chat._keys(c, [peer]).get(peer)
            return chat._no_store({"error": t("Die Geräte des Gegenübers haben sich geändert – bitte erneut anrufen."),
                                   "devices": [d for d in (keys or {}).get("devices", []) if not d["revoked"]]}, 409)
        if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
            return _bad(t("Diesen Anruf gibt es schon."), 409)
        inc = c.execute("SELECT id FROM chat_call WHERE state='ringing' AND caller=? AND callee=? LIMIT 1", (peer, uid)).fetchone()
        if inc:                                            # Rueckruf, waehrend der Anruf des Gegenuebers noch klingelt
            return chat._no_store({"error": t("{name} ruft dich gerade an – nimm den Anruf an.", name=names(c, [peer]).get(peer) or peer),
                                   "incoming": inc[0]}, 409)
        if _busy(c, uid):
            return _bad(t("Du telefonierst gerade schon."), 409)
        if not _rate("call", uid, CALL_RATE) or not _rate("call-h", uid, CALL_RATE_H, 3600) or not _rate("pair", uid + "|" + peer, PAIR_RATE):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a) "
                  "VALUES (?,?,?,?,?,?,'ringing',?,?)", (cid, ch["id"], uid, peer, dev, media, now, now))
        _OWN.add(cid)
        call = _load(c, cid)
        if _busy_other(c, peer, cid):
            _finish(c, call, "busy", now)
            return chat._no_store({"ok": False, "busy": True, "error": t("Besetzt – {name} telefoniert gerade.",
                                                                         name=names(c, [peer]).get(peer) or peer)}, 409)
        c.executemany("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?)",
                      [(cid, dev, k, v, now) for k, v in sorted(sig.items())])
        c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (len(sig), cid))
        nm = names(c, [uid])
        _emit(c, [peer], "call.ring", {"call_id": cid, "chat_id": ch["id"], "media": media, "from_device": dev, "ring_s": ring_s(),
                                       "from": {"username": uid, "name": nm.get(uid) or uid, "avatar": nm.avatar(uid)}})
        # Handy/Desktop: Art "call" (hohe Prioritaet, kurze Lebensdauer) - nie Inhalt; ohne Namen, wenn so eingestellt
        muted = c.execute("SELECT muted FROM chat_member WHERE chat_id=? AND uid=?", (ch["id"], peer)).fetchone()
        if not (muted and muted[0]):
            bus.fire("push.send", c, uid=peer, kind="call", actor=uid, data={"chat": ch["id"], "call": cid})
        print(f"call: {cid[:8]} klingelt ({media}, Chat {ch['id']}, {len(sig)} Geraet(e))", flush=True)
        return chat._no_store({"ok": True, "call": _dict(c, call, uid)})


def _busy_other(c, uid, cid):
    return bool(c.execute("SELECT 1 FROM chat_call WHERE state IN ('ringing','active') AND (caller=? OR callee=?) AND id<>? LIMIT 1",
                          (uid, uid, cid)).fetchone())


@router.get("/api/calls/{cid}")
def api_get(cid: str, request: Request, device: str = "", after: int = 0):
    """Zustand + Signale fuer MEIN Geraet (?device=, ?after=<seq>) - z. B. nach dem Oeffnen aus einer Mitteilung."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c:
        call, err = _mine(c, uid, cid)
        if err:
            return err
        sigs = []
        if _HEX32.fullmatch(device or "") and _my_device(c, uid, device):
            sigs = [{"seq": r[0], "from_device": r[1], "to_device": device, "sealed": r[2]} for r in c.execute(
                "SELECT seq, from_device, sealed FROM chat_call_signal WHERE call_id=? AND to_device=? AND seq>? ORDER BY seq LIMIT 400",
                (call["id"], device, max(0, min(after, 2 ** 62)))).fetchall()]
        return chat._no_store({"call": _dict(c, call, uid), "signals": sigs})


@router.post("/api/calls/{cid}/answer")
async def api_answer(cid: str, request: Request):
    """Annehmen: {device, signals:{anrufendes_geraet: versiegelte Antwort}}. Nur einmal - das erste Geraet gewinnt."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    now = time.time()
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["callee"] != uid:
            return _bad(t("ungültige Angabe"))
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        if call["state"] != "ringing" or call["created"] + ring_s() < now:
            return chat._no_store({"error": t("Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen."),
                                   "call": _dict(c, call, uid)}, 409)
        sig = _sealed_map(b.get("signals"), {call["caller_device"]})
        if sig is None:
            return _bad(t("ungültige Angabe"))
        c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=? WHERE id=?",
                  (dev, now, now, now, call["id"]))
        seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                        (call["id"], dev, call["caller_device"], sig[call["caller_device"]], now)).fetchone()[0]
        c.execute("UPDATE chat_call SET nsig=nsig+1 WHERE id=?", (call["id"],))
        _emit(c, [call["caller"]], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": call["caller_device"],
                                                   "sealed": sig[call["caller_device"]]})
        _emit(c, [call["caller"], call["callee"]], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active",
                                                                   "device": dev})
        print(f"call: {call['id'][:8]} angenommen", flush=True)
        return chat._no_store({"ok": True, "call": _dict(c, _load(c, call["id"]), uid)})


@router.post("/api/calls/{cid}/decline")
async def api_decline(cid: str, request: Request):
    """Ablehnen (nur das Gegenueber, solange es klingelt): {reason: 'declined'|'busy'}."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    reason = b.get("reason") if b.get("reason") in ("declined", "busy") else "declined"
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["callee"] != uid or call["state"] != "ringing":
            return chat._no_store({"ok": False, "call": _dict(c, call, uid)}, 409)
        _finish(c, call, reason, time.time(), by=uid)
    return chat._no_store({"ok": True})


@router.post("/api/calls/{cid}/end")
async def api_end(cid: str, request: Request):
    """Auflegen/Abbrechen (beide Seiten): {reason: 'hangup'|'failed', connected: bool}. Der Anrufer bricht beim Klingeln ab
    -> beim Gegenueber "verpasst"."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    reason = b.get("reason") if b.get("reason") in ("hangup", "failed") else "hangup"
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["state"] == "ended":
            return chat._no_store({"ok": True, "already": True})
        if call["state"] == "ringing":
            reason = "cancelled" if uid == call["caller"] else "declined"
        call["_connected"] = bool(b.get("connected"))
        _finish(c, call, reason, time.time(), by=uid)
    return chat._no_store({"ok": True})


@router.post("/api/calls/{cid}/signal")
async def api_signal(cid: str, request: Request):
    """Signal weiterreichen (ICE-Kandidaten, neue Angebote, Zustand): {device, signals:{zielgeraet: versiegelt}}.
    Anrufer -> alle Geraete des Gegenuebers (vor dem Annehmen) bzw. das annehmende Geraet; Gegenueber -> anrufendes Geraet."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    now = time.time()
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["state"] == "ended":
            return chat._no_store({"error": t("Der Anruf ist beendet."), "state": "ended"}, 409)
        if uid == call["caller"]:
            if dev != call["caller_device"]:
                return _bad(t("ungültige Angabe"), 403)
            allowed = {call["callee_device"]} if call["callee_device"] else _active_devices(c, call["callee"])
            to_uid = call["callee"]
        else:
            if call["state"] != "active" or dev != call["callee_device"]:
                return _bad(t("ungültige Angabe"), 403)
            allowed, to_uid = {call["caller_device"]}, call["caller"]
        if not _my_device(c, uid, dev):                    # inzwischen abgemeldetes/gesperrtes Geraet
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        sig = _sealed_map(b.get("signals"), allowed)
        if sig is None:
            return _bad(t("ungültige Angabe"))
        if call["nsig"] + len(sig) > SIGNALS_MAX or not _rate("sig", dev, SIG_RATE):
            return _bad(t("Zu viele Signale – bitte kurz warten."), 429)
        out = []
        for k, v in sorted(sig.items()):
            seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                            (call["id"], dev, k, v, now)).fetchone()[0]
            out.append({"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": k, "sealed": v})
        c.execute("UPDATE chat_call SET nsig=nsig+?, " + ("seen_a" if uid == call["caller"] else "seen_b") + "=? WHERE id=?",
                  (len(sig), now, call["id"]))
        for ev in out:
            _emit(c, [to_uid], "call.signal", ev)
    return chat._no_store({"ok": True, "n": len(out)})


@router.post("/api/calls/{cid}/alive")
async def api_alive(cid: str, request: Request):
    """Lebenszeichen waehrend Klingeln/Gespraech (alle ~15 s). -> Zustand (beendet: Client legt auf)."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c, c:
        call, err = _mine(c, uid, cid)
        if err:
            return err
        if call["state"] != "ended":
            c.execute("UPDATE chat_call SET " + ("seen_a" if uid == call["caller"] else "seen_b") + "=? WHERE id=?", (time.time(), call["id"]))
        return chat._no_store({"state": call["state"], "reason": call["reason"]})


# --- Hintergrund: Klingeldauer, verlorene Verbindungen, Aufraeumen -------------------------------------------------
def tick(now=None):
    """-> Anzahl beendeter Anrufe. Klingelt zu lange (verpasst), Anrufer weg (abgebrochen), keine Lebenszeichen (lost)."""
    now = now or time.time()
    n = 0
    rs = ring_s()
    with _db() as c, c:
        # Geraet waehrend des Anrufs abgemeldet/gesperrt (Chat "Geraete"): Anruf sofort beenden - ein entferntes Geraet
        # darf weder weiter signalisieren noch ein Gespraech offen halten (gilt fuer jeden Prozess, kein Live-Ereignis noetig:
        # die Clients erfahren es spaetestens ueber /alive)
        for call in [_row(r) for r in c.execute(
                f"SELECT {C_COLS} FROM chat_call WHERE state<>'ended' AND EXISTS (SELECT 1 FROM chat_device d "
                "WHERE d.id IN (chat_call.caller_device, chat_call.callee_device) AND d.revoked_at IS NOT NULL) FOR UPDATE SKIP LOCKED").fetchall()]:
            call["_connected"] = call["state"] == "active"
            n += bool(_finish(c, call, "gone", now))
            _OWN.discard(call["id"])
        rows = [_row(r) for r in c.execute(
            f"SELECT {C_COLS} FROM chat_call WHERE (state='ringing' AND (created<? OR seen_a<?)) "
            "OR (state='active' AND (LEAST(seen_a, seen_b)<? OR answered<?)) FOR UPDATE SKIP LOCKED",
            (now - rs, now - ALIVE_GAP, now - ALIVE_GAP, now - MAX_S)).fetchall()]
        for call in rows:
            # Live-Ereignisse kommen nur bei Verbindungen DIESES Prozesses an: eigene Anrufe sofort, andere (Neustart,
            # zweiter Prozess an derselben Datenbank) erst ORPHAN Sekunden spaeter
            if call["id"] not in _OWN and not (call["created"] < now - rs - ORPHAN if call["state"] == "ringing"
                                               else min(call["seen_a"] or 0, call["seen_b"] or 0) < now - ALIVE_GAP - ORPHAN
                                               or call["answered"] < now - MAX_S):
                continue
            if call["state"] == "ringing":
                reason = "timeout" if call["created"] < now - rs else "cancelled"
            else:
                reason = "lost"
                call["_connected"] = True
            n += bool(_finish(c, call, reason, now))
            _OWN.discard(call["id"])
        c.execute("DELETE FROM chat_call WHERE state='ended' AND ended<?", (now - KEEP_S,))
        if len(_OWN) > 5000:
            _OWN.clear()
    return n


async def _loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg: spaeter weiter
            print(f"call: {type(ex).__name__}: {ex}", flush=True)
        await asyncio.sleep(TICK)


def start():
    if _S["task"] is None:
        _S["task"] = asyncio.create_task(_loop())


def stop():
    if _S["task"] is not None:
        _S["task"].cancel()
        _S["task"] = None


# Benachrichtigung "Verpasster Anruf": Titel/Link wie bei Chat-Nachrichten (nur fuer aktuelle Mitglieder)
notify.RENDER["call.missed"] = chat._notif
