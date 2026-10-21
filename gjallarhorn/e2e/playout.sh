#!/bin/sh
# playout-delay im Browser nachweisen: Knoten einmal ohne (HORN_PLAYOUT_DELAY=0) und einmal mit Standard (150,400).
set -eu
cd "$(dirname "$0")/.."
port="${PORT:-18544}"
tmp="$(mktemp -d)"
go build -o "$tmp/gjallarhorn" .
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT INT TERM
for pd in 0 150,400; do
  HORN_PLAYOUT_DELAY="$pd" HORN_DEV=1 HORN_UDP=0 HORN_TLS_SELF=1 HORN_LISTEN=":$port" HORN_DATA="$tmp" HORN_ICE_IPS=127.0.0.1 \
    "$tmp/gjallarhorn" node >"$tmp/node.log" 2>&1 &
  pid="$!"
  sleep 1
  printf 'HORN_PLAYOUT_DELAY=%s ' "$pd"
  env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy node e2e/playout.mjs "https://localhost:$port"
  kill "$pid"; wait "$pid" 2>/dev/null || true; pid=""
done
