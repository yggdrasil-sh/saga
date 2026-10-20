package netx

import (
	"encoding/binary"
	"errors"
	"io"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"time"
)

// ICEMux ist eine allokationsfreie Alternative zu pions TCPMuxDefault
// (ice.TCPMux): Die Weiche reicht ICE-TCP-Verbindungen mit ihrem ufrag
// direkt herein (Add), Pakete laufen ueber einen Puffer-Pool, Rahmen nach
// RFC 4571 werden in einem wiederverwendeten Puffer je Verbindung gebaut,
// Gegenadressen sind zwischengespeichert. pions Variante allokiert je Paket
// zweimal und formatiert je Schreibvorgang die Adresse als Text.
type ICEMux struct {
	addr *net.TCPAddr

	// Unclaimed: so lange darf eine Verbindung ohne Bein (unbekannter ufrag)
	// offen bleiben.
	Unclaimed time.Duration

	mu     sync.Mutex
	pcs    map[iceKey]*ICEPacketConn
	closed bool

	Stats struct{ Conns, Unclaimed, Oversize atomic.Uint64 }
}

type iceKey struct {
	ufrag string
	v6    bool
}

// NewICEMux legt die Weiche fuer die lokale Adresse addr an.
func NewICEMux(addr *net.TCPAddr) *ICEMux {
	return &ICEMux{addr: addr, pcs: map[iceKey]*ICEPacketConn{}, Unclaimed: 15 * time.Second}
}

// GetConnByUfrag liefert (bzw. erzeugt) die PacketConn fuer ufrag; jede
// Anfrage zaehlt eine Referenz, Close gibt sie zurueck (wie pion).
func (m *ICEMux) GetConnByUfrag(ufrag string, isIPv6 bool, local net.IP) (net.PacketConn, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return nil, io.ErrClosedPipe
	}
	pc := m.getLocked(ufrag, isIPv6, local)
	pc.claim()
	return &iceShared{ICEPacketConn: pc}, nil
}

func (m *ICEMux) getLocked(ufrag string, v6 bool, local net.IP) *ICEPacketConn {
	k := iceKey{ufrag, v6}
	if pc := m.pcs[k]; pc != nil {
		return pc
	}
	la := *m.addr
	if local != nil {
		la.IP = local
	}
	pc := &ICEPacketConn{mux: m, key: k, local: &la, rx: make(chan *icePkt, 512), closed: make(chan struct{})}
	m.pcs[k] = pc
	return pc
}

// RemoveConnByUfrag schliesst alle Verbindungen zu ufrag.
func (m *ICEMux) RemoveConnByUfrag(ufrag string) {
	m.mu.Lock()
	var ps []*ICEPacketConn
	for _, v6 := range []bool{false, true} {
		if pc := m.pcs[iceKey{ufrag, v6}]; pc != nil {
			ps = append(ps, pc)
		}
	}
	m.mu.Unlock()
	for _, pc := range ps {
		pc.close()
	}
}

// Close schliesst alles.
func (m *ICEMux) Close() error {
	m.mu.Lock()
	m.closed = true
	ps := make([]*ICEPacketConn, 0, len(m.pcs))
	for _, pc := range m.pcs {
		ps = append(ps, pc)
	}
	m.mu.Unlock()
	for _, pc := range ps {
		pc.close()
	}
	return nil
}

// Add nimmt eine ICE-TCP-Verbindung der Weiche an (erste Nachricht noch
// ungelesen im Puffer von c).
func (m *ICEMux) Add(ufrag string, c net.Conn) {
	ra, _ := c.RemoteAddr().(*net.TCPAddr)
	if ufrag == "" || ra == nil {
		c.Close()
		return
	}
	ap := ra.AddrPort()
	v6 := !ap.Addr().Unmap().Is4()
	la, _ := c.LocalAddr().(*net.TCPAddr)
	var lip net.IP
	if la != nil {
		lip = la.IP
	}
	m.mu.Lock()
	if m.closed {
		m.mu.Unlock()
		c.Close()
		return
	}
	pc := m.getLocked(ufrag, v6, lip)
	m.mu.Unlock()
	m.Stats.Conns.Add(1)
	ic := &iceConn{c: c, raddr: &net.TCPAddr{IP: ra.IP, Port: ra.Port, Zone: ra.Zone}, ap: ap}
	if !pc.add(ic) {
		c.Close()
		return
	}
	if !pc.claimed() {
		// unbekannter ufrag: nur kurz offen halten
		time.AfterFunc(m.Unclaimed, func() {
			if !pc.claimed() {
				m.Stats.Unclaimed.Add(1)
				pc.close()
			}
		})
	}
	go pc.readLoop(ic)
}

func (m *ICEMux) forget(pc *ICEPacketConn) {
	m.mu.Lock()
	if m.pcs[pc.key] == pc {
		delete(m.pcs, pc.key)
	}
	m.mu.Unlock()
}

// Paketpuffer des Lesepfads.
const icePktSize = 2048

type icePkt struct {
	buf  [icePktSize]byte
	big  []byte // fuer seltene groessere Rahmen
	n    int
	from *iceConn
}

var icePool = sync.Pool{New: func() any { return new(icePkt) }}

type iceConn struct {
	c     net.Conn
	raddr *net.TCPAddr
	ap    netip.AddrPort
	wmu   sync.Mutex
	wbuf  [2 + 1600]byte
}

// ICEPacketConn sind alle ICE-TCP-Verbindungen eines ufrag (eine Familie).
type ICEPacketConn struct {
	mux   *ICEMux
	key   iceKey
	local net.Addr

	mu    sync.Mutex
	conns []*iceConn
	refs  int
	ever  bool

	rx     chan *icePkt
	closed chan struct{}
	once   sync.Once
}

func (p *ICEPacketConn) claim() {
	p.mu.Lock()
	p.refs++
	p.ever = true
	p.mu.Unlock()
}

func (p *ICEPacketConn) claimed() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.ever
}

func (p *ICEPacketConn) add(ic *iceConn) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	select {
	case <-p.closed:
		return false
	default:
	}
	p.conns = append(p.conns, ic)
	return true
}

func (p *ICEPacketConn) remove(ic *iceConn) {
	p.mu.Lock()
	for i, x := range p.conns {
		if x == ic {
			p.conns = append(p.conns[:i], p.conns[i+1:]...)
			break
		}
	}
	p.mu.Unlock()
	ic.c.Close()
}

func (p *ICEPacketConn) readLoop(ic *iceConn) {
	defer p.remove(ic)
	var hdr [2]byte
	for {
		if _, err := io.ReadFull(ic.c, hdr[:]); err != nil {
			return
		}
		n := int(binary.BigEndian.Uint16(hdr[:]))
		pk := icePool.Get().(*icePkt)
		b := pk.buf[:]
		if n > len(b) {
			p.mux.Stats.Oversize.Add(1)
			if cap(pk.big) < n {
				pk.big = make([]byte, n)
			}
			b = pk.big[:n]
		}
		if _, err := io.ReadFull(ic.c, b[:n]); err != nil {
			icePool.Put(pk)
			return
		}
		pk.n, pk.from = n, ic
		select {
		case p.rx <- pk:
		case <-p.closed:
			icePool.Put(pk)
			return
		}
	}
}

// ReadFrom liefert das naechste Paket irgendeiner Verbindung des ufrag.
func (p *ICEPacketConn) ReadFrom(b []byte) (int, net.Addr, error) {
	select {
	case pk := <-p.rx:
		src := pk.buf[:]
		if pk.n > len(src) {
			src = pk.big
		}
		want := pk.n
		n := copy(b, src[:want])
		a := pk.from.raddr
		pk.from = nil
		icePool.Put(pk)
		if n < want {
			return n, a, io.ErrShortBuffer
		}
		return n, a, nil
	case <-p.closed:
		return 0, nil, io.EOF
	}
}

// WriteTo schreibt einen RFC-4571-Rahmen an die Verbindung zu addr.
func (p *ICEPacketConn) WriteTo(b []byte, addr net.Addr) (int, error) {
	ic := p.find(addr)
	if ic == nil {
		return 0, io.ErrClosedPipe
	}
	if len(b) > 0xffff {
		return 0, errors.New("ice-tcp: paket zu gross")
	}
	ic.wmu.Lock()
	defer ic.wmu.Unlock()
	var out []byte
	if len(b)+2 <= len(ic.wbuf) {
		out = ic.wbuf[:2+len(b)]
	} else {
		out = make([]byte, 2+len(b))
	}
	binary.BigEndian.PutUint16(out, uint16(len(b)))
	copy(out[2:], b)
	if _, err := ic.c.Write(out); err != nil {
		return 0, err
	}
	return len(b), nil
}

func (p *ICEPacketConn) find(addr net.Addr) *iceConn {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, ic := range p.conns { // pion gibt die gelesene Adresse zurueck
		if ic.raddr == addr {
			return ic
		}
	}
	ta, ok := addr.(*net.TCPAddr)
	if !ok {
		return nil
	}
	ip, ok := netip.AddrFromSlice(ta.IP)
	if !ok {
		return nil
	}
	want := netip.AddrPortFrom(ip.Unmap(), uint16(ta.Port))
	for _, ic := range p.conns {
		if ic.ap.Addr().Unmap() == want.Addr() && ic.ap.Port() == want.Port() {
			return ic
		}
	}
	return nil
}

func (p *ICEPacketConn) close() {
	p.once.Do(func() {
		close(p.closed)
		p.mux.forget(p)
		p.mu.Lock()
		cs := append([]*iceConn(nil), p.conns...)
		p.conns = nil
		p.mu.Unlock()
		for _, ic := range cs {
			ic.c.Close()
		}
		for {
			select {
			case pk := <-p.rx:
				icePool.Put(pk)
			default:
				return
			}
		}
	})
}

// LocalAddr ist die Adresse der Weiche.
func (p *ICEPacketConn) LocalAddr() net.Addr { return p.local }

// Close der gemeinsamen Verbindung geht ueber iceShared.
func (p *ICEPacketConn) Close() error { p.close(); return nil }

// Fristen werden von pion fuer TCP nicht gebraucht.
func (p *ICEPacketConn) SetDeadline(time.Time) error      { return nil }
func (p *ICEPacketConn) SetReadDeadline(time.Time) error  { return nil }
func (p *ICEPacketConn) SetWriteDeadline(time.Time) error { return nil }

// iceShared zaehlt Referenzen (eine je GetConnByUfrag).
type iceShared struct {
	*ICEPacketConn
	once sync.Once
}

func (s *iceShared) Close() error {
	s.once.Do(func() {
		p := s.ICEPacketConn
		p.mu.Lock()
		p.refs--
		last := p.refs <= 0
		p.mu.Unlock()
		if last {
			p.close()
		}
	})
	return nil
}
