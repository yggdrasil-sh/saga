/* Saga-Schema v1 fuer den Web-Editor (Tiptap 3 / ProseMirror) - 2.5, Tiptap-Umbau T1.
 *
 * Massgeblich ist das Server-Modell saga/collab_model.py (docs/APP-API-COLLAB.md Abschnitt 5): Knotennamen = Saga-Typen,
 * Attribute exakt wie dort (der Server lehnt unbekannte ab), Marken link/color/bg/mark/b/i/u/s/icode, Inline-Knoten
 * hardBreak/mention/date/math_inline/page. Alle Attribute haben den Vorgabewert null: y-tiptap schreibt null nie nach
 * Yjs, so entsteht beim Hin und Her kein Attribut, das im Saga-Block nicht stand.
 *
 * Inhalts-Ausdruecke sind so weit wie das, was clean_blocks auf dem Server durchlaesst (leere Listen/Tabellen, Spalten
 * mit 1 oder mehr als 6 Spalten): ein Knoten, der nicht ins lokale Schema passt, wird von y-tiptap aus dem gemeinsamen
 * Zustand GELOESCHT (Risiko R1, tools/pm_ytest.mjs). Grenzen wie 2-6 Spalten setzt der Editor beim Bearbeiten durch.
 *
 * Ohne Build-Schritt (ES5-nah). Browser: window.HKPmSchema, Node: module.exports.
 *   HKPmSchema.extensions(T, opts) -> [Tiptap-Erweiterungen]
 *     T: {Node, Mark, Extension, mergeAttributes, UniqueID?} (Browser: window.SagaPM.core + window.SagaPM.UniqueID,
 *        Node: require('@tiptap/core') + {UniqueID: require('@tiptap/extension-unique-id').UniqueID})
 *     opts: {uniqueId: true|false (Vorgabe: true, wenn T.UniqueID da), generateID: fn, filterTransaction: fn,
 *            pageTitle: fn(id) -> Text, mentionName: fn(u) -> Text,
 *            nodeViews: {Saga-Typ: Renderer(props) -> NodeView} fuer atom-Knoten (HKTools.nodeViews(ctx), T4b)}
 *   HKPmSchema.VERSION, MODEL, BLOCKS, MARKS, INLINE_NODES, ATTRS, BLOCK_COMMON, hashInput(), hash()
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HKPmSchema = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var MODEL = 'saga-pm', VERSION = 2;
  var PALETTE = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  // Rangfolge aussen -> innen (= Reihenfolge der Marken im Schema, ProseMirror sortiert Marken danach)
  var MARKS = ['link', 'color', 'bg', 'mark', 'b', 'i', 'u', 's', 'icode'];
  var INLINE_NODES = ['hardBreak', 'mention', 'date', 'math_inline', 'page'];
  var TEXTBLOCK = ['paragraph', 'header', 'callout'];
  var OPAQUE = ['database', 'tasks', 'calendar', 'widget', 'buttons', 'attaches', 'nextcloud', 'embed'];
  var BLOCK_COMMON = ['id', 'style', 'conflict', 'extra'];
  // Attribute je Knoten ausser BLOCK_COMMON (wie collab_model.ATTRS)
  var ATTRS = {
    paragraph: [], header: ['level'], callout: ['emoji'], quote: ['alignment'], qtext: [], qcap: [],
    code: ['language', 'view', 'render'], delimiter: [], toggle: ['open', 'level'], ttitle: [],
    columns: [], column: ['w', 'extra'], synced: ['sid'], synced_ref: ['sid'], list: ['listStyle', 'meta'],
    litem: ['id', 'meta', 'extra'], ltext: [], table: ['withHeadings', 'headCol', 'colWidths', 'colors', 'stretched'],
    trow: [], tcell: [], image: ['file', 'withBorder', 'withBackground', 'stretched', 'width', 'align'], icap: [],
    math: ['tex', 'render'], unknown: ['type', 'data'],
    hardBreak: ['marks'], mention: ['u', 'marks'], date: ['dt', 'remind', 'label', 'marks'],
    math_inline: ['tex', 'marks'], page: ['id', 'marks']
  };
  OPAQUE.forEach(function (o) { ATTRS[o] = ['data']; });
  var BLOCKS = TEXTBLOCK.concat(OPAQUE, ['quote', 'code', 'delimiter', 'toggle', 'columns', 'synced', 'synced_ref',
    'list', 'table', 'image', 'math', 'unknown']);
  // Gruppen: block (oberste Ebene), blockNoCols (in toggle/column), blockNoSync (in synced)
  var NO_COLS = BLOCKS.filter(function (b) { return b !== 'columns'; });
  var NO_SYNC = NO_COLS.filter(function (b) { return b !== 'synced' && b !== 'synced_ref'; });

  function group(name) {
    var g = ['block'];
    if (NO_COLS.indexOf(name) >= 0) g.push('blockNoCols');
    if (NO_SYNC.indexOf(name) >= 0) g.push('blockNoSync');
    return g.join(' ');
  }

  // Inhalt der Blockknoten (ohne die reinen Hilfsknoten)
  var CONTENT = {
    paragraph: 'inline*', header: 'inline*', callout: 'inline*', quote: 'qtext qcap', code: 'text*',
    toggle: 'ttitle blockNoCols*', columns: 'column*', synced: 'blockNoSync*', list: 'litem*', table: 'trow*',
    image: 'icap'
  };
  // Hilfsknoten: Name -> [Inhalt, HTML-Tag]
  var PARTS = {
    qtext: ['inline*', 'div'], qcap: ['inline*', 'div'], ttitle: ['inline*', 'div'], column: ['blockNoCols*', 'div'],
    litem: ['ltext litem*', 'li'], ltext: ['inline*', 'div'], trow: ['tcell*', 'tr'], tcell: ['inline*', 'td'],
    icap: ['inline*', 'figcaption']
  };

  // --- Schema-Beschreibung und Pruefsumme (gleich collab_model.SCHEMA_HASH) ------------------------------------------
  function pyJson(v) {           // json.dumps(..., separators=(",", ":")) fuer Listen/Zeichenketten/Zahlen (ensure_ascii)
    if (Array.isArray(v)) return '[' + v.map(pyJson).join(',') + ']';
    if (typeof v === 'number') return String(v);
    return '"' + String(v).replace(/[\\"\u0000-\u001f\u007f-￿]/g, function (c) {
      if (c === '"') return '\\"';
      if (c === '\\') return '\\\\';
      var m = { '\n': '\\n', '\r': '\\r', '\t': '\\t', '\b': '\\b', '\f': '\\f' }[c];
      return m || '\\u' + ('000' + c.charCodeAt(0).toString(16)).slice(-4);
    }) + '"';
  }
  function hashInput() {
    var keys = Object.keys(ATTRS).sort();
    return pyJson([MODEL, VERSION, BLOCKS.slice().sort(), keys.map(function (k) { return [k, ATTRS[k]]; }),
      MARKS, INLINE_NODES, BLOCK_COMMON]);
  }

  // SHA-256 (synchron, klein) ueber UTF-8 - fuer die Schema-Pruefsumme und interne Listenpunkt-IDs (pm-adapter.js)
  var K256 = [];
  (function () {
    function frac(x) { return ((x - Math.floor(x)) * 4294967296) >>> 0; }
    var n = 2, c = 0;
    while (c < 64) {
      var p = true;
      for (var d = 2; d * d <= n; d++) if (n % d === 0) { p = false; break; }
      if (p) { K256.push(frac(Math.pow(n, 1 / 3))); c++; }
      n++;
    }
  })();
  function utf8(s) {
    var out = [];
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0xd800 && c < 0xdc00 && i + 1 < s.length) {
        var c2 = s.charCodeAt(i + 1);
        if (c2 >= 0xdc00 && c2 < 0xe000) { c = 0x10000 + ((c - 0xd800) << 10) + (c2 - 0xdc00); i++; }
      }
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xc0 | c >> 6, 0x80 | c & 63);
      else if (c < 0x10000) out.push(0xe0 | c >> 12, 0x80 | c >> 6 & 63, 0x80 | c & 63);
      else out.push(0xf0 | c >> 18, 0x80 | c >> 12 & 63, 0x80 | c >> 6 & 63, 0x80 | c & 63);
    }
    return out;
  }
  function sha256(str) {
    var b = utf8(str), l = b.length * 8;
    b.push(0x80);
    while (b.length % 64 !== 56) b.push(0);
    for (var s = 56; s >= 0; s -= 8) b.push(s >= 32 ? Math.floor(l / Math.pow(2, s)) & 255 : (l >>> s) & 255);
    var H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19], w = [];
    function r(x, n) { return (x >>> n) | (x << (32 - n)); }
    for (var o = 0; o < b.length; o += 64) {
      for (var i = 0; i < 64; i++) {
        if (i < 16) w[i] = (b[o + 4 * i] << 24 | b[o + 4 * i + 1] << 16 | b[o + 4 * i + 2] << 8 | b[o + 4 * i + 3]) >>> 0;
        else {
          var s0 = r(w[i - 15], 7) ^ r(w[i - 15], 18) ^ (w[i - 15] >>> 3), s1 = r(w[i - 2], 17) ^ r(w[i - 2], 19) ^ (w[i - 2] >>> 10);
          w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
        }
      }
      var a = H[0], bb = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (i = 0; i < 64; i++) {
        var t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i]) >>> 0;
        var t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c))) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = bb; bb = a; a = (t1 + t2) >>> 0;
      }
      H = [H[0] + a, H[1] + bb, H[2] + c, H[3] + d, H[4] + e, H[5] + f, H[6] + g, H[7] + h].map(function (x) { return x >>> 0; });
    }
    return H.map(function (x) { return ('0000000' + x.toString(16)).slice(-8); }).join('');
  }
  function hash() { return sha256(hashInput()).slice(0, 16); }

  // --- HTML (Zwischenablage/Anzeige): Attribute als JSON in data-pm, Block-ID in data-block-id ------------------------
  function readPm(el) {
    try { var v = JSON.parse(el.getAttribute('data-pm') || '{}'); return v && typeof v === 'object' ? v : {}; } catch (e) { return {}; }
  }
  function writePm(attrs, skip) {
    var o = {}, any = false;
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] !== null && attrs[k] !== undefined && (!skip || skip.indexOf(k) < 0)) { o[k] = attrs[k]; any = true; }
    });
    return any ? JSON.stringify(o) : null;
  }
  function attrSpec(names, isBlock) {
    var spec = {};
    names.forEach(function (k) {
      spec[k] = {
        default: null, rendered: false, keepOnSplit: !(isBlock && (k === 'id' || k === 'conflict' || k === 'extra')),
        parseHTML: k === 'id' && isBlock
          ? function (el) { return el.getAttribute('data-block-id') || null; }
          : function (el) { var v = readPm(el)[k]; return v === undefined ? null : v; }
      };
    });
    return spec;
  }
  function blockDom(name, attrs, extra) {
    var o = { 'data-block-type': name === 'synced_ref' ? 'synced' : name === 'unknown' ? String(attrs.type || 'unknown') : name };
    if (attrs.id) o['data-block-id'] = attrs.id;
    var pm = writePm(attrs, ['id']);
    if (pm) o['data-pm'] = pm;
    if (attrs.style && attrs.style.c && PALETTE.indexOf(attrs.style.c) >= 0) o['class'] = 'hk-st-' + attrs.style.c + (attrs.style.bg ? ' hk-st-bg' : '');
    for (var k in extra || {}) o[k] = extra[k];
    return o;
  }

  function extensions(T, opts) {
    opts = opts || {};
    if (!T || !T.Node || !T.Mark) throw new Error('HKPmSchema.extensions: Tiptap-Bausteine fehlen (T.Node/T.Mark)');
    var N = T.Node, M = T.Mark, out = [];
    function title(fn, v) { try { return fn ? String(fn(v) || '') : ''; } catch (e) { return ''; } }

    out.push(N.create({ name: 'doc', topNode: true, content: 'block*' }));
    out.push(N.create({ name: 'text', group: 'inline' }));

    BLOCKS.forEach(function (name) {
      var cfg = {
        name: name, group: group(name), draggable: true,
        addAttributes: function () { return attrSpec(BLOCK_COMMON.concat(ATTRS[name]), true); }
      };
      if (CONTENT[name]) { cfg.content = CONTENT[name]; cfg.defining = true; }
      else {
        cfg.atom = true; cfg.selectable = true;
        // T4b: Node-Views der Werkzeuge (static/pm-tools.js, HKTools.nodeViews) - Renderer im Tiptap-addNodeView-Format
        var nv = opts.nodeViews && Object.prototype.hasOwnProperty.call(opts.nodeViews, name) ? opts.nodeViews[name] : null;
        if (typeof nv === 'function') cfg.addNodeView = function () { return nv; };
      }
      if (name === 'code') { cfg.marks = ''; cfg.code = true; }
      if (name === 'toggle' || name === 'columns' || name === 'synced' || name === 'list' || name === 'table' ||
          name === 'quote' || name === 'image') cfg.isolating = true;
      if (TEXTBLOCK.indexOf(name) >= 0 || name === 'code') cfg.marks = name === 'code' ? '' : '_';
      cfg.parseHTML = function () {
        var rules = [{ tag: '[data-block-type="' + name + '"]', priority: 60 }];
        if (name === 'paragraph') rules.push({ tag: 'p' });
        if (name === 'header') [1, 2, 3, 4].forEach(function (l) { rules.push({ tag: 'h' + l, attrs: { level: l } }); });
        if (name === 'code') rules.push({ tag: 'pre', preserveWhitespace: 'full' });
        if (name === 'delimiter') rules.push({ tag: 'hr' });
        if (name === 'synced_ref') rules[0] = { tag: '[data-block-type="synced"][data-pm*="sid"]:empty', priority: 61 };
        return rules;
      };
      cfg.renderHTML = function (p) {
        var a = p.node.attrs;
        if (name === 'paragraph') return ['p', blockDom(name, a), 0];
        if (name === 'header') return ['h' + Math.min(4, Math.max(1, +a.level || 2)), blockDom(name, a), 0];
        if (name === 'code') return ['pre', blockDom(name, a), ['code', 0]];
        if (name === 'delimiter') return ['hr', blockDom(name, a)];
        if (!CONTENT[name]) return ['div', blockDom(name, a)];
        return ['div', blockDom(name, a), 0];
      };
      out.push(N.create(cfg));
    });

    Object.keys(PARTS).forEach(function (name) {
      out.push(N.create({
        name: name, content: PARTS[name][0], defining: true,
        isolating: name === 'tcell' || name === 'column' || name === 'litem',
        addAttributes: function () { return attrSpec(ATTRS[name], false); },
        parseHTML: function () { return [{ tag: PARTS[name][1] + '[data-pm-part="' + name + '"]' }]; },
        renderHTML: function (p) {
          var o = { 'data-pm-part': name }, pm = writePm(p.node.attrs);
          if (pm) o['data-pm'] = pm;
          return [PARTS[name][1], o, 0];
        }
      }));
    });

    // Inline-Knoten: ohne Kinder; Marken um den Knoten stehen fuer Yjs im Attribut "marks" (y-tiptap schreibt
    // node.marks nicht nach Yjs) - pm-adapter.js liest beides
    var INL = {
      hardBreak: {
        parse: [{ tag: 'br' }],
        render: function () { return ['br']; }
      },
      mention: {
        parse: [{ tag: 'span.hk-mention[data-u]', priority: 70, getAttrs: function (el) { return { u: el.getAttribute('data-u') }; } }],
        render: function (a) { return ['span', { 'class': 'hk-mention', 'data-u': a.u || '' }, title(opts.mentionName, a.u)]; }
      },
      date: {
        parse: [{ tag: 'time[datetime]', priority: 70, getAttrs: function (el) {
          return { dt: el.getAttribute('datetime'), remind: /(^|\s)hk-remind(\s|$)/.test(el.className || ''), label: el.textContent };
        } }],
        render: function (a) { return ['time', { datetime: a.dt || '', 'class': a.remind ? 'hk-remind' : 'hk-date' }, a.label || '']; }
      },
      math_inline: {
        parse: [{ tag: 'code.hk-math', priority: 70, getAttrs: function (el) { return { tex: el.textContent }; } }],
        render: function (a) { return ['code', { 'class': 'hk-math' }, a.tex || '']; }
      },
      page: {
        parse: [{ tag: 'a[href]', priority: 70, getAttrs: function (el) {
          var m = /^\/notes#([1-9][0-9]{0,17})$/.exec(el.getAttribute('href') || '');
          return m ? { id: +m[1] } : false;
        } }],
        render: function (a) { return ['a', { href: '/notes#' + (a.id || 0) }, title(opts.pageTitle, a.id)]; }
      }
    };
    INLINE_NODES.forEach(function (name) {
      out.push(N.create({
        name: name, inline: true, group: 'inline', atom: true, selectable: name !== 'hardBreak',
        addAttributes: function () { return attrSpec(ATTRS[name], false); },
        parseHTML: function () { return INL[name].parse; },
        renderHTML: function (p) { return INL[name].render(p.node.attrs); },
        renderText: name === 'hardBreak' ? function () { return '\n'; } : undefined
      }));
    });

    // Marken in Rangfolge (aussen -> innen)
    function hasClass(el, c) { return (' ' + (el.className || '') + ' ').indexOf(' ' + c + ' ') >= 0; }
    function palClass(el, pre) {
      var cl = String(el.className || '').split(/\s+/);
      for (var i = 0; i < cl.length; i++) if (cl[i].indexOf(pre) === 0 && PALETTE.indexOf(cl[i].slice(pre.length)) >= 0) return cl[i].slice(pre.length);
      return null;
    }
    var MK = {
      link: {
        attrs: { href: { default: null } }, inclusive: false,
        parse: [{ tag: 'a[href]', getAttrs: function (el) {
          var h = el.getAttribute('href') || '';
          if (/^\/notes#/.test(h)) return false;
          h = linkHref(h);                                           // 2.5 (Sicherheit): nur Schemata, die der Server zulaesst
          return h ? { href: h } : false;
        } }],
        render: function (a) { return ['a', { href: a.href || '', target: '_blank', rel: 'noopener noreferrer' }, 0]; }
      },
      color: {
        attrs: { c: { default: null } },
        parse: [{ tag: 'span', getAttrs: function (el) { var c = palClass(el, 'hk-c-'); return c ? { c: c } : false; } }],
        render: function (a) { return ['span', { 'class': 'hk-c-' + (a.c || '') }, 0]; }
      },
      bg: {
        attrs: { c: { default: null } }, excludes: 'bg mark',
        parse: [{ tag: 'mark', priority: 60, getAttrs: function (el) { var c = palClass(el, 'hk-bg-'); return c ? { c: c } : false; } }],
        render: function (a) { return ['mark', { 'class': 'hk-bg-' + (a.c || '') }, 0]; }
      },
      mark: {
        excludes: 'mark bg',
        parse: [{ tag: 'mark', getAttrs: function (el) { return palClass(el, 'hk-bg-') ? false : null; } }],
        render: function () { return ['mark', { 'class': 'cdx-marker' }, 0]; }
      },
      b: { parse: [{ tag: 'b' }, { tag: 'strong' }], render: function () { return ['b', 0]; } },
      i: { parse: [{ tag: 'i' }, { tag: 'em' }], render: function () { return ['i', 0]; } },
      u: { parse: [{ tag: 'u' }], render: function () { return ['u', 0]; } },
      s: { parse: [{ tag: 's' }], render: function () { return ['s', 0]; } },
      icode: {
        code: true,
        parse: [{ tag: 'code', getAttrs: function (el) { return hasClass(el, 'hk-math') ? false : null; } }],
        render: function () { return ['code', { 'class': 'inline-code' }, 0]; }
      }
    };
    MARKS.forEach(function (name) {
      var d = MK[name], cfg = {
        name: name,
        addAttributes: function () {
          var s = {};
          Object.keys(d.attrs || {}).forEach(function (k) {
            s[k] = { default: null, rendered: false, parseHTML: function () { return null; } };
          });
          return s;
        },
        parseHTML: function () { return d.parse; },
        renderHTML: function (p) { return d.render(p.mark.attrs); }
      };
      if (d.excludes) cfg.excludes = d.excludes;
      if (d.inclusive === false) cfg.inclusive = false;
      if (d.code) cfg.code = true;
      out.push(M.create(cfg));
    });

    // Block-IDs: 10 Zeichen [A-Za-z0-9], keine Vergabe fuer entfernte (y-sync-)Transaktionen
    var useId = opts.uniqueId !== undefined ? !!opts.uniqueId : !!T.UniqueID;
    if (useId && T.UniqueID) {
      out.push(T.UniqueID.configure({
        attributeName: 'id', types: BLOCKS.slice(),
        generateID: opts.generateID || newId,
        filterTransaction: opts.filterTransaction || function (tr) { return !tr.getMeta('y-sync$'); }
      }));
    }
    return out;
  }

  var ID_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  // Link-Marke: nur http(s) und mailto (wie saga/pages.py _Sanitizer; /notes#<id> ist der Knoten "page", nie eine Marke) -
  // sonst '' (kein Link). Leerzeichen am Rand zaehlen nicht, Steuerzeichen machen die Adresse ungueltig.
  function linkHref(h) {
    h = String(h == null ? '' : h).replace(/^[ \t\n\r\f]+|[ \t\n\r\f]+$/g, '');
    return /^(https?|mailto):/i.test(h) && !/[\u0000-\u001f\u007f]/.test(h) ? h : '';
  }
  // 2.5 (Sicherheit): PM-JSON aus fremder Hand (eingefuegtes Saga-HTML mit data-pm) vor dem Einfuegen entschaerfen - der
  // Browser laedt Bild-/Vorschau-Adressen sofort mit der Sitzung (GET /logout o. ae.), bevor der Server etwas pruefen kann.
  // Bilder/Dateien/Vorschaubilder nur /files/<id> bzw. /api/fed/files/<seite>/<id>, Link-Felder nur http(s)/mailto.
  var FILE_URL = /^\/files\/[0-9a-f]{24}$|^\/api\/fed\/files\/-[1-9][0-9]{0,18}\/[0-9a-f]{24}$/;
  var URL_KEYS = { url: 1, href: 1, link: 1, source: 1, embed: 1, src: 1, inst: 1 };
  function fileUrl(u) { return typeof u === 'string' && FILE_URL.test(u); }
  function cleanFile(f) { return f && typeof f === 'object' && !Array.isArray(f) && fileUrl(f.url) ? f : null; }
  // wg: Widget-Daten - dort ist cfg.src die Quelle ('fixed'|'db'|'tasks', saga/page_widgets.py), keine Adresse
  function cleanData(v, key, depth, wg) {
    if (depth > 40) return null;
    if (typeof v === 'string') return URL_KEYS[key] && !(wg && key === 'src') && v && !linkHref(v) ? '' : v;
    if (Array.isArray(v)) return v.map(function (x) { return cleanData(x, key, depth + 1, wg); });
    if (v && typeof v === 'object') {
      var o = {};
      Object.keys(v).forEach(function (k) {
        if (k === '__proto__' || k === 'constructor' || k === 'prototype') return;
        o[k] = k === 'file' ? cleanFile(v[k]) : cleanData(v[k], k, depth + 1, wg);
      });
      return o;
    }
    return v;
  }
  function cleanRender(r) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) return null;
    if ((r.png != null && !fileUrl(r.png)) || (r.svg != null && !fileUrl(r.svg))) return null;
    return r;
  }
  function cleanPm(n, depth) {
    depth = depth || 0;
    if (!n || typeof n !== 'object' || depth > 60) return n;
    var a = n.attrs;
    if (a && typeof a === 'object') {
      a = Object.assign({}, a);
      if (n.type === 'image' && a.file != null) a.file = cleanFile(a.file);
      if ((n.type === 'code' || n.type === 'math') && a.render != null) a.render = cleanRender(a.render);
      if (OPAQUE.indexOf(n.type) >= 0 && a.data != null) a.data = cleanData(a.data, null, 0, n.type === 'widget');
      if (n.type === 'unknown' && a.data != null) a.data = cleanData(a.data, null, 0);
      n = Object.assign({}, n, { attrs: a });
    }
    if (Array.isArray(n.marks)) {
      n = Object.assign({}, n, { marks: n.marks.filter(function (m) { return !(m && m.type === 'link') || linkHref(m.attrs && m.attrs.href); }) });
    }
    if (Array.isArray(n.content)) n = Object.assign({}, n, { content: n.content.map(function (c) { return cleanPm(c, depth + 1); }) });
    return n;
  }
  function newId() {
    var s = '', c = typeof crypto !== 'undefined' && crypto.getRandomValues ? crypto.getRandomValues(new Uint8Array(10)) : null;
    for (var i = 0; i < 10; i++) s += ID_CHARS[(c ? c[i] : Math.floor(Math.random() * 256)) % 62];
    return s;
  }

  return {
    MODEL: MODEL, VERSION: VERSION, PALETTE: PALETTE, MARKS: MARKS, INLINE_NODES: INLINE_NODES, TEXTBLOCK: TEXTBLOCK,
    OPAQUE: OPAQUE, BLOCKS: BLOCKS, BLOCK_COMMON: BLOCK_COMMON, ATTRS: ATTRS, PARTS: Object.keys(PARTS),
    NESTED_OK: NO_COLS, SYNC_OK: NO_SYNC, extensions: extensions, hashInput: hashInput, hash: hash, sha256: sha256,
    newId: newId, linkHref: linkHref, fileUrl: fileUrl, cleanPm: cleanPm
  };
});
