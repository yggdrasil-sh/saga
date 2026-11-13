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
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/interceptor/pkg/twcc"
	"github.com/pion/logging"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// HORN_BWE: Schalterwerte.
func TestBWESchalter(t *testing.T) {
	for in, want := range map[string]string{"": bweGCC, "gcc": bweGCC, " GCC ": bweGCC, "mit": bweMit, "Mit": bweMit, "eigen": bweEigen, " Eigen": bweEigen, "selbst": bweGCC, "1": bweGCC} {
		got, warn := parseBWE(in)
		if got != want {
			t.Fatalf("%q: %s", in, got)
		}
		if known := in == "" || strings.EqualFold(strings.TrimSpace(in), got); known != (warn == "") {
			t.Fatalf("%q: warnung %q", in, warn)
		}
	}
	// gcc (Standard): Die Fabrik liefert den pion-Schaetzer selbst, keine Huelle. mit und eigen: die Huelle.
	for _, mode := range []string{"", bweMit, bweEigen} {
		mit := mode != ""
		hook := &gccHook{bwe: mode}
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
			t.Fatalf("%q: schaetzer %T", mode, est)
		}
		if w, ok := est.(*bweWrap); ok && w.mode != mode {
			t.Fatalf("%q: huelle mit %q", mode, w.mode)
		}
	}
}

// Wegwechsel: Nur ein anderes Adresspaar setzt die Basis des Detektors zurueck - nicht dasselbe Paar noch einmal.
func TestBWEPaarwechsel(t *testing.T) {
	b := newBWEWrap(nil, bweMit)
	paar := func(lport, rport uint16) *webrtc.ICECandidatePair {
		return &webrtc.ICECandidatePair{
			Local:  &webrtc.ICECandidate{Protocol: webrtc.ICEProtocolUDP, Address: "127.0.0.1", Port: lport, Typ: webrtc.ICECandidateTypeHost},
			Remote: &webrtc.ICECandidate{Protocol: webrtc.ICEProtocolUDP, Address: "127.0.0.1", Port: rport, Typ: webrtc.ICECandidateTypePrflx},
		}
	}
	var got []bool
	for _, cp := range []*webrtc.ICECandidatePair{paar(7000, 40001), paar(7000, 40001), paar(7000, 40001), paar(7000, 40002), paar(7000, 40002), paar(7000, 40001), nil, {}} {
		got = append(got, b.pairChanged(cp))
	}
	if want := []bool{true, false, false, true, false, true, false, false}; !slices.Equal(got, want) {
		t.Fatalf("%v, erwartet %v", got, want)
	}
}

// fakeEst: Schaetzer-Attrappe - zaehlt, was durch seinen Schreiber geht (wie pion-GCC: AddStream liefert den eigenen
// Schreiber, der an w weiterreicht).
type fakeEst struct {
	cc.BandwidthEstimator
	seen []uint16
}

func (f *fakeEst) AddStream(_ *interceptor.StreamInfo, w interceptor.RTPWriter) interceptor.RTPWriter {
	return interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, a interceptor.Attributes) (int, error) {
		f.seen = append(f.seen, h.SequenceNumber)
		return w.Write(h, pl, a)
	})
}

// Doppel: Geht eine RTP-Folgenummer auf einem Strom ein zweites Mal hinaus, zaehlt die Huelle es als Doppel (sent.dup)
// - seit dem Abgleich je Quelle (fwd.TestDoppelDerQuelle) sollte das nicht mehr vorkommen, der Zaehler bleibt als
// Messwert. Es geht hinaus wie jedes Paket, auch durch pion-GCC (bei mit wie bei eigen; das fruehere Vorbeileiten bei
// eigen wirkte nicht, TestPruefDoppelGCCVerlust). Bleibt die Bestaetigung aus, ist das fuer den Detektor kein Verlust.
func TestBWEDoppel(t *testing.T) {
	for _, mode := range []string{bweMit, bweEigen} {
		est := &fakeEst{}
		b := newBWEWrap(est, mode)
		var out []uint16
		info := &interceptor.StreamInfo{SSRC: 7, MimeType: mimeVP8, RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
		w := b.AddStream(info, interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) {
			out = append(out, h.SequenceNumber)
			return len(pl), nil
		}))
		tw := uint16(0)
		send := func(seq uint16) {
			h := &rtp.Header{Version: 2, PayloadType: 96, SSRC: 7, SequenceNumber: seq}
			tw++
			_ = h.SetExtension(5, []byte{byte(tw >> 8), byte(tw)})
			if _, err := w.Write(h, make([]byte, 100), nil); err != nil {
				t.Fatal(err)
			}
		}
		// 1000..1009, dabei 1004 und 1002 noch einmal; dann weit weiter (Umlauf des Fensters) und 1004 erneut: kein Doppel
		for _, s := range []uint16{1000, 1001, 1002, 1003, 1004, 1004, 1005, 1002, 1006} {
			send(s)
		}
		for s := uint16(1007); s < 1007+1100; s++ {
			send(s)
		}
		send(1004)
		c := b.m.Counters()
		if c.Sent[bwe.KindDup] != 2 || c.Sent[bwe.KindVideo] != 7+1100+1 {
			t.Fatalf("%s: gezaehlt %+v", mode, c.Sent)
		}
		if len(out) != 9+1100+1 {
			t.Fatalf("%s: %d Pakete hinaus", mode, len(out))
		}
		if len(est.seen) != len(out) {
			t.Fatalf("%s: GCC sah %d Pakete, erwartet alle %d", mode, len(est.seen), len(out))
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
	// Widerlegung und Ruecknahme einer Schicht (sfu.LegEvent)
	tip := bwe.Tip{State: bwe.Free, QueueUs: 1400, Loss: 0.031}
	for _, c := range []struct {
		ev   sfu.LegEvent
		tip  bwe.Tip
		want string
	}{
		{sfu.LegEvent{Kind: sfu.LegRefute, Art: "spitze", GccKbit: 1130, RateKbit: 1330, EstKbit: 1480}, tip,
			"bwe 5884a4 widerlegung=beginn art=spitze gcc=1130k rate=1330k est=1480k zustand=frei schlange=1ms verlust=3.1%"},
		{sfu.LegEvent{Kind: sfu.LegRefuteEnd, Art: "halten", Why: "detektor", Dur: 1600 * time.Millisecond, GccKbit: 393, RateKbit: 600, EstKbit: 835}, bwe.Tip{State: bwe.Warn, QueueUs: -1},
			"bwe 5884a4 widerlegung=ende art=halten grund=detektor dauer=1.6s gcc=393k rate=600k est=835k zustand=warnung schlange=-1ms verlust=0.0%"},
		{sfu.LegEvent{Kind: sfu.LegDown, Why: "gcc-fall", Class: "speaker", From: "h", To: "m", GccKbit: 900, RateKbit: 1330, EstKbit: 1100}, tip,
			"schicht 5884a4 platz=speaker h>m grund=gcc-fall est=1100k gcc=900k rate=1330k zustand=frei schlange=1ms verlust=3.1%"},
		{sfu.LegEvent{Kind: sfu.LegDown, Why: "bedarf", Class: "tile", From: "l", GccKbit: 900, RateKbit: 700, EstKbit: 650}, tip,
			"schicht 5884a4 platz=tile l>p grund=bedarf est=650k gcc=900k rate=700k zustand=frei schlange=1ms verlust=3.1%"},
	} {
		if got := legEventLine("5884a4", c.ev, c.tip); got != c.want {
			t.Fatalf("\n%s\n%s", got, c.want)
		}
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

// Zeile "probe" bei HORN_BWE=mit: Das Urteil des Detektors steht zweimal darin - bwe_sofort= (was er beim Ende der
// Probe wusste) und bwe= (nach dem Nachlauf, wenn die Rueckmeldungen zu den letzten Paketen da sind). Probe von 0,4 s
// in eine Enge (1600 kbit/s auf 700): Beim Ende sind erst die Pakete der ersten ~0,17 s gemeldet (die Meldung kommt
// um die Schlange spaeter) - sofort "staut-nicht"; nach dem Nachlauf "staut". Die Zeile kommt erst nach dem Nachlauf,
// genau einmal. Ohne Mitleser (gcc) kommt sie sofort und ohne Zusatz.
func TestBWEProbeNachlauf(t *testing.T) {
	for bweNow() < int64(3200*time.Millisecond) { // die Sendezeiten unten liegen bis 3 s zurueck
		time.Sleep(50 * time.Millisecond)
	}
	logs := &logFang{t0: time.Now()}
	old := log.Writer()
	log.SetOutput(logs)
	defer log.SetOutput(old)
	sl, sl2 := &sfu.Leg{}, &sfu.Leg{}
	l := &leg{id: "a1b2c3d4e5f6", bwe: newBWEWrap(nil, bweMit)}
	l2 := &leg{id: "0f9e8d7c6b5a"}
	s := &server{bySFU: map[*sfu.Leg]*leg{sl: l, sl2: l2}}
	m := l.bwe.m

	// 3 s Senden, alle 5 ms 1000 Byte; die letzten 0,4 s durch eine Leitung mit 700 kbit/s (20 ms Laufzeit)
	type pkt struct {
		seq   uint16
		arrNs int64
	}
	end := bweNow()
	var pend []pkt
	var linkFree int64
	seq := uint16(65000) // mit Umlauf der Folgenummer
	for at := end - int64(3*time.Second); at < end; at += int64(5 * time.Millisecond) {
		m.Sent(at, seq, 1000, bwe.KindVideo)
		arr := at
		if at >= end-int64(400*time.Millisecond) {
			linkFree = max(linkFree, at) + 11_428_571 // 1000 Byte bei 700 kbit/s, ns
			arr = linkFree
		}
		pend = append(pend, pkt{seq, arr + int64(20*time.Millisecond)})
		seq++
	}
	rec := twcc.NewRecorder(1)
	feed := func(bis int64) { // der Empfaenger meldet alle 50 ms, was bis dahin angekommen ist
		for len(pend) > 0 && pend[0].arrNs <= bis {
			to := pend[0].arrNs + int64(50*time.Millisecond)
			for len(pend) > 0 && pend[0].arrNs <= min(to, bis) {
				rec.Record(2, pend[0].seq, pend[0].arrNs/1000+7_000_000)
				pend = pend[1:]
			}
			for _, p := range rec.BuildFeedbackPacket() {
				m.Feedback(bweNow(), p.(*rtcp.TransportLayerCC))
			}
		}
	}
	feed(end)
	if len(pend) < 30 {
		t.Fatalf("nur %d Pakete noch unterwegs", len(pend))
	}
	ev := sfu.ProbeEvent{Leg: sl, Class: "speaker", From: "m", To: "h", TargetKbit: 1879, FillKbit: 800, Result: "fail", Why: "gcc",
		Dur: 400 * time.Millisecond, Stage: 1, Hint: "gcc", EstKbit: 1100, GccKbit: 900}
	s.onProbe(ev)
	ev.Leg = sl2
	s.onProbe(ev)
	time.Sleep(100 * time.Millisecond)
	feed(1 << 62) // die uebrigen Rueckmeldungen kommen im Nachlauf (ohne Umlaufzeit 500 ms)
	if n := len(logs.grep("probe a1b2c3")); n != 0 {
		t.Fatalf("zeile vor dem Nachlauf (%d)", n)
	}
	gccLines := logs.grep("probe 0f9e8d")
	if len(gccLines) != 1 || strings.Contains(gccLines[0].S, "bwe") || !strings.HasSuffix(gccLines[0].S, "ergebnis=fail grund=gcc dauer=0.4s stufe=1 geklemmt=0 hinweis=gcc est=1100k gcc=900k") {
		t.Fatalf("gcc: %v", gccLines)
	}
	time.Sleep(700 * time.Millisecond)
	lines := logs.grep("probe a1b2c3")
	if len(lines) != 1 {
		t.Fatalf("%d Zeilen", len(lines))
	}
	t.Log(lines[0].S)
	if lines[0].T < 0.45 {
		t.Fatalf("zeile nach %.2f s - vor dem Nachlauf", lines[0].T)
	}
	re := regexp.MustCompile(`probe a1b2c3 platz=speaker schritt=m>h ziel=1879k fuell=800k ergebnis=fail grund=gcc dauer=0\.4s stufe=1 geklemmt=0 hinweis=gcc est=1100k gcc=900k bwe=staut bwe_schlange=(\d+)ms bwe_verlust=0\.0% bwe_durchsatz=(\d+)k bwe_zustand=(warnung|stau) bwe_fenster=[45] bwe_sofort=staut-nicht$`)
	g := re.FindStringSubmatch(lines[0].S)
	if g == nil {
		t.Fatalf("zeile: %q", lines[0].S)
	}
	if q, _ := strconv.Atoi(g[1]); q < 100 || q > 300 {
		t.Fatalf("schlange %d ms", q)
	}
	if d, _ := strconv.Atoi(g[2]); d < 600 || d > 800 {
		t.Fatalf("durchsatz %dk (Leitung 700k)", d)
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
	// Token-Bucket-Policer (statt der Schlange, wenn tbRate > 0): Ein Paket geht ohne Verzoegerung durch, solange der
	// Eimer (tbDepth lang die Rate) genug Guthaben hat, sonst wird es verworfen - wie ein Policer beim Anbieter, der
	// kurze Spitzen traegt und Dauerlast ueber der Rate abschneidet.
	tbRate  atomic.Int64 // bit/s
	tbDepth atomic.Int64 // ns
	wait    time.Duration
	ch      chan engPkt
	lost    atomic.Int64
}

func newStrecke(c net.PacketConn) *strecke {
	e := &strecke{PacketConn: c, ch: make(chan engPkt, 8192), wait: 20 * time.Millisecond}
	e.buf.Store(int64(300 * time.Millisecond))
	go func() {
		rng := rand.New(rand.NewSource(1))
		var last, pauseTo, tbAt time.Time
		var tokens float64 // Guthaben des Policers (bit)
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
			if r := e.tbRate.Load(); r > 0 {
				depth := float64(r) * time.Duration(e.tbDepth.Load()).Seconds()
				if tbAt.IsZero() {
					tokens = depth
				}
				tokens = min(depth, tokens+now.Sub(tbAt).Seconds()*float64(r))
				tbAt = now
				if tokens < float64(n*8) {
					e.lost.Add(1)
					continue
				}
				tokens -= float64(n * 8)
			} else {
				tbAt = time.Time{}
			}
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
//	engjit   30 s Leitung 1000 kbit/s, dann 240 s frei mit Jitter 0-6 ms und Pausen wie s60 (Proben nach dem Rueckfall)
//	mitte    20 s frei, 20 s Leitung 1000 kbit/s, 20 s frei, 20 s Leitung 1250 kbit/s, 20 s frei
//	knapp    wie mitte, aber Leitung 1450 und 1520 kbit/s - knapp unter der Rate von h (rund 1570 kbit/s)
//	plan     freier Ablauf aus HORN_BWE_PLAN, Dauer HORN_BWE_DAUER
//
// Mit HORN_BWE_NACHPROBE bricht die Leitung nach dem Plan ein, sobald eine Probe bestanden ist und der Platz auf h
// steht - Gegenprobe zum Halten nach bestandener Probe (wie schnell wird h verlassen?).
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
		return func(s *strecke) { s.tbRate.Store(0); s.buf.Store(bufMs * 1e6); s.rate.Store(kbit * 1000) }
	}
	frei := func(s *strecke) { s.rate.Store(0); s.tbRate.Store(0) }
	// tb: Token-Bucket-Policer mit kbit und Eimer depthMs lang die Rate (strecke.tbRate)
	tb := func(kbit, depthMs int64) func(*strecke) {
		return func(s *strecke) { s.rate.Store(0); s.tbDepth.Store(depthMs * 1e6); s.tbRate.Store(kbit * 1000) }
	}
	faelle := map[string]struct {
		prep func(*strecke)
		plan []schritt
		dur  float64
	}{
		"engfrei": {eng(1000, 300), []schritt{{30, "frei", frei}}, 100},
		"s60":     {func(s *strecke) { s.jit.Store(6e6); s.pause.Store(true) }, nil, 90},
		"frei0":   {nil, nil, 60},
		"policer": {nil, []schritt{{20, "policer-1000", eng(1000, 20)}, {50, "frei", frei}}, 80},
		"engjit":  {eng(1000, 300), []schritt{{30, "frei", func(s *strecke) { s.rate.Store(0); s.jit.Store(6e6); s.pause.Store(true) }}}, 270},
		"mitte":   {nil, []schritt{{20, "eng-1000", eng(1000, 300)}, {40, "frei", frei}, {60, "eng-1250", eng(1250, 300)}, {80, "frei", frei}}, 100},
		"knapp":   {nil, []schritt{{20, "eng-1450", eng(1450, 300)}, {40, "frei", frei}, {60, "eng-1520", eng(1520, 300)}, {80, "frei", frei}}, 100},
	}
	// Fall "plan": freier Ablauf aus HORN_BWE_PLAN = "<s>:<kbit>:<puffer-ms>[:tb],..." (kbit 0 = frei), Dauer HORN_BWE_DAUER (s) -
	// fuer Leitungen, die die festen Faelle nicht abdecken (Policer mit kurzem Puffer, Leitung knapp unter der Rate). Mit
	// ":tb" ist der Abschnitt ein Token-Bucket-Policer (Eimer <puffer-ms> lang die Rate, keine Schlange).
	if spec := os.Getenv("HORN_BWE_PLAN"); spec != "" {
		var plan []schritt
		for _, part := range strings.Split(spec, ",") {
			f := strings.Split(part, ":")
			if len(f) != 3 && (len(f) != 4 || f[3] != "tb") {
				t.Fatalf("HORN_BWE_PLAN: %q", part)
			}
			at, _ := strconv.ParseFloat(f[0], 64)
			kbit, _ := strconv.ParseInt(f[1], 10, 64)
			buf, _ := strconv.ParseInt(f[2], 10, 64)
			switch {
			case kbit == 0:
				plan = append(plan, schritt{at, "frei", frei})
			case len(f) == 4:
				plan = append(plan, schritt{at, fmt.Sprintf("tb-%d-%dms", kbit, buf), tb(kbit, buf)})
			default:
				plan = append(plan, schritt{at, fmt.Sprintf("eng-%d-%dms", kbit, buf), eng(kbit, buf)})
			}
		}
		dur, _ := strconv.ParseFloat(os.Getenv("HORN_BWE_DAUER"), 64)
		if dur <= 0 {
			dur = 100
		}
		faelle["plan"] = struct {
			prep func(*strecke)
			plan []schritt
			dur  float64
		}{nil, plan, dur}
	}
	out := os.Getenv("HORN_BWE_OUT")
	n, _ := strconv.Atoi(os.Getenv("HORN_BWE_N"))
	for _, name := range strings.Split(want, ",") {
		f, ok := faelle[name]
		if !ok {
			t.Fatalf("fall %q unbekannt", name)
		}
		t.Run(name, func(t *testing.T) {
			// HORN_BWE_ART=gcc | mit | eigen (Standard mit): Schalterstellung des Laufs
			art := bweMit
			if v := os.Getenv("HORN_BWE_ART"); v != "" {
				art, _ = parseBWE(v)
			}
			a := newBWEAufbau(t, art, f.prep)
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
				Clear  bool    `json:"clear,omitempty"` // Spitze des Detektors widerlegt Stau (bwe.Tip.Clear)
			}
			var rows []any
			plan := f.plan
			// HORN_BWE_NACHPROBE="<kbit>:<puffer-ms>[:<verzug-ms>]" (Gegenprobe zum Halten nach bestandener Probe): Ist der
			// Plan abgearbeitet, bricht die Leitung ein, sobald eine Probe bestanden ist und der Sprecherplatz auf h steht
			// (hoechstens 10 ms danach, plus Verzug) - Ereignis "einbruch-<kbit>-<puffer>ms". Der Lauf endet 12 s danach.
			var einbruch atomic.Int64 // ns seit a.t0, 0 = noch nicht
			einbruchWas := ""
			if spec := os.Getenv("HORN_BWE_NACHPROBE"); spec != "" {
				ff := strings.Split(spec, ":")
				kbit, _ := strconv.ParseInt(ff[0], 10, 64)
				bufMs := int64(300)
				if len(ff) > 1 {
					bufMs, _ = strconv.ParseInt(ff[1], 10, 64)
				}
				var verzug int64
				if len(ff) > 2 {
					verzug, _ = strconv.ParseInt(ff[2], 10, 64)
				}
				einbruchWas = fmt.Sprintf("einbruch-%d-%dms", kbit, bufMs)
				planEnd := 0.0
				if len(plan) > 0 {
					planEnd = plan[len(plan)-1].at
				}
				stop := make(chan struct{})
				t.Cleanup(func() { close(stop) })
				go func() {
					var passed0 uint64
					have := false
					for {
						select {
						case <-stop:
							return
						case <-time.After(10 * time.Millisecond):
						}
						if time.Since(a.t0).Seconds() < planEnd+0.5 {
							continue
						}
						ps := a.l.sl.ProbeStats()
						if !have {
							passed0, have = ps.Passed, true
							continue
						}
						if ps.Passed > passed0 && a.layer() == "h" {
							time.Sleep(time.Duration(verzug) * time.Millisecond)
							eng(kbit, bufMs)(a.st)
							einbruch.Store(int64(time.Since(a.t0)))
							return
						}
					}
				}()
			}
			einbruchRow := false
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
				if at := einbruch.Load(); at != 0 {
					if !einbruchRow {
						einbruchRow = true
						rows = append(rows, probe{T: float64(at) / 1e9, Ev: einbruchWas})
					}
					if el >= float64(at)/1e9+12 {
						break
					}
				}
				if el >= f.dur {
					break
				}
				var s bwe.Snap
				clear := false
				if b != nil {
					s = a.snap()
					clear = b.m.Tip(bweNow()).Clear
				}
				x := a.l.sl.ExtStat()
				by := a.l.fl.Stats.BytesOut.Load() + a.l.fl.Stats.HdrExtra.Load()
				rate := float64(by-lastB) * 8 / now.Sub(lastT).Seconds() / 1000
				lastB, lastT = by, now
				rows = append(rows, probe{T: el, Layer: a.layer(), Gcc: a.l.sl.Ext() / 1000, Est: a.l.sl.EstimateKbit(), Rate: int(rate),
					State: s.State.String(), Queue: s.QueueUs / 1000, Loss: s.Loss, Acked: s.AckedBps / 1000, Over: x.Over, Trend: x.DelayMs, Thresh: x.ThreshMs, GLoss: x.Loss, Clear: clear})
			}
			time.Sleep(300 * time.Millisecond)
			var c bwe.Counters
			if b != nil {
				c = b.m.Counters()
			}
			t.Logf("%s (%s): zaehler %+v, proben %+v, strecke-verlust %d", name, art, c, a.l.sl.ProbeStats(), a.st.lost.Load())
			for _, ln := range a.logs.grep("probe " + shortID(a.l.id)) {
				t.Logf("%6.1f %s", ln.T, ln.S)
			}
			for _, ln := range a.logs.grep("bwe " + shortID(a.l.id)) {
				t.Logf("%6.1f %s", ln.T, ln.S)
			}
			for _, ln := range a.logs.grep("schicht " + shortID(a.l.id)) {
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
				if strings.Contains(ln.S, "probe ") || strings.Contains(ln.S, "bwe ") || strings.Contains(ln.S, "schicht ") {
					_ = enc.Encode(map[string]any{"t": ln.T, "log": ln.S[strings.Index(ln.S, " ")+1:]})
				}
			}
			a.logs.mu.Unlock()
			_ = enc.Encode(map[string]any{"zaehler": c, "art": art, "proben": a.l.sl.ProbeStats()})
			if err := os.WriteFile(filepath.Join(out, fmt.Sprintf("%s-%d.jsonl", name, n)), buf.Bytes(), 0o644); err != nil {
				t.Fatal(err)
			}
		})
	}
}

// TestUDPBWELast (nur mit HORN_BWE_LAST=<sekunden>[,<empfaenger>]; kein Regressionstest): Rechenzeit des Prozesses
// (Knoten und Bots zusammen, user + sys) ueber einen Lauf auf freier Leitung mit dem Schalter aus HORN_BWE_LAST_ART
// (gcc | mit). Je Aufruf ein Lauf - zum Vergleich abwechselnd mit beiden Stellungen rufen.
func TestUDPBWELast(t *testing.T) {
	want := os.Getenv("HORN_BWE_LAST")
	if want == "" {
		t.Skip("nur mit HORN_BWE_LAST")
	}
	parts := strings.Split(want, ",")
	sec, _ := strconv.Atoi(parts[0])
	nrcv := 1
	if len(parts) > 1 {
		nrcv, _ = strconv.Atoi(parts[1])
	}
	mode, _ := parseBWE(os.Getenv("HORN_BWE_LAST_ART"))
	a := newBWEAufbau(t, mode, nil)
	api, err := sfuBotAPI("udp")
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < nrcv; i++ {
		b := &sfuBot{peer: fmt.Sprintf("empf%d", i), path: "udp"}
		b.level.Store(127)
		if err := b.join(httpClient(), a.base, api, "raum-bwe", 0, "desktop"); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(b.close)
		b.start()
	}
	if !a.until(15*time.Second, func() bool { return a.layer() == "h" }) {
		t.Fatalf("kein h: %q", a.layer())
	}
	time.Sleep(2 * time.Second)
	cpu := func() time.Duration {
		var ru syscall.Rusage
		_ = syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
		return time.Duration(ru.Utime.Nano() + ru.Stime.Nano())
	}
	c0, t0, b0 := cpu(), time.Now(), a.l.fl.Stats.BytesOut.Load()
	time.Sleep(time.Duration(sec) * time.Second)
	c1, el := cpu(), time.Since(t0)
	kbit := float64(a.l.fl.Stats.BytesOut.Load()-b0) * 8 / el.Seconds() / 1000
	var fb uint64
	if b := a.l.bweWrap(); b != nil {
		fb = b.m.Counters().Feedbacks
	}
	t.Logf("LAST art=%s empfaenger=%d dauer=%.1fs cpu=%.3fs (%.2f %% eines Kerns) schicht=%s rate=%.0fk rueckmeldungen=%d",
		mode, nrcv, el.Seconds(), (c1 - c0).Seconds(), (c1-c0).Seconds()/el.Seconds()*100, a.layer(), kbit, fb)
}
