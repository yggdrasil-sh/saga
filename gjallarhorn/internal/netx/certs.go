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
	"sync"
	"time"

	"golang.org/x/crypto/acme"
	"golang.org/x/crypto/acme/autocert"
)

// Certs liefert das Server-Zertifikat. Reihenfolge (Konzept §2.9 + Probe):
// Dateien (HORN_TLS_CERT/HORN_TLS_KEY oder /data/tls/fullchain.pem +
// privkey.pem) > selbst signiert (HORN_TLS_SELF=1) > ACME TLS-ALPN-01.
type Certs struct {
	Mode string // "file", "self", "acme"
	Name string

	certFile, keyFile string
	mu                sync.RWMutex
	cur               *tls.Certificate
	sig               fileSig  // Stand der Dateien beim letzten Laden
	sum               [32]byte // SHA-256 ueber Zertifikat und Schluessel
	acme              *autocert.Manager
}

// CertOptions steuert die Zertifikatsquelle.
type CertOptions struct {
	Name              string
	CertFile, KeyFile string
	Self              bool
	ACMEDir           string // Cache-Verzeichnis
	ACMEURL           string // leer = Let's Encrypt
	ACMEEmail         string
}

// NewCerts waehlt die Quelle nach den Optionen.
func NewCerts(o CertOptions) (*Certs, error) {
	c := &Certs{Name: o.Name}
	switch {
	case o.CertFile != "" && fileExists(o.CertFile) && fileExists(o.KeyFile):
		c.Mode, c.certFile, c.keyFile = "file", o.CertFile, o.KeyFile
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
	default:
		return nil, errors.New("kein Zertifikat: HORN_TLS_CERT/HORN_TLS_KEY, /data/tls/fullchain.pem, HORN_TLS_SELF=1 oder HORN_NAME fuer ACME setzen")
	}
	return c, nil
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
	if c.Mode != "file" {
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
	if c.Mode != "file" {
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
		cfg.GetCertificate = func(h *tls.ClientHelloInfo) (*tls.Certificate, error) {
			if h.ServerName == "" {
				h.ServerName = c.Name
			}
			return c.acme.GetCertificate(h)
		}
		return cfg
	}
	cfg.GetCertificate = func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
		c.mu.RLock()
		defer c.mu.RUnlock()
		return c.cur, nil
	}
	return cfg
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
