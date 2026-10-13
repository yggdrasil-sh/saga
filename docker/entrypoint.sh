#!/bin/sh
# Baut ein CA-Bundle aus System-CAs + allen Zertifikaten in /certs (z.B. eigene Firmen-/LDAP-CA)
# und startet Hauptseite + Admin-Bereich. Laeuft als Non-Root, Root-FS darf read-only sein (/tmp ist tmpfs).
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
exec python /app/run.py
