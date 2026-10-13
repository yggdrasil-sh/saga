#!/bin/sh
# Test-Stack auf einem Docker-Host per SSH: Repo hinkopieren, dort tools/stack-remote.sh ausfuehren
# (Stack bauen + alle Tests). Aufruf:
#   SAGA_SSH=root@dockerhost tools/test-stack.sh [nur-deploy]
#   Docker in einem Proxmox-Container ohne eigenes SSH:  SAGA_SSH=root@proxmox SAGA_PCT=<CT-ID> tools/test-stack.sh
# Optional: SAGA_DIR (Verzeichnis auf dem Host, Standard /root/saga), SAGA_SCRIPT (Standard stack-remote.sh)
set -e
cd "$(dirname "$0")/.."
: "${SAGA_SSH:?SAGA_SSH=user@host setzen}"
DIR=${SAGA_DIR:-/root/saga}; SCRIPT=${SAGA_SCRIPT:-stack-remote.sh}
COPYFILE_DISABLE=1 tar --exclude .git --exclude .env --exclude __pycache__ -czf /tmp/saga-src.tgz .
scp -q -o BatchMode=yes /tmp/saga-src.tgz "$SAGA_SSH":/tmp/saga-src.tgz
RUN="mkdir -p $DIR && cd $DIR && tar xzf /tmp/saga-src.tgz 2>/dev/null && rm /tmp/saga-src.tgz && bash tools/$SCRIPT $1 </dev/null"
if [ -n "$SAGA_PCT" ]; then
  ssh -o BatchMode=yes "$SAGA_SSH" "pct push $SAGA_PCT /tmp/saga-src.tgz /tmp/saga-src.tgz && rm /tmp/saga-src.tgz && pct exec $SAGA_PCT -- bash -c '$RUN'"
else
  ssh -o BatchMode=yes "$SAGA_SSH" "bash -c '$RUN'"
fi 2>&1 | grep -v -i -E "Pseudo|deprecat"
