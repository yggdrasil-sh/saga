package bwe

import "testing"

// Spitze fuer das Halten nach einer bestandenen Probe (sfu, estimator.held): Calm (keine Schlange an der Spitze) und
// LossUp (Verlust gestiegen) getrennt.
//   - Schluesselbild-Buendel auf kurzem Puffer (Leitung 1400 kbit/s, 20 ms): Die Schlange bleibt unter 10 ms - Calm
//     gilt durchgehend; der Verlust steht hoechstens rund 1 s lang an der Spitze, dann widerlegt der Detektor wieder.
//   - echte Enge mit Puffer (Leitung 20 % unter der Rate, 300 ms): Calm faellt binnen 0,4 s.
//   - Policer (Leitung 30 % unter der Rate, 5 ms Puffer): die Schlange pendelt um die Schwelle von 10 ms, Calm faellt
//     und kommt wieder - aber LossUp steht dauerhaft (der Schub von 1,2 s reicht dafuer nicht, siehe held).
func TestSpitzeSchubUndEnge(t *testing.T) {
	ns := func(s float64) int64 { return int64(s * 1e9) }
	// lauf: sec Sekunden weiter, alle 20 ms die Spitze lesen; liefert, ob Calm durchgehend galt, wann es zuerst fiel
	// und wann LossUp zuletzt stand (Sekunden seit Beginn, -1 = nie)
	lauf := func(l *leitung, sec float64) (calm bool, firstNot, lastUp float64) {
		calm, firstNot, lastUp = true, -1, -1
		t0 := l.now
		for l.now < t0+sec {
			l.run(0.02)
			tp := l.d.Tip(ns(l.now))
			if !tp.Calm && calm {
				calm, firstNot = false, l.now-t0
			}
			if tp.LossUp {
				lastUp = l.now - t0
			}
			if tp.Clear != (tp.Calm && !tp.LossUp) {
				t.Fatalf("Clear %v, Calm %v, LossUp %v", tp.Clear, tp.Calm, tp.LossUp)
			}
		}
		return
	}
	for seed := int64(1); seed <= 5; seed++ {
		l := newLeitung(seed, 70, 1000)
		l.jitMs, l.capBps, l.bufMs = 2, 1400e3, 20
		l.run(10)
		for i := 0; i < 30; i++ { // Schluesselbild: 30 Pakete in 3 ms - der Puffer (3,5 kB) fasst davon rund 3
			l.send(KindVideo, 1100)
			l.now += 0.0001
		}
		calm, _, lastUp := lauf(l, 3)
		t.Logf("Buendel auf 1400 kbit/s mit 20 ms Puffer: Calm durchgehend %v, Verlust an der Spitze bis %.2f s danach, Zustand %v", calm, lastUp, l.state())
		if !calm || lastUp < 0 || lastUp > 1.0 || l.state() != Free {
			t.Fatalf("Buendel: Calm %v, LossUp bis %.2f s, Zustand %v", calm, lastUp, l.state())
		}
		// echte Enge mit Puffer
		l = newLeitung(seed, 160, 1000)
		l.jitMs = 2
		l.run(10)
		l.capBps, l.bufMs = 0.8*160*1000*8, 300
		calm, firstNot, _ := lauf(l, 3)
		if calm || firstNot > 0.4 {
			t.Fatalf("Enge mit Puffer: Calm faellt nach %.2f s", firstNot)
		}
		// Policer
		l = newLeitung(seed, 160, 1000)
		l.jitMs = 2
		l.run(10)
		l.capBps, l.bufMs = 0.7*160*1000*8, 5
		calm, firstNot, _ = lauf(l, 1.2)
		_, _, lastUp = lauf(l, 2)
		t.Logf("Policer 30 %% unter der Rate: Calm bis %.2f s (-1 = durchgehend), danach LossUp zuletzt bei %.2f von 2 s", firstNot, lastUp)
		if lastUp < 1.9 {
			t.Fatalf("Policer: LossUp steht nicht dauerhaft (zuletzt %.2f s)", lastUp)
		}
	}
}
