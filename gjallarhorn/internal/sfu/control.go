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

// Schwellen der Sprechererkennung.
const (
	speakOn       = 0.04                    // Aktivitaet, ab der jemand als sprechend gilt
	domHold       = 1500 * time.Millisecond // Mindestdauer des Hauptsprechers
	domFactor     = 1.4                     // so viel lauter muss ein neuer Hauptsprecher sein
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
)

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
	r.mu.Unlock()
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
func (r *Room) pickDominant(peers []*Peer, now int64) {
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
	}
	r.dominant = best
	r.domSince = now
	r.cand = nil
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

// layerRate liefert die gemessene (sonst angenommene) Rate einer Quelle.
func layerRate(st *source) float64 {
	if st.bps > 1000 {
		return st.bps
	}
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
		if len(cand) > len(aslots) {
			// wer schon einen Platz hat und noch spricht, bleibt
			cand = keepActive(aslots, cand)
		}
		assignSticky(aslots, cand)
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
		if spk != nil && len(spk.cam) == 0 {
			spk = nil
		}
		if spk == nil {
			rec := append([]*Peer(nil), others...)
			byRecent(rec)
			for _, p := range rec {
				if len(p.cam) > 0 {
					spk = p
					break
				}
			}
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
	// Schaetzung und Verteilung
	prb := l.probing(cfg)
	l.est.probing = prb
	l.est.update(l, dt, now)
	l.probeStep(now) // laufende Probe: weiter, Abbruch oder Freigabe (hebt dann die Schaetzung)
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
		if stream {
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
	sum := 0.0
	for _, c := range cs {
		c.rate = rateOf(c)
		sum += c.rate
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
	// Auf UDP geht je Takt hoechstens ein Platz eine Schicht hoeher (derselbe Platz darf mehrere Schichten nehmen): Jeder
	// Schritt ist eine Probe der Leitung - gingen mehrere Plaetze im selben Takt, springt die Rate um die Summe, und ein
	// Stau danach liesse sich keinem Schritt zuordnen. Ob der Schritt traegt, zeigt erst der naechste Takt (Schonfrist
	// bzw. ready). Kacheln, die aus der Pause dazukommen (unterste Schicht), zaehlen nicht mit: Jemanden zu zeigen geht
	// vor. TCP bleibt wie bisher.
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
	var cand struct {
		s    *slot
		rank int
		need float64
	}
	// Kacheln, die gerade nichts zeigen, starten pausiert und kommen nur mit
	// Reserve dazu (gegen Pendeln bei schwankenden Raten)
	for _, c := range cs {
		if c.s.class == "tile" && c.idx >= 0 && c.s.layer == "" {
			sum -= c.rate
			c.idx, c.rate = -1, 0
			c.wasOff = true
		}
	}
	// zu wenig: laufende Kacheln von hinten pausieren (Bild mit Sprechring)
	for i := len(cs) - 1; i >= 0 && sum > budget; i-- {
		c := cs[i]
		if c.s.class != "tile" || c.idx < 0 {
			continue
		}
		sum -= c.rate
		c.idx, c.rate = -1, 0
	}
	up := func(c *choice, maxIdx int) {
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
			// passed: Fuer genau diesen Schritt ist gerade eine Probe bestanden (probe.go)
			passed := prb && !cur && d.ok != 0 && now-d.ok <= int64(probeGrant)
			switch {
			case prb && !cur && (d.d > layerDwell || d.pd > 0):
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
				if oneUp && stepper != nil && stepper != c {
					lim = 0
				}
				// Die Probe hat die Rate des Schritts belegt, wie sie zu ihrem Beginn war. Schwankt der Bedarf seither
				// leicht (Schluesselbild in der geglaetteten Rate der Quelle), scheitert der Schritt nicht daran.
				if t := need*upHeadroom + audioCut; passed && lim > 0 && t > budget+audioCut && t <= l.est.grant*probeSlack && t <= quotaCap {
					l.est.bps = max(l.est.bps, min(t, l.est.max))
					budget = max(budget, need*upHeadroom)
					lim, limit, upLimit = budget, budget, budget
				}
			}
			if layerStep(cur, need, budget, lim, since) {
				sum, c.rate = need, nr
				if !cur {
					stepper = c
				}
				continue
			}
			if !cur {
				deny(need)
				if prb && cand.s == nil && stepper == nil && upLimit > 0 && sinceDown >= int64(layerDwell) && now >= d.next &&
					(blocked || need*upHeadroom > upLimit) {
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
	for _, c := range cs {
		if c.s.class == "screen" || c.s.class == "speaker" {
			up(c, 2)
		}
	}
	for _, c := range cs {
		if !c.wasOff || len(c.opts) == 0 {
			continue
		}
		c.idx = 0
		nr := rateOf(c)
		// Probing: nach einer gescheiterten Probe fuer diese Kachel erst wieder mit einer bestandenen
		var d dwell
		if c.s.owner == c.s.dwellOwner {
			d = c.s.dw[layerRank(c.opts[0].Layer)]
		}
		blocked := prb && d.pd > 0 && (d.ok == 0 || now-d.ok > int64(probeGrant))
		if (sum+nr)*upHeadroom <= upLimit && !blocked {
			sum, c.rate = sum+nr, nr
		} else {
			deny(sum + nr)
			if prb && cand.s == nil && stepper == nil && upLimit > 0 && c.s.owner != nil && now >= d.next {
				cand.s, cand.rank, cand.need = c.s, layerRank(c.opts[0].Layer), sum+nr
			}
			c.idx = -1
		}
	}
	if !stream { // UDP-Kacheln duerfen bis zur mittleren Schicht
		for _, c := range cs {
			if c.s.class == "tile" {
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
	moved := false // aendert dieser Takt die Auswahl? Dann beginnt keine Probe (die Rate ist in Bewegung)
	for _, c := range cs {
		to := ""
		if c.idx >= 0 {
			to = c.opts[c.idx].Layer
		}
		moved = moved || to != c.s.layer
	}
	for _, c := range cs {
		s := c.s
		if c.idx < 0 {
			if s.layer != "" && s.owner != nil && s.src != nil && s.src.peer == s.owner {
				s.setWhy(whyBudget, s.src, now) // laufendes Bild pausiert
			}
			s.out.Assign(nil)
			s.paused, s.layer = s.owner != nil, ""
			continue
		}
		st := c.opts[c.idx]
		s.out.Assign(st.Source)
		s.out.SetMaxTID(c.tid)
		if prev := s.src; prev != nil && prev.peer == s.owner && (st != prev || s.layer == "") {
			switch {
			case s.layer == "": // Pause zu Ende
				s.setWhy(whyBudget, st, now)
			case layerRank(st.Layer) < layerRank(prev.Layer) && !prev.usable(now):
				s.setWhy(whyGone, prev, now)
			case layerRank(st.Layer) > layerRank(prev.Layer) && st.fl.backAt != 0 && st.fl.backAt > s.srcAt:
				s.setWhy(whyBack, st, now)
			default:
				s.setWhy(whyBudget, st, now)
			}
		}
		if st != s.src || s.layer == "" {
			s.heldAt = now
		}
		if st != s.src {
			s.src, s.srcAt = st, now
		}
		if layerRank(st.Layer) < layerRank(s.layer) { // nach unten: Mindestverweildauer fuer den Weg zurueck
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
			fast := s.why == whyBudget && s.upAt != 0 && s.upOwner == s.owner &&
				(now-s.upAt < int64(upFail) || l.est.probeFailed(s.upAt, now))
			if s.dwellOwner != s.owner {
				s.dw, s.dwellOwner = [4]dwell{}, s.owner
			}
			d := &s.dw[layerRank(s.layer)]
			switch {
			case fast:
				d.d, d.at = min(2*max(d.d, layerDwell), upDwellMax), now
				d.afterFail(now)
			case s.why == whyBudget && d.d > 0:
				d.d, d.at = min(d.d, upDwellSoft), now
				d.afterFail(now)
			}
			s.downAt, s.downOwner = now, s.owner
		} else if s.layer != "" && layerRank(st.Layer) > layerRank(s.layer) {
			s.upAt, s.upOwner = now, s.owner
		}
		s.paused, s.layer, s.tid = false, st.Layer, c.tid
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
	// Probe fuer den abgelehnten Schritt: nur, wenn sich die Auswahl seit probeQuiet nicht bewegt hat (nach einem Wechsel
	// bzw. zu Beginn des Beins sind Rate und GCC-Ziel noch in Bewegung), keine andere laeuft, die Zielrate ins
	// Kontingent passt und ein laufender Bild-Ausgang zum Fuellen da ist (der des Platzes selbst, sonst
	// der erste laufende in der Reihenfolge des Vorrangs)
	if moved {
		l.movedAt = now
	}
	if cand.s != nil && now-l.movedAt >= int64(probeQuiet) && l.pr.stage == 0 && cand.need*upHeadroom+audioCut <= quotaCap {
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
		if fs != nil {
			l.probeBegin(cand.s, fs, cand.rank, cand.need, sum, audioCut, now)
		}
	}
}

// keepActive behaelt Teilnehmende, die schon einen Platz haben und weiter
// zur ersten Auswahl gehoeren wuerden, vorne (gegen Platzwechsel).
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
	padLast    uint64  // Fuell-Bytes beim letzten Takt
	prOn       bool    // Probe laeuft: Stausignale nur beobachten (prSig), Schaetzung unveraendert
	prUntil    int64   // ... ebenso bis hierhin nach einer gescheiterten Probe (probeFloor)
	prReady    bool    // GCC-Ziel im Schutz weit genug ueber der Medienrate (wie ready)
	prTail     bool    // Nachlauf der Probe: ein fallendes Ziel zaehlt nicht (siehe probeStep)
	prSig      string  // Stausignal dieses Takts waehrend des Schutzes: gcc | over | queue
	prPeak     float64 // hoechstes GCC-Ziel seit Beginn der Probe
	grant      float64 // bestandene Probe: Deckel und Schaetzung mindestens so hoch, bis grantUntil
	grantUntil int64
}

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
			return false
		}
		e.lagAt, e.lagBase = now, e.rateAvg
		e.lagJump = rate > e.lagBase*lagJumpMin
		if v < e.gccLast*gccFall { // faellt, waehrend die Rate es ueberholt: Stau, bis zum Ende dieser Folge
			e.lagDead, e.deadAt = true, now
			return false
		}
		e.lagDead, e.lagPeak, e.lagMark, e.lagMarkAt = false, v, v, now
		return true
	}
	if e.lagDead {
		return false
	}
	if v > e.lagPeak {
		e.lagPeak = v
	}
	ok := true
	switch {
	case v < e.lagPeak*gccFall, now-e.lagAt >= int64(lagMax):
		ok = false
	case now-e.lagMarkAt >= int64(lagStep):
		ok = v >= e.lagMark*lagRise
		e.lagMark, e.lagMarkAt = v, now
	}
	if !ok {
		e.lagDead, e.deadAt, e.failAt, e.failFrom = true, now, now, e.lagAt
	}
	return ok
}

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
	// Fuelldaten einer Probe zaehlen zur Bein-Rate, aber nicht zur Medienrate, mit der hier gerechnet wird
	if pb := st.PadBytes.Load(); pb != e.padLast {
		rate = max(rate-float64(pb-e.padLast)*8/dt, 0)
		e.padLast = pb
	}
	age := time.Duration(st.TickAgeNs.Swap(0))
	drops := st.TickDrops.Swap(0)
	if e.prOn || e.prUntil != 0 && now < e.prUntil {
		// Probe bzw. kurz nach einer gescheiterten: Stausignale nur festhalten. Die Schaetzung bleibt, wie sie vor der
		// Probe war - nach oben geht in dieser Zeit nichts (ready).
		v := 0.0
		if l.Ext != nil {
			v = float64(l.Ext())
		}
		e.prSig = ""
		switch {
		case !e.prOn:
		case age > congAge || drops > 0:
			e.prSig = "queue"
		case v > 0 && v < e.prPeak*gccFall && !e.prTail:
			e.prSig = "gcc"
		case l.ExtOver != nil && l.ExtOver():
			e.prSig = "over"
		}
		if v > e.prPeak {
			e.prPeak = v
		}
		e.gccLast, e.lag, e.ready = v, false, false
		e.prReady = v <= 0 || v >= rate*gccReady
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
		if after && v > 0 && v < rate*0.9 {
			// Das Ziel liegt auch probeFloor nach der gescheiterten Probe noch unter der Rate: Stau wie frueher, ohne
			// Schonfrist (das Ziel ist im Schutz gefallen; dass es inzwischen wieder steigt, entschuldigt nichts)
			e.lagAt, e.lagDead, e.lagJump, e.deadAt = now, true, false, now
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
		}
		e.gccLast = v
	}
	if e.rateAvg == 0 {
		e.rateAvg = rate
	} else {
		e.rateAvg = 0.8*e.rateAvg + 0.2*rate
	}
	switch {
	case age > congAge || drops > 0 || gccLow > 0:
		if gccLow > 0 && gccLow < rate {
			rate = gccLow / 0.85
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
			if now-e.lastDown < int64(20*time.Second) {
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
		if l.Ext != nil && c < e.want && !(e.probing && e.gccLast > 0) {
			c = e.want
		}
		if now < e.grantUntil && c < e.grant { // bestandene Probe: bis der Schritt in der Rate sichtbar ist
			c = e.grant
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
