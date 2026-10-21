package fwd

import (
	"encoding/binary"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/rtpx"
)

type rec struct {
	mu    sync.Mutex
	seqs  []uint16
	block chan struct{}
	n     int
}

func (r *rec) WriteRTP(h *rtp.Header, p []byte) (int, error) {
	if r.block != nil {
		<-r.block
	}
	r.mu.Lock()
	r.seqs = append(r.seqs, h.SequenceNumber)
	r.n++
	r.mu.Unlock()
	return len(p), nil
}

func (r *rec) count() int { r.mu.Lock(); defer r.mu.Unlock(); return r.n }

// vp8pkt baut ein VP8-RTP-Paket; start=Rahmenanfang, key=Schluesselbild.
func vp8pkt(seq uint16, ts uint32, start, key, marker bool) []byte {
	b := make([]byte, 12, 64)
	b[0] = 0x80
	b[1] = 96
	if marker {
		b[1] |= 0x80
	}
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	binary.BigEndian.PutUint32(b[8:], 1)
	d := byte(0)
	if start {
		d = 0x10
	}
	pl := byte(1)
	if key {
		pl = 0
	}
	return append(b, d, pl, 9, 9, 9, 9)
}

func waitFor(t *testing.T, f func() bool) {
	t.Helper()
	for i := 0; i < 200; i++ {
		if f() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatal("timeout")
}

func TestForwardAndSwitch(t *testing.T) {
	room := NewRoom(time.Hour)
	defer room.Close()
	leg := NewLeg()
	defer leg.Close()
	w := &rec{}
	o := leg.NewOut("v0", "p1", Video, ClassSpeaker, "h", "l")
	o.Bind(w, 7, 96)
	room.AddOut(o)
	lo := &Source{Owner: "p1", Layer: "l", Kind: Video}
	room.AddSource(lo)
	lo.Inject(vp8pkt(100, 1000, true, false, true)) // kein Schluesselbild: wird nicht gesendet
	lo.Inject(vp8pkt(101, 4000, true, true, true))  // Schluesselbild: Start
	lo.Inject(vp8pkt(102, 7000, true, false, true))
	waitFor(t, func() bool { return w.count() == 2 })
	if o.Current() != lo {
		t.Fatal("aktuelle Quelle")
	}
	// hoehere Schicht taucht auf: Wechsel erst am Schluesselbild
	hi := &Source{Owner: "p1", Layer: "h", Kind: Video}
	room.AddSource(hi)
	hi.Inject(vp8pkt(500, 90000, true, false, true))
	room.Rebalance()
	hi.Inject(vp8pkt(501, 93000, true, true, false))
	hi.Inject(vp8pkt(502, 93000, false, false, true))
	waitFor(t, func() bool { return w.count() == 4 })
	if o.Current() != hi {
		t.Fatal("kein Wechsel")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	for i := 1; i < len(w.seqs); i++ {
		if w.seqs[i] != w.seqs[i-1]+1 {
			t.Fatalf("folgenummern nicht lueckenlos: %v", w.seqs)
		}
	}
}

func TestDropOldFramesNotAudio(t *testing.T) {
	old := MaxQueueAge
	MaxQueueAge = 20 * time.Millisecond
	defer func() { MaxQueueAge = old }()
	room := NewRoom(time.Hour)
	defer room.Close()
	leg := NewLeg()
	defer leg.Close()
	w := &rec{block: make(chan struct{})}
	v := leg.NewOut("v", "p", Video, ClassTile, "l")
	a := leg.NewOut("a", "p", Audio, ClassAudio, "a")
	v.Bind(w, 1, 96)
	a.Bind(w, 2, 111)
	room.AddOut(v)
	room.AddOut(a)
	var plis int
	vs := &Source{Owner: "p", Layer: "l", Kind: Video, PLI: func(uint32) { plis++ }}
	as := &Source{Owner: "p", Layer: "a", Kind: Audio}
	room.AddSource(vs)
	room.AddSource(as)
	// erstes Schluesselbild blockiert den Schreiber, dahinter stauen sich Pakete
	vs.Inject(vp8pkt(1, 0, true, true, true))
	for i := 2; i < 6; i++ {
		vs.Inject(vp8pkt(uint16(i), uint32(i)*3000, true, false, true))
		as.Inject(vp8pkt(uint16(i), uint32(i)*960, false, false, false))
	}
	time.Sleep(40 * time.Millisecond) // alles aelter als MaxQueueAge
	close(w.block)
	waitFor(t, func() bool { return leg.Stats.FramesDropped.Load() == 4 })
	// Ton zuerst (Vorrang) und vollstaendig, Bild: nur das Schluesselbild
	waitFor(t, func() bool { return w.count() == 5 })
	if leg.Stats.Stalls.Load() != 1 || plis != 1 {
		t.Fatalf("stalls %d plis %d", leg.Stats.Stalls.Load(), plis)
	}
	// naechstes Schluesselbild beendet das Verwerfen, Folgenummer lueckenlos
	vs.Inject(vp8pkt(6, 18000, true, true, true))
	waitFor(t, func() bool { return w.count() == 6 })
	w.mu.Lock()
	last := w.seqs[len(w.seqs)-1]
	w.mu.Unlock()
	if last != 2 {
		t.Fatalf("folgenummer nach verwerfen %d, erwartet 2", last)
	}
}

type nopW struct{}

func (nopW) WriteRTP(*rtp.Header, []byte) (int, error) { return 0, nil }

func TestHotPathNoAlloc(t *testing.T) {
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})} // ohne Schreiber
	o := leg.NewOut("v", "p", Video, ClassTile, "l")
	o.Bind(nopW{}, 1, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	o.cur.Store(s)
	s.subscribe(o)
	b := vp8pkt(1, 0, true, false, true)
	// warm laufen (Pool fuellen)
	for i := 0; i < 10; i++ {
		s.Inject(b)
		e, _ := leg.pop()
		leg.process(e.p, e.o)
		e.p.release()
	}
	n := testing.AllocsPerRun(2000, func() {
		s.Inject(b)
		e, _ := leg.pop()
		leg.process(e.p, e.o)
		e.p.release()
	})
	if n != 0 {
		t.Fatalf("allokationen je paket: %v", n)
	}
}

func BenchmarkFanout10(b *testing.B) {
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	var legs []*Leg
	for i := 0; i < 10; i++ {
		l := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
		o := l.NewOut("v", "p", Video, ClassTile, "l")
		o.Bind(nopW{}, 1, 96)
		o.cur.Store(s)
		s.subscribe(o)
		legs = append(legs, l)
	}
	pk := vp8pkt(1, 0, true, false, true)
	pk = append(pk, make([]byte, 1000)...)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		s.Inject(pk)
		for _, l := range legs {
			e, _ := l.pop()
			l.process(e.p, e.o)
			e.p.release()
		}
	}
}

// vp8tid baut ein VP8-Paket mit PictureID (15 Bit), TL0PICIDX und TID.
func vp8tid(seq uint16, ts uint32, pic uint16, tl0, tid uint8, sync, key bool) []byte {
	b := make([]byte, 12, 64)
	b[0] = 0x80
	b[1] = 96 | 0x80
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	binary.BigEndian.PutUint32(b[8:], 1)
	t := tid << 6
	if sync {
		t |= 0x20
	}
	pl := byte(1)
	if key {
		pl = 0
	}
	return append(b, 0x90, 0xe0, 0x80|byte(pic>>8), byte(pic), tl0, t, pl, 7, 7)
}

type hrec struct {
	mu   sync.Mutex
	hdrs []rtp.Header
	pls  [][]byte
}

func (r *hrec) WriteRTP(h *rtp.Header, p []byte) (int, error) {
	r.mu.Lock()
	c := *h
	c.CSRC = append([]uint32(nil), h.CSRC...)
	r.hdrs = append(r.hdrs, c)
	r.pls = append(r.pls, append([]byte(nil), p...))
	r.mu.Unlock()
	return len(p), nil
}

func (r *hrec) count() int { r.mu.Lock(); defer r.mu.Unlock(); return len(r.hdrs) }

func TestTemporalAndPictureID(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassTile)
	o.Bind(w, 9, 96)
	a := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000, CSRC: 77}
	o.Assign(a)
	o.SetMaxTID(0) // nur TL0
	// L1T2: TID 0,1,0,1 ...
	seq, pic := uint16(10), uint16(100)
	for i := 0; i < 6; i++ {
		a.Inject(vp8tid(seq, uint32(i)*3000, pic, uint8(i/2), uint8(i%2), i%2 == 1, i == 0))
		seq++
		pic++
	}
	waitFor(t, func() bool { return w.count() == 3 })
	if leg.Stats.TIDSkipped.Load() != 3 {
		t.Fatalf("ausgelassen %d", leg.Stats.TIDSkipped.Load())
	}
	// Schicht wieder frei: ab dem naechsten Y-Rahmen
	o.SetMaxTID(3)
	for i := 6; i < 10; i++ {
		a.Inject(vp8tid(seq, uint32(i)*3000, pic, uint8(i/2), uint8(i%2), i%2 == 1, false))
		seq++
		pic++
	}
	waitFor(t, func() bool { return w.count() == 7 })
	// Wechsel auf andere Quelle (andere PictureID-Folge) am Schluesselbild
	b := &Source{Owner: "q", Layer: "l", Kind: Video, Clock: 90000, CSRC: 88}
	o.Assign(b)
	b.Inject(vp8tid(5000, 777, 9000, 40, 0, false, false)) // kein Schluesselbild
	b.Inject(vp8tid(5001, 3777, 9001, 41, 0, false, true))
	waitFor(t, func() bool { return w.count() == 8 })
	w.mu.Lock()
	defer w.mu.Unlock()
	var lastPic uint16
	var lastTL0 uint8
	for i, h := range w.hdrs {
		v := parse(w.pls[i])
		if i > 0 {
			if h.SequenceNumber != w.hdrs[i-1].SequenceNumber+1 {
				t.Fatalf("seq %d: %d nach %d", i, h.SequenceNumber, w.hdrs[i-1].SequenceNumber)
			}
			if v.PicID != lastPic+1 {
				t.Fatalf("picid %d: %d nach %d", i, v.PicID, lastPic)
			}
			if v.TL0 != lastTL0 && v.TL0 != lastTL0+1 {
				t.Fatalf("tl0 %d: %d nach %d", i, v.TL0, lastTL0)
			}
		}
		lastPic, lastTL0 = v.PicID, v.TL0
		want := uint32(77)
		if i == 7 {
			want = 88
		}
		if len(h.CSRC) != 1 || h.CSRC[0] != want || h.SSRC != 9 {
			t.Fatalf("csrc/ssrc %d: %v %d", i, h.CSRC, h.SSRC)
		}
	}
	// Taktgenauer Zeitstempel: Abstand > 0 trotz fremder Zeitbasis
	if d := w.hdrs[7].Timestamp - w.hdrs[6].Timestamp; d == 0 || d > 90000*10 {
		t.Fatalf("zeitstempel-sprung %d", d)
	}
	if o.Current() != b {
		t.Fatal("kein wechsel")
	}
}

func parse(p []byte) rtpx.VP8 { return rtpx.ParseVP8(p) }

func TestAudioSwitchAndAssign(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("a", "", Audio, ClassAudio)
	o.Bind(w, 3, 111)
	x := &Source{Owner: "x", Layer: "a", Kind: Audio, Clock: 48000, CSRC: 1}
	y := &Source{Owner: "y", Layer: "a", Kind: Audio, Clock: 48000, CSRC: 2}
	o.Assign(x)
	x.Inject(vp8pkt(1, 960, false, false, false))
	waitFor(t, func() bool { return w.count() == 1 })
	o.Assign(y)
	x.Inject(vp8pkt(2, 1920, false, false, false)) // laeuft bis zum ersten y-Paket
	waitFor(t, func() bool { return w.count() == 2 })
	time.Sleep(20 * time.Millisecond)
	y.Inject(vp8pkt(900, 555555, false, false, false))
	x.Inject(vp8pkt(3, 2880, false, false, false)) // nach dem Wechsel verworfen
	waitFor(t, func() bool { return w.count() == 3 })
	time.Sleep(10 * time.Millisecond)
	w.mu.Lock()
	h := w.hdrs[2]
	d := h.Timestamp - w.hdrs[1].Timestamp
	w.mu.Unlock()
	if h.CSRC[0] != 2 || h.SequenceNumber != w.hdrs[1].SequenceNumber+1 || d < 48*15 || d > 48*200 {
		t.Fatalf("wechsel csrc %v seq %d ts-abstand %d", h.CSRC, h.SequenceNumber, d)
	}
	if subs := x.subs.Load(); subs != nil && len(*subs) != 0 {
		o.Assign(y)
	}
	o.Assign(y)
	if subs := x.subs.Load(); subs != nil && len(*subs) != 0 {
		t.Fatal("alte quelle noch abonniert")
	}
	o.Assign(nil)
	if o.Current() != nil {
		t.Fatal("pause")
	}
	o.Detach()
	if subs := y.subs.Load(); subs != nil && len(*subs) != 0 {
		t.Fatal("detach")
	}
}

func TestPLIResponseTime(t *testing.T) {
	old := Totals.PLIResp[0].Load()
	var all uint64
	for i := range Totals.PLIResp {
		all += Totals.PLIResp[i].Load()
	}
	s := &Source{Owner: "p", Layer: "l", Kind: Video, PLI: func(uint32) {}}
	s.RequestPLI()
	s.Inject(vp8pkt(1, 0, true, true, true))
	var now uint64
	for i := range Totals.PLIResp {
		now += Totals.PLIResp[i].Load()
	}
	if now != all+1 || Totals.PLIResp[0].Load() != old+1 {
		t.Fatalf("pli-antwort nicht erfasst")
	}
	if PLIRespP(0.5) <= 0 {
		t.Fatal("perzentil")
	}
}

func TestThrottleQueuesAndDrops(t *testing.T) {
	old := MaxQueueAge
	MaxQueueAge = 30 * time.Millisecond
	defer func() { MaxQueueAge = old }()
	leg := NewLeg()
	defer leg.Close()
	leg.SetThrottle(80_000) // 10 kB/s
	w := &rec{}
	o := leg.NewOut("v", "p", Video, ClassTile)
	o.Bind(w, 1, 96)
	var plis atomic.Int32
	s := &Source{Owner: "p", Layer: "l", Kind: Video, PLI: func(uint32) { plis.Add(1) }}
	o.Assign(s)
	big := append(vp8pkt(0, 0, true, true, true), make([]byte, 1000)...)
	s.Inject(big)
	for i := 1; i < 40; i++ {
		p := append(vp8pkt(uint16(i), uint32(i)*3000, true, false, true), make([]byte, 1000)...)
		s.Inject(p)
	}
	waitFor(t, func() bool { return leg.Stats.Stalls.Load() >= 1 })
	if leg.Stats.TickAgeNs.Load() < int64(MaxQueueAge) {
		t.Fatal("alter nicht gemeldet")
	}
}

func TestHotPathRewriteNoAlloc(t *testing.T) {
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})} // ohne Schreiber
	o := leg.NewOut("v", "p", Video, ClassTile, "l")
	o.Bind(nopW{}, 1, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, CSRC: 5, Clock: 90000}
	o.cur.Store(s)
	s.subscribe(o)
	o.picDelta, o.tl0Delta, o.started = 17, 3, true // Umschreiben erzwingen
	o.SetMaxTID(0)
	b0 := vp8tid(1, 0, 100, 1, 0, false, false)
	b1 := vp8tid(2, 0, 101, 1, 1, false, false)
	run := func() {
		s.Inject(b0)
		e, _ := leg.pop()
		leg.process(e.p, e.o)
		e.p.release()
		s.Inject(b1)
		e, _ = leg.pop()
		leg.process(e.p, e.o)
		e.p.release()
	}
	for i := 0; i < 10; i++ {
		run()
	}
	if n := testing.AllocsPerRun(2000, run); n != 0 {
		t.Fatalf("allokationen je paket: %v", n)
	}
}

// TestMirrorKeepsAllTemporalLayers: ohne Begrenzung gehen alle zeitlichen
// Schichten durch (Regression: neue Ausgaenge standen auf TID 0, der
// Spiegel der Probe lieferte bei L1T3 nur ein Viertel der Bilder).
func TestMirrorKeepsAllTemporalLayers(t *testing.T) {
	room := NewRoom(time.Hour)
	defer room.Close()
	leg := NewLeg()
	defer leg.Close()
	w := &hrec{}
	o := leg.NewOut("v", "p", Video, ClassTile, "l")
	o.Bind(w, 1, 96)
	room.AddOut(o)
	s := &Source{Owner: "p", Layer: "l", Kind: Video}
	room.AddSource(s)
	pat := []uint8{0, 2, 1, 2}
	for i := 0; i < 40; i++ {
		tid := pat[i%4]
		s.Inject(vp8tid(uint16(i), uint32(i)*3000, uint16(i), uint8(i/4), tid, tid > 0, i == 0))
	}
	waitFor(t, func() bool { return w.count() == 40 })
	if o.MaxTID() != 3 || leg.Stats.TIDSkipped.Load() != 0 {
		t.Fatalf("tid %d ausgelassen %d", o.MaxTID(), leg.Stats.TIDSkipped.Load())
	}
}
