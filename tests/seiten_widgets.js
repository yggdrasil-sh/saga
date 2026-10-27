// Widgets in Seiten (2.5, Etappe 1) im Web: einfuegen per Slash ("/Kennzahl"), einrichten mit Live-Vorschau
// (/widgets/preview), drei Widgets nebeneinander in Spalten (Uhr, Fortschritt, Kennzahl), Fehlerzustand ohne Zugriff auf
// die Quelle (zweites Konto), unbekannte Art, gemeinsame Testvektoren (tests/widget_vectors.json) gegen calcNum
// (= Fusszeile der Datenbank-Tabelle). Nicht verwechseln mit tests/widgets.py (Home-Bildschirm-Widgets der Apps).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> CK2=<Cookie hk-test-b> node tests/seiten_widgets.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const path = require('path'), fs = require('fs');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const VEC = JSON.parse(fs.readFileSync(path.join(__dirname, 'widget_vectors.json'), 'utf8'));
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 5000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], reqs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  p.on('request', (r) => { if (/\/widgets(\/preview)?(\?|$)/.test(r.url())) reqs.push(r.method() + ' ' + new URL(r.url()).pathname); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const body = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;
  const P = (t) => ({ type: 'paragraph', data: { text: t } });

  // --- Gemeinsame Testvektoren (Server: tests/seiten_widgets.py) ---------------------------------------------------
  await until(() => p.evaluate(() => !!(window.HKPages && window.HKPages.k && window.HKPages.k.calcNum)));
  const bad = await p.evaluate((v) => {
    const out = [], props = {}; v.schema.props.forEach((x) => { props[x.id] = x; });
    v.cases.forEach((c) => {
      const pr = c.prop && c.prop !== 'title' ? props[c.prop] : null;
      const got = window.HKPages.k.calcNum(c.op, pr, v.sets[c.set]);
      if (got !== c.want) out.push([c, got]);
    });
    return out;
  }, VEC);
  ok(`Testvektoren: calcNum = Server (${VEC.cases.length} Faelle)`, !bad.length, bad.slice(0, 3));

  // --- Quelle -----------------------------------------------------------------------------------------------------
  const db = (await api('POST', '/api/notes', { title: 'WG Quelle Geheim', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + db, { schema: { props: [{ id: 'erl', name: 'Erledigt', type: 'checkbox' }, { id: 'std', name: 'Stunden', type: 'number' }],
    views: [{ id: 'alle', name: 'Alle', type: 'table' }] } });
  for (const [t, e, h] of [['a', true, 2], ['b', false, 3], ['c', true, 5]]) await api('POST', '/api/notes', { title: t, parent_id: db, props: { erl: e, std: h } });

  // --- Einfuegen per Slash und Einrichten mit Vorschau ---------------------------------------------------------------
  const id = (await api('POST', '/api/notes', { title: 'WG Web' })).id;
  await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [P('Start'), P('')] }), format: 'blocks' });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector('.ce-paragraph'); await sleep(1200);
  const empty = p.locator('.ce-paragraph').nth(1);
  await empty.click(); await p.keyboard.type('/'); await sleep(400);
  await p.keyboard.type('Kennzahl', { delay: 30 }); await sleep(400);
  const menu = await p.evaluate(() => [...document.querySelectorAll('.ce-popover--opened .ce-popover-item:not(.ce-popover-item--hidden) .ce-popover-item__title')].map((x) => x.textContent.trim()));
  ok(`Slash-Menue zeigt "Widget: Kennzahl" (${menu.slice(0, 4).join(', ')})`, menu.indexOf('Widget: Kennzahl') >= 0, menu);
  await p.keyboard.press('Enter');
  const panel = await until(() => p.locator('.wg-panel').count());
  ok('Widget eingefuegt, Einrichten-Panel offen', panel === 1 && await p.locator('.wg[data-kind="metric"]').count() === 1);
  await p.locator('.wg-panel select').filter({ has: p.locator('option', { hasText: 'WG Quelle Geheim' }) }).first().selectOption(String(db));
  await until(() => p.evaluate(() => [...document.querySelectorAll('.wg-panel .wg-l')].some((x) => x.textContent === 'Rechenart')));
  const rech = p.locator('.wg-row', { has: p.locator('.wg-l', { hasText: /^Rechenart$/ }) }).locator('select');
  await rech.selectOption('sum'); await sleep(200);
  await p.locator('.wg-row', { has: p.locator('.wg-l', { hasText: /^Eigenschaft$/ }) }).locator('select').selectOption('std');
  await p.locator('.wg-row', { has: p.locator('.wg-l', { hasText: /^Titel$/ }) }).locator('input').fill('Stunden gesamt');
  const pv = await until(async () => { const t = await p.locator('.wg[data-kind="metric"] .wg-v').textContent(); return t === '10' ? t : null; }, 6000);
  ok(`Live-Vorschau zeigt Summe 10 (${await p.locator('.wg[data-kind="metric"] .wg-v').textContent()})`, pv === '10');
  ok('Vorschau ueber POST …/widgets/preview', reqs.some((r) => r === `POST /api/notes/${id}/widgets/preview`), reqs);
  await p.locator('.wg-panel .wg-done').click(); await sleep(3500);
  let bl = await body(id);
  const wb = bl.find((b) => b.type === 'widget');
  ok('gespeichert: kind metric, cfg wie eingestellt, Titel, eigene id', wb && wb.data.kind === 'metric' && wb.data.cfg.db === db && wb.data.cfg.agg === 'sum'
    && wb.data.cfg.prop === 'std' && wb.data.title === 'Stunden gesamt' && typeof wb.id === 'string', wb);
  const w = await api('GET', '/api/notes/' + id + '/widgets');
  ok('GET …/widgets liefert den Wert zum Block', w.widgets && w.widgets[wb.id] && w.widgets[wb.id].value === 10, w);

  // --- Drei Widgets nebeneinander in Spalten + unbekannte Art -------------------------------------------------------
  const id2 = (await api('POST', '/api/notes', { title: 'WG Spalten' })).id;
  const W = (kind, cfg, extra) => Object.assign({ type: 'widget', data: Object.assign({ v: 1, kind: kind, cfg: cfg }, extra || {}) });
  await api('PATCH', '/api/notes/' + id2, { format: 'blocks', body: JSON.stringify({ blocks: [
    { type: 'columns', data: { cols: [
      { blocks: [W('clock', { tz: 'UTC', h24: true, sec: true }, { title: 'UTC' })] },
      { blocks: [W('progress', { src: 'fixed', value: 1, max: 4 }, { accent: 'green' })] },
      { blocks: [W('metric', { db: db, agg: 'pct_checked', prop: 'erl' }, { title: 'Erledigt', size: 'l', accent: 'blue' })] }] } },
    W('zukunft', { a: 1 }, { title: 'Neu' }), P('Ende')] }) });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id2);
  await p.waitForSelector('.cols .wg'); await sleep(1500);
  const boxes = await p.evaluate(() => [...document.querySelectorAll('.cols .wg')].map((x) => { const r = x.getBoundingClientRect(); return [Math.round(r.x), Math.round(r.y), Math.round(r.width)]; }));
  ok(`drei Widgets nebeneinander (${JSON.stringify(boxes)})`, boxes.length === 3 && boxes[0][0] < boxes[1][0] && boxes[1][0] < boxes[2][0]
    && Math.abs(boxes[0][1] - boxes[2][1]) < 30, boxes);
  const v3 = await until(async () => { const t = await p.locator('.wg[data-kind="metric"] .wg-v').textContent(); return /67/.test(t) ? t : null; }, 6000);
  ok(`Kennzahl in Spalte: 67 % angehakt (${v3})`, !!v3);
  const t1 = await p.locator('.wg[data-kind="clock"] .wg-v').textContent(); await sleep(1300);
  const t2 = await p.locator('.wg[data-kind="clock"] .wg-v').textContent();
  ok(`Uhr tickt lokal (Sekunden: ${t1} -> ${t2})`, /\d{2}:\d{2}:\d{2}/.test(t2) && t1 !== t2);
  const prog = await p.evaluate(() => { const f = document.querySelector('.wg[data-kind="progress"] .wg-fill'); return [f && f.style.width, document.querySelector('.wg[data-kind="progress"] .wg-v').textContent]; });
  ok(`Fortschritt fest 25 % (${prog})`, parseFloat(prog[0]) === 25 && /25/.test(prog[1]), prog);
  const acc = await p.evaluate(() => [document.querySelector('.wg[data-kind="metric"]').className, getComputedStyle(document.querySelector('.wg[data-kind="metric"] .wg-v')).color,
    getComputedStyle(document.querySelector('.wg[data-kind="clock"] .wg-v')).color]);
  ok(`Akzent und Groesse als Klassen (${acc[0]})`, /wg-l/.test(acc[0]) && /wg-a-blue/.test(acc[0]) && acc[1] !== acc[2], acc);
  const unk = await p.locator('.wg[data-kind="zukunft"]').textContent();
  ok(`unbekannte Art: Hinweis (${unk})`, /neuere Version/.test(unk));
  // Speichern einer anderen Stelle laesst die unbekannte Art unveraendert
  const ende = p.locator('.ce-paragraph', { hasText: 'Ende' }).first();
  await ende.click(); await p.keyboard.press('End'); await p.keyboard.type(' plus'); await sleep(3500);
  bl = await body(id2);
  const z = bl.find((b) => b.type === 'widget' && b.data.kind === 'zukunft');
  ok('Rundreise: unbekannte Art unveraendert gespeichert', z && z.data.cfg && z.data.cfg.a === 1 && z.data.title === 'Neu' && bl.some((b) => b.type === 'paragraph' && /plus/.test(b.data.text)), z);

  // --- Fehlerzustand: zweites Konto ohne Zugriff auf die Quelle ----------------------------------------------------
  if (process.env.CK2) {
    await api('PUT', '/api/notes/' + id2 + '/shares', { type: 'user', target: 'hk-test-b', role: 'view' });
    const ctx2 = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx2.addCookies([{ name: 'saga', value: process.env.CK2, domain: new URL(BASE).hostname, path: '/' }]);
    const q = await ctx2.newPage(); q.on('pageerror', (e) => errs.push(e.message));
    await q.goto(BASE + '/notes#' + id2); await q.waitForSelector('.cols .wg'); await sleep(1500);
    const msg = await until(async () => { const t = await q.locator('.wg[data-kind="metric"]').textContent(); return /Kein Zugriff/.test(t) ? t : null; }, 6000);
    ok(`ohne Zugriff: "Kein Zugriff auf die Datenquelle" (${msg})`, !!msg && !/67/.test(msg));
    const html = await q.content();
    ok('kein Name der Quelle im Dokument', html.indexOf('WG Quelle Geheim') < 0);
    await q.locator('.wg[data-kind="metric"]').click(); await sleep(300);
    ok('Lesen: Klick oeffnet kein Einrichten-Panel', await q.locator('.wg-panel').count() === 0);
    await ctx2.close();
  }

  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  for (const x of [id, id2, db]) await api('DELETE', '/api/notes/' + x);
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nSEITEN-WIDGETS-WEB ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
