package sfu

import (
	"testing"
	"time"
)

// Pruefung von HORN_BWE=eigen (Stufe 2): Echte Enge, waehrend eine Widerlegung laeuft. Ein grundloser Zielsturz ist
// gerade widerlegt (Schonfrist steht auf dem Detektor), da bricht die Leitung wirklich ein - 0, 1 und 2 Takte nach dem
// Sturz. Der Platz darf h nicht spaeter verlassen als bei gcc nach einem Einbruch ohne Vorgeschichte (plus ein Takt).
func TestPruefEngeInDerWiderlegung(t *testing.T) {
	leave := func(eigen bool, sturz bool, lagTicks int, link float64) time.Duration {
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		er.until("h", 30*time.Second)
		er.run(40 * time.Second)
		if sturz {
			er.falsch = 3
			for i := 0; i < lagTicks; i++ {
				er.tick()
			}
			if er.layer() != "h" {
				t.Fatalf("eigen=%v: h schon vor dem Einbruch verlassen (Sturz nicht widerlegt)", eigen)
			}
		}
		er.link = link
		start := er.now
		for er.now-start < int64(30*time.Second) {
			er.tick()
			if er.layer() != "h" {
				return time.Duration(er.now - start)
			}
		}
		return -1
	}
	for _, link := range []float64{700e3, 1000e3, 1150e3} {
		g := leave(false, false, 0, link)
		for _, n := range []int{0, 1, 2, 5} {
			e := leave(true, true, n, link)
			t.Logf("Leitung faellt auf %.0f kbit/s, %d Takte nach einem widerlegten Zielsturz: h verlassen nach %v (gcc ohne Vorgeschichte: %v)", link/1000, n, e, g)
			if e < 0 || e > g+200*time.Millisecond {
				t.Fatalf("%.0f kbit/s, %d Takte: eigen %v, gcc %v", link/1000, n, e, g)
			}
		}
	}
}
