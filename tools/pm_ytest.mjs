// Gegentest mit echtem @tiptap/y-tiptap gegen pycrdt (saga/collab_model.py), Saga-Schema v1 - 2.5 Tiptap-Umbau T1.
//
//   node tools/pm_ytest.mjs      (braucht tools/pm-test/node_modules und Python mit pycrdt: PYTHON=..., sonst python3)
//
// 1. Server -> Web: Anfangszustand aus M.build (pycrdt) in ein yjs-Doc, y-tiptap baut daraus PM-Knoten mit unserem
//    Schema (yXmlFragmentToProseMirrorRootNode/initProseMirrorDoc) -> blocksFromDoc = out bzw. blocks; y-tiptap darf
//    dabei NICHTS am gemeinsamen Zustand aendern.
// 2. Web -> Server: docFromBlocks -> PM -> Yjs ueber y-tiptap (prosemirrorToYXmlFragment) -> Update an pycrdt ->
//    gleiche Y-Struktur wie M.build, M.read = out bzw. blocks.
// 3. Bearbeiten: Server-Zustand laden, im EditorState aendern, mit updateYFragment (wie das y-sync-Plugin) nach Yjs,
//    nur die Differenz an pycrdt -> M.read = erwarteter Stand.
// 4. Risiko R1: Zustand mit Knoten/Attribut/Marke, die das lokale Schema nicht kennt -> was y-tiptap daraus macht.
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const S = require(join(ROOT, 'static/pm-schema.js'));
const A = require(join(ROOT, 'static/pm-adapter.js'));
const V = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/pm_schema.json'), 'utf8'));
const MODS = process.env.PM_TEST_MODULES || join(ROOT, 'tools/pm-test/node_modules');
const rq = createRequire(join(MODS, 'x.js'));
const core = rq('@tiptap/core');
const Y = rq('yjs');
const YT = rq('@tiptap/y-tiptap');
const { Node: PMNode } = rq('@tiptap/pm/model');
const { EditorState } = rq('@tiptap/pm/state');
const schema = core.getSchema(S.extensions(core, { uniqueId: false }));
const PY = process.env.PYTHON || (existsSync('/home/claude/srvtest/venv/bin/python') ? '/home/claude/srvtest/venv/bin/python' : 'python3');

function py(mode, input) {
  const p = spawnSync(PY, [join(ROOT, 'tools/pm_ytest.py'), mode], { input: input ? JSON.stringify(input) : '', encoding: 'utf8', maxBuffer: 1 << 28 });
  if (p.status !== 0) { console.error(p.stderr); process.exit(2); }
  return JSON.parse(p.stdout);
}
const b64 = (u) => Buffer.from(u).toString('base64');
const unb64 = (s) => new Uint8Array(Buffer.from(s, 'base64'));
const R = V.resolve;
const resolve = {
  page: (b, pid) => (Object.prototype.hasOwnProperty.call(R.pages, String(pid)) ? R.pages[String(pid)] : R.page_default),
  mention: (b, u) => (Object.prototype.hasOwnProperty.call(R.people, u) ? R.people[u] : R.mention_default)
};
const st = A.stable;
let pass = 0, fail = 0;
function ok(name, cond, info) {
  if (cond) pass++;
  else { fail++; console.log('FAIL', name, info ? String(info).slice(0, 900) : ''); }
}
function loadServer(upd) {
  const doc = new Y.Doc();
  Y.applyUpdate(doc, upd);
  return doc;
}

const built = py('build');
const toServer = {};

// --- 1. Server -> Web ------------------------------------------------------------------------------------------------
for (const c of V.cases) {
  const doc = loadServer(unb64(built[c.name]));
  let changes = 0;
  doc.on('update', () => { changes++; });
  const frag = doc.getXmlFragment('prosemirror');
  try {
    const { doc: pmDoc } = YT.initProseMirrorDoc(frag, schema);
    pmDoc.check();
    const got = A.blocksFromDoc(pmDoc, { resolve }).blocks;
    ok(`S->W ${c.name}: blocks`, st(got) === st(c.out || c.blocks), `\n  erwartet ${st(c.out || c.blocks)}\n  bekommen ${st(got)}`);
    ok(`S->W ${c.name}: y-tiptap aendert den Zustand nicht`, changes === 0, changes);
    // Rohform von y-tiptap (Marken um Inline-Knoten als attrs.marks) = Vektor-pm
    const raw = A.attrsToMarks(YT.yXmlFragmentToProsemirrorJSON(frag));
    ok(`S->W ${c.name}: Y-JSON = pm`, st(norm(raw)) === st(norm(c.pm)), `\n  erwartet ${st(norm(c.pm))}\n  bekommen ${st(norm(raw))}`);
  } catch (e) {
    ok(`S->W ${c.name}: ${e.message}`, false, e.stack);
  }
  // --- 2. Web -> Server: frischer Zustand aus dem Adapter ueber y-tiptap
  try {
    const pm = A.marksToAttrs(A.docFromBlocks({ blocks: c.blocks }));
    const node = PMNode.fromJSON(schema, pm);
    const yd = new Y.Doc();
    YT.prosemirrorToYXmlFragment(node, yd.getXmlFragment('prosemirror'));
    toServer[`W->S ${c.name}`] = { update: b64(Y.encodeStateAsUpdate(yd)), case: c.name };
  } catch (e) {
    ok(`W->S ${c.name}: ${e.message}`, false, e.stack);
  }
}
function norm(v) {                                   // ohne null/leere attrs/leere content (Python schreibt [] bzw. {})
  if (Array.isArray(v)) return v.map(norm);
  if (v && typeof v === 'object') {
    const o = {};
    for (const k of Object.keys(v)) if (v[k] !== null) o[k] = norm(v[k]);
    if (typeof o.type === 'string' && o.attrs && !Object.keys(o.attrs).length) delete o.attrs;
    if (typeof o.type === 'string' && Array.isArray(o.content) && !o.content.length) delete o.content;
    if (Array.isArray(o.marks)) o.marks.sort((a, b) => S.MARKS.indexOf(a.type) - S.MARKS.indexOf(b.type));   // Yjs: Reihenfolge der Formatierung beliebig
    return o;
  }
  return v;
}

// --- 3. Bearbeiten im Editor, Differenz an den Server ------------------------------------------------------------------
const EDITS = [
  ['Knoten toggle mit Inhalt', (st0) => { const p = findPos(st0.doc, 't4'); return st0.tr.insertText(' neu', p + 1 + 4); },
    (b) => { b[0].data.blocks[1].data.blocks[0].data.text = 'tief neu'; }],
  ['Marke b', (st0) => { const p = findPos(st0.doc, 'p'); return st0.tr.addMark(p + 1, p + 2, schema.marks.i.create()); },
    (b) => { b[0].data.text = '<i>a</i> <b>x</b> z'; }],
  ['Knoten list Checkliste', (st0) => {
    let pos = -1; st0.doc.descendants((n, p) => { if (pos < 0 && n.type.name === 'litem' && n.attrs.meta && n.attrs.meta.checked === false) pos = p; });
    return st0.tr.setNodeMarkup(pos, null, { ...st0.doc.nodeAt(pos).attrs, meta: { checked: true } });
  }, (b) => { b[0].data.items[1].meta.checked = true; }],
  ['Knoten columns 2 mit Breiten', (st0) => {
    const p = findPos(st0.doc, 'c1'), n = st0.doc.nodeAt(p);
    return st0.tr.insert(p + n.nodeSize, schema.nodes.paragraph.create({ id: 'neu0000001' }, schema.text('dazu')));
  }, (b) => { b[0].data.cols[0].blocks.push({ id: 'neu0000001', type: 'paragraph', data: { text: 'dazu' } }); }],
  ['Inline-Knoten alle', (st0) => { const p = findPos(st0.doc, 'p'); return st0.tr.insertText('X', p + 1); },
    (b) => { b[0].data.text = 'X' + b[0].data.text; }]
];
function findPos(doc, id) {
  let pos = -1;
  doc.descendants((n, p) => { if (pos < 0 && n.attrs && n.attrs.id === id && S.BLOCKS.includes(n.type.name)) pos = p; });
  if (pos < 0) throw new Error('kein Block ' + id);
  return pos;
}
for (const [name, edit, expect] of EDITS) {
  const c = V.cases.find((x) => x.name === name);
  const yd = loadServer(unb64(built[name]));
  const frag = yd.getXmlFragment('prosemirror');
  const init = YT.initProseMirrorDoc(frag, schema);
  let state = EditorState.create({ doc: init.doc });
  state = state.apply(edit(state));
  const sv = Y.encodeStateVector(yd);
  yd.transact(() => YT.updateYFragment(yd, frag, state.doc, init.meta), YT.ySyncPluginKey);
  const want = JSON.parse(JSON.stringify(c.out || c.blocks));
  expect(want);
  // gleiche Materialisierung im Web
  ok(`Edit ${name}: Web blocksFromDoc`, st(A.blocksFromDoc(state.doc, { resolve }).blocks) === st(want),
    st(A.blocksFromDoc(state.doc, { resolve }).blocks));
  toServer[`Edit ${name}`] = { update: b64(Y.encodeStateAsUpdate(yd, sv)), base: name, case: name, want };
}

// --- 4. R1: unbekannter Knoten / unbekanntes Attribut / unbekannte Marke ------------------------------------------
const r1 = {};
for (const kind of ['node', 'attr', 'mark', 'nodeattr_inline']) {
  const key = `R1 ${kind}`;
  const yd = loadServer(unb64(built[key]));
  const frag = yd.getXmlFragment('prosemirror');
  const sv0 = Y.encodeStateVector(yd);
  let changed = 0;
  yd.on('update', () => { changed++; });
  const init = YT.initProseMirrorDoc(frag, schema);
  const afterLoad = { changed, kids: frag.length, json: YT.yXmlFragmentToProsemirrorJSON(frag) };
  // naechste lokale Aenderung im Block "a" (nur dort), wie das y-sync-Plugin sie schreibt
  let state = EditorState.create({ doc: init.doc });
  state = state.apply(state.tr.insertText('!', findPos(state.doc, 'a') + 1 + 4));
  yd.transact(() => YT.updateYFragment(yd, frag, state.doc, init.meta), YT.ySyncPluginKey);
  r1[kind] = { afterLoad, after: YT.yXmlFragmentToProsemirrorJSON(frag) };
  toServer[`R1 ${kind}`] = { update: b64(Y.encodeStateAsUpdate(yd, sv0)), base: key };
}

const res = py('check', toServer);
for (const [k, r] of Object.entries(res)) {
  if (k.startsWith('R1 ')) continue;
  ok(`${k}: Server`, r.ok, r.error || `\n  y        ${r.y}\n  y_server ${r.y_server}\n  blocks ${JSON.stringify(r.blocks)}`);
}

// R1-Befund festhalten (erwartetes Verhalten von y-tiptap 3.0.x - aendert es sich, faellt der Test auf)
const blk = (k) => (res[k].blocks || []).map((b) => b.type + ':' + (b.data.text || ''));
const yattrs = (j, i) => (j.content[i] && j.content[i].attrs) || {};
ok('R1 Knoten: y-tiptap LOESCHT den unbekannten Block schon beim Laden aus dem gemeinsamen Zustand',
  r1.node.afterLoad.changed > 0 && r1.node.afterLoad.kids === 3 && !JSON.stringify(r1.node.after).includes('zukunft'), JSON.stringify(r1.node));
ok('R1 Knoten: Server uebernimmt das Loeschen (Block weg)', st(blk('R1 node')) === st(['paragraph:eins!', 'paragraph:zwei', 'paragraph:drei']), blk('R1 node'));
ok('R1 Attribut: beim Laden bleibt es, beim naechsten Schreiben DES BLOCKS wird es geloescht, andere Bloecke behalten es',
  r1.attr.afterLoad.changed === 0 && !('zukunft' in yattrs(r1.attr.after, 0)) && 'zukunft' in yattrs(r1.attr.after, 1), JSON.stringify(r1.attr.after));
ok('R1 Marke: y-tiptap LOESCHT den ganzen Text des Blocks beim Laden', r1.mark.afterLoad.changed > 0 &&
  st(blk('R1 mark')) === st(['paragraph:eins!', 'paragraph:', 'paragraph:drei']), blk('R1 mark'));
ok('R1 Inline-Attribut: bleibt, solange der Block nicht geschrieben wird', r1.nodeattr_inline.afterLoad.changed === 0 &&
  JSON.stringify(r1.nodeattr_inline.after).includes('zukunft'));
if (process.argv.includes('-v')) console.log(JSON.stringify({ r1, res: Object.fromEntries(Object.entries(res).filter(([k]) => k.startsWith('R1'))) }, null, 1));

console.log(`Y-TIPTAP ${pass}/${pass + fail} (${V.cases.length} Vektoren, y-tiptap ${JSON.parse(readFileSync(join(MODS, '@tiptap/y-tiptap/package.json'))).version}, yjs ${JSON.parse(readFileSync(join(MODS, 'yjs/package.json'))).version})`,
  fail ? 'FEHLER' : 'ALLES GRUEN');
process.exit(fail ? 1 : 0);
