/* Saga-Werkzeuge als eigenstaendige Komponenten (Node-Views) - 2.5, Tiptap-Umbau T4b (Konzept 2.4).
 *
 * Schnittstelle, unabhaengig von Editor.js und Tiptap:
 *
 *   HKTools.register(type, spec)       spec = {title, icon, slash: [{title, icon, data}], atom: true,
 *                                              mount(el, data, ctx) -> {update(data) -> bool, destroy(), stopEvent(e) -> bool}}
 *   HKTools.get(type) / types() / slash()   Eintraege (z. B. fuers "/"-Menue des Kern-Editors)
 *   HKTools.nodeViews(base)            {Saga-Typ: Renderer} im Tiptap-addNodeView-Format, fuer
 *                                      HKPmSchema.extensions(T, {..., nodeViews: HKTools.nodeViews(base)})
 *   HKTools.fromEditorJs(Klasse, opts) Bruecke: bestehende Editor.js-Werkzeugklasse -> spec (siehe unten)
 *   HKTools.setup(fn)                  Registrierung nachholen, sobald fn() true liefert (Ladereihenfolge)
 *   HKTools.stable(v), clone(v)        Hilfen (JSON mit sortierten Schluesseln, tiefe Kopie)
 *
 * ctx je Block (Getter, immer aktuell): {type, id, readOnly, role, foreign, page, editor, view, getPos(), node(),
 *   change(newData[, {history:false}]) -> bool, remove(), replaceWith(blocks), select(), focusNext()}
 *   change() schreibt GENAU EINE Transaktion (setNodeMarkup, Attribut data) - nichts, wenn sich data nicht aendert
 *   oder ctx.readOnly gilt. Eigene Aenderungen kommen nicht als update() zurueck.
 * base (Editor-weit, alles optional, Werte oder Funktionen): {readOnly, role, foreign, page, replaceWith(ctx, blocks),
 *   blockToPm(block) -> PM-JSON}. Ohne Angabe: Zustand der offenen Seite aus HKPages.k bzw. editor.isEditable.
 *
 * Node-View: div.hk-tool[data-block-id][data-block-type][contenteditable=false], ignoreMutation: true,
 *   stopEvent: alles innerhalb des Werkzeugs (nicht die Huelle selbst - Klick auf den Rand waehlt den Block),
 *   selectNode/deselectNode: Klasse ProseMirror-selectednode, Blockfarbe (style) als hk-st-*.
 *   update(node): neue Daten von aussen -> handle.update(data); false -> ProseMirror baut die Node-View neu auf.
 *
 * Ohne Build-Schritt (ES5-nah). Keine Inline-Styles (CSP style-src 'self').
 */
(function (root) {
  'use strict';
  var REG = {}, SETUPS = [];

  function isDict(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function stable(v) {
    if (Array.isArray(v)) return '[' + v.map(stable).join(',') + ']';
    if (isDict(v)) {
      return '{' + Object.keys(v).filter(function (k) { return v[k] !== undefined; }).sort()
        .map(function (k) { return JSON.stringify(k) + ':' + stable(v[k]); }).join(',') + '}';
    }
    return v === undefined ? 'null' : JSON.stringify(v);
  }
  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function val(x, dflt) { if (typeof x === 'function') { try { return x(); } catch (e) { return dflt; } } return x === undefined ? dflt : x; }
  function K() { return root.HKPages && root.HKPages.k; }

  function runSetups() {
    for (var i = 0; i < SETUPS.length; i++) {
      var ok = false;
      try { ok = SETUPS[i]() !== false; } catch (e) { ok = false; if (root.console) console.warn('HKTools.setup', e); }
      if (ok) { SETUPS.splice(i, 1); i--; }
    }
  }

  function register(type, spec) {
    if (!type || !spec || typeof spec.mount !== 'function') throw new Error('HKTools.register: Typ und mount() noetig');
    REG[type] = spec;
    return spec;
  }
  function get(type) { runSetups(); return REG[type] || null; }
  function types() { runSetups(); return Object.keys(REG); }
  function slash() {
    runSetups();
    var out = [];
    Object.keys(REG).forEach(function (t) {
      var s = REG[t], list = s.slash || [{ title: s.title, icon: s.icon, data: {} }];
      list.forEach(function (e) { out.push({ type: t, title: e.title || s.title, icon: e.icon || s.icon, data: clone(e.data || {}) }); });
    });
    return out;
  }

  // --- Node-View-Huelle ------------------------------------------------------------------------------------------
  var PALETTE = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  function makeNodeView(type, props, base) {
    var spec = REG[type], node = props.node, editor = props.editor, view = props.view || (editor && editor.view);
    var getPos = props.getPos, dom = document.createElement('div'), handle = null, cur = stable(node.attrs.data || {});
    var destroyed = false;
    dom.className = 'hk-tool hk-tool-' + type;
    dom.setAttribute('contenteditable', 'false');
    dom.setAttribute('data-block-type', type);
    function syncAttrs() {
      var a = node.attrs || {};
      if (a.id) dom.setAttribute('data-block-id', a.id); else dom.removeAttribute('data-block-id');
      PALETTE.forEach(function (c) { dom.classList.remove('hk-st-' + c); });
      dom.classList.remove('hk-st-bg');
      if (a.style && PALETTE.indexOf(a.style.c) >= 0) { dom.classList.add('hk-st-' + a.style.c); if (a.style.bg) dom.classList.add('hk-st-bg'); }
    }
    syncAttrs();
    // Klick auf die Karte (nicht auf ein Bedienelement darin): Karte auswaehlen. stopEvent haelt ProseMirror aus dem
    // Werkzeug heraus, ohne das bliebe die alte Auswahl stehen (z. B. Schreibmarke im Absatz darueber).
    dom.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || e.shiftKey || !view || readOnly() || destroyed) return;
      var t = e.target;
      for (var x = t; x && x !== dom; x = x.parentNode) {          // Bedienelemente im Werkzeug behalten ihren Klick
        if (x.nodeType === 1 && (/^(INPUT|TEXTAREA|SELECT|BUTTON|A|LABEL)$/.test(x.tagName) || x.isContentEditable || (x.classList && x.classList.contains('wg-panel')))) return;
      }
      var p = pos(); if (p === null) return;
      var S = root.SagaPM.pm.state, sel = view.state.selection;
      if (sel instanceof S.NodeSelection && sel.from === p) return;
      view.dispatch(view.state.tr.setSelection(S.NodeSelection.create(view.state.doc, p)).setMeta('pointer', true));
      setTimeout(function () {                                       // Fokus nur, wenn das Werkzeug ihn nicht selbst nimmt
        var a = document.activeElement;
        if (!destroyed && (!a || a === document.body || a === view.dom)) view.focus();
      }, 0);
    });
    function pos() { var p = typeof getPos === 'function' ? getPos() : null; return typeof p === 'number' ? p : null; }
    function ySynced() { return !!(view && view.state.plugins.some(function (pl) { return /^y-sync\$/.test(pl.key || ''); })); }
    function readOnly() {
      var r = val(base.readOnly, undefined);
      if (r === undefined) { var k = K(); r = !!(k && k.readOnly && k.readOnly()); }
      return !!r || !!(editor && editor.isEditable === false) || !!(view && view.editable === false);
    }
    function blockToPm(b) {
      if (typeof base.blockToPm === 'function') return base.blockToPm(b);
      return root.HKPmAdapter.blockToPm(b, {});
    }
    var ctx = {
      type: type,
      get id() { return (node.attrs && node.attrs.id) || null; },
      get readOnly() { return readOnly(); },
      get role() {
        var r = val(base.role, undefined); if (r !== undefined) return r;
        var k = K(), c = k && k.cur && k.cur(); return c ? (c.role || 'owner') : 'owner';
      },
      get foreign() {
        var f = val(base.foreign, undefined); if (f !== undefined) return !!f;
        var k = K(); return !!(k && k.foreign && k.foreign());
      },
      get page() {
        var p = val(base.page, undefined); if (p !== undefined) return p;
        var k = K(); return (k && k.cur && k.cur()) || null;
      },
      editor: editor, view: view,
      getPos: pos,
      node: function () { return node; },
      change: function (d, o) {
        if (destroyed || readOnly()) return false;
        // Nachtrag beim Laden (quiet) bei live gebundenem Editor (y-sync) nicht ins Dokument: jede Transaktion ginge ueber
        // Yjs an den Server, der materialisiert -> neue Revision ohne Bedienschritt und "geaendert"-Ereignis an alle Tabs.
        // Das Werkzeug behaelt den Stand und gibt ihn mit der naechsten echten Aenderung am Block mit
        if (o && o.quiet && ySynced()) return false;
        var p = pos(); if (p === null) return false;
        var s = stable(d || {}), n = view.state.doc.nodeAt(p);
        if (!n || n.type !== node.type || s === stable(n.attrs.data || {})) return false;
        cur = s;
        var attrs = {}; for (var k in n.attrs) attrs[k] = n.attrs[k];
        attrs.data = clone(d || {});
        var tr = view.state.tr.setNodeMarkup(p, null, attrs).setMeta('hkTool', type);
        if (o && o.history === false) tr.setMeta('addToHistory', false);
        if (o && o.quiet) tr.setMeta('hkQuiet', true);              // Nachtrag beim Laden: im Dokument, aber kein Speichern
        view.dispatch(tr);
        return true;
      },
      remove: function () {
        var p = pos(); if (p === null || readOnly()) return false;
        view.dispatch(view.state.tr.delete(p, p + node.nodeSize).setMeta('hkTool', type));
        return true;
      },
      replaceWith: function (blocks) {
        if (readOnly()) return false;
        if (typeof base.replaceWith === 'function') return base.replaceWith(ctx, blocks);
        var p = pos(); if (p === null) return false;
        var schema = view.state.schema, nodes = (blocks || []).map(function (b) { return schema.nodeFromJSON(blockToPm(b)); });
        view.dispatch(view.state.tr.replaceWith(p, p + node.nodeSize, nodes).setMeta('hkTool', type));
        return true;
      },
      insertAfter: function (blocks) {
        if (readOnly()) return false;
        var p = pos(); if (p === null) return false;
        var schema = view.state.schema, nodes = (blocks || []).map(function (b) { return schema.nodeFromJSON(blockToPm(b)); });
        view.dispatch(view.state.tr.insert(p + node.nodeSize, nodes).setMeta('hkTool', type));
        return true;
      },
      select: function () {
        var p = pos(); if (p === null) return false;
        var S = root.SagaPM.pm.state;
        view.dispatch(view.state.tr.setSelection(S.NodeSelection.create(view.state.doc, p)));
        view.focus();
        return true;
      },
      focusNext: function () {
        var p = pos(); if (p === null) return false;
        var S = root.SagaPM.pm.state, doc = view.state.doc, at = Math.min(doc.content.size, p + node.nodeSize);
        view.dispatch(view.state.tr.setSelection(S.Selection.near(doc.resolve(at), 1)).scrollIntoView());
        view.focus();
        return true;
      }
    };
    try {
      handle = spec.mount(dom, clone(node.attrs.data || {}), ctx) || {};
    } catch (e) {
      if (root.console) console.error('HKTools.mount ' + type, e);
      dom.textContent = '';
      var err = document.createElement('div'); err.className = 'hk-tool-err'; err.textContent = type; dom.appendChild(err);
      handle = {};
    }
    dom.hkTool = handle;                                                // Tests/Diagnose (Eigenschaft, kein Attribut)
    return {
      dom: dom,
      update: function (n) {
        if (n.type !== node.type) return false;
        node = n; syncAttrs();
        var s = stable(n.attrs.data || {});
        if (s === cur) return true;                                   // eigene Aenderung (ctx.change) oder nur andere Attribute
        cur = s;
        if (typeof handle.update !== 'function') return false;        // ohne update(): sicherer Neuaufbau
        try { return handle.update(clone(n.attrs.data || {})) !== false; } catch (e) { return false; }
      },
      selectNode: function () { dom.classList.add('ProseMirror-selectednode'); },
      deselectNode: function () { dom.classList.remove('ProseMirror-selectednode'); },
      stopEvent: function (e) {
        if (e.target === dom) return false;                           // Rand der Huelle: Block waehlen/ziehen
        return typeof handle.stopEvent === 'function' ? !!handle.stopEvent(e) : true;
      },
      ignoreMutation: function () { return true; },
      destroy: function () {
        destroyed = true;
        if (typeof handle.destroy === 'function') { try { handle.destroy(); } catch (e) { /* schon weg */ } }
      }
    };
  }

  function nodeViews(base) {
    runSetups();
    base = base || {};
    var out = {};
    Object.keys(REG).forEach(function (t) {
      out[t] = function (props) { return makeNodeView(t, props, base); };
    });
    return out;
  }

  // --- Bruecke fuer bestehende Editor.js-Werkzeugklassen ------------------------------------------------------------
  // opts: {known: [Felder, die save() liefert] | null (save() liefert alles, Regel 0 im Werkzeug selbst),
  //        hook(inst, notify)        Schreibstellen umbiegen (z. B. inst.mark = notify statt S.dirtyBody/schedule)
  //        carry(base, out) -> out   nach save(): Felder aus dem bisherigen Stand uebernehmen (z. B. Signaturen)
  //        meta(base, out) -> {history:false}?   Optionen fuer ctx.change (z. B. Nachtrag ohne Undo-Schritt)
  //        live(inst, data, h) -> bool   neue Daten von aussen ohne Neuaufbau einspielen (true = erledigt);
  //                                  h = {busy()}; sonst Neuaufbau (verschoben, solange ein Feld/Menue offen ist)
  //        destroy(inst)             zusaetzliches Aufraeumen
  //        title, icon, slash}       sonst aus Klasse.toolbox
  // Klasse darf ein Name sein (String): dann aus HKPages.k.tools beim Einhaengen.
  function fromEditorJs(Klasse, opts) {
    opts = opts || {};
    function cls() {
      if (typeof Klasse === 'function') return Klasse;
      var k = K(), t = k && k.tools; return t && t[Klasse];
    }
    var C0 = cls(), tb = C0 && C0.toolbox;
    var slashList = opts.slash || (Array.isArray(tb) ? tb.map(function (x) { return { title: x.title, icon: x.icon, data: x.data || {} }; })
      : tb ? [{ title: tb.title, icon: tb.icon, data: {} }] : null);
    var first = Array.isArray(tb) ? tb[0] : tb;
    return {
      title: opts.title || (first && first.title) || '', icon: opts.icon || (first && first.icon) || '', slash: slashList,
      atom: true, bridge: true,
      mount: function (el, data, ctx) {
        var C = cls();
        if (!C) throw new Error('HKTools.fromEditorJs: Werkzeugklasse fehlt');
        var base = isDict(data) ? clone(data) : {}, inst = null, rootEl = null, deferT = null, dead = false;
        var blk = { dispatchChange: function () { notify(); }, holder: el };
        Object.defineProperty(blk, 'id', { get: function () { return ctx.id; }, enumerable: true });
        var api = { blocks: {
          getCurrentBlockIndex: function () { return -1; }, getBlocksCount: function () { return 0; },
          delete: function () { ctx.remove(); },
          insert: function (type, d) { ctx.insertAfter([{ type: type, data: d || {} }]); }
        } };
        function merged() {
          var out = inst.save(); out = isDict(out) ? clone(out) : {};
          if (opts.known) {                                           // Regel 0: unbekannte Felder bleiben erhalten
            Object.keys(base).forEach(function (k) { if (opts.known.indexOf(k) < 0 && !(k in out)) out[k] = clone(base[k]); });
          }
          if (opts.carry) out = opts.carry(base, out) || out;
          return out;
        }
        function notify() {
          if (dead || !inst) return false;
          var out = merged();
          if (stable(out) === stable(base)) return false;
          if (ctx.change(out, opts.meta ? opts.meta(base, out) : undefined) === false) return false;
          base = clone(out);
          return true;
        }
        function build() {
          inst = new C({ data: clone(base), api: api, block: blk, readOnly: !!ctx.readOnly, config: opts.config || {} });
          if (opts.hook) opts.hook(inst, notify, ctx);
          rootEl = inst.render();
          el.appendChild(rootEl);
        }
        function teardown() {
          if (!inst) return;
          if (typeof inst.destroy === 'function') { try { inst.destroy(); } catch (e) { /* schon weg */ } }
          if (opts.destroy) { try { opts.destroy(inst); } catch (e) { /* egal */ } }
          inst = null;
          if (rootEl && rootEl.parentNode) rootEl.parentNode.removeChild(rootEl);
          rootEl = null;
        }
        function busy() {
          var a = document.activeElement;
          if (a && rootEl && rootEl.contains(a) && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return true;
          return !!document.querySelector('.menu:not([hidden]), #pg-modal:not([hidden])');
        }
        function rebuild() {
          clearTimeout(deferT); deferT = null;
          if (dead) return;
          if (busy()) { deferT = setTimeout(rebuild, 400); return; }
          teardown(); build();
        }
        build();
        return {
          update: function (d) {
            base = isDict(d) ? clone(d) : {};
            if (opts.live && inst && opts.live(inst, clone(base), { busy: busy, ctx: ctx })) return true;
            rebuild();
            return true;
          },
          destroy: function () { dead = true; clearTimeout(deferT); deferT = null; teardown(); },
          stopEvent: function () { return true; },                   // Ersatz fuer isolate(): Tasten/Ziehen/Einfuegen bleiben im Werkzeug
          instance: function () { return inst; }                     // fuer Tests/Diagnose
        };
      }
    };
  }

  root.HKTools = {
    register: register, get: get, types: types, slash: slash, nodeViews: nodeViews, fromEditorJs: fromEditorJs,
    setup: function (fn) { SETUPS.push(fn); runSetups(); }, stable: stable, clone: clone
  };
})(typeof self !== 'undefined' ? self : this);
