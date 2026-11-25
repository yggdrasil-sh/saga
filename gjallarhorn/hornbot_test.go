package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"
)

// syncBuf sammelt die Ereignisse von horn-bot (stdout) zeilenweise.
type syncBuf struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuf) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuf) events(ev string) []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []map[string]any
	for _, ln := range strings.Split(s.b.String(), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(ln), &m) == nil && (ev == "" || m["ev"] == ev) {
			out = append(out, m)
		}
	}
	return out
}

func (s *syncBuf) wait(t *testing.T, ev string, n int, d time.Duration) []map[string]any {
	t.Helper()
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if e := s.events(ev); len(e) >= n {
			return e
		}
		if e := s.events("err"); len(e) > 0 {
			t.Fatalf("horn-bot: %v", e)
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatalf("%d x %s nicht in %v: %s", n, ev, d, s.b.String())
	return nil
}

// horn-bot gegen einen Knoten im Tunnel-Betrieb (Dev-Token statt Saga): drei Bots ueber den Weg ws mit Frame-E2E,
// Sprechmuster, Schluesselwechsel (genwait) und Kennzahlen; dazu ein vierter Bot ohne Schluessel der anderen (nokey).
func TestHornBotTunnelE2E(t *testing.T) {
	_, hs, _ := tunnelServer(t)
	hc := httpClient()
	room := "hornbot"
	if err := postJSON(hc, hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	out := &syncBuf{}
	r := newHornRun(out, nil, true, false)
	keys := map[string][]byte{}
	ids := []string{"hb0", "hb1", "hb2"}
	for i, id := range ids {
		toks := map[string]string{}
		for _, role := range []string{"audio", "video"} {
			var tok struct{ Token string }
			if err := postJSON(hc, hs.URL+"/api/dev/token", map[string]any{"room": room, "peer": id, "role": role, "device": "desktop"}, &tok); err != nil {
				t.Fatal(err)
			}
			toks[role] = tok.Token
		}
		script := "still"
		if i == 0 {
			script = "haupt"
		}
		r.handle(hbCmd{Op: "add", ID: id, Cand: hbCand{URL: hs.URL, Peer: id, Tokens: toks}, Path: "ws", Video: true, Tiles: 2, Script: script, E2E: true})
		keys[id] = e2eRandKey()
	}
	out.wait(t, "up", 3, 30*time.Second)
	b64 := base64.StdEncoding.EncodeToString
	for _, id := range ids {
		r.handle(hbCmd{Op: "own", ID: id, N: 0, Key: b64(keys[id])})
		for _, o := range ids {
			if o != id {
				r.handle(hbCmd{Op: "key", ID: id, From: o, N: 0, Key: b64(keys[o]), S: "0123456789abcdef"})
			}
		}
	}
	out.wait(t, "first", 6, 20*time.Second)
	time.Sleep(3 * time.Second)
	// Abgang: hb0 wechselt den Schluessel (neu, Umschalten nach 300 ms)
	k1 := e2eRandKey()
	t0 := time.Now()
	r.handle(hbCmd{Op: "own", ID: "hb0", N: 1, Key: b64(k1), AfterMs: 300})
	for _, o := range ids[1:] {
		r.handle(hbCmd{Op: "key", ID: o, From: "hb0", N: 1, Key: b64(k1), S: "0123456789abcdef"})
	}
	r.handle(hbCmd{Op: "genwait", Tag: "gehen", T0: t0.UnixMilli(), Want: map[string]int{"hb0": 1}, Timeout: 8000})
	gw := out.wait(t, "genwait", 1, 12*time.Second)[0]
	if gw["pairs"].(float64) != 2 || gw["done"].(float64) != 2 || gw["p50_ms"].(float64) < 250 {
		t.Fatalf("genwait: %v", gw)
	}
	r.handle(hbCmd{Op: "stats"})
	sum := out.wait(t, "sum", 1, 5*time.Second)[0]
	st := out.events("stats")
	for _, s := range st {
		e, _ := s["e2e"].(map[string]any)
		if e == nil || e["ok"].(float64) < 100 || e["auth"] != nil || e["tx_sealed"].(float64) < 100 {
			t.Fatalf("%v: E2E %v", s["id"], e)
		}
		if s["first_audio_ms"].(float64) <= 0 || s["first_video_ms"].(float64) <= 0 {
			t.Fatalf("%v: erstes Bild/Ton fehlt: %v", s["id"], s)
		}
		if tu, _ := s["tunnel"].([]any); len(tu) != 2 {
			t.Fatalf("%v: Tunnel-Zaehler %v", s["id"], s["tunnel"])
		}
	}
	if sum["bots"].(float64) != 3 || sum["errors"].(float64) != 0 {
		t.Fatalf("Zusammenfassung: %v", sum)
	}
	t.Logf("genwait %v", gw)
	t.Logf("sum %v", sum)
	for _, s := range st {
		t.Logf("%v: e2e %v freezes %v loss %v aswitch %v", s["id"], s["e2e"], s["freezes"], s["loss_pct"], s["aswitch_rx"])
	}
	for _, id := range ids {
		r.handle(hbCmd{Op: "leave", ID: id})
	}
	out.wait(t, "stats", len(st)+3, 10*time.Second)
}

// Ton der Bots ist gueltiges Opus (Code 3, CBR, 2 x 20 ms, Fuellbytes): Laenge und Rahmenaufbau (RFC 6716 §3.2.5).
func TestHornBotOpusStille(t *testing.T) {
	for _, n := range []int{120, 133} {
		p := opusSilence(nil, n)
		if len(p) != n || p[0]&3 != 3 || p[1]&0x3f != 2 || p[1]&0x40 == 0 || p[1]&0x80 != 0 {
			t.Fatalf("%d: %x", n, p[:8])
		}
		if fl := n - 3 - int(p[2]); fl != 4 || !bytes.Equal(p[3:7], []byte{0xff, 0xfe, 0xff, 0xfe}) {
			t.Fatalf("%d: Rahmen %d", n, fl)
		}
	}
}
