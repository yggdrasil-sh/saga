#!/bin/bash
# Ablauf fuer die Browser-Reihe Gedaechtnis (2.6, Bugs-DB 2581) auf saga-test: eigene DB, eigener Server (Haupt- und
# Admin-Port), Fake-Embedding/Fake-KI (tests/fakeai.py), Rig, dann Chromium und Firefox; raeumt danach auf.
#   GW_DB=saga_mem_web GW_PORT=8910 GW_ADMIN_PORT=8911 GW_FAKE_PORT=8915 GW_DEAD_PORT=8919 [SHOTS=/pfad] [VORHER=1] \
#     bash tests/gedaechtnis_web.sh
# Erwartet die Umgebung von /srv/saga-test/env.sh (venv, Playwright, SESSION_SECRET). Auf GW_DEAD_PORT darf nichts lauschen.
set -u
P=${GW_PORT:-8910}; AP=${GW_ADMIN_PORT:-8911}; FP=${GW_FAKE_PORT:-8915}; DP=${GW_DEAD_PORT:-8919}; DBN=${GW_DB:-saga_mem_web}
ST=${GW_STATE:-/tmp/gedaechtnis-web-$$}
for x in $P $AP $FP $DP; do
  if ss -ltn | grep -qE "[:.]$x\b"; then echo "Port $x ist belegt - Abbruch"; exit 3; fi
done
mkdir -p "$ST"
dropdb -h /tmp -p 55432 -U saga --if-exists "$DBN" 2>/dev/null; createdb -h /tmp -p 55432 -U saga "$DBN" || exit 3
export DATABASE_URL="postgresql://saga@/$DBN?host=/tmp&port=55432" STATE_DIR=$ST HK_TEST_DB=$ST/test.db
export PORT=$P ADMIN_PORT=$AP HOST=127.0.0.1 ADMIN_HOST=127.0.0.1 BASE_URL=http://127.0.0.1:$P
export HK_TEST_BASE=http://127.0.0.1:$P HK_TEST_ADMIN=http://127.0.0.1:$AP HK_FAKEAI_PORT=$FP
export GW_PASS="gw-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
PIDS=()
cleanup() { for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; sleep 1
  PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py clean >/dev/null 2>&1
  dropdb -h /tmp -p 55432 -U saga --if-exists "$DBN" 2>/dev/null; rm -rf "$ST"; }
trap cleanup EXIT
python tests/fakeai.py "$FP" > "$ST/fakeai.log" 2>&1 & PIDS+=($!)
PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py setup || exit 4
python run.py > "$ST/server.log" 2>&1 & PIDS+=($!)
up=0; for i in $(seq 120); do curl -sf -o /dev/null "http://127.0.0.1:$P/healthz" && { up=1; break; }; sleep 0.5; done
[ $up = 1 ] || { echo "Server nicht bereit"; tail -20 "$ST/server.log"; exit 5; }
CK=$(PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py admin-cookie)
export SAGA_BASE=http://127.0.0.1:$P SAGA_ADMIN=http://127.0.0.1:$AP SAGA_ADMIN_COOKIE=$CK SAGA_USER=gw-web SAGA_PASS=$GW_PASS FAKE_PORT=$FP DEAD_PORT=$DP
rc=0
node tests/browser_gedaechtnis.js || rc=1
if [ -z "${VORHER:-}" ]; then
  PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py setup > /dev/null || exit 4      # frischer Stand fuer Firefox
  sleep 5.5                                                  # Einstellungen sind im Server 5 s zwischengespeichert
  SAGA_ADMIN_COOKIE=$(PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py admin-cookie) BR=firefox SHOTS= node tests/browser_gedaechtnis.js || rc=1
fi
echo "Serverfehler: $(grep -cE 'Traceback|Exception in ASGI' "$ST/server.log")"
grep -E 'Traceback|Error' "$ST/server.log" | head -5
exit $rc
