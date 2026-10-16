#!/bin/bash
# Laeuft AUF dem Docker-Host im Repo-Verzeichnis (von tools/test-stack.sh aufgerufen):
#   1. .env beim ersten Mal erzeugen, docker compose up -d --build (Postgres + Saga)
#   2. Im Container eine zweite Instanz aus /tmp/hkapp auf 8099/8098 starten (gleiche Datenbank)
#      + Radicale (CalDAV-Testserver), dann alle Tests (alte Tests ueber tests/pgshim auf Postgres)
set -e
cd "$(dirname "$0")/.."
TESTS=${SAGA_TESTS:-"isolation funktion kalender_modul kalender_api kalender_schreiben app_token app_login spalten vorlagen immich widgets inline archiv bilder dienste standalone sso_ldap app_pair versionen sprachen haertung veroeffentlichen teilen live chat push ki foederation fehlerrunde zweifaktor avatare"}   # SAGA_TESTS="chat avatare": nur diese Reihen
rnd() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
# Zweiter Stack auf demselben Host (z. B. SAGA_DIR=/root/saga-live SAGA_PORT=18086): eigener Port (Admin = Port+1, nur
# 127.0.0.1), eigenes Image-Tag und eigene Namen der Test-Container - der Compose-Projektname kommt vom Verzeichnis.
P=$(basename "$PWD")
TN=hk-test${SAGA_PORT:+-$P}
if [ ! -f .env ] || grep -q '^SECRET_KEY=$' .env; then
  IP=$(hostname -I | awk '{print $1}')
  if [ -n "$SAGA_PORT" ]; then
    sed -e "s|^BASE_URL=.*|BASE_URL=http://$IP:$SAGA_PORT|" -e "s|^ADMIN_URL=.*|ADMIN_URL=http://127.0.0.1:$((SAGA_PORT + 1))|" \
        -e "s|^HOST_PORT=.*|HOST_PORT=$SAGA_PORT|" -e "s|^ADMIN_HOST_PORT=.*|ADMIN_HOST_PORT=$((SAGA_PORT + 1))|" \
        -e "s|^ADMIN_BIND=.*|ADMIN_BIND=127.0.0.1|" -e "s|^SAGA_VERSION=.*|SAGA_VERSION=test-$P|" \
        -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rnd 32)|" -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rnd 24)|" \
        -e "s|^CANONICAL_REDIRECT=.*|CANONICAL_REDIRECT=false|" .env.example > .env
  else
    sed -e "s|^BASE_URL=.*|BASE_URL=http://$IP:8080|" -e "s|^ADMIN_URL=.*|ADMIN_URL=http://$IP:8081|" \
        -e "s|^SECRET_KEY=.*|SECRET_KEY=$(rnd 32)|" -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rnd 24)|" \
        -e "s|^ADMIN_BIND=.*|ADMIN_BIND=0.0.0.0|" -e "s|^CANONICAL_REDIRECT=.*|CANONICAL_REDIRECT=false|" .env.example > .env
  fi
  chmod 600 .env
  echo ".env erzeugt ($(grep '^BASE_URL=' .env))"
fi
docker compose up -d --build --remove-orphans 2>&1 | tail -3
C=$(docker compose ps -q saga)
for i in $(seq 1 40); do
  docker exec "$C" python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" </dev/null 2>/dev/null && break
  sleep 2
done
docker compose ps --format '{{.Service}}: {{.Status}}'
[ "$1" = "nur-deploy" ] && exit 0

X() { docker exec "$C" sh -c "$1" </dev/null; }

# Test-Anmeldedienste im selben Docker-Netz: OpenLDAP (ldap.test) und ein OIDC-Testanbieter (mockoidc).
# Nur Testkonten mit Wegwerf-Passwoertern; die Container laufen nur fuer die Tests.
NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$C")
LDAP_PW=$(rnd 12)
docker rm -f $TN-ldap $TN-oidc >/dev/null 2>&1 || true
docker run -d --name $TN-ldap --network "$NET" --network-alias ldap.test --ulimit nofile=1024:1024 \
  -e LDAP_ORGANISATION=Test -e LDAP_DOMAIN=example.org -e LDAP_ADMIN_PASSWORD="$LDAP_PW" -e LDAP_TLS_VERIFY_CLIENT=never \
  docker.io/osixia/openldap:1.5.0 >/dev/null
docker run -d --name $TN-oidc --network "$NET" --network-alias mockoidc -e 'JSON_CONFIG={"interactiveLogin":true}' \
  ghcr.io/navikt/mock-oauth2-server:2.1.10 >/dev/null
sleep 8
X "rm -rf /tmp/hkapp /tmp/hkstage /tmp/kal && mkdir -p /tmp/hkapp /tmp/hkstage /tmp/kal/py"
tar --exclude .env -cf - . | docker exec -i "$C" tar xf - -C /tmp/hkapp
X 'pip install -q --target /tmp/kal/py radicale >/dev/null 2>&1; echo test:geheim-app-pw-123 > /tmp/kal/users
   cd /tmp/kal && PYTHONPATH=/tmp/kal/py nohup python -m radicale --server-hosts 127.0.0.1:5232 --storage-filesystem-folder /tmp/kal/col \
     --auth-type htpasswd --auth-htpasswd-filename /tmp/kal/users --auth-htpasswd-encryption plain > /tmp/kal/rad.log 2>&1 &
   echo $! > /tmp/kal/pid'
# PyNaCl (libsodium) fuer die Chat-Tests: native Bibliothek - /tmp ist noexec, deshalb auf das Daten-Volume (danach weg)
X 'pip install -q --target /data/.saga-test-py pynacl==1.6.2 >/dev/null 2>&1 || true'
X 'cd /tmp/hkapp && python - <<PY
import auth, db
db.wait_ready(); db.init_schema()
for u in ("hk-test-a", "hk-test-b"):
    auth.get_user(u) or auth.create_user(u, None, u)
open("/tmp/hkstage/prev_hosts", "w").write(auth.settings(fresh=True)["allowed_hosts"])
auth.save_setting("allowed_hosts", "127.0.0.1:5232, 127.0.0.1:18283, 127.0.0.1:18284")
PY'
X 'cd /tmp/hkapp && STATE_DIR=/tmp/hkstage CALDAV_ALLOW_HTTP=1 IMMICH_URL=http://127.0.0.1:18283 GRAVATAR_URL=http://127.0.0.1:18284/avatar/ PORT=8099 ADMIN_PORT=8098 HOST=127.0.0.1 ADMIN_HOST=127.0.0.1 \
   nohup python run.py > /tmp/hkstage/server.log 2>&1 & echo $! > /tmp/hkstage/pid; sleep 6'
X "cd /tmp/hkapp/tests && export SESSION_SECRET=\$SECRET_KEY HK_RAD_PW=geheim-app-pw-123 STATE_DIR=/tmp/hkstage CALDAV_ALLOW_HTTP=1 \
     PYTHONPATH=/tmp/hkapp/tests/pgshim:/tmp/hkapp:/tmp/kal/py:/data/.saga-test-py HK_TEST_BASE=http://127.0.0.1:8099 HK_TEST_ADMIN=http://127.0.0.1:8098 \
     HK_TEST_DB=/tmp/hkstage/test.db IMMICH_URL=http://127.0.0.1:18283 HK_LDAP_ADMIN_PW=$LDAP_PW
   for t in $TESTS; do rm -rf /tmp/kal/col/collection-root/test; python -W ignore \$t.py > /tmp/hkstage/t-\$t.log 2>&1; tail -1 /tmp/hkstage/t-\$t.log; grep -E '^FAIL|Traceback|Error' /tmp/hkstage/t-\$t.log | head -8; done
   echo Serverfehler: \$(grep -c Traceback /tmp/hkstage/server.log); grep -B2 -E '^[A-Za-z.]+(Error|Exception):' /tmp/hkstage/server.log | head -30"
X 'kill $(cat /tmp/hkstage/pid) $(cat /tmp/kal/pid) 2>/dev/null; rm -rf /data/.saga-test-py; true'
X 'cd /tmp/hkapp && python -c "
import auth, app
auth.save_setting(\"allowed_hosts\", open(\"/tmp/hkstage/prev_hosts\").read())
for u in (\"hk-test-a\", \"hk-test-b\"):
    app.delete_user_data(u)
from db import connect
with connect() as c, c:
    c.execute(\"DELETE FROM users WHERE username IN (%s, %s)\" % (repr(\"hk-test-a\"), repr(\"hk-test-b\")))
"'
docker rm -f $TN-ldap $TN-oidc >/dev/null 2>&1 || true
