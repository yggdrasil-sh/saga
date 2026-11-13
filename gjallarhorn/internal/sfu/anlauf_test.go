package sfu

import (
	"testing"
	"time"
)

// Anrufbeginn: Die erste Messung einer Quelle stammt aus einem Teil-Takt, der Kodierer faehrt erst hoch - h steht mit
// 250 kbit/s statt 1250 in der Rechnung, der Schritt auf h geht im ersten Takt mit der Schaetzung auf dem Anfangswert
// durch. Die Anlauf-Regel (Schritt hinauf auf eine junge Quelle mit der angenommenen Rate, bis Stufe 3 bei eigen) ist
// gestrichen (Stufe 3b): Im Labor (labbwe2 bei gcc, labbwe3 bei eigen) brachte sie nichts Messbares - eigen ohne Regel
// 0 von 24 Richtungen mit Rueckfall in 15 s, mit Regel 1 von 24 - und kostete rund 2 s bis h.

// anlaufArt faehrt einen Anrufbeginn: Bein und Quellen beginnen zugleich, h wird im ersten Takt mit 250 kbit/s gemessen
// und sendet danach 1250 kbit/s; wahlweise mit Probing und mit dem Modell des eigenen Detektors (eigen). Liefert
// Schicht und Schaetzung je Takt.
func anlaufArt(t *testing.T, probing, eigen bool, link float64, d time.Duration) (layers []string, est []int, er *estRoom) {
	er = newEstCfg(t, 250e3, 1200e3, 1, probing)
	if eigen {
		er.eigen()
	}
	er.link = link
	for k := 0; k < int(d/(200*time.Millisecond)); k++ {
		if k == 1 {
			er.rates["h"], er.allR[2] = 1250e3, 1250e3
		}
		er.tick()
		layers, est = append(layers, er.layer()), append(est, er.l.EstimateKbit())
	}
	return layers, est, er
}

// TestAnlaufJeArt (Stufe 3b, F1): Ohne Anlauf-Regel plant der Anrufbeginn bei gcc und eigen, mit und ohne Probing,
// wie am Stand vor der Regel (22a5bd3): h im ersten Takt mit der Schaetzung auf dem Anfangswert, Schicht und
// Schaetzung Takt fuer Takt gleich dem Lauf gcc ohne Probing, keine Probe.
func TestAnlaufJeArt(t *testing.T) {
	ref, refEst, _ := anlaufArt(t, false, false, 0, 6*time.Second)
	if ref[0] != "h" || refEst[0] != 1200 {
		t.Fatalf("gcc ohne Probing: erster Takt %q mit %d kbit/s, erwartet h mit 1200", ref[0], refEst[0])
	}
	for _, probing := range []bool{false, true} {
		for _, eigen := range []bool{false, true} {
			layers, est, er := anlaufArt(t, probing, eigen, 0, 6*time.Second)
			if probing && er.l.pstat.Started != 0 {
				t.Fatalf("probing, eigen=%v: Probe im Anlauf: %+v", eigen, er.l.pstat)
			}
			for k := range ref {
				if layers[k] != ref[k] || est[k] != refEst[k] {
					t.Fatalf("probing=%v, eigen=%v: Takt %d %s/%d, gcc ohne Probing %s/%d", probing, eigen, k, layers[k], est[k], ref[k], refEst[k])
				}
			}
		}
	}
}
