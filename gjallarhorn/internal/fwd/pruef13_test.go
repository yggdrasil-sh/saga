package fwd

import (
	"encoding/binary"
	"math/rand"
	"testing"
	"time"
)

// Pruefung der Nacharbeit zum Abgleich (Rolle pruef-bwe13): eigene Gegentests zu den Funden F1 bis F4.
//
//   - Der Ausgang fuehrt die hoechste je geschriebene Folgenummer (hiSeq) in jedem Schreibweg richtig mit - in Reihe,
//     Nachzuegler, Rohweg; ein Schichtwechsel setzt genau dahinter an und nie auf eine schon vergebene Nummer.
//   - PictureID (auch mit 7 Bit) und Zeitbasis (auch ueber den Umlauf des Zeitstempels) laufen am Wechsel nie rueckwaerts.
//   - Schwellen des Abgleichs der Quelle (Source.admit): Zeitstempel-Regeln tsJump und tsOld von beiden Seiten, Ton
//     ueber den Umlauf des Zeitstempels.

// p13pkt baut ein VP8-Paket wie fpkt, wahlweise mit 7-Bit-PictureID.
func p13pkt(seq uint16, ts uint32, pic uint16, long bool, tl0, tid uint8, start, key, marker bool, tag uint32) []byte {
	if long {
		return fpkt(seq, ts, pic&0x7fff, tl0, tid, start, key, marker, tag)
	}
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
	b = append(b, d, 0xe0, byte(pic)&0x7f, tl0, tid<<6, pl)
	return binary.BigEndian.AppendUint32(b, tag)
}

// p13out: ein geschriebenes Paket, gleich auf welchem Weg.
type p13out struct {
	seq uint16
	ts  uint32
	tag uint32
	pic uint16
	pad bool // Fuellpaket des Knotens (nur im Branch mit Proben)
}

// p13raw zeichnet den Rohweg (Kaskade) auf.
type p13raw struct{ out []p13out }

func (r *p13raw) WriteRaw(b []byte) error {
	r.out = append(r.out, p13out{seq: binary.BigEndian.Uint16(b[2:]), ts: binary.BigEndian.Uint32(b[4:]), tag: binary.BigEndian.Uint32(b[len(b)-4:])})
	return nil
}

// p13Lauf: zwei Schichten einer Quelle mit Verlust, Nachsendungen, Umsortierung, Wiederholungen und Fuellpaketen der
// Quelle; der Ausgang wechselt zufaellig (am naechsten Schluesselbild), aendert die Zeitschicht-Grenze und verwirft
// zeitweise bis zum Schluesselbild; eine Schicht beginnt ihre Folge einmal neu. nach (falls gesetzt) darf nach jedem
// Paket am Ausgang eingreifen (Branch mit Proben: Fuell-Schub).
//
// Geprueft wird nach JEDEM Paket der Quelle: Der Stand des Ausgangs (hiSeq) ist die hoechste bisher geschriebene
// Folgenummer; keine Nummer wird zweimal vergeben; das erste Paket nach einem Wechsel der Schicht traegt genau die
// hoechste bisher geschriebene Nummer + 1, eine noch nicht vergebene PictureID und (ausser auf dem Rohweg, der den
// Zeitstempel der Quelle laesst) einen juengeren Zeitstempel als alles Geschriebene.
func p13Lauf(t *testing.T, seed int64, raw, long bool, nach func(leg *Leg, o *Out, step int)) (switches, lateOut, pads int) {
	t.Helper()
	old := MaxQueueAge
	defer func() { MaxQueueAge = old }()
	MaxQueueAge = time.Hour
	rng := rand.New(rand.NewSource(seed))
	leg := &Leg{wake: make(chan struct{}, 1), done: make(chan struct{})}
	o := leg.NewOut("v", "p", Video, ClassSpeaker)
	w, rw := &hrec{}, &p13raw{}
	if raw {
		o.BindRaw(rw)
	} else {
		o.Bind(w, 7, 96)
	}
	var src [2]*Source
	var arr [2]map[int][][]byte
	steps := 0
	for k := 0; k < 2; k++ {
		src[k] = &Source{Owner: "p", Layer: []string{"l", "h"}[k], Kind: Video, Clock: 90000, PLI: func(uint32) {}}
		arr[k] = map[int][][]byte{}
		seq := uint16(rng.Intn(65536))
		tsBase := rng.Uint32() // je Schicht eine eigene Zeitbasis, auch nahe am Umlauf
		if seed%4 == 0 {
			tsBase = uint32(0) - uint32(rng.Intn(600000))
		}
		picBase := uint16(rng.Intn(0x8000))
		restart, restartStep := -1, 0
		if k == int(seed%2) {
			restart = 120 + rng.Intn(150)
		}
		type item struct {
			at, f int
			raw   []byte
		}
		var items []item
		step := 0
		for f := 0; f < 400; f++ {
			if f == restart {
				seq -= uint16(2000 + rng.Intn(20000))
				restartStep = step + 1
			}
			tid := []uint8{0, 2, 1, 2}[f%4]
			key := f%20 == 0
			n := 1 + rng.Intn(3)
			for i := 0; i < n; i++ {
				tag := uint32(k)<<28 | uint32(f)<<8 | uint32(i)
				pkt := p13pkt(seq, tsBase+uint32(f)*3000, picBase+uint16(f), long, uint8(f/4), tid, i == 0, key, i == n-1, tag)
				seq++
				step++
				at := step
				switch r := rng.Intn(100); {
				case key && i == 0: // Schluesselbild-Anfaenge kommen in Reihe (sonst gibt es keinen Wechsel)
				case r < 3:
					continue // verloren
				case r < 11:
					at += 1 + rng.Intn(30) // Nachsendung bzw. Umsortierung
				}
				items = append(items, item{at, f, pkt})
				for rng.Intn(100) < 10 { // Wiederholung
					items = append(items, item{at + rng.Intn(150), f, pkt})
				}
			}
			if rng.Intn(12) == 0 {
				items = append(items, item{step, f, padpkt(seq, tsBase+uint32(f)*3000)})
				seq++
			}
		}
		for _, it := range items {
			if restart >= 0 && it.f < restart && it.at >= restartStep {
				it.at = restartStep - 1 // vor dem Neubeginn gesendet: bis dahin eingetroffen
			}
			arr[k][it.at] = append(arr[k][it.at], it.raw)
		}
		steps = max(steps, step+200)
	}

	var (
		used    = map[uint16]int{}    // Folgenummer -> Index des Pakets
		picOf   = map[uint16]uint32{} // PictureID am Ausgang -> Bild
		picLast []uint16              // zuletzt vergebene PictureIDs (7 Bit: nur im Fenster eindeutig)
		tags    = map[uint32]bool{}
		maxSeq  uint16
		maxTS   uint32
		have    bool
		prevSrc = uint32(99)
		seen    int // schon gepruefte Ausgaben
		n       int
	)
	outs := func() []p13out {
		if raw {
			return rw.out
		}
		res := make([]p13out, 0, len(w.hdrs)-seen)
		for j := seen; j < len(w.hdrs); j++ {
			h := w.hdrs[j]
			x := p13out{seq: h.SequenceNumber, ts: h.Timestamp, pad: h.Padding}
			if !h.Padding {
				x.tag, x.pic = ptag(w.pls[j]), parse(w.pls[j]).PicID
			}
			res = append(res, x)
		}
		return res
	}
	check := func(st int) {
		list := outs()
		if raw {
			list = list[seen:]
		}
		for _, x := range list {
			n++
			if j, dup := used[x.seq]; dup {
				t.Fatalf("seed %d raw=%v long=%v Schritt %d: Folgenummer %d zweimal vergeben (Paket %d und %d)", seed, raw, long, st, x.seq, j, n)
			}
			used[x.seq] = n
			if x.pad {
				pads++
				if have && int16(x.seq-maxSeq) <= 0 { // hinter allem Geschriebenen (und hinter wartenden Luecken)
					t.Fatalf("seed %d Schritt %d: Fuellpaket auf %d, hoechste geschriebene Nummer %d", seed, st, x.seq, maxSeq)
				}
				maxSeq, have = x.seq, true
				continue
			}
			if tags[x.tag] {
				t.Fatalf("seed %d raw=%v Schritt %d: Paket %#x ging zweimal hinaus", seed, raw, st, x.tag)
			}
			tags[x.tag] = true
			k, bild := x.tag>>28, x.tag>>8
			if k != prevSrc && have { // Wechsel der Schicht: das Schluesselbild der neuen
				switches++
				if x.seq != maxSeq+1 {
					t.Fatalf("seed %d raw=%v long=%v Schritt %d: nach dem Wechsel Nummer %d, hoechste geschriebene %d", seed, raw, long, st, x.seq, maxSeq)
				}
				if !raw && int32(x.ts-maxTS) <= 0 {
					t.Fatalf("seed %d long=%v Schritt %d: nach dem Wechsel Zeitstempel %d, juengster geschriebener %d", seed, long, st, x.ts, maxTS)
				}
			}
			prevSrc = k
			if !raw {
				if long {
					if g, ok := picOf[x.pic]; ok && g != bild {
						t.Fatalf("seed %d Schritt %d: PictureID %d fuer Bild %#x und Bild %#x", seed, st, x.pic, g, bild)
					}
					picOf[x.pic] = bild
				} else { // 7 Bit: eindeutig unter den letzten 40 Bildern
					if g, ok := picOf[x.pic]; ok && g != bild {
						for _, q := range picLast {
							if q == x.pic {
								t.Fatalf("seed %d Schritt %d: PictureID %d (7 Bit) fuer Bild %#x und kurz davor Bild %#x", seed, st, x.pic, bild, g)
							}
						}
					}
					if g, ok := picOf[x.pic]; !ok || g != bild {
						picLast = append(picLast, x.pic)
						if len(picLast) > 40 {
							picLast = picLast[1:]
						}
					}
					picOf[x.pic] = bild
				}
				if !have || int32(x.ts-maxTS) > 0 {
					maxTS = x.ts
				}
			}
			if !have || int16(x.seq-maxSeq) > 0 {
				maxSeq = x.seq
			} else {
				lateOut++
			}
			have = true
		}
		if raw {
			seen = len(rw.out)
		} else {
			seen = len(w.hdrs)
		}
		if have && o.hiSeq != maxSeq {
			t.Fatalf("seed %d raw=%v long=%v Schritt %d: Stand des Ausgangs hiSeq %d, hoechste geschriebene Nummer %d", seed, raw, long, st, o.hiSeq, maxSeq)
		}
	}
	o.Assign(src[0])
	cur := 0
	for st := 1; st <= steps; st++ {
		switch r := rng.Intn(400); {
		case r < 8:
			cur = 1 - cur
			o.Assign(src[cur])
		case r < 14:
			o.SetMaxTID(uint8([]int{0, 1, 3}[rng.Intn(3)]))
		case r < 17:
			MaxQueueAge = -1 // ab dem naechsten Rahmenanfang verwerfen bis zum Schluesselbild
		}
		for k := 0; k < 2; k++ {
			for _, pkt := range arr[k][st] {
				src[k].Inject(pkt)
				for {
					e, ok := leg.pop()
					if !ok {
						break
					}
					leg.process(e.p, e.o)
					e.p.release()
				}
				if o.dropping {
					MaxQueueAge = time.Hour
				}
				check(st)
				if nach != nil {
					nach(leg, o, st)
					check(st)
				}
			}
		}
	}
	return
}

// TestPruef13HoechsteNummer: Zufallslaeufe ueber den gewoehnlichen Schreibweg (15- und 7-Bit-PictureID) und den Rohweg.
func TestPruef13HoechsteNummer(t *testing.T) {
	for _, c := range []struct {
		name      string
		raw, long bool
	}{{"rtp-15bit", false, true}, {"rtp-7bit", false, false}, {"roh", true, true}} {
		sw, late := 0, 0
		for seed := int64(1); seed <= 80; seed++ {
			s, l, _ := p13Lauf(t, seed, c.raw, c.long, nil)
			sw, late = sw+s, late+l
		}
		t.Logf("%s: %d Wechsel, %d Nachzuegler hinaus", c.name, sw, late)
		if sw < 300 || late < 1500 {
			t.Fatalf("%s: nur %d Wechsel und %d Nachzuegler - der Test prueft zu wenig", c.name, sw, late)
		}
	}
}

// TestPruef13ZeitbasisUmlauf (F4): Die Zeitbasis nach einem Wechsel liegt hinter dem juengsten geschriebenen
// Zeitstempel - auch wenn der Nachzuegler ueber den Umlauf des Zeitstempels hinweg liegt; ohne Nachzuegler bleibt es
// bei "letztes Paket in Reihe + vergangene Zeit".
func TestPruef13ZeitbasisUmlauf(t *testing.T) {
	t0 := uint32(0xffffff00)
	b := newDbench(t)
	lo := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(lo)
	b.o.SetMaxTID(0)
	b.in(lo, fpkt(20, t0, 100, 1, 0, true, true, true, 20))       // Bild A
	b.in(lo, fpkt(22, t0+6000, 102, 2, 1, true, false, true, 22)) // Bild C (TL1): ausgelassen
	b.in(lo, fpkt(21, t0+3000, 101, 2, 0, true, false, true, 21)) // Bild B kommt nach - Zeitstempel hinter dem Umlauf
	b.o.Assign(hi)
	b.in(hi, fpkt(700, 123456, 400, 9, 0, true, true, true, 700))
	b.in(hi, fpkt(701, 123456+3000, 401, 10, 0, true, false, true, 701))
	ts := htss(b.w)
	if len(ts) != 4 || ts[0] != t0 || ts[1] != t0+3000 || int32(ts[2]-ts[1]) <= 0 || ts[2]-ts[1] > 9000 || ts[3]-ts[2] != 3000 {
		t.Fatalf("Zeitstempel ueber den Umlauf: %v", ts)
	}
	uniq(t, b.seqs())
	// ohne Nachzuegler: wie bisher letztes Paket in Reihe + vergangene Zeit (hier wenige Takte)
	b = newDbench(t)
	lo = &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	hi = &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(lo)
	b.in(lo, fpkt(20, t0, 100, 1, 0, true, true, true, 20))
	b.o.Assign(hi)
	b.in(hi, fpkt(700, 123456, 400, 9, 0, true, true, true, 700))
	ts = htss(b.w)
	if len(ts) != 2 || ts[1]-ts[0] < 1 || ts[1]-ts[0] > 9000 {
		t.Fatalf("Zeitstempel ohne Nachzuegler: %v", ts)
	}
}

// TestPruef13TL0NachNachzuegler haelt fest, was am Wechsel mit TL0PICIDX geschieht, wenn direkt davor ein ganzes
// TL0-Bild nachgekommen ist: Folgenummer, PictureID und Zeitstempel des Schluesselbilds liegen hinter dem Nachzuegler;
// TL0PICIDX zaehlt vom letzten Bild in Reihe weiter und kann der des Nachzueglers gleichen (am Schluesselbild setzt
// der Empfaenger die Buchfuehrung dieser Stufe neu).
func TestPruef13TL0NachNachzuegler(t *testing.T) {
	b := newDbench(t)
	lo := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "h", Kind: Video, Clock: 90000}
	b.o.Assign(lo)
	b.o.SetMaxTID(0)
	b.in(lo, fpkt(20, 0, 100, 5, 0, true, true, true, 20))     // A (TL0 5)
	b.in(lo, fpkt(22, 6000, 102, 6, 1, true, false, true, 22)) // C (TL1 zu TL0 6): ausgelassen
	b.in(lo, fpkt(21, 3000, 101, 6, 0, true, false, true, 21)) // B (TL0 6) kommt nach
	b.o.Assign(hi)
	b.in(hi, fpkt(700, 9000, 400, 40, 0, true, true, true, 700))
	if n := len(b.w.pls); n != 3 {
		t.Fatalf("%d Pakete geschrieben", n)
	}
	late, key := parse(b.w.pls[1]), parse(b.w.pls[2])
	t.Logf("Nachzuegler: PictureID %d TL0PICIDX %d - Schluesselbild danach: PictureID %d TL0PICIDX %d", late.PicID, late.TL0, key.PicID, key.TL0)
	if key.PicID != late.PicID+1 || b.w.hdrs[2].SequenceNumber != b.w.hdrs[1].SequenceNumber+1 || int32(b.w.hdrs[2].Timestamp-b.w.hdrs[1].Timestamp) <= 0 {
		t.Fatalf("Schluesselbild nicht hinter dem Nachzuegler: PictureID %d/%d, Nummern %v, Zeitstempel %v", late.PicID, key.PicID, b.seqs(), htss(b.w))
	}
	if key.TL0 != 6 || late.TL0 != 6 {
		t.Fatalf("TL0PICIDX: Nachzuegler %d, Schluesselbild %d (erwartet beide 6: letztes Bild in Reihe + 1)", late.TL0, key.TL0)
	}
}

// TestPruef13AbgleichSchwellen: Urteile von Source.admit an den Grenzen der beiden Zeitstempel-Regeln und ueber den
// Umlauf des Zeitstempels (Ton, 48 kHz).
func TestPruef13AbgleichSchwellen(t *testing.T) {
	// neu: Quelle mit n Paketen in Reihe ab seq/ts (Abstand dts), ohne die Nummern in miss
	neu := func(seq uint16, ts, dts uint32, n int, miss ...int) *Source {
		s := &Source{}
	next:
		for i := 0; i < n; i++ {
			for _, m := range miss {
				if m == i {
					continue next
				}
			}
			if v := s.admit(seq+uint16(i), ts+uint32(i)*dts); v != seqNew {
				t.Fatalf("Paket %d in Reihe: Urteil %d", i, v)
			}
		}
		return s
	}
	// 1. Ton ueber den Umlauf des Zeitstempels: Nachzuegler und Doppel von vor und nach dem Umlauf bleiben, was sie sind
	var null uint32
	t0 := null - 100*960 // der Zeitstempel laeuft nach 100 Paketen um
	s := neu(1000, t0, 960, 300, 90, 110)
	for _, c := range []struct{ i, want int }{{90, seqLate}, {90, seqDup}, {110, seqLate}, {110, seqDup}, {95, seqDup}, {105, seqDup}, {299, seqDup}} {
		if v := s.admit(uint16(1000+c.i), t0+uint32(c.i)*960); v != c.want || s.hi != 1299 {
			t.Fatalf("Ton ueber den Umlauf, Paket %d: Urteil %d (erwartet %d), Stand %d", c.i, v, c.want, s.hi)
		}
	}
	// 2. tsJump: hinter dem Stand im Fenster - genau an der Schwelle noch Nachzuegler bzw. Doppel, einen Takt darueber
	// neue Folge (auch ueber den Umlauf des Zeitstempels)
	for _, base := range []uint32{1_000_000, null - 500_000} {
		hiTS := base + 99*3000
		s = neu(5000, base, 3000, 100, 50)
		if v := s.admit(5050, hiTS+tsJump); v != seqLate || s.hi != 5099 {
			t.Fatalf("tsJump an der Schwelle (fehlend): Urteil %d, Stand %d", v, s.hi)
		}
		if v := s.admit(5060, hiTS+tsJump); v != seqDup || s.hi != 5099 {
			t.Fatalf("tsJump an der Schwelle (gesehen): Urteil %d, Stand %d", v, s.hi)
		}
		if v := s.admit(5060, hiTS+tsJump+1); v != seqNew || s.hi != 5060 {
			t.Fatalf("tsJump ueber der Schwelle: Urteil %d, Stand %d", v, s.hi)
		}
		if v := s.admit(5061, hiTS+tsJump+3001); v != seqNew || s.hi != 5061 { // die neue Folge laeuft weiter
			t.Fatalf("nach dem Neubeginn: Urteil %d, Stand %d", v, s.hi)
		}
		if v := s.admit(5060, hiTS+tsJump+1); v != seqDup {
			t.Fatalf("Doppel in der neuen Folge: Urteil %d", v)
		}
	}
	// 3. tsOld: aelter als das Fenster, hinter dem Stand - knapp unter der Schwelle alte Nachsendungen (auch drei in
	// Reihe sind kein Neubeginn), an der Schwelle gilt die Regel "zweites Paket in Reihe"
	hiTS := uint32(50_000_000 + 2999*600)
	s = neu(100, 50_000_000, 600, 3000)
	for i := uint16(0); i < 3; i++ {
		if v := s.admit(500+i, hiTS-tsOld+1); v != seqOld || s.hi != 3099 {
			t.Fatalf("alte Nachsendung %d knapp unter tsOld: Urteil %d, Stand %d", i, v, s.hi)
		}
	}
	if v := s.admit(600, hiTS-tsOld); v != seqOld || s.hi != 3099 {
		t.Fatalf("erstes Paket an der Schwelle tsOld: Urteil %d, Stand %d", v, s.hi)
	}
	if v := s.admit(601, hiTS-tsOld); v != seqNew || s.hi != 601 {
		t.Fatalf("zweites Paket in Reihe an der Schwelle tsOld: Urteil %d, Stand %d", v, s.hi)
	}
	// 4. fremde Zeitbasis VORAUS (Zeitstempel aelter): ab dem zweiten Paket in Reihe; ein Sprung voraus mit juengerem
	// Zeitstempel sofort
	s = neu(100, 50_000_000, 3000, 200)
	if v := s.admit(5299, 10_000); v != seqOld {
		t.Fatalf("fremde Zeitbasis voraus, erstes Paket: Urteil %d", v)
	}
	if v := s.admit(5300, 13_000); v != seqNew || s.hi != 5300 {
		t.Fatalf("fremde Zeitbasis voraus, zweites Paket: Urteil %d, Stand %d", v, s.hi)
	}
	s = neu(100, 50_000_000, 3000, 200)
	if v := s.admit(100+199+1024, 50_000_000+199*3000+1); v != seqNew || s.hi != 100+199+1024 {
		t.Fatalf("Sprung voraus mit juengerem Zeitstempel: Urteil %d, Stand %d", v, s.hi)
	}
	// 5. dazwischen bleibt eine alte Nachsendung ohne Wirkung auf eine scharfe Regel: fremde Folge, alte Nachsendung,
	// zweites Paket der fremden Folge
	s = neu(100, 50_000_000, 600, 3000)
	hiTS = 50_000_000 + 2999*600
	if v := s.admit(20000, 1000); v != seqOld {
		t.Fatalf("fremde Folge, erstes Paket: Urteil %d", v)
	}
	if v := s.admit(900, hiTS-600_000); v != seqOld {
		t.Fatalf("alte Nachsendung dazwischen: Urteil %d", v)
	}
	if v := s.admit(20001, 4000); v != seqNew || s.hi != 20001 {
		t.Fatalf("fremde Folge, zweites Paket: Urteil %d, Stand %d", v, s.hi)
	}
}
