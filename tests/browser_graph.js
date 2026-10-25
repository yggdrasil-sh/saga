// 2.5 Graph: ganzer Graph (Seitenleiste "Graph") und lokaler Graph je Seite - laedt, zeichnet, sucht, oeffnet per Enter,
// Esc schliesst, Filter wirken, keine Seitenfehler. Gegen einen laufenden Test-Stack:
//   SAGA_BASE=http://host:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=playwright-core] [SHOTS=/ordner] node tests/browser_graph.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c || i === undefined ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function session(br, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1280, height: 860 }, locale: 'de-DE' }, opts || {}));
  const p = await ctx.newPage(); p.errs = [];
  p.on('pageerror', (e) => p.errs.push(e.message));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username]', E.SAGA_USER); await p.fill('input[type=password]', E.SAGA_PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => { const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }); return r.json().catch(() => ({})); }, [m, u, b]);
  return p;
}
async function shot(p, n) { if (E.SHOTS) await p.screenshot({ path: E.SHOTS + '/' + n + '.png' }); }
(async () => {
  const br = await PW.chromium.launch();
  const made = [];
  try {
    const p = await session(br);
    const mk = async (title, extra) => { const id = (await p.api('POST', '/api/notes', Object.assign({ title }, extra || {}))).id; made.push(id); return id; };
    const link = (id) => ({ type: 'paragraph', data: { text: '<a href="/notes#' + id + '">x</a>' } });
    const hub = await mk('Graph Mitte');
    const ids = [];
    for (let i = 0; i < 12; i++) ids.push(await mk('Graph Blatt ' + i, i < 4 ? { parent_id: hub } : {}));
    await p.api('PATCH', '/api/notes/' + hub, { format: 'blocks', body: JSON.stringify({ blocks: ids.slice(4, 10).map(link) }), tags: ['grafik'] });
    await p.api('PATCH', '/api/notes/' + ids[10], { format: 'blocks', body: JSON.stringify({ blocks: [link(hub), link(ids[11])] }), tags: ['grafik'] });
    await sleep(2500);
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + hub); await p.waitForSelector('#pg-title'); await sleep(1500);
    await p.evaluate(() => document.getElementById('pg-graph') && document.getElementById('pg-graph').scrollIntoView());
    await sleep(1200);
    const loc = await p.evaluate(() => { const s = document.getElementById('pg-graph'); const c = s && s.querySelector('canvas'); return s && { hidden: s.hidden, w: c.width, label: c.getAttribute('aria-label') }; });
    ok('Lokaler Graph sichtbar mit Nachbarn', loc && !loc.hidden && loc.w > 0 && /11/.test(loc.label || ''), loc);
    await shot(p, 'graph-lokal');
    await p.click('#pg-graph .gr-big'); await p.waitForSelector('.gr-full'); await sleep(2500);
    const st = await p.textContent('.gr-stat');
    ok('Ganzer Graph geladen', /Seiten/.test(st) && !/nicht geladen/.test(st), st);
    const nonblank = await p.evaluate(() => { const c = document.querySelector('.gr-canvas'), x = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 3; i < x.length; i += 40) if (x[i] > 0) n++; return n; });
    ok('Canvas gezeichnet', nonblank > 50, nonblank);
    await shot(p, 'graph-ganz');
    await p.fill('.gr-search', 'Graph Blatt 11'); await sleep(400);
    await p.press('.gr-search', 'Enter'); await sleep(1500);
    ok('Suche + Enter oeffnet die Seite, Graph zu', !(await p.$('.gr-full')) && (await p.inputValue('#pg-title')) === 'Graph Blatt 11');
    await p.click('#pg-graph-btn'); await p.waitForSelector('.gr-full'); await sleep(1500);
    const n0 = await p.evaluate(() => document.querySelector('.gr-stat').textContent);
    const tg = p.locator('.gr-check', { hasText: 'Tags als Knoten' }); await tg.click(); await sleep(800);
    const n1 = await p.evaluate(() => document.querySelector('.gr-stat').textContent);
    ok('Filter "Tags als Knoten" aendert den Graph', n0 !== n1, [n0, n1]);
    await tg.click();
    await p.keyboard.press('Escape'); await sleep(300);
    ok('Esc schliesst', !(await p.$('.gr-full')));
    ok('keine Seitenfehler', !p.errs.length, p.errs);
    // Handy + dunkel
    const m = await session(br, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, colorScheme: 'dark' });
    await m.goto(BASE + '/notes?t=' + Date.now() + '#' + hub); await m.waitForSelector('#pg-title'); await sleep(1200);
    await m.evaluate(() => window.HKGraph.open(null)); await m.waitForSelector('.gr-full'); await sleep(2500);
    const ov = await m.evaluate(() => ({ sw: document.documentElement.scrollWidth, vw: innerWidth, panel: document.querySelector('.gr-panel').hidden }));
    ok('Handy: kein Ueberlauf, Einstellungen zu Beginn eingeklappt', ov.sw <= ov.vw && ov.panel, ov);
    await shot(m, 'graph-handy-dunkel');
    ok('Handy: keine Seitenfehler', !m.errs.length, m.errs);
  } catch (e) { ok('Ablauf', false, String(e && e.stack || e).slice(0, 500)); }
  finally {
    try { const p2 = br.contexts()[0].pages()[0]; for (const id of made) await p2.evaluate(async (id) => { await fetch('/api/notes/' + id, { method: 'DELETE', headers: { 'X-Saga': '1' } }); }, id); } catch (e) { /* egal */ }
    await br.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-GRAPH ${n}/${res.length} - ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
