package main

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Foederation (2.4, chatfile2): grosse Chat-Anhaenge anderer Server bereichsweise.
//
// Empfaenger: Die Zwischenkopie liegt lueckenhaft (sparse, volle Laenge) in STATE_DIR/chat/fed/<id>. Welche 1-MiB-Einheiten
// geprueft vorliegen, weiss Python (chat_file_fed.have). Fehlt ein angefragter Bereich, holt saga-media ihn: Python plant
// (/internal/media/fed-need: signierte Anfrage je Teil bis 4 MiB, erlaubte Adressen der Gegenstelle), saga-media laedt,
// schreibt an die Stelle, rechnet SHA-256 und meldet Kopf und Digest an Python (/internal/media/fed-got) - Python prueft nur
// die Signatur der Antwort und traegt die Einheiten ein. Erst dann liefert saga-media die Bytes an die App.
//
// Absender: GET /fed/v2/chat/files/{id}?range=a-b eines eigenen Anhangs beantwortet saga-media selbst - es liest den Teil,
// rechnet den Digest, Python prueft die Signatur der Anfrage und die Berechtigung und signiert die Antwort
// (/internal/media/fed-serve). So laufen auch ueber Servergrenzen keine Medienbytes durch Python.
const (
	fedFilesPrefix = "/fed/v2/chat/files/"
	fedPartMax     = 4 << 20 // hoechstens je Teil (wie Python PART_MAX)
	fedPiece       = 4 << 20 // beim Ausliefern je Schritt sichern, dann senden
)

var errFedGone = errors.New("fed: nicht (mehr) da")

var rangeQRe = regexp.MustCompile(`^(\d{1,15})-(\d{1,15})$`)

type fedChunk struct {
	From    int64             `json:"from"`
	To      int64             `json:"to"`
	URL     string            `json:"url"`
	Headers map[string]string `json:"headers"`
	Nonce   string            `json:"nonce"`
	Dial    []string          `json:"dial"`
}

type fedPlan struct {
	Size  int64      `json:"size"`
	Have  string     `json:"have"`
	Unit  int64      `json:"unit"`
	Fetch []fedChunk `json:"fetch"`
}

type fedGot struct {
	OK       bool   `json:"ok"`
	Have     string `json:"have"`
	Complete bool   `json:"complete"`
	Error    string `json:"error"`
}

// fedState: was saga-media ueber eine Zwischenkopie weiss (von Python bestaetigt) - gilt nur fuer genau diese Datei (inode).
type fedState struct {
	ino      uint64
	have     []byte
	unit     int64
	at       time.Time
	complete bool
}

type fedCache struct {
	mu      sync.Mutex
	st      map[string]*fedState
	flights map[string]*fedFlight
	trs     map[string]*http.Transport
}

type fedFlight struct {
	done chan struct{}
	err  error
}

func newFedCache() *fedCache {
	return &fedCache{st: map[string]*fedState{}, flights: map[string]*fedFlight{}, trs: map[string]*http.Transport{}}
}

func (m *media) fedPath(fid string) string { return filepath.Join(m.c.blobDir, "fed", fid) }

func inodeOf(p string) uint64 {
	st, err := os.Lstat(p)
	if err != nil || !st.Mode().IsRegular() {
		return 0
	}
	if s, ok := st.Sys().(*syscall.Stat_t); ok {
		return uint64(s.Ino)
	}
	return 0
}

func bitsHave(have []byte, unit, a, b int64) bool {
	if unit <= 0 {
		return false
	}
	for u := a / unit; u <= b/unit; u++ {
		if int(u>>3) >= len(have) || have[u>>3]>>(u&7)&1 == 0 {
			return false
		}
	}
	return true
}

// known: liegt a..b laut gemerktem (bestaetigtem) Stand vor - und ist es noch dieselbe Datei? Ganz geholt: die Datei
// liegt dann (von Python verschoben) im Blob-Verzeichnis.
func (m *media) fedKnown(fid string, a, b int64) bool {
	m.fed.mu.Lock()
	s := m.fed.st[fid]
	m.fed.mu.Unlock()
	if s == nil {
		return false
	}
	if s.complete {
		return inodeOf(filepath.Join(m.c.blobDir, fid)) == s.ino && s.ino != 0
	}
	ino := inodeOf(m.fedPath(fid))
	return ino != 0 && s.ino == ino && bitsHave(s.have, s.unit, a, b)
}

// fedDone: Python meldet "vollstaendig" - Datei liegt jetzt im Blob-Verzeichnis.
func (m *media) fedDone(fid string) {
	ino := inodeOf(filepath.Join(m.c.blobDir, fid))
	m.fed.mu.Lock()
	m.fed.st[fid] = &fedState{ino: ino, complete: true, at: time.Now()}
	m.fed.mu.Unlock()
}

// openFed: lueckenhafte Kopie oder (inzwischen ganz geholt) die Datei im Blob-Verzeichnis.
func (m *media) openFed(fid string) (*os.File, error) {
	f, err := os.OpenFile(m.fedPath(fid), os.O_RDONLY|noFollow, 0)
	if err == nil {
		return f, nil
	}
	if bf, _, err2 := openBlob(m.c.blobDir, fid); err2 == nil {
		return bf, nil
	}
	return nil, err
}

func (m *media) fedRemember(fid, have string, unit int64) {
	raw, err := base64.StdEncoding.DecodeString(have)
	if err != nil {
		return
	}
	ino := inodeOf(m.fedPath(fid))
	m.fed.mu.Lock()
	defer m.fed.mu.Unlock()
	if len(m.fed.st) > 2000 {
		m.fed.st = map[string]*fedState{}
	}
	if ino == 0 {
		delete(m.fed.st, fid)
		return
	}
	m.fed.st[fid] = &fedState{ino: ino, have: raw, unit: unit, at: time.Now()}
}

func (m *media) fedForget(fid string) {
	m.fed.mu.Lock()
	delete(m.fed.st, fid)
	m.fed.mu.Unlock()
}

// fedEnsure: a..b liegt danach geprueft in der Zwischenkopie (oder Fehler). errFedGone: Python kennt die lueckenhafte
// Kopie nicht (mehr) - z. B. inzwischen ganz geholt; dann den normalen Weg nehmen.
func (m *media) fedEnsure(ctx context.Context, fid string, size, a, b int64) error {
	if b >= size {
		b = size - 1
	}
	if a > b || m.fedKnown(fid, a, b) {
		return nil
	}
	for try := 0; try < 64; try++ {
		var pl fedPlan
		status, _, err := m.callPython(ctx, http.MethodPost, "/internal/media/fed-need", map[string]any{"fid": fid, "from": a, "to": b}, &pl, nil)
		if err != nil {
			return err
		}
		if status == http.StatusNotFound {
			m.fedForget(fid)
			return errFedGone
		}
		if status != http.StatusOK {
			return fmt.Errorf("fed-need: HTTP %d", status)
		}
		m.fedRemember(fid, pl.Have, pl.Unit)
		if len(pl.Fetch) == 0 {
			if inodeOf(m.fedPath(fid)) == 0 { // alles da, aber Datei weg (ganz geholt und verschoben): normaler Weg
				return errFedGone
			}
			return nil
		}
		for _, ch := range pl.Fetch {
			if err := m.fedChunkOnce(ctx, fid, pl.Size, ch); err != nil {
				return err
			}
		}
		if m.fedKnown(fid, a, b) {
			return nil
		}
	}
	return errors.New("fed: zu viele Schritte")
}

// fedChunkOnce: einen geplanten Teil holen - parallele Anfragen fuer denselben Teil warten auf einen Abruf.
func (m *media) fedChunkOnce(ctx context.Context, fid string, size int64, ch fedChunk) error {
	key := fid + ":" + strconv.FormatInt(ch.From, 10)
	m.fed.mu.Lock()
	if f := m.fed.flights[key]; f != nil {
		m.fed.mu.Unlock()
		select {
		case <-f.done:
			return f.err
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	f := &fedFlight{done: make(chan struct{})}
	m.fed.flights[key] = f
	m.fed.mu.Unlock()
	// eigener Kontext: bricht der erste Client ab, sollen wartende den Teil trotzdem bekommen
	cctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	f.err = m.fedFetch(cctx, fid, size, ch)
	cancel()
	m.fed.mu.Lock()
	delete(m.fed.flights, key)
	m.fed.mu.Unlock()
	close(f.done)
	return f.err
}

// transportFor: Verbindungen nur zu den von Python freigegebenen Adressen (SSRF-Schutz wie netguard), TLS mit dem Namen
// aus der Adresse.
func (m *media) transportFor(u *url.URL, dial []string) *http.Transport {
	key := u.Scheme + "://" + u.Host + "|" + strings.Join(dial, ",")
	m.fed.mu.Lock()
	defer m.fed.mu.Unlock()
	if tr := m.fed.trs[key]; tr != nil {
		return tr
	}
	if len(m.fed.trs) > 64 {
		for k, tr := range m.fed.trs {
			tr.CloseIdleConnections()
			delete(m.fed.trs, k)
		}
	}
	addrs := append([]string(nil), dial...)
	d := &net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}
	tr := &http.Transport{
		Proxy: nil,
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			var last error = errors.New("keine erlaubte Adresse")
			for _, a := range addrs {
				c, err := d.DialContext(ctx, "tcp", a)
				if err == nil {
					return c, nil
				}
				last = err
			}
			return nil, last
		},
		TLSClientConfig:       &tls.Config{ServerName: u.Hostname(), MinVersion: tls.VersionTLS12},
		ForceAttemptHTTP2:     false,
		MaxIdleConnsPerHost:   4,
		IdleConnTimeout:       60 * time.Second,
		ResponseHeaderTimeout: 20 * time.Second,
		DisableCompression:    true,
	}
	m.fed.trs[key] = tr
	return tr
}

// fedFetch: Teil laden, an seine Stelle schreiben, Digest an Python - erst nach dessen Bestaetigung gilt er.
func (m *media) fedFetch(ctx context.Context, fid string, size int64, ch fedChunk) error {
	n := ch.To - ch.From + 1
	if n <= 0 || n > fedPartMax || ch.To >= size {
		return errors.New("fed: Plan ungueltig")
	}
	u, err := url.Parse(ch.URL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") {
		return errors.New("fed: Adresse ungueltig")
	}
	if err := os.MkdirAll(filepath.Dir(m.fedPath(fid)), 0o700); err != nil {
		return err
	}
	f, err := os.OpenFile(m.fedPath(fid), os.O_WRONLY|os.O_CREATE|noFollow, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	if st, err := f.Stat(); err != nil || !st.Mode().IsRegular() {
		return errors.New("fed: keine Datei")
	} else if st.Size() != size {
		if err := f.Truncate(size); err != nil {
			return err
		}
	}
	q, err := http.NewRequestWithContext(ctx, http.MethodGet, ch.URL, nil)
	if err != nil {
		return err
	}
	for k, v := range ch.Headers {
		q.Header.Set(k, v)
	}
	resp, err := (&http.Client{Transport: m.transportFor(u, ch.Dial), Timeout: 60 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(q)
	if err != nil {
		return fmt.Errorf("fed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusPartialContent {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		return fmt.Errorf("fed: Gegenstelle HTTP %d", resp.StatusCode)
	}
	hs := sha256.New()
	w := io.MultiWriter(io.NewOffsetWriter(f, ch.From), hs)
	buf := m.bufs.Get()
	got, err := io.CopyBuffer(w, io.LimitReader(resp.Body, n+1), buf)
	m.bufs.Put(buf)
	if err != nil || got != n {
		return fmt.Errorf("fed: %d statt %d Bytes", got, n)
	}
	heads := map[string]string{}
	for _, k := range []string{"Signature-Input", "Signature", "Content-Digest", "Saga-Nonce"} {
		heads[strings.ToLower(k)] = resp.Header.Get(k)
	}
	dig := "sha-256=:" + base64.StdEncoding.EncodeToString(hs.Sum(nil)) + ":"
	var g fedGot
	status, _, err := m.callPython(ctx, http.MethodPost, "/internal/media/fed-got",
		map[string]any{"nonce": ch.Nonce, "status": resp.StatusCode, "headers": heads, "digest": dig, "length": got}, &g, nil)
	if err != nil || status != http.StatusOK || !g.OK {
		return fmt.Errorf("fed: nicht bestaetigt (%d %s)", status, g.Error)
	}
	if g.Complete {
		m.fedDone(fid)
	} else {
		m.fedRemember(fid, g.Have, fedUnit)
	}
	return nil
}

const fedUnit = 1 << 20

// serveFed: GET/HEAD /api/chat/files/{fid} einer lueckenhaften Zwischenkopie. Antwort wie bei lokalen Dateien (Range,
// ETag); fehlende Bereiche werden vor dem Senden je Schritt (4 MiB) geholt und geprueft.
func (m *media) serveFed(w http.ResponseWriter, r *http.Request, fid string, g *grant, t0 time.Time) {
	ip := clientIP(r, m.c.trusted)
	size := g.fed.Size
	h := w.Header()
	for k, v := range g.headers {
		if k != "" && !strings.ContainsAny(k+v, "\r\n") {
			h.Set(k, v)
		}
	}
	etag := `"` + fid + `"`
	h.Set("ETag", etag)
	h.Set("Accept-Ranges", "bytes")
	if inm := r.Header.Get("If-None-Match"); inm != "" && etagMatch(inm, etag) {
		h.Del("Content-Disposition")
		w.WriteHeader(http.StatusNotModified)
		m.access(r, ip, http.StatusNotModified, 0, t0)
		return
	}
	start, end, status := int64(0), size-1, http.StatusOK
	if rh := r.Header.Get("Range"); rh != "" {
		if ir := strings.TrimSpace(r.Header.Get("If-Range")); ir == "" || ir == etag {
			s, e, ok, unsat := parseRange(rh, size)
			if unsat {
				h.Set("Content-Range", "bytes */"+itoa(size))
				h.Set("Content-Length", "0")
				w.WriteHeader(http.StatusRequestedRangeNotSatisfiable)
				m.access(r, ip, http.StatusRequestedRangeNotSatisfiable, 0, t0)
				return
			}
			if ok {
				start, end, status = s, e, http.StatusPartialContent
			}
		}
	}
	if r.Method != http.MethodHead {
		first := min(end, start+fedPiece-1)
		if err := m.fedEnsure(r.Context(), fid, size, start, first); err != nil {
			if errors.Is(err, errFedGone) {
				m.az.forget(r, fid)
				for k := range h {
					delete(h, k)
				}
				m.proxy.ServeHTTP(w, r) // Python entscheidet neu (z. B. inzwischen ganz hier)
				return
			}
			if r.Context().Err() == nil {
				logf("Datei %s…: Bereich nicht geholt: %v", fid[:6], err)
			}
			for k := range h {
				delete(h, k)
			}
			h.Set("Retry-After", "5")
			h.Set("Cache-Control", "no-store")
			http.Error(w, "upstream", http.StatusBadGateway)
			m.access(r, ip, http.StatusBadGateway, 0, t0)
			return
		}
	}
	f, err := m.openFed(fid)
	if err != nil && r.Method != http.MethodHead {
		m.az.forget(r, fid)
		m.fedForget(fid)
		for k := range h {
			delete(h, k)
		}
		m.proxy.ServeHTTP(w, r)
		return
	}
	if f != nil {
		defer f.Close()
	}
	if !m.lim.acquire(ip) {
		h.Set("Retry-After", "2")
		h.Set("Cache-Control", "no-store")
		http.Error(w, "too many downloads", http.StatusServiceUnavailable)
		return
	}
	defer m.lim.release(ip)
	h.Set("Content-Type", "application/octet-stream")
	h.Set("Content-Length", itoa(end-start+1))
	if status == http.StatusPartialContent {
		h.Set("Content-Range", "bytes "+itoa(start)+"-"+itoa(end)+"/"+itoa(size))
	}
	ctl := http.NewResponseController(w)
	_ = ctl.SetWriteDeadline(time.Now().Add(m.c.stall))
	w.WriteHeader(status)
	if r.Method == http.MethodHead {
		m.access(r, ip, status, 0, t0)
		return
	}
	var sent int64
	rf, _ := w.(io.ReaderFrom)
	for pos := start; pos <= end; {
		pe := min(end, pos+fedPiece-1)
		if pos != start {
			if err := m.fedEnsure(r.Context(), fid, size, pos, pe); err != nil {
				if r.Context().Err() == nil {
					logf("Datei %s…: Bereich ab %d nicht geholt: %v", fid[:6], pos, err)
				}
				break // Verbindung endet vor Content-Length: der Client fordert den Rest neu an
			}
		}
		_ = ctl.SetWriteDeadline(time.Now().Add(m.c.stall))
		sr := io.NewSectionReader(f, pos, pe-pos+1)
		var k int64
		if rf != nil {
			k, err = rf.ReadFrom(struct{ io.Reader }{sr})
		} else {
			k, err = io.Copy(w, sr)
		}
		sent += k
		if err != nil || k != pe-pos+1 {
			break
		}
		pos = pe + 1
	}
	_ = ctl.SetWriteDeadline(time.Time{})
	m.access(r, ip, status, sent, t0)
}

// serveFedOrigin: GET /fed/v2/chat/files/{fid}?range=a-b eines eigenen Anhangs. Alles, was nicht eindeutig passt (Datei
// fehlt, Bereich ungueltig, Zwischenkopie eines fremden Anhangs), geht unveraendert an Python.
func (m *media) serveFedOrigin(w http.ResponseWriter, r *http.Request, fid string) {
	t0 := time.Now()
	ip := clientIP(r, m.c.trusted)
	f, size, err := openBlob(m.c.blobDir, fid)
	if err != nil {
		m.proxy.ServeHTTP(w, r)
		return
	}
	defer f.Close()
	g := rangeQRe.FindStringSubmatch(r.URL.Query().Get("range"))
	if g == nil {
		m.proxy.ServeHTTP(w, r)
		return
	}
	a, _ := strconv.ParseInt(g[1], 10, 64)
	b, _ := strconv.ParseInt(g[2], 10, 64)
	if b < a || b-a+1 > fedPartMax || a >= size {
		m.proxy.ServeHTTP(w, r)
		return
	}
	b = min(b, size-1)
	if !m.lim.acquire(ip) {
		w.Header().Set("Retry-After", "2")
		http.Error(w, "busy", http.StatusServiceUnavailable)
		return
	}
	defer m.lim.release(ip)
	hs := sha256.New()
	buf := m.bufs.Get()
	_, err = io.CopyBuffer(hs, io.NewSectionReader(f, a, b-a+1), buf)
	m.bufs.Put(buf)
	if err != nil {
		m.proxy.ServeHTTP(w, r)
		return
	}
	heads := map[string]string{}
	for _, k := range []string{"Signature-Input", "Signature", "Content-Digest", "Saga-Actor"} {
		heads[strings.ToLower(k)] = r.Header.Get(k)
	}
	var rep struct {
		OK      bool              `json:"ok"`
		Pass    bool              `json:"pass"`
		Status  int               `json:"status"`
		Headers map[string]string `json:"headers"`
		Body    string            `json:"body"`
	}
	status, _, err := m.callPython(r.Context(), http.MethodPost, "/internal/media/fed-serve", map[string]any{
		"method": r.Method, "path": r.URL.EscapedPath(), "query": r.URL.RawQuery, "headers": heads, "fid": fid,
		"digest": "sha-256=:" + base64.StdEncoding.EncodeToString(hs.Sum(nil)) + ":", "from": a, "to": b, "size": size}, &rep, nil)
	if err != nil || status != http.StatusOK || rep.Pass {
		m.proxy.ServeHTTP(w, r)
		return
	}
	h := w.Header()
	for k, v := range rep.Headers {
		if lk := strings.ToLower(k); lk != "content-length" && lk != "transfer-encoding" && !strings.ContainsAny(k+v, "\r\n") {
			h.Set(k, v)
		}
	}
	if !rep.OK {
		body, _ := base64.StdEncoding.DecodeString(rep.Body)
		code := rep.Status
		if code < 400 || code > 599 {
			code = http.StatusNotFound
		}
		h.Set("Content-Length", itoa(int64(len(body))))
		w.WriteHeader(code)
		_, _ = w.Write(body)
		m.access(r, ip, code, 0, t0)
		return
	}
	h.Set("Content-Length", itoa(b-a+1))
	ctl := http.NewResponseController(w)
	_ = ctl.SetWriteDeadline(time.Now().Add(m.c.stall))
	w.WriteHeader(http.StatusPartialContent)
	var k int64
	sr := io.NewSectionReader(f, a, b-a+1)
	if rf, ok := w.(io.ReaderFrom); ok {
		k, _ = rf.ReadFrom(struct{ io.Reader }{sr})
	} else {
		k, _ = io.Copy(w, sr)
	}
	_ = ctl.SetWriteDeadline(time.Time{})
	m.access(r, ip, http.StatusPartialContent, k, t0)
}

// serveWarm: POST /internal/media/warm?fid=&from=&to=&size= (nur Python, mit Geheimnis): Bereich vorab holen
// (Vorababruf nach Eingang einer Nachricht - Anfang und Ende eines Videos).
func (m *media) serveWarm(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	fid := q.Get("fid")
	a, e1 := strconv.ParseInt(q.Get("from"), 10, 64)
	b, e2 := strconv.ParseInt(q.Get("to"), 10, 64)
	size, e3 := strconv.ParseInt(q.Get("size"), 10, 64)
	if !fidRe.MatchString(fid) || e1 != nil || e2 != nil || e3 != nil || a < 0 || b < a || b-a+1 > 16<<20 || b >= size {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	if err := m.fedEnsure(r.Context(), fid, size, a, b); err != nil {
		w.WriteHeader(http.StatusBadGateway)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func keyOK(r *http.Request, key string) bool {
	v := r.Header.Get(keyHeader)
	return len(key) >= 32 && len(v) == len(key) && subtle.ConstantTimeCompare([]byte(v), []byte(key)) == 1
}

const noFollow = syscall.O_NOFOLLOW
