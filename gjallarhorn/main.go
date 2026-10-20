// Gjallarhorn: Weiterleiter-Knoten fuer grosse Saga-Gruppenanrufe.
// Dieser Stand ist der Prototyp "Horn-Probe" (Saga 2.5, Paket P0).
package main

import (
	"fmt"
	"os"
)

var version = "2.5.0-probe"

const usage = `gjallarhorn %s – Horn-Probe

Aufruf:
  gjallarhorn probe                         Messknoten starten (HTTPS, ICE-TCP, TURN-TLS, opt. UDP)
  gjallarhorn bench [-n 25] [--video 12] [--secs 60] [--path tcp|udp]
                                            Last mit lokalen Pion-Bots messen
  gjallarhorn report [--data DIR]           Markdown-Bericht mit Go/No-Go (stdout)
  gjallarhorn version

Umgebung (probe): HORN_NAME, HORN_LISTEN (:443), HORN_UDP (1), HORN_DATA (/data),
  HORN_TLS_CERT/HORN_TLS_KEY, HORN_TLS_SELF=1, HORN_PROXY_PROTOCOL (0|1|auto),
  HORN_PUBLIC_IP, HORN_ACME_URL, HORN_ACME_EMAIL. Details: PROBE.md
`

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, usage, version)
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "probe":
		err = runProbe(os.Args[2:], false)
	case "bench-node": // intern: Knoten fuer bench als eigener Prozess
		err = runProbe(os.Args[2:], true)
	case "bench":
		err = runBench(os.Args[2:])
	case "report":
		err = runReport(os.Args[2:], os.Stdout)
	case "version", "--version", "-v":
		fmt.Println(version)
	default:
		fmt.Fprintf(os.Stderr, usage, version)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "fehler:", err)
		os.Exit(1)
	}
}
