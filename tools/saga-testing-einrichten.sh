#!/bin/sh
# Saga auf einen Update-Kanal stellen und automatisch aktuell halten (wie die Testinstanz der Entwickler).
#
#   sudo sh saga-testing-einrichten.sh [Saga-Ordner] [Kanal]
#
#   Saga-Ordner  Ordner mit docker-compose.yml und .env (Standard: aktueller Ordner)
#   Kanal        testing (Standard; Release Candidates, z. B. 2.5.0-rc.N)
#                2 = neueste stabile 2.x  |  2.5.0 = genau diese Version
#
# Was das Skript macht:
#   1. sichert .env (.env.vor-<Kanal>-<Zeit>) und setzt SAGA_VERSION=<Kanal>
#   2. legt eine Datenbank-Sicherung an (pg_dump, Ordner sicherung/ neben docker-compose.yml)
#   3. installiert /usr/local/sbin/saga-autoupdate und den systemd-Timer saga-autoupdate.timer
#      (alle 15 Minuten: neues Image ziehen, nur bei Aenderung neu starten, Gesundheit pruefen)
#   4. fuehrt das erste Update sofort aus
#
# Zurueck auf stabil:  sudo sh saga-testing-einrichten.sh <Saga-Ordner> 2
#   (Achtung: eine Datenbank, die schon auf einer neueren Version lief, startet nicht mit einer aelteren -
#    dafuer die Sicherung aus Schritt 2 einspielen.)
# Abschalten:          sudo systemctl disable --now saga-autoupdate.timer
# Protokoll:           journalctl -t saga-autoupdate
set -eu

DIR=${1:-$(pwd)}
CH=${2:-testing}

die() { echo "FEHLER: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "bitte mit sudo bzw. als root ausfuehren"
command -v docker >/dev/null || die "docker nicht gefunden"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2) nicht gefunden"
command -v systemctl >/dev/null || die "systemd nicht gefunden"
DIR=$(cd "$DIR" && pwd) || die "Ordner $1 nicht gefunden"
[ -f "$DIR/.env" ] || die "$DIR/.env fehlt - ist das der Saga-Ordner?"
found=; for f in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do [ -f "$DIR/$f" ] && found=1; done
[ -n "$found" ] || die "keine docker-compose.yml in $DIR"
case "$CH" in testing|rc|[0-9]*) ;; *) die "unbekannter Kanal '$CH' (testing, 2, 2.5.0 ...)" ;; esac
cd "$DIR"
docker compose config --services 2>/dev/null | grep -qx saga || die "kein Dienst 'saga' in der Compose-Datei"

STAMP=$(date +%Y%m%d-%H%M%S)
echo "== Saga-Ordner: $DIR  ->  Kanal: $CH"

# 1. .env sichern und Kanal setzen
cp -p .env ".env.vor-$CH-$STAMP"
if grep -q '^SAGA_VERSION=' .env; then
  sed -i "s/^SAGA_VERSION=.*/SAGA_VERSION=$CH/" .env
else
  printf '\nSAGA_VERSION=%s\n' "$CH" >> .env
fi
echo "   .env gesichert als .env.vor-$CH-$STAMP, SAGA_VERSION=$CH"

# 2. Datenbank sichern (Saga legt vor Schema-Aenderungen zusaetzlich selbst ein Backup an)
if docker compose config --services | grep -qx db && [ -n "$(docker compose ps -q db)" ]; then
  mkdir -p sicherung && chmod 700 sicherung
  F="sicherung/saga-db-$STAMP.dump"
  if docker compose exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$F"; then
    chmod 600 "$F"; echo "   Datenbank gesichert: $DIR/$F ($(du -h "$F" | cut -f1))"
  else
    rm -f "$F"; die "Datenbank-Sicherung fehlgeschlagen - nichts geaendert ausser .env (zurueck: cp .env.vor-$CH-$STAMP .env)"
  fi
else
  echo "   Hinweis: kein laufender Dienst 'db' gefunden - Datenbank bitte selbst sichern"
fi

# 3. Update-Skript und Timer
cat > /usr/local/sbin/saga-autoupdate <<EOF
#!/bin/sh
# Saga automatisch aktualisieren: Image laut SAGA_VERSION (.env) ziehen; nur wenn es neu ist, saga neu starten.
# Vor Datenbank-Aenderungen legt Saga selbst ein Backup an. Log: journalctl -t saga-autoupdate
set -eu
cd "$DIR"
CH=\$(sed -n 's/^SAGA_VERSION=//p' .env | tail -1); CH=\${CH:-2}
PORT=\$(sed -n 's/^HOST_PORT=//p' .env | tail -1); PORT=\${PORT:-8080}
IMG="ghcr.io/yggdrasil-sh/saga:\$CH"
docker compose pull -q saga </dev/null >/dev/null 2>&1 || { logger -t saga-autoupdate "Pull von \$IMG fehlgeschlagen"; exit 0; }
NEW=\$(docker image inspect -f '{{.Id}}' "\$IMG")
CID=\$(docker compose ps -q saga </dev/null)
CUR=\$( [ -n "\$CID" ] && docker inspect -f '{{.Image}}' "\$CID" || echo none)
[ "\$NEW" = "\$CUR" ] && exit 0
OLDV=\$( [ -n "\$CID" ] && docker exec "\$CID" cat /app/VERSION 2>/dev/null || echo "?")
docker compose up -d saga </dev/null >/dev/null 2>&1
ok=0; for i in \$(seq 1 60); do curl -sf "http://127.0.0.1:\$PORT/healthz" >/dev/null && { ok=1; break; }; sleep 2; done
NEWV=\$(docker compose exec -T saga cat /app/VERSION </dev/null 2>/dev/null || echo "?")
if [ "\$ok" = 1 ]; then logger -t saga-autoupdate "aktualisiert: \$OLDV -> \$NEWV (\$IMG)"
else logger -p user.err -t saga-autoupdate "nach Update auf \$NEWV nicht gesund - docker compose logs saga pruefen"; fi
docker image prune -f >/dev/null 2>&1 || true
EOF
chmod 755 /usr/local/sbin/saga-autoupdate

cat > /etc/systemd/system/saga-autoupdate.service <<'EOF'
[Unit]
Description=Saga: neues Image ziehen und bei Bedarf neu starten
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/saga-autoupdate
EOF

cat > /etc/systemd/system/saga-autoupdate.timer <<'EOF'
[Unit]
Description=Saga alle 15 Minuten auf neue Version pruefen

[Timer]
OnBootSec=5min
OnCalendar=*:0/15
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now saga-autoupdate.timer >/dev/null 2>&1
echo "   Timer aktiv: saga-autoupdate.timer (alle 15 Minuten)"

# 4. erstes Update sofort
echo "== erstes Update (kann ein paar Minuten dauern) ..."
systemctl start saga-autoupdate.service || true
PORT=$(sed -n 's/^HOST_PORT=//p' .env | tail -1); PORT=${PORT:-8080}
V=$(curl -sf "http://127.0.0.1:$PORT/api/version" 2>/dev/null || true)
echo "   /api/version: ${V:-nicht erreichbar - journalctl -t saga-autoupdate und docker compose logs saga pruefen}"
echo "== fertig. Protokoll: journalctl -t saga-autoupdate"
