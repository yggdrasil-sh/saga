// Seiten-Editor auf Tiptap/ProseMirror (Saga 2.5, Tiptap-Umbau Etappe T2 "Kern-Editor").
// Aktiv nur mit <body data-editor="tiptap"> (Admin editor.engine = tiptap bzw. choice + Konto-Wahl, saga.core.editor_engine);
// sonst tut diese Datei nichts. Gespeichert wird weiter Block-JSON (static/pm-adapter.js), ueber denselben Pfad wie mit
// Editor.js (static/pages.js: PATCH merge:1, Rebase/mergedReply, Offline, Konflikt). pages.js bekommt ein kleines
// Editor-Objekt (create() unten: save/render/applyBlocks/caret ...) und spricht sonst nur ueber die Fassade.
//
// Fassade HKPages.k.doc (kind 'tiptap'): dieselbe Schnittstelle und Semantik wie static/editor-doc.js (dort beschrieben).
// Bloecke in Spalten, Toggles und synchronisierten Bloecken stehen mit parent/slot in blocks()/byId() (T3); Bloecke in
// Karten (noch nicht portierte Typen) fehlen. Mutationen sind synchron (Promise loest sofort auf).
//
// Bloecke: Absatz, Ueberschrift 1-4, Zitat (Text, Quelle, Ausrichtung), Hinweis, Trennlinie und Liste (3 Stile,
// verschachtelt) sind bearbeitbar; Toggle, Spalten und synchronisierte Bloecke kommen aus static/pm-struktur.js (T3,
// Anmeldung ueber HKPmEditor.use). Alle anderen Typen erscheinen als Karte "noch nicht unterstuetzt" und bleiben
// unveraendert (WeakMap-Durchreichung, Schutz-Plugin gegen Aenderungen im Inneren). Werkzeug-Ansichten aus
// static/pm-tools*.js (window.HKTools.nodeViews(ctx), Rolle t4b) haben Vorrang vor der Karte.
//
// CSP (script-src/style-src 'self'): kein injectCSS, keine style-Attribute; Lage von Menues nur ueber CSSOM (el.style.x).
(function () {
  'use strict';
  if (!window.HKPages || !HKPages.k || document.body.dataset.editor !== 'tiptap') return;
  var K = HKPages.k, el = HK.el, BOX = document.getElementById('pg-editor');
  if (!BOX) return;
  var V = document.body.dataset.v || '';
  var AID = 'data-block-id', ATY = 'data-block-type';
  var P = null, SC = null, AD = null;                 // window.SagaPM, HKPmSchema, HKPmAdapter (nach load())
  var ed = null, holder = null, ctx = null, data0 = null, broken = false;
  var CB = null;                                      // Rueckrufe aus pages.js (create)
  var TITLES = {}, NAMES = {};                        // Rueckfall-Titel/-Namen aus dem geladenen Inhalt
  var BLOCKSET = {}, EDIT = { paragraph: 1, header: 1, callout: 1, quote: 1, list: 1, delimiter: 1 };
  var TEXTB = { paragraph: 1, header: 1, callout: 1 };
  var COLORS = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  var CALLOUT_EMOJI = ['💡', '⚠️', '✅', '❗', 'ℹ️', '🔥', '📌'];        // wie CalloutTool (pages.js)
  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  // Werkzeug-Skripte der Rolle t4b (static/pm-tools.js, static/pm-tools-daten.js) - werden mitgeladen, sobald sie da sind
  var TOOL_SCRIPTS = ['pm-tools.js', 'pm-tools-daten.js', 'pm-tools-medien.js'];
  // Erweiterungen des Kern-Editors (T3: pm-struktur.js), melden sich mit HKPmEditor.use(fn) an (siehe "Erweiterungen")
  var EXT_SCRIPTS = ['pm-struktur.js', 'pm-tools-text.js'];          // T4c: Tabelle, Code, Mathe-Block
  var EXTS = [], MODS = [];

  // =================================================================================================================
  // Laden (vorgebautes Bundle + Schema + Adapter, CSS als Datei)
  // =================================================================================================================
  var loading = null;
  function script(src) {
    return new Promise(function (ok, fail) {
      var s = document.createElement('script'); s.async = false; s.src = src; s.onload = ok;
      s.onerror = function () { fail(new Error(T('Laden fehlgeschlagen: {src}', { src: src }))); };
      document.body.appendChild(s);
    });
  }
  function css(href) { var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = href; document.head.appendChild(l); }
  function load() {
    if (loading) return loading;
    css('/static/vendor/tiptap/saga-pm.css?v=' + V); css('/static/pm-editor.css?v=' + V);
    var list = ['/static/vendor/tiptap/saga-pm.js', '/static/pm-schema.js', '/static/pm-adapter.js']
      .concat(TOOL_SCRIPTS.concat(EXT_SCRIPTS).map(function (f) { return '/static/' + f; }));
    loading = Promise.all(list.map(function (s) { return script(s + '?v=' + V); })).then(function () {
      P = window.SagaPM; SC = window.HKPmSchema; AD = window.HKPmAdapter;
      SC.BLOCKS.forEach(function (b) { BLOCKSET[b] = 1; });
      EXTS.forEach(initExt); EXTS = [];
    });
    loading.catch(function () { loading = null; });
    return loading;
  }

  // =================================================================================================================
  // Hilfen: Knoten, Positionen, Block-JSON
  // =================================================================================================================
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function plainOf(html) { var t = document.createElement('template'); t.innerHTML = String(html || ''); return t.content.textContent || ''; }
  function sagaType(n) { var t = n.type.name; return t === 'synced_ref' ? 'synced' : t === 'unknown' ? String(n.attrs.type || 'unknown') : t; }
  function isBlock(n) { return !!(n && BLOCKSET[n.type.name]); }
  function schema() { return ed.schema; }
  function newId() { return SC.newId(); }
  function ro() { return !ed || !ed.isEditable; }

  // geschuetzt wie eine Karte (Inhalt nicht bearbeitbar): nicht portierte Typen + Erweiterungen (z. B. synchronisierte
  // Kopie nur lesend, pm-struktur.js)
  function cardish(n) {
    if (!EDIT[n.type.name]) return true;
    for (var i = 0; i < MODS.length; i++) if (MODS[i].protect && MODS[i].protect(n)) return true;
    return false;
  }
  // alle Bloecke (auch verschachtelt) in Dokumentreihenfolge: fn({node, pos, parent, slot, inCard, depth})
  function walk(doc, fn) {
    function rec(node, start, pid, slot, inCard, dp) {
      node.forEach(function (c, off) {
        var p = start + off, t = c.type.name;
        if (!BLOCKSET[t]) return;
        if (fn({ node: c, pos: p, parent: pid, slot: slot, inCard: inCard, depth: dp }) === false) throw walk.STOP;
        var card = inCard || cardish(c);
        if (t === 'toggle' || t === 'synced') rec(c, p + 1, c.attrs.id, 0, card, dp + 1);
        else if (t === 'columns') c.forEach(function (col, o2, ci) { rec(col, p + 1 + o2 + 1, c.attrs.id, ci, card, dp + 1); });
      });
    }
    try { rec(doc, 0, null, 0, false, 0); } catch (e) { if (e !== walk.STOP) throw e; }
  }
  walk.STOP = {};
  // Block-ID -> Lage, je Dokumentstand einmal berechnet (Fassade: blocks() fragt je Getter, sonst quadratisch)
  var ID_CACHE = new WeakMap();
  function findId(id, doc) {
    doc = doc || ed.state.doc; id = String(id);
    var m = ID_CACHE.get(doc);
    if (!m) { m = {}; walk(doc, function (x) { var i = x.node.attrs.id; if (i && !(i in m)) m[i] = x; }); ID_CACHE.set(doc, m); }
    return Object.prototype.hasOwnProperty.call(m, id) ? m[id] : null;
  }
  // Block, in dem eine Position liegt (innerster), samt Lage
  function blockAt(pos, doc) {
    doc = doc || ed.state.doc;
    var $p = doc.resolve(Math.max(0, Math.min(pos, doc.content.size))), best = null;
    for (var d = $p.depth; d >= 1; d--) {
      var n = $p.node(d);
      if (isBlock(n)) { best = { node: n, pos: $p.before(d), depth: d }; break; }
    }
    if (!best) { var after = $p.nodeAfter; if (after && isBlock(after) && $p.depth === 0) best = { node: after, pos: pos, depth: 1 }; }
    return best;
  }
  function blockJson(n) {
    var b = AD.pmToBlock(n, ctx), tu = {};
    Object.keys(b.tunes || {}).forEach(function (k) { if (b.tunes[k] != null) tu[k] = b.tunes[k]; });
    return { id: b.id, type: b.type, data: b.data, tunes: tu };
  }
  function nodeOf(b) {                                // Block-JSON -> echter PM-Knoten (+ Durchreichung)
    var j = AD.blockToPm(b, { orig: null }), n = schema().nodeFromJSON(j);
    n.check();
    AD.bind(schema().topNodeType.create(null, [n]), { blocks: [b] }, ctx);
    return n;
  }
  function stable(v) { return AD.stable(v); }

  // Titel/Namen fuer Seitenverweise und Erwaehnungen (stehen nicht im Modell): aktuelle Seitenliste bzw. people,
  // Rueckfall der beim Laden gespeicherte Text
  function pageTitle(id) {
    var p = K.byId(+id);
    if (p) return (K.ti(p.icon) ? K.ti(p.icon) + ' ' : '') + K.label(p);
    return TITLES[id] != null ? TITLES[id] : T('Seite nicht gefunden');
  }
  function personName(u) {
    var pp = window.HKMention ? HKMention.people() : {}, p = pp && Object.prototype.hasOwnProperty.call(pp, u) ? pp[u] : null;
    return p && p.name ? p.name : NAMES[u] != null ? NAMES[u] : String(u || '');
  }
  function collectTitles(data) {
    TITLES = {}; NAMES = {};
    (function rec(v, depth) {
      if (depth > 30 || v == null) return;
      if (typeof v === 'string') {
        if (v.indexOf('<') < 0) return;
        v.replace(/<a\s[^>]*href="\/notes#(\d+)"[^>]*>([\s\S]*?)<\/a>/g, function (m, id, t) { if (!(id in TITLES)) TITLES[id] = plainOf(t); return m; });
        v.replace(/<span\s[^>]*data-u="([^"]+)"[^>]*>([\s\S]*?)<\/span>/g, function (m, u, t) { if (!(u in NAMES)) NAMES[plainOf(u)] = plainOf(t); return m; });
        return;
      }
      if (Array.isArray(v)) v.forEach(function (x) { rec(x, depth + 1); });
      else if (typeof v === 'object') Object.keys(v).forEach(function (k) { rec(v[k], depth + 1); });
    })(data, 0);
  }
  var RESOLVE = {
    page: function (bid, id) { return esc(pageTitle(id)); },
    mention: function (bid, u) { return esc(personName(u)); }
  };

  // =================================================================================================================
  // Node-Views: Blockelemente tragen data-block-id/-type; Aenderungen anderer Module am aeusseren Element (Kommentar-
  // zahl, Anwesenheit, Klassen) stoeren ProseMirror nicht (ignoreMutation ausserhalb des Inhalts)
  // =================================================================================================================
  function paint(dom, n, base) {
    var id = n.attrs.id;
    if (id) { if (dom.getAttribute(AID) !== id) dom.setAttribute(AID, id); } else if (dom.hasAttribute(AID)) dom.removeAttribute(AID);
    var ty = sagaType(n); if (dom.getAttribute(ATY) !== ty) dom.setAttribute(ATY, ty);
    if (base) base.split(' ').forEach(function (c) { if (c) dom.classList.add(c); });
    var st = n.attrs.style, want = st && COLORS.indexOf(st.c) >= 0 ? (st.bg ? 'hk-bg-' : 'hk-c-') + st.c : null;
    COLORS.forEach(function (c) {
      ['hk-c-', 'hk-bg-'].forEach(function (pre) { var k = pre + c; if (k !== want && dom.classList.contains(k)) dom.classList.remove(k); });
    });
    if (want) dom.classList.add(want);
    dom.classList.toggle('pm-conflict', !!n.attrs.conflict);
  }
  function ignoreOutside(inner) {
    return function (m) { if (m.type === 'selection') return false; return !inner || !inner.contains(m.target) || (m.type === 'attributes' && m.target === inner); };
  }
  function nodeBtn(cls, text, label) {
    var b = el('button', cls, text); b.type = 'button'; b.contentEditable = 'false'; b.tabIndex = -1;
    if (label) { b.title = label; b.setAttribute('aria-label', label); }
    b.addEventListener('mousedown', function (e) { e.preventDefault(); });
    return b;
  }
  function posOf(getPos) { var p = typeof getPos === 'function' ? getPos() : null; return typeof p === 'number' ? p : null; }
  function setAttrs(getPos, patch) {
    var p = posOf(getPos); if (p == null || ro()) return;
    var n = ed.state.doc.nodeAt(p); if (!n) return;
    ed.view.dispatch(ed.state.tr.setNodeMarkup(p, null, Object.assign({}, n.attrs, patch)));
  }

  // Textblock: aeusseres div (Blockelement) + inneres Element (Inhalt)
  function textView(tagOf, decorate) {
    return function (props) {
      var node = props.node, name = node.type.name, dom = el('div', 'pm-blk pm-b-' + name), inner = document.createElement(tagOf(node));
      inner.className = 'pm-in'; dom.appendChild(inner); paint(dom, node);
      var v = { dom: dom, contentDOM: inner, ignoreMutation: ignoreOutside(inner),
        update: function (n) { if (n.type !== node.type || tagOf(n) !== tagOf(node)) return false; node = n; paint(dom, n); if (v.sync) v.sync(n); return true; },
        stopEvent: function (e) { return !!(e.target && e.target.closest && e.target.closest('.pm-nb')); } };
      if (decorate) decorate(v, props, dom, inner, function () { return node; });
      return v;
    };
  }
  var NV = {};
  NV.paragraph = textView(function () { return 'p'; });
  NV.header = textView(function (n) { return 'h' + Math.min(4, Math.max(1, +n.attrs.level || 2)); });
  NV.callout = textView(function () { return 'div'; }, function (v, props, dom, inner, cur) {
    dom.classList.add('pm-callout');
    var b = nodeBtn('pm-nb pm-emo', cur().attrs.emoji || '💡', T('Symbol wechseln'));
    dom.insertBefore(b, inner);
    b.addEventListener('click', function (e) {
      e.preventDefault(); if (ro()) return;
      var i = CALLOUT_EMOJI.indexOf(cur().attrs.emoji);
      setAttrs(props.getPos, { emoji: CALLOUT_EMOJI[(i + 1) % CALLOUT_EMOJI.length] });
    });
    v.sync = function (n) { var e = n.attrs.emoji || '💡'; if (b.textContent !== e) b.textContent = e; };
  });
  var ALIGN = ['left', 'center'];
  NV.quote = textView(function () { return 'div'; }, function (v, props, dom, inner, cur) {
    dom.classList.add('pm-quote');
    var b = nodeBtn('pm-nb pm-qal', '⇔', T('Ausrichtung'));
    dom.appendChild(b);
    b.addEventListener('click', function (e) {
      e.preventDefault(); if (ro()) return;
      var a = cur().attrs.alignment === 'center' ? 'left' : 'center';
      setAttrs(props.getPos, { alignment: a });
    });
    v.sync = function (n) { ALIGN.forEach(function (a) { dom.classList.toggle('pm-al-' + a, (n.attrs.alignment || 'left') === a); }); };
    v.sync(cur());
  });
  NV.list = function (props) {
    var node = props.node, dom = el('div', 'pm-blk pm-b-list'), tag = function (n) { return n.attrs.listStyle === 'ordered' ? 'ol' : 'ul'; };
    var inner = document.createElement(tag(node)); inner.className = 'pm-in pm-list';
    dom.appendChild(inner);
    function sync(n) {
      paint(dom, n);
      var st = n.attrs.listStyle || 'unordered', meta = n.attrs.meta || {};
      ['unordered', 'ordered', 'checklist'].forEach(function (s) { dom.classList.toggle('pm-ls-' + s, s === st); });
      var ct = st === 'ordered' && /^(lower|upper)-(roman|alpha)$/.test(meta.counterType || '') ? meta.counterType : 'numeric';
      ['numeric', 'lower-roman', 'upper-roman', 'lower-alpha', 'upper-alpha'].forEach(function (c) { dom.classList.toggle('pm-ct-' + c, c === ct); });
      var start = st === 'ordered' && typeof meta.start === 'number' && isFinite(meta.start) ? Math.round(meta.start) : 1;
      inner.style.counterReset = 'pmli ' + (start - 1);              // CSSOM (CSP-konform)
    }
    sync(node);
    return { dom: dom, contentDOM: inner, ignoreMutation: ignoreOutside(inner),
      update: function (n) { if (n.type !== node.type || (n.attrs.listStyle || '') !== (node.attrs.listStyle || '')) return false; node = n; sync(n); return true; } };
  };
  function listStyleAt(getPos, view) {
    var p = posOf(getPos), st = view && view.state ? view.state : ed && ed.state; if (p == null || !st) return 'unordered';
    var $p = st.doc.resolve(p);
    for (var d = $p.depth; d >= 0; d--) if ($p.node(d).type.name === 'list') return $p.node(d).attrs.listStyle || 'unordered';
    return 'unordered';
  }
  NV.litem = function (props) {
    var node = props.node, st = listStyleAt(props.getPos, props.view || (props.editor && props.editor.view)), dom = el('li', 'pm-li'), mk = null;
    if (st === 'checklist') {
      mk = nodeBtn('pm-nb pm-check', '', T('Erledigt'));
      mk.setAttribute('role', 'checkbox');
      mk.addEventListener('click', function (e) {
        e.preventDefault(); if (ro()) return;
        var m = Object.assign({}, node.attrs.meta || {}); m.checked = !m.checked;
        setAttrs(props.getPos, { meta: m });
      });
      dom.appendChild(mk);
    }
    var inner = el('div', 'pm-li-c'); dom.appendChild(inner);
    function sync(n) {
      var on = !!(n.attrs.meta && n.attrs.meta.checked);
      dom.classList.toggle('pm-done', st === 'checklist' && on);
      if (mk) mk.setAttribute('aria-checked', on ? 'true' : 'false');
    }
    sync(node);
    return { dom: dom, contentDOM: inner, ignoreMutation: ignoreOutside(inner),
      stopEvent: function (e) { return !!(mk && e.target && mk.contains(e.target)); },
      update: function (n) { if (n.type !== node.type) return false; node = n; sync(n); return true; } };
  };
  NV.delimiter = function (props) {
    var node = props.node, dom = el('div', 'pm-blk pm-b-delimiter'); dom.appendChild(document.createElement('hr')); paint(dom, node);
    return { dom: dom, ignoreMutation: function () { return true; },
      update: function (n) { if (n.type !== node.type) return false; node = n; paint(dom, n); return true; } };
  };
  // Rueckfall-Karte fuer alles, was T2 noch nicht bearbeitet (T3/T4): zeigt Typ + kurze Vorschau, Inhalt bleibt unberuehrt
  var CARD_NAME = { code: T('Code'), toggle: T('Toggle'), columns: T('Spalten'), synced: T('Synchronisierter Block'),
    table: T('Tabelle'), image: T('Bild'), math: T('Formel'), database: T('Datenbank'), tasks: T('Aufgaben'), calendar: T('Kalender'),
    widget: T('Widget'), buttons: T('Schaltflächen'), attaches: T('Datei'), nextcloud: 'Nextcloud', embed: T('Einbetten') };
  var CARD_IC = { code: '</>', toggle: '▸', columns: '▥', synced: '⟳', table: '▦', image: '🖼', math: '∑', database: '🗃',
    tasks: '☑', calendar: '📅', widget: '◷', buttons: '▭', attaches: '📎', nextcloud: '☁', embed: '⧉' };
  function cardPreview(n) {
    var b; try { b = AD.pmToBlock(n, ctx); } catch (e) { return ''; }
    var d = b.data || {}, t = '';
    if (b.type === 'code') t = String(d.code || '').split('\n')[0];
    else if (b.type === 'toggle') t = plainOf(d.title);
    else if (b.type === 'columns') t = T('{n} Spalten', { n: (d.cols || []).length });
    else if (b.type === 'table') t = (d.content || []).length + ' × ' + ((d.content || [])[0] || []).length;
    else if (b.type === 'image') t = plainOf(d.caption) || (d.file && d.file.name) || '';
    else if (b.type === 'math') t = String(d.tex || '');
    else if (b.type === 'attaches') t = (d.file && d.file.name) || d.title || '';
    else if (b.type === 'embed') t = d.service || d.source || '';
    else if (b.type === 'widget') t = d.kind || '';
    else if (typeof d.title === 'string') t = plainOf(d.title);
    return t.length > 80 ? t.slice(0, 79) + '…' : t;
  }
  function cardView(props) {
    var node = props.node, dom = el('div', 'pm-blk pm-card');
    function draw(n) {
      paint(dom, n);
      var ty = sagaType(n);
      dom.textContent = '';
      dom.appendChild(el('span', 'pm-card-ic', CARD_IC[ty] || '□'));
      var tx = el('span', 'pm-card-t');
      tx.appendChild(el('b', null, CARD_NAME[ty] || ty));
      var pv = cardPreview(n); if (pv) tx.appendChild(el('span', 'pm-card-pv', pv));
      tx.appendChild(el('span', 'pm-card-h', T('Wird im neuen Editor noch nicht unterstützt – im klassischen Editor bearbeiten')));
      dom.appendChild(tx);
    }
    draw(node);
    dom.contentEditable = 'false';
    return { dom: dom, ignoreMutation: function () { return true; }, stopEvent: function () { return false; },
      update: function (n) { if (n.type !== node.type) return false; if (n !== node) { node = n; draw(n); } return true; } };
  }
  // Inline-Knoten ohne Text im Modell
  function inlineView(make) {
    return function (props) {
      var node = props.node, dom = make(node); dom.contentEditable = 'false';
      return { dom: dom, ignoreMutation: function () { return true; },
        update: function (n) { if (n.type !== node.type) return false; if (stable(n.attrs) !== stable(node.attrs)) { var d2 = make(n); dom.replaceWith(d2); return false; } node = n; return true; } };
    };
  }
  NV.mention = inlineView(function (n) {
    var s = el('span', 'hk-mention', personName(n.attrs.u)); s.setAttribute('data-u', n.attrs.u || '');
    if (window.HKMention) setTimeout(function () { HKMention.decorate(s.parentNode || undefined); }, 0);
    return s;
  });
  NV.page = inlineView(function (n) {
    var id = n.attrs.id, a = el('a', 'note-link', pageTitle(id)); a.setAttribute('href', '/notes#' + id);
    var p = K.byId(+id), na = !p && K.foreign();
    a.classList.toggle('broken', !p && !na); a.classList.toggle('noaccess', na);
    a.title = p ? T('Zur Seite {name}', { name: K.label(p) }) : na ? T('Seite ohne Zugriff') : T('Seite nicht gefunden');
    return a;
  });
  NV.date = inlineView(function (n) {
    var t = el('time', n.attrs.remind ? 'hk-remind' : 'hk-date', n.attrs.label || ''); t.setAttribute('datetime', n.attrs.dt || '');
    return t;
  });
  NV.math_inline = inlineView(function (n) { return el('code', 'hk-math', n.attrs.tex || ''); });

  function nodeViewFor(name) {
    for (var i = 0; i < MODS.length; i++) if (MODS[i].nodeViews && MODS[i].nodeViews[name]) return MODS[i].nodeViews[name];
    if (NV[name]) return NV[name];
    if (BLOCKSET[name] && !EDIT[name]) return cardView;
    return null;
  }
  // Werkzeug-Ansichten der Rolle t4b (static/pm-tools*.js): Map Typ -> NodeView-Fabrik
  function toolViews() {
    if (!window.HKTools || typeof HKTools.nodeViews !== 'function') return null;
    var c = {};
    Object.defineProperty(c, 'readOnly', { enumerable: true, get: function () { return !!(CB && CB.readOnly()); } });
    Object.defineProperty(c, 'role', { enumerable: true, get: function () { return CB ? CB.role() : null; } });
    Object.defineProperty(c, 'foreign', { enumerable: true, get: function () { return !!(CB && CB.foreign()); } });
    Object.defineProperty(c, 'page', { enumerable: true, get: function () { return CB ? CB.cur() : null; } });
    c.editor = function () { return ed; };
    try { return HKTools.nodeViews(c) || null; } catch (e) { return null; }
  }

  // =================================================================================================================
  // Schutz: Inhalt nicht unterstuetzter Bloecke (Karten) wird nie veraendert; Schreibmarke darin -> Karte auswaehlen
  // =================================================================================================================
  // je Dokumentstand einmal (WeakMap): filterTransaction und appendTransaction fragen beide
  var PROT_CACHE = new WeakMap();
  function protectedRanges(doc) {
    var out = PROT_CACHE.get(doc); if (out) return out;
    out = [];
    doc.descendants(function (n, pos) {
      if (!BLOCKSET[n.type.name]) return true;
      if (cardish(n)) { if (!n.isLeaf) out.push([pos, pos + n.nodeSize]); return false; }
      return true;
    });
    PROT_CACHE.set(doc, out);
    return out;
  }
  function isMarkStep(step) { var t = step.toJSON().stepType; return /^(add|remove)Mark$/.test(t); }
  function guardPlugin() {
    var S0 = P.pm.state;
    return new S0.Plugin({
      key: new S0.PluginKey('hkGuard'),
      props: {
        // Ausgewaehlte Karte (Widget, DB, Medien ...): Tippen ersetzt sie nicht. Ein Klick ins Werkzeug waehlt die
        // Karte; ein versehentlicher Tastendruck danach darf sie nicht still durch Text ersetzen. Loeschen per
        // Backspace/Entf bleibt (bewusste Aktion, Notion-Verhalten).
        handleTextInput: function (view) {
          var sel = view.state.selection, n = sel && sel.node;
          return !!(n && n.isAtom && BLOCKSET[n.type.name]);
        }
      },
      filterTransaction: function (tr, state) {
        if (!tr.docChanged || tr.getMeta('hkAllow')) return true;
        var prot = protectedRanges(state.doc); if (!prot.length) return true;
        var onlyMarks = true, badMarks = false;
        for (var i = 0; i < tr.steps.length; i++) {
          var m = tr.mapping.slice(0, i), step = tr.steps[i], bad = false;
          var cur = prot.map(function (r) { return [m.map(r[0], 1), m.map(r[1], -1)]; });
          var hit = function (a, b) {
            cur.forEach(function (r) {
              var s = r[0], e = r[1]; if (e - s < 2) return;
              var whole = a <= s && b >= e;
              if (!whole && a < e && b > s) bad = true;              // greift ins Innere oder schneidet an
            });
          };
          var mk = step.from != null && step.to != null && isMarkStep(step);
          if (!mk) onlyMarks = false;
          if (mk) hit(step.from, step.to);
          step.getMap().forEach(function (a, b) { hit(a, b); });
          if (bad && mk) badMarks = true;
          else if (bad) return false;
        }
        // Marken ueber eine Auswahl mit Karten (Strg+A + Fett ...): nicht still verwerfen, sondern nur ausserhalb der
        // Karten anwenden (eigene Transaktion mit denselben Marken-Schritten ohne die Karten-Bereiche)
        if (badMarks && onlyMarks && ed) {
          var steps = tr.steps.filter(function (s) {
            return !prot.some(function (r) { return s.from < r[1] && s.to > r[0]; });
          });
          if (steps.length) setTimeout(function () {
            if (!ed || ed.state.doc !== state.doc) return;
            var t2 = ed.state.tr; steps.forEach(function (s) { t2.step(s); });
            t2.setSelection(tr.selection.map(t2.doc, t2.mapping)); ed.view.dispatch(t2);
          }, 0);
          return false;
        }
        return !badMarks;
      },
      appendTransaction: function (trs, old, state) {
        var sel = state.selection;
        if (sel instanceof S0.NodeSelection) return null;
        var prot = protectedRanges(state.doc), head = sel.head, hit = null;
        prot.forEach(function (r) { if (head > r[0] && head < r[1]) hit = r; });
        if (!hit) return null;
        return state.tr.setSelection(S0.NodeSelection.create(state.doc, hit[0]));
      }
    });
  }

  // =================================================================================================================
  // Mehrfachauswahl der Fassade (select/selected) als Dekoration; Platzhalter (E7)
  // =================================================================================================================
  var SELKEY = null, PHKEY = null;
  function selPlugin() {
    var S0 = P.pm.state, VW = P.pm.view;
    SELKEY = new S0.PluginKey('hkBlockSel');
    return new S0.Plugin({
      key: SELKEY,
      state: { init: function () { return []; }, apply: function (tr, v) { var m = tr.getMeta(SELKEY); return m ? m.slice() : v; } },
      props: { decorations: function (state) {
        var ids = SELKEY.getState(state); if (!ids || !ids.length) return null;
        var want = {}, decos = []; ids.forEach(function (i) { want[i] = 1; });
        walk(state.doc, function (x) {
          if (want[x.node.attrs.id]) { decos.push(VW.Decoration.node(x.pos, x.pos + x.node.nodeSize, { 'class': 'pm-sel' })); delete want[x.node.attrs.id]; }
        });
        return VW.DecorationSet.create(state.doc, decos);
      } }
    });
  }
  function phText(n, parent, cur) {
    var t = n.type.name;
    if (t === 'paragraph') return cur ? T('Schreib etwas oder tippe „/“ …') : null;
    if (t === 'ttitle') return parent && parent.attrs.level ? T('Toggle-Überschrift') : T('Toggle');
    if (t === 'header') return T('Überschrift {n}', { n: n.attrs.level || 2 });
    if (t === 'callout') return cur ? T('Hinweis') : null;
    if (t === 'qtext') return T('Zitat');
    if (t === 'qcap') return T('Quelle');
    if (t === 'icap') return T('Bildunterschrift');
    if (t === 'ltext') return cur ? T('Liste') : null;
    return null;
  }
  function phPlugin() {
    var S0 = P.pm.state, VW = P.pm.view;
    PHKEY = new S0.PluginKey('hkPlaceholder');
    return new S0.Plugin({
      key: PHKEY,
      props: { decorations: function (state) {
        if (!ed || !ed.isEditable) return null;
        var decos = [], $h = state.selection.$head, curPos = $h.parent.isTextblock ? $h.before() : -1;
        state.doc.descendants(function (n, pos, parent) {
          if (!n.isTextblock) return BLOCKSET[n.type.name] ? !cardish(n) : true;
          if (n.content.size) return false;
          var t = phText(n, parent, pos === curPos);
          if (t) decos.push(VW.Decoration.node(pos, pos + n.nodeSize, { 'class': 'is-empty', 'data-ph': t }));
          return false;
        });
        return decos.length ? VW.DecorationSet.create(state.doc, decos) : null;
      } }
    });
  }

  // =================================================================================================================
  // Umwandeln (Slash, Leiste, Markdown): ID und Farbe bleiben
  // =================================================================================================================
  // Inline-Inhalt eines Blocks (Textbloecke: Inhalt; Zitat: Text; Liste: erster Punkt)
  function inlineOf(n) {
    var t = n.type.name;
    if (TEXTB[t]) return n.content;
    if (t === 'quote') return n.firstChild ? n.firstChild.content : P.pm.model.Fragment.empty;
    if (t === 'list') { var it = n.firstChild; return it && it.firstChild ? it.firstChild.content : P.pm.model.Fragment.empty; }
    if (t === 'toggle') return n.firstChild ? n.firstChild.content : P.pm.model.Fragment.empty;
    return P.pm.model.Fragment.empty;
  }
  // Toggle-Schluessel: toggle = ausklappbar, t1..t3 = Toggle-Ueberschrift
  function toggleLevel(key) { return key === 'toggle' ? 0 : /^t[1-3]$/.test(key) ? +key.slice(1) : -1; }
  // Abstand der Schreibmarke vom Blockanfang nach dem Umwandeln
  function caretOff(key) { return key === 'quote' || toggleLevel(key) >= 0 ? 2 : /^(un)?ordered$|^checklist$/.test(key) ? 3 : 1; }
  // Ziel -> neuer Knoten aus Inline-Inhalt; key: p | h1..h4 | unordered | ordered | checklist | quote | callout
  function makeBlock(key, attrs, content) {
    var s = schema(), a = { id: attrs.id || newId(), style: attrs.style || null };
    content = content || P.pm.model.Fragment.empty;
    if (key === 'p') return s.nodes.paragraph.create(a, content);
    if (/^h[1-4]$/.test(key)) return s.nodes.header.create(Object.assign(a, { level: +key.slice(1) }), content);
    if (key === 'callout') return s.nodes.callout.create(Object.assign(a, { emoji: '💡' }), content);
    if (key === 'quote') return s.nodes.quote.create(Object.assign(a, { alignment: 'left' }), [s.nodes.qtext.create(null, content), s.nodes.qcap.create()]);
    if (key === 'unordered' || key === 'ordered' || key === 'checklist') {
      var item = s.nodes.litem.create({ meta: key === 'checklist' ? { checked: false } : {} }, s.nodes.ltext.create(null, content));
      return s.nodes.list.create(Object.assign(a, { listStyle: key, meta: {} }), item);
    }
    if (key === 'delimiter') return s.nodes.delimiter.create(a);
    var lv = toggleLevel(key);
    if (lv >= 0 && s.nodes.toggle && EDIT.toggle) return s.nodes.toggle.create(Object.assign(a, { open: true, level: lv || null }), s.nodes.ttitle.create(null, content));
    return null;
  }
  var CONV = { paragraph: 1, header: 1, callout: 1, quote: 1, list: 1, toggle: 1 };
  // Block an pos umwandeln; liefert die Position des Inhalts (fuer die Schreibmarke) oder null
  function convertAt(tr, pos, key) {
    var n = tr.doc.nodeAt(pos); if (!n || !CONV[n.type.name]) return null;
    var t = n.type.name;
    if (t === 'list' && (key === 'unordered' || key === 'ordered' || key === 'checklist')) {   // Stilwechsel, Verschachtelung bleibt
      tr.setNodeMarkup(pos, null, Object.assign({}, n.attrs, { listStyle: key }));
      if (key === 'checklist') n.descendants(function (c, p) {
        if (c.type.name === 'litem' && !(c.attrs.meta && 'checked' in c.attrs.meta)) tr.setNodeMarkup(pos + 1 + p, null, Object.assign({}, c.attrs, { meta: Object.assign({}, c.attrs.meta || {}, { checked: false }) }));
      });
      return pos + 3;
    }
    if (t === 'header' && /^h[1-4]$/.test(key)) { tr.setNodeMarkup(pos, null, Object.assign({}, n.attrs, { level: +key.slice(1) })); return pos + 1; }
    if (t === 'toggle') {                            // Toggle <-> Toggle-Ueberschrift: nur die Ebene; mit Inhalt kein Umwandeln
      if (toggleLevel(key) >= 0) { tr.setNodeMarkup(pos, null, Object.assign({}, n.attrs, { level: toggleLevel(key) || null })); return pos + 2; }
      if (n.childCount > 1) return null;
    }
    var nodes = [];
    if (t === 'list') {                              // Liste -> je Punkt ein Block (erster behaelt die ID)
      var first = true;
      n.descendants(function (c) {
        if (c.type.name !== 'ltext') return true;
        nodes.push(makeBlock(key, first ? n.attrs : { style: n.attrs.style }, c.content)); first = false; return false;
      });
    } else {
      nodes.push(makeBlock(key, n.attrs, inlineOf(n)));
      if (t === 'quote' && n.childCount > 1 && n.child(1).content.size) nodes.push(makeBlock('p', { style: n.attrs.style }, n.child(1).content));
    }
    nodes = nodes.filter(Boolean); if (!nodes.length) return null;
    tr.replaceWith(pos, pos + n.nodeSize, nodes);
    return pos + caretOff(key);
  }

  // =================================================================================================================
  // Listen: Enter, Tab/Umschalt+Tab, Rueckschritt (Matrix in tests/browser_tiptap_kern.js)
  // =================================================================================================================
  function liCtx(state) {                           // Schreibmarke in einem Listenpunkt -> {$f, item, itemPos, depth, list, listPos}
    var sel = state.selection, $f = sel.$from;
    if ($f.parent.type.name !== 'ltext') return null;
    var d = $f.depth - 1, item = $f.node(d);
    if (!item || item.type.name !== 'litem') return null;
    var ld = d; while (ld > 0 && $f.node(ld).type.name !== 'list') ld--;
    return { $f: $f, item: item, itemPos: $f.before(d), d: d, list: $f.node(ld), listPos: ld ? $f.before(ld) : 0, ld: ld,
             nested: $f.node(d - 1).type.name === 'litem', empty: !$f.parent.content.size, atStart: $f.parentOffset === 0 };
  }
  function itemMeta(list) { return (list.attrs.listStyle || 'unordered') === 'checklist' ? { checked: false } : {}; }
  function setSel(tr, pos) { var S0 = P.pm.state; tr.setSelection(S0.TextSelection.near(tr.doc.resolve(Math.max(0, Math.min(pos, tr.doc.content.size))))); }
  // Liste an der Stelle des Punktes teilen: [vorher] paragraph(inhalt) [nachher als neue Liste mit neuer ID]
  function exitList(state, dispatch, c) {
    var s = schema(), tr = state.tr, list = c.list, lp = c.listPos;
    var idx = c.$f.index(c.ld);                       // Index des Punktes in der Liste (oberste Ebene)
    var before = [], after = [];
    list.forEach(function (it, o, i) { if (i < idx) before.push(it); else if (i > idx) after.push(it); });
    var kids = []; c.item.forEach(function (x, o, i) { if (i > 0) kids.push(x); });  // Unterpunkte des geleerten Punktes
    after = kids.concat(after);
    var para = s.nodes.paragraph.create({ id: newId() }, c.$f.parent.content);
    var out = [];
    if (before.length) out.push(list.type.create(list.attrs, before));
    out.push(para);
    if (after.length) out.push(list.type.create(Object.assign({}, list.attrs, { id: newId() }), after));
    tr.replaceWith(lp, lp + list.nodeSize, out);
    var pp = lp + (before.length ? out[0].nodeSize : 0);
    if (!before.length) tr.setNodeMarkup(pp, null, Object.assign({}, para.attrs, { id: list.attrs.id, style: list.attrs.style }));
    setSel(tr, pp + 1);
    if (dispatch) dispatch(tr.scrollIntoView());
    return true;
  }
  function listEnter(state, dispatch) {
    var c = liCtx(state); if (!c) return false;
    if (!state.selection.empty) return false;
    if (c.empty && c.nested) return listOutdent(state, dispatch);
    if (c.empty) return exitList(state, dispatch, c);
    var s = schema(), tr = state.tr, $f = c.$f;
    var rest = $f.parent.content.cut($f.parentOffset), keep = $f.parent.content.cut(0, $f.parentOffset);
    var kids = []; c.item.forEach(function (x, o, i) { if (i > 0) kids.push(x); });
    var meta = itemMeta(c.list);
    var atEnd = !rest.size;
    var newItem = s.nodes.litem.create({ meta: meta }, s.nodes.ltext.create(null, rest));
    var item2;
    if (atEnd && kids.length) {                      // Ende eines Punktes mit Unterpunkten: neuer erster Unterpunkt
      item2 = c.item.type.create(c.item.attrs, [s.nodes.ltext.create(null, keep), newItem].concat(kids));
      tr.replaceWith(c.itemPos, c.itemPos + c.item.nodeSize, item2);
      setSel(tr, c.itemPos + 1 + item2.firstChild.nodeSize + 2);
    } else {                                         // sonst Geschwister danach; Unterpunkte bleiben am ersten
      item2 = c.item.type.create(c.item.attrs, [s.nodes.ltext.create(null, keep)].concat(kids));
      tr.replaceWith(c.itemPos, c.itemPos + c.item.nodeSize, [item2, newItem]);
      setSel(tr, c.itemPos + item2.nodeSize + 2);
    }
    if (dispatch) dispatch(tr.scrollIntoView());
    return true;
  }
  function listIndent(state, dispatch) {
    var c = liCtx(state); if (!c) return false;
    var $f = c.$f, idx = $f.index(c.d - 1);           // Index des Punktes im Elternteil (Liste oder Punkt)
    var parent = $f.node(c.d - 1), firstItem = parent.type.name === 'litem' ? 1 : 0;
    if (idx <= firstItem) return true;                // kein vorheriger Punkt: nichts tun (Tab bleibt im Editor)
    var prev = parent.child(idx - 1), prevPos = c.itemPos - prev.nodeSize;
    var tr = state.tr, off = $f.pos - c.itemPos;
    var np = prev.type.create(prev.attrs, prev.content.append(P.pm.model.Fragment.from(c.item)));
    tr.replaceWith(prevPos, c.itemPos + c.item.nodeSize, np);
    setSel(tr, prevPos + prev.nodeSize - 1 + off);
    if (dispatch) dispatch(tr.scrollIntoView());
    return true;
  }
  function listOutdent(state, dispatch) {
    var c = liCtx(state); if (!c) return false;
    if (!c.nested) return true;                       // oberste Ebene: nichts tun
    var $f = c.$f, pd = c.d - 1, parentItem = $f.node(pd), parentPos = $f.before(pd), idx = $f.index(pd);
    var tr = state.tr, off = $f.pos - c.itemPos;
    // folgende Geschwister werden Unterpunkte des ausgerueckten Punktes (Reihenfolge bleibt)
    var keepKids = [], follow = [];
    parentItem.forEach(function (x, o, i) { if (i < idx) keepKids.push(x); else if (i > idx) follow.push(x); });
    var moved = c.item.type.create(c.item.attrs, c.item.content.append(P.pm.model.Fragment.fromArray(follow)));
    var np = parentItem.type.create(parentItem.attrs, keepKids);
    tr.replaceWith(parentPos, parentPos + parentItem.nodeSize, [np, moved]);
    setSel(tr, parentPos + np.nodeSize + off);
    if (dispatch) dispatch(tr.scrollIntoView());
    return true;
  }
  function listBackspace(state, dispatch) {
    var c = liCtx(state); if (!c || !state.selection.empty || !c.atStart) return false;
    if (c.nested) return listOutdent(state, dispatch);
    var idx = c.$f.index(c.ld);
    if (idx === 0) {                                  // erster Punkt: wird Absatz vor der Liste
      var s = schema(), tr = state.tr, list = c.list, lp = c.listPos, rest = [], kids = [];
      c.item.forEach(function (x, o, i) { if (i > 0) kids.push(x); });
      list.forEach(function (it, o, i) { if (i > 0) rest.push(it); });
      rest = kids.concat(rest);
      var para = s.nodes.paragraph.create({ id: rest.length ? newId() : list.attrs.id, style: list.attrs.style }, c.$f.parent.content);
      var out = [para]; if (rest.length) out.push(list.type.create(list.attrs, rest));
      tr.replaceWith(lp, lp + list.nodeSize, out);
      setSel(tr, lp + 1);
      if (dispatch) dispatch(tr.scrollIntoView());
      return true;
    }
    // sonst: Text an den vorherigen sichtbaren Punkt haengen, Unterpunkte wandern mit
    var tr2 = state.tr, prev = c.list.child(idx - 1), prevPos = c.itemPos - prev.nodeSize;
    var lastText = null, ltPos = 0;
    prev.descendants(function (x, p) { if (x.type.name === 'ltext') { lastText = x; ltPos = prevPos + 1 + p; } });
    if (!lastText) return false;
    var kids2 = []; c.item.forEach(function (x, o, i) { if (i > 0) kids2.push(x); });
    var joinAt = ltPos + 1 + lastText.content.size;
    tr2.delete(c.itemPos, c.itemPos + c.item.nodeSize);
    tr2.insert(joinAt, c.$f.parent.content);
    if (kids2.length) {
      var p2 = tr2.doc.nodeAt(prevPos);
      tr2.replaceWith(prevPos, prevPos + p2.nodeSize, p2.type.create(p2.attrs, p2.content.append(P.pm.model.Fragment.fromArray(kids2))));
    }
    setSel(tr2, joinAt);
    if (dispatch) dispatch(tr2.scrollIntoView());
    return true;
  }

  // =================================================================================================================
  // Saga-Erweiterung: Tasten, Eingaberegeln (E5), Plugins
  // =================================================================================================================
  function toggleMark(name) { return function () { return ed.chain().focus().toggleMark(name).run(); }; }
  function textBlockCtx(state) {                    // Absatz auf oberster Ebene (fuer Markdown-Blockregeln)
    var $f = state.selection.$from, n = $f.parent;
    if (n.type.name !== 'paragraph') return null;
    return { node: n, pos: $f.before() };
  }
  function blockRule(find, key, opts) {
    return new P.core.InputRule({ find: find, handler: function (a) {
      var st = a.state, tr = st.tr, c = textBlockCtx(st); if (!c) return null;
      tr.delete(a.range.from, a.range.to);
      var n = tr.doc.nodeAt(c.pos);
      if (key === 'delimiter') {
        var s = schema(), dl = s.nodes.delimiter.create({ id: n.attrs.id, style: null }), np = s.nodes.paragraph.create({ id: newId() }, n.content);
        tr.replaceWith(c.pos, c.pos + n.nodeSize, [dl, np]);
        setSel(tr, c.pos + dl.nodeSize + 1);
        return;
      }
      var nb = makeBlock(key, n.attrs, n.content); if (!nb) return null;
      tr.replaceWith(c.pos, c.pos + n.nodeSize, nb);
      setSel(tr, c.pos + caretOff(key));
    } });
  }
  function SagaKern() {
    var core = P.core;
    return core.Extension.create({
      name: 'sagaKern', priority: 1000,
      addKeyboardShortcuts: function () {
        var self = this;
        function run(fn) { return function () { return fn(self.editor.state, self.editor.view.dispatch); }; }
        // Auswahl aus dem DOM uebernehmen, bevor eine Taste ausgewertet wird (selectionchange kommt nach Klick/Pfeil erst spaeter)
        function sync(fn) { return function () { var v = self.editor.view; if (v.domObserver && v.domObserver.flush) v.domObserver.flush(); return fn.apply(this, arguments); }; }
        var keys = {
          'Enter': function () {
            if (menuActive()) return false;                               // Vorschlagsmenue offen: dort waehlen
            if (run(listEnter)()) return true;
            var st = self.editor.state, $f = st.selection.$from, t = $f.parent.type.name;
            if (t === 'qtext') {                      // Zitat: Enter = neue Zeile; Enter in leerer letzter Zeile verlaesst das Zitat
              var lc = $f.parent.lastChild;
              if (st.selection.empty && $f.parentOffset === $f.parent.content.size && lc && lc.type.name === 'hardBreak') {
                var qb = blockAt($f.pos), tr = st.tr; if (!qb) return false;
                tr.delete($f.pos - 1, $f.pos);
                var at0 = tr.mapping.map(qb.pos + qb.node.nodeSize);
                tr.insert(at0, schema().nodes.paragraph.create({ id: newId() })); setSel(tr, at0 + 1);
                self.editor.view.dispatch(tr.scrollIntoView()); return true;
              }
              return self.editor.chain().insertContent({ type: 'hardBreak' }).run();
            }
            if (t === 'qcap' && st.selection.empty) {                     // Quelle: neuer Absatz unter dem Zitat
              var q = blockAt($f.pos); if (!q) return false;
              var tr2 = st.tr, np = schema().nodes.paragraph.create({ id: newId() }), at = q.pos + q.node.nodeSize;
              tr2.insert(at, np); setSel(tr2, at + 1); self.editor.view.dispatch(tr2.scrollIntoView()); return true;
            }
            if (t === 'callout' && st.selection.empty && $f.parentOffset === $f.parent.content.size) {
              var tr3 = st.tr, at3 = $f.after(), np3 = schema().nodes.paragraph.create({ id: newId() });
              tr3.insert(at3, np3); setSel(tr3, at3 + 1); self.editor.view.dispatch(tr3.scrollIntoView()); return true;
            }
            return false;
          },
          'ArrowDown': function () {              // am Ende des letzten Blocks (Zitat, Hinweis, Ueberschrift ...): Absatz darunter
            var st = self.editor.state, sel = st.selection, $f = sel.$from;
            if (!sel.empty || !$f.parent.isTextblock || $f.parentOffset !== $f.parent.content.size) return false;
            var b0 = blockAt($f.pos); if (!b0 || b0.depth !== 1 || b0.node.type.name === 'paragraph' || b0.node.type.name === 'list') return false;
            if (b0.pos + b0.node.nodeSize !== st.doc.content.size) return false;
            if (b0.node.type.name === 'quote' && $f.parent.type.name === 'qtext' && b0.node.child(1).content.size) return false;
            var tr = st.tr, at = st.doc.content.size; tr.insert(at, schema().nodes.paragraph.create({ id: newId() })); setSel(tr, at + 1);
            self.editor.view.dispatch(tr.scrollIntoView()); return true;
          },
          'Shift-Enter': function () { return self.editor.chain().focus().insertContent({ type: 'hardBreak' }).run(); },
          'Tab': function () { if (menuActive()) return false; if (run(listIndent)()) return true; return false; },
          'Shift-Tab': function () { if (run(listOutdent)()) return true; return false; },
          'Backspace': function () {
            if (run(listBackspace)()) return true;
            return selectNeighbourCard(self.editor.state, self.editor.view.dispatch, -1);
          },
          'Delete': function () { return selectNeighbourCard(self.editor.state, self.editor.view.dispatch, 1); },
          'Mod-b': toggleMark('b'), 'Mod-i': toggleMark('i'), 'Mod-u': toggleMark('u'),
          'Mod-Shift-s': toggleMark('s'), 'Mod-Shift-x': toggleMark('s'), 'Mod-e': toggleMark('icode'), 'Mod-Shift-h': toggleMark('mark')
        };
        // Erweiterungen zuerst (pm-struktur.js: Toggle-Titel, Mehrfachauswahl ...), dann der Kern
        MODS.forEach(function (m) {
          var mk = m.keys ? m.keys(self.editor) : {};
          Object.keys(mk).forEach(function (k) {
            var base = keys[k], own = mk[k];
            keys[k] = function () { return own.apply(this, arguments) || (base ? base.apply(this, arguments) : false); };
          });
        });
        Object.keys(keys).forEach(function (k) { keys[k] = sync(keys[k]); });
        return keys;
      },
      addInputRules: function () {
        var core2 = P.core, s = this.editor.schema;
        var rules = [
          blockRule(/^####\s$/, 'h4'), blockRule(/^###\s$/, 'h3'), blockRule(/^##\s$/, 'h2'), blockRule(/^#\s$/, 'h1'),
          blockRule(/^[-*]\s$/, 'unordered'), blockRule(/^1[.)]\s$/, 'ordered'), blockRule(/^\[\s?\]\s$/, 'checklist'),
          blockRule(/^>\s$/, EDIT.toggle ? 'toggle' : 'quote'),   // Notion-Belegung (E5): "> " Toggle, '" ' Zitat
          blockRule(/^["„“]\s$/, 'quote'), blockRule(/^---$/, 'delimiter'),
          core2.markInputRule({ find: /(?:^|\s)(\*\*(?!\s+\*\*)((?:[^*]+))\*\*(?!\s+\*\*))$/, type: s.marks.b }),
          core2.markInputRule({ find: /(?:^|\s)(\*(?!\s+\*)((?:[^*]+))\*(?!\s+\*))$/, type: s.marks.i }),
          core2.markInputRule({ find: /(?:^|\s)(_(?!\s+_)((?:[^_]+))_(?!\s+_))$/, type: s.marks.i }),
          core2.markInputRule({ find: /(?:^|\s)(~~(?!\s+~~)((?:[^~]+))~~(?!\s+~~))$/, type: s.marks.s }),
          core2.markInputRule({ find: /(?:^|\s)(==(?!\s+==)((?:[^=]+))==(?!\s+==))$/, type: s.marks.mark }),
          core2.markInputRule({ find: /(^|[^`])`([^`]+)`(?!`)$/, type: s.marks.icode }),
          new core2.InputRule({ find: /(?:^|\s)(\$([^$\s][^$]*)\$)$/, handler: function (a) {
            var m = a.match, start = a.range.from + m[0].indexOf(m[1]);
            a.state.tr.replaceWith(start, a.range.to, s.nodes.math_inline.create({ tex: m[2] }));
          } })
        ];
        return rules;
      },
      addProseMirrorPlugins: function () {
        var ed2 = this.editor, extra = [], early = [];
        // early: vor den Plugins der anderen Erweiterungen (T4a: Einfuegen mit Dateiverweisen vor dem Block-Einfuegen von T3)
        MODS.forEach(function (m) { if (m.plugins) (m.early ? early : extra).push.apply(m.early ? early : extra, m.plugins(ed2) || []); });
        return [guardPlugin(), selPlugin(), phPlugin(), slashPlugin(ed2), atPlugin(ed2), refPlugin(ed2), emojiPlugin(ed2), bubbleKeysPlugin()].concat(early, extra);
      }
    });
  }
  // Rueckschritt am Anfang / Entf am Ende eines Textblocks neben einer Karte oder Trennlinie: diese auswaehlen
  function selectNeighbourCard(state, dispatch, dir) {
    var sel = state.selection, S0 = P.pm.state; if (!sel.empty) return false;
    var $f = sel.$from; if (!$f.parent.isTextblock) return false;
    if (dir < 0 ? $f.parentOffset !== 0 : $f.parentOffset !== $f.parent.content.size) return false;
    var b = blockAt($f.pos); if (!b || b.depth !== $f.depth) return false;
    var $b = state.doc.resolve(b.pos), idx = $b.index(), parent = $b.parent;
    var nb = dir < 0 ? (idx > 0 ? parent.child(idx - 1) : null) : (idx + 1 < parent.childCount ? parent.child(idx + 1) : null);
    if (!nb || EDIT[nb.type.name] && nb.type.name !== 'delimiter') return false;
    var np = dir < 0 ? b.pos - nb.nodeSize : b.pos + b.node.nodeSize;
    if (dir < 0 && !$f.parent.content.size && b.node.type.name === 'paragraph') {   // leerer Absatz nach Karte: Absatz weg, Karte waehlen
      var tr0 = state.tr.delete(b.pos, b.pos + b.node.nodeSize);
      tr0.setSelection(S0.NodeSelection.create(tr0.doc, np));
      if (dispatch) dispatch(tr0); return true;
    }
    if (dispatch) dispatch(state.tr.setSelection(S0.NodeSelection.create(state.doc, np)));
    return true;
  }

  // =================================================================================================================
  // Vorschlagsmenues (Suggestion): "/" Slash (E1), "@" Personen/Datum/Seiten, "[[" Seitenverweis, ":" Emoji
  // =================================================================================================================
  function menuActive() { return !![].some.call(document.querySelectorAll('.pm-pop'), function (b) { return !b.hidden && b.firstChild; }); }
  function popBox(cls) { var b = el('div', 'hk-emo pm-pop ' + (cls || '')); b.setAttribute('role', 'listbox'); b.hidden = true; document.body.appendChild(b); return b; }
  function place(box, rect) {
    if (!rect) return;
    box.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - box.offsetWidth - 8)) + 'px';
    var below = rect.bottom + 6, h = box.offsetHeight;
    box.style.top = (below + h > window.innerHeight - 8 && rect.top - 6 - h > 8 ? rect.top - 6 - h : below) + 'px';
  }
  // Grundgeruest: items(q) -> [{group, ic, t, sub, kbd, ...}] (oder Promise), pick(item, range)
  function menuPlugin(editor, o) {
    var box = null, items = [], idx = 0, cmd = null, rect = null;
    function draw() {
      box.textContent = ''; var last = null;
      items.forEach(function (it, i) {
        if (it.group && it.group !== last) { box.appendChild(el('div', 'hk-at-g', it.group)); last = it.group; }
        var b = el('button', 'hk-emo-i' + (i === idx ? ' on' : '')); b.type = 'button'; b.setAttribute('role', 'option');
        var ic = el('span', 'hk-emo-e');
        if (/^<svg[\s>]/.test(it.ic || '')) ic.innerHTML = it.ic;           // Werkzeug-Symbol (eigener Code, toolbox.icon)
        else ic.textContent = it.ic || '';
        b.appendChild(ic);
        var tx = el('span', 'pm-mi-t'); tx.appendChild(el('span', 'hk-at-t', it.t));
        if (it.desc) tx.appendChild(el('span', 'pm-mi-d', it.desc));
        b.appendChild(tx);
        if (it.sub) b.appendChild(el('span', 'hk-emo-k', it.sub));
        if (it.kbd) b.appendChild(el('kbd', 'pm-mi-k', it.kbd));
        b.addEventListener('mousedown', function (e) { e.preventDefault(); if (cmd) cmd(it); });
        box.appendChild(b);
        if (i === idx) setTimeout(function () { if (b.isConnected) b.scrollIntoView({ block: 'nearest' }); }, 0);
      });
      box.hidden = !items.length;
      place(box, rect);
    }
    return P.Suggestion({
      editor: editor, pluginKey: new P.pm.state.PluginKey(o.key), char: o.char, allowSpaces: !!o.spaces,
      allowedPrefixes: o.prefixes || [' '], startOfLine: false, minQueryLength: o.min || 0,
      allow: function (a) {
        if (!editor.isEditable) return false;
        var $p = a.state.doc.resolve(a.range.from), t = $p.parent.type.name;
        return !!({ paragraph: 1, header: 1, callout: 1, qtext: 1, qcap: 1, ltext: 1 })[t] && (!o.allow || o.allow(a, $p));
      },
      items: function (a) { return o.items(a.query, a); },
      command: function (a) { o.pick(a.props, a.range, a.editor); },
      render: function () {
        return {
          onStart: function (pr) { if (!box) box = popBox(o.cls); items = pr.items || []; idx = 0; cmd = pr.command; rect = pr.clientRect && pr.clientRect(); draw(); },
          onUpdate: function (pr) { items = pr.items || []; idx = 0; cmd = pr.command; rect = pr.clientRect && pr.clientRect(); draw(); },
          onKeyDown: function (pr) {
            var e = pr.event; if (!box || box.hidden || !items.length) return false;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { idx = (idx + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length; draw(); return true; }
            if (e.key === 'Enter' || e.key === 'Tab') { if (cmd) cmd(items[idx]); return true; }
            if (e.key === 'Escape') { box.hidden = true; items = []; return true; }       // Esc laesst den Text stehen
            return false;
          },
          onExit: function () { if (box) { box.hidden = true; } items = []; }
        };
      }
    });
  }

  // --- Slash-Menue (E1): Registry mit Gruppen ---------------------------------------------------------------------
  var MOD = isMac ? '⌘' : T('Strg') + '+', ALT = isMac ? '⌥' : 'Alt+';
  var SLASH = [];
  function slashReg(it) { SLASH.push(it); SLASH.sort(function (a, b) { return (a.order || 50) - (b.order || 50); }); }
  var G_BASIC = T('Grundblöcke'), G_SAGA = 'Saga';
  [
    ['p', T('Text'), '¶', T('Einfacher Absatz'), 'text absatz paragraph', MOD + ALT + '0'],
    ['h1', T('Überschrift 1'), 'H1', T('Große Überschrift'), 'heading titel h1', '#'],
    ['h2', T('Überschrift 2'), 'H2', T('Mittlere Überschrift'), 'heading h2', '##'],
    ['h3', T('Überschrift 3'), 'H3', T('Kleine Überschrift'), 'heading h3', '###'],
    ['h4', T('Überschrift 4'), 'H4', T('Kleinste Überschrift'), 'heading h4', '####'],
    ['unordered', T('Aufzählung'), '•', T('Liste mit Punkten'), 'liste bullet punkte list', '-'],
    ['ordered', T('Nummerierte Liste'), '1.', T('Liste mit Nummern'), 'nummer liste ordered', '1.'],
    ['checklist', T('Checkliste'), '☑', T('Aufgaben zum Abhaken'), 'todo aufgabe haken checkbox', '[]'],
    ['quote', T('Zitat'), '❝', T('Zitat mit Quelle'), 'zitat quote', '>'],
    ['callout', T('Hinweis (Callout)'), '💡', T('Hervorgehobener Kasten mit Symbol'), 'callout hinweis box', ''],
    ['delimiter', T('Trennlinie'), '—', T('Linie zwischen Abschnitten'), 'trenner linie hr divider', '---']
  ].forEach(function (x, i) { slashReg({ key: x[0], group: G_BASIC, t: x[1], ic: x[2], desc: x[3], kw: x[4], kbd: x[5], order: 10 + i }); });
  [
    ['@', T('Person erwähnen'), '@', T('Jemanden auf der Seite erwähnen'), 'erwaehnen person mention', '@'],
    ['[[', T('Seitenverweis'), '🔗', T('Auf eine andere Seite verlinken'), 'verweis link seite page', '[['],
    ['date', T('Datum oder Erinnerung'), '📅', T('Datum einfügen, Erinnerung legt eine Aufgabe an'), 'datum date erinnerung termin', '@'],
    ['math', T('Formel im Text'), '∑', T('TeX-Formel in der Zeile'), 'formel mathe math tex', '$…$']
  ].forEach(function (x, i) { slashReg({ key: x[0], group: G_SAGA, t: x[1], ic: x[2], desc: x[3], kw: x[4], kbd: x[5], order: 80 + i }); });
  function norm(s) { return String(s || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss'); }
  // Werkzeuge aus static/pm-tools*.js (HKTools.slash(): [{type, title, icon, data}])
  var TOOL_GROUP = { database: T('Datenbank'), tasks: T('Datenbank'), calendar: T('Datenbank') };
  function toolSlash() {
    if (!window.HKTools || typeof HKTools.slash !== 'function') return [];
    try {
      return (HKTools.slash() || []).map(function (x, i) {
        return { key: 'tool', group: TOOL_GROUP[x.type] || G_SAGA, t: x.title, ic: x.icon || '□', kw: x.type, order: 60 + i,
                 block: { type: x.type, data: x.data || {} } };
      });
    } catch (e) { return []; }
  }
  function slashItems(q) {
    q = String(q || '').replace(/ /g, ' ');
    var nq = norm(q.trim()), out = [];
    var col = K.slashColor ? K.slashColor(q) : null;
    if (col) out.push({ key: 'color', group: T('Farbe'), ic: 'A', t: (K.colorNames[col.c] || col.c) + (col.bg ? ' (' + T('Hintergrund') + ')' : ''), desc: T('Block einfärben'), st: col });
    var spaced = /\s/.test(q.trim());             // mit Leerzeichen ("2 Spalten", "Toggle-Überschrift 3"): nur Titel-Treffer
    if (spaced && col) return out;
    var hits = SLASH.concat(toolSlash()).filter(function (it) { try { return !it.when || it.when(); } catch (e) { return false; } }).map(function (it) {
      var t = norm(it.t), r = !nq ? 1 : t.indexOf(nq) === 0 ? 0 : t.indexOf(nq) > 0 ? 1 : norm(it.kw || '').indexOf(nq) >= 0 ? 2 : 9;
      return { it: it, r: r };
    }).filter(function (h) { return h.r < (spaced ? 2 : 9); });
    if (nq) hits.sort(function (a, b) { return a.r - b.r || (a.it.order || 50) - (b.it.order || 50); });
    // Gruppen zusammenhalten (Reihenfolge der besten Treffer je Gruppe)
    var groups = [], by = {};
    hits.forEach(function (h) { var g = h.it.group || ''; if (!by[g]) { by[g] = []; groups.push(g); } by[g].push(h.it); });
    groups.forEach(function (g) { out = out.concat(by[g]); });
    return out.slice(0, 40);
  }
  function slashPick(it, range, editor) {
    var st = editor.state, tr = st.tr, $f = st.doc.resolve(range.from), tb = $f.parent;
    tr.delete(range.from, range.to);
    var b = blockAt(range.from);
    var onlySlash = tb.content.size === range.to - range.from;          // Textblock bestand nur aus "/…"
    var top = b && b.node.type.name === 'paragraph' && $f.parent === b.node;
    if (it.key === 'color') {
      if (b) tr.setNodeMarkup(b.pos, null, Object.assign({}, b.node.attrs, { style: it.st }));
      editor.view.dispatch(tr); return;
    }
    if (it.key === '@' || it.key === 'date') { tr.insertText('@', range.from); editor.view.dispatch(tr); return; }
    if (it.key === '[[') { tr.insertText('[[', range.from); editor.view.dispatch(tr); return; }
    if (it.key === 'math') {
      tr.insert(range.from, schema().nodes.math_inline.create({ tex: '' }));
      editor.view.dispatch(tr);
      setTimeout(function () { openMath(range.from); }, 0);
      return;
    }
    if (it.run) { editor.view.dispatch(tr); it.run(editor, range.from); return; }
    var nb = it.block ? nodeOf({ type: it.block.type, data: JSON.parse(JSON.stringify(it.block.data || {})), id: newId() }) : null;
    if (b && b.node.type.name === 'list' && /^(un)?ordered$|^checklist$/.test(it.key)) {   // in einer Liste: nur den Stil wechseln
      var keepSel = range.from;
      convertAt(tr, b.pos, it.key); setSel(tr, tr.mapping.map(keepSel));
      editor.view.dispatch(tr); return;
    }
    if (onlySlash && top && !it.block) {             // leerer Absatz: umwandeln (ID bleibt)
      if (it.key === 'delimiter') {
        var pn = tr.doc.nodeAt(b.pos), dl = schema().nodes.delimiter.create({ id: pn.attrs.id });
        var after = b.pos + pn.nodeSize, nx = tr.doc.resolve(after).nodeAfter;
        tr.replaceWith(b.pos, after, dl);
        if (!nx || nx.type.name !== 'paragraph') { tr.insert(b.pos + 1, schema().nodes.paragraph.create({ id: newId() })); }
        setSel(tr, b.pos + 2);
      } else {
        var cp = convertAt(tr, b.pos, it.key); if (cp != null) setSel(tr, cp);
      }
      editor.view.dispatch(tr.scrollIntoView()); return;
    }
    if (onlySlash && top && nb) { tr.replaceWith(b.pos, b.pos + tr.doc.nodeAt(b.pos).nodeSize, nb); editor.view.dispatch(tr); return; }
    // sonst: neuer Block nach dem aktuellen (oberste Ebene des Textblocks)
    var host = b; if (!host) return;
    var at = host.pos + tr.doc.nodeAt(host.pos).nodeSize;
    var neu = nb || (it.key === 'delimiter' ? schema().nodes.delimiter.create({ id: newId() }) : makeBlock(it.key, {}, null));
    if (!neu) { editor.view.dispatch(tr); return; }
    tr.insert(at, neu);
    if (it.key === 'delimiter') { var p2 = schema().nodes.paragraph.create({ id: newId() }); tr.insert(at + neu.nodeSize, p2); setSel(tr, at + neu.nodeSize + 1); }
    else setSel(tr, at + caretOff(it.key));
    editor.view.dispatch(tr.scrollIntoView());
  }
  function slashPlugin(editor) {
    return menuPlugin(editor, { key: 'hkSlash', char: '/', spaces: true, cls: 'pm-slash', items: slashItems, pick: slashPick,
      allow: function (a, $p) { var q = a.state.doc.textBetween(a.range.from, a.range.to); return q.length <= 41 && !/\s\s/.test(q); } });
  }

  // --- "@": Personen (Server), Datum, Erinnerung, Seiten (Daten wie static/keys.js) ---------------------------------
  function atItems(q) {
    var opts = window.HKKeys && HKKeys.atOptions ? HKKeys.atOptions(q) : [];
    if (/^\s/.test(q) || /\s{2,}/.test(q) || q.length > 30) return [];
    if (!window.HKMention || K.readOnly()) return opts;
    return HKMention.options(q).then(function (pp) { return pp.concat(opts); }, function () { return opts; });
  }
  function insertInline(editor, range, node) {
    var tr = editor.state.tr.replaceWith(range.from, range.to, node);
    tr.insertText(' ', range.from + node.nodeSize);
    setSel(tr, range.from + node.nodeSize + 1);
    editor.view.dispatch(tr.scrollIntoView()); editor.view.focus();
  }
  function atPick(it, range, editor) {
    var s = schema();
    if (it.person) {
      HKMention.pick(it, range, function () { insertInline(editor, range, s.nodes.mention.create({ u: it.person.ref })); });
      return;
    }
    if (it.page) { insertInline(editor, range, s.nodes.page.create({ id: it.page.id })); return; }
    if (!it.d) return;
    var label = (it.remind ? '⏰ ' : '') + HKKeys.dateText(it.d);
    if (it.remind) {
      var b = blockAt(range.from), cur = K.cur(), txt = b ? b.node.textContent.replace(/@\S*$/, '').trim() : '';
      var text = (txt || (cur && cur.title) || T('Erinnerung')); if (txt && cur && cur.title) text += ' (' + cur.title + ')';
      HK.api('POST', '/api/tasks', { text: text.slice(0, 300), due: it.d.date }).then(function () {
        HK.toast(T('Erinnerung als Aufgabe für {date} angelegt', { date: HKKeys.dateText(it.d) }));
      }).catch(function (err) { HK.toast(T('Aufgabe nicht angelegt: {err}', { err: err.message })); });
    }
    insertInline(editor, range, s.nodes.date.create({ dt: it.d.date + (it.d.time ? 'T' + it.d.time : ''), remind: !!it.remind, label: label }));
  }
  function atPlugin(editor) { return menuPlugin(editor, { key: 'hkAt', char: '@', spaces: true, cls: 'hk-at', items: atItems, pick: atPick }); }

  // --- "[[": Seitenverweis (Seiten, Dienste, neue Seite) ------------------------------------------------------------
  function refItems(q) {
    var nq = norm(q.trim()), out = [], cur = K.cur();
    var pages = K.pages().concat(K.shared()).filter(function (p) { return (!cur || p.id !== cur.id) && (!nq || norm(K.label(p)).indexOf(nq) >= 0); }).slice(0, 8);
    pages.forEach(function (p) { out.push({ group: T('Seiten'), ic: K.ti(p.icon) || '📄', t: K.label(p), sub: K.ancestors(p.id).map(K.label).join(' / '), page: p }); });
    (K.services ? K.services() : []).filter(function (s) { return !nq || norm(s.name + ' ' + s.host).indexOf(nq) >= 0; }).slice(0, 5)
      .forEach(function (s) { out.push({ group: T('Dienste'), ic: '↗', t: s.name, sub: s.host, svc: s }); });
    if (q.trim()) out.push({ group: T('Neu'), ic: '+', t: T('+ Neue Seite „{name}“', { name: q.trim() }), neu: q.trim() });
    return out;
  }
  function refPick(it, range, editor) {
    var s = schema();
    if (it.page) { insertInline(editor, range, s.nodes.page.create({ id: it.page.id })); return; }
    if (it.svc) {
      var tr = editor.state.tr.insertText(it.svc.name, range.from, range.to);
      tr.addMark(range.from, range.from + it.svc.name.length, s.marks.link.create({ href: it.svc.url }));
      tr.removeStoredMark(s.marks.link); tr.insertText(' ', range.from + it.svc.name.length);
      editor.view.dispatch(tr); return;
    }
    if (it.neu) {
      var cur = K.cur();
      HK.api('POST', '/api/notes', { title: it.neu, parent_id: cur ? cur.id : null }).then(function (d) {
        return K.reloadList().then(function () { insertInline(editor, range, s.nodes.page.create({ id: d.id })); });
      }).catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
    }
  }
  function refPlugin(editor) { return menuPlugin(editor, { key: 'hkRef', char: '[[', spaces: true, prefixes: null, items: refItems, pick: refPick }); }

  // --- ":" Emoji ----------------------------------------------------------------------------------------------------
  function emojiPlugin(editor) {
    return menuPlugin(editor, { key: 'hkEmoji', char: ':', min: 2, prefixes: [' ', '('],
      items: function (q) {
        if (!/^[a-zäöüß0-9_+-]{2,20}$/i.test(q) || !window.HKKeys || !HKKeys.emoji) return [];
        return HKKeys.emoji(q).map(function (x) { return { ic: x.e, t: x.k.split(' ')[0], e: x.e }; });
      },
      pick: function (it, range, ed2) { ed2.view.dispatch(ed2.state.tr.insertText(it.e, range.from, range.to)); } });
  }

  // =================================================================================================================
  // Formatierungsleiste (E8): Umwandeln · B I U S · Code · Link · Farbe · Kommentar · KI (+ Formel)
  // =================================================================================================================
  var bubble = null;
  var CONV_KEYS = [['p', T('Text'), '¶'], ['h1', T('Überschrift 1'), 'H1'], ['h2', T('Überschrift 2'), 'H2'], ['h3', T('Überschrift 3'), 'H3'],
    ['h4', T('Überschrift 4'), 'H4'], ['unordered', T('Aufzählung'), '•'], ['ordered', T('Nummerierte Liste'), '1.'],
    ['checklist', T('Checkliste'), '☑'], ['quote', T('Zitat'), '❝'], ['callout', T('Hinweis (Callout)'), '💡']];
  function bubbleEl() {
    var b = el('div', 'pm-bubble'); b.setAttribute('role', 'toolbar'); b.setAttribute('aria-label', T('Formatierung'));
    function btn(a, txt, label, cls) {
      var x = el('button', 'pm-bb' + (cls ? ' ' + cls : ''), txt); x.type = 'button'; x.dataset.a = a; x.title = label; x.setAttribute('aria-label', label);
      x.addEventListener('mousedown', function (e) { e.preventDefault(); });
      x.addEventListener('click', function (e) { e.preventDefault(); bubbleAct(a, x); });
      b.appendChild(x); return x;
    }
    btn('conv', T('Umwandeln') + ' ▾', T('Umwandeln in'), 'pm-bb-w');
    b.appendChild(el('span', 'pm-bsep'));
    btn('b', 'B', T('Fett') + ' (' + MOD + 'B)', 'pm-bb-b'); btn('i', 'I', T('Kursiv') + ' (' + MOD + 'I)', 'pm-bb-i');
    btn('u', 'U', T('Unterstrichen') + ' (' + MOD + 'U)', 'pm-bb-u'); btn('s', 'S', T('Durchgestrichen'), 'pm-bb-s');
    btn('icode', '</>', T('Code'));
    btn('math', '∑', T('Formel'));
    btn('link', '🔗', T('Link'));
    btn('color', 'A ▾', T('Farbe'), 'pm-bb-col');
    b.appendChild(el('span', 'pm-bsep'));
    btn('comment', '💬', T('Kommentieren'));
    if (window.HKAI && HKAI.state && HKAI.state()) btn('ai', '✦', T('KI'));
    var form = el('div', 'pm-blink'); form.hidden = true;
    var inp = el('input', 'hk-in'); inp.type = 'url'; inp.placeholder = 'https://…'; inp.setAttribute('aria-label', T('Link-Adresse'));
    form.appendChild(inp); b.appendChild(form);
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); setLink(inp.value); form.hidden = true; }
      else if (e.key === 'Escape') { e.preventDefault(); form.hidden = true; ed.commands.focus(); }
    });
    b._link = form; b._inp = inp;
    return b;
  }
  function selBlocks(state) {                       // Bloecke (Textbloecke der obersten Ebene) in der Auswahl
    var out = [], seen = {};
    state.doc.nodesBetween(state.selection.from, state.selection.to, function (n, pos) {
      if (BLOCKSET[n.type.name]) { if (CONV[n.type.name] && !seen[pos]) { seen[pos] = 1; out.push(pos); } return false; }
      return true;
    });
    return out;
  }
  function bubbleAct(a, btnEl) {
    if (!ed || ro()) return;
    var st = ed.state;
    if (a === 'b' || a === 'i' || a === 'u' || a === 's' || a === 'icode') { ed.chain().focus().toggleMark(a).run(); refreshBubble(); return; }
    if (a === 'math') {
      var txt = st.doc.textBetween(st.selection.from, st.selection.to, ' ');
      if (!txt.trim()) return;
      var from = st.selection.from;
      ed.view.dispatch(st.tr.replaceWith(from, st.selection.to, schema().nodes.math_inline.create({ tex: txt })));
      setTimeout(function () { openMath(from); }, 0); return;
    }
    if (a === 'link') {
      var lm = schema().marks.link, cur = null;
      st.doc.nodesBetween(st.selection.from, st.selection.to, function (n) { n.marks.forEach(function (m) { if (m.type === lm) cur = m.attrs.href; }); });
      bubble._link.hidden = false; bubble._inp.value = cur || ''; bubble._inp.focus(); bubble._inp.select(); return;
    }
    if (a === 'color') { colorMenu(btnEl); return; }
    if (a === 'conv') {
      HK.openMenu(btnEl, function (m) {
        CONV_KEYS.forEach(function (k) { m.appendChild(HK.menuItem(k[1], function () { convertSel(k[0]); }, { icon: k[2] })); });
      });
      return;
    }
    if (a === 'comment') { var b = blockAt(st.selection.from); if (b && window.HKComments) HKComments.openBlock(topId(b)); return; }
    if (a === 'ai') { if (window.HKAI) HKAI.open('page'); }
  }
  function topId(b) { return b.node.attrs.id; }
  function convertSel(key) {
    var tr = ed.state.tr, list = selBlocks(ed.state), caret = null;
    list.reverse().forEach(function (pos) { var m = tr.mapping.map(pos); var c = convertAt(tr, m, key); if (caret == null) caret = c; });
    if (caret != null) setSel(tr, caret);
    ed.view.dispatch(tr.scrollIntoView()); ed.commands.focus();
  }
  function safeHref(h) {
    h = String(h || '').trim(); if (!h) return '';
    if (/^(https?:\/\/|mailto:|\/|#)/i.test(h)) return h;
    if (/^[a-z][a-z0-9+.-]*:/i.test(h)) return '';                   // andere Schemata (javascript: ...) nie
    return 'https://' + h;
  }
  function setLink(v) {
    var h = safeHref(v), ch = ed.chain().focus().extendMarkRange('link');
    if (!h) ch.unsetMark('link').run(); else ch.setMark('link', { href: h }).run();
  }
  function colorMenu(anchor) {
    var LAST = 'hk-last-color';
    HK.openMenu(anchor, function (m) {
      function item(label, cls, fn) { var it = HK.menuItem(label, fn, { icon: 'A' }); if (cls) it.querySelector('.mi-ic').classList.add(cls); m.appendChild(it); }
      m.appendChild(HK.menuLabel(T('Text')));
      item(T('Standard'), null, function () { applyColor(null, false); });
      COLORS.forEach(function (c) { item(K.colorNames[c], 'hk-c-' + c, function () { applyColor(c, false); try { localStorage.setItem(LAST, JSON.stringify({ c: c })); } catch (e) { /* privat */ } }); });
      m.appendChild(HK.menuLabel(T('Hintergrund')));
      item(T('Kein Hintergrund'), null, function () { applyColor(null, true); });
      item(T('Markieren'), 'cdx-marker', function () { ed.chain().focus().unsetMark('bg').setMark('mark').run(); });
      COLORS.forEach(function (c) { item(T('{farbe} (Hintergrund)', { farbe: K.colorNames[c] }), 'hk-bg-' + c, function () { applyColor(c, true); }); });
    });
  }
  function applyColor(c, bg) {
    var ch = ed.chain().focus();
    if (bg) { ch = ch.unsetMark('bg').unsetMark('mark'); if (c) ch = ch.setMark('bg', { c: c }); }
    else { ch = ch.unsetMark('color'); if (c) ch = ch.setMark('color', { c: c }); }
    ch.run();
  }
  function refreshBubble() {
    if (!bubble || !ed) return;
    ['b', 'i', 'u', 's', 'icode'].forEach(function (k) { var x = bubble.querySelector('[data-a="' + k + '"]'); if (x) x.classList.toggle('on', ed.isActive(k)); });
    var l = bubble.querySelector('[data-a="link"]'); if (l) l.classList.toggle('on', ed.isActive('link'));
  }
  function bubbleKeysPlugin() {                      // Leiste: Link-Eingabe schliessen, wenn sich die Auswahl aendert
    var S0 = P.pm.state;
    return new S0.Plugin({ view: function () { return { update: function (v, prev) {
      if (bubble && !prev.selection.eq(v.state.selection) && document.activeElement !== bubble._inp) bubble._link.hidden = true;
      refreshBubble();
    } }; } });
  }
  function bubbleExt() {
    bubble = bubbleEl();
    return P.BubbleMenu.configure({
      element: bubble, updateDelay: 150, pluginKey: 'hkBubble',
      options: { placement: 'top', offset: 8 },
      shouldShow: function (a) {
        var st = a.state, sel = st.selection;
        if (!a.editor.isEditable || sel.empty || !(sel instanceof P.pm.state.TextSelection)) return false;
        if (sel.$from.parent.type.spec.code) return false;              // Code-Block (T4c): keine Marken
        if (!st.doc.textBetween(sel.from, sel.to).length) return false;
        var b = blockAt(sel.from); return !!(b && EDIT[b.node.type.name]);
      }
    });
  }

  // =================================================================================================================
  // Datum bearbeiten, Formel bearbeiten, Kontextmenue "Verweis einfuegen", Klick unter den letzten Block
  // =================================================================================================================
  function dateMenu(pos, dom) {
    var n = ed.state.doc.nodeAt(pos); if (!n || n.type.name !== 'date') return;
    HK.openMenu(dom, function (m) {
      var v = n.attrs.dt || '', wrap = el('div', 'hk-date-edit');
      var inp = el('input', 'hk-in'); inp.type = 'date'; inp.value = v.slice(0, 10);
      var tm = el('input', 'hk-in'); tm.type = 'time'; tm.value = v.slice(11, 16);
      wrap.appendChild(inp); wrap.appendChild(tm); m.appendChild(wrap);
      if (ro()) { inp.disabled = tm.disabled = true; return; }
      function apply() {
        if (!inp.value) return;
        var d = { date: inp.value, time: tm.value || null }, cur = ed.state.doc.nodeAt(pos); if (!cur || cur.type.name !== 'date') return;
        ed.view.dispatch(ed.state.tr.setNodeMarkup(pos, null, Object.assign({}, cur.attrs, {
          dt: d.date + (d.time ? 'T' + d.time : ''), label: (cur.attrs.remind ? '⏰ ' : '') + HKKeys.dateText(d) })));
      }
      inp.addEventListener('change', apply); tm.addEventListener('change', apply);
      m.appendChild(HK.menuItem(T('Datum entfernen'), function () {
        var cur = ed.state.doc.nodeAt(pos); if (cur && cur.type.name === 'date') ed.view.dispatch(ed.state.tr.delete(pos, pos + cur.nodeSize));
      }, { icon: '✕', danger: true }));
    });
  }
  var mathPop = null;
  function closeMath() { if (mathPop) { mathPop.remove(); mathPop = null; } }
  function openMath(pos) {
    closeMath(); if (!ed) return;
    var n = ed.state.doc.nodeAt(pos); if (!n || n.type.name !== 'math_inline') return;
    var dom = ed.view.nodeDOM(pos), r = dom && dom.getBoundingClientRect ? dom.getBoundingClientRect() : { left: 20, bottom: 20 };
    var p = el('div', 'hk-mpop'); p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', T('Formel'));
    var inp = el('input', 'hk-mpop-in'); inp.type = 'text'; inp.value = n.attrs.tex || ''; inp.spellcheck = false; inp.setAttribute('aria-label', T('Formel (TeX)'));
    inp.readOnly = ro();
    p.appendChild(inp); document.body.appendChild(p); mathPop = p;
    p.style.left = Math.max(8, Math.min(r.left, window.innerWidth - p.offsetWidth - 8)) + 'px';
    p.style.top = Math.min(r.bottom + 6, window.innerHeight - p.offsetHeight - 8) + 'px';
    inp.addEventListener('input', function () {
      var cur = ed.state.doc.nodeAt(pos); if (!cur || cur.type.name !== 'math_inline') return;
      ed.view.dispatch(ed.state.tr.setNodeMarkup(pos, null, Object.assign({}, cur.attrs, { tex: inp.value })));
    });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === 'Escape') {
        e.preventDefault(); closeMath();
        var cur = ed.state.doc.nodeAt(pos);
        if (cur && cur.type.name === 'math_inline' && !cur.attrs.tex) ed.view.dispatch(ed.state.tr.delete(pos, pos + cur.nodeSize));
        ed.commands.focus(); setSel0(pos + 1);
      }
    });
    setTimeout(function () { inp.focus(); inp.select(); }, 0);
  }
  function setSel0(pos) { var tr = ed.state.tr; setSel(tr, pos); ed.view.dispatch(tr); }
  document.addEventListener('mousedown', function (e) { if (mathPop && !mathPop.contains(e.target)) closeMath(); });

  var ctxAnchor = el('div', 'ctx-anchor'); document.body.appendChild(ctxAnchor);
  function contextMenu(view, e) {
    if (e.shiftKey || !ed || ro()) return false;
    var hit = view.posAtCoords({ left: e.clientX, top: e.clientY }); if (!hit) return false;
    var $p = view.state.doc.resolve(hit.pos); if (!$p.parent.isTextblock || $p.parent.type.spec.code) return false;
    var b = blockAt(hit.pos); if (!b || !EDIT[b.node.type.name]) return false;
    e.preventDefault();
    var sel = view.state.selection, from = hit.pos, to = hit.pos;
    if (!sel.empty && sel.from <= hit.pos && sel.to >= hit.pos && sel.$from.parent === sel.$to.parent) { from = sel.from; to = sel.to; }
    ctxAnchor.style.left = e.clientX + 'px'; ctxAnchor.style.top = e.clientY + 'px';
    HK.openMenu(ctxAnchor, function (m) {
      m.appendChild(HK.menuItem(from === to ? T('Verweis einfügen …') : T('Markierung durch Verweis ersetzen …'), function () {
        if (!K.picker) return;
        K.picker({ left: e.clientX, right: e.clientX, top: e.clientY, bottom: e.clientY }, function (href, text) {
          var s = schema(), mm = /^\/notes#(\d+)$/.exec(href), tr = ed.state.tr, node;
          if (mm) node = s.nodes.page.create({ id: +mm[1] });
          else node = s.text(text, [s.marks.link.create({ href: href })]);
          tr.replaceWith(from, to, node); tr.insertText(' ', from + node.nodeSize); setSel(tr, from + node.nodeSize + 1);
          ed.view.dispatch(tr); ed.commands.focus();
        });
      }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(el('div', 'menu-hint', T('Browser-Menü: Shift + Rechtsklick')));
    });
    return true;
  }
  // Klick in die Flaeche unter dem letzten Block: leerer letzter Absatz -> dorthin, sonst neuer Absatz (wie bisher)
  var art = BOX.closest('article'), artDown = false;
  // nur wenn auch das Druecken hier war (Klick in ein Vorschlagsmenue schliesst es beim Druecken, das Loslassen landet
  // dann unter dem letzten Block - T4c: "/Code" als letzter Block bekam sonst einen leeren Absatz dahinter)
  document.addEventListener('mousedown', function (e) { artDown = !!(art && art.contains(e.target)); }, true);
  if (art) art.addEventListener('mouseup', function (e) {
    if (!ed || ro() || e.button !== 0 || BOX.hidden || !artDown) return;
    if (e.target.closest && e.target.closest('[data-block-id], input, textarea, button, a, select, .backlinks, .db, .props, .page-head, .pm-bubble')) return;
    var dom = ed.view.dom, last = dom.lastElementChild; if (!last || e.clientY < last.getBoundingClientRect().bottom) return;
    var sel = window.getSelection(); if (sel && sel.rangeCount && !sel.isCollapsed) return;
    var doc = ed.state.doc, ln = doc.lastChild, tr = ed.state.tr;
    if (ln && ln.type.name === 'paragraph' && !ln.content.size) setSel(tr, doc.content.size - 1);
    else { tr.insert(doc.content.size, schema().nodes.paragraph.create({ id: newId() })); setSel(tr, tr.doc.content.size - 1); }
    ed.view.dispatch(tr.scrollIntoView()); ed.view.focus();
  });

  // =================================================================================================================
  // Editor anlegen / abbauen, Aenderungen melden
  // =================================================================================================================
  var lastBlk = null;
  function changedIds(tr) {
    var ids = [], seen = {}, doc = tr.doc;
    tr.mapping.maps.forEach(function (map, i) {
      var rest = tr.mapping.slice(i + 1);
      map.forEach(function (a, b, na, nb) {
        var from = rest.map(na, -1), to = rest.map(nb, 1);
        from = Math.max(0, Math.min(from, doc.content.size)); to = Math.max(from, Math.min(to, doc.content.size));
        var x = blockAt(from, doc); if (x && x.node.attrs.id && !seen[x.node.attrs.id]) { seen[x.node.attrs.id] = 1; ids.push(x.node.attrs.id); }
        doc.nodesBetween(from, to, function (n) { if (BLOCKSET[n.type.name] && n.attrs.id && !seen[n.attrs.id]) { seen[n.attrs.id] = 1; ids.push(n.attrs.id); } return true; });
      });
    });
    return ids;
  }
  function onTr(a) {
    var tr = a.transaction;
    if (!tr.docChanged || tr.getMeta('hkRemote') || !CB) return;
    CB.changed(changedIds(tr));
  }
  function onSel() {                                   // Anwesenheit Stufe 1: anderer Block -> focusin wie bei Editor.js
    var b = focusBlock();
    if (b !== lastBlk) { lastBlk = b; if (b) BOX.dispatchEvent(new FocusEvent('focusin', { bubbles: true })); }
  }
  function focusBlock() {
    if (!ed || !ed.view.hasFocus()) {
      var a = document.activeElement, own = a && a.closest ? a.closest('[' + AID + ']') : null;
      return own && BOX.contains(own) ? own.getAttribute(AID) : null;
    }
    var sel = ed.state.selection, b = sel instanceof P.pm.state.NodeSelection && isBlock(sel.node) ? { node: sel.node } : blockAt(sel.from);
    return b && b.node.attrs.id ? b.node.attrs.id : null;
  }
  function extensions() {
    var Tt = { Node: P.core.Node, Mark: P.core.Mark, Extension: P.core.Extension, mergeAttributes: P.core.mergeAttributes, UniqueID: P.UniqueID };
    var tv = toolViews();
    var base = SC.extensions(Tt, { pageTitle: pageTitle, mentionName: personName, nodeViews: tv });
    base = base.map(function (x) {
      var nv = nodeViewFor(x.name);
      if (!nv || (tv && tv[x.name])) return x;
      return x.extend({ addNodeView: function () { return nv; } });
    });
    return base.concat([P.UndoRedo.configure({ depth: 200, newGroupDelay: 500 }), P.Dropcursor.configure({ 'class': 'pm-dropcursor', color: false }),
      P.Gapcursor, SagaKern(), bubbleExt()]);
  }
  function destroy() {
    closeMath();
    if (ed) { try { ed.destroy(); } catch (e) { /* schon weg */ } ed = null; }
    if (bubble) { bubble.remove(); bubble = null; }
    [].forEach.call(document.querySelectorAll('.pm-pop'), function (x) { x.remove(); });
    if (holder) { holder.remove(); holder = null; }
    lastBlk = null;
  }
  function build(data) {
    destroy();
    data0 = data && Array.isArray(data.blocks) ? data : { blocks: [] };
    ORIG = new WeakSet(); markOrig(data0.blocks);
    collectTitles(data0);
    ctx = { orig: new WeakMap(), resolve: RESOLVE, version: 'saga-pm/' + SC.VERSION };
    // Altformat Toggle {text}: wie ToggleTool als erster Absatz anzeigen (upgrade); der Toggle bleibt an sein Original
    // gebunden (bind ohne upgrade) und kommt unveraendert byte-gleich zurueck. Bloecke ohne ID (nur der neue Absatz)
    // bekommen sie hier, nicht per UniqueID-Transaktion nach dem Laden (die waere eine Scheinaenderung).
    var json = AD.docFromBlocks(data0, { orig: null, upgrade: !!EDIT.toggle }), exts = extensions(), schemaX = P.core.getSchema(exts);
    (function ids(list) { (list || []).forEach(function (n) { if (BLOCKSET[n.type] && n.attrs && !n.attrs.id) n.attrs.id = newId(); else if (BLOCKSET[n.type] && !n.attrs) n.attrs = { id: newId() }; if (n.type === 'columns') (n.content || []).forEach(function (c) { ids(c.content); }); else ids(n.type === 'toggle' || n.type === 'synced' ? n.content : null); }); })(json.content);
    broken = false;
    try { schemaX.nodeFromJSON(json).check(); } catch (e) { broken = true; }
    if (!json.content.length) json.content = [{ type: 'paragraph', attrs: { id: newId() } }];
    holder = el('div', 'pm-holder'); BOX.appendChild(holder);
    ed = new P.core.Editor({
      element: holder, injectCSS: false, content: broken ? { type: 'doc', content: [{ type: 'paragraph' }] } : json, extensions: exts,
      editable: !broken && !(CB && CB.readOnly()), enableContentCheck: false, autofocus: false,
      editorProps: {
        attributes: { 'class': 'pm-doc', 'aria-label': T('Seiteninhalt'), role: 'textbox', 'aria-multiline': 'true' },
        handleDOMEvents: { contextmenu: contextMenu,
          // Enter ohne keydown (Handy-Tastaturen, abgefangene Taste): wie die Taste behandeln statt den DOM teilen zu lassen
          beforeinput: function (view, e) {
            if (e.inputType !== 'insertParagraph' && e.inputType !== 'insertLineBreak') return false;
            e.preventDefault();
            if (view.domObserver && view.domObserver.flush) view.domObserver.flush();      // Auswahl aus dem DOM (Pfeiltasten) zuerst
            ed.commands.keyboardShortcut(e.inputType === 'insertParagraph' ? 'Enter' : 'Shift-Enter');
            return true;
          } },
        handleClickOn: function (view, pos, node, nodePos, e, direct) {
          if (!direct) return false;
          if (node.type.name === 'date') { e.preventDefault(); dateMenu(nodePos, view.nodeDOM(nodePos)); return true; }
          if (node.type.name === 'math_inline') { e.preventDefault(); openMath(nodePos); return true; }
          return false;
        }
      },
      onTransaction: onTr, onSelectionUpdate: onSel, onFocus: onSel
    });
    if (broken) {
      HK.toast(T('Diese Seite kann der neue Editor nicht anzeigen – bitte im klassischen Editor öffnen. Es wird nichts gespeichert.'));
    } else AD.bind(ed.state.doc, data0, ctx);
  }

  // =================================================================================================================
  // Fassade HKPages.k.doc (kind 'tiptap')
  // =================================================================================================================
  function entryOf(x) {
    var n = x.node, id = n.attrs.id, dom = ed.view.nodeDOM(x.pos);
    if (!dom || dom.nodeType !== 1) return null;
    var e = { id: id, type: sagaType(n), el: dom, parent: x.parent, slot: x.slot,
      json: function () { var y = findId(id); return Promise.resolve(y ? blockJson(y.node) : null); } };
    Object.defineProperty(e, 'data', { enumerable: true, get: function () { var y = findId(id); return Promise.resolve(y ? blockJson(y.node).data : null); } });
    Object.defineProperty(e, 'index', { enumerable: true, get: function () {      // Stelle im Behaelter (Toggle: ohne Titel)
      var y = findId(id); if (!y) return -1;
      var $y = ed.state.doc.resolve(y.pos); return $y.index() - ($y.parent.type.name === 'toggle' ? 1 : 0);
    } });
    return e;
  }
  function blocks() {
    if (!ed) return [];
    var out = [];
    walk(ed.state.doc, function (x) { if (!x.inCard && x.node.attrs.id) { var e = entryOf(x); if (e) out.push(e); } });
    return out;
  }
  function byId(id) { if (!ed || id == null) return null; var x = findId(id); return x && !x.inCard ? entryOf(x) : null; }
  function current() {
    var a = document.activeElement; if (!ed || !a || !BOX.contains(a)) return null;
    if (a !== ed.view.dom) { var own = a.closest('[' + AID + ']'); if (own && BOX.contains(own)) return byId(own.getAttribute(AID)); }
    var id = focusBlock() || lastBlk; return id ? byId(id) : null;
  }
  // Behaelter: {start, end, parentPos} fuer parent (null = oberste Ebene) + slot
  function container(pid, slot) {
    var doc = ed.state.doc;
    if (pid == null) return { start: 0, end: doc.content.size, node: doc };
    var x = findId(pid); if (!x) return null;
    var n = x.node, t = n.type.name;
    if (t === 'columns') {
      var off = x.pos + 1, hit = null;
      n.forEach(function (col, o, i) { if (i === (slot || 0)) hit = { start: x.pos + 1 + o + 1, end: x.pos + 1 + o + col.nodeSize - 1, node: col }; });
      return hit;
    }
    if (t === 'toggle') { var s0 = x.pos + 1 + n.firstChild.nodeSize; return { start: s0, end: x.pos + n.nodeSize - 1, node: n, skip: 1 }; }
    if (t === 'synced') return { start: x.pos + 1, end: x.pos + n.nodeSize - 1, node: n };
    return null;
  }
  function mk(list) {
    var used = {}; walk(ed.state.doc, function (x) { if (x.node.attrs.id) used[x.node.attrs.id] = 1; });
    return (list || []).map(function (b) {
      var id = b.id && !used[b.id] ? String(b.id) : newId(); used[id] = 1;
      return nodeOf({ id: id, type: b.type, data: b.data || {}, tunes: b.tunes || undefined });
    });
  }
  function focusIn(tr, pos, n, where) {
    var S0 = P.pm.state, inner = null, innerEnd = null;
    if (n.isTextblock) { inner = pos + 1; innerEnd = pos + 1 + n.content.size; }
    else n.descendants(function (c, p) { if (c.isTextblock) { if (inner == null) inner = pos + 1 + p + 1; innerEnd = pos + 1 + p + 1 + c.content.size; } return !c.isTextblock; });
    if (inner == null) { tr.setSelection(S0.NodeSelection.create(tr.doc, pos)); return; }
    var at = where === 'start' ? inner : innerEnd;
    tr.setSelection(S0.TextSelection.create(tr.doc, at));
  }
  function emptyFill(tr, start, nodeIsEmpty) {
    if (nodeIsEmpty) tr.insert(start, schema().nodes.paragraph.create({ id: newId() }));
  }
  function insertAfter(ref, list, o) {
    if (!ed) return Promise.reject(new Error('editor not ready'));
    var at;
    if (ref == null) at = ed.state.doc.content.size;
    else if (typeof ref === 'object') {
      var c = container(ref.parent, ref.slot); if (!c) return Promise.reject(new Error('block not found'));
      var kids = []; c.node.forEach(function (k, off) { kids.push(off); });
      var first = c.skip ? 1 : 0, n = c.node.childCount - first;
      var i = ref.at === 'start' ? 0 : typeof ref.at === 'number' ? Math.max(0, Math.min(n, ref.at)) : n;
      at = i >= n ? c.end : c.start + (kids[i + first] - (kids[first] || 0));
    } else {
      var x = findId(ref); if (!x || x.inCard) return Promise.reject(new Error('block not found'));
      at = x.pos + x.node.nodeSize;
    }
    var nodes = mk(list); if (!nodes.length) return Promise.resolve([]);
    var tr = ed.state.tr.insert(at, nodes);
    if (o && (o.focus === 'start' || o.focus === 'end')) focusIn(tr, at, nodes[0], o.focus);
    ed.view.dispatch(tr.scrollIntoView());
    if (o && o.focus) ed.view.focus();
    return Promise.resolve(nodes.map(function (n) { return n.attrs.id; }));
  }
  function group(ids) {
    var xs = ids.map(function (id) { return findId(id); });
    if (xs.some(function (x) { return !x || x.inCard; })) return null;
    var k = xs[0].parent + '/' + xs[0].slot;
    if (xs.some(function (x) { return x.parent + '/' + x.slot !== k; })) return null;
    return xs;
  }
  function remove(ids) {
    if (!ed) return Promise.resolve([]);
    ids = (ids || []).map(String);
    var xs = ids.map(function (id) { return findId(id); }).filter(function (x) { return x && !x.inCard; });
    if (!xs.length) return Promise.resolve([]);
    var tr = ed.state.tr, conts = {};
    xs.slice().sort(function (a, b) { return b.pos - a.pos; }).forEach(function (x) {
      tr.delete(tr.mapping.map(x.pos), tr.mapping.map(x.pos + x.node.nodeSize));
      conts[x.parent + '/' + x.slot] = x;
    });
    Object.keys(conts).forEach(function (k) { refill(tr, conts[k].parent, conts[k].slot); });
    ed.view.dispatch(tr);
    return Promise.resolve(xs.map(function (x) { return x.node.attrs.id; }));
  }
  function refill(tr, pid, slot) {                   // leerer Behaelter bekommt einen leeren Absatz (wie Editor.js)
    if (pid == null) { if (!tr.doc.childCount) tr.insert(0, schema().nodes.paragraph.create({ id: newId() })); return; }
    var x = findId(pid, tr.doc); if (!x) return;
    var n = x.node;
    if (n.type.name === 'toggle') { emptyFill(tr, x.pos + 1 + n.firstChild.nodeSize, n.childCount === 1); return; }
    if (n.type.name === 'synced') { emptyFill(tr, x.pos + 1, !n.childCount); return; }
    if (n.type.name === 'columns') n.forEach(function (col, o, i) { if (i === (slot || 0)) emptyFill(tr, x.pos + 1 + o + 1, !col.childCount); });
  }
  function replace(ids, list, o) {
    if (!ed) return Promise.reject(new Error('editor not ready'));
    ids = (ids || []).map(String); list = list || [];
    if (!list.length) return remove(ids);
    var xs = group(ids); if (!xs) return Promise.reject(new Error('blocks not in one container'));
    var tr = ed.state.tr, nodes;
    if (ids.length === 1 && list.length === 1 && String(list[0].id) === ids[0] && sagaType(xs[0].node) === list[0].type) {
      var b = list[0], cur = blockJson(xs[0].node);
      nodes = [nodeOf({ id: ids[0], type: b.type, data: b.data || {}, tunes: b.tunes || cur.tunes || undefined })];
    } else {
      var keep = {}; ids.forEach(function (i) { keep[i] = 1; });
      var used = {}; walk(ed.state.doc, function (x) { if (x.node.attrs.id && !keep[x.node.attrs.id]) used[x.node.attrs.id] = 1; });
      nodes = list.map(function (b) {
        var id = b.id && !used[b.id] ? String(b.id) : newId(); used[id] = 1;
        return nodeOf({ id: id, type: b.type, data: b.data || {}, tunes: b.tunes || undefined });
      });
    }
    var at = Math.min.apply(null, xs.map(function (x) { return x.pos; }));
    xs.slice().sort(function (a, b) { return b.pos - a.pos; }).forEach(function (x) { tr.delete(x.pos, x.pos + x.node.nodeSize); });
    tr.insert(at, nodes);
    if (o && (o.focus === 'start' || o.focus === 'end')) focusIn(tr, at, nodes[0], o.focus);
    ed.view.dispatch(tr.scrollIntoView());
    if (o && o.focus) ed.view.focus();
    return Promise.resolve(nodes.map(function (n) { return n.attrs.id; }));
  }
  function move(id, dir) {
    if (!ed || ro()) return false;
    var x = findId(id); if (!x || x.inCard) return false;
    for (var mi = 0; mi < MODS.length; mi++) if (MODS[mi].api && MODS[mi].api.moveBlock) return MODS[mi].api.moveBlock(String(id), dir);
    var $p = ed.state.doc.resolve(x.pos), parent = $p.parent, i = $p.index(), first = parent.type.name === 'toggle' ? 1 : 0;
    var j = i + dir; if (j < first || j >= parent.childCount) return false;
    var other = parent.child(j), tr = ed.state.tr, S0 = P.pm.state;
    var sel = ed.state.selection, inside = sel.from >= x.pos && sel.to <= x.pos + x.node.nodeSize, off = sel.from - x.pos;
    var to;
    if (dir < 0) { var op = x.pos - other.nodeSize; tr.delete(x.pos, x.pos + x.node.nodeSize); tr.insert(op, x.node); to = op; }
    else { var after = x.pos + x.node.nodeSize; tr.delete(after, after + other.nodeSize); tr.insert(x.pos, other); to = x.pos + other.nodeSize; }
    if (inside) { try { tr.setSelection(S0.TextSelection.near(tr.doc.resolve(to + off))); } catch (e) { /* Auswahl bleibt */ } }
    ed.view.dispatch(tr.scrollIntoView());
    return true;
  }
  function focus(id, pos) {
    if (!ed) return false;
    if (SELKEY && (SELKEY.getState(ed.state) || []).length) select([]);     // Schreibmarke statt Blockauswahl
    var x = findId(id); if (!x || x.inCard) return false;
    var tr = ed.state.tr; focusIn(tr, x.pos, x.node, pos === 'start' ? 'start' : 'end');
    ed.view.dispatch(tr.scrollIntoView()); ed.view.focus();
    return true;
  }
  function select(ids) {
    if (!ed) return;
    var want = (ids || []).map(String).filter(function (i) { var x = findId(i); return x && !x.inCard; });
    var tr = ed.state.tr.setMeta(SELKEY, want); ed.view.dispatch(tr);
    ed.view.dom.classList.toggle('pm-blocksel', want.length > 0);
  }
  function selected() {
    if (!ed || !SELKEY) return [];
    var want = {}; (SELKEY.getState(ed.state) || []).forEach(function (i) { want[i] = 1; });
    return blocks().filter(function (b) { if (!want[b.id]) return false; delete want[b.id]; return true; });
  }
  function onChange(cb) { return K.onDocChange ? K.onDocChange(cb) : function () {}; }
  function modApi(name) { for (var i = 0; i < MODS.length; i++) if (MODS[i].api && MODS[i].api[name]) return MODS[i].api[name]; return null; }
  // Tiptap-Zusaetze (T3): Block-Menue (Cmd+/, static/keys.js), Undo (Rueckgaengig in Hinweisen), Auswahl ohne Nachkommen
  function menu(id, anchor) { var f = modApi('openBlockMenu'); if (!f || !findId(id)) return false; f(String(id), anchor); return true; }
  function undo() { return !!(ed && ed.commands.undo()); }
  K.doc = { kind: 'tiptap', blocks: blocks, current: current, byId: byId, insertAfter: insertAfter, replace: replace,
            remove: remove, move: move, select: select, selected: selected, focus: focus, onChange: onChange,
            menu: menu, undo: undo };

  // =================================================================================================================
  // Editor-Objekt fuer pages.js (an Stelle der Editor.js-Instanz)
  // =================================================================================================================
  // Fremde Bloecke blockweise uebernehmen (Rebase/Live, Stufe 1): unveraenderte Bloecke bleiben dieselben Knoten,
  // ersetzt wird nur der Bereich zwischen gleichem Anfang und gleichem Ende (Schreibmarke bleibt meist, kein Undo-Schritt)
  function applyBlocks(list) {
    if (!ed || broken) return;
    markOrig(list);                                   // Stand vom Server: gilt als unveraendert (prune)
    var doc = ed.state.doc, cur = [], byIdTop = {};
    doc.forEach(function (n) { cur.push(n); if (n.attrs.id) byIdTop[n.attrs.id] = n; });
    var neu = (list || []).filter(function (b) { return b && typeof b === 'object'; }).map(function (b) {
      var old = b.id != null ? byIdTop[b.id] : null;
      if (old) { var j = blockJson(old); if (stable([j.type, j.data, j.tunes]) === stable([b.type, b.data || {}, b.tunes || {}])) return old; }
      return nodeOf(b);
    });
    if (!neu.length) neu = [schema().nodes.paragraph.create({ id: newId() })];
    var i = 0; while (i < cur.length && i < neu.length && cur[i] === neu[i]) i++;
    var j2 = 0; while (j2 < cur.length - i && j2 < neu.length - i && cur[cur.length - 1 - j2] === neu[neu.length - 1 - j2]) j2++;
    if (i === cur.length && i === neu.length) return;
    var from = 0, k; for (k = 0; k < i; k++) from += cur[k].nodeSize;
    var to = from; for (k = i; k < cur.length - j2; k++) to += cur[k].nodeSize;
    // Schreibmarke ueber Block-ID + Abstand halten (der ersetzte Bereich wuerde sie an seinen Rand schieben)
    var sel = ed.state.selection, hb = blockAt(sel.head), keep = hb && hb.node.attrs.id ? { id: hb.node.attrs.id, off: sel.head - hb.pos } : null;
    var tr = ed.state.tr.replaceWith(from, to, neu.slice(i, neu.length - j2));
    tr.setMeta('addToHistory', false); tr.setMeta('hkRemote', true); tr.setMeta('hkAllow', true);
    if (keep && sel.empty) {
      var nx = findId(keep.id, tr.doc);
      if (nx) { try { tr.setSelection(P.pm.state.TextSelection.near(tr.doc.resolve(Math.min(nx.pos + keep.off, nx.pos + nx.node.nodeSize - 1)))); } catch (e) { /* Auswahl bleibt gemappt */ } }
    }
    ed.view.dispatch(tr);
    if (window.HKPmEditor) HKPmEditor.applied = (HKPmEditor.applied || 0) + 1;      // Diagnose/Tests
  }
  function saveNow() {
    if (!ed) return { blocks: [] };
    if (broken) return { time: Date.now(), version: data0.version, blocks: data0.blocks };
    return prune(AD.blocksFromDoc(ed.state.doc, ctx));
  }
  // Wie validate() der Editor.js-Werkzeuge: neue/geaenderte Bloecke, die eine Erweiterung ablehnt (validate(block) ===
  // false: Bild ohne Datei, fremder Nextcloud-Block ...), nicht mitspeichern. Unveraenderte Bloecke (Original-Objekte aus
  // dem geladenen Stand) bleiben immer - auch in Spalten/Toggles; ohne Ablehnung kommt das Ergebnis unveraendert zurueck.
  var ORIG = new WeakSet();
  function markOrig(list) {
    (list || []).forEach(function (b) {
      if (!b || typeof b !== 'object') return;
      ORIG.add(b); var d = b.data || {};
      if (Array.isArray(d.blocks)) markOrig(d.blocks);
      if (Array.isArray(d.cols)) d.cols.forEach(function (c) { if (c && Array.isArray(c.blocks)) markOrig(c.blocks); });
    });
  }
  function prune(out) {
    var vs = MODS.map(function (m) { return m.validate; }).filter(Boolean);
    if (!vs.length || !out || !Array.isArray(out.blocks)) return out;
    function list(bl) {
      var res = [], ch = false;
      bl.forEach(function (b) {
        if (!b || typeof b !== 'object' || ORIG.has(b)) { res.push(b); return; }
        if (vs.some(function (v) { try { return v(b) === false; } catch (e) { return false; } })) { ch = true; return; }
        var nb = inner(b); if (nb !== b) ch = true; res.push(nb);
      });
      return ch ? res : bl;
    }
    function inner(b) {
      var d = b.data; if (!d || typeof d !== 'object') return b;
      if (Array.isArray(d.blocks)) { var l = list(d.blocks); if (l !== d.blocks) return Object.assign({}, b, { data: Object.assign({}, d, { blocks: l }) }); }
      if (b.type === 'columns' && Array.isArray(d.cols)) {
        var ch = false, cols = d.cols.map(function (c) {
          if (!c || !Array.isArray(c.blocks)) return c;
          var l2 = list(c.blocks); if (l2 === c.blocks) return c; ch = true; return Object.assign({}, c, { blocks: l2 });
        });
        if (ch) return Object.assign({}, b, { data: Object.assign({}, d, { cols: cols }) });
      }
      return b;
    }
    var l0 = list(out.blocks);
    return l0 === out.blocks ? out : Object.assign({}, out, { blocks: l0 });
  }
  var COLORABLE = { paragraph: 1, header: 1, list: 1, quote: 1, callout: 1, toggle: 1 };
  function styleAt(node, st) {                       // Blockfarbe (Cmd+/, "/rot"): Element im Block oder Blockelement
    if (!ed || ro()) return false;
    var b = node && node.closest ? node.closest('[' + AID + ']') : null; if (!b) return false;
    var x = findId(b.getAttribute(AID)); if (!x || !COLORABLE[x.node.type.name]) return false;
    var c = st && COLORS.indexOf(st.c) >= 0 ? (st.bg === true ? { c: st.c, bg: true } : { c: st.c }) : null;
    ed.view.dispatch(ed.state.tr.setNodeMarkup(x.pos, null, Object.assign({}, x.node.attrs, { style: c })));
    return true;
  }
  function draft() {
    if (!ed) return '';
    var out = [];
    ed.state.doc.forEach(function (n) { var t = n.textBetween(0, n.content.size, '\n', ' ').trim(); if (t) out.push(t); });
    return out.join('\n\n');
  }
  function create(cb) {
    CB = cb;
    return {
      kind: 'tiptap', isReady: Promise.resolve(),
      render: function (data) { build(data); return Promise.resolve(); },
      save: function () { try { return Promise.resolve(saveNow()); } catch (e) { return Promise.reject(e); } },
      applyBlocks: applyBlocks,
      setEditable: function (on) { if (ed && !broken && ed.isEditable !== !!on) ed.setEditable(!!on); },
      caret: {
        setToFirstBlock: function (pos) { var f = ed && ed.state.doc.firstChild; if (f && f.attrs.id) focus(f.attrs.id, pos === 'end' ? 'end' : 'start'); },
        setToBlock: function (idx, pos) { var n = ed && ed.state.doc.maybeChild(idx); if (n && n.attrs.id) focus(n.attrs.id, pos === 'start' ? 'start' : 'end'); }
      },
      toolbar: { close: function () {} },
      text: draft, focusBlock: focusBlock, styleAt: styleAt, destroy: destroy,
      editor: function () { return ed; }                 // Tests/Diagnose
    };
  }

  // =================================================================================================================
  // Erweiterungen (T3: static/pm-struktur.js). HKPmEditor.use(fn): fn(X) laeuft nach dem Laden des Bundles und liefert
  // {edit: [Typen, die bearbeitbar werden], nodeViews: {Knoten: Fabrik}, plugins(editor) -> [Plugin], keys(editor) ->
  // {Taste: fn -> true wenn erledigt} (vor den Kern-Tasten), protect(node) -> true = wie Karte schuetzen, api: {...}}.
  // X sind die internen Bausteine dieses Editors (nur fuer Erweiterungen im Repo, keine oeffentliche Schnittstelle).
  // =================================================================================================================
  function usedIds() { var u = {}; walk(ed.state.doc, function (x) { if (x.node.attrs.id) u[x.node.attrs.id] = 1; }); return u; }
  function freshIds(node, used, sidFn) {             // tiefe Kopie mit neuen Block-IDs (Duplizieren, Uebernehmen)
    used = used || usedIds();                         // Kopie ist nie selbst Konfliktblock; sidFn: Originale bekommen neue sid
    return (function rec(n) {
      if (n.isText) return n;
      var kids = []; n.forEach(function (c) { kids.push(rec(c)); });
      var a = n.attrs;
      if (BLOCKSET[n.type.name]) { var id; do { id = newId(); } while (used[id]); used[id] = 1; a = Object.assign({}, a, { id: id }); }
      if (BLOCKSET[n.type.name] && a.conflict) a = Object.assign({}, a, { conflict: null });
      if (sidFn && n.type.name === 'synced' && !(a.extra && a.extra.data && a.extra.data.src)) a = Object.assign({}, a, { sid: sidFn() });
      return n.type.create(a, kids.length ? kids : null, n.marks);
    })(node);
  }
  var X = {
    get P() { return P; }, get SC() { return SC; }, get AD() { return AD; }, K: K, el: el, T: T, BOX: BOX, COLORS: COLORS, isMac: isMac,
    ed: function () { return ed; }, holder: function () { return holder; }, schema: schema, ro: ro, newId: newId, walk: walk, findId: findId,
    blockAt: blockAt, blockJson: blockJson, nodeOf: nodeOf, setSel: setSel, paint: paint, ignoreOutside: ignoreOutside, nodeBtn: nodeBtn,
    posOf: posOf, setAttrs: setAttrs, sagaType: sagaType, isBlock: isBlock, menuActive: menuActive, slashReg: slashReg, makeBlock: makeBlock,
    convertAt: convertAt, refill: refill, focus: focus, focusIn: focusIn, freshIds: freshIds,
    mk: function (list, fresh) { var n = mk(list); if (!fresh) return n; var u = usedIds(); return n.map(function (x) { return freshIds(x, u); }); },
    select: select, selIds: function () { return ed && SELKEY ? (SELKEY.getState(ed.state) || []).slice() : []; },
    convertible: function (n) { return !!CONV[n.type.name]; }, colorable: function (n) { return !!COLORABLE[n.type.name]; },
    nestOk: function (n) { return n.type.name !== 'columns'; },
    // Block-Menue (pm-struktur.js): Einstellungen und Namen weiterer Typen aus anderen Erweiterungen (T4a: Medien)
    settings: function (m, x) { MODS.forEach(function (md) { if (md.settings) md.settings(m, x); }); },
    typeName: function (t) { for (var i = 0; i < MODS.length; i++) if (MODS[i].names && MODS[i].names[t]) return MODS[i].names[t]; return null; },
    inNested: function (pos) { return !!ed && ed.state.doc.resolve(Math.min(pos, ed.state.doc.content.size)).depth > 1; }
  };
  function initExt(fn) {
    var m = null; try { m = fn(X); } catch (e) { if (window.console) console.error('pm-editor Erweiterung', e); }
    if (!m) return;
    MODS.push(m);
    (m.edit || []).forEach(function (t) { EDIT[t] = 1; });
  }
  function use(fn) { if (P) initExt(fn); else EXTS.push(fn); }
  function mod(name) { return modApi(name); }

  window.HKPmEditor = { load: load, create: create, slash: { register: slashReg, list: function () { return SLASH.slice(); } },
                        TOOL_SCRIPTS: TOOL_SCRIPTS, use: use, api: mod };
})();
