package bwe

import (
	"math/rand"
	"testing"
)

// leitung: kuenstliche Leitung fuer den Detektor - Sender mit fester Paketrate, Engpass mit Schlange (hoechstens
// bufMs, danach Verlust), feste Laufzeit, Jitter, Zufallsverlust; der Empfaenger meldet alle fbMs zurueck (in
// Reihenfolge der Folgenummern bis zum hoechsten angekommenen Paket; was noch unterwegs ist, haelt die Meldung auf).
type leitung struct {
	rng     *rand.Rand
	d       *Detector
	pps     int     // Pakete/s
	size    int     // Byte
	capBps  float64 // 0 = frei
	bufMs   float64
	baseMs  float64
	jitMs   float64 // +- gleichverteilt
	loss    float64
	lossAt  func(t float64) float64        // falls gesetzt: Verlustanteil zur Sendezeit t (statt loss)
	police  func(t float64, size int) bool // falls gesetzt: false = verworfen (Token-Bucket, policer_test.go)
	pauseEv float64                        // mittlerer Abstand von Pausen der Leitung (s), 0 = keine
	pauseMs [2]float64
	fbMs    float64
	fbLoss  float64 // Anteil verlorener Rueckmeldungen

	now      float64 // s
	linkFree float64 // Engpass frei ab
	pauseTo  float64
	nextPau  float64
	pend     []simPkt
	nextFb   float64
	chg      []Change
	onChange func(Change)
}

type simPkt struct {
	sent, arr float64 // s; arr < 0 = verloren
	size      int
	kind      Kind
}

func newLeitung(seed int64, pps, size int) *leitung {
	return &leitung{rng: rand.New(rand.NewSource(seed)), d: NewDetector(Defaults()), pps: pps, size: size, bufMs: 300, baseMs: 20, fbMs: 50}
}

// run laeuft sec Sekunden weiter.
func (l *leitung) run(sec float64) {
	end := l.now + sec
	gap := 1 / float64(l.pps)
	for l.now < end {
		l.send(KindVideo, l.size)
		l.now += gap
	}
}

func (l *leitung) send(kind Kind, size int) {
	t := l.now
	if c := l.d.OnSent(int64(t * 1e9)); c != nil {
		l.note(*c)
	}
	p := simPkt{sent: t, size: size, kind: kind, arr: -1}
	if l.pauseEv > 0 {
		if l.nextPau == 0 {
			l.nextPau = t + l.rng.ExpFloat64()*l.pauseEv
		}
		if t >= l.nextPau {
			l.pauseTo = t + (l.pauseMs[0]+l.rng.Float64()*(l.pauseMs[1]-l.pauseMs[0]))/1000
			l.nextPau = l.pauseTo + l.rng.ExpFloat64()*l.pauseEv
		}
	}
	at := t
	loss := l.loss
	if l.lossAt != nil {
		loss = l.lossAt(t)
	}
	ok := l.rng.Float64() >= loss
	if l.police != nil && !l.police(t, size) {
		ok = false
	}
	if l.capBps > 0 {
		if l.linkFree < t {
			l.linkFree = t
		}
		if (l.linkFree-t)*1000 > l.bufMs {
			ok = false
		} else {
			l.linkFree += float64(size*8) / l.capBps
			at = l.linkFree
		}
	}
	if at < l.pauseTo {
		at = l.pauseTo
	}
	if ok {
		p.arr = at + l.baseMs/1000 + (l.rng.Float64()*2-1)*l.jitMs/1000
	}
	l.pend = append(l.pend, p)
	if t >= l.nextFb {
		l.nextFb = t + l.fbMs/1000
		l.feedback()
	}
}

func (l *leitung) feedback() {
	hi := -1
	for i, p := range l.pend {
		if p.arr >= 0 && p.arr <= l.now {
			hi = i
		}
	}
	var acks []Ack
	n := 0
	for i := 0; i <= hi; i++ {
		p := l.pend[i]
		if p.arr > l.now {
			break // noch unterwegs: der Empfaenger meldet es spaeter
		}
		a := Ack{SentNs: int64(p.sent * 1e9), Size: p.size, Kind: p.kind, Lost: p.arr < 0}
		if !a.Lost {
			a.ArrivalUs = int64(p.arr*1e6) + 123_456_789_000 // Uhr des Empfaengers mit Versatz
		}
		acks = append(acks, a)
		n++
	}
	l.pend = l.pend[n:]
	if n == 0 || l.rng.Float64() < l.fbLoss {
		return
	}
	for _, c := range l.d.Feedback(int64(l.now*1e9), acks, nil) {
		l.note(c)
	}
}

func (l *leitung) note(c Change) {
	l.chg = append(l.chg, c)
	if l.onChange != nil {
		l.onChange(c)
	}
}

func (l *leitung) count(to State) int {
	n := 0
	for _, c := range l.chg {
		if c.To == to {
			n++
		}
	}
	return n
}

// firstAfter liefert den Zeitpunkt (s) des ersten Wechsels nach to ab t0, sonst -1.
func (l *leitung) firstAfter(to State, t0 float64) float64 {
	for _, c := range l.chg {
		if c.To == to && float64(c.AtNs)/1e9 >= t0 {
			return float64(c.AtNs) / 1e9
		}
	}
	return -1
}

func (l *leitung) state() State { return l.d.Snapshot(int64(l.now * 1e9)).State }

// Freie Leitung mit Jitter +- 20 ms: durchgehend frei - bei hoher Paketrate (Schicht h) wie bei niedriger (l + Ton).
func TestDetektorFreiMitJitter(t *testing.T) {
	for _, pps := range []int{30, 75, 160, 400} {
		for seed := int64(1); seed <= 5; seed++ {
			l := newLeitung(seed, pps, 1000)
			l.jitMs = 20
			l.run(120)
			if l.state() != Free || l.count(Warn)+l.count(Congested)+l.count(Unknown) != 0 {
				t.Fatalf("pps=%d seed=%d: zustand %v wechsel %+v", pps, seed, l.state(), l.chg)
			}
			if s := l.d.Snapshot(int64(l.now * 1e9)); s.QueueUs > 15_000 {
				t.Fatalf("pps=%d: schlange %d us", pps, s.QueueUs)
			}
		}
	}
}

// Jitter 0-6 ms und alle ~1,5 s eine Pause der Leitung von 30-90 ms (der nachgestellte Fall "s60", in dem das
// GCC-Ziel faellt): kein Stau; Warnungen hoechstens vereinzelt.
func TestDetektorPausen(t *testing.T) {
	warn, cong := 0, 0
	for seed := int64(1); seed <= 10; seed++ {
		l := newLeitung(seed, 160, 1000)
		l.jitMs, l.pauseEv, l.pauseMs = 3, 1.5, [2]float64{30, 90}
		l.run(120)
		warn += l.count(Warn)
		cong += l.count(Congested)
	}
	t.Logf("20 min mit Pausen 30-90 ms: Warnungen %d, Stau %d", warn, cong)
	if cong != 0 || warn > 2 {
		t.Fatalf("warnungen %d stau %d", warn, cong)
	}
}

// Wachsende Schlange: Die Leitung traegt ab t = 10 s weniger, als gesendet wird -> Warnung, dann Stau. Gemessen wird
// ab dem Beginn der Enge in Sendezeit des letzten gewerteten Pakets (das ist, was die Fensterregel bestimmt: 4 Fenster
// ueber der Schwelle) und in Uhrzeit des Knotens (dazu kommt, dass die Meldung ueber ein Paket in der Schlange selbst
// um die Schlange spaeter kommt).
func TestDetektorWachsendeSchlange(t *testing.T) {
	for _, c := range []struct {
		name     string
		pps      int
		capBps   float64 // Senderate ist pps x 1000 Byte
		maxSent  float64 // Stau spaetestens so lange nach Beginn der Enge (Sendezeit)
		maxClock float64 // ... Uhrzeit
	}{
		{"h auf 1000k", 190, 1_000_000, 0.60, 1.00}, // 1520 kbit/s auf 1000: Schlange waechst 0,52 s/s
		{"h auf 1250k", 190, 1_250_000, 0.75, 1.05}, // 0,22 s/s
		{"doppelt", 250, 1_000_000, 0.60, 1.00},
	} {
		l := newLeitung(3, c.pps, 1000)
		l.jitMs = 5
		l.run(10)
		if l.state() != Free {
			t.Fatalf("%s: vorher %v", c.name, l.state())
		}
		l.capBps = c.capBps
		var warnSent, congSent float64
		l.onChange = func(ch Change) {
			last := float64(l.d.at(l.d.histN-1).endNs) / 1e9
			switch ch.To {
			case Warn:
				warnSent = last - 10
			case Congested:
				congSent = last - 10
			}
		}
		l.run(5)
		tw, tc := l.firstAfter(Warn, 10)-10, l.firstAfter(Congested, 10)-10
		t.Logf("%s: Warnung nach %.2f s (Sendezeit %.2f), Stau nach %.2f s (Sendezeit %.2f), Grund %s, Kapazitaet %d kbit/s", c.name, tw, warnSent, tc, congSent, l.chg[len(l.chg)-1].Why, l.d.capBps/1000)
		if tw < 0 || tc < 0 || tw >= tc {
			t.Fatalf("%s: warnung %.2f stau %.2f", c.name, tw, tc)
		}
		if congSent > c.maxSent || tc > c.maxClock {
			t.Fatalf("%s: stau erst nach %.2f s Sendezeit / %.2f s Uhrzeit", c.name, congSent, tc)
		}
		if l.state() != Congested || l.d.Snapshot(0).Why != WhyQueue {
			t.Fatalf("%s: am ende %v", c.name, l.state())
		}
		// Kapazitaet beim Eintritt: Durchsatz der Stau-Fenster = was die Leitung traegt
		if k := float64(l.d.capBps); k < c.capBps*0.9 || k > c.capBps*1.1 {
			t.Fatalf("%s: kapazitaet %.0f bei leitung %.0f", c.name, k, c.capBps)
		}
		// Leitung wieder frei: Schlange laeuft ab, nach 5 leeren Fenstern frei
		l.capBps = 0
		l.linkFree = 0
		l.run(3)
		if tf := l.firstAfter(Free, 15) - 15; tf < 0 || tf > 1.2 || l.state() != Free {
			t.Fatalf("%s: frei nach %.2f s, zustand %v", c.name, tf, l.state())
		}
	}
}

// Verlust: 1 % (LTE) bleibt frei; ueber 8 % wird Stau - auch ohne jede Schlange (Policer mit kurzem Puffer).
func TestDetektorVerlust(t *testing.T) {
	for seed := int64(1); seed <= 5; seed++ {
		l := newLeitung(seed, 160, 1000)
		l.jitMs, l.loss = 20, 0.01
		l.run(300)
		if l.state() != Free || l.count(Warn)+l.count(Congested) != 0 {
			t.Fatalf("1 %% verlust, seed %d: %v, warnungen %d stau %d", seed, l.state(), l.count(Warn), l.count(Congested))
		}
	}
	// Schwerer anhaltender Verlust: Stau, ohne Flattern. (Bis Stufe 0 schon ab 12 %; seit der Verlust ueber die letzten
	// 160 Pakete beurteilt wird und "hoch" bei 12 % beginnt, liegt Dauerverlust von 12 % auf der Schwelle.)
	for _, loss := range []float64{0.2, 0.35} {
		worst := 0.0
		for seed := int64(1); seed <= 10; seed++ {
			l := newLeitung(seed, 160, 1000)
			l.jitMs = 5
			l.run(5)
			l.loss = loss
			l.run(20)
			tc := l.firstAfter(Congested, 5) - 5
			if tc < 0 || l.state() != Congested || l.d.Snapshot(0).Why != WhyLoss {
				t.Fatalf("verlust %.0f %%, seed %d: stau nach %.2f, zustand %v", loss*100, seed, tc, l.state())
			}
			worst = max(worst, tc)
			if l.count(Free) != 1 { // nur der Beginn; kein Flattern zurueck
				t.Fatalf("verlust %.0f %%: flattert (%d x frei)", loss*100, l.count(Free))
			}
		}
		t.Logf("Verlust %.0f %%: Stau spaetestens nach %.2f s (10 Laeufe)", loss*100, worst)
		if lim := map[float64]float64{0.2: 3, 0.35: 2}[loss]; worst > lim {
			t.Fatalf("verlust %.0f %%: %.2f s", loss*100, worst)
		}
	}
	// Policer: 1520 kbit/s auf 1000 kbit/s mit 20 ms Puffer - die Schlange bleibt unter der Schwelle, der Verlust zeigt es
	l := newLeitung(1, 190, 1000)
	l.run(5)
	l.capBps, l.bufMs = 1_000_000, 20
	l.run(5)
	tc := l.firstAfter(Congested, 5) - 5
	s := l.d.Snapshot(int64(l.now * 1e9))
	t.Logf("Policer 20 ms: Stau nach %.2f s, Grund %s, Schlange %d ms, Verlust %.0f %%, Durchsatz %d kbit/s", tc, s.Why, s.QueueUs/1000, s.Loss*100, s.AckedBps/1000)
	if tc < 0 || tc > 1.8 || s.Why != WhyLoss || s.QueueUs > 50_000 || s.AckedBps < 900_000 || s.AckedBps > 1_100_000 {
		t.Fatalf("policer: %.2f %+v", tc, s)
	}
}

// Basis-Wechsel: kuerzerer Weg -> Basis folgt sofort, bleibt frei. Laengerer Weg (+80 ms) sieht aus wie eine stehende
// Schlange: ohne Reset meldet der Detektor Stau, bis die alte Basis nach 30 s herausgealtert ist; mit Reset
// (Wegwechsel bekannt) sofort wieder frei.
func TestDetektorBasisWechsel(t *testing.T) {
	l := newLeitung(1, 160, 1000)
	l.jitMs, l.baseMs = 5, 100
	l.run(10)
	l.baseMs = 40
	l.run(10)
	if l.state() != Free || l.count(Warn)+l.count(Congested) != 0 {
		t.Fatalf("kuerzerer weg: %v %+v", l.state(), l.chg)
	}
	l.baseMs = 120
	l.run(5)
	if l.state() != Congested {
		t.Fatalf("laengerer weg ohne reset: %v", l.state())
	}
	l.run(27)
	tf := l.firstAfter(Free, 25) - 20
	t.Logf("laengerer Weg ohne Reset: falscher Stau, frei erst %.1f s nach dem Wechsel", tf)
	if l.state() != Free || tf < 25 || tf > 32 {
		t.Fatalf("basis altert nicht heraus: %v nach %.1f s", l.state(), tf)
	}

	l = newLeitung(1, 160, 1000)
	l.jitMs, l.baseMs = 5, 40
	l.run(10)
	l.baseMs = 120
	if c := l.d.Reset(int64(l.now * 1e9)); c == nil || c.To != Unknown || c.Why != WhyReset {
		t.Fatalf("reset: %+v", c)
	}
	l.run(10)
	if l.state() != Free || l.count(Warn)+l.count(Congested) != 0 {
		t.Fatalf("laengerer weg mit reset: %v %+v", l.state(), l.chg)
	}
	// langsame Drift der Uhren (50 ms in 100 s, in beide Richtungen): bleibt frei
	for _, dir := range []float64{1, -1} {
		l = newLeitung(1, 160, 1000)
		l.jitMs = 5
		for i := 0; i < 1000; i++ {
			l.baseMs = 100 + dir*float64(i)*0.05
			l.run(0.1)
		}
		if l.state() != Free || l.count(Warn)+l.count(Congested) != 0 {
			t.Fatalf("drift %v: %v, warnungen %d", dir, l.state(), l.count(Warn))
		}
	}
}

// Keine Rueckmeldung seit 1 s bei laufendem Senden -> unbekannt; mit der naechsten Rueckmeldung wieder frei. Einzelne
// verlorene Rueckmeldungen aendern nichts.
func TestDetektorStille(t *testing.T) {
	l := newLeitung(1, 160, 1000)
	l.jitMs = 5
	l.fbLoss = 0.2
	l.run(60)
	if l.state() != Free || len(l.chg) != 1 {
		t.Fatalf("20 %% rueckmeldungen verloren: %v %+v", l.state(), l.chg)
	}
	l.fbLoss = 1
	l.run(3)
	tu := l.firstAfter(Unknown, 60) - 60
	if l.state() != Unknown || tu < 0.7 || tu > 1.2 || l.chg[len(l.chg)-1].Why != WhySilent {
		t.Fatalf("stille: %v nach %.2f s", l.state(), tu)
	}
	l.fbLoss = 0
	l.run(1)
	if l.state() != Free || l.chg[len(l.chg)-1].Why != WhyFirst {
		t.Fatalf("nach der stille: %v", l.state())
	}
	// Bein sendet nicht (keine Pakete, keine Rueckmeldung): Zustand bleibt, kein Wechsel
	n := len(l.chg)
	l.now += 30
	if s := l.d.Snapshot(int64(l.now * 1e9)); s.State != Free || s.AgeNs < 29e9 || len(l.chg) != n {
		t.Fatalf("pause: %+v", s)
	}
}

// Duenner Strom (nur Ton mit Pausen, 4 Pakete/s): Fenster mit zu wenigen Paketen werten nicht - kein Fehlurteil.
func TestDetektorDuenn(t *testing.T) {
	l := newLeitung(1, 4, 100)
	l.jitMs, l.loss = 20, 0.1
	l.run(120)
	if l.count(Warn)+l.count(Congested) != 0 || l.d.Thin == 0 {
		t.Fatalf("duenn: %+v thin=%d", l.chg, l.d.Thin)
	}
}

// Urteil ueber eine Probe (Span): Fuellpakete zusaetzlich zum Bild fuer 1,4 s.
func TestDetektorProbe(t *testing.T) {
	probe := func(capBps, bufMs, jit float64) (Verdict, State) {
		l := newLeitung(7, 100, 1000) // 800 kbit/s Bild
		l.jitMs, l.capBps, l.bufMs = jit, capBps, bufMs
		l.run(10)
		t0 := l.now
		// 1,4 s lang zusaetzlich 100 Fuellpakete/s zu 1000 Byte -> 1600 kbit/s
		for l.now < t0+1.4 {
			l.send(KindVideo, 1000)
			l.now += 0.005
			l.send(KindPad, 1000)
			l.now += 0.005
		}
		t1 := l.now
		l.run(0.4) // Nachlauf
		return l.d.Span(int64(t0*1e9), int64(t1*1e9)), l.state()
	}
	v, st := probe(0, 300, 20)
	t.Logf("frei, Jitter 20 ms: %+v", v)
	if v.Result != VerdictFree || st != Free || v.Bps < 1_450_000 || v.Bps > 1_800_000 || v.PadPkts < 120 {
		t.Fatalf("frei: %+v %v", v, st)
	}
	v, _ = probe(1_000_000, 300, 5)
	t.Logf("eng 1000 kbit/s: %+v", v)
	if v.Result != VerdictCong || v.QueueUs < 50_000 || v.Bps < 900_000 || v.Bps > 1_100_000 {
		t.Fatalf("eng: %+v", v)
	}
	v, _ = probe(1_000_000, 20, 5)
	t.Logf("Policer 1000 kbit/s, 20 ms: %+v", v)
	if v.Result != VerdictCong || v.Loss < 0.08 || v.Bps < 900_000 || v.Bps > 1_100_000 {
		t.Fatalf("policer: %+v", v)
	}
	// Leitung knapp ueber dem Fuellziel: traegt
	v, _ = probe(1_700_000, 300, 5)
	t.Logf("1700 kbit/s: %+v", v)
	if v.Result != VerdictFree {
		t.Fatalf("1700: %+v", v)
	}
	// leere Spanne
	if v := NewDetector(Defaults()).Span(0, 1e9); v.Result != VerdictUnclear || v.Windows != 0 {
		t.Fatalf("leer: %+v", v)
	}
}

func BenchmarkDetektorRueckmeldung(b *testing.B) {
	// eine Rueckmeldung mit 16 Paketen (Schicht h, 160 Pakete/s, Rueckmeldung alle 100 ms)
	d := NewDetector(Defaults())
	acks := make([]Ack, 16)
	var ns int64
	var out []Change
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		for j := range acks {
			ns += 6_250_000
			acks[j] = Ack{SentNs: ns, ArrivalUs: ns/1000 + 20_000 + int64(j%7)*900, Size: 1100}
		}
		out = d.Feedback(ns, acks, out[:0])
	}
}

func BenchmarkMitleserSenden(b *testing.B) {
	m := NewMitleser(Defaults())
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		m.Sent(int64(i)*6_250_000, uint16(i), 1100, KindVideo)
	}
}

func BenchmarkMitleserRueckmeldung(b *testing.B) {
	// 16 Pakete senden (alle 4 ms) + eine transport-cc-Rueckmeldung dazu (Entpacken, Zuordnen, Detektor)
	m := NewMitleser(Defaults())
	in := make([]rep, 16)
	for j := range in {
		in[j] = rep{0, StRecv, 20_000 + int64(j)*4000}
	}
	fb := fbOf(0, 0, in)
	var ns int64
	seq := uint16(0)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		fb.BaseSequenceNumber, fb.ReferenceTime = seq, uint32(i)&0xffffff
		for j := 0; j < 16; j++ {
			m.Sent(ns, seq, 1100, KindVideo)
			ns += 4_000_000
			seq++
		}
		m.Feedback(ns, fb)
	}
	if c := m.Counters(); c.Acked != uint64(b.N)*16 || c.ToWarn+c.ToCong != 0 {
		b.Fatalf("%+v", c)
	}
}
