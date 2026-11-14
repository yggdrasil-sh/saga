// MCP-Nachrunde (2.5, mcp2): eine per MCP (create_database views / update_database_schema add_views) angelegte Ansicht
// mit Filter und Sortierung zeigt das Web richtig an - Zaehler, Filter-Chip, Sortier-Chip und die Filterzeile
// (Eigenschaft, Operator, Wert) wie bei einer im Web angelegten Ansicht. Nur Lesen: das Oeffnen speichert nichts.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a mit sec_at> node tests/browser_mcp_ansicht.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
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
  const tok = (await api('POST', '/api/account/api-tokens', { name: 'MCP Ansicht Browser', scopes: ['mcp', 'write:pages', 'write:db'] })).token;
  let n = 0;
  const tool = async (name, args) => {
    const r = await fetch(BASE + '/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok, 'MCP-Protocol-Version': '2025-06-18' },
      body: JSON.stringify({ jsonrpc: '2.0', id: ++n, method: 'tools/call', params: { name, arguments: args } }) });
    const d = await r.json(); const txt = (d.result.content || []).map((c) => c.text || '').join('');
    let body = {}; try { body = JSON.parse(txt.split('">\n')[1].split('\n</saga-data')[0]); } catch (e) { body = {}; }
    return { err: d.result.isError, txt, body };
  };
  const c = await tool('create_database', { title: 'Browser Meilensteine', properties: [
    { name: 'Meilenstein', type: 'select', options: ['2.4', '2.5'] }, { name: 'Fertig', type: 'checkbox' }, { name: 'Status', type: 'status' }, { name: 'Fällig', type: 'date' }],
    views: [{ type: 'table', name: 'Alle' }, { type: 'table', name: '2.5', filters: [{ property: 'Meilenstein', op: 'is', value: '2.5' }], sorts: [{ property: 'Fällig', dir: 'desc' }] }] });
  const D = c.body.id;
  ok('create_database per MCP', !c.err && D, c.txt.slice(0, 300));
  for (const [t, m] of [['A1', '2.5'], ['A2', '2.5'], ['A3', '2.5'], ['B1', '2.4'], ['B2', '2.4']]) await tool('create_row', { database_id: D, title: t, properties: { Meilenstein: m, 'Fällig': '2026-11-0' + t[1] } });
  const u = await tool('update_database_schema', { id: D, add_views: [{ type: 'table', name: 'Bald offen', filters: [
    { property: 'Status', op: 'status_group', value: 'open' }, { property: 'Fällig', op: 'next_days', value: 7 }, { property: 'Fertig', op: 'unchecked' }] }] });
  ok('add_views mit drei Filtern per MCP', !u.err, u.txt.slice(0, 300));
  const before = await api('GET', '/api/notes/' + D);
  p.on('request', (r) => { if (r.method() === 'PATCH' && r.url().includes('/api/notes/' + D)) patches.push(r.postData()); });

  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + D);
  await p.waitForSelector('.db-tabs .db-tab', { timeout: 15000 });
  const tab = async (name) => { await p.click(`.db-tab:has(.db-tab-t:text-is("${name}"))`); await sleep(500); };
  await tab('2.5');
  const bar = await p.evaluate(() => ({ count: (document.querySelector('.db-count') || {}).textContent, filter: (document.querySelector('.db-filter') || {}).textContent,
    sort: [...document.querySelectorAll('.db-bar .db-chip.on')].map((x) => x.textContent).join('|'),
    rows: [...document.querySelectorAll('.db-table tbody tr')].map((tr) => tr.textContent) }));
  ok('Ansicht 2.5: Zaehler 3 von 5', /3 von 5/.test(bar.count || ''), bar);
  ok('Filter-Chip "Filter · 1", Sortier-Chip Fällig absteigend', /Filter · 1/.test(bar.filter || '') && /Sortiert: Fällig ↓/.test(bar.sort), bar);
  ok('Zeilen nur 2.5, absteigend nach Fällig', bar.rows.length === 3 && /03\.11\.2026/.test(bar.rows[0]) && /01\.11\.2026/.test(bar.rows[2]) && bar.rows.every((r) => /2\.5/.test(r)), bar.rows);
  await p.click('.db-filter'); await sleep(300);
  const f = await p.evaluate(() => [...document.querySelectorAll('.flt-box .flt')].map((l) => { const v = l.querySelector('.flt-val'); return {
    name: l.querySelector('.flt-name').textContent, op: l.querySelector('.flt-op').value, opText: l.querySelector('.flt-op').selectedOptions[0].textContent,
    val: v ? (v.tagName === 'SELECT' ? v.selectedOptions[0].textContent : v.value) : null }; }));
  ok('Filterzeile: Meilenstein / ist / 2.5', f.length === 1 && f[0].name === 'Meilenstein' && f[0].op === 'is' && f[0].opText === 'ist' && f[0].val === '2.5', f);
  await p.keyboard.press('Escape'); await sleep(200);
  await tab('Bald offen');
  await p.click('.db-filter'); await sleep(300);
  const f2 = await p.evaluate(() => [...document.querySelectorAll('.flt-box .flt')].map((l) => { const v = l.querySelector('.flt-val'); return {
    name: l.querySelector('.flt-name').textContent, op: l.querySelector('.flt-op').value, val: v ? (v.tagName === 'SELECT' ? v.value : v.value) : null }; }));
  ok('Filterzeilen: Status-Gruppe offen, nächste 7 Tage, nicht angehakt', f2.length === 3 && f2[0].op === 'status_group' && f2[0].val === 'open'
     && f2[1].op === 'next_days' && f2[1].val === '7' && f2[2].op === 'unchecked' && f2[2].val === null, f2);
  await p.keyboard.press('Escape'); await sleep(1500);
  const after = await api('GET', '/api/notes/' + D);
  ok('Nur angesehen: Schema nicht neu gespeichert', !patches.length && JSON.stringify(after.schema.views) === JSON.stringify(before.schema.views), patches.map((x) => (x || '').slice(0, 200)));
  ok('Keine Seitenfehler', !errs.length, errs);
  await api('DELETE', '/api/notes/' + D);
  const toks = (await api('GET', '/api/account/api-tokens')).tokens || [];
  for (const t of toks) if (t.name === 'MCP Ansicht Browser' && !t.revoked) await api('DELETE', '/api/account/api-tokens/' + t.id).catch(() => null);
  await br.close();
  const k = res.filter(Boolean).length;
  console.log('\nMCP-ANSICHT ' + k + '/' + res.length + (k === res.length ? ' ALLES GRUEN' : ' FEHLER'));
  process.exit(k === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
