package sfu

import (
	"testing"
	"time"
)

// Anrufbeginn (srcSettle): Die erste Messung einer Quelle stammt aus einem Teil-Takt, der Kodierer faehrt erst hoch -
// h steht mit 250 kbit/s statt 1250 in der Rechnung. Frueher ging der Schritt auf h damit im ersten Takt durch (und
// scheiterte im Labor fast immer binnen 2,4 s am noch nicht eingeschwungenen GCC-Ziel, h dann erst nach 8 s). Jetzt
// zaehlt fuer den Schritt hinauf auf eine junge Quelle mindestens die angenommene Rate.

// anlauf faehrt einen Anrufbeginn: Bein und Quellen beginnen zugleich, h wird im ersten Takt mit 250 kbit/s gemessen
// und sendet danach 1250 kbit/s. Liefert Schicht und Schaetzung je Takt.
func anlauf(t *testing.T, probing bool, link float64, d time.Duration) (layers []string, est []int, er *estRoom) {
	er = newEstCfg(t, 250e3, 1200e3, 1, probing)
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

func firstH(layers []string) int {
	for k, l := range layers {
		if l == "h" {
			return k
		}
	}
	return -1
}

// TestAnlaufJungeQuelle: Auf freier Leitung kommt der erste Schritt auf h nicht im Anlauf der Quelle, sondern nach
// rund 2 s - und haelt. Mit und ohne Probing gleich (Anlauf wie ohne Probing).
func TestAnlaufJungeQuelle(t *testing.T) {
	var seq [2][]string
	for i, probing := range []bool{false, true} {
		layers, est, er := anlauf(t, probing, 0, 10*time.Second)
		seq[i] = layers
		k := firstH(layers)
		if layers[0] != "m" {
			t.Fatalf("probing=%v: erster Takt auf %q, soll m (h ist jung: angenommene Rate passt nicht in die Anfangsschaetzung)", probing, layers[0])
		}
		// Takt k endet (k+1) x 200 ms nach Beginn
		if at := time.Duration(k+1) * 200 * time.Millisecond; k < 0 || at < srcSettle || at > srcSettle+time.Second {
			t.Fatalf("probing=%v: erster Schritt auf h in Takt %d (%v), soll %v bis %v nach Beginn: %v", probing, k, at, srcSettle, srcSettle+time.Second, layers)
		}
		if est[k] <= 1200 {
			t.Fatalf("probing=%v: Schritt auf h mit der Schaetzung auf dem Anfangswert (%d kbit/s)", probing, est[k])
		}
		for j := k; j < len(layers); j++ {
			if layers[j] != "h" {
				t.Fatalf("probing=%v: h haelt nicht (Takt %d auf %q): %v", probing, j, layers[j], layers)
			}
		}
		if probing && er.l.pstat.Started != 0 {
			t.Fatalf("Probe im Anlauf: %+v", er.l.pstat)
		}
		t.Logf("probing=%v: m ab Takt 0, h ab Takt %d (%.1f s) mit Schaetzung %d kbit/s", probing, k, float64(k+1)*0.2, est[k])
	}
	for k := range seq[0] {
		if seq[0][k] != seq[1][k] {
			t.Fatalf("Takt %d: ohne Probing %q, mit %q", k, seq[0][k], seq[1][k])
		}
	}
}

// TestAnlaufAlt: Gegenprobe - ohne die Regel (srcSettle 0) geht der Schritt im ersten Takt mit der Schaetzung auf dem
// Anfangswert durch (der Zustand, den das Labor als Ausloeser des Rueckfalls gemessen hat).
func TestAnlaufAlt(t *testing.T) {
	old := srcSettle
	srcSettle = 0
	t.Cleanup(func() { srcSettle = old })
	layers, est, _ := anlauf(t, false, 0, 2*time.Second)
	if layers[0] != "h" || est[0] != 1200 {
		t.Fatalf("ohne srcSettle: erster Takt %q mit %d kbit/s, erwartet h mit 1200", layers[0], est[0])
	}
}

// TestAnlaufEngeLeitung: Auf einer Leitung, die h nicht traegt (1000 kbit/s), wird es durch die Regel nicht schlechter:
// nicht mehr Versuche auf h als ohne sie und nicht weniger Zeit auf m.
func TestAnlaufEngeLeitung(t *testing.T) {
	count := func() (tries, onM int) {
		layers, _, _ := anlauf(t, false, 1000e3, 60*time.Second)
		for k, l := range layers {
			if l == "h" && (k == 0 || layers[k-1] != "h") {
				tries++
			}
			if l == "m" {
				onM++
			}
		}
		return tries, onM
	}
	tries, onM := count()
	old := srcSettle
	srcSettle = 0
	t.Cleanup(func() { srcSettle = old })
	tries0, onM0 := count()
	if tries > tries0 || onM < onM0 {
		t.Fatalf("enge Leitung: mit Regel %d Versuche auf h, %d Takte auf m; ohne %d und %d", tries, onM, tries0, onM0)
	}
	t.Logf("enge Leitung 60 s: Versuche auf h %d (ohne Regel %d), Takte auf m %d (ohne %d)", tries, tries0, onM, onM0)
}

// TestAnlaufAlteQuelle: Die Regel gilt nur fuer junge Quellen. Ein Bein, das zu laufenden Quellen stoesst (Beitritt zu
// einem laufenden Anruf, Wegwechsel), plant Takt fuer Takt wie ohne sie.
func TestAnlaufAlteQuelle(t *testing.T) {
	run := func() (out []string) {
		er := newEstCfg(t, 1250e3, 1200e3, 1, false)
		er.run(5 * time.Second)
		l2, err := er.r.AddLeg(Grant{Room: "raumschaetzer", Peer: "spaet", Leg: "spaet-b", Role: RoleBoth}, PathUDP, Want{Audio: 1, Speaker: true})
		if err != nil {
			t.Fatal(err)
		}
		gcc := 1200e3
		l2.Ext = func() int { return int(gcc) }
		for k := 0; k < 40; k++ {
			er.tick()
			gcc += 10e3
			for _, s := range l2.Layout() {
				if s.Class == "speaker" {
					out = append(out, s.Layer+":"+time.Duration(l2.EstimateKbit()).String())
				}
			}
		}
		return out
	}
	with := run()
	old := srcSettle
	srcSettle = 0
	t.Cleanup(func() { srcSettle = old })
	without := run()
	if len(with) != len(without) || len(with) == 0 {
		t.Fatalf("Laenge %d / %d", len(with), len(without))
	}
	for k := range with {
		if with[k] != without[k] {
			t.Fatalf("Takt %d: mit Regel %s, ohne %s", k, with[k], without[k])
		}
	}
	if with[len(with)-1][0] != 'h' {
		t.Fatalf("spaeter Beitritt erreicht h nicht: %v", with)
	}
}
