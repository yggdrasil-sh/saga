package bwe

import (
	"sync"

	"github.com/pion/rtcp"
)

// RingSize: gemerkte abgehende Pakete je Bein (Zweierpotenz). 4096 Eintraege sind rund 20 s bei 200 Paketen/s.
const RingSize = 4096

// sentMaxAge: Aeltere Eintraege gelten nicht mehr (die 16-bit-Folgenummer koennte inzwischen umgelaufen sein).
const sentMaxAge = int64(30e9)

type sent struct {
	ns   int64
	size int32
	seq  uint16
	kind Kind
	fl   uint8 // slUsed, slAcked, slLost
}

const (
	slUsed  = 1 << iota
	slAcked // Ankunft gemeldet
	slLost  // als verloren gemeldet
)

// Counters: Zaehler des Mitlesers und des Detektors (GET /api/sfu/leg/{id}).
type Counters struct {
	Sent       [kindN]uint64 // abgehende Pakete je Art
	Feedbacks  uint64        // verarbeitete Rueckmeldungen
	Bad        uint64        // in sich unstimmige Rueckmeldungen
	Acked      uint64        // als angekommen gemeldete Pakete
	Lost       uint64        // als verloren gemeldete Pakete
	Dup        uint64        // schon gemeldete Pakete (doppelte bzw. ueberlappende Rueckmeldung)
	Recovered  uint64        // erst verloren, dann doch als angekommen gemeldet (zaehlt nicht neu)
	Unknown    uint64        // gemeldete Folgenummern ohne Eintrag (nicht von uns, zu alt, ueberschrieben)
	Windows    uint64        // gewertete Messfenster
	Thin       uint64        // Fenster mit zu wenigen Paketen
	Late       uint64        // Pakete vor dem laufenden Fenster
	ToUnknown  uint64        // Wechsel nach unbekannt
	ToFree     uint64        // ... frei
	ToWarn     uint64        // ... Warnung
	ToCong     uint64        // ... Stau
	Resets     uint64        // Basis zurueckgesetzt
	LostByKind [kindN]uint64 // verlorene Pakete je Art
	DupUnacked uint64        // Doppel (KindDup), die als nicht angekommen gemeldet wurden - zaehlen nicht als Verlust
}

// Mitleser: Ring der abgehenden Pakete, Zuordnung der Rueckmeldungen, Detektor. Nebenlaeufig benutzbar. Entscheidet
// nichts - er liest nur mit.
type Mitleser struct {
	mu   sync.Mutex
	ring [RingSize]sent
	det  *Detector
	ref  refUnwrap
	cnt  Counters
	acks []Ack    // Arbeitspuffer einer Rueckmeldung
	chg  []Change // Arbeitspuffer

	// OnChange wird je Zustandswechsel ausserhalb der Sperre gerufen (optional; vor dem ersten Paket setzen bzw.
	// ueber SetOnChange).
	onChange func(Change)
}

// NewMitleser baut einen Mitleser mit eigenem Detektor.
func NewMitleser(cfg Config) *Mitleser {
	return &Mitleser{det: NewDetector(cfg)}
}

// SetOnChange setzt den Rueckruf fuer Zustandswechsel.
func (m *Mitleser) SetOnChange(f func(Change)) {
	m.mu.Lock()
	m.onChange = f
	m.mu.Unlock()
}

// Sent merkt ein abgehendes Paket: TWCC-Folgenummer, Sendezeit (Prozessuhr, ns), Groesse, Art.
func (m *Mitleser) Sent(nowNs int64, seq uint16, size int, kind Kind) {
	m.mu.Lock()
	m.ring[seq&(RingSize-1)] = sent{ns: nowNs, size: int32(size), seq: seq, kind: kind, fl: slUsed}
	if kind < kindN {
		m.cnt.Sent[kind]++
	}
	c := m.det.OnSent(nowNs)
	f := m.onChange
	m.mu.Unlock()
	if c != nil && f != nil {
		f(*c)
	}
}

// Feedback verarbeitet eine transport-cc-Rueckmeldung: je gemeldetem Paket, das der Ring kennt und das noch nicht
// gemeldet war, geht (Sendezeit, Ankunft oder "verloren", Groesse, Art) an den Detektor. Nicht gemeldete Pakete
// zaehlen nicht als verloren.
func (m *Mitleser) Feedback(nowNs int64, fb *rtcp.TransportLayerCC) {
	m.mu.Lock()
	off := m.ref.offset(fb.ReferenceTime)
	m.acks = m.acks[:0]
	ok := Unpack(fb, func(seq uint16, st int, arrUs int64) {
		e := &m.ring[seq&(RingSize-1)]
		if e.fl&slUsed == 0 || e.seq != seq || nowNs-e.ns > sentMaxAge {
			m.cnt.Unknown++
			return
		}
		switch {
		case e.fl&slAcked != 0:
			m.cnt.Dup++
			return
		case e.fl&slLost != 0:
			if st == StLost {
				m.cnt.Dup++
			} else {
				// Nachzuegler: war schon als verloren gewertet - das Fenster ist geschlossen, nicht neu zaehlen
				e.fl |= slAcked
				m.cnt.Recovered++
			}
			return
		}
		a := Ack{SentNs: e.ns, Size: int(e.size), Kind: e.kind}
		if e.kind == KindDup && st == StLost {
			// Ein Doppel verwirft der Empfaenger am SRTP-Wiederholungsschutz, bevor er es bestaetigt - das ist kein Verlust
			// der Leitung. (Kommt es an, weil das Original verloren ging, zaehlt es wie jedes Paket.)
			e.fl |= slLost
			m.cnt.DupUnacked++
			return
		}
		switch st {
		case StLost:
			e.fl |= slLost
			a.Lost = true
			m.cnt.Lost++
			if e.kind < kindN {
				m.cnt.LostByKind[e.kind]++
			}
		case StNoTime:
			e.fl |= slAcked
			a.NoTime = true
			m.cnt.Acked++
		default:
			e.fl |= slAcked
			a.ArrivalUs = arrUs + off
			m.cnt.Acked++
		}
		m.acks = append(m.acks, a)
	})
	m.cnt.Feedbacks++
	if !ok {
		m.cnt.Bad++
	}
	m.chg = m.det.Feedback(nowNs, m.acks, m.chg[:0])
	var chg []Change
	f := m.onChange
	if len(m.chg) > 0 && f != nil {
		chg = append(chg, m.chg...)
	}
	m.mu.Unlock()
	for _, c := range chg {
		f(c)
	}
}

// Reset: Wegwechsel - Basis des Detektors verwerfen.
func (m *Mitleser) Reset(nowNs int64) {
	m.mu.Lock()
	c := m.det.Reset(nowNs)
	m.cnt.Resets++
	f := m.onChange
	m.mu.Unlock()
	if c != nil && f != nil {
		f(*c)
	}
}

// Snapshot liefert den Zustand des Detektors.
func (m *Mitleser) Snapshot(nowNs int64) Snap {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.det.Snapshot(nowNs)
}

// Span liefert das Urteil des Detektors ueber die Pakete, die zwischen fromNs und toNs gesendet wurden (Probe).
func (m *Mitleser) Span(fromNs, toNs int64) Verdict {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.det.Span(fromNs, toNs)
}

// Tip: Was die zuletzt gemeldeten Pakete zeigen (Detector.Tip).
func (m *Mitleser) Tip(nowNs int64) Tip {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.det.Tip(nowNs)
}

// Wins: die Messfenster der letzten spanNs (Detector.Wins).
func (m *Mitleser) Wins(spanNs int64) []WinStat {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.det.Wins(spanNs)
}

// Rates: gesendete und bestaetigte Rate der Pakete, die zwischen fromNs und toNs gesendet wurden (Detector.Rates).
func (m *Mitleser) Rates(fromNs, toNs int64) Rates {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.det.Rates(fromNs, toNs)
}

// Counters liefert die Zaehler.
func (m *Mitleser) Counters() Counters {
	m.mu.Lock()
	defer m.mu.Unlock()
	c := m.cnt
	d := m.det
	c.Windows, c.Thin, c.Late = d.Windows, d.Thin, d.Late
	c.ToUnknown, c.ToFree, c.ToWarn, c.ToCong = d.Changes[Unknown], d.Changes[Free], d.Changes[Warn], d.Changes[Congested]
	return c
}
