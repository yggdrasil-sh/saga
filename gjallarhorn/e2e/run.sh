#!/bin/sh
# Lokaler Ende-zu-Ende-Lauf: Probe mit selbst signiertem Zertifikat auf
# einem hohen Port, Messung in headless Chromium (Playwright).
#   e2e/run.sh            alle Wege direkt (UDP, ICE-TCP, TURN-TLS)
#   e2e/run.sh tls        "nur TLS": Mini-Proxy mit PROXY v2 davor, ICE-TCP/UDP
#                         unerreichbar (Kandidaten zeigen ins Leere), TURN-TLS muss gehen
# Voraussetzungen: Go, Node mit Playwright, Chromium (PLAYWRIGHT_BROWSERS_PATH).
set -eu
cd "$(dirname "$0")/.."
mode="${1:-direkt}"
dur="${DUR:-20}"
tmp="$(mktemp -d)"
go build -o "$tmp/gjallarhorn" .
go build -o "$tmp/miniproxy" ./e2e/miniproxy
pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null || true; done; rm -rf "$tmp"; }
trap cleanup EXIT INT TERM
if [ "$mode" = tls ]; then
  HORN_PROXY_PROTOCOL=auto HORN_PUBLIC_IP=192.0.2.123 HORN_TLS_SELF=1 HORN_LISTEN=:18443 HORN_DATA="$tmp" \
    "$tmp/gjallarhorn" probe >"$tmp/probe.log" 2>&1 &
  pids="$!"
  "$tmp/miniproxy" 127.0.0.1:18444 127.0.0.1:18443 >/dev/null 2>&1 &
  pids="$pids $!"
  url=https://localhost:18444/ expect=turn
else
  HORN_TLS_SELF=1 HORN_LISTEN=:18443 HORN_DATA="$tmp" "$tmp/gjallarhorn" probe >"$tmp/probe.log" 2>&1 &
  pids="$!"
  url=https://localhost:18443/ expect=tcp,turn
fi
sleep 1
# Ein gesetzter HTTP-Proxy wuerde ICE-TCP aus Chromium per CONNECT tunneln.
env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy HORN_EXPECT="$expect" \
  node e2e/probe.mjs "$url" udp,tcp,turn "$dur" >"$tmp/result.json"
rc=$?
"$tmp/gjallarhorn" report --lokal --data "$tmp"
exit $rc
