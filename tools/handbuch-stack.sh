#!/bin/bash
# Handbuch-Bilder aufnehmen (Saga 2.5): startet je Welt/Art einen Lauf der Testumgebung (tools/testumgebung/umgebung.sh, root,
# eigener Netz-Namensraum, eigene DB) und darin tools/screens-handbuch.py (Demo-Welt + Attrappen + Playwright).
#   tools/handbuch-stack.sh [--nur a06-02,b13-07,...]     ohne --nur: alle Bilder, die screens-handbuch.py kennt
#   tools/handbuch-stack.sh --liste                       Bilder mit Welt (familie|verein) und Art (server|knoten)
# Ergebnis: PNG unter $HB_OUT (Standard $HB_DIR/bilder), Log unter $UMG/log/screens-handbuch.py.<welt>-<art>.log.
# Variablen: HB_SLOT (Slot der Testumgebung, Standard 6 -> Ports 9061..9069, Hilfsports 19061..19069), UMG_NS (Standard hb),
#   UMG_DB (Standard saga_hb), UMG_BIN (gjallarhorn/saga-media fuer die Art knoten), HB_DIR (Arbeitsordner),
#   HB_FONTS (Zusatzschriften; Noto Sans Runic fuer das Runensiegel wird beim ersten Lauf dorthin geladen), HB_PYLIB
#   (pyoxipng zum verlustfreien Verkleinern, wird bei Bedarf per pip dorthin geladen), HB_SENV (weitere Server-Variablen).
# Anzeige-Hosts nur im Namensraum: saga.example/verein.example -> 127.0.1.1 (Port 80 -> Server), admin.* -> 127.0.1.2
# (Admin-Port), cloud.saga.example -> 127.0.1.3 (Nextcloud-Attrappe), bookorbit.example -> 127.0.1.4 (BookOrbit-Attrappe).
set -u
D=$(cd "$(dirname "$0")" && pwd)
NUR=""
case ${1:-} in
  --liste) exec python3 "$D/screens-handbuch.py" --liste ;;
  --nur) NUR=${2:?Namen}; shift 2 ;;
  "") ;;
  *) sed -n 2,11p "$0"; exit 2 ;;
esac
SLOT=${HB_SLOT:-6}
HB_DIR=${HB_DIR:-/srv/saga-test/tmp/handbuch}
export UMG_NS=${UMG_NS:-hb} UMG_DB=${UMG_DB:-saga_hb}
HB_PYLIB=${HB_PYLIB:-$HB_DIR/pylib}                                            # pyoxipng (verlustfrei verkleinern)
PYTHONPATH=$HB_PYLIB python3 -c "import oxipng" 2>/dev/null || pip install -q --target "$HB_PYLIB" pyoxipng >/dev/null 2>&1 \
  || echo "Hinweis: pyoxipng nicht installiert - PNG nur mit Pillow verkleinert"
export UMG_PYX=${UMG_PYX:-${UMG_PWLIB:-/srv/saga-test/tmp/suite/pylib}:$HB_PYLIB}   # Python-Playwright, pyoxipng
export HB_OUT=${HB_OUT:-$HB_DIR/bilder} HB_FONTS=${HB_FONTS:-$HB_DIR/fonts} HB_SLOT=$SLOT
mkdir -p "$HB_OUT" "$HB_FONTS"
RUNIC=$HB_FONTS/NotoSansRunic-Regular.ttf
if [ ! -s "$RUNIC" ]; then     # Runensiegel im Anruf: Labor-Chromium hat keine Runen-Schrift (sonst Tofu)
  curl -sfL -o "$RUNIC" https://github.com/notofonts/notofonts.github.io/raw/main/fonts/NotoSansRunic/hinted/ttf/NotoSansRunic-Regular.ttf \
    || { rm -f "$RUNIC"; echo "WARNUNG: Noto Sans Runic nicht geladen - Runensiegel ohne Schrift"; }
fi
export UMG_HOSTS=$'127.0.1.1 saga.example verein.example\n127.0.1.2 admin.saga.example admin.verein.example\n127.0.1.3 cloud.saga.example\n127.0.1.4 bookorbit.example'
GRUPPEN=$(python3 "$D/screens-handbuch.py" --gruppen ${NUR:+--nur "$NUR"})
[ -n "$GRUPPEN" ] || { echo "keine passenden Bilder: $NUR"; exit 2; }
RC=0
while read -r WELT ART NAMES; do
  case $WELT in familie) H=saga.example;; verein) H=verein.example;; *) echo "Welt unbekannt: $WELT"; exit 2;; esac
  echo "== $WELT ($H), Art $ART: $NAMES"
  UMG_SENV="BASE_URL=http://$H ADMIN_URL=http://admin.$H BOOKORBIT_URL=http://bookorbit.example ${HB_SENV:-}" \
    HB_WELT=$WELT HB_ART=$ART HB_NUR=$NAMES bash "$D/testumgebung/umgebung.sh" lauf "$ART" "$SLOT" "$D/screens-handbuch.py" "$WELT-$ART" || RC=1
  L=${UMG:-/srv/saga-test/tmp/umgebung}/log/screens-handbuch.py.$WELT-$ART.log
  grep -E '^(OK|FAIL|BILD|Bilder:|FEHLER|PNG )|bereit=' "$L" | sed 's/^/  /'
done <<< "$GRUPPEN"
ls -la "$HB_OUT" | tail -n +2 | wc -l | sed 's/^/Bilder im Ordner: /'
exit $RC
