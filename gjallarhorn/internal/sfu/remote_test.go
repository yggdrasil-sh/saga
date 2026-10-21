package sfu

import (
	"errors"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// TestKontingentBeine: Teilnehmende eines verbundenen Servers (q im Token) hoechstens QuotaLegs - ueber eigene Beine
// und Bruecken zusammen; Notaus entfernt sie und sperrt neue.
func TestKontingentBeine(t *testing.T) {
	n := NewNode(Config{Name: "k"})
	r, _ := n.CreateRoom("raumraum", nil)
	defer n.Close()
	g := func(peer string) Grant {
		return Grant{Room: "raumraum", Peer: peer, Leg: peer + "-a", Role: RoleAudio, Pub: PubAudio, Quota: "server-b-q", QuotaLegs: 2, QuotaKbit: 1000}
	}
	if _, err := r.AddLeg(g("p1"), PathTCP, Want{Audio: 3}); err != nil {
		t.Fatal(err)
	}
	q, _ := n.Quota("server-b-q", 0, 0, false)
	if !r.AddRemote("bruecke1", "p2", Desktop, q) {
		t.Fatal("zweiter (ueber Bruecke) abgewiesen")
	}
	if _, err := r.AddLeg(g("p3"), PathTCP, Want{Audio: 3}); !errors.Is(err, ErrQuota) {
		t.Fatalf("dritter: %v", err)
	}
	if r.AddRemote("bruecke1", "p4", Desktop, q) {
		t.Fatal("dritter ueber Bruecke angenommen")
	}
	// eigene Konten (ohne q) zaehlen nicht
	if _, err := r.AddLeg(Grant{Room: "raumraum", Peer: "e1", Leg: "e1-a", Role: RoleAudio, Pub: PubAudio}, PathTCP, Want{Audio: 3}); err != nil {
		t.Fatal(err)
	}
	if got := n.QuotaStats()["server-b-q"].Peers; got != 2 {
		t.Fatalf("zaehler %d", got)
	}
	if n.QuotaOff("server-b-q", time.Minute) != 2 {
		t.Fatal("notaus entfernt nicht beide")
	}
	if len(r.Peers()) != 1 {
		t.Fatalf("nach notaus: %v", r.Peers())
	}
	if _, err := r.AddLeg(g("p5"), PathTCP, Want{Audio: 3}); !errors.Is(err, ErrQuota) {
		t.Fatalf("nach notaus: %v", err)
	}
}

// TestBrueckeQuellen: Quellen ueber eine Bruecke gelten als nutzbar, wenn der andere Knoten sie meldet; der Blick einer
// Bruecke laesst ihre eigenen Teilnehmenden aus (keine Schleife).
func TestBrueckeQuellen(t *testing.T) {
	n := NewNode(Config{Name: "k"})
	r, _ := n.CreateRoom("raumraum", nil)
	defer n.Close()
	if !r.AddRemote("b1", "fern1", Desktop, nil) {
		t.Fatal("aufnehmen")
	}
	if r.AddRemote("b2", "fern1", Desktop, nil) {
		t.Fatal("dieselbe Kennung ueber eine zweite Bruecke angenommen")
	}
	s := r.RemoteSource("b1", "fern1", fwd.Video, "m", false, 0, nil)
	if s == nil {
		t.Fatal("quelle")
	}
	if v := r.View("b1"); len(v) != 0 {
		t.Fatalf("blick der eigenen bruecke: %v", v)
	}
	v := r.View("b2")
	if len(v) != 1 || len(v[0].Srcs) != 1 || !v[0].Srcs[0].On {
		t.Fatalf("blick: %+v", v)
	}
	r.SetRemote("fern1", -1, map[*fwd.Source]bool{s: false})
	if r.View("")[0].Srcs[0].On {
		t.Fatal("aus gemeldet, trotzdem nutzbar")
	}
	if r.RemoveVia("b1") != 1 || len(r.Peers()) != 0 {
		t.Fatal("bruecke zu: teilnehmende bleiben")
	}
}
