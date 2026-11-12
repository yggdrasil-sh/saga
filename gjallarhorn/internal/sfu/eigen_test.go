package sfu

import (
	"fmt"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
)

// estDet: Modell des eigenen Stau-Detektors fuer das Raten-Modell (estRoom) - eine Schlange am Engpass (Rate ueber der
// Leitung fuellt sie, darunter laeuft sie ab; hoechstens buf, danach Verlust) und daraus, was internal/bwe meldet:
//   - Spitze (ExtTip): widerlegt Stau, solange die Schlange der juengsten bestaetigten Pakete unter 10 ms liegt und
//     kein Verlust dazukommt. Die Rueckmeldung laeuft der Leitung nach: Der Detektor kennt die Schlange von vor rund
//     150 ms (ein Viertel des Wegs vom vorigen zum jetzigen Takt zurueck).
//   - Spanne (ExtSpan): staut ab 50 ms Schlange bzw. Verlust, staut nicht unter 20 ms, dazwischen unklar; gesendete und
//     bestaetigte Rate der Takte der vollen Fuellung.
type estDet struct {
	buf   float64 // s
	q     float64 // Schlange am Ende des Takts (s)
	qPrev float64
	hist  []estDetTick
	blind bool    // der Detektor sieht nichts (Rueckmeldung alt): widerlegt nie
	mute  bool    // Verlust am vollen Puffer bleibt unbemerkt (fuer den Test des Durchsatz-Nachweises allein)
	burst int     // so viele Takte lang meldet der Detektor Verlust ohne Schlange (Schluesselbild ueberlaeuft einen kurzen Puffer)
	spike float64 // Schlange an der Spitze nur in diesem Takt (s): durch ein eigenes Buendel (estRoom.bund) bzw. kick
	kick  float64 // ... fuer den naechsten Takt vorgemerkt, ohne eigenes Buendel (Stoerung von aussen)
}

type estDetTick struct {
	at          int64
	q           float64
	sent, acked float64
	lost        bool
}

func (d *estDet) step(er *estRoom, rate float64) {
	const dt = 0.2
	d.qPrev = d.q
	acked, lost := rate, er.loss
	if er.link > 0 {
		d.q += (rate - er.link) / er.link * dt
		if d.q > d.buf {
			d.q, lost = d.buf, !d.mute
		}
		acked = min(rate, er.link)
	} else {
		d.q = 0
	}
	d.q = max(d.q, 0)
	if d.burst > 0 {
		d.burst--
		lost = true
	}
	d.hist = append(d.hist, estDetTick{er.now, d.q, rate, acked, lost})
	if len(d.hist) > 64 {
		d.hist = d.hist[len(d.hist)-64:]
	}
}

// eigen schaltet das Modell des Detektors an das Bein (wie HORN_BWE=eigen).
func (er *estRoom) eigen() *estRoom {
	d := &estDet{buf: 0.3}
	er.det = d
	er.l.ExtTip = func() bwe.Tip {
		q := d.qPrev + 0.25*(d.q-d.qPrev) + d.spike
		lost := len(d.hist) > 0 && d.hist[len(d.hist)-1].lost
		calm := !d.blind && q < 0.010
		return bwe.Tip{Clear: calm && !lost, Calm: calm, Fresh: !d.blind, LossUp: lost, QueueUs: int64(q * 1e6)}
	}
	er.l.ExtSpan = func(from, to, rateFrom time.Duration) (bwe.Verdict, bwe.Rates) {
		v := bwe.Verdict{Result: bwe.VerdictUnclear}
		var r bwe.Rates
		var maxQ, sent, acked float64
		n, rn := 0, 0
		for _, h := range d.hist {
			if h.at <= er.now-int64(from) || h.at > er.now-int64(to) {
				continue
			}
			n++
			maxQ = max(maxQ, h.q)
			if h.lost {
				v.Loss = 0.3
			}
			if h.at > er.now-int64(rateFrom) {
				rn++
				sent += h.sent
				acked += h.acked
			}
		}
		if rn > 0 {
			// gemessen wird ueber ganze Messfenster (100 ms): an den Raendern faellt im Mittel ein halbes weg
			r = bwe.Rates{SentBps: int64(sent / float64(rn)), AckedBps: int64(acked / float64(rn)), Ns: int64(rn)*200e6 - 100e6}
		}
		v.QueueUs = int64(maxQ * 1e6)
		v.Hint = maxQ >= 0.02 || v.Loss > 0
		switch {
		case n == 0 || d.blind:
		case maxQ > 0.05 || v.Loss > 0:
			v.Result = bwe.VerdictCong
		case maxQ < 0.02:
			v.Result = bwe.VerdictFree
		}
		return v, r
	}
	return er
}

// neu: Modell-Lauf mit Probing, wahlweise mit dem Detektor (eigen).
func neuEigen(t *testing.T, eigen bool, h, gcc float64, n int) *estRoom {
	er := newEstCfg(t, h, gcc, n, true)
	if eigen {
		er.eigen()
	}
	return er
}

// Stufe 2: Ein grundloser Zielsturz von GCC (Ruecksprung auf 85 % der Rate bei freier Leitung - der belegte Irrtum:
// im Container 11-mal in 1679 s, der Detektor jedes Mal "frei") nimmt bei gcc die Schicht zurueck; bei eigen bleibt
// sie, weil der Detektor den Stau widerlegt.
func TestEigenGrundloserZielsturz(t *testing.T) {
	falls := map[bool]int{}
	for _, eigen := range []bool{false, true} {
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		er.until("h", 30*time.Second)
		er.run(40 * time.Second)
		var z zahlRun
		n := 0
		z = er.zahlen(10*time.Minute, func(el time.Duration) {
			if el > 0 && el%(30*time.Second) == 0 {
				er.falsch = 3
				n++
			}
		})
		// jeder Schritt nach oben in dieser Zeit folgt auf einen Rueckfall von h
		falls[eigen] = z.ups
		t.Logf("eigen=%v: %d grundlose Zielstuerze in 10 min, Rueckfaelle von h: %d, widerlegt: %d | %v", eigen, n, z.ups, er.l.est.refuted, z)
		if eigen && (z.ups != 0 || z.share < 0.999 || er.l.est.refuted < uint64(n)) {
			t.Fatalf("eigen: %d Rueckfaelle, h %.1f %%, widerlegt %d von %d", z.ups, z.share*100, er.l.est.refuted, n)
		}
	}
	if falls[false] < 10 {
		t.Fatalf("gcc: nur %d Rueckfaelle - das Modell stellt den Irrtum nicht nach", falls[false])
	}
}

// Stufe 2: Echte Enge wird nicht langsamer behandelt als bei gcc. Leitung faellt mitten im Anruf unter die Rate von h -
// Zeit, bis der Platz h verlaesst, bei gcc und eigen. Dazu: Sieht der Detektor nichts (Rueckmeldung alt), gilt das
// Signal von GCC wie bisher.
func TestEigenEchteEnge(t *testing.T) {
	leave := func(eigen, blind bool, link float64) time.Duration {
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		er.until("h", 30*time.Second)
		er.run(40 * time.Second)
		if blind {
			er.det.blind = true
		}
		er.link = link
		start := er.now
		for er.now-start < int64(30*time.Second) {
			er.tick()
			if er.layer() != "h" {
				return time.Duration(er.now - start)
			}
		}
		return -1
	}
	for _, link := range []float64{700e3, 1000e3, 1150e3, 1200e3} {
		g, e, b := leave(false, false, link), leave(true, false, link), leave(true, true, link)
		t.Logf("Leitung faellt auf %.0f kbit/s (h braucht 1290): h verlassen nach gcc %v, eigen %v, eigen ohne Sicht %v", link/1000, g, e, b)
		if g < 0 || e != g || b != g {
			t.Fatalf("%.0f kbit/s: gcc %v, eigen %v, eigen ohne Sicht %v", link/1000, g, e, b)
		}
	}
	// Grenze: Leitung nur 1,5 bzw. 3 % unter der Rate - die Schlange waechst mit 15 bzw. 30 ms je Sekunde. Das Modell
	// laesst GCC schon im zweiten Takt melden; die Widerlegung gilt, bis die Schlange 10 ms erreicht.
	for _, link := range []float64{1270e3, 1250e3} {
		g, e := leave(false, false, link), leave(true, false, link)
		t.Logf("Leitung faellt auf %.0f kbit/s (knapp unter h): h verlassen nach gcc %v, eigen %v", link/1000, g, e)
		if e < 0 || e > g+time.Second {
			t.Fatalf("%.0f kbit/s: gcc %v, eigen %v", link/1000, g, e)
		}
	}
}

// Stufe 1: Wertung der Probe durch den Detektor.
//   - enge Leitung (traegt m, nicht h), 10 min: keine bestandene Probe m -> h, kein Schritt auf h; die Zahl der Proben
//     bleibt begrenzt (unklare werden hoechstens probeUnkMax Mal in Folge wiederholt).
//   - freie Leitung, GCC meldet in der Probe faelschlich Ueberlast (zwei Takte gleich zu Beginn der vollen Fuellung -
//     bei gcc "fail:over" mit Abstand): bei eigen haelt die Fuellung an, der Detektor sieht keinen Stau, aber der
//     Nachweis fehlt - unklar, Wiederholung ohne Abstand, dann bestanden.
//   - freie Leitung ohne Stoerung: bestanden wie bei gcc.
//
// Seit Stufe 3 (Fuellziel Bedarf x 1,05) traegt eine Leitung von 1400 kbit/s die Probe auf h (1250 + Ton, gefuellt auf
// 1352 kbit/s) - sie besteht, und h haelt (TestEigenProbeKnappTraegt). Eng sind hier 1000 und 1250 kbit/s.
func TestEigenProbe(t *testing.T) {
	for _, link := range []float64{1000e3, 1250e3} {
		for _, pion := range []bool{false, true} {
			er := neuEigen(t, true, 1250e3, 810e3, 1)
			er.l.firstUsed = true
			er.pion, er.link = pion, link
			er.until("m", 2*time.Second)
			z := er.zahlen(10*time.Minute, nil)
			ps := er.l.ProbeStats()
			t.Logf("eng %.0f kbit/s (pion-Regler %v): %v, proben %+v", link/1000, pion, z, ps)
			if ps.Passed != 0 || z.share != 0 || z.ups != 0 {
				t.Fatalf("eng %.0f: bestandene Proben %d, Schritte %d, h %.1f %%", link/1000, ps.Passed, z.ups, z.share*100)
			}
			if ps.Started > 40 {
				t.Fatalf("eng %.0f: %d Proben in 10 min", link/1000, ps.Started)
			}
		}
	}
	// faelschlich Ueberlast in zwei Takten der vollen Fuellung
	for _, eigen := range []bool{false, true} {
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.until("m", 2*time.Second)
		start := er.now
		first := probeOver(t, er, 0, 1)
		var second string
		if eigen {
			// Seit Stufe 3 ist das Fenster 0,4 bis 0,6 s: Die zwei Takte voller Fuellung bis zum Halt durch GCC belegen
			// die Zielrate schon (Nachweis ueber 300 ms) - der Detektor sieht keinen Stau: bestanden, ohne Wiederholung.
			// (Vorher mit bis zu 1,5 s Fenster: unklar, Wiederholung, dann bestanden.)
			if first != probeOK || er.l.pf.pd != 0 {
				t.Fatalf("eigen: erste Probe %q, abstand %v", first, er.l.pf.pd)
			}
		} else if first != probeFail+":over" || er.l.pf.pd != probeGap {
			t.Fatalf("gcc: %q, abstand %v", first, er.l.pf.pd)
		}
		er.l.ExtOver = func() bool { return er.over >= 2 }
		d := er.until("h", time.Minute)
		t.Logf("eigen=%v: falsche Ueberlast in der Probe: %q, dann %q; h %v nach Beginn (%.1f s nach der ersten Probe)", eigen, first, second,
			time.Duration(er.now-start).Round(100*time.Millisecond), d.Seconds())
	}
	// dauerhaft falsche Ueberlast, die Fuellung haelt nach einem Takt an (kein Nachweis): nach probeUnkMax unklaren
	// Proben in Folge gescheitert, mit Abstand
	er := neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.until("m", 2*time.Second)
	short(er)
	var got []string
	for i := 0; i < probeUnkMax; i++ {
		got = append(got, probeOver(t, er, 0, 1))
	}
	if got[probeUnkMax-1] != probeFail+":over" || er.l.pf.pd != probeGap || er.l.pstat.Unclear != probeUnkMax-1 {
		t.Fatalf("dauerhaft unklar: %v, abstand %v, proben %+v", got, er.l.pf.pd, er.l.pstat)
	}
	// ohne Stoerung
	er = neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.until("m", 2*time.Second)
	if got := probeOver(t, er); got != probeOK {
		t.Fatalf("freie Leitung: %q", got)
	}
	var ev ProbeEvent
	er.r.cfg.OnProbe = func(e ProbeEvent) { ev = e }
	er.until("h", 10*time.Second)
	// Nachweis: gesendet gegen bestaetigt
	er = neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.r.mu.Lock()
	er.r.cfg.OnProbe = func(e ProbeEvent) { ev = e }
	er.r.mu.Unlock()
	er.until("m", 2*time.Second)
	probeOver(t, er)
	er.tick()
	if ev.Result != probeOK || ev.Det != bwe.VerdictFree || ev.GccSig != "" || float64(ev.SentKbit) < probeSentMin*float64(ev.TargetKbit) || ev.AckedKbit != ev.SentKbit {
		t.Fatalf("ereignis: %+v", ev)
	}
	t.Logf("bestanden: ziel %dk, gesendet %dk, bestaetigt %dk, Urteil %s", ev.TargetKbit, ev.SentKbit, ev.AckedKbit, ev.Det)
	// Leitung traegt die Zielrate knapp nicht (1,4 Mbit/s bei Ziel 1,48): GCC bleibt still, und der Detektor zeigt weder
	// Schlange noch Verlust (im Modell erzwungen) - "staut nicht". Der Nachweis haelt dicht: bestaetigt unter 95 % des
	// Gesendeten.
	er = neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.r.mu.Lock()
	er.r.cfg.OnProbe = func(e ProbeEvent) { ev = e }
	er.r.mu.Unlock()
	er.until("m", 2*time.Second)
	er.det.buf, er.det.mute = 0.015, true // kurzer Puffer: die Schlange zeigt nichts
	er.link = 1270e3                      // seit Stufe 3 (Ziel 1352k): unter dem Bedarf von h (1290k); vorher 1400 bei Ziel 1477k
	er.grow, er.capF = 0, 0
	gcc := er.gcc
	er.l.ExtOver = func() bool { return false }
	for i := 0; i < 100 && er.l.pstat.Started == 0; i++ {
		er.gcc = gcc // GCC merkt nichts
		er.tick()
	}
	for i := 0; i < 20 && er.l.pr.stage != 0; i++ {
		er.gcc = gcc
		er.tick()
	}
	er.tick()
	t.Logf("Leitung 1270 bei Ziel %dk, GCC still, Puffer 15 ms: %s (%s), gesendet %dk, bestaetigt %dk, Urteil %s", ev.TargetKbit, ev.Result, ev.Why, ev.SentKbit, ev.AckedKbit, ev.Det)
	if ev.Result == probeOK || ev.Det != bwe.VerdictFree {
		t.Fatalf("bestanden trotz fehlendem Nachweis (bzw. Urteil nicht staut-nicht): %+v", ev)
	}
}

// short: Der Nachweis des Detektor-Modells reicht nie ueber probeProofMin (die Fuellung lief zu kurz) - fuer Tests, in
// denen GCC die Fuellung anhaelt und das Ergebnis deshalb unklar sein soll (Fenster seit Stufe 3 nur 0,4 bis 0,6 s).
func short(er *estRoom) {
	span := er.l.ExtSpan
	er.l.ExtSpan = func(from, to, rateFrom time.Duration) (bwe.Verdict, bwe.Rates) {
		v, r := span(from, to, rateFrom)
		r.Ns = min(r.Ns, int64(probeProofMin)-1)
		return v, r
	}
}

// TestEigenProbeKnappTraegt (Stufe 3): Leitung 1400 kbit/s, h braucht 1250 + Ton - mit dem Fuellziel Bedarf x 1,05
// (1352 kbit/s) besteht die Probe, h kommt und haelt 10 min ohne Fehlversuch. Vorher (x 1,15, 1477 kbit/s) scheiterte
// jede Probe, und die Leitung blieb auf m, obwohl sie h trug.
func TestEigenProbeKnappTraegt(t *testing.T) {
	for _, pion := range []bool{false, true} {
		er := neuEigen(t, true, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.pion, er.link = pion, 1400e3
		er.until("m", 2*time.Second)
		z := er.zahlen(10*time.Minute, nil)
		ps := er.l.ProbeStats()
		t.Logf("1400 kbit/s (pion-Regler %v): %v, proben %+v", pion, z, ps)
		if ps.Passed == 0 || z.share < 0.95 || z.fails != 0 {
			t.Fatalf("1400 kbit/s: bestandene Proben %d, h %.1f %%, Fehlversuche %d", ps.Passed, z.share*100, z.fails)
		}
	}
}

// Einbruch der Leitung mitten in der Probe: Der Nachlauf bis zum Urteil darf den Rueckzug nicht aufhalten (vgl.
// TestProbingEinbruchInDerProbe: gcc 1,2 s).
func TestEigenEinbruchInDerProbe(t *testing.T) {
	leave := map[bool]time.Duration{}
	for _, eigen := range []bool{false, true} {
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.link = 1150e3
		er.until("m", 2*time.Second)
		er.run(8 * time.Second)
		hit := false
		for i := 0; i < 600 && !hit; i++ {
			er.tick()
			hit = er.l.pr.stage == 2
		}
		if !hit {
			t.Fatal("keine Probe")
		}
		er.link = 300e3
		start := er.now
		tl := time.Duration(-1)
		for er.now-start < int64(20*time.Second) && tl < 0 {
			er.tick()
			if er.layer() != "m" {
				tl = time.Duration(er.now - start)
			}
		}
		leave[eigen] = tl
	}
	t.Logf("Einbruch 1150 -> 300 kbit/s in der Probe: m verlassen nach gcc %v, eigen %v", leave[false], leave[true])
	if leave[true] < 0 || leave[true] > leave[false]+400*time.Millisecond {
		t.Fatalf("eigen %v, gcc %v", leave[true], leave[false])
	}
}

// TestEigenZahlen: Kennzahlen des Modells gcc gegen eigen (nur Ausgabe mit -v), Faelle wie TestProbingZahlen, dazu
// grundlose Zielstuerze auf freier Leitung.
func TestEigenZahlen(t *testing.T) {
	for _, eigen := range []bool{false, true} {
		name := map[bool]string{false: "gcc  ", true: "eigen"}[eigen]
		er := neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		t.Logf("%s | Anrufbeginn frei: %s", name, er.bis("h", 30*time.Second))
		t.Logf("%s | frei, Stau-Schuebe alle 45-90 s: %v", name, er.zahlen(10*time.Minute, schuebe(er, 45*time.Second, 70*time.Second, 55*time.Second, 90*time.Second)))
		er = neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		er.bis("h", 30*time.Second)
		t.Logf("%s | frei, grundloser Zielsturz alle 30 s: %v", name, er.zahlen(10*time.Minute, func(el time.Duration) {
			if el > 0 && el%(30*time.Second) == 0 {
				er.falsch = 3
			}
		}))
		er = neuEigen(t, eigen, 1250e3, 810e3, 1)
		er.pion = true
		er.link = 1000e3
		er.bis("m", 2*time.Second)
		tight := er.zahlen(4*time.Minute, nil)
		er.link = 0
		back := er.bis("h", upDwellMax+time.Minute)
		t.Logf("%s | eng 4 min: %v", name, tight)
		t.Logf("%s | ... dann frei: %s; danach 2 min: %v", name, back, er.zahlen(2*time.Minute, nil))
		for _, link := range []float64{1000e3, 1250e3} {
			for _, pion := range []bool{false, true} {
				er = neuEigen(t, eigen, 1250e3, 810e3, 1)
				er.pion, er.link = pion, link
				er.bis("m", 2*time.Second)
				t.Logf("%s | eng %.0f kbit/s (pion-Regler %v): %v", name, link/1000, pion, er.zahlen(10*time.Minute, nil))
			}
		}
		er = neuEigen(t, eigen, 1250e3, 810e3, 4)
		er.link = 1700e3
		er.tick()
		g := er.measure(10*time.Minute, nil)
		t.Logf("%s | Gruppe 4, 1,7 Mbit/s: %v, am Ende %q, %s", name, g, er.video(), voll(g))
		er = neuEigen(t, eigen, 1250e3, 810e3, 4)
		er.pion, er.link = true, 1200e3
		er.tick()
		ps := er.senders()
		rot := func(el time.Duration) { er.speak(ps[int(el/(20*time.Second))%len(ps)]) }
		g = er.measure(3*time.Minute, rot)
		er.link = 0
		full := "nicht in 6 min"
		for el := time.Duration(0); el < 6*time.Minute; el += 200 * time.Millisecond {
			rot(3*time.Minute + el)
			er.tick()
			if v := er.video(); v == "hmmm" || v == "hmm" {
				full = fmt.Sprintf("nach %.1f s", el.Seconds())
				break
			}
		}
		t.Logf("%s | Gruppe 4, Sprecherwechsel alle 20 s, 3 min 1,2 Mbit/s: %v; dann frei: volle Belegung %s", name, g, full)
	}
}
