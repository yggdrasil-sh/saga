package sfu

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"testing"
	"time"
)

// Bug 2159 (Pruefung von 2152, Container-Reihe rang345 400k): Traegt eine wirklich enge Leitung (kein grundloser
// Zielsturz, ~260-480 kbit/s) den Sprecher auf l und zwei Kacheln im Mindestbild nicht, pendelte eine Kachel alle ~6 s
// zwischen Pause und Mindestbild (je Rueckkehr eine Schluesselbild-Anforderung und ein kurzer Stau), und der Sprecher
// lief dauerhaft auf halber Bildrate (l*): Die vorgemerkte Reserve der Kachel ging dem Schritt des Sprechers vor. Dazu
// (320-480k und grundlose GCC-Stuerze 560k-1M) l <-> l* alle 2-6 s.
//
// Modell (Pruefer pruef-knoten-eng): Empfaenger mit Sprecherplatz und zwei Kacheln (UDP, Probing an), drei Sender mit
// l (20 fps, zwei Zeitschichten: die Basis-Zeitschicht traegt 60 % der Rate), m, h; echte Leitung mit Ratengrenze,
// GCC-Ziel nach pion (pionGCC), Warteschlange an der Leitung bis 300 ms (darueber: Verlust). Optional grundlose
// Zielstuerze (falsch) wie im Container.

type engRes struct {
	link      float64
	falsch    time.Duration
	over      float64         // Anteil Takte mit Rate ueber der Leitung
	qMean     time.Duration   // mittlere Schlange
	qMax      time.Duration   // laengste Schlange
	lossT     int             // Takte mit voller Schlange (Verlust)
	pause     float64         // Anteil Kachel-Zeit in Pause
	maxPause  time.Duration   // laengste Pause einer Kachel (nach dem Anlauf)
	tileMin   float64         // Anteil Kachel-Zeit im Mindestbild
	tileChg   float64         // Zustandswechsel je Kachel und Minute (hoechster Wert der Kacheln)
	spkFull   float64         // Anteil Sprecher-Zeit in voller Form (l mit allen Zeitschichten oder hoeher)
	spkChg    float64         // Zustandswechsel des Sprechers je Minute
	minFlips  int             // Wechsel l <-> l* (alle Plaetze)
	pauseBack int             // Wechsel Pause <-> Mindestbild (Kacheln)
	backs     []time.Duration // Zeitpunkte (ab Beginn) der Rueckkehr einer Kachel aus der Pause
	ticks     int             // gezaehlte Takte (nach dem Anlauf)
	maxFull   time.Duration   // laengste Zeit am Stueck mit (fast) voller Schlange (>= 250 ms)
	trans     map[string]int
}

func (r engRes) String() string {
	var tr []string
	for k, v := range r.trans {
		if v >= 2 {
			tr = append(tr, fmt.Sprintf("%s=%d", k, v))
		}
	}
	sort.Strings(tr)
	return fmt.Sprintf("link=%4.0fk falsch=%-3v ueber %4.1f %% Schlange %3d/%3d ms Verlust %3d | Kachel Pause %4.1f %% laengste %5.1f s"+
		" Mindest %4.1f %% Wechsel/min %4.1f | Sprecher voll %5.1f %% Wechsel/min %4.1f | l<>l* %d p<>l* %d  %s",
		r.link/1000, r.falsch, 100*r.over, r.qMean.Milliseconds(), r.qMax.Milliseconds(), r.lossT, 100*r.pause,
		r.maxPause.Seconds(), 100*r.tileMin, r.tileChg, 100*r.spkFull, r.spkChg, r.minFlips, r.pauseBack, strings.Join(tr, " "))
}

// engRun: Leitung link (bit/s), Dauer d (die ersten 20 s zaehlen nicht), grundloser Zielsturz alle falsch (0 = nie).
func engRun(t *testing.T, link float64, d, falsch time.Duration) engRes {
	t.Helper()
	return engRunOH(t, link, d, falsch, 1)
}

// engRunOH: ... die Leitung traegt oh x die gerechnete Rate (Kopfdaten, RTCP, weitere Tonstroeme, die der Knoten nicht
// einrechnet - Container 400k: Sprecher auf l und drei Tonstroeme fuellten die Leitung). Die Umlaufzeit (ExtRTT) ist
// 40 ms plus Schlange.
func engRunOH(t *testing.T, link float64, d, falsch time.Duration, oh float64) engRes {
	t.Helper()
	er := newEstCfg(t, 1250e3, 600e3, 3, true)
	er.pion = true
	er.link = link
	er.grow = 0
	tidOf := func(s Slot) float64 {
		if s.Layer == "l" && s.MaxTID == 0 {
			return 0.6
		}
		return 1
	}
	var ls []int
	for i, s := range er.all {
		if s.Layer == "l" {
			ls = append(ls, i)
			s.HasTID.Store(true)
		}
	}
	const dt = 0.2
	r := engRes{link: link, falsch: falsch, trans: map[string]int{}}
	q, sumQ := 0.0, 0.0
	er.l.ExtRTT = func() time.Duration { return 40*time.Millisecond + time.Duration(q*float64(time.Second)) }
	var fullRun int
	var n, over, tileT, pauseT, minT, spkT, spkFull int
	last := map[string]string{}
	chg := map[string]int{}
	pauseFrom := map[int]int64{}
	start := er.now
	warm := int64(20 * time.Second)
	next := start + int64(falsch)
	for er.now-start < int64(d) {
		for _, i := range ls { // 20 fps: je Takt 2 Basis- und 2 obere Rahmen
			er.all[i].TIDFrames[0].Add(2)
			er.all[i].TIDFrames[1].Add(2)
		}
		if falsch > 0 && er.now >= next {
			er.falsch = 1
			next += int64(falsch)
		}
		er.tick()
		er.sent = 0
		for _, s := range er.l.Layout() {
			if s.Class != "audio" && !s.Paused && s.Layer != "" {
				er.sent += er.rates[s.Layer] * tidOf(s)
			}
		}
		er.sent = (estAudio+er.sent)*oh - estAudio // auf der Leitung (auch fuer das GCC-Modell im naechsten Takt)
		rate := estAudio + er.sent + er.fill
		q += (rate - link) / link * dt
		lost := false
		if q > 0.3 {
			q, lost = 0.3, true
		}
		q = max(q, 0)
		if engSpur != nil {
			engSpur(er, rate, q)
		}
		if er.now-start < warm {
			for _, s := range er.l.Layout() {
				if s.Class != "audio" {
					last[fmt.Sprintf("%s%d", s.Class, s.Index)] = engState(s)
				}
			}
			continue
		}
		n++
		if rate > link {
			over++
		}
		if lost {
			r.lossT++
		}
		if q >= 0.25 {
			fullRun++
			r.maxFull = max(r.maxFull, time.Duration(fullRun)*200*time.Millisecond)
		} else {
			fullRun = 0
		}
		sumQ += q
		r.qMax = max(r.qMax, time.Duration(q*float64(time.Second)))
		for _, s := range er.l.Layout() {
			if s.Class == "audio" {
				continue
			}
			k := fmt.Sprintf("%s%d", s.Class, s.Index)
			st := engState(s)
			if o, ok := last[k]; ok && o != st {
				r.trans[s.Class+":"+o+">"+st]++
				chg[k]++
				if o == "l" && st == "l*" || o == "l*" && st == "l" {
					r.minFlips++
				}
				if s.Class == "tile" && (o == "p" && st == "l*" || o == "l*" && st == "p") {
					r.pauseBack++
				}
				if s.Class == "tile" && o == "p" {
					r.backs = append(r.backs, time.Duration(er.now-start))
				}
			}
			last[k] = st
			switch s.Class {
			case "tile":
				tileT++
				if st == "p" {
					pauseT++
					if _, ok := pauseFrom[s.Index]; !ok {
						pauseFrom[s.Index] = er.now
					}
					continue
				}
				if st == "l*" {
					minT++
				}
				if p0, ok := pauseFrom[s.Index]; ok {
					r.maxPause = max(r.maxPause, time.Duration(er.now-p0))
					delete(pauseFrom, s.Index)
				}
			case "speaker":
				spkT++
				if st != "p" && st != "l*" {
					spkFull++
				}
			}
		}
	}
	for _, p0 := range pauseFrom {
		r.maxPause = max(r.maxPause, time.Duration(er.now-p0))
	}
	mins := float64(n) * dt / 60
	for k, v := range chg {
		if strings.HasPrefix(k, "tile") {
			r.tileChg = max(r.tileChg, float64(v)/mins)
		} else if strings.HasPrefix(k, "speaker") {
			r.spkChg = float64(v) / mins
		}
	}
	r.over = float64(over) / float64(max(n, 1))
	r.ticks = n
	r.qMean = time.Duration(sumQ / float64(max(n, 1)) * float64(time.Second))
	r.pause = float64(pauseT) / float64(max(tileT, 1))
	r.tileMin = float64(minT) / float64(max(tileT, 1))
	r.spkFull = float64(spkFull) / float64(max(spkT, 1))
	return r
}

// engState: Form eines Platzes - "p" (Pause), "l*" (Mindestbild), sonst die Schicht.
func engState(s Slot) string {
	switch {
	case s.Paused || s.Layer == "":
		return "p"
	case s.Layer == "l" && s.MaxTID == 0:
		return "l*"
	}
	return s.Layer
}

// TestEngLeitungZahlen: Uebersicht fuer NODE.md (go test -run TestEngLeitungZahlen -v), ohne Zusicherungen.
func TestEngLeitungZahlen(t *testing.T) {
	if testing.Short() {
		t.Skip("Uebersicht")
	}
	for _, link := range []float64{200e3, 260e3, 320e3, 400e3, 480e3, 560e3, 600e3, 700e3, 1000e3} {
		for _, f := range []time.Duration{0, 6 * time.Second} {
			t.Log(engRun(t, link, 4*time.Minute, f))
		}
	}
}

// TestEngLeitungTraegtNicht: Die Leitung traegt den Sprecher auf l, aber nicht zusaetzlich zwei Kacheln auf voller
// unterster Schicht (400k, 480k) bzw. nicht einmal ein bzw. zwei Mindestbilder dazu (260k, 320k). Ziel 2159: Der
// Sprecher laeuft in voller Form (vorher 9 / 31 / 9 / 100 % der Zeit; nur mit der Daempfung, ohne "Sprecher geht vor",
// bei 320k 89 %), kein Pendeln (Zustandswechsel je Kachel und Minute vorher 20,7 / 20,7 / 13,6 / 26,2; jetzt ein
// gescheiterter Versuch je 30 s, mbBackMax), keine volle Schlange; bei 400k zeigen beide Kacheln ein Mindestbild statt
// einer Pause.
func TestEngLeitungTraegtNicht(t *testing.T) {
	for _, link := range []float64{260e3, 320e3, 400e3, 480e3} {
		r := engRun(t, link, 4*time.Minute, 0)
		t.Log(r)
		if r.spkFull < 0.95 {
			t.Errorf("%.0fk: Sprecher nur %.0f %% der Zeit in voller Form (Ziel >= 95 %%)", link/1000, 100*r.spkFull)
		}
		if r.spkChg > 1 {
			t.Errorf("%.0fk: Sprecher wechselt %.1f Mal je Minute (Ziel <= 1)", link/1000, r.spkChg)
		}
		if r.tileChg > 6 {
			t.Errorf("%.0fk: Kachel wechselt %.1f Mal je Minute (Ziel <= 6: ein gescheiterter Versuch je ~30 s)", link/1000, r.tileChg)
		}
		if r.lossT*100 > r.ticks || r.maxFull > 2*time.Second {
			t.Errorf("%.0fk: %d Takte mit voller Schlange, am Stueck bis %v (Ziel <= 1 %%, <= 2 s)", link/1000, r.lossT, r.maxFull)
		}
		if link >= 400e3 && r.pause > 0.02 {
			t.Errorf("%.0fk: Kacheln %.0f %% der Zeit in Pause (Ziel: Mindestbild)", link/1000, 100*r.pause)
		}
	}
}

// TestEngLeitungStau: Die Leitung traegt nicht einmal den Sprecher auf voller unterster Schicht samt Ton (Kopfdaten,
// weitere Tonstroeme: Faktor oh). Ton geht vor Bild (Container 400k: vorher RTT ~400 ms, Ton verdeckt 7,5 %): Die
// Schlange bleibt im Mittel klein, laeuft nicht voll, der Sprecher laeuft im Mindestbild (so viel Bild, wie die Leitung
// traegt), und er versucht die volle Schicht hoechstens etwa alle 30 s (jeder Versuch staut kurz, kein Dauerstau).
// Vorher (58802e6 und Ziel 1/2 ohne Stau-Schritt): Schlange im Mittel 300 ms, voll in 100 % der Takte.
func TestEngLeitungStau(t *testing.T) {
	for _, c := range []struct{ link, oh float64 }{{220e3, 1.25}, {300e3, 1.7}} {
		r := engRunOH(t, c.link, 4*time.Minute, 0, c.oh)
		t.Log(r)
		if r.qMean > 100*time.Millisecond {
			t.Errorf("%.0fk x%.2f: Schlange im Mittel %v (Ziel < 100 ms)", c.link/1000, c.oh, r.qMean)
		}
		if r.lossT*100 > 3*r.ticks || r.maxFull > 2*time.Second {
			t.Errorf("%.0fk x%.2f: %d von %d Takten mit voller Schlange, am Stueck bis %v (Ziel <= 3 %%, <= 2 s: kein Dauerstau)",
				c.link/1000, c.oh, r.lossT, r.ticks, r.maxFull)
		}
		if r.spkChg > 6 {
			t.Errorf("%.0fk x%.2f: Sprecher wechselt %.1f Mal je Minute (Ziel <= 6: ein Versuch je ~30 s und Anlauf)", c.link/1000, c.oh, r.spkChg)
		}
	}
}

// TestEngLeitungAufschub: 260k - jede Rueckkehr einer Kachel aus der Pause scheitert (Stau binnen 1 s). Der Abstand
// der Versuche verdoppelt sich bis mbBackMax (30 s) und bleibt dort; vorher kam die Kachel alle ~6 s zurueck.
func TestEngLeitungAufschub(t *testing.T) {
	r := engRun(t, 260e3, 4*time.Minute, 0)
	t.Log(r, r.backs)
	if len(r.backs) < 3 {
		t.Fatalf("nur %d Rueckkehrversuche", len(r.backs))
	}
	const backMax = 30 * time.Second // mbBackMax (Zahl, damit der Test auch gegen 58802e6 laeuft)
	var gaps []time.Duration
	for i := 1; i < len(r.backs); i++ {
		gaps = append(gaps, r.backs[i]-r.backs[i-1])
	}
	t.Log("Abstaende", gaps)
	for i, g := range gaps {
		if i > 0 && g+time.Second < gaps[i-1] && gaps[i-1] < backMax {
			t.Errorf("Abstand %d kuerzer als davor: %v", i, gaps)
		}
		if g > backMax+5*time.Second {
			t.Errorf("Abstand %v ueber mbBackMax: %v", g, gaps)
		}
	}
	if last := gaps[len(gaps)-1]; last < backMax-time.Second {
		t.Errorf("letzter Abstand %v - Aufschub nicht bei mbBackMax: %v", last, gaps)
	}
}

// TestEngLeitungTraegt: Die Leitung traegt Sprecher und Kacheln - kein Rueckschritt gegenueber 2152 (58802e6): keine
// Pause, kaum Wechsel; 1 Mbit/s mit grundlosen Zielstuerzen alle 6 s keine Kachel laenger als 5 s ohne Bild, der
// Sprecher in voller Form. (Dort pendeln die Kacheln weiter l <-> l* wie vorher, 19 Mal je Minute: Ein grundloser
// Sturz meldet keine Ueberlast und zaehlt nicht als gescheiterte Rueckkehr - blieben die Kacheln im Mindestbild, traefe
// der naechste Sturz den Sprecher, NODE.md.)
func TestEngLeitungTraegt(t *testing.T) {
	for _, c := range []struct {
		link     float64
		falsch   time.Duration
		maxPause time.Duration
		chg      float64
	}{
		{600e3, 0, 0, 1},
		{1000e3, 0, 0, 1},
		{1000e3, 6 * time.Second, 5 * time.Second, 20},
	} {
		r := engRun(t, c.link, 4*time.Minute, c.falsch)
		t.Log(r)
		if r.maxPause > c.maxPause {
			t.Errorf("%.0fk/%v: laengste Pause %v (Ziel <= %v)", c.link/1000, c.falsch, r.maxPause, c.maxPause)
		}
		if r.tileChg > c.chg {
			t.Errorf("%.0fk/%v: Kachel wechselt %.1f Mal je Minute (Ziel <= %.0f)", c.link/1000, c.falsch, r.tileChg, c.chg)
		}
		if r.spkFull < 0.95 {
			t.Errorf("%.0fk/%v: Sprecher nur %.0f %% in voller Form", c.link/1000, c.falsch, 100*r.spkFull)
		}
		if r.lossT > 0 {
			t.Errorf("%.0fk/%v: %d Takte mit voller Schlange", c.link/1000, c.falsch, r.lossT)
		}
	}
}

// TestEngSpur: Spur je Takt fuer die Fehlersuche (ENG_SPUR=<kbit/s>[,falsch-s[,oh[,Dauer-s]]]), sonst uebersprungen.
func TestEngSpur(t *testing.T) {
	v := os.Getenv("ENG_SPUR")
	if v == "" {
		t.Skip("ENG_SPUR nicht gesetzt")
	}
	k, f, oh, sec := 0.0, 0.0, 1.0, 90.0
	fmt.Sscanf(strings.ReplaceAll(v, ",", " "), "%g %g %g %g", &k, &f, &oh, &sec)
	engSpur = func(er *estRoom, rate, q float64) {
		st := ""
		for _, s := range er.l.Layout() {
			if s.Class != "audio" {
				st += fmt.Sprintf("%-3s", engState(s))
			}
		}
		t.Logf("%6.1f %s rate %4.0f gcc %4.0f est %4d q %3.0f pr %v", float64(er.now%int64(time.Hour))/1e9, st, rate/1000,
			er.gcc/1000, er.l.EstimateKbit(), q*1000, er.fill > 0)
	}
	defer func() { engSpur = nil }()
	t.Log(engRunOH(t, k*1000, time.Duration(sec*float64(time.Second)), time.Duration(f*float64(time.Second)), oh))
}

var engSpur func(er *estRoom, rate, q float64)

// TestEngLeitungAnlauf (Pruefung 2159): pion-GCC meldet bis zur ersten Laufzeit-Auswertung "overuse" (Nullwert der
// Statistik), die Umlaufzeit ist bis zum ersten Empfaengerbericht unbekannt. Das ist kein Stau - sonst ging zu Beginn
// jedes Anrufs eine Stufe zurueck, und 60 s lang (jamHold) gab es keinen Schritt nach der Schaetzung (Container,
// Spur: freie Beine mit jam=true bei rtt=0; Modell: Sprecher erst nach 62 s auf h statt nach 12 s).
func TestEngLeitungAnlauf(t *testing.T) {
	er := newEstCfg(t, 1250e3, 600e3, 3, true)
	er.pion = true
	er.link = 4e6
	er.grow = 0
	var ls []int
	for i, s := range er.all {
		if s.Layer == "l" {
			ls = append(ls, i)
			s.HasTID.Store(true)
		}
	}
	start := er.now
	orig := er.l.ExtOver
	er.l.ExtOver = func() bool { return er.now-start < int64(1500*time.Millisecond) || orig() }
	er.l.ExtRTT = func() time.Duration {
		if er.now-start < int64(2*time.Second) {
			return 0
		}
		return 40 * time.Millisecond
	}
	firstH := time.Duration(0)
	for er.now-start < int64(40*time.Second) && firstH == 0 {
		for _, i := range ls {
			er.all[i].TIDFrames[0].Add(2)
			er.all[i].TIDFrames[1].Add(2)
		}
		er.tick()
		if er.l.jamAt != 0 {
			t.Fatalf("Stau nach %v (GCC-Nullwert, Umlaufzeit unbekannt)", time.Duration(er.l.jamAt-start))
		}
		if er.layer() == "h" {
			firstH = time.Duration(er.now - start)
		}
	}
	if firstH == 0 || firstH > 20*time.Second {
		t.Errorf("Sprecher erst nach %v auf h (Ziel <= 20 s, frei ~12 s)", firstH)
	}
}
