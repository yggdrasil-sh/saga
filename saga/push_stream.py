"""Eigene Push-Verbindung der Apps (2.5): Rueckfall ohne Verteiler-App, ohne Google FCM.

Hat ein Android-Geraet keinen UnifiedPush-Verteiler (ntfy, NextPush ...), haelt die App selbst EINE WebSocket-Verbindung
zu GET /api/push/stream. Fuer saga.push ist das nur ein weiteres Ziel (Abo-Art "stream", genau eines je
Geraete-Anmeldung): send() verschluesselt wie fuer Web Push (RFC 8291, aes128gcm, Schluessel der App) und legt das
Ergebnis hier ab - wie bei einem Push-Dienst. Klartext gibt es auf diesem Weg nie, eigene Logik auch nicht.

Ablage (Tabelle push_stream_msg): verschluesselter Koerper, Ablauf nach TTL (wie Web Push; Anrufe: so lange es klingelt),
Topic ersetzt eine noch nicht bestaetigte gleiche Mitteilung, hoechstens KEEP je Abo. Ist die App verbunden, geht die
Mitteilung sofort hinaus; sonst beim naechsten Verbindungsaufbau. Die App bestaetigt jede Mitteilung (dann geloescht).

Rahmen (binaer, ein Byte Art):
  Server -> App   0x01 + Kennung (8 Byte, big endian) + aes128gcm-Koerper     Mitteilung
                  0x04                                                         Antwort auf 0x03
  App -> Server   0x02 + Kennung (8 Byte)                                      erhalten (loeschen)
                  0x03                                                         Lebenszeichen (Ping)
Daneben beantwortet der Webserver echte WebSocket-Pings (Protokoll). Der Server pingt NIE selbst (run.py:
ws_ping_interval=None) und hat keine Zeitschleife: Die App waehlt den Abstand ihrer Pings (so gross, wie NAT und Proxy
es zulassen) - jeder Ping weckt das Handy, deshalb so wenige wie moeglich. Je Verbindung nur ein kleines Objekt;
keine Aufgaben, keine Warteschlangen im Speicher.

Anmeldung: 'Authorization: Bearer hkd_...' (Geraete-Schluessel der App). Schliess-Codes (nach dem Aufbau):
  4401 Anmeldung ungueltig (Geraet abgemeldet, Konto gesperrt)   -> nicht neu verbinden, App meldet sich neu an
  4404 kein Abo "stream" fuer dieses Geraet / Weg abgeschaltet   -> Abo neu anlegen (POST /api/push/subscriptions)
  4409 eine neuere Verbindung desselben Geraets hat uebernommen   -> nicht sofort zurueckdraengen
  4429 zu viele Verbindungsaufbauten                              -> spaeter
  1012 Server startet neu                                         -> mit kurzer, zufaelliger Wartezeit neu verbinden
Laeuft im Saga-Prozess (asyncio, uvicorn). Mit saga-media davor wird die Verbindung nur durchgereicht."""
import asyncio
import os
import struct
import time


import auth
from db import connect as _db
from saga import core

PATH = "/api/push/stream"
KEEP = 100                            # unbestaetigte Mitteilungen je Abo (darueber: die aeltesten weg)
CHECK = 600                           # Geraet/Konto hoechstens alle 10 min neu pruefen (beim Ping der App)
CONN_MIN = 12                         # Verbindungsaufbauten je Geraet und Minute
MAX_TOTAL = max(10, int(os.environ.get("PUSH_STREAM_MAX", "20000")))
F_MSG, F_ACK, F_PING, F_PONG = 1, 2, 3, 4
PONG = bytes([F_PONG])
C_AUTH, C_NOSUB, C_REPLACED, C_RATE, C_RESTART = 4401, 4404, 4409, 4429, 1012
FRAME_MAX = 16384                       # groesster Rahmen der App (Lebenszeichen, Bestaetigungen)

_S = {"loop": None}
_conns = {}                           # sub_id -> _Conn  (nur in der Event-Schleife veraendert)
_hits = {}                            # device_id -> [Zeitpunkte]  (Bremse beim Aufbau)
stats = {"delivered": 0, "acked": 0, "pings": 0, "replayed": 0}
_tasks = set()                        # laufende Hilfsaufgaben (asyncio haelt nur schwache Verweise)


def _spawn(coro):
    tk = asyncio.get_running_loop().create_task(coro)
    _tasks.add(tk)
    tk.add_done_callback(_tasks.discard)
    return tk


class _Conn:
    __slots__ = ("ws", "sid", "uid", "did", "checked", "lock", "closed")

    def __init__(self, ws, sid, uid, did):
        self.ws, self.sid, self.uid, self.did = ws, sid, uid, did
        self.checked, self.lock, self.closed = time.monotonic(), None, False

    async def send(self, data):
        if self.closed:
            return False
        if self.lock is None:
            self.lock = asyncio.Lock()
        try:
            async with self.lock:
                await self.ws.send_bytes(data)
            return True
        except Exception:  # noqa: BLE001 - Verbindung weg: der Empfangsteil raeumt auf
            self.closed = True
            return False

    async def close(self, code):
        if self.closed:
            return
        self.closed = True
        try:
            await self.ws.close(code)
        except Exception:  # noqa: BLE001
            pass


def start():
    _S["loop"] = asyncio.get_running_loop()
    core.on_forget_device(_forget)


def stop():
    """Beim Beenden: alle Verbindungen mit 1012 schliessen (die App verbindet mit kurzer, zufaelliger Wartezeit neu)."""
    loop = _S["loop"]
    _S["loop"] = None
    if loop is None:
        return
    for cl in list(_conns.values()):
        _tasks.add(loop.create_task(cl.close(C_RESTART)))


def connected(sid):
    return sid in _conns


def count():
    return len(_conns)


# =====================================================================================================
# Ablage (aus dem Zustell-Thread von saga.push)
# =====================================================================================================
def deposit(sid, body, ttl, topic=""):
    """Verschluesselte Mitteilung fuer das Abo sid ablegen und - falls verbunden - sofort zustellen -> Kennung."""
    now = time.time()
    topic = (topic or "")[:32]
    with _db() as c, c:
        c.execute("DELETE FROM push_stream_msg WHERE sub_id=? AND (expires < ? OR (topic <> '' AND topic = ?))", (sid, now, topic))
        mid = c.execute("INSERT INTO push_stream_msg (sub_id, topic, body, created, expires) VALUES (?,?,?,?,?)",
                        (sid, topic, bytes(body), now, now + max(1, int(ttl)))).lastrowid
        if c.execute("SELECT COUNT(*) FROM push_stream_msg WHERE sub_id=?", (sid,)).fetchone()[0] > KEEP:
            c.execute("DELETE FROM push_stream_msg WHERE sub_id=? AND id NOT IN "
                      "(SELECT id FROM push_stream_msg WHERE sub_id=? ORDER BY id DESC LIMIT ?)", (sid, sid, KEEP))
    loop = _S["loop"]
    if loop is not None:
        try:
            loop.call_soon_threadsafe(_kick, sid, mid, bytes(body))
        except RuntimeError:              # Schleife beendet
            pass
    return mid


def _frame(mid, body):
    return bytes([F_MSG]) + struct.pack("!Q", mid) + body


def _kick(sid, mid, body):
    cl = _conns.get(sid)
    if cl is not None:
        _spawn(_push_one(cl, mid, body))


async def _push_one(cl, mid, body):
    if await cl.send(_frame(mid, body)):
        stats["delivered"] += 1


def cleanup():
    """Stuendlich (saga.push.cleanup): abgelaufene Mitteilungen weg."""
    with _db() as c, c:
        return c.execute("DELETE FROM push_stream_msg WHERE expires < ?", (time.time(),)).rowcount


# =====================================================================================================
# Verbindung
# =====================================================================================================
def _who(hdr):
    """Authorization-Kopf -> (uid, device_id) oder (None, None)."""
    if not hdr.startswith("Bearer hkd_") or len(hdr) > 130:
        return None, None
    with _db() as c:
        r = c.execute("SELECT uid, id FROM device_login WHERE hash=?", (core._tok_hash(hdr[7:].strip()),)).fetchone()
    return (r[0], r[1]) if r and auth.user_info(r[0]) is not None else (None, None)


def _sub_of(uid, did):
    with _db() as c:
        r = c.execute("SELECT id FROM push_sub WHERE uid=? AND device_id=? AND kind='stream'", (uid, did)).fetchone()
    return r[0] if r else None


def _still_ok(uid, did, sid):
    """Beim Ping (hoechstens alle CHECK s): Geraet, Konto und Abo gibt es noch?"""
    if auth.user_info(uid) is None:
        return C_AUTH
    with _db() as c:
        if not c.execute("SELECT 1 FROM device_login WHERE id=? AND uid=?", (did, uid)).fetchone():
            return C_AUTH
        if not c.execute("SELECT 1 FROM push_sub WHERE id=? AND uid=?", (sid, uid)).fetchone():
            return C_NOSUB
    return 0


def _pending(sid):
    with _db() as c:
        return c.execute("SELECT id, body FROM push_stream_msg WHERE sub_id=? AND expires >= ? ORDER BY id",
                         (sid, time.time())).fetchall()


def _ack(sid, mids):
    now = time.time()
    with _db() as c, c:
        n = 0
        for m in mids:
            n += c.execute("DELETE FROM push_stream_msg WHERE id=? AND sub_id=?", (m, sid)).rowcount
        if n:
            c.execute("UPDATE push_sub SET last_ok=?, last_err=NULL, fails=0 WHERE id=?", (now, sid))
    return n


def _rate_ok(did):
    now = time.monotonic()
    lst = [x for x in _hits.get(did, ()) if now - x < 60]
    if len(lst) >= CONN_MIN:
        _hits[did] = lst
        return False
    lst.append(now)
    _hits[did] = lst
    if len(_hits) > 5000:
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60][:2500]:
            _hits.pop(k, None)
    return True


def _forget(uid, did=None):
    """core.forget_device (Abmelden eines Geraets, "ueberall abmelden"): Verbindungen sofort schliessen. Aus jedem Thread."""
    loop = _S["loop"]
    if loop is None:
        return

    async def check(cl):
        await asyncio.sleep(1.5)          # Abmelden laeuft in einer Transaktion: erst nach dem Speichern pruefen
        try:
            bad = await asyncio.to_thread(_still_ok, cl.uid, cl.did, cl.sid)
        except Exception:  # noqa: BLE001
            bad = 0
        if bad:
            await cl.close(bad)

    def go():                             # "ueberall abmelden" kann das eigene Geraet behalten: je Verbindung pruefen
        for cl in [x for x in _conns.values() if x.uid == uid and (did is None or str(x.did) == str(did))]:
            _spawn(check(cl))
    try:
        loop.call_soon_threadsafe(go)
    except RuntimeError:
        pass


def drop_sub(sid):
    """Abo entfernt (Abmelden, Geraeteliste, "gone"): Verbindung mit 4404 schliessen. Aus jedem Thread."""
    loop = _S["loop"]
    if loop is None:
        return

    def go():
        cl = _conns.get(sid)
        if cl is not None:
            _spawn(cl.close(C_NOSUB))
    try:
        loop.call_soon_threadsafe(go)
    except RuntimeError:
        pass


class Front:
    """Reines ASGI ganz aussen (app.py: zuletzt registrierte Middleware): /api/push/stream geht an _serve, OHNE
    FastAPI-Routing und die uebrigen Middlewares (Sitzung, Sprache, Groessengrenze ...). Das spart je ruhender Verbindung
    gut zwei Drittel des Speichers (gemessen mit tracemalloc: Ausnahme-Rahmen, AsyncExitStacks, Kopien des scope).
    Alles andere geht unveraendert an die App."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and scope.get("path") == PATH:
            return await _serve(scope, receive, send)
        return await self.app(scope, receive, send)


class _Sock:
    """Duenne Huelle um ASGI send/receive (statt starlette.WebSocket)."""
    __slots__ = ("_send", "_recv")

    def __init__(self, send, receive):
        self._send, self._recv = send, receive

    async def send_bytes(self, data):
        await self._send({"type": "websocket.send", "bytes": data})

    async def close(self, code):
        await self._send({"type": "websocket.close", "code": code})

    async def receive(self):
        return await self._recv()


async def _serve(scope, receive, send):
    if (await receive())["type"] != "websocket.connect":
        return
    hdr = next((v.decode("latin-1") for k, v in scope.get("headers") or () if k == b"authorization"), "")
    ws = _Sock(send, receive)
    try:
        uid, did = await asyncio.to_thread(_who, hdr)
        sid = await asyncio.to_thread(_sub_of, uid, did) if uid else None
    except Exception:  # noqa: BLE001 - Datenbank kurz weg: wie Neustart behandeln (App versucht es spaeter)
        await send({"type": "websocket.accept"})
        await ws.close(C_RESTART)
        return
    await send({"type": "websocket.accept"})
    from saga import push                     # hier: push importiert dieses Modul
    if not uid:
        await ws.close(C_AUTH)
        return
    if not _rate_ok(did) or (len(_conns) >= MAX_TOTAL and sid not in _conns):
        await ws.close(C_RATE)
        return
    if not push.server().get("stream") or not sid:
        await ws.close(C_NOSUB)
        return
    cl = _Conn(ws, sid, uid, did)
    old = _conns.get(sid)
    _conns[sid] = cl
    if old is not None:
        await old.close(C_REPLACED)
    try:
        # nachholen, was waehrend der Trennung abgelegt wurde (Kennungen aufsteigend; die App erkennt Doppelte)
        for mid, body in await asyncio.to_thread(_pending, sid):
            if not await cl.send(_frame(mid, bytes(body))):
                return
            stats["replayed"] += 1
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                return
            b = m.get("bytes")
            if not b:
                continue                      # Text: unbekannt, still uebergehen
            if len(b) > FRAME_MAX:            # wie bisher die Grenze von uvicorn (run.py erlaubt seit 2.5 P3 mehr, fuer /api/collab)
                await ws.close(1009)
                return
            if b[0] == F_PING:
                stats["pings"] += 1
                if time.monotonic() - cl.checked >= CHECK:
                    cl.checked = time.monotonic()
                    try:
                        bad = await asyncio.to_thread(_still_ok, uid, did, sid)
                    except Exception:  # noqa: BLE001 - Datenbank kurz weg: spaeter erneut pruefen
                        bad = 0
                    if bad:
                        await cl.close(bad)
                        return
                await cl.send(PONG)
            elif b[0] == F_ACK and len(b) >= 9 and (len(b) - 1) % 8 == 0:
                mids = [struct.unpack_from("!Q", b, 1 + 8 * i)[0] for i in range((len(b) - 1) // 8)][:KEEP]
                try:
                    stats["acked"] += await asyncio.to_thread(_ack, sid, mids)
                except Exception:  # noqa: BLE001 - bleibt liegen, kommt beim naechsten Aufbau noch einmal
                    pass
    except Exception:  # noqa: BLE001 - Verbindung abgebrochen
        return
    finally:
        cl.closed = True
        if _conns.get(sid) is cl:
            _conns.pop(sid, None)
