package main

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// Zertifikat des TLS-Eingangs (2.5 "Ein Port", Konzept §5). saga-media fordert selbst nichts an: Es liest Dateien und
// laedt sie ohne Neustart neu (Pruefung alle SAGA_TLS_RELOAD_S, Standard 30 s). Quellen:
//   - SAGA_TLS_CERT/SAGA_TLS_KEY: eigene Dateien (z. B. certbot) - nur diese.
//   - sonst die Dateien des eingebauten Knotens, der den Antrag fuer den Saga-Namen stellt: HORN_DATA/tls/fullchain.pem +
//     privkey.pem (DNS-01 ueber den Cloudflare-Token in Saga) bzw. der autocert-Speicher HORN_DATA/tls/acme/<name>
//     (TLS-ALPN-01; Schluessel und Kette in einer Datei, ECDSA bzw. "+rsa").
// Es gilt nur ein Zertifikat, das jetzt gueltig ist und auf den Namen aus BASE_URL passt - sonst bleibt die Weiche fuer
// HTTPS zu (HSTS: ein Browser, der Saga kennt, laesst ein falsches Zertifikat nicht einmal wegklicken).

// certState: geladenes Zertifikat bzw. warum keins gilt.
type certState struct {
	cert     *tls.Certificate
	notAfter time.Time
	src      string // own | dns01 | alpn | file (Datei des Knotens ohne Kennzeichen)
	state    string // ok | fehlt | name (passt nicht zum Namen) | abgelaufen | fehler (Datei unlesbar)
}

type edgeCert struct {
	name, own, ownKey, data string
	every                   time.Duration
	cur                     atomic.Pointer[certState]
	mu                      sync.Mutex
	sig                     string // Stand der Dateien beim letzten Laden (Groesse + mtime)
	onChange                func(*certState)
}

func newEdgeCert(c *config) *edgeCert {
	e := &edgeCert{name: c.baseHost, own: c.tlsCert, ownKey: c.tlsKey, data: c.hornData, every: c.tlsReload}
	if e.every <= 0 {
		e.every = 30 * time.Second
	}
	e.cur.Store(&certState{state: "fehlt"})
	return e
}

func (e *edgeCert) state() *certState { return e.cur.Load() }

// ok: es gibt ein gueltiges Zertifikat fuer den Namen (Weiche fuer HTTPS offen).
func (e *edgeCert) ok() bool {
	s := e.cur.Load()
	return s.cert != nil && time.Now().Before(s.notAfter)
}

// get fuer tls.Config.GetCertificate.
func (e *edgeCert) get(*tls.ClientHelloInfo) (*tls.Certificate, error) {
	s := e.cur.Load()
	if s.cert == nil || !time.Now().Before(s.notAfter) {
		return nil, errors.New("kein gueltiges Zertifikat")
	}
	return s.cert, nil
}

type certFile struct{ cert, key, src string }

func (e *edgeCert) files() []certFile {
	if e.own != "" {
		return []certFile{{e.own, e.ownKey, "own"}}
	}
	tlsDir := filepath.Join(e.data, "tls")
	src := "file"
	if _, err := os.Stat(filepath.Join(tlsDir, "dns01.json")); err == nil {
		src = "dns01"
	}
	acme := filepath.Join(tlsDir, "acme", e.name)
	return []certFile{{filepath.Join(tlsDir, "fullchain.pem"), filepath.Join(tlsDir, "privkey.pem"), src},
		{acme, acme, "alpn"}, {acme + "+rsa", acme + "+rsa", "alpn"}}
}

func fileStamp(p string) string {
	st, err := os.Stat(p)
	if err != nil {
		return "-"
	}
	return st.ModTime().Format(time.RFC3339Nano) + "/" + itoa(st.Size())
}

// reload liest die Quellen, wenn sich eine Datei geaendert hat (oder das Zertifikat inzwischen abgelaufen ist), und nimmt
// das passende Zertifikat mit dem spaetesten Ablauf. -> true, wenn sich der Zustand geaendert hat.
func (e *edgeCert) reload() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	fs := e.files()
	sig := ""
	for _, f := range fs {
		sig += fileStamp(f.cert) + "|" + fileStamp(f.key) + ";"
	}
	old := e.cur.Load()
	now := time.Now()
	if sig == e.sig && (old.cert == nil || now.Before(old.notAfter)) {
		return false
	}
	e.sig = sig
	best := &certState{state: "fehlt"}
	rank := map[string]int{"fehlt": 0, "fehler": 1, "abgelaufen": 2, "name": 3}
	for _, f := range fs {
		cb, err := os.ReadFile(f.cert)
		if err != nil {
			continue
		}
		kb, err := os.ReadFile(f.key)
		if err != nil {
			continue
		}
		crt, err := tls.X509KeyPair(cb, kb) // autocert: Schluessel und Kette in einer Datei - X509KeyPair nimmt je den passenden Block
		if err != nil || len(crt.Certificate) == 0 {
			if rank["fehler"] > rank[best.state] {
				best = &certState{state: "fehler", src: f.src}
			}
			continue
		}
		x, err := x509.ParseCertificate(crt.Certificate[0])
		if err != nil {
			continue
		}
		st := "ok"
		switch {
		case x.VerifyHostname(e.name) != nil:
			st = "name"
		case !now.Before(x.NotAfter) || now.Before(x.NotBefore):
			st = "abgelaufen"
		}
		if st != "ok" {
			if best.cert == nil && rank[st] > rank[best.state] {
				best = &certState{state: st, src: f.src, notAfter: x.NotAfter}
			}
			continue
		}
		if best.cert == nil || x.NotAfter.After(best.notAfter) {
			c := crt
			c.Leaf = x
			best = &certState{cert: &c, notAfter: x.NotAfter, src: f.src, state: "ok"}
		}
	}
	e.cur.Store(best)
	changed := old.state != best.state || !old.notAfter.Equal(best.notAfter) || old.src != best.src
	if changed {
		switch {
		case best.state == "ok":
			logf("TLS-Eingang: Zertifikat fuer %s (%s) gueltig bis %s", e.name, best.src, best.notAfter.Format(time.DateOnly))
		case old.state == "ok" || old.state == "fehlt" && best.state != "fehlt":
			logf("TLS-Eingang: kein gueltiges Zertifikat fuer %s (%s) - HTTPS bleibt zu", e.name, best.state)
		}
		if e.onChange != nil {
			e.onChange(best)
		}
	}
	return changed
}

func (e *edgeCert) watch(stop <-chan struct{}) {
	t := time.NewTicker(e.every)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			e.reload()
		}
	}
}
