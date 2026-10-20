package netx

import (
	"bufio"
	"crypto/tls"
	"errors"
	"log"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

// STUN-Magic-Cookie (RFC 5389), Bytes 4..7 jeder STUN-Nachricht.
var stunMagic = [4]byte{0x21, 0x12, 0xa4, 0x42}

// Route ist das Ziel einer Verbindung auf dem gemeinsamen TCP-Port.
type Route uint8

const (
	RouteNone   Route = iota
	RouteICETCP       // RFC 4571 + STUN: an pion TCPMux
	RouteTLS          // TLS: danach HTTP oder TURN
)

// ClassifyTCP entscheidet nach dem ersten Byte (Konzept §1.1).
func ClassifyTCP(b byte) Route {
	switch b {
	case 0x16:
		return RouteTLS
	case 0x00, 0x01:
		return RouteICETCP
	}
	return RouteNone
}

// IsSTUN prueft, ob b mit einer STUN-Nachricht beginnt.
func IsSTUN(b []byte) bool {
	return len(b) >= 8 && b[0]&0xc0 == 0 && b[4] == stunMagic[0] && b[5] == stunMagic[1] && b[6] == stunMagic[2] && b[7] == stunMagic[3]
}

// ChanListener ist ein net.Listener, der Verbindungen aus der Weiche erhaelt.
type ChanListener struct {
	ch     chan net.Conn
	addr   net.Addr
	closed chan struct{}
	once   sync.Once
}

func newChanListener(addr net.Addr) *ChanListener {
	return &ChanListener{ch: make(chan net.Conn, 64), addr: addr, closed: make(chan struct{})}
}

// Accept wartet auf die naechste Verbindung.
func (l *ChanListener) Accept() (net.Conn, error) {
	select {
	case c := <-l.ch:
		return c, nil
	case <-l.closed:
		return nil, net.ErrClosed
	}
}

// Close schliesst den Listener.
func (l *ChanListener) Close() error {
	l.once.Do(func() { close(l.closed) })
	return nil
}

// Addr liefert die Adresse des echten Listeners.
func (l *ChanListener) Addr() net.Addr { return l.addr }

func (l *ChanListener) push(c net.Conn) {
	select {
	case l.ch <- c:
	case <-l.closed:
		c.Close()
	case <-time.After(5 * time.Second):
		c.Close()
	}
}

// bufConn liefert zuerst die schon gelesenen Bytes, dann die Verbindung.
// remote ersetzt die Gegenadresse (PROXY-Protokoll), nur im Speicher.
type bufConn struct {
	net.Conn
	r      *bufio.Reader
	remote net.Addr
}

func (c *bufConn) Read(b []byte) (int, error) { return c.r.Read(b) }

func (c *bufConn) RemoteAddr() net.Addr {
	if c.remote != nil {
		return c.remote
	}
	return c.Conn.RemoteAddr()
}

// Stats zaehlt die Weiche.
type Stats struct {
	Accepted, ICETCP, HTTP, TURN, ACME, Rejected, ProxyErr, BBR atomic.Uint64
	Busy, TLSErr                                                atomic.Uint64
	Pending                                                     atomic.Int64
}

// MaxPending begrenzt Verbindungen, die noch nicht zugeordnet sind (erstes
// Byte, PROXY-Kopf, TLS-Handshake). Darueber wird sofort geschlossen.
var MaxPending int64 = 512

// Fristen der Weiche (Standard; je Mux aenderbar).
var (
	FirstByteTimeout = 5 * time.Second
	HandshakeTimeout = 10 * time.Second
)

func (m *Mux) firstByte() time.Duration {
	if m.FirstByte > 0 {
		return m.FirstByte
	}
	return FirstByteTimeout
}

func (m *Mux) tailProbe() time.Duration {
	if m.TailProbe > 0 {
		return m.TailProbe
	}
	return TailProbeDelay
}

func (m *Mux) handshake() time.Duration {
	if m.Handshake > 0 {
		return m.Handshake
	}
	return HandshakeTimeout
}

// Mux ist die Weiche auf einem TCP-Port: TLS (HTTPS, TURN-TLS) und ICE-TCP.
type Mux struct {
	TLS         *tls.Config
	Proxy       ProxyMode
	NoCoalesce  bool          // Schreibvorgaenge nicht buendeln (HORN_COALESCE=0)
	NoTailProbe bool          // keine Nachlaeufer (HORN_TAILPROBE_MS=0)
	TailProbe   time.Duration // Abstand der Nachlaeufer (0 = TailProbeDelay)
	TailMode    TailMode      // auto (Standard) oder always

	FirstByte, Handshake time.Duration // 0 = Standard
	Stats                Stats

	// OnTURN meldet eine TURN-TLS-Verbindung mit Annahme- und Handshake-Ende
	// (Diagnose des Verbindungsaufbaus; Adresse nur im Speicher).
	OnTURN func(remote net.Addr, accepted, tlsDone time.Time, resumed bool)

	// OnICE meldet eine ICE-TCP-Verbindung mit dem lokalen ufrag, damit das
	// Bein seine Verbindung nach jedem Schub leeren kann.
	OnICE func(ufrag string, c *CoalesceConn)

	// ICEMux nimmt ICE-TCP-Verbindungen direkt an (sonst ueber ICE an pions
	// TCPMuxDefault).
	ICEMux *ICEMux

	ln              net.Listener
	ICE, HTTP, TURN *ChanListener
}

// ProxyMode steuert das PROXY-Protokoll (v1 und v2).
type ProxyMode uint8

const (
	ProxyOff  ProxyMode = iota // kein Kopf erwartet (Standard)
	ProxyOn                    // Kopf vor jeder Verbindung verlangt
	ProxyAuto                  // Kopf lesen, wenn die v2-Signatur da ist
)

// ParseProxyMode liest HORN_PROXY_PROTOCOL: 0/1/auto.
func ParseProxyMode(s string) (ProxyMode, error) {
	switch s {
	case "", "0", "off", "false":
		return ProxyOff, nil
	case "1", "on", "true":
		return ProxyOn, nil
	case "auto":
		return ProxyAuto, nil
	}
	return ProxyOff, errors.New("HORN_PROXY_PROTOCOL: 0, 1 oder auto")
}

// Listen oeffnet den Port und startet die Weiche.
func Listen(addr string, cfg *tls.Config, proxyMode ProxyMode) (*Mux, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}
	m := &Mux{TLS: cfg, Proxy: proxyMode, ln: ln}
	m.ICE = newChanListener(ln.Addr())
	m.HTTP = newChanListener(ln.Addr())
	m.TURN = newChanListener(ln.Addr())
	return m, nil
}

// Start nimmt Verbindungen an (nach dem Setzen von OnICE).
func (m *Mux) Start() { go m.serve() }

// Addr liefert die gebundene Adresse.
func (m *Mux) Addr() *net.TCPAddr { return m.ln.Addr().(*net.TCPAddr) }

// Close schliesst Port und Unter-Listener.
func (m *Mux) Close() error {
	m.ICE.Close()
	m.HTTP.Close()
	m.TURN.Close()
	return m.ln.Close()
}

func (m *Mux) serve() {
	for {
		c, err := m.ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			log.Printf("weiche: accept: %v", err)
			time.Sleep(50 * time.Millisecond)
			continue
		}
		m.Stats.Accepted.Add(1)
		if m.Stats.Pending.Add(1) > MaxPending {
			m.Stats.Pending.Add(-1)
			m.Stats.Busy.Add(1)
			c.Close()
			continue
		}
		go m.handle(c)
	}
}

func (m *Mux) handle(c net.Conn) {
	t0 := time.Now()
	pending := true
	done := func() {
		if pending {
			pending = false
			m.Stats.Pending.Add(-1)
		}
	}
	defer done()
	if tc, ok := c.(*net.TCPConn); ok && Tune(tc) {
		m.Stats.BBR.Add(1)
	}
	_ = c.SetReadDeadline(time.Now().Add(m.firstByte()))
	var remote net.Addr
	br := bufio.NewReaderSize(c, 2048)
	first, err := br.Peek(1)
	if err != nil {
		c.Close()
		return
	}
	cc := NewCoalesceConn(c)
	// Die PROXY-Koepfe beginnen mit 0x0d (v2) bzw. 'P' (v1), beides weder
	// TLS (0x16) noch ICE-TCP (0x00/0x01). Im Modus auto entscheidet daher
	// das erste Byte.
	if m.Proxy == ProxyOn || (m.Proxy == ProxyAuto && IsProxyStart(first[0])) {
		a, err := ReadProxy(br)
		if err != nil {
			m.Stats.ProxyErr.Add(1)
			c.Close()
			return
		}
		remote = a // nur im Speicher, wird nie protokolliert oder gespeichert
		if first, err = br.Peek(1); err != nil {
			c.Close()
			return
		}
	}
	switch ClassifyTCP(first[0]) {
	case RouteICETCP:
		m.Stats.ICETCP.Add(1)
		if !m.NoCoalesce {
			cc.Enable()
			if !m.NoTailProbe {
				cc.EnableTailProbe(TailFrameICE, m.tailProbe(), m.TailMode)
			}
		}
		u := peekUfrag(br)
		if m.OnICE != nil && u != "" {
			m.OnICE(u, cc)
		}
		_ = c.SetReadDeadline(time.Time{})
		done()
		if m.ICEMux != nil {
			m.ICEMux.Add(u, &bufConn{Conn: cc, r: br, remote: remote})
			return
		}
		m.ICE.push(&bufConn{Conn: cc, r: br, remote: remote})
	case RouteTLS:
		m.handleTLS(&bufConn{Conn: cc, r: br, remote: remote}, cc, done, t0)
	default:
		m.Stats.Rejected.Add(1)
		c.Close()
	}
}

func (m *Mux) handleTLS(raw *bufConn, cc *CoalesceConn, done func(), t0 time.Time) {
	tc := tls.Server(raw, m.TLS)
	_ = raw.SetDeadline(time.Now().Add(m.handshake()))
	if err := tc.Handshake(); err != nil {
		m.Stats.TLSErr.Add(1)
		tc.Close()
		return
	}
	tlsDone := time.Now()
	st := tc.ConnectionState()
	if st.NegotiatedProtocol == "acme-tls/1" { // TLS-ALPN-01: erledigt im Handshake
		m.Stats.ACME.Add(1)
		tc.Close()
		return
	}
	br := bufio.NewReaderSize(tc, 4096)
	isTURN := st.NegotiatedProtocol == "stun.turn"
	if !isTURN {
		b, err := br.Peek(8)
		if err != nil && len(b) == 0 {
			tc.Close()
			return
		}
		isTURN = IsSTUN(b)
	}
	_ = raw.SetDeadline(time.Time{})
	done()
	c := &bufConn{Conn: tc, r: br, remote: raw.remote}
	if isTURN {
		if !m.NoCoalesce {
			cc.Enable() // TLS-Datensaetze fuer TURN gebuendelt schreiben
		}
		m.Stats.TURN.Add(1)
		if m.OnTURN != nil {
			m.OnTURN(c.RemoteAddr(), t0, tlsDone, st.DidResume)
		}
		if !m.NoTailProbe && m.tailProbe() > 0 {
			tcp, _ := cc.Conn.(*net.TCPConn)
			m.TURN.push(&probeBufConn{bufConn: c, p: NewProbeConn(c, TailFrameTURN, m.tailProbe(), m.TailMode, tcp)})
			return
		}
		m.TURN.push(c)
		return
	}
	m.Stats.HTTP.Add(1)
	m.HTTP.push(c)
}

// peekUfrag liest aus der ersten RFC-4571-Nachricht (STUN-Binding) den
// lokalen ufrag (USERNAME = "lokal:entfernt"), ohne etwas zu verbrauchen.
func peekUfrag(br *bufio.Reader) string {
	h, err := br.Peek(2)
	if err != nil {
		return ""
	}
	n := int(h[0])<<8 | int(h[1])
	if n < 20 || n > 1500 {
		return ""
	}
	b, err := br.Peek(2 + n)
	if err != nil {
		return ""
	}
	return stunUsernameLocal(b[2:])
}

func stunUsernameLocal(m []byte) string {
	if !IsSTUN(m) {
		return ""
	}
	l := int(m[2])<<8 | int(m[3])
	if 20+l > len(m) {
		return ""
	}
	a := m[20 : 20+l]
	for len(a) >= 4 {
		t := int(a[0])<<8 | int(a[1])
		al := int(a[2])<<8 | int(a[3])
		if 4+al > len(a) {
			return ""
		}
		if t == 0x0006 { // USERNAME
			v := a[4 : 4+al]
			for i, ch := range v {
				if ch == ':' {
					return string(v[:i])
				}
			}
			return string(v)
		}
		p := 4 + (al+3)&^3
		if p > len(a) {
			return ""
		}
		a = a[p:]
	}
	return ""
}

// probeBufConn: Lesen ueber den Puffer, Schreiben mit Nachlaeufer.
type probeBufConn struct {
	*bufConn
	p *ProbeConn
}

func (c *probeBufConn) Write(b []byte) (int, error) { return c.p.Write(b) }
func (c *probeBufConn) Close() error                { return c.p.Close() }
