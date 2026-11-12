package fwd

import "testing"

// TestPruef13HoechsteNummerMitFuellung: der Zufallslauf aus pruef13_test.go (Wechsel der Schicht, Nachzuegler,
// Verwerfen, Neubeginn einer Folge) mit Fuell-Schueben des Knotens nach jedem zweiten Paket. Auch ein Fuellpaket ist
// eine geschriebene Nummer: Der Stand des Ausgangs (hiSeq) folgt ihm, der naechste Wechsel setzt dahinter an, und ein
// Fuellpaket liegt selbst immer hinter allem Geschriebenen.
func TestPruef13HoechsteNummerMitFuellung(t *testing.T) {
	for _, long := range []bool{true, false} {
		sw, late, pads := 0, 0, 0
		for seed := int64(201); seed <= 280; seed++ {
			i := 0
			s, l, p := p13Lauf(t, seed, false, long, func(leg *Leg, o *Out, step int) {
				if i++; i%2 != 0 {
					return
				}
				leg.padBuf[PadSize-1] = PadSize
				now := Now()
				leg.SetPadding(o, 1e9)
				leg.padT, leg.padCredit = now, float64((1+i%3)*padWire)
				leg.pad(now) // schreibt nur zwischen zwei Bildern (nach einem Paket mit Marker), nicht beim Verwerfen
				leg.SetPadding(nil, 0)
			})
			sw, late, pads = sw+s, late+l, pads+p
		}
		t.Logf("15 Bit %v: %d Wechsel, %d Nachzuegler hinaus, %d Fuellpakete", long, sw, late, pads)
		if sw < 300 || late < 1000 || pads < 5000 {
			t.Fatalf("15 Bit %v: nur %d Wechsel, %d Nachzuegler, %d Fuellpakete - der Test prueft zu wenig", long, sw, late, pads)
		}
	}
}
