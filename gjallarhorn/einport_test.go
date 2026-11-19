package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

func TestICETCPMode(t *testing.T) {
	for _, c := range []struct {
		v, url string
		port   int
		want   bool
	}{{"", "", 8443, true}, {"auto", "https://horn-0.example.org", 8443, false}, {"auto", "https://horn-0.example.org", 443, true},
		{"", "https://saga.example.org:8443", 8443, true}, {"", "https://saga.example.org:8443/", 443, false},
		{"0", "", 443, false}, {"1", "https://x.example.org", 8443, true}, {"", "https://[2001:db8::1]", 443, true}} {
		got, err := iceTCPMode(c.v, c.url, c.port)
		if err != nil || got != c.want {
			t.Errorf("%q %q %d: %v %v", c.v, c.url, c.port, got, err)
		}
	}
	if _, err := iceTCPMode("vielleicht", "", 1); err == nil {
		t.Errorf("ungueltiger Wert angenommen")
	}
}

// TestEinportReceive: Uebergabe ueber das Socket-Paar (wie saga-media media/einport.go) -> netx.Mux.Inject mit echter
// Gegenadresse und den gelesenen Bytes; kaputte Nachrichten zaehlen als abgewiesen und lecken keine fds.
func TestEinportReceive(t *testing.T) {
	fds, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_SEQPACKET|syscall.SOCK_CLOEXEC, 0)
	if err != nil {
		t.Fatal(err)
	}
	mk := func(fd int) *net.UnixConn {
		f := os.NewFile(uintptr(fd), "x")
		c, err := net.FileConn(f)
		f.Close()
		if err != nil {
			t.Fatal(err)
		}
		return c.(*net.UnixConn)
	}
	saga, node := mk(fds[0]), mk(fds[1])
	defer saga.Close()
	certs := &netx.Certs{Mode: "self"}
	s := &server{mux: netx.NewInjectMux(&net.TCPAddr{Port: 443}, certs.TLSConfig()), ein: &einport{hand: node, key: "k"}}
	defer s.mux.Close()
	go s.einportReceive()
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	defer ln.Close()
	cl, _ := net.Dial("tcp", ln.Addr().String())
	defer cl.Close()
	sv, _ := ln.Accept()
	go cl.Write(append([]byte{0, 20}, 0, 1, 0, 0, 0x21, 0x12, 0xa4, 0x42, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12))
	pre := make([]byte, 2)
	io.ReadFull(sv, pre)
	rc, _ := sv.(*net.TCPConn).SyscallConn()
	addr := "198.51.100.3:4444"
	msg := append(append([]byte{1, byte(len(addr))}, addr...), pre...)
	// zuerst kaputt (ohne fd, falsche Version), dann gueltig
	saga.Write([]byte{1, 2})
	rc.Control(func(fd uintptr) { saga.WriteMsgUnix([]byte{9, 0}, syscall.UnixRights(int(fd)), nil) })
	rc.Control(func(fd uintptr) { saga.WriteMsgUnix(msg, syscall.UnixRights(int(fd)), nil) })
	sv.Close() // nur die Kopie von "saga-media"
	ch := make(chan net.Conn, 1)
	go func() { c, _ := s.mux.ICE.Accept(); ch <- c }()
	var got net.Conn
	select {
	case got = <-ch:
	case <-time.After(3 * time.Second):
		t.Fatal("keine Uebergabe angekommen")
	}
	buf := make([]byte, 22)
	if _, err := io.ReadFull(got, buf); err != nil || buf[1] != 20 || buf[6] != 0x21 || got.RemoteAddr().String() != addr {
		t.Fatalf("ice: %x %v %v", buf, err, got.RemoteAddr())
	}
	if r := s.mux.Stats.Rejected.Load(); r != 2 {
		t.Errorf("abgewiesen %d statt 2", r)
	}
	got.Close()
}

func TestEinportHTTP(t *testing.T) {
	s := &server{ein: &einport{key: "geheim-0123456789"}}
	var seen string
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r.URL.Path + " " + ipKey(r)
		io.WriteString(w, "knoten")
	})
	in := s.einportInner(h)
	for _, c := range []struct {
		path, key, ip string
		code          int
	}{{"/api/sfu/leg", "geheim-0123456789", "203.0.113.4", 200}, {"/probe", "geheim-0123456789", "2001:db8::7", 200},
		{"/api/sfu/leg", "falsch", "203.0.113.4", 403}, {"/api/sfu/leg", "geheim-0123456789", "kaputt", 403},
		{"/healthz", "geheim-0123456789", "203.0.113.4", 403}} {
		seen = ""
		r := httptest.NewRequest("GET", c.path, nil)
		r.Header.Set(einKeyHeader, c.key)
		r.Header.Set(einForHeader, c.ip)
		w := httptest.NewRecorder()
		in.ServeHTTP(w, r)
		if w.Code != c.code || (c.code == 200 && seen != c.path+" "+c.ip) {
			t.Errorf("%s %s %s: %d %q", c.path, c.key, c.ip, w.Code, seen)
		}
	}
	// Rueckfall: Geraete-Pfade im Knoten, alles andere an saga-media (back.sock) mit Schluessel und Adresse
	dir := t.TempDir()
	back := dir + "/back.sock"
	bl, err := net.Listen("unix", back)
	if err != nil {
		t.Fatal(err)
	}
	defer bl.Close()
	go http.Serve(bl, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "saga "+r.URL.Path+" "+r.Header.Get(einForHeader)+" "+map[bool]string{true: "key", false: "nokey"}[r.Header.Get(einKeyHeader) == "geheim-0123456789"])
	}))
	s.ein.back = back
	front := s.einportFront(h)
	for p, want := range map[string]string{"/api/sfu/turn": "knoten", "/probe": "knoten", "/": "saga / 203.0.113.9 key", "/api/me": "saga /api/me 203.0.113.9 key"} {
		r := httptest.NewRequest("GET", p, nil)
		r.RemoteAddr = "203.0.113.9:5000"
		r.Header.Set(einKeyHeader, "von-aussen")
		w := httptest.NewRecorder()
		front.ServeHTTP(w, r)
		if b := w.Body.String(); !strings.HasPrefix(b, want) {
			t.Errorf("%s: %q statt %q", p, b, want)
		}
	}
}

// writeCertFile: Zertifikat fuer name mit Ablauf notAfter als fullchain.pem/privkey.pem (DNS-01-Ablage des Knotens).
func writeCertFile(t *testing.T, dir, name string, notAfter time.Time) {
	t.Helper()
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tpl := &x509.Certificate{SerialNumber: big.NewInt(time.Now().UnixNano()), Subject: pkix.Name{CommonName: name}, DNSNames: []string{name},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: notAfter}
	der, _ := x509.CreateCertificate(rand.Reader, tpl, tpl, &k.PublicKey, k)
	kd, _ := x509.MarshalECPrivateKey(k)
	_ = os.MkdirAll(dir, 0o700)
	_ = os.WriteFile(filepath.Join(dir, "privkey.pem"), pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kd}), 0o600)
	_ = os.WriteFile(filepath.Join(dir, "fullchain.pem"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644)
}

// TestCertDNS01ZuALPN (Pruefer-Fund pruef-horn): Token entfernt (Saga schickt cert alpn) bei vorhandenem DNS-01-Zertifikat:
// Datei bleibt, bis sie in 30 Tagen ablaeuft, dann TLS-ALPN-01. Zertifikat fuer einen anderen Namen gilt als fehlend.
func TestCertDNS01ZuALPN(t *testing.T) {
	data := t.TempDir()
	tlsDir := filepath.Join(data, "tls")
	name := "saga.example.org"
	writeCertFile(t, tlsDir, name, time.Now().Add(60*24*time.Hour))
	_ = os.WriteFile(filepath.Join(tlsDir, "dns01.json"), []byte("{}"), 0o600)
	certs, err := netx.NewCerts(netx.CertOptions{Name: name, CertFile: filepath.Join(tlsDir, "fullchain.pem"), KeyFile: filepath.Join(tlsDir, "privkey.pem"),
		ACMEDir: filepath.Join(tlsDir, "acme"), ACMEURL: "http://127.0.0.1:1/dir", Defer: true})
	if err != nil {
		t.Fatal(err)
	}
	s := &server{cfg: &config{Name: name, Data: data}, certs: certs}
	defer s.closing.Store(true)
	// kurze Fristen; nicht zuruecksetzen - alpnWarm laeuft nach dem Test im Hintergrund weiter (liest sie noch)
	certCheckEvery, alpnECDSAWait = 20*time.Millisecond, 50*time.Millisecond
	if st := s.certStatus(); st["state"] != "ok" || st["mode"] != "file" {
		t.Fatalf("vorher: %v", st)
	}
	s.certMode("alpn")
	time.Sleep(100 * time.Millisecond)
	if certs.ModeNow() != "file" {
		t.Fatalf("60 Tage Rest: Datei muss bleiben, Modus %s", certs.ModeNow())
	}
	// Datei laeuft in 20 Tagen ab -> Wechsel auf TLS-ALPN-01
	writeCertFile(t, tlsDir, name, time.Now().Add(20*24*time.Hour))
	if err := certs.Reload(); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 100 && certs.ModeNow() != "acme"; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if certs.ModeNow() != "acme" || certs.ACME() == nil {
		t.Fatalf("kein Wechsel: %s", certs.ModeNow())
	}
	// anderer Name (eingebauter Knoten von horn-0 auf den Saga-Namen): nicht "ok"
	writeCertFile(t, tlsDir, "horn-0.example.org", time.Now().Add(60*24*time.Hour))
	c2, _ := netx.NewCerts(netx.CertOptions{Name: name, CertFile: filepath.Join(tlsDir, "fullchain.pem"), KeyFile: filepath.Join(tlsDir, "privkey.pem"),
		ACMEDir: filepath.Join(tlsDir, "acme"), Defer: true})
	s2 := &server{cfg: &config{Name: name, Data: data}, certs: c2}
	if st := s2.certStatus(); st["state"] != "fehlt" || c2.Covers(name) {
		t.Fatalf("falscher Name: %v", st)
	}
	if tc := c2.TLSConfig(); !strings.Contains(strings.Join(tc.NextProtos, ","), "acme-tls/1") {
		t.Errorf("Dateimodus: acme-tls/1 fehlt in %v", tc.NextProtos)
	}
}

// TestEinportFrontKeepsQuery (Bugs-DB 2182): Rueckfall an saga-media reicht die Query byte-gleich durch - ReverseProxy
// kodierte sie bei ';', ungueltigem '%' bzw. gesetztem GODEBUG urlmaxqueryparams neu (sortiert), signierte
// Foederations-Anfragen (@query) schlugen dahinter mit 401 fehl.
func TestEinportFrontKeepsQuery(t *testing.T) {
	back := t.TempDir() + "/back.sock"
	bl, err := net.Listen("unix", back)
	if err != nil {
		t.Fatal(err)
	}
	defer bl.Close()
	go http.Serve(bl, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, r.RequestURI) }))
	s := &server{ein: &einport{key: "geheim-0123456789", back: back}}
	front := s.einportFront(http.NotFoundHandler())
	targets := []string{"/fed/v2/chat/msgs?ref=abc&after=0", "/fed/v2/directory?since=0&cfg=", "/api/x?b=2&a=1&a=0&b=1",
		"/api/x?a=1;b=2&c=3", "/api/x?q=%7e%2F%20x+y&z=%C3%A4", "/api/x?bad=%zz&a=1", "/api/x?leer=&=w&nur&&x=1", "/api/x?",
		"/fed/v2/x/a%2Fb%20c:d@e?ref=r%2Fx&after=0"}
	for _, gd := range []string{"", "urlmaxqueryparams=10000"} {
		if gd != "" {
			t.Setenv("GODEBUG", gd)
		}
		for _, want := range targets {
			r := httptest.NewRequest("GET", want, nil)
			r.RemoteAddr = "203.0.113.9:5000"
			w := httptest.NewRecorder()
			front.ServeHTTP(w, r)
			if got := w.Body.String(); got != want {
				t.Errorf("GODEBUG=%s: saga-media sah %q statt %q", gd, got, want)
			}
		}
	}
}
