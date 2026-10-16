#!/bin/sh
# Test-Stack auf einem Docker-Host per SSH: Repo hinkopieren, dort tools/stack-remote.sh ausfuehren
# (Stack bauen + alle Tests). Aufruf:
#   SAGA_SSH=root@dockerhost tools/test-stack.sh [nur-deploy]
#   Docker in einem Proxmox-Container ohne eigenes SSH:  SAGA_SSH=root@proxmox SAGA_PCT=<CT-ID> tools/test-stack.sh
# Optional: SAGA_DIR (Verzeichnis auf dem Host, Standard /root/saga), SAGA_SCRIPT (Standard stack-remote.sh),
#   SAGA_PORT (zweiter Stack neben einem anderen: eigener Port, Admin = Port+1 nur lokal, siehe stack-remote.sh),
#   SAGA_TESTS (nur diese Testreihen, z. B. "avatare chat")
set -e
cd "$(dirname "$0")/.."
: "${SAGA_SSH:?SAGA_SSH=user@host setzen}"
DIR=${SAGA_DIR:-/root/saga}; SCRIPT=${SAGA_SCRIPT:-stack-remote.sh}
TGZ=/tmp/saga-src-$(basename "$DIR").tgz
COPYFILE_DISABLE=1 tar --exclude .git --exclude .env --exclude __pycache__ -czf "$TGZ" .
scp -q -o BatchMode=yes "$TGZ" "$SAGA_SSH":"$TGZ"   # eigener Name je Verzeichnis: zwei Stacks gleichzeitig
RUN="mkdir -p $DIR && cd $DIR && tar xzf $TGZ 2>/dev/null && rm $TGZ && SAGA_PORT=$SAGA_PORT SAGA_TESTS=\"$SAGA_TESTS\" bash tools/$SCRIPT $1 </dev/null"
if [ -n "$SAGA_PCT" ]; then
  ssh -o BatchMode=yes "$SAGA_SSH" "pct push $SAGA_PCT $TGZ $TGZ && rm $TGZ && pct exec $SAGA_PCT -- bash -c '$RUN'"
else
  ssh -o BatchMode=yes "$SAGA_SSH" "bash -c '$RUN'"
fi 2>&1 | grep -v -i -E "Pseudo|deprecat"
