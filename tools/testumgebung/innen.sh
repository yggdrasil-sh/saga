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
export SAGA_BASE=http://127.0.0.1:$P SAGA_UPDATE_CHECK=0 SAGA_SKIP_BACKUP=1 NO_PROXY='*' no_proxy='*'
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
export HOME=$UMG/home                                    # NSS-DB mit der Test-CA (Chromium), sonst nichts darin
[ -d $UMG_GOBIN ] && export PATH=$UMG_GOBIN:$PATH        # age/age-keygen wie im Image
export SHOTS=${SHOTS:-$RUN/shots} CALDAV_ALLOW_HTTP=1 IMMICH_URL=http://127.0.0.1:18283 ICON_CDN=http://127.0.0.1:18310
export GJALLARHORN_BIN=$UMG_BIN/gjallarhorn SAGA_MEDIA_BIN=$UMG_BIN/saga-media
export UMG_CERT=$UMG/ca/srv.pem UMG_KEY=$UMG/ca/srv.key UMG_CA=$UMG/ca/ca.pem
[ -f $UMG/e0/calls-e0.js ] && export SAGA_CALLS_E0=${SAGA_CALLS_E0:-$UMG/e0/calls-e0.js}
[ -f $UMG/e0/calls-23.js ] && export SAGA_CALLS23=${SAGA_CALLS23:-$UMG/e0/calls-23.js}
[ -f $UMG/ton.wav ] && export TON=${TON:-$UMG/ton.wav}
export UMG_PW="um-Test-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')-Pw1"
GRAV=$((H0 + 3))
export HK_FAKE_GRAVATAR_PORT=$GRAV UMG_ALLOWED="127.0.0.1:5232, 127.0.0.1:18283, 127.0.0.1:$GRAV"
SENV=(GRAVATAR_URL=http://127.0.0.1:$GRAV/avatar/ ${UMG_SENV:-})
for i in $(seq 40); do python -c "import socket; socket.create_connection(('127.0.0.1', 55432), 1)" 2>/dev/null && break; sleep 0.2; done

if [ $ART = sso ]; then
  export HK_LDAP_ADMIN_PW="ldap-$(head -c 9 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  PYTHONPATH=$UMG/pylib python "$D/ldap_attrappe.py" --port 389 --cert $UMG_CERT --key $UMG_KEY > $RUN/ldap.log 2>&1 & PIDS+=($!)
  python "$D/oidc_attrappe.py" --host mockoidc --port 8080 --name default > $RUN/oidc.log 2>&1 & PIDS+=($!)
  for i in $(seq 50); do curl -sf -o /dev/null http://mockoidc:8080/default/.well-known/openid-configuration && \
    python -c "import socket; socket.create_connection(('ldap.test', 389), 1)" 2>/dev/null && break; sleep 0.2; done
  echo "Attrappen: LDAP ldap.test:389 (StartTLS), OIDC http://mockoidc:8080/default"
fi
[ $ART = knoten ] && export UMG_HORN=1
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
    SENV+=(MEDIA_INTERNAL_PORT=$MP HORN_PORT=$HP HORN_NAME=localhost HORN_TLS_CERT=$UMG_CERT HORN_TLS_KEY=$UMG_KEY HORN_UDP=${HORN_UDP:-0}
           HORN_ICE_IPS=${HORN_ICE_IPS:-127.0.0.1})
  fi
  env HOST=127.0.0.1 ADMIN_HOST=127.0.0.1 SAGA_FAVICON_TEST_PUBLIC=127.0.0.2,127.0.0.3 "${SENV[@]}" "${CMD[@]}" > $RUN/server.log 2>&1 &
  SRV=$!; PIDS+=($SRV)
  export SAGA_SERVER_PID=$SRV SAGA_SERVER_LOG=$RUN/server.log
  up=0; for i in $(seq 120); do curl -sf -o /dev/null $BASE_URL/healthz && { up=1; break; }; sleep 0.5; done
  echo "Server pid $SRV auf $P: bereit=$up"
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
LIMIT=${UMG_LIMIT:-1500}
echo "== test start $(date +%T)"
case $F in
  *.js) PYTHONPATH=tests/pgshim:. timeout -k 20 $LIMIT node $TF ;;
  *)    PYTHONPATH=tests/pgshim:.${UMG_PYX:+:$UMG_PYX} timeout -k 20 $LIMIT python -u $TF ;;
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
