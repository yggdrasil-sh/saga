#!/bin/sh
# Baut ein CA-Bundle aus System-CAs + allen Zertifikaten in /certs (z.B. eigene Firmen-/LDAP-CA)
# und startet Hauptseite (ueber saga-media) + Admin-Bereich. Laeuft als Non-Root, Root-FS darf read-only sein (/tmp ist tmpfs).
set -eu

BUNDLE=/tmp/ca-bundle.pem
cat /etc/ssl/certs/ca-certificates.crt > "$BUNDLE"

n=0
for f in /certs/*.crt /certs/*.pem; do
  [ -f "$f" ] || continue
  cat "$f" >> "$BUNDLE"
  printf '\n' >> "$BUNDLE"
  n=$((n + 1))
  echo "saga: zusaetzliche CA geladen: $f"
done
[ "$n" -gt 0 ] || echo "saga: keine zusaetzliche CA in /certs - nur System-CAs"

export CA_FILE="$BUNDLE" SSL_CERT_FILE="$BUNDLE"

# Mit Argumenten (z. B. "docker compose run saga python /tools/...") diese ausfuehren - mit gleichem CA-Bundle
[ "$#" -gt 0 ] && exec "$@"

# Saga 2.4: saga-media lauscht auf PORT, liefert Chat-Anhaenge direkt von der Platte (Range, ohne Python) und leitet
# alles andere an Saga weiter; es startet Saga selbst und endet mit ihm. SAGA_MEDIA=off: Saga direkt wie bis 2.3.
if [ "${SAGA_MEDIA:-on}" != "off" ] && command -v saga-media >/dev/null 2>&1; then
  exec saga-media -- python /app/run.py
fi
exec python /app/run.py
