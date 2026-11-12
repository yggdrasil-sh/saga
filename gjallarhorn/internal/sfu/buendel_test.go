package sfu

import (
	"strings"
	"testing"
	"time"
)

// Eigenes Buendel in einer laufenden Widerlegung (estimator.blip): Auf einer Leitung mit Ratengrenze zeigt ein
// Schluesselbild einen Takt lang eine Schlange an der Spitze - die letzten bestaetigten Pakete standen hintereinander
// an. Die Widerlegung uebersteht diesen einen Takt; alles andere beendet sie wie bisher.

// inWiderlegung faehrt das Modell auf h (Leitung 2000 kbit/s - traegt h, zeigt aber ein Buendel als Schlange) und
// loest einen grundlosen Zielsturz aus; bei eigen laeuft danach die Widerlegung.
func inWiderlegung(t *testing.T, eigen bool) (*estRoom, *[]LegEvent) {
	t.Helper()
	er := neuEigen(t, eigen, 1250e3, 810e3, 1)
	er.pion = true
	er.until("h", 30*time.Second)
	er.run(40 * time.Second)
	er.link = 2000e3
	er.run(2 * time.Second)
	if er.layer() != "h" {
		t.Fatalf("eigen=%v: Schicht %q vor dem Zielsturz", eigen, er.layer())
	}
	evs := legFang(er)
	er.falsch = 3
	er.tick()
	return er, evs
}

func TestEigenBuendelInWiderlegung(t *testing.T) {
	// ein Schluesselbild-Buendel im Takt nach dem Zielsturz: 600 kbit/s ueber den Takt = 15 kB, an 2000 kbit/s 60 ms Schlange
	er, evs := inWiderlegung(t, true)
	if got := legZeile(*evs); got != "[refute spitze ]" {
		t.Fatalf("vor dem Buendel: %s", got)
	}
	er.bund = 600e3
	er.run(20 * time.Second)
	if got := legZeile(*evs); got != "[refute spitze ][refute-end spitze erholt]" || er.layer() != "h" || er.l.est.blips != 1 {
		t.Fatalf("ein Buendel in der Widerlegung: %s, Schicht %q, entschuldigt %d", got, er.layer(), er.l.est.blips)
	}
	// ... dasselbe, das Buendel faellt in den Takt davor (die Rueckmeldung dazu kommt erst im naechsten)
	er, evs = inWiderlegung(t, true)
	er.bund = 600e3
	er.link = 0 // in diesem Takt zeigt die Leitung es noch nicht
	er.tick()
	er.link = 2000e3
	er.det.kick = 0.06
	er.run(20 * time.Second)
	if got := legZeile(*evs); got != "[refute spitze ][refute-end spitze erholt]" || er.layer() != "h" || er.l.est.blips != 1 {
		t.Fatalf("Buendel im Takt davor: %s, Schicht %q, entschuldigt %d", got, er.layer(), er.l.est.blips)
	}
	// zwei Takte in Folge: der zweite ist nicht entschuldigt
	er, evs = inWiderlegung(t, true)
	er.bund = 600e3
	er.tick()
	er.bund = 600e3
	er.run(3 * time.Second)
	if got := legZeile(*evs); !strings.HasPrefix(got, "[refute spitze ][refute-end spitze detektor][down speaker h>m gcc-detektor]") || er.l.est.blips != 1 {
		t.Fatalf("zwei Buendel in Folge: %s, entschuldigt %d", got, er.l.est.blips)
	}
	// Schlange an der Spitze ohne eigenes Buendel: wie bisher sofort
	er, evs = inWiderlegung(t, true)
	er.det.kick = 0.06
	er.tick()
	if got := legZeile(*evs); !strings.HasPrefix(got, "[refute spitze ][refute-end spitze detektor][down speaker h>m gcc-detektor]") || er.l.est.blips != 0 {
		t.Fatalf("Schlange ohne Buendel: %s, entschuldigt %d", got, er.l.est.blips)
	}
	// Buendel und gestiegener Verlust: nicht entschuldigt
	er, evs = inWiderlegung(t, true)
	er.bund = 600e3
	er.det.burst = 1
	er.tick()
	if got := legZeile(*evs); !strings.HasPrefix(got, "[refute spitze ][refute-end spitze detektor]") || er.l.est.blips != 0 {
		t.Fatalf("Buendel mit Verlust: %s, entschuldigt %d", got, er.l.est.blips)
	}
	// der Detektor sieht nichts: nicht entschuldigt
	er, evs = inWiderlegung(t, true)
	er.bund = 600e3
	er.det.blind = true
	er.tick()
	if got := legZeile(*evs); !strings.HasPrefix(got, "[refute spitze ][refute-end spitze detektor]") || er.l.est.blips != 0 {
		t.Fatalf("Buendel, Detektor blind: %s, entschuldigt %d", got, er.l.est.blips)
	}
	// gcc: keine Widerlegung, der Platz ist mit dem Zielsturz unten
	er, evs = inWiderlegung(t, false)
	er.bund = 600e3
	er.run(time.Second)
	if got := legZeile(*evs); !strings.HasPrefix(got, "[down speaker h>m gcc-fall]") || er.l.est.blips != 0 {
		t.Fatalf("gcc: %s", got)
	}
}

// TestEigenBuendelUndEnge (Grenze): Beginnt eine echte Enge in einer laufenden Widerlegung, gilt das Signal von GCC
// sofort - mit einem eigenen Buendel im selben Takt einen Takt spaeter, nicht mehr.
func TestEigenBuendelUndEnge(t *testing.T) {
	verl := func(bund float64) time.Duration {
		er, _ := inWiderlegung(t, true)
		start := er.now
		er.link = 700e3
		er.bund = bund
		for i := 0; i < 50 && er.layer() == "h"; i++ {
			er.tick()
		}
		if er.layer() == "h" {
			t.Fatalf("Buendel %.0f: h nach 10 s Enge nicht verlassen", bund)
		}
		return time.Duration(er.now - start)
	}
	ohne, mit := verl(0), verl(600e3)
	t.Logf("Enge (700 kbit/s) in laufender Widerlegung: h verlassen nach %v, mit eigenem Buendel im ersten Takt nach %v", ohne, mit)
	if ohne > 400*time.Millisecond || mit > ohne+200*time.Millisecond {
		t.Fatalf("ohne Buendel %v, mit %v", ohne, mit)
	}
}
