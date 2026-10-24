// Bughunt W01: Seite mit Code-Block im Ausklappbar-Block speichert (zu- und aufgeklappt), Inhalt und Formatierung bleiben.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<signiertes Sitzungs-Cookie> node tests/browser_toggle_code.js
const PW = require('playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
(async () => {
  const br = await PW.chromium.launch();
  const ctx = await br.newContext({ viewport: { width: 1100, height: 800 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => { const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }); return r.json().catch(() => ({})); }, [m, url, b]);
  const id = (await api('POST', '/api/notes', { title: 'Toggle Code' })).id;
  const blocks = { blocks: [
    { type: 'paragraph', data: { text: 'Start' } },
    { type: 'toggle', data: { title: 'Klapp <b>fett</b>', open: false, blocks: [
      { type: 'code', data: { code: 'if (a < b) { x = "<b>"; }', language: 'js' } },
      { type: 'paragraph', data: { text: 'innen <b>fett</b> und <code class="inline-code">c</code>' } }] } },
    { type: 'paragraph', data: { text: 'Ende' } }] };
  await api('PATCH', '/api/notes/' + id, { body: JSON.stringify(blocks), format: 'blocks' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector('.tgl'); await p.waitForTimeout(800);
  const last = p.locator('.ce-paragraph', { hasText: 'Ende' }).last();
  await last.click(); await p.keyboard.press('End'); await p.keyboard.type(' getippt');
  await p.waitForTimeout(3500);
  const st = await p.evaluate(() => (document.getElementById('pg-state') || document.querySelector('.pg-state') || {}).textContent || '');
  let n = await api('GET', '/api/notes/' + id), b = JSON.parse(n.body || '{}');
  const flat = JSON.stringify(b);
  ok('Seite mit Code im Toggle speichert (zugeklappt)', /getippt/.test(flat), st);
  const tg = (b.blocks || []).find((x) => x.type === 'toggle') || { data: {} };
  ok('Code im Toggle unveraendert', tg.data.blocks && tg.data.blocks[0].data.code === 'if (a < b) { x = "<b>"; }', tg.data.blocks);
  ok('Formatierung im Toggle-Inhalt bleibt', tg.data.blocks && /<b>fett<\/b>/.test(tg.data.blocks[1].data.text) && /inline-code/.test(tg.data.blocks[1].data.text), tg.data.blocks && tg.data.blocks[1]);
  ok('Toggle-Titel behaelt Fett', /<b>fett<\/b>/.test(tg.data.title || ''), tg.data.title);
  // aufgeklappt
  await p.click('.tgl-btn'); await p.waitForTimeout(800);
  await last.click(); await p.keyboard.press('End'); await p.keyboard.type(' zwei');
  await p.waitForTimeout(3500);
  n = await api('GET', '/api/notes/' + id); b = JSON.parse(n.body || '{}');
  const tg2 = (b.blocks || []).find((x) => x.type === 'toggle') || { data: {} };
  ok('Speichert auch aufgeklappt', /zwei/.test(JSON.stringify(b)) && tg2.data.open === true);
  ok('Code aufgeklappt unveraendert', tg2.data.blocks && tg2.data.blocks[0].data.code === 'if (a < b) { x = "<b>"; }', tg2.data.blocks && tg2.data.blocks[0]);
  ok('Keine Seitenfehler', !errs.length, errs);
  await api('DELETE', '/api/notes/' + id);
  await br.close();
  console.log('W01 ' + res.filter(Boolean).length + '/' + res.length);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
