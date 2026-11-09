// Links im Editor (2.5, wie Notion): einfacher Klick oeffnet externe Links im neuen Tab und Seitenverweise direkt;
// Alt/Shift-Klick und Klick nach dem Markieren oeffnen nichts (Text bleibt bearbeitbar).
// Aufruf: CK=<Cookie> SAGA_BASE=http://127.0.0.1:8099 SAGA_EDITOR=tiptap|editorjs node tests/browser_link_klick.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE || 'http://127.0.0.1:8099';
const CK = process.env.CK;
let pass = 0, fail = 0;
const ok = (name, cond, info) => { if (cond) { pass++; console.log('OK   ' + name); } else { fail++; console.log('FAIL ' + name + (info !== undefined ? ' :: ' + JSON.stringify(info).slice(0, 300) : '')); } };

(async () => {
  const br = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || undefined });
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage();
  await p.goto(BASE + '/notes');
  const engine = await D.useEditor(p);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => (await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined })).json().catch(() => ({})), [m, url, b]);
  const ziel = (await api('POST', '/api/notes', { title: 'Linkziel' })).id;
  const page = (await api('POST', '/api/notes', { title: 'Links klicken' })).id;
  await api('PATCH', '/api/notes/' + page, { format: 'blocks', body: JSON.stringify({ blocks: [
    { id: 'l1', type: 'paragraph', data: { text: 'Vorher <a href="https://example.invalid/buch/3">In BookOrbit öffnen</a> nachher' } },
    { id: 'l2', type: 'paragraph', data: { text: 'Siehe <a href="/notes#' + ziel + '">Linkziel</a>' } }] }) });
  await p.goto(BASE + '/notes?t=1#' + page);
  await p.waitForSelector(D.block('l1') + ' a[href^="https://"]', { timeout: 15000 });
  await p.waitForTimeout(800);
  // window.open mitschneiden statt echte Tabs (example.invalid ist nicht erreichbar)
  await p.evaluate(() => { window.__opened = []; window.open = function (u) { window.__opened.push(String(u)); return null; }; });
  const ext = p.locator(D.block('l1') + ' a[href^="https://"]');

  await ext.click();
  await p.waitForTimeout(500);
  let o = await p.evaluate(() => window.__opened.slice());
  ok(`${engine}: einfacher Klick oeffnet externen Link (${o})`, o.length === 1 && o[0] === 'https://example.invalid/buch/3', o);
  ok(`${engine}: Seite bleibt (kein Wechsel im selben Tab)`, (await p.evaluate(() => location.hash)) === '#' + page);

  await p.evaluate(() => { window.__opened = []; });
  await ext.click({ modifiers: ['Alt'] });
  await p.waitForTimeout(500);
  o = await p.evaluate(() => window.__opened.slice());
  ok(`${engine}: Alt-Klick oeffnet nichts (Cursor setzen)`, o.length === 0, o);

  // markieren (Doppelklick) -> nichts oeffnen
  await p.evaluate(() => { window.__opened = []; });
  await ext.dblclick();
  await p.waitForTimeout(500);
  o = await p.evaluate(() => window.__opened.slice());
  ok(`${engine}: Doppelklick zum Markieren oeffnet nichts`, o.length === 0, o);

  // Seitenverweis: einfacher Klick wechselt die Seite
  await p.evaluate(() => { window.getSelection().removeAllRanges(); window.__opened = []; });
  await p.locator(D.block('l2') + ' a').first().click();
  await p.waitForTimeout(800);
  ok(`${engine}: Seitenverweis oeffnet die Seite`, (await p.evaluate(() => location.hash)) === '#' + ziel);

  await br.close();
  console.log(`BROWSER-LINK-KLICK (${engine}) ${pass}/${pass + fail}` + (fail ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
