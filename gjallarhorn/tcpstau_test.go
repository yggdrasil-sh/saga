package main

import (
	"encoding/binary"
	"testing"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
)

// tcpChain: Abgriff wie in newSFUAPI (er setzt die transport-cc-Nummer selbst), ein Strom mit Kopf-Erweiterung 5.
type tcpChain struct {
	m    *bwe.Mitleser
	w    interceptor.RTPWriter
	r    interceptor.RTCPReader
	sent []sentPkt
	rtcp [][]byte
}

type sentPkt struct {
	seq uint16
	ns  int64
}

func newTCPChain(t *testing.T) *tcpChain {
	t.Helper()
	c := &tcpChain{}
	hook := &tcpHook{next: func(m *bwe.Mitleser) { c.m = m }, on: true}
	ir := &interceptor.Registry{}
	ir.Add(tcpCCFactory{hook})
	ch, err := ir.Build("")
	if err != nil {
		t.Fatal(err)
	}
	if c.m == nil {
		t.Fatal("kein Mitleser")
	}
	info := &interceptor.StreamInfo{SSRC: 1, MimeType: "video/VP8", RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
	c.w = ch.BindLocalStream(info, interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) {
		raw := h.GetExtension(5)
		if len(raw) < 2 {
			t.Fatal("ohne transport-cc-Nummer gesendet")
		}
		c.sent = append(c.sent, sentPkt{binary.BigEndian.Uint16(raw), bweNow()})
		return len(pl), nil
	}))
	c.r = ch.BindRTCPReader(interceptor.RTCPReaderFunc(func(b []byte, a interceptor.Attributes) (int, interceptor.Attributes, error) {
		p := c.rtcp[0]
		c.rtcp = c.rtcp[1:]
		return copy(b, p), a, nil
	}))
	return c
}

// feedback meldet die gesendeten Pakete ab Index from mit Ankunft = Sendezeit + 50 ms + extra(i) (Leitungsformat).
func (c *tcpChain) feedback(t *testing.T, from int, extra func(i int) time.Duration) {
	t.Helper()
	ps := c.sent[from:]
	ref := uint32((ps[0].ns/1000 + 50_000) / 64_000) // Bezugszeit x 64 ms, vor der ersten Ankunft
	fb := &rtcp.TransportLayerCC{BaseSequenceNumber: ps[0].seq, PacketStatusCount: uint16(len(ps)), ReferenceTime: ref,
		PacketChunks: []rtcp.PacketStatusChunk{&rtcp.RunLengthChunk{Type: rtcp.TypeTCCRunLengthChunk,
			PacketStatusSymbol: rtcp.TypeTCCPacketReceivedLargeDelta, RunLength: uint16(len(ps))}}}
	at := int64(ref) * 64_000
	for i, p := range ps {
		arr := p.ns/1000 + 50_000 + int64(extra(from+i)/time.Microsecond)
		d := (arr - at) / 250 * 250
		fb.RecvDeltas = append(fb.RecvDeltas, &rtcp.RecvDelta{Type: rtcp.TypeTCCPacketReceivedLargeDelta, Delta: d})
		at += d
	}
	fb.Header = rtcp.Header{Count: rtcp.FormatTCC, Type: rtcp.TypeTransportSpecificFeedback, Length: uint16(fb.MarshalSize()/4 - 1)}
	b, err := fb.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	c.rtcp = append(c.rtcp, b)
	buf := make([]byte, 1500)
	if _, _, err := c.r.Read(buf, nil); err != nil {
		t.Fatal(err)
	}
}

func (c *tcpChain) send(t *testing.T, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if _, err := c.w.Write(&rtp.Header{Version: 2, SSRC: 1, SequenceNumber: uint16(len(c.sent))}, make([]byte, 1000), nil); err != nil {
			t.Fatal(err)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// Abgriff in der Kette: sieht die Folgenummer des Kopfs, ordnet die Rueckmeldung zu; das Signal zeigt eine wachsende
// Schlange (Laufzeit und gesendet gegen bestaetigt), und nach einer freien Strecke keine.
func TestTCPStauAbgriff(t *testing.T) {
	c := newTCPChain(t)
	c.send(t, 60)
	c.feedback(t, 0, func(int) time.Duration { return 0 })
	cnt := c.m.Counters()
	if cnt.Acked != 60 || cnt.Unknown != 0 || cnt.Bad != 0 || cnt.Sent[bwe.KindVideo] != 60 {
		t.Fatalf("zaehler %+v", cnt)
	}
	s := tcpSig(c.m)
	if !s.OK || s.Queue > 2*time.Millisecond {
		t.Fatalf("frei: %+v", s)
	}
	// Schlange waechst: jedes Paket 3 ms laenger unterwegs als das vorige (Leitung bei ~60 % der Rate)
	c.send(t, 60)
	c.feedback(t, 60, func(i int) time.Duration { return time.Duration(i-59) * 3 * time.Millisecond })
	s = tcpSig(c.m)
	if !s.OK || s.Queue < 150*time.Millisecond || s.AckedBps <= 0 || s.SentBps < s.AckedBps*1.2 {
		t.Fatalf("wachsend: %+v", s)
	}
}

// HORN_TCP_STAU=0: keine Kopf-Erweiterung, kein Abgriff.
func TestTCPStauAus(t *testing.T) {
	ir := &interceptor.Registry{}
	if err := configureTCPStau(nil, ir, &tcpHook{mode: tcpStauOff}); err != nil {
		t.Fatal(err)
	}
	if err := configureTCPStau(nil, ir, nil); err != nil {
		t.Fatal(err)
	}
	ch, err := ir.Build("")
	if err != nil {
		t.Fatal(err)
	}
	info := &interceptor.StreamInfo{SSRC: 1, RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
	w := ch.BindLocalStream(info, interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) {
		if h.Extension {
			t.Fatal("Kopf-Erweiterung trotz Schalter aus")
		}
		return len(pl), nil
	}))
	if _, err := w.Write(&rtp.Header{Version: 2, SSRC: 1}, []byte{1}, nil); err != nil {
		t.Fatal(err)
	}
}

// Bein ohne Signal (Tunnel-Weg ws ohne HORN_TCP_STAU_WS): Abgriff in der Kette, aber keine Nummer, kein Mitleser.
func TestTCPStauBeinAus(t *testing.T) {
	called := false
	hook := &tcpHook{next: func(*bwe.Mitleser) { called = true }}
	ir := &interceptor.Registry{}
	ir.Add(tcpCCFactory{hook})
	ch, err := ir.Build("")
	if err != nil {
		t.Fatal(err)
	}
	if called {
		t.Fatal("Mitleser fuer ein Bein ohne Signal")
	}
	info := &interceptor.StreamInfo{SSRC: 1, RTPHeaderExtensions: []interceptor.RTPHeaderExtension{{URI: sdp.TransportCCURI, ID: 5}}}
	w := ch.BindLocalStream(info, interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, _ interceptor.Attributes) (int, error) {
		if h.Extension {
			t.Fatal("Kopf-Erweiterung auf einem Bein ohne Signal")
		}
		return len(pl), nil
	}))
	if _, err := w.Write(&rtp.Header{Version: 2, SSRC: 1}, []byte{1}, nil); err != nil {
		t.Fatal(err)
	}
}

// Auflage Pruefung: Nur Beine mit Bild-Ausgaengen bekommen Folgenummern und Mitleser. Die Ton-Leitung eines TCP-Geraets
// (role=audio) wertet das Signal nie aus; Nummern und Rueckmeldungen waeren dort nur Last auf dem Uplink.
func TestTCPStauNurBildBein(t *testing.T) {
	s := testNodeServer(t, true)
	base := "https://" + s.mux.Addr().String()
	startBots(t, base, "stau", []string{"tcp", "tcp"}, 4, "l", "m")
	var au, vi []*leg
	deadline := time.Now().Add(10 * time.Second)
	for {
		au, vi = nil, nil
		s.mu.Lock()
		for _, l := range s.legs {
			switch l.role {
			case "audio":
				au = append(au, l)
			case "video":
				vi = append(vi, l)
			}
		}
		s.mu.Unlock()
		fed := len(vi) == 2
		for _, l := range vi {
			if m := l.tcpMeter(); m == nil || m.Counters().Feedbacks == 0 {
				fed = false
			}
		}
		if fed || time.Now().After(deadline) {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if len(au) != 2 || len(vi) != 2 {
		t.Fatalf("beine: %d Ton, %d Bild", len(au), len(vi))
	}
	for _, l := range au {
		if l.tcpMeter() != nil {
			t.Fatalf("Ton-Bein %s hat das Signal", l.id)
		}
	}
	for _, l := range vi {
		m := l.tcpMeter()
		if m == nil {
			t.Fatalf("Bild-Bein %s ohne Signal", l.id)
		}
		if c := m.Counters(); c.Feedbacks == 0 || c.Acked == 0 {
			t.Fatalf("Bild-Bein %s: keine Rueckmeldungen %+v", l.id, c)
		}
	}
}
