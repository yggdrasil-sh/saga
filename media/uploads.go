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
	"time"
)

// Videos im Chat (2.4): fortsetzbarer Upload im Format saga-file/v2. Angelegt wird er in Python (POST
// /api/chats/{cid}/uploads: Kontingent, Grenzen); saga-media nimmt die Bytes an und schreibt sie direkt auf die Platte:
//
//	PUT  /api/chat/uploads/{id}   Content-Range: bytes a-b/size   (nur fortlaufend anhaengend, a = bisheriger Stand)
//	HEAD /api/chat/uploads/{id}   -> Upload-Offset, Upload-Length (Fortsetzen nach Abbruch)
//
// Berechtigung wie bei Dateien ueber Python (/internal/media/upload, hoechstens 60 s gemerkt). Ist das letzte Byte
// geschrieben, meldet saga-media den Abschluss an Python (/internal/media/upload-done: Kopf pruefen, Anhang mit fmt=2
// eintragen) und reicht dessen Antwort ({id, size, ...}) an die App weiter. Ohne Erlaubnis geht die Anfrage an Python
// (gleiche Antwort wie ohne saga-media: 401/404).
const uploadsPrefix = "/api/chat/uploads/"

var cRangeRe = regexp.MustCompile(`^bytes (\d{1,15})-(\d{1,15})/(\d{1,15})$`)

func uploadID(p string) string {
	if !strings.HasPrefix(p, uploadsPrefix) {
		return ""
	}
	id := p[len(uploadsPrefix):]
	if !fidRe.MatchString(id) {
		return ""
	}
	return id
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }

// upLocks: hoechstens ein PUT je Upload gleichzeitig.
type upLocks struct {
	mu sync.Mutex
	m  map[string]bool
}

func (l *upLocks) try(id string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.m[id] {
		return false
	}
	l.m[id] = true
	return true
}

func (l *upLocks) done(id string) {
	l.mu.Lock()
	delete(l.m, id)
	l.mu.Unlock()
}

func (m *media) upPath(id string) string { return filepath.Join(m.c.blobDir, "up", id) }

func (m *media) upOffset(id string) int64 {
	st, err := os.Lstat(m.upPath(id))
	if err != nil || !st.Mode().IsRegular() {
		return 0
	}
	return st.Size()
}

func parseCRange(v string) (a, b, size int64, ok bool) {
	g := cRangeRe.FindStringSubmatch(strings.TrimSpace(v))
	if g == nil {
		return 0, 0, 0, false
	}
	a, _ = strconv.ParseInt(g[1], 10, 64)
	b, _ = strconv.ParseInt(g[2], 10, 64)
	size, _ = strconv.ParseInt(g[3], 10, 64)
	return a, b, size, a <= b && b < size
}

func (m *media) serveUpload(w http.ResponseWriter, r *http.Request, id string) {
	t0 := time.Now()
	ip := clientIP(r, m.c.trusted)
	g := m.az.checkKind(r, kindUpload, id)
	if g == nil || g.size <= 0 {
		if r.Body != nil && r.Body != http.NoBody && r.ContentLength != 0 {
			r.Body = &stallBody{rc: r.Body, ctl: http.NewResponseController(w), d: m.c.stall}
		}
		m.proxy.ServeHTTP(w, r) // Python antwortet wie ohne saga-media (401/404 bzw. nimmt selbst an)
		return
	}
	if st, err := os.Lstat(filepath.Join(m.c.blobDir, id)); err == nil && st.Mode().IsRegular() {
		// schon abgeschlossen: Python antwortet (HEAD: fertig mit Upload-Done, PUT: 404)
		m.az.forgetKind(r, kindUpload, id)
		m.proxy.ServeHTTP(w, r)
		return
	}
	off := m.upOffset(id)
	setOff := func() {
		w.Header().Set("Upload-Offset", itoa(off))
		w.Header().Set("Upload-Length", itoa(g.size))
	}
	if r.Method == http.MethodHead {
		setOff()
		w.Header().Set("Cache-Control", "no-store")
		w.WriteHeader(http.StatusNoContent)
		m.access(r, ip, http.StatusNoContent, 0, t0)
		return
	}
	fail := func(status int, msg string) {
		setOff()
		writeJSON(w, status, map[string]any{"error": msg, "offset": off})
		m.access(r, ip, status, 0, t0)
	}
	a, b, size, ok := parseCRange(r.Header.Get("Content-Range"))
	if !ok || size != g.size {
		fail(http.StatusBadRequest, "Content-Range")
		return
	}
	n := b - a + 1
	if g.partMax > 0 && n > g.partMax {
		fail(http.StatusRequestEntityTooLarge, "part too large")
		return
	}
	if r.ContentLength >= 0 && r.ContentLength != n {
		fail(http.StatusBadRequest, "Content-Length")
		return
	}
	if !m.ups.try(id) {
		fail(http.StatusConflict, "busy")
		return
	}
	defer m.ups.done(id)
	off = m.upOffset(id)
	if a != off {
		fail(http.StatusConflict, "offset")
		return
	}
	if err := os.MkdirAll(filepath.Dir(m.upPath(id)), 0o700); err != nil {
		fail(http.StatusInternalServerError, "disk")
		return
	}
	f, err := os.OpenFile(m.upPath(id), os.O_WRONLY|os.O_CREATE|os.O_APPEND|noFollow, 0o600)
	if err != nil {
		fail(http.StatusInternalServerError, "disk")
		return
	}
	body := &stallBody{rc: r.Body, ctl: http.NewResponseController(w), d: m.c.stall}
	buf := m.bufs.Get()
	got, cerr := io.CopyBuffer(struct{ io.Writer }{f}, io.LimitReader(body, n), buf)
	m.bufs.Put(buf)
	werr := f.Close()
	off = m.upOffset(id)
	if body.stalled.Load() {
		w.Header().Set("Connection", "close")
	}
	if got < n || (cerr != nil && !errors.Is(cerr, io.EOF)) || werr != nil {
		fail(http.StatusBadRequest, "incomplete")
		return
	}
	if off < g.size {
		setOff()
		writeJSON(w, http.StatusOK, map[string]any{"offset": off, "done": false})
		m.access(r, ip, http.StatusOK, got, t0)
		return
	}
	// letztes Byte: Python prueft den Kopf und traegt den Anhang ein - dessen Antwort geht an die App
	status, raw, err := m.callPython(r.Context(), http.MethodPost, "/internal/media/upload-done?id="+id, nil, nil, r)
	m.az.forgetKind(r, kindUpload, id) // fertig oder verworfen: ab jetzt entscheidet Python neu
	if err != nil || status == 0 {
		fail(http.StatusBadGateway, "saga")
		return
	}
	h := w.Header()
	h.Set("Content-Type", "application/json")
	h.Set("Cache-Control", "no-store")
	h.Set("Upload-Offset", itoa(off))
	h.Set("Content-Length", itoa(int64(len(raw))))
	w.WriteHeader(status)
	_, _ = w.Write(raw)
	m.access(r, ip, status, got, t0)
}
