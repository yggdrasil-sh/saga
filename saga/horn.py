"""Gjallarhorn-Steuerung (Saga 2.5, Paket P3; Konzept v2 §2.1-2.4, §2.6, §2.10): Knotenregister, Saga-Signaturschluessel,
Medien-Token, Kandidaten im Beitritt und Steuerkanal horn-ctl/1 (WebSocket /api/horn/ctl).

Python bleibt die Instanz fuer Rechte und Signale (saga/calls.py). Medienbytes laufen nie durch Python: Geraete senden
Ton und Bild direkt an einen Knoten (gjallarhorn/, Go), Python stellt nur kurzlebige, signierte Medien-Token aus.

Medien-Token (Gegenstueck gjallarhorn/horntoken.go):
    h1.<payload>.<sig>   payload = B64URL(JSON), sig = B64URL(Ed25519(horn_sign, "saga-horn-token/1." + payload))
    JSON {v:1, node, call_ref, peer_ref, leg_ref, role: audio|video|both, pub: [a,v,s], max_kbit, udp_ok,
          dev_class: desktop|mobile, exp: jetzt + 60 s, jti}
  call_ref/peer_ref/leg_ref leitet Saga je (Anruf, Knoten[, Geraet[, Rolle]]) mit HMAC aus einem Geheimnis ab - je Knoten
  andere, zufaellig aussehende Kennungen, keine Konto- oder Geraetenamen. Der Knoten prueft lokal (kein Rueckruf), nimmt
  jedes Token nur einmal (jti) und nur fuer sich selbst (node). Ein Bein je leg_ref; ein zweites ersetzt das erste.
  horn_sign: Ed25519, liegt verschluesselt in den Einstellungen (Bereich "horn", Fernet "enc:" wie alle Geheimnisse).

Steuerkanal horn-ctl/1 (der Knoten verbindet sich AUSGEHEND, gjallarhorn/ctl.go): challenge -> hello (vom Knoten
signiert) -> welcome {node_id, saga_pub, origin, rooms}; danach Folgenummern je Richtung. Saga -> Knoten: open, close,
kick, ping. Knoten -> Saga: load (alle 5 s), closed, pong. 2.5 P3 nur fuer den eingebauten Knoten (gemeinsames Geheimnis
SAGA_HORN_KEY von saga-media); gekoppelte Knoten ("Erweitern", horn_pair) folgen mit P6, Kaskade mit P9, Foederation
mit P10 (CASCADE, share bleiben bis dahin aus)."""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlparse
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga.core import BASE_URL, E, MEDIA_KEY, SECRET

router = APIRouter()

HORN_KEY = E.pop("SAGA_HORN_KEY", "")   # von saga-media (eingebauter Knoten); nicht an Kindprozesse weitergeben
PROTO = "horn-ctl/1"
TOKEN_TTL = 60                          # Medien-Token gelten 60 s
READY_S = 30                            # Knoten gilt als bereit, solange seine letzte Lastmeldung juenger ist
SEEN_EVERY = 10                         # Lastmeldung hoechstens so oft in die Datenbank (Bereitschaft fuer alle Prozesse)
CANDIDATES = 3                          # Kandidaten im Beitritt (§2.6)
CASCADE = False                         # P9: ohne Kaskade bleibt ein Anruf auf EINEM Knoten (nur dieser ist Kandidat)
MAX_KBIT = 4000                         # Eingang je Bein (§3.4)
FULL, PENALTY = 0.9, 0.7                # Auslastung: ab 90 % voll, ab 70 % Abzug bei der Wahl
LIMITS = {"tcp": (12, 50), "udp": (25, 100)}   # Endpunkte je Anruf (Video, nur Ton) nach Weg (§1.4, Entscheidung 4)
PUB = {"audio": ["a"], "video": ["v", "s"], "both": ["a", "v", "s"]}

_SK = {"k": None, "pub": ""}
_LOCK = threading.Lock()
_LIVE = {}                              # node_id -> {"load": {...}, "at": t, "db": t}  (nur dieser Prozess)
_CONNS = {}                             # node_id -> _Conn (Steuerverbindung in diesem Prozess)


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def horn_on():
    """Feature-Flag calls.horn (Standard aus, gleiche Quelle wie saga/calls.py)."""
    c = auth.settings().get("calls") or {}
    return bool(c.get("horn")) and bool(c.get("enabled", True))


# --- Saga-Signaturschluessel horn_sign ------------------------------------------------------------------------------
def _signer():
    """Ed25519-Schluessel von Saga (einmal erzeugt, verschluesselt in den Einstellungen). Mehrere Prozesse an derselben
    Datenbank: wer zuerst schreibt, gewinnt; alle lesen danach dasselbe."""
    if _SK["k"] is not None:
        return _SK["k"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    with _LOCK:
        if _SK["k"] is not None:
            return _SK["k"]
        for _ in range(2):
            seed = _seed(auth.settings(fresh=True).get("horn") or {})
            if seed:
                k = Ed25519PrivateKey.from_private_bytes(seed)
                _SK.update(k=k, pub=base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
                return k
            seed = secrets.token_bytes(32)
            pub = base64.b64encode(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
            v = json.dumps({"sign_sk": auth._enc(base64.b64encode(seed).decode()), "sign_pk": pub})
            with _db() as c, c:
                row = c.execute("SELECT v FROM setting WHERE k='horn'").fetchone()
                if row is None:
                    c.execute("INSERT INTO setting (k, v) VALUES ('horn', ?) ON CONFLICT (k) DO NOTHING", (v,))
                elif not _seed(_dec_horn(row[0])):          # unlesbar (SECRET_KEY gewechselt): neu
                    c.execute("UPDATE setting SET v=? WHERE k='horn' AND v=?", (v, row[0]))
            auth._settings_cache["v"] = None
        raise RuntimeError("horn_sign nicht lesbar")


def _dec_horn(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return {"sign_sk": auth._dec(d.get("sign_sk"))} if isinstance(d, dict) else {}


def _seed(h):
    try:
        s = base64.b64decode(h.get("sign_sk") or "", validate=True)
    except ValueError:
        return None
    return s if len(s) == 32 else None


def saga_pub():
    _signer()
    return _SK["pub"]


# --- Kennungen und Medien-Token -------------------------------------------------------------------------------------
_REFK = hashlib.sha256(("saga-horn-ref|" + SECRET).encode()).digest()


def _ref(*parts):
    return _b64u(hmac.new(_REFK, "|".join(parts).encode(), hashlib.sha256).digest()[:16])


def call_ref(cid, node_id):
    return _ref("call", cid, node_id)


def peer_ref(cid, node_id, dev):
    return _ref("peer", cid, node_id, dev)


def leg_ref(cid, node_id, dev, role):
    return _ref("leg", cid, node_id, dev, role)


def token(node, cid, dev, role, udp_ok=False, dev_class="desktop", pub=None, max_kbit=MAX_KBIT, now=None):
    """Medien-Token fuer ein Bein (role audio | video | both) dieses Geraets auf dem Knoten."""
    body = {"v": 1, "node": node["name"], "call_ref": call_ref(cid, node["id"]), "peer_ref": peer_ref(cid, node["id"], dev),
            "leg_ref": leg_ref(cid, node["id"], dev, role), "role": role, "pub": PUB[role] if pub is None else pub,
            "max_kbit": int(max_kbit), "udp_ok": bool(udp_ok), "dev_class": "mobile" if dev_class == "mobile" else "desktop",
            "exp": int(now or time.time()) + TOKEN_TTL, "jti": _b64u(secrets.token_bytes(12))}
    p = _b64u(json.dumps(body, separators=(",", ":")).encode())
    return "h1." + p + "." + _b64u(_signer().sign(("saga-horn-token/1." + p).encode()))


# --- Knotenregister -------------------------------------------------------------------------------------------------
N_COLS = "id, name, pub, builtin, url, udp, share, version, state, load, seen"


def _nrow(r):
    return dict(zip([x.strip() for x in N_COLS.split(",")], r)) if r else None


def nodes(c):
    return [_nrow(r) for r in c.execute(f"SELECT {N_COLS} FROM horn_node WHERE removed IS NULL ORDER BY builtin DESC, name").fetchall()]


def node_by_id(c, nid):
    return _nrow(c.execute(f"SELECT {N_COLS} FROM horn_node WHERE id=? AND removed IS NULL", (nid,)).fetchone()) if nid else None


def util(n):
    """Auslastung = max(cpu, Beine / max_legs) aus der letzten Lastmeldung (Upload/link_mbit folgt mit P6)."""
    ld = (_LIVE.get(n["id"]) or {}).get("load") or n.get("load") or {}
    try:
        u = max(float(ld.get("cpu") or 0), float(ld.get("legs") or 0) / max(1, int(ld.get("max_legs") or 48)))
    except (TypeError, ValueError):
        u = 0.0
    return 1.0 if ld.get("state") in ("voll", "draining", "update", "fehler") else u


def ready(n, now=None):
    """Knoten nimmt neue Beine an: verbunden (Lastmeldung juenger als READY_S), Zustand ok, nicht voll."""
    now = now or time.time()
    live = _LIVE.get(n["id"])
    seen = max(n.get("seen") or 0, (live or {}).get("at") or 0)
    return n["state"] == "ok" and now - seen < READY_S and util(n) < FULL and bool(n.get("url"))


def ready_nodes(c, now=None):
    """Bereite Knoten, beste zuerst (kleine Auslastung; ab PENALTY Abzug)."""
    ns = [n for n in nodes(c) if ready(n, now)]
    ns.sort(key=lambda n: (util(n) + (0.5 if util(n) >= PENALTY else 0), not n["builtin"], n["name"]))
    return ns


def any_ready(c):
    return horn_on() and bool(ready_nodes(c))


def call_node(c, cid):
    """Knoten, auf dem der Anruf laeuft (die meisten aktiven Endpunkte), oder None."""
    r = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND state='active' AND node IS NOT NULL GROUP BY node "
                  "ORDER BY COUNT(*) DESC, node LIMIT 1", (cid,)).fetchone()
    return r[0] if r else None


def limits(c, cid=None, node=None, udp=None):
    """(Video, nur Ton) Endpunkte je Anruf ueber Knoten: TCP 12/50, mit UDP am Knoten des Anrufs 25/100. Der Admin kann
    nur senken (calls.horn_video / calls.horn_audio)."""
    if udp is None:
        n = node or (node_by_id(c, call_node(c, cid)) if cid else None)
        udp = bool(n and n["udp"])
    v, a = LIMITS["udp" if udp else "tcp"]
    cf = auth.settings().get("calls") or {}
    try:
        v = max(2, min(v, int(cf.get("horn_video") or v)))
        a = max(2, min(a, int(cf.get("horn_audio") or a)))
    except (TypeError, ValueError):
        pass
    return v, a


def candidates(c, call, dev, role="member", dev_class="desktop", now=None):
    """Bis zu CANDIDATES Knoten fuer dieses Geraet, je mit eigenen Medien-Token (Ton- und Bild-Leitung fuer TCP-Wege,
    dazu 'both' fuer den UDP-Weg). Laeuft der Anruf schon auf einem Knoten, kommt dieser zuerst (ohne Kaskade: nur er).
    Begleitgeraet: ohne Ton senden."""
    now = now or time.time()
    ns = ready_nodes(c, now)
    cur = call_node(c, call["id"])
    if cur:
        ns = [n for n in ns if n["id"] == cur] if not CASCADE else sorted(ns, key=lambda n: n["id"] != cur)
    out = []
    for n in ns[:CANDIDATES]:
        udp = bool(n["udp"])
        toks = {}
        for r in ("audio", "video") + (("both",) if udp else ()):
            pub = [x for x in PUB[r] if not (role == "companion" and x == "a")]
            toks[r] = token(n, call["id"], dev, r, udp, dev_class, pub, now=now)
        out.append({"node": n["name"], "url": n["url"], "udp": udp, "here": n["id"] == cur, "load": round(util(n), 3),
                    "peer": peer_ref(call["id"], n["id"], dev), "tokens": toks, "exp": int(now) + TOKEN_TTL})
    return out


def node_named(c, name):
    r = c.execute(f"SELECT {N_COLS} FROM horn_node WHERE name=? AND removed IS NULL", (name,)).fetchone()
    return _nrow(r)


def set_leg_node(c, cid, dev, node_id):
    c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=? AND state='active'", (node_id, cid, dev))


def leg_node(c, cid, dev):
    r = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND device=?", (cid, dev)).fetchone()
    return r[0] if r else None


# --- Befehle an Knoten (nach dem Commit) ----------------------------------------------------------------------------
def _later(c, fn):
    after = getattr(c, "after_commit", None)
    after(fn) if after is not None else fn()


def send(node_id, msg):
    """Nachricht an einen verbundenen Knoten (aus jedem Thread). Ohne Verbindung in diesem Prozess: verworfen - der
    Knoten raeumt leere Raeume selbst ab und gleicht beim Verbinden ab (welcome.rooms)."""
    k = _CONNS.get(node_id)
    if k is None:
        return False
    try:
        k.loop.call_soon_threadsafe(k.q.put_nowait, msg)
    except RuntimeError:
        return False
    return True


def room_open(c, cid, node_id, max_peers=None):
    _later(c, lambda: send(node_id, {"t": "open", "call_ref": call_ref(cid, node_id), **({"max_peers": max_peers} if max_peers else {})}))


def room_close(c, cid):
    """Anruf vorbei: Raum auf allen Knoten des Anrufs schliessen."""
    ids = [r[0] for r in c.execute("SELECT DISTINCT node FROM chat_call_leg WHERE call_id=? AND node IS NOT NULL", (cid,)).fetchall()]
    for nid in ids:
        _later(c, lambda nid=nid: send(nid, {"t": "close", "call_ref": call_ref(cid, nid)}))


def kick(c, cid, dev, node_id):
    """Endpunkt hat den Anruf verlassen bzw. wurde entfernt: Beine am Knoten sofort beenden (keine neuen mit alten Token)."""
    if node_id:
        _later(c, lambda: send(node_id, {"t": "kick", "call_ref": call_ref(cid, node_id), "peer_ref": peer_ref(cid, node_id, dev)}))


def _active_rooms(node_id):
    with _db() as c:
        rows = c.execute("SELECT DISTINCT l.call_id FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.node=? "
                         "AND l.state='active' AND k.state<>'ended'", (node_id,)).fetchall()
    return [call_ref(r[0], node_id) for r in rows]


# --- Steuerkanal horn-ctl/1 -----------------------------------------------------------------------------------------
class _Conn:
    def __init__(self, ws, node_id):
        self.ws, self.node_id = ws, node_id
        self.loop = asyncio.get_running_loop()
        self.q = asyncio.Queue(maxsize=10000)
        self.n = 0


def _origin():
    u = urlparse(BASE_URL)
    return f"{u.scheme}://{u.netloc}"


def _verify_hello(h, nonce):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = base64.b64decode(h.get("pub") or "", validate=True)
        sig = base64.b64decode(h.get("sig") or "", validate=True)
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, (PROTO + " hello\n" + nonce).encode())
    except (ValueError, InvalidSignature):
        return False
    return len(pub) == 32


def _clean_hello(h):
    name = str(h.get("name") or "").strip().lower()[:253]
    url = str(h.get("url") or "").strip().rstrip("/")[:300]
    pu = urlparse(url)
    if not name or pu.scheme != "https" or not pu.netloc:
        return None
    return {"name": name, "url": url, "pub": str(h.get("pub")), "udp": bool(h.get("udp")), "version": str(h.get("version") or "")[:40],
            "max_legs": int(h.get("max_legs") or 0) if str(h.get("max_legs") or "0").isdigit() else 0}


def register_builtin(h, now=None):
    """Eingebauten Knoten eintragen bzw. aktualisieren (genau einer; Name und Schluessel duerfen sich aendern)."""
    now = now or time.time()
    with _db() as c, c:
        r = c.execute("SELECT id FROM horn_node WHERE builtin=1 AND removed IS NULL").fetchone()
        clash = c.execute("SELECT id FROM horn_node WHERE name=? AND builtin=0 AND removed IS NULL", (h["name"],)).fetchone()
        if clash:
            return None
        if r:
            nid = r[0]
            c.execute("UPDATE horn_node SET name=?, pub=?, url=?, udp=?, version=?, state='ok', seen=? WHERE id=?",
                      (h["name"], h["pub"], h["url"], h["udp"], h["version"], now, nid))
        else:
            nid = secrets.token_hex(8)
            c.execute("DELETE FROM horn_node WHERE name=? AND removed IS NOT NULL", (h["name"],))
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, created, seen) "
                      "VALUES (?,?,?,1,?,?,?,'ok',?,?)", (nid, h["name"], h["pub"], h["url"], h["udp"], h["version"], now, now))
    print(f"horn: eingebauter Knoten {h['name']} verbunden ({h['url']}, UDP {'ja' if h['udp'] else 'nein'})", flush=True)
    return nid


def _gone(nid):
    with _db() as c, c:
        c.execute("UPDATE horn_node SET state='getrennt' WHERE id=? AND state='ok'", (nid,))


def _store_load(nid, ld, now):
    with _db() as c, c:
        st = "ok" if ld.get("state") in (None, "ok", "voll") else str(ld.get("state"))[:16]
        c.execute("UPDATE horn_node SET load=CAST(? AS JSONB), seen=?, state=? WHERE id=? AND state IN ('ok','getrennt','voll')",
                  (json.dumps(ld), now, st, nid))


_LOAD_KEYS = ("cpu", "mem", "legs", "calls", "peers", "udp_share", "fwd_p95_us", "drops", "down_kbit", "max_legs", "state")


@router.websocket("/api/horn/ctl")
async def ctl(ws: WebSocket):
    """Steuerverbindung eines Knotens. 2.5 P3: nur der eingebaute Knoten (X-Saga-Horn-Key von saga-media)."""
    key = ws.headers.get("x-saga-horn-key", "")
    if not HORN_KEY or not hmac.compare_digest(key.encode(), HORN_KEY.encode()) or not horn_on():
        await ws.close(code=4403)
        return
    await ws.accept()
    nonce = _b64u(secrets.token_bytes(24))
    try:
        await ws.send_json({"t": "challenge", "nonce": nonce, "proto": PROTO})
        h = await asyncio.wait_for(ws.receive_json(), 10)
    except Exception:  # noqa: BLE001 - Verbindung weg / kein JSON
        await _close(ws, 4400)
        return
    hh = _clean_hello(h) if isinstance(h, dict) and h.get("t") == "hello" and h.get("proto") == PROTO else None
    if hh is None or not _verify_hello(h, nonce):
        await _close(ws, 4401)
        return
    nid = await asyncio.to_thread(register_builtin, hh)
    if nid is None:
        await _close(ws, 4409)
        return
    pub = await asyncio.to_thread(saga_pub)
    rooms = await asyncio.to_thread(_active_rooms, nid)
    k = _Conn(ws, nid)
    old = _CONNS.get(nid)
    _CONNS[nid] = k
    if old is not None:
        old.q.put_nowait(None)
    _LIVE[nid] = {"load": {"max_legs": hh["max_legs"], "legs": 0, "state": "ok"}, "at": time.time(), "db": time.time()}
    await ws.send_json({"t": "welcome", "n": 0, "node_id": nid, "saga_pub": pub, "origin": _origin(), "rooms": rooms, "proto": PROTO})

    async def writer():
        while True:
            m = await k.q.get()
            if m is None:
                return
            k.n += 1
            m["n"] = k.n
            await ws.send_json(m)

    async def pinger():
        while True:
            await asyncio.sleep(20)
            k.q.put_nowait({"t": "ping"})

    wt, pt = asyncio.create_task(writer()), asyncio.create_task(pinger())
    last = 0
    try:
        while not wt.done():
            m = await asyncio.wait_for(ws.receive_json(), 60)
            if not isinstance(m, dict) or m.get("n") != last + 1:
                break
            last = m["n"]
            if m.get("t") == "load" and isinstance(m.get("load"), dict):
                ld = {x: m["load"].get(x) for x in _LOAD_KEYS if x in m["load"]}
                now = time.time()
                live = _LIVE.setdefault(nid, {"db": 0})
                live.update(load=ld, at=now)
                if now - live.get("db", 0) >= SEEN_EVERY:
                    live["db"] = now
                    await asyncio.to_thread(_store_load, nid, ld, now)
            elif m.get("t") == "closed":
                pass                                   # Knoten hat einen leeren Raum selbst geschlossen
    except Exception:  # noqa: BLE001 - Verbindung weg, Zeitueberschreitung, kein JSON
        pass
    finally:
        wt.cancel()
        pt.cancel()
        if _CONNS.get(nid) is k:
            _CONNS.pop(nid, None)
            _LIVE.pop(nid, None)
            await asyncio.to_thread(_gone, nid)
            print(f"horn: Knoten {hh['name']} getrennt", flush=True)
        await _close(ws, 1000)


async def _close(ws, code):
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001 - schon zu
        pass


@router.get("/internal/horn/conf")
def internal_conf(request: Request):
    """Fuer saga-media: soll der eingebaute Knoten laufen? (Feature-Flag calls.horn)"""
    if not MEDIA_KEY or not hmac.compare_digest(request.headers.get("x-saga-media-key", "").encode(), MEDIA_KEY.encode()):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"on": horn_on()}, headers={"Cache-Control": "no-store"})


def status():
    """Diagnose (Tests, spaeter Admin-Seite P8): Knoten mit Bereitschaft und Last."""
    with _db() as c:
        return [{"id": n["id"], "name": n["name"], "builtin": n["builtin"], "url": n["url"], "udp": n["udp"], "state": n["state"],
                 "ready": ready(n), "util": round(util(n), 3), "connected": n["id"] in _CONNS,
                 "load": (_LIVE.get(n["id"]) or {}).get("load") or n["load"]} for n in nodes(c)]


# --- CSP: Geraete sprechen die Knoten direkt an (Leg-Aufbau, spaeter Probe) ------------------------------------------
_CSP = {"t": 0.0, "v": ""}


def _csp_extra():
    now = time.time()
    if now - _CSP["t"] < 10:
        return _CSP["v"]
    v = ""
    try:
        if horn_on():
            with _db() as c:
                us = sorted({n["url"] for n in nodes(c) if n.get("url")})
            v = "".join(f" {u} {'wss' + u[5:]}" for u in us if u.startswith("https://"))
    except Exception:  # noqa: BLE001 - Datenbank kurz weg: ohne Zusatz
        v = _CSP["v"]
    _CSP.update(t=now, v=v)
    return v


class NodeCsp:
    """ASGI: ergaenzt connect-src der HTML-Seiten um die Adressen der Knoten (nur mit Feature-Flag; sonst unveraendert)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(("/api/", "/static/", "/fed/", "/internal/")):
            return await self.app(scope, receive, send)

        async def snd(m):
            if m["type"] == "http.response.start":
                hs = m.get("headers") or []
                for i, (k, v) in enumerate(hs):
                    if k.lower() == b"content-security-policy" and b"connect-src 'self'" in v:
                        extra = await asyncio.to_thread(_csp_extra) if time.time() - _CSP["t"] >= 10 else _CSP["v"]
                        if extra:
                            hs = list(hs)
                            hs[i] = (k, v.replace(b"connect-src 'self'", b"connect-src 'self'" + extra.encode(), 1))
                            m = dict(m, headers=hs)
                        break
            await send(m)
        return await self.app(scope, receive, snd)
