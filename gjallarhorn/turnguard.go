package main

import (
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/transport/v5"
	"github.com/pion/transport/v5/stdnet"
	"github.com/pion/turn/v5"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/turnpipe"
)

// Relay-Schutz des TURN-Wegs (§3.4): eine Allocation darf nur zum Knoten selbst senden - und dort nur an die
// UDP-Ports, die die ICE-Agenten des TURN-Wegs gebunden haben. Ohne diese Grenze erreicht ein Geraet mit gueltigen
// Zugangsdaten jeden Dienst auf der Relay-Adresse (Standard 127.0.0.1: Dienste des Knoten-Hosts). TCP-Allocations
// (RFC 6062, Connect) braucht WebRTC nicht; sie sind abgeschaltet.

// Kopplung im Prozess (2.5, internal/turnpipe): Zwischen den ICE-Sockets des TURN-Wegs und den Relay-Sockets der
// Allocations gehen Pakete nicht ueber Loopback-UDP, sondern durch eine kleine Warteschlange im Prozess. Staut die
// TURN-TCP/TLS-Verbindung zum Geraet, wartet das Bein beim Schreiben - wie auf ICE-TCP (TCP_NOTSENT_LOWAT): Die
// Schaetzung sieht das Alter der eigenen Warteschlange, H3 verwirft, Schichten gehen herunter. Ohne Kopplung lag der
// Stau unsichtbar im Empfangspuffer des Relay-Sockets (bei 600 kbit/s rund 2,8 s). Adressen, Ports und der
// Relay-Schutz unten bleiben gleich; ein Relay-Ende erreicht im Prozess nur ICE-Enden des TURN-Wegs.
//   HORN_TURN_COUPLE=0  Kopplung aus (Gegenprobe: Loopback-UDP wie bis 2.4)
//   HORN_TURN_PIPE=n    Grenze der Warteschlange je Relay-Ende in Bytes (Standard 4096)

var errNoTCPRelay = errors.New("turn: tcp-relay abgeschaltet")

// turnPorts: UDP-Sockets der ICE-Agenten des TURN-Wegs (lokaler Port -> Socket).
type turnPorts struct {
	mu      sync.Mutex
	m       map[int]*turnPort
	dropped atomic.Uint64
	hub     *turnpipe.Hub // nil = Kopplung aus
}

type turnPort struct {
	c    *net.UDPConn
	seen time.Time // letzte Pruefung "Socket noch offen"
}

func newTurnPorts() *turnPorts {
	p := &turnPorts{m: map[int]*turnPort{}}
	if os.Getenv("HORN_TURN_COUPLE") != "0" {
		p.hub = turnpipe.NewHub()
		if n, err := strconv.Atoi(os.Getenv("HORN_TURN_PIPE")); err == nil && n > 0 {
			p.hub.Limit = n
		}
	} else {
		log.Printf("TURN-Weg: Kopplung im Prozess aus (HORN_TURN_COUPLE=0)")
	}
	return p
}

func (p *turnPorts) add(c *net.UDPConn) {
	a, ok := c.LocalAddr().(*net.UDPAddr)
	if !ok {
		return
	}
	p.mu.Lock()
	p.m[a.Port] = &turnPort{c: c, seen: time.Now()}
	if len(p.m) > 4096 { // geschlossene Sockets aufraeumen
		for k, v := range p.m {
			if !udpOpen(v.c) {
				delete(p.m, k)
			}
		}
	}
	p.mu.Unlock()
}

// live: gehoert der Port zu einem noch offenen Socket des TURN-Wegs? (Ergebnis je Port hoechstens 1 s zwischengespeichert)
func (p *turnPorts) live(port int) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	e := p.m[port]
	if e == nil {
		return false
	}
	if time.Since(e.seen) < time.Second {
		return true
	}
	if !udpOpen(e.c) {
		delete(p.m, port)
		return false
	}
	e.seen = time.Now()
	return true
}

func udpOpen(c *net.UDPConn) bool {
	rc, err := c.SyscallConn()
	if err != nil {
		return false
	}
	return rc.Control(func(uintptr) {}) == nil
}

// guardNet: stdnet, merkt sich die UDP-Sockets der ICE-Agenten (nur fuer die SettingEngine des TURN-Wegs).
type guardNet struct {
	*stdnet.Net
	ports *turnPorts
}

func newGuardNet(p *turnPorts) (*guardNet, error) {
	n, err := stdnet.NewNet()
	if err != nil {
		return nil, err
	}
	return &guardNet{Net: n, ports: p}, nil
}

func (g *guardNet) ListenUDP(network string, la *net.UDPAddr) (transport.UDPConn, error) {
	c, err := g.Net.ListenUDP(network, la)
	if err == nil {
		if uc, ok := c.(*net.UDPConn); ok {
			g.ports.add(uc)
			if g.ports.hub != nil {
				return g.ports.hub.Attach(uc, turnpipe.ICE, nil), nil
			}
		}
	}
	return c, err
}

var _ transport.UDPConn = (*turnpipe.Conn)(nil)

// relayGen: feste Relay-Adresse, ausgehend nur zu erlaubten Zielen, kein TCP.
type relayGen struct {
	turn.RelayAddressGeneratorStatic
	ok func(net.Addr) bool
	p  *turnPorts
}

func (r *relayGen) AllocatePacketConn(c turn.AllocateListenerConfig) (net.PacketConn, net.Addr, error) {
	pc, a, err := r.RelayAddressGeneratorStatic.AllocatePacketConn(c)
	if err != nil {
		return nil, nil, err
	}
	g := &guardPC{PacketConn: pc, ok: r.ok, p: r.p}
	if uc, ok := pc.(*net.UDPConn); ok && r.p.hub != nil {
		// im Prozess nur zu ICE-Enden des TURN-Wegs; alles andere wie bisher durch den Schutz
		return r.p.hub.Attach(uc, turnpipe.Relay, g.WriteTo), a, nil
	}
	return g, a, nil
}

func (r *relayGen) AllocateListener(turn.AllocateListenerConfig) (net.Listener, net.Addr, error) {
	return nil, nil, errNoTCPRelay
}

func (r *relayGen) AllocateConn(turn.AllocateConnConfig) (net.Conn, error) {
	return nil, errNoTCPRelay
}

// guardPC verwirft Pakete an nicht erlaubte Ziele still (kein Fehler je Paket, nur ein Zaehler).
type guardPC struct {
	net.PacketConn
	ok func(net.Addr) bool
	p  *turnPorts
}

func (g *guardPC) WriteTo(b []byte, a net.Addr) (int, error) {
	if !g.ok(a) {
		g.p.dropped.Add(1)
		return len(b), nil
	}
	return g.PacketConn.WriteTo(b, a)
}

// relayPeerOK: CreatePermission/ChannelBind/Connect - nur die Relay-Adresse selbst (auch ::ffff:-Form derselben IPv4).
func relayPeerOK(relay, peer net.IP) bool {
	if peer == nil || relay == nil || peer.IsUnspecified() || peer.IsMulticast() {
		return false
	}
	return peer.Equal(relay)
}

// relayTargetOK: Ziel eines Relay-Pakets - Relay-Adresse und ein offener ICE-Port des TURN-Wegs.
func (s *server) relayTargetOK(a net.Addr) bool {
	ua, ok := a.(*net.UDPAddr)
	if !ok || !relayPeerOK(s.cfg.RelayIP, ua.IP) {
		return false
	}
	return s.turnPorts.live(ua.Port)
}

// apiTurnPipe: Zaehler der Kopplung (Dev, Pruefstand e2e/stau.mjs).
func (s *server) apiTurnPipe(w http.ResponseWriter, _ *http.Request) {
	if s.turnPorts == nil || s.turnPorts.hub == nil {
		writeJSON(w, 200, map[string]any{"on": false, "relay_dropped": s.turnPorts.droppedN()})
		return
	}
	writeJSON(w, 200, map[string]any{"on": true, "pipe": s.turnPorts.hub.View(), "relay_dropped": s.turnPorts.droppedN()})
}

func (p *turnPorts) droppedN() uint64 {
	if p == nil {
		return 0
	}
	return p.dropped.Load()
}
