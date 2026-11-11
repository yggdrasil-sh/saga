package sfu

import "time"

// Hysterese fuer flatternde Simulcast-Schichten. Senkt ein Sender seine Schichten selbst (libwebrtc passt bei CPU-Last
// bzw. Waerme die Simulcast-Schichten an), liefert er eine obere Schicht oft nur noch zeitweise. Ohne Hysterese schaltet
// jeder Ausgang dann im Sekundentakt zwischen den Schichten hin und her; jeder Wechsel wartet auf ein Schluesselbild,
// die PLI dafuer frisst bei kleinen Raten die ganze Rate (Feldbefund: m3<->l3 im 1:1-Anruf ueber eine Bruecke, 1-4 fps).
//
// Regel (je Quelle, gilt damit fuer alle Beine und Bruecken gleich, auch fuer Quellen ueber eine Bruecke):
//   - Faellt eine Schicht aus (nicht mehr live), waehrend eine tiefere derselben Menge live ist, ist das ein Flattern:
//     Die Schicht faellt sofort aus der Auswahl (Schalten nach unten bleibt sofort) und zaehlt erst wieder, wenn sie
//     hold lang am Stueck live war.
//   - Flattert sie erneut, bevor sie nach der Rueckkehr flapStable lang stabil war, verdoppelt sich hold (bis flapMax);
//     nach laengerer Stabilitaet beginnt der naechste Ausfall wieder bei flapHold.
//   - Fallen alle Schichten zugleich aus (Sender weg, Freigabe pausiert, Netz), gibt es keine Wartezeit: kommen sie
//     wieder, sind sie sofort nutzbar.
//
// Schichtwechsel wegen der Downlink-Schaetzung (Budget) laufen unveraendert ueber layerStep.

// Schwellen der Hysterese (Variablen fuer Tests).
var (
	flapHold   = 3 * time.Second  // erste Wartezeit nach einem Flattern
	flapMax    = 60 * time.Second // Obergrenze der Wartezeit
	flapStable = 30 * time.Second // so lange nach der Rueckkehr stabil: Wartezeit zurueck auf flapHold
)

// Gruende eines Schichtwechsels (Slot.Why, Diagnose).
const (
	whyBudget = "budget" // Downlink-Schaetzung bzw. Kontingent
	whyGone   = "gone"   // die bisherige Schicht ist nicht mehr live
	whyBack   = "back"   // Rueckkehr einer Schicht nach der Hysterese
)

// flap ist der Hysterese-Zustand einer Quelle (nur im Takt unter Room.mu).
type flap struct {
	use    bool          // im letzten Schritt nutzbar
	held   bool          // nach einem Flattern gesperrt, bis sie hold lang am Stueck live war
	hold   time.Duration // geforderte Live-Zeit (zuletzt verhaengt)
	liveAt int64         // seit wann ununterbrochen live (0 = nicht live)
	useAt  int64         // seit wann nutzbar
	backAt int64         // letzte Rueckkehr nach der Hysterese
	run    time.Duration // Live-Zeit vor dem letzten Ausfall
	bps    float64       // zuletzt gemessene Rate, solange live
	flaps  int           // Zahl der Flatter-Ausfaelle
}

// step fuehrt den Zustand einen Takt weiter. live: Quelle nutzbar laut source.live; lower: eine tiefere Schicht
// derselben Menge ist live; bps: geglaettete Rate.
func (f *flap) step(live, lower bool, bps float64, now int64) {
	if live {
		if f.liveAt == 0 {
			f.liveAt = now
		}
		if bps > 1000 {
			f.bps = bps
		}
		if f.held && now-f.liveAt >= int64(f.hold) {
			f.held = false
			f.backAt = now
		}
		if !f.use && !f.held {
			f.use, f.useAt = true, now
		}
		return
	}
	if f.use { // faellt aus
		f.use = false
		if f.liveAt != 0 {
			f.run = time.Duration(now - f.liveAt)
		}
		if lower {
			// gerade erst nach der Hysterese zurueck: wieder geflattert -> verdoppeln; sonst (erstes Flattern, nach
			// Stabilitaet oder nach einer Rueckkehr ohne Hysterese, z. B. gemeinsamer Ausfall) wieder flapHold
			if f.hold > 0 && f.backAt != 0 && f.backAt >= f.useAt && now-f.useAt < int64(flapStable) {
				f.hold *= 2
				if f.hold > flapMax {
					f.hold = flapMax
				}
			} else {
				f.hold = flapHold
			}
			f.held = true
			f.flaps++
		}
	}
	f.liveAt = 0
}

// usable: Quelle live und nicht durch die Hysterese gesperrt.
func (st *source) usable(now int64) bool { return st.live(now) && !st.fl.held }

// young: Die Quelle ist erst seit weniger als srcSettle ohne Unterbrechung live - ihre gemessene Rate taugt noch nicht
// fuer einen Schritt hinauf (control.go, srcSettle).
func (st *source) young(now int64) bool {
	return st.fl.liveAt != 0 && now-st.fl.liveAt < int64(srcSettle)
}

// stepFlaps fuehrt die Hysterese fuer eine Schicht-Menge (Kamera bzw. Bildschirm) einen Takt weiter.
func stepFlaps(m map[string]*source, now int64) {
	lower := false
	for _, k := range [...]string{"l", "m", "h"} {
		s := m[k]
		if s == nil {
			continue
		}
		live := s.live(now)
		s.fl.step(live, lower, s.bps, now)
		if live {
			lower = true
		}
	}
}

// setWhy haelt den Grund eines Schichtwechsels fuer die naechste Layout-Meldung fest (ref: Quelle, deren Zahlen
// gemeldet werden).
func (s *slot) setWhy(why string, ref *source, now int64) {
	s.why, s.whyKbit, s.whyLive, s.whyHold = why, 0, 0, 0
	if ref == nil {
		return
	}
	f := &ref.fl
	ms := func(d int64) int { return int(d / int64(time.Millisecond)) }
	switch why {
	case whyGone:
		s.whyKbit, s.whyLive, s.whyHold = int(f.bps/1000), ms(int64(f.run)), ms(int64(f.hold))
	case whyBack:
		s.whyKbit, s.whyLive, s.whyHold = int(f.bps/1000), ms(now-f.liveAt), ms(int64(f.hold))
	default:
		s.whyKbit = int(layerRate(ref) / 1000)
		if f.liveAt != 0 {
			s.whyLive = ms(now - f.liveAt)
		}
	}
}
