package sfu

import (
	"testing"
	"time"
)

// Pruefung der Nacharbeit zu HORN_BWE=eigen (Rolle pruef-bwe13): die Bedingungen von Halten (estimator.held) und
// Buendel-Regel (estimator.blip) einzeln, an ihren Grenzen - als Festschreibung dessen, was NODE.md dazu sagt.

// TestPruef13Halten: Das Halten nach bestandener Probe gilt nur mit eigenem Detektor, nur bis probeKeep nach der Probe
// und nur ohne Schlange an der Spitze (Calm). Gestiegener Verlust beendet es - ausser in den ersten probeBurst nach dem
// letzten Schritt nach oben des Beins; ein weiterer Schritt setzt diese Frist neu an, aber nie ueber probeKeep hinaus.
func TestPruef13Halten(t *testing.T) {
	t0 := int64(time.Hour)
	ms := func(d int) int64 { return t0 + int64(time.Duration(d)*time.Millisecond) }
	for _, c := range []struct {
		name              string
		det, calm, lossUp bool
		stepMs, nowMs     int // Schritt nach oben und Jetzt, ms nach der bestandenen Probe (stepMs < 0: nie)
		want              bool
	}{
		{"ohne Detektor (gcc, mit)", false, true, false, 0, 200, false},
		{"frei, kein Verlust", true, true, false, 0, 200, true},
		{"bis kurz vor 3 s", true, true, false, 0, 2999, true},
		{"ab 3 s nicht mehr", true, true, false, 0, 3000, false},
		{"Schlange an der Spitze (nicht Calm): sofort nicht", true, false, false, 0, 200, false},
		{"Verlust gestiegen, 1,0 s nach dem Schritt: entschuldigt", true, true, true, 0, 1000, true},
		{"Verlust gestiegen, 1,2 s nach dem Schritt: zaehlt", true, true, true, 0, 1200, false},
		{"Verlust gestiegen, nie ein Schritt: zaehlt", true, true, true, -1, 200, false},
		{"weiterer Schritt bei 1,0 s: Verlust bei 2,0 s entschuldigt", true, true, true, 1000, 2000, true},
		{"weiterer Schritt bei 2,5 s: Verlust bei 2,9 s entschuldigt", true, true, true, 2500, 2900, true},
		{"... aber nie ueber 3 s nach der Probe hinaus", true, true, true, 2500, 3000, false},
		{"Verlust und Schlange: nicht", true, false, true, 0, 200, false},
	} {
		e := &estimator{det: c.det, detCalm: c.calm, detLossUp: c.lossUp, proofUntil: t0 + int64(probeKeep)}
		if c.stepMs >= 0 {
			e.stepAt = ms(c.stepMs)
		}
		if got := e.held(ms(c.nowMs)); got != c.want {
			t.Errorf("%s: held = %v, erwartet %v", c.name, got, c.want)
		}
	}
	// ohne bestandene Probe (proofUntil 0) nie
	if e := (&estimator{det: true, detCalm: true}); e.held(t0) {
		t.Error("Halten ohne bestandene Probe")
	}
}

// TestPruef13Buendel: Die Buendel-Regel entschuldigt einen Takt nur mit eigenem Detektor, der die Leitung sieht
// (Fresh), ohne gestiegenen Verlust, und nur, wenn das Bein in diesem oder im vorigen Takt mindestens burstMin x der
// geglaetteten Rate davor gesendet hat - und nicht zweimal binnen blipGap. Die dokumentierte Grenze: Kommt der naechste
// Takt mehr als 300 ms spaeter (Last), wird auch er entschuldigt.
func TestPruef13Buendel(t *testing.T) {
	t0 := int64(time.Hour)
	neu := func() *estimator {
		return &estimator{det: true, detFresh: true, rateAvg: 1000e3, avgPrev: 1000e3, ratePrev: 1000e3}
	}
	for _, c := range []struct {
		name string
		mod  func(e *estimator)
		rate float64
		want bool
	}{
		{"Buendel in diesem Takt (1,25 x)", nil, 1250e3, true},
		{"knapp darunter (1,24 x)", nil, 1240e3, false},
		{"Buendel im vorigen Takt", func(e *estimator) { e.ratePrev = 1300e3 }, 1000e3, true},
		{"ohne Detektor", func(e *estimator) { e.det = false }, 1300e3, false},
		{"Detektor sieht nichts (nicht Fresh)", func(e *estimator) { e.detFresh = false }, 1300e3, false},
		{"Verlust gestiegen", func(e *estimator) { e.detLossUp = true }, 1300e3, false},
		{"noch keine geglaettete Rate", func(e *estimator) { e.rateAvg, e.avgPrev = 0, 0 }, 1300e3, false},
	} {
		e := neu()
		if c.mod != nil {
			c.mod(e)
		}
		if got := e.blip(c.rate, t0); got != c.want {
			t.Errorf("%s: blip = %v, erwartet %v", c.name, got, c.want)
		}
		if n := e.blips; (n == 1) != c.want {
			t.Errorf("%s: Zaehler %d", c.name, n)
		}
	}
	// nicht zweimal binnen blipGap: der naechste Takt (200 ms) nicht, der uebernaechste (400 ms) wieder
	e := neu()
	tick := int64(200 * time.Millisecond)
	if !e.blip(1300e3, t0) || e.blip(1300e3, t0+tick) || !e.blip(1300e3, t0+2*tick) || e.blips != 2 {
		t.Errorf("Folge im Takt von 200 ms: Zaehler %d (erwartet: ja, nein, ja)", e.blips)
	}
	// Grenze: verspaeteter Takt (310 ms nach dem entschuldigten) wird ebenfalls entschuldigt
	e = neu()
	if !e.blip(1300e3, t0) || !e.blip(1300e3, t0+int64(310*time.Millisecond)) {
		t.Error("verspaeteter Takt: nicht entschuldigt - die dokumentierte Grenze (blipGap) stimmt nicht mehr, NODE.md anpassen")
	}
}
