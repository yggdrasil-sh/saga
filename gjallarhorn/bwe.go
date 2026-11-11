package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// HORN_BWE: Wer die Leitung eines UDP-Beins beurteilt ("Weg B").
//
//	gcc  (Standard) pion-GCC allein, wie bisher - der Mitleser wird gar nicht eingebaut.
//	mit  Stufe 0: Der eigene Stau-Detektor (internal/bwe) liest die transport-cc-Rueckmeldungen mit und schreibt
//	     sein Urteil ins Log (Zeile "bwe" je Zustandswechsel, Zusatz in der Zeile "probe"). Er entscheidet nichts.
//
// Jeder andere Wert gilt als gcc (mit Warnzeile).
const (
	bweGCC = "gcc"
	bweMit = "mit"
)

// parseBWE liefert die Betriebsart und, wenn der Wert unbekannt ist, eine Warnung.
func parseBWE(v string) (mode, warn string) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "", bweGCC:
		return bweGCC, ""
	case bweMit:
		return bweMit, ""
	}
	return bweGCC, fmt.Sprintf("HORN_BWE=%q unbekannt (gcc oder mit) - nutze gcc", v)
}

// bweEpoch: Nullpunkt der Sendezeiten des Mitlesers (Prozessuhr, monoton).
var bweEpoch = time.Now()

func bweNow() int64 { return int64(time.Since(bweEpoch)) }

// bweWrap ist die Huelle um den pion-Schaetzer (cc.BandwidthEstimator): Sie merkt sich jedes abgehende Paket
// (TWCC-Folgenummer aus dem Kopf, Sendezeit, Groesse, Art) und liest jede transport-cc-Rueckmeldung mit; alles
// andere - und beides danach unveraendert - geht an pion-GCC. Die Folgenummer steht beim Durchlauf schon im Kopf:
// newSFUAPI traegt die Kopf-Erweiterung nach cc ein, sie liegt in der Kette also aussen.
//
// Nicht sichtbar sind hier (wie fuer pion-GCC selbst) die Wiederholungen des NACK-Antworters: Er ist vor cc
// eingetragen und schreibt an cc vorbei.
type bweWrap struct {
	cc.BandwidthEstimator
	m *bwe.Mitleser
}

func newBWEWrap(est cc.BandwidthEstimator) *bweWrap {
	return &bweWrap{BandwidthEstimator: est, m: bwe.NewMitleser(bwe.Defaults())}
}

func (b *bweWrap) AddStream(info *interceptor.StreamInfo, w interceptor.RTPWriter) interceptor.RTPWriter {
	inner := b.BandwidthEstimator.AddStream(info, w)
	var ext uint8
	for _, e := range info.RTPHeaderExtensions {
		if e.URI == sdp.TransportCCURI {
			ext = uint8(e.ID)
			break
		}
	}
	if ext == 0 {
		return inner
	}
	kind := bwe.KindVideo
	if strings.HasPrefix(strings.ToLower(info.MimeType), "audio/") {
		kind = bwe.KindAudio
	}
	rtxPT, rtxSSRC := info.PayloadTypeRetransmission, info.SSRCRetransmission
	return interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, a interceptor.Attributes) (int, error) {
		if raw := h.GetExtension(ext); len(raw) >= 2 {
			k := kind
			switch {
			case h.Padding && len(pl) == fwd.PadSize && int(pl[len(pl)-1]) == len(pl):
				k = bwe.KindPad
			case rtxSSRC != 0 && h.SSRC == rtxSSRC || rtxPT != 0 && h.PayloadType == rtxPT:
				k = bwe.KindRTX
			}
			b.m.Sent(bweNow(), binary.BigEndian.Uint16(raw), h.MarshalSize()+len(pl), k)
		}
		return inner.Write(h, pl, a)
	})
}

func (b *bweWrap) WriteRTCP(pkts []rtcp.Packet, a interceptor.Attributes) error {
	for _, p := range pkts {
		if fb, ok := p.(*rtcp.TransportLayerCC); ok {
			b.m.Feedback(bweNow(), fb)
		}
	}
	return b.BandwidthEstimator.WriteRTCP(pkts, a)
}

// bweLine: Zeile je Zustandswechsel des Detektors - Bein (Kurzkennung wie "bein auf"), Zustand, Grund, Schlange,
// Verlust, bestaetigter Durchsatz, Kapazitaet beim letzten Stau, daneben GCC-Ziel und Schaetzung des Knotens. Keine
// Kennungen von Personen, Geraeten oder Raeumen, keine Adressen.
func bweLine(id string, c bwe.Change, gccBps, estKbit int) string {
	s := c.Snap
	return fmt.Sprintf("bwe %s zustand=%s vorher=%s grund=%s schlange=%dms verlust=%.1f%% durchsatz=%dk kapazitaet=%dk gcc=%dk est=%dk",
		id, c.To, c.From, c.Why, s.QueueUs/1000, s.Loss*100, s.AckedBps/1000, s.CapBps/1000, gccBps/1000, estKbit)
}

// bweProbe: Zusatz zur Zeile "probe" - Urteil des Detektors ueber die Pakete der Probe nach dem Nachlauf (v) und
// das Urteil, das er beim Ende der Probe schon hatte (sofort).
func bweProbe(v, sofort bwe.Verdict) string {
	return fmt.Sprintf(" bwe=%s bwe_schlange=%dms bwe_verlust=%.1f%% bwe_durchsatz=%dk bwe_zustand=%s bwe_fenster=%d bwe_sofort=%s",
		v.Result, v.QueueUs/1000, v.Loss*100, v.Bps/1000, v.WorstSt, v.Windows, sofort.Result)
}

// bweTail: Nachlauf nach einer Probe, bis die Rueckmeldungen zu ihren letzten Paketen da sind - zwei Umlaufzeiten
// + 200 ms, hoechstens 1 s (ohne bekannte Umlaufzeit 500 ms).
func bweTail(rtt time.Duration) time.Duration {
	if rtt <= 0 {
		return 500 * time.Millisecond
	}
	return min(2*rtt+200*time.Millisecond, time.Second)
}

// bweAttach verbindet den Mitleser eines neuen UDP-Beins mit dem Bein: Logzeile je Zustandswechsel. Die Zeile
// entsteht in einer eigenen Goroutine - der Wechsel wird im Sende- bzw. RTCP-Pfad bemerkt, und die Schaetzung des
// Knotens zu lesen braucht die Sperre des Raums.
func bweAttach(l *leg, b *bweWrap, est func() int, estKbit func() int) {
	id := shortID(l.id)
	b.m.SetOnChange(func(c bwe.Change) {
		go func() { log.Print(bweLine(id, c, est(), estKbit())) }()
	})
}

// bweJSON: Zaehler und Zustand fuer GET /api/sfu/leg/{id}.
func bweJSON(b *bweWrap) map[string]any {
	s := b.m.Snapshot(bweNow())
	c := b.m.Counters()
	age := int64(-1)
	if s.AgeNs >= 0 {
		age = s.AgeNs / 1e6
	}
	return map[string]any{
		"mode": bweMit, "state": s.State.String(), "why": s.Why, "queue_ms": s.QueueUs / 1000, "loss": s.Loss,
		"acked_kbit": s.AckedBps / 1000, "cap_kbit": s.CapBps / 1000, "age_ms": age,
		"sent":      map[string]uint64{"video": c.Sent[bwe.KindVideo], "audio": c.Sent[bwe.KindAudio], "rtx": c.Sent[bwe.KindRTX], "pad": c.Sent[bwe.KindPad]},
		"feedbacks": c.Feedbacks, "bad": c.Bad, "acked": c.Acked, "lost": c.Lost, "dup": c.Dup, "recovered": c.Recovered, "unknown": c.Unknown,
		"windows": c.Windows, "thin": c.Thin, "late": c.Late, "resets": c.Resets,
		"to_free": c.ToFree, "to_warn": c.ToWarn, "to_cong": c.ToCong, "to_unknown": c.ToUnknown,
	}
}
