package main

import (
	"encoding/binary"
	"encoding/json"
	"net"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/logging"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// engpass bildet auf der Empfangsseite eine Leitung mit Ratengrenze nach: Pakete warten in einer Schlange (hoechstens
// 300 ms, danach Verlust) und werden erst zu ihrer Zeit gelesen - transport-cc misst die Ankunft danach, GCC im Knoten
// sieht den Stau also wie auf einer echten Leitung. (fwd.Leg.SetThrottle bremst dagegen vor dem Absenden.)
type engpass struct {
	net.PacketConn
	rate atomic.Int64  // bit/s, 0 = frei
	wait time.Duration // feste Laufzeit
	ch   chan engPkt
	last time.Time
	lost atomic.Int64
}

type engPkt struct {
	b    []byte
	from net.Addr
	at   time.Time
}

func newEngpass(c net.PacketConn) *engpass {
	e := &engpass{PacketConn: c, ch: make(chan engPkt, 8192), wait: 20 * time.Millisecond}
	go func() {
		for {
			b := make([]byte, 1600)
			n, from, err := c.ReadFrom(b)
			if err != nil {
				close(e.ch)
				return
			}
			now := time.Now()
			at := now.Add(e.wait)
			if r := e.rate.Load(); r > 0 {
				if e.last.Before(now) {
					e.last = now
				}
				if e.last.Sub(now) > 300*time.Millisecond {
					e.lost.Add(1)
					continue
				}
				e.last = e.last.Add(time.Duration(float64(n*8) / float64(r) * 1e9))
				at = e.last.Add(e.wait)
			}
			e.ch <- engPkt{b[:n], from, at}
		}
	}()
	return e
}

func (e *engpass) ReadFrom(b []byte) (int, net.Addr, error) {
	p, ok := <-e.ch
	if !ok {
		return 0, nil, net.ErrClosed
	}
	if d := time.Until(p.at); d > 0 {
		time.Sleep(d)
	}
	return copy(b, p.b), p.from, nil
}

// probeBotAPI: Empfaenger wie sfuBotAPI("udp"), aber hinter einem engpass und mit NACK wie ein Browser.
func probeBotAPI(t *testing.T) (*webrtc.API, *engpass) {
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
	eng := newEngpass(uc)
	se.SetICEUDPMux(ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: eng, Logger: lf.NewLogger("udpmux")}))
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
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), eng
}

// TestUDPProbing: Ende zu Ende mit pion-Gegenstelle. Ein Sender mit l/m/h (200/600/1500 kbit/s), ein Empfaenger auf
// UDP hinter einem Engpass; der Knoten laeuft mit Probing. Fuellpakete gehen durch den pion-Schreibweg (transport-cc,
// SRTP), GCC sieht sie.
//   - frei: Der erste Schritt auf h laeuft ohne Probe (Anrufbeginn wie ohne Probing). Dann 2 s Stau (Leitung auf
//     700 kbit/s): Der Platz faellt, GCC springt zurueck. Wieder frei, ist fuer den Weg zurueck eine Probe noetig (h hielt
//     keine 30 s) - sie besteht, obwohl pion-GCC sein Ziel dabei an die Empfangsrate klemmen kann (frueher: fail:gcc);
//     h kommt und haelt, der Empfaenger sieht ab da lueckenlose Folgenummern und keine zusaetzlichen Pausen.
//   - eng (1,2 Mbit/s, traegt m, nicht h): Nach dem einen freien Versuch zum Anrufbeginn scheitert die Probe (Stau laut
//     eigener Auswertung), der Platz bleibt auf m - kein weiterer Wechsel, keine Bildpause.
func TestUDPProbing(t *testing.T) {
	// HORN_BWE_ART=eigen: derselbe Ablauf mit dem eigenen Stau-Detektor (sonst der Standard gcc)
	mode, _ := parseBWE(os.Getenv("HORN_BWE_ART"))
	testUDPProbing(t, mode)
}

// TestUDPProbingEigen: derselbe Ablauf mit HORN_BWE=eigen - der Detektor wertet die Proben und widerlegt grundlose
// Faelle des GCC-Ziels; an echter Enge aendert sich nichts.
func TestUDPProbingEigen(t *testing.T) { testUDPProbing(t, bweEigen) }

func testUDPProbing(t *testing.T, mode string) {
	for _, c := range []struct {
		name string
		link int64
	}{{"frei", 0}, {"eng-1200", 1200e3}} {
		t.Run(c.name, func(t *testing.T) {
			cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", UDP: true, UDPListen: "127.0.0.1:0", Data: t.TempDir(),
				ICEIPs: []string{"127.0.0.1"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 32, Mode: "node", Dev: true, Probing: true, BWE: mode,
				Certs: netx.CertOptions{Name: "localhost", Self: true}}
			s, err := newServer(cfg)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(s.shutdown)
			base := "https://" + s.mux.Addr().String()
			hc := httpClient()
			if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": "raum-probe"}, &struct{}{}); err != nil {
				t.Fatal(err)
			}
			sapi, err := sfuBotAPI("udp")
			if err != nil {
				t.Fatal(err)
			}
			snd := &sfuBot{peer: "sender", path: "udp", video: true, layers: []string{"l", "m", "h"}}
			snd.level.Store(20)
			rapi, eng := probeBotAPI(t)
			eng.rate.Store(c.link)
			rcv := &sfuBot{peer: "empf", path: "udp"}
			rcv.level.Store(127)
			for _, b := range []*sfuBot{snd, rcv} {
				api := sapi
				if b == rcv {
					api = rapi
					time.Sleep(4 * time.Second) // die Raten der Quellen sind eingeschwungen
				}
				if err := b.join(hc, base, api, "raum-probe", 0, "desktop"); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(b.close)
				b.start()
			}
			// Senderberichte des Knotens verarbeiten (pion liest RTCP nur, wenn jemand liest): Erst damit tragen die
			// Empfaengerberichte LSR/DLSR wie bei einem Browser, und der Knoten kennt die Umlaufzeit.
			for _, r := range rcv.pcs[0].GetReceivers() {
				go func() {
					buf := make([]byte, 1500)
					for {
						if _, _, err := r.Read(buf); err != nil {
							return
						}
					}
				}()
			}
			l := s.getLeg(rcv.legIDs[0])
			if l == nil || l.sl == nil {
				t.Fatal("bein fehlt")
			}
			layer := func() string {
				for _, sl := range l.sl.Layout() {
					if sl.Class == "speaker" {
						return sl.Layer
					}
				}
				return ""
			}
			deadline := time.Now().Add(15 * time.Second)
			for layer() != "m" && layer() != "h" || rcv.rxPkts.Load() == 0 {
				if time.Now().After(deadline) {
					t.Fatalf("kein Bild: %q", layer())
				}
				time.Sleep(50 * time.Millisecond)
			}
			ps := l.sl.ProbeStats
			st := &l.fl.Stats
			report := func(what string, hEp int, hDur time.Duration) {
				_, _, frames := rcv.freezes()
				t.Logf("%s: schicht %q (h-Abschnitte %d, zusammen %v), proben %+v, schaetzung %d kbit/s, rtt %v, empfaenger: %d Pakete, %d Luecken, "+
					"%d Rahmen; bein: %d Pausen, %d verworfene Rahmen; engpass-verlust %d", what, layer(), hEp, hDur.Round(100*time.Millisecond), ps(),
					l.sl.EstimateKbit(), time.Duration(l.rttNs.Load()).Round(100*time.Microsecond), rcv.rxPkts.Load(), rcv.rxLost.Load(), frames,
					st.Pauses.Load(), st.FramesDropped.Load(), eng.lost.Load())
			}
			// until wartet, bis ok() gilt (hoechstens max); zaehlt dabei die Abschnitte auf h und ihre Dauer
			hEp, hDur, wasH := 0, time.Duration(0), false
			until := func(max time.Duration, ok func() bool) bool {
				for end := time.Now().Add(max); time.Now().Before(end); {
					time.Sleep(100 * time.Millisecond)
					if is := layer() == "h"; is {
						if hDur += 100 * time.Millisecond; !wasH {
							hEp++
						}
						wasH = true
					} else {
						wasH = false
					}
					if ok() {
						return true
					}
				}
				return false
			}
			if c.link == 0 {
				// 1. Anrufbeginn: h ohne Probe
				if !until(15*time.Second, func() bool { return layer() == "h" }) || ps().Started != 0 {
					report("anrufbeginn", hEp, hDur)
					t.Fatalf("anrufbeginn: schicht %q, proben %+v (erwartet h ohne Probe)", layer(), ps())
				}
				time.Sleep(2 * time.Second)
				// 2. Stau: der Platz faellt
				eng.rate.Store(700e3)
				fell := until(6*time.Second, func() bool { return layer() != "h" })
				time.Sleep(time.Second)
				eng.rate.Store(0)
				if !fell {
					report("stau", hEp, hDur)
					t.Fatalf("stau: schicht %q bleibt", layer())
				}
				time.Sleep(1500 * time.Millisecond) // Schlange geleert, Nachsendungen durch
				lost0, pause0, drop0 := rcv.rxLost.Load(), st.Pauses.Load(), st.FramesDropped.Load()
				// 3. wieder frei: zurueck auf h nur ueber eine bestandene Probe, und h haelt
				back := until(45*time.Second, func() bool { return layer() == "h" && ps().Passed > 0 })
				held := back && !until(4*time.Second, func() bool { return layer() != "h" })
				report("frei", hEp, hDur)
				got := ps()
				if !back || !held || got.Passed == 0 {
					t.Fatalf("freie Leitung nach dem Stau: zurueck %v, gehalten %v, proben %+v", back, held, got)
				}
				// Eine Probe darf am echten Stau scheitern (sie kann noch in die Stauphase fallen) - auf der freien
				// Leitung danach keine: hoechstens eine gescheiterte bzw. unklare insgesamt
				if got.Failed+got.Unclear > 1 {
					t.Fatalf("freie Leitung: proben %+v", got)
				}
				if d := rcv.rxLost.Load() - lost0; d != 0 {
					t.Fatalf("%d Luecken in den Folgenummern beim Empfaenger seit dem Freiwerden", d)
				}
				if st.Pauses.Load() != pause0 || st.FramesDropped.Load() != drop0 {
					t.Fatalf("bildpause bzw. verworfene Rahmen seit dem Freiwerden: %d / %d", st.Pauses.Load()-pause0, st.FramesDropped.Load()-drop0)
				}
				if got.PadBytes < 60e3 || got.PadBytes > 600e3 {
					t.Fatalf("fuelldaten %d Byte fuer %d Probe(n)", got.PadBytes, got.Started)
				}
			} else {
				// enge Leitung: bis 3 s nach der ersten gescheiterten Probe fuer den Schritt auf h (Zielrate ueber der
				// Leitung); danach darf h nicht mehr kommen
				until(45*time.Second, func() bool { return ps().Failed > 0 })
				ep0 := hEp
				until(3*time.Second, func() bool { return false })
				report("eng", hEp, hDur)
				got := ps()
				if got.Started == 0 {
					// enge Leitung unter Last: Liegt das GCC-Ziel die ganze Zeit unter 1,25 x der Rate, geht nichts nach oben
					// und es wird auch nicht geprobt (Regel von vor dem Probing) - dann prueft dieser Lauf nur "kaum h"
					t.Logf("keine Probe")
				}
				// Faellt der Platz zwischendurch auf l (pion-GCC nimmt sein Ziel auf dieser Leitung zeitweise weit zurueck,
				// auch ohne Probing), darf die Probe fuer den Schritt zurueck auf m bestehen. h kommt hoechstens mit dem
				// einen freien Versuch zum Anrufbeginn - kurz, und nicht mehr nach der ersten gescheiterten Probe.
				if hEp > 1 || hDur > 4*time.Second || got.Failed > 0 && hEp != ep0 {
					t.Fatalf("enge Leitung: %d Abschnitte auf h (%v), davon %d nach der gescheiterten Probe; proben %+v", hEp, hDur, hEp-ep0, got)
				}
				if st.Pauses.Load() != 0 {
					t.Fatalf("bildpause: %d", st.Pauses.Load())
				}
			}
			if rtt := time.Duration(l.rttNs.Load()); rtt < 15*time.Millisecond || rtt > 80*time.Millisecond {
				t.Fatalf("umlaufzeit %v (der Engpass verzoegert um 20 ms)", rtt)
			}
		})
	}
}

// TestProbeLogzeile: die Zeile je Probenende im Log des Knotens - Kurzkennung des Beins, Schritt, Zielrate, Ergebnis,
// Grund, Dauer; nichts, was auf Personen, Geraete oder Raeume zeigt (das Ereignis traegt nur das Bein).
func TestProbeLogzeile(t *testing.T) {
	got := probeLine("5884a4", sfu.ProbeEvent{Class: "speaker", From: "m", To: "h", TargetKbit: 1882, FillKbit: 1136, Result: "fail",
		Why: "gcc", Dur: 400 * time.Millisecond, Stage: 1, EstKbit: 1338, GccKbit: 721})
	want := "probe 5884a4 platz=speaker schritt=m>h ziel=1882k fuell=1136k ergebnis=fail grund=gcc dauer=0.4s stufe=1 geklemmt=0 hinweis=- est=1338k gcc=721k"
	if got != want {
		t.Fatalf("log-zeile\n %s\nerwartet\n %s", got, want)
	}
	got = probeLine("0e9442", sfu.ProbeEvent{Class: "tile", To: "l", TargetKbit: 830, FillKbit: 500, Result: "ok", Dur: 1400 * time.Millisecond,
		Stage: 3, Clamped: 1, Hint: "over", EstKbit: 830, GccKbit: 1100})
	want = "probe 0e9442 platz=tile schritt=p>l ziel=830k fuell=500k ergebnis=ok grund=- dauer=1.4s stufe=3 geklemmt=1 hinweis=over est=830k gcc=1100k"
	if got != want {
		t.Fatalf("log-zeile\n %s\nerwartet\n %s", got, want)
	}
}

// TestNackOhneFuellpakete: Der NACK-Antworter des Knotens puffert reine Fuellpakete nicht - ein NACK dafuer bleibt
// ohne Antwort, Medienpakete werden wie bisher nachgesendet.
func TestNackOhneFuellpakete(t *testing.T) {
	m := &webrtc.MediaEngine{}
	ir := &interceptor.Registry{}
	if err := configureNackNoPad(m, ir); err != nil {
		t.Fatal(err)
	}
	i, err := ir.Build("t")
	if err != nil {
		t.Fatal(err)
	}
	defer i.Close()
	var mu sync.Mutex
	var out []uint16
	info := &interceptor.StreamInfo{SSRC: 7, RTCPFeedback: []interceptor.RTCPFeedback{{Type: "nack"}}}
	w := i.BindLocalStream(info, interceptor.RTPWriterFunc(func(h *rtp.Header, _ []byte, _ interceptor.Attributes) (int, error) {
		mu.Lock()
		out = append(out, h.SequenceNumber)
		mu.Unlock()
		return 0, nil
	}))
	pad := make([]byte, fwd.PadSize)
	pad[fwd.PadSize-1] = fwd.PadSize
	for seq := uint16(1); seq <= 6; seq++ {
		h := &rtp.Header{Version: 2, SSRC: 7, SequenceNumber: seq, PayloadType: 96}
		pl := []byte{0x10, 1, 2, 3}
		if seq == 3 || seq == 4 { // Fuellpakete zwischen zwei Bildern
			h.Padding, pl = true, pad
		}
		if _, err := w.Write(h, pl, nil); err != nil {
			t.Fatal(err)
		}
	}
	nk, _ := (&rtcp.TransportLayerNack{MediaSSRC: 7, Nacks: []rtcp.NackPair{{PacketID: 2, LostPackets: 0b111}}}).Marshal() // 2, 3, 4, 5
	rd := i.BindRTCPReader(interceptor.RTCPReaderFunc(func(b []byte, a interceptor.Attributes) (int, interceptor.Attributes, error) {
		return copy(b, nk), a, nil
	}))
	if _, _, err := rd.Read(make([]byte, 1500), nil); err != nil {
		t.Fatal(err)
	}
	time.Sleep(200 * time.Millisecond)
	mu.Lock()
	defer mu.Unlock()
	re := out[6:]
	if len(out) < 6 || len(re) != 2 || re[0]+re[1] != 7 {
		t.Fatalf("geschrieben %v, nachgesendet %v (erwartet 2 und 5, nicht die Fuellpakete 3 und 4)", out[:min(6, len(out))], re)
	}
}

// TestRRRTT: Umlaufzeit aus einem Empfaengerbericht (jetzt - LSR - DLSR).
func TestRRRTT(t *testing.T) {
	now := time.Unix(1_800_000_000, 250_000_000)
	ntp := func(tm time.Time) uint32 {
		sec := uint64(tm.Unix()) + 2208988800
		return uint32((sec<<32 | uint64(tm.Nanosecond())<<32/1e9) >> 16)
	}
	rr := &rtcp.ReceiverReport{SSRC: 1, Reports: []rtcp.ReceptionReport{{SSRC: 7, LastSenderReport: ntp(now.Add(-180 * time.Millisecond)),
		Delay: 100 * 65536 / 1000}}}
	b, err := rr.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	pli, _ := (&rtcp.PictureLossIndication{MediaSSRC: 7}).Marshal()
	d, ok := rrRTT(append(pli, b...), now)
	if !ok || d < 79*time.Millisecond || d > 81*time.Millisecond {
		t.Fatalf("umlaufzeit %v (%v), erwartet 80 ms", d, ok)
	}
	if !hasKeyRequest(append(pli, b...)) {
		t.Fatal("PLI im Verbund nicht erkannt")
	}
	binary.BigEndian.PutUint32(b[8+16:], 0) // noch kein Senderbericht angekommen
	if _, ok := rrRTT(b, now); ok {
		t.Fatal("umlaufzeit ohne LSR")
	}
	if _, ok := rrRTT(b[:len(b)-3], now); ok {
		t.Fatal("abgeschnittener Bericht")
	}
}

// TestStatsLateDropped: Der Zaehler der verworfenen Nachzuegler (fwd.LegStats.LateDropped) steht in der Statistik des
// Beins - wie pad_bytes nur, wenn er nicht null ist.
func TestStatsLateDropped(t *testing.T) {
	l := &leg{id: "b1", path: "udp", role: "sub", fl: fwd.NewLeg(), created: time.Now()}
	defer l.fl.Close()
	js := func() map[string]any {
		b, err := json.Marshal(l.stats())
		if err != nil {
			t.Fatal(err)
		}
		m := map[string]any{}
		if err := json.Unmarshal(b, &m); err != nil {
			t.Fatal(err)
		}
		return m
	}
	m := js()
	if _, ok := m["late_dropped"]; ok {
		t.Fatalf("late_dropped ohne Nachzuegler in der Statistik: %v", m)
	}
	if _, ok := m["pad_bytes"]; ok {
		t.Fatalf("pad_bytes ohne Probe in der Statistik: %v", m)
	}
	l.fl.Stats.LateDropped.Add(3)
	l.fl.Stats.PadBytes.Add(267)
	m = js()
	if m["late_dropped"] != float64(3) || m["pad_bytes"] != float64(267) {
		t.Fatalf("late_dropped %v, pad_bytes %v (erwartet 3 und 267)", m["late_dropped"], m["pad_bytes"])
	}
}
