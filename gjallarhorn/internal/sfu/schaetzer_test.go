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
	sent  float64       // Bildrate, die gerade wirklich gesendet wird (einen Takt nach dem Wechsel: Schluesselbild)
	all   []*fwd.Source // Quellen aller Sender
	allR  []float64     // ... und ihre Raten
	link  float64       // Leitung (bit/s), 0 = frei
	gcc   float64       // GCC-Ziel
	capF  float64       // Ziel hoechstens capF x gesendete Rate (pion nahe einer frueheren Staurate: 1,5); 0 = ohne
	floor float64       // ... aber nicht unter den Anfangswert (der Anlauf des Anrufs ist nicht Gegenstand)
	grow  float64       // Anstieg des Ziels ohne Stau (bit/s je Sekunde)
	loss  bool          // Stau-Schub: Ziel faellt auf 600 kbit/s (traegt m, nicht h)
	over  int           // Takte ueber der Leitung
	free  float64       // letzte Rate, die die Leitung trug
	evs   []Slot
	on    map[*fwd.Source]bool // Quellen des ersten Senders
	pevs  []Slot               // Probe-Ereignisse (Probing)
	fill  float64              // Fuellrate dieses Takts (bit/s)
	padMB float64              // gesendete Fuelldaten
	noPad bool                 // Fuelldaten werden eingestellt, aber nicht gesendet
}

const estAudio = 40e3

func newEstRoom(t *testing.T, h, gcc float64) *estRoom { return newEstGroup(t, h, gcc, 1) }

// newEstGroup: wie newEstRoom, aber mit n Sendern (fern, fern2, ...) und n-1 Kacheln neben dem Sprecherplatz.
func newEstGroup(t *testing.T, h, gcc float64, n int) *estRoom { return newEstCfg(t, h, gcc, n, false) }

// newEstCfg: ... mit bzw. ohne Probing (Config.Probing). Mit Probing meldet das Modell Ueberlast wie pion-GCC
// (Leg.ExtOver), sobald die Verzoegerung auffaellt.
func newEstCfg(t *testing.T, h, gcc float64, n int, probing bool) *estRoom {
	er := &estRoom{t: t, now: fwd.Now(), src: map[string]*fwd.Source{}, gcc: gcc, floor: gcc, capF: 1.5, grow: 50e3,
		rates: map[string]float64{"l": 150e3, "m": 500e3, "h": h}}
	nd := NewNode(Config{Name: "k", Tick: time.Hour, Probing: probing, OnLayout: func(ev LayoutEvent) {
		for _, s := range ev.Slots {
			if s.Probe != "" { // Probe-Ereignisse getrennt: evs bleibt die Folge der Wechsel des Sprecherplatzes
				er.pevs = append(er.pevs, s)
			} else if s.Class == "speaker" {
				er.evs = append(er.evs, s)
			}
		}
	}})
	t.Cleanup(nd.Close)
	r, err := nd.CreateRoom("raumschaetzer", nil)
	if err != nil {
		t.Fatal(err)
	}
	er.r = r
	if er.l, err = r.AddLeg(Grant{Room: "raumschaetzer", Peer: "empf", Leg: "empf-b", Role: RoleBoth}, PathUDP, Want{Audio: 1, Speaker: true, Tiles: n - 1}); err != nil {
		t.Fatal(err)
	}
	er.l.Ext = func() int { return int(er.gcc) }
	if probing {
		er.l.ExtOver = func() bool { return er.over >= 2 }
	}
	for i := 0; i < n; i++ {
		id := "fern"
		if i > 0 {
			id += string(rune('1' + i))
		}
		if !r.AddRemote("b1", id, Desktop, nil) {
			t.Fatal("aufnehmen")
		}
		on := map[*fwd.Source]bool{}
		for _, k := range []string{"l", "m", "h"} {
			s := r.RemoteSource("b1", id, fwd.Video, k, false, 0, nil)
			if s == nil {
				t.Fatal("quelle " + k)
			}
			if i == 0 {
				er.src[k] = s
				er.on = on
			}
			er.all, er.allR = append(er.all, s), append(er.allR, er.rates[k])
			on[s] = true
		}
		r.SetRemote(id, -1, on)
	}
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
	for i, s := range er.all {
		s.Bytes.Add(uint64(er.allR[i] * dt / 8))
	}
	rate := estAudio + er.sent
	// Fuelldaten einer Probe (Probing): Das Modell sendet sie mit der eingestellten Rate, die Leitung sieht sie
	er.fill = float64(er.l.fl.Padding())
	if er.noPad { // die Fuellpakete fliessen nicht (stille Quelle, Ausgang wartet auf ein Schluesselbild)
		er.fill = 0
	}
	if er.fill > 0 {
		er.l.fl.Stats.PadBytes.Add(uint64(er.fill * dt / 8))
		er.padMB += er.fill * dt / 8e6
		rate += er.fill
	}
	er.l.fl.Stats.BytesOut.Add(uint64(rate * dt / 8))
	switch {
	case er.loss:
		er.gcc = 600e3
	case er.link > 0 && rate > er.link:
		// Verzoegerung faellt nach ~0,3 s auf: 85 % dessen, was durchkommt. Bei deutlicher Ueberlast enthaelt das
		// Messfenster dann noch ueberwiegend die alte Rate (gemessen: erster Wert ~1,03 x alte Rate); danach holt
		// der Verlust-Teil mit 5 % je 200 ms auf.
		tgt := 0.85 * er.link
		switch er.over++; {
		case er.over == 2 && rate > 1.1*er.link:
			er.gcc = min(tgt, 1.03*er.free)
		case er.over == 2:
			er.gcc = tgt
		case er.over > 2:
			er.gcc = min(tgt, er.gcc*1.05)
		}
	default:
		er.over, er.free = 0, rate
		er.gcc += er.grow * dt
		if c := max(er.capF*rate, er.floor); er.capF > 0 && er.gcc > c {
			er.gcc = c
		}
	}
	er.r.tickAt(er.now)
	er.sent = 0
	for _, s := range er.l.Layout() {
		if s.Class != "audio" && !s.Paused {
			er.sent += er.rates[s.Layer]
		}
	}
}

// video: Schichten der Bild-Plaetze in Platzfolge, pausiert bzw. leer als "p" (z. B. "hmm", "lmp").
func (er *estRoom) video() string {
	out := ""
	for _, s := range er.l.Layout() {
		switch {
		case s.Class == "audio":
		case s.Paused || s.Layer == "":
			out += "p"
		default:
			out += s.Layer
		}
	}
	return out
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
// sofort; milde Ueberlast, Ziel springt auf 85 % der Leitung und steht: nach hoechstens zwei Fortschrittspruefungen),
// zaehlt als gescheitert, und der Aufschub waechst (8, 16, 32 s ... bis upDwellMax) - kein Pendeln: Im Labor kam mit
// der alten Obergrenze von 32 s auf einer solchen Leitung ohne Ende alle 32 s ein Versuch (20 in 10 min, jeder mit
// Schluesselbildern und kurz verdecktem Ton). Der Rueckfall geht auf m, nicht auf l, obwohl der erste Ruecksprung des
// Ziels kaum ueber der m-Rate liegt.
func TestSchaetzerEngeLeitung(t *testing.T) {
	for _, c := range []struct {
		name string
		link float64
		hold time.Duration // laengste Zeit auf h je Versuch
	}{{"ziel-faellt-800", 800e3, time.Second}, {"ziel-faellt-1000", 1000e3, time.Second}, {"ziel-steht-1200", 1200e3, 3 * time.Second}} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstRoom(t, 1250e3, 810e3)
			er.link = c.link
			er.until("m", 2*time.Second)
			var ups, downs []int64
			onH := time.Duration(0)
			start := er.now
			at10 := 0 // Versuche in den ersten 10 min
			for er.now-start < int64(20*time.Minute) {
				if er.now-start < int64(10*time.Minute) {
					at10 = len(ups)
				}
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
			if at10 < 5 {
				t.Fatalf("nur %d Versuche nach oben in 10 min (der Wechsel ist die Probe)", at10)
			}
			if at10 > 8 || len(ups) > 11 {
				t.Fatalf("pendelt: %d Versuche in 10 min, %d in 20 min", at10, len(ups))
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
				t.Fatalf("%v von 20 min auf h (%d Versuche)", onH, len(ups))
			}
			if d := er.l.slots[1].dwellOf("h"); d != upDwellMax {
				t.Fatalf("aufschub %v, erwartet %v", d, upDwellMax)
			}
			if last := time.Duration(ups[len(ups)-1] - downs[len(ups)-2]); last < upDwellMax-time.Second {
				t.Fatalf("letzter Abstand %v, erwartet %v", last, upDwellMax)
			}
			t.Logf("%d Versuche in 10 min, %d in 20 min, zusammen %v auf h", at10, len(ups), onH)
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
	// faellt in dem Takt, in dem die Rate es ueberholt: Stau ohne Schonfrist. Die Schaetzung faellt dabei lagFloor
	// lang nicht unter die Rate vor dem Versuch (der erste Ruecksprung liegt oft knapp darunter), danach schon.
	v = mk(800e3)
	for i := 0; i < 10; i++ {
		step(v, 540e3)
	}
	if !v.e.ready {
		t.Fatal("ziel 800 bei rate 540 nicht bereit fuer einen Schritt nach oben")
	}
	v.gcc = 450e3
	if !step(v, 1290e3) {
		t.Fatal("fallendes Ziel beim Ratensprung entschuldigt")
	}
	if v.e.bps < 539e3 || v.e.bps > 541e3 {
		t.Fatalf("schaetzung nach gescheitertem Versuch %v, erwartet den Stand davor (540 kbit/s)", v.e.bps)
	}
	for i := 0; i < 12; i++ {
		step(v, 540e3)
	}
	if v.e.bps > 450e3+1 || v.e.ready {
		t.Fatalf("ziel bleibt unter der alten Rate: schaetzung %v, bereit %v", v.e.bps, v.e.ready)
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

// TestEstimatorEinbruchOhneVersuch: Bricht die Leitung bei gleichbleibender Rate ein (kein Schritt nach oben davor),
// folgt die Schaetzung dem Ziel sofort - die Untergrenze "Stand vor dem Versuch" gilt nur nach einem Ratensprung.
func TestEstimatorEinbruchOhneVersuch(t *testing.T) {
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
	step := func(v *env, rate float64) {
		v.l.fl.Stats.BytesOut.Add(uint64(rate * 0.2 / 8))
		v.now += int64(200 * time.Millisecond)
		v.e.update(v.l, 0.2, v.now)
	}
	// 10 s stabil auf 1290 kbit/s, dann faellt das Ziel auf 300 kbit/s
	v := mk(3e6)
	for i := 0; i < 50; i++ {
		step(v, 1290e3)
	}
	v.e.bps = 2e6
	v.gcc = 300e3
	step(v, 1290e3)
	if v.e.lag || v.e.lagJump || v.e.bps > 320e3 {
		t.Fatalf("einbruch ohne versuch: lag=%v sprung=%v schaetzung=%.0f (soll ~300k)", v.e.lag, v.e.lagJump, v.e.bps)
	}
	// Gegenstueck: gleicher Einbruch im Takt eines Ratensprungs (540 -> 1290 kbit/s) - Stand davor haelt lagFloor lang
	w := mk(3e6)
	for i := 0; i < 50; i++ {
		step(w, 540e3)
	}
	w.e.bps = 2e6
	w.gcc = 300e3
	step(w, 1290e3)
	if !w.e.lagJump || w.e.bps < 500e3 || w.e.bps > 560e3 {
		t.Fatalf("einbruch im versuch: sprung=%v schaetzung=%.0f (soll ~540k)", w.e.lagJump, w.e.bps)
	}
	for i := 0; i < 11; i++ { // nach lagFloor folgt die Schaetzung dem Ziel
		step(w, 540e3)
	}
	if w.e.bps > 320e3 {
		t.Fatalf("nach der Untergrenze: schaetzung=%.0f (soll ~300k)", w.e.bps)
	}
}

// TestSchaetzerAufschubZurueck: die Ruecksetz-Regel. Nach Fehlversuchen auf enger Leitung (Aufschub bei upDwellMax)
// wird die Leitung frei, h haelt. Ein Stau-Schub nach kurzem Halten (unter upHold) ist kein Fehlversuch: Der Aufschub
// faellt auf upDwellSoft, h ist nach einer halben Minute wieder da (Prueferfund: blieb er stehen, dauerte das 5 min).
// Haelt h upHold, ist der Aufschub weg - auch ohne Ruecknahme -, und nach dem naechsten Schub ist h nach wenigen
// Sekunden zurueck. Eine gute Leitung bleibt nach einem einzelnen Rueckfall nicht minutenlang auf m.
func TestSchaetzerAufschubZurueck(t *testing.T) {
	er := newEstRoom(t, 1250e3, 810e3)
	sl := er.l.slots[1]
	er.link = 1000e3
	er.until("m", 2*time.Second)
	for i := 0; sl.dwellOf("h") < upDwellMax; i++ {
		if i > 5*60*15 {
			t.Fatalf("aufschub nach 15 min erst %v", sl.dwellOf("h"))
		}
		er.tick()
	}
	grown := sl.dwellOf("h")
	er.link = 0
	burst := func() {
		er.loss = true
		er.until("m", 2*time.Second)
		er.run(time.Second)
		er.loss = false
	}
	er.until("h", grown+20*time.Second)
	er.run(10 * time.Second)
	if er.layer() != "h" {
		t.Fatalf("freie Leitung: %q", er.layer())
	}
	burst()
	if sl.dwellOf("h") != upDwellSoft {
		t.Fatalf("nach 10 s auf h: aufschub %v, erwartet %v", sl.dwellOf("h"), upDwellSoft)
	}
	soft := er.until("h", upDwellSoft+20*time.Second)
	if soft < upDwellSoft-layerDwell {
		t.Fatalf("schon nach %v wieder hoch (aufschub %v)", soft, upDwellSoft)
	}
	er.run(upHold + time.Second)
	if er.layer() != "h" || sl.dwellOf("h") != 0 {
		t.Fatalf("nach %v auf h: %q, aufschub %v, erwartet 0", upHold, er.layer(), sl.dwellOf("h"))
	}
	burst()
	if sl.dwellOf("h") != 0 {
		t.Fatalf("nach dem Schub: aufschub %v, erwartet 0", sl.dwellOf("h"))
	}
	d := er.until("h", 15*time.Second)
	er.run(20 * time.Second)
	if er.layer() != "h" {
		t.Fatalf("nach dem Zuruecksetzen nicht stabil: %q", er.layer())
	}
	t.Logf("aufschub %v -> %v nach kurzem Halten (h wieder nach %v) -> 0 nach %v Halten (h wieder nach %v)", grown, upDwellSoft, soft, upHold, d)
}

// vrank: Rang eines Platzes in video() - pausiert 0, l 1, m 2, h 3.
func vrank(c byte) int {
	if c == 'p' {
		return 0
	}
	return layerRank(string(c))
}

// TestSchaetzerGruppeEinSchritt: Empfaenger mit Sprecherplatz und zwei Kacheln, drei Sender (UDP). Je Takt geht
// hoechstens ein Platz eine Schicht hoeher - frueher konnten zwei Kacheln im selben Takt von l auf m gehen, die Rate
// sprang um beide, und ein Stau danach war keinem Schritt zuzuordnen. Kacheln aus der Pause (auf l) zaehlen nicht:
// Zu Beginn sind sofort alle zu sehen. Auf freier Leitung erreichen alle Plaetze ihr Ziel (Sprecher h, Kacheln m);
// auf einer Leitung, die das nicht traegt, bleibt die Zahl der Versuche klein, und die Summe liegt kaum je ueber der
// Leitung.
func TestSchaetzerGruppeEinSchritt(t *testing.T) {
	for _, c := range []struct {
		name  string
		link  float64
		tries int // hoechstens so viele Schritte nach oben in 10 min
	}{{"frei", 0, 12}, {"eng-1700", 1700e3, 25}} {
		t.Run(c.name, func(t *testing.T) {
			er := newEstGroup(t, 1250e3, 810e3, 3)
			er.link = c.link
			er.tick() // erste Belegung: alle sofort zu sehen
			if v := er.video(); v[1] == 'p' || v[2] == 'p' {
				t.Fatalf("erste Belegung %q: Kachel pausiert", v)
			}
			ups, over, seen := 0, 0, map[string]time.Duration{}
			start := er.now
			for er.now-start < int64(10*time.Minute) {
				was := er.video()
				er.tick()
				is := er.video()
				n := 0
				for i := range is {
					if vrank(is[i]) > vrank(was[i]) && vrank(is[i]) > 1 {
						n++
					}
				}
				if n > 1 {
					t.Fatalf("%v: %d Plaetze im selben Takt eine Schicht hoeher (%s -> %s)", time.Duration(er.now-start), n, was, is)
				}
				ups += n
				seen[is] += 200 * time.Millisecond
				if c.link > 0 && estAudio+er.sent > c.link {
					over++
				}
			}
			if ups > c.tries {
				t.Fatalf("%d Schritte nach oben in 10 min (hoechstens %d): %v", ups, c.tries, seen)
			}
			if c.link == 0 {
				if er.video() != "hmm" || seen["hmm"] < 9*time.Minute {
					t.Fatalf("freie Leitung: am Ende %q, Verlauf %v", er.video(), seen)
				}
			} else if d := time.Duration(over) * 200 * time.Millisecond; d > 20*time.Second {
				t.Fatalf("%v von 10 min ueber der Leitung: %v", d, seen)
			}
			t.Logf("%d Schritte nach oben, %v ueber der Leitung, Verlauf %v", ups, time.Duration(over)*200*time.Millisecond, seen)
		})
	}
}

// TestSchaetzerAufschubJeSchicht (Prueferfunde): Der Aufschub nach gescheiterten h-Versuchen gilt nur fuer den Schritt
// auf h. Bricht die Leitung kurz darauf so weit ein, dass der Platz auf l faellt, ist er nach dem Einbruch zuegig
// wieder auf m - und nicht erst nach dem h-Aufschub (im Modell waren das fast 5 min auf l). Das gilt auch, wenn im
// Einbruch ein m-Versuch scheitert: Er bekommt seinen eigenen, kleinen Aufschub und erbt nicht den von h. Der Einbruch
// stellt den h-Aufschub und seine Uhr nicht zurueck: Der naechste h-Versuch kommt wie geplant.
func TestSchaetzerAufschubJeSchicht(t *testing.T) {
	for _, dip := range []time.Duration{2 * time.Second, 12 * time.Second, 20 * time.Second} {
		t.Run(dip.String(), func(t *testing.T) {
			er := newEstRoom(t, 1250e3, 810e3)
			sl := er.l.slots[1]
			er.link = 1000e3
			er.until("m", 2*time.Second)
			for i := 0; sl.dwellOf("h") < upDwellMax; i++ {
				if i > 5*60*15 {
					t.Fatalf("aufschub nach 15 min erst %v", sl.dwellOf("h"))
				}
				er.tick()
			}
			failed := er.now
			er.run(10 * time.Second)
			er.link = 400e3
			er.until("l", 5*time.Second)
			er.run(dip)
			er.link = 1000e3
			back := time.Duration(0)
			if er.layer() != "m" {
				back = er.until("m", 40*time.Second)
			}
			er.run(upHold + 2*time.Second)
			if er.layer() != "m" || sl.dwellOf("h") != upDwellMax || sl.dwellOf("m") != 0 {
				t.Fatalf("nach dem Einbruch: %q, aufschub h %v, m %v", er.layer(), sl.dwellOf("h"), sl.dwellOf("m"))
			}
			for er.now-failed < int64(upDwellMax-2*time.Second) {
				er.tick()
				if er.layer() == "h" {
					t.Fatalf("h-Versuch schon %v nach dem Fehlversuch", time.Duration(er.now-failed))
				}
			}
			next := er.until("h", 30*time.Second)
			t.Logf("nach dem Einbruch wieder m nach %v; naechster h-Versuch %v nach dem letzten", back, upDwellMax-2*time.Second+next)
		})
	}
}

// TestSchaetzerQuelleWeg (Prueferfund): Hat h nach einer engen Phase lange gehalten, ist der alte Aufschub erledigt -
// auch wenn die naechste Ruecknahme nicht vom Budget kommt, sondern der Sender h kurz wegnimmt. Frueher wurde nur bei
// einer Budget-Ruecknahme zurueckgestellt; h kam dann erst nach dem alten Aufschub wieder.
func TestSchaetzerQuelleWeg(t *testing.T) {
	er := newEstRoom(t, 1250e3, 810e3)
	sl := er.l.slots[1]
	er.link = 1000e3
	er.until("m", 2*time.Second)
	for i := 0; sl.dwellOf("h") < 2*time.Minute; i++ {
		if i > 5*60*15 {
			t.Fatalf("aufschub nach 15 min erst %v", sl.dwellOf("h"))
		}
		er.tick()
	}
	er.link = 0
	er.until("h", upDwellMax)
	er.run(time.Minute)
	if er.layer() != "h" || sl.dwellOf("h") != 0 {
		t.Fatalf("nach 1 min auf h: %q, aufschub %v", er.layer(), sl.dwellOf("h"))
	}
	off := map[*fwd.Source]bool{}
	for s, v := range er.on {
		off[s] = v && s != er.src["h"]
	}
	er.r.SetRemote("fern", -1, off)
	er.until("m", 3*time.Second)
	er.run(3 * time.Second)
	er.r.SetRemote("fern", -1, er.on)
	d := er.until("h", 20*time.Second)
	t.Logf("h wieder %v, nachdem der Sender es zurueckgab", d)
}

// TestSchaetzerWechselndesScheitern (Prueferfund): Scheitert h abwechselnd sofort und nach laengerem Halten (unter
// upHold), darf der Aufschub nicht jedes Mal von vorn beginnen - sonst kommt das Dauer-Pendeln zurueck (im Modell 38
// bis 40 Versuche je 10 min). Die spaete Ruecknahme deckelt ihn nur auf upDwellSoft, sie verkuerzt ihn nicht.
func TestSchaetzerWechselndesScheitern(t *testing.T) {
	for _, hold := range []time.Duration{12 * time.Second, 15 * time.Second} {
		t.Run(hold.String(), func(t *testing.T) {
			er := newEstRoom(t, 1250e3, 810e3)
			er.until("h", 10*time.Second)
			ups, onH, late := 1, time.Duration(0), true
			upAt, start := er.now, er.now
			for er.now-start < int64(10*time.Minute) {
				was := er.layer()
				wait := 400 * time.Millisecond
				if late {
					wait = hold
				}
				er.loss = was == "h" && er.now-upAt >= int64(wait)
				er.tick()
				switch is := er.layer(); {
				case was != "h" && is == "h":
					ups, upAt = ups+1, er.now
				case was == "h" && is != "h":
					late = !late
				}
				if er.layer() == "h" {
					onH += 200 * time.Millisecond
				}
			}
			if ups > 16 {
				t.Fatalf("pendelt: %d h-Versuche in 10 min (%v auf h)", ups, onH)
			}
			if ups < 6 {
				t.Fatalf("nur %d h-Versuche in 10 min", ups)
			}
			t.Logf("%d h-Versuche in 10 min, %v auf h", ups, onH)
		})
	}
}
