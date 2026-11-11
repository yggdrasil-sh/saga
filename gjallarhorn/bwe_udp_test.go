package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/logging"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

// HORN_BWE: Schalterwerte.
func TestBWESchalter(t *testing.T) {
	for in, want := range map[string]string{"": bweGCC, "gcc": bweGCC, " GCC ": bweGCC, "mit": bweMit, "Mit": bweMit, "eigen": bweGCC, "1": bweGCC} {
		got, warn := parseBWE(in)
		if got != want {
			t.Fatalf("%q: %s", in, got)
		}
		if known := in == "" || strings.EqualFold(strings.TrimSpace(in), got); known != (warn == "") {
			t.Fatalf("%q: warnung %q", in, warn)
		}
	}
	// gcc (Standard): Die Fabrik liefert den pion-Schaetzer selbst, keine Huelle. mit: die Huelle.
	for _, mit := range []bool{false, true} {
		hook := &gccHook{mit: mit}
		se := webrtc.SettingEngine{}
		api, err := newSFUAPI(se, true, hook)
		if err != nil {
			t.Fatal(err)
		}
		var est cc.BandwidthEstimator
		hook.next = func(e cc.BandwidthEstimator) { est = e }
		pc, err := api.NewPeerConnection(webrtc.Configuration{})
		if err != nil {
			t.Fatal(err)
		}
		_ = pc.Close()
		_, isGCC := est.(*gcc.SendSideBWE)
		_, isWrap := est.(*bweWrap)
		if isGCC == mit || isWrap != mit {
			t.Fatalf("mit=%v: schaetzer %T", mit, est)
		}
	}
}

func TestBWEZeilen(t *testing.T) {
	got := bweLine("5884a4", bwe.Change{From: bwe.Warn, To: bwe.Congested, Why: bwe.WhyQueue,
		Snap: bwe.Snap{QueueUs: 123_400, Loss: 0.025, AckedBps: 1_012_000, CapBps: 1_007_000}}, 1_062_000, 1200)
	want := "bwe 5884a4 zustand=stau vorher=warnung grund=schlange schlange=123ms verlust=2.5% durchsatz=1012k kapazitaet=1007k gcc=1062k est=1200k"
	if got != want {
		t.Fatalf("\n%s\n%s", got, want)
	}
	got = bweProbe(bwe.Verdict{Result: bwe.VerdictFree, QueueUs: 3900, Loss: 0, Bps: 1_640_000, WorstSt: bwe.Free, Windows: 15}, bwe.Verdict{Result: bwe.VerdictUnclear})
	want = " bwe=staut-nicht bwe_schlange=3ms bwe_verlust=0.0% bwe_durchsatz=1640k bwe_zustand=frei bwe_fenster=15 bwe_sofort=unklar"
	if bweTail(0) != 500*time.Millisecond || bweTail(40*time.Millisecond) != 280*time.Millisecond || bweTail(3*time.Second) != time.Second {
		t.Fatal("nachlauf")
	}
	if got != want {
		t.Fatalf("\n%s\n%s", got, want)
	}
}

// strecke bildet auf der Empfangsseite eine Leitung nach (wie engpass, dazu Puffergroesse, Jitter und Pausen): Pakete
// warten in einer Schlange (hoechstens buf, danach Verlust) und werden erst zu ihrer Zeit gelesen.
type strecke struct {
	net.PacketConn
	rate  atomic.Int64 // bit/s, 0 = frei
	buf   atomic.Int64 // ns, Schlange hoechstens so lang
	jit   atomic.Int64 // ns, Jitter 0..jit je Paket
	pause atomic.Bool  // alle ~1,5 s eine Pause von 30-90 ms
	wait  time.Duration
	ch    chan engPkt
	lost  atomic.Int64
}

func newStrecke(c net.PacketConn) *strecke {
	e := &strecke{PacketConn: c, ch: make(chan engPkt, 8192), wait: 20 * time.Millisecond}
	e.buf.Store(int64(300 * time.Millisecond))
	go func() {
		rng := rand.New(rand.NewSource(1))
		var last, pauseTo time.Time
		nextPause := time.Now().Add(1500 * time.Millisecond)
		for {
			b := make([]byte, 1600)
			n, from, err := c.ReadFrom(b)
			if err != nil {
				close(e.ch)
				return
			}
			now := time.Now()
			at := now
			if r := e.rate.Load(); r > 0 {
				if last.Before(now) {
					last = now
				}
				if last.Sub(now) > time.Duration(e.buf.Load()) {
					e.lost.Add(1)
					continue
				}
				last = last.Add(time.Duration(float64(n*8) / float64(r) * 1e9))
				at = last
			}
			if e.pause.Load() {
				if now.After(nextPause) {
					pauseTo = now.Add(time.Duration(30+rng.Intn(61)) * time.Millisecond)
					nextPause = pauseTo.Add(time.Duration(1000+rng.Intn(1000)) * time.Millisecond)
				}
				if at.Before(pauseTo) {
					at = pauseTo
				}
			}
			if j := e.jit.Load(); j > 0 {
				at = at.Add(time.Duration(rng.Int63n(j)))
			}
			e.ch <- engPkt{b[:n], from, at.Add(e.wait)}
		}
	}()
	return e
}

func (e *strecke) ReadFrom(b []byte) (int, net.Addr, error) {
	p, ok := <-e.ch
	if !ok {
		return 0, nil, net.ErrClosed
	}
	if d := time.Until(p.at); d > 0 {
		time.Sleep(d)
	}
	return copy(b, p.b), p.from, nil
}

// bweBotAPI: Empfaenger wie probeBotAPI, hinter einer strecke.
func bweBotAPI(t *testing.T) (*webrtc.API, *strecke) {
	t.Helper()
	se := webrtc.SettingEngine{}
	lf := logging.NewDefaultLoggerFactory()
	lf.DefaultLogLevel = logging.LogLevelError
	se.LoggerFactory = lf
	se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	se.SetIncludeLoopbackCandidate(true)
	se.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	uc, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = uc.Close() })
	st := newStrecke(uc)
	se.SetICEUDPMux(ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: st, Logger: lf.NewLogger("udpmux")}))
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}, PayloadType: 111}, webrtc.RTPCodecTypeAudio); err != nil {
		t.Fatal(err)
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000}, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		t.Fatal(err)
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.AudioLevelURI}, webrtc.RTPCodecTypeAudio); err != nil {
		t.Fatal(err)
	}
	ir := &interceptor.Registry{}
	if err := webrtc.ConfigureNackWithOptions(m, ir, nackGenOpts()); err != nil {
		t.Fatal(err)
	}
	if err := webrtc.ConfigureRTCPReports(ir); err != nil {
		t.Fatal(err)
	}
	if err := webrtc.ConfigureTWCCSender(m, ir); err != nil {
		t.Fatal(err)
	}
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), st
}

// logFang schneidet das Protokoll mit (Zeilen mit Zeit seit Beginn).
type logFang struct {
	mu    sync.Mutex
	t0    time.Time
	lines []logZeile
	tee   io.Writer
}

type logZeile struct {
	T float64 `json:"t"`
	S string  `json:"s"`
}

func (f *logFang) Write(b []byte) (int, error) {
	f.mu.Lock()
	for _, ln := range bytes.Split(bytes.TrimRight(b, "\n"), []byte("\n")) {
		f.lines = append(f.lines, logZeile{time.Since(f.t0).Seconds(), string(ln)})
	}
	f.mu.Unlock()
	if f.tee != nil {
		return f.tee.Write(b)
	}
	return len(b), nil
}

func (f *logFang) grep(sub string) []logZeile {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []logZeile
	for _, l := range f.lines {
		if strings.Contains(l.S, sub) {
			out = append(out, l)
		}
	}
	return out
}

// bweAufbau: Knoten (Probing an, HORN_BWE wie mode), Sender mit l/m/h, Empfaenger hinter einer strecke.
type bweAufbau struct {
	s    *server
	l    *leg
	rcv  *sfuBot
	st   *strecke
	logs *logFang
	base string
	t0   time.Time
}

func newBWEAufbau(t *testing.T, mode string, prep func(*strecke)) *bweAufbau {
	t.Helper()
	a := &bweAufbau{logs: &logFang{t0: time.Now()}}
	old := log.Writer()
	log.SetOutput(a.logs)
	t.Cleanup(func() { log.SetOutput(old) })
	cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", UDP: true, UDPListen: "127.0.0.1:0", Data: t.TempDir(),
		ICEIPs: []string{"127.0.0.1"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 32, Mode: "node", Dev: true, Probing: true, BWE: mode,
		Certs: netx.CertOptions{Name: "localhost", Self: true}}
	s, err := newServer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.shutdown)
	a.s = s
	a.base = "https://" + s.mux.Addr().String()
	hc := httpClient()
	if err := postJSON(hc, a.base+"/api/dev/room", map[string]string{"room": "raum-bwe"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	sapi, err := sfuBotAPI("udp")
	if err != nil {
		t.Fatal(err)
	}
	snd := &sfuBot{peer: "sender", path: "udp", video: true, layers: []string{"l", "m", "h"}}
	snd.level.Store(20)
	rapi, st := bweBotAPI(t)
	a.st = st
	if prep != nil {
		prep(st)
	}
	a.rcv = &sfuBot{peer: "empf", path: "udp"}
	a.rcv.level.Store(127)
	for _, b := range []*sfuBot{snd, a.rcv} {
		api := sapi
		if b == a.rcv {
			api = rapi
			time.Sleep(4 * time.Second) // die Raten der Quellen sind eingeschwungen
			a.t0 = time.Now()
			a.logs.mu.Lock()
			a.logs.t0 = a.t0
			a.logs.mu.Unlock()
		}
		if err := b.join(hc, a.base, api, "raum-bwe", 0, "desktop"); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(b.close)
		b.start()
	}
	for _, r := range a.rcv.pcs[0].GetReceivers() {
		go func() {
			buf := make([]byte, 1500)
			for {
				if _, _, err := r.Read(buf); err != nil {
					return
				}
			}
		}()
	}
	a.l = s.getLeg(a.rcv.legIDs[0])
	if a.l == nil || a.l.sl == nil {
		t.Fatal("bein fehlt")
	}
	return a
}

func (a *bweAufbau) layer() string {
	for _, sl := range a.l.sl.Layout() {
		if sl.Class == "speaker" {
			return sl.Layer
		}
	}
	return ""
}

func (a *bweAufbau) snap() bwe.Snap { return a.l.bweWrap().m.Snapshot(bweNow()) }

func (a *bweAufbau) until(max time.Duration, ok func() bool) bool {
	for end := time.Now().Add(max); time.Now().Before(end); {
		if ok() {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return ok()
}

// TestUDPBWEMit: Ende zu Ende mit pion-Gegenstelle und HORN_BWE=mit. Der Mitleser ordnet die echten
// transport-cc-Rueckmeldungen zu, der Detektor meldet auf freier Leitung "frei", in der Enge "Stau" und danach wieder
// "frei"; Zeilen und Zaehler stimmen. Das Verhalten des Knotens selbst prueft TestUDPProbing (der Detektor entscheidet
// nichts).
func TestUDPBWEMit(t *testing.T) {
	a := newBWEAufbau(t, bweMit, nil)
	b := a.l.bweWrap()
	if b == nil {
		t.Fatal("kein Mitleser am Bein")
	}
	if !a.until(15*time.Second, func() bool { return a.layer() == "h" }) {
		t.Fatalf("kein h: %q", a.layer())
	}
	time.Sleep(3 * time.Second)
	c := b.m.Counters()
	s := a.snap()
	t.Logf("frei: %+v zaehler %+v", s, c)
	if s.State != bwe.Free || c.ToWarn+c.ToCong != 0 {
		t.Fatalf("freie Leitung: %+v, zaehler %+v", s, c)
	}
	if c.Feedbacks < 20 || c.Acked < 300 || c.Bad != 0 || c.Sent[bwe.KindVideo] == 0 || c.Sent[bwe.KindAudio] == 0 {
		t.Fatalf("zaehler: %+v", c)
	}
	// alles, was der Empfaenger meldet, kennt der Ring (Unbekannte hoechstens vereinzelt)
	if c.Unknown > c.Acked/100 || c.Lost > c.Acked/50 {
		t.Fatalf("zuordnung: %+v", c)
	}
	if s.AckedBps < 1_000_000 || s.AckedBps > 2_400_000 || s.QueueUs > 20_000 {
		t.Fatalf("freie Leitung auf h: %+v", s)
	}
	// Enge: 700 kbit/s tragen h nicht
	a.st.rate.Store(700e3)
	t1 := time.Now()
	if !a.until(4*time.Second, func() bool { return a.snap().State == bwe.Congested }) {
		t.Fatalf("enge: kein Stau, %+v", a.snap())
	}
	s = a.snap()
	t.Logf("enge: Stau nach %v: %+v", time.Since(t1).Round(10*time.Millisecond), s)
	if s.Why != bwe.WhyQueue || s.CapBps < 500_000 || s.CapBps > 900_000 {
		t.Fatalf("enge: %+v", s)
	}
	time.Sleep(time.Second)
	a.st.rate.Store(0)
	if !a.until(6*time.Second, func() bool { return a.snap().State == bwe.Free }) {
		t.Fatalf("wieder frei: %+v", a.snap())
	}
	// Zeilen: je Wechsel eine, mit Kurzkennung des Beins
	time.Sleep(200 * time.Millisecond)
	re := regexp.MustCompile(`bwe ` + shortID(a.l.id) + ` zustand=(frei|warnung|stau|unbekannt) vorher=\S+ grund=\S+ schlange=-?\d+ms verlust=[\d.]+% durchsatz=\d+k kapazitaet=\d+k gcc=\d+k est=\d+k$`)
	seen := map[string]int{}
	for _, ln := range a.logs.grep("bwe " + shortID(a.l.id)) {
		m := re.FindStringSubmatch(ln.S)
		if m == nil {
			t.Fatalf("zeile: %q", ln.S)
		}
		seen[m[1]]++
	}
	c = b.m.Counters()
	if seen["stau"] == 0 || seen["frei"] < 2 || uint64(seen["stau"]) != c.ToCong || uint64(seen["frei"]) != c.ToFree || uint64(seen["warnung"]) != c.ToWarn {
		t.Fatalf("zeilen %v, zaehler %+v", seen, c)
	}
	if len(a.logs.grep("HORN_BWE=mit")) != 1 {
		t.Fatal("startzeile fehlt")
	}
	// Zaehler unter /api/sfu/leg/{id}
	var out struct {
		BWE struct {
			Mode      string
			State     string
			Feedbacks uint64
			ToCong    uint64 `json:"to_cong"`
			Sent      map[string]uint64
		}
	}
	resp, err := httpClient().Get(a.base + "/api/sfu/leg/" + a.l.id)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if out.BWE.Mode != bweMit || out.BWE.State != "frei" || out.BWE.Feedbacks == 0 || out.BWE.ToCong != c.ToCong || out.BWE.Sent["video"] == 0 {
		t.Fatalf("api: %+v", out.BWE)
	}
}

// Schalter gcc (Standard): kein Mitleser am Bein, keine bwe-Zeilen, kein "bwe" in der Antwort.
func TestUDPBWEAus(t *testing.T) {
	a := newBWEAufbau(t, "", nil)
	if a.l.bweWrap() != nil {
		t.Fatal("Mitleser trotz gcc")
	}
	if !a.until(15*time.Second, func() bool { return a.layer() == "h" }) {
		t.Fatalf("kein h: %q", a.layer())
	}
	var out map[string]any
	resp, err := httpClient().Get(a.base + "/api/sfu/leg/" + a.l.id)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if _, ok := out["bwe"]; ok || out["probes"] == nil {
		t.Fatalf("api: %v", out)
	}
	if n := len(a.logs.grep("bwe ")) + len(a.logs.grep("HORN_BWE")); n != 0 {
		t.Fatalf("%d bwe-Zeilen", n)
	}
}

// TestUDPBWEMessung (nur mit HORN_BWE_MESSUNG=<fall>[,<fall>...]; kein Regressionstest): die nachgestellten Faelle,
// in denen pion-GCC irrefuehrt - Urteil des Detektors neben dem von GCC, im 100-ms-Raster. Schreibt je Lauf eine Datei
// <HORN_BWE_OUT>/<fall>-<n>.jsonl (Abtastwerte, Leitungs-Ereignisse, Logzeilen).
//
//	engfrei  30 s Leitung 1000 kbit/s, dann 70 s frei
//	s60      Jitter 0-6 ms, alle ~1,5 s eine Pause von 30-90 ms, 90 s
//	frei0    freie Leitung, 60 s
//	policer  20 s frei, 30 s Policer 1000 kbit/s (Puffer 20 ms), 30 s frei
//	mitte    20 s frei, 20 s Leitung 1000 kbit/s, 20 s frei, 20 s Leitung 1250 kbit/s, 20 s frei
func TestUDPBWEMessung(t *testing.T) {
	want := os.Getenv("HORN_BWE_MESSUNG")
	if want == "" {
		t.Skip("nur mit HORN_BWE_MESSUNG")
	}
	type schritt struct {
		at   float64 // s seit Beginn des Empfaenger-Beins
		what string
		do   func(*strecke)
	}
	eng := func(kbit int64, bufMs int64) func(*strecke) {
		return func(s *strecke) { s.buf.Store(bufMs * 1e6); s.rate.Store(kbit * 1000) }
	}
	frei := func(s *strecke) { s.rate.Store(0) }
	faelle := map[string]struct {
		prep func(*strecke)
		plan []schritt
		dur  float64
	}{
		"engfrei": {eng(1000, 300), []schritt{{30, "frei", frei}}, 100},
		"s60":     {func(s *strecke) { s.jit.Store(6e6); s.pause.Store(true) }, nil, 90},
		"frei0":   {nil, nil, 60},
		"policer": {nil, []schritt{{20, "policer-1000", eng(1000, 20)}, {50, "frei", frei}}, 80},
		"mitte":   {nil, []schritt{{20, "eng-1000", eng(1000, 300)}, {40, "frei", frei}, {60, "eng-1250", eng(1250, 300)}, {80, "frei", frei}}, 100},
	}
	out := os.Getenv("HORN_BWE_OUT")
	n, _ := strconv.Atoi(os.Getenv("HORN_BWE_N"))
	for _, name := range strings.Split(want, ",") {
		f, ok := faelle[name]
		if !ok {
			t.Fatalf("fall %q unbekannt", name)
		}
		t.Run(name, func(t *testing.T) {
			a := newBWEAufbau(t, bweMit, f.prep)
			b := a.l.bweWrap()
			type probe struct {
				T      float64 `json:"t"`
				Ev     string  `json:"ev,omitempty"`
				Layer  string  `json:"layer,omitempty"`
				Gcc    int     `json:"gcc,omitempty"`
				Est    int     `json:"est,omitempty"`
				Rate   int     `json:"rate,omitempty"` // gesendet, kbit/s ueber den Schritt
				State  string  `json:"st,omitempty"`
				Queue  int64   `json:"q"`
				Loss   float64 `json:"loss"`
				Acked  int64   `json:"acked,omitempty"`
				Over   bool    `json:"over,omitempty"`
				Trend  float64 `json:"trend,omitempty"`
				Thresh float64 `json:"thresh,omitempty"`
				GLoss  float64 `json:"gloss,omitempty"`
			}
			var rows []any
			plan := f.plan
			lastB := a.l.fl.Stats.BytesOut.Load() + a.l.fl.Stats.HdrExtra.Load()
			lastT := time.Now()
			for {
				time.Sleep(100 * time.Millisecond)
				now := time.Now()
				el := now.Sub(a.t0).Seconds()
				for len(plan) > 0 && el >= plan[0].at {
					plan[0].do(a.st)
					rows = append(rows, probe{T: el, Ev: plan[0].what})
					plan = plan[1:]
				}
				if el >= f.dur {
					break
				}
				s := a.snap()
				x := a.l.sl.ExtStat()
				by := a.l.fl.Stats.BytesOut.Load() + a.l.fl.Stats.HdrExtra.Load()
				rate := float64(by-lastB) * 8 / now.Sub(lastT).Seconds() / 1000
				lastB, lastT = by, now
				rows = append(rows, probe{T: el, Layer: a.layer(), Gcc: b.GetTargetBitrate() / 1000, Est: a.l.sl.EstimateKbit(), Rate: int(rate),
					State: s.State.String(), Queue: s.QueueUs / 1000, Loss: s.Loss, Acked: s.AckedBps / 1000, Over: x.Over, Trend: x.DelayMs, Thresh: x.ThreshMs, GLoss: x.Loss})
			}
			time.Sleep(300 * time.Millisecond)
			c := b.m.Counters()
			t.Logf("%s: zaehler %+v, proben %+v, strecke-verlust %d", name, c, a.l.sl.ProbeStats(), a.st.lost.Load())
			for _, ln := range a.logs.grep("probe " + shortID(a.l.id)) {
				t.Logf("%6.1f %s", ln.T, ln.S)
			}
			for _, ln := range a.logs.grep("bwe " + shortID(a.l.id)) {
				t.Logf("%6.1f %s", ln.T, ln.S)
			}
			if out == "" {
				return
			}
			var buf bytes.Buffer
			enc := json.NewEncoder(&buf)
			for _, r := range rows {
				_ = enc.Encode(r)
			}
			a.logs.mu.Lock()
			for _, ln := range a.logs.lines {
				if strings.Contains(ln.S, "probe ") || strings.Contains(ln.S, "bwe ") {
					_ = enc.Encode(map[string]any{"t": ln.T, "log": ln.S[strings.Index(ln.S, " ")+1:]})
				}
			}
			a.logs.mu.Unlock()
			_ = enc.Encode(map[string]any{"zaehler": c})
			if err := os.WriteFile(filepath.Join(out, fmt.Sprintf("%s-%d.jsonl", name, n)), buf.Bytes(), 0o644); err != nil {
				t.Fatal(err)
			}
		})
	}
}
