package sfu

import (
	"fmt"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
)

// Nacharbeit zu HORN_BWE=eigen (Pruefer-Funde 4 und 8): zweite Rampen-Probe, Nachweis "bestaetigt mindestens der
// Bedarf".

// TestEigenRampe (Fund 4, Stufe 3 E5): Kam das Stausignal von GCC schon in der Rampe (halbe Fuellung), ist die Probe
// unklar und wird wiederholt - die zweite solche Probe in Folge zaehlt als gescheitert (bisher erst die dritte), und
// war die letzte gewertete Probe auf dieses Ziel gescheitert (binnen probeUnkGap), schon die erste. In der Enge geht
// damit nur der ersten gescheiterten Probe eine unklare voraus (und jeder, deren Vorgaengerin laenger als probeUnkGap
// zurueckliegt); ein Signal erst bei voller Fuellung zaehlt weiter bis probeUnkMax.
func TestEigenRampe(t *testing.T) {
	er := neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.pion, er.link = true, 1000e3
	var evs []ProbeEvent
	var at []int64
	er.r.mu.Lock()
	er.r.cfg.OnProbe = func(e ProbeEvent) { evs, at = append(evs, e), append(at, er.now) }
	er.r.mu.Unlock()
	er.until("m", 2*time.Second)
	z := er.zahlen(10*time.Minute, nil)
	ps := er.l.ProbeStats()
	t.Logf("eng 1000 kbit/s (pion-Regler): %v, proben %+v", z, ps)
	if ps.Passed != 0 || ps.Started == 0 || ps.Unclear == 0 || ps.Unclear >= ps.Failed {
		t.Fatalf("proben %+v (erwartet: weniger unklare als gescheiterte)", ps)
	}
	var failAt int64 // letzte gescheiterte Probe
	for i, e := range evs {
		ramp := e.GccSig != "" && e.SentKbit == 0
		if !ramp {
			t.Fatalf("Probe %d: Stausignal %q, gesendet %dk - nicht in der Rampe beendet", i, e.GccSig, e.SentKbit)
		}
		want := probeAbort
		if i > 0 && evs[i-1].Result == probeAbort || failAt != 0 && at[i]-failAt <= int64(probeUnkGap) {
			want = probeFail
		}
		if e.Result != want {
			t.Fatalf("Probe %d nach %.1f s: %s:%s, erwartet %s", i, float64(at[i]-at[0])/1e9, e.Result, e.Why, want)
		}
		if e.Result == probeFail {
			failAt = at[i]
		}
	}
	// Signal erst bei voller Fuellung: weiter drei in Folge
	er = neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.until("m", 2*time.Second)
	var got []string
	for i := 0; i < probeUnkMax; i++ {
		got = append(got, probeOver(t, er, 0, 1))
	}
	if fmt.Sprint(got) != fmt.Sprint([]string{"abort:unklar", "abort:unklar", "fail:over"}) {
		t.Fatalf("Signal bei voller Fuellung: %v", got)
	}
}

// TestEigenNachweisBedarf (Fund 8): Bestanden ist eine Probe nur, wenn die bestaetigte Rate auch den Bedarf der
// Zielschicht deckt (die Zielrate ohne die Reserve, 87 %) - neben "gesendet mindestens 87 % der Zielrate" und "davon
// mindestens 95 % bestaetigt". Sonst reichten 87 % x 95 % = 83 % der Zielrate, weniger als der Schritt braucht.
func TestEigenNachweisBedarf(t *testing.T) {
	for _, c := range []struct {
		sent, acked float64 // Anteil der Zielrate
		want        string
	}{
		{1.00, 1.00, probeOK},
		{0.90, 0.88, probeOK},                        // bestaetigt ueber dem Bedarf (87,0 % der Zielrate)
		{0.875, 0.871, probeOK},                      // knapp: beides gerade ueber dem Bedarf
		{0.88, 0.845, probeAbort + ":" + whyUnclear}, // 96 % des Gesendeten, aber unter dem Bedarf
		{0.95, 0.89, probeAbort + ":" + whyUnclear},  // ueber dem Bedarf, aber nur 93,7 % des Gesendeten
		{0.80, 0.80, probeAbort + ":" + whyUnclear},  // Zielrate nicht gesendet
	} {
		er := neuEigen(t, true, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.until("m", 2*time.Second)
		var target float64
		er.l.ExtSpan = func(_, _, _ time.Duration) (bwe.Verdict, bwe.Rates) {
			target = er.l.pr.target
			return bwe.Verdict{Result: bwe.VerdictFree}, bwe.Rates{SentBps: int64(c.sent * target), AckedBps: int64(c.acked * target), Ns: 1e9}
		}
		got := probeOver(t, er)
		if got != c.want {
			t.Fatalf("gesendet %.1f %%, bestaetigt %.1f %% der Zielrate %.0f (Bedarf %.1f %%): %q, erwartet %q", c.sent*100, c.acked*100, target, probeSentMin*100, got, c.want)
		}
	}
}

// rampOver faehrt das Modell, bis eine Probe endet, und meldet in der Rampe (halbe Fuellung) Ueberlast - ein Stausignal
// von GCC, bevor die volle Fuellung lief. Liefert den Ausgang (ProbeStats.Last).
func rampOver(t *testing.T, er *estRoom) string {
	t.Helper()
	over := false
	er.l.ExtOver = func() bool { return over }
	n0 := er.l.pstat.Started
	for i := 0; i < 300; i++ {
		over = er.l.pr.stage == 1
		was := er.l.pr.stage
		er.tick()
		if was != 0 && er.l.pr.stage == 0 && er.l.pstat.Started > n0 {
			return er.l.pstat.Last
		}
	}
	t.Fatalf("keine Probe in 60 s (est %d, gcc %.0f)", er.l.EstimateKbit(), er.gcc)
	return ""
}

// TestEigenRampeNachFehlschlag (Stufe 3, E5): Eine Rampen-Probe (Stausignal von GCC bei halber Fuellung) wird nur
// wiederholt, wenn die letzte gewertete Probe auf dieses Ziel nicht gescheitert war. Sonst zaehlt schon die erste als
// gescheitert. Dieses Ziel: ab probeNear der damaligen Zielrate; die Erinnerung gilt probeUnkGap.
func TestEigenRampeNachFehlschlag(t *testing.T) {
	for _, c := range []struct {
		name   string
		failed bool
		rate   float64 // Zielrate der letzten gewerteten Probe (bit/s; 0 = keine)
		ago    time.Duration
		want   string
	}{
		{"keine Vorgeschichte", false, 0, 0, probeAbort + ":" + whyUnclear},
		{"zuletzt bestanden", false, 1, time.Second, probeAbort + ":" + whyUnclear},
		{"zuletzt gescheitert", true, 1, time.Second, probeFail + ":over"},
		{"gescheitert, kleineres Ziel", true, 1, 50 * time.Second, probeFail + ":over"},
		{"gescheitert, viel hoeheres Ziel", true, 100e6, time.Second, probeAbort + ":" + whyUnclear},
		{"gescheitert, zu lange her", true, 1, probeUnkGap + time.Second, probeAbort + ":" + whyUnclear},
	} {
		er := neuEigen(t, true, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.until("m", 2*time.Second)
		for er.l.pr.stage == 0 { // bis die Probe beginnt; die Vorgeschichte gilt ab ihrem Ende
			er.tick()
			if er.l.pstat.Started > 1 {
				t.Fatal("mehr als eine Probe")
			}
		}
		if c.rate > 0 {
			er.l.lastJ.rate, er.l.lastJ.failed, er.l.lastJ.at = c.rate, c.failed, er.now-int64(c.ago)
		}
		over := true
		er.l.ExtOver = func() bool { return over }
		for er.l.pr.stage != 0 {
			over = er.l.pr.stage == 1
			er.tick()
		}
		if got := er.l.pstat.Last; got != c.want {
			t.Fatalf("%s: %q, erwartet %q", c.name, got, c.want)
		}
	}
	// zweite Rampen-Probe nach einer unklaren: gescheitert, wie bisher
	er := neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.until("m", 2*time.Second)
	if a, b := rampOver(t, er), rampOver(t, er); a != probeAbort+":"+whyUnclear || b != probeFail+":over" {
		t.Fatalf("zwei Rampen-Proben in Folge: %q, %q", a, b)
	}
	// ... und die naechste danach (letzte gewertete gescheitert): sofort gescheitert, ohne unklare davor
	if c := rampOver(t, er); c != probeFail+":over" {
		t.Fatalf("Rampen-Probe nach gescheiterter: %q", c)
	}
}
