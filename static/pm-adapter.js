/* Block-JSON <-> ProseMirror-JSON nach Saga-Schema v1 (2.5, Tiptap-Umbau T1) - reine Funktionen, Browser und Node.
 *
 * Gleiche Regeln wie saga/collab_model.py (block_to_pm/pm_to_block, html_to_inline/inline_to_html); gesichert ueber
 * tests/vectors/pm_schema.json (tools/pm_vectors.mjs hier, tests/pm_vectors.py gegen den Server).
 *
 *   HKPmAdapter.docFromBlocks(save, ctx)       {blocks:[...]} -> PM-JSON {type:'doc', content:[...]}
 *   HKPmAdapter.blocksFromDoc(doc, ctx)        PM-Knoten (Editor) oder PM-JSON -> {time, version, blocks}
 *   HKPmAdapter.bind(pmDoc, save, ctx)         Original-Bloecke an die echten PM-Knoten haengen (nach Node.fromJSON)
 *   HKPmAdapter.htmlToInline(html) / inlineToHtml(nodes, resolve, bid)
 *   HKPmAdapter.marksToAttrs(pm) / attrsToMarks(pm)   Marken um Inline-Knoten: node.marks <-> attrs.marks (y-tiptap)
 *
 * ctx (alles optional):
 *   orig     WeakMap(Knoten -> Original-Block): unveraenderte Knoten kommen byte-gleich zurueck (Entscheidung T-E4).
 *            docFromBlocks fuellt sie fuer die erzeugten JSON-Knoten, bind() fuer echte Editor-Knoten.
 *   resolve  {page(bid, id, i) -> HTML, mention(bid, u, i) -> HTML}: Titel/Namen, die nicht im Modell stehen
 *   upgrade  true: Altformat Toggle {text} beim Lesen in Titel + ersten Absatz wandeln (wie ToggleTool)
 *   time, version   fuer das Ergebnis von blocksFromDoc
 * Fehler (unbekannter Knoten/Attribut/Marke, falsche Verschachtelung): wirft PmError (wie ModelError im Server).
 */
(function (root, factory) {
  var S = typeof module === 'object' && module.exports ? require('./pm-schema.js') : root.HKPmSchema;
  var api = factory(S);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HKPmAdapter = api;
})(typeof self !== 'undefined' ? self : this, function (S) {
  'use strict';
  if (!S) throw new Error('pm-adapter.js braucht pm-schema.js (HKPmSchema)');

  var MAX_DEPTH = 24;
  var MARK_RANK = S.MARKS, BLOCKS = S.BLOCKS, OPAQUE = S.OPAQUE, TEXTBLOCK = S.TEXTBLOCK, ATTRS = S.ATTRS;
  var INLINE_NODES = S.INLINE_NODES, PALETTE = S.PALETTE, BLOCK_COMMON = S.BLOCK_COMMON;
  var FIELDS = {
    paragraph: ['text'], header: ['text', 'level'], callout: ['emoji', 'text'], quote: ['text', 'caption', 'alignment'],
    code: ['code', 'language', 'view', 'render'], delimiter: [], toggle: ['title', 'open', 'level', 'blocks'],
    columns: ['cols'], synced: ['sid', 'blocks'], synced_ref: ['sid'], list: ['style', 'meta', 'items'],
    table: ['withHeadings', 'headCol', 'colWidths', 'colors', 'stretched', 'content'],
    image: ['file', 'caption', 'withBorder', 'withBackground', 'stretched', 'width', 'align'], math: ['tex', 'render']
  };
  var NOTE_HREF = /^\/notes#([1-9][0-9]{0,17})$/;

  function PmError(msg) { this.name = 'PmError'; this.message = msg; }
  PmError.prototype = Object.create(Error.prototype);
  PmError.prototype.constructor = PmError;

  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function isDict(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function isList(v) { return Array.isArray(v); }
  function inArr(a, v) { return a.indexOf(v) >= 0; }
  function assign(t) {
    for (var i = 1; i < arguments.length; i++) { var s = arguments[i]; for (var k in s) if (has(s, k)) t[k] = s[k]; }
    return t;
  }
  function stable(v) {                                // JSON mit sortierten Schluesseln (json.dumps(sort_keys=True))
    if (isList(v)) return '[' + v.map(stable).join(',') + ']';
    if (isDict(v)) return '{' + Object.keys(v).sort().map(function (k) { return JSON.stringify(k) + ':' + stable(v[k]); }).join(',') + '}';
    return JSON.stringify(v === undefined ? null : v);
  }
  function pyStr(v) {                                 // str() von Python fuer f"{bid}/{path}"
    if (v === undefined || v === null) return 'None';
    if (v === true) return 'True';
    if (v === false) return 'False';
    return String(v);
  }
  function truthyStr(v) {                             // str(x or "")
    return (v === undefined || v === null || v === '' || v === 0 || v === false) ? '' : String(v);
  }

  // =================================================================================================================
  // Inline-HTML -> PM-Inhalt (wie collab_model._Inline)
  // =================================================================================================================
  var ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', shy: '­', copy: '©',
    reg: '®', hellip: '…', mdash: '—', ndash: '–', laquo: '«', raquo: '»', euro: '€',
    bdquo: '„', ldquo: '“', rdquo: '”', lsquo: '‘', rsquo: '’', sbquo: '‚', zwj: '‍',
    zwnj: '‌', thinsp: ' ', ensp: ' ', emsp: ' ', times: '×', middot: '·', deg: '°',
    auml: 'ä', ouml: 'ö', uuml: 'ü', Auml: 'Ä', Ouml: 'Ö', Uuml: 'Ü', szlig: 'ß' };
  function unescape(s) {
    if (s.indexOf('&') < 0) return s;
    return s.replace(/&(#[0-9]{1,7};?|#[xX][0-9a-fA-F]{1,6};?|[A-Za-z][A-Za-z0-9]{1,31};)/g, function (m, e) {
      if (e.charAt(0) === '#') {
        var n = e.charAt(1) === 'x' || e.charAt(1) === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
        if (n === 0 || n > 0x10ffff || (n >= 0xd800 && n < 0xe000)) return '�';
        return String.fromCodePoint ? String.fromCodePoint(n) : String.fromCharCode(n);
      }
      var k = e.slice(0, -1);
      return has(ENT, k) ? ENT[k] : m;
    });
  }

  // kleiner Tokenizer (Node; im Browser auf Wunsch): ruft h.start(tag, attrs{}), h.end(tag), h.data(text)
  var RX_END = /^<\/([a-zA-Z][^\s\/>]*)[^>]*>/;
  var RX_START = /^<([a-zA-Z][^\s\/>]*)((?:\s+[^\s\/>"'=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>/;
  var RX_ATTR = /([^\s\/>"'=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  function tokenize(s, h) {
    var i = 0, buf = '';
    function flush() { if (buf) { h.data(unescape(buf)); buf = ''; } }
    while (i < s.length) {
      var lt = s.indexOf('<', i);
      if (lt < 0) { buf += s.slice(i); break; }
      buf += s.slice(i, lt);
      var rest = s.slice(lt), m;
      if (rest.slice(0, 4) === '<!--') {
        flush();
        var e = s.indexOf('-->', lt + 4);
        i = e < 0 ? s.length : e + 3;
        continue;
      }
      if ((m = RX_END.exec(rest))) { flush(); h.end(m[1].toLowerCase()); i = lt + m[0].length; continue; }
      if ((m = RX_START.exec(rest))) {
        flush();
        var attrs = {}, a;
        RX_ATTR.lastIndex = 0;
        while ((a = RX_ATTR.exec(m[2]))) {
          var k = a[1].toLowerCase();
          if (!has(attrs, k)) attrs[k] = unescape(a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : '');
        }
        var tag = m[1].toLowerCase();
        h.start(tag, attrs);
        if (m[3]) h.end(tag);
        i = lt + m[0].length;
        continue;
      }
      buf += '<';
      i = lt + 1;
    }
    flush();
  }
  var VOID = { br: 1, wbr: 1, img: 1, hr: 1, input: 1, meta: 1, link: 1, area: 1, base: 1, col: 1, embed: 1, source: 1, track: 1 };
  function domWalk(html, h) {                        // Browser: <template> statt Tokenizer, gleiche Ereignisse
    var t = document.createElement('template');
    t.innerHTML = html;
    (function walk(node) {
      for (var c = node.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) { if (c.data) h.data(c.data); }
        else if (c.nodeType === 1) {
          var tag = c.localName, at = {};
          for (var j = 0; j < c.attributes.length; j++) at[c.attributes[j].name] = c.attributes[j].value;
          h.start(tag, at);
          if (!VOID[tag]) { walk(c.content || c); h.end(tag); }
        }
      }
    })(t.content);
  }
  var config = { tokenizer: typeof document === 'undefined' || typeof document.createElement !== 'function' };

  function mkList(m) {
    var out = [];
    MARK_RANK.forEach(function (k) {
      if (has(m, k)) { var x = { type: k }; if (Object.keys(m[k]).length) x.attrs = m[k]; out.push(x); }
    });
    return out;
  }
  function mkDict(lst) {
    var m = {};
    (lst || []).forEach(function (x) { m[x.type] = x.attrs || {}; });
    return m;
  }
  function textNode(s, m) {
    var n = { type: 'text', text: s };
    if (Object.keys(m).length) n.marks = mkList(m);
    return n;
  }

  function htmlToInline(html) {
    var out = [], stack = [], cap = null;          // cap: [art, attrs, texte, tiefe]
    function marks() {
      var m = {};
      stack.forEach(function (x) { if (x[1]) m[x[1][0]] = x[1][1]; });
      if (has(m, 'bg')) delete m.mark;
      return m;
    }
    function node(kind, attrs) {
      var n = { type: kind, attrs: attrs }, m = marks();
      if (Object.keys(m).length) n.marks = mkList(m);
      out.push(n);
    }
    function pal(cls, pre) {
      for (var i = 0; i < cls.length; i++) if (cls[i].indexOf(pre) === 0 && inArr(PALETTE, cls[i].slice(pre.length))) return cls[i].slice(pre.length);
      return null;
    }
    var h = {
      start: function (tag, a) {
        if (cap) { if (tag !== 'br') cap[3]++; return; }
        var cls = (a['class'] || '').split(/[ \t\n\r\f]+/).filter(Boolean);
        if (tag === 'br') { node('hardBreak', {}); return; }
        if (tag === 'a') {
          var href = a.href || '', mm = NOTE_HREF.exec(href);
          if (mm) { cap = ['page', { id: +mm[1] }, [], 0]; return; }
          stack.push(['a', ['link', { href: href }]]);
          return;
        }
        if (tag === 'span' && inArr(cls, 'hk-mention')) { cap = ['mention', { u: a['data-u'] || '' }, [], 0]; return; }
        if (tag === 'code' && inArr(cls, 'hk-math')) { cap = ['math_inline', {}, [], 0]; return; }
        if (tag === 'time') { cap = ['date', { dt: a.datetime || '', remind: inArr(cls, 'hk-remind') }, [], 0]; return; }
        var mk = null, c;
        if (tag === 'b' || tag === 'strong') mk = ['b', {}];
        else if (tag === 'i' || tag === 'em') mk = ['i', {}];
        else if (tag === 'u' || tag === 's') mk = [tag, {}];
        else if (tag === 'code') mk = ['icode', {}];
        else if (tag === 'mark') { c = pal(cls, 'hk-bg-'); mk = c ? ['bg', { c: c }] : ['mark', {}]; }
        else if (tag === 'span') { c = pal(cls, 'hk-c-'); mk = c ? ['color', { c: c }] : null; }
        stack.push([tag, mk]);
      },
      end: function (tag) {
        if (cap) {
          if (cap[3] > 0) { cap[3]--; return; }
          var kind = cap[0], attrs = cap[1], txt = cap[2].join('');
          cap = null;
          if (kind === 'math_inline') attrs.tex = txt;
          else if (kind === 'date') attrs.label = txt;
          node(kind, attrs);
          return;
        }
        for (var i = stack.length - 1; i >= 0; i--) if (stack[i][0] === tag) { stack.length = i; break; }
      },
      data: function (d) {
        if (cap) { cap[2].push(d); return; }
        if (!d) return;
        var m = marks(), last = out[out.length - 1];
        if (last && last.type === 'text' && stable(mkDict(last.marks)) === stable(m)) last.text += d;
        else out.push(textNode(d, m));
      }
    };
    if (config.tokenizer) tokenize(html || '', h); else domWalk(html || '', h);
    if (cap) h.end('');
    return out;
  }

  // =================================================================================================================
  // PM-Inhalt -> Inline-HTML in Normalform (wie collab_model.inline_to_html)
  // =================================================================================================================
  function escText(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function escAttr(s) { return escText(s).replace(/"/g, '&quot;').replace(/'/g, '&#x27;'); }
  function openTag(name, a) {
    if (name === 'link') return ['<a href="' + escAttr(truthyStr(a.href)) + '" target="_blank" rel="noopener noreferrer">', '</a>'];
    if (name === 'color') return ['<span class="hk-c-' + escAttr(truthyStr(a.c)) + '">', '</span>'];
    if (name === 'bg') return ['<mark class="hk-bg-' + escAttr(truthyStr(a.c)) + '">', '</mark>'];
    if (name === 'mark') return ['<mark class="cdx-marker">', '</mark>'];
    if (name === 'icode') return ['<code class="inline-code">', '</code>'];
    return ['<' + name + '>', '</' + name + '>'];
  }
  // nodes: [{type, text?, attrs?, marks?}] (JSON-Form, ohne null-Attribute)
  function inlineToHtml(nodes, resolve, bid) {
    var out = [], open = [], seen = {};
    nodes.forEach(function (n) {
      var m = mkDict(n.marks), want = [];
      MARK_RANK.forEach(function (k) { if (has(m, k)) want.push([k, stable(m[k])]); });
      var k = 0;
      while (k < open.length && k < want.length && open[k][0] === want[k][0] && open[k][1] === want[k][1]) k++;
      while (open.length > k) out.push(openTag(open.pop()[0], {})[1]);
      for (var j = k; j < want.length; j++) { out.push(openTag(want[j][0], JSON.parse(want[j][1]))[0]); open.push(want[j]); }
      var t = n.type, a = n.attrs || {}, key, i;
      if (t === 'text') out.push(escText(n.text || ''));
      else if (t === 'hardBreak') out.push('<br>');
      else if (t === 'math_inline') out.push('<code class="hk-math">' + escText(truthyStr(a.tex)) + '</code>');
      else if (t === 'date') {
        out.push('<time datetime="' + escAttr(truthyStr(a.dt)) + '" class="' + (a.remind ? 'hk-remind' : 'hk-date') + '">' +
          escText(truthyStr(a.label)) + '</time>');
      } else if (t === 'mention') {
        var u = truthyStr(a.u);
        key = 'm\u0000' + u; i = seen[key] || 0; seen[key] = i + 1;
        out.push('<span class="hk-mention" data-u="' + escAttr(u) + '">' + (resolve ? resolve.mention(bid, u, i) : '') + '</span>');
      } else if (t === 'page') {
        var pid = a.id;
        key = 'p\u0000' + stable(pid); i = seen[key] || 0; seen[key] = i + 1;
        var txt = resolve ? resolve.page(bid, pid, i) : '';
        out.push('<a href="/notes#' + (typeof pid === 'number' ? Math.trunc(pid) : 0) + '">' + txt + '</a>');
      } else throw new PmError('inline ' + t);
    });
    while (open.length) out.push(openTag(open.pop()[0], {})[1]);
    return out.join('');
  }

  // =================================================================================================================
  // Block-JSON -> PM-JSON (wie collab_model.block_to_pm)
  // =================================================================================================================
  function lid(bid, path) { return 'l' + S.sha256(pyStr(bid) + '/' + path).slice(0, 9); }

  function common(b, used) {
    var a = {};
    if (typeof b.id === 'string') a.id = b.id;
    var tn = isDict(b.tunes) ? b.tunes : {};
    if (tn.hkstyle !== undefined && tn.hkstyle !== null) a.style = tn.hkstyle;
    if (tn.hkconflict !== undefined && tn.hkconflict !== null) a.conflict = tn.hkconflict;
    var d = isDict(b.data) ? b.data : {}, extra = {}, tx = {}, ne = 0, nt = 0, k;
    for (k in d) if (has(d, k) && !inArr(used, k)) { extra[k] = d[k]; ne++; }
    for (k in tn) if (has(tn, k) && k !== 'hkstyle' && k !== 'hkconflict') { tx[k] = tn[k]; nt++; }
    if (ne || nt) { a.extra = {}; if (ne) a.extra.data = extra; if (nt) a.extra.tunes = tx; }
    return a;
  }
  function txt(v) { return typeof v === 'string' ? htmlToInline(v) : []; }
  function plain(v) { return typeof v === 'string' && v ? [{ type: 'text', text: v }] : []; }
  function itemsOk(items) {
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (typeof it === 'string') continue;
      if (!isDict(it) || (has(it, 'content') && typeof it.content !== 'string') || (has(it, 'items') && !isList(it.items))) return false;
      if (!itemsOk(it.items || [])) return false;
    }
    return true;
  }
  function itemsPm(items, bid, path) {
    return items.map(function (it, i) {
      var p = path + '.' + i;
      if (typeof it === 'string') it = { content: it, meta: {}, items: [] };
      var a = { id: lid(bid, p) }, ex = {}, ne = 0;
      if (has(it, 'meta')) a.meta = it.meta;
      for (var k in it) if (has(it, k) && k !== 'content' && k !== 'meta' && k !== 'items') { ex[k] = it[k]; ne++; }
      if (!has(it, 'items')) { ex._noitems = true; ne++; }
      if (ne) a.extra = ex;
      return { type: 'litem', attrs: a, content: [{ type: 'ltext', content: txt(it.content) }].concat(itemsPm(it.items || [], bid, p)) };
    });
  }
  function upgradeToggle(b) {                         // Altformat Toggle {text} -> Titel leer + erster Absatz (ToggleTool)
    var d = b.data, nd = {};
    for (var k in d) if (has(d, k) && k !== 'text') nd[k] = d[k];
    if (!has(nd, 'title')) nd.title = '';
    nd.blocks = typeof d.text === 'string' && d.text ? [{ type: 'paragraph', data: { text: d.text } }] : [];
    return assign({}, b, { data: nd });
  }

  function blockToPm(b, ctx) {
    var typ = b.type, d = isDict(b.data) ? b.data : {}, n;
    if (ctx && ctx.upgrade && typ === 'toggle' && has(d, 'text') && !isList(d.blocks)) return blockToPm(upgradeToggle(b), assign({}, ctx, { orig: null }));
    if (typ === 'synced' && !isList(d.blocks)) typ = 'synced_ref';
    if (!inArr(BLOCKS, typ) || ((typ === 'unknown' || typ === 'synced_ref') && b.type !== 'synced')) {
      n = { type: 'unknown', attrs: common(b, Object.keys(d)) };
      n.attrs.type = b.type === undefined ? null : b.type;
      n.attrs.data = d;
      delete n.attrs.extra;
      var tn = isDict(b.tunes) ? b.tunes : {}, tx = {}, nt = 0;
      for (var k0 in tn) if (has(tn, k0) && k0 !== 'hkstyle' && k0 !== 'hkconflict') { tx[k0] = tn[k0]; nt++; }
      if (nt) n.attrs.extra = { tunes: tx };
      return remember(ctx, n, b);
    }
    var fields = FIELDS[typ] || [], bad = [];
    ['text', 'caption', 'title', 'code'].forEach(function (k) { if (inArr(fields, k) && has(d, k) && typeof d[k] !== 'string') bad.push(k); });
    fields.forEach(function (k) { if (has(d, k) && d[k] === null) bad.push(k); });
    if ((typ === 'toggle' || typ === 'synced') && has(d, 'blocks') && !isList(d.blocks)) bad.push('blocks');
    if (typ === 'columns' && !(isList(d.cols) && d.cols.every(function (c) { return isDict(c) && (!has(c, 'blocks') || isList(c.blocks)); }))) bad.push('cols');
    if (typ === 'list' && !((!has(d, 'items') || isList(d.items)) && itemsOk(has(d, 'items') ? d.items : []))) bad.push('items');
    if (typ === 'table' && !((!has(d, 'content') || isList(d.content)) && (has(d, 'content') ? d.content : []).every(function (r) {
      return isList(r) && r.every(function (x) { return typeof x === 'string'; });
    }))) bad.push('content');
    if (inArr(OPAQUE, typ)) {
      n = { type: typ, attrs: common(b, Object.keys(d)) };
      n.attrs.data = d;
      return remember(ctx, n, b);
    }
    var used = fields.filter(function (k) { return !inArr(bad, k); });
    var a = common(b, used);
    (ATTRS[typ] || []).forEach(function (k) { if (has(d, k) && inArr(used, k)) a[k] = d[k]; });
    if (typ === 'list' && inArr(used, 'style') && has(d, 'style')) a.listStyle = d.style;
    n = { type: typ, attrs: a };
    var bid = b.id, U = function (k) { return inArr(used, k); };
    function kids(lst) { return (lst || []).filter(isDict).map(function (x) { return blockToPm(x, ctx); }); }
    if (inArr(TEXTBLOCK, typ)) n.content = U('text') ? txt(d.text) : [];
    else if (typ === 'quote') {
      n.content = [{ type: 'qtext', content: U('text') ? txt(d.text) : [] }, { type: 'qcap', content: U('caption') ? txt(d.caption) : [] }];
    } else if (typ === 'code') n.content = U('code') ? plain(d.code) : [];
    else if (typ === 'image') n.content = [{ type: 'icap', content: U('caption') ? txt(d.caption) : [] }];
    else if (typ === 'toggle') {
      n.content = [{ type: 'ttitle', content: U('title') ? txt(d.title) : [] }];
      if (U('blocks')) {
        n.content = n.content.concat(kids(d.blocks));
        if (!has(d, 'blocks')) { a.extra = a.extra || {}; (a.extra.absent = a.extra.absent || []).push('blocks'); }
      }
    } else if (typ === 'synced') n.content = kids(d.blocks);
    else if (typ === 'columns') {
      n.content = (U('cols') ? d.cols || [] : []).map(function (c) {
        var ca = {}, ex = {}, ne = 0;
        if (has(c, 'w')) ca.w = c.w;
        for (var k in c) if (has(c, k) && k !== 'w' && k !== 'blocks') { ex[k] = c[k]; ne++; }
        if (ne || !has(c, 'blocks')) { ca.extra = ex; if (!has(c, 'blocks')) ca.extra._noblocks = true; }
        return { type: 'column', attrs: ca, content: kids(c.blocks) };
      });
    } else if (typ === 'list') n.content = U('items') ? itemsPm(d.items || [], bid, '') : [];
    else if (typ === 'table') {
      n.content = (U('content') ? d.content || [] : []).map(function (r) {
        return { type: 'trow', content: r.map(function (x) { return { type: 'tcell', content: txt(x) }; }) };
      });
    }
    var absent = ['text', 'caption', 'title', 'code', 'items', 'content', 'cols'].filter(function (k) { return U(k) && !has(d, k); });
    if (absent.length) {
      a.extra = a.extra || {};
      var set = (a.extra.absent || []).slice();
      absent.forEach(function (k) { if (!inArr(set, k)) set.push(k); });
      a.extra.absent = set.sort();
    }
    return remember(ctx, n, b);
  }
  function remember(ctx, n, b) { if (ctx && ctx.orig) ctx.orig.set(n, b); return n; }

  function docFromBlocks(save, ctx) {
    var doc = typeof save === 'string' ? JSON.parse(save) : save;
    var blocks = isDict(doc) && isList(doc.blocks) ? doc.blocks : [];
    return { type: 'doc', content: blocks.filter(isDict).map(function (b) { return blockToPm(b, ctx); }) };
  }

  // =================================================================================================================
  // PM (echte Knoten oder JSON) -> Block-JSON (wie collab_model.pm_to_block)
  // =================================================================================================================
  function isReal(n) { return n && n.type && typeof n.type === 'object' && typeof n.type.name === 'string'; }
  function tname(n) { return isReal(n) ? n.type.name : n && n.type; }
  function attrsOf(n) {                               // ohne null (null = nicht gesetzt, wie in Yjs)
    var src = isReal(n) ? n.attrs : (n.attrs || {}), o = {};
    for (var k in src) if (has(src, k) && src[k] !== null && src[k] !== undefined) o[k] = src[k];
    return o;
  }
  function kidsOf(n) {
    if (isReal(n)) { var a = []; n.content.forEach(function (c) { a.push(c); }); return a; }
    var c = n.content || [];
    if (!isList(c)) throw new PmError('content');
    return c;
  }
  function marksOf(n) {
    var src = isReal(n) ? n.marks.map(function (m) { return { type: m.type.name, attrs: m.attrs }; }) : (n.marks || []);
    return src.map(function (m) {
      var at = {}, s = m.attrs || {};
      for (var k in s) if (has(s, k) && s[k] !== null && s[k] !== undefined) at[k] = s[k];
      var x = { type: m.type };
      if (Object.keys(at).length) x.attrs = at;
      return x;
    });
  }
  function checkAttrs(n, allowed) {
    var a = attrsOf(n);
    for (var k in a) if (!inArr(allowed, k)) throw new PmError(tname(n) + ': Attribut ' + k);
    return a;
  }
  function kidsTyped(n, types) {
    var c = kidsOf(n);
    c.forEach(function (x) { var t = tname(x); if (types && !inArr(types, t)) throw new PmError(tname(n) + ': ' + t); });
    return c;
  }
  // Inline-Inhalt -> JSON-Form fuer inlineToHtml (Marken um Inline-Knoten: node.marks, sonst attrs.marks)
  function inlineJson(n) {
    return kidsOf(n).map(function (x) {
      var t = tname(x);
      if (t !== 'text' && !inArr(INLINE_NODES, t)) throw new PmError(tname(n) + ': ' + t);
      var mk = marksOf(x);
      mk.forEach(function (m) { if (!inArr(MARK_RANK, m.type)) throw new PmError('Marke ' + m.type); });
      if (t === 'text') { var o = { type: 'text', text: x.text }; if (mk.length) o.marks = mk; return o; }
      var a = checkAttrs(x, ATTRS[t]);
      if (!mk.length && isList(a.marks)) mk = a.marks;
      mk.forEach(function (m) { if (!inArr(MARK_RANK, m.type)) throw new PmError('Marke ' + m.type); });
      var at = assign({}, a); delete at.marks;
      var r = { type: t, attrs: at };
      if (mk.length) r.marks = mk;
      return r;
    });
  }
  function inlineHtml(n, ctx, bid) { return inlineToHtml(inlineJson(n), ctx && ctx.resolve, bid); }

  function ordered(typ, d) {
    var order = FIELDS[typ] || [], o = {};
    order.forEach(function (k) { if (has(d, k)) o[k] = d[k]; });
    for (var k in d) if (has(d, k) && !inArr(order, k)) o[k] = d[k];
    return o;
  }
  function emptyish(v) { return v === undefined || v === null || v === '' || (isList(v) && !v.length); }

  function pmToBlock(n, ctx, depth, allow) {
    if (ctx && ctx.orig && n && typeof n === 'object') { var o0 = ctx.orig.get(n); if (o0) return o0; }
    if (depth > MAX_DEPTH || !n || typeof n !== 'object') throw new PmError('zu tief');
    var typ = tname(n);
    if (!inArr(BLOCKS, typ) || (allow && !inArr(allow, typ))) throw new PmError('Block ' + typ);
    var a = checkAttrs(n, BLOCK_COMMON.concat(ATTRS[typ])), bid = a.id;
    var ex = isDict(a.extra) ? a.extra : {}, absent = isList(ex.absent) ? ex.absent : [];
    var out = {}, d = {}, c;
    if (bid !== undefined) out.id = bid;
    function blocksOf(nodes, dp, al) { return nodes.map(function (x) { return pmToBlock(x, ctx, dp + 1, al); }); }
    function pick(keys) { keys.forEach(function (k) { if (has(a, k)) d[k] = a[k]; }); }
    if (typ === 'unknown') {
      out.type = has(a, 'type') ? a.type : null;
      d = isDict(a.data) ? a.data : {};
    } else if (inArr(OPAQUE, typ)) {
      out.type = typ;
      d = isDict(a.data) ? a.data : {};
      kidsTyped(n, []);
    } else {
      out.type = typ === 'synced_ref' ? 'synced' : typ;
      if (inArr(TEXTBLOCK, typ)) {
        if (typ === 'callout' && has(a, 'emoji')) d.emoji = a.emoji;
        d.text = inlineHtml(n, ctx, bid);
        if (typ === 'header' && has(a, 'level')) d.level = a.level;
      } else if (typ === 'quote') {
        c = kidsTyped(n, ['qtext', 'qcap']);
        if (c.length !== 2 || tname(c[0]) !== 'qtext' || tname(c[1]) !== 'qcap') throw new PmError('quote');
        d.text = inlineHtml(c[0], ctx, bid);
        d.caption = inlineHtml(c[1], ctx, bid);
        pick(['alignment']);
      } else if (typ === 'code') {
        c = kidsTyped(n, ['text']);
        if (c.some(function (x) { return marksOf(x).length; })) throw new PmError('code: Marken');
        d.code = c.map(function (x) { return x.text || ''; }).join('');
        pick(['language', 'view', 'render']);
      } else if (typ === 'image') {
        c = kidsTyped(n, ['icap']);
        if (c.length !== 1) throw new PmError('image');
        pick(['file', 'withBorder', 'withBackground', 'stretched', 'width', 'align']);
        d.caption = inlineHtml(c[0], ctx, bid);
      } else if (typ === 'math') { kidsTyped(n, []); pick(['tex', 'render']); }
      else if (typ === 'delimiter') kidsTyped(n, []);
      else if (typ === 'toggle') {
        c = kidsOf(n);
        if (!c.length || tname(c[0]) !== 'ttitle') throw new PmError('toggle');
        d.title = inlineHtml(c[0], ctx, bid);
        pick(['open', 'level']);
        d.blocks = blocksOf(c.slice(1), depth, S.NESTED_OK);
      } else if (typ === 'synced') {
        pick(['sid']);
        d.blocks = blocksOf(kidsOf(n), depth, S.SYNC_OK);
      } else if (typ === 'synced_ref') { kidsTyped(n, []); pick(['sid']); }
      else if (typ === 'columns') {
        d.cols = kidsTyped(n, ['column']).map(function (col) {
          var ca = checkAttrs(col, ATTRS.column), cx = isDict(ca.extra) ? assign({}, ca.extra) : {};
          var nob = cx._noblocks; delete cx._noblocks;
          var cd = assign({}, cx);
          if (has(ca, 'w')) cd.w = ca.w;
          if (!nob) cd.blocks = blocksOf(kidsOf(col), depth + 1, S.NESTED_OK);
          return cd;
        });
      } else if (typ === 'list') {
        if (has(a, 'listStyle')) d.style = a.listStyle;
        if (has(a, 'meta')) d.meta = a.meta;
        d.items = itemsBlock(kidsTyped(n, ['litem']), ctx, bid, depth);
      } else if (typ === 'table') {
        pick(['withHeadings', 'headCol', 'colWidths', 'colors', 'stretched']);
        d.content = kidsTyped(n, ['trow']).map(function (r) {
          return kidsTyped(r, ['tcell']).map(function (cell) { return inlineHtml(cell, ctx, bid); });
        });
      }
      absent.forEach(function (k) { if (emptyish(d[k])) delete d[k]; });
      var xd = isDict(ex.data) ? ex.data : {};
      d = ordered(typ, d);
      for (var k in xd) if (has(xd, k) && !has(d, k)) d[k] = xd[k];
    }
    out.data = d;
    var tn = {}, nt = 0;
    if (has(a, 'style')) { tn.hkstyle = a.style; nt++; }
    if (has(a, 'conflict')) { tn.hkconflict = a.conflict; nt++; }
    if (isDict(ex.tunes)) for (var t in ex.tunes) if (has(ex.tunes, t) && !has(tn, t)) { tn[t] = ex.tunes[t]; nt++; }
    if (nt) out.tunes = tn;
    return out;
  }
  function itemsBlock(items, ctx, bid, depth) {
    if (depth > MAX_DEPTH) throw new PmError('zu tief');
    return items.map(function (it) {
      var a = checkAttrs(it, ATTRS.litem), c = kidsTyped(it, ['ltext', 'litem']);
      if (!c.length || tname(c[0]) !== 'ltext' || c.slice(1).some(function (x) { return tname(x) !== 'litem'; })) throw new PmError('litem');
      var ex = isDict(a.extra) ? assign({}, a.extra) : {}, noitems = ex._noitems;
      delete ex._noitems;
      var o = { content: inlineHtml(c[0], ctx, bid) };
      if (has(a, 'meta')) o.meta = a.meta;
      if (!noitems) o.items = itemsBlock(c.slice(1), ctx, bid, depth + 1);
      for (var k in ex) if (has(ex, k) && !has(o, k)) o[k] = ex[k];
      return o;
    });
  }

  function blocksFromDoc(doc, ctx) {
    ctx = ctx || {};
    if (!doc || tname(doc) !== 'doc') throw new PmError('doc');
    var blocks = kidsOf(doc).map(function (x) { return pmToBlock(x, ctx, 0, BLOCKS); });
    return { time: ctx.time !== undefined ? ctx.time : Date.now(), version: ctx.version !== undefined ? ctx.version : 'saga-pm/' + S.VERSION, blocks: blocks };
  }

  // Original-Bloecke an echte Editor-Knoten haengen (gleiche Struktur wie docFromBlocks sie erzeugt)
  function bind(pmDoc, save, ctx) {
    if (!ctx || !ctx.orig) throw new Error('bind: ctx.orig (WeakMap) fehlt');
    var doc = typeof save === 'string' ? JSON.parse(save) : save;
    function walk(nodes, blocks) {
      blocks = (blocks || []).filter(isDict);
      if (nodes.length !== blocks.length) return;
      nodes.forEach(function (n, i) {
        var b = blocks[i], d = isDict(b.data) ? b.data : {}, t = tname(n), c = kidsOf(n);
        if (ctx.upgrade && b.type === 'toggle' && has(d, 'text') && !isList(d.blocks)) return;     // umgewandelt: neu schreiben
        ctx.orig.set(n, b);
        if (t === 'toggle' && isList(d.blocks)) walk(c.slice(1), d.blocks);
        else if (t === 'synced' && isList(d.blocks)) walk(c, d.blocks);
        else if (t === 'columns' && isList(d.cols) && c.length === d.cols.length) {
          c.forEach(function (col, j) { if (isDict(d.cols[j])) walk(kidsOf(col), d.cols[j].blocks); });
        }
      });
    }
    walk(kidsOf(pmDoc), isDict(doc) ? doc.blocks : []);
    return ctx;
  }

  // Marken um Inline-Knoten: Editor fuehrt sie als node.marks, Yjs (y-tiptap) nur als Attribut "marks"
  function mapInline(pm, fn) {
    if (isList(pm)) return pm.map(function (x) { return mapInline(x, fn); });
    if (!isDict(pm)) return pm;
    var o = assign({}, pm);
    if (isList(o.content)) o.content = o.content.map(function (x) { return mapInline(x, fn); });
    return inArr(INLINE_NODES, o.type) ? fn(o) : o;
  }
  function marksToAttrs(pm) {
    return mapInline(pm, function (o) {
      if (isList(o.marks) && o.marks.length) { o.attrs = assign({}, o.attrs, { marks: o.marks }); delete o.marks; }
      return o;
    });
  }
  function attrsToMarks(pm) {
    return mapInline(pm, function (o) {
      if (o.attrs && isList(o.attrs.marks)) {
        if (o.attrs.marks.length) o.marks = o.attrs.marks;
        o.attrs = assign({}, o.attrs); delete o.attrs.marks;
      }
      return o;
    });
  }

  return {
    docFromBlocks: docFromBlocks, blocksFromDoc: blocksFromDoc, bind: bind, blockToPm: blockToPm,
    pmToBlock: function (n, ctx) { return pmToBlock(n, ctx || {}, 0, null); },
    htmlToInline: htmlToInline, inlineToHtml: inlineToHtml, marksToAttrs: marksToAttrs, attrsToMarks: attrsToMarks,
    stable: stable, config: config, PmError: PmError, FIELDS: FIELDS
  };
});
