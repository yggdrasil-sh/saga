package turnpipe

import (
	"runtime"
	"testing"
	"time"
)

// Ein haengendes Geraet (Relay-Ende wird nie gelesen, Schreiber wartet bis MaxWait) bremst andere Beine desselben Hubs
// nicht: weder ein zweites Paar noch die Richtung Knoten des haengenden Paares.
func TestNoCrossBlocking(t *testing.T) {
	h := NewHub()
	h.MaxWait = 3 * time.Second
	iceA, relayA := pair(t, h) // haengt
	iceB, relayB := pair(t, h) // gesund
	go func() {
		for {
			if _, err := iceA.WriteTo(rtp(1200), relayA.LocalAddr()); err != nil {
				return
			}
		}
	}()
	time.Sleep(100 * time.Millisecond) // A ist voll, sein Schreiber wartet
	if relayA.Queued() < h.Limit-1200 {
		t.Fatalf("A nicht voll: %d", relayA.Queued())
	}
	for i := 0; i < 50; i++ {
		t0 := time.Now()
		if _, err := iceB.WriteTo(rtp(1200), relayB.LocalAddr()); err != nil {
			t.Fatal(err)
		}
		if _, _, err := readTimeout(t, relayB, time.Second); err != nil {
			t.Fatalf("B lesen %d: %v", i, err)
		}
		if d := time.Since(t0); d > 100*time.Millisecond {
			t.Fatalf("B wartet hinter A: %v", d)
		}
	}
	// Richtung Knoten des haengenden Paares wartet nie
	t0 := time.Now()
	for i := 0; i < 20; i++ {
		if _, err := relayA.WriteTo(rtp(1200), iceA.LocalAddr()); err != nil {
			t.Fatal(err)
		}
	}
	if d := time.Since(t0); d > 50*time.Millisecond {
		t.Fatalf("relay -> ice wartet: %v", d)
	}
	if h.Stats.Timeouts.Load() != 0 {
		t.Fatalf("timeouts %d", h.Stats.Timeouts.Load())
	}
}

// Schliessen raeumt auf: Pumpen und wartende Schreiber enden, der Hub vergisst die Enden.
func TestCloseNoLeak(t *testing.T) {
	h := NewHub()
	h.Limit = 1000
	h.MaxWait = 10 * time.Second
	base := runtime.NumGoroutine()
	for i := 0; i < 20; i++ {
		ice := h.Attach(listen(t), ICE, nil)
		relay := h.Attach(listen(t), Relay, nil)
		_, _ = ice.WriteTo(rtp(1200), relay.LocalAddr())
		go func() { _, _ = ice.WriteTo(rtp(1200), relay.LocalAddr()) }() // wartet
		time.Sleep(5 * time.Millisecond)
		relay.Close()
		ice.Close()
	}
	deadline := time.Now().Add(2 * time.Second)
	for runtime.NumGoroutine() > base && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if n := runtime.NumGoroutine(); n > base {
		t.Fatalf("goroutinen: %d statt %d", n, base)
	}
	if v := h.View(); v.Ends != 0 {
		t.Fatalf("hub kennt noch %d enden", v.Ends)
	}
}
