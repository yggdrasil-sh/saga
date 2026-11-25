package sfu

import (
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/yggdrasil-sh/saga/gjallarhorn/internal/fwd"
)

// Steuerung im Takt (Standard 200 ms). Alles hier ist Steuerpfad: Pakete
// werden nie angefasst, nur Ausgaenge umgehaengt und Grenzen gesetzt.

// Top-3-Ton (2.6, Vertrag docs/APP-API.md "Top-3-Ton" 3/4, Bugs-DB 2417): Sprechen alle Inhaber der Ton-Plaetze,
// verdraengt ein Neuer den leisesten erst, wenn er mindestens audioHold lang mindestens audioFactor-mal so aktiv ist
// (dieselben Werte wie beim Hauptsprecher). Einen freien oder stillen Platz bekommt ein Neuer im naechsten Takt.
const (
	audioHold   = domHold
	audioFactor = domFactor
)

// Schwellen der Sprechererkennung.
const (
	speakOn       = 0.04                    // Aktivitaet, ab der jemand als sprechend gilt
	domHold       = 1500 * time.Millisecond // Mindestdauer des Hauptsprechers
	domFactor     = 1.4                     // so viel lauter muss ein neuer Hauptsprecher sein
	domKeep       = 5 * time.Second         // so lange behaelt ein Hauptsprecher seinen Status ueber ein Wiederverbinden
	upHeadroom    = 1.15                    // Reserve vor einem Wechsel nach oben
	keepSlack     = 1.1                     // ohne Stau bleibt die laufende Schicht bis 10 % ueber der Schaetzung
	layerDwell    = 4 * time.Second         // nach einem Wechsel nach unten so lange nicht wieder hoch (gleiche Quelle)
	upFail        = 2 * time.Second         // Budget-Ruecknahme so kurz nach dem Hochschalten: Aufschub verdoppeln
	upDwellMax    = 5 * time.Minute         // Obergrenze dieses Aufschubs
	upHold        = 30 * time.Second        // so lange haelt die gescheiterte Schicht (oder eine hoehere): Aufschub zurueck
	upDwellSoft   = 32 * time.Second        // Aufschub hoechstens so lang, nachdem ein Versuch laenger als upFail hielt
	wantSlack     = 1.05                    // Deckel der Schaetzung so weit ueber dem Bedarf des naechsten Schritts (mit Reserve)
	defaultRateL  = 150e3                   // angenommene Raten, bis gemessen wurde
	defaultRateM  = 500e3
	defaultRateH  = 1500e3
	audioRateUDP  = 40e3 // Ton je Strom auf einem UDP-Bein (BUNDLE)
	tileFPSMargin = 1.25
	// Mindestbild (UDP, Bug 2152): Bevor eine Kachel pausiert, laeuft sie auf der untersten Schicht mit nur so vielen
	// Bildern/s (Basis-Zeitschicht, bei L1T2 mit 20 fps die Haelfte, rund 60 % der Rate) - lieber weniger Bilder als
	// gar kein Bild. Eine pausierte Kachel kommt in dieser Form fruehestens minBack nach der Pause zurueck.
	minFPS  = 10
	minBack = 2 * time.Second
	// Daempfung (Bug 2159): Faellt die Auswahl des Beins binnen mbFailWin nach einer Rueckkehr der Mindestform (Pause ->
	// Mindestbild bzw. Mindestbild -> volle unterste Schicht), ist die Rueckkehr gescheitert - ihr Aufschub verdoppelt
	// sich (2 -> 4 -> ... bzw. 4 -> 8 -> ...) bis mbBackMax. Zurueck auf den Grundwert, wenn sie upHold gehalten hat.
	mbBackMax = 30 * time.Second
	mbFailWin = 1500 * time.Millisecond
	// Stau auf der Leitung (Bug 2159, Ton geht vor Bild): GCC meldet jamTicks Takte in Folge Ueberlast, oder die
	// Umlaufzeit liegt rttJamFor lang und ueber rttJamN Empfaengerberichte rttJam ueber ihrem Boden (kleinste gesehene,
	// steigt um rttDrift je Sekunde). Dann nimmt das Bein je jamStep eine Stufe zurueck (Kachel tiefer, ins Mindestbild,
	// in die Pause, zuletzt der Sprecher), solange die Umlaufzeit nicht faellt - faellt sie nach jamPathN Schritten
	// nicht (ohne Ueberlast und Verlust), ist es ein Netzwechsel, keine eigene Schlange. Zurueck kommt die Mindestform
	// erst nach jamCalm ohne Stau (mit Aufschub); Schritte nach der Schaetzung erst nach jamHold, davor nur mit
	// bestandener Probe.
	jamTicks    = 3
	rttJam      = 100 * time.Millisecond
	rttJamFor   = 600 * time.Millisecond
	rttJamN     = 2 // ... ueber so viele Empfaengerberichte (Pruefung 2159: eine einzelne Spitze ist kein Stau)
	rttDrift    = 500 * time.Microsecond
	jamStep     = time.Second
	jamCalm     = 4 * time.Second
	jamHold     = time.Minute
	jamPathN    = 2    // Netzwechsel: nach so vielen Schritten zurueck ohne Fall der Umlaufzeit (planLeg)
	jamPathLoss = 0.02 // ... und Verlust darunter
	// Ueberlast laut GCC zaehlt vor dem ersten Empfaengerbericht nur mit so viel Verlust (planLeg)
	jamLossStart = 0.05
	rttWaitMax   = 3 * time.Second // Anrufbeginn: so lange hoechstens auf den ersten Empfaengerbericht warten
)

// Stufen der Rueckkehr der Mindestform (Leg.mb, Index k)
const (
	mbBack = 0 // Pause -> Mindestbild (Grundwert minBack)
	mbFull = 1 // Mindestbild -> volle unterste Schicht (Grundwert layerDwell)
)

// mbDamp: Daempfung der Mindestform auf einem Bein, je Art der Plaetze (mbCls) und Stufe: Aufschub nach gescheiterter
// Rueckkehr (0 = Grundwert minBack bzw. layerDwell), letzte Rueckkehr und letzte gescheiterte Rueckkehr (der Aufschub
// zaehlt ab dann). Je Bein, nicht je Platz und Besitzer (Pruefung 2159): Gescheitert ist die Rate des Beins - mit dem
// Besitzer (Sprecherwechsel alle 10 s) fing die Daempfung sonst jedes Mal von vorn an (Modell 400k: 16,6 statt 3,8
// Wechsel je Kachel und Minute).
type mbDamp struct {
	dw     time.Duration
	upAt   int64
	failAt int64
}

// mbCls: Art des Platzes fuer die Daempfung - Kacheln teilen sich eine, Sprecher und Bildschirm die andere.
func mbCls(s *slot) int {
	if s.class == "tile" {
		return 0
	}
	return 1
}

func mbBase(k int) time.Duration {
	if k == mbFull {
		return layerDwell
	}
	return minBack
}

// mbReady: Darf Platz s auf Stufe k der Mindestform zurueck (Bug 2159)? since: seit wann er in der Form davor ist
// (Pause bzw. Mindestbild); der Aufschub (mindestens base) zaehlt ab dann bzw. ab der letzten gescheiterten Rueckkehr
// dieser Stufe auf dem Bein.
func (l *Leg) mbReady(s *slot, k int, since int64, base time.Duration, now int64) bool {
	m := &l.mb[mbCls(s)][k]
	since, base = max(since, m.failAt), max(base, m.dw)
	return now-since >= int64(base)
}

// mbReturn merkt die Rueckkehr der Mindestform (Stufe k) von Platz s als letzten solchen Schritt des Beins.
func (l *Leg) mbReturn(s *slot, k int, now int64) {
	l.mb[mbCls(s)][k].upAt = now
	l.mbStep.s, l.mbStep.stage, l.mbStep.at, l.mbStep.owner = s, k, now, s.owner
}

// mbDouble: Rueckkehr der Art c auf Stufe k gescheitert - Aufschub verdoppeln (bis mbBackMax), ab jetzt.
func (l *Leg) mbDouble(c, k int, now int64) {
	m := &l.mb[c][k]
	m.dw, m.failAt = min(2*max(m.dw, mbBase(k)), mbBackMax), now
}

// mbFail: Die Auswahl des Beins ist gefallen. War die letzte Rueckkehr der Mindestform juenger als mbFailWin und meldet
// GCC dabei Ueberlast (over), ist sie gescheitert: Ihr Aufschub verdoppelt sich - bei der Art des Platzes, der
// zurueckkam, auch wenn der Stau einen anderen trifft (faellt der Sprecher, weil eine Kachel zurueckkam, warten die
// Kacheln laenger, nicht der Sprecher). Der Aufschub einer Kachel gilt fuer alle Kacheln des Beins: Gescheitert ist die
// Rate des Beins, nicht die Quelle - sonst versuchte es die naechste Kachel gleich danach.
func (l *Leg) mbFail(now int64, over, jamOn bool) {
	st := &l.mbStep
	if s := st.s; over && s != nil && s.owner == st.owner && now-st.at < int64(mbFailWin) {
		l.mbDouble(mbCls(s), st.stage, now)
		if jamOn {
			l.jamAt = now // gescheiterte Rueckkehr mit Ueberlast: auch ein Stau (jamHold: keine Schritte nach der Schaetzung)
		}
	}
	st.s = nil
}

// mbJam: Stau hat Platz s auf Stufe k zurueckgenommen (Bug 2159). Kam ein Platz dieser Art binnen upHold auf diese
// Stufe zurueck, war die Rueckkehr zu viel fuer die Leitung: Aufschub verdoppeln wie bei mbFail.
func (l *Leg) mbJam(s *slot, k int, now int64) {
	c := mbCls(s)
	if m := l.mb[c][k]; m.upAt == 0 || now-m.upAt >= int64(upHold) {
		return
	}
	l.mbDouble(c, k, now)
}

// mbHeld: Hat die letzte Rueckkehr upHold ohne Scheitern gehalten, ist der Aufschub erledigt (zurueck auf den Grundwert).
func (l *Leg) mbHeld(now int64) {
	for c := range l.mb {
		for k := range l.mb[c] {
			m := &l.mb[c][k]
			if m.upAt != 0 && m.failAt < m.upAt && now-m.upAt >= int64(upHold) {
				*m = mbDamp{}
			}
		}
	}
}

// screenResume: Kommt eine Bildschirmquelle nach mindestens so langer Stille (zusaetzlich zur Sekunde bis „nicht
// aktiv“) wieder, zaehlt das als neuer Beginn der Freigabe (Apps und Web beenden das Teilen mit replaceTrack(null),
// die Quelle bleibt angemeldet). Variable fuer Tests.
var screenResume = 3 * time.Second

func (r *Room) run(tick time.Duration) {
	t := time.NewTicker(tick)
	defer t.Stop()
	for {
		select {
		case <-t.C:
			r.Tick()
		case <-r.stop:
			return
		}
	}
}

// Tick fuehrt einen Steuerschritt aus (sonst im Takt; fuer Tests oeffentlich).
func (r *Room) Tick() { r.tickAt(fwd.Now()) }

// tickAt: Steuerschritt zur Zeit now (Tests mit kuenstlicher Zeit).
func (r *Room) tickAt(now int64) {
	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	dt := 0.2
	if r.lastTick != 0 {
		dt = float64(now-r.lastTick) / 1e9
		if dt <= 0.001 {
			dt = 0.001
		}
	}
	r.lastTick = now
	cfg := r.cfg
	peers := r.sortedPeers()
	for _, p := range peers {
		r.measurePeer(p, dt, now)
	}
	r.pickDominant(peers, now)
	var events []LayoutEvent
	for _, l := range r.legs {
		r.planLeg(l, peers, dt, now, cfg)
		l.spur(now)
		if ev, ok := l.layoutChanged(); ok {
			events = append(events, ev)
		}
		for _, s := range l.slots { // Grund und Probe-Ereignis nur in der Meldung des Wechsels bzw. Ereignisses selbst
			s.why, s.pev, s.pwhy, s.pkbit = "", "", "", 0
		}
	}
	if cfg.KeyframeEvery > 0 {
		for _, p := range peers {
			for _, st := range p.srcs {
				if st.Kind == fwd.Video && st.Active(now) && now-st.LastKey.Load() > int64(cfg.KeyframeEvery) && now-st.lastKeyRq > int64(cfg.KeyframeEvery) {
					st.lastKeyRq = now
					st.RequestPLI()
				}
			}
		}
	}
	peersEv := cfg.OnPeers != nil && r.peersChangedLocked()
	pevs, levs := r.pevs, r.levs
	r.pevs, r.levs = nil, nil
	r.mu.Unlock()
	for _, ev := range levs {
		cfg.OnLegEvent(ev)
	}
	for _, ev := range pevs {
		cfg.OnProbe(ev)
	}
	if peersEv {
		cfg.OnPeers(r.ref)
	}
	if cfg.OnLayout != nil {
		for _, ev := range events {
			cfg.OnLayout(ev)
		}
	}
}

// measurePeer aktualisiert Raten, Bildraten und Sprechaktivitaet.
func (r *Room) measurePeer(p *Peer, dt float64, now int64) {
	for _, st := range p.srcs {
		b := st.Bytes.Load()
		rate := float64(b-st.lastBytes) * 8 / dt
		st.lastBytes = b
		// langsam geglaettet (ca. 2 s), damit Schluesselbilder nicht pendeln
		if st.bps == 0 {
			st.bps = rate
		} else {
			st.bps = 0.9*st.bps + 0.1*rate
		}
		if st.screen { // erneute Freigabe nach einer Pause: neuer Beginn (Bildschirmplatz, neueste Freigabe gewinnt)
			if st.live(now) {
				if st.offAt != 0 && now-st.offAt >= int64(screenResume) && now > p.scrAt {
					p.scrAt = now
				}
				st.offAt = 0
			} else if st.offAt == 0 {
				st.offAt = now
			}
		}
		if st.Kind == fwd.Video {
			for i := range st.lastTID {
				c := st.TIDFrames[i].Load()
				f := float64(c-st.lastTID[i]) / dt
				st.lastTID[i] = c
				st.fps[i] = 0.7*st.fps[i] + 0.3*f
			}
		}
	}
	stepFlaps(p.cam, now) // Hysterese gegen flatternde Schichten (flap.go)
	stepFlaps(p.scr, now)
	act := 0.0
	if a := p.audio; a != nil {
		vp := a.VoicePkts.Swap(0)
		ls := a.LevelSum.Swap(0)
		if vp > 0 {
			loud := float64(ls) / float64(vp) / 127 // 0..1, hoeher = lauter
			frac := float64(vp) / (dt * 25)         // Anteil Sprachpakete (ptime 40)
			if frac > 1 {
				frac = 1
			}
			act = loud * frac
		}
		if lv := a.LastVoice.Load(); lv > p.lastSpoke {
			p.lastSpoke = lv
		}
	}
	if p.spoke > p.lastSpoke { // P9: laut anderem Knoten
		p.lastSpoke = p.spoke
	}
	p.score = 0.8*p.score + 0.2*act
}

// pickDominant waehlt den Hauptsprecher mit Hysterese: Ein neuer wird es,
// wenn er domHold lang am lautesten war, sofort, wenn er deutlich lauter
// ist (domFactor) und der alte mindestens domHold hatte.
// Ist der Hauptsprecher gerade beim Wiederverbinden (Room.lost, hoechstens domKeep), gilt dieselbe Hysterese gegen ihn
// wie gegen einen stillen Hauptsprecher: Sonst wuerde jeder, dessen Aktivitaet noch ueber speakOn liegt (ein Einwurf
// kurz vorher), in der Luecke sofort Hauptsprecher.
func (r *Room) pickDominant(peers []*Peer, now int64) {
	k := &r.lost
	if k.id != "" && (r.dominant != nil || now-k.at > int64(domKeep)) {
		k.id = ""
	}
	if k.id != "" {
		k.score *= 0.8 // klingt ab wie bei einem stillen Teilnehmenden (measurePeer)
	}
	var best *Peer
	for _, p := range peers {
		if p.audio == nil {
			continue
		}
		if best == nil || p.score > best.score {
			best = p
		}
	}
	if best == nil || best.score < speakOn {
		r.cand = nil
		return
	}
	cur := r.dominant
	if cur == best {
		r.cand = nil
		return
	}
	if r.cand != best {
		r.cand, r.candSince = best, now
	}
	if cur != nil {
		held := now-r.domSince >= int64(domHold)
		long := now-r.candSince >= int64(domHold)
		loud := best.score >= cur.score*domFactor || cur.score < speakOn
		if !held || !(long || loud) {
			return
		}
		r.prevDom = cur
	} else if k.id != "" {
		held := now-k.since >= int64(domHold)
		long := now-r.candSince >= int64(domHold)
		loud := best.score >= k.score*domFactor || k.score < speakOn
		if !held || !(long || loud) {
			return
		}
		k.id = ""
	}
	r.dominant = best
	r.domSince = now
	r.cand = nil
}

// spkFallback: Besitzer des Sprecherplatzes, wenn weder der Hauptsprecher noch (fuer ihn selbst) der vorige in Frage
// kommt - vor allem beim Hauptsprecher selbst, wenn vor ihm niemand Hauptsprecher war (wer zuerst spricht bzw. anruft).
// Bis 2.5 war das jeden Takt der zuletzt Sprechende mit Bild (byRecent): Senden mehrere zugleich Ton (Gespraech,
// Hintergrund), wechselte der Platz bei ihm alle paar Takte den Besitzer, mit ihm die Kacheln - jedes Mal neue
// Schluesselbilder, Beginn auf l (Bug 2153: 17-19 Schichtwechsel/min und bis 21 Einfrierer/min nur bei der
// Anrufenden). Jetzt bleibt beim Hauptsprecher selbst (self) der bisherige Besitzer, solange er dabei ist und Bild hat.
// Ohne Hauptsprecher (Luecke nach dem Auflegen bzw. Wiederverbinden) bleibt es beim zuletzt Sprechenden - dort soll
// wer jetzt spricht sofort auf den Platz (TestGruppeSprecherAuflegen). Ein anderer bekommt den Platz,
// wenn er domHold lang der lauteste der anderen ist und spricht, waehrend der bisherige schweigt bzw. er domFactor-mal
// lauter ist - die Regel des Hauptsprechers, auf die anderen beschraenkt.
func (l *Leg) spkFallback(cur *Peer, others []*Peer, self bool, now int64) *Peer {
	ok := false
	var best *Peer
	for _, p := range others {
		if len(p.cam) == 0 {
			continue
		}
		if p == cur {
			ok = true
		}
		if p.audio != nil && (best == nil || p.score > best.score) {
			best = p
		}
	}
	if !ok || !self {
		l.spkCand = nil
		rec := append([]*Peer(nil), others...)
		byRecent(rec)
		for _, p := range rec {
			if len(p.cam) > 0 {
				return p
			}
		}
		return nil
	}
	if best == nil || best == cur || best.score < speakOn || !(cur.score < speakOn || best.score >= cur.score*domFactor) {
		l.spkCand = nil
		return cur
	}
	if l.spkCand != best {
		l.spkCand, l.spkCandAt = best, now
	}
	if now-l.spkCandAt < int64(domHold) {
		return cur
	}
	l.spkCand = nil
	return best
}

// byActivity sortiert nach Sprechaktivitaet, dann letzter Sprache, dann
// Beitritt.
func byActivity(ps []*Peer) {
	sort.SliceStable(ps, func(i, j int) bool {
		a, b := ps[i], ps[j]
		as, bs := a.score >= speakOn, b.score >= speakOn
		if as != bs {
			return as
		}
		if as && a.score != b.score {
			return a.score > b.score
		}
		if a.lastSpoke != b.lastSpoke {
			return a.lastSpoke > b.lastSpoke
		}
		return a.joined < b.joined
	})
}

// byRecent sortiert nach letzter Sprache, dann Beitritt (Kachelfolge).
func byRecent(ps []*Peer) {
	sort.SliceStable(ps, func(i, j int) bool {
		a, b := ps[i], ps[j]
		if a.lastSpoke != b.lastSpoke {
			return a.lastSpoke > b.lastSpoke
		}
		return a.joined < b.joined
	})
}

// assignSticky verteilt die gewuenschten Teilnehmenden auf die Plaetze;
// wer schon einen Platz hat, behaelt ihn. Gewuenscht sind nur die ersten
// len(slots) (keepActive haengt die Wartenden hinten an): sonst behielte
// ein Platzhalter, der nur noch wartet, seinen Platz, und eine neue laute
// Person kaeme bei vollen Plaetzen nie dran (P3-Browser-Test, 5 Personen).
func assignSticky(slots []*slot, want []*Peer) {
	if len(want) > len(slots) {
		want = want[:len(slots)]
	}
	keep := map[*Peer]bool{}
	for _, p := range want {
		keep[p] = true
	}
	placed := map[*Peer]bool{}
	for _, s := range slots {
		if s.owner != nil && keep[s.owner] && !placed[s.owner] {
			placed[s.owner] = true
		} else {
			s.owner = nil
		}
	}
	i := 0
	for _, s := range slots {
		if s.owner != nil {
			continue
		}
		for i < len(want) && placed[want[i]] {
			i++
		}
		if i < len(want) {
			s.owner = want[i]
			placed[want[i]] = true
			i++
		}
	}
}

// layerStep: darf ein Ausgang eine Schicht hoeher gehen (bzw. die laufende behalten)? Hysterese gegen Pendeln (jeder
// Wechsel wartet auf ein Schluesselbild, die PLI dafuer erhoeht die Rate fuer alle Empfaenger):
//   - laufende Schicht: bleibt, solange need <= budget; ohne Stau (limit > 0, keine Sperrzeit) bis keepSlack darueber -
//     kurze Ratenspitzen (Schluesselbilder) schalten nicht herunter, echter Stau (Sperrzeit) sofort
//   - hoehere Schicht: nur mit Reserve (upHeadroom) und fruehestens layerDwell nach dem letzten Wechsel nach unten
func layerStep(cur bool, need, budget, limit float64, sinceDown int64) bool {
	if cur {
		return need <= budget || limit > 0 && need <= budget*keepSlack
	}
	if sinceDown < int64(layerDwell) {
		return false
	}
	return need*upHeadroom <= limit
}

// layerRank: Reihenfolge der Schichten (fuer "nach unten")
func layerRank(l string) int {
	switch l {
	case "l":
		return 1
	case "m":
		return 2
	case "h":
		return 3
	}
	return 0
}

// swapped (Pruefer B2 F5): Der Platz geht auf st, eine tiefere Schicht als bisher - ist das ein Quellenwechsel statt
// eines Wechsels nach unten? Dann gilt er wie ein neuer Beginn des Platzes auf st (wie nach einer Pause): kein downAt
// (layerDwell sperrte sonst den Weg zurueck, obwohl nichts zurueckgefallen ist), kein Aufschub, kein Rueckfall fuer
// Probing und Bremse. Die Vorgeschichte des Platzes (Aufschub je Schicht, fruehere Wechsel nach unten) bleibt.
//   - st ist nicht live: Vom Teilnehmenden kommt gerade gar nichts (layers faellt auf eine Quelle ohne Pakete zurueck,
//     z. B. Netzwechsel des Senders). Gezeigt wird nichts - zurueck nach unten ist das nicht (flap.go: fallen alle
//     Schichten zugleich aus, sind sie bei der Rueckkehr sofort nutzbar).
//   - Die bisherige Quelle ist weg und st kam nach ihr: neues Bein desselben Teilnehmenden (gleiche ID; ersetzt mit
//     derselben leg_ref - dann auch ein neues Peer-Objekt -, Uebergabe udp/tcp; nicht "Hierher uebernehmen": anderes
//     Geraet, andere ID). Die Schichten kommen nacheinander (l zuerst, m wartet auf sein Schluesselbild); dass der
//     Platz zuerst l nimmt, ist kein Rueckfall. Weg heisst: abgemeldet, anderes Peer-Objekt oder nicht mehr live. Eine
//     Schicht, die neben der bisherigen schon lief (Flattern: m faellt aus, l laeuft weiter; Budget), ist nicht neuer -
//     dafuer bleibt alles wie bisher.
func (s *slot) swapped(st *source, now int64) bool {
	if !st.live(now) {
		return true
	}
	was := s.src
	if was == nil || s.owner == nil || was.peer.ID != s.owner.ID || st.added <= was.added {
		return false
	}
	// lief st schon, als der Platz die bisherige nahm, ist sie nicht neuer - auch wenn sie spaeter angemeldet wurde
	// (Reihenfolge der ersten Pakete bzw. Rahmen eines Beins ist nicht fest; Pruefer: Flattern bei Folge h,m,l)
	if st.fl.liveAt != 0 && st.fl.liveAt <= s.srcAt {
		return false
	}
	return was.peer != s.owner || s.owner.srcs[was.Source] != was || !was.live(now)
}

// fresh (gruppe-sprecher): Der Platz wechselt den Besitzer, und beim neuen ist die Schicht des bisherigen (noch) nicht
// nutzbar - seine hoechste nutzbare liegt darunter. Das ist ein Beginn des Platzes auf dem neuen Besitzer, kein Wechsel
// nach unten: Gewaehlt hat nicht das Budget, sondern was gerade da ist (frisch beigetreten bzw. wiederverbunden: l
// zuerst, m wartet auf sein Schluesselbild; "Hierher uebernehmen": anderes Geraet, andere ID). Sonst sperrte downAt den
// Weg hinauf layerDwell lang, sobald m live ist. Hat der neue Besitzer die Schicht (oder eine hoehere) nutzbar und das
// Budget waehlt tiefer, bleibt es ein Wechsel nach unten wie bisher; gleicher Besitzer: slot.swapped.
func (s *slot) fresh(opts []*source) bool {
	if s.owner == nil || len(opts) == 0 || s.src != nil && s.src.peer == s.owner {
		return false
	}
	return layerRank(opts[len(opts)-1].Layer) < layerRank(s.layer)
}

// layerRate liefert die gemessene (sonst angenommene) Rate einer Quelle.
func layerRate(st *source) float64 {
	if st.bps > 1000 {
		return st.bps
	}
	return nominalRate(st)
}

// nominalRate: angenommene Rate einer Schicht, solange keine belastbare Messung da ist.
func nominalRate(st *source) float64 {
	switch st.Layer {
	case "l":
		return defaultRateL
	case "m":
		return defaultRateM
	}
	return defaultRateH
}

// layers liefert die nutzbaren Schichten einer Quellen-Menge, unten zuerst. Eine Schicht, die nach einem Flattern noch
// in der Hysterese steckt (flap.go), zaehlt nicht; ist sonst nichts nutzbar, die tiefste live.
func layers(m map[string]*source, stream bool, now int64) []*source {
	order := [...]string{"l", "m", "h"}
	var out []*source
	for _, k := range order {
		if stream && k == "h" && len(out) >= 2 {
			break // TCP: hoechstens 2 Schichten (H4)
		}
		if s := m[k]; s != nil && s.usable(now) {
			out = append(out, s)
		}
	}
	if len(out) == 0 { // nichts nutzbar: die tiefste live (gesperrt), sonst irgendeine vorhandene, unten zuerst
		for _, k := range order {
			if s := m[k]; s != nil && s.live(now) {
				return []*source{s}
			}
		}
		for _, k := range order {
			if s := m[k]; s != nil {
				return []*source{s}
			}
		}
	}
	if stream && len(out) > 2 {
		out = out[:2]
	}
	return out
}

// tidFor waehlt die hoechste zeitliche Schicht mit hoechstens fps Bildern/s.
func tidFor(st *source, fps float64) (uint8, float64) {
	if !st.HasTID.Load() {
		return 3, 1
	}
	total := st.fps[0] + st.fps[1] + st.fps[2] + st.fps[3]
	if total <= 0 {
		return 3, 1
	}
	acc := 0.0
	t := uint8(0)
	for i := 0; i < 4; i++ {
		if acc+st.fps[i] > fps*tileFPSMargin && i > 0 {
			break
		}
		acc += st.fps[i]
		t = uint8(i)
	}
	if t >= 3 {
		return 3, 1
	}
	// Basisrahmen sind groesser: Anteil leicht aufrunden
	frac := acc / total * 1.2
	if frac > 1 {
		frac = 1
	}
	return t, frac
}

// planLeg legt Belegung, Schichten und Pausen fuer ein Bein fest.
func (r *Room) planLeg(l *Leg, peers []*Peer, dt float64, now int64, cfg Config) {
	if len(l.slots) == 0 {
		return
	}
	me := l.Peer
	others := make([]*Peer, 0, len(peers))
	for _, p := range peers {
		if p != me {
			others = append(others, p)
		}
	}
	// Ton: Top-N nach Aktivitaet
	var aslots, vslots []*slot
	var speaker, screen *slot
	var tiles []*slot
	for _, s := range l.slots {
		switch s.class {
		case "audio":
			aslots = append(aslots, s)
		case "speaker":
			speaker = s
			vslots = append(vslots, s)
		case "screen":
			screen = s
			vslots = append(vslots, s)
		default:
			tiles = append(tiles, s)
			vslots = append(vslots, s)
		}
	}
	if len(aslots) > 0 {
		cand := make([]*Peer, 0, len(others))
		for _, p := range others {
			if p.audio != nil {
				cand = append(cand, p)
			}
		}
		byActivity(cand)
		l.holdAudio(aslots, cand, now) // Top-3-Hysterese: sprechende Inhaber bleiben
		for _, s := range aslots {
			if s.owner != nil && s.owner.audio != nil {
				s.out.Assign(s.owner.audio.Source)
				s.layer, s.paused = "a", false
			} else {
				s.out.Assign(nil)
				s.layer, s.paused = "", true
			}
		}
	}
	if len(vslots) == 0 {
		return
	}
	// Sprecher: Hauptsprecher bzw. fuer ihn selbst der vorige
	var spk *Peer
	if speaker != nil {
		spk = r.dominant
		if spk == me {
			spk = r.prevDom
		}
		// Nie das eigene Geraet: prevDom kann es selbst sein - war es Hauptsprecher, dann ein anderer, und verschwindet
		// dieser (Wiederverbinden: letztes Bein zu), wird es ohne Wechsel wieder Hauptsprecher (pickDominant ohne cur),
		// prevDom bleibt (Pruefer B2 F5: im 1:1 stand die Gegenstelle selbst auf ihrem Sprecherplatz, das
		// wiederverbundene Geraet auf einer Kachel, bis es wieder Hauptsprecher war)
		if spk == me || spk != nil && len(spk.cam) == 0 {
			spk = nil
		}
		if spk == nil {
			// beim Hauptsprecher selbst stabil (spkFallback), sonst (niemand ist Hauptsprecher) der zuletzt Sprechende
			spk = l.spkFallback(speaker.owner, others, r.dominant == me, now)
		}
		speaker.owner = spk
	}
	if screen != nil {
		var best *Peer
		for _, p := range others {
			if len(p.scr) > 0 && (best == nil || p.scrAt > best.scrAt) {
				best = p
			}
		}
		screen.owner = best
	}
	if len(tiles) > 0 {
		cand := make([]*Peer, 0, len(others))
		for _, p := range others {
			if len(p.cam) > 0 && p != spk {
				cand = append(cand, p)
			}
		}
		byRecent(cand)
		if len(cand) > len(tiles) {
			cand = keepActive(tiles, cand)
		}
		assignSticky(tiles, cand)
	}
	if len(r.mem) > 0 {
		r.recall(l, vslots, now)
	}
	l.soloCheck(others) // Bremse nach frischem Rueckfall nur im 1:1 (probe.go)
	// Schaetzung und Verteilung
	prb := l.probing(cfg)
	l.est.probing = prb
	// Anlauf (probe.go, freier Versuch zum Anrufbeginn): Bis der freie Versuch des Beins verbraucht ist, plant es genau
	// wie ohne Probing - Deckel der Schaetzung, Schonfrist und Aufschub wie dort, keine Proben.
	l.est.first = prb && !l.firstUsed
	l.est.update(l, dt, now)
	if n := l.est.note; n.kind != "" && cfg.OnLegEvent != nil { // Beginn bzw. Ende einer Widerlegung (eigen)
		r.levs = append(r.levs, LegEvent{Leg: l, Kind: n.kind, Art: n.art, Why: n.why, Dur: n.dur,
			EstKbit: int(l.est.bps / 1000), GccKbit: int(l.est.gccLast / 1000), RateKbit: int(l.est.rateTick / 1000)})
	}
	l.probeStep(now) // laufende Probe: weiter, Abbruch oder Freigabe (hebt dann die Schaetzung)
	// Bremse nach frischem Rueckfall: kleineres von Budget und GCC-Ziel, seit dem offenen geprobten Schritt (Einbruch)
	low := l.est.bps
	if g := l.est.gccLast; g > 0 {
		low = min(low, g)
	}
	l.fb.watch(low)
	budget := l.est.bps
	quotaCap := 1e12
	if q := me.quota; q != nil { // P10: Kontingent des verbundenen Servers, gleich verteilt auf seine Teilnehmenden
		quotaCap = q.downShare()
		if budget > quotaCap {
			budget = quotaCap
		}
	}
	audioCut := 0.0
	if l.Role == RoleBoth {
		audioCut = audioRateUDP * float64(len(aslots))
		budget -= audioCut
	}
	stream := l.Path.Stream()
	type choice struct {
		s      *slot
		opts   []*source
		idx    int // gewaehlte Schicht, -1 = Pause
		tid    uint8
		rate   float64
		wasOff bool
		min    bool // Mindestbild: unterste Schicht, nur die Basis-Zeitschicht (minFPS)
	}
	var cs []*choice
	order := make([]*slot, 0, len(vslots))
	if screen != nil {
		order = append(order, screen)
	}
	if speaker != nil {
		order = append(order, speaker)
	}
	order = append(order, tiles...)
	for _, s := range order {
		c := &choice{s: s, idx: -1, tid: 3}
		if s.owner != nil {
			m := s.owner.cam
			if s.class == "screen" {
				m = s.owner.scr
			}
			c.opts = layers(m, stream, now)
		}
		if len(c.opts) > 0 {
			c.idx = 0
		}
		cs = append(cs, c)
	}
	rateOf := func(c *choice) float64 {
		if c.idx < 0 {
			return 0
		}
		st := c.opts[c.idx]
		c.tid = 3
		f := 1.0
		if c.min && c.idx == 0 && !stream {
			c.tid, f = tidFor(st, minFPS)
		} else if stream {
			fps := cfg.TCPSpeakerFPS
			if c.s.class == "tile" {
				fps = cfg.TCPTileFPS
			}
			if fps > 0 {
				c.tid, f = tidFor(st, fps)
			}
		}
		return layerRate(st) * f
	}
	// Mindestbild (Bug 2152, nur UDP mit GCC): mb. Ein Platz, der im letzten Takt in der Mindestform lief, beginnt in ihr.
	mb := l.Ext != nil && !stream
	canMin := func(c *choice) bool {
		if !mb || c.idx != 0 || len(c.opts) == 0 {
			return false
		}
		t, f := tidFor(c.opts[0], minFPS)
		return t < 3 && f < 0.9
	}
	canMinAt := func(c *choice) bool { // wie canMin, fuer eine pausierte Kachel
		i := c.idx
		c.idx = 0
		ok := canMin(c)
		c.idx = i
		return ok
	}
	sum := 0.0
	for _, c := range cs {
		if mb && c.s.min && c.s.layer != "" && !c.s.paused && len(c.opts) > 0 && c.s.layer == c.opts[0].Layer && canMin(c) {
			c.min = true
		}
		c.rate = rateOf(c)
		sum += c.rate
	}
	// Bremse nach frischem Rueckfall (freshBrake, probe.go): Solange die lange Probe aussteht, deckelt sie die Auswahl
	// unter der Zielrate des gefallenen Schritts (capNeed, Bildrate) - Schritte nach oben wie laufende Schichten. Ein
	// Deckel nur fuer den einzelnen Schritt genuegt nicht: Geplant wird in der Reihenfolge des Vorrangs, jeder Platz
	// beginnt auf der untersten Schicht. In der Gruppe ging nach einem Sprecherwechsel der neue Sprecher so von l auf h,
	// waehrend die Kacheln als "laufend" auf m blieben - die Summe lag in der Bremse, der Schritt des Sprechers nicht.
	// Nie unter die laufende Auswahl: Der Deckel haelt das Bein, wo es ist, und verhindert nur das Wachsen - sonst naehme
	// er nach einem kleinen Schritt (Kachel aus der Pause, unter 10 % der Rate) auch laufende Schichten zurueck.
	// run: laufende Auswahl vor diesem Takt (Bildrate) - auch fuer die Bremse: Zielrate vor einem geprobten Schritt,
	// und ob ein Rueckfall die Auswahl des Beins senkt (sonst Umverteilung, freshBrake.down)
	run := 0.0
	for _, c := range cs {
		if c.s.layer == "" || c.s.paused {
			continue
		}
		for _, o := range c.opts {
			if o.Layer == c.s.layer {
				run += layerRate(o)
			}
		}
	}
	capNeed := 0.0
	if prb {
		capNeed = l.fb.capNeed(audioCut)
	}
	if capNeed > 0 {
		capNeed = max(capNeed, run)
	}
	// Reserve: nach oben nur mit Abstand und ausserhalb der Sperrzeit
	limit := budget
	if !l.est.canUp(now) {
		limit = 0
	}
	// Solange das GCC-Ziel der zuletzt erhoehten Rate noch nachlaeuft (Schonfrist, estimator.lagging), ist der letzte
	// Schritt nach oben unbestaetigt: Laufendes bleibt (mit keepSlack), Neues kommt erst danach dazu.
	// Ebenso nichts Neues, solange sich das Ziel von einem Ruecksprung noch nicht erholt hat (estimator.ready): Erst
	// ein Ziel deutlich ueber der Rate macht einen Stau nach dem naechsten Schritt als Fall des Ziels erkennbar.
	upLimit := limit
	if l.est.lag || !l.est.ready {
		upLimit = 0
	}
	// Stau auf der Leitung (Bug 2159): siehe jamTicks. Im Container lief bei 400 kbit/s die Schlange voll (RTT ~400 ms,
	// Ton verdeckt 7,5 %), waehrend Schaetzung und GCC-Ziel den Sprecher auf l noch zuliessen - die Leitung traegt
	// mehr als die gerechnete Rate (Kopfdaten, drei Tonstroeme).
	// Nur, wo es eine Mindestform gibt (Quellen mit Zeitschichten): Ohne sie bliebe als Schritt nur die Pause, und
	// Beine ohne Zeitschichten (aeltere Modelle, Geraete ohne L1T2) planen wie bisher.
	jamOn := false
	for _, c := range cs {
		jamOn = jamOn || mb && len(c.opts) > 0 && canMinAt(c)
	}
	jam, rtt := false, time.Duration(0)
	if jamOn {
		if l.ExtRTT != nil {
			rtt = l.ExtRTT()
		}
		// Ueberlast erst, wenn die Umlaufzeit bekannt ist (erster Empfaengerbericht): pion-GCC meldet bis zur ersten
		// Laufzeit-Auswertung "overuse" (Nullwert der Statistik) - sonst staute jeder Anrufbeginn (Pruefung 2159).
		// Oder mit Verlust ab jamLossStart: Den Nullwert begleitet keiner, eine Leitung, die der Anrufbeginn (Schaetzung
		// vom Start) ueberfaehrt, schon (Container 400k: 67 % Verlust vor dem ersten Bericht, der Browser wich auf TCP aus)
		known := l.ExtRTT == nil || rtt > 0 || l.ExtStat != nil && l.ExtStat().Loss >= jamLossStart
		if l.ExtOver != nil && known && l.ExtOver() {
			l.overN++
		} else {
			l.overN = 0
		}
		if l.ExtRTT != nil {
			if rtt > 0 {
				if l.rttFloor == 0 || rtt < l.rttFloor {
					l.rttFloor = rtt
				} else {
					l.rttFloor += time.Duration(float64(rttDrift) * dt)
				}
			}
		}
		// Hohe Umlaufzeit erst ueber mehrere Empfaengerberichte (rttJamN, Pruefung 2159): Eine einzelne verspaetete
		// Antwort (+160 ms) nahm sonst eine Kachel fuer jamHold zurueck. ExtRTT ist die Umlaufzeit des letzten Berichts -
		// ein neuer Wert ist ein neuer Bericht.
		if rtt > 0 && rtt > l.rttFloor+rttJam {
			if l.rttHighAt == 0 {
				l.rttHighAt, l.rttHighN = now, 0
			}
			if rtt != l.rttPrev {
				l.rttHighN++
			}
		} else {
			l.rttHighAt, l.rttHighN = 0, 0
		}
		jam = l.overN >= jamTicks || l.rttHighN >= rttJamN && now-l.rttHighAt >= int64(rttJamFor)
		// Eine Probe staut mit Absicht (Fuelldaten) und wertet das selbst aus (probe.go): kein Stau fuer die Auswahl.
		// Steigt dabei aber die Umlaufzeit um rttJam ueber den Boden, hat die Probe die Schlange gefuellt, die der Stau
		// misst: gescheitert (sonst bestand sie auf GCC-Zeichen allein, und der Schritt danach staute, Pruefung 2159).
		if l.pr.stage != 0 && rtt > 0 && rtt > l.rttFloor+rttJam {
			l.probeEnd(probeFail, "rtt", now)
		}
		if l.pr.stage != 0 || l.est.prOn || l.est.prUntil != 0 && now < l.est.prUntil {
			jam = false
		}
		// Netzwechsel (Pruefung 2159): Hat das Bein jamPathN Stufen zurueckgenommen und liegt die Umlaufzeit noch immer
		// so hoch wie beim ersten Schritt - ohne Ueberlast laut GCC und ohne Verlust -, ist es nicht die eigene Schlange
		// (anderer Pfad, Grund-Umlaufzeit gestiegen): Boden neu, der Stau war keiner (Sperre und Aufschub wie davor).
		// Sonst nahm das Bein im Dauerstau alles zurueck, bis der Boden nachgezogen war (0,5 ms/s: Minuten).
		// Ohne Verlustangabe (ExtStat) nicht: Eine volle Schlange haelt die Umlaufzeit ebenso flach.
		// Verglichen wird auch mit der Spitze seit dem ersten Schritt (jamRTTMax): Der erste Schritt faellt oft noch in
		// den Anstieg der eigenen Schlange - baut sie sich danach ab, laeuft die Umlaufzeit wieder durch die beim ersten
		// Schritt, gefallen ist sie trotzdem (Pruefung 2159: sonst galt ein echter Stau als Netzwechsel).
		if l.jamN > 0 && rtt > l.jamRTTMax {
			l.jamRTTMax = rtt
		}
		if jam && l.jamN >= jamPathN && now-l.jamStepAt >= int64(jamStep) && rtt > 0 && rtt+20*time.Millisecond >= max(l.jamRTT0, l.jamRTTMax) && rtt <= l.jamRTT0+40*time.Millisecond &&
			l.overN == 0 && l.ExtStat != nil && l.ExtStat().Loss < jamPathLoss {
			l.rttFloor, l.rttHighAt, l.rttHighN = rtt, 0, 0
			l.jamAt, l.mb, l.jamN, l.jamRTT = l.jamPre, l.jamMB, 0, 0
			l.pathN++
			jam = false
		}
		if jam {
			if !l.jamLast {
				l.jamPre = l.jamAt // Stand vor diesem Stau (fuer den Netzwechsel: zurueck dorthin)
				// Staut es kurz nach einer bestandenen Probe, traegt die Leitung nicht, was die Probe zeigte: in dieser
				// Sperre keine Proben mehr
				if lj := l.lastJ; lj.at != 0 && !lj.failed && now-lj.at < int64(upHold) {
					l.jamBad = true
				}
			}
			l.jamAt = now
		}
	}
	l.jamLast = jam
	// Anrufbeginn: bis zum ersten Empfaengerbericht (hoechstens rttWaitMax) nichts nach oben. Schaetzung und GCC-Ziel
	// sind dann noch die Startwerte - eine enge Leitung bekam sofort alles (Container 400k: 755k vor dem ersten
	// Bericht, 67 % Verlust, der Browser wich nach 3 s auf TCP aus). Vor fccc6dd hielt das zufaellig der GCC-Nullwert
	// (Stau zu Beginn, jamHold).
	startHold := false
	if rtt > 0 {
		l.rttSeen = true
	} else if jamOn && l.ExtRTT != nil && !l.rttSeen {
		if l.rttWaitAt == 0 {
			l.rttWaitAt = now
		}
		startHold = now-l.rttWaitAt < int64(rttWaitMax)
	}
	calm := l.jamAt == 0 || now-l.jamAt >= int64(jamCalm)
	// jamHeld (jamHold): nichts Neues nach der Schaetzung, solange es eben gestaut hat: Sie waechst, waehrend der Stau die
	// Auswahl unten haelt, anwendungsbegrenzt weit ueber die Leitung (Modell: bis 1200k bei 300k). Zurueck kommt die
	// Mindestform, einzeln und mit Aufschub (mbReady) - und hoeher geht es nur mit bestandener Probe (Pruefung 2159:
	// sonst blieb nach einem kurzen Stau auf wieder freier Leitung alles jamHold lang unten, TestUDPProbing). Proben
	// erst nach jamCalm ohne Stau und mit der Umlaufzeit wieder nahe am Boden (die Schlange ist abgebaut); nach einem
	// Stau trotz bestandener Probe bzw. nach der zweiten gescheiterten Probe in der Sperre keine mehr (jamBad). Eine
	// bestandene Probe gibt nur ihren Schritt frei (upFree), nicht die Schaetzung.
	jamHeld := l.jamAt != 0 && now-l.jamAt < int64(jamHold)
	upFree, probeLim := upLimit, upLimit
	if jamHeld {
		upLimit = 0
		if lj := l.lastJ; lj.failed && lj.at > l.jamFailAt { // gescheiterte Probe in dieser Sperre
			l.jamFailAt = lj.at
			if l.jamFailN++; l.jamFailN >= 2 {
				l.jamBad = true
			}
		}
		if !calm || l.jamBad || rtt > 0 && rtt > l.rttFloor+rttJam/2 {
			probeLim = 0
		}
	} else {
		l.jamBad, l.jamFailN, l.jamFailAt = false, 0, now
	}
	if startHold {
		upLimit, upFree, probeLim = 0, 0, 0
	}
	// Auf UDP geht je Takt hoechstens ein Platz eine Schicht hoeher (derselbe Platz darf mehrere Schichten nehmen): Jeder
	// Schritt ist eine Probe der Leitung - gingen mehrere Plaetze im selben Takt, springt die Rate um die Summe, und ein
	// Stau danach liesse sich keinem Schritt zuordnen. Ob der Schritt traegt, zeigt erst der naechste Takt (Schonfrist
	// bzw. ready). Kacheln, die aus der Pause dazukommen (unterste Schicht), zaehlen nicht mit: Jemanden zu zeigen geht
	// vor. TCP bleibt wie bisher.
	// Mindestbild halten (keep) bzw. aus der Pause zurueckholen (keepUp): nicht nur nach der Schaetzung, sondern auch nach
	// dem GCC-Ziel. Die Schaetzung haengt nach einem Stau in der Sperrzeit (bis 30 s) an der Rate fest, waehrend das Ziel
	// laengst wieder darueber liegt - im Labor blieben Kacheln so 15-83 s ohne Bild (2152). Das Ziel zaehlt nur, wenn es
	// gerade nicht entschuldigt nachlaeuft (lag), keine Probe laeuft bzw. eben gescheitert ist (Fuelldaten im Ziel) und
	// die eigene Warteschlange des Knotens nicht eben Stau zeigte (dann zaehlt allein die Schaetzung).
	keep, keepUp := budget, (budget+audioCut)/upHeadroom-audioCut
	ownQ := l.est.downAt != 0 && now-l.est.downAt < int64(lagFloor) && l.est.downWhy == "schlange"
	gccKeep := mb && l.est.gccLast > 0 && !l.est.lag && !ownQ && !l.est.prOn && (l.est.prUntil == 0 || now >= l.est.prUntil)
	if gccKeep {
		g := min(l.est.gccLast, quotaCap)/upHeadroom - audioCut
		keep, keepUp = max(keep, g), max(keepUp, g)
	}
	oneUp := l.Ext != nil
	var stepper *choice
	// wish: kleinster Bedarf eines Schritts nach oben, der in diesem Takt nicht ging (fuer den Deckel der Schaetzung)
	wish := 0.0
	deny := func(need float64) {
		if wish == 0 || need < wish {
			wish = need
		}
	}
	// Probing: der erste Schritt nach oben (in der Reihenfolge des Vorrangs), den nur die Schaetzung bzw. die
	// Vorgeschichte verhindert - fuer ihn beginnt am Ende des Takts eine Probe (probe.go)
	prb = prb && l.est.gccLast > 0
	// book: Buchfuehrung zu Schritten und Rueckfaellen (freier Versuch, Abstand je Zielrate) - auch im Anlauf.
	// start: Anlauf, siehe oben - die Planung selbst laeuft dann ohne Probing (prb aus).
	book, start := prb, prb && l.est.first
	prb = prb && !start
	var cand struct {
		s    *slot
		rank int
		need float64
	}
	big, wishBig := false, 0.0 // dieser Schritt ist nicht probbar; kleinster Bedarf eines abgelehnten solchen Schritts
	// wishFree: kleinster Bedarf eines abgelehnten Schritts, der ohne Probe versucht werden darf (freier Versuch,
	// probe.go) - der Deckel der Schaetzung folgt ihm wie ohne Probing, und solange beginnt keine Probe
	wishFree := 0.0
	// Kacheln, die gerade nichts zeigen, starten pausiert und kommen nur mit
	// Reserve dazu (gegen Pendeln bei schwankenden Raten)
	for _, c := range cs {
		if c.s.class == "tile" && c.idx >= 0 && c.s.layer == "" {
			sum -= c.rate
			c.idx, c.rate = -1, 0
			c.wasOff = true
		}
	}
	prev := sum // Bildrate der Auswahl vor diesem Takt (mit Mindestbild) - faellt sie, scheitert eine Rueckkehr (mbFail)
	// zu wenig (UDP): erst laufende Kacheln von hinten ins Mindestbild, dann - passt auch das nicht ins GCC-Ziel - der
	// Sprecher. Faellt die Rate kurz nach einer Rueckkehr der Mindestform (mbStep, binnen mbFailWin), nimmt zuerst der
	// Platz zurueck, der zurueckkam (Bug 2159): Er hat den Stau ausgeloest - eine Kachel, die aus der Pause kam, geht
	// wieder in die Pause, eine, die eben voll wurde, zurueck ins Mindestbild.
	toMin := func(c *choice) {
		if c.min || !canMin(c) {
			return
		}
		sum -= c.rate
		c.min = true
		c.rate = rateOf(c)
		sum += c.rate
	}
	// cause: Die Rate faellt kurz nach einer Rueckkehr - echter Stau, nicht ein grundloser Sturz des GCC-Ziels. Dann
	// pausieren die Kacheln, bevor der Sprecher ins Mindestbild geht (der Sprecher geht vor; sonst lief er auf einer
	// Leitung, die ihn und das Mindestbild einer Kachel nicht traegt, dauerhaft mit halber Bildrate).
	// Nur mit Ueberlast laut GCC (ExtOver: Verzoegerung steigt bzw. Ziel im Abstieg): Ein grundloser Sturz des Ziels
	// (Container, Fund B) kurz nach einer Rueckkehr ist kein Scheitern - sonst wartete der Platz ohne Grund im
	// Mindestbild, und der naechste Sturz traefe den Sprecher.
	// Der Sprecher geht nur ins Mindestbild, solange dafuer noch eine Kachel Bild zeigt (Bug 2159): Laeuft keine mehr,
	// gewaenne sein Mindestbild nichts - er bleibt auf der vollen untersten Schicht wie vor 2152 (Labor/Container 400k:
	// sonst lief er bei jedem Sturz des Ziels unter seine Rate wieder Sekunden lang mit halber Bildrate)
	tileRun := func() bool {
		for _, c := range cs {
			if c.s.class == "tile" && c.idx >= 0 {
				return true
			}
		}
		return false
	}
	over := mb && l.mbStep.s != nil && now-l.mbStep.at < int64(mbFailWin) && (l.ExtOver == nil || l.ExtOver())
	cause := false
	if st := l.mbStep; mb && over && st.s != nil && st.s.owner == st.owner && now-st.at < int64(mbFailWin) {
		cause = sum > keep
		for _, c := range cs {
			if c.s != st.s || c.idx != 0 {
				continue
			}
			if sum > budget && (st.stage == mbFull || c.s.class == "tile") {
				toMin(c)
			}
			if st.stage == mbBack && c.s.class == "tile" && sum > keep {
				sum -= c.rate
				c.idx, c.rate, c.min = -1, 0, false
			}
		}
	}
	if mb {
		for i := len(cs) - 1; i >= 0 && sum > budget; i-- {
			if cs[i].s.class == "tile" {
				toMin(cs[i])
			}
		}
		for _, c := range cs {
			if c.s.class == "speaker" && sum > keep && !cause && tileRun() {
				toMin(c)
			}
		}
	}
	// zu wenig: laufende Kacheln von hinten pausieren (Bild mit Sprechring) - auf UDP erst, wenn auch das Mindestbild
	// nicht ins GCC-Ziel passt
	pauseAt := budget
	if mb {
		pauseAt = keep
	}
	for i := len(cs) - 1; i >= 0 && sum > pauseAt; i-- {
		c := cs[i]
		if c.s.class != "tile" || c.idx < 0 {
			continue
		}
		sum -= c.rate
		c.idx, c.rate, c.min = -1, 0, false
	}
	if cause {
		for _, c := range cs {
			if c.s.class == "speaker" && sum > keep && tileRun() {
				toMin(c)
			}
		}
	}
	if mb && calm && l.overN == 0 && !tileRun() { // ... auch nicht, wenn die Kacheln erst danach pausierten (Anrufbeginn: Sprecher sonst 20 s halbiert)
		for _, c := range cs {
			if c.s.class == "speaker" && c.idx == 0 && c.min && !c.s.min { // eben erst, in diesem Takt
				sum -= c.rate
				c.min = false
				c.rate = rateOf(c)
				sum += c.rate
			}
		}
	}
	// Stau: je jamStep eine Stufe zurueck, von hinten, der Sprecher zuletzt - nur, solange die Umlaufzeit seit dem
	// letzten Schritt nicht faellt (sonst baut sich die Schlange schon ab). Ein Platz, der binnen upHold zurueckkam,
	// wartet fuer diese Stufe danach doppelt so lange (mbJam).
	// Nicht, wenn das Bein in diesem Takt bzw. binnen jamStep schon zurueckgenommen hat (der Stau baut sich dann erst
	// ab) oder die Umlaufzeit seit dem letzten Takt faellt.
	rttUp := rtt == 0 || l.rttPrev == 0 || rtt >= l.rttPrev
	if jam && sum >= prev && now-l.cutAt >= int64(jamStep) && now-l.jamStepAt >= int64(jamStep) && rttUp &&
		(l.jamRTT == 0 || rtt == 0 || rtt+20*time.Millisecond >= l.jamRTT) {
		if l.jamN == 0 { // erster Schritt dieses Staus: Stand fuer den Netzwechsel
			l.jamRTT0, l.jamRTTMax, l.jamMB = rtt, rtt, l.mb
		}
		l.jamN++
		done := false
		// Je Art erst die tieferen Schritte aller Plaetze, dann die naechste Stufe (Pruefung 2159: lieber alle Kacheln im
		// Mindestbild als eine voll und eine ohne Bild - sonst pausierte der Stau die Kachel im Mindestbild, waehrend die
		// eben voll gewordene weiterlief)
		for pass := 0; pass < 2 && !done; pass++ {
			for step := 0; step < 3 && !done; step++ {
				for i := len(cs) - 1; i >= 0 && !done; i-- {
					c := cs[i]
					if c.idx < 0 || (pass == 0) != (c.s.class == "tile") {
						continue
					}
					switch {
					case step == 0 && c.idx > 0:
						sum -= c.rate
						c.idx--
						c.rate = rateOf(c)
						sum += c.rate
						done = true
					case step == 1 && c.idx == 0 && !c.min && canMin(c):
						toMin(c)
						l.mbJam(c.s, mbFull, now)
						done = true
					case step == 2 && c.idx == 0 && c.s.class == "tile":
						sum -= c.rate
						c.idx, c.rate, c.min = -1, 0, false
						l.mbJam(c.s, mbBack, now)
						done = true
					}
				}
			}
		}
		l.jamStepAt, l.jamRTT = now, rtt
	} else if !jam {
		l.jamRTT, l.jamN = 0, 0
	}
	// Mindestbild zurueck: pausierte Kacheln (Budget) gehen dem Schritt des Sprechers nach oben vor - der Bedarf ihres
	// Mindestbilds ist vorgemerkt (resv), solange der Sprecher plant
	resv := 0.0
	minNr := func(c *choice) float64 {
		c.idx, c.min = 0, true
		nr := rateOf(c)
		c.idx, c.min = -1, false
		return nr
	}
	// Rueckkehr der Mindestform erst, wenn die letzte auf diesem Bein bestaetigt ist (mbFailWin ohne Fall): sonst
	// liesse sich ein Stau keinem Platz zuordnen (Bug 2159)
	mbCalm := calm && (l.mbStep.s == nil || now-l.mbStep.at >= int64(mbFailWin))
	backOK := func(c *choice) bool {
		return mbCalm && c.wasOff && len(c.opts) > 0 && c.s.owner != nil && canMinAt(c) && l.mbReady(c.s, mbBack, c.s.pauseAt, minBack, now)
	}
	if mb {
		for _, c := range cs {
			if c.s.class == "tile" && c.s.pauseAt != 0 && backOK(c) {
				if nr := minNr(c); sum+resv+nr <= keepUp {
					resv += nr
				}
			}
		}
	}
	held := 0.0 // vorgemerkte Reserve der Kacheln (resv), solange Sprecher bzw. Bildschirm planen
	up := func(c *choice, maxIdx int) {
		if c.min && c.idx == 0 && c.wasOff {
			return // eben erst aus der Pause ins Mindestbild: in diesem Takt nicht weiter (minAt gilt noch nicht)
		}
		if c.min && c.idx == 0 {
			// Mindestbild -> volle unterste Schicht: ein Schritt nach oben wie jeder andere (Reserve, Sperrzeit, Schonfrist,
			// auf UDP hoechstens einer je Takt). Die Reserve pausierter Kacheln (held) zaehlt dabei nicht: Der Sprecher
			// bekommt seine volle Form vor dem Mindestbild einer Kachel zurueck (Bug 2159). Nach einer gescheiterten
			// Rueckkehr wartet der Platz laenger (mbReady).
			old := c.rate
			c.min = false
			nr := rateOf(c)
			need := sum - held - old + nr
			lim := upLimit
			other := oneUp && stepper != nil && stepper != c
			if other {
				lim = 0
			}
			// ... oder, auch in der Sperrzeit, nach dem GCC-Ziel (keepUp), wenn das Mindestbild schon layerDwell lief: die
			// volle unterste Schicht gehoert zum Bild, das die Sperrzeit nicht zurueckhalten soll - hoehere Schichten schon
			byKeep := gccKeep && calm && !other && need <= keepUp && l.mbReady(c.s, mbFull, c.s.minAt, layerDwell, now)
			wait := !l.mbReady(c.s, mbFull, c.s.minAt, 0, now)
			if wait || !byKeep && (lim <= 0 || need*upHeadroom > lim) || capNeed > 0 && need > capNeed {
				c.min = true
				c.rate = rateOf(c)
				if !wait && calm { // im Aufschub bzw. nach Stau waechst der Deckel der Schaetzung nicht dafuer (Bug 2159)
					deny(need)
				}
				return
			}
			sum, c.rate = need+held, nr
			stepper = c
		}
		for c.idx >= 0 && c.idx+1 < len(c.opts) && c.idx+1 <= maxIdx {
			old := c.rate
			c.idx++
			nr := rateOf(c)
			// "laufend" ist auch jede Schicht unter der laufenden derselben Quelle: Sonst scheiterte bei Stau (limit 0)
			// schon der Zwischenschritt l -> m, und ein Platz auf h fiele trotz ausreichender Schaetzung bis auf l.
			cur := c.s.layer == c.opts[c.idx].Layer ||
				c.s.src != nil && c.s.src.peer == c.s.owner && layerRank(c.opts[c.idx].Layer) < layerRank(c.s.layer)
			need := sum - old + nr
			since := int64(layerDwell)
			if c.s.downAt != 0 && c.s.owner == c.s.downOwner {
				since = now - c.s.downAt
			}
			// nach gescheiterten Versuchen laenger warten - je Schicht: Ein Platz, der nach h-Versuchen spaeter von m
			// auf l faellt, darf nach layerDwell zurueck auf m
			rank := layerRank(c.opts[c.idx].Layer)
			var d dwell
			if c.s.owner == c.s.dwellOwner {
				d = c.s.dw[rank]
			}
			sinceDown, blocked := since, false
			// Ein Schritt, fuer den die Fuellrate ueber probeFillMax laege (z. B. Bildschirm von l auf h), laesst sich
			// nicht proben. Er wird nicht "halb geprobt" freigegeben, sondern laeuft wie ohne Probing: Der Deckel der
			// Schaetzung folgt seinem Bedarf (wantBig), der Wechsel selbst ist die Probe, der Aufschub das Netz.
			fits := !(max(need*probeHead, sum+probeFillMin)-sum > probeFillMax) // Fuellrate der Probe, siehe probeBegin
			prb := prb && fits
			big = !prb && !cur && !start && l.est.probing && l.est.gccLast > 0
			// passed: Fuer genau diesen Schritt ist gerade eine Probe bestanden (probe.go)
			passed := prb && !cur && d.ok != 0 && now-d.ok <= int64(probeGrant)
			tgt := need*upHeadroom + audioCut    // Zielrate des Beins fuer diesen Schritt
			brake := prb && !cur && l.fb.on(tgt) // Schritt in der Bremse nach frischem Rueckfall (freshBrake)
			// free: Dieser Schritt darf ohne Probe versucht werden - zeitlich wie ohne Probing (Aufschub, Deckel). Nicht,
			// wenn er ohnehin nicht gehen kann (Kontingent, Obergrenze der Schaetzung) oder sein Aufschub schon ueber
			// den ersten hinaus gewachsen ist: Ein freier Versuch, der lange aussteht, hielte alle Proben des Beins auf.
			// Nach einem frischen Rueckfall auf diese Zielrate (freshBrake) kein freier Versuch und kein Schritt ohne Probe.
			free := prb && !cur && !passed && tgt <= quotaCap && tgt <= l.est.max && d.d <= 2*layerDwell && l.freeStep(c.s, rank) && !brake && !jamHeld
			switch {
			case prb && !cur && !free && (d.d > layerDwell || l.pf.covers(tgt) || brake):
				// Probing: Nach einem gescheiterten Versuch bzw. einer gescheiterten Probe gibt nur eine bestandene
				// Probe den naechsten Versuch frei - dafuer auch vor Ablauf des Aufschubs
				if !passed {
					since, blocked = 0, true
				}
			case d.d <= layerDwell:
			case now-d.at < int64(d.d):
				since = 0
			}
			lim := limit
			if !cur {
				lim = upLimit
				if jamHeld && passed {
					lim = upFree // Sperre nach Stau: nur mit bestandener Probe
				}
				if oneUp && stepper != nil && stepper != c {
					lim = 0
				}
				// Waechst die Schaetzung gerade fuer einen nicht probbaren Schritt ueber den Deckel hinaus (wantBig),
				// gilt das nicht fuer die probbaren: Sie brauchen weiter ihre Probe
				// ... ebenso, wenn sie fuer einen freien Versuch waechst (wantFree): Der gilt nur fuer seinen Schritt
				if prb && !free && (l.est.wantBig > 0 || l.est.wantFree > 0) && l.est.capProbed > 0 {
					lim = min(lim, l.est.capProbed-audioCut)
				}
				// Die Probe hat die Rate des Schritts belegt, wie sie zu ihrem Beginn war. Schwankt der Bedarf seither
				// leicht (Schluesselbild in der geglaetteten Rate der Quelle), scheitert der Schritt nicht daran. Belegt
				// ist der Bedarf mal probeHead (Fuellziel); den Schritt gibt das mit seiner vollen Reserve frei.
				if t := need*upHeadroom + audioCut; passed && lim > 0 && t > budget+audioCut && need*probeHead+audioCut <= l.est.grant*probeSlack && t <= quotaCap {
					l.est.bps = max(l.est.bps, min(t, l.est.max))
					budget = max(budget, need*upHeadroom)
					lim, limit, upLimit = budget, budget, budget
				}
			}
			capped := capNeed > 0 && need > capNeed
			if layerStep(cur, need, budget, lim, since) && !capped {
				sum, c.rate = need, nr
				if !cur {
					stepper = c
					if prb && !passed && l.pf.pd > 0 && tgt >= l.pf.rate*probeNear {
						l.pf.n = 0 // Versuch ohne Probe (probeBlindAfter): der naechste erst nach weiteren gescheiterten Proben
					}
					// Freier Versuch verbraucht - aber nur, wenn der Schritt ihn brauchte (sonst waere er auch mit
					// Probing ohne Probe gegangen: Schaetzung hoch genug, keine Vorgeschichte)
					if free && (d.d > layerDwell || l.pf.covers(tgt) || l.est.capProbed > 0 && need*upHeadroom > l.est.capProbed-audioCut) {
						l.freeUsed(c.s, rank)
					}
					// Anlauf: ebenso - der Schritt ging wie ohne Probing, haette mit Probing aber eine Probe gebraucht
					// (nicht probbare Schritte ausgenommen: Sie laufen immer so)
					if start && fits && (d.d > layerDwell || l.est.capProbed > 0 && need*upHeadroom > l.est.capProbed-audioCut) {
						l.firstUsed = true
					}
				}
				continue
			}
			if !cur {
				deny(need)
				if big && (wishBig == 0 || need < wishBig) {
					wishBig = need
				}
				if free && (wishFree == 0 || need < wishFree) {
					wishFree = need
				}
				if prb && !free && cand.s == nil && stepper == nil && probeLim > 0 && sinceDown >= int64(layerDwell) && now >= d.next &&
					!l.pf.blocks(tgt, now) && !l.fb.blocks(tgt, now) && (blocked || capped || need*upHeadroom > upLimit) {
					cand.s, cand.rank, cand.need = c.s, rank, need
				}
			}
			c.idx--
			c.rate = rateOf(c)
			break
		}
	}
	// Vorrang: Bildschirm, Sprecher, dann weitere Kacheln, dann (UDP)
	// hoehere Kachelschichten
	sum += resv
	held = resv
	for _, c := range cs {
		if c.s.class == "screen" || c.s.class == "speaker" {
			up(c, 2)
		}
	}
	sum -= resv
	held = 0
	// Sprecher geht vor (Bug 2159): Laeuft er noch im Mindestbild (Aufschub, Sperrzeit), kommt keine Kachel dazu und
	// keine geht hoeher - sonst naehme sie ihm die Rate, die er fuer seine volle Form braucht
	spkMin := false
	for _, c := range cs {
		spkMin = spkMin || c.s.class == "speaker" && c.idx == 0 && c.min
	}
	mbOne := false
	for _, c := range cs {
		if !c.wasOff || len(c.opts) == 0 {
			continue
		}
		if spkMin {
			c.idx = -1
			continue
		}
		c.idx = 0
		nr := rateOf(c)
		// Probing: nach einer gescheiterten Probe fuer diese Kachel erst wieder mit einer bestandenen
		var d dwell
		if c.s.owner == c.s.dwellOwner {
			d = c.s.dw[layerRank(c.opts[0].Layer)]
		}
		tgt := (sum+nr)*upHeadroom + audioCut
		blocked := prb && (l.pf.covers(tgt) || l.fb.on(tgt)) && (d.ok == 0 || now-d.ok > int64(probeGrant))
		if prb && l.est.wantFree > 0 && l.est.capProbed > 0 && (sum+nr)*upHeadroom > l.est.capProbed-audioCut {
			blocked = true // die Schaetzung waechst gerade fuer den freien Versuch eines anderen Platzes, nicht fuer diese Kachel
		}
		// nach einem Stau (binnen upHold) zaehlt auch die Rueckkehr aus der Pause als Schritt (einer je Takt, Bug 2159)
		jamRecent := l.jamAt != 0 && now-l.jamAt < int64(upHold)
		tlim := upLimit
		if jamHeld && prb && d.ok != 0 && now-d.ok <= int64(probeGrant) {
			tlim = upFree // Sperre nach Stau: mit bestandener Probe
		}
		if (sum+nr)*upHeadroom <= tlim && !blocked && !(capNeed > 0 && sum+nr > capNeed) && !(jamRecent && stepper != nil) {
			sum, c.rate = sum+nr, nr
			if jamRecent {
				stepper = c
			}
		} else if mb && !mbOne && stepper == nil && backOK(c) && sum+minNr(c) <= keepUp {
			// Mindestbild statt Pause - auch in der Sperrzeit, solange das GCC-Ziel es traegt (keepUp); je Takt hoechstens
			// eine Kachel und nicht im Takt eines anderen Schritts nach oben (Bug 2159: ein Stau danach gehoert dann genau ihr)
			mbOne = true
			c.idx, c.min = 0, true
			c.rate = rateOf(c)
			sum += c.rate
		} else {
			deny(sum + nr)
			if prb && cand.s == nil && stepper == nil && probeLim > 0 && c.s.owner != nil && now >= d.next && !l.pf.blocks(tgt, now) &&
				!l.fb.blocks(tgt, now) {
				cand.s, cand.rank, cand.need = c.s, layerRank(c.opts[0].Layer), sum+nr
			}
			c.idx = -1
		}
	}
	// ... ebenso keine Kachel ueber das Mindestbild, solange eine andere (mit Besitzer) eben erst pausiert hat (binnen
	// upHold): lieber alle im Mindestbild als eine voll und eine ohne Bild. Nicht laenger - kommt die pausierte nicht
	// zurueck (traegt die Leitung nicht), haelt sie die anderen nicht auf.
	tileOff := false
	for _, c := range cs {
		tileOff = tileOff || mb && c.s.class == "tile" && c.idx < 0 && c.s.owner != nil && len(c.opts) > 0 &&
			c.s.pauseAt != 0 && now-c.s.pauseAt < int64(upHold)
	}
	if !stream { // UDP-Kacheln duerfen bis zur mittleren Schicht
		for _, c := range cs {
			if c.s.class == "tile" && !spkMin && !(tileOff && c.min) {
				up(c, 1)
			}
		}
	}
	// Bedarf fuer den Deckel der Schaetzung im naechsten Takt (estimator.want): der naechste Schritt nach oben, sonst
	// die laufende Auswahl - jeweils mit Reserve, zuzueglich des Tonabzugs, hoechstens das Kontingent.
	if wish < sum {
		wish = sum
	}
	l.est.want = wish*upHeadroom*wantSlack + audioCut
	if l.est.want > quotaCap {
		l.est.want = quotaCap
	}
	l.est.wantBig = 0
	if wishBig > 0 {
		l.est.wantBig = min(wishBig*upHeadroom*wantSlack+audioCut, quotaCap)
	}
	l.est.wantFree = 0
	if wishFree > 0 {
		l.est.wantFree = min(wishFree*upHeadroom*wantSlack+audioCut, quotaCap)
	}
	moved := false // aendert dieser Takt die Auswahl? Dann beginnt keine Probe (die Rate ist in Bewegung)
	for _, c := range cs {
		to := ""
		if c.idx >= 0 {
			to = c.opts[c.idx].Layer
		}
		moved = moved || to != c.s.layer || c.idx >= 0 && c.min != c.s.min
	}
	fell := false // faellt in diesem Takt ein Platz (Pause, tiefere Schicht, Mindestbild)? Dann ggf. mbFail
	for _, c := range cs {
		s := c.s
		if c.idx < 0 {
			if s.layer != "" && s.owner != nil && s.src != nil && s.src.peer == s.owner {
				fell = true
				s.setWhy(whyBudget, s.src, now) // laufendes Bild pausiert
				l.downEvent(s, "", budget+audioCut >= quotaCap, now, cfg)
				s.pauseAt = now // Mindestbild fruehestens minBack spaeter zurueck
			}
			s.out.Assign(nil)
			s.paused, s.layer, s.min = s.owner != nil, "", false
			continue
		}
		st := c.opts[c.idx]
		s.out.Assign(st.Source)
		s.out.SetMaxTID(c.tid)
		// Quellenwechsel statt Wechsel nach unten (slot.swapped, slot.fresh): keine Hysterese, kein Aufschub, kein Rueckfall
		swap := layerRank(st.Layer) < layerRank(s.layer) && (s.swapped(st, now) || s.fresh(c.opts))
		if prev := s.src; prev != nil && prev.peer == s.owner && (st != prev || s.layer == "") {
			switch {
			case s.layer == "": // Pause zu Ende
				s.setWhy(whyBudget, st, now)
			case swap, layerRank(st.Layer) < layerRank(prev.Layer) && !prev.usable(now):
				s.setWhy(whyGone, prev, now)
			case layerRank(st.Layer) > layerRank(prev.Layer) && st.fl.backAt != 0 && st.fl.backAt > s.srcAt:
				s.setWhy(whyBack, st, now)
			default:
				s.setWhy(whyBudget, st, now)
			}
		}
		heldFor, was := now-s.heldAt, s.src // so lange lief die bisherige Quelle dieses Platzes
		if st != s.src || s.layer == "" {
			s.heldAt = now
		}
		if st != s.src {
			s.src, s.srcAt = st, now
		}
		if layerRank(st.Layer) < layerRank(s.layer) && !swap { // nach unten: Mindestverweildauer fuer den Weg zurueck
			fell = true
			// Budget-Ruecknahme binnen upFail nach dem Hochschalten (Rate der Zielschicht unterschaetzt, z. B. nach
			// einer Aufloesungsaenderung): Aufschub fuer diese Schicht verdoppeln (bis upDwellMax). Jede Schicht hat
			// ihren eigenen Aufschub - ein gescheiterter m-Versuch erbt nicht die Vorgeschichte von h. Zurueckgestellt
			// wird er, sobald die Schicht upHold gehalten hat (unten, unabhaengig von einer Ruecknahme) bzw. bei
			// anderer Quelle. Hielt ein Versuch laenger als upFail und faellt dann wegen des Budgets, traegt die
			// Leitung die Schicht zumindest zeitweise: Aufschub hoechstens upDwellSoft - eine frei gewordene Leitung
			// bleibt nach einem einzelnen Rueckfall nicht minutenlang unten; verkuerzt wird er darunter nicht (sonst
			// begaenne bei abwechselnd fruehem und spaetem Scheitern jedes Mal alles von vorn).
			// - ebenso, wenn die Schonfrist nach diesem Hochschalten gescheitert ist (GCC-Ziel gefallen bzw. nicht
			// nachgezogen, estimator.probeFailed): auch das ist ein gescheiterter Versuch, kein Halten
			// - ebenso (nur Probing), wenn Schritte mit einer eben bestandenen Probe probeFreshN Mal in Folge binnen
			// probeFresh zurueckfallen: Die Leitung traegt die Probe, aber nicht die Schicht (Token-Bucket-Policer: der
			// Eimer traegt die Fuellung, die Dauerlast nicht) - gescheiterte Versuche, kein Halten. Der erste frische
			// Rueckfall fuehrt zur langen Probe (freshBrake, probe.go). Gezaehlt wird je Bein, nicht je Platz: In der
			// Gruppe nimmt der Rueckfall nach einem geprobten Schritt des Sprechers zuerst eine Kachel zurueck.
			own := s.why == whyBudget && s.upAt != 0 && s.upOwner == s.owner
			// Nur wenn die Auswahl des Beins sinkt: Faellt eine Kachel, weil der Sprecher im selben Takt steigt, ist das
			// Umverteilung (Pruefer bwe3b: mmm -> hml nach einem geprobten l -> m galt als frischer Rueckfall auf 960k)
			brake := false
			if book && s.why == whyBudget && was != nil && was.peer == s.owner {
				t, pre, lo, open := l.fb.open(now, low)
				art := freshSpread
				if sum < run {
					art, brake = l.fb.down(now, low)
				}
				if open {
					l.freshEvent(art, t, pre, lo, s, st.Layer, cfg)
				}
			}
			fast := own && (now-s.upAt < int64(upFail) || l.est.probeFailed(s.upAt, now) ||
				brake && s.upProbe && now-s.upAt < int64(probeFresh))
			// unramped (nur Probing): Die Schicht, von der der Platz faellt, sendet inzwischen deutlich mehr als bei
			// seinem Schritt hinauf - der Schritt ging mit einer zu kleinen Rate durch (Anrufbeginn: der Kodierer faehrt
			// erst hoch; im Labor h nach 0,2 s mit 299 kbit/s statt 1250). Sein Scheitern sagt nichts ueber die Leitung:
			// Es bleibt beim Aufschub wie ohne Probing, zaehlt aber nicht als gescheiterter Versuch des Beins.
			unramped := book && was != nil && was.peer == s.owner && s.rampOwner == s.owner && s.rampRate > 0 &&
				layerRate(was) > probeRampUp*s.rampRate
			if s.dwellOwner != s.owner {
				s.dw, s.dwellOwner = [4]dwell{}, s.owner
			}
			d := &s.dw[layerRank(s.layer)]
			switch {
			case fast:
				d.d, d.at = min(2*max(d.d, layerDwell), upDwellMax), now
				d.afterFail(now)
				if book && s.upT > 0 && !unramped {
					// Der Fehlversuch gilt fuer das Bein, nicht nur fuer diesen Platz und Besitzer: Wechselt der Sprecher,
					// braucht auch der naechste fuer eine Zielrate ab dieser eine bestandene Probe
					l.pf.fail(s.upT, now)
				}
			case s.why == whyBudget && d.d > 0:
				d.d, d.at = min(d.d, upDwellSoft), now
				d.afterFail(now)
			}
			s.downAt, s.downOwner = now, s.owner
			if was != nil && was.peer == s.owner { // Rueckfall derselben Quelle, kein Besitzerwechsel
				l.downEvent(s, st.Layer, budget+audioCut >= quotaCap, now, cfg)
			}
			if book && was != nil && was.peer == s.owner {
				l.freeGrant(s, layerRank(st.Layer), layerRank(s.layer), heldFor, unramped)
			}
		} else if s.layer != "" && layerRank(st.Layer) > layerRank(s.layer) {
			s.upAt, s.upOwner, s.upT = now, s.owner, sum*upHeadroom+audioCut
			// mit bestandener Probe (probe.go, probeEnd: dwell.ok dieses Platzes und dieser Schicht)?
			s.upProbe = false
			if s.dwellOwner == s.owner {
				d := s.dw[layerRank(st.Layer)]
				s.upProbe = d.ok != 0 && now-d.ok <= int64(probeGrant)
			}
			if book && l.solo && s.upProbe {
				l.fb.stepped(s.upT, run+audioCut, now)
			} else if book && l.solo {
				// Ein Rueckfall gilt jetzt diesem Schritt (freshBrake, beyond) - aber nur, wenn der getragene noch frisch
				// war: Ein Schritt, der schon probeFresh hielt, ist erledigt, ein spaeter Schritt ohne Probe weckt ihn
				// nicht wieder (Pruefer bwe3e; in den Modellen war er beim "getragen" hoechstens 13 s alt).
				age := now - l.fb.upAt
				if t, pre, lo, open := l.fb.open(now, low); open && l.fb.beyond(s.upT) {
					l.freshEvent(freshBeyond, t, pre, lo, s, st.Layer, cfg)
					if age < int64(probeFresh) {
						l.fb.stepped(s.upT, run+audioCut, now)
					}
				}
			}
			s.rampRate, s.rampOwner = layerRate(st), s.owner
		} else if swap || s.layer == "" || s.rampOwner != s.owner {
			// Platz beginnt auf dieser Schicht (Anrufbeginn, neuer Besitzer, neue Quellen nach dem Wiederverbinden)
			s.rampRate, s.rampOwner = layerRate(st), s.owner
		}
		if book && (layerRank(st.Layer) > layerRank(s.layer) || s.min && !c.min && st.Layer == s.layer) {
			l.est.stepAt = now // Schritt nach oben (auch aus der Pause, aus dem Mindestbild): Die Rate springt, siehe estimator.clamped
		}
		if c.min && !s.min && st.Layer == s.layer { // Mindestbild statt Pause: im Log wie eine Ruecknahme ("l>l*")
			fell = true
			l.downEvent(s, st.Layer+"*", budget+audioCut >= quotaCap, now, cfg)
		}
		if c.min && !s.min {
			s.minAt = now
		}
		// Rueckkehr der Mindestform (Bug 2159): aus der Pause ins Mindestbild, aus dem Mindestbild auf die volle Schicht
		switch {
		case s.layer == "" && s.paused: // auch gleich auf die volle Schicht: Rueckkehr aus der Pause
			l.mbReturn(s, mbBack, now)
		case !c.min && s.min && st.Layer == s.layer:
			l.mbReturn(s, mbFull, now)
		}
		s.paused, s.layer, s.tid, s.min = false, st.Layer, c.tid, c.min
		// gehalten: Die Schicht laeuft seit upHold - ihr Aufschub und der aller tieferen ist erledigt
		if s.dwellOwner != s.owner {
			s.dw = [4]dwell{}
		} else if now-s.heldAt >= int64(upHold) {
			for k := layerRank(st.Layer); k >= 0; k-- {
				s.dw[k] = dwell{}
			}
		}
	}
	for _, s := range vslots {
		if s.owner == nil {
			s.out.Assign(nil)
			s.paused, s.layer = false, ""
		}
	}
	l.mbHeld(now)
	if fell && sum < prev {
		l.mbFail(now, over, jamOn)
		l.cutAt = now
	}
	l.rttPrev = rtt
	// Probe fuer den abgelehnten Schritt: nur, wenn sich die Auswahl seit probeQuiet nicht bewegt hat (nach einem Wechsel
	// bzw. zu Beginn des Beins sind Rate und GCC-Ziel noch in Bewegung), keine andere laeuft, die Zielrate ins
	// Kontingent passt und ein laufender Bild-Ausgang zum Fuellen da ist (der des Platzes selbst, sonst
	// der erste laufende in der Reihenfolge des Vorrangs)
	if moved {
		l.movedAt = now
		if l.pr.stage != 0 {
			// Die Auswahl hat sich waehrend der Probe geaendert (auch an einem anderen Platz): Die Rate, auf die die
			// Fuellung berechnet war, stimmt nicht mehr - Abbruch ohne Wertung
			l.probeEnd(probeAbort, "platz", now)
		}
	}
	l.pf.decay(now)
	l.fb.decay(now)
	// Darf der naechste Schritt ohne Probe versucht werden, folgt der Deckel der Schaetzung wieder dem Bedarf (wie ohne
	// Probing): nach probeBlindAfter gescheiterten Proben in Folge
	l.est.blind = prb && probeBlindAfter > 0 && l.pf.pd > 0 && l.pf.n >= probeBlindAfter
	quiet := int64(probeQuiet)
	if l.pstat.Started == 0 {
		quiet = int64(probeQuietFirst)
	}
	if cand.s != nil && wishFree == 0 && now-l.movedAt >= quiet && now >= l.prNext && l.pr.stage == 0 &&
		cand.need*upHeadroom+audioCut <= quotaCap {
		fs := cand.s
		if fs.layer == "" || fs.paused {
			fs = nil
			for _, s := range order {
				if s.owner != nil && s.layer != "" && !s.paused {
					fs = s
					break
				}
			}
		}
		// Eine stille Quelle traegt keine Fuellpakete (sie folgen nur auf ein Bildende): dann nicht proben
		if fs != nil && (fs.src == nil || !fs.src.live(now)) {
			fs = nil
		}
		if fs != nil {
			l.probeBegin(cand.s, fs, cand.rank, cand.need, sum, audioCut, now)
		}
	}
}

// downEvent meldet die Ruecknahme einer Schicht (Platz s faellt von s.layer auf to, "" = Pause) mit ihrem Grund - fuers
// Log des Knotens (Config.OnLegEvent). Gruende:
//
//	quelle       die Schicht ist nicht mehr nutzbar (Quelle weg bzw. flattert)
//	schlange     die Schaetzung ist in diesem Takt gefallen: eigene Warteschlange (Alter, verworfene Rahmen)
//	gcc-<warum>  ... bzw. das GCC-Ziel liegt unter der Rate und ist nicht entschuldigt: fall (gefallen, als die Rate es
//	             ueberholte), faellt, steht, frist (Schonfrist gescheitert), detektor (eine Widerlegung endete, weil der
//	             Detektor nicht mehr widerlegt), sperre (Sperrzeit nach einem Stau), tot (Folge schon gescheitert),
//	             nach-probe (Ziel nach einer gescheiterten Probe weiter unter der Rate)
//	kontingent   das Kontingent des verbundenen Servers begrenzt
//	bedarf       die Schaetzung ist nicht gefallen, aber die Auswahl passt nicht mehr hinein (Rate der Quellen gestiegen)
func (l *Leg) downEvent(s *slot, to string, quota bool, now int64, cfg Config) {
	if cfg.OnLegEvent == nil {
		return
	}
	e := &l.est
	why := "bedarf"
	switch {
	case s.why == whyGone:
		why = "quelle"
	case e.downAt != 0 && now-e.downAt <= int64(400*time.Millisecond):
		why = e.downWhy
	case quota:
		why = "kontingent"
	}
	l.room.levs = append(l.room.levs, LegEvent{Leg: l, Kind: LegDown, Why: why, Class: s.class, From: s.layer, To: to,
		EstKbit: int(e.bps / 1000), GccKbit: int(e.gccLast / 1000), RateKbit: int(e.rateTick / 1000)})
}

// freshEvent: Einordnung eines Rueckfalls bzw. Schritts nach einem geprobten Schritt (freshBrake) als Logzeile "frisch"
// (Config.OnLegEvent) - damit das Labor sieht, welche Bedingung griff. to ist die neue Schicht des Platzes.
func (l *Leg) freshEvent(art string, t, pre, low float64, s *slot, to string, cfg Config) {
	if cfg.OnLegEvent == nil {
		return
	}
	e, b := &l.est, &l.fb
	brake := 0
	if b.at != 0 {
		brake = int(b.rate / 1000)
	}
	l.room.levs = append(l.room.levs, LegEvent{Leg: l, Kind: LegFresh, Why: art, Class: s.class, From: s.layer, To: to,
		EstKbit: int(e.bps / 1000), GccKbit: int(e.gccLast / 1000), RateKbit: int(e.rateTick / 1000),
		TgtKbit: int(t / 1000), PreKbit: int(pre / 1000), LowKbit: int(low / 1000), SlumpKbit: int(probeSlump * pre / 1000),
		BrakeKbit: brake, N: b.n})
}

// keepActive (Kacheln): Ein Inhaber, der nicht mehr zu den ersten len(slots) gehoert, behaelt seinen Platz nur, wenn er
// still ist und der schwaechste Neue in der ersten Auswahl auch - er verdraengt also nur stille Neue. Einen sprechenden
// Inhaber schuetzt das nicht (bis 2.5 galt das auch fuer die Ton-Plaetze, Bugs-DB 2417; dort jetzt holdAudio).
func keepActive(slots []*slot, cand []*Peer) []*Peer {
	n := len(slots)
	has := map[*Peer]bool{}
	for _, s := range slots {
		if s.owner != nil {
			has[s.owner] = true
		}
	}
	// ein Platzhalter wird nur verdraengt, wenn ein Neuer spricht und er nicht
	out := append(make([]*Peer, 0, len(cand)), cand[:n]...)
	var waiting []*Peer
	for _, p := range cand[n:] {
		if has[p] && p.score < speakOn {
			// ersetze den schwaechsten Neuen, der selbst nicht spricht
			for i := len(out) - 1; i >= 0; i-- {
				if !has[out[i]] && out[i].score < speakOn {
					waiting = append(waiting, out[i])
					out[i] = p
					break
				}
			}
			continue
		}
		waiting = append(waiting, p)
	}
	return append(out, waiting...)
}

// HoldAudio belegt Ton-Plaetze nach der Top-3-Hysterese (Vertrag docs/APP-API.md "Top-3-Ton" 3/4, Bugs-DB 2417) -
// fuer die Ton-Plaetze eines Beins (Leg.holdAudio) und die Ton-Auswahl einer Bifroest-Bruecke (gleiche Regel):
//   - Ein sprechender Inhaber (Aktivitaet >= speakOn) behaelt seinen Platz.
//   - Ein Neuer bekommt sofort einen freien Platz; ein sprechender Neuer auch den Platz eines stillen Inhabers (des
//     stillsten). Stille Inhaber weichen nur Sprechenden.
//   - Sprechen alle Inhaber, verdraengt ein Neuer den leisesten erst, wenn er mindestens audioHold lang mindestens
//     audioFactor-mal so aktiv ist wie der jeweils leiseste Inhaber.
//
// cur: bisherige Inhaber je Platz (Nullwert = frei; wer nicht mehr unter cand ist, gibt seinen Platz frei). cand: alle
// Anwaerter, sprechende zuerst und nach Aktivitaet absteigend (byActivity). wait: Neue, die einen sprechenden Inhaber
// verdraengen wollen, und seit wann sie lauter genug sind (Zustand des Empfaengers, wird hier gepflegt). Ergebnis: neue
// Inhaber je Platz, gleiche Reihenfolge wie cur (ein Inhaber behaelt seinen Platz).
func HoldAudio[K comparable](cur, cand []K, score func(K) float64, wait map[K]int64, now int64) []K {
	var zero K
	in := make(map[K]bool, len(cand))
	for _, c := range cand {
		in[c] = true
	}
	out := make([]K, len(cur))
	held := make(map[K]bool, len(cur))
	for i, k := range cur {
		if k != zero && in[k] && !held[k] {
			out[i], held[k] = k, true
		}
	}
	waiting := map[K]bool{}
	for _, c := range cand {
		if held[c] {
			continue
		}
		free := -1
		for i, k := range out {
			if k == zero {
				free = i
				break
			}
		}
		if free >= 0 {
			out[free], held[c] = c, true
			continue
		}
		sc := score(c)
		if sc < speakOn {
			continue // stille Neue verdraengen niemanden
		}
		q, qs := -1, 0.0 // leisester Inhaber
		for i, k := range out {
			if s := score(k); q < 0 || s < qs {
				q, qs = i, s
			}
		}
		if q < 0 {
			continue
		}
		if qs >= speakOn {
			if sc < qs*audioFactor {
				continue
			}
			waiting[c] = true
			since, ok := wait[c]
			if !ok {
				wait[c] = now
				continue
			}
			if now-since < int64(audioHold) {
				continue
			}
		}
		held[out[q]] = false
		out[q], held[c] = c, true
		delete(waiting, c)
	}
	for k := range wait {
		if !waiting[k] {
			delete(wait, k)
		}
	}
	return out
}

// holdAudio belegt die Ton-Plaetze des Beins (HoldAudio) und zaehlt Inhaberwechsel (aswitch).
func (l *Leg) holdAudio(aslots []*slot, cand []*Peer, now int64) {
	if l.aWait == nil {
		l.aWait = map[*Peer]int64{}
	}
	cur := make([]*Peer, len(aslots))
	for i, s := range aslots {
		cur[i] = s.owner
	}
	out := HoldAudio(cur, cand, func(p *Peer) float64 { return p.score }, l.aWait, now)
	for i, s := range aslots {
		p := out[i]
		s.owner = p
		if p == nil {
			continue
		}
		if s.aLast != "" && s.aLast != p.ID {
			l.aSwitch.Add(1)
		}
		s.aLast = p.ID
	}
}

// ASwitch: Inhaberwechsel auf den Ton-Plaetzen dieses Beins seit dem Anlegen (Zaehler "aswitch" in den stats des Beins;
// ein Platz, der zum ersten Mal belegt wird oder nach einer Luecke denselben Inhaber wieder bekommt, zaehlt nicht).
func (l *Leg) ASwitch() uint64 { return l.aSwitch.Load() }

// layoutChanged baut die Layout-Meldung, wenn sich etwas geaendert hat.
func (l *Leg) layoutChanged() (LayoutEvent, bool) {
	sl := l.layoutLocked()
	var b strings.Builder
	for _, s := range sl {
		b.WriteString(s.Peer)
		b.WriteByte('/')
		b.WriteString(s.Layer)
		b.WriteByte('/')
		b.WriteString(strconv.Itoa(int(s.MaxTID)))
		if s.Paused {
			b.WriteByte('p')
		}
		b.WriteByte(';')
	}
	sig := b.String()
	ev := false // ein Probe-Ereignis wird gemeldet, auch wenn sich die Belegung nicht aendert
	for _, s := range sl {
		ev = ev || s.Probe != ""
	}
	if sig == l.sig && !ev {
		return LayoutEvent{}, false
	}
	l.sig = sig
	return LayoutEvent{Room: l.room.ref, Peer: l.Peer.ID, Leg: l.Ref, Slots: sl, EstKbit: int(l.est.bps / 1000)}, true
}

// estimator schaetzt die Downlink-Rate eines Beins aus der eigenen
// Warteschlange: Auf TCP liegt der Stau dank TCP_NOTSENT_LOWAT im Knoten,
// das Alter der Pakete beim Schreiben zeigt ihn. Auf UDP kommt GCC (TWCC)
// als Stausignal dazu (Leg.Ext).
type estimator struct {
	bps, start, max float64
	lastBytes       uint64
	holdUntil       int64
	backoff         time.Duration
	lastDown        int64
	ups, downs      uint64
	// want: Bedarf der laufenden Auswahl bzw. des naechsten Schritts nach oben (planLeg, bit/s mit Reserve) - der
	// Deckel der freien Schaetzung liegt auf UDP nie darunter
	want float64
	// Schonfrist fuer ein nachlaufendes GCC-Ziel (lagging)
	gccLast   float64 // GCC-Ziel im letzten Takt
	ready     bool    // Ziel mindestens gccReady x Rate (bzw. kein GCC): Schritte nach oben erlaubt
	rateAvg   float64 // gesendete Rate, ueber rund 1 s geglaettet
	lag       bool    // in diesem Takt entschuldigt: Ziel unter der Rate, aber nicht gefallen
	lagBase   float64 // geglaettete Rate vor Beginn der Folge
	deadAt    int64   // wann die Folge gescheitert ist
	lagAt     int64   // Beginn der laufenden Folge (0 = keine)
	lagSeen   int64   // letzter Takt mit Ziel unter der Rate
	lagDead   bool    // Folge gescheitert: bis zu ihrem Ende zaehlt das Ziel wieder wie frueher als Stau
	lagJump   bool    // Folge begann mit einem Ratensprung (Schritt nach oben): nur dann gilt die Untergrenze lagFloor
	lagPeak   float64 // hoechstes Ziel seit Beginn
	lagMark   float64 // Ziel bei der letzten Fortschrittspruefung
	lagMarkAt int64
	failAt    int64 // letzte gescheiterte Folge: wann gescheitert, wann begonnen
	failFrom  int64
	// Probing (probe.go)
	probing    bool    // dieses Bein probt mit Fuelldaten: der Deckel folgt nicht mehr dem Bedarf (want)
	first      bool    // ... aber noch nicht: Anlauf, der freie Versuch des Beins steht aus - alles wie ohne Probing (planLeg)
	blind      bool    // ... ausser der naechste Schritt darf ohne Probe versucht werden (planLeg)
	wantBig    float64 // ... bzw. bis zum Bedarf eines Schritts, der sich nicht proben laesst (Fuellrate ueber probeFillMax)
	wantFree   float64 // ... bzw. eines Schritts, der ohne Probe versucht werden darf (freier Versuch, probe.go)
	capProbed  float64 // Deckel ohne wantBig: so weit duerfen probbare Schritte ohne Probe
	padLast    uint64  // Fuell-Bytes beim letzten Takt
	stepAt     int64   // letzter Schritt nach oben eines Platzes dieses Beins (planLeg; 0 = noch keiner)
	hxLast     uint64  // Bytes der Kopf-Erweiterungen beim letzten Takt (fwd.LegStats.HdrExtra)
	prOn       bool    // Probe laeuft: Stausignale nur beobachten (prSig), Schaetzung unveraendert
	prUntil    int64   // ... ebenso bis hierhin nach einer gescheiterten Probe (probeFloor)
	prReady    bool    // GCC-Ziel im Schutz weit genug ueber der Medienrate (wie ready)
	prTail     bool    // Nachlauf der Probe: ein fallendes Ziel zaehlt nicht (siehe probeStep)
	prN, prBad int     // Takte seit dem Abbruch; davon zuletzt in Folge mit Stausignal ohne Fuelldaten
	prSig      string  // hartes Stausignal dieses Takts waehrend des Schutzes: queue (eigene Warteschlange)
	prPeak     float64 // hoechstes GCC-Ziel seit Beginn der Probe bzw. seit dem letzten gewerteten Fall
	// Eigene Auswertung der Probe (probe.go, probeJudge): Hinweise dieses Takts
	prEv            string  // Ueberlast laut Laufzeit-Messung (over) bzw. Verlust gestiegen (loss)
	prFall          int     // Ziel gefallen: fallClamp | fallAmb | fallDec (0 = nicht gefallen)
	prBase          float64 // Rate des Beins vor der Probe (ohne Fuelldaten, geglaettet)
	prLoss0         float64 // Verlustanteil vor der Probe
	prLossN         int     // Takte in Folge mit Verlust deutlich darueber
	tot1, tot2, tot float64 // Rate des Beins mit Fuelldaten und Kopf-Erweiterungen in den letzten drei Takten (tot = dieser)
	grant           float64 // bestandene Probe: Deckel und Schaetzung mindestens so hoch, bis grantUntil
	grantUntil      int64
	// Eigener Stau-Detektor (Leg.ExtTip, HORN_BWE=eigen)
	det        bool    // das Bein hat einen
	detClear   bool    // ... und er widerlegt in diesem Takt einen Stau ausdruecklich (bwe.Tip.Clear)
	detCalm    bool    // ... bzw. zeigt wenigstens keine Schlange (bwe.Tip.Calm: wie Clear, ohne den Verlust)
	detFresh   bool    // ... bzw. sieht die Leitung ueberhaupt (bwe.Tip.Fresh: wie Calm, ohne die Schlange)
	detLossUp  bool    // ... der Verlust an der Spitze ist gestiegen (bwe.Tip.LossUp)
	lagDet     bool    // die laufende Schonfrist besteht nur, weil der Detektor einen Fall des Ziels widerlegt hat
	refuted    uint64  // Faelle des GCC-Ziels unter die Rate, die der Detektor widerlegt hat
	proofUntil int64   // Halten nach bestandener Probe (held): bis hierhin nimmt das GCC-Ziel allein die Schicht nicht zurueck
	heldN      uint64  // Folgen, deren Widerlegung mit dem Halten nach einer bestandenen Probe begann
	rateTick   float64 // Medienrate dieses Takts - an ihr wird das GCC-Ziel gemessen (fuers Log)
	// Eigenes Buendel in einer laufenden Widerlegung (blip)
	ratePrev float64 // Medienrate des vorigen Takts
	avgPrev  float64 // ... und die geglaettete Rate davor
	blipAt   int64   // letzter entschuldigter Takt (0 = keiner)
	blips    uint64  // entschuldigte Takte (auch wenn die Folge im selben Takt aus anderem Grund endete)
	// Fuers Log (LegEvent): laufende Widerlegung, Ereignis dieses Takts, Grund der letzten verweigerten Schonfrist und
	// der letzten Senkung der Schaetzung
	refAt   int64  // Beginn der laufenden Widerlegung (0 = keine)
	refArt  string // ... ihre Art: refSpitze | refHalten
	note    legNote
	lagWhy  string
	downWhy string
	downAt  int64
	// Ende-zu-Ende-Signal der TCP-Wege (tcpstau.go, Leg.ExtQueue)
	e2eOn      bool          // das Bein hat es
	e2e        TCPStauStats  // Zaehler und letzter Stand
	e2eN       int           // Takte in Folge mit Schlange ueber congAge
	e2eRef     time.Duration // ... und ihr kleinster Wert darin
	e2eHit     bool          // in diesem Takt: Stau laut Signal
	e2eAge     bool          // ... und das Alter der Schaetzung kommt von ihm (nicht von der eigenen Warteschlange)
	e2eHold    bool          // in diesem Takt: kein Schritt nach oben wegen des Signals
	e2eDrainTo int64         // nach einem Stau laut Signal: bis hierhin halten, solange die Schlange steht
}

// legNote: Ereignis der Schaetzung in diesem Takt (Beginn bzw. Ende einer Widerlegung) - planLeg macht daraus ein
// LegEvent.
type legNote struct {
	kind, art, why string
	dur            time.Duration
}

// Art einer Widerlegung (LegEvent.Art).
const (
	refSpitze = "spitze" // der Detektor widerlegt den Stau ausdruecklich (bwe.Tip.Clear)
	refHalten = "halten" // eben bestandene Probe, der Detektor zeigt kein Zeichen von Stau (held)
)

// Halten nach bestandener Probe (HORN_BWE=eigen, estimator.held).
const (
	// probeKeep: So lange nach einer bestandenen Probe nimmt das GCC-Ziel allein die Schicht nicht zurueck. Die Probe
	// hat gegen GCC bestanden (der Detektor sah keinen Stau, die Zielrate kam an); gehorchte die Schaetzung GCC gleich
	// danach wieder, hielte der Schritt 0,2 s (Leitung 1400 kbit/s mit 20 ms Puffer: 6 Schritte l -> m, keiner hielt).
	probeKeep = 3 * time.Second
	// probeBurst: So lange nach dem letzten Schritt nach oben des Beins (stepAt) zaehlt in dieser Zeit ein gestiegener
	// Verlust nicht als Zeichen von Stau - das Schluesselbild der neuen Schicht kommt als Buendel und ueberlaeuft einen
	// kurzen Puffer (gemessen: der Verlust stand 0,7 bis 0,8 s lang in den letzten 500 ms der Rueckmeldungen). Eine
	// Schlange zaehlt sofort. Ein weiterer Schritt nach oben innerhalb von probeKeep (anderer Platz, Kachel aus der
	// Pause) setzt die Frist neu an - laengstens bis probeKeep nach der Probe.
	probeBurst = 1200 * time.Millisecond
)

// Eigenes Buendel in einer laufenden Widerlegung (HORN_BWE=eigen, estimator.blip).
const (
	// burstMin: Ab dieser Rate eines Takts gegenueber der geglaetteten Rate hat das Bein selbst ein Buendel gesendet
	// (Schluesselbild: am Testaufbau 1,35 x, bei einer Kamera das 2- bis 3-fache). Die Bedingung trifft auch sonst zu:
	// rund 1 s lang nach jedem Schritt nach oben (die geglaettete Rate laeuft nach) und bei Beinen mit wenigen Bildern
	// je Sekunde in vielen Takten. Deshalb ist die Ausnahme auf einen Takt begrenzt.
	burstMin = 1.25
	// blipGap: Entschuldigt wird nicht zweimal binnen dieser Zeit - bei 200 ms je Takt also nicht zwei Takte in Folge.
	// (Verspaetet sich ein Takt unter Last um mehr als 100 ms, koennen zwei aufeinanderfolgende entschuldigt werden.)
	blipGap = 300 * time.Millisecond
)

// Schwellen der Schaetzung.
const (
	congAge  = 100 * time.Millisecond // ab hier Stau
	clearAge = 40 * time.Millisecond  // darunter frei
	minBps   = 100e3
)

// Schonfrist fuer ein nachlaufendes GCC-Ziel. pion-GCC senkt sein Ziel nur bei Stau (Verzoegerung: auf 85 % der
// empfangenen Rate; Verlust ueber 10 %: um den halben Verlustanteil) und hebt es sonst mit rund 8 %/s (nahe einer
// frueheren Staurate additiv, etwa ein halbes Paket je Antwortzeit). Springt die gesendete Rate (Schicht hoeher, Kachel
// dazu, Schluesselbild), liegt ein unveraendertes oder steigendes Ziel daher eine Weile unter der Rate, ohne dass die
// Leitung eng waere. Entschuldigt wird das nur, solange alle Bedingungen gelten - sonst zaehlt es wie frueher als Stau:
//   - das Ziel faellt nicht (weder im Takt, in dem die Rate es ueberholt, noch danach unter gccFall des Hoechstwerts)
//   - es steigt weiter: je lagStep mindestens um lagRise (ein nach dem Stau-Ruecksprung stehendes Ziel faellt so auf)
//   - hoechstens lagMax am Stueck; eine Folge endet erst nach lagClear ohne Rueckstand
//   - es laeuft keine Sperrzeit (kurz nach einem Stau gilt die alte Regel)
//
// Nach einem Ruecksprung steigt das Ziel auch bei anhaltendem Stau zunaechst wieder (der Verlust-Teil holt mit 5 % je
// 200 ms zum Verzoegerungs-Teil auf, der bei 85 % der Leitung steht). Damit ein Stau nach einem Schritt nach oben als
// Fall erkennbar ist, geht es nur nach oben, wenn das Ziel mindestens gccReady ueber der Rate liegt (ready). Scheitert
// die Schonfrist, faellt die Schaetzung lagFloor lang nicht unter die Rate vor dem Versuch: Der erste Ruecksprung des
// Ziels (85 % eines Fensters, das noch halb die alte Rate enthaelt) sagt nichts ueber die Stufe davor.
const (
	gccFall  = 0.97             // Ziel unter diesem Anteil des Vergleichswerts: gefallen
	gccReady = 1.25             // Ziel mindestens so weit ueber der Rate: Schritt nach oben erlaubt
	lagRise  = 1.015            // Mindestanstieg je lagStep
	lagStep  = time.Second      // Abstand der Fortschrittspruefung
	lagMax   = 15 * time.Second // laengste Schonfrist
	lagClear = 2 * time.Second  // so lange ohne Rueckstand: Folge beendet
	lagLink  = 3 * time.Second  // Folge beginnt binnen dieser Zeit nach einem Hochschalten: gehoert zu diesem Versuch
	lagFloor = 2 * time.Second  // so lange nach dem Scheitern nicht unter den Stand vor dem Versuch
	// Die Untergrenze gilt nur, wenn die Folge mit einem Ratensprung begann (Schritt nach oben). Bricht die Leitung
	// bei gleichbleibender Rate ein, gibt es keinen "Stand davor" - dann zaehlt das Ziel sofort wie frueher.
	lagJumpMin = 1.1
)

// lagging: Das GCC-Ziel v liegt unter der gesendeten Rate - laeuft es nur nach (true, kein Stau) oder ist das Stau?
func (e *estimator) lagging(v, rate float64, now int64) bool {
	if e.lagAt == 0 {
		if now < e.holdUntil || e.gccLast <= 0 {
			e.lagWhy = "sperre"
			return false
		}
		e.lagAt, e.lagBase = now, e.rateAvg
		e.lagJump = rate > e.lagBase*lagJumpMin
		e.lagDet = false
		// faellt, waehrend die Rate es ueberholt: Stau, bis zum Ende dieser Folge - ausser der Fall ist kein Ruecksprung
		// von GCC, sondern die Klemmung an die Empfangsrate (clamped): Dann beginnt die Schonfrist beim neuen Ziel.
		// Ebenso (eigen), wenn der eigene Detektor den Stau in diesem Takt ausdruecklich widerlegt bzw. nach einer eben
		// bestandenen Probe kein Zeichen von Stau zeigt (refute).
		if v < e.gccLast*gccFall && !e.clamped(v, now) && !e.refute(now) {
			e.lagDead, e.deadAt, e.lagWhy = true, now, "fall"
			return false
		}
		e.lagDead, e.lagPeak, e.lagMark, e.lagMarkAt = false, v, v, now
		return true
	}
	if e.lagDead {
		if e.deadAt != now {
			e.lagWhy = "tot" // die Folge ist schon frueher gescheitert und noch nicht zu Ende
		}
		return false
	}
	if v > e.lagPeak {
		e.lagPeak = v
	}
	ok := true
	switch {
	case e.lagDet && !e.detClear && !e.held(now) && !e.blip(rate, now):
		// Die Schonfrist stand nur auf der Widerlegung durch den Detektor - und der widerlegt nicht mehr (Schlange an
		// der Spitze, Verlust gestiegen, Warnung, Rueckmeldung alt): Das Signal von GCC gilt, sofort. Ausnahme: ein
		// einzelner Takt, in dem nur das eigene Buendel des Beins die Schlange zeigt (blip).
		ok, e.lagWhy = false, "detektor"
	case v < e.lagPeak*gccFall && now-e.lagAt < int64(lagMax) && (e.clamped(v, now) || e.refute(now)):
		// Klemmung waehrend der Schonfrist: kein Stau - aber von hier aus muss das Ziel wieder steigen
		e.lagPeak, e.lagMark, e.lagMarkAt = v, v, now
	case v < e.lagPeak*gccFall:
		ok, e.lagWhy = false, "faellt"
	case now-e.lagAt >= int64(lagMax):
		ok, e.lagWhy = false, "frist"
	case now-e.lagMarkAt >= int64(lagStep):
		// Fortschritt - im Halten nach einer bestandenen Probe darf das Ziel auch stehen
		switch ok = v >= e.lagMark*lagRise; {
		case ok:
		case e.held(now):
			ok = true
			e.refStart(refHalten, now)
		default:
			e.lagWhy = "steht"
		}
		e.lagMark, e.lagMarkAt = v, now
	}
	if !ok {
		e.lagDead, e.deadAt, e.failAt, e.failFrom = true, now, now, e.lagAt
		e.refEnd(e.lagWhy, now)
	}
	return ok
}

// clamped: Das GCC-Ziel v ist gefallen und liegt unter der Rate - aber so hoch, dass es kein Ruecksprung wegen Stau
// sein kann (sendRate, probeDecSent). Dann hat pion sein Ziel nur an die empfangene Rate geklemmt (1,5 x, siehe
// fallClass in probe.go). Das trifft auch den Wechsel selbst: Springt die Rate nach einem Schritt nach oben durch das
// Band um die letzte Staurate, faellt ein weit gewachsenes Ziel auf das 1,5-Fache der noch gemischten Empfangsrate -
// unter die neue Rate. Ohne diese Unterscheidung scheiterte der Schritt auf freier Leitung gleich nach einer
// bestandenen Probe (im Container nachgestellt: h hielt 0,2 s).
//
// Nur mit Probing und nur clampWin lang nach einem Schritt nach oben - sonst bleibt die Regel, wie sie war (ein
// gefallenes Ziel unter der Rate ist Stau, sofort). Im Dauerzustand springt die Rate nicht durch das Band; dort
// verdeckte die Ausnahme sonst einen echten Ruecksprung bei leichtem Stau bis zu lagMax (Pruefung 3, Fund 2).
func (e *estimator) clamped(v float64, now int64) bool {
	return e.probing && !e.first && e.stepAt != 0 && now-e.stepAt < int64(clampWin) && v > probeDecSent*e.sendRate()
}

// refute (HORN_BWE=eigen): Das GCC-Ziel ist gefallen und liegt unter der Rate - aber der eigene Stau-Detektor widerlegt
// den Stau in diesem Takt ausdruecklich (bwe.Tip.Clear: Zustand frei, die juengsten bestaetigten Pakete ohne Schlange,
// Verlust nicht gestiegen, Rueckmeldung frisch). Dann beginnt wie bei der Klemmung die Schonfrist beim neuen Ziel: Es
// muss von dort wieder steigen (lagRise), laengstens lagMax - und die Widerlegung muss in jedem weiteren Takt der Folge
// neu gelten (lagDet in lagging), sonst gilt das Signal von GCC sofort.
//
// Warum so und nicht umgekehrt ("Detektor meldet Stau"): In echter Enge ist der Zustand des Detektors langsamer als
// GCC (Warnung nach 0,4 bis 0,7 s, GCC 0,2 bis 0,3 s; im Labor meldete er in 11 von 12 Schritten nichts, bevor der
// Knoten unten war). Die Widerlegung dagegen stuetzt sich auf die Laufzeit der juengsten Pakete ueber der Basis
// (10 ms): In echter Enge ist sie zu dem Zeitpunkt, an dem GCC Ueberlast meldet, schon ueberschritten (Modell: sie
// haelt bis 0,15 bis 0,17 s nach Beginn der Enge) - das Signal von GCC gilt dort also so schnell wie bisher. Nur wo
// die Leitung den Fall des Ziels nicht zeigt, bleibt die Schicht. Grenze: Liegt die Leitung nur knapp unter der Rate
// (wenige Prozent), waechst die Schlange so langsam, dass die Widerlegung noch gilt, wenn GCC schon meldet - dann
// bleibt die Schicht, bis die Schlange 10 ms erreicht (hoechstens rund 1 s laenger als bei gcc, bei einer Schlange
// unter 10 ms).
//
// Halten nach bestandener Probe (held): probeKeep lang nach einer bestandenen Probe genuegt dafuer, dass der Detektor
// kein Zeichen von Stau zeigt - auch ein Verlust-Schub in den ersten probeBurst nach dem Schritt zaehlt dann nicht. Die
// Folge steht danach wie jede Widerlegung auf dem Detektor (lagDet): Sobald das Halten endet, muss er in jedem Takt
// ausdruecklich widerlegen.
func (e *estimator) refute(now int64) bool {
	switch {
	case !e.det:
		return false
	case e.detClear:
		e.refStart(refSpitze, now)
	case e.held(now):
		e.refStart(refHalten, now)
	default:
		return false
	}
	return true
}

// refStart: Die laufende Schonfrist steht ab jetzt auf dem Detektor (lagDet) - Beginn einer Widerlegung.
func (e *estimator) refStart(art string, now int64) {
	if e.lagDet {
		return
	}
	e.lagDet = true
	if art == refSpitze {
		e.refuted++
	} else {
		e.heldN++
	}
	e.refAt, e.refArt = now, art
	e.note = legNote{kind: LegRefute, art: art}
}

// held (HORN_BWE=eigen): Gilt in diesem Takt das Halten nach einer bestandenen Probe? Bis probeKeep nach der Probe,
// solange der Detektor kein Zeichen von Stau zeigt: Zustand frei, Rueckmeldung frisch, keine Schlange an der Spitze
// (bwe.Tip.Calm). Ein gestiegener Verlust ist ein Zeichen - ausser in den ersten probeBurst nach dem letzten Schritt
// nach oben (das Schluesselbild der neuen Schicht). Sieht der Detektor nichts (Rueckmeldung alt, Basis jung), gilt das
// Halten nicht: Dann entscheidet GCC wie bisher.
//
// Das Halten gilt fuer das Bein, nicht fuer einen Platz: In der Zeit ist auch ein weiterer Schritt nach oben ohne
// Probe gehalten (die Schaetzung ist eine je Bein).
//
// Grenze: Ein Einbruch gleich nach dem Schritt, der sich nur als Verlust zeigt (Policer, Puffer unter 10 ms), wird
// bis zu probeBurst spaeter behandelt als ohne das Halten (gemessen ueber UDP: 0,2 s) - mit weiteren Schritten nach
// oben in der Zeit bis zu probeKeep; mit einer Schlange an der Spitze so schnell wie bisher.
func (e *estimator) held(now int64) bool {
	if !e.det || now >= e.proofUntil || !e.detCalm {
		return false
	}
	return !e.detLossUp || e.stepAt != 0 && now-e.stepAt < int64(probeBurst)
}

// blip (HORN_BWE=eigen): Die laufende Widerlegung gilt in diesem Takt nicht - aber nur, weil das Bein selbst ein
// Buendel gesendet hat (Medienrate dieses oder des vorigen Takts mindestens burstMin x der geglaetteten Rate davor -
// ein Schluesselbild, siehe burstMin) und der Detektor sonst nichts zeigt: Zustand frei, Rueckmeldung frisch, Verlust
// nicht gestiegen (bwe.Tip.Fresh, nicht LossUp). Die Schlange an der Spitze ist dann das Abbild des eigenen Buendels -
// die letzten bestaetigten Pakete standen hintereinander an - und laeuft binnen eines Takts wieder ab. Entschuldigt
// wird EIN Takt (blipGap); eine Widerlegung beginnt so nicht (refute), sie uebersteht es nur.
//
// Ohne das endete am Testaufbau rund jede zweite Widerlegung auf einer Leitung knapp unter h an einem Schluesselbild
// der Quelle (alle 10 s; 5 von 11), und der Platz fiel auf l - obwohl die Leitung m mit doppelter Reserve trug.
// Grenze: Beginnt eine echte Enge in einem solchen Takt - beim Schluesselbild oder in der Sekunde nach einem Schritt
// nach oben - waehrend eine Widerlegung laeuft, gilt das Signal von GCC einen Takt spaeter.
func (e *estimator) blip(rate float64, now int64) bool {
	if !e.det || !e.detFresh || e.detLossUp {
		return false
	}
	if !(e.rateAvg > 0 && rate >= burstMin*e.rateAvg || e.avgPrev > 0 && e.ratePrev >= burstMin*e.avgPrev) {
		return false
	}
	if e.blipAt != 0 && now-e.blipAt <= int64(blipGap) {
		return false
	}
	e.blipAt = now
	e.blips++
	return true
}

// refEnd: Die Folge, die auf einer Widerlegung stand, ist zu Ende (fuers Log).
func (e *estimator) refEnd(why string, now int64) {
	if e.refAt == 0 {
		return
	}
	e.note = legNote{kind: LegRefuteEnd, art: e.refArt, why: why, dur: time.Duration(now - e.refAt)}
	e.refAt = 0
}

// clampWin: So lange nach einem Schritt nach oben kann die Klemmung den Wechsel treffen. Sie braucht eine Empfangsrate,
// die noch Pakete von vor dem Schritt enthaelt: das Fenster von pion (500 ms), davor das Schluesselbild der neuen
// Schicht (der Ausgang wechselt erst mit ihm, bis rund 1 s) und der Weg der Rueckmeldung (Umlaufzeit und Abstand der
// Rueckmeldungen, zusammen bis rund 0,5 s), dazu ein Takt Abtastung - gut 2 s. Derselbe Wert wie lagLink (so lange
// nach einem Hochschalten gehoert eine Schonfrist zu diesem Versuch).
const clampWin = lagLink

// sendRate: Was das Bein in den letzten 500 ms gesendet hat (bit/s) - mit Fuelldaten und so gezaehlt wie bei pion-GCC
// (Kopf samt Erweiterungen, fwd.LegStats.HdrExtra). 500 ms ist das Fenster, ueber das pion die Empfangsrate bildet.
func (e *estimator) sendRate() float64 { return (e.tot + e.tot1 + 0.5*e.tot2) / 2.5 }

// probeFailed: Ist die Schonfrist gescheitert, die mit dem Hochschalten zur Zeit upAt begann (Stau binnen upFail
// zuvor gemeldet)?
func (e *estimator) probeFailed(upAt, now int64) bool {
	return e.failAt != 0 && now-e.failAt < int64(upFail) && e.failFrom >= upAt && e.failFrom-upAt < int64(lagLink)
}

func (e *estimator) init(start, max float64) {
	e.bps, e.start, e.max = start, start, max
	e.backoff = 2 * time.Second
}

func (e *estimator) canUp(now int64) bool { return now >= e.holdUntil }

func (e *estimator) update(l *Leg, dt float64, now int64) float64 {
	st := &l.fl.Stats
	b := st.BytesOut.Load()
	rate := float64(b-e.lastBytes) * 8 / dt
	e.lastBytes = b
	// tot zaehlt wie pion-GCC: mit den Kopf-Erweiterungen, die BytesOut nicht enthaelt (fallClass, clamped)
	hx := st.HdrExtra.Load()
	e.tot2, e.tot1, e.tot = e.tot1, e.tot, rate+float64(hx-e.hxLast)*8/dt
	e.hxLast = hx
	// Fuelldaten einer Probe zaehlen zur Bein-Rate, aber nicht zur Medienrate, mit der hier gerechnet wird
	if pb := st.PadBytes.Load(); pb != e.padLast {
		rate = max(rate-float64(pb-e.padLast)*8/dt, 0)
		e.padLast = pb
	}
	e.rateTick = rate
	age := time.Duration(st.TickAgeNs.Swap(0))
	drops := st.TickDrops.Swap(0)
	e.note = legNote{}
	var e2eAck float64 // bestaetigte Rate bei Stau laut Ende-zu-Ende-Signal (0 = keiner)
	e.e2eHit, e.e2eAge, e.e2eHold = false, false, false
	if l.ExtQueue != nil && l.Ext == nil { // nur TCP-Wege (ohne GCC): UDP bleibt unveraendert
		age, e2eAck = e.e2eStep(l.ExtQueue(), age, now)
	}
	if e.det = l.ExtTip != nil; e.det {
		tip := l.ExtTip()
		e.detClear, e.detCalm, e.detFresh, e.detLossUp = tip.Clear, tip.Calm, tip.Fresh, tip.LossUp
	}
	shield := e.prOn || e.prUntil != 0 && now < e.prUntil
	if shield && !e.prOn {
		// Nach einer gescheiterten Probe schuetzt die Frist nur vor dem Stau, den die Fuelldaten selbst erzeugt haben -
		// der baut sich binnen probeDrain Takten ab. Steht danach, ohne Fuelldaten, weiter ein Stausignal an (eigene
		// Warteschlange; GCC-Ziel unter der Medienrate und dabei fallend bzw. Ueberlast gemeldet), probeBad Takte in
		// Folge, ist die Leitung wirklich eingebrochen: Schutz sofort beenden und reagieren wie ohne Probing.
		v := 0.0
		if l.Ext != nil {
			v = float64(l.Ext())
		}
		bad := age > congAge || drops > 0 ||
			v > 0 && v < rate*0.9 && (v < e.gccLast*gccFall || l.ExtOver != nil && l.ExtOver())
		if e.prN++; e.prN > probeDrain && bad {
			e.prBad++
		} else {
			e.prBad = 0
		}
		if e.prBad >= probeBad {
			shield = false
		}
	}
	if shield {
		// Probe bzw. kurz nach einer gescheiterten: Stausignale nur festhalten. Die Schaetzung bleibt, wie sie vor der
		// Probe war - nach oben geht in dieser Zeit nichts (ready).
		v := 0.0
		if l.Ext != nil {
			v = float64(l.Ext())
		}
		e.prSig, e.prEv, e.prFall = "", "", 0
		switch {
		case !e.prOn:
		case age > congAge || drops > 0:
			e.prSig = "queue"
		default:
			// Hinweise fuer die eigene Auswertung der Probe (probeJudge): Ueberlast laut Laufzeit-Messung, Verlust
			// gegenueber dem Stand vor der Probe, und ein gefallenes Ziel - eingeordnet danach, ob es ein Ruecksprung
			// von GCC sein kann (fallClass). Ein gewerteter Fall setzt den Vergleichswert neu an.
			var x ExtStat
			if l.ExtStat != nil {
				x = l.ExtStat()
			} else if l.ExtOver != nil {
				x.Over = l.ExtOver()
			}
			// Verlust zaehlt erst im zweiten Takt in Folge: pion mittelt den Verlustanteil ueber rund 200 ms und zaehlt
			// ein Paket, das zur Rueckmeldung noch fehlt, als verloren - ein einzelner Ausschlag (spaetes Paket) klingt
			// binnen eines Takts ab, echter Verlust durch die Fuelldaten bleibt stehen. (Ohne das: in 152 Proben auf
			// freier Leitung am Testaufbau zweimal faelschlich "loss".)
			if x.Loss > e.prLoss0+probeLossUp {
				e.prLossN++
			} else {
				e.prLossN = 0
			}
			switch {
			case x.Over:
				e.prEv = "over"
			case e.prLossN >= 2:
				e.prEv = "loss"
			}
			if v > 0 && v < e.prPeak*gccFall && !e.prTail {
				e.prFall = e.fallClass(v)
				e.prPeak = v
			}
		}
		if v > e.prPeak {
			e.prPeak = v
		}
		e.gccLast, e.lag, e.ready = v, false, false
		e.prReady = v <= 0 || v >= rate*gccReady
		e.avgPrev, e.ratePrev = e.rateAvg, rate
		e.rateAvg = 0.8*e.rateAvg + 0.2*rate
		return e.bps
	}
	after := e.prUntil != 0 // erster Takt nach dem Schutz einer gescheiterten Probe
	e.prUntil = 0
	// GCC (UDP) ist ohne Probepakete anwendungsbegrenzt und taugt daher nur als Stausignal: Ziel unter der Rate =
	// Stau - ausser das Ziel laeuft der Rate nur nach (lagging, begrenzte Schonfrist).
	gccLow := 0.0
	e.lag, e.ready = false, true
	if l.Ext != nil {
		v := float64(l.Ext())
		e.ready = v <= 0 || v >= rate*gccReady
		if after && v > 0 && v < rate*0.9 && !(e.det && e.detClear) {
			// Das Ziel liegt auch probeFloor nach der gescheiterten Probe noch unter der Rate: Stau wie frueher, ohne
			// Schonfrist (das Ziel ist im Schutz gefallen; dass es inzwischen wieder steigt, entschuldigt nichts)
			e.lagAt, e.lagDead, e.lagJump, e.deadAt = now, true, false, now
			e.lagWhy = "nach-probe"
			e.refEnd("probe", now)
		}
		switch {
		case v > 0 && v < rate*0.9:
			e.lagSeen = now
			if e.lagging(v, rate, now) {
				e.lag = true
			} else {
				gccLow = v
			}
		case e.lagAt != 0 && now-e.lagSeen >= int64(lagClear):
			e.lagAt, e.lagDead = 0, false
			e.refEnd("erholt", now) // das Ziel liegt wieder ueber der Rate
		}
		e.gccLast = v
	}
	e.avgPrev, e.ratePrev = e.rateAvg, rate
	if e.rateAvg == 0 {
		e.rateAvg = rate
	} else {
		e.rateAvg = 0.8*e.rateAvg + 0.2*rate
	}
	switch {
	case age > congAge || drops > 0 || gccLow > 0:
		// Grund der Senkung (fuers Log, LegEvent): die eigene Warteschlange oder das GCC-Ziel unter der Rate - und
		// warum es nicht entschuldigt war (lagging)
		if e.downAt = now; age > congAge || drops > 0 {
			e.downWhy = "schlange"
			if e.e2eAge && drops == 0 {
				e.downWhy = "e2e" // Schlange unterwegs (transport-cc des Geraets), nicht im Knoten
			}
		} else {
			e.downWhy = "gcc-" + e.lagWhy
		}
		if gccLow > 0 && gccLow < rate {
			rate = gccLow / 0.85
		}
		if e2eAck > 0 && e2eAck < rate {
			rate = e2eAck // waechst die Schlange unterwegs, kommt weniger an, als der Knoten schreibt
		}
		if e.e2eHit {
			e.e2eDown(now)
		}
		// runter auf 85 % des tatsaechlich Durchgegangenen (nicht kumulativ)
		t := rate * 0.85
		if e.lagDead && e.lagJump && gccLow > 0 && now-e.deadAt < int64(lagFloor) && t < e.lagBase {
			t = e.lagBase // gescheiterter Versuch nach oben: zurueck auf den Stand davor, nicht darunter
		}
		if t > e.bps {
			t = e.bps
		}
		if t < minBps {
			t = minBps
		}
		e.bps = t
		if now >= e.holdUntil {
			// Stau nach freier Zeit bzw. nach einem Versuch nach oben:
			// Sperrzeit verdoppeln, wenn der letzte Stau kurz her ist
			if e.lastDown != 0 && now-e.lastDown < int64(20*time.Second) { // 0 = noch kein Stau (nicht: Stau bei Programmstart)
				e.backoff *= 2
				if e.backoff > 30*time.Second {
					e.backoff = 30 * time.Second
				}
			} else {
				e.backoff = 2 * time.Second
			}
			e.downs++
		}
		e.lastDown = now
		e.holdUntil = now + int64(e.backoff)
	case age < clearAge:
		if rate > e.bps {
			e.bps = rate
		}
		if now >= e.holdUntil && !e.lag {
			e.bps *= 1.04
			e.ups++
		}
		// ohne Proben nicht beliebig ueber das Gemessene hinaus - auf UDP aber mindestens bis zum Bedarf des naechsten
		// Schritts (der Wechsel selbst ist die Probe, GCC meldet ihr Scheitern). TCP bleibt beim alten Deckel.
		c := rate*1.6 + 300e3
		if c < e.start {
			c = e.start
		}
		if l.Ext != nil && c < e.want && !(e.probing && e.gccLast > 0 && !e.blind) {
			c = e.want
		}
		if now < e.grantUntil && c < e.grant { // bestandene Probe: bis der Schritt in der Rate sichtbar ist
			c = e.grant
		}
		e.capProbed = c
		if e.probing && c < e.wantBig { // nicht probbarer Schritt: wie ohne Probing bis zu seinem Bedarf
			c = e.wantBig
		}
		if e.probing && c < e.wantFree { // freier Versuch (probe.go): ebenso
			c = e.wantFree
		}
		if e.first && l.Ext != nil && c < e.want { // Anlauf: Deckel wie ohne Probing (capProbed bleibt der mit Probing)
			c = e.want
		}
		if e.bps > c {
			e.bps = c
		}
	}
	if e.bps > e.max {
		e.bps = e.max
	}
	return e.bps
}
