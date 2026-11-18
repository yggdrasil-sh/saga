"""Steuerkanal der Knoten (saga/horn.py ctl): Knoten bricht die Verbindung gleich nach seinem hello ab (vor/waehrend
welcome). Erwartung: kein Serverfehler (Traceback) im Log, der Knoten gilt danach als getrennt (keine haengende
Steuerverbindung im Register), eine neue Verbindung bekommt wieder ein welcome.
Wiederverbinden (pruef-klein-web2): der Abbau der alten Verbindung im finally darf eine neuere desselben Knotens nicht
abmelden - weder beim Ersatz einer offenen Verbindung noch bei Abbruch nach hello und sofortiger Neuverbindung. Beobachtet
ueber den Zustand im Register und solo im welcome eines zweiten Knotens (solo=False nur, wenn der erste noch verbunden ist).

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
NAME2 = "ctl-abbruch-c.example.org"
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


def hello(ws, sk, name=NAME):
    ch = json.loads(ws.recv(5))
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    ws.send(json.dumps({"t": "hello", "proto": "horn-ctl/1", "name": name, "url": f"https://{name}", "pub": pub,
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


def node(name):
    sk = Ed25519PrivateKey.generate()
    nid = secrets.token_hex(8)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, state, created, port) VALUES (?,?,?,0,?,'neu',?,443)",
                  (nid, name, pub, f"https://{name}", time.time()))
    return sk, nid


def still_connected(sk2):
    """Zweiter Knoten C verbindet kurz: solo im welcome ist False, solange der erste Knoten im Register verbunden ist."""
    ws = ws_open()
    try:
        hello(ws, sk2, NAME2)
        w = json.loads(ws.recv(5))
        return w.get("t") == "welcome" and w.get("solo") is False
    finally:
        ws.close()
        time.sleep(0.3)


def wieder(sk, nid, sk2):
    """Wiederverbinden desselben Knotens: der alte Abbau darf die neue Verbindung nicht abmelden."""
    a = ws_open()
    hello(a, sk)
    ok("A: welcome", json.loads(a.recv(5)).get("t") == "welcome")
    b = ws_open()                                      # Ersatz, waehrend A noch offen ist (Knoten verbindet neu)
    hello(b, sk)
    ok("B (Ersatz bei offener A): welcome", json.loads(b.recv(5)).get("t") == "welcome")
    abort(a)                                           # alte Verbindung bricht hart ab -> ihr finally laeuft
    time.sleep(1.0)
    ok(f"... nach Abbruch von A: Knoten ok ({state(nid)}), B weiter verbunden", state(nid) == "ok" and still_connected(sk2))
    b.close()
    time.sleep(0.5)
    ok(f"... B sauber zu: getrennt ({state(nid)})", state(nid) == "getrennt" and not still_connected(sk2))
    bad, diag = [], []
    for i in range(RUNDEN):                            # Abbruch nach hello, sofort neu verbinden (Prozess-Neustart)
        a = ws_open()
        hello(a, sk)
        if i % 3:
            time.sleep(0.003 * i)
        abort(a)
        b = ws_open()
        hello(b, sk)
        w = json.loads(b.recv(5))
        time.sleep(0.8)
        st, conn = state(nid), still_connected(sk2)
        if w.get("t") != "welcome" or not conn:
            bad.append((i, w.get("t"), st, conn))
        elif st != "ok":
            diag.append(i)
        b.close()
        time.sleep(0.3)
    ok(f"{RUNDEN}x Abbruch nach hello + sofort neu: neue Verbindung bleibt verbunden {bad}", not bad)
    # Rennen _gone des alten finally <-> register_paired der neuen Verbindung (Bug 2126): seit der Generation je Knoten
    # schreibt der alte Abbau nicht mehr 'getrennt' ueber die neue Anmeldung (gezielt erzwungen: tests/horn_ctl_rennen.py)
    ok(f"... Zustand dabei immer ok (Runden mit 'getrennt': {diag})", not diag)


def main():
    sk, nid = node(NAME)
    sk2, _ = node(NAME2)
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
        wieder(sk, nid, sk2)
        tb = tracebacks() - tb0
        ok(f"Instanz: Serverfehler {tb}", tb == 0)
    finally:
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE name IN (?,?)", (NAME, NAME2))
    print(f"\nHORN-CTL-ABBRUCH {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
