// Prueft den Web-Adapter static/pm-adapter.js und das Schema static/pm-schema.js gegen tests/vectors/pm_schema.json.
//
//   node tools/pm_vectors.mjs            alle Pruefungen (braucht tools/pm-test/node_modules: cd tools/pm-test && npm ci)
//   node tools/pm_vectors.mjs --dump     nur Adapter-Ausgabe {name: blocks} als JSON auf stdout (ohne npm-Pakete;
//                                        tests/pm_vectors.py prueft damit den clean_blocks-Fixpunkt)
//   PM_TEST_MODULES=/pfad/node_modules   anderer Ort der Test-Pakete
//
// Je Fall: blocks -> pm (= Vektor), pm -> blocks frisch (= out bzw. blocks, sortierte Schluessel), WeakMap-Durchreichung
// byte-gleich (JSON- und echte Knoten), Schema-Validitaet (Node.fromJSON + check), html_norm, Altformat-Toggle (upgrade);
// dazu Schema-Pruefsumme = Server und eine Bearbeitung im echten EditorState (nur der geaenderte Block neu).
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const S = require(join(ROOT, 'static/pm-schema.js'));
const A = require(join(ROOT, 'static/pm-adapter.js'));
const V = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/pm_schema.json'), 'utf8'));
const R = V.resolve;
const resolve = {
  page: (bid, pid) => (Object.prototype.hasOwnProperty.call(R.pages, String(pid)) ? R.pages[String(pid)] : R.page_default),
  mention: (bid, u) => (Object.prototype.hasOwnProperty.call(R.people, u) ? R.people[u] : R.mention_default)
};
const st = A.stable;
const ctxOf = (x) => ({ resolve, time: 1, version: 'v' });

if (process.argv.includes('--dump')) {
  const out = {};
  for (const c of V.cases) out[c.name] = A.blocksFromDoc(A.docFromBlocks({ blocks: c.blocks }), ctxOf(c)).blocks;
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

const MODS = process.env.PM_TEST_MODULES || join(ROOT, 'tools/pm-test/node_modules');
const req2 = createRequire(join(MODS, 'x.js'));
const core = req2('@tiptap/core');
const { UniqueID } = req2('@tiptap/extension-unique-id');
const { Node: PMNode } = req2('@tiptap/pm/model');
const { EditorState } = req2('@tiptap/pm/state');
const schema = core.getSchema(S.extensions({ ...core, UniqueID }));

let pass = 0, fail = 0;
function ok(name, cond, info) {
  if (cond) pass++;
  else { fail++; console.log('FAIL', name, info ? String(info).slice(0, 700) : ''); }
}

ok(`Schema-Pruefsumme = Server (${S.hash()})`, S.hash() === V.schema_hash, V.schema_hash);
ok('Modell/Version', S.MODEL === V.model && S.VERSION === V.schema_ver);
ok('Schema: alle Knoten und Marken vorhanden', S.BLOCKS.concat(S.PARTS, S.INLINE_NODES).every((n) => schema.nodes[n]) &&
  S.MARKS.every((m) => schema.marks[m]) && Object.keys(schema.marks).join() === S.MARKS.join());

for (const c of V.cases) {
  const n = c.name;
  const want = c.out || c.blocks;
  try {
    const pm = A.docFromBlocks({ blocks: c.blocks });
    ok(`${n}: blocks -> pm`, st(pm) === st(c.pm), `\n  erwartet ${st(c.pm)}\n  bekommen ${st(pm)}`);
    const fresh = A.blocksFromDoc(c.pm, ctxOf(c));
    ok(`${n}: pm -> blocks (frisch)`, st(fresh.blocks) === st(want), `\n  erwartet ${st(want)}\n  bekommen ${st(fresh.blocks)}`);
    // unveraendert: dieselben Objekte, byte-gleich (auch wenn das Original nicht in Normalform ist)
    const ctx = { ...ctxOf(c), orig: new WeakMap() };
    const pass1 = A.blocksFromDoc(A.docFromBlocks({ blocks: c.blocks }, ctx), ctx);
    ok(`${n}: WeakMap byte-gleich`, pass1.blocks.length === c.blocks.length && pass1.blocks.every((b, i) => b === c.blocks[i]) &&
      JSON.stringify(pass1.blocks) === JSON.stringify(c.blocks));
    // echtes Schema
    let node = null;
    try { node = PMNode.fromJSON(schema, c.pm); node.check(); } catch (e) { node = null; ok(`${n}: Schema-Validitaet`, false, e.message); }
    if (node) {
      ok(`${n}: Schema-Validitaet`, true);
      const real = A.blocksFromDoc(node, ctxOf(c));
      ok(`${n}: echte Knoten -> blocks`, st(real.blocks) === st(want), `\n  erwartet ${st(want)}\n  bekommen ${st(real.blocks)}`);
      const rc = { ...ctxOf(c), orig: new WeakMap() };
      A.bind(node, { blocks: c.blocks }, rc);
      const rb = A.blocksFromDoc(node, rc);
      ok(`${n}: echte Knoten WeakMap byte-gleich`, rb.blocks.every((b, i) => b === c.blocks[i]));
      ok(`${n}: toJSON des Editors = pm (ohne null/leere Listen)`, st(dropNull(node.toJSON())) === st(dropNull(c.pm)));
    }
    for (const [src, norm] of c.html_norm || []) {
      const h = A.inlineToHtml(A.htmlToInline(src), resolve, 'p1');
      ok(`${n}: html_norm`, h === norm, `\n  erwartet ${norm}\n  bekommen ${h}`);
      ok(`${n}: html_norm Fixpunkt`, A.inlineToHtml(A.htmlToInline(norm), resolve, 'p1') === norm);
    }
    if (c.upgrade) {
      const up = A.docFromBlocks({ blocks: c.blocks }, { upgrade: true });
      ok(`${n}: upgrade blocks -> pm`, st(up) === st(c.upgrade.pm), `\n  erwartet ${st(c.upgrade.pm)}\n  bekommen ${st(up)}`);
      const uc = { ...ctxOf(c), orig: new WeakMap(), upgrade: true };
      const ub = A.blocksFromDoc(A.docFromBlocks({ blocks: c.blocks }, uc), uc);
      ok(`${n}: upgrade -> blocks (nicht durchgereicht)`, st(ub.blocks) === st(c.upgrade.out) && ub.blocks[0] !== c.blocks[0],
        `\n  erwartet ${st(c.upgrade.out)}\n  bekommen ${st(ub.blocks)}`);
    }
  } catch (e) {
    ok(`${n}: ${e.name}: ${e.message}`, false, e.stack);
  }
}

function dropNull(v) {
  if (Array.isArray(v)) return v.map(dropNull);
  if (v && typeof v === 'object') {
    const o = {};
    for (const k of Object.keys(v)) if (v[k] !== null) o[k] = dropNull(v[k]);
    if (typeof o.type === 'string' && o.attrs && !Object.keys(o.attrs).length) delete o.attrs;
    if (typeof o.type === 'string' && Array.isArray(o.content) && !o.content.length) delete o.content;
    return o;
  }
  return v;
}

// Bearbeitung im echten EditorState: nur der geaenderte Block wird neu geschrieben, die anderen bleiben dieselben Objekte
{
  const blocks = [{ id: 'a', type: 'paragraph', data: { text: '<strong>alt</strong>' } },
    { id: 'b', type: 'paragraph', data: { text: 'zwei' } },
    { id: 't', type: 'toggle', data: { title: 'T', open: true, blocks: [{ id: 't1', type: 'paragraph', data: { text: 'x' } },
      { id: 't2', type: 'paragraph', data: { text: '<em>y</em>' } }] } }];
  const ctx = { resolve, orig: new WeakMap() };
  let state = EditorState.create({ doc: PMNode.fromJSON(schema, A.docFromBlocks({ blocks })) });
  A.bind(state.doc, { blocks }, ctx);
  let pos = -1;
  state.doc.descendants((nd, p) => { if (pos < 0 && nd.type.name === 'paragraph' && nd.attrs.id === 't1') pos = p + 1; });
  state = state.apply(state.tr.insertText('!', pos + 1));
  const got = A.blocksFromDoc(state.doc, ctx).blocks;
  ok('Editor: unveraenderte Bloecke bleiben (auch Altformat strong)', got[0] === blocks[0] && got[1] === blocks[1]);
  ok('Editor: geaenderter Toggle frisch, unveraendertes Kind durchgereicht', got[2] !== blocks[2] &&
    got[2].data.blocks[0].data.text === 'x!' && got[2].data.blocks[1] === blocks[2].data.blocks[1]);
  // UniqueID-Konfiguration: Optionen wie vorgesehen
  const uid = S.extensions({ ...core, UniqueID }).find((e) => e.name === 'uniqueID');
  ok('UniqueID: attributeName id, nur Bloecke, 10 Zeichen', uid && uid.options.attributeName === 'id' &&
    uid.options.types.join() === S.BLOCKS.join() && /^[A-Za-z0-9]{10}$/.test(uid.options.generateID()));
  ok('UniqueID: keine Vergabe in y-sync-Transaktionen', uid.options.filterTransaction({ getMeta: (k) => (k === 'y-sync$' ? {} : undefined) }) === false &&
    uid.options.filterTransaction({ getMeta: () => undefined }) === true);
}

// Live-Schluesselreihenfolge (ctx.like, collab_model._like): nur Reihenfolge, nie Werte - auch Schluessel "__proto__"
{
  const base = JSON.parse('{"blocks":[{"id":"U1","type":"foreign","data":{"z":1,"__proto__":{"q":1},"a":2}},' +
    '{"id":"L1","type":"list","data":{"style":"ordered","meta":{"start":2,"counterType":"upper-alpha","__proto__":{"x":1}},"items":[]}}]}');
  const ch = JSON.parse(JSON.stringify(base).replace('"a":2', '"a":3'));
  const got = A.blocksFromDoc(A.docFromBlocks(ch), { time: 1, version: 'v', like: () => A.blockIds(base.blocks) }).blocks;
  ok('Live like: Werte unveraendert (auch __proto__), Reihenfolge wie Vorlage', st(got) === st(ch.blocks) &&
    JSON.stringify(got) === JSON.stringify(ch.blocks), JSON.stringify(got));
}

// Verschachtelungsverbote im Schema
for (const [what, json] of [
  ['columns in column', { type: 'doc', content: [{ type: 'columns', content: [{ type: 'column', content: [{ type: 'columns', content: [] }] }] }] }],
  ['columns in toggle', { type: 'doc', content: [{ type: 'toggle', content: [{ type: 'ttitle' }, { type: 'columns', content: [] }] }] }],
  ['synced in synced', { type: 'doc', content: [{ type: 'synced', content: [{ type: 'synced', content: [] }] }] }],
  ['synced_ref in synced', { type: 'doc', content: [{ type: 'synced', content: [{ type: 'synced_ref' }] }] }],
  ['Marke im Code-Block', { type: 'doc', content: [{ type: 'code', content: [{ type: 'text', text: 'x', marks: [{ type: 'b' }] }] }] }],
  ['quote ohne qcap', { type: 'doc', content: [{ type: 'quote', content: [{ type: 'qtext' }] }] }]]) {
  let bad = false;
  try { PMNode.fromJSON(schema, json).check(); } catch (e) { bad = true; }
  ok(`Schema verbietet ${what}`, bad);
}

console.log(`${pass}/${pass + fail} (${V.cases.length} Vektoren)`, fail ? 'FEHLER' : 'ALLES GRUEN');
process.exit(fail ? 1 : 0);
