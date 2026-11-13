package sfu

import (
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/bwe"
	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Probing (experimentell, Config.Probing): Vor einem Schritt nach oben, den nur die Schaetzung verhindert, fuellt der
// Knoten die Leitung kurz mit wertlosen RTP-Padding-Paketen (fwd.Leg.SetPadding) auf die Rate, die der Schritt
// braeuchte. Traegt die Leitung das ohne Stausignal, gibt der Schaetzer den Schritt frei; sonst bleibt alles, wie es
// ist - kein Schluesselbild, kein Wechsel, kein verdeckter Ton. Nur UDP-Beine mit GCC; TCP/TLS und Beine ohne GCC
// bleiben beim bisherigen Verfahren (der Wechsel selbst ist die Probe).
//
// Ablauf einer Probe (alles im Takt, die Pakete schreibt der Schreiber des Beins):
//  1. halbe Fuellrate fuer probeRamp Takte
//  2. volle Fuellrate fuer das Fenster (2,5 Umlaufzeiten, mindestens probeMinTicks Takte, hoechstens probeWinMax;
//     ohne Umlaufzeit probeWin - seit Stufe 3 also 0,4 bis 0,6 s)
//  3. Nachlauf ohne Fuelldaten fuer probeSettle Takte (die Rueckmeldung zum Ende des Fensters steht noch aus)
//
// Ob die Leitung dabei staut, wertet die Probe selbst aus (probeJudge, fallClass): eigene Warteschlange, Ueberlast laut
// Laufzeit-Messung (Leg.ExtStat bzw. ExtOver), Verlust gegenueber dem Stand vor der Probe - und ein gefallenes
// GCC-Ziel nur, wenn es ein Ruecksprung sein kann (pion klemmt sein Ziel auch ohne Stau an die Empfangsrate, und
// gerade die Fuelldaten loesen das aus). Stau: sofort gescheitert. Ein einzelner Hinweis wird ueber die naechsten
// Takte bestaetigt oder ausgeraeumt; bleibt das Ergebnis unklar, endet die Probe ohne Wertung und wird einmal nach
// probeRetry wiederholt - erst dann zaehlt sie als gescheitert. Die Schaetzung bleibt waehrend der Probe und bis
// probeFloor nach einem Abbruch unveraendert (estimator.update) - ein Stau, den die Fuelldaten selbst erzeugt haben,
// sagt nichts ueber die laufende Auswahl. Steht das Stausignal aber auch ohne Fuelldaten weiter an (probeDrain,
// probeBad), ist die Leitung wirklich eingebrochen: Der Schutz endet sofort.
//
// Bestanden ist die Probe ohne Stau und ohne offenen Hinweis bis zum Ende des Nachlaufs. Dass das GCC-Ziel die Zielrate erreicht, wird
// nicht verlangt: pion-GCC hebt sein Ziel auch bei freier Leitung nur um rund 8 %/s (gemessen), in einem Fenster von
// gut einer Sekunde also um hoechstens ein Zehntel. Den Beleg liefert die Verzoegerungsmessung selbst - eine Leitung,
// die die Zielrate nicht traegt, staut binnen weniger Rueckmeldungen und GCC meldet Ueberlast.
//
// Ohne Probe (freier Versuch, siehe freeStep): der erste Schritt nach oben eines Beins und die Rueckkehr auf eine
// Schicht, die mindestens upHold gehalten hat - jeweils einmal, zeitlich wie ohne Probing.
//
// Abstand: nach einer gescheiterten Probe probeGap, je weitere gescheiterte mal probeGapUp bis probeGapMax (3 s, 4,5 s,
// 6,75 s ... 20 s) - je Bein und Zielrate (legFail), nicht je Platz; eine bestandene Probe ab dieser Zielrate oder
// laengere Ruhe setzt zurueck (die naechste gescheiterte beginnt wieder bei probeGap). Nach einer bestandenen Probe
// fruehestens probeGap spaeter die naechste fuer denselben Schritt (falls er aus anderem Grund ausbleibt). Proben ohne
// Wertung (unklar, abgebrochen) aendern den Abstand nicht - eine Folge unklarer Proben endet selbst mit einer
// gescheiterten (probeUnclear, probeVerdict).
//
// Nicht geprobt wird ein Schritt, dessen Fuellrate ueber probeFillMax laege: Er laeuft wie ohne Probing (planLeg).
//
// Verhaeltnis zum Aufschub (dwell): Der Aufschub entsteht unveraendert, wenn ein echter Wechsel scheitert. Mit Probing
// ist danach fuer einen neuen Versuch auf diese Schicht eine bestandene Probe noetig - der blosse Ablauf des Aufschubs
// genuegt nicht mehr (genau diese blinden Versuche kosten Schluesselbilder). Dafuer darf schon waehrend des Aufschubs
// geprobt werden, sobald der Proben-Abstand um ist, und eine bestandene Probe erlaubt den Versuch vorzeitig. Die Probe
// verlaengert den Aufschub nie. Nach einem echten Fehlversuch (bzw. einer Budget-Ruecknahme mit Vorgeschichte) wird
// fruehestens nach dem Aufschub geprobt, hoechstens aber nach probeDwellMax (dwell.afterFail): Besteht die Probe
// wiederholt, waehrend der Wechsel scheitert, kommen die Versuche so nie haeufiger als ohne Probing (8, 16, 32 ... s) -
// eine bestandene Probe setzt nur den Abstand nach gescheiterten Proben zurueck, nicht diesen. "Vorzeitig" wirkt also
// bei langen Aufschueben (ueber 2 min bis 5 min): Eine frei gewordene Leitung ist nach gut 2 min wieder oben (Probe
// spaetestens 2 min nach dem letzten Fehlversuch, dazu Ruhezeit, Probe und Wechsel - gemessen bis 2 min 43 s).
// Zusammenspiel mit dem kurzen Proben-Abstand (Stufe 3): Der Aufschub entsteht nur aus echten Wechseln, die scheitern;
// gescheiterte Proben setzen ihn nicht. In anhaltender Enge ohne Fehlversuch gilt also allein der Proben-Abstand
// (hoechstens probeGapMax, 20 s) - der Aufschub zieht ihn nicht auf Minuten. Erst wenn Wechsel nach bestandener Probe
// wiederholt scheitern (Leitung traegt die Probe, nicht die Schicht), bremst er wie ohne Probing.
const (
	probeRamp     = 2                      // Takte mit halber Fuellrate
	probeMinTicks = 2                      // Mindestlaenge des Fensters in Takten (Stufe 3: 0,4 s, vorher 3)
	probeWin      = 600 * time.Millisecond // Fenster ohne bekannte Umlaufzeit (vorher 1 s)
	probeWinMax   = 600 * time.Millisecond // laengstes Fenster (vorher 1,5 s)
	probeRTTs     = 2.5                    // Fenster in Umlaufzeiten
	probeSettle   = 2                      // Takte Nachlauf
	probeFillMax  = 1500e3                 // hoechste Fuellrate (bit/s)
	probeMinFill  = 0.6                    // so viel der Fuellrate muss im Fenster wirklich gesendet worden sein
	probeGap      = 3 * time.Second        // Abstand nach einer gescheiterten Probe (Stufe 3; vorher 10 s verdoppelnd bis 2 min)
	probeGapUp    = 1.5                    // ... je weitere gescheiterte Probe mal so viel
	probeGapMax   = 20 * time.Second       // ... hoechstens
	probeDwellMax = 2 * time.Minute        // nach einem echten Fehlversuch die naechste Probe hoechstens so spaet (dwell.afterFail)
	// Fuellziel: Die Probe fuellt auf den Bedarf des Schritts mal probeHead (Stufe 3; vorher upHeadroom, 1,15) - mindestens
	// probeFillMin ueber der laufenden Rate, damit sich die Probe im Nachweis von Schwankungen der Bildrate abhebt
	// (Schluesselbilder, 3 bis 4 % in der Rate des 100-ms-Rasters; bei kleinen Schritten waeren 5 % weniger als das).
	probeHead    = 1.05
	probeFillMin = 200e3
	// Lange Probe (Stufe 3b, freshBrake): Nach einem frischen Rueckfall faehrt die naechste Probe auf dieses Ziel mit
	// dem Fenster und Fuellziel von vor Stufe 3 - fest probeWinLong (unabhaengig von der Umlaufzeit) und Bedarf x
	// probeHeadLong. Ein Token-Bucket-Policer knapp unter der Zielrate traegt die kurze Probe aus seinem Eimer: Sie schiebt
	// nur (Bedarf x 1,05 - Policer-Rate) x 0,4 bis 0,6 s ueber die Grenze, rund 80 bis 120 kbit - ein Eimer von 1 bis
	// 2 x Rate x 100 ms (145 bis 290 kbit) leert sich nie. Die lange schiebt (Bedarf x 1,15 - Policer-Rate) x 1,5 s,
	// rund 400 bis 600 kbit (Pruefer bwe3: mit altem Fenster, aber Fuellziel 1,05 blieb der Zyklus - beides ist noetig).
	// (Variablen fuer Tests, siehe unten.)
	probeRetry = 2 * time.Second // Abstand nach einer abgebrochenen Probe (Belegung geaendert)
	probeQuiet = layerDwell      // so lange muss die Bild-Auswahl des Beins unveraendert laufen, bevor geprobt wird
	// ... vor der ersten Probe eines Beins kuerzer: Es gibt noch keinen Wechsel, dessen Folgen (Schluesselbild,
	// nachlaufendes Ziel) abzuwarten waeren - nur der Anlauf (erstes Schluesselbild, erste Rueckmeldungen an GCC)
	probeQuietFirst = 2 * time.Second
	probeGrant      = time.Second // so lange gilt eine bestandene Probe fuer den Schritt
	probeSlack      = 1.1         // ... auch wenn sein Bedarf seit Beginn der Probe um so viel gewachsen ist
	probeFloor      = lagFloor    // so lange nach einem Abbruch faellt die Schaetzung nicht unter den Stand davor
	probeDrain      = 2           // ... es sei denn, nach so vielen Takten ohne Fuelldaten steht weiter ein Stausignal an,
	probeBad        = 2           // und zwar so viele Takte in Folge (echter Einbruch, estimator.update)
	// Eigene Auswertung (probeJudge, fallClass)
	// Ziel ueber diesem Anteil der eigenen Senderate (estimator.sendRate): kein Ruecksprung. Der laege bei 85 % der
	// Rate, die pion empfangen hat - und die liegt im selben Augenblick oft ueber der Senderate der letzten 500 ms,
	// auch wenn beide dieselben Bytes zaehlen (siehe fallClass). Gemessen bei 50 Rueckspruengen: Ziel hoechstens 99 %
	// der Senderate (in einer Probe 86 %; einmal darueber, gleich nach einem Schritt nach unten - dort wird weder
	// geprobt noch entschuldigt); Klemmungen (62) mindestens 105 %.
	probeDecSent = 1.0
	probeDecBase = 1.2              // Ziel unter diesem Vielfachen der Rate vor der Probe: Ruecksprung (Klemmung laege bei 1,5)
	probeLossUp  = 0.05             // Verlustanteil um so viel ueber dem Stand vor der Probe, zwei Takte in Folge: Hinweis auf Stau
	probeConfirm = 2                // so viele Takte mit steigendem Ziel raeumen einen einzelnen Hinweis aus
	probeStall   = 2                // ... so viele ohne Anstieg lassen ihn stehen (Ergebnis unklar)
	probeSusMax  = 3                // hoechstens so viele ausgeraeumte Hinweise je Probe, sonst unklar
	probeSusOver = time.Second      // so lange darf das Fenster wegen eines offenen Hinweises ueberzogen werden
	probeUnkWin  = 10 * time.Second // zweites unklares Ergebnis binnen dieser Zeit: gescheitert
	// Wertung durch den eigenen Stau-Detektor (HORN_BWE=eigen, probeVerdict)
	probeUnkMax = 3           // so viele Proben ohne klares Ergebnis in Folge (je binnen probeUnkGap): die letzte zaehlt als gescheitert
	probeUnkGap = time.Minute // ... laenger auseinander zaehlen sie nicht als Folge (die Wiederholung kann auf ein erholtes GCC-Ziel warten muessen)
	// Durchsatz-Nachweis: bei voller Fuellung gesendet mindestens so viel der Zielrate - der Bedarf des Schritts ohne die
	// Reserve (1/upHeadroom). Die Fuellpakete folgen nur auf Medienpakete und erreichen die Fuellrate nicht immer ganz
	// (UDP-Laeufe: Schritt l -> m 87 bis 102 % der Zielrate, m -> h 98 bis 103 %); mit 0,9 blieb jede vierte Probe
	// l -> m auf freier Leitung ohne Nachweis.
	probeSentMin  = 1 / probeHead
	probeAckMin   = 0.95                   // ... und davon vom Empfaenger im selben Zeitraum bestaetigt mindestens so viel
	probeProofMin = 200 * time.Millisecond // ... gemessen ueber mindestens so viel Sendezeit (vorher 400 ms); bwe.Rates zaehlt nur ganze 100-ms-Fenster der Fuellung - bei 0,4 s Fenster 2 oder 3
	probeTail     = 500 * time.Millisecond // Nachlauf bis zum Urteil ohne bekannte Umlaufzeit (sonst 2 Umlaufzeiten + 200 ms, hoechstens 1 s)
)

// Lange Probe nach frischem Rueckfall (siehe oben bei probeHead). Variablen fuer Tests.
//
// probeGapMaxLong: Abstand nach gescheiterten langen Proben hoechstens so viel (statt probeGapMax). In anhaltender Enge
// hinter einem Policer scheitert jede lange Probe; mit probeGapMax kam alle 20 s eine, und jede schob 1 bis 1,5 s lang
// Fuelldaten ueber die Policer-Rate (UDP-Aufbau, Policer 1450/150 und 1500/100 ms, 600 s: 25 bis 31 MB/h Fuelldaten,
// 5 bis 7 Tausend verworfene Pakete - auch Bildpakete, denn der Policer verwirft, was den Eimer ueberzieht). Die
// Bremse (freshBrake.decay) wartet den Abstand ab, sonst kaeme danach wieder eine kurze Probe.
var (
	probeWinLong    = 1500 * time.Millisecond
	probeHeadLong   = upHeadroom
	probeGapMaxLong = 40 * time.Second
)

// Ergebnis einer Probe in der Layout-Meldung (Slot.Probe).
const (
	probeStart = "start"
	probeOK    = "ok"
	probeFail  = "fail"
	probeAbort = "abort"
	// Grund eines Abbruchs ohne Wertung, wenn die eigene Auswertung zu keinem Ergebnis kam: Die Probe wird einmal
	// nach probeRetry wiederholt; ist auch die Wiederholung unklar, zaehlt sie als gescheitert.
	whyUnclear = "unklar"
)

// probe ist die laufende Probe eines Beins (hoechstens eine).
type probe struct {
	stage  int      // 0 keine, 1 halbe Fuellrate, 2 volle, 3 Nachlauf
	n      int      // Takte in dieser Stufe
	at     int64    // Beginn der Stufe
	s      *slot    // Platz, der hoeher gehen soll
	owner  *Peer    // ... fuer wen
	layer  string   // ... von welcher Schicht ("" = aus der Pause)
	rank   int      // Zielschicht (layerRank)
	fs     *slot    // Platz, auf dessen Ausgang gefuellt wird
	fown   *Peer    // ... und sein Besitzer
	out    *fwd.Out // ... sein Ausgang
	target float64  // Zielrate des Beins (bit/s, mit Ton): Bedarf x probeHead (bzw. probeFillMin ueber der laufenden Rate)
	stepT  float64  // ... dieselbe mit der Reserve des Schritts (upHeadroom) - in diesem Mass fuehrt legFail Buch
	fill   float64  // volle Fuellrate (bit/s)
	win    int64    // Dauer des Fensters
	wmax   int64    // ... hoechstens (probeWinMax, bei der langen Probe probeWinLong)
	head   float64  // Fuellziel: Bedarf x head (probeHead, lange Probe probeHeadLong)
	long   bool     // lange Probe nach frischem Rueckfall (freshBrake)
	pad0   uint64   // Fuell-Bytes des Beins zu Beginn des Fensters
	t0     int64    // Beginn der Probe
	// Eigene Auswertung (probeJudge): offener Hinweis auf Stau
	sus      int     // noch so viele Takte mit steigendem Ziel, dann ist er ausgeraeumt (0 = keiner offen)
	susWhy   string  // gcc | over | loss
	susV     float64 // Ziel im letzten Takt
	susStall int     // Takte in Folge ohne Anstieg
	susN     int     // ausgeraeumte Hinweise
	clamps   int     // Faelle des Ziels, die kein Ruecksprung waren (Klemmung an die Empfangsrate)
	// Wertung durch den eigenen Stau-Detektor (HORN_BWE=eigen): GCC haelt nur die Fuellung an
	gsig     string // Stausignal von GCC, das die Fuellung angehalten hat bzw. im Nachlauf kam (gcc | over | loss; "" = keins)
	fillFrom int64  // Beginn der vollen Fuellung (0 = nie erreicht)
	fillEnd  int64  // Ende der Fuellung
	wait     int    // Takte Nachlauf bis zum Urteil
	det      string // Urteil des Detektors (bwe.Verdict.Result) und die Raten des Nachweises - fuer das Ereignis
	detSent  int64
	detAcked int64
}

// ProbeEvent: Ende einer Probe (Config.OnProbe).
type ProbeEvent struct {
	Leg        *Leg
	Class      string        // Platzart des Schritts (speaker, tile, screen)
	From, To   string        // Schicht vor dem Schritt ("" = Pause) und Zielschicht
	TargetKbit int           // Zielrate des Beins (mit Reserve und Ton)
	FillKbit   int           // volle Fuellrate
	Result     string        // ok | fail | abort
	Why        string        // Grund (fail: gcc | over | loss | queue; abort: platz | ausgang | fuell | unklar)
	Dur        time.Duration // Dauer der Probe
	Stage      int           // Stufe beim Ende: 1 halbe Fuellrate, 2 volle, 3 Nachlauf
	Clamped    int           // Faelle des GCC-Ziels in dieser Probe, die kein Stau waren
	Hint       string        // letzter einzelner Hinweis auf Stau (gcc | over | loss), auch wenn er ausgeraeumt wurde
	EstKbit    int           // Schaetzung des Beins nach der Probe
	GccKbit    int           // GCC-Ziel beim Ende
	// Nur HORN_BWE=eigen (sonst leer): Urteil des eigenen Detektors (staut | staut-nicht | unklar), das Stausignal von
	// GCC, das die Fuellung angehalten hat ("" = keins), und der Durchsatz-Nachweis - bei voller Fuellung gesendet und
	// im selben Zeitraum bestaetigt (kbit/s, 0 = volle Fuellung nicht erreicht).
	Det       string
	GccSig    string
	SentKbit  int
	AckedKbit int
}

// LegEvent: Ereignis aus der Steuerung eines Beins (Config.OnLegEvent).
//
//	LegRefute     Beginn einer Widerlegung (HORN_BWE=eigen): Das GCC-Ziel liegt unter der Rate und ist gefallen bzw. steht,
//	              aber der Detektor widerlegt den Stau - Art spitze (bwe.Tip.Clear) oder halten (eben bestandene Probe,
//	              kein Zeichen von Stau; estimator.held). Die Schicht bleibt.
//	LegRefuteEnd  ihr Ende mit Grund und Dauer: erholt (das Ziel liegt wieder ueber der Rate), detektor (er widerlegt
//	              nicht mehr - das Signal von GCC gilt), faellt, steht, frist (Schonfrist gescheitert), probe
//	LegDown       Ruecknahme einer Schicht desselben Besitzers (To "" = Pause) mit Grund, siehe downEvent
type LegEvent struct {
	Leg                        *Leg
	Kind                       string
	Art                        string        // Widerlegung: spitze | halten
	Why                        string        // Grund (Ende einer Widerlegung, Ruecknahme)
	Dur                        time.Duration // Dauer der Widerlegung (LegRefuteEnd)
	Class, From, To            string        // Ruecknahme: Platzart, Schicht davor und danach
	EstKbit, GccKbit, RateKbit int           // Schaetzung des Beins, GCC-Ziel, Medienrate dieses Takts (an ihr wird das Ziel gemessen)
	// LegFresh (Art in Why): Zielrate des geprobten Schritts, Rate des Beins davor, kleinstes Budget bzw. GCC-Ziel
	// seither, Grenze des Einbruchs (probeSlump x davor), Zielrate der Bremse danach (0 = keine) und Folge n
	TgtKbit, PreKbit, LowKbit, SlumpKbit, BrakeKbit, N int
}

const (
	LegRefute    = "refute"
	LegRefuteEnd = "refute-end"
	LegDown      = "down"
	LegFresh     = "fresh" // Einordnung eines Rueckfalls nach geprobtem Schritt (freshBrake; nur mit Probing)
)

// ProbeStats zaehlt die Proben eines Beins.
type ProbeStats struct {
	Started, Passed, Failed, Aborted uint64
	Unclear                          uint64 // davon (Aborted) ohne klares Ergebnis beendet und wiederholt
	Refuted                          uint64 // eigen: Faelle des GCC-Ziels unter die Rate, die der Detektor widerlegt hat (estimator.refute)
	Held                             uint64 // eigen: Folgen, deren Widerlegung mit dem Halten nach einer bestandenen Probe begann (estimator.held)
	Bursts                           uint64 // eigen: Takte einer laufenden Widerlegung, die ein eigenes Buendel entschuldigt hat (estimator.blip)
	Clamped                          uint64 // Faelle des GCC-Ziels in Proben, die kein Stau waren (probeJudge)
	PadBytes                         uint64 // gesendete Fuell-Bytes
	LateDropped                      uint64 // verworfene Nachzuegler, deren Stelle der Ausgang nicht mehr kennt (fwd.LegStats.LateDropped)
	Last                             string // Ausgang der letzten Probe: ok, fail:<grund> bzw. abort:<grund>
}

// ProbeStats liefert die Zaehler der Proben dieses Beins.
func (l *Leg) ProbeStats() ProbeStats {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	ps := l.pstat
	ps.PadBytes = l.fl.Stats.PadBytes.Load()
	ps.LateDropped = l.fl.Stats.LateDropped.Load()
	ps.Refuted, ps.Held, ps.Bursts = l.est.refuted, l.est.heldN, l.est.blips
	return ps
}

// probing: Laeuft dieses Bein mit Probing (Schalter an, UDP, GCC liefert ein Ziel)?
func (l *Leg) probing(cfg Config) bool {
	return cfg.Probing && l.Ext != nil && !l.Path.Stream()
}

// dwFor liefert den Aufschub-Eintrag des Platzes fuer die Schicht rank und den aktuellen Besitzer (zum Schreiben).
func (s *slot) dwFor(rank int) *dwell {
	if s.dwellOwner != s.owner {
		s.dw, s.dwellOwner = [4]dwell{}, s.owner
	}
	return &s.dw[rank]
}

// probeBegin startet die Probe fuer den Schritt von Platz s auf die Schicht rank: need ist die Bildrate der Auswahl
// nach dem Schritt, sum die laufende, audio der Tonanteil (alles bit/s).
func (l *Leg) probeBegin(s, fs *slot, rank int, need, sum, audio float64, now int64) {
	stepT := need*upHeadroom + audio
	// Lange Probe nach frischem Rueckfall auf dieses Ziel (freshBrake): langes Fenster, Fuellziel x probeHeadLong
	head, long := probeHead, l.fb.longFor(stepT)
	if long {
		head = probeHeadLong
	}
	target := max(need*head, sum+probeFillMin) + audio
	// Gefuellt wird bis zur Zielrate auf der Leitung: Sendet das Bein gerade weniger, als die Planung mit den geglaetteten
	// Raten seiner Schichten annimmt (sie enthalten die Schluesselbilder), fuellt die Probe die Luecke mit. Mit der
	// Reserve 1,15 ging diese Luecke (UDP-Laeufe l -> m: gesendet 85 bis 92 % der Zielrate) im Nachweis unter; mit 1,05
	// fehlte dem Nachweis sonst der Bedarf.
	base := sum + audio
	if sr := l.est.sendRate(); sr > 0 && sr < base {
		base = sr
	}
	fill := target - base
	if fill > probeFillMax {
		// Freigegeben wird nur, was wirklich geprobt wurde. (planLeg probt Schritte mit groesserer Fuellrate gar nicht
		// erst; das hier greift nur, wenn die Raten seither gewandert sind.)
		fill = probeFillMax
		target = base + fill
		stepT = min(stepT, (target-audio)*upHeadroom/head+audio)
	}
	if fill <= 0 {
		return
	}
	win, wmax := int64(probeWin), int64(probeWinMax)
	if l.ExtRTT != nil {
		if rtt := l.ExtRTT(); rtt > 0 {
			win = min(int64(float64(rtt)*probeRTTs), int64(probeWinMax))
		}
	}
	if long {
		win, wmax = int64(probeWinLong), int64(probeWinLong)
	}
	l.pr = probe{stage: 1, at: now, t0: now, s: s, owner: s.owner, layer: s.layer, rank: rank, fs: fs, fown: fs.owner, out: fs.out,
		target: target, stepT: stepT, fill: fill, win: win, wmax: wmax, head: head, long: long}
	loss0 := 0.0
	if l.ExtStat != nil {
		loss0 = l.ExtStat().Loss
	}
	l.est.shieldOn(now, loss0)
	l.fl.SetPadding(fs.out, int64(fill/2))
	l.pstat.Started++
	s.setProbe(probeStart, "", target)
}

// probeStep fuehrt die laufende Probe einen Takt weiter (nach estimator.update, vor der Planung).
func (l *Leg) probeStep(now int64) {
	p := &l.pr
	if p.stage == 0 {
		return
	}
	e := &l.est
	switch {
	// Belegung geaendert (Sprecherwechsel, Pause): Abbruch ohne Wertung - zuerst pruefen, denn mit dem Wechsel sinkt
	// die Rate und das GCC-Ziel kann ihr folgen, was sonst als Stausignal zaehlte
	case p.s.owner != p.owner || p.s.layer != p.layer:
		l.probeEnd(probeAbort, "platz", now)
		return
	case p.fs.owner != p.fown || p.fs.layer == "" || p.fs.paused:
		l.probeEnd(probeAbort, "ausgang", now)
		return
	case e.prSig != "": // Stau in der eigenen Warteschlange: gescheitert
		l.probeEnd(probeFail, e.prSig, now)
		return
	}
	eig := l.ExtSpan != nil // HORN_BWE=eigen: Der Detektor wertet, GCC haelt nur die Fuellung an
	if !eig || p.gsig == "" {
		switch why, res := l.probeJudge(); {
		case res == "":
		case eig:
			// Stausignal von GCC (gescheitert oder unklar): das schnelle Signal zum Anhalten der Fuellung. Ueber die
			// Wertung entscheidet der Detektor nach dem Nachlauf (probeVerdict).
			p.gsig = why
			if p.stage < 3 {
				l.probeHold(now)
				return
			}
		case res == probeFail:
			l.probeEnd(probeFail, why, now)
			return
		default:
			l.probeUnclear(why, now)
			return
		}
	}
	p.n++
	switch p.stage {
	case 1:
		if p.n >= probeRamp {
			p.stage, p.n, p.at, p.pad0 = 2, 0, now, l.fl.Stats.PadBytes.Load()
			p.fillFrom = now
			l.fl.SetPadding(p.out, int64(p.fill))
		}
	case 2:
		el := now - p.at
		if p.sus > 0 && el < p.wmax+int64(probeSusOver) {
			break // offener Hinweis: Das Fenster laeuft weiter, bis er ausgeraeumt ist oder stehen bleibt (probeJudge)
		}
		if p.n >= probeMinTicks && el >= p.win || el >= p.wmax {
			if p.sus > 0 { // auch nach der Verlaengerung nicht ausgeraeumt
				if eig {
					p.gsig = p.susWhy
					l.probeHold(now)
					return
				}
				l.probeUnclear(p.susWhy, now)
				return
			}
			// Sind die Fuelldaten wirklich geflossen? Sonst (Ausgang wartet auf ein Schluesselbild, Quelle still,
			// Rahmen verworfen) haette die Probe nichts gemessen.
			if sent := float64(l.fl.Stats.PadBytes.Load()-p.pad0) * 8; sent < probeMinFill*p.fill*float64(el)/1e9 {
				l.probeEnd(probeAbort, "fuell", now)
				return
			}
			// Im Nachlauf zaehlt ein fallendes Ziel nicht als Stau: Mit dem Ende der Fuelldaten sinkt die empfangene
			// Rate, und pion-GCC nimmt sein Ziel nahe einer frueheren Staurate auf das 1,5-Fache davon zurueck
			// (rate_controller: min(Ziel + Schritt, 1,5 x empfangen)). Ueberlast und Warteschlange zaehlen weiter.
			p.stage, p.n, p.at = 3, 0, now
			e.prTail = true
			l.fl.SetPadding(nil, 0)
			p.fillEnd, p.wait = now, max(probeSettle, l.tailTicks())
		}
	case 3:
		if eig {
			// Nachlauf, bis die Rueckmeldungen zu den letzten Paketen der Fuellung da sind - dann wertet der Detektor.
			// Meldet er schon vorher Stau (nach einem Stausignal von GCC), wartet die Probe nicht weiter.
			if p.n >= p.wait || p.gsig != "" && l.spanCong(now) {
				l.probeVerdict(now)
			}
			return
		}
		if p.n >= probeSettle {
			if p.sus > 0 { // Hinweis im Nachlauf, nicht mehr auszuraeumen
				l.probeUnclear(p.susWhy, now)
				return
			}
			l.probeEnd(probeOK, "", now)
		}
	}
}

// tailTicks: Nachlauf in Takten, bis die Rueckmeldungen zu den letzten Paketen einer Probe da sind - zwei Umlaufzeiten
// + 200 ms, hoechstens 1 s; ohne bekannte Umlaufzeit probeTail.
func (l *Leg) tailTicks() int {
	d := probeTail
	if l.ExtRTT != nil {
		if rtt := l.ExtRTT(); rtt > 0 {
			d = min(2*rtt+200*time.Millisecond, time.Second)
		}
	}
	return int((d + 199*time.Millisecond) / (200 * time.Millisecond))
}

// probeHold (eigen): GCC hat ein Stausignal gemeldet - die Fuellung haelt sofort an, die Probe wartet den Nachlauf ab.
func (l *Leg) probeHold(now int64) {
	p := &l.pr
	l.fl.SetPadding(nil, 0)
	p.stage, p.n, p.at = 3, 0, now
	p.fillEnd, p.wait = now, l.tailTicks()
	l.est.prTail = true
}

// spanCong (eigen): Meldet der Detektor ueber die Pakete der Probe schon jetzt Stau?
func (l *Leg) spanCong(now int64) bool {
	p := &l.pr
	v, _ := l.ExtSpan(time.Duration(now-p.t0), time.Duration(now-p.fillEnd), time.Duration(now-p.fillEnd))
	return v.Result == bwe.VerdictCong
}

// probeVerdict (HORN_BWE=eigen): Wertung der Probe durch den eigenen Stau-Detektor, nach dem Nachlauf.
//
//   - Der Detektor meldet ueber die Pakete der Probe Stau (Schlange, Verlust mit der Last gestiegen): gescheitert
//     ("stau"), mit Abstand wie bisher.
//   - GCC hat ein Stausignal gemeldet und der Detektor zeigt wenigstens ein Anzeichen (Schlange ab 20 ms, Verlust
//     gestiegen, Warnung; bwe.Verdict.Hint): zwei Hinweise - gescheitert, Grund der von GCC.
//   - Der Detektor meldet "staut nicht" UND der Durchsatz-Nachweis stimmt: bestanden. Nachweis: Bei voller Fuellung
//     wurde mindestens probeSentMin der Zielrate (der Bedarf des Schritts) wirklich gesendet, und der Empfaenger hat davon im selben Zeitraum
//     mindestens probeAckMin bestaetigt (bwe.Rates, ueber mindestens probeProofMin). Beides am selben Zaehler
//     (Pakete des Beins mit Kopf, Ton und Fuellung) - nicht Zielrate gegen Durchsatz. Haelt GCC die Fuellung frueh an,
//     ist die Zielrate nie gesendet worden: "staut nicht" sagt dann nichts ueber sie (Labor: auf enger Leitung 10 von
//     29 Proben "staut nicht", keine mit Nachweis).
//   - sonst unklar: ohne Abstand wiederholen (probeRetry) - hoechstens probeUnkMax Mal in Folge, die letzte zaehlt als
//     gescheitert.
func (l *Leg) probeVerdict(now int64) {
	p := &l.pr
	rf := p.fillFrom
	if rf == 0 || rf > p.fillEnd {
		rf = p.fillEnd
	}
	v, r := l.ExtSpan(time.Duration(now-p.t0), time.Duration(now-p.fillEnd), time.Duration(now-rf))
	p.det, p.detSent, p.detAcked = v.Result, r.SentBps, r.AckedBps
	// Durchsatz-Nachweis: Der Bedarf des Schritts (die Zielrate ohne die Reserve, probeSentMin) wurde gesendet UND
	// bestaetigt, und vom Gesendeten kam mindestens probeAckMin an. Ohne "bestaetigt >= Bedarf" reichten
	// 87 % x 95 % = 83 % der Zielrate - weniger, als die Schicht braucht.
	need := p.target / p.head // probeSentMin x Zielrate (lange Probe: 1/probeHeadLong)
	proof := r.Ns >= int64(probeProofMin) && float64(r.SentBps) >= need && float64(r.AckedBps) >= need &&
		float64(r.AckedBps) >= probeAckMin*float64(r.SentBps)
	switch {
	case v.Result == bwe.VerdictCong:
		l.probeEnd(probeFail, "stau", now)
	case p.gsig != "" && v.Hint:
		l.probeEnd(probeFail, p.gsig, now)
	case v.Result == bwe.VerdictFree && proof:
		l.probeEnd(probeOK, "", now)
	default:
		why := "nachweis" // "staut nicht", aber die Zielrate ist nicht belegt
		switch {
		case p.gsig != "":
			why = p.gsig
		case v.Result != bwe.VerdictFree:
			why = "bwe"
		}
		// ramp: Das Stausignal von GCC kam schon in der Rampe (halbe Fuellung) - die volle Fuellung lief nie, die Probe
		// hat die Zielrate nicht geprueft. Die zweite solche Probe in Folge zaehlt als gescheitert: In der Enge folgte
		// sonst auf zwei Rampen-Abbrueche eine dritte Probe, die mit voller Fuellung eine Schlange von 50 bis 100 ms baut.
		// Wiederholt wird eine Rampen-Probe ausserdem nur, wenn die letzte gewertete Probe auf dieses Ziel (ab probeNear
		// ihrer Zielrate, binnen probeUnkGap) nicht gescheitert war: In anhaltender Enge ging sonst jeder gescheiterten
		// Probe genau eine unklare voraus (Messung bwe13: 21 von 24). Auf freier Leitung bleibt der zweite Versuch.
		ramp := p.gsig != "" && p.fillFrom == 0
		lj := &l.lastJ
		afterFail := lj.failed && p.target >= lj.rate*probeNear && now-lj.at <= int64(probeUnkGap)
		if l.unkAt != 0 && now-l.unkAt <= int64(probeUnkGap) {
			l.unkN++
		} else {
			l.unkN, l.unkRamp = 1, false
		}
		if l.unkN >= probeUnkMax || ramp && (l.unkRamp || afterFail) {
			l.probeEnd(probeFail, why, now)
			return
		}
		l.unkRamp = ramp
		p.susWhy = why
		l.probeEnd(probeAbort, whyUnclear, now)
	}
}

// probeUnclear: Die eigene Auswertung kam zu keinem Ergebnis (Hinweis why steht, ist aber nicht bestaetigt). Beim
// ersten Mal endet die Probe ohne Wertung und wird nach probeRetry wiederholt; ist auch die Wiederholung (binnen
// probeUnkWin) unklar, zaehlt sie als gescheitert - mit Abstand wie jede gescheiterte Probe.
func (l *Leg) probeUnclear(why string, now int64) {
	if l.unkAt != 0 && now-l.unkAt <= int64(probeUnkWin) {
		l.probeEnd(probeFail, why, now)
		return
	}
	l.probeEnd(probeAbort, whyUnclear, now)
}

// probeJudge wertet die Hinweise dieses Takts aus (estimator.prEv, prFall) - die eigene Auswertung der Probe. Ein
// gefallenes GCC-Ziel allein laesst sie nicht scheitern (fallClass: pion klemmt sein Ziel auch ohne Stau an die
// Empfangsrate, und gerade die Fuelldaten loesen das aus):
//
//   - Ruecksprung (Ziel unter dem, was eine Klemmung ergaebe) bzw. gefallenes Ziel und im selben Takt Ueberlast laut
//     Laufzeit-Messung oder gestiegener Verlust: Stau, gescheitert.
//   - Klemmung (Ziel ueber dem, was ein Ruecksprung ergaebe): kein Stau, die Probe laeuft weiter.
//   - sonst ein einzelner Hinweis (Fall nicht einzuordnen; Ueberlast oder Verlust ohne Fall): Die Probe laeuft weiter
//     und wartet auf Bestaetigung. Ein zweiter Hinweis, solange der erste offen ist: Stau, gescheitert. Steigt das
//     Ziel dagegen probeConfirm Takte in Folge wieder (GCC ist im Zustand "Anstieg" - bei anhaltendem Stau bliebe es
//     im Ruecksprung oder stuende), ist der Hinweis ausgeraeumt. Bleibt er stehen, ist das Ergebnis unklar.
//
// Liefert (Grund, probeFail), (Grund, probeAbort) fuer "unklar" oder ("", "").
func (l *Leg) probeJudge() (string, string) {
	p, e := &l.pr, &l.est
	ev, fall := e.prEv, e.prFall
	switch {
	case fall == fallDec, fall != 0 && ev != "":
		return "gcc", probeFail
	case fall == fallClamp:
		p.clamps++
		fall = 0
	}
	hint := ev
	if fall == fallAmb {
		hint = "gcc"
	}
	v := e.gccLast
	switch {
	case hint != "" && p.sus > 0:
		return p.susWhy, probeFail
	case hint != "":
		if p.susN >= probeSusMax {
			return hint, probeAbort
		}
		p.sus, p.susWhy, p.susStall = probeConfirm, hint, 0
	case p.sus > 0 && v > p.susV:
		p.susStall = 0
		if p.sus--; p.sus == 0 {
			p.susN++
		}
	case p.sus > 0:
		if p.susStall++; p.susStall >= probeStall {
			return p.susWhy, probeAbort
		}
	}
	p.susV = v
	return "", ""
}

// probeEnd beendet die Probe mit dem Ergebnis res (probeOK, probeFail, probeAbort).
func (l *Leg) probeEnd(res, why string, now int64) {
	p := &l.pr
	l.fl.SetPadding(nil, 0)
	e := &l.est
	var d *dwell
	if p.s.owner == p.owner && p.owner != nil {
		d = p.s.dwFor(p.rank)
	}
	if res != probeAbort {
		l.unkAt, l.unkN, l.unkRamp = 0, 0, false
		l.lastJ.rate, l.lastJ.failed, l.lastJ.at = p.target, res == probeFail, now
	}
	l.pstat.Clamped += uint64(p.clamps)
	switch res {
	case probeOK:
		l.pstat.Passed++
		e.shieldOff(now, 0)
		e.ready = e.prReady
		e.grant, e.grantUntil = p.target, now+int64(probeGrant)
		e.proofUntil = now + int64(probeKeep) // eigen: Halten nach bestandener Probe (estimator.held)
		if e.bps < p.target {
			e.bps = min(p.target, e.max)
		}
		l.pf.pass(p.stepT)
		if p.long {
			l.fb.passLong(p.stepT)
		}
		if d != nil {
			d.next, d.ok = now+int64(probeGap), now
		}
	case probeFail:
		l.pstat.Failed++
		e.shieldOff(now, probeFloor)
		if p.long {
			l.pf.failMax(p.stepT, now, probeGapMaxLong)
			l.fb.failLong(now, l.pf.pd) // die Enge besteht fort: die Bremse bleibt (freshBrake.decay)
		} else {
			l.pf.fail(p.stepT, now)
		}
		l.firstUsed = true
		if d != nil {
			d.ok = 0
		}
	default:
		l.pstat.Aborted++
		if why == whyUnclear {
			// wie nach einer gescheiterten Probe: Ein Stau, den die Fuelldaten erzeugt haben koennten, aendert die
			// Schaetzung nicht; die Wiederholung kommt nach probeRetry
			l.pstat.Unclear++
			l.unkAt = now
			e.shieldOff(now, probeFloor)
		} else {
			e.shieldOff(now, 0)
		}
		l.prNext = now + int64(probeRetry)
		if why == "fuell" {
			// Die Fuelldaten sind nicht geflossen (Quelle still, Ausgang wartet): Der naechste Versuch braechte dasselbe -
			// Abstand wie nach einer gescheiterten Probe, statt alle probeRetry von vorn
			l.pf.fail(p.stepT, now)
		}
	}
	p.s.setProbe(res, why, p.target)
	if l.pstat.Last = res; why != "" {
		l.pstat.Last += ":" + why
	}
	if l.room.cfg.OnProbe != nil {
		to := ""
		for _, k := range [...]string{"l", "m", "h"} {
			if layerRank(k) == p.rank {
				to = k
			}
		}
		l.room.pevs = append(l.room.pevs, ProbeEvent{Leg: l, Class: p.s.class, From: p.layer, To: to, TargetKbit: int(p.target / 1000),
			FillKbit: int(p.fill / 1000), Result: res, Why: why, Dur: time.Duration(now - p.t0), Stage: p.stage, Clamped: p.clamps, Hint: p.susWhy,
			EstKbit: int(e.bps / 1000), GccKbit: int(e.gccLast / 1000),
			Det: p.det, GccSig: p.gsig, SentKbit: int(p.detSent / 1000), AckedKbit: int(p.detAcked / 1000)})
	}
	*p = probe{}
}

// legFail: Abstand nach gescheiterten Proben, je Bein und Zielrate. Eine gescheiterte Probe mit Zielrate X sperrt
// bis next jede Probe mit einer Zielrate ab probeNear x X - gleich fuer welchen Platz und welchen Besitzer (in Gruppen
// wechselt der Sprecherplatz laufend den Besitzer; hinge der Abstand am Platz, begaenne er jedes Mal von vorn). Proben
// mit deutlich kleinerer Zielrate duerfen frueher. Bis eine Probe ab dieser Zielrate besteht, gibt es dafuer auch
// keinen Versuch ohne Probe (covers).
type legFail struct {
	rate float64       // kleinste gescheiterte Zielrate (bit/s)
	pd   time.Duration // aktueller Abstand (0 = keine Vorgeschichte)
	next int64         // fruehestens dann die naechste Probe ab dieser Zielrate
	n    int           // gescheiterte Proben in Folge
}

// probeFresh: Faellt ein Schritt, der mit einer bestandenen Probe kam, binnen dieser Zeit zurueck (nach upFail und
// ohne gescheiterte Schonfrist), ist das ein frischer Rueckfall (freshBrake): Die Leitung trug die Probe, aber nicht
// die Schicht. UDP-Aufbau mit Token-Bucket-Policer 1450 bzw. 1500 kbit/s (Eimer 150 bzw. 100 ms): alle kurzen Proben
// bestanden, die Schritte fielen nach 3,1 bis 13,4 s zurueck - immer wieder, ohne dass Aufschub oder Abstand wuchsen.
// Variable fuer Tests.
var probeFresh = 15 * time.Second

// probeFreshN: so viele frische Rueckfaelle in Folge zaehlen als echter Fehlversuch (Aufschub; planLeg: fast). Der
// erste fuehrt nur zur langen Probe (freshBrake.long) - auf einer freien Leitung mit Stoerschueben
// (TestProbingStoerschuebe) faellt auch mal ein Schritt bald nach der Probe, der naechste haelt dann. Neben der langen
// Probe noch noetig fuer Eimer, die auch sie nicht leert (Modell, Eimer 0,6 s, 7 % unter h: ohne die Regel 36 statt 9
// Rueckfaelle in 10 min, Gruppe mit Sprecherwechsel 71 bzw. 98 statt 28). Variable fuer Tests.
var probeFreshN = 2

// freshBrake: Bremse nach frischen Rueckfaellen (probeFresh), je Bein und Zielrate - nicht je Platz (Stufe 3b). In
// einer Gruppe wechselt der Sprecherplatz laufend den Besitzer; hing die Bremse am Platz (Aufschub s.dw, Zaehler der
// frischen Rueckfaelle), begann sie mit jedem Sprecherwechsel von vorn, und die naechste kurze Probe bestand wieder
// (Pruefer bwe3: Gruppe 3, Wechsel alle 20 s, Eimer 0,15 s - 105 Rueckfaelle in 10 min, ohne die Regel ebenso viele).
// Wie legFail haengt sie an der Leitung des Empfaengers: Sie gilt fuer jeden Schritt ab probeNear x rate.
//
//   - Der erste frische Rueckfall: Die naechste Probe ab dieser Zielrate faehrt lang (probeWinLong, probeHeadLong) -
//     sie leert den Eimer eines Policers, den die kurze nicht sieht. Bis dahin geht kein solcher Schritt ohne
//     bestandene Probe (auch nicht der freie Versuch). Besteht die lange Probe, gilt wieder die kurze; scheitert sie,
//     waechst der Proben-Abstand (legFail), und die naechste ist wieder lang.
//   - Der probeFreshN-te in Folge (der Eimer ist so gross, dass auch die lange Probe ihn nicht leert): Fehlversuch -
//     zusaetzlich zum Aufschub des Platzes ein Aufschub am Bein (8, 16, 32 ... s, die Probe danach hoechstens nach
//     probeDwellMax), den keine bestandene Probe und kein Besitzerwechsel aufhebt.
//   - Ein Rueckfall eines solchen Schritts nach probeFresh setzt die Folge zurueck (die lange Probe bleibt faellig).
//     Ganz erledigt ist die Bremse nach upHold (+ Aufschub) ohne neues Zeichen der Enge (frischer Rueckfall,
//     gescheiterte lange Probe) - wie "upHold Halten setzt zurueck" beim Aufschub.
//
// Nur Rueckfaelle nach dem Muster eines Policers zaehlen (Stufe 3c): Der Policer nimmt den Schritt zurueck, die Leitung
// traegt aber weiter, was vor dem Schritt lief (sein Eimer fuellt sich darunter wieder) - und das immer wieder auf
// dieselbe Zielrate. Ein Stoerschub (Querverkehr) dagegen drueckt das Bein unter den Stand vor dem Schritt, oft alle
// Plaetze zugleich; er waere auch ohne den Schritt gekommen (Pruefer bwe3b: Gruppe 3, Wechsel 20 s, freie Leitung mit
// Schueben - jeder Schub zaehlte als frischer Rueckfall, Sprecher auf h 47 -> 12 %, Aufschub am Bein ohne Ende). Kein
// frischer Rueckfall ist deshalb:
//   - ein Einbruch (slump): Budget bzw. GCC-Ziel lagen zwischen dem Schritt und dem Rueckfall unter probeSlump x Rate
//     des Beins vor dem Schritt (der Schub beginnt oft, bevor der Platz faellt - das GCC-Ziel ist dann schon wieder im
//     Steigen). Er unterbricht auch die Folge (n): Zwischen zwei Rueckfaellen eines Policers liegt kein Verlust darunter;
//   - eine Umverteilung (der Platz faellt, weil ein anderer steigt; die Auswahl des Beins sinkt nicht, planLeg);
//   - ein Rueckfall, nachdem das Bein ohne Probe ueber den geprobten Schritt hinaus gestiegen ist (beyond).
//
// Ein frischer Rueckfall auf eine kleinere Zielrate als die der laufenden Bremse senkt sie (wie in Stufe 3b): Liegt
// ein Policer zwischen zwei Zwischenbelegungen, faellt erst die groessere, dann die kleinere (Pruefer bwe3c: Gruppe 3,
// Policer 1580k zwischen mmm und hll - hielt die Bremse an hml fest, fiel hll alle 20 bis 40 s zurueck, und die
// scheiternden langen Proben auf hml frischten die Bremse immer wieder auf: 77 bis 94 statt 9 bis 12 Rueckfaelle).
type freshBrake struct {
	rate float64       // kleinste Zielrate der frisch zurueckgefallenen Schritte (Mass wie legFail: mit Reserve und Ton)
	n    int           // frische Rueckfaelle in Folge
	long bool          // die naechste Probe ab probeNear x rate faehrt lang
	at   int64         // letztes Zeichen der Enge (frischer Rueckfall, gescheiterte lange Probe); 0 = keine Bremse
	d    time.Duration // Aufschub am Bein
	next int64         // ... vorher keine Probe ab probeNear x rate
	gap  time.Duration // Abstand nach der letzten gescheiterten langen Probe (legFail.pd)
	// letzter Schritt des Beins mit bestandener Probe (0 = keiner offen), die Zielrate des Beins danach und davor,
	// kleinstes Budget bzw. GCC-Ziel seither (0 = noch keins)
	upAt  int64
	upT   float64
	upPre float64
	upLow float64
}

// probeSlump: Lagen Budget bzw. GCC-Ziel zwischen dem geprobten Schritt und dem Rueckfall unter diesem Anteil der Rate
// des Beins vor dem Schritt (Bild + Ton, ohne Reserve), ist das ein Einbruch, kein frischer Rueckfall (freshBrake). Ein
// Policer liegt ueber dieser Rate (sonst fiele schon das Laufende); springt GCC an ihm auf g x seine Rate zurueck, bleibt
// das Verhaeltnis also ueber g - auch wenn der Policer knapp ueber einer Zwischenbelegung liegt. Gemessen: g ~0,85
// (Modell), ~0,82 (UDP-Aufbau 1:1, Token-Bucket 1450/150 ms: Minimum 1183k). Bis Stufe 3c stand hier 0,7 x Zielrate
// (mit Reserve, also ~0,8 x Rate): Ab g < ~0,8 galt ein solcher Policer als Einbruch (Pruefer bwe3c, Modell mit
// g = 0,76: Gruppe mit Policer 1650-2030k 1298 statt 715 Rueckfaelle im Raster; mit 0,75 x Rate 634). Der Stoerschub
// im Modell (GCC-Ziel 600k) bleibt ein Einbruch ab einer Rate von 800k vor dem Schritt. Variable fuer Tests.
var probeSlump = 0.75

// stepped: Ein Platz ist mit bestandener Probe hinaufgegangen; das Bein hat jetzt die Zielrate t, die Rate vorher war
// pre (Bild + Ton, ohne Reserve).
func (b *freshBrake) stepped(t, pre float64, now int64) {
	b.upAt, b.upT, b.upPre, b.upLow = now, t, pre, 0
}

// watch: je Takt das kleinere von Budget und GCC-Ziel (Mass wie die Zielraten) - fuer den Einbruch (down).
func (b *freshBrake) watch(bud float64) {
	if b.upAt != 0 && (b.upLow == 0 || bud < b.upLow) {
		b.upLow = bud
	}
}

// Einordnung eines Rueckfalls nach einem geprobten Schritt (freshBrake, Logzeile "frisch").
const (
	freshCount  = "zaehlt"       // frischer Rueckfall (Muster eines Policers)
	freshFail   = "fehlversuch"  // ... der probeFreshN-te in Folge: Aufschub am Bein
	freshSlump  = "einbruch"     // Budget bzw. GCC-Ziel unter probeSlump x Rate vor dem Schritt
	freshHeld   = "gehalten"     // der Schritt hielt laenger als probeFresh
	freshSpread = "umverteilung" // ein Platz faellt, die Auswahl des Beins sinkt nicht (der Schritt bleibt offen)
	freshBeyond = "getragen"     // das Bein stieg ohne Probe darueber hinaus (der Schritt ist erledigt)
)

// open: offener geprobter Schritt - Zielrate, Rate davor und kleinstes Budget bzw. GCC-Ziel seither samt bud (fuer
// die Logzeile; ok = false: keiner offen bzw. aus diesem Takt).
func (b *freshBrake) open(now int64, bud float64) (t, pre, low float64, ok bool) {
	if b.upAt == 0 || b.upAt == now {
		return 0, 0, 0, false
	}
	low = bud
	if b.upLow > 0 {
		low = min(low, b.upLow)
	}
	return b.upT, b.upPre, low, true
}

// down: Ein Platz faellt wegen des Budgets zurueck, und die Auswahl des Beins sinkt (keine Umverteilung, planLeg). Kam
// der letzte geprobte Schritt des Beins binnen probeFresh davor, ist das ein frischer Rueckfall (fall) - ausser bei
// einem Einbruch (watch, bud: kleineres von Budget und GCC-Ziel in diesem Takt); sonst hielt er (held). Liefert die
// Einordnung ("" = kein offener Schritt bzw. im selben Takt: Umverteilung durch die Planung, kein Rueckfall der
// Leitung) und ob er als Fehlversuch zaehlt.
func (b *freshBrake) down(now int64, bud float64) (string, bool) {
	t, pre, low, ok := b.open(now, bud)
	if !ok {
		return "", false
	}
	at := b.upAt
	b.upAt = 0
	switch {
	case now-at >= int64(probeFresh):
		b.held(t)
		return freshHeld, false
	case low < probeSlump*pre:
		b.n = 0 // Einbruch: Verlust bzw. Stau auch unter dem Stand vor dem Schritt - kein Muster eines Policers
		return freshSlump, false
	case b.fall(t, now):
		return freshFail, true
	}
	return freshCount, false
}

// beyond: Das Bein ist ohne Probe weiter gestiegen, auf die Zielrate t. Liegt sie ueber der des offenen geprobten
// Schritts, hat die Leitung ihn getragen; ein spaeterer Rueckfall gilt dem neuen Schritt, nicht ihm (Pruefer bwe3b:
// l -> m geprobt, dann ohne Probe bis hml, Sprecherwechsel, Stoerschub 13 s danach - zaehlte als frischer Rueckfall).
// Liefert, ob das einen offenen Schritt erledigt hat.
func (b *freshBrake) beyond(t float64) bool {
	if b.upAt != 0 && t > b.upT {
		b.upAt = 0
		return true
	}
	return false
}

// on: Gilt die Bremse fuer einen Schritt mit Zielrate t?
func (b *freshBrake) on(t float64) bool { return b.at != 0 && t >= b.rate*probeNear }

// capNeed: Deckel der Auswahl (Bildrate ohne Ton, bit/s), solange die lange Probe aussteht (0 = keiner): Die
// Zielrate des Beins (Bildrate x upHeadroom + Ton) bleibt unter probeNear x rate. audio ist der Tonabzug.
func (b *freshBrake) capNeed(audio float64) float64 {
	if !b.long || b.at == 0 {
		return 0
	}
	return max((b.rate*probeNear-audio)/upHeadroom, 1)
}

// longFor: Faehrt eine Probe mit Zielrate t lang?
func (b *freshBrake) longFor(t float64) bool { return b.long && b.on(t) }

// blocks: Ist eine Probe mit Zielrate t wegen des Aufschubs am Bein noch gesperrt?
func (b *freshBrake) blocks(t float64, now int64) bool { return b.on(t) && now < b.next }

// fall: Ein Schritt mit Zielrate t ist frisch zurueckgefallen. Liefert, ob das als Fehlversuch zaehlt.
func (b *freshBrake) fall(t float64, now int64) bool {
	if b.at == 0 || t < b.rate {
		b.rate = t
	}
	b.n++
	b.long, b.at, b.gap = true, now, 0
	if b.n < probeFreshN {
		return false
	}
	b.d = min(2*max(b.d, layerDwell), upDwellMax)
	b.next = max(b.next, now+int64(min(b.d, probeDwellMax)))
	return true
}

// held: Ein Schritt mit Zielrate t ab dieser Bremse hielt laenger als probeFresh - die Folge beginnt von vorn.
func (b *freshBrake) held(t float64) {
	if b.on(t) {
		b.n = 0
	}
}

// passLong: lange Probe mit Zielrate t bestanden - wieder die kurze.
func (b *freshBrake) passLong(t float64) {
	if b.on(t) {
		b.long, b.gap = false, 0
	}
}

// failLong: lange Probe gescheitert - die Enge besteht fort; die naechste Probe kommt nach dem Abstand gap (legFail).
func (b *freshBrake) failLong(now int64, gap time.Duration) {
	if b.at != 0 {
		b.at, b.gap = now, gap
	}
}

// decay: upHold (+ Aufschub bzw. Abstand bis zur naechsten langen Probe) ohne neues Zeichen der Enge - die Bremse ist
// erledigt (ein offener Schritt bleibt offen).
func (b *freshBrake) decay(now int64) {
	if b.at != 0 && now >= b.at+int64(upHold)+int64(max(b.d, b.gap)) {
		*b = freshBrake{upAt: b.upAt, upT: b.upT, upPre: b.upPre, upLow: b.upLow}
	}
}

// probeNear: Zielraten ab diesem Anteil einer gescheiterten gelten als "dieselbe oder hoeher".
const probeNear = 0.9

// probeBlindAfter: Nach so vielen gescheiterten Proben in Folge darf ein Schritt einmal ohne bestandene Probe versucht
// werden (wie ohne Probing, mit Aufschub, falls er scheitert). 0 = nie (Standard). Hintergrund: Die Probe prueft die
// Zielrate mit Reserve (Bedarf x upHeadroom). Eine Leitung zwischen Bedarf und Bedarf x upHeadroom traegt die Schicht,
// besteht die Probe aber nie - mit 0 erreicht sie die Schicht mit Probing nicht. Ob das so bleibt, entscheidet sich
// nach dem Labor; ein Wert wie 3 gaebe solchen Leitungen nach 10 + 20 + 40 s einen Versuch. Variable fuer Tests.
var probeBlindAfter = 0

// covers: Braucht ein Schritt mit Zielrate t eine bestandene Probe, weil eine Probe ab dieser Rate gescheitert ist?
func (f *legFail) covers(t float64) bool {
	if f.pd == 0 || t < f.rate*probeNear {
		return false
	}
	return probeBlindAfter <= 0 || f.n < probeBlindAfter
}

// blocks: Ist eine Probe mit Zielrate t jetzt noch gesperrt?
func (f *legFail) blocks(t float64, now int64) bool {
	return f.pd > 0 && t >= f.rate*probeNear && now < f.next
}

// fail: Probe (bzw. echter Versuch) mit Zielrate t gescheitert - Abstand mal probeGapUp; eine kleinere gescheiterte
// Zielrate senkt die Schwelle, ohne den Abstand zurueckzusetzen.
func (f *legFail) fail(t float64, now int64) { f.failMax(t, now, probeGapMax) }

// failMax: wie fail, Abstand hoechstens mx (lange Probe: probeGapMaxLong).
func (f *legFail) failMax(t float64, now int64, mx time.Duration) {
	switch {
	case f.pd == 0:
		f.rate, f.pd = t, probeGap
	case t >= f.rate*probeNear:
		f.pd = min(time.Duration(float64(f.pd)*probeGapUp), mx)
		f.rate = min(f.rate, t)
	default:
		f.rate = t
	}
	f.next = now + int64(f.pd)
	f.n++
}

// pass: Probe mit Zielrate t bestanden - die Vorgeschichte ab dieser Rate ist erledigt.
func (f *legFail) pass(t float64) {
	if f.pd > 0 && t >= f.rate*probeNear {
		*f = legFail{}
	}
}

// decay: Will seit dem Ende der Sperre lange niemand mehr so hoch hinaus (Abstand + upHold ohne neue Probe), ist die
// Vorgeschichte erledigt - entspricht "upHold Halten der Schicht setzt zurueck" je Platz.
func (f *legFail) decay(now int64) {
	if f.pd > 0 && now >= f.next+int64(f.pd)+int64(upHold) {
		*f = legFail{}
	}
}

// Freier Versuch: Zwei Faelle, in denen ein Schritt nach oben ohne bestandene Probe versucht werden darf - zeitlich wie
// ohne Probing (layerDwell bzw. Aufschub, die Schaetzung waechst bis zum Bedarf des Schritts, der Wechsel selbst ist
// die Probe). Scheitert der Versuch, gilt wieder die Proben-Pflicht.
//
//   - Rueckkehr (Platz): Faellt ein Platz von einer Schicht zurueck, die er mindestens upHold gehalten hat, darf er
//     einmal ohne Probe wieder hinauf. Die Leitung hat die Schicht eben noch getragen; im Labor (10./11.11.) blieb das
//     Bild sonst nach einem gewoehnlichen Rueckfall bis 279 s unten statt 4 bis 6 s. Haelt die Schicht danach wieder
//     upHold, gibt es beim naechsten Rueckfall erneut einen freien Versuch; faellt sie frueher, nicht.
//   - Anrufbeginn (Bein): Der erste Schritt nach oben eines Beins, den die Schaetzung allein nicht hergibt. Zu Beginn
//     gibt es keine Vorgeschichte, die eine Probe rechtfertigt, und die Probe kostete gut 4 s (Labor: h stabil nach
//     12,8 s statt 8,8 s). Verbraucht mit dem Versuch, mit der ersten gescheiterten Probe und mit dem ersten Rueckfall
//     eines Platzes - danach greift Probing. Nicht als Rueckfall zaehlt dabei der Fall von einer Schicht, deren Quelle
//     beim Schritt hinauf noch nicht eingeschwungen war (unramped in planLeg; zu Anrufbeginn die Regel).
const probeRampUp = 1.3 // Quelle sendet so viel mehr als beim Schritt hinauf: Der Schritt war keine Probe der Leitung

// freeStep: Darf der Platz s jetzt ohne Probe auf die Schicht rank?
func (l *Leg) freeStep(s *slot, rank int) bool {
	return s.owner != nil && s.freeOwner == s.owner && s.free[rank] || !l.firstUsed
}

// freeUsed: Der Platz ist ohne Probe auf die Schicht rank gegangen, und der Schritt brauchte den freien Versuch - er
// ist verbraucht (der des Platzes zuerst, sonst der des Beins).
func (l *Leg) freeUsed(s *slot, rank int) {
	if s.freeOwner == s.owner && s.free[rank] {
		s.free[rank] = false
		return
	}
	l.firstUsed = true
}

// freeGrant: Der Platz faellt von der Schicht from auf to; from lief heldFor lang. Hat sie upHold gehalten, darf er
// einmal ohne Probe zurueck (jede Schicht dazwischen ebenso). Der freie Versuch des Beins endet mit dem ersten
// Rueckfall - ausser die Quelle war beim Schritt hinauf noch nicht eingeschwungen (unramped).
func (l *Leg) freeGrant(s *slot, to, from int, heldFor int64, unramped bool) {
	if s.freeOwner != s.owner {
		s.free, s.freeOwner = [4]bool{}, s.owner
	}
	if heldFor >= int64(upHold) {
		for k := to + 1; k <= from && k < len(s.free); k++ {
			s.free[k] = true
		}
	}
	if !unramped {
		l.firstUsed = true
	}
}

// afterFail: Ein echter Wechsel auf diese Schicht ist gescheitert bzw. wurde zurueckgenommen (Aufschub d.d gesetzt) -
// die naechste Probe fruehestens nach dem Aufschub, hoechstens nach probeDwellMax. Ohne Probing ohne Wirkung.
func (d *dwell) afterFail(now int64) {
	d.next, d.ok = max(d.next, now+int64(min(d.d, probeDwellMax))), 0
}

// setProbe haelt ein Probe-Ereignis fuer die naechste Layout-Meldung fest.
func (s *slot) setProbe(ev, why string, target float64) {
	s.pev, s.pwhy, s.pkbit = ev, why, int(target/1000)
}

// shieldOn: Eine Probe beginnt - bis shieldOff (und danach floor lang) beobachtet der Schaetzer Stausignale nur
// (prSig, prEv, prFall), statt die Schaetzung zu aendern. loss0 ist der Verlustanteil vor der Probe.
func (e *estimator) shieldOn(now int64, loss0 float64) {
	e.prOn, e.prSig, e.prPeak, e.prUntil, e.prTail = true, "", e.gccLast, 0, false
	e.prEv, e.prFall, e.prBase, e.prLoss0, e.prLossN = "", 0, e.rateAvg, loss0, 0
}

// Einordnung eines gefallenen GCC-Ziels waehrend der Probe (estimator.prFall).
const (
	fallClamp = 1 + iota // kein Ruecksprung: das Ziel liegt ueber dem, was GCC nach einem Ruecksprung haben koennte
	fallAmb              // nicht zu unterscheiden
	fallDec              // Ruecksprung: das Ziel liegt unter dem, was eine Klemmung ergaebe
)

// fallClass: Das GCC-Ziel v ist waehrend der Probe um mehr als gccFall gefallen - warum?
//
// pion-GCC senkt sein Ziel auf zwei Arten. (1) Ruecksprung bei Stau: auf 85 % der empfangenen Rate (Fenster 500 ms).
// (2) Klemmung ohne jeden Stau: Liegt die empfangene Rate nahe der Rate beim letzten Ruecksprung (dessen Mittel
// +- 3 Streuungen), rechnet der Anstieg min(Ziel + Schritt, 1,5 x empfangen) - ein Ziel, das seit dem Ruecksprung weit
// ueber das 1,5-Fache gewachsen ist (GCC ist ohne Fuelldaten anwendungsbegrenzt und waechst mit 8 %/s ungebremst),
// faellt dabei in einem Zug auf 1,5 x empfangen. Die Fuelldaten einer Probe schieben die empfangene Rate selbst in
// dieses Band - das war die Ursache der falsch gescheiterten Proben auf freier Leitung (Labor 10./11.11.: bis 54 %;
// im Container ohne jedes Rauschen nachgestellt). Unterscheiden laesst sich beides an der eigenen Senderate der
// letzten 500 ms (estimator.sendRate): Nach (1) liegt das Ziel darunter, nach (2) auf freier Leitung beim 1,5-Fachen
// der Rate, die schon vor der Probe lief, oder darueber.
//
// Wie scharf das trennt: Auf Dauer wird hoechstens empfangen, was gesendet wurde - in einem einzelnen Fenster nicht.
// Zum einen muessen beide Seiten dieselben Bytes zaehlen: pion rechnet je Paket Kopf samt Erweiterungen + Nutzlast
// (8 Byte je Paket mehr als BytesOut; bei Fuellpaketen 3 %), deshalb zaehlt sendRate sie mit (LegStats.HdrExtra). Zum
// anderen teilt pion die Bytes seines Fensters durch die Zeit zwischen dessen erstem und letztem Paket (das erste Paket
// zaehlt mit, die Zeit davor nicht), und sein Fenster liegt um den Weg der Rueckmeldung versetzt - bei Bildern, die in
// Schueben kommen, liegt seine Rate dadurch im Mittel 3 bis 4 % ueber der Senderate (gleiche Bytes; in 5 % der Takte
// ueber 10 %), bei einem Ruecksprung gemessen bis 16 %. Die Schwelle probeDecSent = 1,0 laesst dafuer 17 % (bis
// Pruefung 3: 0,87, also 2 % - weniger als allein der Unterschied der Zaehlung). Viel Abstand ist das nicht; hoeher
// kann die Schwelle nicht liegen, ohne Klemmungen zu verfehlen (gemessen ab 105 % der Senderate).
func (e *estimator) fallClass(v float64) int {
	switch {
	case v < probeDecBase*e.prBase:
		return fallDec
	case v > probeDecSent*e.sendRate():
		return fallClamp
	}
	return fallAmb
}

func (e *estimator) shieldOff(now int64, floor time.Duration) {
	e.prOn, e.prSig = false, ""
	e.prUntil, e.prN, e.prBad = 0, 0, 0
	if floor > 0 {
		e.prUntil = now + int64(floor)
	}
}

// Aufschub und Proben-Abstand haengen am Platz eines Beins. Wechselt ein Geraet den Weg (udp -> tcp -> udp), entsteht
// ein neues Bein, und beides begaenne von vorn - auf einer Leitung, die h nicht traegt, kaeme nach jedem Wegwechsel
// sofort wieder ein Versuch. Deshalb merkt sich der Raum die Eintraege eines entfernten Beins je (Empfaenger, Besitzer
// der Quelle, Platzart) fuer memKeep und gibt sie dem naechsten Platz desselben Empfaengers fuer denselben Besitzer
// zurueck - dazu je Empfaenger den Proben-Abstand des Beins und ob sein freier Versuch zum Anrufbeginn verbraucht ist.
// Nur mit Probing (Schalter aus: Verhalten wie zuvor).
const (
	memKeep = upDwellMax // so lange nach dem Entfernen des Beins
	memMax  = 512        // Eintraege je Raum
)

type memKey struct{ recv, owner, class string }

type memEnt struct {
	dw    [4]dwell
	pf    legFail    // nur im Eintrag des Beins (Besitzer und Platzart leer)
	fb    freshBrake // ... ebenso
	first bool       // ... ebenso: der freie Versuch zum Anrufbeginn ist verbraucht (Leg.firstUsed)
	until int64
}

// remember merkt sich Aufschub und Proben-Abstand der Plaetze eines Beins, das entfernt wird.
func (r *Room) remember(l *Leg) {
	if !r.cfg.Probing {
		return
	}
	now := r.lastTick
	for k, e := range r.mem {
		if now >= e.until {
			delete(r.mem, k)
		}
	}
	if r.mem == nil {
		r.mem = map[memKey]memEnt{}
	}
	// Der verbrauchte freie Versuch zum Anrufbeginn gehoert dazu, auch ohne gescheiterte Probe und ohne Aufschub (h
	// haelt seit dem ersten Schritt) - sonst lebte er mit jedem Wegwechsel wieder auf
	if (l.pf.pd > 0 || l.fb.at != 0 || l.firstUsed) && len(r.mem) < memMax {
		r.mem[memKey{recv: l.Peer.ID}] = memEnt{pf: l.pf, fb: l.fb, first: l.firstUsed, until: now + int64(memKeep)}
	}
	for _, s := range l.slots {
		if s.dwellOwner == nil || s.dw == ([4]dwell{}) || len(r.mem) >= memMax {
			continue
		}
		r.mem[memKey{l.Peer.ID, s.dwellOwner.ID, s.class}] = memEnt{dw: s.dw, until: now + int64(memKeep)}
	}
}

// recall gibt gemerkte Eintraege an Plaetze zurueck, die fuer ihren Besitzer noch keine eigene Vorgeschichte haben.
func (r *Room) recall(l *Leg, vslots []*slot, now int64) {
	if l.pf.pd == 0 && l.fb.at == 0 {
		k := memKey{recv: l.Peer.ID}
		if e, ok := r.mem[k]; ok {
			delete(r.mem, k)
			if now < e.until {
				l.pf, l.fb = e.pf, e.fb
				// Vorgeschichte von vor dem Wegwechsel bzw. dort schon verbraucht: kein freier Versuch zum Anrufbeginn
				l.firstUsed = l.firstUsed || e.first || e.pf.pd > 0
			}
		}
	}
	for _, s := range vslots {
		if s.owner == nil || s.dwellOwner == s.owner {
			continue
		}
		k := memKey{l.Peer.ID, s.owner.ID, s.class}
		e, ok := r.mem[k]
		if !ok {
			continue
		}
		delete(r.mem, k)
		if now < e.until {
			s.dw, s.dwellOwner = e.dw, s.owner
			l.firstUsed = true
		}
	}
}
