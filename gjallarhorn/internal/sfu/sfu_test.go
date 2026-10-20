package sfu

import (
	"encoding/binary"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// rec zaehlt je CSRC die geschriebenen Pakete eines Ausgangs.
type rec struct {
	mu    sync.Mutex
	csrc  map[uint32]int
	last  uint32
	total int
}

func (r *rec) WriteRTP(h *rtp.Header, p []byte) (int, error) {
	r.mu.Lock()
	if r.csrc == nil {
		r.csrc = map[uint32]int{}
	}
	if len(h.CSRC) == 1 {
		r.csrc[h.CSRC[0]]++
		r.last = h.CSRC[0]
	}
	r.total++
	r.mu.Unlock()
	return len(p), nil
}

func (r *rec) lastCSRC() uint32 { r.mu.Lock(); defer r.mu.Unlock(); return r.last }

func audioPkt(seq uint16, ts uint32, level uint8) []byte {
	b := make([]byte, 12, 40)
	b[0] = 0x90 // Erweiterung
	b[1] = 111
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	b = append(b, 0xbe, 0xde, 0, 1, 0x10, 0x80|level, 0, 0)
	return append(b, 0x78, 1, 2, 3, 4, 5, 6, 7)
}

func vp8Pkt(seq uint16, ts uint32, pic uint16, tid uint8, key bool, size int) []byte {
	b := make([]byte, 12, 12+8+size)
	b[0] = 0x80
	b[1] = 96 | 0x80
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	pl := byte(1)
	if key {
		pl = 0
	}
	b = append(b, 0x90, 0xa0, 0x80|byte(pic>>8), byte(pic), tid<<6|0x20, pl)
	return append(b, make([]byte, size)...)
}

type fakePeer struct {
	id           string
	audio        *fwd.Source
	cam          map[string]*fwd.Source
	level        atomic.Uint32 // 127 = still
	aLeg, vLeg   *Leg
	aRecs, vRecs []*rec
	plis         atomic.Int32
	keyReq       atomic.Bool
}

func testNode(t *testing.T, cfg Config) *Node {
	if cfg.Verifier == nil {
		cfg.Verifier = VerifierFunc(func(tok string) (Grant, error) { return Grant{}, errors.New("nein") })
	}
	cfg.Tick = time.Hour // Takt nur von Hand
	n := NewNode(cfg)
	t.Cleanup(n.Close)
	return n
}

// join legt ein Geraet mit Ton- und Bild-Leitung (TCP) an.
func join(t *testing.T, r *Room, id string, dev Device, path Path, tiles int, layersSent ...string) *fakePeer {
	t.Helper()
	fp := &fakePeer{id: id, cam: map[string]*fwd.Source{}}
	fp.level.Store(127)
	pub := PubAudio | PubVideo
	ga := Grant{Room: r.Ref(), Peer: id, Leg: id + "-a", Role: RoleAudio, Pub: pub, Device: dev}
	gv := Grant{Room: r.Ref(), Peer: id, Leg: id + "-v", Role: RoleVideo, Pub: pub, Device: dev}
	var err error
	if fp.aLeg, err = r.AddLeg(ga, path, Want{Audio: 3}); err != nil {
		t.Fatal(err)
	}
	if fp.vLeg, err = r.AddLeg(gv, path, Want{Speaker: true, Tiles: tiles}); err != nil {
		t.Fatal(err)
	}
	for i, o := range fp.aLeg.Outs() {
		w := &rec{}
		fp.aRecs = append(fp.aRecs, w)
		o.Bind(w, uint32(100+i), 111)
	}
	for i, o := range fp.vLeg.Outs() {
		w := &rec{}
		fp.vRecs = append(fp.vRecs, w)
		o.Bind(w, uint32(200+i), 96)
	}
	if fp.audio, err = fp.aLeg.AddSource(fwd.Audio, "a", false, 1, 1, nil); err != nil {
		t.Fatal(err)
	}
	for _, l := range layersSent {
		s, err := fp.vLeg.AddSource(fwd.Video, l, false, 2, 0, func(uint32) { fp.plis.Add(1); fp.keyReq.Store(true) })
		if err != nil {
			t.Fatal(err)
		}
		fp.cam[l] = s
	}
	return fp
}

// pump sendet Ton (25/s) und Bild (30 fps, L1T3) aller Geraete in Echtzeit.
func pump(peers []*fakePeer, stop <-chan struct{}) {
	tk := time.NewTicker(time.Second / 30)
	defer tk.Stop()
	var n uint32
	for {
		select {
		case <-stop:
			return
		case <-tk.C:
		}
		n++
		for _, p := range peers {
			if n%6 < 5 || n%6 == 0 { // ca. 25/s
				p.audio.Inject(audioPkt(uint16(n), n*1920, uint8(p.level.Load())))
			}
			tid := [4]uint8{0, 2, 1, 2}[n%4]
			key := n%90 == 1
			if p.keyReq.Load() && tid == 0 {
				key = true
				p.keyReq.Store(false)
			}
			for l, s := range p.cam {
				size := 400
				if l == "m" {
					size = 1100
				}
				s.Inject(vp8Pkt(uint16(n), n*3000, uint16(n), tid, key, size))
			}
		}
	}
}

func settle(r *Room, d time.Duration) {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		time.Sleep(100 * time.Millisecond)
		r.Tick()
	}
}

func audioOwners(p *fakePeer) map[string]bool {
	m := map[string]bool{}
	for _, s := range p.aLeg.Layout() {
		if s.Peer != "" {
			m[s.Peer] = true
		}
	}
	return m
}

func TestTop3SpeakerLastN(t *testing.T) {
	n := testNode(t, Config{})
	r, _ := n.CreateRoom("call1", nil)
	var ps []*fakePeer
	for i := 0; i < 6; i++ {
		dev := Desktop
		if i == 5 {
			dev = Mobile
		}
		ps = append(ps, join(t, r, fmt.Sprintf("p%d", i), dev, PathTCP, 9, "l", "m"))
	}
	// p1 laut, p2 und p3 leiser, p4 still
	ps[1].level.Store(15)
	ps[2].level.Store(35)
	ps[3].level.Store(40)
	stop := make(chan struct{})
	go pump(ps, stop)
	defer close(stop)
	settle(r, 2500*time.Millisecond)

	// Top-3-Ton fuer p0: p1, p2, p3
	if got := audioOwners(ps[0]); !(got["p1"] && got["p2"] && got["p3"]) || len(got) != 3 {
		t.Fatalf("top-3 fuer p0: %v", got)
	}
	// p1 hoert p2, p3 und einen Stillen
	if got := audioOwners(ps[1]); !(got["p2"] && got["p3"]) || got["p1"] {
		t.Fatalf("top-3 fuer p1: %v", got)
	}
	// Hauptsprecher p1 im Sprecherplatz, fuer p1 selbst jemand anderes
	if s := ps[0].vLeg.Layout()[0]; s.Class != "speaker" || s.Peer != "p1" {
		t.Fatalf("sprecher fuer p0: %+v", s)
	}
	if s := ps[1].vLeg.Layout()[0]; s.Peer == "p1" || s.Peer == "" {
		t.Fatalf("sprecher fuer p1: %+v", s)
	}
	// Pakete kommen mit der CSRC des Sprechers an
	if c := ps[0].vRecs[0].lastCSRC(); c != r.Peers()["p1"] {
		t.Fatalf("csrc im sprecherplatz %d", c)
	}
	// Last-N: Desktop 9 Plaetze (4 belegt: alle ausser sich und Sprecher), Handy 4
	if len(ps[0].vLeg.Outs()) != 10 || len(ps[5].vLeg.Outs()) != 5 {
		t.Fatalf("plaetze desktop %d handy %d", len(ps[0].vLeg.Outs()), len(ps[5].vLeg.Outs()))
	}
	tiles := 0
	for _, s := range ps[0].vLeg.Layout()[1:] {
		if s.Peer != "" {
			tiles++
			if s.Peer == "p0" || s.Peer == "p1" {
				t.Fatalf("kachel mit %s", s.Peer)
			}
			// TCP: Kacheln unten, Bildrate begrenzt (L1T3 30 fps -> TID 0)
			if s.Layer != "l" || s.MaxTID != 0 {
				t.Fatalf("kachel %+v", s)
			}
		}
	}
	if tiles != 4 {
		t.Fatalf("%d kacheln", tiles)
	}
	// Sprecher auf TCP bekommt mehr (m), solange die Schaetzung reicht
	if s := ps[0].vLeg.Layout()[0]; s.Layer != "m" || s.MaxTID != 3 {
		t.Fatalf("sprecher-schicht %+v (schaetzung %d kbit/s)", s, ps[0].vLeg.EstimateKbit())
	}
	// Sprecherwechsel mit Hysterese: p1 still, p4 laut
	ps[1].level.Store(127)
	ps[2].level.Store(70) // Hintergrund, keine Sprache
	ps[4].level.Store(10)
	settle(r, 2500*time.Millisecond)
	if s := ps[0].vLeg.Layout()[0]; s.Peer != "p4" {
		t.Fatalf("neuer sprecher %+v", s)
	}
	if got := audioOwners(ps[0]); !got["p4"] {
		t.Fatalf("p4 nicht im ton: %v", got)
	}
	ld := n.Load()
	if ld.Rooms != 1 || ld.Peers != 6 || ld.Legs != 12 || ld.Sources != 18 {
		t.Fatalf("last %+v", ld)
	}
}

func TestBudgetDownswitch(t *testing.T) {
	n := testNode(t, Config{})
	r, _ := n.CreateRoom("call2", nil)
	a := join(t, r, "a", Desktop, PathTCP, 4, "l", "m")
	b := join(t, r, "b", Desktop, PathTCP, 4, "l", "m")
	c := join(t, r, "c", Desktop, PathTCP, 4, "l", "m")
	b.level.Store(20)
	stop := make(chan struct{})
	go pump([]*fakePeer{a, b, c}, stop)
	defer close(stop)
	settle(r, 2*time.Second)
	if s := a.vLeg.Layout()[0]; s.Peer != "b" || s.Layer != "m" {
		t.Fatalf("vorher %+v", s)
	}
	// enger Downlink fuer a: 220 kbit/s (m allein braucht ~270)
	a.vLeg.FwdLeg().SetThrottle(220_000)
	settle(r, 4*time.Second)
	sl := a.vLeg.Layout()
	if sl[0].Layer != "l" {
		t.Fatalf("sprecher nicht runter: %+v est %d", sl[0], a.vLeg.EstimateKbit())
	}
	if est := a.vLeg.EstimateKbit(); est > 400 {
		t.Fatalf("schaetzung %d kbit/s", est)
	}
	// andere Empfaenger unberuehrt
	if s := c.vLeg.Layout()[0]; s.Layer != "m" {
		t.Fatalf("c betroffen %+v", s)
	}
	// Drossel weg: nach der Sperrzeit wieder hoch
	a.vLeg.FwdLeg().SetThrottle(0)
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) && a.vLeg.Layout()[0].Layer != "m" {
		settle(r, 500*time.Millisecond)
	}
	if s := a.vLeg.Layout()[0]; s.Layer != "m" {
		t.Fatalf("nicht wieder hoch %+v est %d", s, a.vLeg.EstimateKbit())
	}
}

func TestUDPThreeLayers(t *testing.T) {
	n := testNode(t, Config{StartKbit: 5000})
	r, _ := n.CreateRoom("call3", nil)
	a := join(t, r, "a", Desktop, PathUDP, 4, "l", "m", "h")
	b := join(t, r, "b", Desktop, PathUDP, 4, "l", "m", "h")
	c := join(t, r, "c", Desktop, PathTCP, 4, "l", "m", "h")
	b.level.Store(20)
	stop := make(chan struct{})
	go pump([]*fakePeer{a, b, c}, stop)
	defer close(stop)
	settle(r, 2*time.Second)
	if s := a.vLeg.Layout()[0]; s.Layer != "h" {
		t.Fatalf("udp sprecher %+v", s)
	}
	if s := c.vLeg.Layout()[0]; s.Layer == "h" {
		t.Fatalf("tcp mit 3. schicht %+v", s)
	}
	for _, s := range a.vLeg.Layout()[1:] {
		if s.Peer != "" && s.MaxTID != 3 {
			t.Fatalf("udp-kachel mit bildraten-grenze %+v", s)
		}
	}
}

func TestAdmitReplaceRemove(t *testing.T) {
	var closed []string
	var mu sync.Mutex
	exp := time.Now().Add(time.Minute)
	n := NewNode(Config{Name: "horn-1", Tick: time.Hour,
		Verifier: VerifierFunc(func(tok string) (Grant, error) {
			switch tok {
			case "ok":
				return Grant{Node: "horn-1", Room: "r", Peer: "p", Leg: "l1", Role: RoleAudio, Pub: PubAudio, Exp: exp}, nil
			case "alt":
				return Grant{Room: "r", Exp: time.Now().Add(-time.Second)}, nil
			case "fremd":
				return Grant{Node: "horn-2", Room: "r"}, nil
			case "ohne-raum":
				return Grant{Room: "x"}, nil
			}
			return Grant{}, errors.New("ungueltig")
		}),
		OnLegClosed: func(l *Leg, why string) { mu.Lock(); closed = append(closed, l.Ref+":"+why); mu.Unlock() },
	})
	defer n.Close()
	if _, _, err := n.Admit("ok", PathTCP); err != ErrNoRoom {
		t.Fatalf("ohne raum: %v", err)
	}
	r, _ := n.CreateRoom("r", nil)
	if r2, _ := n.CreateRoom("r", nil); r2 != r {
		t.Fatal("nicht idempotent")
	}
	for tok, want := range map[string]error{"alt": ErrExpired, "fremd": ErrWrongNode, "ohne-raum": ErrNoRoom} {
		if _, _, err := n.Admit(tok, PathTCP); err != want {
			t.Fatalf("%s: %v", tok, err)
		}
	}
	if _, _, err := n.Admit("kaputt", PathTCP); err == nil {
		t.Fatal("kaputt angenommen")
	}
	if _, _, err := n.Admit("ok", PathUDP); err != ErrNoUDP {
		t.Fatalf("udp: %v", err)
	}
	g, rr, err := n.Admit("ok", PathTCP)
	if err != nil || rr != r {
		t.Fatal(err)
	}
	l1, err := r.AddLeg(g, PathTCP, Want{Audio: 3})
	if err != nil {
		t.Fatal(err)
	}
	// Rechte: Ton-Leitung darf kein Bild senden
	if _, err := l1.AddSource(fwd.Video, "l", false, 1, 0, nil); err != ErrNotAllowed {
		t.Fatalf("bild auf ton-leitung: %v", err)
	}
	if _, err := l1.AddSource(fwd.Audio, "a", false, 1, 1, nil); err != nil {
		t.Fatal(err)
	}
	// zweites Bein mit gleicher leg_ref ersetzt das erste
	l2, err := r.AddLeg(g, PathTCP, Want{Audio: 3})
	if err != nil || l2 == l1 {
		t.Fatal("ersetzen")
	}
	mu.Lock()
	if len(closed) != 1 || closed[0] != "l1:ersetzt" {
		t.Fatalf("geschlossen %v", closed)
	}
	mu.Unlock()
	if n.Load().Legs != 1 || n.Load().Sources != 0 {
		t.Fatalf("last nach ersetzen %+v", n.Load())
	}
	if r.RemovePeer("p") != 1 || n.Load().Peers != 0 {
		t.Fatal("remove peer")
	}
	l3, _ := r.AddLeg(g, PathTCP, Want{Audio: 3})
	if err := n.CloseRoom("r"); err != nil {
		t.Fatal(err)
	}
	if _, err := l3.AddSource(fwd.Audio, "a", false, 1, 1, nil); err != ErrClosed {
		t.Fatalf("nach schliessen: %v", err)
	}
	if n.Load().Legs != 0 || n.Room("r") != nil {
		t.Fatalf("raum nicht weg %+v", n.Load())
	}
	mu.Lock()
	defer mu.Unlock()
	if len(closed) != 3 {
		t.Fatalf("geschlossen %v", closed)
	}
}

func TestLimits(t *testing.T) {
	n := NewNode(Config{MaxLegs: 3, Tick: time.Hour, Limits: Limits{MaxPeers: 2}})
	defer n.Close()
	r, _ := n.CreateRoom("r", nil)
	g := func(p, l string) Grant { return Grant{Room: "r", Peer: p, Leg: l, Role: RoleAudio, Pub: PubAudio} }
	if _, err := r.AddLeg(g("a", "a1"), PathTCP, Want{}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.AddLeg(g("b", "b1"), PathTCP, Want{}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.AddLeg(g("c", "c1"), PathTCP, Want{}); err != ErrFull {
		t.Fatalf("drittes geraet: %v", err)
	}
	if _, err := r.AddLeg(g("a", "a2"), PathTCP, Want{}); err != nil {
		t.Fatal(err)
	}
	if _, err := r.AddLeg(g("b", "b2"), PathTCP, Want{}); err != ErrFull {
		t.Fatalf("knotengrenze: %v", err)
	}
}

func TestEstimator(t *testing.T) {
	var e estimator
	e.init(1e6, 25e6)
	l := &Leg{fl: fwd.NewLeg()}
	defer l.fl.Close()
	now := int64(10 * time.Second)
	step := func(bytes uint64, age time.Duration, drops uint32) float64 {
		l.fl.Stats.BytesOut.Add(bytes)
		l.fl.Stats.TickAgeNs.Store(int64(age))
		l.fl.Stats.TickDrops.Store(drops)
		now += int64(200 * time.Millisecond)
		return e.update(l, 0.2, now)
	}
	// frei, 2 Mbit/s gemessen: folgt nach oben
	for i := 0; i < 10; i++ {
		step(50_000, 5*time.Millisecond, 0)
	}
	if e.bps < 2e6 || e.bps > 2e6*1.6+300e3 {
		t.Fatalf("frei %v", e.bps)
	}
	// Stau: runter auf 85 % des Gemessenen, Sperrzeit
	v := step(10_000, 200*time.Millisecond, 0)
	if v > 0.4e6*0.86 || e.canUp(now) {
		t.Fatalf("stau %v", v)
	}
	// anhaltender Stau: nicht kumulativ weiter runter, Sperrzeit bleibt
	b1 := e.backoff
	v2 := step(10_000, 200*time.Millisecond, 1)
	if e.backoff != b1 || v2 < v*0.99 {
		t.Fatalf("anhaltend %v %v / %v %v", b1, e.backoff, v, v2)
	}
	// Stau nach Ablauf der Sperrzeit (Versuch nach oben gescheitert): doppelt
	now += int64(e.backoff)
	step(10_000, 200*time.Millisecond, 0)
	if e.backoff != 2*b1 {
		t.Fatalf("backoff %v %v", b1, e.backoff)
	}
	// GCC kleiner als die Warteschlangen-Schaetzung: GCC gilt
	l.Ext = func() int { return 150_000 }
	if v := step(10_000, 5*time.Millisecond, 0); v != 150_000 {
		t.Fatalf("ext %v", v)
	}
}
