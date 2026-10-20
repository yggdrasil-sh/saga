"""Saga-Lastmessung (nur Standardbibliothek): Antwortzeit (p50/p95/max) und Groesse der wichtigsten Endpunkte, Last
durch parallele Geraete, Live-Kanal, CPU/RAM der Container (docker stats). Daten vorher mit tools/perf/gen.py erzeugen.

    python3 tools/perf/bench.py --base http://127.0.0.1:8170 --info perf.json --out ergebnis.json [--n 10] [--project saga-perf]

--info: die Zeile "PERF {...}" von gen.py (ohne "PERF "). Ergebnis: JSON (je Messung name/p50/p95/max/bytes/status)
und eine Markdown-Tabelle auf stdout."""
import argparse
import gzip
import http.client
import json
import os
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
    # Abgleich (2.2): ganz mit Stand, ohne Datenbank-Eintraege, nur Geaendertes (aeltere Server: ignorieren die Parameter)
    measure("GET /api/notes?delta=1 (Seitenbaum mit Stand, A)", "GET", "/api/notes?delta=1", a)
    measure("GET /api/notes?rows=0 (ohne DB-Eintraege, A)", "GET", "/api/notes?rows=0", a)
    _, _, _, d = req("GET", "/api/notes?delta=1", a)
    if isinstance(d, dict) and d.get("cursor"):
        measure("GET /api/notes?since (unveraendert, A)", "GET", "/api/notes?since=" + urllib.parse.quote(d["cursor"]), a)
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
    # 2.2 opt-in: nur eine Seite (SQL) bzw. Ansicht "Offen" (Filter, serverseitig wie im Web) + Seite
    measure("GET /rows groesste DB: Seite limit=100", "GET", f"/api/notes/{I['db_big']}/rows?limit=100&offset=100", a)
    measure("GET /rows groesste DB: Ansicht Offen + Suche, limit=100", "GET",
            f"/api/notes/{I['db_big']}/rows?view=offen&q=server&limit=100&tz=Europe%2FBerlin", a)
    measure("GET /rows groesste DB: Ansicht Offen, limit=100 (als B)", "GET", f"/api/notes/{I['db_big']}/rows?view=offen&limit=100", b)
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
        measure("GET /api/app/rows groesste DB: Widget Ansicht Offen, limit=8", "GET",
                f"/api/app/rows/{I['db_big']}?view=offen&limit=8&tz=Europe%2FBerlin", bearer=tok)
        measure("GET /api/app/today (Widget)", "GET", "/api/app/today", bearer=tok)
        req("DELETE", f"/api/settings/tokens/{d['id']}", a)


def bench_write():
    I, a = INFO, INFO["cookie_a"]
    print("Schreiben", flush=True)
    n = ARGS.n
    # Neuer Eintrag in der groessten DB: nur POST, dann wie das Web (POST + /api/notes + /api/shared), wie die App vor 2.1 (+ /rows)
    ts, ss, cs, web, app, made = [], [], [], [], [], []
    _, _, _, d0 = req("GET", "/api/notes?delta=1", a)
    cur = d0.get("cursor") if isinstance(d0, dict) else None
    sync = []
    for i in range(n):
        if cur:                                        # wie das Web ab 2.2: POST + Abgleich (since) + /api/shared
            s, bts, dt, d = req("POST", "/api/notes", a, {"parent_id": I["db_big"], "title": f"perf s{i}", "props": {"status": "todo"}})
            if d and d.get("id"):
                made.append(d["id"])
            t0 = time.perf_counter()
            _, _, _, dd = req("GET", "/api/notes?since=" + urllib.parse.quote(cur), a); req("GET", "/api/shared", a)
            sync.append(dt + time.perf_counter() - t0)
            cur = (dd or {}).get("cursor") or cur
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
    if sync:
        stats("Neuer Eintrag + Abgleich Web (since+shared)", sync, [0], [200], "Ablauf")
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
    # 2.2-Clients: Baum ohne DB-Eintraege bzw. nur Abgleich (aeltere Server ignorieren die Parameter = voller Baum)
    _, _, _, d = req("GET", "/api/notes?delta=1", a)
    cur = d.get("cursor") if isinstance(d, dict) else None
    for label, path in (("rows=0", "/api/notes?rows=0"), ("since", "/api/notes?since=" + urllib.parse.quote(cur) if cur else None)):
        if not path:
            continue
        stop, small = [False], []

        def heavy2(p=path):
            while not stop[0]:
                req("GET", p, a)
        th = [threading.Thread(target=heavy2, daemon=True) for _ in range(8)]
        for t in th:
            t.start()
        time.sleep(1)
        for _ in range(ARGS.n):
            s, b, dt, _ = req("GET", f"/api/notes/{I['page_normal']}", a)
            small.append(dt)
        stop[0] = True
        for t in th:
            t.join(timeout=300)
        stats(f"Seite oeffnen waehrend 8x /api/notes?{label} parallel", small, [0], [200], "Parallel")


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


def bench_calls():
    """2.4 (E0): Anruf-Signalisierung ueber den Server (Medien laufen nie durch Saga). Versiegelte Signale sind fuer den
    Server undurchsichtig - hier Zufallsbytes in der Groesse eines echten Angebots. Je Anruf: anrufen, annehmen, 20 Signale
    (Trickle-ICE, Neuverhandlung/ICE-Restart in beide Richtungen), auflegen. Hoechstens 3 Anrufe (PAIR_RATE je Minute)."""
    import base64
    import os
    import secrets
    I, a, b = INFO, INFO["cookie_a"], INFO["cookie_b"]
    if not (I.get("chat") and I.get("dev_a") and I.get("dev_b")):
        return
    print("Anrufe", flush=True)
    fake = lambda n: base64.b64encode(os.urandom(n)).decode()  # noqa: E731
    tc, ta, tsig, tall, cs = [], [], [], [], []
    for _ in range(3):
        cid = secrets.token_hex(16)
        t0 = time.perf_counter()
        s, _, dt, d = req("POST", "/api/calls", a, {"id": cid, "chat_id": I["chat"], "media": "video", "device": I["dev_a"],
                                                     "signals": {I["dev_b"]: fake(4200)}, "caps": ["reneg", "want", "bye"]})
        tc.append(dt); cs.append(s)
        if s != 200:
            print("  Anruf nicht moeglich:", s, d, flush=True)
            break
        s, _, dt, _ = req("POST", f"/api/calls/{cid}/answer", b, {"device": I["dev_b"], "signals": {I["dev_a"]: fake(4000)},
                                                                   "caps": ["reneg", "want", "bye"]})
        ta.append(dt); cs.append(s)
        for i in range(20):
            who, dev, to = (a, I["dev_a"], I["dev_b"]) if i % 2 == 0 else (b, I["dev_b"], I["dev_a"])
            s, _, dt, _ = req("POST", f"/api/calls/{cid}/signal", who, {"device": dev, "signals": {to: fake(600 if i % 5 else 4200)}})
            tsig.append(dt); cs.append(s)
        tall.append(time.perf_counter() - t0)
        req("POST", f"/api/calls/{cid}/end", a, {"reason": "hangup", "connected": True})
    stats("Anruf: anrufen (POST /api/calls)", tc, [0], cs[:1] if tc else [0], "Anrufe")
    stats("Anruf: annehmen", ta or [0], [0], [200], "Anrufe")
    stats("Anruf: Signal weiterreichen", tsig or [0], [0], sorted(set(cs)), "Anrufe")
    stats("Anruf: Ablauf anrufen+annehmen+20 Signale", tall or [0], [0], [200], "Ablauf")
    # 2.4 (E4/E5): derselbe Ablauf als Raum (beide Geraete melden "multi": Endpunkte je Geraet, Teilnehmerliste an alle
    # Geraete beider Personen) und die Abfrage laufender Anrufe (Leiste "Anruf laeuft auf …", jede Seite beim Laden)
    if "PAIR_WAIT" not in os.environ:
        time.sleep(61)                                  # PAIR_RATE: hoechstens 3 Anrufe je Paar und Minute
    caps = ["reneg", "want", "bye", "screen2", "multi"]
    tc3, tsig3, tact, cs3 = [], [], [], []
    for _ in range(3):
        cid = secrets.token_hex(16)
        s, _, dt, d = req("POST", "/api/calls", a, {"id": cid, "chat_id": I["chat"], "media": "video", "device": I["dev_a"],
                                                     "signals": {I["dev_b"]: fake(4200)}, "caps": caps})
        tc3.append(dt); cs3.append(s)
        if s != 200:
            print("  Anruf (Raum) nicht moeglich:", s, d, flush=True)
            break
        req("POST", f"/api/calls/{cid}/answer", b, {"device": I["dev_b"], "signals": {I["dev_a"]: fake(4000)}, "caps": caps})
        for i in range(20):
            who, dev, to = (a, I["dev_a"], I["dev_b"]) if i % 2 == 0 else (b, I["dev_b"], I["dev_a"])
            s, _, dt, _ = req("POST", f"/api/calls/{cid}/signal", who, {"device": dev, "signals": {to: fake(600 if i % 5 else 4200)}})
            tsig3.append(dt); cs3.append(s)
        for _ in range(5):
            s, _, dt, _ = req("GET", "/api/calls/active", b)
            tact.append(dt); cs3.append(s)
        req("POST", f"/api/calls/{cid}/end", a, {"reason": "hangup", "connected": True, "device": I["dev_a"]})
    stats("Anruf: anrufen als Raum (multi)", tc3 or [0], [0], cs3[:1] if tc3 else [0], "Anrufe")
    stats("Anruf: Signal im Raum", tsig3 or [0], [0], sorted(set(cs3)), "Anrufe")
    stats("Anruf: GET /api/calls/active (laufender Anruf)", tact or [0], [0], [200], "Anrufe")


def _cpu(pid):
    """CPU-Sekunden (utime+stime) des Server-Prozesses (/proc, nur nativ - im Container ueber docker stats)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            v = f.read().rsplit(")", 1)[1].split()
        return (int(v[11]) + int(v[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def bench_receipts():
    """2.4: Gelesen-Bestaetigungen und Online-Status in einer Gruppe mit 50 Mitgliedern (gen.py). Alle 50 haben den
    Live-Kanal offen; 49 lesen 20 s lang alle 2 s (POST /read mit versiegelten Bestaetigungen fuer die 49 anderen,
    Zufallsbytes in echter Groesse - der Server sieht nur Base64). Gemessen: p95 von /read mit Bestaetigung, Live-Ereignisse
    je Konto (Buendelung: hoechstens eins je 2 s und Chat), Python-CPU waehrend der Phase (--pid). Danach abonnieren alle
    den Online-Status der anderen (POST /api/chat/presence, 49 Personen). Gegen einen Server ohne 2.4 laufen dieselben
    Anfragen (/read ohne Wirkung der Bestaetigung, Presence 404) - so vergleicht die Python-CPU gleiche Last."""
    import base64
    I = INFO
    if not I.get("group") or not I.get("cookies_g"):
        return
    print("Gelesen/Online (Gruppe mit %d)" % len(I["group_members"]), flush=True)
    ck = dict(I["cookies_g"], **{"perf-a": I["cookie_a"], "perf-b": I["cookie_b"]})
    mem = I["group_members"]
    seal = lambda: base64.b64encode(os.urandom(250)).decode()  # noqa: E731   (~ echte Groesse: Siegel + Signatur + JSON)
    ls = {u: Listener(ck[u]) for u in mem}
    for x in ls.values():
        x.start()
    for x in ls.values():
        x.ok.wait(10)
    time.sleep(1.0)
    c0, t_start = _cpu(ARGS.pid) if ARGS.pid else None, time.perf_counter()
    times, codes, lock = [], [], threading.Lock()

    def reader(u, k):
        for r in range(10):
            body = {"seq": I["group_last"], "receipt": {"keys": {"backup:" + v: seal() for v in mem if v != u}}}
            s, _, dt, d = req("POST", f"/api/chats/{I['group']}/read", ck[u], body)
            with lock:
                times.append(dt); codes.append(s)
            time.sleep(max(0.0, 2.0 - dt))
    th = [threading.Thread(target=reader, args=(u, i)) for i, u in enumerate(mem[1:])]
    for i, t_ in enumerate(th):
        t_.start()
        time.sleep(2.0 / len(th))                   # gleichmaessig ueber die 2 s verteilt
    for t_ in th:
        t_.join()
    time.sleep(2.5)                                 # letzte Buendelung
    dur = time.perf_counter() - t_start
    c1 = _cpu(ARGS.pid) if ARGS.pid else None
    evs = [sum(1 for _, e in x.events if e == "chat.receipt") for x in ls.values()]
    live_kb = round(sum(x.bytes for x in ls.values()) / 1024)
    cpu = round((c1 - c0) / dur * 100, 1) if c0 is not None and c1 is not None else None
    stats("Gelesen: POST /read mit Bestaetigung (49 Leser, 50er-Gruppe)", times or [0], [0], sorted(set(codes)), "Gelesen",
          extra={"cpu_pct": cpu, "events_max": max(evs), "events_avg": round(sum(evs) / len(evs), 1), "requests": len(times),
                 "seconds": round(dur, 1), "live_kb": live_kb})
    print(f"  Python-CPU {cpu} % ueber {dur:.0f} s, {len(times)} Bestaetigungen, chat.receipt je Konto max {max(evs)}, "
          f"Live-Kanal gesamt {live_kb} KB", flush=True)
    # Online-Status: jedes Mitglied abonniert die 49 anderen
    pt, pc = [], []
    c0, t0 = _cpu(ARGS.pid) if ARGS.pid else None, time.perf_counter()
    for u in mem:
        s, _, dt, d = req("POST", "/api/chat/presence", ck[u], {"users": [v for v in mem if v != u]})
        pt.append(dt); pc.append(s)
    c1, dur = _cpu(ARGS.pid) if ARGS.pid else None, time.perf_counter() - t0
    stats("Online: POST /api/chat/presence (49 Personen)", pt, [0], sorted(set(pc)), "Gelesen",
          extra={"cpu_s": round(c1 - c0, 2) if c0 is not None and c1 is not None else None})
    for x in ls.values():
        x.close()


def main():
    global ARGS, INFO
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--info", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--project", default="saga-perf")
    ap.add_argument("--only", default="read,write,apps,parallel,live,calls,receipts")
    ap.add_argument("--pid", type=int, default=0, help="Server-Prozess (nativ): Python-CPU je Phase aus /proc")
    ARGS = ap.parse_args()
    INFO = json.load(open(ARGS.info))
    smp = Sampler()
    smp.start()
    only = ARGS.only.split(",")
    phases = {}
    for name, fn in (("read", bench_read), ("write", bench_write), ("apps", bench_apps), ("parallel", bench_parallel), ("live", bench_live),
                     ("calls", bench_calls), ("receipts", bench_receipts)):
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
