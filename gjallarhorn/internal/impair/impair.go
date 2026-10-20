// Package impair bildet einen schlechten TCP-Weg im Prozess nach, wo tc/netem
// fehlt (Tests, Ende-zu-Ende-Laeufe). Ein Durchreicher verzoegert jede
// Richtung um RTT/2 und "verliert" Segmente (1448 Byte) mit Wahrscheinlichkeit
// Loss. Wie bei TCP geht dabei nichts verloren, sondern das Segment kommt erst
// nach der Reparatur an, und alles dahinter wartet (Head-of-Line-Blocking):
// schnelle Wiederholung nach etwa einer RTT, wenn genug Segmente folgen
// (doppelte ACKs), sonst Tail-Loss-Probe nach etwa zwei RTT. Rate begrenzt
// optional den Durchsatz (Engpass mit Warteschlange).
package impair

import (
	"math/rand"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// Config einer Richtung.
type Config struct {
	RTT  time.Duration // Rundlaufzeit des nachgebildeten Wegs
	Loss float64       // Anteil verlorener Segmente (0.015 = 1,5 %)
	Rate int64         // Engpass in bit/s (0 = unbegrenzt)
	Seed int64
}

// Stats zaehlt Ereignisse.
type Stats struct {
	Segments, Losses, TailLosses atomic.Uint64
	MaxStallNs                   atomic.Int64
}

// Proxy reicht Verbindungen von Listen nach Target durch.
type Proxy struct {
	ln       net.Listener
	target   string
	Up, Down Config // Up: Client -> Ziel, Down: Ziel -> Client
	Stats    Stats

	mu    sync.Mutex
	conns []net.Conn
	seed  int64
}

// Listen startet den Durchreicher.
func Listen(listen, target string, up, down Config) (*Proxy, error) {
	ln, err := net.Listen("tcp", listen)
	if err != nil {
		return nil, err
	}
	p := &Proxy{ln: ln, target: target, Up: up, Down: down, seed: up.Seed}
	go p.serve()
	return p, nil
}

// Addr liefert die Lauschadresse.
func (p *Proxy) Addr() *net.TCPAddr { return p.ln.Addr().(*net.TCPAddr) }

// Close beendet alles.
func (p *Proxy) Close() error {
	err := p.ln.Close()
	p.mu.Lock()
	for _, c := range p.conns {
		c.Close()
	}
	p.mu.Unlock()
	return err
}

func (p *Proxy) serve() {
	for {
		c, err := p.ln.Accept()
		if err != nil {
			return
		}
		go p.handle(c)
	}
}

func (p *Proxy) handle(c net.Conn) {
	u, err := net.Dial("tcp", p.target)
	if err != nil {
		c.Close()
		return
	}
	p.mu.Lock()
	p.conns = append(p.conns, c, u)
	p.seed++
	s := p.seed
	p.mu.Unlock()
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
	}
	if tc, ok := u.(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
	}
	go p.pipe(u, c, p.Up, s*2)
	p.pipe(c, u, p.Down, s*2+1)
}

type seg struct {
	b  []byte
	at time.Time
}

// pipe kopiert src nach dst mit Verzoegerung, Verlust und Reihenfolge.
// Reparatur wie Linux mit RACK-TLP (Standard seit 4.18): Ein verlorenes
// Segment gilt als verloren, sobald ein spaeteres ankommt (SACK nach einer
// RTT, Umordnungsfenster RTT/4); kommt keines, sendet die Tail-Loss-Probe
// nach 2 RTT neu, mit nur einem Segment unterwegs erst nach 2 RTT + 200 ms.
func (p *Proxy) pipe(dst, src net.Conn, cfg Config, seed int64) {
	defer dst.Close()
	defer src.Close()
	rnd := rand.New(rand.NewSource(seed))
	q := make(chan seg, 4096)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for s := range q {
			if d := time.Until(s.at); d > 0 {
				time.Sleep(d)
			}
			if _, err := dst.Write(s.b); err != nil {
				for range q {
				}
				return
			}
		}
	}()
	buf := make([]byte, 64<<10)
	const mss = 1448
	var lastDep, lastDel time.Time
	var recent []time.Time // Abgaenge der letzten RTT (Segmente unterwegs)
	var held []seg         // wartet hinter einem verlorenen Segment
	var lostAt, pto time.Time
	lost := false
	emit := func(s seg) {
		if s.at.Before(lastDel) {
			s.at = lastDel // Reihenfolge: alles wartet auf das Fehlende
		}
		lastDel = s.at
		q <- s
	}
	resolve := func(detect time.Time) {
		del := detect.Add(cfg.RTT / 2)
		if st := del.Sub(lostAt) - cfg.RTT/2; int64(st) > p.Stats.MaxStallNs.Load() {
			p.Stats.MaxStallNs.Store(int64(st))
		}
		held[0].at = del
		for _, h := range held {
			emit(h)
		}
		held, lost = held[:0], false
		_ = src.SetReadDeadline(time.Time{})
	}
	for {
		n, err := src.Read(buf)
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() && lost {
				p.Stats.TailLosses.Add(1)
				resolve(pto) // Tail-Loss-Probe
				continue
			}
		}
		for off := 0; off < n; off += mss {
			end := off + mss
			if end > n {
				end = n
			}
			b := append([]byte(nil), buf[off:end]...)
			now := time.Now()
			dep := now
			if dep.Before(lastDep) {
				dep = lastDep
			}
			if cfg.Rate > 0 {
				dep = dep.Add(time.Duration(int64(len(b)) * 8 * int64(time.Second) / cfg.Rate))
			}
			lastDep = dep
			del := dep.Add(cfg.RTT / 2)
			p.Stats.Segments.Add(1)
			cut := 0
			for cut < len(recent) && now.Sub(recent[cut]) > cfg.RTT {
				cut++
			}
			recent = append(recent[cut:], now)
			isLost := cfg.Loss > 0 && rnd.Float64() < cfg.Loss
			if isLost {
				p.Stats.Losses.Add(1)
			}
			switch {
			case lost && !isLost:
				// spaeteres Segment kommt an: RACK erkennt den Verlust
				det := dep.Add(cfg.RTT + cfg.RTT/4)
				if pto.Before(det) {
					det = pto
				}
				resolve(det)
				emit(seg{b, del})
			case lost:
				held = append(held, seg{b, del}) // weiterer Verlust dahinter
			case isLost:
				lost, lostAt = true, dep
				extra := 2 * time.Millisecond
				if len(recent) <= 1 {
					extra = 200 * time.Millisecond // nur ein Segment unterwegs
				}
				pto = dep.Add(2*cfg.RTT + extra)
				held = append(held[:0], seg{b, del})
				_ = src.SetReadDeadline(pto)
			default:
				emit(seg{b, del})
			}
		}
		if err != nil {
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			if lost {
				resolve(pto)
			}
			close(q)
			<-done
			return
		}
	}
}
