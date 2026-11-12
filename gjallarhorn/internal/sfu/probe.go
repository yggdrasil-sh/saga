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
//     ohne Umlaufzeit probeWin)
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
// Abstand: nach einer gescheiterten Probe probeGap, verdoppelnd bis probeGapMax - je Bein und Zielrate (legFail), nicht
// je Platz; eine bestandene Probe ab dieser Zielrate oder laengere Ruhe setzt zurueck. Nach einer bestandenen Probe
// fruehestens probeGap spaeter die naechste fuer denselben Schritt (falls er aus anderem Grund ausbleibt).
//
// Nicht geprobt wird ein Schritt, dessen Fuellrate ueber probeFillMax laege: Er laeuft wie ohne Probing (planLeg).
//
// Verhaeltnis zum Aufschub (dwell): Der Aufschub entsteht unveraendert, wenn ein echter Wechsel scheitert. Mit Probing
// ist danach fuer einen neuen Versuch auf diese Schicht eine bestandene Probe noetig - der blosse Ablauf des Aufschubs
// genuegt nicht mehr (genau diese blinden Versuche kosten Schluesselbilder). Dafuer darf schon waehrend des Aufschubs
// geprobt werden, sobald der Proben-Abstand um ist, und eine bestandene Probe erlaubt den Versuch vorzeitig. Die Probe
// verlaengert den Aufschub nie. Nach einem echten Fehlversuch (bzw. einer Budget-Ruecknahme mit Vorgeschichte) wird
// fruehestens nach dem Aufschub geprobt, hoechstens aber nach probeGapMax (dwell.afterFail): Besteht die Probe
// wiederholt, waehrend der Wechsel scheitert, kommen die Versuche so nie haeufiger als ohne Probing (8, 16, 32 ... s) -
// eine bestandene Probe setzt nur den Abstand nach gescheiterten Proben zurueck, nicht diesen. "Vorzeitig" wirkt also
// bei langen Aufschueben (ueber 2 min bis 5 min): Eine frei gewordene Leitung ist nach gut 2 min wieder oben (Probe
// spaetestens 2 min nach dem letzten Fehlversuch, dazu Ruhezeit, Probe und Wechsel - gemessen bis 2 min 43 s).
const (
	probeRamp     = 2                       // Takte mit halber Fuellrate
	probeMinTicks = 3                       // Mindestlaenge des Fensters in Takten
	probeWin      = time.Second             // Fenster ohne bekannte Umlaufzeit
	probeWinMax   = 1500 * time.Millisecond // laengstes Fenster
	probeRTTs     = 2.5                     // Fenster in Umlaufzeiten
	probeSettle   = 2                       // Takte Nachlauf
	probeFillMax  = 1500e3                  // hoechste Fuellrate (bit/s)
	probeMinFill  = 0.6                     // so viel der Fuellrate muss im Fenster wirklich gesendet worden sein
	probeGap      = 10 * time.Second        // Abstand nach einer gescheiterten Probe (verdoppelnd)
	probeGapMax   = 2 * time.Minute
	probeRetry    = 2 * time.Second // Abstand nach einer abgebrochenen Probe (Belegung geaendert)
	probeQuiet    = layerDwell      // so lange muss die Bild-Auswahl des Beins unveraendert laufen, bevor geprobt wird
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
	probeUnkMax   = 3                      // so viele Proben ohne klares Ergebnis in Folge (je binnen probeUnkGap): die letzte zaehlt als gescheitert
	probeUnkGap   = time.Minute            // ... laenger auseinander zaehlen sie nicht als Folge (die Wiederholung kann auf ein erholtes GCC-Ziel warten muessen)
	probeSentMin  = 0.9                    // Durchsatz-Nachweis: bei voller Fuellung gesendet mindestens so viel der Zielrate ...
	probeAckMin   = 0.95                   // ... und davon vom Empfaenger im selben Zeitraum bestaetigt mindestens so viel
	probeProofMin = 400 * time.Millisecond // ... gemessen ueber mindestens so viel Sendezeit
	probeTail     = 500 * time.Millisecond // Nachlauf bis zum Urteil ohne bekannte Umlaufzeit (sonst 2 Umlaufzeiten + 200 ms, hoechstens 1 s)
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
	target float64  // Zielrate des Beins (bit/s, mit Ton)
	fill   float64  // volle Fuellrate (bit/s)
	win    int64    // Dauer des Fensters
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

// ProbeStats zaehlt die Proben eines Beins.
type ProbeStats struct {
	Started, Passed, Failed, Aborted uint64
	Unclear                          uint64 // davon (Aborted) ohne klares Ergebnis beendet und wiederholt
	Refuted                          uint64 // eigen: Faelle des GCC-Ziels unter die Rate, die der Detektor widerlegt hat (estimator.refute)
	Clamped                          uint64 // Faelle des GCC-Ziels in Proben, die kein Stau waren (probeJudge)
	PadBytes                         uint64 // gesendete Fuell-Bytes
	LateDropped                      uint64 // verworfene Nachzuegler jenseits der Fuell-Historie (fwd.LegStats.LateDropped)
	Last                             string // Ausgang der letzten Probe: ok, fail:<grund> bzw. abort:<grund>
}

// ProbeStats liefert die Zaehler der Proben dieses Beins.
func (l *Leg) ProbeStats() ProbeStats {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	ps := l.pstat
	ps.PadBytes = l.fl.Stats.PadBytes.Load()
	ps.LateDropped = l.fl.Stats.LateDropped.Load()
	ps.Refuted = l.est.refuted
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
	target := need*upHeadroom + audio
	fill := target - sum - audio
	if fill > probeFillMax {
		// Freigegeben wird nur, was wirklich geprobt wurde. (planLeg probt Schritte mit groesserer Fuellrate gar nicht
		// erst; das hier greift nur, wenn die Raten seither gewandert sind.)
		fill = probeFillMax
		target = sum + audio + fill
	}
	if fill <= 0 {
		return
	}
	win := int64(probeWin)
	if l.ExtRTT != nil {
		if rtt := l.ExtRTT(); rtt > 0 {
			win = min(int64(float64(rtt)*probeRTTs), int64(probeWinMax))
		}
	}
	l.pr = probe{stage: 1, at: now, t0: now, s: s, owner: s.owner, layer: s.layer, rank: rank, fs: fs, fown: fs.owner, out: fs.out,
		target: target, fill: fill, win: win}
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
		if p.sus > 0 && el < int64(probeWinMax+probeSusOver) {
			break // offener Hinweis: Das Fenster laeuft weiter, bis er ausgeraeumt ist oder stehen bleibt (probeJudge)
		}
		if p.n >= probeMinTicks && el >= p.win || el >= int64(probeWinMax) {
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
//     wurde mindestens probeSentMin der Zielrate wirklich gesendet, und der Empfaenger hat davon im selben Zeitraum
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
	proof := r.Ns >= int64(probeProofMin) && float64(r.SentBps) >= probeSentMin*p.target &&
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
		if l.unkAt != 0 && now-l.unkAt <= int64(probeUnkGap) {
			l.unkN++
		} else {
			l.unkN = 1
		}
		if l.unkN >= probeUnkMax {
			l.probeEnd(probeFail, why, now)
			return
		}
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
		l.unkAt, l.unkN = 0, 0
	}
	l.pstat.Clamped += uint64(p.clamps)
	switch res {
	case probeOK:
		l.pstat.Passed++
		e.shieldOff(now, 0)
		e.ready = e.prReady
		e.grant, e.grantUntil = p.target, now+int64(probeGrant)
		if e.bps < p.target {
			e.bps = min(p.target, e.max)
		}
		l.pf.pass(p.target)
		if d != nil {
			d.next, d.ok = now+int64(probeGap), now
		}
	case probeFail:
		l.pstat.Failed++
		e.shieldOff(now, probeFloor)
		l.pf.fail(p.target, now)
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
			l.pf.fail(p.target, now)
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

// fail: Probe (bzw. echter Versuch) mit Zielrate t gescheitert - Abstand verdoppeln; eine kleinere gescheiterte
// Zielrate senkt die Schwelle, ohne den Abstand zurueckzusetzen.
func (f *legFail) fail(t float64, now int64) {
	switch {
	case f.pd == 0:
		f.rate, f.pd = t, probeGap
	case t >= f.rate*probeNear:
		f.pd = min(2*f.pd, probeGapMax)
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
// die naechste Probe fruehestens nach dem Aufschub, hoechstens nach probeGapMax. Ohne Probing ohne Wirkung.
func (d *dwell) afterFail(now int64) {
	d.next, d.ok = max(d.next, now+int64(min(d.d, probeGapMax))), 0
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
	pf    legFail // nur im Eintrag des Beins (Besitzer und Platzart leer)
	first bool    // ... ebenso: der freie Versuch zum Anrufbeginn ist verbraucht (Leg.firstUsed)
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
	if (l.pf.pd > 0 || l.firstUsed) && len(r.mem) < memMax {
		r.mem[memKey{recv: l.Peer.ID}] = memEnt{pf: l.pf, first: l.firstUsed, until: now + int64(memKeep)}
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
	if l.pf.pd == 0 {
		k := memKey{recv: l.Peer.ID}
		if e, ok := r.mem[k]; ok {
			delete(r.mem, k)
			if now < e.until {
				l.pf = e.pf
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
