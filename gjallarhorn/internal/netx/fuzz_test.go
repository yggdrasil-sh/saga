package netx

import (
	"bufio"
	"bytes"
	"net"
	"net/netip"
	"testing"
	"time"
)

// FuzzProxyHeader: PROXY v1/v2 mit beliebigen Bytes, nie mehr lesen als der
// Kopf lang ist und nie Panik.
func FuzzProxyHeader(f *testing.F) {
	src := &net.TCPAddr{IP: net.ParseIP("192.0.2.7"), Port: 40000}
	dst := &net.TCPAddr{IP: net.ParseIP("192.0.2.1"), Port: 443}
	f.Add(BuildProxyV2(src, dst))
	f.Add(BuildProxyV1(src, dst))
	f.Add([]byte("PROXY UNKNOWN\r\n"))
	f.Add([]byte("PROXY TCP6 2001:db8::1 2001:db8::2 1 2\r\n"))
	f.Fuzz(func(t *testing.T, b []byte) {
		br := bufio.NewReader(bytes.NewReader(append(b, 0x16)))
		a, err := ReadProxy(br)
		if err == nil && a != nil {
			if _, ok := a.(*net.TCPAddr); !ok {
				t.Fatal("adresstyp")
			}
		}
	})
}

// FuzzSTUN: Ufrag-Suche, einfache Bindings und Antworten.
func FuzzSTUN(f *testing.F) {
	f.Add(stunBinding)
	f.Add([]byte{0, 1, 0, 8, 0x21, 0x12, 0xa4, 0x42, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 0, 6, 0, 3, 'a', ':', 'b', 0})
	f.Add(AppendBindingSuccess(nil, stunBinding, netip.MustParseAddrPort("[2001:db8::7]:443")))
	f.Fuzz(func(t *testing.T, b []byte) {
		_ = stunUsernameLocal(b)
		if len(b) > 0 {
			_ = ClassifyUDP(b[0])
		}
		if len(b) < 1600 {
			l := []byte{byte(len(b) >> 8), byte(len(b))}
			_ = peekUfrag(bufio.NewReader(bytes.NewReader(append(l, b...))))
		}
		if IsPlainBinding(b) {
			ap := netip.MustParseAddrPort("192.0.2.5:3478")
			r := AppendBindingSuccess(nil, b, ap)
			if back, ok := ParseXorMapped(r); !ok || back != ap {
				t.Fatalf("antwort %v", back)
			}
		}
		_, _ = ParseXorMapped(b)
	})
}

// FuzzWeiche: beliebige erste Bytes einer TCP-Verbindung (mit allen
// PROXY-Modi). Die Weiche darf nie haengen bleiben oder abstuerzen.
func FuzzWeiche(f *testing.F) {
	f.Add(byte(0), append([]byte{0, 20}, stunBinding...))
	f.Add(byte(2), append(BuildProxyV2(&net.TCPAddr{IP: net.ParseIP("192.0.2.9"), Port: 1}, &net.TCPAddr{IP: net.ParseIP("192.0.2.1"), Port: 443}), 0x16, 3, 1))
	f.Add(byte(1), []byte("PROXY TCP4 192.0.2.9 192.0.2.1 1 443\r\n\x00\x14"))
	f.Add(byte(0), []byte{0x16, 3, 1, 0, 5, 1, 0, 0, 1, 0})
	f.Add(byte(0), []byte("GET / HTTP/1.1\r\n\r\n"))
	crt, err := SelfSigned("localhost")
	if err != nil {
		f.Fatal(err)
	}
	c := &Certs{Mode: "self", cur: crt}
	ofb, ohs := FirstByteTimeout, HandshakeTimeout
	FirstByteTimeout, HandshakeTimeout = 100*time.Millisecond, 100*time.Millisecond
	f.Cleanup(func() { FirstByteTimeout, HandshakeTimeout = ofb, ohs })
	f.Fuzz(func(t *testing.T, mode byte, b []byte) {
		addr := &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 443}
		m := &Mux{TLS: c.TLSConfig(), Proxy: ProxyMode(mode % 3), ICE: newChanListener(addr), HTTP: newChanListener(addr), TURN: newChanListener(addr)}
		defer m.ICE.Close()
		defer m.HTTP.Close()
		defer m.TURN.Close()
		cli, srv := net.Pipe()
		m.Stats.Pending.Add(1)
		done := make(chan struct{})
		go func() { m.handle(srv); close(done) }()
		go func() {
			_ = cli.SetWriteDeadline(time.Now().Add(200 * time.Millisecond))
			_, _ = cli.Write(b)
			time.Sleep(5 * time.Millisecond)
			cli.Close()
		}()
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			t.Fatal("weiche haengt")
		}
		for _, l := range []*ChanListener{m.ICE, m.HTTP, m.TURN} {
			select {
			case x := <-l.ch:
				x.Close()
			default:
			}
		}
		if m.Stats.Pending.Load() != 0 {
			t.Fatalf("pending %d", m.Stats.Pending.Load())
		}
	})
}
