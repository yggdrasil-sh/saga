package main

import (
	"bufio"
	"context"
	"crypto/ed25519"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Bifroest (Paket P9, Konzept v2 §2.4): Kaskade zwischen zwei Knoten ueber TLS/TCP 443.
//
// Aufbau: Saga schickt dem waehlenden Knoten ueber den Steuerkanal
//
//	{"t":"cascade","bridge","url","peer","peer_pub","token","mine"}
//
// token = Kaskaden-Token fuer die Gegenseite (von DEREN Saga signiert, Rolle accept), mine = Token fuer diesen Knoten
// (von der eigenen Saga, Rolle dial). Beide pruefen ein von Saga signiertes Token:
//
//	c1.<payload>.<sig>   sig = Ed25519(saga_sk, "saga-horn-cascade/1." + payload)
//	payload {v:1, bridge, node, room, role: accept|dial, peer_node, peer_pub, q?, q_legs?, q_kbit?, exp (+60 s), jti}
//
// Der waehlende Knoten baut TLS zum anderen auf (443, ALPN http/1.1) und schickt
//
//	GET /api/sfu/bridge  Upgrade: saga-bifrost/1  X-Bifrost-Token: <token>  X-Bifrost-From: <eigener Name>
//	X-Bifrost-Sig: B64(Ed25519(knoten_sk, "saga-bifrost/1 dial\n" + bridge + "\n" + HEX(EKM)))
//
// EKM = TLS-ExportKeyingMaterial("saga-bifrost/1", 32): die Unterschrift gilt nur fuer genau diese TLS-Verbindung
// (kein Weiterreichen durch einen Mittelsmann). Der andere prueft Token (Signatur seiner Saga, Name, Ablauf, einmalig) und
// die Unterschrift mit peer_pub aus dem Token und antwortet 101 mit X-Bifrost-Sig ("... accept ..."), die der waehlende
// Knoten mit peer_pub aus dem Steuerbefehl prueft. Danach Rahmen in beide Richtungen:
//
//	[typ u8][laenge u16][nutzlast]   typ 1 = RTP: [sid u16][paket]   typ 2 = Steuerung (JSON)
//
// Steuerung: peers {p:[{id, d, sp, s:[[sid, art 0=Ton|1=Bild, schicht, bildschirm 0|1, ext, aktiv 0|1]]}]} (Teilnehmende
// dieses Knotens und anderer Bruecken, nie die der Gegenseite), want {s:[sid]} (gewuenschte Bild-Quellen der Gegenseite),
// pli {s}, ping, bye {why}. Ton: jede Seite schickt von sich aus ihre lautesten drei (Top-3); Bild nur auf Nachfrage
// (Last-N, Sprecher, Schichtwahl der Gegenseite). Die Pakete bleiben unveraendert (Frame-E2E, die Bruecke sieht nur
// Chiffrat), nur die Folgenummer wird beim Verwerfen lueckenlos gehalten.

const (
	bifrostProto     = "saga-bifrost/1"
	ctokPrefix       = "c1."
	ctokDomain       = "saga-horn-cascade/1."
	bridgeTick       = 200 * time.Millisecond
	bridgeIdle       = 30 * time.Second // ohne eigene Teilnehmende (und ohne weitere Bruecken) so lange, dann zu
	bridgeAudio      = 3                // Ton-Top-3 je Richtung
	bridgeFrameMax   = 65535
	bridgeReadIdle   = 20 * time.Second
	bridgeWriteLimit = 5 * time.Second
	frameRTP         = 1
	frameCtl         = 2
)

type ctokBody struct {
	V        int    `json:"v"`
	Bridge   string `json:"bridge"`
	Node     string `json:"node"`
	Room     string `json:"room"`
	Role     string `json:"role"`
	PeerNode string `json:"peer_node"`
	PeerPub  string `json:"peer_pub"`
	Q        string `json:"q,omitempty"`
	QLegs    int    `json:"q_legs,omitempty"`
	QKbit    int    `json:"q_kbit,omitempty"`
	Exp      int64  `json:"exp"`
	JTI      string `json:"jti"`
}

// verifyCascade prueft ein Kaskaden-Token (Signatur der eigenen Saga, Form, Lebensdauer, Ablauf).
func (v *sagaVerifier) verifyCascade(tok string) (ctokBody, error) {
	var b ctokBody
	k := v.pub.Load()
	if k == nil {
		return b, sfu.ErrNoVerifier
	}
	if len(tok) > 4096 || !strings.HasPrefix(tok, ctokPrefix) {
		return b, errToken
	}
	p, sig, ok := strings.Cut(tok[len(ctokPrefix):], ".")
	if !ok {
		return b, errToken
	}
	sb, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil || len(sb) != ed25519.SignatureSize || !ed25519.Verify(*k, []byte(ctokDomain+p), sb) {
		return b, errToken
	}
	raw, err := base64.RawURLEncoding.DecodeString(p)
	if err != nil || json.Unmarshal(raw, &b) != nil || b.V != 1 {
		return b, errToken
	}
	if b.Node == "" || b.PeerNode == "" || !validRef(b.Bridge) || !validRef(b.Room) || !validRef(b.JTI) || b.Exp <= 0 ||
		(b.Role != "accept" && b.Role != "dial") || (b.Q != "" && !validRef(b.Q)) {
		return b, errToken
	}
	exp := time.Unix(b.Exp, 0)
	if time.Until(exp) > tokenMaxLife {
		return b, errToken
	}
	if time.Now().After(exp) {
		return b, sfu.ErrExpired
	}
	if pk, err := base64.StdEncoding.DecodeString(b.PeerPub); err != nil || len(pk) != ed25519.PublicKeySize {
		return b, errToken
	}
	return b, nil
}

func bridgeMsg(role, bridge string, ekm []byte) []byte {
	return []byte(bifrostProto + " " + role + "\n" + bridge + "\n" + hex.EncodeToString(ekm))
}

func verifySig(pubB64, sigB64 string, msg []byte) bool {
	pk, err := base64.StdEncoding.DecodeString(pubB64)
	if err != nil || len(pk) != ed25519.PublicKeySize {
		return false
	}
	sig, err := base64.StdEncoding.DecodeString(sigB64)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(ed25519.PublicKey(pk), msg, sig)
}

// ---------- Bruecke ----------

type bridge struct {
	s       *server
	id      string
	room    *sfu.Room
	roomRef string
	peer    string // Name des anderen Knotens
	dialer  bool
	quota   *sfu.Quota // Kontingent des verbundenen Servers (Bruecke zu einem fremden Knoten)
	qkey    string

	conn net.Conn
	br   *bufio.Reader
	wmu  sync.Mutex
	bw   *bufio.Writer
	werr error
	fl   *fwd.Leg

	// Senden (Takt-Goroutine; smu fuer want/bySid aus dem Leser)
	smu     sync.Mutex
	sids    map[*fwd.Source]uint16
	bySid   map[uint16]*fwd.Source
	nextSid uint16
	outs    map[*fwd.Source]*fwd.Out
	want    map[uint16]bool
	aud     []*fwd.Source
	peerSig string
	peerAt  time.Time

	// Empfangen (Leser-Goroutine schreibt unter rmu, liest ohne)
	rmu     sync.Mutex
	rsrc    map[uint16]*rsource
	rpeers  map[string]bool
	wantSig string
	wantAt  time.Time

	up       atomic.Bool
	done     chan struct{}
	once     sync.Once
	why      string
	idleFrom time.Time
	started  time.Time

	BytesIn, BytesOut, PktsIn, PktsOut atomic.Uint64
}

type rsource struct {
	src    *fwd.Source
	peer   string
	kind   fwd.Kind
	layer  string
	screen bool
}

type rawW struct {
	b   *bridge
	sid uint16
}

func (w rawW) WriteRaw(p []byte) error { return w.b.writeRTP(w.sid, p) }

type bridgeFlusher struct{ b *bridge }

func (f bridgeFlusher) Flush() { f.b.flush() }

func newBridge(s *server, id string, room *sfu.Room, peer string, dialer bool, conn net.Conn, br *bufio.Reader) *bridge {
	b := &bridge{s: s, id: id, room: room, roomRef: room.Ref(), peer: peer, dialer: dialer, conn: conn, br: br,
		bw: bufio.NewWriterSize(conn, 64<<10), fl: fwd.NewLeg(), sids: map[*fwd.Source]uint16{}, bySid: map[uint16]*fwd.Source{},
		outs: map[*fwd.Source]*fwd.Out{}, want: map[uint16]bool{}, rsrc: map[uint16]*rsource{}, rpeers: map[string]bool{},
		done: make(chan struct{}), started: time.Now()}
	if br == nil {
		b.br = bufio.NewReaderSize(conn, 64<<10)
	}
	b.fl.SetFlusher(bridgeFlusher{b})
	return b
}

func (b *bridge) writeFrame(typ byte, sid int, p []byte) error {
	n := len(p)
	if sid >= 0 {
		n += 2
	}
	if n > bridgeFrameMax {
		return errors.New("rahmen zu gross")
	}
	b.wmu.Lock()
	defer b.wmu.Unlock()
	if b.werr != nil {
		return b.werr
	}
	var h [5]byte
	h[0] = typ
	binary.BigEndian.PutUint16(h[1:3], uint16(n))
	hl := 3
	if sid >= 0 {
		binary.BigEndian.PutUint16(h[3:5], uint16(sid))
		hl = 5
	}
	if b.bw.Available() < hl+len(p) { // der Puffer geht gleich an die Verbindung: Frist erneuern (sonst gilt die vom letzten Leeren)
		_ = b.conn.SetWriteDeadline(time.Now().Add(bridgeWriteLimit))
	}
	if _, err := b.bw.Write(h[:hl]); err != nil {
		b.werr = err
		return err
	}
	if _, err := b.bw.Write(p); err != nil {
		b.werr = err
		return err
	}
	b.BytesOut.Add(uint64(hl + len(p)))
	return nil
}

func (b *bridge) writeRTP(sid uint16, p []byte) error {
	b.PktsOut.Add(1)
	return b.writeFrame(frameRTP, int(sid), p)
}

func (b *bridge) flush() {
	b.wmu.Lock()
	defer b.wmu.Unlock()
	if b.werr != nil || b.bw.Buffered() == 0 {
		return
	}
	_ = b.conn.SetWriteDeadline(time.Now().Add(bridgeWriteLimit))
	if err := b.bw.Flush(); err != nil {
		b.werr = err
		go b.close("schreiben: " + err.Error())
	}
}

func (b *bridge) ctl(m map[string]any) {
	p, err := json.Marshal(m)
	if err != nil {
		return
	}
	if b.writeFrame(frameCtl, -1, p) == nil {
		b.flush()
	}
}

// run startet Leser und Takt; blockiert bis die Bruecke zu ist.
func (b *bridge) run() {
	b.up.Store(true)
	log.Printf("bifroest %s: verbunden mit %s (raum %s, %s)", short(b.id), b.peer, short(b.roomRef), map[bool]string{true: "waehlend", false: "annehmend"}[b.dialer])
	go b.reader()
	t := time.NewTicker(bridgeTick)
	defer t.Stop()
	for {
		select {
		case <-b.done:
			return
		case <-t.C:
			if b.room.Closed() {
				b.close("raum geschlossen")
				return
			}
			b.tick()
		}
	}
}

func short(s string) string {
	if len(s) > 8 {
		return s[:8]
	}
	return s
}

func (b *bridge) close(why string) {
	b.once.Do(func() {
		b.why = why
		b.up.Store(false)
		close(b.done)
		b.ctlBye(why)
		_ = b.conn.Close()
		b.room.RemoveVia(b.id)
		b.smu.Lock()
		for _, o := range b.outs {
			o.Detach()
		}
		b.outs = map[*fwd.Source]*fwd.Out{}
		b.smu.Unlock()
		b.fl.Close()
		b.s.bridgeGone(b)
		log.Printf("bifroest %s: getrennt (%s)", short(b.id), why)
	})
}

func (b *bridge) ctlBye(why string) {
	p, _ := json.Marshal(map[string]any{"t": "bye", "why": why})
	b.wmu.Lock()
	if b.werr == nil {
		var h [3]byte
		h[0] = frameCtl
		binary.BigEndian.PutUint16(h[1:3], uint16(len(p)))
		_ = b.conn.SetWriteDeadline(time.Now().Add(time.Second))
		_, _ = b.bw.Write(h[:])
		_, _ = b.bw.Write(p)
		_ = b.bw.Flush()
	}
	b.wmu.Unlock()
}

// tick: Teilnehmende melden, Ton-Top-3 und gewuenschte Bild-Quellen weiterleiten, eigene Nachfrage melden.
func (b *bridge) tick() {
	now := time.Now()
	view := b.room.View(b.id)
	// Leerlauf: keine eigenen Teilnehmenden und keine anderen Bruecken -> nach bridgeIdle zu
	if b.room.LocalPeers() == 0 && !b.s.otherBridges(b) {
		if b.idleFrom.IsZero() {
			b.idleFrom = now
		} else if now.Sub(b.idleFrom) > bridgeIdle {
			go b.close("leer")
			return
		}
	} else {
		b.idleFrom = time.Time{}
	}
	b.smu.Lock()
	// Kennungen der Quellen (sid) - nur fuer Quellen im Blick
	alive := map[*fwd.Source]bool{}
	for _, p := range view {
		for _, sv := range p.Srcs {
			alive[sv.Src] = true
			if _, ok := b.sids[sv.Src]; !ok {
				b.nextSid++
				if b.nextSid == 0 {
					b.nextSid = 1
				}
				b.sids[sv.Src] = b.nextSid
				b.bySid[b.nextSid] = sv.Src
			}
		}
	}
	for src, sid := range b.sids {
		if !alive[src] {
			delete(b.sids, src)
			delete(b.bySid, sid)
			delete(b.want, sid)
		}
	}
	// Teilnehmende melden (bei Aenderung, sonst alle 2 s fuer die letzte Sprache)
	type srcJ = [6]any
	type peerJ struct {
		ID string `json:"id"`
		D  string `json:"d,omitempty"`
		SP int64  `json:"sp"`
		S  []srcJ `json:"s"`
	}
	ps := make([]peerJ, 0, len(view))
	var sig strings.Builder
	nowF := fwd.Now()
	for _, p := range view {
		pj := peerJ{ID: p.ID, SP: -1}
		if p.Device == sfu.Mobile {
			pj.D = "m"
		}
		if p.Spoke > 0 {
			pj.SP = (nowF - p.Spoke) / int64(time.Millisecond)
		}
		sig.WriteString(p.ID)
		sig.WriteByte(':')
		for _, sv := range p.Srcs {
			k, scr, on := 0, 0, 0
			if sv.Src.Kind == fwd.Video {
				k = 1
			}
			if sv.Screen {
				scr = 1
			}
			if sv.On {
				on = 1
			}
			sid := b.sids[sv.Src]
			pj.S = append(pj.S, srcJ{sid, k, sv.Src.Layer, scr, sv.Src.LevelExt, on})
			sig.WriteString(strconv.Itoa(int(sid)))
			sig.WriteByte('/')
			sig.WriteString(strconv.Itoa(on))
			sig.WriteByte(',')
		}
		sig.WriteByte(';')
		ps = append(ps, pj)
	}
	sendPeers := sig.String() != b.peerSig || now.Sub(b.peerAt) > 2*time.Second
	if sendPeers {
		b.peerSig, b.peerAt = sig.String(), now
	}
	// Ton: Top-3 nach Sprechaktivitaet (wer schon dabei ist und spricht, bleibt)
	cand := make([]sfu.PeerView, 0, len(view))
	for _, p := range view {
		if p.Audio != nil {
			cand = append(cand, p)
		}
	}
	cur := map[*fwd.Source]bool{}
	for _, a := range b.aud {
		cur[a] = true
	}
	sort.SliceStable(cand, func(i, j int) bool {
		a, c := cand[i], cand[j]
		as, cs := a.Score >= 0.04, c.Score >= 0.04
		if as != cs {
			return as
		}
		if as && a.Score != c.Score {
			return a.Score > c.Score
		}
		if cur[a.Audio] != cur[c.Audio] {
			return cur[a.Audio]
		}
		return a.Spoke > c.Spoke
	})
	wantAud := map[*fwd.Source]bool{}
	b.aud = b.aud[:0]
	for i := 0; i < len(cand) && i < bridgeAudio; i++ {
		wantAud[cand[i].Audio] = true
		b.aud = append(b.aud, cand[i].Audio)
	}
	// Ausgaenge: Ton-Top-3 + gewuenschte Bild-Quellen
	need := map[*fwd.Source]bool{}
	for a := range wantAud {
		need[a] = true
	}
	for sid := range b.want {
		if src := b.bySid[sid]; src != nil && src.Kind == fwd.Video {
			need[src] = true
		}
	}
	for src, o := range b.outs {
		if !need[src] {
			o.Detach()
			delete(b.outs, src)
		}
	}
	screens := map[*fwd.Source]bool{}
	for _, p := range view {
		for _, sv := range p.Srcs {
			if sv.Screen {
				screens[sv.Src] = true
			}
		}
	}
	for src := range need {
		if o := b.outs[src]; o != nil {
			// wie die Beine in jedem Takt: wartet der Ausgang auf ein Schluesselbild (Anlegen, Stau nach H3), fragt
			// Assign erneut an - sonst blieb es bei einer PLI, die in die Drossel fallen kann (BH3)
			o.Assign(src)
			continue
		}
		cl := fwd.ClassSpeaker
		switch {
		case src.Kind == fwd.Audio:
			cl = fwd.ClassAudio
		case screens[src]:
			cl = fwd.ClassScreen
		}
		o := b.fl.NewOut("b"+strconv.Itoa(int(b.sids[src])), src.Owner, src.Kind, cl)
		o.BindRaw(rawW{b, b.sids[src]})
		o.Assign(src)
		b.outs[src] = o
	}
	b.smu.Unlock()
	if sendPeers {
		b.ctl(map[string]any{"t": "peers", "p": ps})
	}
	// Nachfrage: Bild-Quellen der Gegenseite, die hier ein Ausgang weiterleitet bzw. waehlt
	b.rmu.Lock()
	var ws []int
	for sid, r := range b.rsrc {
		if r.kind == fwd.Video && r.src.Subscribed() {
			ws = append(ws, int(sid))
		}
	}
	b.rmu.Unlock()
	sort.Ints(ws)
	wsig := fmt.Sprint(ws)
	if wsig != b.wantSig || now.Sub(b.wantAt) > 2*time.Second {
		b.wantSig, b.wantAt = wsig, now
		if ws == nil {
			ws = []int{}
		}
		b.ctl(map[string]any{"t": "want", "s": ws})
	}
}

// reader liest Rahmen der Gegenseite.
func (b *bridge) reader() {
	var hdr [3]byte
	buf := make([]byte, bridgeFrameMax)
	lastDL := time.Time{}
	for {
		if now := time.Now(); now.Sub(lastDL) > time.Second {
			lastDL = now
			_ = b.conn.SetReadDeadline(now.Add(bridgeReadIdle))
		}
		if _, err := io.ReadFull(b.br, hdr[:]); err != nil {
			b.close("lesen: " + errText(err))
			return
		}
		n := int(binary.BigEndian.Uint16(hdr[1:3]))
		if _, err := io.ReadFull(b.br, buf[:n]); err != nil {
			b.close("lesen: " + errText(err))
			return
		}
		b.BytesIn.Add(uint64(n + 3))
		switch hdr[0] {
		case frameRTP:
			if n < 2+12 {
				continue
			}
			b.PktsIn.Add(1)
			sid := binary.BigEndian.Uint16(buf[:2])
			if r := b.rsrc[sid]; r != nil {
				r.src.Inject(buf[2:n])
			}
		case frameCtl:
			if !b.onCtl(buf[:n]) {
				return
			}
		}
	}
}

// validPeerID: Kennung eines Teilnehmenden ueber die Bruecke (wie validRef, auch kurze Kennungen der Testbots).
func validPeerID(s string) bool {
	return len(s) > 0 && len(s) <= 56 && validRef("xxxxxxxx"+s)
}

func errText(err error) string {
	if errors.Is(err, io.EOF) {
		return "zu"
	}
	return err.Error()
}

type bridgeCtl struct {
	T   string `json:"t"`
	Why string `json:"why"`
	S   any    `json:"s"`
	P   []struct {
		ID string            `json:"id"`
		D  string            `json:"d"`
		SP int64             `json:"sp"`
		S  []json.RawMessage `json:"s"`
	} `json:"p"`
}

func (b *bridge) onCtl(raw []byte) bool {
	var m bridgeCtl
	if json.Unmarshal(raw, &m) != nil {
		return true
	}
	switch m.T {
	case "peers":
		b.onPeers(m)
	case "want":
		var ws []uint16
		if arr, ok := m.S.([]any); ok {
			for _, x := range arr {
				if f, ok := x.(float64); ok && f > 0 && f < 65536 {
					ws = append(ws, uint16(f))
				}
			}
		}
		b.smu.Lock()
		b.want = map[uint16]bool{}
		for _, s := range ws {
			b.want[s] = true
		}
		b.smu.Unlock()
	case "pli":
		if f, ok := m.S.(float64); ok && f > 0 && f < 65536 {
			b.smu.Lock()
			src := b.bySid[uint16(f)]
			b.smu.Unlock()
			if src != nil {
				src.RequestPLI()
			}
		}
	case "bye":
		b.close("gegenseite: " + m.Why)
		return false
	}
	return true
}

// onPeers: Teilnehmende der Gegenseite in den Raum uebernehmen (nur Leser-Goroutine).
func (b *bridge) onPeers(m bridgeCtl) {
	seen := map[string]bool{}
	sidSeen := map[uint16]bool{}
	for _, p := range m.P {
		if !validPeerID(p.ID) || len(seen) >= 200 {
			continue
		}
		seen[p.ID] = true
		dev := sfu.Desktop
		if p.D == "m" {
			dev = sfu.Mobile
		}
		if !b.rpeers[p.ID] {
			if !b.room.AddRemote(b.id, p.ID, dev, b.quota) {
				continue
			}
			b.rmu.Lock()
			b.rpeers[p.ID] = true
			b.rmu.Unlock()
		}
		on := map[*fwd.Source]bool{}
		for _, rs := range p.S {
			var a [6]any
			if json.Unmarshal(rs, &a) != nil {
				continue
			}
			fs, _ := a[0].(float64)
			kf, _ := a[1].(float64)
			layer, _ := a[2].(string)
			sf, _ := a[3].(float64)
			ef, _ := a[4].(float64)
			of, _ := a[5].(float64)
			if fs <= 0 || fs >= 65536 || (layer != "a" && layer != "l" && layer != "m" && layer != "h") {
				continue
			}
			sid := uint16(fs)
			kind := fwd.Audio
			if kf == 1 {
				kind = fwd.Video
			}
			sidSeen[sid] = true
			r := b.rsrc[sid]
			if r != nil && (r.peer != p.ID || r.kind != kind || r.layer != layer || r.screen != (sf == 1)) {
				b.room.DropRemoteSource(r.src)
				b.rmu.Lock()
				delete(b.rsrc, sid)
				b.rmu.Unlock()
				r = nil
			}
			if r == nil {
				s0 := sid
				src := b.room.RemoteSource(b.id, p.ID, kind, layer, sf == 1, uint8(ef), func(uint32) { b.ctl(map[string]any{"t": "pli", "s": int(s0)}) })
				if src == nil {
					continue
				}
				r = &rsource{src: src, peer: p.ID, kind: kind, layer: layer, screen: sf == 1}
				b.rmu.Lock()
				b.rsrc[sid] = r
				b.rmu.Unlock()
			}
			on[r.src] = of == 1
		}
		ago := time.Duration(-1)
		if p.SP >= 0 {
			ago = time.Duration(p.SP) * time.Millisecond
		}
		b.room.SetRemote(p.ID, ago, on)
	}
	for sid, r := range b.rsrc {
		if !sidSeen[sid] {
			b.room.DropRemoteSource(r.src)
			b.rmu.Lock()
			delete(b.rsrc, sid)
			b.rmu.Unlock()
		}
	}
	for id := range b.rpeers {
		if !seen[id] {
			b.room.RemoveRemote(id)
			b.rmu.Lock()
			delete(b.rpeers, id)
			b.rmu.Unlock()
		}
	}
}

// ---------- Verwaltung im Knoten ----------

type bridgeInfo struct {
	St   string `json:"st"`
	Peer string `json:"peer,omitempty"`
	Why  string `json:"why,omitempty"`
}

type cascadeCmd struct {
	Bridge, URL, Peer, PeerPub, Token, Mine string
	Room                                    string   // Entwicklungsmodus
	IPs                                     []string // von Saga gepruefte Zieladressen (genau diese waehlen, kein DNS)
	Own                                     bool     // Ziel ist ein eigener Knoten derselben Saga (Name frei aufloesen)
}

// ctokMaxLen: Hoechstlaenge eines Kaskaden-Tokens (wie Saga, fed_horn.CTOK_MAX).
const ctokMaxLen = 4096

// peerTokenOK: das fremde Token geht unveraendert in eine Kopfzeile der Waehlanfrage - nur Base64url und Punkte
// (c1.<nutzlast>.<signatur>), im Entwicklungsmodus eine Raumkennung. Kein CR/LF, keine Leerzeichen, nichts anderes.
func peerTokenOK(dev bool, tok string) bool {
	if dev {
		return validRef(tok)
	}
	if len(tok) > ctokMaxLen || !strings.HasPrefix(tok, ctokPrefix) {
		return false
	}
	parts := strings.Split(tok[len(ctokPrefix):], ".")
	if len(parts) != 2 {
		return false
	}
	for _, p := range parts {
		if p == "" {
			return false
		}
		for i := 0; i < len(p); i++ {
			if c := p[i]; !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
				return false
			}
		}
	}
	return true
}

// publicAddr: oeffentliche Unicast-Adresse (IPv4-gemappte IPv6 wie IPv4; NAT64/6to4 mit internem Ziel nicht).
func publicAddr(a netip.Addr) bool {
	a = a.Unmap()
	if !a.IsValid() || a.Zone() != "" || !a.IsGlobalUnicast() || a.IsPrivate() || a.IsLoopback() || a.IsLinkLocalUnicast() {
		return false
	}
	for _, p := range nonPublic {
		if p.Contains(a) {
			return false
		}
	}
	if a.Is6() {
		b := a.As16()
		if netip.MustParsePrefix("64:ff9b::/96").Contains(a) {
			return publicAddr(netip.AddrFrom4([4]byte{b[12], b[13], b[14], b[15]}))
		}
		if netip.MustParsePrefix("2002::/16").Contains(a) {
			return publicAddr(netip.AddrFrom4([4]byte{b[2], b[3], b[4], b[5]}))
		}
	}
	return true
}

var nonPublic = func() []netip.Prefix {
	var out []netip.Prefix
	for _, p := range []string{"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
		"203.0.113.0/24", "240.0.0.0/4", "::/128", "100::/64", "2001:db8::/32", "2001::/23"} {
		out = append(out, netip.MustParsePrefix(p))
	}
	return out
}()

// bridgeTargets: Waehlziele der Kaskade (host:port). Fremder Knoten: Saga gibt die geprueften Adressen mit - genau diese,
// kein erneutes DNS (Rebinding zwischen Pruefung und Waehlen). Eigener Knoten (own, auch im LAN): Name aufloesen.
// Sonst (aeltere Saga) selbst aufloesen, dann nur oeffentliche Adressen.
func (s *server) bridgeTargets(ctx context.Context, c cascadeCmd, u *url.URL) ([]string, error) {
	port := u.Port()
	if port == "" {
		port = "443"
	}
	if len(c.IPs) > 0 {
		if len(c.IPs) > 8 {
			return nil, errors.New("adresse")
		}
		var out []string
		for _, ip := range c.IPs {
			a, err := netip.ParseAddr(ip)
			if err != nil || a.Zone() != "" || a.IsUnspecified() || a.IsMulticast() {
				return nil, errors.New("adresse")
			}
			out = append(out, net.JoinHostPort(a.Unmap().String(), port))
		}
		return out, nil
	}
	if s.cfg.Dev || c.Own {
		return []string{net.JoinHostPort(u.Hostname(), port)}, nil
	}
	lctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupNetIP(lctx, "ip", u.Hostname())
	if err != nil || len(ips) == 0 {
		return nil, errors.New("adresse nicht aufloesbar")
	}
	var out []string
	for _, a := range ips {
		if !publicAddr(a) {
			return nil, errors.New("interne adresse")
		}
		out = append(out, net.JoinHostPort(a.Unmap().String(), port))
	}
	return out, nil
}

// bridgeSet haelt die Bruecken des Knotens.
type bridgeSet struct {
	mu    sync.Mutex
	m     map[string]*bridge
	dial  map[string]*dialState
	stats map[string]bridgeInfo // letzter Stand (Lastmeldung)
}

type dialState struct {
	cmd    cascadeCmd
	until  time.Time
	cancel context.CancelFunc
	err    string
}

func (s *server) bridges() *bridgeSet {
	s.brOnce.Do(func() {
		s.brs = &bridgeSet{m: map[string]*bridge{}, dial: map[string]*dialState{}, stats: map[string]bridgeInfo{}}
	})
	return s.brs
}

func (s *server) otherBridges(b *bridge) bool {
	bs := s.bridges()
	bs.mu.Lock()
	defer bs.mu.Unlock()
	for id, x := range bs.m {
		if id != b.id && x.roomRef == b.roomRef {
			return true
		}
	}
	return false
}

func (s *server) bridgeGone(b *bridge) {
	bs := s.bridges()
	bs.mu.Lock()
	if bs.m[b.id] == b {
		delete(bs.m, b.id)
		bs.stats[b.id] = bridgeInfo{St: "zu", Peer: b.peer, Why: b.why}
	}
	bs.mu.Unlock()
}

func (s *server) addBridge(b *bridge) {
	bs := s.bridges()
	bs.mu.Lock()
	old := bs.m[b.id]
	bs.m[b.id] = b
	bs.stats[b.id] = bridgeInfo{St: "up", Peer: b.peer}
	bs.mu.Unlock()
	if old != nil {
		old.close("ersetzt")
	}
}

// bridgeStates fuer die Lastmeldung: {bridge: {st: up|dial|err|zu, peer, why}}; alte Eintraege nach 2 min weg.
func (s *server) bridgeStates() map[string]bridgeInfo {
	bs := s.bridges()
	bs.mu.Lock()
	defer bs.mu.Unlock()
	out := make(map[string]bridgeInfo, len(bs.stats))
	for id, st := range bs.stats {
		if b := bs.m[id]; b != nil {
			st = bridgeInfo{St: "up", Peer: b.peer}
		} else if d := bs.dial[id]; d != nil {
			st = bridgeInfo{St: "dial", Peer: d.cmd.Peer, Why: d.err}
		}
		out[id] = st
	}
	return out
}

// closeBridges: alle Bruecken eines Raums (bzw. alle mit Kontingent q) schliessen.
func (s *server) closeBridges(room, q, why string) {
	bs := s.bridges()
	bs.mu.Lock()
	var cl []*bridge
	for _, b := range bs.m {
		if (room != "" && b.roomRef == room) || (q != "" && b.qkey == q) {
			cl = append(cl, b)
		}
	}
	bs.mu.Unlock()
	for _, b := range cl {
		b.close(why)
	}
}

func (s *server) uncascade(id string) {
	bs := s.bridges()
	bs.mu.Lock()
	b := bs.m[id]
	if d := bs.dial[id]; d != nil {
		d.cancel()
		delete(bs.dial, id)
	}
	delete(bs.stats, id)
	bs.mu.Unlock()
	if b != nil {
		b.close("saga")
	}
}

// cascade (Steuerbefehl): eigenes Token pruefen, dann waehlen (mit Neuversuch bis zum Ablauf der Token).
func (s *server) cascade(c cascadeCmd) error {
	var mine ctokBody
	if !s.cfg.Dev {
		var err error
		mine, err = s.verifier.verifyCascade(c.Mine)
		if err != nil {
			return fmt.Errorf("eigenes token: %w", err)
		}
		if mine.Node != s.cfg.Name || mine.Role != "dial" || mine.Bridge != c.Bridge || mine.PeerNode != c.Peer || mine.PeerPub != c.PeerPub {
			return errors.New("eigenes token passt nicht")
		}
		c.Room = mine.Room
	}
	if !validRef(c.Bridge) || !validRef(c.Room) {
		return errors.New("kennung")
	}
	if !peerTokenOK(s.cfg.Dev, c.Token) {
		return errors.New("token der gegenseite")
	}
	u, err := url.Parse(c.URL)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" {
		return errors.New("adresse")
	}
	bs := s.bridges()
	bs.mu.Lock()
	if b := bs.m[c.Bridge]; b != nil && b.up.Load() {
		bs.mu.Unlock()
		return nil // laeuft schon
	}
	if d := bs.dial[c.Bridge]; d != nil {
		d.cmd, d.until = c, time.Now().Add(tokenMaxLife)
		bs.mu.Unlock()
		return nil
	}
	ctx, cancel := context.WithCancel(context.Background())
	d := &dialState{cmd: c, until: time.Now().Add(tokenMaxLife), cancel: cancel}
	bs.dial[c.Bridge] = d
	bs.stats[c.Bridge] = bridgeInfo{St: "dial", Peer: c.Peer}
	bs.mu.Unlock()
	go s.dialLoop(ctx, d)
	return nil
}

func (s *server) dialLoop(ctx context.Context, d *dialState) {
	back := 500 * time.Millisecond
	defer func() {
		bs := s.bridges()
		bs.mu.Lock()
		if bs.dial[d.cmd.Bridge] == d {
			delete(bs.dial, d.cmd.Bridge)
			if _, ok := bs.m[d.cmd.Bridge]; !ok && d.err != "" {
				bs.stats[d.cmd.Bridge] = bridgeInfo{St: "err", Peer: d.cmd.Peer, Why: d.err}
			}
		}
		bs.mu.Unlock()
	}()
	for {
		bs := s.bridges()
		bs.mu.Lock()
		c := d.cmd
		until := d.until
		bs.mu.Unlock()
		if time.Now().After(until) || ctx.Err() != nil || s.closing.Load() {
			return
		}
		b, err := s.dialBridge(ctx, c)
		if err == nil {
			bs.mu.Lock()
			delete(bs.dial, c.Bridge)
			bs.mu.Unlock()
			s.addBridge(b)
			b.run()
			return
		}
		bs.mu.Lock()
		d.err = err.Error()
		bs.mu.Unlock()
		if debug {
			log.Printf("debug bifroest %s: %v", short(c.Bridge), err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(back):
		}
		if back *= 2; back > 4*time.Second {
			back = 4 * time.Second
		}
	}
}

// bridgeClientTLS: TLS des waehlenden Knotens. Verlangt wird TLS 1.3 (Pruefung nach dem Handshake), angeboten wird
// aber ab 1.2: Mit MinVersion 1.3 fehlen im Hello die ECDHE-ECDSA-Suiten, und autocert auf der Gegenseite (Knoten bis
// rc.184) waehlt dann das RSA-Zertifikat - das es meist nicht gibt: der Handshake hing in einer neuen ACME-Bestellung
// ("context deadline exceeded") oder scheiterte mit "internal error".
func bridgeClientTLS(host string) *tls.Config {
	return &tls.Config{ServerName: host, InsecureSkipVerify: true, NextProtos: []string{"http/1.1"}, MinVersion: tls.VersionTLS12} //nolint:gosec
}

// dialBridge baut die Verbindung zum annehmenden Knoten auf.
func (s *server) dialBridge(ctx context.Context, c cascadeCmd) (*bridge, error) {
	if !peerTokenOK(s.cfg.Dev, c.Token) || !validRef(c.Bridge) { // geht in Kopfzeilen: vor jedem Waehlen pruefen
		return nil, errors.New("token der gegenseite")
	}
	u, err := url.Parse(c.URL)
	if err != nil || u.Scheme != "https" || u.Host == "" {
		return nil, errors.New("adresse")
	}
	targets, err := s.bridgeTargets(ctx, c, u)
	if err != nil {
		return nil, err
	}
	room := s.node.Room(c.Room)
	if room == nil {
		s.mu.Lock()
		dead := time.Now().Before(s.graves[c.Room])
		s.mu.Unlock()
		if dead {
			return nil, errors.New("raum geschlossen")
		}
		var err error
		if room, err = s.node.CreateRoom(c.Room, nil); err != nil {
			return nil, err
		}
	}
	dctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	nd := &net.Dialer{Timeout: 5 * time.Second}
	var raw net.Conn
	for _, t := range targets { // genau die gepruefte Adresse; Name nur fuer SNI und Host
		if raw, err = nd.DialContext(dctx, "tcp", t); err == nil {
			break
		}
	}
	if err != nil {
		return nil, err
	}
	if tc, ok := raw.(*net.TCPConn); ok {
		netx.Tune(tc)
	}
	// Echtheit des anderen Knotens: Unterschrift mit seinem Knotenschluessel ueber die Kanalbindung (EKM), nicht das
	// Web-PKI-Zertifikat (der Schluessel kommt signiert ueber die eigene Saga)
	tc := tls.Client(raw, bridgeClientTLS(u.Hostname()))
	_ = raw.SetDeadline(time.Now().Add(8 * time.Second))
	if err := tc.HandshakeContext(dctx); err != nil {
		raw.Close()
		return nil, fmt.Errorf("tls: %w", err)
	}
	st := tc.ConnectionState()
	if st.Version != tls.VersionTLS13 {
		tc.Close()
		return nil, errors.New("tls 1.3 noetig")
	}
	ekm, err := st.ExportKeyingMaterial(bifrostProto, nil, 32)
	if err != nil {
		tc.Close()
		return nil, err
	}
	req := "GET /api/sfu/bridge HTTP/1.1\r\nHost: " + u.Host + "\r\nUpgrade: " + bifrostProto + "\r\nConnection: Upgrade\r\nX-Saga: 1\r\n" +
		"X-Bifrost-From: " + s.cfg.Name + "\r\nX-Bifrost-Bridge: " + c.Bridge + "\r\n"
	if s.cfg.Dev {
		req += "X-Bifrost-Room: " + c.Token + "\r\n"
	} else {
		req += "X-Bifrost-Token: " + c.Token + "\r\nX-Bifrost-Sig: " +
			base64.StdEncoding.EncodeToString(ed25519.Sign(s.nodeKey, bridgeMsg("dial", c.Bridge, ekm))) + "\r\n"
	}
	if _, err := io.WriteString(tc, req+"\r\n"); err != nil {
		tc.Close()
		return nil, err
	}
	br := bufio.NewReaderSize(tc, 64<<10)
	resp, err := http.ReadResponse(br, nil)
	if err != nil {
		tc.Close()
		return nil, err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusSwitchingProtocols {
		tc.Close()
		return nil, fmt.Errorf("abgelehnt (http %d %s)", resp.StatusCode, resp.Header.Get("X-Bifrost-Error"))
	}
	if !s.cfg.Dev && !verifySig(c.PeerPub, resp.Header.Get("X-Bifrost-Sig"), bridgeMsg("accept", c.Bridge, ekm)) {
		tc.Close()
		return nil, errors.New("gegenseite nicht echt (unterschrift)")
	}
	_ = raw.SetDeadline(time.Time{})
	b := newBridge(s, c.Bridge, room, c.Peer, true, tc, br)
	return b, nil
}

// apiBridge: annehmende Seite (GET /api/sfu/bridge mit Upgrade saga-bifrost/1).
func (s *server) apiBridge(w http.ResponseWriter, r *http.Request) {
	fail := func(code int, msg string) {
		w.Header().Set("X-Bifrost-Error", msg)
		writeJSON(w, code, map[string]string{"error": msg})
	}
	if !strings.EqualFold(r.Header.Get("Upgrade"), bifrostProto) {
		fail(400, "upgrade")
		return
	}
	if s.draining.Load() {
		fail(429, "knoten wird geleert")
		return
	}
	bid, from := r.Header.Get("X-Bifrost-Bridge"), r.Header.Get("X-Bifrost-From")
	var tok ctokBody
	if s.cfg.Dev {
		tok = ctokBody{Bridge: bid, Room: r.Header.Get("X-Bifrost-Room"), PeerNode: from}
	} else {
		var err error
		tok, err = s.verifier.verifyCascade(r.Header.Get("X-Bifrost-Token"))
		if err != nil {
			fail(401, "token: "+err.Error())
			return
		}
		if tok.Role != "accept" || tok.Node != s.cfg.Name || tok.Bridge != bid || tok.PeerNode != from {
			fail(401, "token passt nicht")
			return
		}
		if !s.used.use(tok.JTI, "bridge", time.Unix(tok.Exp, 0)) {
			fail(401, "token schon benutzt")
			return
		}
	}
	if !validRef(tok.Bridge) || !validRef(tok.Room) {
		fail(400, "kennung")
		return
	}
	var q *sfu.Quota
	if tok.Q != "" {
		var err error
		if q, err = s.node.Quota(tok.Q, tok.QLegs, tok.QKbit, false); err != nil {
			fail(429, err.Error())
			return
		}
	}
	room := s.node.Room(tok.Room)
	if room == nil {
		s.mu.Lock()
		dead := time.Now().Before(s.graves[tok.Room])
		s.mu.Unlock()
		if dead {
			fail(404, "raum geschlossen")
			return
		}
		var err error
		if room, err = s.node.CreateRoom(tok.Room, nil); err != nil {
			fail(409, err.Error())
			return
		}
	}
	hj, ok := w.(http.Hijacker)
	if !ok {
		fail(500, "hijack")
		return
	}
	conn, rw, err := hj.Hijack()
	if err != nil {
		return
	}
	var ekm []byte
	if ts, ok := conn.(interface {
		TLSState() (tls.ConnectionState, bool)
	}); ok {
		if st, ok := ts.TLSState(); ok && st.Version == tls.VersionTLS13 {
			ekm, _ = st.ExportKeyingMaterial(bifrostProto, nil, 32)
		}
	}
	reject := func(msg string) {
		_, _ = io.WriteString(conn, "HTTP/1.1 401 Unauthorized\r\nX-Bifrost-Error: "+msg+"\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
		conn.Close()
	}
	resp := "HTTP/1.1 101 Switching Protocols\r\nUpgrade: " + bifrostProto + "\r\nConnection: Upgrade\r\n"
	if !s.cfg.Dev {
		if ekm == nil {
			reject("keine kanalbindung")
			return
		}
		if !verifySig(tok.PeerPub, r.Header.Get("X-Bifrost-Sig"), bridgeMsg("dial", tok.Bridge, ekm)) {
			reject("unterschrift")
			return
		}
		resp += "X-Bifrost-Sig: " + base64.StdEncoding.EncodeToString(ed25519.Sign(s.nodeKey, bridgeMsg("accept", tok.Bridge, ekm))) + "\r\n"
	}
	if _, err := io.WriteString(conn, resp+"\r\n"); err != nil {
		conn.Close()
		return
	}
	_ = conn.SetDeadline(time.Time{})
	b := newBridge(s, tok.Bridge, room, from, false, conn, rw.Reader)
	b.quota, b.qkey = q, tok.Q
	s.addBridge(b)
	go b.run()
}

// bridgeStats (Entwicklung, bench): Zaehler je Bruecke.
func (s *server) bridgeStats() map[string]any {
	bs := s.bridges()
	bs.mu.Lock()
	defer bs.mu.Unlock()
	out := map[string]any{}
	for id, b := range bs.m {
		out[id] = map[string]any{"peer": b.peer, "dialer": b.dialer, "bytes_in": b.BytesIn.Load(), "bytes_out": b.BytesOut.Load(),
			"pkts_in": b.PktsIn.Load(), "pkts_out": b.PktsOut.Load(), "secs": time.Since(b.started).Seconds()}
	}
	return out
}
