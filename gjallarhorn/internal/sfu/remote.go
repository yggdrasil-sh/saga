package sfu

import (
	"errors"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Kaskade (Paket P9, Konzept v2 §2.4/§2.6) und Kontingente (P10, §2.7/§3.4).
//
// Teilnehmende anderer Knoten kommen ueber eine Bruecke ("Bifroest") in den Raum: Peer.Via = Kennung der Bruecke, ohne
// eigene Beine. Ihre Quellen (RemoteSource) speist die Bruecke mit fertigen RTP-Paketen; sie fliessen nur auf Nachfrage
// (Subscribed). Fuer die Planung (Top-3-Ton, Sprecher, Last-N, Schichtwahl) zaehlen sie wie eigene Teilnehmende: eine
// Quelle ueber eine Bruecke gilt als nutzbar, wenn der andere Knoten sie als aktiv meldet (SetRemoteOn).

// ErrQuota: Kontingent des verbundenen Servers erschoepft bzw. Notaus.
var ErrQuota = errors.New("kontingent erschoepft")

// Quota ist das Kontingent eines verbundenen Servers an diesem Knoten: hoechstens Legs Teilnehmende und Kbit je
// Richtung. Rein: gemeinsamer Eimer fuer alle eingehenden Pakete seiner Teilnehmenden (Bild wird darueber verworfen,
// Ton nie). Raus: die Schaetzung jedes seiner Beine ist auf Kbit / Teilnehmende begrenzt.
type Quota struct {
	Key   string
	peers atomic.Int32

	mu      sync.Mutex
	legs    int
	kbit    int
	tokens  float64
	last    int64
	off     time.Time // Notaus bis
	dropped atomic.Uint64
}

func (q *Quota) set(legs, kbit int) {
	q.mu.Lock()
	if legs > 0 {
		q.legs = legs
	}
	if kbit > 0 {
		q.kbit = kbit
	}
	q.mu.Unlock()
}

// allowIn zaehlt n Byte gegen den Eimer (Rate kbit, Tiefe 0,5 s) und meldet false, wenn er leer ist.
func (q *Quota) allowIn(n int) bool {
	now := fwd.Now()
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.kbit <= 0 {
		return true
	}
	rate := float64(q.kbit) * 1000 / 8 // Byte/s
	if q.last == 0 {
		q.last, q.tokens = now, rate/2
	}
	q.tokens += float64(now-q.last) / 1e9 * rate
	q.last = now
	if q.tokens > rate/2 {
		q.tokens = rate / 2
	}
	if q.tokens < 0 {
		q.dropped.Add(1)
		return false
	}
	q.tokens -= float64(n)
	return true
}

// downShare: hoechste Downlink-Rate (bit/s) je Teilnehmendem dieses Servers.
func (q *Quota) downShare() float64 {
	q.mu.Lock()
	k := q.kbit
	q.mu.Unlock()
	n := q.peers.Load()
	if k <= 0 {
		return 1e12
	}
	if n < 1 {
		n = 1
	}
	return float64(k) * 1000 / float64(n)
}

// QuotaStat ist der Stand eines Kontingents (Lastmeldung, Tests).
type QuotaStat struct {
	Peers   int    `json:"peers"`
	Legs    int    `json:"legs"`
	Kbit    int    `json:"kbit"`
	Dropped uint64 `json:"dropped"`
	Off     bool   `json:"off,omitempty"`
}

// quotaFor liefert das Kontingent aus dem Token (nil ohne) bzw. ErrQuota, wenn es voll oder abgeschaltet ist. Die
// Grenzen kommen mit jedem Token (Saga ist die Instanz); der Knoten setzt sie durch.
func (n *Node) quotaFor(g Grant) (*Quota, error) {
	if g.Quota == "" {
		return nil, nil
	}
	return n.Quota(g.Quota, g.QuotaLegs, g.QuotaKbit, true)
}

// Quota liefert (bzw. legt an) das Kontingent key; join = ein weiterer Teilnehmender soll dazu.
func (n *Node) Quota(key string, legs, kbit int, join bool) (*Quota, error) {
	n.mu.Lock()
	q := n.quotas[key]
	if q == nil {
		q = &Quota{Key: key}
		n.quotas[key] = q
	}
	n.mu.Unlock()
	q.set(legs, kbit)
	q.mu.Lock()
	off, lim := time.Now().Before(q.off), q.legs
	q.mu.Unlock()
	if off {
		return nil, ErrQuota
	}
	if join && lim > 0 && int(q.peers.Load()) >= lim {
		return nil, ErrQuota
	}
	return q, nil
}

// QuotaOff (Notaus): alle Teilnehmenden mit diesem Kontingent sofort entfernen (eigene Beine und ueber Bruecken), neue
// fuer d abweisen. -> Zahl der entfernten Teilnehmenden.
func (n *Node) QuotaOff(key string, d time.Duration) int {
	n.mu.Lock()
	q := n.quotas[key]
	if q == nil {
		q = &Quota{Key: key}
		n.quotas[key] = q
	}
	rs := make([]*Room, 0, len(n.rooms))
	for _, r := range n.rooms {
		rs = append(rs, r)
	}
	n.mu.Unlock()
	q.mu.Lock()
	q.off = time.Now().Add(d)
	q.mu.Unlock()
	cnt := 0
	for _, r := range rs {
		r.mu.Lock()
		var ids []string
		for id, p := range r.peers {
			if p.quota == q {
				ids = append(ids, id)
			}
		}
		r.mu.Unlock()
		for _, id := range ids {
			cnt++
			if r.RemovePeer(id) == 0 {
				r.RemoveRemote(id)
			}
		}
	}
	return cnt
}

// QuotaStats liefert den Stand aller Kontingente.
func (n *Node) QuotaStats() map[string]QuotaStat {
	n.mu.Lock()
	qs := make([]*Quota, 0, len(n.quotas))
	for _, q := range n.quotas {
		qs = append(qs, q)
	}
	n.mu.Unlock()
	out := map[string]QuotaStat{}
	now := time.Now()
	for _, q := range qs {
		q.mu.Lock()
		out[q.Key] = QuotaStat{Peers: int(q.peers.Load()), Legs: q.legs, Kbit: q.kbit, Dropped: q.dropped.Load(), Off: now.Before(q.off)}
		q.mu.Unlock()
	}
	return out
}

// ---------- Teilnehmende ueber Bruecken ----------

// AddRemote nimmt einen Teilnehmenden eines anderen Knotens auf (Bruecke via). false, wenn es ihn hier schon gibt
// (eigenes Geraet beim Wechsel, oder ueber eine andere Bruecke: der erste Weg gewinnt) oder das Kontingent voll ist.
func (r *Room) AddRemote(via, id string, dev Device, q *Quota) bool {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return false
	}
	if p := r.peers[id]; p != nil {
		r.mu.Unlock()
		return p.Via == via
	}
	if q != nil {
		q.mu.Lock()
		lim, off := q.legs, time.Now().Before(q.off)
		q.mu.Unlock()
		if off || (lim > 0 && int(q.peers.Load()) >= lim) {
			r.mu.Unlock()
			return false
		}
		q.peers.Add(1)
	}
	r.seq++
	p := &Peer{ID: id, CSRC: r.newCSRC(), Device: dev, Via: via, joined: r.seq, legs: map[*Leg]struct{}{},
		srcs: map[*fwd.Source]*source{}, cam: map[string]*source{}, scr: map[string]*source{}, quota: q}
	r.peers[id] = p
	notify := r.peersChangedLocked()
	cfg := r.cfg
	r.mu.Unlock()
	if notify && cfg.OnPeers != nil {
		cfg.OnPeers(r.ref)
	}
	return true
}

// RemoveRemote entfernt einen Teilnehmenden, der ueber eine Bruecke kam.
func (r *Room) RemoveRemote(id string) bool {
	r.mu.Lock()
	p := r.peers[id]
	ok := p != nil && p.Via != ""
	if ok {
		r.dropPeerLocked(p)
	}
	notify := ok && r.peersChangedLocked()
	cfg := r.cfg
	r.mu.Unlock()
	if notify && cfg.OnPeers != nil {
		cfg.OnPeers(r.ref)
	}
	return ok
}

// RemoveVia entfernt alle Teilnehmenden einer Bruecke (Bruecke zu).
func (r *Room) RemoveVia(via string) int {
	r.mu.Lock()
	n := 0
	for _, p := range r.peers {
		if p.Via == via {
			r.dropPeerLocked(p)
			n++
		}
	}
	notify := n > 0 && r.peersChangedLocked()
	cfg := r.cfg
	r.mu.Unlock()
	if notify && cfg.OnPeers != nil {
		cfg.OnPeers(r.ref)
	}
	return n
}

// RemoteSource legt eine Quelle eines Teilnehmenden ueber die Bruecke via an (ersetzt dieselbe Schicht). kind Ton
// oder Bild, layer "a" bzw. "l"/"m"/"h". Die Pakete speist die Bruecke mit Source.Inject.
func (r *Room) RemoteSource(via, id string, kind fwd.Kind, layer string, screen bool, levelExt uint8, pli func(uint32)) *fwd.Source {
	r.mu.Lock()
	defer r.mu.Unlock()
	p := r.peers[id]
	if p == nil || p.Via != via {
		return nil
	}
	s := &fwd.Source{Owner: p.ID, Layer: layer, Kind: kind, CSRC: p.CSRC, PLI: pli, LevelExt: levelExt}
	if kind == fwd.Audio {
		s.Clock = 48000
	} else {
		s.Clock = 90000
	}
	if p.quota != nil {
		s.Gate = p.quota.allowIn
	}
	st := &source{Source: s, peer: p, screen: screen, remote: true, on: true}
	p.srcs[s] = st
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
	return s
}

// DropRemoteSource entfernt eine Quelle ueber die Bruecke.
func (r *Room) DropRemoteSource(s *fwd.Source) { r.RemoveSource(s) }

// SetRemote meldet den Stand eines Teilnehmenden laut anderem Knoten: letzte Sprache (vor ago) und welche seiner
// Quellen aktiv sind.
func (r *Room) SetRemote(id string, spokeAgo time.Duration, on map[*fwd.Source]bool) {
	now := fwd.Now()
	r.mu.Lock()
	defer r.mu.Unlock()
	p := r.peers[id]
	if p == nil || p.Via == "" {
		return
	}
	if spokeAgo >= 0 {
		if t := now - int64(spokeAgo); t > p.spoke {
			p.spoke = t
		}
	}
	for s, v := range on {
		if st := p.srcs[s]; st != nil {
			st.on = v
		}
	}
}

// PeerView ist der Blick einer Bruecke auf einen Teilnehmenden (Kopie, ausserhalb der Sperre lesbar).
type PeerView struct {
	ID     string
	Device Device
	Via    string
	Score  float64
	Spoke  int64 // fwd.Now der letzten Sprache (0 = nie)
	Audio  *fwd.Source
	Srcs   []SourceView
}

// SourceView ist eine Quelle im Blick einer Bruecke.
type SourceView struct {
	Src    *fwd.Source
	Screen bool
	On     bool
}

// View liefert alle Teilnehmenden ausser denen, die ueber die Bruecke exclude kommen (keine Schleife zurueck), in
// Beitrittsfolge.
func (r *Room) View(exclude string) []PeerView {
	now := fwd.Now()
	r.mu.Lock()
	defer r.mu.Unlock()
	ps := r.sortedPeers()
	out := make([]PeerView, 0, len(ps))
	for _, p := range ps {
		if exclude != "" && p.Via == exclude {
			continue
		}
		v := PeerView{ID: p.ID, Device: p.Device, Via: p.Via, Score: p.score, Spoke: p.lastSpoke}
		if p.audio != nil {
			v.Audio = p.audio.Source
		}
		for _, st := range p.srcs {
			v.Srcs = append(v.Srcs, SourceView{Src: st.Source, Screen: st.screen, On: st.live(now)})
		}
		sort.Slice(v.Srcs, func(i, j int) bool {
			a, b := v.Srcs[i], v.Srcs[j]
			if a.Src.Kind != b.Src.Kind {
				return a.Src.Kind < b.Src.Kind
			}
			if a.Screen != b.Screen {
				return !a.Screen
			}
			return a.Src.Layer < b.Src.Layer
		})
		out = append(out, v)
	}
	return out
}

// LocalPeers zaehlt die Teilnehmenden mit eigenem Bein an diesem Knoten.
func (r *Room) LocalPeers() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, p := range r.peers {
		if p.Via == "" {
			n++
		}
	}
	return n
}

// Closed meldet, ob der Raum geschlossen ist.
func (r *Room) Closed() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.closed
}
