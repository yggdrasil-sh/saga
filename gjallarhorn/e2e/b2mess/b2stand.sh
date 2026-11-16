#!/bin/sh
# B2-Messstand (nur Tests): Knoten (HORN_DEV, eigener TLS-Port fuer die Gegenstelle per TURN-TLS, internes HTTP node.sock)
# <- saga-media (MEDIA_NODE_SOCK, roher WS-Weg wie im Produkt) <- nginx (NPM-Nachbau, Websockets an; dazu /b2mess/ und
# /static/ des Repos) <- engpass (Leitung im Userspace, eine FIFO je Richtung fuer alle Verbindungen des Messgeraets).
#   B2M_DIR=/tmp/b2/stand B2M_BASE=8590 B2M_BIN=<Ordner mit gjallarhorn, saga-media, engpass> b2stand.sh start|stop|status
# Ports: +1 nginx (WS an) | +2 nginx (WS aus) | +3 saga-media | +4 Knoten (TLS, TURN-TLS) | +6 engpass -> nginx |
#        +7 engpass -> Knoten (TURN-TLS) | +8 engpass-Steuerung | +9 saga-media intern. Schluessel nur fuer den Pruefstand.
set -u
D=${B2M_DIR:-/tmp/b2/stand}; B=${B2M_BASE:-8590}; BIN=${B2M_BIN:-$D}
HERE=$(cd "$(dirname "$0")" && pwd); REPO=$(cd "$HERE/../../.." && pwd)
K=b2stand0123456789abcdef
case "${1:-status}" in
start)
  mkdir -p $D/hdata $D/run $D/nginx/conf $D/nginx/logs $D/big
  rm -f $D/node.sock
  [ -f $D/big/blob.bin ] || head -c 100000000 /dev/urandom > $D/big/blob.bin
  [ -f $D/nginx/cert.pem ] || openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout $D/nginx/key.pem \
    -out $D/nginx/cert.pem -days 30 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null
  sed -e "s#{DIR}#$D/nginx#g" -e "s#{NGX}#$((B+1))#g" -e "s#{NGX_OFF}#$((B+2))#g" -e "s#{UPSTREAM}#127.0.0.1:$((B+3))#g" \
      -e "s#{WSB}#$HERE#g" -e "s#{STATIC}#$REPO/static#g" -e "s#{BIG}#$D/big#g" "$HERE/nginx.conf.tmpl" > $D/nginx/conf/nginx.conf
  env HORN_DEV=1 HORN_UDP=0 HORN_TLS_SELF=1 HORN_NAME=localhost HORN_LISTEN=127.0.0.1:$((B+4)) HORN_DATA=$D/hdata HORN_ICE_IPS=127.0.0.1 \
    HORN_INNER_SOCK=$D/node.sock HORN_CTL_KEY=$K HORN_DEBUG=${HORN_DEBUG:-0} nohup $BIN/gjallarhorn node > $D/run/node.log 2>&1 & echo $! > $D/run/node.pid
  sleep 1
  env SAGA_MEDIA_KEY=$K$K PORT=$((B+3)) HOST=127.0.0.1 MEDIA_INTERNAL_PORT=$((B+9)) STATE_DIR=$D/st MEDIA_NODE_SOCK=$D/node.sock \
    HORN_CTL_KEY=$K TRUSTED_PROXIES=127.0.0.1 nohup $BIN/saga-media > $D/run/media.log 2>&1 & echo $! > $D/run/media.pid
  nginx -p $D/nginx -c $D/nginx/conf/nginx.conf
  nohup $BIN/engpass -map 127.0.0.1:$((B+6))=127.0.0.1:$((B+1)),127.0.0.1:$((B+7))=127.0.0.1:$((B+4)) -up 0 -down 0 -rtt 0 -qms 300 \
    -ctl 127.0.0.1:$((B+8)) > $D/run/shaper.log 2>&1 & echo $! > $D/run/shaper.pid
  sleep 1; "$0" status
  curl -sk -o /dev/null -w "ohne Upgrade: %{http_code} (erwartet 426)\n" https://127.0.0.1:$((B+1))/api/sfu/ws ;;
stop)
  for p in node media shaper; do [ -f $D/run/$p.pid ] && kill "$(cat $D/run/$p.pid)" 2>/dev/null; rm -f $D/run/$p.pid; done
  [ -f $D/nginx/nginx.pid ] && kill "$(cat $D/nginx/nginx.pid)" 2>/dev/null; true ;;
status)
  for p in node media shaper; do [ -f $D/run/$p.pid ] && kill -0 "$(cat $D/run/$p.pid)" 2>/dev/null && echo "$p laeuft" || echo "$p AUS"; done
  [ -f $D/nginx/nginx.pid ] && kill -0 "$(cat $D/nginx/nginx.pid)" 2>/dev/null && echo "nginx laeuft" || echo "nginx AUS" ;;
esac
