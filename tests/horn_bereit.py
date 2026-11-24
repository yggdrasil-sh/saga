"""Knoten erst nach der Anmeldung bereit (Bugs-DB 2254): Zwischen dem Eintrag der Steuerverbindung (_CONNS) und _welcome
(Wege ws/tunnel/ice_tcp in _LIVE) nimmt ein Knoten keine neuen Beine an; alive() bleibt davon unberuehrt (laufende Anrufe).
Ohne Server und ohne Datenbank-Eintraege: Knoten als dict, Verbindung als Attrappe.
Aufruf: PYTHONPATH=tests/pgshim:. python tests/horn_bereit.py"""
import sys
import time

from saga import horn
from version import VERSION

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else info, flush=True)


class K:
    def __init__(self, welcomed):
        self.welcomed = welcomed


def main():
    nid = "bereit-2254"
    n = {"id": nid, "builtin": False, "state": "ok", "seen": time.time(), "url": "https://horn.example.org", "drain": False,
         "version": VERSION, "load": {"state": "ok", "legs": 0, "max_legs": 48}, "cert": {"state": "ok"}, "link_mbit": None}
    try:
        ok("ohne Steuerverbindung: wie bisher (Zustand aus der DB)", horn.ready(n) and horn.alive(n))
        horn._CONNS[nid] = K(False)
        ok("eingetragen, _welcome noch nicht fertig: nicht bereit", not horn.ready(n))
        ok("... aber lebendig (laufende Anrufe bleiben)", horn.alive(n))
        horn._CONNS[nid].welcomed = True
        ok("nach _welcome: bereit", horn.ready(n))
        horn._CONNS[nid] = object()                     # aeltere Attrappen ohne Feld: gelten als angemeldet
        ok("Verbindung ohne Feld 'welcomed': bereit", horn.ready(n))
        src = open(horn.__file__, encoding="utf-8").read()
        w = src[src.index("async def _welcome"):src.index("async def _writer")]
        ok("_welcome setzt welcomed erst nach den Wegen in _LIVE", 0 < w.index("_LIVE[nid] =") < w.index("k.welcomed = True"))
        ok("_Conn beginnt unangemeldet", "self.welcomed = False" in src[src.index("class _Conn"):src.index("def _origin")])
    finally:
        horn._CONNS.pop(nid, None)
    print(f"\n{sum(res)}/{len(res)} OK")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
