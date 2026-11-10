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
		now += int64(5 * time.Millisecond)
		leg.pad(now)
	}
	for i := 0; i < 10; i++ {
		run()
	}
	if n := testing.AllocsPerRun(2000, run); n != 0 {
		t.Fatalf("allokationen je paket: %v", n)
	}
	if n := leg.Stats.PadPkts.Load() - pads; n < 2000 {
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
