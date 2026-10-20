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
	fed     *fedInfo          // 2.4: lueckenhafte Zwischenkopie eines Anhangs eines anderen Servers (Bereiche bei Bedarf)
	size    int64             // 2.4 Upload: angekuendigte Groesse
	partMax int64             // 2.4 Upload: hoechstens je PUT
}

// fedInfo: Anhang liegt beim Absender-Server (chatfile2) - saga-media holt fehlende Bereiche (fed.go).
type fedInfo struct {
	Size int64 `json:"size"`
}

// Arten der Abfrage: Datei lesen (GET/HEAD /api/chat/files) bzw. in einen Upload schreiben (PUT/HEAD /api/chat/uploads).
const (
	kindFile   = "file"
	kindUpload = "upload"
)

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

// cacheKey: alles, was die Entscheidung in Python beeinflusst - Art, Datei, Anmeldung (Cookie, Authorization,
// Medien-Token), der Pflicht-Kopf X-Saga und der Hostname (Umleitung auf BASE_URL).
func cacheKey(kind, fid string, r *http.Request) [32]byte {
	h := sha256.New()
	put := func(s string) { _, _ = io.WriteString(h, s); _, _ = h.Write([]byte{0}) }
	put(kind)
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
	put(r.Header.Get(tokenHeader))
	var k [32]byte
	copy(k[:], h.Sum(nil))
	return k
}

func (a *authz) check(r *http.Request, fid string) *grant { return a.checkKind(r, kindFile, fid) }

// checkKind: erteilte Berechtigung -> die Verbindung gilt als angemeldet (laengerer Leerlauf, markAuth).
func (a *authz) checkKind(r *http.Request, kind, fid string) *grant {
	g := a.checkKind0(r, kind, fid)
	if g != nil {
		markAuth(r)
	}
	return g
}

func (a *authz) checkKind0(r *http.Request, kind, fid string) *grant {
	if a.c.ttl <= 0 {
		return a.ask(r, kind, fid)
	}
	k := cacheKey(kind, fid, r)
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

	g := a.ask(r, kind, fid)

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
func (a *authz) forget(r *http.Request, fid string) { a.forgetKind(r, kindFile, fid) }

func (a *authz) forgetKind(r *http.Request, kind, fid string) {
	k := cacheKey(kind, fid, r)
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
	Fed     *fedInfo          `json:"fed"`
	Size    int64             `json:"size"`
	PartMax int64             `json:"part_max"`
}

// tokenHeader: Medien-Token (2.4) fuer lange Wiedergaben - an Datei und Sitzung gebunden, Python prueft es je Abfrage.
const tokenHeader = "X-Saga-Media-Token"

// authHeaders: Koepfe, die Python fuer die Entscheidung braucht (Anmeldung, Sprache).
var authHeaders = []string{"Cookie", "Authorization", "X-Saga", "X-Hlidskjalf", tokenHeader, "Accept-Language", "User-Agent"}

func copyAuth(dst http.Header, r *http.Request) {
	for _, h := range authHeaders {
		if v := r.Header.Values(h); len(v) > 0 {
			dst[h] = append([]string(nil), v...)
		}
	}
}

// ask: einmal Python fragen. Die Anfrage traegt dieselben Anmelde- und Host-Koepfe wie die des Clients.
func (a *authz) ask(r *http.Request, kind, fid string) *grant {
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	defer cancel()
	u := "http://" + a.c.upstream + "/internal/media/authz?fid=" + url.QueryEscape(fid)
	if kind == kindUpload {
		u = "http://" + a.c.upstream + "/internal/media/upload?id=" + url.QueryEscape(fid)
	}
	q, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil
	}
	q.Host = r.Host
	copyAuth(q.Header, r)
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
	return &grant{exp: a.now().Add(ttl), headers: rep.Headers, fed: rep.Fed, size: rep.Size, partMax: rep.PartMax}
}
