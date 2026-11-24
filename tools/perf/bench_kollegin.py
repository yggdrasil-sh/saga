"""Sammel-Messung Kollegin (Saga 2.6, Bugs-DB 2266 K-A6): alle Messungen der Kollegin gegen tools/perf/limits.json in
einem Lauf - je Messung ein eigener Prozess (jede raeumt selbst auf, stellt Einstellungen und Testuhr wieder her):

  Takt         tools/perf/kollegin_takt.py   "Kollegin: Takt 1000 wartende Auftraege" (Stufe S, M; mit EXPLAIN job_due)
  Plan pruefen tools/perf/kollegin_plan.py   "Kollegin: Plan pruefen (5 Schritte)"
  Kontext      tools/perf/kollegin.py        "Kollegin: Kontext-Auswahl" (Stufe S, M)
  Runner       tools/perf/kollegin_runner.py "Kollegin: Runner-Schritt (create_task)"

    PYTHONPATH=tests/pgshim:. python tools/perf/bench_kollegin.py [--stufe SM] [--nur takt,plan,kontext,runner]
    (saga-test: tools/testumgebung/umgebung.sh lauf server <slot> $PWD/tools/perf/bench_kollegin.py)
Ausgabe: die Zeilen der Messungen, dann je Messung eine Zeile OK/FAIL mit p95 und Grenze, "PERF {...}" je Ergebnis und
"BENCH-KOLLEGIN ... - ALLES GRUEN" bzw. "- FEHLER" (Ende-Code 1, wenn eine Messung scheitert)."""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def plan(stufe):
    out = []
    for st in stufe:
        out.append(("takt", st, ["kollegin_takt.py", "--stufe", st]))
    out.append(("plan", "S", ["kollegin_plan.py", "--stage", "S"]))
    for st in stufe:
        out.append(("kontext", st, ["kollegin.py", "--stufe", st]))
    out.append(("runner", "S", ["kollegin_runner.py", "--stage", "S"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", default="SM")
    ap.add_argument("--nur", default="takt,plan,kontext,runner")
    a = ap.parse_args()
    want = set(a.nur.split(","))
    stufe = [x for x in a.stufe if x in "SM"]
    rows, fail = [], 0
    for what, st, cmd in plan(stufe):
        if what not in want:
            continue
        p = subprocess.run([sys.executable, os.path.join(HERE, cmd[0]), *cmd[1:]], capture_output=True, text=True, timeout=3600)
        out = p.stdout + p.stderr
        print(out.rstrip(), flush=True)
        perf = [json.loads(x[5:]) for x in out.splitlines() if x.startswith("PERF ")]
        good = p.returncode == 0 and bool(perf)
        fail |= not good
        for r in perf:
            rows.append((what, r.get("stufe") or st, r.get("name"), r.get("p95_ms"), r.get("limit"), good))
        if not perf:
            rows.append((what, st, cmd[0], None, None, False))
    print("\n== Kollegin-Messungen (p95 ms / Grenze)")
    for what, st, name, p95, lim, good in rows:
        print(("OK  " if good else "FAIL"), f"{what:8} Stufe {st}: {name}: p95 {p95} ms (Grenze {lim})", flush=True)
    print(f"BENCH-KOLLEGIN {len(rows)} Messungen", "- ALLES GRUEN" if rows and not fail else "- FEHLER")
    return 1 if fail or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
