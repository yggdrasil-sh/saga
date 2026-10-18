#!/bin/sh
# Version eines Testing-Builds (Release Candidate) ausgeben, z. B. "2.2.0-rc.7+1cc210f":
#   Basis  = VERSION im Repo (die naechste geplante Version). Ist sie nicht neuer als der letzte Release-Tag
#            (z. B. direkt nach einem Release, bevor VERSION hochgesetzt wurde), wird die Patch-Stelle erhoeht -
#            ein RC ist so immer erkennbar neuer als die stabile Version.
#   rc.N   = Commits seit dem letzten Release-Tag v<x.y.z> (ohne Zusatz) bis HEAD
#   +SHA   = Kurz-SHA des Commits (Build-Kennung, zaehlt beim Vergleich nicht)
# Ohne Tags im Clone (flacher Checkout): "2.2.0-rc+1cc210f". Aufruf: tools/rc-version.sh [<commit>]
set -eu
cd "$(dirname "$0")/.."
REV="${1:-HEAD}"
SHORT="$(git rev-parse --short=7 "$REV")"
BASE="$( (git show "$REV:VERSION" 2>/dev/null || cat VERSION) | tr -d ' \r\n')"
BASE="${BASE%%-*}"; BASE="${BASE%%+*}"

TAG="$(git tag --merged "$REV" --list 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null \
       | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 || true)"
if [ -z "$TAG" ]; then
  echo "${BASE}-rc+${SHORT}"
  exit 0
fi

# BASE <= hoechster Release-Tag?  -> dieser Tag mit erhoehter Patch-Stelle als Basis. Verglichen wird mit allen
# Release-Tags, auch einem, der noch nicht nach testing zurueckgefuehrt ist (Release auf main vor dem Merge) -
# der RC soll neuer sein als jedes veroeffentlichte Release.
HI="$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null \
      | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sed 's/^v//' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1 || true)"
[ -n "$HI" ] || HI="$TAG"
MAX="$(printf '%s\n%s\n' "$BASE" "$HI" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)"
if [ "$MAX" = "$HI" ]; then
  BASE="$(echo "$HI" | awk -F. '{ printf "%d.%d.%d", $1, $2, $3 + 1 }')"
fi
N="$(git rev-list --count "v${TAG}..${REV}")"
echo "${BASE}-rc.${N}+${SHORT}"
