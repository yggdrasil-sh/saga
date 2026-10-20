package main

import (
	"context"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// Eingebauter Gjallarhorn-Knoten (Saga 2.5, Konzept v2 §2.1/§2.2, Paket P3): saga-media startet "gjallarhorn node" als
// zweiten Kindprozess - nur, solange in Saga das Feature-Flag calls.horn an ist (Abfrage GET /internal/horn/conf alle
// hornPoll, mit dem gemeinsamen Geheimnis). Der Knoten lauscht auf HORN_PORT (Standard 8443, TCP und optional UDP) und
// verbindet sich fuer die Steuerung ueber localhost mit Saga (ws://127.0.0.1:MEDIA_INTERNAL_PORT/api/horn/ctl, Kopf
// X-Saga-Horn-Key = HORN_CTL_KEY, Python bekommt dasselbe als SAGA_HORN_KEY). Medienbytes laufen nur durch den Knoten,
// nie durch saga-media oder Python. Weitere HORN_*-Variablen (HORN_NAME, HORN_PUBLIC_URL, HORN_TLS_*, HORN_UDP ...)
// reicht saga-media unveraendert durch. SAGA_HORN=off: nie starten. Bricht der Knoten ab, startet saga-media ihn mit
// Backoff (2 ... 60 s) neu; beim Abschalten des Flags bekommt er SIGTERM.

const hornPoll = 5 * time.Second

type horn struct {
	m       *media
	bin     string
	key     string
	env     []string
	mu      sync.Mutex
	cmd     *exec.Cmd
	done    chan struct{}
	stopped bool
	fails   int
	next    time.Time
}

// newHorn -> nil, wenn der Knoten hier nie laufen soll (SAGA_HORN=off, kein Binaer).
func newHorn(m *media, key string) *horn {
	if strings.EqualFold(os.Getenv("SAGA_HORN"), "off") {
		return nil
	}
	bin := envStr("GJALLARHORN_BIN", "")
	if bin == "" {
		if p, err := exec.LookPath("gjallarhorn"); err == nil {
			bin = p
		}
	}
	if bin == "" {
		return nil
	}
	port, err := envInt("HORN_PORT", 8443, 1, 65535)
	if err != nil {
		logf("HORN_PORT: %v - eingebauter Knoten aus", err)
		return nil
	}
	env := []string{}
	for _, kv := range os.Environ() {
		k, _, _ := strings.Cut(kv, "=")
		switch k {
		case "SAGA_MEDIA_KEY", "SAGA_HORN_KEY", "HORN_CTL_KEY", "HORN_SAGA_CTL", "HORN_MODE", "HORN_DEV":
			continue // Geheimnisse von Saga nie an den Knoten; Betriebsart legt saga-media fest
		}
		env = append(env, kv)
	}
	set := func(k, v string) {
		if os.Getenv(k) == "" {
			env = append(env, k+"="+v)
		}
	}
	set("HORN_LISTEN", ":"+strconv.Itoa(port))
	set("HORN_DATA", filepath.Join(envStr("STATE_DIR", "/data"), "horn"))
	env = append(env, "HORN_MODE=node", "HORN_CTL_KEY="+key,
		"HORN_SAGA_CTL=ws://"+net.JoinHostPort("127.0.0.1", strconv.Itoa(m.c.internalPort))+"/api/horn/ctl")
	return &horn{m: m, bin: bin, key: key, env: env}
}

// loop fragt Saga nach dem Flag und startet bzw. beendet den Knoten.
// Bis Saga zum ersten Mal antwortet, jede Sekunde (kurzer Start), danach alle hornPoll.
func (h *horn) loop(ctx context.Context) {
	wait := 300 * time.Millisecond
	for {
		on, ok := h.wanted(ctx)
		if ok {
			h.apply(on)
			wait = hornPoll
		} else if wait < hornPoll {
			wait = time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
}

func (h *horn) wanted(ctx context.Context) (bool, bool) {
	var out struct {
		On bool `json:"on"`
	}
	st, _, err := h.m.callPython(ctx, http.MethodGet, "/internal/horn/conf", nil, &out, nil)
	if err != nil || st != http.StatusOK {
		return false, false // Saga startet noch bzw. kennt den Pfad nicht: nichts aendern
	}
	return out.On, true
}

func (h *horn) apply(on bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped {
		return
	}
	running := h.cmd != nil
	switch {
	case on && !running && time.Now().After(h.next):
		h.startLocked()
	case !on && running:
		logf("Gjallarhorn: Feature-Flag aus - beende den eingebauten Knoten")
		h.termLocked()
	}
}

func (h *horn) startLocked() {
	cmd := exec.Command(h.bin, "node")
	cmd.Env = h.env
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		h.failLocked()
		logf("Gjallarhorn: kann %s nicht starten: %v", h.bin, err)
		return
	}
	done := make(chan struct{})
	h.cmd, h.done = cmd, done
	logf("Gjallarhorn: eingebauter Knoten gestartet (PID %d)", cmd.Process.Pid)
	t0 := time.Now()
	go func() {
		err := cmd.Wait()
		close(done)
		h.mu.Lock()
		defer h.mu.Unlock()
		if h.cmd != cmd {
			return
		}
		h.cmd = nil
		if h.stopped {
			return
		}
		if time.Since(t0) > time.Minute {
			h.fails = 0
		}
		h.failLocked()
		logf("Gjallarhorn: Knoten beendet (%v) - neuer Start in %v", err, time.Until(h.next).Round(time.Second))
	}()
}

func (h *horn) failLocked() {
	h.fails++
	d := 2 * time.Second << (h.fails - 1)
	if d > time.Minute || d <= 0 {
		d = time.Minute
	}
	h.next = time.Now().Add(d)
}

// termLocked: SIGTERM, nach 10 s SIGKILL (wartet nicht unter der Sperre).
func (h *horn) termLocked() {
	cmd, done := h.cmd, h.done
	if cmd == nil {
		return
	}
	h.cmd = nil
	_ = cmd.Process.Signal(syscall.SIGTERM)
	go func() {
		select {
		case <-done:
		case <-time.After(10 * time.Second):
			_ = cmd.Process.Kill()
		}
	}()
}

// stop beendet den Knoten endgueltig (saga-media endet) und wartet hoechstens d.
func (h *horn) stop(d time.Duration) {
	h.mu.Lock()
	h.stopped = true
	cmd, done := h.cmd, h.done
	h.termLocked()
	h.mu.Unlock()
	if cmd == nil {
		return
	}
	select {
	case <-done:
	case <-time.After(d):
		_ = cmd.Process.Kill()
	}
}

// running: PID des Knotens oder 0 (Tests, Diagnose).
func (h *horn) running() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.cmd == nil || h.cmd.Process == nil {
		return 0
	}
	return h.cmd.Process.Pid
}
