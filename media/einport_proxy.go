package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"net"
	"strconv"
	"strings"
)

// PROXY-Protokoll (HAProxy-Spezifikation) fuer den TLS-Eingang hinter einer L4-Durchreiche (2.5 "Ein Port"). Nur mit
// TLS_PROXY_PROTOCOL=1, dann aber Pflicht vor jeder Verbindung: Ohne Kopf saehe Saga alle Clients mit der Adresse des
// Proxys (Grenzen je Adresse, Anmelde-Sperren). v2 (binaer: NetBird, Traefik, HAProxy send-proxy-v2, caddy-l4) und v1
// (Text: nginx stream "proxy_protocol on" kann nur v1). Gleiche Regeln wie gjallarhorn internal/netx/proxyproto.go.

var ppSig = []byte("\r\n\r\n\x00\r\nQUIT\n")

var errProxyHeader = errors.New("kein gueltiger PROXY-v2-Kopf")

// proxyNeed: wie viele Byte der Kopf braucht (0 = noch unbekannt, weniger als 16 Byte da), Fehler bei falscher Signatur.
func proxyNeed(b []byte) (int, error) {
	n := len(b)
	if n > len(ppSig) {
		n = len(ppSig)
	}
	if !bytes.Equal(b[:n], ppSig[:n]) {
		return 0, errProxyHeader
	}
	if len(b) < 16 {
		return 0, nil
	}
	if b[12]>>4 != 2 {
		return 0, errProxyHeader
	}
	l := int(binary.BigEndian.Uint16(b[14:16]))
	if l > 1024 {
		return 0, errProxyHeader
	}
	return 16 + l, nil
}

// parseProxyV2: vollstaendiger Kopf (genau proxyNeed Byte) -> Client-Adresse; nil bei LOCAL (Gesundheitspruefung des
// Proxys) bzw. unbekannter Familie (UNSPEC/Unix): dann gilt die Adresse der Gegenstelle.
func parseProxyV2(b []byte) (*net.TCPAddr, error) {
	cmd, fam := b[12]&0x0f, b[13]
	body := b[16:]
	switch cmd {
	case 0: // LOCAL
		return nil, nil
	case 1: // PROXY
	default:
		return nil, errProxyHeader
	}
	switch fam {
	case 0x11: // TCP ueber IPv4
		if len(body) < 12 {
			return nil, errProxyHeader
		}
		return &net.TCPAddr{IP: net.IP(append([]byte(nil), body[0:4]...)), Port: int(binary.BigEndian.Uint16(body[8:10]))}, nil
	case 0x21: // TCP ueber IPv6
		if len(body) < 36 {
			return nil, errProxyHeader
		}
		return &net.TCPAddr{IP: net.IP(append([]byte(nil), body[0:16]...)), Port: int(binary.BigEndian.Uint16(body[32:34]))}, nil
	case 0x12, 0x22: // UDP ueber TCP-Eingang: unsinnig
		return nil, errProxyHeader
	}
	return nil, nil
}

// proxyV1Need: Laenge des v1-Kopfs bis einschliesslich CRLF (0 = noch nicht vollstaendig), Fehler ab 108 Byte ohne Ende.
func proxyV1Need(b []byte) (int, error) {
	n := len(b)
	if n > 6 {
		n = 6
	}
	if !bytes.Equal(b[:n], []byte("PROXY ")[:n]) {
		return 0, errProxyHeader
	}
	if i := bytes.IndexByte(b, '\n'); i >= 0 {
		if i > 106 {
			return 0, errProxyHeader
		}
		return i + 1, nil
	}
	if len(b) >= 107 {
		return 0, errProxyHeader
	}
	return 0, nil
}

// parseProxyV1: "PROXY TCP4|TCP6 src dst sport dport\r\n" bzw. "PROXY UNKNOWN ...\r\n" (dann nil).
func parseProxyV1(l []byte) (*net.TCPAddr, error) {
	if !bytes.HasSuffix(l, []byte("\r\n")) {
		return nil, errProxyHeader
	}
	f := strings.Split(string(l[6:len(l)-2]), " ")
	if len(f) >= 1 && f[0] == "UNKNOWN" {
		return nil, nil
	}
	if len(f) != 5 || (f[0] != "TCP4" && f[0] != "TCP6") {
		return nil, errProxyHeader
	}
	ip, dst := net.ParseIP(f[1]), net.ParseIP(f[2])
	if ip == nil || dst == nil || (f[0] == "TCP4") != (ip.To4() != nil) {
		return nil, errProxyHeader
	}
	sp, err1 := strconv.Atoi(f[3])
	_, err2 := strconv.Atoi(f[4])
	if err1 != nil || err2 != nil || sp < 0 || sp > 65535 || len(f[3]) > 5 || (len(f[3]) > 1 && f[3][0] == '0') {
		return nil, errProxyHeader
	}
	return &net.TCPAddr{IP: ip, Port: sp}, nil
}

// buildProxyV2 erzeugt einen Kopf (Tests).
func buildProxyV2(src, dst *net.TCPAddr) []byte {
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
	return binary.BigEndian.AppendUint16(b, uint16(dst.Port))
}
