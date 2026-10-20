package fwd

import (
	"encoding/binary"
	"sync"
	"testing"
	"time"

	"github.com/pion/rtp"
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
