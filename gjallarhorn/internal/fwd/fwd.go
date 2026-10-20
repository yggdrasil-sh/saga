// Package fwd ist der Kern des Weiterleiters: Quellen (eingehende Spuren),
// Ausgaenge (abgehende Spuren je Empfaenger) und je Bein (PeerConnection)
// eine Vorrang-Warteschlange mit eigenem Schreiber.
//
// Heisser Pfad ohne Allokation: Pakete kommen aus einem Pool, werden einmal
// gelesen und per Referenzzaehler an alle Ausgaenge verteilt. Der Schreiber
// baut den RTP-Kopf in einer wiederverwendeten Struktur neu auf.
package fwd

import (
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/rtpx"
)

// Class ist der Vorrang in der Warteschlange (kleiner = wichtiger).
type Class uint8

const (
	ClassAudio   Class = iota // Ton, wird nie verworfen
	ClassScreen               // Bildschirm
	ClassSpeaker              // Sprecherbild
	ClassTile                 // Kacheln
	numClasses
)

// Kind unterscheidet Ton und Bild.
type Kind uint8

const (
	Audio Kind = iota
	Video
)

// MaxQueueAge: aelter darf ein Video-Rahmen am Rahmenanfang nicht sein (H3).
var MaxQueueAge = 150 * time.Millisecond

// PLIInterval: hoechstens eine Schluesselbild-Anforderung je Quelle.
var PLIInterval = 500 * time.Millisecond

// MTU des Lesepuffers.
const MTU = 1500

var epoch = time.Now()

// Now liefert monotone Nanosekunden seit Programmstart (ohne Allokation).
func Now() int64 { return int64(time.Since(epoch)) }

// Packet ist ein empfangenes RTP-Paket im Pool.
type Packet struct {
	buf  [MTU]byte
	n    int
	h    rtpx.Header
	vp8  rtpx.VP8
	skip uint16 // bis hierhin entfernte Fuellpakete der Quelle
	t    int64  // Empfangszeit
	src  *Source
	refs atomic.Int32
}

var pool = sync.Pool{New: func() any { return new(Packet) }}

func getPacket() *Packet { return pool.Get().(*Packet) }

func (p *Packet) release() {
	if p.refs.Add(-1) == 0 {
		p.src = nil
		pool.Put(p)
	}
}

// Totals sind prozessweite Zaehler (fuer bench und /api/stats).
var Totals struct {
	PktsIn, BytesIn       atomic.Uint64
	PktsOut, BytesOut     atomic.Uint64
	FramesDropped, Stalls atomic.Uint64
	Overflow, WriteErrs   atomic.Uint64
	PLIs                  atomic.Uint64
}

// Source ist eine eingehende Spur (eine Simulcast-Schicht bzw. der Ton).
type Source struct {
	Owner string // Teilnehmer-Kennung (zufaellig, kein Name)
	Layer string // "a" fuer Ton, sonst Schicht ("l", "m", "h")
	Kind  Kind
	SSRC  uint32

	// PLI sendet eine Schluesselbild-Anforderung an den Absender.
	PLI func(ssrc uint32)

	mu      sync.Mutex
	subs    atomic.Pointer[[]*Out]
	last    atomic.Int64
	lastPLI atomic.Int64
	skip    uint16 // nur Lese-Goroutine
	Pkts    atomic.Uint64
	Bytes   atomic.Uint64
}

// Active meldet, ob die Quelle in der letzten Sekunde Pakete hatte.
func (s *Source) Active(now int64) bool {
	l := s.last.Load()
	return l != 0 && now-l < int64(time.Second)
}

func (s *Source) subscribe(o *Out) {
	s.mu.Lock()
	defer s.mu.Unlock()
	old := s.subs.Load()
	var n []*Out
	if old != nil {
		for _, x := range *old {
			if x == o {
				return
			}
		}
		n = make([]*Out, len(*old), len(*old)+1)
		copy(n, *old)
	}
	n = append(n, o)
	s.subs.Store(&n)
}

func (s *Source) unsubscribe(o *Out) {
	s.mu.Lock()
	defer s.mu.Unlock()
	old := s.subs.Load()
	if old == nil {
		return
	}
	n := make([]*Out, 0, len(*old))
	for _, x := range *old {
		if x != o {
			n = append(n, x)
		}
	}
	s.subs.Store(&n)
}

// RequestPLI fordert ein Schluesselbild an (begrenzt auf 1 je PLIInterval).
func (s *Source) RequestPLI() {
	if s.Kind != Video || s.PLI == nil {
		return
	}
	now := Now()
	l := s.lastPLI.Load()
	if l != 0 && now-l < int64(PLIInterval) {
		return
	}
	if s.lastPLI.CompareAndSwap(l, now) {
		Totals.PLIs.Add(1)
		s.PLI(s.SSRC)
	}
}

// ReadLoop liest Pakete mit read (z. B. TrackRemote.Read) und verteilt sie,
// bis read einen Fehler liefert.
func (s *Source) ReadLoop(read func([]byte) (int, error)) {
	for {
		p := getPacket()
		n, err := read(p.buf[:])
		if err != nil {
			pool.Put(p)
			return
		}
		s.handle(p, n)
	}
}

// Inject verteilt ein fertiges Paket (fuer Tests und synthetische Quellen).
func (s *Source) Inject(b []byte) {
	p := getPacket()
	n := copy(p.buf[:], b)
	s.handle(p, n)
}

func (s *Source) handle(p *Packet, n int) {
	if !rtpx.Parse(p.buf[:n], &p.h) {
		pool.Put(p)
		return
	}
	now := Now()
	s.last.Store(now)
	s.Pkts.Add(1)
	s.Bytes.Add(uint64(n))
	Totals.PktsIn.Add(1)
	Totals.BytesIn.Add(uint64(n))
	if p.h.Len == 0 { // reines Fuellpaket (Bandbreiten-Probe): nicht weiterleiten
		s.skip++
		pool.Put(p)
		return
	}
	p.n = n
	p.skip = s.skip
	p.t = now
	p.src = s
	if s.Kind == Video {
		p.vp8 = rtpx.ParseVP8(p.buf[p.h.Off : p.h.Off+p.h.Len])
	} else {
		p.vp8 = rtpx.VP8{}
	}
	subs := s.subs.Load()
	if subs == nil || len(*subs) == 0 {
		pool.Put(p)
		return
	}
	list := *subs
	p.refs.Store(int32(len(list)))
	for _, o := range list {
		o.leg.enqueue(p, o)
	}
}

// Writer ist die Schreibseite einer abgehenden Spur (pion TrackLocalWriter).
type Writer interface {
	WriteRTP(h *rtp.Header, payload []byte) (int, error)
}

type binding struct {
	w    Writer
	ssrc uint32
	pt   uint8
}

// Out ist eine abgehende Spur an einen Empfaenger.
type Out struct {
	Owner string   // gewuenschter Absender
	Prefs []string // Schichten nach Vorliebe, z. B. {"h","m","l"}
	Class Class
	Kind  Kind
	ID    string // Spur-Kennung fuer pion

	leg   *Leg
	bound atomic.Pointer[binding]
	cur   atomic.Pointer[Source]
	pend  atomic.Pointer[Source]
	want  atomic.Bool // Schluesselbild erwartet

	// nur Schreiber-Goroutine
	hdr      rtp.Header
	started  bool
	dropping bool
	seqDelta uint16
	tsDelta  uint32
	lastSeq  uint16
	lastTS   uint32
}

// Bind setzt das Schreibziel (aus pion TrackLocal.Bind).
func (o *Out) Bind(w Writer, ssrc uint32, pt uint8) {
	o.bound.Store(&binding{w: w, ssrc: ssrc, pt: pt})
	o.want.Store(true)
}

// Unbind loest das Schreibziel.
func (o *Out) Unbind() { o.bound.Store(nil) }

// KeyRequest: der Empfaenger moechte ein Schluesselbild (PLI/FIR).
func (o *Out) KeyRequest() {
	if s := o.cur.Load(); s != nil {
		s.RequestPLI()
	}
}

// Current liefert die aktuell weitergeleitete Quelle.
func (o *Out) Current() *Source { return o.cur.Load() }

func (o *Out) anchor(p *Packet) {
	in := p.h.Seq - p.skip
	if !o.started {
		o.started = true
		o.seqDelta = 0
		o.tsDelta = 0
		return
	}
	o.seqDelta = o.lastSeq + 1 - in
	o.tsDelta = o.lastTS + 3000 - p.h.TS
}

// LegStats sind die Zaehler eines Beins.
type LegStats struct {
	PktsOut, BytesOut     atomic.Uint64
	FramesDropped, Stalls atomic.Uint64
	Overflow, WriteErrs   atomic.Uint64
	MaxAgeNs              atomic.Int64
	Hist                  [24]atomic.Uint64 // Weiterleitungszeit, Klasse k = [2^k, 2^(k+1)) us
	QueueLen              atomic.Int64
}

// P95us schaetzt das 95. Perzentil der Weiterleitungszeit in Mikrosekunden.
func (s *LegStats) P95us() float64 { return histP(&s.Hist, 0.95) }

func histP(h *[24]atomic.Uint64, q float64) float64 {
	var tot uint64
	for i := range h {
		tot += h[i].Load()
	}
	if tot == 0 {
		return 0
	}
	lim := uint64(float64(tot) * q)
	var acc uint64
	for i := range h {
		acc += h[i].Load()
		if acc >= lim {
			return float64(uint64(1) << (i + 1)) // obere Klassengrenze
		}
	}
	return float64(uint64(1) << 24)
}

type entry struct {
	p *Packet
	o *Out
}

const ringSize = 4096 // je Klasse, Zweierpotenz

type ring struct {
	buf        [ringSize]entry
	head, tail uint32
}

// Leg ist ein Bein (eine PeerConnection) mit Vorrang-Warteschlange.
type Leg struct {
	Stats LegStats

	flusher atomic.Pointer[flushBox]

	mu   sync.Mutex
	q    [numClasses]ring
	qlen int
	wake chan struct{}
	done chan struct{}
	once sync.Once
}

// Flusher leert gebuendelte Schreibvorgaenge (netx.CoalesceConn).
type Flusher interface{ Flush() }

type flushBox struct{ f Flusher }

// SetFlusher meldet die Verbindung, die nach jedem Schub geleert wird.
func (l *Leg) SetFlusher(f Flusher) { l.flusher.Store(&flushBox{f}) }

// NewLeg legt ein Bein an und startet seinen Schreiber.
func NewLeg() *Leg {
	l := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
	go l.run()
	return l
}

// NewOut legt einen Ausgang auf diesem Bein an.
func (l *Leg) NewOut(id, owner string, kind Kind, class Class, prefs ...string) *Out {
	return &Out{ID: id, Owner: owner, Kind: kind, Class: class, Prefs: prefs, leg: l}
}

// Close beendet den Schreiber und gibt wartende Pakete frei.
func (l *Leg) Close() {
	l.once.Do(func() { close(l.done) })
}

func (l *Leg) enqueue(p *Packet, o *Out) {
	l.mu.Lock()
	r := &l.q[o.Class]
	if r.tail-r.head >= ringSize {
		l.mu.Unlock()
		l.Stats.Overflow.Add(1)
		Totals.Overflow.Add(1)
		p.release()
		return
	}
	r.buf[r.tail&(ringSize-1)] = entry{p, o}
	r.tail++
	l.qlen++
	l.mu.Unlock()
	select {
	case l.wake <- struct{}{}:
	default:
	}
}

func (l *Leg) pop() (entry, bool) {
	l.mu.Lock()
	for c := range l.q {
		r := &l.q[c]
		if r.tail != r.head {
			i := r.head & (ringSize - 1)
			e := r.buf[i]
			r.buf[i] = entry{}
			r.head++
			l.qlen--
			l.mu.Unlock()
			return e, true
		}
	}
	l.mu.Unlock()
	return entry{}, false
}

func (l *Leg) run() {
	dirty := false
	for {
		e, ok := l.pop()
		if !ok {
			if dirty { // Schub zu Ende: gebuendelt an den Kernel
				dirty = false
				if fb := l.flusher.Load(); fb != nil {
					fb.f.Flush()
				}
				continue
			}
			select {
			case <-l.wake:
				continue
			case <-l.done:
				for {
					e, ok := l.pop()
					if !ok {
						return
					}
					e.p.release()
				}
			}
		}
		select {
		case <-l.done:
			e.p.release()
			continue
		default:
		}
		l.process(e.p, e.o)
		e.p.release()
		dirty = true
	}
}

// process entscheidet ueber ein Paket und schreibt es. Nur Schreiber-Goroutine.
func (l *Leg) process(p *Packet, o *Out) {
	b := o.bound.Load()
	if b == nil {
		return
	}
	src := p.src
	cur := o.cur.Load()
	now := Now()
	age := now - p.t
	if age > l.Stats.MaxAgeNs.Load() {
		l.Stats.MaxAgeNs.Store(age)
	}
	if o.Kind == Video {
		if src != cur {
			if src != o.pend.Load() || !p.vp8.Key {
				return
			}
			// Schichtwechsel bzw. Start an einem Schluesselbild
			o.cur.Store(src)
			o.pend.CompareAndSwap(src, nil)
			cur = src
			o.anchor(p)
			o.dropping = false
		}
		if p.vp8.FrameStart {
			switch {
			case p.vp8.Key:
				o.dropping = false
				o.want.Store(false)
			case o.dropping:
				l.Stats.FramesDropped.Add(1)
				Totals.FramesDropped.Add(1)
			case age > int64(MaxQueueAge):
				// Warteschlange zu alt: ganze Rahmen bis zum naechsten
				// Schluesselbild verwerfen statt zu stauen (H3).
				o.dropping = true
				o.want.Store(true)
				l.Stats.Stalls.Add(1)
				Totals.Stalls.Add(1)
				l.Stats.FramesDropped.Add(1)
				Totals.FramesDropped.Add(1)
				src.RequestPLI()
			}
		}
		if o.dropping {
			o.seqDelta-- // Folgenummern bleiben lueckenlos
			return
		}
	} else {
		if src != cur {
			if cur != nil || src != o.pend.Load() {
				return
			}
			o.cur.Store(src)
			o.pend.CompareAndSwap(src, nil)
			o.anchor(p)
		}
	}
	seq := p.h.Seq - p.skip + o.seqDelta
	ts := p.h.TS + o.tsDelta
	h := &o.hdr
	h.Version = 2
	h.Padding = false
	h.Extension = false
	h.Marker = p.h.Marker
	h.PayloadType = b.pt
	h.SequenceNumber = seq
	h.Timestamp = ts
	h.SSRC = b.ssrc
	payload := p.buf[p.h.Off : p.h.Off+p.h.Len]
	if _, err := b.w.WriteRTP(h, payload); err != nil {
		l.Stats.WriteErrs.Add(1)
		Totals.WriteErrs.Add(1)
		return
	}
	o.lastSeq, o.lastTS = seq, ts
	n := uint64(12 + len(payload))
	l.Stats.PktsOut.Add(1)
	l.Stats.BytesOut.Add(n)
	Totals.PktsOut.Add(1)
	Totals.BytesOut.Add(n)
	us := (Now() - p.t) / 1000
	k := 0
	for us > 1 && k < 23 {
		us >>= 1
		k++
	}
	l.Stats.Hist[k].Add(1)
}

// QueueLen liefert die aktuelle Laenge der Warteschlange.
func (l *Leg) QueueLen() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.qlen
}
