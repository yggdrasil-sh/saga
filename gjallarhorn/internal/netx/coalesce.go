package netx

import (
	"net"
	"sync"
	"time"
)

// CoalesceConn sammelt kleine Schreibvorgaenge (je RTP-Paket einer) und gibt
// sie gebuendelt an den Kernel: ein Systemaufruf je Schub statt je Paket.
// Geleert wird bei Flush (der Weiterleiter ruft es, wenn seine Warteschlange
// leer ist), ab HighWater sofort und sonst spaetestens nach Delay.
// Der Puffer ist klein (MaxBuffer), damit die Warteschlange im Knoten bleibt
// und dort Rahmen verworfen werden koennen (H2/H3).
type CoalesceConn struct {
	net.Conn

	mu       sync.Mutex
	cond     *sync.Cond
	buf      []byte
	spare    []byte
	flushing bool
	armed    bool
	err      error
	timer    *time.Timer

	on             bool // erst nach Enable wird gebuendelt
	highWater, max int
	delay          time.Duration

	// Nachlaeufer (tailprobe.go)
	probe      []byte
	probeDelay time.Duration
	ptimer     *time.Timer
	pArmed     bool

	Flushes, Writes uint64 // nur unter mu
}

// Standardwerte: 8 KB sofort leeren, hoechstens 16 KB puffern, 2 ms Frist.
const (
	CoalesceHigh  = 8 << 10
	CoalesceMax   = 16 << 10
	CoalesceDelay = 2 * time.Millisecond
)

// NewCoalesceConn umhuellt c.
func NewCoalesceConn(c net.Conn) *CoalesceConn {
	cc := &CoalesceConn{Conn: c, highWater: CoalesceHigh, max: CoalesceMax, delay: CoalesceDelay,
		buf: make([]byte, 0, CoalesceMax+2048), spare: make([]byte, 0, CoalesceMax+2048)}
	cc.cond = sync.NewCond(&cc.mu)
	cc.timer = time.AfterFunc(time.Hour, cc.onTimer)
	cc.timer.Stop()
	return cc
}

// Enable schaltet das Buendeln ein (vorher wird direkt geschrieben).
func (c *CoalesceConn) Enable() {
	c.mu.Lock()
	c.on = true
	c.mu.Unlock()
}

// Write puffert b. Blockiert, wenn der Puffer voll ist (Gegendruck).
func (c *CoalesceConn) Write(b []byte) (int, error) {
	c.mu.Lock()
	if !c.on {
		c.mu.Unlock()
		return c.Conn.Write(b)
	}
	defer c.mu.Unlock()
	for c.err == nil && len(c.buf) > 0 && len(c.buf)+len(b) > c.max {
		if !c.flushing {
			c.flushLocked()
			continue
		}
		c.cond.Wait()
	}
	if c.err != nil {
		return 0, c.err
	}
	if c.pArmed { // Daten folgen: kein Nachlaeufer noetig
		c.pArmed = false
		c.ptimer.Stop()
	}
	c.buf = append(c.buf, b...)
	c.Writes++
	if len(c.buf) >= c.highWater {
		c.flushLocked()
	} else if !c.armed {
		c.armed = true
		c.timer.Reset(c.delay)
	}
	return len(b), c.err
}

// Flush schreibt den Puffer sofort.
func (c *CoalesceConn) Flush() {
	c.mu.Lock()
	c.flushLocked()
	c.mu.Unlock()
}

func (c *CoalesceConn) onTimer() {
	c.mu.Lock()
	c.armed = false
	c.flushLocked()
	c.mu.Unlock()
}

// flushLocked: mu gehalten; waehrend des Schreibens wird mu freigegeben.
func (c *CoalesceConn) flushLocked() {
	for c.flushing {
		c.cond.Wait()
	}
	if len(c.buf) == 0 || c.err != nil {
		return
	}
	data := c.buf
	c.buf = c.spare[:0]
	c.flushing = true
	c.Flushes++
	if c.armed { // Frist nicht mehr noetig: kein Wecker-Lauf je Paket
		c.armed = false
		c.timer.Stop()
	}
	c.mu.Unlock()
	_, err := c.Conn.Write(data)
	c.mu.Lock()
	c.flushing = false
	c.spare = data[:0]
	if err != nil && c.err == nil {
		c.err = err
	}
	if c.probe != nil && err == nil && len(c.buf) == 0 {
		c.pArmed = true
		c.ptimer.Reset(c.probeDelay)
	}
	c.cond.Broadcast()
}

// Close leert den Puffer und schliesst.
func (c *CoalesceConn) Close() error {
	c.Flush()
	c.timer.Stop()
	c.mu.Lock()
	if c.ptimer != nil {
		c.pArmed = false
		c.ptimer.Stop()
	}
	c.mu.Unlock()
	c.mu.Lock()
	if c.err == nil {
		c.err = net.ErrClosed
	}
	c.cond.Broadcast()
	c.mu.Unlock()
	return c.Conn.Close()
}

// Stats liefert Schreib- und Leerungszaehler.
func (c *CoalesceConn) Stats() (writes, flushes uint64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.Writes, c.Flushes
}
