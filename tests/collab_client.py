"""Testhilfe: minimaler Client fuer WS /api/collab (docs/APP-API-COLLAB.md) mit pycrdt - nur fuer tests/collab*.py.
Absichtlich ohne Server-Code (nur das Dokumentmodell saga.collab_model fuer Lesen/Schreiben), damit die Tests das
dokumentierte Protokoll pruefen."""
import json, queue, struct, threading, time
from websockets.sync.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed
from pycrdt import Doc

T = dict(HELLO=1, SUB=2, UNSUB=3, SYNC1=4, SYNC2=5, UPDATE=6, AW=7, REJECT=8, BYE=9, ERROR=10, PING=11, PONG=12, ACK=13)
N = {v: k for k, v in T.items()}


def vu(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def rvu(b, i):
    n = s = 0
    while True:
        x = b[i]; i += 1
        n |= (x & 0x7F) << s; s += 7
        if not x & 0x80:
            return n, i


def enc(kind, nid, *parts):
    out = bytes([T[kind]]) + struct.pack(">q", nid)
    for p in parts:
        if not isinstance(p, bytes):
            p = json.dumps(p, separators=(",", ":")).encode()
        out += vu(len(p)) + p
    return out


def dec(b):
    kind, nid, parts, i = b[0], struct.unpack(">q", b[1:9])[0], [], 9
    while i < len(b):
        n, i = rvu(b, i)
        parts.append(b[i:i + n]); i += n
    return N.get(kind, kind), nid, parts


class Client:
    """Eine Verbindung, ein Doc je Seite. Empfang in einem Thread; Nachrichten in self.inbox (alle) - Updates werden
    sofort angewendet (ausser hold=True: dann gesammelt in self.held, fuer Verzoegerung/Umordnung)."""

    def __init__(self, ws_url, headers=None, origin=None):
        self.url, self.headers = ws_url, dict(headers or {})
        self.origin = origin
        self.docs, self.inbox, self.held, self.hold = {}, queue.Queue(), [], False
        self.closed_code = None
        self.lock = threading.Lock()
        self.connect()

    def connect(self):
        kw = {"additional_headers": self.headers, "open_timeout": 10, "max_size": 1 << 21}
        if self.origin is not None:
            kw["origin"] = self.origin
        self.ws = ws_connect(self.url, **kw)
        self.closed_code = None
        self.t = threading.Thread(target=self._rx, daemon=True)
        self.t.start()

    def _rx(self):
        try:
            for m in self.ws:
                if isinstance(m, str):
                    continue
                k, nid, parts = dec(m)
                if k in ("SYNC2", "UPDATE") and nid in self.docs and parts:
                    if self.hold and k == "UPDATE":
                        with self.lock:
                            self.held.append(parts[0])
                    else:
                        with self.lock:
                            self.docs[nid].apply_update(parts[0])
                if k == "REJECT" and nid in self.docs and len(parts) > 1:
                    with self.lock:                      # Neuaufbau aus dem Vollzustand des Servers
                        d = Doc(); d.apply_update(parts[1]); self.docs[nid] = d
                if k == "HELLO" and nid in self.docs and parts:
                    info = json.loads(parts[0])
                    self.gen = info.get("gen")
                self.inbox.put((k, nid, parts))
        except ConnectionClosed as ex:
            self.closed_code = ex.rcvd.code if ex.rcvd else 1006
        except Exception:  # noqa: BLE001
            self.closed_code = self.closed_code or 1006
        self.inbox.put(("CLOSED", 0, [self.closed_code]))

    def send(self, kind, nid, *parts):
        self.ws.send(enc(kind, nid, *parts))

    def sub(self, nid, schema=None, caps=None, keep=False):
        from saga import collab_model as M
        if not keep or nid not in self.docs:
            self.docs[nid] = Doc()
        opts = {"schema": schema if schema is not None else {"model": M.MODEL, "ver": M.SCHEMA_VER, "hash": M.SCHEMA_HASH}}
        if caps:
            opts["caps"] = caps
        if keep and getattr(self, "gen", None):
            opts["gen"] = self.gen
            self.send("SUB", nid, opts, self.docs[nid].get_state())
        else:
            self.send("SUB", nid, opts)

    def wait(self, kinds, nid=None, timeout=8.0, pred=None):
        """Naechste Nachricht der Arten kinds (andere werden verworfen) -> (art, nid, teile) oder None."""
        end = time.time() + timeout
        kinds = (kinds,) if isinstance(kinds, str) else kinds
        while time.time() < end:
            try:
                m = self.inbox.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                return None
            if m[0] in kinds and (nid is None or m[1] == nid) and (pred is None or pred(m)):
                return m
            if m[0] == "CLOSED" and "CLOSED" not in kinds:
                return m
        return None

    def edit(self, nid, fn, ref=None):
        """fn(doc) aendert das Doc; das Update geht an den Server. -> Update-Bytes"""
        with self.lock:
            d = self.docs[nid]
            sv = d.get_state()
            fn(d)
            u = d.get_update(sv)
        if len(u) > 2:
            if ref:
                self.send("UPDATE", nid, u, ref.encode())
            else:
                self.send("UPDATE", nid, u)
        return u

    def release(self, nid, order=None, dup=False):
        """Gesammelte Updates anwenden (order: Indizes, z. B. umgekehrt; dup: jedes doppelt)."""
        with self.lock:
            lst = self.held[:]
            self.held.clear()
            idx = order if order is not None else range(len(lst))
            for i in idx:
                self.docs[nid].apply_update(lst[i])
                if dup:
                    self.docs[nid].apply_update(lst[i])

    def blocks(self, nid):
        from saga import collab_model as M
        with self.lock:
            d = Doc(); d.apply_update(self.docs[nid].get_update())
        return M.read(d)

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass
