package main

import (
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

// 2.5 (horn-admin): Der eingebaute Knoten (Steuerkanal HORN_SAGA_CTL von saga-media) wartet mit ACME wie gekoppelte
// Knoten auf die Art aus dem welcome (dns01/alpn) - vorher holte er sofort per TLS-ALPN-01, was hinter einer
// TLS-Durchreiche auf einem anderen Port als 443 nie klappt. Probe-Betrieb und Dateien/HORN_TLS_SELF unveraendert.
func TestEingebautWartetAufSaga(t *testing.T) {
	t.Setenv("HORN_DATA", t.TempDir())
	t.Setenv("HORN_NAME", "horn-0.example.org")
	t.Setenv("HORN_TLS_SELF", "")
	t.Setenv("HORN_SAGA", "")
	t.Setenv("HORN_MODE", "node")
	t.Setenv("HORN_SAGA_CTL", "ws://127.0.0.1:1/api/horn/ctl")
	c, err := loadConfig(false)
	if err != nil {
		t.Fatal(err)
	}
	if !c.Certs.Defer {
		t.Fatal("eingebauter Knoten (HORN_SAGA_CTL): Defer erwartet")
	}
	ce, err := netx.NewCerts(c.Certs)
	if err != nil || ce.ModeNow() != "wait" {
		t.Fatalf("modus %v %v", ce, err)
	}
	t.Setenv("HORN_SAGA_CTL", "")
	t.Setenv("HORN_MODE", "probe")
	if c, _ = loadConfig(false); c.Certs.Defer {
		t.Fatal("probe: kein Defer")
	}
	t.Setenv("HORN_MODE", "node")
	t.Setenv("HORN_SAGA_CTL", "ws://127.0.0.1:1/api/horn/ctl")
	t.Setenv("HORN_TLS_SELF", "1")
	c, _ = loadConfig(false)
	if ce, err := netx.NewCerts(c.Certs); err != nil || ce.ModeNow() != "self" {
		t.Fatalf("HORN_TLS_SELF: %v", err)
	}
}

// TLS-ALPN-01: NotAfter kommt aus dem autocert-Cache (vorher Null-Zeit -> nach einer Neuverbindung der Steuerung
// meldete der Knoten "fehlt", Saga hielt ihn fuer unbereit).
func TestALPNNotAfterAusCache(t *testing.T) {
	name := "horn-0.example.org"
	ca := acmefake.New()
	hs := httptest.NewServer(ca)
	defer hs.Close()
	ca.Base = hs.URL
	c, err := netx.NewCerts(netx.CertOptions{Name: name, CertFile: filepath.Join(t.TempDir(), "x.pem"), ACMEDir: t.TempDir(), ACMEURL: hs.URL + "/dir", Defer: true})
	if err != nil {
		t.Fatal(err)
	}
	ln, err := tls.Listen("tcp", "127.0.0.1:0", c.TLSConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		for {
			cn, err := ln.Accept()
			if err != nil {
				return
			}
			go func() { _ = cn.(*tls.Conn).Handshake(); time.Sleep(50 * time.Millisecond); cn.Close() }()
		}
	}()
	ca.Dial = func(string) (net.Conn, error) { return net.Dial("tcp", ln.Addr().String()) }
	if !c.NotAfter().IsZero() {
		t.Fatal("vor ALPN: kein Ablauf erwartet")
	}
	c.UseALPN()
	if !c.NotAfter().IsZero() {
		t.Fatal("ALPN ohne Zertifikat: kein Ablauf erwartet")
	}
	m := c.ACME()
	m.Client = &acme.Client{DirectoryURL: hs.URL + "/dir"}
	crt, err := m.GetCertificate(&tls.ClientHelloInfo{ServerName: name, SupportedProtos: []string{"http/1.1"}})
	if err != nil || crt == nil {
		t.Fatalf("tls-alpn-01: %v", err)
	}
	if na := c.NotAfter(); na.IsZero() || time.Until(na) < time.Hour {
		t.Fatalf("NotAfter aus dem Cache: %v", na)
	}
}
