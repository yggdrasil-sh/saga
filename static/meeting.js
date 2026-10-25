// Saga 2.5 - Meeting-Ansicht fuer Anrufe (Spezifikation MEETING-SPEC.md, gilt fuer Web und Linux, das Anrufe im Browser
// oeffnet): reine Entscheidungen ohne DOM und ohne WebRTC, damit Node sie pruefen kann (tests/meeting_logik.js mit den
// gemeinsamen Testvektoren tests/meeting_vectors.json - dieselben Faelle pruefen iOS und Android). static/calls.js laedt
// die Datei (window.SagaMeeting) und zeichnet damit Buehne, Streifen und Galerie.
//
// - meetingTiles: Personen ueber `who` zusammenfassen (eine Person = eine Kachel), Bildschirm je teilendem Geraet eine
//   eigene Kachel ohne Kamera-Bild-im-Bild, eigene Begleitgeraete nie als Kachel, eigener Bildschirm nie auf der Buehne
// - pickLayout / arrange: Auto (Praesentation, 1:1, Galerie, Sprecher), Galerie, Sprecher, Praesentation; Anheften
// - galleryGrid / pages: Spalten und Seiten je Fensterklasse (§2.2), Video-Hoechstzahl (§8)
// - SpeakerPick: Sprechring (> 0,03 fuer >= 300 ms, Nachlauf 600 ms) und Buehnenwechsel mit Hysterese (>= 1,5 s am
//   Stueck lauter und >= 3 s seit dem letzten Wechsel; eigene Stimme bewegt die Buehne nie)
// - hideControls: Leisten nach 4 s ausblenden, mit Ausnahmen (§6.1)
// - selfPipSize: eigenes Bild im Seitenverhaeltnis der Kamera, S/M/L (§7)
(function (root) {
  'use strict';
  var K = {
    IDLE_MS: 4000,          // Leisten blenden nach 4 s ohne Bedienung aus
    LEVEL: 0.03,            // "spricht": Pegel ueber 0,03 ...
    SPEAK_MIN: 300,         // ... fuer mindestens 300 ms
    SPEAK_TAIL: 600,        // Nachlauf des Sprechrings
    LOUDER_FOR: 1500,       // Buehnenwechsel: neue Person 1,5 s am Stueck lauter ...
    MIN_GAP: 3000,          // ... und 3 s seit dem letzten Wechsel
    NOVIDEO_TAIL: 5000,     // ausgeblendete Person ohne Video: waehrend des Sprechens + 5 s als Kachel
    RECENT: 30000,          // Galerie-Reihenfolge: Sprecher der letzten 30 s
    MAX_TILES: 25, MIN_TILE_W: 112, MIN_TILE_H: 63
  };
  var LAYOUTS = ['auto', 'gallery', 'speaker', 'presentation'];

  function clamp(x, a, b) { return Math.max(a, Math.min(b, x)); }
  function str(x) { return x == null ? '' : String(x); }

  // Personen aus Teilnehmerliste + Medienzustand. roster: [{device, who, name, dev_name, role, me, avatar}],
  // media: {device: {mic, cam, screen, level, screenAt}}. Reihenfolge = erste Nennung in der Liste.
  function people(roster, media) {
    var by = {}, out = [];
    (roster || []).forEach(function (x) {
      if (!x || !x.device) return;
      var who = str(x.who || x.device), m = (media || {})[x.device] || {};
      var P = by[who];
      if (!P) { P = by[who] = { who: who, name: str(x.name), avatar: x.avatar || null, me: false, devices: [], mic: false, level: 0 }; out.push(P); }
      if (x.me) P.me = true;
      if (!P.name && x.name) P.name = str(x.name);
      if (!P.avatar && x.avatar) P.avatar = x.avatar;
      var d = { device: x.device, dev_name: str(x.dev_name), role: x.role === 'companion' ? 'companion' : 'member',
                cam: !!m.cam, screen: !!m.screen, mic: m.mic === true, level: +m.level || 0, screenAt: +m.screenAt || 0 };
      P.devices.push(d);
      if (d.mic) P.mic = true;
      P.level = Math.max(P.level, d.mic ? d.level : 0);
    });
    out.forEach(function (P) { P.main = P.devices.filter(function (d) { return d.role !== 'companion'; })[0] || P.devices[0]; });
    return out;
  }

  // Kacheln (§1). prefs: {showNoVideo (Standard an), spokeAt: {who: ms}, now: ms}
  //  {id: who,               kind: 'person', who, name, me, dev (Videoquelle bzw. Hauptgeraet), video, mic, level}
  //  {id: who|dev:cam,       kind: 'cam',    ... devName}  - Begleitgeraet mit eigener Kamera neben der Kamera des Hauptgeraets
  //  {id: who|dev:scr,       kind: 'screen', ... devName (nur bei mehreren Geraeten der Person), screenAt}
  // Reihenfolge: Bildschirme (aelteste zuerst), Personen in Listenfolge, die eigene Person zuletzt.
  function meetingTiles(roster, media, prefs) {
    prefs = prefs || {};
    var now = +prefs.now || 0, spoke = prefs.spokeAt || {}, show = prefs.showNoVideo !== false;
    var scr = [], per = [], mine = null;
    people(roster, media).forEach(function (P) {
      if (P.me) {        // eigene Person: nur "Du" - eigene Begleitgeraete und der eigene Bildschirm erscheinen nie (§1.5, §4.2)
        var self = P.devices.filter(function (d) { return d.device === prefs.self; })[0] || P.main;
        mine = { id: P.who, kind: 'person', who: P.who, name: P.name, avatar: P.avatar, me: true, dev: self.device, devName: '',
                 video: self.cam, mic: self.mic, level: self.mic ? self.level : 0 };
        return;
      }
      var many = P.devices.length > 1;
      var camDev = P.main.cam ? P.main : P.devices.filter(function (d) { return d.cam; })[0] || null;
      var t = { id: P.who, kind: 'person', who: P.who, name: P.name, avatar: P.avatar, me: false, dev: (camDev || P.main).device,
                devName: '', video: !!camDev, mic: P.mic, level: P.level };
      var quietOk = show || t.video || (spoke[P.who] != null && now - spoke[P.who] <= K.NOVIDEO_TAIL);
      if (quietOk) per.push(t);
      P.devices.forEach(function (d) {
        if (d.cam && camDev && d !== camDev)
          per.push({ id: P.who + '|' + d.device + ':cam', kind: 'cam', who: P.who, name: P.name, avatar: P.avatar, me: false, dev: d.device,
                     devName: d.dev_name, video: true, mic: d.mic, level: d.mic ? d.level : 0 });
        if (d.screen)
          scr.push({ id: P.who + '|' + d.device + ':scr', kind: 'screen', who: P.who, name: P.name, avatar: P.avatar, me: false, dev: d.device,
                     devName: many ? d.dev_name : '', video: true, mic: false, level: 0, screenAt: d.screenAt });
      });
    });
    scr.sort(function (a, b) { return a.screenAt - b.screenAt; });
    return scr.concat(per, mine ? [mine] : []);
  }
  // Andere Personen ohne Video, die meetingTiles ausgeblendet hat ("+ 3 ohne Video")
  function hiddenNoVideo(roster, media, prefs) {
    var shown = {}; meetingTiles(roster, media, prefs).forEach(function (t) { shown[t.who] = true; });
    return people(roster, media).filter(function (P) { return !P.me && !shown[P.who]; }).map(function (P) { return P.who; });
  }

  // Layout fuer die Einstellung choice ('auto' | 'gallery' | 'speaker' | 'presentation'). o.reduced = reduzierte Bewegung
  // (Auto bleibt bis 8 Kacheln Galerie). -> 'one' (Buehne + eigenes Bild schwebend) | 'gallery' | 'speaker' | 'presentation'
  function pickLayout(tiles, choice, o) {
    o = o || {};
    var screens = 0, n = 0;
    (tiles || []).forEach(function (t) { if (t.kind === 'screen') screens++; else n++; });
    if (LAYOUTS.indexOf(choice) < 0) choice = 'auto';
    if (choice === 'gallery') return n + screens <= 1 ? 'one' : 'gallery';
    if (choice === 'speaker') return n + screens <= 1 ? 'one' : 'speaker';
    if (choice === 'presentation') return screens ? 'presentation' : n <= 1 ? 'one' : 'speaker';
    if (screens) return 'presentation';
    if (n <= 2) return 'one';
    if (n <= 4 || (o.reduced && n <= 8)) return 'gallery';
    return 'speaker';
  }

  // Galerie-Reihenfolge (§2.2): Pin -> Bildschirme -> Personen mit Video -> Sprecher der letzten 30 s -> Rest alphabetisch;
  // die eigene Kachel zuletzt
  function galleryOrder(tiles, o) {
    o = o || {};
    var now = +o.now || 0, spoke = o.spokeAt || {};
    var rank = function (t) {
      if (o.pin && t.id === o.pin) return 0;
      if (t.me) return 9;
      if (t.kind === 'screen') return 1;
      if (t.video) return 2;
      if (spoke[t.who] != null && now - spoke[t.who] <= K.RECENT) return 3;
      return 4;
    };
    return tiles.map(function (t, i) { return { t: t, r: rank(t), i: i }; }).sort(function (a, b) {
      if (a.r !== b.r) return a.r - b.r;
      if (a.r === 4) { var x = a.t.name.toLowerCase(), y = b.t.name.toLowerCase(); if (x !== y) return x < y ? -1 : 1; }
      return a.i - b.i;
    }).map(function (x) { return x.t; });
  }

  // Verteilung auf Buehne, Streifen und Raster. o: {pin, speaker (who auf der Buehne laut SpeakerPick), spokeAt, now}
  // -> {layout, stage: id|null, strip: [id], grid: [id], float: eigenes Bild schwebend}
  function arrange(tiles, layout, o) {
    o = o || {};
    tiles = tiles || [];
    var ids = function (a) { return a.map(function (t) { return t.id; }); };
    var byId = {}; tiles.forEach(function (t) { byId[t.id] = t; });
    var pin = o.pin && byId[o.pin] ? byId[o.pin] : null;
    var self = tiles.filter(function (t) { return t.me; })[0] || null;
    var others = tiles.filter(function (t) { return !t.me; });
    var out = { layout: layout, stage: null, strip: [], grid: [], float: false };
    if (layout === 'gallery') { out.grid = ids(galleryOrder(tiles, o)); return out; }
    var stage = null;
    if (layout === 'presentation') {
      var screens = others.filter(function (t) { return t.kind === 'screen'; });
      stage = pin || screens.reduce(function (a, t) { return !a || t.screenAt >= a.screenAt ? t : a; }, null);
    } else if (layout === 'speaker' || layout === 'one') {
      var spk = o.speaker ? others.filter(function (t) { return t.kind === 'person' && t.who === o.speaker; })[0] : null;
      stage = pin || spk || others.filter(function (t) { return t.kind === 'person'; })[0] || others[0] || null;
    }
    out.stage = stage ? stage.id : null;
    if (layout === 'one') { out.float = !!self; out.strip = ids(others.filter(function (t) { return t !== stage; })); return out; }
    var rest = others.filter(function (t) { return t !== stage; });
    // Bildschirme vorn im Streifen (die neueste zuerst), dann Personen in Listenfolge, die eigene Kachel zuletzt
    var rs = rest.filter(function (t) { return t.kind === 'screen'; }).sort(function (a, b) { return b.screenAt - a.screenAt; });
    var rp = rest.filter(function (t) { return t.kind !== 'screen'; });
    var persons = tiles.filter(function (t) { return t.kind === 'person'; }).length;
    // Sprecher mit hoechstens zwei Personen: eigenes Bild schwebend; Praesentation: nie ueber dem Bildschirm (Streifen)
    out.float = !!self && layout === 'speaker' && persons <= 2;
    out.strip = ids(rs.concat(rp, self && !out.float ? [self] : []));
    return out;
  }

  // Fensterklasse (§8): 'phone' (< 600 breit), 'phone-land' (Hoehe < 500, quer), 'tablet' (600-899), 'desktop', 'large'
  function windowClass(w, h) {
    if (h && h < 500 && w > h) return 'phone-land';
    if (w < 600) return 'phone';
    if (w < 900) return 'tablet';
    if (w < 1920) return 'desktop';
    return 'large';
  }
  // Galerie (§2.2): Spalten fuer n Kacheln auf dieser Seite, Kacheln je Seite, davon mit Video
  function galleryGrid(n, w, h) {
    var cls = windowClass(w, h), cols, max, vids;
    if (cls === 'phone') { cols = n <= 2 ? 1 : 2; max = 6; vids = 4; }
    else if (cls === 'phone-land') { cols = n <= 2 ? 2 : 3; max = 6; vids = 4; }
    else if (w < 1024) { cols = n <= 4 ? 2 : 3; max = 9; vids = 6; }
    else if (w < 1920) { cols = n <= 4 ? 2 : n <= 9 ? 3 : 4; max = 16; vids = 9; }
    else { cols = n <= 4 ? 2 : n <= 9 ? 3 : n <= 16 ? 4 : 5; max = 25; vids = 16; }
    if (n <= 1) cols = 1;
    return { cols: Math.min(cols, Math.max(1, n)), perPage: max, video: vids, cls: cls };
  }
  // Seiten: Zahl der Seiten und die Kacheln einer Seite (Seite 0-basiert, wird begrenzt)
  function pages(n, perPage) { return Math.max(1, Math.ceil((n || 0) / Math.max(1, perPage))); }
  function pageOf(list, page, perPage) {
    var np = pages(list.length, perPage), p = clamp(page | 0, 0, np - 1);
    return { page: p, pages: np, items: list.slice(p * perPage, (p + 1) * perPage) };
  }
  // Videos gleichzeitig dekodiert (§8, wie Knoten-Last-N), dazu je ein Bildschirm
  function videoBudget(w, h) {
    var c = windowClass(w, h);
    return c === 'phone' || c === 'phone-land' ? 4 : c === 'tablet' ? 6 : c === 'desktop' ? 9 : 16;
  }
  // Welche Kacheln bekommen Video? Vorrang: Pin -> aktiver Sprecher -> zuletzt gesprochen -> Reihenfolge der Liste.
  // Bildschirme zaehlen nicht mit (+1 je Bildschirm). -> {id: true}
  function videoFor(order, max, o) {
    o = o || {};
    var spoke = o.spokeAt || {}, out = {}, n = 0;
    var cand = order.filter(function (t) { return t.video && t.kind !== 'screen'; });
    cand = cand.map(function (t, i) {
      var r = o.pin === t.id ? 0 : t.me ? 1 : o.speaker === t.who ? 2 : spoke[t.who] != null ? 3 : 4;
      return { t: t, r: r, s: -(spoke[t.who] || 0), i: i };
    }).sort(function (a, b) { return a.r - b.r || (a.r === 3 ? a.s - b.s : 0) || a.i - b.i; });
    cand.forEach(function (x) { if (n < max) { out[x.t.id] = true; n++; } });
    order.forEach(function (t) { if (t.kind === 'screen') out[t.id] = true; });
    return out;
  }
  // Streifen (§2.3/§2.4/§8): Lage und Breite bzw. Hoehe in px
  function stripSide(w, h, layout) {
    var c = windowClass(w, h);
    if (c === 'phone-land') return { side: 'right', size: 120 };
    if (c === 'phone') return { side: 'bottom', size: 84 };
    if (c === 'tablet') return { side: 'bottom', size: 96 };
    if (c === 'large') return { side: 'right', size: 240 };
    if (w < 1100) return { side: 'right', size: 160 };
    return { side: 'right', size: layout === 'presentation' && w < 1440 ? 160 : 200 };
  }

  // Sprechring und Buehnenwechsel mit Hysterese (§2.3). update(now, {who: pegel}, eigenesWho) -> who auf der Buehne
  function SpeakerPick() { this.cur = null; this.changed = -Infinity; this.cand = null; this.candSince = 0; this.over = {}; this.lastOn = {}; this.speaking = {}; }
  SpeakerPick.prototype.update = function (now, levels, me) {
    levels = levels || {};
    var self = this, all = {};
    Object.keys(levels).concat(Object.keys(this.over), Object.keys(this.lastOn)).forEach(function (w) { all[w] = true; });
    var sp = {};
    Object.keys(all).forEach(function (w) {
      var lv = +levels[w] || 0;
      if (lv > K.LEVEL) {
        if (self.over[w] == null) self.over[w] = now;
        if (now - self.over[w] >= K.SPEAK_MIN) self.lastOn[w] = now;
      } else delete self.over[w];
      if (self.lastOn[w] != null && now - self.lastOn[w] <= K.SPEAK_TAIL) sp[w] = true;
    });
    this.speaking = sp;
    var best = null;
    Object.keys(sp).forEach(function (w) { if (w !== me && (!best || (+levels[w] || 0) > (+levels[best] || 0))) best = w; });
    if (!this.cur) { if (best) { this.cur = best; this.changed = now; } return this.cur; }
    var louder = best && best !== this.cur && ((+levels[best] || 0) > (+levels[this.cur] || 0) || !sp[this.cur]) && (+levels[best] || 0) > 0;
    if (louder) {
      if (this.cand !== best) { this.cand = best; this.candSince = now; }
      if (now - this.candSince >= K.LOUDER_FOR && now - this.changed >= K.MIN_GAP) { this.cur = best; this.changed = now; this.cand = null; }
    } else if (!(best && best === this.cand && !(+levels[best] > 0))) { this.cand = null; }
    return this.cur;
  };
  // Person ist gegangen: nicht mehr auf der Buehne halten
  SpeakerPick.prototype.forget = function (who) {
    if (this.cur === who) { this.cur = null; this.changed = -Infinity; }
    if (this.cand === who) this.cand = null;
    delete this.over[who]; delete this.lastOn[who]; delete this.speaking[who];
  };
  SpeakerPick.prototype.spokeAt = function () { return Object.assign({}, this.lastOn); };

  // Leisten ausblenden? (§6.1) idle = 4 s ohne Bedienung, phase 'connected' (sonst Klingeln/Verbinden), video = Bild auf
  // der Buehne, screenReader, menuOpen (Menue/Blatt/Dialog), notice (wichtige Meldung), pointerOverBar (Maus auf Kopf/Leiste
  // bzw. Tastaturfokus darin)
  function hideControls(idle, phase, video, screenReader, menuOpen, notice, pointerOverBar) {
    return !!idle && phase === 'connected' && !!video && !screenReader && !menuOpen && !notice && !pointerOverBar;
  }

  // Eigenes Bild schwebend (§7): Breite 22 % der Buehne (quer, 120-280 px) bzw. 26 % (hoch, 90-160 px), Faktor S/M/L,
  // Seitenverhaeltnis der Kamera (0,5-2,0); passt es nicht zweimal uebereinander, verkleinern (bis zur Haelfte)
  var SIZES = { S: 0.7, M: 1, L: 1.4 };
  function selfPipSize(stageW, stageH, ar, size) {
    var land = stageW >= stageH;
    var w = land ? clamp(stageW * 0.22, 120, 280) : clamp(stageW * 0.26, 90, 160);
    w *= SIZES[size] || 1;
    ar = clamp(+ar || (land ? 16 / 9 : 9 / 16), 0.5, 2);
    var h = w / ar;
    if (stageH > 0 && 2 * h > stageH) {
      var w2 = Math.max(w / 2, (stageH / 2) * ar);
      w = Math.min(w, w2); h = w / ar;
    }
    return { w: Math.round(w), h: Math.round(h) };
  }
  // Gespeicherte Layout-Wahl lesen (localStorage-Werte, §2.6): saga-call-layout, sonst Uebernahme aus saga-horn-view
  function layoutPref(v, old) {
    if (LAYOUTS.indexOf(v) >= 0) return v;
    if (old === 'grid') return 'gallery';
    if (old === 'speaker') return 'speaker';
    return 'auto';
  }

  var api = { K: K, LAYOUTS: LAYOUTS, people: people, meetingTiles: meetingTiles, hiddenNoVideo: hiddenNoVideo, pickLayout: pickLayout,
              galleryOrder: galleryOrder, arrange: arrange, windowClass: windowClass, galleryGrid: galleryGrid, pages: pages, pageOf: pageOf,
              videoBudget: videoBudget, videoFor: videoFor, stripSide: stripSide, SpeakerPick: SpeakerPick, hideControls: hideControls,
              selfPipSize: selfPipSize, layoutPref: layoutPref };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SagaMeeting = api;
})(typeof self !== 'undefined' ? self : this);
