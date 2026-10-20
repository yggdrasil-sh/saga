package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Ereigniskanal je Bein (Paket P5, Web-Client): GET /api/sfu/leg/{id}/events (WSS, Herkunft wie /probe).
// Der Knoten schiebt dem Geraet, was es sonst alle 1,5 s abfragen muesste, sofort zu:
//
//	{t:"hello", leg, peers:{peer_ref:csrc}, slots, est_kbit, state}   nach dem Verbinden
//	{t:"peers", peers}            Teilnehmende des Raums geaendert (Beitritt sofort, also VOR dem ersten Rahmen)
//	{t:"layout", slots, est_kbit} Belegung der Plaetze dieses Beins geaendert
//	{t:"state", state}            Knoten ok | draining (Leeren: Geraete wechseln make-before-break den Knoten)
//	{t:"closed", why}             Bein vom Knoten beendet (ersetzt, entfernt, Raum geschlossen)
//	{t:"pong", n}                 Antwort auf {t:"ping", n} des Geraets (RTT ueber denselben TLS-Weg, Live-Umschalten)
//
// Die Bein-Kennung (8 Byte Zufall, nur im POST /api/sfu/leg an das Geraet) ist die Berechtigung, wie bei
// GET /api/sfu/leg/{id}. Inhalte: nur Kennungen und CSRCs des Raums, keine Adressen.

const (
	evQueue    = 32 // Nachrichten je Verbindung; voll = Geraet liest nicht, Verbindung wird geschlossen
	evPingMin  = 150 * time.Millisecond
	evLegCheck = 2 * time.Second // Bein noch da? (falls das Schliessen nicht ueber OnLegClosed kam)
)

type evConn struct {
	room, ref string
	out       chan []byte
	done      chan struct{}
	once      sync.Once
}

func (c *evConn) send(b []byte) {
	select {
	case <-c.done:
	case c.out <- b:
	default:
		c.stop()
	}
}

func (c *evConn) stop() { c.once.Do(func() { close(c.done) }) }

// evHub: Ereignisverbindungen je Raum und je Bein (room|leg_ref).
type evHub struct {
	mu     sync.Mutex
	byLeg  map[string]*evConn
	byRoom map[string]map[*evConn]struct{}
}

func (h *evHub) add(c *evConn) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.byLeg == nil {
		h.byLeg, h.byRoom = map[string]*evConn{}, map[string]map[*evConn]struct{}{}
	}
	k := c.room + "|" + c.ref
	if old := h.byLeg[k]; old != nil { // zweite Verbindung fuer dasselbe Bein ersetzt die erste
		old.stop()
		delete(h.byRoom[old.room], old)
	}
	h.byLeg[k] = c
	if h.byRoom[c.room] == nil {
		h.byRoom[c.room] = map[*evConn]struct{}{}
	}
	h.byRoom[c.room][c] = struct{}{}
}

func (h *evHub) remove(c *evConn) {
	h.mu.Lock()
	defer h.mu.Unlock()
	k := c.room + "|" + c.ref
	if h.byLeg[k] == c {
		delete(h.byLeg, k)
	}
	if m := h.byRoom[c.room]; m != nil {
		delete(m, c)
		if len(m) == 0 {
			delete(h.byRoom, c.room)
		}
	}
}

func (h *evHub) leg(room, ref string) *evConn {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.byLeg[room+"|"+ref]
}

func (h *evHub) room(room string) []*evConn {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]*evConn, 0, len(h.byRoom[room]))
	for c := range h.byRoom[room] {
		out = append(out, c)
	}
	return out
}

func (h *evHub) all() []*evConn {
	h.mu.Lock()
	defer h.mu.Unlock()
	out := make([]*evConn, 0, len(h.byLeg))
	for _, c := range h.byLeg {
		out = append(out, c)
	}
	return out
}

func evMsg(v map[string]any) []byte {
	b, _ := json.Marshal(v)
	return b
}

// nodeState: "draining" waehrend des Leerens, sonst "ok".
func (s *server) nodeState() string {
	if s.draining.Load() {
		return "draining"
	}
	return "ok"
}

// setDraining schaltet das Leeren um (keine neuen Beine; Geraete wechseln den Knoten). Ausloeser: SIGUSR1/SIGUSR2
// (Betrieb und Tests); der Steuerbefehl "Leeren" aus dem Admin-Bereich folgt mit P6.
func (s *server) setDraining(on bool) {
	if s.draining.Swap(on) == on {
		return
	}
	log.Printf("knoten: %s", map[bool]string{true: "wird geleert (draining)", false: "nimmt wieder Beine an"}[on])
	b := evMsg(map[string]any{"t": "state", "state": s.nodeState()})
	for _, c := range s.ev.all() {
		c.send(b)
	}
}

// onLayout (sfu.Config.OnLayout): Belegung an das Geraet des Beins.
func (s *server) onLayout(ev sfu.LayoutEvent) {
	if c := s.ev.leg(ev.Room, ev.Leg); c != nil {
		c.send(evMsg(map[string]any{"t": "layout", "slots": ev.Slots, "est_kbit": ev.EstKbit}))
	}
}

// onPeers (sfu.Config.OnPeers): Teilnehmende des Raums an alle seine Geraete.
func (s *server) onPeers(room string) {
	cs := s.ev.room(room)
	if len(cs) == 0 {
		return
	}
	r := s.node.Room(room)
	if r == nil {
		return
	}
	b := evMsg(map[string]any{"t": "peers", "peers": r.Peers()})
	for _, c := range cs {
		c.send(b)
	}
}

// evLegClosed: der Knoten hat ein Bein beendet -> Grund an das Geraet, dann Verbindung zu.
func (s *server) evLegClosed(sl *sfu.Leg, why string) {
	if c := s.ev.leg(sl.Grant.Room, sl.Ref); c != nil {
		c.send(evMsg(map[string]any{"t": "closed", "why": why}))
		time.AfterFunc(200*time.Millisecond, c.stop)
	}
}

// apiSFUEvents: GET /api/sfu/leg/{id}/events.
func (s *server) apiSFUEvents(w http.ResponseWriter, r *http.Request) {
	l := s.getLeg(r.PathValue("id"))
	if l == nil {
		writeJSON(w, 404, map[string]string{"error": "unbekannt"})
		return
	}
	l.mu.Lock()
	sl := l.sl
	l.mu.Unlock()
	if sl == nil {
		writeJSON(w, 404, map[string]string{"error": "unbekannt"})
		return
	}
	up := websocket.Upgrader{ReadBufferSize: 512, WriteBufferSize: 4096, CheckOrigin: func(r *http.Request) bool {
		o := r.Header.Get("Origin")
		return o == "" || strings.TrimPrefix(o, "https://") == r.Host || s.originOK(o)
	}}
	ws, err := up.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	c := &evConn{room: sl.Grant.Room, ref: sl.Ref, out: make(chan []byte, evQueue), done: make(chan struct{})}
	s.ev.add(c)
	defer func() { s.ev.remove(c); c.stop(); _ = ws.Close() }()
	peers := map[string]uint32{}
	if rm := s.node.Room(c.room); rm != nil {
		peers = rm.Peers()
	}
	c.send(evMsg(map[string]any{"t": "hello", "leg": l.id, "peers": peers, "slots": sl.Layout(), "est_kbit": sl.EstimateKbit(),
		"state": s.nodeState()}))
	ws.SetReadLimit(256)
	go func() { // Leser: ping -> pong (hoechstens alle 150 ms), sonst nichts
		defer c.stop()
		var last time.Time
		for {
			_ = ws.SetReadDeadline(time.Now().Add(90 * time.Second))
			_, b, err := ws.ReadMessage()
			if err != nil {
				return
			}
			var m struct {
				T string `json:"t"`
				N int64  `json:"n"`
			}
			if json.Unmarshal(b, &m) != nil || m.T != "ping" || time.Since(last) < evPingMin {
				continue
			}
			last = time.Now()
			c.send(evMsg(map[string]any{"t": "pong", "n": m.N}))
		}
	}()
	tick := time.NewTicker(evLegCheck)
	defer tick.Stop()
	for {
		select {
		case <-c.done:
			// ausstehende Nachrichten (closed) noch zustellen
			for {
				select {
				case b := <-c.out:
					_ = ws.SetWriteDeadline(time.Now().Add(2 * time.Second))
					_ = ws.WriteMessage(websocket.TextMessage, b)
				default:
					return
				}
			}
		case b := <-c.out:
			_ = ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if ws.WriteMessage(websocket.TextMessage, b) != nil {
				return
			}
		case <-tick.C:
			if s.getLeg(l.id) == nil {
				_ = ws.SetWriteDeadline(time.Now().Add(2 * time.Second))
				_ = ws.WriteMessage(websocket.TextMessage, evMsg(map[string]any{"t": "closed", "why": "zu"}))
				return
			}
		}
	}
}
