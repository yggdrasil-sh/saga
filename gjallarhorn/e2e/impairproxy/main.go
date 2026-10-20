// impairproxy: TCP-Durchreicher mit nachgebildeter RTT und Verlust (fuer
// Browser-Laeufe ohne netem). Aufruf:
//   impairproxy <lauschen> <ziel> [rtt-ms] [verlust-anteil]
package main

import (
	"log"
	"os"
	"strconv"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/impair"
)

func main() {
	if len(os.Args) < 3 {
		log.Fatal("aufruf: impairproxy <lauschen> <ziel> [rtt-ms] [verlust]")
	}
	rtt, loss := 40, 0.0
	if len(os.Args) > 3 {
		rtt, _ = strconv.Atoi(os.Args[3])
	}
	if len(os.Args) > 4 {
		loss, _ = strconv.ParseFloat(os.Args[4], 64)
	}
	c := impair.Config{RTT: time.Duration(rtt) * time.Millisecond, Loss: loss, Seed: 1}
	if _, err := impair.Listen(os.Args[1], os.Args[2], c, c); err != nil {
		log.Fatal(err)
	}
	select {}
}
