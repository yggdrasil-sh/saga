package main

import (
	"encoding/binary"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// HORN_BWE: Wer die Leitung eines UDP-Beins beurteilt ("Weg B").
//
//	gcc  (Standard) pion-GCC allein, wie bisher - der Mitleser wird gar nicht eingebaut.
//	mit  Stufe 0: Der eigene Stau-Detektor (internal/bwe) liest die transport-cc-Rueckmeldungen mit und schreibt
//	     sein Urteil ins Log (Zeile "bwe" je Zustandswechsel, Zusatz in der Zeile "probe"). Er entscheidet nichts.
//	eigen  Stufe 1 und 2: wie mit, und der Detektor entscheidet mit - er wertet die Proben (GCC haelt nur noch die
//	     Fuellung an) und widerlegt einen Fall des GCC-Ziels, den die Leitung nicht zeigt (sfu.Leg.ExtTip, ExtSpan).
//
// Bei mit und eigen kommt dazu je Ruecknahme einer Schicht eine Zeile "schicht" mit Grund, bei eigen je Widerlegung
// eine Zeile "bwe ... widerlegung=beginn|ende" (legEventLine).
//
// Jeder andere Wert gilt als gcc (mit Warnzeile).
const (
	bweGCC   = "gcc"
	bweMit   = "mit"
	bweEigen = "eigen"
)

// parseBWE liefert die Betriebsart und, wenn der Wert unbekannt ist, eine Warnung.
func parseBWE(v string) (mode, warn string) {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "", bweGCC:
		return bweGCC, ""
	case bweMit:
		return bweMit, ""
	case bweEigen:
		return bweEigen, ""
	}
	return bweGCC, fmt.Sprintf("HORN_BWE=%q unbekannt (gcc, mit oder eigen) - nutze gcc", v)
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
	m    *bwe.Mitleser
	mode string // bweMit | bweEigen

	pairMu sync.Mutex
	pair   string // zuletzt gewaehltes Adresspaar (pairChanged)
}

// pairChanged: Hat ICE ein anderes Adresspaar gewaehlt als zuletzt? Die erste Wahl zaehlt als Wechsel, dasselbe Paar
// noch einmal nicht - sonst verloere der Detektor ohne Not seine Basis, und eine stehende Schlange ginge in der neuen
// Basis auf.
func (b *bweWrap) pairChanged(cp *webrtc.ICECandidatePair) bool {
	if cp == nil || cp.Local == nil || cp.Remote == nil {
		return false
	}
	key := fmt.Sprintf("%s %s:%d > %s %s:%d", cp.Local.Protocol, cp.Local.Address, cp.Local.Port, cp.Remote.Protocol, cp.Remote.Address, cp.Remote.Port)
	b.pairMu.Lock()
	defer b.pairMu.Unlock()
	if key == b.pair {
		return false
	}
	b.pair = key
	return true
}

func newBWEWrap(est cc.BandwidthEstimator, mode string) *bweWrap {
	return &bweWrap{BandwidthEstimator: est, m: bwe.NewMitleser(bwe.Defaults()), mode: mode}
}

// dupWin merkt sich die zuletzt gesendeten RTP-Folgenummern eines Stroms (je Platz die Nummer + 1, 0 = leer): Geht
// eine Nummer ein zweites Mal hinaus, ist das Paket ein Doppel. Der Empfaenger verwirft es am
// SRTP-Wiederholungsschutz, bevor er es zaehlt oder per transport-cc bestaetigt - es saehe wie Verlust aus (Labor
// labbwe1, 7.1 und 7.2: Der sendende Browser wiederholt alte Pakete ueber RTX, pion gibt sie entpackt mit der
// urspruenglichen Nummer zurueck, und der Weiterleiter reichte jedes Exemplar weiter).
//
// Seit dem Abgleich je Quelle (fwd.Source.admit) erreicht kein Doppel der Quelle mehr die Huelle, und der Weiterleiter
// vergibt keine Folgenummer zweimal. Das Fenster bleibt als Messwert: sent.dup und dup_unacked sollten 0 sein - alles
// andere hiesse, der Weiterleiter hat doch eine Nummer doppelt vergeben. Der Detektor wertet ein solches Paket, dessen
// Bestaetigung ausbleibt, weiter nicht als Verlust.
type dupWin [1024]uint32

func (d *dupWin) seen(seq uint16) bool {
	e := &d[seq&1023]
	if *e == uint32(seq)+1 {
		return true
	}
	*e = uint32(seq) + 1
	return false
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
	dups := &dupWin{} // je Strom; der Schreiber eines Beins ruft nacheinander
	var dupMu sync.Mutex
	return interceptor.RTPWriterFunc(func(h *rtp.Header, pl []byte, a interceptor.Attributes) (int, error) {
		dup := false
		if raw := h.GetExtension(ext); len(raw) >= 2 {
			k := kind
			switch {
			case h.Padding && len(pl) == fwd.PadSize && int(pl[len(pl)-1]) == len(pl):
				k = bwe.KindPad
			case rtxSSRC != 0 && h.SSRC == rtxSSRC || rtxPT != 0 && h.PayloadType == rtxPT:
				k = bwe.KindRTX
			}
			dupMu.Lock()
			if dup = dups.seen(h.SequenceNumber); dup {
				k = bwe.KindDup
			}
			dupMu.Unlock()
			b.m.Sent(bweNow(), binary.BigEndian.Uint16(raw), h.MarshalSize()+len(pl), k)
		}
		// Ein Doppel geht wie jedes Paket durch pion-GCC. (Bis zur Nacharbeit ging es bei eigen an GCC vorbei - ohne
		// Wirkung: pion fuehrt die transport-cc-Nummer aus der Rueckmeldung auch dann als verloren, wenn es das Paket nie
		// gesehen hat; TestPruefDoppelGCCVerlust. Gegen den falschen Verlust hilft nur der Abgleich im Weiterleiter.)
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

// onLegEvent (sfu.Config.OnLegEvent): eine Zeile je Beginn und Ende einer Widerlegung (HORN_BWE=eigen) und je
// Ruecknahme einer Schicht, mit Grund. Nur fuer Beine mit eigenem Stau-Detektor (HORN_BWE=mit bzw. eigen) - bei gcc
// bleibt das Log, wie es war. Bein als Kurzkennung wie in "bein auf"; keine Kennungen von Personen, Geraeten oder
// Raeumen, keine Adressen.
func (s *server) onLegEvent(ev sfu.LegEvent) {
	s.mu.Lock()
	l := s.bySFU[ev.Leg]
	s.mu.Unlock()
	if l == nil {
		return
	}
	b := l.bweWrap()
	if b == nil {
		return
	}
	log.Print(legEventLine(shortID(l.id), ev, b.m.Tip(bweNow())))
}

// legEventLine: Zeile zu einem Ereignis der Steuerung, mit dem, was der Detektor in diesem Augenblick an der Spitze
// sieht (Zustand, Schlange, Verlust der letzten 500 ms; Schlange -1 = nichts Frisches).
//
//	bwe 5884a4 widerlegung=beginn art=spitze gcc=1130k rate=1330k est=1480k zustand=frei schlange=1ms verlust=0.0%
//	bwe 5884a4 widerlegung=ende art=spitze grund=erholt dauer=1.6s gcc=1250k rate=1330k est=1480k zustand=frei schlange=1ms verlust=0.0%
//	schicht 5884a4 platz=speaker h>m grund=gcc-fall est=1100k gcc=900k rate=1330k zustand=frei schlange=24ms verlust=3.1%
func legEventLine(id string, ev sfu.LegEvent, tip bwe.Tip) string {
	q := int64(-1)
	if tip.QueueUs >= 0 {
		q = tip.QueueUs / 1000
	}
	tail := fmt.Sprintf("gcc=%dk rate=%dk est=%dk zustand=%s schlange=%dms verlust=%.1f%%", ev.GccKbit, ev.RateKbit, ev.EstKbit, tip.State, q, tip.Loss*100)
	switch ev.Kind {
	case sfu.LegRefute:
		return fmt.Sprintf("bwe %s widerlegung=beginn art=%s %s", id, ev.Art, tail)
	case sfu.LegRefuteEnd:
		return fmt.Sprintf("bwe %s widerlegung=ende art=%s grund=%s dauer=%.1fs %s", id, ev.Art, ev.Why, ev.Dur.Seconds(), tail)
	}
	from, to := ev.From, ev.To
	if from == "" {
		from = "p"
	}
	if to == "" {
		to = "p"
	}
	return fmt.Sprintf("schicht %s platz=%s %s>%s grund=%s est=%dk gcc=%dk rate=%dk zustand=%s schlange=%dms verlust=%.1f%%",
		id, ev.Class, from, to, ev.Why, ev.EstKbit, ev.GccKbit, ev.RateKbit, tip.State, q, tip.Loss*100)
}

// bweProbe: Zusatz zur Zeile "probe" - Urteil des Detektors ueber die Pakete der Probe nach dem Nachlauf (v) und
// das Urteil, das er beim Ende der Probe schon hatte (sofort).
func bweProbe(v, sofort bwe.Verdict) string {
	return fmt.Sprintf(" bwe=%s bwe_schlange=%dms bwe_verlust=%.1f%% bwe_durchsatz=%dk bwe_zustand=%s bwe_fenster=%d bwe_sofort=%s",
		v.Result, v.QueueUs/1000, v.Loss*100, v.Bps/1000, v.WorstSt, v.Windows, sofort.Result)
}

// bweProbeEigen: Zusatz zur Zeile "probe" bei HORN_BWE=eigen - das Urteil, nach dem die Probe gewertet wurde (bwe=,
// "-" = vor dem Urteil beendet), das Stausignal von GCC, das die Fuellung angehalten hat (bwe_gcc=, "-" = keins), und
// der Durchsatz-Nachweis: bei voller Fuellung gesendet und im selben Zeitraum bestaetigt (0 = volle Fuellung nicht
// erreicht). Schlange, Verlust, Zustand und Fenster wie bei mit, ueber die ganze Probe samt Nachlauf.
func bweProbeEigen(ev sfu.ProbeEvent, v bwe.Verdict) string {
	det, sig := ev.Det, ev.GccSig
	if det == "" {
		det = "-"
	}
	if sig == "" {
		sig = "-"
	}
	return fmt.Sprintf(" bwe=%s bwe_gcc=%s bwe_gesendet=%dk bwe_bestaetigt=%dk bwe_schlange=%dms bwe_verlust=%.1f%% bwe_zustand=%s bwe_fenster=%d",
		det, sig, ev.SentKbit, ev.AckedKbit, v.QueueUs/1000, v.Loss*100, v.WorstSt, v.Windows)
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

// bweHooks (eigen): Der Detektor entscheidet mit - die Steuerung (internal/sfu) fragt ihn je Takt, ob die juengsten
// Rueckmeldungen einen Stau widerlegen (ExtTip), und am Ende einer Probe nach Urteil und Raten ueber deren Pakete
// (ExtSpan; Zeiten als Abstand zu jetzt, die Steuerung hat eine andere Uhr).
func bweHooks(sl *sfu.Leg, b *bweWrap) {
	sl.ExtTip = func() bwe.Tip { return b.m.Tip(bweNow()) }
	sl.ExtSpan = func(from, to, rateFrom time.Duration) (bwe.Verdict, bwe.Rates) {
		now := bweNow()
		return b.m.Span(now-int64(from), now-int64(to)), b.m.Rates(now-int64(rateFrom), now-int64(to))
	}
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
		"mode": b.mode, "state": s.State.String(), "why": s.Why, "queue_ms": s.QueueUs / 1000, "loss": s.Loss,
		"acked_kbit": s.AckedBps / 1000, "cap_kbit": s.CapBps / 1000, "age_ms": age,
		"sent":        map[string]uint64{"video": c.Sent[bwe.KindVideo], "audio": c.Sent[bwe.KindAudio], "rtx": c.Sent[bwe.KindRTX], "pad": c.Sent[bwe.KindPad], "dup": c.Sent[bwe.KindDup]},
		"dup_unacked": c.DupUnacked,
		"feedbacks":   c.Feedbacks, "bad": c.Bad, "acked": c.Acked, "lost": c.Lost, "dup": c.Dup, "recovered": c.Recovered, "unknown": c.Unknown,
		"windows": c.Windows, "thin": c.Thin, "late": c.Late, "resets": c.Resets,
		"to_free": c.ToFree, "to_warn": c.ToWarn, "to_cong": c.ToCong, "to_unknown": c.ToUnknown,
	}
}
