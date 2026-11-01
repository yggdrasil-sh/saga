// Helfer fuer Browser-Tests am Seiten-Editor (Saga 2.5, Tiptap-Umbau T1). Tests finden Bloecke NUR ueber die Attribute
// data-block-id / data-block-type und die Editor-Fassade HKPages.k.doc (static/editor-doc.js) - so laufen sie in T2
// unveraendert gegen Editor.js und Tiptap. Editor-eigene Bedienelemente (Block-Menue, Slash-Menue, Inline-Leiste)
// stehen gesammelt unter UI; nur diese Stelle aendert sich beim Editorwechsel.
//
//   const D = require('./lib/editor_dom');
//   await p.click(D.text('a1'));                 // Textfeld des Blocks a1
//   await D.typeEnd(p, 'a1', ' mehr');           // ans Ende tippen
//   const n = await D.count(p, { top: true });   // Bloecke der obersten Ebene
'use strict';

const ROOT = '#pg-editor';
const q = (s) => String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');

// --- Selektoren (CSS, fuer page.click / locator / waitForSelector / querySelector) -------------------------------------
// Blockelement mit dieser ID
const block = (id) => `${ROOT} [data-block-id="${q(id)}"]`;
// alle Blockelemente eines Typs (oder alle Bloecke)
const ofType = (type) => type ? `${ROOT} [data-block-type="${q(type)}"]` : `${ROOT} [data-block-id]`;
// Bloecke der obersten Ebene (in keinem anderen Block)
const top = (type) => `${ofType(type)}:not([data-block-id] [data-block-id])`;
// Textfeld(er) in einem Blockelement: die aeussersten contenteditable-Elemente darin (Editor.js; auch nur lesend mit
// contenteditable="false"; Knoepfe wie Emoji/Kommentarzahl und Chips in einem Textfeld zaehlen nicht) - oder das
// Blockelement selbst, wenn es keines enthaelt (Tiptap: ein contenteditable fuer das ganze Dokument).
// Mehrere Felder (Zitat: Text + Quelle): das erste ist der Haupttext; Locator dann mit .first().
const ED = '[contenteditable]:not(button):not([contenteditable] *)';
const editIn = (b) => `${b} ${ED}, ${b}:not(:has(${ED}))`;
const text = (id) => editIn(block(id));
// Textfelder aller Bloecke eines Typs (paragraph, header, quote ...) in Dokumentreihenfolge; scope: nur in diesem
// Element (z. B. D.ofType('synced') oder D.block('c1'))
const textOf = (type, scope) => editIn(scope ? `${scope} [data-block-type="${q(type)}"]` : ofType(type));
// Textfelder der Bloecke eines Typs auf oberster Ebene
const topText = (type) => editIn(top(type));
const any = `${ROOT} [data-block-id]`;
// erstes Textfeld (Locator .first()) aller Bloecke - "irgendein Absatz" fuer waitForSelector
const anyText = textOf('paragraph');

// Editor-eigene Bedienelemente (Editor.js 2.31). editorjs-only (T7): in T2 fuer Tiptap ergaenzen
const UI = {
  blockMenuBtn: '.ce-toolbar__settings-btn',                  // Griff ⋮⋮ (Block-Menue)
  plusBtn: `${ROOT} .ce-toolbar--opened .ce-toolbar__plus`,   // "+" links am Block
  toolbar: `${ROOT} .ce-toolbar`,
  toolbarOpenClass: 'ce-toolbar--opened',                     // Klasse der sichtbaren Werkzeugleiste
  blockMenu: '.ce-settings',                                  // geoeffnetes Block-Menue
  popover: '.ce-popover--opened',                             // geoeffnetes Menue (Block-Menue, "/"-Menue)
  popoverContainer: '.ce-popover__container',
  popoverItems: '.ce-popover__items',
  item: '.ce-popover-item',
  itemHidden: '.ce-popover-item--hidden',
  itemTitle: '.ce-popover-item__title',
  search: '.ce-popover__search',
  nothingFound: '.ce-popover__nothing-found-message--displayed',
  inlineTool: '.ce-inline-tool',                              // Knopf der Inline-Leiste
  editorBox: `${ROOT} .codex-editor`,                         // Flaeche des Editors (Breite/Lage)
};
// sichtbare Eintraege (Titel) des offenen Menues
const UI_ITEMS = `${UI.popover} ${UI.item}:not(${UI.itemHidden}) ${UI.itemTitle}`;
// Menue-Eintrag mit genau diesem Titel (Playwright-Selektor)
const menuItem = (title) => `${UI.popover} ${UI.item}:has(${UI.itemTitle}:text-is("${q(title)}"))`;

// --- Funktionen (page = Playwright-Page) ---------------------------------------------------------------------------------
// Fassade im Browser: blocks() als schlichte Liste {id, type, parent, slot, text}
async function blocks(page) {
  return page.evaluate(() => (window.HKPages && HKPages.k.doc ? HKPages.k.doc.blocks() : [])
    .map((b) => ({ id: b.id, type: b.type, parent: b.parent, slot: b.slot, text: (b.el.textContent || '').trim() })));
}
// Anzahl Bloecke; o.top: nur oberste Ebene; o.type: nur dieser Typ; o.parent: nur Kinder dieses Blocks
async function count(page, o = {}) {
  return (await blocks(page)).filter((b) => (!o.top || b.parent == null) && (!o.type || b.type === o.type)
    && (o.parent === undefined || b.parent === o.parent)).length;
}
// ID des ersten Blocks, auf den alles passt: {type, text (Teiltext oder RegExp), parent, top}; sonst null
async function find(page, o = {}) {
  const l = await blocks(page);
  const hit = l.find((b) => (!o.type || b.type === o.type) && (!o.top || b.parent == null)
    && (o.parent === undefined || b.parent === o.parent)
    && (o.text === undefined || (o.text instanceof RegExp ? o.text.test(b.text) : b.text.includes(o.text))));
  return hit ? hit.id : null;
}
// sichtbarer Text eines Blocks (Textfeld) oder null
async function textOfBlock(page, id) {
  return page.evaluate((s) => { const e = document.querySelector(s); return e ? e.textContent : null; }, text(id));
}
// Blockelement-HTML (innerHTML des Textfelds) oder null
async function htmlOf(page, id) {
  return page.evaluate((s) => { const e = document.querySelector(s); return e ? e.innerHTML : null; }, text(id));
}
// Schreibmarke an Anfang/Ende des Blocks (ueber die Fassade); false, wenn es den Block nicht gibt
async function focus(page, id, pos = 'end') {
  return page.evaluate(([id, pos]) => HKPages.k.doc.focus(id, pos), [id, pos]);
}
// in das Textfeld klicken und die Schreibmarke ans Ende setzen (wie ein Mensch: Klick, dann Ende per Auswahl)
async function clickEnd(page, id) {
  await page.click(text(id));
  await page.evaluate((s) => {
    const e = document.querySelector(s); if (!e) return;
    const r = document.createRange(); r.selectNodeContents(e); r.collapse(false);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
  }, text(id));
}
// am Blockende tippen
async function typeEnd(page, id, s, delay = 20) {
  await clickEnd(page, id);
  await page.keyboard.type(s, { delay });
}
// Mehrfachauswahl GENAU auf diese Bloecke setzen (Fassade; die Auswahl-Leiste erscheint wie bei Ziehen/Umschalt+Pfeil)
async function select(page, ids) {
  await page.evaluate((ids) => { HKPages.k.doc.select(ids); if (window.HKEd25 && HKEd25.updateBar) HKEd25.updateBar(); }, ids);
}
async function selected(page) { return page.evaluate(() => HKPages.k.doc.selected().map((b) => b.id)); }
// ID des Blocks mit der Schreibmarke oder null
async function current(page) { return page.evaluate(() => { const c = HKPages.k.doc.current(); return c ? c.id : null; }); }
// warten, bis der Block da ist (Seite geladen)
async function waitBlock(page, id, timeout) { return page.waitForSelector(block(id), timeout ? { timeout } : undefined); }

module.exports = { ROOT, block, ofType, top, text, textOf, topText, editIn, any, anyText, UI, UI_ITEMS, menuItem,
  blocks, count, find, textOfBlock, htmlOf, focus, clickEnd, typeEnd, select, selected, current, waitBlock };
