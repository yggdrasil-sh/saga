package main

import (
	"net"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

// TestUDPNackPausierterStrom (Labortest 10.11., Fund 1): Ein Geraet sendet Bild ueber UDP mit Luecken (Verlust-Schub)
// und pausiert den Strom danach (Simulcast-Ebene abgeschaltet, Kamera aus). Der Knoten darf die Luecken nur begrenzt
// nachfordern. Ohne Grenze kommen dauerhaft ~10 NACK/s, und die Neuuebertragungen zehren an der Senderate des Geraets.
func TestUDPNackPausierterStrom(t *testing.T) {
	var mu sync.Mutex
	var nacks []time.Time // NACK-Meldungen fuer die Kamera-SSRC
	var camSSRC uint32
	nackTap = func(p *rtcp.TransportLayerNack) {
		mu.Lock()
		defer mu.Unlock()
		if camSSRC != 0 && p.MediaSSRC == camSSRC {
			nacks = append(nacks, time.Now())
		}
	}
	t.Cleanup(func() { nackTap = nil }) // nach dem Herunterfahren des Knotens (Cleanups laufen rueckwaerts)
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": "raum-nack"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var tok struct{ Token string }
	if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": "raum-nack", "peer": "anna", "role": "both", "udp": true}, &tok); err != nil {
		t.Fatal(err)
	}
	se := webrtc.SettingEngine{}
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000,
		RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}}}, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		t.Fatal(err)
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000,
		SDPFmtpLine: "apt=96"}, PayloadType: 97}, webrtc.RTPCodecTypeVideo); err != nil {
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
	tx, err := pc.AddTransceiverFromTrack(cam, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly})
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
	if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "udp", Offer: pc.LocalDescription().SDP, Speaker: true}, &ans); err != nil {
		t.Fatal(err)
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-cam.bound:
	case <-time.After(10 * time.Second):
		t.Fatal("nicht verbunden")
	}
	mu.Lock()
	camSSRC = cam.ssrc
	mu.Unlock()
	// 3 s Bild; in der zweiten Haelfte fehlt jedes vierte Paket (Verlust-Schub), danach sendet das Geraet nichts mehr
	var seq uint16
	end := time.Now().Add(3 * time.Second)
	half := time.Now().Add(1500 * time.Millisecond)
	for i := 0; time.Now().Before(end); i++ {
		seq++
		if time.Now().After(half) && i%4 == 0 {
			time.Sleep(33 * time.Millisecond)
			continue // verloren
		}
		p := byte(1)
		if i == 0 {
			p = 0
		}
		h := &rtp.Header{Version: 2, PayloadType: 96, SequenceNumber: seq, Timestamp: uint32(time.Now().UnixMilli()) * 90, SSRC: cam.ssrc, Marker: true}
		if _, err := cam.w.WriteRTP(h, append([]byte{0x10, p}, make([]byte, 300)...)); err != nil {
			t.Fatal(err)
		}
		time.Sleep(33 * time.Millisecond)
	}
	pause := time.Now()
	time.Sleep(6 * time.Second)

	mu.Lock()
	defer mu.Unlock()
	var vor, frueh, spaet int
	for _, at := range nacks {
		switch d := at.Sub(pause); {
		case d < 0:
			vor++
		case d < 2*time.Second:
			frueh++
		default:
			spaet++
		}
	}
	t.Logf("NACK-Meldungen fuer die Kamera: %d waehrend des Sendens, %d in den ersten 2 s der Pause, %d in den 4 s danach", vor, frueh, spaet)
	if vor == 0 {
		t.Error("der Knoten hat die Luecken gar nicht nachgefordert (Test prueft so nichts)")
	}
	if spaet > 0 {
		t.Errorf("der Knoten fordert Pakete eines pausierten Stroms weiter nach: %d NACK-Meldungen ab 2 s nach dem letzten Paket", spaet)
	}
}
