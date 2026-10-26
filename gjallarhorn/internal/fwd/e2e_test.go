package fwd

import (
	"encoding/binary"
	"math/rand"
	"sync"
	"testing"
	"time"

	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/rtpx"
)

// prec merkt sich Kopf und Nutzlast jedes geschriebenen Pakets (Kopie: der Schreiber nutzt einen Zwischenpuffer).
type prec struct {
	mu   sync.Mutex
	seqs []uint16
	pls  [][]byte
}

func (r *prec) WriteRTP(h *rtp.Header, p []byte) (int, error) {
	r.mu.Lock()
	r.seqs = append(r.seqs, h.SequenceNumber)
	r.pls = append(r.pls, append([]byte(nil), p...))
	r.mu.Unlock()
	return len(p), nil
}

// e2eFrame baut einen FrameCryptor-artigen VP8-Rahmen (docs/HORN-E2E.md): Klartext-Kopf 10 Byte (Schluesselbild) bzw.
// 3 Byte (Zwischenbild), dann Chiffrat (zufaellig), Tag 16, IV 12, 0x0C, kid - verteilt auf parts RTP-Pakete mit
// VP8-Descriptor (PictureID 15 Bit, TL0PICIDX, TID).
func e2eFrame(rnd *rand.Rand, seq *uint16, ts uint32, ssrc uint32, pic uint16, tl0, tid uint8, key bool, parts int) [][]byte {
	hdr := 3
	if key {
		hdr = 10
	}
	fr := make([]byte, hdr+40*parts+16+12+2)
	rnd.Read(fr)
	if key {
		fr[0] &^= 0x01 // P-Bit 0
		copy(fr[3:6], []byte{0x9d, 0x01, 0x2a})
	} else {
		fr[0] |= 0x01
	}
	fr[len(fr)-2], fr[len(fr)-1] = 0x0C, 3
	var out [][]byte
	chunk := (len(fr) + parts - 1) / parts
	for i := 0; i < parts; i++ {
		b := make([]byte, 12, 12+6+chunk)
		b[0] = 0x80
		b[1] = 96
		if i == parts-1 {
			b[1] |= 0x80
		}
		*seq++
		binary.BigEndian.PutUint16(b[2:], *seq)
		binary.BigEndian.PutUint32(b[4:], ts)
		binary.BigEndian.PutUint32(b[8:], ssrc)
		d := byte(0x80)
		if i == 0 {
			d |= 0x10
		}
		y := byte(0)
		if tid == 0 || key {
			y = 0x20
		}
		b = append(b, d, 0xe0, 0x80|byte(pic>>8), byte(pic), tl0, tid<<6|y)
		end := (i + 1) * chunk
		if end > len(fr) {
			end = len(fr)
		}
		out = append(out, append(b, fr[i*chunk:end]...))
	}
	return out
}

// TestE2EOpakBeimUmschreiben (BH3): Der Knoten schreibt beim Schichtwechsel und beim Weglassen zeitlicher Schichten
// nur den VP8-Descriptor um. Die verschluesselten Rahmen (Klartext-Kopf, Chiffrat, Trailer) muessen Byte fuer Byte
// ankommen, PictureID lueckenlos, TL0PICIDX je Basisrahmen +1, Folgenummern lueckenlos.
func TestE2EOpakBeimUmschreiben(t *testing.T) {
	leg := NewLeg()
	defer leg.Close()
	w := &prec{}
	o := leg.NewOut("v0", "", Video, ClassScreen)
	o.Bind(w, 9, 96)
	lo := &Source{Owner: "p", Layer: "l", Kind: Video, Clock: 90000}
	hi := &Source{Owner: "p", Layer: "m", Kind: Video, Clock: 90000}
	rnd := rand.New(rand.NewSource(7))
	sent := map[string]bool{} // Nutzlast hinter dem Descriptor
	var seqL, seqH uint16 = 1000, 40000
	picL, picH := uint16(0x7ff0), uint16(300) // PictureID-Ueberlauf auf l
	var tl0L, tl0H uint8 = 250, 17
	frame := func(s *Source, i int, key bool) {
		var pkts [][]byte
		tid := uint8([]uint8{0, 2, 1, 2}[i%4])
		if key {
			tid = 0
		}
		if s == lo {
			if tid == 0 {
				tl0L++
			}
			picL = (picL + 1) & 0x7fff
			pkts = e2eFrame(rnd, &seqL, uint32(i)*3000, 1, picL, tl0L, tid, key, 1+i%3)
		} else {
			if tid == 0 {
				tl0H++
			}
			picH = (picH + 1) & 0x7fff
			pkts = e2eFrame(rnd, &seqH, uint32(i)*3000+77, 2, picH, tl0H, tid, key, 1+i%3)
		}
		for _, p := range pkts {
			var h rtpx.Header
			rtpx.Parse(p, &h)
			v := rtpx.ParseVP8(p[h.Off:])
			sent[string(p[h.Off+int(v.HdrLen):])] = true
			s.Inject(p)
		}
	}
	o.Assign(lo)
	frame(lo, 0, true)
	for i := 1; i < 12; i++ {
		frame(lo, i, false)
		frame(hi, i, false)
	}
	drain := func() { waitFor(t, func() bool { return leg.QueueLen() == 0 }); time.Sleep(10 * time.Millisecond) }
	drain()
	o.SetMaxTID(0) // nur Basisschicht
	for i := 12; i < 20; i++ {
		frame(lo, i, false)
		frame(hi, i, false)
	}
	drain()
	o.SetMaxTID(3)
	o.Assign(hi) // Wechsel am naechsten Schluesselbild von m
	for i := 20; i < 24; i++ {
		frame(lo, i, false)
		frame(hi, i, false)
	}
	frame(hi, 24, true)
	for i := 25; i < 40; i++ {
		frame(lo, i, false)
		frame(hi, i, false)
	}
	drain()
	if o.Current() != hi {
		t.Fatal("kein wechsel auf m")
	}
	if leg.Stats.TIDSkipped.Load() == 0 {
		t.Fatal("keine zeitliche Schicht weggelassen")
	}

	w.mu.Lock()
	defer w.mu.Unlock()
	var lastPic uint16
	var lastTL0 uint8
	frames := 0
	for i, p := range w.pls {
		if i > 0 && w.seqs[i] != w.seqs[i-1]+1 {
			t.Fatalf("folgenummer %d -> %d", w.seqs[i-1], w.seqs[i])
		}
		v := rtpx.ParseVP8(p)
		if !sent[string(p[v.HdrLen:])] {
			t.Fatalf("paket %d: verschluesselter Rahmen veraendert", i)
		}
		if !v.FrameStart {
			continue
		}
		if frames > 0 {
			if v.PicID != (lastPic+1)&0x7fff {
				t.Fatalf("rahmen %d: picid %d nach %d", frames, v.PicID, lastPic)
			}
			want := lastTL0
			if v.TID == 0 {
				want++
			}
			if v.TL0 != want {
				t.Fatalf("rahmen %d: tl0 %d nach %d (tid %d)", frames, v.TL0, lastTL0, v.TID)
			}
		}
		lastPic, lastTL0 = v.PicID, v.TL0
		frames++
	}
	if frames < 30 {
		t.Fatalf("nur %d rahmen", frames)
	}
}
