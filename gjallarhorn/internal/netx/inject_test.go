package netx

import (
	"crypto/tls"
	"io"
	"net"
	"testing"
	"time"
)

// TestInjectMux (2.5 "Ein Port"): Verbindungen kommen von saga-media mit schon gelesenen Bytes und echter Gegenadresse.
func TestInjectMux(t *testing.T) {
	crt, err := SelfSigned("localhost")
	if err != nil {
		t.Fatal(err)
	}
	c := &Certs{Mode: "self", cur: crt}
	m := NewInjectMux(&net.TCPAddr{IP: net.IPv4zero, Port: 443}, c.TLSConfig())
	m.Start()
	defer m.Close()
	if m.Addr().Port != 443 {
		t.Fatalf("Addr %v", m.Addr())
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	real := &net.TCPAddr{IP: net.ParseIP("203.0.113.50"), Port: 6000}
	// pair: Client-Verbindung und angenommene Gegenseite; die ersten n Byte liest "saga-media" vor der Uebergabe
	pair := func(write func(net.Conn), n int) {
		cl, err := net.Dial("tcp", ln.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { cl.Close() })
		sv, err := ln.Accept()
		if err != nil {
			t.Fatal(err)
		}
		go write(cl)
		pre := make([]byte, n)
		if _, err := io.ReadFull(sv, pre); err != nil {
			t.Fatal(err)
		}
		m.Inject(sv, pre, real)
	}
	// ICE-TCP: 1 Byte vorab gelesen
	pair(func(c net.Conn) { c.Write(append([]byte{0, 20}, stunBinding...)) }, 1)
	s := accept(t, m.ICE)
	buf := make([]byte, 22)
	if _, err := io.ReadFull(s, buf); err != nil || buf[1] != 20 || s.RemoteAddr().String() != real.String() {
		t.Fatalf("ice-tcp: %v %v %v", buf, err, s.RemoteAddr())
	}
	// TURN-TLS: der ganze erste Datensatz vorab gelesen (wie die Weiche in saga-media)
	pair(func(c net.Conn) {
		tlc := tls.Client(c, &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"})
		tlc.Write(stunBinding)
	}, 5)
	s = accept(t, m.TURN)
	buf = make([]byte, 20)
	_ = s.SetReadDeadline(time.Now().Add(3 * time.Second))
	if _, err := io.ReadFull(s, buf); err != nil || !IsSTUN(buf) || s.RemoteAddr().String() != real.String() {
		t.Fatalf("turn: %v %v", err, s.RemoteAddr())
	}
	// HTTP nach TLS (Client ohne ALPN): an m.HTTP
	pair(func(c net.Conn) {
		hc := tls.Client(c, &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"})
		hc.Write([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))
	}, 3)
	s = accept(t, m.HTTP)
	buf = make([]byte, 3)
	if _, err := io.ReadFull(s, buf); err != nil || string(buf) != "GET" || s.RemoteAddr().String() != real.String() {
		t.Fatalf("http: %q %v", buf, err)
	}
	// PROXY-Kopf wird bei uebergebenen Verbindungen nie gelesen (auch nicht mit ProxyOn)
	m.Proxy = ProxyOn
	pair(func(c net.Conn) { c.Write(append([]byte{0, 20}, stunBinding...)) }, 2)
	s = accept(t, m.ICE)
	if s.RemoteAddr().String() != real.String() {
		t.Fatalf("proxy on: %v", s.RemoteAddr())
	}
	// Klartext: abgewiesen
	before := m.Stats.Rejected.Load()
	pair(func(c net.Conn) { c.Write([]byte("GET / HTTP/1.1\r\n\r\n")) }, 1)
	for i := 0; i < 100 && m.Stats.Rejected.Load() == before; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if m.Stats.Rejected.Load() != before+1 {
		t.Fatalf("klartext nicht abgewiesen")
	}
}
