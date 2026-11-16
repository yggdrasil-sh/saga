package main

import (
	"bytes"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/pion/rtp"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/wstun"
)

// --- Browser-Attrappe fuer saga-frames.1 (Vertrag S12) ------------------------------------------------------------

type frRx struct {
	slot, fl, x byte
	ts, csrc    uint32
	data        []byte
}

type frClient struct {
	t     *testing.T
	ws    *websocket.Conn
	ok    map[string]any
	ack   bool
	qDown atomic.Int32 // >0: 11-Byte-Quittung mit dieser Verzoegerungszunahme
	wmu   sync.Mutex
	mu    sync.Mutex
	rx    []frRx
	plis  []int
	rcvd  uint64
	fb    atomic.Int64
	fbQ   atomic.Int64
	done  chan struct{}
	code  atomic.Int64 // Close-Code der Gegenseite
	fno   map[byte]uint32
}

func frToken(t *testing.T, base, room, peer, role string, kbit int) string {
	t.Helper()
	var tok struct{ Token string }
	if err := postJSON(httpClient(), base+"/api/dev/token", map[string]any{"room": room, "peer": peer, "role": role, "max_kbit": kbit}, &tok); err != nil {
		t.Fatal(err)
	}
	return tok.Token
}

func dialFramesWS(t *testing.T, url string) (*websocket.Conn, *http.Response, error) {
	d := websocket.Dialer{Subprotocols: []string{framesSubprotocol}, HandshakeTimeout: 5 * time.Second}
	return d.Dial(url, nil)
}

// dialFrames: WebSocket + join; Fehler -> Antwort err (Code) statt Abbruch.
func dialFrames(t *testing.T, url string, j framesJoin, ack bool) (*frClient, string) {
	t.Helper()
	ws, resp, err := dialFramesWS(t, url)
	if err != nil {
		t.Fatalf("Dial: %v %v", err, resp)
	}
	if p := resp.Header.Get("Sec-WebSocket-Protocol"); p != framesSubprotocol {
		t.Fatalf("Unterprotokoll %q", p)
	}
	j.T, j.V = "join", 1
	_ = ws.WriteJSON(j)
	_ = ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	mt, b, err := ws.ReadMessage()
	_ = ws.SetReadDeadline(time.Time{})
	if err != nil || mt != websocket.TextMessage {
		t.Fatalf("keine Antwort: %v", err)
	}
	var m map[string]any
	_ = json.Unmarshal(b, &m)
	if m["t"] != "ok" {
		ws.Close()
		code, _ := m["code"].(string)
		return nil, code
	}
	c := &frClient{t: t, ws: ws, ok: m, ack: ack, done: make(chan struct{}), fno: map[byte]uint32{}}
	t.Cleanup(func() { c.ws.Close() })
	go c.read()
	return c, ""
}

func (c *frClient) read() {
	defer close(c.done)
	for {
		mt, b, err := c.ws.ReadMessage()
		if err != nil {
			if ce, ok := err.(*websocket.CloseError); ok {
				c.code.Store(int64(ce.Code))
			}
			return
		}
		if mt == websocket.TextMessage {
			var m struct {
				T     string
				Track int
			}
			if json.Unmarshal(b, &m) == nil && m.T == "pli" {
				c.mu.Lock()
				c.plis = append(c.plis, m.Track)
				c.mu.Unlock()
			}
			continue
		}
		switch b[0] {
		case frMsgFb:
			if len(b) == 17 {
				c.fb.Add(1)
				c.fbQ.Store(int64(binary.BigEndian.Uint16(b[15:])))
			}
		case frMsgFrame:
			r := frRx{slot: b[1], fl: b[2], x: b[3], ts: binary.BigEndian.Uint32(b[4:]), csrc: binary.BigEndian.Uint32(b[8:]),
				data: append([]byte(nil), b[frHdr:]...)}
			c.mu.Lock()
			c.rx = append(c.rx, r)
			c.rcvd += uint64(len(b) - 1)
			v := c.rcvd
			c.mu.Unlock()
			if c.ack {
				n := 9
				if c.qDown.Load() > 0 {
					n = 11
				}
				m := make([]byte, n)
				m[0] = frMsgAck
				binary.BigEndian.PutUint64(m[1:], v)
				if n == 11 {
					binary.BigEndian.PutUint16(m[9:], uint16(c.qDown.Load()))
				}
				c.wmu.Lock()
				_ = c.ws.WriteMessage(websocket.BinaryMessage, m)
				c.wmu.Unlock()
			}
		}
	}
}

// send: Rahmen auf Spur tr (Rahmen-Nr. lueckenlos, skip: eine Nummer auslassen).
func (c *frClient) send(tr, fl, x byte, ts uint32, data []byte, skip bool) {
	if skip {
		c.fno[tr]++
	}
	c.fno[tr]++
	m := make([]byte, frHdr+len(data))
	m[0], m[1], m[2], m[3] = frMsgFrame, tr, fl, x
	binary.BigEndian.PutUint32(m[4:], ts)
	binary.BigEndian.PutUint32(m[8:], c.fno[tr])
	binary.BigEndian.PutUint32(m[12:], nowMs32())
	copy(m[frHdr:], data)
	c.wmu.Lock()
	_ = c.ws.WriteMessage(websocket.BinaryMessage, m)
	c.wmu.Unlock()
}

func (c *frClient) frames() []frRx {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]frRx(nil), c.rx...)
}

func (c *frClient) pliCount() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.plis)
}

func (c *frClient) csrc() uint32 { v, _ := c.ok["csrc"].(float64); return uint32(v) }
func (c *frClient) id() string   { v, _ := c.ok["id"].(string); return v }

// vp8: Nutzlast eines kuenstlichen VP8-Rahmens (Bit 0 des ersten Bytes = P: 0 Schluesselbild), Rest = Kennung.
func vp8(key bool, n int, tag byte) []byte {
	b := bytes.Repeat([]byte{tag}, n)
	if key {
		b[0] = tag &^ 1
	} else {
		b[0] = tag | 1
	}
	return b
}

// sender: Ton alle 20 ms, Bild l mit 20 Bildern/s (L1T2: TID 0/1, Y an TID 1), Schluesselbild am Anfang und auf pli.
func (c *frClient) sender(stop <-chan struct{}, video bool, size int) {
	var ats, vts uint32
	n := 0
	seen := 0
	tk := time.NewTicker(20 * time.Millisecond)
	defer tk.Stop()
	for {
		select {
		case <-stop:
			return
		case <-tk.C:
		}
		ats += 960
		c.send(frTrackAudio, 0, 0x80|30, ats, []byte{0x78, byte(n), 2, 3, 4}, false)
		if !video || n%2 == 1 {
			n++
			continue
		}
		key := n == 0 || c.pliCount() > seen
		seen = c.pliCount()
		vts += 4500
		tid := byte((n / 2) % 2)
		var fl byte
		if key {
			fl, tid = frFlagKey, 0
		} else if tid == 1 {
			fl = frFlagSync
		}
		c.send(frTrackL, fl, tid, vts, vp8(key, size, 0x40+byte(n%32)*2), false)
		n++
	}
}

// --- Handschlag, Zugang ---------------------------------------------------------------------------------------------

// S12.1: Unterprotokoll waehlt den Weg; unbekannt -> 400 mit beiden.
func TestFramesUnterprotokoll(t *testing.T) {
	_, _, url := tunnelServer(t)
	ws, resp, err := dialTunnel(t, url, "chat")
	if err == nil {
		ws.Close()
		t.Fatal("chat angenommen")
	}
	var j map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&j)
	if resp.StatusCode != 400 || j["error"] != "subprotocol" || len(j["supported"].([]any)) != 2 {
		t.Fatalf("%d %v", resp.StatusCode, j)
	}
	ws, resp, err = dialFramesWS(t, url)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	if resp.Header.Get("Sec-WebSocket-Protocol") != framesSubprotocol {
		t.Fatalf("%q", resp.Header.Get("Sec-WebSocket-Protocol"))
	}
}

// S12.2: erste Nachricht, Fehlercodes und Close-Codes; Token einmalig.
func TestFramesZugang(t *testing.T) {
	a := framesAuthWait.set(300 * time.Millisecond)
	t.Cleanup(func() { framesAuthWait.set(a) })
	s, hs, url := tunnelServer(t)
	room := "frames-zugang"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	good := frToken(t, hs.URL, room, "p1", "audio", 0)
	other := frToken(t, hs.URL, "frames-unbekannt", "p1", "audio", 0)
	for _, c := range []struct {
		name string
		msg  any
		bin  bool
		code string
		cc   int
	}{
		{"kein json", "hallo", false, "bad", wstun.CloseBad},
		{"falsches t", map[string]any{"t": "auth", "v": 1}, false, "bad", wstun.CloseBad},
		{"version", map[string]any{"t": "join", "v": 2, "token": good}, false, "version", wstun.CloseBad},
		{"binaer vor join", []byte{frMsgFrame, 0, 0}, true, "bad", wstun.CloseBad},
		{"falsches token", map[string]any{"t": "join", "v": 1, "token": "x.y"}, false, "auth", wstun.CloseAuth},
		{"raum unbekannt", map[string]any{"t": "join", "v": 1, "token": other}, false, "room", frCloseRoom},
		{"zu viele kacheln", map[string]any{"t": "join", "v": 1, "token": good, "tiles": 99}, false, "bad", wstun.CloseBad},
		{"ohne nachricht", nil, false, "timeout", wstun.CloseTimeout},
	} {
		ws, _, err := dialFramesWS(t, url)
		if err != nil {
			t.Fatal(err)
		}
		switch m := c.msg.(type) {
		case nil:
		case []byte:
			_ = ws.WriteMessage(websocket.BinaryMessage, m)
		case string:
			_ = ws.WriteMessage(websocket.TextMessage, []byte(m))
		default:
			_ = ws.WriteJSON(m)
		}
		r, cc := readErr(t, ws)
		ws.Close()
		if r.T != "err" || r.Code != c.code || cc != c.cc || r.V != 1 || (c.code == "version" && r.Max != 1) {
			t.Errorf("%s: %+v close %d", c.name, r, cc)
		}
	}
	// richtig: ok mit Bein, Plaetzen, Quittungswerten; zweites Mal dasselbe Token: auth
	c, code := dialFrames(t, url, framesJoin{Token: good, Audio: 3}, true)
	if c == nil {
		t.Fatalf("ok erwartet: %s", code)
	}
	if c.id() == "" || c.csrc() == 0 || c.ok["ack_bytes"].(float64) != 8192 || c.ok["ack_ms"].(float64) != 10 || c.ok["fb_ms"].(float64) != 50 ||
		len(c.ok["slots"].([]any)) != 3 || c.ok["state"] != "ok" {
		t.Fatalf("ok: %v", c.ok)
	}
	if st := s.tun.stats(); st.AuthFail < 1 || st.Frames != 1 || st.FramesTotal != 1 || st.Open != 0 {
		t.Fatalf("Zaehler: %+v", st)
	}
	l := s.getLeg(c.id())
	if l == nil || l.path != "ws" || !l.framesLeg || l.state().String() != "connected" {
		t.Fatalf("Bein: %+v", l)
	}
}

// Medien-Token von Saga (Ed25519) sind einmalig: zweiter join mit demselben Token -> auth; fremder Knoten -> auth.
func TestFramesTokenEinmalig(t *testing.T) {
	s := testNodeServer(t, false)
	pub, sk, _ := ed25519.GenerateKey(rand.Reader)
	s.verifier.SetKey(pub)
	hs := httptest.NewServer(s.routes())
	t.Cleanup(hs.Close)
	url := "ws" + strings.TrimPrefix(hs.URL, "http") + "/api/sfu/ws"
	tok := signTok(sk, tokBody(map[string]any{"node": s.cfg.Name, "role": "audio", "pub": []string{"a"}, "dev_class": "desktop"}))
	c, code := dialFrames(t, url, framesJoin{Token: tok, Audio: 3}, true)
	if c == nil || c.ok["csrc"] == nil {
		t.Fatalf("erstes Mal: %q", code)
	}
	if _, code := dialFrames(t, url, framesJoin{Token: tok, Audio: 3}, true); code != "auth" {
		t.Fatalf("zweites Mal: %q", code)
	}
	tok2 := signTok(sk, tokBody(map[string]any{"node": "anderer.example.org", "role": "audio", "jti": "jti-anderer-knoten1"}))
	if _, code := dialFrames(t, url, framesJoin{Token: tok2, Audio: 3}, true); code != "auth" {
		t.Fatalf("fremder Knoten: %q", code)
	}
}

// --- Rahmen <-> RTP --------------------------------------------------------------------------------------------------

type rtpPkt struct {
	h rtp.Header
	p []byte
}

func capturePack(t *testing.T, tid uint8, sync bool, sizes ...int) ([]rtpPkt, *frUp) {
	t.Helper()
	var out []rtpPkt
	u := &frUp{kind: fwd.Video, ssrc: 7, seq: 65530, pic: 0x7ffe, buf: make([]byte, 0, 1500)}
	u.out = func(b []byte) {
		var h rtp.Header
		n, err := h.Unmarshal(b)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, rtpPkt{h, append([]byte(nil), b[n:]...)})
	}
	for _, n := range sizes {
		u.packVideo(uint32(n), tid, sync, vp8(tid == 0, n, 0x20))
	}
	return out, u
}

// Rahmen -> RTP: VP8-Descriptor (S, PictureID 15 Bit lueckenlos mit Ueberlauf, TL0PICIDX nur fuer Basisrahmen, TID, Y nur
// mit TID > 0), Aufteilung auf frPayload, Marker im letzten Paket; RTP -> Rahmen wieder gleich (Rundreise).
func TestFramesRTPRundreise(t *testing.T) {
	sizes := []int{1, frPayload, frPayload + 1, 5000}
	pk, _ := capturePack(t, 0, false, sizes...)
	want := 1 + 1 + 2 + 5
	if len(pk) != want {
		t.Fatalf("%d Pakete, erwartet %d", len(pk), want)
	}
	var a frAsm
	var msgs [][]byte
	prevPic, prevTL0 := -1, -1
	for i, p := range pk {
		d := p.p
		if d[0]&0x80 == 0 || d[1] != 0xE0 || d[2]&0x80 == 0 {
			t.Fatalf("Descriptor %d: % x", i, d[:6])
		}
		pic, tl0 := int(d[2]&0x7f)<<8|int(d[3]), int(d[4])
		if d[0]&0x10 != 0 { // Rahmenbeginn
			if prevPic >= 0 && pic != (prevPic+1)&0x7fff {
				t.Fatalf("PictureID %d nach %d", pic, prevPic)
			}
			if prevTL0 >= 0 && tl0 != (prevTL0+1)&0xff {
				t.Fatalf("TL0PICIDX %d nach %d", tl0, prevTL0)
			}
			prevPic, prevTL0 = pic, tl0
		}
		if d[5]&0xe0 != 0 {
			t.Fatalf("TID/Y bei Basisrahmen: %x", d[5])
		}
		if m, _ := a.push(3, false, &p.h, p.p); m != nil {
			msgs = append(msgs, m)
		}
	}
	if len(msgs) != len(sizes) {
		t.Fatalf("%d Rahmen zurueck", len(msgs))
	}
	for i, m := range msgs {
		if m[0] != frMsgFrame || m[1] != 3 || m[2] != frFlagKey || m[3] != 0 || binary.BigEndian.Uint32(m[4:]) != uint32(sizes[i]) ||
			!bytes.Equal(m[frHdr:], vp8(true, sizes[i], 0x20)) {
			t.Fatalf("Rahmen %d: % x ...", i, m[:frHdr])
		}
	}
	// Zeitschicht 1 mit Y; ohne Y-Angabe kein Y
	pk, _ = capturePack(t, 1, true, 300)
	if pk[0].p[5] != 1<<6|0x20 {
		t.Fatalf("TID1+Y: %x", pk[0].p[5])
	}
	var a2 frAsm
	m, _ := a2.push(0, false, &pk[0].h, pk[0].p)
	if m == nil || m[2] != frFlagSync || m[3] != 1 {
		t.Fatalf("Rundreise TID1/Y: %v", m)
	}
	pk, _ = capturePack(t, 2, false, 300)
	if pk[0].p[5] != 2<<6 {
		t.Fatalf("TID2 ohne Y: %x", pk[0].p[5])
	}
	// Gegenprobe Y: an einem Basisrahmen nie gesetzt (auch wenn der Browser es meldet)
	pk, _ = capturePack(t, 0, true, 300)
	if pk[0].p[5]&0x20 != 0 {
		t.Fatalf("Y an TID0: %x", pk[0].p[5])
	}
}

// Luecke in der Paketfolge: angefangener Rahmen faellt weg, danach bis zum Schluesselbild nichts (Gegenprobe: ohne
// Luecke kommt alles); Fuellpakete sind kein Rahmen; Ton: ein Paket = ein Rahmen mit CSRC.
func TestFramesZusammensetzenLuecke(t *testing.T) {
	u := &frUp{kind: fwd.Video, ssrc: 7, buf: make([]byte, 0, 1500)}
	var pk []rtpPkt
	u.out = func(b []byte) {
		var h rtp.Header
		n, _ := h.Unmarshal(b)
		h.CSRC = []uint32{42}
		pk = append(pk, rtpPkt{h, append([]byte(nil), b[n:]...)})
	}
	u.packVideo(1, 0, false, vp8(true, 3000, 2))  // 0..2
	u.packVideo(2, 0, false, vp8(false, 3000, 4)) // 3..5
	u.packVideo(3, 0, false, vp8(false, 500, 6))  // 6
	u.packVideo(4, 0, false, vp8(true, 500, 8))   // 7
	var a frAsm
	var got []uint32
	var evs []int
	for i, p := range pk {
		if i == 4 { // mitten im zweiten Rahmen verloren
			continue
		}
		m, ev := a.push(1, false, &p.h, p.p)
		evs = append(evs, ev)
		if m != nil {
			got = append(got, binary.BigEndian.Uint32(m[4:]))
			if binary.BigEndian.Uint32(m[8:]) != 42 {
				t.Fatalf("CSRC %d", binary.BigEndian.Uint32(m[8:]))
			}
		}
	}
	if len(got) != 2 || got[0] != 1 || got[1] != 4 {
		t.Fatalf("Rahmen %v (erwartet 1, 4) ereignisse %v", got, evs)
	}
	broken, want := 0, 0
	for _, e := range evs {
		if e == asmBroken {
			broken++
		}
		if e == asmWantKey {
			want++
		}
	}
	if broken != 1 || want < 1 {
		t.Fatalf("Ereignisse %v", evs)
	}
	// Fuellpaket und Ton
	var b frAsm
	pad := make([]byte, fwd.PadSize)
	pad[len(pad)-1] = fwd.PadSize
	if m, _ := b.push(0, false, &rtp.Header{SequenceNumber: 1, Padding: true}, pad); m != nil {
		t.Fatal("Fuellpaket als Rahmen")
	}
	m, _ := b.push(2, true, &rtp.Header{SequenceNumber: 2, Timestamp: 960, CSRC: []uint32{9}}, []byte{0x78, 1, 2})
	if m == nil || m[1] != 2 || binary.BigEndian.Uint32(m[8:]) != 9 || !bytes.Equal(m[frHdr:], []byte{0x78, 1, 2}) {
		t.Fatalf("Ton: %v", m)
	}
}

// --- Ende zu Ende am Knoten ------------------------------------------------------------------------------------------

type frDev struct{ a, v *frClient }

func joinFramesDev(t *testing.T, base, url, room, peer string, kbit int) frDev {
	t.Helper()
	a, code := dialFrames(t, url, framesJoin{Token: frToken(t, base, room, peer, "audio", 0), Audio: 3}, true)
	if a == nil {
		t.Fatalf("%s Ton: %s", peer, code)
	}
	v, code := dialFrames(t, url, framesJoin{Token: frToken(t, base, room, peer, "video", kbit), Speaker: true, Tiles: 1}, true)
	if v == nil {
		t.Fatalf("%s Bild: %s", peer, code)
	}
	return frDev{a, v}
}

func waitFor(d time.Duration, f func() bool) bool {
	end := time.Now().Add(d)
	for time.Now().Before(end) {
		if f() {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return f()
}

// Zwei Browser-Attrappen ueber den Rahmen-Weg: Ton und Bild in beide Richtungen mit richtiger CSRC, Inhalt unveraendert
// (der Knoten liest nur Kopf und Metadaten), Y/TID kommen an, Rueckmeldung aufwaerts, Zaehler und Beine je Weg.
func TestFramesEndeZuEnde(t *testing.T) {
	s, hs, url := tunnelServer(t)
	room := "frames-e2e"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	A := joinFramesDev(t, hs.URL, url, room, "anna", 0)
	B := joinFramesDev(t, hs.URL, url, room, "bert", 0)
	stop := make(chan struct{})
	defer close(stop)
	go func() { // Ton und Bild beider Geraete
		go A.a.sender(stop, false, 0)
		go B.a.sender(stop, false, 0)
	}()
	go A.v.sender(stop, true, 2500)
	go B.v.sender(stop, true, 2500)
	check := func(rx *frClient, from frDev, video bool) (n, sync int) {
		for _, f := range rx.frames() {
			if f.csrc != from.a.csrc() {
				continue
			}
			n++
			if f.fl&frFlagSync != 0 && f.x == 1 {
				sync++
			}
		}
		return
	}
	ok := waitFor(8*time.Second, func() bool {
		na, _ := check(B.a, A, false)
		nv, sy := check(B.v, A, true)
		ma, _ := check(A.a, B, false)
		mv, _ := check(A.v, B, true)
		return na > 30 && nv > 15 && sy > 3 && ma > 30 && mv > 15
	})
	na, _ := check(B.a, A, false)
	nv, sy := check(B.v, A, true)
	ma, _ := check(A.a, B, false)
	mv, _ := check(A.v, B, true)
	if !ok {
		t.Fatalf("Empfang: B<-A Ton %d Bild %d (Y %d), A<-B Ton %d Bild %d", na, nv, sy, ma, mv)
	}
	// Inhalt: Ton-Rahmen von A unveraendert (Kennung im zweiten Byte laeuft), Bild beginnt mit einem Schluesselbild
	first := true
	for _, f := range B.v.frames() {
		if f.csrc != A.a.csrc() {
			continue
		}
		if first && (f.fl&frFlagKey == 0 || f.data[0]&1 != 0) {
			t.Fatalf("erstes Bild kein Schluesselbild: %+v", f.fl)
		}
		first = false
		if len(f.data) != 2500 {
			t.Fatalf("Bildgroesse %d", len(f.data))
		}
	}
	for _, f := range B.a.frames() {
		if f.csrc == A.a.csrc() && (len(f.data) != 5 || f.data[0] != 0x78 || f.data[2] != 2) {
			t.Fatalf("Ton veraendert: % x", f.data)
		}
	}
	if A.v.fb.Load() == 0 || A.a.fb.Load() == 0 {
		t.Fatal("keine Rueckmeldung aufwaerts")
	}
	if p := s.legPaths(); p["frames"] != 4 || p["ws"] != 0 {
		t.Fatalf("Beine je Weg: %v", p)
	}
	if st := s.tun.stats(); st.Frames != 4 || st.FramesTotal != 4 {
		t.Fatalf("Zaehler: %+v", st)
	}
	var lay struct {
		Stats legStats `json:"stats"`
	}
	resp, err := httpClient().Get(hs.URL + "/api/sfu/leg/" + B.v.id())
	if err != nil {
		t.Fatal(err)
	}
	_ = json.NewDecoder(resp.Body).Decode(&lay)
	resp.Body.Close()
	st := lay.Stats
	if st.Path != "ws" || st.State != "connected" || st.Frames == nil || st.Frames.UpFrames == 0 || st.Frames.DownFrames == 0 ||
		st.Tunnel == nil || st.Tunnel.Window < frMinWindow || st.Tunnel.AcksRx == 0 || st.Tunnel.RTTus == 0 {
		t.Fatalf("Bein-Diagnose: %+v %+v %+v", st, st.Frames, st.Tunnel)
	}
	// Abmelden (DELETE) schliesst den WebSocket
	req, _ := http.NewRequest("DELETE", hs.URL+"/api/sfu/leg/"+A.a.id(), nil)
	if resp, err := httpClient().Do(req); err != nil || resp.StatusCode != 200 {
		t.Fatalf("DELETE: %v", err)
	}
	select {
	case <-A.a.done:
	case <-time.After(3 * time.Second):
		t.Fatal("WebSocket nach DELETE offen")
	}
	if !waitFor(2*time.Second, func() bool { return s.tun.stats().Frames == 3 && s.legPaths()["frames"] == 3 }) {
		t.Fatalf("nach DELETE: %+v %v", s.tun.stats(), s.legPaths())
	}
}

// Interop: Rahmen-Weg (Browser B2) <-> App ueber den A3-Tunnel (pion, ICE-TCP im WebSocket) im selben Raum.
func TestFramesInteropTunnel(t *testing.T) {
	s, hs, url := tunnelServer(t)
	s.iceTCP = false
	user, cred := tunnelCreds(t, s)
	room := "frames-interop"
	hc := httpClient()
	if err := postJSON(hc, hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	api, err := sfuBotAPI("ws")
	if err != nil {
		t.Fatal(err)
	}
	b := &sfuBot{peer: "app", path: "ws", video: true, layers: []string{"l"}}
	b.level.Store(100)
	b.bend = func(role, sdp string) string { return bendTo(sdp, appTunnel(t, url, user, cred, role)) }
	if err := b.join(hc, hs.URL, api, room, 1, "desktop"); err != nil {
		t.Fatal(err)
	}
	defer b.close()
	if !waitFor(10*time.Second, func() bool { return b.connected() == 2 }) {
		t.Fatal("App nicht verbunden")
	}
	b.start()
	A := joinFramesDev(t, hs.URL, url, room, "web", 0)
	stop := make(chan struct{})
	defer close(stop)
	go A.a.sender(stop, false, 0)
	go A.v.sender(stop, true, 1500)
	ok := waitFor(10*time.Second, func() bool {
		au, vi := b.seen()
		var fa, fv int
		for _, f := range A.a.frames() {
			if f.csrc != 0 && f.csrc != A.a.csrc() {
				fa++
			}
		}
		for _, f := range A.v.frames() {
			if f.csrc != 0 && f.csrc != A.a.csrc() {
				fv++
			}
		}
		return au[A.a.csrc()] > 20 && vi[A.a.csrc()] > 20 && fa > 20 && fv > 10
	})
	if !ok {
		au, vi := b.seen()
		t.Fatalf("App<-Web Ton %d Bild %d, Web<-App Ton %d Bild %d", au[A.a.csrc()], vi[A.a.csrc()], len(A.a.frames()), len(A.v.frames()))
	}
}

// --- Fenster, Drossel, Luecke ----------------------------------------------------------------------------------------

// Abwaerts-Fenster (S12.4): ohne Quittungen haelt der Knoten hoechstens ein Fenster unterwegs und verwirft danach
// Bildrahmen (bis Schluesselbild); mit Quittungen kommt alles. Gemeldete Warteschlange (11-Byte-Quittung) -> Mindestfenster.
func TestFramesFenster(t *testing.T) {
	w := framesWaitMax.set(100 * time.Millisecond)
	t.Cleanup(func() { framesWaitMax.set(w) })
	s, hs, url := tunnelServer(t)
	room := "frames-fenster"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	A := joinFramesDev(t, hs.URL, url, room, "send", 0)
	// B ohne Quittungen
	Bv, code := dialFrames(t, url, framesJoin{Token: frToken(t, hs.URL, room, "stumm", "video", 0), Speaker: true}, false)
	if Bv == nil {
		t.Fatal(code)
	}
	// C mit Quittungen und gemeldeter Warteschlange
	Cv, code := dialFrames(t, url, framesJoin{Token: frToken(t, hs.URL, room, "voll", "video", 0), Speaker: true}, true)
	if Cv == nil {
		t.Fatal(code)
	}
	Cv.qDown.Store(200)
	stop := make(chan struct{})
	go A.v.sender(stop, true, 3000)
	time.Sleep(3 * time.Second)
	close(stop)
	var bytesB, framesC int
	for _, f := range Bv.frames() {
		bytesB += len(f.data) + frHdr - 1
	}
	framesC = len(Cv.frames())
	lb := s.getLeg(Bv.id())
	lc := s.getLeg(Cv.id())
	sb, sc := lb.stats(), lc.stats()
	if bytesB > frMinWindow+3100 || sb.Frames.DownDrop == 0 {
		t.Fatalf("ohne Quittung: %d Byte empfangen, verworfen %d", bytesB, sb.Frames.DownDrop)
	}
	if framesC < 20 || sc.Frames.DownDrop != 0 || sc.Tunnel.Window != frMinWindow || sc.Frames.QDownMs != 200 {
		t.Fatalf("mit Quittung: %d Rahmen, %+v %+v", framesC, sc.Frames, sc.Tunnel)
	}
	// Gegenprobe: ohne gemeldete Warteschlange waechst das Fenster ueber das Minimum (Rate x RTT + 16 KiB)
	if sc.Tunnel.Window < frMinWindow {
		t.Fatal("Fenster unter dem Minimum")
	}
}

// Eingangsdrossel (max_kbit): Bild darueber verworfen (Referenzrahmen -> bis Schluesselbild, pli), Ton nie.
func TestFramesDrossel(t *testing.T) {
	s, hs, url := tunnelServer(t)
	room := "frames-drossel"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	A := joinFramesDev(t, hs.URL, url, room, "laut", 100) // Bild 100 kbit/s
	B := joinFramesDev(t, hs.URL, url, room, "hoer", 0)
	stop := make(chan struct{})
	go A.a.sender(stop, false, 0)
	go A.v.sender(stop, true, 4000) // ~800 kbit/s
	time.Sleep(3 * time.Second)
	close(stop)
	lv, la := s.getLeg(A.v.id()).stats(), s.getLeg(A.a.id()).stats()
	if lv.Frames.UpDropped == 0 || lv.Frames.UpFrames == 0 || lv.Frames.PLI == 0 || A.v.pliCount() == 0 {
		t.Fatalf("Bild: %+v pli %d", lv.Frames, A.v.pliCount())
	}
	if la.Frames.UpDropped != 0 || la.Frames.UpFrames < 100 {
		t.Fatalf("Ton: %+v", la.Frames)
	}
	var bytesV int
	for _, f := range B.v.frames() {
		if f.csrc == A.a.csrc() {
			bytesV += len(f.data)
		}
	}
	if kbit := bytesV * 8 / 3 / 1000; kbit > 200 {
		t.Fatalf("Drossel wirkt nicht: %d kbit/s beim Empfaenger", kbit)
	}
}

// Luecke in der Rahmen-Nr. des Browsers: bis zum Schluesselbild nichts weiterleiten, pli an den Browser; danach weiter.
func TestFramesLueckePLI(t *testing.T) {
	s, hs, url := tunnelServer(t)
	room := "frames-luecke"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	A := joinFramesDev(t, hs.URL, url, room, "a", 0)
	B := joinFramesDev(t, hs.URL, url, room, "b", 0)
	// Schluesselbild + Bilder, bis B empfaengt
	ts := uint32(0)
	frame := func(key, skip bool) {
		ts += 4500
		fl := byte(0)
		if key {
			fl = frFlagKey
		}
		A.v.send(frTrackL, fl, 0, ts, vp8(key, 800, 0x10), skip)
		time.Sleep(30 * time.Millisecond)
	}
	got := func() int {
		n := 0
		for _, f := range B.v.frames() {
			if f.csrc == A.a.csrc() {
				n++
			}
		}
		return n
	}
	for i := 0; i < 60 && got() < 5; i++ {
		frame(i == 0 || A.v.pliCount() > 0 && i%10 == 0, false)
	}
	if got() < 5 {
		t.Fatalf("vor der Luecke nur %d Bilder", got())
	}
	n0, p0 := got(), A.v.pliCount()
	frame(false, true) // Luecke
	for i := 0; i < 10; i++ {
		frame(false, false)
	}
	if got() != n0 {
		t.Fatalf("nach Luecke weitergeleitet: %d -> %d", n0, got())
	}
	if A.v.pliCount() <= p0 {
		t.Fatal("kein pli nach der Luecke")
	}
	if st := s.getLeg(A.v.id()).stats(); st.Frames.UpGaps != 1 || st.Frames.UpWaitKey < 10 {
		t.Fatalf("Zaehler: %+v", st.Frames)
	}
	frame(true, false)
	for i := 0; i < 5; i++ {
		frame(false, false)
	}
	if !waitFor(2*time.Second, func() bool { return got() >= n0+5 }) {
		t.Fatalf("nach Schluesselbild: %d -> %d", n0, got())
	}
}

// Grenzen: WebSockets je Adresse gemeinsam mit dem Tunnel (4 x HORN_MAX_PER_IP); Beine je Adresse (HORN_MAX_PER_IP)
// -> limit 4429; gleiche leg_ref ersetzt das alte Bein (alter WebSocket zu).
func TestFramesGrenzenErsetzen(t *testing.T) {
	s, hs, url := tunnelServer(t)
	room := "frames-grenzen"
	if err := postJSON(httpClient(), hs.URL+"/api/dev/room", map[string]string{"room": room}, &struct{}{}); err != nil {
		t.Fatal(err)
	}
	// gleiche leg_ref (peer + role): ersetzt
	a1, _ := dialFrames(t, url, framesJoin{Token: frToken(t, hs.URL, room, "x", "audio", 0), Audio: 3}, true)
	a2, code := dialFrames(t, url, framesJoin{Token: frToken(t, hs.URL, room, "x", "audio", 0), Audio: 3}, true)
	if a1 == nil || a2 == nil {
		t.Fatalf("join: %s", code)
	}
	select {
	case <-a1.done:
	case <-time.After(3 * time.Second):
		t.Fatal("ersetztes Bein: WebSocket offen")
	}
	if a2.id() == a1.id() || !waitFor(2*time.Second, func() bool { return s.getLeg(a1.id()) == nil }) || s.getLeg(a2.id()) == nil {
		t.Fatal("Ersetzen")
	}
	// Beine je Adresse (ausserhalb von HORN_DEV)
	s.cfg.Dev, s.cfg.MaxPerIP = false, 1
	if _, code := dialFrames(t, url, framesJoin{Token: frToken(t, hs.URL, room, "y", "audio", 0), Audio: 3}, true); code != "limit" {
		t.Fatalf("zweites Bein derselben Adresse: %q", code)
	}
	// WebSockets je Adresse: 4 x 1, gemeinsam mit dem Tunnel (a2 ist offen)
	var open []*websocket.Conn
	defer func() {
		for _, w := range open {
			w.Close()
		}
	}()
	for i := 0; i < 3; i++ {
		var ws *websocket.Conn
		var err error
		if i%2 == 0 {
			ws, _, err = dialTunnel(t, url)
		} else {
			ws, _, err = dialFramesWS(t, url)
		}
		if err != nil {
			t.Fatalf("%d: %v", i, err)
		}
		open = append(open, ws)
	}
	if _, resp, err := dialFramesWS(t, url); err == nil || resp == nil || resp.StatusCode != 429 {
		t.Fatalf("5. WebSocket: %v %v", err, resp)
	}
	_ = strconv.Itoa
	_ = strings.TrimSpace
}
