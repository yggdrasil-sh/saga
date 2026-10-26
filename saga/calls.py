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
Art, Ergebnis, Dauer) - Apps/Clients bekommen ihn nur mit ?calls=1 (aeltere Apps sehen ihn nicht).

2.4 (Etappe E0, "saga-call v3"): Versiegelung und Signatur bleiben v1/v2; neu sind nur Felder im (fuer den Server
undurchsichtigen) Body - pc, caps, n, Neuverhandlung (offer/answer auch waehrend des Gespraechs, "Perfect Negotiation"),
ICE-Restart, state.want, bye (Vertrag: docs/APP-API.md "Anrufe: saga-call v3"). Der Server reicht dafuer beliebig viele
Signale in beide Richtungen weiter (Budget waechst mit der Gespraechsdauer, sig_budget) und merkt sich die vom Geraet
gemeldeten Faehigkeiten nur als Hinweis (caps_a/caps_b, nicht sicherheitsrelevant - massgeblich sind die signierten caps
im Body). GET /api/calls/config meldet features und limits.

2.4 (Etappe E3, "Bildschirm v2"): features enthaelt screen2 - Bildschirm als eigene Videospur ueber die Neuverhandlung;
am Server aendert sich sonst nichts (Body bleibt undurchsichtig, keine Medien durch Python).

2.4 (Etappen E4/E5, Raum-Modell, Vertrag docs/APP-API.md "Anrufe: Mehrgeraete und Gruppen"): Jeder Anruf hat Endpunkte
je Geraet (chat_call_leg: member | companion | handover). Ein 1:1-Anruf ist der Raum mit zwei Endpunkten und laeuft genau
wie bisher; dazu kommen weitere eigene Geraete (POST /join als Begleitgeraet ohne Ton oder als Uebernahme mit replaces)
und Gruppenanrufe in Gruppen-, freien und Seiten-Chats (Mesh: je Geraete-Paar eine PeerConnection, pc = sortiertes Paar,
der Hinzukommende bietet allen an - E2E wie bisher: DTLS-SRTP je Paar, Fingerabdruecke in der signierten SDP). Grenzen:
ENDPOINTS_VIDEO/ENDPOINTS_AUDIO Endpunkte (eigene Zusatzgeraete zaehlen mit, der Admin kann nur senken). Beitreten nur,
wenn ALLE Endpunkte "multi" melden (2.1/2.3 und E0-Clients bleiben 1:1). Signale gehen sternfoermig ueber den Server,
der den Anruf fuehrt (lead; 1:1: Server der anrufenden Person wie 2.3, Gruppe: Heim des Chats) - versiegelt je Zielgeraet,
der Server liest nichts. Live: call.roster {call_id, legs}, call.invite (Gruppe klingelt), call.state wie bisher.
Medienbytes laufen nie durch Python.

2.5 (Gjallarhorn P4, Vertrag docs/HORN-E2E.md): Frame-Schluessel je sendendem Geraet gehen als Body-Typen fkey/fkey-ack
im selben versiegelten Signalweg (POST /signal, lokal saga-call/v1 bzw. v2 mit Handles, ueber Servergrenzen Auftrag
signal) - der Server sieht weder Typ noch Schluessel, nur Kennungen und Groessen, und gibt sie nie an einen Knoten.
Feature-Flag calls.horn (Standard aus): features + "horn", GET /api/calls/config "e2e": "saga-horn/v1".

2.5 (Gjallarhorn P3, saga/horn.py): Gruppenanrufe in Chats mit mehr Mitgliedern als die Mesh-Grenze laufen ueber einen
Knoten (topo 'horn'), wenn das Flag an ist, das anrufende Geraet "horn" meldet und ein Knoten bereit ist - sonst Mesh wie
2.4. Beitritt ohne Mesh-Angebote; Antworten von POST /api/calls, /join und POST /api/calls/{id}/horn tragen horn.candidates
(bis 3 Knoten mit je eigenen, 60 s gueltigen Medien-Token). Grenzen nach Weg: 12 Video / 50 Ton ueber TCP, 25 / 100 mit
UDP am Knoten. Die Teilnehmerliste traegt je Endpunkt hp (Kennung des Geraets am Knoten, fuer die Zuordnung der Spuren).
Verlassen/Ende: der Knoten beendet die Beine (kick/close ueber den Steuerkanal). Foederierte Gruppen bleiben bis P10 Mesh.

2.5 ("Alle Anrufe ueber Knoten", Admin-Einstellung calls.horn_all, Standard aus; Vertrag docs/APP-API.md "1:1 ueber Knoten"): Auch
1:1-Anrufe koennen ueber einen Knoten laufen - die Geraete sehen gegenseitig keine IP-Adressen. GET /api/calls/config
meldet dann horn_dm: true. Ein Client mit "horn" + "multi" ruft im Direkt-Chat mit {via: "horn"} OHNE signals an; der
Server legt einen Raum-Anruf an (kind 'group', topo 'horn', callee = Gegenueber - daran erkennt man den 1:1-Knoten-Anruf,
_dmh) und klingelt per call.invite {dm: true} + Push "call.group" mit dm: true (1:1-Texte, Name der anrufenden Person).
Annehmen = POST /join, Ablehnen beendet den Anruf ("declined"), Besetzt/Klingeldauer/Ende/Eintrag im Chat wie 1:1
(Eintrag ohne group/n, from/to wie 1:1). Geht es nicht (Einstellung aus, kein Knoten bereit, Geraet ohne horn/multi,
Gegenueber ohne Geraet mit "horn", anderer Server ohne horn_dm), antwortet der Server 409 {fallback: "direct", horn_block:
off|caps|node|peer|fed} - es ist nichts angelegt, der Client ruft mit derselben id wie bisher direkt an. Aeltere Clients
schicken kein via und rufen weiter direkt an; ankommende Knoten-Anrufe sehen 2.4-Geraete als Gruppenanruf (Beitritt:
"braucht Saga 2.5"). Foederierte Direkt-Chats (Faehigkeit horn_dm bei beiden Servern): Auftrag dmhorn - der Server der
anrufenden Person fuehrt den Anruf, der andere legt eine Kopie an und klingelt (saga/fed_calls.py dm_horn_start,
op_dmhorn); Ablehnen dort -> end an den fuehrenden Server (dm_ended)."""
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
from saga import bus, chat, horn, horn_bridge, horn_web, notify
from saga.core import SECRET, _bad, _body, local_ref, t
from saga import sharing
from saga.sharing import names

router = APIRouter()

MEDIA = ("audio", "video")
SEALED_MAX = 24 * 1024                 # versiegelte Signal-Nachricht (B64) - SDP mit Video ~6 KB
SIGNALS_MAX = 600                      # Signal-Nachrichten je Anruf (Grundbudget bis zur Annahme und in der ersten Minute)
SIG_PER_MIN = 60                       # 2.4: + so viele je angefangener Gespraechsminute (Neuverhandlung, ICE-Restart, Zustand)
SIGNALS_HARD = 6000                    # 2.4: harte Obergrenze je Anruf (auch bei sehr langen Gespraechen)
FEATURES = ("reneg", "pip_hint", "screen2", "multi", "group")   # 2.4: GET /api/calls/config -> features (E0: Neuverhandlung,
#                                        PiP-Hinweis want; E3: Bildschirm als zweite Videospur; E4: weitere eigene Geraete
#                                        (join/leave, Begleitgeraet, Uebernahme); E5: Gruppenanrufe als Mesh)
ENDPOINTS_VIDEO, ENDPOINTS_AUDIO = 4, 6  # 2.4 Mesh: Endpunkte (Geraete) je Anruf mit Video / nur Ton - eigene Zusatzgeraete
#                                        zaehlen mit; der Admin kann nur senken (Einstellung calls.endpoints_video/_audio)
ROLES = ("member", "companion", "handover")
RING_SILENT = 8                        # Gruppen mit mehr Mitgliedern: kein Klingeln, nur stiller Hinweis "Anruf laeuft"
ROSTER_EVERY = 60                      # Server, der einen Gruppenanruf fuehrt: Teilnehmerliste so oft an die anderen Server
MIRROR_GAP = 150                       # Kopie eines Gruppenanrufs ohne Nachricht vom fuehrenden Server -> beendet
CAPS_MAX, _CAP = 16, re.compile(r"[a-z0-9_]{1,16}")
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
FED = None                             # 2.3: saga.fed_calls (Anrufe ueber Servergrenzen) setzt sich hier ein
KASKADE = horn_bridge                  # 2.5 P9: Bruecken zwischen Knoten (Abgleich startet mit dem ersten verbundenen Knoten)


# --- Einstellungen --------------------------------------------------------------------------------------------------
def cfg():
    return dict(auth.settings().get("calls") or {})


def enabled():
    return bool(cfg().get("enabled", True)) and chat.enabled()


def horn_on():
    """2.5 Gjallarhorn (Feature-Flag calls.horn, Standard aus): Gruppenanrufe ueber Knoten mit Frame-E2E saga-horn/v1.
    Solange aus, meldet der Server die Faehigkeit nicht (features, Foederation CAPS2) und alles bleibt Mesh wie 2.4."""
    return bool(cfg().get("horn"))


def horn_dm_on():
    """2.5: Admin-Einstellung "Alle Anrufe ueber Knoten (auch 1:1)" (calls.horn_all) bei eingeschaltetem Gjallarhorn."""
    return horn_on() and bool(cfg().get("horn_all"))


def _dmh(call):
    """2.5: 1:1-Anruf ueber einen Knoten (Raum-Anruf im Direkt-Chat, callee gesetzt; normale Gruppenanrufe haben callee '')."""
    return bool(call) and call.get("kind") == "group" and bool(call.get("callee"))


def _dmf(call):
    return {"dm": True} if _dmh(call) else {}


def features():
    return list(FEATURES) + (["horn"] if horn_on() else [])


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


def limit(media):
    """Hoechstzahl Endpunkte (Geraete) je Anruf: Mesh-Grenzen, vom Admin nur zu senken (mindestens 2 = 1:1)."""
    c = cfg()
    top = ENDPOINTS_VIDEO if media == "video" else ENDPOINTS_AUDIO
    try:
        v = int(c.get("endpoints_video" if media == "video" else "endpoints_audio") or top)
    except (TypeError, ValueError):
        v = top
    return max(2, min(top, v))


def is_horn(call):
    """2.5: laeuft der Anruf ueber einen Gjallarhorn-Knoten?"""
    return bool(call) and call.get("topo") == "horn"


def climit(c, call):
    """Hoechstzahl Endpunkte dieses Anrufs: Mesh-Grenze (limit) bzw. ueber Knoten nach Weg (2.5: TCP 12/50, UDP 25/100)."""
    if is_horn(call):
        v, a = horn.limits(c, call["id"])
        return v if call["media"] == "video" else a
    return limit(call["media"])


def horn_wanted(c, ch, media, caps, why=None):
    """2.5: neuen Gruppenanruf ueber einen Knoten fuehren? Flag an, Geraet meldet "horn", Chat hat mehr Mitglieder als die
    Mesh-Grenze (bzw. calls.horn_from, mindestens 3) und ein Knoten ist bereit. P10: im foederierten Chat (wir sind das
    Heim) zusaetzlich alle beteiligten Server mit "horn" und je Server ein nutzbarer Knoten (geteilt oder eigener) -
    sonst Mesh; why (Liste) bekommt dann den Hinweis fuer die Clients."""
    if not horn_on() or not _has(caps, "horn"):
        return False
    try:
        frm = int(cfg().get("horn_from") or 0)
    except (TypeError, ValueError):
        frm = 0
    need = max(3, frm) if frm else limit(media) + 1
    if len(chat.members(c, ch["id"])) < need or not horn.any_ready(c):
        return False
    if chat.is_fed(ch):
        if FED is None or ch["home_peer"] is not None:
            return False
        ok, msg = FED.horn_fed_ok(c, ch)
        if not ok and msg and why is not None:
            why.append(msg)
        return ok
    return True


def horn_block(c, call, dev, role="member", dev_class="desktop", avoid=()):
    """Kandidaten fuer dieses Geraet (Antwort von POST /api/calls, /join, /horn). 2.5 P5: saga/horn_web.py - faellt der
    Knoten des Anrufs aus, die besten bereiten (Live-Umschalten); avoid = vom Geraet gemiedene Knoten."""
    return {"e2e": "saga-horn/v1", "ttl": horn.TOKEN_TTL, "candidates": horn_web.candidates(c, call, dev, role, dev_class, avoid)}


def _dev_class(b):
    return "mobile" if (b or {}).get("dev_class") == "mobile" else "desktop"


# --- Hilfen ---------------------------------------------------------------------------------------------------------
def sig_budget(call, now=None):
    """Wie viele Signal-Nachrichten dieser Anruf insgesamt haben darf: Grundbudget, dazu SIG_PER_MIN je angefangener
    Minute seit der Annahme (Neuverhandlung, ICE-Restart bei Netzwechsel, Zustandswechsel), hoechstens SIGNALS_HARD."""
    ans = call.get("answered")
    mins = int(((now or time.time()) - ans) // 60) + 1 if ans else 0
    return min(SIGNALS_HARD, SIGNALS_MAX + SIG_PER_MIN * mins)


def caps_in(v):
    """Vom Geraet gemeldete Faehigkeiten (nur Hinweis) -> JSON-Text einer Liste kurzer Namen; Ungueltiges faellt weg."""
    if not isinstance(v, list):
        return "[]"
    out = []
    for x in v[:4 * CAPS_MAX]:
        if isinstance(x, str) and _CAP.fullmatch(x) and x not in out:
            out.append(x)
    return json.dumps(out[:CAPS_MAX], separators=(",", ":"))


def caps_out(s):
    try:
        v = json.loads(s or "[]")
    except ValueError:
        return []
    return v if isinstance(v, list) else []


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


C_COLS = ("id, chat_id, caller, callee, caller_device, callee_device, media, state, reason, created, answered, ended, seen_a, seen_b, nsig, "
          "peer_id, caps_a, caps_b, kind, v, lead, topo")


def _row(r):
    return dict(zip([x.strip() for x in C_COLS.split(",")], r)) if r else None


def _load(c, cid, lock=False):
    if not isinstance(cid, str) or not _HEX32.fullmatch(cid):
        return None
    return _row(c.execute(f"SELECT {C_COLS} FROM chat_call WHERE id=?" + (" FOR UPDATE" if lock else ""), (cid,)).fetchone())


def _part(c, uid, call):
    """Gehoert uid zu diesem Anruf? 1:1: anrufende oder angerufene Person; Gruppe: aktuelles Mitglied des Chats."""
    if call["kind"] == "group":
        m = chat._member(c, call["chat_id"], uid)
        return bool(m) and (m.get("state") or "ok") == "ok"
    return uid in (call["caller"], call["callee"])


def _mine(c, uid, cid, lock=False):
    """Anruf, an dem uid beteiligt ist -> (anruf, fehler). Sonst 404 (den Anruf "gibt es nicht")."""
    call = _load(c, cid, lock)
    if not call or not _part(c, uid, call):
        return None, _bad("not found", 404)
    return call, None


# --- Raum-Modell (2.4 E4/E5): Endpunkte je Geraet -----------------------------------------------------------------
L_COLS = "device, who, peer_id, role, state, replaces, caps, name, joined, left_at, seen, reason, node"


def _legs(c, cid, active=True):
    """Endpunkte eines Anrufs (aktive bzw. alle) in Beitrittsreihenfolge."""
    return [dict(zip([x.strip() for x in L_COLS.split(",")], r)) for r in c.execute(
        f"SELECT {L_COLS} FROM chat_call_leg WHERE call_id=?" + (" AND state='active'" if active else "") + " ORDER BY joined NULLS FIRST, device",
        (cid,)).fetchall()]


def _leg_put(c, cid, dev, who, role="member", peer_id=None, caps="[]", replaces=None, name="", now=None):
    """Endpunkt anlegen bzw. (wieder) aktiv setzen."""
    now = now or time.time()
    c.execute("INSERT INTO chat_call_leg (call_id, device, who, peer_id, role, state, replaces, caps, name, joined, seen) "
              "VALUES (?,?,?,?,?,'active',?,?,?,?,?) ON CONFLICT (call_id, device) DO UPDATE SET who=EXCLUDED.who, peer_id=EXCLUDED.peer_id, "
              "role=EXCLUDED.role, state='active', replaces=EXCLUDED.replaces, caps=EXCLUDED.caps, "
              "name=CASE WHEN EXCLUDED.name<>'' THEN EXCLUDED.name ELSE chat_call_leg.name END, joined=EXCLUDED.joined, left_at=NULL, "
              "seen=EXCLUDED.seen, reason=NULL", (cid, dev, who, peer_id, role if role in ROLES else "member", replaces, caps, (name or "")[:80], now, now))


def _dev_name(c, did):
    r = c.execute("SELECT name FROM chat_device WHERE id=?", (did,)).fetchone()
    return (r[0] or "") if r else ""


def _has(caps, cap):
    return cap in (caps_out(caps) if isinstance(caps, str) else (caps or []))


def note_caps(c, uid, dev, caps):
    """Vom Geraet gemeldete Faehigkeiten merken (Hinweis: wer "multi" kann, wird zu Gruppenanrufen eingeladen)."""
    if isinstance(dev, str) and _HEX32.fullmatch(dev) and isinstance(caps, str):
        r = c.execute("SELECT caps FROM chat_device WHERE id=? AND uid=?", (dev, uid)).fetchone()
        if r and "chat3" in caps_out(r[0]) and "chat3" not in caps_out(caps):    # 2.5: Chat-Stufe meldet saga.chat (bleibt)
            caps = json.dumps(caps_out(caps) + ["chat3"], separators=(",", ":"))
        c.execute("UPDATE chat_device SET caps=? WHERE id=? AND uid=? AND caps<>?", (caps, dev, uid, caps))


def _can_multi(c, uid):
    """Hat uid ein aktives Chat-Geraet, das Gruppenanrufe kann (caps "multi")?"""
    return bool(c.execute("SELECT 1 FROM chat_device WHERE uid=? AND revoked_at IS NULL AND caps LIKE '%\"multi\"%' LIMIT 1",
                          (uid,)).fetchone())


def _can_horn(c, uid):
    """2.5: Hat uid ein aktives Chat-Geraet, das Anrufe ueber Knoten kann (caps "horn")?"""
    return bool(c.execute("SELECT 1 FROM chat_device WHERE uid=? AND revoked_at IS NULL AND caps LIKE '%\"horn\"%' LIMIT 1",
                          (uid,)).fetchone())


def _audience(c, call):
    """Konten DIESES Servers, die Ereignisse zum Anruf bekommen: 1:1 beide Seiten, Gruppe alle Mitglieder des Chats."""
    if call["kind"] == "group":
        return [u for u in chat.members(c, call["chat_id"]) if not sharing.is_remote(u)]
    return [u for u in (call["caller"], call["callee"]) if u and not sharing.is_remote(u)]


def _who_label(c, u, nm):
    if sharing.is_remote(u) and FED is not None:
        lab = FED.FC.label_of(c, u)
        return {"name": lab["name"], "host": lab["host"], "handle": FED.FC.handle_of(u)}
    return {"name": nm.get(u) or t("Gelöschtes Konto")}


_RV_LAST = [0.0]


def _rv():
    """2.4: Stand einer Teilnehmerliste (call.roster und Anruf-Objekte) - streng steigend in diesem Prozess. Apps verwerfen
    eine Liste mit kleinerem rv als die zuletzt uebernommene. Ereignisse bekommen ihn erst nach dem Commit, Antworten vor
    dem Lesen der Endpunkte: eine Liste mit groesserem rv ist nie aelter."""
    v = max(time.time(), _RV_LAST[0] + 0.001)
    _RV_LAST[0] = v
    return round(v, 3)


def roster(c, call, uid=None, legs=None):
    """Endpunkte fuer Clients: [{who, name, device, dev_name, role, state, caps, me, joined, handle?, host?}] (nur aktive)."""
    legs = _legs(c, call["id"]) if legs is None else legs
    nm = names(c, [x["who"] for x in legs])
    fed = _fedcall(c, call)
    v3 = _v3(c, call)
    cn = horn.call_node(c, call["id"]) if is_horn(call) else None
    hn = horn_web.nodes_for(c, call["id"]) if is_horn(call) else []
    out = []
    for x in legs:
        who = _pp(c, call, x["who"], uid)                 # 2.5: andere in Stufe 3 von Anfang an als Handle
        d = {"who": who, "device": x["device"], "dev_name": x["name"] or "", "role": x["role"], "state": x["state"],
             "caps": caps_out(x["caps"]), "joined": x["joined"], "me": x["who"] == uid}
        d.update(_who_label(c, x["who"], nm))
        if (fed or v3) and not sharing.is_remote(x["who"]):
            d["handle"] = who if who != x["who"] else FED.FC.my_handle(c, x["who"])
        if who != x["who"]:                            # 2.5: andere als Handle (Schluessel nur ueber v2)
            d["host"] = FED.F.SELF_HOST
        if x.get("replaces"):
            d["replaces"] = x["replaces"]
        if (cn or x.get("node")) and not (x.get("node") or "").startswith("f:"):   # 2.5: Kennung am Knoten (Spur -> Geraet);
            d["hp"] = horn.peer_ref(call["id"], x.get("node") or cn, x["device"])   # Knoten anderer Server: state.hp der Geraete
        if hn:                                         # 2.5 P5: Kennung je Knoten (Wechsel zwischen Knoten, horn_web)
            d["hpn"] = horn_web.peer_refs(call["id"], x["device"], hn)
        out.append(d)
    return out


def _v3(c, call):
    """2.5: Anruf in einem lokalen Chat der Stufe 3 - saga-call/v2 mit Handles (wie ueber Servergrenzen), aber ohne andere
    Server. Clients sehen v: 2 und andere Personen nur als Handle (Krypto an den Handle gebunden)."""
    if "_v3" not in call:
        r = c.execute("SELECT v FROM chat WHERE id=?", (call["chat_id"],)).fetchone()
        call["_v3"] = bool(r) and r[0] in (2, 3) and FED is not None   # 2.5: auch foederiert - lokale Mitmitglieder als Handle
    return call["_v3"]


def _pp(c, call, u, viewer):
    """Kennung einer Person fuer viewer: in Stufe 3 andere Konten als Handle, sonst unveraendert."""
    if not u or u == viewer or sharing.is_remote(u) or not _v3(c, call):
        return u
    return FED.FC.my_handle(c, u)


def _fedcall(c, call):
    """Anruf ueber Servergrenzen (saga-call/v2 mit Handles)? 1:1: Gegenstelle auf einem anderen Server; Gruppe: Chat v2."""
    if FED is None:
        return False
    if call.get("peer_id") or call.get("lead"):
        return True
    if call["kind"] == "group":
        r = c.execute("SELECT v FROM chat WHERE id=?", (call["chat_id"],)).fetchone()
        return bool(r) and r[0] == 2
    return False


def _joinable(c, call, uid, legs):
    """Kann uid (mit einem weiteren Geraet) beitreten? -> (ja, grund). Grund: ended | full | old | none."""
    if call["state"] == "ended":
        return False, "ended"
    if call["kind"] != "group" and call["state"] != "active":
        return False, "none"
    if not all(_has(x["caps"], "multi") for x in legs):
        return False, "old"
    if _fedcall(c, call) and not FED.can3(c, call):
        return False, "old"
    if is_horn(call) and uid and not _can_horn(c, uid):
        return False, "old"
    if uid and _removed(c, call["id"], uid):
        return False, "removed"
    if len(legs) >= climit(c, call):
        return False, "full"
    return True, None


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


def _dict(c, call, uid, nm=None, legs=None):
    me_caller = call["caller"] == uid
    group = call["kind"] == "group"
    dmh = _dmh(call)
    peer = call["caller"] if group and not dmh else (call["callee"] if me_caller else call["caller"])
    nm = nm or names(c, [peer])
    now = time.time()
    pu = _pp(c, call, peer, uid)                         # 2.5: Format v2 - Gegenueber von Anfang an nur als Handle
    pd = {"username": pu, "name": nm.get(peer) or t("Gelöschtes Konto"), "avatar": nm.avatar(peer)}
    fed = _fedcall(c, call)
    v3 = _v3(c, call)
    if fed and sharing.is_remote(peer):                  # 2.3: Gegenueber auf einem anderen Server (saga-call/v2, Handles)
        pd.update(handle=FED.FC.handle_of(peer), name=FED.FC.label_of(c, peer)["name"])
    elif (fed and group) or (v3 and peer):
        pd["handle"] = pu if pu != peer else FED.FC.my_handle(c, peer)
    if pu != peer:
        pd["host"] = FED.F.SELF_HOST
    rv = _rv()                                           # vor dem Lesen der Endpunkte (siehe _rv)
    legs = _legs(c, call["id"]) if legs is None else legs
    can, why = _joinable(c, call, uid, legs)
    d = {"id": call["id"], "chat_id": call["chat_id"], "media": call["media"], "state": call["state"], "reason": call["reason"],
         "role": "member" if group else ("caller" if me_caller else "callee"), "peer": pd, **({"v": 2} if fed or v3 else {}),
         "caller_device": call["caller_device"], "callee_device": call["callee_device"], "created": call["created"],
         "answered": call["answered"], "ended": call["ended"], "ring_s": ring_s(),
         # 2.4: Faehigkeiten, die die Geraete beim Anrufen/Annehmen gemeldet haben (Hinweis fuer die Oberflaeche)
         "caps": {"caller": caps_out(call.get("caps_a")), "callee": caps_out(call.get("caps_b"))},
         "expires_in": max(0, round(call["created"] + ring_s() - now, 1)) if call["state"] == "ringing" else None,
         # 2.4 E4/E5: Raum - Art, Endpunkte je Geraet, ob (ein weiteres Geraet von) uid beitreten kann, Grenze
         "kind": call["kind"], "legs": roster(c, call, uid, legs), "joinable": can, "join_block": why, "limit": climit(c, call),
         "mine": [x["device"] for x in legs if x["who"] == uid], "rv": rv, "topo": call.get("topo") or "p2p",
         "now": round(now, 3)}                           # 2.5: Server-Uhr (Dauer ab answered, gleich fuer alle)
    if group and FED is not None and FED.HORN_MESH.get(call["id"]):
        d["horn_mesh"] = FED.HORN_MESH[call["id"]]      # 2.5 P10: warum ohne Knoten (anderer Server kann keinen nutzen)
    if group:
        d["from"] = dict(pd)
        d["host"] = me_caller                           # 2.5 P5: Gastgeber (darf stummschalten und entfernen)
        n = len(chat.members(c, call["chat_id"]))         # wie ring_group: grosse Gruppen klingeln nur still
        d["members"], d["silent"] = n, n > RING_SILENT
        inv = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who=?", (call["id"], uid)).fetchone()
        d["invite"] = inv[0] if inv else None
        d["old"] = [r[0] for r in c.execute("SELECT who FROM chat_call_invite WHERE call_id=? AND state='old'", (call["id"],)).fetchall()]
        nmo = names(c, d["old"])
        d["old"] = [dict(_who_label(c, u, nmo), who=_pp(c, call, u, uid)) for u in d["old"]]
    if dmh:                                              # 2.5: 1:1 ueber Knoten - peer = Gegenueber, from = anrufende Person
        d["dm"] = True
        if not me_caller:
            d["from"] = dict(pd)
        else:
            nmc = names(c, [call["caller"]])
            d["from"] = {"username": _pp(c, call, call["caller"], uid), "name": nmc.get(call["caller"]) or call["caller"],
                         "avatar": nmc.avatar(call["caller"])}
    return d


def _emit(c, uids, typ, data):
    chat._direct(c, uids, typ, data)


def _busy(c, uid):
    """Laeuft fuer uid gerade ein Anruf (klingelt oder Gespraech)? 2.4: auch ein Gruppenanruf, in dem uid mit einem Geraet
    ausser einem Begleitgeraet ist (Gruppenanrufe sind gegenueber eigenen Begleitgeraeten nicht exklusiv)."""
    return bool(c.execute("SELECT 1 FROM chat_call WHERE state IN ('ringing','active') AND (kind='dm' OR callee<>'') AND (caller=? OR callee=?) "
                          "LIMIT 1", (uid, uid)).fetchone()) or _in_group(c, uid)


def _in_group(c, uid, cid=""):
    return bool(c.execute("SELECT 1 FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.who=? AND l.state='active' "
                          "AND l.role<>'companion' AND k.state<>'ended' AND k.kind='group' AND k.id<>? LIMIT 1", (uid, cid)).fetchone())


def _log(c, call, status, now, end=None):
    """Eintrag im Chat (chat_msg kind='call'): nur Metadaten, die der Server ohnehin kennt. end: Gespraechsende fuer die
    Dauer (verlorene Verbindung: letztes Lebenszeichen statt Zeitpunkt der Erkennung). 2.4 Gruppe: group und n (Zahl der
    Personen, die dabei waren)."""
    ch = chat._load(c, call["chat_id"])
    if not ch:
        return None
    dur = max(0, int((end or now) - call["answered"])) if call["answered"] and status == "ended" else None
    d = {"v": 1, "type": "call", "call": call["id"], "media": call["media"], "status": status, "dur": dur,
         "from": call["caller"], "to": call["callee"]}
    if _v3(c, call):                                   # 2.5: Stufe 3 - Klartext-Metadaten nur mit Handles
        d.update({k: FED.FC.my_handle(c, d[k]) if d[k] and not sharing.is_remote(d[k]) else d[k] for k in ("from", "to")})
    if call["kind"] == "group" and not _dmh(call):     # 2.5: 1:1 ueber Knoten -> Eintrag wie 1:1
        d.update(group=True, n=c.execute("SELECT COUNT(DISTINCT who) FROM chat_call_leg WHERE call_id=?", (call["id"],)).fetchone()[0])
    body = json.dumps(d, separators=(",", ":"))
    eff, mx = ch["retention_s"], chat.retention_max()
    eff = min(eff, mx) if eff and mx else (eff or mx)
    mid = secrets.token_hex(16)
    seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size) "
                    "VALUES (?,?,'call',?,?,?,?,?,?,'',NULL,0) RETURNING seq",
                    (mid, ch["id"], call["caller"], call["caller_device"], int(call["created"] * 1000), now, now + eff if eff else None,
                     body)).fetchone()[0]
    c.execute("UPDATE chat SET last_seq=?, last_at=? WHERE id=?", (seq, now, ch["id"]))
    chat.revive(c, ch["id"], call["caller"])        # Anruf holt den Chat aus dem Archiv (wie eine neue Nachricht)
    chat.direct_person(c, ch, chat.members(c, ch["id"]), "chat.message", {"chat_id": ch["id"], "seq": seq, "id": mid, "sender": call["caller"],
                                                                         "kind": "call"}, call["caller"], "sender")
    return seq


def _finish(c, call, reason, now, by=None, remote=False):
    """Anruf beenden (in der Transaktion, Zeile gesperrt bzw. Zustand schon geprueft): Zustand, Chat-Eintrag, verpasst,
    Ereignis an beide. reason: hangup | declined | busy | missed | cancelled | failed | lost | timeout | gone | ended."""
    n = c.execute("UPDATE chat_call SET state='ended', reason=?, ended=? WHERE id=? AND state<>'ended'", (reason, now, call["id"])).rowcount
    if not n:
        return False
    was = call["state"]
    group = call["kind"] == "group"
    if was == "ringing":
        status = "missed" if group and not _dmh(call) else {"declined": "declined", "busy": "busy"}.get(reason, "missed")
    else:
        status = "failed" if reason == "failed" and not call.get("_connected") else "ended"
    seen = [x for x in (call.get("seen_a"), call.get("seen_b")) if x]
    _log(c, call, status, now, min(seen) if reason == "lost" and seen and not group else None)
    if group:                                          # Eingeladene, die nicht dabei waren: verpasst
        if _dmh(call) and reason == "declined":
            c.execute("UPDATE chat_call_invite SET state='declined' WHERE call_id=? AND state='ringing'", (call["id"],))
        for (u,) in c.execute("SELECT who FROM chat_call_invite WHERE call_id=? AND state='ringing'", (call["id"],)).fetchall():
            if not sharing.is_remote(u):
                notify.create(c, u, "call.missed", call["caller"], data={"chat": call["chat_id"], "call": call["id"]})
        c.execute("UPDATE chat_call_invite SET state='missed' WHERE call_id=? AND state='ringing'", (call["id"],))
    elif status in ("missed", "busy") and not call["callee"].startswith("@"):
        notify.create(c, call["callee"], "call.missed", call["caller"], data={"chat": call["chat_id"], "call": call["id"]})
    c.execute("UPDATE chat_call_leg SET state='left', left_at=?, reason=COALESCE(reason, ?) WHERE call_id=? AND state='active'",
              (now, reason, call["id"]))
    if is_horn(call):
        horn.room_close(c, call["id"])                 # 2.5: Raum am Knoten schliessen (nach dem Commit)
    if FED is not None and not remote:
        if group:
            if call.get("lead") is None and _fedcall(c, call):
                FED.group_ended(c, call, reason)       # alle anderen Server des Chats (verwerfbar)
            elif call.get("lead") is not None and _dmh(call) and was == "ringing" and reason in ("declined", "busy"):
                FED.dm_ended(c, call, reason)          # 2.5: 1:1 ueber Knoten - Ablehnen hier beendet den Anruf dort
        elif call.get("peer_id"):
            FED.ended(c, call, reason)                 # 2.3: der anderen Instanz melden (verwerfbar)
    data = {"call_id": call["id"], "chat_id": call["chat_id"], "state": "ended", "reason": reason, "status": status, "by": by,
            **({"kind": "group"} if group else {}), **_dmf(call)}
    if _v3(c, call) and by and not sharing.is_remote(by):                            # 2.5: wer aufgelegt hat - in Stufe 3 je Empfaenger als Handle
        bus.emit_after(c, "direct_each", {"items": [(u, "call.state", dict(data, by=_pp(c, call, by, u))) for u in sorted(set(_audience(c, call)))]})
    else:
        _emit(c, _audience(c, call), "call.state", data)
    _OWN.discard(call["id"])
    print(f"call: {call['id'][:8]} beendet ({was} -> {reason}/{status})", flush=True)
    return True


GONE_REASONS = ("notaus",)                     # Abgaenge mit eigenem Hinweis im Client (call.roster "gone")


def gone(c, cid, secs=120):
    """{geraet: grund} fuer Endpunkte, die in den letzten secs Sekunden aus einem Grund in GONE_REASONS gegangen sind."""
    rows = c.execute(f"SELECT device, reason FROM chat_call_leg WHERE call_id=? AND state='left' AND left_at>? AND reason IN "
                     f"({','.join('?' * len(GONE_REASONS))}) LIMIT 64", (cid, time.time() - secs, *GONE_REASONS)).fetchall()
    return {r[0]: r[1] for r in rows}


def emit_roster(c, call, legs=None, fed=True):
    """Teilnehmerliste an alle Konten dieses Servers, die zum Anruf gehoeren (call.roster), und - fuehrt dieser Server den
    Anruf - an die anderen Server (Auftrag 'roster')."""
    legs = _legs(c, call["id"]) if legs is None else legs
    base = {"call_id": call["id"], "chat_id": call["chat_id"], "kind": call["kind"], "media": call["media"], "state": call["state"],
            "limit": climit(c, call), "topo": call.get("topo") or "p2p", **_dmf(call),
            # Bughunt W09: Beginn des Gespraechs + Server-Uhr - die Dauer laeuft bei allen ab answered (auch beim Anrufer,
            # der ueber einen Knoten schon waehrend des Klingelns verbunden ist)
            "answered": call.get("answered"), "now": round(time.time(), 3)}
    can, why = _joinable(c, call, None, legs)
    data = dict(base, legs=roster(c, call, None, legs), joinable=can, join_block=why)
    g = gone(c, call["id"])
    if g:
        data["gone"] = g                                      # 2.5: warum Geraete gerade weg sind (Notaus) - Clients zeigen es
    after = getattr(c, "after_commit", None)                  # rv erst nach dem Commit (vor dem Versand, siehe _rv)
    if _v3(c, call):                                          # 2.5: Stufe 3 - je Konto eigene Liste (andere als Handle)
        items = [(u, "call.roster", dict(data, legs=roster(c, call, u, legs))) for u in sorted(set(_audience(c, call)))]

        def stamp():
            rv = _rv()
            for it in items:
                it[2]["rv"] = rv
        after(stamp) if after is not None else stamp()
        bus.emit_after(c, "direct_each", {"items": items})
    else:
        after(lambda: data.__setitem__("rv", _rv())) if after is not None else data.__setitem__("rv", _rv())
        _emit(c, _audience(c, call), "call.roster", data)
    if fed and FED is not None and call.get("lead") is None and _fedcall(c, call):
        FED.roster_out(c, call, legs)


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
                           "alive_s": 15, "max_s": MAX_S, "format": "saga-call/v1",
                           # 2.4: Body-Fassung, Server-Faehigkeiten und Grenzen (E4/E5: Endpunkte je Anruf im Mesh)
                           "body_v": 3, "features": features(), **({"e2e": "saga-horn/v1", "horn_ready": _horn_ready(), "horn_dm": horn_dm_on()} if horn_on() else {}),
                           "limits": {"endpoints_video": limit("video"), "endpoints_audio": limit("audio"), "signals_max": SIGNALS_MAX,
                                      "signals_per_min": SIG_PER_MIN, "signals_hard": SIGNALS_HARD, "sig_rate": SIG_RATE,
                                      "sealed_max": SEALED_MAX, "caps_max": CAPS_MAX, "ring_silent": RING_SILENT, "handover_s": 15,
                                      **(_horn_limits() if horn_on() else {})}})


def _horn_ready():
    """2.5 P5: ist gerade ein Knoten bereit? Sonst gilt fuer neue Gruppenanrufe die Mesh-Grenze (Hinweis vor dem Start)."""
    with _db() as c:
        return horn.any_ready(c)


def _horn_limits():
    """2.5: Grenzen ueber Knoten nach Weg (endpoints_video/_audio bleiben die Mesh-Grenzen fuer 2.4-Clients)."""
    v, a = horn.limits(None)
    vu, au = horn.limits(None, udp=True)
    return {"horn": {"video_tcp": v, "audio_tcp": a, "video_udp": vu, "audio_udp": au}}


@router.put("/api/calls/caps")
async def api_caps(request: Request):
    """2.4: Faehigkeiten dieses Geraets melden {device, caps} (nur Hinweis: zu Gruppenanrufen eingeladen wird, wer ein Geraet
    mit "multi" hat; aeltere Apps melden nichts und sehen nur den Eintrag im Chat)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    with _db() as c, c:
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        note_caps(c, uid, dev, caps_in(b.get("caps")))
    return chat._no_store({"ok": True})


@router.get("/api/calls/active")
def api_active(request: Request):
    """Laufende/klingelnde Anrufe der Person (nach dem Laden einer Seite, App aus dem Hintergrund). 2.4: auch Gruppenanrufe
    in Chats der Person und Anrufe, in denen ein anderes eigenes Geraet ist (Banner "Anruf laeuft auf …")."""
    uid, err = _me(request)
    if err:
        return err
    with _db() as c:
        rows = [_row(r) for r in c.execute(
            f"SELECT {C_COLS} FROM chat_call k WHERE state IN ('ringing','active') AND (caller=? OR callee=? OR (kind='group' AND EXISTS "
            "(SELECT 1 FROM chat_member m WHERE m.chat_id=k.chat_id AND m.uid=? AND m.left_at IS NULL AND m.state='ok'))) "
            "ORDER BY created DESC LIMIT 8", (uid, uid, uid)).fetchall()]
        now = time.time()
        rows = [r for r in rows if not (r["state"] == "ringing" and r["kind"] == "dm" and r["created"] + ring_s() < now)]
        nm = names(c, [r["caller"] for r in rows] + [r["callee"] for r in rows])
        return chat._no_store({"calls": [_dict(c, r, uid, nm) for r in rows]})


@router.post("/api/calls")
async def api_call(request: Request):
    """Anrufen: {id, chat_id, media:'audio'|'video', device, signals:{geraet_des_gegenuebers: versiegeltes Angebot}}.
    signals muss GENAU die aktiven Chat-Geraete des Gegenuebers abdecken - sonst 409 mit {devices}.
    2.4: In Gruppen-, freien und Seiten-Chats startet das einen Gruppenanruf (kind 'group', ohne signals): die anrufende
    Person ist zuerst allein, alle anderen Mitglieder klingeln (call.invite) und treten mit POST /join bei."""
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
    caps = caps_in(b.get("caps"))
    if FED is not None and horn_on() and _has(caps, "horn"):
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(FED.horn_prefetch, chat_id)   # 2.5 P10: Knoten der anderen Server (vor der Transaktion)
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, chat_id, do_sync=False)
        if err:
            return err
        if ch["kind"] != "dm":                             # 2.4 E5: Gruppenanruf
            gr = _group_start(c, uid, ch, m, cid, media, dev, caps, now, _dev_class(b))
            if gr is not None:
                return gr
            fed = ("group", ch)
        elif b.get("via") == "horn" and not horn_dm_on():
            return _fallback("off")
        elif b.get("via") == "horn" and chat.is_fed(ch):   # 2.5: 1:1 ueber Knoten mit einem anderen Server (Auftrag dmhorn)
            if FED is None:
                return _fallback("fed")
            if not _my_device(c, uid, dev):
                return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
            note_caps(c, uid, dev, caps)
            fed = ("dmhorn", ch)
        elif chat.is_fed(ch):                              # 2.3: Gegenueber auf einem anderen Server
            if FED is None:
                return _bad(t("Diese Person ist nicht erreichbar."), 404)
            if not _my_device(c, uid, dev):
                return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
            sig = _sealed_map(b.get("signals"))
            if sig is None:
                return _bad(t("ungültige Angabe"))
            note_caps(c, uid, dev, caps)
            fed = (ch, sig)
        else:
            fed = None
    from starlette.concurrency import run_in_threadpool
    if fed is not None and fed[0] == "group":
        return await run_in_threadpool(FED.group_start, uid, fed[1], cid, media, dev, caps)
    if fed is not None and fed[0] == "dmhorn":
        return await run_in_threadpool(FED.dm_horn_start, uid, fed[1], cid, media, dev, caps, _dev_class(b))
    if fed is not None:
        return await run_in_threadpool(FED.start, uid, fed[0], cid, media, dev, fed[1], caps)
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, chat_id, do_sync=False)
        if err:
            return err
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        a, _, z = (ch["dm_key"] or "").partition("|")
        peer = z if a == uid else a
        if peer not in mem or not chat.may_contact(c, uid, peer):
            return _bad(t("Diese Person ist nicht erreichbar."), 404)
        if b.get("via") == "horn":                         # 2.5: 1:1 ueber einen Knoten (calls.horn_all)
            return _dm_horn_start(c, uid, ch, peer, cid, media, dev, caps, now, _dev_class(b))
        devs = _active_devices(c, peer)
        if not devs:
            return _bad(t("{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.",
                          name=names(c, [peer]).get(peer) or _pp(c, {"chat_id": ch["id"]}, peer, uid)), 409)
        sig = _sealed_map(b.get("signals"))
        if sig is None:
            return _bad(t("ungültige Angabe"))
        if set(sig) != devs:
            v3c = chat.is_v3(ch)                           # 2.5: Stufe 3 - Geraete aus den Schluesseln v2 (ohne Anmeldenamen)
            keys = FED.FC.local_entry(c, FED.FC.my_handle(c, peer)) if v3c else chat._keys(c, [peer]).get(peer)
            return chat._no_store({"error": t("Die Geräte des Gegenübers haben sich geändert – bitte erneut anrufen."),
                                   "devices": [d for d in (keys or {}).get("devices", []) if not d["revoked"]]}, 409)
        if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
            return _bad(t("Diesen Anruf gibt es schon."), 409)
        inc = c.execute("SELECT id FROM chat_call WHERE state='ringing' AND caller=? AND callee=? LIMIT 1", (peer, uid)).fetchone()
        if inc:                                            # Rueckruf, waehrend der Anruf des Gegenuebers noch klingelt
            return chat._no_store({"error": t("{name} ruft dich gerade an – nimm den Anruf an.",
                                                     name=names(c, [peer]).get(peer) or _pp(c, {"chat_id": ch["id"]}, peer, uid)),
                                   "incoming": inc[0]}, 409)
        if _busy(c, uid):
            return _bad(t("Du telefonierst gerade schon."), 409)
        if not _rate("call", uid, CALL_RATE) or not _rate("call-h", uid, CALL_RATE_H, 3600) or not _rate("pair", uid + "|" + peer, PAIR_RATE):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        # 2.4: v=3 (Raum: weitere Geraete koennen beitreten), wenn das anrufende Geraet "multi" meldet - sonst 1:1 wie 2.3
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, caps_a, v) "
                  "VALUES (?,?,?,?,?,?,'ringing',?,?,?,?)", (cid, ch["id"], uid, peer, dev, media, now, now, caps, 3 if _has(caps, "multi") else 1))
        _leg_put(c, cid, dev, uid, "member", None, caps, name=_dev_name(c, dev), now=now)
        note_caps(c, uid, dev, caps)
        _OWN.add(cid)
        call = _load(c, cid)
        if _busy_other(c, peer, cid):
            _finish(c, call, "busy", now)
            return chat._no_store({"ok": False, "busy": True, "error": t("Besetzt – {name} telefoniert gerade.",
                                                                         name=names(c, [peer]).get(peer) or _pp(c, call, peer, uid))}, 409)
        c.executemany("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?)",
                      [(cid, dev, k, v, now) for k, v in sorted(sig.items())])
        c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (len(sig), cid))
        nm = names(c, [uid])
        frm = {"username": uid, "name": nm.get(uid) or uid, "avatar": nm.avatar(uid)}
        if _v3(c, call):                               # 2.5: Stufe 3 - anrufende Person als Handle (saga-call/v2)
            frm.update(username=FED.FC.my_handle(c, uid), handle=FED.FC.my_handle(c, uid), host=FED.F.SELF_HOST)
        _emit(c, [peer], "call.ring", {"call_id": cid, "chat_id": ch["id"], "media": media, "from_device": dev, "ring_s": ring_s(),
                                       "from": frm, **({"v": 2} if _v3(c, call) else {})})
        # Handy/Desktop: Art "call" (hohe Prioritaet, kurze Lebensdauer) - nie Inhalt; ohne Namen, wenn so eingestellt
        muted = c.execute("SELECT muted FROM chat_member WHERE chat_id=? AND uid=?", (ch["id"], peer)).fetchone()
        if not (muted and muted[0]):
            bus.fire("push.send", c, uid=peer, kind="call", actor=uid, data={"chat": ch["id"], "call": cid})
        print(f"call: {cid[:8]} klingelt ({media}, Chat {ch['id']}, {len(sig)} Geraet(e))", flush=True)
        return chat._no_store({"ok": True, "call": _dict(c, call, uid)})


def _fallback(why):
    """2.5: 1:1 ueber Knoten geht nicht - nichts angelegt, der Client ruft mit derselben id direkt an."""
    msg = {"off": t("Anrufe über Knoten sind auf diesem Server nicht eingeschaltet."),
           "caps": t("Dieses Gerät kann keine Anrufe über Knoten."),
           "node": t("Gerade ist kein Knoten bereit."),
           "peer": t("Das Gegenüber hat kein Gerät, das Anrufe über Knoten kann."),
           "fed": t("Anrufe mit anderen Servern laufen direkt.")}.get(why, "")
    return chat._no_store({"error": msg, "fallback": "direct", "horn_block": why}, 409)


def _dm_horn_start(c, uid, ch, peer, cid, media, dev, caps, now, dev_class="desktop"):
    """2.5: POST /api/calls {via: "horn"} im lokalen Direkt-Chat -> Raum-Anruf ueber einen Knoten (kind 'group', topo
    'horn', callee = Gegenueber) oder 409 fallback (nichts angelegt)."""
    if not (_has(caps, "horn") and _has(caps, "multi")):
        return _fallback("caps")
    if not horn.any_ready(c):
        return _fallback("node")
    if not _can_horn(c, peer):
        return _fallback("peer")
    if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
        return _bad(t("Diesen Anruf gibt es schon."), 409)
    inc = c.execute("SELECT id FROM chat_call WHERE state='ringing' AND caller=? AND callee=? LIMIT 1", (peer, uid)).fetchone()
    if inc:
        return chat._no_store({"error": t("{name} ruft dich gerade an – nimm den Anruf an.",
                                          name=names(c, [peer]).get(peer) or _pp(c, {"chat_id": ch["id"]}, peer, uid)), "incoming": inc[0]}, 409)
    if _busy(c, uid):
        return _bad(t("Du telefonierst gerade schon."), 409)
    if not _rate("call", uid, CALL_RATE) or not _rate("call-h", uid, CALL_RATE_H, 3600) or not _rate("pair", uid + "|" + peer, PAIR_RATE):
        return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
    note_caps(c, uid, dev, caps)
    busy = _busy_other(c, peer, cid)
    call = group_create(c, ch, cid, media, uid, dev, caps, now, name=_dev_name(c, dev), dm_peer=peer, ring=not busy)
    if busy:
        _finish(c, call, "busy", now)
        return chat._no_store({"ok": False, "busy": True, "error": t("Besetzt – {name} telefoniert gerade.",
                                                                     name=names(c, [peer]).get(peer) or _pp(c, call, peer, uid))}, 409)
    d = _dict(c, call, uid)
    d["horn"] = horn_block(c, call, dev, "member", dev_class)
    return chat._no_store({"ok": True, "call": d})


def _group_start(c, uid, ch, m, cid, media, dev, caps, now, dev_class="desktop"):
    """POST /api/calls in einem Gruppen-/freien/Seiten-Chat. -> Antwort, oder None (foederierter Chat mit fremdem Heim:
    das Heim fuehrt den Anruf, FED.group_start nach der Transaktion)."""
    if not _my_device(c, uid, dev):
        return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
    if (m.get("state") or "ok") != "ok":
        return _bad(t("Anrufe gehen erst, wenn du die Unterhaltung angenommen hast."), 409)
    if not _has(caps, "multi"):
        return _bad(t("Gruppenanrufe brauchen Saga 2.4 auf diesem Gerät."), 409)
    note_caps(c, uid, dev, caps)
    if chat.is_fed(ch):
        why = FED.group_block(c, ch)
        if why:
            return _bad(why, 409)
    run = c.execute("SELECT id FROM chat_call WHERE chat_id=? AND kind='group' AND state<>'ended' LIMIT 1", (ch["id"],)).fetchone()
    if run:
        return chat._no_store({"error": t("In diesem Chat läuft schon ein Anruf – tritt ihm bei."), "running": run[0]}, 409)
    if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
        return _bad(t("Diesen Anruf gibt es schon."), 409)
    if _busy(c, uid):
        return _bad(t("Du telefonierst gerade schon."), 409)
    if not _rate("call", uid, CALL_RATE) or not _rate("call-h", uid, CALL_RATE_H, 3600):
        return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
    if chat.is_fed(ch) and ch["home_peer"] is not None:
        return None                                    # Heim auf einem anderen Server
    call = group_create(c, ch, cid, media, uid, dev, caps, now, name=_dev_name(c, dev))
    d = _dict(c, call, uid)
    if is_horn(call):
        d["horn"] = horn_block(c, call, dev, "member", dev_class)
    return chat._no_store({"ok": True, "call": d})


def group_create(c, ch, cid, media, starter, dev, caps, now, peer_id=None, name="", lead=None, dm_peer=None, ring=True):
    """Gruppenanruf anlegen (dieser Server fuehrt ihn, bzw. lead = fuehrender Server bei einer Kopie): Endpunkt der
    anrufenden Person, Einladungen (wer kein Geraet mit "multi" hat: 'old', "braucht Saga 2.4"), Klingeln hier, Liste an
    alle. -> Anruf. 2.5: ueber einen Knoten (topo 'horn'), wenn horn_wanted - der Endpunkt der anrufenden Person kommt auf
    den besten bereiten Knoten, Einladungen nur an Personen mit einem Geraet, das "horn" kann (sonst 'old')."""
    node, why = None, []
    hn = lead is None and (dm_peer is not None or horn_wanted(c, ch, media, caps, why))   # 2.5: 1:1 ueber Knoten (geprueft)
    if hn and peer_id is None:
        node = horn.ready_nodes(c)[0]
    c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, caps_a, kind, v, topo, "
              "lead, peer_id) VALUES (?,?,?,?,?,?,'ringing',?,?,?,?,'group',3,?,?,?) ON CONFLICT (id) DO NOTHING",
              (cid, ch["id"], starter, dm_peer or "", dev, media, now, now, now, caps, "horn" if hn else "mesh", lead, lead))
    _leg_put(c, cid, dev, starter, "member", peer_id, caps, name=name, now=now)
    if why and FED is not None:
        if len(FED.HORN_MESH) > 5000:
            FED.HORN_MESH.clear()
        FED.HORN_MESH[cid] = why[0]                    # P10: Fall c - Hinweis an alle (call.horn_mesh)
    if node:
        horn.set_leg_node(c, cid, dev, node["id"])
        horn.room_open(c, cid, node["id"], climit(c, {"id": cid, "media": media, "topo": "horn"}))
    _OWN.add(cid)
    call = _load(c, cid)
    rung = []
    for u in chat.members(c, ch["id"]):
        if u == starter:
            continue
        if sharing.is_remote(u):
            st = "ringing"                             # Server der Person meldet zurueck, wer 2.4 braucht (Auftrag roster)
        else:
            mm = chat._member(c, ch["id"], u)
            if (mm or {}).get("state", "ok") != "ok":
                continue                               # Nachrichtenanfrage: klingelt nie
            st = "ringing" if (_can_horn(c, u) if hn else _can_multi(c, u)) else "old"
            if st == "ringing" and ring:
                rung.append(u)
        c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,?,?) ON CONFLICT (call_id, who) DO NOTHING",
                  (cid, u, st, now))
    ring_group(c, call, rung)
    emit_roster(c, call)
    print(f"call: {cid[:8]} {'1:1 ueber Knoten' if dm_peer else 'Gruppenanruf'} ({media}, Chat {ch['id']}, {len(rung)} klingeln hier{', Knoten ' + node['name'] if node else ''}"
          f"{', ueber Knoten' if hn and not node else ''})", flush=True)
    return call


def ring_group(c, call, uids):
    """Gruppenanruf klingeln lassen (call.invite + Mitteilung Art "call.group"); in grossen Chats nur still (kein Push)."""
    if not uids:
        return
    n = len(chat.members(c, call["chat_id"]))
    silent = n > RING_SILENT
    st = call["caller"]
    nm = names(c, [st])
    frm = {"username": st, "name": nm.get(st) or st, "avatar": nm.avatar(st)}
    if sharing.is_remote(st) and FED is not None:
        frm.update(handle=FED.FC.handle_of(st), name=FED.FC.label_of(c, st)["name"])
    elif _v3(c, call):                                 # 2.5: Format v2 - als Handle
        frm.update(username=FED.FC.my_handle(c, st), handle=FED.FC.my_handle(c, st), host=FED.F.SELF_HOST)
    ch = chat._load(c, call["chat_id"])
    frm["chat"] = chat._name(c, uids[0], ch, chat.members(c, ch["id"]), names(c, chat.members(c, ch["id"]))) if ch else None
    for u in uids:
        _emit(c, [u], "call.invite", {"call_id": call["id"], "chat_id": call["chat_id"], "kind": "group", "media": call["media"],
                                      "from": frm, "from_device": call["caller_device"], "ring_s": ring_s(), "silent": silent, "members": n,
                                      **_dmf(call)})
        if silent:
            continue
        muted = c.execute("SELECT muted FROM chat_member WHERE chat_id=? AND uid=?", (call["chat_id"], u)).fetchone()
        if not (muted and muted[0]):
            # actor auch bei fremden Anrufenden (wie 1:1 in fed_calls): die Mitteilung nennt den Namen (caller fuer CallKit)
            bus.fire("push.send", c, uid=u, kind="call.group", actor=st, data={"chat": call["chat_id"], "call": call["id"]})


def _busy_other(c, uid, cid):
    return bool(c.execute("SELECT 1 FROM chat_call WHERE state IN ('ringing','active') AND (kind='dm' OR callee<>'') AND (caller=? OR callee=?) "
                          "AND id<>? LIMIT 1",
                          (uid, uid, cid)).fetchone()) or _in_group(c, uid, cid)


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
        if call["callee"] != uid or call["kind"] == "group":
            return _bad(t("ungültige Angabe"))
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        if call["state"] != "ringing" or call["created"] + ring_s() < now:
            return chat._no_store({"error": t("Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen."),
                                   "call": _dict(c, call, uid)}, 409)
        sig = _sealed_map(b.get("signals"), {call["caller_device"]})
        if sig is None:
            return _bad(t("ungültige Angabe"))
        caps = caps_in(b.get("caps"))
        note_caps(c, uid, dev, caps)
        if not (call["peer_id"] and FED is not None):
            c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=?, caps_b=? WHERE id=?",
                      (dev, now, now, now, caps, call["id"]))
            _leg_put(c, call["id"], dev, uid, "member", None, caps, name=_dev_name(c, dev), now=now)
            seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                            (call["id"], dev, call["caller_device"], sig[call["caller_device"]], now)).fetchone()[0]
            c.execute("UPDATE chat_call SET nsig=nsig+1 WHERE id=?", (call["id"],))
            _emit(c, [call["caller"]], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": call["caller_device"],
                                                       "sealed": sig[call["caller_device"]]})
            _emit(c, [call["caller"], call["callee"]], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active",
                                                                       "device": dev})
            cur = _load(c, call["id"])
            if cur["v"] == 3:
                emit_roster(c, cur)                        # 2.4: andere eigene Geraete zeigen "Anruf laeuft auf …"
            print(f"call: {call['id'][:8]} angenommen", flush=True)
            return chat._no_store({"ok": True, "call": _dict(c, cur, uid)})
    from starlette.concurrency import run_in_threadpool         # 2.3: der Server des Anrufers fuehrt den Zustand
    return await run_in_threadpool(FED.answer, uid, call, dev, sig, caps)


@router.post("/api/calls/{cid}/decline")
async def api_decline(cid: str, request: Request):
    """Ablehnen (nur das Gegenueber, solange es klingelt): {reason: 'declined'|'busy'}. 2.4 Gruppe: nur die eigene Einladung
    (der Anruf laeuft weiter, die anderen eigenen Geraete hoeren auf zu klingeln)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    reason = b.get("reason") if b.get("reason") in ("declined", "busy") else "declined"
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if _dmh(call) and call["callee"] == uid and call["state"] == "ringing":   # 2.5: 1:1 ueber Knoten - Ablehnen beendet
            _finish(c, call, reason, time.time(), by=uid)
            return chat._no_store({"ok": True})
        if call["kind"] == "group":
            c.execute("UPDATE chat_call_invite SET state='declined' WHERE call_id=? AND who=? AND state='ringing'", (call["id"], uid))
            _emit(c, [uid], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "declined", "kind": "group"})
            return chat._no_store({"ok": True})
        if call["callee"] != uid or call["state"] != "ringing":
            return chat._no_store({"ok": False, "call": _dict(c, call, uid)}, 409)
        _finish(c, call, reason, time.time(), by=uid)
    return chat._no_store({"ok": True})


@router.post("/api/calls/{cid}/end")
async def api_end(cid: str, request: Request):
    """Auflegen/Abbrechen (beide Seiten): {reason: 'hangup'|'failed', connected: bool}. Der Anrufer bricht beim Klingeln ab
    -> beim Gegenueber "verpasst". 2.4: mit {device}: Begleitgeraet bzw. Gruppenanruf -> nur dieses Geraet verlaesst den
    Anruf (wie POST /leave); ohne device im Gruppenanruf: alle eigenen Geraete."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    reason = b.get("reason") if b.get("reason") in ("hangup", "failed") else "hangup"
    dev = str(b.get("device") or "")
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["state"] == "ended":
            return chat._no_store({"ok": True, "already": True})
        if _dmh(call) and call["state"] == "ringing" and uid == call["callee"]:   # 2.5: 1:1 ueber Knoten - wie Ablehnen
            _finish(c, call, "declined", time.time(), by=uid)
            return chat._no_store({"ok": True})
        legs = {x["device"]: x for x in _legs(c, call["id"])}
        mine = legs.get(dev) if dev else None
        if dev and (mine is None or mine["who"] != uid) and not (call["kind"] == "dm" and call["state"] == "ringing" and uid == call["callee"]):
            return chat._no_store({"ok": True, "left": []})      # dieses Geraet ist (nicht mehr) im Anruf - nichts zu beenden
        companion = mine is not None and mine["who"] == uid and dev not in (call["caller_device"], call["callee_device"])
        if call["kind"] == "group" or companion:
            devs = [dev] if mine is not None and mine["who"] == uid else [d for d, x in legs.items() if x["who"] == uid] if not dev else []
            fed = call if call.get("lead") is not None and FED is not None else None
            if fed is None:
                for d in devs:
                    _leave(c, call, d, time.time(), "hangup", by=uid)
                return chat._no_store({"ok": True, "left": devs})
        else:
            fed = None
            if call["state"] == "ringing":
                reason = "cancelled" if uid == call["caller"] else "declined"
            call["_connected"] = bool(b.get("connected"))
            _finish(c, call, reason, time.time(), by=uid)
            return chat._no_store({"ok": True})
    from starlette.concurrency import run_in_threadpool
    for d in devs:
        await run_in_threadpool(FED.leave, uid, fed, d)
    return chat._no_store({"ok": True, "left": devs})


@router.post("/api/calls/{cid}/signal")
async def api_signal(cid: str, request: Request):
    """Signal weiterreichen (ICE-Kandidaten, neue Angebote, Zustand): {device, signals:{zielgeraet: versiegelt}}.
    Anrufer -> alle Geraete des Gegenuebers (vor dem Annehmen) bzw. das annehmende Geraet; Gegenueber -> anrufendes Geraet.
    2.4: auch weitere offer/answer waehrend des Gespraechs (Neuverhandlung, ICE-Restart) in beide Richtungen - der Server
    sieht den Typ nicht, er begrenzt nur Menge (sig_budget), Rate je Geraet und Groesse. Raum (E4/E5): Ziele sind alle
    anderen aktiven Endpunkte des Anrufs (auch auf anderen Servern - sternfoermig ueber den Server, der den Anruf fuehrt)."""
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
        if call["v"] == 3:                                 # 2.4: Raum
            if not _my_device(c, uid, dev):
                return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
            legs = {x["device"]: x for x in _legs(c, call["id"])}
            r = _room_signal(c, call, uid, dev, b.get("signals"), legs, now)
            if not isinstance(r, dict):
                return r
            fwd = r
        else:
            fwd = None
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
            if call["peer_id"] and FED is not None:            # 2.3: Zielgeraete auf dem anderen Server - der prueft sie
                fsig = _sealed_map(b.get("signals"), allowed if uid != call["caller"] or call["callee_device"] else None)
                if fsig is None:
                    return _bad(t("ungültige Angabe"))
                if call["nsig"] + len(fsig) > sig_budget(call, now) or not _rate("sig", dev, SIG_RATE):
                    return _bad(t("Zu viele Signale – bitte kurz warten."), 429)
                fed = call
            else:
                fed = None
                sig = _sealed_map(b.get("signals"), allowed)
                if sig is None:
                    return _bad(t("ungültige Angabe"))
                if call["nsig"] + len(sig) > sig_budget(call, now) or not _rate("sig", dev, SIG_RATE):
                    return _bad(t("Zu viele Signale – bitte kurz warten."), 429)
            out = []
            for k, v in sorted(sig.items()) if fed is None else ():
                seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                                (call["id"], dev, k, v, now)).fetchone()[0]
                out.append({"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": k, "sealed": v})
            if fed is None:
                c.execute("UPDATE chat_call SET nsig=nsig+?, " + ("seen_a" if uid == call["caller"] else "seen_b") + "=? WHERE id=?",
                          (len(sig), now, call["id"]))
            for ev in out:
                _emit(c, [to_uid], "call.signal", ev)
    from starlette.concurrency import run_in_threadpool
    if fwd is not None:
        n = sum(len(x) for x in fwd["fwd"].values())
        if fwd["fwd"] and not await run_in_threadpool(FED.forward, call, uid, dev, fwd["fwd"], fwd["leg"]):
            return _bad(t("Die andere Seite ist gerade nicht erreichbar."), 502)
        return chat._no_store({"ok": True, "n": fwd["n"] + n})
    if fed is not None:
        return await run_in_threadpool(FED.signal, uid, fed, dev, fsig)
    return chat._no_store({"ok": True, "n": len(out)})


def pairs_budget(call, nlegs, now=None):
    """Signal-Budget eines Raums: je Endpunkt-Paar das Budget eines 1:1-Anrufs (Konzept §5.4)."""
    n = max(2, nlegs)
    return min(SIGNALS_HARD * (n - 1), sig_budget(call, now) * (n * (n - 1) // 2))


def _room_signal(c, call, who, dev, signals, legs, now, peer_id=None):
    """Signale eines Endpunkts im Raum pruefen und verteilen (lokal: speichern + Live-Ereignis; fremd: sammeln).
    -> {"n": lokal zugestellt, "fwd": {peer_id: {geraet: versiegelt}}, "leg": Endpunkt des Absenders} oder Fehler-Antwort.
    peer_id: Anfrage kam von diesem Server (Endpunkt dort)."""
    me = legs.get(dev)
    ringing_dm = call["kind"] == "dm" and call["state"] == "ringing"
    if ringing_dm:
        if dev != call["caller_device"] or (me or {}).get("who") != who:
            return _bad(t("ungültige Angabe"), 403)
        # vor dem Annehmen: an alle Geraete der angerufenen Person (auf dem anderen Server prueft der die Geraete)
        allowed = None if sharing.is_remote(call["callee"]) else _active_devices(c, call["callee"])
    else:
        if not me or me["who"] != who or me["state"] != "active":
            return _bad(t("ungültige Angabe"), 403)
        allowed = set(legs) - {dev}
    sig = _sealed_map(signals, allowed)
    if sig is None:
        return _bad(t("ungültige Angabe"))
    nall = c.execute("SELECT COUNT(*) FROM chat_call_leg WHERE call_id=?", (call["id"],)).fetchone()[0]
    if call["nsig"] + len(sig) > pairs_budget(call, nall, now) or not _rate("sig", dev, SIG_RATE):
        return _bad(t("Zu viele Signale – bitte kurz warten."), 429)
    fwd, n = _route(c, call, dev, sig, legs, now)
    c.execute("UPDATE chat_call SET nsig=nsig+? WHERE id=?", (len(sig), call["id"]))
    if peer_id is None:
        _seen(c, call, who, dev, now)
    return {"n": n, "fwd": fwd, "leg": me}


def _route(c, call, frm, sig, legs, now):
    """Zustellen: Endpunkt hier -> chat_call_signal + call.signal; Endpunkt anderswo -> {peer_id: {...}} (der Aufrufer
    schickt es nach dem Commit weiter). Vor dem Annehmen (1:1) gehen Signale an die Geraete der angerufenen Person."""
    fwd, n = {}, 0
    for k, v in sorted(sig.items()):
        x = legs.get(k)
        if x is None:
            to = call["callee"]
            pid = call["peer_id"] if sharing.is_remote(to) else None
        else:
            to, pid = x["who"], x["peer_id"]
        if pid is not None:
            fwd.setdefault(pid, {})[k] = v
            continue
        seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                        (call["id"], frm, k, v, now)).fetchone()[0]
        _emit(c, [to], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": frm, "to_device": k, "sealed": v})
        n += 1
    return fwd, n


def _seen(c, call, who, dev, now):
    """Lebenszeichen: Endpunkt (bzw. alle Endpunkte von who ohne Geraeteangabe) und - wie 2.3 - die Seite der Person."""
    if dev:
        c.execute("UPDATE chat_call_leg SET seen=? WHERE call_id=? AND device=? AND state='active'", (now, call["id"], dev))
    else:
        c.execute("UPDATE chat_call_leg SET seen=? WHERE call_id=? AND who=? AND state='active'", (now, call["id"], who))
    if call["kind"] == "group":
        c.execute("UPDATE chat_call SET seen_a=? WHERE id=?", (now, call["id"]))
    elif who in (call["caller"], call["callee"]):
        c.execute("UPDATE chat_call SET " + ("seen_a" if who == call["caller"] else "seen_b") + "=? WHERE id=?", (now, call["id"]))


@router.post("/api/calls/{cid}/alive")
async def api_alive(cid: str, request: Request):
    """Lebenszeichen waehrend Klingeln/Gespraech (alle ~15 s). -> Zustand (beendet: Client legt auf). 2.4: {device} -
    dann gilt es fuer diesen Endpunkt (state "left": dieses Geraet ist nicht mehr im Anruf)."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    dev = dev if _HEX32.fullmatch(dev) else ""
    with _db() as c, c:
        call, err = _mine(c, uid, cid)
        if err:
            return err
        out = {"state": call["state"], "reason": call["reason"]}
        if call["state"] != "ended":
            now = time.time()
            leg = c.execute("SELECT state, who FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], dev)).fetchone() if dev else None
            if leg and leg[1] == uid and leg[0] != "active":
                out["leg"] = "left"
            else:
                _seen(c, call, uid, dev if leg else "", now)
            if call.get("lead") is not None and FED is not None and call["kind"] == "group":
                FED.alive(uid, call, dev)                  # Gruppe: Lebenszeichen an den fuehrenden Server
            elif call["peer_id"] and FED is not None:
                FED.alive(uid, call, dev)                  # 2.3: Lebenszeichen auch an die andere Instanz (verwerfbar)
        return chat._no_store(out)


# --- 2.4 E4/E5: Beitreten, Verlassen, Einladen -------------------------------------------------------------------
@router.post("/api/calls/{cid}/join")
async def api_join(cid: str, request: Request):
    """Weiteres Geraet bzw. weitere Person tritt bei: {device, role:'member'|'companion'|'handover', replaces?, caps,
    signals:{geraet: versiegeltes Angebot}} - je ein Angebot an JEDEN aktiven Endpunkt anderer Personen (eigene Geraete
    optional). Der Hinzukommende bietet an (kein Glare). Fehler: 409 {join_block: old|full|ended|none, legs} -
    "braucht Saga 2.4" (ein Endpunkt ohne multi), voll (Grenze), beendet; 409 {legs} = Liste veraltet, neu anbieten."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    role = b.get("role") if b.get("role") in ROLES else "member"
    rep = b.get("replaces") if isinstance(b.get("replaces"), str) and _HEX32.fullmatch(b.get("replaces")) else None
    caps = caps_in(b.get("caps"))
    now = time.time()
    dcl = _dev_class(b)
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if not _my_device(c, uid, dev):
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        note_caps(c, uid, dev, caps)
        if call.get("lead") is not None and FED is not None:
            sig = _sealed_map(b.get("signals")) if b.get("signals") else {}
            if sig is None:
                return _bad(t("ungültige Angabe"))
            fed = call
        else:
            r = join_local(c, call, uid, dev, role, rep, caps, b.get("signals"), now, name=_dev_name(c, dev))
            if not isinstance(r, dict):
                return r
            fed = None
    from starlette.concurrency import run_in_threadpool
    if fed is not None:
        return await run_in_threadpool(FED.join, uid, fed, dev, role, rep, caps, sig)
    if r["fwd"] and not await run_in_threadpool(FED.forward, call, uid, dev, r["fwd"], r["leg"]):
        print(f"call: {cid[:8]} Angebot an einen anderen Server nicht zugestellt", flush=True)
    with _db() as c:
        call = _load(c, cid)
        d = _dict(c, call, uid)
        if is_horn(call):
            d["horn"] = horn_block(c, call, dev, r["leg"]["role"], dcl)
        return chat._no_store({"ok": True, "call": d})


def join_local(c, call, who, dev, role, rep, caps, signals, now, peer_id=None, name=""):
    """Beitritt pruefen und eintragen (dieser Server fuehrt den Anruf). who: Konto bzw. Prinzipal, peer_id: Server des
    Geraets (None = hier). -> {"fwd", "leg", "n"} oder Fehler-Antwort."""
    legs = _legs(c, call["id"])
    others = [x for x in legs if x["device"] != dev]
    if call["state"] == "ended":
        return chat._no_store({"error": t("Der Anruf ist schon beendet."), "join_block": "ended"}, 409)
    if any(x["device"] == dev for x in legs):
        return chat._no_store({"error": t("Dieses Gerät ist schon im Anruf."), "join_block": "already"}, 409)
    if call["kind"] != "group" and call["state"] != "active":
        return chat._no_store({"error": t("Der Anruf ist noch nicht angenommen."), "join_block": "none"}, 409)
    if not _has(caps, "multi"):
        return chat._no_store({"error": t("Dafür braucht dieses Gerät Saga 2.4."), "join_block": "old"}, 409)
    if _removed(c, call["id"], who):
        return chat._no_store({"error": t("Du wurdest aus diesem Anruf entfernt."), "join_block": "removed"}, 409)
    hn = is_horn(call)
    if hn and not _has(caps, "horn"):
        return chat._no_store({"error": t("Dafür braucht dieses Gerät Saga 2.5."), "join_block": "old"}, 409)
    old = [x for x in others if not _has(x["caps"], "multi")]
    if old or (_fedcall(c, call) and FED is not None and not FED.can3(c, call)):
        nm = names(c, [x["who"] for x in old])
        return chat._no_store({"error": t("Beitreten geht nicht: {names} braucht Saga 2.4.",
                                          names=", ".join(sorted({_who_label(c, x["who"], nm)["name"] for x in old})) or t("Gegenüber")),
                               "join_block": "old", "legs": roster(c, call, who, legs)}, 409)
    lim = climit(c, call)
    if len(others) + 1 > lim:
        return chat._no_store({"error": t("Der Anruf ist voll (höchstens {n} Geräte).", n=lim), "join_block": "full",
                               "limit": lim, "legs": roster(c, call, who, legs)}, 409)
    node = None
    if hn and signals:
        return _bad(t("ungültige Angabe"))             # ueber Knoten keine Mesh-Angebote
    if hn and peer_id is None:                         # 2.5: Knoten des Anrufs, sonst der beste (Geraete anderer Server
        node = horn_web.pick(c, call["id"])           # waehlen ueber ihren Server, saga/fed_horn.py)
        if node is None:
            horn.note_full(c)
            return chat._no_store({"error": t("Gerade ist kein Anruf-Knoten erreichbar – bitte gleich noch einmal."),
                                   "join_block": "node"}, 409)
    mine = [x for x in others if x["who"] == who]
    if role == "handover" and not any(x["device"] == rep for x in mine):
        return _bad(t("ungültige Angabe"))
    if role == "member" and mine:
        role = "companion"                             # zweites eigenes Geraet: Begleitgeraet (ohne Ton)
    if role != "member" and not mine:
        role, rep = "member", None
    if role != "handover":
        rep = None
    need = {x["device"] for x in others if x["who"] != who} if not hn else set()
    sig = _sealed_map(signals, {x["device"] for x in others}) if signals else {}
    if sig is None:
        return _bad(t("ungültige Angabe"))
    if not need <= set(sig):
        return chat._no_store({"error": t("Die Teilnehmenden haben sich geändert – bitte erneut beitreten."), "legs": roster(c, call, who, legs)}, 409)
    if not _rate("call", who, CALL_RATE):
        return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
    _leg_put(c, call["id"], dev, who, role, peer_id, caps, rep, name=name, now=now)
    if node is not None:
        horn.set_leg_node(c, call["id"], dev, node["id"])
    if call["kind"] == "group":
        c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,'joined',?) "
                  "ON CONFLICT (call_id, who) DO UPDATE SET state='joined'", (call["id"], who, now))
        if call["state"] == "ringing" and len({x["who"] for x in others} | {who}) >= 2:
            c.execute("UPDATE chat_call SET state='active', answered=? WHERE id=?", (now, call["id"]))
            call = _load(c, call["id"])
            _emit(c, _audience(c, call), "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "kind": "group",
                                                         **_dmf(call)})
    legs = _legs(c, call["id"])
    emit_roster(c, call, legs)                         # zuerst die Liste, dann die Angebote (Clients pruefen das neue Geraet)
    fwd, n = _route(c, call, dev, sig, {x["device"]: x for x in legs}, now)
    c.execute("UPDATE chat_call SET nsig=nsig+? WHERE id=?", (len(sig), call["id"]))
    print(f"call: {call['id'][:8]} Beitritt ({role}, {len(legs)} Endpunkte)", flush=True)
    return {"fwd": fwd, "leg": next(x for x in legs if x["device"] == dev), "n": n}


@router.post("/api/calls/{cid}/horn")
async def api_horn(cid: str, request: Request):
    """2.5 (Gjallarhorn): frische Kandidaten mit Medien-Token fuer MEIN Endpunkt {device, node?, dev_class?, avoid?} - vor
    dem Ablauf (60 s), beim Neuverbinden und nach dem Messen. node = gewaehlter Knoten (Name aus den Kandidaten): Saga
    merkt ihn am Endpunkt (ohne Kaskade nur der Knoten des Anrufs - laeuft dort nichts mehr, ein anderer bereiter).
    P5: avoid = [knotenname] - Knoten, die das Geraet nicht erreicht bzw. verlaesst (Leeren); sie fehlen in den
    Kandidaten, und der Anruf zaehlt dort fuer dieses Geraet nicht."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    with _db() as c, c:
        call, err = _mine(c, uid, cid)
        if err:
            return err
        leg = c.execute("SELECT who, state, role FROM chat_call_leg WHERE call_id=? AND device=?", (cid, dev)).fetchone() if _HEX32.fullmatch(dev) else None
        if is_horn(call) and leg and leg[0] == uid and (call["state"] == "ended" or leg[1] != "active"):
            # Bughunt 3: nach Auflegen/Verlassen bzw. Entfernen 404 wie "beendet" - Web und Apps hoeren dann auf (bei 409
            # galt das als "kein Knoten" und sie versuchten es bis zu 2 min weiter)
            return chat._no_store({"error": t("Der Anruf ist schon beendet."), "ended": True}, 404)
        if not is_horn(call) or call["state"] == "ended" or not leg or leg[0] != uid or leg[1] != "active":
            return _bad(t("ungültige Angabe"), 409)
        if not _rate("horn", dev, 30):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        avoid = horn_web._clean_avoid(b.get("avoid"))
        if call.get("lead") is None or FED is None:
            if isinstance(b.get("node"), str):
                n = horn.node_named(c, b["node"][:253])
                if not horn_web.may_use(c, cid, n, avoid):
                    return _bad(t("ungültige Angabe"), 409)
                horn.set_leg_node(c, cid, dev, n["id"])
            return chat._no_store({"ok": True, "horn": horn_block(c, call, dev, leg[2], _dev_class(b), avoid)})
    # 2.5 P10: Anruf, den ein anderer Server fuehrt - Kandidaten von dort (geteilte Knoten) und eigene (Kaskade)
    from starlette.concurrency import run_in_threadpool
    node = b["node"][:253] if isinstance(b.get("node"), str) else None
    blk, err = await run_in_threadpool(FED.horn_block, uid, call, dev, leg[2], _dev_class(b), avoid, node)
    if err:
        return _bad(err, 409)
    return chat._no_store({"ok": True, "horn": blk})


@router.post("/api/calls/{cid}/leave")
async def api_leave(cid: str, request: Request):
    """Ein eigenes Geraet verlaesst den Anruf: {device}. 1:1: geht das letzte Geraet einer Seite, endet der Anruf; nach
    einer Uebernahme laeuft er mit dem neuen Geraet weiter. Gruppe: laeuft weiter, solange mindestens zwei Personen dabei
    sind."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    dev = str(b.get("device") or "")
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        leg = c.execute("SELECT who, state FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], dev)).fetchone() if _HEX32.fullmatch(dev) else None
        if not leg or leg[0] != uid:
            return _bad(t("ungültige Angabe"))
        if leg[1] != "active" or call["state"] == "ended":
            return chat._no_store({"ok": True, "already": True})
        if call.get("lead") is not None and FED is not None:
            fed = call
        else:
            _leave(c, call, dev, time.time(), b.get("reason") if b.get("reason") in ("handover", "hangup") else "hangup", by=uid)
            return chat._no_store({"ok": True})
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(FED.leave, uid, fed, dev)


def _leave(c, call, dev, now, reason="hangup", by=None):
    """Endpunkt verlaesst den Anruf (dieser Server fuehrt ihn). 1:1: war es das Hauptgeraet einer Seite, uebernimmt ein
    anderes Geraet derselben Person (Uebernahme bzw. Begleitgeraet), sonst endet der Anruf. Gruppe: weniger als zwei
    Personen -> Ende."""
    leg = next((x for x in _legs(c, call["id"]) if x["device"] == dev), None)
    if not leg:
        return False
    c.execute("UPDATE chat_call_leg SET state='left', left_at=?, reason=? WHERE call_id=? AND device=?", (now, reason, call["id"], dev))
    if is_horn(call):
        horn.kick(c, call["id"], dev, leg.get("node"))  # 2.5: Beine am Knoten sofort beenden
    legs = _legs(c, call["id"])
    if call["kind"] == "group":
        people = {x["who"] for x in legs}
        if not legs or (call["state"] == "active" and len(people) < 2):
            call["_connected"] = call["state"] == "active"
            _finish(c, call, "ended" if call["state"] == "active" else "cancelled", now, by=by)
            return True
        own = [x for x in legs if x["who"] == leg["who"]]
        if leg["role"] != "companion" and own and all(x["role"] != "member" for x in own):
            # Hauptgeraet weg (Uebernahme bzw. "Hierher uebernehmen" am Begleitgeraet): das neue Geraet wird Mitglied
            own.sort(key=lambda x: (x["replaces"] != dev, x["role"] != "handover", -(x["joined"] or 0)))
            c.execute("UPDATE chat_call_leg SET role='member', replaces=NULL WHERE call_id=? AND device=?", (call["id"], own[0]["device"]))
            legs = _legs(c, call["id"])
    elif dev in (call["caller_device"], call["callee_device"]):
        side = "caller_device" if dev == call["caller_device"] else "callee_device"
        nxt = [x for x in legs if x["who"] == leg["who"]]
        nxt.sort(key=lambda x: (x["replaces"] != dev, x["role"] != "handover", -(x["joined"] or 0)))
        if not nxt:
            call["_connected"] = call["state"] == "active"
            _finish(c, call, "hangup", now, by=by)
            return True
        c.execute(f"UPDATE chat_call SET {side}=? WHERE id=?", (nxt[0]["device"], call["id"]))
        c.execute("UPDATE chat_call_leg SET role='member', replaces=NULL WHERE call_id=? AND device=?", (call["id"], nxt[0]["device"]))
        call = _load(c, call["id"])
        legs = _legs(c, call["id"])
        _emit(c, _audience(c, call), "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "device": nxt[0]["device"],
                                                     "moved": side.split("_")[0]})
    emit_roster(c, call, legs)
    print(f"call: {call['id'][:8]} Endpunkt weg ({reason}, {len(legs)} uebrig)", flush=True)
    return True


def _removed(c, cid, uid):
    """2.5 P5: hat die Gastgeberin diese Person aus dem Anruf entfernt? (bis sie erneut eingeladen wird)"""
    r = c.execute("SELECT state FROM chat_call_invite WHERE call_id=? AND who=?", (cid, uid)).fetchone()
    return bool(r) and r[0] == "removed"


@router.post("/api/calls/{cid}/moderate")
async def api_moderate(cid: str, request: Request):
    """2.5 P5: Gastgeber (wer den Gruppenanruf begonnen hat) schaltet ein anderes Geraet stumm oder entfernt es:
    {device (eigenes, aktives), target (Geraet im Anruf), action: mute | remove}.
    mute: Ereignis call.mod an die Person des Geraets - ihr Client schaltet das Mikrofon aus (sie kann es wieder
    einschalten; Ton bleibt Ende-zu-Ende verschluesselt, der Server kann nicht selbst stummschalten).
    remove: Endpunkt verlaesst den Anruf (Grund removed, am Knoten sofort beendet), call.mod an die Person; sie kann
    nicht wieder beitreten, bis sie erneut eingeladen wird (POST /invite). Die anderen wechseln wie bei jedem Abgang
    den Frame-Schluessel."""
    uid, err = _me(request)
    if err:
        return err
    if not horn_on():                                  # nur mit Feature-Flag calls.horn (Gastgeber-Funktionen des Web-Clients P5)
        return _bad("not available", 404)
    b = await _body(request) or {}
    dev, tgt, act = str(b.get("device") or ""), str(b.get("target") or ""), b.get("action")
    if act not in ("mute", "remove") or not _HEX32.fullmatch(dev) or not _HEX32.fullmatch(tgt) or dev == tgt:
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["kind"] != "group" or call["state"] == "ended" or call.get("lead") is not None:
            return _bad(t("ungültige Angabe"), 409)
        legs = {x["device"]: x for x in _legs(c, cid)}
        if (legs.get(dev) or {}).get("who") != uid:
            return _bad(t("ungültige Angabe"), 409)
        if call["caller"] != uid:
            return _bad(t("Nur wer den Anruf begonnen hat, kann andere stummschalten oder entfernen."), 403)
        x = legs.get(tgt)
        if not x or x["who"] == uid:
            return _bad(t("ungültige Angabe"), 409)
        if not _rate("moderate", uid, 30):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        nm = names(c, [uid])
        ev = {"call_id": cid, "chat_id": call["chat_id"], "action": act, "device": tgt, "by": nm.get(uid) or local_ref(uid)}   # nie der Anmeldename
        if act == "remove":
            c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,'removed',?) ON CONFLICT (call_id, who) "
                      "DO UPDATE SET state='removed'", (cid, x["who"], time.time()))
            for d, y in legs.items():                   # alle Geraete der Person (auch Begleitgeraete)
                if y["who"] == x["who"]:
                    _leave(c, _load(c, cid), d, time.time(), "removed", by=uid)
        if not sharing.is_remote(x["who"]):
            _emit(c, [x["who"]], "call.mod", ev)
        print(f"call: {cid[:8]} Gastgeber: {act}", flush=True)
    return chat._no_store({"ok": True})


@router.post("/api/calls/{cid}/invite")
async def api_invite(cid: str, request: Request):
    """Gruppenanruf: weitere Mitglieder (noch einmal) klingeln lassen {uids: [benutzername | '@~x@host']}."""
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    who = [u for u in (b.get("uids") or []) if isinstance(u, str) and 0 < len(u) < 300][:chat.MEMBERS_MAX]
    with _db() as c, c:
        call, err = _mine(c, uid, cid, lock=True)
        if err:
            return err
        if call["kind"] != "group" or call["state"] == "ended":
            return _bad(t("ungültige Angabe"))
        if not _rate("invite", uid, CALL_RATE):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        n, remote = invite_local(c, call, who)
    if remote and FED is not None:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(FED.invite, uid, call, remote)
    return chat._no_store({"ok": True, "n": n + len(remote)})


def invite_local(c, call, who):
    """Mitglieder erneut klingeln lassen (nicht, wer schon dabei ist). -> (hier geklingelt, fremde Prinzipale)."""
    mem = set(chat.members(c, call["chat_id"]))
    inside = {x["who"] for x in _legs(c, call["id"])}
    ring, remote = [], []
    now = time.time()
    if _v3(c, call):                                   # 2.5: Stufe 3 nennt Mitglieder per Handle
        who = [chat.who_in(c, call["caller"], u) if chat._FULL_H.fullmatch(u) else u for u in who]
    for u in who:
        if u not in mem or u in inside:
            continue
        if sharing.is_remote(u):
            remote.append(u)
            continue
        st = "ringing" if (_can_horn(c, u) if is_horn(call) else _can_multi(c, u)) else "old"
        c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,?,?) ON CONFLICT (call_id, who) DO UPDATE "
                  "SET state=EXCLUDED.state, created=EXCLUDED.created", (call["id"], u, st, now))
        if st == "ringing":
            ring.append(u)
    ring_group(c, call, ring)
    return len(ring), remote


# --- Hintergrund: Klingeldauer, verlorene Verbindungen, Aufraeumen -------------------------------------------------
_RLAST = {}


def tick(now=None):
    """-> Anzahl beendeter Anrufe. Klingelt zu lange (verpasst), Anrufer weg (abgebrochen), keine Lebenszeichen (lost).
    2.4: Endpunkte ohne Lebenszeichen, mit abgemeldetem Geraet oder ohne Mitgliedschaft verlassen den Anruf; Gruppenanruf,
    dem niemand beitritt, endet nach der Klingeldauer; Kopien eines Gruppenanrufs ohne Nachricht vom fuehrenden Server
    enden nach MIRROR_GAP."""
    now = now or time.time()
    n = 0
    rs = ring_s()
    with _db() as c, c:
        # Geraet waehrend des Anrufs abgemeldet/gesperrt (Chat "Geraete"): Anruf sofort beenden - ein entferntes Geraet
        # darf weder weiter signalisieren noch ein Gespraech offen halten (gilt fuer jeden Prozess, kein Live-Ereignis noetig:
        # die Clients erfahren es spaetestens ueber /alive)
        for call in [_row(r) for r in c.execute(
                f"SELECT {C_COLS} FROM chat_call WHERE state<>'ended' AND kind='dm' AND EXISTS (SELECT 1 FROM chat_device d "
                "WHERE d.id IN (chat_call.caller_device, chat_call.callee_device) AND d.revoked_at IS NOT NULL) FOR UPDATE SKIP LOCKED").fetchall()]:
            call["_connected"] = call["state"] == "active"
            n += bool(_finish(c, call, "gone", now))
            _OWN.discard(call["id"])
        rows = [_row(r) for r in c.execute(
            f"SELECT {C_COLS} FROM chat_call WHERE kind='dm' AND ((state='ringing' AND (created<? OR seen_a<?)) "
            "OR (state='active' AND (LEAST(seen_a, seen_b)<? OR answered<?))) FOR UPDATE SKIP LOCKED",
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
        n += _tick_rooms(c, now, rs)
        c.execute("DELETE FROM chat_call WHERE state='ended' AND ended<?", (now - KEEP_S,))
        if len(_OWN) > 5000:
            _OWN.clear()
    return n


def _tick_rooms(c, now, rs):
    """2.4: Endpunkte und Gruppenanrufe (nur Anrufe, die dieser Server fuehrt; Kopien: MIRROR_GAP)."""
    n = 0
    # Endpunkte ohne Lebenszeichen, mit abgemeldetem Geraet bzw. (Gruppe) ohne Mitgliedschaft -> verlassen
    rows = c.execute(
        "SELECT l.call_id, l.device, CASE WHEN l.peer_id IS NULL AND EXISTS (SELECT 1 FROM chat_device d WHERE d.id=l.device "
        "AND d.revoked_at IS NOT NULL) THEN 'gone' WHEN k.kind='group' AND NOT EXISTS (SELECT 1 FROM chat_member m WHERE "
        "m.chat_id=k.chat_id AND m.uid=l.who AND m.left_at IS NULL) THEN 'removed' WHEN l.seen<? THEN 'lost' END AS why "
        "FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.state='active' AND k.state<>'ended' AND k.lead IS NULL "
        "AND (k.v=3 OR k.kind='group')", (now - ALIVE_GAP,)).fetchall()
    for cid, dev, why in rows:
        if not why:
            continue
        call = _load(c, cid, lock=True)
        if not call or call["state"] == "ended":
            continue
        if why == "lost" and cid not in _OWN:
            seen = c.execute("SELECT seen FROM chat_call_leg WHERE call_id=? AND device=?", (cid, dev)).fetchone()
            if seen and (seen[0] or 0) >= now - ALIVE_GAP - ORPHAN:
                continue
        if call["kind"] == "dm" and dev in (call["caller_device"], call["callee_device"]) and why == "lost":
            continue                                   # 1:1: Hauptgeraete wie bisher ueber seen_a/seen_b (oben)
        n += bool(_leave(c, call, dev, now, why)) and _load(c, cid)["state"] == "ended"
    # Gruppenanruf, dem niemand beitritt
    for call in [_row(r) for r in c.execute(
            f"SELECT {C_COLS} FROM chat_call WHERE kind='group' AND state='ringing' AND lead IS NULL AND created<? FOR UPDATE SKIP LOCKED",
            (now - rs,)).fetchall()]:
        n += bool(_finish(c, call, "timeout", now))
    # Kopien von Gruppenanrufen anderer Server: ohne Nachricht vom fuehrenden Server (seen_b) -> beendet
    for call in [_row(r) for r in c.execute(
            f"SELECT {C_COLS} FROM chat_call WHERE kind='group' AND state<>'ended' AND lead IS NOT NULL AND seen_b<? FOR UPDATE SKIP LOCKED",
            (now - MIRROR_GAP,)).fetchall()]:
        n += bool(_finish(c, call, "gone", now, remote=True))
    # fuehrender Server: Teilnehmerliste regelmaessig an die anderen Server (sie halten ihre Kopie damit am Leben)
    if FED is not None:
        for call in [_row(r) for r in c.execute(
                f"SELECT {C_COLS} FROM chat_call WHERE kind='group' AND state<>'ended' AND lead IS NULL").fetchall()]:
            if now - _RLAST.get(call["id"], call["created"]) >= ROSTER_EVERY and _fedcall(c, call):
                _RLAST[call["id"]] = now
                FED.roster_out(c, call, _legs(c, call["id"]))
        if len(_RLAST) > 2000:
            _RLAST.clear()
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


def chat_extra(c, uid, ch, d):
    """2.4: /api/chats/{cid} -> call: {group: Gruppenanruf hier moeglich, group_block: Hinweis, running: laufender Anruf
    {id, media, kind, n, joinable} oder None}. Nur beim einzelnen Chat (nicht in der Liste - keine Zusatzabfragen)."""
    if not enabled():
        d["call"] = {"group": False, "group_block": t("Anrufe sind auf diesem Server abgeschaltet."), "running": None}
        return
    ok_, why = (True, None) if ch["kind"] == "dm" else ((FED.chat_flags(c, ch) if chat.is_fed(ch) else (True, None)))
    r = c.execute(f"SELECT {C_COLS} FROM chat_call WHERE chat_id=? AND state<>'ended' AND (kind='group' OR caller=? OR callee=?) "
                  "ORDER BY created DESC LIMIT 1", (ch["id"], uid, uid)).fetchone()
    run = None
    if r:
        call = _row(r)
        legs = _legs(c, call["id"])
        can, blk = _joinable(c, call, uid, legs)
        run = {"id": call["id"], "media": call["media"], "kind": call["kind"], "state": call["state"], "n": len(legs), "joinable": can,
               "join_block": blk, "mine": [x["device"] for x in legs if x["who"] == uid], "topo": call.get("topo") or "p2p"}
    d["call"] = {"group": ch["kind"] != "dm" and ok_, "group_block": why, "running": run}


chat.CALLS = __import__("sys").modules[__name__]

# Benachrichtigung "Verpasster Anruf": Titel/Link wie bei Chat-Nachrichten (nur fuer aktuelle Mitglieder)
notify.RENDER["call.missed"] = chat._notif
