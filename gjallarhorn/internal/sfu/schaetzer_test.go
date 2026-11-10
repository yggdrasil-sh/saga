package sfu

import (
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Labortest „Fund 2“: Im 1:1 ueber UDP blieb der Empfaenger auf m, obwohl der Sender h schickte und die Leitung frei
// war - der Deckel der Schaetzung liess h (Browser, ~1250 kbit/s) nie zu, und nach einem Hochschalten galt das
// nachlaufende GCC-Ziel als Stau. Die Tests hier fahren den Takt mit kuenstlicher Zeit und einem kleinen Modell des
// pion-GCC-Ziels (Regeln aus pkg/gcc: bei Stau 85 % der empfangenen Rate, sonst Anstieg, gedeckelt bei 1,5 x Rate).

// estRoom: Empfaenger mit Ton und Sprecherplatz auf einem UDP-Bein, Sender ueber eine Bruecke mit l, m und h.
type estRoom struct {
	t     *testing.T
	r     *Room
	l     *Leg
	src   map[string]*fwd.Source
	rates map[string]float64 // Rate je Schicht (bit/s)
	now   int64
	sent  string  // Schicht, die gerade wirklich gesendet wird (einen Takt nach dem Wechsel: Schluesselbild)
	link  float64 // Leitung (bit/s), 0 = frei
	gcc   float64 // GCC-Ziel
	capF  float64 // Ziel hoechstens capF x gesendete Rate (pion nahe einer frueheren Staurate: 1,5); 0 = ohne
	floor float64 // ... aber nicht unter den Anfangswert (der Anlauf des Anrufs ist nicht Gegenstand)
	grow  float64 // Anstieg des Ziels ohne Stau (bit/s je Sekunde)
	loss  bool    // Stau-Schub: Ziel faellt auf 600 kbit/s (traegt m, nicht h)
	over  int     // Takte ueber der Leitung
	evs   []Slot
}

const estAudio = 40e3

func newEstRoom(t *testing.T, h, gcc float64) *estRoom {
	er := &estRoom{t: t, now: fwd.Now(), src: map[string]*fwd.Source{}, gcc: gcc, floor: gcc, capF: 1.5, grow: 50e3,
		rates: map[string]float64{"l": 150e3, "m": 500e3, "h": h}}
	n := NewNode(Config{Name: "k", Tick: time.Hour, OnLayout: func(ev LayoutEvent) {
		for _, s := range ev.Slots {
			if s.Class == "speaker" {
				er.evs = append(er.evs, s)
			}
		}
	}})
	t.Cleanup(n.Close)
	r, err := n.CreateRoom("raumschaetzer", nil)
	if err != nil {
		t.Fatal(err)
	}
	er.r = r
	if er.l, err = r.AddLeg(Grant{Room: "raumschaetzer", Peer: "empf", Leg: "empf-b", Role: RoleBoth}, PathUDP, Want{Audio: 1, Speaker: true}); err != nil {
		t.Fatal(err)
	}
	er.l.Ext = func() int { return int(er.gcc) }
	if !r.AddRemote("b1", "fern", Desktop, nil) {
		t.Fatal("aufnehmen")
	}
	on := map[*fwd.Source]bool{}
	for k := range er.rates {
		s := r.RemoteSource("b1", "fern", fwd.Video, k, false, 0, nil)
		if s == nil {
			t.Fatal("quelle " + k)
		}
		er.src[k] = s
		on[s] = true
	}
	r.SetRemote("fern", -1, on)
	return er
}

func (er *estRoom) layer() string {
	for _, s := range er.l.Layout() {
		if s.Class == "speaker" {
			return s.Layer
		}
	}
	return ""
}

// tick: ein Takt von 200 ms - Quellen und Bein senden, das GCC-Modell folgt, dann plant der Raum.
func (er *estRoom) tick() {
	const dt = 0.2
	er.now += int64(200 * time.Millisecond)
	for k, s := range er.src {
		s.Bytes.Add(uint64(er.rates[k] * dt / 8))
	}
	rate := estAudio + er.rates[er.sent]
	er.l.fl.Stats.BytesOut.Add(uint64(rate * dt / 8))
	switch {
	case er.loss:
		er.gcc = 600e3
	case er.link > 0 && rate > er.link:
		if er.over++; er.over >= 2 { // Verzoegerung faellt nach ~0,3 s auf: 85 % dessen, was durchkommt
			er.gcc = 0.85 * er.link
		}
	default:
		er.over = 0
		er.gcc += er.grow * dt
		if c := max(er.capF*rate, er.floor); er.capF > 0 && er.gcc > c {
			er.gcc = c
		}
	}
	er.r.tickAt(er.now)
	er.sent = er.layer()
}

func (er *estRoom) run(d time.Duration) {
	for end := er.now + int64(d); er.now < end; {
		er.tick()
	}
}

func (er *estRoom) until(want string, max time.Duration) time.Duration {
	start := er.now
	for er.now-start < int64(max) {
		er.tick()
		if er.layer() == want {
			return time.Duration(er.now - start)
		}
	}
	er.t.Fatalf("schicht %q nicht nach %v (ist %q, est %d kbit/s, gcc %.0f)", want, max, er.layer(), er.l.EstimateKbit(), er.gcc)
	return 0
}

// TestSchaetzerFreieLeitung (a): freie Leitung, m ~500 kbit/s; das GCC-Ziel steht bei 1,5 x der bisherigen Rate bzw.
// darunter und zieht nach dem Hochschalten erst ueber Sekunden nach. Der Empfaenger erreicht h und bleibt dort.
// Browser-h (1250) scheiterte frueher am Deckel, Android-h (880) am nachlaufenden Ziel.
func TestSchaetzerFreieLeitung(t *testing.T) {
	for _, c := range []struct {
		name   string
		h, gcc float64
	}{{"browser", 1250e3, 810e3}, {"android", 880e3, 700e3}} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstRoom(t, c.h, c.gcc)
			d := er.until("h", 10*time.Second)
			n0 := len(er.evs)
			lagged := false
			for i := 0; i < 300; i++ { // 60 s
				er.tick()
				lagged = lagged || er.l.est.lag
				if er.layer() != "h" {
					t.Fatalf("nach %v wieder auf %q (est %d kbit/s, gcc %.0f, grund %+v)", time.Duration(i)*200*time.Millisecond,
						er.layer(), er.l.EstimateKbit(), er.gcc, er.evs[len(er.evs)-1])
				}
			}
			if len(er.evs) != n0 {
				t.Fatalf("%d Wechsel nach dem Hochschalten: %+v", len(er.evs)-n0, er.evs[n0:])
			}
			if !lagged {
				t.Fatal("das Modell-Ziel lief nie nach - der Test prueft die Schonfrist nicht")
			}
			if er.l.est.lag || er.l.est.lagAt != 0 {
				t.Fatalf("schonfrist laeuft nach 60 s noch (gcc %.0f)", er.gcc)
			}
			if est := float64(er.l.EstimateKbit()) * 1000; est < c.h+estAudio {
				t.Fatalf("schaetzung %v unter dem Bedarf", est)
			}
			t.Logf("h nach %v, est %d kbit/s, gcc %.0f", d, er.l.EstimateKbit(), er.gcc)
		})
	}
}

// TestSchaetzerEngeLeitung (b): Die Leitung traegt m, aber nicht h. Der Versuch nach oben endet zuegig (Ziel faellt:
// sofort; Ziel springt auf 85 % der Leitung und steht: nach hoechstens zwei Fortschrittspruefungen), zaehlt als
// gescheitert, und der Aufschub waechst (8/16/32 s) - kein Pendeln.
func TestSchaetzerEngeLeitung(t *testing.T) {
	for _, c := range []struct {
		name string
		link float64
		hold time.Duration // laengste Zeit auf h je Versuch
	}{{"ziel-faellt-800", 800e3, time.Second}, {"ziel-steht-1000", 1000e3, 3 * time.Second}} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstRoom(t, 1250e3, 810e3)
			er.link = c.link
			er.until("m", 2*time.Second)
			var ups, downs []int64
			onH := time.Duration(0)
			start := er.now
			for er.now-start < int64(3*time.Minute) {
				was := er.layer()
				er.tick()
				switch is := er.layer(); {
				case was != "h" && is == "h":
					ups = append(ups, er.now)
				case was == "h" && is != "h":
					downs = append(downs, er.now)
					if is != "m" || er.evs[len(er.evs)-1].Why != whyBudget {
						t.Fatalf("zurueck auf %q, grund %+v", is, er.evs[len(er.evs)-1])
					}
				}
				if er.layer() == "h" {
					onH += 200 * time.Millisecond
				}
			}
			if len(ups) < 3 {
				t.Fatalf("nur %d Versuche nach oben in 3 min (der Wechsel ist die Probe)", len(ups))
			}
			if len(ups) > 8 {
				t.Fatalf("pendelt: %d Versuche in 3 min", len(ups))
			}
			for i := range downs {
				if d := time.Duration(downs[i] - ups[i]); d > c.hold {
					t.Fatalf("versuch %d: %v auf h (hoechstens %v)", i, d, c.hold)
				}
			}
			want := 2 * layerDwell
			for i := 1; i < len(ups) && i < len(downs)+1; i++ {
				gap := time.Duration(ups[i] - downs[i-1])
				if gap < want-200*time.Millisecond {
					t.Fatalf("versuch %d schon %v nach dem Rueckfall (aufschub %v)", i, gap, want)
				}
				want = min(2*want, upDwellMax)
			}
			if onH > time.Duration(len(ups))*c.hold {
				t.Fatalf("%v von 3 min auf h (%d Versuche)", onH, len(ups))
			}
			if d := er.l.slots[1].dwell; d != upDwellMax {
				t.Fatalf("aufschub %v, erwartet %v", d, upDwellMax)
			}
			t.Logf("%d Versuche, zusammen %v auf h", len(ups), onH)
		})
	}
}

// TestSchaetzerStauSchub (c): Stau waehrend h (das GCC-Ziel faellt unter die Rate) schaltet wie bisher
// sofort herunter - ein fallendes Ziel wird nie entschuldigt; danach geht es nach Sperrzeit und Verweildauer wieder
// hoch (Android-h, Ziel vorher weit ueber der Rate). Neu ist nur das Ziel des Rueckfalls: m, wenn die Schaetzung m
// traegt - frueher fiel der Platz in der Sperrzeit immer bis auf l.
func TestSchaetzerStauSchub(t *testing.T) {
	er := newEstRoom(t, 880e3, 3e6)
	er.capF = 0
	er.until("h", 5*time.Second)
	er.run(20 * time.Second)
	if er.layer() != "h" {
		t.Fatalf("vor dem Schub: %q", er.layer())
	}
	er.loss = true
	d := er.until("m", 2*time.Second)
	if er.l.est.lag || er.l.est.canUp(er.now) {
		t.Fatal("fallendes Ziel entschuldigt bzw. keine Sperrzeit")
	}
	if w := er.evs[len(er.evs)-1].Why; w != whyBudget {
		t.Fatalf("grund %q", w)
	}
	er.run(time.Second)
	er.loss = false
	if er.layer() != "m" {
		t.Fatalf("im Schub auf %q", er.layer())
	}
	back := er.until("h", 40*time.Second)
	if back < layerDwell-time.Second {
		t.Fatalf("schon nach %v wieder hoch", back)
	}
	er.run(20 * time.Second)
	if er.layer() != "h" {
		t.Fatalf("nach dem Schub nicht stabil: %q", er.layer())
	}
	t.Logf("herunter nach %v, wieder hoch nach %v", d, back)
}

// TestEstimatorNachlauf: die Schonfrist im Einzelnen - nachlaufendes, steigendes Ziel ist kein Stau; fallendes,
// stehendes oder zu lange nachlaufendes Ziel ist Stau; waehrend einer Sperrzeit gilt die alte Regel; TCP (ohne Ext)
// behaelt den alten Deckel.
func TestEstimatorNachlauf(t *testing.T) {
	type env struct {
		e   estimator
		l   *Leg
		now int64
		gcc float64
	}
	mk := func(gcc float64) *env {
		v := &env{l: &Leg{fl: fwd.NewLeg()}, now: int64(100 * time.Second), gcc: gcc}
		t.Cleanup(v.l.fl.Close)
		v.e.init(1.2e6, 25e6)
		v.l.Ext = func() int { return int(v.gcc) }
		return v
	}
	step := func(v *env, rate float64) bool { // -> Stau gemeldet
		v.l.fl.Stats.BytesOut.Add(uint64(rate * 0.2 / 8))
		v.now += int64(200 * time.Millisecond)
		v.e.update(v.l, 0.2, v.now)
		return !v.e.canUp(v.now) && v.e.lastDown == v.now
	}
	// steigt mit 6 %/s: 5 s entschuldigt, danach (Ziel ueber 90 % der Rate) ist die Folge beendet
	v := mk(800e3)
	step(v, 540e3)
	for i := 0; i < 25; i++ {
		v.gcc *= 1.012
		if step(v, 1290e3) || !v.e.lag {
			t.Fatalf("takt %d: steigendes Ziel %.0f als Stau", i, v.gcc)
		}
	}
	if v.e.bps < 1290e3 {
		t.Fatalf("schaetzung %v unter der gesendeten Rate", v.e.bps)
	}
	v.gcc = 1.4e6
	for i := 0; i < 11; i++ {
		step(v, 1290e3)
	}
	if v.e.lag || v.e.lagAt != 0 {
		t.Fatal("folge nicht beendet")
	}
	// faellt in der Schonfrist: sofort Stau, Schaetzung auf dem Ziel
	v = mk(800e3)
	step(v, 540e3)
	step(v, 1290e3)
	v.gcc = 680e3
	if !step(v, 1290e3) || v.e.bps > 680e3+1 {
		t.Fatalf("fallendes Ziel entschuldigt (est %v)", v.e.bps)
	}
	if !v.e.probeFailed(v.now-int64(time.Second), v.now) || v.e.probeFailed(v.now-int64(10*time.Second), v.now) {
		t.Fatal("gescheiterte Schonfrist nicht dem Hochschalten kurz davor zugeordnet")
	}
	// faellt in dem Takt, in dem die Rate es ueberholt: Stau ohne Schonfrist
	v = mk(800e3)
	step(v, 540e3)
	v.gcc = 700e3
	if !step(v, 1290e3) {
		t.Fatal("fallendes Ziel beim Ratensprung entschuldigt")
	}
	// steht: Stau spaetestens nach zwei Pruefabstaenden
	v = mk(800e3)
	step(v, 540e3)
	n := 0
	for ; n < 100 && !step(v, 1290e3); n++ {
	}
	if d := time.Duration(n) * 200 * time.Millisecond; d < lagStep-200*time.Millisecond || d > 2*lagStep {
		t.Fatalf("stehendes Ziel nach %v als Stau", d)
	}
	// steigt, holt aber nicht auf: Frist lagMax
	v = mk(400e3)
	step(v, 300e3)
	for n = 0; n < 200; n++ {
		v.gcc *= 1.004
		if step(v, 3e6) {
			break
		}
	}
	if d := time.Duration(n) * 200 * time.Millisecond; d < lagMax-time.Second || d > lagMax+time.Second {
		t.Fatalf("frist nach %v (erwartet %v)", d, lagMax)
	}
	// in der Sperrzeit nach einem Stau: alte Regel (jedes Ziel unter der Rate ist Stau)
	v.gcc = 3.5e6
	for i := 0; i < 15; i++ { // Folge beenden
		step(v, 3e6)
	}
	v.gcc = 2e6
	if !step(v, 3e6) {
		t.Fatal("fall nicht als Stau")
	}
	v.gcc = 2.05e6
	if !step(v, 3e6) || v.e.lag {
		t.Fatal("in der Sperrzeit entschuldigt")
	}
	// Deckel: auf UDP bis zum Bedarf des naechsten Schritts, auf TCP unveraendert
	v = mk(5e6)
	v.e.want = 1.55e6
	for i := 0; i < 20; i++ {
		step(v, 540e3)
	}
	if v.e.bps < 1.55e6-1 || v.e.bps > 1.55e6+1 {
		t.Fatalf("udp-deckel %v", v.e.bps)
	}
	v.l.Ext = nil
	step(v, 540e3)
	if v.e.bps > 1.2e6+1 {
		t.Fatalf("tcp-deckel %v", v.e.bps)
	}
}
