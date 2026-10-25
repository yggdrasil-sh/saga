package main

import (
	"crypto/ecdsa"
	"crypto/tls"
	"net"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/acmefake"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"golang.org/x/crypto/acme"
)

// Labor 25.10.: Die Kaskade zu einem Knoten mit ACME-Zertifikat (TLS-ALPN-01) kam nie zustande. Der waehlende Knoten
// bot nur TLS 1.3 an (keine ECDHE-ECDSA-Suite im Hello), autocert auf der Gegenseite waehlte deshalb das RSA-Zertifikat,
// das es nicht gab: Handshake hing in der Bestellung ("context deadline exceeded") bzw. "internal error".

func tlsServe(t *testing.T, cfg *tls.Config) string {
	t.Helper()
	ln, err := tls.Listen("tcp", "127.0.0.1:0", cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			cn, err := ln.Accept()
			if err != nil {
				return
			}
			go func() { _ = cn.(*tls.Conn).Handshake(); time.Sleep(50 * time.Millisecond); cn.Close() }()
		}
	}()
	return ln.Addr().String()
}

// kaskadeCA: Knoten-Zertifikate wie im Labor - nur ECDSA ausgestellt, danach keine Bestellung mehr moeglich.
func kaskadeCA(t *testing.T, name string) (*netx.Certs, *acmefake.Server, string) {
	t.Helper()
	ca := acmefake.New()
	hs := httptest.NewServer(ca)
	t.Cleanup(hs.Close)
	ca.Base = hs.URL
	c, err := netx.NewCerts(netx.CertOptions{Name: name, CertFile: filepath.Join(t.TempDir(), "x.pem"), ACMEDir: t.TempDir(), ACMEURL: hs.URL + "/dir", Defer: true})
	if err != nil {
		t.Fatal(err)
	}
	addr := tlsServe(t, c.TLSConfig())
	ca.Dial = func(string) (net.Conn, error) { return net.Dial("tcp", addr) }
	m := c.ACME()
	m.Client = &acme.Client{DirectoryURL: hs.URL + "/dir"}
	c.UseALPN()
	if _, err := m.GetCertificate(&tls.ClientHelloInfo{ServerName: name, SupportedProtos: []string{"http/1.1"},
		CipherSuites: []uint16{tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256}, SignatureSchemes: []tls.SignatureScheme{tls.ECDSAWithP256AndSHA256},
		SupportedCurves: []tls.CurveID{tls.CurveP256}}); err != nil {
		t.Fatalf("ecdsa: %v (log %v)", err, ca.Log)
	}
	ca.MaxOrder = 1
	return c, ca, addr
}

func dialTLS(addr string, cfg *tls.Config) (tls.ConnectionState, error) {
	cn, err := tls.DialWithDialer(&net.Dialer{Timeout: 5 * time.Second}, "tcp", addr, cfg)
	if err != nil {
		return tls.ConnectionState{}, err
	}
	defer cn.Close()
	return cn.ConnectionState(), nil
}

// Annehmender Knoten (Fix in netx): reiner TLS-1.3-Client, auch nur mit X25519, bekommt das ECDSA-Zertifikat.
func TestKaskadeTLS13ClientBekommtECDSA(t *testing.T) {
	name := "horn-9.example.org"
	_, ca, addr := kaskadeCA(t, name)
	for _, curves := range [][]tls.CurveID{nil, {tls.X25519}} {
		old := &tls.Config{ServerName: name, InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}, MinVersion: tls.VersionTLS13, CurvePreferences: curves} //nolint:gosec // Test
		st, err := dialTLS(addr, old)
		if err != nil {
			t.Fatalf("tls-1.3-client %v: %v", curves, err)
		}
		if _, ok := st.PeerCertificates[0].PublicKey.(*ecdsa.PublicKey); !ok || st.PeerCertificates[0].Issuer.CommonName != "Saga Test-ACME CA" {
			t.Fatalf("zertifikat %v: %T %s", curves, st.PeerCertificates[0].PublicKey, st.PeerCertificates[0].Issuer.CommonName)
		}
	}
	if n, _ := ca.Snapshot(); n != 1 {
		t.Fatalf("weitere Bestellung ausgeloest: %d", n)
	}
}

// Waehlender Knoten (Fix in bifrost): gegen einen Knoten mit unveraendertem autocert (bis rc.184) klappt der
// Handshake mit bridgeClientTLS und ergibt TLS 1.3; die alte Einstellung (nur TLS 1.3) scheitert dort.
func TestKaskadeDialerGegenAltenKnoten(t *testing.T) {
	name := "horn-10.example.org"
	c, ca, _ := kaskadeCA(t, name)
	m := c.ACME()
	addr := tlsServe(t, &tls.Config{GetCertificate: m.GetCertificate, NextProtos: []string{"http/1.1"}, MinVersion: tls.VersionTLS12})
	old := &tls.Config{ServerName: name, InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}, MinVersion: tls.VersionTLS13} //nolint:gosec // Test
	if _, err := dialTLS(addr, old); err == nil {
		t.Fatal("alte Einstellung unerwartet verbunden - Testaufbau pruefen")
	}
	st, err := dialTLS(addr, bridgeClientTLS(name))
	if err != nil {
		t.Fatalf("bridgeClientTLS: %v", err)
	}
	if st.Version != tls.VersionTLS13 || st.NegotiatedProtocol != "http/1.1" {
		t.Fatalf("version %x alpn %q", st.Version, st.NegotiatedProtocol)
	}
	if _, ok := st.PeerCertificates[0].PublicKey.(*ecdsa.PublicKey); !ok {
		t.Fatalf("zertifikat %T", st.PeerCertificates[0].PublicKey)
	}
	if n, _ := ca.Snapshot(); n != 1 {
		t.Fatalf("weitere Bestellung ausgeloest: %d", n)
	}
}
