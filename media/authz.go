package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"sync"
	"time"
)

// grant: Python hat den Abruf erlaubt (je Datei und Sitzung, hoechstens ttl lang gemerkt).
type grant struct {
	exp     time.Time
	headers map[string]string // Cache-Control, Content-Disposition, CSP ... genau wie die Python-Antwort
}

type flight struct {
	done chan struct{}
	g    *grant
}

// authz: Berechtigung bleibt in Python (/internal/media/authz). Ergebnis je (Datei, Sitzung) gemerkt; parallele
// Anfragen mit demselben Schluessel warten auf eine einzige Abfrage. Ablehnungen merken wir nicht - die eigentliche
// Anfrage geht dann an Python und bekommt dort genau die bisherige Antwort (401/404/Umleitung).
type authz struct {
	c       *config
	client  *http.Client
	mu      sync.Mutex
	cache   map[[32]byte]*grant
	pending map[[32]byte]*flight
	now     func() time.Time
}

const cacheMax = 20000

func newAuthz(c *config, tr http.RoundTripper) *authz {
	return &authz{c: c, client: &http.Client{Transport: tr, Timeout: 60 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		cache: map[[32]byte]*grant{}, pending: map[[32]byte]*flight{}, now: time.Now}
}

// cacheKey: alles, was die Entscheidung in Python beeinflusst - Datei, Anmeldung (Cookie, Authorization),
// der Pflicht-Kopf X-Saga und der Hostname (Umleitung auf BASE_URL).
func cacheKey(fid string, r *http.Request) [32]byte {
	h := sha256.New()
	put := func(s string) { _, _ = io.WriteString(h, s); _, _ = h.Write([]byte{0}) }
	put(fid)
	put(r.Host)
	for _, v := range r.Header.Values("X-Forwarded-Host") {
		put(v)
	}
	for _, v := range r.Header.Values("Cookie") {
		put(v)
	}
	put(r.Header.Get("Authorization"))
	put(r.Header.Get("X-Saga"))
	put(r.Header.Get("X-Hlidskjalf"))
	var k [32]byte
	copy(k[:], h.Sum(nil))
	return k
}

func (a *authz) check(r *http.Request, fid string) *grant {
	if a.c.ttl <= 0 {
		return a.ask(r, fid)
	}
	k := cacheKey(fid, r)
	a.mu.Lock()
	if g := a.cache[k]; g != nil {
		if a.now().Before(g.exp) {
			a.mu.Unlock()
			return g
		}
		delete(a.cache, k)
	}
	if f := a.pending[k]; f != nil {
		a.mu.Unlock()
		select {
		case <-f.done:
			return f.g
		case <-r.Context().Done():
			return nil
		}
	}
	f := &flight{done: make(chan struct{})}
	a.pending[k] = f
	a.mu.Unlock()

	g := a.ask(r, fid)

	a.mu.Lock()
	delete(a.pending, k)
	if g != nil {
		if len(a.cache) >= cacheMax {
			a.prune()
		}
		a.cache[k] = g
	}
	f.g = g
	close(f.done)
	a.mu.Unlock()
	return g
}

// forget: Datei fehlt auf der Platte - gemerkte Erlaubnisse fuer diese Anfrage verwerfen.
func (a *authz) forget(r *http.Request, fid string) {
	k := cacheKey(fid, r)
	a.mu.Lock()
	delete(a.cache, k)
	a.mu.Unlock()
}

func (a *authz) prune() {
	now := a.now()
	for k, g := range a.cache {
		if !now.Before(g.exp) {
			delete(a.cache, k)
		}
	}
	if len(a.cache) >= cacheMax {
		a.cache = map[[32]byte]*grant{}
	}
}

type authzReply struct {
	OK      bool              `json:"ok"`
	TTL     float64           `json:"ttl"`
	Headers map[string]string `json:"headers"`
}

// ask: einmal Python fragen. Die Anfrage traegt dieselben Anmelde- und Host-Koepfe wie die des Clients.
func (a *authz) ask(r *http.Request, fid string) *grant {
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	u := "http://" + a.c.upstream + "/internal/media/authz?fid=" + url.QueryEscape(fid)
	q, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil
	}
	q.Host = r.Host
	for _, h := range []string{"Cookie", "Authorization", "X-Saga", "X-Hlidskjalf", "Accept-Language", "User-Agent"} {
		if v := r.Header.Values(h); len(v) > 0 {
			q.Header[h] = append([]string(nil), v...)
		}
	}
	setForwarded(q.Header, r, a.c.trusted)
	q.Header.Set(keyHeader, a.c.key)
	resp, err := a.client.Do(q)
	if err != nil {
		if r.Context().Err() == nil {
			logf("Berechtigung nicht abfragbar: %v", err)
		}
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64*1024))
		return nil
	}
	var rep authzReply
	if err := json.NewDecoder(io.LimitReader(resp.Body, 64*1024)).Decode(&rep); err != nil || !rep.OK {
		return nil
	}
	ttl := a.c.ttl
	if t := time.Duration(rep.TTL * float64(time.Second)); t < ttl {
		ttl = t
	}
	if ttl < 0 {
		ttl = 0
	}
	return &grant{exp: a.now().Add(ttl), headers: rep.Headers}
}
