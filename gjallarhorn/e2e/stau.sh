#!/bin/sh
# Pruefstand "enger Downlink" (TURN-TLS bzw. ICE-TCP durch einen Userspace-Engpass, weil netem fehlt).
#
#   e2e/stau.sh start [bin]     Knoten (HORN_DEV=1, ohne UDP) + engpass + Download-Quelle starten
#   e2e/stau.sh set down=600&rtt=40&qms=200    Engpass zur Laufzeit stellen (Parameter wie engpass /set)
#   e2e/stau.sh mess --path turn|tcp --secs 60 ...   ein Messlauf (e2e/stau.mjs), Ergebnis als JSON-Zeile
#   e2e/stau.sh stop
#
# Ports (Umgebung): STAU_NODE (Knoten, HTTPS/TURN-TLS/ICE-TCP), STAU_ENG (Engpass -> Knoten), STAU_CTL (Steuerung),
# STAU_BULK (Engpass -> Download-Quelle STAU_SRC). Arbeitsordner STAU_DIR. Weitere Knoten-Schalter: HORN_EXTRA="A=1 B=2".
set -eu
cd "$(dirname "$0")/.."
D="${STAU_DIR:-/tmp/stau}"
NODE="${STAU_NODE:-18531}" ENG="${STAU_ENG:-18532}" CTL="${STAU_CTL:-18533}" BULK="${STAU_BULK:-18534}" SRC="${STAU_SRC:-18535}"
mkdir -p "$D/logs" "$D/www"
stopall() {
	for f in "$D"/*.pid; do
		[ -f "$f" ] || continue
		kill "$(cat "$f")" 2>/dev/null || true
		rm -f "$f"
	done
}
case "${1:-}" in
start)
	stopall
	bin="${2:-}"
	if [ -z "$bin" ]; then
		bin="$D/gjallarhorn"
		go build -o "$bin" .
	fi
	[ -f "$D/engpass" ] || go build -o "$D/engpass" ./e2e/engpass
	[ -f "$D/www/big.bin" ] || head -c 200000000 /dev/zero >"$D/www/big.bin"
	rm -rf "$D/data"
	# shellcheck disable=SC2086
	env HORN_DEV=1 HORN_UDP=0 HORN_TLS_SELF=1 HORN_MODE=node HORN_LISTEN="127.0.0.1:$NODE" HORN_DATA="$D/data" \
		HORN_ICE_IPS=127.0.0.1 ${HORN_EXTRA:-} "$bin" node >"$D/logs/node.log" 2>&1 &
	echo $! >"$D/node.pid"
	"$D/engpass" -map "$ENG=127.0.0.1:$NODE,$BULK=127.0.0.1:$SRC" -rtt 20 -qms 200 -ctl "127.0.0.1:$CTL" >"$D/logs/engpass.log" 2>&1 &
	echo $! >"$D/engpass.pid"
	python3 -m http.server "$SRC" --bind 127.0.0.1 --directory "$D/www" >"$D/logs/www.log" 2>&1 &
	echo $! >"$D/www.pid"
	sleep 1.5
	echo "Knoten $(cat "$D/node.pid") auf $NODE, engpass $ENG/$BULK, Steuerung $CTL"
	;;
set)
	curl -s "http://127.0.0.1:$CTL/set?${2:-}"
	;;
mess)
	shift
	env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy NO_PROXY='*' NODE_PATH="${NODE_PATH:-$(npm root -g)}" \
		node e2e/stau.mjs --base "https://127.0.0.1:$NODE" --eng "$ENG" --engctl "127.0.0.1:$CTL" \
		--bulkurl "http://127.0.0.1:$BULK/big.bin" --nodepid "$(cat "$D/node.pid")" "$@"
	;;
stop)
	stopall
	;;
*)
	echo "Aufruf: $0 start [bin] | set <query> | mess <args> | stop" >&2
	exit 2
	;;
esac
