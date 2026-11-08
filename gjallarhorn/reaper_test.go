package main

import (
	"testing"
	"time"

	"github.com/pion/webrtc/v4"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Horn-Fix: Knoten-Beine (Betriebsart node) duerfen nicht nach 15 min
// abgeraeumt werden; Spiegel-/Bench-Beine schon. failed/new-Regeln bleiben.
func TestReapReason(t *testing.T) {
	t0 := time.Date(2026, 11, 8, 12, 0, 0, 0, time.UTC)
	sfuLeg := &leg{id: "aaaaaaaaaaaaaaaa", created: t0, sl: &sfu.Leg{}}
	mirror := &leg{id: "bbbbbbbbbbbbbbbb", created: t0}
	con := webrtc.PeerConnectionStateConnected
	cases := []struct {
		name string
		l    *leg
		st   webrtc.PeerConnectionState
		age  time.Duration
		want string
	}{
		{"sfu verbunden 16 min", sfuLeg, con, 16 * time.Minute, ""},
		{"sfu verbunden 3 h", sfuLeg, con, 3 * time.Hour, ""},
		{"spiegel verbunden 14 min", mirror, con, 14 * time.Minute, ""},
		{"spiegel verbunden 16 min", mirror, con, 16 * time.Minute, "alter"},
		{"sfu failed 10 s", sfuLeg, webrtc.PeerConnectionStateFailed, 10 * time.Second, ""},
		{"sfu failed 31 s", sfuLeg, webrtc.PeerConnectionStateFailed, 31 * time.Second, "gescheitert"},
		{"sfu closed 20 min", sfuLeg, webrtc.PeerConnectionStateClosed, 20 * time.Minute, "gescheitert"},
		{"sfu new 59 s", sfuLeg, webrtc.PeerConnectionStateNew, 59 * time.Second, ""},
		{"sfu new 61 s", sfuLeg, webrtc.PeerConnectionStateNew, 61 * time.Second, "nie verbunden"},
		{"spiegel new 61 s", mirror, webrtc.PeerConnectionStateNew, 61 * time.Second, "nie verbunden"},
	}
	for _, c := range cases {
		if got := reapReason(c.l, c.st, t0.Add(c.age)); got != c.want {
			t.Errorf("%s: grund %q, erwartet %q", c.name, got, c.want)
		}
	}
}

// reapOnce mit injizierter Zeit: nur das Spiegel-Bein wird entfernt.
func TestReapOnceKeepsSFULeg(t *testing.T) {
	t0 := time.Now()
	s := &server{legs: map[string]*leg{}, ufrags: map[string]*leg{}, perIP: map[string]int{}, bySFU: map[*sfu.Leg]*leg{}}
	sl := &sfu.Leg{}
	a := &leg{id: "sfu0000000000000", created: t0, sl: sl}
	b := &leg{id: "spg0000000000000", created: t0}
	s.legs[a.id], s.legs[b.id] = a, b
	s.bySFU[sl] = a
	// Beine ohne pc gelten als "new": a bleibt unter der new-Grenze (60 s),
	// b ist aelter als legMaxAge.
	b.created = t0.Add(-legMaxAge - time.Minute)
	s.reapOnce(t0.Add(30 * time.Second))
	if _, ok := s.legs[a.id]; !ok {
		t.Fatal("sfu-bein wurde abgeraeumt")
	}
	if _, ok := s.legs[b.id]; ok {
		t.Fatal("spiegel-bein nach 15 min nicht abgeraeumt")
	}
}
