// Editor-Fassade HKPages.k.doc (Saga 2.5, Tiptap-Umbau T1) - hier die Umsetzung ueber Editor.js.
// Querschnittsmodule (keys.js, ai.js, editor25.js, comments.js, live.js, mention.js) und die Browser-Tests
// (tests/lib/editor_dom.js) greifen NUR noch ueber diese Schnittstelle und die Attribute data-block-id/-type auf den
// Editor zu. In T2 stellt der Tiptap-Editor dieselbe Schnittstelle bereit - nach aussen gehen deshalb keine
// Editor.js-Objekte, nur DOM-Elemente, IDs und Block-JSON ({id, type, data, tunes}).
//
// Begriffe
//   Block    ein Saga-Block (oberste Ebene oder in Spalte/Toggle/synchronisiertem Block). Jedes Blockelement im
//            Editor (#pg-editor) traegt data-block-id (= Block-ID) und data-block-type (= Saga-Typ: paragraph,
//            header, list, columns, toggle, synced ...), auch in verschachtelten Instanzen.
//   Eintrag  {id, type, el, parent, slot, data, json()}:
//              el      das Blockelement (Traeger von data-block-*)
//              parent  ID des umgebenden Blocks (columns/toggle/synced) oder null (oberste Ebene)
//              slot    Nummer des Behaelters im Elternblock: Spalte 0..5 bei columns, sonst 0; oberste Ebene 0
//              data    Promise -> data des Blocks (wie gespeichert; Editor.js liest die Daten asynchron)
//              json()  Promise -> {id, type, data, tunes} (tunes: {} wenn keine) oder null (Block weg)
//   Behaelter oberste Ebene, eine Spalte oder der Inhalt von Toggle/synchronisiertem Block. Geschwister = gleicher
//            Behaelter (parent + slot).
//
// IDs: Block-IDs sind seitenweit eindeutig (Server fix_block_ids). Gibt es doch eine Dublette (z. B. kurz nach dem
// Einfuegen aus der Zwischenablage), meint jede Methode den ERSTEN Block in Dokumentreihenfolge. Eine ID meint immer
// genau einen Block, egal in welcher Ebene er steht; Einfuegen "nach id" fuegt in DENSELBEN Behaelter ein.
//
// Methoden (Mutationen geben ein Promise zurueck; ist der Editor bereit, ist die Aenderung beim Rueckkehren schon
// im DOM - das Promise loest dann sofort auf):
//   blocks()                    -> [Eintrag] aller Bloecke in Dokumentreihenfolge (Elternblock vor seinen Kindern,
//                                  Spalten von links nach rechts). Zugeklappte Toggles: Editor.js baut den Inhalt erst
//                                  beim ersten Aufklappen - bis dahin fehlen dessen Bloecke (Tiptap: immer da).
//   current()                   -> Eintrag des Blocks mit Fokus/Schreibmarke (innerster) oder null
//   byId(id)                    -> Eintrag oder null
//   insertAfter(ref, blocks, o) -> Promise [neue IDs]. ref: Block-ID (dahinter, gleicher Behaelter) | null (Ende der
//                                  obersten Ebene) | {parent: id|null, slot: n, at: 'start'|'end'} (Anfang/Ende eines
//                                  Behaelters). blocks: [{type, data, tunes?, id?}] - id wird uebernommen, sonst neu
//                                  vergeben; von tunes uebernimmt Editor.js nur hkstyle (Farbe).
//                                  o.focus: 'start'|'end' setzt die Schreibmarke in den ersten neuen Block.
//   replace(ids, blocks, o)     -> Promise [neue IDs]. Ersetzt die Bloecke (alle im selben Behaelter) durch blocks an
//                                  der Stelle des ersten. Sonderfall genau ein Block durch einen gleichen Typs mit
//                                  derselben id: Daten/tunes werden in place aktualisiert (ID, Farbe bleiben).
//                                  blocks leer = remove(ids). o.focus wie oben.
//   remove(ids)                 -> Promise [entfernte IDs]. Leerer Behaelter bekommt einen leeren Absatz (Editor.js).
//   move(id, dir)               -> true|false: Block um dir (-1/+1) im Behaelter verschieben (Zusatz zum Konzept:
//                                  Cmd+Shift+Pfeil - Verschieben ohne Neuaufbau).
//   select(ids)                 -> Mehrfachauswahl GENAU auf diese Bloecke setzen ([] hebt sie auf)
//   selected()                  -> [Eintrag] aller ausgewaehlten Bloecke in Dokumentreihenfolge, ueber alle Ebenen
//                                  (auch mehrere Behaelter). Wer nur eine Gruppe braucht, gruppiert nach parent+slot.
//   focus(id, 'start'|'end')    -> true|false: Schreibmarke an Anfang/Ende des Blocks
//   onChange(cb)                -> Abmeldefunktion. cb({ids}) bei jeder Inhaltsaenderung in irgendeinem Behaelter
//                                  (nicht waehrend die Seite laedt); ids = betroffene Bloecke, soweit bekannt.
//   kind                        -> 'editorjs' (T2: 'tiptap')
(function () {
  'use strict';
  if (!window.HKPages || !HKPages.k) return;
  var K = HKPages.k, BOX = document.getElementById('pg-editor');
  if (!BOX) return;
  var AID = 'data-block-id', ATY = 'data-block-type', SEL = 'ce-block--selected';

  // --- Editor.js-Zuordnung: Blockelement <-> Instanz ----------------------------------- editorjs-only (T7) ---------
  function holderOf(blk) {                    // Blockelement -> Element des Editors (Behaelter), in dem es steht
    var ui = blk && blk.parentElement && blk.parentElement.closest('.codex-editor');
    return ui ? ui.parentElement : null;
  }
  function edMap() {                          // Behaelter-Element -> Editor.js-Instanz
    var m = new Map(), main = K.editor();
    if (main) m.set(BOX, main);
    K.eds().forEach(function (x) { if (x.ed && x.holder) m.set(x.holder, x.ed); });
    return m;
  }
  function ready(ed) { return !!(ed && ed.blocks && typeof ed.blocks.getBlocksCount === 'function'); }
  function tagEditor(holder, ed) {            // alle Bloecke einer Instanz mit data-block-id/-type versehen
    if (!ready(ed)) { if (ed && ed.isReady) ed.isReady.then(function () { if (holder.isConnected) tagEditor(holder, ed); }, function () {}); return; }
    try {
      for (var i = 0, n = ed.blocks.getBlocksCount(); i < n; i++) {
        var b = ed.blocks.getBlockByIndex(i), h = b && b.holder;
        if (!h) continue;
        if (h.getAttribute(AID) !== b.id) h.setAttribute(AID, b.id);
        if (h.getAttribute(ATY) !== b.name) h.setAttribute(ATY, b.name);
      }
    } catch (e) { /* Instanz wird gerade abgebaut */ }
  }
  // Neue Blockelemente (Laden, Einfuegen, Umwandeln, Verschieben, Ziehen, Einfuegen aus der Zwischenablage ...) erkennt
  // ein MutationObserver: nur hinzugefuegte Elemente zaehlen, Tippen (Textknoten) kostet fast nichts; je betroffener
  // Instanz ein Durchlauf ueber ihre Bloecke. Attribute am Blockelement loesen bei Editor.js keine Aenderung aus.
  function scan(list) {
    var hs = new Set();
    for (var i = 0; i < list.length; i++) {
      var add = list[i].addedNodes;
      for (var j = 0; j < add.length; j++) {
        var n = add[j]; if (n.nodeType !== 1) continue;
        if (n.classList.contains('ce-block')) { hs.add(holderOf(n)); continue; }
        if (!n.firstElementChild) continue;
        var inner = n.querySelectorAll('.ce-block');
        for (var k = 0; k < inner.length; k++) hs.add(holderOf(inner[k]));
      }
    }
    hs.delete(null);
    if (!hs.size) return;
    var m = edMap();
    hs.forEach(function (h) { var ed = m.get(h); if (ed) tagEditor(h, ed); });
  }
  var mo = new MutationObserver(scan);
  mo.observe(BOX, { childList: true, subtree: true });
  function flush() { var r = mo.takeRecords(); if (r.length) scan(r); }
  function tagAll() { edMap().forEach(function (ed, h) { tagEditor(h, ed); }); }
  document.addEventListener('hk-page-ready', function () { flush(); tagAll(); });

  // --- Eintraege ------------------------------------------------------------------------------------------------------
  function blockEls() { return BOX.querySelectorAll('[' + AID + ']'); }
  function elById(id) {
    if (id == null) return null;
    flush();
    try { return BOX.querySelector('[' + AID + '="' + CSS.escape(String(id)) + '"]'); } catch (e) { return null; }
  }
  function parentEl(el) { var p = el.parentElement && el.parentElement.closest('[' + AID + ']'); return p && BOX.contains(p) ? p : null; }
  function slotsOf(pel, m) {                  // Behaelter eines Blocks (Spalten von links nach rechts)
    var hs = [];
    m.forEach(function (ed, h) { if (h !== BOX && pel.contains(h) && h.closest('[' + AID + ']') === pel) hs.push(h); });
    hs.sort(function (a, b) { return a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1; });
    return hs;
  }
  function edOfEl(el, m) { var h = holderOf(el); return h ? (m || edMap()).get(h) || null : null; }
  function entry(el, m, cache) {
    var id = el.getAttribute(AID), pel = parentEl(el), slot = 0;
    if (pel) {
      var key = pel.getAttribute(AID), hs = cache && cache[key];
      if (!hs) { hs = slotsOf(pel, m); if (cache) cache[key] = hs; }
      slot = Math.max(0, hs.indexOf(holderOf(el)));
    }
    var e = { id: id, type: el.getAttribute(ATY) || '', el: el, parent: pel ? pel.getAttribute(AID) : null, slot: slot,
              json: function () { return readJson(el, id); } };
    Object.defineProperty(e, 'data', { enumerable: true, get: function () { return readJson(el, id).then(function (x) { return x ? x.data : null; }); } });
    return e;
  }
  function readJson(el, id) {
    var ed = edOfEl(el); if (!ready(ed)) return Promise.resolve(null);
    var b = null; try { b = ed.blocks.getById(id); } catch (e) { b = null; }
    if (!b || !b.save) return Promise.resolve(null);
    return b.save().then(function (s) { if (!s) return null;
      var tu = {}; Object.keys(s.tunes || {}).forEach(function (k) { if (s.tunes[k] != null) tu[k] = s.tunes[k]; });
      return { id: s.id || id, type: s.tool, data: s.data, tunes: tu }; }, function () { return null; });
  }
  function blocks() {
    flush();
    var m = edMap(), cache = {};
    return [].map.call(blockEls(), function (el) { return entry(el, m, cache); });
  }
  function byId(id) { var el = elById(id); return el ? entry(el, edMap(), {}) : null; }
  function current() {
    var a = document.activeElement; if (!a || !BOX.contains(a)) return null;
    flush();
    var m = edMap(), best = null, ed;
    m.forEach(function (x, h) { if (h !== BOX && h.contains(a) && (!best || best.contains(h))) best = h; });
    ed = best ? m.get(best) : m.get(BOX);
    if (!ready(ed)) return null;
    var i = ed.blocks.getCurrentBlockIndex(), b = i >= 0 ? ed.blocks.getBlockByIndex(i) : null;
    if (!b || !b.holder) return null;
    tagEditor(best || BOX, ed);
    return entry(b.holder, m, {});
  }

  // --- Aendern --------------------------------------------------------------------------------------------------------
  function place(ref) {                       // ref -> {ed, idx} (Einfuegestelle) oder null
    var m = edMap(), ed, el;
    if (ref == null) { ed = m.get(BOX); return ready(ed) ? { ed: ed, idx: ed.blocks.getBlocksCount() } : null; }
    if (typeof ref === 'object') {
      if (ref.parent == null) ed = m.get(BOX);
      else { el = elById(ref.parent); if (!el) return null; ed = m.get(slotsOf(el, m)[ref.slot || 0]); }
      if (!ready(ed)) return null;
      return { ed: ed, idx: ref.at === 'start' ? 0 : ed.blocks.getBlocksCount() };
    }
    el = elById(ref); ed = el && edOfEl(el, m);
    if (!ready(ed)) return null;
    var i = ed.blocks.getBlockIndex(String(ref));
    return i >= 0 ? { ed: ed, idx: i + 1 } : null;
  }
  function putBlocks(ed, idx, list, o) {      // editorjs-only (T7): blocks.insert + Farbe (insert kennt keine tunes)
    var ids = [];
    list.forEach(function (b, j) {
      var nb = ed.blocks.insert(b.type, b.data || {}, undefined, idx + j, false, false, b.id || undefined);
      if (b.tunes && b.tunes.hkstyle && K.styleBlock) K.styleBlock(nb, b.tunes.hkstyle);
      ids.push(nb.id);
    });
    if (o && (o.focus === 'start' || o.focus === 'end') && ids.length) { try { ed.caret.setToBlock(idx, o.focus); } catch (e) { /* ohne Textfeld */ } }
    return ids;
  }
  function insertAfter(ref, list, o) {
    var p = place(ref);
    if (!p) return Promise.reject(new Error('block not found'));
    return Promise.resolve(putBlocks(p.ed, p.idx, list || [], o));
  }
  function group(ids) {                       // IDs -> {ed, idx: [Index je ID]} (alle im selben Behaelter) oder null
    var m = edMap(), ed = null, idx = [];
    for (var i = 0; i < ids.length; i++) {
      var el = elById(ids[i]), e = el && edOfEl(el, m);
      if (!ready(e) || (ed && e !== ed)) return null;
      ed = e; var x = ed.blocks.getBlockIndex(String(ids[i])); if (x < 0) return null; idx.push(x);
    }
    return ed ? { ed: ed, idx: idx } : null;
  }
  function remove(ids) {
    ids = (ids || []).map(String);
    var m = edMap(), per = new Map(), done = [];
    ids.forEach(function (id) {
      var el = elById(id), ed = el && edOfEl(el, m); if (!ready(ed)) return;
      var i = ed.blocks.getBlockIndex(id); if (i < 0) return;
      if (!per.has(ed)) per.set(ed, []); per.get(ed).push(i); done.push(id);
    });
    per.forEach(function (list, ed) { list.sort(function (a, b) { return b - a; }).forEach(function (i) { ed.blocks.delete(i); }); });
    return Promise.resolve(done);
  }
  function replace(ids, list, o) {
    ids = (ids || []).map(String); list = list || [];
    if (!list.length) return remove(ids);
    var g = group(ids);
    if (!g) return Promise.reject(new Error('blocks not in one container'));
    var ed = g.ed;
    if (ids.length === 1 && list.length === 1 && list[0].id === ids[0]) {
      var cur = ed.blocks.getBlockByIndex(g.idx[0]);
      if (cur && cur.name === list[0].type) {           // in place: ID, Farbe und Position bleiben
        return ed.blocks.update(ids[0], list[0].data, list[0].tunes || undefined).then(function () {
          if (o && o.focus) focus(ids[0], o.focus);
          return [ids[0]];
        });
      }
    }
    // erst einfuegen, dann (ueber den Index, auch bei gleicher ID) loeschen - so wird der Behaelter nie leer
    var at = Math.min.apply(null, g.idx), n = list.length;
    var out = putBlocks(ed, at, list, null);
    g.idx.map(function (i) { return i + n; }).sort(function (a, b) { return b - a; }).forEach(function (i) { ed.blocks.delete(i); });
    if (o && (o.focus === 'start' || o.focus === 'end')) { try { ed.caret.setToBlock(at, o.focus); } catch (e) { /* ohne Textfeld */ } }
    return Promise.resolve(out);
  }
  function move(id, dir) {
    var g = group([String(id)]); if (!g) return false;
    var to = g.idx[0] + dir; if (to < 0 || to >= g.ed.blocks.getBlocksCount()) return false;
    g.ed.blocks.move(to, g.idx[0]);
    return true;
  }
  function focus(id, pos) {
    var g = group([String(id)]); if (!g) return false;
    try { g.ed.caret.setToBlock(g.idx[0], pos === 'start' ? 'start' : 'end'); return true; } catch (e) { return false; }
  }
  // Mehrfachauswahl: Editor.js markiert ausgewaehlte Bloecke mit .ce-block--selected (static/editor25.js beobachtet die
  // Klasse und zeigt die Leiste). editorjs-only (T7)
  function select(ids) {
    flush();
    var want = {}; (ids || []).forEach(function (id) { want[String(id)] = 1; });
    [].forEach.call(blockEls(), function (el) {
      var on = !!want[el.getAttribute(AID)];
      if (el.classList.contains(SEL) !== on) el.classList.toggle(SEL, on);
      if (on) delete want[el.getAttribute(AID)];      // Dublette: nur der erste
    });
  }
  function selected() {
    flush();
    var m = edMap(), cache = {};
    return [].map.call(BOX.querySelectorAll('[' + AID + '].' + SEL), function (el) { return entry(el, m, cache); });
  }
  function onChange(cb) { return K.onDocChange ? K.onDocChange(cb) : function () {}; }

  K.doc = { kind: 'editorjs', blocks: blocks, current: current, byId: byId, insertAfter: insertAfter, replace: replace,
            remove: remove, move: move, select: select, selected: selected, focus: focus, onChange: onChange };
})();
