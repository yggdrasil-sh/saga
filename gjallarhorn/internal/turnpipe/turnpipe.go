// Package turnpipe koppelt auf dem TURN-Weg die ICE-Sockets der Beine und die Relay-Sockets des eigenen TURN-Servers
// im Prozess (2.5, turntls).
//
// Ohne Kopplung schreibt ein Bein per Loopback-UDP an den Relay-Socket seiner Allocation; der TURN-Server liest dort und
// schreibt in die TCP/TLS-Verbindung zum Geraet. Staut diese Verbindung (enger Downlink), fuellt sich der
// Empfangspuffer des Relay-Sockets (Linux-Standard ~208 KiB, bei 600 kbit/s rund 2,8 s), waehrend das Bein nie
// blockiert: Seine Warteschlange bleibt jung, H3 verwirft nichts, die Schaetzung sinkt nicht.
//
// Mit Kopplung bleiben die Sockets (Adressen, Ports, Schutz "nur zum Knoten selbst") wie sie sind. Ein Paket zwischen
// einem ICE-Ende und einem Relay-Ende desselben Hubs geht aber nicht durch den Kernel, sondern in die Warteschlange des
// Empfaengers im Prozess:
//   - Richtung Geraet (ICE -> Relay): Die Warteschlange des Relay-Endes ist klein (Limit). Ist sie voll, wartet der
//     Schreiber, bis der TURN-Server weiterliest - also bis die TCP/TLS-Verbindung zum Geraet Platz hat
//     (TCP_NOTSENT_LOWAT, Buendler). Der Gegendruck kommt damit wie auf ICE-TCP im Bein an.
//   - Richtung Knoten (Relay -> ICE): nie warten (der TURN-Server liest dabei die Verbindung vom Geraet), ueber der
//     Grenze des Endes (wie ein Empfangspuffer) wird verworfen.
//
// STUN (ICE-Pruefungen und ihre Antworten) wartet nie; es darf ueber das Limit hinaus, damit der Lesezweig des
// ICE-Agenten nicht hinter einem Stau haengt.
package turnpipe

import (
	"errors"
	"net"
	"net/netip"
	"os"
	"sync"
	"sync/atomic"
	"time"
)

const (
	// DefaultLimit: Bytes in der Warteschlange eines Relay-Endes, ab denen ein Schreiber wartet. Klein, damit die
	// Warteschlange im Bein bleibt (dort misst die Schaetzung ihr Alter und H3 verwirft ganze Rahmen); der TURN-Server
	// liest ohnehin sofort weiter in den Buendler der TLS-Verbindung.
	DefaultLimit = 4 << 10
	// DefaultMaxWait: so lange wartet ein Schreiber hoechstens, dann wird das Paket verworfen - Schutz gegen eine
	// haengende Verbindung. Normal wartet er weit kuerzer (Buendler und TCP_NOTSENT_LOWAT leeren sich bei 600 kbit/s
	// in ~0,4 s); laenger als die Rahmen-Grenze des Beins (H3, 150 ms) wartet ohnehin nur, wer schon verwirft.
	DefaultMaxWait = 2 * time.Second
	// Cap: Grenze jedes Endes (Ersatz fuer den Empfangspuffer des Kernels), darueber wird verworfen.
	Cap = 256 << 10
	// ctlExtra: so weit darf STUN ueber das Limit eines Relay-Endes hinaus, ohne zu warten.
	ctlExtra = 16 << 10
	bufSize  = 2048
)

// Stats: Zaehler eines Hubs.
type Stats struct {
	Pkts, Bytes   atomic.Uint64 // im Prozess zugestellt
	Waits, WaitNs atomic.Uint64 // Schreibvorgaenge, die auf Platz warten mussten (Gegendruck der TURN-Verbindung)
	Timeouts      atomic.Uint64 // ... und nach MaxWait verworfen wurden
	Dropped       atomic.Uint64 // verworfen: Ziel voll bzw. geschlossen
	Kernel        atomic.Uint64 // nicht im Prozess (Ziel kein Gegenstueck dieses Hubs): ueber den echten Socket
}

// StatsView: Zaehler als JSON.
type StatsView struct {
	Ends     int    `json:"ends"`
	Pkts     uint64 `json:"pkts"`
	Bytes    uint64 `json:"bytes"`
	Waits    uint64 `json:"waits"`
	WaitMs   uint64 `json:"wait_ms"`
	Timeouts uint64 `json:"timeouts"`
	Dropped  uint64 `json:"dropped"`
	Kernel   uint64 `json:"kernel"`
	Limit    int    `json:"limit"`
}

// Hub verbindet die Enden eines TURN-Wegs (ICE-Sockets der Beine, Relay-Sockets der Allocations).
type Hub struct {
	Limit   int           // siehe DefaultLimit
	MaxWait time.Duration // siehe DefaultMaxWait
	Stats   Stats

	mu   sync.RWMutex
	ends map[netip.AddrPort]*Conn
}

// NewHub: Hub mit Standardwerten.
func NewHub() *Hub {
	return &Hub{Limit: DefaultLimit, MaxWait: DefaultMaxWait, ends: map[netip.AddrPort]*Conn{}}
}

// View: Zaehler fuer die Ausgabe.
func (h *Hub) View() StatsView {
	h.mu.RLock()
	n := len(h.ends)
	h.mu.RUnlock()
	return StatsView{Ends: n, Pkts: h.Stats.Pkts.Load(), Bytes: h.Stats.Bytes.Load(), Waits: h.Stats.Waits.Load(),
		WaitMs: h.Stats.WaitNs.Load() / 1e6, Timeouts: h.Stats.Timeouts.Load(), Dropped: h.Stats.Dropped.Load(),
		Kernel: h.Stats.Kernel.Load(), Limit: h.Limit}
}

// Kind eines Endes.
type Kind uint8

const (
	ICE   Kind = iota + 1 // Socket eines ICE-Agenten (Bein): Eingang verwirft, wenn voll
	Relay                 // Relay-Socket einer Allocation: Eingang laesst Schreiber warten, wenn voll
)

// Attach meldet c als Ende an und gibt die gekoppelte Huelle zurueck (sie gehoert ab jetzt dem Aufrufer; Close
// schliesst auch c). kernel schreibt an Ziele, die kein Gegenstueck dieses Hubs sind (nil = direkt ueber c).
func (h *Hub) Attach(c *net.UDPConn, k Kind, kernel func([]byte, net.Addr) (int, error)) *Conn {
	ap := addrPortOf(c.LocalAddr())
	e := &Conn{UDPConn: c, h: h, kind: k, key: ap, kernel: kernel,
		la:   &net.UDPAddr{IP: ap.Addr().AsSlice(), Port: int(ap.Port()), Zone: ap.Addr().Zone()},
		done: make(chan struct{}), ready: make(chan struct{}, 1), space: make(chan struct{}, 1), rdlCh: make(chan struct{}, 1)}
	if ap.IsValid() {
		h.mu.Lock()
		h.ends[ap] = e
		h.mu.Unlock()
	}
	go e.pump()
	return e
}

func (h *Hub) lookup(ap netip.AddrPort) *Conn {
	h.mu.RLock()
	e := h.ends[ap]
	h.mu.RUnlock()
	return e
}

func (h *Hub) remove(e *Conn) {
	h.mu.Lock()
	if h.ends[e.key] == e {
		delete(h.ends, e.key)
	}
	h.mu.Unlock()
}

func addrPortOf(a net.Addr) netip.AddrPort {
	var ap netip.AddrPort
	switch v := a.(type) {
	case *net.UDPAddr:
		if v == nil {
			return ap
		}
		ap = v.AddrPort()
	case nil:
		return ap
	default:
		ap, _ = netip.ParseAddrPort(a.String())
	}
	return netip.AddrPortFrom(ap.Addr().Unmap(), ap.Port())
}

var bufPool = sync.Pool{New: func() any { b := make([]byte, bufSize); return &b }}

func getBuf(n int) *[]byte {
	if n > bufSize {
		b := make([]byte, n)
		return &b
	}
	return bufPool.Get().(*[]byte)
}

func putBuf(b *[]byte) {
	if cap(*b) == bufSize {
		bufPool.Put(b)
	}
}

type pkt struct {
	buf  *[]byte
	n    int
	from *net.UDPAddr // Absender (geteilt, nicht aendern); nil = aus ap bilden
	ap   netip.AddrPort
}

// Conn ist ein gekoppeltes Ende: ein echter UDP-Socket (Adresse, Port, Verkehr zu fremden Zielen) und eine
// Warteschlange fuer Pakete aus dem Prozess. Erfuellt net.PacketConn, transport.UDPConn (pion) und den
// AddrPort-Weg von pion/ice.
type Conn struct {
	*net.UDPConn
	h      *Hub
	kind   Kind
	key    netip.AddrPort
	la     *net.UDPAddr
	kernel func([]byte, net.Addr) (int, error)
	wdl    atomic.Int64 // Schreibfrist (UnixNano, 0 = keine)

	mu     sync.Mutex
	q      []pkt // FIFO ab head
	head   int
	qb     int
	closed bool
	rdl    time.Time
	once   sync.Once

	done  chan struct{}
	ready chan struct{} // Lesende wecken (ein Zeichen genuegt, siehe recv)
	space chan struct{} // Wartende Schreiber wecken
	rdlCh chan struct{} // Lesefrist geaendert
}

func signal(c chan struct{}) {
	select {
	case c <- struct{}{}:
	default:
	}
}

// Kind des Endes.
func (c *Conn) Kind() Kind { return c.kind }

// Queued: Bytes in der Warteschlange (Tests, Diagnose).
func (c *Conn) Queued() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.qb
}

// pump liest den echten Socket (Verkehr von ausserhalb des Hubs) in dieselbe Warteschlange.
func (c *Conn) pump() {
	for {
		bp := getBuf(bufSize)
		n, ap, err := c.UDPConn.ReadFromUDPAddrPort(*bp)
		if err != nil {
			putBuf(bp)
			if errors.Is(err, net.ErrClosed) || c.isClosed() {
				return
			}
			time.Sleep(time.Millisecond)
			continue
		}
		ap = netip.AddrPortFrom(ap.Addr().Unmap(), ap.Port())
		c.mu.Lock()
		if c.closed || (c.qb > 0 && c.qb+n > Cap) {
			c.mu.Unlock()
			putBuf(bp)
			c.h.Stats.Dropped.Add(1)
			continue
		}
		c.pushLocked(pkt{buf: bp, n: n, ap: ap})
		c.mu.Unlock()
	}
}

func (c *Conn) pushLocked(p pkt) {
	c.q = append(c.q, p)
	c.qb += p.n
	signal(c.ready)
}

func (c *Conn) isClosed() bool {
	select {
	case <-c.done:
		return true
	default:
		return false
	}
}

// --- Schreiben ---

// WriteTo schreibt ein Paket: an ein Gegenstueck dieses Hubs im Prozess, sonst ueber den echten Socket.
func (c *Conn) WriteTo(b []byte, a net.Addr) (int, error) {
	return c.write(b, addrPortOf(a), a)
}

// WriteToUDP wie WriteTo.
func (c *Conn) WriteToUDP(b []byte, a *net.UDPAddr) (int, error) {
	if a == nil {
		return c.write(b, netip.AddrPort{}, nil)
	}
	return c.write(b, addrPortOf(a), a)
}

// WriteToAddrPort wie WriteTo (pion/ice ohne Allokation).
func (c *Conn) WriteToAddrPort(b []byte, ap netip.AddrPort) (int, error) {
	return c.write(b, netip.AddrPortFrom(ap.Addr().Unmap(), ap.Port()), nil)
}

// WriteToUDPAddrPort wie WriteToAddrPort.
func (c *Conn) WriteToUDPAddrPort(b []byte, ap netip.AddrPort) (int, error) {
	return c.WriteToAddrPort(b, ap)
}

// WriteMsgUDP: ohne Zusatzdaten wie WriteTo.
func (c *Conn) WriteMsgUDP(b, oob []byte, a *net.UDPAddr) (n, oobn int, err error) {
	if len(oob) > 0 || a == nil {
		return c.UDPConn.WriteMsgUDP(b, oob, a)
	}
	n, err = c.WriteToUDP(b, a)
	return n, 0, err
}

func (c *Conn) write(b []byte, ap netip.AddrPort, a net.Addr) (int, error) {
	if c.isClosed() {
		return 0, net.ErrClosed
	}
	if d := c.h.lookup(ap); d != nil && d.kind != c.kind {
		return c.h.deliver(c, d, b)
	}
	// Kein Gegenstueck: wie ohne Kopplung (Relay-Enden: Schutz des Aufrufers, siehe Attach)
	c.h.Stats.Kernel.Add(1)
	if a == nil {
		if !ap.IsValid() {
			return 0, errors.New("turnpipe: keine Zieladresse")
		}
		a = net.UDPAddrFromAddrPort(ap)
	}
	if c.kernel != nil {
		return c.kernel(b, a)
	}
	return c.UDPConn.WriteTo(b, a)
}

// deliver legt b in die Warteschlange von d; wartet nur am Eingang eines Relay-Endes (Richtung Geraet).
func (h *Hub) deliver(src, d *Conn, b []byte) (int, error) {
	n := len(b)
	ctl := n > 0 && b[0] < 4 // STUN (RFC 7983)
	var t *time.Timer
	var t0 time.Time
	stop := func() {
		if t != nil {
			t.Stop()
			h.Stats.Waits.Add(1)
			h.Stats.WaitNs.Add(uint64(time.Since(t0)))
		}
	}
	d.mu.Lock()
	for {
		if d.closed {
			d.mu.Unlock()
			stop()
			h.Stats.Dropped.Add(1)
			return n, nil
		}
		lim := Cap
		if d.kind == Relay {
			lim = h.Limit
			if ctl {
				lim += ctlExtra
			}
		}
		if d.qb == 0 || d.qb+n <= lim {
			break
		}
		if d.kind != Relay || ctl {
			d.mu.Unlock()
			stop()
			h.Stats.Dropped.Add(1)
			return n, nil
		}
		d.mu.Unlock()
		if t == nil {
			t0 = time.Now()
			wait := h.MaxWait
			if dl := src.wdl.Load(); dl != 0 {
				if w := time.Until(time.Unix(0, dl)); w < wait {
					wait = w
				}
			}
			if wait <= 0 {
				return 0, os.ErrDeadlineExceeded
			}
			t = time.NewTimer(wait)
		}
		select {
		case <-d.space:
		case <-d.done:
		case <-src.done:
			stop()
			return 0, net.ErrClosed
		case <-t.C:
			h.Stats.Waits.Add(1)
			h.Stats.WaitNs.Add(uint64(time.Since(t0)))
			if dl := src.wdl.Load(); dl != 0 && time.Now().UnixNano() >= dl {
				return 0, os.ErrDeadlineExceeded
			}
			h.Stats.Timeouts.Add(1)
			return n, nil // verworfen wie ein volles UDP-Ziel
		}
		d.mu.Lock()
	}
	bp := getBuf(n)
	copy(*bp, b)
	d.pushLocked(pkt{buf: bp, n: n, from: src.la, ap: src.key})
	if d.qb < h.Limit {
		signal(d.space) // Platz fuer den naechsten Wartenden
	}
	d.mu.Unlock()
	stop()
	h.Stats.Pkts.Add(1)
	h.Stats.Bytes.Add(uint64(n))
	return n, nil
}

// --- Lesen ---

func (c *Conn) recv(b []byte) (int, pkt, error) {
	c.mu.Lock()
	for c.head == len(c.q) {
		if c.closed {
			c.mu.Unlock()
			return 0, pkt{}, net.ErrClosed
		}
		var tc <-chan time.Time
		var t *time.Timer
		if !c.rdl.IsZero() {
			w := time.Until(c.rdl)
			if w <= 0 {
				c.mu.Unlock()
				return 0, pkt{}, os.ErrDeadlineExceeded
			}
			t = time.NewTimer(w)
			tc = t.C
		}
		c.mu.Unlock()
		select {
		case <-c.ready:
		case <-c.done:
		case <-c.rdlCh:
		case <-tc:
		}
		if t != nil {
			t.Stop()
		}
		c.mu.Lock()
	}
	p := c.q[c.head]
	c.q[c.head] = pkt{}
	c.head++
	if c.head == len(c.q) {
		c.q, c.head = c.q[:0], 0
	} else {
		signal(c.ready) // weitere Lesende
		if c.head > 64 && c.head*2 > len(c.q) {
			k := copy(c.q, c.q[c.head:])
			clear(c.q[k:])
			c.q, c.head = c.q[:k], 0
		}
	}
	c.qb -= p.n
	signal(c.space)
	c.mu.Unlock()
	n := copy(b, (*p.buf)[:p.n])
	putBuf(p.buf)
	p.buf = nil
	return n, p, nil
}

func (p pkt) addr() *net.UDPAddr {
	if p.from != nil {
		return p.from
	}
	return net.UDPAddrFromAddrPort(p.ap)
}

// ReadFrom liest das naechste Paket (aus dem Prozess oder vom echten Socket).
func (c *Conn) ReadFrom(b []byte) (int, net.Addr, error) {
	n, p, err := c.recv(b)
	if err != nil {
		return 0, nil, err
	}
	return n, p.addr(), nil
}

// ReadFromUDP wie ReadFrom.
func (c *Conn) ReadFromUDP(b []byte) (int, *net.UDPAddr, error) {
	n, p, err := c.recv(b)
	if err != nil {
		return 0, nil, err
	}
	return n, p.addr(), nil
}

// ReadFromAddrPort wie ReadFrom (pion/ice ohne Allokation).
func (c *Conn) ReadFromAddrPort(b []byte) (int, netip.AddrPort, error) {
	n, p, err := c.recv(b)
	return n, p.ap, err
}

// ReadFromUDPAddrPort wie ReadFromAddrPort.
func (c *Conn) ReadFromUDPAddrPort(b []byte) (int, netip.AddrPort, error) {
	return c.ReadFromAddrPort(b)
}

// ReadMsgUDP: ohne Zusatzdaten.
func (c *Conn) ReadMsgUDP(b, _ []byte) (n, oobn, flags int, addr *net.UDPAddr, err error) {
	n, addr, err = c.ReadFromUDP(b)
	return n, 0, 0, addr, err
}

// Read wie ReadFrom ohne Adresse.
func (c *Conn) Read(b []byte) (int, error) {
	n, _, err := c.recv(b)
	return n, err
}

// --- Fristen, Schliessen ---

// SetDeadline setzt Lese- und Schreibfrist (nur fuer die Huelle; der echte Socket liest ohne Frist weiter).
func (c *Conn) SetDeadline(t time.Time) error {
	_ = c.SetWriteDeadline(t)
	return c.SetReadDeadline(t)
}

// SetReadDeadline wie bei net.UDPConn.
func (c *Conn) SetReadDeadline(t time.Time) error {
	c.mu.Lock()
	c.rdl = t
	c.mu.Unlock()
	signal(c.rdlCh)
	return nil
}

// SetWriteDeadline gilt fuer das Warten im Prozess und fuer den echten Socket.
func (c *Conn) SetWriteDeadline(t time.Time) error {
	if t.IsZero() {
		c.wdl.Store(0)
	} else {
		c.wdl.Store(t.UnixNano())
	}
	return c.UDPConn.SetWriteDeadline(t)
}

// Close meldet das Ende ab, weckt Wartende und schliesst den echten Socket.
func (c *Conn) Close() error {
	var err error = net.ErrClosed
	c.once.Do(func() {
		c.h.remove(c)
		c.mu.Lock()
		c.closed = true
		for i := c.head; i < len(c.q); i++ {
			putBuf(c.q[i].buf)
		}
		c.q, c.head, c.qb = nil, 0, 0
		c.mu.Unlock()
		close(c.done)
		err = c.UDPConn.Close()
	})
	return err
}
