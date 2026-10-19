#!/bin/bash
# Saga-Lastmessung mit Grenzwerten. Laeuft AUF dem Docker-Host im Repo-Verzeichnis (wie tools/stack-remote.sh) -
# vom Mac/Laptop aus z. B. ueber tools/test-stack.sh:
#   SAGA_SSH=root@proxmox SAGA_PCT=143 SAGA_DIR=/root/saga-perf SAGA_PORT=8170 SAGA_SCRIPT=perf/run.sh tools/test-stack.sh "S M"
# Argument: Stufen (Standard "S"; S/M/L siehe tools/perf/gen.py). Pro Stufe: Testdaten erzeugen (Konten perf-a/perf-b,
# werden vorher geloescht), Endpunkte messen (bench.py), EXPLAIN der teuersten Abfragen (explain.py), langsame Abfragen
# aus dem Postgres-Log (> 25 ms), Grenzwerte aus tools/perf/limits.json pruefen. Ergebnis: perf-out/<stufe>.json/.txt.
# Exit 1, wenn ein Grenzwert ueberschritten ist. SAGA_PERF_KEEP=1: Testkonten danach behalten. SAGA_PERF_N: Wiederholungen.
# SAGA_PERF_WEB=1: zusaetzlich Weboberflaeche (Playwright-Image, headless; SAGA_PERF_WEB_ARGS z. B. "--db-timeout 180"). SAGA_PERF_ZIEL=1: gegen die 2.2-Ziele (ziel_*) pruefen.
# NUR auf Test-Stacks: legt NOTE_MAX_COUNT=200000 in die .env (sonst scheitert schon Stufe M am Seiten-Limit).
set -e
cd "$(dirname "$0")/../.."
STUFEN=${*:-S}                                  # test-stack.sh reicht "S M" als zwei Argumente weiter
N=${SAGA_PERF_N:-10}
if [ -f .env ] && ! grep -q '^NOTE_MAX_COUNT=' .env; then echo "NOTE_MAX_COUNT=200000" >> .env; fi
bash tools/stack-remote.sh nur-deploy
if ! grep -q '^NOTE_MAX_COUNT=' .env; then echo "NOTE_MAX_COUNT=200000" >> .env; docker compose up -d 2>&1 | tail -1; sleep 8; fi
C=$(docker compose ps -q saga); D=$(docker compose ps -q db)
P=$(basename "$PWD")
PORT=$(grep '^HOST_PORT=' .env | cut -d= -f2); PORT=${PORT:-8080}
PGU=$(grep '^POSTGRES_USER=' .env | cut -d= -f2); PGU=${PGU:-saga}
mkdir -p perf-out
# langsame Abfragen protokollieren (nur dieser Test-Stack)
docker exec "$D" psql -U "$PGU" -d saga -qc "ALTER SYSTEM SET log_min_duration_statement = 25" -c "SELECT pg_reload_conf()" >/dev/null
FAIL=0
for S in $STUFEN; do
  echo "=== Stufe $S ==="
  docker exec -i -w /app "$C" python - --stufe "$S" < tools/perf/gen.py | grep '^PERF ' | cut -c6- > "perf-out/$S.info.json"
  python3 -c "import json;d=json.load(open('perf-out/$S.info.json'));print({k:v for k,v in d.items() if not k.startswith('cookie')})"
  docker restart "$C" >/dev/null; sleep 3          # frischer Prozess je Stufe (RAM vergleichbar)
  for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null && break; sleep 1; done
  # Abgleich der abgeleiteten Daten (saga/pageindex.py: Suchtext, Verweise, Vorschaubilder) nach dem Erzeugen abwarten -
  # sonst misst die Stufe den einmaligen Aufbau mit. Aeltere Versionen ohne note_stale: sofort weiter.
  T0=$(date +%s)
  for i in $(seq 1 600); do
    n=$(docker exec "$D" psql -U "$PGU" -d saga -tAc "SELECT count(*) FROM note_stale" 2>/dev/null || echo 0)
    [ "${n:-0}" = "0" ] && break; sleep 1
  done
  echo "Abgleich (pageindex) nach dem Erzeugen: $(( $(date +%s) - T0 )) s"
  sleep 2                                           # danach frischt der Abgleich noch die Statistik auf (ANALYZE)
  SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  python3 tools/perf/bench.py --base "http://127.0.0.1:$PORT" --info "perf-out/$S.info.json" --out "perf-out/$S.json" --n "$N" --project "$P" \
    | tee "perf-out/$S.txt"
  docker exec -i -w /app "$C" python - "$(cat perf-out/$S.info.json)" < tools/perf/explain.py | tee "perf-out/$S.explain.txt"
  if [ "$SAGA_PERF_WEB" = "1" ]; then                # Weboberflaeche (Playwright im Container, headless)
    docker run --rm --network host -v "$PWD/tools/perf:/w:ro" -v "$PWD/perf-out:/o" mcr.microsoft.com/playwright/python:v1.48.0-noble \
      sh -c "pip install -q --break-system-packages playwright==1.48.0 >/dev/null 2>&1; python /w/web.py --base http://127.0.0.1:$PORT --info /o/$S.info.json --out /o/$S.web.json $SAGA_PERF_WEB_ARGS" \
      | tee "perf-out/$S.web.txt" || FAIL=1
  fi
  docker logs --since "$SINCE" "$D" 2>&1 | grep -E 'duration: [0-9.]+ ms' | sed -E 's/^.*duration: ([0-9.]+) ms +(statement|execute [^:]*): /\1 ms  /' \
    | sort -rn | awk '!seen[substr($0, index($0,"ms")+4, 90)]++' | head -25 | cut -c1-260 > "perf-out/$S.slow.txt" || true
  echo "--- langsamste Abfragen (Postgres-Log) ---"; head -10 "perf-out/$S.slow.txt"
  python3 - "$S" "$SAGA_PERF_ZIEL" <<'PY' || FAIL=1
import json, sys
s = sys.argv[1]
lim = json.load(open("tools/perf/limits.json")).get(("ziel_" if sys.argv[2] == "1" else "") + s, {})
res = {r["name"]: r for r in json.load(open(f"perf-out/{s}.json"))["results"]}
bad = 0
for n, r in res.items():                      # je Messung der laengste passende Praefix
    pre = max((p for p in lim if n.startswith(p)), key=len, default=None)
    if pre is None or "p95_ms" not in r:
        continue
    ms = lim[pre]
    if r["p95_ms"] > ms or any(c >= 500 for c in r.get("status", [])):
        print(f"GRENZWERT {s}: {r['name']}: p95 {r['p95_ms']} ms > {ms} ms (Status {r['status']})"); bad += 1
print(f"Grenzwerte {s}: {len(lim)} geprueft, {bad} ueberschritten")
sys.exit(1 if bad else 0)
PY
done
docker exec "$D" psql -U "$PGU" -d saga -qc "ALTER SYSTEM RESET log_min_duration_statement" -c "SELECT pg_reload_conf()" >/dev/null
if [ "$SAGA_PERF_KEEP" != "1" ]; then
  docker exec -w /app "$C" python -c "
import auth
from saga.userdata import delete_user_data
for u in ('perf-a', 'perf-b'):
    auth.get_user(u) and delete_user_data(u, drop_user=True)
print('Testkonten perf-a/perf-b geloescht')"
fi
exit $FAIL
