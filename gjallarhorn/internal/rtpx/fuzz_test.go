package rtpx

import "testing"

// FuzzParse: beliebige Bytes duerfen nie zu Panik oder Grenzueberschreitung
// fuehren; angenommene Pakete haben eine Nutzlast innerhalb des Puffers.
func FuzzParse(f *testing.F) {
	f.Add(pkt(true, 4, []byte{0x10, 0x00, 1, 2, 3}))
	f.Add(pkt(false, 0, []byte{0x90, 0xe0, 0x81, 0x23, 0x05, 0xa0, 0x01}))
	f.Add([]byte{0x90, 111, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 0x10, 0x00, 0, 1, 3, 1, 90, 0})
	f.Fuzz(func(t *testing.T, b []byte) {
		var h Header
		if !Parse(b, &h) {
			return
		}
		if h.Off < 12 || h.Len < 0 || h.Off+h.Len > len(b) || h.ExtOff+h.ExtLen > len(b) {
			t.Fatalf("grenzen %+v len %d", h, len(b))
		}
		for id := uint8(0); id < 16; id++ {
			if e := Extension(b, &h, id); len(e) > h.ExtLen {
				t.Fatal("erweiterung zu lang")
			}
			AudioLevel(b, &h, id)
		}
		v := ParseVP8(b[h.Off : h.Off+h.Len])
		checkVP8(t, b[h.Off:h.Off+h.Len], v)
	})
}

func checkVP8(t *testing.T, p []byte, v VP8) {
	if v.FrameStart && int(v.HdrLen) > len(p) {
		t.Fatalf("descriptor laenger als paket %+v %d", v, len(p))
	}
	if v.HasPicID && int(v.PicOff) >= len(p) || v.HasTL0 && int(v.TL0Off) >= len(p) {
		t.Fatalf("offsets %+v %d", v, len(p))
	}
	if v.TID > 3 {
		t.Fatal("tid")
	}
	q := append([]byte(nil), p...)
	RewriteVP8(q, &v, v.PicID+1, v.TL0+1)
	w := ParseVP8(q)
	if w.HdrLen != v.HdrLen || w.FrameStart != v.FrameStart || w.Key != v.Key || w.TID != v.TID {
		t.Fatalf("umschreiben aendert struktur %+v %+v", v, w)
	}
	if v.HasPicID && w.PicID != (v.PicID+1)&v.PicIDMask() {
		t.Fatalf("picid %d %d", v.PicID, w.PicID)
	}
}

// FuzzVP8 prueft den Descriptor-Parser direkt.
func FuzzVP8(f *testing.F) {
	f.Add([]byte{0x90, 0xe0, 0x81, 0x23, 0x05, 0xa0, 0x01})
	f.Add([]byte{0x90, 0x80, 0x12, 0x00})
	f.Add([]byte{0x10, 0x00})
	f.Fuzz(func(t *testing.T, p []byte) {
		checkVP8(t, p, ParseVP8(p))
	})
}
