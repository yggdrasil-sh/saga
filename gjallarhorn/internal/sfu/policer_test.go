package sfu

import (
	"testing"
	"time"
)

// Stufe 3b: Token-Bucket-Policer im Modell (nach dem Modell des Pruefers bwe3). Die kurze Probe von Stufe 3 schiebt
// nur (Bedarf x 1,05 - Policer-Rate) x 0,4 bis 0,6 s ueber die Grenze; den Eimer leert sie nicht, sie besteht, und die
// Schicht faellt Sekunden spaeter zurueck - immer wieder. Nach dem ersten frischen Rueckfall faehrt die naechste Probe
// auf dieses Ziel lang (freshBrake, probeWinLong, probeHeadLong), und die Bremse haengt am Bein, nicht am Platz.

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
	er := neuEigen(t, eigen, 1250e3, 810e3, n)
	er.l.firstUsed = true
	er.grow = 400e3
	er.tick()
	ps := er.senders()
	er.until("m", 2*time.Second)
	R := 0.93 * (1250e3 + 500e3*float64(n-1) + estAudio)
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
// (h + 2 x m), Eimer 0,15 und 0,6 s, 10 min. Vorher hing die Bremse am Platz (Aufschub s.dw, Zaehler der frischen
// Rueckfaelle) und begann mit jedem Sprecherwechsel von vorn; die kurze Probe bestand wieder: gcc 107 bzw. 90, eigen 87
// bzw. 82 Rueckfaelle. Jetzt am Bein: 13 bzw. 28 (eigen 13 bzw. 27), der Sprecherplatz 96 bis 97 % der Zeit auf h.
func TestProbingPolicerGruppe(t *testing.T) {
	for _, eigen := range []bool{false, true} {
		for _, bs := range []float64{0.15, 0.6} {
			r := policerRun(t, eigen, 3, 20*time.Second, bs, 10*time.Minute, 0)
			t.Logf("eigen=%v, Eimer %.2f s: %+v", eigen, bs, r)
			if r.falls > 40 || r.hShare < 0.9 {
				t.Fatalf("eigen=%v, Eimer %.2f s: %+v - soll hoechstens 40 Rueckfaelle, h mindestens 90 %%", eigen, bs, r)
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
			if er.l.fb != (freshBrake{upAt: er.l.fb.upAt, upT: er.l.fb.upT}) {
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

// TestFreshBrake: Buchfuehrung der Bremse.
func TestFreshBrake(t *testing.T) {
	var b freshBrake
	s := int64(time.Second)
	b.stepped(1500e3, 10*s)
	if b.down(10*s) || b.at != 0 || b.upAt == 0 {
		t.Fatalf("Rueckfall im selben Takt zaehlt: %+v", b)
	}
	if b.down(15*s) || !b.long || b.n != 1 || b.capNeed(40e3) == 0 {
		t.Fatalf("erster frischer Rueckfall: %+v", b)
	}
	if !b.longFor(1400e3) || b.longFor(1300e3) || !b.on(1400e3) {
		t.Fatalf("Zielraten: %+v", b)
	}
	b.passLong(1500e3)
	if b.long || b.capNeed(40e3) != 0 {
		t.Fatalf("lange Probe bestanden: %+v", b)
	}
	b.stepped(1500e3, 20*s)
	if !b.down(25*s) || b.d != 2*layerDwell || !b.blocks(1500e3, 30*s) || b.blocks(1500e3, 34*s) {
		t.Fatalf("zweiter frischer Rueckfall: Aufschub am Bein %+v", b)
	}
	b.stepped(1500e3, 40*s)
	if b.down(60*s) || b.n != 0 || !b.long {
		t.Fatalf("Rueckfall nach probeFresh setzt die Folge zurueck, die lange Probe bleibt: %+v", b)
	}
	b.failLong(70 * s)
	b.decay(70*s + int64(upHold) + int64(b.d) - 1)
	if b.at == 0 {
		t.Fatal("zu frueh erledigt")
	}
	b.decay(70*s + int64(upHold) + int64(b.d))
	if b.at != 0 || b.long {
		t.Fatalf("nicht erledigt: %+v", b)
	}
}

// TestProbingBremseHaeltLaufendes: Der Deckel der Bremse verhindert nur das Wachsen. Kam der frisch gefallene Schritt
// mit wenig Zuwachs (hier 8 % - eine Kachel aus der Pause), laege 90 % seiner Zielrate unter der laufenden Auswahl;
// die laufenden Schichten bleiben trotzdem (bis zur Nachbesserung fiel hier eine Kachel auf l).
func TestProbingBremseHaeltLaufendes(t *testing.T) {
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
