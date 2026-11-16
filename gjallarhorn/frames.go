package main

// Browser-Weg B2 "Schleifen-PeerConnection + WebSocket" (Saga 2.5, Vertrag vertrag-a3.md S12, docs/APP-API.md
// "Rahmen-Weg saga-frames.1"): Browser hinter einem TLS-beendenden Proxy (NPM mit "Websockets Support") fuehren ihre
// Medien als kodierte Rahmen (VP8/Opus aus RTCRtpScriptTransform, schon E2E-versiegelt) ueber eine WebSocket-Verbindung
// zur normalen Saga-Adresse; saga-media reicht sie roh an den eingebauten Knoten (wie den Tunnel, tunnel.go). Kein ICE,
// kein DTLS/SRTP: TLS bis zum Proxy, der Inhalt bleibt Ende zu Ende versiegelt.
//
// Fuer den Knoten ist das ein normales Bein (Weg "ws", fuer internal/sfu ein Stromweg wie ICE-TCP): eingehende Rahmen
// werden RTP (VP8-Descriptor mit PictureID/TL0PICIDX/TID/Y, Opus mit audio-level) und gehen als fwd.Source in den Raum;
// abgehend setzt ein fwd.Writer je Platz die RTP-Pakete wieder zu Rahmen zusammen. Alle anderen Teilnehmenden (UDP,
// ICE-TCP, TURN-TLS, A3-Tunnel) bleiben unveraendert im selben Anruf.
//
// Je Geraet zwei WebSockets (Ton-, Bild-Leitung; wie H1 auf TCP). Abwaerts Fenster wie beim Tunnel (wstun.Flow,
// Quittungen des Browsers), aufwaerts Rueckmeldung (Verzoegerungszunahme) fuer die Senderegelung im Browser und eine
// Eingangsdrossel nach max_kbit aus dem Token.

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"math/rand/v2"
	"net"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// Protokoll saga-frames.1 (Vertrag S12).
const (
	framesSubprotocol = "saga-frames.1"
	framesVersion     = 1

	frMsgAck   = 0x01 // Browser -> Knoten: Quittung (9 bzw. 11 Byte)
	frMsgFb    = 0x02 // Knoten -> Browser: Rueckmeldung aufwaerts (17 Byte)
	frMsgFrame = 0x10 // beide Richtungen: Rahmen

	frHdr    = 16
	frMaxMsg = 1 << 20

	frFlagKey  = 0x01
	frFlagSync = 0x02

	frTrackAudio  = 0
	frTrackL      = 1
	frTrackM      = 2
	frTrackScreen = 3

	frMinWindow = 16 << 10 // ganze Rahmen (S12.4); 64 KiB des Tunnels hielten bei 600 kbit/s fast 1 s Warteschlange
	frPayload   = 1100     // Nutzlast je RTP-Paket (Platz fuer Kopf/CSRC/SRTP der anderen Wege)
	frClampMs   = 80       // gemeldete Verzoegerungszunahme abwaerts, ab der das Mindestfenster gilt
	frDefKbit   = 4000     // Eingangsdrossel ohne max_kbit im Token (wie saga/horn.py MAX_KBIT)
)

// Fristen (Tests aendern sie).
var (
	framesAuthWait = newDurVar(10 * time.Second)
	framesPing     = 25 * time.Second
	framesFbEvery  = 50 * time.Millisecond
	framesWaitMax  = newDurVar(400 * time.Millisecond) // laenger wartet ein Rahmen nicht auf das Fenster (dann verwerfen)
	framesPLIEvery = 500 * time.Millisecond
)

// durVar: Frist, die Tests aendern (atomar - Handler laengst beendeter Tests laufen auf gekaperten Verbindungen weiter).
type durVar struct{ v atomic.Int64 }

func newDurVar(d time.Duration) *durVar { x := &durVar{}; x.v.Store(int64(d)); return x }
func (x *durVar) get() time.Duration    { return time.Duration(x.v.Load()) }
func (x *durVar) set(d time.Duration) time.Duration {
	return time.Duration(x.v.Swap(int64(d)))
}

// framesJoin ist die erste Nachricht des Browsers (S12.2).
type framesJoin struct {
	T       string `json:"t"`
	V       int    `json:"v"`
	Token   string `json:"token"`
	Audio   int    `json:"audio"`
	Speaker bool   `json:"speaker"`
	Screen  bool   `json:"screen"`
	Tiles   int    `json:"tiles"`
	Device  string `json:"device"`
}

// Close-Codes zusaetzlich zu wstun (S12.2).
const (
	frCloseRoom  = 4404
	frCloseLimit = 4429
)

// parseFramesJoin prueft die erste Nachricht -> Fehlercode ("" = in Ordnung) und Close-Code.
func parseFramesJoin(mt int, b []byte) (framesJoin, string, int) {
	var j framesJoin
	if mt != websocket.TextMessage || len(b) > wstun.MaxText {
		return j, "bad", wstun.CloseBad
	}
	if err := json.Unmarshal(b, &j); err != nil || j.T != "join" || j.V < 1 {
		return j, "bad", wstun.CloseBad
	}
	if j.V != framesVersion {
		return j, "version", wstun.CloseBad
	}
	if j.Token == "" || len(j.Token) > 3<<10 {
		return j, "auth", wstun.CloseAuth
	}
	if j.Audio < 0 || j.Audio > 8 || j.Tiles < 0 || j.Tiles > 30 {
		return j, "bad", wstun.CloseBad
	}
	return j, "", 0
}

func framesErr(ws *websocket.Conn, code string, closeCode int) {
	m := map[string]any{"t": "err", "v": framesVersion, "code": code}
	if code == "version" {
		m["max"] = framesVersion
	}
	b, _ := json.Marshal(m)
	_ = ws.SetWriteDeadline(time.Now().Add(2 * time.Second))
	_ = ws.WriteMessage(websocket.TextMessage, b)
	_ = ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(closeCode, code), time.Now().Add(time.Second))
}

// apiSFUFrames: GET /api/sfu/ws mit saga-frames.1 (Upgrade und Unterprotokoll schon geprueft, tunnel.go apiSFUWS).
func (s *server) apiSFUFrames(w http.ResponseWriter, r *http.Request) {
	t := s.tun
	ip := ipKey(r)
	authed, release, ok := t.admit(ip, s.cfg)
	if !ok {
		writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "limit"})
		return
	}
	up := websocket.Upgrader{ReadBufferSize: 64 << 10, WriteBufferSize: 64 << 10, Subprotocols: []string{framesSubprotocol},
		HandshakeTimeout: 10 * time.Second, EnableCompression: false,
		// Jede Herkunft wie beim Tunnel: keine Cookies, der Zugang haengt allein am Medien-Token der ersten Nachricht
		CheckOrigin: func(*http.Request) bool { return true }}
	ws, err := up.Upgrade(w, r, nil)
	if err != nil {
		authed()
		release()
		return
	}
	if tc, ok := ws.UnderlyingConn().(*net.TCPConn); ok {
		_ = tc.SetNoDelay(true)
	}
	ws.SetReadLimit(wstun.MaxText + 64)
	_ = ws.SetReadDeadline(time.Now().Add(framesAuthWait.get()))
	mt, b, err := ws.ReadMessage()
	_ = ws.SetReadDeadline(time.Time{})
	authed()
	if err != nil {
		code, cc := "bad", wstun.CloseBad
		if ne, ok := err.(net.Error); ok && ne.Timeout() {
			code, cc = "timeout", wstun.CloseTimeout
		}
		framesErr(ws, code, cc)
		_ = ws.Close()
		release()
		return
	}
	j, code, cc := parseFramesJoin(mt, b)
	var l *leg
	if code == "" {
		l, code, cc = s.newFramesLeg(j, ip)
		if code == "auth" {
			t.authFail.Add(1)
		}
	}
	if code != "" {
		framesErr(ws, code, cc)
		_ = ws.Close()
		release()
		return
	}
	f := newFrLeg(s, l, ws)
	if !l.attachFrames(f) { // Bein schon wieder zu (ersetzt, Raum geschlossen)
		framesErr(ws, "room", frCloseRoom)
		_ = ws.Close()
		release()
		return
	}
	t.frTotal.Add(1)
	t.frOpen.Add(1)
	t.lastOpen.Store(time.Now().Unix())
	peers := map[string]uint32{}
	if rm := s.node.Room(l.sl.Grant.Room); rm != nil {
		peers = rm.Peers()
	}
	okMsg, _ := json.Marshal(map[string]any{"t": "ok", "v": framesVersion, "id": l.id, "csrc": l.sl.Peer.CSRC, "slots": l.sl.Layout(),
		"peers": peers, "state": s.nodeState(), "ack_bytes": wstun.DefAckBytes, "ack_ms": int(wstun.DefAckDelay / time.Millisecond),
		"fb_ms": int(framesFbEvery / time.Millisecond)})
	f.wmu.Lock()
	_ = ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	err = ws.WriteMessage(websocket.TextMessage, okMsg)
	f.wmu.Unlock()
	if err != nil {
		f.close("schreiben")
	} else {
		f.bind()
		go f.feedback()
		go f.ping()
		f.read()
	}
	f.close("gelesen")
	s.closeLeg(l, "ws zu")
	t.frOpen.Add(-1)
	release()
}

// newFramesLeg: Token pruefen, Grenzen, Bein im Raum anlegen (wie newSFULeg ohne PeerConnection) -> Bein bzw.
// Fehlercode und Close-Code (S12.2).
func (s *server) newFramesLeg(j framesJoin, ipKey string) (*leg, string, int) {
	if s.draining.Load() || s.closing.Load() {
		return nil, "unavailable", wstun.CloseUnavailable
	}
	g, room, err := s.admit(j.Token, sfu.PathTCP)
	if err != nil {
		switch {
		case errors.Is(err, sfu.ErrNoRoom):
			return nil, "room", frCloseRoom
		case errors.Is(err, errDraining):
			return nil, "unavailable", wstun.CloseUnavailable
		}
		return nil, "auth", wstun.CloseAuth
	}
	if !s.used.use(g.ID, "leg", g.Exp) {
		return nil, "auth", wstun.CloseAuth
	}
	if sfu.ParseDevice(j.Device) == sfu.Mobile {
		g.Device = sfu.Mobile
	}
	s.mu.Lock()
	if len(s.legs) >= s.cfg.MaxLegs || (!s.cfg.Bench && !s.cfg.Dev && ipKey != "" && s.perIP[ipKey] >= s.cfg.maxPerIP()) {
		s.mu.Unlock()
		return nil, "limit", frCloseLimit
	}
	l := &leg{id: randHex(8), sid: g.Peer, path: "ws", role: g.Role.String(), ipKey: ipKey, created: time.Now(), framesLeg: true}
	s.legs[l.id] = l
	s.perIP[ipKey]++
	s.mu.Unlock()
	sl, err := room.AddLeg(g, sfu.PathTCP, sfu.Want{Audio: j.Audio, Speaker: j.Speaker, Screen: j.Screen, Tiles: j.Tiles})
	if err != nil {
		s.closeLeg(l, "aufbau fehlgeschlagen")
		if errors.Is(err, sfu.ErrFull) || errors.Is(err, sfu.ErrQuota) {
			return nil, "limit", frCloseLimit
		}
		return nil, "bad", wstun.CloseBad
	}
	s.mu.Lock()
	l.mu.Lock()
	l.sl = sl
	l.fl = sl.FwdLeg()
	closed := l.closed
	if !closed {
		l.connected = time.Now()
	}
	l.mu.Unlock()
	if !closed {
		s.bySFU[sl] = l
	}
	s.mu.Unlock()
	if closed {
		sl.Close()
		return nil, "room", frCloseRoom
	}
	log.Printf("bein auf %s modus=node weg=ws art=frames", shortID(l.id))
	return l, "", 0
}

// attachFrames haengt die WebSocket-Seite an das Bein (false: Bein schon zu).
func (l *leg) attachFrames(f *frLeg) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.closed {
		return false
	}
	l.fr = f
	return true
}

// frStats: Zaehler eines Rahmen-Beins (GET /api/sfu/leg/{id} -> stats.frames).
type frStats struct {
	UpFrames   uint64 `json:"up_frames"`
	UpBytes    uint64 `json:"up_bytes"`
	UpDropped  uint64 `json:"up_dropped,omitempty"` // Eingangsdrossel (max_kbit)
	UpGaps     uint64 `json:"up_gaps,omitempty"`    // Luecken in der Rahmen-Nr. des Browsers
	UpWaitKey  uint64 `json:"up_waitkey,omitempty"` // verworfen, weil die Spur auf ein Schluesselbild wartet
	DownFrames uint64 `json:"down_frames"`
	DownDrop   uint64 `json:"down_dropped,omitempty"` // Fenster blieb voll (> 400 ms)
	DownBroken uint64 `json:"down_broken,omitempty"`  // Rahmen nach Paketluecke verworfen (bis Schluesselbild)
	KeyReq     uint64 `json:"key_req,omitempty"`      // Schluesselbild-Anforderungen des Browsers (Text key)
	PLI        uint64 `json:"pli,omitempty"`          // Schluesselbild-Anforderungen an den Browser (Text pli)
	QUpMs      int64  `json:"q_up_ms"`                // Verzoegerungszunahme aufwaerts (zuletzt)
	QDownMs    int64  `json:"q_down_ms"`              // ... abwaerts laut Browser
}

// frLeg ist die WebSocket-Seite eines Rahmen-Beins.
type frLeg struct {
	s    *server
	l    *leg
	ws   *websocket.Conn
	sl   *sfu.Leg
	g    sfu.Grant
	wmu  sync.Mutex
	done chan struct{}
	once sync.Once

	st    wstun.Stats
	flow  *wstun.Flow
	wdl   atomic.Int64 // Frist fuer Reserve (je Rahmen gesetzt)
	alive atomic.Int64

	// aufwaerts (nur Lese-Goroutine; umu fuer die Rueckmeldung)
	umu    sync.Mutex
	tracks map[uint8]*frUp
	upB    uint64
	upMin  int64
	upMinT time.Time
	upQ    int64
	upEcho uint32
	upAt   time.Time
	tok    float64 // Eingangsdrossel: Guthaben in Byte
	tokAt  time.Time
	rate   float64 // Byte/s

	c struct {
		upFrames, upBytes, upDropped, upGaps, upWaitKey, downFrames, downDrop, downBroken, keyReq, pli atomic.Uint64
		qDown                                                                                          atomic.Int64
	}
}

// frUp: eine eingehende Spur (Ton, Bild l/m, Bildschirm).
type frUp struct {
	src   *fwd.Source
	kind  fwd.Kind
	top   uint8 // hoechste Zeitschicht dieser Spur (diese Rahmen sind keine Referenz: Drossel darf sie verwerfen)
	ssrc  uint32
	seq   uint16
	tl0   uint8
	pic   uint16 // PictureID (lueckenlos ueber die weitergeleiteten Rahmen)
	last  uint32 // Rahmen-Nr. des Browsers
	start bool
	wait  bool         // nach einer Luecke bzw. Drossel bis zum Schluesselbild
	pliAt atomic.Int64 // letzte Anforderung an den Browser (UnixNano)
	buf   []byte
	out   func([]byte) // nur Tests: statt src.Inject
}

func newFrLeg(s *server, l *leg, ws *websocket.Conn) *frLeg {
	kbit := l.sl.Grant.MaxKbit
	if kbit <= 0 {
		kbit = frDefKbit
	}
	f := &frLeg{s: s, l: l, ws: ws, sl: l.sl, g: l.sl.Grant, done: make(chan struct{}), tracks: map[uint8]*frUp{}, rate: float64(kbit) * 125}
	f.tok, f.tokAt = f.rate, time.Now()
	f.flow = wstun.NewFlow(frMinWindow, wstun.DefMaxWindow, &f.st)
	f.alive.Store(time.Now().UnixNano())
	ws.SetReadLimit(frMaxMsg)
	ws.SetPongHandler(func(string) error { f.alive.Store(time.Now().UnixNano()); return nil })
	return f
}

// bind: Ausgaenge des Beins an die WebSocket-Seite (je Platz ein Schreiber).
func (f *frLeg) bind() {
	for i, o := range f.sl.Outs() {
		o.Bind(&frOut{f: f, slot: uint8(i), o: o, kind: o.Kind}, rand.Uint32(), 96)
	}
}

func (f *frLeg) closed() bool {
	select {
	case <-f.done:
		return true
	default:
		return false
	}
}

func (f *frLeg) close(why string) {
	f.once.Do(func() {
		close(f.done)
		f.flow.Wake()
		f.wmu.Lock()
		_ = f.ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(websocket.CloseNormalClosure, trunc(why, 100)), time.Now().Add(time.Second))
		f.wmu.Unlock()
		_ = f.ws.Close()
		for _, o := range f.sl.Outs() {
			o.Unbind()
		}
		f.umu.Lock()
		srcs := make([]*fwd.Source, 0, len(f.tracks))
		for _, t := range f.tracks {
			if t.src != nil {
				srcs = append(srcs, t.src)
			}
		}
		f.umu.Unlock()
		for _, src := range srcs {
			f.l.endSrc(src)
			if r := f.s.node.Room(f.g.Room); r != nil {
				r.RemoveSource(src)
			}
		}
	})
}

func trunc(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func (f *frLeg) sendJSON(v any) {
	b, _ := json.Marshal(v)
	f.wmu.Lock()
	_ = f.ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	err := f.ws.WriteMessage(websocket.TextMessage, b)
	f.wmu.Unlock()
	if err != nil {
		go f.close("schreiben")
	}
}

// ping: WebSocket-Ping (Proxys mit Leerlauf-Frist, toter Browser); ohne Lebenszeichen 3 x Ping -> Schluss.
func (f *frLeg) ping() {
	tk := time.NewTicker(framesPing)
	defer tk.Stop()
	for {
		select {
		case <-f.done:
			return
		case <-tk.C:
		}
		if time.Since(time.Unix(0, f.alive.Load())) > 3*framesPing {
			f.close("kein lebenszeichen")
			return
		}
		f.wmu.Lock()
		err := f.ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(5*time.Second))
		f.wmu.Unlock()
		if err != nil {
			f.close("ping")
			return
		}
	}
}

// ---------- aufwaerts: Browser -> Knoten ----------

func (f *frLeg) read() {
	for {
		mt, b, err := f.ws.ReadMessage()
		if err != nil {
			return
		}
		f.alive.Store(time.Now().UnixNano())
		if mt == websocket.TextMessage {
			var m struct {
				T    string `json:"t"`
				Slot int    `json:"slot"`
			}
			if len(b) <= 256 && json.Unmarshal(b, &m) == nil && m.T == "key" {
				if outs := f.sl.Outs(); m.Slot >= 0 && m.Slot < len(outs) {
					f.c.keyReq.Add(1)
					outs[m.Slot].KeyRequest()
				}
			}
			continue
		}
		if len(b) == 0 {
			continue
		}
		switch b[0] {
		case frMsgFrame:
			if len(b) > frHdr {
				f.upFrame(b)
			}
		case frMsgAck:
			if len(b) == 9 || len(b) == 11 {
				f.st.AcksRx.Add(1)
				f.flow.OnAck(binary.BigEndian.Uint64(b[1:9]))
				if len(b) == 11 {
					q := int64(binary.BigEndian.Uint16(b[9:11]))
					f.c.qDown.Store(q)
					f.flow.Clamp(q > frClampMs)
				}
			}
		}
	}
}

func nowMs32() uint32 { return uint32(time.Now().UnixMilli()) }

// throttle: Eingangsdrossel (max_kbit); true = Rahmen darf passieren.
func (f *frLeg) throttle(n int, now time.Time) bool {
	f.tok += now.Sub(f.tokAt).Seconds() * f.rate
	f.tokAt = now
	if f.tok > f.rate { // hoechstens 1 s Guthaben
		f.tok = f.rate
	}
	if f.tok < float64(n) {
		return false
	}
	f.tok -= float64(n)
	return true
}

// upFrame packt einen Rahmen in RTP und speist ihn ein (S12.3/S12.4).
func (f *frLeg) upFrame(b []byte) {
	tr, fl, x := b[1], b[2], b[3]
	ts := binary.BigEndian.Uint32(b[4:])
	fno := binary.BigEndian.Uint32(b[8:])
	sentAt := binary.BigEndian.Uint32(b[12:])
	data := b[frHdr:]
	now := time.Now()
	f.umu.Lock()
	defer f.umu.Unlock()
	// Verzoegerungszunahme (Uhrversatz faellt mit dem Minimum der letzten 10 s heraus)
	d := int64(int32(nowMs32() - sentAt))
	if f.upMinT.IsZero() || d < f.upMin || now.Sub(f.upMinT) > 10*time.Second {
		f.upMin, f.upMinT = d, now
	}
	f.upQ = d - f.upMin
	f.upB += uint64(len(b) - 1)
	f.upEcho, f.upAt = sentAt, now
	f.c.upBytes.Add(uint64(len(b)))
	t := f.tracks[tr]
	if t == nil {
		t = f.newTrack(tr)
		if t == nil {
			return
		}
	}
	if t.src == nil { // nicht erlaubt (Rolle/Token)
		return
	}
	if t.kind == fwd.Audio {
		f.throttle(len(b), now) // Ton zaehlt mit, wird aber nie verworfen
		f.c.upFrames.Add(1)
		t.rtp(ts, true, nil, data, x)
		return
	}
	key := fl&frFlagKey != 0
	if t.start && fno != t.last+1 {
		t.wait = true // Luecke beim Absender: bis zum Schluesselbild nichts
		f.c.upGaps.Add(1)
	}
	t.start, t.last = true, fno
	tid := x & 3
	if key {
		tid = 0
	}
	if !f.throttle(len(b), now) {
		f.c.upDropped.Add(1)
		if key || tid == 0 || tid < t.top { // Referenzrahmen fehlt: bis zum naechsten Schluesselbild
			t.wait = true
			f.pli(t, tr)
		}
		return
	}
	if t.wait && !key {
		// ohne Schluesselbild nichts weiterleiten - und selbst eines anfordern (sonst wartet die Spur ewig, wenn sie noch
		// kein Empfaenger abonniert hat), hoechstens alle 500 ms
		f.c.upWaitKey.Add(1)
		f.pli(t, tr)
		return
	}
	t.wait = false
	f.c.upFrames.Add(1)
	t.packVideo(ts, tid, fl&frFlagSync != 0, data)
}

// packVideo: Bildrahmen -> RTP-Pakete mit VP8-Descriptor (RFC 7741): X, I (15-Bit-PictureID, lueckenlos ueber die
// weitergeleiteten Rahmen), L (TL0PICIDX, +1 je Basisrahmen), T (TID, Y = haengt nur von Basisrahmen ab - ohne Y
// verwirft libwebrtc beim Empfaenger alle Rahmen mit TID > 0). S im ersten Paket, Marker im letzten.
func (t *frUp) packVideo(ts uint32, tid uint8, sync bool, data []byte) {
	if tid == 0 {
		t.tl0++
	}
	t.pic = (t.pic + 1) & 0x7fff
	desc := [6]byte{0x80, 0xE0, 0x80 | byte(t.pic>>8), byte(t.pic), t.tl0, tid << 6}
	if sync && tid > 0 {
		desc[5] |= 0x20
	}
	for off := 0; off < len(data) || off == 0; off += frPayload {
		end := min(off+frPayload, len(data))
		if off == 0 {
			desc[0] |= 0x10
		} else {
			desc[0] &^= 0x10
		}
		t.rtp(ts, end == len(data), desc[:], data[off:end], 0)
		if end == len(data) {
			break
		}
	}
}

// pli: Schluesselbild fuer Spur tr beim Browser anfordern, hoechstens alle framesPLIEvery (ohne Sperre: auch aus dem
// Rueckruf des Raums).
func (f *frLeg) pli(t *frUp, tr uint8) {
	now := time.Now().UnixNano()
	last := t.pliAt.Load()
	if now-last < int64(framesPLIEvery) || !t.pliAt.CompareAndSwap(last, now) {
		return
	}
	f.c.pli.Add(1)
	go f.sendJSON(map[string]any{"t": "pli", "track": tr})
}

// newTrack legt die Quelle einer Spur an (unter umu). nil: unbekannte Spur.
func (f *frLeg) newTrack(tr uint8) *frUp {
	kind, layer, screen, top := fwd.Video, "", false, uint8(0)
	switch tr {
	case frTrackAudio:
		kind, layer = fwd.Audio, "a"
	case frTrackL:
		layer, top = "l", 1
	case frTrackM:
		layer, top = "m", 2
	case frTrackScreen:
		layer, screen = "h", true
	default:
		return nil
	}
	t := &frUp{kind: kind, top: top, ssrc: rand.Uint32(), seq: uint16(rand.Uint32()), pic: uint16(rand.Uint32()) & 0x7fff,
		wait: kind == fwd.Video, buf: make([]byte, 0, 1500)}
	var ext uint8
	if kind == fwd.Audio {
		ext = 1
	}
	src, err := f.sl.AddSource(kind, layer, screen, t.ssrc, ext, func(uint32) { f.pli(t, tr) })
	f.tracks[tr] = t
	if err != nil {
		if debug {
			log.Printf("debug %s ws: spur %d abgelehnt: %v", shortID(f.l.id), tr, err)
		}
		return t // nicht erlaubt: verwerfen
	}
	t.src = src
	f.l.mu.Lock()
	f.l.srcs = append(f.l.srcs, src)
	f.l.mu.Unlock()
	return t
}

func (t *frUp) rtp(ts uint32, marker bool, desc, data []byte, level byte) {
	h := rtp.Header{Version: 2, Marker: marker, SequenceNumber: t.seq, Timestamp: ts, SSRC: t.ssrc, PayloadType: 96}
	t.seq++
	if t.kind == fwd.Audio {
		h.PayloadType = 111
		_ = h.SetExtension(1, []byte{level})
	}
	n := h.MarshalSize()
	buf := t.buf[:n]
	if _, err := h.MarshalTo(buf); err != nil {
		return
	}
	buf = append(buf, desc...)
	buf = append(buf, data...)
	t.buf = buf[:0]
	if t.out != nil {
		t.out(buf)
		return
	}
	t.src.Inject(buf)
}

// feedback: Rueckmeldung aufwaerts (0x02) alle fb_ms, solange Rahmen kommen.
func (f *frLeg) feedback() {
	tk := time.NewTicker(framesFbEvery)
	defer tk.Stop()
	var m [17]byte
	m[0] = frMsgFb
	for {
		select {
		case <-f.done:
			return
		case <-tk.C:
		}
		f.umu.Lock()
		if f.upAt.IsZero() || time.Since(f.upAt) > 2*time.Second {
			f.umu.Unlock()
			continue
		}
		binary.BigEndian.PutUint64(m[1:], f.upB)
		binary.BigEndian.PutUint32(m[9:], f.upEcho)
		binary.BigEndian.PutUint16(m[13:], uint16(min(time.Since(f.upAt).Milliseconds(), 65535)))
		binary.BigEndian.PutUint16(m[15:], uint16(min(max(f.upQ, 0), 65535)))
		f.umu.Unlock()
		f.wmu.Lock()
		_ = f.ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
		err := f.ws.WriteMessage(websocket.BinaryMessage, m[:])
		f.wmu.Unlock()
		if err != nil {
			go f.close("schreiben")
			return
		}
	}
}

// ---------- abwaerts: Knoten -> Browser ----------

// sendFrame schickt einen fertigen Rahmen (msg[0] = 0x10) nach dem Fenster. false: verworfen (Fenster blieb voll) bzw. zu.
func (f *frLeg) sendFrame(msg []byte) (bool, error) {
	f.wdl.Store(time.Now().Add(framesWaitMax.get()).UnixNano())
	if err := f.flow.Reserve(f.done, &f.wdl); err != nil {
		if f.closed() {
			return false, errFramesClosed
		}
		return false, nil
	}
	// vor dem Senden zaehlen (wie wstun.Conn.Write seit 322f289): die Quittung kann vor einem Zaehlen danach ankommen,
	// OnAck kappte sie dann auf das alte sent und das Fenster schloesse sich dauerhaft. Scheitert das Senden, ist der
	// WebSocket ohnehin zu.
	f.flow.Sent(len(msg)-1, true)
	f.wmu.Lock()
	_ = f.ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	err := f.ws.WriteMessage(websocket.BinaryMessage, msg)
	f.wmu.Unlock()
	if err != nil {
		go f.close("schreiben: " + err.Error())
		return false, err
	}
	f.st.TxBytes.Add(uint64(len(msg)))
	f.st.TxMsgs.Add(1)
	f.c.downFrames.Add(1)
	return true, nil
}

var errFramesClosed = errors.New("ws zu")

// frOut ist der fwd.Writer eines Platzes: setzt Rahmen aus den RTP-Paketen zusammen (frAsm) und schickt sie.
type frOut struct {
	f    *frLeg
	slot uint8
	o    *fwd.Out
	kind fwd.Kind
	a    frAsm
}

func (w *frOut) WriteRTP(h *rtp.Header, payload []byte) (int, error) {
	if w.f.closed() {
		return 0, errFramesClosed
	}
	msg, ev := w.a.push(w.slot, w.kind == fwd.Audio, h, payload)
	switch ev {
	case asmBroken:
		w.f.c.downBroken.Add(1)
	case asmWantKey:
		w.o.KeyRequest()
	}
	if msg == nil {
		return len(payload), nil
	}
	sent, err := w.f.sendFrame(msg)
	if err != nil {
		return len(payload), err
	}
	if !sent && w.kind == fwd.Video {
		// Fenster blieb voll: Rahmen verwerfen, ab jetzt bis zum Schluesselbild
		w.f.c.downDrop.Add(1)
		w.a.broken = true
		w.o.KeyRequest()
	} else if !sent {
		w.f.c.downDrop.Add(1)
	}
	return len(payload), nil
}

// Ereignisse des Zusammensetzens.
const (
	asmNone    = iota
	asmBroken  // Rahmen nach Luecke verworfen (wartet auf Schluesselbild)
	asmWantKey // Paket ohne Rahmenbeginn nach Luecke: Schluesselbild anfordern
)

// frAsm: RTP-Pakete eines Platzes -> Rahmen-Nachrichten (0x10). Opus: ein Paket = ein Rahmen. VP8: Descriptor lesen
// (TID, Y), Pakete bis zum Marker sammeln; nach einer Luecke in der Folgenummer bis zum naechsten Schluesselbild nichts.
type frAsm struct {
	buf     []byte
	ts      uint32
	csrc    uint32
	key     bool
	tid     uint8
	sync    bool
	inFrame bool
	broken  bool
	last    uint16
	started bool
}

func frHeader(m []byte, slot uint8, ts, csrc uint32, fl, x byte) {
	m[0], m[1], m[2], m[3] = frMsgFrame, slot, fl, x
	binary.BigEndian.PutUint32(m[4:], ts)
	binary.BigEndian.PutUint32(m[8:], csrc)
	binary.BigEndian.PutUint32(m[12:], nowMs32())
}

// push nimmt ein Paket -> fertige Nachricht (oder nil) und Ereignis.
func (a *frAsm) push(slot uint8, audio bool, h *rtp.Header, payload []byte) ([]byte, int) {
	var csrc uint32
	if len(h.CSRC) > 0 {
		csrc = h.CSRC[0]
	}
	gap := a.started && h.SequenceNumber != a.last+1
	a.started, a.last = true, h.SequenceNumber
	if h.Padding && (len(payload) == 0 || len(payload) == fwd.PadSize && int(payload[len(payload)-1]) == len(payload)) {
		return nil, asmNone // Fuellpaket (Probe): kein Rahmen
	}
	if audio {
		msg := make([]byte, frHdr+len(payload))
		frHeader(msg, slot, h.Timestamp, csrc, 0, 0)
		copy(msg[frHdr:], payload)
		return msg, asmNone
	}
	if len(payload) < 1 {
		return nil, asmNone
	}
	x, s, pid := payload[0]&0x80 != 0, payload[0]&0x10 != 0, payload[0]&0x07
	i := 1
	var tid uint8
	var ysync bool
	if x {
		if len(payload) < 2 {
			return nil, asmNone
		}
		fb := payload[1]
		i = 2
		if fb&0x80 != 0 { // I
			if len(payload) > i && payload[i]&0x80 != 0 {
				i += 2
			} else {
				i++
			}
		}
		if fb&0x40 != 0 { // L
			i++
		}
		if fb&0x30 != 0 { // T oder K
			if len(payload) > i {
				tid, ysync = payload[i]>>6, payload[i]&0x20 != 0
			}
			i++
		}
	}
	if i > len(payload) {
		return nil, asmNone
	}
	data := payload[i:]
	if gap {
		a.inFrame = false
		a.broken = true
	}
	if s && pid == 0 {
		key := len(data) > 0 && data[0]&0x01 == 0
		if a.broken && !key {
			a.inFrame = false
			return nil, asmBroken
		}
		a.broken = false
		a.inFrame, a.key, a.ts, a.csrc, a.tid, a.sync = true, key, h.Timestamp, csrc, tid, ysync
		a.buf = append(a.buf[:0], make([]byte, frHdr)...)
	}
	if !a.inFrame || h.Timestamp != a.ts {
		if a.broken {
			return nil, asmWantKey
		}
		return nil, asmNone
	}
	if len(a.buf)+len(data) > frMaxMsg { // Pruefer B2: Rahmen ohne Ende (Marker fehlt) waechst nicht ohne Grenze
		a.inFrame, a.broken = false, true
		a.buf = nil
		return nil, asmBroken
	}
	a.buf = append(a.buf, data...)
	if !h.Marker {
		return nil, asmNone
	}
	a.inFrame = false
	var fl byte
	if a.key {
		fl |= frFlagKey
	}
	if a.sync {
		fl |= frFlagSync
	}
	msg := make([]byte, len(a.buf))
	copy(msg, a.buf)
	frHeader(msg, slot, a.ts, a.csrc, fl, a.tid)
	return msg, asmNone
}

// stats: Zaehler fuer GET /api/sfu/leg/{id}.
func (f *frLeg) stats() (wstun.Snapshot, frStats) {
	sn := wstun.Snapshot{Window: int64(f.flow.Window()), Inflight: f.st.Inflight.Load(), MaxInflight: f.st.MaxInflight.Load(),
		RTTus: f.st.MinRTTus.Load(), BWBps: f.st.BWBps.Load(), Tx: f.st.TxBytes.Load(), Rx: f.c.upBytes.Load(),
		AcksRx: f.st.AcksRx.Load(), BlockedMs: f.flow.BlockedNs() / 1e6}
	f.umu.Lock()
	q := f.upQ
	f.umu.Unlock()
	return sn, frStats{UpFrames: f.c.upFrames.Load(), UpBytes: f.c.upBytes.Load(), UpDropped: f.c.upDropped.Load(), UpGaps: f.c.upGaps.Load(),
		UpWaitKey: f.c.upWaitKey.Load(), DownFrames: f.c.downFrames.Load(), DownDrop: f.c.downDrop.Load(), DownBroken: f.c.downBroken.Load(),
		KeyReq: f.c.keyReq.Load(), PLI: f.c.pli.Load(), QUpMs: q, QDownMs: f.c.qDown.Load()}
}

// legPaths: Beine je Weg (Lastmeldung load.paths, Admin -> Anrufe): udp, tcp, turn, ws (A3-Tunnel der Apps), frames (B2).
func (s *server) legPaths() map[string]int {
	out := map[string]int{"udp": 0, "tcp": 0, "turn": 0, "ws": 0, "frames": 0}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, l := range s.legs {
		if l.sl == nil {
			continue // Spiegel/Bench bzw. noch im Aufbau
		}
		k := l.path
		if l.framesLeg {
			k = "frames"
		}
		if _, ok := out[k]; ok {
			out[k]++
		}
	}
	return out
}

// String fuer Logs (keine Adressen).
func (f *frLeg) String() string {
	st := &f.c
	return fmt.Sprintf("auf=%d ab=%d ab_verworfen=%d drossel=%d luecken=%d", st.upFrames.Load(), st.downFrames.Load(), st.downDrop.Load(),
		st.upDropped.Load(), st.upGaps.Load())
}
