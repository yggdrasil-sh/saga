"""Messstufe "VIDEO" (2.4 E2, Konzept §8): fortsetzbarer Upload von 1 GB im Format saga-file/v2 und Range-Abruf.
Wie viel CPU/RAM kostet das den Python-Prozess (und saga-media)? Nur Standardbibliothek (dazu saga.file_v2 fuer den Kopf).

1. Testdaten (Saga-Umgebung, wie media.py; Konto perf-v mit einem Chat):
       python3 tools/perf/video.py --setup > ausgabe                       (Zeile "PERF {...}")
2. Messen (von aussen; Prozess-IDs fuer CPU/RAM aus /proc):
       python3 tools/perf/video.py --base http://127.0.0.1:8080 --info video.info.json --out video.json \\
           --py-pid <uvicorn> [--media-pid <saga-media>] [--gb 1] [--limits]
   a) Upload --gb GB in 8-MiB-Teilen (PUT /api/chat/uploads/{id}) mit --mbit Mbit/s (Standard 400, wie ein schneller
      Anschluss; 0 = so schnell wie moeglich) - Dauer, MB/s, CPU in % eines Kerns je Prozess (Python abzueglich Ruhewert),
      CPU-Sekunden je GB, RAM-Zuwachs bzw. -Spitze
   b) Range-TTFB 1 MiB auf dem hochgeladenen Blob (50 zufaellige Stellen) - mit Sitzung und mit Medien-Token
   --v1: "vorher" (Saga 2.3, ohne saga-file/v2): als Anhaenge zu je 10 MB (POST /api/chats/{id}/files; wegen der
        Upload-Bremse 60/min mit --gb 0.5 messen und je GB vergleichen: py_cpu_s_je_gb)
   --limits: Grenzwerte aus tools/perf/limits.json ("VIDEO") pruefen, Exit 1 bei Ueberschreitung.
3. Aufraeumen:  python3 tools/perf/video.py --cleanup"""
import argparse
import http.client
import json
import os
import random
import statistics
import struct
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from media import Proc  # noqa: E402

USER = "perf-v"
PART = 8 * 1024 * 1024
CS = 262144


def _cookie(uid):
    import base64
    from itsdangerous import TimestampSigner
    from saga.core import SECRET
    return TimestampSigner(SECRET).sign(base64.b64encode(json.dumps({"uid": uid, "name": uid, "pwc": time.time() + 5, "lo": "perf"}).encode())).decode()


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
    import auth
    import db
    from db import connect
    db.wait_ready()
    db.init_schema()
    cleanup()
    auth.create_user(USER, None, "Perf V")
    now = time.time()
    with connect() as c, c:
        cid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free', ?, ?) RETURNING id", (USER, now)).fetchone()[0]
        c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,?,?)", (cid, USER, "admin", now))
    print("PERF " + json.dumps({"cookie": _cookie(USER), "chat": cid}), flush=True)


def blob_size(plain):
    return 48 + plain + 16 * max(1, -(-plain // CS))


def header(plain):
    return b"SGF2" + bytes([2, 0]) + struct.pack(">HIIQ", 0, CS, 0, plain) + os.urandom(16) + b"\0" * 8


def req(base, m, p, ck, body=None, hdr=None, js=None):
    u = urllib.parse.urlparse(base)
    c = http.client.HTTPConnection(u.hostname, u.port, timeout=300)
    h = {"Cookie": "saga=" + ck, "X-Saga": "1"}
    if js is not None:
        body = json.dumps(js).encode()
        h["Content-Type"] = "application/json"
    elif body is not None:
        h["Content-Type"] = "application/octet-stream"
    h.update(hdr or {})
    t0 = time.perf_counter()
    c.request(m, p, body=body, headers=h)
    r = c.getresponse()
    ttfb = time.perf_counter() - t0
    data = r.read()
    c.close()
    return r.status, data, ttfb


def measure(a):
    info = json.load(open(a.info))
    ck, cid = info["cookie"], info["chat"]
    py, md = Proc(a.py_pid), Proc(a.media_pid)
    res = []

    def row(name, **kw):
        res.append(dict(name=name, **kw))
        print("  " + name.ljust(46) + "  ".join(f"{k} {v}" for k, v in kw.items()), flush=True)

    req(a.base, "GET", "/api/chat/me", ck)
    time.sleep(1)
    c0, t0 = py.cpu(), time.time()
    time.sleep(5)
    idle_py = (py.cpu() - c0) / (time.time() - t0) * 100
    rss_py0, rss_md0 = py.mem(), md.mem()
    row("Ruhe (5 s)", py_cpu_pct=round(idle_py, 1), py_rss_mb=rss_py0, media_rss_mb=rss_md0)

    total_plain = int(a.gb * 1024 ** 3)
    filler = os.urandom(PART)
    peak = {"py": rss_py0, "md": rss_md0}
    stop = threading.Event()

    def sampler():
        while not stop.wait(0.25):
            peak["py"] = max(peak["py"], py.mem())
            peak["md"] = max(peak["md"], md.mem())
    threading.Thread(target=sampler, daemon=True).start()
    c0, m0, t0 = py.cpu(), md.cpu(), time.time()
    fid, statuses = None, set()
    if a.v1:                                        # vorher: 10-MB-Anhaenge durch Python
        n = 0
        rate = a.mbit * 1e6 / 8 if a.mbit else 0
        while n < total_plain:
            if rate and n / rate > time.time() - t0:
                time.sleep(n / rate - (time.time() - t0))
            s, d, _ = req(a.base, "POST", f"/api/chats/{cid}/files", ck, body=filler[:24] + os.urandom(16) + filler + filler[:10 * 1048576 - PART - 40])
            statuses.add(s)
            n += 10 * 1048576
        name = f"Upload v1 {a.gb:g} GB in 10-MB-Anhaengen ({a.mbit:g} Mbit/s)"
    else:
        size = blob_size(total_plain)
        s, d, _ = req(a.base, "POST", f"/api/chats/{cid}/uploads", ck, js={"size": size, "fmt": 2})
        up = json.loads(d)
        statuses.add(s)
        if s != 200:
            raise SystemExit(f"Upload anlegen: {s} {up} (VIDEO_MAX_MB/CHAT_QUOTA_MB hoch genug fuer --gb?)")
        off, hd = 0, header(total_plain)
        rate = a.mbit * 1e6 / 8 if a.mbit else 0
        while off < size:
            if rate and off / rate > time.time() - t0:          # Anschluss-Geschwindigkeit nachbilden (Teil fuer Teil)
                time.sleep(off / rate - (time.time() - t0))
            n = min(PART, size - off)
            body = (hd + filler[:n - 48]) if off == 0 else filler[:n]
            s, d, _ = req(a.base, "PUT", f"/api/chat/uploads/{up['upload']}", ck, body=body,
                          hdr={"Content-Range": f"bytes {off}-{off + n - 1}/{size}"})
            statuses.add(s)
            if s != 200:
                print("PUT:", s, d[:200])
                break
            off += n
        fid = json.loads(d).get("id")
        name = f"Upload v2 {a.gb:g} GB in 8-MiB-Teilen ({a.mbit:g} Mbit/s)" if a.mbit else f"Upload v2 {a.gb:g} GB in 8-MiB-Teilen (unbegrenzt)"
    dt = time.time() - t0
    stop.set()
    cpu_py = (py.cpu() - c0) / dt * 100 - max(0.0, idle_py)
    cpu_md = (md.cpu() - m0) / dt * 100
    gb = total_plain / 1024 ** 3
    row(name, sekunden=round(dt, 1), mb_s=round(total_plain / dt / 1e6), py_cpu_pct=round(cpu_py, 2), py_rss_plus_mb=peak["py"] - rss_py0,
        media_cpu_pct=round(cpu_md, 1), media_rss_mb=peak["md"], py_cpu_s_je_gb=round(((py.cpu() - c0) - max(0.0, idle_py) / 100 * dt) / gb, 2),
        media_cpu_s_je_gb=round((md.cpu() - m0) / gb, 2), status=sorted(statuses), fid=fid)

    if fid:
        s, d, _ = req(a.base, "POST", f"/api/chat/files/{fid}/token", ck)
        tok = json.loads(d).get("token") if s == 200 else None
        for label, h, cookie in (("Sitzung", {}, ck), ("Medien-Token", {"X-Saga-Media-Token": tok or ""}, "")):
            ts = []
            for _ in range(50):
                o = random.randrange(0, blob_size(total_plain) - (1 << 20))
                s, d, ttfb = req(a.base, "GET", f"/api/chat/files/{fid}", cookie, hdr=dict(h, Range=f"bytes={o}-{o + (1 << 20) - 1}"))
                if s == 206 and len(d) == 1 << 20:
                    ts.append(ttfb)
            ts.sort()
            row(f"Range-TTFB 1 MiB v2 ({label})", n=len(ts), p50_ms=round(statistics.median(ts) * 1000, 1) if ts else None,
                p95_ms=round(ts[min(len(ts) - 1, int(0.95 * (len(ts) - 1) + 0.5))] * 1000, 1) if ts else None)
    json.dump({"results": res, "py_pid": a.py_pid, "media_pid": a.media_pid}, open(a.out, "w"), indent=1)

    if a.limits:
        lim = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "limits.json")))["VIDEO"]
        u = res[1]
        checks = [("upload_py_cpu_pct", u["py_cpu_pct"]), ("upload_py_rss_plus_mb", u["py_rss_plus_mb"]),
                  ("upload_media_cpu_pct", u["media_cpu_pct"]), ("upload_media_rss_mb", u["media_rss_mb"])]
        checks += [("range_ttfb_p95_ms", r["p95_ms"] or 1e9) for r in res[2:]]
        bad = 0
        for k, v in checks:
            if k in lim and v > lim[k]:
                print(f"GRENZWERT VIDEO: {k} = {v} > {lim[k]}")
                bad += 1
        if u["status"] != [200] or not u["fid"]:
            print("GRENZWERT VIDEO: Upload nicht vollstaendig")
            bad += 1
        print(f"Grenzwerte VIDEO: {len(checks)} geprueft, {bad} ueberschritten")
        return 1 if bad else 0
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--setup", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--base")
    p.add_argument("--info")
    p.add_argument("--out", default="video.json")
    p.add_argument("--py-pid", type=int, default=0)
    p.add_argument("--media-pid", type=int, default=0)
    p.add_argument("--gb", type=float, default=1.0)
    p.add_argument("--v1", action="store_true")
    p.add_argument("--mbit", type=float, default=400.0, help="Upload-Geschwindigkeit nachbilden (0 = so schnell wie moeglich)")
    p.add_argument("--limits", action="store_true")
    a = p.parse_args()
    if a.setup:
        return setup()
    if a.cleanup:
        return cleanup()
    sys.exit(measure(a))


if __name__ == "__main__":
    main()
