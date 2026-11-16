package sfu

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Saga 2.5 (Rest aus F5, gruppe-sprecher): Hauptsprecher in der Gruppe nach dem Wiederverbinden. Vorher (testing
// 619656c), Zeit ab dem Wiederkommen:
//   - Fall 1, er spricht gleich weiter: nach 0,2 s wieder auf dem Platz, aber erst nach 4,2 s auf m - der Platz ging in
//     der Luecke auf den zuletzt Sprechenden (byRecent), zurueck ist das ein Besitzerwechsel; beim Wiederkommenden war
//     erst l live, das galt als Wechsel nach unten (downAt, layerDwell). Ebenso Fall 2 (jemand Neues spricht gleich:
//     m nach 4,4 s) und Fall 3 ("Hierher uebernehmen", andere ID: m nach 4,2 s). Jetzt: slot.fresh, m nach 0,6 s.
//   - Fall 1, er schweigt nach dem Wiederkommen noch 3 s: erst nach 3,2 s wieder auf dem Platz (mit dem alten
//     Peer-Objekt war sein Status weg); ohne Wiederverbinden bliebe der stille Hauptsprecher darauf. Jetzt: backLocked,
//     nach 0,2 s auf dem Platz, nach 0,6 s auf m.
//   - Fall 1, jemand sagt in der Luecke kurz etwas (bzw. hat kurz vorher etwas eingeworfen): Ohne Hauptsprecher wurde
//     er sofort Hauptsprecher, der Wiederkommende kam erst nach der Hysterese zurueck (1,4 s) bzw. erst, wenn er
//     wieder sprach (3,2 s). Jetzt gilt in der Luecke die Hysterese gegen einen stillen Hauptsprecher: 0,2 s.
//   - unveraendert: Spricht in der Luecke jemand laenger als domHold, wird er Hauptsprecher; der Wiederkommende loest
//     ihn mit der Hysterese von pickDominant ab - wie bei einer stillen Pause ohne Wiederverbinden.
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
				r := g.rate[k]
				if x, ok := g.rate[id+k]; ok { // eigene Rate eines Teilnehmenden
					r = x
				}
				s.Bytes.Add(uint64(r * 0.2 / 8))
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
	name     string
	gap      time.Duration // Luecke: a ist nicht im Raum
	neu      string        // ID des Wiederkommenden ("a" = gleiches Geraet; sonst "Hierher uebernehmen")
	mDelay   time.Duration // m des Wiederkommenden live so lange nach l (0: sofort)
	aStill   time.Duration // so lange schweigt der Wiederkommende nach dem Wiederkommen
	bSpricht time.Duration // so lange spricht b am Ende der Luecke
	cVorher  bool          // c wirft vor dem Gehen kurz etwas ein (leiser, a bleibt Hauptsprecher; c spricht zuletzt)
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
	if f.cVorher {
		g.level["c"] = 60
		g.run(400 * time.Millisecond)
		delete(g.level, "c")
	}
	o, _ := g.spk()
	add(o)
	g.weg("a")
	g.level = map[string]uint32{}
	for end := g.now + int64(f.gap); g.now < end; {
		if end-g.now <= int64(f.bSpricht) {
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
	{name: "1 weiter m spaet", gap: 600 * time.Millisecond, neu: "a", mDelay: time.Second},
	{name: "1 weiter m sofort", gap: 600 * time.Millisecond, neu: "a"},
	// ... schweigt nach dem Wiederkommen noch 3 s (ohne Wiederverbinden bliebe der stille Hauptsprecher auf dem Platz)
	{name: "1 still 3 s", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second},
	{name: "1 still 3 s", gap: 1200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second},
	{name: "1 still 3 s", gap: 3 * time.Second, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second},
	{name: "1 still 3 s m sofort", gap: 600 * time.Millisecond, neu: "a", aStill: 3 * time.Second},
	{name: "1 still, c Einwurf", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, aStill: 3 * time.Second, cVorher: true},
	// ... b sagt in der Luecke kurz etwas (zu kurz fuer einen Wechsel gegen einen stillen Hauptsprecher)
	{name: "1 b kurz in Luecke", gap: 600 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, bSpricht: 400 * time.Millisecond},
	{name: "1 b kurz in Luecke", gap: 1200 * time.Millisecond, neu: "a", mDelay: 400 * time.Millisecond, bSpricht: 400 * time.Millisecond},
	// Fall 3: "Hierher uebernehmen" - anderes Geraet (andere ID) kommt und spricht
	{name: "3 uebernehmen", gap: 200 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
	{name: "3 uebernehmen", gap: 600 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
	{name: "3 uebernehmen", gap: 1200 * time.Millisecond, neu: "a2", mDelay: 400 * time.Millisecond},
}

func (f gruppeFall) String() string {
	return fmt.Sprintf("%-20s Luecke %3.1f s, m nach %3.1f s", f.name, f.gap.Seconds(), f.mDelay.Seconds())
}

// TestGruppeSprecher: Nach dem Wiederverbinden ist der Hauptsprecher binnen 0,4 s wieder auf dem Sprecherplatz und
// binnen 0,4 s nach seiner m auf m (Fall 1 und 3), ohne Pendeln (Folge l, m).
func TestGruppeSprecher(t *testing.T) {
	for _, f := range gruppeFaelle {
		g := newGruppe(t)
		g.vorlauf()
		tOn, tM, seq := g.messen(f)
		t.Logf("%v: auf dem Platz nach %3.1f s, auf m nach %3.1f s, Folge %s", f, tOn.Seconds(), tM.Seconds(), seq)
		if tOn < 0 || tOn > 400*time.Millisecond || tM < 0 || tM > f.mDelay+400*time.Millisecond {
			t.Errorf("%v: auf dem Platz nach %v, auf m nach %v", f, tOn, tM)
		}
		if want := map[bool]string{true: "/m", false: "/l" + f.neu + "/m"}[f.mDelay == 0]; !strings.HasSuffix(seq, f.neu+want) {
			t.Errorf("%v: Folge %s", f, seq)
		}
	}
}

// TestGruppeSprecherGegenprobe: Der Status gilt nur ueber eine kurze Luecke und nur, solange niemand anderes
// Hauptsprecher wurde; ein echter Wechsel nach unten bleibt einer.
func TestGruppeSprecherGegenprobe(t *testing.T) {
	// Luecke laenger als domKeep: Der Platz bleibt beim zuletzt Sprechenden, bis der Wiederkommende spricht
	f := gruppeFall{name: "lang", gap: domKeep + time.Second, neu: "a", aStill: 3 * time.Second}
	g := newGruppe(t)
	g.vorlauf()
	tOn, _, seq := g.messen(f)
	t.Logf("%v: auf dem Platz nach %v, Folge %s", f, tOn, seq)
	if tOn < f.aStill || tOn > f.aStill+400*time.Millisecond {
		t.Errorf("%v: auf dem Platz nach %v (erwartet erst, wenn er spricht)", f, tOn)
	}
	// In der Luecke spricht b laenger als domHold: b ist Hauptsprecher, der Wiederkommende loest ihn erst nach der
	// Hysterese ab
	for _, gap := range []time.Duration{2 * time.Second, 3 * time.Second} {
		f = gruppeFall{name: "b spricht in Luecke", gap: gap, neu: "a", mDelay: 400 * time.Millisecond, bSpricht: 1800 * time.Millisecond}
		g = newGruppe(t)
		g.vorlauf()
		tOn, tM, seq := g.messen(f)
		t.Logf("%v: auf dem Platz nach %v, auf m nach %v, Folge %s", f, tOn, tM, seq)
		if !strings.Contains(seq, "b") || tOn < 600*time.Millisecond || tOn > 2*time.Second || tM != tOn {
			t.Errorf("%v: auf dem Platz nach %v, auf m nach %v, Folge %s", f, tOn, tM, seq)
		}
	}
	// Besitzerwechsel mit nutzbarer m, das Budget waehlt l: Wechsel nach unten wie bisher (layerDwell)
	g = newGruppe(t)
	g.vorlauf()
	g.rate["dm"] = 3000e3
	g.dazu("d", true)
	g.level = map[string]uint32{"d": 30}
	g.run(2 * time.Second)
	if o, l := g.spk(); o != "d" || l != "l" {
		t.Fatalf("budget: Sprecherplatz %q/%q", o, l)
	}
	down := int64(0)
	g.r.mu.Lock()
	for _, s := range g.me.slots {
		if s.class == "speaker" && s.downOwner == s.owner {
			down = s.downAt
		}
	}
	g.r.mu.Unlock()
	g.rate["dm"] = 500e3
	start := g.now
	for i := 0; i < 100; i++ {
		if _, l := g.spk(); l == "m" {
			break
		}
		g.tick()
	}
	t.Logf("budget: d auf m %v nach der kleineren Rate, downAt %v", time.Duration(g.now-start), down > 0)
	if _, l := g.spk(); l != "m" || down <= 0 || g.now-down < int64(layerDwell) {
		t.Errorf("budget: m nach %v (downAt %v), erwartet fruehestens layerDwell nach dem Wechsel nach unten", time.Duration(g.now-start), down)
	}
	// Flattern der m nach dem Neubeginn (gleicher Besitzer): herunter, zurueck erst nach layerDwell
	g = newGruppe(t)
	g.vorlauf()
	g.messen(gruppeFaelle[1])
	g.live[g.vid["a"]["m"]] = false
	g.sync("a")
	g.tick()
	if _, l := g.spk(); l != "l" {
		t.Fatalf("flattern: %q", l)
	}
	g.live[g.vid["a"]["m"]] = true
	g.sync("a")
	start = g.now
	for i := 0; i < 100; i++ {
		if _, l := g.spk(); l == "m" {
			break
		}
		g.tick()
	}
	t.Logf("flattern: wieder m nach %v", time.Duration(g.now-start))
	if _, l := g.spk(); l != "m" || g.now-start < int64(layerDwell-200*time.Millisecond) {
		t.Errorf("flattern: wieder m nach %v, erwartet fruehestens layerDwell", time.Duration(g.now-start))
	}
}

// TestGruppeSprecherNeu (Fall 2): Besitzerwechsel ohne Wiederverbinden auf einen frischen Teilnehmenden (kommt dazu und
// spricht gleich, seine m ist spaeter live als l): m binnen 0,4 s nach ihrer Quelle (vorher 4,4 s ab dem Beitritt).
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
		t.Logf("2 neu, m nach %3.1f s: auf dem Platz nach %3.1f s, auf m nach %3.1f s, Folge%s", md.Seconds(), tOn.Seconds(), tM.Seconds(), seq)
		if tOn < 0 || tOn > 600*time.Millisecond || tM < 0 || tM > max(md, tOn)+400*time.Millisecond {
			t.Errorf("2 neu, m nach %v: auf dem Platz nach %v, auf m nach %v (Folge%s)", md, tOn, tM, seq)
		}
	}
}

// TestGruppeSprecherAuflegen (Pruefer): Der Hauptsprecher legt endgueltig auf, waehrend er spricht, und b spricht sofort
// danach. Room.lost haelt den Status hoechstens per Hysterese zurueck - auf dem Sprecherplatz steht b trotzdem binnen
// 0,4 s (byRecent, wie vorher), und spaetestens nach domKeep ist b Hauptsprecher; a kommt nicht zurueck.
func TestGruppeSprecherAuflegen(t *testing.T) {
	for _, pause := range []time.Duration{0, 600 * time.Millisecond, 2 * time.Second} {
		g := newGruppe(t)
		g.vorlauf()
		g.level = map[string]uint32{"a": 30}
		g.run(3 * time.Second)
		g.weg("a")
		g.level = map[string]uint32{}
		g.run(pause)
		g.level = map[string]uint32{"b": 30}
		start, tOn, tDom := g.now, time.Duration(-1), time.Duration(-1)
		for g.now-start < int64(domKeep+time.Second) {
			g.tick()
			el := time.Duration(g.now - start)
			if o, _ := g.spk(); tOn < 0 && o == "b" {
				tOn = el
			}
			g.r.mu.Lock()
			if tDom < 0 && g.r.dominant != nil && g.r.dominant.ID == "b" {
				tDom = el
			}
			g.r.mu.Unlock()
		}
		t.Logf("auflegen, Pause %v: b auf dem Platz nach %v, Hauptsprecher nach %v", pause, tOn, tDom)
		if tOn < 0 || tOn > 400*time.Millisecond || tDom < 0 || tDom > domKeep {
			t.Errorf("auflegen, Pause %v: b auf dem Platz nach %v, Hauptsprecher nach %v", pause, tOn, tDom)
		}
		if o, _ := g.spk(); o != "b" {
			t.Errorf("auflegen, Pause %v: zuletzt Sprecherplatz %q", pause, o)
		}
	}
}
