"""Bughunt 3: Namensaufloesung mit Zeitlimit (saga/netguard.py lookup) in den Vorab-Pruefungen von ai, calendars, push,
fed_chat, fed_horn, horn_ops, immich und nextcloud. Vorher rief jede Stelle socket.getaddrinfo ohne Zeitlimit - ein
haengender DNS-Server hielt so je Abruf einen Thread des Threadpools bis zu 5 s x Versuche x Server fest.

Geprueft (im Prozess, ohne Server): ein haengendes getaddrinfo (Attrappe, 30 s) beendet jede Pruefung nach ~DNS-Zeitlimit
mit "nicht aufloesbar" statt zu haengen; schnelle Antworten laufen weiter durch; im Code ruft ausser netguard keine
Stelle unter saga/ mehr socket.getaddrinfo direkt auf."""
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import netguard  # noqa: E402

res = []


def ok(msg, cond):
    res.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + msg, flush=True)


REAL = socket.getaddrinfo
HANG = threading.Event()


def fake(host, port, *a, **kw):
    if str(host).endswith(".haengt.example"):
        HANG.wait(30)
        raise socket.gaierror(socket.EAI_NONAME, "zu spaet")
    if str(host).endswith(".schnell.example"):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 0))]
    return REAL(host, port, *a, **kw)


def timed(fn):
    t0 = time.monotonic()
    try:
        r = fn()
    except Exception as e:                                # noqa: BLE001 - Art der Ausnahme wird mitgeprueft
        r = e
    return r, time.monotonic() - t0


def main():
    netguard.DNS_TIMEOUT = 0.8
    lim = netguard.DNS_TIMEOUT + 1.0
    socket.getaddrinfo = fake
    try:
        r, dt = timed(lambda: netguard.lookup("a.haengt.example", 443, timeout=0.8, type=socket.SOCK_STREAM))
        ok(f"lookup: haengender Name -> gaierror nach Zeitlimit ({type(r).__name__}, {dt:.1f} s)",
           isinstance(r, socket.gaierror) and dt < lim)
        r, dt = timed(lambda: netguard.lookup("a.schnell.example", 443, type=socket.SOCK_STREAM))
        ok(f"lookup: schnelle Antwort unveraendert ({r if isinstance(r, Exception) else r[0][4]}, {dt:.1f} s)",
           isinstance(r, list) and r[0][4][0] == "93.184.216.34")

        from saga import ai
        r, dt = timed(lambda: ai.resolve("https://x.haengt.example/v1"))
        ok(f"ai.resolve: nicht aufloesbar statt haengen ({r[1] if isinstance(r, tuple) else r}, {dt:.1f} s)",
           isinstance(r, tuple) and r[0] == [] and r[1] and dt < lim)

        from saga import calendars
        calendars._admin_hosts = lambda: set()
        import auth
        auth.settings = lambda *a, **k: {}
        r, dt = timed(lambda: calendars._cal_host_ok("x.haengt.example", "x.haengt.example:443"))
        ok(f"calendars._cal_host_ok: abgelehnt statt haengen ({r}, {dt:.1f} s)", r is False and dt < lim)

        from saga import push
        r, dt = timed(lambda: push._resolves_ok("https://x.haengt.example/push"))
        ok(f"push._resolves_ok: False statt haengen ({r}, {dt:.1f} s)", r is False and dt < lim)

        from saga import fed_chat
        r, dt = timed(lambda: fed_chat._dial("https://x.haengt.example"))
        ok(f"fed_chat._dial: [] statt haengen ({r}, {dt:.1f} s)", r == [] and dt < lim)

        from saga import fed_horn
        r, dt = timed(lambda: fed_horn._dial_ok("https://x.haengt.example"))
        ok(f"fed_horn._dial_ok: [] statt haengen ({r}, {dt:.1f} s)", r == [] and dt < lim)

        from saga import horn_ops
        r, dt = timed(lambda: horn_ops.dns_needed({"name": "x.haengt.example", "v4": "203.0.113.5", "v6": None}))
        ok(f"horn_ops.dns_needed: ok False statt haengen ({r}, {dt:.1f} s)", isinstance(r, list) and r and r[0]["ok"] is False and dt < lim)
    finally:
        socket.getaddrinfo = REAL
        HANG.set()

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "saga")
    direct = []
    for f in sorted(os.listdir(root)):
        if f.endswith(".py") and f != "netguard.py":
            src = open(os.path.join(root, f), encoding="utf-8").read()
            direct += [f"{f}:{src[:m.start()].count(chr(10)) + 1}" for m in re.finditer(r"socket\.getaddrinfo\(", src)]
    ok(f"kein direktes socket.getaddrinfo ausser netguard ({direct})", not direct)

    print(f"\nDNS-ZEITLIMIT {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
