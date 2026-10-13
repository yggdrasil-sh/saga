#!/bin/bash
# Holt Editor.js + Block-Plugins von npm, legt sie lokal unter static/vendor/editorjs/ ab
# und macht sie CSP-tauglich (Saga erlaubt keine Inline-Styles):
#   1. Alle Styles, die die Pakete zur Laufzeit per <style> injizieren, werden einmalig
#      in einem Browser eingesammelt -> editorjs.css (als Datei ausgeliefert, CSP-konform)
#   2. Die Injektions-Vorspaenne (vite-plugin-css-injected-by-js) werden neutralisiert
#   3. Der Editor-Kern injiziert nur, wenn kein Element mit id="editor-js-styles" existiert;
#      pages.js legt die CSS-Datei mit genau dieser id an.
# Voraussetzungen: node/npm, Google Chrome (macOS-Pfad unten anpassen).
# Aufruf aus dem Repo-Wurzelverzeichnis:  bash tools/vendor-editorjs.sh
set -euo pipefail

CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
OUT=static/vendor/editorjs
PKGS=(
  "@editorjs/editorjs@2.31.7:editorjs.umd.js"
  "@editorjs/header@2.8.9:header.umd.js"
  "@editorjs/list@2.0.9:editorjs-list.umd.js"
  "@editorjs/quote@2.7.6:quote.umd.js"
  "@editorjs/code@2.9.4:code.umd.js"
  "@editorjs/delimiter@1.4.2:delimiter.umd.js"
  "@editorjs/inline-code@1.5.2:inline-code.umd.js"
  "@editorjs/marker@1.4.0:marker.umd.js"
  "@editorjs/table@2.4.6:table.umd.js"
  "@editorjs/image@2.10.3:image.umd.js"
  "@editorjs/attaches@1.3.2:attaches.umd.js"
  "editorjs-drag-drop@1.1.16:bundle.js:drag-drop.js"
)

[ -f app.py ] || { echo "Bitte im Repo-Wurzelverzeichnis ausfuehren"; exit 1; }
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
SPECS=(); for e in "${PKGS[@]}"; do SPECS+=("${e%%:*}"); done
( cd "$W" && npm init -y >/dev/null && npm i --silent --no-audit --no-fund "${SPECS[@]}" )

rm -rf "$OUT"; mkdir -p "$OUT/licenses" "$W/cap"
: > "$OUT/VERSIONS.txt"
for e in "${PKGS[@]}"; do
  spec=${e%%:*}; rest=${e#*:}; file=${rest%%:*}; dest=${rest#*:}; name=${spec%@*}
  src="$W/node_modules/$name/dist/$file"
  cp "$src" "$W/cap/$dest"
  cp "$src" "$OUT/$dest"
  lic=$(ls "$W"/node_modules/$name/LICENSE* 2>/dev/null | head -1 || true)
  [ -n "$lic" ] && cp "$lic" "$OUT/licenses/$(basename "$name").LICENSE"
  echo "$spec ($(node -p "require('$W/node_modules/$name/package.json').license"))" >> "$OUT/VERSIONS.txt"
done

# 1) Styles im Browser einsammeln (ohne CSP, Editor mit allen Werkzeugen starten)
cat > "$W/cap/cap.html" <<'H'
<!doctype html><html><head><meta charset="utf-8"></head><body><div id="ed"></div>
<script src="editorjs.umd.js"></script><script src="header.umd.js"></script><script src="editorjs-list.umd.js"></script>
<script src="quote.umd.js"></script><script src="code.umd.js"></script><script src="delimiter.umd.js"></script>
<script src="inline-code.umd.js"></script><script src="marker.umd.js"></script>
<script src="table.umd.js"></script><script src="image.umd.js"></script><script src="attaches.umd.js"></script><script src="drag-drop.js"></script>
<script>
var ed = new EditorJS({ holder: 'ed', tools: { header: Header, list: EditorjsList, quote: Quote, code: CodeTool,
  delimiter: Delimiter, inlineCode: InlineCode, marker: Marker, table: Table,
  image: { class: ImageTool, config: { endpoints: { byFile: '/x' } } }, attaches: { class: AttachesTool, config: { endpoint: '/x' } } },
  data: { blocks: [{ type: 'header', data: { text: 'x', level: 2 } }, { type: 'table', data: { content: [['a', 'b']] } }] } });
ed.isReady.then(function () { new DragDrop(ed); setTimeout(function () {
  var p = document.createElement('pre'); p.id = 'out';
  p.textContent = [].map.call(document.querySelectorAll('style'), function (s) { return '/* ' + (s.id || 'plugin') + ' */\n' + s.textContent; }).join('\n');
  document.body.appendChild(p); }, 500); });
</script></body></html>
H
"$CHROME" --headless=new --disable-gpu --allow-file-access-from-files --virtual-time-budget=5000 \
  --dump-dom "file://$W/cap/cap.html" 2>/dev/null > "$W/cap/dom.html"
python3 - "$W/cap/dom.html" "$OUT/editorjs.css" <<'PY'
import re, html, sys
d = open(sys.argv[1]).read()
m = re.search(r'<pre id="out">(.*?)</pre>', d, re.S)
if not m: sys.exit("FEHLER: keine Styles eingesammelt")
css = html.unescape(m.group(1))
open(sys.argv[2], "w").write("/* Editor.js-Styles, automatisch eingesammelt von tools/vendor-editorjs.sh - nicht von Hand aendern */\n" + css + "\n")
print("editorjs.css:", len(css), "Bytes,", css.count("/* "), "Bloecke")
PY

# 2) Injektions-Vorspaenne neutralisieren (Styles kommen aus editorjs.css)
for f in "$OUT"/*.umd.js; do
  n=$(grep -o 'if(typeof document<"u"){' "$f" | wc -l | tr -d ' ')
  sed -i.bak 's/if(typeof document<"u"){/if(false){\/*css: editorjs.css*\//g' "$f" && rm -f "$f.bak"
  echo "$(basename "$f"): $n Injektion(en) neutralisiert"
done

# 3) Eingebetteter webpack-style-loader (Benachrichtigungen, cdx-notify) im Kern:
#    <style> wird erzeugt, aber nicht mehr eingehaengt. Das CSS steckt bereits in editorjs.css.
#    Muss genau einmal vorkommen - sonst hat sich der Kern geaendert und das Skript bricht ab.
K="$OUT/editorjs.umd.js"
cnt=$(grep -o 'G(m,k.attrs),O(k,m),m}' "$K" | wc -l | tr -d ' ')
[ "$cnt" = "1" ] || { echo "FEHLER: style-loader-Stelle ${cnt}x gefunden (erwartet 1) - Skript an neue Editor.js-Version anpassen"; exit 1; }
grep -q 'cdx-notify' "$OUT/editorjs.css" || { echo "FEHLER: cdx-notify-CSS fehlt in editorjs.css"; exit 1; }
sed -i.bak 's/G(m,k.attrs),O(k,m),m}/G(m,k.attrs),m}/' "$K" && rm -f "$K.bak"
echo "editorjs.umd.js: style-loader neutralisiert"
D="$OUT/drag-drop.js"
cnt=$(grep -o 'return e.setAttributes(t,e.attributes),e.insert(t,e.options),t' "$D" | wc -l | tr -d ' ')
[ "$cnt" = "1" ] || { echo "FEHLER: style-loader in drag-drop.js ${cnt}x gefunden (erwartet 1)"; exit 1; }
sed -i.bak 's/return e.setAttributes(t,e.attributes),e.insert(t,e.options),t/return e.setAttributes(t,e.attributes),t/' "$D" && rm -f "$D.bak"
echo "drag-drop.js: style-loader neutralisiert"
# 4) Bild-/Datei-Plugin: eingebettete Hilfsbibliothek ermittelt das globale Objekt per
#    new Function("return this")() - von der CSP (kein 'unsafe-eval') blockiert. Direkt window nehmen.
for f in "$OUT/image.umd.js" "$OUT/attaches.umd.js"; do
  cnt=$(grep -o 'new Function("return this")()' "$f" | wc -l | tr -d ' ')
  [ "$cnt" = "1" ] || { echo "FEHLER: globalThis-Ermittlung in $(basename "$f") ${cnt}x gefunden (erwartet 1)"; exit 1; }
  sed -i.bak 's/new Function("return this")()/window/' "$f" && rm -f "$f.bak"
  echo "$(basename "$f"): eval-freie Ermittlung des globalen Objekts"
done
echo "fertig -> $OUT"
