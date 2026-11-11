package bwe

import (
	"testing"

	"github.com/pion/rtcp"
)

// spy: Mitleser, dessen Detektor-Eingang mitgeschnitten wird (ueber die Fenster des Detektors).
func totals(m *Mitleser) (n, lost int) {
	d := m.det
	for i := d.lo(); i < d.histN; i++ {
		n += d.at(i).n
		lost += d.at(i).lost
	}
	if d.curOpen {
		n += d.cur.n
		lost += d.cur.lost
	}
	return
}

func TestMitleserZuordnung(t *testing.T) {
	m := NewMitleser(Defaults())
	// 40 Pakete ueber den 16-bit-Ueberlauf hinweg, alle 5 ms, 1000 Byte
	base := uint16(65520)
	for i := 0; i < 40; i++ {
		m.Sent(int64(i)*5e6, base+uint16(i), 1000, KindVideo)
	}
	var in []rep
	for i := 0; i < 40; i++ {
		st := StRecv
		if i == 7 || i == 20 {
			st = StLost
		}
		in = append(in, rep{0, st, 50_000 + int64(i)*5000})
	}
	m.Feedback(300e6, wire(t, fbOf(base, 0, in)))
	c := m.Counters()
	if c.Acked != 38 || c.Lost != 2 || c.Unknown != 0 || c.Dup != 0 || c.Bad != 0 || c.Sent[KindVideo] != 40 {
		t.Fatalf("%+v", c)
	}
	// Sendezeiten richtig zugeordnet: Laufzeit ueberall 50 ms -> kleinste Laufzeit 50 ms, Fenster nach 100 ms / 20 Paketen
	d := m.det
	if d.histN != 1 || d.at(0).n != 20 || d.at(0).lost != 1 || d.at(0).minD != 50_000 || d.at(0).startNs != 0 || d.cur.startNs != 100e6 || d.cur.minD != 50_000 {
		t.Fatalf("fenster %+v laufend %+v", *d.at(0), d.cur)
	}

	// dieselbe Rueckmeldung noch einmal (cc haengt an jedem RTCP-Leser): nichts zaehlt doppelt
	m.Feedback(301e6, fbOf(base, 0, in))
	c = m.Counters()
	if n, lost := totals(m); n != 40 || lost != 2 || c.Dup != 40 || c.Acked != 38 || c.Lost != 2 {
		t.Fatalf("doppelt: n=%d lost=%d %+v", n, lost, c)
	}
	// erst verloren gemeldet, dann doch angekommen: zaehlt nicht neu, bleibt beim Detektor "verloren"
	m.Feedback(302e6, fbOf(base+7, 0, []rep{{0, StRecv, 400_000}}))
	c = m.Counters()
	if n, lost := totals(m); n != 40 || lost != 2 || c.Recovered != 1 || c.Acked != 38 {
		t.Fatalf("nachzuegler: n=%d lost=%d %+v", n, lost, c)
	}
	// Folgenummern, die wir nie gesendet haben
	m.Feedback(303e6, fbOf(3000, 0, []rep{{0, StRecv, 1000}, {0, StLost, 0}}))
	if c = m.Counters(); c.Unknown != 2 {
		t.Fatalf("unbekannt: %+v", c)
	}
}

// Eine verlorene Rueckmeldung ist kein verlorenes Paket: Was nie gemeldet wird, zaehlt nicht.
func TestMitleserVerloreneRueckmeldung(t *testing.T) {
	m := NewMitleser(Defaults())
	for i := 0; i < 300; i++ {
		m.Sent(int64(i)*5e6, uint16(i), 1000, KindVideo)
	}
	mk := func(from, n int) *rtcp.TransportLayerCC {
		var in []rep
		for i := from; i < from+n; i++ {
			in = append(in, rep{0, StRecv, 30_000 + int64(i)*5000})
		}
		return fbOf(uint16(from), 0, in)
	}
	m.Feedback(600e6, mk(0, 100))
	// Rueckmeldung zu 100..199 geht verloren
	m.Feedback(1600e6, mk(200, 100))
	c := m.Counters()
	n, lost := totals(m)
	if c.Acked != 200 || c.Lost != 0 || n != 200 || lost != 0 {
		t.Fatalf("n=%d lost=%d %+v", n, lost, c)
	}
	if s := m.Snapshot(1600e6); s.State != Free || s.Loss != 0 {
		t.Fatalf("%+v", s)
	}
	// die verspaetete Rueckmeldung kommt doch noch: Pakete vor dem laufenden Fenster zaehlen nicht mehr
	m.Feedback(1700e6, mk(100, 100))
	if c = m.Counters(); c.Acked != 300 || c.Late != 100 {
		t.Fatalf("%+v", c)
	}
}

// Ring: Ein ueberschriebener Eintrag (Folgenummer + RingSize) und ein zu alter Eintrag werden nicht zugeordnet.
func TestMitleserRing(t *testing.T) {
	m := NewMitleser(Defaults())
	m.Sent(0, 5, 100, KindAudio)
	m.Sent(1e6, 5+RingSize, 100, KindAudio) // selber Platz
	m.Feedback(2e6, fbOf(5, 0, []rep{{0, StRecv, 1000}}))
	if c := m.Counters(); c.Unknown != 1 || c.Acked != 0 {
		t.Fatalf("%+v", c)
	}
	m.Feedback(3e6, fbOf(5+RingSize, 0, []rep{{0, StRecv, 1000}}))
	if c := m.Counters(); c.Acked != 1 {
		t.Fatalf("%+v", c)
	}
	// 65536 Pakete spaeter traegt ein anderes Paket dieselbe Nummer; liegt das alte noch im Ring (Sendepause), gilt es
	// nach sentMaxAge nicht mehr
	m.Sent(10e9, 77, 100, KindPad)
	m.Feedback(10e9+sentMaxAge+1, fbOf(77, 0, []rep{{0, StLost, 0}}))
	if c := m.Counters(); c.Unknown != 2 || c.Lost != 0 || c.Sent[KindPad] != 1 {
		t.Fatalf("%+v", c)
	}
}

// Voller Umlauf der 16-bit-Nummer bei laufendem Strom: jede Rueckmeldung trifft das richtige (neue) Paket.
func TestMitleserUmlauf(t *testing.T) {
	m := NewMitleser(Defaults())
	const n = 70_000 + 100
	var in []rep
	fbFrom := 0
	for i := 0; i < n; i++ {
		ns := int64(i) * 2e6
		m.Sent(ns, uint16(i), 500, KindVideo)
		in = append(in, rep{0, StRecv, ns/1000 + 10_000})
		if len(in) == 50 {
			m.Feedback(ns+20e6, fbOf(uint16(fbFrom), uint32(in[0].us/refUnit), in))
			fbFrom, in = i+1, in[:0]
		}
	}
	c := m.Counters()
	if c.Acked != n || c.Unknown != 0 || c.Dup != 0 || c.Late != 0 || c.Bad != 0 {
		t.Fatalf("%+v", c)
	}
	// Laufzeit durchgehend 10 ms (+ Rundung auf 250 us): Schlange 0, frei
	s := m.Snapshot(int64(n) * 2e6)
	if s.State != Free || s.QueueUs > 500 || c.ToWarn+c.ToCong != 0 {
		t.Fatalf("%+v %+v", s, c)
	}
	if s.AckedBps < 1_950_000 || s.AckedBps > 2_050_000 { // 500 Byte alle 2 ms = 2 Mbit/s
		t.Fatalf("durchsatz %d", s.AckedBps)
	}
}
