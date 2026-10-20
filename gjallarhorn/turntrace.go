package main

import (
	"net"
	"sync"
	"time"

	"github.com/pion/turn/v5"
	"github.com/pion/webrtc/v4"
)

// Aufbau-Diagnose fuer TURN-TLS-Beine: Zeitpunkte je TURN-Verbindung,
// verknuepft ueber die Relay-Adresse mit dem Bein. Gemeldet werden nur
// Dauern in ms, Adressen bleiben im Speicher und verfallen nach 2 min.

type turnTrace struct {
	accepted, tls, auth, alloc, perm, channel time.Time
	resumed                                   bool
}

// TURNSetup ist die Zerlegung eines TURN-TLS-Aufbaus (ms ab TCP-Annahme).
type TURNSetup struct {
	LegBeforeTCP float64 `json:"leg_before_tcp_ms"` // Bein am Knoten angelegt, bevor TCP ankam
	TLS          float64 `json:"tls_ms"`            // TLS-Handshake fertig
	Resumed      bool    `json:"tls_resumed"`
	Auth         float64 `json:"auth_ms"`    // Allocate mit Zugangsdaten (nach 401-Runde)
	Alloc        float64 `json:"alloc_ms"`   // Allocation angelegt
	Perm         float64 `json:"perm_ms"`    // CreatePermission
	Channel      float64 `json:"channel_ms"` // ChannelBind
	ICE          float64 `json:"ice_ms"`     // ICE verbunden (Knoten)
	DTLS         float64 `json:"dtls_ms"`    // DTLS fertig = Bein verbunden
}

type turnTracer struct {
	mu      sync.Mutex
	bySrc   map[string]*turnTrace
	byRelay map[string]*turnTrace
}

func newTurnTracer() *turnTracer {
	t := &turnTracer{bySrc: map[string]*turnTrace{}, byRelay: map[string]*turnTrace{}}
	go func() {
		for range time.Tick(30 * time.Second) {
			t.mu.Lock()
			for k, v := range t.bySrc {
				if time.Since(v.accepted) > 2*time.Minute {
					delete(t.bySrc, k)
				}
			}
			for k, v := range t.byRelay {
				if time.Since(v.accepted) > 2*time.Minute {
					delete(t.byRelay, k)
				}
			}
			t.mu.Unlock()
		}
	}()
	return t
}

func (t *turnTracer) conn(remote net.Addr, accepted, tls time.Time, resumed bool) {
	if remote == nil {
		return
	}
	t.mu.Lock()
	t.bySrc[remote.String()] = &turnTrace{accepted: accepted, tls: tls, resumed: resumed}
	t.mu.Unlock()
}

func (t *turnTracer) src(a net.Addr, f func(*turnTrace)) {
	if a == nil {
		return
	}
	t.mu.Lock()
	if tr := t.bySrc[a.String()]; tr != nil {
		f(tr)
	}
	t.mu.Unlock()
}

func (t *turnTracer) handlers() turn.EventHandler {
	return turn.EventHandler{
		OnAuth: func(src, _ net.Addr, _, _, _ string, method string, ok bool) {
			t.src(src, func(tr *turnTrace) {
				if ok && tr.auth.IsZero() {
					tr.auth = time.Now()
				}
			})
		},
		OnAllocationCreated: func(src, _ net.Addr, _, _, _ string, relay net.Addr, _ int) {
			t.src(src, func(tr *turnTrace) {
				tr.alloc = time.Now()
				if relay != nil {
					t.byRelay[relay.String()] = tr
				}
			})
		},
		OnPermissionCreated: func(src, _ net.Addr, _, _, _ string, _ net.Addr, _ net.IP) {
			t.src(src, func(tr *turnTrace) {
				if tr.perm.IsZero() {
					tr.perm = time.Now()
				}
			})
		},
		OnChannelCreated: func(src, _ net.Addr, _, _, _ string, _, _ net.Addr, _ uint16) {
			t.src(src, func(tr *turnTrace) {
				if tr.channel.IsZero() {
					tr.channel = time.Now()
				}
			})
		},
	}
}

// setup baut die Zerlegung fuer ein Bein (nil, wenn kein TURN-Bein).
func (t *turnTracer) setup(pc *webrtc.PeerConnection, created, iceAt, connected time.Time) *TURNSetup {
	if pc == nil || connected.IsZero() {
		return nil
	}
	var remote string
	for _, tx := range pc.GetTransceivers() {
		if s := tx.Sender(); s != nil && s.Transport() != nil {
			if p, err := s.Transport().ICETransport().GetSelectedCandidatePair(); err == nil && p != nil && p.Remote != nil {
				remote = net.JoinHostPort(p.Remote.Address, itoa(int(p.Remote.Port)))
				break
			}
		}
	}
	if remote == "" {
		return nil
	}
	t.mu.Lock()
	tr := t.byRelay[remote]
	t.mu.Unlock()
	if tr == nil {
		return nil
	}
	ms := func(x time.Time) float64 {
		if x.IsZero() {
			return -1
		}
		return float64(x.Sub(tr.accepted).Microseconds()) / 1000
	}
	return &TURNSetup{LegBeforeTCP: float64(tr.accepted.Sub(created).Microseconds()) / 1000, TLS: ms(tr.tls), Resumed: tr.resumed,
		Auth: ms(tr.auth), Alloc: ms(tr.alloc), Perm: ms(tr.perm), Channel: ms(tr.channel), ICE: ms(iceAt), DTLS: ms(connected)}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [12]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
