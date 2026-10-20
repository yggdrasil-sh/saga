"""Messstufe "Medien parallel" (2.4, Konzept §8): wie viel CPU/RAM kosten Chat-Anhaenge den Python-Prozess - mit und
ohne saga-media? Nur Standardbibliothek.

1. Testdaten (im Saga-Verzeichnis bzw. im Container, wie gen.py; Konto perf-m, 20 Anhaenge zu je 10 MB):
       python3 tools/perf/media.py --setup  > ausgabe        (Zeile "PERF {...}")
2. Messen (von aussen gegen den Port; Prozess-IDs fuer CPU/RAM aus /proc):
       python3 tools/perf/media.py --base http://127.0.0.1:8080 --info media.info.json --out media.json \\
           --py-pid <uvicorn> [--media-pid <saga-media>] [--limits]
   a) 20 parallele Stroeme je 2 Mbit/s fuer --seconds (Standard 30 s) - CPU in % eines Kerns je Prozess, RAM-Zuwachs
   b) 20 x 10 MB parallel so schnell wie moeglich - Dauer, CPU-Sekunden je Prozess
   c) Range-TTFB: 50 Abrufe "bytes=a-(a+1 MiB)" an zufaelligen Stellen - p50/p95 bis zum ersten Byte
   --limits: Grenzwerte aus tools/perf/limits.json ("MEDIA") pruefen, Exit 1 bei Ueberschreitung.
3. Aufraeumen:  python3 tools/perf/media.py --cleanup"""
import argparse
import http.client
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.parse

N_FILES = 20
SIZE = 10 * 1024 * 1024
USER = "perf-m"
HZ = os.sysconf("SC_CLK_TCK")


# --- Testdaten (laeuft mit der Saga-Umgebung) --------------------------------------------------------------------
def _cookie(uid):
    import base64
    from itsdangerous import TimestampSigner
    from saga.core import SECRET
    return TimestampSigner(SECRET).sign(base64.b64encode(json.dumps({"uid": uid, "name": uid, "pwc": time.time() + 5}).encode())).decode()


def cleanup():
    import auth
    from db import connect
    from saga.chat import CHAT_DIR
    from saga.userdata import delete_user_data
    with connect() as c, c:
        for (fid,) in c.execute("SELECT id FROM chat_file WHERE uid=?", (USER,)).fetchall():
            try:
                os.remove(os.path.join(CHAT_DIR, fid))
            except OSError:
                pass
        c.execute("DELETE FROM chat_file WHERE uid=?", (USER,))
        c.execute("DELETE FROM chat WHERE created_by=?", (USER,))
    if auth.get_user(USER):
        delete_user_data(USER, drop_user=True)


def setup():
    import secrets
    import auth
    import db
    from db import connect
    from saga.chat import CHAT_DIR
    db.wait_ready()
    db.init_schema()
    cleanup()
    auth.create_user(USER, None, "Perf M")
    now = time.time()
    os.makedirs(CHAT_DIR, mode=0o700, exist_ok=True)
    fids = []
    with connect() as c, c:
        cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', ?, ?) RETURNING id", (USER, now)).fetchone()[0]
        c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,?,?)", (cid, USER, "admin", now))
        seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, body_ct, sig) VALUES "
                        "(?,?,?,?,?,?,?,?,?) RETURNING seq", (secrets.token_hex(16), cid, "msg", USER, "d", int(now * 1000), now, "x", "x")).fetchone()[0]
        block = os.urandom(1 << 20)
        for _ in range(N_FILES):
            fid = secrets.token_urlsafe(18)
            with open(os.path.join(CHAT_DIR, fid), "wb") as f:
                for _ in range(SIZE >> 20):
                    f.write(block)
            c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created, msg_seq) VALUES (?,?,?,?,?,?)", (fid, cid, USER, SIZE, now, seq))
            fids.append(fid)
    print("PERF " + json.dumps({"cookie": _cookie(USER), "fids": fids, "size": SIZE}), flush=True)


# --- Messung (nur HTTP und /proc) -----------------------------------------------------------------------------------
class Proc:
    def __init__(self, pid):
        self.pid = pid

    def cpu(self):
        if not self.pid:
            return 0.0
        with open(f"/proc/{self.pid}/stat") as f:
            x = f.read().rsplit(")", 1)[1].split()
        return (int(x[11]) + int(x[12])) / HZ        # utime + stime in Sekunden

    def mem(self, key="VmRSS"):
        if not self.pid:
            return 0
        with open(f"/proc/{self.pid}/status") as f:
            for line in f:
                if line.startswith(key + ":"):
                    return int(line.split()[1]) // 1024        # MB
        return 0


def get(base, cookie, fid, rate=None, rng=None, deadline=None):
    """-> (status, bytes, ttfb, dauer). rate: Bytes je Sekunde (gedrosseltes Lesen wie ein Player)."""
    u = urllib.parse.urlparse(base)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=120)
    h = {"Cookie": "saga=" + cookie, "X-Saga": "1"}
    if rng:
        h["Range"] = rng
    t0 = time.perf_counter()
    c.request("GET", f"/api/chat/files/{fid}", headers=h)
    r = c.getresponse()
    ttfb = time.perf_counter() - t0
    n = 0
    step = 64 * 1024 if not rate else max(4096, int(rate / 10))
    while True:
        if deadline and time.time() > deadline:
            break
        d = r.read(step)
        if not d:
            break
        n += len(d)
        if rate:
            time.sleep(len(d) / rate)
    c.close()
    return r.status, n, ttfb, time.perf_counter() - t0


def parallel(fn, k):
    out = [None] * k
    th = [threading.Thread(target=lambda i=i: out.__setitem__(i, fn(i))) for i in range(k)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    return out


def measure(a):
    info = json.load(open(a.info))
    ck, fids = info["cookie"], info["fids"]
    py, md = Proc(a.py_pid), Proc(a.media_pid)
    res = []

    def row(name, **kw):
        r = dict(name=name, **kw)
        res.append(r)
        print("  " + name.ljust(46) + "  ".join(f"{k} {v}" for k, v in kw.items()), flush=True)

    # Aufwaermen (Berechtigung, Seitencache)
    for f in fids:
        get(a.base, ck, f, rng="bytes=0-0")
    time.sleep(1)
    # Ruhewert
    c0, m0, t0 = py.cpu(), md.cpu(), time.time()
    time.sleep(5)
    idle_py = (py.cpu() - c0) / (time.time() - t0) * 100
    rss_py0, rss_md0 = py.mem(), md.mem()
    row("Ruhe (5 s)", py_cpu_pct=round(idle_py, 1), py_rss_mb=rss_py0, media_rss_mb=rss_md0)

    # a) 20 Stroeme je 2 Mbit/s
    rate = a.rate_mbit * 1e6 / 8
    end = time.time() + a.seconds
    c0, m0, t0 = py.cpu(), md.cpu(), time.time()
    peak = {"py": rss_py0, "md": rss_md0}
    stop = threading.Event()

    def sampler():
        while not stop.wait(0.5):
            peak["py"] = max(peak["py"], py.mem())
            peak["md"] = max(peak["md"], md.mem())
    st = threading.Thread(target=sampler, daemon=True)
    st.start()

    def stream(i):
        got, codes = 0, set()
        while time.time() < end:
            s, n, _, _ = get(a.base, ck, fids[i % len(fids)], rate=rate, deadline=end)
            got += n
            codes.add(s)
        return got, codes
    out = parallel(stream, a.n)
    dt = time.time() - t0
    stop.set()
    cpu_py = (py.cpu() - c0) / dt * 100
    cpu_md = (md.cpu() - m0) / dt * 100
    mbit = sum(g for g, _ in out) * 8 / dt / 1e6
    row(f"Medien parallel: {a.n} Stroeme je {a.rate_mbit} Mbit/s", sekunden=round(dt, 1), mbit_s=round(mbit, 1),
        py_cpu_pct=round(cpu_py, 1), py_rss_plus_mb=peak["py"] - rss_py0, media_cpu_pct=round(cpu_md, 1), media_rss_mb=peak["md"],
        status=sorted({c for _, cs in out for c in cs}))

    # b) 20 x 10 MB so schnell wie moeglich
    c0, m0, t0 = py.cpu(), md.cpu(), time.time()
    out = parallel(lambda i: get(a.base, ck, fids[i % len(fids)]), a.n)
    dt = time.time() - t0
    ok = all(s == 200 and n == info["size"] for s, n, _, _ in out)
    row(f"Medien parallel: {a.n} x 10 MB voll", sekunden=round(dt, 2), mb_s=round(a.n * info["size"] / dt / 1e6),
        py_cpu_s=round(py.cpu() - c0, 2), media_cpu_s=round(md.cpu() - m0, 2), py_rss_mb=py.mem(), vollstaendig=ok)

    # c) Range-TTFB 1 MiB
    ts = []
    for _ in range(50):
        o = random.randrange(0, info["size"] - (1 << 20))
        s, n, ttfb, _ = get(a.base, ck, random.choice(fids), rng=f"bytes={o}-{o + (1 << 20) - 1}")
        if s == 206 and n == 1 << 20:
            ts.append(ttfb)
    ts.sort()
    row("Range-TTFB 1 MiB", n=len(ts), p50_ms=round(statistics.median(ts) * 1000, 1) if ts else None,
        p95_ms=round(ts[min(len(ts) - 1, int(0.95 * (len(ts) - 1) + 0.5))] * 1000, 1) if ts else None)
    json.dump({"results": res, "py_pid": a.py_pid, "media_pid": a.media_pid}, open(a.out, "w"), indent=1)

    if a.limits:
        lim = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "limits.json")))["MEDIA"]
        r = {x["name"].split(":")[0]: x for x in res}
        s = res[1]
        checks = [("py_cpu_pct", s["py_cpu_pct"] - max(0.0, idle_py)), ("py_rss_plus_mb", s["py_rss_plus_mb"]),
                  ("media_cpu_pct", s["media_cpu_pct"]), ("media_rss_mb", s["media_rss_mb"]),
                  ("range_ttfb_p95_ms", r["Range-TTFB 1 MiB"]["p95_ms"] or 1e9)]
        bad = 0
        for k, v in checks:
            if k in lim and v > lim[k]:
                print(f"GRENZWERT MEDIA: {k} = {v} > {lim[k]}")
                bad += 1
        if not all(c in (200, 206) for c in s["status"]) or not res[2]["vollstaendig"]:
            print("GRENZWERT MEDIA: Fehlerantworten/unvollstaendig")
            bad += 1
        print(f"Grenzwerte MEDIA: {len(checks)} geprueft, {bad} ueberschritten")
        return 1 if bad else 0
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--setup", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--base")
    p.add_argument("--info")
    p.add_argument("--out", default="media.json")
    p.add_argument("--py-pid", type=int, default=0)
    p.add_argument("--media-pid", type=int, default=0)
    p.add_argument("--n", type=int, default=N_FILES)
    p.add_argument("--rate-mbit", type=float, default=2.0)
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--limits", action="store_true")
    a = p.parse_args()
    if a.setup:
        return setup()
    if a.cleanup:
        return cleanup()
    sys.exit(measure(a))


if __name__ == "__main__":
    main()
