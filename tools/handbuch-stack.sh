#!/bin/bash
# Handbuch-Bilder aufnehmen (Saga 2.5): startet je Welt/Art einen Lauf der Testumgebung (tools/testumgebung/umgebung.sh, root,
# eigener Netz-Namensraum, eigene DB) und darin tools/screens-handbuch.py (Demo-Welt + Attrappen + Playwright).
#   tools/handbuch-stack.sh [--nur a06-02,b13-07,...]     ohne --nur: alle Bilder, die screens-handbuch.py kennt
#   tools/handbuch-stack.sh --liste                       Bilder mit Welt (familie|verein) und Art (server|knoten)
# Ergebnis: PNG unter $HB_OUT (Standard $HB_DIR/bilder), Log unter $UMG/log/screens-handbuch.py.<UMG_NS><slot>-<welt>-<art>.log (eigener Name je Namensraum).
# Variablen: HB_SLOT (Slot der Testumgebung, Standard 6 -> Ports 9061..9069, Hilfsports 19061..19069), UMG_NS (Standard hb),
#   UMG_DB (Standard saga_hb), UMG_BIN (gjallarhorn/saga-media fuer die Art knoten; ohne: wie im Image gebaut), HB_DIR (Arbeitsordner),
#   HB_FONTS (Zusatzschriften; Noto Sans Runic fuer das Runensiegel wird beim ersten Lauf dorthin geladen), HB_PYLIB
#   (pyoxipng zum verlustfreien Verkleinern, wird bei Bedarf per pip dorthin geladen), HB_SENV (weitere Server-Variablen).
# Anzeige-Hosts nur im Namensraum: saga.example -> 127.0.1.1, verein.example -> 127.0.1.7 (Port 80 -> Server), admin.* -> 127.0.1.2/.8
# (Admin-Port; in der Welt familie laeuft verein.example als zweiter Server fuer die Foederations-Bilder), cloud.saga.example -> 127.0.1.3 (Nextcloud-Attrappe), bookorbit.example -> 127.0.1.4 (BookOrbit-Attrappe).
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
export UMG_HOSTS=$'127.0.1.1 saga.example\n127.0.1.2 admin.saga.example\n127.0.1.7 verein.example\n127.0.1.8 admin.verein.example\n127.0.1.3 cloud.saga.example\n127.0.1.4 bookorbit.example\n127.0.1.5 wetter.saga.example\n127.0.1.6 ki.saga.example\n127.0.1.9 sommerfest.example\n127.0.1.10 fotos.saga.example\n127.0.0.1 knoten.verein.example'
# Art knoten: Binaries wie im Image (Version aus VERSION statt "x.y.z-probe"), einmal je Stand nach $HB_DIR/bin-<rev> gebaut;
# der eingebaute Knoten des Gartenvereins heisst knoten.verein.example (eigenes Zertifikat der Test-CA, 30 Tage, nur im Lauf)
UMG=${UMG:-/srv/saga-test/tmp/umgebung}
if [ -z "${UMG_BIN:-}" ]; then
  export UMG_BIN=$HB_DIR/bin-$(git -C "$D/.." log -1 --format=%h -- gjallarhorn media VERSION)
  if [ ! -x "$UMG_BIN/gjallarhorn" ] || [ ! -x "$UMG_BIN/saga-media" ]; then
    mkdir -p "$UMG_BIN"; V=$(cat "$D/../VERSION")
    (cd "$D/../gjallarhorn" && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=$V" -o "$UMG_BIN/gjallarhorn" .) &&
      (cd "$D/../media" && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.version=$V" -o "$UMG_BIN/saga-media" .) ||
      { echo "Knoten-Binaries nicht gebaut"; rm -rf "$UMG_BIN"; }
  fi
fi
HCERT=$HB_DIR/knoten-verein
if [ ! -s "$HCERT.pem" ] && [ -s "$UMG/ca/ca.key" ]; then
  openssl req -newkey rsa:2048 -nodes -subj "/CN=knoten.verein.example" -keyout "$HCERT.key" -out "$HCERT.csr" 2>/dev/null &&
    printf 'subjectAltName=DNS:knoten.verein.example,DNS:localhost\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > "$HCERT.ext" &&
    openssl x509 -req -in "$HCERT.csr" -CA "$UMG/ca/ca.pem" -CAkey "$UMG/ca/ca.key" -CAserial "$HCERT.srl" -CAcreateserial -days 30 -extfile "$HCERT.ext" \
      -out "$HCERT.pem" 2>/dev/null && chmod 600 "$HCERT.key"
fi
# Zusatz +tls (Szenen mit https, z. B. Passkeys, Foederation): Zertifikat der Test-CA fuer die Anzeige-Hosts (30 Tage, nur im
# Lauf; Chromium vertraut der CA ueber die NSS-DB der Testumgebung, die Server ueber SSL_CERT_FILE)
TCERT=$HB_DIR/tls-anzeige
if [ ! -s "$TCERT.pem" ] && [ -s "$UMG/ca/ca.key" ]; then
  openssl req -newkey rsa:2048 -nodes -subj "/CN=saga.example" -keyout "$TCERT.key" -out "$TCERT.csr" 2>/dev/null &&
    printf 'subjectAltName=DNS:saga.example,DNS:admin.saga.example,DNS:verein.example,DNS:admin.verein.example\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > "$TCERT.ext" &&
    openssl x509 -req -in "$TCERT.csr" -CA "$UMG/ca/ca.pem" -CAkey "$UMG/ca/ca.key" -CAserial "$TCERT.srl" -CAcreateserial -days 30 -extfile "$TCERT.ext" \
      -out "$TCERT.pem" 2>/dev/null && chmod 600 "$TCERT.key"
fi
GRUPPEN=$(python3 "$D/screens-handbuch.py" --gruppen ${NUR:+--nur "$NUR"}) || exit 2    # Name ohne Szene: Fehler (stderr)
[ -n "$GRUPPEN" ] || { echo "keine passenden Bilder: $NUR"; exit 2; }
RC=0
while read -r WELT ART NAMES; do
  case $WELT in familie) H=saga.example;; verein) H=verein.example;; *) echo "Welt unbekannt: $WELT"; exit 2;; esac
  echo "== $WELT ($H), Art $ART: $NAMES"
  SCH=http; XENV=""; UART=${ART%%+*}           # Art der Testumgebung ohne Zusaetze
  case $ART in *+tls*)
    [ -s "$TCERT.pem" ] || { echo "  FEHLER: kein TLS-Zertifikat ($UMG/ca fehlt? umgebung.sh einrichten)"; RC=1; continue; }
    SCH=https; XENV="SSL_CERT_FILE=$UMG/ca/ca.pem" ;; esac
  case $ART in *+suche*) XENV="$XENV SEARXNG_URL=http://127.0.0.1:$((19010 + 10 * SLOT)) SEARXNG_SECRET=hb-attrappe AI_WEB_FETCH=0" ;; esac
  UMG_SENV="BASE_URL=$SCH://$H ADMIN_URL=$SCH://admin.$H BOOKORBIT_URL=http://bookorbit.example CACHE_TTL=3 FED_ALLOW_HTTP=1 SAGA_UNFURL_TEST_PUBLIC=127.0.1.9 $XENV ${HB_SENV:-}" \
    UMG_HORN_NAME=$([ $WELT = verein ] && [ -s "$HCERT.pem" ] && echo knoten.verein.example) \
    UMG_HORN_CERT=$([ $WELT = verein ] && [ -s "$HCERT.pem" ] && echo "$HCERT.pem") UMG_HORN_KEY=$([ $WELT = verein ] && [ -s "$HCERT.pem" ] && echo "$HCERT.key") \
    HB_WELT=$WELT HB_ART=$ART HB_NUR=$NAMES HB_TLS_CERT=$TCERT.pem HB_TLS_KEY=$TCERT.key bash "$D/testumgebung/umgebung.sh" lauf "$UART" "$SLOT" "$D/screens-handbuch.py" "$UMG_NS$SLOT-$WELT-$ART" || RC=1
  L=${UMG:-/srv/saga-test/tmp/umgebung}/log/screens-handbuch.py.$UMG_NS$SLOT-$WELT-$ART.log
  grep -E '^(OK|FAIL|BILD|Bilder:|FEHLER|PNG )|bereit=' "$L" | sed 's/^/  /'
done <<< "$GRUPPEN"
find "$HB_OUT" -maxdepth 1 -name '*.png' | wc -l | sed 's/^/Bilder im Ordner: /'
exit $RC
