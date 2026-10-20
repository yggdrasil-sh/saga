package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

func testServer(t *testing.T, pm netx.ProxyMode) *server {
	t.Helper()
	cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", UDP: true, UDPListen: "127.0.0.1:0", Data: t.TempDir(),
		Proxy: pm, ICEIPs: []string{"127.0.0.1"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 16,
		Certs: netx.CertOptions{Name: "localhost", Self: true}}
	s, err := newServer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.shutdown)
	return s
}

func httpClient() *http.Client {
	return &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{Proxy: nil,
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}} // Test gegen eigenes, selbst signiertes Zertifikat
}

// miniProxy setzt vor jede Verbindung einen PROXY-v2-Kopf (Client 192.0.2.77).
func miniProxy(t *testing.T, target string) string {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				u, err := net.Dial("tcp", target)
				if err != nil {
					return
				}
				defer u.Close()
				src := &net.TCPAddr{IP: net.ParseIP("192.0.2.77"), Port: 4242}
				_, _ = u.Write(netx.BuildProxyV2(src, u.RemoteAddr().(*net.TCPAddr)))
				go func() { _, _ = io.Copy(u, c) }()
				_, _ = io.Copy(c, u)
			}()
		}
	}()
	return ln.Addr().String()
}

// mirrorBot verbindet sich ueber path und zaehlt die gespiegelten Pakete.
func mirrorBot(t *testing.T, base, path string) (rx *atomic.Uint64, stop func()) {
	t.Helper()
	api, err := botAPI(path)
	if err != nil {
		t.Fatal(err)
	}
	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		t.Fatal(err)
	}
	b := &bot{id: "testbot-" + randHex(4), stop: make(chan struct{})}
	for _, k := range []string{"a", "l", "h"} {
		c := webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}
		if k != "a" {
			c = webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000}
		}
		tr, _ := webrtc.NewTrackLocalStaticRTP(c, k, b.id)
		if _, err := pc.AddTransceiverFromTrack(tr, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly}); err != nil {
			t.Fatal(err)
		}
		s := &botStream{tr: tr, kind: k}
		s.key.Store(true)
		b.streams = append(b.streams, s)
	}
	for i := 0; i < 3; i++ {
		_, _ = pc.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly})
	}
	for i := 0; i < 2; i++ {
		_, _ = pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly})
	}
	rx = &atomic.Uint64{}
	pc.OnTrack(func(tr *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		buf := make([]byte, 1500)
		for {
			if _, _, err := tr.Read(buf); err != nil {
				return
			}
			rx.Add(1)
		}
	})
	offer, _ := pc.CreateOffer(nil)
	g := webrtc.GatheringCompletePromise(pc)
	_ = pc.SetLocalDescription(offer)
	<-g
	body, _ := json.Marshal(legReq{SID: b.id, Path: path, Role: "both", Audio: 3, Video: 2, Offer: pc.LocalDescription().SDP})
	resp, err := httpClient().Post(base+"/api/leg", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	var ans struct{ ID, Answer, Error string }
	_ = json.NewDecoder(resp.Body).Decode(&ans)
	resp.Body.Close()
	if ans.Answer == "" {
		t.Fatalf("leg: %s", ans.Error)
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
		t.Fatal(err)
	}
	if path == "tcp" && !strings.Contains(ans.Answer, "tcptype passive") {
		t.Fatal("keine passiven TCP-Kandidaten in der Antwort")
	}
	b.pcs = []*webrtc.PeerConnection{pc}
	b.start()
	return rx, b.close
}

func waitConnected(t *testing.T, s *server, n int) {
	t.Helper()
	for i := 0; i < 100; i++ {
		c := 0
		s.mu.Lock()
		for _, l := range s.legs {
			if l.state() == webrtc.PeerConnectionStateConnected {
				c++
			}
		}
		s.mu.Unlock()
		if c >= n {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("bein nicht verbunden")
}

func waitPackets(t *testing.T, rx *atomic.Uint64, n uint64) {
	t.Helper()
	for i := 0; i < 100; i++ {
		if rx.Load() >= n {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("zu wenig gespiegelte pakete: %d < %d", rx.Load(), n)
}

func TestICETCPMirror(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	base := "https://" + s.mux.Addr().String()
	rx, stop := mirrorBot(t, base, "tcp")
	defer stop()
	waitConnected(t, s, 1)
	// 3 x Ton (25/s) + Sprecher h (150/s) + Kachel l (30/s), grob
	waitPackets(t, rx, 400)
	if s.mux.Stats.ICETCP.Load() == 0 {
		t.Fatal("keine ICE-TCP-Verbindung ueber die Weiche")
	}
}

func TestUDPMirror(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	rx, stop := mirrorBot(t, "https://"+s.mux.Addr().String(), "udp")
	defer stop()
	waitConnected(t, s, 1)
	waitPackets(t, rx, 400)
}

func turnClient(t *testing.T, addr string, s *server) *turn.Client {
	t.Helper()
	conn, err := tls.Dial("tcp", addr, &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"})
	if err != nil {
		t.Fatal(err)
	}
	user, pass, _ := turn.GenerateLongTermCredentials(s.turnSecret, time.Minute)
	c, err := turn.NewClient(&turn.ClientConfig{TURNServerAddr: addr, Username: user, Password: pass, Realm: "localhost", Conn: turn.NewSTUNConn(conn)})
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Listen(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(c.Close)
	return c
}

func TestTURNOnlySelf(t *testing.T) {
	s := testServer(t, netx.ProxyAuto)
	for _, addr := range []string{s.mux.Addr().String(), miniProxy(t, s.mux.Addr().String())} {
		c := turnClient(t, addr, s)
		relay, err := c.Allocate()
		if err != nil {
			t.Fatalf("%s: allocate: %v", addr, err)
		}
		if !strings.HasPrefix(relay.LocalAddr().String(), "127.0.0.1:") {
			t.Fatalf("relay-adresse %v", relay.LocalAddr())
		}
		// zu sich selbst: erlaubt
		if _, err := relay.WriteTo([]byte("x"), &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9}); err != nil {
			t.Fatalf("permission loopback: %v", err)
		}
		// fremde Adresse: verboten (kein offenes Relay)
		if _, err := relay.WriteTo([]byte("x"), &net.UDPAddr{IP: net.ParseIP("192.0.2.1"), Port: 9}); err == nil {
			t.Fatal("relay zu fremder adresse erlaubt")
		}
		relay.Close()
	}
	// falsches Passwort: keine Allocation
	conn, _ := tls.Dial("tcp", s.mux.Addr().String(), &tls.Config{InsecureSkipVerify: true})
	c, _ := turn.NewClient(&turn.ClientConfig{TURNServerAddr: s.mux.Addr().String(), Username: "9999999999", Password: "falsch", Realm: "localhost", Conn: turn.NewSTUNConn(conn)})
	_ = c.Listen()
	defer c.Close()
	if _, err := c.Allocate(); err == nil {
		t.Fatal("allocation ohne gueltige zugangsdaten")
	}
}

func TestProxyProtocolAuto(t *testing.T) {
	s := testServer(t, netx.ProxyAuto)
	px := miniProxy(t, s.mux.Addr().String())
	hc := httpClient()
	for _, a := range []string{s.mux.Addr().String(), px} {
		resp, err := hc.Get("https://" + a + "/healthz")
		if err != nil {
			t.Fatalf("%s: %v", a, err)
		}
		resp.Body.Close()
	}
	// ueber den Proxy sieht der Knoten die Client-Adresse aus dem Kopf (nur im Speicher)
	rx, stop := mirrorBot(t, "https://"+px, "tcp")
	defer stop()
	_ = rx
	s.mu.Lock()
	n := s.perIP["192.0.2.77"]
	s.mu.Unlock()
	if n != 1 {
		t.Fatalf("client-adresse aus proxy-kopf nicht erkannt: %v", s.perIP)
	}
}

func TestResultsNoAddresses(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	base := "https://" + s.mux.Addr().String()
	in := `{"v":1,"net":"Test","phases":[{"path":"tcp","ok":true,"cand":{"protocol":"tcp","address":"192.0.2.9","port":5000,"ip":"192.0.2.9"}}],"url":"x"}`
	resp, err := httpClient().Post(base+"/api/results", "application/json", strings.NewReader(in))
	if err != nil {
		t.Fatal(err)
	}
	var r struct{ File string }
	_ = json.NewDecoder(resp.Body).Decode(&r)
	resp.Body.Close()
	b, err := os.ReadFile(filepath.Join(s.cfg.Data, "results", r.File))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(b, []byte("192.0.2.9")) || bytes.Contains(b, []byte(`"url"`)) {
		t.Fatalf("adresse gespeichert: %s", b)
	}
	if !bytes.Contains(b, []byte(`"protocol": "tcp"`)) {
		t.Fatalf("messwert fehlt: %s", b)
	}
	// Bericht laeuft ueber die gespeicherten Daten
	var out bytes.Buffer
	if err := runReport([]string{"--data", s.cfg.Data}, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "Go/No-Go") {
		t.Fatal("bericht ohne bewertung")
	}
}

func TestReportVerdicts(t *testing.T) {
	f := func(x float64) *float64 { return &x }
	ok := pPhase{Path: "tcp", Profile: "v12", OK: true}
	ok.RTT.P95 = f(120)
	ok.Down.Stable = 3500
	ok.Audio = &struct {
		Conceal *float64 `json:"conceal_pct"`
		JB      *float64 `json:"jb_ms"`
		Loss    *float64 `json:"loss_pct"`
	}{Conceal: f(1.2)}
	rs := []pResult{{Net: "LTE", Device: "Handy", Browser: "Chrome", OS: "Android", Phases: []pPhase{ok}}}
	bad := ok
	bad.RTT.P95 = f(400)
	rs = append(rs, pResult{Net: "Firma", Device: "Laptop", Browser: "Firefox", OS: "Linux", Phases: []pPhase{bad}})
	gs := evaluateGo(rs, []benchResult{{N: 25, Video: 12, CPUusPkt: 8, RAMPartMB: 3}})
	got := map[string]verdict{}
	for _, g := range gs {
		got[g.id] = g.v
	}
	if got["G2"] != vNoGo || got["G3"] != vGo || got["G5"] != vGo || got["G7"] != vGo {
		t.Fatalf("bewertung %v", got)
	}
	if !strings.Contains(overall(gs), "zurückschneiden") {
		t.Fatalf("gesamt: %s", overall(gs))
	}
}

func TestKeyRequestParse(t *testing.T) {
	rr := []byte{0x80, 201, 0, 1, 0, 0, 0, 1}              // RR, Laenge 1
	pli := []byte{0x81, 206, 0, 2, 0, 0, 0, 1, 0, 0, 0, 2} // PLI
	if hasKeyRequest(rr) || !hasKeyRequest(append(rr, pli...)) {
		t.Fatal("pli-erkennung")
	}
}

func TestAddPtime(t *testing.T) {
	in := "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 VP8/90000\r\n"
	out := addPtime(in, 40)
	if strings.Count(out, "a=ptime:40") != 1 || !strings.Contains(out, "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=ptime:40\r\n") {
		t.Fatalf("ptime: %q", out)
	}
}

func TestUDPSelfTest(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	r := s.udpSelfTest()
	if r.State != "ok" || r.Via != "lokal" {
		t.Fatalf("selbsttest %+v", r)
	}
	if s.UDPCheck().State != "ok" {
		t.Fatal("nicht gespeichert")
	}
}
