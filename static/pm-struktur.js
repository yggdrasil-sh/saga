// Struktur im Tiptap-Editor (Saga 2.5, Tiptap-Umbau Etappe T3): Spalten, Toggle (auch Toggle-Ueberschrift 1-3),
// synchronisierte Bloecke (Original/Kopie), Konfliktblock, Block-Griffe (+ / ⋮⋮) mit Ziehen und Rand-Drop,
// Block-Menue und Mehrfachauswahl ueber Ebenen und Spalten (Entscheidung T-E5 A).
//
// Wird von static/pm-editor.js mitgeladen und meldet sich dort mit HKPmEditor.use(fn) an. fn bekommt die internen
// Bausteine des Kern-Editors (X, siehe pm-editor.js "Erweiterungen") und liefert Node-Views, Plugins, Tasten und
// Blocktypen, die der Kern bearbeitbar macht. Alles ist eine Transaktion: ein Zug, ein Drop, ein Menuebefehl = ein
// Undo-Schritt (Aufraeumen leerer Spalten haengt sich per appendTransaction an denselben Schritt).
//
// CSP: keine style-Attribute, Breiten/Lagen nur ueber CSSOM (el.style.x, setProperty).
(function () {
  'use strict';
  if (!window.HKPmEditor || typeof HKPmEditor.use !== 'function') return;

  HKPmEditor.use(function (X) {
    var P = X.P, S0 = P.pm.state, VW = P.pm.view, M0 = P.pm.model, K = X.K, el = X.el;
    var Fragment = M0.Fragment;
    var MAX_COLS = 6, EDGE = 40;
    function ed() { return X.ed(); }
    function schema() { return X.schema(); }
    function ro() { return X.ro(); }
    function nid() { return X.newId(); }
    function state() { return ed().state; }
    function dispatch(tr) { ed().view.dispatch(tr); }

    // =================================================================================================================
    // Hilfen
    // =================================================================================================================
    var CONTAINERS = { toggle: 1, columns: 1, synced: 1 };
    function isCont(n) { return !!(n && CONTAINERS[n.type.name]); }
    // Kinder-Bloecke eines Behaelters (Toggle ohne Titel, Spalten ueber alle Spalten)
    function kidsOf(n) {
      var out = [];
      if (n.type.name === 'toggle') n.forEach(function (c, o, i) { if (i > 0) out.push(c); });
      else if (n.type.name === 'synced') n.forEach(function (c) { out.push(c); });
      else if (n.type.name === 'columns') n.forEach(function (col) { col.forEach(function (c) { out.push(c); }); });
      return out;
    }
    function domOf(pos) { var d = ed().view.nodeDOM(pos); return d && d.nodeType === 1 ? d : null; }
    // flache Liste aller Bloecke in Dokumentreihenfolge [{id, node, pos, depth, parent, slot}]
    function flat(doc) {
      var out = [];
      X.walk(doc || state().doc, function (x) { if (x.node.attrs.id) out.push(x); });
      return out;
    }
    function isOpen(n) {
      var id = n.attrs.id;
      if (id && Object.prototype.hasOwnProperty.call(LOCAL_OPEN, id)) return LOCAL_OPEN[id];
      return n.attrs.open !== false;
    }
    var LOCAL_OPEN = {};                    // Lesen/gesperrt: auf-/zuklappen nur lokal (wird nie gespeichert)
    function para(content, id) { return schema().nodes.paragraph.create({ id: id || nid() }, content || null); }

    // Spaltenbreiten (columns.cols[].w): Gewichte nur, wenn jede Spalte eines hat (wie Server und Editor.js), Summe 1
    function weights(cols) {
      var n = cols.childCount; if (n < 2) return null;
      var ws = [], ok = true;
      cols.forEach(function (c) { var w = c.attrs.w; if (typeof w === 'number' && isFinite(w) && w > 0) ws.push(w); else ok = false; });
      if (!ok) return null;
      var tot = ws.reduce(function (a, x) { return a + x; }, 0);
      return ws.map(function (x) { return x / tot; });
    }
    function equal(n) { var a = []; for (var i = 0; i < n; i++) a.push(1 / n); return a; }
    function frs(ws) { return ws.map(function (x) { return 'minmax(0,' + (Math.round(x * 1000) / 1000) + 'fr)'; }).join(' '); }
    function normWs(ws) {                   // gleich breit -> null, sonst auf 2 Stellen (wie ColumnsTool.setWs)
      if (!ws) return null;
      var eq = ws.every(function (x) { return Math.abs(x - 1 / ws.length) < 0.004; });
      return eq ? null : ws.map(function (x) { return Math.round(x * 100) / 100; });
    }
    // alle Spaltenbreiten eines Spalten-Blocks in tr setzen (null = gleich)
    function setWidths(tr, cpos, ws) {
      var cols = tr.doc.nodeAt(cpos); if (!cols || cols.type.name !== 'columns') return;
      var off = cpos + 1;
      cols.forEach(function (c, o, i) {
        var w = ws ? ws[i] : null;
        if (c.attrs.w !== w) tr.setNodeMarkup(off + o, null, Object.assign({}, c.attrs, { w: w }));
      });
    }
    function column(blocks, attrs) { return schema().nodes.column.create(Object.assign({ w: null }, attrs || {}), blocks); }
    function columnsNode(cols, attrs) { return schema().nodes.columns.create(Object.assign({ id: nid() }, attrs || {}), cols); }
    // Ziel innerhalb eines Behaelters erlaubt? (Schema: keine Spalten in Spalte/Toggle/synced, kein synced in synced)
    function canPlace(doc, pos, nodes) {
      try {
        var $p = doc.resolve(pos), parent = $p.parent, i = $p.index();
        if (parent.type.name === 'columns') return false;
        return parent.canReplace(i, i, Fragment.fromArray(nodes));
      } catch (e) { return false; }
    }
    function topOf(doc, pos) {              // Block der obersten Ebene um pos -> {node, pos}
      var $p = doc.resolve(pos); if ($p.depth === 0) { var n = $p.nodeAfter; return n ? { node: n, pos: pos } : null; }
      return { node: $p.node(1), pos: $p.before(1) };
    }

    // =================================================================================================================
    // Spalten: columns > column+ > Bloecke. Breiten per Griff (5-%-Raster, min. 0,15, Doppelklick = gleich), Anzahl 2-6
    // =================================================================================================================
    var NV = {};
    NV.columns = function (props) {
      var node = props.node, getPos = props.getPos;
      var dom = el('div', 'pm-blk pm-b-columns'), bar = el('div', 'cols-ctl pm-cols-ctl'), inner = el('div', 'cols pm-cols pm-in-c');
      var rs = el('div', 'pm-cols-rs');
      bar.contentEditable = 'false'; rs.contentEditable = 'false';
      dom.appendChild(bar); dom.appendChild(inner); dom.appendChild(rs);
      var live = null;                       // Breiten waehrend eines Zugs (nur CSSOM, noch keine Transaktion)
      function grid() {
        var n = node.childCount, ws = live || weights(node);
        dom.style.setProperty('--cols', String(n)); inner.style.setProperty('--cols', String(n));
        inner.classList.toggle('cols-many', n >= 5);
        if (ws) inner.style.setProperty('--cols-t', frs(ws)); else inner.style.removeProperty('--cols-t');
        inner.classList.toggle('cols-w', !!ws);
        while (rs.children.length > Math.max(0, n - 1)) rs.lastChild.remove();
        while (rs.children.length < n - 1) rs.appendChild(handle(rs.children.length));
        var eq = ws || equal(n), acc = 0;
        [].forEach.call(rs.children, function (h, i) {
          acc += eq[i];
          h.style.setProperty('--rs-f', String(Math.round(acc * 10000) / 10000)); h.style.setProperty('--rs-i', String(i));
        });
        drawBar();
      }
      function cur() { return node; }
      function pos() { var p = X.posOf(getPos); return p; }
      function handle(i) {
        var h = el('div', 'col-rs pm-col-rs'); h.setAttribute('aria-hidden', 'true');
        h.title = T('Ziehen ändert die Spaltenbreite, Doppelklick setzt beide gleich');
        h.addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); });
        h.addEventListener('dblclick', function (e) {
          e.preventDefault(); e.stopPropagation(); if (ro()) return;
          var n = cur(), ws = weights(n) || equal(n.childCount), sum = ws[i] + ws[i + 1];
          ws = ws.slice(); ws[i] = ws[i + 1] = sum / 2;
          commitWidths(ws);
        });
        h.addEventListener('pointerdown', function (e) {
          if (ro() || e.button !== 0) return;
          e.preventDefault(); e.stopPropagation();
          var n = cur(), cnt = n.childCount, box = inner.getBoundingClientRect(), start = weights(n) || equal(cnt), before = JSON.stringify(normWs(start));
          var left = start.slice(0, i).reduce(function (a, x) { return a + x; }, 0), pair = start[i] + start[i + 1];
          var gap = parseFloat(getComputedStyle(inner).columnGap) || 0, usable = box.width - gap * (cnt - 1);
          if (usable <= 0) return;
          try { h.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
          document.body.classList.add('hk-resizing'); h.classList.add('on');
          function mv(ev) {
            var x = (ev.clientX - box.left - gap * (i + 0.5)) / usable - left, a = Math.round((left + x) * 20) / 20 - left;  // 5-%-Raster
            a = Math.min(pair - 0.15, Math.max(0.15, a));
            var ws = start.slice(); ws[i] = a; ws[i + 1] = pair - a; live = ws; grid();
          }
          function up() {
            h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
            document.body.classList.remove('hk-resizing'); h.classList.remove('on');
            var ws = live; live = null;
            if (ws && JSON.stringify(normWs(ws)) !== before) commitWidths(ws); else grid();
          }
          h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
        });
        return h;
      }
      function commitWidths(ws) {           // ein Zug = eine Transaktion mit allen w
        var p = pos(); if (p == null || ro()) { grid(); return; }
        var tr = state().tr; setWidths(tr, p, normWs(ws));
        if (tr.docChanged) dispatch(tr); else grid();
      }
      // Leiste "Spalten 2-6" (+ "Block darunter -> Spalte n" / "Zusammenfuehren"), wie bisher
      function drawBar() {
        bar.textContent = '';
        bar.appendChild(el('span', 'cols-ctl-t', T('Spalten')));
        for (var k = 2; k <= MAX_COLS; k++) (function (k) {
          var b = X.nodeBtn('cols-ctl-b' + (node.childCount === k ? ' on' : ''), String(k), T('{n} Spalten', { n: k }));
          b.setAttribute('aria-pressed', node.childCount === k ? 'true' : 'false');
          b.addEventListener('click', function (e) { e.preventDefault(); var p = pos(); if (p != null) setCount(p, k); });
          bar.appendChild(b);
        })(k);
        var extra = el('span', 'cols-ctl-x'); bar.appendChild(extra);
        bar.onmouseenter = function () { barExtra(extra); };
      }
      function barExtra(box) {
        box.textContent = ''; var p = pos(); if (p == null || ro()) return;
        var nx = nextTop(p); if (!nx) return;
        box.appendChild(el('span', 'cols-ctl-sep', '|'));
        if (nx.node.type.name === 'columns') {
          var m = X.nodeBtn('cols-ctl-b wide', '⤓ ' + T('Zusammenführen'), T('Spalten darunter mit diesen zusammenführen (Spalte 1 + 1, 2 + 2 …)'));
          m.addEventListener('click', function (e) { e.preventDefault(); mergeBelow(p); });
          box.appendChild(m);
        } else {
          box.appendChild(el('span', 'cols-ctl-t', T('↑ Block darunter →')));
          for (var ci = 0; ci < node.childCount; ci++) (function (ci) {
            var b = X.nodeBtn('cols-ctl-b', String(ci + 1), T('Block unter den Spalten ans Ende von Spalte {n}', { n: ci + 1 }));
            b.addEventListener('click', function (e) { e.preventDefault(); takeBelow(p, ci); });
            box.appendChild(b);
          })(ci);
        }
      }
      X.paint(dom, node); grid();
      return {
        dom: dom, contentDOM: inner, ignoreMutation: X.ignoreOutside(inner),
        stopEvent: function (e) { return !!(e.target && (bar.contains(e.target) || rs.contains(e.target))); },
        update: function (n) { if (n.type !== node.type) return false; node = n; X.paint(dom, n); grid(); return true; }
      };
    };
    NV.column = function (props) {
      var node = props.node, getPos = props.getPos;
      var dom = el('div', 'col pm-col'), inner = el('div', 'col-ed pm-col-c'), add = X.nodeBtn('col-add', T('+ Block'), T('Neuer Block am Ende dieser Spalte'));
      dom.appendChild(inner); dom.appendChild(add);
      function sync() { dom.classList.toggle('pm-col-empty', !node.childCount); }
      add.addEventListener('click', function (e) { e.preventDefault(); var p = X.posOf(getPos); if (p != null) addToColumn(p); });
      dom.addEventListener('pointerdown', function (e) {           // Touch: Leisten nur an der angetippten Spalte (wie Fix27 #17)
        if (e.pointerType === 'mouse') return;
        [].forEach.call(document.querySelectorAll('.col-tap'), function (x) { if (x !== dom && !x.contains(dom)) x.classList.remove('col-tap'); });
        dom.classList.add('col-tap'); var w = dom.closest('.pm-b-columns'); if (w) w.classList.add('col-tap');
      });
      dom.addEventListener('mousedown', function (e) {             // Klick in eine leere Spalte: Absatz anlegen
        if (node.childCount || ro() || e.target === add) return;
        var p = X.posOf(getPos); if (p == null) return;
        e.preventDefault(); addToColumn(p);
      });
      sync();
      return { dom: dom, contentDOM: inner, ignoreMutation: X.ignoreOutside(inner),
        stopEvent: function (e) { return !!(e.target && add.contains(e.target)); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; } };
    };
    function addToColumn(cp) {              // "+ Block": leerer letzter Absatz -> dorthin, sonst neuer Absatz am Ende
      if (ro()) return;
      var st = state(), col = st.doc.nodeAt(cp); if (!col) return;
      var tr = st.tr, last = col.lastChild, end = cp + col.nodeSize - 1;
      if (last && last.type.name === 'paragraph' && !last.content.size) X.setSel(tr, end - 1);
      else { tr.insert(end, para()); X.setSel(tr, end + 1); }
      dispatch(tr.scrollIntoView()); ed().view.focus();
    }
    function nextTop(cpos) {
      var doc = state().doc, n = doc.nodeAt(cpos); if (!n) return null;
      var at = cpos + n.nodeSize, nx = at < doc.content.size ? doc.nodeAt(at) : null;
      return nx ? { node: nx, pos: at } : null;
    }
    // Anzahl aendern: weniger -> Inhalt entfernter Spalten in die letzte verbleibende; mehr -> leere Spalten (Absatz);
    // Breiten danach wieder gleich (wie bisher)
    function setCount(cpos, n) {
      if (ro()) return;
      var st = state(), cols = st.doc.nodeAt(cpos); if (!cols || cols.type.name !== 'columns' || n === cols.childCount) return;
      n = Math.max(2, Math.min(MAX_COLS, n));
      var list = []; cols.forEach(function (c) { list.push(c); });
      var out;
      if (n < list.length) {
        out = list.slice(0, n);
        var moved = [];                      // leere Absaetze entfernter Spalten fallen weg
        list.slice(n).forEach(function (c) { c.forEach(function (b) { if (!(b.type.name === 'paragraph' && !b.content.size)) moved.push(b); }); });
        var lastC = out[n - 1], kids = []; lastC.forEach(function (b) { kids.push(b); });
        out[n - 1] = lastC.type.create(lastC.attrs, kids.concat(moved));
      } else {
        out = list.slice();
        while (out.length < n) out.push(column([para()]));
      }
      out = out.map(function (c) { return c.type.create(Object.assign({}, c.attrs, { w: null }), c.content); });
      var tr = st.tr.replaceWith(cpos, cpos + cols.nodeSize, cols.type.create(cols.attrs, out));
      dispatch(tr);
    }
    function takeBelow(cpos, ci) {          // Block unter den Spalten ans Ende von Spalte ci
      if (ro()) return;
      var st = state(), cols = st.doc.nodeAt(cpos), nx = nextTop(cpos); if (!cols || !nx || nx.node.type.name === 'columns') return;
      if (!X.nestOk(nx.node)) { HK.toast(T('Dieser Block kann nicht in eine Spalte')); return; }
      var tr = st.tr, off = cpos + 1, end = null;
      cols.forEach(function (c, o, i) { if (i === ci) end = off + o + c.nodeSize - 1; });
      if (end == null) return;
      tr.delete(nx.pos, nx.pos + nx.node.nodeSize);
      tr.insert(end, nx.node);
      dispatch(tr);
    }
    function mergeBelow(cpos) {             // gestapelte Spalten-Bloecke zusammenfuehren (Spalte 1 + 1, 2 + 2 ...)
      if (ro()) return;
      var st = state(), a = st.doc.nodeAt(cpos), nx = nextTop(cpos); if (!a || !nx || nx.node.type.name !== 'columns') return;
      var b = nx.node, n = Math.min(MAX_COLS, Math.max(a.childCount, b.childCount)), cols = [];
      for (var k = 0; k < n; k++) {
        var kids = [];
        if (k < a.childCount) a.child(k).forEach(function (x) { kids.push(x); });
        if (k < b.childCount) b.child(k).forEach(function (x) { kids.push(x); });
        if (!kids.length) kids.push(para());
        cols.push(column(kids));
      }
      var tr = st.tr.replaceWith(cpos, nx.pos + b.nodeSize, a.type.create(a.attrs, cols));
      dispatch(tr);
      HK.toast(T('Spalten zusammengeführt'));
    }
    function unwrapColumns(cpos) {          // Spalten aufloesen: Inhalt untereinander (IDs bleiben)
      if (ro()) return;
      var st = state(), cols = st.doc.nodeAt(cpos); if (!cols || cols.type.name !== 'columns') return;
      var kids = kidsOf(cols); if (!kids.length) kids = [para()];
      dispatch(st.tr.replaceWith(cpos, cpos + cols.nodeSize, kids));
    }
    // Spalten anlegen (Slash "Spalten"): leerer Absatz wird Spalte 1 (ID bleibt), daneben n-1 leere Spalten
    function insertColumns(editor, at, n) {
      var st = editor.state, b = X.blockAt(at), tr = st.tr; if (!b) return;
      var top = topOf(st.doc, b.pos), cols = [], i;
      var empty = b.node.type.name === 'paragraph' && !b.node.content.size && b.depth === 1;
      if (empty) {
        cols.push(column([b.node]));
        for (i = 1; i < n; i++) cols.push(column([para()]));
        tr.replaceWith(b.pos, b.pos + b.node.nodeSize, columnsNode(cols));
        X.setSel(tr, b.pos + 3);
      } else {
        for (i = 0; i < n; i++) cols.push(column([para()]));
        var where = top.pos + top.node.nodeSize;
        tr.insert(where, columnsNode(cols));
        X.setSel(tr, where + 3);
      }
      editor.view.dispatch(tr.scrollIntoView()); editor.view.focus();
    }

    // Aufraeumen nach jeder eigenen Aenderung (appendTransaction, gleicher Undo-Schritt): Spalten, die in dieser Aenderung
    // leer wurden, entfallen; bleibt nur eine Spalte, wird der Spalten-Block aufgeloest (Inhalt an seine Stelle, IDs
    // bleiben); keine Spalte mehr -> Block weg. Geladene Seiten werden nie angefasst (keine Scheinaenderung).
    function tidy(trs, oldState, newState) {
      if (!trs.some(function (t) { return t.docChanged; })) return null;
      if (trs.some(function (t) { return t.getMeta('hkRemote') || t.getMeta('y-sync$') || t.getMeta('hkTidy'); })) return null;
      var map = new P.pm.transform.Mapping(); trs.forEach(function (t) { map.appendMapping(t.mapping); });
      var old = {}, wasEmpty = {};
      oldState.doc.forEach(function (n, pos) {
        if (n.type.name !== 'columns' || !n.attrs.id) return;
        old[n.attrs.id] = n.childCount;
        n.forEach(function (c, o) { if (!c.childCount) { var r = map.mapResult(pos + 1 + o, 1); if (!r.deleted) wasEmpty[r.pos] = 1; } });
      });
      var jobs = [];
      newState.doc.forEach(function (n, pos) {
        if (n.type.name !== 'columns' || !n.attrs.id || !(n.attrs.id in old)) return;
        var keep = [], dropped = 0;
        n.forEach(function (c, o) { if (!c.childCount && !wasEmpty[pos + 1 + o]) dropped++; else keep.push(c); });
        if (!dropped && !(n.childCount === 1 && old[n.attrs.id] >= 2)) return;
        jobs.push({ pos: pos, node: n, keep: keep, dropped: dropped });
      });
      if (!jobs.length) return null;
      var tr = newState.tr;
      jobs.reverse().forEach(function (j) {
        var end = j.pos + j.node.nodeSize;
        if (!j.keep.length) { tr.delete(j.pos, end); return; }
        if (j.keep.length === 1) {
          var kids = []; j.keep[0].forEach(function (b) { kids.push(b); });
          if (kids.length) tr.replaceWith(j.pos, end, kids); else tr.delete(j.pos, end);
          return;
        }
        var ws = weights(j.node);
        var cols = j.keep.map(function (c) { return c; });
        if (ws && j.dropped) {               // Gewichte der verbleibenden Spalten neu verteilen
          var rest = []; j.node.forEach(function (c, o, i) { if (cols.indexOf(c) >= 0) rest.push(ws[i]); });
          var tot = rest.reduce(function (a, x) { return a + x; }, 0), nw = normWs(rest.map(function (x) { return x / tot; }));
          cols = cols.map(function (c, i) { return c.type.create(Object.assign({}, c.attrs, { w: nw ? nw[i] : null }), c.content); });
        }
        tr.replaceWith(j.pos, end, j.node.type.create(j.node.attrs, cols));
      });
      if (!tr.doc.childCount) tr.insert(0, para());
      tr.setMeta('hkTidy', true);
      return tr;
    }

    // =================================================================================================================
    // Toggle: toggle(ttitle, Bloecke). Offen/zu steht im Dokument (T-E6 A) und wird nur beim eigenen Klick geschrieben;
    // zugeklappt ist der Inhalt per CSS verborgen (bleibt im Dokument). Lesen/gesperrt: nur lokal.
    // =================================================================================================================
    NV.toggle = function (props) {
      var node = props.node, getPos = props.getPos;
      // tgl/tgl-btn wie das Editor.js-Werkzeug (Stil des Pfeils, bestehende Tests)
      var dom = el('div', 'pm-blk pm-b-toggle pm-tgl tgl'), btn = X.nodeBtn('pm-nb pm-tgl-btn tgl-btn', '▸', T('Auf- oder zuklappen'));
      var inner = el('div', 'pm-in pm-tgl-c'), empty = X.nodeBtn('pm-tgl-empty', T('Leerer Toggle – klicken, um etwas hinzuzufügen'));
      dom.appendChild(btn); dom.appendChild(inner); dom.appendChild(empty);
      function sync() {
        X.paint(dom, node);
        var open = isOpen(node), lv = node.attrs.level;
        dom.classList.toggle('open', open);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        [1, 2, 3].forEach(function (l) { dom.classList.toggle('pm-tgl-h' + l, lv === l); });
        dom.classList.toggle('pm-tgl-h', lv === 1 || lv === 2 || lv === 3);
        empty.hidden = !(open && node.childCount === 1);
      }
      btn.addEventListener('click', function (e) {
        e.preventDefault(); var p = X.posOf(getPos); if (p == null) return;
        setOpen(p, !isOpen(node));
      });
      empty.addEventListener('click', function (e) {
        e.preventDefault(); var p = X.posOf(getPos); if (p == null || ro()) return;
        var st = state(), n = st.doc.nodeAt(p), at = p + 1 + n.firstChild.nodeSize, tr = st.tr.insert(at, para());
        X.setSel(tr, at + 1); dispatch(tr); ed().view.focus();
      });
      sync();
      return { dom: dom, contentDOM: inner, ignoreMutation: X.ignoreOutside(inner),
        stopEvent: function (e) { return !!(e.target && (btn.contains(e.target) || empty.contains(e.target))); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; } };
    };
    function setOpen(p, open) {
      var st = state(), n = st.doc.nodeAt(p); if (!n || n.type.name !== 'toggle') return;
      if (ro()) {                             // nur lokal; Node-View neu zeichnen ueber eine leere Transaktion
        LOCAL_OPEN[n.attrs.id] = open;
        var v = ed().view, d = v.nodeDOM(p);
        if (d) { d.classList.toggle('open', open); var b = d.querySelector('.pm-tgl-btn'); if (b) b.setAttribute('aria-expanded', open ? 'true' : 'false'); }
        return;
      }
      delete LOCAL_OPEN[n.attrs.id];
      var tr = st.tr.setNodeMarkup(p, null, Object.assign({}, n.attrs, { open: open }));
      tr.setMeta('addToHistory', false);      // auf/zu ist kein Undo-Schritt, wird aber gespeichert
      // Schreibmarke im zugeklappten Inhalt -> ans Ende des Titels
      var sel = st.selection;
      if (!open && sel.from > p + 1 + n.firstChild.nodeSize && sel.to < p + n.nodeSize) X.setSel(tr, p + 1 + n.firstChild.nodeSize - 1);
      dispatch(tr);
    }
    // Schreibmarke darf nicht im zugeklappten Inhalt landen (Pfeiltasten): weiter hinter bzw. vor den Toggle
    function hiddenAt(doc, pos) {
      var $p = doc.resolve(pos);
      for (var d = $p.depth - 1; d >= 0; d--) {
        var n = $p.node(d);
        if (n.type.name === 'toggle' && !isOpen(n) && $p.index(d) > 0) return { node: n, pos: d ? $p.before(d) : 0 };
      }
      return null;
    }
    function skipHidden(trs, oldState, newState) {
      if (trs.some(function (t) { return t.docChanged; })) return null;
      var sel = newState.selection; if (!(sel instanceof S0.TextSelection) || !sel.empty) return null;
      var h = hiddenAt(newState.doc, sel.head); if (!h) return null;
      var down = oldState.selection.head < h.pos, tr = newState.tr;
      if (down) tr.setSelection(S0.Selection.near(newState.doc.resolve(Math.min(newState.doc.content.size, h.pos + h.node.nodeSize)), 1));
      else X.setSel(tr, h.pos + 1 + h.node.firstChild.nodeSize - 1);
      return tr;
    }

    // =================================================================================================================
    // Synchronisierte Bloecke (P6-6): Original {sid, blocks} = Behaelter (bearbeitbar); Kopie mit vom Server eingesetztem
    // Inhalt (data.src.note) = derselbe Behaelter, bearbeitet wird wie heute ueber sync1 (Durchschreiben beim Speichern);
    // nur lesend (kein Bearbeiten auf dem Original, gesperrt) = geschuetzt wie eine Karte. Kopie ohne Inhalt (frisch
    // eingefuegt, kein Zugriff, nicht gefunden) = Knoten synced_ref mit Hinweis.
    // =================================================================================================================
    var SYNC_IC = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/></svg>';
    function srcOf(n) { var x = n.attrs.extra, d = x && x.data; return d && d.src && typeof d.src === 'object' ? d.src : null; }   // data.src steht im Modell unter extra.data
    function syncInfo(n) {
      var src = srcOf(n) || {}, copy = n.type.name === 'synced_ref' || !!(src.note != null || src.noaccess || src.missing || (src.gone && !src.original));
      var locked = copy && !!(src.noaccess || src.missing || src.role === 'view' || src.role === 'comment' || src.locked);
      return { src: src, copy: copy, locked: locked, pending: n.type.name === 'synced_ref' && !src.noaccess && !src.missing && src.note == null };
    }
    function syncLocked(n) { return n.type.name === 'synced' && syncInfo(n).locked; }
    function syncBar(n, getPos) {
      var inf = syncInfo(n), src = inf.src, bar = el('div', 'hk-sync-bar pm-sync-bar'); bar.contentEditable = 'false';
      var ic = el('span', 'hk-sync-ic'); ic.innerHTML = SYNC_IC; bar.appendChild(ic);
      var lab;
      if (src.noaccess) lab = '🔒 ' + T('Synchronisierter Block – kein Zugriff');
      else if (src.missing) lab = T('Synchronisierter Block nicht gefunden');
      else if (src.gone) lab = T('Original gelöscht – letzter Stand');
      else if (inf.pending) lab = T('Synchronisiert');
      else if (inf.copy && inf.locked) lab = T('Synchronisiert – nur lesend');
      else if (inf.copy) lab = T('Synchronisiert');
      else lab = T('Original');
      bar.appendChild(el('span', 'hk-sync-lab', lab));
      if (src.refs && !src.noaccess) bar.appendChild(el('span', 'hk-sync-n', T('auf {n} weiteren Seiten', { n: src.refs })));
      if (inf.copy && src.note != null && !src.noaccess) {
        var jb = X.nodeBtn('linkbtn hk-sync-go', T('Original öffnen'));
        jb.addEventListener('click', function (e) { e.preventDefault(); location.hash = '#' + src.note + (src.block ? ':' + encodeURIComponent(src.block) : ''); });
        bar.appendChild(jb);
      }
      if (src.gone && n.type.name === 'synced' && !ro()) {
        var tb = X.nodeBtn('linkbtn', T('Als normale Blöcke übernehmen'));
        tb.addEventListener('click', function (e) { e.preventDefault(); var p = X.posOf(getPos); if (p != null) unwrapSynced(p); });
        bar.appendChild(tb);
      }
      return bar;
    }
    NV.synced = function (props) {
      var node = props.node, getPos = props.getPos, dom = el('div', 'pm-blk pm-b-synced hk-sync'), barBox = el('div', 'pm-sync-head');
      var inner = el('div', 'hk-sync-body pm-sync-body');
      barBox.contentEditable = 'false';
      dom.appendChild(barBox); dom.appendChild(inner);
      var key = null;
      function sync() {
        X.paint(dom, node);
        var inf = syncInfo(node);
        dom.classList.toggle('copy', inf.copy); dom.classList.toggle('orig', !inf.copy); dom.classList.toggle('locked', inf.locked);
        inner.classList.toggle('ro', inf.locked);
        var k = JSON.stringify([inf.src, ro()]);
        if (k !== key) { key = k; barBox.textContent = ''; barBox.appendChild(syncBar(node, getPos)); }
      }
      sync();
      return { dom: dom, contentDOM: inner, ignoreMutation: X.ignoreOutside(inner),
        stopEvent: function (e) { return !!(e.target && barBox.contains(e.target)); },
        update: function (n) { if (n.type !== node.type) return false; node = n; sync(); return true; } };
    };
    NV.synced_ref = function (props) {
      var node = props.node, getPos = props.getPos, dom = el('div', 'pm-blk pm-b-synced hk-sync copy');
      function draw() {
        X.paint(dom, node); dom.textContent = '';
        var inf = syncInfo(node);
        dom.classList.toggle('locked', inf.locked || !inf.pending);
        dom.appendChild(syncBar(node, getPos));
        if (inf.pending) dom.appendChild(el('p', 'hk-sync-wait', T('Synchronisierter Block – Inhalt erscheint nach dem Speichern')));
      }
      draw(); dom.contentEditable = 'false';
      return { dom: dom, ignoreMutation: function () { return true; },
        stopEvent: function (e) { return !!(e.target && e.target.closest && e.target.closest('button')); },
        update: function (n) { if (n.type !== node.type) return false; if (n !== node) { node = n; draw(); } return true; } };
    };
    function unwrapSynced(p) {              // Original geloescht: Inhalt als normale Bloecke (neue IDs, wie bisher)
      var st = state(), n = st.doc.nodeAt(p); if (!n || n.type.name !== 'synced') return;
      var used = {}; X.walk(st.doc, function (x) { if (x.node.attrs.id) used[x.node.attrs.id] = 1; });
      var kids = []; n.forEach(function (c) { kids.push(c); });
      var list = kids.map(function (c) { var j = X.blockJson(c); return { type: j.type, data: j.data }; });
      var nodes = X.mk(list, true);
      dispatch(st.tr.replaceWith(p, p + n.nodeSize, nodes.length ? nodes : [para()]));
      HK.toast(T('Inhalt übernommen – der Block ist nicht mehr synchronisiert'));
    }
    function sidNew() {
      var a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', b = new Uint8Array(16), s = '';
      crypto.getRandomValues(b); for (var i = 0; i < 16; i++) s += a[b[i] % 62]; return s;
    }
    function clipSync(sid, msg) {
      var json = JSON.stringify([{ tool: 'synced', data: { sid: sid } }]);
      var h = function (e) { e.preventDefault(); e.stopImmediatePropagation(); e.clipboardData.setData('application/x-editor-js', json);
                             e.clipboardData.setData('text/plain', T('Synchronisierter Block')); };
      document.addEventListener('copy', h, true);
      var ok = false; try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      document.removeEventListener('copy', h, true);
      HK.toast(ok ? msg : T('Kopieren nicht möglich'));
      if (window.HKMention && HKMention.last) { HKMention.last.sid = sid; HKMention.last.at = Date.now(); }
      STATE.clipSid = sid;
    }
    var STATE = { clipSid: null };
    // "Als synchronisierten Block kopieren": Block wird Original {sid, blocks:[Block]} (ID bleibt am inneren Block),
    // sofort speichern, Kopie in die Zwischenablage
    function wrapSynced(id) {
      var x = X.findId(id); if (!x || ro()) return;
      var cur = K.cur(); if (!cur || cur.id < 0) { HK.toast(T('Nur auf Seiten dieses Servers möglich')); return; }
      var n = x.node;
      if (n.type.name === 'synced' || n.type.name === 'synced_ref') {
        var sid = n.attrs.sid; if (!sid || syncInfo(n).pending) { HK.toast(T('Erst speichern, dann kopieren')); return; }
        clipSync(sid, T('Synchronisierte Kopie in der Zwischenablage – auf einer anderen Seite einfügen')); return;
      }
      if (n.type.name === 'columns' || n.type.name === 'database' || inSynced(x.pos)) { HK.toast(T('Dieser Block lässt sich nicht synchronisieren')); return; }
      var s = sidNew(), wrap = schema().nodes.synced.create({ id: nid(), sid: s }, [n]);
      var tr = state().tr.replaceWith(x.pos, x.pos + n.nodeSize, wrap);
      dispatch(tr);
      if (K.dirty) K.dirty();
      if (K.save) K.save();
      clipSync(s, T('Synchronisierter Block angelegt und kopiert – auf einer anderen Seite einfügen'));
    }
    function inSynced(pos) {
      var $p = state().doc.resolve(pos);
      for (var d = $p.depth; d > 0; d--) if ($p.node(d).type.name === 'synced') return true;
      return false;
    }
    // Einfuegen einer Kopie ({tool:'synced', data:{sid}} aus Editor.js-/Tiptap-Zwischenablage)
    function pasteSynced(view, e) {
      var cd = e.clipboardData; if (!cd || ro()) return false;
      var raw = cd.getData('application/x-editor-js'); if (!raw) return false;
      var list; try { list = JSON.parse(raw); } catch (x) { return false; }
      if (!Array.isArray(list) || list.length !== 1 || !list[0] || list[0].tool !== 'synced' || !list[0].data || typeof list[0].data.sid !== 'string') return false;
      var st = view.state, b = X.blockAt(st.selection.from); if (!b) return false;
      if (inSynced(b.pos) || b.node.type.name === 'synced') { HK.toast(T('Kein synchronisierter Block in einem synchronisierten Block')); e.preventDefault(); return true; }
      var ref = schema().nodes.synced_ref.create({ id: nid(), sid: list[0].data.sid });
      var tr = st.tr, empty = b.node.type.name === 'paragraph' && !b.node.content.size;
      if (empty) tr.replaceWith(b.pos, b.pos + b.node.nodeSize, ref);
      else tr.insert(b.pos + b.node.nodeSize, ref);
      view.dispatch(tr.scrollIntoView()); e.preventDefault();
      return true;
    }
    // Einfuegen kopierter Saga-Bloecke (Blockauswahl, HTML nach Schema mit data-block-type): als Bloecke unter dem
    // aktuellen (leerer Absatz wird ersetzt), neue IDs - nicht in den Text des aktuellen Absatzes verschmelzen
    function pasteBlocks(view, e) {
      var cd = e.clipboardData; if (!cd || ro()) return false;
      var html = cd.getData('text/html'); if (!html || html.indexOf('data-block-type') < 0) return false;
      var tpl = document.createElement('template'); tpl.innerHTML = html;
      var tops = [].filter.call(tpl.content.childNodes, function (n) { return n.nodeType === 1 && n.tagName !== 'META'; });
      if (!tops.length || !tops.every(function (n) { return n.hasAttribute('data-block-type'); })) return false;
      var slice = M0.DOMParser.fromSchema(schema()).parseSlice(tpl.content), nodes = [];
      slice.content.forEach(function (n) { if (X.isBlock(n)) nodes.push(X.freshIds(n)); });
      if (!nodes.length) return false;
      var st = view.state, b = blockOfSel(st); if (!b) return false;
      var tr = st.tr, empty = b.node.type.name === 'paragraph' && !b.node.content.size, at;
      if (empty && canPlace(st.doc, b.pos, nodes)) { tr.replaceWith(b.pos, b.pos + b.node.nodeSize, nodes); at = b.pos; }
      else {
        at = b.pos + b.node.nodeSize;
        if (!canPlace(st.doc, at, nodes)) { var t = topOf(st.doc, b.pos); at = t.pos + t.node.nodeSize; }
        tr.insert(at, nodes);
      }
      var endN = 0; nodes.forEach(function (n) { endN += n.nodeSize; });
      try { tr.setSelection(S0.Selection.near(tr.doc.resolve(at + endN), -1)); } catch (x) { /* bleibt */ }
      view.dispatch(tr.scrollIntoView()); e.preventDefault();
      return true;
    }
    // Antwort auf das Speichern (static/mention.js HKMention.saved): Kopien ohne Inhalt -> neu laden; Stand des Originals
    // (synced_rev) als Basis der naechsten Durchschreibung in data.src.rev uebernehmen (ohne Aenderungsmeldung)
    function savedReply(d) {
      if (!ed()) return false;
      var revs = (d && d.synced_rev) || {}, st = state(), tr = st.tr, pend = false;
      X.walk(st.doc, function (x) {
        var n = x.node, t = n.type.name; if (t !== 'synced' && t !== 'synced_ref') return;
        var inf = syncInfo(n);
        if (inf.pending) pend = true;
        if (t === 'synced' && inf.copy && typeof revs[n.attrs.sid] === 'number' && inf.src.rev !== revs[n.attrs.sid]) {
          var ex = Object.assign({}, n.attrs.extra || {}); ex.data = Object.assign({}, ex.data || {}, { src: Object.assign({}, inf.src, { rev: revs[n.attrs.sid] }) });
          tr.setNodeMarkup(x.pos, null, Object.assign({}, n.attrs, { extra: ex }));
        }
      });
      if (tr.docChanged) { tr.setMeta('addToHistory', false); tr.setMeta('hkRemote', true); tr.setMeta('hkAllow', true); dispatch(tr); }
      return pend;
    }

    // =================================================================================================================
    // Konfliktblock (tunes.hkconflict = Attribut conflict): Leiste ueber dem Block als Dekoration. Uebernehmen = Original
    // (conflict.of) entfernen + Attribut leeren, Verwerfen = diese Fassung entfernen - je eine Transaktion
    // =================================================================================================================
    function conflictDecos(state0) {
      var out = [];
      X.walk(state0.doc, function (x) {
        var c = x.node.attrs.conflict; if (!c || typeof c !== 'object') return;
        out.push(VW.Decoration.widget(x.pos, function () { return conflictBar(x.node.attrs.id, c); }, { side: -1, key: 'cf-' + x.node.attrs.id + '-' + JSON.stringify(c), ignoreSelection: true,
          stopEvent: function () { return true; } }));
      });
      return out;
    }
    function conflictBar(id, c) {
      var bar = el('div', 'hk-cf-bar pm-cf-bar'); bar.contentEditable = 'false'; bar.setAttribute('data-cf-for', id);
      var who = c.name ? T('Fassung von {name}', { name: c.name }) : T('Andere Fassung');
      bar.appendChild(el('span', 'hk-cf-t', '⚠ ' + who + ' – ' + T('gleichzeitig geändert')));
      // Knoepfe immer zeichnen (beim Aufbau ist der Editor noch nicht da); Lesen/gesperrt blendet sie per CSS aus
      var take = X.nodeBtn('btn btn-sm hk-cf-take', T('Übernehmen')), drop = X.nodeBtn('btn btn-sm hk-cf-drop', T('Verwerfen'));
      take.addEventListener('click', function (e) { e.preventDefault(); resolveConflict(id, true); });
      drop.addEventListener('click', function (e) { e.preventDefault(); resolveConflict(id, false); });
      bar.appendChild(take); bar.appendChild(drop);
      return bar;
    }
    function resolveConflict(id, keep) {
      if (ro()) return;
      var x = X.findId(id); if (!x) return;
      var st = state(), tr = st.tr, c = x.node.attrs.conflict || {};
      if (!keep) { tr.delete(x.pos, x.pos + x.node.nodeSize); X.refill(tr, x.parent, x.slot); dispatch(tr); ed().view.focus(); return; }
      tr.setNodeMarkup(x.pos, null, Object.assign({}, x.node.attrs, { conflict: null }));
      var o = c.of != null ? X.findId(String(c.of)) : null;
      if (o && o.node.attrs.id !== id) tr.delete(tr.mapping.map(o.pos), tr.mapping.map(o.pos + o.node.nodeSize));
      dispatch(tr); ed().view.focus();
    }

    // =================================================================================================================
    // Mehrfachauswahl (Entscheidung T-E5 A): Blockauswahl ueber Ebenen und Spalten in Dokumentreihenfolge. Zustand ist
    // die Auswahl der Fassade (pm-editor.js: Plugin hkBlockSel, Dekoration .pm-sel). Anker/Kopf fuer Umschalt+Pfeil.
    // =================================================================================================================
    var SEL = { anchor: null, head: null };
    function selIds() { return X.selIds(); }
    function setBlockSel(ids, anchor, head) {
      SEL.anchor = anchor != null ? anchor : ids[0] || null; SEL.head = head != null ? head : ids[ids.length - 1] || null;
      X.select(ids);
      if (window.HKEd25 && HKEd25.updateBar) setTimeout(HKEd25.updateBar, 0);
      var v = ed() && ed().view; if (v) v.dom.classList.toggle('pm-blocksel', !!ids.length);
    }
    function clearBlockSel() { if (selIds().length) setBlockSel([]); }
    // ohne Nachkommen bereits gewaehlter Behaelter, in Dokumentreihenfolge
    function roots(ids) {
      var want = {}; ids.forEach(function (i) { want[i] = 1; });
      var out = [], inside = [];
      X.walk(state().doc, function (x) {
        var id = x.node.attrs.id; if (!want[id]) return;
        if (inside.some(function (r) { return x.pos > r[0] && x.pos < r[1]; })) return;
        out.push(x); inside.push([x.pos, x.pos + x.node.nodeSize]);
      });
      return out;
    }
    // Liste fuer Pfeiltasten: alle Bloecke, deren Behaelter offen ist (zugeklappter Toggle-Inhalt zaehlt nicht)
    function navList() {
      var doc = state().doc;
      return flat(doc).filter(function (x) { return !hiddenAt(doc, x.pos + 1); });
    }
    function rangeIds(a, b) {
      var l = navList().map(function (x) { return x.node.attrs.id; }), i = l.indexOf(a), j = l.indexOf(b);
      if (i < 0 || j < 0) return [b];
      return l.slice(Math.min(i, j), Math.max(i, j) + 1);
    }
    function blockOfSel(st) {               // innerster Block an der Schreibmarke (Liste ganz)
      var sel = st.selection;
      if (sel instanceof S0.NodeSelection && X.isBlock(sel.node)) return { node: sel.node, pos: sel.from };
      return X.blockAt(sel.head);
    }
    function selectCurrent() {
      var b = blockOfSel(state()); if (!b || !b.node.attrs.id) return false;
      var v = ed().view, tr = state().tr;
      if (!(state().selection.empty)) { X.setSel(tr, state().selection.head); v.dispatch(tr); }
      setBlockSel([b.node.attrs.id]);
      return true;
    }
    function deleteSel() {
      var rs = roots(selIds()); if (!rs.length || ro()) return false;
      var st = state(), tr = st.tr, first = rs[0], conts = {};
      rs.slice().reverse().forEach(function (x) {
        tr.delete(tr.mapping.map(x.pos), tr.mapping.map(x.pos + x.node.nodeSize));
        conts[x.parent + '/' + x.slot] = x;
      });
      Object.keys(conts).forEach(function (k) { X.refill(tr, conts[k].parent, conts[k].slot); });
      var at = Math.max(0, Math.min(tr.mapping.map(first.pos, -1), tr.doc.content.size));
      try { tr.setSelection(S0.Selection.near(tr.doc.resolve(at), -1)); } catch (e) { /* bleibt */ }
      setBlockSel([]);
      dispatch(tr.scrollIntoView());
      return true;
    }
    function selectAllBlocks() {
      var l = navList().map(function (x) { return x.node.attrs.id; });
      setBlockSel(l, l[0], l[l.length - 1]);
    }
    function selFragment(rs) { return Fragment.fromArray(rs.map(function (x) { return x.node; })); }
    function copySel(e, cut) {
      var rs = roots(selIds()); if (!rs.length) return false;
      // Serialisierer der Zwischenablage (pm-clip.js: Bild zusaetzlich als <img>), sonst das Schema
      var frag = selFragment(rs), v0 = ed() && ed().view, ser = (v0 && v0.someProp('clipboardSerializer')) || P.pm.model.DOMSerializer.fromSchema(schema()), box = document.createElement('div');
      box.appendChild(ser.serializeFragment(frag));
      var txt = rs.map(function (x) { return x.node.textBetween(0, x.node.content.size, '\n', ' '); }).filter(Boolean).join('\n\n');
      var list = rs.map(function (x) { var j = X.blockJson(x.node); var o = { type: j.type, data: j.data }; if (j.tunes && j.tunes.hkstyle) o.tunes = { hkstyle: j.tunes.hkstyle }; return o; });
      var json = JSON.stringify(list.map(function (b) { var o = { tool: b.type, data: b.data }; if (b.tunes) o.tunes = b.tunes; return o; }));
      e.clipboardData.setData('text/html', box.innerHTML);
      e.clipboardData.setData('text/plain', txt);
      e.clipboardData.setData('application/x-editor-js', json);                 // klassischer Editor (Editor.js liest es selbst)
      e.clipboardData.setData('application/x-saga-blocks', JSON.stringify({ v: 1, blocks: list }));   // Saga-Format (T4d, pm-clip.js)
      e.preventDefault();
      if (cut) deleteSel();
      return true;
    }
    function dupSel() {
      var rs = roots(selIds()); if (!rs.length || ro()) return false;
      var last = rs[rs.length - 1], st = state(), tr = st.tr, ids = [];
      var used = {}; X.walk(st.doc, function (x) { if (x.node.attrs.id) used[x.node.attrs.id] = 1; });
      var copies = rs.map(function (x) { var c = X.freshIds(x.node, used, sidNew); ids.push(c.attrs.id); return c; });
      var at = last.pos + last.node.nodeSize;
      if (!canPlace(tr.doc, at, copies)) { var t = topOf(tr.doc, last.pos); at = t.pos + t.node.nodeSize; }
      tr.insert(at, copies);
      dispatch(tr);
      setBlockSel(ids);
      return true;
    }
    // Text-Auswahl ueber Blockgrenzen -> Blockauswahl (Notion): alle innersten Bloecke zwischen Anfang und Ende
    function textToBlocks(st) {
      var sel = st.selection;
      if (!(sel instanceof S0.TextSelection) || sel.empty) return null;
      var a = X.blockAt(sel.from), b = X.blockAt(sel.to);
      if (!a || !b || a.node === b.node || a.pos === b.pos) return null;
      if (a.node.type.name === 'list' && a.pos === b.pos) return null;
      var ids = [], from = sel.from, to = sel.to;
      flat(st.doc).forEach(function (x) {
        var s = x.pos, e = x.pos + x.node.nodeSize;
        if (e <= from || s >= to) return;
        if (isCont(x.node) && !(s >= from && e <= to)) return;   // Behaelter nur ganz
        ids.push(x.node.attrs.id);
      });
      return ids.length > 1 ? ids : null;
    }
    function extend(dir) {                  // Umschalt+Pfeil
      var l = navList().map(function (x) { return x.node.attrs.id; });
      var head = SEL.head, anchor = SEL.anchor;
      if (!head || l.indexOf(head) < 0) return false;
      var j = l.indexOf(head) + dir; if (j < 0 || j >= l.length) return true;
      head = l[j];
      setBlockSel(rangeIds(anchor, head), anchor, head);
      scrollTo(head);
      return true;
    }
    function step(dir) {                    // Pfeil ohne Umschalt: Auswahl auf den Nachbarblock
      var l = navList().map(function (x) { return x.node.attrs.id; }), cur = dir < 0 ? selIds()[0] : selIds()[selIds().length - 1];
      var i = l.indexOf(SEL.head || cur); if (i < 0) i = l.indexOf(cur);
      var j = Math.max(0, Math.min(l.length - 1, i + dir));
      setBlockSel([l[j]]); scrollTo(l[j]);
      return true;
    }
    function scrollTo(id) { var x = X.findId(id); var d = x && domOf(x.pos); if (d && d.scrollIntoView) d.scrollIntoView({ block: 'nearest' }); }
    function editSel() {                    // Enter: Auswahl aufheben, Schreibmarke ans Ende des Kopfblocks
      var id = SEL.head || selIds()[0]; setBlockSel([]);
      if (id) X.focus(id, 'end');
      return true;
    }
    // Ziehen im Rand (ausserhalb der Bloecke): Rahmen, alle Bloecke, die er schneidet (innerste; Behaelter nur ganz)
    var band = null;
    function bandStart(e) {
      if (ro() || e.button !== 0 || !ed()) return;
      var t = e.target; if (!t || !t.closest) return;
      var v = ed().view, holder = X.holder(); if (!holder) return;
      if (t.closest('.pm-handles, button, input, textarea, select, a, .hk-tool, .pm-cf-bar, .page-bar, .page-head, .page-cover, .props, .db, .backlinks, .menu')) return;
      var hr = holder.getBoundingClientRect(); if (e.clientY < hr.top || e.clientY > hr.bottom) return;
      var inBlock = v.dom.contains(t) ? t.closest('[data-block-id]') : null;
      var r = v.dom.getBoundingClientRect(), side = e.clientX < r.left + 2 || e.clientX > r.right - 2;
      if (inBlock && !side) return;
      if (!inBlock && v.dom.contains(t) && t !== v.dom) return;
      // Luecke zwischen zwei Bloecken innerhalb der Textspalte (Ziel ist die Editor-Flaeche selbst, z. B. unter einer
      // Karte/Spalten mit Widgets): kein Rahmen, Klick setzt die Schreibmarke in den naechsten Textblock (clickGap)
      if (t === v.dom && !side) { clickGap(v, e); return; }
      e.preventDefault();
      band = { x: e.clientX, y: e.clientY, box: el('div', 'pm-band'), on: false, ids: [] };
      document.body.appendChild(band.box);
      document.addEventListener('mousemove', bandMove, true);
      document.addEventListener('mouseup', bandEnd, true);
    }
    // Klick in die Luecke zwischen Bloecken (Abstand/Margin, kein Block unter dem Zeiger): Schreibmarke in den
    // senkrecht naechsten bearbeitbaren Textblock an der Zeigerspalte. Ohne das blieb die alte Auswahl stehen - nach
    // dem Laden oft die Knotenauswahl der ersten Karte (z. B. Uhr-Widget in Spalten), weil mousedown abgefangen war.
    function clickGap(v, e) {
      var x = e.clientX, y = e.clientY, best = null;
      v.state.doc.descendants(function (n, pos) {
        if (!n.isTextblock) return true;
        var d = v.nodeDOM(pos); if (!d || d.nodeType !== 1) return false;
        if (d.closest('[contenteditable="false"]')) return false;                    // Text in Karten/Kopien: nicht
        var r = d.getBoundingClientRect(); if (!r.height || x < r.left || x > r.right) return false;
        var dist = y < r.top ? r.top - y : y > r.bottom ? y - r.bottom : 0;
        if (!best || dist < best.dist) best = { dist: dist, pos: pos, node: n, r: r };
        return false;
      });
      if (!best || best.dist > 40) return;                                         // nichts in der Naehe: wie bisher
      e.preventDefault();
      var hit = v.posAtCoords({ left: x, top: Math.min(best.r.bottom - 2, Math.max(best.r.top + 2, y)) });
      var lo = best.pos + 1, hi = best.pos + 1 + best.node.content.size;
      var at = hit && hit.pos >= lo && hit.pos <= hi ? hit.pos : (y < best.r.top ? lo : hi);
      var tr = v.state.tr.setSelection(S0.TextSelection.create(v.state.doc, at)).setMeta('pointer', true);
      v.dispatch(tr); v.focus();
    }
    function bandMove(e) {
      if (!band) return;
      var x1 = Math.min(band.x, e.clientX), x2 = Math.max(band.x, e.clientX), y1 = Math.min(band.y, e.clientY), y2 = Math.max(band.y, e.clientY);
      if (!band.on && Math.abs(e.clientY - band.y) + Math.abs(e.clientX - band.x) < 6) return;
      band.on = true;
      var b = band.box.style; b.left = x1 + 'px'; b.top = y1 + 'px'; b.width = (x2 - x1) + 'px'; b.height = (y2 - y1) + 'px';
      var hits = [], doc = state().doc, list = flat(doc).filter(function (x) { return !hiddenAt(doc, x.pos + 1); });
      list.forEach(function (x) {
        var d = domOf(x.pos); if (!d) return;
        var r = d.getBoundingClientRect(); if (r.bottom < y1 || r.top > y2 || r.right < x1 || r.left > x2) return;
        hits.push(x);
      });
      // Spalten: nur die getroffenen Kinder (ueber Spalten hinweg). Toggle/synchronisiert: ganz, wenn der Rahmen den
      // Kopf (Titel/Leiste) trifft, sonst nur die getroffenen Kinder
      var whole = hits.filter(function (x) {
        if (!isCont(x.node) || x.node.type.name === 'columns') return false;
        var d = domOf(x.pos), hd = d && d.querySelector(':scope > .pm-tgl-c > [data-pm-part="ttitle"], :scope > .pm-sync-head');
        var hr = hd && hd.getBoundingClientRect();
        return !!hr && hr.bottom >= y1 && hr.top <= y2;
      });
      var ids = hits.filter(function (x) {
        if (whole.some(function (w) { return w !== x && x.pos > w.pos && x.pos < w.pos + w.node.nodeSize; })) return false;
        if (whole.indexOf(x) >= 0) return true;
        return !(isCont(x.node) && hits.some(function (y) { return y !== x && y.pos > x.pos && y.pos < x.pos + x.node.nodeSize; }));
      }).map(function (x) { return x.node.attrs.id; });
      if (ids.join() !== band.ids.join()) { band.ids = ids; setBlockSel(ids); }
    }
    function bandEnd() {
      document.removeEventListener('mousemove', bandMove, true);
      document.removeEventListener('mouseup', bandEnd, true);
      if (band) { band.box.remove(); if (band.on && ed()) ed().view.focus(); }
      band = null;
    }

    // =================================================================================================================
    // Block-Griffe (+ / ⋮⋮, E2): mittig zur ersten Zeile, 24x24, 120-150 ms Einblendung. "+" -> Slash-Menue darunter
    // (Alt-Klick: darueber), "⋮⋮" Klick -> Block-Menue, Ziehen -> verschieben (E3), Umschalt-Klick -> zur Auswahl.
    // =================================================================================================================
    var H = null;                           // {box, plus, drag, id, el}
    function handleBuild() {
      var holder = X.holder(); if (!holder) return;
      var box = el('div', 'pm-handles'); box.contentEditable = 'false'; box.setAttribute('aria-hidden', 'false');
      var plus = el('button', 'pm-handle pm-handle-plus', '+'), drag = el('button', 'pm-handle pm-handle-menu', '⋮⋮');
      plus.type = drag.type = 'button'; plus.tabIndex = drag.tabIndex = -1;
      plus.title = T('Klicken: Block darunter einfügen, Alt-Klick: darüber'); plus.setAttribute('aria-label', T('Block einfügen'));
      drag.title = T('Ziehen zum Verschieben, Klicken für das Menü'); drag.setAttribute('aria-label', T('Block-Menü'));
      box.appendChild(plus); box.appendChild(drag); holder.appendChild(box);
      H = { box: box, plus: plus, drag: drag, id: null, el: null, holder: holder };
      [plus, drag].forEach(function (b) { b.addEventListener('mousedown', function (e) { e.preventDefault(); }); });
      plus.addEventListener('click', function (e) { e.preventDefault(); if (H.id) plusAt(H.id, e.altKey); });
      drag.addEventListener('pointerdown', dragDown);
      holder.addEventListener('mousemove', hover);
      holder.addEventListener('mouseleave', function (e) { if (!dragS && !(e.relatedTarget && H.box.contains(e.relatedTarget))) hideSoon(); });
      H.art = X.BOX.closest('article') || holder;           // Rahmen auch im Rand neben dem Text beginnen
      H.art.addEventListener('mousedown', bandStart);
    }
    var hideT = null;
    function hideSoon() { clearTimeout(hideT); hideT = setTimeout(function () { if (!dragS) showFor(null); }, 250); }
    function blockElAt(x, y) {
      var v = ed().view, r = v.dom.getBoundingClientRect();
      var px = Math.max(r.left + 6, Math.min(x, r.right - 6)), t = document.elementFromPoint(px, y);
      if (!t || !v.dom.contains(t)) return null;
      var b = t.closest('[data-block-id]');
      while (b && !v.dom.contains(b)) b = null;
      return b;
    }
    function hover(e) {
      if (!ed() || dragS || ro()) { if (ro()) showFor(null); return; }
      if (H.box.contains(e.target)) { clearTimeout(hideT); return; }
      var b = blockElAt(e.clientX, e.clientY);
      // auf dem Weg vom Block zum Griff (links daneben) den Block behalten
      if (H.el && H.el.isConnected && b !== H.el) {
        var r = H.el.getBoundingClientRect(), hr = H.box.getBoundingClientRect();
        if (e.clientY >= r.top && e.clientY <= r.bottom && e.clientX >= hr.left - 4 && e.clientX <= r.left + 8) { clearTimeout(hideT); return; }
      }
      if (!b) { hideSoon(); return; }
      clearTimeout(hideT);
      showFor(b);
    }
    function firstLine(b) {                 // Element der ersten Zeile im Block (Textfeld, Titel, Listenpunkt)
      var c = b.querySelector(':scope > .pm-in > [data-pm-part], :scope > .pm-in, :scope > .pm-list .pm-li-c > [data-pm-part], :scope > .pm-tgl-c > [data-pm-part]');
      return c || b;
    }
    function showFor(b) {
      if (!H) return;
      if (!b) { H.box.classList.remove('pm-handles-on'); H.id = null; H.el = null; return; }
      var id = b.getAttribute('data-block-id'); H.id = id; H.el = b;
      var hr = H.holder.getBoundingClientRect(), r = b.getBoundingClientRect(), fl = firstLine(b), fr = fl.getBoundingClientRect();
      var lh = parseFloat(getComputedStyle(fl).lineHeight); if (!(lh > 0)) lh = Math.min(fr.height || 24, 28);
      var top = (fl === b ? r.top + Math.min(14, r.height / 2) : fr.top + Math.min(lh, fr.height || lh) / 2) - 12;
      H.box.style.left = (r.left - hr.left - 50) + 'px';
      H.box.style.top = (top - hr.top) + 'px';
      H.box.classList.add('pm-handles-on');
    }
    function plusAt(id, above) {            // leeren Absatz darunter (Alt: darueber) anlegen bzw. nutzen, "/" tippen
      if (ro()) return;
      var x = X.findId(id); if (!x) return;
      var st = state(), tr = st.tr, n = x.node, at;
      if (n.type.name === 'paragraph' && !n.content.size) at = x.pos + 1;
      else {
        var where = above ? x.pos : x.pos + n.nodeSize;
        if (!canPlace(st.doc, where, [para()])) { var t = topOf(st.doc, x.pos); where = above ? t.pos : t.pos + t.node.nodeSize; }
        tr.insert(where, para()); at = where + 1;
      }
      X.setSel(tr, at); dispatch(tr.scrollIntoView()); ed().view.focus();
      setTimeout(function () { if (ed()) ed().commands.insertContent('/'); }, 0);
    }

    // --- Ziehen (eigene Zeigerlogik statt HTML5-Drag: Rand-Drop, Geisterbild, Telefon per langem Druck) -------------
    var dragS = null;
    function dragDown(e) {
      if (ro() || !H.id || (e.button !== 0 && e.pointerType === 'mouse')) return;
      e.preventDefault();
      var id = H.id, sx = e.clientX, sy = e.clientY, touch = e.pointerType !== 'mouse', started = false, timer = null;
      var btn = H.drag;
      try { btn.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
      if (touch) timer = setTimeout(function () { started = true; dragStart(id, sx, sy); }, 450);
      function mv(ev) {
        if (!started) {
          if (touch) { if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 10) { clearTimeout(timer); cleanup(); } return; }
          if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 5) return;
          started = true; dragStart(id, sx, sy);
        }
        dragMove(ev.clientX, ev.clientY);
      }
      function up(ev) {
        clearTimeout(timer); cleanup();
        if (started) { dragEnd(ev.type === 'pointercancel'); return; }
        if (e.shiftKey) { toggleInSel(id); return; }
        openBlockMenu(id, btn);
      }
      function cleanup() { btn.removeEventListener('pointermove', mv); btn.removeEventListener('pointerup', up); btn.removeEventListener('pointercancel', up); }
      btn.addEventListener('pointermove', mv); btn.addEventListener('pointerup', up); btn.addEventListener('pointercancel', up);
    }
    function toggleInSel(id) {
      var ids = selIds().slice(), i = ids.indexOf(id);
      if (i >= 0) ids.splice(i, 1); else ids.push(id);
      var order = flat().map(function (x) { return x.node.attrs.id; });
      ids.sort(function (a, b) { return order.indexOf(a) - order.indexOf(b); });
      setBlockSel(ids, ids[0], id);
    }
    function dragStart(id, x, y) {
      var ids = selIds().indexOf(id) >= 0 ? selIds().slice() : [id];
      var rs = roots(ids); if (!rs.length) return;
      var ghost = el('div', 'pm-ghost'), w = 0;
      rs.slice(0, 3).forEach(function (r) { var d = domOf(r.pos); if (d) { w = Math.max(w, d.getBoundingClientRect().width); ghost.appendChild(d.cloneNode(true)); } });
      [].forEach.call(ghost.querySelectorAll('[data-block-id]'), function (n) { n.removeAttribute('data-block-id'); });
      ghost.style.width = Math.min(w || 400, 640) + 'px';
      document.body.appendChild(ghost);
      var line = el('div', 'pm-dropline'); line.hidden = true; document.body.appendChild(line);
      rs.forEach(function (r) { var d = domOf(r.pos); if (d) d.classList.add('pm-dragsrc'); });
      document.body.classList.add('pm-dragging');
      dragS = { ids: rs.map(function (r) { return r.node.attrs.id; }), ghost: ghost, line: line, target: null, ox: 16, oy: 12 };
      dragMove(x, y);
    }
    function dragged(pos) {                 // liegt pos in einem der gezogenen Bloecke?
      return dragS.ids.some(function (i) { var x = X.findId(i); return x && pos >= x.pos && pos < x.pos + x.node.nodeSize; });
    }
    function dragNodes() { return dragS.ids.map(function (i) { var x = X.findId(i); return x && x.node; }).filter(Boolean); }
    function dragMove(x, y) {
      if (!dragS) return;
      var g = dragS.ghost.style; g.left = (x + dragS.ox) + 'px'; g.top = (y + dragS.oy) + 'px';
      if (y < 50) window.scrollBy(0, -12); else if (y > window.innerHeight - 50) window.scrollBy(0, 12);
      var t = target(x, y); dragS.target = t;
      var l = dragS.line, s = l.style;
      if (!t) { l.hidden = true; return; }
      l.hidden = false; l.classList.toggle('pm-dropline-v', !!t.v);
      if (t.v) { s.left = (t.x - 1) + 'px'; s.top = t.top + 'px'; s.width = '2px'; s.height = Math.max(8, t.bottom - t.top) + 'px'; }
      else { s.left = t.left + 'px'; s.top = (t.y - 1) + 'px'; s.width = Math.max(8, t.right - t.left) + 'px'; s.height = '2px'; }
    }
    // Ziel unter dem Zeiger: {kind: 'at'|'wrap'|'col', pos, side, index, v, Linie}
    function target(x, y) {
      var v = ed().view, doc = state().doc, nodes = dragNodes(); if (!nodes.length) return null;
      var hasCols = nodes.some(function (n) { return n.type.name === 'columns'; });
      var b = blockElAt(x, y), x0 = null;
      while (b) {
        var id = b.getAttribute('data-block-id'), hit = id ? X.findId(id) : null;
        if (hit && !dragged(hit.pos)) { x0 = hit; break; }
        b = b.parentElement && b.parentElement.closest('[data-block-id]');
        if (b && !v.dom.contains(b)) b = null;
      }
      var r;
      if (!x0) {                              // leere Spalte?
        var t0 = document.elementFromPoint(x, y), col = t0 && t0.closest ? t0.closest('.pm-col') : null;
        if (col && v.dom.contains(col)) {
          var cp = v.posAtDOM(col, 0) - 1, cn = cp >= 0 ? doc.nodeAt(cp) : null;
          if (cn && cn.type.name === 'column' && !cn.childCount && !hasCols) { r = col.getBoundingClientRect(); return { kind: 'at', pos: cp + 1, left: r.left, right: r.right, y: r.top + 6 }; }
        }
        // unter dem letzten Block
        var vr = v.dom.lastElementChild && v.dom.lastElementChild.getBoundingClientRect();
        if (vr && y > vr.bottom && x > v.dom.getBoundingClientRect().left - 60) return { kind: 'at', pos: doc.content.size, left: vr.left, right: vr.right, y: vr.bottom };
        return null;
      }
      r = b.getBoundingClientRect();
      var $p = doc.resolve(x0.pos), depth = $p.depth, n = x0.node;
      // Rand-Drop (E3): oberste Ebene -> neue Spalten; am Rand eines Spalten-Blocks (auch ueber dessen Inhalt) -> neue Spalte
      var colsTop = null;
      if (depth === 0 && n.type.name === 'columns') colsTop = { node: n, pos: x0.pos, el: b };
      else if (depth >= 2 && $p.node(depth - 1).type.name === 'columns') colsTop = { node: $p.node(depth - 1), pos: $p.before(depth - 1) };
      if (colsTop && !hasCols && colsTop.node.childCount < MAX_COLS) {
        var cel = colsTop.el || domOf(colsTop.pos), cr = cel && cel.getBoundingClientRect();
        if (cr && x < cr.left + EDGE) return { kind: 'col', pos: colsTop.pos, index: 0, v: true, x: cr.left - 4, top: cr.top, bottom: cr.bottom };
        if (cr && x > cr.right - EDGE) return { kind: 'col', pos: colsTop.pos, index: colsTop.node.childCount, v: true, x: cr.right + 4, top: cr.top, bottom: cr.bottom };
      }
      if (depth === 0 && n.type.name !== 'columns' && !hasCols && nodes.every(X.nestOk) && X.nestOk(n)) {
        if (x < r.left + EDGE) return { kind: 'wrap', pos: x0.pos, side: 'left', v: true, x: r.left - 4, top: r.top, bottom: r.bottom };
        if (x > r.right - EDGE) return { kind: 'wrap', pos: x0.pos, side: 'right', v: true, x: r.right + 4, top: r.top, bottom: r.bottom };
      }
      // Toggle: untere Haelfte des Titels eines offenen Toggles -> als erster Inhalt
      if (n.type.name === 'toggle' && isOpen(n)) {
        var tt = b.querySelector(':scope > .pm-tgl-c > [data-pm-part="ttitle"]'), tr0 = tt && tt.getBoundingClientRect();
        if (tr0 && y >= tr0.top && y <= tr0.bottom + 4 && y > (tr0.top + tr0.bottom) / 2) {
          var into = x0.pos + 1 + n.firstChild.nodeSize;
          if (canPlace(doc, into, nodes)) return { kind: 'at', pos: into, left: tr0.left + 20, right: r.right, y: tr0.bottom + 1 };
        }
      }
      var before = y < r.top + r.height / 2, at = before ? x0.pos : x0.pos + n.nodeSize;
      if (canPlace(doc, at, nodes)) return { kind: 'at', pos: at, left: r.left, right: r.right, y: before ? r.top : r.bottom };
      var top = topOf(doc, x0.pos), te = domOf(top.pos), tr1 = te ? te.getBoundingClientRect() : r;      // geht hier nicht: oberste Ebene
      at = before ? top.pos : top.pos + top.node.nodeSize;
      if (canPlace(doc, at, nodes)) return { kind: 'at', pos: at, left: tr1.left, right: tr1.right, y: before ? tr1.top : tr1.bottom };
      return null;
    }
    function dragEnd(cancel) {
      var s = dragS; dragS = null;
      if (!s) return;
      s.ghost.remove(); s.line.remove(); document.body.classList.remove('pm-dragging');
      [].forEach.call(document.querySelectorAll('.pm-dragsrc'), function (d) { d.classList.remove('pm-dragsrc'); });
      if (cancel || !s.target || ro()) return;
      drop(s.ids, s.target);
    }
    // Verschieben als eine Transaktion (Aufraeumen leerer Spalten haengt sich an)
    function drop(ids, t) {
      var st = state(), rs = roots(ids); if (!rs.length) return false;
      var nodes = rs.map(function (x) { return x.node; }), tr = st.tr;
      if (rs.some(function (x) { return t.pos > x.pos && t.pos < x.pos + x.node.nodeSize; })) return false;
      rs.slice().reverse().forEach(function (x) { tr.delete(tr.mapping.map(x.pos), tr.mapping.map(x.pos + x.node.nodeSize)); });
      var at = tr.mapping.map(t.pos, t.kind === 'at' ? -1 : 1);
      if (t.kind === 'at') {
        if (!canPlace(tr.doc, at, nodes)) return false;
        tr.insert(at, nodes);
      } else if (t.kind === 'wrap') {
        var bn = tr.doc.nodeAt(at); if (!bn) return false;
        var c1 = column([bn]), c2 = column(nodes);
        tr.replaceWith(at, at + bn.nodeSize, columnsNode(t.side === 'left' ? [c2, c1] : [c1, c2]));
      } else if (t.kind === 'col') {
        var cn = tr.doc.nodeAt(at); if (!cn || cn.type.name !== 'columns' || cn.childCount >= MAX_COLS) return false;
        var ip = at + 1; for (var i = 0; i < t.index && i < cn.childCount; i++) ip += cn.child(i).nodeSize;
        tr.insert(ip, column(nodes));
        setWidths(tr, at, null);
      }
      if (ids.length > 1) setBlockSel(rs.map(function (x) { return x.node.attrs.id; }));
      else { clearBlockSel(); var nx = X.findId(rs[0].node.attrs.id, tr.doc); if (nx) X.focusIn(tr, nx.pos, nx.node, 'end'); }
      dispatch(tr.scrollIntoView()); ed().view.focus();
      return true;
    }

    // =================================================================================================================
    // Verschieben mit der Tastatur (Cmd+Umschalt+Pfeil, keys.js -> Fassade move): auch ueber Spaltengrenzen und aus
    // Toggles heraus. Mit Blockauswahl wandert die ganze Gruppe (zusammenhaengende Geschwister).
    // =================================================================================================================
    function moveBlock(id, dir) {
      if (ro()) return false;
      var ids = selIds().length ? selIds() : [id];          // mit Blockauswahl: die Auswahl
      var rs = roots(ids); if (!rs.length) return false;
      var doc = state().doc, first = rs[0], last = rs[rs.length - 1];
      var $a = doc.resolve(first.pos), parent = $a.parent;
      var sibs = rs.every(function (x) { return doc.resolve(x.pos).parent === parent; });
      if (!sibs) { rs = [rs[0]]; first = last = rs[0]; }
      var nodes = rs.map(function (x) { return x.node; }), from = first.pos, to = last.pos + last.node.nodeSize;
      var i0 = $a.index(), i1 = doc.resolve(last.pos).index(), lo = parent.type.name === 'toggle' ? 1 : 0, target = null;
      if (dir < 0 && i0 > lo) target = from - parent.child(i0 - 1).nodeSize;
      else if (dir > 0 && i1 + 1 < parent.childCount) target = to + parent.child(i1 + 1).nodeSize;
      else {
        var d = $a.depth;
        if (parent.type.name === 'column') {       // in die Nachbarspalte, sonst vor/hinter den Spalten-Block
          var cols = $a.node(d - 1), ci = $a.index(d - 1), cpos = $a.before(d - 1), cstart = $a.before(d);
          if (dir < 0 && ci > 0) target = cstart - 1;                                  // Ende der Spalte davor
          else if (dir > 0 && ci + 1 < cols.childCount) target = cstart + parent.nodeSize + 1;   // Anfang der Spalte danach
          else target = dir < 0 ? cpos : cpos + cols.nodeSize;
        } else if (d > 0) {                         // aus Toggle/synced heraus
          var cp = $a.before(d), cn = $a.node(d);
          target = dir < 0 ? cp : cp + cn.nodeSize;
        } else return false;
      }
      var tr = state().tr;
      if (!canPlace(doc, target, nodes)) return false;
      var sel = state().selection, inside = sel.from >= from && sel.to <= to, off = sel.from - from;
      tr.delete(from, to);
      var at = tr.mapping.map(target, dir < 0 ? -1 : 1);
      tr.insert(at, nodes);
      if (inside && !selIds().length) { try { tr.setSelection(S0.TextSelection.near(tr.doc.resolve(at + off))); } catch (e) { /* bleibt */ } }
      dispatch(tr.scrollIntoView());
      announceMove(nodes[0].attrs.id, dir, nodes.length);
      return true;
    }
    // T4d (Bedienhilfen): Ansage fuer Bildschirmleser nach dem Verschieben (aria-live, unsichtbar)
    function announceMove(id, dir, n) {
      var x = X.findId(id); if (!x) return;
      var $x = state().doc.resolve(x.pos), lo = $x.parent.type.name === 'toggle' ? 1 : 0;
      var msg = (n > 1 ? T('{n} Blöcke verschoben', { n: n }) : dir < 0 ? T('Block nach oben verschoben') : T('Block nach unten verschoben')) + ' – ' +
        T('Position {i} von {n}', { i: $x.index() - lo + 1, n: $x.parent.childCount - lo });
      announce(msg);
    }
    function announce(msg) {
      var r = document.getElementById('pm-sr');
      if (!r) { r = el('div', 'pm-sr'); r.id = 'pm-sr'; r.setAttribute('role', 'status'); r.setAttribute('aria-live', 'polite'); document.body.appendChild(r); }
      r.textContent = ''; setTimeout(function () { r.textContent = msg; }, 30);
    }

    // =================================================================================================================
    // Block-Menue (Klick auf ⋮⋮, Cmd+/): Umwandeln in, Farbe, Duplizieren, Verschieben nach …, synchronisiert kopieren,
    // Link, Kommentieren, Werkzeug-Einstellungen, Loeschen
    // =================================================================================================================
    var MK = X.isMac ? '⌘' : T('Strg') + '+', ALT = X.isMac ? '⌥' : 'Alt+';
    var CONV_TO = [['p', T('Text'), '¶'], ['h1', T('Überschrift 1'), 'H1'], ['h2', T('Überschrift 2'), 'H2'], ['h3', T('Überschrift 3'), 'H3'],
      ['h4', T('Überschrift 4'), 'H4'], ['unordered', T('Aufzählung'), '•'], ['ordered', T('Nummerierte Liste'), '1.'], ['checklist', T('Checkliste'), '☑'],
      ['toggle', T('Toggle'), '▸'], ['t1', T('Toggle-Überschrift 1'), '▸1'], ['t2', T('Toggle-Überschrift 2'), '▸2'], ['t3', T('Toggle-Überschrift 3'), '▸3'],
      ['quote', T('Zitat'), '❝'], ['callout', T('Hinweis (Callout)'), '💡']];
    var TYPE_NAME = { paragraph: T('Text'), header: T('Überschrift'), list: T('Liste'), quote: T('Zitat'), callout: T('Hinweis'), delimiter: T('Trennlinie'),
      toggle: T('Toggle'), columns: T('Spalten'), synced: T('Synchronisierter Block') };
    function anchorFor(id) { var x = X.findId(id); return (x && domOf(x.pos)) || X.holder(); }
    function openBlockMenu(id, anchor) {
      var x = X.findId(id); if (!x) return;
      anchor = anchor || anchorFor(id);
      var n = x.node, t = n.type.name, readonly = ro();
      HK.openMenu(anchor, function (m) {
        m.appendChild(el('div', 'menu-label pm-blockmenu', TYPE_NAME[t] || (X.typeName && X.typeName(t)) || X.sagaType(n)));
        if (!readonly) {
          if (X.convertible(n)) {
            m.appendChild(HK.menuItem(T('Umwandeln in'), null, { icon: '⇄', sub: function (sm) {
              CONV_TO.forEach(function (c) { sm.appendChild(HK.menuItem(c[1], function () { convertBlock(id, c[0]); }, { icon: c[2] })); });
            } }));
          }
          if (X.colorable(n)) {
            m.appendChild(HK.menuItem(T('Farbe'), null, { icon: 'A', sub: function (sm) {
              var dom = anchorFor(id);
              sm.appendChild(HK.menuItem(T('Standard'), function () { K.styleAt(dom, null); }, { icon: 'A' }));
              X.COLORS.forEach(function (c) { var it = HK.menuItem(K.colorNames[c], function () { K.styleAt(anchorFor(id), { c: c }); }, { icon: 'A' }); it.querySelector('.mi-ic').classList.add('hk-c-' + c); sm.appendChild(it); });
              X.COLORS.forEach(function (c) { var it = HK.menuItem(T('{farbe} (Hintergrund)', { farbe: K.colorNames[c] }), function () { K.styleAt(anchorFor(id), { c: c, bg: true }); }, { icon: 'A' }); it.querySelector('.mi-ic').classList.add('hk-bg-' + c); sm.appendChild(it); });
            } }));
          }
          settingsItems(m, x);
          m.appendChild(el('div', 'menu-sep'));
          m.appendChild(HK.menuItem(T('Duplizieren'), function () { setBlockSel([id]); dupSel(); }, { icon: '⧉', hint: MK + 'D' }));
          m.appendChild(HK.menuItem(T('Nach oben'), function () { moveBlock(id, -1); }, { icon: '↑', hint: MK + '⇧↑' }));
          m.appendChild(HK.menuItem(T('Nach unten'), function () { moveBlock(id, 1); }, { icon: '↓', hint: MK + '⇧↓' }));
          var cur = K.cur(), copy = !!(cur && K.remoteId && K.remoteId(cur.id));
          if (window.HKEd25 && HKEd25.moveDialog) {
            m.appendChild(HK.menuItem(copy ? T('Kopieren nach …') : T('Verschieben nach …'), function () {
              setTimeout(function () { HKEd25.moveDialog({ ids: [id], holders: [anchorFor(id)], types: [X.sagaType(n)], parent: x.parent, slot: x.slot, top: x.parent == null }, anchorFor(id)); }, 0);
            }, { icon: '↪' }));
          }
          if (cur && cur.id > 0 && t !== 'columns' && t !== 'database' && !(inSynced(x.pos) && t !== 'synced')) {
            var isS = t === 'synced' || t === 'synced_ref';
            m.appendChild(HK.menuItem(isS ? T('Synchronisierten Block kopieren') : T('Als synchronisierten Block kopieren'), function () { wrapSynced(id); }, { icon: '⟳' }));
          }
        }
        m.appendChild(HK.menuItem(T('Link zum Block kopieren'), function () { K.copyLink(id); }, { icon: '🔗' }));
        if (window.HKComments) m.appendChild(HK.menuItem(T('Kommentieren'), function () { HKComments.openBlock(id); }, { icon: '💬' }));
        if (!readonly) {
          m.appendChild(el('div', 'menu-sep'));
          m.appendChild(HK.menuItem(T('Löschen'), function () { setBlockSel([id]); deleteSel(); }, { icon: '🗑', danger: true }));
        }
      }, { search: T('Aktion suchen …') });
      menuFocusBack(id);
    }
    // T4d (Bedienhilfen): Menue zu (Esc, Eintrag, Klick daneben) und der Fokus liegt nirgends -> zurueck in den Editor,
    // Schreibmarke im Block (falls es ihn noch gibt und sie nicht schon darin steht)
    var FB = { id: null, mo: null };
    function menuFocusBack(id) {
      var mel = [].filter.call(document.querySelectorAll('body > .menu'), function (m) { return !m.classList.contains('menu-sub'); })[0];
      if (!mel || !window.MutationObserver) return;
      FB.id = id;
      if (FB.mo) return;                    // ein Beobachter fuer alle Block-Menues
      FB.mo = new MutationObserver(function () {
        if (!mel.hidden || FB.id == null) return;
        var bid = FB.id; FB.id = null;
        setTimeout(function () {
          var a = document.activeElement, e = ed();
          if (!e || FB.id != null || (a && a !== document.body && a.isConnected && !mel.contains(a))) return;
          var x = X.findId(bid), s = state().selection;
          if (x && !ro() && !(s.from >= x.pos && s.to <= x.pos + x.node.nodeSize) && !selIds().length) X.focus(bid, 'end');
          else e.view.focus();
        }, 0);
      });
      FB.mo.observe(mel, { attributes: true, attributeFilter: ['hidden'] });
    }
    function settingsItems(m, x) {          // Werkzeug-Einstellungen je Typ
      var n = x.node, t = n.type.name, id = n.attrs.id;
      function setA(patch) { var y = X.findId(id); if (!y) return; dispatch(state().tr.setNodeMarkup(y.pos, null, Object.assign({}, y.node.attrs, patch))); }
      if (t === 'columns') {
        m.appendChild(HK.menuItem(T('Spaltenanzahl'), null, { icon: '▥', sub: function (sm) {
          for (var k = 2; k <= MAX_COLS; k++) (function (k) { sm.appendChild(HK.menuItem(T('{n} Spalten', { n: k }), function () { var y = X.findId(id); if (y) setCount(y.pos, k); }, { on: n.childCount === k })); })(k);
        } }));
        m.appendChild(HK.menuItem(T('Spalten gleich breit'), function () { var y = X.findId(id); if (y) { var tr = state().tr; setWidths(tr, y.pos, null); if (tr.docChanged) dispatch(tr); } }, { icon: '⇹' }));
        var nx = nextTop(x.pos);
        if (nx && nx.node.type.name === 'columns') m.appendChild(HK.menuItem(T('Mit den Spalten darunter zusammenführen'), function () { var y = X.findId(id); if (y) mergeBelow(y.pos); }, { icon: '⤓' }));
        else if (nx) {
          m.appendChild(HK.menuItem(T('Block darunter in Spalte …'), null, { icon: '↑', sub: function (sm) {
            for (var c = 0; c < n.childCount; c++) (function (c) { sm.appendChild(HK.menuItem(T('Spalte {n}', { n: c + 1 }), function () { var y = X.findId(id); if (y) takeBelow(y.pos, c); })); })(c);
          } }));
        }
        m.appendChild(HK.menuItem(T('Spalten auflösen'), function () { var y = X.findId(id); if (y) unwrapColumns(y.pos); }, { icon: '☰' }));
      } else if (t === 'toggle') {
        [[0, T('Toggle')], [1, T('Toggle-Überschrift 1')], [2, T('Toggle-Überschrift 2')], [3, T('Toggle-Überschrift 3')]].forEach(function (o) {
          if ((n.attrs.level || 0) !== o[0]) m.appendChild(HK.menuItem(o[1], function () { setA({ level: o[0] || null }); }, { icon: '▸' }));
        });
      } else if (t === 'list' && n.attrs.listStyle === 'ordered') {
        var meta = n.attrs.meta || {};
        m.appendChild(HK.menuItem(T('Zählart'), null, { icon: '1.', sub: function (sm) {
          [['numeric', '1, 2, 3'], ['lower-alpha', 'a, b, c'], ['upper-alpha', 'A, B, C'], ['lower-roman', 'i, ii, iii'], ['upper-roman', 'I, II, III']].forEach(function (c) {
            sm.appendChild(HK.menuItem(c[1], function () { var mm = Object.assign({}, meta); if (c[0] === 'numeric') delete mm.counterType; else mm.counterType = c[0]; setA({ meta: mm }); }, { on: (meta.counterType || 'numeric') === c[0] }));
          });
        } }));
        m.appendChild(HK.menuItem(T('Beginnt mit …'), function () {
          var v = window.prompt(T('Nummer des ersten Punkts'), String(typeof meta.start === 'number' ? meta.start : 1));
          if (v == null) return; var k = parseInt(v, 10); if (!isFinite(k)) return;
          var mm = Object.assign({}, meta); if (k === 1) delete mm.start; else mm.start = Math.max(0, Math.min(99999, k)); setA({ meta: mm });
        }, { icon: '#' }));
      } else if (t === 'quote') {
        m.appendChild(HK.menuItem(n.attrs.alignment === 'center' ? T('Linksbündig') : T('Zentriert'), function () { setA({ alignment: n.attrs.alignment === 'center' ? 'left' : 'center' }); }, { icon: '⇔' }));
      }
      if (X.settings) X.settings(m, x);   // weitere Typen (pm-tools-medien.js: Bild, Einbetten)
    }
    function convertBlock(id, key) {
      var x = X.findId(id); if (!x || ro()) return;
      var n = x.node;
      if (n.type.name === 'toggle' && n.childCount > 1 && !/^(toggle|t[1-3])$/.test(key)) { HK.toast(T('Der Toggle hat Inhalt – erst ausräumen, dann umwandeln')); return; }
      var tr = state().tr, cp = X.convertAt(tr, x.pos, key);
      if (cp == null) { HK.toast(T('Dieser Block lässt sich nicht umwandeln')); return; }
      X.setSel(tr, cp); dispatch(tr.scrollIntoView()); ed().view.focus();
    }

    // =================================================================================================================
    // Tasten (vor denen des Kerns): Toggle-Titel/-Inhalt, Mehrfachauswahl, Cmd+A zweimal
    // =================================================================================================================
    function tglCtx(st) {
      var $f = st.selection.$from; if ($f.parent.type.name !== 'ttitle') return null;
      var d = $f.depth - 1; return { $f: $f, node: $f.node(d), pos: $f.before(d) };
    }
    function enterTitle(st) {
      var c = tglCtx(st); if (!c) return false;
      var tr = st.tr, $f = c.$f, title = $f.parent, sel = st.selection;
      if (!sel.empty) tr.deleteSelection();
      var $h = tr.doc.resolve(tr.mapping.map(sel.from)), rest = $h.parent.content.cut($h.parentOffset);
      var tn = tr.doc.nodeAt(c.pos);
      if (!isOpen(tn) && !rest.size) {        // zugeklappt, am Ende: neuer Absatz hinter dem Toggle
        var at = c.pos + tn.nodeSize; tr.insert(at, para()); X.setSel(tr, at + 1); dispatch(tr.scrollIntoView()); return true;
      }
      if (rest.size) tr.delete($h.pos, $h.pos + rest.size);
      tn = tr.doc.nodeAt(c.pos);
      if (!isOpen(tn)) { tr.setNodeMarkup(c.pos, null, Object.assign({}, tn.attrs, { open: true })); delete LOCAL_OPEN[tn.attrs.id]; }
      var into = c.pos + 1 + tr.doc.nodeAt(c.pos).firstChild.nodeSize;
      tr.insert(into, para(rest));
      X.setSel(tr, into + 1);
      dispatch(tr.scrollIntoView());
      void title;
      return true;
    }
    function enterEmptyLast(st) {           // leerer letzter Absatz im Toggle -> hinter den Toggle
      var sel = st.selection, $f = sel.$from; if (!sel.empty || $f.parent.type.name !== 'paragraph' || $f.parent.content.size) return false;
      var d = $f.depth; if (d < 2) return false;
      var cont = $f.node(d - 1); if (cont.type.name !== 'toggle') return false;
      if ($f.index(d - 1) !== cont.childCount - 1) return false;
      var cpos = $f.before(d - 1), ppos = $f.before(d), p0 = $f.parent, tr = st.tr;
      tr.delete(ppos, ppos + p0.nodeSize);
      var at = tr.mapping.map(cpos + cont.nodeSize);
      if (!canPlace(tr.doc, at, [p0])) return false;
      tr.insert(at, p0); X.setSel(tr, at + 1);
      dispatch(tr.scrollIntoView());
      return true;
    }
    function backTitle(st) {                // Rueckschritt am Titelanfang: Toggle -> Absatz, Inhalt rueckt dahinter (IDs bleiben)
      var c = tglCtx(st); if (!c || !st.selection.empty || c.$f.parentOffset !== 0) return false;
      var n = c.node, kids = [];
      n.forEach(function (k, o, i) { if (i > 0) kids.push(k); });
      var p = schema().nodes.paragraph.create({ id: n.attrs.id, style: n.attrs.style }, n.firstChild.content);
      var out = [p].concat(kids);
      if (!out.every(function (k, i) { return i === 0 || canPlace(st.doc, c.pos, [k]); })) return false;
      var tr = st.tr.replaceWith(c.pos, c.pos + n.nodeSize, out);
      X.setSel(tr, c.pos + 1); dispatch(tr.scrollIntoView());
      return true;
    }
    var lastModA = 0;
    function modA(st) {                     // 1. Cmd+A: Text des Blocks; 2. Cmd+A: alle Bloecke
      if (selIds().length) { selectAllBlocks(); return true; }
      var sel = st.selection, $f = sel.$from;
      if (!$f.parent.isTextblock) { selectAllBlocks(); return true; }
      var a = $f.start(), b = $f.end();
      if ((sel.from === a && sel.to === b && $f.parent === sel.$to.parent) || Date.now() - lastModA < 600 && sel.from === a && sel.to === b) { selectAllBlocks(); return true; }
      if (!$f.parent.content.size) { selectAllBlocks(); return true; }
      lastModA = Date.now();
      dispatch(st.tr.setSelection(S0.TextSelection.create(st.doc, a, b)));
      return true;
    }
    function shiftArrow(view, dir) {        // Umschalt+Pfeil am Rand des Textblocks -> Blockauswahl beginnen
      var st = view.state, sel = st.selection;
      if (!view.endOfTextblock(dir < 0 ? 'up' : 'down')) return false;
      var b = X.blockAt(sel.head); if (!b || !b.node.attrs.id) return false;
      if (b.node.type.name === 'list') return false;
      var $h = sel.$head, edge = dir < 0 ? $h.parentOffset === 0 || sel.head <= sel.anchor : $h.parentOffset === $h.parent.content.size || sel.head >= sel.anchor;
      if (!edge) return false;
      var a = X.blockAt(sel.anchor), aid = a && a.node.attrs.id ? a.node.attrs.id : b.node.attrs.id;
      var tr = st.tr; X.setSel(tr, sel.head); view.dispatch(tr);
      setBlockSel([aid], aid, aid);
      if (aid === b.node.attrs.id) return true;
      setBlockSel(rangeIds(aid, b.node.attrs.id), aid, b.node.attrs.id);
      return true;
    }
    function keys(editor) {
      function st() { return editor.state; }
      return {
        'Enter': function () { if (X.menuActive()) return false; if (selIds().length) return editSel(); return enterTitle(st()) || enterEmptyLast(st()); },
        'Backspace': function () { if (selIds().length) return deleteSel(); return backTitle(st()); },
        'Delete': function () { return selIds().length ? deleteSel() : false; },
        'Escape': function () {
          if (X.menuActive() || HK.menuOpen() || document.querySelector('.pm-pop:not([hidden])')) return false;
          if (selIds().length) { clearBlockSel(); return true; }
          return selectCurrent();
        },
        'ArrowUp': function () { return selIds().length ? step(-1) : false; },
        'ArrowDown': function () { return selIds().length ? step(1) : false; },
        'Shift-ArrowUp': function () { return selIds().length ? extend(-1) : shiftArrow(editor.view, -1); },
        'Shift-ArrowDown': function () { return selIds().length ? extend(1) : shiftArrow(editor.view, 1); },
        'Mod-a': function () { return modA(st()); },
        'Mod-d': function () { if (selIds().length) return dupSel(); return false; }
      };
    }

    // =================================================================================================================
    // Plugin: Aufraeumen (Spalten), Toggle-Sprung, Konflikt-Leisten, Auswahl-Wechsel, Einfuegen synchronisierter Kopien
    // =================================================================================================================
    var CFKEY = new S0.PluginKey('hkStruct');
    function plugin() {
      return new S0.Plugin({
        key: CFKEY,
        appendTransaction: function (trs, o, n) { return tidy(trs, o, n) || skipHidden(trs, o, n); },
        state: {
          init: function (c, s) { return VW.DecorationSet.create(s.doc, conflictDecos(s)); },
          apply: function (tr, set, o, s) { return tr.docChanged || tr.getMeta('hkCf') ? VW.DecorationSet.create(s.doc, conflictDecos(s)) : set.map(tr.mapping, tr.doc); }
        },
        props: {
          decorations: function (s) { return CFKEY.getState(s); },
          handleDOMEvents: {
            copy: function (v, e) { return selIds().length ? copySel(e, false) : false; },
            cut: function (v, e) { return selIds().length && !ro() ? copySel(e, true) : false; },
            mousedown: function (v, e) { if (selIds().length && !e.shiftKey && !(e.target.closest && e.target.closest('.pm-handles'))) clearBlockSel(); return false; },
            keydown: function (v, e) {
              if (!selIds().length || e.metaKey || e.ctrlKey || e.altKey) return false;
              if (e.key.length === 1) { clearBlockSel(); }
              return false;
            }
          },
          handlePaste: function (v, e) { return pasteSynced(v, e) || pasteBlocks(v, e); }
        },
        view: function (view) {
          var down = false;
          function md() { down = true; }
          function mu() { down = false; setTimeout(convertText, 0); }
          function convertText() {
            if (!ed() || down) return;
            var ids = textToBlocks(view.state); if (!ids) return;
            var tr = view.state.tr; X.setSel(tr, view.state.selection.head); view.dispatch(tr);
            setBlockSel(ids);
          }
          view.dom.addEventListener('mousedown', md); document.addEventListener('mouseup', mu);
          setTimeout(handleBuild, 0);
          return {
            update: function (v, prev) {
              if (!prev.selection.eq(v.state.selection) && !down) setTimeout(convertText, 0);
              if (H && H.el && !H.el.isConnected) showFor(null);
            },
            destroy: function () {
              view.dom.removeEventListener('mousedown', md); document.removeEventListener('mouseup', mu);
              if (H) { H.box.remove(); if (H.art) H.art.removeEventListener('mousedown', bandStart); H = null; }
              if (dragS) { dragS.ghost.remove(); dragS.line.remove(); dragS = null; document.body.classList.remove('pm-dragging'); }
              bandEnd(); SEL.anchor = SEL.head = null;
            }
          };
        }
      });
    }

    // Slash-Eintraege (E1)
    var G = T('Grundblöcke');
    X.slashReg({ key: 'toggle', group: G, t: T('Toggle'), ic: '▸', desc: T('Ausklappbarer Abschnitt'), kw: 'toggle ausklappen aufklappen details', kbd: '>', order: 21 });
    [1, 2, 3].forEach(function (l) {
      X.slashReg({ key: 't' + l, group: G, t: T('Toggle-Überschrift {n}', { n: l }), ic: '▸' + l, desc: T('Überschrift, die sich auf- und zuklappen lässt'), kw: 'toggle heading ueberschrift', order: 21 + l / 10 });
    });
    [2, 3, 4].forEach(function (k, i) {
      X.slashReg({ key: 'cols' + k, group: G, t: T('{n} Spalten', { n: k }), ic: '▥', desc: T('Blöcke nebeneinander'), kw: 'spalten columns nebeneinander layout', order: 24 + i / 10,
        run: function (editor, at) { if (X.inNested(at)) { HK.toast(T('Spalten gehen nur auf der obersten Ebene – unter dem Block eingefügt')); } insertColumns(editor, at, k); } });
    });
    X.slashReg({ key: 'synced', group: 'Saga', t: T('Synchronisierter Block'), ic: '⟳', desc: T('Inhalt, der auf mehreren Seiten gleich bleibt'), kw: 'synchronisiert synced sync kopie', order: 79,
      run: function (editor, at) {
        var st = editor.state, b = X.blockAt(at); if (!b) return;
        var cur = K.cur(); if (!cur || cur.id < 0) { HK.toast(T('Nur auf Seiten dieses Servers möglich')); return; }
        var top = topOf(st.doc, b.pos), inner = para(), node = schema().nodes.synced.create({ id: nid(), sid: sidNew() }, [inner]), tr = st.tr;
        if (inSynced(b.pos)) { HK.toast(T('Kein synchronisierter Block in einem synchronisierten Block')); return; }
        var empty = b.node.type.name === 'paragraph' && !b.node.content.size, where;
        if (empty && canPlace(st.doc, b.pos, [node])) { tr.replaceWith(b.pos, b.pos + b.node.nodeSize, node); where = b.pos; }
        else { where = canPlace(st.doc, b.pos + b.node.nodeSize, [node]) ? b.pos + b.node.nodeSize : top.pos + top.node.nodeSize; tr.insert(where, node); }
        X.setSel(tr, where + 2); editor.view.dispatch(tr.scrollIntoView()); editor.view.focus();
      } });

    return {
      edit: ['toggle', 'columns', 'synced'],
      nodeViews: NV,
      plugins: function () { return [plugin()]; },
      keys: keys,
      protect: function (n) { return syncLocked(n); },
      api: { setOpen: setOpen, setCount: setCount, takeBelow: takeBelow, mergeBelow: mergeBelow, unwrapColumns: unwrapColumns, moveBlock: moveBlock,
             openBlockMenu: openBlockMenu, selectBlocks: setBlockSel, deleteSel: deleteSel, dupSel: dupSel, drop: drop, wrapSynced: wrapSynced, resolveConflict: resolveConflict,
             savedReply: savedReply, selectAll: selectAllBlocks, roots: function (ids) { return roots(ids).map(function (x) { return x.node.attrs.id; }); },
             handle: function () { return H; }, target: function (x, y) { return target(x, y); } }
    };
  });
})();
