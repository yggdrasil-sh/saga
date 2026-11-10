package sfu

import (
	"fmt"
	"testing"
	"time"
)

// probeRun: Kennzahlen eines Modell-Laufs (schaetzer_test.go) mit bzw. ohne Probing.
type probeRun struct {
	ups, fails      int           // Schritte nach oben (Schicht ueber l) und davon binnen 5 s zurueckgenommene
	started, ok     int           // Proben
	failed, aborted int           //
	padMB           float64       // Fuelldaten
	over            time.Duration // Zeit ueber der Leitung (Medien + Fuelldaten)
	overMedia       time.Duration // ... davon nur Medien
	seen            map[string]time.Duration
}

func (p probeRun) String() string {
	return fmt.Sprintf("schritte %d, fehlversuche %d, proben %d (ok %d, gescheitert %d, abgebrochen %d), fuell %.2f MB, ueber der leitung %v (medien %v)",
		p.ups, p.fails, p.started, p.ok, p.failed, p.aborted, p.padMB, p.over, p.overMedia)
}

// measure faehrt das Modell d lang und zaehlt. step (optional) laeuft vor jedem Takt.
func (er *estRoom) measure(d time.Duration, step func(el time.Duration)) probeRun {
	res := probeRun{seen: map[string]time.Duration{}}
	type up struct {
		i  int
		at int64
	}
	var open []up
	start, pad0, pev0 := er.now, er.padMB, len(er.pevs)
	const dt = 200 * time.Millisecond
	for er.now-start < int64(d) {
		if step != nil {
			step(time.Duration(er.now - start))
		}
		was := er.video()
		er.tick()
		is := er.video()
		for i := range is {
			switch a, b := vrank(was[i]), vrank(is[i]); {
			case b > a && b > 1:
				res.ups++
				open = append(open, up{i, er.now})
			case b < a:
				for k := len(open) - 1; k >= 0; k-- {
					if open[k].i == i {
						if er.now-open[k].at < int64(5*time.Second) {
							res.fails++
						}
						open = append(open[:k], open[k+1:]...)
						break
					}
				}
			}
		}
		res.seen[is] += dt
		if er.link > 0 {
			if estAudio+er.sent > er.link {
				res.overMedia += dt
			}
			if estAudio+er.sent+float64(er.l.fl.Padding()) > er.link {
				res.over += dt
			}
		}
	}
	res.padMB = er.padMB - pad0
	for _, e := range er.pevs[pev0:] {
		switch e.Probe {
		case probeStart:
			res.started++
		case probeOK:
			res.ok++
		case probeFail:
			res.failed++
		case probeAbort:
			res.aborted++
		}
	}
	return res
}

// TestProbingEngeLeitung (a): Die Leitung traegt m, aber nicht h (1,0 bzw. 1,25 Mbit/s, h 1250 kbit/s), 10 min. Ohne
// Probing ist der Wechsel die Probe: 7 bis 8 Versuche, jeder mit Schluesselbild, Rueckwechsel und verdecktem Ton. Mit
// Probing scheitert stattdessen die Probe - kein einziger Wechsel auf h, der Platz bleibt auf m; die Proben kommen im
// Abstand 10, 20, 40 ... 120 s.
func TestProbingEngeLeitung(t *testing.T) {
	for _, link := range []float64{1000e3, 1250e3} {
		t.Run(fmt.Sprintf("%.0f", link/1000), func(t *testing.T) {
			var res [2]probeRun
			for i, on := range []bool{false, true} {
				er := newEstCfg(t, 1250e3, 810e3, 1, on)
				er.link = link
				er.until("m", 2*time.Second)
				res[i] = er.measure(10*time.Minute, nil)
				t.Logf("probing %v: %v, verlauf %v", on, res[i], res[i].seen)
				if on && er.layer() != "m" {
					t.Fatalf("am Ende auf %q", er.layer())
				}
			}
			off, on := res[0], res[1]
			if off.fails < 5 || off.started != 0 || off.padMB != 0 {
				t.Fatalf("ohne Probing: %v (erwartet 7 bis 8 Fehlversuche, keine Proben)", off)
			}
			if on.fails > 1 || on.ups > 1 {
				t.Fatalf("mit Probing: %v (hoechstens ein Fehlversuch)", on)
			}
			if on.started < 6 || on.started > 9 || on.ok != 0 || on.failed != on.started {
				t.Fatalf("proben: %v (erwartet 6 bis 9 in 10 min, alle gescheitert)", on)
			}
			if on.padMB > 1 {
				t.Fatalf("fuelldaten %.2f MB in 10 min", on.padMB)
			}
			if on.seen["m"] < 10*time.Minute-time.Second {
				t.Fatalf("mit Probing nicht durchgehend auf m: %v", on.seen)
			}
		})
	}
}

// TestProbingLeitungWirdFrei (b): erst eng (1,0 Mbit/s), dann frei. Ohne Probing wartet h auf den Aufschub, der in der
// engen Phase gewachsen ist (bis 5 min); mit Probing kommt h mit der naechsten Probe (Abstand hoechstens probeGapMax).
func TestProbingLeitungWirdFrei(t *testing.T) {
	for _, tightFor := range []time.Duration{3 * time.Minute, 10*time.Minute + 30*time.Second} {
		t.Run(tightFor.String(), func(t *testing.T) { probingWirdFrei(t, tightFor) })
	}
}

func probingWirdFrei(t *testing.T, tightFor time.Duration) {
	var took [2]time.Duration
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		er.link = 1000e3
		er.until("m", 2*time.Second)
		tight := er.measure(tightFor, nil)
		er.link = 0
		took[i] = er.until("h", upDwellMax+time.Minute)
		after := er.measure(time.Minute, nil)
		if er.layer() != "h" || after.seen["h"] < time.Minute-time.Second {
			t.Fatalf("probing %v: h haelt nicht auf freier Leitung: %v", on, after.seen)
		}
		t.Logf("probing %v: enge Phase %v; h %v nach dem Freiwerden; danach %v", on, tight, took[i], after)
		if on && (tight.fails != 0 || after.fails != 0) {
			t.Fatalf("fehlversuche mit Probing: eng %d, frei %d", tight.fails, after.fails)
		}
	}
	if took[1] > probeGapMax+5*time.Second {
		t.Fatalf("mit Probing erst nach %v auf h (Proben-Abstand hoechstens %v)", took[1], probeGapMax)
	}
}

// TestProbingStoerschuebe (c): freie Leitung mit Stau-Schueben von 2 s alle 10 bis 30 s (das GCC-Ziel faellt auf
// 600 kbit/s). Der Anteil der Zeit auf h darf mit Probing nicht nennenswert sinken, und eine Probe im Schub scheitert,
// statt einen Wechsel auszuloesen.
func TestProbingStoerschuebe(t *testing.T) {
	var share [2]float64
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		er.until("h", 15*time.Second)
		next, gaps, k := 10*time.Second, []time.Duration{10 * time.Second, 30 * time.Second, 17 * time.Second, 23 * time.Second}, 0
		res := er.measure(10*time.Minute, func(el time.Duration) {
			if el >= next+2*time.Second {
				k++
				next = el + gaps[k%len(gaps)]
			}
			er.loss = el >= next
		})
		share[i] = float64(res.seen["h"]) / float64(10*time.Minute)
		t.Logf("probing %v: %.0f %% auf h; %v", on, 100*share[i], res)
	}
	if share[1] < share[0]-0.05 {
		t.Fatalf("anteil auf h mit Probing %.0f %%, ohne %.0f %%", 100*share[1], 100*share[0])
	}
}

// TestProbingGruppe (d): Empfaenger mit 3 bzw. 4 Sendern auf einer Leitung, die nicht alles traegt. Mit Probing
// weniger Schritte nach oben und weniger Zeit ueber der Leitung; am Ende laeuft, was die Leitung traegt.
func TestProbingGruppe(t *testing.T) {
	for _, c := range []struct {
		n    int
		link float64
	}{{3, 1700e3}, {4, 1700e3}, {3, 2500e3}, {4, 2500e3}} {
		t.Run(fmt.Sprintf("%d-sender-%.0f", c.n, c.link/1000), func(t *testing.T) {
			var res [2]probeRun
			for i, on := range []bool{false, true} {
				er := newEstCfg(t, 1250e3, 810e3, c.n, on)
				er.link = c.link
				er.tick()
				res[i] = er.measure(10*time.Minute, nil)
				t.Logf("probing %v: %v, am Ende %q, verlauf %v", on, res[i], er.video(), res[i].seen)
			}
			off, on := res[0], res[1]
			if on.fails > off.fails || on.overMedia > off.overMedia {
				t.Fatalf("mit Probing schlechter: %v gegen %v", on, off)
			}
			if on.over > 30*time.Second {
				t.Fatalf("mit Probing %v ueber der Leitung", on.over)
			}
		})
	}
}

// TestProbingFreieLeitung: freie Leitung, das GCC-Ziel steht wie im Labor bei 1,5 x der Rate. Die erste Probe besteht,
// h kommt ohne Fehlversuch und bleibt; die Probe kostet rund 0,1 bis 0,15 MB.
func TestProbingFreieLeitung(t *testing.T) {
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.until("m", 2*time.Second)
	d := er.until("h", 15*time.Second)
	res := er.measure(time.Minute, nil)
	if er.layer() != "h" || res.fails != 0 || res.ups != 0 {
		t.Fatalf("h haelt nicht: %v", res)
	}
	var ev []string
	for _, e := range er.pevs {
		ev = append(ev, fmt.Sprintf("%s/%s/%d%s", e.Class, e.Probe, e.ProbeKbit, e.ProbeWhy))
	}
	if len(er.pevs) != 2 || er.pevs[0].Probe != probeStart || er.pevs[1].Probe != probeOK || er.pevs[0].ProbeKbit != 1477 {
		t.Fatalf("probe-ereignisse %v", ev)
	}
	if er.padMB < 0.08 || er.padMB > 0.16 {
		t.Fatalf("fuelldaten %.3f MB (erwartet 0,1 bis 0,15)", er.padMB)
	}
	if ps := er.l.ProbeStats(); ps.Started != 1 || ps.Passed != 1 || ps.PadBytes == 0 {
		t.Fatalf("zaehler %+v", ps)
	}
	t.Logf("h nach %v, %v, fuell %.3f MB", d, ev, er.padMB)
}

// TestProbingWechselndesScheitern: Besteht die Probe jedes Mal, waehrend der echte Wechsel abwechselnd sofort und nach
// laengerem Halten scheitert (wie TestSchaetzerWechselndesScheitern), kommen die Versuche nicht haeufiger als ohne
// Probing - eine bestandene Probe setzt den Aufschub nicht ausser Kraft.
func TestProbingWechselndesScheitern(t *testing.T) {
	var ups [2]int
	for i, on := range []bool{false, true} {
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		er.until("h", 15*time.Second)
		late, upAt := true, er.now
		res := er.measure(10*time.Minute, func(time.Duration) {
			wait := 400 * time.Millisecond
			if late {
				wait = 12 * time.Second
			}
			if er.layer() == "h" {
				if er.loss = er.now-upAt >= int64(wait); er.loss {
					late = !late
				}
			} else {
				er.loss, upAt = false, er.now+int64(200*time.Millisecond)
			}
		})
		ups[i] = res.ups
		t.Logf("probing %v: %v", on, res)
	}
	if ups[1] > ups[0]+1 {
		t.Fatalf("mit Probing %d h-Versuche in 10 min, ohne %d", ups[1], ups[0])
	}
}
