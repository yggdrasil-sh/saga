package netx

import (
	"net"
	"net/netip"
	"sync/atomic"
)

// UDPClass ist die Art eines UDP-Pakets nach RFC 9443.
type UDPClass uint8

const (
	UDPOther       UDPClass = iota
	UDPSTUN                 // 0..3
	UDPZRTP                 // 16..19
	UDPDTLS                 // 20..63
	UDPTURNChannel          // 64..79
	UDPQUIC                 // 80..127 und 192..255
	UDPRTP                  // 128..191 (RTP/RTCP)
)

// ClassifyUDP ordnet das erste Byte nach RFC 9443 zu.
func ClassifyUDP(b byte) UDPClass {
	switch {
	case b <= 3:
		return UDPSTUN
	case b >= 16 && b <= 19:
		return UDPZRTP
	case b >= 20 && b <= 63:
		return UDPDTLS
	case b >= 64 && b <= 79:
		return UDPTURNChannel
	case b >= 128 && b <= 191:
		return UDPRTP
	case (b >= 80 && b <= 127) || b >= 192:
		return UDPQUIC
	}
	return UDPOther
}

// WebRTCPacketConn reicht nur STUN, DTLS und RTP/RTCP an pion weiter. QUIC
// (spaeter Bifroest) und Unbekanntes wird gezaehlt und verworfen.
// Liest direkt in den Puffer des Aufrufers (keine Allokation).
type WebRTCPacketConn struct {
	*net.UDPConn
	Dropped atomic.Uint64
}

// ReadFrom liefert das naechste WebRTC-Paket.
func (c *WebRTCPacketConn) ReadFrom(b []byte) (int, net.Addr, error) {
	for {
		n, a, err := c.UDPConn.ReadFrom(b)
		if err != nil || n == 0 {
			return n, a, err
		}
		switch ClassifyUDP(b[0]) {
		case UDPSTUN, UDPDTLS, UDPRTP:
			return n, a, nil
		}
		c.Dropped.Add(1)
	}
}

// ReadFromAddrPort ist der allokationsfreie Lesepfad fuer pion/ice.
func (c *WebRTCPacketConn) ReadFromAddrPort(b []byte) (int, netip.AddrPort, error) {
	for {
		n, a, err := c.UDPConn.ReadFromUDPAddrPort(b)
		if err != nil || n == 0 {
			return n, a, err
		}
		switch ClassifyUDP(b[0]) {
		case UDPSTUN, UDPDTLS, UDPRTP:
			return n, a, nil
		}
		c.Dropped.Add(1)
	}
}

// WriteToAddrPort ist der allokationsfreie Schreibpfad fuer pion/ice.
func (c *WebRTCPacketConn) WriteToAddrPort(b []byte, a netip.AddrPort) (int, error) {
	return c.UDPConn.WriteToUDPAddrPort(b, a)
}
