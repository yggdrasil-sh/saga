package main

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/logging"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// benchResult wird als bench-*.json unter /data/results abgelegt.
type benchResult struct {
	Kind       string  `json:"kind"` // "bench"
	Time       string  `json:"time"`
	Version    string  `json:"version"`
	N          int     `json:"n"`
	Video      int     `json:"video"`
	LastN      int     `json:"lastn"`
	Path       string  `json:"path"`
	Secs       float64 `json:"secs"`
	Cores      int     `json:"cores"`
	Legs       int     `json:"legs"`
	Connected  int     `json:"connected"`
	ConnectS   float64 `json:"connect_s"`
	CPUPct     float64 `json:"cpu_pct"` // Anteil eines Kerns
	CPUusPkt   float64 `json:"cpu_us_per_pkt"`
	CPUusPktIO float64 `json:"cpu_us_per_pkt_io"` // je (ein + aus)
	PPSIn      float64 `json:"pps_in"`
	PPSOut     float64 `json:"pps_out"`
	MbitIn     float64 `json:"mbit_in"`
	MbitOut    float64 `json:"mbit_out"`
	RSSBaseMB  float64 `json:"rss_base_mb"`
	RSSMB      float64 `json:"rss_mb"`
	RAMLegMB   float64 `json:"ram_per_leg_mb"`
	RAMPartMB  float64 `json:"ram_per_participant_mb"`
	AllocsPkt  float64 `json:"allocs_per_pkt"`
	Dropped    uint64  `json:"frames_dropped"`
	Stalls     uint64  `json:"stalls"`
	Overflow   uint64  `json:"overflow"`
	MaxQMs     float64 `json:"max_queue_ms"`
	FwdP95us   float64 `json:"fwd_p95_us"`
	BotPPS     float64 `json:"bot_rx_pps"`
	ExpPPS     float64 `json:"expected_rx_pps"`
	BotLossPct float64 `json:"bot_loss_pct"`
	BBRConns   uint64  `json:"bbr_conns"`
	TCPConns   uint64  `json:"tcp_conns"`
	// nur --sfu
	Mode       string  `json:"mode,omitempty"` // "" (Spiegel) oder "sfu"
	TIDSkipped uint64  `json:"tid_skipped,omitempty"`
	PLIp95ms   float64 `json:"pli_p95_ms,omitempty"`
	Freezes    int     `json:"bot_freezes,omitempty"`
	BotFrames  int     `json:"bot_frames,omitempty"`
	Switches   uint64  `json:"switches,omitempty"`
	EstKbitLeg int     `json:"est_kbit_per_leg,omitempty"`
}

func runBench(args []string) error {
	fs := flag.NewFlagSet("bench", flag.ContinueOnError)
	n := fs.Int("n", 25, "Teilnehmende (Bots)")
	video := fs.Int("video", 12, "davon mit Kamera")
	secs := fs.Int("secs", 60, "Messdauer in Sekunden")
	path := fs.String("path", "tcp", "Weg: tcp (ICE-TCP, je Bot Ton- und Bild-Bein) oder udp")
	lastN := fs.Int("lastn", 9, "hoechstens so viele bewegte Kacheln je Bot (H6)")
	data := fs.String("data", env("HORN_DATA", "/data"), "Datenordner (Ergebnis unter results/)")
	useSFU := fs.Bool("sfu", false, "echter Weiterleiter (Raum, Top-3, Last-N, Schichtwahl) statt Spiegel")
	if err := fs.Parse(normFlags(args)); err != nil {
		return err
	}
	if *n < 2 || *n > 100 || *video < 0 || *video > *n || *secs < 5 {
		return errors.New("ungueltige werte (-n 2..100, --video 0..n, --secs >= 5)")
	}
	if *path != "tcp" && *path != "udp" {
		return errors.New("--path tcp oder udp")
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	tmp, err := os.MkdirTemp("", "horn-bench-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmp)
	legsPerBot := 2
	if *path == "udp" {
		legsPerBot = 1
	}
	mode := "HORN_MODE=probe"
	if *useSFU {
		mode = "HORN_MODE=node"
	}
	cmd := exec.Command(exe, "bench-node")
	cmd.Env = append(os.Environ(), mode, "HORN_DEV=1", "HORN_LISTEN=127.0.0.1:0", "HORN_ICE_IPS=127.0.0.1", "HORN_DATA="+tmp,
		"HORN_TLS_SELF=1", "HORN_PROXY_PROTOCOL=0", "HORN_TLS_CERT=", "HORN_PUBLIC_IP=",
		fmt.Sprintf("HORN_MAX_LEGS=%d", *n*legsPerBot+8), "HORN_UDP="+map[bool]string{true: "1", false: "0"}[*path == "udp"])
	out, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil {
		return err
	}
	defer func() {
		_ = cmd.Process.Signal(os.Interrupt)
		done := make(chan struct{})
		go func() { _ = cmd.Wait(); close(done) }()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			_ = cmd.Process.Kill()
			<-done
		}
	}()
	port := 0
	sc := bufio.NewScanner(out)
	for sc.Scan() {
		if _, err := fmt.Sscanf(sc.Text(), "LISTEN %d", &port); err == nil {
			break
		}
	}
	go func() { _, _ = io.Copy(io.Discard, out) }()
	if port == 0 {
		return errors.New("bench-knoten startet nicht")
	}
	base := fmt.Sprintf("https://127.0.0.1:%d", port)
	hc := &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{
		Proxy:           nil,
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, // nur Loopback zum eigenen Kindprozess
	}}
	st0, err := fetchStats(hc, base)
	if err != nil {
		return err
	}
	if *useSFU {
		return benchSFU(hc, base, st0, *n, *video, *secs, *lastN, *path, *data)
	}
	api, err := botAPI(*path)
	if err != nil {
		return err
	}
	fmt.Fprintf(os.Stderr, "bench: %d Bots (%d mit Video, Last-N %d), Weg %s, %d s ...\n", *n, *video, *lastN, *path, *secs)
	bots := make([]*bot, *n)
	ids := make([]string, *n)
	for i := range ids {
		ids[i] = fmt.Sprintf("bot%02d-%s", i, randHex(4))
	}
	t0 := time.Now()
	var wg sync.WaitGroup
	sem := make(chan struct{}, 4)
	var cerr atomic.Value
	for i := 0; i < *n; i++ {
		b := &bot{i: i, id: ids[i], video: i < *video}
		bots[i] = b
		wg.Add(1)
		go func() {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			if err := b.join(hc, base, api, *path, ids, *video, *lastN); err != nil {
				cerr.Store(err)
			}
		}()
	}
	wg.Wait()
	defer func() {
		for _, b := range bots {
			b.close()
		}
	}()
	if e, ok := cerr.Load().(error); ok && e != nil {
		return fmt.Errorf("bot: %w", e)
	}
	connected := 0
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		connected = 0
		for _, b := range bots {
			connected += b.connected()
		}
		if connected == *n*legsPerBot {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	connS := time.Since(t0).Seconds()
	if connected < *n*legsPerBot {
		return fmt.Errorf("nur %d von %d Beinen verbunden", connected, *n*legsPerBot)
	}
	for _, b := range bots {
		b.start()
	}
	time.Sleep(5 * time.Second) // Anlauf: Schluesselbilder, Abos
	s1, err := fetchStats(hc, base)
	if err != nil {
		return err
	}
	rx1 := botTotals(bots)
	time.Sleep(time.Duration(*secs) * time.Second)
	s2, err := fetchStats(hc, base)
	if err != nil {
		return err
	}
	rx2 := botTotals(bots)
	dt := s2.Uptime - s1.Uptime
	pOut := float64(s2.PktsOut - s1.PktsOut)
	pIn := float64(s2.PktsIn - s1.PktsIn)
	cpu := float64(s2.CPUns - s1.CPUns)
	legs := *n * legsPerBot
	r := benchResult{
		Kind: "bench", Time: time.Now().UTC().Format(time.RFC3339), Version: version,
		N: *n, Video: *video, LastN: *lastN, Path: *path, Secs: round(dt, 1), Cores: numCPU(),
		Legs: legs, Connected: connected, ConnectS: round(connS, 2),
		CPUPct: round(100*cpu/(dt*1e9), 2), CPUusPkt: round(cpu/1000/pOut, 2), CPUusPktIO: round(cpu/1000/(pOut+pIn), 2),
		PPSIn: round(pIn/dt, 0), PPSOut: round(pOut/dt, 0),
		MbitIn: round(float64(s2.BytesIn-s1.BytesIn)*8/dt/1e6, 2), MbitOut: round(float64(s2.BytesOut-s1.BytesOut)*8/dt/1e6, 2),
		RSSBaseMB: round(float64(st0.RSS)/1e6, 1), RSSMB: round(float64(s2.RSS)/1e6, 1),
		RAMLegMB: round(float64(s2.RSS-st0.RSS)/1e6/float64(legs), 2), RAMPartMB: round(float64(s2.RSS-st0.RSS)/1e6/float64(*n), 2),
		AllocsPkt: round(float64(s2.Mallocs-s1.Mallocs)/(pOut+pIn), 2),
		Dropped:   s2.FramesDropped - s1.FramesDropped, Stalls: s2.Stalls - s1.Stalls, Overflow: s2.Overflow - s1.Overflow,
		MaxQMs: round(s2.MaxQMs, 2), FwdP95us: s2.FwdP95us,
		BotPPS: round(float64(rx2.pkts-rx1.pkts)/dt, 0), ExpPPS: round(expectedPPS(*n, *video, *lastN), 0),
		BBRConns: s2.BBRConns, TCPConns: s2.TCPConns,
	}
	if got := rx2.pkts - rx1.pkts; got > 0 {
		r.BotLossPct = round(100*float64(rx2.lost-rx1.lost)/float64(got+rx2.lost-rx1.lost), 3)
	}
	printBench(os.Stdout, r)
	if dir := filepath.Join(*data, "results"); dirWritable(dir) {
		name := filepath.Join(dir, "bench-"+time.Now().UTC().Format("20060102-150405")+".json")
		b, _ := json.MarshalIndent(r, "", " ")
		if err := os.WriteFile(name, b, 0o640); err == nil {
			fmt.Fprintf(os.Stderr, "gespeichert: %s\n", name)
		}
	}
	return nil
}

// normFlags erlaubt --video wie -video.
func normFlags(args []string) []string {
	out := make([]string, len(args))
	for i, a := range args {
		if strings.HasPrefix(a, "--") {
			a = a[1:]
		}
		out[i] = a
	}
	return out
}

func dirWritable(dir string) bool {
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return false
	}
	f, err := os.CreateTemp(dir, ".w")
	if err != nil {
		return false
	}
	f.Close()
	os.Remove(f.Name())
	return true
}

func round(x float64, d int) float64 {
	p := 1.0
	for i := 0; i < d; i++ {
		p *= 10
	}
	if x < 0 {
		return -float64(int64(-x*p+0.5)) / p
	}
	return float64(int64(x*p+0.5)) / p
}

// expectedPPS: was alle Bots zusammen empfangen sollten (gleiche Abos wie join).
func expectedPPS(n, video, lastN int) float64 {
	const audio, speaker, tile = 25.0, 150.0, 30.0 // Pakete/s je Strom (siehe botStream)
	tot := 0.0
	for i := 0; i < n; i++ {
		tot += 3 * audio
		sp := -1
		if video > 0 {
			sp = 0
			if i == 0 {
				sp = 1
			}
			if sp < video {
				tot += speaker
			}
		}
		k := 0
		for j := 0; j < video && k < lastN; j++ {
			if j != i && j != sp {
				k++
			}
		}
		tot += float64(k) * tile
	}
	return tot
}

func numCPU() int { return runtime.NumCPU() }

func printBench(w io.Writer, r benchResult) {
	what := "Spiegel"
	if r.Mode == "sfu" {
		what = "Weiterleiter (SFU)"
	}
	fmt.Fprintf(w, "\nGjallarhorn bench %s, %s: %d Teilnehmende, %d mit Video, Weg %s, %.0f s, %d Kerne\n", r.Version, what, r.N, r.Video, r.Path, r.Secs, r.Cores)
	fmt.Fprintf(w, "  Beine verbunden     %d/%d in %.1f s\n", r.Connected, r.Legs, r.ConnectS)
	fmt.Fprintf(w, "  CPU                 %.1f %% eines Kerns, %.2f us je ausgehendem Paket (%.2f je Paket ein+aus)\n", r.CPUPct, r.CPUusPkt, r.CPUusPktIO)
	fmt.Fprintf(w, "  Pakete              rein %.0f/s (%.1f Mbit/s), raus %.0f/s (%.1f Mbit/s)\n", r.PPSIn, r.MbitIn, r.PPSOut, r.MbitOut)
	fmt.Fprintf(w, "  RAM                 %.1f MB (Start %.1f MB), %.2f MB je Bein, %.2f MB je Teilnehmendem\n", r.RSSMB, r.RSSBaseMB, r.RAMLegMB, r.RAMPartMB)
	fmt.Fprintf(w, "  Allokationen        %.2f je Paket (pion, SRTP, Interceptoren; Weiterleiter selbst 0)\n", r.AllocsPkt)
	fmt.Fprintf(w, "  Warteschlange       max %.1f ms, Weiterleitung p95 <= %.0f us, verworfene Rahmen %d, Stalls %d, Ueberlauf %d\n", r.MaxQMs, r.FwdP95us, r.Dropped, r.Stalls, r.Overflow)
	fmt.Fprintf(w, "  Bots empfangen      %.0f Pakete/s (erwartet ~%.0f), Verlust %.3f %%\n", r.BotPPS, r.ExpPPS, r.BotLossPct)
	fmt.Fprintf(w, "  TCP                 %d Verbindungen, davon %d mit BBR\n", r.TCPConns, r.BBRConns)
	if r.Mode == "sfu" {
		fmt.Fprintf(w, "  SFU                 %d Rahmen per TID ausgelassen, %d Wechsel, PLI-Antwort p95 <= %.0f ms, Einfrieren bei Bots %d (%d Rahmen), Schaetzung %d kbit/s je Bein\n",
			r.TIDSkipped, r.Switches, r.PLIp95ms, r.Freezes, r.BotFrames, r.EstKbitLeg)
	}
}

func fetchStats(hc *http.Client, base string) (nodeStats, error) {
	var ns nodeStats
	resp, err := hc.Get(base + "/api/stats")
	if err != nil {
		return ns, err
	}
	defer resp.Body.Close()
	return ns, json.NewDecoder(resp.Body).Decode(&ns)
}

// ---------- Bots ----------

func botAPI(path string) (*webrtc.API, error) {
	se := webrtc.SettingEngine{}
	lf := logging.NewDefaultLoggerFactory()
	lf.DefaultLogLevel = logging.LogLevelError
	se.LoggerFactory = lf
	se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	se.SetIncludeLoopbackCandidate(true)
	se.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	if path == "tcp" {
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeTCP4})
	} else {
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	}
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}, PayloadType: 111}, webrtc.RTPCodecTypeAudio); err != nil {
		return nil, err
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000,
		RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack", Parameter: "pli"}}}, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	ir := &interceptor.Registry{}
	if err := webrtc.ConfigureRTCPReports(ir); err != nil {
		return nil, err
	}
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), nil
}

type rxTotals struct{ pkts, lost uint64 }

func botTotals(bots []*bot) rxTotals {
	var t rxTotals
	for _, b := range bots {
		t.pkts += b.rxPkts.Load()
		t.lost += b.rxLost.Load()
	}
	return t
}

type bot struct {
	i     int
	id    string
	video bool

	pcs     []*webrtc.PeerConnection
	streams []*botStream
	rxPkts  atomic.Uint64
	rxLost  atomic.Uint64
	stop    chan struct{}
	once    sync.Once
}

// botStream erzeugt synthetische RTP-Pakete: Ton (Opus-Groesse, 40 ms) oder
// VP8-Rahmen mit Schluesselbildern und Dummy-E2E-Trailer.
type botStream struct {
	tr      *webrtc.TrackLocalStaticRTP
	kind    string // a, l, h
	key     atomic.Bool
	ssrcSeq uint16
}

func (b *bot) connected() int {
	c := 0
	for _, pc := range b.pcs {
		if pc.ConnectionState() == webrtc.PeerConnectionStateConnected {
			c++
		}
	}
	return c
}

func (b *bot) join(hc *http.Client, base string, api *webrtc.API, path string, ids []string, video, lastN int) error {
	b.stop = make(chan struct{})
	// Abos wie in einem echten Anruf: Top-3-Ton, Sprecher (hohe Schicht), Kacheln (niedrige)
	var aSubs, vSubs []sub
	for j, k := 0, 0; j < len(ids) && k < 3; j++ {
		if j != b.i {
			aSubs = append(aSubs, sub{Owner: ids[j], Kind: "audio", Class: "audio", Prefs: []string{"a"}})
			k++
		}
	}
	speaker := -1
	if video > 0 {
		speaker = 0
		if b.i == 0 {
			speaker = 1
		}
		if speaker < video {
			vSubs = append(vSubs, sub{Owner: ids[speaker], Kind: "video", Class: "speaker", Prefs: []string{"h", "l"}})
		}
	}
	for j, k := 0, 0; j < video && k < lastN; j++ {
		if j != b.i && j != speaker {
			vSubs = append(vSubs, sub{Owner: ids[j], Kind: "video", Class: "tile", Prefs: []string{"l", "h"}})
			k++
		}
	}
	type legSpec struct {
		role         string
		sendA, sendV bool
		subs         []sub
	}
	var specs []legSpec
	if path == "udp" {
		specs = []legSpec{{"both", true, b.video, append(aSubs, vSubs...)}}
	} else {
		specs = []legSpec{{"audio", true, false, aSubs}, {"video", false, b.video, vSubs}}
	}
	for _, sp := range specs {
		pc, err := api.NewPeerConnection(webrtc.Configuration{})
		if err != nil {
			return err
		}
		b.pcs = append(b.pcs, pc)
		add := func(kind, id string) error {
			c := webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}
			if kind != "a" {
				c = webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000}
			}
			tr, err := webrtc.NewTrackLocalStaticRTP(c, id, b.id)
			if err != nil {
				return err
			}
			tx, err := pc.AddTransceiverFromTrack(tr, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly})
			if err != nil {
				return err
			}
			s := &botStream{tr: tr, kind: kind}
			s.key.Store(true)
			b.streams = append(b.streams, s)
			go func() {
				buf := make([]byte, 1500)
				for {
					n, _, err := tx.Sender().Read(buf)
					if err != nil {
						return
					}
					if hasKeyRequest(buf[:n]) {
						s.key.Store(true)
					}
				}
			}()
			return nil
		}
		if sp.sendA {
			if err := add("a", "a"); err != nil {
				return err
			}
		}
		if sp.sendV {
			if err := add("l", "l"); err != nil {
				return err
			}
			if err := add("h", "h"); err != nil {
				return err
			}
		}
		na, nv := 0, 0
		for _, s := range sp.subs {
			if s.Kind == "audio" {
				na++
			} else {
				nv++
			}
		}
		for i := 0; i < na; i++ {
			if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
				return err
			}
		}
		for i := 0; i < nv; i++ {
			if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
				return err
			}
		}
		pc.OnTrack(func(tr *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
			buf := make([]byte, 1500)
			var last uint16
			first := true
			var h rtp.Header
			for {
				n, _, err := tr.Read(buf)
				if err != nil {
					return
				}
				if _, err := h.Unmarshal(buf[:n]); err != nil {
					continue
				}
				b.rxPkts.Add(1)
				if !first {
					if d := h.SequenceNumber - last; d > 1 && d < 1000 {
						b.rxLost.Add(uint64(d - 1))
					}
				}
				first, last = false, h.SequenceNumber
			}
		})
		offer, err := pc.CreateOffer(nil)
		if err != nil {
			return err
		}
		gather := webrtc.GatheringCompletePromise(pc)
		if err := pc.SetLocalDescription(offer); err != nil {
			return err
		}
		<-gather
		body, _ := json.Marshal(legReq{SID: b.id, Path: path, Role: sp.role, Audio: na, Video: nv, Offer: pc.LocalDescription().SDP, Subs: sp.subs})
		resp, err := hc.Post(base+"/api/leg", "application/json", bytes.NewReader(body))
		if err != nil {
			return err
		}
		var ans struct{ Answer, Error string }
		_ = json.NewDecoder(resp.Body).Decode(&ans)
		resp.Body.Close()
		if ans.Answer == "" {
			return fmt.Errorf("leg: %s", ans.Error)
		}
		if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
			return err
		}
	}
	return nil
}

func (b *bot) start() {
	for _, s := range b.streams {
		go s.run(b.stop)
	}
}

func (b *bot) close() {
	b.once.Do(func() {
		if b.stop != nil {
			close(b.stop)
		}
		for _, pc := range b.pcs {
			_ = pc.Close()
		}
	})
}

// Profile: Ton 40 ms (25/s, ~120 B + Trailer), Kachel 180p 15 fps 200 kbit/s,
// Sprecher 540p 30 fps 1,2 Mbit/s, Schluesselbild alle 3 s (4-fache Groesse).
func (s *botStream) run(stop <-chan struct{}) {
	var fps, frameBytes int
	var tsStep uint32
	switch s.kind {
	case "a":
		fps, frameBytes, tsStep = 25, 120, 1920
	case "l":
		fps, frameBytes, tsStep = 15, 200000/8/15, 6000
	default:
		fps, frameBytes, tsStep = 30, 1200000/8/30, 3000
	}
	const trailer = 13
	const maxPayload = 1150
	tick := time.NewTicker(time.Second / time.Duration(fps))
	defer tick.Stop()
	var seq uint16
	var ts uint32
	var buf [8192]byte
	_, _ = rand.Read(buf[:])
	frames := 0
	pkt := &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 96}}
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
		}
		frames++
		ts += tsStep
		if s.kind == "a" {
			pkt.Header.PayloadType = 111
			pkt.Header.Marker = false
			pkt.Header.SequenceNumber = seq
			pkt.Header.Timestamp = ts
			pkt.Payload = buf[:frameBytes+trailer]
			pkt.Payload[0] = 0x78 // Opus-TOC
			seq++
			if err := s.tr.WriteRTP(pkt); err != nil && errors.Is(err, io.ErrClosedPipe) {
				return
			}
			continue
		}
		key := s.key.Swap(false) || frames%(3*fps) == 1
		size := frameBytes
		if key {
			size *= 4
		}
		size += trailer
		if size > len(buf)-2 {
			size = len(buf) - 2
		}
		for off := 0; off < size; {
			chunk := size - off
			if chunk > maxPayload {
				chunk = maxPayload
			}
			p := make([]byte, 1+chunk)
			if off == 0 {
				p[0] = 0x10 // S=1, PartID 0
			}
			copy(p[1:], buf[off:off+chunk])
			if off == 0 {
				if key {
					p[1] &^= 0x01
				} else {
					p[1] |= 0x01
				}
			}
			off += chunk
			pkt.Header.PayloadType = 96
			pkt.Header.Marker = off >= size
			pkt.Header.SequenceNumber = seq
			pkt.Header.Timestamp = ts
			pkt.Payload = p
			seq++
			if err := s.tr.WriteRTP(pkt); err != nil && errors.Is(err, io.ErrClosedPipe) {
				return
			}
		}
	}
}
