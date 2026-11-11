package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/nack"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

const (
	mimeOpus = webrtc.MimeTypeOpus
	mimeVP8  = webrtc.MimeTypeVP8
)

// nackMaxJePaket begrenzt, wie oft der Knoten ein fehlendes Paket nachfordert (alle 100 ms, also rund 1 s lang;
// libwebrtc haelt es ebenso mit 10 Versuchen). Ohne Grenze fordert pion Luecken eines Stroms endlos nach, sobald
// dieser nichts Neues mehr sendet (pausierte Simulcast-Ebene, Kamera aus): Das Fenster rueckt dann nie weiter, und
// die Neuuebertragungen des Geraets zehren dauerhaft an seiner Senderate.
const nackMaxJePaket = 10

func nackGenOpts() []nack.GeneratorOption {
	return []nack.GeneratorOption{nack.GeneratorMaxNacksPerPacket(nackMaxJePaket)}
}

// newAPI baut eine pion-API je Weg. nack=false fuer TCP-Beine (H5): keine
// NACK/RTX, Opus ohne FEC, ptime 40, DTX an.
func newAPI(se webrtc.SettingEngine, nack bool) (*webrtc.API, error) {
	m := &webrtc.MediaEngine{}
	opusFmtp := "minptime=10;useinbandfec=1"
	if !nack {
		opusFmtp = "minptime=40;ptime=40;useinbandfec=0;usedtx=1"
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{
		RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2, SDPFmtpLine: opusFmtp,
			RTCPFeedback: []webrtc.RTCPFeedback{{Type: "transport-cc"}}},
		PayloadType: 111,
	}, webrtc.RTPCodecTypeAudio); err != nil {
		return nil, err
	}
	fb := []webrtc.RTCPFeedback{{Type: "transport-cc"}, {Type: "ccm", Parameter: "fir"}, {Type: "nack", Parameter: "pli"}}
	if nack {
		fb = append(fb, webrtc.RTCPFeedback{Type: "nack"})
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{
		RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000, RTCPFeedback: fb},
		PayloadType:        96,
	}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	if nack {
		if err := m.RegisterCodec(webrtc.RTPCodecParameters{
			RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000, SDPFmtpLine: "apt=96"},
			PayloadType:        97,
		}, webrtc.RTPCodecTypeVideo); err != nil {
			return nil, err
		}
	}
	if err := webrtc.ConfigureSimulcastExtensionHeaders(m); err != nil {
		return nil, err
	}
	ir := &interceptor.Registry{}
	if nack {
		if err := webrtc.ConfigureNackWithOptions(m, ir, nackGenOpts()); err != nil {
			return nil, err
		}
	}
	if err := webrtc.ConfigureRTCPReports(ir); err != nil {
		return nil, err
	}
	// Transport-CC-Rueckmeldung, damit das Geraet seine Senderate hochfaehrt
	if err := webrtc.ConfigureTWCCSender(m, ir); err != nil {
		return nil, err
	}
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), nil
}

// playout-delay (P9): auf TCP-/TLS-Beinen fuer Bild min/max in ms (HORN_PLAYOUT_DELAY="150,400", "0" = aus). Ersetzt fuer
// Apps ohne jitterBufferTarget die Gegenmassnahme gegen Einfrieren auf TCP (Messung G4).
const playoutDelayURI = "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay"

var playoutMin, playoutMax = func() (int, int) {
	v := strings.TrimSpace(env("HORN_PLAYOUT_DELAY", "150,400"))
	a, b, ok := strings.Cut(v, ",")
	x, e1 := strconv.Atoi(strings.TrimSpace(a))
	y, e2 := strconv.Atoi(strings.TrimSpace(b))
	if v == "0" || !ok || e1 != nil || e2 != nil || x < 0 || y < x || y > 10000 {
		return 0, 0
	}
	return x, y
}()

// trackOut verbindet einen fwd.Out mit pion (eigene TrackLocal ohne Puffer).
type trackOut struct {
	o      *fwd.Out
	id     string
	stream string
	kind   webrtc.RTPCodecType
	mime   string
}

func (t *trackOut) Bind(ctx webrtc.TrackLocalContext) (webrtc.RTPCodecParameters, error) {
	for _, c := range ctx.CodecParameters() {
		if strings.EqualFold(c.MimeType, t.mime) {
			if t.kind == webrtc.RTPCodecTypeVideo && playoutMax > 0 {
				for _, h := range ctx.HeaderExtensions() {
					if h.URI == playoutDelayURI {
						t.o.SetPlayoutDelay(uint8(h.ID), playoutMin, playoutMax)
					}
				}
			}
			t.o.Bind(ctx.WriteStream(), uint32(ctx.SSRC()), uint8(c.PayloadType))
			return c, nil
		}
	}
	return webrtc.RTPCodecParameters{}, webrtc.ErrUnsupportedCodec
}

func (t *trackOut) Unbind(webrtc.TrackLocalContext) error { t.o.Unbind(); return nil }
func (t *trackOut) ID() string                            { return t.id }
func (t *trackOut) RID() string                           { return "" }
func (t *trackOut) StreamID() string                      { return t.stream }
func (t *trackOut) Kind() webrtc.RTPCodecType             { return t.kind }

// sub ist ein Abo eines Beins: von wem, welche Art, welcher Vorrang.
type sub struct {
	Owner string   `json:"owner"`
	Kind  string   `json:"kind"`  // audio | video
	Class string   `json:"class"` // audio | speaker | tile | screen
	Prefs []string `json:"prefs"`
}

type legReq struct {
	SID   string `json:"sid"`
	Path  string `json:"path"` // udp | tcp | turn
	Role  string `json:"role"` // audio | video | both
	Audio int    `json:"audio"`
	Video int    `json:"video"`
	Offer string `json:"offer"`
	Subs  []sub  `json:"subs,omitempty"` // nur bench
}

// leg ist ein Bein (PeerConnection) am Knoten.
type leg struct {
	id, sid, path, role string
	ufrag               string
	ipKey               string // nur im Speicher fuer die Grenze je Adresse
	pc                  *webrtc.PeerConnection
	fl                  *fwd.Leg
	sl                  *sfu.Leg // nur Betriebsart node
	screenMid           string   // P5: mid der Bildschirm-Spur (sfuLegReq.ScreenMid)
	outs                []*fwd.Out
	created             time.Time

	mu        sync.Mutex
	connected time.Time
	iceAt     time.Time
	srcs      []*fwd.Source
	srcEnd    [3]uint64 // Zaehler des Abgleichs der beendeten Spuren: Doppel, zu alt, spaet (endSrc)
	closed    bool
	rttNs     atomic.Int64 // Umlaufzeit aus den Empfaengerberichten (Probing: Laenge des Fensters)
	bwe       *bweWrap     // nur HORN_BWE=mit (UDP-Beine): Mitleser mit eigenem Stau-Detektor, sonst nil
}

func (l *leg) bweWrap() *bweWrap {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.bwe
}

func classOf(s string) fwd.Class {
	switch s {
	case "audio":
		return fwd.ClassAudio
	case "screen":
		return fwd.ClassScreen
	case "speaker":
		return fwd.ClassSpeaker
	}
	return fwd.ClassTile
}

// mirrorSubs: Spiegel-Profil. Das Geraet bekommt seine eigenen synthetischen
// Quellen so oft zurueck, wie es sie in einem N-Personen-Anruf von anderen
// bekaeme: Ton x audio, Sprecher (hohe Schicht) + Kacheln (niedrige Schicht).
func mirrorSubs(sid string, audio, video int) []sub {
	var out []sub
	for i := 0; i < audio; i++ {
		out = append(out, sub{Owner: sid, Kind: "audio", Class: "audio", Prefs: []string{"a"}})
	}
	for i := 0; i < video; i++ {
		if i == 0 {
			out = append(out, sub{Owner: sid, Kind: "video", Class: "speaker", Prefs: []string{"h", "m", "l"}})
		} else {
			out = append(out, sub{Owner: sid, Kind: "video", Class: "tile", Prefs: []string{"l", "m", "h"}})
		}
	}
	return out
}

func layerOf(tr *webrtc.TrackRemote) string {
	if tr.Kind() == webrtc.RTPCodecTypeAudio {
		return "a"
	}
	if r := tr.RID(); r != "" {
		return r
	}
	switch id := tr.ID(); id {
	case "l", "m", "h":
		return id
	}
	return "h"
}

var errLimit = errors.New("zu viele Verbindungen")

func (s *server) newLeg(req legReq, ipKey string) (*leg, string, error) {
	var api *webrtc.API
	switch req.Path {
	case "udp":
		api = s.apiUDP
	case "tcp":
		api = s.apiTCP
	case "turn":
		api = s.apiTURN
	}
	if api == nil {
		return nil, "", fmt.Errorf("weg %q nicht verfuegbar", req.Path)
	}
	if req.Audio < 0 || req.Audio > 8 || req.Video < 0 || req.Video > 30 || len(req.SID) < 8 || len(req.SID) > 64 || len(req.Subs) > 40 {
		return nil, "", errors.New("ungueltige anfrage")
	}
	subs := req.Subs
	if len(subs) == 0 {
		subs = mirrorSubs(req.SID, req.Audio, req.Video)
	} else if !s.cfg.Bench {
		return nil, "", errors.New("abos nur im bench-modus")
	}
	s.mu.Lock()
	if len(s.legs) >= s.cfg.MaxLegs || (!s.cfg.Bench && ipKey != "" && s.perIP[ipKey] >= s.cfg.maxPerIP()) {
		s.mu.Unlock()
		return nil, "", errLimit
	}
	l := &leg{id: randHex(8), sid: req.SID, path: req.Path, role: req.Role, ipKey: ipKey, created: time.Now(), fl: fwd.NewLeg()}
	s.legs[l.id] = l
	s.perIP[ipKey]++
	s.mu.Unlock()

	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		s.closeLeg(l, "aufbau fehlgeschlagen")
		return nil, "", err
	}
	l.setPC(pc)
	pc.OnICEConnectionStateChange(func(st webrtc.ICEConnectionState) {
		if st == webrtc.ICEConnectionStateConnected {
			l.mu.Lock()
			if l.iceAt.IsZero() {
				l.iceAt = time.Now()
			}
			l.mu.Unlock()
		}
	})
	pc.OnConnectionStateChange(func(st webrtc.PeerConnectionState) {
		if st == webrtc.PeerConnectionStateConnected {
			l.mu.Lock()
			if l.connected.IsZero() {
				l.connected = time.Now()
			}
			l.mu.Unlock()
		}
	})
	pc.OnTrack(func(tr *webrtc.TrackRemote, rc *webrtc.RTPReceiver) {
		kind := fwd.Audio
		if tr.Kind() == webrtc.RTPCodecTypeVideo {
			kind = fwd.Video
		}
		src := &fwd.Source{Owner: req.SID, Layer: layerOf(tr), Kind: kind, SSRC: uint32(tr.SSRC())}
		if debug {
			log.Printf("debug %s %s: spur %s/%s ssrc %d", l.id, req.Path, tr.Kind(), src.Layer, src.SSRC)
		}
		src.PLI = func(ssrc uint32) {
			_ = pc.WriteRTCP([]rtcp.Packet{&rtcp.PictureLossIndication{MediaSSRC: ssrc}})
		}
		l.mu.Lock()
		l.srcs = append(l.srcs, src)
		l.mu.Unlock()
		s.room.AddSource(src)
		go func() {
			buf := make([]byte, 1500)
			rid := tr.RID()
			for {
				var err error
				if rid != "" {
					_, _, err = rc.ReadSimulcast(buf, rid)
				} else {
					_, _, err = rc.Read(buf)
				}
				if err != nil {
					return
				}
			}
		}()
		src.ReadLoop(func(b []byte) (int, error) {
			n, _, err := tr.Read(b)
			return n, err
		})
		l.endSrc(src)
		s.room.RemoveSource(src)
	})
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: req.Offer}); err != nil {
		s.closeLeg(l, "aufbau fehlgeschlagen")
		return nil, "", fmt.Errorf("angebot: %w", err)
	}
	for i, sb := range subs {
		kind := fwd.Audio
		rk, mime := webrtc.RTPCodecTypeAudio, mimeOpus
		if sb.Kind == "video" {
			kind, rk, mime = fwd.Video, webrtc.RTPCodecTypeVideo, mimeVP8
		}
		id := fmt.Sprintf("%s%d", sb.Kind[:1], i)
		o := l.fl.NewOut(id, sb.Owner, kind, classOf(sb.Class), sb.Prefs...)
		t := &trackOut{o: o, id: id, stream: "s-" + l.id + "-" + id, kind: rk, mime: mime}
		sender, err := pc.AddTrack(t)
		if err != nil {
			s.closeLeg(l, "aufbau fehlgeschlagen")
			return nil, "", fmt.Errorf("spur: %w", err)
		}
		l.outs = append(l.outs, o)
		s.room.AddOut(o)
		go readSenderRTCP(sender, o, nil)
	}
	ans, err := pc.CreateAnswer(nil)
	if err != nil {
		s.closeLeg(l, "aufbau fehlgeschlagen")
		return nil, "", fmt.Errorf("antwort: %w", err)
	}
	gather := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(ans); err != nil {
		s.closeLeg(l, "aufbau fehlgeschlagen")
		return nil, "", err
	}
	select {
	case <-gather:
	case <-time.After(3 * time.Second):
	}
	sdp := pc.LocalDescription().SDP
	if req.Path != "udp" {
		sdp = addPtime(sdp, 40) // H5: Opus mit 40 ms auf TCP-Beinen
	}
	for _, ln := range strings.Split(sdp, "\n") {
		if u, ok := strings.CutPrefix(strings.TrimSpace(ln), "a=ice-ufrag:"); ok {
			s.mu.Lock()
			l.ufrag = u
			s.ufrags[u] = l
			s.mu.Unlock()
			break
		}
	}
	if debug {
		for _, ln := range strings.Split(sdp, "\n") {
			if strings.HasPrefix(ln, "a=candidate") {
				log.Printf("debug %s %s: %s", l.id, req.Path, strings.TrimSpace(ln))
			}
		}
	}
	logLegOpen(l)
	return l, sdp, nil
}

var debug = os.Getenv("HORN_DEBUG") == "1"

// readSenderRTCP liest Rueckmeldungen des Empfaengers; PLI/FIR gehen an die Quelle.
func readSenderRTCP(sender *webrtc.RTPSender, o *fwd.Out, rtt *atomic.Int64) {
	buf := make([]byte, 1500)
	for {
		n, _, err := sender.Read(buf)
		if err != nil {
			return
		}
		if rtt != nil {
			if d, ok := rrRTT(buf[:n], time.Now()); ok {
				rtt.Store(int64(d))
			}
		}
		if !hasKeyRequest(buf[:n]) {
			continue
		}
		o.KeyRequest()
	}
}

// rrRTT liest die Umlaufzeit aus den Empfangsberichten eines RTCP-Verbunds (SR/RR: jetzt - LSR - DLSR, RFC 3550
// 6.4.1), ohne zu allokieren. Unsere Senderberichte tragen die Systemzeit als NTP-Zeit.
func rrRTT(b []byte, now time.Time) (time.Duration, bool) {
	for len(b) >= 4 {
		rc, pt := int(b[0]&0x1f), b[1]
		ln := (int(b[2])<<8 | int(b[3]) + 1) * 4
		if ln > len(b) {
			return 0, false
		}
		off := 0
		switch pt {
		case 200: // Senderbericht: 28 Byte Kopf
			off = 28
		case 201: // Empfaengerbericht
			off = 8
		}
		for i := 0; off != 0 && i < rc && off+24 <= ln; i, off = i+1, off+24 {
			lsr := binary.BigEndian.Uint32(b[off+16:])
			dlsr := binary.BigEndian.Uint32(b[off+20:])
			if lsr == 0 {
				continue
			}
			sec := uint64(now.Unix()) + 2208988800 // NTP zaehlt ab 1900
			frac := uint64(now.Nanosecond()) << 32 / 1e9
			mid := uint32((sec<<32 | frac) >> 16)
			d := mid - lsr - dlsr // in 1/65536 s
			if d == 0 || d > 5<<16 {
				continue
			}
			return time.Duration(uint64(d) * 1e9 >> 16), true
		}
		b = b[ln:]
	}
	return 0, false
}

// hasKeyRequest sucht PLI (PT 206, FMT 1) oder FIR (PT 206, FMT 4) im
// RTCP-Verbund, ohne zu allokieren.
func hasKeyRequest(b []byte) bool {
	for len(b) >= 4 {
		fmtv := b[0] & 0x1f
		pt := b[1]
		ln := (int(b[2])<<8 | int(b[3]) + 1) * 4
		if pt == 206 && (fmtv == 1 || fmtv == 4) {
			return true
		}
		if ln <= 0 || ln > len(b) {
			return false
		}
		b = b[ln:]
	}
	return false
}

// shortID kuerzt eine Bein-ID fuers Protokoll.
func shortID(id string) string {
	if len(id) > 6 {
		return id[:6]
	}
	return id
}

// legMode: Betriebsart eines Beins fuers Protokoll (node = Knoten-Bein,
// sonst Spiegel/Bench). Ruft l.mu.
func (l *leg) legMode() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.sl != nil {
		return "node"
	}
	return "spiegel"
}

// logLegOpen: Protokollzeile "bein auf" (ohne Adressen/ufrag).
func logLegOpen(l *leg) {
	log.Printf("bein auf %s modus=%s weg=%s", shortID(l.id), l.legMode(), l.path)
}

// closeLeg schliesst ein Bein und protokolliert den Grund (ohne Adressen/ufrag).
func (s *server) closeLeg(l *leg, reason string) {
	mode := l.legMode()
	l.mu.Lock()
	if l.closed {
		l.mu.Unlock()
		return
	}
	l.closed = true
	l.mu.Unlock()
	// Abgleich des Weiterleiters fuer die Quellen dieses Beins: verworfene Doppel, Pakete aelter als das Fenster,
	// spaete erste Exemplare - und was dieses Bein als Empfaenger nicht mehr einordnen konnte
	st := l.stats()
	dups, old, late := st.ended[0], st.ended[1], st.ended[2] // beendete Spuren
	for _, x := range st.Sources {
		dups, old, late = dups+x.Dups, old+x.TooOld, late+x.Late
	}
	log.Printf("bein zu %s modus=%s weg=%s dauer=%s doppel=%d alt=%d spaet=%d spaet_verworfen=%d grund=%s", shortID(l.id), mode, l.path,
		time.Since(l.created).Round(time.Second), dups, old, late, st.LateDrop, reason)
	s.mu.Lock()
	delete(s.legs, l.id)
	if l.ufrag != "" && s.ufrags[l.ufrag] == l {
		delete(s.ufrags, l.ufrag)
	}
	if s.perIP[l.ipKey]--; s.perIP[l.ipKey] <= 0 {
		delete(s.perIP, l.ipKey)
	}
	l.mu.Lock()
	if l.sl != nil {
		delete(s.bySFU, l.sl)
	}
	l.mu.Unlock()
	s.mu.Unlock()
	for _, o := range l.outs {
		s.room.RemoveOut(o)
	}
	l.mu.Lock()
	pc, fl, sl := l.pc, l.fl, l.sl
	l.mu.Unlock()
	if sl != nil {
		sl.Close()
	}
	if pc != nil {
		_ = pc.Close()
	}
	if fl != nil {
		fl.Close()
	}
}

// legStats ist die Knotensicht eines Beins (fuer die Messseite).
type legStats struct {
	ID        string     `json:"id"`
	Path      string     `json:"path"`
	Role      string     `json:"role"`
	State     string     `json:"state"`
	ConnectMs int64      `json:"connect_ms"`
	PktsOut   uint64     `json:"pkts_out"`
	BytesOut  uint64     `json:"bytes_out"`
	PadBytes  uint64     `json:"pad_bytes,omitempty"` // Fuelldaten der Proben (in bytes_out enthalten)
	Dropped   uint64     `json:"frames_dropped"`
	Stalls    uint64     `json:"stalls"`
	MaxQMs    float64    `json:"max_queue_ms"`
	FwdP95us  float64    `json:"fwd_p95_us"`
	Overflow  uint64     `json:"overflow"`
	TURN      *TURNSetup `json:"turn_setup,omitempty"`
	WriteErrs uint64     `json:"write_errs"`
	Secs      float64    `json:"secs"`
	LateDrop  uint64     `json:"late_dropped,omitempty"` // verspaetete Pakete, die dieses Bein nicht mehr einordnen konnte
	Sources   []srcStats `json:"sources,omitempty"`      // laufende eingehende Spuren dieses Beins
	ended     [3]uint64  // Summen der beendeten Spuren (Doppel, zu alt, spaet) - fuer die Zeile "bein zu"
}

// srcStats: Zaehler einer eingehenden Spur (Abgleich auf Doppel im Weiterleiter, fwd.Source).
type srcStats struct {
	Kind   string `json:"kind"`              // audio | video
	Layer  string `json:"layer"`             // a | l | m | h
	Pkts   uint64 `json:"pkts"`              // angenommene Pakete (ohne Doppel und zu alte)
	Dups   uint64 `json:"dups,omitempty"`    // verworfen: Folgenummer schon gesehen
	TooOld uint64 `json:"too_old,omitempty"` // verworfen: aelter als das Fenster
	Late   uint64 `json:"late,omitempty"`    // spaete erste Exemplare (weitergegeben)
}

func (l *leg) setPC(pc *webrtc.PeerConnection) {
	l.mu.Lock()
	l.pc = pc
	l.mu.Unlock()
}

// state liefert den Verbindungszustand (new, solange das Bein entsteht).
func (l *leg) state() webrtc.PeerConnectionState {
	l.mu.Lock()
	pc := l.pc
	l.mu.Unlock()
	if pc == nil {
		return webrtc.PeerConnectionStateNew
	}
	return pc.ConnectionState()
}

// endSrc: Die Spur ist zu Ende. Ihre Zaehler des Abgleichs gehen in die Summe des Beins ein (Zeile "bein zu"), die
// Quelle selbst steht nicht mehr in sources[] - sonst wuechse die Liste eines langen Beins mit jeder neuen Spur.
func (l *leg) endSrc(src *fwd.Source) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for i, x := range l.srcs {
		if x == src {
			l.srcs = append(l.srcs[:i], l.srcs[i+1:]...)
			l.srcEnd[0] += src.Dups.Load()
			l.srcEnd[1] += src.TooOld.Load()
			l.srcEnd[2] += src.Late.Load()
			return
		}
	}
}

func (l *leg) stats() legStats {
	l.mu.Lock()
	con := l.connected
	fl := l.fl
	srcs := append([]*fwd.Source(nil), l.srcs...)
	ended := l.srcEnd
	l.mu.Unlock()
	if fl == nil {
		return legStats{ID: l.id, Path: l.path, Role: l.role, State: "new"}
	}
	st := legStats{ID: l.id, Path: l.path, Role: l.role, State: l.state().String(),
		PktsOut: fl.Stats.PktsOut.Load(), BytesOut: fl.Stats.BytesOut.Load(), PadBytes: fl.Stats.PadBytes.Load(),
		LateDrop: fl.Stats.LateDropped.Load(), Dropped: fl.Stats.FramesDropped.Load(), Stalls: fl.Stats.Stalls.Load(),
		MaxQMs: float64(fl.Stats.MaxAgeNs.Load()) / 1e6, FwdP95us: fl.Stats.P95us(),
		Overflow: fl.Stats.Overflow.Load(), WriteErrs: fl.Stats.WriteErrs.Load(),
		Secs: time.Since(l.created).Seconds(), ended: ended}
	for _, x := range srcs {
		k := "audio"
		if x.Kind == fwd.Video {
			k = "video"
		}
		st.Sources = append(st.Sources, srcStats{Kind: k, Layer: x.Layer, Pkts: x.Pkts.Load(),
			Dups: x.Dups.Load(), TooOld: x.TooOld.Load(), Late: x.Late.Load()})
	}
	if !con.IsZero() {
		st.ConnectMs = con.Sub(l.created).Milliseconds()
	}
	return st
}

// addPtime setzt a=ptime in jedem Audio-Abschnitt der Antwort. Browser lesen
// die Rahmendauer aus a=ptime, nicht aus dem fmtp-Parameter.
func addPtime(sdp string, ms int) string {
	lines := strings.Split(sdp, "\r\n")
	out := make([]string, 0, len(lines)+4)
	audio := false
	for _, ln := range lines {
		if strings.HasPrefix(ln, "m=") {
			audio = strings.HasPrefix(ln, "m=audio")
		}
		if audio && strings.HasPrefix(ln, "a=ptime:") {
			continue
		}
		out = append(out, ln)
		if audio && strings.HasPrefix(ln, "m=audio") {
			out = append(out, fmt.Sprintf("a=ptime:%d", ms))
		}
	}
	return strings.Join(out, "\r\n")
}
