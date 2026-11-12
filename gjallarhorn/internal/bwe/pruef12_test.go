package bwe

import (
	"math/rand"
	"testing"
)

// Pruefung von HORN_BWE=eigen (Stufe 2): Gegenbeispiele zur Widerlegung (Tip.Clear). Gefragt ist jeweils, wie lange
// die Spitze nach dem Beginn ECHTER Enge noch "kein Stau" sagt - so lange gaelte ein Stausignal von GCC nicht.

// clearNach liefert, wie lange nach t0 die Spitze zuletzt widerlegt hat (s, im 20-ms-Raster) und in welchem Anteil
// der Abfragen der ersten sec Sekunden.
func clearNach(l *leitung, sec float64) (last, share float64) {
	t0 := l.now
	yes, n := 0, 0
	for l.now < t0+sec {
		l.run(0.02)
		n++
		if l.d.Tip(int64(l.now * 1e9)).Clear {
			yes++
			last = l.now - t0
		}
	}
	return last, float64(yes) / float64(n)
}

// Deutliche Enge jeder Bauart beendet die Widerlegung schnell: Policer ohne Puffer (nur Verlust), kurzer Puffer,
// tiefe Schlange - ab 8 % Ueberlast widerlegt die Spitze in den ersten 3 s (in denen GCC meldet) nur ganz zu Beginn.
// Ausgegeben wird dazu der Anteil ueber 15 s: Bei kurzem Puffer steht der Verlust nach rund 10 s im Vergleichswert,
// und die Spitze widerlegt wieder (Grenze, siehe TestPruefSpitzeGrenzen).
func TestPruefSpitzeDeutlicheEnge(t *testing.T) {
	for _, c := range []struct {
		name     string
		cap, buf float64 // Anteil der Rate; ms
	}{
		{"Policer ohne Puffer, 20 % unter der Rate", 0.80, 1}, {"Policer ohne Puffer, 10 % unter", 0.90, 1}, {"Policer ohne Puffer, 8 % unter", 0.92, 1},
		{"Puffer 5 ms, 10 % unter", 0.90, 5}, {"Puffer 8 ms, 8 % unter", 0.92, 8}, {"Puffer 300 ms, 8 % unter", 0.92, 300},
	} {
		worst, worstShare, worstSpaet := 0.0, 0.0, 0.0
		for seed := int64(1); seed <= 10; seed++ {
			l := newLeitung(seed, 190, 1000)
			l.jitMs, l.fbMs = 3, 50+float64(seed%2)*50
			l.run(12 + float64(seed)*0.013)
			l.capBps, l.bufMs = c.cap*190*8000, c.buf
			last, share := clearNach(l, 3)
			_, spaet := clearNach(l, 12)
			worst, worstShare, worstSpaet = max(worst, last), max(worstShare, share), max(worstSpaet, spaet)
		}
		t.Logf("%s: Spitze widerlegt in den ersten 3 s laengstens bis %.2f s nach Beginn (hoechstens %.1f %% der Abfragen), in den 12 s danach bis zu %.0f %% (10 Laeufe)",
			c.name, worst, worstShare*100, worstSpaet*100)
		if worstShare > 0.15 {
			t.Fatalf("%s: widerlegt in %.1f %% der ersten 3 s (zuletzt nach %.2f s)", c.name, worstShare*100, worst)
		}
	}
}

// Grenzen (nur Ausgabe mit -v, haelt den Stand fest): Enge, die die Spitze NICHT sieht - die Widerlegung gilt weiter,
// ein Stausignal von GCC wuerde bis zu lagMax (15 s) uebergangen.
//   - kurzer Puffer (unter TipQueueUs = 10 ms) und Ueberlast bis etwa 4 % (Verlust hoechstens TipLossUp ueber dem der
//     10 s davor)
//   - niedrige Paketrate: Verlust zaehlt an der Spitze erst ab zwei verlorenen Paketen in 500 ms
//   - Verlust, der schon laenger als rund 10 s anhaelt (er steht dann im Vergleichswert)
func TestPruefSpitzeGrenzen(t *testing.T) {
	for _, c := range []struct {
		name     string
		pps      int
		cap, buf float64
		pre      float64 // Zufallsverlust schon vor der Enge
	}{
		{"190 Pakete/s, Puffer 5 ms, 2 % unter der Rate", 190, 0.98, 5, 0},
		{"190 Pakete/s, Puffer 5 ms, 4 % unter", 190, 0.96, 5, 0},
		{"190 Pakete/s, Puffer 5 ms, 6 % unter", 190, 0.94, 5, 0},
		{"190 Pakete/s, Puffer 8 ms, 4 % unter", 190, 0.96, 8, 0},
		{"190 Pakete/s, Policer ohne Puffer, 4 % unter", 190, 0.96, 1, 0},
		{"190 Pakete/s, Policer ohne Puffer, 6 % unter", 190, 0.94, 1, 0},
		{"190 Pakete/s, Policer 10 % unter, davor schon 5 % Zufallsverlust", 190, 0.90, 1, 0.05},
		{"190 Pakete/s, Puffer 300 ms, 1 % unter (Schlange waechst 10 ms/s)", 190, 0.99, 300, 0},
		{"190 Pakete/s, Puffer 300 ms, 3 % unter", 190, 0.97, 300, 0},
		{"25 Pakete/s (l ohne Ton), Policer 10 % unter", 25, 0.90, 1, 0},
		{"25 Pakete/s, Policer 20 % unter", 25, 0.80, 1, 0},
		{"25 Pakete/s, Policer 40 % unter", 25, 0.60, 1, 0},
		{"25 Pakete/s, Puffer 300 ms, 20 % unter", 25, 0.80, 300, 0},
		{"70 Pakete/s (m), Policer 10 % unter", 70, 0.90, 1, 0},
		{"70 Pakete/s, Policer 20 % unter", 70, 0.80, 1, 0},
	} {
		worst, sum := 0.0, 0.0
		st := map[State]int{}
		for seed := int64(1); seed <= 10; seed++ {
			l := newLeitung(seed, c.pps, 1000)
			l.jitMs, l.fbMs, l.loss = 3, 50+float64(seed%2)*50, c.pre
			l.run(12 + float64(seed)*0.013)
			l.capBps, l.bufMs = c.cap*float64(c.pps)*8000, c.buf
			last, share := clearNach(l, 15)
			worst, sum = max(worst, last), sum+share
			st[l.state()]++
		}
		t.Logf("%s: Spitze widerlegt bis %.2f s nach Beginn, im Mittel %.0f %% der ersten 15 s; Zustand danach %v", c.name, worst, sum*10, st)
	}
}

// Seltene bzw. verspaetete Rueckmeldungen: Die Spitze widerlegt nur mit frischer Rueckmeldung (TipAgeNs 400 ms) -
// sonst gilt GCC wie bisher. Auf freier Leitung heisst das: Mit Rueckmeldungen alle 500 ms widerlegt sie kaum noch.
func TestPruefSpitzeRueckmeldung(t *testing.T) {
	for _, fb := range []float64{50, 100, 250, 400, 500, 1000} {
		l := newLeitung(1, 190, 1000)
		l.jitMs, l.fbMs = 3, fb
		l.run(12)
		_, frei := clearNach(l, 30)
		l.capBps = 0.8 * 190 * 8000
		last, _ := clearNach(l, 10)
		t.Logf("Rueckmeldung alle %v ms: freie Leitung widerlegt in %.0f %% der Abfragen; nach Beginn der Enge (20 %% unter) noch bis %.2f s", fb, frei*100, last)
		if last > 1.0 {
			t.Fatalf("Rueckmeldung alle %v ms: widerlegt noch %.2f s nach Beginn der Enge", fb, last)
		}
	}
}

// Wegwechsel: Nach dem Zuruecksetzen der Basis widerlegt die Spitze BaseYoungNs lang nichts - auch nicht, wenn der
// neue Weg frei ist; und eine Enge, die mit dem Wechsel beginnt, geht nicht in der Widerlegung unter.
func TestPruefSpitzeReset(t *testing.T) {
	for _, eng := range []bool{false, true} {
		l := newLeitung(1, 190, 1000)
		l.jitMs = 3
		l.run(12)
		l.d.Reset(int64(l.now * 1e9))
		l.baseMs = 60
		if eng {
			l.capBps = 0.9 * 190 * 8000
		}
		t0 := l.now
		first := -1.0
		for l.now < t0+6 {
			l.run(0.02)
			if l.d.Tip(int64(l.now*1e9)).Clear && first < 0 {
				first = l.now - t0
			}
		}
		t.Logf("Reset, neuer Weg (eng=%v): Spitze widerlegt erstmals nach %.2f s (-1 = nie in 6 s)", eng, first)
		if !eng && (first < 1.9 || first > 3) || eng && first >= 0 {
			t.Fatalf("eng=%v: erstmals nach %.2f s", eng, first)
		}
	}
}

// Dauer-Doppel plus echter Verlust: Jedes zweite Paket ist ein Doppel und bleibt unbestaetigt (kein Verlust); setzt
// daneben echter Verlust ein (Policer, 30 % der Originale), sieht ihn der Detektor unverduennt - Warnung und Stau wie
// ohne Doppel (bei 100 Originalen/s: Warnung nach gut 1 s, Stau nach rund 3 s), die Spitze widerlegt nicht mehr.
func TestPruefDoppelPlusVerlust(t *testing.T) {
	m := NewMitleser(Defaults())
	var chg []Change
	m.SetOnChange(func(c Change) { chg = append(chg, c) })
	rng := rand.New(rand.NewSource(5))
	seq := uint16(100)
	now := int64(0)
	var pend []rep
	base := seq
	tEng := int64(20e9)
	clearLast := int64(-1)
	for i := 0; i < 26*100; i++ {
		now += 10e6
		st := StRecv
		if now >= tEng && rng.Float64() < 0.3 {
			st = StLost
		}
		m.Sent(now, seq, 1000, KindVideo)
		pend = append(pend, rep{seq, st, now/1000 + 20_000 + int64(rng.Intn(4000))})
		seq++
		m.Sent(now+1e6, seq, 1000, KindDup)
		pend = append(pend, rep{seq, StLost, 0})
		seq++
		if len(pend) >= 20 && pend[len(pend)-2].st == StRecv {
			// die Meldung endet mit einem angekommenen Paket (dem letzten Original)
			k := len(pend) - 1
			m.Feedback(now+30e6, fbOf(base, uint32(pend[0].us/refUnit), pend[:k]))
			base += uint16(k)
			pend = append(pend[:0], pend[k:]...)
		}
		if now >= tEng && m.Tip(now+30e6).Clear {
			clearLast = now - tEng
		}
	}
	c := m.Counters()
	var warn, cong int64 = -1, -1
	for _, ch := range chg {
		if ch.To == Warn && warn < 0 && ch.AtNs >= tEng {
			warn = ch.AtNs - tEng
		}
		if ch.To == Congested && cong < 0 {
			cong = ch.AtNs - tEng
		}
	}
	t.Logf("Doppel + 30 %% echter Verlust: verloren %d, Doppel unbestaetigt %d; Warnung nach %.2f s, Stau nach %.2f s, Spitze widerlegt zuletzt %.2f s nach Beginn",
		c.Lost, c.DupUnacked, float64(warn)/1e9, float64(cong)/1e9, float64(clearLast)/1e9)
	if c.Lost < 120 || warn < 0 || cong < 0 || cong > 3500e6 || clearLast > 300e6 {
		t.Fatalf("zaehler %+v, warnung %d, stau %d, widerlegt bis %d", c, warn, cong, clearLast)
	}
}
