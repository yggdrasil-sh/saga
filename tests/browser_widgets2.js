// Widgets Etappe 2 + Diagramme (2.5, Paket 4) im Web: Diagramm-Ansicht einer Datenbank (Umschalten im Menue, SVG,
// Einrichten, Klick auf Balken zeigt die Eintraege), Diagramm-Widget per Slash mit Live-Vorschau, Gewohnheiten (Raster,
// Abhaken per Klick, Serie), Anstehendes (eigene Aufgaben, gruppiert), Wetter-Fehlerzustand bei ausgeschaltetem Wetter,
// Achsen-Vektoren (tests/chart_ticks.json) gegen chartTicks, keine Seiten-/CSP-Fehler.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8230 CK=<Cookie hk-test-a> node tests/browser_widgets2.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const path = require('path'), fs = require('fs');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const TICKS = JSON.parse(fs.readFileSync(path.join(__dirname, 'chart_ticks.json'), 'utf8'));
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], ext = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  p.on('request', (r) => { const h = new URL(r.url()).host; if (h !== new URL(BASE).host) ext.push(r.url()); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const body = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;
  const P = (t) => ({ type: 'paragraph', data: { text: t } });
  const W = (bid, kind, cfg, extra) => Object.assign({ id: bid, type: 'widget', data: Object.assign({ v: 1, kind: kind, cfg: cfg }, extra || {}) });

  await until(() => p.evaluate(() => !!(window.HKPages && window.HKPages.k && window.HKPages.k.chartTicks)));
  const bad = await p.evaluate((v) => v.cases.filter((c) => JSON.stringify(window.HKPages.k.chartTicks(c.lo, c.hi)) !== JSON.stringify(c.want))
    .map((c) => [c, window.HKPages.k.chartTicks(c.lo, c.hi)]), TICKS);
  ok(`Achsen-Vektoren: chartTicks = Server (${TICKS.cases.length} Faelle)`, !bad.length, bad.slice(0, 2));

  // --- Datenbank mit Diagramm-Ansicht -------------------------------------------------------------------------------
  const db = (await api('POST', '/api/notes', { title: 'B2 Ausgaben', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + db, { schema: { props: [
    { id: 'kat', name: 'Kategorie', type: 'select', options: [{ id: 'essen', name: 'Essen', color: 'red' }, { id: 'reise', name: 'Reise', color: 'blue' }, { id: 'buch', name: 'Bücher' }] },
    { id: 'betrag', name: 'Betrag', type: 'number' }, { id: 'tag', name: 'Tag', type: 'date' }],
  views: [{ id: 'tab', name: 'Tabelle', type: 'table' }] } });
  for (const [t, k, b] of [['Pizza', 'essen', 12], ['Zug', 'reise', 40], ['Roman', 'buch', 15], ['Brot', 'essen', 3]]) {
    await api('POST', '/api/notes', { title: t, parent_id: db, props: { kat: k, betrag: b, tag: '2026-10-0' + (t.length % 9 + 1) } });
  }
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + db);
  await p.waitForSelector('.db-bar'); await sleep(800);
  await p.locator('.db-tab-add').first().click();
  const add = p.locator('.menu-item', { hasText: 'Diagramm' });
  if (await add.count()) await add.first().click();
  const svg = await until(() => p.locator('.dbc .ch-svg').count(), 8000);
  ok('Neue Ansicht "Diagramm": SVG gezeichnet', svg >= 1);
  const bars = await p.locator('.dbc .ch-svg rect.ch-f').count();
  ok(`Vorschlag: nach erster Auswahl gruppiert, 3 Balken (${bars})`, bars === 3);
  const colors = await p.evaluate(() => [...document.querySelectorAll('.dbc .ch-svg rect.ch-f')].map((r) => r.getAttribute('class')));
  ok(`Optionsfarben als Klassen (${colors.join(' | ')})`, colors[0].includes('ch-c-red') && colors[1].includes('ch-c-blue'), colors);
  ok('Werte als Tabelle vorhanden', await p.locator('.dbc details.ch-tab tbody tr').count() === 3);
  await p.locator('.dbc .ch-svg rect.ch-f').first().click();
  const rows = await until(async () => { const t = await p.locator('.dbc-rows').textContent(); return /Pizza/.test(t) && /Brot/.test(t) ? t : null; });
  ok(`Klick auf Balken: Eintraege der Kategorie (${rows})`, !!rows && !/Zug/.test(rows));
  // Einrichten: Summe des Betrags, Kreis
  await p.locator('.dbc-bar button').click(); await sleep(300);
  const vs = p.locator('.flt-box.vs');
  await vs.locator('.vs-row', { has: p.locator('.vs-lbl', { hasText: /^Rechenart$/ }) }).locator('select').selectOption('sum'); await sleep(400);
  await vs.locator('.vs-row', { has: p.locator('.vs-lbl', { hasText: /^Eigenschaft$/ }) }).locator('select').selectOption('betrag'); await sleep(400);
  await vs.locator('.vs-row', { has: p.locator('.vs-lbl', { hasText: /^Darstellung$/ }) }).locator('select').selectOption('donut'); await sleep(400);
  await p.keyboard.press('Escape');
  const donut = await until(async () => { const t = await p.locator('.dbc .ch-svg .ch-big').count() ? await p.locator('.dbc .ch-svg .ch-big').textContent() : ''; return /70/.test(t) ? t : null; }, 8000);
  ok(`Ring mit Summe in der Mitte (${donut})`, !!donut);
  await sleep(1200);
  const sc = (await api('GET', '/api/notes/' + db + '/rows?limit=1')).schema;
  const cv = sc.views.find((v) => v.type === 'chart');
  ok('Ansicht gespeichert: type chart, Spec sum/betrag/donut', cv && cv.chart.agg === 'sum' && cv.chart.y === 'betrag' && cv.chart.type === 'donut', cv);

  // --- Seite mit Widgets ------------------------------------------------------------------------------------------
  const hdb = (await api('POST', '/api/notes', { title: 'B2 Log', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + hdb, { schema: { props: [{ id: 'datum', name: 'Datum', type: 'date' }, { id: 'sport', name: 'Sport', type: 'checkbox' },
    { id: 'lesen', name: 'Lesen', type: 'checkbox' }], views: [{ id: 'alle', name: 'Alle', type: 'table' }] } });
  const now = new Date(), d1 = new Date(now.getTime() - 864e5), d2 = new Date(now.getTime() - 2 * 864e5);
  for (const d of [d1, d2]) await api('POST', '/api/notes', { title: ymd(d), parent_id: hdb, props: { datum: ymd(d), sport: true } });
  await api('POST', '/api/tasks', { text: 'B2 Aufgabe morgen', due: ymd(new Date(now.getTime() + 864e5)) });
  const id = (await api('POST', '/api/notes', { title: 'B2 Cockpit' })).id;
  await api('PATCH', '/api/notes/' + id, { format: 'blocks', body: JSON.stringify({ blocks: [
    W('hb', 'habit', { db: hdb, view: 'alle', date: 'datum', habits: ['sport', 'lesen'], range: 7 }, { title: 'Gewohnheiten' }),
    W('up', 'upcoming', { days: 7, cal: false }, { title: 'Bald' }),
    W('wx', 'weather', { place: 'Ort', lat: 50, lon: 8 }, { title: 'Wetter' }),
    P('Ende'), P('')] }) });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
  await p.waitForSelector('.wg[data-kind="habit"]');
  const grid = await until(async () => (await p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').count()) === 2, 8000);
  ok('Gewohnheiten: zwei Zeilen im Raster', grid);
  const streak = await p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').first().locator('.hb-st').textContent();
  ok(`Sport: Serie 2 (gestern + vorgestern), Quote 29 % (${streak})`, /29/.test(streak) && /2/.test(streak.split('🔥')[1] || ''), streak);
  const todayBtn = p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').first().locator('button.hb-c.hb-today');
  ok('heutiger Tag als Knopf (abhakbar)', await todayBtn.count() === 1);
  await todayBtn.click();
  const after = await until(async () => { const t = await p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').first().locator('.hb-st').textContent(); return /🔥\s*3/.test(t) ? t : null; }, 8000);
  ok(`Abhaken per Klick: Serie 3 (${after})`, !!after);
  const r = await api('GET', '/api/notes/' + hdb + '/rows');
  ok('Zeile fuer heute angelegt, Sport gesetzt', r.rows.some((x) => x.props.datum === ymd(now) && x.props.sport === true), r.rows.map((x) => x.props));
  const up = await until(async () => { const t = await p.locator('.wg[data-kind="upcoming"]').textContent(); return /B2 Aufgabe morgen/.test(t) ? t : null; });
  ok(`Anstehendes: eigene Aufgabe unter "Morgen" (${(up || '').slice(0, 80)})`, !!up && /Morgen/.test(up));
  const wx = await until(async () => { const t = await p.locator('.wg[data-kind="weather"]').textContent(); return /ausgeschaltet/.test(t) ? t : null; });
  ok(`Wetter aus: freundlicher Hinweis (${wx})`, !!wx);

  // --- Diagramm-Widget per Slash mit Vorschau ---------------------------------------------------------------------
  await p.locator('.ce-paragraph').last().click(); await p.keyboard.type('/'); await sleep(400);
  await p.keyboard.type('Diagramm', { delay: 30 }); await sleep(400);
  const menu = await p.evaluate(() => [...document.querySelectorAll('.ce-popover--opened .ce-popover-item:not(.ce-popover-item--hidden) .ce-popover-item__title')].map((x) => x.textContent.trim()));
  ok(`Slash-Menue: "Widget: Diagramm" (${menu.slice(0, 3).join(', ')})`, menu.indexOf('Widget: Diagramm') >= 0, menu);
  await p.keyboard.press('Enter');
  await until(() => p.locator('.wg-panel').count());
  await p.locator('.wg-panel select').filter({ has: p.locator('option', { hasText: 'B2 Ausgaben' }) }).first().selectOption(String(db));
  await until(() => p.evaluate(() => [...document.querySelectorAll('.wg-panel .wg-l')].some((x) => x.textContent === 'Gruppieren nach')));
  await p.locator('.wg-row', { has: p.locator('.wg-l', { hasText: /^Gruppieren nach$/ }) }).locator('select').selectOption('kat');
  const pv = await until(() => p.locator('.wg[data-kind="chart"] .ch-svg rect.ch-f').count(), 8000);
  ok(`Diagramm-Widget: Live-Vorschau mit Balken (${pv})`, pv === 3);
  await p.locator('.wg-panel .wg-done').click(); await sleep(3500);
  const bl = await body(id);
  const cw = bl.find((b) => b.type === 'widget' && b.data.kind === 'chart');
  ok('gespeichert: chart mit db/x', cw && cw.data.cfg.db === db && cw.data.cfg.x === 'kat' && cw.data.cfg.type === 'bar', cw);

  // --- schmal (Handy) --------------------------------------------------------------------------------------------
  await p.setViewportSize({ width: 390, height: 800 }); await sleep(600);
  const ov = await p.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  ok(`Handy-Breite: kein seitliches Scrollen der Seite (${ov})`, ov <= 2);

  ok('Browser fragt keine fremden Dienste (Open-Meteo nur der Server)', !ext.length, ext.slice(0, 3));
  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs);
  for (const x of [id, db, hdb]) await api('DELETE', '/api/notes/' + x);
  const tasks = await api('GET', '/api/tasks');
  for (const t of (tasks.open || []).concat(tasks.done || [])) if (t && /^B2 /.test(t.text || '')) await api('DELETE', '/api/tasks/' + t.id);
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nBROWSER-WIDGETS2 ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
