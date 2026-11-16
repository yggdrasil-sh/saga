package sfu

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Saga 2.5, Pruefer B2 F5: Nach dem Wiederverbinden eines Geraets (neues Bein ersetzt das alte bzw. Uebergabe auf einen
// anderen Weg, Netzwechsel) zeigte die Gegenstelle die Kachel nach 0,23 s wieder, den Sprecherplatz aber erst nach
// 4,7-5,5 s auf m: Die neuen Quellen kommen nacheinander (l zuerst, m wartet auf sein Schluesselbild), der Platz nahm
// zuerst l - das zaehlte als Wechsel nach unten (downAt), danach sperrte layerDwell das Hochschalten.
//
// Modell: estRoom (Empfaenger mit Probing, pion-naher Ziel-Regler; TCP-Empfaenger ohne GCC), der Sender kommt ueber
// eine Bruecke - so laesst sich steuern, wann seine Quellen live sind. Die Arten des Wiederverbindens:
//   - ersetzt:    der Teilnehmende verschwindet und kommt als neues Peer-Objekt mit derselben ID wieder (Bein mit
//                 derselben leg_ref ersetzt, letztes Bein des Geraets); neue Quellen
//   - uebergabe:  derselbe Teilnehmende, neue Quellen (zweites Bein auf einem anderen Weg, das alte endet: seine
//                 Quellen sind abgemeldet)
//   - bruecke:    ebenso, die alten Quellen bleiben aber angemeldet (nicht live), bis die neuen sie je Schicht ersetzen
//   - netz:       dieselben Quellen, alle kurz nicht live (Netzwechsel auf demselben Bein)
// angemeldet: die neuen Quellen sind gleich angemeldet und werden erst nach und nach live (Rahmen-Weg: Spur beim ersten
// Rahmen angemeldet, live mit dem Schluesselbild); sonst angemeldet, sobald sie live sind (pion: OnTrack beim ersten
// Paket).

type wieder struct {
	*estRoom
	tcp  bool
	srcs map[string]*fwd.Source // aktuelle Quellen des Senders je Schicht
	live map[*fwd.Source]bool
}

func newWieder(t *testing.T, tcp bool) *wieder {
	er := newEstCfg(t, 1250e3, 810e3, 1, true)
	er.pion = true
	w := &wieder{estRoom: er, tcp: tcp, srcs: map[string]*fwd.Source{}, live: map[*fwd.Source]bool{}}
	if tcp {
		er.r.mu.Lock()
		er.l.Path, er.l.Ext, er.l.ExtOver = PathTCP, nil, nil
		er.r.mu.Unlock()
	}
	for k, s := range er.src {
		w.srcs[k] = s
		w.live[s] = true
	}
	return w
}

// top: hoechste Schicht des Sprecherplatzes auf diesem Weg
func (w *wieder) top() string {
	if w.tcp {
		return "m"
	}
	return "h"
}

// sync: Bytes nur fuer Quellen, die live sind; Stand an die Bruecke melden.
func (w *wieder) sync() {
	w.all, w.allR = nil, nil
	on := map[*fwd.Source]bool{}
	for k, s := range w.srcs {
		on[s] = w.live[s]
		if w.live[s] {
			w.all, w.allR = append(w.all, s), append(w.allR, w.rates[k])
		}
	}
	w.r.SetRemote("fern", -1, on)
}

// step: ein Takt; gesendet wird nur, was eine live Quelle liefert
func (w *wieder) step() {
	w.tick()
	l := w.layer()
	if s := w.srcs[l]; l != "" && (s == nil || !w.live[s]) {
		w.sent -= w.rates[l]
	}
}

// wiederverbinden: Art art, Luecke gap (nichts live), danach Quellen live nach den Verzoegerungen je Schicht. Ergebnis:
// Zeit ab Ende der Luecke bis m bzw. bis zur hoechsten Schicht, Folge der Schichten des Sprecherplatzes.
func (w *wieder) wiederverbinden(art string, angemeldet bool, gap time.Duration, delay map[string]time.Duration) (tm, tf time.Duration, seq string) {
	w.t.Helper()
	add := func(k string) {
		s := w.r.RemoteSource("b1", "fern", fwd.Video, k, false, 0, nil)
		if s == nil {
			w.t.Fatal("quelle " + k)
		}
		w.srcs[k] = s
	}
	neu := map[string]bool{} // nach dem Wiederverbinden angelegt
	switch art {
	case "ersetzt":
		if !w.r.RemoveRemote("fern") || !w.r.AddRemote("b1", "fern", Desktop, nil) {
			w.t.Fatal("ersetzen")
		}
		w.srcs = map[string]*fwd.Source{}
	case "uebergabe": // das alte Bein endet: seine Quellen sind abgemeldet
		for k, s := range w.srcs {
			w.r.DropRemoteSource(s)
			delete(w.srcs, k)
		}
	case "bruecke", "netz": // alte Quellen bleiben angemeldet (nicht live), bis neue sie ersetzen
		for _, s := range w.srcs {
			w.live[s] = false
		}
	}
	if art != "netz" && angemeldet {
		for _, k := range []string{"l", "m", "h"} {
			add(k)
			neu[k] = true
		}
	}
	w.sync()
	for end := w.now + int64(gap); w.now < end; {
		w.step()
	}
	start := w.now
	seq = w.layer()
	tm, tf = -1, -1
	for w.now-start < int64(40*time.Second) {
		el := time.Duration(w.now - start)
		for _, k := range []string{"l", "m", "h"} {
			if el < delay[k] {
				continue
			}
			if art != "netz" && !neu[k] {
				add(k)
				neu[k] = true
			}
			w.live[w.srcs[k]] = true
		}
		w.sync()
		w.step()
		if l := w.layer(); !strings.HasSuffix(seq, l) {
			seq += l
		}
		el = time.Duration(w.now - start)
		if tm < 0 && w.layer() == "m" {
			tm = el
		}
		if tf < 0 && w.layer() == w.top() {
			tf = el
		}
		if tm >= 0 && tf >= 0 && el > tf+5*time.Second {
			break
		}
	}
	return tm, tf, seq
}

type wiederFall struct {
	art        string
	angemeldet bool
	gap        time.Duration
}

func (f wiederFall) String() string {
	a := ""
	if f.angemeldet {
		a = ", angemeldet"
	}
	return fmt.Sprintf("%s%s, Luecke %v", f.art, a, f.gap)
}

var wiederFaelle = []wiederFall{
	{"ersetzt", true, 0}, {"ersetzt", false, 0}, {"ersetzt", true, 400 * time.Millisecond}, {"ersetzt", false, 400 * time.Millisecond},
	{"uebergabe", true, 0}, {"uebergabe", false, 0}, {"uebergabe", false, 400 * time.Millisecond},
	{"bruecke", true, 0}, {"bruecke", false, 0},
	{"netz", false, 400 * time.Millisecond}, {"netz", false, 1200 * time.Millisecond},
}

// eingeschwungen: Empfaenger auf der hoechsten Schicht, danach 60 s gehalten
func (w *wieder) eingeschwungen() {
	w.t.Helper()
	w.sync()
	for i := 0; i < 300 && w.layer() != w.top(); i++ {
		w.step()
	}
	for end := w.now + int64(60*time.Second); w.now < end; {
		w.step()
	}
	if w.layer() != w.top() {
		w.t.Fatalf("tcp=%v: vorher %q", w.tcp, w.layer())
	}
}

// TestSprecherWieder (F5): Nach dem Wiederverbinden ist der Sprecherplatz binnen 1,5 s auf m (dessen Quelle ist 0,3 s
// nach l live), auf UDP und TCP, fuer alle Arten des Wiederverbindens - ohne Pendeln: nach dem Neubeginn auf l geht es
// nur noch nach oben. h (UDP) folgt den Regeln von Probing (die Schaetzung ist in der Luecke gefallen).
// Vorher (testing 6561b91): m nach 3,8-4,2 s (layerDwell ab dem vermeintlichen Wechsel nach unten), h nach 4,6-7,6 s.
func TestSprecherWieder(t *testing.T) {
	delay := map[string]time.Duration{"l": 0, "m": 300 * time.Millisecond, "h": time.Second}
	for _, tcp := range []bool{false, true} {
		for _, f := range wiederFaelle {
			w := newWieder(t, tcp)
			w.eingeschwungen()
			tm, tf, seq := w.wiederverbinden(f.art, f.angemeldet, f.gap, delay)
			t.Logf("tcp=%-5v %-34v: m nach %4.1f s, %s nach %4.1f s, Folge %s", tcp, f, tm.Seconds(), w.top(), tf.Seconds(), seq)
			up := strings.TrimPrefix(seq, w.top())
			if tm < 0 || tm > 1500*time.Millisecond {
				t.Errorf("tcp=%v %v: m nach %v (Folge %s)", tcp, f, tm, seq)
			}
			if want := map[bool]string{false: "lmh", true: "lm"}[tcp]; up != want {
				t.Errorf("tcp=%v %v: Folge %s, erwartet %s nach dem Neubeginn", tcp, f, seq, want)
			}
			if tf < 0 || tf > 10*time.Second {
				t.Errorf("tcp=%v %v: %s nach %v", tcp, f, w.top(), tf)
			}
		}
	}
}

// TestSprecherWiederRueckfall (Gegenprobe zu F5): Ein echter Wechsel nach unten bleibt einer - auch gleich nach dem
// Wiederverbinden. Budget (Schaetzung faellt) und Flattern (m faellt aus, l laeuft weiter) sperren den Weg zurueck wie
// bisher layerDwell bzw. flapHold lang; eine Quelle, die schon neben der bisherigen lief, ist kein Neubeginn.
func TestSprecherWiederRueckfall(t *testing.T) {
	delay := map[string]time.Duration{"l": 0, "m": 300 * time.Millisecond, "h": time.Second}
	// Budget, TCP: nach dem Wiederverbinden auf m, dann faellt die Schaetzung kurz
	w := newWieder(t, true)
	w.eingeschwungen()
	w.wiederverbinden("uebergabe", false, 0, delay)
	w.r.mu.Lock()
	w.l.est.max = 300e3
	w.r.mu.Unlock()
	w.step()
	if w.layer() != "l" {
		t.Fatalf("budget: %q", w.layer())
	}
	w.r.mu.Lock()
	w.l.est.max = 25e6
	w.r.mu.Unlock()
	start := w.now
	for i := 0; i < 100 && w.layer() != "m"; i++ {
		w.step()
	}
	if d := time.Duration(w.now - start); w.layer() != "m" || d < layerDwell-200*time.Millisecond {
		t.Fatalf("budget: wieder m nach %v (%q), erwartet fruehestens layerDwell", d, w.layer())
	}
	// Flattern der neuen m, UDP (l laeuft weiter): sofort herunter, zurueck erst nach Hysterese und layerDwell
	w = newWieder(t, false)
	w.eingeschwungen()
	w.wiederverbinden("ersetzt", false, 0, delay)
	if w.layer() != "h" {
		t.Fatalf("flattern: vorher %q", w.layer())
	}
	w.live[w.srcs["h"]], w.live[w.srcs["m"]] = false, false
	w.sync()
	w.step()
	if w.layer() != "l" {
		t.Fatalf("flattern: %q", w.layer())
	}
	w.live[w.srcs["h"]], w.live[w.srcs["m"]] = true, true
	w.sync()
	start = w.now
	for i := 0; i < 100 && w.layer() == "l"; i++ {
		w.step()
	}
	if d := time.Duration(w.now - start); w.layer() == "l" || d < layerDwell-200*time.Millisecond {
		t.Fatalf("flattern: wieder %q nach %v, erwartet fruehestens layerDwell", w.layer(), d)
	}
}

// TestSprecherWiederBein (F5) ueber den Weg des Knotens: Das Bein des Senders wird mit derselben leg_ref ersetzt
// (Room.AddLeg - ein neues Peer-Objekt) bzw. auf einen anderen Weg uebergeben (zweites Bein, das alte endet). Die
// neuen Spuren melden sich mit dem ersten Rahmen an (Rahmen-Weg) und sind live, sobald ihr Schluesselbild da ist - l
// sofort, m 0,4 s spaeter. Empfaenger ueber TCP (Sprecherplatz bis m). Live ist eine Quelle des Beins hier wie eine
// ueber eine Bruecke (source.on), damit der Takt mit kuenstlicher Zeit laufen kann.
func TestSprecherWiederBein(t *testing.T) {
	for _, art := range []string{"ersetzt", "uebergabe"} {
		n := NewNode(Config{Name: "k", Tick: time.Hour, Probing: true})
		r, err := n.CreateRoom("raumwieder", nil)
		if err != nil {
			t.Fatal(err)
		}
		recv, err := r.AddLeg(Grant{Room: "raumwieder", Peer: "empf", Leg: "empf-b", Role: RoleBoth}, PathTCP, Want{Audio: 1, Speaker: true})
		if err != nil {
			t.Fatal(err)
		}
		g := Grant{Room: "raumwieder", Peer: "fern", Leg: "fern-b", Role: RoleBoth, Pub: PubAudio | PubVideo}
		rates := map[string]float64{"l": 150e3, "m": 500e3}
		var srcs []*fwd.Source
		var srate []float64
		bein := func(g Grant) *Leg {
			l, err := r.AddLeg(g, PathUDP, Want{})
			if err != nil {
				t.Fatal(err)
			}
			return l
		}
		spur := func(l *Leg, k string, live bool) *fwd.Source {
			s, err := l.AddSource(fwd.Video, k, false, uint32(len(srcs)+1), 0, nil)
			if err != nil {
				t.Fatal(err)
			}
			r.mu.Lock()
			st := l.Peer.srcs[s]
			st.remote, st.on = true, live
			r.mu.Unlock()
			srcs, srate = append(srcs, s), append(srate, rates[k])
			return s
		}
		on := func(s *fwd.Source) {
			r.mu.Lock()
			for _, p := range r.peers {
				if st := p.srcs[s]; st != nil {
					st.on = true
				}
			}
			r.mu.Unlock()
		}
		now := fwd.Now()
		tick := func() {
			now += int64(200 * time.Millisecond)
			for i, s := range srcs {
				s.Bytes.Add(uint64(srate[i] * 0.2 / 8))
			}
			recv.fl.Stats.BytesOut.Add(uint64(700e3 * 0.2 / 8))
			r.tickAt(now)
		}
		layer := func() string {
			for _, s := range recv.Layout() {
				if s.Class == "speaker" {
					return s.Layer
				}
			}
			return ""
		}
		alt := bein(g)
		spur(alt, "l", true)
		spur(alt, "m", true)
		for i := 0; i < 100; i++ {
			tick()
		}
		if layer() != "m" {
			t.Fatalf("%s: vorher %q", art, layer())
		}
		var neu *Leg
		switch art {
		case "ersetzt":
			neu = bein(g)
		case "uebergabe":
			g2 := g
			g2.Leg = "fern-t"
			neu = bein(g2)
			alt.Close()
		}
		srcs, srate = nil, nil
		spur(neu, "l", true)
		m := spur(neu, "m", false)
		seq, tm := "", time.Duration(-1)
		start := now
		for now-start < int64(10*time.Second) {
			if now-start >= int64(400*time.Millisecond) {
				on(m)
			}
			tick()
			if l := layer(); !strings.HasSuffix(seq, l) {
				seq += l
			}
			if tm < 0 && layer() == "m" {
				tm = time.Duration(now - start)
			}
		}
		t.Logf("%s: m nach %v, Folge %s", art, tm, seq)
		if tm < 0 || tm > 1500*time.Millisecond || seq != "lm" {
			t.Errorf("%s: m nach %v, Folge %s (erwartet lm binnen 1,5 s)", art, tm, seq)
		}
		n.Close()
	}
}

// TestSprecherWiederHauptsprecher (F5, Messstand B2): Im 1:1 war der Hauptsprecher das Geraet, das wiederverbindet. Mit
// seinem letzten Bein verschwindet es aus dem Raum; in der Luecke wird sofort die Gegenstelle Hauptsprecherin. War sie
// es schon einmal (prevDom), stand fuer sie "der vorige" - sie selbst - auf dem Sprecherplatz (mit E2E ein schwarzer
// Platz), das wiederverbundene Geraet nur auf einer Kachel (TCP: l), bis es nach domHold und Hysterese wieder
// Hauptsprecher war. Jetzt: nie das eigene Geraet auf dem Sprecherplatz; das wiederverbundene kommt sofort darauf und
// binnen 1,5 s auf m.
func TestSprecherWiederHauptsprecher(t *testing.T) {
	n := NewNode(Config{Name: "k", Tick: time.Hour, Probing: true})
	defer n.Close()
	r, err := n.CreateRoom("raumdom", nil)
	if err != nil {
		t.Fatal(err)
	}
	me, err := r.AddLeg(Grant{Room: "raumdom", Peer: "gegen", Leg: "gegen-b", Role: RoleBoth, Pub: PubAudio | PubVideo}, PathTCP,
		Want{Audio: 1, Speaker: true, Tiles: 1})
	if err != nil {
		t.Fatal(err)
	}
	type spr struct {
		s   *fwd.Source
		lvl uint32
	}
	var talk []spr
	var vid []*fwd.Source
	mine := func(kind fwd.Kind, k string) *fwd.Source {
		s, err := me.AddSource(kind, k, false, uint32(len(vid)+len(talk)+1), 1, nil)
		if err != nil {
			t.Fatal(err)
		}
		r.mu.Lock()
		st := me.Peer.srcs[s]
		st.remote, st.on = true, true
		r.mu.Unlock()
		return s
	}
	talk = append(talk, spr{mine(fwd.Audio, "a"), 60})
	vid = append(vid, mine(fwd.Video, "l"), mine(fwd.Video, "m"))
	now := fwd.Now()
	tick := func() {
		now += int64(200 * time.Millisecond)
		for _, x := range talk {
			x.s.VoicePkts.Add(5)
			x.s.LevelSum.Add(5 * (127 - x.lvl))
		}
		for _, s := range vid {
			s.Bytes.Add(uint64(300e3 * 0.2 / 8))
		}
		me.fl.Stats.BytesOut.Add(uint64(700e3 * 0.2 / 8))
		r.tickAt(now)
	}
	run := func(d time.Duration) {
		for end := now + int64(d); now < end; {
			tick()
		}
	}
	spk := func() (owner, layer string) {
		r.mu.Lock()
		defer r.mu.Unlock()
		for _, s := range me.slots {
			if s.class == "speaker" && s.owner != nil {
				return s.owner.ID, s.layer
			}
		}
		return "", ""
	}
	var fernM *fwd.Source
	fern := func(mLive bool) {
		if !r.AddRemote("b1", "fern", Desktop, nil) {
			t.Fatal("fern")
		}
		talk = append(talk, spr{r.RemoteSource("b1", "fern", fwd.Audio, "a", false, 1, nil), 30})
		l := r.RemoteSource("b1", "fern", fwd.Video, "l", false, 0, nil)
		fernM = r.RemoteSource("b1", "fern", fwd.Video, "m", false, 0, nil)
		vid = append(vid, l, fernM)
		r.SetRemote("fern", -1, map[*fwd.Source]bool{fernM: mLive})
	}
	run(3 * time.Second) // die Gegenstelle spricht allein: Hauptsprecherin
	fern(true)
	run(10 * time.Second) // das Geraet spricht lauter: Hauptsprecher, die Gegenstelle ist prevDom
	if o, l := spk(); o != "fern" || l != "m" {
		t.Fatalf("vorher: Sprecherplatz %q/%q", o, l)
	}
	// Wiederverbinden: letztes Bein zu, 0,6 s spaeter ein neues (neues Peer-Objekt), m 0,4 s nach l
	r.RemoveRemote("fern")
	talk, vid = talk[:1], vid[:2]
	run(600 * time.Millisecond)
	fern(false)
	start, self, tm := now, false, time.Duration(-1)
	for now-start < int64(10*time.Second) {
		if now-start >= int64(400*time.Millisecond) {
			r.SetRemote("fern", -1, map[*fwd.Source]bool{fernM: true})
		}
		tick()
		o, l := spk()
		self = self || o == "gegen"
		if tm < 0 && o == "fern" && l == "m" {
			tm = time.Duration(now - start)
		}
	}
	t.Logf("Sprecherplatz nach dem Wiederverbinden: fern auf m nach %v, eigenes Geraet darauf: %v", tm, self)
	if self || tm < 0 || tm > 1500*time.Millisecond {
		t.Fatalf("Sprecherplatz: fern auf m nach %v, eigenes Geraet darauf: %v", tm, self)
	}
}

// TestSprecherWiederFlatternFolge (Pruefer, Gegenprobe zu slot.swapped): Echtes Flattern im selben Bein (m und h fallen
// aus, l laeuft weiter) ist kein Quellenwechsel - unabhaengig davon, in welcher Folge sich die Schichten angemeldet
// haben (pion: OnTrack beim ersten Paket, Rahmen-Weg: erster Rahmen). Vorher galt es bei der Folge h,m,l als
// Neubeginn (st.added > was.added, die bisherige nicht live): ohne Rueckfall-Buchfuehrung, UDP h nach 6,6 statt 5,2 s.
func TestSprecherWiederFlatternFolge(t *testing.T) {
	run := func(tcp bool, folge string, aus time.Duration) string {
		w := newWieder(t, tcp)
		w.live = map[*fwd.Source]bool{}
		for _, k := range strings.Split(folge, "") { // Quellen in dieser Folge neu anmelden (ersetzt je Schicht)
			s := w.r.RemoteSource("b1", "fern", fwd.Video, k, false, 0, nil)
			w.srcs[k] = s
			w.live[s] = true
		}
		w.eingeschwungen()
		w.live[w.srcs["m"]], w.live[w.srcs["h"]] = false, false
		w.sync()
		seq, start, back := w.layer(), w.now, int64(-1)
		tm, tf := time.Duration(-1), time.Duration(-1)
		for w.now-start < int64(30*time.Second) {
			if back < 0 && w.now-start >= int64(aus) {
				w.live[w.srcs["m"]], w.live[w.srcs["h"]] = true, true
				w.sync()
				back = w.now
			}
			w.step()
			if l := w.layer(); !strings.HasSuffix(seq, l) {
				seq += l
			}
			if back >= 0 && tm < 0 && w.layer() == "m" {
				tm = time.Duration(w.now - back)
			}
			if back >= 0 && tf < 0 && w.layer() == w.top() {
				tf = time.Duration(w.now - back)
			}
		}
		return fmt.Sprintf("m nach %v, %s nach %v, Folge %s", tm, w.top(), tf, seq)
	}
	for _, tcp := range []bool{false, true} {
		for _, aus := range []time.Duration{200 * time.Millisecond, 1200 * time.Millisecond} {
			a, b := run(tcp, "lmh", aus), run(tcp, "hml", aus)
			t.Logf("tcp=%-5v aus %v: Folge l,m,h: %s; Folge h,m,l: %s", tcp, aus, a, b)
			if a != b {
				t.Errorf("tcp=%v aus %v: Anmeldefolge h,m,l verhaelt sich anders (%s) als l,m,h (%s)", tcp, aus, b, a)
			}
		}
	}
}
