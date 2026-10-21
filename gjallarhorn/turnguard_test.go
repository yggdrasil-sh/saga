package main

import (
	"crypto/tls"
	"encoding/binary"
	"io"
	"net"
	"testing"
	"time"

	"github.com/pion/stun/v4"
	"github.com/pion/turn/v5"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
)

// rawTURN: minimaler TURN-Client ueber TLS fuer Anfragen, die pions Client nicht so stellt (ChannelBind mit beliebigem
// Ziel, IPv6-Familie mit ::ffff:-Adresse).
type rawTURN struct {
	t            *testing.T
	c            net.Conn
	user, pass   string
	realm, nonce string
	integrity    stun.MessageIntegrity
}

func newRawTURN(t *testing.T, addr string, s *server) *rawTURN {
	t.Helper()
	c, err := tls.Dial("tcp", addr, &tls.Config{InsecureSkipVerify: true, ServerName: "localhost"}) //nolint:gosec
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { c.Close() })
	user, pass, _ := turn.GenerateLongTermCredentials(s.turnSecret, time.Minute)
	return &rawTURN{t: t, c: c, user: user, pass: pass}
}

func (r *rawTURN) do(m *stun.Message) *stun.Message {
	r.t.Helper()
	_ = r.c.SetDeadline(time.Now().Add(3 * time.Second))
	if _, err := r.c.Write(m.Raw); err != nil {
		r.t.Fatal(err)
	}
	hdr := make([]byte, 20)
	if _, err := io.ReadFull(r.c, hdr); err != nil {
		r.t.Fatal(err)
	}
	body := make([]byte, binary.BigEndian.Uint16(hdr[2:4]))
	if _, err := io.ReadFull(r.c, body); err != nil {
		r.t.Fatal(err)
	}
	res := &stun.Message{Raw: append(hdr, body...)}
	if err := res.Decode(); err != nil {
		r.t.Fatal(err)
	}
	return res
}

const magicCookie = 0x2112A442

// peerAttr: XOR-PEER-ADDRESS roh (v6 = IPv6-Familie, auch fuer ::ffff:-Adressen).
type peerAttr struct {
	ip   net.IP
	port int
	v6   bool
}

func (p peerAttr) AddTo(m *stun.Message) error {
	ip, fam := p.ip.To4(), byte(1)
	if p.v6 || ip == nil {
		ip, fam = p.ip.To16(), 2
	}
	key := make([]byte, 16)
	binary.BigEndian.PutUint32(key, magicCookie)
	copy(key[4:], m.TransactionID[:])
	v := []byte{0, fam, 0, 0}
	binary.BigEndian.PutUint16(v[2:], uint16(p.port)^uint16(magicCookie>>16)) //nolint:gosec
	for i := range ip {
		v = append(v, ip[i]^key[i])
	}
	m.Add(stun.AttrXORPeerAddress, v)
	return nil
}

func (r *rawTURN) authed(method stun.Method, attrs ...stun.Setter) *stun.Message {
	all := append([]stun.Setter{stun.TransactionID, stun.NewType(method, stun.ClassRequest)}, attrs...)
	all = append(all, stun.NewUsername(r.user), stun.NewRealm(r.realm), stun.NewNonce(r.nonce), r.integrity, stun.Fingerprint)
	m, err := stun.Build(all...)
	if err != nil {
		r.t.Fatal(err)
	}
	return r.do(m)
}

func reqTransport(proto byte) stun.Setter {
	return stun.RawAttribute{Type: stun.AttrRequestedTransport, Value: []byte{proto, 0, 0, 0}}
}

// allocate (UDP 17 / TCP 6) -> Erfolg?
func (r *rawTURN) allocate(proto byte) bool {
	r.t.Helper()
	m, _ := stun.Build(stun.TransactionID, stun.NewType(stun.MethodAllocate, stun.ClassRequest), reqTransport(proto), stun.Fingerprint)
	res := r.do(m)
	var realm stun.Realm
	var nonce stun.Nonce
	if realm.GetFrom(res) != nil || nonce.GetFrom(res) != nil {
		r.t.Fatalf("401 ohne realm/nonce: %v", res)
	}
	r.realm, r.nonce = realm.String(), nonce.String()
	r.integrity = stun.NewLongTermIntegrity(r.user, r.realm, r.pass)
	return r.authed(stun.MethodAllocate, reqTransport(proto)).Type.Class == stun.ClassSuccessResponse
}

func TestTURNRelayGuard(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	addr := s.mux.Addr().String()

	// TCP-Allocation (RFC 6062): abgelehnt, Connect erreicht keinen Dienst auf 127.0.0.1
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	defer ln.Close()
	hit := make(chan struct{}, 1)
	go func() {
		if c, err := ln.Accept(); err == nil {
			hit <- struct{}{}
			c.Close()
		}
	}()
	if a, err := turnClient(t, addr, s).AllocateTCP(); err == nil {
		_, _ = a.Connect(ln.Addr())
		t.Error("tcp-allocation erlaubt")
	}
	if newRawTURN(t, addr, s).allocate(6) {
		t.Error("tcp-allocation (roh) erlaubt")
	}

	// CreatePermission und ChannelBind: nur die Relay-Adresse selbst
	r := newRawTURN(t, addr, s)
	if !r.allocate(17) {
		t.Fatal("udp-allocation abgelehnt")
	}
	bad := []peerAttr{
		{ip: net.ParseIP("127.0.0.2"), port: 9}, {ip: net.ParseIP("127.1.2.3"), port: 9}, {ip: net.ParseIP("0.0.0.0"), port: 9},
		{ip: net.ParseIP("10.0.0.1"), port: 53}, {ip: net.ParseIP("172.16.0.1"), port: 53}, {ip: net.ParseIP("192.168.1.1"), port: 53},
		{ip: net.ParseIP("169.254.169.254"), port: 80}, {ip: net.ParseIP("100.64.0.1"), port: 9}, {ip: net.ParseIP("192.0.2.1"), port: 9},
		{ip: net.ParseIP("::ffff:10.0.0.1"), port: 53, v6: true}, {ip: net.ParseIP("::ffff:127.0.0.2"), port: 9, v6: true},
		{ip: net.ParseIP("::1"), port: 9, v6: true}, {ip: net.ParseIP("fd00::1"), port: 9, v6: true}, {ip: net.ParseIP("224.0.0.1"), port: 9},
	}
	ch := uint16(0x4000)
	for _, p := range bad {
		if res := r.authed(stun.MethodCreatePermission, p); res.Type.Class == stun.ClassSuccessResponse {
			t.Errorf("CreatePermission %v erlaubt", p.ip)
		}
		ch++
		if res := r.authed(stun.MethodChannelBind, stun.RawAttribute{Type: stun.AttrChannelNumber, Value: []byte{byte(ch >> 8), byte(ch), 0, 0}}, p); res.Type.Class == stun.ClassSuccessResponse {
			t.Errorf("ChannelBind %v erlaubt", p.ip)
		}
	}
	if res := r.authed(stun.MethodCreatePermission, peerAttr{ip: net.ParseIP("127.0.0.1"), port: 9}); res.Type.Class != stun.ClassSuccessResponse {
		t.Errorf("CreatePermission zur Relay-Adresse abgelehnt: %v", res)
	}
	if res := r.authed(stun.MethodChannelBind, stun.RawAttribute{Type: stun.AttrChannelNumber, Value: []byte{0x7f, 0xff, 0, 0}},
		peerAttr{ip: net.ParseIP("127.0.0.1"), port: 9}); res.Type.Class != stun.ClassSuccessResponse {
		t.Errorf("ChannelBind zur Relay-Adresse abgelehnt: %v", res)
	}

	// UDP: an die Relay-Adresse nur zu ICE-Ports des TURN-Wegs (hier: ein nachgebildeter), sonst still verworfen
	other, _ := net.ListenPacket("udp4", "127.0.0.1:0")
	defer other.Close()
	ice, _ := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	s.turnPorts.add(ice)
	relay, err := turnClient(t, addr, s).Allocate()
	if err != nil {
		t.Fatal(err)
	}
	recv := func(pc net.PacketConn) bool {
		_ = pc.SetReadDeadline(time.Now().Add(700 * time.Millisecond))
		b := make([]byte, 64)
		_, _, err := pc.ReadFrom(b)
		return err == nil
	}
	_, _ = relay.WriteTo([]byte("fremd"), other.LocalAddr())
	if recv(other) {
		t.Error("relay zu beliebigem port der relay-adresse")
	}
	_, _ = relay.WriteTo([]byte("ice"), ice.LocalAddr())
	if !recv(ice) {
		t.Error("relay zum ice-port kommt nicht an")
	}
	ia := ice.LocalAddr()
	ice.Close()
	time.Sleep(1100 * time.Millisecond) // Zwischenspeicher "offen" verfallen lassen
	if s.turnPorts.live(ia.(*net.UDPAddr).Port) {
		t.Error("geschlossener ice-port gilt noch als ziel")
	}
	if s.turnPorts.dropped.Load() == 0 {
		t.Error("kein verworfenes paket gezaehlt")
	}
}

func TestRelayPeerOK(t *testing.T) {
	relay := net.ParseIP("127.0.0.1")
	for ip, want := range map[string]bool{"127.0.0.1": true, "::ffff:127.0.0.1": true, "127.0.0.2": false, "::1": false,
		"::ffff:10.0.0.1": false, "10.0.0.1": false, "0.0.0.0": false, "::": false} {
		if got := relayPeerOK(relay, net.ParseIP(ip)); got != want {
			t.Errorf("%s: %v, erwartet %v", ip, got, want)
		}
	}
	if relayPeerOK(relay, nil) {
		t.Error("nil erlaubt")
	}
}
