#!/bin/bash
# Holt libsodium.js (Sumo-Variante, enthaelt crypto_pwhash/Argon2id) von npm und legt es unter static/vendor/libsodium/ ab
# (keine CDNs). Gepinnte Versionen; Lizenz ISC (AGPL-kompatibel). Die Chat-Seite laedt zuerst libsodium-sumo.js
# (setzt window.libsodium, WebAssembly eingebettet), dann libsodium-wrappers.js (window.sodium).
# Aufruf aus dem Repo-Wurzelverzeichnis:  bash tools/vendor-libsodium.sh
set -euo pipefail
V=0.8.4
OUT=static/vendor/libsodium
[ -f app.py ] || { echo "Bitte im Repo-Wurzelverzeichnis ausfuehren"; exit 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
( cd "$W" && npm pack --silent "libsodium-sumo@$V" "libsodium-wrappers-sumo@$V" >/dev/null && for f in *.tgz; do mkdir -p "${f%.tgz}" && tar xzf "$f" -C "${f%.tgz}"; done )
rm -rf "$OUT"; mkdir -p "$OUT"
cp "$W/libsodium-sumo-$V/package/dist/modules-sumo/libsodium-sumo.js" "$OUT/libsodium-sumo.js"
cp "$W/libsodium-wrappers-sumo-$V/package/dist/modules-sumo/libsodium-wrappers.js" "$OUT/libsodium-wrappers.js"
cp "$W/libsodium-sumo-$V/package/LICENSE" "$OUT/LICENSE"
{ echo "libsodium-sumo@$V"; echo "libsodium-wrappers-sumo@$V"; echo "Quelle: npm (https://github.com/jedisct1/libsodium.js), Lizenz ISC"; } > "$OUT/VERSIONS.txt"
( cd "$OUT" && shasum -a 256 libsodium-sumo.js libsodium-wrappers.js >> VERSIONS.txt )
echo "fertig: $OUT"
