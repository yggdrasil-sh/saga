package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const testName = "saga.einport.test"

// writeCert: selbst signiertes Zertifikat fuer name in dir (cert.pem, key.pem).
func writeCert(t *testing.T, dir, name string, notAfter time.Time) (string, string, *x509.Certificate) {
	t.Helper()
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	sn, _ := rand.Int(rand.Reader, big.NewInt(1<<62))
	tpl := &x509.Certificate{SerialNumber: sn, Subject: pkix.Name{CommonName: name}, DNSNames: []string{name},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: notAfter, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &k.PublicKey, k)
	if err != nil {
		t.Fatal(err)
	}
	kd, _ := x509.MarshalECPrivateKey(k)
	cp, kp := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	_ = os.WriteFile(cp+".tmp", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o600)
	_ = os.WriteFile(kp, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kd}), 0o600)
	_ = os.Rename(cp+".tmp", cp)
	x, _ := x509.ParseCertificate(der)
	return cp, kp, x
}

// echoUp: nachgebildetes uvicorn - antwortet mit den Weiterleitungs-Koepfen.
func echoUp(t *testing.T) *httptest.Server {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintf(w, "xff=%s xfp=%s path=%s orig=%s", r.Header.Get("X-Forwarded-For"), r.Header.Get("X-Forwarded-Proto"), r.URL.Path,
			r.Header.Get("X-Saga-Orig-For"))
	}))
	t.Cleanup(up.Close)
	return up
}

type testEdge struct {
	e    *edge
	addr string
	cert *x509.Certificate
	dir  string
}

func newTestEdge(t *testing.T, proxy, withCert bool) *testEdge {
	t.Helper()
	up := echoUp(t)
	dir := t.TempDir()
	c := &config{upstream: strings.TrimPrefix(up.URL, "http://"), blobDir: t.TempDir(), trusted: parseTrust("127.0.0.1"), ttl: time.Minute,
		stall: 5 * time.Second, key: "k", maxConns: 64, maxConnsIP: 32, maxStreams: 8, maxStreamsIP: 8, idleUnauth: 5 * time.Second,
		tlsOn: true, tlsListen: "127.0.0.1:0", tlsProxy: proxy, baseHost: testName, baseOrigin: "https://" + testName, basePort: 443,
		hornData: filepath.Join(dir, "horn"), tlsCert: filepath.Join(dir, "cert.pem"), tlsKey: filepath.Join(dir, "key.pem")}
	te := &testEdge{dir: dir}
	if withCert {
		_, _, te.cert = writeCert(t, dir, testName, time.Now().Add(90*24*time.Hour))
	}
	tr := newTransport()
	m := &media{c: c, az: newAuthz(c, tr), proxy: newProxy(c, tr), lim: &limits{perIP: map[string]int{}, max: 10, maxIP: 10},
		client: &http.Client{Transport: tr}, ups: &upLocks{m: map[string]bool{}}, fed: newFedCache(), bufs: newBufPoolSize(32 << 10)}
	e, err := newEdge(m, "einport-schluessel-0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	if err := e.start(); err != nil {
		t.Fatal(err)
	}
	m.edge = e
	t.Cleanup(e.close)
	te.e, te.addr = e, e.ln.Addr().String()
	return te
}

func (te *testEdge) pool() *x509.CertPool {
	p := x509.NewCertPool()
	if te.cert != nil {
		p.AddCert(te.cert)
	}
	return p
}

// dialTLS: Verbindung mit optionalem PROXY-Kopf, TLS mit alpn.
func (te *testEdge) dialTLS(t *testing.T, pp []byte, alpn []string) (*tls.Conn, error) {
	t.Helper()
	raw, err := net.Dial("tcp", te.addr)
	if err != nil {
		t.Fatal(err)
	}
	if pp != nil {
		_, _ = raw.Write(pp)
	}
	tc := tls.Client(raw, &tls.Config{ServerName: testName, RootCAs: te.pool(), NextProtos: alpn})
	_ = tc.SetDeadline(time.Now().Add(3 * time.Second))
	if err := tc.Handshake(); err != nil {
		raw.Close()
		return nil, err
	}
	return tc, nil
}

func httpGet(t *testing.T, c net.Conn, path string, hdr string) string {
	t.Helper()
	fmt.Fprintf(c, "GET %s HTTP/1.1\r\nHost: %s\r\n%sConnection: close\r\n\r\n", path, testName, hdr)
	b, _ := io.ReadAll(c)
	return string(b)
}

func isClosed(c net.Conn, wait time.Duration) bool {
	_ = c.SetReadDeadline(time.Now().Add(wait))
	b := make([]byte, 64)
	_, err := c.Read(b)
	return err == io.EOF || (err != nil && !isTimeout(err))
}

// helloBytes: ClientHello eines echten Go-Clients mit alpn.
func helloBytes(t *testing.T, alpn []string) []byte {
	t.Helper()
	a, b := net.Pipe()
	go func() {
		tc := tls.Client(a, &tls.Config{ServerName: testName, NextProtos: alpn, InsecureSkipVerify: true})
		_ = tc.Handshake()
	}()
	p := &peekBuf{c: b}
	e := &edge{}
	_ = b.SetReadDeadline(time.Now().Add(2 * time.Second))
	got, err := e.readHello(p)
	a.Close()
	b.Close()
	if err != nil {
		t.Fatalf("readHello %v: %v", alpn, err)
	}
	if strings.Join(got, ",") != strings.Join(alpn, ",") {
		t.Fatalf("ALPN %v statt %v", got, alpn)
	}
	return p.b
}

func TestEinportHello(t *testing.T) {
	for _, a := range [][]string{nil, {"h2", "http/1.1"}, {"stun.turn"}, {"acme-tls/1"}, {"http/1.1"}} {
		helloBytes(t, a)
	}
	// abgeschnitten, falscher Typ, ueberlanger Datensatz
	h := helloBytes(t, []string{"h2"})
	e := &edge{}
	for name, b := range map[string][]byte{
		"abgeschnitten": h[:len(h)-10], "kein handshake": append([]byte{0x17}, h[1:]...),
		"zu lang": {0x16, 3, 1, 0x7f, 0xff}, "server hello": append(append([]byte(nil), h[:5]...), append([]byte{2}, h[6:]...)...),
	} {
		a, b2 := net.Pipe()
		go func(x []byte) { _, _ = a.Write(x); a.Close() }(b)
		_ = b2.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := e.readHello(&peekBuf{c: b2}); err == nil {
			t.Errorf("%s: kein Fehler", name)
		}
		b2.Close()
	}
}

func TestEinportProxyV2(t *testing.T) {
	src := &net.TCPAddr{IP: net.ParseIP("203.0.113.7"), Port: 4711}
	dst := &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443}
	h := buildProxyV2(src, dst)
	n, err := proxyNeed(h)
	if err != nil || n != len(h) {
		t.Fatalf("need %d %v", n, err)
	}
	a, err := parseProxyV2(h)
	if err != nil || a.String() != "203.0.113.7:4711" {
		t.Fatalf("v4: %v %v", a, err)
	}
	h6 := buildProxyV2(&net.TCPAddr{IP: net.ParseIP("2001:db8::5"), Port: 1}, &net.TCPAddr{IP: net.ParseIP("2001:db8::1"), Port: 443})
	if a, err := parseProxyV2(h6); err != nil || a.String() != "[2001:db8::5]:1" {
		t.Fatalf("v6: %v %v", a, err)
	}
	local := append([]byte(nil), h...)
	local[12] = 0x20
	if a, err := parseProxyV2(local); err != nil || a != nil {
		t.Fatalf("LOCAL: %v %v", a, err)
	}
	if n, err := proxyNeed(h[:5]); err != nil || n != 0 {
		t.Fatalf("Anfang: %d %v", n, err)
	}
	for name, b := range map[string][]byte{"signatur": append([]byte("GET / HTTP"), h[10:]...),
		"version": func() []byte { x := append([]byte(nil), h...); x[12] = 0x11; return x }(),
		"laenge":  func() []byte { x := append([]byte(nil), h...); x[14], x[15] = 0x10, 0; return x }()} {
		if _, err := proxyNeed(b); err == nil {
			t.Errorf("%s: angenommen", name)
		}
	}
	udp := append([]byte(nil), h...)
	udp[13] = 0x12
	if _, err := parseProxyV2(udp); err == nil {
		t.Errorf("udp angenommen")
	}
	cmd := append([]byte(nil), h...)
	cmd[12] = 0x2f
	if _, err := parseProxyV2(cmd); err == nil {
		t.Errorf("befehl 15 angenommen")
	}
	// v1 (nginx stream)
	for l, want := range map[string]string{"PROXY TCP4 203.0.113.7 10.0.0.1 4711 443\r\n": "203.0.113.7:4711",
		"PROXY TCP6 2001:db8::5 2001:db8::1 1 443\r\n": "[2001:db8::5]:1", "PROXY UNKNOWN\r\n": "<nil>"} {
		n, err := proxyV1Need([]byte(l + "rest"))
		if err != nil || n != len(l) {
			t.Fatalf("v1 need %q: %d %v", l, n, err)
		}
		a, err := parseProxyV1([]byte(l))
		if err != nil || fmt.Sprint(a) != want && !(a == nil && want == "<nil>") {
			t.Errorf("v1 %q: %v %v", l, a, err)
		}
	}
	for _, l := range []string{"PROXY TCP4 2001:db8::5 10.0.0.1 1 2\r\n", "PROXY TCP4 1.2.3.4 10.0.0.1 01 2\r\n", "PROXY TCP4 1.2.3.4 10.0.0.1 1\r\n",
		"PROXY TCP4 1.2.3.4 10.0.0.1 1 2\n", "PROXY UDP4 1.2.3.4 10.0.0.1 1 2\r\n"} {
		if _, err := parseProxyV1([]byte(l)); err == nil {
			t.Errorf("v1 angenommen: %q", l)
		}
	}
	if _, err := proxyV1Need([]byte("PROXY " + strings.Repeat("x", 120))); err == nil {
		t.Errorf("v1 ohne Ende angenommen")
	}
	if _, err := proxyV1Need([]byte("GET / ")); err == nil {
		t.Errorf("v1: GET angenommen")
	}
}

func TestEinportHTTPS(t *testing.T) {
	te := newTestEdge(t, false, true)
	tc, err := te.dialTLS(t, nil, []string{"h2", "http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if p := tc.ConnectionState().NegotiatedProtocol; p != "http/1.1" {
		t.Errorf("ALPN %q statt http/1.1 (Stufe 1 ohne h2)", p)
	}
	out := httpGet(t, tc, "/api/x", "X-Forwarded-For: 6.6.6.6\r\nX-Forwarded-Proto: http\r\n")
	if !strings.Contains(out, "xff=127.0.0.1 xfp=https path=/api/x orig=") {
		t.Errorf("Antwort: %q", out)
	}
	// Klartext-HTTP auf dem TLS-Port: geschlossen, keine Antwort
	raw, _ := net.Dial("tcp", te.addr)
	fmt.Fprintf(raw, "GET / HTTP/1.1\r\nHost: x\r\n\r\n")
	b, _ := io.ReadAll(raw)
	if len(b) != 0 {
		t.Errorf("Klartext beantwortet: %q", b)
	}
	if te.e.st.Rejected.Load() != 1 {
		t.Errorf("abgewiesen %d", te.e.st.Rejected.Load())
	}
}

func TestEinportProxyHeader(t *testing.T) {
	te := newTestEdge(t, true, true)
	pp := buildProxyV2(&net.TCPAddr{IP: net.ParseIP("203.0.113.7"), Port: 4711}, &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443})
	tc, err := te.dialTLS(t, pp, []string{"http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if out := httpGet(t, tc, "/", "X-Forwarded-For: 6.6.6.6\r\n"); !strings.Contains(out, "xff=203.0.113.7 xfp=https") {
		t.Errorf("Antwort: %q", out)
	}
	tc, err = te.dialTLS(t, []byte("PROXY TCP4 198.51.100.20 10.0.0.1 5555 443\r\n"), []string{"http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if out := httpGet(t, tc, "/", ""); !strings.Contains(out, "xff=198.51.100.20 xfp=https") {
		t.Errorf("v1: %q", out)
	}
	if te.e.info().ProxySeen == 0 {
		t.Errorf("proxy_seen fehlt")
	}
	// Gegenproben: ohne Kopf, kaputter Kopf, Zeitueberschreitung
	if _, err := te.dialTLS(t, nil, []string{"http/1.1"}); err == nil {
		t.Errorf("ohne PROXY-Kopf angenommen")
	}
	bad := append([]byte(nil), pp...)
	bad[12] = 0x11
	if _, err := te.dialTLS(t, bad, []string{"http/1.1"}); err == nil {
		t.Errorf("kaputter Kopf angenommen")
	}
	te.e.firstByte.Store(int64(300 * time.Millisecond))
	raw, _ := net.Dial("tcp", te.addr)
	_, _ = raw.Write(pp[:10])
	t0 := time.Now()
	if !isClosed(raw, 3*time.Second) || time.Since(t0) > 2*time.Second {
		t.Errorf("halber Kopf: nicht nach der Frist geschlossen (%v)", time.Since(t0))
	}
	if te.e.st.ProxyErr.Load() != 3 || te.e.info().ProxyBad == 0 || !te.e.info().ProxyMiss {
		t.Errorf("proxy_err %d, proxy_bad %d", te.e.st.ProxyErr.Load(), te.e.info().ProxyBad)
	}
}

// fakeNode: Gegenstelle der Uebergabe im Test (statt gjallarhorn).
type fakeNode struct {
	uc   *net.UnixConn
	ln   net.Listener
	got  chan handed
	once sync.Once
}

type handed struct {
	remote string
	pre    []byte
	c      net.Conn
}

func startFakeNode(t *testing.T, te *testEdge) *fakeNode {
	t.Helper()
	fs, err := te.e.nodeFiles()
	if err != nil {
		t.Fatal(err)
	}
	c, err := net.FileConn(fs[0])
	if err != nil {
		t.Fatal(err)
	}
	ln, err := net.FileListener(fs[1])
	if err != nil {
		t.Fatal(err)
	}
	fs[0].Close()
	fs[1].Close()
	n := &fakeNode{uc: c.(*net.UnixConn), ln: ln, got: make(chan handed, 16)}
	t.Cleanup(n.stop)
	go func() {
		buf, oob := make([]byte, 70<<10), make([]byte, syscall.CmsgSpace(16))
		for {
			k, oobn, _, _, err := n.uc.ReadMsgUnix(buf, oob)
			if err != nil || k == 0 {
				return
			}
			msgs, _ := syscall.ParseSocketControlMessage(oob[:oobn])
			fds, _ := syscall.ParseUnixRights(&msgs[0])
			f := os.NewFile(uintptr(fds[0]), "x")
			cc, _ := net.FileConn(f)
			f.Close()
			al := int(buf[1])
			n.got <- handed{remote: string(buf[2 : 2+al]), pre: append([]byte(nil), buf[2+al:k]...), c: cc}
		}
	}()
	if _, err := n.uc.Write([]byte("ready")); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 100 && te.e.hand.Load() == nil; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if te.e.hand.Load() == nil {
		t.Fatal("Knoten nicht bereit")
	}
	return n
}

func (n *fakeNode) stop() { n.once.Do(func() { n.uc.Close(); n.ln.Close() }) }

func (n *fakeNode) next(t *testing.T) handed {
	t.Helper()
	select {
	case h := <-n.got:
		return h
	case <-time.After(3 * time.Second):
		t.Fatal("keine Uebergabe")
	}
	return handed{}
}

func TestEinportHandover(t *testing.T) {
	te := newTestEdge(t, true, true)
	n := startFakeNode(t, te)
	pp := buildProxyV2(&net.TCPAddr{IP: net.ParseIP("198.51.100.9"), Port: 5000}, &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443})
	crt, _ := tls.LoadX509KeyPair(filepath.Join(te.dir, "cert.pem"), filepath.Join(te.dir, "key.pem"))
	for _, alpn := range [][]string{nil, {"stun.turn"}} {
		done := make(chan error, 1)
		go func() {
			tc, err := te.dialTLS(t, pp, alpn)
			if err != nil {
				done <- err
				return
			}
			_, _ = tc.Write([]byte("ping"))
			b := make([]byte, 4)
			_, err = io.ReadFull(tc, b)
			if err == nil && string(b) != "pong" {
				err = fmt.Errorf("antwort %q", b)
			}
			tc.Close()
			done <- err
		}()
		h := n.next(t)
		if h.remote != "198.51.100.9:5000" || len(h.pre) < 50 || h.pre[0] != 0x16 {
			t.Fatalf("Uebergabe %v: Adresse %q, %d Byte", alpn, h.remote, len(h.pre))
		}
		if _, ok := h.c.(*net.TCPConn); !ok {
			t.Errorf("kein echter TCP-Socket: %T", h.c)
		}
		// Knoten beendet TLS auf dem uebergebenen Socket (gelesene Bytes zuerst)
		sc := tls.Server(&prefixConn{Conn: h.c, pre: h.pre}, &tls.Config{Certificates: []tls.Certificate{crt}, NextProtos: []string{"stun.turn"}})
		_ = sc.SetDeadline(time.Now().Add(3 * time.Second))
		b := make([]byte, 4)
		if _, err := io.ReadFull(sc, b); err != nil || string(b) != "ping" {
			t.Fatalf("Knoten liest %q %v", b, err)
		}
		_, _ = sc.Write([]byte("pong"))
		if err := <-done; err != nil {
			t.Fatalf("Client %v: %v", alpn, err)
		}
		sc.Close()
	}
	// rohes ICE-TCP (RFC 4571 + STUN-Binding)
	raw, _ := net.Dial("tcp", te.addr)
	ice := append(append([]byte(nil), pp...), 0x00, 0x14, 0x00, 0x01, 0x00, 0x00, 0x21, 0x12, 0xa4, 0x42)
	ice = append(ice, make([]byte, 12)...)
	_, _ = raw.Write(ice)
	h := n.next(t)
	if h.remote != "198.51.100.9:5000" || !bytes.Equal(h.pre, ice[len(pp):]) {
		t.Errorf("ICE-TCP: %q %x", h.remote, h.pre)
	}
	h.c.Close()
	raw.Close()
	// TLS-ALPN-01
	go func() { _, _ = te.dialTLS(t, pp, []string{"acme-tls/1"}) }()
	h = n.next(t)
	h.c.Close()
	if te.e.st.Handed.Load() != 4 || te.e.st.ACME.Load() != 1 || te.e.st.ICE.Load() != 1 {
		t.Errorf("zaehler handed=%d acme=%d ice=%d", te.e.st.Handed.Load(), te.e.st.ACME.Load(), te.e.st.ICE.Load())
	}
	// HTTP mit ALPN bleibt bei saga-media
	tc, err := te.dialTLS(t, pp, []string{"http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if out := httpGet(t, tc, "/x", ""); !strings.Contains(out, "xff=198.51.100.9") {
		t.Errorf("https: %q", out)
	}
	select {
	case h := <-n.got:
		t.Errorf("HTTPS uebergeben: %v", h.remote)
	default:
	}
	// Knoten weg -> Uebergabe aus, TLS ohne ALPN bedient saga-media (HTTP), ICE-TCP wird geschlossen
	n.stop()
	for i := 0; i < 100 && te.e.hand.Load() != nil; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if te.e.hand.Load() != nil || te.e.info().Node {
		t.Fatal("Knoten weg nicht erkannt")
	}
	tc, err = te.dialTLS(t, pp, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out := httpGet(t, tc, "/ohne-alpn", ""); !strings.Contains(out, "path=/ohne-alpn") {
		t.Errorf("ohne Knoten, ohne ALPN: %q", out)
	}
	raw, _ = net.Dial("tcp", te.addr)
	_, _ = raw.Write(ice)
	if !isClosed(raw, 2*time.Second) {
		t.Errorf("ICE-TCP ohne Knoten nicht geschlossen")
	}
}

func TestEinportNoCert(t *testing.T) {
	te := newTestEdge(t, false, false)
	if te.e.cert.ok() || te.e.info().Cert != "fehlt" {
		t.Fatalf("Zertifikat: %+v", te.e.info())
	}
	if _, err := te.dialTLS(t, nil, []string{"http/1.1"}); err == nil {
		t.Errorf("HTTPS ohne Zertifikat angenommen")
	}
	n := startFakeNode(t, te)
	// ohne Zertifikat: TURN-TLS nicht, TLS-ALPN-01 schon (Knoten holt das Zertifikat)
	if _, err := te.dialTLS(t, nil, nil); err == nil {
		t.Errorf("TLS ohne ALPN ohne Zertifikat angenommen")
	}
	pool := te.pool()
	go func() {
		raw, _ := net.Dial("tcp", te.addr)
		_ = tls.Client(raw, &tls.Config{ServerName: testName, RootCAs: pool, NextProtos: []string{"acme-tls/1"}}).Handshake()
	}()
	n.next(t).c.Close()
	// Zertifikat kommt (Datei) -> ohne Neustart nach reload gueltig
	_, _, te.cert = writeCert(t, te.dir, testName, time.Now().Add(80*24*time.Hour))
	if !te.e.cert.reload() || !te.e.cert.ok() {
		t.Fatalf("nach reload: %+v", te.e.info())
	}
	tc, err := te.dialTLS(t, nil, []string{"http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	first := tc.ConnectionState().PeerCertificates[0].SerialNumber
	tc.Close()
	time.Sleep(20 * time.Millisecond) // mtime muss sich unterscheiden
	_, _, te.cert = writeCert(t, te.dir, testName, time.Now().Add(85*24*time.Hour))
	te.e.cert.reload()
	tc, err = te.dialTLS(t, nil, []string{"http/1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if tc.ConnectionState().PeerCertificates[0].SerialNumber.Cmp(first) == 0 {
		t.Errorf("neues Zertifikat nicht geladen")
	}
	tc.Close()
	// falscher Name bzw. abgelaufen: HTTPS wieder zu, Zustand fuer den Admin-Bereich
	writeCert(t, te.dir, "anderer.einport.test", time.Now().Add(80*24*time.Hour))
	te.e.cert.reload()
	if te.e.cert.ok() || te.e.info().Cert != "name" {
		t.Errorf("falscher Name: %+v", te.e.info())
	}
	time.Sleep(20 * time.Millisecond)
	writeCert(t, te.dir, testName, time.Now().Add(-time.Minute))
	te.e.cert.reload()
	if te.e.cert.ok() || te.e.info().Cert != "abgelaufen" {
		t.Errorf("abgelaufen: %+v", te.e.info())
	}
}

func TestEinportCertFromNode(t *testing.T) {
	// Dateien des Knotens: DNS-01 (fullchain/privkey + dns01.json) bzw. autocert-Speicher (Schluessel + Kette in einer Datei)
	dir := t.TempDir()
	c := &config{baseHost: testName, hornData: dir}
	e := newEdgeCert(c)
	tlsDir := filepath.Join(dir, "tls")
	_ = os.MkdirAll(filepath.Join(tlsDir, "acme"), 0o700)
	cp, kp, _ := writeCert(t, t.TempDir(), testName, time.Now().Add(40*24*time.Hour))
	cb, _ := os.ReadFile(cp)
	kb, _ := os.ReadFile(kp)
	_ = os.WriteFile(filepath.Join(tlsDir, "acme", testName), append(append([]byte(nil), kb...), cb...), 0o600)
	e.reload()
	if s := e.state(); s.state != "ok" || s.src != "alpn" {
		t.Fatalf("autocert: %+v", s)
	}
	cp2, kp2, _ := writeCert(t, t.TempDir(), testName, time.Now().Add(80*24*time.Hour))
	cb2, _ := os.ReadFile(cp2)
	kb2, _ := os.ReadFile(kp2)
	_ = os.WriteFile(filepath.Join(tlsDir, "fullchain.pem"), cb2, 0o644)
	_ = os.WriteFile(filepath.Join(tlsDir, "privkey.pem"), kb2, 0o600)
	_ = os.WriteFile(filepath.Join(tlsDir, "dns01.json"), []byte("{}"), 0o600)
	e.reload()
	if s := e.state(); s.state != "ok" || s.src != "dns01" || time.Until(s.notAfter) < 70*24*time.Hour {
		t.Fatalf("dns01 (spaeterer Ablauf gewinnt): %+v", s)
	}
}

func TestEinportNodeProxyAndBack(t *testing.T) {
	te := newTestEdge(t, true, true)
	n := startFakeNode(t, te)
	got := make(chan string, 4)
	go func() {
		_ = http.Serve(n.ln, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			got <- r.URL.Path + " key=" + fmt.Sprint(r.Header.Get(einKeyHeader) == te.e.key) + " for=" + r.Header.Get(einForHeader)
			_, _ = io.WriteString(w, "knoten")
		}))
	}()
	pp := buildProxyV2(&net.TCPAddr{IP: net.ParseIP("192.0.2.44"), Port: 1}, &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443})
	for _, p := range []string{"/api/sfu/leg", "/probe"} {
		tc, err := te.dialTLS(t, pp, []string{"http/1.1"})
		if err != nil {
			t.Fatal(err)
		}
		out := httpGet(t, tc, p, einKeyHeader+": falsch\r\n"+einForHeader+": 6.6.6.6\r\n")
		if !strings.HasSuffix(out, "knoten") {
			t.Errorf("%s: %q", p, out)
		}
		if g := <-got; g != p+" key=true for=192.0.2.44" {
			t.Errorf("%s: Knoten sah %q", p, g)
		}
	}
	// HTTP-Rueckfall vom Knoten: nur mit Schluessel, Adresse aus dem Kopf
	cl := &http.Client{Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return net.Dial("unix", te.e.backSock)
	}}}
	q, _ := http.NewRequest("GET", "http://saga/zurueck", nil)
	q.Header.Set(einKeyHeader, te.e.key)
	q.Header.Set(einForHeader, "198.51.100.77")
	q.Header.Set("X-Forwarded-For", "6.6.6.6")
	resp, err := cl.Do(q)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if !strings.Contains(string(b), "xff=198.51.100.77 xfp=https path=/zurueck") {
		t.Errorf("Rueckfall: %q", b)
	}
	q.Header.Set(einKeyHeader, "falsch")
	resp, err = cl.Do(q)
	if err != nil || resp.StatusCode != 403 {
		t.Errorf("Rueckfall ohne Schluessel: %v %v", resp, err)
	}
}

// TestEinportPendingPerIP (pruef-einport): halb offene Verbindungen einer Adresse (nach dem PROXY-Kopf bzw. ohne Proxy
// der Gegenstelle) sperren andere nicht aus - vorher genuegten 512 leere Verbindungen von einer Adresse fuer alle.
func TestEinportPendingPerIP(t *testing.T) {
	te := newTestEdge(t, true, true)
	te.e.firstByte.Store(int64(3 * time.Second))
	pp := func(ip string) []byte {
		return buildProxyV2(&net.TCPAddr{IP: net.ParseIP(ip), Port: 4711}, &net.TCPAddr{IP: net.ParseIP("10.0.0.1"), Port: 443})
	}
	var idle []net.Conn
	defer func() {
		for _, c := range idle {
			c.Close()
		}
	}()
	for i := 0; i < edgeMaxPendIP; i++ {
		c, err := net.Dial("tcp", te.addr)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = c.Write(pp("203.0.113.9"))
		idle = append(idle, c)
	}
	deadline := time.Now().Add(2 * time.Second)
	for te.e.pending.Load() < int64(edgeMaxPendIP) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	time.Sleep(100 * time.Millisecond)
	c, _ := net.Dial("tcp", te.addr)
	_, _ = c.Write(pp("203.0.113.9"))
	if !isClosed(c, time.Second) {
		t.Errorf("Verbindung ueber der Grenze je Adresse nicht geschlossen")
	}
	c.Close()
	tc, err := te.dialTLS(t, pp("198.51.100.3"), []string{"http/1.1"})
	if err != nil {
		t.Fatalf("andere Adresse ausgesperrt: %v", err)
	}
	if out := httpGet(t, tc, "/", ""); !strings.Contains(out, "xff=198.51.100.3") {
		t.Errorf("andere Adresse: %q", out)
	}
	if te.e.st.Busy.Load() != 1 {
		t.Errorf("busy %d", te.e.st.Busy.Load())
	}
	for _, c := range idle {
		c.Close()
	}
	idle = nil
	deadline = time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		te.e.pendMu.Lock()
		n := len(te.e.pendIP)
		te.e.pendMu.Unlock()
		if n == 0 && te.e.pending.Load() == 0 {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Errorf("Zaehler nicht frei: pending %d, je Adresse %v", te.e.pending.Load(), te.e.pendIP)
}
