/* Zwischenablage im Tiptap-Editor (2.5, Tiptap-Umbau T4d "Querschnitte", Konzept 3.3).
 *
 * Wird von static/pm-editor.js mitgeladen (EXT_SCRIPTS) und meldet sich mit HKPmEditor.use an (Plugin hkClip, early).
 *
 * Kopieren/Ausschneiden einer Textauswahl ueber mehrere Bloecke (Tastatur; Maus-Auswahlen werden in pm-struktur.js zur
 * Blockauswahl, die kopiert copySel dort mit denselben Formaten):
 *   text/html                  ProseMirror-Slice ueber clipboardSerializer (Bild zusaetzlich als <img> fuer andere Programme)
 *   text/plain                 Text
 *   application/x-editor-js    [{tool, data, tunes?}] - Format des klassischen Editors (Editor.js liest es selbst)
 *   application/x-saga-blocks  {v: 1, blocks: [{type, data, tunes?}]} - Saga-Format (pm-tools-medien.js clipList)
 *   Eine Auswahl innerhalb eines Textblocks bleibt beim ProseMirror-Standard (Text wird beim Einfuegen eingefuegt, nicht
 *   als neuer Block).
 * Einfuegen:
 *   - Saga-/Editor.js-Bloecke: pm-tools-medien.js pasteSaga (fixRefs /api/files/copy + cleanClip, neue IDs).
 *   - HTML aus diesem Editor mit Dateiverweisen /files/<id>, aber ohne Block-JSON (aeltere Kopie, T3-Weg pasteBlocks,
 *     Textbereich ueber ein Bild): Verweise ueber /api/files/copy fuer die Zielseite umschreiben, dann dasselbe Einfuegen
 *     noch einmal mit dem umgeschriebenen HTML (pasteBlocks bzw. ProseMirror selbst, UniqueID vergibt neue IDs).
 *   - Fremdes HTML (Word, Google Docs, Webseiten): clipboardParser baut Saga-Bloecke (Absatz, Ueberschrift h1-h6 -> 1-4,
 *     Liste ul/ol/li verschachtelt inkl. Word-mso-list und Google-aria-level, Checkliste, Zitat, Code, Tabelle, Trennlinie)
 *     und bereinigt sie mit K.cleanClip (Positivliste wie der Server). Inline nur b/i/u/s/mark/code/a(http/https/mailto)/br
 *     (Google-/Word-Stile font-weight/font-style/text-decoration werden zu b/i/u/s). Bilder werden nie geladen: Link auf
 *     die Adresse (http/https), sonst weggelassen; Bilddateien in der Zwischenablage laedt pm-tools-medien.js hoch.
 *     In Textbereichen ohne Bloecke (Listenpunkt, Zelle, Titel, Unterschrift) nur Inline-Inhalt.
 */
(function (root) {
  'use strict';
  var T = root.T || function (s, p) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return p && k in p ? p[k] : m; }); };
  var SAGA_MIME = 'application/x-saga-blocks', EJS_MIME = 'application/x-editor-js';
  var FILE_REF = /\/files\/[0-9a-f]{24}/g;
  function K() { return root.HKPages && root.HKPages.k; }
  function toast(m) { if (root.HK && HK.toast) HK.toast(m); }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }

  // ===================================================================================================================
  // Fremdes HTML -> Saga-Bloecke (Editor.js-Datenform; bereinigt wird danach mit K.cleanClip)
  // ===================================================================================================================
  var DROP = /^(script|style|template|iframe|frame|frameset|object|embed|noscript|svg|math|textarea|select|button|form|title|head|link|meta|input|video|audio|canvas|map|xml)$/;
  var BLOCK = /^(p|div|h[1-6]|ul|ol|li|blockquote|pre|table|thead|tbody|tfoot|tr|td|th|hr|figure|figcaption|section|article|header|footer|main|nav|aside|dl|dt|dd|center|address|details|summary|body|html)$/;
  var MAX_NODES = 20000;
  function tag(n) { return n.nodeType === 1 ? n.tagName.toLowerCase().replace(/^[a-z]+:/, '') : ''; }
  function styleOf(n) { return String((n.getAttribute && n.getAttribute('style')) || '').toLowerCase(); }
  function hasBlock(n) {
    for (var c = n.firstChild; c; c = c.nextSibling) if (c.nodeType === 1 && (BLOCK.test(tag(c)) || hasBlock(c))) return true;
    return false;
  }
  function imgLink(n) {
    var src = String(n.getAttribute('src') || '').trim();
    if (!/^https?:\/\//i.test(src)) return '';                       // data:/blob:/relativ: nicht uebernehmen
    var alt = String(n.getAttribute('alt') || '').trim() || T('Bild');
    return '<a href="' + esc(src) + '">' + esc(alt) + '</a>';
  }
  function okHref(h) { h = String(h || '').trim(); return /^(https?:|mailto:)/i.test(h) || /^\/notes#[1-9][0-9]{0,17}$/.test(h) ? h : ''; }
  // Inline-Inhalt eines Elements als HTML nach der Positivliste (Tags b/i/u/s/mark/code/a/br)
  function inline(n, st) {
    st = st || { n: 0 };
    var out = '';
    for (var c = n.firstChild; c; c = c.nextSibling) {
      if (++st.n > MAX_NODES) break;
      out += inlineNode(c, st, out);
    }
    return out;
  }
  function inlineNode(c, st, before) {
    if (c.nodeType === 3) return esc(c.nodeValue.replace(/[\s\u00a0]+/g, ' '));
    if (c.nodeType !== 1) return '';
    var t = tag(c), s = styleOf(c);
    if (DROP.test(t) || /mso-list:\s*ignore/.test(s)) return '';       // Word: Aufzaehlungszeichen der Liste
    if (t === 'br') return '<br>';
    if (t === 'img') return imgLink(c);
    var inner = inline(c, st);
    if (t === 'ul' || t === 'ol' || t === 'table') return ' ' + inner + ' ';
    if (t === 'a') { var h = okHref(c.getAttribute('href')); return h && !emptyInline(inner) ? '<a href="' + esc(h) + '">' + inner + '</a>' : inner; }
    if (!inner) return '';
    var wrap = [], bold = t === 'b' || t === 'strong';
    if (bold && /font-weight:\s*(normal|400|300|lighter)/.test(s)) bold = false;   // Google Docs: <b style="font-weight:normal" id="docs-internal-guid-…">
    if (/font-weight:\s*(bold|bolder|[6-9]00)/.test(s)) bold = true;
    if (bold) wrap.push('b');
    if (t === 'i' || t === 'em' || t === 'cite' || /font-style:\s*italic/.test(s)) wrap.push('i');
    if (t === 'u' || t === 'ins' || /text-decoration[^;]*underline/.test(s)) wrap.push('u');
    if (t === 's' || t === 'strike' || t === 'del' || /text-decoration[^;]*line-through/.test(s)) wrap.push('s');
    if (t === 'mark') wrap.push('mark');
    if (t === 'code' || t === 'kbd' || t === 'samp' || t === 'tt') wrap.push('code');
    var sep = BLOCK.test(t) && before && !/(<br>|\s)$/.test(before) ? ' ' : '';
    return sep + wrap.map(function (w) { return '<' + w + '>'; }).join('') + inner + wrap.slice().reverse().map(function (w) { return '</' + w + '>'; }).join('');
  }
  function trimInline(h) { return h.replace(/^(\s|<br>)+/, '').replace(/(\s|<br>)+$/, ''); }
  function emptyInline(h) { return !h.replace(/<[^>]*>/g, '').replace(/&nbsp;/g, '').trim(); }
  // Inhalt eines Behaelters (li, td, blockquote) mit Bloecken darin: Absaetze mit <br> verbunden
  function flatInline(n, st) {
    if (!hasBlock(n)) return trimInline(inline(n, st));
    var parts = [];
    collect(n, parts, st);
    return parts.map(blockInline).filter(function (x) { return x && !emptyInline(x); }).join('<br>');
  }
  function blockInline(b) {
    var d = b.data || {};
    if (b.type === 'list') return (d.items || []).map(function lit(it) { return [it.content].concat((it.items || []).map(lit)).join('<br>'); }).join('<br>');
    if (b.type === 'table') return (d.content || []).map(function (r) { return r.join(' · '); }).join('<br>');
    if (b.type === 'code') return esc(d.code || '').replace(/\n/g, '<br>');
    return d.text || '';
  }
  function wordList(p) {                                            // Word: <p class=MsoListParagraph style="mso-list:l0 level2 lfo1">
    var s = styleOf(p), cls = String(p.className || '');
    if (!/mso-list:\s*l\d/.test(s) && !/MsoListParagraph/i.test(cls)) return null;
    if (/mso-list:\s*none/.test(s)) return null;
    var lv = /level(\d+)/.exec(s), mark = '';
    var ig = p.querySelector('[style*="mso-list"]');
    if (ig && /ignore/i.test(ig.getAttribute('style') || '')) mark = ig.textContent.trim();
    return { level: lv ? Math.max(1, Math.min(8, +lv[1])) : 1, ordered: /^([0-9]+|[a-z]|[ivxlcdm]+)[.)]$/i.test(mark) };
  }
  function listItem(content, level, meta) { return { content: content, meta: meta || {}, items: [], _lv: level }; }
  function nestItems(flat) {                                        // [{_lv}] -> verschachtelt
    var top = [], stack = [];
    flat.forEach(function (it) {
      var lv = it._lv || 1; delete it._lv;
      while (stack.length && stack[stack.length - 1].lv >= lv) stack.pop();
      (stack.length ? stack[stack.length - 1].it.items : top).push(it);
      stack.push({ lv: lv, it: it });
    });
    return top;
  }
  function checkOf(li) {
    var cb = li.querySelector('input[type="checkbox"]');
    if (cb) return { checked: cb.checked || cb.hasAttribute('checked') };
    var r = li.getAttribute('role') === 'checkbox' ? li : li.querySelector('[role="checkbox"]');
    if (r) return { checked: r.getAttribute('aria-checked') === 'true' };
    return null;
  }
  function listBlock(n, st) {
    var flat = [], check = false, ordered = tag(n) === 'ol';
    (function walk(list, base) {
      for (var c = list.firstChild; c; c = c.nextSibling) {
        if (c.nodeType !== 1) continue;
        var t = tag(c);
        if (t === 'ul' || t === 'ol') { walk(c, base + 1); continue; }   // Liste direkt in der Liste (Google Docs, aeltere Seiten)
        if (t !== 'li') { var x = flatInline(c, st); if (!emptyInline(x)) flat.push(listItem(x, base)); continue; }
        var al = +c.getAttribute('aria-level'), lv = al > 0 ? base + al - 1 : base, cb = checkOf(c);
        if (cb) check = true;
        var clone = c.cloneNode(true);
        [].slice.call(clone.querySelectorAll('ul, ol')).forEach(function (s) { s.remove(); });
        flat.push(listItem(flatInline(clone, st), lv, cb ? cb : null));
        [].forEach.call(c.children, function (s) { if (tag(s) === 'ul' || tag(s) === 'ol') walk(s, lv + 1); });
      }
    })(n, 1);
    if (!flat.length) return null;
    flat.forEach(function (it) { if (check) it.meta = { checked: !!(it.meta && it.meta.checked) }; else it.meta = {}; });
    return { type: 'list', data: { style: check ? 'checklist' : ordered ? 'ordered' : 'unordered', meta: {}, items: nestItems(flat) } };
  }
  function tableBlock(n, st) {
    var rows = [], head = false;
    [].forEach.call(n.querySelectorAll('tr'), function (tr) {
      if (tr.closest('table') !== n || rows.length >= 500) return;
      var cells = [];
      [].forEach.call(tr.children, function (td) { var t = tag(td); if ((t === 'td' || t === 'th') && cells.length < 50) cells.push(flatInline(td, st)); });
      if (!rows.length && (tr.closest('thead') || (tr.children.length && [].every.call(tr.children, function (x) { return tag(x) === 'th'; })))) head = true;
      if (cells.length) rows.push(cells);
    });
    if (!rows.length) return null;
    var w = Math.max.apply(null, rows.map(function (r) { return r.length; }));
    rows.forEach(function (r) { while (r.length < w) r.push(''); });
    return { type: 'table', data: { withHeadings: head, content: rows } };
  }
  function collect(n, out, st) {
    var buf = '', wl = null;
    function flush() { var h = trimInline(buf); buf = ''; if (!emptyInline(h)) out.push({ type: 'paragraph', data: { text: h } }); }
    function flushList() { if (wl) { out.push({ type: 'list', data: { style: wl.ordered ? 'ordered' : 'unordered', meta: {}, items: nestItems(wl.items) } }); wl = null; } }
    for (var c = n.firstChild; c; c = c.nextSibling) {
      if (++st.n > MAX_NODES || out.length > 2000) break;
      if (c.nodeType === 3) { buf += esc(c.nodeValue.replace(/[\s ]+/g, ' ')); continue; }
      if (c.nodeType !== 1) continue;
      var t = tag(c);
      if (DROP.test(t)) continue;
      if (t === 'p' || t === 'div') {                                   // Word-Liste: aufeinanderfolgende Absaetze sammeln
        var li = t === 'p' ? wordList(c) : null;
        if (li) {
          flush();
          if (!wl) wl = { ordered: li.ordered, items: [] };
          wl.items.push(listItem(trimInline(inline(c, st)), li.level));
          continue;
        }
      }
      if (wl && !(c.nodeType === 1 && t === 'br')) flushList();
      if (!BLOCK.test(t) && !hasBlock(c)) { buf += inlineNode(c, st, buf); continue; }
      flush();
      var b = null;
      if (/^h[1-6]$/.test(t)) { var ht = trimInline(inline(c, st)); if (!emptyInline(ht)) b = { type: 'header', data: { text: ht, level: Math.min(4, +t.slice(1)) } }; }
      else if (t === 'ul' || t === 'ol') b = listBlock(c, st);
      else if (t === 'blockquote') { var q = flatInline(c, st); if (!emptyInline(q)) b = { type: 'quote', data: { text: q, caption: '', alignment: 'left' } }; }
      else if (t === 'pre') { var code = c.textContent.replace(/\r\n?/g, '\n').replace(/\n$/, ''); if (code.trim()) b = { type: 'code', data: { code: code } }; }
      else if (t === 'table') b = tableBlock(c, st);
      else if (t === 'hr') b = { type: 'delimiter', data: {} };
      else if (t === 'img') { var l = imgLink(c); if (l) b = { type: 'paragraph', data: { text: l } }; }
      else if (t === 'li') { var lt = flatInline(c, st); if (!emptyInline(lt)) b = { type: 'list', data: { style: 'unordered', meta: {}, items: [{ content: lt, meta: {}, items: [] }] } }; }
      else if (hasBlock(c)) { collect(c, out, st); continue; }             // div/section/... bzw. <b> um alles (Google Docs)
      else { var pt = trimInline(inline(c, st)); if (!emptyInline(pt)) b = { type: 'paragraph', data: { text: pt } }; }
      if (b) out.push(b);
    }
    flushList(); flush();
    return out;
  }
  function foreignBlocks(dom) { return collect(dom, [], { n: 0 }); }
  function ownHtml(dom) { return !!(dom.querySelector && dom.querySelector('[data-block-type], [data-pm-part], [data-pm-slice], [data-saga-block]')); }

  if (!root.HKPmEditor || typeof HKPmEditor.use !== 'function') { root.HKPmClip = { foreignBlocks: foreignBlocks }; return; }
  HKPmEditor.use(function (X) {
    var P = X.P, S0 = P.pm.state, M0 = P.pm.model, Kk = X.K;
    function ed() { return X.ed(); }
    function ro() { return X.ro(); }
    function M() { return (Kk && Kk.media) || {}; }

    // --- Kopieren ------------------------------------------------------------------------------------------------
    // Block-JSON der obersten Knoten eines Slices (abgeschnittene Knoten auffuellen; synchronisierte Originale: Inhalt)
    function sliceBlocks(slice) {
      var out = [], bad = false;
      slice.content.forEach(function (n) {
        if (!X.isBlock(n)) { bad = true; return; }
        var list = n.type.name === 'synced' ? (function () { var a = []; n.forEach(function (c) { a.push(c); }); return a; })() : [n];
        list.forEach(function (x) {
          var j = null;
          try { j = X.blockJson(x); } catch (e) { j = null; }
          if (!j) { try { var f = x.type.createAndFill(x.attrs, x.content, x.marks); j = f ? X.blockJson(f) : null; } catch (e2) { j = null; } }
          if (!j) { bad = true; return; }
          var o = { type: j.type, data: j.data };
          if (j.tunes && j.tunes.hkstyle) o.tunes = { hkstyle: j.tunes.hkstyle };
          out.push(o);
        });
      });
      return bad || !out.length ? null : out;
    }
    function setBlocks(dt, list) {
      dt.setData(EJS_MIME, JSON.stringify(list.map(function (b) { var o = { tool: b.type, data: b.data }; if (b.tunes) o.tunes = b.tunes; return o; })));
      dt.setData(SAGA_MIME, JSON.stringify({ v: 1, blocks: list }));
    }
    function onCopy(view, e, cut) {
      var st = view.state, sel = st.selection, dt = e.clipboardData;
      if (!dt || sel.empty || X.selIds().length) return false;             // Blockauswahl: pm-struktur.js copySel
      if (sel instanceof S0.NodeSelection && sel.node.type.name === 'image') return false;   // pm-tools-medien.js copyImage
      if (!(sel instanceof S0.NodeSelection) && sel.$from.sameParent(sel.$to) && sel.$from.parent.isTextblock) return false;   // innerhalb eines Textblocks: Standard
      if (!view.serializeForClipboard) return false;
      var slice = sel.content(), list = sliceBlocks(slice);
      if (!list) return false;
      var r = view.serializeForClipboard(slice);
      e.preventDefault();
      dt.clearData();
      dt.setData('text/html', r.dom.innerHTML);
      dt.setData('text/plain', r.text);
      setBlocks(dt, list);
      if (cut && !ro()) view.dispatch(view.state.tr.deleteSelection().scrollIntoView().setMeta('uiEvent', 'cut'));
      return true;
    }
    // HTML fuer die Zwischenablage: wie das Schema, Bild zusaetzlich mit <img> (andere Programme sehen das Bild)
    var SER = null;
    function serializer() {
      if (SER && SER.schema === X.schema()) return SER.ser;
      var base = M0.DOMSerializer.fromSchema(X.schema()), nodes = Object.assign({}, base.nodes);
      var img = nodes.image;
      if (img) nodes.image = function (node) {
        var spec = img(node), f = node.attrs.file, u = f && typeof f.url === 'string' ? f.url : '';
        if (!Array.isArray(spec) || !u || !/^(\/(?!\/)|https?:\/\/)/i.test(u)) return spec;
        var abs = /^\//.test(u) ? location.origin + u : u;
        return [spec[0], spec[1] && typeof spec[1] === 'object' && !Array.isArray(spec[1]) ? spec[1] : {}, ['img', { src: abs, alt: node.textContent || '' }], ['div', { 'class': 'pm-clip-cap' }, 0]];
      };
      SER = { schema: X.schema(), ser: new M0.DOMSerializer(nodes, base.marks) };
      return SER.ser;
    }

    // --- Einfuegen -------------------------------------------------------------------------------------------------
    // Fremdes HTML: eigene Bloecke statt der Schema-Regeln (die kennen nur Saga-HTML)
    var BASEPARSER = null;
    function baseParser() { if (!BASEPARSER || BASEPARSER.schema !== X.schema()) BASEPARSER = M0.DOMParser.fromSchema(X.schema()); return BASEPARSER; }
    var TEXT_PARENTS = { paragraph: 1, header: 1, callout: 1 };
    var clipboardParser = {
      parseSlice: function (dom, opts) {
        var bp = baseParser();
        try {
          if (ownHtml(dom) || !Kk || !Kk.cleanClip) return bp.parseSlice(dom, opts);
          var raw = foreignBlocks(dom); if (!raw.length) return bp.parseSlice(dom, opts);
          var $c = opts && opts.context, par = $c ? $c.parent : null;
          var blockMode = !par || !!TEXT_PARENTS[par.type.name];
          if (blockMode && $c && $c.depth > 1) {
            var up = $c.node($c.depth - 1).type.name;
            if (up !== 'toggle' && up !== 'column' && up !== 'synced') blockMode = false;
          }
          var nested = !!($c && $c.depth > 1);
          var list = Kk.cleanClip(raw.map(function (b) { return { tool: b.type, data: b.data }; }), nested);
          if (!list.length) return M0.Slice.empty;
          if (!blockMode) {                                           // nur Inline (Listenpunkt, Zelle, Titel ...)
            var txt = list.map(function (b) { return blockInline({ type: b.tool, data: b.data }); }).filter(function (x) { return x && !emptyInline(x); }).join('<br>');
            var pn = X.mk([{ type: 'paragraph', data: { text: txt } }], true)[0];
            return new M0.Slice(pn.content, 0, 0);
          }
          var nodes = X.mk(list.map(function (b) { return { type: b.tool, data: b.data }; }), true);
          if (!nodes.length) return M0.Slice.empty;
          var os = nodes[0].type.name === 'paragraph' ? 1 : 0, oe = nodes[nodes.length - 1].type.name === 'paragraph' ? 1 : 0;
          return new M0.Slice(M0.Fragment.fromArray(nodes), os, oe);
        } catch (e) {
          if (root.console) console.error('pm-clip', e);
          return bp.parseSlice(dom, opts);
        }
      }
    };
    // HTML aus diesem Editor mit fremden Dateiverweisen ohne Block-JSON: Verweise umschreiben, dann noch einmal einfuegen
    var AGAIN = null;
    function pasteRefs(view, e) {
      var cd = e.clipboardData; if (!cd || ro() || cd === AGAIN) return false;
      if (cd.getData(EJS_MIME) || cd.getData(SAGA_MIME)) return false;   // Bloecke: pm-tools-medien.js pasteSaga
      var html = cd.getData('text/html'); if (!html || html.indexOf('data-block-type') < 0) return false;
      FILE_REF.lastIndex = 0; if (!FILE_REF.test(html)) return false;
      e.preventDefault();
      var text = cd.getData('text/plain'), cur = Kk.cur && Kk.cur(), cid = cur && cur.id, refs = {}, edAt = ed();
      FILE_REF.lastIndex = 0; (html.match(FILE_REF) || []).forEach(function (r) { refs[r] = r; });
      Promise.all(Object.keys(refs).map(function (r) {
        return HK.api('POST', '/api/files/copy', { url: r, note: cid != null ? cid : null }).then(function (d) { refs[r] = d.url; });
      })).then(function () {
        var c2 = Kk.cur && Kk.cur(); if (!c2 || c2.id !== cid || ro() || ed() !== edAt) return;
        var h2 = html.replace(FILE_REF, function (r) { return refs[r] || r; });
        var v = ed().view, dt = new DataTransfer();
        dt.setData('text/html', h2); if (text) dt.setData('text/plain', text);
        AGAIN = dt;
        var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
        try {
          if (ev.clipboardData === dt) v.dom.dispatchEvent(ev);
          else if (v.pasteHTML) v.pasteHTML(h2);                       // Browser ohne clipboardData im Ereignis
        } finally { AGAIN = null; }
      }).catch(function (err) { toast(T('Einfügen fehlgeschlagen: {err}', { err: err.message })); });
      return true;
    }
    function plugin() {
      return new S0.Plugin({
        key: new S0.PluginKey('hkClip'),
        props: {
          clipboardSerializer: { serializeFragment: function (f, o, t) { return serializer().serializeFragment(f, o, t); },
                                 serializeNode: function (n, o) { return serializer().serializeNode(n, o); } },
          clipboardParser: clipboardParser,
          handleDOMEvents: {
            copy: function (v, e) { return onCopy(v, e, false); },
            cut: function (v, e) { return !ro() && onCopy(v, e, true); }
          },
          handlePaste: function (v, e) { return pasteRefs(v, e); }
        }
      });
    }
    root.HKPmClip = { foreignBlocks: foreignBlocks, sliceBlocks: sliceBlocks };
    return { plugins: function () { return [plugin()]; }, early: true };
  });
})(window);
