package main

import (
	"errors"
	"fmt"
	"log"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

const (
	mimeOpus = webrtc.MimeTypeOpus
	mimeVP8  = webrtc.MimeTypeVP8
)

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
		if err := webrtc.ConfigureNack(m, ir); err != nil {
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
	outs                []*fwd.Out
	created             time.Time

	mu        sync.Mutex
	connected time.Time
	srcs      []*fwd.Source
	closed    bool
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
	if len(s.legs) >= s.cfg.MaxLegs || (!s.cfg.Bench && ipKey != "" && s.perIP[ipKey] >= 12) {
		s.mu.Unlock()
		return nil, "", errLimit
	}
	l := &leg{id: randHex(8), sid: req.SID, path: req.Path, role: req.Role, ipKey: ipKey, created: time.Now(), fl: fwd.NewLeg()}
	s.legs[l.id] = l
	s.perIP[ipKey]++
	s.mu.Unlock()

	pc, err := api.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		s.closeLeg(l)
		return nil, "", err
	}
	l.pc = pc
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
		s.room.RemoveSource(src)
	})
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: req.Offer}); err != nil {
		s.closeLeg(l)
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
			s.closeLeg(l)
			return nil, "", fmt.Errorf("spur: %w", err)
		}
		l.outs = append(l.outs, o)
		s.room.AddOut(o)
		go readSenderRTCP(sender, o)
	}
	ans, err := pc.CreateAnswer(nil)
	if err != nil {
		s.closeLeg(l)
		return nil, "", fmt.Errorf("antwort: %w", err)
	}
	gather := webrtc.GatheringCompletePromise(pc)
	if err := pc.SetLocalDescription(ans); err != nil {
		s.closeLeg(l)
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
			l.ufrag = u
			s.mu.Lock()
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
	return l, sdp, nil
}

var debug = os.Getenv("HORN_DEBUG") == "1"

// readSenderRTCP liest Rueckmeldungen des Empfaengers; PLI/FIR gehen an die Quelle.
func readSenderRTCP(sender *webrtc.RTPSender, o *fwd.Out) {
	buf := make([]byte, 1500)
	for {
		n, _, err := sender.Read(buf)
		if err != nil {
			return
		}
		if !hasKeyRequest(buf[:n]) {
			continue
		}
		o.KeyRequest()
	}
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

func (s *server) closeLeg(l *leg) {
	l.mu.Lock()
	if l.closed {
		l.mu.Unlock()
		return
	}
	l.closed = true
	l.mu.Unlock()
	s.mu.Lock()
	delete(s.legs, l.id)
	if l.ufrag != "" && s.ufrags[l.ufrag] == l {
		delete(s.ufrags, l.ufrag)
	}
	if s.perIP[l.ipKey]--; s.perIP[l.ipKey] <= 0 {
		delete(s.perIP, l.ipKey)
	}
	s.mu.Unlock()
	for _, o := range l.outs {
		s.room.RemoveOut(o)
	}
	if l.pc != nil {
		_ = l.pc.Close()
	}
	l.fl.Close()
}

// legStats ist die Knotensicht eines Beins (fuer die Messseite).
type legStats struct {
	ID        string  `json:"id"`
	Path      string  `json:"path"`
	Role      string  `json:"role"`
	State     string  `json:"state"`
	ConnectMs int64   `json:"connect_ms"`
	PktsOut   uint64  `json:"pkts_out"`
	BytesOut  uint64  `json:"bytes_out"`
	Dropped   uint64  `json:"frames_dropped"`
	Stalls    uint64  `json:"stalls"`
	MaxQMs    float64 `json:"max_queue_ms"`
	FwdP95us  float64 `json:"fwd_p95_us"`
	Overflow  uint64  `json:"overflow"`
	WriteErrs uint64  `json:"write_errs"`
	Secs      float64 `json:"secs"`
}

func (l *leg) stats() legStats {
	l.mu.Lock()
	con := l.connected
	l.mu.Unlock()
	st := legStats{ID: l.id, Path: l.path, Role: l.role, State: l.pc.ConnectionState().String(),
		PktsOut: l.fl.Stats.PktsOut.Load(), BytesOut: l.fl.Stats.BytesOut.Load(),
		Dropped: l.fl.Stats.FramesDropped.Load(), Stalls: l.fl.Stats.Stalls.Load(),
		MaxQMs: float64(l.fl.Stats.MaxAgeNs.Load()) / 1e6, FwdP95us: l.fl.Stats.P95us(),
		Overflow: l.fl.Stats.Overflow.Load(), WriteErrs: l.fl.Stats.WriteErrs.Load(),
		Secs: time.Since(l.created).Seconds()}
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
