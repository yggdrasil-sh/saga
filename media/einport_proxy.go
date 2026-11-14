package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"net"
)

// PROXY-Protokoll v2 (HAProxy-Spezifikation) fuer den TLS-Eingang hinter einer L4-Durchreiche (2.5 "Ein Port"). Nur v2,
// nur mit TLS_PROXY_PROTOCOL=1, dann aber Pflicht vor jeder Verbindung: Ohne Kopf saehe Saga alle Clients mit der
// Adresse des Proxys (Grenzen je Adresse, Anmelde-Sperren, Sitzungsliste). Gleiche Regeln wie gjallarhorn
// internal/netx/proxyproto.go ReadProxyV2.

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
