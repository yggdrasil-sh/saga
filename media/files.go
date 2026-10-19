package main

import (
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const filesPrefix = "/api/chat/files/"

// Kennung wie saga/chat.py (_FID): nur diese Zeichen, kein Pfadtrenner, kein Punkt - damit kein Traversal und
// keine Zwischendateien (".part-...").
var fidRe = regexp.MustCompile(`^[A-Za-z0-9_-]{22,40}$`)

const chunk = 4 << 20 // je sendfile-Aufruf; dazwischen wird die Schreibfrist verlaengert

// limits: gleichzeitige Stroeme insgesamt und je Client-Adresse.
type limits struct {
	mu    sync.Mutex
	total int
	perIP map[string]int
	max   int
	maxIP int
}

func (l *limits) acquire(ip string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.total >= l.max || l.perIP[ip] >= l.maxIP {
		return false
	}
	l.total++
	l.perIP[ip]++
	return true
}

func (l *limits) release(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.total--
	if l.perIP[ip]--; l.perIP[ip] <= 0 {
		delete(l.perIP, ip)
	}
}

type media struct {
	c      *config
	az     *authz
	proxy  http.Handler
	lim    *limits
	client *http.Client // interne Aufrufe an Python
	ups    *upLocks     // 2.4: ein PUT je Upload
	fed    *fedCache    // 2.4: Zwischenkopien anderer Server
	bufs   *bufPool     // 2.4: Puffer fuer Platte/Netz (256 KiB)
}

// fileID: "/api/chat/files/<fid>" -> fid (sonst "").
func fileID(p string) string {
	if !strings.HasPrefix(p, filesPrefix) {
		return ""
	}
	fid := p[len(filesPrefix):]
	if !fidRe.MatchString(fid) {
		return ""
	}
	return fid
}

// openBlob: nur eine regulaere Datei direkt im Blob-Verzeichnis, ohne symbolische Verweise.
func openBlob(dir, fid string) (*os.File, int64, error) {
	if !fidRe.MatchString(fid) {
		return nil, 0, os.ErrNotExist
	}
	p := filepath.Join(dir, fid)
	if filepath.Dir(p) != dir {
		return nil, 0, os.ErrNotExist
	}
	f, err := os.OpenFile(p, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, 0, err
	}
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() {
		f.Close()
		return nil, 0, os.ErrNotExist
	}
	return f, st.Size(), nil
}

// serveFile: GET/HEAD /api/chat/files/{fid}. Erlaubt Python den Abruf nicht (oder fehlt die Datei hier), geht die
// Anfrage unveraendert an Python - dort entsteht dieselbe Antwort wie bisher (401/404, Foederation holt nach).
func (m *media) serveFile(w http.ResponseWriter, r *http.Request, fid string) {
	t0 := time.Now()
	g := m.az.check(r, fid)
	if g == nil {
		m.proxy.ServeHTTP(w, r)
		return
	}
	if g.fed != nil && g.fed.Size > 0 { // 2.4: lueckenhafte Zwischenkopie (fed.go)
		m.serveFed(w, r, fid, g, t0)
		return
	}
	f, size, err := openBlob(m.c.blobDir, fid)
	if err != nil {
		m.az.forget(r, fid)
		m.proxy.ServeHTTP(w, r)
		return
	}
	defer f.Close()
	ip := clientIP(r, m.c.trusted)
	if !m.lim.acquire(ip) {
		w.Header().Set("Retry-After", "2")
		w.Header().Set("Cache-Control", "no-store")
		http.Error(w, "too many downloads", http.StatusServiceUnavailable)
		m.access(r, ip, http.StatusServiceUnavailable, 0, t0)
		return
	}
	defer m.lim.release(ip)
	status, n := serveBlob(w, r, f, size, fid, g.headers, m.c.stall)
	m.access(r, ip, status, n, t0)
}

// etagMatch: If-None-Match-Liste (schwacher Vergleich, "*" passt immer).
func etagMatch(header, etag string) bool {
	for _, p := range strings.Split(header, ",") {
		p = strings.TrimSpace(p)
		if p == "*" || strings.TrimPrefix(p, "W/") == etag {
			return true
		}
	}
	return false
}

// parseRange: erster Bereich aus "bytes=..." (weitere Bereiche ignoriert). ok=false: Kopf unbrauchbar -> ganze
// Datei. unsat=true: Bereich ausserhalb -> 416.
func parseRange(h string, size int64) (start, end int64, ok, unsat bool) {
	h = strings.TrimSpace(h)
	if len(h) < 6 || !strings.EqualFold(h[:6], "bytes=") {
		return 0, 0, false, false
	}
	spec := strings.TrimSpace(strings.SplitN(h[6:], ",", 2)[0])
	i := strings.IndexByte(spec, '-')
	if i < 0 {
		return 0, 0, false, false
	}
	a, b := strings.TrimSpace(spec[:i]), strings.TrimSpace(spec[i+1:])
	num := func(s string) (int64, bool) {
		if s == "" || len(s) > 18 || strings.Trim(s, "0123456789") != "" {
			return 0, false
		}
		n, err := strconv.ParseInt(s, 10, 64)
		return n, err == nil
	}
	if a == "" { // Ende: "-n"
		n, good := num(b)
		if !good {
			return 0, 0, false, false
		}
		if n == 0 || size == 0 {
			return 0, 0, true, true
		}
		if n > size {
			n = size
		}
		return size - n, size - 1, true, false
	}
	s, good := num(a)
	if !good {
		return 0, 0, false, false
	}
	e := size - 1
	if b != "" {
		x, good := num(b)
		if !good || x < s {
			return 0, 0, false, false
		}
		if x < e {
			e = x
		}
	}
	if s >= size {
		return 0, 0, true, true
	}
	return s, e, true, false
}

// serveBlob: Kopfzeilen, Bedingungen (If-None-Match, If-Range), Range, Inhalt per sendfile in Stuecken mit
// verlaengerter Schreibfrist (Slowloris: wer nichts mehr liest, verliert die Verbindung nach "stall").
func serveBlob(w http.ResponseWriter, r *http.Request, f *os.File, size int64, fid string, hdr map[string]string, stall time.Duration) (int, int64) {
	h := w.Header()
	for k, v := range hdr {
		if k != "" && !strings.ContainsAny(k+v, "\r\n") {
			h.Set(k, v)
		}
	}
	etag := `"` + fid + `"`
	h.Set("ETag", etag)
	h.Set("Accept-Ranges", "bytes")
	h.Set("Content-Type", "application/octet-stream")
	h.Del("Content-Length")
	if inm := r.Header.Get("If-None-Match"); inm != "" && etagMatch(inm, etag) {
		h.Del("Content-Type")
		h.Del("Content-Disposition")
		w.WriteHeader(http.StatusNotModified)
		return http.StatusNotModified, 0
	}
	start, end, status := int64(0), size-1, http.StatusOK
	if rh := r.Header.Get("Range"); rh != "" {
		useRange := true
		if ir := strings.TrimSpace(r.Header.Get("If-Range")); ir != "" && ir != etag {
			useRange = false // anderer Stand (oder Datum): ganze Datei
		}
		if useRange {
			s, e, ok, unsat := parseRange(rh, size)
			if unsat {
				h.Set("Content-Range", "bytes */"+strconv.FormatInt(size, 10))
				h.Set("Content-Length", "0")
				h.Del("Content-Type")
				w.WriteHeader(http.StatusRequestedRangeNotSatisfiable)
				return http.StatusRequestedRangeNotSatisfiable, 0
			}
			if ok {
				start, end, status = s, e, http.StatusPartialContent
				h.Set("Content-Range", "bytes "+strconv.FormatInt(s, 10)+"-"+strconv.FormatInt(e, 10)+"/"+strconv.FormatInt(size, 10))
			}
		}
	}
	length := end - start + 1
	if size == 0 {
		length = 0
	}
	h.Set("Content-Length", strconv.FormatInt(length, 10))
	ctl := http.NewResponseController(w)
	_ = ctl.SetWriteDeadline(time.Now().Add(stall))
	w.WriteHeader(status)
	if r.Method == http.MethodHead || length == 0 {
		return status, 0
	}
	if _, err := f.Seek(start, io.SeekStart); err != nil {
		return status, 0
	}
	var sent int64
	rf, _ := w.(io.ReaderFrom)
	for sent < length {
		_ = ctl.SetWriteDeadline(time.Now().Add(stall))
		n := min(int64(chunk), length-sent)
		var k int64
		var err error
		lr := &io.LimitedReader{R: f, N: n}
		if rf != nil {
			k, err = rf.ReadFrom(lr) // net/http -> TCPConn.ReadFrom -> sendfile
		} else {
			k, err = io.Copy(w, lr)
		}
		sent += k
		if err != nil || k == 0 {
			if err != nil && !errors.Is(err, syscall.EPIPE) && !errors.Is(err, syscall.ECONNRESET) && !isTimeout(err) {
				logf("Datei %s…: Senden abgebrochen nach %d Bytes: %v", fid[:6], sent, err)
			}
			break
		}
	}
	_ = ctl.SetWriteDeadline(time.Time{})
	return status, sent
}

func isTimeout(err error) bool {
	var te interface{ Timeout() bool }
	return errors.As(err, &te) && te.Timeout()
}
