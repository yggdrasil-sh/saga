package fwd

import (
	"testing"
	"time"
)

// vp8part: Paket eines Bilds aus mehreren Paketen (start = erstes, marker = letztes), mit Nutzlast.
func vp8part(seq uint16, ts uint32, start, key, marker bool) []byte {
	return append(vp8pkt(seq, ts, start, key, marker), make([]byte, 400)...)
}

// checkPad prueft die Mitschrift eines Ausgangs: Folgenummern lueckenlos und monoton; ein Fuellpaket (P-Bit, nur
// Padding) folgt nur auf ein Bildende bzw. ein weiteres Fuellpaket und traegt Zeitstempel, SSRC und Payload-Typ des
// letzten Bilds. Liefert die Zahl der Fuell- und Medienpakete.
func checkPad(t *testing.T, w *hrec) (pads, media int) {
	t.Helper()
	w.mu.Lock()
	defer w.mu.Unlock()
	for i, h := range w.hdrs {
		pl := w.pls[i]
		if i > 0 && h.SequenceNumber != w.hdrs[i-1].SequenceNumber+1 {
			t.Fatalf("paket %d: folgenummer %d nach %d", i, h.SequenceNumber, w.hdrs[i-1].SequenceNumber)
		}
		if !h.Padding {
			media++
			continue
		}
		pads++
		if len(pl) != PadSize || int(pl[len(pl)-1]) != PadSize || h.Marker || len(h.CSRC) != 0 {
			t.Fatalf("paket %d: kein reines Fuellpaket (%d Byte, marker %v)", i, len(pl), h.Marker)
		}
		if i == 0 {
			t.Fatal("fuellpaket vor dem ersten Bild")
		}
		prev := w.hdrs[i-1]
		if !prev.Padding && !prev.Marker {
			t.Fatalf("paket %d: fuellpaket mitten im Bild (nach folgenummer %d ohne Marker)", i, prev.SequenceNumber)
		}
		if h.Timestamp != prev.Timestamp || h.SSRC != prev.SSRC || h.PayloadType != prev.PayloadType {
			t.Fatalf("paket %d: zeitstempel/ssrc/pt %d/%d/%d, letztes Bild %d/%d/%d", i, h.Timestamp, h.SSRC, h.PayloadType,
				prev.Timestamp, prev.SSRC, prev.PayloadType)
		}
	}
	return pads, media
}

// TestPaddingFolgenummern: Fuellpakete einer Probe (SetPadding) reihen sich lueckenlos in die Folgenummern des
// Ausgangs ein - ueber Bilder aus mehreren Paketen, einen Quellwechsel und eine Pause hinweg -,
// stehen nie mitten in einem Bild und zaehlen in der Bein-Rate, aber nicht beim Ausgang.
func TestPaddingFolgenummern(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassSpeaker, "h", "l")
	o.Bind(w, 7, 96)
	a := leg.NewOut("a", "p", Audio, ClassAudio)
	lo := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	o.Assign(lo)
	leg.SetPadding(a, 1e6) // Ton-Ausgang: abgelehnt
	if leg.Padding() != 0 {
		t.Fatal("fuellen auf einem Ton-Ausgang")
	}
	seq := map[*Source]uint16{lo: 1000, hi: 65500} // hi laeuft ueber 65535
	ts := uint32(0)
	frame := func(s *Source, key bool, parts int, gap time.Duration) {
		ts += 3000
		for i := 0; i < parts; i++ {
			seq[s]++
			s.Inject(vp8part(seq[s], ts, i == 0, key, i == parts-1))
			if i == 0 && gap > 0 { // mitten im Bild warten: hier darf nichts eingefuegt werden
				time.Sleep(gap)
			}
		}
	}
	frame(lo, true, 3, 0)
	waitFor(t, func() bool { return w.count() == 3 })
	leg.SetPadding(o, 2e6)
	for i := 0; i < 10; i++ {
		frame(lo, false, 2, 12*time.Millisecond)
		time.Sleep(15 * time.Millisecond)
	}
	// Quellwechsel am Schluesselbild; davor ein unvollstaendiges Bild der alten Quelle
	o.Assign(hi)
	seq[lo]++
	lo.Inject(vp8part(seq[lo], ts+3000, true, false, false))
	time.Sleep(15 * time.Millisecond)
	frame(hi, true, 3, 10*time.Millisecond)
	for i := 0; i < 10; i++ {
		frame(hi, false, 2, 0)
		time.Sleep(15 * time.Millisecond)
	}
	// Pause: kein Fuellen ohne laufende Quelle
	o.Assign(nil)
	time.Sleep(20 * time.Millisecond)
	n0 := w.count()
	time.Sleep(40 * time.Millisecond)
	if n := w.count(); n != n0 {
		t.Fatalf("%d Pakete in der Pause", n-n0)
	}
	o.Assign(lo)
	frame(lo, true, 2, 0)
	for i := 0; i < 5; i++ {
		frame(lo, false, 1, 0)
		time.Sleep(15 * time.Millisecond)
	}
	// Probe aus: danach nur noch Medien
	leg.SetPadding(nil, 0)
	time.Sleep(20 * time.Millisecond)
	n0 = w.count()
	for i := 0; i < 5; i++ {
		frame(lo, false, 2, 0)
	}
	waitFor(t, func() bool { return w.count() == n0+10 })
	pads, media := checkPad(t, w)
	if pads < 100 {
		t.Fatalf("nur %d Fuellpakete (2 Mbit/s ueber rund 0,6 s)", pads)
	}
	w.mu.Lock()
	for _, h := range w.hdrs[n0:] {
		if h.Padding {
			t.Fatal("fuellpaket nach dem Abschalten")
		}
	}
	w.mu.Unlock()
	st := &leg.Stats
	if got := st.PadPkts.Load(); got != uint64(pads) || st.PadBytes.Load() != uint64(pads)*padWire {
		t.Fatalf("zaehler: %d Pakete / %d Byte, mitgeschrieben %d", got, st.PadBytes.Load(), pads)
	}
	if st.PktsOut.Load() != uint64(pads+media) || st.BytesOut.Load() != o.Bytes.Load()+st.PadBytes.Load() {
		t.Fatalf("bein-rate ohne Fuelldaten: %d Pakete, %d Byte (Ausgang %d + Fuell %d)", st.PktsOut.Load(), st.BytesOut.Load(),
			o.Bytes.Load(), st.PadBytes.Load())
	}
	if o.Pkts.Load() != uint64(media) || lo.Bytes.Load()+hi.Bytes.Load() < o.Bytes.Load() {
		t.Fatalf("ausgang zaehlt Fuellpakete mit: %d Pakete, %d Medien", o.Pkts.Load(), media)
	}
	t.Logf("%d Fuellpakete, %d Medienpakete", pads, media)
}

// TestPaddingRate: Die Fuellrate wird im Mittel eingehalten (Bilder mit 30/s, Fuellen dazwischen im Takt padTick).
func TestPaddingRate(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassSpeaker, "l")
	o.Bind(w, 7, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	o.Assign(s)
	s.Inject(vp8part(1, 0, true, true, true))
	waitFor(t, func() bool { return w.count() == 1 })
	const rate = 900e3
	leg.SetPadding(o, rate)
	start := time.Now()
	for i := 2; time.Since(start) < time.Second; i++ {
		s.Inject(vp8part(uint16(i), uint32(i)*3000, true, false, true))
		time.Sleep(33 * time.Millisecond)
	}
	leg.SetPadding(nil, 0)
	el := time.Since(start).Seconds()
	time.Sleep(20 * time.Millisecond)
	got := float64(leg.Stats.PadBytes.Load()) * 8 / el
	if got < 0.8*rate || got > 1.1*rate {
		t.Fatalf("fuellrate %.0f kbit/s, eingestellt %.0f", got/1000, rate/1000)
	}
	checkPad(t, w)
	t.Logf("fuellrate %.0f kbit/s (eingestellt %.0f)", got/1000, rate/1000)
}

// TestPaddingNoAlloc: Fuellpakete ohne Allokation; der Paketpfad bleibt auch mit laufender Probe frei davon.
func TestPaddingNoAlloc(t *testing.T) {
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})} // ohne Schreiber
	leg.padBuf[PadSize-1] = PadSize
	o := leg.NewOut("v", "p", Video, ClassTile, "l")
	o.Bind(nopW{}, 1, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	o.cur.Store(s)
	s.subscribe(o)
	leg.SetPadding(o, 8e6)
	b := vp8pkt(1, 0, true, false, true)
	now := Now()
	pads := leg.Stats.PadPkts.Load()
	run := func() {
		s.Inject(b)
		e, _ := leg.pop()
		leg.process(e.p, e.o)
		e.p.release()
		now += int64(time.Millisecond) // der ganze Lauf bleibt unter PadMax (Totmann)
		leg.pad(now)
	}
	for i := 0; i < 10; i++ {
		run()
	}
	if n := testing.AllocsPerRun(2000, run); n != 0 {
		t.Fatalf("allokationen je paket: %v", n)
	}
	if n := leg.Stats.PadPkts.Load() - pads; n < 2000 || leg.Padding() == 0 {
		t.Fatalf("nur %d Fuellpakete im Lauf", n)
	}
}

// BenchmarkPad: Kosten eines Fuellpakets im Schreiber.
func BenchmarkPad(b *testing.B) {
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
	leg.padBuf[PadSize-1] = PadSize
	o := leg.NewOut("v", "p", Video, ClassTile, "l")
	o.Bind(nopW{}, 1, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	o.cur.Store(s)
	o.frameEnd = true
	leg.SetPadding(o, 1e9)
	now := Now()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		now += 2136 // genau ein Paket bei 1 Gbit/s
		leg.pad(now)
	}
}

// padBench: Bein ohne Schreiber - Pakete und Fuell-Schuebe von Hand, in fester Reihenfolge.
type padBench struct {
	t   *testing.T
	leg *Leg
	o   *Out
	w   *hrec
	now int64
}

func newPadBench(t *testing.T) *padBench {
	b := &padBench{t: t, leg: &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}, w: &hrec{}, now: Now()}
	b.leg.padBuf[PadSize-1] = PadSize
	b.o = b.leg.NewOut("v", "p", Video, ClassSpeaker, "h", "l")
	b.o.Bind(b.w, 7, 96)
	return b
}

// in: ein Paket der Quelle s durch den Schreiber.
func (b *padBench) in(s *Source, seq uint16, ts uint32, start, key, marker bool) {
	s.Inject(vp8part(seq, ts, start, key, marker))
	for {
		e, ok := b.leg.pop()
		if !ok {
			return
		}
		b.leg.process(e.p, e.o)
		e.p.release()
	}
}

// fill: ein Fuell-Schub von n Paketen (falls der Ausgang zwischen zwei Bildern steht).
func (b *padBench) fill(n int) {
	b.leg.SetPadding(b.o, 1e9)
	b.now += int64(time.Millisecond)
	b.leg.padT, b.leg.padCredit = b.now, float64(n*padWire)
	b.leg.pad(b.now)
	b.leg.SetPadding(nil, 0)
}

// check: jede Folgenummer genau einmal, zusammen ein lueckenloser Bereich; liefert Medien- und Fuellpakete.
func (b *padBench) check() (media, pads int) {
	b.t.Helper()
	seen := map[uint16]bool{}
	var first uint16
	for i, h := range b.w.hdrs {
		if seen[h.SequenceNumber] {
			b.t.Fatalf("folgenummer %d doppelt vergeben (paket %d)", h.SequenceNumber, i)
		}
		seen[h.SequenceNumber] = true
		if i == 0 {
			first = h.SequenceNumber
		}
		if h.Padding {
			pads++
		} else {
			media++
		}
	}
	for i := 0; i < len(b.w.hdrs); i++ {
		if !seen[first+uint16(i)] {
			b.t.Fatalf("luecke bei folgenummer %d (%d Pakete ab %d)", first+uint16(i), len(b.w.hdrs), first)
		}
	}
	return media, pads
}

// TestPaddingSpaetesPaket (Prueferfund): Ein Paket der Quelle, das erst nach Fuellpaketen eintrifft, aber vor deren
// Stelle gehoert (Nachsendung, Umsortierung), geht mit dem Versatz von damals hinaus - frueher landete es auf einer
// schon vergebenen Fuell-Folgenummer, und seine eigene Luecke blieb offen. Geprueft vor und nach einem und mehreren
// Fuell-Schueben, ueber den Ueberlauf der Folgenummern und mit einem Quellwechsel dazwischen.
func TestPaddingSpaetesPaket(t *testing.T) {
	for _, base := range []uint16{100, 65500} { // 65500: Ein- und Ausgang laufen im Test ueber 65535
		b := newPadBench(t)
		lo := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
		hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
		b.o.Assign(lo)
		q := base
		b.in(lo, q, 3000, true, true, true) // Bild 1
		// Bild 2 = q+1, q+2, q+3(M); q+2 fehlt zunaechst
		b.in(lo, q+1, 6000, true, false, false)
		b.fill(5) // mitten im Bild: nichts
		b.in(lo, q+3, 6000, false, false, true)
		b.fill(7)
		b.in(lo, q+2, 6000, false, false, false) // Nachsendung nach einem Schub
		b.fill(4)                                // weiter an derselben Bildgrenze - das verspaetete Paket verschiebt sie nicht
		// Bild 3 = q+4(M), Schub, Bild 4 = q+5, q+6(M) umsortiert, Schub, Bild 5 = q+7 .. q+9(M) mit q+8 spaet
		b.in(lo, q+4, 9000, true, false, true)
		b.fill(3)
		b.in(lo, q+6, 12000, false, false, true)
		b.in(lo, q+5, 12000, true, false, false) // Umsortierung innerhalb des Bilds, vor jedem Schub dieses Bilds
		b.fill(6)
		b.in(lo, q+7, 15000, true, false, false)
		b.in(lo, q+9, 15000, false, false, true)
		b.fill(2)
		b.in(lo, q+10, 18000, true, false, true)
		b.fill(2)
		b.in(lo, q+8, 15000, false, false, false) // Nachsendung nach zwei Schueben
		b.in(lo, q+8, 15000, false, false, false) // ... und noch einmal (Doppel der Quelle): dieselbe Folgenummer
		b.w.mu.Lock()
		n := len(b.w.hdrs)
		if d := b.w.hdrs[n-1].SequenceNumber - b.w.hdrs[n-2].SequenceNumber; d != 0 {
			t.Fatalf("doppelt eingetroffenes Paket mit zwei Folgenummern (Abstand %d)", d)
		}
		b.w.hdrs, b.w.pls = b.w.hdrs[:n-1], b.w.pls[:n-1]
		b.w.mu.Unlock()
		media, pads := b.check()
		if media != 11 || pads != 7+4+3+6+2+2 {
			t.Fatalf("basis %d: %d Medien-, %d Fuellpakete", base, media, pads)
		}
		// Quellwechsel: neue Historie. Ein Nachzuegler der alten Quelle wird nicht mehr weitergeleitet, ein
		// verspaetetes Paket der neuen passt wieder.
		b.o.Assign(hi)
		h := base + 3000
		b.in(hi, h, 90000, true, true, false)
		b.in(hi, h+2, 90000, false, false, true)
		b.fill(5)
		b.in(lo, q+11, 21000, true, false, true) // alte Quelle: verworfen
		b.in(hi, h+1, 90000, false, false, false)
		b.fill(1)
		b.in(hi, h+3, 93000, true, false, true)
		media, pads = b.check()
		if media != 15 || pads != 24+5+1 {
			t.Fatalf("basis %d nach dem Quellwechsel: %d Medien-, %d Fuellpakete", base, media, pads)
		}
		// Nachsende-Puffer (NACK): Das verspaetete Paket traegt die Folgenummer seiner Luecke - der Empfaenger, der
		// genau diese nachfragt, bekommt das richtige Medienpaket. Hier: Zeitstempel je Folgenummer passt zum Bild.
		b.w.mu.Lock()
		byTS := map[uint32][]uint16{}
		for _, hd := range b.w.hdrs {
			if !hd.Padding {
				byTS[hd.Timestamp] = append(byTS[hd.Timestamp], hd.SequenceNumber)
			}
		}
		b.w.mu.Unlock()
		for ts, seqs := range byTS {
			lo16, hi16 := seqs[0], seqs[0]
			for _, s := range seqs {
				if int16(s-lo16) < 0 {
					lo16 = s
				}
				if int16(s-hi16) > 0 {
					hi16 = s
				}
			}
			if int(hi16-lo16)+1 != len(seqs) {
				t.Fatalf("basis %d: Bild mit Zeitstempel %d nicht zusammenhaengend: %v", base, ts, seqs)
			}
		}
		if b.leg.Stats.LateDropped.Load() != 0 {
			t.Fatalf("verworfen: %d", b.leg.Stats.LateDropped.Load())
		}
	}
}

// TestPaddingSpaetAelterAlsHistorie: Ein Nachzuegler, der aelter ist als die Historie (mehr als padHist gefuellte
// Bildgrenzen), wird verworfen statt auf eine vergebene Folgenummer zu geraten - die Luecke bleibt, ein Doppel
// entsteht nicht. Innerhalb der Historie passt auch die aelteste Stelle noch.
func TestPaddingSpaetAelterAlsHistorie(t *testing.T) {
	b := newPadBench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	b.in(s, 10, 0, true, true, true)
	b.in(s, 11, 3000, true, false, false) // 12 fehlt
	b.in(s, 13, 3000, false, false, true)
	seq := uint16(13)
	frames := func(n int) {
		for i := 0; i < n; i++ {
			b.fill(2)
			seq++
			b.in(s, seq, uint32(seq)*3000, true, false, true)
		}
	}
	frames(padHist) // die Stelle hinter Paket 13 ist jetzt die aelteste im Ring
	b.in(s, 12, 3000, false, false, false)
	media, _ := b.check()
	if media != 3+padHist+1 {
		t.Fatalf("nachzuegler an der aeltesten Stelle nicht eingereiht: %d Medienpakete", media)
	}
	// zweite Luecke, diesmal faellt ihre Stelle aus dem Ring
	seq++ // fehlt
	gap := seq
	seq++
	b.in(s, seq, uint32(seq)*3000, true, false, true)
	frames(padHist + 3)
	n0 := b.w.count()
	b.in(s, gap, uint32(gap)*3000, true, false, true)
	if b.w.count() != n0 || b.leg.Stats.LateDropped.Load() != 1 {
		t.Fatalf("nachzuegler jenseits der Historie geschrieben (%d Pakete, verworfen %d)", b.w.count()-n0, b.leg.Stats.LateDropped.Load())
	}
	seen := map[uint16]bool{}
	for _, h := range b.w.hdrs {
		if seen[h.SequenceNumber] {
			t.Fatalf("folgenummer %d doppelt", h.SequenceNumber)
		}
		seen[h.SequenceNumber] = true
	}
}

// TestPaddingTotmann: Schaltet niemand ab (Takt haengt), hoert das Bein nach PadMax von selbst auf zu fuellen - und
// SetPadding weckt den Schreiber, er fuellt also auch, wenn nach dem letzten Bild gerade kein Paket kommt.
func TestPaddingTotmann(t *testing.T) {
	old := PadMax
	PadMax = 150 * time.Millisecond
	defer func() { PadMax = old }()
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassSpeaker, "l")
	o.Bind(w, 7, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	o.Assign(s)
	s.Inject(vp8part(1, 0, true, true, true))
	waitFor(t, func() bool { return w.count() == 1 })
	time.Sleep(20 * time.Millisecond) // der Schreiber schlaeft
	leg.SetPadding(o, 1e6)
	time.Sleep(60 * time.Millisecond)
	if n := leg.Stats.PadPkts.Load(); n < 10 {
		t.Fatalf("nur %d Fuellpakete 60 ms nach SetPadding ohne weiteres Bild (Schreiber nicht geweckt)", n)
	}
	time.Sleep(200 * time.Millisecond)
	if leg.Padding() != 0 {
		t.Fatalf("fuellrate nach dem Totmann noch %d", leg.Padding())
	}
	n := leg.Stats.PadPkts.Load()
	time.Sleep(60 * time.Millisecond)
	if leg.Stats.PadPkts.Load() != n {
		t.Fatal("fuellt nach dem Totmann weiter")
	}
	if got := float64(n) * padWire * 8; got > 1e6*0.25 {
		t.Fatalf("%d Fuellpakete in 150 ms bei 1 Mbit/s", n)
	}
	checkPad(t, w)
}

// TestPaddingHistorieVeraltet: Lange nach einer Probe (mehr als 32768 Pakete ohne Quellwechsel) geht ein Nachzuegler
// weiter mit richtiger Folgenummer hinaus. Ohne padHistAge hielt der 16-Bit-Vergleich mit der stehengebliebenen
// Historie ihn fuer "aelter als die Historie" und verwarf ihn (LateDropped).
func TestPaddingHistorieVeraltet(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassSpeaker, "l")
	o.Bind(w, 7, 96)
	src := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	o.Assign(src)
	seq, ts := uint16(100), uint32(0)
	frame := func(key bool) {
		ts += 3000
		seq++
		src.Inject(vp8part(seq, ts, true, key, true))
	}
	frame(true)
	waitFor(t, func() bool { return w.count() == 1 })
	leg.SetPadding(o, 1e6)
	for i := 0; i < 2*padHist+8; i++ { // mehr Fuell-Stellen, als die Historie fasst
		frame(false)
		time.Sleep(6 * time.Millisecond)
	}
	leg.SetPadding(nil, 0)
	time.Sleep(20 * time.Millisecond)
	if leg.Stats.PadPkts.Load() == 0 {
		t.Fatal("keine Fuellpakete")
	}
	for i := 0; i < 33000; i++ {
		frame(false)
		if i%500 == 499 {
			time.Sleep(2 * time.Millisecond) // Warteschlange nicht ueberlaufen lassen
		}
	}
	waitFor(t, func() bool { return leg.QueueLen() == 0 })
	time.Sleep(20 * time.Millisecond)
	if n := leg.Stats.Overflow.Load(); n != 0 {
		t.Fatalf("%d Pakete uebergelaufen", n)
	}
	n0 := w.count()
	w.mu.Lock()
	prev := w.hdrs[n0-1].SequenceNumber
	w.mu.Unlock()
	src.Inject(vp8part(seq+2, ts+6000, true, false, true)) // seq+1 fehlt zunaechst ...
	waitFor(t, func() bool { return w.count() == n0+1 })
	src.Inject(vp8part(seq+1, ts+3000, true, false, true)) // ... und kommt nach
	time.Sleep(30 * time.Millisecond)
	if n := leg.Stats.LateDropped.Load(); n != 0 {
		t.Fatalf("%d Nachzuegler verworfen", n)
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.hdrs) != n0+2 || w.hdrs[n0].SequenceNumber != prev+2 || w.hdrs[n0+1].SequenceNumber != prev+1 {
		t.Fatalf("nach %d: %d Pakete, erwartet %d und %d", prev, len(w.hdrs)-n0, prev+2, prev+1)
	}
}
