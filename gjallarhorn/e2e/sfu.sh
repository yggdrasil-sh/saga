#!/bin/sh
# Ende-zu-Ende-Lauf des Weiterleiters: Knoten (node, HORN_DEV=1, nur TCP)
# mit selbst signiertem Zertifikat auf einem hohen Port, 4 Chromium-Kontexte.
# Voraussetzungen: Go, Node mit Playwright, Chromium (PLAYWRIGHT_BROWSERS_PATH).
set -eu
cd "$(dirname "$0")/.."
port="${PORT:-18543}"
tmp="$(mktemp -d)"
go build -o "$tmp/gjallarhorn" .
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT INT TERM
HORN_TAILPROBE_MS="${HORN_TAILPROBE_MS:-10}" HORN_DEV=1 HORN_UDP=0 HORN_TLS_SELF=1 HORN_LISTEN=":$port" HORN_DATA="$tmp" HORN_ICE_IPS=127.0.0.1 \
  "$tmp/gjallarhorn" node >"$tmp/node.log" 2>&1 &
pid="$!"
sleep 1
# Ein gesetzter HTTP-Proxy wuerde ICE-TCP aus Chromium per CONNECT tunneln.
rc=0
env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy node e2e/sfu.mjs "https://localhost:$port" || rc=$?
[ "$rc" = 0 ] || tail -20 "$tmp/node.log" >&2
exit $rc
