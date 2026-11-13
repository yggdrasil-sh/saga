package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
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

// TestProbingKlemmungNurNachSchritt (Pruefung 3, Fund 2): Die Ausnahme "gefallenes Ziel unter der Rate ist eine
// Klemmung, kein Stau" (estimator.clamped) gilt nur clampWin lang nach einem Schritt nach oben. Danach reagiert ein
// Bein mit Probing auf ein gefallenes Ziel Takt fuer Takt wie eines ohne (Stand vor dem Probing) - auch wenn das Ziel
// ueber der Senderate der letzten 500 ms liegt (Rate schwankt, Schluesselbild).
func TestProbingKlemmungNurNachSchritt(t *testing.T) {
	type env struct {
		e   estimator
		l   *Leg
		now int64
		gcc float64
	}
	mk := func(probing bool) *env {
		v := &env{l: &Leg{fl: fwd.NewLeg()}, now: int64(100 * time.Second), gcc: 3e6}
		t.Cleanup(v.l.fl.Close)
		v.e.init(1.2e6, 25e6)
		v.e.probing = probing
		v.l.Ext = func() int { return int(v.gcc) }
		return v
	}
	step := func(v *env, rate float64) {
		v.l.fl.Stats.BytesOut.Add(uint64(rate * 0.2 / 8))
		v.l.fl.Stats.HdrExtra.Add(uint64(rate * 0.2 / 8 / 100)) // Kopf-Erweiterungen: 1 %
		v.now += int64(200 * time.Millisecond)
		v.e.update(v.l, 0.2, v.now)
	}
	// Lage: 540 kbit/s, dann springt die Rate auf 1290 und das Ziel faellt im selben Takt von 3000 auf 1100 kbit/s -
	// unter die neue Rate, aber ueber die Senderate der letzten 500 ms (848 kbit/s). after: so lange vor dem Sprung
	// lag der letzte Schritt nach oben (0 = keiner).
	jump := func(probing bool, after time.Duration) *env {
		v := mk(probing)
		for i := 0; i < 50; i++ {
			step(v, 540e3)
		}
		if after > 0 {
			v.e.stepAt = v.now + int64(200*time.Millisecond) - int64(after)
		}
		v.e.bps = 2e6
		v.gcc = 1100e3
		step(v, 1290e3)
		return v
	}
	// (i) kurz nach dem Schritt: Klemmung, die Schonfrist beginnt, die Schaetzung bleibt
	for _, after := range []time.Duration{200 * time.Millisecond, time.Second, clampWin - 200*time.Millisecond} {
		if v := jump(true, after); !v.e.lag || v.e.lagDead || v.e.bps < 2e6 {
			t.Fatalf("%v nach dem Schritt: lag=%v gescheitert=%v schaetzung=%.0f (erwartet: Schonfrist)", after, v.e.lag, v.e.lagDead, v.e.bps)
		}
	}
	// (ii) ohne Schritt bzw. lange danach: Stau, sofort - dieselbe Schaetzung wie ohne Probing, Takt fuer Takt
	for _, after := range []time.Duration{0, clampWin, 10 * time.Second, time.Minute} {
		on, off := jump(true, after), jump(false, 0)
		for i := 0; ; i++ {
			if on.e.lag || on.e.bps != off.e.bps || on.e.holdUntil != off.e.holdUntil || on.e.lagDead != off.e.lagDead {
				t.Fatalf("%v nach dem Schritt, Takt %d: mit Probing lag=%v schaetzung=%.0f, ohne lag=%v schaetzung=%.0f",
					after, i, on.e.lag, on.e.bps, off.e.lag, off.e.bps)
			}
			if i == 20 {
				break
			}
			step(on, 1290e3)
			step(off, 1290e3)
		}
		if on.e.bps > 1100e3+1 {
			t.Fatalf("%v nach dem Schritt: schaetzung %.0f folgt dem Ziel nicht", after, on.e.bps)
		}
	}
	// ohne Probing aendert auch ein frischer Schritt nichts
	if v := jump(false, time.Second); v.e.lag {
		t.Fatal("ohne Probing entschuldigt")
	}
}

// TestProbingDauerstau (Pruefung 3, Fund 2): leichter Dauerstau auf h, lange nach dem letzten Schritt - die Leitung
// liegt 3 bis 8 % unter der Rate, GCC springt zurueck auf 85 % dessen, was es als empfangen rechnet (bis 8 % ueber
// der Senderate des Beins, estRoom.bias). Das Ziel landet damit zwischen 78 und 89 % der Rate - teils in dem Band, in
// dem bis Pruefung 3 eine Schonfrist begann (ueber 87 % der Senderate). Der Platz faellt mit Probing im selben Takt wie
// ohne.
func TestProbingDauerstau(t *testing.T) {
	for _, under := range []float64{0.03, 0.05, 0.08} {
		for _, bias := range []float64{0, 0.04, 0.08} {
			var d [2]time.Duration
			var v [2]float64
			for i, on := range []bool{false, true} {
				er := newEstCfg(t, 1250e3, 810e3, 1, on)
				er.pion, er.bias = true, bias
				er.until("h", 15*time.Second)
				er.run(time.Minute)
				if er.layer() != "h" {
					t.Fatalf("probing %v: h haelt nicht", on)
				}
				er.link = (1 - under) * (1250e3 + estAudio)
				start := er.now
				for er.layer() == "h" && er.now-start < int64(20*time.Second) {
					er.tick()
					if v[i] == 0 && er.over >= 2 {
						v[i] = er.gcc / (1250e3 + estAudio)
					}
				}
				d[i] = time.Duration(er.now - start)
			}
			t.Logf("leitung %2.0f %% unter der Rate, pion liest %2.0f %% mehr: ziel nach dem ruecksprung %.3f x rate, rueckfall ohne Probing nach %v, mit nach %v",
				under*100, bias*100, v[1], d[0], d[1])
			if d[1] != d[0] || d[0] >= 20*time.Second {
				t.Fatalf("leitung %.0f %% unter der Rate (bias %.2f): rueckfall ohne Probing nach %v, mit nach %v", under*100, bias, d[0], d[1])
			}
		}
	}
}

// TestProbingWegwechselFreierVersuch (Pruefung 3, Fund 3): Der freie Versuch zum Anrufbeginn ist verbraucht, h haelt -
// keine gescheiterte Probe, kein Aufschub. Nach einem Wegwechsel (udp -> tcp -> udp) lebt er nicht wieder auf: Auf
// der inzwischen engen Leitung gibt es keinen Versuch auf h ohne Probe.
func TestProbingWegwechselFreierVersuch(t *testing.T) {
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.until("h", 15*time.Second)
	er.run(20 * time.Second)
	if !er.l.firstUsed || er.l.pf.pd != 0 || er.l.pstat.Started != 0 {
		t.Fatalf("vor dem Wegwechsel: verbraucht %v, abstand %v, proben %+v", er.l.firstUsed, er.l.pf.pd, er.l.pstat)
	}
	for _, s := range er.l.slots {
		if s.dw != ([4]dwell{}) {
			t.Fatalf("aufschub vor dem Wegwechsel: %+v", s.dw)
		}
	}
	swap := func(ref string, path Path) {
		er.r.RemoveLeg(er.l.Ref)
		l, err := er.r.AddLeg(Grant{Room: "raumschaetzer", Peer: "empf", Leg: ref, Role: RoleBoth}, path, Want{Audio: 1, Speaker: true})
		if err != nil {
			t.Fatal(err)
		}
		if path == PathUDP {
			l.Ext = func() int { return int(er.gcc) }
			l.ExtOver = func() bool { return er.over >= 2 }
		}
		er.l, er.sent = l, 0
	}
	er.link = 1000e3 // traegt m, nicht h
	swap("empf-tcp", PathTCP)
	er.run(5 * time.Second)
	if !er.l.firstUsed {
		t.Fatal("auf dem TCP-Bein: freier Versuch nicht als verbraucht gemerkt")
	}
	swap("empf-udp", PathUDP)
	er.tick()
	if !er.l.firstUsed {
		t.Fatal("nach dem Wegwechsel: freier Versuch lebt wieder auf")
	}
	res := er.measure(40*time.Second, nil)
	for k, d := range res.seen {
		if vrank(k[0]) > 2 {
			t.Fatalf("nach dem Wegwechsel %v auf h ohne bestandene Probe: %v, verlauf %v", d, res, res.seen)
		}
	}
	if res.started == 0 || res.ok != 0 {
		t.Fatalf("nach dem Wegwechsel: %v (erwartet: Proben fuer h, keine bestanden)", res)
	}
	// Gegenstueck: Ein Bein, das seinen freien Versuch noch hat (kein Schritt nach oben noetig gewesen), hinterlaesst
	// nichts - das naechste Bein beginnt wie ein neuer Anruf
	fr := newEstCfg(t, 1250e3, 810e3, 1, true)
	fr.tick()
	if fr.l.firstUsed {
		t.Fatal("freier Versuch schon im ersten Takt verbraucht")
	}
	fr.r.RemoveLeg(fr.l.Ref)
	if len(fr.r.mem) != 0 {
		t.Fatalf("gemerkt ohne Vorgeschichte: %d", len(fr.r.mem))
	}
}

// TestProbingAnlaufWieOhne (Labor, Runde 3: h stabil ab 8,4 s statt 3,0 s; mit Probing stand die Schaetzung beim Schritt
// auf h beim Anfangswert, ohne bei 1248 bzw. 1297 kbit/s): h geht beim Aufbau durch, der Ausgang wartet noch auf das
// Schluesselbild - das Bein sendet fast nichts -, und der Kodierer faehrt hoch. Ohne Probing folgt der Deckel der
// Schaetzung dem Bedarf der laufenden Auswahl (4 % je Takt). Mit Probing blieb sie bis zum ersten Bild beim Anfangswert
// (im Modell haelt h trotzdem; der Rueckfall im Labor ist hier nicht nachgestellt). Im Anlauf - der freie Versuch des
// Beins ist nicht verbraucht - laeuft jetzt alles wie ohne Probing: Schicht und Schaetzung Takt fuer Takt gleich.
// (Pruefung bwe0: der Anlauf muss auch ohne die Regel fuer junge Quellen gelten - seit der Streichung der Anlauf-Regel,
// Stufe 3b, prueft dieser Test genau das wieder; TestProbingAnlaufOhneJungeQuelle entfiel als derselbe Lauf.)
func TestProbingAnlaufWieOhne(t *testing.T) {
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
	for k := range seq[0] {
		if seq[0][k].layer != "h" {
			t.Fatalf("ohne Probing: Takt %d auf %q (Modell soll h von Anfang an halten)", k, seq[0][k].layer)
		}
		if seq[1][k] != seq[0][k] {
			t.Fatalf("Takt %d (%.1f s): mit Probing %+v, ohne %+v", k, float64(k)*0.2, seq[1][k], seq[0][k])
		}
	}
	if seq[0][0].est != 1200 || seq[1][3].est <= 1200 {
		t.Fatalf("Schaetzung waechst im Anlauf nicht mit dem Bedarf: %+v", seq[1][:5])
	}
	t.Logf("Anlauf: h ab Takt 0 mit und ohne Probing, Schaetzung %d -> %d -> %d kbit/s", seq[1][0].est, seq[1][1].est, seq[1][3].est)
}
