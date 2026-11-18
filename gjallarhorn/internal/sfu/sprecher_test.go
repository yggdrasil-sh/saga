package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Bug 2153 (Labor 18.11., Container-Messreihe rang345): Im 4er ueber den Knoten wechselte bei der Anrufenden der Empfang
// 17-19-mal je Minute die Schicht (die anderen <= 1,7), mit nur einer Schicht 14 statt 20 fps und bis 21 Einfrierer/min
// - abhaengig von der Rolle, nicht vom Rechner. Die Spur des Knotens (HORN_SPUR) zeigte: Die Anrufende ist
// Hauptsprecherin (sie spricht zuerst; die anderen sind nicht 1,4-mal lauter), vor ihr war niemand Hauptsprecher. Fuer
// sie selbst gilt "der vorige" - den gibt es nicht, also nahm der Sprecherplatz jeden Takt den zuletzt Sprechenden
// (byRecent). Senden alle Ton (im Messaufbau ein Dauerton, im Gespraech Hintergrund), wechselt der zuletzt Sprechende
// staendig: Sprecherplatz und Kacheln bekamen alle paar Sekunden neue Besitzer, jedes Mal mit Schluesselbild und Beginn
// auf l.
//
// Modell: Empfaenger (eigenes Bein mit Ton, spricht zuerst und am lautesten) und drei Teilnehmende ueber eine Bruecke,
// die danach alle gleich laut sprechen; wer zuletzt ein Sprachpaket hatte, wechselt reihum je Takt.

// sprecherLauf: n Takte mit reihum wechselnder letzter Sprache; zaehlt Besitzerwechsel des Sprecherplatzes.
func sprecherLauf(g *gruppe, mine *fwd.Source, ids []string, lvl map[string]uint32, myLvl uint32, d time.Duration) (changes int, seq string) {
	last, _ := g.spk()
	seq = last
	i := 0
	for end := g.now + int64(d); g.now < end; i++ {
		next := g.now + int64(200*time.Millisecond)
		for k, id := range ids {
			a := g.aud[id]
			lv, ok := lvl[id]
			if !ok || lv >= 127 {
				continue
			}
			a.VoicePkts.Add(5)
			a.LevelSum.Add(5 * (127 - lv))
			// reihum: in jedem Takt kam das letzte Sprachpaket von einem anderen
			a.LastVoice.Store(next - int64((k+i)%len(ids))*int64(time.Millisecond))
		}
		if myLvl < 127 {
			mine.VoicePkts.Add(5)
			mine.LevelSum.Add(5 * (127 - myLvl))
			mine.LastVoice.Store(next)
		}
		g.tick()
		if o, _ := g.spk(); o != last {
			changes++
			last = o
			if len(seq) < 60 {
				seq += ">" + o
			}
		}
	}
	return changes, seq
}

func sprecherGruppe(t *testing.T) (*gruppe, *fwd.Source) {
	g := newGruppe(t)
	g.me.Grant.Pub = PubAudio // der Empfaenger spricht selbst
	mine, err := g.me.AddSource(fwd.Audio, "a", false, 7001, 1, nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"a", "b", "c"} {
		g.dazu(id, true)
	}
	return g, mine
}

// TestSprecherplatzHauptsprecherOhneVorigen: Die Hauptsprecherin ohne vorigen Hauptsprecher behaelt ihren Sprecherplatz,
// waehrend alle anderen zugleich (gleich laut) sprechen. Vorher (Stand 1276701): Besitzer wechselte in 60 s 299-mal.
func TestSprecherplatzHauptsprecherOhneVorigen(t *testing.T) {
	g, mine := sprecherGruppe(t)
	// sie spricht zuerst allein: Hauptsprecherin, ohne vorigen
	if n, _ := sprecherLauf(g, mine, nil, nil, 20, 3*time.Second); n > 1 {
		t.Fatalf("Anlauf: %d Wechsel", n)
	}
	g.r.mu.Lock()
	dom, prev := g.r.dominant, g.r.prevDom
	g.r.mu.Unlock()
	if dom != g.me.Peer || prev != nil {
		t.Fatalf("Aufbau: Hauptsprecher %v, voriger %v", dom, prev)
	}
	lvl := map[string]uint32{"a": 40, "b": 40, "c": 40}
	n, seq := sprecherLauf(g, mine, []string{"a", "b", "c"}, lvl, 20, 60*time.Second)
	t.Logf("Wechsel des Sprecherplatzes in 60 s: %d (%s)", n, seq)
	if n > 0 {
		t.Fatalf("Sprecherplatz wechselt %d-mal, obwohl niemand deutlich lauter spricht: %s", n, seq)
	}
	// eine spricht deutlich lauter als der bisherige Besitzer: nach domHold bekommt sie den Platz (einmal)
	o, _ := g.spk()
	other := "a"
	if o == "a" {
		other = "b"
	}
	lvl[other] = 5
	n, seq = sprecherLauf(g, mine, []string{"a", "b", "c"}, lvl, 20, 10*time.Second)
	if o2, _ := g.spk(); o2 != other || n != 1 {
		t.Fatalf("lautere Person %q: Besitzer %q nach %d Wechseln (%s)", other, o2, n, seq)
	}
}

// TestSprecherplatzVorigerBleibt: unveraendert - wird jemand anderes Hauptsprecher, zeigt der Platz bei der vorigen
// Hauptsprecherin den neuen (sofort, ohne die Regel von spkFallback).
func TestSprecherplatzVorigerBleibt(t *testing.T) {
	g, mine := sprecherGruppe(t)
	sprecherLauf(g, mine, nil, nil, 20, 3*time.Second)
	if o, _ := g.spk(); o != "a" {
		t.Fatalf("Aufbau: Sprecherplatz %q (erwartet a, zuerst beigetreten)", o)
	}
	// b spricht deutlich lauter und wird Hauptsprecher; sie selbst schweigt: bei ihr zeigt der Platz b
	n, seq := sprecherLauf(g, mine, []string{"b"}, map[string]uint32{"b": 5}, 127, 5*time.Second)
	g.r.mu.Lock()
	dom, prev := g.r.dominant, g.r.prevDom
	g.r.mu.Unlock()
	if dom == nil || dom.ID != "b" || prev != g.me.Peer {
		t.Fatalf("Hauptsprecher %v, voriger %v", dom, prev)
	}
	if o, _ := g.spk(); o != "b" || n != 1 {
		t.Fatalf("neuer Hauptsprecher b: Besitzer %q nach %d Wechseln (%s)", o, n, seq)
	}
}
