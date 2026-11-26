"""Messung Tageszeile (Saga 2.6 K-D, Bugs-DB 2271): "Kollegin: Tageszeile" = saga.ai_today.compute im Prozess (Fakten aus
Eingang + Aufgabenquellen, Kalender aus dem Speicher, Bausteine) und "Kollegin: GET /api/ai/today" ueber HTTP (HK_TEST_BASE).

  Stufe S: 200 offene Aufgaben im Eingang + 5 Aufgabenquellen mit je 500 offenen Zeilen
  Stufe M: 200 offene Aufgaben + 20 Aufgabenquellen mit je 500 offenen Zeilen

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin_heute.py [--stage S|M] [--n 40] [--ohne-http]
Gemessen: p50/p95/max, DB-Abfragen je Berechnung (INFO), dazu Leerlauf: 0 DB-Abfragen in 2 s ohne Anfrage (hart).
Grenzwerte: limits.json Stufe S/M/L. Wegwerf-Konto kh-perf; raeumt auf, stellt die KI-Einstellung wieder her."""
import argparse
import base64
import http.client
import json
import os
import statistics
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
NAMES = ("Kollegin: Tageszeile", "Kollegin: GET /api/ai/today")
UID = "kh-perf"
SIZES = {"S": (200, 5, 500), "M": (200, 20, 500)}


def limit(name, stage):
    lim = json.load(open(os.path.join(HERE, "limits.json"))).get(stage, {})
    pre = max((p for p in lim if name.startswith(p)), key=len, default=None)
    return lim.get(pre) if pre else None


def stats(name, times, **extra):
    times = sorted(times)
    q = lambda p: times[min(len(times) - 1, int(round(p * (len(times) - 1))))]  # noqa: E731
    return dict({"name": name, "group": "kollegin", "n": len(times), "p50_ms": round(statistics.median(times) * 1000, 2),
                 "p95_ms": round(q(0.95) * 1000, 2), "max_ms": round(times[-1] * 1000, 2)}, **extra)


def seed(stage):
    from datetime import date, timedelta
    from db import connect
    n_tasks, n_src, n_rows = SIZES[stage]
    d0 = date.today()
    sch = json.dumps({"props": [{"id": "ok", "name": "Erledigt", "type": "checkbox"}, {"id": "d", "name": "Datum", "type": "date"}],
                      "views": [{"id": "a", "name": "A", "type": "table"}], "tasks": {"done": "ok", "due": "d"}})
    now = time.time()
    with connect() as c, c:
        c.executemany("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, ?, ?, ?, 0, ?)",
                      [(UID, f"Aufgabe {i}", (d0 + timedelta(days=(i % 9) - 4)).isoformat() if i % 3 else None, 1 + i % 3, now + i)
                       for i in range(n_tasks)])
        for s in range(n_src):
            db_id = c.execute("INSERT INTO note (uid, title, kind, schema, created, updated) VALUES (?, ?, 'database', ?, ?, ?) RETURNING id",
                              (UID, f"Quelle {s}", sch, now, now)).fetchone()[0]
            c.executemany("INSERT INTO note (uid, title, parent_id, props, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
                          [(UID, f"Zeile {s}-{i}", db_id, json.dumps({"d": (d0 + timedelta(days=(i % 11) - 5)).isoformat()}), now + i, now + i)
                           for i in range(n_rows)])
            c.execute("INSERT INTO task_source (uid, db_id, scope, added) VALUES (?, ?, 'all', ?)", (UID, db_id, now + s))
        c.execute("INSERT INTO job (uid, kind, source, state, title, created, updated) VALUES (?, 'ai.task', 'saga', 'wartet_ok', 'Perf', ?, ?)",
                  (UID, now, now))


def measure_compute(n):
    import db
    from saga import ai_today
    orig, cnt, times, queries = db.Conn.execute, {"n": 0}, [], []

    def ex(conn, sql, args=()):
        cnt["n"] += 1
        return orig(conn, sql, args)
    for i in range(n + 3):
        cnt["n"] = 0
        db.Conn.execute = ex
        try:
            t0 = time.perf_counter()
            d = ai_today.compute(UID, "Europe/Berlin", "de")
            dt = time.perf_counter() - t0
        finally:
            db.Conn.execute = orig
        assert d.get("enabled") and d.get("line"), d
        if i >= 3:
            times.append(dt)
            queries.append(cnt["n"])
    idle = {"n": 0}

    def ex2(conn, sql, args=()):
        idle["n"] += 1
        return orig(conn, sql, args)
    db.Conn.execute = ex2
    try:
        time.sleep(2.0)
    finally:
        db.Conn.execute = orig
    return stats(NAMES[0], times, queries=max(queries)), idle["n"]


def measure_http(n):
    from itsdangerous import TimestampSigner
    base = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8920"))
    host = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
    ck = TimestampSigner(os.environ["SESSION_SECRET"]).sign(
        base64.b64encode(json.dumps({"uid": UID, "name": UID, "pwc": time.time() + 5}).encode())).decode()
    times = []
    conn = http.client.HTTPConnection(base.hostname, base.port, timeout=30)
    for i in range(n + 3):
        t0 = time.perf_counter()
        conn.request("GET", "/api/ai/today?tz=Europe/Berlin", headers={"Host": host, "Cookie": "saga=" + ck, "X-Saga": "1",
                                                                       "Accept-Language": "de"})
        r = conn.getresponse()
        body = r.read()
        dt = time.perf_counter() - t0
        assert r.status == 200 and json.loads(body).get("enabled"), (r.status, body[:200])
        if i >= 3:
            times.append(dt)
    return stats(NAMES[1], times)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--stage", default="S")
    ap.add_argument("--ohne-http", action="store_true")
    ap.add_argument("--profil", action="store_true", help="cProfile von 10 Berechnungen ausgeben (nur Analyse)")
    a = ap.parse_args()
    import db
    db.wait_ready()
    import auth
    from saga.userdata import delete_user_data
    from db import connect
    prev = dict(auth.settings(fresh=True).get("ai") or {})

    def clean():
        if auth.get_user(UID):
            delete_user_data(UID, drop_user=True)
        with connect() as c, c:
            for t_ in ("task", "note", "task_source", "job", "user_kv"):
                c.execute(f"DELETE FROM {t_} WHERE uid=?", (UID,))
    bad = False
    try:
        clean()
        auth.create_user(UID, "Kh-Perf-Pass-12345", "Anna")
        auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="perf", groups=[], caps={}))
        auth.settings(fresh=True)
        seed(a.stage)
        if a.profil:
            import cProfile
            import pstats
            from saga import ai_today
            ai_today.compute(UID, "Europe/Berlin", "de")
            pr = cProfile.Profile()
            pr.enable()
            for _ in range(10):
                ai_today.compute(UID, "Europe/Berlin", "de")
            pr.disable()
            pstats.Stats(pr).sort_stats("cumulative").print_stats(25)
            return 0
        r, idle = measure_compute(a.n)
        out = [r]
        if not a.ohne_http:
            time.sleep(5.6)                                   # Einstellungen sind im Server 5 s zwischengespeichert
            out.append(measure_http(a.n))
        for x in out:
            lim = limit(x["name"], a.stage)
            fail = lim is not None and x["p95_ms"] > lim
            bad |= fail
            print(("INFO" if lim is None else "FAIL" if fail else "OK  "), f"{x['name']} (Stufe {a.stage}): p50 {x['p50_ms']} ms, "
                  f"p95 {x['p95_ms']} ms (Grenze {lim}), max {x['max_ms']}" + (f", DB-Abfragen {x['queries']}" if "queries" in x else ""), flush=True)
            print("PERF " + json.dumps(dict(x, stufe=a.stage, limit=lim), ensure_ascii=False), flush=True)
        print(("OK  " if idle == 0 else "FAIL"), f"Leerlauf: {idle} DB-Abfragen in 2 s ohne Anfrage (Grenze 0)", flush=True)
        bad |= idle != 0
        print("Kollegin-Tageszeile-Messung", "- ALLES GRUEN" if not bad else "- FEHLER")
        return 1 if bad else 0
    finally:
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        clean()


if __name__ == "__main__":
    sys.exit(main())
