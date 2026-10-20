package netx

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
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
	mtime             time.Time
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

// Reload liest die Zertifikatsdateien neu (SIGHUP oder Aenderung).
func (c *Certs) Reload() error {
	if c.Mode != "file" {
		return nil
	}
	st, err := os.Stat(c.certFile)
	if err != nil {
		return err
	}
	crt, err := tls.LoadX509KeyPair(c.certFile, c.keyFile)
	if err != nil {
		return fmt.Errorf("zertifikat laden: %w", err)
	}
	c.mu.Lock()
	c.cur = &crt
	c.mtime = st.ModTime()
	c.mu.Unlock()
	return nil
}

// Watch prueft alle interval, ob sich die Dateien geaendert haben.
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
			st1, e1 := os.Stat(c.certFile)
			st2, e2 := os.Stat(c.keyFile)
			if e1 != nil || e2 != nil {
				continue
			}
			c.mu.RLock()
			old := c.mtime
			c.mu.RUnlock()
			mt := st1.ModTime()
			if st2.ModTime().After(mt) {
				mt = st2.ModTime()
			}
			if mt.After(old) {
				if err := c.Reload(); err != nil {
					log.Printf("zertifikat: neu laden fehlgeschlagen: %v", err)
				} else {
					log.Printf("zertifikat: neu geladen")
				}
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
