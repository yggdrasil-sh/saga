// Package acmefake ist ein kleiner ACME-Testserver (RFC 8555, nur was golang.org/x/crypto/acme braucht) fuer
// Gjallarhorn-Tests ohne Let's Encrypt (Paket P7). Er prueft dns-01 ueber eine Rueckruf-Funktion (Tests: der lokale
// Fake-Cloudflare) und tls-alpn-01 durch einen echten TLS-Handshake mit ALPN acme-tls/1 zum Knoten. Signaturen der
// JWS werden NICHT geprueft - nur fuer Tests.
package acmefake

import (
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Server ist der Fake-CA. Base muss vor dem ersten Abruf gesetzt sein (z. B. http://127.0.0.1:1234).
type Server struct {
	Base string
	// TXT liefert die TXT-Werte eines Namens (dns-01).
	TXT func(name string) []string
	// Dial verbindet zum Knoten fuer tls-alpn-01 (Standard: name:443).
	Dial func(name string) (net.Conn, error)

	mu       sync.Mutex
	n        int
	accounts map[string]string // kid -> Thumbprint
	thumbs   map[string]string // Thumbprint -> kid
	orders   map[string]*order
	authzs   map[string]*authz
	chals    map[string]*chal
	certs    map[string][]byte
	caKey    *ecdsa.PrivateKey
	caCert   *x509.Certificate
	caDER    []byte
	Issued   int      // ausgestellte Zertifikate
	MaxOrder int      // >0: ab so vielen ausgestellten Zertifikaten neue Bestellungen ablehnen (Grenze wie bei Let's Encrypt)
	Log      []string // Pruefungen (Tests)
}

type order struct {
	ID, Status, Name, Cert string
	Authz                  []string
	kid                    string
}

type authz struct {
	ID, Status, Name string
	Chals            []string
}

type chal struct {
	ID, Type, Token, Status, Authz, kid string
}

// New erzeugt den Server mit eigener Test-CA.
func New() *Server {
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Saga Test-ACME CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(10 * 365 * 24 * time.Hour), IsCA: true,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, _ := x509.CreateCertificate(rand.Reader, tpl, tpl, &k.PublicKey, k)
	c, _ := x509.ParseCertificate(der)
	return &Server{accounts: map[string]string{}, thumbs: map[string]string{}, orders: map[string]*order{}, authzs: map[string]*authz{},
		chals: map[string]*chal{}, certs: map[string][]byte{}, caKey: k, caCert: c, caDER: der}
}

// Snapshot: ausgestellte Zertifikate und Pruefprotokoll.
func (s *Server) Snapshot() (int, []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.Issued, append([]string(nil), s.Log...)
}

// CAPEM: Wurzel der Test-CA.
func (s *Server) CAPEM() []byte {
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.caDER})
}

func (s *Server) id() string {
	s.n++
	return fmt.Sprintf("x%d", s.n)
}

func (s *Server) nonce(w http.ResponseWriter) {
	b := make([]byte, 12)
	_, _ = rand.Read(b)
	w.Header().Set("Replay-Nonce", base64.RawURLEncoding.EncodeToString(b))
	w.Header().Set("Cache-Control", "no-store")
}

func (s *Server) problem(w http.ResponseWriter, code int, typ, detail string) {
	s.nonce(w)
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]any{"type": "urn:ietf:params:acme:error:" + typ, "detail": detail, "status": code})
}

func (s *Server) js(w http.ResponseWriter, code int, loc string, v any) {
	s.nonce(w)
	if loc != "" {
		w.Header().Set("Location", loc)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

type jws struct {
	Protected, Payload, Signature string
}

type protected struct {
	Alg, Nonce, URL, Kid string
	JWK                  map[string]any `json:"jwk"`
}

func decodeJWS(r *http.Request) (protected, []byte, error) {
	var j jws
	var p protected
	if err := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<20)).Decode(&j); err != nil {
		return p, nil, err
	}
	hb, err := base64.RawURLEncoding.DecodeString(j.Protected)
	if err != nil {
		return p, nil, err
	}
	if err := json.Unmarshal(hb, &p); err != nil {
		return p, nil, err
	}
	pl, err := base64.RawURLEncoding.DecodeString(j.Payload)
	return p, pl, err
}

// thumbprint nach RFC 7638 (EC und RSA).
func thumbprint(jwk map[string]any) (string, error) {
	var keys []string
	switch jwk["kty"] {
	case "EC":
		keys = []string{"crv", "kty", "x", "y"}
	case "RSA":
		keys = []string{"e", "kty", "n"}
	default:
		return "", fmt.Errorf("kty %v", jwk["kty"])
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		v, _ := jwk[k].(string)
		b, _ := json.Marshal(v)
		parts = append(parts, fmt.Sprintf("%q:%s", k, b))
	}
	h := sha256.Sum256([]byte("{" + strings.Join(parts, ",") + "}"))
	return base64.RawURLEncoding.EncodeToString(h[:]), nil
}

// ServeHTTP: Verzeichnis unter /dir.
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	p := r.URL.Path
	switch {
	case p == "/dir":
		s.js(w, 200, "", map[string]any{"newNonce": s.Base + "/nonce", "newAccount": s.Base + "/acct", "newOrder": s.Base + "/order",
			"revokeCert": s.Base + "/revoke", "keyChange": s.Base + "/key", "meta": map[string]any{}})
		return
	case p == "/nonce":
		s.nonce(w)
		w.WriteHeader(http.StatusOK)
		return
	}
	if r.Method != http.MethodPost {
		s.problem(w, 405, "malformed", "POST erwartet")
		return
	}
	hd, pl, err := decodeJWS(r)
	if err != nil {
		s.problem(w, 400, "malformed", err.Error())
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	kid := hd.Kid
	switch {
	case p == "/acct":
		tp, err := thumbprint(hd.JWK)
		if err != nil {
			s.problem(w, 400, "badPublicKey", err.Error())
			return
		}
		if k, ok := s.thumbs[tp]; ok {
			s.js(w, 200, k, map[string]any{"status": "valid"})
			return
		}
		k := s.Base + "/acct/" + s.id()
		s.accounts[k], s.thumbs[tp] = tp, k
		s.js(w, 201, k, map[string]any{"status": "valid"})
	case p == "/order":
		if _, ok := s.accounts[kid]; !ok {
			s.problem(w, 400, "accountDoesNotExist", "kid unbekannt")
			return
		}
		if s.MaxOrder > 0 && s.Issued >= s.MaxOrder {
			s.problem(w, 429, "rateLimited", "zu viele Zertifikate fuer diesen Namen")
			return
		}
		var in struct {
			Identifiers []struct{ Type, Value string }
		}
		if json.Unmarshal(pl, &in) != nil || len(in.Identifiers) != 1 || in.Identifiers[0].Type != "dns" {
			s.problem(w, 400, "malformed", "genau ein dns-Name")
			return
		}
		name := strings.ToLower(in.Identifiers[0].Value)
		o := &order{ID: s.id(), Status: "pending", Name: name, kid: kid}
		a := &authz{ID: s.id(), Status: "pending", Name: name}
		for _, t := range []string{"dns-01", "tls-alpn-01"} {
			tb := make([]byte, 16)
			_, _ = rand.Read(tb)
			c := &chal{ID: s.id(), Type: t, Token: base64.RawURLEncoding.EncodeToString(tb), Status: "pending", Authz: a.ID, kid: kid}
			s.chals[c.ID] = c
			a.Chals = append(a.Chals, c.ID)
		}
		s.authzs[a.ID] = a
		o.Authz = []string{a.ID}
		s.orders[o.ID] = o
		s.js(w, 201, s.Base+"/o/"+o.ID, s.orderJSON(o))
	case strings.HasPrefix(p, "/o/"):
		o := s.orders[strings.TrimPrefix(p, "/o/")]
		if o == nil || o.kid != kid {
			s.problem(w, 404, "malformed", "order")
			return
		}
		s.refresh(o)
		s.js(w, 200, s.Base+"/o/"+o.ID, s.orderJSON(o))
	case strings.HasPrefix(p, "/a/"):
		a := s.authzs[strings.TrimPrefix(p, "/a/")]
		if a == nil {
			s.problem(w, 404, "malformed", "authz")
			return
		}
		s.js(w, 200, "", s.authzJSON(a))
	case strings.HasPrefix(p, "/c/"):
		c := s.chals[strings.TrimPrefix(p, "/c/")]
		if c == nil || c.kid != kid {
			s.problem(w, 404, "malformed", "chal")
			return
		}
		if c.Status == "pending" && string(pl) != "" {
			s.validate(c)
		}
		s.js(w, 200, "", s.chalJSON(c))
	case strings.HasPrefix(p, "/f/"):
		o := s.orders[strings.TrimPrefix(p, "/f/")]
		if o == nil || o.kid != kid {
			s.problem(w, 404, "malformed", "order")
			return
		}
		s.refresh(o)
		if o.Status != "ready" {
			s.problem(w, 403, "orderNotReady", o.Status)
			return
		}
		var in struct{ CSR string }
		_ = json.Unmarshal(pl, &in)
		der, err := base64.RawURLEncoding.DecodeString(in.CSR)
		var csr *x509.CertificateRequest
		if err == nil {
			csr, err = x509.ParseCertificateRequest(der)
		}
		if err != nil || len(csr.DNSNames) != 1 || !strings.EqualFold(csr.DNSNames[0], o.Name) {
			s.problem(w, 400, "badCSR", "CSR passt nicht zum Auftrag")
			return
		}
		tpl := &x509.Certificate{SerialNumber: big.NewInt(int64(1000 + s.n)), Subject: pkix.Name{CommonName: o.Name},
			DNSNames: []string{o.Name}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().Add(90 * 24 * time.Hour),
			KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
		cd, err := x509.CreateCertificate(rand.Reader, tpl, s.caCert, csr.PublicKey, crypto.Signer(s.caKey))
		if err != nil {
			s.problem(w, 500, "serverInternal", err.Error())
			return
		}
		id := s.id()
		s.certs[id] = append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cd}), s.CAPEM()...)
		o.Status, o.Cert = "valid", id
		s.Issued++
		s.js(w, 200, s.Base+"/o/"+o.ID, s.orderJSON(o))
	case strings.HasPrefix(p, "/cert/"):
		b := s.certs[strings.TrimPrefix(p, "/cert/")]
		if b == nil {
			s.problem(w, 404, "malformed", "cert")
			return
		}
		s.nonce(w)
		w.Header().Set("Content-Type", "application/pem-certificate-chain")
		_, _ = w.Write(b)
	default:
		s.problem(w, 404, "malformed", p)
	}
}

func (s *Server) refresh(o *order) {
	if o.Status != "pending" {
		return
	}
	for _, id := range o.Authz {
		if a := s.authzs[id]; a == nil || a.Status != "valid" {
			if a != nil && a.Status == "invalid" {
				o.Status = "invalid"
			}
			return
		}
	}
	o.Status = "ready"
}

func (s *Server) keyAuth(c *chal) string { return c.Token + "." + s.accounts[c.kid] }

func (s *Server) validate(c *chal) {
	a := s.authzs[c.Authz]
	ka := s.keyAuth(c)
	h := sha256.Sum256([]byte(ka))
	ok := false
	var why string
	switch c.Type {
	case "dns-01":
		want := base64.RawURLEncoding.EncodeToString(h[:])
		var got []string
		if s.TXT != nil {
			s.mu.Unlock()
			got = s.TXT("_acme-challenge." + a.Name)
			s.mu.Lock()
		}
		for _, g := range got {
			ok = ok || g == want
		}
		why = fmt.Sprintf("dns-01 %s: %d Werte", a.Name, len(got))
	case "tls-alpn-01":
		s.mu.Unlock()
		ok, why = s.checkALPN(a.Name, h[:])
		s.mu.Lock()
	}
	s.Log = append(s.Log, fmt.Sprintf("%s ok=%v %s", c.Type, ok, why))
	if ok {
		c.Status, a.Status = "valid", "valid"
	} else {
		c.Status, a.Status = "invalid", "invalid"
	}
}

var oidACME = asn1.ObjectIdentifier{1, 3, 6, 1, 5, 5, 7, 1, 31}

func (s *Server) checkALPN(name string, sum []byte) (bool, string) {
	dial := s.Dial
	if dial == nil {
		dial = func(n string) (net.Conn, error) {
			return net.DialTimeout("tcp", net.JoinHostPort(n, "443"), 5*time.Second)
		}
	}
	raw, err := dial(name)
	if err != nil {
		return false, err.Error()
	}
	defer raw.Close()
	_ = raw.SetDeadline(time.Now().Add(10 * time.Second))
	tc := tls.Client(raw, &tls.Config{ServerName: name, NextProtos: []string{"acme-tls/1"}, InsecureSkipVerify: true}) //nolint:gosec // Pruefung ueber die Erweiterung
	if err := tc.Handshake(); err != nil {
		return false, "handshake: " + err.Error()
	}
	st := tc.ConnectionState()
	if st.NegotiatedProtocol != "acme-tls/1" || len(st.PeerCertificates) == 0 {
		return false, "kein acme-tls/1"
	}
	leaf := st.PeerCertificates[0]
	if len(leaf.DNSNames) != 1 || !strings.EqualFold(leaf.DNSNames[0], name) {
		return false, "falscher Name"
	}
	for _, e := range leaf.Extensions {
		if e.Id.Equal(oidACME) {
			var v []byte
			if _, err := asn1.Unmarshal(e.Value, &v); err == nil && string(v) == string(sum) {
				return true, "tls-alpn-01 " + name
			}
			return false, "falscher Wert"
		}
	}
	return false, "keine acmeIdentifier-Erweiterung"
}

func (s *Server) orderJSON(o *order) map[string]any {
	az := make([]string, 0, len(o.Authz))
	for _, id := range o.Authz {
		az = append(az, s.Base+"/a/"+id)
	}
	m := map[string]any{"status": o.Status, "expires": time.Now().Add(time.Hour).UTC().Format(time.RFC3339),
		"identifiers": []map[string]string{{"type": "dns", "value": o.Name}}, "authorizations": az, "finalize": s.Base + "/f/" + o.ID}
	if o.Cert != "" {
		m["certificate"] = s.Base + "/cert/" + o.Cert
	}
	return m
}

func (s *Server) authzJSON(a *authz) map[string]any {
	cs := []map[string]any{}
	for _, id := range a.Chals {
		cs = append(cs, s.chalJSON(s.chals[id]))
	}
	return map[string]any{"status": a.Status, "identifier": map[string]string{"type": "dns", "value": a.Name},
		"expires": time.Now().Add(time.Hour).UTC().Format(time.RFC3339), "challenges": cs}
}

func (s *Server) chalJSON(c *chal) map[string]any {
	return map[string]any{"type": c.Type, "url": s.Base + "/c/" + c.ID, "token": c.Token, "status": c.Status}
}
