package rtpx

import "testing"

func pkt(ext bool, pad int, payload []byte) []byte {
	b := []byte{0x80, 0xe0 | 96, 0x12, 0x34, 0, 0, 0x10, 0, 0xde, 0xad, 0xbe, 0xef}
	if ext {
		b[0] |= 0x10
		b = append(b, 0xbe, 0xde, 0, 1, 0x10, 0xaa, 0, 0)
	}
	b = append(b, payload...)
	if pad > 0 {
		b[0] |= 0x20
		for i := 0; i < pad-1; i++ {
			b = append(b, 0)
		}
		b = append(b, byte(pad))
	}
	return b
}

func TestParse(t *testing.T) {
	var h Header
	b := pkt(true, 4, []byte{0x10, 0x00, 1, 2, 3})
	if !Parse(b, &h) {
		t.Fatal("parse")
	}
	if h.Seq != 0x1234 || h.TS != 0x1000 || h.SSRC != 0xdeadbeef || !h.Marker || h.PayloadType != 96 {
		t.Fatalf("felder %+v", h)
	}
	if h.Off != 20 || h.Len != 5 {
		t.Fatalf("nutzlast %d %d", h.Off, h.Len)
	}
	if Parse(b[:10], &h) {
		t.Fatal("zu kurz akzeptiert")
	}
	// reine Auffuellung
	p := pkt(false, 8, nil)
	if !Parse(p, &h) || h.Len != 0 {
		t.Fatalf("padding %+v", h)
	}
}

func TestVP8(t *testing.T) {
	// ohne X: S=1, PID=0, P=0 -> Schluesselbild
	if v := ParseVP8([]byte{0x10, 0x00}); !v.FrameStart || !v.Key {
		t.Fatalf("key %+v", v)
	}
	if v := ParseVP8([]byte{0x10, 0x01}); !v.FrameStart || v.Key {
		t.Fatalf("delta %+v", v)
	}
	if v := ParseVP8([]byte{0x00, 0x00}); v.FrameStart {
		t.Fatalf("fortsetzung %+v", v)
	}
	// mit X, I (15 Bit PictureID), L, T
	if v := ParseVP8([]byte{0x90, 0xe0, 0x81, 0x23, 0x05, 0x40, 0x00}); !v.FrameStart || !v.Key {
		t.Fatalf("ext key %+v", v)
	}
	if v := ParseVP8([]byte{0x90, 0x80, 0x12, 0x01}); !v.FrameStart || v.Key {
		t.Fatalf("ext delta %+v", v)
	}
}

func TestRewriteAllocs(t *testing.T) {
	b := pkt(false, 0, []byte{0x10, 0})
	var h Header
	n := testing.AllocsPerRun(1000, func() {
		Parse(b, &h)
		_ = ParseVP8(b[h.Off : h.Off+h.Len])
		Rewrite(b, h.Seq+1, h.TS+3000, 42)
	})
	if n != 0 {
		t.Fatalf("allokationen %v", n)
	}
	Parse(b, &h)
	if h.SSRC != 42 {
		t.Fatal("ssrc")
	}
}

func TestVP8Fields(t *testing.T) {
	// X, I (15 Bit), L, T (TID 2, Y)
	p := []byte{0x90, 0xe0, 0x81, 0x23, 0x05, 0xa0, 0x01}
	v := ParseVP8(p)
	if !v.HasPicID || !v.PicIDLong || v.PicID != 0x123 || !v.HasTL0 || v.TL0 != 5 || !v.HasTID || v.TID != 2 || !v.Sync || v.HdrLen != 6 || v.Key {
		t.Fatalf("%+v", v)
	}
	RewriteVP8(p, &v, 0x7ffe, 9)
	w := ParseVP8(p)
	if w.PicID != 0x7ffe || w.TL0 != 9 || w.TID != 2 {
		t.Fatalf("umgeschrieben %+v", w)
	}
	// 7 Bit PictureID
	p = []byte{0x90, 0x80, 0x12, 0x00}
	v = ParseVP8(p)
	if v.PicIDLong || v.PicID != 0x12 || !v.Key || v.PicIDMask() != 0x7f {
		t.Fatalf("kurz %+v", v)
	}
	RewriteVP8(p, &v, 0x85, 0)
	if p[2] != 0x05 {
		t.Fatalf("maske %x", p[2])
	}
	// abgeschnitten: kein Rahmenanfang
	if v := ParseVP8([]byte{0x90, 0x80, 0x81}); v.FrameStart || v.HasPicID {
		t.Fatalf("abgeschnitten %+v", v)
	}
}

func TestAudioLevel(t *testing.T) {
	// Ein-Byte-Kopf: id 1 Laenge 1 (audio-level 0x80|30), Fuellung
	b := []byte{0x90, 111, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 0xbe, 0xde, 0, 1, 0x10, 0x80 | 30, 0, 0, 0xf8}
	var h Header
	if !Parse(b, &h) {
		t.Fatal("parse")
	}
	if l, v, ok := AudioLevel(b, &h, 1); !ok || !v || l != 30 {
		t.Fatalf("pegel %d %v %v", l, v, ok)
	}
	if _, _, ok := AudioLevel(b, &h, 2); ok {
		t.Fatal("falsche id")
	}
	// Zwei-Byte-Kopf
	b2 := []byte{0x90, 111, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 0x10, 0x00, 0, 1, 3, 1, 90, 0, 0xf8}
	if !Parse(b2, &h) {
		t.Fatal("parse2")
	}
	if l, v, ok := AudioLevel(b2, &h, 3); !ok || v || l != 90 {
		t.Fatalf("pegel2 %d %v %v", l, v, ok)
	}
	n := testing.AllocsPerRun(1000, func() { AudioLevel(b, &h, 1) })
	if n != 0 {
		t.Fatal("allokation")
	}
}
