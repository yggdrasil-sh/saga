#!/bin/bash
# A3-Messstand (nur Tests): Knoten (HORN_DEV, eigener Port fuer die Gegenstelle per TURN-TLS, internes HTTP node.sock)
# <- saga-media (MEDIA_NODE_SOCK, roher WS-Weg) <- nginx (NPM-Nachbau, e2e/npm-nginx.conf.tmpl, Websockets an/aus)
# <- engpass (Leitung im Userspace). Messlauf: e2e/wsmess.mjs (Ports dort anpassen, Standard A3_BASE=8521).
#   A3_DIR=/tmp/a3-stand A3_BASE=8521 A3_BIN=<Ordner mit gjallarhorn, saga-media, engpass> e2e/a3stand.sh start|stop
# Ports: BASE nginx (WS an), +1 saga-media, +2 Knoten (TCP), +3 nginx (WS aus), +4 engpass -> nginx, +7 engpass-Steuerung,
#        +8 engpass -> Knoten. Schluessel nur fuer den Pruefstand.
set -e
D=${A3_DIR:-/tmp/a3-stand}; B=${A3_BASE:-8521}; BIN=${A3_BIN:-$D}; mkdir -p $D
K=a3stand$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')
case "$1" in
start)
  rm -f $D/node.sock $D/pids
  [ -f $D/cert.pem ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout $D/key.pem -out $D/cert.pem \
    -days 30 -subj "/CN=saga.example.org" -addext "subjectAltName=DNS:saga.example.org,IP:127.0.0.1" 2>/dev/null
  sed -e "s#{DIR}#$D#g" -e "s#{NGX}#$B#g" -e "s#{NGX_OFF}#$((B+3))#g" -e "s#{UPSTREAM}#127.0.0.1:$((B+1))#g" \
    "$(dirname "$0")/npm-nginx.conf.tmpl" > $D/nginx.conf
  cd $D
  env HORN_DEV=1 HORN_TLS_SELF=1 HORN_NAME=localhost HORN_UDP=0 HORN_ICE_IPS=127.0.0.1 HORN_LISTEN=127.0.0.1:$((B+2)) \
     HORN_INNER_SOCK=$D/node.sock HORN_CTL_KEY=$K HORN_DATA=$D/horn nohup $BIN/gjallarhorn node > $D/node.log 2>&1 &
  echo "node $!" >> $D/pids; sleep 1
  env SAGA_MEDIA_KEY=$K$K MEDIA_INTERNAL_PORT=9 PORT=$((B+1)) MEDIA_NODE_SOCK=$D/node.sock HORN_CTL_KEY=$K \
     TRUSTED_PROXIES=127.0.0.1 nohup $BIN/saga-media > $D/media.log 2>&1 &
  echo "media $!" >> $D/pids
  nginx -c $D/nginx.conf; sleep 0.3; echo "nginx $(cat $D/nginx.pid)" >> $D/pids
  nohup $BIN/engpass -map 127.0.0.1:$((B+4))=127.0.0.1:$B,127.0.0.1:$((B+8))=127.0.0.1:$((B+2)) -up 0 -down 0 -rtt 20 -qms 200 \
     -ctl 127.0.0.1:$((B+7)) > $D/engpass.log 2>&1 &
  echo "engpass $!" >> $D/pids
  sleep 1; curl -sk -o /dev/null -w "ohne Upgrade: %{http_code} (erwartet 426)\n" https://127.0.0.1:$B/api/sfu/ws ;;
stop)
  [ -f $D/pids ] && while read n p; do kill $p 2>/dev/null || true; done < $D/pids; rm -f $D/pids ;;
*) echo "a3stand.sh start|stop" ;;
esac
