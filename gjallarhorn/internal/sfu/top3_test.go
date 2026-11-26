package sfu

import (
	"fmt"
	"math/rand"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// 2.6 (Bugs-DB 2417, 2263-3): Top-3-Hysterese der Ton-Plaetze nach dem Vertrag (docs/APP-API.md "Top-3-Ton" 3/4).
// Bis 2.5 schuetzte keepActive nur stille Inhaber gegen stille Neue: Bei vier und mehr Sprechenden nahm jeder Takt die
// drei lautesten, Platz 3 wechselte bei aehnlich lauten Sprechenden staendig (TestTop3HystereseVierSprecher, vorher
// in derselben Simulation 30-136 Wechsel je Minute, siehe dort).
//
// Modell wie gruppe_test.go (Takt von Hand, Teilnehmende ueber eine Bruecke, Pegel je Takt), der Empfaenger hat aber 3
// Ton-Plaetze (wie jeder Client, Vertrag 1) und schweigt.

func tonGruppe(t *testing.T, ids ...string) *gruppe {
	t.Helper()
	n := NewNode(Config{Name: "k", Tick: time.Hour})
	t.Cleanup(n.Close)
	r, err := n.CreateRoom("raumton", nil)
	if err != nil {
		t.Fatal(err)
	}
	me, err := r.AddLeg(Grant{Room: "raumton", Peer: "empf", Leg: "empf-b", Role: RoleBoth}, PathTCP,
		Want{Audio: 3, Speaker: true, Tiles: 2})
	if err != nil {
		t.Fatal(err)
	}
	g := &gruppe{t: t, n: n, r: r, me: me, now: fwd.Now(), level: map[string]uint32{}, aud: map[string]*fwd.Source{},
		vid: map[string]map[string]*fwd.Source{}, live: map[*fwd.Source]bool{}, rate: map[string]float64{"l": 150e3, "m": 500e3}}
	for _, id := range ids {
		g.dazu(id, true)
	}
	return g
}

// ton: Inhaber der Ton-Plaetze des Empfaengers je Platz ("" = frei)
func (g *gruppe) ton() []string {
	g.r.mu.Lock()
	defer g.r.mu.Unlock()
	var out []string
	for _, s := range g.me.slots {
		if s.class != "audio" {
			continue
		}
		if s.owner != nil {
			out = append(out, s.owner.ID)
		} else {
			out = append(out, "")
		}
	}
	return out
}

func hat(xs []string, id string) bool {
	for _, x := range xs {
		if x == id {
			return true
		}
	}
	return false
}

// tonLauf: n Takte; zaehlt Inhaberwechsel auf den Ton-Plaetzen (ein freier Platz, der belegt wird, zaehlt nicht - wie
// aswitch) und meldet, nach wie vielen Takten id zum ersten Mal einen Platz hatte (-1 = nie). step setzt vor jedem Takt
// die Pegel (i = Nummer des Takts).
func (g *gruppe) tonLauf(d time.Duration, id string, step func(i int)) (changes, first int) {
	first = -1
	last := g.ton()
	i := 0
	for end := g.now + int64(d); g.now < end; i++ {
		if step != nil {
			step(i)
		}
		g.tick()
		cur := g.ton()
		for k := range cur {
			if cur[k] != last[k] && cur[k] != "" && last[k] != "" {
				changes++
			}
		}
		if first < 0 && id != "" && hat(cur, id) {
			first = i + 1
		}
		last = cur
	}
	return changes, first
}

// TestTop3HystereseSprecherBleibt: Sprechen alle drei Inhaber, verdraengt ein etwas lauterer Neuer niemanden; ein
// deutlich lauterer (>= 1,4x) erst nach mindestens 1,5 s, und dann den leisesten.
func TestTop3HystereseSprecherBleibt(t *testing.T) {
	g := tonGruppe(t, "a", "b", "c")
	g.level = map[string]uint32{"a": 40, "b": 40, "c": 45}
	g.run(3 * time.Second)
	if got := g.ton(); !hat(got, "a") || !hat(got, "b") || !hat(got, "c") {
		t.Fatalf("Aufbau: Ton-Plaetze %v", got)
	}
	g.dazu("d", true)
	// d spricht lauter als alle, aber nicht 1,4-mal so laut wie der leiseste Inhaber (c): niemand weicht
	g.level["d"] = 30
	if n, first := g.tonLauf(20*time.Second, "d", nil); n != 0 || first >= 0 {
		t.Fatalf("d (1,1x) verdraengt: %d Wechsel, d ab Takt %d, Plaetze %v", n, first, g.ton())
	}
	// Vorher (2.5, abe8892): d nach 9 Takten (1,8 s) auf dem Platz von c, sobald seine Aktivitaet die von c ueberstieg
	// c wird leise, spricht aber weiter (Aktivitaet ueber speakOn): d ist bald 1,4-mal so aktiv, wartet aber 1,5 s
	g.level["c"] = 100
	cond := -1 // erster Takt, in dem d die Bedingung erfuellt
	n, first := g.tonLauf(6*time.Second, "d", func(i int) {
		if cond < 0 {
			g.r.mu.Lock()
			if p, q := g.r.peers["d"], g.r.peers["c"]; p.score >= q.score*audioFactor && q.score >= speakOn {
				cond = i
			}
			g.r.mu.Unlock()
		}
	})
	got := g.ton()
	t.Logf("d 1,4x ab Takt %d, auf dem Platz ab Takt %d (%d Wechsel), Plaetze %v", cond, first, n, got)
	if first < 0 || n != 1 || hat(got, "c") || !hat(got, "a") || !hat(got, "b") {
		t.Fatalf("d deutlich lauter: %d Wechsel, d ab Takt %d, Plaetze %v (erwartet: d statt c)", n, first, got)
	}
	if cond < 0 || time.Duration(first-cond)*200*time.Millisecond < audioHold {
		t.Fatalf("d auf dem Platz nach %d Takten mit 1,4x (Haltedauer %s)", first-cond, audioHold)
	}
	if first > 15 { // 3 s: rund 1 s Anstieg der Aktivitaet + 1,5 s
		t.Fatalf("d erst nach Takt %d auf dem Platz", first)
	}
	if a := g.me.ASwitch(); a != 1 {
		t.Fatalf("aswitch %d, erwartet 1", a)
	}
	// der verdraengte c (spricht weiter leise) kommt nicht zurueck, solange alle drei sprechen
	if n, _ := g.tonLauf(10*time.Second, "", nil); n != 0 || hat(g.ton(), "c") {
		t.Fatalf("danach %d Wechsel, Plaetze %v", n, g.ton())
	}
}

// TestTop3HystereseStillerWeicht: Ein stiller Inhaber weicht einem Sprechenden sofort (<= 0,5 s), einem stillen Neuen
// nicht; einen freien Platz bekommt ein Neuer im naechsten Takt.
func TestTop3HystereseStillerWeicht(t *testing.T) {
	g := tonGruppe(t, "a", "b", "c")
	g.level = map[string]uint32{"a": 40, "b": 40, "c": 40}
	g.run(3 * time.Second)
	// c schweigt, bis die Aktivitaet unter speakOn liegt (gleitend 0,8/0,2: rund 2,4 s)
	delete(g.level, "c")
	g.run(4 * time.Second)
	g.r.mu.Lock()
	sc := g.r.peers["c"].score
	g.r.mu.Unlock()
	if sc >= speakOn || !hat(g.ton(), "c") {
		t.Fatalf("Aufbau: c Aktivitaet %.3f, Plaetze %v", sc, g.ton())
	}
	// ein stiller Neuer (e) verdraengt den stillen Inhaber c nicht
	g.dazu("e", true)
	if n, first := g.tonLauf(3*time.Second, "e", nil); n != 0 || first >= 0 {
		t.Fatalf("stiller Neuer: %d Wechsel, e ab Takt %d, Plaetze %v", n, first, g.ton())
	}
	// d spricht (mittlerer Pegel): nach hoechstens 0,5 s auf dem Platz von c
	g.dazu("d", true)
	g.level["d"] = 60
	n, first := g.tonLauf(2*time.Second, "d", nil)
	got := g.ton()
	t.Logf("d auf dem stillen Platz nach %d Takten, Plaetze %v", first, got)
	if first < 0 || first > 2 || n != 1 || hat(got, "c") || hat(got, "e") {
		t.Fatalf("d: ab Takt %d (hoechstens 2 = 0,5 s), %d Wechsel, Plaetze %v", first, n, got)
	}
	// a geht: der freie Platz geht im naechsten Takt an den Wartenden (c spricht wieder), nicht an den Stillen e
	g.level["c"] = 50
	g.run(time.Second)
	g.weg("a")
	if _, first := g.tonLauf(time.Second, "c", nil); first != 1 {
		t.Fatalf("freier Platz: c ab Takt %d, Plaetze %v", first, g.ton())
	}
	// noch einer geht: jetzt bekommt auch der stille e den freien Platz
	g.weg("b")
	if _, first := g.tonLauf(time.Second, "e", nil); first != 1 {
		t.Fatalf("freier Platz fuer den stillen e: ab Takt %d, Plaetze %v", first, g.ton())
	}
	if a := g.me.ASwitch(); a != 1+2 { // d statt c; c und e auf den frei gewordenen Plaetzen von a und b
		t.Fatalf("aswitch %d, erwartet 3", a)
	}
}

// vierSprecher: die Personen sprechen 65 s zugleich mit schwankendem Pegel (je Takt zufaellig im Band, mit
// Wahrscheinlichkeit pause je Takt eine Atempause von 0,2-0,8 s); Ergebnis: Inhaberwechsel auf den Ton-Plaetzen in den
// letzten 60 s (die ersten 5 s sind Anlauf) und die Belegung am Ende.
func vierSprecher(t *testing.T, seed int64, band [][2]uint32, pause float64) (int, []string) {
	ids := []string{"a", "b", "c", "d", "e", "f"}[:len(band)]
	g := tonGruppe(t, ids...)
	rnd := rand.New(rand.NewSource(seed))
	still := map[string]int{}
	step := func(int) {
		for k, id := range ids {
			if still[id] > 0 {
				still[id]--
				delete(g.level, id)
				continue
			}
			if rnd.Float64() < pause {
				still[id] = 1 + rnd.Intn(4)
			}
			b := band[k]
			g.level[id] = b[0] + uint32(rnd.Intn(int(b[1]-b[0]+1)))
		}
	}
	g.tonLauf(5*time.Second, "", step)
	g.me.aSwitch.Store(0)
	n, _ := g.tonLauf(60*time.Second, "", step)
	if a := int(g.me.ASwitch()); a != n {
		t.Errorf("aswitch %d, gezaehlt %d", a, n)
	}
	return n, g.ton()
}

// TestTop3HystereseVierSprecher: vier gleichzeitig Sprechende mit aehnlichem, schwankendem Pegel - hoechstens 6 Wechsel
// je Minute auf den Ton-Plaetzen (Plan 2263-3); sechs mit Gemurmel und Atempausen hoechstens 12 (eine Atempause von
// 0,8 s laesst die Aktivitaet so weit sinken, dass ein Neuer 1,5 s lang 1,4-mal so aktiv ist - nach Vertrag ein
// Wechsel). Je 5 Saaten. Gemessen (2.6): 4 gleich laut 0, mit Atempausen 1-3, gestaffelt 0-1, 6 Gemurmel 3-8 je
// Minute; mit dem Redewechsel 2432 (Onset/audioTalk): 0, 2-5, 0-2, 6-12. Vorher (2.5, abe8892, dieselbe Simulation): 106-119, 40-60, 30-37, 101-136.
func TestTop3HystereseVierSprecher(t *testing.T) {
	cases := []struct {
		name  string
		band  [][2]uint32
		pause float64
		max   int
	}{
		{"4 gleich laut", [][2]uint32{{30, 50}, {30, 50}, {30, 50}, {30, 50}}, 0, 6},
		{"4 mit Atempausen", [][2]uint32{{30, 50}, {30, 50}, {30, 50}, {30, 50}}, 0.05, 6},
		{"4 gestaffelt", [][2]uint32{{20, 40}, {30, 50}, {35, 55}, {40, 60}}, 0.03, 6},
		{"6 Gemurmel", [][2]uint32{{40, 70}, {40, 70}, {40, 70}, {40, 70}, {40, 70}, {40, 70}}, 0.05, 12},
	}
	for _, c := range cases {
		for seed := int64(1); seed <= 5; seed++ {
			n, end := vierSprecher(t, seed, c.band, c.pause)
			t.Logf("%s (Saat %d): %d Wechsel/min, am Ende %s", c.name, seed, n, end)
			if n > c.max {
				t.Errorf("%s (Saat %d): %d Wechsel in 60 s (hoechstens %d)", c.name, seed, n, c.max)
			}
		}
	}
}

// TestTonPlaetzeHoechstens3: Limits.Audio ist fest 3 (Vertrag "Top-3-Ton" 1) - mehr angefordert (Want.Audio bzw.
// Limits.Audio des Raums) wird gekappt.
func TestTonPlaetzeHoechstens3(t *testing.T) {
	n := NewNode(Config{Name: "k", Tick: time.Hour})
	defer n.Close()
	r, err := n.CreateRoom("raum3", &Limits{Audio: 8})
	if err != nil {
		t.Fatal(err)
	}
	for i, want := range []int{3, 4, 8, 1} {
		l, err := r.AddLeg(Grant{Room: "raum3", Peer: fmt.Sprintf("p%d", i), Leg: fmt.Sprintf("l%d", i), Role: RoleAudio},
			PathTCP, Want{Audio: want})
		if err != nil {
			t.Fatal(err)
		}
		exp := min(want, MaxAudio)
		if got := len(l.Outs()); got != exp {
			t.Fatalf("Want.Audio %d: %d Ton-Plaetze, erwartet %d", want, got, exp)
		}
	}
	if (Limits{}).withDefaults().Audio != 3 || (Limits{Audio: 5}).withDefaults().Audio != 3 {
		t.Fatal("Limits.Audio nicht auf 3 gekappt")
	}
}

// TestHoldAudioRegeln: die Regel ohne Raum (wie sie die Bruecke nutzt), Takt fuer Takt.
func TestHoldAudioRegeln(t *testing.T) {
	sc := map[string]float64{}
	score := func(k string) float64 { return sc[k] }
	wait := map[string]int64{}
	ms := int64(time.Millisecond)
	// frei: sofort
	out := HoldAudio([]string{"", "", ""}, []string{"a", "b"}, score, wait, 0)
	if out[0] != "a" || out[1] != "b" || out[2] != "" {
		t.Fatalf("frei: %v", out)
	}
	// alle sprechen, Neuer 1,39x: nie
	sc["a"], sc["b"], sc["c"], sc["d"] = 0.5, 0.4, 0.3, 0.417
	cur := []string{"a", "b", "c"}
	for now := int64(0); now < 5000*ms; now += 200 * ms {
		if out = HoldAudio(cur, []string{"a", "d", "b", "c"}, score, wait, now); out[2] != "c" {
			t.Fatalf("1,39x verdraengt bei %d ms: %v", now/ms, out)
		}
	}
	// 1,4x (0,43 gegen 0,3): erst nach 1,5 s, Unterbrechung setzt die Zeit zurueck
	sc["d"] = 0.43
	for now := int64(0); now <= 1400*ms; now += 200 * ms {
		if out = HoldAudio(cur, []string{"a", "d", "b", "c"}, score, wait, now); out[2] != "c" {
			t.Fatalf("vor 1,5 s verdraengt (%d ms): %v", now/ms, out)
		}
	}
	sc["d"] = 0.41
	HoldAudio(cur, []string{"a", "d", "b", "c"}, score, wait, 1600*ms)
	if _, ok := wait["d"]; ok {
		t.Fatal("Wartezeit nicht zurueckgesetzt")
	}
	sc["d"] = 0.43
	for now := int64(1800); now < 3300; now += 200 {
		if out = HoldAudio(cur, []string{"a", "d", "b", "c"}, score, wait, now*ms); out[2] != "c" {
			t.Fatalf("nach Unterbrechung zu frueh verdraengt (%d ms): %v", now, out)
		}
	}
	if out = HoldAudio(cur, []string{"a", "d", "b", "c"}, score, wait, 3400*ms); out[0] != "a" || out[1] != "b" || out[2] != "d" {
		t.Fatalf("nach 1,6 s mit 1,4x: %v (erwartet d auf dem Platz von c)", out)
	}
	// stiller Inhaber weicht einem Sprechenden sofort, einem Stillen nicht
	wait = map[string]int64{}
	sc["c"], sc["d"], sc["e"] = 0.01, 0, 0.05
	if out = HoldAudio(cur, []string{"a", "b", "c", "d"}, score, wait, 0); out[2] != "c" {
		t.Fatalf("stiller Neuer verdraengt stillen Inhaber: %v", out)
	}
	if out = HoldAudio(cur, []string{"a", "b", "e", "c", "d"}, score, wait, 0); out[2] != "e" {
		t.Fatalf("Sprechender bekommt den stillen Platz nicht: %v", out)
	}
	// wer weg ist, gibt den Platz frei - auch an einen Stillen
	if out = HoldAudio(cur, []string{"a", "c", "d"}, score, wait, 0); out[0] != "a" || out[1] != "d" || out[2] != "c" {
		t.Fatalf("Platz von b: %v (erwartet [a d c])", out)
	}
}

// TestTop3Redewechsel: Alle drei Inhaber sprachen, einer hoert auf und im selben Takt beginnt ein Neuer (Redewechsel in
// einer Runde mit vier und mehr). Nach Vertrag ist der Verstummte noch "sprechend", bis seine gleitende Aktivitaet unter
// speakOn faellt - der Neue kommt ueber die Regel 1,4x/1,5 s bzw. den stillen Platz dran, nicht sofort. Gemessen (2.6):
// 2,4 s (Pegel 40 bei beiden); vorher (2.5, abe8892): 0,8 s (Platz 3 ging an den lautesten). Die ersten Worte des Neuen hoert in
// dieser Zeit niemand - Kosten der Hysterese, siehe Bericht 2263-3.
func TestTop3Redewechsel(t *testing.T) {
	g := tonGruppe(t, "a", "b", "c", "d")
	g.level = map[string]uint32{"a": 40, "b": 40, "c": 40}
	g.run(5 * time.Second)
	delete(g.level, "c")
	g.level["d"] = 40
	n, first := g.tonLauf(5*time.Second, "d", nil)
	t.Logf("Redewechsel c -> d: d nach %d Takten (%s) auf dem Platz, %d Wechsel", first, time.Duration(first)*200*time.Millisecond, n)
	// 2.6 (Bugs-DB 2432): c gilt nach audioQuiet (0,6 s) ohne Sprachpaket als still -> d nach 3-5 Takten (vorher bis 13)
	if first < 0 || first > 5 || n != 1 || hat(g.ton(), "c") {
		t.Fatalf("Redewechsel: d ab Takt %d (hoechstens 5 = 1,0 s), %d Wechsel, Plaetze %v", first, n, g.ton())
	}
}

// 2.6 (Bugs-DB 2432): Atempause unter audioQuiet - der Inhaber behaelt seinen Platz, obwohl ein Vierter spricht.
func TestTop3Atempause(t *testing.T) {
	g := tonGruppe(t, "a", "b", "c", "d")
	g.level = map[string]uint32{"a": 40, "b": 40, "c": 40}
	g.run(5 * time.Second)
	g.level["d"] = 40
	n, first := g.tonLauf(4*time.Second, "d", func(i int) {
		if i%6 == 1 || i%6 == 2 { // c macht alle 1,2 s eine Pause von 2 Takten (0,4 s)
			delete(g.level, "c")
		} else {
			g.level["c"] = 40
		}
	})
	t.Logf("Atempausen c (0,4 s): d ab Takt %d, %d Wechsel, Plaetze %v", first, n, g.ton())
	if !hat(g.ton(), "c") || n > 1 {
		t.Fatalf("Atempause 0,4 s: c verliert den Platz (%d Wechsel, Plaetze %v)", n, g.ton())
	}
}

func TestAudioActivity(t *testing.T) {
	now := int64(10 * time.Second)
	ms := func(d int) int64 { return now - int64(time.Duration(d)*time.Millisecond) }
	if AudioActivity(0.3, 0, now, now) != 0.3 || AudioActivity(0.3, ms(500), now, now) != 0.3 ||
		AudioActivity(0.3, ms(600), ms(100), now) != 0 || AudioActivity(0.01, now, now, now) != 0.01 ||
		AudioActivity(0.3, ms(600), ms(700), now) != 0.3 { // Neuer sprach schon vor der Pause: kein Redewechsel
		t.Fatal("AudioActivity")
	}
}
