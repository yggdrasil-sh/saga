#!/bin/bash
# Testumgebung: laeuft IM Netz- und Mount-Namensraum (von umgebung.sh lauf). Attrappen + Server starten, Reihe mit
# Zeitlimit, alles per PID beenden. Nicht direkt aufrufen.
set -u
cd "$WT"
mount --bind $RUN/hosts /etc/hosts || { echo "hosts-Bind fehlgeschlagen"; exit 1; }   # nur in diesem Mount-Namensraum
P=$((9001 + 10 * SLOT)); AP=$((P + 1)); MP=$((P + 2)); HP=$((P + 3)); H0=$((19001 + 10 * SLOT))
PIDS=()
python "$D/pgfwd.py" 55432 $PGSOCK & PIDS+=($!)       # POSTGRES_HOST=127.0.0.1 (Foederations-Helfer) im Namensraum
export DATABASE_URL="postgresql://saga@/$DBN?host=/tmp&port=55432" STATE_DIR=$RUN HK_TEST_DB=$RUN/test.db
export PORT=$P ADMIN_PORT=$AP BASE_URL=http://127.0.0.1:$P HK_TEST_BASE=http://127.0.0.1:$P HK_TEST_ADMIN=http://127.0.0.1:$AP
export SAGA_BASE=http://127.0.0.1:$P SAGA_UPDATE_CHECK=0 NO_PROXY='*' no_proxy='*'
RN=$(basename "$F")
# Server-Variablen je Reihe (gehen an den Server UND an die Reihe; UMG_SENV kommt danach und gewinnt). Ohne sie warten
# die Reihen zu lange bzw. startet der Server nicht (ki_web.py: SEARXNG_URL ohne SEARXNG_SECRET -> "fehlende Konfiguration").
REIHE_ENV=()
case $RN in
  erwaehnungen.py) REIHE_ENV=(SAGA_MENTION_DELAY=1) ;;
  collab.py)       REIHE_ENV=(COLLAB_MAT_DELAY=0.5 COLLAB_COMPACT_IDLE=3) ;;
  ki_web.py)       REIHE_ENV=(SEARXNG_URL=http://127.0.0.1:18292 SEARXNG_SECRET=umg-nur-test-$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')) ;;
  webhooks.py)     REIHE_ENV=(SAGA_WEBHOOK_DEBOUNCE=1 SAGA_WEBHOOK_RETRY_SCALE=0.02) ;;
  # 2.6 (2264, 2505): Registrierungen 40 je IP (die Reihe registriert viele Clients von 127.0.0.1); anonyme Anfragen mit
  # dem Server-Standard (SAGA_OAUTH_ANON_PER_IP 30, die Reihe liest saga.oauth.ANON_PER_IP)
  mcp_oauth.py|mcp_oauth_proben.py) REIHE_ENV=(SAGA_OAUTH_REG_PER_IP=40 SAGA_OAUTH_REG_PER_HOUR=1000) ;;
  # 2.6 (2516): Push-Relais der Reihe (Durchreicher 18630 im Namensraum, Domain-Nachweis mit saga.example.org)
  fakerelay.py)    REIHE_ENV=(SAGA_PUSH_RELAY_URL=http://127.0.0.1:18630 SAGA_PUSH_RELAY_HOST=saga.example.org) ;;
  widget_push.py)  REIHE_ENV=(SAGA_PUSH_RELAY_URL=http://127.0.0.1:18630 SAGA_PUSH_RELAY_HOST=saga.example.org SAGA_WIDGET_PUSH_GAP=6) ;;
esac
# Backups vor Migrationen: sonst aus (schneller); an fuer Reihen, die sie pruefen (versionen.py) oder mit UMG_BACKUP=1
case $RN in versionen.py) UMG_BACKUP=${UMG_BACKUP:-1} ;; esac
if [ "${UMG_BACKUP:-0}" = 1 ]; then unset SAGA_SKIP_BACKUP; else export SAGA_SKIP_BACKUP=1; fi
[ ${#REIHE_ENV[@]} -gt 0 ] && { export "${REIHE_ENV[@]}"; echo "Variablen der Reihe (Server + Reihe): $(printf '%s ' "${REIHE_ENV[@]}" | sed 's/SECRET=[^ ]*/SECRET=.../g')"; }
echo "Backups vor Migrationen: $([ "${UMG_BACKUP:-0}" = 1 ] && echo an || echo aus) (UMG_BACKUP)"
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export HOME=$UMG/home                                    # NSS-DB mit der Test-CA (Chromium), sonst nichts darin
[ -d $UMG_GOBIN ] && export PATH=$UMG_GOBIN:$PATH        # age/age-keygen wie im Image
export SHOTS=${SHOTS:-$RUN/shots} CALDAV_ALLOW_HTTP=1 IMMICH_URL=http://127.0.0.1:18283 ICON_CDN=http://127.0.0.1:18310
# Knoten-Binaries nur, wenn es sie gibt (umgebung.sh baut sie fuer knoten/fedknoten; sonst bauen Reihen wie a3_tunnel.py selbst)
[ -x $UMG_BIN/gjallarhorn ] && [ -x $UMG_BIN/saga-media ] && export GJALLARHORN_BIN=$UMG_BIN/gjallarhorn SAGA_MEDIA_BIN=$UMG_BIN/saga-media
export UMG_CERT=$UMG/ca/srv.pem UMG_KEY=$UMG/ca/srv.key UMG_CA=$UMG/ca/ca.pem
[ -f $UMG/e0/calls-e0.js ] && export SAGA_CALLS_E0=${SAGA_CALLS_E0:-$UMG/e0/calls-e0.js}
[ -f $UMG/e0/calls-23.js ] && export SAGA_CALLS23=${SAGA_CALLS23:-$UMG/e0/calls-23.js}
[ -f $UMG/ton.wav ] && export TON=${TON:-$UMG/ton.wav}
export UMG_PW="um-Test-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')-Pw1"
GRAV=$((H0 + 3))
export HK_FAKE_GRAVATAR_PORT=$GRAV UMG_ALLOWED="127.0.0.1:5232, 127.0.0.1:18283, 127.0.0.1:$GRAV"
SENV=(GRAVATAR_URL=http://127.0.0.1:$GRAV/avatar/ "${REIHE_ENV[@]}" ${UMG_SENV:-})
for i in $(seq 40); do python -c "import socket; socket.create_connection(('127.0.0.1', 55432), 1)" 2>/dev/null && break; sleep 0.2; done

# CalDAV: Radicale im Namensraum auf 127.0.0.1:5232 (Konto test / geheim-app-pw-123 wie in den Reihen, nur Test), fuer
# kalender_api.py, kalender_schreiben.py, mcp.py, mcp_nach.py, haertung.py. Aus mit UMG_CALDAV=0.
if [ "${UMG_CALDAV:-1}" = 1 ]; then
  # Radicale aus dem System-Python (nicht im venv; /usr/bin/radicale will den Benutzer radicale); anderes: UMG_RADICALE
  RAD=${UMG_RADICALE:-/usr/bin/python3 -m radicale}
  if ${RAD% -m radicale} -c "import radicale" 2>/dev/null || [ -n "${UMG_RADICALE:-}" ]; then
    mkdir -p $RUN/caldav/col; printf 'test:geheim-app-pw-123\n' > $RUN/caldav/users
    $RAD --server-hosts 127.0.0.1:5232 --storage-filesystem-folder $RUN/caldav/col --auth-type htpasswd \
      --auth-htpasswd-filename $RUN/caldav/users --auth-htpasswd-encryption plain > $RUN/caldav/radicale.log 2>&1 & PIDS+=($!)
    for i in $(seq 50); do python -c "import socket; socket.create_connection(('127.0.0.1', 5232), 1)" 2>/dev/null && break; sleep 0.2; done
    export HK_TEST_CALDAV=http://127.0.0.1:5232/
    echo "CalDAV: Radicale $HK_TEST_CALDAV ($(python -c "import socket; socket.create_connection(('127.0.0.1', 5232), 1); print('bereit')" 2>&1 | tail -1))"
  else
    echo "CalDAV: Radicale fehlt (/usr/bin/python3 -m pip install radicale oder UMG_RADICALE) - Kalender-Teile melden SKIP"
  fi
fi

if [ $ART = sso ]; then
  export HK_LDAP_ADMIN_PW="ldap-$(head -c 9 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  PYTHONPATH=$UMG/pylib python "$D/ldap_attrappe.py" --port 389 --cert $UMG_CERT --key $UMG_KEY > $RUN/ldap.log 2>&1 & PIDS+=($!)
  python "$D/oidc_attrappe.py" --host mockoidc --port 8080 --name default > $RUN/oidc.log 2>&1 & PIDS+=($!)
  for i in $(seq 50); do curl -sf -o /dev/null http://mockoidc:8080/default/.well-known/openid-configuration && \
    python -c "import socket; socket.create_connection(('ldap.test', 389), 1)" 2>/dev/null && break; sleep 0.2; done
  echo "Attrappen: LDAP ldap.test:389 (StartTLS), OIDC http://mockoidc:8080/default"
fi
[ $ART = knoten ] && export UMG_HORN=1
# Frage "Erste Schritte" (static/firststeps.js) fuer ALLE Konten des Laufs vorab mit "Nein" beantworten - auch fuer Konten,
# die eine Reihe selbst anlegt (konten.py legt dazu einen Trigger in der DB des Laufs an). Nicht fuer die Reihen, die den
# Dialog selbst pruefen; von Hand: UMG_FIRST_STEPS=frage.
case $RN in erste_schritte.py|browser_leere_zustaende.js) UMG_FIRST_STEPS=${UMG_FIRST_STEPS:-frage} ;; esac
export UMG_FIRST_STEPS=${UMG_FIRST_STEPS:-nein}
echo "Erste Schritte: $UMG_FIRST_STEPS (UMG_FIRST_STEPS)"
PYTHONPATH=. python "$D/konten.py" $RUN/konten.env && . $RUN/konten.env || echo "Konten fehlgeschlagen"

if [ $ART = fed ] || [ $ART = fedknoten ]; then
  KN=""; [ $ART = fedknoten ] && KN=--knoten
  UMG_FED_PORTS="$((P + 4)),$((P + 5)),$((P + 6)),$((P + 7))" UMG_FED_MEDIA=$((P + 8)) UMG_HORN_PORT=$((P + 9)) UMG_FED_DB=${DBN}f \
    PYTHONPATH=tests/pgshim:. python -u "$D/zwei_stacks.py" --env $RUN/fed.env --state $RUN/fed $KN > $RUN/fed.log 2>&1 & FEDPID=$!
  for i in $(seq 360); do [ -f $RUN/fed.env ] && break; kill -0 $FEDPID 2>/dev/null || break; sleep 0.5; done
  [ -f $RUN/fed.env ] && . $RUN/fed.env
  grep -E '^(OK|FAIL)' $RUN/fed.log | sed 's/^/  [stacks] /'
  echo "Stacks: A=${SAGA_A:-?} B=${SAGA_B:-?} bereit=${UMG_FED_OK:-0}"
else
  CMD=(python run.py)
  if [ $ART = knoten ]; then
    CMD=($UMG_BIN/saga-media -- python run.py)
    SENV+=(MEDIA_INTERNAL_PORT=$MP HORN_PORT=$HP HORN_NAME=${UMG_HORN_NAME:-localhost} HORN_TLS_CERT=${UMG_HORN_CERT:-$UMG_CERT} HORN_TLS_KEY=${UMG_HORN_KEY:-$UMG_KEY} HORN_UDP=${HORN_UDP:-0}
           HORN_ICE_IPS=${HORN_ICE_IPS:-127.0.0.1})
  fi
  env HOST=127.0.0.1 ADMIN_HOST=127.0.0.1 SAGA_FAVICON_TEST_PUBLIC=127.0.0.2,127.0.0.3 "${SENV[@]}" "${CMD[@]}" > $RUN/server.log 2>&1 &
  SRV=$!; PIDS+=($SRV)
  export SAGA_SERVER_PID=$SRV SAGA_SERVER_LOG=$RUN/server.log
  up=0; for i in $(seq 120); do curl -sf -o /dev/null $BASE_URL/healthz && { up=1; break; }; kill -0 $SRV 2>/dev/null || break; sleep 0.5; done
  echo "Server pid $SRV auf $P: bereit=$up"
  if [ $up = 0 ]; then              # sonst laeuft die Reihe gegen nichts und meldet nur viele FAIL
    echo "== SERVER NICHT BEREIT - Reihe wird nicht gestartet. Ende von $RUN/server.log:"
    tail -15 $RUN/server.log | sed 's/^/   /'
    grep -a "fehlende/ungesetzte Konfiguration" $RUN/server.log | tail -1 | sed 's/^/== /'
    for p in "${PIDS[@]}"; do kill $p 2>/dev/null; done
    echo "== test rc=98 $(date +%T)"
    exit 0
  fi
  if [ $ART = knoten ]; then
    ok=0; for i in $(seq 60); do
      st=$(PYTHONPATH=. python -c "
from db import connect
with connect() as c:
    r = c.execute(\"SELECT state FROM horn_node WHERE builtin=1 AND removed IS NULL\").fetchone()
print(r[0] if r else '-')" 2>/dev/null | tail -1)
      code=$(curl -s -o /dev/null -w '%{http_code}' --cacert $UMG_CA https://localhost:$HP/)   # TLS mit der Test-CA (jede HTTP-Antwort)
      [ "$st" = ok ] && [ "$code" != 000 ] && { ok=1; break; }; sleep 0.5; done
    echo "Knoten https://localhost:$HP bereit=$ok (Register: $st, TLS mit Test-CA: HTTP $code)"
  fi
fi

TF=tests/$F; case $F in /*) TF=$F;; esac
PGSHIM=tests/pgshim:                                     # sqlite3 -> PostgreSQL (Server-Reihen)
case $RN in relay_dienst.py) PGSHIM= ;; esac             # 2516: Relais-Dienst im Prozess mit echtem SQLite (store.py)
LIMIT=${UMG_LIMIT:-1500}
echo "== test start $(date +%T)"
case $F in
  *.js) PYTHONPATH=tests/pgshim:. timeout -k 20 $LIMIT node $TF ;;
  *)    PYTHONPATH=$PGSHIM.${UMG_PYX:+:$UMG_PYX} timeout -k 20 $LIMIT python -u $TF ;;
esac
rc=$?
echo "== test rc=$rc $(date +%T)"
[ $rc = 124 ] && echo "== ZEITLIMIT ${LIMIT}s"
if [ -n "${FEDPID:-}" ]; then
  kill -TERM $FEDPID 2>/dev/null; for i in $(seq 60); do kill -0 $FEDPID 2>/dev/null || break; sleep 0.5; done
  grep -E '^(OK|FAIL)|zwei_stacks:' $RUN/fed.log | tail -4 | sed 's/^/  [stacks] /'
fi
for p in "${PIDS[@]}"; do kill $p 2>/dev/null; done
for i in $(seq 20); do alive=0; for p in "${PIDS[@]}"; do kill -0 $p 2>/dev/null && alive=1; done; [ $alive = 0 ] && break; sleep 0.5; done
for p in "${PIDS[@]}"; do kill -9 $p 2>/dev/null; done
[ -f $RUN/server.log ] && echo "Server: Tracebacks $(grep -c Traceback $RUN/server.log), Suche ohne Trigramm-Index: $(grep -c 'ohne Trigramm-Index' $RUN/server.log)"
exit 0
