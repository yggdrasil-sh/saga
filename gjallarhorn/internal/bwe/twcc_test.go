package bwe

import (
	"testing"

	"github.com/pion/rtcp"
)

type rep struct {
	seq uint16
	st  int
	us  int64
}

func unpackAll(t *testing.T, fb *rtcp.TransportLayerCC) ([]rep, bool) {
	t.Helper()
	var out []rep
	ok := Unpack(fb, func(seq uint16, st int, us int64) { out = append(out, rep{seq, st, us}) })
	return out, ok
}

// fbOf baut eine Rueckmeldung aus einer Statusliste (Test-Hilfe; Ankunft in us ab ref x 64 ms, auf 250 us gerundet).
// Lange gleiche Folgen als Lauflaenge, sonst Zwei-Bit-Vektoren.
func fbOf(base uint16, ref uint32, sts []rep) *rtcp.TransportLayerCC {
	fb := &rtcp.TransportLayerCC{BaseSequenceNumber: base, PacketStatusCount: uint16(len(sts)), ReferenceTime: ref}
	t := int64(ref) * refUnit
	syms := make([]uint16, len(sts))
	for i, s := range sts {
		switch s.st {
		case StLost:
			syms[i] = rtcp.TypeTCCPacketNotReceived
		case StNoTime:
			syms[i] = rtcp.TypeTCCPacketReceivedWithoutDelta
		default:
			d := (s.us - t) / 250 * 250
			ty := uint16(rtcp.TypeTCCPacketReceivedSmallDelta)
			if d < 0 || d > 255*250 {
				ty = rtcp.TypeTCCPacketReceivedLargeDelta
			}
			syms[i] = ty
			fb.RecvDeltas = append(fb.RecvDeltas, &rtcp.RecvDelta{Type: ty, Delta: d})
			t += d
		}
	}
	for i := 0; i < len(syms); {
		j := i
		for j < len(syms) && syms[j] == syms[i] {
			j++
		}
		if j-i >= 7 {
			fb.PacketChunks = append(fb.PacketChunks, &rtcp.RunLengthChunk{Type: rtcp.TypeTCCRunLengthChunk, PacketStatusSymbol: syms[i], RunLength: uint16(j - i)})
			i = j
			continue
		}
		v := &rtcp.StatusVectorChunk{Type: rtcp.TypeTCCStatusVectorChunk, SymbolSize: rtcp.TypeTCCSymbolSizeTwoBit}
		for k := 0; k < 7; k++ {
			if i+k < len(syms) {
				v.SymbolList = append(v.SymbolList, syms[i+k])
			} else {
				v.SymbolList = append(v.SymbolList, rtcp.TypeTCCPacketNotReceived) // Auffuellung
			}
		}
		fb.PacketChunks = append(fb.PacketChunks, v)
		i += 7
	}
	return fb
}

// wire schickt die Rueckmeldung einmal durch pions Kodierer und Dekodierer (echtes Leitungsformat).
func wire(t *testing.T, fb *rtcp.TransportLayerCC) *rtcp.TransportLayerCC {
	t.Helper()
	fb.Header = rtcp.Header{Count: rtcp.FormatTCC, Type: rtcp.TypeTransportSpecificFeedback, Length: uint16(fb.MarshalSize()/4 - 1)}
	b, err := fb.Marshal()
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var out rtcp.TransportLayerCC
	if err := out.Unmarshal(b); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	return &out
}

func TestUnpackReihenfolge(t *testing.T) {
	ref := uint32(1000)
	t0 := int64(ref) * refUnit
	in := []rep{
		{0, StRecv, t0 + 250}, {0, StRecv, t0 + 1000}, {0, StLost, 0}, {0, StRecv, t0 + 5000}, {0, StLost, 0},
		{0, StLost, 0}, {0, StRecv, t0 + 5250}, {0, StRecv, t0 + 9000}, {0, StRecv, t0 + 9000}, {0, StLost, 0},
	}
	for i := 0; i < 20; i++ { // Lauflaenge
		in = append(in, rep{0, StRecv, t0 + 10_000 + int64(i)*2500})
	}
	in = append(in, rep{0, StLost, 0}, rep{0, StRecv, t0 + 70_000}, rep{0, StRecv, t0 + 70_250})
	for i := range in {
		in[i].seq = uint16(500 + i)
	}
	for _, viaWire := range []bool{false, true} {
		fb := fbOf(500, ref, in)
		if viaWire {
			fb = wire(t, fb)
		}
		got, ok := unpackAll(t, fb)
		if !ok || len(got) != len(in) {
			t.Fatalf("wire=%v ok=%v len=%d want %d", viaWire, ok, len(got), len(in))
		}
		for i := range in {
			if got[i] != in[i] {
				t.Fatalf("wire=%v paket %d: %+v want %+v", viaWire, i, got[i], in[i])
			}
		}
	}
}

// Der letzte Vektor ist aufgefuellt: Symbole jenseits der gemeldeten Anzahl duerfen nicht als "verloren" erscheinen.
func TestUnpackAuffuellung(t *testing.T) {
	in := []rep{{10, StRecv, 250}, {11, StLost, 0}, {12, StRecv, 500}}
	fb := wire(t, fbOf(10, 0, in))
	if n := len(fb.PacketChunks[0].(*rtcp.StatusVectorChunk).SymbolList); n != 7 {
		t.Fatalf("vektor mit %d symbolen", n)
	}
	got, ok := unpackAll(t, fb)
	if !ok || len(got) != 3 || got[2] != in[2] {
		t.Fatalf("ok=%v %+v", ok, got)
	}
	// Ein-Bit-Vektor (14 Symbole), 9 gemeldet
	fb = &rtcp.TransportLayerCC{BaseSequenceNumber: 65530, PacketStatusCount: 9, ReferenceTime: 1,
		PacketChunks: []rtcp.PacketStatusChunk{&rtcp.StatusVectorChunk{Type: rtcp.TypeTCCStatusVectorChunk, SymbolSize: rtcp.TypeTCCSymbolSizeOneBit,
			SymbolList: []uint16{1, 1, 0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0}}}}
	for i := 0; i < 7; i++ {
		fb.RecvDeltas = append(fb.RecvDeltas, &rtcp.RecvDelta{Type: rtcp.TypeTCCPacketReceivedSmallDelta, Delta: 1000})
	}
	got, ok = unpackAll(t, wire(t, fb))
	if !ok || len(got) != 9 {
		t.Fatalf("ok=%v len=%d", ok, len(got))
	}
	// Ueberlauf der Folgenummer mitten in der Rueckmeldung
	want := []uint16{65530, 65531, 65532, 65533, 65534, 65535, 0, 1, 2}
	lost := 0
	for i, g := range got {
		if g.seq != want[i] {
			t.Fatalf("seq %d: %d want %d", i, g.seq, want[i])
		}
		if g.st == StLost {
			lost++
		}
	}
	if lost != 2 || got[8].us != refUnit+7000 {
		t.Fatalf("lost=%d letzte ankunft=%d", lost, got[8].us)
	}
}

func TestUnpackGrosseDeltas(t *testing.T) {
	ref := uint32(77)
	t0 := int64(ref) * refUnit
	// grosser Abstand vorwaerts (ueber 63,75 ms), rueckwaerts (umgeordnet angekommen), Maximum des 16-bit-Abstands
	in := []rep{{1, StRecv, t0 + 100_000}, {2, StRecv, t0 + 90_000}, {3, StRecv, t0 + 90_000 + 32767*250}, {4, StRecv, t0 + 90_000 + 32767*250 - 8000_000}}
	got, ok := unpackAll(t, wire(t, fbOf(1, ref, in)))
	if !ok || len(got) != 4 {
		t.Fatalf("ok=%v %+v", ok, got)
	}
	for i := range in {
		if got[i] != in[i] {
			t.Fatalf("paket %d: %+v want %+v", i, got[i], in[i])
		}
	}
}

func TestUnpackUnstimmig(t *testing.T) {
	in := []rep{{1, StRecv, 250}, {2, StRecv, 500}, {3, StRecv, 750}}
	fb := fbOf(1, 0, in)
	fb.RecvDeltas = fb.RecvDeltas[:2] // ein Abstand fehlt
	got, ok := unpackAll(t, fb)
	if ok || len(got) != 2 {
		t.Fatalf("ok=%v len=%d", ok, len(got))
	}
	fb = fbOf(1, 0, in)
	fb.PacketStatusCount = 20 // mehr gemeldet als Chunks tragen
	if _, ok := unpackAll(t, fb); ok {
		t.Fatal("zu wenige chunks nicht bemerkt")
	}
	// "empfangen ohne Abstand" verbraucht keinen Abstand
	fb = fbOf(1, 0, []rep{{1, StRecv, 250}, {2, StNoTime, 0}, {3, StRecv, 750}})
	got, ok = unpackAll(t, fb)
	if !ok || got[1].st != StNoTime || got[2].us != 750 {
		t.Fatalf("ok=%v %+v", ok, got)
	}
	// leere Rueckmeldung
	if got, ok := unpackAll(t, &rtcp.TransportLayerCC{}); !ok || len(got) != 0 {
		t.Fatalf("leer: ok=%v %+v", ok, got)
	}
}

func TestRefUnwrap(t *testing.T) {
	var u refUnwrap
	if o := u.offset(uint32(refRange - 2)); o != 0 {
		t.Fatal(o)
	}
	if o := u.offset(uint32(refRange - 1)); o != 0 {
		t.Fatal(o)
	}
	if o := u.offset(1); o != refRange*refUnit { // Ueberlauf vorwaerts
		t.Fatal(o)
	}
	if o := u.offset(uint32(refRange - 1)); o != 0 { // aeltere Rueckmeldung von vor dem Ueberlauf
		t.Fatal(o)
	}
	if o := u.offset(3); o != refRange*refUnit {
		t.Fatal(o)
	}
}
