package main

import (
	"bytes"
	"crypto/tls"
	"net"
	"testing"
	"time"
)

// Fuzz-Ziele fuer die Parser des TLS-Eingangs (pruef-einport). Normale Laeufe pruefen nur die Startwerte; lange Laeufe:
// go test -run x -fuzz FuzzEinportProxy -fuzztime 60s (bzw. FuzzEinportHello).

func FuzzEinportProxy(f *testing.F) {
	src, dst := &net.TCPAddr{IP: net.ParseIP("203.0.113.7"), Port: 4711}, &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443}
	f.Add(buildProxyV2(src, dst))
	f.Add(buildProxyV2(&net.TCPAddr{IP: net.ParseIP("2001:db8::1"), Port: 1}, &net.TCPAddr{IP: net.ParseIP("2001:db8::2"), Port: 2}))
	f.Add([]byte("PROXY TCP4 198.51.100.20 10.0.0.1 5555 443\r\n"))
	f.Add([]byte("PROXY UNKNOWN\r\n"))
	f.Add(append(append([]byte(nil), ppSig...), 0x20, 0, 0, 0))
	f.Fuzz(func(t *testing.T, b []byte) {
		e := &edge{}
		p := &peekBuf{c: &helloConn{r: bytes.NewReader(b)}}
		a, err := e.readProxy(p)
		if err != nil {
			return
		}
		if a != nil && (a.IP == nil || a.Port < 0 || a.Port > 65535) {
			t.Fatalf("Adresse %v", a)
		}
		if len(p.b) > len(b) {
			t.Fatalf("mehr Bytes als gelesen")
		}
	})
}

func FuzzEinportHello(f *testing.F) {
	for _, alpn := range [][]string{nil, {"h2", "http/1.1"}, {"stun.turn"}, {"acme-tls/1"}} {
		f.Add(helloSeed(alpn))
	}
	f.Add([]byte{0x16, 3, 1, 0, 4, 1, 0, 0, 0})
	f.Fuzz(func(t *testing.T, b []byte) {
		e := &edge{}
		p := &peekBuf{c: &helloConn{r: bytes.NewReader(b)}}
		t0 := time.Now()
		_, _ = e.readHello(p)
		if len(p.b) > maxHello+4096 {
			t.Fatalf("Puffer %d", len(p.b))
		}
		if time.Since(t0) > 2*time.Second {
			t.Fatalf("zu langsam")
		}
	})
}

// helloSeed: ClientHello eines Go-Clients (ohne Test-Helfer, die *testing.T brauchen).
func helloSeed(alpn []string) []byte {
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	go clientHello(a, alpn)
	_ = b.SetReadDeadline(time.Now().Add(2 * time.Second))
	p := &peekBuf{c: b}
	if _, err := (&edge{}).readHello(p); err != nil {
		return nil
	}
	return p.b
}

func clientHello(c net.Conn, alpn []string) {
	_ = tls.Client(c, &tls.Config{ServerName: testName, NextProtos: alpn, InsecureSkipVerify: true}).Handshake()
}
