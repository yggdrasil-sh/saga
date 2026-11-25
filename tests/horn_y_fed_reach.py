"""2.5 Grundsatz Y (Pruefer): foederiertes 1:1 ueber Knoten, wenn WIR fuehren (saga/fed_calls.py dm_horn_start) - Logiktest
ohne laufende Instanz (Datenbank, Gegenstelle und Knoten nachgebildet).

Foederierte Anrufe nutzen nur Knoten ohne Tunnel-Betrieb (fed_horn.shared_nodes, horn.ready_nodes ohne ws). Hat dieser
Server nur einen eingebauten Knoten im Tunnel-Betrieb (Ein Port hinter einem TLS-beendenden Proxy), ist das kein Ausfall:
Rueckfall direkt mit horn_block "reach" (ohne Hinweis) statt "node" ("Anruf-Knoten nicht erreichbar") - und der Server
der Gegenstelle wird nicht angeklingelt (kein Auftrag dmhorn). Ohne bereiten Knoten: "node". Mit einem Knoten ohne
Tunnel: der Auftrag dmhorn geht raus (hier mit 409 off beantwortet -> Rueckfall off). Bugs-DB 2357: mit eingebautem
Knoten im Tunnel-Betrieb UND geteiltem Knoten ohne Tunnel liegt der Anruf auf dem geteilten - auch fuer Geraete mit "ws".

Aufruf: PYTHONPATH=tests/pgshim:. python tests/horn_y_fed_reach.py   (Umgebung env.sh, die Datenbank wird nicht benutzt)"""
import contextlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from saga import fed_calls as X, horn  # noqa: E402

RES = []


def ok(name, cond):
    RES.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name, flush=True)


class _C:
    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextlib.contextmanager
def _db():
    yield _C()


SENT = []


def setup(nodes):
    """nodes: [(id, tunnel)] - alle bereit."""
    horn._LIVE.clear()
    for nid, tun in nodes:
        horn._LIVE[nid] = {"at": time.time(), "tunnel": tun, "load": {}}
    X._db = _db
    X.chat._member = lambda c, chid, uid: {"state": "ok"}
    X.chat.members = lambda c, chid: ["anna", "bert@b.example"]
    X.sharing.is_remote = lambda u: "@" in u
    X.FC._ro_error = lambda c, ch: None
    X.FC.handle_of = lambda u: u
    X.FC.split_handle = lambda h: tuple(h.split("@", 1))
    X.F.peer_by = lambda c, k, v: {"id": 7, "host": v}
    X.peer_horn_dm = lambda p: True
    X.FH.fed_ok = lambda c, ch, *a: (True, None)
    X.calls._busy = lambda c, uid: False
    X.calls._rate = lambda *a, **k: True
    X.calls._dev_name = lambda c, dev: "Testgeraet"
    X.FC.my_handle = lambda c, uid: "anna@a.example"
    X.FC.label_of = lambda c, u: {"name": "Bert"}
    horn.horn_on = lambda cfg=None: True             # 2263-4: horn_on(cfg) - calls.horn_on reicht die Einstellungen durch
    horn.nodes = lambda c: [{"id": nid, "builtin": 1, "name": nid, "share": 1} for nid, _ in nodes]
    horn.ready = lambda n, now=None: True
    SENT.clear()

    def sync_call(p, body, path=None):
        SENT.append(body.get("op"))
        return 409, {"horn_block": "off"}
    X.FC.sync_call = sync_call


def start(caps):
    r = X.dm_horn_start("anna", {"id": 1, "ref": "r1"}, "c" * 32, "audio", "d" * 32, json.dumps(caps))
    return r.status_code, json.loads(bytes(r.body).decode())


def main():
    for caps, what in ((["horn", "multi"], "App ohne ws"), (["horn", "multi", "ws"], "App mit ws"),
                       (["horn", "multi", "frames"], "Browser mit Rahmen-Weg")):
        setup([("horn-1", True)])
        s, d = start(caps)
        ok(f"nur Tunnel-Knoten, {what}: 409 fallback direct, horn_block reach ({s}, {d.get('horn_block')})",
           s == 409 and d.get("fallback") == "direct" and d.get("horn_block") == "reach")
        ok(f"nur Tunnel-Knoten, {what}: Gegenstelle nicht angeklingelt ({SENT})", SENT == [])
    setup([])
    s, d = start(["horn", "multi", "ws"])
    ok(f"kein Knoten bereit: horn_block node ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "node" and SENT == [])
    setup([("horn-1", True), ("horn-2", False)])
    s, d = start(["horn", "multi"])
    ok(f"Knoten ohne Tunnel bereit: Auftrag dmhorn geht raus ({SENT}, {s}, {d.get('horn_block')})",
       SENT == ["dmhorn"] and s == 409 and d.get("horn_block") == "off")
    # Bugs-DB 2357: eingebauter Knoten im Tunnel-Betrieb + geteilter Knoten ohne Tunnel (horn-1), anrufendes Geraet mit "ws"
    # (App, Mac-Huelle): der Anruf muss auf horn-1 liegen - das Gegenueber (anderer Server) erreicht nur diesen. Vorher lag
    # er bei gleicher Last auf dem eingebauten (Feld 24.11.: Kaskade eingebauter <- horn-1, Ton fehlte)
    for caps, what in ((["horn", "multi", "ws"], "App/Huelle mit ws"), (["horn", "multi", "frames"], "Browser mit Rahmen-Weg"),
                       (["horn", "multi"], "App ohne ws")):
        setup([("saga.proxy", True), ("horn-1", False)])
        horn.nodes = lambda c: [{"id": "saga.proxy", "builtin": 1, "name": "saga.proxy", "share": 0},
                                {"id": "horn-1", "builtin": 0, "name": "horn-1", "share": 1}]
        X.FC.sync_call = lambda p, body, path=None: (SENT.append(body.get("op")), (200, {}))[1]
        got = {}

        def group_create(c, ch, cid, media, starter, dev, caps_, now, name="", dm_peer=None, ring=True, node=None, **kw):
            # wie calls.group_create: ohne vorgegebenen Knoten der erste bereite fuer die caps des anrufenden Geraets
            got["node"] = (node or horn.ready_nodes(c, ws=horn.caps_reach(caps_))[0])["id"]
            return {"id": cid}
        old = (X.calls.group_create, X.calls._dict, X.calls.horn_block)
        X.calls.group_create, X.calls._dict, X.calls.horn_block = group_create, lambda c, call, uid: {}, lambda *a, **k: {}
        try:
            s, d = start(caps)
        finally:
            X.calls.group_create, X.calls._dict, X.calls.horn_block = old
        ok(f"Tunnel-Knoten + geteilter Knoten, {what}: Anruf auf horn-1 ({s}, {got.get('node')})", s == 200 and got.get("node") == "horn-1")
    # Bugs-DB 2357: Kandidaten fuer eigene Geraete im foederierten 1:1 - kein Tunnel-Knoten, solange ein anderer bereit ist
    # (Feld bb03f9d7: Anruf auf horn-1, die Geraete waehlten per Messung trotzdem den eingebauten -> Kaskade). Lokales 1:1
    # und Gruppen unveraendert; nur Tunnel-Knoten bereit: der bleibt.
    old = (horn.leg_ws, horn.token)
    horn.leg_ws, horn.token = (lambda c, cid, dev: "ws"), (lambda *a, **k: "tok")
    try:
        def names(callee, nodes, avoid=()):
            setup([(nid, tun) for nid, tun, _ in nodes])
            horn.nodes = lambda c: [{"id": nid, "builtin": int(b), "name": nid, "share": int(not b), "udp": int(not b),
                                     "url": "https://" + nid} for nid, _, b in nodes]
            call = {"id": "c" * 32, "kind": "group", "topo": "horn", "caller": "anna", "callee": callee}
            return [x["node"] for x in X.calls.horn_block(_C(), call, "d" * 32, "member", "desktop", avoid)["candidates"]]
        both = [("saga.proxy", True, True), ("horn-1", False, False)]
        got = names("bert@b.example", both)
        ok(f"Kandidaten foederiertes 1:1 (Geraet mit ws): nur horn-1, kein Tunnel-Knoten ({got})", got == ["horn-1"])
        got = names("bert", both)
        ok(f"Kandidaten lokales 1:1: beide (Kaskade wie bisher) ({got})", sorted(got) == ["horn-1", "saga.proxy"])
        got = names("", both)
        ok(f"Kandidaten Gruppenanruf: beide ({got})", sorted(got) == ["horn-1", "saga.proxy"])
        # Pruefer: Geraet erreicht horn-1 nicht (nennt ihn in avoid, z. B. Netz laesst nur die Saga-Adresse zu) -> der
        # Tunnel-Knoten kommt wieder (Kaskade wie vor Bugs-DB 2357)
        got = names("bert@b.example", both, avoid=("horn-1",))
        ok(f"Kandidaten foederiertes 1:1, horn-1 gemieden: Tunnel-Knoten ({got})", got == ["saga.proxy"])
        got = names("bert@b.example", [("saga.proxy", True, True)])
        ok(f"Kandidaten foederiertes 1:1, nur Tunnel-Knoten bereit: der bleibt ({got})", got == ["saga.proxy"])
    finally:
        horn.leg_ws, horn.token = old
    n = sum(RES)
    print(f"\nHORN-Y-FED-REACH {n}/{len(RES)} - " + ("ALLES GRUEN" if n == len(RES) else "FEHLER"))
    sys.exit(0 if n == len(RES) else 1)


if __name__ == "__main__":
    main()
