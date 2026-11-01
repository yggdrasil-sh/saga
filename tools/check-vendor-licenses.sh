#!/bin/bash
# Prueft die mitgelieferten Fremdbibliotheken unter static/vendor/ (laeuft in der CI, .github/workflows/testing.yml, Job image).
#   1. npm ci --ignore-scripts in tools/vendor-tiptap (exakt nach package-lock, nur registry.npmjs.org)
#   2. license-checker-rseidelsohn: Produktionsabhaengigkeiten nur unter MIT/ISC/BSD-2/BSD-3/Apache-2.0
#   3. Sperrliste: @tiptap-pro/, @blocknote/, @hocuspocus/, UNLICENSED, "SEE LICENSE IN" -> Abbruch
#   4. Neubau mit build.sh in ein Temp-Verzeichnis, SHA-256 von saga-pm.js/saga-y.js/saga-pm.css muss gleich sein
#      (und der restliche Ordner - licenses/, VERSIONS.txt, README - byteweise gleich)
#   5. jede in VERSIONS.txt genannte (gebuendelte) Lizenz liegt in licenses/; kein eval/new Function in den Bundles
#   6. Grundpruefung editorjs/katex/mermaid/libsodium: VERSIONS.txt nennt nur bekannte Pakete mit erwarteter Lizenz,
#      Lizenzdateien vorhanden
# Aufruf aus dem Repo-Wurzelverzeichnis: bash tools/check-vendor-licenses.sh   (node >= 22, npm)
# Exitcode 0 = alles in Ordnung, sonst Anzahl/Art der Verstoesse auf stderr.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VT="$ROOT/tools/vendor-tiptap"
OUT="$ROOT/static/vendor/tiptap"
ALLOW="MIT;ISC;BSD-2-Clause;BSD-3-Clause;Apache-2.0"
FAIL=0
fail() { echo "FEHLER: $*" >&2; [ -n "${GITHUB_ACTIONS:-}" ] && echo "::error::$*"; FAIL=$((FAIL + 1)); }
ok() { echo "ok    $*"; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# --- 1. Installation exakt nach Lock ----------------------------------------------------------------------------------
cd "$VT" || exit 2
if grep -o '"resolved": "[^"]*"' package-lock.json | grep -v '"https://registry.npmjs.org/' >/dev/null; then
  fail "package-lock.json verweist auf andere Quellen als registry.npmjs.org"
fi
if grep -q '"integrity"' package-lock.json && [ "$(grep -c '"resolved"' package-lock.json)" = "$(grep -c '"integrity"' package-lock.json)" ]; then
  ok "package-lock: jedes Paket mit integrity-Hash"
else
  fail "package-lock: Pakete ohne integrity-Hash"
fi
node -e '
  const p = require("./package.json"); const bad = [];
  for (const s of ["dependencies", "devDependencies"]) for (const [k, v] of Object.entries(p[s] || {})) if (!/^\d+\.\d+\.\d+$/.test(v)) bad.push(k + "@" + v);
  if (bad.length) { console.error("nicht exakt gepinnt: " + bad.join(", ")); process.exit(1) }' || fail "package.json: Versionen nicht exakt gepinnt"
for pat in '@tiptap-pro/' '@blocknote/' '@hocuspocus/'; do   # Sperrliste schon am Lock (vor jeder Installation)
  grep -q "\"node_modules/[^\"]*$pat\|\"$pat[^\"]*\": \"" package.json package-lock.json && { fail "gesperrtes Paket $pat im Lock/package.json"; exit 1; }
done
npm ci --ignore-scripts --no-audit --no-fund --loglevel=error >"$TMP/npm.log" 2>&1 || { cat "$TMP/npm.log" >&2; fail "npm ci fehlgeschlagen"; exit 1; }
ok "npm ci --ignore-scripts"

# --- 2. Lizenzen der Produktionsabhaengigkeiten ----------------------------------------------------------------------
LC=./node_modules/.bin/license-checker-rseidelsohn
if $LC --production --excludePrivatePackages --onlyAllow "$ALLOW" --summary >"$TMP/lc.txt" 2>&1; then
  ok "Lizenzen nur $ALLOW: $(tr -d '\n' < "$TMP/lc.txt" | sed 's/[^A-Za-z0-9:;.,() -]//g; s/  */ /g')"
else
  cat "$TMP/lc.txt" >&2; fail "license-checker: nicht erlaubte Lizenz"
fi
$LC --production --excludePrivatePackages --json >"$TMP/lc.json" 2>/dev/null || fail "license-checker --json fehlgeschlagen"

# --- 3. Sperrliste ---------------------------------------------------------------------------------------------------
for pat in '@tiptap-pro/' '@blocknote/' '@hocuspocus/'; do
  if grep -q "\"node_modules/[^\"]*$pat" package-lock.json || grep -q "\"$pat" "$TMP/lc.json"; then fail "gesperrtes Paket $pat"; fi
done
for pat in 'UNLICENSED' 'SEE LICENSE IN'; do
  if grep -qi "$pat" "$TMP/lc.json"; then fail "gesperrte Lizenzangabe \"$pat\""; fi
done
[ "$FAIL" = 0 ] && ok "Sperrliste (@tiptap-pro, @blocknote, @hocuspocus, UNLICENSED, SEE LICENSE IN)"

# --- 4. Neubau und Hash-Vergleich ------------------------------------------------------------------------------------
if bash "$VT/build.sh" "$TMP/rebuild" >"$TMP/build.log" 2>&1; then
  for f in saga-pm.js saga-y.js saga-pm.css; do
    a="$(sha256sum "$OUT/$f" 2>/dev/null | cut -d' ' -f1)"; b="$(sha256sum "$TMP/rebuild/$f" | cut -d' ' -f1)"
    if [ -n "$a" ] && [ "$a" = "$b" ]; then ok "$f reproduzierbar (sha256 $a)"; else fail "$f weicht vom Neubau ab (eingecheckt ${a:-fehlt}, Neubau $b) - bash tools/vendor-tiptap/build.sh"; fi
  done
  if diff -r "$OUT" "$TMP/rebuild" >"$TMP/diff.txt" 2>&1; then ok "licenses/, VERSIONS.txt, README gleich dem Neubau"
  else head -20 "$TMP/diff.txt" >&2; fail "static/vendor/tiptap weicht vom Neubau ab"; fi
  grep -E '^saga-' "$TMP/build.log" | sed 's/^/      /'
else
  cat "$TMP/build.log" >&2; fail "build.sh fehlgeschlagen"
fi

# --- 5. Lizenzdateien der gebuendelten Pakete, CSP -------------------------------------------------------------------
n=0
while IFS= read -r line; do
  name="${line%%@[0-9]*}"; lic="$(echo "$line" | sed -n 's/^[^ ]* (\([^)]*\)).*/\1/p')"
  n=$((n + 1))
  f="$OUT/licenses/$(echo "$name" | sed 's/^@//; s#/#__#').LICENSE"
  [ -s "$f" ] || fail "Lizenzdatei fehlt: $f"
  case ";$ALLOW;" in *";$lic;"*) ;; *) fail "$name: Lizenz $lic nicht erlaubt";; esac
  node -e 'const j=require(process.argv[1]);process.exit(Object.keys(j).some(k=>k.replace(/@[^@]+$/,"")===process.argv[2])?0:1)' "$TMP/lc.json" "$name" \
    || fail "$name ist gebuendelt, aber keine Produktionsabhaengigkeit laut license-checker"
done < <(grep -E '^[@a-z0-9].*@[0-9][^ ]* \(' "$OUT/VERSIONS.txt")
[ "$n" -gt 0 ] && ok "$n gebuendelte Pakete, je mit Lizenzdatei in licenses/" || fail "VERSIONS.txt ohne Paketliste"
for f in "$OUT"/saga-pm.js "$OUT"/saga-y.js; do
  if grep -qE '\beval\(|new Function\(' "$f"; then fail "$(basename "$f") enthaelt eval/new Function (CSP)"; fi
done
grep -qE 'Yjs was already imported|__ \$YJS\$ __' "$OUT/saga-pm.js" && fail "saga-pm.js enthaelt yjs (gehoert nur in saga-y.js)"
ok "kein eval/new Function, yjs nur im Live-Bundle"

# --- 6. Grundpruefung der uebrigen Vendor-Ordner ---------------------------------------------------------------------
check_known() {   # Ordner, erwartete Lizenzdatei(en), dann "paket=lizenz"-Liste
  local nm="$1" dir="$ROOT/static/vendor/$1" lic="$2"; shift 2
  [ -f "$dir/VERSIONS.txt" ] || { fail "$nm: VERSIONS.txt fehlt"; return; }
  for l in $lic; do [ -s "$dir/$l" ] || fail "$nm: Lizenzdatei $l fehlt"; done
  local found
  found="$(grep -oE '^(@[a-z0-9-]+/)?[a-z0-9.-]+@[0-9][0-9.]*( \([A-Za-z0-9.-]+\))?' "$dir/VERSIONS.txt" | sed 's/@[0-9][0-9.]*//')"
  while IFS= read -r e; do
    [ -z "$e" ] && continue
    local pk="${e%% *}" li; li="$(echo "$e" | sed -n 's/.*(\(.*\))/\1/p')"
    local want=""; for kv in "$@"; do [ "${kv%%=*}" = "$pk" ] && want="${kv#*=}"; done
    if [ -z "$want" ]; then fail "$nm: unbekanntes Paket $pk in VERSIONS.txt - Liste in check-vendor-licenses.sh ergaenzen"
    elif [ -n "$li" ] && [ "$li" != "$want" ]; then fail "$nm: $pk unter $li statt $want"; fi
  done <<< "$found"
  ok "$nm: $(echo "$found" | grep -c .) bekannte Pakete"
}
check_known editorjs "licenses/editorjs.LICENSE licenses/header.LICENSE licenses/list.LICENSE licenses/quote.LICENSE licenses/code.LICENSE licenses/delimiter.LICENSE licenses/inline-code.LICENSE licenses/marker.LICENSE licenses/table.LICENSE licenses/image.LICENSE licenses/attaches.LICENSE licenses/editorjs-drag-drop.LICENSE" \
  @editorjs/editorjs=Apache-2.0 @editorjs/header=MIT @editorjs/list=MIT @editorjs/quote=MIT @editorjs/code=MIT \
  @editorjs/delimiter=MIT @editorjs/inline-code=MIT @editorjs/marker=MIT @editorjs/table=MIT @editorjs/image=MIT \
  @editorjs/attaches=MIT editorjs-drag-drop=MIT
check_known katex LICENSE katex=MIT
check_known mermaid LICENSE mermaid=MIT
check_known libsodium LICENSE libsodium-sumo=ISC libsodium-wrappers-sumo=ISC
for d in "$ROOT"/static/vendor/*/; do
  case "$(basename "$d")" in editorjs|katex|mermaid|libsodium|tiptap) ;; *) fail "unbekannter Vendor-Ordner $(basename "$d") - in check-vendor-licenses.sh aufnehmen";; esac
done

if [ "$FAIL" != 0 ]; then echo "check-vendor-licenses: $FAIL Verstoss/Verstoesse" >&2; exit 1; fi
echo "check-vendor-licenses: alles in Ordnung"
