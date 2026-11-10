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
// dabei und bis probeFloor danach unveraendert (estimator.update) - ein Stau, den die Fuelldaten selbst erzeugt haben,
// sagt nichts ueber die laufende Auswahl. Steht das Stausignal aber auch ohne Fuelldaten weiter an (probeDrain,
// probeBad), ist die Leitung wirklich eingebrochen: Der Schutz endet sofort.
//
// Bestanden ist die Probe ohne Stausignal bis zum Ende des Nachlaufs. Dass das GCC-Ziel die Zielrate erreicht, wird
// nicht verlangt: pion-GCC hebt sein Ziel auch bei freier Leitung nur um rund 8 %/s (gemessen), in einem Fenster von
// gut einer Sekunde also um hoechstens ein Zehntel. Den Beleg liefert die Verzoegerungsmessung selbst - eine Leitung,
// die die Zielrate nicht traegt, staut binnen weniger Rueckmeldungen und GCC meldet Ueberlast.
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
	probeQuiet    = layerDwell      // so lange muss die Bild-Auswahl des Beins unveraendert laufen, bevor geprobt wird
	// ... vor der ersten Probe eines Beins kuerzer: Es gibt noch keinen Wechsel, dessen Folgen (Schluesselbild,
	// nachlaufendes Ziel) abzuwarten waeren - nur der Anlauf (erstes Schluesselbild, erste Rueckmeldungen an GCC)
	probeQuietFirst = 2 * time.Second
	probeGrant      = time.Second // so lange gilt eine bestandene Probe fuer den Schritt
	probeSlack      = 1.1         // ... auch wenn sein Bedarf seit Beginn der Probe um so viel gewachsen ist
	probeFloor      = lagFloor    // so lange nach einem Abbruch faellt die Schaetzung nicht unter den Stand davor
	probeDrain      = 2           // ... es sei denn, nach so vielen Takten ohne Fuelldaten steht weiter ein Stausignal an,
	probeBad        = 2           // und zwar so viele Takte in Folge (echter Einbruch, estimator.update)
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
	Last                             string // Ausgang der letzten Probe: ok, fail:<grund> bzw. abort:<grund>
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
	// Belegung geaendert (Sprecherwechsel, Pause): Abbruch ohne Wertung - zuerst pruefen, denn mit dem Wechsel sinkt
	// die Rate und das GCC-Ziel kann ihr folgen, was sonst als Stausignal zaehlte
	case p.s.owner != p.owner || p.s.layer != p.layer:
		l.probeEnd(probeAbort, "platz", now)
		return
	case p.fs.owner != p.fown || p.fs.layer == "" || p.fs.paused:
		l.probeEnd(probeAbort, "ausgang", now)
		return
	case e.prSig != "": // Stausignal: gescheitert
		l.probeEnd(probeFail, e.prSig, now)
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
		l.pf.pass(p.target)
		if d != nil {
			d.next, d.ok = now+int64(probeGap), now
		}
	case probeFail:
		l.pstat.Failed++
		e.shieldOff(now, probeFloor)
		l.pf.fail(p.target, now)
		if d != nil {
			d.ok = 0
		}
	default:
		l.pstat.Aborted++
		e.shieldOff(now, 0)
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
	e.prUntil, e.prN, e.prBad = 0, 0, 0
	if floor > 0 {
		e.prUntil = now + int64(floor)
	}
}

// Aufschub und Proben-Abstand haengen am Platz eines Beins. Wechselt ein Geraet den Weg (udp -> tcp -> udp), entsteht
// ein neues Bein, und beides begaenne von vorn - auf einer Leitung, die h nicht traegt, kaeme nach jedem Wegwechsel
// sofort wieder ein Versuch. Deshalb merkt sich der Raum die Eintraege eines entfernten Beins je (Empfaenger, Besitzer
// der Quelle, Platzart) fuer memKeep und gibt sie dem naechsten Platz desselben Empfaengers fuer denselben Besitzer
// zurueck. Nur mit Probing (Schalter aus: Verhalten wie zuvor).
const (
	memKeep = upDwellMax // so lange nach dem Entfernen des Beins
	memMax  = 512        // Eintraege je Raum
)

type memKey struct{ recv, owner, class string }

type memEnt struct {
	dw    [4]dwell
	pf    legFail // nur im Eintrag des Beins (Besitzer und Platzart leer)
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
	if l.pf.pd > 0 && len(r.mem) < memMax {
		r.mem[memKey{recv: l.Peer.ID}] = memEnt{pf: l.pf, until: now + int64(memKeep)}
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
		}
	}
}
