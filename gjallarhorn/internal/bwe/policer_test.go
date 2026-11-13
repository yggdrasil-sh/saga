package bwe

import (
	"testing"
)

// Stufe 3b (4b): Policer-Merkmal. Am Token-Bucket-Policer haengt der Verlust an der Senderate und kommt in Folgen; beim
// Zufallsverlust nicht. Der Detektor widerlegt dann einen Fall des GCC-Ziels nicht mehr (Tip.LossUp, kein Clear),
// waehrend er Zufallsverlust weiter fuer unauffaellig haelt.

// videoSender: 30 Bilder/s, Schluesselbild alle 2 s, Bildgroesse +-20 %, Pakete bis 1100 Byte im Abstand von 0,2 ms -
// im Mittel kbit kbit/s.
func videoSender(l *leitung, kbit float64, sec float64) {
	frame := kbit * 1000 / 8 / 30
	for end := l.now + sec; l.now < end; {
		t0 := l.now
		k := int(t0*30 + 0.5)
		size := frame * 59 / (59 + 8) * (0.8 + 0.4*l.rng.Float64())
		if k%60 == 0 { // Schluesselbild: neunmal ein gewoehnliches
			size = frame * 9 * 60 / (59 + 8) * (0.8 + 0.4*l.rng.Float64())
		}
		for b := int(size); b > 0; b -= 1100 {
			l.send(KindVideo, min(b, 1100))
			l.now += 0.0002
		}
		l.now = t0 + 1.0/30
	}
}

// tokenBucket liefert einen Policer mit kbit kbit/s und Eimer ms Millisekunden.
func tokenBucket(kbit, ms float64) func(t float64, size int) bool {
	rate := kbit * 1000 / 8
	b := rate * ms / 1000
	tok, last := b, 0.0
	return func(t float64, size int) bool {
		tok = min(b, tok+(t-last)*rate)
		last = t
		if tok < float64(size) {
			return false
		}
		tok -= float64(size)
		return true
	}
}

// TestDetektorPolicerMerkmal: Video mit Schluesselbildern (videoSender), 120 s nach 20 s Vorlauf, 3 Laeufe je Fall.
// Policer 1400 kbit/s (Eimer 100 bzw. 200 ms, gesendet 1450 bzw. 1500): Merkmal in jedem Takt (Folgen 89 bis 97 %,
// Korrelation 0,86 bis 0,97), und kein Takt widerlegt, solange an der Spitze Verlust ist - ohne das Merkmal (Gegenprobe)
// widerlegte der Detektor in 2 bis 39 von rund 150 solchen Takten. Zufallsverlust 3, 5 und 10 %: nie (Folgen hoechstens
// 4 %, Korrelation -0,08 bis 0,07); widerlegt wie bisher (3 %: 97 % der Takte, 5 %: 94 %).
func TestDetektorPolicerMerkmal(t *testing.T) {
	type res struct {
		pol, clear, lossClear, lossTicks, ticks int
		last                                    Tip
	}
	run := func(seed int64, cfg Config, prep func(l *leitung), kbit float64) (r res) {
		l := newLeitung(seed, 100, 1000)
		l.d = NewDetector(cfg)
		l.jitMs = 3
		videoSender(l, kbit, 5)
		prep(l)
		videoSender(l, kbit, 15) // Verlauf fuer PolSpanNs
		for end := l.now + 120; l.now < end; {
			videoSender(l, kbit, 0.2)
			tp := l.d.Tip(int64(l.now * 1e9))
			r.ticks++
			if tp.Policer {
				r.pol++
			}
			if tp.Clear {
				r.clear++
			}
			if tp.Loss > 0 {
				r.lossTicks++
				if tp.Clear {
					r.lossClear++
				}
			}
			r.last = tp
		}
		return r
	}
	off := Defaults()
	off.PolSpanNs = 0
	for _, c := range []struct {
		name     string
		prep     func(l *leitung)
		kbit     float64
		policer  bool
		minClear float64
	}{
		{"Policer 1400, Eimer 200 ms, gesendet 1500", func(l *leitung) { l.police = tokenBucket(1400, 200) }, 1500, true, 0},
		{"Policer 1400, Eimer 200 ms, gesendet 1450", func(l *leitung) { l.police = tokenBucket(1400, 200) }, 1450, true, 0},
		{"Policer 1400, Eimer 100 ms, gesendet 1500", func(l *leitung) { l.police = tokenBucket(1400, 100) }, 1500, true, 0},
		{"Zufallsverlust 3 %", func(l *leitung) { l.loss = 0.03 }, 1500, false, 0.9},
		{"Zufallsverlust 5 %", func(l *leitung) { l.loss = 0.05 }, 1500, false, 0.8},
		{"Zufallsverlust 10 %", func(l *leitung) { l.loss = 0.10 }, 1500, false, 0},
		{"frei", func(l *leitung) {}, 1500, false, 0.99},
	} {
		lossClear0 := 0
		for seed := int64(1); seed <= 3; seed++ {
			r := run(seed, Defaults(), c.prep, c.kbit)
			r0 := run(seed, off, c.prep, c.kbit)
			lossClear0 += r0.lossClear
			t.Logf("%s (seed %d): Merkmal in %d von %d Takten, widerlegt %d (mit Verlust an der Spitze %d von %d; ohne Merkmal %d); zuletzt %+v",
				c.name, seed, r.pol, r.ticks, r.clear, r.lossClear, r.lossTicks, r0.lossClear, r.last.Pol)
			switch {
			case c.policer && (r.pol < r.ticks*95/100 || r.lossClear != 0):
				t.Fatalf("%s: Policer nicht erkannt bzw. widerlegt trotz Verlust", c.name)
			case !c.policer && r.pol != 0:
				t.Fatalf("%s: falsch als Policer erkannt", c.name)
			case float64(r.clear) < c.minClear*float64(r.ticks):
				t.Fatalf("%s: widerlegt nur in %d von %d Takten", c.name, r.clear, r.ticks)
			}
		}
		if c.policer && lossClear0 == 0 {
			t.Fatalf("%s: Gegenprobe ohne Merkmal widerlegt auch nie - der Test prueft das Merkmal nicht", c.name)
		}
	}
}

// TestDetektorWins: Zaehler je Messfenster (fuer GET /api/sfu/leg/{id}) - dieselben Zahlen, aus denen das Merkmal
// entsteht.
func TestDetektorWins(t *testing.T) {
	l := newLeitung(1, 100, 1000)
	l.jitMs = 3
	videoSender(l, 1500, 5)
	l.police = tokenBucket(1400, 200)
	videoSender(l, 1500, 20)
	ws := l.d.Wins(10e9)
	tp := l.d.Tip(int64(l.now * 1e9))
	lost, runs := 0, 0
	for _, w := range ws {
		if w.LostRuns > w.Lost || w.Pkts <= 0 || w.Ns <= 0 || w.SentBps <= 0 {
			t.Fatalf("Fenster %+v", w)
		}
		lost, runs = lost+w.Lost, runs+w.LostRuns
	}
	if len(ws) < 80 || len(ws) > 110 || lost < tp.Pol.Lost || runs == 0 {
		t.Fatalf("%d Fenster, verloren %d, in Folgen %d; Merkmal %+v", len(ws), lost, runs, tp.Pol)
	}
}
