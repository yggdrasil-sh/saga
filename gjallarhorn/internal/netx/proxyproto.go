package netx

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"net"
)

var ppSig = []byte("\r\n\r\n\x00\r\nQUIT\n")

// ErrNoProxyHeader: Verbindung ohne gueltigen PROXY-v2-Kopf.
var ErrNoProxyHeader = errors.New("kein PROXY-v2-Kopf")

// ReadProxyV2 liest genau einen PROXY-Protocol-v2-Kopf (HAProxy-Spezifikation)
// und liefert die Client-Adresse. Bei LOCAL (z. B. Gesundheitspruefung des
// Proxys) ist addr nil. Es wird nicht mehr gelesen als der Kopf lang ist.
func ReadProxyV2(r io.Reader) (addr net.Addr, err error) {
	var h [16]byte
	if _, err = io.ReadFull(r, h[:]); err != nil {
		return nil, err
	}
	if !bytes.Equal(h[:12], ppSig) || h[12]>>4 != 2 {
		return nil, ErrNoProxyHeader
	}
	cmd := h[12] & 0x0f
	fam := h[13]
	n := int(binary.BigEndian.Uint16(h[14:]))
	if n > 1024 {
		return nil, ErrNoProxyHeader
	}
	var buf [1024]byte
	body := buf[:n]
	if _, err = io.ReadFull(r, body); err != nil {
		return nil, err
	}
	if cmd == 0 { // LOCAL
		return nil, nil
	}
	if cmd != 1 {
		return nil, ErrNoProxyHeader
	}
	switch fam {
	case 0x11, 0x12: // TCP/UDP ueber IPv4
		if n < 12 {
			return nil, ErrNoProxyHeader
		}
		ip := net.IP(append([]byte(nil), body[0:4]...))
		return &net.TCPAddr{IP: ip, Port: int(binary.BigEndian.Uint16(body[8:]))}, nil
	case 0x21, 0x22: // IPv6
		if n < 36 {
			return nil, ErrNoProxyHeader
		}
		ip := net.IP(append([]byte(nil), body[0:16]...))
		return &net.TCPAddr{IP: ip, Port: int(binary.BigEndian.Uint16(body[32:]))}, nil
	default: // UNSPEC/Unix: Adresse unbekannt
		return nil, nil
	}
}

// BuildProxyV2 erzeugt einen PROXY-v2-Kopf (fuer Tests und den Mini-Proxy).
func BuildProxyV2(src, dst *net.TCPAddr) []byte {
	b := append([]byte(nil), ppSig...)
	b = append(b, 0x21)
	s4, d4 := src.IP.To4(), dst.IP.To4()
	if s4 != nil && d4 != nil {
		b = append(b, 0x11, 0, 12)
		b = append(b, s4...)
		b = append(b, d4...)
	} else {
		b = append(b, 0x21, 0, 36)
		b = append(b, src.IP.To16()...)
		b = append(b, dst.IP.To16()...)
	}
	b = binary.BigEndian.AppendUint16(b, uint16(src.Port))
	b = binary.BigEndian.AppendUint16(b, uint16(dst.Port))
	return b
}
