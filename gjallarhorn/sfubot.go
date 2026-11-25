package main

import (
	"bytes"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/logging"
	"github.com/pion/rtp"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
)

// sfuBot ist ein synthetisches Geraet fuer bench --sfu und Tests: es sendet
// Ton (40 ms, audio-level) und VP8-Simulcast mit PictureID und zeitlichen
// Schichten und zaehlt, was es von wem (CSRC) bekommt.
type sfuBot struct {
	peer   string
	path   string
	video  bool
	level  atomic.Uint32 // 127 = still
	layers []string      // gesendete Schichten, z. B. l, m
	// bend (nur Tests, 2.5 A3 Weg "ws"): Antwort-SDP je Leitung vor dem Setzen biegen (passiver TCP-Kandidat -> Tunnel)
	bend func(role, sdp string) string
	toks map[string]string // nur Tests: Medien-Token von Saga je Leitung (sonst Entwicklungs-Token)

	pcs     []*webrtc.PeerConnection
	legIDs  []string
	streams []*sfuStream
	stop    chan struct{}
	once    sync.Once

	mu      sync.Mutex
	rxAudio map[uint32]uint64 // CSRC -> Pakete
	rxLevel map[uint32]uint8  // CSRC -> zuletzt empfangener Pegel (audio-level, -dBov)
	rxVideo map[uint32]uint64
	rxPkts  atomic.Uint64
	rxLost  atomic.Uint64
	frames  map[*webrtc.TrackRemote]*frameClock

	// 2263-2 (horn-bot): Frame-E2E saga-horn/v1 und Messung; alles nil/0 = wie bisher (bench, Tests)
	e2eTx   *e2eSender                    // eigener Schluessel: Rahmen versiegeln (ohne Schluessel wird nichts gesendet)
	e2eRx   *e2eRecv                      // Schluessel der anderen: Rahmen zusammensetzen und oeffnen
	refOf   func(csrc uint32) string      // CSRC -> Kennung des Absenders am Knoten (peers)
	onFirst func(kind string)             // erster (aufgegangener) Ton bzw. erstes Bild
	onASlot func(csrc uint32)             // Ton-Platz wechselt den Absender (CSRC)
	onRx    func(from string, ok bool)    // je geoeffnetem bzw. mangels Schluessel verworfenem Rahmen (Nachforderung)
	onLat   func(from string, ms float64) // Bild mit Zeitcode (tcMagic) eines anderen horn-bot aufgegangen: Laufzeit in ms
	noMic   bool                          // kein Ton senden (Begleitgeraet: Token ohne "a"; der Knoten lehnte die Quelle ab)
	csrc    uint32                        // eigene CSRC am Knoten (POST /api/sfu/leg)
	peers   map[string]uint32             // Kennung -> CSRC aus POST /api/sfu/leg
	e2eSt   map[string]uint64             // ok, nokey, auth, replay, format, short, nomap, incomplete
	gotA    bool
	gotV    bool
}

// frameClock misst Rahmenabstaende eines Empfangsplatzes (Einfrieren nach
// der Chrome-Definition: Abstand > max(3 * Mittel, Mittel + 150 ms)).
type frameClock struct {
	class    byte // erster Buchstabe der Spur-ID: s(peaker), t(ile), ...
	frames   int
	freezes  int // nach Ankunft (streng, ohne Jitter-Puffer)
	freezeNs int64
	raw      freezeMeter
	jb       freezeMeter // Wiedergabe mit festem Jitter-Puffer-Ziel
	minTr    float64     // kleinste Laufzeit (Ankunft - Zeitstempel)
	tsBase   uint32
	tsWrap   int64
	lastTS   uint32
	started  bool
}

// JitterTarget ist das Jitter-Puffer-Ziel der Wiedergabe-Messung (Empfehlung
// fuer Clients auf TCP-Wegen: RTCRtpReceiver.jitterBufferTarget).
var JitterTarget = 150 * time.Millisecond

// freezeMeter zaehlt Einfrieren nach der Chrome-Definition: Abstand zweier
// Bilder > max(3 x Mittel, Mittel + 150 ms).
type freezeMeter struct {
	last     int64
	avg      float64
	n, count int
	ns       int64
}

func (m *freezeMeter) add(t int64) {
	m.n++
	if m.last != 0 {
		d := float64(t - m.last)
		if m.avg > 0 && m.n > 20 {
			lim := 3 * m.avg
			if m.avg+150e6 > lim {
				lim = m.avg + 150e6
			}
			if d > lim {
				m.count++
				m.ns += int64(d)
			}
		}
		if m.avg == 0 {
			m.avg = d
		} else {
			m.avg = 0.95*m.avg + 0.05*d
		}
	}
	m.last = t
}

func (f *frameClock) frame(now int64, ts uint32) {
	f.frames++
	f.raw.add(now)
	f.freezes, f.freezeNs = f.raw.count, f.raw.ns
	// Wiedergabe: Zeitstempel in Wanduhr, Ankunft frühestens nach kleinster
	// Laufzeit + Ziel (wie ein Jitter-Puffer mit festem Mindestverzug)
	if !f.started {
		f.started, f.tsBase, f.lastTS = true, ts, ts
	}
	if ts < f.lastTS && f.lastTS-ts > 1<<31 {
		f.tsWrap += 1 << 32
	}
	f.lastTS = ts
	media := float64(int64(ts)+f.tsWrap-int64(f.tsBase)) / 90000 * 1e9
	tr := float64(now) - media
	if f.frames == 1 || tr < f.minTr {
		f.minTr = tr
	}
	play := f.minTr + media + float64(JitterTarget)
	if float64(now) > play {
		play = float64(now)
	}
	f.jb.add(int64(play))
}

type sfuStream struct {
	tr    *webrtc.TrackLocalStaticRTP
	kind  string // a, l, m, h
	ssrc  uint32 // SSRC des Senders (IV des Frame-E2E)
	key   atomic.Bool
	ext   uint8 // audio-level-ID
	owner *sfuBot
}

func sfuBotAPI(path string) (*webrtc.API, error) {
	se := webrtc.SettingEngine{}
	lf := logging.NewDefaultLoggerFactory()
	lf.DefaultLogLevel = logging.LogLevelError
	se.LoggerFactory = lf
	se.SetICEMulticastDNSMode(ice.MulticastDNSModeDisabled)
	se.SetIncludeLoopbackCandidate(true)
	se.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() || (sfuBotAllIPs && ip.To4() != nil) })
	if path == "udp" {
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	} else {
		se.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeTCP4})
	}
	m := &webrtc.MediaEngine{}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}, PayloadType: 111}, webrtc.RTPCodecTypeAudio); err != nil {
		return nil, err
	}
	if err := m.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000,
		RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack", Parameter: "pli"}}}, PayloadType: 96}, webrtc.RTPCodecTypeVideo); err != nil {
		return nil, err
	}
	if err := m.RegisterHeaderExtension(webrtc.RTPHeaderExtensionCapability{URI: sdp.AudioLevelURI}, webrtc.RTPCodecTypeAudio); err != nil {
		return nil, err
	}
	ir := &interceptor.Registry{}
	if err := webrtc.ConfigureRTCPReports(ir); err != nil {
		return nil, err
	}
	// Rueckmeldung wie im Browser, damit GCC im Knoten schaetzen kann
	if err := webrtc.ConfigureTWCCSender(m, ir); err != nil {
		return nil, err
	}
	return webrtc.NewAPI(webrtc.WithMediaEngine(m), webrtc.WithInterceptorRegistry(ir), webrtc.WithSettingEngine(se)), nil
}

func postJSON(hc *http.Client, url string, in, out any) error {
	b, _ := json.Marshal(in)
	resp, err := hc.Post(url, "application/json", bytes.NewReader(b))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != 200 {
		return fmt.Errorf("%s: %d %s", url, resp.StatusCode, body)
	}
	return json.Unmarshal(body, out)
}

// join verbindet den Bot mit dem Raum (dev-Token). tiles = Kachelplaetze.
func (b *sfuBot) join(hc *http.Client, base string, api *webrtc.API, room string, tiles int, device string) error {
	b.stop = make(chan struct{})
	b.rxAudio, b.rxVideo, b.frames = map[uint32]uint64{}, map[uint32]uint64{}, map[*webrtc.TrackRemote]*frameClock{}
	b.rxLevel = map[uint32]uint8{}
	type spec struct {
		role         string
		sendA, sendV bool
		audio, tiles int
		speaker      bool
	}
	specs := []spec{{"audio", true, false, 3, 0, false}, {"video", false, b.video, 0, tiles, true}}
	if b.path == "udp" {
		specs = []spec{{"both", true, b.video, 3, tiles, true}}
	}
	for _, sp := range specs {
		var tok struct{ Token string }
		if b.toks != nil {
			tok.Token = b.toks[sp.role]
		} else if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": room, "peer": b.peer, "role": sp.role,
			"device": device, "udp": b.path == "udp"}, &tok); err != nil {
			return err
		}
		pc, err := api.NewPeerConnection(webrtc.Configuration{})
		if err != nil {
			return err
		}
		b.pcs = append(b.pcs, pc)
		var txs []*webrtc.RTPTransceiver
		var sts []*sfuStream
		add := func(kind string) error {
			c := webrtc.RTPCodecCapability{MimeType: mimeOpus, ClockRate: 48000, Channels: 2}
			if kind != "a" {
				c = webrtc.RTPCodecCapability{MimeType: mimeVP8, ClockRate: 90000}
			}
			tr, err := webrtc.NewTrackLocalStaticRTP(c, kind, "cam-"+b.peer)
			if err != nil {
				return err
			}
			tx, err := pc.AddTransceiverFromTrack(tr, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly})
			if err != nil {
				return err
			}
			st := &sfuStream{tr: tr, kind: kind, owner: b}
			st.key.Store(true)
			txs = append(txs, tx)
			sts = append(sts, st)
			go func() {
				buf := make([]byte, 1500)
				for {
					n, _, err := tx.Sender().Read(buf)
					if err != nil {
						return
					}
					if hasKeyRequest(buf[:n]) {
						st.key.Store(true)
					}
				}
			}()
			return nil
		}
		if sp.sendA && !b.noMic {
			if err := add("a"); err != nil {
				return err
			}
		}
		if sp.sendV {
			for _, l := range b.layers {
				if err := add(l); err != nil {
					return err
				}
			}
		}
		nv := sp.tiles
		if sp.speaker {
			nv++
		}
		for i := 0; i < sp.audio; i++ {
			if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
				return err
			}
		}
		for i := 0; i < nv; i++ {
			if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly}); err != nil {
				return err
			}
		}
		pc.OnTrack(b.onTrack)
		offer, err := pc.CreateOffer(nil)
		if err != nil {
			return err
		}
		gather := webrtc.GatheringCompletePromise(pc)
		if err := pc.SetLocalDescription(offer); err != nil {
			return err
		}
		<-gather
		var ans struct {
			ID     string
			Answer string
			CSRC   uint32            `json:"csrc"`
			Peers  map[string]uint32 `json:"peers"`
		}
		if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: b.path, Offer: pc.LocalDescription().SDP,
			Audio: sp.audio, Speaker: sp.speaker, Tiles: sp.tiles, Device: device}, &ans); err != nil {
			return err
		}
		b.legIDs = append(b.legIDs, ans.ID)
		b.mu.Lock()
		if ans.CSRC != 0 {
			b.csrc = ans.CSRC
		}
		if b.peers == nil {
			b.peers = map[string]uint32{}
		}
		for k, v := range ans.Peers {
			b.peers[k] = v
		}
		b.mu.Unlock()
		if b.bend != nil {
			ans.Answer = b.bend(sp.role, ans.Answer)
		}
		if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
			return err
		}
		for i, tx := range txs {
			if enc := tx.Sender().GetParameters().Encodings; len(enc) > 0 {
				sts[i].ssrc = uint32(enc[0].SSRC)
			}
			if sts[i].kind == "a" {
				for _, h := range tx.Sender().GetParameters().HeaderExtensions {
					if h.URI == sdp.AudioLevelURI {
						sts[i].ext = uint8(h.ID)
					}
				}
			}
		}
		b.streams = append(b.streams, sts...)
	}
	return nil
}

func (b *sfuBot) onTrack(tr *webrtc.TrackRemote, rc *webrtc.RTPReceiver) {
	video := tr.Kind() == webrtc.RTPCodecTypeVideo
	var lvID uint8 // audio-level wie bei Browser und Apps (getSynchronizationSources)
	if !video && rc != nil {
		for _, h := range rc.GetParameters().HeaderExtensions {
			if h.URI == sdp.AudioLevelURI {
				lvID = uint8(h.ID)
			}
		}
	}
	fc := &frameClock{}
	if id := tr.ID(); id != "" {
		fc.class = id[0]
	}
	b.mu.Lock()
	b.frames[tr] = fc
	b.mu.Unlock()
	buf := make([]byte, 1500)
	var h rtp.Header
	var last uint16
	first := true
	var asm vp8Asm  // nur mit Frame-E2E: Rahmen zusammensetzen
	var slot uint32 // Ton: aktueller Absender des Platzes
	for {
		n, _, err := tr.Read(buf)
		if err != nil {
			return
		}
		hn, err := h.Unmarshal(buf[:n])
		if err != nil {
			continue
		}
		b.rxPkts.Add(1)
		if !first {
			if d := h.SequenceNumber - last; d > 1 && d < 1000 {
				b.rxLost.Add(uint64(d - 1))
			}
		}
		first, last = false, h.SequenceNumber
		var c uint32
		if len(h.CSRC) > 0 {
			c = h.CSRC[0]
		}
		if !video && c != 0 && c != slot {
			slot = c
			if b.onASlot != nil {
				b.onASlot(c)
			}
		}
		if b.e2eRx != nil {
			b.rxE2E(video, fc, &asm, &h, c, buf[hn:n])
			continue
		}
		var firstKind string
		b.mu.Lock()
		if video {
			b.rxVideo[c]++
			if h.Marker && hn < n {
				fc.frame(time.Now().UnixNano(), h.Timestamp)
				if !b.gotV {
					b.gotV, firstKind = true, "video"
				}
			}
		} else {
			b.rxAudio[c]++
			if e := h.GetExtension(lvID); lvID != 0 && len(e) > 0 {
				b.rxLevel[c] = e[0] & 0x7f
			}
			if !b.gotA {
				b.gotA, firstKind = true, "audio"
			}
		}
		b.mu.Unlock()
		if firstKind != "" && b.onFirst != nil {
			b.onFirst(firstKind)
		}
	}
}

// vp8Asm setzt einen VP8-Rahmen aus RTP-Paketen zusammen (ohne Nachsenden: Luecke = Rahmen verloren).
type vp8Asm struct {
	ts     uint32
	next   uint16
	active bool
	ok     bool
	buf    []byte
}

// vp8DescLen: Laenge des VP8-Nutzlastkopfs (RFC 7741), -1 = kaputt.
func vp8DescLen(p []byte) int {
	if len(p) < 1 {
		return -1
	}
	n := 1
	if p[0]&0x80 != 0 {
		if len(p) < 2 {
			return -1
		}
		x := p[1]
		n = 2
		if x&0x80 != 0 {
			if len(p) < n+1 {
				return -1
			}
			if p[n]&0x80 != 0 {
				n += 2
			} else {
				n++
			}
		}
		if x&0x40 != 0 {
			n++
		}
		if x&0x30 != 0 {
			n++
		}
	}
	if n >= len(p) {
		return -1
	}
	return n
}

func (b *sfuBot) e2eCount(k string) {
	b.mu.Lock()
	if b.e2eSt == nil {
		b.e2eSt = map[string]uint64{}
	}
	b.e2eSt[k]++
	b.mu.Unlock()
}

// rxE2E: Empfang mit Frame-E2E - Ton je Paket, Bild je zusammengesetztem Rahmen oeffnen; nur aufgegangene Rahmen zaehlen
// fuer Einfrieren und "erstes Bild/Ton".
func (b *sfuBot) rxE2E(video bool, fc *frameClock, a *vp8Asm, h *rtp.Header, c uint32, pl []byte) {
	var frame []byte
	if video {
		dl := vp8DescLen(pl)
		if a.active && (h.Timestamp != a.ts) {
			b.e2eCount("incomplete")
			a.active = false
		}
		if !a.active {
			a.active, a.ts, a.buf = true, h.Timestamp, a.buf[:0]
			a.ok = dl > 0 && pl[0]&0x10 != 0 && pl[0]&0x07 == 0
		} else if h.SequenceNumber != a.next {
			a.ok = false
		}
		a.next = h.SequenceNumber + 1
		if dl > 0 {
			a.buf = append(a.buf, pl[dl:]...)
		} else {
			a.ok = false
		}
		if !h.Marker {
			return
		}
		a.active = false
		if !a.ok {
			b.e2eCount("incomplete")
			return
		}
		frame = a.buf
	} else {
		frame = pl
	}
	from := ""
	if b.refOf != nil {
		from = b.refOf(c)
	}
	if from == "" {
		b.e2eCount("nomap")
		return
	}
	key := video && len(frame) > 0 && frame[0]&0x01 == 0
	plain, err := b.e2eRx.open(from, video, key, frame)
	if err != nil {
		b.e2eCount(err.Error())
		if err == errE2ENoKey && b.onRx != nil {
			b.onRx(from, false)
		}
		return
	}
	if b.onRx != nil {
		b.onRx(from, true)
	}
	if video && b.onLat != nil {
		if t, ok := tcGet(plain); ok {
			b.onLat(from, float64(time.Now().UnixMicro()-t)/1000)
		}
	}
	var firstKind string
	b.mu.Lock()
	if b.e2eSt == nil {
		b.e2eSt = map[string]uint64{}
	}
	b.e2eSt["ok"]++
	if video {
		b.rxVideo[c]++
		fc.frame(time.Now().UnixNano(), h.Timestamp)
		if !b.gotV {
			b.gotV, firstKind = true, "video"
		}
	} else {
		b.rxAudio[c]++
		if !b.gotA {
			b.gotA, firstKind = true, "audio"
		}
	}
	b.mu.Unlock()
	if firstKind != "" && b.onFirst != nil {
		b.onFirst(firstKind)
	}
}

// freezes liefert Einfrieren und Rahmen ueber alle Bildplaetze.
func (b *sfuBot) freezes() (n int, dur time.Duration, frames int) {
	n, dur, frames, _ = b.freezeStats()
	return
}

// freezeByClass liefert je Platzart: Einfrieren roh, mit Jitter-Puffer,
// Bildplaetze.
func (b *sfuBot) freezeByClass() map[byte][3]int {
	b.mu.Lock()
	defer b.mu.Unlock()
	m := map[byte][3]int{}
	for _, f := range b.frames {
		if f.frames == 0 {
			continue
		}
		v := m[f.class]
		v[0] += f.raw.count
		v[1] += f.jb.count
		v[2]++
		m[f.class] = v
	}
	return m
}

// jbFreezes: Einfrieren mit Jitter-Puffer-Ziel ueber alle Bildplaetze.
func (b *sfuBot) jbFreezes() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := 0
	for _, f := range b.frames {
		n += f.jb.count
	}
	return n
}

// freezeStats wie freezes, dazu die Zahl der Bildplaetze mit Rahmen.
func (b *sfuBot) freezeStats() (n int, dur time.Duration, frames, tracks int) {
	b.mu.Lock()
	defer b.mu.Unlock()
	for _, f := range b.frames {
		n += f.freezes
		dur += time.Duration(f.freezeNs)
		frames += f.frames
		if f.frames > 0 {
			tracks++
		}
	}
	return
}

func (b *sfuBot) seen() (audio, video map[uint32]uint64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	audio, video = map[uint32]uint64{}, map[uint32]uint64{}
	for k, v := range b.rxAudio {
		audio[k] = v
	}
	for k, v := range b.rxVideo {
		video[k] = v
	}
	return
}

// levels: zuletzt empfangener Pegel je Absender (CSRC); fehlt, wer ohne Pegel ankam.
func (b *sfuBot) levels() map[uint32]uint8 {
	b.mu.Lock()
	defer b.mu.Unlock()
	m := map[uint32]uint8{}
	for k, v := range b.rxLevel {
		m[k] = v
	}
	return m
}

func (b *sfuBot) connected() int {
	c := 0
	for _, pc := range b.pcs {
		if pc.ConnectionState() == webrtc.PeerConnectionStateConnected {
			c++
		}
	}
	return c
}

func (b *sfuBot) start() {
	for _, s := range b.streams {
		go s.run(b.stop)
	}
}

func (b *sfuBot) close() {
	b.once.Do(func() {
		if b.stop != nil {
			close(b.stop)
		}
		for _, pc := range b.pcs {
			_ = pc.Close()
		}
	})
}

// opusSilence: gueltiges Opus-Paket von n Byte (7 ... 261): 40 ms = zwei CELT-Stille-Rahmen (FB 20 ms, Code 3, CBR) mit
// Fuellbytes - Browser dekodieren es (Stille statt Verdeckung), die Groesse bleibt wie ein Sprachpaket.
func opusSilence(dst []byte, n int) []byte {
	if n < 7 {
		n = 7
	}
	if n > 261 { // eine Fuelllaengen-Angabe reicht bis 254 Byte
		n = 261
	}
	dst = append(dst, 0xfb, 0x42, byte(n-7), 0xff, 0xfe, 0xff, 0xfe)
	for len(dst) < n {
		dst = append(dst, 0)
	}
	return dst
}

// run: Ton 25/s (120 B + Trailer), Bild l 180p 20 fps ~200 kbit/s (L1T2),
// m 360p 30 fps ~600 kbit/s (L1T3), h 720p 30 fps ~1,5 Mbit/s. Schluesselbild
// auf PLI und alle 10 s (4-fache Groesse). PictureID 15 Bit.
func (s *sfuStream) run(stop <-chan struct{}) {
	var fps, frameBytes, tlayers int
	var tsStep uint32
	switch s.kind {
	case "a":
		fps, frameBytes, tsStep = 25, 120, 1920
	case "l":
		// 20 fps L1T2: auf TCP-Wegen laesst der Knoten TID 1 weg (10 fps)
		fps, frameBytes, tsStep, tlayers = 20, 200000/8/20, 4500, 2
	case "m":
		fps, frameBytes, tsStep, tlayers = 30, 600000/8/30, 3000, 3
	default:
		fps, frameBytes, tsStep, tlayers = 30, 1500000/8/30, 3000, 3
	}
	const trailer = 13
	const maxPayload = 1150
	tick := time.NewTicker(time.Second / time.Duration(fps))
	defer tick.Stop()
	var seq, pic uint16
	var tl0 uint8
	var ts uint32
	var data [8192]byte
	_, _ = rand.Read(data[:])
	frames := 0
	pkt := &rtp.Packet{Header: rtp.Header{Version: 2}}
	pbuf := make([]byte, 0, 1200)
	fbuf := make([]byte, 0, len(data)+64)
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
		}
		frames++
		ts += tsStep
		e2e := s.owner.e2eTx
		if s.kind == "a" {
			pkt.Header = rtp.Header{Version: 2, PayloadType: 111, SequenceNumber: seq, Timestamp: ts}
			lv := uint8(s.owner.level.Load())
			if s.ext != 0 {
				_ = pkt.Header.SetExtension(s.ext, []byte{0x80 | lv})
			}
			if e2e != nil {
				if pkt.Payload = e2e.seal(false, false, s.ssrc, ts, opusSilence(fbuf[:0], frameBytes)); pkt.Payload == nil {
					continue // ohne Schluessel nichts senden (nie Klartext)
				}
			} else {
				pkt.Payload = opusSilence(fbuf[:0], frameBytes+trailer)
			}
			seq++
			if err := s.tr.WriteRTP(pkt); err != nil && errors.Is(err, io.ErrClosedPipe) {
				return
			}
			continue
		}
		key := s.key.Swap(false) || frames%(10*fps) == 1
		tid := uint8(0)
		switch tlayers {
		case 2:
			tid = uint8(frames % 2)
		case 3:
			tid = [4]uint8{0, 2, 1, 2}[frames%4]
		}
		if key {
			tid = 0
		}
		if tid == 0 {
			tl0++
		}
		sync := tid > 0 && (frames%4 == 1 || frames%4 == 2)
		size := frameBytes
		if key {
			size *= 4
		}
		if e2e == nil {
			size += trailer
		}
		if size > len(data)-2 {
			size = len(data) - 2
		}
		// codierter Rahmen: VP8-Rahmenkopf (P-Bit; Schluesselbild mit Startcode und Groesse), Rest Zufall
		fr := append(fbuf[:0], data[:size]...)
		if key {
			fr[0] &^= 0x01
			fr[3], fr[4], fr[5] = 0x9d, 0x01, 0x2a
			fr[6], fr[7], fr[8], fr[9] = 0x80, 0x02, 0x68, 0x01 // 640 x 360
		} else {
			fr[0] |= 0x01
		}
		tcPut(fr) // Zeitcode (Sendezeit) im Zufallsteil: andere horn-bots messen die Laufzeit (nur mit Frame-E2E ausgewertet)
		if e2e != nil {
			if fr = e2e.seal(true, key, s.ssrc, ts, fr); fr == nil {
				continue
			}
			size = len(fr)
		}
		pic = (pic + 1) & 0x7fff
		for off := 0; off < size; {
			chunk := size - off
			if chunk > maxPayload {
				chunk = maxPayload
			}
			start := byte(0)
			if off == 0 {
				start = 0x10
			}
			t := tid << 6
			if sync {
				t |= 0x20
			}
			p := append(pbuf[:0], 0x80|start, 0xe0, 0x80|byte(pic>>8), byte(pic), tl0, t)
			p = append(p, fr[off:off+chunk]...)
			off += chunk
			pkt.Header = rtp.Header{Version: 2, PayloadType: 96, Marker: off >= size, SequenceNumber: seq, Timestamp: ts}
			pkt.Payload = p
			seq++
			if err := s.tr.WriteRTP(pkt); err != nil && errors.Is(err, io.ErrClosedPipe) {
				return
			}
		}
	}
}

// Zeitcode im synthetischen Bild (2263-9, Bugs-DB 2438): tcMagic + Sendezeit (Unix-Mikrosekunden, big endian) ab Byte
// tcOff - hinter dem VP8-Rahmenkopf (Schluesselbild 10 Byte), im sonst zufaelligen Teil. Browser dekodieren das Bild
// ohnehin nicht; andere horn-bots lesen den Zeitcode nach dem Oeffnen (Frame-E2E) und messen die Laufzeit Bot -> Bot.
var tcMagic = [4]byte{'H', 'L', 'T', 'C'}

const tcOff = 10

func tcPut(fr []byte) {
	if len(fr) < tcOff+12 {
		return
	}
	copy(fr[tcOff:], tcMagic[:])
	binary.BigEndian.PutUint64(fr[tcOff+4:], uint64(time.Now().UnixMicro()))
}

func tcGet(fr []byte) (int64, bool) {
	if len(fr) < tcOff+12 || [4]byte(fr[tcOff:tcOff+4]) != tcMagic {
		return 0, false
	}
	return int64(binary.BigEndian.Uint64(fr[tcOff+4:])), true
}
