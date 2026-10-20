#!/usr/bin/env python3
"""Lasttest der eigenen Push-Verbindung (2.5, saga.push_stream): N gleichzeitige Geraete an /api/push/stream.

Misst am laufenden Server (PID ueber --pid; mit --media zusaetzlich saga-media davor):
  - Speicher (RSS) je Verbindung: Zuwachs nach dem Aufbau von N ruhenden Verbindungen / N
  - CPU im Leerlauf (N Verbindungen, IDLE Sekunden, keiner sendet etwas)
  - CPU und Antwortzeit fuer N Lebenszeichen (je Verbindung ein 0x03 -> 0x04)
  - Zustellung: Testmitteilung an M Geraete (POST .../test), Zeit bis der verschluesselte Rahmen ankommt
Legt Wegwerf-Konto lt-push mit N Geraete-Anmeldungen und Abos an und raeumt danach auf. Aufruf wie die Testreihen
(PYTHONPATH mit tests/pgshim, HK_TEST_BASE):  python3 tools/perf/push_stream.py --n 1000 --pid <saga-pid> [--port 8097 --media-pid <pid>]"""
import argparse
import asyncio
import base64
import json
import os
import secrets
import statistics
import struct
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga.core import _tok_hash  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HZ = os.sysconf("SC_CLK_TCK")
KEYS = {"p256dh": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "auth": "BTBZMqHH6r4Tts7J_aSIgg"}


def rss_kb(pid):
    with open(f"/proc/{pid}/status") as fh:
        return next(int(x.split()[1]) for x in fh if x.startswith("VmRSS"))


def cpu_s(pid):
    with open(f"/proc/{pid}/stat") as fh:
        f = fh.read().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / HZ


def setup(n):
    if not auth.get_user("lt-push"):
        auth.create_user("lt-push", None, "Last Push")
    toks, sids = [], []
    now = time.time()
    with connect() as c, c:
        for i in range(n):
            tok = "hkd_" + secrets.token_urlsafe(32)
            did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES ('lt-push','lt-push',?,?,?,?)",
                            (f"Last {i}", _tok_hash(tok), tok[:8], now)).lastrowid
            sid = c.execute("INSERT INTO push_sub (uid, kind, endpoint, p256dh, auth, name, device_id, created) VALUES "
                            "('lt-push','stream',?,?,?,?,?,?)", (f"stream:{did}", KEYS["p256dh"], KEYS["auth"], f"Last {i}", did, now)).lastrowid
            toks.append(tok)
            sids.append(sid)
    return toks, sids


def teardown():
    with connect() as c, c:
        c.execute("DELETE FROM push_sub WHERE uid='lt-push'")
        c.execute("DELETE FROM push_queue WHERE uid='lt-push'")
        c.execute("DELETE FROM device_login WHERE uid='lt-push'")
    from saga.userdata import delete_user_data
    delete_user_data("lt-push")


def mask(payload, opcode=2):
    k = os.urandom(4)
    head = bytes([0x80 | opcode, 0x80 | len(payload)]) + k        # nur kurze Rahmen (< 126 Byte)
    return head + bytes(b ^ k[i % 4] for i, b in enumerate(payload))


class Dev:
    __slots__ = ("r", "w", "pong", "msg", "t_sent")

    def __init__(self, r, w):
        self.r, self.w, self.pong, self.msg, self.t_sent = r, w, None, None, 0.0


async def read_frame(r):
    h = await r.readexactly(2)
    ln = h[1] & 0x7F
    if ln == 126:
        ln = struct.unpack("!H", await r.readexactly(2))[0]
    elif ln == 127:
        ln = struct.unpack("!Q", await r.readexactly(8))[0]
    return h[0] & 0x0F, await r.readexactly(ln)


async def open_dev(host, port, tok):
    r, w = await asyncio.open_connection(host, port)
    key = base64.b64encode(os.urandom(16)).decode()
    w.write((f"GET /api/push/stream HTTP/1.1\r\nHost: {B_.hostname}:{B_.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nAuthorization: Bearer {tok}\r\n\r\n").encode())
    await w.drain()
    head = await r.readuntil(b"\r\n\r\n")
    if not head.startswith(b"HTTP/1.1 101"):
        raise RuntimeError(head[:60])
    return Dev(r, w)


async def reader(d):
    try:
        while True:
            op, data = await read_frame(d.r)
            if op == 8:
                return
            if op == 2 and data[:1] == b"\x04" and d.pong:
                d.pong.set_result(time.perf_counter() - d.t_sent)
            elif op == 2 and data[:1] == b"\x01" and d.msg and not d.msg.done():
                d.msg.set_result((time.perf_counter(), struct.unpack("!Q", data[1:9])[0]))
    except (asyncio.IncompleteReadError, ConnectionError):
        return


def test_push(sid, tok):
    import http.client
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    c.request("POST", f"/api/push/subscriptions/{sid}/test", headers={"Authorization": "Bearer " + tok, "Host": f"{B_.hostname}:{B_.port}"})
    r = c.getresponse(); r.read(); c.close()
    return r.status


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--port", type=int, default=B_.port)
    ap.add_argument("--media-pid", type=int, default=0)
    ap.add_argument("--idle", type=float, default=60)
    ap.add_argument("--deliver", type=int, default=200)
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    import socket
    for _ in range(120):                          # Server noch beim Start?
        try:
            socket.create_connection(("127.0.0.1", a.port), 1).close()
            break
        except OSError:
            await asyncio.sleep(1)
    toks, sids = await asyncio.to_thread(setup, a.n)
    pids = [("saga", a.pid)] + ([("saga-media", a.media_pid)] if a.media_pid else [])
    out = {"label": a.label, "n": a.n}
    try:
        await asyncio.sleep(2)
        base = {nm: rss_kb(p) for nm, p in pids}
        t0 = time.perf_counter()
        devs = []
        for i in range(0, a.n, 50):
            devs += await asyncio.gather(*[open_dev("127.0.0.1", a.port, tok) for tok in toks[i:i + 50]])
        out["connect_s"] = round(time.perf_counter() - t0, 2)
        tasks = [asyncio.create_task(reader(d)) for d in devs]
        await asyncio.sleep(3)
        conn = {nm: rss_kb(p) for nm, p in pids}
        for nm, _ in pids:
            out[f"{nm}_rss_base_mb"] = round(base[nm] / 1024, 1)
            out[f"{nm}_rss_conn_mb"] = round(conn[nm] / 1024, 1)
            out[f"{nm}_kb_per_conn"] = round((conn[nm] - base[nm]) / a.n, 2)
        c0 = {nm: cpu_s(p) for nm, p in pids}
        await asyncio.sleep(a.idle)
        for nm, p in pids:
            out[f"{nm}_idle_cpu_pct"] = round((cpu_s(p) - c0[nm]) / a.idle * 100, 3)
        # Lebenszeichen: alle N je einmal
        loop = asyncio.get_running_loop()
        c0 = {nm: cpu_s(p) for nm, p in pids}
        for d in devs:
            d.pong = loop.create_future()
            d.t_sent = time.perf_counter()
            d.w.write(mask(b"\x03"))
        lat = await asyncio.wait_for(asyncio.gather(*[d.pong for d in devs]), 60)
        await asyncio.sleep(0.5)
        for nm, p in pids:
            out[f"{nm}_cpu_ms_per_1000_pings"] = round((cpu_s(p) - c0[nm]) * 1000 / a.n * 1000, 1)
        lat = sorted(lat)
        out["ping_ms_p50"] = round(statistics.median(lat) * 1000, 1)
        out["ping_ms_p95"] = round(lat[int(len(lat) * 0.95) - 1] * 1000, 1)
        # Zustellung an M Geraete (Testmitteilung, sofort)
        m = min(a.deliver, a.n)
        for d in devs[:m]:
            d.msg = loop.create_future()
        t_req = {}

        async def one(i):
            t_req[i] = time.perf_counter()
            return await asyncio.to_thread(test_push, sids[i], toks[i])
        sts = await asyncio.gather(*[one(i) for i in range(m)])
        got = await asyncio.wait_for(asyncio.gather(*[devs[i].msg for i in range(m)]), 60)
        dl = sorted(got[i][0] - t_req[i] for i in range(m))
        out["deliver_ok"] = sum(1 for s in sts if s == 200)
        out["deliver_ms_p50"] = round(statistics.median(dl) * 1000, 1)
        out["deliver_ms_p95"] = round(dl[int(len(dl) * 0.95) - 1] * 1000, 1)
        # bestaetigen (raeumt die Ablage auf)
        for i in range(m):
            devs[i].w.write(mask(b"\x02" + struct.pack("!Q", got[i][1])))
        await asyncio.sleep(1)
        for d in devs:
            d.w.close()
        for tk in tasks:
            tk.cancel()
        await asyncio.sleep(2)
        for nm, p in pids:
            out[f"{nm}_rss_after_close_mb"] = round(rss_kb(p) / 1024, 1)
    finally:
        await asyncio.to_thread(teardown)
    print("PERF " + json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
