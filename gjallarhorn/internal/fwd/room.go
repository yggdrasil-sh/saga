package fwd

import (
	"sync"
	"time"
)

// Room verwaltet Quellen und Ausgaenge und waehlt je Ausgang die passende
// Schicht. Die Wahl laeuft im Takt (Steuerpfad), nie je Paket.
type Room struct {
	mu   sync.Mutex
	srcs map[*Source]struct{}
	outs map[*Out]struct{}
	stop chan struct{}
}

// NewRoom startet einen Raum mit Takt tick (z. B. 250 ms).
func NewRoom(tick time.Duration) *Room {
	r := &Room{srcs: map[*Source]struct{}{}, outs: map[*Out]struct{}{}, stop: make(chan struct{})}
	go func() {
		t := time.NewTicker(tick)
		defer t.Stop()
		for {
			select {
			case <-t.C:
				r.Rebalance()
			case <-r.stop:
				return
			}
		}
	}()
	return r
}

// Close beendet den Takt.
func (r *Room) Close() { close(r.stop) }

// AddSource meldet eine neue Quelle an.
func (r *Room) AddSource(s *Source) {
	r.mu.Lock()
	r.srcs[s] = struct{}{}
	r.mu.Unlock()
	r.Rebalance()
}

// RemoveSource meldet eine Quelle ab.
func (r *Room) RemoveSource(s *Source) {
	r.mu.Lock()
	delete(r.srcs, s)
	for o := range r.outs {
		o.cur.CompareAndSwap(s, nil)
		o.pend.CompareAndSwap(s, nil)
	}
	r.mu.Unlock()
	r.Rebalance()
}

// AddOut meldet einen Ausgang an.
func (r *Room) AddOut(o *Out) {
	r.mu.Lock()
	r.outs[o] = struct{}{}
	r.mu.Unlock()
	r.Rebalance()
}

// RemoveOut meldet einen Ausgang ab und loest alle Abos.
func (r *Room) RemoveOut(o *Out) {
	r.mu.Lock()
	delete(r.outs, o)
	for s := range r.srcs {
		s.unsubscribe(o)
	}
	r.mu.Unlock()
}

// Counts liefert Anzahl Quellen und Ausgaenge.
func (r *Room) Counts() (int, int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.srcs), len(r.outs)
}

// Rebalance waehlt fuer jeden Ausgang die Schicht und pflegt die Abos.
func (r *Room) Rebalance() {
	now := Now()
	r.mu.Lock()
	defer r.mu.Unlock()
	for o := range r.outs {
		cands := map[string]*Source{}
		for s := range r.srcs {
			if s.Owner == o.Owner && s.Kind == o.Kind {
				cands[s.Layer] = s
			}
		}
		var want *Source
		// erste aktive Schicht nach Vorliebe, sonst die erste vorhandene
		for _, l := range o.Prefs {
			if s := cands[l]; s != nil && s.Active(now) {
				want = s
				break
			}
		}
		if want == nil {
			for _, l := range o.Prefs {
				if s := cands[l]; s != nil {
					want = s
					break
				}
			}
		}
		cur := o.cur.Load()
		if want != nil && want != cur {
			if o.pend.Load() != want {
				o.pend.Store(want)
			}
		} else if want == cur {
			o.pend.Store(nil)
		}
		pend := o.pend.Load()
		for _, s := range cands {
			if s == cur || s == pend {
				s.subscribe(o)
			} else {
				s.unsubscribe(o)
			}
		}
		// Schluesselbild nachfordern, solange gewartet wird (Limit in RequestPLI)
		if pend != nil {
			pend.RequestPLI()
		} else if cur != nil && o.want.Load() {
			cur.RequestPLI()
		}
	}
}
