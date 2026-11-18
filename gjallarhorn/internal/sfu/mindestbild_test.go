package sfu

import (
	"testing"
	"time"
)

// Bug 2152 (Labor 18.11., Container-Messreihe rang345): Im 4er ueber den Knoten kamen bei einer Person mit 1 Mbit/s
// einzelne Bildstroeme 15-83 s lang gar nicht an, obwohl die Leitung nichts verwarf. Die Spur des Knotens (HORN_SPUR,
// Container) zeigte den Ablauf: Das GCC-Ziel faellt (im Container grundlos - der eigene Detektor sah keine Schlange),
// die Schaetzung folgt, eine Kachel passt mit der untersten Schicht nicht mehr ins Budget und pausiert. In der
// Sperrzeit danach (4, 8, 16, 30 s) waechst die Schaetzung nicht, nur bis zur Rate - und die ist ohne die Kachel
// kleiner. Das Ziel lag oft schon nach 2 s wieder weit ueber der Rate; die Kachel kam trotzdem erst nach der Sperrzeit
// zurueck, und der Sprecher ging vorher noch auf m.
//
// Modell: Empfaenger mit Sprecherplatz und zwei Kacheln (UDP, Probing an), drei Sender mit l (20 fps, zwei
// Zeitschichten: die Basis-Zeitschicht traegt 60 % der Rate), m, h; Leitung 1 Mbit/s, GCC-Ziel nach den Regeln von
// pion (pionGCC). Dazu alle stoerAlle Sekunden ein grundloser Zielsturz auf 85 % der Rate fuer stoerTakte Takte (wie im
// Container). Gemessen: laengste Pause einer Kachel, Anteil der Zeit mit pausierten Kacheln.

type mbRes struct {
	maxPause   time.Duration // laengste Pause einer Kachel (ohne Anrufbeginn)
	pauseShare float64       // Anteil Kachel-Zeit in Pause
	minShare   float64       // Anteil Kachel-Zeit im Mindestbild (Basis-Zeitschicht)
	upShare    float64       // Anteil Kachel-Zeit ueber l
	spkM       float64       // Anteil Sprecher-Zeit auf m oder hoeher
	recover    time.Duration // nach dem Ende der Stoerungen (stopAt): bis alle Kacheln wieder voll laufen (-1 = nie)
}

func mbRun(t *testing.T, link float64, stoerAlle time.Duration, stoerTakte int, d time.Duration) (r mbRes) {
	return mbRunStop(t, link, stoerAlle, stoerTakte, d, 0)
}

// mbRunStop: wie mbRun, die Stoerungen enden nach stopAt (0 = nie); gemessen wird zusaetzlich die Erholung.
func mbRunStop(t *testing.T, link float64, stoerAlle time.Duration, stoerTakte int, d, stopAt time.Duration) (r mbRes) {
	t.Helper()
	r.recover = -1
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
	var ls []int // Indizes der l-Quellen in er.all
	for i, s := range er.all {
		if s.Layer == "l" {
			ls = append(ls, i)
			s.HasTID.Store(true)
		}
	}
	paused := map[int]int64{} // Platz -> Beginn der Pause
	var tileT, pauseT, minT, upT, spkT, spkM float64
	start := er.now
	warm := int64(10 * time.Second) // Anrufbeginn zaehlt nicht
	nextStoer := start + int64(stoerAlle)
	for er.now-start < int64(d) {
		for _, i := range ls { // 20 fps: je Takt 2 Basis- und 2 obere Rahmen
			er.all[i].TIDFrames[0].Add(2)
			er.all[i].TIDFrames[1].Add(2)
		}
		if stoerAlle > 0 && er.now >= nextStoer && (stopAt == 0 || er.now-start < int64(stopAt)) {
			er.falsch = stoerTakte
			nextStoer += int64(stoerAlle)
		}
		er.tick()
		// gesendete Bildrate mit Zeitschicht (er.tick zaehlt die volle Schicht)
		er.sent = 0
		for _, s := range er.l.Layout() {
			if s.Class != "audio" && !s.Paused && s.Layer != "" {
				er.sent += er.rates[s.Layer] * tidOf(s)
			}
		}
		if stopAt > 0 && er.now-start >= int64(stopAt) && r.recover < 0 {
			full := true
			for _, s := range er.l.Layout() {
				if s.Class == "tile" && (s.Paused || s.Layer == "" || s.Layer == "l" && s.MaxTID == 0) {
					full = false
				}
			}
			if full {
				r.recover = time.Duration(er.now - start - int64(stopAt))
			}
		}
		if er.now-start < warm {
			continue
		}
		for _, s := range er.l.Layout() {
			switch s.Class {
			case "tile":
				tileT++
				switch {
				case s.Paused || s.Layer == "":
					pauseT++
					if _, ok := paused[s.Index]; !ok {
						paused[s.Index] = er.now
					}
					continue
				case s.Layer == "l" && s.MaxTID == 0:
					minT++
				case s.Layer != "l":
					upT++
				}
				if p0, ok := paused[s.Index]; ok {
					r.maxPause = max(r.maxPause, time.Duration(er.now-p0))
					delete(paused, s.Index)
				}
			case "speaker":
				spkT++
				if s.Layer == "m" || s.Layer == "h" {
					spkM++
				}
			}
		}
	}
	for _, p0 := range paused {
		r.maxPause = max(r.maxPause, time.Duration(er.now-p0))
	}
	if tileT > 0 {
		r.pauseShare, r.minShare, r.upShare = pauseT/tileT, minT/tileT, upT/tileT
	}
	if spkT > 0 {
		r.spkM = spkM / spkT
	}
	return r
}

// TestMindestbildEng: 1 Mbit/s mit grundlosen Zielstuerzen alle 6 s (je ein Takt) - keine Kachel laenger als 5 s ohne
// Bild, Kacheln hoechstens 10 % der Zeit in Pause. Vorher (Stand 1276701): laengste Pause 2 min 30 s, 86 % Pause.
func TestMindestbildEng(t *testing.T) {
	r := mbRun(t, 1000e3, 6*time.Second, 1, 3*time.Minute)
	t.Logf("eng+stoer: laengste Pause %v, Pause %.0f %%, Mindestbild %.0f %%, ueber l %.0f %%, Sprecher m+ %.0f %%",
		r.maxPause, 100*r.pauseShare, 100*r.minShare, 100*r.upShare, 100*r.spkM)
	if r.maxPause > 5*time.Second {
		t.Fatalf("laengste Pause %v (Ziel <= 5 s)", r.maxPause)
	}
	if r.pauseShare > 0.10 {
		t.Fatalf("Kacheln %.0f %% der Zeit in Pause", 100*r.pauseShare)
	}
}

// TestMindestbildEngOhneStoerung: 1 Mbit/s ohne Zielstuerze - die Leitung traegt Sprecher und Kacheln auf l; keine
// Pause, das Mindestbild hoechstens kurz.
func TestMindestbildEngOhneStoerung(t *testing.T) {
	r := mbRun(t, 1000e3, 0, 0, 3*time.Minute)
	t.Logf("eng: laengste Pause %v, Pause %.0f %%, Mindestbild %.0f %%, ueber l %.0f %%, Sprecher m+ %.0f %%",
		r.maxPause, 100*r.pauseShare, 100*r.minShare, 100*r.upShare, 100*r.spkM)
	if r.pauseShare > 0.02 || r.maxPause > 3*time.Second {
		t.Fatalf("Pausen ohne Stoerung: %.0f %%, laengste %v", 100*r.pauseShare, r.maxPause)
	}
}

// TestMindestbildFrei: 3 Mbit/s mit denselben Stuerzen - nichts pausiert, die Kacheln laufen ueberwiegend ueber l
// (kein Rueckschritt: das Mindestbild greift nur, wo vorher pausiert wurde).
func TestMindestbildFrei(t *testing.T) {
	r := mbRun(t, 3000e3, 6*time.Second, 1, 3*time.Minute)
	t.Logf("frei+stoer: laengste Pause %v, Pause %.0f %%, Mindestbild %.0f %%, ueber l %.0f %%, Sprecher m+ %.0f %%",
		r.maxPause, 100*r.pauseShare, 100*r.minShare, 100*r.upShare, 100*r.spkM)
	if r.pauseShare > 0.02 {
		t.Fatalf("Pausen auf freier Leitung: %.0f %%", 100*r.pauseShare)
	}
}

// TestMindestbildErholung: 60 s mit Stuerzen bei 1 Mbit/s, danach ruhig - die Kacheln laufen nach hoechstens 10 s wieder
// mit voller unterster Schicht (Mindestbild zurueck auch in der Sperrzeit, nach dem GCC-Ziel).
func TestMindestbildErholung(t *testing.T) {
	r := mbRunStop(t, 1000e3, 6*time.Second, 1, 2*time.Minute, time.Minute)
	t.Logf("Erholung %v (laengste Pause %v, Mindestbild %.0f %%)", r.recover, r.maxPause, 100*r.minShare)
	if r.recover < 0 || r.recover > 10*time.Second {
		t.Fatalf("Erholung %v (Ziel <= 10 s)", r.recover)
	}
}
