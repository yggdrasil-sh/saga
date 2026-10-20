// Saga 2.5 Gjallarhorn (Paket P5, Konzept v2 §1.2 H6/H7, §2.6): Entscheidungen des Web-Clients fuer Anrufe ueber Knoten -
// ohne DOM und ohne WebRTC, damit sie sich in Node pruefen lassen (tests/horn_logik.js). static/calls.js laedt die Datei
// bei Bedarf (window.SagaHorn) und fuehrt Messung, Beine, Live-Umschalten und Kachelansicht damit aus.
//
// - Punktzahl je Kandidat (kleiner = besser): rtt_p50 + 2*jitter + Lastabzug + (UDP fehlt ? 15 : 0) - (Anruf schon hier ? 25 : 0)
// - Umschalter mit Hysterese: Verbindung weg / Knoten leert sich -> sofort; anderer Knoten besser -> nur wenn >= 25 % UND
//   >= 30 ms besser, 20 s am Stueck, Mindestverweildauer 60 s, hoechstens 3 Wechsel je 10 min; UDP-Verlust > 20 % fuer 3 s
//   -> TCP am selben Knoten; UDP wieder ok fuer 30 s -> zurueck, hoechstens 1x je 5 min
// - Kachelansicht bis 25: Spalten je Breite, Last-N (Desktop 9, Handy 4 bewegt, die uebrigen als Standbild mit Sprechring)
// - Qualitaetspunkt aus RTT, Verlust und Einfrieren; Anzeige des Wegs UDP / TCP / TLS
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
  function pathName(p) { return p === 'udp' ? 'UDP' : p === 'turn' ? 'TLS' : p === 'tcp' ? 'TCP' : ''; }

  var api = { K: K, median: median, jitter: jitter, loadMs: loadMs, score: score, rank: rank, better: better, Switcher: Switcher,
              closeOld: closeOld, cols: cols, tiles: tiles, lastN: lastN, quality: quality, pathName: pathName };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SagaHorn = api;
})(typeof self !== 'undefined' ? self : this);
