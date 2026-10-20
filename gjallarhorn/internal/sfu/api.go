// Package sfu ist der Weiterleiter (Selective Forwarding Unit) des
// Gjallarhorn-Knotens: Raeume, Teilnehmende und ihre Beine, Sprechererkennung
// (Top-3-Ton per audio-level), Last-N, Schichtwahl je Empfaenger nach
// geschaetzter Downlink-Rate und die Vorrang-Warteschlange aus fwd.
//
// Das Paket kennt pion nicht. Die Anbindung (PeerConnection, Spuren) macht
// der Aufrufer: er legt mit Room.AddLeg ein Bein an, meldet eingehende
// Spuren mit Leg.AddSource und bindet die Ausgaenge (Leg.Outs) an
// abgehende Spuren. Medienrahmen bleiben opak; gelesen wird nur der
// VP8-Descriptor (Schluesselbild, Schicht) und die Kopferweiterung
// ssrc-audio-level.
//
// Fuer die Saga-Steuerung (Paket P3) ist die interne API: Node.CreateRoom,
// Node.CloseRoom, Node.Admit (Token-Pruefung ueber Verifier), Room.AddLeg,
// Room.RemoveLeg, Room.RemovePeer, Node.Load und Config.OnLayout.
package sfu

import (
	"errors"
	"time"
)

// Path ist der Transportweg eines Beins.
type Path uint8

const (
	PathUDP  Path = iota // ICE ueber 443/UDP, BUNDLE, NACK/RTX, TWCC/GCC
	PathTCP              // ICE-TCP passiv auf 443/TCP
	PathTURN             // TURN-TLS auf 443/TCP (Relay nur zum Knoten selbst)
)

func (p Path) String() string {
	switch p {
	case PathUDP:
		return "udp"
	case PathTCP:
		return "tcp"
	case PathTURN:
		return "turn"
	}
	return "?"
}

// ParsePath liest "udp", "tcp" oder "turn".
func ParsePath(s string) (Path, error) {
	switch s {
	case "udp":
		return PathUDP, nil
	case "tcp":
		return PathTCP, nil
	case "turn":
		return PathTURN, nil
	}
	return 0, errors.New("weg: udp, tcp oder turn")
}

// Stream meldet einen TCP-artigen Weg (Head-of-Line-Blocking moeglich).
func (p Path) Stream() bool { return p != PathUDP }

// Device ist die Geraeteklasse (aus dem Token, sonst Hinweis des Clients).
type Device uint8

const (
	Desktop Device = iota
	Mobile
)

// ParseDevice liest "desktop" oder "mobile" (Standard desktop).
func ParseDevice(s string) Device {
	switch s {
	case "mobile", "handy", "phone", "tablet":
		return Mobile
	}
	return Desktop
}

func (d Device) String() string {
	if d == Mobile {
		return "mobile"
	}
	return "desktop"
}

// Role eines Beins. Auf TCP hat ein Geraet zwei Beine (H1): die Ton-Leitung
// und die Bild-Leitung. Auf UDP eines mit BUNDLE.
type Role uint8

const (
	RoleBoth  Role = iota // Ton und Bild (UDP)
	RoleAudio             // Ton-Leitung
	RoleVideo             // Bild-Leitung (Kamera, Bildschirm)
)

// ParseRole liest "both", "audio" oder "video".
func ParseRole(s string) (Role, error) {
	switch s {
	case "both", "":
		return RoleBoth, nil
	case "audio":
		return RoleAudio, nil
	case "video":
		return RoleVideo, nil
	}
	return 0, errors.New("rolle: both, audio oder video")
}

func (r Role) String() string {
	switch r {
	case RoleAudio:
		return "audio"
	case RoleVideo:
		return "video"
	}
	return "both"
}

func (r Role) audio() bool { return r != RoleVideo }
func (r Role) video() bool { return r != RoleAudio }

// Pub sind die Sende-Rechte aus dem Token.
type Pub uint8

const (
	PubAudio  Pub = 1 << iota // "a"
	PubVideo                  // "v" Kamera
	PubScreen                 // "s" Bildschirm
)

// ParsePub liest die Liste ["a","v","s"].
func ParsePub(list []string) Pub {
	var p Pub
	for _, x := range list {
		switch x {
		case "a":
			p |= PubAudio
		case "v":
			p |= PubVideo
		case "s":
			p |= PubScreen
		}
	}
	return p
}

// Grant ist ein geprueftes Medien-Token (Konzept §2.4). Die Kennungen sind
// je Knoten zufaellig; Konto- oder Geraetenamen kommen hier nie an.
type Grant struct {
	Node    string    // Knotenname, fuer den das Token gilt ("" = egal)
	Room    string    // call_ref
	Peer    string    // Geraet im Anruf (mehrere Beine je Geraet)
	Leg     string    // leg_ref: ein Bein je leg_ref, ein zweites ersetzt das erste
	Role    Role      // Ton-, Bild- oder beides
	Pub     Pub       // Sende-Rechte
	MaxKbit int       // hoechste Eingangsrate des Beins (0 = Standard)
	UDPOK   bool      // UDP-Weg erlaubt
	Device  Device    // Geraeteklasse (Last-N)
	Exp     time.Time // Ablauf (Token +60 s)
}

// Verifier prueft ein Medien-Token. Die Ed25519-Pruefung kommt mit P3; bis
// dahin gibt es nur den Entwicklungs-Pruefer im Knoten (HORN_DEV=1).
type Verifier interface {
	Verify(token string) (Grant, error)
}

// VerifierFunc macht aus einer Funktion einen Verifier.
type VerifierFunc func(token string) (Grant, error)

// Verify ruft f auf.
func (f VerifierFunc) Verify(token string) (Grant, error) { return f(token) }

// Fehler der API.
var (
	ErrNoVerifier = errors.New("kein token-pruefer")
	ErrExpired    = errors.New("token abgelaufen")
	ErrWrongNode  = errors.New("token fuer anderen knoten")
	ErrNoRoom     = errors.New("raum unbekannt")
	ErrRoomExists = errors.New("raum existiert")
	ErrFull       = errors.New("knoten oder raum voll")
	ErrNoUDP      = errors.New("udp nicht erlaubt")
	ErrClosed     = errors.New("geschlossen")
	ErrNotAllowed = errors.New("senden nicht erlaubt")
)

// Limits je Raum.
type Limits struct {
	MaxPeers     int // Geraete im Raum (Standard 50)
	LastNDesktop int // bewegte Kacheln am Desktop (Standard 9, H6)
	LastNMobile  int // bewegte Kacheln am Handy (Standard 4)
	Audio        int // gleichzeitige Tonstroeme je Empfaenger (Standard 3)
}

func (l Limits) withDefaults() Limits {
	if l.MaxPeers <= 0 {
		l.MaxPeers = 50
	}
	if l.LastNDesktop <= 0 {
		l.LastNDesktop = 9
	}
	if l.LastNMobile <= 0 {
		l.LastNMobile = 4
	}
	if l.Audio <= 0 {
		l.Audio = 3
	}
	return l
}

// LastN liefert die Kachelgrenze fuer die Geraeteklasse.
func (l Limits) LastN(d Device) int {
	if d == Mobile {
		return l.LastNMobile
	}
	return l.LastNDesktop
}

// Config des Knotens.
type Config struct {
	Name     string        // Knotenname (Pruefung Grant.Node)
	Verifier Verifier      // Token-Pruefung
	Tick     time.Duration // Takt der Steuerung (Standard 200 ms)
	MaxLegs  int           // Beine am Knoten (Standard 400)
	Limits   Limits        // Standardgrenzen je Raum

	// Bild auf TCP-Wegen (H4 + Messung G4): Kacheln mit hoechstens
	// TCPTileFPS Bildern/s (Standard 10), nur die untere Schicht.
	TCPTileFPS float64
	// TCPSpeakerFPS begrenzt Sprecher und Bildschirm auf TCP-Wegen (0 = alle
	// Bilder, Standard). Messung: Die Einfrier-Schwelle der Browser liegt
	// bei Mittel + 150 ms, eine kleinere Bildrate hilft daher erst unter
	// 10 Bildern/s; wirksam ist kuerzerer Stau (Nachlaeufer, netx).
	TCPSpeakerFPS float64
	// KeyframeEvery fordert vorbeugend Schluesselbilder je bewegter Quelle an
	// (0 = aus, Standard). Siehe Bericht zur Einfrier-Ursache.
	KeyframeEvery time.Duration
	// StartKbit ist die angenommene Downlink-Rate eines neuen Beins.
	StartKbit int
	// MaxDownKbit begrenzt die Schaetzung je Bein (Standard 25000).
	MaxDownKbit int

	// OnLayout meldet, welche Teilnehmenden ein Bein in welchem Platz bekommt
	// (fuer die Kachelansicht; die Saga-Steuerung reicht es ans Geraet).
	// Wird ausserhalb aller Sperren aufgerufen.
	OnLayout func(LayoutEvent)
	// OnLegClosed meldet ein vom Knoten beendetes Bein (ersetzt, entfernt,
	// Raum geschlossen), damit der Aufrufer die PeerConnection schliesst.
	OnLegClosed func(l *Leg, why string)
}

func (c Config) withDefaults() Config {
	if c.Tick <= 0 {
		c.Tick = 200 * time.Millisecond
	}
	if c.MaxLegs <= 0 {
		c.MaxLegs = 400
	}
	if c.TCPTileFPS <= 0 {
		c.TCPTileFPS = 10
	}

	if c.StartKbit <= 0 {
		c.StartKbit = 1200
	}
	if c.MaxDownKbit <= 0 {
		c.MaxDownKbit = 25000
	}
	c.Limits = c.Limits.withDefaults()
	return c
}

// Want beschreibt die Empfangsplaetze, die ein Bein anbietet (Reihenfolge
// der Empfangs-Transceiver im Angebot): erst Ton, dann Sprecher,
// Bildschirm, Kacheln. Last-N kappt die Kacheln nach Geraeteklasse.
type Want struct {
	Audio   int
	Speaker bool
	Screen  bool
	Tiles   int
}

// Slot ist ein Platz eines Beins in der Layout-Meldung.
type Slot struct {
	Index  int    `json:"i"`     // Reihenfolge wie Leg.Outs
	Class  string `json:"class"` // audio | speaker | screen | tile
	Peer   string `json:"peer,omitempty"`
	CSRC   uint32 `json:"csrc,omitempty"` // steht auch in jedem RTP-Paket
	Layer  string `json:"layer,omitempty"`
	MaxTID uint8  `json:"tid"`
	Paused bool   `json:"paused,omitempty"`
}

// LayoutEvent meldet die Belegung eines Beins.
type LayoutEvent struct {
	Room, Peer, Leg string
	Slots           []Slot
	EstKbit         int
}

// Load sind die Lastzahlen des Knotens (Lastmeldung §2.6).
type Load struct {
	Rooms     int     `json:"calls"`
	Peers     int     `json:"peers"`
	Legs      int     `json:"legs"`
	UDPLegs   int     `json:"udp_legs"`
	UDPShare  float64 `json:"udp_share"`
	Sources   int     `json:"sources"`
	Outs      int     `json:"outs"`
	FwdP95us  float64 `json:"fwd_p95_us"`
	Drops     uint64  `json:"drops"`  // verworfene Rahmen gesamt
	Pauses    uint64  `json:"pauses"` // Bildpausen gesamt
	PLIp50ms  float64 `json:"pli_p50_ms"`
	PLIp95ms  float64 `json:"pli_p95_ms"`
	WriteStls uint64  `json:"write_stalls"`
	DownKbit  int     `json:"down_kbit"` // Summe der Schaetzungen
}
