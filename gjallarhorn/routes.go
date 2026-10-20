package main

import (
	"embed"
	"encoding/json"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/turn/v5"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

//go:embed web
var webFS embed.FS

func (s *server) routes() http.Handler {
	m := http.NewServeMux()
	sub, _ := fs.Sub(webFS, "web")
	static := http.FileServer(http.FS(sub))
	page := func(w http.ResponseWriter, r *http.Request) { s.secHeaders(w, r); static.ServeHTTP(w, r) }
	m.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) { _, _ = io.WriteString(w, "ok\n") })
	m.HandleFunc("GET /api/session", s.apiSession)
	m.HandleFunc("GET /ws", s.apiWS)
	m.HandleFunc("GET /probe", s.apiWS) // Kandidatenmessung der Geraete (§2.6)
	m.HandleFunc("GET /api/stats", s.apiStats)
	if s.node != nil {
		s.sfuRoutes(m)
		m.HandleFunc("OPTIONS /api/sfu/", func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusNoContent) })
		if s.cfg.Dev {
			m.HandleFunc("GET /dev/{$}", page)
			m.HandleFunc("GET /dev/sfu.js", page)
			m.HandleFunc("GET /style.css", page)
		}
		return s.cors(m)
	}
	m.HandleFunc("GET /{$}", page)
	m.HandleFunc("GET /app.js", page)
	m.HandleFunc("GET /e2e-worker.js", page)
	m.HandleFunc("GET /style.css", page)
	m.HandleFunc("POST /api/leg", s.apiLeg)
	m.HandleFunc("GET /api/leg/{id}", s.apiLegStats)
	m.HandleFunc("DELETE /api/leg/{id}", s.apiLegDelete)
	if !s.cfg.Bench {
		m.HandleFunc("POST /api/results", s.apiSaveResult)
		m.HandleFunc("GET /results", s.apiResults)
	}
	return m
}

func (s *server) secHeaders(w http.ResponseWriter, r *http.Request) {
	h := w.Header()
	h.Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; worker-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob: mediastream:; connect-src 'self' wss://"+r.Host)
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("Referrer-Policy", "no-referrer")
	h.Set("Cache-Control", "no-store")
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

// ipKey: Client-Adresse (ggf. aus PROXY v2) nur als Schluessel im Speicher.
func ipKey(r *http.Request) string {
	h, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return h
}

func (s *server) apiSession(w http.ResponseWriter, r *http.Request) {
	if s.node != nil { // TURN-Zugang nur gegen Medien-Token (/api/sfu/turn)
		writeJSON(w, 200, map[string]any{"version": version, "build": buildID(), "name": s.cfg.Name, "mode": "node",
			"udp": s.sfuUDP != nil, "udp_check": s.UDPCheck(), "dev": s.cfg.Dev})
		return
	}
	user, pass, err := turn.GenerateLongTermCredentials(s.turnSecret, 30*time.Minute)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "turn"})
		return
	}
	writeJSON(w, 200, map[string]any{
		"version":   version,
		"build":     buildID(),
		"name":      s.cfg.Name,
		"udp":       s.apiUDP != nil,
		"udp_check": s.UDPCheck(),
		"turn":      map[string]string{"username": user, "credential": pass},
	})
}

func (s *server) apiLeg(w http.ResponseWriter, r *http.Request) {
	var req legReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 256<<10)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "json"})
		return
	}
	l, answer, err := s.newLeg(req, ipKey(r))
	if err == errLimit {
		writeJSON(w, 429, map[string]string{"error": err.Error()})
		return
	}
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]string{"id": l.id, "answer": answer})
}

func (s *server) getLeg(id string) *leg {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.legs[id]
}

func (s *server) apiLegStats(w http.ResponseWriter, r *http.Request) {
	l := s.getLeg(r.PathValue("id"))
	if l == nil {
		writeJSON(w, 404, map[string]string{"error": "unbekannt"})
		return
	}
	writeJSON(w, 200, l.stats())
}

func (s *server) apiLegDelete(w http.ResponseWriter, r *http.Request) {
	l := s.getLeg(r.PathValue("id"))
	if l == nil {
		writeJSON(w, 404, map[string]string{"error": "unbekannt"})
		return
	}
	st := l.stats()
	if l.path == "turn" {
		l.mu.Lock()
		pc, created, iceAt, con := l.pc, l.created, l.iceAt, l.connected
		l.mu.Unlock()
		st.TURN = s.ttrace.setup(pc, created, iceAt, con)
	}
	s.closeLeg(l)
	writeJSON(w, 200, st)
}

// apiWS: Echo fuer den WSS-Ping (RTT ueber TLS/TCP 443). Erlaubt: eigene Herkunft und die Saga-Seiten (P3).
func (s *server) apiWS(w http.ResponseWriter, r *http.Request) {
	up := websocket.Upgrader{ReadBufferSize: 1024, WriteBufferSize: 1024, CheckOrigin: func(r *http.Request) bool {
		o := r.Header.Get("Origin")
		return o == "" || strings.TrimPrefix(o, "https://") == r.Host || s.originOK(o)
	}}
	c, err := up.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer c.Close()
	c.SetReadLimit(512)
	_ = c.SetReadDeadline(time.Now().Add(15 * time.Minute))
	for {
		mt, b, err := c.ReadMessage()
		if err != nil {
			return
		}
		if err := c.WriteMessage(mt, b); err != nil {
			return
		}
	}
}

// nodeStats sind Prozesswerte des Knotens (fuer bench und Bericht).
type nodeStats struct {
	CPUns         int64   `json:"cpu_ns"`
	RSS           int64   `json:"rss"`
	Legs          int     `json:"legs"`
	Sources       int     `json:"sources"`
	Outs          int     `json:"outs"`
	PktsIn        uint64  `json:"pkts_in"`
	BytesIn       uint64  `json:"bytes_in"`
	PktsOut       uint64  `json:"pkts_out"`
	BytesOut      uint64  `json:"bytes_out"`
	FramesDropped uint64  `json:"frames_dropped"`
	Stalls        uint64  `json:"stalls"`
	Overflow      uint64  `json:"overflow"`
	WriteErrs     uint64  `json:"write_errs"`
	PLIs          uint64  `json:"plis"`
	Mallocs       uint64  `json:"mallocs"`
	Goroutines    int     `json:"goroutines"`
	TCPConns      uint64  `json:"tcp_conns"`
	BBRConns      uint64  `json:"bbr_conns"`
	UDPDropped    uint64  `json:"udp_non_webrtc"`
	MaxQMs        float64 `json:"max_queue_ms"`
	FwdP95us      float64 `json:"fwd_p95_us"`
	Uptime        float64 `json:"uptime"`
	TIDSkipped    uint64  `json:"tid_skipped"`
	Switches      uint64  `json:"switches"`
	Pauses        uint64  `json:"pauses"`
	WriteStalls   uint64  `json:"write_stalls"`
	PLIp95ms      float64 `json:"pli_p95_ms"`
	TailProbes    uint64  `json:"tail_probes"`
}

func (s *server) nodeStats() nodeStats {
	var ru syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	ns := nodeStats{
		CPUns: ru.Utime.Nano() + ru.Stime.Nano(), RSS: readRSS(),
		PktsIn: fwd.Totals.PktsIn.Load(), BytesIn: fwd.Totals.BytesIn.Load(),
		PktsOut: fwd.Totals.PktsOut.Load(), BytesOut: fwd.Totals.BytesOut.Load(),
		FramesDropped: fwd.Totals.FramesDropped.Load(), Stalls: fwd.Totals.Stalls.Load(),
		Overflow: fwd.Totals.Overflow.Load(), WriteErrs: fwd.Totals.WriteErrs.Load(), PLIs: fwd.Totals.PLIs.Load(),
		Mallocs: ms.Mallocs, Goroutines: runtime.NumGoroutine(),
		TCPConns: s.mux.Stats.Accepted.Load(), BBRConns: s.mux.Stats.BBR.Load(),
		Uptime:     time.Since(s.start).Seconds(),
		TIDSkipped: fwd.Totals.TIDSkipped.Load(), Switches: fwd.Totals.Switches.Load(), Pauses: fwd.Totals.Pauses.Load(),
		WriteStalls: fwd.Totals.WriteStalls.Load(), PLIp95ms: fwd.PLIRespP(0.95), TailProbes: netx.TailProbes.Load(),
	}
	if s.udp != nil {
		ns.UDPDropped = s.udp.Dropped.Load()
	}
	ns.Sources, ns.Outs = s.room.Counts()
	s.mu.Lock()
	ns.Legs = len(s.legs)
	for _, l := range s.legs {
		if l.fl == nil {
			continue
		}
		if q := float64(l.fl.Stats.MaxAgeNs.Load()) / 1e6; q > ns.MaxQMs {
			ns.MaxQMs = q
		}
		if p := l.fl.Stats.P95us(); p > ns.FwdP95us {
			ns.FwdP95us = p
		}
	}
	s.mu.Unlock()
	return ns
}

func readRSS() int64 {
	b, err := os.ReadFile("/proc/self/status")
	if err != nil {
		var ru syscall.Rusage
		_ = syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
		return ru.Maxrss * 1024
	}
	for _, ln := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(ln, "VmRSS:") {
			var kb int64
			for _, f := range strings.Fields(ln[6:]) {
				for _, ch := range f {
					if ch >= '0' && ch <= '9' {
						kb = kb*10 + int64(ch-'0')
					}
				}
				break
			}
			return kb * 1024
		}
	}
	return 0
}

func (s *server) apiStats(w http.ResponseWriter, r *http.Request) {
	ns := s.nodeStats()
	if !s.cfg.Bench {
		// oeffentlich nur Zaehler, keine Prozessdetails noetig
		writeJSON(w, 200, map[string]any{"legs": ns.Legs, "pkts_out": ns.PktsOut, "bytes_out": ns.BytesOut,
			"frames_dropped": ns.FramesDropped, "bbr_conns": ns.BBRConns, "tcp_conns": ns.TCPConns,
			"ice_tcp": s.mux.Stats.ICETCP.Load(), "turn_tls": s.mux.Stats.TURN.Load(), "https": s.mux.Stats.HTTP.Load()})
		return
	}
	writeJSON(w, 200, ns)
}

// verbotene Schluessel: alles, was Adressen enthalten koennte.
var scrubKeys = map[string]bool{"address": true, "ip": true, "relatedaddress": true, "relatedport": true,
	"port": true, "url": true, "foundation": true, "usernamefragment": true, "candidate": true, "remoteaddr": true}

func scrub(v any) any {
	switch t := v.(type) {
	case map[string]any:
		for k, x := range t {
			if scrubKeys[strings.ToLower(k)] {
				delete(t, k)
				continue
			}
			t[k] = scrub(x)
		}
	case []any:
		for i := range t {
			t[i] = scrub(t[i])
		}
	}
	return v
}

func (s *server) apiSaveResult(w http.ResponseWriter, r *http.Request) {
	var v map[string]any
	if err := json.NewDecoder(io.LimitReader(r.Body, 512<<10)).Decode(&v); err != nil {
		writeJSON(w, 400, map[string]string{"error": "json"})
		return
	}
	scrub(v)
	if n, _ := v["net"].(string); len(n) > 80 {
		v["net"] = n[:80]
	}
	v["saved"] = time.Now().UTC().Format(time.RFC3339)
	v["node_version"] = version
	v["node_build"] = buildID()
	name := time.Now().UTC().Format("20060102-150405") + "-" + randHex(3) + ".json"
	b, _ := json.MarshalIndent(v, "", " ")
	p := filepath.Join(s.cfg.Data, "results", name)
	if err := os.WriteFile(p, b, 0o640); err != nil {
		writeJSON(w, 500, map[string]string{"error": "speichern"})
		return
	}
	writeJSON(w, 200, map[string]string{"file": name})
}

func (s *server) apiResults(w http.ResponseWriter, r *http.Request) {
	dir := filepath.Join(s.cfg.Data, "results")
	ents, _ := os.ReadDir(dir)
	var names []string
	for _, e := range ents {
		if strings.HasSuffix(e.Name(), ".json") && !e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	out := []json.RawMessage{}
	for _, n := range names {
		b, err := os.ReadFile(filepath.Join(dir, n))
		if err == nil && json.Valid(b) {
			out = append(out, b)
		}
	}
	writeJSON(w, 200, out)
}
