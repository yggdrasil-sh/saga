package sfu

import (
	"testing"
	"time"
)

// Anrufbeginn (srcSettle): Die erste Messung einer Quelle stammt aus einem Teil-Takt, der Kodierer faehrt erst hoch -
// h steht mit 250 kbit/s statt 1250 in der Rechnung. Frueher ging der Schritt auf h damit im ersten Takt durch (und
// scheiterte im Labor fast immer binnen 2,4 s am noch nicht eingeschwungenen GCC-Ziel, h dann erst nach 8 s). Jetzt
// zaehlt fuer den Schritt hinauf auf eine junge Quelle mindestens die angenommene Rate.

// Seit Stufe 3 (E2) gilt die Regel nur bei HORN_BWE=eigen (Leg.anlauf); die Tests der Regel selbst schalten sie mit
// Config.Anlauf ein (setAnlauf), TestAnlaufJeArt prueft den Standard je Art.

// setAnlauf stellt Config.Anlauf des Raums (Test-Einstellung HORN_ANLAUF).
func setAnlauf(er *estRoom, v int) {
	er.r.mu.Lock()
	er.r.cfg.Anlauf = v
	er.r.mu.Unlock()
}

// anlauf faehrt einen Anrufbeginn: Bein und Quellen beginnen zugleich, h wird im ersten Takt mit 250 kbit/s gemessen
// und sendet danach 1250 kbit/s. Die Anlauf-Regel ist an. Liefert Schicht und Schaetzung je Takt.
func anlauf(t *testing.T, probing bool, link float64, d time.Duration) (layers []string, est []int, er *estRoom) {
	return anlaufArt(t, probing, false, AnlaufAn, link, d)
}

// anlaufArt: wie anlauf, wahlweise mit dem Modell des eigenen Detektors (eigen) und mit Config.Anlauf = mode.
func anlaufArt(t *testing.T, probing, eigen bool, mode int, link float64, d time.Duration) (layers []string, est []int, er *estRoom) {
	er = newEstCfg(t, 250e3, 1200e3, 1, probing)
	if eigen {
		er.eigen()
	}
	setAnlauf(er, mode)
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
		setAnlauf(er, AnlaufAn)
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

// TestAnlaufJeArt (Stufe 3, E2): Die Anlauf-Regel gilt im Standard nur bei HORN_BWE=eigen. Bei gcc (und mit, das im
// Modell dasselbe ist: kein ExtSpan) geht h wie vor der Regel (Stand 22a5bd3) im ersten Takt mit der Schaetzung auf dem
// Anfangswert durch - Schicht und Schaetzung Takt fuer Takt gleich dem Lauf mit srcSettle 0. Bei eigen erst nach rund
// 2 s. Config.Anlauf (HORN_ANLAUF) ueberstimmt beides. Je mit und ohne Probing.
func TestAnlaufJeArt(t *testing.T) {
	type lauf struct {
		layers []string
		est    []int
	}
	run := func(probing, eigen bool, mode int) lauf {
		layers, est, er := anlaufArt(t, probing, eigen, mode, 0, 6*time.Second)
		if probing && er.l.pstat.Started != 0 {
			t.Fatalf("probing, eigen=%v, anlauf=%d: Probe im Anlauf: %+v", eigen, mode, er.l.pstat)
		}
		return lauf{layers, est}
	}
	for _, probing := range []bool{false, true} {
		// Bezug ohne Regel: srcSettle 0 (die Quelle ist nie jung) - so plante der Anlauf vor der Regel
		old := srcSettle
		srcSettle = 0
		ref := run(probing, false, AnlaufAn)
		srcSettle = old
		if ref.layers[0] != "h" || ref.est[0] != 1200 {
			t.Fatalf("probing=%v: Bezug ohne Regel %q mit %d kbit/s im ersten Takt", probing, ref.layers[0], ref.est[0])
		}
		for _, c := range []struct {
			name  string
			eigen bool
			mode  int
			regel bool
		}{
			{"gcc", false, AnlaufStandard, false},
			{"eigen", true, AnlaufStandard, true},
			{"gcc+HORN_ANLAUF=1", false, AnlaufAn, true},
			{"eigen+HORN_ANLAUF=0", true, AnlaufAus, false},
		} {
			got := run(probing, c.eigen, c.mode)
			if !c.regel {
				for k := range ref.layers {
					if got.layers[k] != ref.layers[k] || got.est[k] != ref.est[k] {
						t.Fatalf("probing=%v, %s: Takt %d %s/%d, ohne Regel %s/%d", probing, c.name, k, got.layers[k], got.est[k], ref.layers[k], ref.est[k])
					}
				}
				continue
			}
			k := firstH(got.layers)
			if got.layers[0] != "m" {
				t.Fatalf("probing=%v, %s: erster Takt %q, mit Regel soll m", probing, c.name, got.layers[0])
			}
			if at := time.Duration(k+1) * 200 * time.Millisecond; k < 0 || at < srcSettle || at > srcSettle+time.Second {
				t.Fatalf("probing=%v, %s: h in Takt %d, soll %v bis %v nach Beginn: %v", probing, c.name, k, srcSettle, srcSettle+time.Second, got.layers)
			}
			t.Logf("probing=%v, %s: h ab Takt %d (%.1f s)", probing, c.name, k, float64(k+1)*0.2)
		}
	}
}

// TestProbingAnlaufOhneJungeQuelle (Pruefung bwe0): Der Anlauf (bis der freie Versuch des Beins verbraucht ist, plant
// es wie ohne Probing) muss auch ohne die Regel fuer junge Quellen gelten - etwa fuer ein Bein, das zu laufenden
// Quellen stoesst. Seit srcSettle hielt kein Test den Anlauf mehr fest (TestProbingAnlaufWieOhne lief auch ohne ihn
// durch, weil h dort erst nach 2 s geht). Hier mit srcSettle 0: h geht beim Aufbau durch, der Ausgang wartet noch auf
// das Schluesselbild, der Kodierer faehrt hoch - Schicht und Schaetzung Takt fuer Takt gleich mit und ohne Probing,
// keine Probe.
func TestProbingAnlaufOhneJungeQuelle(t *testing.T) {
	old := srcSettle
	srcSettle = 0
	t.Cleanup(func() { srcSettle = old })
	type st struct {
		layer string
		est   int
	}
	var seq [2][]st
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1000e3, 810e3, 1, on)
		for k := 0; k < 100; k++ {
			if k >= 1 && k <= 3 { // der Kodierer faehrt hoch
				er.rates["h"] += 100e3
				er.allR[2] = er.rates["h"]
			}
			er.tick()
			if k < 5 { // noch kein Schluesselbild: Das Bein sendet nur Ton
				er.sent = 0
			}
			seq[i] = append(seq[i], st{er.layer(), er.l.EstimateKbit()})
		}
		if on && er.l.pstat.Started != 0 {
			t.Fatalf("Probe im Anlauf: %+v", er.l.pstat)
		}
	}
	if seq[0][0].layer != "h" {
		t.Fatalf("ohne Regel soll h im ersten Takt durchgehen, ist %q", seq[0][0].layer)
	}
	for k := range seq[0] {
		if seq[1][k] != seq[0][k] {
			t.Fatalf("Takt %d (%.1f s): mit Probing %+v, ohne %+v", k, float64(k)*0.2, seq[1][k], seq[0][k])
		}
	}
	if seq[1][3].est <= 1200 {
		t.Fatalf("Schaetzung waechst im Anlauf nicht mit dem Bedarf: %+v", seq[1][:5])
	}
}
