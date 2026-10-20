#!/bin/sh
# Statische Binaerdateien fuer Linux amd64 und arm64 nach dist/ bauen.
# Braucht Go >= 1.24. Aufruf: ./build.sh [version]
set -eu
cd "$(dirname "$0")"
v="${1:-2.5.0-probe}"
mkdir -p dist
for a in amd64 arm64; do
  CGO_ENABLED=0 GOOS=linux GOARCH="$a" go build -trimpath -ldflags="-s -w -X main.version=$v" -o "dist/gjallarhorn-linux-$a" .
done
( cd dist && sha256sum gjallarhorn-linux-* > SHA256SUMS )
ls -l dist
