#!/bin/bash
# Auf dem Docker-Host im Repo-Verzeichnis (ueber tools/test-stack.sh mit SAGA_SCRIPT=screens-remote.sh):
#   1. PNG-Symbole aus static/icon.svg erzeugen
#   2. Eigenen, leeren Wegwerf-Stack (Projekt saga-shots, ohne Ports) mit Demo-Daten starten
#   3. Screenshots nach docs/screenshots/, Wegwerf-Stack samt Daten wieder entfernen
set -e
cd "$(dirname "$0")/.."
PWIMG=mcr.microsoft.com/playwright/python:v1.48.0-noble
PWRUN="pip install -q --break-system-packages playwright==1.48.0 >/dev/null 2>&1 || pip install -q playwright==1.48.0 >/dev/null 2>&1"
docker run --rm -v "$PWD:/repo" "$PWIMG" sh -c "$PWRUN; python /repo/tools/render-icons.py"

rnd() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
cat > .env.shots <<EOF
BASE_URL=http://saga:8080
ADMIN_URL=http://saga:8081
SECRET_KEY=$(rnd 32)
POSTGRES_PASSWORD=$(rnd 16)
CANONICAL_REDIRECT=false
ADMIN_USERNAME=demo-admin
ADMIN_PASSWORD=
EOF
DC="docker compose -p saga-shots --env-file .env.shots -f docker-compose.yml -f tools/compose.shots.yml"
$DC down -v >/dev/null 2>&1 || true
$DC up -d --build 2>&1 | tail -2
C=$($DC ps -q saga)
for i in $(seq 1 40); do
  docker exec "$C" python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" </dev/null 2>/dev/null && break
  sleep 2
done
NET=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$C")
SHOT_PW=$(rnd 12)
docker exec -i -e SHOT_PW="$SHOT_PW" "$C" python - <<'PY'
import os, sys, time
sys.path.insert(0, "/app")
import auth
from db import connect
pw = os.environ["SHOT_PW"]
auth.reset_admin("demo-admin"); auth.set_password("demo-admin", pw)
auth.create_user("demo", pw, "Alex Beispiel", "alex@example.org", groups=["s_nextcloud", "s_immich", "s_jellyfin", "s_paperless", "familie"])
for n in ("kim", "robin", "sam"):
    auth.create_user(n, None, n.title() + " Beispiel", f"{n}@example.org", groups=["familie"])
auth.save_setting("registration", True)
svcs = [("nextcloud", "Nextcloud", "https://cloud.example.org", "nextcloud", "Dateien", "[]", 0),
        ("paperless", "Paperless", "https://docs.example.org", "paperless-ngx", "Dateien", "[]", 0),
        ("immich", "Immich", "https://fotos.example.org", "immich", "Medien", "[]", 0),
        ("jellyfin", "Jellyfin", "https://kino.example.org", "jellyfin", "Medien", "[]", 0),
        ("vaultwarden", "Vaultwarden", "https://vault.example.org", "vaultwarden", "Sicherheit", "[]", 1),
        ("homeassistant", "Home Assistant", "https://ha.example.org", "home-assistant", "Zuhause", '["familie"]', 0),
        ("grafana", "Grafana", "https://grafana.example.org", "grafana", "Betrieb", '["admins"]', 0)]
with connect() as c, c:
    for slug, name, url, icon, cat, extra, every in svcs:
        c.execute("INSERT INTO service (slug, name, url, icon, category, extra_groups, everyone, check_status, sort, created) "
                  "VALUES (?,?,?,?,?,?,?,0,0,?)", (slug, name, url, icon, cat, extra, every, time.time()))
        auth.ensure_group(c, "s_" + slug, f"Zugang zu {name}")
    for i, (n, u, ic, cat) in enumerate([("Router", "https://192.168.1.1", "openwrt", "Netzwerk"), ("Drucker", "http://drucker.lan", "", "Netzwerk"),
                                         ("Wetter", "https://www.dwd.de", "", "Lesezeichen"), ("Pi-hole", "http://pihole.lan/admin", "pi-hole", "Netzwerk")]):
        c.execute("INSERT INTO bookmark (uid, name, url, icon, category, sort, created) VALUES (?,?,?,?,?,?,?)", ("demo", n, u, ic, cat, i, time.time()))
    c.execute("INSERT INTO fav (uid, grp, ts) VALUES (?,?,?)", ("demo", "s_nextcloud", time.time()))
    for t, p in (("Steuererklärung abgeben", 1), ("Backup prüfen", 2), ("Geschenk für Oma", 2), ("Fahrrad zur Inspektion", 3)):
        c.execute("INSERT INTO task (uid, text, done, created, prio) VALUES (?,?,0,?,?)", ("demo", t, time.time(), p))
print("demo ok")
PY
docker restart "$C" >/dev/null; sleep 8       # Zwischenspeicher der laufenden Instanz leeren
rm -rf docs/screenshots && mkdir -p docs/screenshots && chmod 777 docs/screenshots
docker run --rm --network "$NET" -v "$PWD/docs/screenshots:/out" -v "$PWD/tools/screens.py:/screens.py:ro" \
  -e BASE=http://saga:8080 -e ADMIN=http://saga:8081 -e SHOT_PW="$SHOT_PW" "$PWIMG" sh -c "$PWRUN; python /screens.py" 2>&1 | tail -25
$DC down -v >/dev/null 2>&1
rm -f .env.shots
ls docs/screenshots
tar czf /tmp/saga-assets.tgz static/icon-192.png static/icon-512.png static/apple-touch-icon.png docs/screenshots
