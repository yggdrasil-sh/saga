"""Saga-Lastmessung (nur Standardbibliothek): Antwortzeit (p50/p95/max) und Groesse der wichtigsten Endpunkte, Last
durch parallele Geraete, Live-Kanal, CPU/RAM der Container (docker stats). Daten vorher mit tools/perf/gen.py erzeugen.

    python3 tools/perf/bench.py --base http://127.0.0.1:8170 --info perf.json --out ergebnis.json [--n 10] [--project saga-perf]

--info: die Zeile "PERF {...}" von gen.py (ohne "PERF "). Ergebnis: JSON (je Messung name/p50/p95/max/bytes/status)
und eine Markdown-Tabelle auf stdout."""
import argparse
import gzip
import http.client
import json
import statistics
import subprocess
import threading
import time
import urllib.parse

ARGS = None
INFO = {}
RESULTS = []


def req(method, path, cookie=None, body=None, bearer=None, timeout=120):
    """-> (status, bytes, sekunden, daten)"""
    u = urllib.parse.urlparse(ARGS.base)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=timeout)
    h = {"Accept-Language": "de", "Accept-Encoding": "gzip"}
    if cookie:
        h["Cookie"] = "saga=" + cookie
        h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    raw = None
    if body is not None:
        raw = json.dumps(body)
        h["Content-Type"] = "application/json"
    t0 = time.perf_counter()
    c.request(method, path, body=raw, headers=h)
    r = c.getresponse()
    data = r.read()
    dt = time.perf_counter() - t0
    enc = r.getheader("content-encoding")
    c.close()
    d = None
    plain = gzip.decompress(data) if enc == "gzip" else data       # Groesse = uebertragene Bytes (ggf. komprimiert)
    if "json" in (r.getheader("content-type") or "") and enc in (None, "gzip"):
        try:
            d = json.loads(plain)
        except ValueError:
            pass
    return r.status, len(data), dt, d


def stats(name, times, sizes, codes, group, extra=None):
    times = sorted(times)
    q = lambda p: times[min(len(times) - 1, int(round(p * (len(times) - 1))))]  # noqa: E731
    r = {"name": name, "group": group, "n": len(times), "p50_ms": round(statistics.median(times) * 1000, 1),
         "p95_ms": round(q(0.95) * 1000, 1), "max_ms": round(times[-1] * 1000, 1), "bytes": max(sizes) if sizes else 0,
         "status": sorted(set(codes))}
    if extra:
        r.update(extra)
    RESULTS.append(r)
    print(f"  {name:<48} p50 {r['p50_ms']:>8} ms  p95 {r['p95_ms']:>8} ms  {r['bytes']:>10} B  {r['status']}", flush=True)
    return r


def measure(name, method, path, cookie=None, body=None, bearer=None, n=None, group="Lesen", warm=1):
    n = n or ARGS.n
    for _ in range(warm):
        req(method, path, cookie, body, bearer)
    ts, ss, cs = [], [], []
    for _ in range(n):
        s, b, dt, _ = req(method, path, cookie, body, bearer)
        ts.append(dt); ss.append(b); cs.append(s)
    return stats(name, ts, ss, cs, group)


# --- CPU/RAM der Container ----------------------------------------------------------------------------------
class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop, self.peak, self.samples = False, {}, []

    def run(self):
        while not self.stop:
            try:
                out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.CPUPerc}};{{.MemUsage}}"],
                                     capture_output=True, text=True, timeout=20).stdout
            except Exception:
                break
            for line in out.splitlines():
                name, cpu, mem = line.split(";")
                if not name.startswith(ARGS.project + "-"):
                    continue
                cpu = float(cpu.rstrip("%") or 0)
                m = mem.split("/")[0].strip()
                mb = float(m[:-3]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(m[-3:], 1) if m[-3:] in ("KiB", "MiB", "GiB") else 0
                k = "saga" if "-saga-" in name else "db"
                p = self.peak.setdefault(k, {"cpu_pct": 0, "mem_mb": 0})
                p["cpu_pct"], p["mem_mb"] = max(p["cpu_pct"], cpu), max(p["mem_mb"], round(mb))
                self.samples.append((time.time(), k, cpu, round(mb)))


# --- Live-Kanal ------------------------------------------------------------------------------------------------
class Listener(threading.Thread):
    """SSE-Verbindung: merkt sich Ankunftszeit je Ereignis (note.changed)"""
    def __init__(self, cookie):
        super().__init__(daemon=True)
        self.cookie, self.events, self.bytes, self.ok = cookie, [], 0, threading.Event()
        self.conn = None

    def run(self):
        u = urllib.parse.urlparse(ARGS.base)
        self.conn = http.client.HTTPConnection(u.hostname, u.port, timeout=300)
        self.conn.request("GET", "/api/events", headers={"Cookie": "saga=" + self.cookie, "Accept": "text/event-stream"})
        r = self.conn.getresponse()
        ev = None
        try:
            while True:
                line = r.fp.readline()
                if not line:
                    break
                self.bytes += len(line)
                s = line.decode("utf-8", "replace").strip()
                if s.startswith("event:"):
                    ev = s[6:].strip()
                    if ev == "hello":
                        self.ok.set()
                elif s.startswith("data:") and ev:
                    self.events.append((time.perf_counter(), ev))
        except Exception:
            pass

    def close(self):
        try:
            self.conn.sock.shutdown(2)
        except Exception:
            pass


def bench_read():
    I, a, b = INFO, INFO["cookie_a"], INFO["cookie_b"]
    print("Lesen", flush=True)
    measure("GET /api/notes (Seitenbaum, A)", "GET", "/api/notes", a)
    measure("GET /api/shared (Mit mir geteilt, B)", "GET", "/api/shared", b)
    measure("GET /api/notes (B, eigene 50)", "GET", "/api/notes", b)
    measure("GET /api/notes/{id} normale Seite", "GET", f"/api/notes/{I['page_normal']}", a)
    measure("GET /api/notes/{id} lange Seite", "GET", f"/api/notes/{I['page_long']}", a)
    if I.get("page_deep"):
        measure("GET /api/notes/{id} Tiefe 6", "GET", f"/api/notes/{I['page_deep']}", a)
    measure("GET /api/notes/{id} geteilte Seite (B)", "GET", f"/api/notes/{I['shared_root']}", b)
    measure("GET /api/notes/{db} Datenbank-Seite", "GET", f"/api/notes/{I['db_big']}", a)
    measure(f"GET /rows groesste DB ({I['db_big_rows']} Zeilen)", "GET", f"/api/notes/{I['db_big']}/rows", a)
    measure("GET /rows groesste DB als B (geteilt)", "GET", f"/api/notes/{I['db_big']}/rows", b)
    measure(f"GET /rows mittlere DB ({I['db_mid_rows']} Zeilen)", "GET", f"/api/notes/{I['db_mid']}/rows", a)
    measure("POST /api/notes/search Treffer (\"projekt\")", "POST", "/api/notes/search", a, {"q": "projekt"})
    measure("POST /api/notes/search ohne Treffer", "POST", "/api/notes/search", a, {"q": "zzkeintrefferzz"})
    measure("POST /api/notes/search Schlagwort", "POST", "/api/notes/search", a, {"tag": "kunde"})
    measure("POST /api/notes/search ohne Treffer (B)", "POST", "/api/notes/search", b, {"q": "zzkeintrefferzz"})
    measure("GET / Dashboard (HTML)", "GET", "/", a)
    measure("GET / Dashboard (HTML, B)", "GET", "/", b)
    measure("GET /notes (HTML-Huelle)", "GET", "/notes", a)
    measure("GET /api/tasks", "GET", "/api/tasks", a)
    measure("GET /api/calendar/events (14 Tage)", "GET", "/api/calendar/events?start=2026-10-01&end=2026-10-15", a)
    measure("GET /api/notifications", "GET", "/api/notifications", a)
    measure("GET /api/notes/{id}/comments", "GET", f"/api/notes/{I['shared_root']}/comments", a)
    measure("GET /api/notes/{id}/backlinks", "GET", f"/api/notes/{I['page_normal']}/backlinks", a)
    measure("GET /api/notes/{id}/shares", "GET", f"/api/notes/{I['shared_root']}/shares", a)
    measure("GET /api/trash", "GET", "/api/trash", a)
    measure("GET /api/chats", "GET", f"/api/chats?device={I['dev_a']}", a)
    measure("GET /api/chats/unread", "GET", "/api/chats/unread", a)
    measure("GET /api/chats/{id}/messages (neueste 50)", "GET", f"/api/chats/{I['chat']}/messages?device={I['dev_a']}", a)
    measure("GET /api/chats/{id}/messages (aelteste Seite)", "GET",
            f"/api/chats/{I['chat']}/messages?device={I['dev_a']}&before=60&limit=50", a)
    # Widgets / Teilen-Erweiterung (App-Schluessel)
    s, _, _, d = req("POST", "/api/settings/tokens", a, {"name": "perf"})
    tok = (d or {}).get("token")
    if tok:
        measure("GET /api/app/pages (Widget-Auswahl)", "GET", "/api/app/pages", bearer=tok)
        measure("GET /api/app/rows groesste DB (Widget)", "GET", f"/api/app/rows/{I['db_big']}", bearer=tok)
        measure("GET /api/app/today (Widget)", "GET", "/api/app/today", bearer=tok)
        req("DELETE", f"/api/settings/tokens/{d['id']}", a)


def bench_write():
    I, a = INFO, INFO["cookie_a"]
    print("Schreiben", flush=True)
    n = ARGS.n
    # Neuer Eintrag in der groessten DB: nur POST, dann wie das Web (POST + /api/notes + /api/shared), wie die App vor 2.1 (+ /rows)
    ts, ss, cs, web, app, made = [], [], [], [], [], []
    for i in range(n):
        s, bts, dt, d = req("POST", "/api/notes", a, {"parent_id": I["db_big"], "title": f"perf {i}", "props": {"status": "todo"}})
        ts.append(dt); ss.append(bts); cs.append(s)
        if d and d.get("id"):
            made.append(d["id"])
        t0 = time.perf_counter()
        req("GET", "/api/notes", a); req("GET", "/api/shared", a)
        web.append(dt + time.perf_counter() - t0)
        t0 = time.perf_counter()
        req("GET", f"/api/notes/{I['db_big']}/rows", a)
        app.append(web[-1] + time.perf_counter() - t0)
    stats("POST /api/notes neuer DB-Eintrag (nur Anlegen)", ts, ss, cs, "Schreiben")
    stats("Neuer Eintrag + Nachladen Web (notes+shared)", web, [0], [200], "Ablauf")
    stats("Neuer Eintrag + Nachladen inkl. /rows", app, [0], [200], "Ablauf")
    # Zeile aendern (Status) - announce_row/Live
    ts, cs = [], []
    for i in range(n):
        s, _, dt, _ = req("PATCH", f"/api/notes/{made[i % len(made)]}", a, {"props": {"status": ("doing", "done")[i % 2]}})
        ts.append(dt); cs.append(s)
    stats("PATCH Zeile (Status)", ts, [0], cs, "Schreiben")
    # Seite speichern (Inhalt, mit rev)
    ts, cs = [], []
    pid = I["page_normal"]
    for i in range(n):
        _, _, _, d = req("GET", f"/api/notes/{pid}", a)
        body = json.loads(d["body"])
        body["blocks"].append({"id": "perf%06d" % i, "type": "paragraph", "data": {"text": "perf %d" % i}})
        s, _, dt, _ = req("PATCH", f"/api/notes/{pid}", a, {"body": json.dumps(body), "format": "blocks", "rev": d["rev"]})
        ts.append(dt); cs.append(s)
    stats("PATCH Seite speichern (Inhalt)", ts, [0], cs, "Schreiben")
    for x in made:                                     # aufraeumen (Papierkorb)
        req("DELETE", f"/api/notes/{x}", a)


def bench_parallel():
    """8 'Geraete' laden gleichzeitig den Seitenbaum; daneben eine kleine Anfrage - wie stark bremst das?"""
    I, a = INFO, INFO["cookie_a"]
    print("Parallel", flush=True)
    stop, small = [False], []

    def heavy():
        while not stop[0]:
            req("GET", "/api/notes", a)

    th = [threading.Thread(target=heavy, daemon=True) for _ in range(8)]
    for t in th:
        t.start()
    time.sleep(1)
    for _ in range(ARGS.n):
        s, b, dt, _ = req("GET", f"/api/notes/{I['page_normal']}", a)
        small.append(dt)
    stop[0] = True
    for t in th:
        t.join(timeout=300)
    stats("Seite oeffnen waehrend 8x /api/notes parallel", small, [0], [200], "Parallel")
    t0, cnt = time.perf_counter(), [0]

    def one():
        req("GET", "/api/notes", a); cnt[0] += 1
    th = [threading.Thread(target=one) for _ in range(10)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    dt = time.perf_counter() - t0
    stats("10x /api/notes gleichzeitig (Gesamtdauer)", [dt], [0], [200], "Parallel")


def bench_live():
    """5 Verbindungen je Konto; B hat die geteilte Seite offen (Anwesenheit); A speichert 10x - Latenz bis zum Ereignis"""
    I, a, b = INFO, INFO["cookie_a"], INFO["cookie_b"]
    print("Live", flush=True)
    ls = [Listener(a) for _ in range(5)] + [Listener(b) for _ in range(5)]
    for x in ls:
        x.start()
    for x in ls:
        x.ok.wait(10)
    pid = I["shared_root"]
    for _ in range(2):
        req("POST", "/api/presence", b, {"note_id": pid})
    ts, lat, cs = [], [], []
    for i in range(ARGS.n):
        _, _, _, d = req("GET", f"/api/notes/{pid}", a)
        n0 = sum(len(x.events) for x in ls[5:])
        t0 = time.perf_counter()
        s, _, dt, _ = req("PATCH", f"/api/notes/{pid}", a, {"title": d["title"].split(" #")[0] + f" #{i}", "rev": d["rev"]})
        ts.append(dt); cs.append(s)
        end = time.time() + 5
        while time.time() < end and sum(len(x.events) for x in ls[5:]) == n0:
            time.sleep(0.005)
        got = [e[0] for x in ls[5:] for e in x.events[-1:]]
        if got:
            lat.append(max(got) - t0)
    stats("PATCH mit 10 Live-Verbindungen", ts, [0], cs, "Live")
    if lat:
        stats("Live: Speichern -> Ereignis bei B", lat, [0], [200], "Live")
    RESULTS.append({"name": "Live: Bytes empfangen", "group": "Live", "bytes": sum(x.bytes for x in ls)})
    for x in ls:
        x.close()


def seq(steps, cookie):
    """Folge von Anfragen wie eine App sie schickt -> (sekunden, bytes, anzahl)"""
    t, b = 0.0, 0
    for m, p, body in steps:
        _, n, dt, _ = req(m, p, cookie, body)
        t += dt; b += n
    return t, b, len(steps)


def bench_apps():
    """Netzwerk-Ablaeufe der Apps (aus dem Code: iOS/macOS Store.refresh, Database.add; Android SagaRepository.refresh;
    Linux store.refresh/database.add) - Summe der Antwortzeiten (nacheinander) und Bytes je Aktion."""
    I, a = INFO, INFO["cookie_a"]
    print("Apps", flush=True)
    today = time.strftime("%Y-%m-%d")
    refresh_ios = [("GET", "/api/notes", None), ("GET", "/api/shared", None), ("GET", "/api/fed/shared", None),
                   ("GET", "/api/notifications?limit=0", None), ("GET", "/api/chats/unread", None), ("GET", "/api/ai/status", None),
                   ("GET", "/api/tasks", None), ("GET", f"/api/calendar/events?start={today}&end={today}", None)]
    refresh_and = [("GET", "/api/notes", None), ("GET", "/api/shared", None), ("GET", "/api/fed/shared", None), ("GET", "/api/tasks", None),
                   ("GET", f"/api/calendar/events?start={today}&end={today}", None)]
    refresh_lin = [("GET", "/api/notes", None), ("GET", "/api/tasks", None), ("GET", "/api/shared", None)]
    rows = ("GET", f"/api/notes/{I['db_big']}/rows", None)
    out = {}
    for name, steps in (("iOS/macOS: Start/Aktualisieren/60-s-Takt", refresh_ios), ("Android: Start/60-s-Takt", refresh_and),
                        ("Linux: Start/60-s-Takt", refresh_lin),
                        ("iOS/macOS: grosse DB oeffnen", [rows]),
                        ("Linux: grosse DB oeffnen", [rows])):
        ts, bs = [], []
        for _ in range(max(3, ARGS.n // 2)):
            t, b, n = seq(steps, a)
            ts.append(t); bs.append(b)
        out[name] = stats(name, ts, bs, [200], "Apps", {"requests": len(steps)})
    made = []
    for name, steps in (("iOS/macOS: Neuer Eintrag (POST+rows+refresh+rows)", [rows] + refresh_ios + [rows]),
                        ("Android: Neue Seite (POST+refresh)", refresh_and),
                        ("Linux: Neuer Eintrag (POST+rows+refresh)", [rows] + refresh_lin)):
        ts, bs = [], []
        for _ in range(3):
            s, n0, dt0, d = req("POST", "/api/notes", a, {"parent_id": I["db_big"], "title": "app"})
            made.append((d or {}).get("id"))
            t, b, n = seq(steps, a)
            ts.append(t + dt0); bs.append(b + n0)
        stats(name, ts, bs, [200], "Apps", {"requests": len(steps) + 1})
    for x in made:
        if x:
            req("DELETE", f"/api/notes/{x}", a)
    # Tag mit App im Vordergrund: 8 h x 60/h Takt
    t = out["iOS/macOS: Start/Aktualisieren/60-s-Takt"]
    RESULTS.append({"name": "iOS/macOS: Datenvolumen 8 h Vordergrund (60-s-Takt)", "group": "Apps",
                    "bytes": t["bytes"] * 480, "mb": round(t["bytes"] * 480 / 1048576, 1)})


def main():
    global ARGS, INFO
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--info", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--project", default="saga-perf")
    ap.add_argument("--only", default="read,write,apps,parallel,live")
    ARGS = ap.parse_args()
    INFO = json.load(open(ARGS.info))
    smp = Sampler()
    smp.start()
    only = ARGS.only.split(",")
    phases = {}
    for name, fn in (("read", bench_read), ("write", bench_write), ("apps", bench_apps), ("parallel", bench_parallel), ("live", bench_live)):
        if name in only:
            t0 = time.time()
            fn()
            phases[name] = (t0, time.time())
    smp.stop = True
    smp.join(timeout=30)
    res = {"stufe": INFO.get("stufe"), "info": {k: v for k, v in INFO.items() if not k.startswith("cookie")},
           "results": RESULTS, "peak": smp.peak, "phases": phases, "samples": smp.samples}
    json.dump(res, open(ARGS.out, "w"), indent=1)
    print("Spitze CPU/RAM:", json.dumps(smp.peak))


if __name__ == "__main__":
    main()
