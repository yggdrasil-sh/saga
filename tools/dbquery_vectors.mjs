// Testvektoren fuer die serverseitigen Datenbank-Ansichten (saga/dbquery.py): Zufallsdaten durch GENAU den Code des
// Webs (static/pages.js: matchFilter, relDate, rowText, filterRows, sortKey, sortedRows, eachGrouped, calcValue2 ...)
// schicken und das Ergebnis festhalten. tests/datenbank_abfrage.py prueft den Server dagegen.
//   Aufruf: node tools/dbquery_vectors.mjs            -> schreibt tests/dbquery_vectors.json
//           node tools/dbquery_vectors.mjs --check    -> nur vergleichen (Datei unveraendert?)
import { readFileSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = readFileSync(join(ROOT, 'static/pages.js'), 'utf8');
const OUT = process.env.DBQ_OUT || join(ROOT, 'tests/dbquery_vectors.json');   // DBQ_OUT/DBQ_SEED/DBQ_DBS: mehr Faelle zum Probieren

// Funktion "function name(...) { ... }" aus pages.js herausloesen (Klammern zaehlen, Zeichenketten ueberspringen)
function fn(name) {
  const i = SRC.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('nicht gefunden: ' + name);
  let j = SRC.indexOf('{', i), depth = 0, q = null;
  for (; j < SRC.length; j++) {
    const ch = SRC[j];
    if (q) { if (ch === '\\') j++; else if (ch === q) q = null; continue; }
    if (ch === "'" || ch === '"') { q = ch; continue; }
    if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) return SRC.slice(i, j + 1);
  }
  throw new Error('Ende nicht gefunden: ' + name);
}
const NAMES = ['relDate', 'propValue', 'isFx', 'numLike', 'groupable', 'weekdayIndex', 'valueGroups', 'groupCols', 'calcValue2', 'isoDay', 'propById', 'sortKey', 'sortedRows', 'eachGrouped',
  'titleName', 'fprop', 'matchFilter', 'rowText', 'filterRows'];
const BODY = NAMES.map(fn).join('\n');
const RealDate = Date;

function sandbox(schema, rows, labels, view, q, nowMs) {
  function FakeDate(...a) { return a.length ? new RealDate(...a) : new RealDate(nowMs); }
  FakeDate.prototype = RealDate.prototype; FakeDate.now = () => nowMs;
  const f = new Function('Date', 'DB', 'CUR', 'LABELS',
    "var AUTO = { created: 'created', edited: 'updated' };\n" +
    'function T(s) { return s; }\n' +
    'function curView() { return CUR; }\n' +
    'function byId(id) { var t = LABELS.get(id); return t === undefined ? null : { id: id, title: t }; }\n' +
    "function label(p) { return p && p.title ? p.title : T('Unbenannt'); }\n" +
    BODY + '\nreturn { sortedRows: sortedRows, filterRows: filterRows, eachGrouped: eachGrouped, propById: propById, sortKey: sortKey };');
  return f(FakeDate, { schema: schema, rows: rows, q: q }, view, labels);
}

// --- Zufallsdaten (fester Startwert: reproduzierbar) ---
let seed = +(process.env.DBQ_SEED || 20261019);
function rnd() { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }
const pick = (a) => a[Math.floor(rnd() * a.length)];
const TEXT = ['', 'Alpha', 'alpha', 'Ärger', 'Straße', 'STRASSE', 'Привет мир', 'привет', 'ПРИВЕТ', 'สวัสดี', 'ไทย ภาษา', 'İstanbul', 'Ω', 'ΣΑΣ',
  'emoji 😀', '￿', '10', '9', ' 5 ', '0x10', '1e3', 'toString', 'Kunde Müller', 'b', 'B', 'a b', 0, 12, true, false, [], ['a'], {}];
const NUM = [0, 1, -1, 2.5, 1e21, 0.1 + 0.2, '7', '', 'abc', true, [3], 100, 33.333, -0.5, '1.200,5', 1e-7];
const DATE = ['2026-10-19', '2026-10-13', '2026-10-25', '2026-10-26', '2026-11-01', '2026-09-30', '2026-10-31', '0099-01-01', '2024-02-30',
  '2026-1-5', 'abc', '', 20261019, '2026-10-19T23:30', '2026-03-29', '2026-10-12', '2025-10-19'];
const TZS = ['Europe/Berlin', 'America/New_York', 'Asia/Bangkok', 'UTC'];
const NOWS = ['2026-10-19T10:00:00Z', '2026-10-31T23:30:00Z', '2026-03-29T00:30:00Z', '2026-01-01T00:10:00Z', '2026-10-25T01:30:00Z'];

function makeSchema(k) {
  const opts = [{ id: 'o1', name: 'Offen', color: 'gray' }, { id: 'o2', name: 'In Arbeit', color: 'blue' }, { id: 'o3', name: 'Готово', color: 'green' },
    { id: 'o4', name: 'เสร็จ', color: 'red' }];
  if (k % 3 === 1) opts.push({ id: 'toString', name: 'Prototyp', color: 'pink' });
  if (k % 4 === 2) opts.push({ id: 'o1', name: 'Doppelt', color: 'gray' });
  const calcOp = pick(['div', 'pct', 'mul', 'add', 'sub', 'xyz', undefined]);
  const calc = { id: 'k1', name: 'Rechnung', type: 'calc', a: 'n1' };
  if (calcOp) calc.op = calcOp;
  if (rnd() < 0.8) calc.b = 'n2';
  const props = [{ id: 't1', name: 'Text', type: 'text' }, { id: 'n1', name: 'Zahl', type: 'number' }, { id: 'n2', name: 'Zahl 2', type: 'number' },
    { id: 's1', name: 'Status', type: 'select', options: opts }, { id: 'm1', name: 'Tags', type: 'multi_select', options: opts.slice(0, 3) },
    { id: 'c1', name: 'Erledigt', type: 'checkbox' }, { id: 'd1', name: 'Datum', type: 'date' }, { id: 'u1', name: 'Link', type: 'url' },
    { id: 'r1', name: 'Projekt', type: 'relation', target: 900 }, calc, { id: 'cr', name: 'Erstellt', type: 'created' },
    { id: 'ed', name: 'Bearbeitet', type: 'edited' }, { id: 's2', name: 'Prio', type: 'select', options: opts.slice(1) }];
  return { props: props };
}
function makeValue(p) {
  switch (p.type) {
    case 'text': case 'url': return pick(TEXT);
    case 'number': return pick(NUM);
    case 'select': return pick(['o1', 'o2', 'o3', 'o4', 'zz', 'toString', ['o1'], 5, null, '']);
    case 'multi_select': return pick([[], ['o1'], ['o1', 'o2'], ['o9'], ['o3', 'o1'], 'o1', 3, {}, ['o2']]);
    case 'checkbox': return pick([true, false, true, 0, 1, 'x', '', []]);
    case 'date': return pick(DATE);
    case 'relation': return rnd() < 0.04 ? pick(['abc', 7]) : pick([[], [901], [901, 902], [903], [999], ['901'], [904, 905], [902.0]]);
  }
  return undefined;
}
function makeRows(schema, n, now) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    const props = {};
    schema.props.forEach(function (p) {
      if (p.type === 'calc' || p.type === 'created' || p.type === 'edited' || rnd() < 0.3) return;
      const v = makeValue(p); if (v !== undefined) props[p.id] = v;
    });
    const created = now / 1000 - Math.floor(rnd() * 40 * 86400) - (rnd() < 0.3 ? 0.5 : 0);
    rows.push({ id: 1000 + i, title: pick(TEXT.filter((x) => typeof x === 'string')), props: props, created: created,
      updated: created + Math.floor(rnd() * 20 * 86400) });
  }
  return rows;
}
const OPS = ['contains', 'not_contains', 'is_empty', 'not_empty', 'is', 'is_not', 'eq', 'gt', 'lt', 'before', 'after', 'today', 'this_week',
  'this_month', 'checked', 'unchecked', 'weird'];
const PIDS = ['title', 't1', 'n1', 'n2', 's1', 'm1', 'c1', 'd1', 'u1', 'r1', 'k1', 'cr', 'ed', 's2', 'nope'];
function fvalue(pid) {
  if (rnd() < 0.08) return pick(['', undefined, null]);
  if (pid === 'r1') return pick(['901', '902', '999', 'x']);
  if (pid === 's1' || pid === 's2' || pid === 'm1') return pick(['o1', 'o2', 'o3', 'zz', 'toString']);
  if (pid === 'n1' || pid === 'n2' || pid === 'k1') return pick(['0', '1', '2.5', '100', '-1', 'abc', '1e21', '33.333', '7']);
  if (pid === 'd1' || pid === 'cr' || pid === 'ed') return pick(['2026-10-19', '2026-10-01', '2026-10-30', '2025-01-01', '2026-10-19T12']);
  return pick(['a', 'ALPHA', 'привет', 'ไทย', 'ß', 'İ', '5', 'true', '😀', ' ', 'o', 'Ä']);
}
function makeView(k) {
  const v = { id: 'v' + k, name: 'Ansicht ' + k, type: pick(['table', 'table', 'list', 'board', 'gallery', 'calendar', undefined]) };
  const nf = Math.floor(rnd() * 3.5);
  if (nf) v.filters = Array.from({ length: nf }, function () { const pid = pick(PIDS); return { pid: pid, op: pick(OPS), value: fvalue(pid) }; });
  const ns = Math.floor(rnd() * 4);
  if (ns) v.sorts = Array.from({ length: ns }, function () { return { pid: pick(PIDS), dir: pick(['asc', 'desc', 'asc', undefined]) }; });
  if (rnd() < 0.1 && v.sorts) { v.sort = v.sorts[0]; delete v.sorts; }                      // altes Einzelfeld
  if (rnd() < 0.4) v.group = pick(['s1', 's2', 'm1', 'nope']);
  if (v.type === 'board') v.group_by = pick(['s1', 's2', 't1', 'nope']);
  return v;
}

// Widerspruechlicher Vergleich (z. B. Text gegen Zahl: beide Richtungen "groesser", NaN): dann haengt die Reihenfolge
// vom Sortierverfahren der JS-Engine ab (Chrome, Firefox, Safari und Versionen unterscheiden sich) - solche Faelle
// markiert "engine": der Test prueft dort nur Menge, Anzahl und Gruppen, nicht die Reihenfolge.
function consistent(S, view) {
  const keys = (view.sorts || []).map(function (s) { return { p: s.pid === 'title' ? null : S.propById(s.pid), d: s.dir === 'desc' ? -1 : 1 }; })
    .filter(function (k) { return k.p !== undefined; });
  if (!keys.length) return true;
  const rows = S.filterRows(S.rows);
  function cmp(a, b) {                           // wie sortedRows
    for (let i = 0; i < keys.length; i++) {
      const x = S.sortKey(keys[i].p, a), y = S.sortKey(keys[i].p, b);
      if (x === y) continue; if (x == null) return 1; if (y == null) return -1;
      return (x < y ? -1 : 1) * keys[i].d;
    }
    return 0;
  }
  for (let i = 0; i < rows.length; i++) for (let j = i + 1; j < rows.length; j++) {
    const a = Math.sign(cmp(rows[i], rows[j])), b = Math.sign(cmp(rows[j], rows[i]));
    if (a !== -b) return false;
    for (let k = j + 1; k < rows.length; k++) {   // Transitivitaet (kleine Listen)
      const c = Math.sign(cmp(rows[j], rows[k])), d = Math.sign(cmp(rows[i], rows[k]));
      if (a <= 0 && c <= 0 && d > 0) return false;
      if (a >= 0 && c >= 0 && d < 0) return false;
    }
  }
  return true;
}

function expected(S, view) {
  try {
    const rows = S.sortedRows(), matched = S.filterRows(S.rows).length;
    const t = view.type;
    if (t === 'gallery' || t === 'calendar') return { ids: rows.map((r) => r.id), matched: matched, groups: null };
    if (t === 'board') {                       // wie renderBoard (Spalten-Ermittlung)
      let g = S.propById(view.group_by);
      if (!g || g.type !== 'select') g = S.schema.props.find(function (p) { return p.type === 'select'; });
      const known = {}; (g.options || []).forEach(function (o) { known[o.id] = 1; });
      const cols = [{ o: null, rows: rows.filter(function (r) { return !known[r.props[g.id]]; }) }].concat(
        (g.options || []).map(function (o) { return { o: o, rows: rows.filter(function (r) { return r.props[g.id] === o.id; }) }; }));
      if (!cols[0].rows.length) cols.shift();
      return { ids: [].concat(...cols.map((c) => c.rows.map((r) => r.id))), groups: cols.map((c) => ({ id: c.o ? c.o.id : null, count: c.rows.length })), board: true, matched: matched };
    }
    const ids = [], heads = [];
    S.eachGrouped(rows, function (o, n) { heads.push({ id: o ? o.id : null, count: n }); }, function (r) { ids.push(r.id); });
    return { ids: ids, groups: heads.length || (view.group && S.propById(view.group) && S.propById(view.group).type === 'select') ? heads : null, matched: matched };
  } catch (e) {
    return { error: e.constructor.name + ': ' + e.message };
  }
}

const dbs = [], cases = [];
for (let n = 0; n < +(process.env.DBQ_DBS || 50); n++) {               // 50 Datenbanken mit je 8 Ansichten (Zeitzone/"jetzt" wechseln)
  const t0 = Date.parse(NOWS[n % NOWS.length]);
  const schema = JSON.parse(JSON.stringify(makeSchema(n)));
  const rows = JSON.parse(JSON.stringify(makeRows(schema, 18 + Math.floor(rnd() * 20), t0)));
  const labels = [[901, 'Kunde Müller'], [902, ''], [903, 'Проект Альфа'], [904, 'โครงการ'], [905, 'ALPHA beta']].filter(() => rnd() < 0.85);
  dbs.push({ schema: schema, rows: rows, labels: labels });
  for (let j = 0; j < 8; j++) {
    const k = n * 8 + j, tz = TZS[k % TZS.length], now = Date.parse(NOWS[(n + j) % NOWS.length]);
    process.env.TZ = tz;
    const view = JSON.parse(JSON.stringify(makeView(k)));
    const q = rnd() < 0.4 ? pick(['alpha', 'ПРИВЕТ', ' ไทย ', '😀', 'unbenannt', 'kunde', '1', 'true', 'o', '[object', 'straße', 'i̇', 'Готово', 'müller']) : '';
    const sc = Object.assign({}, schema, { views: [view] });
    const cur = JSON.parse(JSON.stringify(view));
    if (cur.sort) { if (!cur.sorts) cur.sorts = [cur.sort]; delete cur.sort; }   // wie curView()
    const S = sandbox(sc, rows, new Map(labels), cur, q, now);
    S.schema = sc; S.rows = rows;
    const exp = expected(S, cur);
    if (!exp.error && !consistent(S, cur)) exp.engine = true;
    // "heute" im Browser = Kalendertag der Ortszeit
    const d = new Date(now), today = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    cases.push({ k: k, db: n, tz: tz, today: today, views: [view], q: q, view: view.id, expected: exp });
  }
}
const json = JSON.stringify({ about: 'erzeugt mit tools/dbquery_vectors.mjs aus static/pages.js', dbs: dbs, cases: cases });
if (process.argv.includes('--check')) {
  const old = readFileSync(OUT, 'utf8');
  console.log(old === json + '\n' ? 'OK  Vektoren passen zu static/pages.js' : 'FAIL Vektoren veraltet - neu erzeugen');
  process.exit(old === json + '\n' ? 0 : 1);
}
writeFileSync(OUT, json + '\n');
const err = cases.filter((c) => c.expected.error).length, grp = cases.filter((c) => c.expected.groups).length;
console.log(cases.length + ' Faelle (' + err + ' mit Fehler im Web, ' + grp + ' gruppiert) -> ' + OUT);
