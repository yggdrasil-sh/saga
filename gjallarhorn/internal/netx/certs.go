package netx

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"fmt"
	"log"
	"math/big"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"golang.org/x/crypto/acme"
	"golang.org/x/crypto/acme/autocert"
)

// Certs liefert das Server-Zertifikat. Reihenfolge (Konzept §2.9 + Probe):
// Dateien (HORN_TLS_CERT/HORN_TLS_KEY oder /data/tls/fullchain.pem +
// privkey.pem) > selbst signiert (HORN_TLS_SELF=1) > ACME TLS-ALPN-01.
type Certs struct {
	Mode string // Start: "file", "self", "acme"; gekoppelte Knoten "wait" (Saga entscheidet: DNS-01 oder TLS-ALPN-01)
	Name string

	certFile, keyFile string
	mu                sync.RWMutex
	cur               *tls.Certificate
	sig               fileSig  // Stand der Dateien beim letzten Laden
	sum               [32]byte // SHA-256 ueber Zertifikat und Schluessel
	acme              *autocert.Manager
	alpn              bool             // P7: regulaere Handshakes mit SNI=Name ueber autocert (TLS-ALPN-01)
	fallback          *tls.Certificate // selbst signiert: fremde/fehlende SNI (Rueckpruefung), bis ein echtes da ist
	rsaOnly           bool             // ACME: nur das RSA-Zertifikat ausliefern (ECDSA liess sich nicht ausstellen)
	mode              string           // aktueller Modus (unter mu)
}

// CertOptions steuert die Zertifikatsquelle.
type CertOptions struct {
	Name              string
	CertFile, KeyFile string
	Self              bool
	ACMEDir           string // Cache-Verzeichnis
	ACMEURL           string // leer = Let's Encrypt
	ACMEEmail         string
	// Defer (P7, gekoppelte Knoten): ohne Dateien erst nur das Rueckfall-Zertifikat; Saga legt im Steuerkanal fest,
	// ob DNS-01 ueber Saga (UseFiles nach Ausstellung) oder TLS-ALPN-01 (UseALPN) gilt.
	Defer bool
}

// NewCerts waehlt die Quelle nach den Optionen.
func NewCerts(o CertOptions) (*Certs, error) {
	c := &Certs{Name: o.Name}
	switch {
	case o.CertFile != "" && fileExists(o.CertFile) && fileExists(o.KeyFile):
		c.Mode, c.mode, c.certFile, c.keyFile = "file", "file", o.CertFile, o.KeyFile
		if err := c.Reload(); err != nil {
			return nil, err
		}
	case o.Self:
		c.Mode = "self"
		crt, err := SelfSigned(o.Name)
		if err != nil {
			return nil, err
		}
		c.cur = crt
	case o.Name != "" && o.Name != "localhost":
		c.Mode = "acme"
		c.alpn = !o.Defer
		if o.Defer {
			c.Mode = "wait"
		}
		c.certFile, c.keyFile = o.CertFile, o.KeyFile
		m := &autocert.Manager{
			Prompt:     autocert.AcceptTOS,
			HostPolicy: autocert.HostWhitelist(o.Name),
			Cache:      autocert.DirCache(o.ACMEDir),
			Email:      o.ACMEEmail,
		}
		if o.ACMEURL != "" {
			m.Client = &acme.Client{DirectoryURL: o.ACMEURL}
		}
		c.acme = m
		fb, err := SelfSigned(o.Name)
		if err != nil {
			return nil, err
		}
		c.fallback = fb
	default:
		return nil, errors.New("kein Zertifikat: HORN_TLS_CERT/HORN_TLS_KEY, /data/tls/fullchain.pem, HORN_TLS_SELF=1 oder HORN_NAME fuer ACME setzen")
	}
	c.mode = c.Mode
	return c, nil
}

// ModeNow: aktueller Modus ("file", "self", "acme", "wait").
func (c *Certs) ModeNow() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.mode
}

// UseALPN: Zertifikat per TLS-ALPN-01 (autocert) fuer regulaere Handshakes mit SNI=Name.
func (c *Certs) UseALPN() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.acme != nil && c.mode == "wait" {
		c.alpn, c.mode = true, "acme"
	}
}

// PreferRSA: regulaere Handshakes bekommen das RSA-Zertifikat aus dem autocert-Cache, auch wenn der Client ECDSA
// kann. autocert fuehrt je Schluesseltyp ein eigenes Zertifikat; liess sich das ECDSA-Zertifikat nicht ausstellen
// (z. B. Let's-Encrypt-Grenze je Namen), haengt sonst jeder Handshake moderner Clients in einer neuen Bestellung.
func (c *Certs) PreferRSA(on bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.rsaOnly = on
}

// rsaHello: Kopie des Client-Hellos, fuer die autocert das RSA-Zertifikat waehlt (keine ECDSA-Angaben).
func rsaHello(h *tls.ClientHelloInfo) *tls.ClientHelloInfo {
	x := *h
	x.CipherSuites, x.SignatureSchemes, x.SupportedCurves = nil, nil, nil
	return &x
}

// ACME liefert den autocert-Manager (nil ausser im ACME-Modus).
func (c *Certs) ACME() *autocert.Manager { return c.acme }

// UseFiles: nach einer Ausstellung per DNS-01 die Dateien laden und ab jetzt nutzen (auch nach Neustart).
func (c *Certs) UseFiles(certFile, keyFile string) error {
	c.mu.Lock()
	c.certFile, c.keyFile, c.mode, c.alpn = certFile, keyFile, "file", false
	c.sig = fileSig{}
	c.mu.Unlock()
	_, err := c.reload(true)
	return err
}

// NotAfter: Ablauf des aktuellen Zertifikats (Null-Zeit ohne Zertifikat).
func (c *Certs) NotAfter() time.Time {
	c.mu.RLock()
	cur := c.cur
	c.mu.RUnlock()
	if cur == nil || len(cur.Certificate) == 0 {
		return time.Time{}
	}
	x, err := x509.ParseCertificate(cur.Certificate[0])
	if err != nil {
		return time.Time{}
	}
	return x.NotAfter
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

// fileSig ist der Stand beider Dateien (Aenderungszeit und Groesse).
type fileSig struct {
	m1, m2 time.Time
	s1, s2 int64
}

func (c *Certs) stat() (fileSig, error) {
	st1, err := os.Stat(c.certFile)
	if err != nil {
		return fileSig{}, err
	}
	st2, err := os.Stat(c.keyFile)
	if err != nil {
		return fileSig{}, err
	}
	return fileSig{st1.ModTime(), st2.ModTime(), st1.Size(), st2.Size()}, nil
}

// Reload liest die Zertifikatsdateien neu (SIGHUP oder Aenderung).
func (c *Certs) Reload() error {
	_, err := c.reload(true)
	return err
}

// ReloadIfChanged laedt nur, wenn sich Aenderungszeit, Groesse oder Inhalt
// geaendert haben. changed=true nur bei neuem Inhalt.
func (c *Certs) ReloadIfChanged() (changed bool, err error) { return c.reload(false) }

func (c *Certs) reload(force bool) (bool, error) {
	c.mu.RLock()
	m := c.mode
	c.mu.RUnlock()
	if m != "file" {
		return false, nil
	}
	sig, err := c.stat()
	if err != nil {
		return false, err
	}
	c.mu.RLock()
	same := sig == c.sig && c.cur != nil
	c.mu.RUnlock()
	if same && !force {
		return false, nil
	}
	cb, err := os.ReadFile(c.certFile)
	if err != nil {
		return false, err
	}
	kb, err := os.ReadFile(c.keyFile)
	if err != nil {
		return false, err
	}
	h := sha256.New()
	h.Write(cb)
	h.Write([]byte{0})
	h.Write(kb)
	var sum [32]byte
	h.Sum(sum[:0])
	c.mu.RLock()
	sameSum := sum == c.sum && c.cur != nil
	c.mu.RUnlock()
	if sameSum { // nur angefasst (touch, Kopie): nichts tun
		c.mu.Lock()
		c.sig = sig
		c.mu.Unlock()
		return false, nil
	}
	crt, err := tls.X509KeyPair(cb, kb)
	if err != nil {
		return false, fmt.Errorf("zertifikat laden: %w", err)
	}
	c.mu.Lock()
	c.cur = &crt
	c.sig = sig
	c.sum = sum
	c.mu.Unlock()
	return true, nil
}

// Watch prueft alle interval, ob sich die Dateien geaendert haben, und
// meldet nur echte Wechsel.
func (c *Certs) Watch(interval time.Duration, stop <-chan struct{}) {
	if c.Mode != "file" && c.Mode != "wait" && c.Mode != "acme" {
		return
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			changed, err := c.ReloadIfChanged()
			if err != nil {
				log.Printf("zertifikat: neu laden fehlgeschlagen: %v", err)
			} else if changed {
				log.Printf("zertifikat: neu geladen")
			}
		}
	}
}

// TLSConfig baut die Server-Konfiguration fuer die Weiche.
func (c *Certs) TLSConfig() *tls.Config {
	cfg := &tls.Config{
		MinVersion: tls.VersionTLS12,
		NextProtos: []string{"http/1.1", "stun.turn"},
	}
	if c.acme != nil {
		cfg.NextProtos = append(cfg.NextProtos, acme.ALPNProto)
	}
	cfg.GetCertificate = func(h *tls.ClientHelloInfo) (*tls.Certificate, error) {
		c.mu.RLock()
		mode, cur, alpn, fb, rsaOnly := c.mode, c.cur, c.alpn, c.fallback, c.rsaOnly
		c.mu.RUnlock()
		if c.acme != nil && (alpn || mode == "wait") && isALPNChallenge(h) && strings.EqualFold(h.ServerName, c.Name) {
			if !alpn {
				return nil, errors.New("tls-alpn-01 aus")
			}
			return c.acme.GetCertificate(h)
		}
		if mode == "file" || mode == "self" {
			if cur != nil || fb == nil {
				return cur, nil
			}
		}
		// fremde oder fehlende SNI (Rueckpruefung von Saga ueber die IP-Adresse): nie ACME ausloesen
		if fb != nil && !strings.EqualFold(h.ServerName, c.Name) {
			return fb, nil
		}
		if mode == "acme" && alpn {
			if rsaOnly {
				return c.acme.GetCertificate(rsaHello(h))
			}
			return c.acme.GetCertificate(h)
		}
		if cur != nil {
			return cur, nil
		}
		return fb, nil
	}
	return cfg
}

func isALPNChallenge(h *tls.ClientHelloInfo) bool {
	return len(h.SupportedProtos) == 1 && h.SupportedProtos[0] == acme.ALPNProto
}

// SelfSigned erzeugt ein kurzlebiges, selbst signiertes Zertifikat (lokale Tests).
func SelfSigned(name string) (*tls.Certificate, error) {
	if name == "" {
		name = "localhost"
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	serial, _ := rand.Int(rand.Reader, big.NewInt(1<<62))
	tpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: name},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(30 * 24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		DNSNames:     []string{name},
		IPAddresses:  []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("::1")},
	}
	if name != "localhost" {
		tpl.DNSNames = append(tpl.DNSNames, "localhost")
	}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		return nil, err
	}
	return &tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}, nil
}
