package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/webrtc/v4"
)

func webrtcConfig() webrtc.Configuration { return webrtc.Configuration{} }

// audioOffer: Angebot einer Ton-Leitung (eigene Spur + 3 Empfangsplaetze), Kandidaten gesammelt.
func audioOffer(t *testing.T, pc *webrtc.PeerConnection) string {
	t.Helper()
	tr, err := webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}, "a", "cam-x")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := pc.AddTransceiverFromTrack(tr, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly}); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
			t.Fatal(err)
		}
	}
	o, err := pc.CreateOffer(nil)
	if err != nil {
		t.Fatal(err)
	}
	g := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(o); err != nil {
		t.Fatal(err)
	}
	<-g
	return pc.LocalDescription().SDP
}

type evMsgT struct {
	T       string            `json:"t"`
	Leg     string            `json:"leg"`
	Peers   map[string]uint32 `json:"peers"`
	Slots   []map[string]any  `json:"slots"`
	State   string            `json:"state"`
	Why     string            `json:"why"`
	N       int64             `json:"n"`
	EstKbit int               `json:"est_kbit"`
	at      time.Time
}

func evDial(t *testing.T, base, leg, origin string) (*websocket.Conn, chan evMsgT) {
	t.Helper()
	d := websocket.Dialer{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}, HandshakeTimeout: 5 * time.Second}
	h := http.Header{}
	if origin != "" {
		h.Set("Origin", origin)
	}
	c, _, err := d.Dial("wss"+strings.TrimPrefix(base, "https")+"/api/sfu/leg/"+leg+"/events", h)
	if err != nil {
		return nil, nil
	}
	ch := make(chan evMsgT, 256)
	go func() {
		defer close(ch)
		for {
			_, b, err := c.ReadMessage()
			if err != nil {
				return
			}
			var m evMsgT
			if json.Unmarshal(b, &m) == nil {
				m.at = time.Now()
				ch <- m
			}
		}
	}()
	t.Cleanup(func() { c.Close() })
	return c, ch
}

func evWait(t *testing.T, ch chan evMsgT, d time.Duration, ok func(evMsgT) bool) (evMsgT, bool) {
	t.Helper()
	end := time.After(d)
	for {
		select {
		case m, more := <-ch:
			if !more {
				return evMsgT{}, false
			}
			if ok(m) {
				return m, true
			}
		case <-end:
			return evMsgT{}, false
		}
	}
}

// TestSFUEvents (P5): Ereigniskanal je Bein - hello mit allen Teilnehmenden und CSRCs, Beitritt eines weiteren Geraets
// sofort als "peers" (bevor es Medien schickt), Belegung als "layout", ping/pong, Leeren (state draining, neue Beine 429),
// Bein vom Knoten beendet ("closed"), fremde Herkunft abgelehnt, unbekanntes Bein 404. Dazu peers in POST /api/sfu/leg.
func TestSFUEvents(t *testing.T) {
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	bs := startBots(t, base, "ev", []string{"tcp"}, 4, "l", "m")
	b0 := bs[0]
	video := b0.legIDs[1]
	if _, ch := evDial(t, base, video, "https://evil.example"); ch != nil {
		t.Fatal("fremde Herkunft angenommen")
	}
	if _, ch := evDial(t, base, "0000000000000000", ""); ch != nil {
		t.Fatal("unbekanntes Bein angenommen")
	}
	_, ch := evDial(t, base, video, "")
	if ch == nil {
		t.Fatal("ereigniskanal")
	}
	h, ok := evWait(t, ch, 3*time.Second, func(m evMsgT) bool { return m.T == "hello" })
	if !ok || h.Leg != video || len(h.Peers) != 1 || h.Peers["bot0"] == 0 || h.State != "ok" || len(h.Slots) != 5 {
		t.Fatalf("hello %+v", h)
	}
	// Beitritt: peers kommt beim Anlegen des Beins, also bevor bot1 verbunden ist und sendet
	b1 := &sfuBot{peer: "bot1", path: "tcp", video: true, layers: []string{"l", "m"}}
	b1.level.Store(127)
	api, _ := sfuBotAPI("tcp")
	t0 := time.Now()
	if err := b1.join(hc, base, api, "ev", 4, "desktop"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(b1.close)
	p, ok := evWait(t, ch, 3*time.Second, func(m evMsgT) bool { return m.T == "peers" && len(m.Peers) == 2 })
	if !ok || p.Peers["bot1"] == 0 || p.Peers["bot0"] != h.Peers["bot0"] {
		t.Fatalf("peers %+v", p)
	}
	au, vi := b0.seen()
	if au[p.Peers["bot1"]] != 0 || vi[p.Peers["bot1"]] != 0 {
		t.Fatal("medien von bot1 vor der peers-meldung")
	}
	t.Logf("peers %v nach dem Beitritt", p.at.Sub(t0))
	for i := 0; i < 100 && b1.connected() != len(b1.pcs); i++ {
		time.Sleep(100 * time.Millisecond)
	}
	b1.start()
	// Belegung: bot1 bekommt bei bot0 einen Bildplatz; die CSRC dort ist die aus peers
	l, ok := evWait(t, ch, 8*time.Second, func(m evMsgT) bool {
		for _, s := range m.Slots {
			if s["peer"] == "bot1" {
				return m.T == "layout"
			}
		}
		return false
	})
	if !ok {
		t.Fatal("layout mit bot1 fehlt")
	}
	for _, sl := range l.Slots {
		if sl["peer"] == "bot1" && uint32(sl["csrc"].(float64)) != p.Peers["bot1"] {
			t.Fatalf("csrc %v != %v", sl["csrc"], p.Peers["bot1"])
		}
	}
	// ping/pong
	c2, ch2 := evDial(t, base, b1.legIDs[0], "")
	if ch2 == nil {
		t.Fatal("ereigniskanal bot1")
	}
	if _, ok := evWait(t, ch2, 3*time.Second, func(m evMsgT) bool { return m.T == "hello" }); !ok {
		t.Fatal("hello bot1")
	}
	tp := time.Now()
	_ = c2.WriteMessage(websocket.TextMessage, []byte(`{"t":"ping","n":7}`))
	if m, ok := evWait(t, ch2, 3*time.Second, func(m evMsgT) bool { return m.T == "pong" }); !ok || m.N != 7 {
		t.Fatalf("pong %+v", m)
	} else {
		t.Logf("ping/pong %v", m.at.Sub(tp))
	}
	// POST /api/sfu/leg meldet peers und state
	var tok struct{ Token string }
	if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": "ev", "peer": "bot2", "role": "audio"}, &tok); err != nil {
		t.Fatal(err)
	}
	// Leeren: state an alle, neue Beine 429
	s.setDraining(true)
	if m, ok := evWait(t, ch, 3*time.Second, func(m evMsgT) bool { return m.T == "state" }); !ok || m.State != "draining" {
		t.Fatalf("state %+v", m)
	}
	if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "tcp", Offer: "v=0"}, &struct{}{}); err == nil || !contains(err.Error(), "429") {
		t.Fatalf("neues bein beim leeren: %v", err)
	}
	s.setDraining(false)
	if m, ok := evWait(t, ch, 3*time.Second, func(m evMsgT) bool { return m.T == "state" }); !ok || m.State != "ok" {
		t.Fatalf("state %+v", m)
	}
	// Raum geschlossen: closed an das Geraet, danach ist der Kanal zu
	req, _ := http.NewRequest(http.MethodDelete, base+"/api/dev/room/ev", nil)
	if resp, err := hc.Do(req); err == nil {
		resp.Body.Close()
	}
	if m, ok := evWait(t, ch, 3*time.Second, func(m evMsgT) bool { return m.T == "closed" }); !ok || m.Why == "" {
		t.Fatalf("closed %+v", m)
	}
	if _, ok := evWait(t, ch, 3*time.Second, func(evMsgT) bool { return false }); ok {
		t.Fatal("kanal offen")
	}
}

// TestSFULegPeers (P5): die Antwort auf POST /api/sfu/leg traegt alle Teilnehmenden mit CSRC und den Zustand.
func TestSFULegPeers(t *testing.T) {
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	bs := startBots(t, base, "lp", []string{"tcp", "tcp"}, 2, "l")
	ids := roomCSRCs(t, base, "lp")
	hc := httpClient()
	var tok struct{ Token string }
	if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": "lp", "peer": "bot9", "role": "audio"}, &tok); err != nil {
		t.Fatal(err)
	}
	api, _ := sfuBotAPI("tcp")
	pc, _ := api.NewPeerConnection(webrtcConfig())
	defer pc.Close()
	offer := audioOffer(t, pc)
	var ans struct {
		ID    string
		Peers map[string]uint32
		State string
		CSRC  uint32
	}
	if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "tcp", Offer: offer, Audio: 3}, &ans); err != nil {
		t.Fatal(err)
	}
	if ans.State != "ok" || len(ans.Peers) != 3 || ans.Peers["bot0"] != ids["bot0"] || ans.Peers["bot1"] != ids["bot1"] || ans.Peers["bot9"] != ans.CSRC {
		t.Fatalf("antwort %+v (raum %v)", ans, ids)
	}
	_ = bs
}

// TestRoomWithoutCtl (P5): ein Knoten mit festem Saga-Schluessel (HORN_SAGA_PUB, ohne Steuerkanal) legt den Raum fuer
// ein gueltiges Saga-Token selbst an; gefaelschte Token bleiben draussen, ein geschlossener Raum bleibt gesperrt.
func TestRoomWithoutCtl(t *testing.T) {
	s := testNodeServer(t, false)
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	_, other, _ := ed25519.GenerateKey(rand.Reader)
	s.verifier.SetKey(pub)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	tok := func(k ed25519.PrivateKey, jti string) string {
		return signTok(k, tokBody(map[string]any{"node": "localhost", "jti": jti, "call_ref": "CallRefOhneCtl01"}))
	}
	if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": tok(other, "jti-fremd-000001")}, &struct{}{}); err == nil || !contains(err.Error(), "401") {
		t.Fatalf("fremder schluessel: %v", err)
	}
	if s.node.Room("CallRefOhneCtl01") != nil {
		t.Fatal("raum fuer gefaelschtes token")
	}
	var cr struct{ Username string }
	if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": tok(sk, "jti-echt-0000001")}, &cr); err != nil || cr.Username == "" {
		t.Fatalf("saga-token ohne steuerkanal: %v", err)
	}
	if s.node.Room("CallRefOhneCtl01") == nil {
		t.Fatal("raum nicht angelegt")
	}
	s.closeRoom("CallRefOhneCtl01", "test")
	if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": tok(sk, "jti-echt-0000002")}, &struct{}{}); err == nil || !contains(err.Error(), "401") {
		t.Fatalf("geschlossener raum: %v", err)
	}
}
