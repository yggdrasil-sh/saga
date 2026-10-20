package netx

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"errors"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"time"
)

// Einfache STUN-Antworten auf 443/UDP: Binding-Anfragen ohne USERNAME (also
// keine ICE-Pruefungen, sondern "Ist UDP erreichbar?" von Geraeten, von Saga
// oder vom Selbsttest) beantwortet die Weiche selbst mit XOR-MAPPED-ADDRESS.
// ICE-Pruefungen (mit USERNAME) gehen unveraendert an pion.

const (
	stunBindingRequest  = 0x0001
	stunBindingSuccess  = 0x0101
	stunAttrUsername    = 0x0006
	stunAttrIntegrity   = 0x0008
	stunAttrXorMapped   = 0x0020
	stunAttrFingerprint = 0x8028
	stunHeader          = 20
)

// IsPlainBinding meldet eine STUN-Binding-Anfrage ohne USERNAME und ohne
// MESSAGE-INTEGRITY (RFC 5389 Abschnitt 7.3.1, ohne Authentisierung).
func IsPlainBinding(b []byte) bool {
	if len(b) < stunHeader || !IsSTUN(b) || binary.BigEndian.Uint16(b) != stunBindingRequest {
		return false
	}
	l := int(binary.BigEndian.Uint16(b[2:]))
	if l&3 != 0 || stunHeader+l > len(b) {
		return false
	}
	a := b[stunHeader : stunHeader+l]
	for len(a) >= 4 {
		t := binary.BigEndian.Uint16(a)
		al := int(binary.BigEndian.Uint16(a[2:]))
		if t == stunAttrUsername || t == stunAttrIntegrity {
			return false
		}
		p := 4 + (al+3)&^3
		if p > len(a) {
			return false
		}
		a = a[p:]
	}
	return len(a) == 0
}

// AppendBindingSuccess haengt an dst eine Binding-Antwort fuer die Anfrage req
// mit XOR-MAPPED-ADDRESS from an. req muss IsPlainBinding erfuellen.
func AppendBindingSuccess(dst, req []byte, from netip.AddrPort) []byte {
	ip := from.Addr().Unmap()
	alen := 8
	if ip.Is6() {
		alen = 20
	}
	dst = binary.BigEndian.AppendUint16(dst, stunBindingSuccess)
	dst = binary.BigEndian.AppendUint16(dst, uint16(4+alen))
	dst = append(dst, req[4:20]...) // Cookie + Transaktion
	dst = binary.BigEndian.AppendUint16(dst, stunAttrXorMapped)
	dst = binary.BigEndian.AppendUint16(dst, uint16(alen))
	fam := byte(1)
	if ip.Is6() {
		fam = 2
	}
	dst = append(dst, 0, fam)
	dst = binary.BigEndian.AppendUint16(dst, from.Port()^0x2112)
	if ip.Is4() {
		a := ip.As4()
		for i := 0; i < 4; i++ {
			dst = append(dst, a[i]^stunMagic[i])
		}
	} else {
		a := ip.As16()
		for i := 0; i < 16; i++ {
			k := stunMagic[i%4]
			if i >= 4 {
				k = req[8+i-4]
			}
			dst = append(dst, a[i]^k)
		}
	}
	return dst
}

// ParseXorMapped liest XOR-MAPPED-ADDRESS aus einer Binding-Antwort.
func ParseXorMapped(b []byte) (netip.AddrPort, bool) {
	if len(b) < stunHeader || !IsSTUN(b) || binary.BigEndian.Uint16(b) != stunBindingSuccess {
		return netip.AddrPort{}, false
	}
	l := int(binary.BigEndian.Uint16(b[2:]))
	if stunHeader+l > len(b) {
		return netip.AddrPort{}, false
	}
	a := b[stunHeader : stunHeader+l]
	for len(a) >= 4 {
		t := binary.BigEndian.Uint16(a)
		al := int(binary.BigEndian.Uint16(a[2:]))
		if 4+al > len(a) {
			break
		}
		v := a[4 : 4+al]
		if t == stunAttrXorMapped && len(v) >= 8 {
			port := binary.BigEndian.Uint16(v[2:]) ^ 0x2112
			switch {
			case v[1] == 1:
				var ip [4]byte
				for i := range ip {
					ip[i] = v[4+i] ^ stunMagic[i]
				}
				return netip.AddrPortFrom(netip.AddrFrom4(ip), port), true
			case v[1] == 2 && len(v) >= 20:
				var ip [16]byte
				for i := range ip {
					k := stunMagic[i%4]
					if i >= 4 {
						k = b[8+i-4]
					}
					ip[i] = v[4+i] ^ k
				}
				return netip.AddrPortFrom(netip.AddrFrom16(ip), port), true
			}
		}
		p := 4 + (al+3)&^3
		if p > len(a) {
			break
		}
		a = a[p:]
	}
	return netip.AddrPort{}, false
}

// stunResponder beantwortet einfache Binding-Anfragen, hoechstens Rate je
// Sekunde (Schutz vor Missbrauch als Reflektor; Antwort ist kaum groesser
// als die Anfrage).
type stunResponder struct {
	mu     sync.Mutex
	buf    [64]byte
	tokens float64
	last   time.Time
	rate   float64
	Answer atomic.Uint64
	Limit  atomic.Uint64
}

func (r *stunResponder) allow(now time.Time) bool {
	if r.rate == 0 {
		r.rate = 200
	}
	if !r.last.IsZero() {
		r.tokens += now.Sub(r.last).Seconds() * r.rate
	} else {
		r.tokens = r.rate
	}
	if r.tokens > r.rate {
		r.tokens = r.rate
	}
	r.last = now
	if r.tokens < 1 {
		return false
	}
	r.tokens--
	return true
}

func (r *stunResponder) reply(c *net.UDPConn, req []byte, from netip.AddrPort) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.allow(time.Now()) {
		r.Limit.Add(1)
		return
	}
	out := AppendBindingSuccess(r.buf[:0], req, from)
	_, _ = c.WriteToUDPAddrPort(out, from)
	r.Answer.Add(1)
}

// UDPCheck ist das Ergebnis des UDP-Selbsttests.
type UDPCheck struct {
	State  string   `json:"state"` // ok | fehlt | aus | unbekannt
	Via    string   `json:"via,omitempty"`
	Tested []string `json:"tested,omitempty"`
	RTTms  float64  `json:"rtt_ms,omitempty"`
	At     string   `json:"at,omitempty"`
}

// ErrNoAnswer: keine der Adressen hat geantwortet.
var ErrNoAnswer = errors.New("keine STUN-Antwort")

// ProbeUDP schickt von einem eigenen Socket Binding-Anfragen an die Ziele
// und liefert das erste, das antwortet (Rundlaufzeit). Sendet 3 Versuche.
func ProbeUDP(ctx context.Context, targets []netip.AddrPort) (netip.AddrPort, time.Duration, error) {
	if len(targets) == 0 {
		return netip.AddrPort{}, 0, ErrNoAnswer
	}
	c, err := net.ListenUDP("udp", nil)
	if err != nil {
		return netip.AddrPort{}, 0, err
	}
	defer c.Close()
	var req [20]byte
	binary.BigEndian.PutUint16(req[0:], stunBindingRequest)
	copy(req[4:8], stunMagic[:])
	_, _ = rand.Read(req[8:])
	dl, ok := ctx.Deadline()
	if !ok {
		dl = time.Now().Add(2 * time.Second)
	}
	type res struct {
		from netip.AddrPort
		rtt  time.Duration
	}
	got := make(chan res, 1)
	start := time.Now()
	go func() {
		buf := make([]byte, 1500)
		_ = c.SetReadDeadline(dl)
		for {
			n, from, err := c.ReadFromUDPAddrPort(buf)
			if err != nil {
				return
			}
			m := buf[:n]
			if n >= 20 && binary.BigEndian.Uint16(m) == stunBindingSuccess && string(m[8:20]) == string(req[8:20]) {
				got <- res{from, time.Since(start)}
				return
			}
		}
	}()
	for try := 0; try < 3; try++ {
		for _, t := range targets {
			_, _ = c.WriteToUDPAddrPort(req[:], t)
		}
		select {
		case r := <-got:
			return r.from, r.rtt, nil
		case <-ctx.Done():
			return netip.AddrPort{}, 0, ErrNoAnswer
		case <-time.After(300 * time.Millisecond):
		}
	}
	select {
	case r := <-got:
		return r.from, r.rtt, nil
	case <-ctx.Done():
	case <-time.After(time.Until(dl)):
	}
	return netip.AddrPort{}, 0, ErrNoAnswer
}
