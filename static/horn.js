// Saga 2.5 Gjallarhorn (Paket P5, Konzept v2 §1.2 H6/H7, §2.6): Entscheidungen des Web-Clients fuer Anrufe ueber Knoten -
// ohne DOM und ohne WebRTC, damit sie sich in Node pruefen lassen (tests/horn_logik.js). static/calls.js laedt die Datei
// bei Bedarf (window.SagaHorn) und fuehrt Messung, Beine, Live-Umschalten und Kachelansicht damit aus.
//
// - Punktzahl je Kandidat (kleiner = besser): rtt_p50 + 2*jitter + Lastabzug + (UDP fehlt ? 15 : 0) - (Anruf schon hier ? 25 : 0)
// - Umschalter mit Hysterese: Verbindung weg / Knoten leert sich -> sofort; anderer Knoten besser -> nur wenn >= 25 % UND
//   >= 30 ms besser, 20 s am Stueck, Mindestverweildauer 60 s, hoechstens 3 Wechsel je 10 min; UDP-Verlust > 20 % fuer 3 s
//   -> TCP am selben Knoten; UDP wieder ok fuer 30 s -> zurueck, hoechstens 1x je 5 min
// - Kachelansicht bis 25: Spalten je Breite, Last-N (Desktop 9, Handy 4 bewegt, die uebrigen als Standbild mit Sprechring)
// - Qualitaetspunkt (Empfangsqualitaet) aus RTT, Verlust und Einfrieren ueber 30 s mit Hysterese; Anzeige des Wegs UDP / TCP / TLS
(function (root) {
  'use strict';
  var K = {
    PROBE_MS: 800,          // Messung beim Beitritt hoechstens so lange (parallel fuer alle Kandidaten)
    PINGS: 5,               // WSS-Pings je Kandidat
    NO_UDP_MS: 15,          // Abzug, wenn der Kandidat (oder das Geraet) kein UDP hat
    HERE_MS: 25,            // Bonus "Anruf laeuft schon auf diesem Knoten"
    LOAD_FROM: 0.7,         // ab 70 % Auslastung Abzug ...
    LOAD_MS: 200,           // ... (load - 0,7) * 200 ms, also bis 40 ms bei 90 %
    FULL: 0.9,              // ab 90 % voll: nur, wenn nichts anderes geht
    FULL_MS: 1000,
    BETTER_REL: 0.25,       // anderer Knoten besser: mindestens 25 % ...
    BETTER_MS: 30,          // ... und mindestens 30 ms
    BETTER_FOR: 20000,      // ... fuer 20 s am Stueck
    DWELL: 60000,           // Mindestverweildauer auf einem Knoten
    WINDOW: 600000,         // Fenster fuer die Wechselgrenze
    MAX_SWITCH: 3,          // hoechstens 3 Wechsel (wegen "besser") je 10 min
    LOST_MS: 2000,          // ICE disconnected laenger als 2 s = Verbindung weg
    UDP_LOSS: 0.2,          // UDP-Verlust ueber 20 % ...
    UDP_LOSS_FOR: 3000,     // ... fuer 3 s -> TCP am selben Knoten
    UDP_BACK_FOR: 30000,    // UDP wieder ok fuer 30 s -> zurueck
    UDP_BACK_EVERY: 300000, // hoechstens 1x je 5 min
    OVERLAP_MIN: 2000,      // make-before-break: altes Bein nach 2 s schliessen ...
    OVERLAP_MAX: 15000,     // ... solange dort noch andere sind, die noch nicht gewechselt haben, hoechstens 15 s
    LASTN_DESKTOP: 9, LASTN_MOBILE: 4, MAX_TILES: 25
  };

  function median(a) {
    var s = a.slice().sort(function (x, y) { return x - y; }), n = s.length;
    return !n ? NaN : n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
  }
  // Jitter = mittlere Abweichung aufeinanderfolgender RTTs (wie RFC 3550 ohne Glaettung)
  function jitter(a) {
    if (a.length < 2) return 0;
    var s = 0; for (var i = 1; i < a.length; i++) s += Math.abs(a[i] - a[i - 1]);
    return s / (a.length - 1);
  }
  function loadMs(load) {
    load = +load || 0;
    if (load >= K.FULL) return K.FULL_MS;
    return load > K.LOAD_FROM ? (load - K.LOAD_FROM) * K.LOAD_MS : 0;
  }
  // m = {ok, rtts:[ms], tls:ms, udp:ms|null}; cand = {here, udp, load}
  function score(m, cand, devUdp) {
    if (!m || !m.ok || !m.rtts || !m.rtts.length) return Infinity;
    var r = m.rtts, s = median(r) + 2 * jitter(r) + loadMs(cand && cand.load);
    var udp = !!(cand && cand.udp && devUdp !== false && m.udp != null);
    if (!udp) s += K.NO_UDP_MS;
    if (cand && cand.here) s -= K.HERE_MS;
    return Math.round(s * 10) / 10;
  }
  // Kandidaten nach Punktzahl (unerreichbare zuletzt, bei Gleichstand die Reihenfolge des Servers)
  function rank(cands, meas, devUdp) {
    return cands.map(function (c, i) {
      var m = meas[c.node];
      return { cand: c, m: m || null, score: score(m, c, devUdp), i: i };
    }).sort(function (a, b) { return a.score === b.score ? a.i - b.i : a.score - b.score; });
  }
  function better(cur, other) {
    if (!isFinite(other)) return false;
    if (!isFinite(cur)) return true;
    return cur - other >= K.BETTER_MS && other <= cur * (1 - K.BETTER_REL);
  }

  // Umschalter mit Hysterese. now in ms. Zustand nur hier; der Aufrufer fuehrt den Wechsel aus und meldet ihn mit moved().
  function Switcher(o) {
    o = o || {};
    this.node = o.node || null; this.path = o.path || '';
    this.since = o.now || 0;          // auf diesem Knoten seit
    this.moves = [];                  // Zeitpunkte der Wechsel wegen "besser"
    this.cand = null; this.candSince = 0;
    this.lossSince = 0; this.udpOkSince = 0; this.udpBackAt = -Infinity;
    this.discSince = 0;
  }
  Switcher.prototype.moved = function (now, node, path, reason) {
    if (reason === 'better') this.moves.push(now);
    if (node !== this.node) this.since = now;
    this.node = node; this.path = path || this.path;
    this.cand = null; this.candSince = 0; this.lossSince = 0; this.discSince = 0;
    if (reason === 'udp') this.udpBackAt = now;
    if (path === 'udp') this.udpOkSince = 0;
  };
  Switcher.prototype.budget = function (now) {
    var w = now - K.WINDOW;
    this.moves = this.moves.filter(function (t) { return t > w; });
    return K.MAX_SWITCH - this.moves.length;
  };
  // Verbindung: state = schlechtester Zustand der Beine ('connected' | 'disconnected' | 'failed' | 'closed' | ...)
  Switcher.prototype.conn = function (now, state) {
    if (state === 'failed' || state === 'closed') return { act: 'switch', why: 'lost' };
    if (state === 'disconnected') {
      if (!this.discSince) this.discSince = now;
      if (now - this.discSince > K.LOST_MS) return { act: 'switch', why: 'lost' };
    } else this.discSince = 0;
    return null;
  };
  // Knoten meldet Leeren bzw. wurde entfernt
  Switcher.prototype.draining = function () { return { act: 'switch', why: 'draining' }; };
  // Laufende Beobachtung: cur = Punktzahl des aktuellen Knotens, others = [{node, score}]
  Switcher.prototype.observe = function (now, cur, others) {
    var best = null;
    (others || []).forEach(function (x) { if (x.node !== this.node && better(cur, x.score) && (!best || x.score < best.score)) best = x; }, this);
    if (!best) { this.cand = null; this.candSince = 0; return null; }
    if (!this.cand || this.cand !== best.node) { this.cand = best.node; this.candSince = now; return null; }
    if (now - this.candSince < K.BETTER_FOR) return null;
    if (now - this.since < K.DWELL) return null;
    if (this.budget(now) <= 0) return null;
    return { act: 'switch', why: 'better', to: best.node };
  };
  // UDP-Weg: Verlust (0..1) aus getStats; ok = UDP-Probe gelingt (fuer die Rueckkehr)
  Switcher.prototype.udp = function (now, loss, ok) {
    if (this.path === 'udp') {
      if (loss > K.UDP_LOSS) {
        if (!this.lossSince) this.lossSince = now;
        if (now - this.lossSince >= K.UDP_LOSS_FOR) return { act: 'path', why: 'udp-loss', path: 'tcp' };
      } else this.lossSince = 0;
      return null;
    }
    if (!ok) { this.udpOkSince = 0; return null; }
    if (!this.udpOkSince) this.udpOkSince = now;
    if (now - this.udpOkSince >= K.UDP_BACK_FOR && now - this.udpBackAt >= K.UDP_BACK_EVERY) return { act: 'path', why: 'udp', path: 'udp' };
    return null;
  };

  // Altes Bein beim Wechsel schliessen? up = Zeitpunkt, an dem das neue Bein Rahmen bekam; waiting = Zahl der anderen, die
  // noch nur ueber den alten Knoten zu erreichen sind
  function closeOld(now, up, waiting) {
    if (!up) return false;
    var d = now - up;
    return d >= K.OVERLAP_MAX || (d >= K.OVERLAP_MIN && !waiting);
  }

  // Kachelansicht: Spalten fuer n Kacheln bei Breite w (px). Handy: 2 Spalten (scrollbar ab 9).
  function cols(n, w) {
    if (w && w <= 640) return n <= 1 ? 1 : 2;
    return n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : n <= 16 ? 4 : 5;
  }
  // Kacheln: devs = andere Geraete (Reihenfolge der Teilnehmerliste), moving = {dev: true} (hat gerade einen Bildplatz am
  // Knoten), speaker = Geraet des Sprecherplatzes. -> [{dev, moving, big}], hoechstens MAX_TILES (Rest: more)
  // Sprecheransicht (view 'speaker'): der Sprecher zuerst und gross, die anderen danach.
  function tiles(devs, moving, speaker, view) {
    var list = devs.slice(0, K.MAX_TILES).map(function (d) { return { dev: d, moving: !!moving[d], big: false }; });
    if (view === 'speaker' && list.length) {
      var i = -1; list.forEach(function (t, j) { if (t.dev === speaker) i = j; });
      if (i < 0) i = 0;
      var s = list.splice(i, 1)[0]; s.big = true; list.unshift(s);
    }
    return { list: list, more: Math.max(0, devs.length - K.MAX_TILES) };
  }
  function lastN(mobile) { return mobile ? K.LASTN_MOBILE : K.LASTN_DESKTOP; }

  // Qualitaetspunkt: q = {rtt ms, loss 0..1, freezes je Minute}
  function quality(q) {
    if (!q) return '';
    var rtt = +q.rtt || 0, loss = +q.loss || 0, fr = +q.freezes || 0;
    if (rtt > 400 || loss > 0.08 || fr > 3) return 'bad';
    if (rtt > 200 || loss > 0.02 || fr > 1) return 'mid';
    return 'good';
  }
  // Empfangsqualitaet ueber ein gleitendes Fenster (Analyse 1.11.: das 3-s-Fenster machte aus einem einzigen Freeze 20/min
  // und damit sofort Rot). add(now, m) je Messung (alle ~3 s) mit den Zuwaechsen seit der letzten: m = {rtt ms|null,
  // lost, recv (Pakete), fz (Freezes), dt (ms)}. Bewertet werden Verlust und Freezes je Minute ueber die letzten WIN ms
  // (bzw. die bisherige Anrufdauer, wenn kuerzer) und der Median der RTT im Fenster - Schwellen wie quality().
  // Hysterese: die Anzeige wechselt erst, wenn zwei Messungen in Folge auf derselben Seite liegen (schlechter: auf die
  // bessere der beiden, besser: auf die schlechtere der beiden) - so ist Gelb auf dem Weg nach oben und unten erreichbar.
  var SEV = { good: 0, mid: 1, bad: 2 }, LV = ['good', 'mid', 'bad'];
  function hyst(shown, a, b) {
    var s = SEV[shown] || 0, x = SEV[a], y = SEV[b];
    if (x === undefined || y === undefined) return shown || 'good';
    if (x > s && y > s) return LV[Math.min(x, y)];
    if (x < s && y < s) return LV[Math.max(x, y)];
    return shown || 'good';
  }
  function QualityWin(win) { this.win = win || 30000; this.e = []; this.raw = []; this.q = 'good'; this.net = null; }
  QualityWin.prototype.add = function (now, m) {
    m = m || {};
    var dt = Math.max(0, +m.dt || 0);
    if (dt > 0) this.e.push({ t: now, dt: dt, rtt: m.rtt == null ? null : +m.rtt, lost: Math.max(0, +m.lost || 0), recv: Math.max(0, +m.recv || 0), fz: Math.max(0, +m.fz || 0) });
    var self = this;
    // Messung zaehlt, solange sie (ganz) im Fenster liegt
    this.e = this.e.filter(function (x) { return now - (x.t - x.dt) <= self.win + 500; });
    var lost = 0, recv = 0, fz = 0, span = 0, rtts = [];
    this.e.forEach(function (x) { lost += x.lost; recv += x.recv; fz += x.fz; span += x.dt; if (x.rtt != null && isFinite(x.rtt)) rtts.push(x.rtt); });
    if (!this.e.length) return this.q;
    var mins = Math.max(1000, Math.min(this.win, span)) / 60000;
    this.net = { rtt: rtts.length ? Math.round(median(rtts)) : null, loss: lost + recv > 0 ? Math.round(lost / (lost + recv) * 1000) / 1000 : 0,
                 freezes: Math.round(fz / mins * 10) / 10, win: Math.round(Math.min(this.win, span) / 1000) };
    var r = quality(this.net);
    this.raw.push(r); if (this.raw.length > 2) this.raw.shift();
    if (this.raw.length === 2) this.q = hyst(this.q, this.raw[0], this.raw[1]);
    return this.q;
  };
  function pathName(p) { return p === 'udp' ? 'UDP' : p === 'turn' ? 'TLS' : p === 'tcp' ? 'TCP' : ''; }

  // --- Senderate auf TCP/TLS (A1, Labor 2.11.) ------------------------------------------------------------------------------
  // Ueber TCP/TLS sieht WebRTC keinen Verlust: Neuuebertragungen kommen als Schuebe, und ein zu grosser Sendestrom staut sich
  // in der TCP-Sendewarteschlange (RTT 5-12 s beim Bildschirmteilen). Darum hier harte Obergrenzen je Weg und eine Stufe
  // 0..3, die aus der RTT (Abstand zum Grundwert = kleinste RTT der letzten 2 min) mit Hysterese folgt. UDP bleibt bei GCC
  // (Stufe 0, alte Regeln). Werte in kbit/s; mH/screenH = hoechste Bildhoehe (kurze Seite) der Schicht m bzw. des Bildschirms.
  K.TX = {
    tcp: { l: 150, m: 600, mH: 720, screen: 900, screenFps: 8, screenH: 1080 },
    turn: { l: 120, m: 450, mH: 540, screen: 600, screenFps: 5, screenH: 720 }
  };
  K.TX_F = [1, 0.6, 0.4, 0.25];       // Faktor je Stufe auf m und Bildschirm
  K.TX_FPS = [0, 5, 3, 2];            // Bildschirm hoechstens so viele Bilder/s je Stufe (0 = Wert des Wegs)
  K.RTT_ABS = 120;                    // Stau: RTT mindestens 120 ms ...
  K.RTT_REL = 0.5;                    // ... und 50 % ueber dem Grundwert
  K.RTT_HUGE = 1000;                  // ab 1 s ueber der kleinsten sofort (ohne zweite Messung)
  K.RTT_OK = 60;                      // frei: hoechstens 60 ms ueber der kleinsten
  K.TX_BAD_N = 2;                     // Stau in 2 Messungen in Folge (Messung alle ~3 s)
  K.TX_HOLD = 6000;                   // zwischen zwei Stufen nach unten mindestens 6 s (die Warteschlange muss sich leeren)
  K.TX_UP = 15000;                    // frei fuer 15 s -> eine Stufe zurueck ...
  K.TX_UP_MAX = 120000;               // ... nach erneutem Stau kurz danach doppelt so lange, hoechstens 2 min
  K.TX_RETRY = 30000;                 // Stau innerhalb von 30 s nach einem Schritt nach oben = Fehlversuch
  K.TX_BASE_WIN = 120000;             // Grundwert = kleinste RTT der letzten 2 min ...
  K.TX_BASE_BUCKET = 10000;           // ... gehalten in Eimern zu 10 s (wie die Apps, Pruefung A1)
  // Der Grundwert ist nicht die kleinste RTT seit Beginn: nach einem dauerhaften Routenwechsel (z. B. 20 -> 180 ms ohne Stau)
  // bliebe sonst Stufe 3 fuer immer bzw. im Band 60..120 ms ueber dem alten Grundwert jede Stufe haengen. Haelt ein echter
  // Stau laenger als das Fenster an, steigt der Grundwert mit; der Regler probiert dann eine Stufe hoeher (Stau -> wieder
  // runter, mit verdoppelter Wartezeit) - ein langsames Nachfassen, kein Pendeln im Messtakt.
  function TxGov(path) {
    this.path = path || ''; this.level = 0; this.base = Infinity; this.bad = 0; this.okSince = 0;
    this.downAt = -Infinity; this.upAt = -Infinity; this.upWait = K.TX_UP; this.last = null;
    this.mins = [];                   // [Eimer-Beginn, kleinste RTT im Eimer], aelteste zuerst
  }
  TxGov.prototype.baseAt = function (now, rtt) {
    var b = now - (((now % K.TX_BASE_BUCKET) + K.TX_BASE_BUCKET) % K.TX_BASE_BUCKET), m = this.mins, last = m[m.length - 1], i, v;
    if (last && last[0] === b) last[1] = Math.min(last[1], rtt); else m.push([b, rtt]);
    // ein Eimer faellt weg, wenn er ganz aelter als das Fenster ist
    while (m.length > 1 && m[0][0] + K.TX_BASE_BUCKET <= now - K.TX_BASE_WIN) m.shift();
    for (v = Infinity, i = 0; i < m.length; i++) if (m[i][1] < v) v = m[i][1];
    return v;
  };
  // Eine Messung (rtt in ms, null = keine). -> {from, to, rtt, base} bei Wechsel der Stufe, sonst null
  TxGov.prototype.add = function (now, rtt) {
    if (this.path === 'udp' || rtt == null || !isFinite(rtt) || rtt <= 0) return null;
    this.base = this.baseAt(now, rtt);
    var q = rtt - this.base, from = this.level;
    this.last = { rtt: Math.round(rtt), base: Math.round(this.base) };
    if (q >= K.RTT_HUGE || (q >= K.RTT_ABS && q >= this.base * K.RTT_REL)) {
      this.bad++; this.okSince = 0;
      if (this.level < 3 && now - this.downAt >= K.TX_HOLD && (this.bad >= K.TX_BAD_N || q >= K.RTT_HUGE)) {
        if (now - this.upAt < K.TX_RETRY) this.upWait = Math.min(K.TX_UP_MAX, this.upWait * 2);
        this.level++; this.downAt = now; this.bad = 0;
      }
    } else if (q <= K.RTT_OK) {
      this.bad = 0;
      if (!this.okSince) this.okSince = now;
      if (now - this.upAt >= 4 * K.TX_UP_MAX && now - this.downAt >= 4 * K.TX_UP_MAX) this.upWait = K.TX_UP;   // lange ruhig
      if (this.level > 0 && now - this.okSince >= this.upWait && now - this.downAt >= this.upWait) {
        this.level--; this.upAt = now; this.okSince = now;
      }
    } else { this.bad = 0; this.okSince = 0; }
    return this.level !== from ? { from: from, to: this.level, rtt: this.last.rtt, base: this.last.base } : null;
  };
  function r2(x) { return Math.round(x * 100) / 100; }
  // kurze Seite aus Hoehe und Breite (0/fehlend = unbekannt; ohne Breite zaehlt die Hoehe)
  function shortSide(h, w) { h = +h || 0; w = +w || 0; return h > 0 && w > 0 ? Math.min(h, w) : h; }
  // Sende-Parameter fuer die Bild-Leitung eines Knotenbeins. o = {others (andere am Knoten), share (ich teile), h/w (Hoehe/Breite
  // der Kamera), sh/sw (Hoehe/Breite des Bildschirms), rids ([l, m(, h)])}; die Deckel gelten fuer die kurze Seite (Hochkant
  // zaehlt die Breite, wie die Apps) -> {cam: {rid: {active, maxBitrate?, scaleResolutionDownBy?}},
  // screen: {maxBitrate, maxFramerate, scaleResolutionDownBy} | null}. Werte in bit/s.
  // UDP: wie bisher (Bughunt P03): allein nichts, zu zweit ohne l, Raten aus dem Angebot.
  // TCP/TLS: l bleibt auch zu zweit an - nur so kann der Sender bei knapper Rate auf l ausweichen (der Browser pausiert dann
  // m) und der Knoten l weiterreichen; m gedeckelt und ab Stufe 2 (bzw. beim Teilen) aus; Bildschirm gedeckelt.
  function txPlan(path, level, o) {
    o = o || {};
    var others = +o.others || 0, rids = o.rids || ['l', 'm'], cam = {}, i;
    level = Math.max(0, Math.min(3, +level || 0));
    var c = K.TX[path];
    if (!c) {
      for (i = 0; i < rids.length; i++) cam[rids[i]] = { active: others > 0 && !(others === 1 && rids.length > 1 && rids[i] === 'l') };
      return { cam: cam, screen: null };
    }
    var f = K.TX_F[level], ch = shortSide(o.h, o.w), sh = shortSide(o.sh, o.sw);
    for (i = 0; i < rids.length; i++) {
      var r = rids[i];
      if (r === 'l') cam.l = { active: others > 0, maxBitrate: Math.round(c.l * (level >= 3 ? 0.7 : 1)) * 1000 };
      else if (r === 'm') {
        cam.m = { active: others > 0 && level < 2 && !o.share, maxBitrate: Math.round(c.m * f) * 1000,
                  scaleResolutionDownBy: ch > c.mH ? r2(ch / c.mH) : 1 };
      } else cam[r] = { active: false };              // h gibt es auf TCP nicht
    }
    var fps = K.TX_FPS[level] ? Math.min(c.screenFps, K.TX_FPS[level]) : c.screenFps;
    var sc = sh > c.screenH ? sh / c.screenH : 1;
    if (level >= 3) sc *= 1.5;
    return { cam: cam, screen: { maxBitrate: Math.round(c.screen * f) * 1000, maxFramerate: fps, scaleResolutionDownBy: r2(Math.max(1, sc)) } };
  }

  var api = { K: K, median: median, jitter: jitter, loadMs: loadMs, score: score, rank: rank, better: better, Switcher: Switcher,
              closeOld: closeOld, cols: cols, tiles: tiles, lastN: lastN, quality: quality, hyst: hyst, QualityWin: QualityWin, pathName: pathName,
              TxGov: TxGov, txPlan: txPlan };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SagaHorn = api;
})(typeof self !== 'undefined' ? self : this);
