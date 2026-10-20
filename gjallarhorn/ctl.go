package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Steuerkanal horn-ctl/1 (Konzept v2 §2.4, Paket P3): Der Knoten haelt AUSGEHEND eine WebSocket-Verbindung zu Saga
// (HORN_SAGA_CTL, eingebauter Knoten: ws://127.0.0.1:<intern>/api/horn/ctl). Ablauf:
//
//	Saga -> {"t":"challenge","nonce","proto":"horn-ctl/1"}
//	Knoten -> {"t":"hello","proto","name","url","pub","sig","version","udp","udp_check","cores","builtin"}
//	          sig = Ed25519(knoten_sk, "horn-ctl/1 hello\n" + nonce)
//	Saga -> {"t":"welcome","n":0,"node_id","saga_pub","origin","rooms":[call_ref...]}
//
// Danach traegt jede Nachricht eine Folgenummer n (je Richtung ab 1, lueckenlos; sonst Verbindung zu und neu).
// Saga -> Knoten: open {call_ref, max_peers}, close {call_ref}, kick {call_ref, peer_ref}, ping.
// Knoten -> Saga: load (alle 5 s, Lastmeldung §2.6), closed {call_ref, why}, pong.
// Eingebauter Knoten: zusaetzlich das gemeinsame Geheimnis HORN_CTL_KEY (setzt saga-media) im Kopf X-Saga-Horn-Key.

const ctlProto = "horn-ctl/1"

type ctlMsg struct {
	T        string          `json:"t"`
	N        int64           `json:"n"`
	Nonce    string          `json:"nonce,omitempty"`
	Proto    string          `json:"proto,omitempty"`
	NodeID   string          `json:"node_id,omitempty"`
	SagaPub  string          `json:"saga_pub,omitempty"`
	Origin   string          `json:"origin,omitempty"`
	Rooms    []string        `json:"rooms,omitempty"`
	CallRef  string          `json:"call_ref,omitempty"`
	PeerRef  string          `json:"peer_ref,omitempty"`
	MaxPeers int             `json:"max_peers,omitempty"`
	Load     json.RawMessage `json:"load,omitempty"`
}

// ctlClient haelt die Verbindung zu Saga.
type ctlClient struct {
	s      *server
	url    string
	key    string
	sk     ed25519.PrivateKey
	mu     sync.Mutex
	conn   *websocket.Conn
	out    int64 // letzte gesendete Folgenummer
	nodeID string
	up     bool

	lastCPU  int64
	lastAt   time.Time
	lastDrop uint64
}

// loadNodeKey liest bzw. erzeugt den Ed25519-Knotenschluessel (HORN_DATA/node.key, nur der Seed, 0600).
func loadNodeKey(dir string) (ed25519.PrivateKey, error) {
	p := filepath.Join(dir, "node.key")
	if b, err := os.ReadFile(p); err == nil {
		seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(b)))
		if err != nil || len(seed) != ed25519.SeedSize {
			return nil, fmt.Errorf("%s: ungueltig", p)
		}
		return ed25519.NewKeyFromSeed(seed), nil
	}
	_, sk, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	if err := os.WriteFile(p, []byte(base64.StdEncoding.EncodeToString(sk.Seed())+"\n"), 0o600); err != nil {
		return nil, err
	}
	return sk, nil
}

func newCtl(s *server, url, key string) (*ctlClient, error) {
	sk, err := loadNodeKey(s.cfg.Data)
	if err != nil {
		return nil, fmt.Errorf("knotenschluessel: %w", err)
	}
	return &ctlClient{s: s, url: url, key: key, sk: sk}, nil
}

// run verbindet und haelt die Verbindung (Neuversuch mit Backoff 1 ... 30 s).
func (c *ctlClient) run() {
	back := time.Second
	for {
		t0 := time.Now()
		err := c.session()
		if c.s.closing.Load() {
			return
		}
		if time.Since(t0) > time.Minute {
			back = time.Second
		}
		log.Printf("steuerung: getrennt (%v), neuer versuch in %v", err, back)
		time.Sleep(back)
		if back *= 2; back > 30*time.Second {
			back = 30 * time.Second
		}
	}
}

func (c *ctlClient) session() error {
	h := http.Header{}
	h.Set("X-Saga", "1")
	if c.key != "" {
		h.Set("X-Saga-Horn-Key", c.key)
	}
	d := websocket.Dialer{HandshakeTimeout: 10 * time.Second, Proxy: nil}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	conn, resp, err := d.DialContext(ctx, c.url, h)
	cancel()
	if err != nil {
		if resp != nil {
			return fmt.Errorf("%w (http %d)", err, resp.StatusCode)
		}
		return err
	}
	defer conn.Close()
	conn.SetReadLimit(256 << 10)
	_ = conn.SetReadDeadline(time.Now().Add(15 * time.Second))
	var ch ctlMsg
	if err := conn.ReadJSON(&ch); err != nil || ch.T != "challenge" || ch.Proto != ctlProto || len(ch.Nonce) < 16 {
		return errors.New("keine gueltige challenge")
	}
	uc := c.s.UDPCheck()
	hello := map[string]any{"t": "hello", "proto": ctlProto, "name": c.s.cfg.Name, "url": c.s.publicURL(), "version": version,
		"pub": base64.StdEncoding.EncodeToString(c.sk.Public().(ed25519.PublicKey)),
		"sig": base64.StdEncoding.EncodeToString(ed25519.Sign(c.sk, []byte(ctlProto+" hello\n"+ch.Nonce))),
		"udp": c.s.sfuUDP != nil && uc.State == "ok", "udp_check": uc.State, "cores": runtime.GOMAXPROCS(0),
		"builtin": c.key != "", "max_legs": c.s.cfg.MaxLegs}
	if err := conn.WriteJSON(hello); err != nil {
		return err
	}
	var w ctlMsg
	if err := conn.ReadJSON(&w); err != nil || w.T != "welcome" {
		return errors.New("kein welcome")
	}
	pub, err := base64.StdEncoding.DecodeString(w.SagaPub)
	if err != nil || len(pub) != ed25519.PublicKeySize {
		return errors.New("saga_pub ungueltig")
	}
	c.s.verifier.SetKey(pub)
	if w.Origin != "" {
		c.s.setOrigin(w.Origin)
	}
	c.mu.Lock()
	c.conn, c.out, c.nodeID, c.up = conn, 0, w.NodeID, true
	c.mu.Unlock()
	defer func() {
		c.mu.Lock()
		c.conn, c.up = nil, false
		c.mu.Unlock()
	}()
	log.Printf("steuerung: verbunden (knoten %s, %d laufende anrufe)", w.NodeID, len(w.Rooms))
	c.s.syncRooms(w.Rooms)
	stop := make(chan struct{})
	defer close(stop)
	go c.loadLoop(stop)
	in := int64(0)
	for {
		_ = conn.SetReadDeadline(time.Now().Add(45 * time.Second))
		var m ctlMsg
		if err := conn.ReadJSON(&m); err != nil {
			return err
		}
		if m.N != in+1 {
			return fmt.Errorf("folgenummer %d statt %d", m.N, in+1)
		}
		in = m.N
		switch m.T {
		case "open":
			if validRef(m.CallRef) {
				var lim *sfu.Limits
				if m.MaxPeers > 0 {
					lim = &sfu.Limits{MaxPeers: m.MaxPeers}
				}
				c.s.openRoom(m.CallRef, lim)
			}
		case "close":
			if validRef(m.CallRef) {
				c.s.closeRoom(m.CallRef, "saga")
			}
		case "kick":
			if validRef(m.CallRef) && validRef(m.PeerRef) {
				c.s.kick(m.CallRef, m.PeerRef)
			}
		case "ping":
			c.send(map[string]any{"t": "pong"})
		}
	}
}

// send schickt eine Nachricht mit Folgenummer (ohne Verbindung: verworfen).
func (c *ctlClient) send(m map[string]any) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.conn == nil {
		return false
	}
	c.out++
	m["n"] = c.out
	_ = c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
	if err := c.conn.WriteJSON(m); err != nil {
		_ = c.conn.Close()
		return false
	}
	return true
}

func (c *ctlClient) loadLoop(stop chan struct{}) {
	c.sendLoad()
	t := time.NewTicker(5 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			c.sendLoad()
		}
	}
}

// sendLoad: Lastmeldung (§2.6, etwa 200 Byte).
func (c *ctlClient) sendLoad() {
	ld := c.s.node.Load()
	var ru syscall.Rusage
	_ = syscall.Getrusage(syscall.RUSAGE_SELF, &ru)
	cpuNs := ru.Utime.Nano() + ru.Stime.Nano()
	now := time.Now()
	cpu := 0.0
	if !c.lastAt.IsZero() {
		cpu = float64(cpuNs-c.lastCPU) / float64(now.Sub(c.lastAt).Nanoseconds()) / float64(runtime.GOMAXPROCS(0))
	}
	drops := 0.0
	if !c.lastAt.IsZero() && ld.Drops >= c.lastDrop {
		drops = float64(ld.Drops-c.lastDrop) / now.Sub(c.lastAt).Seconds()
	}
	c.lastCPU, c.lastAt, c.lastDrop = cpuNs, now, ld.Drops
	state := "ok"
	if c.s.cfg.MaxLegs > 0 && ld.Legs*10 >= c.s.cfg.MaxLegs*9 {
		state = "voll"
	}
	c.send(map[string]any{"t": "load", "load": map[string]any{
		"cpu": round3(cpu), "mem": readRSS(), "legs": ld.Legs, "calls": ld.Rooms, "peers": ld.Peers, "udp_share": round3(ld.UDPShare),
		"fwd_p95_us": round3(ld.FwdP95us), "drops": round3(drops), "down_kbit": ld.DownKbit, "max_legs": c.s.cfg.MaxLegs, "state": state}})
}

func round3(x float64) float64 { return float64(int64(x*1000+0.5)) / 1000 }

// ---------- Raeume (Steuerung) ----------

// graves: geschlossene Raeume bleiben gesperrt, solange noch Token dafuer gueltig sein koennen.
const graveFor = 3 * time.Minute

func (s *server) openRoom(ref string, lim *sfu.Limits) {
	s.mu.Lock()
	delete(s.graves, ref)
	s.mu.Unlock()
	_, _ = s.node.CreateRoom(ref, lim)
}

func (s *server) closeRoom(ref, why string) {
	s.mu.Lock()
	s.graves[ref] = time.Now().Add(graveFor)
	s.mu.Unlock()
	if s.node.CloseRoom(ref) == nil && why != "saga" && s.ctl != nil {
		s.ctl.send(map[string]any{"t": "closed", "call_ref": ref, "why": why})
	}
}

func (s *server) kick(room, peer string) {
	s.mu.Lock()
	s.kicked[room+"|"+peer] = time.Now().Add(graveFor)
	s.mu.Unlock()
	if r := s.node.Room(room); r != nil {
		r.RemovePeer(peer)
	}
}

// syncRooms: nach (Neu-)Verbindung Raeume schliessen, die Saga nicht mehr kennt.
func (s *server) syncRooms(keep []string) {
	k := map[string]bool{}
	for _, r := range keep {
		k[r] = true
	}
	for _, ref := range s.node.RoomRefs() {
		if !k[ref] {
			s.closeRoom(ref, "abgleich")
		}
	}
}

// admit: Token pruefen; einen noch unbekannten Raum legt ein gueltiges Saga-Token selbst an (die Anlage ueber die
// Steuerung kann knapp nach dem ersten Bein kommen), ausser er wurde gerade geschlossen. Entfernte Teilnehmende kommen
// mit alten Token nicht zurueck.
func (s *server) admit(tok string, p sfu.Path) (sfu.Grant, *sfu.Room, error) {
	g, r, err := s.node.Admit(tok, p)
	if errors.Is(err, sfu.ErrNoRoom) && s.ctl != nil {
		s.mu.Lock()
		dead := time.Now().Before(s.graves[g.Room])
		s.mu.Unlock()
		if !dead {
			_, _ = s.node.CreateRoom(g.Room, nil)
			g, r, err = s.node.Admit(tok, p)
		}
	}
	if err != nil {
		return g, r, err
	}
	s.mu.Lock()
	banned := time.Now().Before(s.kicked[g.Room+"|"+g.Peer])
	s.mu.Unlock()
	if banned {
		return sfu.Grant{}, nil, fmt.Errorf("%w: entfernt", errAuth)
	}
	return g, r, nil
}

// roomReaper: leere Raeume nach 2 min schliessen, abgelaufene Sperren vergessen.
func (s *server) roomReaper() {
	empty := map[string]time.Time{}
	for range time.Tick(10 * time.Second) {
		if s.closing.Load() {
			return
		}
		now := time.Now()
		seen := map[string]bool{}
		for _, ref := range s.node.RoomRefs() {
			seen[ref] = true
			r := s.node.Room(ref)
			if r == nil || r.LegCount() > 0 {
				delete(empty, ref)
				continue
			}
			if t, ok := empty[ref]; !ok {
				empty[ref] = now
			} else if now.Sub(t) > 2*time.Minute && now.Sub(r.Created()) > 2*time.Minute {
				delete(empty, ref)
				s.closeRoom(ref, "leer")
			}
		}
		for ref := range empty {
			if !seen[ref] {
				delete(empty, ref)
			}
		}
		s.mu.Lock()
		for k, t := range s.graves {
			if now.After(t) {
				delete(s.graves, k)
			}
		}
		for k, t := range s.kicked {
			if now.After(t) {
				delete(s.kicked, k)
			}
		}
		s.mu.Unlock()
	}
}

// ---------- Herkunft (CORS, WSS-Probe) ----------

func (s *server) setOrigin(o string) {
	o = strings.TrimRight(strings.TrimSpace(o), "/")
	s.origin.Store(&o)
}

func (s *server) originOK(o string) bool {
	if o == "" {
		return false
	}
	if p := s.origin.Load(); p != nil && *p != "" && o == *p {
		return true
	}
	return false
}

// cors: Saga-Seiten (andere Herkunft) sprechen den Knoten direkt an (/api/sfu/..., /probe). Erlaubt ist nur die
// Herkunft der eigenen Saga (welcome bzw. HORN_SAGA_ORIGIN). Das Token bleibt die eigentliche Berechtigung.
func (s *server) cors(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if o := r.Header.Get("Origin"); o != "" && strings.HasPrefix(r.URL.Path, "/api/sfu/") && s.originOK(o) {
			hd := w.Header()
			hd.Set("Access-Control-Allow-Origin", o)
			hd.Add("Vary", "Origin")
			if r.Method == http.MethodOptions {
				hd.Set("Access-Control-Allow-Methods", "GET, POST, DELETE")
				hd.Set("Access-Control-Allow-Headers", "Content-Type")
				hd.Set("Access-Control-Max-Age", "600")
				w.WriteHeader(http.StatusNoContent)
				return
			}
		}
		h.ServeHTTP(w, r)
	})
}

// publicURL: Adresse fuer Geraete (HORN_PUBLIC_URL, sonst https://NAME[:Port]).
func (s *server) publicURL() string {
	if u := strings.TrimSpace(os.Getenv("HORN_PUBLIC_URL")); u != "" {
		return strings.TrimRight(u, "/")
	}
	port := s.mux.Addr().Port
	if port == 443 {
		return "https://" + s.cfg.Name
	}
	return fmt.Sprintf("https://%s:%d", s.cfg.Name, port)
}
