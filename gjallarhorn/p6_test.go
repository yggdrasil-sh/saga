package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/acmefake"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
	"golang.org/x/crypto/acme"
)

// Pakete P6/P7: Release-Signatur, Waechter, Update-Maschine, Zertifikate (Rueckfall, TLS-ALPN-01 gegen Fake-ACME).

func testRelease(t *testing.T) ed25519.PrivateKey {
	t.Helper()
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	old := releaseKeyOverride
	releaseKeyOverride = base64.StdEncoding.EncodeToString(pub)
	t.Cleanup(func() { releaseKeyOverride = old })
	return sk
}

func signRel(sk ed25519.PrivateKey, ver, arch string, bin []byte) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(sk, releaseMsg(ver, arch, bin)))
}

func TestReleaseSignatur(t *testing.T) {
	sk := testRelease(t)
	bin := []byte("binaer-inhalt")
	sig := signRel(sk, "2.5.1", "linux-amd64", bin)
	if err := verifyRelease(releaseKey(), bin, sig, "2.5.1", "linux-amd64"); err != nil {
		t.Fatalf("gueltig abgelehnt: %v", err)
	}
	for name, c := range map[string]func() error{
		"anderes binaer": func() error { return verifyRelease(releaseKey(), []byte("boese"), sig, "2.5.1", "linux-amd64") },
		"andere version": func() error { return verifyRelease(releaseKey(), bin, sig, "2.5.0", "linux-amd64") },
		"andere arch":    func() error { return verifyRelease(releaseKey(), bin, sig, "2.5.1", "linux-arm64") },
		"fremder schl.": func() error {
			_, o, _ := ed25519.GenerateKey(rand.Reader)
			return verifyRelease(releaseKey(), bin, signRel(o, "2.5.1", "linux-amd64", bin), "2.5.1", "linux-amd64")
		},
		"kaputte sig":      func() error { return verifyRelease(releaseKey(), bin, "xx", "2.5.1", "linux-amd64") },
		"version unsicher": func() error { return verifyRelease(releaseKey(), bin, sig, "../x", "linux-amd64") },
		"ohne schluessel":  func() error { return verifyRelease(nil, bin, sig, "2.5.1", "linux-amd64") },
	} {
		if c() == nil {
			t.Errorf("%s: angenommen", name)
		}
	}
	releaseKeyOverride = ""
	if k := releaseKey(); len(k) != 32 {
		t.Errorf("release.pub im Repo: gueltiger Ed25519-Schluessel erwartet, Laenge %d", len(k))
	}
}

func writeBin(t *testing.T, dir, name, ver string, bin []byte, sig string) {
	t.Helper()
	_ = os.MkdirAll(dir, 0o755)
	for n, b := range map[string][]byte{name: bin, name + ".ver": []byte(ver + "\n"), name + ".sig": []byte(sig)} {
		if err := os.WriteFile(filepath.Join(dir, n), b, 0o755); err != nil {
			t.Fatal(err)
		}
	}
}

func TestWaechterBinaer(t *testing.T) {
	sk := testRelease(t)
	bd := binDir{t.TempDir()}
	bin := []byte("#!/bin/true\n")
	// falsche Signatur: bin/next wird verworfen, gestartet wird das Image-Binaer
	writeBin(t, bd.dir, "next", "2.5.1", bin, signRel(sk, "2.5.2", releaseArch(), bin))
	bd.promote()
	if _, err := os.Stat(bd.p("next")); err == nil {
		t.Fatal("next mit falscher Signatur nicht verworfen")
	}
	if b, _ := bd.pick("/self"); b != "/self" {
		t.Fatalf("pick = %s", b)
	}
	// richtige Signatur: wird current
	writeBin(t, bd.dir, "next", "2.5.1", bin, signRel(sk, "2.5.1", releaseArch(), bin))
	bd.promote()
	if b, v := bd.pick("/self"); b != bd.p("current") || v != "2.5.1" {
		t.Fatalf("pick = %s %s", b, v)
	}
	// nachtraeglich veraendert: nicht mehr verwendbar
	_ = os.WriteFile(bd.p("current"), []byte("veraendert"), 0o755)
	if b, _ := bd.pick("/self"); b != "/self" {
		t.Fatal("veraendertes current gestartet")
	}
	// als schlecht markiert: Image-Binaer
	writeBin(t, bd.dir, "current", "2.5.1", bin, signRel(sk, "2.5.1", releaseArch(), bin))
	_ = os.WriteFile(bd.p("bad"), []byte("2.5.1\n"), 0o644)
	if b, _ := bd.pick("/self"); b != "/self" {
		t.Fatal("schlechte Version gestartet")
	}
}

func newTestUpdater(t *testing.T, base string) (*updater, *server, *int) {
	t.Helper()
	s := &server{cfg: &config{Data: t.TempDir()}, node: sfu.NewNode(sfu.Config{Name: "horn-9.example.org", Verifier: &sagaVerifier{}})}
	u := newUpdater(s)
	u.base = base
	code := -1
	u.exit = func(c int) { code = c }
	u.shut = func() {}
	s.upd = u
	return u, s, &code
}

func TestUpdateMaschine(t *testing.T) {
	sk := testRelease(t)
	t.Setenv("HORN_WATCHED", "1")
	bin := []byte("neues-binaer")
	good := signRel(sk, "9.9.9", releaseArch(), bin)
	sig := good
	hits := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		switch r.URL.Path {
		case "/v9.9.9/gjallarhorn-" + releaseArch():
			_, _ = w.Write(bin)
		case "/v9.9.9/gjallarhorn-" + releaseArch() + ".sig":
			_, _ = w.Write([]byte(sig))
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	// falsche Signatur: abgelehnt, kein Leeren, Backoff (kein zweiter Abruf sofort)
	sig = signRel(sk, "9.9.8", releaseArch(), bin)
	u, s, code := newTestUpdater(t, srv.URL)
	u.target("9.9.9", false)
	now := time.Now()
	u.step(now)
	if u.state != "fehler" || u.code != "sig" || !strings.Contains(u.err, "signatur") || s.draining.Load() || *code != -1 {
		t.Fatalf("falsche Signatur: state=%s err=%s drain=%v", u.state, u.err, s.draining.Load())
	}
	n := hits
	u.step(now.Add(time.Second))
	if hits != n {
		t.Fatal("kein Backoff nach Fehler")
	}
	// richtige Signatur, weitere Knoten da: Leeren, Neustart sobald leer
	sig = good
	u, s, code = newTestUpdater(t, srv.URL)
	u.target("9.9.9", false)
	u.step(time.Now())
	if u.state != "leeren" || !s.draining.Load() {
		t.Fatalf("leeren erwartet: %s", u.state)
	}
	if b, _ := os.ReadFile(filepath.Join(s.cfg.Data, "bin", "next")); string(b) != string(bin) {
		t.Fatal("bin/next fehlt")
	}
	if s.drainGate() == nil {
		t.Fatal("waehrend des Leerens neue Beine erlaubt")
	}
	u.step(time.Now())
	if *code != exitUpdate {
		t.Fatalf("leerer Knoten: Neustart erwartet, code %d", *code)
	}
	// einziger Knoten: kein Leeren, wartet auf anruffreie Zeit
	u, s, code = newTestUpdater(t, srv.URL)
	u.target("9.9.9", true)
	u.step(time.Now())
	if u.state != "wartet" || s.draining.Load() {
		t.Fatalf("solo: %s drain=%v", u.state, s.draining.Load())
	}
	// ohne Waechter kein Selbst-Update
	t.Setenv("HORN_WATCHED", "")
	u, _, _ = newTestUpdater(t, srv.URL)
	u.target("9.9.9", false)
	u.step(time.Now())
	if u.state != "manuell" {
		t.Fatalf("ohne Waechter: %s", u.state)
	}
	// ohne Release-Schluessel aus ("#" = nur Kommentar, also kein Schluessel)
	t.Setenv("HORN_WATCHED", "1")
	releaseKeyOverride = "#"
	u, _, _ = newTestUpdater(t, srv.URL)
	u.target("9.9.9", false)
	u.step(time.Now())
	if u.state != "aus" {
		t.Fatalf("ohne Schluessel: %s", u.state)
	}
}

func TestZertifikatRueckfallUndALPN(t *testing.T) {
	name := "horn-7.example.org"
	ca := acmefake.New()
	hs := httptest.NewServer(ca)
	defer hs.Close()
	ca.Base = hs.URL
	c, err := netx.NewCerts(netx.CertOptions{Name: name, CertFile: filepath.Join(t.TempDir(), "x.pem"), ACMEDir: t.TempDir(), ACMEURL: hs.URL + "/dir", Defer: true})
	if err != nil {
		t.Fatal(err)
	}
	if c.ModeNow() != "wait" {
		t.Fatalf("modus %s", c.ModeNow())
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
	peer := func(sni string) *x509.Certificate {
		cn, err := tls.Dial("tcp", ln.Addr().String(), &tls.Config{ServerName: sni, InsecureSkipVerify: true}) //nolint:gosec // Test
		if err != nil {
			t.Fatalf("handshake %s: %v", sni, err)
		}
		defer cn.Close()
		return cn.ConnectionState().PeerCertificates[0]
	}
	// vor der Entscheidung: Rueckfall (selbst signiert), auch bei SNI = Name; ALPN-Pruefung abgelehnt
	if x := peer(name); x.Issuer.CommonName != name {
		t.Fatalf("rueckfall erwartet, aussteller %s", x.Issuer.CommonName)
	}
	m := c.ACME()
	m.Client = &acme.Client{DirectoryURL: hs.URL + "/dir"}
	c.UseALPN()
	crt, err := m.GetCertificate(&tls.ClientHelloInfo{ServerName: name, SupportedProtos: []string{"http/1.1"}})
	if err != nil {
		t.Fatalf("tls-alpn-01: %v (log %v)", err, ca.Log)
	}
	x, _ := x509.ParseCertificate(crt.Certificate[0])
	if x.Issuer.CommonName != "Saga Test-ACME CA" {
		t.Fatalf("aussteller %s", x.Issuer.CommonName)
	}
	if x := peer(name); x.Issuer.CommonName != "Saga Test-ACME CA" {
		t.Fatalf("nach alpn: %s", x.Issuer.CommonName)
	}
	// fremde SNI (Rueckpruefung ueber IP) loest nie ACME aus
	n, _ := ca.Snapshot()
	if x := peer("rueck.invalid"); x.Issuer.CommonName != name {
		t.Fatalf("fremde sni: %s", x.Issuer.CommonName)
	}
	if n2, _ := ca.Snapshot(); n2 != n {
		t.Fatal("fremde SNI hat ein Zertifikat ausgeloest")
	}
}

func TestReleaseURLVorab(t *testing.T) {
	b := "https://example.org/dl"
	if u := releaseURL(b, "2.5.0", "linux-arm64"); u != b+"/v2.5.0/gjallarhorn-linux-arm64" {
		t.Fatalf("Freigabe: %s", u)
	}
	if u := releaseURL(b, "2.5.0-rc.131", "linux-amd64"); u != b+"/horn-testing/gjallarhorn-2.5.0-rc.131-linux-amd64" {
		t.Fatalf("Vorabversion: %s", u)
	}
}
