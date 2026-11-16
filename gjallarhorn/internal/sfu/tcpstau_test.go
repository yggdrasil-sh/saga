package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// tcpRig: ein TCP-Bein (ohne GCC) mit Ende-zu-Ende-Signal aus sig, 200-ms-Takt.
type tcpRig struct {
	e   estimator
	l   *Leg
	sig TCPSig
	now int64
}

func newTCPRig(t *testing.T) *tcpRig {
	r := &tcpRig{l: &Leg{fl: fwd.NewLeg()}, now: int64(10 * time.Second)}
	t.Cleanup(r.l.fl.Close)
	r.e.init(1e6, 25e6)
	r.l.ExtQueue = func() TCPSig { return r.sig }
	return r
}

// step: ein Takt mit gesendeter Rate bps und eigener Warteschlange age.
func (r *tcpRig) step(bps float64, age time.Duration) float64 {
	r.l.fl.Stats.BytesOut.Add(uint64(bps * 0.2 / 8))
	r.l.fl.Stats.TickAgeNs.Store(int64(age))
	r.now += int64(200 * time.Millisecond)
	return r.e.update(r.l, 0.2, r.now)
}

func (r *tcpRig) set(q time.Duration, sent, acked float64) {
	r.sig = TCPSig{OK: true, Queue: q, SentBps: sent, AckedBps: acked, FbAge: 50 * time.Millisecond}
}

// Freie Leitung: Signal ohne Schlange aendert nichts gegenueber ohne Signal (Takt fuer Takt gleich).
func TestTCPStauFreiGleich(t *testing.T) {
	a, b := newTCPRig(t), newTCPRig(t)
	b.l.ExtQueue = nil
	for i := 0; i < 100; i++ {
		a.set(time.Duration(i%7)*time.Millisecond, 800e3, 800e3)
		va, vb := a.step(800e3, 5*time.Millisecond), b.step(800e3, 5*time.Millisecond)
		if va != vb {
			t.Fatalf("takt %d: mit Signal %v, ohne %v", i, va, vb)
		}
	}
	if s := a.e.e2e; s.Downs != 0 || s.Holds != 0 {
		t.Fatalf("frei: %+v", s)
	}
}

// Wachsende Schlange unterwegs, eigene Warteschlange leer: nach e2eRun Takten herunter auf 85 % der bestaetigten Rate,
// danach Abbau ohne Schritt nach oben, solange die Schlange steht.
func TestTCPStauWachsend(t *testing.T) {
	r := newTCPRig(t)
	for i := 0; i < 20; i++ {
		r.set(5*time.Millisecond, 900e3, 900e3)
		r.step(900e3, 5*time.Millisecond)
	}
	before := r.e.bps
	r.set(150*time.Millisecond, 900e3, 560e3)
	if v := r.step(900e3, 5*time.Millisecond); v != before {
		t.Fatalf("erster Takt ueber der Schwelle: %v statt %v (halten)", v, before)
	}
	r.set(190*time.Millisecond, 900e3, 560e3)
	v := r.step(900e3, 5*time.Millisecond)
	if want := 560e3 * 0.85; v < want*0.99 || v > want*1.01 {
		t.Fatalf("stau: %v, erwartet %v", v, want)
	}
	if r.e.downWhy != "e2e" || r.e.e2e.Downs != 1 {
		t.Fatalf("grund %q, downs %d", r.e.downWhy, r.e.e2e.Downs)
	}
	// Abbau: Schlange steht noch (waechst nicht mehr), Sperrzeit vorbei - kein Schritt nach oben bis e2eDrain
	low := r.e.bps
	r.set(300*time.Millisecond, 400e3, 560e3)
	for i := 0; i < 20; i++ { // 4 s
		if v := r.step(400e3, 5*time.Millisecond); v > low {
			t.Fatalf("abbau takt %d: %v > %v", i, v, low)
		}
	}
	// leer: wieder nach oben
	r.set(10*time.Millisecond, 400e3, 400e3)
	r.now += int64(e2eDrain)
	if v := r.step(400e3, 5*time.Millisecond); v <= low {
		t.Fatalf("nach dem Abbau: %v <= %v", v, low)
	}
}

// Stehende Schlange, die nicht waechst (fremder Download auf derselben Leitung): kein Stau, keine Sperre nach oben.
func TestTCPStauStehendFremd(t *testing.T) {
	a, b := newTCPRig(t), newTCPRig(t)
	b.l.ExtQueue = nil
	for i := 0; i < 100; i++ {
		a.set(200*time.Millisecond, 600e3, 600e3*(1+0.03*float64(i%3-1)))
		va, vb := a.step(600e3, 5*time.Millisecond), b.step(600e3, 5*time.Millisecond)
		if va != vb {
			t.Fatalf("takt %d: mit Signal %v, ohne %v", i, va, vb)
		}
	}
	if s := a.e.e2e; s.Downs != 0 || s.Holds != 0 {
		t.Fatalf("stehend: %+v", s)
	}
}

// Keine frische Rueckmeldung: kein Signal (wie ohne).
func TestTCPStauAlt(t *testing.T) {
	a, b := newTCPRig(t), newTCPRig(t)
	b.l.ExtQueue = nil
	for i := 0; i < 30; i++ {
		a.set(500*time.Millisecond, 900e3, 300e3)
		a.sig.FbAge = e2eStale + time.Millisecond
		va, vb := a.step(900e3, 5*time.Millisecond), b.step(900e3, 5*time.Millisecond)
		if va != vb {
			t.Fatalf("takt %d: mit altem Signal %v, ohne %v", i, va, vb)
		}
	}
	if a.e.e2e.Stale != 30 {
		t.Fatalf("stale %d", a.e.e2e.Stale)
	}
}

// Kurzer Ausschlag (Schluesselbild): ein Takt ueber der Schwelle senkt nicht.
func TestTCPStauEinTakt(t *testing.T) {
	r := newTCPRig(t)
	for i := 0; i < 50; i++ {
		q := 5 * time.Millisecond
		if i%10 == 9 {
			q = 180 * time.Millisecond
		}
		r.set(q, 900e3, 850e3)
		r.step(900e3, 5*time.Millisecond)
	}
	if r.e.e2e.Downs != 0 || r.e.downs != 0 {
		t.Fatalf("ausschlag: %+v downs %d", r.e.e2e, r.e.downs)
	}
}

// UDP-Beine (mit GCC) werten das Signal nicht, auch wenn es gesetzt waere.
func TestTCPStauNichtUDP(t *testing.T) {
	a, b := newTCPRig(t), newTCPRig(t)
	b.l.ExtQueue = nil
	a.l.Ext = func() int { return 3_000_000 }
	b.l.Ext = a.l.Ext
	for i := 0; i < 30; i++ {
		a.set(400*time.Millisecond, 900e3, 300e3)
		if va, vb := a.step(900e3, 5*time.Millisecond), b.step(900e3, 5*time.Millisecond); va != vb {
			t.Fatalf("takt %d: %v %v", i, va, vb)
		}
	}
	if a.e.e2eOn {
		t.Fatal("UDP-Bein wertet das Signal")
	}
}

// Sprung der Schlange (Download beginnt auf derselben Leitung): Sie steht danach, gesendet liegt noch rund 1 s ueber
// bestaetigt (die Spanne enthaelt den Sprung) - kein Stau, solange sie nicht weiter waechst. Waechst sie danach doch
// (der Knoten selbst ueberlastet die Leitung), greift die Regel.
func TestTCPStauSprung(t *testing.T) {
	r := newTCPRig(t)
	for i := 0; i < 20; i++ {
		r.set(2*time.Millisecond, 640e3, 640e3)
		r.step(640e3, 5*time.Millisecond)
	}
	before := r.e.bps
	for i, q := range []int{227, 237, 237, 238, 244, 241, 237, 235, 236, 240, 246, 233} {
		sent, ack := 632e3, 530e3
		if i >= 5 {
			ack = 630e3
		}
		r.set(time.Duration(q)*time.Millisecond, sent, ack)
		if v := r.step(640e3, 5*time.Millisecond); v < before {
			t.Fatalf("takt %d (q %d ms): gesenkt auf %v", i, q, v)
		}
	}
	if r.e.e2e.Downs != 0 {
		t.Fatalf("sprung: %+v", r.e.e2e)
	}
	// jetzt waechst sie weiter
	for _, q := range []int{250, 280, 310} {
		r.set(time.Duration(q)*time.Millisecond, 900e3, 600e3)
		r.step(900e3, 5*time.Millisecond)
	}
	if r.e.e2e.Downs != 1 {
		t.Fatalf("danach wachsend: %+v", r.e.e2e)
	}
}
