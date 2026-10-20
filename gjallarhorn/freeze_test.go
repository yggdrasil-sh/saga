package main

import (
	"fmt"
	"net"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/impair"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

// freezeRun: 4 Geraete ueber ICE-TCP durch einen nachgebildeten Weg
// (RTT, Segmentverlust, HoL). Liefert Einfrieren je Bildplatz und 5 min
// (Chrome-Definition auf Ankunftszeiten, also strenger als im Browser).
func freezeRun(t *testing.T, rtt time.Duration, loss float64, secs int, tileFPS, speakerFPS float64) (per5, per5jb float64, info string) {
	t.Helper()
	// Verlust entsteht im Durchreicher, nicht im Kernel: Nachlaeufer immer
	if os.Getenv("HORN_TAILPROBE") == "" {
		t.Setenv("HORN_TAILPROBE", "always")
	}
	cfg := &config{Name: "localhost", Listen: "127.0.0.1:0", Data: t.TempDir(), ICEIPs: []string{"127.0.0.1"},
		PublicIPs: []string{"127.0.0.2"}, RelayIP: net.ParseIP("127.0.0.1"), MaxLegs: 32, Mode: "node", Dev: true,
		Certs: netx.CertOptions{Name: "localhost", Self: true}}
	s, err := newServer(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.shutdown)
	if tileFPS > 0 {
		s.node.SetTCPTileFPS(tileFPS)
	}
	if speakerFPS > 0 {
		s.node.SetTCPSpeakerFPS(speakerFPS)
	}
	port := s.mux.Addr().Port
	w := impair.Config{RTT: rtt, Loss: loss, Seed: 7}
	px, err := impair.Listen(net.JoinHostPort("127.0.0.2", strconv.Itoa(port)), s.mux.Addr().String(), w, w)
	if err != nil {
		t.Skipf("127.0.0.2 nicht verfuegbar: %v", err)
	}
	t.Cleanup(func() { px.Close() })
	base := "https://" + s.mux.Addr().String()
	bs := startBots(t, base, "frz", []string{"tcp", "tcp", "tcp", "tcp"}, 4, "l", "m")
	bs[0].level.Store(25)
	time.Sleep(6 * time.Second) // Anlauf
	type snap struct{ n, jb, frames int }
	start := make([]snap, len(bs))
	for i, b := range bs {
		n, _, fr, _ := b.freezeStats()
		start[i] = snap{n, b.jbFreezes(), fr}
	}
	p0, d0 := fwd.Totals.Pauses.Load(), fwd.Totals.FramesDropped.Load()
	ws0 := fwd.Totals.WriteStalls.Load()
	time.Sleep(time.Duration(secs) * time.Second)
	frz, frzJB, tracks, frames := 0, 0, 0, 0
	for i, b := range bs {
		n, _, fr, tr := b.freezeStats()
		frz += n - start[i].n
		frzJB += b.jbFreezes() - start[i].jb
		frames += fr - start[i].frames
		tracks += tr
	}
	if tracks == 0 {
		t.Fatal("keine bildplaetze")
	}
	per5 = float64(frz) / float64(tracks) / (float64(secs) / 300)
	per5jb = float64(frzJB) / float64(tracks) / (float64(secs) / 300)
	cls := map[byte][3]int{}
	for _, b := range bs {
		for k, v := range b.freezeByClass() {
			c := cls[k]
			c[0] += v[0]
			c[1] += v[1]
			c[2] += v[2]
			cls[k] = c
		}
	}
	byClass := ""
	for k, v := range cls {
		byClass += fmt.Sprintf(" %c:%d|%d/%d", k, v[0], v[1], v[2])
	}
	info = fmt.Sprintf("rtt %v verlust %.1f%%: roh %d einfrieren auf %d bildplaetzen in %d s (%.2f je platz und 5 min), mit jitter-puffer %v: %d (%.2f); je art roh|jb%s; %d rahmen; "+
		"knoten: pausen %d, verworfen %d, schreibstaus %d, pli p50 %.0f p95 %.0f ms; weg: %d verluste, davon %d tail, max stau %v",
		rtt, loss*100, frz, tracks, secs, per5, JitterTarget, frzJB, per5jb, byClass, frames, fwd.Totals.Pauses.Load()-p0, fwd.Totals.FramesDropped.Load()-d0,
		fwd.Totals.WriteStalls.Load()-ws0, fwd.PLIRespP(0.5), fwd.PLIRespP(0.95),
		px.Stats.Losses.Load(), px.Stats.TailLosses.Load(), time.Duration(px.Stats.MaxStallNs.Load()).Round(time.Millisecond))
	return per5, per5jb, info
}

// TestFreezeLossyTCP ist der Regressionstest zur 5G-Messung (G4): 80 ms RTT
// und 1,5 % Segmentverlust auf TCP. Ziel: hoechstens 1 Einfrieren je
// Bildplatz und 5 min, gemessen mit dem Jitter-Puffer-Ziel, das Clients auf
// TCP-Wegen setzen sollen (JitterTarget); die strenge Zahl nach Ankunft
// steht im Protokoll.
func TestFreezeLossyTCP(t *testing.T) {
	if testing.Short() {
		t.Skip("lang")
	}
	secs := 30
	if v, _ := strconv.Atoi(os.Getenv("HORN_FREEZE_SECS")); v > 0 {
		secs = v
	}
	loss := 0.015
	if v, err := strconv.ParseFloat(os.Getenv("HORN_FREEZE_LOSS"), 64); err == nil && v > 0 {
		loss = v
	}
	_, per5jb, info := freezeRun(t, 80*time.Millisecond, loss, secs, 0, 0)
	t.Log(info)
	if per5jb > 1 {
		t.Fatalf("zu viel einfrieren: %s", info)
	}
}

// TestFreezeDiag vergleicht Bildraten auf TCP (nur mit HORN_FREEZE_DIAG=1).
func TestFreezeDiag(t *testing.T) {
	if os.Getenv("HORN_FREEZE_DIAG") != "1" {
		t.Skip("nur Diagnose")
	}
	for _, c := range []struct{ tile, spk float64 }{{30, 30}, {10, 30}, {10, 15}} {
		for _, loss := range []float64{0.01, 0.02} {
			_, _, info := freezeRun(t, 80*time.Millisecond, loss, 30, c.tile, c.spk)
			t.Logf("kacheln %v fps, sprecher %v fps: %s", c.tile, c.spk, info)
		}
	}
}
