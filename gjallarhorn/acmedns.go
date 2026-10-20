package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"golang.org/x/crypto/acme"
)

// Zertifikate gekoppelter Knoten (Konzept v2 §2.9, Paket P7). Saga legt im Steuerkanal den Weg fest:
//   - dns01: Cloudflare-Token in Saga vorhanden. Der Knoten macht ACME selbst (eigener Konto- und Zertifikatsschluessel),
//     den TXT-Eintrag _acme-challenge.<eigener Name> setzt Saga auf Anfrage (acme_txt) - nur fuer diesen Namen. Der
//     Cloudflare-Token verlaesst Saga nie. Ergebnis in HORN_DATA/tls/fullchain.pem + privkey.pem (+ dns01.json).
//   - alpn: kein Token. TLS-ALPN-01 ueber autocert auf demselben 443/TCP, ohne Port 80.
//   - eigenes Zertifikat (Dateien ohne dns01.json): nie anfassen.
// Erneuerung 30 Tage vor Ablauf; Fehler meldet der Knoten (cert {state, err}), Saga zeigt sie im Admin-Bereich.

const renewBefore = 30 * 24 * time.Hour

func (s *server) certMode(mode string) {
	if mode != "dns01" && mode != "alpn" {
		return
	}
	if p := s.certModeV.Load(); p != nil && *p == mode {
		s.reportCert()
		return
	}
	s.certModeV.Store(&mode)
	cur := s.certs.ModeNow()
	own := cur == "file" && !fileExists(filepath.Join(s.cfg.Data, "tls", "dns01.json"))
	switch {
	case cur == "self" || own:
		// Testzertifikat bzw. eigenes Zertifikat: nichts tun
	case mode == "alpn":
		s.certs.UseALPN()
		go s.alpnWarm()
	case mode == "dns01" && s.acmeRun.CompareAndSwap(false, true):
		go s.dns01Loop()
	}
	s.reportCert()
}

// alpnWarm: Zertifikat per TLS-ALPN-01 gleich holen (nicht erst beim ersten Geraet).
func (s *server) alpnWarm() {
	m := s.certs.ACME()
	if m == nil {
		return
	}
	for i := 0; i < 6 && !s.closing.Load(); i++ {
		crt, err := m.GetCertificate(&tls.ClientHelloInfo{ServerName: s.cfg.Name, SupportedProtos: []string{"http/1.1"}})
		if err == nil && crt != nil {
			if x, err := x509.ParseCertificate(crt.Certificate[0]); err == nil {
				log.Printf("zertifikat: tls-alpn-01 ok, gueltig bis %s", x.NotAfter.Format(time.DateOnly))
				na := x.NotAfter.Unix()
				e := ""
				s.certErr.Store(&e)
				if c := s.ctlc(); c != nil {
					c.send(map[string]any{"t": "cert", "mode": "alpn", "state": "ok", "not_after": na})
				}
			}
			return
		}
		msg := "tls-alpn-01: " + err.Error()
		s.certErr.Store(&msg)
		log.Printf("zertifikat: %s", msg)
		s.reportCert()
		time.Sleep(time.Duration(30<<i) * time.Second)
	}
}

func (s *server) dns01Loop() {
	defer s.acmeRun.Store(false)
	back := 5 * time.Minute
	for !s.closing.Load() {
		if p := s.certModeV.Load(); p == nil || *p != "dns01" {
			return
		}
		na := s.certs.NotAfter()
		if s.certs.ModeNow() == "file" && time.Until(na) > renewBefore {
			time.Sleep(minDur(12*time.Hour, time.Until(na)-renewBefore))
			continue
		}
		err := s.obtainDNS01()
		if err == nil {
			back = 5 * time.Minute
			e := ""
			s.certErr.Store(&e)
			s.reportCert()
			continue
		}
		msg := "dns-01: " + err.Error()
		s.certErr.Store(&msg)
		log.Printf("zertifikat: %s - neuer versuch in %v", msg, back)
		s.reportCert()
		time.Sleep(back)
		if back *= 2; back > 6*time.Hour {
			back = 6 * time.Hour
		}
	}
}

func minDur(a, b time.Duration) time.Duration {
	if b < time.Minute {
		b = time.Minute
	}
	if a < b {
		return a
	}
	return b
}

func loadECKey(p string) (*ecdsa.PrivateKey, error) {
	if b, err := os.ReadFile(p); err == nil {
		blk, _ := pem.Decode(b)
		if blk == nil {
			return nil, errors.New(p + ": kein PEM")
		}
		return x509.ParseECPrivateKey(blk.Bytes)
	}
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, err
	}
	der, _ := x509.MarshalECPrivateKey(k)
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return nil, err
	}
	return k, os.WriteFile(p, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: der}), 0o600)
}

func (s *server) obtainDNS01() error {
	c := s.ctlc()
	if c == nil || !c.connected() {
		return errors.New("steuerung nicht verbunden")
	}
	dir := filepath.Join(s.cfg.Data, "tls")
	ak, err := loadECKey(filepath.Join(dir, "acme", "dns01-account.key"))
	if err != nil {
		return err
	}
	url := os.Getenv("HORN_ACME_URL")
	if url == "" {
		url = acme.LetsEncryptURL
	}
	cl := &acme.Client{Key: ak, DirectoryURL: url, UserAgent: "gjallarhorn/" + version}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	acct := &acme.Account{}
	if e := os.Getenv("HORN_ACME_EMAIL"); e != "" {
		acct.Contact = []string{"mailto:" + e}
	}
	if _, err := cl.Register(ctx, acct, acme.AcceptTOS); err != nil && !errors.Is(err, acme.ErrAccountAlreadyExists) {
		return fmt.Errorf("konto: %w", err)
	}
	o, err := cl.AuthorizeOrder(ctx, acme.DomainIDs(s.cfg.Name))
	if err != nil {
		return fmt.Errorf("auftrag: %w", err)
	}
	txt := "_acme-challenge." + s.cfg.Name
	set := false
	defer func() {
		if set {
			_, _ = c.request(map[string]any{"t": "acme_txt", "op": "del", "name": txt}, 30*time.Second)
		}
	}()
	for _, u := range o.AuthzURLs {
		z, err := cl.GetAuthorization(ctx, u)
		if err != nil {
			return err
		}
		if z.Status == acme.StatusValid {
			continue
		}
		var ch *acme.Challenge
		for _, x := range z.Challenges {
			if x.Type == "dns-01" {
				ch = x
			}
		}
		if ch == nil {
			return errors.New("CA bietet kein dns-01")
		}
		val, err := cl.DNS01ChallengeRecord(ch.Token)
		if err != nil {
			return err
		}
		r, err := c.request(map[string]any{"t": "acme_txt", "op": "set", "name": txt, "value": val}, 60*time.Second)
		if err != nil {
			return err
		}
		if !r.OK {
			return fmt.Errorf("saga setzt TXT nicht: %s", r.Err)
		}
		set = true
		wait, _ := strconv.Atoi(env("HORN_ACME_DNS_WAIT", "20")) // Verteilung im DNS
		time.Sleep(time.Duration(wait) * time.Second)
		if _, err := cl.Accept(ctx, ch); err != nil {
			return fmt.Errorf("pruefung: %w", err)
		}
		if _, err := cl.WaitAuthorization(ctx, z.URI); err != nil {
			return fmt.Errorf("pruefung: %w", err)
		}
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	csr, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: s.cfg.Name},
		DNSNames: []string{s.cfg.Name}}, key)
	if err != nil {
		return err
	}
	if o, err = cl.WaitOrder(ctx, o.URI); err != nil {
		return fmt.Errorf("auftrag: %w", err)
	}
	ders, _, err := cl.CreateOrderCert(ctx, o.FinalizeURL, csr, true)
	if err != nil {
		return fmt.Errorf("ausstellen: %w", err)
	}
	var chain []byte
	for _, d := range ders {
		chain = append(chain, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: d})...)
	}
	kd, _ := x509.MarshalECPrivateKey(key)
	cf, kf := filepath.Join(dir, "fullchain.pem"), filepath.Join(dir, "privkey.pem")
	if err := writeAtomic(kf, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kd}), 0o600); err != nil {
		return err
	}
	if err := writeAtomic(cf, chain, 0o644); err != nil {
		return err
	}
	meta, _ := json.Marshal(map[string]any{"name": s.cfg.Name, "at": time.Now().UTC().Format(time.RFC3339), "ca": url})
	_ = writeAtomic(filepath.Join(dir, "dns01.json"), meta, 0o600)
	if err := s.certs.UseFiles(cf, kf); err != nil {
		return err
	}
	log.Printf("zertifikat: dns-01 ok, gueltig bis %s", s.certs.NotAfter().Format(time.DateOnly))
	return nil
}

func writeAtomic(p string, b []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(p), 0o700); err != nil {
		return err
	}
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, b, mode); err != nil {
		return err
	}
	return os.Rename(tmp, p)
}
