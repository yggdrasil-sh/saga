package main

import (
	"errors"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/transport/v5"
	"github.com/pion/transport/v5/stdnet"
	"github.com/pion/turn/v5"
)

// Relay-Schutz des TURN-Wegs (§3.4): eine Allocation darf nur zum Knoten selbst senden - und dort nur an die
// UDP-Ports, die die ICE-Agenten des TURN-Wegs gebunden haben. Ohne diese Grenze erreicht ein Geraet mit gueltigen
// Zugangsdaten jeden Dienst auf der Relay-Adresse (Standard 127.0.0.1: Dienste des Knoten-Hosts). TCP-Allocations
// (RFC 6062, Connect) braucht WebRTC nicht; sie sind abgeschaltet.

var errNoTCPRelay = errors.New("turn: tcp-relay abgeschaltet")

// turnPorts: UDP-Sockets der ICE-Agenten des TURN-Wegs (lokaler Port -> Socket).
type turnPorts struct {
	mu      sync.Mutex
	m       map[int]*turnPort
	dropped atomic.Uint64
}

type turnPort struct {
	c    *net.UDPConn
	seen time.Time // letzte Pruefung "Socket noch offen"
}

func newTurnPorts() *turnPorts { return &turnPorts{m: map[int]*turnPort{}} }

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
		}
	}
	return c, err
}

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
	return &guardPC{PacketConn: pc, ok: r.ok, p: r.p}, a, nil
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
