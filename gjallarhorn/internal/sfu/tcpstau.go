package sfu

import "time"

// Ende-zu-Ende-Stausignal fuer TCP-Wege (ICE-TCP, TURN-TLS; Saga 2.5, HORN_TCP_STAU).
//
// Auf TCP-Wegen schaetzt der Knoten aus dem Alter seiner eigenen Warteschlange (estimator.update). Das sieht einen
// engen Downlink erst, wenn die Puffer ausserhalb des Knotens voll sind: Warteschlange der Leitung, Empfangsfenster
// der TCP-Verbindung (gemessen bei 600 kbit/s: Bild-Latenz ~1,5 s, p95 ~3 s). Das Geraet meldet aber per transport-cc,
// wann jedes Paket ankam - daraus entstehen dieselben Groessen wie beim eigenen Stau-Detektor (internal/bwe): die
// Schlange unterwegs (Laufzeit ueber der kleinsten der letzten 30 s) und gesendete gegen bestaetigte Rate derselben
// Pakete (waechst die Schlange, dehnt sich die Ankunft).
//
// Regel (TCPSig, e2eStep):
//   - Stau: Schlange ueber congAge in e2eRun Takten in Folge UND sie waechst (gesendet > e2eGrow x bestaetigt). Dann
//     wie bei der eigenen Warteschlange herunter - auf 85 % der bestaetigten Rate (die gesendete liegt bei wachsender
//     Schlange ueber dem, was die Leitung durchlaesst).
//   - Abbau: nach einem solchen Stau geht es e2eDrain lang nicht nach oben, solange die Schlange noch ueber clearAge
//     liegt (sonst stiege die Schaetzung, waehrend die Leitung noch abbaut).
//   - Eine stehende Schlange, die nicht waechst, ist kein Stau: Eine fremde Last auf derselben Leitung (Download)
//     haelt sie voll, und kein Herunterschalten des Knotens leert sie. Dort gilt weiter nur die eigene Warteschlange.
//   - Ohne frische Rueckmeldung (aelter als e2eStale) gibt es kein Signal - alles wie ohne Schalter.
//
// Nur fuer Beine ohne GCC (Leg.Ext == nil): UDP bleibt unveraendert.

// TCPSig: Was die juengsten transport-cc-Rueckmeldungen eines TCP-Beins zeigen (Leg.ExtQueue).
type TCPSig struct {
	OK       bool          // Basis vorhanden und Rueckmeldung frisch genug
	Queue    time.Duration // Schlange unterwegs: kleinste Laufzeit der juengsten Pakete ueber der Basis
	SentBps  float64       // gesendete Rate der Pakete der letzten rund 1 s (Sendezeit) ...
	AckedBps float64       // ... und ihre bestaetigte Rate (Ankunftszeit); 0 = unbekannt
	FbAge    time.Duration // Alter der letzten Rueckmeldung
}

const (
	e2eRun   = 2                      // Takte in Folge mit Schlange ueber congAge
	e2eGrow  = 1.05                   // gesendet ueber so viel mal bestaetigt: die Schlange waechst
	e2eDrain = 5 * time.Second        // nach einem Stau: so lange kein Schritt nach oben, solange die Schlange steht
	e2eStale = 600 * time.Millisecond // Rueckmeldung aelter: kein Signal
)

// TCPStauStats: Zaehler des Ende-zu-Ende-Signals eines Beins (GET /api/sfu/leg/{id}).
type TCPStauStats struct {
	On       bool    // das Bein hat das Signal
	QueueMs  float64 // Schlange im letzten Takt (-1 = kein Signal)
	SentKbit float64
	AckKbit  float64
	Downs    uint64 // Senkungen wegen des Signals
	Holds    uint64 // Takte, in denen das Signal einen Schritt nach oben verhindert hat
	Stale    uint64 // Takte ohne frische Rueckmeldung
}

// e2eStep wertet das Signal eines Takts: liefert das Alter, mit dem die Schaetzung rechnet (mindestens das der
// eigenen Warteschlange), und die bestaetigte Rate, auf die bei Stau gesenkt wird (0 = die gesendete).
func (e *estimator) e2eStep(s TCPSig, age time.Duration, now int64) (time.Duration, float64) {
	e.e2eOn = true
	st := &e.e2e
	if !s.OK || s.FbAge > e2eStale {
		st.QueueMs = -1
		st.Stale++
		e.e2eN = 0
		return age, 0
	}
	st.QueueMs = float64(s.Queue) / 1e6
	st.SentKbit, st.AckKbit = s.SentBps/1000, s.AckedBps/1000
	grow := s.AckedBps > 0 && s.SentBps > s.AckedBps*e2eGrow
	if s.Queue > congAge {
		e.e2eN++
	} else {
		e.e2eN = 0
	}
	switch {
	case e.e2eN >= e2eRun && grow:
		e.e2eHit = true
		if age <= congAge {
			e.e2eAge = true
			age = s.Queue
		}
		return age, s.AckedBps
	case s.Queue > clearAge && (grow || now < e.e2eDrainTo):
		// Schlange waechst (noch unter der Schwelle bzw. erst einen Takt) oder baut nach einem Stau noch ab: halten
		if age < clearAge {
			e.e2eHold = true
			st.Holds++
			age = clearAge
		}
	}
	return age, 0
}

// e2eDown: Die Schaetzung ist in diesem Takt wegen des Signals gesenkt worden.
func (e *estimator) e2eDown(now int64) {
	e.e2e.Downs++
	e.e2eDrainTo = now + int64(e2eDrain)
	e.e2eN = 0
}

// TCPStau liefert die Zaehler des Ende-zu-Ende-Signals.
func (l *Leg) TCPStau() TCPStauStats {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	s := l.est.e2e
	s.On = l.est.e2eOn
	return s
}
