"""Lastmessung "Kollegin: Plan pruefen" (2.6, Bugs-DB 2266 K-A3, Konzept Zahlentabelle): saga.tools.check_plan fuer einen
Plan mit 5 Schritten (2 lesen, 3 schreiben) - Registry, Bereiche, interner Tok, Konto/Faehigkeit, Rolle und Seitenbaum je
Ziel. Im Prozess gegen die Datenbank aus DATABASE_URL, ohne Server und ohne Modell. Grenzwert: limits.json (S/M/L,
Praefix "Kollegin: Plan pruefen").

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin_plan.py [--n 50] [--stage S]

Ohne Argumente: Wegwerf-Konto kp-perf mit 2 Seiten, einer Datenbank und einer Zeile, KI-Einstellung kurz an (danach wie
vorher), Ergebnis als JSON-Zeile "PERF {...}" + Grenzwert-Pruefung (Ende-Code 1 bei Ueberschreitung).
tests/kollegin_werkzeuge.py nutzt measure() mit eigenen Seiten."""
import json
import os
import statistics
import sys
import time

NAME = "Kollegin: Plan pruefen (5 Schritte)"
HERE = os.path.dirname(os.path.abspath(__file__))


def plan(read_id, db_id, write_id, row_id, rev):
    return [{"tool": "get_page", "args": {"id": read_id}},
            {"tool": "query_database", "args": {"id": db_id}},
            {"tool": "append_to_page", "args": {"id": write_id, "markdown": "- Grillkohle besorgt"}},
            {"tool": "update_row", "args": {"id": row_id, "rev": rev, "properties": {"Erledigt": True}}},
            {"tool": "create_task", "args": {"text": "Grillkohle abholen"}}]


def measure(uid, steps, reads, n=50, job_id=900001):
    """check_plan n-mal (nach 3 Aufwaermlaeufen) -> Ergebnis wie bench.py (p50/p95/max in ms) + Probleme des letzten Laufs."""
    from saga import tools
    for _ in range(3):
        tools.check_plan(uid, job_id, steps, reads)
    times, probs = [], None
    for _ in range(n):
        t0 = time.perf_counter()
        _tok, probs = tools.check_plan(uid, job_id, steps, reads)
        times.append(time.perf_counter() - t0)
    times.sort()
    q = lambda p: times[min(len(times) - 1, int(round(p * (len(times) - 1))))]  # noqa: E731
    return {"name": NAME, "group": "kollegin", "n": n, "p50_ms": round(statistics.median(times) * 1000, 2),
            "p95_ms": round(q(0.95) * 1000, 2), "max_ms": round(times[-1] * 1000, 2), "problems": probs}


def limit(stage="S"):
    lim = json.load(open(os.path.join(HERE, "limits.json"))).get(stage, {})
    pre = max((p for p in lim if NAME.startswith(p)), key=len, default=None)
    return lim.get(pre) if pre else None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--stage", default="S")
    args = ap.parse_args()
    import db
    db.wait_ready()
    import auth
    from saga import api_struct, api_v1, apitok
    from saga.userdata import delete_user_data
    uid = "kp-perf"
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    try:
        if not auth.get_user(uid):
            auth.create_user(uid, "Kp-Perf-Pass-12345", uid)
        auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:18290/v1", model="fake"))
        auth.settings(fresh=True)
        full = apitok.Tok(10 ** 9, uid, "perf", "sga_perf", apitok.SCOPES, None, None)
        p_read = api_v1.page_create(full, {"title": "Sommerfest Einkauf", "markdown": "Kohle, Wurst"})[1]["id"]
        p_write = api_v1.page_create(full, {"title": "Sommerfest", "markdown": "Plan"})[1]["id"]
        dbid = api_struct.database_create(full, {"title": "Mitbringliste", "parent_id": p_write,
                                                 "properties": [{"name": "Erledigt", "type": "checkbox"}]})[1]["id"]
        row = api_v1.row_create(full, dbid, {"title": "Grillkohle"})[1]["id"]
        rev = api_v1.page_read(full, row)[1]["rev"]
        r = measure(uid, plan(p_read, dbid, p_write, row, rev), [p_read], args.n)
        ms = limit(args.stage)
        print("PERF " + json.dumps(r, ensure_ascii=False))
        bad = bool(r["problems"]) or (ms is not None and r["p95_ms"] > ms)
        print(f"{NAME}: p50 {r['p50_ms']} ms, p95 {r['p95_ms']} ms (Grenze {args.stage}: {ms} ms), Probleme {r['problems']}"
              + (" - GRENZWERT" if bad else " - ok"))
        return 1 if bad else 0
    finally:
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        if auth.get_user(uid):
            delete_user_data(uid, drop_user=True)


if __name__ == "__main__":
    sys.exit(main())
