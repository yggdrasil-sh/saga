// Keine Scheinaenderung beim reinen Oeffnen (T6, Fund home-dash): Seite mit Datenbank-Block ohne eigene Ansichten
// (views: null, wie die Galerie-Vorlagen) oeffnen, ~9 s warten -> die Seite wird NICHT gespeichert (rev bleibt).
// makeDbView legt die Ansichten beim ersten Laden im Block ab; gespeichert werden sie erst mit der naechsten echten
// Aenderung (Editor.js-Verhalten). Laeuft in beiden Modi (SAGA_EDITOR=editorjs|tiptap; stellt die Konto-Wahl selbst ein,
// Admin editor.engine = choice) und prueft danach: nach einer echten Aenderung sind die Ansichten mitgespeichert.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> [SAGA_EDITOR=tiptap] node tests/browser_db_views_null.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
if (!BASE || !CK) { console.error('HK_TEST_BASE, CK setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], patches = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/notes'); await sleep(400);
  const api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
  const was = await api('GET', '/api/account/editor');
  if (was.choice) await D.setEditor(p, D.EDITOR);
  const db = (await api('POST', '/api/notes', { title: 'Views-null DB', kind: 'database' })).id;
  await api('POST', '/api/notes', { title: 'Zeile 1', parent_id: db });
  const pg = (await api('POST', '/api/notes', { title: 'Views-null Seite' })).id;
  await api('PATCH', '/api/notes/' + pg, { format: 'blocks', body: JSON.stringify({ blocks: [
    { id: 'v0', type: 'paragraph', data: { text: 'Oben' } },
    { id: 'v1', type: 'database', data: { db, view: null, views: null } },
    { id: 'v2', type: 'paragraph', data: { text: 'Unten' } }] }) });
  const before = await api('GET', '/api/notes/' + pg);
  p.on('request', (r) => { if (r.method() === 'PATCH' && r.url().endsWith('/api/notes/' + pg)) patches.push(r.postData()); });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pg);
  await p.waitForSelector(D.block('v1') + ' .db-row, ' + D.block('v1') + ' .db-table', { timeout: 15000 }).catch(() => null);
  ok('Editor wie erwartet (' + D.EDITOR + ')', (await D.isEditor(p)) === D.EDITOR, await D.isEditor(p));
  ok('Datenbank-Block geladen (Tabelle mit 1 Eintrag)', await p.evaluate((s) => { const b = document.querySelector(s); return !!b && !!b.querySelector('.db-table') && /1 Eintrag/.test(b.textContent); }, D.block('v1')));
  await sleep(9000);
  const after = await api('GET', '/api/notes/' + pg);
  ok(`Nur geoeffnet: nicht gespeichert (rev ${before.rev} -> ${after.rev}, ${patches.length} PATCH)`, after.rev === before.rev && !patches.length, patches.map((x) => (x || '').slice(0, 200)));
  // langsame Datenbank-Antwort: Ansichten entstehen erst lange nach dem Oeffnen - auch dann keine Scheinaenderung
  await p.route('**/api/notes/' + db + '**', async (r) => { if (r.request().method() === 'GET') await sleep(2500); await r.continue(); });
  const b2 = await api('GET', '/api/notes/' + pg);
  patches.length = 0;
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + pg); await sleep(9000);
  const a2 = await api('GET', '/api/notes/' + pg);
  // Editor.js vergleicht bei jeder DOM-Aenderung den ganzen Stand (edChanged) und speichert dann die neuen Ansichten mit -
  // bekannt, bleibt bis T7 (Editor.js entfaellt); geprueft wird das nur im Tiptap-Modus
  if (D.EDITOR === 'tiptap') ok(`Langsame DB: nicht gespeichert (rev ${b2.rev} -> ${a2.rev}, ${patches.length} PATCH)`, a2.rev === b2.rev && !patches.length, patches.map((x) => (x || '').slice(0, 200)));
  else console.log(`     Hinweis Editor.js, langsame DB: rev ${b2.rev} -> ${a2.rev} (${patches.length} PATCH) - vorbestehend`);
  await p.unroute('**/api/notes/' + db + '**');
  // echte Aenderung: jetzt werden die beim Laden angelegten Ansichten mitgespeichert (wie Editor.js)
  await D.typeEnd(p, 'v2', '!');
  let bl = null;
  for (let i = 0; i < 40; i++) { bl = JSON.parse((await api('GET', '/api/notes/' + pg)).body).blocks; if (bl[2] && bl[2].data.text === 'Unten!') break; await sleep(250); }
  ok('Nach echter Aenderung gespeichert, Ansichten im Block', bl[2].data.text === 'Unten!' && Array.isArray(bl[1].data.views) && bl[1].data.views.length === 1 && bl[1].data.db === db, bl[1]);
  ok('Keine Seitenfehler', !errs.length, errs);
  for (const id of [pg, db]) await api('DELETE', '/api/notes/' + id);
  if (was.choice) await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  await br.close();
  const n = res.filter(Boolean).length;
  console.log('\nDB-VIEWS-NULL ' + n + '/' + res.length + (n === res.length ? ' ALLES GRUEN' : ' FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
