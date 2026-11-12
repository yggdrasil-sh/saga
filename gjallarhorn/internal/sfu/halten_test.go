package sfu

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

// Nacharbeit zu HORN_BWE=eigen (Pruefer-Fund 3): Halten nach bestandener Probe; Ereignisse fuers Log (Widerlegung,
// Ruecknahme einer Schicht). Rampen-Probe und Nachweis: rampe_test.go.

// nachProbe faehrt das Modell auf freier Leitung bis zu einer bestandenen Probe m -> h und dem Schritt auf h.
func nachProbe(t *testing.T, eigen bool) *estRoom {
	t.Helper()
	er := neuEigen(t, eigen, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.until("m", 2*time.Second)
	if got := probeOver(t, er); got != probeOK {
		t.Fatalf("eigen=%v: Probe %q", eigen, got)
	}
	er.l.ExtOver = func() bool { return er.over >= 2 }
	for i := 0; i < 10 && er.layer() != "h"; i++ {
		er.tick()
	}
	if er.layer() != "h" {
		t.Fatalf("eigen=%v: kein Schritt auf h nach der bestandenen Probe (est %d)", eigen, er.l.EstimateKbit())
	}
	return er
}

// verlassen: Zeit, bis der Sprecherplatz h verlaesst (hoechstens max; -1 = bleibt). In den ersten sturz Takten steht
// das GCC-Ziel dabei auf 600 kbit/s (estRoom.loss) - weit unter der Rate von h und unter dem, was das Bein zuletzt
// gesendet hat (keine Klemmung, estimator.clamped), ohne dass die Leitung staut.
func (er *estRoom) verlassen(max time.Duration, sturz int) time.Duration {
	start := er.now
	for i := 0; er.now-start < int64(max); i++ {
		er.loss = i < sturz
		er.tick()
		if er.layer() != "h" {
			er.loss = false
			return time.Duration(er.now - start)
		}
	}
	return -1
}

// TestEigenHaltenNachProbe (Fund 3): Gleich nach dem Schritt, den eine bestandene Probe freigegeben hat, faellt das
// GCC-Ziel unter die Rate, und die Rueckmeldungen zeigen einen Verlust-Schub ohne Schlange - das Schluesselbild der
// neuen Schicht ueberlaeuft einen kurzen Puffer (UDP, Leitung 1400 kbit/s mit 20 ms Puffer: GCC-Verlust 14 bis 19 %,
// die Schicht hielt 0,2 s). Bei gcc faellt der Platz zurueck. Bei eigen haelt die Schicht: probeKeep lang nach der
// Probe nimmt das GCC-Ziel allein sie nicht zurueck, solange der Detektor keine Schlange zeigt; der Verlust-Schub
// zaehlt in den ersten probeBurst nach dem Schritt nicht.
//   - Derselbe Schub ohne Probe davor (10 s spaeter): Der Detektor widerlegt nicht (Verlust gestiegen), GCC gilt.
//   - Haelt der Verlust ueber probeBurst hinaus an, endet das Halten dort.
func TestEigenHaltenNachProbe(t *testing.T) {
	// gcc: faellt
	er := nachProbe(t, false)
	g := er.verlassen(20*time.Second, 3)
	// eigen: haelt
	er = nachProbe(t, true)
	er.det.burst = 3
	e := er.verlassen(20*time.Second, 3)
	t.Logf("Verlust-Schub und Zielsturz gleich nach dem Schritt: h verlassen nach gcc %v, eigen %v (gehalten %d, widerlegt %d)", g, e, er.l.est.heldN, er.l.est.refuted)
	if g < 0 || g > time.Second {
		t.Fatalf("gcc: h verlassen nach %v - das Modell stellt den Rueckfall nicht nach", g)
	}
	if e >= 0 || er.l.est.heldN != 1 {
		t.Fatalf("eigen: h verlassen nach %v, gehalten %d", e, er.l.est.heldN)
	}
	// ... und die Folge ist zu Ende, sobald das Ziel wieder ueber der Rate liegt
	if er.l.est.refAt != 0 || er.l.est.lagAt != 0 {
		t.Fatalf("eigen: Folge nach 20 s noch offen (refAt %d, lagAt %d, gcc %.0f)", er.l.est.refAt, er.l.est.lagAt, er.gcc)
	}
	// derselbe Schub ausserhalb des Haltens
	er = nachProbe(t, true)
	er.run(10 * time.Second)
	er.det.burst = 3
	if d := er.verlassen(10*time.Second, 3); d < 0 || d > time.Second || er.l.est.heldN != 0 {
		t.Fatalf("eigen, Schub 10 s nach der Probe: h verlassen nach %v, gehalten %d (erwartet: GCC gilt)", d, er.l.est.heldN)
	}
	// Verlust haelt an: Ende des Haltens bei probeBurst nach dem Schritt
	er = nachProbe(t, true)
	er.det.burst = 40
	d := er.verlassen(10*time.Second, 3)
	t.Logf("anhaltender Verlust ohne Schlange nach dem Schritt: h verlassen nach %v (Halten zaehlt Verlust ab %v nach dem Schritt)", d, probeBurst)
	if d < 0 || d < probeBurst-400*time.Millisecond || d > probeBurst+400*time.Millisecond {
		t.Fatalf("eigen, anhaltender Verlust: h verlassen nach %v", d)
	}
	// Schlange waehrend des Haltens: sofort
	er = nachProbe(t, true)
	er.link = 700e3
	if d := er.verlassen(10*time.Second, 0); d < 0 || d > 600*time.Millisecond {
		t.Fatalf("eigen, Schlange im Halten: h verlassen nach %v", d)
	}
}

// TestEigenEinbruchNachProbe (Gegenprobe zum Halten): Die Leitung bricht gleich nach dem Schritt ein, den eine
// bestandene Probe freigegeben hat. Mit einer Schlange an der Spitze (Puffer 300 ms) verlaesst der Platz h bei eigen
// nicht spaeter als bei gcc. Zeigt sich der Einbruch nur als Verlust (Puffer 5 ms, Policer), dauert es bis zu
// probeBurst laenger - die Grenze des Haltens.
func TestEigenEinbruchNachProbe(t *testing.T) {
	for _, link := range []float64{700e3, 1000e3, 1150e3} {
		for _, nach := range []int{0, 1, 3} { // Takte zwischen Schritt und Einbruch
			leave := map[bool]time.Duration{}
			for _, eigen := range []bool{false, true} {
				er := nachProbe(t, eigen)
				for i := 0; i < nach; i++ {
					er.tick()
				}
				er.link = link
				leave[eigen] = er.verlassen(30*time.Second, 0)
			}
			t.Logf("Einbruch auf %.0f kbit/s %d Takte nach dem Schritt (Schlange): h verlassen nach gcc %v, eigen %v", link/1000, nach, leave[false], leave[true])
			if leave[true] < 0 || leave[true] > leave[false] {
				t.Fatalf("%.0f kbit/s, %d Takte: eigen %v, gcc %v", link/1000, nach, leave[true], leave[false])
			}
		}
	}
	for _, link := range []float64{700e3, 1000e3} {
		leave := map[bool]time.Duration{}
		for _, eigen := range []bool{false, true} {
			er := nachProbe(t, eigen)
			if eigen {
				er.det.buf = 0.005
			}
			er.link = link
			leave[eigen] = er.verlassen(30*time.Second, 0)
		}
		t.Logf("Einbruch auf %.0f kbit/s gleich nach dem Schritt, nur Verlust (Puffer 5 ms): h verlassen nach gcc %v, eigen %v", link/1000, leave[false], leave[true])
		if leave[true] < 0 || leave[true] > leave[false]+probeBurst+200*time.Millisecond {
			t.Fatalf("%.0f kbit/s, nur Verlust: eigen %v, gcc %v", link/1000, leave[true], leave[false])
		}
	}
}

// TestLegEreignisse: Beginn und Ende einer Widerlegung und jede Ruecknahme einer Schicht werden gemeldet
// (Config.OnLegEvent) - mit Art, Grund und Dauer; nur beim Wechsel, nicht je Takt.
func TestLegEreignisse(t *testing.T) {
	fang := func(er *estRoom) *[]LegEvent {
		var evs []LegEvent
		er.r.mu.Lock()
		er.r.cfg.OnLegEvent = func(e LegEvent) { evs = append(evs, e) }
		er.r.mu.Unlock()
		return &evs
	}
	zeile := func(evs []LegEvent) string {
		out := ""
		for _, e := range evs {
			switch e.Kind {
			case LegDown:
				out += fmt.Sprintf("[down %s %s>%s %s]", e.Class, e.From, e.To, e.Why)
			default:
				out += fmt.Sprintf("[%s %s %s]", e.Kind, e.Art, e.Why)
			}
		}
		return out
	}
	// grundloser Zielsturz: widerlegt, das Ziel erholt sich
	er := neuEigen(t, true, 1250e3, 810e3, 1)
	er.pion = true
	er.until("h", 30*time.Second)
	er.run(40 * time.Second)
	evs := fang(er)
	er.falsch = 3
	er.run(20 * time.Second)
	if got := zeile(*evs); got != "[refute spitze ][refute-end spitze erholt]" {
		t.Fatalf("grundloser Zielsturz: %s", got)
	}
	if d := (*evs)[1].Dur; d < 400*time.Millisecond || d > 10*time.Second || (*evs)[0].GccKbit == 0 || (*evs)[0].RateKbit == 0 {
		t.Fatalf("Dauer %v, Ereignis %+v", d, (*evs)[0])
	}
	// ... danach echte Enge: Ruecknahme mit Grund (das Ziel faellt, als es unter der Rate liegt - nicht entschuldigt)
	*evs = nil
	er.link = 700e3
	er.run(3 * time.Second)
	if got := zeile(*evs); got != "[down speaker h>m gcc-fall]" {
		t.Fatalf("Enge: %s", got)
	}
	// Enge waehrend einer laufenden Widerlegung: sie endet, weil der Detektor nicht mehr widerlegt
	er = neuEigen(t, true, 1250e3, 810e3, 1)
	er.pion = true
	er.until("h", 30*time.Second)
	er.run(40 * time.Second)
	evs = fang(er)
	er.falsch = 3
	er.tick()
	er.link = 700e3
	er.run(3 * time.Second)
	if got := zeile(*evs); !strings.HasPrefix(got, "[refute spitze ][refute-end spitze detektor][down speaker h>m gcc-detektor]") {
		t.Fatalf("Enge in der Widerlegung: %s", got)
	}
	// Halten nach bestandener Probe
	er = nachProbe(t, true)
	evs = fang(er)
	er.det.burst = 3
	if d := er.verlassen(20*time.Second, 3); d >= 0 {
		t.Fatalf("Halten: h verlassen nach %v", d)
	}
	if got := zeile(*evs); got != "[refute halten ][refute-end halten erholt]" {
		t.Fatalf("Halten: %s", got)
	}
	// gcc: keine Widerlegung, nur die Ruecknahme
	er = neuEigen(t, false, 1250e3, 810e3, 1)
	er.pion = true
	er.until("h", 30*time.Second)
	er.run(40 * time.Second)
	evs = fang(er)
	er.falsch = 3
	er.run(3 * time.Second)
	if got := zeile(*evs); !strings.HasPrefix(got, "[down speaker h>m gcc-fall]") || strings.Contains(got, "refute") {
		t.Fatalf("gcc: %s", got)
	}
}
