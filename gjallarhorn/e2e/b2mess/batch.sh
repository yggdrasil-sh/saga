#!/bin/bash
# Kernmessungen B2 (nur Tests): je Bedingung Messgeraet ws bzw. turn ueber den engpass, Gegenstelle turn direkt, n Laeufe.
#   B2M_BIN=<bin> [B2M_DIR] [B2M_BASE] [N=2] [DUR=30] batch.sh frei mbit06 rtt150 loss1 ...
# Jeder Lauf unter flock /tmp/ws-mess.lock (2 Kerne, andere Agenten); Rohdaten $B2M_DIR/runs/<tag>.json, Tabelle table.py.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
D=${B2M_DIR:-/tmp/b2/stand}; N=${N:-2}; DUR=${DUR:-30}
declare -A SET=( [frei]="down=0&up=0&rtt=0&qms=300&loss=0" [mbit06]="down=600&up=0&rtt=50&qms=300&loss=0"
                 [rtt150]="down=0&up=0&rtt=150&qms=300&loss=0" [loss1]="down=0&up=0&rtt=50&qms=300&loss=0.01" )
export WSB_FLOCK=1 B2M_DIR=$D NODE_PATH=${NODE_PATH:-$(npm root -g)}

for c in "$@"; do
  for i in $(seq 1 $N); do
    for p in ws turn; do
      tag="$c-$p-$i"
      flock /tmp/ws-mess.lock sh -c "env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy NO_PROXY='*' \
        node $HERE/call.mjs '{\"who\":[{\"path\":\"$p\",\"via\":\"shaper\"},{\"path\":\"turn\"}],\"dur\":$DUR,\"warm\":8,\"set\":\"${SET[$c]}\",\"tag\":\"$tag\"}'" \
        > $D/runs/$tag.out 2> $D/runs/$tag.err || echo "$tag FEHLER"
      echo "$tag fertig"
    done
  done
done
