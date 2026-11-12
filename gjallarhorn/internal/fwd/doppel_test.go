package fwd

import (
	"encoding/binary"
	"math/rand"
	"testing"
	"time"

	"github.com/pion/rtp"
)

// Tests zum Abgleich auf Doppel je Quelle (Source.admit) und zu verspaeteten Paketen am Ausgang (Leg.process,
// Out.lateDelta). Alle Laeufe von Hand, ohne Schreiber-Goroutine: feste Reihenfolge, keine Zeitabhaengigkeit.

// dbench: Bein ohne Schreiber mit einem Bild- und einem Ton-Ausgang.
type dbench struct {
	t   *testing.T
	leg *Leg
	o   *Out // Bild
	a   *Out // Ton
	w   *hrec
	wa  *hrec
}

func newDbench(t *testing.T) *dbench {
	b := &dbench{t: t, leg: &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}, w: &hrec{}, wa: &hrec{}}
	b.o = b.leg.NewOut("v", "p", Video, ClassSpeaker)
	b.o.Bind(b.w, 7, 96)
	b.a = b.leg.NewOut("a", "p", Audio, ClassAudio)
	b.a.Bind(b.wa, 8, 111)
	return b
}

// drain: alles Wartende durch den Schreiber.
func (b *dbench) drain() {
	for {
		e, ok := b.leg.pop()
		if !ok {
			return
		}
		b.leg.process(e.p, e.o)
		e.p.release()
	}
}

// in: ein Paket der Quelle s durch Abgleich und Schreiber.
func (b *dbench) in(s *Source, pkt []byte) {
	s.Inject(pkt)
	b.drain()
}

// seqs: die bisher geschriebenen Folgenummern des Bild-Ausgangs.
func (b *dbench) seqs() []uint16 { return hseqs(b.w) }

func hseqs(w *hrec) []uint16 {
	out := make([]uint16, len(w.hdrs))
	for i, h := range w.hdrs {
		out[i] = h.SequenceNumber
	}
	return out
}

func sameSeqs(a []uint16, b ...uint16) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// uniq: keine Folgenummer zweimal vergeben.
func uniq(t *testing.T, seqs []uint16) {
	t.Helper()
	seen := map[uint16]int{}
	for i, s := range seqs {
		if j, ok := seen[s]; ok {
			t.Fatalf("folgenummer %d zweimal vergeben (paket %d und %d): %v", s, j, i, seqs)
		}
		seen[s] = i
	}
}

// fpkt baut ein VP8-Paket mit PictureID (15 Bit), TL0PICIDX und TID; tag steht hinter dem Rahmenkopf in der Nutzlast.
func fpkt(seq uint16, ts uint32, pic uint16, tl0, tid uint8, start, key, marker bool, tag uint32) []byte {
	b := make([]byte, 12, 64)
	b[0] = 0x80
	b[1] = 96
	if marker {
		b[1] |= 0x80
	}
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	binary.BigEndian.PutUint32(b[8:], 1)
	d := byte(0x80)
	if start {
		d |= 0x10
	}
	pl := byte(1)
	if key {
		pl = 0
	}
	b = append(b, d, 0xe0, 0x80|byte(pic>>8), byte(pic), tl0, tid<<6, pl)
	return binary.BigEndian.AppendUint32(b, tag)
}

// ptag liest das Kennzeichen aus der Nutzlast eines mit fpkt gebauten Pakets.
func ptag(pl []byte) uint32 { return binary.BigEndian.Uint32(pl[len(pl)-4:]) }

// padpkt baut ein reines Fuellpaket (P-Bit, keine Nutzlast), wie es ein Absender zum Proben schickt.
func padpkt(seq uint16, ts uint32) []byte {
	b := make([]byte, 16)
	b[0] = 0xa0
	b[1] = 96
	binary.BigEndian.PutUint16(b[2:], seq)
	binary.BigEndian.PutUint32(b[4:], ts)
	binary.BigEndian.PutUint32(b[8:], 1)
	b[15] = 4
	return b
}

func audpkt(seq uint16, ts uint32) []byte { return vp8pkt(seq, ts, false, false, false) }

// TestAbgleichDoppel: Liefert eine Quelle ein Paket mehrfach (der sendende Browser wiederholt alte Pakete ueber RTX,
// pion gibt sie entpackt mit der urspruenglichen Nummer zurueck), geht nur das erste Exemplar hinaus. Die Wiederholung
// zaehlt weder am Ausgang noch als Paket, Bild oder Schluesselbild der Quelle - nur in Dups.
func TestAbgleichDoppel(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	tot, in0 := Totals.Dups.Load(), Totals.PktsIn.Load()
	b.in(s, vp8pkt(101, 4000, true, true, true))
	b.in(s, vp8pkt(102, 7000, true, false, true))
	b.in(s, vp8pkt(102, 7000, true, false, true)) // Wiederholung
	b.in(s, vp8pkt(103, 10000, true, false, true))
	b.in(s, vp8pkt(101, 4000, true, true, true)) // aeltere Wiederholung (Schluesselbild)
	b.in(s, vp8pkt(104, 13000, true, false, true))
	if got := b.seqs(); !sameSeqs(got, 101, 102, 103, 104) {
		t.Fatalf("folgenummern %v", got)
	}
	if n := b.leg.Stats.PktsOut.Load(); n != 4 {
		t.Fatalf("pkts_out %d", n)
	}
	if s.Dups.Load() != 2 || Totals.Dups.Load()-tot != 2 || s.TooOld.Load() != 0 || s.Late.Load() != 0 {
		t.Fatalf("zaehler: doppel %d (gesamt +%d) alt %d spaet %d", s.Dups.Load(), Totals.Dups.Load()-tot, s.TooOld.Load(), s.Late.Load())
	}
	if s.Pkts.Load() != 4 || s.Keys.Load() != 1 || s.TIDFrames[0].Load() != 4 || Totals.PktsIn.Load()-in0 != 6 {
		t.Fatalf("quelle: pkts %d keys %d bilder %d, eingang gesamt +%d", s.Pkts.Load(), s.Keys.Load(), s.TIDFrames[0].Load(), Totals.PktsIn.Load()-in0)
	}
}

// TestAbgleichUmlauf: Der Abgleich haelt ueber den Umlauf der Folgenummern (65535 -> 0): Doppel beiderseits der
// Grenze fallen weg, ein Nachzuegler von vor der Grenze bekommt seine Nummer.
func TestAbgleichUmlauf(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	pk := func(q uint16) []byte { return vp8pkt(q, uint32(q-65530)*3000, true, q == 65530, true) }
	dups := 0
	for i := 0; i < 16; i++ {
		q := uint16(65530 + i)
		if q == 65534 { // fehlt zunaechst
			continue
		}
		b.in(s, pk(q))
		b.in(s, pk(q)) // sofort noch einmal
		dups++
		if i >= 5 {
			if old := q - 5; old != 65534 {
				b.in(s, pk(old)) // und fuenf Pakete spaeter wieder
				dups++
			}
		}
		if q == 3 {
			b.in(s, pk(65534)) // Nachsendung ueber die Grenze hinweg
			b.in(s, pk(65534))
			dups++
		}
	}
	got := b.seqs()
	uniq(t, got)
	if len(got) != 16 || int(s.Dups.Load()) != dups || s.Late.Load() != 1 {
		t.Fatalf("%d Pakete, doppel %d (erwartet %d), spaet %d: %v", len(got), s.Dups.Load(), dups, s.Late.Load(), got)
	}
	// die Nachsendung traegt ihre eigene Nummer, alle anderen stehen in Reihe
	want := []uint16{65530, 65531, 65532, 65533, 65535, 0, 1, 2, 3, 65534, 4, 5, 6, 7, 8, 9}
	if !sameSeqs(got, want...) {
		t.Fatalf("folgenummern %v", got)
	}
	if b.o.lastSeq != 9 {
		t.Fatalf("stand des ausgangs %d", b.o.lastSeq)
	}
}

// TestAbgleichSprung: Nummernsprung und Neustart der Quelle. Ein Sprung ueber das Fenster hinaus mit juengerem
// Zeitstempel (lange Unterbrechung; Bruecke nach einer Pause) setzt das Fenster neu auf, ohne ein Paket zu verwerfen -
// auch rueckwaerts; der Ausgang zaehlt dann lueckenlos weiter, statt vergebene Nummern noch einmal zu vergeben. Ein
// Neustart mit fremder Zeitbasis gilt ab dem zweiten Paket in Reihe (das erste geht verloren). Ein einzelnes Paket
// von weit zurueck ist zu alt und aendert nichts.
func TestAbgleichSprung(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	ts := uint32(1000)
	pk := func(q uint16) []byte { ts += 3000; return vp8pkt(q, ts, true, false, true) }
	b.in(s, vp8pkt(100, ts, true, true, true))
	b.in(s, pk(101))
	b.in(s, pk(102))
	// 1. weit voraus, Zeitstempel juenger: neue Folge, nichts verworfen
	b.in(s, pk(5000))
	b.in(s, pk(5001))
	if got := b.seqs(); !sameSeqs(got, 100, 101, 102, 5000, 5001) || s.TooOld.Load() != 0 {
		t.Fatalf("sprung voraus: %v, alt %d", got, s.TooOld.Load())
	}
	// ... ein Doppel von vor dem Sprung liegt jetzt ausserhalb des Fensters
	b.in(s, vp8pkt(101, 4000, true, false, true))
	if got := b.seqs(); len(got) != 5 || s.TooOld.Load() != 1 || s.Dups.Load() != 0 {
		t.Fatalf("altes Paket nach dem Sprung: %v, alt %d doppel %d", got, s.TooOld.Load(), s.Dups.Load())
	}
	// 2. weit zurueck, Zeitstempel juenger (Bruecke: Ausgang der Gegenseite neu angelegt): neue Folge ab dem ersten
	// Paket; der Ausgang haengt sie an seinen Stand an
	b.in(s, pk(300))
	b.in(s, pk(301))
	b.in(s, pk(301)) // Doppel im neuen Fenster
	if got := b.seqs(); !sameSeqs(got, 100, 101, 102, 5000, 5001, 5002, 5003) || s.TooOld.Load() != 1 || s.Dups.Load() != 1 {
		t.Fatalf("sprung zurueck: %v, alt %d doppel %d", got, s.TooOld.Load(), s.Dups.Load())
	}
	// ... Nachzuegler der alten Folge, die im neuen Fenster liegen, gehen nicht hinaus (vor dem Neuanfang des Ausgangs)
	b.in(s, vp8pkt(299, 2000, true, false, true))
	if got := b.seqs(); len(got) != 7 || b.leg.Stats.LateDropped.Load() != 0 {
		t.Fatalf("Nachzuegler vor dem Neuanfang: %v, verworfen %d", got, b.leg.Stats.LateDropped.Load())
	}
	// 3. Neustart mit fremder Zeitbasis (Zeitstempel aelter): das erste Paket faellt weg, ab dem zweiten in Reihe gilt
	// die neue Folge
	b.in(s, vp8pkt(20000, 500, true, true, true))
	if got := b.seqs(); len(got) != 7 || s.TooOld.Load() != 2 {
		t.Fatalf("erstes Paket des Neustarts: %v, alt %d", got, s.TooOld.Load())
	}
	b.in(s, vp8pkt(20001, 3500, true, false, true))
	b.in(s, vp8pkt(20002, 6500, true, false, true))
	got := b.seqs()
	uniq(t, got)
	if len(got) != 9 || got[8] != got[7]+1 || s.TooOld.Load() != 2 {
		t.Fatalf("neustart: %v, alt %d", got, s.TooOld.Load())
	}
	// 4. ein einzelnes Paket von weit zurueck: zu alt, die Folge laeuft weiter
	b.in(s, vp8pkt(10, 100, true, false, true))
	b.in(s, vp8pkt(20003, 9500, true, false, true))
	b.in(s, vp8pkt(11, 200, true, false, true)) // noch eines, aber nicht in Reihe eingetroffen
	b.in(s, vp8pkt(20004, 12500, true, false, true))
	got = b.seqs()
	uniq(t, got)
	if len(got) != 11 || got[10] != got[8]+2 || s.TooOld.Load() != 4 {
		t.Fatalf("einzelne alte Pakete: %v, alt %d", got, s.TooOld.Load())
	}
	for i := 1; i < len(got); i++ { // der Ausgang laeuft nie rueckwaerts
		if int16(got[i]-got[i-1]) <= 0 {
			t.Fatalf("ausgang laeuft rueckwaerts: %v", got)
		}
	}
}

// TestAbgleichSchichtwechsel: Jede Quelle hat ihr eigenes Fenster - gleiche Folgenummern auf zwei Schichten sind keine
// Doppel. Und der Befund aus der Pruefung (frueher TestDoppelDerQuelleFolgen): Ein aelteres Doppel direkt vor einem
// Schichtwechsel setzt den Stand des Ausgangs nicht mehr zurueck, das Schluesselbild der neuen Schicht bekommt die
// naechste freie Nummer.
func TestAbgleichSchichtwechsel(t *testing.T) {
	b := newDbench(t)
	lo := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(lo)
	for i := 0; i < 4; i++ { // beide Schichten mit denselben Nummern
		q := uint16(101 + i)
		b.in(lo, vp8pkt(q, uint32(i)*3000, true, i == 0, true))
		b.in(hi, vp8pkt(q, uint32(i)*3000, true, i == 0, true))
	}
	b.in(lo, vp8pkt(102, 3000, true, false, true)) // aelteres Doppel
	if lo.Dups.Load() != 1 || hi.Dups.Load() != 0 || hi.Pkts.Load() != 4 {
		t.Fatalf("doppel lo %d hi %d, pakete hi %d", lo.Dups.Load(), hi.Dups.Load(), hi.Pkts.Load())
	}
	b.o.Assign(hi)
	b.in(hi, vp8pkt(105, 12000, true, true, true)) // Schluesselbild: Wechsel
	b.in(hi, vp8pkt(105, 12000, true, true, true)) // ... und noch einmal
	b.in(lo, vp8pkt(105, 12000, true, false, true))
	b.in(hi, vp8pkt(106, 15000, true, false, true))
	if got := b.seqs(); !sameSeqs(got, 101, 102, 103, 104, 105, 106) || b.o.Current() != hi || hi.Dups.Load() != 1 {
		t.Fatalf("folgenummern %v (doppel hi %d)", got, hi.Dups.Load())
	}
}

// TestSpaetesOriginal: Ein erstes Exemplar, das hinter dem Stand der Quelle eintrifft (Original auf dem Weg zum Knoten
// verloren und nachgesendet, oder umsortiert), geht mit der Nummer seiner Luecke hinaus. Der Stand des Ausgangs
// (lastSeq) bleibt, wo er war: Das naechste Paket und ein Schichtwechsel schliessen dort an.
func TestSpaetesOriginal(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	b.in(s, vp8pkt(10, 0, true, true, true))       // Bild 1
	b.in(s, vp8pkt(11, 3000, true, false, false))  // Bild 2: 11, 12, 13 - 12 fehlt zunaechst
	b.in(s, vp8pkt(13, 3000, false, false, true))  //
	b.in(s, vp8pkt(14, 6000, true, false, true))   // Bild 3
	b.in(s, vp8pkt(12, 3000, false, false, false)) // Nachsendung
	if got := b.seqs(); !sameSeqs(got, 10, 11, 13, 14, 12) || b.o.lastSeq != 14 || s.Late.Load() != 1 {
		t.Fatalf("nachsendung: %v, stand %d, spaet %d", got, b.o.lastSeq, s.Late.Load())
	}
	if h := b.w.hdrs[4]; h.Timestamp != b.w.hdrs[1].Timestamp || h.Marker {
		t.Fatalf("nachsendung: zeitstempel %d (bild 2: %d)", h.Timestamp, b.w.hdrs[1].Timestamp)
	}
	b.in(s, vp8pkt(12, 3000, false, false, false)) // die Nachsendung noch einmal: Doppel
	b.in(s, vp8pkt(15, 9000, true, false, true))
	if got := b.seqs(); !sameSeqs(got, 10, 11, 13, 14, 12, 15) || s.Dups.Load() != 1 {
		t.Fatalf("nach der nachsendung: %v", got)
	}
	// Nachsendung direkt vor einem Schichtwechsel: das Schluesselbild schliesst an den Stand an, nicht an die Luecke
	b.in(s, vp8pkt(17, 12000, false, false, true)) // Bild 5: 16 fehlt
	b.in(s, vp8pkt(16, 12000, true, false, false)) // Rahmenanfang kommt nach
	b.o.Assign(hi)
	b.in(hi, vp8pkt(900, 500000, true, true, true))
	b.in(s, vp8pkt(18, 15000, true, false, true)) // alte Schicht: nicht mehr weitergeleitet
	got := b.seqs()
	if !sameSeqs(got, 10, 11, 13, 14, 12, 15, 17, 16, 18) {
		t.Fatalf("schichtwechsel nach nachsendung: %v", got)
	}
	// ein spaetes Schluesselbild der wartenden Schicht loest keinen Wechsel aus (sein Rest ist schon vorbei)
	b.o.Assign(s)
	b.in(s, vp8pkt(21, 21000, false, false, true))
	b.in(s, vp8pkt(20, 21000, true, true, false)) // Schluesselbild-Anfang, verspaetet
	if b.o.Current() != hi || len(b.seqs()) != 9 {
		t.Fatalf("spaetes Schluesselbild hat den Wechsel ausgeloest: %v", b.seqs())
	}
	b.in(s, vp8pkt(22, 24000, true, true, true))
	if got := b.seqs(); b.o.Current() != s || len(got) != 10 || got[9] != 19 {
		t.Fatalf("wechsel am naechsten Schluesselbild: %v", got)
	}
}

// TestSpaetBeiZeitschichten: Laesst der Ausgang Bilder aus (Zeitschicht ueber maxTID), aendert sich sein Versatz mit
// jedem ausgelassenen Paket. Ein Nachzuegler bekommt den Versatz seiner Stelle (Folgenummer und PictureID); ein
// Nachzuegler eines ausgelassenen Bildes oder einer ausgelassenen Zeitschicht geht nicht hinaus und verschiebt nichts;
// ein verspaeteter Rahmenanfang stellt den Zustand des laufenden Bildes nicht um; ein ganzes Bild, das zwischen zwei
// ausgelassenen nachkommt, geht mit seiner Nummer hinaus.
func TestSpaetBeiZeitschichten(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	b.o.SetMaxTID(0)
	// Bilder zu je zwei Paketen: A(TL0, Schluesselbild) 20,21 - B(TL1) 22,23 - C(TL0) 24,25 - D(TL1) 26,27 - E(TL0) 28,29
	fr := func(q uint16, k int, start bool) []byte { // Paket q des k-ten Bildes
		return fpkt(q, uint32(k)*3000, uint16(100+k), uint8(1+k/2), uint8(k%2), start, k == 0, !start, uint32(q))
	}
	b.in(s, fr(20, 0, true)) // A, Ende (21) fehlt zunaechst
	b.in(s, fr(22, 1, true)) // B: ausgelassen; Ende (23) fehlt zunaechst
	b.in(s, fr(24, 2, true)) // C, Ende (25) fehlt zunaechst
	// D: Anfang (26) fehlt zunaechst - sein Ende laeuft als Fortsetzung von C mit
	b.in(s, fr(27, 3, false))
	b.in(s, fr(28, 4, true))  // E
	b.in(s, fr(26, 3, true))  // verspaeteter Rahmenanfang von D (TL1) mitten in E: geht nicht hinaus, bricht E nicht ab
	b.in(s, fr(29, 4, false)) // Ende von E
	b.in(s, fr(21, 0, false)) // Nachsendung aus A: Versatz von vor B
	b.in(s, fr(25, 2, false)) // Nachsendung aus C: Versatz nach B
	b.in(s, fr(23, 1, false)) // Nachsendung aus B (ausgelassen): geht nicht hinaus
	b.in(s, fr(30, 5, true))  // F (TL1): ausgelassen
	b.in(s, fr(31, 5, false))
	// G (TL0, ein Paket, 32) fehlt zunaechst ganz
	b.in(s, fpkt(33, 7*3000, 107, 4, 1, true, false, true, 33)) // H (TL1): ausgelassen
	b.in(s, fpkt(34, 8*3000, 108, 5, 0, true, false, true, 34)) // I (TL0)
	b.in(s, fpkt(32, 6*3000, 106, 4, 0, true, false, true, 32)) // G kommt nach: zwischen zwei ausgelassenen Bildern
	// Eingang -> Ausgang: 20->20 21->21 [22 aus, 23 Luecke] 24->23 25->24 [26 Luecke] 27->26 28->27 29->28
	// [30,31 aus] 32->29 [33 aus] 34->30
	want := map[uint32]uint16{20: 20, 21: 21, 24: 23, 25: 24, 27: 26, 28: 27, 29: 28, 32: 29, 34: 30}
	// PictureID: B ausgelassen (-1), D nicht (sein Anfang kam zu spaet zum Auslassen), F ausgelassen (-2), H (-3)
	wantPic := map[uint32]uint16{20: 100, 21: 100, 24: 101, 25: 101, 27: 102, 28: 103, 29: 103, 32: 104, 34: 105}
	if len(b.w.hdrs) != len(want) {
		t.Fatalf("%d Pakete geschrieben, erwartet %d: %v", len(b.w.hdrs), len(want), b.seqs())
	}
	for i, h := range b.w.hdrs {
		q := ptag(b.w.pls[i])
		if h.SequenceNumber != want[q] {
			t.Fatalf("paket %d der quelle: folgenummer %d, erwartet %d (alle: %v)", q, h.SequenceNumber, want[q], b.seqs())
		}
		if v := parse(b.w.pls[i]); v.PicID != wantPic[q] {
			t.Fatalf("paket %d der quelle: PictureID %d, erwartet %d", q, v.PicID, wantPic[q])
		}
	}
	uniq(t, b.seqs())
	if b.leg.Stats.LateDropped.Load() != 0 || s.Late.Load() != 5 || b.leg.Stats.TIDSkipped.Load() != 3 {
		t.Fatalf("verworfen %d, spaet %d, ausgelassen %d", b.leg.Stats.LateDropped.Load(), s.Late.Load(), b.leg.Stats.TIDSkipped.Load())
	}
	// Schichtwechsel direkt nach einem Nachzuegler: PictureID und TL0PICIDX der neuen Schicht schliessen an das letzte
	// Bild in Reihe an, nicht an den Nachzuegler
	b.in(s, fpkt(35, 9*3000, 109, 5, 0, true, false, false, 35)) // J (TL0): 35, 36 - das Ende fehlt zunaechst
	b.in(s, fpkt(37, 10*3000, 110, 6, 0, true, false, true, 37)) // K (TL0)
	b.in(s, fpkt(36, 9*3000, 109, 5, 0, false, false, true, 36)) // Nachsendung aus J
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(hi)
	b.in(hi, fpkt(7000, 500000, 9000, 40, 0, true, true, true, 7000))
	n := len(b.w.hdrs)
	if n != len(want)+4 {
		t.Fatalf("%d Pakete geschrieben: %v", n, b.seqs())
	}
	k, late, key := parse(b.w.pls[n-3]), parse(b.w.pls[n-2]), parse(b.w.pls[n-1])
	if ptag(b.w.pls[n-3]) != 37 || ptag(b.w.pls[n-2]) != 36 || late.PicID != k.PicID-1 {
		t.Fatalf("Nachsendung aus J: PictureID %d, K traegt %d", late.PicID, k.PicID)
	}
	if key.PicID != k.PicID+1 || key.TL0 != k.TL0+1 || b.w.hdrs[n-1].SequenceNumber != b.w.hdrs[n-3].SequenceNumber+1 {
		t.Fatalf("neue Schicht: PictureID %d TL0 %d Folgenummer %d - letztes Bild in Reihe %d/%d/%d", key.PicID, key.TL0,
			b.w.hdrs[n-1].SequenceNumber, k.PicID, k.TL0, b.w.hdrs[n-3].SequenceNumber)
	}
}

// TestDoppelImAusgelassenenBild (frueher TestDoppelDerQuelleFolgen, zweiter Teil): Ein Doppel aus der Bildmitte, das
// eintrifft, waehrend ein Bild ausgelassen wird, zaehlt nicht als ausgelassenes Paket - der Versatz rueckt nicht eins
// zu weit. Ebenso waehrend des Verwerfens bis zum Schluesselbild (dropping); ein Nachzuegler von vor dem Verwerfen geht
// noch hinaus, ein verspaeteter Schluesselbild-Anfang beendet das Verwerfen nicht.
func TestDoppelImAusgelassenenBild(t *testing.T) {
	b := newDbench(t)
	c := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(c)
	b.o.SetMaxTID(0)
	b.in(c, fpkt(20, 0, 100, 1, 0, true, true, false, 20)) // Bild A (TL0), zwei Pakete
	b.in(c, fpkt(21, 0, 100, 1, 0, false, false, true, 21))
	b.in(c, fpkt(22, 3000, 101, 1, 1, true, false, false, 22)) // Bild B (TL1): wird ausgelassen
	b.in(c, fpkt(21, 0, 100, 1, 0, false, false, true, 21))    // Doppel aus Bild A
	b.in(c, fpkt(22, 3000, 101, 1, 1, true, false, false, 22)) // Doppel des Rahmenanfangs von B
	b.in(c, fpkt(23, 3000, 101, 1, 1, false, false, true, 23)) // Rest von Bild B
	b.in(c, fpkt(24, 6000, 102, 2, 0, true, false, true, 24))  // Bild C (TL0)
	if got := b.seqs(); !sameSeqs(got, 20, 21, 22) || c.Dups.Load() != 2 || b.leg.Stats.TIDSkipped.Load() != 1 {
		t.Fatalf("ausgelassenes Bild: %v, doppel %d, ausgelassen %d", got, c.Dups.Load(), b.leg.Stats.TIDSkipped.Load())
	}

	// Verwerfen bis zum Schluesselbild
	old := MaxQueueAge
	defer func() { MaxQueueAge = old }()
	b = newDbench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000, PLI: func(uint32) {}}
	b.o.Assign(s)
	b.in(s, vp8pkt(30, 0, true, true, false)) // Schluesselbild 30, 31
	b.in(s, vp8pkt(31, 0, false, false, true))
	b.in(s, vp8pkt(32, 3000, true, false, false)) // Bild 32, 33 - das Ende fehlt zunaechst
	MaxQueueAge = -1                              // jedes Paket gilt als zu alt: ab dem naechsten Rahmenanfang verwerfen
	b.in(s, vp8pkt(34, 6000, true, false, false))
	MaxQueueAge = old
	if !b.o.dropping {
		t.Fatal("kein Verwerfen")
	}
	b.in(s, vp8pkt(35, 6000, false, false, true))
	b.in(s, vp8pkt(31, 0, false, false, true))     // Doppel waehrend des Verwerfens
	b.in(s, vp8pkt(35, 6000, false, false, true))  // Doppel eines verworfenen Pakets
	b.in(s, vp8pkt(33, 3000, false, false, true))  // Nachsendung von vor dem Verwerfen: geht hinaus
	b.in(s, vp8pkt(38, 12000, false, false, true)) // Bild 36 fehlt ganz; Schluesselbild 37, 38: der Anfang fehlt - Rest verworfen
	b.in(s, vp8pkt(36, 9000, true, false, true))   // das ganze Bild kommt nach: waehrend des Verwerfens geht es nicht hinaus
	b.in(s, vp8pkt(37, 12000, true, true, false))  // der Schluesselbild-Anfang kommt nach: beendet das Verwerfen nicht
	if !b.o.dropping || !b.o.want.Load() {
		t.Fatal("verspaeteter Schluesselbild-Anfang hat das Verwerfen beendet")
	}
	b.in(s, vp8pkt(39, 15000, true, true, false)) // naechstes Schluesselbild 39, 40
	b.in(s, vp8pkt(40, 15000, false, false, true))
	got := b.seqs()
	// 30 31 32 | 33 (Nachsendung) | 34 35 38 verworfen, 36 und 37 Luecken | 39 -> 36, 40 -> 37
	if !sameSeqs(got, 30, 31, 32, 33, 36, 37) || s.Dups.Load() != 2 || s.Late.Load() != 3 || b.o.dropping {
		t.Fatalf("verwerfen: %v, doppel %d, spaet %d", got, s.Dups.Load(), s.Late.Load())
	}
}

// TestDoppeltesFuellpaket: Ein doppeltes Fuellpaket der Quelle zaehlt nur einmal (skip), ein verspaetetes gar nicht
// mehr - sonst rueckte alles Folgende auf eine schon vergebene Nummer. Ein Medienpaket, das nach einem Fuellpaket
// eintrifft, aber vor dessen Stelle gehoert, bekommt den Stand seiner Stelle. Ebenso beim Kontingent (Gate): Ein Doppel
// zaehlt weder Bytes noch als verworfenes Paket.
func TestDoppeltesFuellpaket(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	b.in(s, vp8pkt(10, 0, true, true, true))
	b.in(s, padpkt(11, 0))
	b.in(s, padpkt(11, 0)) // doppeltes Fuellpaket
	b.in(s, vp8pkt(12, 3000, true, false, true))
	if got := b.seqs(); !sameSeqs(got, 10, 11) || s.skip != 1 || s.Dups.Load() != 1 {
		t.Fatalf("doppeltes Fuellpaket: %v, skip %d, doppel %d", got, s.skip, s.Dups.Load())
	}
	b.in(s, vp8pkt(14, 6000, true, false, true)) // 13 fehlt zunaechst ...
	b.in(s, padpkt(13, 3000))                    // ... und war ein Fuellpaket: rueckt nichts mehr
	b.in(s, vp8pkt(15, 9000, true, false, true))
	if got := b.seqs(); !sameSeqs(got, 10, 11, 13, 14) || s.skip != 1 {
		t.Fatalf("verspaetetes Fuellpaket: %v, skip %d", got, s.skip)
	}
	b.in(s, padpkt(17, 9000))                     // 16 fehlt zunaechst, 17 ist ein Fuellpaket
	b.in(s, vp8pkt(18, 15000, true, false, true)) //
	b.in(s, vp8pkt(16, 12000, true, false, true)) // Nachsendung von vor dem Fuellpaket
	b.in(s, vp8pkt(19, 18000, true, false, true)) //
	if got := b.seqs(); !sameSeqs(got, 10, 11, 13, 14, 16, 15, 17) || s.skip != 2 {
		t.Fatalf("Nachsendung vor einem Fuellpaket: %v, skip %d", got, s.skip)
	}
	uniq(t, b.seqs())

	// Kontingent
	b = newDbench(t)
	allow, calls := true, 0
	g := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000, PLI: func(uint32) {}}
	g.Gate = func(int) bool { calls++; return allow }
	b.o.Assign(g)
	b.in(g, vp8pkt(50, 0, true, true, false))
	b.in(g, vp8pkt(51, 0, false, false, true))
	allow = false
	b.in(g, vp8pkt(52, 3000, true, false, false)) // ueber dem Kontingent: verworfen bis zum Schluesselbild
	b.in(g, vp8pkt(52, 3000, true, false, false)) // Doppel
	b.in(g, vp8pkt(54, 3000, false, false, true)) // 53 fehlt zunaechst
	b.in(g, vp8pkt(54, 3000, false, false, true)) // Doppel
	b.in(g, vp8pkt(51, 0, false, false, true))    // Doppel von vor dem Verwerfen
	allow = true
	b.in(g, vp8pkt(55, 6000, true, true, false))   // Schluesselbild: weiter
	b.in(g, vp8pkt(53, 3000, false, false, false)) // Nachsendung aus dem verworfenen Abschnitt: faellt weg
	b.in(g, vp8pkt(56, 6000, false, false, true))
	// 50 51 | 52 54 verworfen (skip 2), 53 Luecke | 55 -> 53, 56 -> 54
	if got := b.seqs(); !sameSeqs(got, 50, 51, 53, 54) || g.skip != 2 || g.Gated.Load() != 3 || g.Dups.Load() != 3 || calls != 5 {
		t.Fatalf("kontingent: %v, skip %d, verworfen %d, doppel %d, gezaehlt %d", got, g.skip, g.Gated.Load(), g.Dups.Load(), calls)
	}
}

// TestZuAltesPaket: Das Fenster der Quelle reicht dupWin Nummern zurueck. Ein Doppel am Rand faellt als Doppel weg,
// alles Aeltere wird verworfen und getrennt gezaehlt (TooOld) - auch ein erstes Exemplar. Eine Nachsendung geht
// hinaus, solange der Ausgang ihre Stelle noch kennt (lateWin), sonst zaehlt sie am Bein (LateDropped).
func TestZuAltesPaket(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	pk := func(q uint16) []byte { return vp8pkt(q, uint32(q)*3000, true, q == 1000, true) }
	n := 0
	for q := uint16(1000); q <= 3000; q++ {
		if q == 1900 || q == 2500 || q == 2900 {
			continue
		}
		b.in(s, pk(q))
		n++
	}
	b.in(s, pk(3000-dupWin+1)) // aeltestes Paket im Fenster: Doppel
	if s.Dups.Load() != 1 || s.TooOld.Load() != 0 {
		t.Fatalf("rand des fensters: doppel %d alt %d", s.Dups.Load(), s.TooOld.Load())
	}
	b.in(s, pk(3000-dupWin)) // eins weiter zurueck: ausserhalb
	b.in(s, pk(1900))        // erstes Exemplar, aber aelter als das Fenster
	if s.Dups.Load() != 1 || s.TooOld.Load() != 2 || len(b.w.hdrs) != n {
		t.Fatalf("ausserhalb des fensters: doppel %d alt %d, geschrieben %d von %d", s.Dups.Load(), s.TooOld.Load(), len(b.w.hdrs), n)
	}
	b.in(s, pk(2900)) // Nachsendung im Fenster der Quelle und im Ring des Ausgangs
	b.in(s, pk(3001))
	got := b.seqs()
	uniq(t, got)
	if len(got) != n+2 || got[n] != 2900 || got[n+1] != 3001 || s.Late.Load() != 1 {
		t.Fatalf("Nachsendung im Fenster: %v", got[n-2:])
	}
	// im Fenster der Quelle, aber aelter als der Ring des Ausgangs (lateWin): verworfen und am Bein gezaehlt
	b.in(s, pk(2500))
	if len(b.w.hdrs) != n+2 || s.Late.Load() != 2 || b.leg.Stats.LateDropped.Load() != 1 || s.TooOld.Load() != 2 {
		t.Fatalf("Nachsendung jenseits des Rings: geschrieben %d, spaet %d, verworfen %d", len(b.w.hdrs)-n, s.Late.Load(), b.leg.Stats.LateDropped.Load())
	}
}

// TestAbgleichTon: Beim Ton gilt dasselbe - Doppel fallen weg, eine Nachsendung bekommt ihre Nummer, der Wechsel des
// Sprechers schliesst an den Stand an.
func TestAbgleichTon(t *testing.T) {
	b := newDbench(t)
	x := &Source{Owner: "x", Layer: "a", Kind: Audio, Clock: 48000, CSRC: 1}
	y := &Source{Owner: "y", Layer: "a", Kind: Audio, Clock: 48000, CSRC: 2}
	b.a.Assign(x)
	for _, q := range []uint16{1, 2, 3, 3, 5, 2, 4, 6, 4} { // 3, 2 und 4 doppelt, 4 verspaetet
		b.in(x, audpkt(q, uint32(q)*960))
	}
	if got := hseqs(b.wa); !sameSeqs(got, 1, 2, 3, 5, 4, 6) || x.Dups.Load() != 3 || x.Late.Load() != 1 || b.a.lastSeq != 6 {
		t.Fatalf("ton: %v, doppel %d spaet %d", got, x.Dups.Load(), x.Late.Load())
	}
	b.a.Assign(y)
	b.in(y, audpkt(900, 555555))
	b.in(y, audpkt(900, 555555))
	b.in(x, audpkt(7, 7*960)) // nach dem Wechsel nicht mehr weitergeleitet
	b.in(y, audpkt(902, 555555+1920))
	b.in(y, audpkt(901, 555555+960)) // Nachsendung der neuen Quelle
	if got := hseqs(b.wa); !sameSeqs(got, 1, 2, 3, 5, 4, 6, 7, 9, 8) || y.Dups.Load() != 1 {
		t.Fatalf("ton nach dem wechsel: %v", got)
	}
	if h := b.wa.hdrs[8]; h.CSRC[0] != 2 || h.Timestamp-b.wa.hdrs[6].Timestamp != 960 {
		t.Fatalf("Nachsendung: csrc %v, zeitstempel-abstand %d", h.CSRC, h.Timestamp-b.wa.hdrs[6].Timestamp)
	}
}

// TestSpaetAelterAlsRing: Der Ausgang kennt die letzten lateWin-1 Stellen seiner Quelle. Ein Nachzuegler von davor wird
// verworfen und gezaehlt (LateDropped) statt auf eine vergebene Nummer zu geraten; am Rand passt er noch. Dazwischen
// laesst der Ausgang laufend Bilder aus - der Versatz aendert sich mit jedem.
func TestSpaetAelterAlsRing(t *testing.T) {
	b := newDbench(t)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	b.o.Assign(s)
	b.o.SetMaxTID(0)
	seq, k := uint16(10), 0
	one := func(tid uint8) { // ein Bild aus einem Paket
		b.in(s, fpkt(seq, uint32(k)*3000, uint16(100+k), uint8(k), tid, true, k == 0, true, uint32(seq)))
		seq++
		k++
	}
	one(0)
	one(0)
	gap1 := seq // fehlt zunaechst
	seq++
	k++
	for seq != gap1+lateWin-1 { // bis die Luecke lateWin-1 Stellen zurueckliegt: abwechselnd ausgelassen und weitergeleitet
		one(uint8(seq & 1))
	}
	one(0)
	n0 := len(b.w.hdrs)
	b.in(s, fpkt(gap1, 6000, 102, 2, 0, true, false, true, uint32(gap1)))
	if len(b.w.hdrs) != n0+1 || b.w.hdrs[n0].SequenceNumber != 12 || b.leg.Stats.LateDropped.Load() != 0 {
		t.Fatalf("Nachzuegler am Rand des Rings: %d geschrieben, verworfen %d", len(b.w.hdrs)-n0, b.leg.Stats.LateDropped.Load())
	}
	gap2 := seq // zweite Luecke: sie faellt aus dem Ring
	seq++
	k++
	for seq != gap2+lateWin {
		one(uint8(seq & 1))
	}
	one(0)
	n0 = len(b.w.hdrs)
	tot := Totals.LateDropped.Load()
	b.in(s, fpkt(gap2, 6000, 102, 2, 0, true, false, true, uint32(gap2)))
	if len(b.w.hdrs) != n0 || b.leg.Stats.LateDropped.Load() != 1 || Totals.LateDropped.Load()-tot != 1 {
		t.Fatalf("Nachzuegler jenseits des Rings: %d geschrieben, verworfen %d", len(b.w.hdrs)-n0, b.leg.Stats.LateDropped.Load())
	}
	one(0)
	uniq(t, b.seqs())
}

// TestAbgleichLangerLauf: Der Ausgang steht ueber mehr als einen Umlauf der Folgenummern auf derselben Quelle und
// laesst dabei laufend Bilder aus. Grenze und Historie bleiben in Reichweite der 16-Bit-Vergleiche: Nachzuegler gehen
// auch nach 150000 Paketen mit ihrer Nummer hinaus, keiner wird faelschlich verworfen.
func TestAbgleichLangerLauf(t *testing.T) {
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
	o := leg.NewOut("v", "p", Video, ClassSpeaker)
	w := &cntW{seen: map[uint16]uint32{}}
	o.Bind(w, 7, 96)
	s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	o.Assign(s)
	o.SetMaxTID(0)
	step := func(pkt []byte) {
		s.Inject(pkt)
		for {
			e, ok := leg.pop()
			if !ok {
				return
			}
			leg.process(e.p, e.o)
			e.p.release()
		}
	}
	const n = 150000
	late, want := 0, 0
	var held []byte
	heldAt := 0
	for i := 0; i < n; i++ {
		tid := uint8(0)
		if i%4 == 3 { // jedes vierte Bild wird ausgelassen
			tid = 1
		}
		pkt := fpkt(uint16(i), uint32(i)*3000, uint16(i)&0x7fff, uint8(i/4), tid, true, i == 0, true, uint32(i))
		if i%97 == 50 && tid == 0 && i+20 < n { // dieses Paket kommt 20 Pakete spaeter
			held, heldAt = pkt, i+20
			continue
		}
		step(pkt)
		if tid == 0 {
			want++
		}
		if held != nil && i == heldAt {
			step(held)
			held = nil
			late++
			want++
		}
	}
	if w.dup != 0 {
		t.Fatalf("%d Folgenummern zweimal vergeben", w.dup)
	}
	if leg.Stats.LateDropped.Load() != 0 || int(s.Late.Load()) != late || late < 1000 || s.Dups.Load() != 0 || s.TooOld.Load() != 0 {
		t.Fatalf("spaet %d von %d, verworfen %d, doppel %d, alt %d", s.Late.Load(), late, leg.Stats.LateDropped.Load(), s.Dups.Load(), s.TooOld.Load())
	}
	if w.n != want {
		t.Fatalf("%d Pakete geschrieben, erwartet %d", w.n, want)
	}
}

// cntW zaehlt geschriebene Pakete und merkt, ob eine Folgenummer binnen 30000 Paketen zweimal vergeben wurde.
type cntW struct {
	seen map[uint16]uint32
	n    int
	dup  int
}

func (w *cntW) WriteRTP(h *rtp.Header, p []byte) (int, error) {
	w.n++
	if at, ok := w.seen[h.SequenceNumber]; ok && uint32(w.n)-at < 30000 {
		w.dup++
	}
	w.seen[h.SequenceNumber] = uint32(w.n)
	return len(p), nil
}

// TestAbgleichZufall: zufaellige Folge mit Verlust, Nachsendungen, Umsortierung, Wiederholungen, Fuellpaketen der
// Quelle und wechselnder Zeitschicht-Grenze - gegen denselben Lauf ohne die Wiederholungen und ohne die Nachzuegler
// (Pakete, an deren Stelle der Ausgang bei ihrer Ankunft schon vorbei ist). Geprueft wird: Kein Paket der Quelle geht
// zweimal hinaus, keine Folgenummer wird zweimal vergeben, alle uebrigen Pakete tragen exakt Folgenummer und PictureID
// des Vergleichslaufs (Doppel und Nachzuegler verschieben nichts und aendern keinen Zustand), ein Nachzuegler teilt das
// Los seines Bildes, wenn dessen Anfang in Reihe kam, und liegt, wenn er hinausgeht, mit Folgenummer und PictureID genau
// an seiner Stelle im Bild.
func TestAbgleichZufall(t *testing.T) {
	old := MaxQueueAge
	MaxQueueAge = time.Hour
	defer func() { MaxQueueAge = old }()
	late := 0
	for seed := int64(1); seed <= 60; seed++ {
		late += zufallLauf(t, seed, nil)
	}
	if late < 1000 {
		t.Fatalf("nur %d Nachzuegler weitergeleitet - der Test prueft zu wenig", late)
	}
}

type zpkt struct {
	seq   uint16
	frame int
	pad   bool
	raw   []byte
	lost  bool // trifft nie ein
	delay int  // trifft so viele Stellen spaeter ein (0 = in Reihe)
	tidAt int  // > 0: vor diesem Paket die Zeitschicht-Grenze auf tidAt-1 stellen
}

// zufallLauf: ein Lauf von TestAbgleichZufall. nach (falls gesetzt) wird in beiden Laeufen nach denselben Paketen
// aufgerufen - nach jedem, das auch im Vergleichslauf vorkommt - und darf dort am Ausgang eingreifen (im Branch mit
// Fuellpaketen des Knotens: ein Fuell-Schub).
func zufallLauf(t *testing.T, seed int64, nach func(leg *Leg, o *Out, i int)) (lateOut int) {
	rng := rand.New(rand.NewSource(seed))
	base := uint16(rng.Intn(65536))
	if seed%3 == 0 {
		base = uint16(65536 - 200 - rng.Intn(300)) // Umlauf mitten im Lauf
	}
	// Quelle: Bilder zu 1 bis 4 Paketen, Zeitschichten 0,2,1,2, gelegentlich Fuellpakete zwischen zwei Bildern
	var pk []zpkt
	pat := []uint8{0, 2, 1, 2}
	seq := base
	nframes := 400
	first := make([]int, nframes) // Index des ersten Pakets je Bild
	for f := 0; f < nframes; f++ {
		tid := pat[f%4]
		n := 1 + rng.Intn(4)
		tidAt := 0
		if rng.Intn(25) == 0 {
			tidAt = 1 + []int{0, 1, 3}[rng.Intn(3)]
		}
		first[f] = len(pk)
		for i := 0; i < n; i++ {
			raw := fpkt(seq, uint32(f)*3000, uint16(1000+f), uint8(f/4), tid, i == 0, f%100 == 0, i == n-1, uint32(len(pk)))
			z := zpkt{seq: seq, frame: f, raw: raw}
			if i == 0 {
				z.tidAt = tidAt
			}
			pk = append(pk, z)
			seq++
		}
		if rng.Intn(10) == 0 {
			for i := 1 + rng.Intn(3); i > 0; i-- {
				pk = append(pk, zpkt{seq: seq, frame: -1, pad: true, raw: padpkt(seq, uint32(f)*3000)})
				seq++
			}
		}
	}
	for i := range pk {
		if i == 0 {
			continue // das erste Schluesselbild-Paket kommt immer in Reihe
		}
		switch r := rng.Intn(100); {
		case r < 3:
			pk[i].lost = true
		case r < 10:
			pk[i].delay = 1 + rng.Intn(40)
		}
	}
	// Ankunftsfolge: Index in pk; negativ = Zeitschicht-Grenze stellen
	at := map[int][]int{} // nach dem Paket mit diesem Index treffen diese ein
	for i := range pk {
		if pk[i].lost {
			continue
		}
		if d := pk[i].delay; d > 0 {
			j := min(i+d, len(pk)-1)
			at[j] = append(at[j], i)
		}
		for rng.Intn(100) < 15 { // Wiederholung irgendwann danach, auch mehrfach
			j := min(i+pk[i].delay+rng.Intn(300), len(pk)-1)
			at[j] = append(at[j], i)
		}
	}
	var dis []int
	for i := range pk {
		if pk[i].tidAt > 0 {
			dis = append(dis, -pk[i].tidAt)
		}
		if !pk[i].lost && pk[i].delay == 0 {
			dis = append(dis, i)
		}
		dis = append(dis, at[i]...)
	}
	// Vergleichslauf: ohne Wiederholungen und ohne Nachzuegler. Nachzuegler ist ein Bildpaket, wenn vor ihm schon ein
	// Bildpaket mit hoeherem Index eingetroffen ist (der Ausgang ist dann an seiner Stelle vorbei).
	var ref []int
	seen := map[int]bool{}
	lateAt := map[int]bool{}
	dups, top := 0, -1
	for _, i := range dis {
		switch {
		case i < 0:
			ref = append(ref, i)
		case seen[i]:
			dups++
		case pk[i].pad:
			seen[i] = true
			ref = append(ref, i)
		case i < top:
			seen[i] = true
			lateAt[i] = true
		default:
			seen[i] = true
			top = i
			ref = append(ref, i)
		}
	}
	type res struct {
		seq, pic uint16
		n        int
	}
	run := func(order []int) (map[int]res, *Source, *Leg, []uint16) {
		leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
		o := leg.NewOut("v", "p", Video, ClassSpeaker)
		w := &hrec{}
		o.Bind(w, 7, 96)
		s := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
		o.Assign(s)
		done := map[int]bool{}
		for _, i := range order {
			if i < 0 {
				o.SetMaxTID(uint8(-i - 1))
				continue
			}
			s.Inject(pk[i].raw)
			for {
				e, ok := leg.pop()
				if !ok {
					break
				}
				leg.process(e.p, e.o)
				e.p.release()
			}
			if nach != nil && !done[i] && !lateAt[i] {
				nach(leg, o, i)
			}
			done[i] = true
		}
		out := map[int]res{}
		for j, h := range w.hdrs {
			if h.Padding { // Fuellpaket des Knotens (nach)
				continue
			}
			i := int(ptag(w.pls[j]))
			r := out[i]
			r.seq, r.pic = h.SequenceNumber, parse(w.pls[j]).PicID
			r.n++
			out[i] = r
		}
		return out, s, leg, hseqs(w)
	}
	want, _, _, _ := run(ref)
	got, s, leg, seqs := run(dis)
	uniq(t, seqs)
	if int(s.Dups.Load()) != dups || s.TooOld.Load() != 0 {
		t.Fatalf("seed %d: doppel %d (erwartet %d), alt %d", seed, s.Dups.Load(), dups, s.TooOld.Load())
	}
	if n := leg.Stats.LateDropped.Load(); n != 0 {
		t.Fatalf("seed %d: %d Nachzuegler verworfen", seed, n)
	}
	picOf := map[uint16]int{} // PictureID am Ausgang -> Bild
	head := map[int]int{}     // erstes hinausgegangenes Paket je Bild (Index)
	for i := range pk {
		r, ok := got[i]
		if ok && r.n != 1 {
			t.Fatalf("seed %d: Paket %d ging %d-mal hinaus", seed, i, r.n)
		}
		f := pk[i].frame
		switch {
		case pk[i].pad || pk[i].lost:
			if ok {
				t.Fatalf("seed %d: Fuellpaket bzw. verlorenes Paket %d weitergeleitet", seed, i)
			}
			continue
		case !lateAt[i]: // exakt wie im Vergleichslauf
			wr, wok := want[i]
			if ok != wok || r.seq != wr.seq || r.pic != wr.pic {
				t.Fatalf("seed %d: Paket %d (Bild %d): %v/%d/%d, Vergleichslauf %v/%d/%d", seed, i, f, ok, r.seq, r.pic, wok, wr.seq, wr.pic)
			}
		default: // Nachzuegler: Kam der Anfang seines Bildes in Reihe, teilt er dessen Los
			if st := first[f]; st != i && seen[st] && !lateAt[st] {
				if _, sok := want[st]; ok != sok {
					t.Fatalf("seed %d: Nachzuegler %d (Bild %d): weitergeleitet %v, der Anfang seines Bildes %v", seed, i, f, ok, sok)
				}
			}
			if ok {
				lateOut++
			}
		}
		if !ok {
			continue
		}
		if g, have := picOf[r.pic]; have && g != f {
			t.Fatalf("seed %d: PictureID %d fuer Bild %d und Bild %d", seed, r.pic, g, f)
		}
		picOf[r.pic] = f
		if j, have := head[f]; !have {
			head[f] = i
		} else if r.seq-got[j].seq != pk[i].seq-pk[j].seq {
			// innerhalb eines Bildes liegt jedes Paket genau an seiner Stelle
			t.Fatalf("seed %d: Bild %d: Paket %d traegt %d, Paket %d traegt %d (Abstand der Quelle %d)", seed, f, j, got[j].seq, i, r.seq, pk[i].seq-pk[j].seq)
		}
	}
	return lateOut
}
