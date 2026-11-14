package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// Eingebauter Knoten (2.5, horn-admin): Grund-Erkennung, Bereinigung der Fehlerzeile, Backoff, Log nur bei Aenderung,
// sofortiger Neustart bei geaenderten Einstellungen, Zustandsmeldung an Saga.

func TestHornReason(t *testing.T) {
	cases := []struct {
		lines          []string
		reason, detail string
	}{
		{[]string{"fehler: kein Zertifikat: HORN_TLS_CERT/HORN_TLS_KEY, /data/tls/fullchain.pem, HORN_TLS_SELF=1 oder HORN_NAME fuer ACME setzen"}, "nocert", ""},
		{[]string{"kandidaten: x", "fehler: listen tcp :8443: bind: address already in use"}, "port", ""},
		{[]string{"fehler: listen tcp :443: bind: permission denied"}, "portperm", ""},
		{[]string{"fehler: open /data/horn/node.key: permission denied"}, "exit", "open …: permission denied"},
		{[]string{"fehler: HORN_MODE: probe oder node", "spaeter noch etwas"}, "exit", "HORN_MODE: probe oder node"},
		{[]string{"panic: runtime error", ""}, "exit", "panic: runtime error"},
		{nil, "exit", ""},
	}
	for _, c := range cases {
		r, d := hornReason(c.lines)
		if r != c.reason || d != c.detail {
			t.Errorf("%q -> %q %q, erwartet %q %q", c.lines, r, d, c.reason, c.detail)
		}
	}
}

func TestHornClean(t *testing.T) {
	s := hornClean("fehler beim laden von /data/horn/tls/privkey.pem und C:/x/y: schluessel 0123456789abcdef0123456789abcdef\x1b[0m")
	if strings.Contains(s, "/data") || strings.Contains(s, "privkey") || strings.Contains(s, "0123456789abcdef") || strings.ContainsRune(s, 0x1b) {
		t.Fatalf("Pfad/Kennung/Steuerzeichen nicht entfernt: %q", s)
	}
	if !strings.Contains(s, "fehler beim laden von") {
		t.Fatalf("Text verloren: %q", s)
	}
	if u := hornClean("get https://saga.example.org/api/horn/ctl: eof"); strings.Contains(u, "/api") || !strings.Contains(u, "https:") {
		t.Fatalf("URL: %q", u)
	}
	long := hornClean(strings.Repeat("ab ", 200))
	if n := len([]rune(long)); n > 160 || !strings.HasSuffix(long, "…") {
		t.Fatalf("Kuerzung: %d %q", n, long)
	}
	if cleanName("localhost") != "" || cleanName("horn-0.example.org") != "horn-0.example.org" || cleanName("a b") != "" {
		t.Fatal("cleanName")
	}
}

func TestHornBackoff(t *testing.T) {
	// wechselnde Gruende: hoechstens 1 min
	want := []time.Duration{2, 4, 8, 16, 32, 60, 60}
	for i, w := range want {
		if d := hornBackoff(i+1, 1); d != w*time.Second {
			t.Errorf("fails %d: %v, erwartet %v", i+1, d, w*time.Second)
		}
	}
	// gleicher Grund in Folge: weiter verdoppeln bis 10 min
	h := &horn{}
	var got []time.Duration
	now := time.Unix(1_700_000_000, 0)
	for i := 0; i < 12; i++ {
		h.failLocked("nocert", "", now)
		got = append(got, h.next.Sub(now))
	}
	exp := []time.Duration{2, 4, 8, 16, 32, 64, 128, 256, 512, 600, 600, 600}
	for i := range exp {
		if got[i] != exp[i]*time.Second {
			t.Fatalf("gleicher Grund: %v, erwartet %v (Sekunden)", got, exp)
		}
	}
	if h.same != 12 || h.fails != 12 {
		t.Fatalf("same %d fails %d", h.same, h.fails)
	}
	// anderer Grund: Zaehler "gleich" von vorn, Grenze wieder 1 min
	h.failLocked("port", "", now)
	if h.same != 1 || h.next.Sub(now) != time.Minute {
		t.Fatalf("anderer Grund: same %d next %v", h.same, h.next.Sub(now))
	}
}

func TestHornOutSkip(t *testing.T) {
	var b bytes.Buffer
	o := newHornOut(&b, nil)
	_, _ = o.Write([]byte("kandidaten: a\nfehler: kein Zert"))
	_, _ = o.Write([]byte("ifikat\n"))
	lines, seen := o.finish()
	if b.String() != "kandidaten: a\nfehler: kein Zertifikat\n" || len(lines) != 2 || !seen["fehler: kein Zertifikat"] {
		t.Fatalf("erster Lauf: %q %q", b.String(), lines)
	}
	b.Reset()
	o2 := newHornOut(&b, seen)
	_, _ = o2.Write([]byte("kandidaten: a\nneu: zeile\nfehler: kein Zertifikat\nrest ohne umbruch"))
	lines, _ = o2.finish()
	if b.String() != "neu: zeile\nrest ohne umbruch\n" {
		t.Fatalf("zweiter Lauf schreibt Bekanntes erneut: %q", b.String())
	}
	if len(lines) != 4 || lines[3] != "rest ohne umbruch" {
		t.Fatalf("letzte Zeilen: %q", lines)
	}
}

// captureStdout: logf schreibt nach os.Stdout.
func captureStdout(t *testing.T) func() string {
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stdout
	os.Stdout = w
	var buf bytes.Buffer
	done := make(chan struct{})
	go func() { _, _ = io.Copy(&buf, r); close(done) }()
	return func() string {
		os.Stdout = old
		_ = w.Close()
		<-done
		return buf.String()
	}
}

// fakeSaga: /internal/horn/conf mit einstellbarem Flag/rev, sammelt /internal/horn/state.
type fakeSaga struct {
	mu     sync.Mutex
	on     bool
	rev    string
	states []hornState
}

func (f *fakeSaga) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get(keyHeader) != "k" {
		http.NotFound(w, r)
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	switch r.URL.Path {
	case "/internal/horn/conf":
		_ = json.NewEncoder(w).Encode(map[string]any{"on": f.on, "rev": f.rev})
	case "/internal/horn/state":
		var s hornState
		_ = json.NewDecoder(r.Body).Decode(&s)
		f.states = append(f.states, s)
		_, _ = w.Write([]byte(`{"ok":true}`))
	default:
		http.NotFound(w, r)
	}
}

func (f *fakeSaga) last() hornState {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.states) == 0 {
		return hornState{}
	}
	return f.states[len(f.states)-1]
}

func TestHornNoCertLoop(t *testing.T) {
	dir := t.TempDir()
	starts := filepath.Join(dir, "starts")
	bin := filepath.Join(dir, "gjallarhorn")
	script := "#!/bin/sh\necho x >> " + starts + "\necho 'fehler: kein Zertifikat: HORN_TLS_CERT/HORN_TLS_KEY, /data/tls/fullchain.pem, HORN_TLS_SELF=1 oder HORN_NAME fuer ACME setzen' >&2\nexit 1\n"
	if err := os.WriteFile(bin, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	fs := &fakeSaga{on: true, rev: "1"}
	up := httptest.NewServer(fs)
	defer up.Close()
	c := &config{upstream: strings.TrimPrefix(up.URL, "http://"), key: "k", internalPort: 1}
	m := &media{c: c, client: &http.Client{}}
	t.Setenv("GJALLARHORN_BIN", bin)
	t.Setenv("HORN_PORT", "8443")
	t.Setenv("HORN_NAME", "")
	t.Setenv("SAGA_HORN", "")
	h, why := newHorn(m, "geheim")
	if h == nil {
		t.Fatalf("newHorn: %s", why)
	}
	nstarts := func() int {
		b, _ := os.ReadFile(starts)
		return bytes.Count(b, []byte("x"))
	}
	// Knoten-Ausgabe (os.Stderr) mitlesen
	er, ew, _ := os.Pipe()
	oldErr := os.Stderr
	os.Stderr = ew
	var errBuf bytes.Buffer
	errDone := make(chan struct{})
	go func() { _, _ = io.Copy(&errBuf, er); close(errDone) }()
	out := captureStdout(t)

	ctx := context.Background()
	step := func() { h.apply(fs.on, fs.rev); h.report(ctx) }
	waitExit := func() {
		for i := 0; i < 200 && h.running() != 0; i++ {
			time.Sleep(10 * time.Millisecond)
		}
		for i := 0; i < 200; i++ { // Wait-Goroutine traegt den Abbruch ein
			h.mu.Lock()
			f := h.fails
			h.mu.Unlock()
			if f > 0 {
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	step()
	waitExit()
	step()
	st := fs.last()
	if st.State != "wait" || st.Reason != "nocert" || st.Fails != 1 || st.Port != 8443 || st.Detail != "" || st.Next == 0 {
		t.Fatalf("Zustand nach erstem Abbruch: %+v", st)
	}
	// drei weitere Fehlversuche ohne Warten (next von Hand vorziehen)
	for i := 0; i < 3; i++ {
		h.mu.Lock()
		h.next = time.Time{}
		h.mu.Unlock()
		f0 := h.fails
		step()
		for j := 0; j < 300; j++ {
			h.mu.Lock()
			f := h.fails
			h.mu.Unlock()
			if f > f0 && h.running() == 0 {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	h.mu.Lock()
	fails, same := h.fails, h.same
	h.mu.Unlock()
	if fails != 4 || same != 4 || nstarts() != 4 {
		t.Fatalf("fails %d same %d starts %d", fails, same, nstarts())
	}
	// gleicher Grund ab dem 3. Mal: Backoff ueber 1 min hinaus erlaubt (4. Versuch 16 s, Grenze 10 min)
	if d := hornBackoff(9, same+5); d != 512*time.Second {
		t.Fatalf("Backoff gleicher Grund: %v", d)
	}
	// Einstellungen gespeichert (rev neu): sofort neuer Start trotz Backoff, Zaehler von vorn
	h.mu.Lock()
	h.next = time.Now().Add(time.Hour)
	h.mu.Unlock()
	fs.mu.Lock()
	fs.rev = "2"
	fs.mu.Unlock()
	n0 := nstarts()
	step()
	waitExit()
	for i := 0; i < 200 && nstarts() == n0; i++ {
		time.Sleep(10 * time.Millisecond)
	}
	if nstarts() != n0+1 {
		t.Fatalf("rev geaendert: kein sofortiger Start (%d -> %d)", n0, nstarts())
	}
	time.Sleep(100 * time.Millisecond)
	// Flag aus -> Zustand off
	fs.mu.Lock()
	fs.on = false
	fs.mu.Unlock()
	step()
	if st := fs.last(); st.State != "off" {
		t.Fatalf("Flag aus: %+v", st)
	}
	logs := out()
	os.Stderr = oldErr
	_ = ew.Close()
	<-errDone
	// Log: Grund zweimal (erster Abbruch + nach dem Speichern), "gestartet" nur ausserhalb der Fehlerserie
	if n := strings.Count(logs, "startet nicht: kein Zertifikat"); n != 2 {
		t.Fatalf("Grund-Zeile %d mal statt 2:\n%s", n, logs)
	}
	if n := strings.Count(logs, "gestartet (PID"); n != 2 {
		t.Fatalf("'gestartet' %d mal statt 2:\n%s", n, logs)
	}
	if n := strings.Count(errBuf.String(), "fehler: kein Zertifikat"); n != 2 {
		t.Fatalf("Knoten-Zeile %d mal statt 2:\n%s", n, errBuf.String())
	}
}

func TestHornStateNone(t *testing.T) {
	fs := &fakeSaga{}
	up := httptest.NewServer(fs)
	defer up.Close()
	m := &media{c: &config{upstream: strings.TrimPrefix(up.URL, "http://"), key: "k"}, client: &http.Client{}}
	t.Setenv("SAGA_HORN", "off")
	if h, why := newHorn(m, "x"); h != nil || why != "disabled" {
		t.Fatalf("SAGA_HORN=off: %v %q", h, why)
	}
	t.Setenv("SAGA_HORN", "")
	t.Setenv("GJALLARHORN_BIN", "")
	t.Setenv("PATH", t.TempDir())
	if h, why := newHorn(m, "x"); h != nil || why != "nobin" {
		t.Fatalf("ohne Binaer: %v %q", h, why)
	}
	if !postHornState(context.Background(), m, hornState{State: "none", Reason: "nobin"}) {
		t.Fatal("Meldung nicht angenommen")
	}
	if st := fs.last(); st.State != "none" || st.Reason != "nobin" {
		t.Fatalf("%+v", st)
	}
}
