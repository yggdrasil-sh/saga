package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Stufe 3b: Token-Bucket-Policer im Modell (nach dem Modell des Pruefers bwe3). Die kurze Probe von Stufe 3 schiebt
// nur (Bedarf x 1,05 - Policer-Rate) x 0,4 bis 0,6 s ueber die Grenze; den Eimer leert sie nicht, sie besteht, und die
// Schicht faellt Sekunden spaeter zurueck - immer wieder. Nach dem ersten frischen Rueckfall faehrt die naechste Probe
// auf dieses Ziel lang (freshBrake, probeWinLong, probeHeadLong), und die Bremse haengt am Bein, nicht am Platz.

// bremseGruppe: Bremse auch in der Gruppe (freshBrakeGroup, Stand bis Stufe 3e) fuer diesen Test. Seit Stufe 3f gilt
// sie nur im 1:1 (soloCheck); ihre Mechanik (Umverteilung, getragen, kleinere Zielrate, Einbruch, Deckel ueber mehrere
// Plaetze) greift dort mit Bildschirmteilen (zwei Plaetze eines Senders) - die Modelle dafuer sind Gruppen.
func bremseGruppe(t *testing.T) {
	old := freshBrakeGroup
	freshBrakeGroup = true
	t.Cleanup(func() { freshBrakeGroup = old })
}

// tokenBucket: Policer mit Rate R und Eimer B (bit) vor dem Bein - leer begrenzt er auf R (er.link), sonst ist die
// Leitung frei. R = 0: kein Policer.
type tokenBucket struct{ R, B, tok float64 }

func (tb *tokenBucket) step(er *estRoom) {
	if tb.R == 0 {
		er.link = 0
		return
	}
	rate := estAudio + er.sent + float64(er.l.fl.Padding())
	tb.tok = min(tb.B, tb.tok+(tb.R-rate)*0.2)
	if tb.tok <= 0 {
		tb.tok, er.link = 0, tb.R
	} else {
		er.link = 0
	}
}

type policerRes struct {
	falls          int     // Rueckfaelle aller Bild-Plaetze (nicht in die Pause)
	hShare         float64 // Anteil der Zeit mit dem Sprecherplatz auf h
	ok, fail, long int     // Proben: bestanden, gescheitert, davon lang (Zielrate ab Bedarf x probeHeadLong)
	padMB          float64
	back           time.Duration // nach dem Ende des Policers: bis volle Belegung (h bzw. h und Kacheln auf m) 10 s haelt; -1 = nie
}

// policerRun: n Sender (Gruppe: n-1 Kacheln), Sprecherwechsel alle rot (0 = keiner), Policer 7 % unter der vollen
// Belegung mit Eimer bs x Rate, d lang; danach free frei.
func policerRun(t *testing.T, eigen bool, n int, rot time.Duration, bs float64, d, free time.Duration) (r policerRes) {
	t.Helper()
	return policerRunAt(t, eigen, n, rot, 0, 0, bs, d, free)
}

// policerRunAt: wie policerRun, aber mit Policer-Rate R (0 = 7 % unter der vollen Belegung) und Ruecksprung des
// GCC-Ziels am Policer back x R (0 = Modell, 0,85).
func policerRunAt(t *testing.T, eigen bool, n int, rot time.Duration, R, back, bs float64, d, free time.Duration) (r policerRes) {
	t.Helper()
	er := neuEigen(t, eigen, 1250e3, 810e3, n)
	er.l.firstUsed = true
	er.grow = 400e3
	er.back = back
	er.tick()
	ps := er.senders()
	er.until("m", 2*time.Second)
	if R == 0 {
		R = 0.93 * (1250e3 + 500e3*float64(n-1) + estAudio)
	}
	tb := &tokenBucket{R: R, B: R * bs, tok: R * bs}
	full := "h"
	for i := 1; i < n; i++ {
		full += "m"
	}
	prev, start, pev0, pad0 := er.video(), er.now, len(er.pevs), er.padMB
	onH := 0
	step := func() {
		if rot > 0 {
			er.speak(ps[int(time.Duration(er.now-start)/rot)%len(ps)])
		}
		tb.step(er)
		er.tick()
		v := er.video()
		for i := range v {
			if i < len(prev) && vrank(v[i]) < vrank(prev[i]) && v[i] != 'p' {
				r.falls++
			}
		}
		prev = v
	}
	for er.now-start < int64(d) {
		step()
		if er.layer() == "h" {
			onH++
		}
	}
	r.hShare = float64(onH) * 0.2 / d.Seconds()
	longT := (1250e3+500e3*float64(n-1))*probeHeadLong + estAudio
	for _, e := range er.pevs[pev0:] {
		switch e.Probe {
		case probeStart:
			if float64(e.ProbeKbit)*1000 >= 0.99*longT {
				r.long++
			}
		case probeOK:
			r.ok++
		case probeFail:
			r.fail++
		}
	}
	r.padMB = er.padMB - pad0
	tb.R, r.back = 0, -1
	t0, at := er.now, int64(-1)
	for er.now-t0 < int64(free) && r.back < 0 {
		step()
		switch {
		case er.video() != full:
			at = -1
		case at < 0:
			at = er.now
		case er.now-at >= int64(10*time.Second):
			r.back = time.Duration(at - t0)
		}
	}
	return r
}

// TestProbingPolicerLangeProbe: 1:1, Policer 7 % unter h, Eimer 0,15 s, 10 min. Vorher (Stufe 3, e3dc945): jede kurze
// Probe bestand, 8 Rueckfaelle (jeder binnen upFail, Aufschub bis 2 min), nach dem Ende des Policers h erst nach 32 s.
// Jetzt: nach dem ersten Rueckfall faehrt jede Probe auf h lang und scheitert am leeren Eimer - 1 Rueckfall, h nach
// dem Ende des Policers nach 15 bzw. 21 s. Preis: in 10 min 30 lange Proben, 4,3 MB Fuelldaten (vorher 0,65).
// Gegenprobe: lange Probe wie die kurze (Fenster 0,6 s, Fuellziel 1,05) - der Zyklus bleibt.
func TestProbingPolicerLangeProbe(t *testing.T) {
	for _, eigen := range []bool{false, true} {
		r := policerRun(t, eigen, 1, 0, 0.15, 10*time.Minute, 2*time.Minute)
		t.Logf("eigen=%v: %+v", eigen, r)
		if r.falls > 2 || r.long < 10 || r.back < 0 || r.back > 30*time.Second {
			t.Fatalf("eigen=%v: %+v - soll hoechstens 2 Rueckfaelle, lange Proben, h binnen 30 s nach dem Ende", eigen, r)
		}
	}
	oldW, oldH := probeWinLong, probeHeadLong
	probeWinLong, probeHeadLong = probeWinMax, probeHead
	defer func() { probeWinLong, probeHeadLong = oldW, oldH }()
	r := policerRun(t, false, 1, 0, 0.15, 10*time.Minute, 0)
	t.Logf("Gegenprobe (lange Probe wie die kurze): %+v", r)
	if r.falls < 5 {
		t.Fatalf("Gegenprobe: nur %d Rueckfaelle - der Test prueft die lange Probe nicht", r.falls)
	}
}

// TestProbingPolicerGruppe (H2): Gruppe mit 3 Sendern, Sprecherwechsel alle 20 s, Policer 7 % unter der vollen Belegung
// (h + 2 x m), Eimer 0,15 und 0,6 s, 10 min. Vor Stufe 3b hing die Bremse am Platz (Aufschub s.dw, Zaehler der frischen
// Rueckfaelle) und begann mit jedem Sprecherwechsel von vorn; die kurze Probe bestand wieder: gcc 107 bzw. 90, eigen 87
// bzw. 82 Rueckfaelle. Mit der Bremse am Bein (bis Stufe 3e, hier mit bremseGruppe): 7 bzw. 28 (eigen 9 bzw. 27), der
// Sprecherplatz 96 bis 98 % der Zeit auf h.
// Seit Stufe 3f gilt die Bremse in der Gruppe nicht (soloCheck): Das ist der bewusste Preis - wieder 107/90 bzw. 87/82
// Rueckfaelle, Sprecher 75 bis 86 % auf h, keine lange Probe. Der zweite Teil haelt diesen Stand fest (nicht schlechter
// als vor Stufe 3b), der erste die Mechanik der Bremse.
func TestProbingPolicerGruppe(t *testing.T) {
	t.Run("bremse", func(t *testing.T) {
		bremseGruppe(t)
		for _, eigen := range []bool{false, true} {
			for _, bs := range []float64{0.15, 0.6} {
				r := policerRun(t, eigen, 3, 20*time.Second, bs, 10*time.Minute, 0)
				t.Logf("eigen=%v, Eimer %.2f s: %+v", eigen, bs, r)
				if r.falls > 40 || r.hShare < 0.9 {
					t.Fatalf("eigen=%v, Eimer %.2f s: %+v - soll hoechstens 40 Rueckfaelle, h mindestens 90 %%", eigen, bs, r)
				}
			}
		}
	})
	t.Run("ohne", func(t *testing.T) {
		for _, eigen := range []bool{false, true} {
			for _, bs := range []float64{0.15, 0.6} {
				r := policerRun(t, eigen, 3, 20*time.Second, bs, 10*time.Minute, 0)
				t.Logf("eigen=%v, Eimer %.2f s: %+v", eigen, bs, r)
				if r.falls > 120 || r.hShare < 0.7 || r.long != 0 {
					t.Fatalf("eigen=%v, Eimer %.2f s: %+v - soll hoechstens 120 Rueckfaelle, h mindestens 70 %%, keine lange Probe", eigen, bs, r)
				}
			}
		}
	})
}

// TestProbingPolicerZwischenbelegung (Stufe 3d, Pruefer bwe3c H): Gruppe 3, Policer 1580k - knapp ueber mmm (1540k mit
// Ton), knapp unter hll (1590k). Zuerst faellt hml (Zielrate 2225k), dann hll (1822k). In Stufe 3c zaehlte ein
// frischer Rueckfall auf eine kleinere Zielrate als die der Bremse nicht: hll fiel alle 20 bis 40 s zurueck, und die
// scheiternden langen Proben auf 2225k frischten die Bremse immer wieder auf - gcc 77/89 (Eimer 0,15/0,6 s, Wechsel
// 20 s), ohne Wechsel 35/24 Rueckfaelle in 10 min. Jetzt senkt er die Bremse wie in Stufe 3b: 9/12 bzw. 8/12.
func TestProbingPolicerZwischenbelegung(t *testing.T) {
	bremseGruppe(t) // Mechanik der Bremse (seit Stufe 3f nur im 1:1)
	for _, eigen := range []bool{false, true} {
		for _, rot := range []time.Duration{20 * time.Second, 0} {
			for _, bs := range []float64{0.15, 0.6} {
				r := policerRunAt(t, eigen, 3, rot, 1580e3, 0, bs, 10*time.Minute, 0)
				t.Logf("eigen=%v Wechsel %v Eimer %.2f s: %+v", eigen, rot, bs, r)
				if r.falls > 20 {
					t.Errorf("eigen=%v Wechsel %v Eimer %.2f s: %d Rueckfaelle (soll hoechstens 20)", eigen, rot, bs, r.falls)
				}
			}
		}
	}
}

// TestProbingPolicerGetragen (Stufe 3e, Pruefer bwe3d H2): Gruppe 3, Policer 900k knapp ueber mll (840k mit Ton),
// Eimer 0,6 s. Die Probe auf mll besteht, 1,6 s danach geht das Bein ohne Probe auf mml (der Eimer ist noch voll),
// 2 s spaeter faellt alles auf lll. Bis Stufe 3d erledigte der Schritt ohne Probe den geprobten ("getragen"), der
// Rueckfall zaehlte nie: keine Bremse, alle ~13 s von vorn - gcc 104/96, eigen 86/96 Rueckfaelle in 10 min (Wechsel
// 20 s / ohne). Jetzt tritt der Schritt ohne Probe an die Stelle des offenen: 5.
func TestProbingPolicerGetragen(t *testing.T) {
	bremseGruppe(t) // Mechanik der Bremse (seit Stufe 3f nur im 1:1)
	for _, eigen := range []bool{false, true} {
		for _, rot := range []time.Duration{20 * time.Second, 0} {
			r := policerRunAt(t, eigen, 3, rot, 900e3, 0, 0.6, 10*time.Minute, 0)
			t.Logf("eigen=%v Wechsel %v: %+v", eigen, rot, r)
			if r.falls > 20 {
				t.Errorf("eigen=%v Wechsel %v: %d Rueckfaelle (soll hoechstens 20)", eigen, rot, r.falls)
			}
		}
	}
}

// TestProbingPolicerFlacherRuecksprung (Stufe 3d, Pruefer bwe3c M1): Springt das GCC-Ziel am Policer tiefer zurueck
// als im Modell (0,76 statt 0,85 x Policer-Rate), darf der Rueckfall nicht als Einbruch gelten. Mit der Grenze 0,7 x
// Zielrate (mit Reserve, Stufe 3c) galt er bei einem Policer knapp ueber hml (1980k) als Einbruch: gcc 45/80 Rueckfaelle
// (Eimer 0,15/0,6 s, Wechsel 20 s), keine lange Probe. Mit 0,75 x Rate vor dem Schritt: 8/10, eigen 8/14.
func TestProbingPolicerFlacherRuecksprung(t *testing.T) {
	bremseGruppe(t) // Mechanik der Bremse (seit Stufe 3f nur im 1:1)
	for _, eigen := range []bool{false, true} {
		for _, bs := range []float64{0.15, 0.6} {
			r := policerRunAt(t, eigen, 3, 20*time.Second, 1980e3, 0.76, bs, 10*time.Minute, 0)
			t.Logf("eigen=%v Eimer %.2f s: %+v", eigen, bs, r)
			if r.falls > 20 || r.long == 0 {
				t.Errorf("eigen=%v Eimer %.2f s: %d Rueckfaelle, %d lange Proben (soll hoechstens 20, lange Proben)", eigen, bs, r.falls, r.long)
			}
		}
	}
}

// TestProbingPolicerGrosserEimer: Ein Eimer, den auch die lange Probe nicht leert (1:1, 0,6 s), faengt erst die Regel
// "probeFreshN frische Rueckfaelle in Folge = Fehlversuch" (Aufschub am Bein): 9 Rueckfaelle in 10 min, ohne die Regel
// 36 (Gruppe, Sprecherwechsel 20 s: 28 gegen 71). Preis: nach dem Ende des Policers kommt h erst nach dem Aufschub
// (bis 2 min, wie vor Stufe 3b).
func TestProbingPolicerGrosserEimer(t *testing.T) {
	r := policerRun(t, false, 1, 0, 0.6, 10*time.Minute, 0)
	t.Logf("mit Regel: %+v", r)
	old := probeFreshN
	probeFreshN = 1 << 20
	defer func() { probeFreshN = old }()
	r0 := policerRun(t, false, 1, 0, 0.6, 10*time.Minute, 0)
	t.Logf("ohne Regel: %+v", r0)
	if r.falls > 12 || r0.falls < 2*r.falls {
		t.Fatalf("Rueckfaelle mit Regel %d, ohne %d", r.falls, r0.falls)
	}
}

// TestProbingPolicerFrei: ohne Policer kein Unterschied - keine lange Probe, kein Rueckfall (1:1 und Gruppe mit
// Sprecherwechsel).
func TestProbingPolicerFrei(t *testing.T) {
	for _, c := range []struct {
		n   int
		rot time.Duration
	}{{1, 0}, {3, 20 * time.Second}} {
		for _, eigen := range []bool{false, true} {
			er := neuEigen(t, eigen, 1250e3, 810e3, c.n)
			er.l.firstUsed = true
			er.grow = 400e3
			ps := er.senders()
			pev0 := len(er.pevs)
			for k := 0; k < 3000; k++ {
				if c.rot > 0 {
					er.speak(ps[int(time.Duration(k)*200*time.Millisecond/c.rot)%len(ps)])
				}
				er.tick()
			}
			if er.l.fb != (freshBrake{upAt: er.l.fb.upAt, upT: er.l.fb.upT, upPre: er.l.fb.upPre, upLow: er.l.fb.upLow}) {
				t.Fatalf("n=%d eigen=%v: Bremse auf freier Leitung: %+v", c.n, eigen, er.l.fb)
			}
			for _, e := range er.pevs[pev0:] {
				if e.Probe == probeStart && e.ProbeKbit > 2000 && c.n == 1 {
					t.Fatalf("n=%d eigen=%v: lange Probe auf freier Leitung: %+v", c.n, eigen, e)
				}
			}
		}
	}
}

// stoerGruppe: Gruppe mit 3 Sendern auf freier Leitung, 10 min Stoerschuebe von 2 s (das GCC-Ziel faellt auf
// 600 kbit/s; Abstaende gaps der Reihe nach, der erste nach first), dann 3 min ohne. Sprecherwechsel alle rot (0 =
// keiner). Ergebnis: Anteil der Takte mit dem Sprecherplatz auf h bzw. mit allen Plaetzen auf l (in den 10 min),
// Rueckkehr nach dem Ende des letzten Schubs (volle Belegung hmm 10 s am Stueck; -1 = nicht binnen 3 min) und der
// laengste Aufschub am Bein (freshBrake.d).
func stoerGruppe(t *testing.T, eigen bool, rot, first time.Duration, gaps []time.Duration) (hShare, lllShare float64, back, maxD time.Duration) {
	t.Helper()
	return stoerGruppeTief(t, eigen, rot, first, gaps, 0)
}

// stoerGruppeTief: wie stoerGruppe, das GCC-Ziel faellt im Schub auf schub (0 = 600 kbit/s).
func stoerGruppeTief(t *testing.T, eigen bool, rot, first time.Duration, gaps []time.Duration, schub float64) (hShare, lllShare float64, back, maxD time.Duration) {
	t.Helper()
	er := neuEigen(t, eigen, 1250e3, 810e3, 3)
	er.schub = schub
	er.l.firstUsed = true
	er.grow = 400e3
	er.tick()
	ps := er.senders()
	er.until("m", 2*time.Second)
	start, next, k := er.now, first, 0
	var lastEnd time.Duration
	onH, lll, n, at := 0, 0, 0, int64(-1)
	back = -1
	for er.now-start < int64(13*time.Minute) && back < 0 {
		el := time.Duration(er.now - start)
		if rot > 0 {
			er.speak(ps[int(el/rot)%len(ps)])
		}
		was := er.loss
		if el >= next+2*time.Second {
			next = el + gaps[k%len(gaps)]
			k++
		}
		er.loss = el < 10*time.Minute && el >= next
		if was && !er.loss {
			lastEnd = el
		}
		er.tick()
		maxD = max(maxD, er.l.fb.d)
		v := er.video()
		if el < 10*time.Minute {
			n++
			if v[0] == 'h' {
				onH++
			}
			if v == "lll" {
				lll++
			}
			continue
		}
		switch {
		case v != "hmm":
			at = -1
		case at < 0:
			at = er.now
		case er.now-at >= int64(10*time.Second):
			back = time.Duration(at-start) - lastEnd
		}
	}
	return float64(onH) / float64(n), float64(lll) / float64(n), back, maxD
}

// TestProbingGruppeStoerschuebe (Stufe 3c, Pruefer bwe3b H1): Gruppe 3, freie Leitung mit Stoerschueben wie
// TestProbingStoerschuebe. Mit der Bremse von Stufe 3b (f64544e) zaehlte jeder Schub bald nach einem geprobten Schritt
// als frischer Rueckfall: rate fiel auf die Zielrate einer Kachel-Stufe (mll, 960k), der Deckel hielt alle Plaetze auf
// l, und der Aufschub am Bein wuchs bis 5 min - Sprecher auf h 12 bzw. 14 %, lll 76 bzw. 67 % der Takte, hmm nach dem
// letzten Schub nicht binnen 3 min (ohne Wechsel 30 s, Sprecher auf h 12 %). Jetzt zaehlen nur Rueckfaelle nach dem
// Muster eines Policers (freshBrake), und die Zahlen sind wieder die von vor Stufe 3b (4660e81): Sprecher auf h 47
// bzw. 49 % (ohne Wechsel 59 bzw. 58 %), lll 22 bis 25 %, hmm 12,4 bis 13,2 s nach dem letzten Schub - so lange haelt
// der Aufschub der Plaetze nach dem Rueckfall im Schub (upFail), wie vorher. Zwei weitere Schub-Folgen: kein Aufschub
// am Bein (f64544e: 1 bis 5 min, Sprecher auf h 5 bis 21 %). Seit Stufe 3f gilt die Bremse in der Gruppe nicht
// (soloCheck): dieselben Zahlen - bei diesem tiefen Schub (600k) griff sie seit 3c ohnehin nicht. Der Unterschied liegt
// bei flachen Schueben (TestProbingGruppeFlacheSchuebe).
func TestProbingGruppeStoerschuebe(t *testing.T) {
	gaps := []time.Duration{30 * time.Second, 17 * time.Second, 23 * time.Second, 10 * time.Second}
	for _, eigen := range []bool{false, true} {
		for _, rot := range []time.Duration{20 * time.Second, 0} {
			h, lll, back, d := stoerGruppe(t, eigen, rot, 10*time.Second, gaps)
			t.Logf("eigen=%v Wechsel %v: Sprecher auf h %.0f %%, lll %.0f %%, hmm %v nach dem letzten Schub, Aufschub am Bein hoechstens %v",
				eigen, rot, 100*h, 100*lll, back, d)
			lim := 30 * time.Second
			if rot == 0 {
				lim = 20 * time.Second
			}
			if h < 0.4 || lll > 0.3 || back < 0 || back > lim || d != 0 {
				t.Errorf("eigen=%v Wechsel %v: h %.0f %% (soll >= 40), lll %.0f %% (<= 30), Rueckkehr %v (<= %v), Aufschub am Bein %v (soll keiner)",
					eigen, rot, 100*h, 100*lll, back, lim, d)
			}
		}
	}
	// weitere Schub-Folgen
	for i, g := range [][]time.Duration{
		{12 * time.Second, 27 * time.Second, 19 * time.Second, 14 * time.Second, 29 * time.Second},
		{21 * time.Second, 10 * time.Second, 16 * time.Second, 25 * time.Second, 11 * time.Second, 30 * time.Second},
	} {
		for _, eigen := range []bool{false, true} {
			h, lll, back, d := stoerGruppe(t, eigen, 20*time.Second, time.Duration(13+4*i)*time.Second, g)
			t.Logf("Folge %d eigen=%v: h %.0f %%, lll %.0f %%, hmm %v, Aufschub am Bein %v", i+1, eigen, 100*h, 100*lll, back, d)
			if d != 0 || h < 0.3 {
				t.Errorf("Folge %d eigen=%v: Aufschub am Bein %v, h %.0f %%", i+1, eigen, d, 100*h)
			}
		}
	}
}

// TestProbingGruppeFlacheSchuebe (Stufe 3f): Gruppe 3, freie Leitung mit flachen Stoerschueben (GCC-Ziel im Schub
// 1500 bzw. 1550 kbit/s, 2 s, drei Folgen wie TestProbingGruppeStoerschuebe), Sprecherwechsel 20 s bzw. keiner. Mit der
// Bremse in der Gruppe (Stand bis 3e, bremseGruppe) zaehlte ein flacher Schub bald nach einem geprobten Schritt oft als
// Policer-Muster (low/pre ueber probeSlump): Aufschub am Bein, volle Belegung erst 58 bis 99 s nach dem letzten Schub.
// Ohne Bremse in der Gruppe (seit 3f): 2 bis 16 s. Die Gegenprobe haelt fest, dass der Test die Bremse sieht.
func TestProbingGruppeFlacheSchuebe(t *testing.T) {
	seqs := [][]time.Duration{
		{30 * time.Second, 17 * time.Second, 23 * time.Second, 10 * time.Second},
		{12 * time.Second, 27 * time.Second, 19 * time.Second, 14 * time.Second, 29 * time.Second},
		{21 * time.Second, 10 * time.Second, 16 * time.Second, 25 * time.Second, 11 * time.Second, 30 * time.Second},
	}
	run := func(t *testing.T) (worst time.Duration) {
		for _, sch := range []float64{1500e3, 1550e3} {
			for _, eigen := range []bool{false, true} {
				for _, rot := range []time.Duration{20 * time.Second, 0} {
					for i, q := range seqs {
						h, _, back, d := stoerGruppeTief(t, eigen, rot, time.Duration(10+3*i)*time.Second, q, sch)
						t.Logf("Schub %.0fk eigen=%v Wechsel %v Folge %d: h %.0f %%, hmm %v nach dem letzten Schub, Aufschub am Bein %v",
							sch/1e3, eigen, rot, i+1, 100*h, back, d)
						if back < 0 {
							back = time.Hour
						}
						worst = max(worst, back)
					}
				}
			}
		}
		return worst
	}
	if w := run(t); w > 30*time.Second {
		t.Errorf("ohne Bremse in der Gruppe: volle Belegung erst %v nach dem letzten Schub (soll hoechstens 30 s)", w)
	}
	t.Run("gegenprobe", func(t *testing.T) {
		bremseGruppe(t)
		if w := run(t); w <= 60*time.Second {
			t.Errorf("Gegenprobe mit Bremse in der Gruppe: hoechstens %v - der Test erkennt die Bremse nicht", w)
		}
	})
}

// TestFreshBrake: Buchfuehrung der Bremse.
func TestFreshBrake(t *testing.T) {
	var b freshBrake
	s := int64(time.Second)
	down := func(now int64, bud float64) (string, bool) { return b.down(now, bud) }
	b.stepped(1500e3, 1000e3, 10*s)
	if art, f := down(10*s, 1400e3); art != "" || f || b.at != 0 || b.upAt == 0 {
		t.Fatalf("Rueckfall im selben Takt zaehlt: %q %+v", art, b)
	}
	if art, f := down(15*s, 1400e3); art != freshCount || f || !b.long || b.n != 1 || b.capNeed(40e3) == 0 {
		t.Fatalf("erster frischer Rueckfall: %q %+v", art, b)
	}
	if !b.longFor(1400e3) || b.longFor(1300e3) || !b.on(1400e3) {
		t.Fatalf("Zielraten: %+v", b)
	}
	b.passLong(1500e3)
	if b.long || b.capNeed(40e3) != 0 {
		t.Fatalf("lange Probe bestanden: %+v", b)
	}
	b.stepped(1500e3, 1000e3, 20*s)
	if art, f := down(25*s, 1400e3); art != freshFail || !f || b.d != 2*layerDwell || !b.blocks(1500e3, 30*s) || b.blocks(1500e3, 34*s) {
		t.Fatalf("zweiter frischer Rueckfall: Aufschub am Bein %q %+v", art, b)
	}
	b.stepped(1500e3, 1000e3, 40*s)
	if art, f := down(60*s, 1400e3); art != freshHeld || f || b.n != 0 || !b.long {
		t.Fatalf("Rueckfall nach probeFresh setzt die Folge zurueck, die lange Probe bleibt: %q %+v", art, b)
	}
	b.failLong(70*s, 3*time.Second) // Abstand kuerzer als der Aufschub: der Aufschub zaehlt
	b.decay(70*s + int64(upHold) + int64(b.d) - 1)
	if b.at == 0 {
		t.Fatal("zu frueh erledigt")
	}
	// Abstand bis zur naechsten langen Probe laenger als der Aufschub (probeGapMaxLong): Die Bremse wartet ihn ab,
	// sonst kaeme danach eine kurze Probe
	b.failLong(80*s, probeGapMaxLong)
	if b.decay(80*s + int64(upHold) + int64(probeGapMaxLong) - 1); b.at == 0 || !b.longFor(1500e3) {
		t.Fatalf("vor der naechsten langen Probe erledigt: %+v", b)
	}
	b.decay(80*s + int64(upHold) + int64(probeGapMaxLong))
	if b.at != 0 || b.long {
		t.Fatalf("nicht erledigt: %+v", b)
	}
	// Stufe 3c: nur Rueckfaelle nach dem Muster eines Policers. Einbruch: unter 0,75 x Rate vor dem Schritt (1000k)
	b = freshBrake{}
	b.stepped(1500e3, 1000e3, 10*s)
	if art, _ := down(12*s, 700e3); art != freshSlump || b.at != 0 || b.upAt != 0 {
		t.Fatalf("Einbruch (Budget unter 0,75 x Rate vor dem Schritt) zaehlt: %q %+v", art, b)
	}
	b.stepped(1500e3, 1000e3, 20*s)
	b.watch(600e3) // der Schub beginnt, bevor der Platz faellt
	b.watch(1300e3)
	if art, _ := down(22*s, 1300e3); art != freshSlump || b.at != 0 {
		t.Fatalf("Einbruch zwischen Schritt und Rueckfall zaehlt: %q %+v", art, b)
	}
	b.stepped(1500e3, 1000e3, 30*s)
	b.watch(800e3) // Policer knapp ueber dem Stand davor, GCC springt auf 0,8 x zurueck: kein Einbruch
	if art, _ := down(32*s, 1300e3); art != freshCount || b.at == 0 || b.n != 1 || b.rate != 1500e3 {
		t.Fatalf("Rueckfall eines Policers zaehlt nicht: %q %+v", art, b)
	}
	// Stufe 3d: ein frischer Rueckfall auf eine kleinere Zielrate senkt die Bremse (Policer zwischen zwei
	// Zwischenbelegungen) und zaehlt in der Folge
	b.stepped(1200e3, 900e3, 40*s)
	if art, f := down(42*s, 1100e3); art != freshFail || !f || b.rate != 1200e3 || b.n != 2 || !b.on(1100e3) {
		t.Fatalf("Rueckfall auf kleinere Zielrate: %q %+v", art, b)
	}
	b.stepped(1500e3, 1000e3, 50*s)
	if b.beyond(1400e3) || b.upAt == 0 {
		t.Fatal("Schritt darunter schliesst den geprobten")
	}
	if !b.beyond(1700e3) || b.upAt != 0 {
		t.Fatalf("Schritt ohne Probe darueber schliesst den geprobten nicht: %+v", b)
	}
	if art, _ := down(52*s, 1300e3); art != "" || b.n != 2 {
		t.Fatalf("Rueckfall nach einem Schritt ohne Probe darueber zaehlt: %q %+v", art, b)
	}
	b.stepped(1500e3, 1000e3, 60*s)
	b.n = 1
	down(62*s, 500e3)
	if b.n != 0 || !b.long {
		t.Fatalf("Einbruch unterbricht die Folge nicht bzw. hebt die lange Probe auf: %+v", b)
	}
}

// TestFreshBrakeDecayOffen (Pruefer bwe3c, N1): Verfaellt die Bremse, waehrend ein geprobter Schritt offen ist, behaelt
// der Schritt seine Rate davor und das kleinste Budget seither - sonst gilt ein Einbruch danach als frischer Rueckfall
// (bis zur Nachbesserung in Stufe 3c: pre = 0, Grenze 0). Gegenprobe: derselbe Ablauf mit verlorenem Vorher-Stand.
func TestFreshBrakeDecayOffen(t *testing.T) {
	s := int64(time.Second)
	run := func(lose bool) string {
		b := freshBrake{rate: 2000e3, n: 1, long: true, at: 1 * s}
		b.stepped(1800e3, 1000e3, 20*s)
		b.watch(600e3) // Schub
		b.decay(20*s + int64(upHold))
		if lose { // so verfiel die Bremse bis zur Nachbesserung
			b.upPre, b.upLow = 0, 0
		}
		if b.at != 0 || b.upAt == 0 {
			t.Fatalf("Ausgangslage: %+v", b)
		}
		art, _ := b.down(25*s, 1200e3)
		return art
	}
	if art := run(false); art != freshSlump {
		t.Fatalf("nach dem Verfall: %q, erwartet %q", art, freshSlump)
	}
	if art := run(true); art != freshCount {
		t.Fatalf("Gegenprobe: %q - der Test erkennt den verlorenen Vorher-Stand nicht", art)
	}
}

// TestProbingBremseHaeltLaufendes: Der Deckel der Bremse verhindert nur das Wachsen. Kam der frisch gefallene Schritt
// mit wenig Zuwachs (hier 8 % - eine Kachel aus der Pause), laege 90 % seiner Zielrate unter der laufenden Auswahl;
// die laufenden Schichten bleiben trotzdem (bis zur Nachbesserung fiel hier eine Kachel auf l).
func TestProbingBremseHaeltLaufendes(t *testing.T) {
	bremseGruppe(t) // Mechanik der Bremse (seit Stufe 3f nur im 1:1)
	er := neuEigen(t, false, 1250e3, 810e3, 3)
	er.l.firstUsed = true
	er.grow = 400e3
	er.run(30 * time.Second)
	if v := er.video(); v != "hmm" {
		t.Fatalf("Ausgangslage %q", v)
	}
	tgt := (1250e3+2*500e3)*upHeadroom + audioRateUDP
	er.l.fb = freshBrake{rate: 1.08 * tgt, n: 1, long: true, at: er.now}
	for k := 0; k < 25; k++ {
		er.tick()
		if v := er.video(); v != "hmm" {
			t.Fatalf("Takt %d: %q - der Deckel nimmt laufende Schichten zurueck", k, v)
		}
	}
}

// TestProbingBremseWegwechsel (N2): Die Bremse nach frischem Rueckfall ueberlebt einen Wegwechsel udp -> tcp -> udp
// (remember/recall wie Aufschub und Proben-Abstand). 1:1, Policer 7 % unter h, Eimer 0,15 s: Nach 3 min faehrt jede
// Probe auf h lang. Nach dem Wechsel muss die erste Probe auf h wieder lang sein - holte recall die Bremse nicht
// zurueck, kaeme eine kurze, die am vollen Eimer besteht, und der Zyklus begaenne von vorn (Gegenprobe: die Bremse im
// gemerkten Eintrag geloescht).
func TestProbingBremseWegwechsel(t *testing.T) {
	run := func(gegen bool) (first int, fb freshBrake) {
		er := neuEigen(t, false, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.grow = 400e3
		er.tick()
		er.until("m", 2*time.Second)
		R := 0.93 * (1250e3 + estAudio)
		tb := &tokenBucket{R: R, B: R * 0.15, tok: R * 0.15}
		step := func() { tb.step(er); er.tick() }
		for end := er.now + int64(3*time.Minute); er.now < end; {
			step()
		}
		old := er.l.fb
		if old.at == 0 || !old.long {
			t.Fatalf("keine Bremse nach 3 min Policer: %+v", old)
		}
		swap := func(ref string, path Path) {
			er.r.RemoveLeg(er.l.Ref)
			if gegen {
				k := memKey{recv: "empf"}
				e := er.r.mem[k]
				e.fb = freshBrake{}
				er.r.mem[k] = e
			}
			l, err := er.r.AddLeg(Grant{Room: "raumschaetzer", Peer: "empf", Leg: ref, Role: RoleBoth}, path, Want{Audio: 1, Speaker: true})
			if err != nil {
				t.Fatal(err)
			}
			if path == PathUDP {
				l.Ext = func() int { return int(er.gcc) }
				l.ExtOver = func() bool { return er.over >= 2 }
			}
			er.l, er.sent = l, 0
		}
		swap("empf-tcp", PathTCP)
		for k := 0; k < 10; k++ {
			step()
		}
		swap("empf-udp", PathUDP)
		pev0 := len(er.pevs)
		for end := er.now + int64(time.Minute); er.now < end && first == 0; {
			step()
			for _, e := range er.pevs[pev0:] {
				if e.Probe == probeStart && first == 0 {
					first = e.ProbeKbit
				}
			}
		}
		return first, er.l.fb
	}
	longT := 1250e3*probeHeadLong + estAudio
	first, fb := run(false)
	t.Logf("erste Probe nach dem Wechsel %d kbit/s (lang ab %.0f), Bremse %+v", first, longT/1000, fb)
	if float64(first)*1000 < 0.99*longT || fb.at == 0 {
		t.Fatalf("nach dem Wegwechsel: erste Probe %d kbit/s, Bremse %+v - recall holt die Bremse nicht zurueck", first, fb)
	}
	first, fb = run(true)
	t.Logf("Gegenprobe: erste Probe %d kbit/s, Bremse %+v", first, fb)
	if first == 0 || float64(first)*1000 >= 0.99*longT {
		t.Fatalf("Gegenprobe: erste Probe %d kbit/s - der Test erkennt die fehlende Bremse nicht", first)
	}
}

// TestProbingBremseUmverteilung (Pruefer bwe3c, N1): Faellt eine Kachel, weil im selben Takt der Sprecher steigt, sinkt
// die Auswahl des Beins nicht - das ist kein Rueckfall der Leitung, auch wenn ein geprobter Schritt offen ist (Pruefer
// bwe3b: mmm -> hml nach geprobtem l -> m zaehlte als frischer Rueckfall auf 960k). Gruppe 3 mit Stoerschueben wie
// TestProbingGruppeStoerschuebe: Ein erster Lauf sucht den ersten Takt mit Umverteilung (ein Platz hinauf, einer
// hinab); ein zweiter, gleicher Lauf oeffnet davor einen geprobten Schritt, den kein Schritt ueberbietet (beyond) und
// den kein Einbruch erklaert, und prueft: keine Bremse, der Schritt bleibt offen, Logzeile "umverteilung".
func TestProbingBremseUmverteilung(t *testing.T) {
	bremseGruppe(t) // Mechanik der Bremse (seit Stufe 3f nur im 1:1)
	gaps := []time.Duration{30 * time.Second, 17 * time.Second, 23 * time.Second, 10 * time.Second}
	run := func(stop int, inject bool) (k int, before, after string, er *estRoom) {
		er = neuEigen(t, false, 1250e3, 810e3, 3)
		er.l.firstUsed = true
		er.grow = 400e3
		er.tick()
		ps := er.senders()
		er.until("m", 2*time.Second)
		start, next, g := er.now, 10*time.Second, 0
		for k = 0; k < 3000; k++ {
			el := time.Duration(er.now - start)
			er.speak(ps[int(el/(20*time.Second))%len(ps)])
			if el >= next+2*time.Second {
				next = el + gaps[g%len(gaps)]
				g++
			}
			er.loss = el >= next
			before = er.video()
			if k == stop && inject {
				er.l.fb = freshBrake{}
				er.l.fb.stepped(10e6, 0, er.now)
			}
			er.tick()
			after = er.video()
			up, down := false, false
			for i := range after {
				up = up || vrank(after[i]) > vrank(before[i])
				down = down || vrank(after[i]) < vrank(before[i]) && after[i] != 'p'
			}
			if k == stop || stop < 0 && up && down && !er.loss {
				return
			}
		}
		t.Fatal("kein Takt mit Umverteilung")
		return
	}
	k, b0, a0, _ := run(-1, false)
	_, b1, a1, er := run(k, true)
	t.Logf("Takt %d: %s -> %s (Wiederholung %s -> %s), Bremse %+v, frisch %+v", k, b0, a0, b1, a1, er.l.fb, er.fresh)
	if b1 != b0 || a1 != a0 {
		t.Fatalf("Wiederholung weicht ab: %s -> %s statt %s -> %s", b1, a1, b0, a0)
	}
	if er.l.fb.at != 0 || er.l.fb.upAt == 0 {
		t.Fatalf("Umverteilung zaehlt als frischer Rueckfall: %+v", er.l.fb)
	}
	if n := len(er.fresh); n == 0 || er.fresh[n-1].Why != freshSpread {
		t.Fatalf("Logzeile: %+v", er.fresh)
	}
}

// TestProbingBremseNurEinsZuEins (Stufe 3f): Die Bremse gilt nur, solange hoechstens ein anderer Teilnehmer Bild
// liefert (soloCheck). 1:1 mit Policer 7 % unter h, Eimer 0,15 s: nach 3 min laeuft die Bremse (lange Probe, Deckel).
// Ein zweiter Sender mit Bild kommt dazu (Gruppe): Die Bremse endet im selben Takt samt offenem Schritt - kein Deckel,
// keine lange Probe, kein Aufschub am Bein, kein Abstand ueber den der kurzen Probe (20 s; ohne die Kuerzung blieben
// 40 s, h erst nach 19,8 s), keine Zeile "frisch"; der Sprecher kommt wieder auf h (am Policer pendelt er, das ist der
// Preis). Er geht wieder (1:1): Die Bremse beginnt frisch - der naechste frische Rueckfall ist der
// erste einer neuen Folge (n = 1, kein Aufschub am Bein). Gegenprobe: mit der Bremse in der Gruppe (bremseGruppe)
// bleibt sie beim Beitritt stehen.
func TestProbingBremseNurEinsZuEins(t *testing.T) {
	run := func(t *testing.T, gegen bool) {
		er := neuEigen(t, false, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.grow = 400e3
		er.tick()
		er.until("m", 2*time.Second)
		R := 0.93 * (1250e3 + estAudio)
		tb := &tokenBucket{R: R, B: R * 0.15, tok: R * 0.15}
		step := func() { tb.step(er); er.tick() }
		for end := er.now + int64(3*time.Minute); er.now < end; {
			step()
		}
		if fb := er.l.fb; fb.at == 0 || !fb.long || !er.l.solo {
			t.Fatalf("1:1: keine Bremse nach 3 min Policer (solo %v): %+v", er.l.solo, fb)
		}
		// Beitritt eines zweiten Senders mit Bild
		if !er.r.AddRemote("b1", "fern9", Desktop, nil) {
			t.Fatal("aufnehmen")
		}
		on := map[*fwd.Source]bool{}
		for _, k := range []string{"l", "m", "h"} {
			s := er.r.RemoteSource("b1", "fern9", fwd.Video, k, false, 0, nil)
			if s == nil {
				t.Fatal("quelle " + k)
			}
			on[s] = true
		}
		er.r.SetRemote("fern9", -1, on)
		longT := 1250e3*probeHeadLong + estAudio
		pev0, fr0, onH, tj := len(er.pevs), len(er.fresh), 0, er.now
		var firstH time.Duration
		for end := er.now + int64(2*time.Minute); er.now < end; {
			step()
			if gegen {
				continue
			}
			if er.l.solo || er.l.fb != (freshBrake{}) || er.l.pf.pd > probeGapMax {
				t.Fatalf("Gruppe: Bremse laeuft weiter (solo %v): %+v, Abstand %v", er.l.solo, er.l.fb, er.l.pf.pd)
			}
			if er.layer() == "h" {
				if onH == 0 {
					firstH = time.Duration(er.now - tj)
				}
				onH++
			}
		}
		if gegen {
			if er.l.fb.at == 0 {
				t.Fatalf("Gegenprobe: Bremse in der Gruppe endet beim Beitritt - der Test erkennt das Ende nicht")
			}
			return
		}
		for _, e := range er.pevs[pev0:] {
			if e.Probe == probeStart && float64(e.ProbeKbit)*1000 >= 0.99*longT {
				t.Fatalf("Gruppe: lange Probe %+v", e)
			}
		}
		if n := len(er.fresh) - fr0; n != 0 || onH == 0 || firstH > probeGapMax {
			t.Fatalf("Gruppe: %d Zeilen frisch, Sprecher %d Takte auf h, zuerst nach %v (soll keine Zeile, h binnen %v)", n, onH, firstH, probeGapMax)
		}
		// Austritt: wieder 1:1, die Bremse beginnt frisch
		if !er.r.RemoveRemote("fern9") {
			t.Fatal("entfernen")
		}
		step()
		if !er.l.solo || er.l.fb.at != 0 || er.l.fb.d != 0 || er.l.fb.n != 0 {
			t.Fatalf("zurueck im 1:1 (solo %v): Bremse %+v - soll frisch beginnen", er.l.solo, er.l.fb)
		}
		for end := er.now + int64(3*time.Minute); er.now < end && er.l.fb.at == 0; {
			step()
		}
		if fb := er.l.fb; fb.at == 0 || fb.n != 1 || fb.d != 0 || !fb.long {
			t.Fatalf("zurueck im 1:1: Bremse %+v - soll nach dem naechsten frischen Rueckfall mit n = 1 und ohne Aufschub laufen", fb)
		}
		t.Logf("Gruppe: Sprecher %.1f s auf h, zuerst %v nach dem Beitritt; zurueck im 1:1: %+v", float64(onH)*0.2, firstH, er.l.fb)
	}
	run(t, false)
	t.Run("gegenprobe", func(t *testing.T) {
		bremseGruppe(t)
		run(t, true)
	})
}

// TestSoloCheck: 1:1 heisst hoechstens ein anderer Teilnehmer mit Bild (Kamera bzw. Bildschirm) - 1:1 mit
// Bildschirmteilen ist ein Sender, ein Dritter ohne Bild zaehlt nicht. In der Gruppe ist die Bremse leer.
func TestSoloCheck(t *testing.T) {
	src := map[string]*source{"h": {}}
	cam := &Peer{cam: src}
	both := &Peer{cam: src, scr: src}
	scr := &Peer{scr: src}
	stumm := &Peer{}
	for _, c := range []struct {
		name string
		ps   []*Peer
		solo bool
	}{
		{"allein", nil, true}, {"1:1", []*Peer{cam}, true}, {"1:1 mit Bildschirm", []*Peer{both}, true},
		{"Dritter ohne Bild", []*Peer{both, stumm}, true}, {"Gruppe", []*Peer{cam, cam}, false},
		{"Kamera und fremder Bildschirm", []*Peer{cam, scr}, false},
	} {
		l := &Leg{fb: freshBrake{rate: 1e6, n: 1, long: true, at: 1, upAt: 1}}
		l.soloCheck(c.ps)
		if l.solo != c.solo || !c.solo && l.fb != (freshBrake{}) || c.solo && l.fb.at == 0 {
			t.Errorf("%s: solo %v (soll %v), Bremse %+v", c.name, l.solo, c.solo, l.fb)
		}
	}
}
