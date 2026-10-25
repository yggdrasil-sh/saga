// Saga - Graph der Seiten (2.5, wie Obsidian). Eine Ueberlagerung mit zwei Ansichten: "Alle Seiten" (Seitenleiste
// "Graph") und "Lokal" (Seitenmenue "…" > "Lokaler Graph", Knopf bei "Verlinkt von"): die offene Seite mit ihren
// Nachbarn bis Tiefe 1-3. Knoten = Seiten (eigene und fuer mich freigegebene), Kanten = Verweise im Text, Unterseiten,
// Datenbank-Relationen und Tags (Tag-Knoten). Daten: GET /api/graph (saga/graph.py). Eigene, schlanke Kraefte-Simulation
// auf Canvas (Barnes-Hut) - keine Fremdbibliothek (CSP). Ueberfahren hebt Knoten + Nachbarn weich hervor (~180 ms),
// Text blendet je nach Zoom ein, gezogene Knoten ziehen ihre Nachbarn mit. prefers-reduced-motion: ohne Uebergaenge.
(function () {
  'use strict';
  if (!window.HK || !document.body.classList.contains('notes-page')) return;
  var HK = window.HK, el = HK.el, T = window.T;
  var KEY = 'saga-graph';
  var DEF = { link: true, child: true, rel: true, tags: false, orphans: true, rows: true, arrows: false, labels: 1.1,
              size: 1, width: 1, center: 0.5, repel: 0.6, spring: 0.5, dist: 0.5, groups: [], depth: 1, panel: false };
  var RESET = ['link', 'child', 'rel', 'tags', 'orphans', 'rows', 'arrows', 'labels', 'size', 'width', 'center', 'repel', 'spring', 'dist'];
  function prefs() {
    var p = {}; try { p = JSON.parse(HK.store(KEY) || '{}') || {}; } catch (e) { p = {}; }
    var o = {}; Object.keys(DEF).forEach(function (k) { o[k] = p[k] != null ? p[k] : DEF[k]; });
    if (!Array.isArray(o.groups)) o.groups = [];
    return o;
  }
  function savePrefs(p) { HK.store(KEY, JSON.stringify(p)); }
  var GCOL = ['#e5484d', '#30a46c', '#e93d82', '#3e9bff', '#f5a524', '#8e4ec6', '#12a594', '#d6409f'];
  var RM = window.matchMedia ? matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  var DUR = 180;                                     // ms fuer Hervorheben/Abblenden (Obsidian: ~150-250)
  function ease(x) { return x * x * (3 - 2 * x); }
  function now() { return window.performance ? performance.now() : Date.now(); }

  // --- Daten -----------------------------------------------------------------------------------------------------
  var DATA = null, DATA_T = 0, LOADING = null;
  function load(force) {
    if (!force && DATA && Date.now() - DATA_T < 30000) return Promise.resolve(DATA);
    if (LOADING) return LOADING;
    LOADING = HK.api('GET', '/api/graph').then(function (d) { DATA = d; DATA_T = Date.now(); LOADING = null; return d; },
      function (e) { LOADING = null; throw e; });
    return LOADING;
  }
  document.addEventListener('hk-live', function (e) { if (e.detail && e.detail.type === 'notes.tree') DATA_T = 0; });

  // Farben aus dem Stylesheet (hell/dunkel); werden je Ansicht zwischengespeichert, nicht je Bild abgefragt
  function palette() {
    var s = getComputedStyle(document.body);
    function v(n, fb) { return s.getPropertyValue(n).trim() || fb; }
    return { fg: v('--fg', '#ddd'), bg: v('--bg', '#111'), acc: v('--accent', '#c9a45c'), line: v('--gr-line', '#8b97a8'),
             page: v('--gr-node', '#9aa6b8'), db: v('--gr-db', '#b39ddb'), row: v('--gr-row', '#7fb0e0'), tag: v('--gr-tag', '#6cc48a'),
             lineA: parseFloat(v('--gr-line-a', '0.35')) || 0.35 };
  }

  // Graph fuer die Anzeige bauen: Filter, Tag-Knoten, Gruppenfarben, lokal (BFS ab einer Seite)
  function build(d, p, opt) {
    var byId = {}, nodes = [], edges = [];
    d.nodes.forEach(function (n) {
      if (!p.rows && n[3] === 'row' && !(opt && opt.focus === n[0])) return;
      var o = { id: n[0], title: n[1] || T('Unbenannt'), icon: n[2], kind: n[3], own: !!n[4], parent: n[5], deg: 0, tags: [] };
      byId[o.id] = o; nodes.push(o);
    });
    function add(a, b, k) { if (byId[a] && byId[b] && a !== b) { edges.push({ s: byId[a], t: byId[b], k: k }); } }
    d.edges.forEach(function (e) {
      if ((e[2] === 'l' && p.link) || (e[2] === 'c' && p.child) || (e[2] === 'r' && p.rel)) add(e[0], e[1], e[2]);
    });
    (d.tags || []).forEach(function (tg) {
      tg[1].forEach(function (id) { if (byId[id]) byId[id].tags.push(tg[0]); });
      if (!p.tags) return;
      var key = 'tag:' + tg[0], tn = { id: key, title: '#' + tg[0], tag: true, kind: 'tag', own: true, deg: 0, tags: [] };
      var any = false;
      tg[1].forEach(function (id) { if (byId[id]) { if (!any) { byId[key] = tn; nodes.push(tn); any = true; } add(id, key, 't'); } });
    });
    if (opt && opt.focus != null) {                          // lokal: nur Nachbarn bis zur gewaehlten Tiefe
      if (!byId[opt.focus]) return { nodes: [], edges: [], missing: true };
      var adj = {}; edges.forEach(function (e) { (adj[e.s.id] = adj[e.s.id] || []).push(e.t.id); (adj[e.t.id] = adj[e.t.id] || []).push(e.s.id); });
      var seen = {}, q = [[opt.focus, 0]]; seen[opt.focus] = 0;
      while (q.length) {
        var x = q.shift(); if (x[1] >= (opt.depth || 1)) continue;
        (adj[x[0]] || []).forEach(function (y) { if (seen[y] == null) { seen[y] = x[1] + 1; q.push([y, x[1] + 1]); } });
      }
      nodes = nodes.filter(function (n) { return seen[n.id] != null; });
      edges = edges.filter(function (e) { return seen[e.s.id] != null && seen[e.t.id] != null; });
    }
    edges.forEach(function (e) { e.s.deg++; e.t.deg++; });
    if (!p.orphans && !(opt && opt.focus != null)) nodes = nodes.filter(function (n) { return n.deg > 0; });
    // Gruppen: erste passende gewinnt. Ausdruck: "tag:x", "path:Titel" (Seite und alles darunter), sonst Text im Titel
    var titleIds = {};
    var groups = (p.groups || []).map(function (g) {
      var q = String(g.q || '').trim(), m = /^(tag|path):(.*)$/i.exec(q);
      return { kind: m ? m[1].toLowerCase() : 'text', v: (m ? m[2] : q).trim().toLowerCase(), color: g.color };
    }).filter(function (g) { return g.v; });
    groups.forEach(function (g) {
      if (g.kind !== 'path') return;
      var roots = d.nodes.filter(function (n) { return String(n[1] || '').toLowerCase() === g.v; }).map(function (n) { return n[0]; });
      var kids = {}; d.nodes.forEach(function (n) { (kids[n[5]] = kids[n[5]] || []).push(n[0]); });
      var set = {}, st = roots.slice();
      while (st.length) { var id = st.pop(); if (set[id]) continue; set[id] = true; (kids[id] || []).forEach(function (k) { st.push(k); }); }
      titleIds[g.v] = set;
    });
    nodes.forEach(function (n) {
      n.group = null;
      for (var i = 0; i < groups.length; i++) {
        var g = groups[i];
        var hit = g.kind === 'tag' ? n.tags.some(function (t) { return t.toLowerCase() === g.v; }) || (n.tag && n.title.slice(1).toLowerCase() === g.v)
          : g.kind === 'path' ? !!(titleIds[g.v] || {})[n.id] : n.title.toLowerCase().indexOf(g.v) >= 0;
        if (hit) { n.group = g.color; break; }
      }
    });
    return { nodes: nodes, edges: edges };
  }

  // --- Simulation ------------------------------------------------------------------------------------------------
  // Barnes-Hut-Quadtree fuer die Abstossung
  function quad(nodes) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, i, n;
    for (i = 0; i < nodes.length; i++) { n = nodes[i]; if (n.x < x0) x0 = n.x; if (n.y < y0) y0 = n.y; if (n.x > x1) x1 = n.x; if (n.y > y1) y1 = n.y; }
    var root = cell(x0, y0, Math.max(x1 - x0, y1 - y0) + 1);
    for (i = 0; i < nodes.length; i++) ins(root, nodes[i], 0);
    return root;
  }
  function cell(x, y, s) { return { x: x, y: y, s: s, m: 0, cx: 0, cy: 0, n: null, k: null }; }
  function ins(q, n, d) {
    if (!q.k && !q.n) { q.n = n; q.m = 1; q.cx = n.x; q.cy = n.y; return; }
    if (!q.k) {
      if (d > 40) { q.m++; return; }
      q.k = [null, null, null, null]; var o = q.n; q.n = null; q.m = 0; q.cx = 0; q.cy = 0;
      put(q, o, d);
    }
    put(q, n, d);
  }
  function put(q, n, d) {
    var h = q.s / 2, i = (n.x >= q.x + h ? 1 : 0) + (n.y >= q.y + h ? 2 : 0);
    if (!q.k[i]) q.k[i] = cell(q.x + (i & 1 ? h : 0), q.y + (i & 2 ? h : 0), h);
    ins(q.k[i], n, d + 1);
    q.cx = (q.cx * q.m + n.x) / (q.m + 1); q.cy = (q.cy * q.m + n.y) / (q.m + 1); q.m++;
  }
  function repel(q, nd, f0) {
    if (!q || !q.m) return;
    if (q.n === nd && !q.k) return;
    var dx = q.cx - nd.x, dy = q.cy - nd.y, d2 = dx * dx + dy * dy + 0.01;
    if (!q.k || (q.s * q.s) / d2 < 0.81) {           // weit weg (theta 0.9): ganze Zelle als ein Koerper
      if (d2 > 1e6) return;
      var f = f0 * q.m / d2; nd.vx -= dx * f; nd.vy -= dy * f; return;
    }
    repel(q.k[0], nd, f0); repel(q.k[1], nd, f0); repel(q.k[2], nd, f0); repel(q.k[3], nd, f0);
  }

  // --- Ansicht: Simulation, Zeichnen, Zeiger --------------------------------------------------------------------
  function View(canvas, opts) {
    this.c = canvas; this.ctx = canvas.getContext('2d'); this.o = opts || {};
    this.nodes = []; this.edges = []; this.adj = {}; this.k = 1; this.tx = 0; this.ty = 0; this.alpha = 0;
    this.hover = null; this.sel = null; this.drag = null; this.p = prefs(); this.raf = 0; this.focus = null; this.match = null;
    this.dim = 0; this.mp = null; this.cam = null; this.autoFit = false; this.last = 0; this.pal = palette();
    var self = this;
    this.ro = window.ResizeObserver ? new ResizeObserver(function () { self.resize(); }) : null;
    if (this.ro) this.ro.observe(canvas); else window.addEventListener('resize', function () { self.resize(); });
    this.bind();
  }
  View.prototype.resize = function () {
    var r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width || !r.height) return;
    var first = !this.w, ow = this.w, oh = this.h;
    this.w = r.width; this.h = r.height; this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr); this.dpr = dpr;
    if (first) { this.tx = this.w / 2; this.ty = this.h / 2; }
    else { this.tx += (this.w - ow) / 2; this.ty += (this.h - oh) / 2; }   // Panel auf/zu: Bildmitte bleibt
    this.draw();
  };
  View.prototype.set = function (g, keep) {
    var old = {}; (keep ? this.nodes : []).forEach(function (n) { old[n.id] = n; });
    var R = Math.sqrt(g.nodes.length + 1) * 18, self = this;
    g.nodes.forEach(function (n, i) {
      var o = old[n.id];
      if (o) { n.x = o.x; n.y = o.y; n.vx = o.vx; n.vy = o.vy; n.e = o.e; n.g = o.g; }
      else { var a = i * 2.399963, rr = R * Math.sqrt((i + 0.5) / g.nodes.length); n.x = Math.cos(a) * rr; n.y = Math.sin(a) * rr; n.vx = 0; n.vy = 0; n.e = 0; n.g = 0; }
      if (n.id === self.focus && !o) { n.x = 0; n.y = 0; }
    });
    g.edges.forEach(function (e) { e.e = 0; });
    this.nodes = g.nodes; this.edges = g.edges;
    this.adj = {}; var adj = this.adj;
    g.edges.forEach(function (e) { (adj[e.s.id] = adj[e.s.id] || {})[e.t.id] = true; (adj[e.t.id] = adj[e.t.id] || {})[e.s.id] = true; });
    if (this.hover && !this.byId(this.hover.id)) this.hover = null; else if (this.hover) this.hover = this.byId(this.hover.id);
    if (this.sel && !this.byId(this.sel.id)) this.setSel(null);
    if (!keep) {
      // Vorlauf ohne Bild (max. ~60 ms), dann sofort passend einpassen - kein spaeterer Sprung der Ansicht
      this.alpha = 1; var t0 = now();
      while (now() - t0 < 60 && this.alpha > 0.05) this.tick();
      this.autoFit = true; this.fit(true);
    }
    this.heat(keep ? 0.5 : this.alpha);
  };
  View.prototype.byId = function (id) { for (var i = 0; i < this.nodes.length; i++) if (this.nodes[i].id === id) return this.nodes[i]; return null; };
  View.prototype.heat = function (a) {
    this.alpha = Math.max(this.alpha, a);
    if (RM.matches) { this.settle(); return; }
    this.loop();
  };
  View.prototype.settle = function () {                 // ohne Bewegung: fertig rechnen, ein Bild
    for (var i = 0; i < 300 && this.alpha > 0.002; i++) this.tick();
    this.alpha = 0; if (this.autoFit) this.fit(true); this.animate(1e9); this.draw();
  };
  View.prototype.loop = function () {
    if (this.raf) return;
    var self = this; this.last = now();
    this.raf = requestAnimationFrame(function step(t) {
      self.raf = 0;
      if (!self.c.isConnected) return;
      var dt = Math.max(0, Math.min(64, t - self.last)); self.last = t;
      var dragN = self.drag && self.drag.node && self.drag.moved;
      if (dragN && self.alpha < 0.3) self.alpha = 0.3;          // Ziehen: Simulation laeuft weiter, Nachbarn folgen
      var sim = self.alpha > 0.002 && !(RM.matches && !dragN);
      if (sim) { self.tick(); self.tick(); }
      var cam = self.camStep(dt, sim);
      if (self.mp && !self.drag && (sim || cam)) self.hoverAt(self.mp.x, self.mp.y);   // Inhalt bewegt sich unter dem Zeiger
      var busy = self.animate(dt);
      self.draw();
      if (sim || cam || busy) self.loop();
    });
  };
  View.prototype.tick = function () {
    var p = this.p, a = this.alpha, nodes = this.nodes, n = nodes.length, i, nd;
    if (!n) return;
    var f0 = (30 + p.repel * 600) * a, spring = 0.02 + p.spring * 0.25, len = 20 + p.dist * 120, cen = 0.002 + p.center * 0.03;
    var tree = quad(nodes);
    for (i = 0; i < n; i++) repel(tree, nodes[i], f0);
    var edges = this.edges;
    for (i = 0; i < edges.length; i++) {
      var e = edges[i], dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      var f = (d - len) / d * spring * a, bs = e.s.deg / (e.s.deg + e.t.deg);
      e.t.vx -= dx * f * bs; e.t.vy -= dy * f * bs; e.s.vx += dx * f * (1 - bs); e.s.vy += dy * f * (1 - bs);
    }
    var drag = this.drag && this.drag.node;
    for (i = 0; i < n; i++) {
      nd = nodes[i];
      nd.vx -= nd.x * cen * a; nd.vy -= nd.y * cen * a;
      if (nd === drag) { nd.vx = 0; nd.vy = 0; continue; }
      nd.vx *= 0.6; nd.vy *= 0.6;
      if (nd.vx > 50) nd.vx = 50; else if (nd.vx < -50) nd.vx = -50;
      if (nd.vy > 50) nd.vy = 50; else if (nd.vy < -50) nd.vy = -50;
      nd.x += nd.vx; nd.y += nd.vy;
    }
    this.alpha *= 0.985;
  };
  // Hervorhebung weich nachfuehren: e = Knoten/Kante gehoert zur Hervorhebung, g = Knoten waechst, dim = Rest blendet ab
  View.prototype.animate = function (dt) {
    var s = RM.matches ? 1 : Math.min(1, dt / DUR), busy = false;
    var hov = this.hover || this.sel, adj = hov ? (this.adj[hov.id] || {}) : null, m = this.match;
    function to(cur, tgt) { if (cur === tgt) return cur; var d = tgt - cur; if (d <= s && d >= -s) return tgt; busy = true; return cur + (d > 0 ? s : -s); }
    this.dim = to(this.dim, hov || m ? 1 : 0);
    var i, n;
    for (i = 0; i < this.nodes.length; i++) {
      n = this.nodes[i];
      n.e = to(n.e || 0, hov ? (n === hov || adj[n.id] ? 1 : 0) : m && m[n.id] ? 1 : 0);
      n.g = to(n.g || 0, n === hov ? 1 : 0);
    }
    for (i = 0; i < this.edges.length; i++) { var e = this.edges[i]; e.e = to(e.e || 0, hov && (e.s === hov || e.t === hov) ? 1 : 0); }
    return busy;
  };
  View.prototype.rad = function (n) { return Math.max((2.5 + Math.sqrt(n.deg) * 1.6) * this.p.size * (n.tag ? 0.85 : 1), 2.5 / this.k); };
  // gezeichneter Radius: waechst beim Ueberfahren um 35 % und auf mind. 5 px (Bildschirm), auch weit herausgezoomt
  View.prototype.rr = function (n) { var g = ease(n.g || 0), r = this.rad(n); return g ? Math.max(r * (1 + 0.35 * g), (2.5 + 2.5 * g) / this.k) : r; };
  View.prototype.color = function (n) {
    var P = this.pal;
    return n.group || (n.tag ? P.tag : n.kind === 'database' ? P.db : n.kind === 'row' ? P.row : P.page);
  };
  View.prototype.draw = function () {
    var ctx = this.ctx, w = this.w, h = this.h, k = this.k, self = this, P = this.pal;
    if (!w) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!this.nodes.length) return;
    var D = ease(this.dim), m = this.match;
    var vx0 = -this.tx / k, vy0 = -this.ty / k, vx1 = (w - this.tx) / k, vy1 = (h - this.ty) / k, pad = 40 / k;
    function vis(x, y, r) { return x + r > vx0 - pad && x - r < vx1 + pad && y + r > vy0 - pad && y - r < vy1 + pad; }
    function evis(e) { var a = e.s, b = e.t; return !((a.x < vx0 && b.x < vx0) || (a.x > vx1 && b.x > vx1) || (a.y < vy0 && b.y < vy0) || (a.y > vy1 && b.y > vy1)); }
    ctx.save(); ctx.translate(this.tx, this.ty); ctx.scale(k, k);
    var lw = Math.max(0.5, 0.9 * this.p.width) / Math.sqrt(k), arrows = this.p.arrows, i, e, n;
    ctx.lineWidth = lw; ctx.lineCap = 'round';
    // Kanten: Grundschicht je Art in einem Pfad (schnell), hervorgehobene einzeln obendrauf
    var base = P.lineA * (1 - 0.8 * D), hi = [];
    [['l', 'c'], ['r'], ['t']].forEach(function (kinds) {
      ctx.beginPath(); var any = false;
      for (i = 0; i < self.edges.length; i++) {
        e = self.edges[i];
        if (kinds.indexOf(e.k) < 0 || !evis(e)) continue;
        if (e.e > 0) { hi.push(e); continue; }
        ctx.moveTo(e.s.x, e.s.y); ctx.lineTo(e.t.x, e.t.y); any = true;
      }
      if (!any) return;
      ctx.setLineDash(kinds[0] === 'r' ? [3 / k, 3 / k] : []);
      ctx.globalAlpha = base * (kinds[0] === 't' ? 0.6 : 1); ctx.strokeStyle = P.line; ctx.stroke();
    });
    ctx.setLineDash([]);
    if (arrows) {
      ctx.beginPath();
      for (i = 0; i < this.edges.length; i++) { e = this.edges[i]; if (e.k !== 't' && !e.e && evis(e)) this.arrow(e, lw); }
      ctx.globalAlpha = Math.min(1, base * 1.6); ctx.fillStyle = P.line; ctx.fill();
    }
    hi.forEach(function (e) {
      var a = ease(e.e);
      ctx.setLineDash(e.k === 'r' ? [3 / k, 3 / k] : []);
      ctx.lineWidth = lw * (1 + 0.8 * a);
      ctx.globalAlpha = base * (1 - a); ctx.strokeStyle = P.line;
      ctx.beginPath(); ctx.moveTo(e.s.x, e.s.y); ctx.lineTo(e.t.x, e.t.y); ctx.stroke();
      ctx.globalAlpha = 0.9 * a; ctx.strokeStyle = P.acc; ctx.stroke();
      if (arrows && e.k !== 't') { ctx.beginPath(); self.arrow(e, lw); ctx.fillStyle = P.acc; ctx.fill(); }
    });
    ctx.setLineDash([]); ctx.lineWidth = lw;
    // Knoten: unbeteiligte je Farbe gebuendelt, hervorgehobene einzeln (wachsen, Akzentfarbe beim ueberfahrenen)
    var dimA = 1 - 0.85 * D, batches = {}, hiN = [], lab = [], thr = this.p.labels * 1.6, few = this.nodes.length <= 40;
    for (i = 0; i < this.nodes.length; i++) {
      n = this.nodes[i];
      var r = this.rad(n);
      if (!vis(n.x, n.y, r * 1.4)) continue;
      if (n.e > 0 || n.g > 0 || n.id === this.focus) hiN.push(n);
      else { var key = this.color(n) + (n.own ? '' : '|s'); (batches[key] = batches[key] || []).push(n); }
      // Text: weich je Zoom (wie Obsidian), Nachbarn des ueberfahrenen Knotens immer
      var z = k * (1 + Math.sqrt(n.deg) * 0.3), za = few ? 1 : Math.max(0, Math.min(1, (z - thr * 0.72) / (thr * 0.28)));
      var la = Math.max(za * dimA, ease(n.e || 0));
      if (m && !m[n.id]) la = Math.min(la, za * dimA);
      if (la > 0.03) lab.push([n, la]);
    }
    Object.keys(batches).forEach(function (key) {
      var list = batches[key]; ctx.beginPath();
      list.forEach(function (n) { var r = self.rad(n); ctx.moveTo(n.x + r, n.y); ctx.arc(n.x, n.y, r, 0, 6.2832); });
      ctx.globalAlpha = dimA * (key.slice(-2) === '|s' ? 0.7 : 1); ctx.fillStyle = key.split('|')[0]; ctx.fill();
    });
    hiN.forEach(function (n) {
      var a = ease(n.e || 0), g = ease(n.g || 0), r = self.rr(n), cur = n.id === self.focus;
      ctx.globalAlpha = (dimA + (1 - dimA) * a) * (n.own ? 1 : 0.7);
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832); ctx.fillStyle = cur ? P.acc : self.color(n); ctx.fill();
      if (g > 0 && !cur) { ctx.globalAlpha = g; ctx.fillStyle = P.acc; ctx.fill(); }
      if (cur) {                                     // offene Seite: Ring mit Abstand ("du bist hier")
        ctx.globalAlpha = Math.max(dimA, a); ctx.lineWidth = 1.6 / k; ctx.strokeStyle = P.acc;
        ctx.beginPath(); ctx.arc(n.x, n.y, r + 3.5 / k, 0, 6.2832); ctx.stroke(); ctx.lineWidth = lw;
      }
    });
    // Beschriftungen: feste Bildschirmgroesse, Rand in Hintergrundfarbe; hervorgehobene zuletzt (oben)
    lab.sort(function (a, b) { return (a[0].e || 0) - (b[0].e || 0); });
    if (lab.length > 450) lab = lab.slice(lab.length - 450);
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.lineJoin = 'round'; ctx.lineWidth = 3 / k; ctx.strokeStyle = P.bg; ctx.fillStyle = P.fg;
    var f0 = (12 / k) + 'px system-ui, -apple-system, "Segoe UI", sans-serif', f1 = '600 ' + (13 / k) + 'px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.font = f0; var curF = f0;
    lab.forEach(function (x) {
      var n = x[0], big = n === self.hover || n === self.sel, f = big ? f1 : f0;
      if (f !== curF) { ctx.font = f; curF = f; }
      var t = (n.icon && n.icon.length <= 4 ? n.icon + ' ' : '') + (n.title.length > 40 && !big ? n.title.slice(0, 39) + '…' : n.title);
      var y = n.y + self.rr(n) + 4 / k;
      ctx.globalAlpha = x[1]; ctx.strokeText(t, n.x, y); ctx.fillText(t, n.x, y);
    });
    ctx.restore();
  };
  View.prototype.arrow = function (e, lw) {
    var ctx = this.ctx, k = this.k, dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
    var r = this.rr(e.t) + 2 / k, s = Math.max(4, lw * 5) / Math.sqrt(k), ux = dx / d, uy = dy / d;
    var ex = e.t.x - ux * r, ey = e.t.y - uy * r;
    ctx.moveTo(ex, ey); ctx.lineTo(ex - ux * s - uy * s * 0.6, ey - uy * s + ux * s * 0.6); ctx.lineTo(ex - ux * s + uy * s * 0.6, ey - uy * s - ux * s * 0.6); ctx.closePath();
  };
  // Treffer: grosszuegig - mindestens 10 px um den Knoten (Bildschirm), auch wenn der Punkt weit herausgezoomt winzig ist
  View.prototype.at = function (cx, cy, slop) {
    var k = this.k, x = (cx - this.tx) / k, y = (cy - this.ty) / k, best = null, bd = Infinity, min = slop || 10;
    for (var i = 0; i < this.nodes.length; i++) {
      var n = this.nodes[i], dx = n.x - x, dy = n.y - y, d = Math.sqrt(dx * dx + dy * dy) * k;
      var rs = this.rr(n) * k, lim = Math.max(rs + 4, min);
      if (d < lim && d - rs < bd) { bd = d - rs; best = n; }
    }
    return best;
  };
  View.prototype.setHover = function (n, src) {
    if (n === this.hover) return;
    this.hover = n;
    if (!this.drag) this.c.style.cursor = n ? 'pointer' : 'grab';
    if (this.o.onHover) this.o.onHover(n, src);
    this.loop();
  };
  View.prototype.hoverAt = function (x, y) { this.setHover(this.at(x, y), 'pointer'); };
  View.prototype.setSel = function (n, src) {        // Auswahl per Tippen/Tastatur: bleibt, bis etwas anderes gewaehlt wird
    if (n === this.sel) return;
    this.sel = n; if (this.o.onSelect) this.o.onSelect(n, src); this.loop();
  };
  // Kamera: weich zu einem Ziel (250 ms), ohne Bewegung sofort; waehrend die Simulation einpendelt, folgt sie dem Graph
  View.prototype.fitTarget = function () {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    this.nodes.forEach(function (n) { if (n.x < x0) x0 = n.x; if (n.y < y0) y0 = n.y; if (n.x > x1) x1 = n.x; if (n.y > y1) y1 = n.y; });
    var pad = Math.min(90, Math.min(this.w, this.h) * 0.12), padX = pad + Math.min(50, this.w * 0.08);   // seitlich Platz fuer Beschriftungen
    var k = Math.max(0.04, Math.min((this.w - 2 * padX) / (x1 - x0 + 1), (this.h - 2 * pad) / (y1 - y0 + 1), 2));
    return { k: k, tx: this.w / 2 - (x0 + x1) / 2 * k, ty: this.h / 2 - (y0 + y1) / 2 * k };
  };
  View.prototype.camTo = function (tg, instant) {
    if (instant || RM.matches || !this.w) { this.cam = null; this.k = tg.k; this.tx = tg.tx; this.ty = tg.ty; this.draw(); return; }
    this.cam = { f: { k: this.k, tx: this.tx, ty: this.ty }, t: tg, p: 0 }; this.loop();
  };
  View.prototype.camStep = function (dt, sim) {
    if (this.cam) {
      var c = this.cam; c.p = Math.min(1, c.p + dt / 260); var a = ease(c.p);
      // Zoom logarithmisch mischen, damit die Bewegung gleichmaessig wirkt
      var k = Math.exp(Math.log(c.f.k) + (Math.log(c.t.k) - Math.log(c.f.k)) * a);
      var wx = this.w / 2, wy = this.h / 2;
      var cx0 = (wx - c.f.tx) / c.f.k, cy0 = (wy - c.f.ty) / c.f.k, cx1 = (wx - c.t.tx) / c.t.k, cy1 = (wy - c.t.ty) / c.t.k;
      var cx = cx0 + (cx1 - cx0) * a, cy = cy0 + (cy1 - cy0) * a;
      this.k = k; this.tx = wx - cx * k; this.ty = wy - cy * k;
      if (c.p >= 1) this.cam = null;
      return true;
    }
    if (this.autoFit && sim) {
      var t = this.fitTarget();
      if (this.alpha > 0.03) {
        var f = Math.min(1, dt / 120) * 0.5;
        this.k += (t.k - this.k) * f; this.tx += (t.tx - this.tx) * f; this.ty += (t.ty - this.ty) * f;
        return false;
      }
      this.autoFit = false; this.camTo(t); return !!this.cam;      // eingependelt: zum Schluss genau einpassen
    }
    return false;
  };
  View.prototype.user = function () { this.autoFit = false; this.cam = null; };      // Person bewegt die Ansicht selbst
  View.prototype.center = function (n, zoom, instant) {
    this.user(); var k = zoom || this.k;
    this.camTo({ k: k, tx: this.w / 2 - n.x * k, ty: this.h / 2 - n.y * k }, instant);
  };
  // Handy: nach dem Antippen Knoten + Nachbarn einpassen - nur hineinzoomen, nie heraus (Ueberblick bleibt erreichbar)
  View.prototype.zoomTo = function (n) {
    var adj = this.adj[n.id] || {}, x0 = n.x, y0 = n.y, x1 = n.x, y1 = n.y;
    this.nodes.forEach(function (m) { if (adj[m.id]) { if (m.x < x0) x0 = m.x; if (m.y < y0) y0 = m.y; if (m.x > x1) x1 = m.x; if (m.y > y1) y1 = m.y; } });
    var k = Math.min((this.w - 140) / (x1 - x0 + 1), (this.h - 260) / (y1 - y0 + 1), 1.6);
    if (k <= this.k * 1.1) return;
    this.user(); this.camTo({ k: k, tx: this.w / 2 - (x0 + x1) / 2 * k, ty: (this.h - 90) / 2 - (y0 + y1) / 2 * k });
  };
  View.prototype.reveal = function (n) {             // Tastatur: ausgewaehlten Knoten sichtbar halten
    var sx = this.tx + n.x * this.k, sy = this.ty + n.y * this.k, m = 60;
    if (sx < m || sy < m || sx > this.w - m || sy > this.h - m) this.center(n, Math.max(this.k, 0.6));
  };
  View.prototype.fit = function (instant) {
    if (!this.nodes.length || !this.w) return;
    this.camTo(this.fitTarget(), instant);
  };
  View.prototype.zoomAt = function (x, y, f) {
    this.user();
    var k = Math.min(8, Math.max(0.04, this.k * f)); f = k / this.k;
    this.tx = x - (x - this.tx) * f; this.ty = y - (y - this.ty) * f; this.k = k;
    if (this.mp) this.hoverAt(this.mp.x, this.mp.y);
    this.draw();
  };
  // Tastatur: Pfeile springen zum naechsten Knoten in der Richtung, Enter oeffnet, +/- zoomt, 0 passt ein
  View.prototype.step = function (dx, dy) {
    var cur = this.sel || this.hover;
    if (!cur) {
      var fc = this.focus != null ? this.byId(this.focus) : null;
      cur = fc || this.nodes.slice().sort(function (a, b) { return b.deg - a.deg; })[0];
      if (cur) { this.setSel(cur, 'key'); this.reveal(cur); } return;
    }
    var best = null, bs = Infinity;
    this.nodes.forEach(function (n) {
      if (n === cur) return;
      var vx = n.x - cur.x, vy = n.y - cur.y, d = Math.sqrt(vx * vx + vy * vy) || 1, cos = (vx * dx + vy * dy) / d;
      if (cos < 0.35) return;
      var sc = d * (2.2 - cos * 1.2);
      if (sc < bs) { bs = sc; best = n; }
    });
    if (best) { this.setSel(best, 'key'); this.reveal(best); }
  };
  View.prototype.bind = function () {
    var self = this, c = this.c, pts = {}, pinch = null;
    function pos(e) { var r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    c.addEventListener('pointerdown', function (e) {
      if (e.button > 0) return;
      var p = pos(e); pts[e.pointerId] = p;
      try { c.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
      var ids = Object.keys(pts);
      if (ids.length === 2) {
        var a = pts[ids[0]], b = pts[ids[1]];
        self.user(); pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, k: self.k }; self.drag = null; return;
      }
      var touch = e.pointerType !== 'mouse';
      var n = self.at(p.x, p.y, touch ? 22 : 10);              // Finger: groessere Trefferflaeche
      self.drag = n ? { node: n, x: p.x, y: p.y, moved: false, touch: touch } : { pan: true, x: p.x, y: p.y, tx: self.tx, ty: self.ty, moved: false, touch: touch };
      if (n && !touch) self.setHover(n, 'pointer');
      c.style.cursor = 'grabbing';
    });
    c.addEventListener('pointermove', function (e) {
      var p = pos(e);
      if (e.pointerType === 'mouse') self.mp = p;
      if (pts[e.pointerId]) pts[e.pointerId] = p;
      if (pinch) {
        var ids = Object.keys(pts); if (ids.length < 2) return;
        var a = pts[ids[0]], b = pts[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        self.zoomAt(mx, my, pinch.k * d / pinch.d / self.k); return;
      }
      var dr = self.drag;
      if (dr) {
        if (!dr.moved && Math.abs(p.x - dr.x) + Math.abs(p.y - dr.y) > (dr.touch ? 8 : 4)) { dr.moved = true; self.user(); }
        if (!dr.moved) return;
        if (dr.node) { dr.node.x = (p.x - self.tx) / self.k; dr.node.y = (p.y - self.ty) / self.k; dr.node.vx = 0; dr.node.vy = 0; self.alpha = Math.max(self.alpha, 0.3); self.loop(); }
        else { self.tx = dr.tx + p.x - dr.x; self.ty = dr.ty + p.y - dr.y; self.draw(); }
        return;
      }
      if (e.pointerType === 'mouse') self.hoverAt(p.x, p.y);
    });
    function up(e) {
      delete pts[e.pointerId];
      if (pinch) { if (Object.keys(pts).length < 2) pinch = null; return; }
      var dr = self.drag; self.drag = null;
      if (!dr) return;
      if (dr.node && dr.moved && RM.matches) self.settle();
      if (!dr.moved) {
        if (dr.touch) {                                  // Finger: erst hervorheben (Karte mit "Öffnen"), nicht sofort wegspringen
          if (dr.node && self.sel === dr.node && self.o.onOpen) self.o.onOpen(dr.node);
          else self.setSel(dr.node || null, 'touch');
        } else if (dr.node && self.o.onOpen) self.o.onOpen(dr.node);
        else self.setSel(null);
      }
      if (e.pointerType === 'mouse') { var p = pos(e); self.mp = p; self.hoverAt(p.x, p.y); }
      c.style.cursor = self.hover ? 'pointer' : 'grab';
    }
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', function (e) { delete pts[e.pointerId]; pinch = null; self.drag = null; c.style.cursor = 'grab'; });
    c.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse' && !self.drag) { self.mp = null; self.setHover(null); } });
    c.addEventListener('wheel', function (e) {
      e.preventDefault(); var p = pos(e);
      // Trackpad-Pinch (ctrlKey) staerker gewichten, Mausrad sanft
      self.zoomAt(p.x, p.y, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
    }, { passive: false });
    c.addEventListener('keydown', function (e) {
      var k = e.key, s = self.sel || self.hover;
      if (k === 'ArrowLeft') self.step(-1, 0); else if (k === 'ArrowRight') self.step(1, 0);
      else if (k === 'ArrowUp') self.step(0, -1); else if (k === 'ArrowDown') self.step(0, 1);
      else if ((k === 'Enter' || k === ' ') && s) { if (self.o.onOpen) self.o.onOpen(s); }
      else if (k === '+' || k === '=') self.zoomAt(self.w / 2, self.h / 2, 1.25);
      else if (k === '-' || k === '_') self.zoomAt(self.w / 2, self.h / 2, 0.8);
      else if (k === '0') { self.user(); self.fit(); }
      else return;
      e.preventDefault();
    });
    if (RM.addEventListener) RM.addEventListener('change', function () { self.draw(); });
  };

  // --- Ueberlagerung: "Alle Seiten" und "Lokal" -----------------------------------------------------------------
  var FULL = null;
  function svg(d) {
    var s = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
    var t = document.createElement('template'); t.innerHTML = s; return t.content.firstChild;
  }
  var IC = {
    fit: '<path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><circle cx="12" cy="12" r="2.5"/>',
    gear: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    close: '<path d="M6 6l12 12M18 6L6 18"/>'
  };
  function iconBtn(cls, ic, label) {
    var b = el('button', 'icon-btn gr-ib ' + cls); b.type = 'button'; b.setAttribute('aria-label', label); b.title = label; b.appendChild(svg(IC[ic])); return b;
  }
  // open({mode: 'all'|'local', id}) - id = offene Seite (Mitte des lokalen Graphen, im ganzen Graph markiert)
  function open(o) {
    if (o == null || typeof o !== 'object') o = { id: o };
    var pageId = o.id != null && o.id >= 0 ? o.id : null, mode = o.mode === 'local' && pageId != null ? 'local' : 'all';
    if (FULL) { FULL.show(mode, pageId); return; }
    var P = prefs();
    var w = el('div', 'gr-full'); w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'true'); w.setAttribute('aria-labelledby', 'gr-title'); w.tabIndex = -1;
    var head = el('div', 'gr-head');
    var ttl = el('h2', 'gr-title', T('Graph')); ttl.id = 'gr-title'; head.appendChild(ttl);
    var seg = el('div', 'gr-seg'); seg.setAttribute('role', 'group'); seg.setAttribute('aria-label', T('Ansicht'));
    var bAll = el('button', 'gr-seg-b', T('Alle Seiten')), bLoc = el('button', 'gr-seg-b', T('Lokal'));
    bAll.type = bLoc.type = 'button'; bLoc.title = T('Die offene Seite und ihre Nachbarn');
    seg.appendChild(bAll); seg.appendChild(bLoc); head.appendChild(seg);
    var depW = el('label', 'gr-depth'); depW.appendChild(el('span', null, T('Tiefe')));
    var dep = el('select'); [1, 2, 3].forEach(function (d) { var op = el('option', null, String(d)); op.value = String(d); dep.appendChild(op); });
    dep.value = String(P.depth || 1); depW.appendChild(dep); head.appendChild(depW);
    var stat = el('span', 'gr-stat'); stat.setAttribute('aria-live', 'polite'); head.appendChild(stat);
    head.appendChild(el('span', 'spacer'));
    var sw = el('div', 'gr-searchw');
    var search = el('input', 'gr-search'); search.type = 'search'; search.placeholder = T('Seiten im Graph suchen'); search.setAttribute('aria-label', T('Seiten im Graph suchen'));
    var none = el('span', 'gr-none', T('Keine Treffer')); none.hidden = true;
    sw.appendChild(search); sw.appendChild(none); head.appendChild(sw);
    var fitB = iconBtn('gr-fit', 'fit', T('Einpassen')), tgl = iconBtn('gr-panel-btn', 'gear', T('Einstellungen')), x = iconBtn('gr-close', 'close', T('Schließen'));
    head.appendChild(fitB); head.appendChild(tgl); head.appendChild(x);
    var body = el('div', 'gr-body'), cv = el('canvas', 'gr-canvas'), panel = el('div', 'gr-panel');
    panel.id = 'gr-panel'; tgl.setAttribute('aria-controls', 'gr-panel'); panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', T('Einstellungen'));
    cv.setAttribute('role', 'application'); cv.setAttribute('aria-roledescription', T('Graph')); cv.tabIndex = 0;
    var msg = el('div', 'gr-msg'); msg.hidden = true;
    var legend = el('div', 'gr-legend'); legend.setAttribute('aria-hidden', 'true');
    var card = el('div', 'gr-card'); card.hidden = true;
    var cardT = el('span', 'gr-card-t'), cardS = el('span', 'gr-card-s'), cardO = el('button', 'gr-card-o', T('Öffnen')); cardO.type = 'button';
    var cardTx = el('span', 'gr-card-tx'); cardTx.appendChild(cardT); cardTx.appendChild(cardS); card.appendChild(cardTx); card.appendChild(cardO);
    var live = el('div', 'sr-only'); live.setAttribute('aria-live', 'polite');
    var hint = el('p', 'sr-only', T('Pfeiltasten wählen eine Seite, Enter öffnet sie, Plus und Minus zoomen, 0 passt ein.')); hint.id = 'gr-kbd';
    cv.setAttribute('aria-describedby', 'gr-kbd');
    body.appendChild(cv); body.appendChild(msg); body.appendChild(legend); body.appendChild(card); body.appendChild(panel); body.appendChild(live); body.appendChild(hint);
    w.appendChild(head); w.appendChild(body);
    document.body.appendChild(w);
    document.body.classList.add('gr-open');
    var back = document.activeElement;
    var S = { mode: mode, id: pageId, g: null };
    function openNode(n) {
      if (!n) return;
      if (n.tag) { search.value = n.title; apply(); return; }
      close(); window.HKPages.k.open(n.id);
    }
    function describe(n) {
      if (!n) return '';
      var c = (view.adj[n.id] ? Object.keys(view.adj[n.id]).length : 0);
      return T('{n} {n:Verbindung|Verbindungen}', { n: c });
    }
    var view = new View(cv, {
      onOpen: openNode,
      onSelect: function (n, src) {
        card.hidden = !n; w.classList.toggle('gr-has-card', !!n);
        if (!n) return;
        cardT.textContent = (n.icon && n.icon.length <= 4 ? n.icon + ' ' : '') + n.title; cardS.textContent = describe(n);
        cardO.textContent = n.tag ? T('Filtern') : T('Öffnen');
        if (src === 'key') live.textContent = n.title + ', ' + describe(n);
        if (src === 'touch') view.zoomTo(n);
      }
    });
    view.focus = pageId;
    cardO.addEventListener('click', function () { openNode(view.sel); });

    function setPanel(open, save) {
      panel.hidden = !open; tgl.setAttribute('aria-expanded', String(open)); tgl.classList.toggle('on', open);
      w.classList.toggle('gr-panel-open', open);
      if (save) { P.panel = open; savePrefs(P); }
    }
    var narrow = window.matchMedia && matchMedia('(max-width: 720px)').matches;
    if (narrow) { fitB.classList.add('gr-fit-float'); body.appendChild(fitB); }   // Handy: wie "Neu zentrieren" in Karten-Apps
    setPanel(!narrow && !!P.panel, false);
    tgl.addEventListener('click', function () { setPanel(panel.hidden, !narrow); if (!panel.hidden) { var f = panel.querySelector('summary'); if (f && narrow) f.focus(); } });

    // Einstellungen (wie Obsidian): Filter, Gruppen, Anzeige, Kraefte
    function sec(title, open) {
      var d = el('details', 'gr-sec'); d.open = open !== false; d.appendChild(el('summary', null, title)); panel.appendChild(d); return d;
    }
    var ctl = [];
    function check(box, key, label) {
      var l = el('label', 'gr-check'), cb = el('input'); cb.type = 'checkbox'; cb.checked = !!P[key];
      cb.addEventListener('change', function () { P[key] = cb.checked; savePrefs(P); rebuild(true); });
      l.appendChild(el('span', null, label)); l.appendChild(cb); box.appendChild(l);
      ctl.push(function () { cb.checked = !!P[key]; });
    }
    function slider(box, key, label, min, max, step, cb2) {
      var l = el('label', 'gr-slider'); l.appendChild(el('span', null, label));
      var r = el('input'); r.type = 'range'; r.min = min; r.max = max; r.step = step; r.value = P[key];
      r.addEventListener('input', function () { P[key] = parseFloat(r.value); view.p = P; savePrefs(P); if (cb2) cb2(); else view.draw(); });
      l.appendChild(r); box.appendChild(l);
      ctl.push(function () { r.value = P[key]; });
    }
    var f = sec(T('Filter'));
    check(f, 'link', T('Verweise im Text'));
    check(f, 'child', T('Unterseiten'));
    check(f, 'rel', T('Datenbank-Relationen'));
    check(f, 'tags', T('Tags als Knoten'));
    check(f, 'rows', T('Datenbank-Einträge'));
    check(f, 'orphans', T('Seiten ohne Verbindung'));
    var g = sec(T('Gruppen'), !!P.groups.length);
    var glist = el('div', 'gr-groups'); g.appendChild(glist);
    var ng = el('button', 'btn gr-newgroup', '+ ' + T('Neue Gruppe')); ng.type = 'button'; g.appendChild(ng);
    g.appendChild(el('p', 'gr-hint', T('Ausdruck: Text im Titel, tag:Name oder path:Seitentitel (Seite mit allen Unterseiten)')));
    function drawGroups() {
      glist.textContent = '';
      P.groups.forEach(function (gr, i) {
        var row = el('div', 'gr-group');
        var c = el('input', 'gr-gc'); c.type = 'color'; c.value = gr.color; c.setAttribute('aria-label', T('Farbe der Gruppe'));
        var q = el('input', 'gr-gq'); q.type = 'text'; q.value = gr.q || ''; q.placeholder = 'path:…'; q.setAttribute('aria-label', T('Ausdruck der Gruppe'));
        var del = el('button', 'icon-btn gr-gdel'); del.type = 'button'; del.setAttribute('aria-label', T('Gruppe entfernen')); del.appendChild(svg(IC.close));
        q.addEventListener('change', function () { gr.q = q.value; savePrefs(P); rebuild(true); });
        c.addEventListener('input', function () { gr.color = c.value; savePrefs(P); rebuild(true); });
        del.addEventListener('click', function () { P.groups.splice(i, 1); savePrefs(P); drawGroups(); rebuild(true); ng.focus(); });
        row.appendChild(c); row.appendChild(q); row.appendChild(del); glist.appendChild(row);
      });
    }
    ng.addEventListener('click', function () { P.groups.push({ q: '', color: GCOL[P.groups.length % GCOL.length] }); savePrefs(P); drawGroups(); var qs = glist.querySelectorAll('.gr-gq'); if (qs.length) qs[qs.length - 1].focus(); });
    drawGroups();
    var dsp = sec(T('Anzeige'), false);
    check(dsp, 'arrows', T('Pfeile'));
    slider(dsp, 'labels', T('Text einblenden ab Zoomstufe'), 0.2, 3, 0.1);
    slider(dsp, 'size', T('Knotengröße'), 0.4, 3, 0.1);
    slider(dsp, 'width', T('Linienstärke'), 0.2, 3, 0.1);
    var frc = sec(T('Kräfte'), false);
    function reheat() { view.heat(0.4); }
    slider(frc, 'center', T('Zug zur Mitte'), 0, 1, 0.05, reheat);
    slider(frc, 'repel', T('Abstoßung'), 0, 1, 0.05, reheat);
    slider(frc, 'spring', T('Linienkraft'), 0, 1, 0.05, reheat);
    slider(frc, 'dist', T('Linienlänge'), 0, 1, 0.05, reheat);
    var pf = el('div', 'gr-panel-foot');
    var anim = el('button', 'btn', T('Neu anordnen')); anim.type = 'button'; pf.appendChild(anim);
    var rst = el('button', 'btn', T('Standard')); rst.type = 'button'; rst.title = T('Filter, Anzeige und Kräfte zurücksetzen'); pf.appendChild(rst);
    panel.appendChild(pf);
    anim.addEventListener('click', function () { rebuild(false); });
    function resetAll() { RESET.forEach(function (k) { P[k] = DEF[k]; }); savePrefs(P); ctl.forEach(function (fn) { fn(); }); rebuild(true); }
    rst.addEventListener('click', resetAll);

    function apply() {
      var q = search.value.trim().toLowerCase();
      none.hidden = true;
      if (!q) { view.match = null; view.loop(); return null; }
      var m = {}, first = null, cnt = 0;
      view.nodes.forEach(function (n) { if (n.title.toLowerCase().indexOf(q) >= 0) { m[n.id] = true; cnt++; if (!first) first = n; } });
      view.match = m; none.hidden = !!first;
      if (first) view.center(first, Math.max(view.k, 1)); else view.loop();
      live.textContent = first ? T('{n} Treffer', { n: cnt }) : T('Keine Treffer');
      return first;
    }
    search.addEventListener('input', apply);
    search.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();                          // sonst "klickt" Enter danach den Knopf, der den Fokus zurueckbekommt
      var n = apply(); if (n && !n.tag) { close(); window.HKPages.k.open(n.id); }
    });
    function title(id) { var n = DATA && DATA.nodes.filter(function (x) { return x[0] === id; })[0]; return n ? n[1] || T('Unbenannt') : ''; }
    function showMsg(kind, text, sub, btn, fn) {
      msg.hidden = !kind; msg.className = 'gr-msg' + (kind ? ' gr-msg-' + kind : ''); msg.textContent = '';
      if (!kind) return;
      if (kind === 'load') msg.appendChild(el('span', 'gr-spin'));
      msg.appendChild(el('p', 'gr-msg-t', text));
      if (sub) msg.appendChild(el('p', 'gr-msg-s', sub));
      if (btn) { var b = el('button', 'btn', btn); b.type = 'button'; b.addEventListener('click', fn); msg.appendChild(b); }
    }
    function drawLegend(gph) {
      legend.textContent = '';
      var P2 = view.pal, has = {};
      gph.nodes.forEach(function (n) { has[n.tag ? 'tag' : n.kind] = true; if (!n.own) has.shared = true; });
      var items = [['page', P2.page, T('Seite')], ['database', P2.db, T('Datenbank')], ['row', P2.row, T('Eintrag')], ['tag', P2.tag, T('Tag')]];
      items.forEach(function (it) {
        if (!has[it[0]]) return;
        var s = el('span', 'gr-lg'), d = el('i'); d.style.background = it[1]; s.appendChild(d); s.appendChild(document.createTextNode(it[2])); legend.appendChild(s);
      });
      if (view.focus != null && view.byId(view.focus)) { var s = el('span', 'gr-lg gr-lg-cur'), d = el('i'); s.appendChild(d); s.appendChild(document.createTextNode(T('Offene Seite'))); legend.appendChild(s); }
      legend.hidden = legend.childNodes.length < 2;
    }
    function paintMode() {
      bAll.setAttribute('aria-pressed', String(S.mode === 'all')); bLoc.setAttribute('aria-pressed', String(S.mode === 'local'));
      bLoc.disabled = S.id == null; depW.hidden = S.mode !== 'local';
      w.classList.toggle('gr-local-mode', S.mode === 'local');
    }
    function rebuild(keep) {
      if (!DATA) return;
      view.p = P;
      var loc = S.mode === 'local';
      var gph = build(DATA, P, loc ? { focus: S.id, depth: parseInt(dep.value, 10) || 1 } : null);
      view.focus = S.id;
      view.set(gph, keep);
      S.g = gph; drawLegend(gph);
      var nm = gph.nodes.length, ed = gph.edges.length;
      var counts = T('{n} {n:Seite|Seiten}', { n: nm }) + ' · ' + T('{n} {n:Verbindung|Verbindungen}', { n: ed });
      stat.textContent = (loc ? '' : counts) + (DATA.truncated && !loc ? ' · ' + T('gekürzt') : '');
      if (loc) { stat.textContent = ''; stat.appendChild(el('b', null, title(S.id))); stat.appendChild(document.createTextNode(' · ' + counts)); }
      cv.setAttribute('aria-label', loc ? T('Lokaler Graph von „{name}“: {n} verbundene {n:Seite|Seiten}', { name: title(S.id), n: Math.max(0, nm - 1) })
        : T('Graph mit {n} Seiten und {m} Verbindungen. Mit der Suche eine Seite finden und mit Enter öffnen.', { n: nm, m: ed }));
      if (!DATA.nodes.length) showMsg('empty', T('Noch keine Seiten'), T('Lege Seiten an und verlinke sie untereinander – hier siehst du dann, wie alles zusammenhängt.'));
      else if (loc && gph.missing) showMsg('empty', T('Diese Seite ist nicht im Graph'), T('Archivierte Seiten, Vorlagen und Seiten anderer Server erscheinen hier nicht.'), T('Alle Seiten'), function () { show('all', S.id); });
      else if (!nm) showMsg('empty', T('Keine Seiten passen zu den Filtern'), null, T('Filter zurücksetzen'), resetAll);
      else if (loc && nm === 1) showMsg('hint', T('„{name}“ ist noch mit keiner anderen Seite verbunden', { name: title(S.id) }), T('Verweise im Text, Unterseiten und Relationen erscheinen hier als Linien.'));
      else showMsg(null);
      apply();
    }
    function show(m, id) {
      if (id !== undefined) S.id = id;
      S.mode = m === 'local' && S.id != null ? 'local' : 'all';
      paintMode(); view.setSel(null);
      if (DATA) rebuild(false);
    }
    bAll.addEventListener('click', function () { show('all'); });
    bLoc.addEventListener('click', function () { show('local'); });
    dep.addEventListener('change', function () { P.depth = parseInt(dep.value, 10) || 1; savePrefs(P); rebuild(true); view.autoFit = true; view.heat(0.6); });
    function close() {
      if (!FULL) return;
      FULL = null; cancelAnimationFrame(view.raf); if (view.ro) view.ro.disconnect();
      document.removeEventListener('keydown', onKey, true); mq.removeEventListener && mq.removeEventListener('change', onTheme);
      w.remove(); document.body.classList.remove('gr-open');
      if (back && back.isConnected) try { back.focus(); } catch (e) { /* egal */ }
    }
    function focusables() { return [].filter.call(w.querySelectorAll('button, input, select, summary, canvas, [tabindex]'), function (x) { return !x.disabled && x.offsetParent !== null && x.tabIndex >= 0; }); }
    function onKey(e) {
      if (e.key === 'Escape' && !e.defaultPrevented) {
        e.preventDefault();
        if (view.sel) { view.setSel(null); return; }           // erst die Auswahl aufheben, dann schliessen
        if (!panel.hidden && narrow) { setPanel(false); tgl.focus(); return; }
        close(); return;
      }
      if (e.key === 'Tab') {                                    // Fokus bleibt im Dialog
        var fs = focusables(); if (!fs.length) return;
        var i = fs.indexOf(document.activeElement);
        if (e.shiftKey && i <= 0) { e.preventDefault(); fs[fs.length - 1].focus(); }
        else if (!e.shiftKey && i === fs.length - 1) { e.preventDefault(); fs[0].focus(); }
      }
    }
    document.addEventListener('keydown', onKey, true);
    var mq = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
    function onTheme() { view.pal = palette(); if (S.g) drawLegend(S.g); view.draw(); }
    if (mq && mq.addEventListener) mq.addEventListener('change', onTheme);
    x.addEventListener('click', close);
    fitB.addEventListener('click', function () { view.user(); view.fit(); });
    FULL = { show: show, close: close, view: view };
    paintMode();
    view.resize();
    function start(force) {
      showMsg('load', T('Graph wird geladen …'));
      stat.textContent = '';
      load(force).then(function () { showMsg(null); rebuild(false); if (narrow) w.focus(); else search.focus(); })
        .catch(function (e) { showMsg('error', T('Der Graph konnte nicht geladen werden.'), e && e.message, T('Erneut versuchen'), function () { start(true); }); });
    }
    start(true);
  }

  // --- Einstiege: Seitenleiste, Seitenmenue "…", "Verlinkt von", Befehle ------------------------------------------
  function curId() { var c = window.HKPages && HKPages.k.cur(); return c && c.id != null && c.id >= 0 ? c.id : null; }
  function openLocal() { open({ mode: 'local', id: curId() }); }
  function addBacklinkBtn() {                       // kleiner Knopf rechts neben "Verlinkt von"
    var sec = document.getElementById('pg-backlinks'), h = sec && sec.querySelector('h3');
    if (!h || sec.querySelector('.bl-graph')) return;
    var hd = el('div', 'bl-head'); h.parentNode.insertBefore(hd, h); hd.appendChild(h);
    var b = el('button', 'bl-graph', T('Lokaler Graph')); b.type = 'button'; b.id = 'pg-local-graph';
    b.insertBefore(el('span', 'bl-graph-ic', '✳'), b.firstChild); b.firstChild.setAttribute('aria-hidden', 'true');
    b.addEventListener('click', openLocal); hd.appendChild(b);
  }
  addBacklinkBtn();

  window.HKGraph = {
    open: open, openLocal: openLocal, build: build, View: View,
    // static/pages.js: Eintrag im Seitenmenue "…"
    menu: function (m) { if (curId() != null) m.appendChild(HK.menuItem(T('Lokaler Graph'), openLocal, { icon: '✳' })); },
    current: function () { return FULL; }
  };
  var btn = document.getElementById('pg-graph-btn');
  if (btn) btn.addEventListener('click', function () { open({ mode: 'all', id: curId() }); });
})();
