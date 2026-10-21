package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/sfu"
)

// Medien-Token der Saga-Steuerung (Konzept v2 §2.4, Paket P3):
//
//	h1.<payload>.<sig>   payload = base64url(JSON), sig = base64url(Ed25519(saga_sk, "saga-horn-token/1." + payload))
//
// JSON: {"v":1,"node","call_ref","peer_ref","leg_ref","role","pub":["a","v","s"],"max_kbit","udp_ok","dev_class","exp","jti"}.
// call_ref/peer_ref/leg_ref sind je Knoten zufaellige Kennungen (Saga leitet sie geheim ab), nie Konto- oder
// Geraetenamen. Der Knoten prueft lokal (kein Rueckruf je Bein) mit dem oeffentlichen Saga-Schluessel aus der
// Steuerverbindung (welcome) bzw. HORN_SAGA_PUB. Gegenstueck: saga/horn.py (token).

const tokenPrefix = "h1."
const tokenDomain = "saga-horn-token/1."

// tokenMaxLife: laenger gueltige Token lehnt der Knoten ab (Saga stellt +60 s aus; etwas Spiel fuer die Uhr).
const tokenMaxLife = 120 * time.Second

type tokenBody struct {
	V       int      `json:"v"`
	Node    string   `json:"node"`
	CallRef string   `json:"call_ref"`
	PeerRef string   `json:"peer_ref"`
	LegRef  string   `json:"leg_ref"`
	Role    string   `json:"role"`
	Pub     []string `json:"pub"`
	MaxKbit int      `json:"max_kbit"`
	UDPOK   bool     `json:"udp_ok"`
	Dev     string   `json:"dev_class"`
	Exp     int64    `json:"exp"`
	JTI     string   `json:"jti"`
}

var errToken = errors.New("token ungueltig")

// sagaVerifier prueft Medien-Token mit dem oeffentlichen Saga-Schluessel. Der
// Schluessel kann sich zur Laufzeit aendern (neue Steuerverbindung).
type sagaVerifier struct {
	pub atomic.Pointer[ed25519.PublicKey]
}

func (v *sagaVerifier) SetKey(k ed25519.PublicKey) {
	if len(k) != ed25519.PublicKeySize {
		return
	}
	c := append(ed25519.PublicKey(nil), k...)
	v.pub.Store(&c)
}

func (v *sagaVerifier) HasKey() bool { return v.pub.Load() != nil }

func validRef(s string) bool {
	if len(s) < 8 || len(s) > 64 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

// Verify: Signatur, Form und Lebensdauer. Ablauf und Knotenname prueft Node.Admit.
func (v *sagaVerifier) Verify(tok string) (sfu.Grant, error) {
	k := v.pub.Load()
	if k == nil {
		return sfu.Grant{}, sfu.ErrNoVerifier
	}
	if len(tok) > 4096 || !strings.HasPrefix(tok, tokenPrefix) {
		return sfu.Grant{}, errToken
	}
	p, sig, ok := strings.Cut(tok[len(tokenPrefix):], ".")
	if !ok {
		return sfu.Grant{}, errToken
	}
	sb, err := base64.RawURLEncoding.DecodeString(sig)
	if err != nil || len(sb) != ed25519.SignatureSize {
		return sfu.Grant{}, errToken
	}
	if !ed25519.Verify(*k, []byte(tokenDomain+p), sb) {
		return sfu.Grant{}, errToken
	}
	raw, err := base64.RawURLEncoding.DecodeString(p)
	if err != nil {
		return sfu.Grant{}, errToken
	}
	var b tokenBody
	if err := json.Unmarshal(raw, &b); err != nil || b.V != 1 {
		return sfu.Grant{}, errToken
	}
	if b.Node == "" || !validRef(b.CallRef) || !validRef(b.PeerRef) || !validRef(b.LegRef) || !validRef(b.JTI) || b.Exp <= 0 {
		return sfu.Grant{}, errToken
	}
	exp := time.Unix(b.Exp, 0)
	if time.Until(exp) > tokenMaxLife {
		return sfu.Grant{}, errToken
	}
	role, err := sfu.ParseRole(b.Role)
	if err != nil {
		return sfu.Grant{}, errToken
	}
	dev := sfu.Desktop
	switch b.Dev {
	case "desktop", "":
	case "mobile":
		dev = sfu.Mobile
	default:
		return sfu.Grant{}, errToken
	}
	return sfu.Grant{Node: b.Node, Room: b.CallRef, Peer: b.PeerRef, Leg: b.LegRef, Role: role, Pub: sfu.ParsePub(b.Pub),
		MaxKbit: b.MaxKbit, UDPOK: b.UDPOK, Device: dev, Exp: exp, ID: b.JTI}, nil
}

// usedTokens: jedes Token gilt einmal je Zweck (Bein, TURN-Zugang). Eintraege
// leben bis zum Ablauf des Tokens.
type usedTokens struct {
	mu sync.Mutex
	m  map[string]time.Time
	n  int
}

// use meldet false, wenn (id, zweck) schon benutzt wurde.
func (u *usedTokens) use(id, purpose string, exp time.Time) bool {
	if id == "" { // Entwicklungs-Token (HORN_DEV) ohne Kennung
		return true
	}
	k := purpose + "|" + id
	now := time.Now()
	u.mu.Lock()
	defer u.mu.Unlock()
	if u.m == nil {
		u.m = map[string]time.Time{}
	}
	if _, ok := u.m[k]; ok {
		return false
	}
	if u.n++; u.n%256 == 0 || len(u.m) > 100000 {
		for x, e := range u.m {
			if now.After(e) {
				delete(u.m, x)
			}
		}
	}
	u.m[k] = exp.Add(5 * time.Second)
	return true
}
