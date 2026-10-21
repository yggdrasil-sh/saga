package main

import (
	"bytes"
	"crypto/rand"
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

	pcs     []*webrtc.PeerConnection
	legIDs  []string
	streams []*sfuStream
	stop    chan struct{}
	once    sync.Once

	mu      sync.Mutex
	rxAudio map[uint32]uint64 // CSRC -> Pakete
	rxVideo map[uint32]uint64
	rxPkts  atomic.Uint64
	rxLost  atomic.Uint64
	frames  map[*webrtc.TrackRemote]*frameClock
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
	se.SetIPFilter(func(ip net.IP) bool { return ip.IsLoopback() })
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
		if err := postJSON(hc, base+"/api/dev/token", map[string]any{"room": room, "peer": b.peer, "role": sp.role,
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
		if sp.sendA {
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
		}
		if err := postJSON(hc, base+"/api/sfu/leg", sfuLegReq{Token: tok.Token, Path: b.path, Offer: pc.LocalDescription().SDP,
			Audio: sp.audio, Speaker: sp.speaker, Tiles: sp.tiles, Device: device}, &ans); err != nil {
			return err
		}
		b.legIDs = append(b.legIDs, ans.ID)
		if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: ans.Answer}); err != nil {
			return err
		}
		for i, tx := range txs {
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

func (b *sfuBot) onTrack(tr *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
	video := tr.Kind() == webrtc.RTPCodecTypeVideo
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
		b.mu.Lock()
		if video {
			b.rxVideo[c]++
			if h.Marker && hn < n {
				fc.frame(time.Now().UnixNano(), h.Timestamp)
			}
		} else {
			b.rxAudio[c]++
		}
		b.mu.Unlock()
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
	for {
		select {
		case <-stop:
			return
		case <-tick.C:
		}
		frames++
		ts += tsStep
		if s.kind == "a" {
			pkt.Header = rtp.Header{Version: 2, PayloadType: 111, SequenceNumber: seq, Timestamp: ts}
			lv := uint8(s.owner.level.Load())
			if s.ext != 0 {
				_ = pkt.Header.SetExtension(s.ext, []byte{0x80 | lv})
			}
			pkt.Payload = data[:frameBytes+trailer]
			pkt.Payload[0] = 0x78
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
		size += trailer
		if size > len(data)-2 {
			size = len(data) - 2
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
			p = append(p, data[off:off+chunk]...)
			if off == 0 {
				if key {
					p[6] &^= 0x01
				} else {
					p[6] |= 0x01
				}
			}
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
