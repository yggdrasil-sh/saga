package netx

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"strconv"
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

// ReadProxy liest einen PROXY-Kopf in Version 2 (binaer) oder 1 (Text,
// "PROXY TCP4 ..."), je nach erstem Byte.
func ReadProxy(br *bufio.Reader) (net.Addr, error) {
	b, err := br.Peek(1)
	if err != nil {
		return nil, err
	}
	if b[0] == 'P' {
		return ReadProxyV1(br)
	}
	return ReadProxyV2(br)
}

// IsProxyStart meldet, ob b der Anfang eines PROXY-Kopfs sein kann (v1 'P',
// v2 '\r'); beides ist weder TLS (0x16) noch ICE-TCP (0x00/0x01).
func IsProxyStart(b byte) bool { return b == ppSig[0] || b == 'P' }

// ReadProxyV1 liest einen PROXY-v1-Kopf (hoechstens 107 Byte bis CRLF).
func ReadProxyV1(br *bufio.Reader) (net.Addr, error) {
	var line [108]byte
	n := 0
	for {
		c, err := br.ReadByte()
		if err != nil {
			return nil, err
		}
		if n >= len(line) {
			return nil, ErrNoProxyHeader
		}
		line[n] = c
		n++
		if c == '\n' {
			break
		}
	}
	return parseProxyV1(line[:n])
}

func parseProxyV1(l []byte) (net.Addr, error) {
	if len(l) < 8 || !bytes.HasPrefix(l, []byte("PROXY ")) || !bytes.HasSuffix(l, []byte("\r\n")) {
		return nil, ErrNoProxyHeader
	}
	f := bytes.Split(l[6:len(l)-2], []byte(" "))
	if len(f) >= 1 && string(f[0]) == "UNKNOWN" {
		return nil, nil
	}
	if len(f) != 5 || (string(f[0]) != "TCP4" && string(f[0]) != "TCP6") {
		return nil, ErrNoProxyHeader
	}
	ip := net.ParseIP(string(f[1]))
	if ip == nil || (string(f[0]) == "TCP4") != (ip.To4() != nil) || net.ParseIP(string(f[2])) == nil {
		return nil, ErrNoProxyHeader
	}
	port, ok := atoiPort(f[3])
	if _, ok2 := atoiPort(f[4]); !ok || !ok2 {
		return nil, ErrNoProxyHeader
	}
	return &net.TCPAddr{IP: ip, Port: port}, nil
}

func atoiPort(b []byte) (int, bool) {
	if len(b) == 0 || len(b) > 5 || (len(b) > 1 && b[0] == '0') {
		return 0, false
	}
	n := 0
	for _, c := range b {
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	return n, n <= 65535
}

// BuildProxyV1 erzeugt einen PROXY-v1-Kopf (fuer Tests).
func BuildProxyV1(src, dst *net.TCPAddr) []byte {
	fam := "TCP4"
	if src.IP.To4() == nil {
		fam = "TCP6"
	}
	return []byte("PROXY " + fam + " " + src.IP.String() + " " + dst.IP.String() + " " +
		strconv.Itoa(src.Port) + " " + strconv.Itoa(dst.Port) + "\r\n")
}
