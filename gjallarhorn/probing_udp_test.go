package main

import (
	"encoding/binary"
	"net"
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
//   - frei: Der Schritt auf h ist der Schaetzung zu gross - die Probe besteht, h kommt und haelt; der Empfaenger sieht
//     lueckenlose Folgenummern und keine zusaetzlichen Pausen.
//   - eng (1,2 Mbit/s, traegt m, nicht h): Die Probe scheitert (GCC meldet Ueberlast), der Platz bleibt auf m - kein
//     Wechsel, kein Schluesselbild der h-Schicht fuer diesen Empfaenger, keine Bildpause.
func TestUDPProbing(t *testing.T) {
	for _, c := range []struct {
		name string
		link int64
	}{{"frei", 0}, {"eng-1200", 1200e3}} {
		t.Run(c.name, func(t *testing.T) {
			cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", UDP: true, UDPListen: "127.0.0.1:0", Data: t.TempDir(),
				ICEIPs: []string{"127.0.0.1"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 32, Mode: "node", Dev: true, Probing: true,
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
			// beobachten: Schicht, Proben. Eine einzelne Probe darf auch auf freier Leitung scheitern (pion-GCC meldet bei
			// Laufzeitschwankungen gelegentlich Ueberlast) - das kostet nur den Abstand bis zur naechsten (10 s).
			sawH, hAt := false, time.Duration(0)
			start := time.Now()
			watch, failAt := 40*time.Second, time.Duration(0)
			for time.Since(start) < watch {
				time.Sleep(100 * time.Millisecond)
				if layer() == "h" && !sawH {
					sawH, hAt = true, time.Since(start)
				}
				if c.link == 0 && sawH && time.Since(start) > hAt+4*time.Second {
					break
				}
				if c.link != 0 { // enge Leitung: bis 3 s nach der ersten gescheiterten Probe
					if failAt == 0 && l.sl.ProbeStats().Failed > 0 {
						failAt = time.Since(start)
					}
					if failAt != 0 && time.Since(start) > failAt+3*time.Second {
						break
					}
				}
			}
			ps := l.sl.ProbeStats()
			st := &l.fl.Stats
			_, _, frames := rcv.freezes()
			t.Logf("schicht %q (h gesehen: %v nach %v), proben %+v, schaetzung %d kbit/s, rtt %v, empfaenger: %d Pakete, %d Luecken, %d Rahmen; "+
				"bein: %d Pausen, %d verworfene Rahmen; engpass-verlust %d", layer(), sawH, hAt.Round(100*time.Millisecond), ps, l.sl.EstimateKbit(),
				time.Duration(l.rttNs.Load()).Round(100*time.Microsecond), rcv.rxPkts.Load(), rcv.rxLost.Load(), frames, st.Pauses.Load(),
				st.FramesDropped.Load(), eng.lost.Load())
			if ps.Started == 0 {
				// enge Leitung unter Last: Liegt das GCC-Ziel die ganze Zeit unter 1,25 x der Rate, geht nichts nach oben
				// und es wird auch nicht geprobt (Regel von vor dem Probing) - dann prueft dieser Lauf nur "kein h"
				if c.link == 0 {
					t.Fatalf("keine Probe: %+v", ps)
				}
				t.Logf("keine Probe in %v", watch)
			}
			if st.Pauses.Load() != 0 || st.FramesDropped.Load() != 0 {
				t.Fatalf("bildpause bzw. verworfene Rahmen durch die Probe: %d / %d", st.Pauses.Load(), st.FramesDropped.Load())
			}
			if rtt := time.Duration(l.rttNs.Load()); rtt < 15*time.Millisecond || rtt > 80*time.Millisecond {
				t.Fatalf("umlaufzeit %v (der Engpass verzoegert um 20 ms)", rtt)
			}
			if c.link == 0 {
				if !sawH || layer() != "h" || ps.Passed == 0 || ps.Failed > 2 {
					t.Fatalf("freie Leitung: schicht %q, proben %+v", layer(), ps)
				}
				if rcv.rxLost.Load() != 0 {
					t.Fatalf("%d Luecken in den Folgenummern beim Empfaenger", rcv.rxLost.Load())
				}
				if ps.PadBytes < 60e3 || ps.PadBytes > 400e3 {
					t.Fatalf("fuelldaten %d Byte fuer %d Probe(n)", ps.PadBytes, ps.Started)
				}
			} else {
				// Faellt der Platz zwischendurch auf l (pion-GCC nimmt sein Ziel auf dieser Leitung zeitweise weit zurueck,
				// auch ohne Probing), darf die Probe fuer den Schritt zurueck auf m bestehen - h darf nie kommen.
				if sawH {
					t.Fatalf("enge Leitung: h gesehen, proben %+v", ps)
				}
			}
		})
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
