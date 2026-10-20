package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestParseRange(t *testing.T) {
	cases := []struct {
		h         string
		size      int64
		s, e      int64
		ok, unsat bool
	}{
		{"bytes=0-99", 1000, 0, 99, true, false},
		{"bytes=500-", 1000, 500, 999, true, false},
		{"bytes=-500", 1000, 500, 999, true, false},
		{"bytes=-5000", 1000, 0, 999, true, false},
		{"bytes=900-5000", 1000, 900, 999, true, false},
		{"bytes=1000-", 1000, 0, 0, true, true},
		{"bytes=-0", 1000, 0, 0, true, true},
		{"bytes=0-0,5-9", 1000, 0, 0, true, false},
		{"bytes=9-5", 1000, 0, 0, false, false},
		{"bytes=a-b", 1000, 0, 0, false, false},
		{"items=0-5", 1000, 0, 0, false, false},
		{"bytes=0-", 0, 0, 0, true, true},
		{"BYTES= 2 - 3", 10, 2, 3, true, false},
		{"bytes=-", 10, 0, 0, false, false},
		{"bytes=+1-2", 10, 0, 0, false, false},
	}
	for _, c := range cases {
		s, e, ok, unsat := parseRange(c.h, c.size)
		if ok != c.ok || unsat != c.unsat || (ok && !unsat && (s != c.s || e != c.e)) {
			t.Errorf("%q/%d: got %d-%d ok=%v unsat=%v", c.h, c.size, s, e, ok, unsat)
		}
	}
}

func TestTrustAndClientIP(t *testing.T) {
	tr := parseTrust("127.0.0.1, 10.0.0.0/8, ::1, kaputt")
	r := httptest.NewRequest("GET", "/", nil)
	r.RemoteAddr = "203.0.113.5:4444"
	r.Header.Set("X-Forwarded-For", "1.2.3.4")
	if ip := clientIP(r, tr); ip != "203.0.113.5" {
		t.Errorf("nicht vertraute Gegenstelle: %s", ip)
	}
	o := http.Header{}
	setForwarded(o, r, tr)
	if o.Get("X-Forwarded-For") != "203.0.113.5" || o.Get("X-Saga-Orig-For") != "1.2.3.4" {
		t.Errorf("nicht vertraut: %v", o)
	}
	r.RemoteAddr = "127.0.0.1:1"
	o = http.Header{}
	setForwarded(o, r, parseTrust("10.9.9.9"))
	if o.Get("X-Forwarded-For") != "127.0.0.1" {
		t.Errorf("loopback nicht vertraut: %v", o)
	}
	r.RemoteAddr = "10.1.2.3:4444"
	r.Header.Set("X-Forwarded-For", "6.6.6.6, 1.2.3.4, 10.9.9.9")
	if ip := clientIP(r, tr); ip != "1.2.3.4" {
		t.Errorf("vertrauter Proxy: %s", ip)
	}
	out := http.Header{}
	r.Header.Set("X-Saga-Orig-For", "boese")
	r.Header.Set("X-Forwarded-Proto", "https")
	setForwarded(out, r, tr)
	if out.Get("X-Forwarded-Proto") != "https" || out.Get("X-Forwarded-For") != "1.2.3.4" || out.Get("X-Saga-Orig-For") != "" {
		t.Errorf("Proto von vertrautem Proxy fehlt")
	}
	r.RemoteAddr = "8.8.8.8:1"
	out = http.Header{}
	r.Header.Set(keyHeader, "x")
	setForwarded(out, r, tr)
	if out.Get("X-Forwarded-Proto") != "" || out.Get(keyHeader) != "" {
		t.Errorf("Proto/Geheimnis von fremder Gegenstelle durchgelassen: %v", out)
	}
	if !parseTrust("*").has(hostOf("9.9.9.9")) || tr.has(hostOf("11.0.0.1")) || !tr.has(hostOf("[::1]:5")) {
		t.Errorf("trustSet")
	}
}

func TestFileIDAndOpen(t *testing.T) {
	dir := t.TempDir()
	good := "abcdefghijklmnopqrstuvwx"
	if fileID("/api/chat/files/"+good) != good {
		t.Fatal("fileID")
	}
	for _, p := range []string{"/api/chat/files/../../etc/passwd", "/api/chat/files/" + good + "/x", "/api/chat/files/" + good + ".part-1",
		"/api/chat/files/short", "/api/chat/filesx/" + good} {
		if fileID(p) != "" {
			t.Errorf("fileID(%q) sollte leer sein", p)
		}
	}
	if err := os.WriteFile(filepath.Join(dir, good), []byte("hallo"), 0o600); err != nil {
		t.Fatal(err)
	}
	f, n, err := openBlob(dir, good)
	if err != nil || n != 5 {
		t.Fatalf("openBlob: %v %d", err, n)
	}
	f.Close()
	link := "linklinklinklinklinklinklink"
	_ = os.Symlink("/etc/hostname", filepath.Join(dir, link))
	if _, _, err := openBlob(dir, link); err == nil {
		t.Error("symbolischer Verweis geoeffnet")
	}
	sub := "subdirsubdirsubdirsubdirsub"
	_ = os.Mkdir(filepath.Join(dir, sub), 0o700)
	if _, _, err := openBlob(dir, sub); err == nil {
		t.Error("Verzeichnis geoeffnet")
	}
	if _, _, err := openBlob(dir, "../"+good); err == nil {
		t.Error("Traversal")
	}
}

func TestEtag(t *testing.T) {
	if !etagMatch(`W/"x", "abc"`, `"abc"`) || !etagMatch("*", `"a"`) || etagMatch(`"abcd"`, `"abc"`) {
		t.Error("etagMatch")
	}
}

func TestLowerKeys(t *testing.T) {
	h := http.Header{}
	h.Set("Location", "/x")
	h.Set("Content-Length", "3")
	h.Add("Set-Cookie", "a=1")
	h.Add("Set-Cookie", "b=2")
	lowerKeys(h)
	if h["location"][0] != "/x" || len(h["set-cookie"]) != 2 || h["Content-Length"][0] != "3" || h["Location"] != nil {
		t.Errorf("lowerKeys: %v", h)
	}
}

func TestAuthzCacheAndServe(t *testing.T) {
	var calls int
	var mu sync.Mutex
	allow := true
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/internal/media/authz" {
			mu.Lock()
			calls++
			ok := allow
			mu.Unlock()
			if r.Header.Get(keyHeader) != "k" || r.Header.Get("X-Saga") != "1" || !ok {
				w.WriteHeader(404)
				return
			}
			_, _ = w.Write([]byte(`{"ok":true,"ttl":60,"headers":{"Cache-Control":"private, no-store","Content-Disposition":"attachment"}}`))
			return
		}
		w.WriteHeader(418) // an Python weitergeleitet
	}))
	defer up.Close()
	dir := t.TempDir()
	fid := "abcdefghijklmnopqrstuvwxyz"
	_ = os.WriteFile(filepath.Join(dir, fid), []byte("0123456789"), 0o600)
	c := &config{upstream: strings.TrimPrefix(up.URL, "http://"), blobDir: dir, trusted: parseTrust("127.0.0.1"), ttl: time.Minute,
		stall: 5 * time.Second, key: "k"}
	tr := newTransport()
	now := time.Now()
	m := &media{c: c, az: newAuthz(c, tr), proxy: newProxy(c, tr), lim: &limits{perIP: map[string]int{}, max: 10, maxIP: 10}}
	m.az.now = func() time.Time { return now }
	get := func(hdr map[string]string) *httptest.ResponseRecorder {
		r := httptest.NewRequest("GET", "/api/chat/files/"+fid, nil)
		r.Header.Set("Cookie", "saga=x")
		r.Header.Set("X-Saga", "1")
		for k, v := range hdr {
			r.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		m.ServeHTTP(w, r)
		return w
	}
	var wg sync.WaitGroup
	for i := 0; i < 10; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); get(nil) }()
	}
	wg.Wait()
	if calls != 1 {
		t.Errorf("10 parallele Abrufe: %d Abfragen statt 1", calls)
	}
	w := get(map[string]string{"Range": "bytes=2-4"})
	if w.Code != 206 || w.Body.String() != "234" || w.Header()["content-range"][0] != "bytes 2-4/10" || w.Header()["etag"][0] != `"`+fid+`"` {
		t.Errorf("Range: %d %q %v", w.Code, w.Body.String(), w.Header())
	}
	if w := get(map[string]string{"X-Saga": ""}); w.Code != 418 {
		t.Errorf("ohne X-Saga muss an Python gehen: %d", w.Code)
	}
	mu.Lock()
	allow = false
	mu.Unlock()
	if w := get(nil); w.Code != 206 && w.Code != 200 {
		t.Errorf("innerhalb TTL gemerkt: %d", w.Code)
	}
	now = now.Add(61 * time.Second)
	if w := get(nil); w.Code != 418 {
		t.Errorf("nach TTL neu gefragt und abgelehnt -> Python: %d", w.Code)
	}
	r := httptest.NewRequest("GET", "/internal/media/authz?fid="+fid, nil)
	w = httptest.NewRecorder()
	m.ServeHTTP(w, r)
	if w.Code != 404 {
		t.Errorf("/internal von aussen: %d", w.Code)
	}
}

func TestParseCRange(t *testing.T) {
	cases := []struct {
		v       string
		a, b, n int64
		ok      bool
	}{
		{"bytes 0-99/1000", 0, 99, 1000, true},
		{"bytes 900-999/1000", 900, 999, 1000, true},
		{"bytes 900-1000/1000", 0, 0, 0, false},
		{"bytes 5-4/1000", 0, 0, 0, false},
		{"bytes */1000", 0, 0, 0, false},
		{"bytes 0-1/x", 0, 0, 0, false},
		{" bytes 1-1/2 ", 1, 1, 2, true},
	}
	for _, c := range cases {
		a, b, n, ok := parseCRange(c.v)
		if ok != c.ok || (ok && (a != c.a || b != c.b || n != c.n)) {
			t.Errorf("%q: %d-%d/%d ok=%v", c.v, a, b, n, ok)
		}
	}
	if uploadID("/api/chat/uploads/../../x") != "" || uploadID("/api/chat/uploads/"+strings.Repeat("a", 24)) == "" {
		t.Errorf("uploadID")
	}
}

func TestBitsHave(t *testing.T) {
	have := []byte{0b00000101, 0b1} // Einheiten 0, 2, 8
	u := int64(1 << 20)
	if !bitsHave(have, u, 0, u-1) || bitsHave(have, u, 0, u) || !bitsHave(have, u, 2*u, 3*u-1) || !bitsHave(have, u, 8*u+5, 8*u+9) || bitsHave(have, u, 9*u, 9*u) {
		t.Errorf("bitsHave")
	}
	if bitsHave(nil, u, 0, 0) || bitsHave(have, 0, 0, 0) {
		t.Errorf("bitsHave leer")
	}
}

// S1b: nur eine von Saga bestaetigte Anmeldung verlaengert den Leerlauf; der interne Kopf geht nie an den Client.
func TestAuthHintFromUpstream(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/an" {
			w.Header().Set(authHint, "1")
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer up.Close()
	c := &config{upstream: strings.TrimPrefix(up.URL, "http://"), blobDir: t.TempDir(), trusted: parseTrust("127.0.0.1"), ttl: time.Minute,
		stall: 5 * time.Second, key: "k"}
	tr := newTransport()
	m := &media{c: c, az: newAuthz(c, tr), proxy: newProxy(c, tr), lim: &limits{perIP: map[string]int{}, max: 10, maxIP: 10}}
	for _, tc := range []struct {
		path string
		want bool
	}{{"/api/an", true}, {"/api/aus", false}} {
		conn := &trackedConn{l: &limitListener{}}
		r := httptest.NewRequest("GET", tc.path, nil)
		r.Header.Set("Cookie", "saga=irgendwas")
		r.Header.Set(authHint, "1") // vom Client eingeschleust: zaehlt nicht
		r = r.WithContext(context.WithValue(r.Context(), connKey{}, conn))
		w := httptest.NewRecorder()
		m.ServeHTTP(w, r)
		if conn.auth != tc.want || w.Header().Get(authHint) != "" {
			t.Errorf("%s: auth=%v (erwartet %v), Kopf an den Client %q", tc.path, conn.auth, tc.want, w.Header().Get(authHint))
		}
	}
}
