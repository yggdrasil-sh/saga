package main

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// 2.6 (Bugs-DB 2417, 2263-3): Die Bruecke schickt ihre Ton-Top-3 nach derselben Regel wie die Ton-Plaetze eines Beins
// (Vertrag docs/APP-API.md "Top-3-Ton" 6). Bis 2.5 nahm sie jeden Takt die drei lautesten: Bei vier aehnlich lauten
// Sprechenden wechselte die dritte Quelle mit jedem Takt, in dem sich die Reihenfolge drehte.
func TestBrueckeTop3Hysterese(t *testing.T) {
	b := &bridge{}
	src := map[string]*fwd.Source{}
	sc := map[string]float64{}
	for _, id := range []string{"a", "b", "c", "d", "e"} {
		src[id] = &fwd.Source{Kind: fwd.Audio, Layer: "a"}
	}
	view := func(ids ...string) []sfu.PeerView {
		var v []sfu.PeerView
		for _, id := range ids {
			v = append(v, sfu.PeerView{ID: id, Score: sc[id], Audio: src[id]})
		}
		v = append(v, sfu.PeerView{ID: "ohne-ton"}) // ohne Ton: nie dabei
		return v
	}
	names := func(w map[*fwd.Source]bool) map[string]bool {
		m := map[string]bool{}
		for id, s := range src {
			if w[s] {
				m[id] = true
			}
		}
		return m
	}
	tick := int64(bridgeTick)
	now := fwd.Now()
	sc["a"], sc["b"], sc["c"] = 0.6, 0.5, 0.4
	if got := names(b.pickAudio(view("a", "b", "c"), now)); len(got) != 3 || !got["a"] || !got["b"] || !got["c"] {
		t.Fatalf("Aufbau: %v", got)
	}
	// d etwas lauter als c (Reihenfolge dreht sich jeden Takt): c bleibt
	switches := 0
	prev := names(b.pickAudio(view("a", "b", "c", "d"), now))
	for i := 0; i < 300; i++ { // 60 s
		now += tick
		if i%2 == 0 {
			sc["c"], sc["d"] = 0.4, 0.45
		} else {
			sc["c"], sc["d"] = 0.45, 0.4
		}
		got := names(b.pickAudio(view("a", "b", "c", "d"), now))
		if got["d"] != prev["d"] {
			switches++
		}
		prev = got
	}
	if switches != 0 || !prev["c"] || prev["d"] {
		t.Fatalf("aehnlich laut: %d Wechsel, am Ende %v (erwartet a, b, c)", switches, prev)
	}
	// d 1,4-mal so aktiv wie der leiseste (c): erst nach 1,5 s, dann statt c
	sc["c"], sc["d"] = 0.3, 0.45
	start := now
	for {
		now += tick
		got := names(b.pickAudio(view("a", "b", "c", "d"), now))
		if got["d"] {
			if d := time.Duration(now - start); d < 1500*time.Millisecond || d > 2*time.Second || got["c"] || !got["a"] || !got["b"] {
				t.Fatalf("d nach %s: %v", d, got)
			}
			break
		}
		if now-start > int64(5*time.Second) {
			t.Fatalf("d nach 5 s nicht dabei: %v", got)
		}
	}
	// a schweigt (still), c auch: ein sprechender Neuer (e) bekommt den Platz von a im naechsten Takt, ein stiller nicht
	sc["a"], sc["c"], sc["e"] = 0.01, 0, 0
	now += tick
	if got := names(b.pickAudio(view("a", "b", "c", "d", "e"), now)); !got["a"] || got["e"] {
		t.Fatalf("stiller Neuer: %v", got)
	}
	sc["e"] = 0.1
	now += tick
	if got := names(b.pickAudio(view("a", "b", "c", "d", "e"), now)); got["a"] || !got["e"] || !got["b"] || !got["d"] {
		t.Fatalf("sprechender Neuer auf dem stillen Platz: %v", got)
	}
	// b ist weg: freier Platz sofort, auch an einen Stillen (a und c still, a zuerst in der Liste)
	now += tick
	if got := names(b.pickAudio(view("a", "c", "d", "e"), now)); len(got) != 3 || got["b"] || !got["a"] || !got["d"] || !got["e"] {
		t.Fatalf("freier Platz: %v", got)
	}
	if len(b.aud) != sfu.MaxAudio || bridgeAudio != 3 {
		t.Fatalf("plaetze %d", len(b.aud))
	}
}
