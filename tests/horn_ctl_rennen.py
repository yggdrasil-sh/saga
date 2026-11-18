"""Steuerkanal der Knoten (saga/horn.py ctl): Rennen zwischen dem Abbau einer alten Steuerverbindung (finally -> _gone)
und der Anmeldung einer neuen desselben Knotens (register_paired in einem Thread, _CONNS noch nicht umgestellt)
(pruef-klein-web2 Fund 1, Bug 2126). Vorher schrieb der alte Abbau 'getrennt', obwohl die neue Verbindung schon
angemeldet war - bis zur naechsten Lastmeldung in der DB (SEEN_EVERY).

Erzwungen ohne Zeitglueck: ctl laeuft im Prozess mit einer WebSocket-Attrappe; register_paired der neuen Verbindung B
wartet nach seinem DB-Eintrag, bis die alte Verbindung A abgebaut ist (hoechstens WARTE_S). Ohne Generation laeuft der
Abbau von A in dieses Fenster und schreibt 'getrennt' (Gegenprobe rot); mit Generation bleibt 'ok'.
Ausserdem direkt: _gone mit veralteter Generation schreibt nicht, mit aktueller schon.

Braucht nur die Datenbank (DATABASE_URL), keinen laufenden Server. Knoten-Attrappe im Register (ctl-rennen.example.org),
am Ende entfernt."""
import asyncio
import base64
import os
import secrets
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

import db  # noqa: E402
from saga import horn  # noqa: E402
from version import VERSION  # noqa: E402

NAME = "ctl-rennen.example.org"
WARTE_S = 2.0
res = []


def ok(msg, cond):
    res.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + msg, flush=True)


class Gone(Exception):
    pass


class FakeWS:
    """Genug von starlette.WebSocket fuer ctl: headers, accept, send_json, receive_json, close."""

    def __init__(self, sk, name=NAME):
        self.sk, self.name = sk, name
        self.headers = {}
        self.inbox = asyncio.Queue()
        self.sent = []
        self.welcome = asyncio.Event()
        self.closed = None
        self.n = 0

    async def accept(self):
        pass

    async def send_json(self, m):
        self.sent.append(m)
        if m.get("t") == "challenge":
            pub = base64.b64encode(self.sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
            sig = base64.b64encode(self.sk.sign(f"horn-ctl/1 hello\n{m['nonce']}".encode())).decode()
            self.inbox.put_nowait({"t": "hello", "proto": "horn-ctl/1", "name": self.name, "url": f"https://{self.name}",
                                   "pub": pub, "sig": sig, "version": VERSION, "udp": False, "max_legs": 48,
                                   "cert": {"mode": "dns01", "state": "self"}})
        elif m.get("t") == "welcome":
            self.welcome.set()

    async def receive_json(self):
        m = await self.inbox.get()
        if isinstance(m, Exception):
            raise m
        return m

    def abort(self):
        self.inbox.put_nowait(Gone("Abbruch"))

    async def close(self, code=1000):
        self.closed = code


def state(nid):
    with db.connect() as c:
        r = c.execute("SELECT state FROM horn_node WHERE id=?", (nid,)).fetchone()
    return r[0] if r else None


def node():
    sk = Ed25519PrivateKey.generate()
    nid = secrets.token_hex(8)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    with db.connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (NAME,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, state, created, port) VALUES (?,?,?,0,?,'neu',?,443)",
                  (nid, NAME, pub, f"https://{NAME}", time.time()))
    return sk, nid


async def rennen(sk, nid):
    real = horn.register_paired
    b_in = threading.Event()        # B hat seinen DB-Eintrag (state ok) geschrieben
    a_weg = threading.Event()       # Abbau von A (finally) ist durch
    waited = {}

    def slow_register(h, now=None):
        r = real(h, now)
        if threading.current_thread() is not threading.main_thread() and getattr(slow_register, "armed", False):
            b_in.set()
            t0 = time.time()
            a_weg.wait(WARTE_S)     # Fenster: register fertig, _CONNS noch alt
            waited["s"] = time.time() - t0
        return r

    horn.register_paired = slow_register
    try:
        a = FakeWS(sk)
        ta = asyncio.create_task(horn.ctl(a))
        await asyncio.wait_for(a.welcome.wait(), 10)
        ok(f"A: welcome, Zustand {state(nid)}", state(nid) == "ok" and horn._CONNS.get(nid) is not None)
        slow_register.armed = True
        b = FakeWS(sk)
        tb = asyncio.create_task(horn.ctl(b))
        await asyncio.to_thread(b_in.wait, 10)
        ok("B: register_paired durch (Fenster offen)", b_in.is_set())
        a.abort()                   # alte Verbindung bricht ab, waehrend B im Fenster steht
        await asyncio.wait_for(ta, 10)
        a_weg.set()
        await asyncio.wait_for(b.welcome.wait(), 10)
        st = state(nid)
        ok(f"Abbau von A waehrend der Anmeldung von B: Knoten bleibt ok ({st}, B wartete {waited.get('s', 0):.1f} s)", st == "ok")
        ok("... B ist die Steuerverbindung im Register", horn._CONNS.get(nid) is not None and horn._CONNS[nid].ws is b)
        b.abort()
        await asyncio.wait_for(tb, 10)
        ok(f"B bricht ab: jetzt getrennt ({state(nid)}), Register leer", state(nid) == "getrennt" and nid not in horn._CONNS)
    finally:
        horn.register_paired = real


def direkt(sk, nid):
    """_claim/_gone ohne ctl: veraltete Generation schreibt nicht."""
    if not hasattr(horn, "_claim"):
        ok("_claim/_gone mit Generation vorhanden", False)
        return
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    hh = {"name": NAME, "url": f"https://{NAME}", "pub": pub, "udp": False, "version": VERSION, "v4": "", "v6": "",
          "cert": {}, "upd": {}}
    (n1, _), g1 = horn._claim(horn.register_paired, hh)
    (n2, _), g2 = horn._claim(horn.register_paired, hh)
    ok(f"zwei Anmeldungen: zwei Generationen ({g1}, {g2})", n1 == n2 == nid and g2 == g1 + 1)
    r1 = horn._gone(nid, g1)
    ok(f"_gone mit alter Generation: nichts geschrieben ({r1}, {state(nid)})", r1 is False and state(nid) == "ok")
    r2 = horn._gone(nid, g2)
    ok(f"_gone mit aktueller Generation: getrennt ({r2}, {state(nid)})", r2 is True and state(nid) == "getrennt")
    bad = dict(hh, name="anders.example.org")
    r, g = horn._claim(horn.register_paired, bad)
    ok(f"abgelehnte Anmeldung (4409) zieht keine Generation ({r}, {g}, aktuell {horn._GEN.get(nid)})",
       r == (None, 4409) and g is None and horn._GEN.get(nid) == g2)


def main():
    db.wait_ready()
    db.init_schema()
    sk, nid = node()
    try:
        asyncio.run(rennen(sk, nid))
        direkt(sk, nid)
    finally:
        with db.connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE name=?", (NAME,))
    print(f"\nHORN-CTL-RENNEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
