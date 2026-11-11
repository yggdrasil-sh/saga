package bwe

import (
	"math/rand"
	"testing"

	"github.com/pion/interceptor/pkg/twcc"
	"github.com/pion/rtcp"
)

// Pruefung bwe0: Unpack gegen pions eigenen Packer (twcc.Recorder), ueber den Draht (Marshal/Unmarshal), mit Verlusten,
// Umordnung, grossen Abstaenden und Umlauf der Folgenummer.
func TestUnpackGegenPionRecorder(t *testing.T) {
	rng := rand.New(rand.NewSource(7))
	for round := 0; round < 300; round++ {
		rec := twcc.NewRecorder(1)
		base := uint16(rng.Intn(65536))
		if round%3 == 0 {
			base = uint16(65536 - rng.Intn(40))
		}
		n := 1 + rng.Intn(400)
		want := map[uint16]int64{}
		t0 := int64(rng.Intn(1<<30)) * 1000
		at := t0
		for i := 0; i < n; i++ {
			at += int64(rng.Intn(3000))
			if rng.Intn(50) == 0 {
				at += int64(rng.Intn(400_000))
			}
			if rng.Intn(10) == 0 {
				continue // verloren
			}
			seq := base + uint16(i)
			a := at
			if rng.Intn(20) == 0 {
				a -= int64(rng.Intn(20000)) // Umordnung
			}
			rec.Record(2, seq, a)
			want[seq] = a
		}
		got := map[uint16]int64{}
		lost := 0
		var firstRef int64 = -1
		for _, p := range rec.BuildFeedbackPacket() {
			raw, err := p.Marshal()
			if err != nil {
				t.Fatal(err)
			}
			var fb rtcp.TransportLayerCC
			if err := fb.Unmarshal(raw); err != nil {
				t.Fatal(err)
			}
			if firstRef < 0 {
				firstRef = int64(fb.ReferenceTime)
			}
			ok := Unpack(&fb, func(seq uint16, st int, us int64) {
				switch st {
				case StRecv:
					if _, dup := got[seq]; dup {
						t.Fatalf("doppelt %d", seq)
					}
					got[seq] = us
				case StLost:
					lost++
				}
			})
			if !ok {
				t.Fatalf("runde %d: Unpack false", round)
			}
		}
		if len(got) != len(want) {
			t.Fatalf("runde %d: %d gemeldet, %d erwartet", round, len(got), len(want))
		}
		// Abstaende muessen bis auf die 250-us-Rasterung stimmen (absolute Zeit modulo Bezug)
		var off int64
		first := true
		for seq, w := range want {
			g, ok := got[seq]
			if !ok {
				t.Fatalf("runde %d: %d fehlt", round, seq)
			}
			d := g - w
			if first {
				off, first = d, false
			}
			if x := d - off; x > 600 || x < -600 {
				t.Fatalf("runde %d seq %d: Abweichung %d us", round, seq, x)
			}
		}
	}
}

// Pruefung bwe0: zufaellige und verstuemmelte Rueckmeldungen duerfen weder Unpack noch den Mitleser zu Fall bringen.
func TestMitleserKaputteRueckmeldungen(t *testing.T) {
	rng := rand.New(rand.NewSource(11))
	m := NewMitleser(Defaults())
	m.SetOnChange(func(Change) {})
	now := int64(0)
	seq := uint16(65000)
	okN, badN := 0, 0
	for round := 0; round < 1000; round++ {
		for i := 0; i < 5; i++ {
			now += int64(rng.Intn(5e6))
			m.Sent(now, seq, 200+rng.Intn(1000), Kind(rng.Intn(4)))
			seq++
		}
		// gueltiger Kopf, zufaelliger Rest
		l := 20 + 4*rng.Intn(30)
		raw := make([]byte, l)
		rng.Read(raw)
		raw[0] = 0x80 | 15
		raw[1] = 205
		raw[2], raw[3] = byte((l/4-1)>>8), byte(l/4-1)
		if rng.Intn(2) == 0 { // Basis nahe der laufenden Folgenummer
			b := seq - uint16(rng.Intn(60))
			raw[12], raw[13] = byte(b>>8), byte(b)
		}
		if rng.Intn(4) == 0 {
			raw[14], raw[15] = 0xff, 0xff
		}
		var fb rtcp.TransportLayerCC
		if err := fb.Unmarshal(raw); err != nil {
			badN++
			continue
		}
		okN++
		m.Feedback(now, &fb)
		if round%200 == 0 {
			m.Reset(now)
		}
		_ = m.Snapshot(now)
		_ = m.Span(now-int64(1e9), now)
	}
	// von Hand: absurde Strukturen
	for _, fb := range []*rtcp.TransportLayerCC{
		{},
		{PacketStatusCount: 65535},
		{PacketStatusCount: 65535, PacketChunks: []rtcp.PacketStatusChunk{&rtcp.RunLengthChunk{PacketStatusSymbol: rtcp.TypeTCCPacketNotReceived, RunLength: 8191}}},
		{PacketStatusCount: 10, PacketChunks: []rtcp.PacketStatusChunk{&rtcp.RunLengthChunk{PacketStatusSymbol: rtcp.TypeTCCPacketReceivedSmallDelta, RunLength: 8191}}},
		{PacketStatusCount: 10, PacketChunks: []rtcp.PacketStatusChunk{&rtcp.StatusVectorChunk{SymbolList: []uint16{1, 2, 3, 7, 99}}}},
		{PacketStatusCount: 3, ReferenceTime: 0xffffffff, RecvDeltas: []*rtcp.RecvDelta{{Delta: -1 << 62}, {Delta: 1 << 62}, {Delta: 1 << 62}},
			PacketChunks: []rtcp.PacketStatusChunk{&rtcp.RunLengthChunk{PacketStatusSymbol: rtcp.TypeTCCPacketReceivedLargeDelta, RunLength: 3}}},
	} {
		fb.BaseSequenceNumber = seq - 5
		m.Feedback(now, fb)
		_ = m.Snapshot(now)
		_ = m.Span(0, now)
	}
	c := m.Counters()
	if okN == 0 || c.Feedbacks == 0 {
		t.Fatalf("nichts geprueft: geparst %d, verworfen %d", okN, badN)
	}
}

// Pruefung bwe0: Bezugszeit laeuft ueber (24 bit x 64 ms), Folgenummer laeuft mehrfach um - der Detektor bleibt frei.
func TestMitleserUmlaufLang(t *testing.T) {
	m := NewMitleser(Defaults())
	var chg []Change
	m.SetOnChange(func(c Change) { chg = append(chg, c) })
	seq := uint16(0)
	now := int64(1e9)
	arr0 := (refRange - 200) * refUnit // kurz vor dem Ueberlauf der Bezugszeit
	var batch []rep
	base := seq
	for i := 0; i < 200000; i++ { // 200000 Pakete zu 5 ms = 1000 s, 3 Umlaeufe der Folgenummer
		now += 5e6
		m.Sent(now, seq, 1000, KindVideo)
		batch = append(batch, rep{seq, StRecv, arr0 + (now-1e9)/1000 + 20000})
		seq++
		if len(batch) == 20 {
			ref := uint32((batch[0].us / refUnit) % refRange)
			sts := make([]rep, len(batch))
			for k, b := range batch {
				sts[k] = rep{b.seq, b.st, b.us - (batch[0].us/refUnit-int64(ref))*refUnit}
			}
			m.Feedback(now+30e6, fbOf(base, ref, sts))
			batch, base = batch[:0], seq
		}
	}
	s := m.Snapshot(now)
	c := m.Counters()
	if s.State != Free || c.ToWarn != 0 || c.ToCong != 0 || c.Unknown != 0 || c.Lost != 0 {
		t.Fatalf("Zustand %v, Wechsel %+v, Zaehler %+v", s.State, chg, c)
	}
	t.Logf("Schlange %d us, Durchsatz %d, Fenster %d", s.QueueUs, s.AckedBps, c.Windows)
}
