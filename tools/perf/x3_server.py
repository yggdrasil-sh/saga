"""Saga 2.5 - Messung: GET /api/chats/{id}/messages (neueste 50) in einem Chat der Stufe 3 ohne und mit Reaktionen
(saga/chat_x3.py) und mit Bearbeitungen. Konzept-Grenze: p95 mit Reaktionen hoechstens +15 % gegenueber ohne.

Laeuft im Test-Stack (Umgebung wie tests/, Arbeitsverzeichnis tests/):  cd tests && python3 ../tools/perf/x3_server.py
Reaktionen werden fuer die Messung direkt in chat_reaction geschrieben (Form wie echte: B64-ct/sig, Handles), damit
auch 50 Personen je Nachricht ohne 50 Konten gehen - der Server prueft beim Lesen nichts davon. Ausgabe: Zeilen
"PERFX3 ..." und "PERFX3 Grenze ... ok/UEBERSCHRITTEN"; Exit 1 bei Ueberschreitung. Raeumt auf."""
import base64, json, os, secrets, statistics, sys, time

sys.path.insert(0, os.getcwd())
import auth  # noqa: E402
import chat_v3_umstellung as U  # noqa: E402
from db import connect  # noqa: E402

N = int(os.environ.get("N", "60"))
LIMIT = 1.15


def p(xs):
    xs = sorted(xs)
    return round(statistics.median(xs), 1), round(xs[max(0, int(len(xs) * 0.95) - 1)], 1)


def timed(u, path):
    out, size = [], 0
    for _ in range(5):
        u.api("GET", path)                                  # aufwaermen
    for _ in range(N):
        t0 = time.perf_counter()
        s, d = u.api("GET", path)
        out.append((time.perf_counter() - t0) * 1000)
        assert s == 200, (s, d)
        size = len(json.dumps(d))
    return p(out) + (size,)


def b64(n):
    return base64.b64encode(secrets.token_bytes(n)).decode()


def react_rows(cid, per, host):
    with connect() as c, c:
        c.execute("DELETE FROM chat_reaction WHERE msg_seq IN (SELECT seq FROM chat_msg WHERE chat_id=?)", (cid,))
        seqs = [r[0] for r in c.execute("SELECT seq FROM chat_msg WHERE chat_id=? AND kind='msg'", (cid,)).fetchall()]
        now = time.time()
        for s in seqs:
            for i in range(per):
                c.execute("INSERT INTO chat_reaction (msg_seq, reactor, dev, ts, ct, sig, at) VALUES (?,?,?,?,?,?,?)",
                          (s, f"~perf{i:03d}@{host}", secrets.token_hex(16), int(now * 1000) + i, b64(24 + 16 + 20), b64(64), now + i / 1000))
    return len(seqs)


def main():
    U.cleanup()
    prev = auth.settings(fresh=True).get("chat_v3")
    auth.save_setting("chat_v3", True)
    time.sleep(5.6)
    bad = 0
    try:
        A, B = U.P("cu-xa", "Perf A"), U.P("cu-xb", "Perf B")
        ch = A.api("POST", "/api/chats", {"kind": "free", "members": ["cu-xb"]})[1]["chat"]
        ch = A.chat(ch["id"]); chb = B.chat(ch["id"])
        assert ch["v"] == 3 and ch.get("x3_ok") is True, ch
        for i in range(30):
            A.send_v2(ch, f"Nachricht A {i} " + "x" * 40); B.send_v2(chb, f"Nachricht B {i} " + "y" * 40)
        host = A.handle.split("@", 1)[1]
        path = f"/api/chats/{ch['id']}/messages?calls=1&device={A.dev['id']}"
        r = {"ohne Reaktionen": timed(A, path)}
        for per in (3, 5, 20, 50):
            n = react_rows(ch["id"], per, host)
            r[f"{n} Nachrichten mit je {per} Reaktionen"] = timed(A, path)
        base = r["ohne Reaktionen"][1]
        for k, v in r.items():
            print(f"PERFX3 GET messages, {k}: p50 {v[0]} ms, p95 {v[1]} ms, {round(v[2] / 1024, 1)} KB", flush=True)
        for k in ("60 Nachrichten mit je 3 Reaktionen", "60 Nachrichten mit je 5 Reaktionen"):
            if k in r:
                okk = r[k][1] <= base * LIMIT + 1.0             # 1 ms Rauschen absolut
                bad += not okk
                print(f"PERFX3 Grenze p95 +15 % ({k}): {r[k][1]} <= {round(base * LIMIT + 1.0, 1)} ms {'ok' if okk else 'UEBERSCHRITTEN'}", flush=True)
    finally:
        auth.save_setting("chat_v3", bool(prev))
        U.cleanup()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
