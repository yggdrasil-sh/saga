package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

func testNodeServer(t *testing.T, dev bool) *server {
	t.Helper()
	cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", UDP: true, UDPListen: "127.0.0.1:0", Data: t.TempDir(),
		ICEIPs: []string{"127.0.0.1"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 32, Mode: "node", Dev: dev,
		Certs: netx.CertOptions{Name: "localhost", Self: true}}
	s, err := newServer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.shutdown)
	return s
}

type sfuBots []*sfuBot

func (bs sfuBots) close() {
	for _, b := range bs {
		b.close()
	}
}

func startBots(t *testing.T, base, room string, paths []string, tiles int, layers ...string) sfuBots {
	t.Helper()
	hc := httpClient()
	if err := postJSON(hc, base+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var bs sfuBots
	for i, p := range paths {
		api, err := sfuBotAPI(p)
		if err != nil {
			t.Fatal(err)
		}
		b := &sfuBot{peer: fmt.Sprintf("bot%d", i), path: p, video: true, layers: layers}
		b.level.Store(127)
		if err := b.join(hc, base, api, room, tiles, "desktop"); err != nil {
			bs.close()
			t.Fatal(err)
		}
		bs = append(bs, b)
	}
	t.Cleanup(bs.close)
	for i := 0; i < 100; i++ {
		c, want := 0, 0
		for _, b := range bs {
			c += b.connected()
			want += len(b.pcs)
		}
		if c == want {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	for _, b := range bs {
		if b.connected() != len(b.pcs) {
			t.Fatalf("%s (%s) nicht verbunden", b.peer, b.path)
		}
		b.start()
	}
	return bs
}

func roomCSRCs(t *testing.T, base, room string) map[string]uint32 {
	var res struct{ Peers map[string]uint32 }
	resp, err := httpClient().Get(base + "/api/dev/room/" + room)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := jsonDecode(resp, &res); err != nil {
		t.Fatal(err)
	}
	return res.Peers
}

// TestSFUMixed: drei Geraete (2x ICE-TCP mit Ton- und Bild-Leitung, 1x UDP
// mit BUNDLE) im selben Raum. Jedes hoert und sieht die anderen beiden.
func TestSFUMixed(t *testing.T) {
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	bs := startBots(t, base, "r1", []string{"tcp", "tcp", "udp"}, 4, "l", "m")
	bs[0].level.Store(20)
	bs[2].level.Store(40)
	ids := roomCSRCs(t, base, "r1")
	if len(ids) != 3 {
		t.Fatalf("teilnehmende %v", ids)
	}
	deadline := time.Now().Add(15 * time.Second)
	check := func() error {
		for _, b := range bs {
			au, vi := b.seen()
			for _, o := range bs {
				if o == b {
					continue
				}
				c := ids[o.peer]
				if au[c] < 20 {
					return fmt.Errorf("%s hoert %s nicht (%d)", b.peer, o.peer, au[c])
				}
				if vi[c] < 20 {
					return fmt.Errorf("%s sieht %s nicht (%d)", b.peer, o.peer, vi[c])
				}
			}
			if au[ids[b.peer]] != 0 || vi[ids[b.peer]] != 0 {
				return fmt.Errorf("%s bekommt sich selbst", b.peer)
			}
		}
		return nil
	}
	var err error
	for time.Now().Before(deadline) {
		if err = check(); err == nil {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	if lost := bs[0].rxLost.Load() + bs[1].rxLost.Load() + bs[2].rxLost.Load(); lost > 5 {
		t.Fatalf("luecken in folgenummern: %d", lost)
	}
	// Layout: TCP-Bein bekommt hoechstens 2 Schichten, Sprecher von bot1 ist bot0
	l := s.getLeg(bs[1].legIDs[1])
	if l == nil || l.sl == nil {
		t.Fatal("bein")
	}
	sl := l.sl.Layout()
	if sl[0].Class != "speaker" || sl[0].Peer != "bot0" {
		t.Fatalf("sprecher %+v", sl[0])
	}
}

// TestSFUAuth: ohne gueltiges Token kein Bein und kein TURN; ohne HORN_DEV
// gibt es weder Token- noch Raum-Endpunkte.
func TestSFUAuth(t *testing.T) {
	s := testNodeServer(t, false)
	base := "https://" + s.mux.Addr().String()
	hc := httpClient()
	for _, u := range []string{"/api/dev/token", "/api/dev/room"} {
		resp, err := hc.Post(base+u, "application/json", nil)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("%s ohne dev: %d", u, resp.StatusCode)
		}
	}
	err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: "x", Path: "tcp", Offer: "v=0"}, &struct{}{})
	if err == nil || !contains(err.Error(), "401") {
		t.Fatalf("ohne pruefer: %v", err)
	}
	if err := postJSON(hc, base+"/api/sfu/turn", map[string]string{"token": "x"}, &struct{}{}); err == nil || !contains(err.Error(), "401") {
		t.Fatalf("turn ohne token: %v", err)
	}
	d := testNodeServer(t, true)
	dbase := "https://" + d.mux.Addr().String()
	var tok struct{ Token string }
	if err := postJSON(hc, dbase+"/api/dev/token", map[string]any{"room": "zz", "peer": "p", "role": "audio"}, &tok); err != nil {
		t.Fatal(err)
	}
	if err := postJSON(hc, dbase+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "tcp", Offer: "v=0"}, &struct{}{}); err == nil || !contains(err.Error(), "404") {
		t.Fatalf("ohne raum: %v", err)
	}
	if err := postJSON(hc, dbase+"/api/sfu/leg", sfuLegReq{Token: tok.Token + "x", Path: "tcp", Offer: "v=0"}, &struct{}{}); err == nil || !contains(err.Error(), "401") {
		t.Fatalf("gefaelscht: %v", err)
	}
	if err := postJSON(hc, dbase+"/api/dev/room", map[string]string{"room": "zz"}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	var cr struct{ Username, Credential string }
	if err := postJSON(hc, dbase+"/api/sfu/turn", map[string]string{"token": tok.Token}, &cr); err != nil || cr.Username == "" {
		t.Fatalf("turn mit token: %v", err)
	}
	if err := postJSON(hc, dbase+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: "udp", Offer: "v=0"}, &struct{}{}); err == nil {
		t.Fatal("udp ohne udp-recht")
	}
}

func contains(s, sub string) bool { return len(s) >= len(sub) && (s == sub || indexOf(s, sub) >= 0) }

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func jsonDecode(resp *http.Response, v any) error { return json.NewDecoder(resp.Body).Decode(v) }
