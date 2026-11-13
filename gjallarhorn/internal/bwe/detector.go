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

import (
	"math"
	"slices"
)

// Kind: Art eines abgehenden Pakets.
type Kind uint8

const (
	KindVideo Kind = iota
	KindAudio
	KindRTX // Wiederholung (am Abgriff der Huelle derzeit nicht sichtbar, siehe NODE.md)
	KindPad // Fuellpaket einer Probe
	// KindDup: Doppel - ein Paket mit einer RTP-Folgenummer, die auf dieser SSRC gerade schon hinausging (die Quelle hat
	// es zweimal geliefert, z. B. als Fuell-Wiederholung des sendenden Browsers). Der Empfaenger verwirft es am
	// SRTP-Wiederholungsschutz, bevor er es zaehlt oder bestaetigt: Bleibt die Bestaetigung aus, ist das kein Verlust
	// der Leitung (Mitleser.Feedback).
	KindDup
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
	WinNs      int64 // Mindestdauer eines Messfensters (Sendezeit)
	WinPkts    int   // Mindestzahl gemeldeter Pakete je Fenster
	WinMaxNs   int64 // spaetestens dann schliesst ein Fenster (duenner Strom)
	WinMinPkts int   // Fenster mit weniger gemeldeten Paketen zaehlen nicht (unbestimmt)
	BaseSec    int   // Basis = kleinste Laufzeit der letzten BaseSec Sekunden (hoechstens baseMax)
	QueueHiUs  int64 // Schlange ab hier: "Schlange"
	QueueLoUs  int64 // Schlange unter hier: "leer"
	// Verlust wird nicht je Fenster beurteilt (20 Pakete: bei 5 % Zufallsverlust haben 26 % der Fenster 10 % und mehr),
	// sondern ueber die letzten LossPkts gemeldeten Pakete, hoechstens LossSpanNs zurueck (lossOver).
	LossHi      float64 // Verlustanteil ab hier: "hoch" (nur, wenn auch das Fenster selbst Verlust hat)
	LossLo      float64 // darunter: "niedrig" (Rueckkehr nach frei)
	LossPkts    int     // so viele gemeldete Pakete zurueck
	LossSpanNs  int64   // ... hoechstens so weit (Sendezeit)
	LossMinPkts int     // weniger gemeldete Pakete: Verlust unbestimmt (weder hoch noch niedrig)
	WarnQueue   int     // Fenster "Schlange" bis zur Warnung
	WarnLoss    int     // Fenster "Verlust hoch" bis zur Warnung
	CongQueue   int     // ... bis zum Stau
	CongLoss    int
	ClearWarn   int   // Fenster "leer und Verlust niedrig" in Folge: Warnung -> frei
	ClearCong   int   // ... Stau -> frei
	SilentNs    int64 // so lange keine Rueckmeldung bei laufendem Senden: unbekannt
	RateNs      int64 // Spanne fuer den laufenden bestaetigten Durchsatz
	ProbeMinNs  int64 // Spanne fuer den Durchsatz einer Probe (Spitze ueber mindestens so lange)
	GraceNs     int64 // Schonzeit nach Beinbeginn: so lange nach dem ersten gemeldeten Paket zaehlt Verlust nicht
	BaseYoungNs int64 // Basis juenger als das: Urteile "staut nicht" bzw. "widerlegt" gibt es noch nicht (unklar)
	// Spitze (Tip): die zuletzt gemeldeten Pakete - widerlegt der Detektor einen Stau, den GCC gerade meldet?
	TipPkts    int     // so viele juengste Pakete mit Ankunftszeit (kleinste Laufzeit ueber der Basis)
	TipSpanNs  int64   // ... hoechstens so weit auseinander gesendet
	TipQueueUs int64   // Schlange an der Spitze darunter: keine
	TipLossNs  int64   // Verlust an der Spitze: ueber so viel Sendezeit
	TipLossUp  float64 // ... hoechstens so viel ueber dem Verlust der 10 s davor
	TipAgeNs   int64   // letzte Rueckmeldung hoechstens so alt
	TipLagNs   int64   // juengstes gemeldetes Paket hoechstens so lange her gesendet
	// Urteil ueber eine Spanne (Span)
	SpanLossUp   float64 // Verlust der Spanne um so viel ueber dem der Zeit davor (und mindestens SpanLossMin): staut
	SpanLossMin  float64
	SpanLossSame float64 // hoechstens so viel darueber (bzw. unter SpanLossFree): Verlust unauffaellig
	SpanLossFree float64
	// Policer-Merkmal (Tip.Policer, Stufe 3b): Verlust, der mit der Senderate steigt bzw. in Folgen kommt
	PolSpanNs   int64   // ueber die Fenster der letzten so viel Sendezeit
	PolMinLost  int     // mindestens so viele verlorene Pakete darin (sonst kein Urteil)
	PolRunShare float64 // Anteil der Verluste in Folgen ab 3 Paketen: ab hier Policer
	PolCorr     float64 // ... oder Korrelation Senderate/Verlustanteil je Fenster ab hier,
	PolMinWins  int     // ueber mindestens so viele Fenster
}

// Defaults liefert die Startwerte (Konzept 4.2).
func Defaults() Config {
	return Config{
		WinNs: 100e6, WinPkts: 20, WinMaxNs: 500e6, WinMinPkts: 5, BaseSec: 30,
		QueueHiUs: 50_000, QueueLoUs: 20_000,
		LossHi: 0.12, LossLo: 0.08, LossPkts: 160, LossSpanNs: 3e9, LossMinPkts: 60,
		WarnQueue: 2, WarnLoss: 2, CongQueue: 4, CongLoss: 10, ClearWarn: 3, ClearCong: 5,
		SilentNs: 1e9, RateNs: 1e9, ProbeMinNs: 400e6,
		GraceNs: 3e9, BaseYoungNs: 2e9,
		TipPkts: 5, TipSpanNs: 300e6, TipQueueUs: 10_000, TipLossNs: 500e6, TipLossUp: 0.04, TipAgeNs: 400e6, TipLagNs: 600e6,
		SpanLossUp: 0.05, SpanLossMin: 0.08, SpanLossSame: 0.02, SpanLossFree: 0.03,
		PolSpanNs: 10e9, PolMinLost: 10, PolRunShare: 0.3, PolCorr: 0.35, PolMinWins: 50,
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
	Loss     float64 // Verlustanteil der letzten LossPkts gemeldeten Pakete (Stand des letzten gewerteten Fensters)
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
	// LossBefore: Verlustanteil der 10 s vor der Spanne (ein Verlust, der schon vorher da war, sagt nichts ueber die
	// Fuelldaten). Young: Die Basis ist jung oder wurde in bzw. kurz vor der Spanne zurueckgesetzt - "staut nicht" gibt
	// es dann nicht. Hint: ein Anzeichen fuer Stau unterhalb der Schwellen (Schlange ab QueueLoUs, Verlust gestiegen,
	// Warnung) - zusammen mit einem Stausignal von GCC reicht das (sfu, probe.go).
	LossBefore float64
	Young      bool
	Hint       bool
}

// Rates: gesendete und bestaetigte Rate ueber dieselben Pakete (Detector.Rates).
type Rates struct {
	SentBps  int64 // gesendet: Bytes aller Pakete der Fenster je Sendezeit
	AckedBps int64 // vom Empfaenger bestaetigt: angekommene Bytes derselben Fenster je Ankunftszeit
	Ns       int64 // Sendezeit, ueber die gemessen wurde (0 = nichts Messbares)
}

// Tip: Was die zuletzt gemeldeten Pakete zeigen (Detector.Tip).
type Tip struct {
	// Clear: Der Detektor widerlegt einen Stau ausdruecklich - Zustand frei, Basis nicht jung, Rueckmeldung frisch und
	// nah an der Gegenwart, die juengsten Pakete ohne Schlange, Verlust nicht gestiegen. Clear = Calm und nicht LossUp.
	Clear bool
	// Calm: dasselbe ohne den Verlust - Zustand frei, Basis nicht jung, Rueckmeldung frisch, keine Schlange an der Spitze.
	// Fresh: ... und ohne die Schlange - der Detektor sieht die Leitung (Zustand frei, Basis nicht jung, Rueckmeldung
	// frisch); Calm = Fresh und keine Schlange. Damit entschuldigt die Steuerung einen einzelnen Takt, in dem nur das
	// eigene Buendel des Beins (Schluesselbild) eine Schlange zeigt (sfu: estimator.blip).
	// LossUp: Der Verlust an der Spitze ist gegenueber den 10 s davor gestiegen bzw. hoch. Getrennt, weil ein einzelner
	// Verlust-Schub kein Zeichen der Leitung sein muss (das Schluesselbild nach einem Schichtwechsel ueberlaeuft einen
	// kurzen Puffer) - die Steuerung haelt eine eben bestandene Probe deshalb nur gegen Calm (sfu: estimator.held).
	Calm     bool
	Fresh    bool
	LossUp   bool
	QueueUs  int64   // kleinste Laufzeit der juengsten TipPkts Pakete ueber der Basis
	Loss     float64 // Verlustanteil der letzten TipLossNs
	LossLong float64 // ... der 10 s davor
	N        int     // Pakete mit Ankunftszeit an der Spitze
	LagNs    int64   // so lange her wurde das juengste gemeldete Paket gesendet
	State    State
	// Policer (Stufe 3b): Der Verlust der letzten PolSpanNs folgt der Senderate bzw. kommt in Folgen (Pol) - dann ist
	// jeder Verlust an der Spitze ein Zeichen (LossUp), auch wenn er nicht ueber dem der Zeit davor liegt. Gleichmaessig
	// verteilter Verlust (Zufallsverlust) bleibt unauffaellig wie bisher.
	Policer bool
	Pol     PolStat
}

// PolStat: Verteilung des Verlusts ueber die Fenster der letzten PolSpanNs (Detector.polStat).
//
// Am Token-Bucket-Policer verlieren die Fenster, in denen das Bein ueber der Policer-Rate sendet (der Eimer ist leer),
// darunter kaum; verworfen wird alles, was ueber der Rate liegt - Paket fuer Paket hintereinander. Beim Zufallsverlust
// ist der Verlustanteil von der Rate unabhaengig, Folgen sind selten: Bei unabhaengigem Verlust p liegt ein verlorenes
// Paket mit 1 - (1-p)^2 x (1+2p) in einer Folge ab 3 - bei 3 % 0,3 %, bei 10 % 2,8 %, erst bei 30 % 22 %. Labor
// labbwe3 (Mitschnitt beim Empfaenger, je 1 Lauf): Policer 1400 kbit/s, Eimer 200 ms - Korrelation 0,47, 71 % der
// Verluste in Folgen ab 3; Zufallsverlust 3 % - 0,07 und 0 %. Schwellen: Folgen ab 30 % (zehnmal ueber dem
// Zufallsverlust bis 10 %), Korrelation ab 0,35 ueber mindestens 50 Fenster (bei Zufallsverlust streut sie um 0 mit
// rund 1/Wurzel(Fenster), bei 50 Fenstern 0,14 - die Schwelle liegt 2,5 Streuungen darueber, bei 100 Fenstern 3,5).
type PolStat struct {
	Lost    int     // verlorene Pakete
	RunPct  float64 // Anteil davon in Folgen ab 3 Paketen
	Corr    float64 // Korrelation Senderate/Verlustanteil je Fenster
	Windows int     // gewertete Fenster
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
	lostRun        int   // davon in Folgen ab 3 verlorenen Paketen (eine Folge zaehlt im Fenster, in dem sie 3 erreicht)
	bytes          int64 // empfangene Bytes
	sbytes         int64 // gesendete Bytes (auch der verlorenen Pakete)
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
	tipN    = 128 // zuletzt gemeldete Pakete
)

type tipAck struct {
	sentNs int64
	dl     int64 // Laufzeit (us), nur bei timed
	lost   bool
	timed  bool
}

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

	firstNs  int64 // Sendezeit des ersten gemeldeten Pakets (Beginn der Schonzeit; 0 = noch keins)
	baseFrom int64 // Sendezeit des ersten Pakets in der Basis seit dem letzten Verwerfen (0 = Basis leer)
	resetNs  int64 // letztes Verwerfen der Basis (Sendezeit-Uhr; 0 = nie)

	lrun int // verlorene Pakete in Folge (Folgenummern), fuer win.lostRun

	// Spitze: die zuletzt gemeldeten Pakete (Tip)
	tip  [tipN]tipAck
	tipI int // naechster Platz
	tipC int // belegte Plaetze

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
	d.baseFrom = 0
	d.tipC = 0
}

// young: Ist die Basis zur Sendezeit atNs noch jung (bzw. leer)? Eine junge Basis kann selbst schon eine Schlange
// enthalten - was darueber liegt, ist dann zu klein gemessen.
func (d *Detector) young(atNs int64) bool {
	return d.baseFrom == 0 || atNs-d.baseFrom < d.cfg.BaseYoungNs
}

// Reset verwirft Basis und laufendes Fenster (Wegwechsel: die Laufzeit des neuen Wegs ist eine andere). Der Zustand
// wird unbekannt, bis das erste Fenster auf dem neuen Weg gewertet ist; Pakete von vor dem Wechsel zaehlen nicht mehr.
func (d *Detector) Reset(nowNs int64) *Change {
	d.clearBase()
	d.curOpen = false
	d.minSentNs = nowNs
	d.resetNs = max(nowNs, 1)
	d.qRun, d.lRun, d.clearRun, d.lrun = 0, 0, 0, 0
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
		w.sbytes += int64(a.Size)
		if d.firstNs == 0 {
			d.firstNs = max(a.SentNs, 1)
		}
		if a.Kind == KindPad {
			w.pad++
		}
		t := &d.tip[d.tipI]
		d.tipI = (d.tipI + 1) % tipN
		d.tipC = min(d.tipC+1, tipN)
		*t = tipAck{sentNs: a.SentNs, lost: a.Lost}
		if a.Lost {
			w.lost++
			switch d.lrun++; {
			case d.lrun == 3:
				w.lostRun += 3
			case d.lrun > 3:
				w.lostRun++
			}
			continue
		}
		d.lrun = 0
		w.bytes += int64(a.Size)
		if a.NoTime {
			continue
		}
		dl := a.ArrivalUs - a.SentNs/1000
		t.dl, t.timed = dl, true
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
	if d.baseFrom == 0 {
		d.baseFrom = max(sentNs, 1)
	}
}

// lossOver: Verlustanteil der gemeldeten Pakete in den geschlossenen Fenstern mit Sendezeit in [fromNs, toNs),
// hoechstens die letzten maxPkts (0 = alle); dazu die Zahl der Pakete. Geht von hinten nach vorn.
func (d *Detector) lossOver(fromNs, toNs int64, maxPkts int) (float64, int) {
	n, lost := 0, 0
	for i := d.histN - 1; i >= d.lo(); i-- {
		w := d.at(i)
		if w.endNs >= toNs {
			continue
		}
		if w.endNs < fromNs || maxPkts > 0 && n >= maxPkts {
			break
		}
		n += w.n
		lost += w.lost
	}
	if n == 0 {
		return 0, 0
	}
	return float64(lost) / float64(n), n
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
	qKnown := false
	if w.timed > 0 {
		if b, ok := d.base(w.endNs); ok {
			w.queueUs, qKnown = w.minD-b, true
		}
	}
	// Verlust: ueber die letzten LossPkts gemeldeten Pakete einschliesslich dieses Fensters (auch duenne Fenster zaehlen
	// hier mit - Pakete sind Pakete)
	w.state = d.state
	d.hist[d.histN%histN] = w
	d.histN++
	lossN, nN := d.lossOver(w.endNs-c.LossSpanNs, w.endNs+1, c.LossPkts)
	if !w.valid {
		d.Thin++
		return nil
	}
	grace := d.firstNs != 0 && w.startNs-d.firstNs < c.GraceNs // Schonzeit nach Beinbeginn: Verlust zaehlt nicht
	qHi := qKnown && w.queueUs > c.QueueHiUs
	qLo := qKnown && w.queueUs < c.QueueLoUs
	lHi := !grace && nN >= c.LossMinPkts && lossN >= c.LossHi && w.lost > 0
	lLo := grace || nN >= c.LossMinPkts && lossN < c.LossLo || nN < c.LossMinPkts && w.lost == 0
	if d.qRun == 0 && d.lRun == 0 && (qHi || lHi) {
		d.runFrom = d.histN - 1
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
	d.lastQ, d.lastLoss = w.queueUs, lossN
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
	d.at(d.histN - 1).state = to
	if to != d.state {
		if to == Congested {
			d.capBps = d.rateOver(d.runFrom, d.histN)
		}
		return d.set(nowNs, to, why)
	}
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

// Span urteilt ueber die Fenster, deren Pakete zwischen fromNs und toNs gesendet wurden (eine Probe). Das noch
// laufende Fenster zaehlt mit, soweit es schon Rueckmeldungen hat.
//
//   - staut: Schlange ueber der Schwelle (in mindestens zwei Fenstern), der Detektor hat in der Spanne Stau gemeldet,
//     oder der Verlust liegt deutlich ueber dem der Zeit davor (SpanLossUp, mindestens SpanLossMin und drei Pakete) -
//     Verlust, der mit der Last kommt, ist das Zeichen einer Ratengrenze (Policer); Verlust, der schon vorher da war
//     (Zufallsverlust), sagt nichts.
//   - staut nicht: Schlange leer, durchgehend "frei", Verlust nicht gestiegen - und die Basis ist nicht jung (kein
//     Zuruecksetzen in oder kurz vor der Spanne).
//   - sonst unklar.
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
	v.LossBefore, _ = d.lossOver(fromNs-10e9, fromNs, 0)
	v.Young = d.young(fromNs) || d.resetNs != 0 && d.resetNs >= fromNs-c.BaseYoungNs
	lossUp := v.Loss >= v.LossBefore+c.SpanLossUp && v.Loss >= c.SpanLossMin && lost >= 3
	lossSame := v.Loss <= max(c.SpanLossFree, v.LossBefore+c.SpanLossSame)
	v.Hint = qn > 0 && v.QueueUs >= c.QueueLoUs || !lossSame && lost >= 2 || v.WorstSt >= Warn
	switch {
	case qn == 0 || n < c.WinMinPkts:
		// nichts Wertbares
	case v.QueueUs > c.QueueHiUs || lossUp || v.WorstSt == Congested:
		v.Result = VerdictCong
	case v.QueueUs < c.QueueLoUs && lossSame && v.WorstSt == Free && !v.Young:
		v.Result = VerdictFree
	}
	return v
}

// Rates: gesendete und bestaetigte Rate ueber die geschlossenen Fenster, die ganz zwischen fromNs und toNs gesendet
// wurden - "vom Empfaenger bestaetigt" gegen "im selben Zeitraum gesendet". Gesendet zaehlt je Sendezeit (bis zum
// Beginn des naechsten Fensters), bestaetigt je Ankunftszeit derselben Pakete (rateOver): Waechst unterwegs eine
// Schlange, dehnt sich die Ankunft, und die bestaetigte Rate bleibt hinter der gesendeten zurueck; verlorene Pakete
// fehlen ihr ganz.
func (d *Detector) Rates(fromNs, toNs int64) Rates {
	first, last := -1, -1
	var sb int64
	for i := d.lo(); i < d.histN; i++ {
		w := d.at(i)
		if w.startNs < fromNs || w.endNs > toNs {
			continue
		}
		if first < 0 {
			first = i
		}
		last = i
		sb += w.sbytes
	}
	if first < 0 {
		return Rates{}
	}
	end := d.at(last).endNs
	switch {
	case last+1 < d.histN:
		end = d.at(last + 1).startNs
	case d.curOpen:
		end = d.cur.startNs
	}
	ns := end - d.at(first).startNs
	if ns <= 0 {
		return Rates{}
	}
	return Rates{SentBps: sb * 8 * 1e9 / ns, AckedBps: d.rateOver(first, last+1), Ns: ns}
}

// Tip: Was zeigen die zuletzt gemeldeten Pakete - widerlegt der Detektor einen Stau, den GCC gerade meldet (Clear)?
// Die Schwellen des Zustands (Schlange ueber 50 ms in zwei Fenstern) sind fuer diese Frage zu traege: Bis sie
// anschlagen, ist der Knoten laengst unten. Umgekehrt genuegt hier viel weniger: Staut die Leitung wirklich, haben
// schon die juengsten bestaetigten Pakete eine Schlange (GCC schliesst aus denselben Rueckmeldungen auf Ueberlast).
// Gemessen wird die KLEINSTE Laufzeit der letzten TipPkts Pakete ueber der Basis: Nach einer Pause der Leitung kommen
// die aufgehaltenen Pakete zusammen an - das zuletzt gesendete hat kaum gewartet -, in einer wachsenden Schlange hat
// jedes gewartet.
func (d *Detector) Tip(nowNs int64) Tip {
	c := d.cfg
	t := Tip{State: d.state, QueueUs: -1, LagNs: -1}
	var newest int64 = -1
	var minDl int64
	lost, n := 0, 0
	for k := 1; k <= d.tipC; k++ {
		a := &d.tip[(d.tipI-k+tipN)%tipN]
		if newest < 0 {
			if !a.timed {
				if a.lost {
					lost++
					n++
				}
				continue
			}
			newest = a.sentNs
		}
		if a.sentNs < newest-c.TipLossNs {
			break
		}
		n++
		if a.lost {
			lost++
		}
		if a.timed && t.N < c.TipPkts && a.sentNs >= newest-c.TipSpanNs {
			if t.N == 0 || a.dl < minDl {
				minDl = a.dl
			}
			t.N++
		}
	}
	if newest < 0 {
		return t
	}
	t.LagNs = nowNs - newest
	if n > 0 {
		t.Loss = float64(lost) / float64(n)
	}
	t.LossLong, _ = d.lossOver(newest-c.TipLossNs-10e9, newest-c.TipLossNs, 0)
	b, ok := d.base(newest)
	if !ok {
		return t
	}
	t.QueueUs = minDl - b
	t.Fresh = d.state == Free && !d.young(newest) && t.N >= min(3, c.TipPkts) &&
		d.lastFbNs > 0 && nowNs-d.lastFbNs <= c.TipAgeNs && t.LagNs <= c.TipLagNs
	t.Calm = t.Fresh && t.QueueUs < c.TipQueueUs
	t.Pol = d.polStat(newest)
	t.Policer = d.policer(t.Pol)
	t.LossUp = !(lost < 2 || t.Loss <= t.LossLong+c.TipLossUp) || t.Loss >= c.LossHi || t.Policer && lost > 0
	t.Clear = t.Calm && !t.LossUp
	return t
}

// WinStat: ein Messfenster fuer die Zaehler des Beins (Detector.Wins).
type WinStat struct {
	Ns       int64 // Sendezeit vom ersten bis zum letzten Paket
	SentBps  int64 // gesendete Rate (alle gemeldeten Pakete, auch die verlorenen)
	Pkts     int   // gemeldete Pakete
	Lost     int   // davon verloren
	LostRuns int   // ... in Folgen ab 3 verlorenen Paketen
}

// Wins liefert die geschlossenen Fenster der letzten spanNs Sendezeit (aeltestes zuerst) - die Zaehler je
// Messfenster (rund 100 ms), aus denen das Policer-Merkmal entsteht.
func (d *Detector) Wins(spanNs int64) []WinStat {
	if d.histN == 0 {
		return nil
	}
	newest := d.at(d.histN - 1).endNs
	var out []WinStat
	for i := d.lo(); i < d.histN; i++ {
		w := d.at(i)
		if w.endNs < newest-spanNs {
			continue
		}
		ws := WinStat{Ns: w.endNs - w.startNs, Pkts: w.n, Lost: w.lost, LostRuns: min(w.lostRun, w.lost)}
		if ws.Ns > 0 {
			ws.SentBps = w.sbytes * 8 * 1e9 / ws.Ns
		}
		out = append(out, ws)
	}
	return out
}

// policer: Zeigt die Verteilung des Verlusts einen Policer (PolStat)?
func (d *Detector) policer(p PolStat) bool {
	c := d.cfg
	return c.PolSpanNs > 0 && p.Lost >= c.PolMinLost && (p.RunPct >= c.PolRunShare || p.Windows >= c.PolMinWins && p.Corr >= c.PolCorr)
}

// polStat: Verteilung des Verlusts ueber die geschlossenen Fenster der letzten PolSpanNs vor newestNs (Sendezeit),
// ohne duenne Fenster und ohne die Schonzeit nach Beinbeginn.
func (d *Detector) polStat(newestNs int64) PolStat {
	c := d.cfg
	var p PolStat
	run := 0
	var sx, sy, sxx, syy, sxy float64
	for i := d.histN - 1; i >= d.lo(); i-- {
		w := d.at(i)
		if w.endNs < newestNs-c.PolSpanNs {
			break
		}
		ns := w.endNs - w.startNs
		if w.n < c.WinMinPkts || ns <= 0 || d.firstNs != 0 && w.startNs-d.firstNs < c.GraceNs {
			continue
		}
		x, y := float64(w.sbytes)*8e9/float64(ns), float64(w.lost)/float64(w.n)
		sx, sy, sxx, syy, sxy = sx+x, sy+y, sxx+x*x, syy+y*y, sxy+x*y
		p.Windows++
		p.Lost += w.lost
		run += w.lostRun
	}
	if p.Lost > 0 {
		p.RunPct = min(float64(run)/float64(p.Lost), 1)
	}
	if n := float64(p.Windows); n >= 3 {
		if vx, vy := n*sxx-sx*sx, n*syy-sy*sy; vx > 0 && vy > 0 {
			p.Corr = (n*sxy - sx*sy) / math.Sqrt(vx*vy)
		}
	}
	return p
}
