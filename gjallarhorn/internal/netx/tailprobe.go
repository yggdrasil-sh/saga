package netx

import (
	"encoding/binary"
	"hash/crc32"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// Nachlaeufer gegen lange TCP-Staus: Geht das letzte Segment eines Schubs
// verloren, erkennt Linux (RACK-TLP) das erst ueber die Tail-Loss-Probe nach
// 2 RTT, mit nur einem Segment unterwegs nach 2 RTT + 200 ms. Ein winziges
// Segment kurz nach dem Schub (STUN Binding Indication, 28 Byte, wird von
// ICE-Gegenstellen als Keepalive behandelt und von TURN-Clients ignoriert)
// laesst RACK den Verlust schon nach etwa 1,25 RTT erkennen. Folgen ohnehin
// Daten, entfaellt der Nachlaeufer.

// TailProbeDelay: Abstand des Nachlaeufers nach dem letzten Schreiben
// (0 = aus). HORN_TAILPROBE_MS.
var TailProbeDelay = 10 * time.Millisecond

// TailProbes zaehlt gesendete Nachlaeufer.
var TailProbes atomic.Uint64

// stunIndication ist eine STUN Binding Indication mit FINGERPRINT.
var stunIndication = func() []byte {
	b := make([]byte, 28)
	binary.BigEndian.PutUint16(b[0:], 0x0011)
	binary.BigEndian.PutUint16(b[2:], 8)
	copy(b[4:8], stunMagic[:])
	copy(b[8:20], "horn-tail-pr")
	binary.BigEndian.PutUint16(b[20:], 0x8028)
	binary.BigEndian.PutUint16(b[22:], 4)
	binary.BigEndian.PutUint32(b[24:], crc32.ChecksumIEEE(b[:20])^0x5354554e)
	return b
}()

// TailFrameICE ist der Nachlaeufer im RFC-4571-Rahmen (ICE-TCP).
var TailFrameICE = append([]byte{0, byte(len(stunIndication))}, stunIndication...)

// TailFrameTURN ist der Nachlaeufer im TURN-Strom (STUN-Nachrichten ohne
// Laengenpraefix).
var TailFrameTURN = stunIndication

// EnableTailProbe schaltet den Nachlaeufer fuer eine gebuendelte
// Verbindung ein (nur im gebuendelten Modus).
func (c *CoalesceConn) EnableTailProbe(frame []byte, delay time.Duration) {
	if delay <= 0 {
		return
	}
	c.mu.Lock()
	c.probe, c.probeDelay = frame, delay
	if c.ptimer == nil {
		c.ptimer = time.AfterFunc(time.Hour, c.onProbe)
		c.ptimer.Stop()
	}
	c.mu.Unlock()
}

func (c *CoalesceConn) onProbe() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.pArmed || c.err != nil || len(c.buf) > 0 || c.flushing {
		c.pArmed = false
		return
	}
	c.pArmed = false
	c.flushing = true
	c.mu.Unlock()
	_, err := c.Conn.Write(c.probe)
	c.mu.Lock()
	c.flushing = false
	if err != nil && c.err == nil {
		c.err = err
	}
	TailProbes.Add(1)
	c.cond.Broadcast()
}

// ProbeConn ergaenzt eine Verbindung (TURN ueber TLS) um den Nachlaeufer:
// nach jedem Schreiben ohne Folgedaten innerhalb von Delay ein Frame.
type ProbeConn struct {
	net.Conn
	Frame []byte
	Delay time.Duration

	mu    sync.Mutex
	t     *time.Timer
	armed bool
}

// NewProbeConn umhuellt c.
func NewProbeConn(c net.Conn, frame []byte, delay time.Duration) *ProbeConn {
	p := &ProbeConn{Conn: c, Frame: frame, Delay: delay}
	p.t = time.AfterFunc(time.Hour, p.fire)
	p.t.Stop()
	return p
}

// Write schreibt b und plant den Nachlaeufer.
func (p *ProbeConn) Write(b []byte) (int, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	n, err := p.Conn.Write(b)
	if err == nil {
		p.armed = true
		p.t.Reset(p.Delay)
	}
	return n, err
}

func (p *ProbeConn) fire() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if !p.armed {
		return
	}
	p.armed = false
	if _, err := p.Conn.Write(p.Frame); err == nil {
		TailProbes.Add(1)
	}
}

// Close stoppt den Wecker.
func (p *ProbeConn) Close() error {
	p.mu.Lock()
	p.armed = false
	p.t.Stop()
	p.mu.Unlock()
	return p.Conn.Close()
}
