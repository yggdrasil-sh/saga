package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/pion/interceptor"
	"github.com/pion/interceptor/pkg/cc"
	"github.com/pion/interceptor/pkg/gcc"
	"github.com/pion/rtcp"
	"github.com/pion/sdp/v3"
	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Betriebsart "node": echter Weiterleiter (internal/sfu) statt Spiegel.
// Beine kommen mit einem Medien-Token (Pruefung ueber sfu.Verifier). Bis zur
// Saga-Steuerung (P3) gibt es nur den Entwicklungs-Pruefer (HORN_DEV=1) mit
// eigenen Endpunkten unter /api/dev/ fuer Tests, bench und die Testseite.

// gccHook verbindet den GCC-Schaetzer einer neuen PeerConnection mit ihrem
// Bein (der Rueckruf kommt synchron in NewPeerConnection).
type gccHook struct {
	mu   sync.Mutex
	next func(cc.BandwidthEstimator)
}

// newSFUAPI baut die pion-API fuer SFU-Beine. UDP: NACK/RTX, TWCC-Kopf auf
// abgehenden Paketen und GCC (H7). TCP-Wege: ohne NACK/RTX (H5), Opus mit
// ptime 40. Beide: audio-level fuer Top-3, TWCC-Rueckmeldung fuer den
// Uplink der Geraete.
func newSFUAPI(se webrtc.SettingEngine, udp bool, hook *gccHook) (*webrtc.API, error) {
	m := &webrtc.MediaEngine{}
	opusFmtp := "minptime=10;useinbandfec=1;usedtx=1"
	if !udp {
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
	if udp {
		fb = append(fb, webrtc.RTCPFeedback{Type: "nack"})
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{
		RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000, RTCPFeedback: fb},
		PayloadType:        96,
	}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	if udp {
		if err := m.RegisterCodec(webrtc.RTPCodecParameters{
			RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000, SDPFmtpLine: "apt=96"},
			PayloadType:        97,
		}, webrtc.RTPCodecTypeVideo); err != nil {
			return nil, err
		}
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.AudioLevelURI}, webrtc.RTPCodecTypeAudio); err != nil {
		return nil, err
	}
	if err := webrtc.ConfigureSimulcastExtensionHeaders(m); err != nil {
		return nil, err
	}
	if !udp && playoutMax > 0 { // P9: playout-delay fuer Bild auf TCP-/TLS-Wegen (Apps koennen jitterBufferTarget nicht setzen)
		if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: playoutDelayURI}, webrtc.RTPCodecTypeVideo); err != nil {
			return nil, err
		}
	}
	ir := &interceptor.Registry{}
	if udp {
		if err := webrtc.ConfigureNack(m, ir); err != nil {
			return nil, err
		}
	}
	if err := webrtc.ConfigureRTCPReports(ir); err != nil {
		return nil, err
	}
	if twccTap != nil { // nur Tests: abgehende transport-cc-Rueckmeldung mitlesen
		ir.Add(twccTapFactory{})
	}
	if err := webrtc.ConfigureTWCCSender(m, ir); err != nil {
		return nil, err
	}
	if udp && hook != nil {
		ccf, err := cc.NewInterceptor(func() (cc.BandwidthEstimator, error) {
			return gcc.NewSendSideBWE(gcc.SendSideBWEInitialBitrate(1_200_000), gcc.SendSideBWEMinBitrate(100_000),
				gcc.SendSideBWEMaxBitrate(25_000_000), gcc.SendSideBWEPacer(gcc.NewNoOpPacer()))
		})
		if err != nil {
			return nil, err
		}
		ccf.OnNewPeerConnection(func(_ string, est cc.BandwidthEstimator) {
			if hook.next != nil {
				hook.next(est)
			}
		})
		ir.Add(ccf)
		// nach cc eintragen: der Kopf muss vor der Schaetzung gesetzt sein
		if err := webrtc.ConfigureTWCCHeaderExtensionSender(m, ir); err != nil {
			return nil, err
		}
	}
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), nil
}

// sfuLegReq ist die Anfrage fuer ein SFU-Bein.
type sfuLegReq struct {
	Token   string `json:"token"`
	Path    string `json:"path"` // udp | tcp | turn
	Offer   string `json:"offer"`
	Audio   int    `json:"audio"`   // Ton-Plaetze (hoechstens 3)
	Speaker bool   `json:"speaker"` // Sprecherplatz
	Screen  bool   `json:"screen"`  // Bildschirmplatz
	Tiles   int    `json:"tiles"`   // Kachelplaetze (Last-N kappt)
	Device  string `json:"device"`  // Hinweis "mobile" (kann nur senken)
	// ScreenMid: mid des Senders fuer den Bildschirm (P5). Browser koennen die Stream-ID nicht waehlen; der Knoten
	// nimmt eine Spur als Bildschirm, wenn ihre Stream-ID mit "screen" beginnt ODER ihre mid ScreenMid ist.
	ScreenMid string `json:"screen_mid"`
}

func (s *server) sfuAPI(p sfu.Path) *webrtc.API {
	switch p {
	case sfu.PathUDP:
		return s.sfuUDP
	case sfu.PathTCP:
		return s.sfuTCP
	}
	return s.sfuTURN
}

// errAuth: Token ungueltig.
var errAuth = errors.New("token ungueltig")

// errDraining: Knoten wird geleert (P5), keine neuen Beine - das Geraet nimmt den naechsten Kandidaten.
var errDraining = errors.New("knoten wird geleert")

func (s *server) newSFULeg(req sfuLegReq, ipKey string) (*leg, string, error) {
	path, err := sfu.ParsePath(req.Path)
	if err != nil {
		return nil, "", err
	}
	api := s.sfuAPI(path)
	if api == nil {
		return nil, "", fmt.Errorf("weg %q nicht verfuegbar", req.Path)
	}
	if req.Audio < 0 || req.Audio > 8 || req.Tiles < 0 || req.Tiles > 30 || len(req.Offer) > 200<<10 {
		return nil, "", errors.New("ungueltige anfrage")
	}
	if s.draining.Load() {
		return nil, "", errDraining
	}
	g, room, err := s.admit(req.Token, path)
	if err != nil {
		if errors.Is(err, sfu.ErrNoRoom) || errors.Is(err, sfu.ErrNoUDP) || errors.Is(err, sfu.ErrNoVerifier) {
			return nil, "", err
		}
		return nil, "", fmt.Errorf("%w: %v", errAuth, err)
	}
	if !s.used.use(g.ID, "leg", g.Exp) {
		return nil, "", fmt.Errorf("%w: schon benutzt", errAuth)
	}
	if sfu.ParseDevice(req.Device) == sfu.Mobile {
		g.Device = sfu.Mobile
	}
	s.mu.Lock()
	if len(s.legs) >= s.cfg.MaxLegs || (!s.cfg.Bench && !s.cfg.Dev && ipKey != "" && s.perIP[ipKey] >= s.cfg.maxPerIP()) {
		s.mu.Unlock()
		return nil, "", errLimit
	}
	l := &leg{id: randHex(8), sid: g.Peer, path: req.Path, role: g.Role.String(), ipKey: ipKey, created: time.Now()}
	if len(req.ScreenMid) <= 16 {
		l.screenMid = req.ScreenMid
	}
	s.legs[l.id] = l
	s.perIP[ipKey]++
	s.mu.Unlock()

	sl, err := room.AddLeg(g, path, sfu.Want{Audio: req.Audio, Speaker: req.Speaker, Screen: req.Screen, Tiles: req.Tiles})
	if err != nil {
		s.closeLeg(l)
		return nil, "", err
	}
	s.mu.Lock()
	l.mu.Lock()
	l.sl = sl
	l.fl = sl.FwdLeg()
	closed := l.closed
	l.mu.Unlock()
	if !closed {
		s.bySFU[sl] = l
	}
	s.mu.Unlock()
	if closed {
		sl.Close()
		return nil, "", errors.New("bein geschlossen")
	}

	var pc *webrtc.PeerConnection
	if path == sfu.PathUDP {
		s.gcc.mu.Lock()
		var est cc.BandwidthEstimator
		s.gcc.next = func(e cc.BandwidthEstimator) { est = e }
		pc, err = api.NewPeerConnection(webrtc.Configuration{})
		s.gcc.next = nil
		s.gcc.mu.Unlock()
		if est != nil {
			sl.Ext = est.GetTargetBitrate
		}
	} else {
		pc, err = api.NewPeerConnection(webrtc.Configuration{})
	}
	if err != nil {
		s.closeLeg(l)
		return nil, "", err
	}
	l.setPC(pc)
	pc.OnConnectionStateChange(func(st webrtc.PeerConnectionState) {
		switch st {
		case webrtc.PeerConnectionStateConnected:
			l.mu.Lock()
			if l.connected.IsZero() {
				l.connected = time.Now()
			}
			l.mu.Unlock()
		case webrtc.PeerConnectionStateFailed, webrtc.PeerConnectionStateClosed:
			go s.closeLeg(l)
		}
	})
	pc.OnTrack(func(tr *webrtc.TrackRemote, rc *webrtc.RTPReceiver) { s.sfuTrack(l, sl, pc, tr, rc) })
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: req.Offer}); err != nil {
		s.closeLeg(l)
		return nil, "", fmt.Errorf("angebot: %w", err)
	}
	for _, o := range sl.Outs() {
		rk, mime := webrtc.RTPCodecTypeAudio, mimeOpus
		if o.Kind == fwd.Video {
			rk, mime = webrtc.RTPCodecTypeVideo, mimeVP8
		}
		t := &trackOut{o: o, id: o.ID, stream: "horn-" + o.ID, kind: rk, mime: mime}
		sender, err := pc.AddTrack(t)
		if err != nil {
			s.closeLeg(l)
			return nil, "", fmt.Errorf("spur: %w", err)
		}
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
	sdpText := pc.LocalDescription().SDP
	if path != sfu.PathUDP {
		sdpText = addPtime(sdpText, 40)
	}
	s.registerUfrag(l, sdpText)
	return l, sdpText, nil
}

func (s *server) registerUfrag(l *leg, sdpText string) {
	for _, ln := range strings.Split(sdpText, "\n") {
		if u, ok := strings.CutPrefix(strings.TrimSpace(ln), "a=ice-ufrag:"); ok {
			s.mu.Lock()
			l.ufrag = u
			s.ufrags[u] = l
			s.mu.Unlock()
			return
		}
	}
}

// sfuTrack meldet eine eingehende Spur als Quelle an. Bildschirm: Stream-ID
// beginnt mit "screen" (Absprache mit den Clients, P5).
func (s *server) sfuTrack(l *leg, sl *sfu.Leg, pc *webrtc.PeerConnection, tr *webrtc.TrackRemote, rc *webrtc.RTPReceiver) {
	kind := fwd.Audio
	if tr.Kind() == webrtc.RTPCodecTypeVideo {
		kind = fwd.Video
	}
	var ext uint8
	if kind == fwd.Audio {
		for _, h := range rc.GetParameters().HeaderExtensions {
			if h.URI == sdp.AudioLevelURI {
				ext = uint8(h.ID)
			}
		}
	}
	screen := strings.HasPrefix(tr.StreamID(), "screen")
	if !screen && l.screenMid != "" && kind == fwd.Video {
		for _, t := range pc.GetTransceivers() {
			if t.Receiver() == rc {
				screen = t.Mid() == l.screenMid
				break
			}
		}
	}
	read := func(b []byte) (int, error) {
		// Einzelne kaputte Pakete (z. B. nicht ausgehandelter Payload-Typ) liefern einen Fehler, ohne dass die Spur zu
		// ist: weiterlesen. Sonst bliebe die SSRC ab da ungelesen - die Quelle waere weg und transport-cc meldete jedes
		// weitere Paket nicht mehr als empfangen (GCC beim Sender: Verlust, Schaetzung faellt; BH3).
		for bad := 0; ; bad++ {
			n, _, err := tr.Read(b)
			if err == nil || errors.Is(err, io.EOF) || bad >= 50 {
				return n, err
			}
		}
	}
	if s.capture != nil { // nur Tests (HORN_CAPTURE): eingehende Rahmen mitschneiden
		read = s.capture.tap(kind == fwd.Video, uint32(tr.SSRC()), read)
	}
	src, err := sl.AddSource(kind, layerOf(tr), screen, uint32(tr.SSRC()), ext, func(ssrc uint32) {
		_ = pc.WriteRTCP([]rtcp.Packet{&rtcp.PictureLossIndication{MediaSSRC: ssrc}})
	})
	// RTCP des Empfaengers leeren (Interceptoren)
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
	if err != nil { // nicht erlaubt: lesen und verwerfen
		if debug {
			log.Printf("debug %s: spur abgelehnt: %v", l.id, err)
		}
		buf := make([]byte, 1500)
		for {
			if _, _, err := tr.Read(buf); err != nil {
				return
			}
		}
	}
	if debug {
		log.Printf("debug %s %s: quelle %s/%s ssrc %d ext %d", l.id, l.path, tr.Kind(), src.Layer, src.SSRC, ext)
	}
	src.ReadLoop(read)
	if r := s.node.Room(sl.Grant.Room); r != nil {
		r.RemoveSource(src)
	}
}

// onSFULegClosed: der Knoten hat ein Bein beendet (ersetzt, entfernt).
func (s *server) onSFULegClosed(sl *sfu.Leg, why string) {
	s.mu.Lock()
	l := s.bySFU[sl]
	s.mu.Unlock()
	s.evLegClosed(sl, why)
	if l != nil {
		if debug {
			log.Printf("debug %s: bein beendet (%s)", l.id, why)
		}
		go s.closeLeg(l)
	}
}

// ---------- Entwicklungs-Pruefer (HORN_DEV=1) ----------

// devToken ist der Inhalt eines Entwicklungs-Tokens: base64(json).base64(hmac).
type devToken struct {
	Room   string   `json:"room"`
	Peer   string   `json:"peer"`
	Leg    string   `json:"leg"`
	Role   string   `json:"role"`
	Pub    []string `json:"pub"`
	Device string   `json:"device"`
	UDP    bool     `json:"udp"`
	Kbit   int      `json:"max_kbit"`
	Exp    int64    `json:"exp"`
}

func (s *server) signDev(t devToken) string {
	b, _ := json.Marshal(t)
	p := base64.RawURLEncoding.EncodeToString(b)
	m := hmac.New(sha256.New, []byte(s.devSecret))
	m.Write([]byte(p))
	return p + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

func (s *server) verifyDev(tok string) (sfu.Grant, error) {
	p, sig, ok := strings.Cut(tok, ".")
	if !ok {
		return sfu.Grant{}, errAuth
	}
	m := hmac.New(sha256.New, []byte(s.devSecret))
	m.Write([]byte(p))
	want := base64.RawURLEncoding.EncodeToString(m.Sum(nil))
	if !hmac.Equal([]byte(sig), []byte(want)) {
		return sfu.Grant{}, errAuth
	}
	b, err := base64.RawURLEncoding.DecodeString(p)
	if err != nil {
		return sfu.Grant{}, errAuth
	}
	var t devToken
	if err := json.Unmarshal(b, &t); err != nil {
		return sfu.Grant{}, errAuth
	}
	role, err := sfu.ParseRole(t.Role)
	if err != nil {
		return sfu.Grant{}, err
	}
	return sfu.Grant{Room: t.Room, Peer: t.Peer, Leg: t.Leg, Role: role, Pub: sfu.ParsePub(t.Pub),
		Device: sfu.ParseDevice(t.Device), UDPOK: t.UDP, MaxKbit: t.Kbit, Exp: time.Unix(t.Exp, 0)}, nil
}

func (s *server) sfuRoutes(m *http.ServeMux) {
	m.HandleFunc("POST /api/sfu/leg", s.apiSFULeg)
	m.HandleFunc("POST /api/sfu/turn", s.apiSFUTurn)
	m.HandleFunc("GET /api/sfu/leg/{id}", s.apiSFULayout)
	m.HandleFunc("GET /api/sfu/leg/{id}/events", s.apiSFUEvents) // P5: Ereigniskanal (events.go)
	m.HandleFunc("DELETE /api/sfu/leg/{id}", s.apiLegDelete)
	m.HandleFunc("GET /api/sfu/bridge", s.apiBridge) // P9: Kaskade (bifrost.go)
	if !s.cfg.Dev {
		return
	}
	m.HandleFunc("POST /api/dev/cascade", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ Bridge, URL, Room, PeerRoom string }
		if err := json.NewDecoder(io.LimitReader(r.Body, 4<<10)).Decode(&req); err != nil {
			writeJSON(w, 400, map[string]string{"error": "json"})
			return
		}
		if err := s.cascade(cascadeCmd{Bridge: req.Bridge, URL: req.URL, Peer: "dev", Token: req.PeerRoom, Room: req.Room}); err != nil {
			writeJSON(w, 400, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true})
	})
	m.HandleFunc("GET /api/dev/bridges", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"bridges": s.bridgeStats()})
	})
	m.HandleFunc("POST /api/dev/room", func(w http.ResponseWriter, r *http.Request) {
		var req struct{ Room string }
		if err := json.NewDecoder(io.LimitReader(r.Body, 4<<10)).Decode(&req); err != nil || req.Room == "" {
			writeJSON(w, 400, map[string]string{"error": "raum"})
			return
		}
		if _, err := s.node.CreateRoom(req.Room, nil); err != nil {
			writeJSON(w, 409, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]string{"room": req.Room})
	})
	m.HandleFunc("DELETE /api/dev/room/{room}", func(w http.ResponseWriter, r *http.Request) {
		if err := s.node.CloseRoom(r.PathValue("room")); err != nil {
			writeJSON(w, 404, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]string{})
	})
	m.HandleFunc("POST /api/dev/token", func(w http.ResponseWriter, r *http.Request) {
		var t devToken
		if err := json.NewDecoder(io.LimitReader(r.Body, 4<<10)).Decode(&t); err != nil || t.Room == "" || t.Peer == "" {
			writeJSON(w, 400, map[string]string{"error": "token"})
			return
		}
		if t.Leg == "" {
			t.Leg = t.Peer + "-" + t.Role
		}
		if len(t.Pub) == 0 {
			t.Pub = []string{"a", "v", "s"}
		}
		t.Exp = time.Now().Add(60 * time.Second).Unix()
		writeJSON(w, 200, map[string]string{"token": s.signDev(t)})
	})
	m.HandleFunc("GET /api/dev/room/{room}", func(w http.ResponseWriter, r *http.Request) {
		rm := s.node.Room(r.PathValue("room"))
		if rm == nil {
			writeJSON(w, 404, map[string]string{"error": "raum"})
			return
		}
		writeJSON(w, 200, map[string]any{"peers": rm.Peers()})
	})
	m.HandleFunc("POST /api/dev/throttle", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Leg  string
			Kbit int64
		}
		if err := json.NewDecoder(io.LimitReader(r.Body, 4<<10)).Decode(&req); err != nil {
			writeJSON(w, 400, map[string]string{"error": "json"})
			return
		}
		l := s.getLeg(req.Leg)
		if l == nil || l.sl == nil {
			writeJSON(w, 404, map[string]string{"error": "unbekannt"})
			return
		}
		l.fl.SetThrottle(req.Kbit * 1000)
		writeJSON(w, 200, map[string]any{"leg": req.Leg, "kbit": req.Kbit})
	})
	m.HandleFunc("GET /api/dev/load", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"load": s.node.Load(), "node": s.nodeStats()})
	})
}

func (s *server) apiSFULeg(w http.ResponseWriter, r *http.Request) {
	var req sfuLegReq
	if err := json.NewDecoder(io.LimitReader(r.Body, 256<<10)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "json"})
		return
	}
	l, answer, err := s.newSFULeg(req, ipKey(r))
	switch {
	case err == nil:
	case errors.Is(err, errLimit), errors.Is(err, sfu.ErrFull), errors.Is(err, errDraining):
		writeJSON(w, 429, map[string]string{"error": err.Error()})
		return
	case errors.Is(err, errAuth), errors.Is(err, sfu.ErrNoVerifier):
		writeJSON(w, 401, map[string]string{"error": err.Error()})
		return
	case errors.Is(err, sfu.ErrNoRoom):
		writeJSON(w, 404, map[string]string{"error": err.Error()})
		return
	default:
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	// P5: peers = alle Teilnehmenden des Raums mit CSRC - das Geraet kennt damit jeden Absender vor dem ersten Rahmen
	peers := map[string]uint32{}
	if rm := s.node.Room(l.sl.Grant.Room); rm != nil {
		peers = rm.Peers()
	}
	writeJSON(w, 200, map[string]any{"id": l.id, "answer": answer, "csrc": l.sl.Peer.CSRC, "slots": l.sl.Layout(), "peers": peers,
		"state": s.nodeState()})
}

// apiSFUTurn gibt TURN-Zugangsdaten nur gegen ein gueltiges Medien-Token
// (§3.4: ohne Token keine Allocation).
func (s *server) apiSFUTurn(w http.ResponseWriter, r *http.Request) {
	var req struct{ Token string }
	if err := json.NewDecoder(io.LimitReader(r.Body, 8<<10)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]string{"error": "json"})
		return
	}
	g, _, err := s.admit(req.Token, sfu.PathTURN)
	if err == nil && !s.used.use(g.ID, "turn", g.Exp) {
		err = errors.New("token schon benutzt")
	}
	if err != nil {
		writeJSON(w, 401, map[string]string{"error": err.Error()})
		return
	}
	user, pass, err := turn.GenerateLongTermCredentials(s.turnSecret, 10*time.Minute)
	if err != nil {
		writeJSON(w, 500, map[string]string{"error": "turn"})
		return
	}
	writeJSON(w, 200, map[string]any{"username": user, "credential": pass})
}

func (s *server) apiSFULayout(w http.ResponseWriter, r *http.Request) {
	l := s.getLeg(r.PathValue("id"))
	if l == nil || l.sl == nil {
		writeJSON(w, 404, map[string]string{"error": "unbekannt"})
		return
	}
	st := l.stats()
	writeJSON(w, 200, map[string]any{"slots": l.sl.Layout(), "est_kbit": l.sl.EstimateKbit(), "stats": st,
		"pauses": l.fl.Stats.Pauses.Load(), "tid_skipped": l.fl.Stats.TIDSkipped.Load(), "write_stalls": l.fl.Stats.WriteStalls.Load()})
}

// twccTap (nur Tests, vor newServer setzen) bekommt jede transport-cc-Rueckmeldung, die der Knoten an Geraete schickt.
var twccTap func(*rtcp.TransportLayerCC)

type twccTapFactory struct{}

func (twccTapFactory) NewInterceptor(string) (interceptor.Interceptor, error) {
	return &twccTapI{}, nil
}

type twccTapI struct{ interceptor.NoOp }

func (*twccTapI) BindRTCPWriter(w interceptor.RTCPWriter) interceptor.RTCPWriter {
	return interceptor.RTCPWriterFunc(func(ps []rtcp.Packet, a interceptor.Attributes) (int, error) {
		for _, p := range ps {
			if cc, ok := p.(*rtcp.TransportLayerCC); ok && twccTap != nil {
				twccTap(cc)
			}
		}
		return w.Write(ps, a)
	})
}
