package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Ende-zu-Ende-Stausignal fuer die TCP-Wege (ICE-TCP, TURN-TLS; Saga 2.5).
//
//	HORN_TCP_STAU     Standard an; "0" = aus (Gegenprobe: TCP-Wege wie bis 2.5 ohne transport-cc auf dem Downlink);
//	                  "mit" = nur messen (Zaehler, Log), die Schaetzung bleibt bei der eigenen Warteschlange
//	HORN_TCP_STAU_LOG "1" = je Takt und Bein eine Zeile "tcpstau ..." (Messlaeufe)
//
// Auf TCP-Wegen traegt jedes abgehende Paket die transport-cc-Folgenummer (wie auf UDP), das Geraet meldet die
// Ankunft. Ein Mitleser (internal/bwe, ohne GCC) ordnet die Rueckmeldungen zu; daraus liest das Bein je Takt die
// Schlange unterwegs und gesendete gegen bestaetigte Rate (sfu.TCPSig, Regel in internal/sfu/tcpstau.go).
const (
	tcpStauOff = "0"
	tcpStauMit = "mit"
)

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
	mode string // tcpStauMode beim Start
}

// tcpCCFactory baut je PeerConnection einen Abgriff mit eigenem Mitleser.
type tcpCCFactory struct{ hook *tcpHook }

func (f tcpCCFactory) NewInterceptor(_ string) (interceptor.Interceptor, error) {
	m := bwe.NewMitleser(bwe.Defaults())
	if f.hook != nil && f.hook.next != nil { // f.hook.mu haelt newSFULeg
		f.hook.next(m)
	}
	return &tcpCC{m: m}, nil
}

// tcpCC: merkt sich jedes abgehende Paket mit transport-cc-Folgenummer (die Kopf-Erweiterung liegt in der Kette
// aussen, die Nummer steht beim Durchlauf schon im Kopf) und reicht jede transport-cc-Rueckmeldung an den Mitleser.
// Aendert nichts an Paketen oder Rueckmeldungen.
type tcpCC struct {
	interceptor.NoOp
	m *bwe.Mitleser
}

func (t *tcpCC) BindLocalStream(info *interceptor.StreamInfo, w interceptor.RTPWriter) interceptor.RTPWriter {
	var ext uint8
	for _, e := range info.RTPHeaderExtensions {
		if e.URI == sdp.TransportCCURI {
			ext = uint8(e.ID)
			break
		}
	}
	if ext == 0 {
		return w
	}
	kind := bwe.KindVideo
	if strings.HasPrefix(strings.ToLower(info.MimeType), "audio/") {
		kind = bwe.KindAudio
	}
	return interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, a interceptor.Attributes) (int, error) {
		if raw := h.GetExtension(ext); len(raw) >= 2 {
			t.m.Sent(bweNow(), binary.BigEndian.Uint16(raw), h.MarshalSize()+len(pl), kind)
		}
		return w.Write(h, pl, a)
	})
}

func (t *tcpCC) BindRTCPReader(r interceptor.RTCPReader) interceptor.RTCPReader {
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

// configureTCPStau: transport-cc-Folgenummern auf den abgehenden Paketen der TCP-Wege und der Abgriff davor (innen),
// damit er die Nummer sieht. Bei HORN_TCP_STAU=0 nichts davon (die TCP-Wege senden wie bis 2.5 ohne Nummer).
func configureTCPStau(m *webrtc.MediaEngine, ir *interceptor.Registry, hook *tcpHook) error {
	if hook == nil || hook.mode == tcpStauOff {
		return nil
	}
	ir.Add(tcpCCFactory{hook})
	return webrtc.ConfigureTWCCHeaderExtensionSender(m, ir)
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
			go log.Printf("tcpstau %s ok=%t q=%dms sent=%dk ack=%dk fb=%dms", id, s.OK, s.Queue.Milliseconds(),
				int(s.SentBps/1000), int(s.AckedBps/1000), s.FbAge.Milliseconds())
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
