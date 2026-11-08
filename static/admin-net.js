// Admin -> Netz (Saga 2.5, Bug-DB 1641): Netz-Karte auf Canvas. Dieser Server in der Mitte, verbundene Server rechts,
// Gjallarhorn-Knoten oben, Clients (Gruppen je Betriebssystem/App, nur Anzahl) links. Kraefte: Barnes-Hut aus
// static/graph-sim.js (wie der Seiten-Graph) plus ein weicher Zug zum Platz der jeweiligen Art - so bleibt das Bild ruhig.
// Live: Server-Sent Events GET /api/net/events (neuer Stand nur bei Aenderung); ohne EventSource bzw. nach wiederholtem
// Abbruch alle 5 s GET /api/net. Animation (Lichtpunkte laufender Anrufe, Pulse bei Foederations-Nachrichten, weiches
// Ein-/Ausblenden) nur bei sichtbarem Tab und ohne prefers-reduced-motion - sonst nur Zustaende. Die Tabellen der Seite
// sind die barrierefreie Ansicht; sie werden bei jedem Stand mit erneuert. Daten nur per textContent (kein innerHTML).
(function () {
  'use strict';
  var canvas = document.getElementById('net-canvas');
  if (!canvas || !canvas.getContext) return;
  var L = {}, DATA = null;
  try { L = JSON.parse(document.getElementById('net-i18n').textContent || '{}'); } catch (e) { L = {}; }
  try { DATA = JSON.parse(document.getElementById('net-data').textContent || 'null'); } catch (e) { DATA = null; }
  if (!DATA) return;
  var F = window.SagaForce;
  var RM = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  function fill(s, p) { return String(s || '').replace(/\{(\w+)\}/g, function (m, k) { return p && k in p ? p[k] : m; }); }
  function now() { return window.performance ? performance.now() : Date.now(); }
  function lbl(group, key) { return (L[group] && L[group][key]) || key; }

  // Linien-Symbole im 24er-Raster (wie static/icons.js: Strich, runde Enden) - auf Canvas per Path2D, in der Legende als SVG
  var SYM = {
    server: 'M5 4h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM5 14h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM8 7h.01M8 17h.01',
    peer: 'M21 12a9 9 0 1 1-18 0a9 9 0 1 1 18 0zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
    horn: 'M3 10v4h4l7 5V5l-7 5zM17 9a4 4 0 0 1 0 6M19.5 6.5a8 8 0 0 1 0 11',
    phone: 'M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2',
    desktop: 'M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM9 20h6M12 16v4',
    browser: 'M4 5h16a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM3 9h18M6.5 7h.01M9 7h.01',
    client: 'M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2'
  };
  var P2D = {};
  function path(k) { if (!P2D[k] && window.Path2D) P2D[k] = new Path2D(SYM[k] || SYM.client); return P2D[k]; }
  (function legend() {
    var NS = 'http://www.w3.org/2000/svg';
    Array.prototype.forEach.call(document.querySelectorAll('.net-sym[data-sym]'), function (el) {
      var s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
      var p = document.createElementNS(NS, 'path'); p.setAttribute('d', SYM[el.dataset.sym] || SYM.client); s.appendChild(p);
      el.appendChild(s);
    });
  })();
  function clsSym(cls) {
    if (/^web-/.test(cls)) return 'browser';
    if (cls === 'ios-app' || cls === 'android-app') return 'phone';
    if (cls === 'other') return 'client';
    return 'desktop';
  }

  // Farben aus tokens.css (hell/dunkel); bei Wechsel des Farbschemas neu
  var PAL = null;
  function palette() {
    var s = getComputedStyle(document.body);
    function v(n, fb) { return s.getPropertyValue(n).trim() || fb; }
    PAL = { fg: v('--fg', '#ddd'), muted: v('--muted', '#888'), bg: v('--card', '#111'), border: v('--border', '#333'),
      ok: v('--ok', '#3fb950'), slow: v('--warn', '#d29922'), err: v('--down', '#f85149'), off: v('--unk', '#5a6577'),
      self: v('--accent', '#c9a45c'), peer: v('--info', '#58a6ff'), horn: v('--gr-db', '#b39ddb'), client: v('--gr-row', '#7fb0e0'),
      line: v('--gr-line', '#8b97a8'), dot: v('--net-call', '#d6409f') };
    PAL.pending = PAL.off;
  }
  palette();
  if (window.matchMedia) {
    var mq = matchMedia('(prefers-color-scheme: dark)');
    if (mq.addEventListener) mq.addEventListener('change', function () { palette(); view.draw(); });
  }

  // --- Modell ---------------------------------------------------------------------------------------------------------
  // Knoten: {id, kind, label, sub, state, sym, r, x, y, vx, vy, a (Deckkraft), ta (Ziel), d (Rohdaten)}
  function View() {
    this.c = canvas; this.ctx = canvas.getContext('2d'); this.nodes = []; this.byId = {}; this.edges = [];
    this.calls = []; this.pulses = []; this.hover = null; this.sel = null; this.raf = 0; this.alpha = 1; this.t = now();
    this.w = 0; this.h = 0; this.k = 1; this.cx = 0; this.cy = 0; this.act = null; this.frames = 0;
  }
  var view = new View();
  View.prototype.resize = function () {
    var r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width || !r.height) return;
    this.w = r.width; this.h = r.height; this.dpr = dpr;
    this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr);
    this.fit(true);
    this.draw();
  };
  // Platz je Art: Winkel und Abstand von der Mitte (Clients links, Server rechts, Knoten oben)
  function home(n, i, cnt) {
    var spread = function (base, width) { return cnt > 1 ? base - width / 2 + width * (i / (cnt - 1)) : base; };
    if (n.kind === 'self') return [0, 0];
    var ang, rad;
    if (n.kind === 'peer') { ang = spread(0, Math.min(2.2, 0.55 * cnt)); rad = 230; }
    else if (n.kind === 'horn') { ang = spread(-Math.PI / 2, Math.min(1.6, 0.5 * cnt)); rad = 175; }
    else if (n.kind === 'remote') { ang = spread(-0.55, Math.min(1.2, 0.4 * cnt)); rad = 330; }
    else { ang = spread(Math.PI, Math.min(2.4, 0.5 * cnt)); rad = 220; }
    return [Math.cos(ang) * rad, Math.sin(ang) * rad];
  }
  View.prototype.set = function (d) {
    var self = this, seen = {}, list = [];
    function put(o) {
      var n = self.byId[o.id];
      if (!n) {
        n = o; n.a = 0; n.vx = 0; n.vy = 0; n.x = null;
        self.byId[o.id] = n; self.nodes.push(n);
      } else { for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) n[k] = o[k]; }
      n.ta = 1; n.gone = false; seen[o.id] = true; list.push(n);
      return n;
    }
    put({ id: 'self', kind: 'self', label: d.self.name, sub: d.self.host, state: 'ok', sym: 'server', r: 30, d: d.self });
    d.peers.forEach(function (p) { put({ id: p.id, kind: 'peer', label: p.name, sub: p.host, state: p.state, sym: 'peer', r: 22, d: p }); });
    d.horns.forEach(function (h) {
      put({ id: h.id, kind: h.kind === 'remote' ? 'remote' : 'horn', label: h.name, sub: h.kind === 'remote' ? (h['from'] || '') : (h.version || ''),
        state: h.state, sym: 'horn', r: h.kind === 'remote' ? 16 : 20, d: h, badge: h.peers ? String(h.peers) : '' });
    });
    d.clients.forEach(function (c) {
      put({ id: c.id, kind: 'client', label: lbl('cls', c.cls), sub: '', state: 'ok', sym: clsSym(c.cls),
        r: Math.min(28, 14 + Math.sqrt(c.n) * 3), d: c, badge: String(c.n) });
    });
    this.nodes.forEach(function (n) { if (!seen[n.id]) { n.ta = 0; n.gone = true; } });
    // Plaetze je Art (Reihenfolge stabil) und Startpunkt neuer Knoten an ihrem Platz
    var groups = {};
    list.forEach(function (n) { (groups[n.kind] = groups[n.kind] || []).push(n); });
    Object.keys(groups).forEach(function (k) {
      groups[k].forEach(function (n, i) {
        var hp = home(n, i, groups[k].length); n.hx = hp[0]; n.hy = hp[1];
        if (n.x == null) { n.x = hp[0] * 0.9 + (Math.random() - 0.5) * 10; n.y = hp[1] * 0.9 + (Math.random() - 0.5) * 10; }
      });
    });
    this.edges = d.edges.map(function (e) { return { s: self.byId[e.a], t: self.byId[e.b], kind: e.kind, state: e.state, rtt: e.rtt }; })
      .filter(function (e) { return e.s && e.t; });
    // Pulse: neue Foederations-Nachrichten seit dem letzten Stand (Zaehler je Server)
    if (this.act && !RM.matches) {
      Object.keys(d.act || {}).forEach(function (pid) {
        var o = self.act[pid] || { 'in': 0, out: 0 }, n = d.act[pid], pn = self.byId[pid];
        if (!pn) return;
        var din = Math.min(3, Math.max(0, n['in'] - o['in'])), dout = Math.min(3, Math.max(0, n.out - o.out));
        for (var i = 0; i < din; i++) self.pulses.push({ a: pn, b: self.byId.self, t0: now() + i * 220, dur: 900 });
        for (var j = 0; j < dout; j++) self.pulses.push({ a: self.byId.self, b: pn, t0: now() + j * 220, dur: 900 });
      });
    }
    this.act = d.act || {};
    // Anrufe: Weg Client-Gruppe(n) -> Server -> Knoten -> verbundene Server
    this.calls = d.calls.map(function (k, ci) {
      var tail = [self.byId.self];
      if (k.node && self.byId[k.node]) tail.push(self.byId[k.node]);
      var ways = [];
      Object.keys(k.os || {}).forEach(function (cls) {
        var cn = self.byId['os:' + cls]; if (cn) ways.push([cn].concat(tail));
      });
      if (!ways.length) ways.push(tail.slice());
      (k.peers || []).forEach(function (pid) { if (self.byId[pid]) ways.forEach(function (w) { w.push(self.byId[pid]); }); });
      return { id: k.id, ways: ways.filter(function (w) { return w.length > 1; }), n: Math.min(4, Math.max(1, k.local + k.remote)), ph: ci * 0.37, d: k };
    });
    this.alpha = Math.max(this.alpha, 0.6);
    if (RM.matches) this.settle(); else this.loop();
  };

  View.prototype.tick = function () {
    var nodes = this.nodes.filter(function (n) { return n.a > 0.02 || n.ta > 0; }), a = this.alpha, i, n;
    if (!nodes.length) return;
    var tree = F.quad(nodes);
    for (i = 0; i < nodes.length; i++) F.repel(tree, nodes[i], 900 * a);
    for (i = 0; i < this.edges.length; i++) {
      var e = this.edges[i], dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      var len = e.kind === 'share' ? 110 : 190, f = (d - len) / d * 0.02 * a;
      e.t.vx -= dx * f; e.t.vy -= dy * f; e.s.vx += dx * f; e.s.vy += dy * f;
    }
    for (i = 0; i < nodes.length; i++) {
      n = nodes[i];
      if (n.kind === 'self') { n.x = 0; n.y = 0; n.vx = 0; n.vy = 0; continue; }
      n.vx += (n.hx - n.x) * 0.06 * a; n.vy += (n.hy - n.y) * 0.06 * a;
      n.vx *= 0.55; n.vy *= 0.55;
      n.vx = Math.max(-30, Math.min(30, n.vx)); n.vy = Math.max(-30, Math.min(30, n.vy));
      n.x += n.vx; n.y += n.vy;
    }
    this.alpha *= 0.97;
  };
  View.prototype.settle = function () {            // ohne Bewegung: fertig rechnen, ausgeblendete weg, ein Bild
    this.alpha = 1;
    for (var i = 0; i < 260 && this.alpha > 0.003; i++) this.tick();
    this.nodes.forEach(function (n) { n.a = n.ta; });
    this.prune(); this.pulses = []; this.alpha = 0; this.fit(true); this.draw();
  };
  // Einpassen: alle sichtbaren Knoten samt Beschriftung im Bild (weich nachgefuehrt, beim ersten Bild sofort)
  View.prototype.fit = function (now_) {
    if (!this.w) return false;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    this.nodes.forEach(function (n) {
      if (n.ta <= 0 || n.x == null) return;
      x0 = Math.min(x0, n.x); x1 = Math.max(x1, n.x); y0 = Math.min(y0, n.y); y1 = Math.max(y1, n.y);
    });
    if (x0 === Infinity) return false;
    // Raender in Pixeln: Knoten + Beschriftung (links/rechts ~85 px, oben ~40, unten ~60)
    var tk = Math.max(0.25, Math.min(1.4, (this.w - 170) / Math.max(1, x1 - x0), (this.h - 100) / Math.max(1, y1 - y0)));
    var tcx = (x0 + x1) / 2, tcy = (y0 + y1) / 2 + 10 / tk;
    if (now_ || !this.fitted) { this.k = tk; this.cx = tcx; this.cy = tcy; this.fitted = true; return false; }
    var dk = tk - this.k, dx = tcx - this.cx, dy = tcy - this.cy;
    if (Math.abs(dk) < 0.002 && Math.abs(dx) < 0.3 && Math.abs(dy) < 0.3) return false;
    this.k += dk * 0.12; this.cx += dx * 0.12; this.cy += dy * 0.12;
    return true;
  };
  View.prototype.prune = function () {
    var self = this;
    this.nodes = this.nodes.filter(function (n) { if (n.gone && n.a <= 0.01) { delete self.byId[n.id]; return false; } return true; });
  };
  View.prototype.anim = function () { return !RM.matches && !document.hidden; };
  View.prototype.loop = function () {
    if (this.raf || !this.anim()) { if (!this.anim()) this.draw(); return; }
    var self = this;
    this.raf = requestAnimationFrame(function step(t) {
      self.raf = 0;
      if (!self.c.isConnected || !self.anim()) { self.draw(); return; }
      var dt = Math.max(0, Math.min(64, t - self.t)); self.t = t;
      if (self.alpha > 0.003) { self.tick(); self.tick(); }
      var fade = dt / 350, busy = false;
      self.nodes.forEach(function (n) {
        if (n.a !== n.ta) { busy = true; n.a = n.ta > n.a ? Math.min(n.ta, n.a + fade) : Math.max(n.ta, n.a - fade); }
      });
      self.prune();
      if (self.fit()) busy = true;
      self.pulses = self.pulses.filter(function (p) { return now() - p.t0 < p.dur; });
      self.frames++;
      self.draw();
      // Laufende Anrufe animieren dauerhaft (Lichtpunkte); sonst nur, solange sich etwas bewegt
      if (self.alpha > 0.003 || busy || self.pulses.length || self.calls.length) self.loop();
    });
  };

  // --- Zeichnen --------------------------------------------------------------------------------------------------------
  View.prototype.toScreen = function (x, y) { return [this.w / 2 + (x - this.cx) * this.k, this.h / 2 + (y - this.cy) * this.k]; };
  // Gezeichnet wird in Bildschirm-Pixeln (Positionen skaliert, Knoten und Schrift immer gleich gross - auch bei vielen Knoten lesbar)
  View.prototype.draw = function () {
    var ctx = this.ctx, self = this, P = PAL;
    if (!this.w) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    function S(n) { return self.toScreen(n.x, n.y); }
    var hov = this.hover || this.sel;
    // Kanten
    this.edges.forEach(function (e) {
      var al = Math.min(e.s.a, e.t.a); if (al <= 0.01) return;
      var hi = hov && (e.s === hov || e.t === hov), a = S(e.s), b = S(e.t);
      ctx.globalAlpha = al * (hov && !hi ? 0.35 : 0.9);
      ctx.strokeStyle = P[e.state] || P.line; ctx.lineWidth = hi ? 3 : 2;
      ctx.setLineDash(e.state === 'off' || e.state === 'pending' ? [6, 5] : e.kind === 'share' ? [2, 4] : []);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      if (e.rtt != null) {                                 // Latenz an der Kante
        var mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, txt = e.rtt + ' ms';
        ctx.setLineDash([]); ctx.font = '11px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        var tw = ctx.measureText(txt).width + 8;
        ctx.fillStyle = P.bg; ctx.fillRect(mx - tw / 2, my - 8, tw, 16);
        ctx.fillStyle = P.muted; ctx.fillText(txt, mx, my);
      }
    });
    ctx.setLineDash([]); ctx.globalAlpha = 1;
    // Anrufe: Weg als feine Spur, darauf Lichtpunkte (ohne Animation: ein ruhender Punkt je Weg in der Mitte)
    var tt = now() / 1000, dots = 0, still = RM.matches || document.hidden;
    this.calls.forEach(function (cl) {
      cl.ways.forEach(function (w) {
        var pts = w.map(S), segs = [], tot = 0, i;
        for (i = 0; i < pts.length - 1; i++) { var dx = pts[i + 1][0] - pts[i][0], dy = pts[i + 1][1] - pts[i][1], l = Math.sqrt(dx * dx + dy * dy); segs.push(l); tot += l; }
        if (!tot) return;
        var al = Math.min(w[0].a, w[w.length - 1].a);
        ctx.globalAlpha = al * 0.6; ctx.strokeStyle = P.dot; ctx.lineWidth = 1.75; ctx.setLineDash([4, 5]);
        ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]); ctx.stroke();
        ctx.setLineDash([]);
        for (var j = 0; j < cl.n; j++) {
          var f = still ? 0.5 : ((tt * 0.3 + cl.ph + j / cl.n) % 1);
          var at = f * tot, si = 0;
          while (si < segs.length - 1 && at > segs[si]) { at -= segs[si]; si++; }
          var q = segs[si] ? at / segs[si] : 0, x = pts[si][0] + (pts[si + 1][0] - pts[si][0]) * q, y = pts[si][1] + (pts[si + 1][1] - pts[si][1]) * q;
          ctx.globalAlpha = al; ctx.fillStyle = P.dot; ctx.beginPath(); ctx.arc(x, y, 4.5, 0, 6.2832); ctx.fill();
          ctx.globalAlpha = al * 0.25; ctx.beginPath(); ctx.arc(x, y, 9, 0, 6.2832); ctx.fill();
          dots++;
          if (still) break;
        }
      });
    });
    this.dots = dots;
    // Pulse (Foederations-Nachrichten)
    var tn = now();
    this.pulses.forEach(function (p) {
      var f = (tn - p.t0) / p.dur; if (f < 0 || f > 1) return;
      var a = S(p.a), b = S(p.b), x = a[0] + (b[0] - a[0]) * f, y = a[1] + (b[1] - a[1]) * f;
      ctx.globalAlpha = 1 - f * 0.6; ctx.fillStyle = P.peer; ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 6.2832); ctx.fill();
    });
    ctx.globalAlpha = 1;
    // Knoten
    this.nodes.forEach(function (n) {
      if (n.a <= 0.01) return;
      var hi = !hov || n === hov || self.near(hov, n), q = S(n), x = q[0], y = q[1];
      ctx.globalAlpha = n.a * (hi ? 1 : 0.45);
      var col = P[n.kind === 'remote' ? 'horn' : n.kind] || P.line;
      ctx.fillStyle = P.bg; ctx.beginPath(); ctx.arc(x, y, n.r, 0, 6.2832); ctx.fill();
      ctx.lineWidth = n === hov ? 3.5 : 2.5; ctx.strokeStyle = n.kind === 'client' || n.kind === 'self' ? col : (P[n.state] || col);
      ctx.stroke();
      if (n.kind !== 'self' && n.kind !== 'client') {      // innerer Ring: Art (Zustand aussen)
        ctx.lineWidth = 1; ctx.strokeStyle = col; ctx.beginPath(); ctx.arc(x, y, n.r - 4, 0, 6.2832); ctx.stroke();
      }
      var pth = path(n.sym);
      if (pth) {
        var s = n.r * 1.05 / 24;
        ctx.save(); ctx.translate(x - 12 * s, y - 12 * s); ctx.scale(s, s);
        ctx.strokeStyle = col; ctx.lineWidth = 1.75; ctx.lineCap = 'round'; ctx.lineJoin = 'round'; ctx.stroke(pth); ctx.restore();
      }
      if (n.badge) {
        ctx.font = 'bold 11px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        var bw = Math.max(18, ctx.measureText(n.badge).width + 10), bx = x + n.r * 0.75, by = y - n.r * 0.75;
        ctx.fillStyle = col; ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(bx - bw / 2, by - 9, bw, 18, 9); else ctx.rect(bx - bw / 2, by - 9, bw, 18);
        ctx.fill(); ctx.fillStyle = P.bg; ctx.fillText(n.badge, bx, by + 0.5);
      }
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.font = (n.kind === 'self' ? '600 13px' : '12px') + ' system-ui, sans-serif';
      var lw = trim(ctx, n.label, 160), sw = n.sub ? (ctx.font = '11px system-ui, sans-serif', trim(ctx, n.sub, 160)) : '';
      ctx.font = (n.kind === 'self' ? '600 13px' : '12px') + ' system-ui, sans-serif';
      var bw2 = Math.max(ctx.measureText(lw).width, sw ? ctx.measureText(sw).width : 0) + 8;
      ctx.globalAlpha *= 0.85; ctx.fillStyle = P.bg; ctx.fillRect(x - bw2 / 2, y + n.r + 3, bw2, sw ? 32 : 18);   // Untergrund: Linien stoeren nicht
      ctx.globalAlpha = n.a * (hi ? 1 : 0.45);
      ctx.fillStyle = P.fg; ctx.fillText(lw, x, y + n.r + 5);
      if (sw) { ctx.font = '11px system-ui, sans-serif'; ctx.fillStyle = P.muted; ctx.fillText(sw, x, y + n.r + 20); }
    });
    ctx.globalAlpha = 1;
  };
  function trim(ctx, s, w) {
    s = String(s || ''); if (ctx.measureText(s).width <= w) return s;
    while (s.length > 1 && ctx.measureText(s + '…').width > w) s = s.slice(0, -1);
    return s + '…';
  }
  View.prototype.near = function (a, b) {
    for (var i = 0; i < this.edges.length; i++) { var e = this.edges[i]; if ((e.s === a && e.t === b) || (e.t === a && e.s === b)) return true; }
    return false;
  };
  View.prototype.at = function (cx, cy) {             // Treffer in Bildschirm-Pixeln (Knoten sind immer gleich gross)
    var best = null, bd = Infinity, self = this;
    this.nodes.forEach(function (n) {
      if (n.a < 0.3) return;
      var q = self.toScreen(n.x, n.y), dx = q[0] - cx, dy = q[1] - cy, d = dx * dx + dy * dy, r = n.r + 6;
      if (d < r * r && d < bd) { bd = d; best = n; }
    });
    return best;
  };

  // --- Detailkarte -----------------------------------------------------------------------------------------------------
  var tip = document.getElementById('net-tip');
  function ago(ts) {
    if (!ts) return L.never || '–';
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    return s < 90 ? fill(L.ago_s, { n: s }) : s < 5400 ? fill(L.ago_m, { n: Math.round(s / 60) }) : fill(L.ago_h, { n: Math.round(s / 3600) });
  }
  function ms(v) { return v == null ? '–' : v + ' ms'; }
  function yn(v) { return v ? '✓' : v === false ? '✗' : '–'; }
  function lines(n) {
    var d = n.d || {}, out = [];
    if (n.kind === 'self') {
      out.push(L.version + ' ' + d.version, L.fed + ': ' + (d.fed ? L.on : L.off) + ' · Gjallarhorn: ' + (d.horn ? L.on : L.off));
      var cc = 0; (DATA.calls || []).forEach(function () { cc++; }); out.push(fill(L.calls_here, { n: cc }));
    } else if (n.kind === 'peer') {
      if (d.version) out.push(L.version + ' ' + d.version);
      out.push(L.rtt + ' ' + ms(d.rtt) + ' · ' + L.seen + ' ' + ago(d.last_seen));
      if (d.last_error || d.probe_error) out.push(L.error + ' ' + (d.last_error || d.probe_error));
      if (d.caps && d.caps.length) out.push(L.caps + ' ' + d.caps.join(', '));
    } else if (n.kind === 'horn') {
      if (d.version) out.push(L.version + ' ' + d.version + (d.builtin ? ' · ' + L.builtin : ''));
      out.push('TCP ' + yn(d.tcp) + ' · UDP ' + yn(d.udp_ok) + ' · IPv4 ' + yn(d.v4 || null) + ' · IPv6 ' + yn(d.v6 || null));
      out.push(L.rtt + ' ' + ms(d.rtt) + (d.udp_rtt != null ? ' · UDP ' + ms(d.udp_rtt) : ''));
      out.push(L.peers + ' ' + (d.peers || 0) + ' · ' + L.util + ' ' + (d.util || 0) + ' %');
    } else if (n.kind === 'remote') {
      out.push(d['from'] || '', L.shared_by);
    } else if (n.kind === 'client') {
      out.push(L.live + ' ' + d.live + ' · ' + L.push + ' ' + d.push + ' · ' + L.incall + ' ' + d.call);
    }
    return out;
  }
  function showTip(n, px, py) {
    if (!n) { tip.hidden = true; return; }
    tip.textContent = '';
    var h = document.createElement('b'); h.textContent = n.label; tip.appendChild(h);
    var k = document.createElement('div'); k.className = 'net-tip-k';
    k.textContent = lbl('kind', n.kind === 'peer' ? 'peer' : n.kind === 'horn' ? (n.d.kind || 'own') : n.kind) +
      (n.kind === 'peer' || n.kind === 'horn' || n.kind === 'remote' ? ' · ' + lbl('state', n.state) : '');
    tip.appendChild(k);
    lines(n).forEach(function (t) { if (!t) return; var p = document.createElement('div'); p.textContent = t; tip.appendChild(p); });
    tip.hidden = false;
    var p = place(n, tip.offsetWidth, tip.offsetHeight);
    tip.style.left = p[0] + 'px'; tip.style.top = p[1] + 'px';
    tip.dataset.node = n.id;
  }
  // Platz der Detailkarte: neben dem Knoten oder in einer Ecke - dort, wo sie am wenigsten verdeckt (nie den Knoten
  // selbst, moeglichst keine anderen Knoten samt Beschriftung und keine Latenz-Beschriftungen an den Kanten)
  function place(n, tw, th) {
    var W = canvas.clientWidth, H = canvas.clientHeight, q = scr(n), g = n.r + 12, m = 8, boxes = [];
    view.nodes.forEach(function (o) {
      if (o.a < 0.3) return;
      var c = scr(o);
      boxes.push({ x0: c[0] - Math.max(o.r, 60), x1: c[0] + Math.max(o.r, 60), y0: c[1] - o.r - 10, y1: c[1] + o.r + 36, w: o === n ? 1000 : 10 });
    });
    view.edges.forEach(function (e) {
      if (e.rtt == null) return;
      var a = scr(e.s), b = scr(e.t), mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
      boxes.push({ x0: mx - 24, x1: mx + 24, y0: my - 9, y1: my + 9, w: 4 });
    });
    var cand = [[q[0] + g, q[1] - th / 2], [q[0] - g - tw, q[1] - th / 2], [q[0] - tw / 2, q[1] - g - th], [q[0] - tw / 2, q[1] + g + 34],
                [m, m], [W - tw - m, m], [m, H - th - m], [W - tw - m, H - th - m]];
    var best = null, bs = Infinity;
    cand.forEach(function (c, i) {
      var x = Math.min(Math.max(m, c[0]), W - tw - m), y = Math.min(Math.max(m, c[1]), H - th - m), sc = i < 4 ? 0 : 1;
      boxes.forEach(function (b) {
        var ox = Math.min(x + tw, b.x1) - Math.max(x, b.x0), oy = Math.min(y + th, b.y1) - Math.max(y, b.y0);
        if (ox > 0 && oy > 0) sc += b.w;
      });
      if (sc < bs) { bs = sc; best = [x, y]; }
    });
    return best;
  }
  function scr(n) { var p = view.toScreen(n.x, n.y); return p; }
  canvas.addEventListener('pointermove', function (e) {
    if (e.pointerType === 'touch') return;
    var r = canvas.getBoundingClientRect(), n = view.at(e.clientX - r.left, e.clientY - r.top);
    if (n !== view.hover) { view.hover = n; canvas.style.cursor = n ? 'pointer' : ''; if (!view.raf) view.draw(); }
    if (!view.sel && (n ? tip.hidden || tip.dataset.node !== n.id : !tip.hidden)) showTip(n);
  });
  canvas.addEventListener('pointerleave', function () { view.hover = null; if (!view.sel) showTip(null); view.draw(); });
  canvas.addEventListener('click', function (e) {
    var r = canvas.getBoundingClientRect(), n = view.at(e.clientX - r.left, e.clientY - r.top);
    view.sel = n === view.sel ? null : n;
    if (view.sel) { var p = scr(view.sel); showTip(view.sel, p[0], p[1]); } else showTip(null);
    view.draw();
  });
  // Tastatur: Pfeile waehlen den naechsten/vorigen Knoten, Esc schliesst die Karte
  canvas.addEventListener('keydown', function (e) {
    var vis = view.nodes.filter(function (n) { return n.ta > 0; });
    if (!vis.length) return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      var i = vis.indexOf(view.sel), d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
      view.sel = vis[(i + d + vis.length) % vis.length];
      var p = scr(view.sel); showTip(view.sel, p[0], p[1]); view.draw(); e.preventDefault();
    } else if (e.key === 'Escape') { view.sel = null; showTip(null); view.draw(); }
  });

  // --- Tabellen (barrierefreie Ansicht) --------------------------------------------------------------------------------
  function td(tr, text, tag) { var c = document.createElement(tag || 'td'); if (tag === 'th') c.scope = 'row'; if (text instanceof Node) c.appendChild(text); else c.textContent = text; tr.appendChild(c); return c; }
  function stateCell(st) { var f = document.createDocumentFragment(), s = document.createElement('span'); s.className = 'net-pill'; f.appendChild(s); f.appendChild(document.createTextNode(lbl('state', st))); return f; }
  function nameCell(a, b) {
    var f = document.createDocumentFragment(); f.appendChild(document.createTextNode(a));
    if (b && b !== a) { f.appendChild(document.createElement('br')); var s = document.createElement('small'); s.textContent = b; f.appendChild(s); }
    return f;
  }
  function tables(d) {
    var names = { self: d.self.name };
    d.peers.forEach(function (p) { names[p.id] = p.name; });
    d.horns.forEach(function (h) { names[h.id] = h.name; });
    d.clients.forEach(function (c) { names[c.id] = lbl('cls', c.cls); });
    var tb = document.querySelector('#net-nodes tbody'); if (!tb) return;
    tb.textContent = '';
    function row(id, cls) { var tr = document.createElement('tr'); tr.dataset.id = id; if (cls) tr.className = cls; tb.appendChild(tr); return tr; }
    var tr = row('self');
    td(tr, nameCell(d.self.name, d.self.host), 'th'); td(tr, lbl('kind', 'self')); td(tr, lbl('state', 'ok'));
    td(tr, lines({ kind: 'self', d: d.self }).join(' · '));
    d.peers.forEach(function (p) {
      var r = row(p.id, 'net-st-' + p.state);
      td(r, nameCell(p.name, p.host), 'th'); td(r, lbl('kind', 'peer')); td(r, stateCell(p.state)); td(r, lines({ kind: 'peer', d: p }).join(' · '));
    });
    d.horns.forEach(function (h) {
      var r = row(h.id, 'net-st-' + h.state);
      td(r, h.name, 'th'); td(r, nameCell(lbl('kind', h.kind), h.kind === 'remote' ? h['from'] : '')); td(r, stateCell(h.state));
      td(r, lines({ kind: h.kind === 'remote' ? 'remote' : 'horn', d: h }).join(' · '));
    });
    d.clients.forEach(function (c) {
      var r = row(c.id);
      td(r, lbl('cls', c.cls), 'th'); td(r, lbl('kind', 'client')); td(r, String(c.n)); td(r, lines({ kind: 'client', d: c }).join(' · '));
    });
    var te = document.querySelector('#net-edges tbody'); te.textContent = '';
    d.edges.forEach(function (e) {
      var r = document.createElement('tr'); r.className = 'net-st-' + e.state; te.appendChild(r);
      td(r, names[e.a] || e.a); td(r, names[e.b] || e.b); td(r, lbl('edge', e.kind)); td(r, stateCell(e.state)); td(r, ms(e.rtt));
    });
    var tc = document.querySelector('#net-calls tbody'); tc.textContent = '';
    if (!d.calls.length) {
      var r0 = document.createElement('tr'); r0.className = 'net-none'; tc.appendChild(r0);
      var c0 = td(r0, ''); c0.colSpan = 4; var sm = document.createElement('small'); sm.textContent = L.nocall; c0.appendChild(sm);
    }
    d.calls.forEach(function (k) {
      var r = document.createElement('tr'); tc.appendChild(r);
      td(r, '#' + k.id.slice(0, 6) + ' · ' + (k.media === 'video' ? L.video : L.audio));
      td(r, k.node ? fill(L.via, { name: names[k.node] || '–' }) : L.mesh);
      td(r, String(k.local));
      td(r, String(k.remote) + (k.peers.length ? ' (' + k.peers.map(function (x) { return names[x] || x; }).join(', ') + ')' : ''));
    });
    var sum = document.getElementById('net-sum');
    if (sum) sum.textContent = fill(L.sum, { a: d.peers.length, b: d.horns.length, c: d.clients.reduce(function (s, c) { return s + c.n; }, 0), d: d.calls.length });
  }

  // --- Live ------------------------------------------------------------------------------------------------------------
  var liveEl = document.getElementById('net-live'), es = null, poll = 0, fails = 0;
  function status(txt, on) { if (liveEl) { liveEl.textContent = txt; liveEl.classList.toggle('on', !!on); } }
  function apply(d) {
    DATA = d; view.set(d); tables(d);
    if (view.sel && !view.byId[view.sel.id]) { view.sel = null; showTip(null); }
    else if (view.sel) { var p = scr(view.sel); showTip(view.sel, p[0], p[1]); }
  }
  function fetchOnce() {
    return fetch('/api/net', { credentials: 'same-origin', headers: { Accept: 'application/json' } })
      .then(function (r) { if (r.status === 401 || r.status === 403) { stopAll(); location.reload(); throw new Error('auth'); } if (!r.ok) throw new Error(r.status); return r.json(); }).then(apply);
  }
  // Rechte entzogen/abgemeldet: nichts mehr abfragen, Neuladen fuehrt zur Anmeldung
  function stopAll() { if (poll) { clearInterval(poll); poll = 0; } if (es) { es.close(); es = null; } fails = 99; }
  function startPoll() {
    if (poll) return;
    status(L.livetxt, true);
    poll = setInterval(function () { if (!document.hidden) fetchOnce().catch(function () { status(L.reconnect, false); }); }, 5000);
  }
  function start() {
    if (document.hidden) return;
    if (!window.EventSource || fails >= 3) { startPoll(); return; }
    if (es) return;
    es = new EventSource('/api/net/events');
    es.addEventListener('net', function (ev) { fails = 0; status(L.livetxt, true); try { apply(JSON.parse(ev.data)); } catch (e) { /* naechster Stand */ } });
    es.addEventListener('bye', function (ev) { if (es) { es.close(); es = null; } if (/auth/.test(String(ev && ev.data || ''))) { stopAll(); location.reload(); return; } setTimeout(start, 1000); });
    es.onerror = function () {
      status(L.reconnect, false);
      if (es && es.readyState === 2) { es = null; fails++; setTimeout(start, 3000); }
      else fails++;
      if (fails >= 3 && es) { es.close(); es = null; startPoll(); }
    };
  }
  function stop() {
    if (es) { es.close(); es = null; }
    if (poll) { clearInterval(poll); poll = 0; }
    status(L.paused, false);
  }
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) stop();
    else { start(); view.loop(); }
  });
  if (RM.addEventListener) RM.addEventListener('change', function () { if (RM.matches) view.settle(); else view.loop(); });

  // Fuer Browser-Tests: Kennzahlen der Ansicht (keine Daten, die nicht ohnehin auf der Seite stehen)
  window.SagaNet = { stats: function () {
    return { nodes: view.nodes.filter(function (n) { return n.ta > 0; }).map(function (n) { return n.kind + ':' + n.label; }),
      edges: view.edges.length, dots: view.dots || 0, calls: view.calls.length, frames: view.frames, anim: view.anim(), raf: !!view.raf };
  }, pos: function (id) { var n = view.byId[id]; return n ? view.toScreen(n.x, n.y) : null; }, apply: apply };

  var ro = window.ResizeObserver ? new ResizeObserver(function () { view.resize(); }) : null;
  if (ro) ro.observe(canvas); else window.addEventListener('resize', function () { view.resize(); });
  view.resize();
  view.set(DATA);
  start();
})();
