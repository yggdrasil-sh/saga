package main

import (
	"encoding/binary"
	"net"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
)

// rawTrack schreibt fertige Koepfe (SSRC frei waehlbar: Medien, RTX, unbekannt) - wie ein Browser mit Fuellpaketen.
type rawTrack struct {
	id, stream string
	mu         sync.Mutex
	w          webrtc.TrackLocalWriter
	ssrc, rtx  uint32
	twccID     uint8
	bound      chan struct{}
}

func (t *rawTrack) Bind(c webrtc.TrackLocalContext) (webrtc.RTPCodecParameters, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.w, t.ssrc, t.rtx = c.WriteStream(), uint32(c.SSRC()), uint32(c.SSRCRetransmission())
	for _, h := range c.HeaderExtensions() {
		if h.URI == sdp.TransportCCURI {
			t.twccID = uint8(h.ID)
		}
	}
	for _, p := range c.CodecParameters() {
		if p.MimeType == webrtc.MimeTypeVP8 {
			close(t.bound)
			return p, nil
		}
	}
	return webrtc.RTPCodecParameters{}, webrtc.ErrUnsupportedCodec
}
func (t *rawTrack) Unbind(webrtc.TrackLocalContext) error { return nil }
func (t *rawTrack) ID() string                            { return t.id }
func (t *rawTrack) RID() string                           { return "" }
func (t *rawTrack) StreamID() string                      { return t.stream }
func (t *rawTrack) Kind() webrtc.RTPCodecType             { return webrtc.RTPCodecTypeVideo }

// twccLog merkt sich gesendete Transport-Folgenummern und die Rueckmeldungen des Knotens.
type twccLog struct {
	mu    sync.Mutex
	seq   uint16
	phase map[uint16]string
	got   map[uint16]bool // true = empfangen gemeldet, false = als fehlend gemeldet
}

func (l *twccLog) next(phase string) uint16 {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.seq++
	l.phase[l.seq] = phase
	return l.seq
}

func (l *twccLog) feedback(p *rtcp.TransportLayerCC) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s := p.BaseSequenceNumber
	for _, c := range p.PacketChunks {
		var st []uint16
		switch c := c.(type) {
		case *rtcp.RunLengthChunk:
			for i := uint16(0); i < c.RunLength; i++ {
				st = append(st, c.PacketStatusSymbol)
			}
		case *rtcp.StatusVectorChunk:
			st = c.SymbolList
		}
		for _, x := range st {
			if s-p.BaseSequenceNumber >= p.PacketStatusCount {
				break
			}
			rec := x != rtcp.TypeTCCPacketNotReceived
			if rec || !l.got[s] {
				l.got[s] = rec
			}
			s++
		}
	}
}

// TestUDPTWCCFuellpakete (BH3, Folgeauftrag): Ein Geraet teilt den Bildschirm ueber UDP, pausiert (nur Fuellpakete auf
// der RTX-SSRC, wie libwebrtc), teilt erneut. Jedes Paket mit transport-cc muss in der Rueckmeldung des Knotens als
// empfangen stehen - sonst wertet GCC beim Sender es als Verlust und senkt die Schaetzung.
func TestUDPTWCCFuellpakete(t *testing.T) {
	log := &twccLog{phase: map[uint16]string{}, got: map[uint16]bool{}}
	twccTap = log.feedback
	t.Cleanup(func() { twccTap = nil }) // nach dem Herunterfahren des Knotens (Cleanups laufen rueckwaerts)
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": "raum-twcc"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var tok struct{ Token string }
	if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": "raum-twcc", "peer": "anna", "role": "both", "udp": true}, &tok); err != nil {
		t.Fatal(err)
	}
	se := webrtc.SettingEngine{}
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000,
		RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}, {Type: "transport-cc"}}}, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		t.Fatal(err)
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000,
		SDPFmtpLine: "apt=96"}, PayloadType: 97}, webrtc.RTPCodecTypeVideo); err != nil {
		t.Fatal(err)
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.TransportCCURI}, webrtc.RTPCodecTypeVideo); err != nil {
		t.Fatal(err)
	}
	se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	se.SetIncludeLoopbackCandidate(true)
	se.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
	se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	api := webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithSettingEngine(se))
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	cam := &rawTrack{id: "cam", stream: "cam-anna", bound: make(chan struct{})}
	scr := &rawTrack{id: "scr", stream: "screen-anna", bound: make(chan struct{})}
	for _, tr := range []*rawTrack{cam, scr} {
		tx, err := pc.AddTransceiverFromTrack(tr, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly})
		if err != nil {
			t.Fatal(err)
		}
		go func() {
			b := make([]byte, 1500)
			for {
				if _, _, err := tx.Sender().Read(b); err != nil {
					return
				}
			}
		}()
	}
	if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	offer, _ := pc.CreateOffer(nil)
	gather := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(offer); err != nil {
		t.Fatal(err)
	}
	<-gather
	var ans struct{ ID, Answer string }
	if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "udp", Offer: pc.LocalDescription().SDP, Screen: true, Speaker: true}, &ans); err != nil {
		t.Fatal(err)
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
		t.Fatal(err)
	}
	for _, tr := range []*rawTrack{cam, scr} {
		select {
		case <-tr.bound:
		case <-time.After(10 * time.Second):
			t.Fatal("nicht verbunden")
		}
	}
	if scr.twccID == 0 || scr.rtx == 0 {
		t.Fatalf("transport-cc %d / rtx %d nicht ausgehandelt", scr.twccID, scr.rtx)
	}
	var mseq, rseq, cseq, useq uint16
	write := func(tr *rawTrack, ssrc uint32, pt uint8, sq *uint16, phase string, media bool, key bool) {
		*sq++
		h := &rtp.Header{Version: 2, PayloadType: pt, SequenceNumber: *sq, Timestamp: uint32(time.Now().UnixMilli()) * 90, SSRC: ssrc}
		var tw [2]byte
		binary.BigEndian.PutUint16(tw[:], log.next(phase))
		_ = h.SetExtension(tr.twccID, tw[:])
		var pl []byte
		if media {
			p := byte(1)
			if key {
				p = 0
			}
			pl = append([]byte{0x10, p}, make([]byte, 300)...)
			h.Marker = true
		} else { // reines Fuellpaket
			h.Padding = true
			pl = make([]byte, 224)
			pl[len(pl)-1] = 224
		}
		if _, err := tr.w.WriteRTP(h, pl); err != nil {
			t.Fatal(err)
		}
	}
	run := func(d time.Duration, every time.Duration, f func(i int)) {
		end := time.Now().Add(d)
		for i := 0; time.Now().Before(end); i++ {
			f(i)
			time.Sleep(every)
		}
	}
	// Kamera sendet nie (aus); Bildschirm: teilen, Pause mit Fuellpaketen auf RTX, erneut teilen
	run(1500*time.Millisecond, 33*time.Millisecond, func(i int) { write(scr, scr.ssrc, 96, &mseq, "1 bild", true, i == 0) })
	run(1500*time.Millisecond, 66*time.Millisecond, func(int) { write(scr, scr.rtx, 97, &rseq, "2 fuell-rtx-bildschirm", false, false) })
	run(1500*time.Millisecond, 66*time.Millisecond, func(int) { write(scr, scr.ssrc, 96, &mseq, "3 fuell-medien-bildschirm", false, false) })
	run(1500*time.Millisecond, 66*time.Millisecond, func(int) { write(cam, cam.rtx, 97, &cseq, "4 fuell-rtx-kamera (nie gesendet)", false, false) })
	run(1500*time.Millisecond, 66*time.Millisecond, func(int) { write(cam, 0x5eed1234, 97, &useq, "5 fuell-unbekannte-ssrc", false, false) })
	run(1500*time.Millisecond, 33*time.Millisecond, func(i int) { write(scr, scr.ssrc, 96, &mseq, "6 bild erneut", true, i == 0) })
	// ein einzelnes Paket mit nicht ausgehandeltem Payload-Typ auf der Bildschirm-SSRC, danach weiter Bild
	write(scr, scr.ssrc, 120, &mseq, "7 ein Paket mit fremdem PT", true, false)
	run(1500*time.Millisecond, 33*time.Millisecond, func(i int) { write(scr, scr.ssrc, 96, &mseq, "8 bild danach", true, i == 0) })
	time.Sleep(500 * time.Millisecond)

	log.mu.Lock()
	defer log.mu.Unlock()
	type cnt struct{ sent, ok, lost, none int }
	res := map[string]*cnt{}
	var order []string
	for q := uint16(1); q <= log.seq; q++ {
		ph := log.phase[q]
		c := res[ph]
		if c == nil {
			c = &cnt{}
			res[ph] = c
			order = append(order, ph)
		}
		c.sent++
		switch v, ok := log.got[q]; {
		case !ok:
			c.none++
		case v:
			c.ok++
		default:
			c.lost++
		}
	}
	bad := false
	for _, ph := range order {
		c := res[ph]
		t.Logf("%-36s gesendet %3d, empfangen gemeldet %3d, als verloren gemeldet %3d, ohne Meldung %3d", ph, c.sent, c.ok, c.lost, c.none)
		switch {
		case strings.HasPrefix(ph, "1 "): // Anfang: Rueckmeldung erst nach DTLS
			bad = bad || c.lost > 0
		case strings.HasPrefix(ph, "5 "): // unbekannte SSRC: pion liest sie nur waehrend der Probe (nur Bericht)
		default:
			bad = bad || c.ok != c.sent
		}
	}
	if bad {
		t.Error("der Knoten meldet empfangene Pakete per transport-cc nicht als empfangen (GCC beim Sender wertet das als Verlust)")
	}
}
