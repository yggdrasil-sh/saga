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
	TIDSkipped            atomic.Uint64 // wegen zeitlicher Schicht ausgelassene Rahmen
	Pauses, PauseNs       atomic.Uint64 // Bildpausen durch Verwerfen bis Schluesselbild
	WriteStalls           atomic.Uint64 // Schreibvorgaenge > WriteStallMin (HoL im Kernel/TCP)
	WriteStallNs          atomic.Uint64
	PLIResp               [24]atomic.Uint64 // Zeit PLI -> Schluesselbild, Klasse k = [2^k, 2^(k+1)) ms
	Switches              atomic.Uint64
}

// WriteStallMin: ab dieser Dauer zaehlt ein Schreibvorgang als Stau.
var WriteStallMin = int64(50 * time.Millisecond)

// PLIRespP liefert ein Perzentil der PLI-Antwortzeit in Millisekunden.
func PLIRespP(q float64) float64 { return histP(&Totals.PLIResp, q) }

func histAdd(h *[24]atomic.Uint64, v int64) {
	k := 0
	for v > 1 && k < 23 {
		v >>= 1
		k++
	}
	h[k].Add(1)
}

// Source ist eine eingehende Spur (eine Simulcast-Schicht bzw. der Ton).
type Source struct {
	Owner string // Teilnehmer-Kennung (zufaellig, kein Name)
	Layer string // "a" fuer Ton, sonst Schicht ("l", "m", "h")
	Kind  Kind
	SSRC  uint32

	// PLI sendet eine Schluesselbild-Anforderung an den Absender.
	PLI func(ssrc uint32)

	Clock    uint32 // Taktrate (48000 Ton, 90000 Bild); 0 = alte Naeherung
	CSRC     uint32 // Kennung des Absenders im Raum (0 = keine)
	LevelExt uint8  // Kopferweiterung ssrc-audio-level (0 = keine)

	mu      sync.Mutex
	subs    atomic.Pointer[[]*Out]
	last    atomic.Int64
	lastPLI atomic.Int64
	pliAt   atomic.Int64 // offene Anforderung seit
	skip    uint16       // nur Lese-Goroutine
	Pkts    atomic.Uint64
	Bytes   atomic.Uint64

	// Steuerdaten, vom Takt gelesen und zurueckgesetzt
	VoicePkts atomic.Uint32    // Pakete mit Sprache (Pegel unter VoiceLevel)
	LevelSum  atomic.Uint32    // Summe (127-Pegel) dieser Pakete
	LastVoice atomic.Int64     // letzte Sprache
	TIDFrames [4]atomic.Uint32 // Rahmenanfaenge je zeitlicher Schicht
	HasTID    atomic.Bool
	Keys      atomic.Uint32
	LastKey   atomic.Int64
}

// VoiceLevel: Pegel (-dBov) bis zu dem ein Paket als Sprache zaehlt.
var VoiceLevel uint8 = 55

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
		s.pliAt.CompareAndSwap(0, now)
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
		if p.vp8.FrameStart {
			if p.vp8.HasTID {
				s.HasTID.Store(true)
				s.TIDFrames[p.vp8.TID&3].Add(1)
			} else {
				s.TIDFrames[0].Add(1)
			}
			if p.vp8.Key {
				s.Keys.Add(1)
				s.LastKey.Store(now)
				if at := s.pliAt.Swap(0); at != 0 {
					histAdd(&Totals.PLIResp, (now-at)/int64(time.Millisecond))
				}
			}
		}
	} else {
		p.vp8 = rtpx.VP8{}
		if s.LevelExt != 0 {
			if lv, _, ok := rtpx.AudioLevel(p.buf[:n], &p.h, s.LevelExt); ok && lv <= VoiceLevel {
				s.VoicePkts.Add(1)
				s.LevelSum.Add(uint32(127 - lv))
				s.LastVoice.Store(now)
			}
		}
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

	leg    *Leg
	bound  atomic.Pointer[binding]
	cur    atomic.Pointer[Source]
	pend   atomic.Pointer[Source]
	want   atomic.Bool   // Schluesselbild erwartet
	maxTID atomic.Uint32 // hoechste weitergeleitete zeitliche Schicht (>=3 = alle)

	// Steuerpfad
	ctl      sync.Mutex
	subs     map[*Source]struct{}
	assignAt int64

	// nur Schreiber-Goroutine
	hdr        rtp.Header
	csrc       [1]uint32
	started    bool
	dropping   bool
	skipFrame  bool
	tidCur     uint8
	seqDelta   uint16
	tsDelta    uint32
	lastSeq    uint16
	lastTS     uint32
	lastT      int64
	picStarted bool
	picDelta   uint16
	tl0Delta   uint8
	lastPic    uint16
	lastTL0    uint8
	pauseAt    int64

	// Zaehler des Ausgangs
	Pkts, Bytes atomic.Uint64
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

// MaxTID liefert die eingestellte hoechste zeitliche Schicht.
func (o *Out) MaxTID() uint8 { return uint8(o.maxTID.Load()) }

// SetMaxTID begrenzt die zeitlichen Schichten (Bildrate). 3 = alle.
// Runter sofort am naechsten Rahmen, rauf am naechsten Y-Rahmen bzw.
// Schluesselbild.
func (o *Out) SetMaxTID(t uint8) {
	if t > 3 {
		t = 3
	}
	o.maxTID.Store(uint32(t))
}

// Pending liefert die Quelle, auf die gewechselt wird.
func (o *Out) Pending() *Source { return o.pend.Load() }

// Assign waehlt die Quelle des Ausgangs (Steuerpfad, z. B. im Takt).
// nil pausiert. Gewechselt wird beim Bild am naechsten Schluesselbild, bis
// dahin laeuft die alte Quelle weiter (erst verbinden, dann trennen).
func (o *Out) Assign(s *Source) {
	o.ctl.Lock()
	defer o.ctl.Unlock()
	if o.subs == nil {
		o.subs = map[*Source]struct{}{}
	}
	cur := o.cur.Load()
	switch {
	case s == nil:
		o.pend.Store(nil)
		o.cur.Store(nil)
	case s == cur:
		o.pend.Store(nil)
	default:
		if o.pend.Load() != s {
			o.pend.Store(s)
			o.assignAt = Now()
		}
		if _, ok := o.subs[s]; !ok {
			o.subs[s] = struct{}{}
			s.subscribe(o)
		}
		if o.Kind == Video {
			s.RequestPLI()
		}
	}
	cur, pend := o.cur.Load(), o.pend.Load()
	if cur != nil {
		if _, ok := o.subs[cur]; !ok {
			o.subs[cur] = struct{}{}
			cur.subscribe(o)
		}
		if o.Kind == Video && o.want.Load() {
			cur.RequestPLI()
		}
	}
	for x := range o.subs {
		if x != cur && x != pend {
			x.unsubscribe(o)
			delete(o.subs, x)
		}
	}
}

// Detach loest alle Abos des Ausgangs (beim Schliessen).
func (o *Out) Detach() {
	o.ctl.Lock()
	defer o.ctl.Unlock()
	for x := range o.subs {
		x.unsubscribe(o)
	}
	o.subs = nil
	o.pend.Store(nil)
	o.cur.Store(nil)
}

// Forget vergisst eine entfernte Quelle (Steuerpfad).
func (o *Out) Forget(s *Source) {
	o.ctl.Lock()
	defer o.ctl.Unlock()
	o.cur.CompareAndSwap(s, nil)
	o.pend.CompareAndSwap(s, nil)
	if _, ok := o.subs[s]; ok {
		s.unsubscribe(o)
		delete(o.subs, s)
	}
}

func (o *Out) anchor(p *Packet, now int64) {
	in := p.h.Seq - p.skip
	if !o.started {
		o.started = true
		o.seqDelta = 0
		o.tsDelta = 0
		return
	}
	o.seqDelta = o.lastSeq + 1 - in
	step := uint32(3000)
	if clk := uint64(p.src.Clock); clk != 0 && o.lastT != 0 {
		el := now - o.lastT
		if el < 0 {
			el = 0
		}
		st := uint64(el) * clk / uint64(time.Second)
		if st < 1 {
			st = 1
		}
		if st > clk*10 {
			st = clk * 10
		}
		step = uint32(st)
	}
	o.tsDelta = o.lastTS + step - p.h.TS
	if o.Kind == Video && p.vp8.HasPicID {
		if o.picStarted {
			o.picDelta = o.lastPic + 1 - p.vp8.PicID
			o.tl0Delta = o.lastTL0 + 1 - p.vp8.TL0
		}
	}
}

func (o *Out) endPause(now int64) {
	if o.pauseAt != 0 {
		d := uint64(now - o.pauseAt)
		o.pauseAt = 0
		o.leg.Stats.Pauses.Add(1)
		o.leg.Stats.PauseNs.Add(d)
		Totals.Pauses.Add(1)
		Totals.PauseNs.Add(d)
	}
}

// LegStats sind die Zaehler eines Beins.
type LegStats struct {
	PktsOut, BytesOut     atomic.Uint64
	FramesDropped, Stalls atomic.Uint64
	Overflow, WriteErrs   atomic.Uint64
	MaxAgeNs              atomic.Int64
	Hist                  [24]atomic.Uint64 // Weiterleitungszeit, Klasse k = [2^k, 2^(k+1)) us
	QueueLen              atomic.Int64
	TickAgeNs             atomic.Int64 // groesstes Alter seit dem letzten Takt (Schaetzung)
	TickDrops             atomic.Uint32
	Pauses, PauseNs       atomic.Uint64
	WriteStalls           atomic.Uint64
	WriteStallNs          atomic.Uint64
	TIDSkipped            atomic.Uint64
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

	// nur Schreiber-Goroutine
	scratch [MTU]byte // Kopie fuer umgeschriebene Descriptoren
	tokens  float64
	tokT    int64

	throttle atomic.Int64 // Testdrossel in bit/s (0 = aus)
}

// SetThrottle begrenzt die Senderate des Beins (nur fuer Tests: nachgebildeter
// enger Downlink). 0 schaltet ab.
func (l *Leg) SetThrottle(bps int64) { l.throttle.Store(bps) }

func (l *Leg) shape(n int) {
	r := l.throttle.Load()
	if r <= 0 {
		return
	}
	now := Now()
	if l.tokT == 0 {
		l.tokT = now
	}
	l.tokens += float64(now-l.tokT) * float64(r) / 8 / 1e9
	l.tokT = now
	if l.tokens > 8000 {
		l.tokens = 8000
	}
	if l.tokens < 0 {
		time.Sleep(time.Duration(-l.tokens * 8 * 1e9 / float64(r)))
		now = Now()
		l.tokens += float64(now-l.tokT) * float64(r) / 8 / 1e9
		l.tokT = now
	}
	l.tokens -= float64(n)
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
		l.shape(e.p.n)
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
	if age > l.Stats.TickAgeNs.Load() {
		l.Stats.TickAgeNs.Store(age)
	}
	v := &p.vp8
	if o.Kind == Video {
		if src != cur {
			if src != o.pend.Load() || !v.Key || !v.FrameStart {
				return
			}
			// Schichtwechsel bzw. Start an einem Schluesselbild
			o.cur.Store(src)
			o.pend.CompareAndSwap(src, nil)
			cur = src
			o.anchor(p, now)
			o.dropping = false
			Totals.Switches.Add(1)
		}
		if v.FrameStart {
			want := uint8(o.maxTID.Load())
			if want < o.tidCur || v.Key || (v.Sync && v.TID <= want) {
				o.tidCur = want
			}
			switch {
			case v.Key:
				o.dropping = false
				o.want.Store(false)
				o.endPause(now)
			case o.dropping:
				l.Stats.FramesDropped.Add(1)
				Totals.FramesDropped.Add(1)
				l.Stats.TickDrops.Add(1)
			case age > int64(MaxQueueAge):
				// Warteschlange zu alt: ganze Rahmen bis zum naechsten
				// Schluesselbild verwerfen statt zu stauen (H3).
				o.dropping = true
				o.pauseAt = now
				o.want.Store(true)
				l.Stats.Stalls.Add(1)
				Totals.Stalls.Add(1)
				l.Stats.FramesDropped.Add(1)
				Totals.FramesDropped.Add(1)
				l.Stats.TickDrops.Add(1)
				src.RequestPLI()
			}
			o.skipFrame = !o.dropping && v.HasTID && v.TID > o.tidCur
			if o.skipFrame {
				l.Stats.TIDSkipped.Add(1)
				Totals.TIDSkipped.Add(1)
			}
			if (o.dropping || o.skipFrame) && v.HasPicID {
				o.picDelta-- // PictureID bleibt lueckenlos
			}
		}
		if o.dropping || o.skipFrame {
			o.seqDelta-- // Folgenummern bleiben lueckenlos
			return
		}
	} else if src != cur {
		if src != o.pend.Load() {
			return
		}
		o.cur.Store(src)
		o.pend.CompareAndSwap(src, nil)
		o.anchor(p, now)
		Totals.Switches.Add(1)
	}
	seq := p.h.Seq - p.skip + o.seqDelta
	ts := p.h.TS + o.tsDelta
	h := &o.hdr
	h.Version = 2
	h.Padding = false
	h.Extension = false
	h.Extensions = h.Extensions[:0]
	h.Marker = p.h.Marker
	h.PayloadType = b.pt
	h.SequenceNumber = seq
	h.Timestamp = ts
	h.SSRC = b.ssrc
	if src.CSRC != 0 {
		o.csrc[0] = src.CSRC
		h.CSRC = o.csrc[:]
	} else {
		h.CSRC = nil
	}
	payload := p.buf[p.h.Off : p.h.Off+p.h.Len]
	if o.Kind == Video && (v.HasPicID || v.HasTL0) {
		pic := (v.PicID + o.picDelta) & v.PicIDMask()
		tl0 := v.TL0 + o.tl0Delta
		if pic != v.PicID || tl0 != v.TL0 {
			// geteiltes Paket: Descriptor auf einer Kopie umschreiben
			c := l.scratch[:len(payload)]
			copy(c, payload)
			rtpx.RewriteVP8(c, v, pic, tl0)
			payload = c
		}
		o.lastPic, o.lastTL0, o.picStarted = pic, tl0, true
	}
	t0 := Now()
	_, err := b.w.WriteRTP(h, payload)
	t1 := Now()
	if d := t1 - t0; d > WriteStallMin {
		l.Stats.WriteStalls.Add(1)
		l.Stats.WriteStallNs.Add(uint64(d))
		Totals.WriteStalls.Add(1)
		Totals.WriteStallNs.Add(uint64(d))
	}
	if err != nil {
		l.Stats.WriteErrs.Add(1)
		Totals.WriteErrs.Add(1)
		return
	}
	o.lastSeq, o.lastTS, o.lastT = seq, ts, now
	n := uint64(12 + 4*len(h.CSRC) + len(payload))
	o.Pkts.Add(1)
	o.Bytes.Add(n)
	l.Stats.PktsOut.Add(1)
	l.Stats.BytesOut.Add(n)
	Totals.PktsOut.Add(1)
	Totals.BytesOut.Add(n)
	us := (t1 - p.t) / 1000
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
