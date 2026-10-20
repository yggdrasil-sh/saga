package netx

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"io"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestClassify(t *testing.T) {
	if ClassifyTCP(0x16) != RouteTLS || ClassifyTCP(0) != RouteICETCP || ClassifyTCP(1) != RouteICETCP || ClassifyTCP('G') != RouteNone {
		t.Fatal("tcp")
	}
	cases := map[byte]UDPClass{0: UDPSTUN, 3: UDPSTUN, 17: UDPZRTP, 22: UDPDTLS, 64: UDPTURNChannel, 100: UDPQUIC, 128: UDPRTP, 191: UDPRTP, 200: UDPQUIC, 10: UDPOther}
	for b, want := range cases {
		if got := ClassifyUDP(b); got != want {
			t.Fatalf("udp %d: %d statt %d", b, got, want)
		}
	}
}

func TestProxyV2(t *testing.T) {
	src := &net.TCPAddr{IP: net.ParseIP("192.0.2.7"), Port: 40000}
	dst := &net.TCPAddr{IP: net.ParseIP("192.0.2.1"), Port: 443}
	h := BuildProxyV2(src, dst)
	r := bytes.NewReader(append(h, 0x16, 0x03))
	a, err := ReadProxyV2(r)
	if err != nil || a.String() != "192.0.2.7:40000" {
		t.Fatalf("v4: %v %v", a, err)
	}
	if rest, _ := io.ReadAll(r); !bytes.Equal(rest, []byte{0x16, 0x03}) {
		t.Fatal("zu viel gelesen")
	}
	s6 := &net.TCPAddr{IP: net.ParseIP("2001:db8::5"), Port: 1234}
	d6 := &net.TCPAddr{IP: net.ParseIP("2001:db8::1"), Port: 443}
	if a, err := ReadProxyV2(bytes.NewReader(BuildProxyV2(s6, d6))); err != nil || a.String() != "[2001:db8::5]:1234" {
		t.Fatalf("v6: %v %v", a, err)
	}
	if _, err := ReadProxyV2(bytes.NewReader([]byte("GET / HTTP/1.1\r\n\r\n"))); err == nil {
		t.Fatal("ohne kopf akzeptiert")
	}
}

func testMux(t *testing.T, pm ProxyMode) *Mux {
	crt, err := SelfSigned("localhost")
	if err != nil {
		t.Fatal(err)
	}
	c := &Certs{Mode: "self", cur: crt}
	m, err := Listen("127.0.0.1:0", c.TLSConfig(), pm)
	if err != nil {
		t.Fatal(err)
	}
	m.Start()
	t.Cleanup(func() { m.Close() })
	return m
}

func accept(t *testing.T, l *ChanListener) net.Conn {
	t.Helper()
	ch := make(chan net.Conn, 1)
	go func() { c, _ := l.Accept(); ch <- c }()
	select {
	case c := <-ch:
		return c
	case <-time.After(3 * time.Second):
		t.Fatal("keine verbindung")
	}
	return nil
}

// stunBinding ist eine minimale STUN-Binding-Anfrage (20 Byte).
var stunBinding = []byte{0, 1, 0, 0, 0x21, 0x12, 0xa4, 0x42, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}

func TestMuxRoutes(t *testing.T) {
	for _, tc := range []struct {
		mode ProxyMode
		pp   bool
	}{{ProxyOff, false}, {ProxyOn, true}, {ProxyAuto, true}, {ProxyAuto, false}} {
		pp := tc.pp
		m := testMux(t, tc.mode)
		dial := func() net.Conn {
			c, err := net.Dial("tcp", m.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			if pp {
				c.Write(BuildProxyV2(&net.TCPAddr{IP: net.ParseIP("192.0.2.9"), Port: 5555}, m.Addr()))
			}
			return c
		}
		// ICE-TCP: RFC-4571-Laenge + STUN
		c := dial()
		c.Write(append([]byte{0, 20}, stunBinding...))
		s := accept(t, m.ICE)
		buf := make([]byte, 22)
		if _, err := io.ReadFull(s, buf); err != nil || buf[1] != 20 {
			t.Fatalf("ice-tcp: %v %v", buf, err)
		}
		if pp && s.RemoteAddr().String() != "192.0.2.9:5555" || !pp && s.RemoteAddr().String() == "192.0.2.9:5555" {
			t.Fatalf("proxy-adresse %v", s.RemoteAddr())
		}
		c.Close()
		// TLS + STUN = TURN
		tlc := tls.Client(dial(), &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"})
		go tlc.Write(stunBinding)
		s = accept(t, m.TURN)
		buf = make([]byte, 20)
		if _, err := io.ReadFull(s, buf); err != nil || !IsSTUN(buf) {
			t.Fatalf("turn: %v", err)
		}
		if pp && s.RemoteAddr().String() != "192.0.2.9:5555" {
			t.Fatalf("turn proxy-adresse %v", s.RemoteAddr())
		}
		tlc.Close()
		// TLS + HTTP
		hc := tls.Client(dial(), &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"})
		go hc.Write([]byte("GET / HTTP/1.1\r\nHost: x\r\n\r\n"))
		s = accept(t, m.HTTP)
		buf = make([]byte, 3)
		if _, err := io.ReadFull(s, buf); err != nil || string(buf) != "GET" {
			t.Fatalf("http: %q %v", buf, err)
		}
		hc.Close()
	}
}

func TestProxyRequired(t *testing.T) {
	m := testMux(t, ProxyOn)
	c, _ := net.Dial("tcp", m.Addr().String())
	c.Write(append([]byte{0, 20}, stunBinding...))
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	if _, err := c.Read(make([]byte, 1)); err == nil {
		t.Fatal("verbindung ohne proxy-kopf nicht geschlossen")
	}
	if m.Stats.ProxyErr.Load() != 1 {
		t.Fatal("zaehler")
	}
}

func writePair(t *testing.T, dir, name string) []byte {
	t.Helper()
	crt, err := SelfSigned(name)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: crt.Certificate[0]})
	kb, _ := x509.MarshalPKCS8PrivateKey(crt.PrivateKey)
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: kb})
	if err := os.WriteFile(filepath.Join(dir, "fullchain.pem"), certPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "privkey.pem"), keyPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	return crt.Certificate[0]
}

func TestCertFilesReload(t *testing.T) {
	dir := t.TempDir()
	first := writePair(t, dir, "probe.example.org")
	c, err := NewCerts(CertOptions{Name: "probe.example.org", CertFile: filepath.Join(dir, "fullchain.pem"), KeyFile: filepath.Join(dir, "privkey.pem")})
	if err != nil || c.Mode != "file" {
		t.Fatalf("modus %v %v", c, err)
	}
	get := func() []byte {
		crt, err := c.TLSConfig().GetCertificate(&tls.ClientHelloInfo{ServerName: "probe.example.org"})
		if err != nil {
			t.Fatal(err)
		}
		return crt.Certificate[0]
	}
	if !bytes.Equal(get(), first) {
		t.Fatal("erstes zertifikat")
	}
	stop := make(chan struct{})
	defer close(stop)
	go c.Watch(20*time.Millisecond, stop)
	time.Sleep(30 * time.Millisecond)
	second := writePair(t, dir, "probe.example.org")
	future := time.Now().Add(2 * time.Second)
	_ = os.Chtimes(filepath.Join(dir, "fullchain.pem"), future, future)
	for i := 0; i < 100 && !bytes.Equal(get(), second); i++ {
		time.Sleep(20 * time.Millisecond)
	}
	if !bytes.Equal(get(), second) {
		t.Fatal("datei-aenderung nicht neu geladen")
	}
	// SIGHUP-Pfad: Reload direkt
	third := writePair(t, dir, "probe.example.org")
	if err := c.Reload(); err != nil || !bytes.Equal(get(), third) {
		t.Fatalf("reload: %v", err)
	}
	// ohne Dateien und ohne Name: Fehler statt stiller Annahme
	if _, err := NewCerts(CertOptions{Name: "localhost"}); err == nil {
		t.Fatal("kein fehler ohne zertifikat")
	}
}

func TestCoalesce(t *testing.T) {
	a, b := net.Pipe()
	cc := NewCoalesceConn(a)
	cc.Enable()
	var want []byte
	done := make(chan []byte)
	go func() {
		got := make([]byte, 0, 1<<20)
		buf := make([]byte, 4096)
		for len(got) < 300*100 {
			n, err := b.Read(buf)
			if err != nil {
				break
			}
			got = append(got, buf[:n]...)
		}
		done <- got
	}()
	for i := 0; i < 300; i++ {
		p := bytes.Repeat([]byte{byte(i)}, 100)
		want = append(want, p...)
		if _, err := cc.Write(p); err != nil {
			t.Fatal(err)
		}
		if i%7 == 0 {
			cc.Flush()
		}
	}
	cc.Flush()
	got := <-done
	if !bytes.Equal(got, want) {
		t.Fatal("reihenfolge/inhalt")
	}
	w, f := cc.Stats()
	if f >= w {
		t.Fatalf("nicht gebuendelt: %d schreibvorgaenge, %d leerungen", w, f)
	}
	// Frist: ohne Flush kommt es spaetestens nach CoalesceDelay an
	go cc.Write([]byte("z"))
	b.SetReadDeadline(time.Now().Add(time.Second))
	one := make([]byte, 1)
	if _, err := b.Read(one); err != nil || one[0] != 'z' {
		t.Fatalf("frist: %v", err)
	}
	cc.Close()
}
