package netx

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/pem"
	"io"
	"net"
	"net/netip"
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
	// unveraenderte bzw. nur angefasste Dateien: kein neues Laden
	if ch, err := c.ReloadIfChanged(); err != nil || ch {
		t.Fatalf("unveraendert neu geladen: %v %v", ch, err)
	}
	later := time.Now().Add(4 * time.Second)
	_ = os.Chtimes(filepath.Join(dir, "privkey.pem"), later, later)
	if ch, err := c.ReloadIfChanged(); err != nil || ch {
		t.Fatalf("touch ohne inhalt neu geladen: %v %v", ch, err)
	}
	if ch, _ := c.ReloadIfChanged(); ch {
		t.Fatal("zweimal")
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

func TestProxyV1(t *testing.T) {
	src := &net.TCPAddr{IP: net.ParseIP("192.0.2.7"), Port: 40000}
	dst := &net.TCPAddr{IP: net.ParseIP("192.0.2.1"), Port: 443}
	br := bufio.NewReader(bytes.NewReader(append(BuildProxyV1(src, dst), 0x16)))
	a, err := ReadProxy(br)
	if err != nil || a.String() != "192.0.2.7:40000" {
		t.Fatalf("v1: %v %v", a, err)
	}
	if b, _ := br.ReadByte(); b != 0x16 {
		t.Fatal("zu viel gelesen")
	}
	s6 := &net.TCPAddr{IP: net.ParseIP("2001:db8::5"), Port: 1234}
	if a, err := ReadProxy(bufio.NewReader(bytes.NewReader(BuildProxyV1(s6, &net.TCPAddr{IP: net.ParseIP("2001:db8::1"), Port: 443})))); err != nil || a.String() != "[2001:db8::5]:1234" {
		t.Fatalf("v1 v6: %v %v", a, err)
	}
	if a, err := ReadProxy(bufio.NewReader(bytes.NewReader([]byte("PROXY UNKNOWN\r\n")))); err != nil || a != nil {
		t.Fatalf("unknown: %v %v", a, err)
	}
	for _, bad := range []string{"PROXY TCP4 192.0.2.1 192.0.2.2 1 99999\r\n", "PROXY TCP4 2001:db8::1 192.0.2.2 1 2\r\n", "PROXY TCP4 1.2.3.4\r\n", "PROXY TCP4 " + string(bytes.Repeat([]byte("1"), 120)) + "\r\n"} {
		if _, err := ReadProxy(bufio.NewReader(bytes.NewReader([]byte(bad)))); err == nil {
			t.Fatalf("akzeptiert: %q", bad)
		}
	}
	// Weiche im Modus auto mit v1-Kopf
	m := testMux(t, ProxyAuto)
	c, err := net.Dial("tcp", m.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.Write(BuildProxyV1(&net.TCPAddr{IP: net.ParseIP("192.0.2.9"), Port: 5555}, m.Addr()))
	c.Write(append([]byte{0, 20}, stunBinding...))
	s := accept(t, m.ICE)
	if s.RemoteAddr().String() != "192.0.2.9:5555" {
		t.Fatalf("adresse %v", s.RemoteAddr())
	}
}

func TestPendingLimit(t *testing.T) {
	old := MaxPending
	MaxPending = 2
	defer func() { MaxPending = old }()
	m := testMux(t, ProxyOff)
	var cs []net.Conn
	for i := 0; i < 4; i++ {
		c, err := net.Dial("tcp", m.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		cs = append(cs, c)
	}
	defer func() {
		for _, c := range cs {
			c.Close()
		}
	}()
	for i := 0; i < 100 && m.Stats.Busy.Load() < 2; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if m.Stats.Busy.Load() != 2 {
		t.Fatalf("busy %d", m.Stats.Busy.Load())
	}
	// nach dem Zuordnen ist wieder Platz
	cs[0].Write(append([]byte{0, 20}, stunBinding...))
	accept(t, m.ICE)
	for i := 0; i < 100 && m.Stats.Pending.Load() > 1; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if m.Stats.Pending.Load() != 1 {
		t.Fatalf("pending %d", m.Stats.Pending.Load())
	}
}

func TestUDPStunAndSelfTest(t *testing.T) {
	uc, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	pc := &WebRTCPacketConn{UDPConn: uc}
	defer pc.Close()
	got := make(chan []byte, 4)
	go func() {
		buf := make([]byte, 1500)
		for {
			n, _, err := pc.ReadFromAddrPort(buf)
			if err != nil {
				return
			}
			got <- append([]byte(nil), buf[:n]...)
		}
	}()
	target := uc.LocalAddr().(*net.UDPAddr).AddrPort()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	from, rtt, err := ProbeUDP(ctx, []netip.AddrPort{target})
	if err != nil || from != target || rtt <= 0 {
		t.Fatalf("selbsttest %v %v %v", from, rtt, err)
	}
	if pc.STUN.Answer.Load() == 0 {
		t.Fatal("keine antwort gezaehlt")
	}
	// ICE-Pruefung mit USERNAME geht an pion, nicht an den Beantworter
	ice := []byte{0, 1, 0, 8, 0x21, 0x12, 0xa4, 0x42, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 0, 6, 0, 3, 'a', ':', 'b', 0}
	if IsPlainBinding(ice) {
		t.Fatal("ice als einfach erkannt")
	}
	c, _ := net.DialUDP("udp", nil, uc.LocalAddr().(*net.UDPAddr))
	defer c.Close()
	c.Write(ice)
	select {
	case b := <-got:
		if !bytes.Equal(b, ice) {
			t.Fatal("falsches paket")
		}
	case <-time.After(time.Second):
		t.Fatal("ice-pruefung nicht weitergereicht")
	}
	// Antwort-Format (v4 und v6)
	for _, ap := range []string{"192.0.2.5:3478", "[2001:db8::7]:443"} {
		a := netip.MustParseAddrPort(ap)
		resp := AppendBindingSuccess(nil, stunBinding, a)
		if back, ok := ParseXorMapped(resp); !ok || back != a {
			t.Fatalf("xor %s -> %v", ap, back)
		}
	}
	// nicht erreichbar: Zeitueberschreitung
	dead, _ := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	da := dead.LocalAddr().(*net.UDPAddr).AddrPort()
	ctx2, cancel2 := context.WithTimeout(context.Background(), 700*time.Millisecond)
	defer cancel2()
	if _, _, err := ProbeUDP(ctx2, []netip.AddrPort{da}); err == nil {
		t.Fatal("antwort von stummem socket")
	}
	dead.Close()
}
