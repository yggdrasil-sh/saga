"""Steuerkanal der Knoten (saga/horn.py ctl): Knoten bricht die Verbindung gleich nach seinem hello ab (vor/waehrend
welcome). Erwartung: kein Serverfehler (Traceback) im Log, der Knoten gilt danach als getrennt (keine haengende
Steuerverbindung im Register), eine neue Verbindung bekommt wieder ein welcome.

Laeuft gegen den Testserver (HK_TEST_BASE, Log unter $STATE_DIR/server.log). Knoten-Attrappe ohne Binaer: Eintrag direkt
im Register (Name ctl-abbruch.example.org), am Ende entfernt."""
import base64
import json
import os
import secrets
import socket
import sys
import time
import urllib.parse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from db import connect
from version import VERSION

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
LOG = os.path.join(os.environ.get("STATE_DIR", ""), "server.log")
NAME = "ctl-abbruch.example.org"
RUNDEN = 12
res = []


def ok(msg, cond):
    res.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + msg, flush=True)


def tracebacks():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().count("Traceback")
    except OSError:
        return -1


def ws_open():
    from websockets.sync.client import connect as wsc
    return wsc(f"ws://{B_.hostname}:{B_.port}/api/horn/ctl", additional_headers={"X-Saga": "1"}, open_timeout=5, proxy=None)


def hello(ws, sk):
    ch = json.loads(ws.recv(5))
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    ws.send(json.dumps({"t": "hello", "proto": "horn-ctl/1", "name": NAME, "url": f"https://{NAME}", "pub": pub,
                        "sig": base64.b64encode(sk.sign(f"horn-ctl/1 hello\n{ch['nonce']}".encode())).decode(),
                        "version": VERSION, "udp": False, "max_legs": 48, "cert": {"mode": "dns01", "state": "self"}}))


def abort(ws):
    """Harter Abbruch ohne Schliessrahmen (TCP-RST): wie ein Knoten, dessen Prozess oder Netz wegfaellt."""
    s = ws.socket
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
    except OSError:
        pass
    s.close()


def state(nid):
    with connect() as c:
        r = c.execute("SELECT state FROM horn_node WHERE id=?", (nid,)).fetchone()
    return r[0] if r else None


def main():
    sk = Ed25519PrivateKey.generate()
    nid = secrets.token_hex(8)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (NAME,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, state, created, port) VALUES (?,?,?,0,?,'neu',?,443)",
                  (nid, NAME, pub, f"https://{NAME}", time.time()))
    tb0 = tracebacks()
    ok(f"Server-Log lesbar ({LOG})", tb0 >= 0)
    try:
        for i in range(RUNDEN):
            ws = ws_open()
            hello(ws, sk)
            if i % 2 and i < RUNDEN - 1:            # letzte Runde sofort
                time.sleep(0.002 * i)                  # sonst mal sofort, mal waehrend register_paired/welcome
            abort(ws)
        time.sleep(1.5)
        tb = tracebacks() - tb0
        ok(f"{RUNDEN} Abbrueche nach hello: keine Serverfehler ({tb})", tb == 0)
        if tb:
            print(open(LOG, encoding="utf-8", errors="replace").read()[-4000:])
        st = state(nid)
        ok(f"... Knoten danach getrennt, keine haengende Steuerverbindung ({st})", st == "getrennt")
        ws = ws_open()
        hello(ws, sk)
        w = json.loads(ws.recv(5))
        ok(f"neue Verbindung bekommt welcome ({w.get('t')}, Zustand {state(nid)})", w.get("t") == "welcome" and w.get("node_id") == nid
           and state(nid) == "ok")
        ws.close()
        time.sleep(0.5)
        ok(f"... nach sauberem Schliessen getrennt ({state(nid)})", state(nid) == "getrennt")
        tb = tracebacks() - tb0
        ok(f"Instanz: Serverfehler {tb}", tb == 0)
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE name=?", (NAME,))
    print(f"\nHORN-CTL-ABBRUCH {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
