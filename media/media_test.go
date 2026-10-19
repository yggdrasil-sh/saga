package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
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
