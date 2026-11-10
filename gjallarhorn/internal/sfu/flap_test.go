package sfu

import (
	"sync/atomic"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// drive fuehrt den Hysterese-Zustand im 200-ms-Takt d lang weiter und liefert die neue Zeit.
func drive(f *flap, live, lower bool, now int64, d time.Duration) int64 {
	for end := now + int64(d); now < end; {
		now += int64(200 * time.Millisecond)
		f.step(live, lower, 30e3, now)
	}
	return now
}

// TestFlapZustand: Wartezeit nach einem Flattern, Verdoppeln bis zur Obergrenze, Rueckstellung nach Stabilitaet, kein
// Warten, wenn alle Schichten zugleich wegfallen.
func TestFlapZustand(t *testing.T) {
	var f flap
	now := int64(1000 * time.Second)
	now = drive(&f, true, true, now, time.Second)
	if !f.use || f.held {
		t.Fatal("neue Quelle nicht sofort nutzbar")
	}
	now = drive(&f, false, true, now, 200*time.Millisecond)
	if !f.held || f.hold != flapHold || f.flaps != 1 {
		t.Fatalf("flattern: held %v hold %v flaps %d", f.held, f.hold, f.flaps)
	}
	if f.bps != 30e3 || f.run < 900*time.Millisecond {
		t.Fatalf("zuletzt gemessen: bps %v run %v", f.bps, f.run)
	}
	// 2 s live, dann wieder weg: bleibt gesperrt, Wartezeit unveraendert (war nie zurueck)
	now = drive(&f, true, true, now, 2*time.Second)
	if !f.held {
		t.Fatal("nach 2 s schon zurueck")
	}
	now = drive(&f, false, true, now, 200*time.Millisecond)
	if !f.held || f.hold != flapHold || f.flaps != 1 {
		t.Fatalf("kurz live: held %v hold %v flaps %d", f.held, f.hold, f.flaps)
	}
	// am Stueck live: hold nach dem ersten Takt mit Paketen zurueck
	now = drive(&f, true, true, now, flapHold)
	if !f.held {
		t.Fatal("vor Ablauf der Wartezeit zurueck")
	}
	now = drive(&f, true, true, now, 200*time.Millisecond)
	if f.held || !f.use || f.backAt != now {
		t.Fatalf("nach %v nicht zurueck", flapHold)
	}
	// gleich wieder weg: verdoppeln bis zur Obergrenze
	want := flapHold
	for i := 0; i < 8; i++ {
		now = drive(&f, false, true, now, 400*time.Millisecond)
		want *= 2
		if want > flapMax {
			want = flapMax
		}
		if f.hold != want {
			t.Fatalf("runde %d: hold %v, erwartet %v", i, f.hold, want)
		}
		now = drive(&f, true, true, now, f.hold-200*time.Millisecond)
		if !f.held {
			t.Fatalf("runde %d: vor %v zurueck", i, f.hold)
		}
		now = drive(&f, true, true, now, 400*time.Millisecond)
		if f.held {
			t.Fatalf("runde %d: nach %v nicht zurueck", i, f.hold)
		}
	}
	// lange stabil: naechster Ausfall faengt wieder bei flapHold an
	now = drive(&f, true, true, now, flapStable)
	now = drive(&f, false, true, now, 200*time.Millisecond)
	if f.hold != flapHold {
		t.Fatalf("nach stabiler Zeit: hold %v", f.hold)
	}
	now = drive(&f, true, true, now, flapHold+200*time.Millisecond)
	// alle Schichten zugleich weg (Sender, Netz): kein Warten
	now = drive(&f, false, false, now, 2*time.Second)
	if f.held {
		t.Fatal("ohne tiefere Schicht gesperrt")
	}
	now = drive(&f, true, true, now, 200*time.Millisecond)
	if !f.use {
		t.Fatal("nach gemeinsamem Ausfall nicht sofort nutzbar")
	}
	// alte, hochgezaehlte Wartezeit wirkt nicht nach: nach einer Rueckkehr ohne Hysterese faengt ein Flattern wieder
	// bei flapHold an (auch innerhalb von flapStable)
	f.hold = flapMax
	now = drive(&f, true, true, now, 2*time.Second)
	drive(&f, false, true, now, 200*time.Millisecond)
	if !f.held || f.hold != flapHold {
		t.Fatalf("nach gemeinsamem Ausfall: held %v hold %v", f.held, f.hold)
	}
}

// flapRoom: Empfaenger mit Sprecherplatz (TCP) und ein Teilnehmender ueber eine Bruecke mit den Schichten l und m.
// Der Takt laeuft mit kuenstlicher Zeit (tickAt); nutzbar sind die Quellen ueber die Bruecke per SetRemote.
type flapRoom struct {
	t      *testing.T
	r      *Room
	l      *Leg
	sl, sm *fwd.Source
	now    int64
	evs    []Slot // Meldungen des Sprecherplatzes
}

func newFlapRoom(t *testing.T) *flapRoom {
	fr := &flapRoom{t: t, now: fwd.Now()}
	n := NewNode(Config{Name: "k", Tick: time.Hour, OnLayout: func(ev LayoutEvent) {
		for _, s := range ev.Slots {
			if s.Class == "speaker" {
				fr.evs = append(fr.evs, s)
			}
		}
	}})
	t.Cleanup(n.Close)
	r, err := n.CreateRoom("raumflattern", nil)
	if err != nil {
		t.Fatal(err)
	}
	fr.r = r
	if fr.l, err = r.AddLeg(Grant{Room: "raumflattern", Peer: "empf", Leg: "empf-v", Role: RoleVideo}, PathTCP, Want{Speaker: true}); err != nil {
		t.Fatal(err)
	}
	if !r.AddRemote("b1", "fern", Mobile, nil) {
		t.Fatal("aufnehmen")
	}
	fr.sl = r.RemoteSource("b1", "fern", fwd.Video, "l", false, 0, nil)
	fr.sm = r.RemoteSource("b1", "fern", fwd.Video, "m", false, 0, nil)
	if fr.sl == nil || fr.sm == nil {
		t.Fatal("quellen")
	}
	return fr
}

func (fr *flapRoom) on(m bool) {
	fr.r.SetRemote("fern", -1, map[*fwd.Source]bool{fr.sl: true, fr.sm: m})
}

func (fr *flapRoom) tick(d time.Duration) {
	for end := fr.now + int64(d); fr.now < end; {
		fr.now += int64(200 * time.Millisecond)
		fr.r.tickAt(fr.now)
	}
}

func (fr *flapRoom) layer() string {
	for _, s := range fr.l.Layout() {
		if s.Class == "speaker" {
			return s.Layer
		}
	}
	return ""
}

// until laesst den Takt laufen, bis der Sprecherplatz die Schicht want zeigt (hoechstens max) -> vergangene Zeit.
func (fr *flapRoom) until(want string, max time.Duration) time.Duration {
	start := fr.now
	for fr.now-start < int64(max) {
		fr.tick(200 * time.Millisecond)
		if fr.layer() == want {
			return time.Duration(fr.now - start)
		}
	}
	fr.t.Fatalf("schicht %q nicht nach %v (ist %q)", want, max, fr.layer())
	return 0
}

func (fr *flapRoom) last() Slot {
	if len(fr.evs) == 0 {
		fr.t.Fatal("keine Meldung")
	}
	return fr.evs[len(fr.evs)-1]
}

// TestFlapKeinPendeln (Feldbefund: Sender liefert m nur zeitweise, Empfaenger ueber eine Bruecke): Die flatternde Schicht
// wird einmal abgewaehlt und erst nach Stabilitaet wieder genommen; Ausfall schaltet sofort herunter; erneutes Flattern
// verdoppelt die Wartezeit; Budget-Wechsel melden ihren Grund und beruehren die Hysterese nicht.
func TestFlapKeinPendeln(t *testing.T) {
	fr := newFlapRoom(t)
	fr.on(true)
	fr.tick(time.Second)
	if fr.layer() != "m" {
		t.Fatalf("start: %q", fr.layer())
	}
	if w := fr.last().Why; w != "" {
		t.Fatalf("erste Belegung mit Grund %q", w)
	}
	// 20 Runden: m 1 s weg, 1,5 s da (ohne Hysterese: nach layerDwell jedes Mal wieder hoch)
	n0 := len(fr.evs)
	for i := 0; i < 20; i++ {
		fr.on(false)
		fr.tick(time.Second)
		fr.on(true)
		fr.tick(1500 * time.Millisecond)
	}
	changes := fr.evs[n0:]
	if len(changes) != 1 || changes[0].Layer != "l" {
		t.Fatalf("pendelt: %d Meldungen %+v", len(changes), changes)
	}
	if c := changes[0]; c.Why != whyGone || c.HoldMs != int(flapHold/time.Millisecond) || c.LiveMs < 900 {
		t.Fatalf("grund des Abschaltens: %+v", c)
	}
	if fr.layer() != "l" {
		t.Fatalf("nach dem Flattern: %q", fr.layer())
	}
	if fr.sm.Subscribed() {
		t.Fatal("flatternde Schicht weiter abonniert (Nachfrage an die Bruecke)")
	}
	// stabil: Rueckkehr nach flapHold am Stueck (der letzte Wechsel nach unten liegt laenger als layerDwell zurueck)
	fr.on(false)
	fr.tick(time.Second)
	fr.on(true)
	d := fr.until("m", 10*time.Second)
	if d < flapHold || d > flapHold+400*time.Millisecond {
		t.Fatalf("rueckkehr nach %v", d)
	}
	if c := fr.last(); c.Why != whyBack || c.LiveMs < int(flapHold/time.Millisecond) || c.HoldMs != int(flapHold/time.Millisecond) {
		t.Fatalf("grund der Rueckkehr: %+v", c)
	}
	fr.tick(time.Second)
	if w := fr.l.Layout()[0].Why; w != "" {
		t.Fatalf("grund bleibt stehen: %q", w)
	}
	// echter Ausfall: sofort herunter (ein Takt)
	fr.on(false)
	fr.tick(200 * time.Millisecond)
	if fr.layer() != "l" || fr.last().Why != whyGone {
		t.Fatalf("ausfall nicht sofort: %q %+v", fr.layer(), fr.last())
	}
	// gleich nach der Rueckkehr wieder geflattert: doppelte Wartezeit
	fr.on(true)
	d = fr.until("m", 20*time.Second)
	if d < 2*flapHold || d > 2*flapHold+400*time.Millisecond {
		t.Fatalf("zweite rueckkehr nach %v (erwartet %v)", d, 2*flapHold)
	}
	// Budget: Wechsel nach unten mit Grund budget, die Schicht bleibt ohne Hysterese
	flaps := fr.r.peers["fern"].cam["m"].fl.flaps
	fr.r.mu.Lock()
	fr.l.est.max = 300e3
	fr.r.mu.Unlock()
	fr.tick(200 * time.Millisecond)
	if fr.layer() != "l" || fr.last().Why != whyBudget || fr.last().SrcKbit != 150 {
		t.Fatalf("budget: %q %+v", fr.layer(), fr.last())
	}
	fr.r.mu.Lock()
	fr.l.est.max = 25e6
	held := fr.r.peers["fern"].cam["m"].fl.held
	fr.r.mu.Unlock()
	if held || fr.r.peers["fern"].cam["m"].fl.flaps != flaps {
		t.Fatal("budget-wechsel beruehrt die Hysterese")
	}
	fr.until("m", 10*time.Second)
	if fr.last().Why != whyBudget {
		t.Fatalf("budget zurueck: %+v", fr.last())
	}
}

// TestPLIAbstandBeiWechsel: Abo-Wechsel (Assign im Takt, auch hin und her) umgehen die PLI-Bremse nicht - je Quelle
// hoechstens eine Anforderung je fwd.PLIInterval.
func TestPLIAbstandBeiWechsel(t *testing.T) {
	old := fwd.PLIInterval
	fwd.PLIInterval = 100 * time.Millisecond
	defer func() { fwd.PLIInterval = old }()
	var pl, pm atomic.Int32
	sl := &fwd.Source{Owner: "p", Layer: "l", Kind: fwd.Video, PLI: func(uint32) { pl.Add(1) }}
	sm := &fwd.Source{Owner: "p", Layer: "m", Kind: fwd.Video, PLI: func(uint32) { pm.Add(1) }}
	fl := fwd.NewLeg()
	defer fl.Close()
	o := fl.NewOut("v0", "p", fwd.Video, fwd.ClassSpeaker)
	start := time.Now()
	for i := 0; time.Since(start) < 600*time.Millisecond; i++ {
		if i%2 == 0 {
			o.Assign(sl)
		} else {
			o.Assign(sm)
		}
		time.Sleep(5 * time.Millisecond)
	}
	el := time.Since(start)
	max := int32(el/fwd.PLIInterval) + 1
	if pl.Load() < 1 || pm.Load() < 1 || pl.Load() > max || pm.Load() > max {
		t.Fatalf("plis l %d m %d in %v (hoechstens %d)", pl.Load(), pm.Load(), el, max)
	}
}

// TestFlapFeldmuster (Feldbefund, direkter Anruf ueber UDP, est konstant): Nach einem Verlust-Schub schaltet der Sender
// m im Takt an und aus - am Knoten sichtbar m 0,2-1,2 s weg, dann 3-4 s (einmal 21 s) da. Ohne Hysterese folgte jeder
// Ausgang diesem Takt (je Wechsel ein Schluesselbild). Mit Hysterese bleibt der Platz ueberwiegend auf l, die Wartezeit
// waechst ueber die laengste Live-Phase hinaus und die Zahl der Wechsel bleibt klein.
func TestFlapFeldmuster(t *testing.T) {
	fr := newFlapRoom(t)
	fr.on(true)
	fr.tick(5 * time.Second)
	if fr.layer() != "m" {
		t.Fatalf("start: %q", fr.layer())
	}
	off := []time.Duration{1200, 445, 1187, 1000, 599} // ms, aus den Messwerten (l-Dauer am Knoten)
	on := []time.Duration{3599, 20968, 4200, 3201, 3600}
	n0, onL, total := len(fr.evs), time.Duration(0), time.Duration(0)
	step := func(d time.Duration) {
		for end := fr.now + int64(d); fr.now < end; {
			fr.tick(200 * time.Millisecond)
			total += 200 * time.Millisecond
			if fr.layer() == "l" {
				onL += 200 * time.Millisecond
			}
		}
	}
	for total < 5*time.Minute {
		for i := range off {
			fr.on(false)
			step(off[i] * time.Millisecond)
			fr.on(true)
			step(on[i] * time.Millisecond)
		}
	}
	cycles := 0
	for total2 := time.Duration(0); total2 < 5*time.Minute; {
		for i := range off {
			total2 += (off[i] + on[i]) * time.Millisecond
			cycles++
		}
	}
	changes := len(fr.evs) - n0
	if changes > 16 { // ohne Hysterese: 2 je Takt (~2*cycles)
		t.Fatalf("pendelt: %d Wechsel in %v (%d Takte des Senders)", changes, total, cycles)
	}
	if onL < total*8/10 {
		t.Fatalf("nur %v von %v auf l", onL, total)
	}
	fr.r.mu.Lock()
	hold := fr.r.peers["fern"].cam["m"].fl.hold
	fr.r.mu.Unlock()
	if hold < 4*flapHold { // gewachsen; laenger als jede Live-Phase des Musters -> l bleibt
		t.Fatalf("wartezeit %v nicht gewachsen", hold)
	}
	t.Logf("%d Wechsel in %v bei %d Sender-Takten, %v auf l", changes, total, cycles, onL)
}

// TestBudgetFehlversuchAufschub (Feldbefund h<->m an der Schwelle): Faellt ein Platz binnen upFail nach dem Hochschalten
// wegen des Budgets wieder zurueck, verdoppelt sich der Aufschub fuer den naechsten Versuch (bis upDwellMax). Eine
// Ruecknahme nach etwas laengerem Halten (unter upHold) laesst ihn stehen; erst wenn die hoehere Schicht upHold gehalten
// hat, gilt wieder layerDwell.
func TestBudgetFehlversuchAufschub(t *testing.T) {
	fr := newFlapRoom(t)
	fr.on(true)
	fr.tick(5 * time.Second)
	est := func(v float64) {
		fr.r.mu.Lock()
		fr.l.est.max = v
		fr.r.mu.Unlock()
	}
	dwell := func() time.Duration {
		fr.r.mu.Lock()
		defer fr.r.mu.Unlock()
		return fr.l.slots[0].dwell
	}
	if fr.layer() != "m" || fr.l.slots[0].class != "speaker" {
		t.Fatalf("start: %q", fr.layer())
	}
	// Grundfall: herunter (budget), lange unten, wieder hoch -> frueh. erlaubt nach layerDwell
	est(300e3)
	fr.tick(200 * time.Millisecond)
	est(25e6)
	base := fr.until("m", 40*time.Second)
	if dwell() != 0 {
		t.Fatalf("aufschub ohne Fehlversuch: %v", dwell())
	}
	want := layerDwell
	for i := 0; i < 5; i++ { // Fehlversuche: kurz nach dem Hochschalten wieder herunter
		fr.tick(600 * time.Millisecond)
		est(300e3)
		fr.tick(200 * time.Millisecond)
		if fr.layer() != "l" || fr.last().Why != whyBudget {
			t.Fatalf("runde %d: %q %+v", i, fr.layer(), fr.last())
		}
		want = min(2*want, upDwellMax)
		if dwell() != want {
			t.Fatalf("runde %d: aufschub %v, erwartet %v", i, dwell(), want)
		}
		est(25e6)
		d := fr.until("m", want+time.Minute)
		if d < want-layerDwell+base-400*time.Millisecond {
			t.Fatalf("runde %d: nach %v wieder hoch (aufschub %v, grundfall %v)", i, d, want, base)
		}
		t.Logf("runde %d: wieder hoch nach %v (aufschub %v, grundfall %v)", i, d, want, base)
	}
	// kurz gehalten (laenger als upFail, kuerzer als upHold): kein Fehlversuch, aber auch kein Zuruecksetzen
	fr.tick(3 * time.Second)
	est(300e3)
	fr.tick(200 * time.Millisecond)
	if fr.layer() != "l" || dwell() != want {
		t.Fatalf("nach kurzem Halten: %q aufschub %v, erwartet %v", fr.layer(), dwell(), want)
	}
	est(25e6)
	if d := fr.until("m", want+time.Minute); d < want-layerDwell+base-400*time.Millisecond {
		t.Fatalf("nach kurzem Halten schon nach %v wieder hoch (aufschub %v)", d, want)
	}
	// upHold gehalten: naechste Ruecknahme stellt zurueck
	fr.tick(upHold + time.Second)
	est(300e3)
	fr.tick(200 * time.Millisecond)
	if fr.layer() != "l" || dwell() != 0 {
		t.Fatalf("nach dem Halten: %q aufschub %v", fr.layer(), dwell())
	}
	est(25e6)
	if d := fr.until("m", 40*time.Second); d > base+400*time.Millisecond {
		t.Fatalf("nach dem Halten erst nach %v hoch (grundfall %v)", d, base)
	}
}
