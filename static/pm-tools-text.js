// Text-Werkzeuge im Tiptap-Editor (Saga 2.5, Tiptap-Umbau Etappe T4c): Tabelle, Code-Block (mit Mermaid-Vorschau),
// Mathe-Block. Meldet sich wie static/pm-struktur.js beim Kern mit HKPmEditor.use(fn) an (mitgeladen ueber EXT_SCRIPTS).
//
// - Tabelle table > trow > tcell (Inline-Zellen, kein Tiptap-TableKit). Attribute wie Editor.js/Server (_clean_table):
//   withHeadings, headCol, colWidths [%], colors {r, c, x}, stretched. Farben als Dekoration, Breiten per CSSOM
//   (<col>.style.width). Jede Zeilen-/Spaltenoperation ist EINE Transaktion (Farben/Breiten wandern mit).
// - Code: ProseMirror verwaltet den Klartext (code: true, keine Marken). Hervorhebung als Dekoration aus dem Tokenizer
//   von static/code.js (HKCodeHL.tokens), Sprache/Ansicht/Kopieren im Kopf, Mermaid-Vorschau ueber static/editor25.js
//   (abgeschotteter Rahmen, Vorschaubild render wie bisher). Markdown-Kuerzel ``` (+ Sprache).
// - Mathe-Block: atom mit tex/render, Darstellung wie MathTool (editor25.js, KaTeX nachgeladen), tex atomar.
//
// CSP: keine style-Attribute, nur CSSOM (el.style.x). Eigenes CSS: static/pm-tools-text.css (hier nachgeladen).
(function () {
  'use strict';
  if (!window.HKPmEditor || typeof HKPmEditor.use !== 'function') return;

  HKPmEditor.use(function (X) {
    var P = X.P, S0 = P.pm.state, VW = P.pm.view, el = X.el, K = X.K;
    var MAX_COLS = 20, COLORS = X.COLORS;
    var V = document.body.dataset.v || '';
    (function () { var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/static/pm-tools-text.css?v=' + V; document.head.appendChild(l); })();
    function ed() { return X.ed(); }
    function schema() { return X.schema(); }
    function ro() { return X.ro(); }
    function st() { return ed().state; }
    function dispatch(tr) { ed().view.dispatch(tr); }
    function nid() { return X.newId(); }
    function para(id, content) { return schema().nodes.paragraph.create({ id: id || nid() }, content || null); }
    function clone(v) { return v == null ? v : JSON.parse(JSON.stringify(v)); }
    function setNode(pos, patch, noHist) {
      var n = st().doc.nodeAt(pos); if (!n) return;
      var tr = st().tr.setNodeMarkup(pos, null, Object.assign({}, n.attrs, patch));
      if (noHist) tr.setMeta('addToHistory', false);
      dispatch(tr);
    }
    function byId(id) { return X.findId(id); }
    // Python round(x, 1) (Server _clean_table): halbe Werte zur geraden Ziffer, sonst exakt gerundet
    function pyRound1(x) {
      if (Number.isInteger(x * 4) && !Number.isInteger(x * 2)) { var f = Math.floor(x * 10); return (f % 2 === 0 ? f : f + 1) / 10; }
      return Number(x.toFixed(1));
    }

    // =================================================================================================================
    // Tabelle
    // =================================================================================================================
    function nCols(t) { var n = 0; t.forEach(function (r) { n = Math.max(n, r.childCount); }); return n; }
    // Breiten wie saga/pages.py _clean_table: je 5-90 %, Summe 100, eine Nachkommastelle (Rest an die breiteste Spalte)
    function normWidths(ws) {
      if (!ws || !ws.length || ws.some(function (x) { return !(x > 0); })) return null;
      var tot = ws.reduce(function (a, x) { return a + x; }, 0);
      var w = ws.map(function (x) { return Math.min(90, Math.max(5, x * 100 / tot)); });
      tot = w.reduce(function (a, x) { return a + x; }, 0);
      w = w.map(function (x) { return pyRound1(x * 100 / tot); });
      var big = 0; w.forEach(function (x, i) { if (x > w[big]) big = i; });
      w[big] = pyRound1(w[big] + 100 - w.reduce(function (a, x) { return a + x; }, 0));
      return w;
    }
    function widthsOf(t) {                 // gueltige Breiten oder null (gleich breit)
      var cw = t.attrs.colWidths, n = nCols(t);
      return Array.isArray(cw) && cw.length === n && n > 0 && cw.every(function (x) { return typeof x === 'number' && x > 0; }) ? cw.slice() : null;
    }
    function equalW(n) { var a = []; for (var i = 0; i < n; i++) a.push(100 / n); return a; }
    function colorOf(cs, r, c) { cs = cs || {}; return (cs.x || {})[r + ',' + c] || (cs.r || {})[String(r)] || (cs.c || {})[String(c)] || null; }
    // Farben umnummerieren: kind 'r' (Zeile) oder 'c' (Spalte); fn(alterIndex) -> neuer Index oder -1 (entfaellt)
    function remapColors(cs, kind, fn) {
      if (!cs) return null;
      var out = {};
      ['r', 'c', 'x'].forEach(function (k) {
        var m = cs[k]; if (!m || typeof m !== 'object') return;
        var nm = {}, any = false;
        Object.keys(m).forEach(function (key) {
          var nk = key;
          if (k === kind) { var i = fn(+key); if (i < 0) return; nk = String(i); }
          else if (k === 'x') {
            var ps = key.split(','), a = +ps[0], b = +ps[1];
            if (kind === 'r') { a = fn(a); if (a < 0) return; } else { b = fn(b); if (b < 0) return; }
            nk = a + ',' + b;
          }
          nm[nk] = m[key]; any = true;
        });
        if (any) out[k] = nm;
      });
      return Object.keys(out).length ? out : null;
    }
    function setColor(cs, kind, key, col) {
      var out = {}, base = clone(cs) || {};
      ['r', 'c', 'x'].forEach(function (k) {
        var m = base[k] ? Object.assign({}, base[k]) : {};
        if (k === kind) { if (col) m[key] = col; else delete m[key]; }
        if (Object.keys(m).length) out[k] = m;
      });
      return Object.keys(out).length ? out : null;
    }
    // Zellen als Matrix [[tcell]] (Zeilen ggf. auf n aufgefuellt)
    function matrix(t, pad) {
      var n = nCols(t), rows = [];
      t.forEach(function (r) { var cells = []; r.forEach(function (c) { cells.push(c); }); if (pad) while (cells.length < n) cells.push(schema().nodes.tcell.create()); rows.push(cells); });
      return rows;
    }
    function build(attrs, rows) {
      var s = schema();
      return s.nodes.table.create(attrs, rows.map(function (cells) { return s.nodes.trow.create(null, cells); }));
    }
    function cellStart(t, tpos, r, c) {     // Position vor der Zelle (r, c)
      var p = tpos + 1;
      for (var i = 0; i < r; i++) p += t.child(i).nodeSize;
      var row = t.child(r); p += 1;
      for (var j = 0; j < c && j < row.childCount; j++) p += row.child(j).nodeSize;
      return p;
    }
    function caretInCell(tr, tpos, r, c, end) {
      var t = tr.doc.nodeAt(tpos); if (!t || r < 0 || r >= t.childCount) return;
      var row = t.child(r); c = Math.max(0, Math.min(c, row.childCount - 1)); if (c < 0) return;
      var p = cellStart(t, tpos, r, c), cell = row.child(c);
      tr.setSelection(S0.TextSelection.create(tr.doc, p + 1 + (end ? cell.content.size : 0)));
    }
    // Schreibmarke in einer Zelle -> {tpos, table, r, c, cellPos, cell, $f}
    function cellCtx(state) {
      var $f = state.selection.$from;
      for (var d = $f.depth; d >= 3; d--) {
        if ($f.node(d).type.name === 'tcell' && $f.node(d - 2).type.name === 'table') {
          return { tpos: $f.before(d - 2), table: $f.node(d - 2), r: $f.index(d - 2), c: $f.index(d - 1), cellPos: $f.before(d), cell: $f.node(d), $f: $f, depth: d };
        }
      }
      return null;
    }
    // Struktur-Operationen (eine Transaktion, Schreibmarke danach in rc)
    function tableOp(id, fn) {
      if (ro()) return;
      var x = byId(id); if (!x || x.node.type.name !== 'table') return;
      var t = x.node, rows = matrix(t, true), a = Object.assign({}, t.attrs), res = fn(rows, a, t);
      if (!res) return;
      var tr = st().tr.replaceWith(x.pos, x.pos + t.nodeSize, build(a, res.rows || rows));
      if (res.rc) caretInCell(tr, x.pos, res.rc[0], res.rc[1], res.end);
      dispatch(tr.scrollIntoView()); ed().view.focus();
    }
    function emptyRow(n) { var out = []; for (var i = 0; i < n; i++) out.push(schema().nodes.tcell.create()); return out; }
    var TBL = {
      addRow: function (id, at) { tableOp(id, function (rows, a, t) {
        var n = Math.max(1, nCols(t)); at = Math.max(0, Math.min(at, rows.length));
        rows.splice(at, 0, emptyRow(n));
        a.colors = remapColors(a.colors, 'r', function (i) { return i >= at ? i + 1 : i; });
        return { rows: rows, rc: [at, 0] };
      }); },
      delRow: function (id, i) { tableOp(id, function (rows, a) {
        if (rows.length <= 1) { HK.toast(T('Die letzte Zeile lässt sich nicht löschen – Tabelle über das Block-Menü löschen')); return null; }
        rows.splice(i, 1);
        a.colors = remapColors(a.colors, 'r', function (k) { return k === i ? -1 : k > i ? k - 1 : k; });
        return { rows: rows, rc: [Math.min(i, rows.length - 1), 0] };
      }); },
      moveRow: function (id, i, d) { tableOp(id, function (rows, a) {
        var j = i + d; if (j < 0 || j >= rows.length) return null;
        var tmp = rows[i]; rows[i] = rows[j]; rows[j] = tmp;
        a.colors = remapColors(a.colors, 'r', function (k) { return k === i ? j : k === j ? i : k; });
        return { rows: rows, rc: [j, 0] };
      }); },
      addCol: function (id, at) { tableOp(id, function (rows, a, t) {
        var n = nCols(t);
        if (n >= MAX_COLS) { HK.toast(T('Höchstens {n} Spalten', { n: MAX_COLS })); return null; }
        at = Math.max(0, Math.min(at, n));
        if (!rows.length) rows.push([]);
        rows.forEach(function (cells) { cells.splice(at, 0, schema().nodes.tcell.create()); });
        a.colors = remapColors(a.colors, 'c', function (i) { return i >= at ? i + 1 : i; });
        var ws = widthsOf(t);
        if (ws) { ws.splice(at, 0, 100 / n); a.colWidths = normWidths(ws); }
        return { rows: rows, rc: [0, at] };
      }); },
      delCol: function (id, i) { tableOp(id, function (rows, a, t) {
        var n = nCols(t);
        if (n <= 1) { HK.toast(T('Die letzte Spalte lässt sich nicht löschen – Tabelle über das Block-Menü löschen')); return null; }
        rows.forEach(function (cells) { cells.splice(i, 1); });
        a.colors = remapColors(a.colors, 'c', function (k) { return k === i ? -1 : k > i ? k - 1 : k; });
        var ws = widthsOf(t);
        if (ws) { ws.splice(i, 1); a.colWidths = n - 1 > 0 ? normWidths(ws) : null; }
        return { rows: rows, rc: [0, Math.min(i, n - 2)] };
      }); },
      moveCol: function (id, i, d) { tableOp(id, function (rows, a, t) {
        var j = i + d, n = nCols(t); if (j < 0 || j >= n) return null;
        rows.forEach(function (cells) { var tmp = cells[i]; cells[i] = cells[j]; cells[j] = tmp; });
        a.colors = remapColors(a.colors, 'c', function (k) { return k === i ? j : k === j ? i : k; });
        var ws = widthsOf(t); if (ws) { var w0 = ws[i]; ws[i] = ws[j]; ws[j] = w0; a.colWidths = ws; }
        return { rows: rows, rc: [0, j] };
      }); },
      color: function (id, kind, key, col) {
        if (ro()) return;
        var x = byId(id); if (!x) return;
        setNode(x.pos, { colors: setColor(x.node.attrs.colors, kind, key, col) });
      },
      set: function (id, patch) { var x = byId(id); if (x && !ro()) setNode(x.pos, patch); }
    };
    function paletteSub(id, kind, key, cur) {
      return function (sm) {
        sm.appendChild(HK.menuItem(T('Keine Farbe'), function () { TBL.color(id, kind, key, null); }, { on: !cur }));
        COLORS.forEach(function (c) {
          var it = HK.menuItem(K.colorNames[c] || c, function () { TBL.color(id, kind, key, c); }, { icon: ' ', on: cur === c });
          var ic = it.querySelector('.mi-ic'); if (ic) ic.classList.add('tbl-sw', 'tbl-bg-' + c);
          sm.appendChild(it);
        });
      };
    }
    function rowMenu(anchor, id, r) {
      var x = byId(id); if (!x) return;
      var t = x.node, cs = t.attrs.colors || {};
      HK.openMenu(anchor, function (m) {
        m.appendChild(HK.menuLabel(T('Zeile {n}', { n: r + 1 })));
        m.appendChild(HK.menuItem(T('Zeile oberhalb einfügen'), function () { TBL.addRow(id, r); }, { icon: '↥' }));
        m.appendChild(HK.menuItem(T('Zeile unterhalb einfügen'), function () { TBL.addRow(id, r + 1); }, { icon: '↧' }));
        if (r > 0) m.appendChild(HK.menuItem(T('Nach oben'), function () { TBL.moveRow(id, r, -1); }, { icon: '↑' }));
        if (r < t.childCount - 1) m.appendChild(HK.menuItem(T('Nach unten'), function () { TBL.moveRow(id, r, 1); }, { icon: '↓' }));
        m.appendChild(HK.menuItem(T('Zeile einfärben'), null, { icon: '◐', sub: paletteSub(id, 'r', String(r), (cs.r || {})[String(r)]) }));
        m.appendChild(el('div', 'menu-sep'));
        m.appendChild(HK.menuItem(T('Zeile löschen'), function () { TBL.delRow(id, r); }, { icon: '🗑', danger: true }));
      });
    }
    function colMenu(anchor, id, c) {
      var x = byId(id); if (!x) return;
      var t = x.node, cs = t.attrs.colors || {}, n = nCols(t);
      HK.openMenu(anchor, function (m) {
        m.appendChild(HK.menuLabel(T('Spalte {n}', { n: c + 1 })));
        if (n < MAX_COLS) {
          m.appendChild(HK.menuItem(T('Spalte links einfügen'), function () { TBL.addCol(id, c); }, { icon: '⇤' }));
          m.appendChild(HK.menuItem(T('Spalte rechts einfügen'), function () { TBL.addCol(id, c + 1); }, { icon: '⇥' }));
        }
        if (c > 0) m.appendChild(HK.menuItem(T('Nach links'), function () { TBL.moveCol(id, c, -1); }, { icon: '←' }));
        if (c < n - 1) m.appendChild(HK.menuItem(T('Nach rechts'), function () { TBL.moveCol(id, c, 1); }, { icon: '→' }));
        m.appendChild(HK.menuItem(T('Spalte einfärben'), null, { icon: '◐', sub: paletteSub(id, 'c', String(c), (cs.c || {})[String(c)]) }));
        m.appendChild(el('div', 'menu-sep'));
        m.appendChild(HK.menuItem(T('Spalte löschen'), function () { TBL.delCol(id, c); }, { icon: '🗑', danger: true }));
      });
    }
    function cellMenu(anchor, id, r, c) {
      var x = byId(id); if (!x) return;
      var cs = x.node.attrs.colors || {};
      HK.openMenu(anchor, function (m) { paletteSub(id, 'x', r + ',' + c, (cs.x || {})[r + ',' + c])(m); });
    }

    var NV = {};
    NV.table = function (props) {
      var node = props.node, getPos = props.getPos;
      var dom = el('div', 'pm-blk pm-b-table'), bar = el('div', 'pm-tbl-bar'), wrap = el('div', 'pm-tbl-wrap');
      var table = el('table', 'pm-tbl'), cg = document.createElement('colgroup'), body = document.createElement('tbody');
      var rs = el('div', 'pm-tbl-rs'), addC = X.nodeBtn('pm-tbl-add pm-tbl-addc', '+', T('Spalte hinzufügen')), addR = X.nodeBtn('pm-tbl-add pm-tbl-addr', '+', T('Zeile unterhalb einfügen'));
      [bar, rs, addC, addR].forEach(function (e) { e.contentEditable = 'false'; });
      table.appendChild(cg); table.appendChild(body); wrap.appendChild(table); wrap.appendChild(rs);
      dom.appendChild(bar); dom.appendChild(wrap); dom.appendChild(addC); dom.appendChild(addR);
      var bRow = X.nodeBtn('pm-tbl-b', '', T('Zeile')), bCol = X.nodeBtn('pm-tbl-b', '', T('Spalte')), bCell = X.nodeBtn('pm-tbl-b', T('Zelle'), T('Zelle einfärben'));
      bRow.dataset.a = 'row'; bCol.dataset.a = 'col'; bCell.dataset.a = 'cell';
      bar.appendChild(bRow); bar.appendChild(bCol); bar.appendChild(bCell);
      var cur = null, live = null;
      function id() { return node.attrs.id; }
      bRow.addEventListener('click', function () { if (cur) rowMenu(bRow, id(), cur[0]); });
      bCol.addEventListener('click', function () { if (cur) colMenu(bCol, id(), cur[1]); });
      bCell.addEventListener('click', function () { if (cur) cellMenu(bCell, id(), cur[0], cur[1]); });
      addC.addEventListener('click', function () { TBL.addCol(id(), nCols(node)); });
      addR.addEventListener('click', function () { TBL.addRow(id(), node.childCount); });
      function widths() { return live || widthsOf(node); }
      function grid() {
        var n = nCols(node), ws = widths();
        while (cg.children.length > n) cg.lastChild.remove();
        while (cg.children.length < n) cg.appendChild(document.createElement('col'));
        [].forEach.call(cg.children, function (c, i) { c.style.width = ws ? ws[i] + '%' : ''; });
        table.classList.toggle('pm-tbl-w', !!ws);
        while (rs.children.length > Math.max(0, n - 1)) rs.lastChild.remove();
        while (rs.children.length < n - 1) rs.appendChild(handle(rs.children.length));
        var eq = ws || equalW(n), acc = 0;
        [].forEach.call(rs.children, function (h, i) { acc += eq[i]; h.style.left = acc + '%'; });
        addC.disabled = n >= MAX_COLS;
      }
      function sync() {
        X.paint(dom, node);
        table.classList.toggle('pm-tbl-head', !!node.attrs.withHeadings);
        table.classList.toggle('pm-tbl-hcol', !!node.attrs.headCol);
        dom.classList.toggle('pm-tbl-stretched', !!node.attrs.stretched);
        grid();
      }
      function handle(i) {
        var h = el('div', 'pm-tbl-rh'); h.setAttribute('aria-hidden', 'true');
        h.title = T('Ziehen ändert die Spaltenbreite, Doppelklick setzt alle gleich');
        h.addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); });
        h.addEventListener('dblclick', function (e) {
          e.preventDefault(); e.stopPropagation();
          if (ro() || !node.attrs.colWidths) return;
          var p = X.posOf(getPos); if (p != null) setNode(p, { colWidths: null });
        });
        h.addEventListener('pointerdown', function (e) {
          if (ro() || e.button !== 0) return;
          e.preventDefault(); e.stopPropagation();
          var W = table.getBoundingClientRect().width; if (!W) return;
          var ws0 = widthsOf(node) || equalW(nCols(node)), x0 = e.clientX, pair = ws0[i] + ws0[i + 1];
          try { h.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
          document.body.classList.add('hk-resizing');
          function mv(ev) {
            var a = Math.max(5, Math.min(pair - 5, ws0[i] + (ev.clientX - x0) / W * 100));
            live = ws0.slice(); live[i] = a; live[i + 1] = pair - a; grid();
          }
          function up() {
            h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
            document.body.classList.remove('hk-resizing');
            var w = live ? normWidths(live) : null; live = null;
            var p = X.posOf(getPos);
            if (w && p != null && JSON.stringify(w) !== JSON.stringify(node.attrs.colWidths || null)) setNode(p, { colWidths: w });
            else grid();
          }
          h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
        });
        return h;
      }
      sync();
      dom._pmTbl = {
        focus: function (r, c) {
          cur = [r, c]; dom.classList.add('pm-tbl-on');
          bRow.textContent = T('Zeile {n}', { n: r + 1 }) + ' ▾'; bCol.textContent = T('Spalte {n}', { n: c + 1 }) + ' ▾'; bCell.textContent = T('Zelle') + ' ▾';
        },
        blur: function () { cur = null; dom.classList.remove('pm-tbl-on'); }
      };
      return {
        dom: dom, contentDOM: body, ignoreMutation: X.ignoreOutside(body),
        stopEvent: function (e) { var t = e.target; return !!(t && t.closest && (bar.contains(t) || rs.contains(t) || t === addC || t === addR)); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; },
        destroy: function () { dom._pmTbl = null; }
      };
    };
    // Plugin: Farben als Dekoration, Leiste der Tabelle mit der Schreibmarke
    function tableColors(doc) {
      var out = [];
      doc.descendants(function (n, pos) {
        if (n.type.name !== 'table') return !n.isTextblock && !n.isAtom;
        var cs = n.attrs.colors; if (!cs) return false;
        var p = pos + 1;
        n.forEach(function (row, o, r) {
          var q = p + o + 1;
          row.forEach(function (cell, o2, c) {
            var col = colorOf(cs, r, c);
            if (col && COLORS.indexOf(col) >= 0) out.push(VW.Decoration.node(q + o2, q + o2 + cell.nodeSize, { 'class': 'tbl-bg-' + col }));
          });
        });
        return false;
      });
      return out;
    }

    // =================================================================================================================
    // Code
    // =================================================================================================================
    var HL = function () { return window.HKCodeHL || null; };
    function langOf(n) { var h = HL(); return h ? h.norm(n.attrs.language) : String(n.attrs.language || ''); }
    var VIEWS = ['preview', 'split', 'code'];
    var LIVE = [];                         // offene Code-/Formel-Ansichten (Lesemodus nachziehen)
    NV.code = function (props) {
      var node = props.node, getPos = props.getPos, localView = null, lastSrc = null;
      var dom = el('div', 'pm-blk pm-b-code hk-code'), head = el('div', 'hk-code-head');
      var sel = el('select', 'hk-code-lang'); sel.setAttribute('aria-label', T('Programmiersprache'));
      ((HL() && HL().langs) || [['', T('Text')]]).forEach(function (x) { var o = el('option', null, x[1]); o.value = x[0]; sel.appendChild(o); });
      var vw = el('span', 'hk-code-views'); vw.setAttribute('role', 'group'); vw.setAttribute('aria-label', T('Ansicht'));
      [['preview', T('Vorschau')], ['split', T('Geteilt')], ['code', T('Code')]].forEach(function (x) {
        var b = el('button', 'hk-code-vb', x[1]); b.type = 'button'; b.dataset.v = x[0];
        b.addEventListener('mousedown', function (e) { e.preventDefault(); });
        b.addEventListener('click', function () { setView(x[0]); });
        vw.appendChild(b);
      });
      var cp = el('button', 'hk-code-copy', T('Kopieren')); cp.type = 'button';
      cp.addEventListener('mousedown', function (e) { e.preventDefault(); });
      cp.addEventListener('click', function () { if (navigator.clipboard) navigator.clipboard.writeText(node.textContent).then(function () { HK.toast(T('Code kopiert')); }); });
      head.appendChild(sel); head.appendChild(vw); head.appendChild(cp);
      var area = el('div', 'hk-code-area'), pre = el('pre', 'pm-code-pre'), code = document.createElement('code');
      code.spellcheck = false; pre.appendChild(code); area.appendChild(pre);
      var box = el('div', 'hk-mmd'); box.setAttribute('aria-live', 'polite'); box.contentEditable = 'false';
      var stale = el('div', 'hk-math-stale'); stale.contentEditable = 'false'; head.contentEditable = 'false';
      dom.appendChild(head); dom.appendChild(area); dom.appendChild(box); dom.appendChild(stale);
      var tool = { mmdBox: box, st: stale, pngName: 'diagramm.png', data: { render: node.attrs.render || undefined },
        source: function () { return node.textContent; },
        makePng: function () { return window.HKEd25.mermaidPng(tool); },
        markRender: function () { if (window.HKEd25 && isMmd()) window.HKEd25.renderStale(tool, stale); },
        block: { dispatchChange: function () {           // Vorschaubild hochgeladen (editor25.js scheduleRender)
          var p = X.posOf(getPos), r = tool.data.render; if (p == null || ro()) return;
          if (JSON.stringify(r || null) !== JSON.stringify(node.attrs.render || null)) setNode(p, { render: r || null }, true);
        } } };
      function isMmd() { return langOf(node) === 'mermaid'; }
      function view() { var v = localView || node.attrs.view; return VIEWS.indexOf(v) >= 0 ? v : 'preview'; }
      function setView(v) {
        if (ro()) { localView = v; sync(true); return; }                // Lesen: nur lokal umschalten
        var p = X.posOf(getPos); if (p == null) return;
        localView = null; setNode(p, { view: v });
        if (v !== 'preview') setTimeout(function () { var q = X.posOf(getPos); if (q != null) { var tr = st().tr; X.setSel(tr, q + 1 + node.content.size); dispatch(tr); ed().view.focus(); } }, 0);
      }
      sel.addEventListener('change', function () {
        var p = X.posOf(getPos); if (p == null || ro()) { sel.value = langOf(node); return; }
        var v = sel.value, patch = { language: v || null };
        if (v === 'mermaid') patch.view = VIEWS.indexOf(node.attrs.view) >= 0 ? node.attrs.view : 'split';
        else { patch.view = null; patch.render = null; }
        setNode(p, patch);
      });
      box.addEventListener('dblclick', function () { if (!ro() && view() === 'preview') setView('split'); });
      function sync(force) {
        X.paint(dom, node);
        var lang = langOf(node), mm = lang === 'mermaid' && !!window.HKEd25;
        dom.dataset.lang = lang; if (sel.value !== lang) sel.value = lang;
        sel.disabled = ro();
        tool.data = { render: node.attrs.render || undefined };
        dom.classList.toggle('hk-mermaid', mm);
        VIEWS.forEach(function (v) { dom.classList.toggle('hk-v-' + v, mm && view() === v); });
        [].forEach.call(vw.children, function (b) { var on = b.dataset.v === view(); b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
        if (!mm) { if (lastSrc !== null) { box.textContent = ''; stale.textContent = ''; lastSrc = null; } return; }
        var src = node.textContent + '\u0000' + view();
        if (force || src !== lastSrc) {
          lastSrc = src;
          if (view() !== 'code') window.HKEd25.mermaidPaint(tool);
          else if (!ro()) window.HKEd25.scheduleRender(tool);
        }
        tool.markRender();
      }
      var me = { ro: function () { sel.disabled = ro(); } };
      LIVE.push(me);
      sync(true);
      setTimeout(me.ro, 0);
      return {
        dom: dom, contentDOM: code, ignoreMutation: X.ignoreOutside(code),
        stopEvent: function (e) { var t = e.target; return !!(t && t.closest && (head.contains(t) || box.contains(t))); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; },
        destroy: function () { var i = LIVE.indexOf(me); if (i >= 0) LIVE.splice(i, 1); if (tool.mUrl) URL.revokeObjectURL(tool.mUrl); clearTimeout(tool.mT); clearTimeout(tool.rT); }
      };
    };
    // Hervorhebung: Dekorationen je Code-Knoten (Cache je Knotenobjekt)
    var HL_CACHE = new WeakMap();
    function codeDecos(doc) {
      var h = HL(), out = []; if (!h || !h.tokens) return out;
      doc.descendants(function (n, pos) {
        if (n.type.name !== 'code') return !n.isTextblock && !n.isAtom;
        var toks = HL_CACHE.get(n);
        if (!toks) { toks = h.tokens(n.textContent, langOf(n)); HL_CACHE.set(n, toks); }
        toks.forEach(function (t) { out.push(VW.Decoration.inline(pos + 1 + t[0], pos + 1 + t[1], { 'class': 'tk-' + t[2] })); });
        return false;
      });
      return out;
    }
    function codeCtx(state) {
      var $f = state.selection.$from, n = $f.parent;
      if (n.type.name !== 'code') return null;
      return { node: n, pos: $f.before(), start: $f.start(), $f: $f };
    }
    function exitCode(state, c, cut) {        // Absatz hinter den Code-Block (cut: Zeichen am Ende entfernen)
      var tr = state.tr;
      if (cut) tr.delete(c.start + c.node.content.size - cut, c.start + c.node.content.size);
      var n = tr.doc.nodeAt(c.pos), at = c.pos + n.nodeSize;
      tr.insert(at, para()); X.setSel(tr, at + 1);
      dispatch(tr.scrollIntoView()); return true;
    }
    function codeLines(state, c) {            // Zeilenanfaenge (Offsets im Code) im Bereich der Auswahl
      var txt = c.node.textContent, sel = state.selection, a = sel.from - c.start, b = sel.to - c.start, out = [];
      var ls = txt.lastIndexOf('\n', a - 1) + 1; out.push(ls);
      for (var i = ls; i < b; i++) if (txt[i] === '\n' && i + 1 <= b && i + 1 < txt.length + 1 && i + 1 !== b) out.push(i + 1);
      return out;
    }
    function codeTab(state, back) {
      var c = codeCtx(state); if (!c) return false;
      var sel = state.selection, tr = state.tr, txt = c.node.textContent;
      if (!back && sel.empty) { dispatch(tr.insertText('  ')); return true; }
      var lines = codeLines(state, c).reverse();
      lines.forEach(function (ls) {
        if (back) { var k = 0; while (k < 2 && txt[ls + k] === ' ') k++; if (k) tr.delete(c.start + ls, c.start + ls + k); }
        else tr.insertText('  ', c.start + ls);
      });
      if (tr.docChanged) dispatch(tr);
      return true;
    }

    // =================================================================================================================
    // Mathe-Block
    // =================================================================================================================
    NV.math = function (props) {
      var node = props.node, getPos = props.getPos, MT = window.HKMathTool;
      var dom = el('div', 'pm-blk pm-b-math hk-mathblk'), view = el('div', 'hk-math-view'), ta = el('textarea', 'hk-math-ta'), stale = el('div', 'hk-math-stale');
      dom.contentEditable = 'false';
      ta.value = String(node.attrs.tex || ''); ta.placeholder = T('TeX, z. B. \\frac{a}{b} = \\sqrt{x^2}'); ta.spellcheck = false; ta.rows = 2;
      ta.setAttribute('aria-label', T('Formel (TeX)'));
      view.setAttribute('role', 'img');
      dom.appendChild(view); dom.appendChild(ta); dom.appendChild(stale);
      var tool = { view: view, st: stale, pngName: 'formel.png', data: { render: node.attrs.render || undefined },
        source: function () { return ta.value; },
        block: { dispatchChange: function () {
          var p = X.posOf(getPos), r = tool.data.render; if (p == null || ro()) return;
          if (JSON.stringify(r || null) !== JSON.stringify(node.attrs.render || null)) setNode(p, { render: r || null }, true);
        } } };
      if (MT) { tool.paint = MT.prototype.paint; tool.markRender = MT.prototype.markRender; tool.makePng = MT.prototype.makePng; }
      function paint() { if (tool.paint) tool.paint(); else view.textContent = ta.value; }
      var t = null;
      ta.addEventListener('input', function () {
        if (ro()) return;
        clearTimeout(t); t = setTimeout(paint, 200);
        var p = X.posOf(getPos); if (p != null) setNode(p, { tex: ta.value });
        if (window.HKEd25) HKEd25.scheduleRender(tool);
      });
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' || ((e.metaKey || e.ctrlKey) && e.key === 'Enter')) { e.preventDefault(); ta.blur(); leave(); }
        e.stopPropagation();
      });
      ta.addEventListener('focus', function () { dom.classList.add('editing'); });
      ta.addEventListener('blur', function () { if (ta.value || ro()) dom.classList.remove('editing'); paint(); });
      view.addEventListener('click', function () { if (!ro()) edit(); });
      function edit() { dom.classList.add('editing'); ta.readOnly = false; ta.focus(); }
      function leave() {                        // Schreibmarke hinter die Formel (naechster Textblock oder neuer Absatz)
        var p = X.posOf(getPos); if (p == null) return;
        var tr = st().tr, at = p + node.nodeSize, nx = tr.doc.resolve(at).nodeAfter;
        if (!nx || !nx.isTextblock) { tr.insert(at, para()); }
        X.setSel(tr, at + 1); dispatch(tr.scrollIntoView()); ed().view.focus();
      }
      function sync() {
        X.paint(dom, node);
        ta.readOnly = ro();
        tool.data = { render: node.attrs.render || undefined };
        var tex = String(node.attrs.tex || '');
        if (document.activeElement !== ta && ta.value !== tex) { ta.value = tex; paint(); }
        if (tool.markRender) tool.markRender();
      }
      var me = { ro: function () { ta.readOnly = ro(); if (ro()) dom.classList.remove('editing'); } };
      LIVE.push(me);
      X.paint(dom, node);
      if (!ta.value) dom.classList.add('editing');
      paint(); if (tool.markRender) tool.markRender();
      if (window.HKEd25) HKEd25.scheduleRender(tool);     // fehlendes/veraltetes Vorschaubild nachholen (prueft Lesen selbst)
      setTimeout(me.ro, 0);                               // beim Aufbau des Editors steht editable erst danach fest
      dom._pmMath = { edit: edit };
      return {
        dom: dom, ignoreMutation: function () { return true; },
        stopEvent: function (e) { var x = e.target; return !!(x && (x === ta || view.contains(x)) && !/^drag/.test(e.type)); },
        selectNode: function () { dom.classList.add('ProseMirror-selectednode'); },
        deselectNode: function () { dom.classList.remove('ProseMirror-selectednode'); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; },
        destroy: function () { var i = LIVE.indexOf(me); if (i >= 0) LIVE.splice(i, 1); clearTimeout(t); clearTimeout(tool.rT); dom._pmMath = null; }
      };
    };

    // =================================================================================================================
    // Plugins, Tasten, Slash, Block-Menue
    // =================================================================================================================
    var HLKEY = new S0.PluginKey('hkTextTools');
    function plugin() {
      var lastTbl = null;
      return new S0.Plugin({
        key: HLKEY,
        state: {
          init: function (c, state) { return VW.DecorationSet.create(state.doc, codeDecos(state.doc).concat(tableColors(state.doc))); },
          apply: function (tr, set, old, state) { return tr.docChanged ? VW.DecorationSet.create(state.doc, codeDecos(state.doc).concat(tableColors(state.doc))) : set; }
        },
        props: {
          decorations: function (state) { return HLKEY.getState(state); },
          // Ausgewaehlte Tabelle/Code (Knotenauswahl, z. B. Klick auf den Rand): Tippen ersetzt sie nicht (wie hkGuard bei
          // Karten und das Bild in pm-tools-medien.js - beide sind keine atom). Backspace/Entf loeschen weiter bewusst.
          handleTextInput: function (v) {
            var s = v.state.selection;
            return !!(s instanceof S0.NodeSelection && (s.node.type.name === 'table' || s.node.type.name === 'code'));
          }
        },
        view: function () {
          return { update: function (v) {
            LIVE.forEach(function (x) { x.ro(); });
            var c = v.editable ? cellCtx(v.state) : null, dom = c ? v.nodeDOM(c.tpos) : null, ctl = dom && dom._pmTbl;
            if (lastTbl && lastTbl !== ctl) lastTbl.blur();
            if (ctl) ctl.focus(c.r, c.c);
            lastTbl = ctl || null;
          }, destroy: function () { lastTbl = null; } };
        }
      });
    }
    // Markdown ``` (+ Sprache) + Leerzeichen (oder Enter am Absatzende) in einem Absatz -> Code-Block (ID bleibt, Rest des
    // Absatzes als Code). Eigener Undo-Schritt: ein Strg+Z stellt den Absatz "```" wieder her (nicht auch das Tippen).
    var FENCE = /^```([A-Za-z0-9+#._-]{0,24})$/;
    function fence(state, textBefore, extra) {
      var $f = state.selection.$from, n = $f.parent; if (n.type.name !== 'paragraph' || !state.selection.empty) return false;
      var m = FENCE.exec(textBefore); if (!m) return false;
      var h = HL(), lang = m[1] ? (h ? h.norm(m[1]) : m[1].toLowerCase()) : '';
      var pos = $f.before(), rest = n.textBetween($f.parentOffset, n.content.size, '\n', '\n');
      var s = schema(), cn = s.nodes.code.create({ id: n.attrs.id, style: null, language: lang || null, view: lang === 'mermaid' ? 'split' : null }, rest ? s.text(rest) : null);
      var tr = state.tr.replaceWith(pos, pos + n.nodeSize, cn);
      X.setSel(tr, pos + 1);
      if (P.pm.history && P.pm.history.closeHistory) P.pm.history.closeHistory(tr);
      // live (T5, y-undo statt prosemirror-history): closeHistory wirkt dort nicht - Erfassung vorher abschliessen
      var YU = window.SagaY && SagaY.ytiptap && SagaY.ytiptap.yUndoPluginKey, us = YU && YU.getState(state);
      if (us && us.undoManager) us.undoManager.stopCapturing();
      if (extra) extra(tr);
      dispatch(tr.scrollIntoView()); return true;
    }
    // Tottasten (deutsche/franzoesische Mac-Tastatur u. a.): der dritte Backtick entsteht erst mit der naechsten Taste
    // als Komposition (compositionend '`'), das Leerzeichen dabei wird verbraucht und kommt nie als Texteingabe an.
    // Daher: nach einer Komposition, die auf '`' endet und den Absatztext vor der Schreibmarke zu genau ``` macht,
    // ebenso umwandeln. Endet sie mit einem Buchstaben ("`j" bzw. "à"), endet data nicht auf '`' - dann nicht.
    // Ein gewohnheitsmaessig nachgeschobenes zweites Leerzeichen landet nicht im leeren Code-Block.
    function fencePlugin() {
      var swallow = null;
      function before(v) {
        var $f = v.state.selection.$from;
        return $f.parent.type.name === 'paragraph' ? $f.parent.textBetween(0, $f.parentOffset, '\n', '\ufffc') : null;
      }
      return new S0.Plugin({ props: {
        handleTextInput: function (v, from, to, text) {
          var sw = swallow; swallow = null;
          if (!v.editable) return false;
          if (sw && text === ' ' && Date.now() < sw.until) {
            var sl = v.state.selection, $s = sl.$from;
            if (sl.empty && $s.parent.type.name === 'code' && !$s.parent.content.size && $s.before() === sw.pos) return true;
          }
          if (text !== ' ') return false;
          var t = before(v); return t != null && fence(v.state, t);
        },
        handleDOMEvents: {
          keydown: function (v, e) { if (e.key !== ' ' && e.key !== 'Process' && e.keyCode !== 229) swallow = null; return false; },
          compositionend: function (v, e) {
            var d = e.data || '';
            if (!v.editable || d.charAt(d.length - 1) !== '`') return false;
            setTimeout(function () {
              if (v.isDestroyed || v.composing || !v.editable || before(v) !== '```') return;
              var pos = v.state.selection.$from.before();
              if (fence(v.state, '```')) swallow = { pos: pos, until: Date.now() + 1500 };
            }, 0);
            return false;
          }
        }
      } });
    }
    function keys(editor) {
      function S() { return editor.state; }
      function blockSel() { return X.selIds().length > 0; }
      function tblKey(fn) { return function () { if (blockSel() || X.menuActive()) return false; var c = cellCtx(S()); return c ? fn(c) : false; }; }
      function go(c, r, col, end) { var tr = S().tr; caretInCell(tr, c.tpos, r, col, end); dispatch(tr.scrollIntoView()); return true; }
      return {
        'Tab': function () {
          if (blockSel() || X.menuActive()) return false;
          var c = cellCtx(S());
          if (c) {
            var row = c.table.child(c.r);
            if (c.c + 1 < row.childCount) return go(c, c.r, c.c + 1);
            if (c.r + 1 < c.table.childCount) return go(c, c.r + 1, 0);
            TBL.addRow(c.table.attrs.id, c.table.childCount); return true;
          }
          return codeTab(S(), false);
        },
        'Shift-Tab': function () {
          if (blockSel()) return false;
          var c = cellCtx(S());
          if (c) {
            if (c.c > 0) return go(c, c.r, c.c - 1, true);
            if (c.r > 0) return go(c, c.r - 1, c.table.child(c.r - 1).childCount - 1, true);
            return true;
          }
          return codeTab(S(), true);
        },
        'Enter': function () {
          if (blockSel() || X.menuActive()) return false;
          var state = S(), sel = state.selection;
          if (sel instanceof S0.NodeSelection && sel.node.type.name === 'math' && !ro()) {
            var d = editor.view.nodeDOM(sel.from); if (d && d._pmMath) { d._pmMath.edit(); return true; }
          }
          var c = cellCtx(state);
          if (c) {                                // wie die Editor.js-Tabelle: naechste Zeile (am Ende neue Zeile)
            if (c.r + 1 < c.table.childCount) return go(c, c.r + 1, c.c);
            TBL.addRow(c.table.attrs.id, c.table.childCount);
            var x = byId(c.table.attrs.id); if (x) go({ tpos: x.pos }, c.r + 1, c.c);
            return true;
          }
          var k = codeCtx(state);
          if (k) {                                // Enter = Zeile; dreimal Enter am Ende verlaesst den Block
            var txt = k.node.textContent, off = k.$f.parentOffset;
            if (sel.empty && off === txt.length && /\n\n$/.test(txt)) return exitCode(state, k, 2);
            dispatch(state.tr.insertText('\n').scrollIntoView()); return true;
          }
          var $f = sel.$from;
          if ($f.parent.type.name === 'paragraph' && sel.empty && $f.parentOffset === $f.parent.content.size) return fence(state, $f.parent.textContent);
          return false;
        },
        'Mod-Enter': function () { var k = codeCtx(S()); return k ? exitCode(S(), k, 0) : false; },
        'Shift-Enter': function () {
          var k = codeCtx(S()); if (!k) return false;
          dispatch(S().tr.insertText('\n').scrollIntoView()); return true;
        },
        'Backspace': function () {
          if (blockSel()) return false;
          var state = S(), sel = state.selection; if (!sel.empty) return false;
          var k = codeCtx(state);
          if (k && k.$f.parentOffset === 0 && !k.node.content.size) {          // leerer Code-Block -> Absatz (ID bleibt)
            var tr = state.tr.replaceWith(k.pos, k.pos + k.node.nodeSize, para(k.node.attrs.id));
            X.setSel(tr, k.pos + 1); dispatch(tr); return true;
          }
          if (k && k.$f.parentOffset === 0) return true;                       // Code nicht mit dem Block davor verbinden
          var c = cellCtx(state);
          return !!(c && c.$f.parentOffset === 0);           // Zellen nicht zusammenfuegen
        },
        'Delete': function () {
          if (blockSel()) return false;
          var state = S(), sel = state.selection; if (!sel.empty) return false;
          var c = cellCtx(state);
          return !!(c && c.$f.parentOffset === c.cell.content.size);
        },
        'ArrowUp': tblKey(function (c) {
          if (!editor.view.endOfTextblock('up')) return false;
          if (c.r === 0) return false;
          return go(c, c.r - 1, c.c);
        }),
        'ArrowDown': tblKey(function (c) {
          if (!editor.view.endOfTextblock('down')) return false;
          if (c.r + 1 >= c.table.childCount) {          // letzte Zeile: hinter die Tabelle (am Dokumentende neuer Absatz)
            var state = S(), at = c.tpos + c.table.nodeSize, nx = state.doc.resolve(at).nodeAfter, tr = state.tr;
            if (!nx) tr.insert(at, para());
            X.setSel(tr, at + 1); dispatch(tr.scrollIntoView()); return true;
          }
          return go(c, c.r + 1, c.c);
        }),
        'Mod-a': function () {
          var k = codeCtx(S()); if (!k) return false;
          var a = k.start, b = k.start + k.node.content.size, sel = S().selection;
          if (sel.from === a && sel.to === b) return false;
          dispatch(S().tr.setSelection(S0.TextSelection.create(S().doc, a, b))); return true;
        }
      };
    }
    // Neuer Block aus dem "/"-Menue: leerer Absatz wird ersetzt (ID bleibt), sonst dahinter eingefuegt
    function place(editor, at, node, caret) {
      var state = editor.state, b = X.blockAt(at); if (!b) return;
      var tr = state.tr, empty = b.node.type.name === 'paragraph' && !b.node.content.size, where;
      if (empty) { node = node.type.create(Object.assign({}, node.attrs, { id: b.node.attrs.id }), node.content); tr.replaceWith(b.pos, b.pos + b.node.nodeSize, node); where = b.pos; }
      else { where = b.pos + b.node.nodeSize; tr.insert(where, node); }
      if (caret) caret(tr, where);
      editor.view.dispatch(tr.scrollIntoView()); editor.view.focus();
      return where;
    }
    var G_BASIC = T('Grundblöcke');
    X.slashReg({ key: 'table', group: G_BASIC, t: T('Tabelle'), ic: '▦', desc: T('Tabelle mit Zeilen und Spalten'), kw: 'tabelle table raster grid', order: 21,
      run: function (editor, at) {
        var s = schema(), rows = [0, 1].map(function () { return s.nodes.trow.create(null, emptyRow(3)); });
        place(editor, at, s.nodes.table.create({ id: nid(), withHeadings: true, stretched: false }, rows), function (tr, where) { caretInCell(tr, where, 0, 0); });
      } });
    X.slashReg({ key: 'code', group: G_BASIC, t: T('Code'), ic: '</>', desc: T('Codeblock mit Hervorhebung'), kw: 'code programm quelltext snippet mermaid', kbd: '```', order: 22,
      run: function (editor, at) { place(editor, at, schema().nodes.code.create({ id: nid() }), function (tr, where) { X.setSel(tr, where + 1); }); } });
    X.slashReg({ key: 'mathblock', group: 'Saga', t: T('Formel (Mathe)'), ic: '∑', desc: T('TeX-Formel als eigener Block'), kw: 'formel mathe math tex katex block', order: 84,
      run: function (editor, at) {
        var where = place(editor, at, schema().nodes.math.create({ id: nid(), tex: '' }), function (tr, w) { tr.setSelection(S0.NodeSelection.create(tr.doc, w)); });
        setTimeout(function () { var d = where != null && editor.view.nodeDOM(where); if (d && d._pmMath) d._pmMath.edit(); }, 0);
      } });

    function settings(m, x) {
      var n = x.node, t = n.type.name, id = n.attrs.id;
      if (t === 'table') {
        m.appendChild(HK.menuItem(n.attrs.withHeadings ? T('Ohne Kopfzeile') : T('Mit Kopfzeile'), function () { TBL.set(id, { withHeadings: !n.attrs.withHeadings }); }, { icon: '▤' }));
        m.appendChild(HK.menuItem(T('Erste Spalte als Kopf'), function () { TBL.set(id, { headCol: n.attrs.headCol ? null : true }); }, { icon: '▥', on: !!n.attrs.headCol }));
        m.appendChild(HK.menuItem(n.attrs.stretched ? T('Volle Breite aus') : T('Volle Breite ein'), function () { TBL.set(id, { stretched: !n.attrs.stretched }); }, { icon: '⇔' }));
        if (n.attrs.colWidths) m.appendChild(HK.menuItem(T('Spalten gleich breit'), function () { TBL.set(id, { colWidths: null }); }, { icon: '⇹' }));
        // T6: Schreibmarke in einer Zelle dieser Tabelle -> einfaerben wie im Editor.js-Block-Menue (auch in der Tabellenleiste)
        var cc = cellCtx(st()), cs = n.attrs.colors || {};
        if (cc && cc.tpos === x.pos) {
          var r = cc.r, c = cc.c;
          m.appendChild(HK.menuItem(T('Zeile einfärben'), null, { icon: '◐', sub: paletteSub(id, 'r', String(r), (cs.r || {})[String(r)]) }));
          m.appendChild(HK.menuItem(T('Spalte einfärben'), null, { icon: '◐', sub: paletteSub(id, 'c', String(c), (cs.c || {})[String(c)]) }));
          m.appendChild(HK.menuItem(T('Zelle einfärben'), null, { icon: '◐', sub: paletteSub(id, 'x', r + ',' + c, (cs.x || {})[r + ',' + c]) }));
        }
      } else if (t === 'code') {
        var h = HL(), lang = langOf(n);
        if (h) m.appendChild(HK.menuItem(T('Programmiersprache'), null, { icon: '</>', sub: function (sm) {
          h.langs.forEach(function (l) {
            sm.appendChild(HK.menuItem(l[1], function () {
              var y = byId(id); if (!y) return;
              var patch = { language: l[0] || null };
              if (l[0] === 'mermaid') patch.view = VIEWS.indexOf(y.node.attrs.view) >= 0 ? y.node.attrs.view : 'split'; else { patch.view = null; patch.render = null; }
              setNode(y.pos, patch);
            }, { on: lang === l[0] }));
          });
        } }));
      }
    }

    return {
      edit: ['table', 'code'],
      nodeViews: NV,
      plugins: function () { return [plugin(), fencePlugin()]; },
      keys: keys,
      settings: settings,
      names: { table: T('Tabelle'), code: T('Code'), math: T('Formel (Mathe)') },
      api: { table: TBL, normWidths: normWidths, remapColors: remapColors }
    };
  });
})();
