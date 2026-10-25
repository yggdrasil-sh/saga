// Saga - Graph der Seiten (2.5, wie Obsidian): ganzer Graph (Seitenleiste "Graph") und lokaler Graph je Seite (unter
// "Verlinkt von"). Knoten = Seiten (eigene und fuer mich freigegebene), Kanten = Verweise im Text, Unterseiten,
// Datenbank-Relationen und Tags (Tag-Knoten). Daten: GET /api/graph (saga/graph.py). Eigene, schlanke Kraefte-Simulation
// auf Canvas (Barnes-Hut fuer die Abstossung) - keine Fremdbibliothek (CSP), fluessig bis einige tausend Knoten.
(function () {
  'use strict';
  if (!window.HK || !document.body.classList.contains('notes-page')) return;
  var HK = window.HK, el = HK.el, T = window.T;
  var KEY = 'saga-graph';
  var DEF = { link: true, child: true, rel: true, tags: false, orphans: true, rows: true, arrows: false, labels: 1.1,
              size: 1, width: 1, center: 0.5, repel: 0.6, spring: 0.5, dist: 0.5, groups: [], depth: 1 };
  function prefs() {
    var p = {}; try { p = JSON.parse(HK.store(KEY) || '{}') || {}; } catch (e) { p = {}; }
    var o = {}; Object.keys(DEF).forEach(function (k) { o[k] = p[k] != null ? p[k] : DEF[k]; });
    if (!Array.isArray(o.groups)) o.groups = [];
    return o;
  }
  function savePrefs(p) { HK.store(KEY, JSON.stringify(p)); }
  var GCOL = ['#e5484d', '#30a46c', '#e93d82', '#3e9bff', '#f5a524', '#8e4ec6', '#12a594', '#d6409f'];

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

  function css(name, fb) { var v = getComputedStyle(document.body).getPropertyValue(name).trim(); return v || fb; }

  // Graph fuer die Anzeige bauen: Filter, Tag-Knoten, Gruppenfarben, lokal (BFS ab einer Seite)
  function build(d, p, opt) {
    var byId = {}, nodes = [], edges = [];
    d.nodes.forEach(function (n) {
      if (!p.rows && n[3] === 'row') return;
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
    if (opt && opt.focus != null && byId[opt.focus]) {       // lokal: nur Nachbarn bis zur gewaehlten Tiefe
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

  // --- Simulation + Zeichnen ---------------------------------------------------------------------------------------
  function View(canvas, opts) {
    this.c = canvas; this.ctx = canvas.getContext('2d'); this.o = opts || {};
    this.nodes = []; this.edges = []; this.k = 1; this.tx = 0; this.ty = 0; this.alpha = 0; this.hover = null; this.drag = null;
    this.p = prefs(); this.raf = 0; this.focus = null; this.match = null;
    var self = this;
    this.ro = window.ResizeObserver ? new ResizeObserver(function () { self.resize(); }) : null;
    if (this.ro) this.ro.observe(canvas); else window.addEventListener('resize', function () { self.resize(); });
    this.bind();
  }
  View.prototype.resize = function () {
    var r = this.c.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    if (!r.width || !r.height) return;
    var first = !this.w;
    this.w = r.width; this.h = r.height; this.c.width = Math.round(r.width * dpr); this.c.height = Math.round(r.height * dpr); this.dpr = dpr;
    if (first) { this.tx = this.w / 2; this.ty = this.h / 2; }
    this.draw();
  };
  View.prototype.set = function (g, keep) {
    var old = {}; (keep ? this.nodes : []).forEach(function (n) { old[n.id] = n; });
    var R = Math.sqrt(g.nodes.length + 1) * 18;
    g.nodes.forEach(function (n, i) {
      var o = old[n.id];
      if (o) { n.x = o.x; n.y = o.y; n.vx = o.vx; n.vy = o.vy; }
      else { var a = i * 2.399963, rr = R * Math.sqrt((i + 0.5) / g.nodes.length); n.x = Math.cos(a) * rr; n.y = Math.sin(a) * rr; n.vx = 0; n.vy = 0; }
    });
    this.nodes = g.nodes; this.edges = g.edges;
    this.adj = {}; var adj = this.adj;
    g.edges.forEach(function (e) { (adj[e.s.id] = adj[e.s.id] || {})[e.t.id] = true; (adj[e.t.id] = adj[e.t.id] || {})[e.s.id] = true; });
    this.heat(keep ? 0.5 : 1);
  };
  View.prototype.heat = function (a) {
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.alpha = Math.max(this.alpha, a);
    if (reduce) { for (var i = 0; i < 300 && this.alpha > 0.002; i++) this.tick(); this.alpha = 0; this.draw(); return; }
    this.loop();
  };
  View.prototype.loop = function () {
    if (this.raf) return;
    var self = this;
    (function step() {
      self.raf = 0;
      if (!self.c.isConnected) return;
      if (self.alpha > 0.002 || self.drag) { self.tick(); self.tick(); self.raf = requestAnimationFrame(step); }
      self.draw();
    })();
  };
  // Barnes-Hut-Quadtree fuer die Abstossung
  function quad(nodes) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    nodes.forEach(function (n) { if (n.x < x0) x0 = n.x; if (n.y < y0) y0 = n.y; if (n.x > x1) x1 = n.x; if (n.y > y1) y1 = n.y; });
    var s = Math.max(x1 - x0, y1 - y0) + 1, root = { x: x0, y: y0, s: s, m: 0, cx: 0, cy: 0, n: null, k: null };
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
      if (!q.k[i]) q.k[i] = { x: q.x + (i & 1 ? h : 0), y: q.y + (i & 2 ? h : 0), s: h, m: 0, cx: 0, cy: 0, n: null, k: null };
      ins(q.k[i], n, d + 1);
      q.cx = (q.cx * q.m + n.x) / (q.m + 1); q.cy = (q.cy * q.m + n.y) / (q.m + 1); q.m++;
    }
    nodes.forEach(function (n) { ins(root, n, 0); });
    return root;
  }
  View.prototype.tick = function () {
    var p = this.p, a = this.alpha, nodes = this.nodes, n = nodes.length;
    if (!n) return;
    var rep = 30 + p.repel * 600, spring = 0.02 + p.spring * 0.25, len = 20 + p.dist * 120, cen = 0.002 + p.center * 0.03;
    var tree = quad(nodes);
    nodes.forEach(function (nd) {
      (function walk(q) {
        if (!q || !q.m) return;
        var dx = q.cx - nd.x, dy = q.cy - nd.y, d2 = dx * dx + dy * dy + 0.01;
        if (q.n === nd && !q.k) return;
        if (!q.k || (q.s * q.s) / d2 < 0.81) {         // weit weg (theta 0.9): ganze Zelle als ein Koerper
          if (d2 > 1e6) return;
          var f = rep * q.m * a / d2;
          nd.vx -= dx * f; nd.vy -= dy * f;
          return;
        }
        q.k.forEach(walk);
      })(tree);
    });
    this.edges.forEach(function (e) {
      var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      var f = (d - len) / d * spring * a, bs = e.s.deg / (e.s.deg + e.t.deg);
      e.t.vx -= dx * f * bs; e.t.vy -= dy * f * bs; e.s.vx += dx * f * (1 - bs); e.s.vy += dy * f * (1 - bs);
    });
    var drag = this.drag && this.drag.node;
    nodes.forEach(function (nd) {
      nd.vx -= nd.x * cen * a; nd.vy -= nd.y * cen * a;
      if (nd === drag) { nd.vx = 0; nd.vy = 0; return; }
      nd.vx *= 0.6; nd.vy *= 0.6;
      if (nd.vx > 50) nd.vx = 50; else if (nd.vx < -50) nd.vx = -50;
      if (nd.vy > 50) nd.vy = 50; else if (nd.vy < -50) nd.vy = -50;
      nd.x += nd.vx; nd.y += nd.vy;
    });
    this.alpha *= 0.985;
  };
  View.prototype.rad = function (n) { return Math.max((2.5 + Math.sqrt(n.deg) * 1.6) * this.p.size * (n.tag ? 0.85 : 1), 2 / this.k); };   // weit herausgezoomt: mind. 2 px
  View.prototype.draw = function () {
    var ctx = this.ctx, w = this.w, h = this.h, k = this.k, self = this;
    if (!w) return;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var fg = css('--fg', '#ddd'), muted = css('--muted', '#888'), acc = css('--accent', '#c9a45c'), bg = css('--bg', '#111');
    var hov = this.hover, adj = hov ? (this.adj[hov.id] || {}) : null, match = this.match;
    ctx.save(); ctx.translate(this.tx, this.ty); ctx.scale(k, k);
    ctx.lineWidth = Math.max(0.4, 0.8 * this.p.width) / Math.sqrt(k);
    this.edges.forEach(function (e) {
      var on = hov && (e.s === hov || e.t === hov);
      ctx.strokeStyle = on ? acc : muted; ctx.globalAlpha = hov ? (on ? 0.9 : 0.08) : (e.k === 't' ? 0.18 : 0.3);
      if (e.k === 'r') ctx.setLineDash([3 / k, 3 / k]); else ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(e.s.x, e.s.y); ctx.lineTo(e.t.x, e.t.y); ctx.stroke();
      if (self.p.arrows && e.k !== 't') {
        var dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1, r = self.rad(e.t) + 2, s = 5 / Math.sqrt(k);
        var ex = e.t.x - dx / d * r, ey = e.t.y - dy / d * r;
        ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - dx / d * s - dy / d * s * 0.6, ey - dy / d * s + dx / d * s * 0.6);
        ctx.lineTo(ex - dx / d * s + dy / d * s * 0.6, ey - dy / d * s - dx / d * s * 0.6); ctx.closePath(); ctx.fillStyle = ctx.strokeStyle; ctx.fill();
      }
    });
    ctx.setLineDash([]);
    var lab = [];
    this.nodes.forEach(function (n) {
      var r = self.rad(n), dim = hov && n !== hov && !adj[n.id];
      if (match && !match[n.id]) dim = true;
      ctx.globalAlpha = dim ? 0.15 : (n.own ? 1 : 0.7);
      ctx.fillStyle = n.id === self.focus ? acc : n.group || (n.tag ? '#5fae7a' : n.kind === 'database' ? '#8e7cc3' : n.kind === 'row' ? '#7d9fc8' : '#8aa2c4');
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832); ctx.fill();
      if (n === hov || n.id === self.focus) { ctx.lineWidth = 2 / k; ctx.strokeStyle = acc; ctx.stroke(); }
      if (!dim && (n === hov || (adj && adj[n.id]) || self.nodes.length <= 40 || k * (1 + Math.sqrt(n.deg) * 0.3) > self.p.labels * 1.6 || (match && match[n.id]))) lab.push(n);   // kleine Graphen: immer beschriftet
    });
    ctx.globalAlpha = 1; ctx.font = (11 / k) + 'px system-ui, sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.lineWidth = 3 / k; ctx.strokeStyle = bg; ctx.fillStyle = fg;
    lab.slice(0, 400).forEach(function (n) {
      var t = (n.icon && n.icon.length <= 4 ? n.icon + ' ' : '') + (n.title.length > 40 ? n.title.slice(0, 39) + '…' : n.title);
      ctx.strokeText(t, n.x, n.y + self.rad(n) + 2 / k); ctx.fillText(t, n.x, n.y + self.rad(n) + 2 / k);
    });
    ctx.restore();
  };
  View.prototype.at = function (cx, cy) {
    var x = (cx - this.tx) / this.k, y = (cy - this.ty) / this.k, best = null, bd = Infinity, self = this;
    this.nodes.forEach(function (n) {
      var dx = n.x - x, dy = n.y - y, d = dx * dx + dy * dy, r = self.rad(n) + 4 / self.k;
      if (d < r * r && d < bd) { bd = d; best = n; }
    });
    return best;
  };
  View.prototype.center = function (n, zoom) {
    if (zoom) this.k = zoom;
    this.tx = this.w / 2 - n.x * this.k; this.ty = this.h / 2 - n.y * this.k; this.draw();
  };
  View.prototype.fit = function () {
    if (!this.nodes.length || !this.w) return;
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    this.nodes.forEach(function (n) { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); });
    var k = Math.min(this.w / (x1 - x0 + 80), this.h / (y1 - y0 + 80), 2.5);
    this.k = Math.max(0.05, k); this.tx = this.w / 2 - (x0 + x1) / 2 * this.k; this.ty = this.h / 2 - (y0 + y1) / 2 * this.k; this.draw();
  };
  View.prototype.bind = function () {
    var self = this, c = this.c, pts = {}, moved = false, pinch = null;
    function pos(e) { var r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    c.addEventListener('pointerdown', function (e) {
      var p = pos(e); pts[e.pointerId] = p; moved = false;
      try { c.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
      var ids = Object.keys(pts);
      if (ids.length === 2) {
        var a = pts[ids[0]], b = pts[ids[1]];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: self.k }; self.drag = null; return;
      }
      var n = self.at(p.x, p.y);
      self.drag = n ? { node: n, x: p.x, y: p.y } : { pan: true, x: p.x, y: p.y, tx: self.tx, ty: self.ty };
      if (n) self.heat(0.3);
    });
    c.addEventListener('pointermove', function (e) {
      var p = pos(e);
      if (pts[e.pointerId]) pts[e.pointerId] = p;
      if (pinch) {
        var ids = Object.keys(pts); if (ids.length < 2) return;
        var a = pts[ids[0]], b = pts[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        self.zoomAt(mx, my, pinch.k * d / pinch.d / self.k); return;
      }
      var dr = self.drag;
      if (dr) {
        if (Math.abs(p.x - dr.x) + Math.abs(p.y - dr.y) > 4) moved = true;
        if (dr.node && moved) { dr.node.x = (p.x - self.tx) / self.k; dr.node.y = (p.y - self.ty) / self.k; self.alpha = Math.max(self.alpha, 0.2); self.loop(); }
        else if (dr.pan) { self.tx = dr.tx + p.x - dr.x; self.ty = dr.ty + p.y - dr.y; self.draw(); }
        return;
      }
      var h = self.at(p.x, p.y);
      if (h !== self.hover) { self.hover = h; c.style.cursor = h ? 'pointer' : 'grab'; c.title = h ? h.title : ''; self.draw(); }
    });
    function up(e) {
      delete pts[e.pointerId];
      if (pinch) { if (Object.keys(pts).length < 2) pinch = null; return; }
      var dr = self.drag; self.drag = null;
      if (dr && dr.node && !moved && self.o.onOpen) self.o.onOpen(dr.node);
    }
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', function (e) { delete pts[e.pointerId]; pinch = null; self.drag = null; });
    c.addEventListener('pointerleave', function () { if (self.hover && !self.drag) { self.hover = null; self.draw(); } });
    c.addEventListener('wheel', function (e) {
      if (self.o.wheelNeedsCtrl && !(e.ctrlKey || e.metaKey)) return;   // lokaler Graph: Seite scrollt normal weiter
      e.preventDefault(); var p = pos(e); self.zoomAt(p.x, p.y, Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });
  };
  View.prototype.zoomAt = function (x, y, f) {
    var k = Math.min(8, Math.max(0.04, this.k * f)); f = k / this.k;
    this.tx = x - (x - this.tx) * f; this.ty = y - (y - this.ty) * f; this.k = k; this.draw();
  };

  // --- Ganzer Graph (Ueberlagerung) ------------------------------------------------------------------------------
  var FULL = null;
  function open(focusId) {
    if (FULL) { FULL.focus(focusId); return; }
    var P = prefs();
    var w = el('div', 'gr-full'); w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'true'); w.setAttribute('aria-label', T('Graph'));
    var head = el('div', 'gr-head');
    head.appendChild(el('h2', 'gr-title', T('Graph')));
    var stat = el('span', 'gr-stat'); head.appendChild(stat);
    var sp = el('span', 'spacer'); head.appendChild(sp);
    var tgl = el('button', 'btn gr-panel-btn', T('Einstellungen')); tgl.type = 'button'; tgl.setAttribute('aria-expanded', 'true'); head.appendChild(tgl);
    var fitB = el('button', 'btn', T('Einpassen')); fitB.type = 'button'; head.appendChild(fitB);
    var x = el('button', 'icon-btn gr-close', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); head.appendChild(x);
    var body = el('div', 'gr-body'), cv = el('canvas', 'gr-canvas'), panel = el('div', 'gr-panel');
    cv.setAttribute('role', 'img'); cv.tabIndex = 0;
    body.appendChild(cv); body.appendChild(panel); w.appendChild(head); w.appendChild(body);
    document.body.appendChild(w);
    var back = document.activeElement;
    var view = new View(cv, { onOpen: function (n) {
      if (n.tag) { search.value = n.title; apply(); return; }
      close(); window.HKPages.k.open(n.id);
    } });
    view.focus = focusId != null ? focusId : null;
    tgl.addEventListener('click', function () { panel.hidden = !panel.hidden; tgl.setAttribute('aria-expanded', String(!panel.hidden)); setTimeout(function () { view.resize(); }, 0); });
    if (window.matchMedia && matchMedia('(max-width: 720px)').matches) { panel.hidden = true; tgl.setAttribute('aria-expanded', 'false'); }

    // Seitenleiste: Filter, Gruppen, Anzeige, Kraefte
    function sec(title, open) {
      var d = el('details', 'gr-sec'); d.open = open !== false; d.appendChild(el('summary', null, title)); panel.appendChild(d); return d;
    }
    function check(box, key, label) {
      var l = el('label', 'gr-check'), cb = el('input'); cb.type = 'checkbox'; cb.checked = !!P[key];
      cb.addEventListener('change', function () { P[key] = cb.checked; savePrefs(P); rebuild(true); });
      l.appendChild(cb); l.appendChild(el('span', null, label)); box.appendChild(l);
    }
    function slider(box, key, label, min, max, step, cb2) {
      var l = el('label', 'gr-slider'); l.appendChild(el('span', null, label));
      var r = el('input'); r.type = 'range'; r.min = min; r.max = max; r.step = step; r.value = P[key];
      r.addEventListener('input', function () { P[key] = parseFloat(r.value); view.p = P; savePrefs(P); if (cb2) cb2(); else view.draw(); });
      l.appendChild(r); box.appendChild(l);
    }
    var f = sec(T('Filter'));
    var search = el('input', 'gr-search'); search.type = 'search'; search.placeholder = T('Seiten im Graph suchen'); search.setAttribute('aria-label', T('Seiten im Graph suchen'));
    f.appendChild(search);
    check(f, 'link', T('Verweise im Text'));
    check(f, 'child', T('Unterseiten'));
    check(f, 'rel', T('Datenbank-Relationen'));
    check(f, 'tags', T('Tags als Knoten'));
    check(f, 'rows', T('Datenbank-Einträge'));
    check(f, 'orphans', T('Seiten ohne Verbindung'));
    var g = sec(T('Gruppen'));
    var glist = el('div', 'gr-groups'); g.appendChild(glist);
    g.appendChild(el('p', 'gr-hint', T('Ausdruck: Text im Titel, tag:Name oder path:Seitentitel (Seite mit allen Unterseiten)')));
    var ng = el('button', 'btn gr-newgroup', T('Neue Gruppe')); ng.type = 'button'; g.appendChild(ng);
    function drawGroups() {
      glist.textContent = '';
      P.groups.forEach(function (gr, i) {
        var row = el('div', 'gr-group');
        var q = el('input', 'gr-gq'); q.type = 'text'; q.value = gr.q || ''; q.placeholder = 'path:…'; q.setAttribute('aria-label', T('Ausdruck der Gruppe'));
        var c = el('input', 'gr-gc'); c.type = 'color'; c.value = gr.color; c.setAttribute('aria-label', T('Farbe der Gruppe'));
        var del = el('button', 'icon-btn', '×'); del.type = 'button'; del.setAttribute('aria-label', T('Gruppe entfernen'));
        q.addEventListener('change', function () { gr.q = q.value; savePrefs(P); rebuild(true); });
        c.addEventListener('input', function () { gr.color = c.value; savePrefs(P); rebuild(true); });
        del.addEventListener('click', function () { P.groups.splice(i, 1); savePrefs(P); drawGroups(); rebuild(true); });
        row.appendChild(q); row.appendChild(c); row.appendChild(del); glist.appendChild(row);
      });
    }
    ng.addEventListener('click', function () { P.groups.push({ q: '', color: GCOL[P.groups.length % GCOL.length] }); savePrefs(P); drawGroups(); var qs = glist.querySelectorAll('.gr-gq'); if (qs.length) qs[qs.length - 1].focus(); });
    drawGroups();
    var dsp = sec(T('Anzeige'), false);
    check(dsp, 'arrows', T('Pfeile'));
    slider(dsp, 'labels', T('Schrift ab Zoomstufe'), 0.2, 3, 0.1);
    slider(dsp, 'size', T('Knotengröße'), 0.4, 3, 0.1);
    slider(dsp, 'width', T('Linienstärke'), 0.2, 3, 0.1);
    var frc = sec(T('Kräfte'), false);
    function reheat() { view.heat(0.4); }
    slider(frc, 'center', T('Zug zur Mitte'), 0, 1, 0.05, reheat);
    slider(frc, 'repel', T('Abstoßung'), 0, 1, 0.05, reheat);
    slider(frc, 'spring', T('Linienkraft'), 0, 1, 0.05, reheat);
    slider(frc, 'dist', T('Linienlänge'), 0, 1, 0.05, reheat);
    var anim = el('button', 'btn', T('Neu anordnen')); anim.type = 'button'; frc.appendChild(anim);
    anim.addEventListener('click', function () { rebuild(false); });

    function apply() {
      var q = search.value.trim().toLowerCase();
      if (!q) { view.match = null; view.draw(); return; }
      var m = {}, first = null;
      view.nodes.forEach(function (n) { if (n.title.toLowerCase().indexOf(q) >= 0) { m[n.id] = true; if (!first) first = n; } });
      view.match = m; if (first) view.center(first, Math.max(view.k, 1)); else view.draw();
      return first;
    }
    search.addEventListener('input', apply);
    search.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();                          // sonst "klickt" Enter danach den Knopf, der den Fokus zurueckbekommt
      var n = apply(); if (n && !n.tag) { close(); window.HKPages.k.open(n.id); }
    });
    function rebuild(keep) {
      if (!DATA) return;
      view.p = P;
      var gph = build(DATA, P);
      view.set(gph, keep);
      stat.textContent = T('{n} Seiten · {m} Verbindungen', { n: gph.nodes.length, m: gph.edges.length }) + (DATA.truncated ? ' · ' + T('gekürzt') : '');
      cv.setAttribute('aria-label', T('Graph mit {n} Seiten und {m} Verbindungen. Mit der Suche eine Seite finden und mit Enter öffnen.', { n: gph.nodes.length, m: gph.edges.length }));
      if (!keep) setTimeout(function () { view.fit(); }, 900);        // ganzer Graph im Blick; die offene Seite ist gold markiert
      apply();
    }
    function close() {
      if (!FULL) return;
      FULL = null; cancelAnimationFrame(view.raf); if (view.ro) view.ro.disconnect();
      document.removeEventListener('keydown', onKey, true); w.remove();
      if (back && back.isConnected) try { back.focus(); } catch (e) { /* egal */ }
    }
    function onKey(e) { if (e.key === 'Escape' && !e.defaultPrevented) { e.preventDefault(); close(); } }
    document.addEventListener('keydown', onKey, true);
    x.addEventListener('click', close);
    fitB.addEventListener('click', function () { view.fit(); });
    FULL = { focus: function (id) { view.focus = id; var fn = view.nodes.filter(function (n) { return n.id === id; })[0]; if (fn) view.center(fn, 1.4); }, close: close };
    stat.textContent = T('Lädt …');
    view.resize();
    load(true).then(function () { rebuild(false); search.focus(); }).catch(function (e) { stat.textContent = T('Graph nicht geladen: {err}', { err: e.message }); });
  }

  // --- Lokaler Graph unter "Verlinkt von" -----------------------------------------------------------------------
  var LOCAL = null;
  function local(id) {
    var page = document.getElementById('page'), anchor = document.getElementById('pg-backlinks');
    if (!page || !anchor) return;
    if (!LOCAL) {
      var sec = el('section', 'gr-local'); sec.id = 'pg-graph';
      var hd = el('div', 'gr-local-head'); hd.appendChild(el('h3', null, T('Graph')));
      var dep = el('select', 'gr-depth'); dep.setAttribute('aria-label', T('Tiefe'));
      [1, 2, 3].forEach(function (d) { var o = el('option', null, T('Tiefe {n}', { n: d })); o.value = String(d); dep.appendChild(o); });
      var big = el('button', 'btn gr-big', T('Ganzer Graph')); big.type = 'button';
      hd.appendChild(dep); hd.appendChild(big); sec.appendChild(hd);
      var cv = el('canvas', 'gr-local-canvas'); cv.setAttribute('role', 'img'); sec.appendChild(cv);
      anchor.parentNode.insertBefore(sec, anchor.nextSibling);
      var view = new View(cv, { wheelNeedsCtrl: true, onOpen: function (n) { if (!n.tag && n.id !== LOCAL.id) window.HKPages.k.open(n.id); } });
      LOCAL = { sec: sec, view: view, dep: dep, id: null };
      var P = prefs(); dep.value = String(P.depth || 1);
      dep.addEventListener('change', function () { var p = prefs(); p.depth = parseInt(dep.value, 10) || 1; savePrefs(p); draw(LOCAL.id, true); });
      big.addEventListener('click', function () { open(LOCAL.id); });
    }
    LOCAL.id = id; draw(id, false);
  }
  function draw(id, keep) {
    var L = LOCAL; if (!L) return;
    if (id == null || id < 0) { L.sec.hidden = true; return; }
    load(false).then(function (d) {
      if (L.id !== id) return;
      var p = prefs(); p.tags = false; p.orphans = true; p.size = Math.max(p.size, 1.4);
      var g = build(d, p, { focus: id, depth: parseInt(L.dep.value, 10) || 1 });
      if (g.nodes.length < 2) { L.sec.hidden = true; return; }
      L.sec.hidden = false; L.view.p = p; L.view.focus = id; L.view.k = 1;
      L.view.resize(); L.view.set(g, keep);
      L.view.c.setAttribute('aria-label', T('Lokaler Graph: {n} verbundene Seiten', { n: g.nodes.length - 1 }));
      setTimeout(function () { if (L.id === id) L.view.fit(); }, 600);
    }).catch(function () { L.sec.hidden = true; });
  }
  document.addEventListener('hk-page-open', function (e) {
    var id = e.detail && e.detail.id;
    if (LOCAL && LOCAL.id === id) return;
    local(id);
  });

  window.HKGraph = { open: open, build: build, View: View };
  var btn = document.getElementById('pg-graph-btn');
  if (btn) btn.addEventListener('click', function () { open(window.HKPages && HKPages.k.cur() ? HKPages.k.cur().id : null); });
})();
