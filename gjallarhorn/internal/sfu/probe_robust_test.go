package sfu

import (
	"testing"
	"time"
)

// TestProbingKlemmung (Labor 10./11.11.: 16 bis 54 % der Proben auf freier Leitung "fail:gcc"): pion-GCC klemmt sein
// Ziel nahe der Rate des letzten Ruecksprungs auf 1,5 x der empfangenen Rate. Ist das Ziel seither weit gewachsen,
// faellt es dabei in einem Zug - ausgeloest von den Fuelldaten der Probe, ohne jeden Stau. Die Probe wertet das nicht
// mehr als Stau (fallClass): Sie besteht, h kommt und haelt - auch wenn dieselbe Klemmung danach den Wechsel selbst
// trifft (Ziel unter der neuen Rate, estimator.clamped). Auf enger Leitung scheitert die Probe aus derselben Lage
// weiter sofort.
func TestProbingKlemmung(t *testing.T) {
	for _, c := range []struct {
		name  string
		decAt float64 // empfangene Rate beim letzten Ruecksprung
	}{{"band-in-der-rampe", 700e3}, {"band-im-fenster", 900e3}, {"band-bei-h", 1300e3}} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstProbe(t, 1250e3, 810e3, 1)
			er.pion = true
			er.decAt, er.gcc = c.decAt, 3e6 // seit dem Ruecksprung ungebremst gewachsen
			er.until("m", 2*time.Second)
			d := er.until("h", 15*time.Second)
			ps := er.l.ProbeStats()
			if ps.Failed != 0 || ps.Unclear != 0 || ps.Passed != 1 || ps.Clamped == 0 {
				t.Fatalf("proben %+v (erwartet: eine, bestanden, mit erkannter Klemmung)", ps)
			}
			res := er.measure(time.Minute, nil)
			if er.layer() != "h" || res.fails != 0 || res.ups != 0 || res.started != 0 {
				t.Fatalf("h haelt nicht: %v (gcc %.0f, est %d)", res, er.gcc, er.l.EstimateKbit())
			}
			t.Logf("h nach %v, proben %+v", d, ps)
		})
	}
	// enge Leitung (1,0 Mbit/s), dieselbe Lage: Die Probe scheitert, kein Wechsel
	er := newEstProbe(t, 1250e3, 810e3, 1)
	er.pion, er.link = true, 1000e3
	er.decAt, er.gcc = 900e3, 3e6
	er.until("m", 2*time.Second)
	res := er.measure(time.Minute, nil)
	ps := er.l.ProbeStats()
	if res.ups != 0 || ps.Passed != 0 || ps.Failed == 0 {
		t.Fatalf("enge Leitung: %v, proben %+v", res, ps)
	}
	if ps.Unclear > 1 {
		t.Fatalf("enge Leitung: %d unklare Proben", ps.Unclear)
	}
	t.Logf("eng: %v, proben %+v", res, ps)
}

// probeOver faehrt das Modell, bis eine Probe endet, und meldet dabei in den Takten hit (gezaehlt ab Beginn der vollen
// Fuellrate) Ueberlast, ohne dass das Ziel faellt. Liefert den Ausgang (ProbeStats.Last).
func probeOver(t *testing.T, er *estRoom, hit ...int) string {
	t.Helper()
	over := false
	er.l.ExtOver = func() bool { return over }
	n0 := er.l.pstat.Started
	for i := 0; i < 300; i++ {
		over = false
		if p := &er.l.pr; p.stage == 2 {
			for _, h := range hit {
				over = over || p.n == h
			}
		}
		was := er.l.pr.stage
		er.tick()
		if was != 0 && er.l.pr.stage == 0 && er.l.pstat.Started > n0 {
			return er.l.pstat.Last
		}
	}
	t.Fatalf("keine Probe in 60 s (est %d, gcc %.0f)", er.l.EstimateKbit(), er.gcc)
	return ""
}

// TestProbingUnklar: ein einzelner Hinweis auf Stau (hier: Ueberlast laut Laufzeit-Messung in einem Takt, das Ziel faellt
// nicht) laesst die Probe nicht scheitern.
//   - Steigt das Ziel danach zwei Takte in Folge, ist er ausgeraeumt: bestanden.
//   - Steht das Ziel, bleibt das Ergebnis unklar: Abbruch ohne Wertung (kein Abstand), Wiederholung nach probeRetry.
//     Ist auch die Wiederholung unklar: gescheitert, mit Abstand.
//   - Zweiter Hinweis, solange der erste offen ist: gescheitert, sofort.
func TestProbingUnklar(t *testing.T) {
	// ausgeraeumt: das Ziel steigt weiter
	er := newEstProbe(t, 1250e3, 810e3, 1)
	er.until("m", 2*time.Second)
	if got := probeOver(t, er, 1); got != probeOK {
		t.Fatalf("einzelner Hinweis, Ziel steigt: %q, proben %+v", got, er.l.pstat)
	}
	// unklar: das Ziel steht (kein Anstieg im Modell)
	er = newEstProbe(t, 1250e3, 810e3, 1)
	er.grow, er.capF = 0, 0
	er.until("m", 2*time.Second)
	if got := probeOver(t, er, 1); got != probeAbort+":"+whyUnclear {
		t.Fatalf("einzelner Hinweis, Ziel steht: %q, proben %+v", got, er.l.pstat)
	}
	end := er.now
	if er.l.pf.pd != 0 || er.l.pstat.Unclear != 1 || er.l.pstat.Failed != 0 || er.l.fl.Padding() != 0 {
		t.Fatalf("nach der unklaren Probe: abstand %v, proben %+v, fuellrate %d", er.l.pf.pd, er.l.pstat, er.l.fl.Padding())
	}
	if got := probeOver(t, er, 1); got != probeFail+":over" {
		t.Fatalf("wiederholung ebenfalls unklar: %q, proben %+v", got, er.l.pstat)
	}
	if gap := time.Duration(er.now - end); gap < probeRetry || gap > probeRetry+3*time.Second {
		t.Fatalf("wiederholung %v nach der unklaren Probe (erwartet rund %v)", gap, probeRetry)
	}
	if er.l.pf.pd != probeGap || er.l.pstat.Failed != 1 || er.l.pstat.Unclear != 1 || er.layer() != "m" {
		t.Fatalf("nach der Wiederholung: abstand %v, proben %+v, schicht %q", er.l.pf.pd, er.l.pstat, er.layer())
	}
	// bestaetigt: zwei Hinweise in Folge
	er = newEstProbe(t, 1250e3, 810e3, 1)
	er.until("m", 2*time.Second)
	if got := probeOver(t, er, 1, 2); got != probeFail+":over" || er.l.pstat.Unclear != 0 {
		t.Fatalf("zwei Hinweise in Folge: %q, proben %+v", got, er.l.pstat)
	}
}

// TestProbingRueckkehr (Labor: nach einem gewoehnlichen Rueckfall h -> m blieb das Bild bis 279 s auf m, ohne Probing 4
// bis 6 s): Faellt der Platz von einer Schicht, die er mindestens upHold gehalten hat, geht er einmal ohne Probe
// zurueck - zeitlich wie ohne Probing. Haelt die Schicht danach keine upHold, braucht der naechste Weg zurueck eine
// Probe. Auf einer Leitung, die inzwischen eng ist, bleibt es bei dem einen Versuch.
func TestProbingRueckkehr(t *testing.T) {
	burst := func(er *estRoom) time.Duration { // Stau-Schub von 2 s; Zeit vom Rueckfall bis wieder h
		er.loss = true
		er.until("m", 3*time.Second)
		fell := er.now
		er.run(2 * time.Second)
		er.loss = false
		er.until("h", 2*time.Minute)
		return time.Duration(er.now - fell)
	}
	var back [2]time.Duration
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		er.until("h", 15*time.Second)
		er.run(upHold + 5*time.Second)
		n0 := er.l.pstat.Started
		back[i] = burst(er)
		if on && er.l.pstat.Started != n0 {
			t.Fatalf("Rueckkehr nach langem Halten mit Probe: %+v", er.l.pstat)
		}
		if !on {
			continue
		}
		// h haelt jetzt erst 10 s: Der naechste Rueckfall braucht eine Probe
		er.run(10 * time.Second)
		d := burst(er)
		if got := er.l.pstat; got.Started != n0+1 || got.Passed != got.Started {
			t.Fatalf("Rueckkehr nach kurzem Halten: proben %+v (erwartet eine, bestanden)", got)
		}
		t.Logf("nach kurzem Halten: zurueck nach %v (mit Probe)", d)
		// ... und nach wieder langem Halten erneut ohne
		er.run(upHold + 5*time.Second)
		n1 := er.l.pstat.Started
		if burst(er); er.l.pstat.Started != n1 {
			t.Fatalf("zweite Rueckkehr nach langem Halten mit Probe: %+v", er.l.pstat)
		}
	}
	t.Logf("zurueck auf h nach langem Halten: ohne Probing %v, mit %v", back[0], back[1])
	if back[1] > back[0]+400*time.Millisecond || back[1] > 9*time.Second {
		t.Fatalf("Rueckkehr mit Probing %v, ohne %v", back[1], back[0])
	}
	// Leitung wird eng, waehrend h laeuft: ein freier Versuch, dann nur noch Proben
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.until("h", 15*time.Second)
	er.run(upHold + 5*time.Second)
	er.link = 1000e3
	er.until("m", 5*time.Second)
	res := er.measure(5*time.Minute, nil)
	if res.ups != 1 || res.fails != 1 || res.failed < 3 || res.ok != 0 {
		t.Fatalf("Leitung eng geworden: %v (erwartet ein Versuch ohne Probe, danach nur gescheiterte Proben)", res)
	}
	t.Logf("eng geworden: %v", res)
}

// TestProbingAnrufbeginn (Labor: h stabil nach 12,8 s statt 8,8 s): Der erste Schritt nach oben eines Beins, den die
// Schaetzung allein nicht hergibt, laeuft ohne Probe und zur selben Zeit wie ohne Probing. Danach ist der freie Versuch
// verbraucht. Faellt zu Anrufbeginn ein Schritt, der mit einer noch nicht eingeschwungenen Quelle durchging (h mit
// 300 statt 1250 kbit/s), zaehlt das nicht als gescheiterter Versuch: Es bleibt beim Aufschub wie ohne Probing, und
// der Weg zurueck braucht keine Probe.
func TestProbingAnrufbeginn(t *testing.T) {
	var d [2]time.Duration
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		d[i] = er.until("h", 15*time.Second)
		if on && (er.l.pstat.Started != 0 || !er.l.firstUsed) {
			t.Fatalf("erster Schritt: proben %+v, freier Versuch verbraucht %v", er.l.pstat, er.l.firstUsed)
		}
		er.run(20 * time.Second)
		if er.layer() != "h" {
			t.Fatalf("probing %v: h haelt nicht", on)
		}
	}
	t.Logf("h zu Anrufbeginn: ohne Probing %v, mit %v", d[0], d[1])
	if d[1] != d[0] {
		t.Fatalf("h zu Anrufbeginn mit Probing nach %v, ohne nach %v", d[1], d[0])
	}
	// Quelle nicht eingeschwungen: h geht sofort durch (300 kbit/s), dann faehrt der Kodierer hoch und ein Stau-Schub
	// nimmt h binnen upFail zurueck
	var back [2]time.Duration
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 300e3, 810e3, 1, on)
		er.until("h", 2*time.Second)
		er.rates["h"], er.allR[2] = 1250e3, 1250e3
		er.run(600 * time.Millisecond)
		er.loss = true
		er.until("m", 3*time.Second)
		er.loss = false
		if on && (er.l.pf.pd != 0 || er.l.firstUsed) {
			t.Fatalf("Rueckfall im Anlauf zaehlt als Fehlversuch: abstand %v, freier Versuch verbraucht %v", er.l.pf.pd, er.l.firstUsed)
		}
		back[i] = er.until("h", time.Minute)
		if on && er.l.pstat.Started != 0 {
			t.Fatalf("zurueck auf h mit Probe: %+v", er.l.pstat)
		}
	}
	t.Logf("zurueck auf h nach dem Anlauf: ohne Probing %v, mit %v", back[0], back[1])
	if back[1] != back[0] {
		t.Fatalf("zurueck auf h mit Probing nach %v, ohne nach %v", back[1], back[0])
	}
}

// TestProbingVerlust: Verlust als Hinweis auf Stau - gemessen gegen den Stand vor der Probe (hier 1 %). Ein Ausschlag in
// einem einzelnen Takt (pion zaehlt ein Paket, das zur Rueckmeldung noch fehlt, als verloren) zaehlt nicht; steht der
// Verlust mit den Fuelldaten an (Leitung mit Ratengrenze ohne Warteschlange), scheitert die Probe mit "loss".
func TestProbingVerlust(t *testing.T) {
	for _, c := range []struct {
		name string
		loss func(n int) float64 // Verlustanteil im n-ten Takt der vollen Fuellrate
		want string
	}{
		{"ausschlag", func(n int) float64 {
			if n == 1 {
				return 0.12
			}
			return 0.01
		}, probeOK},
		{"anhaltend", func(n int) float64 { return 0.09 }, probeFail + ":loss"},
		{"wie-zuvor", func(n int) float64 { return 0.04 }, probeOK},
	} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstProbe(t, 1250e3, 810e3, 1)
			loss := 0.01
			er.l.ExtStat = func() ExtStat { return ExtStat{Loss: loss} }
			er.until("m", 2*time.Second)
			got := ""
			for i := 0; i < 300 && got == ""; i++ {
				loss = 0.01
				if p := &er.l.pr; p.stage == 2 {
					loss = c.loss(p.n)
				}
				was := er.l.pr.stage
				er.tick()
				if was != 0 && er.l.pr.stage == 0 {
					got = er.l.pstat.Last
				}
			}
			if got != c.want {
				t.Fatalf("ausgang %q, erwartet %q (proben %+v)", got, c.want, er.l.pstat)
			}
		})
	}
}
