package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// screenOwner liefert, wem der Bildschirmplatz eines Beins gerade gehoert.
func screenOwner(l *Leg) string {
	for _, s := range l.Layout() {
		if s.Class == "screen" {
			return s.Peer
		}
	}
	return ""
}

// idle laesst Zeit ohne Pakete vergehen und taktet dabei (Quellen werden still).
func idle(r *Room, d time.Duration) {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		r.Tick()
		time.Sleep(50 * time.Millisecond)
	}
	r.Tick()
}

// TestBildschirmErneutGeteilt (BH3): Die Apps und das Web beenden das Teilen mit replaceTrack(null) auf einem festen
// Sender - die Quelle bleibt am Knoten angemeldet (pion meldet kein Spurende). Teilt jemand spaeter erneut, kommt kein
// neues AddSource, scrAt blieb alt: Der Bildschirmplatz blieb bei der zuletzt Teilenden, die laengst aufgehoert hatte,
// und der neue Bildschirm kam nie an (Platz dauerhaft schwarz). Jetzt zaehlt die Rueckkehr nach einer Pause als Beginn.
func TestBildschirmErneutGeteilt(t *testing.T) {
	old := screenResume
	screenResume = 300 * time.Millisecond
	defer func() { screenResume = old }()
	n := testNode(t, Config{})
	r, err := n.CreateRoom("raum-bildschirm", nil)
	if err != nil {
		t.Fatal(err)
	}
	vl, err := r.AddLeg(Grant{Room: r.Ref(), Peer: "zuschauer", Leg: "zuschauer-v", Role: RoleVideo, Pub: PubVideo}, PathTCP, Want{Screen: true})
	if err != nil {
		t.Fatal(err)
	}
	var seq uint16
	share := func(id string, ssrc uint32) *fwd.Source {
		l, err := r.AddLeg(Grant{Room: r.Ref(), Peer: id, Leg: id + "-v", Role: RoleVideo, Pub: PubScreen}, PathTCP, Want{})
		if err != nil {
			t.Fatal(err)
		}
		s, err := l.AddSource(fwd.Video, "h", true, ssrc, 0, nil)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	frame := func(s *fwd.Source) {
		seq++
		s.Inject(vp8Pkt(seq, uint32(seq)*3000, seq, 0, true, 100))
	}
	x := share("xaver", 11)
	frame(x)
	r.Tick()
	if o := screenOwner(vl); o != "xaver" {
		t.Fatalf("erste Freigabe: platz bei %q", o)
	}
	time.Sleep(5 * time.Millisecond)
	y := share("yvonne", 12)
	frame(y)
	r.Tick()
	if o := screenOwner(vl); o != "yvonne" {
		t.Fatalf("zweite Freigabe: platz bei %q", o)
	}
	// beide hoeren auf (replaceTrack(null): keine Pakete mehr, Quellen bleiben angemeldet)
	idle(r, time.Second+2*screenResume)
	if o := screenOwner(vl); o != "yvonne" {
		t.Fatalf("ohne Pakete: platz wechselt grundlos zu %q", o)
	}
	// xaver teilt erneut (derselbe Sender, dieselbe SSRC)
	for i := 0; i < 3; i++ {
		frame(x)
		r.Tick()
	}
	if o := screenOwner(vl); o != "xaver" {
		t.Fatalf("erneute Freigabe kommt nicht an: platz bei %q", o)
	}
}
