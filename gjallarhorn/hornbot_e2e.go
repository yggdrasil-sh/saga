package main

// Frame-E2E saga-horn/v1 (docs/HORN-E2E.md) fuer den Mess-Bot horn-bot (2263-2): Sender (versiegeln je codiertem Rahmen)
// und Empfaenger (Schluesselwahl, Ratsche beim ersten Rahmen, Ablauf 3 s, Wiederholungsschutz Fenster 128). Geprueft
// gegen tests/vectors/horn_e2e.json (hornbot_e2e_test.go). Nur fuer Messungen: der Knoten selbst sieht nie Schluessel.

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"sync"
	"time"
)

const (
	e2eFrameSalt   = "saga-horn/v1/frame"
	e2eRatchetSalt = "saga-horn-ratchet"
	e2eTrailer     = 14 // IV 12 || 0x0C || kid
	e2eTag         = 16
	e2eWindow      = 128
	e2eKeep        = 3                       // gehaltene Generationen je Sender
	e2eAhead       = 4                       // Ratsche beim ersten Rahmen: hoechstens so viele Stufen
	e2eGrace       = 3000 * time.Millisecond // aeltere Generationen laufen so lange nach dem ersten neueren Rahmen ab
)

var e2eInfo = string(make([]byte, 128))

// Fehlerarten wie in den Vektoren (cases.expect).
var (
	errE2ECodec  = errors.New("codec")
	errE2EShort  = errors.New("short")
	errE2EFormat = errors.New("format")
	errE2ENoKey  = errors.New("nokey")
	errE2EReplay = errors.New("replay")
	errE2EAuth   = errors.New("auth")
)

// e2eFK: Rahmenschluessel FK = HKDF-SHA256(K, salt frame, info 128 x 0, 16).
func e2eFK(k []byte) []byte {
	fk, err := hkdf.Key(sha256.New, k, []byte(e2eFrameSalt), e2eInfo, 16)
	if err != nil {
		panic(err)
	}
	return fk
}

// e2eRatchet: K' = HKDF-SHA256(K, salt ratchet, info leer, 32).
func e2eRatchet(k []byte) []byte {
	n, err := hkdf.Key(sha256.New, k, []byte(e2eRatchetSalt), "", 32)
	if err != nil {
		panic(err)
	}
	return n
}

func e2eGCM(k []byte) cipher.AEAD {
	b, err := aes.NewCipher(e2eFK(k))
	if err != nil {
		panic(err)
	}
	g, err := cipher.NewGCM(b)
	if err != nil {
		panic(err)
	}
	return g
}

// e2eHeader: Laenge des Klartext-Kopfs (AAD) je Rahmen; video: key = Schluesselbild.
func e2eHeader(video, key bool) int {
	switch {
	case !video:
		return 1
	case key:
		return 10
	}
	return 3
}

// e2eSeal: Kopf || Chiffrat || Tag || IV || 0x0C || kid.
func e2eSeal(g cipher.AEAD, kid uint8, h int, iv [12]byte, frame []byte) []byte {
	out := make([]byte, 0, len(frame)+e2eTag+e2eTrailer)
	out = append(out, frame[:h]...)
	out = g.Seal(out, iv[:], frame[h:], frame[:h])
	out = append(out, iv[:]...)
	return append(out, 0x0c, kid)
}

// --- Sender --------------------------------------------------------------------------------------------------------

type e2eKey struct {
	n int
	k []byte
	g cipher.AEAD
}

// e2eSender: eigener Schluessel des Bots. set setzt einen neuen Schluessel (sofort oder nach after); der Zaehler der IV
// laeuft je (Schluessel, SSRC).
type e2eSender struct {
	mu      sync.Mutex
	cur     *e2eKey
	next    *e2eKey
	at      time.Time
	ctr     map[[2]uint32]uint32 // (n, ssrc) -> Zaehler
	sealed  uint64
	dropped uint64 // ohne Schluessel nicht gesendet
}

func newE2ESender() *e2eSender { return &e2eSender{ctr: map[[2]uint32]uint32{}} }

func (s *e2eSender) set(n int, k []byte, after time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := &e2eKey{n: n, k: append([]byte(nil), k...), g: e2eGCM(k)}
	if after <= 0 || s.cur == nil {
		s.cur, s.next = key, nil
		return
	}
	s.next, s.at = key, time.Now().Add(after)
}

// switchNow: wartenden Schluessel sofort einsetzen (alle haben bestaetigt).
func (s *e2eSender) switchNow() {
	s.mu.Lock()
	if s.next != nil {
		s.cur, s.next = s.next, nil
	}
	s.mu.Unlock()
}

// gen: Generation des gerade benutzten Schluessels (-1 ohne).
func (s *e2eSender) gen() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.next != nil && !time.Now().Before(s.at) {
		s.cur, s.next = s.next, nil
	}
	if s.cur == nil {
		return -1
	}
	return s.cur.n
}

// seal versiegelt einen codierten Rahmen; nil = ohne Schluessel (nicht senden, nie Klartext).
func (s *e2eSender) seal(video, key bool, ssrc, ts uint32, frame []byte) []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.next != nil && !time.Now().Before(s.at) {
		s.cur, s.next = s.next, nil
	}
	h := e2eHeader(video, key)
	if s.cur == nil || len(frame) <= h {
		s.dropped++
		return nil
	}
	c := [2]uint32{uint32(s.cur.n), ssrc}
	var iv [12]byte
	binary.BigEndian.PutUint32(iv[0:], ssrc)
	binary.BigEndian.PutUint32(iv[4:], ts)
	binary.BigEndian.PutUint32(iv[8:], s.ctr[c])
	s.ctr[c]++
	s.sealed++
	return e2eSeal(s.cur.g, uint8(s.cur.n%16), h, iv, frame)
}

func e2eRandKey() []byte {
	k := make([]byte, 32)
	_, _ = rand.Read(k)
	return k
}

// --- Empfaenger ----------------------------------------------------------------------------------------------------

// replayWindow: Fenster 128 ueber den RTP-Zeitstempel der IV (HORN-E2E §3).
type replayWindow struct {
	max  uint32
	w    []uint32
	used bool
}

func (r *replayWindow) check(t uint32) bool {
	if !r.used {
		return true
	}
	d := t - r.max
	if d >= 1 && d < 1<<31 {
		return true
	}
	if d == 0 {
		return false
	}
	oldest := uint32(0)
	for _, v := range r.w {
		if v == t {
			return false
		}
		if a := r.max - v; a > oldest {
			oldest = a
		}
	}
	if len(r.w) < e2eWindow {
		return true
	}
	return r.max-t < oldest
}

func (r *replayWindow) accept(t uint32) {
	if !r.used || (t-r.max >= 1 && t-r.max < 1<<31) {
		r.max = t
	}
	r.used = true
	r.w = append(r.w, t)
	if len(r.w) > e2eWindow {
		r.w = r.w[1:]
	}
}

type e2eGen struct {
	e2eKey
	exp time.Time // Null = laeuft nicht ab
}

// e2ePeer: Zustand eines Senders beim Empfaenger.
type e2ePeer struct {
	s      string
	gens   map[int]*e2eGen
	newest int // neueste gehaltene Generation
	seen   int // neueste Generation, aus der ein Rahmen aufging (-1)
	win    map[[2]uint32]*replayWindow
	firstN map[int]time.Time // erster aufgegangener Rahmen je Generation (Messung fkey-Wechsel)
	last   time.Time         // zuletzt aufgegangener Rahmen (genwait: sieht der Empfaenger diesen Sender?)
}

// e2eRecv: alle Sender eines Bots (from = Kennung am Knoten bzw. Geraet).
type e2eRecv struct {
	mu    sync.Mutex
	peers map[string]*e2ePeer
	now   func() time.Time
	// onGen: erster aufgegangener Rahmen einer neuen Generation eines Senders (Messung)
	onGen func(from string, n int, at time.Time)
}

func newE2ERecv() *e2eRecv {
	return &e2eRecv{peers: map[string]*e2ePeer{}, now: time.Now}
}

// setKey: fkey eines Senders einsetzen (n, K, Sitzung s). Neue Sitzung verwirft alles Alte; n < newest-1 wird ignoriert.
func (r *e2eRecv) setKey(from string, n int, k []byte, s string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	p := r.peers[from]
	if p == nil || p.s != s {
		p = &e2ePeer{s: s, gens: map[int]*e2eGen{}, newest: -1, seen: -1, win: map[[2]uint32]*replayWindow{}, firstN: map[int]time.Time{}}
		r.peers[from] = p
	}
	if p.newest >= 0 && n < p.newest-1 {
		return
	}
	p.gens[n] = &e2eGen{e2eKey: e2eKey{n: n, k: append([]byte(nil), k...), g: e2eGCM(k)}}
	if n > p.newest {
		p.newest = n
	}
	p.prune()
}

func (p *e2ePeer) prune() {
	for n := range p.gens {
		if n < p.newest-(e2eKeep-1) {
			delete(p.gens, n)
		}
	}
}

// open prueft und oeffnet einen Rahmen des Senders from -> Klartext oder Fehler (errE2E*).
func (r *e2eRecv) open(from string, video, key bool, data []byte) ([]byte, error) {
	h := e2eHeader(video, key)
	if len(data) < h+e2eTag+e2eTrailer {
		return nil, errE2EShort
	}
	kid := data[len(data)-1]
	if data[len(data)-2] != 0x0c || kid >= 16 {
		return nil, errE2EFormat
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	now := r.now()
	p := r.peers[from]
	if p == nil {
		return nil, errE2ENoKey
	}
	var g *e2eGen
	for _, x := range p.gens {
		if x.n%16 == int(kid) && (x.exp.IsZero() || !now.After(x.exp)) {
			g = x
			break
		}
	}
	ratcheted := false
	if g == nil {
		j := (int(kid) - p.newest%16 + 16) % 16
		base := p.gens[p.newest]
		if base == nil || j < 1 || j > e2eAhead {
			return nil, errE2ENoKey
		}
		k := base.k
		for i := 0; i < j; i++ {
			k = e2eRatchet(k)
		}
		g = &e2eGen{e2eKey: e2eKey{n: p.newest + j, k: k, g: e2eGCM(k)}}
		ratcheted = true
	}
	ivb := data[len(data)-e2eTrailer : len(data)-2]
	ssrc, ts := binary.BigEndian.Uint32(ivb[0:]), binary.BigEndian.Uint32(ivb[4:])
	wk := [2]uint32{uint32(g.n), ssrc}
	w := p.win[wk]
	if w != nil && !w.check(ts) {
		return nil, errE2EReplay
	}
	plain, err := g.g.Open(append([]byte(nil), data[:h]...), ivb, data[h:len(data)-e2eTrailer], data[:h])
	if err != nil {
		return nil, errE2EAuth
	}
	if w == nil {
		w = &replayWindow{}
		p.win[wk] = w
	}
	w.accept(ts)
	p.last = now
	if ratcheted { // erst einsetzen, wenn ein Rahmen damit aufging
		p.gens[g.n] = g
		p.newest = g.n
		p.prune()
	}
	if g.n > p.seen {
		for _, x := range p.gens {
			if x.n < g.n && x.exp.IsZero() {
				x.exp = now.Add(e2eGrace)
			}
		}
		p.seen = g.n
		if _, ok := p.firstN[g.n]; !ok {
			p.firstN[g.n] = now
			if r.onGen != nil {
				r.onGen(from, g.n, now) // unter der Sperre: Rueckruf darf r nicht benutzen
			}
		}
	}
	return plain, nil
}
