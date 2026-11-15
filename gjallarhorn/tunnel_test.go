package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/turn/v5"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// tunnelServer: Knoten (Dev) mit seinen Routen ueber Klartext-HTTP (wie hinter saga-media) -> Server, ws-URL.
func tunnelServer(t *testing.T) (*server, *httptest.Server, string) {
	t.Helper()
	s := testNodeServer(t, true)
	hs := httptest.NewServer(s.routes())
	t.Cleanup(hs.Close)
	return s, hs, "ws" + strings.TrimPrefix(hs.URL, "http") + "/api/sfu/ws"
}

func tunnelCreds(t *testing.T, s *server) (string, string) {
	t.Helper()
	u, c, err := turn.GenerateLongTermCredentials(s.turnSecret, 10*time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	return u, c
}

func dialTunnel(t *testing.T, url string, protos ...string) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	if protos == nil {
		protos = []string{wstun.Subprotocol}
	}
	d := websocket.Dialer{Subprotocols: protos, HandshakeTimeout: 5 * time.Second}
	return d.Dial(url, nil)
}

// readErr: Antwort err und Close-Code.
func readErr(t *testing.T, ws *websocket.Conn) (wstun.Reply, int) {
	t.Helper()
	_ = ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	var r wstun.Reply
	mt, b, err := ws.ReadMessage()
	if err != nil {
		t.Fatalf("keine Antwort: %v", err)
	}
	if mt != websocket.TextMessage || json.Unmarshal(b, &r) != nil {
		t.Fatalf("Antwort %d %q", mt, b)
	}
	_, _, err = ws.ReadMessage()
	ce, _ := err.(*websocket.CloseError)
	if ce == nil {
		t.Fatalf("kein Close nach err: %v", err)
	}
	return r, ce.Code
}

// Vertrag 2: ohne Upgrade 426 mit Hinweis - vor jeder anderen Pruefung (auch ohne Unterprotokoll und Zugang).
func TestTunnel426OhneUpgrade(t *testing.T) {
	s, hs, _ := tunnelServer(t)
	resp, err := http.Get(hs.URL + "/api/sfu/ws")
	if err != nil {
		t.Fatal(err)
	}
	var j map[string]string
	_ = json.NewDecoder(resp.Body).Decode(&j)
	resp.Body.Close()
	if resp.StatusCode != http.StatusUpgradeRequired || j["error"] != "websocket_required" || !strings.Contains(j["text"], "Websockets Support") ||
		resp.Header.Get("Upgrade") != "websocket" {
		t.Fatalf("%d %v %v", resp.StatusCode, j, resp.Header)
	}
	// wie NPM ohne "Websockets Support": Upgrade/Connection fehlen, Sec-WebSocket-* kommen trotzdem an
	r, _ := http.NewRequest("GET", hs.URL+"/api/sfu/ws", nil)
	r.Header.Set("Sec-WebSocket-Key", "dGhlIHNhbXBsZSBub25jZQ==")
	r.Header.Set("Sec-WebSocket-Version", "13")
	r.Header.Set("Sec-WebSocket-Protocol", "saga-tunnel.1")
	resp, err = http.DefaultClient.Do(r)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 426 {
		t.Fatalf("ohne Upgrade-Kopf: %d", resp.StatusCode)
	}
	if st := s.tun.stats(); st.NoUpgrade != 2 || st.LastNoUpgrade == 0 {
		t.Fatalf("Zaehler: %+v", st)
	}
}

// saga-frames.1 (Browser-Weg B2) ist reserviert: sauber 400, kein 101.
func TestTunnelUnterprotokoll(t *testing.T) {
	_, _, url := tunnelServer(t)
	for _, p := range [][]string{{"saga-frames.1"}, {"chat"}, {}} {
		ws, resp, err := dialTunnel(t, url, p...)
		if err == nil {
			ws.Close()
			t.Fatalf("%v: angenommen", p)
		}
		var j map[string]any
		if resp != nil {
			_ = json.NewDecoder(resp.Body).Decode(&j)
		}
		if resp == nil || resp.StatusCode != 400 || j["error"] != "subprotocol" {
			t.Fatalf("%v: %v %v", p, resp, j)
		}
	}
	ws, resp, err := dialTunnel(t, url, "saga-frames.1", wstun.Subprotocol)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	if resp.Header.Get("Sec-WebSocket-Protocol") != wstun.Subprotocol {
		t.Fatalf("Unterprotokoll %q", resp.Header.Get("Sec-WebSocket-Protocol"))
	}
}

// Zugang: nur die erste Textnachricht; Fehler als err mit Code und Close-Code.
func TestTunnelZugang(t *testing.T) {
	s, _, url := tunnelServer(t)
	user, cred := tunnelCreds(t, s)
	exp := strconv.FormatInt(time.Now().Add(-time.Minute).Unix(), 10) // abgelaufen
	for _, c := range []struct {
		name string
		msg  any
		bin  bool
		code string
		cc   int
	}{
		{"falsch", wstun.Auth{T: "auth", V: 1, Mode: "ice", User: user, Cred: "AAAA"}, false, "auth", wstun.CloseAuth},
		{"abgelaufen", wstun.Auth{T: "auth", V: 1, Mode: "ice", User: exp, Cred: cred}, false, "auth", wstun.CloseAuth},
		{"turn", wstun.Auth{T: "auth", V: 1, Mode: "turn", User: user, Cred: cred}, false, "mode", wstun.CloseBad},
		{"version", wstun.Auth{T: "auth", V: 2, Mode: "ice", User: user, Cred: cred}, false, "version", wstun.CloseBad},
		{"binaer vor auth", []byte{0, 1, 2}, true, "bad", wstun.CloseBad},
		{"kein json", "hallo", false, "bad", wstun.CloseBad},
	} {
		ws, _, err := dialTunnel(t, url)
		if err != nil {
			t.Fatal(err)
		}
		switch m := c.msg.(type) {
		case []byte:
			_ = ws.WriteMessage(websocket.BinaryMessage, m)
		case string:
			_ = ws.WriteMessage(websocket.TextMessage, []byte(m))
		default:
			_ = ws.WriteJSON(m)
		}
		r, cc := readErr(t, ws)
		ws.Close()
		if r.T != "err" || r.Code != c.code || cc != c.cc || r.V != 1 || (c.code == "version" && r.Max != 1) {
			t.Errorf("%s: %+v close %d", c.name, r, cc)
		}
	}
	if st := s.tun.stats(); st.AuthFail != 2 || st.Total != 0 {
		t.Fatalf("Zaehler: %+v", st)
	}
	// richtig: ok mit Quittungswerten
	ws, _, err := dialTunnel(t, url)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	r, err := wstun.ClientAuth(ws, user, cred, "audio", 5*time.Second)
	if err != nil || r.AckBytes != wstun.DefAckBytes || r.AckMs != 15 || r.V != 1 {
		t.Fatalf("%+v %v", r, err)
	}
}

// Fristen: keine auth in tunnelAuthWait -> timeout; nach ok kein Datenbyte in tunnelFirstWait -> timeout.
func TestTunnelFristen(t *testing.T) {
	a, f := tunnelAuthWait, tunnelFirstWait
	tunnelAuthWait, tunnelFirstWait = 300*time.Millisecond, 400*time.Millisecond
	t.Cleanup(func() { tunnelAuthWait, tunnelFirstWait = a, f })
	s, _, url := tunnelServer(t)
	ws, _, err := dialTunnel(t, url)
	if err != nil {
		t.Fatal(err)
	}
	r, cc := readErr(t, ws)
	ws.Close()
	if r.Code != "timeout" || cc != wstun.CloseTimeout {
		t.Fatalf("auth-Frist: %+v %d", r, cc)
	}
	user, cred := tunnelCreds(t, s)
	ws, _, err = dialTunnel(t, url)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	if _, err := wstun.ClientAuth(ws, user, cred, "", 5*time.Second); err != nil {
		t.Fatal(err)
	}
	if n := s.tun.stats().Open; n != 1 {
		t.Fatalf("%d offen", n)
	}
	r, cc = readErr(t, ws)
	if r.Code != "timeout" || cc != wstun.CloseTimeout {
		t.Fatalf("Datenfrist: %+v %d", r, cc)
	}
	time.Sleep(50 * time.Millisecond)
	if n := s.tun.stats().Open; n != 0 {
		t.Fatalf("%d noch offen", n)
	}
}

// Grenze je Client-Adresse (4 x HORN_MAX_PER_IP) als 429 vor dem Upgrade; andere Adressen nicht betroffen.
func TestTunnelGrenzeJeAdresse(t *testing.T) {
	s, _, url := tunnelServer(t)
	s.cfg.MaxPerIP = 1 // -> 4 Tunnel je Adresse
	var open []*websocket.Conn
	defer func() {
		for _, w := range open {
			w.Close()
		}
	}()
	for i := 0; i < 4; i++ {
		ws, _, err := dialTunnel(t, url)
		if err != nil {
			t.Fatalf("%d: %v", i, err)
		}
		open = append(open, ws)
	}
	_, resp, err := dialTunnel(t, url)
	if err == nil || resp == nil || resp.StatusCode != 429 {
		t.Fatalf("5. Tunnel: %v %v", err, resp)
	}
	// andere Adresse (internes HTTP von saga-media: X-Saga-Einport-For mit Schluessel)
	r := httptest.NewRequest("GET", "/api/sfu/ws", nil)
	r.RemoteAddr = "203.0.113.7:1234"
	if s.tun.perIP[ipKey(r)] != 0 {
		t.Fatal("fremde Adresse gezaehlt")
	}
	open[0].Close()
	open = open[1:]
	time.Sleep(100 * time.Millisecond) // Knoten merkt das Ende (Lesefehler im Handschlag)
	ws, _, err := dialTunnel(t, url)
	if err != nil {
		t.Fatalf("nach Freigabe: %v", err)
	}
	open = append(open, ws)
	if st := s.tun.stats(); st.Limited != 1 {
		t.Fatalf("Zaehler: %+v", st)
	}
}

// bendTo: passiver TCP-Kandidat im Antwort-SDP -> 127.0.0.1:<port> (wie HornTunnel.bend in den Apps).
func bendTo(sdp string, port int) string {
	lines := strings.Split(sdp, "\r\n")
	for i, l := range lines {
		f := strings.Fields(l)
		if strings.HasPrefix(l, "a=candidate:") && len(f) >= 8 && strings.EqualFold(f[2], "tcp") && f[7] == "host" && strings.Contains(l, "tcptype passive") {
			f[4], f[5] = "127.0.0.1", strconv.Itoa(port)
			lines[i] = strings.Join(f, " ")
		}
	}
	return strings.Join(lines, "\r\n")
}

// appTunnel: lokaler Lauscher je Leitung (maxConns 1) -> WebSocket mit auth, ohne Fenster, mit Quittungen (wie die Apps).
func appTunnel(t *testing.T, url, user, cred, role string) int {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	// WebSocket vorab oeffnen (Vertrag 7)
	ws, _, err := dialTunnel(t, url)
	if err != nil {
		t.Fatal(err)
	}
	r, err := wstun.ClientAuth(ws, user, cred, role, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	c := wstun.New(ws, nil, nil, wstun.Options{Ack: true, AckBytes: r.AckBytes, AckDelay: time.Duration(r.AckMs) * time.Millisecond})
	t.Cleanup(func() { c.Close() })
	go func() {
		lc, err := ln.Accept()
		ln.Close() // maxConns 1
		if err != nil {
			return
		}
		go func() { _, _ = io.Copy(lc, c); lc.Close() }()
		_, _ = io.Copy(c, lc)
		c.Close()
	}()
	return ln.Addr().(*net.TCPAddr).Port
}

// Ende zu Ende: zwei Geraete (pion) ueber den Tunnel (Bein-Weg ws), auch wenn der Knoten kein oeffentliches ICE-TCP
// anbietet (Tunnel-Betrieb): "tcp" wird dann sofort abgelehnt, "ws" geht. Zaehler im Bein und in der Lastmeldung.
func TestTunnelEndeZuEnde(t *testing.T) {
	s, hs, url := tunnelServer(t)
	s.iceTCP = false
	user, cred := tunnelCreds(t, s)
	hc := httpClient()
	room := "tunnel-e2e"
	if err := postJSON(hc, hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	// "tcp" ohne oeffentliches ICE-TCP: sofort abgelehnt
	var tok struct{ Token string }
	if err := postJSON(hc, hs.URL+"/api/dev/token", map[string]any{"room": room, "peer": "x", "role": "audio"}, &tok); err != nil {
		t.Fatal(err)
	}
	if err := postJSON(hc, hs.URL+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "tcp", Offer: "v=0"}, &struct{}{}); err == nil ||
		!strings.Contains(err.Error(), "nicht verfuegbar") {
		t.Fatalf("tcp ohne ICE-TCP: %v", err)
	}
	var bots sfuBots
	defer func() { bots.close() }()
	for i := 0; i < 2; i++ {
		api, err := sfuBotAPI("ws")
		if err != nil {
			t.Fatal(err)
		}
		b := &sfuBot{peer: "ws" + strconv.Itoa(i), path: "ws", video: true, layers: []string{"l", "m"}}
		b.level.Store(127)
		b.bend = func(role, sdp string) string { return bendTo(sdp, appTunnel(t, url, user, cred, role)) }
		if err := b.join(hc, hs.URL, api, room, 1, "desktop"); err != nil {
			t.Fatal(err)
		}
		bots = append(bots, b)
	}
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		c := 0
		for _, b := range bots {
			c += b.connected()
		}
		if c == 4 {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	for _, b := range bots {
		if b.connected() != 2 {
			t.Fatalf("%s: %d von 2 Leitungen verbunden", b.peer, b.connected())
		}
		b.start()
	}
	time.Sleep(2 * time.Second)
	for _, b := range bots {
		if b.rxPkts.Load() < 50 {
			t.Fatalf("%s: nur %d Pakete empfangen", b.peer, b.rxPkts.Load())
		}
	}
	st := s.tun.stats()
	if st.Open != 4 || st.Total != 4 || st.WinKiB < 64 {
		t.Fatalf("Tunnel-Zaehler: %+v", st)
	}
	var lay struct {
		Stats legStats `json:"stats"`
	}
	resp, err := hc.Get(hs.URL + "/api/sfu/leg/" + bots[0].legIDs[1])
	if err != nil {
		t.Fatal(err)
	}
	_ = json.NewDecoder(resp.Body).Decode(&lay)
	resp.Body.Close()
	if lay.Stats.Path != "ws" || lay.Stats.Tunnel == nil || lay.Stats.Tunnel.Tx == 0 || lay.Stats.Tunnel.AcksRx == 0 {
		t.Fatalf("Bein-Diagnose: %+v %+v", lay.Stats.Path, lay.Stats.Tunnel)
	}
}

// TestA3Kette (nur mit A3_URL, aus tests/a3_tunnel.py): zwei Geraete mit Medien-Token von Saga ueber die echte Kette
// NPM-Nachbau (nginx, TLS) -> saga-media -> node.sock -> eingebauter Knoten im Tunnel-Betrieb, Bein-Weg ws.
// A3_URL = Saga-Adresse (z. B. https://saga.example.org:8521), A3_DIAL = Ziel der TCP-Verbindungen (nginx),
// A3_TOKENS = JSON-Datei [{"audio": "h1…", "video": "h1…"}, …], A3_SECS = Dauer.
func TestA3Kette(t *testing.T) {
	base, dial, tf := os.Getenv("A3_URL"), os.Getenv("A3_DIAL"), os.Getenv("A3_TOKENS")
	if base == "" {
		t.Skip("nur mit A3_URL (tests/a3_tunnel.py)")
	}
	var devs []map[string]string
	b, err := os.ReadFile(tf)
	if err != nil || json.Unmarshal(b, &devs) != nil || len(devs) < 2 {
		t.Fatalf("A3_TOKENS: %v", err)
	}
	dialer := func(ctx context.Context, network, _ string) (net.Conn, error) {
		var d net.Dialer
		return d.DialContext(ctx, network, dial)
	}
	tlsc := &tls.Config{InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}} // nur Pruefstand (selbst signiert)
	hc := &http.Client{Timeout: 15 * time.Second, Transport: &http.Transport{Proxy: nil, TLSClientConfig: tlsc, DialContext: dialer}}
	wsURL := "wss" + strings.TrimPrefix(base, "https") + "/api/sfu/ws"
	tunnel := func(user, cred, role string) int {
		ln, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { ln.Close() })
		d := websocket.Dialer{Subprotocols: []string{wstun.Subprotocol}, TLSClientConfig: tlsc, NetDialContext: dialer, HandshakeTimeout: 10 * time.Second}
		ws, resp, err := d.Dial(wsURL, nil)
		if err != nil {
			t.Fatalf("WebSocket ueber den Proxy: %v %v", err, resp)
		}
		r, err := wstun.ClientAuth(ws, user, cred, role, 10*time.Second)
		if err != nil {
			t.Fatalf("auth: %v", err)
		}
		c := wstun.New(ws, nil, nil, wstun.Options{Ack: true, AckBytes: r.AckBytes, AckDelay: time.Duration(r.AckMs) * time.Millisecond})
		t.Cleanup(func() { c.Close() })
		go func() {
			lc, err := ln.Accept()
			ln.Close()
			if err != nil {
				return
			}
			go func() { _, _ = io.Copy(lc, c); lc.Close() }()
			_, _ = io.Copy(c, lc)
			c.Close()
		}()
		return ln.Addr().(*net.TCPAddr).Port
	}
	var bots sfuBots
	defer func() { bots.close() }()
	for i, tk := range devs[:2] {
		var cr struct{ Username, Credential string }
		if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": tk["audio"]}, &cr); err != nil {
			t.Fatalf("TURN-Zugang: %v", err)
		}
		api, err := sfuBotAPI("ws")
		if err != nil {
			t.Fatal(err)
		}
		b := &sfuBot{peer: "a3-" + strconv.Itoa(i), path: "ws", video: true, layers: []string{"l", "m"}, toks: tk}
		b.level.Store(127)
		b.bend = func(role, sdp string) string { return bendTo(sdp, tunnel(cr.Username, cr.Credential, role)) }
		if err := b.join(hc, base, api, "", 1, "desktop"); err != nil {
			t.Fatalf("Beitritt %d: %v", i, err)
		}
		bots = append(bots, b)
	}
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) && bots[0].connected()+bots[1].connected() < 4 {
		time.Sleep(100 * time.Millisecond)
	}
	for _, b := range bots {
		if b.connected() != 2 {
			t.Fatalf("%s: %d von 2 Leitungen", b.peer, b.connected())
		}
		b.start()
	}
	secs, _ := strconv.Atoi(os.Getenv("A3_SECS"))
	if secs <= 0 {
		secs = 5
	}
	time.Sleep(time.Duration(secs) * time.Second)
	for _, b := range bots {
		t.Logf("%s: %d Pakete, %d verloren", b.peer, b.rxPkts.Load(), b.rxLost.Load())
		if b.rxPkts.Load() < uint64(50*secs) {
			t.Fatalf("%s: nur %d Pakete", b.peer, b.rxPkts.Load())
		}
	}
	var lay struct {
		Stats legStats `json:"stats"`
	}
	resp, err := hc.Get(base + "/api/sfu/leg/" + bots[0].legIDs[1])
	if err != nil {
		t.Fatal(err)
	}
	_ = json.NewDecoder(resp.Body).Decode(&lay)
	resp.Body.Close()
	if lay.Stats.Path != "ws" || lay.Stats.Tunnel == nil || lay.Stats.Tunnel.AcksRx == 0 {
		t.Fatalf("Bein-Diagnose: %q %+v", lay.Stats.Path, lay.Stats.Tunnel)
	}
	t.Logf("Bild-Bein: Fenster %d, RTT %d us, gesendet %d", lay.Stats.Tunnel.Window, lay.Stats.Tunnel.RTTus, lay.Stats.Tunnel.Tx)
}
