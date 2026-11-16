package wstun

// Flow: Fenster einer Senderichtung ueber einem WebSocket (gemeinsam fuer den Tunnel saga-tunnel.1 und das Rahmen-Bein
// saga-frames.1 des Browser-Wegs B2). Die Gegenseite quittiert die Summe der empfangenen Nutzbytes; der Sender haelt
// hoechstens ein Fenster unquittierter Bytes unterwegs (2 x Lieferrate x kleinste RTT + 16 KiB, begrenzt auf [Min, Max]).
// Darueber wartet Reserve - der Stau liegt dann beim Absender (Warteschlange des Beins), nicht im Socket-Puffer eines
// Proxys. Kleinste RTT und Lieferrate kommen aus den Quittungen (Marke je gesendetem Stueck).

import (
	"os"
	"sync"
	"sync/atomic"
	"time"
)

type sendMark struct {
	off uint64
	t   time.Time
}

// Flow ist die Flusssteuerung einer Senderichtung. Nullwert nicht benutzen (NewFlow).
type Flow struct {
	min, max int
	st       *Stats

	mu      sync.Mutex
	cond    *sync.Cond
	sent    uint64
	acked   uint64
	marks   []sendMark
	minRTT  time.Duration
	minAt   time.Time
	prevMin time.Duration
	bwT0    time.Time
	bwA0    uint64
	bw      [4]float64 // Byte/s je 500-ms-Fach (Hoechstwert ueber 2 s)
	bwIdx   int
	bwAt    time.Time
	clamp   bool // Gegenseite meldet stehende Warteschlange: Mindestfenster bis zur naechsten Quittung ohne Meldung
}

// NewFlow: Fenster zwischen min und max Byte (<= 0: Standardwerte); st nimmt die Zaehler auf (nil: eigene).
func NewFlow(min, max int, st *Stats) *Flow {
	if min <= 0 {
		min = DefMinWindow
	}
	if max < min {
		max = DefMaxWindow
		if max < min {
			max = min
		}
	}
	if st == nil {
		st = &Stats{}
	}
	f := &Flow{min: min, max: max, st: st}
	f.cond = sync.NewCond(&f.mu)
	st.Window.Store(int64(f.windowLocked()))
	return f
}

// Wake weckt wartende Reserve-Aufrufe (Frist geaendert, Verbindung zu).
func (f *Flow) Wake() {
	f.mu.Lock()
	f.cond.Broadcast()
	f.mu.Unlock()
}

// OnAck verarbeitet eine Quittung (Summe der bei der Gegenseite angekommenen Bytes).
func (f *Flow) OnAck(a uint64) { f.OnAckAt(a, time.Now()) }

// OnAckAt wie OnAck mit Zeitpunkt (Tests).
func (f *Flow) OnAckAt(a uint64, now time.Time) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if a > f.sent {
		a = f.sent // nie mehr als gesendet (fehlerhafte Gegenseite)
	}
	if a <= f.acked {
		return
	}
	f.acked = a
	// RTT: juengste Marke, die jetzt quittiert ist
	k := 0
	var rtt time.Duration = -1
	for k < len(f.marks) && f.marks[k].off <= a {
		rtt = now.Sub(f.marks[k].t)
		k++
	}
	if k > 0 {
		f.marks = append(f.marks[:0], f.marks[k:]...)
	}
	if rtt > 0 {
		// kleinste RTT ueber ~10 s (zwei Faecher)
		if now.Sub(f.minAt) > 5*time.Second {
			f.prevMin, f.minRTT, f.minAt = f.minRTT, rtt, now
		} else if rtt < f.minRTT || f.minRTT == 0 {
			f.minRTT = rtt
		}
		f.st.MinRTTus.Store(f.rttMin().Microseconds())
	}
	// Lieferrate (Fach 500 ms, Hoechstwert der letzten 4)
	if f.bwT0.IsZero() {
		f.bwT0, f.bwA0 = now, a
	} else if d := now.Sub(f.bwT0); d >= 100*time.Millisecond {
		r := float64(a-f.bwA0) / d.Seconds()
		if now.Sub(f.bwAt) > 500*time.Millisecond {
			f.bwIdx = (f.bwIdx + 1) % len(f.bw)
			f.bw[f.bwIdx] = 0
			f.bwAt = now
		}
		if r > f.bw[f.bwIdx] {
			f.bw[f.bwIdx] = r
		}
		f.bwT0, f.bwA0 = now, a
		f.st.BWBps.Store(int64(r))
	}
	f.st.Inflight.Store(int64(f.sent - f.acked))
	f.st.Window.Store(int64(f.windowLocked()))
	f.cond.Broadcast()
}

// Clamp: die Gegenseite meldet eine stehende Warteschlange (true) bzw. keine mehr (false) - solange gilt das
// Mindestfenster.
func (f *Flow) Clamp(on bool) {
	f.mu.Lock()
	if f.clamp != on {
		f.clamp = on
		f.st.Window.Store(int64(f.windowLocked()))
		f.cond.Broadcast()
	}
	f.mu.Unlock()
}

// rttMin: kleinste RTT beider Faecher (unter mu).
func (f *Flow) rttMin() time.Duration {
	m := f.minRTT
	if f.prevMin > 0 && (m == 0 || f.prevMin < m) {
		m = f.prevMin
	}
	return m
}

// windowLocked: 2 x Lieferrate x kleinste RTT + 16 KiB, begrenzt auf [min, max] (unter mu bzw. vor Start).
func (f *Flow) windowLocked() int {
	if f.clamp {
		return f.min
	}
	bw := 0.0
	for _, v := range f.bw {
		if v > bw {
			bw = v
		}
	}
	rtt := f.rttMin()
	if rtt <= 0 {
		rtt = 100 * time.Millisecond
	}
	w := int(2*bw*rtt.Seconds()) + 16<<10
	if w < f.min {
		w = f.min
	}
	if w > f.max {
		w = f.max
	}
	return w
}

// Window: aktuelles Fenster in Byte.
func (f *Flow) Window() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.windowLocked()
}

// Free: Platz im Fenster jetzt (ohne zu warten).
func (f *Flow) Free() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return int(f.sent-f.acked) < f.windowLocked()
}

// Reserve wartet, bis das Fenster mindestens ein Byte frei hat (wie TCP: das ganze Stueck geht dann). done: Verbindung
// zu (errClosed); dl: Frist als UnixNano (0 = keine; os.ErrDeadlineExceeded). Die Wartezeit zaehlt in Stats.Blocked.
func (f *Flow) Reserve(done <-chan struct{}, dl *atomic.Int64) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	t0 := time.Time{}
	for {
		select {
		case <-done:
			f.st.BlockedSince.Store(0)
			return errClosed
		default:
		}
		if int(f.sent-f.acked) < f.windowLocked() {
			if !t0.IsZero() {
				f.st.Blocked.Add(int64(time.Since(t0)))
				f.st.BlockedSince.Store(0)
			}
			return nil
		}
		if t0.IsZero() {
			t0 = time.Now()
			f.st.BlockedSince.Store(t0.UnixNano())
		}
		if d := dl.Load(); d != 0 && time.Now().UnixNano() >= d {
			f.st.Blocked.Add(int64(time.Since(t0)))
			f.st.BlockedSince.Store(0)
			return os.ErrDeadlineExceeded
		}
		// Wecker gegen Fristen (Wake weckt selbst, Quittungen auch)
		tm := time.AfterFunc(20*time.Millisecond, f.Wake)
		f.cond.Wait()
		tm.Stop()
	}
}

// Sent zaehlt n gesendete Bytes (mit Marke fuer die RTT, wenn mark).
func (f *Flow) Sent(n int, mark bool) {
	f.mu.Lock()
	f.sent += uint64(n)
	if mark && len(f.marks) < 4096 {
		f.marks = append(f.marks, sendMark{off: f.sent, t: time.Now()})
	}
	in := int64(f.sent - f.acked)
	f.mu.Unlock()
	if !mark {
		return
	}
	f.st.Inflight.Store(in)
	if in > f.st.MaxInflight.Load() {
		f.st.MaxInflight.Store(in)
	}
}

// BlockedNs: Wartezeit vor dem Fenster insgesamt, die laufende eingerechnet.
func (f *Flow) BlockedNs() int64 {
	n := f.st.Blocked.Load()
	if since := f.st.BlockedSince.Load(); since != 0 {
		n += time.Now().UnixNano() - since
	}
	return n
}
