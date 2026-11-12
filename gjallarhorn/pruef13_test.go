package main

import (
	"encoding/binary"
	"net"
	"net/http"
	"sort"
	"sync"
	"testing"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// TestPruef13EndSrcNebenlaeufig (Pruefung pruef-bwe13): Spuren eines Beins kommen und gehen, waehrend die Statistik
// gelesen wird (GET /api/sfu/leg/{id}, Zeile "bein zu") - unter -race ohne Befund, und keine Spur zaehlt doppelt oder
// gar nicht: laufende Spuren (sources[]) plus Summe der beendeten ergeben zu jedem Zeitpunkt hoechstens, am Ende
// genau die Zaehler aller Spuren.
func TestPruef13EndSrcNebenlaeufig(t *testing.T) {
	l := &leg{id: "bein-pruef13", created: time.Now(), fl: fwd.NewLeg()}
	defer l.fl.Close()
	const n = 200
	var wg sync.WaitGroup
	stop := make(chan struct{})
	wg.Add(1)
	go func() { // Leser
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
			}
			st := l.stats()
			sum := st.ended[0]
			for _, x := range st.Sources {
				sum += x.Dups
			}
			if sum > n || len(st.Sources) > n {
				t.Errorf("Zwischenstand: Doppel %d, %d laufende Spuren", sum, len(st.Sources))
				return
			}
		}
	}()
	for w := 0; w < 4; w++ { // vier "Spuren-Goroutinen" wie sfuTrack: anmelden, zaehlen, abmelden
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < n/4; i++ {
				src := &fwd.Source{Kind: fwd.Video, Layer: "h"}
				l.mu.Lock()
				l.srcs = append(l.srcs, src)
				l.mu.Unlock()
				src.Dups.Add(1)
				src.TooOld.Add(2)
				src.Late.Add(3)
				if i%2 == 0 {
					l.endSrc(src)
				}
			}
		}()
	}
	time.Sleep(20 * time.Millisecond)
	close(stop)
	wg.Wait()
	for _, src := range append([]*fwd.Source(nil), l.srcs...) { // der Rest endet mit dem Bein
		l.endSrc(src)
	}
	if st := l.stats(); len(st.Sources) != 0 || st.ended != [3]uint64{n, 2 * n, 3 * n} {
		t.Fatalf("am Ende: %d laufende Spuren, beendet %v (erwartet %d/%d/%d)", len(st.Sources), st.ended, n, 2*n, 3*n)
	}
}

// p13Peer: pion-Gegenstelle am Knoten ueber UDP, VP8 mit NACK und RTX ausgehandelt, ohne Interceptoren (sie fordert
// nichts nach und wiederholt nichts von selbst). Mit cam sendet sie ueber eine rawTrack (Medien- und RTX-SSRC
// frei beschreibbar), mit rx zeichnet sie auf, was auf ihrem Sprecherplatz ankommt. Liefert die Kennung des Beins.
func p13Peer(t *testing.T, hc *http.Client, base, room, peer string, cam *rawTrack, rx func(*rtp.Packet)) string {
	t.Helper()
	var tok struct{ Token string }
	if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": room, "peer": peer, "role": "both", "udp": true}, &tok); err != nil {
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
	// leere Registry ausdruecklich: ohne sie traegt pion die ueblichen Interceptoren selbst ein (NACK-Erzeuger und
	// -Antworter) - dann kreuzt gelegentlich eine Nachforderung ein nachgekommenes Paket, und dessen Wiederholung
	// ueber RTX kaeme als weiteres Doppel hinzu (am Knoten bzw. am Empfaenger)
	pc, err := webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithSettingEngine(se),
		webrtc.WithInterceptorRegistry(&interceptor.Registry{})).NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = pc.Close() })
	if cam != nil {
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
	}
	if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
		t.Fatal(err)
	}
	if rx != nil {
		pc.OnTrack(func(tr *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
			go func() {
				for {
					p, _, err := tr.ReadRTP()
					if err != nil {
						return
					}
					rx(p)
				}
			}()
		})
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
	return ans.ID
}

// TestPruef13RTXUeberPion (Pruefung pruef-bwe13): der Abgleich Ende zu Ende mit echtem pion auf beiden Seiten - bisher
// war nur gelesen, dass pion RTX-Pakete entpackt mit der urspruenglichen Folgenummer auf der Spur zurueckgibt. Ein
// Geraet sendet Bild ueber UDP; zwei Pakete bleiben zunaechst aus. Dann schickt es ueber die RTX-SSRC (wie libwebrtc)
// Wiederholungen schon gesendeter Pakete und die erste Fassung eines ausgebliebenen; das zweite ausgebliebene kommt
// verspaetet auf der Medien-SSRC, danach noch einmal ueber RTX.
//
// Erwartet: Die Quelle am Knoten zaehlt jede Wiederholung als Doppel und beide Nachzuegler als spaete erste Exemplare;
// der Empfaenger bekommt jedes Paket genau einmal - der Knoten schreibt nicht mehr Pakete, als der Empfaenger zaehlt -,
// die Nachzuegler mit der Folgenummer ihrer Luecke (die Folge am Empfaenger ist am Ende lueckenlos).
func TestPruef13RTXUeberPion(t *testing.T) {
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": "raum-rtx"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	var got []uint16 // Folgenummern am Empfaenger, in der Reihenfolge der Ankunft
	bert := p13Peer(t, hc, base, "raum-rtx", "bert", nil, func(p *rtp.Packet) {
		mu.Lock()
		got = append(got, p.SequenceNumber)
		mu.Unlock()
	})
	count := func() int { mu.Lock(); defer mu.Unlock(); return len(got) }
	cam := &rawTrack{id: "cam", stream: "cam-anna", bound: make(chan struct{})}
	anna := p13Peer(t, hc, base, "raum-rtx", "anna", cam, nil)
	select {
	case <-cam.bound:
	case <-time.After(10 * time.Second):
		t.Fatal("nicht verbunden")
	}
	if cam.rtx == 0 {
		t.Fatal("RTX nicht ausgehandelt")
	}
	ts := func(q uint16) uint32 { return 1000 + uint32(q)*3000 }
	pl := func(q uint16, key bool) []byte { // ein Bild je Paket; die Folgenummer der Quelle steht in der Nutzlast
		p := byte(1)
		if key {
			p = 0
		}
		b := append([]byte{0x10, p}, make([]byte, 300)...)
		binary.BigEndian.PutUint16(b[2:], q)
		return b
	}
	media := func(q uint16, key bool) {
		h := &rtp.Header{Version: 2, PayloadType: 96, SequenceNumber: q, Timestamp: ts(q), SSRC: cam.ssrc, Marker: true}
		if _, err := cam.w.WriteRTP(h, pl(q, key)); err != nil {
			t.Fatal(err)
		}
	}
	var rseq uint16
	rtx := func(q uint16) { // Wiederholung ueber RTX: eigene SSRC und Folge, davor die urspruengliche Nummer (RFC 4588)
		rseq++
		h := &rtp.Header{Version: 2, PayloadType: 97, SequenceNumber: rseq, Timestamp: ts(q), SSRC: cam.rtx, Marker: true}
		if _, err := cam.w.WriteRTP(h, append([]byte{byte(q >> 8), byte(q)}, pl(q, false)...)); err != nil {
			t.Fatal(err)
		}
	}
	// Anlauf: Schluesselbilder, bis der Empfaenger die Quelle bekommt
	var seq uint16
	for end := time.Now().Add(10 * time.Second); count() < 3; {
		if time.Now().After(end) {
			t.Fatal("der Empfaenger bekommt kein Bild")
		}
		seq++
		media(seq, true)
		time.Sleep(50 * time.Millisecond)
	}
	b0 := seq
	for i := uint16(1); i <= 60; i++ {
		if i == 20 || i == 21 {
			continue // bleiben zunaechst aus
		}
		media(b0+i, i%10 == 0)
		time.Sleep(10 * time.Millisecond)
	}
	for _, f := range []func(){
		func() { rtx(b0 + 30) }, func() { rtx(b0 + 31) }, func() { rtx(b0 + 32) }, func() { rtx(b0 + 30) }, // vier Wiederholungen
		func() { rtx(b0 + 20) },        // erste Fassung eines ausgebliebenen Pakets, ueber RTX
		func() { rtx(b0 + 20) },        // ... und noch einmal
		func() { media(b0+21, false) }, // das zweite kommt verspaetet auf der Medien-SSRC
		func() { rtx(b0 + 21) },        // ... und danach ueber RTX
		func() { media(b0+61, false) }, func() { media(b0+62, false) },
	} {
		f()
		time.Sleep(20 * time.Millisecond)
	}
	// so viele verschiedene Pakete hat das Geraet gesendet; die ersten davon gingen hinaus, bevor die Spur am Knoten
	// stand - mindestens zaehlt die Quelle die 62 nach dem Anlauf und die 3, die der Empfaenger im Anlauf schon bekam
	total := uint64(b0) + 62
	deadline := time.Now().Add(5 * time.Second)
	var src srcStats
	for {
		for _, x := range s.getLeg(anna).stats().Sources {
			if x.Kind == "video" {
				src = x
			}
		}
		if (src.Dups == 6 && src.Late == 2) || time.Now().After(deadline) {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Logf("Quelle am Knoten: %+v (gesendet %d verschiedene Pakete, 6 Wiederholungen, 2 Nachzuegler)", src, total)
	if src.Pkts < 65 || src.Pkts > total || src.Dups != 6 || src.Late != 2 || src.TooOld != 0 {
		t.Fatalf("Zaehler der Quelle: %+v - erwartet pkts 65 bis %d, dups 6, late 2, too_old 0", src, total)
	}
	// was der Knoten geschrieben hat, muss der Empfaenger erst lesen - unter Last bis zu 3 s Zeit dafuer
	time.Sleep(300 * time.Millisecond)
	out := s.getLeg(bert).stats().PktsOut
	for end := time.Now().Add(3 * time.Second); uint64(count()) < out && time.Now().Before(end); {
		time.Sleep(50 * time.Millisecond)
		out = s.getLeg(bert).stats().PktsOut
	}
	mu.Lock()
	defer mu.Unlock()
	seen := map[uint16]int{}
	for i, q := range got {
		if j, dup := seen[q]; dup {
			t.Fatalf("Folgenummer %d zweimal am Empfaenger (Paket %d und %d)", q, j, i)
		}
		seen[q] = i
	}
	sorted := append([]uint16(nil), got...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
	t.Logf("Empfaenger: %d Pakete, Folgenummern %d bis %d; der Knoten hat %d geschrieben", len(got), sorted[0], sorted[len(sorted)-1], out)
	if uint64(len(got)) != out {
		t.Fatalf("der Knoten hat %d Pakete geschrieben, der Empfaenger zaehlt %d - Wiederholungen weitergeleitet?", out, len(got))
	}
	if int(sorted[len(sorted)-1]-sorted[0])+1 != len(sorted) {
		t.Fatalf("Luecke in der Folge am Empfaenger: %d Pakete zwischen %d und %d", len(sorted), sorted[0], sorted[len(sorted)-1])
	}
	// die beiden Nachzuegler tragen die Nummern ihrer Luecken (der Ausgang beginnt mit der Zaehlung der Quelle) und
	// kamen nach dem Paket 60 an
	for _, q := range []uint16{b0 + 20, b0 + 21} {
		i, ok := seen[q]
		if !ok || i < seen[b0+60] {
			t.Fatalf("Nachzuegler %d: am Empfaenger %v (Stelle %d, Paket 60 an Stelle %d)", q, ok, i, seen[b0+60])
		}
	}
}
