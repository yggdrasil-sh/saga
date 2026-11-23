#!/bin/bash
# Testumgebung fuer Server-Reihen, die mehr brauchen als ein einzelner Server (Saga 2.5, Testmaschine saga-test, root).
#   umgebung.sh einrichten                        Test-CA + Zertifikate, NSS-DB (eigenes HOME), LDAP-Bibliotheken, calls.js
#                                                 aus E0/2.3, Ton-Datei, Knoten-Binaries; prueft age, Python-Playwright, pg_trgm
#   umgebung.sh lauf <art> <slot> <reihe> [tag]  eine Reihe (slot 0-8) in eigenem Netz-Namensraum <UMG_NS><slot> laufen lassen
#   umgebung.sh stop <slot>                       Reste eines Laufs beenden (nur PIDs im Namensraum), Namensraum loeschen
#   umgebung.sh env                               verwendete Pfade/Variablen zeigen
# Arten: server (ein Server), sso (+ LDAP-Attrappe ldap.test:389 mit StartTLS, OIDC-Attrappe mockoidc:8080),
#        knoten (Server hinter saga-media mit eingebautem Knoten, Zertifikat der Test-CA, calls.horn + horn_all),
#        fed (zwei verbundene Instanzen A/B, tools/testumgebung/zwei_stacks.py), fedknoten (dazu Knoten auf A).
# Grenzen: alles im Namensraum (lo + Attrappe um0 10.78.<slot>.1/24, Weg nach aussen "unreachable"), /etc/hosts nur in
# einem eigenen Mount-Namensraum ersetzt, nichts dauerhaft (keine Dienste, keine Firewall), Ports je Slot
# 9001+10*slot ... +9 und Hilfsports 19001+10*slot ... +9 (feste Ports der Reihen wie 8080/389/8094-8097 nur im Namensraum).
# Variablen: UMG (Arbeitsordner), UMG_BIN (gjallarhorn/saga-media), UMG_GOBIN (age), UMG_PWLIB (Python-Playwright),
#   UMG_NS (Praefix Namensraum, Standard um), UMG_DB (Praefix DB, Standard saga_umg), UMG_LIMIT (s), UMG_SENV ("K=V ..." fuer
#   den Server), UMG_PYX (zusaetzlicher PYTHONPATH der Reihe), UMG_HOSTS (weitere Zeilen fuer /etc/hosts des Laufs),
#   UMG_HORN_NAME/UMG_HORN_CERT/UMG_HORN_KEY (Art knoten: Name und Zertifikat des eingebauten Knotens, Standard localhost und
#   das Zertifikat der Test-CA). Weitere Variablen (z. B. MESH=1) gehen an die Reihe durch.
set -u
D=$(cd "$(dirname "$0")" && pwd)
WT=${WT:-$(cd "$D/../.." && pwd)}
export UMG=${UMG:-/srv/saga-test/tmp/umgebung}
REV=$(git -C "$WT" rev-parse --short=7 HEAD 2>/dev/null || echo lokal)
export UMG_BIN=${UMG_BIN:-$UMG/bin-$REV}
export UMG_GOBIN=${UMG_GOBIN:-/srv/saga-test/tmp/suite/gobin}
export UMG_PWLIB=${UMG_PWLIB:-/srv/saga-test/tmp/suite/pylib}
export UMG_NS=${UMG_NS:-um} UMG_DB=${UMG_DB:-saga_umg}
E0_REF=${UMG_E0_REF:-cfac8b11}       # Spitze von feature/24-e0 (Merge ff6e4282); calls.js = 07fbfe53 "2.4 E0 Web"
PGSOCK=${UMG_PGSOCK:-/tmp/.s.PGSQL.55432}

einrichten() {
  mkdir -p $UMG/ca $UMG/home $UMG/e0 $UMG/log $UMG/run
  if [ ! -f $UMG/ca/ca.pem ]; then
    # Nur Test-Schluessel, 30 Tage, nur fuer localhost/ldap.test/mockoidc - liegt nie im Repo
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=Saga Testumgebung CA (nur Test)" -keyout $UMG/ca/ca.key \
      -out $UMG/ca/ca.pem -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" -keyout $UMG/ca/srv.key -out $UMG/ca/srv.csr 2>/dev/null
    printf 'subjectAltName=DNS:localhost,DNS:ldap.test,DNS:mockoidc,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n' > $UMG/ca/srv.ext
    openssl x509 -req -in $UMG/ca/srv.csr -CA $UMG/ca/ca.pem -CAkey $UMG/ca/ca.key -CAcreateserial -days 30 -extfile $UMG/ca/srv.ext \
      -out $UMG/ca/srv.pem 2>/dev/null
    chmod 600 $UMG/ca/*.key
    rm -rf $UMG/home/.pki
  fi
  echo "CA: $(openssl x509 -in $UMG/ca/ca.pem -noout -subject -enddate | tr '\n' ' ')"
  # Chromium (Playwright) vertraut der Test-CA nur ueber die NSS-DB im HOME des Laufs (HOME=$UMG/home), nicht systemweit
  if [ ! -d $UMG/home/.pki/nssdb ]; then
    mkdir -p $UMG/home/.pki/nssdb
    certutil -d sql:$UMG/home/.pki/nssdb -N --empty-password
    certutil -d sql:$UMG/home/.pki/nssdb -A -n saga-umg-test-ca -t "C,," -i $UMG/ca/ca.pem
  fi
  echo "NSS: $(certutil -d sql:$UMG/home/.pki/nssdb -L | grep -c saga-umg) Eintrag"
  # LDAP-Attrappe: ldaptor/twisted/pyOpenSSL nur unter $UMG/pylib (nicht im venv)
  PYTHONPATH=$UMG/pylib python -c "import ldaptor, twisted, OpenSSL" 2>/dev/null || pip install -q --target $UMG/pylib ldaptor
  PYTHONPATH=$UMG/pylib python -c "import ldaptor, twisted; print('ldaptor', ldaptor.__version__ if hasattr(ldaptor, '__version__') else '?', 'twisted', twisted.__version__)"
  # Alte Anruf-Clients fuer tests/browser_screen2.js (Kompatibilitaet E0 und 2.3) - braucht einen vollen Klon
  git -C "$WT" show $E0_REF:static/calls.js > $UMG/e0/calls-e0.js 2>/dev/null || echo "WARNUNG: $E0_REF fehlt (voller Klon noetig)"
  git -C "$WT" show v2.3.0:static/calls.js > $UMG/e0/calls-23.js 2>/dev/null || echo "WARNUNG: v2.3.0 fehlt"
  wc -c $UMG/e0/calls-e0.js $UMG/e0/calls-23.js | head -2
  # Ton fuer tests/browser_anruf_umzug.js: nicht stationaerer Gleitton (Luecken messbar), 60 s, 48 kHz mono
  [ -f $UMG/ton.wav ] || ffmpeg -v error -f lavfi -i "aevalsrc='0.4*sin(2*PI*t*(350+150*sin(2*PI*0.7*t)))':s=48000:d=60" -ac 1 $UMG/ton.wav
  ls -la $UMG/ton.wav | awk '{print "Ton:", $5, "Bytes"}'
  # Knoten-Binaries aus dem Worktree (wie das Image: CGO_ENABLED=0 -trimpath)
  if [ ! -x $UMG_BIN/gjallarhorn ] || [ ! -x $UMG_BIN/saga-media ]; then
    mkdir -p $UMG_BIN
    (cd "$WT/gjallarhorn" && CGO_ENABLED=0 go build -trimpath -o $UMG_BIN/gjallarhorn .) && \
    (cd "$WT/media" && CGO_ENABLED=0 go build -trimpath -o $UMG_BIN/saga-media .) || echo "WARNUNG: Go-Bau fehlgeschlagen"
  fi
  md5sum $UMG_BIN/gjallarhorn $UMG_BIN/saga-media
  # Vorhanden? (nicht installiert von hier): age, Python-Playwright, pg_trgm
  [ -x $UMG_GOBIN/age ] && echo "age: $($UMG_GOBIN/age --version)" || echo "FEHLT age: GOBIN=$UMG_GOBIN go install filippo.io/age/cmd/...@latest"
  PYTHONPATH=$UMG_PWLIB python -c "import playwright.sync_api; from importlib.metadata import version; print('Python-Playwright', version('playwright'), '(Reihen damit: UMG_PYX=$UMG_PWLIB)')" 2>/dev/null || \
    echo "FEHLT Python-Playwright: pip install --target $UMG_PWLIB playwright==<Version wie node>"
  psql -h /tmp -p 55432 -U saga -Atc "select 'pg_trgm verfuegbar: ' || count(*) from pg_available_extensions where name='pg_trgm'" postgres
}

aufraeumen() {   # nur Prozesse im Namensraum <UMG_NS><slot> (auch eigene Prozessgruppen wie timeout/Chromium), dann loeschen
  local NS=$1 sig P
  for sig in TERM KILL; do P=$(ip netns pids $NS 2>/dev/null | tr '\n' ' '); [ -n "$P" ] && { echo "Reste ($sig): $P"; kill -$sig $P 2>/dev/null; sleep 2; }; done
  ip netns del $NS 2>/dev/null
  return 0
}

lauf() {
  local ART=$1 SLOT=$2 F=$3 TAG=${4:-}
  case $ART in server|sso|knoten|fed|fedknoten) ;; *) echo "Art unbekannt: $ART"; exit 2;; esac
  [ "$SLOT" -ge 0 ] 2>/dev/null && [ "$SLOT" -le 8 ] || { echo "Slot 0..8 (Ports 9001+10*slot ... +9)"; exit 2; }
  local NS=$UMG_NS$SLOT NAME=$(basename $F)${TAG:+.$TAG}
  local RUN=$UMG/run/$SLOT LOG=$UMG/log/$NAME.log
  mkdir -p $UMG/log
  local T0=$(date +%s)
  {
    echo "== $NAME art=$ART slot=$SLOT ns=$NS db=${UMG_DB}_$SLOT rev=$REV bin=$UMG_BIN $(date '+%F %T')"
    rm -rf $RUN; mkdir -p $RUN/shots
    dropdb -h /tmp -p 55432 -U saga --if-exists ${UMG_DB}_$SLOT && createdb -h /tmp -p 55432 -U saga ${UMG_DB}_$SLOT || echo "DB-Anlage fehlgeschlagen"
    aufraeumen $NS                      # Reste eines abgebrochenen Laufs auf diesem Slot
    trap "aufraeumen $NS; exit 143" TERM INT
    ip netns add $NS && ip -n $NS link set lo up || { echo "netns fehlgeschlagen"; exit 1; }
    # Attrappe mit privater Adresse + Standardweg: WebRTC (Chromium) sammelt keine ICE-Kandidaten nur auf lo
    ip -n $NS link add um0 type dummy && ip -n $NS addr add 10.78.$SLOT.1/24 dev um0 && ip -n $NS link set um0 up && \
      ip -n $NS route add unreachable default || echo "Attrappe fehlgeschlagen"
    printf '127.0.0.1 localhost ldap.test mockoidc\n::1 localhost\n' > $RUN/hosts
    [ -n "${UMG_HOSTS:-}" ] && printf '%s\n' "$UMG_HOSTS" >> $RUN/hosts      # weitere Namen (z. B. Anzeige-Hosts der Handbuch-Bilder)
    ART=$ART SLOT=$SLOT F=$F RUN=$RUN WT=$WT D=$D DBN=${UMG_DB}_$SLOT PGSOCK=$PGSOCK \
      ip netns exec $NS unshare -m --propagation private bash $D/innen.sh
    echo "== innen rc=$?"
    aufraeumen $NS
    trap - TERM INT
    dropdb -h /tmp -p 55432 -U saga --if-exists ${UMG_DB}_$SLOT
  } > "$LOG" 2>&1
  local RC=$(sed -n 's/^== test rc=\([0-9]*\).*/\1/p' "$LOG" | tail -1)
  local NOK=$(grep -cE '^ *OK ' "$LOG") NF=$(grep -cE '^ *FAIL' "$LOG") TB=-
  local SL=$(ls $RUN/server.log $RUN/fed/*/server.log 2>/dev/null)
  [ -n "$SL" ] && TB=$(cat $SL | grep -a -c Traceback)
  printf '%s\t%s\t%s\trc=%s\tok=%s\tfail=%s\ttb=%s\t%ss\t%s\n' "$NAME" "$ART" "$SLOT" "${RC:-?}" "$NOK" "$NF" "$TB" "$(($(date +%s)-T0))" "$(date +%T)" | tee -a $UMG/ergebnis.tsv
}

case ${1:-} in
  einrichten) einrichten ;;
  lauf) shift; lauf "$@" ;;
  stop) aufraeumen $UMG_NS${2:?slot}; echo "$UMG_NS$2 weg" ;;
  env) echo "UMG=$UMG UMG_BIN=$UMG_BIN UMG_GOBIN=$UMG_GOBIN UMG_PWLIB=$UMG_PWLIB UMG_NS=$UMG_NS UMG_DB=$UMG_DB E0=$E0_REF WT=$WT" ;;
  *) sed -n 2,17p "$0" ;;
esac
