package main

import (
	"encoding/binary"
	"io"
	"net"
	"testing"
	"time"

	"github.com/pion/stun/v4"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/netx"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/turnpipe"
)

// relayedAddr: Allocation (UDP) ueber den rohen Client, Ergebnis XOR-RELAYED-ADDRESS.
func (r *rawTURN) relayedAddr() *net.UDPAddr {
	r.t.Helper()
	m, _ := stun.Build(stun.TransactionID, stun.NewType(stun.MethodAllocate, stun.ClassRequest), reqTransport(17), stun.Fingerprint)
	res := r.do(m)
	var realm stun.Realm
	var nonce stun.Nonce
	if realm.GetFrom(res) != nil || nonce.GetFrom(res) != nil {
		r.t.Fatalf("401 ohne realm/nonce: %v", res)
	}
	r.realm, r.nonce = realm.String(), nonce.String()
	r.integrity = stun.NewLongTermIntegrity(r.user, r.realm, r.pass)
	res = r.authed(stun.MethodAllocate, reqTransport(17))
	var ra stun.XORMappedAddress // gleiche Kodierung wie XOR-RELAYED-ADDRESS
	if err := ra.GetFromAs(res, stun.AttrXORRelayedAddress); err != nil {
		r.t.Fatalf("allocate: %v", res)
	}
	return &net.UDPAddr{IP: ra.IP, Port: ra.Port}
}

// stauLauf: ICE-Ende des TURN-Wegs (wie ein Bein) schreibt so schnell es kann an die Allocation eines Geraets, das
// seine TURN-TLS-Verbindung nicht liest. Ergebnis: geschriebene Bytes und laengster einzelner Schreibvorgang.
func stauLauf(t *testing.T, s *server) (sent int, longest time.Duration, ice net.PacketConn, r *rawTURN, relay *net.UDPAddr) {
	t.Helper()
	gn, err := newGuardNet(s.turnPorts)
	if err != nil {
		t.Fatal(err)
	}
	uc, err := gn.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { uc.Close() })
	r = newRawTURN(t, s.mux.Addr().String(), s)
	relay = r.relayedAddr()
	if res := r.authed(stun.MethodCreatePermission, peerAttr{ip: net.ParseIP("127.0.0.1"), port: 9}); res.Type.Class != stun.ClassSuccessResponse {
		t.Fatalf("CreatePermission: %v", res)
	}
	_ = r.c.SetDeadline(time.Time{})
	pkt := make([]byte, 1200)
	pkt[0] = 0x80
	end := time.Now().Add(1500 * time.Millisecond)
	for time.Now().Before(end) && sent < 64<<20 {
		t0 := time.Now()
		n, err := uc.WriteTo(pkt, relay)
		if err != nil {
			t.Fatal(err)
		}
		if d := time.Since(t0); d > longest {
			longest = d
		}
		sent += n
	}
	return sent, longest, uc, r, relay
}

// Kern des Befunds (Spike ws-apps §6.1): Staut die TURN-TLS-Verbindung zum Geraet, muss das Bein beim Schreiben
// warten - wie auf ICE-TCP -, statt dass der Stau unsichtbar im Empfangspuffer des Relay-Sockets liegt.
func TestTURNBackpressure(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	if s.turnPorts.hub == nil {
		t.Fatal("Kopplung nicht an")
	}
	sent, longest, ice, r, relay := stauLauf(t, s)
	v := s.turnPorts.hub.View()
	t.Logf("gekoppelt: %d KiB geschrieben, laengster Schreibvorgang %v, %+v", sent>>10, longest, v)
	if longest < 200*time.Millisecond || v.Waits == 0 {
		t.Fatalf("Bein wartet nicht (laengster Schreibvorgang %v, waits %d)", longest, v.Waits)
	}
	if sent > 8<<20 {
		t.Fatalf("%d KiB angenommen - Stau liegt nicht im Bein", sent>>10)
	}
	// Liest das Geraet wieder, kommt alles in Reihe an (nichts im Prozess verworfen ausser nach MaxWait)
	go func() { _, _ = io.Copy(io.Discard, r.c) }()
	pkt := make([]byte, 1200)
	pkt[0] = 0x80
	t0 := time.Now()
	for i := 0; i < 200; i++ {
		if _, err := ice.WriteTo(pkt, relay); err != nil {
			t.Fatal(err)
		}
	}
	if d := time.Since(t0); d > 2*time.Second {
		t.Fatalf("nach dem Lesen weiter gestaut: %v", d)
	}
}

// Gegenprobe: ohne Kopplung blockiert das Bein nie (der Befund kommt zurueck).
func TestTURNBackpressureOff(t *testing.T) {
	t.Setenv("HORN_TURN_COUPLE", "0")
	s := testServer(t, netx.ProxyOff)
	if s.turnPorts.hub != nil {
		t.Fatal("Kopplung trotz HORN_TURN_COUPLE=0")
	}
	sent, longest, _, _, _ := stauLauf(t, s)
	t.Logf("ohne Kopplung: %d KiB geschrieben, laengster Schreibvorgang %v", sent>>10, longest)
	if longest > 100*time.Millisecond {
		t.Fatalf("Bein wartet ohne Kopplung (%v) - Gegenprobe ungueltig", longest)
	}
}

// Richtung Knoten und ICE-Pruefungen: Ein Geraet erreicht ueber seine Allocation das ICE-Ende im Prozess (nicht ueber
// den Kernel), ein Relay-Ende erreicht kein anderes Relay-Ende (kein offenes Relais, auch nicht im Prozess).
func TestTURNPipeDirections(t *testing.T) {
	s := testServer(t, netx.ProxyOff)
	gn, _ := newGuardNet(s.turnPorts)
	uc, err := gn.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1")})
	if err != nil {
		t.Fatal(err)
	}
	defer uc.Close()
	if _, ok := uc.(*turnpipe.Conn); !ok {
		t.Fatalf("ICE-Socket des TURN-Wegs nicht gekoppelt: %T", uc)
	}
	r := newRawTURN(t, s.mux.Addr().String(), s)
	relay := r.relayedAddr()
	r.authed(stun.MethodCreatePermission, peerAttr{ip: net.ParseIP("127.0.0.1"), port: 9})
	// Send-Indication an das ICE-Ende
	ia := uc.LocalAddr().(*net.UDPAddr)
	m, _ := stun.Build(stun.TransactionID, stun.NewType(stun.MethodSend, stun.ClassIndication),
		peerAttr{ip: ia.IP, port: ia.Port}, stun.RawAttribute{Type: stun.AttrData, Value: []byte{0x80, 7, 7}})
	if _, err := r.c.Write(m.Raw); err != nil {
		t.Fatal(err)
	}
	_ = uc.SetReadDeadline(time.Now().Add(2 * time.Second))
	b := make([]byte, 64)
	n, from, err := uc.ReadFrom(b)
	if err != nil || n != 3 || from.String() != relay.String() {
		t.Fatalf("geraet -> ice: %d von %v (%v), erwartet von %v", n, from, err, relay)
	}
	v := s.turnPorts.hub.View()
	if v.Pkts == 0 || v.Kernel != 0 {
		t.Fatalf("nicht im prozess: %+v", v)
	}
	// zweites Geraet: seine Allocation darf die erste nicht erreichen
	r2 := newRawTURN(t, s.mux.Addr().String(), s)
	r2.relayedAddr()
	r2.authed(stun.MethodCreatePermission, peerAttr{ip: net.ParseIP("127.0.0.1"), port: 9})
	m, _ = stun.Build(stun.TransactionID, stun.NewType(stun.MethodSend, stun.ClassIndication),
		peerAttr{ip: relay.IP, port: relay.Port}, stun.RawAttribute{Type: stun.AttrData, Value: []byte{0x80, 8}})
	d0 := s.turnPorts.dropped.Load()
	if _, err := r2.c.Write(m.Raw); err != nil {
		t.Fatal(err)
	}
	time.Sleep(200 * time.Millisecond)
	if s.turnPorts.dropped.Load() != d0+1 {
		t.Fatal("relay -> relay nicht vom schutz verworfen")
	}
	_ = r.c.SetReadDeadline(time.Now().Add(300 * time.Millisecond))
	hdr := make([]byte, 4)
	if _, err := io.ReadFull(r.c, hdr); err == nil {
		t.Fatalf("erstes geraet bekam fremde daten: %x (%d)", hdr, binary.BigEndian.Uint16(hdr[2:]))
	}
}
