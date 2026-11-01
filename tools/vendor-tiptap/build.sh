#!/bin/bash
# Baut static/vendor/tiptap/ (saga-pm.js, saga-y.js, saga-pm.css, licenses/, VERSIONS.txt) reproduzierbar
# aus package-lock.json. Aufruf: bash tools/vendor-tiptap/build.sh [Zielordner]  (Standard: static/vendor/tiptap)
# Erwartet node >= 22. Installiert nur, wenn node_modules fehlt (npm ci, ohne Install-Skripte).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/../../static/vendor/tiptap}"
cd "$HERE"
[ -d node_modules ] || npm ci --ignore-scripts --no-audit --no-fund
# README (Exportvertrag) wird mitkopiert, damit der Zielordner vollstaendig aus dem Repo entsteht
node build.mjs "$OUT"
cp README.vendor "$OUT/README"
