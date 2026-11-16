package sfu

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Saga 2.5 (Rest aus F5, gruppe-sprecher): Hauptsprecher in der Gruppe nach dem Wiederverbinden.
//
// Modell: ein Empfaenger (eigenes Bein, TCP: Sprecherplatz bis m, zwei Kacheln auf l) und drei Teilnehmende ueber eine
// Bruecke (so laesst sich steuern, wann ihre Quellen live sind und wer spricht). Gemessen wird am Sprecherplatz des
// Empfaengers ab dem Wiederkommen: wann wieder der Hauptsprecher darauf ist und wann auf m.

type gruppe struct {
	t     *testing.T
	n     *Node
	r     *Room
	me    *Leg
	now   int64
	level map[string]uint32 // wer gerade spricht (Pegel, 127 = still)
	aud   map[string]*fwd.Source
	vid   map[string]map[string]*fwd.Source
	live  map[*fwd.Source]bool
	rate  map[string]float64
}

func newGruppe(t *testing.T) *gruppe {
	t.Helper()
	n := NewNode(Config{Name: "k", Tick: time.Hour, Probing: true})
	t.Cleanup(n.Close)
	r, err := n.CreateRoom("raumgruppe", nil)
	if err != nil {
		t.Fatal(err)
	}
	me, err := r.AddLeg(Grant{Room: "raumgruppe", Peer: "empf", Leg: "empf-b", Role: RoleBoth}, PathTCP,
		Want{Audio: 2, Speaker: true, Tiles: 2})
	if err != nil {
		t.Fatal(err)
	}
	return &gruppe{t: t, n: n, r: r, me: me, now: fwd.Now(), level: map[string]uint32{}, aud: map[string]*fwd.Source{},
		vid: map[string]map[string]*fwd.Source{}, live: map[*fwd.Source]bool{}, rate: map[string]float64{"l": 150e3, "m": 500e3}}
}

// dazu: Teilnehmende id kommt (neues Peer-Objekt); l sofort live, m erst mit mLive (sonst spaeter per mAn)
func (g *gruppe) dazu(id string, mLive bool) {
	g.t.Helper()
	if !g.r.AddRemote("b1", id, Desktop, nil) {
		g.t.Fatal("dazu " + id)
	}
	g.aud[id] = g.r.RemoteSource("b1", id, fwd.Audio, "a", false, 1, nil)
	g.vid[id] = map[string]*fwd.Source{}
	for _, k := range []string{"l", "m"} {
		s := g.r.RemoteSource("b1", id, fwd.Video, k, false, 0, nil)
		g.vid[id][k] = s
		g.live[s] = k == "l" || mLive
	}
	g.live[g.aud[id]] = true
	g.sync(id)
}

func (g *gruppe) weg(id string) {
	g.t.Helper()
	if !g.r.RemoveRemote(id) {
		g.t.Fatal("weg " + id)
	}
	delete(g.aud, id)
	delete(g.vid, id)
	delete(g.level, id)
}

func (g *gruppe) mAn(id string) {
	if s := g.vid[id]["m"]; s != nil && !g.live[s] {
		g.live[s] = true
		g.sync(id)
	}
}

func (g *gruppe) sync(id string) {
	on := map[*fwd.Source]bool{g.aud[id]: true}
	for _, s := range g.vid[id] {
		on[s] = g.live[s]
	}
	g.r.SetRemote(id, -1, on)
}

func (g *gruppe) tick() {
	g.now += int64(200 * time.Millisecond)
	for id, a := range g.aud {
		if lv, ok := g.level[id]; ok && lv < 127 {
			a.VoicePkts.Add(5)
			a.LevelSum.Add(5 * (127 - lv))
			a.LastVoice.Store(g.now)
		}
		for k, s := range g.vid[id] {
			if g.live[s] {
				s.Bytes.Add(uint64(g.rate[k] * 0.2 / 8))
			}
		}
	}
	g.me.fl.Stats.BytesOut.Add(uint64(1500e3 * 0.2 / 8))
	g.r.tickAt(g.now)
}

func (g *gruppe) run(d time.Duration) {
	for end := g.now + int64(d); g.now < end; {
		g.tick()
	}
}

// spk: Besitzer und Schicht des Sprecherplatzes
func (g *gruppe) spk() (string, string) {
	g.r.mu.Lock()
	defer g.r.mu.Unlock()
	for _, s := range g.me.slots {
		if s.class == "speaker" && s.owner != nil {
			return s.owner.ID, s.layer
		}
	}
	return "", ""
}

// vorlauf: b, c, dann a sprechen nacheinander; a ist Hauptsprecher auf m, b und c auf den Kacheln
func (g *gruppe) vorlauf() {
	g.t.Helper()
	for _, id := range []string{"a", "b", "c"} {
		g.dazu(id, true)
	}
	for _, id := range []string{"b", "c", "a"} {
		g.level = map[string]uint32{id: 30}
		g.run(4 * time.Second)
	}
	g.run(10 * time.Second)
	if o, l := g.spk(); o != "a" || l != "m" {
		g.t.Fatalf("vorher: Sprecherplatz %q/%q", o, l)
	}
}

// gruppeFall: Ablauf ab dem Wiederkommen von wer (Zeit 0): wann spricht wer, wann ist m des Wiederkommenden live
type gruppeFall struct {
	name   string
	gap    time.Duration // Luecke: a ist nicht im Raum
	neu    string        // ID des Wiederkommenden ("a" = gleiches Geraet; sonst "Hierher uebernehmen")
	mDelay time.Duration // m des Wiederkommenden live so lange nach l (0: sofort)
	aStill time.Duration // so lange schweigt der Wiederkommende nach dem Wiederkommen
	bLuecke bool         // b sagt in der Luecke kurz etwas (0,4 s, am Ende der Luecke)
}

// messen: Zeit ab dem Wiederkommen, bis der Wiederkommende auf dem Sprecherplatz ist (tOn) bzw. dort auf m (tM),
// Folge der Besitzer des Sprecherplatzes ab dem Gehen.
func (g *gruppe) messen(f gruppeFall) (tOn, tM time.Duration, seq string) {
	g.t.Helper()
	add := func(o string) {
		if o == "" {
			o = "-"
		}
		if !strings.HasSuffix(seq, o) {
			seq += o
		}
	}
	o, _ := g.spk()
	add(o)
	g.weg("a")
	g.level = map[string]uint32{}
	for end := g.now + int64(f.gap); g.now < end; {
		if f.bLuecke && end-g.now <= int64(400*time.Millisecond) {
			g.level["b"] = 40
		}
		g.tick()
		o, _ := g.spk()
		add(o)
	}
	delete(g.level, "b")
	g.dazu(f.neu, f.mDelay == 0)
	start := g.now
	tOn, tM = -1, -1
	for g.now-start < int64(12*time.Second) {
		el := time.Duration(g.now - start)
		if el >= f.mDelay {
			g.mAn(f.neu)
		}
		if el >= f.aStill {
			g.level[f.neu] = 30
		}
		g.tick()
		el = time.Duration(g.now - start)
		o, l := g.spk()
		add(o + map[bool]string{true: "/" + l, false: ""}[o == f.neu])
		if tOn < 0 && o == f.neu {
			tOn = el
		}
		if tM < 0 && o == f.neu && l == "m" {
			tM = el
		}
	}
	return tOn, tM, seq
}

var gruppeFaelle = []gruppeFall{
	// Fall 1: der Hauptsprecher verbindet neu (gleiches Geraet) und spricht gleich weiter
	{name: "1 weiter", gap: 200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond},
	{name: "1 weiter", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond},
	{name: "1 weiter", gap: 1200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond},
	{name: "1 weiter m sofort", gap: 600 * time.Millisecond, neu: "a"},
	// ... b sagt in der Luecke kurz etwas
	{name: "1 b in Luecke", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, bLuecke: true},
	{name: "1 b in Luecke", gap: 1200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, bLuecke: true},
	{name: "1 b in Luecke m sofort", gap: 1200 * time.Millisecond, neu: "a", bLuecke: true},
	// ... schweigt nach dem Wiederkommen noch 3 s (vorher blieb der stille Hauptsprecher auf dem Platz)
	{name: "1 still 3 s", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second},
	{name: "1 still 3 s", gap: 1200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second},
	{name: "1 still 3 s m sofort", gap: 600 * time.Millisecond, neu: "a", aStill: 3 * time.Second},
	// Fall 3: "Hierher uebernehmen" - anderes Geraet (andere ID) kommt und spricht
	{name: "3 uebernehmen", gap: 200 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
	{name: "3 uebernehmen", gap: 600 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
	{name: "3 uebernehmen", gap: 1200 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
}

func (f gruppeFall) String() string {
	return fmt.Sprintf("%-24s Luecke %4.1f s, m nach %3.1f s", f.name, f.gap.Seconds(), f.mDelay.Seconds())
}

// TestGruppeSprecherMessung: Messung (Ausgabe mit -v)
func TestGruppeSprecherMessung(t *testing.T) {
	for _, f := range gruppeFaelle {
		g := newGruppe(t)
		g.vorlauf()
		tOn, tM, seq := g.messen(f)
		t.Logf("%v: auf dem Platz nach %5.1f s, auf m nach %5.1f s, Folge %s", f, tOn.Seconds(), tM.Seconds(), seq)
	}
}

// Fall 2: Besitzerwechsel ohne Wiederverbinden auf einen frischen Teilnehmenden (kommt dazu und spricht gleich)
func TestGruppeSprecherNeu(t *testing.T) {
	for _, md := range []time.Duration{0, 200 * time.Millisecond, 400 * time.Millisecond, 1000 * time.Millisecond} {
		g := newGruppe(t)
		g.vorlauf()
		g.level = map[string]uint32{}
		g.run(time.Second)
		g.dazu("d", md == 0)
		start := g.now
		tOn, tM, seq := time.Duration(-1), time.Duration(-1), ""
		for g.now-start < int64(12*time.Second) {
			if time.Duration(g.now-start) >= md {
				g.mAn("d")
			}
			g.level["d"] = 30
			g.tick()
			el := time.Duration(g.now - start)
			o, l := g.spk()
			if x := o + "/" + l; !strings.HasSuffix(seq, x) {
				seq += " " + x
			}
			if tOn < 0 && o == "d" {
				tOn = el
			}
			if tM < 0 && o == "d" && l == "m" {
				tM = el
			}
		}
		t.Logf("2 neu, m nach %3.1f s: auf dem Platz nach %5.1f s, auf m nach %5.1f s, Folge%s", md.Seconds(), tOn.Seconds(), tM.Seconds(), seq)
	}
}
