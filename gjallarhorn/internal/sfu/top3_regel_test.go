package sfu

import (
	"fmt"
	"math/rand"
	"testing"
	"time"
)

// 2263-3 Pruefung: HoldAudio gegen die Vertragsregeln (docs/APP-API.md "Top-3-Ton" 3/4) mit zufaelligen Verlaeufen -
// unabhaengig von der Umsetzung nachgerechnet, Takt fuer Takt:
//   - kein Inhaber doppelt, nur Anwaerter auf den Plaetzen, ein Inhaber behaelt seinen Platz (Index);
//   - kein freier Platz, solange ein Anwaerter ohne Platz ist;
//   - ein stiller Neuer verdraengt niemanden;
//   - ein sprechender Inhaber weicht nur einem Neuen, der in jedem Takt der letzten audioHold mindestens
//     audioFactor-mal so aktiv war wie der jeweils leiseste Inhaber (bzw. ein Platz frei oder still war und in dem Takt
//     neu belegt wurde - dann vergleicht HoldAudio mit der neuen Belegung).
func TestHoldAudioVertragZufall(t *testing.T) {
	const tick = int64(200 * time.Millisecond)
	for seed := int64(1); seed <= 40; seed++ {
		rnd := rand.New(rand.NewSource(seed))
		ids := []string{}
		for i := 0; i < 3+rnd.Intn(5); i++ {
			ids = append(ids, fmt.Sprintf("p%d", i))
		}
		sc := map[string]float64{}
		score := func(k string) float64 { return sc[k] }
		wait := map[string]int64{}
		cur := []string{"", "", ""}
		since := map[string]int64{} // erster Takt der laufenden Bedingung (Neuer >= 1,4x leisester, alle sprechen)
		for i := 0; i < 600; i++ {
			now := int64(i) * tick
			// Pegel: je Person zufaellig sprechend/still, gelegentlich Wechsel
			for _, id := range ids {
				switch r := rnd.Float64(); {
				case r < 0.08:
					sc[id] = 0
				case r < 0.16:
					sc[id] = 0.04 + rnd.Float64()*0.6
				case r < 0.5:
					sc[id] *= 0.8 + rnd.Float64()*0.4
				}
			}
			// Anwesende (gelegentlich fehlt jemand)
			var cand []string
			for _, id := range ids {
				if rnd.Float64() > 0.03 {
					cand = append(cand, id)
				}
			}
			ps := make([]*Peer, len(cand))
			for k, id := range cand {
				ps[k] = &Peer{ID: id, score: sc[id]}
			}
			byActivity(ps)
			for k, p := range ps {
				cand[k] = p.ID
			}
			in := map[string]bool{}
			for _, c := range cand {
				in[c] = true
			}
			// Bedingung vor dem Aufruf, mit den Inhabern, die bleiben duerfen
			held := map[string]bool{}
			allSpeak, minS, full := true, 0.0, true
			first := true
			for _, k := range cur {
				if k == "" || !in[k] {
					full = false
					continue
				}
				held[k] = true
				if sc[k] < speakOn {
					allSpeak = false
				}
				if first || sc[k] < minS {
					minS, first = sc[k], false
				}
			}
			// Ist vor dem Aufruf ein Platz frei oder still, wird er in diesem Takt neu belegt, und HoldAudio vergleicht
			// die weiteren Neuen schon mit der neuen Belegung: dann gilt die Bedingung hier als erfuellt (nur
			// eindeutige Verstoesse fallen auf).
			for _, c := range cand {
				switch {
				case held[c] || sc[c] < speakOn || full && allSpeak && sc[c] < minS*audioFactor:
					delete(since, c)
				default:
					if _, ok := since[c]; !ok {
						since[c] = now
					}
				}
			}
			out := HoldAudio(cur, cand, score, wait, now)
			// Regeln
			seen := map[string]bool{}
			free := 0
			for k, o := range out {
				if o == "" {
					free++
					continue
				}
				if seen[o] || !in[o] {
					t.Fatalf("Saat %d Takt %d: Platz %d = %q (doppelt oder nicht da), %v -> %v", seed, i, k, o, cur, out)
				}
				seen[o] = true
				if held[o] && cur[k] != o {
					t.Fatalf("Saat %d Takt %d: Inhaber %s hat den Platz gewechselt %v -> %v", seed, i, o, cur, out)
				}
			}
			if free > 0 && len(seen) < len(cand) {
				t.Fatalf("Saat %d Takt %d: freier Platz trotz Wartender %v -> %v (cand %v)", seed, i, cur, out, cand)
			}
			for k, o := range out {
				old := cur[k]
				if !held[old] || o == old {
					continue
				}
				// old wurde verdraengt durch o
				if sc[o] < speakOn {
					t.Fatalf("Saat %d Takt %d: stiller %s verdraengt %s", seed, i, o, old)
				}
				if sc[old] >= speakOn {
					st, ok := since[o]
					if !ok || now-st < int64(audioHold) {
						t.Fatalf("Saat %d Takt %d: sprechender %s (%.3f) verdraengt durch %s (%.3f) ohne 1,4x ueber 1,5 s (seit %v)",
							seed, i, old, sc[old], o, sc[o], ok)
					}
					for _, k := range cur { // vorher weichen die Stillen
						if held[k] && sc[k] < speakOn && seen[k] {
							t.Fatalf("Saat %d Takt %d: sprechender %s weicht %s, stiller %s bleibt (%v -> %v)", seed, i, old, o, k, cur, out)
						}
					}
				}
			}
			cur = out
		}
	}
}
