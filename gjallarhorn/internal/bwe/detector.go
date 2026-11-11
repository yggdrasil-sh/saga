// Package bwe: eigener Stau-Detektor des Knotens ("Weg B", Stufe 0 - laeuft nur mit und schreibt mit).
//
// Der Mitleser (mitleser.go) merkt sich je abgehendem Paket Folgenummer, Sendezeit, Groesse und Art, ordnet die
// transport-cc-Rueckmeldungen des Empfaengers zu und gibt sie an den Detektor (diese Datei). Der Detektor ist ein
// reiner Rechenbaustein: Rueckmeldungen hinein, Zustand heraus - ohne Netz, ohne Uhr (die Zeit kommt als Zahl herein).
//
// Verfahren: Pakete werden nach Sendezeit zu Messfenstern gebuendelt. Je Paket zaehlt "Ankunft minus Sendezeit" (die
// Uhren haben einen unbekannten Versatz, nur Unterschiede zaehlen). Die Basis ist der kleinste Wert der letzten 30 s;
// die Schlange eines Fensters ist sein KLEINSTER Paketwert minus Basis - Jitter hebt das Minimum nicht, eine stehende
// Schlange schon. Dazu der Verlustanteil des Fensters. Aus Folgen solcher Fenster entsteht der Zustand frei /
// Warnung / Stau; ohne Rueckmeldung bei laufendem Senden "unbekannt".
package bwe

import "slices"

// Kind: Art eines abgehenden Pakets.
type Kind uint8

const (
	KindVideo Kind = iota
	KindAudio
	KindRTX // Wiederholung (am Abgriff der Huelle derzeit nicht sichtbar, siehe NODE.md)
	KindPad // Fuellpaket einer Probe
	kindN
)

// State: Leitungszustand laut Detektor.
type State uint8

const (
	Unknown State = iota // keine (frische) Rueckmeldung
	Free
	Warn
	Congested
)

func (s State) String() string {
	switch s {
	case Free:
		return "frei"
	case Warn:
		return "warnung"
	case Congested:
		return "stau"
	}
	return "unbekannt"
}

// Gruende eines Zustandswechsels.
const (
	WhyQueue   = "schlange"
	WhyLoss    = "verlust"
	WhyClear   = "leer"
	WhySilent  = "stille"  // keine Rueckmeldung bei laufendem Senden
	WhyFirst   = "beginn"  // erstes Fenster bzw. erstes nach der Stille
	WhyReset   = "wechsel" // Basis zurueckgesetzt (Wegwechsel)
	whyNothing = ""
)

// Config: Schwellen des Detektors (Startwerte aus dem Konzept; Laborgroessen).
type Config struct {
	WinNs      int64   // Mindestdauer eines Messfensters (Sendezeit)
	WinPkts    int     // Mindestzahl gemeldeter Pakete je Fenster
	WinMaxNs   int64   // spaetestens dann schliesst ein Fenster (duenner Strom)
	WinMinPkts int     // Fenster mit weniger gemeldeten Paketen zaehlen nicht (unbestimmt)
	BaseSec    int     // Basis = kleinste Laufzeit der letzten BaseSec Sekunden (hoechstens baseMax)
	QueueHiUs  int64   // Schlange ab hier: "Schlange"
	QueueLoUs  int64   // Schlange unter hier: "leer"
	LossHi     float64 // Verlustanteil eines Fensters ab hier: "hoch"
	LossLo     float64 // darunter: "niedrig"
	WarnQueue  int     // Fenster "Schlange" bis zur Warnung
	WarnLoss   int     // Fenster "Verlust hoch" bis zur Warnung
	CongQueue  int     // ... bis zum Stau
	CongLoss   int
	ClearWarn  int   // Fenster "leer und Verlust niedrig" in Folge: Warnung -> frei
	ClearCong  int   // ... Stau -> frei
	SilentNs   int64 // so lange keine Rueckmeldung bei laufendem Senden: unbekannt
	RateNs     int64 // Spanne fuer den laufenden bestaetigten Durchsatz
	ProbeMinNs int64 // Spanne fuer den Durchsatz einer Probe (Spitze ueber mindestens so lange)
}

// Defaults liefert die Startwerte (Konzept 4.2).
func Defaults() Config {
	return Config{
		WinNs: 100e6, WinPkts: 20, WinMaxNs: 500e6, WinMinPkts: 5, BaseSec: 30,
		QueueHiUs: 50_000, QueueLoUs: 20_000, LossHi: 0.08, LossLo: 0.03,
		WarnQueue: 2, WarnLoss: 3, CongQueue: 4, CongLoss: 6, ClearWarn: 3, ClearCong: 5,
		SilentNs: 1e9, RateNs: 1e9, ProbeMinNs: 400e6,
	}
}

// Ack: ein vom Empfaenger gemeldetes Paket.
type Ack struct {
	SentNs    int64 // Sendezeit (Prozessuhr des Knotens, ns)
	ArrivalUs int64 // Ankunft (Uhr des Empfaengers, us); ohne Bedeutung bei Lost bzw. NoTime
	Size      int   // Bytes (RTP-Kopf + Nutzlast)
	Kind      Kind
	Lost      bool
	NoTime    bool // empfangen, aber ohne Ankunftszeit gemeldet
}

// Change: ein Zustandswechsel.
type Change struct {
	AtNs     int64
	From, To State
	Why      string
	Snap     Snap
}

// Snap: Zustand zum Lesen.
type Snap struct {
	State    State
	Why      string  // Grund des letzten Wechsels
	QueueUs  int64   // Schlange des letzten gewerteten Fensters
	Loss     float64 // Verlustanteil des letzten gewerteten Fensters
	AckedBps int64   // bestaetigter Durchsatz der letzten RateNs
	CapBps   int64   // Durchsatz der Fenster, die den letzten Stau gezeigt haben (0 = noch kein Stau)
	AgeNs    int64   // Alter der letzten Rueckmeldung (-1 = noch keine)
}

// Verdict: Urteil ueber eine Spanne (Probe).
type Verdict struct {
	Result   string  // VerdictCong, VerdictFree, VerdictUnclear
	QueueUs  int64   // zweithoechste Fenster-Schlange der Spanne (bei weniger als 3 Fenstern die hoechste)
	Loss     float64 // Verlustanteil ueber alle Fenster der Spanne
	Bps      int64   // bestaetigter Durchsatz: Spitze ueber mindestens ProbeMinNs
	Windows  int
	Pkts     int
	WorstSt  State
	PadPkts  int
	LostPkts int
}

const (
	VerdictCong    = "staut"
	VerdictFree    = "staut-nicht"
	VerdictUnclear = "unklar"
)

// win: ein Messfenster.
type win struct {
	startNs, endNs int64 // Sendezeit erstes/letztes Paket
	n, lost, pad   int
	bytes          int64 // empfangene Bytes
	minD           int64 // kleinste Laufzeit (us), nur bei timed > 0
	timed          int
	arr0, arr1     int64 // kleinste/groesste Ankunft (us)
	queueUs        int64 // nach dem Schliessen
	valid          bool  // genug Pakete fuer ein Urteil
	state          State // Zustand nach diesem Fenster
}

const (
	histN   = 128 // geschlossene Fenster (mind. 12,8 s)
	baseMax = 64
)

// Detector: nicht nebenlaeufig benutzbar (der Mitleser haelt die Sperre).
type Detector struct {
	cfg Config

	cur     win
	curOpen bool

	hist  [histN]win
	histN int // Zahl je geschlossener Fenster (laufend); Index = (n-1) % histN

	// Basis: kleinste Laufzeit je Sekunde Sendezeit
	baseSec [baseMax]int64 // Sekunde (Sendezeit/1e9), -1 = leer
	baseMin [baseMax]int64

	state    State
	why      string
	qRun     int   // Fenster "Schlange" seit dem letzten leeren
	lRun     int   // Fenster "Verlust hoch" seit dem letzten mit niedrigem Verlust
	clearRun int   // Fenster "leer und Verlust niedrig" in Folge
	runFrom  int   // histN beim Beginn der laufenden Stau-Folge (fuer CapBps)
	capBps   int64 // siehe Snap.CapBps
	lastQ    int64
	lastLoss float64

	lastFbNs   int64 // letzte Rueckmeldung (0 = keine)
	minSentNs  int64 // Pakete, die davor gesendet wurden, zaehlen nicht (Reset: noch auf dem alten Weg unterwegs)
	lastSentNs int64

	// Zaehler
	Windows, Thin, Late uint64
	Changes             [4]uint64 // Wechsel in den Zustand
}

// NewDetector baut einen Detektor mit den gegebenen Schwellen.
func NewDetector(cfg Config) *Detector {
	if cfg.BaseSec <= 0 || cfg.BaseSec > baseMax {
		cfg.BaseSec = 30
	}
	d := &Detector{cfg: cfg}
	d.clearBase()
	return d
}

func (d *Detector) clearBase() {
	for i := range d.baseSec {
		d.baseSec[i] = -1
	}
}

// Reset verwirft Basis und laufendes Fenster (Wegwechsel: die Laufzeit des neuen Wegs ist eine andere). Der Zustand
// wird unbekannt, bis das erste Fenster auf dem neuen Weg gewertet ist; Pakete von vor dem Wechsel zaehlen nicht mehr.
func (d *Detector) Reset(nowNs int64) *Change {
	d.clearBase()
	d.curOpen = false
	d.minSentNs = nowNs
	d.qRun, d.lRun, d.clearRun = 0, 0, 0
	return d.set(nowNs, Unknown, WhyReset)
}

// OnSent meldet ein abgehendes Paket (fuer "keine Rueckmeldung bei laufendem Senden"). Liefert einen Wechsel nach
// unbekannt, wenn die Rueckmeldungen ausbleiben.
func (d *Detector) OnSent(nowNs int64) *Change {
	d.lastSentNs = nowNs
	if d.state != Unknown && d.lastFbNs > 0 && nowNs-d.lastFbNs > d.cfg.SilentNs {
		d.curOpen = false
		d.qRun, d.lRun, d.clearRun = 0, 0, 0
		return d.set(nowNs, Unknown, WhySilent)
	}
	return nil
}

// Feedback verarbeitet die Pakete einer Rueckmeldung (in Reihenfolge der Folgenummern). out nimmt Zustandswechsel auf.
func (d *Detector) Feedback(nowNs int64, acks []Ack, out []Change) []Change {
	d.lastFbNs = nowNs
	for i := range acks {
		a := &acks[i]
		if d.curOpen && a.SentNs < d.cur.startNs || a.SentNs < d.minSentNs {
			d.Late++ // Nachzuegler vor dem laufenden Fenster bzw. von vor dem Wegwechsel: zaehlt nicht
			continue
		}
		if d.curOpen && d.full(a.SentNs) {
			if c := d.closeWin(nowNs); c != nil {
				out = append(out, *c)
			}
		}
		if !d.curOpen {
			d.cur = win{startNs: a.SentNs}
			d.curOpen = true
		}
		w := &d.cur
		w.endNs = a.SentNs
		w.n++
		if a.Kind == KindPad {
			w.pad++
		}
		if a.Lost {
			w.lost++
			continue
		}
		w.bytes += int64(a.Size)
		if a.NoTime {
			continue
		}
		dl := a.ArrivalUs - a.SentNs/1000
		if w.timed == 0 || dl < w.minD {
			w.minD = dl
		}
		if w.timed == 0 || a.ArrivalUs < w.arr0 {
			w.arr0 = a.ArrivalUs
		}
		if w.timed == 0 || a.ArrivalUs > w.arr1 {
			w.arr1 = a.ArrivalUs
		}
		w.timed++
		d.baseAdd(a.SentNs, dl)
	}
	return out
}

// full: Gehoert ein Paket mit dieser Sendezeit schon ins naechste Fenster?
func (d *Detector) full(sentNs int64) bool {
	age := sentNs - d.cur.startNs
	return age >= d.cfg.WinMaxNs || age >= d.cfg.WinNs && d.cur.n >= d.cfg.WinPkts
}

func (d *Detector) baseAdd(sentNs, dl int64) {
	sec := sentNs / 1e9
	i := int(sec % baseMax)
	if i < 0 {
		i += baseMax
	}
	if d.baseSec[i] != sec {
		d.baseSec[i], d.baseMin[i] = sec, dl
	} else if dl < d.baseMin[i] {
		d.baseMin[i] = dl
	}
}

// base liefert die kleinste Laufzeit der letzten BaseSec Sekunden vor (und einschliesslich) sentNs.
func (d *Detector) base(sentNs int64) (int64, bool) {
	sec := sentNs / 1e9
	var b int64
	ok := false
	for i := range d.baseSec {
		s := d.baseSec[i]
		if s < 0 || s > sec || sec-s >= int64(d.cfg.BaseSec) {
			continue
		}
		if !ok || d.baseMin[i] < b {
			b, ok = d.baseMin[i], true
		}
	}
	return b, ok
}

// closeWin wertet das laufende Fenster und schreibt den Zustand fort.
func (d *Detector) closeWin(nowNs int64) *Change {
	w := d.cur
	d.curOpen = false
	d.Windows++
	c := d.cfg
	w.valid = w.n >= c.WinMinPkts
	loss := 0.0
	if w.n > 0 {
		loss = float64(w.lost) / float64(w.n)
	}
	qKnown := false
	if w.timed > 0 {
		if b, ok := d.base(w.endNs); ok {
			w.queueUs, qKnown = w.minD-b, true
		}
	}
	var ch *Change
	if !w.valid {
		d.Thin++
	} else {
		qHi := qKnown && w.queueUs > c.QueueHiUs
		qLo := qKnown && w.queueUs < c.QueueLoUs
		lHi := loss >= c.LossHi
		lLo := loss < c.LossLo
		if d.qRun == 0 && d.lRun == 0 && (qHi || lHi) {
			d.runFrom = d.histN
		}
		// Folgen: ein Fenster im Zwischenbereich zaehlt nicht und setzt nicht zurueck
		if qHi {
			d.qRun++
		} else if qLo {
			d.qRun = 0
		}
		if lHi {
			d.lRun++
		} else if lLo {
			d.lRun = 0
		}
		if qLo && lLo {
			d.clearRun++
		} else {
			d.clearRun = 0
		}
		d.lastQ, d.lastLoss = w.queueUs, loss
		to, why := d.state, whyNothing
		bad := WhyLoss // Grund eines Wechsels nach Warnung bzw. Stau
		if d.qRun >= c.CongQueue || d.qRun >= c.WarnQueue && d.lRun < c.CongLoss {
			bad = WhyQueue
		}
		switch {
		case d.qRun >= c.CongQueue || d.lRun >= c.CongLoss:
			to, why = Congested, bad
		case d.state == Congested:
			if d.clearRun >= c.ClearCong {
				to, why = Free, WhyClear
			}
		case d.qRun >= c.WarnQueue || d.lRun >= c.WarnLoss:
			to, why = Warn, bad
		case d.state == Warn:
			if d.clearRun >= c.ClearWarn {
				to, why = Free, WhyClear
			}
		case d.state == Unknown:
			to, why = Free, WhyFirst
		}
		w.state = to
		d.hist[d.histN%histN] = w
		d.histN++
		if to != d.state {
			if to == Congested {
				d.capBps = d.rateOver(d.runFrom, d.histN)
			}
			ch = d.set(nowNs, to, why)
		}
		return ch
	}
	w.state = d.state
	d.hist[d.histN%histN] = w
	d.histN++
	return nil
}

func (d *Detector) set(nowNs int64, to State, why string) *Change {
	if to == d.state {
		return nil
	}
	from := d.state
	d.state, d.why = to, why
	d.Changes[to]++
	return &Change{AtNs: nowNs, From: from, To: to, Why: why, Snap: d.Snapshot(nowNs)}
}

// at liefert das geschlossene Fenster Nummer i (0 = erstes ueberhaupt); i muss in [histN-len, histN) liegen.
func (d *Detector) at(i int) *win { return &d.hist[i%histN] }

func (d *Detector) lo() int { return max(0, d.histN-histN) }

// rateOver: bestaetigter Durchsatz der Fenster [from, to) in bit/s: empfangene Bytes je Ankunftszeitspanne. Gibt es
// ein Fenster danach, reicht die Spanne bis zu dessen erster Ankunft (dann stimmt die Zaehlung an den Raendern);
// sonst bis zur letzten Ankunft der Fenster selbst.
func (d *Detector) rateOver(from, to int) int64 {
	from = max(from, d.lo())
	var bytes int64
	var a0, a1 int64
	have := false
	for i := from; i < to; i++ {
		w := d.at(i)
		if w.timed == 0 {
			continue
		}
		bytes += w.bytes
		if !have || w.arr0 < a0 {
			a0 = w.arr0
		}
		if !have || w.arr1 > a1 {
			a1 = w.arr1
		}
		have = true
	}
	if !have {
		return 0
	}
	if nx := d.next(to); nx != nil && nx.arr0 > a1 {
		a1 = nx.arr0
	} else if to-from > 1 {
		// kein Fenster danach: das letzte Fenster nur bis zu seiner ersten Ankunft zaehlen
		l := d.at(to - 1)
		if l.timed > 0 && l.arr0 > a0 {
			bytes -= l.bytes
			a1 = l.arr0
		}
	}
	if a1 <= a0 {
		return 0
	}
	return bytes * 8 * 1e6 / (a1 - a0)
}

// next liefert das naechste Fenster mit Ankunftszeit ab Index i (geschlossen oder das laufende), sonst nil.
func (d *Detector) next(i int) *win {
	for ; i < d.histN; i++ {
		if w := d.at(i); w.timed > 0 {
			return w
		}
	}
	if d.curOpen && d.cur.timed > 0 {
		return &d.cur
	}
	return nil
}

// Snapshot liefert den Zustand.
func (d *Detector) Snapshot(nowNs int64) Snap {
	s := Snap{State: d.state, Why: d.why, QueueUs: d.lastQ, Loss: d.lastLoss, CapBps: d.capBps, AgeNs: -1}
	if d.lastFbNs > 0 {
		s.AgeNs = nowNs - d.lastFbNs
	}
	// Fenster der letzten RateNs (Sendezeit)
	if d.histN > 0 {
		last := d.at(d.histN - 1)
		from := d.histN - 1
		for from > d.lo() && last.endNs-d.at(from-1).startNs <= d.cfg.RateNs {
			from--
		}
		s.AckedBps = d.rateOver(from, d.histN)
	}
	return s
}

// Span urteilt ueber die Fenster, deren Pakete zwischen fromNs und toNs gesendet wurden (eine Probe): staut bei
// Schlange ueber der Schwelle (in mindestens zwei Fenstern), hohem Verlust oder wenn der Detektor in der Spanne Stau
// gemeldet hat; staut nicht bei leerer Schlange, niedrigem Verlust und durchgehend "frei"; sonst unklar. Das noch
// laufende Fenster zaehlt mit, soweit es schon Rueckmeldungen hat.
func (d *Detector) Span(fromNs, toNs int64) Verdict {
	v := Verdict{Result: VerdictUnclear, WorstSt: Unknown}
	var qs []int64 // Schlangen der gewerteten Fenster
	lost, n := 0, 0
	first, last := -1, -1
	add := func(w *win, closed bool) {
		v.Windows++
		n += w.n
		lost += w.lost
		v.PadPkts += w.pad
		if !closed {
			return
		}
		if w.state > v.WorstSt {
			v.WorstSt = w.state
		}
		if w.valid && w.timed > 0 {
			qs = append(qs, w.queueUs)
		}
	}
	for i := d.lo(); i < d.histN; i++ {
		w := d.at(i)
		if w.endNs < fromNs || w.startNs > toNs {
			continue
		}
		if first < 0 {
			first = i
		}
		last = i
		add(w, true)
	}
	if d.curOpen && d.cur.startNs <= toNs && d.cur.endNs >= fromNs {
		add(&d.cur, false)
	}
	v.Pkts, v.LostPkts = n, lost
	if n > 0 {
		v.Loss = float64(lost) / float64(n)
	}
	qn := len(qs)
	if qn > 0 {
		slices.Sort(qs)
		v.QueueUs = qs[qn-1]
		if qn >= 3 {
			v.QueueUs = qs[qn-2]
		}
	}
	if first >= 0 {
		// Spitze des Durchsatzes ueber mindestens ProbeMinNs Sendezeit
		for i := first; i <= last; i++ {
			j := i
			for j < last && d.at(j).endNs-d.at(i).startNs < d.cfg.ProbeMinNs {
				j++
			}
			if r := d.rateOver(i, j+1); r > v.Bps {
				v.Bps = r
			}
			if j == last {
				break
			}
		}
	}
	c := d.cfg
	switch {
	case qn == 0 || n < c.WinMinPkts:
		// nichts Wertbares
	case v.QueueUs > c.QueueHiUs || v.Loss >= c.LossHi || v.WorstSt == Congested:
		v.Result = VerdictCong
	case v.QueueUs < c.QueueLoUs && v.Loss < c.LossLo && v.WorstSt == Free:
		v.Result = VerdictFree
	}
	return v
}
