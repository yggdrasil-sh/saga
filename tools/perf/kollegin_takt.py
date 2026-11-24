"""Messung "Kollegin: Takt 1000 wartende Auftraege" (Saga 2.6 K-A2, Bugs-DB 2266): saga/jobs.run_once im Prozess gegen echtes
Postgres (wie tools/perf/kollegin.py, nicht ueber bench.py/HTTP).

Stufe S: 1.000 wartende Auftraege (laeuft/wartet_ereignis, je ein job_wait) von 200 Konten.
Stufe M: dazu 30.000 beendete Auftraege (Verlauf, je 4 Protokollzeilen) und 2.000 nicht faellige (wartet_ok/wartet_dich).
Gemessen (Testuhr angehalten, Zeit als Parameter):
  - Takt im Leerlauf (nichts faellig) p50/p95/max -> Grenzwert limits.json Stufe S/M "Kollegin: Takt 1000 wartende Auftraege"
  - DB-Abfragen je Leerlauf-Takt (Zaehler auf db.Conn.execute) -> hoechstens 1 (harte Grenze dieser Messung)
  - EXPLAIN (FORMAT JSON) der Takt-Abfrage (jobs._CLAIM, ohne Ausfuehrung): nutzt den Teilindex job_due (K-A6, harte
    Grenze in S und M)
  - INFO: Takt mit 20 faelligen (signal vorher, je ein Warte-Schritt, der danach wieder wartet), signal() mit 1.000 Wartenden
    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin_takt.py [--stufe SM] [--n 200]
    (saga-test: tools/testumgebung/umgebung.sh lauf server <slot> $PWD/tools/perf/kollegin_takt.py)
Wegwerf-Konten kt-*; raeumt auf, stellt KI-Einstellung und Testuhr wieder her. Exit 1, wenn eine Grenze ueberschritten ist."""
import argparse
import json
import os
import statistics
import sys
import time

import auth
import db
from db import connect
from saga import jobs, testclock

NAME = "Kollegin: Takt 1000 wartende Auftraege"
HERE = os.path.dirname(os.path.abspath(__file__))
T0 = 1893456000.0
USERS = 200


def p95(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def ms(x):
    return round(x * 1000, 2)


def run_perf(ctx, job, step):
    """Wartet auf perf_done/<job>; nach jedem Treffer wieder (gleichbleibende Last)."""
    return jobs.Outcome.wait("perf_done", f"job:{ctx.job_id}", T0 + 30 * 86400)


def gen(stufe):
    with connect() as c, c:
        c.executemany("INSERT INTO users (username, display_name, created) VALUES (?, ?, ?) ON CONFLICT (username) DO NOTHING",
                      [(f"kt-{i}", f"kt {i}", T0) for i in range(USERS)])
        ins = ("INSERT INTO job (uid, kind, source, state, sub, title, plan, plan_v, plan_hash, next_at, created, updated, ended_at, "
               "actor_kind, actor, on_behalf_of) SELECT 'kt-' || (g % {u}), 'perf.wait', 'saga', {state}, {sub}, 'Perf ' || g, '{{}}'::jsonb, 1, "
               "md5(g::text), {next}, {t0}, {t0}, {ended}, 'service', 'svc:kollegin', 'kt-' || (g % {u}) FROM generate_series(1, {n}) g")
        if stufe == "M":                          # Verlauf zuerst (aeltere ids), dazu nicht faellige
            c.execute(ins.format(u=USERS, state="'erledigt'", sub="NULL", next="NULL", t0=T0 - 86400, ended=T0 - 3600, n=30000))
            c.execute("INSERT INTO job_event (job_id, at, kind, data) SELECT j.id, j.created, 'state', '{}'::jsonb FROM job j, "
                      "generate_series(1, 4) k WHERE j.uid LIKE 'kt-%'")
            c.execute(ins.format(u=USERS, state="(CASE WHEN g % 2 = 0 THEN 'wartet_ok' ELSE 'wartet_dich' END)", sub="NULL", next="NULL",
                                 t0=T0, ended="NULL", n=2000))
        c.execute(ins.format(u=USERS, state="'laeuft'", sub="'wartet_ereignis'", next=f"{T0 + 7 * 86400} + g", t0=T0, ended="NULL",
                             n=1000))
        c.execute("INSERT INTO job_step (job_id, n, tool, klass, state, idem_key) SELECT id, 0, 'wait', 'read', 'wartet', "
                  "id || ':0:1' FROM job WHERE uid LIKE 'kt-%' AND state='laeuft'")
        c.execute("INSERT INTO job_wait (job_id, step_n, on_kind, ref, until) SELECT id, 0, 'perf_done', 'job:' || id, next_at "
                  "FROM job WHERE uid LIKE 'kt-%' AND state='laeuft'")
        c.execute("ANALYZE job")
        c.execute("ANALYZE job_wait")
        return [r[0] for r in c.execute("SELECT id FROM job WHERE uid LIKE 'kt-%' AND state='laeuft' ORDER BY id").fetchall()]


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM job WHERE uid LIKE 'kt-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kt-%'")


class Count:
    def __enter__(self):
        self.n, self.orig = 0, db.Conn.execute
        me = self

        def ex(conn, sql, args=()):
            me.n += 1
            return me.orig(conn, sql, args)
        db.Conn.execute = ex
        return self

    def __exit__(self, *a):
        db.Conn.execute = self.orig


def explain():
    """Plan der Takt-Abfrage (nicht ausgefuehrt) -> (nutzt job_due?, Knoten-Typen)."""
    t = testclock.now()
    with connect() as c:
        r = c.execute("EXPLAIN (FORMAT JSON) " + jobs._CLAIM, (t + jobs.LEASE, t + jobs.LEASE, t, 20)).fetchone()[0]
    txt = r if isinstance(r, str) else json.dumps(r)
    nodes = []

    def walk(x):
        if isinstance(x, dict):
            if "Node Type" in x:
                nodes.append(x["Node Type"] + (f" {x['Index Name']}" if x.get("Index Name") else ""))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(json.loads(txt))
    return '"job_due"' in txt, nodes


def measure(ids, n):
    for _ in range(5):                            # warm (Pool, Plan-Cache)
        jobs.run_once()
    idle = []
    for _ in range(n):
        t0 = time.perf_counter()
        k = jobs.run_once()
        idle.append(time.perf_counter() - t0)
        assert k == 0, "im Leerlauf faellig?"
    with Count() as cnt:
        for _ in range(20):
            jobs.run_once()
    q_per_tick = cnt.n / 20
    sig, due = [], []
    for r in range(10):                           # 20 faellige je Runde
        batch = ids[r * 20:(r + 1) * 20]
        for jid in batch:
            with connect() as c, c:
                t0 = time.perf_counter()
                jobs.signal(c, "perf_done", f"job:{jid}")
                sig.append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        k = jobs.run_once(limit=20)
        due.append(time.perf_counter() - t0)
        assert k == 20, f"faellig: {k}"
    uses, nodes = explain()
    return {"name": NAME, "n": n, "p50_ms": ms(statistics.median(idle)), "p95_ms": ms(p95(idle)), "max_ms": ms(max(idle)),
            "queries_per_idle_tick": q_per_tick, "uses_job_due": uses, "plan": nodes, "due20_p50_ms": ms(statistics.median(due)), "due20_p95_ms": ms(p95(due)),
            "signal_p95_ms": ms(p95(sig))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", default="SM")
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()
    with open(os.path.join(HERE, "limits.json"), encoding="utf-8") as f:
        limits = json.load(f)
    db.wait_ready()
    jobs.register("perf.wait", run_perf)
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="perf", groups=[], caps={}))
    auth.settings(fresh=True)
    testclock.set_clock({"freeze": True})
    testclock.set_clock({"set": T0})
    fail = 0
    try:
        for st in [x for x in a.stufe if x in "SM"]:
            cleanup()
            try:
                ids = gen(st)
                r = measure(ids, a.n)
            finally:
                cleanup()
            lim = limits.get(st, {}).get(NAME)
            good = (lim is None or r["p95_ms"] <= lim) and r["queries_per_idle_tick"] <= 1 and r["uses_job_due"]
            fail |= not good
            print(("INFO" if lim is None else "OK  " if good else "FAIL"), f"Stufe {st}: {NAME} p95 {r['p95_ms']} ms (Grenze {lim}), "
                  f"p50 {r['p50_ms']}, max {r['max_ms']}, n={r['n']}; DB-Abfragen je Leerlauf-Takt {r['queries_per_idle_tick']} (Grenze 1)",
                  flush=True)
            print(("OK  " if r["uses_job_due"] else "FAIL"), f"Stufe {st}: Takt-Abfrage nutzt Index job_due: "
                  f"{r['uses_job_due']} (Plan {' > '.join(r['plan'])})", flush=True)
            print(f"INFO Stufe {st}: Takt mit 20 faelligen p50 {r['due20_p50_ms']} / p95 {r['due20_p95_ms']} ms, "
                  f"signal() bei 1.000 Wartenden p95 {r['signal_p95_ms']} ms", flush=True)
            print("PERF " + json.dumps(dict(r, stufe=st, limit=lim)), flush=True)
    finally:
        auth.save_setting("ai", prev)
        testclock.set_clock({"reset": True})
    print("Kollegin-Takt-Messung", "- ALLES GRUEN" if not fail else "- FEHLER")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
