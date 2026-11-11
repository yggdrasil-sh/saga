package sfu

import (
	"fmt"
	"testing"
	"time"
)

// zahlRun: ein Modell-Lauf fuer die Kennzahlen-Tabelle - Anteil auf h, laengste Phase ohne h, Zaehler.
type zahlRun struct {
	probeRun
	share   float64       // Anteil der Zeit, in der der Sprecherplatz auf h steht
	longest time.Duration // laengste Phase ohne h
}

func (z zahlRun) String() string {
	return fmt.Sprintf("h %3.0f %%, laengste Phase ohne h %6.1f s | %v", 100*z.share, z.longest.Seconds(), z.probeRun)
}

// zahlen faehrt das Modell d lang (wie measure) und haelt dazu fest, wie lange der Sprecherplatz nicht auf h stand.
func (er *estRoom) zahlen(d time.Duration, step func(el time.Duration)) zahlRun {
	var z zahlRun
	var cur, on time.Duration
	const dt = 200 * time.Millisecond
	z.probeRun = er.measure(d, func(el time.Duration) {
		if el > 0 { // Stand nach dem vorigen Takt
			if er.layer() == "h" {
				cur, on = 0, on+dt
			} else if cur += dt; cur > z.longest {
				z.longest = cur
			}
		}
		if step != nil {
			step(el)
		}
	})
	z.share = float64(on) / float64(d)
	return z
}

// bis faehrt das Modell, bis der Sprecherplatz auf want steht (hoechstens max); ohne Abbruch des Tests.
func (er *estRoom) bis(want string, max time.Duration) string {
	start := er.now
	for er.now-start < int64(max) {
		er.tick()
		if er.layer() == want {
			return fmt.Sprintf("%s nach %.1f s", want, time.Duration(er.now-start).Seconds())
		}
	}
	return fmt.Sprintf("%s nicht in %v", want, max)
}

// voll: Zeit auf der haeufigsten und auf der vollen Belegung (h und sonst m) aus probeRun.seen.
func voll(p probeRun) string {
	best, full := "", time.Duration(0)
	for k, d := range p.seen {
		if best == "" || d > p.seen[best] || d == p.seen[best] && k < best {
			best = k
		}
		ok := len(k) > 0 && k[0] == 'h'
		for _, c := range k[1:] {
			ok = ok && c == 'm'
		}
		if ok {
			full += d
		}
	}
	return fmt.Sprintf("volle Belegung %.0f s, am haeufigsten %q %.0f s", full.Seconds(), best, p.seen[best].Seconds())
}

// schuebe: Stau-Schuebe von 2 s im Abstand gaps (reihum) - das GCC-Ziel springt zurueck, der Platz faellt von h.
func schuebe(er *estRoom, gaps ...time.Duration) func(time.Duration) {
	next, k := gaps[0], 0
	return func(el time.Duration) {
		if el >= next+2*time.Second {
			k++
			next = el + gaps[k%len(gaps)]
		}
		er.loss = el >= next
	}
}

// TestProbingZahlen: Kennzahlen des Modells mit und ohne Probing fuer die Faelle der Laborrunden (nur Ausgabe mit -v;
// die Schranken stehen in den einzelnen Tests). Freie Leitung, Anrufbeginn und "eng, dann frei" mit dem pion-nahen
// Ziel-Regler (estRoom.pion: Klemmung an die Empfangsrate), die uebrigen mit dem einfachen Modell.
func TestProbingZahlen(t *testing.T) {
	for _, on := range []bool{false, true} {
		name := map[bool]string{false: "ohne Probing", true: "mit Probing "}[on]
		// freie Leitung, 10 min, Stau-Schuebe im Abstand 45 bis 90 s (Labor: rund ein Rueckfall je Minute)
		er := newEstCfg(t, 1250e3, 810e3, 1, on)
		er.pion = true
		t.Logf("%s | Anrufbeginn frei: %s", name, er.bis("h", 30*time.Second))
		t.Logf("%s | frei, Schuebe alle 45-90 s: %v", name, er.zahlen(10*time.Minute, schuebe(er, 45*time.Second, 70*time.Second, 55*time.Second, 90*time.Second)))
		// freie Leitung, Schuebe dicht (10 bis 30 s): h haelt selten 30 s
		er = newEstCfg(t, 1250e3, 810e3, 1, on)
		er.pion = true
		er.bis("h", 30*time.Second)
		t.Logf("%s | frei, Schuebe alle 10-30 s: %v", name, er.zahlen(10*time.Minute, schuebe(er, 10*time.Second, 30*time.Second, 17*time.Second, 23*time.Second)))
		// eng (1,0 Mbit/s, 4 min), dann frei
		er = newEstCfg(t, 1250e3, 810e3, 1, on)
		er.pion = true
		er.link = 1000e3
		er.bis("m", 2*time.Second)
		tight := er.zahlen(4*time.Minute, nil)
		er.link = 0
		back := er.bis("h", upDwellMax+time.Minute)
		t.Logf("%s | eng 4 min: %v", name, tight)
		t.Logf("%s | ... dann frei: %s; danach 2 min: %v", name, back, er.zahlen(2*time.Minute, nil))
		// enge Leitung 10 min
		for _, link := range []float64{1000e3, 1250e3} {
			for _, pion := range []bool{false, true} {
				er = newEstCfg(t, 1250e3, 810e3, 1, on)
				er.pion, er.link = pion, link
				er.bis("m", 2*time.Second)
				t.Logf("%s | eng %.0f kbit/s (pion-Regler %v): %v", name, link/1000, pion, er.zahlen(10*time.Minute, nil))
			}
		}
		// Gruppe 4 Sender, 1,7 Mbit/s
		er = newEstCfg(t, 1250e3, 810e3, 4, on)
		er.link = 1700e3
		er.tick()
		g := er.measure(10*time.Minute, nil)
		t.Logf("%s | Gruppe 4, 1,7 Mbit/s: %v, am Ende %q, %s", name, g, er.video(), voll(g))
		// Gruppe 4 Sender mit Sprecherwechsel alle 10 s, 1,7 und 1,0 Mbit/s
		for _, link := range []float64{1700e3, 1000e3} {
			er = newEstCfg(t, 1250e3, 810e3, 4, on)
			er.link = link
			er.tick()
			ps := er.senders()
			g = er.measure(10*time.Minute, func(el time.Duration) { er.speak(ps[int(el/(10*time.Second))%len(ps)]) })
			t.Logf("%s | Gruppe 4, Sprecherwechsel alle 10 s, %.1f Mbit/s: %v, %s", name, link/1e6, g, voll(g))
		}
		// Gruppe 4 Sender, frei, Schuebe
		er = newEstCfg(t, 1250e3, 810e3, 4, on)
		er.pion = true
		er.tick()
		g = er.measure(10*time.Minute, schuebe(er, 45*time.Second, 70*time.Second, 55*time.Second, 90*time.Second))
		t.Logf("%s | Gruppe 4, frei, Schuebe alle 45-90 s: %v, %s", name, g, voll(g))
		// Gruppe 4 Sender: 3 min 1,2 Mbit/s mit Sprecherwechsel alle 20 s, dann frei
		er = newEstCfg(t, 1250e3, 810e3, 4, on)
		er.pion, er.link = true, 1200e3
		er.tick()
		ps := er.senders()
		rot := func(el time.Duration) { er.speak(ps[int(el/(20*time.Second))%len(ps)]) }
		g = er.measure(3*time.Minute, rot)
		er.link = 0
		full := "nicht in 6 min"
		for el := time.Duration(0); el < 6*time.Minute; el += 200 * time.Millisecond {
			rot(3*time.Minute + el)
			er.tick()
			if v := er.video(); v == "hmmm" || v == "hmm" {
				full = fmt.Sprintf("nach %.1f s", el.Seconds())
				break
			}
		}
		t.Logf("%s | Gruppe 4, Sprecherwechsel alle 20 s, 3 min 1,2 Mbit/s: %v; dann frei: volle Belegung %s", name, g, full)
	}
}
