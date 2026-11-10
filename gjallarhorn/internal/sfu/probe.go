package sfu

import (
	"time"

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
// Ein Stausignal in 1 bis 3 bricht sofort ab (gescheitert): GCC-Ziel gefallen (gccFall unter dem Hoechstwert seit
// Beginn), GCC meldet Ueberlast (Leg.ExtOver) oder Alter/Verlust in der eigenen Warteschlange. Die Schaetzung bleibt
// dabei und probeFloor danach unveraendert (estimator.shield) - ein Stau, den die Fuelldaten selbst erzeugt haben, sagt
// nichts ueber die laufende Auswahl.
//
// Bestanden ist die Probe ohne Stausignal bis zum Ende des Nachlaufs. Dass das GCC-Ziel die Zielrate erreicht, wird
// nicht verlangt: pion-GCC hebt sein Ziel auch bei freier Leitung nur um rund 8 %/s (gemessen), in einem Fenster von
// gut einer Sekunde also um hoechstens ein Zehntel. Den Beleg liefert die Verzoegerungsmessung selbst - eine Leitung,
// die die Zielrate nicht traegt, staut binnen weniger Rueckmeldungen und GCC meldet Ueberlast.
//
// Abstand: nach einer gescheiterten Probe probeGap, verdoppelnd bis probeGapMax, je Platz und Schicht; eine bestandene
// Probe oder upHold Halten der Schicht setzt zurueck. Nach einer bestandenen Probe frueestens probeGap spaeter die
// naechste (falls der freigegebene Schritt aus anderem Grund ausbleibt).
//
// Verhaeltnis zum Aufschub (dwell): Der Aufschub entsteht unveraendert, wenn ein echter Wechsel scheitert. Mit Probing
// ist danach fuer einen neuen Versuch auf diese Schicht eine bestandene Probe noetig - der blosse Ablauf des Aufschubs
// genuegt nicht mehr (genau diese blinden Versuche kosten Schluesselbilder). Dafuer darf schon waehrend des Aufschubs
// geprobt werden, sobald der Proben-Abstand um ist, und eine bestandene Probe erlaubt den Versuch vorzeitig. Die Probe
// verlaengert den Aufschub nie. Nach einem echten Fehlversuch (bzw. einer Budget-Ruecknahme mit Vorgeschichte) wird
// fruehestens nach dem Aufschub geprobt, hoechstens aber nach probeGapMax (dwell.afterFail): Besteht die Probe
// wiederholt, waehrend der Wechsel scheitert, kommen die Versuche so nie haeufiger als ohne Probing (8, 16, 32 ... s) -
// eine bestandene Probe setzt nur den Abstand nach gescheiterten Proben zurueck, nicht diesen. "Vorzeitig" wirkt also
// bei langen Aufschueben (ueber 2 min bis 5 min): Eine frei gewordene Leitung ist nach spaetestens 2 min wieder oben.
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
	probeGrant    = time.Second     // so lange gilt eine bestandene Probe fuer den Schritt
	probeFloor    = lagFloor        // so lange nach einem Abbruch faellt die Schaetzung nicht unter den Stand davor
)

// Ergebnis einer Probe in der Layout-Meldung (Slot.Probe).
const (
	probeStart = "start"
	probeOK    = "ok"
	probeFail  = "fail"
	probeAbort = "abort"
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
}

// ProbeStats zaehlt die Proben eines Beins.
type ProbeStats struct {
	Started, Passed, Failed, Aborted uint64
	PadBytes                         uint64 // gesendete Fuell-Bytes
}

// ProbeStats liefert die Zaehler der Proben dieses Beins.
func (l *Leg) ProbeStats() ProbeStats {
	l.room.mu.Lock()
	defer l.room.mu.Unlock()
	ps := l.pstat
	ps.PadBytes = l.fl.Stats.PadBytes.Load()
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
		fill = probeFillMax
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
	l.pr = probe{stage: 1, at: now, s: s, owner: s.owner, layer: s.layer, rank: rank, fs: fs, fown: fs.owner, out: fs.out,
		target: target, fill: fill, win: win}
	l.est.shieldOn(now)
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
	case e.prSig != "": // Stausignal: gescheitert
		l.probeEnd(probeFail, e.prSig, now)
		return
	case p.s.owner != p.owner || p.s.layer != p.layer:
		l.probeEnd(probeAbort, "platz", now)
		return
	case p.fs.owner != p.fown || p.fs.layer == "" || p.fs.paused:
		l.probeEnd(probeAbort, "ausgang", now)
		return
	}
	p.n++
	switch p.stage {
	case 1:
		if p.n >= probeRamp {
			p.stage, p.n, p.at, p.pad0 = 2, 0, now, l.fl.Stats.PadBytes.Load()
			l.fl.SetPadding(p.out, int64(p.fill))
		}
	case 2:
		if el := now - p.at; p.n >= probeMinTicks && el >= p.win || el >= int64(probeWinMax) {
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
		}
	case 3:
		if p.n >= probeSettle {
			l.probeEnd(probeOK, "", now)
		}
	}
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
	switch res {
	case probeOK:
		l.pstat.Passed++
		e.shieldOff(now, 0)
		e.ready = e.prReady
		e.grant, e.grantUntil = p.target, now+int64(probeGrant)
		if e.bps < p.target {
			e.bps = min(p.target, e.max)
		}
		if d != nil {
			d.pd, d.next, d.ok = 0, now+int64(probeGap), now
		}
	case probeFail:
		l.pstat.Failed++
		e.shieldOff(now, probeFloor)
		if d != nil {
			d.failProbe(now)
		}
	default:
		l.pstat.Aborted++
		e.shieldOff(now, 0)
		if d != nil && d.next < now+int64(probeRetry) {
			d.next = now + int64(probeRetry)
		}
	}
	p.s.setProbe(res, why, p.target)
	*p = probe{}
}

// failProbe: gescheiterte Probe - Abstand bis zur naechsten verdoppeln.
func (d *dwell) failProbe(now int64) {
	d.pd = min(max(2*d.pd, probeGap), probeGapMax)
	d.next, d.ok = max(d.next, now+int64(d.pd)), 0
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
// (prSig), statt die Schaetzung zu aendern.
func (e *estimator) shieldOn(now int64) {
	e.prOn, e.prSig, e.prPeak, e.prUntil, e.prTail = true, "", e.gccLast, 0, false
}

func (e *estimator) shieldOff(now int64, floor time.Duration) {
	e.prOn, e.prSig = false, ""
	e.prUntil = 0
	if floor > 0 {
		e.prUntil = now + int64(floor)
	}
}
