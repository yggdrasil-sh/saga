package sfu

import (
	"crypto/rand"
	"encoding/binary"
	"sort"
	"sync"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Node verwaltet die Raeume eines Knotens.
type Node struct {
	cfg Config

	mu     sync.Mutex
	rooms  map[string]*Room
	legs   int
	closed bool
}

// NewNode legt einen Knoten an.
func NewNode(cfg Config) *Node {
	return &Node{cfg: cfg.withDefaults(), rooms: map[string]*Room{}}
}

// Config liefert die wirksame Konfiguration.
func (n *Node) Config() Config { return n.cfg }

// CreateRoom legt einen Raum an (Saga: Anruf auf diesem Knoten). lim nil =
// Standardgrenzen. Ein vorhandener Raum wird zurueckgegeben (idempotent).
func (n *Node) CreateRoom(ref string, lim *Limits) (*Room, error) {
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.closed {
		return nil, ErrClosed
	}
	if r := n.rooms[ref]; r != nil {
		return r, nil
	}
	l := n.cfg.Limits
	if lim != nil {
		l = lim.withDefaults()
	}
	r := &Room{ref: ref, node: n, lim: l, peers: map[string]*Peer{}, legs: map[string]*Leg{},
		csrcs: map[uint32]bool{}, stop: make(chan struct{}), created: time.Now()}
	n.rooms[ref] = r
	go r.run(n.cfg.Tick)
	return r, nil
}

// Room liefert einen Raum oder nil.
func (n *Node) Room(ref string) *Room {
	n.mu.Lock()
	defer n.mu.Unlock()
	return n.rooms[ref]
}

// CloseRoom beendet einen Raum und alle seine Beine.
func (n *Node) CloseRoom(ref string) error {
	n.mu.Lock()
	r := n.rooms[ref]
	delete(n.rooms, ref)
	n.mu.Unlock()
	if r == nil {
		return ErrNoRoom
	}
	r.close("raum geschlossen")
	return nil
}

// Close beendet alle Raeume.
func (n *Node) Close() {
	n.mu.Lock()
	n.closed = true
	rs := make([]*Room, 0, len(n.rooms))
	for _, r := range n.rooms {
		rs = append(rs, r)
	}
	n.rooms = map[string]*Room{}
	n.mu.Unlock()
	for _, r := range rs {
		r.close("knoten beendet")
	}
}

// Admit prueft ein Medien-Token und liefert Freigabe und Raum. Der Raum
// muss vorher angelegt sein (Saga legt ihn ueber den Steuerkanal an).
func (n *Node) Admit(token string, path Path) (Grant, *Room, error) {
	if n.cfg.Verifier == nil {
		return Grant{}, nil, ErrNoVerifier
	}
	g, err := n.cfg.Verifier.Verify(token)
	if err != nil {
		return Grant{}, nil, err
	}
	if !g.Exp.IsZero() && time.Now().After(g.Exp) {
		return Grant{}, nil, ErrExpired
	}
	if g.Node != "" && n.cfg.Name != "" && g.Node != n.cfg.Name {
		return Grant{}, nil, ErrWrongNode
	}
	if path == PathUDP && !g.UDPOK {
		return Grant{}, nil, ErrNoUDP
	}
	r := n.Room(g.Room)
	if r == nil {
		return Grant{}, nil, ErrNoRoom
	}
	return g, r, nil
}

// Load sammelt die Lastzahlen.
func (n *Node) Load() Load {
	n.mu.Lock()
	rs := make([]*Room, 0, len(n.rooms))
	for _, r := range n.rooms {
		rs = append(rs, r)
	}
	n.mu.Unlock()
	var ld Load
	ld.Rooms = len(rs)
	for _, r := range rs {
		r.mu.Lock()
		ld.Peers += len(r.peers)
		ld.Legs += len(r.legs)
		for _, l := range r.legs {
			if l.Path == PathUDP {
				ld.UDPLegs++
			}
			ld.Outs += len(l.outs)
			if p := l.fl.Stats.P95us(); p > ld.FwdP95us {
				ld.FwdP95us = p
			}
			ld.DownKbit += int(l.est.bps / 1000)
		}
		for _, p := range r.peers {
			ld.Sources += len(p.srcs)
		}
		r.mu.Unlock()
	}
	if ld.Legs > 0 {
		ld.UDPShare = float64(ld.UDPLegs) / float64(ld.Legs)
	}
	ld.Drops = fwd.Totals.FramesDropped.Load()
	ld.Pauses = fwd.Totals.Pauses.Load()
	ld.PLIp50ms = fwd.PLIRespP(0.5)
	ld.PLIp95ms = fwd.PLIRespP(0.95)
	ld.WriteStls = fwd.Totals.WriteStalls.Load()
	return ld
}

func (n *Node) reserveLeg() bool {
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.legs >= n.cfg.MaxLegs {
		return false
	}
	n.legs++
	return true
}

func (n *Node) releaseLeg() {
	n.mu.Lock()
	n.legs--
	n.mu.Unlock()
}

// Room ist ein Anruf auf diesem Knoten.
type Room struct {
	ref     string
	node    *Node
	lim     Limits
	created time.Time

	mu        sync.Mutex
	peers     map[string]*Peer
	legs      map[string]*Leg
	csrcs     map[uint32]bool
	dominant  *Peer
	prevDom   *Peer
	domSince  int64
	cand      *Peer // moeglicher neuer Hauptsprecher
	candSince int64
	closed    bool
	stop      chan struct{}
	seq       uint64
	lastTick  int64
}

// Ref liefert die Raumkennung.
func (r *Room) Ref() string { return r.ref }

// Peer ist ein Geraet im Raum (ein oder zwei Beine).
type Peer struct {
	ID     string
	CSRC   uint32
	Device Device
	joined uint64 // Reihenfolge des Beitritts

	legs  map[*Leg]struct{}
	srcs  map[*fwd.Source]*source
	audio *source
	cam   map[string]*source // Schicht -> Quelle
	scr   map[string]*source
	scrAt int64 // Beginn der Bildschirmfreigabe

	score     float64 // Sprechaktivitaet (geglaettet)
	lastSpoke int64
}

// source sind Steuerdaten einer Quelle (nur unter Room.mu).
type source struct {
	*fwd.Source
	peer      *Peer
	screen    bool
	leg       *Leg
	lastBytes uint64
	bps       float64
	lastTID   [4]uint32
	fps       [4]float64
	lastKeyRq int64
}

func (r *Room) newCSRC() uint32 {
	var b [4]byte
	for {
		_, _ = rand.Read(b[:])
		c := binary.BigEndian.Uint32(b[:])
		if c != 0 && !r.csrcs[c] {
			r.csrcs[c] = true
			return c
		}
	}
}

// Leg ist ein Bein (eine PeerConnection) im Raum.
type Leg struct {
	Ref    string
	Peer   *Peer
	Path   Path
	Role   Role
	Grant  Grant
	room   *Room
	fl     *fwd.Leg
	outs   []*fwd.Out
	slots  []*slot
	est    estimator
	closed bool
	sig    string // letzte Layout-Meldung
	srcs   []*source

	// Ext liefert eine externe Schaetzung in bit/s (GCC auf UDP), 0 = keine.
	Ext func() int
}

// slot ist ein Empfangsplatz (Steuerdaten).
type slot struct {
	out    *fwd.Out
	class  string
	owner  *Peer
	layer  string
	paused bool
	tid    uint8
}

// FwdLeg liefert das Bein des Weiterleiters (Flusher, Zaehler, Drossel).
func (l *Leg) FwdLeg() *fwd.Leg { return l.fl }

// Outs liefert die Ausgaenge in der Reihenfolge der Empfangsplaetze: Ton,
// Sprecher, Bildschirm, Kacheln.
func (l *Leg) Outs() []*fwd.Out { return l.outs }

// EstimateKbit liefert die aktuelle Downlink-Schaetzung.
func (l *Leg) EstimateKbit() int {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	return int(l.est.bps / 1000)
}

// AddLeg legt ein Bein fuer eine gepruefte Freigabe an. Ein vorhandenes Bein
// mit derselben leg_ref wird ersetzt (Config.OnLegClosed).
func (r *Room) AddLeg(g Grant, path Path, want Want) (*Leg, error) {
	if !r.node.reserveLeg() {
		return nil, ErrFull
	}
	var replaced *Leg
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		r.node.releaseLeg()
		return nil, ErrClosed
	}
	if old := r.legs[g.Leg]; old != nil {
		replaced = old
		r.removeLegLocked(old)
	}
	p := r.peers[g.Peer]
	if p == nil {
		if len(r.peers) >= r.lim.MaxPeers {
			r.mu.Unlock()
			r.node.releaseLeg()
			if replaced != nil {
				r.finishLeg(replaced, "ersetzt")
			}
			return nil, ErrFull
		}
		r.seq++
		p = &Peer{ID: g.Peer, CSRC: r.newCSRC(), Device: g.Device, joined: r.seq, legs: map[*Leg]struct{}{},
			srcs: map[*fwd.Source]*source{}, cam: map[string]*source{}, scr: map[string]*source{}}
		r.peers[g.Peer] = p
	}
	l := &Leg{Ref: g.Leg, Peer: p, Path: path, Role: g.Role, Grant: g, room: r, fl: fwd.NewLeg()}
	cfg := r.node.cfg
	l.est.init(float64(cfg.StartKbit)*1000, float64(cfg.MaxDownKbit)*1000)
	if g.Role.audio() {
		na := want.Audio
		if na > r.lim.Audio {
			na = r.lim.Audio
		}
		for i := 0; i < na; i++ {
			l.addSlot("audio", fwd.Audio, fwd.ClassAudio)
		}
	}
	if g.Role.video() {
		if want.Speaker {
			l.addSlot("speaker", fwd.Video, fwd.ClassSpeaker)
		}
		if want.Screen {
			l.addSlot("screen", fwd.Video, fwd.ClassScreen)
		}
		nt := want.Tiles
		if ln := r.lim.LastN(g.Device); nt > ln {
			nt = ln
		}
		for i := 0; i < nt; i++ {
			l.addSlot("tile", fwd.Video, fwd.ClassTile)
		}
	}
	p.legs[l] = struct{}{}
	r.legs[g.Leg] = l
	r.mu.Unlock()
	if replaced != nil {
		r.finishLeg(replaced, "ersetzt")
	}
	return l, nil
}

func (l *Leg) addSlot(class string, kind fwd.Kind, c fwd.Class) {
	id := class[:1] + string(rune('0'+len(l.outs)%10))
	o := l.fl.NewOut(id, "", kind, c)
	if kind == fwd.Video {
		o.SetMaxTID(3)
	}
	l.outs = append(l.outs, o)
	l.slots = append(l.slots, &slot{out: o, class: class, tid: 3})
}

// AddSource meldet eine eingehende Spur des Beins: kind "audio"/"video",
// layer "a" bzw. Simulcast-Schicht "l"/"m"/"h", screen fuer den Bildschirm.
// Die Rechte aus dem Token werden geprueft.
func (l *Leg) AddSource(kind fwd.Kind, layer string, screen bool, ssrc uint32, levelExt uint8, pli func(uint32)) (*fwd.Source, error) {
	r := l.room
	r.mu.Lock()
	defer r.mu.Unlock()
	if l.closed {
		return nil, ErrClosed
	}
	g := l.Grant
	switch {
	case kind == fwd.Audio && (g.Pub&PubAudio == 0 || !g.Role.audio()),
		kind == fwd.Video && !screen && (g.Pub&PubVideo == 0 || !g.Role.video()),
		kind == fwd.Video && screen && (g.Pub&PubScreen == 0 || !g.Role.video()):
		return nil, ErrNotAllowed
	}
	p := l.Peer
	s := &fwd.Source{Owner: p.ID, Layer: layer, Kind: kind, SSRC: ssrc, CSRC: p.CSRC, PLI: pli, LevelExt: levelExt}
	if kind == fwd.Audio {
		s.Clock = 48000
	} else {
		s.Clock = 90000
	}
	st := &source{Source: s, peer: p, screen: screen, leg: l}
	p.srcs[s] = st
	l.srcs = append(l.srcs, st)
	switch {
	case kind == fwd.Audio:
		if p.audio != nil {
			r.dropSourceLocked(p.audio)
		}
		p.audio = st
	case screen:
		if old := p.scr[layer]; old != nil {
			r.dropSourceLocked(old)
		}
		if len(p.scr) == 0 {
			p.scrAt = fwd.Now()
		}
		p.scr[layer] = st
	default:
		if old := p.cam[layer]; old != nil {
			r.dropSourceLocked(old)
		}
		p.cam[layer] = st
	}
	return s, nil
}

// RemoveSource meldet eine beendete Spur ab.
func (r *Room) RemoveSource(s *fwd.Source) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, p := range r.peers {
		if st := p.srcs[s]; st != nil {
			r.dropSourceLocked(st)
			return
		}
	}
}

func (r *Room) dropSourceLocked(st *source) {
	p := st.peer
	delete(p.srcs, st.Source)
	if p.audio == st {
		p.audio = nil
	}
	for k, v := range p.cam {
		if v == st {
			delete(p.cam, k)
		}
	}
	for k, v := range p.scr {
		if v == st {
			delete(p.scr, k)
		}
	}
	if st.leg != nil {
		for i, x := range st.leg.srcs {
			if x == st {
				st.leg.srcs = append(st.leg.srcs[:i], st.leg.srcs[i+1:]...)
				break
			}
		}
	}
	for _, l := range r.legs {
		for _, o := range l.outs {
			o.Forget(st.Source)
		}
	}
}

// RemoveLeg entfernt ein Bein (Saga: Teilnehmende entfernen, Geraet legt auf).
func (r *Room) RemoveLeg(ref string) bool {
	r.mu.Lock()
	l := r.legs[ref]
	if l != nil {
		r.removeLegLocked(l)
	}
	r.mu.Unlock()
	if l != nil {
		r.finishLeg(l, "entfernt")
	}
	return l != nil
}

// Close entfernt das Bein (vom Aufrufer, z. B. PeerConnection zu).
func (l *Leg) Close() {
	r := l.room
	r.mu.Lock()
	if l.closed {
		r.mu.Unlock()
		return
	}
	r.removeLegLocked(l)
	r.mu.Unlock()
	l.fl.Close()
	r.node.releaseLeg()
}

// RemovePeer entfernt alle Beine eines Geraets.
func (r *Room) RemovePeer(peer string) int {
	r.mu.Lock()
	p := r.peers[peer]
	var ls []*Leg
	if p != nil {
		for l := range p.legs {
			ls = append(ls, l)
		}
		for _, l := range ls {
			r.removeLegLocked(l)
		}
	}
	r.mu.Unlock()
	for _, l := range ls {
		r.finishLeg(l, "entfernt")
	}
	return len(ls)
}

func (r *Room) removeLegLocked(l *Leg) {
	if l.closed {
		return
	}
	l.closed = true
	if r.legs[l.Ref] == l {
		delete(r.legs, l.Ref)
	}
	for _, st := range append([]*source(nil), l.srcs...) {
		r.dropSourceLocked(st)
	}
	for _, o := range l.outs {
		o.Detach()
	}
	p := l.Peer
	delete(p.legs, l)
	if len(p.legs) == 0 {
		delete(r.peers, p.ID)
		delete(r.csrcs, p.CSRC)
		if r.dominant == p {
			r.dominant = nil
		}
		if r.prevDom == p {
			r.prevDom = nil
		}
		if r.cand == p {
			r.cand = nil
		}
	}
}

// finishLeg schliesst ein vom Knoten beendetes Bein ausserhalb der Sperre.
func (r *Room) finishLeg(l *Leg, why string) {
	l.fl.Close()
	r.node.releaseLeg()
	if f := r.node.cfg.OnLegClosed; f != nil {
		f(l, why)
	}
}

func (r *Room) close(why string) {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	r.closed = true
	close(r.stop)
	var ls []*Leg
	for _, l := range r.legs {
		ls = append(ls, l)
	}
	for _, l := range ls {
		r.removeLegLocked(l)
	}
	r.mu.Unlock()
	for _, l := range ls {
		r.finishLeg(l, why)
	}
}

// Peers liefert die Kennungen und CSRCs der Teilnehmenden (Beitrittsfolge).
func (r *Room) Peers() map[string]uint32 {
	r.mu.Lock()
	defer r.mu.Unlock()
	m := make(map[string]uint32, len(r.peers))
	for id, p := range r.peers {
		m[id] = p.CSRC
	}
	return m
}

// Layout liefert die aktuelle Belegung eines Beins.
func (l *Leg) Layout() []Slot {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	return l.layoutLocked()
}

func (l *Leg) layoutLocked() []Slot {
	out := make([]Slot, len(l.slots))
	for i, s := range l.slots {
		out[i] = Slot{Index: i, Class: s.class, Layer: s.layer, MaxTID: s.tid, Paused: s.paused}
		if s.owner != nil {
			out[i].Peer, out[i].CSRC = s.owner.ID, s.owner.CSRC
		}
	}
	return out
}

// sortedPeers liefert die Teilnehmenden in Beitrittsfolge.
func (r *Room) sortedPeers() []*Peer {
	ps := make([]*Peer, 0, len(r.peers))
	for _, p := range r.peers {
		ps = append(ps, p)
	}
	sort.Slice(ps, func(i, j int) bool { return ps[i].joined < ps[j].joined })
	return ps
}
