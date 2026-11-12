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
// Probing scheitert stattdessen die Probe - bis auf den einen freien Versuch zum Anrufbeginn (der erste Schritt nach
// oben laeuft ohne Probe wie ohne Probing) kein Wechsel auf h, der Platz bleibt auf m; die Proben kommen im Abstand
// 3, 4,5, 6,75 ... 20 s (Stufe 3; vorher 10, 20, 40 ... 120 s und 6 bis 9 Proben, hoechstens 1 MB).
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
			if on.started < 25 || on.started > 35 || on.ok != 0 || on.failed != on.started {
				t.Fatalf("proben: %v (erwartet 25 bis 35 in 10 min, alle gescheitert)", on)
			}
			if on.padMB > 3 {
				t.Fatalf("fuelldaten %.2f MB in 10 min", on.padMB)
			}
			if on.seen["m"] < 10*time.Minute-5*time.Second {
				t.Fatalf("mit Probing nicht (bis auf den ersten Versuch) durchgehend auf m: %v", on.seen)
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
		if on && (tight.fails > 1 || after.fails != 0) { // eng: hoechstens der freie Versuch zum Anrufbeginn
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
	er := newEstProbe(t, 1250e3, 810e3, 1)
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
	if len(er.pevs) != 2 || er.pevs[0].Probe != probeStart || er.pevs[1].Probe != probeOK || er.pevs[0].ProbeKbit != 1352 {
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

// TestProbingWegwechsel: Aufschub und Proben-Abstand ueberleben einen Wegwechsel udp -> tcp -> udp. Auf einer Leitung,
// die h nicht traegt, kaeme sonst nach jedem Wechsel sofort die naechste Probe bzw. der naechste Versuch.
func TestProbingWegwechsel(t *testing.T) {
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.link = 1000e3
	er.until("m", 2*time.Second)
	er.measure(90*time.Second, nil) // Proben nach 0, 3, 7,5 ... s gescheitert: Abstand probeGapMax
	old := er.l.pf
	if old.pd != probeGapMax || old.next <= er.now {
		t.Fatalf("vor dem Wegwechsel: abstand %v, naechste Probe in %v", old.pd, time.Duration(old.next-er.now))
	}
	swap := func(ref string, path Path) *Leg {
		er.r.RemoveLeg(er.l.Ref)
		l, err := er.r.AddLeg(Grant{Room: "raumschaetzer", Peer: "empf", Leg: ref, Role: RoleBoth}, path, Want{Audio: 1, Speaker: true})
		if err != nil {
			t.Fatal(err)
		}
		if path == PathUDP {
			l.Ext = func() int { return int(er.gcc) }
			l.ExtOver = func() bool { return er.over >= 2 }
		}
		er.l = l
		er.sent = 0
		return l
	}
	swap("empf-tcp", PathTCP)
	er.measure(10*time.Second, nil)
	if d := er.l.pf; d != old {
		t.Fatalf("auf dem TCP-Bein: %+v, erwartet %+v", d, old)
	}
	swap("empf-udp", PathUDP)
	res := er.measure(max(time.Duration(old.next-er.now)-2*time.Second, 0), nil)
	if res.started != 0 || res.ups > 1 { // der Schritt auf m ist kein Versuch auf h
		t.Fatalf("nach dem Wegwechsel vor Ablauf des Abstands: %v, verlauf %v", res, res.seen)
	}
	res = er.measure(10*time.Second, nil)
	if res.started != 1 || res.failed != 1 {
		t.Fatalf("nach Ablauf des Abstands: %v", res)
	}
	if d := er.l.pf; d.pd != probeGapMax {
		t.Fatalf("abstand nach der naechsten gescheiterten Probe %v, erwartet %v", d.pd, probeGapMax)
	}
	// ohne Probing merkt sich der Raum nichts
	off := newEstCfg(t, 1250e3, 810e3, 1, false)
	off.link = 1000e3
	off.until("m", 2*time.Second)
	off.measure(time.Minute, nil)
	if off.l.slots[1].dwellOf("h") == 0 {
		t.Fatal("kein Aufschub ohne Probing")
	}
	off.r.RemoveLeg(off.l.Ref)
	if len(off.r.mem) != 0 {
		t.Fatal("ohne Probing gemerkt")
	}
}

// senders: die Sender des Modells (alle ausser dem Empfaenger), in Beitrittsfolge.
func (er *estRoom) senders() []*Peer {
	var ps []*Peer
	for _, p := range er.r.sortedPeers() {
		if p != er.l.Peer {
			ps = append(ps, p)
		}
	}
	return ps
}

// speak macht p zum Hauptsprecher (der Sprecherplatz des Empfaengers wechselt den Besitzer).
func (er *estRoom) speak(p *Peer) {
	er.r.mu.Lock()
	er.r.dominant = p
	er.r.mu.Unlock()
}

// TestProbingSprecherwechsel (Prueferfund 2): Gruppe mit 4 Sendern auf enger Leitung, der Sprecher wechselt alle 6, 10
// bzw. 20 s. Der Abstand nach gescheiterten Proben haengt am Bein und an der Zielrate, nicht am Platz und seinem
// Besitzer - sonst begann er bei jedem Sprecherwechsel von vorn (115 bis 145 Proben in 10 min und mehr Zeit ueber der
// Leitung als ohne Probing). Jetzt: Zahl der Proben wie im 1:1-Fall, Zeit ueber der Leitung weit unter der ohne
// Probing, und ein Fehlversuch sperrt die Zielrate auch fuer den naechsten Sprecher.
func TestProbingSprecherwechsel(t *testing.T) {
	for _, rot := range []time.Duration{6 * time.Second, 10 * time.Second, 20 * time.Second} {
		for _, link := range []float64{700e3, 1000e3, 1400e3, 1700e3} {
			t.Run(fmt.Sprintf("%v-%.0f", rot, link/1000), func(t *testing.T) {
				var res [2]probeRun
				var evs int
				for i, on := range []bool{false, true} {
					er := newEstCfg(t, 1250e3, 810e3, 4, on)
					er.link = link
					er.tick()
					ps := er.senders()
					res[i] = er.measure(10*time.Minute, func(el time.Duration) { er.speak(ps[int(el/rot)%len(ps)]) })
					evs = len(er.pevs)
					t.Logf("probing %v: %v", on, res[i])
				}
				off, on := res[0], res[1]
				if on.started > 40 {
					t.Fatalf("%d Proben in 10 min (1:1-Fall seit Stufe 3: 31)", on.started)
				}
				if on.over > off.over || on.fails > off.fails {
					t.Fatalf("mit Probing schlechter: %v ueber der Leitung, %d Fehlversuche (ohne: %v, %d)", on.over, on.fails, off.over, off.fails)
				}
				if evs > 2*on.started {
					t.Fatalf("%d Probe-Meldungen fuer %d Proben", evs, on.started)
				}
			})
		}
	}
}

// TestProbingBesitzerwechsel (Pruefer G7): Wechselt der Besitzer des Platzes mitten in der Probe, endet sie sofort als
// abgebrochen (nicht als gescheitert - das fallende GCC-Ziel kommt vom Wechsel), die Fuelldaten sind aus, und die
// naechste Probe kommt fruehestens probeRetry spaeter.
func TestProbingBesitzerwechsel(t *testing.T) {
	er := newEstProbe(t, 2500e3, 810e3, 3)
	er.tick()
	ps := er.senders()
	sw, lastEnd := 0, int64(0)
	for i := 0; i < 3000; i++ {
		// (Gegenstand ist der Abbruch: ohne freie Versuche nach langem Halten, sonst kaeme die Gruppe bald ohne
		// weitere Proben auf volle Belegung)
		for _, s := range er.l.slots {
			s.free = [4]bool{}
		}
		// ... und die Vorgeschichte nach dem ersten Fehlversuch verfaellt nicht: Seit Stufe 3 (Abstand 3 s) waere sie
		// nach gut 30 s erledigt (legFail.decay), und die Schaetzung allein truege die Gruppe ohne Probe hinauf
		if f := &er.l.pf; f.pd > 0 {
			f.next = max(f.next, er.now-int64(upHold))
		}
		if er.l.pr.stage == 2 && er.l.pr.n == 1 {
			if lastEnd != 0 && er.now-lastEnd < int64(probeRetry) {
				t.Fatalf("naechste Probe schon %v nach dem Abbruch", time.Duration(er.now-lastEnd))
			}
			sw++
			er.speak(ps[sw%len(ps)])
			er.tick()
			if er.l.pr.stage != 0 || er.l.fl.Padding() != 0 || er.l.pstat.Last != "abort:platz" {
				t.Fatalf("wechsel %d: stufe %d, fuellrate %d, ausgang %q", sw, er.l.pr.stage, er.l.fl.Padding(), er.l.pstat.Last)
			}
			lastEnd = er.now
			continue
		}
		er.tick()
		if er.l.pr.stage != 0 && er.now-er.l.pr.at > int64(3*time.Second) {
			t.Fatalf("probe haengt in stufe %d", er.l.pr.stage)
		}
	}
	if st := er.l.pstat; sw < 5 || st.Failed != 0 || st.Aborted != uint64(sw) {
		t.Fatalf("%d Wechsel, proben %+v", sw, st)
	}
}

// TestProbingAbstandJeZielrate: die Regeln des Abstands im Einzelnen (legFail).
func TestProbingAbstandJeZielrate(t *testing.T) {
	var f legFail
	now := int64(time.Hour)
	if f.covers(1e6) || f.blocks(1e6, now) {
		t.Fatal("ohne Vorgeschichte gesperrt")
	}
	f.fail(1500e3, now)
	if !f.blocks(1500e3, now) || !f.blocks(3e6, now) || !f.blocks(1400e3, now) || f.blocks(1300e3, now) {
		t.Fatal("gesperrt sind Zielraten ab 90 % der gescheiterten, kleinere nicht")
	}
	if f.blocks(1500e3, now+int64(probeGap)) || !f.covers(1500e3) || f.covers(1e6) {
		t.Fatal("nach dem Abstand darf geprobt werden; ohne bestandene Probe bleibt der Schritt gesperrt")
	}
	for i, want := range []time.Duration{4500 * time.Millisecond, 6750 * time.Millisecond, 10125 * time.Millisecond, 15187500 * time.Microsecond, probeGapMax, probeGapMax} {
		now += int64(f.pd)
		f.fail(1500e3, now)
		if f.pd != want {
			t.Fatalf("abstand nach %d weiteren: %v, erwartet %v", i+1, f.pd, want)
		}
	}
	f.fail(900e3, now) // kleinere Zielrate gescheitert: Schwelle sinkt, Abstand bleibt
	if f.rate != 900e3 || f.pd != probeGapMax || !f.blocks(850e3, now) {
		t.Fatalf("nach kleinerer Zielrate: %+v", f)
	}
	f.pass(700e3) // darunter bestanden: aendert nichts
	if f.pd == 0 {
		t.Fatal("bestandene Probe unter der Schwelle setzt zurueck")
	}
	f.pass(950e3)
	if f != (legFail{}) {
		t.Fatalf("bestandene Probe ab der Schwelle setzt nicht zurueck: %+v", f)
	}
	f.fail(1e6, now)
	f.decay(f.next + int64(f.pd) + int64(upHold) - 1)
	if f.pd == 0 {
		t.Fatal("zu frueh verfallen")
	}
	f.decay(f.next + int64(f.pd) + int64(upHold))
	if f.pd != 0 {
		t.Fatal("nicht verfallen")
	}
	// probeBlindAfter: nach N gescheiterten Proben in Folge ein Versuch ohne Probe (Standard 0 = nie)
	defer func(v int) { probeBlindAfter = v }(probeBlindAfter)
	f = legFail{}
	for i := 0; i < 5; i++ {
		f.fail(1e6, now)
	}
	if !f.covers(1e6) {
		t.Fatal("Standard: nie ohne Probe")
	}
	probeBlindAfter = 3
	if f.covers(1e6) {
		t.Fatal("probeBlindAfter 3: nach 5 gescheiterten Proben noch gesperrt")
	}
}

// TestProbingKnappeLeitung (Prueferfund 4, Verhalten bewusst so): Eine Leitung zwischen Bedarf und Bedarf x probeHead
// traegt h, besteht die Probe (Zielrate mit Reserve) aber nie - mit Probing erreicht sie h nicht; ab Bedarf x 1,06
// besteht die Probe (Stufe 3; vorher Reserve 1,15, die Luecke reichte bis Bedarf x 1,15). Mit probeBlindAfter = 3
// bekaeme sie nach drei gescheiterten Proben einen Versuch ohne Probe.
// (Bein mit Vorgeschichte. Der freie Versuch zum Anrufbeginn bzw. nach langem Halten erreicht h auf so einer Leitung
// einmal ohne Probe - TestProbingAnrufbeginn; die Luecke bleibt fuer alles danach.)
func TestProbingKnappeLeitung(t *testing.T) {
	defer func(v int) { probeBlindAfter = v }(probeBlindAfter)
	for _, c := range []struct {
		f     float64
		blind int
		h     bool
	}{{1.02, 0, false}, {1.04, 0, false}, {1.06, 0, true}, {1.14, 0, true}, {1.02, 3, true}} {
		probeBlindAfter = c.blind
		er := newEstProbe(t, 1250e3, 810e3, 1)
		er.link = 1250e3*c.f + estAudio
		er.until("m", 2*time.Second)
		res := er.measure(10*time.Minute, nil)
		t.Logf("leitung = bedarf x %.2f, probeBlindAfter %d: %v, verlauf %v", c.f, c.blind, res, res.seen)
		if got := res.seen["h"] > 8*time.Minute; got != c.h {
			t.Fatalf("leitung = bedarf x %.2f, probeBlindAfter %d: h %v, erwartet %v", c.f, c.blind, got, c.h)
		}
	}
}

// TestProbingEinbruchInDerProbe (Prueferfund 3): Bricht die Leitung waehrend einer Probe wirklich ein (von 1150 auf
// 300 kbit/s, traegt nur noch l), darf der Schutz der Schaetzung das nicht verdecken. Frueher blieb m die ganze Frist
// (2 s) stehen und wurde erst nach 2,4 s verlassen; ohne Probing sind es 0,8 s. Jetzt endet der Schutz, sobald das
// Stausignal ohne Fuelldaten weiter ansteht - nach 1,2 s, hoechstens 0,8 s spaeter als ohne Probing. Eine Probe, die
// nur an der eigenen Fuellung scheitert (Leitung traegt m weiter), veraendert die Schaetzung weiterhin nicht.
// (Ohne Probing sind es 0,4 oder 0,8 s, je nach Alter des Prozesses: In den ersten 20 s gilt der erste Stau eines
// Beins als "kurz nach dem letzten" - estimator.lastDown beginnt bei 0 -, die Sperrzeit ist dann doppelt so lang.
// Mit der frueheren Schranke von 0,6 s war dieser Test deshalb nur in einem jungen Prozess gruen: mit -count ab etwa
// der 15. Wiederholung rot, auch schon vor den Aenderungen der Pruefung 2.)
func TestProbingEinbruchInDerProbe(t *testing.T) {
	leave := map[string]time.Duration{}
	for _, mode := range []string{"aus", "an-in-probe", "an-ohne-probe"} {
		er := newEstCfg(t, 1250e3, 810e3, 1, mode != "aus")
		er.link = 1150e3
		er.until("m", 2*time.Second)
		er.run(8 * time.Second)
		hit := false
		for i := 0; i < 600 && !hit; i++ {
			er.tick()
			switch mode {
			case "an-in-probe":
				hit = er.l.pr.stage == 2
			case "an-ohne-probe":
				hit = i > 100 && er.l.pr.stage == 0 && er.l.est.prUntil == 0 && er.layer() == "m"
			default:
				hit = i > 100 && er.layer() == "m"
			}
		}
		if !hit {
			t.Fatalf("%s: Ausgangslage nicht erreicht", mode)
		}
		er.link = 300e3
		start, over := er.now, time.Duration(0)
		tl := time.Duration(-1)
		for er.now-start < int64(20*time.Second) {
			er.tick()
			if estAudio+er.sent > er.link {
				over += 200 * time.Millisecond
			}
			if tl < 0 && er.layer() != "m" {
				tl = time.Duration(er.now - start)
			}
		}
		leave[mode] = tl
		t.Logf("%s: verlaesst m nach %v, %v ueber der Leitung, am Ende %q", mode, tl, over, er.layer())
		if tl < 0 || er.layer() != "l" {
			t.Fatalf("%s: m nicht verlassen bzw. am Ende %q", mode, er.layer())
		}
	}
	if d := leave["an-in-probe"] - leave["aus"]; d > 800*time.Millisecond || leave["an-in-probe"] > 1200*time.Millisecond {
		t.Fatalf("Einbruch in der Probe %v spaeter erkannt als ohne Probing (%v gegen %v)", d, leave["an-in-probe"], leave["aus"])
	}
	if leave["an-ohne-probe"] > leave["aus"] {
		t.Fatalf("ohne laufende Probe spaeter als ohne Probing: %v gegen %v", leave["an-ohne-probe"], leave["aus"])
	}
	// Gegenstueck: Die Probe scheitert nur an der eigenen Fuellung - m bleibt, die Schaetzung bleibt
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.link = 1150e3
	er.until("m", 2*time.Second)
	for i := 0; i < 600 && er.l.pstat.Failed == 0; i++ {
		er.tick()
	}
	est := er.l.EstimateKbit()
	for i := 0; i < 25; i++ {
		er.tick()
		if er.layer() != "m" || er.l.EstimateKbit() < est {
			t.Fatalf("takt %d nach gescheiterter Probe: schicht %q, schaetzung %d (vorher %d)", i, er.layer(), er.l.EstimateKbit(), est)
		}
	}
}

// TestProbingGrosserSchritt (Prueferfund 5): Ein Schritt, dessen Fuellrate ueber probeFillMax (1,5 Mbit/s) laege, wird
// nicht geprobt und nicht "halb geprobt" freigegeben - er laeuft wie ohne Probing (der Wechsel ist die Probe, der
// Aufschub das Netz). Hier h mit 3 Mbit/s von m aus: mit und ohne Probing dieselben Schritte, keine Probe.
func TestProbingGrosserSchritt(t *testing.T) {
	for _, link := range []float64{0, 2500e3} {
		var res [2]probeRun
		for i, on := range []bool{false, true} {
			er := newEstCfg(t, 3000e3, 810e3, 1, on)
			er.link = link
			er.until("m", 2*time.Second)
			res[i] = er.measure(10*time.Minute, nil)
			t.Logf("leitung %.0f probing %v: %v, verlauf %v", link/1000, on, res[i], res[i].seen)
		}
		off, on := res[0], res[1]
		if on.started != 0 || on.padMB != 0 {
			t.Fatalf("nicht probbarer Schritt geprobt: %v", on)
		}
		if on.ups != off.ups || on.fails != off.fails || on.seen["h"] != off.seen["h"] {
			t.Fatalf("leitung %.0f: mit Probing %v / %v, ohne %v / %v", link/1000, on, on.seen, off, off.seen)
		}
	}
	// Grenze: Fuellrate knapp unter dem Deckel wird geprobt, die Freigabe entspricht der geprobten Rate
	er := newEstProbe(t, 2000e3, 810e3, 1) // 2000 x 1,05 - 500 = 1600 kbit/s Fuellrate: nicht probbar
	er.until("m", 2*time.Second)
	er.run(20 * time.Second)
	if len(er.pevs) != 0 {
		t.Fatalf("2000: probe-ereignisse %+v", er.pevs)
	}
	er = newEstProbe(t, 1900e3, 810e3, 1) // 1900 x 1,05 - 500 = 1495 kbit/s Fuellrate (Stufe 3; vorher Reserve 1,15)
	er.until("m", 2*time.Second)
	er.until("h", 20*time.Second)
	if len(er.pevs) != 2 || er.pevs[1].Probe != probeOK || er.pevs[1].ProbeKbit < 2030 || er.pevs[1].ProbeKbit > 2040 || er.fill != 0 {
		t.Fatalf("probe-ereignisse %+v", er.pevs)
	}
}

// TestProbingFuelldatenFliessenNicht (Prueferfund 8): Stellt der Takt die Fuellrate ein, aber der Schreiber sendet
// nichts (Quelle still, Ausgang wartet auf ein Schluesselbild), endet die Probe als abgebrochen ("fuell") - bestanden
// ist sie nicht. Der naechste Versuch kommt mit wachsendem Abstand wie nach einer gescheiterten Probe, nicht alle 2 s.
func TestProbingFuelldatenFliessenNicht(t *testing.T) {
	er := newEstProbe(t, 1250e3, 810e3, 1)
	er.noPad = true
	er.until("m", 2*time.Second)
	res := er.measure(5*time.Minute, nil)
	t.Logf("%v, letzte %q", res, er.l.pstat.Last)
	if res.ok != 0 || res.ups != 0 || res.aborted < 14 || res.aborted > 20 || er.l.pstat.Last != "abort:fuell" {
		t.Fatalf("%v, letzte %q (erwartet 14 bis 20 abgebrochene Proben in 5 min - Abstand wie nach gescheiterten, 3 s bis 20 s -, kein Schritt)", res, er.l.pstat.Last)
	}
	er.noPad = false
	if d := er.until("h", probeGapMax+10*time.Second); d > probeGapMax+5*time.Second {
		t.Fatalf("h erst nach %v", d)
	}
}

// TestProbingFrischerRueckfall (Stufe 3, labbwe2: Token-Bucket-Policer knapp unter h): Die Leitung traegt die kurze
// Probe, aber nicht die Schicht - jede Probe besteht, h faellt nach rund 5 s zurueck. Ohne Gegenmittel (probeFresh 0)
// beginnt das alle paar Sekunden von vorn: Der Rueckfall nach mehr als upFail zaehlt nicht als Fehlversuch, und die
// bestandene Probe setzt den Abstand zurueck. Mit probeFresh zaehlt der Rueckfall binnen 15 s als Fehlversuch: Der
// Aufschub waechst (8, 16, 32 ... s, Proben danach hoechstens probeDwellMax), die Zahl der Versuche faellt auf einen
// Bruchteil. Je gcc und eigen.
func TestProbingFrischerRueckfall(t *testing.T) {
	run := func(eigen bool, fresh time.Duration) (ups int, onH time.Duration) {
		old := probeFresh
		probeFresh = fresh
		defer func() { probeFresh = old }()
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.grow = 400e3 // GCC zieht nach dem Schritt binnen 2 s nach: Die Schonfrist besteht, h laeuft erst einmal
		er.until("m", 2*time.Second)
		var hFor time.Duration
		res := er.measure(10*time.Minute, func(el time.Duration) {
			er.link = 0 // die Probe traegt der Eimer
			if er.layer() == "h" {
				if hFor += 200 * time.Millisecond; hFor >= 6*time.Second { // ... h nur 6 s, dann ist er leer
					er.link = 1200e3
				}
			} else {
				hFor = 0
			}
		})
		return res.ups, res.seen["h"]
	}
	for _, eigen := range []bool{false, true} {
		ups0, h0 := run(eigen, 0)
		ups, h := run(eigen, probeFresh)
		t.Logf("eigen=%v: Schritte auf h in 10 min ohne probeFresh %d (%v auf h), mit %d (%v)", eigen, ups0, h0.Round(time.Second), ups, h.Round(time.Second))
		if ups0 < 30 || ups > 12 || ups == 0 {
			t.Fatalf("eigen=%v: Schritte auf h ohne probeFresh %d, mit %d (erwartet: mindestens 30, dann hoechstens 12)", eigen, ups0, ups)
		}
	}
}
