package sfu

import (
	"fmt"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
)

// Nacharbeit zu HORN_BWE=eigen (Pruefer-Funde 4 und 8): zweite Rampen-Probe, Nachweis "bestaetigt mindestens der
// Bedarf".

// TestEigenRampe (Fund 4): Kam das Stausignal von GCC schon in der Rampe (halbe Fuellung), ist die Probe unklar und
// wird wiederholt - die zweite solche Probe in Folge zaehlt als gescheitert (bisher erst die dritte). In der Enge folgt
// auf jede unklare Probe genau eine gescheiterte; ein Signal erst bei voller Fuellung zaehlt weiter bis probeUnkMax.
func TestEigenRampe(t *testing.T) {
	er := neuEigen(t, true, 1250e3, 810e3, 1)
	er.l.firstUsed = true
	er.pion, er.link = true, 1000e3
	var evs []ProbeEvent
	er.r.mu.Lock()
	er.r.cfg.OnProbe = func(e ProbeEvent) { evs = append(evs, e) }
	er.r.mu.Unlock()
	er.until("m", 2*time.Second)
	z := er.zahlen(10*time.Minute, nil)
	ps := er.l.ProbeStats()
	t.Logf("eng 1000 kbit/s (pion-Regler): %v, proben %+v", z, ps)
	if ps.Passed != 0 || ps.Started == 0 || ps.Unclear != ps.Failed || ps.Started > 16 {
		t.Fatalf("proben %+v (erwartet: je gescheiterter Probe genau eine unklare davor, hoechstens 16)", ps)
	}
	for i, e := range evs {
		ramp := e.GccSig != "" && e.SentKbit == 0
		if !ramp {
			t.Fatalf("Probe %d: Stausignal %q, gesendet %dk - nicht in der Rampe beendet", i, e.GccSig, e.SentKbit)
		}
		want := probeAbort
		if i%2 == 1 {
			want = probeFail
		}
		if e.Result != want {
			t.Fatalf("Probe %d: %s:%s, erwartet %s (Folge unklar, gescheitert)", i, e.Result, e.Why, want)
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
// Zielschicht deckt - neben "gesendet mindestens 87 % der Zielrate" und "davon mindestens 95 % bestaetigt". Sonst
// reichten 87 % x 95 % = 83 % der Zielrate, weniger als der Schritt braucht (87 %).
func TestEigenNachweisBedarf(t *testing.T) {
	for _, c := range []struct {
		sent, acked float64 // Anteil der Zielrate
		want        string
	}{
		{1.00, 1.00, probeOK},
		{0.90, 0.88, probeOK},                        // bestaetigt ueber dem Bedarf (87,3 % der Zielrate)
		{0.88, 0.845, probeAbort + ":" + whyUnclear}, // 96 % des Gesendeten, aber unter dem Bedarf
		{0.95, 0.89, probeAbort + ":" + whyUnclear},  // ueber dem Bedarf, aber nur 93,7 % des Gesendeten
		{0.80, 0.80, probeAbort + ":" + whyUnclear},  // Zielrate nicht gesendet
	} {
		er := neuEigen(t, true, 1250e3, 810e3, 1)
		er.l.firstUsed = true
		er.until("m", 2*time.Second)
		var target, need float64
		er.l.ExtSpan = func(_, _, _ time.Duration) (bwe.Verdict, bwe.Rates) {
			target, need = er.l.pr.target, er.l.pr.need
			return bwe.Verdict{Result: bwe.VerdictFree}, bwe.Rates{SentBps: int64(c.sent * target), AckedBps: int64(c.acked * target), Ns: 1e9}
		}
		got := probeOver(t, er)
		if got != c.want {
			t.Fatalf("gesendet %.0f %%, bestaetigt %.1f %% der Zielrate (Bedarf %.1f %%): %q, erwartet %q", c.sent*100, c.acked*100, need/target*100, got, c.want)
		}
		if n := need / target; n < 0.87 || n > 0.88 {
			t.Fatalf("Bedarf %.0f von Zielrate %.0f", need, target)
		}
	}
}
