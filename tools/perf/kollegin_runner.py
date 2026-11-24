"""Messung "Kollegin: Runner-Schritt" (Saga 2.6 K-A6, Bugs-DB 2266): EIN Schritt des Runners ai.task (saga/ai_task.py)
ueber jobs.run_once im Prozess gegen echtes Postgres - Takt-Abfrage, Vorpruefung, Beanspruchen, Plan-Hash, JobTok,
Hintergrund-Spur, Einheiten belegen/abrechnen (mit Protokoll), Werkzeug create_task samt Stempel, Abschluss.
Ohne Modell (der Planer kommt mit K-B). Testuhr angehalten in der Zukunft; je Messung ein neuer Auftrag mit einem Schritt.

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin_runner.py [--n 40] [--stage S]
    (saga-test: tools/testumgebung/umgebung.sh lauf server <slot> $PWD/tools/perf/kollegin_runner.py)
Gemessen: p50/p95/max je Schritt, DB-Abfragen je Schritt (INFO). Grenzwert: limits.json Stufe S/M, Name
"Kollegin: Runner-Schritt". Wegwerf-Konto kr-perf; raeumt auf, stellt KI-Einstellung und Testuhr wieder her."""
import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Kollegin: Runner-Schritt (create_task)"
T0 = 1893456000.0
UID = "kr-perf"


def limit(stage="S"):
    lim = json.load(open(os.path.join(HERE, "limits.json"))).get(stage, {})
    pre = max((p for p in lim if NAME.startswith(p)), key=len, default=None)
    return lim.get(pre) if pre else None


def measure(n):
    import db
    from saga import ai_task, jobs
    times, queries = [], []
    orig = db.Conn.execute
    cnt = {"n": 0}

    def ex(conn, sql, args=()):
        cnt["n"] += 1
        return orig(conn, sql, args)
    for i in range(n + 3):                        # 3 Aufwaermlaeufe
        jid = jobs.create(UID, ai_task.KIND, title="Perf", steps=[{"tool": "create_task", "args": {"text": f"Perf {i}"}}])
        s = jobs.state_of(jid)
        jobs.approve(jid, UID, s["plan_v"], s["plan_hash"])
        cnt["n"] = 0
        db.Conn.execute = ex
        try:
            t0 = time.perf_counter()
            k = jobs.run_once()
            dt = time.perf_counter() - t0
        finally:
            db.Conn.execute = orig
        st = jobs.state_of(jid)
        assert k == 1 and st["state"] == "erledigt", (k, st)
        if i >= 3:
            times.append(dt)
            queries.append(cnt["n"])
    times.sort()
    q = lambda p: times[min(len(times) - 1, int(round(p * (len(times) - 1))))]  # noqa: E731
    return {"name": NAME, "group": "kollegin", "n": n, "p50_ms": round(statistics.median(times) * 1000, 2),
            "p95_ms": round(q(0.95) * 1000, 2), "max_ms": round(times[-1] * 1000, 2), "queries_per_step": max(queries)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--stage", default="S")
    a = ap.parse_args()
    import db
    db.wait_ready()
    import auth
    from saga import testclock
    from saga.userdata import delete_user_data
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    try:
        if auth.get_user(UID):
            delete_user_data(UID, drop_user=True)
        auth.create_user(UID, "Kr-Perf-Pass-12345", UID)
        auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="perf", groups=[], caps={},
                                     quota=0))
        auth.settings(fresh=True)
        testclock.set_clock({"freeze": True})
        testclock.set_clock({"set": T0})
        r = measure(a.n)
        lim = limit(a.stage)
        bad = lim is not None and r["p95_ms"] > lim
        print(("INFO" if lim is None else "FAIL" if bad else "OK  "), f"{NAME}: p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms "
              f"(Grenze {a.stage}: {lim}), max {r['max_ms']}, DB-Abfragen je Schritt {r['queries_per_step']}", flush=True)
        print("PERF " + json.dumps(dict(r, stufe=a.stage, limit=lim), ensure_ascii=False), flush=True)
        print("Kollegin-Runner-Messung", "- ALLES GRUEN" if not bad else "- FEHLER")
        return 1 if bad else 0
    finally:
        testclock.set_clock({"reset": True})
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        if auth.get_user(UID):
            delete_user_data(UID, drop_user=True)


if __name__ == "__main__":
    sys.exit(main())
