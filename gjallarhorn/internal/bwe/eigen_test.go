package bwe

import (
	"math/rand"
	"testing"
)

// Auflagen an den Detektor fuer HORN_BWE=eigen (Pruefer-Funde 4 bis 8, Labor labbwe1).

// Zufallsverlust 3 % und 5 % ohne jeden Stau, 30 min, bei den Paketraten von l mit Ton, m mit Ton und h mit Ton:
// kein Stau, keine Warnung (bis Stufe 0: 66 bis 171 Warnungen in 30 min).
func TestDetektorZufallsverlust(t *testing.T) {
	for _, loss := range []float64{0.03, 0.05} {
		for _, pps := range []int{75, 110, 250} {
			warn, cong := 0, 0
			for seed := int64(1); seed <= 3; seed++ {
				l := newLeitung(seed, pps, 900)
				l.jitMs, l.loss = 5, loss
				l.run(1800)
				warn += l.count(Warn)
				cong += l.count(Congested)
				if st := l.state(); st != Free {
					t.Fatalf("verlust %.0f %%, %d pps: am Ende %v", loss*100, pps, st)
				}
			}
			t.Logf("Zufallsverlust %.0f %%, %d Pakete/s, 3 x 30 min: Warnungen %d, Stau %d", loss*100, pps, warn, cong)
			if cong != 0 || warn > 1 {
				t.Fatalf("verlust %.0f %%, %d pps: Warnungen %d, Stau %d", loss*100, pps, warn, cong)
			}
		}
	}
}

// Nicht bestaetigte Pakete ohne Verzoegerung (Labor: Client unter Last). Zwei Fassungen:
//   - als Doppel erkannt (KindDup, siehe NODE.md): Der Mitleser reicht sie gar nicht als Verlust weiter - der Detektor
//     bleibt frei, auch wenn dauerhaft jedes zweite Paket ein Doppel ist (Labor 7.2: Verhaeltnis 1,55).
//   - nicht als Doppel erkennbar: Schuebe von 0,3 bis 0,9 s, in denen 45 bis 85 % der Pakete unbestaetigt bleiben,
//     ein- bis dreimal je Minute - hoechstens Warnung, kein Stau.
func TestDetektorUnbestaetigtOhneStau(t *testing.T) {
	// Doppel ueber den Mitleser: 10 min, jedes Bildpaket zweimal (das zweite als Doppel, nie bestaetigt)
	m := NewMitleser(Defaults())
	var chg []Change
	m.SetOnChange(func(c Change) { chg = append(chg, c) })
	rng := rand.New(rand.NewSource(3))
	seq := uint16(100)
	now := int64(0)
	var pend []rep
	base := seq
	for i := 0; i < 600*100; i++ {
		now += 10e6
		m.Sent(now, seq, 1000, KindVideo)
		pend = append(pend, rep{seq, StRecv, now/1000 + 20_000 + int64(rng.Intn(4000))})
		seq++
		m.Sent(now+1e6, seq, 1000, KindDup)
		pend = append(pend, rep{seq, StLost, 0})
		seq++
		if len(pend) >= 20 {
			// die Meldung endet wie beim echten Empfaenger mit einem angekommenen Paket
			m.Feedback(now+30e6, fbOf(base, uint32(pend[0].us/refUnit), pend[:len(pend)-1]))
			base += uint16(len(pend) - 1)
			pend = append(pend[:0], pend[len(pend)-1])
		}
	}
	c := m.Counters()
	s := m.Snapshot(now)
	t.Logf("Doppel: gesendet %d Bild + %d Doppel, bestaetigt %d, verloren %d, Doppel unbestaetigt %d, Zustand %v, Wechsel %d", c.Sent[KindVideo], c.Sent[KindDup], c.Acked, c.Lost, c.DupUnacked, s.State, len(chg))
	if s.State != Free || c.ToWarn+c.ToCong != 0 || c.Lost != 0 || c.DupUnacked < 59000 || s.Loss != 0 {
		t.Fatalf("doppel: %+v %+v", s, c)
	}
	// ... ein Doppel, das doch ankommt (das Original ging verloren), zaehlt wie jedes Paket
	m.Sent(now+2e6, seq, 1000, KindDup)
	m.Feedback(now+40e6, fbOf(seq, uint32((now/1000+25_000)/refUnit), []rep{{seq, StRecv, now/1000 + 25_000}}))
	if c2 := m.Counters(); c2.Acked != c.Acked+1 {
		t.Fatalf("doppel angekommen: %+v", c2)
	}

	// nicht erkennbare Schuebe
	for _, pps := range []int{110, 250} {
		warn, cong := 0, 0
		for seed := int64(1); seed <= 3; seed++ {
			l := newLeitung(seed, pps, 900)
			l.jitMs = 5
			r := rand.New(rand.NewSource(seed))
			var from, to, frac float64
			next := 10 + r.Float64()*20
			l.lossAt = func(ts float64) float64 {
				if ts >= next {
					from, to, frac = ts, ts+0.3+r.Float64()*0.6, 0.45+r.Float64()*0.4
					next = ts + 20 + r.Float64()*40
				}
				if ts >= from && ts < to {
					return frac
				}
				return 0
			}
			l.run(1800)
			warn += l.count(Warn)
			cong += l.count(Congested)
		}
		t.Logf("Schuebe unbestaetigter Pakete (0,3-0,9 s, 45-85 %%), %d Pakete/s, 3 x 30 min: Warnungen %d, Stau %d", pps, warn, cong)
		if cong != 0 {
			t.Fatalf("%d pps: Stau %d", pps, cong)
		}
	}
}

// Schonzeit nach Beinbeginn: In den ersten 3 s zaehlt Verlust nicht (Labor: fruehe "warnung grund=verlust" auf 32 %
// der Beine, gemeldeter Verlust 10 bis 62 %; achtmal frueher Stau).
func TestDetektorSchonzeit(t *testing.T) {
	for seed := int64(1); seed <= 5; seed++ {
		l := newLeitung(seed, 160, 1000)
		l.jitMs = 5
		l.lossAt = func(ts float64) float64 {
			if ts < 2.5 {
				return 0.5
			}
			return 0
		}
		l.run(20)
		if l.count(Warn)+l.count(Congested) != 0 || l.state() != Free {
			t.Fatalf("seed %d: fruehe Meldung: %+v", seed, l.chg)
		}
	}
	// eine Schlange zaehlt auch in der Schonzeit
	l := newLeitung(1, 190, 1000)
	l.capBps = 1_000_000
	l.run(3)
	if l.count(Warn) == 0 {
		t.Fatalf("Schlange in der Schonzeit nicht gemeldet: %+v", l.chg)
	}
	// ... und nach der Schonzeit zaehlt Verlust wieder
	l = newLeitung(1, 160, 1000)
	l.run(5)
	l.loss = 0.35
	l.run(5)
	if l.count(Congested) == 0 {
		t.Fatal("Verlust nach der Schonzeit nicht gemeldet")
	}
}

// Kein Haengenbleiben in "Stau" bei dauerhaftem Verlust um 4 bis 5 % (Labor: ein Bein blieb 3 min auf Stau, weil die
// Rueckkehr Verlust unter 3 % verlangte).
func TestDetektorDauerverlust(t *testing.T) {
	for _, loss := range []float64{0.04, 0.05} {
		for seed := int64(1); seed <= 5; seed++ {
			l := newLeitung(seed, 110, 900)
			l.jitMs, l.loss = 5, loss
			l.run(5)
			l.loss = 0.4 // Policer beim Schritt nach oben: Stau
			l.run(4)
			if l.state() != Congested {
				t.Fatalf("kein Stau bei 40 %% Verlust: %v", l.state())
			}
			l.loss = loss
			t0 := l.now
			l.run(180)
			back := -1.0
			for _, c := range l.chg {
				if c.To == Free && float64(c.AtNs)/1e9 > t0 {
					back = float64(c.AtNs)/1e9 - t0
					break
				}
			}
			if back < 0 || back > 5 || l.state() != Free {
				t.Fatalf("verlust %.0f %%, seed %d: zurueck nach %.1f s, am Ende %v", loss*100, seed, back, l.state())
			}
			if seed == 1 {
				t.Logf("Dauerverlust %.0f %%: nach dem Stau wieder frei nach %.1f s", loss*100, back)
			}
		}
	}
}

// Junge Basis: Nach einem Zuruecksetzen (Wegwechsel) gibt es fuer BaseYoungNs weder "staut nicht" ueber eine Spanne
// noch eine Widerlegung (Tip.Clear) - die neue Basis kann selbst schon eine Schlange enthalten.
func TestDetektorJungeBasis(t *testing.T) {
	l := newLeitung(1, 160, 1000)
	l.jitMs = 3
	l.run(10)
	ns := func(s float64) int64 { return int64(s * 1e9) }
	if v := l.d.Span(ns(l.now-1.5), ns(l.now-0.3)); v.Result != VerdictFree || v.Young {
		t.Fatalf("alte Basis: %+v", v)
	}
	if tp := l.d.Tip(ns(l.now)); !tp.Clear {
		t.Fatalf("alte Basis: %+v", tp)
	}
	l.d.Reset(ns(l.now))
	t0 := l.now
	l.run(1.5)
	if v := l.d.Span(ns(t0+0.3), ns(l.now-0.2)); v.Result != VerdictUnclear || !v.Young {
		t.Fatalf("junge Basis: %+v", v)
	}
	if tp := l.d.Tip(ns(l.now)); tp.Clear {
		t.Fatalf("junge Basis: %+v", tp)
	}
	l.run(3)
	// Spanne beginnt kurz nach dem Zuruecksetzen: weiter unklar; spaetere Spanne: wieder ein Urteil
	if v := l.d.Span(ns(t0+1), ns(t0+2.5)); v.Result != VerdictUnclear {
		t.Fatalf("Spanne kurz nach dem Zuruecksetzen: %+v", v)
	}
	if v := l.d.Span(ns(l.now-1.5), ns(l.now-0.3)); v.Result != VerdictFree {
		t.Fatalf("nach %v s: %+v", l.now-t0, v)
	}
	if tp := l.d.Tip(ns(l.now)); !tp.Clear {
		t.Fatalf("nach %v s: %+v", l.now-t0, tp)
	}
}

// Policer bleibt erkannt: Verlust ist dort das einzige Signal (Schlange unter der Schwelle).
func TestDetektorPolicer(t *testing.T) {
	for _, c := range []struct {
		pps  int
		kbit float64
		lim  float64
	}{{190, 1000, 1.8}, {212, 1400, 2.5}, {212, 1200, 2.0}} {
		worstW, worstC := 0.0, 0.0
		for seed := int64(1); seed <= 5; seed++ {
			l := newLeitung(seed, c.pps, 1000)
			l.jitMs = 3
			l.run(8)
			l.capBps, l.bufMs = c.kbit*1000, 20
			l.run(6)
			tw, tc := l.firstAfter(Warn, 8)-8, l.firstAfter(Congested, 8)-8
			s := l.d.Snapshot(int64(l.now * 1e9))
			if tw < 0 || tc < 0 || s.Why != WhyLoss || s.QueueUs > 50_000 {
				t.Fatalf("policer %v kbit/s, seed %d: warnung %.2f stau %.2f %+v", c.kbit, seed, tw, tc, s)
			}
			worstW, worstC = max(worstW, tw), max(worstC, tc)
			// die Spitze widerlegt hier nichts
			if l.d.Tip(int64(l.now * 1e9)).Clear {
				t.Fatalf("policer: Spitze meldet frei")
			}
		}
		t.Logf("Policer %v kbit/s bei %d kbit/s gesendet: Warnung spaetestens nach %.2f s, Stau nach %.2f s (5 Laeufe)", c.kbit, c.pps*8, worstW, worstC)
		if worstC > c.lim {
			t.Fatalf("policer %v: Stau erst nach %.2f s", c.kbit, worstC)
		}
	}
}

// Spitze (Tip): Auf freier Leitung - auch mit Jitter, Pausen und Zufallsverlust - widerlegt der Detektor einen Stau;
// setzt echte Enge ein, hoert das auf, bevor GCC Ueberlast meldet (0,2 bis 0,3 s nach Beginn).
func TestDetektorSpitze(t *testing.T) {
	share := func(l *leitung, sec float64) float64 {
		yes, n := 0, 0
		for end := l.now + sec; l.now < end; {
			l.run(0.2)
			n++
			if l.d.Tip(int64(l.now * 1e9)).Clear {
				yes++
			}
		}
		return float64(yes) / float64(n)
	}
	for _, c := range []struct {
		name string
		prep func(*leitung)
		min  float64
	}{
		{"frei, Jitter +-5 ms", func(l *leitung) { l.jitMs = 5 }, 0.99},
		{"frei, Pausen 30-90 ms alle ~1,5 s", func(l *leitung) { l.jitMs, l.pauseEv, l.pauseMs = 3, 1.5, [2]float64{30, 90} }, 0.9},
		{"frei, Zufallsverlust 3 %", func(l *leitung) { l.jitMs, l.loss = 3, 0.03 }, 0.9},
		{"frei, Zufallsverlust 5 %", func(l *leitung) { l.jitMs, l.loss = 3, 0.05 }, 0.8},
	} {
		for _, pps := range []int{110, 200} {
			l := newLeitung(1, pps, 1000)
			c.prep(l)
			l.run(5)
			f := share(l, 300)
			t.Logf("%s, %d Pakete/s: widerlegt in %.1f %% der Takte", c.name, pps, f*100)
			if f < c.min {
				t.Fatalf("%s: nur %.1f %%", c.name, f*100)
			}
		}
	}
	// Beginn echter Enge: Wie lange nach dem Beginn widerlegt die Spitze noch (im 20-ms-Raster abgefragt)?
	for _, c := range []struct {
		kbit, buf, lim float64
	}{{1000, 300, 0.2}, {1250, 300, 0.25}, {1400, 300, 0.45}, {1000, 20, 0.25}} {
		worst := 0.0
		for seed := int64(1); seed <= 10; seed++ {
			l := newLeitung(seed, 190, 1000) // 1520 kbit/s
			l.jitMs, l.fbMs = 3, 50+float64(seed%2)*50
			l.run(8 + float64(seed)*0.013)
			l.capBps, l.bufMs = c.kbit*1000, c.buf
			t0 := l.now
			last := 0.0
			for l.now < t0+3 {
				l.run(0.02)
				if l.d.Tip(int64(l.now * 1e9)).Clear {
					last = l.now - t0
				}
			}
			worst = max(worst, last)
		}
		t.Logf("1520 kbit/s auf %v kbit/s (Puffer %v ms): Spitze widerlegt laengstens bis %.2f s nach Beginn (10 Laeufe)", c.kbit, c.buf, worst)
		if worst > c.lim {
			t.Fatalf("%v kbit/s: widerlegt noch nach %.2f s", c.kbit, worst)
		}
	}
}

// Durchsatz-Nachweis (Rates): bestaetigt gegen im selben Zeitraum gesendet.
func TestDetektorRaten(t *testing.T) {
	probe := func(capBps, bufMs float64, prep func(*leitung)) (Rates, Verdict) {
		l := newLeitung(7, 100, 1000) // 800 kbit/s Bild
		l.jitMs, l.capBps, l.bufMs = 3, capBps, bufMs
		if prep != nil {
			prep(l)
		}
		l.run(10)
		t0 := l.now
		for l.now < t0+1.2 { // dazu 100 Fuellpakete/s: 1600 kbit/s
			l.send(KindVideo, 1000)
			l.now += 0.005
			l.send(KindPad, 1000)
			l.now += 0.005
		}
		t1 := l.now
		l.run(0.5)
		return l.d.Rates(int64(t0*1e9), int64(t1*1e9)), l.d.Span(int64(t0*1e9), int64(t1*1e9))
	}
	r, v := probe(0, 300, nil)
	t.Logf("frei: gesendet %d, bestaetigt %d kbit/s ueber %.2f s, %s", r.SentBps/1000, r.AckedBps/1000, float64(r.Ns)/1e9, v.Result)
	if r.SentBps < 1_520_000 || r.SentBps > 1_680_000 || float64(r.AckedBps) < 0.95*float64(r.SentBps) || r.Ns < 800e6 || v.Result != VerdictFree {
		t.Fatalf("frei: %+v %+v", r, v)
	}
	r, v = probe(1_450_000, 300, nil) // traegt die Fuellung knapp nicht
	t.Logf("1450 kbit/s: gesendet %d, bestaetigt %d kbit/s, %s, Schlange %d ms", r.SentBps/1000, r.AckedBps/1000, v.Result, v.QueueUs/1000)
	if float64(r.AckedBps) > 0.95*float64(r.SentBps) || v.Result == VerdictFree {
		t.Fatalf("1450: %+v %+v", r, v)
	}
	r, v = probe(1_000_000, 20, nil) // Policer
	t.Logf("Policer 1000 kbit/s: gesendet %d, bestaetigt %d kbit/s, %s, Verlust %.0f %% (davor %.0f %%)", r.SentBps/1000, r.AckedBps/1000, v.Result, v.Loss*100, v.LossBefore*100)
	if float64(r.AckedBps) > 0.75*float64(r.SentBps) || v.Result != VerdictCong {
		t.Fatalf("policer: %+v %+v", r, v)
	}
	// Zufallsverlust 5 %, der schon vorher da war: kein Stau - und nicht "staut", weil der Verlust nicht gestiegen ist
	r, v = probe(0, 300, func(l *leitung) { l.loss = 0.05 })
	t.Logf("Zufallsverlust 5 %%: gesendet %d, bestaetigt %d kbit/s, %s, Verlust %.1f %% (davor %.1f %%)", r.SentBps/1000, r.AckedBps/1000, v.Result, v.Loss*100, v.LossBefore*100)
	if v.Result == VerdictCong || float64(r.AckedBps) < 0.88*float64(r.SentBps) {
		t.Fatalf("zufallsverlust: %+v %+v", r, v)
	}
}
