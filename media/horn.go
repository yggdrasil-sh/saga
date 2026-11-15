package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"
)

// Eingebauter Gjallarhorn-Knoten (Saga 2.5, Konzept v2 §2.1/§2.2, Paket P3): saga-media startet "gjallarhorn node" als
// zweiten Kindprozess - nur, solange in Saga das Feature-Flag calls.horn an ist (Abfrage GET /internal/horn/conf alle
// hornPoll, mit dem gemeinsamen Geheimnis). Der Knoten lauscht auf HORN_PORT (Standard 8443, TCP und optional UDP) und
// verbindet sich fuer die Steuerung ueber localhost mit Saga (ws://127.0.0.1:MEDIA_INTERNAL_PORT/api/horn/ctl, Kopf
// X-Saga-Horn-Key = HORN_CTL_KEY, Python bekommt dasselbe als SAGA_HORN_KEY). Medienbytes laufen nur durch den Knoten,
// nie durch saga-media oder Python. Weitere HORN_*-Variablen (HORN_NAME, HORN_PUBLIC_URL, HORN_TLS_*, HORN_UDP ...)
// reicht saga-media unveraendert durch. SAGA_HORN=off: nie starten. Beim Abschalten des Flags bekommt er SIGTERM.
//
// Zustand fuer den Admin-Bereich (2.5, horn-admin): saga-media meldet Saga per POST /internal/horn/state, ob der Knoten
// laeuft bzw. warum nicht (kein Zertifikat/Name, Port belegt, sonstiger Abbruch mit gekuerzter letzter Fehlerzeile ohne
// Pfade), mit Zahl der Fehlversuche und naechstem Versuch. Bricht der Knoten ab, startet saga-media ihn mit Backoff neu
// (2 s ... 1 min, nach hornSlowAfter Fehlversuchen mit demselben Grund bis hornBackoffSlow). Dieselbe Fehlerzeile kommt
// nur bei Aenderung erneut ins Log. Aendert der Admin die Einstellungen (rev in /internal/horn/conf) oder schaltet er
// das Flag ein, startet ein wartender Knoten sofort.

const (
	hornPoll        = 5 * time.Second
	hornOKAfter     = time.Minute      // so lange gelaufen = gilt als gestartet (Fehlerzaehler zurueck)
	hornBackoffMax  = time.Minute      // Backoff bei wechselnden Gruenden
	hornBackoffSlow = 10 * time.Minute // Backoff, wenn der Knoten immer wieder mit demselben Grund abbricht
	hornSlowAfter   = 3                // ab so vielen gleichen Fehlversuchen in Folge gilt hornBackoffSlow
	hornBeat        = 30 * time.Second // unveraenderter Zustand: so oft trotzdem an Saga melden (Lebenszeichen)
	hornTail        = 8                // so viele letzte Ausgabezeilen merkt sich saga-media je Lauf
)

type horn struct {
	m    *media
	bin  string
	key  string
	env  []string
	port int
	name string // HORN_NAME (nur zur Anzeige im Admin-Bereich)

	mu      sync.Mutex
	cmd     *exec.Cmd
	done    chan struct{}
	stopped bool
	fails   int
	next    time.Time

	known    bool   // Saga hat schon einmal geantwortet
	on       bool   // letzter Stand des Flags
	rev      string // letzter Stand der Einstellungen (Admin speichert -> neuer Wert)
	t0       time.Time
	ok       bool            // laufender Prozess lebt laenger als hornOKAfter
	prev     map[string]bool // Ausgabezeilen des letzten schnellen Fehlversuchs (nicht erneut ins Log)
	reason   string          // letzter Abbruch: nocert | port | portperm | exit | start
	detail   string          // bei exit: letzte Fehlerzeile, gekuerzt, ohne Pfade
	same     int             // Fehlversuche in Folge mit demselben Grund
	slowNote bool            // Hinweis "nur noch alle 10 min" schon geschrieben
	sent     []byte          // zuletzt an Saga gemeldeter Zustand
	sentAt   time.Time
	kick     chan struct{} // Knoten beendet: Zustand gleich melden (nicht erst nach hornPoll)
}

// hornState: Meldung an Saga (POST /internal/horn/state). Keine Pfade, keine Geheimnisse.
type hornState struct {
	State  string `json:"state"`            // run | wait | off | none
	Reason string `json:"reason,omitempty"` // nocert | port | portperm | exit | start; bei none: disabled | nobin | portcfg
	Detail string `json:"detail,omitempty"`
	Fails  int    `json:"fails"`
	Next   int64  `json:"next,omitempty"`  // Unix-Sekunden des naechsten Versuchs (wait)
	Since  int64  `json:"since,omitempty"` // Unix-Sekunden des Starts (run)
	Port   int    `json:"port,omitempty"`
	Name   string `json:"name,omitempty"`
	// 2.5 "Ein Port": Betriebsart, Zertifikat, PROXY-Kopf (einport.go)
	Einport *einportInfo `json:"einport,omitempty"`
}

// newHorn -> nil und Grund, wenn der Knoten hier nie laufen soll (SAGA_HORN=off, kein Binaer, HORN_PORT ungueltig).
func newHorn(m *media, key string) (*horn, string) {
	if strings.EqualFold(os.Getenv("SAGA_HORN"), "off") {
		return nil, "disabled"
	}
	bin := envStr("GJALLARHORN_BIN", "")
	if bin == "" {
		if p, err := exec.LookPath("gjallarhorn"); err == nil {
			bin = p
		}
	}
	if bin == "" {
		return nil, "nobin"
	}
	port, err := envInt("HORN_PORT", 8443, 1, 65535)
	if err != nil {
		logf("HORN_PORT: %v - eingebauter Knoten aus", err)
		return nil, "portcfg"
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
	if e := m.edge; e != nil {
		// "Ein Port": Name, Adresse und Port kommen aus BASE_URL/TLS_LISTEN - alte HORN_NAME/HORN_PUBLIC_URL (eigener Name
		// fuer den Knoten) gelten nicht mehr; der Knoten lauscht selbst auf keinem TCP-Port (Sockets kommen aus der Weiche)
		drop := map[string]bool{"HORN_NAME": true, "HORN_PUBLIC_URL": true, "HORN_LISTEN": true, "HORN_PROXY_PROTOCOL": true,
			"HORN_TLS_CERT": true, "HORN_TLS_KEY": true, "HORN_EINPORT_FD": true, "HORN_EINPORT_HTTP_FD": true, "HORN_EINPORT_BACK": true}
		kept := env[:0]
		for _, kv := range env {
			k, v, _ := strings.Cut(kv, "=")
			if drop[k] {
				if (k == "HORN_NAME" || k == "HORN_PUBLIC_URL") && v != "" {
					logf("Gjallarhorn: %s wird in der Betriebsart \"Ein Port\" nicht gebraucht (Name aus BASE_URL) - ignoriert", k)
				}
				continue
			}
			kept = append(kept, kv)
		}
		env = append(kept, e.nodeEnv()...)
		port = m.c.basePort
	} else if l := m.link; l != nil {
		// 2.5 A3: internes HTTP (fd 3) und Saga-Adresse fuer den Tunnel-Betrieb (nur ohne eigenen Namen/Zertifikat)
		kept := env[:0]
		for _, kv := range env {
			if k, _, _ := strings.Cut(kv, "="); k != "HORN_INNER_HTTP_FD" && k != "HORN_TUNNEL_URL" && k != "HORN_EINPORT_FD" {
				kept = append(kept, kv)
			}
		}
		env = append(kept, l.nodeEnv()...)
	}
	set("HORN_LISTEN", ":"+strconv.Itoa(port))
	set("HORN_DATA", filepath.Join(envStr("STATE_DIR", "/data"), "horn"))
	env = append(env, "HORN_MODE=node", "HORN_CTL_KEY="+key,
		"HORN_SAGA_CTL=ws://"+net.JoinHostPort("127.0.0.1", strconv.Itoa(m.c.internalPort))+"/api/horn/ctl")
	name := cleanName(os.Getenv("HORN_NAME"))
	if m.edge != nil {
		name = m.c.baseHost
	}
	h := &horn{m: m, bin: bin, key: key, env: env, port: port, name: name, kick: make(chan struct{}, 1)}
	if m.edge != nil {
		m.edge.onState = h.poke
	}
	return h, ""
}

// loop fragt Saga nach dem Flag und startet bzw. beendet den Knoten.
// Bis Saga zum ersten Mal antwortet, jede Sekunde (kurzer Start), danach alle hornPoll.
func (h *horn) loop(ctx context.Context) {
	wait := 300 * time.Millisecond
	for {
		on, rev, ok := h.wanted(ctx)
		if ok {
			h.apply(on, rev)
			h.report(ctx)
			wait = hornPoll
		} else if wait < hornPoll {
			wait = time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		case <-h.kick:
		}
	}
}

func (h *horn) wanted(ctx context.Context) (bool, string, bool) {
	var out struct {
		On  bool   `json:"on"`
		Rev string `json:"rev"`
	}
	st, _, err := h.m.callPython(ctx, http.MethodGet, "/internal/horn/conf", nil, &out, nil)
	if err != nil || st != http.StatusOK {
		return false, "", false // Saga startet noch bzw. kennt den Pfad nicht: nichts aendern
	}
	return out.On, out.Rev, true
}

// poke: Zustand gleich an Saga melden (Zertifikat, Uebergabe bereit/weg).
func (h *horn) poke() {
	select {
	case h.kick <- struct{}{}:
	default:
	}
}

func (h *horn) apply(on bool, rev string) {
	if h.m.edge != nil {
		on = true // "Ein Port": der Knoten laeuft immer (Zertifikat fuer den Saga-Namen, TURN-TLS), Anrufe nur mit calls.horn
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped {
		return
	}
	now := time.Now()
	// eingeschaltet oder Einstellungen gespeichert: ein wartender Knoten startet sofort (ein laufender bleibt unberuehrt)
	if on && h.cmd == nil && (!h.on || (h.known && rev != h.rev)) {
		h.retryLocked()
	}
	h.known, h.on, h.rev = true, on, rev
	running := h.cmd != nil
	if running && !h.ok && now.Sub(h.t0) >= hornOKAfter {
		h.ok = true
		if h.fails > 0 {
			logf("Gjallarhorn: eingebauter Knoten laeuft wieder (nach %d %s)", h.fails, plural(h.fails, "Fehlversuch", "Fehlversuchen"))
		}
		h.fails, h.same, h.reason, h.detail, h.prev, h.slowNote = 0, 0, "", "", nil, false
	}
	switch {
	case on && !running && !now.Before(h.next):
		h.startLocked()
	case !on && running:
		logf("Gjallarhorn: Feature-Flag aus - beende den eingebauten Knoten")
		h.termLocked()
	}
}

// retryLocked: Backoff vergessen (naechster Versuch sofort, Grund kommt beim naechsten Abbruch wieder ins Log).
func (h *horn) retryLocked() {
	h.fails, h.same, h.next, h.prev, h.slowNote = 0, 0, time.Time{}, nil, false
}

func (h *horn) startLocked() {
	cmd := exec.Command(h.bin, "node")
	cmd.Env = h.env
	var extra []*os.File
	if e := h.m.edge; e != nil { // "Ein Port": Socket-Paar fuer die Uebergabe (fd 3), internes HTTP (fd 4)
		fs, err := e.nodeFiles()
		if err != nil {
			h.failLocked("start", "", time.Now())
			if h.same == 1 {
				logf("Gjallarhorn: Uebergabe an den eingebauten Knoten nicht vorbereitet: %v", err)
			}
			return
		}
		extra = fs
		cmd.ExtraFiles = fs
	} else if l := h.m.link; l != nil { // 2.5 A3: internes HTTP (fd 3, nodews.go)
		f, err := l.listenerFile()
		if err != nil {
			h.failLocked("start", "", time.Now())
			if h.same == 1 {
				logf("Gjallarhorn: internes HTTP fuer den eingebauten Knoten nicht vorbereitet: %v", err)
			}
			return
		}
		extra = []*os.File{f}
		cmd.ExtraFiles = extra
	}
	defer func() {
		for _, f := range extra {
			_ = f.Close()
		}
	}()
	out := newHornOut(os.Stderr, h.prev)
	cmd.Stdout, cmd.Stderr = out, out
	cmd.WaitDelay = 5 * time.Second // Ausgabe-Rohr haengt nicht ewig, falls ein Enkel es offen haelt
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		h.failLocked("start", "", time.Now())
		if h.same == 1 {
			logf("Gjallarhorn: kann den eingebauten Knoten nicht starten: %v - neuer Versuch in %v", err, time.Until(h.next).Round(time.Second))
		}
		return
	}
	done := make(chan struct{})
	h.cmd, h.done, h.ok = cmd, done, false
	t0 := time.Now()
	h.t0 = t0
	if h.same == 0 {
		logf("Gjallarhorn: eingebauter Knoten gestartet (PID %d)", cmd.Process.Pid)
	}
	go func() {
		err := cmd.Wait()
		close(done)
		lines, seen := out.finish()
		h.mu.Lock()
		defer h.mu.Unlock()
		if h.cmd != cmd {
			return
		}
		h.cmd = nil
		if h.stopped {
			return
		}
		now := time.Now()
		if now.Sub(t0) >= hornOKAfter {
			h.fails, h.same, h.reason, h.detail, h.slowNote = 0, 0, "", "", false
			h.prev = nil
		} else {
			h.prev = seen
		}
		reason, detail := hornReason(lines)
		h.failLocked(reason, detail, now)
		select {
		case h.kick <- struct{}{}:
		default:
		}
		wait := h.next.Sub(now).Round(time.Second)
		switch {
		case h.same == 1:
			logf("Gjallarhorn: %s - neuer Start in %v", hornText(reason, detail, err, h.port), wait)
		case h.next.Sub(now) >= hornBackoffSlow && !h.slowNote:
			h.slowNote = true
			logf("Gjallarhorn: eingebauter Knoten startet weiter nicht (%d Versuche, gleicher Grund) - neue Versuche alle %v, "+
				"Meldung nur bei Aenderung; Speichern unter Admin -> Anrufe startet sofort", h.fails, hornBackoffSlow)
		}
	}()
}

// failLocked zaehlt einen Fehlversuch und legt den naechsten Start fest.
func (h *horn) failLocked(reason, detail string, now time.Time) {
	if h.fails > 0 && reason == h.reason && detail == h.detail {
		h.same++
	} else {
		h.same, h.slowNote = 1, false
	}
	h.reason, h.detail = reason, detail
	h.fails++
	h.next = now.Add(hornBackoff(h.fails, h.same))
}

// hornBackoff: 2, 4, 8 ... s bis 1 min; derselbe Grund ab hornSlowAfter Mal in Folge bis 10 min.
func hornBackoff(fails, same int) time.Duration {
	if fails < 1 {
		fails = 1
	}
	if fails > 20 {
		fails = 20
	}
	d := 2 * time.Second << (fails - 1)
	lim := hornBackoffMax
	if same >= hornSlowAfter {
		lim = hornBackoffSlow
	}
	if d > lim || d <= 0 {
		d = lim
	}
	return d
}

// state: aktueller Zustand fuer Saga.
func (h *horn) state() hornState {
	h.mu.Lock()
	defer h.mu.Unlock()
	s := hornState{Fails: h.fails, Port: h.port, Name: h.name}
	if h.m.edge != nil {
		s.Einport = h.m.edge.info()
	}
	switch {
	case !h.on:
		s.State, s.Fails = "off", 0
	case h.cmd != nil:
		s.State, s.Since = "run", h.t0.Unix()
		if !h.ok && h.fails > 0 { // erneuter Versuch nach einem Abbruch: letzter Grund bleibt sichtbar
			s.Reason, s.Detail = h.reason, h.detail
		}
	default:
		s.State, s.Reason, s.Detail = "wait", h.reason, h.detail
		if !h.next.IsZero() {
			s.Next = h.next.Unix()
		}
	}
	return s
}

// report meldet den Zustand an Saga - bei Aenderung sofort, sonst alle hornBeat.
func (h *horn) report(ctx context.Context) {
	b, _ := json.Marshal(h.state())
	h.mu.Lock()
	due := !bytes.Equal(b, h.sent) || time.Since(h.sentAt) >= hornBeat
	h.mu.Unlock()
	if !due {
		return
	}
	if postHornState(ctx, h.m, json.RawMessage(b)) {
		h.mu.Lock()
		h.sent, h.sentAt = b, time.Now()
		h.mu.Unlock()
	}
}

func postHornState(ctx context.Context, m *media, v any) bool {
	st, _, err := m.callPython(ctx, http.MethodPost, "/internal/horn/state", v, nil, nil)
	return err == nil && st == http.StatusOK
}

// reportNone: kein eingebauter Knoten moeglich (SAGA_HORN=off, kein Binaer, HORN_PORT ungueltig) - Saga erfaehrt den
// Grund trotzdem (Admin -> Anrufe), bis Saga antwortet jede Sekunde, danach alle hornBeat * 2.
func reportNone(ctx context.Context, m *media, why string) {
	wait := time.Second
	for {
		st := hornState{State: "none", Reason: why}
		if m.edge != nil {
			st.Einport = m.edge.info() // "Ein Port" ohne Knoten: Zertifikat nur aus SAGA_TLS_CERT/SAGA_TLS_KEY
		}
		if postHornState(ctx, m, st) {
			wait = 2 * hornBeat
			if m.edge != nil {
				wait = hornBeat
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		}
	}
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

// --- Ausgabe des Knotens ---------------------------------------------------------------------------------------------

// hornOut reicht die Ausgabe des Knotens zeilenweise ins eigene Log weiter und merkt sich die letzten Zeilen (Grund des
// Abbruchs). Zeilen, die schon beim letzten schnellen Fehlversuch kamen (skip), schreibt es nicht noch einmal.
type hornOut struct {
	mu    sync.Mutex
	w     interface{ Write([]byte) (int, error) }
	skip  map[string]bool
	part  []byte
	tail  []string
	seen  map[string]bool
	ended bool
}

func newHornOut(w interface{ Write([]byte) (int, error) }, skip map[string]bool) *hornOut {
	return &hornOut{w: w, skip: skip, seen: map[string]bool{}}
}

func (o *hornOut) Write(p []byte) (int, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.part = append(o.part, p...)
	for {
		i := bytes.IndexByte(o.part, '\n')
		if i < 0 {
			break
		}
		o.line(string(o.part[:i]))
		o.part = o.part[i+1:]
	}
	if len(o.part) > 16<<10 { // ueberlange Zeile ohne Umbruch: als Zeile behandeln
		o.line(string(o.part))
		o.part = nil
	}
	return len(p), nil
}

func (o *hornOut) line(s string) {
	s = strings.TrimRight(s, "\r")
	if len(s) > 1000 {
		s = s[:1000]
	}
	if strings.TrimSpace(s) != "" {
		o.tail = append(o.tail, s)
		if len(o.tail) > hornTail {
			o.tail = o.tail[len(o.tail)-hornTail:]
		}
		if len(o.seen) < 64 {
			o.seen[s] = true
		}
	}
	if o.skip[s] {
		return
	}
	_, _ = o.w.Write([]byte(s + "\n"))
}

// finish: Rest ohne Zeilenumbruch ausgeben -> letzte Zeilen und alle gesehenen Zeilen dieses Laufs.
func (o *hornOut) finish() ([]string, map[string]bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.ended && len(o.part) > 0 {
		o.line(string(o.part))
		o.part = nil
	}
	o.ended = true
	return append([]string(nil), o.tail...), o.seen
}

// --- Grund des Abbruchs ----------------------------------------------------------------------------------------------

var (
	reHornPath  = regexp.MustCompile(`(^|[\s"'(=\[])(?:/[^\s/:,;()'"\[\]]+)+/?`) // Pfad am Wortanfang (URLs bleiben)
	reHornToken = regexp.MustCompile(`[A-Za-z0-9+/_=-]{24,}`)
	reHornName  = regexp.MustCompile(`^[A-Za-z0-9.-]{1,253}$`)
)

// hornReason ordnet die letzten Ausgabezeilen eines abgebrochenen Knotens einem Grund zu:
// nocert (kein Zertifikat, kein Name fuer ACME), port (Port belegt), portperm (Port unter 1024 ohne Recht),
// sonst exit mit der letzten Fehlerzeile (gekuerzt, ohne Pfade und lange Kennungen).
func hornReason(lines []string) (string, string) {
	last := ""
	for i := len(lines) - 1; i >= 0; i-- {
		if s := strings.TrimSpace(lines[i]); s != "" {
			if strings.HasPrefix(s, "fehler:") || last == "" {
				last = s
			}
			if strings.HasPrefix(s, "fehler:") {
				break
			}
		}
	}
	low := strings.ToLower(last)
	switch {
	case strings.Contains(low, "kein zertifikat"):
		return "nocert", ""
	case strings.Contains(low, "address already in use"):
		return "port", ""
	case strings.Contains(low, "permission denied") && (strings.Contains(low, "bind") || strings.Contains(low, "listen")):
		return "portperm", ""
	}
	return "exit", hornClean(strings.TrimSpace(strings.TrimPrefix(last, "fehler:")))
}

// hornClean: Fehlerzeile fuer den Admin-Bereich - ohne Pfade, ohne lange Kennungen (Schluessel), ohne Steuerzeichen,
// hoechstens 160 Zeichen.
func hornClean(s string) string {
	s = strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7f {
			return ' '
		}
		return r
	}, s)
	s = reHornPath.ReplaceAllString(s, "${1}…")
	s = reHornToken.ReplaceAllString(s, "…")
	s = strings.TrimSpace(s)
	if utf8.RuneCountInString(s) > 160 {
		r := []rune(s)
		s = string(r[:159]) + "…"
	}
	return s
}

func cleanName(s string) string {
	s = strings.TrimSpace(s)
	if !reHornName.MatchString(s) || strings.EqualFold(s, "localhost") {
		return ""
	}
	return s
}

// hornText: Log-Zeile zum Grund.
func hornText(reason, detail string, err error, port int) string {
	switch reason {
	case "nocert":
		return "eingebauter Knoten startet nicht: kein Zertifikat (HORN_NAME fuer ACME, HORN_TLS_CERT/HORN_TLS_KEY oder HORN_TLS_SELF=1 setzen)"
	case "port":
		return fmt.Sprintf("eingebauter Knoten startet nicht: Port %d belegt", port)
	case "portperm":
		return fmt.Sprintf("eingebauter Knoten startet nicht: Port %d braucht Sonderrechte (HORN_PORT ab 1024 waehlen)", port)
	}
	if detail != "" {
		return fmt.Sprintf("Knoten beendet (%v): %s", err, detail)
	}
	return fmt.Sprintf("Knoten beendet (%v)", err)
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}
