package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// Ende-zu-Ende-Stausignal fuer die TCP-Wege (ICE-TCP, TURN-TLS; Saga 2.5).
//
//	HORN_TCP_STAU     Standard an; "0" = aus (Gegenprobe: TCP-Wege wie bis 2.5 ohne transport-cc auf dem Downlink);
//	                  "mit" = nur messen (Zaehler, Log), die Schaetzung bleibt bei der eigenen Warteschlange
//	HORN_TCP_STAU_LOG "1" = je Takt und Bein eine Zeile "tcpstau ..." (Messlaeufe)
//	HORN_TCP_STAU_WS  Standard an: auch auf dem Tunnel-Weg ws (A3); "0" = dort aus (nur das Fenster des Tunnels wie
//	                  bis 2.5). Mit Signal quittiert die App seltener (tunnelAckSig): Die Rueckmeldungen des Geraets
//	                  kosten auf dem Uplink rund 5-6 kbit/s, seltenere Quittungen sparen mehr (NODE.md, a3-stau)
//
// Auf TCP-Wegen traegt jedes abgehende Paket die transport-cc-Folgenummer (wie auf UDP), das Geraet meldet die
// Ankunft. Ein Mitleser (internal/bwe, ohne GCC) ordnet die Rueckmeldungen zu; daraus liest das Bein je Takt die
// Schlange unterwegs und gesendete gegen bestaetigte Rate (sfu.TCPSig, Regel in internal/sfu/tcpstau.go).
const (
	tcpStauOff = "0"
	tcpStauMit = "mit"
)

// tcpStauWS: Signal auch auf dem Tunnel-Weg ws? Standard an, HORN_TCP_STAU_WS=0 aus.
func tcpStauWS() bool { return strings.TrimSpace(os.Getenv("HORN_TCP_STAU_WS")) != "0" }

// tunnelAckSig: Quittungsabstand der Tunnel (ack_ms der Antwort ok), wenn ihre Beine das Signal bekommen. Gemessen
// (Pruefstand A3, Uplink 500 kbit/s): mit 10 ms quittiert die App rund 64-mal je Sekunde (Ton- und Bild-Tunnel, je
// Quittung ~37 Byte mit WS-Kopf und TLS-Satz, zusammen ~19 kbit/s) - mehr als die transport-cc-Rueckmeldungen kosten.
// Das Fenster (mindestens 64 KiB) bleibt weit offen; den Stau sieht das Signal, nicht der Quittungstakt.
const tunnelAckSig = 50 * time.Millisecond

func tcpStauMode() string {
	switch v := strings.TrimSpace(os.Getenv("HORN_TCP_STAU")); v {
	case tcpStauOff, tcpStauMit:
		return v
	}
	return ""
}

// tcpHook verbindet den Mitleser einer neuen PeerConnection mit ihrem Bein (der Rueckruf kommt synchron in
// NewPeerConnection, wie beim gccHook).
type tcpHook struct {
	mu   sync.Mutex
	next func(*bwe.Mitleser)
	on   bool   // fuer die naechste PeerConnection: Folgenummern und Mitleser (sonst reicht der Abgriff nur durch)
	mode string // tcpStauMode beim Start
	ws   bool   // HORN_TCP_STAU_WS=1: auch Beine "ws" (A3)
}

// tcpCCFactory baut je PeerConnection einen Abgriff mit eigenem Mitleser.
type tcpCCFactory struct{ hook *tcpHook }

// tunnelAckDelay: ack_ms fuer neue Tunnel - seltener, wenn Beine "ws" Folgenummern bekommen (auch HORN_TCP_STAU=mit:
// die Rueckmeldungen kosten dort ebenso), sonst wie bis 2.5.
func (s *server) tunnelAckDelay() time.Duration {
	if s.tcp.ws && s.tcp.mode != tcpStauOff {
		return tunnelAckSig
	}
	return wstun.DefAckDelay
}

func (f tcpCCFactory) NewInterceptor(_ string) (interceptor.Interceptor, error) {
	if f.hook == nil || !f.hook.on { // f.hook.mu haelt newSFULeg
		return &tcpCC{}, nil
	}
	m := bwe.NewMitleser(bwe.Defaults())
	if f.hook.next != nil {
		f.hook.next(m)
	}
	return &tcpCC{m: m}, nil
}

// tcpCC: setzt auf jedes abgehende Paket die transport-cc-Folgenummer (eine Folge je PeerConnection, wie auf UDP),
// merkt sich Nummer, Sendezeit und Groesse und reicht jede transport-cc-Rueckmeldung an den Mitleser. Ohne Mitleser
// (Bein ohne Signal) reicht er alles unveraendert durch - die Pakete gehen dann ohne Nummer hinaus wie bis 2.5.
type tcpCC struct {
	interceptor.NoOp
	m   *bwe.Mitleser
	seq atomic.Uint32
}

func (t *tcpCC) BindLocalStream(info *interceptor.StreamInfo, w interceptor.RTPWriter) interceptor.RTPWriter {
	var ext uint8
	for _, e := range info.RTPHeaderExtensions {
		if e.URI == sdp.TransportCCURI {
			ext = uint8(e.ID)
			break
		}
	}
	if ext == 0 || t.m == nil {
		return w
	}
	kind := bwe.KindVideo
	if strings.HasPrefix(strings.ToLower(info.MimeType), "audio/") {
		kind = bwe.KindAudio
	}
	var buf [2]byte // je Strom; die Kette schreibt ein Paket nach dem anderen (der Kopf haelt den Verweis nur bis dahin)
	return interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, a interceptor.Attributes) (int, error) {
		n := uint16(t.seq.Add(1) - 1)
		binary.BigEndian.PutUint16(buf[:], n)
		if err := h.SetExtension(ext, buf[:]); err != nil {
			return 0, err
		}
		t.m.Sent(bweNow(), n, h.MarshalSize()+len(pl), kind)
		return w.Write(h, pl, a)
	})
}

func (t *tcpCC) BindRTCPReader(r interceptor.RTCPReader) interceptor.RTCPReader {
	if t.m == nil {
		return r
	}
	return interceptor.RTCPReaderFunc(func(b []byte, a interceptor.Attributes) (int, interceptor.Attributes, error) {
		n, a, err := r.Read(b, a)
		if err != nil {
			return n, a, err
		}
		if a == nil {
			a = interceptor.Attributes{}
		}
		pkts, perr := a.GetRTCPPackets(b[:n])
		if perr != nil {
			return n, a, nil
		}
		for _, p := range pkts {
			if fb, ok := p.(*rtcp.TransportLayerCC); ok {
				t.m.Feedback(bweNow(), fb)
			}
		}
		return n, a, nil
	})
}

// configureTCPStau: der Abgriff, der auf den TCP-Wegen die transport-cc-Folgenummern setzt (die Kopf-Erweiterung ist
// schon ausgehandelt: ConfigureTWCCSender meldet sie fuer die Rueckmeldungen an den Uplink an). Bei HORN_TCP_STAU=0
// nichts davon (die TCP-Wege senden wie bis 2.5 ohne Nummer).
func configureTCPStau(m *webrtc.MediaEngine, ir *interceptor.Registry, hook *tcpHook) error {
	if hook == nil || hook.mode == tcpStauOff {
		return nil
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.TransportCCURI}, webrtc.RTPCodecTypeVideo); err != nil {
		return err
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.TransportCCURI}, webrtc.RTPCodecTypeAudio); err != nil {
		return err
	}
	ir.Add(tcpCCFactory{hook})
	return nil
}

// tcpSpan: Spanne (Sendezeit) fuer gesendete gegen bestaetigte Rate.
const tcpSpan = int64(1e9)

// tcpSig liest das Signal eines Mitlesers fuer den Takt der Steuerung.
func tcpSig(m *bwe.Mitleser) sfu.TCPSig {
	now := bweNow()
	tip := m.Tip(now)
	snap := m.Snapshot(now)
	r := m.Recent(tcpSpan)
	s := sfu.TCPSig{SentBps: float64(r.SentBps), AckedBps: float64(r.AckedBps)}
	if snap.AgeNs >= 0 {
		s.FbAge = toDur(snap.AgeNs)
	}
	if tip.QueueUs >= 0 && snap.AgeNs >= 0 {
		s.OK = true
		s.Queue = toDur(tip.QueueUs * 1000)
	}
	return s
}

// tcpAttach haengt das Signal an ein Bein (Modus "mit": nur Zaehler und Log, keine Wirkung auf die Schaetzung).
func tcpAttach(l *leg, sl *sfu.Leg, m *bwe.Mitleser, mode string) {
	l.mu.Lock()
	l.tcpm = m
	l.mu.Unlock()
	logOn := os.Getenv("HORN_TCP_STAU_LOG") == "1"
	id := shortID(l.id)
	f := func() sfu.TCPSig {
		s := tcpSig(m)
		if logOn {
			// Takt der Steuerung (unter der Sperre des Raums): nur das Signal, keine Kennungen ausser der Kurzkennung
			// rr: Umlaufzeit aus dem letzten Empfaengerbericht (RTCP, zum Vergleich)
			rr := time.Duration(l.rttNs.Load())
			go log.Printf("tcpstau %s ok=%t q=%dms sent=%dk ack=%dk fb=%dms rr=%dms", id, s.OK, s.Queue.Milliseconds(),
				int(s.SentBps/1000), int(s.AckedBps/1000), s.FbAge.Milliseconds(), rr.Milliseconds())
		}
		if mode == tcpStauMit {
			s.OK = false
		}
		return s
	}
	sl.ExtQueue = f
}

// tcpJSON: Zustand des Signals fuer GET /api/sfu/leg/{id}.
func tcpJSON(sl *sfu.Leg, m *bwe.Mitleser) map[string]any {
	st := sl.TCPStau()
	c := m.Counters()
	return map[string]any{"on": st.On, "queue_ms": st.QueueMs, "sent_kbit": int(st.SentKbit), "ack_kbit": int(st.AckKbit),
		"downs": st.Downs, "holds": st.Holds, "stale": st.Stale, "feedbacks": c.Feedbacks, "acked": c.Acked, "lost": c.Lost,
		"unknown": c.Unknown, "mode": fmt.Sprint(tcpStauMode())}
}

func toDur(ns int64) time.Duration { return time.Duration(ns) }

// tcpWants: Braucht ein Bein mit diesen Ausgaengen das Signal? Nur, wenn es Bild sendet - die Schaetzung waehlt nur
// fuer Bildplaetze Schichten (planLeg).
func tcpWants(outs []*fwd.Out) bool {
	for _, o := range outs {
		if o.Kind == fwd.Video {
			return true
		}
	}
	return false
}
