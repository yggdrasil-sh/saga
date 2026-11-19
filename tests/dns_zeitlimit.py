"""Bughunt 3: Namensaufloesung mit Zeitlimit (saga/netguard.py lookup) in den Vorab-Pruefungen von ai, calendars, push,
fed_chat, fed_horn, horn_ops, immich und nextcloud. Vorher rief jede Stelle socket.getaddrinfo ohne Zeitlimit - ein
haengender DNS-Server hielt so je Abruf einen Thread des Threadpools bis zu 5 s x Versuche x Server fest.

Geprueft (im Prozess, ohne Server): ein haengendes getaddrinfo (Attrappe, 30 s) beendet jede Pruefung nach ~DNS-Zeitlimit
mit "nicht aufloesbar" statt zu haengen; schnelle Antworten laufen weiter durch; im Code ruft ausser netguard keine
Stelle unter saga/ mehr socket.getaddrinfo direkt auf.
Bugs-DB 2164: Rueckfall, wenn der DNS-Pool nichts mehr annimmt (Interpreter-Ende im kurzlebigen Prozess) - eigener Thread
mit Zeitlimit, gleiche Adresspruefung, Notfallweg ohne Threads; echtes Interpreter-Ende im Kindprozess."""
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


def fallback():
    """Bugs-DB 2164: nimmt der DNS-Pool nichts mehr an (Interpreter-Ende), loest netguard in einem eigenen Thread mit
    demselben Zeitlimit auf - gleiche Pruefung der Adressen; ohne neue Threads direkt (Notfallweg)."""
    import concurrent.futures
    import httpcore
    old = netguard._dns_pool
    dead = concurrent.futures.ThreadPoolExecutor(1)
    dead.shutdown()
    netguard._dns_pool = dead                              # submit -> RuntimeError wie nach dem Interpreter-Ende
    try:
        r, dt = timed(lambda: netguard.lookup("b.haengt.example", 443, timeout=0.8, type=socket.SOCK_STREAM))
        ok(f"Rueckfall-Thread: haengender Name -> gaierror nach Zeitlimit ({type(r).__name__}, {dt:.1f} s)",
           isinstance(r, socket.gaierror) and dt < 1.8)
        r, dt = timed(lambda: netguard.resolve("b.haengt.example", 443, timeout=0.8))
        ok(f"Rueckfall-Thread: resolve -> ConnectTimeout nach Zeitlimit ({type(r).__name__}, {dt:.1f} s)",
           isinstance(r, httpcore.ConnectTimeout) and dt < 1.8)
        r, _ = timed(lambda: netguard.lookup("b.schnell.example", 443, type=socket.SOCK_STREAM))
        ok(f"Rueckfall-Thread: schnelle Antwort ({r if isinstance(r, Exception) else r[0][4]})",
           isinstance(r, list) and r[0][4][0] == "93.184.216.34")
        real2 = socket.getaddrinfo
        socket.getaddrinfo = lambda h, p, *a, **k: ([(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", p))]
                                                    if str(h).endswith(".intern.example") else real2(h, p, *a, **k))
        try:
            gb = netguard.GuardBackend(lambda ip, host, port: netguard.is_public(ip))
            r, _ = timed(lambda: gb.connect_tcp("x.intern.example", 80, timeout=1.0))
            ok(f"Rueckfall-Thread: interne Adresse weiter gesperrt ({type(r).__name__})", isinstance(r, netguard.Blocked))
        finally:
            socket.getaddrinfo = real2
        start = threading.Thread.start

        def nostart(self, *a, **k):
            raise RuntimeError("can't create new thread at interpreter shutdown")
        threading.Thread.start = nostart                   # Notfallweg: auch keine neuen Threads mehr
        try:
            r, _ = timed(lambda: netguard.lookup("c.schnell.example", 443, type=socket.SOCK_STREAM))
        finally:
            threading.Thread.start = start
        ok(f"Notfallweg ohne Threads: direkt aufgeloest ({r if isinstance(r, Exception) else r[0][4]})",
           isinstance(r, list) and r[0][4][0] == "93.184.216.34")
    finally:
        netguard._dns_pool = old


CHILD = r"""
import socket, sys, threading, time
sys.path.insert(0, sys.argv[1])
from saga import netguard
netguard.lookup("127.0.0.1", 80)                        # Pool wie im Werkzeug-Prozess schon benutzt
REAL = socket.getaddrinfo
def fake(h, p, *a, **k):
    if str(h).endswith(".haengt.example"):
        time.sleep(30)
    return REAL(h, p, *a, **k)
socket.getaddrinfo = fake
def late():                                             # wie die Express-Zustellung nach dem Ende des Hauptthreads
    while threading.main_thread().is_alive():
        time.sleep(0.01)
    time.sleep(0.3)
    try:
        netguard._pool().submit(int).result()
        pool = "Pool nimmt noch an"
    except RuntimeError as e:
        pool = str(e)
    t0 = time.monotonic()
    try:
        netguard.lookup("x.haengt.example", 443, timeout=0.8)
        r = "kein Fehler"
    except Exception as e:
        r = type(e).__name__
    print("POOL=%s|HANG=%s|%.1f" % (pool, r, time.monotonic() - t0), flush=True)
    try:
        print("SCHNELL=%s" % netguard.lookup("127.0.0.1", 80)[0][4][0], flush=True)
    except Exception as e:
        print("SCHNELL=%s: %s" % (type(e).__name__, e), flush=True)
threading.Thread(target=late).start()
"""


def shutdown_child():
    """Echtes Interpreter-Ende: Aufloesung aus einem Thread, der nach dem Hauptthread laeuft - mit Zeitlimit, und der
    Prozess endet trotz haengendem DNS (Daemon-Thread) ohne zu warten (Bugs-DB 2164)."""
    import subprocess
    app = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-c", CHILD, app], capture_output=True, text=True, timeout=60)
    dt = time.monotonic() - t0
    out = p.stdout
    m = re.search(r"POOL=(.*)\|HANG=(\w+)\|([\d.]+)", out)
    ok(f"Interpreter-Ende: Pool lehnt ab ({m and m.group(1)})", m and "shutdown" in m.group(1))
    ok(f"Interpreter-Ende: haengender Name -> Zeitlimit statt RuntimeError ({m and m.group(2)}, {m and m.group(3)} s)",
       m and m.group(2) == "gaierror" and float(m.group(3)) < 1.8)
    ok(f"Interpreter-Ende: schnelle Aufloesung geht ({out.strip().splitlines()[-1:] or p.stderr[-200:]})", "SCHNELL=127.0.0.1" in out)
    ok(f"Interpreter-Ende: Prozess endet ohne auf das haengende DNS zu warten ({dt:.1f} s, rc {p.returncode})",
       dt < 10 and p.returncode == 0)


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
        fallback()
    finally:
        socket.getaddrinfo = REAL
        HANG.set()
    shutdown_child()

    root =os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "saga")
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
