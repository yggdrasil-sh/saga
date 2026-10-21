// Gjallarhorn: Weiterleiter-Knoten fuer grosse Saga-Gruppenanrufe.
// Betriebsarten: Horn-Probe (Saga 2.5, P0) und Knoten mit Weiterleiter (P1/P2) unter Saga-Steuerung (P3).
package main

import (
	"fmt"
	"os"
	rdebug "runtime/debug"
)

var version = "2.5.0-probe"

// build ist der Commit des Builds (build.sh setzt ihn, sonst aus den
// Go-Build-Informationen).
var build = ""

// buildID liefert den Build-Hash (kurz, "-dirty" bei geaenderten Dateien).
func buildID() string {
	if build != "" {
		return build
	}
	if bi, ok := rdebug.ReadBuildInfo(); ok {
		rev, dirty := "", false
		for _, s := range bi.Settings {
			switch s.Key {
			case "vcs.revision":
				rev = s.Value
			case "vcs.modified":
				dirty = s.Value == "true"
			}
		}
		if len(rev) > 12 {
			rev = rev[:12]
		}
		if rev != "" && dirty {
			rev += "-dirty"
		}
		if rev != "" {
			return rev
		}
	}
	return "unbekannt"
}

const usage = `gjallarhorn %s

Aufruf:
  gjallarhorn probe                         Messknoten starten (HTTPS, ICE-TCP, TURN-TLS, opt. UDP)
  gjallarhorn node                          Knoten mit Weiterleiter (SFU); Beine nur mit Medien-Token von Saga
                                            (Steuerung HORN_SAGA_CTL bzw. HORN_SAGA_PUB; HORN_DEV=1 nur fuer Tests)
  gjallarhorn watch                         Waechter: startet "node" und uebernimmt signierte Updates (Knoten-Image)
  gjallarhorn bench [-n 25] [--video 12] [--secs 60] [--path tcp|udp] [--sfu]
                                            Last mit lokalen Pion-Bots messen
  gjallarhorn report [--data DIR]           Markdown-Bericht mit Go/No-Go (stdout)
  gjallarhorn version

Umgebung (probe): HORN_NAME, HORN_LISTEN (:443), HORN_UDP (1), HORN_DATA (/data),
  HORN_TLS_CERT/HORN_TLS_KEY, HORN_TLS_SELF=1, HORN_PROXY_PROTOCOL (0|1|auto),
  HORN_PUBLIC_IP, HORN_ACME_URL, HORN_ACME_EMAIL, HORN_MODE (probe|node), HORN_DEV.
Umgebung (node, P3): HORN_SAGA_CTL (ws[s]://saga/api/horn/ctl), HORN_CTL_KEY (eingebauter Knoten, setzt saga-media),
  HORN_SAGA_PUB (Base64, ohne Steuerkanal), HORN_SAGA_ORIGIN, HORN_PUBLIC_URL, HORN_MAX_LEGS (48), HORN_MAX_PER_IP (12).
Umgebung (weiterer Knoten, P6/P7): HORN_SAGA (https://saga), HORN_PAIR (einmaliger Kopplungs-Code), HORN_NAME,
  HORN_LINK_MBIT, HORN_PUBLIC_PORT, HORN_UPDATE_BASE, HORN_ACME_URL, HORN_ACME_DNS_WAIT.
  Details: PROBE.md, NODE.md
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
	case "node":
		err = runNode(os.Args[2:], false, "node")
	case "watch": // P6: Waechter (PID 1 im Knoten-Image, Selbst-Update ohne Docker-Socket)
		err = runWatch(os.Args[2:])
	case "bench-node": // intern: Knoten fuer bench als eigener Prozess
		err = runProbe(os.Args[2:], true)
	case "bench":
		err = runBench(os.Args[2:])
	case "report":
		err = runReport(os.Args[2:], os.Stdout)
	case "version", "--version", "-v":
		fmt.Println(version, buildID())
	default:
		fmt.Fprintf(os.Stderr, usage, version)
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "fehler:", err)
		os.Exit(1)
	}
}
