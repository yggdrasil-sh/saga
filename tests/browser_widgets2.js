// Widgets Etappe 2 + Diagramme (2.5, Paket 4) im Web: Diagramm-Ansicht einer Datenbank (Umschalten im Menue, SVG,
// Einrichten, Klick auf Balken zeigt die Eintraege), Diagramm-Widget per Slash mit Live-Vorschau, Gewohnheiten (Raster,
// Abhaken per Klick, Serie), Anstehendes (eigene Aufgaben, gruppiert), Wetter-Fehlerzustand bei ausgeschaltetem Wetter,
// Achsen-Vektoren (tests/chart_ticks.json) gegen chartTicks, Diagramme in 4 Spalten (Schrift >= 10 px, Palette ohne
// Optionsfarben, Flaeche gestapelt, Tabelle als Knopf), keine Seiten-/CSP-Fehler.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8230 CK=<Cookie hk-test-a> node tests/browser_widgets2.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');            // Bloecke ueber data-block-* und HKPages.k.doc (T1)
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
  await D.useEditor(p);   // M3: Editor-Modus ausdruecklich (SAGA_EDITOR)
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
  await vs.locator('.vs-row', { has: p.locator('.vs-lbl', { hasText: /^(Diagrammart|Darstellung)$/ }) }).locator('select').selectOption('donut'); await sleep(400);
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
  ok(`Sport: Serie 2 (gestern + vorgestern), Quote 29 % (${streak})`, /29/.test(streak) && /2/.test(streak.split('🔥')[1] || streak.split('%')[1] || ''), streak);
  const todayBtn = p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').first().locator('button.hb-c.hb-today');
  ok('heutiger Tag als Knopf (abhakbar)', await todayBtn.count() === 1);
  await todayBtn.click();
  const after = await until(async () => { const t = await p.locator('.wg[data-kind="habit"] .hb-row:not(.hb-head)').first().locator('.hb-st').textContent(); return /(🔥|%)\s*3/.test(t) ? t : null; }, 8000);
  ok(`Abhaken per Klick: Serie 3 (${after})`, !!after);
  const r = await api('GET', '/api/notes/' + hdb + '/rows');
  ok('Zeile fuer heute angelegt, Sport gesetzt', r.rows.some((x) => x.props.datum === ymd(now) && x.props.sport === true), r.rows.map((x) => x.props));
  const up = await until(async () => { const t = await p.locator('.wg[data-kind="upcoming"]').textContent(); return /B2 Aufgabe morgen/.test(t) ? t : null; });
  ok(`Anstehendes: eigene Aufgabe unter "Morgen" (${(up || '').slice(0, 80)})`, !!up && /Morgen/.test(up));
  const wx = await until(async () => { const t = await p.locator('.wg[data-kind="weather"]').textContent(); return /ausgeschaltet/.test(t) ? t : null; });
  ok(`Wetter aus: freundlicher Hinweis (${wx})`, !!wx);

  // --- Diagramm-Widget per Slash mit Vorschau ---------------------------------------------------------------------
  await p.locator(D.textOf('paragraph')).last().click(); await p.keyboard.type('/'); await sleep(400);
  await p.keyboard.type('Diagramm', { delay: 30 }); await sleep(400);
  const items = () => p.evaluate((s) => [...document.querySelectorAll(s)].map((x) => x.textContent.trim()), D.UI_ITEMS);
  const menu = (await until(async () => { const l = await items(); return l.indexOf('Widget: Diagramm') >= 0 ? l : null; }, 4000)) || await items();
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

  // --- Diagramme in 4 Spalten (Design-Runde): echte Schriftgroesse, Palette ohne Optionsfarben, Flaeche gestapelt -----
  const rdb = (await api('POST', '/api/notes', { title: 'B2 Reihen', kind: 'database' })).id;
  await api('PATCH', '/api/notes/' + rdb, { schema: { props: [
    { id: 'wer', name: 'Wer', type: 'select', options: [{ id: 'a', name: 'Anna' }, { id: 'b', name: 'Ben', color: 'gray' }, { id: 'c', name: 'Cleo' }] },
    { id: 'n', name: 'Menge', type: 'number' }, { id: 'am', name: 'Am', type: 'date' }], views: [{ id: 'tab', name: 'Tabelle', type: 'table' }] } });
  for (const [w, nn, am] of [['a', 3, '2026-08-05'], ['b', 4, '2026-08-09'], ['c', 5, '2026-08-20'], ['a', 2, '2026-09-03'], ['b', 6, '2026-09-11'], ['c', 1, '2026-09-28'], ['a', 7, '2026-10-02']]) {
    await api('POST', '/api/notes', { title: w + nn, parent_id: rdb, props: { wer: w, n: nn, am: am } });
  }
  const CW = (bid, cfg, t) => W(bid, 'chart', Object.assign({ db: rdb, view: '', agg: 'sum', y: 'n' }, cfg), { title: t });
  const cid = (await api('POST', '/api/notes', { title: 'B2 Diagramme' })).id;
  await api('PATCH', '/api/notes/' + cid, { format: 'blocks', body: JSON.stringify({ blocks: [
    { id: 'cc', type: 'columns', data: { cols: [
      CW('c1', { type: 'bar', x: 'am', bucket: 'month', series: 'wer' }, 'Reihen'),
      CW('c2', { type: 'area', x: 'am', bucket: 'month', series: 'wer', stack: true }, 'Flaeche gestapelt'),
      CW('c3', { type: 'donut', x: 'wer' }, 'Ring'),
      CW('c4', { type: 'hbar', x: 'wer' }, 'Quer')].map((w) => ({ blocks: [w] })) } }, P('')] }), wide: true });
  await p.setViewportSize({ width: 1920, height: 1000 });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + cid);
  await until(() => p.evaluate(() => document.querySelectorAll('.wg[data-kind="chart"] .ch-svg').length === 4), 10000); await sleep(400);
  const fonts = await p.evaluate(() => [...document.querySelectorAll('.wg[data-kind="chart"] .ch-svg:not(.ch-round)')].map((s) => {
    const sc = s.getBoundingClientRect().width / s.viewBox.baseVal.width;
    return { w: Math.round(s.getBoundingClientRect().width), min: Math.min(...[...s.querySelectorAll('text.ch-t')].map((t) => parseFloat(getComputedStyle(t).fontSize) * sc)) };
  }));
  ok(`4 Spalten: Achsentext effektiv >= 10 px (${JSON.stringify(fonts)})`, fonts.length === 3 && fonts.every((f) => f.min >= 10 && isFinite(f.min)), fonts);
  const serCls = await p.evaluate(() => {
    const w = [...document.querySelectorAll('.wg[data-kind="chart"]')].find((x) => /Reihen/.test(x.textContent));
    return w ? [...new Set([...w.querySelectorAll('.ch-svg rect.ch-f')].map((r) => (r.getAttribute('class').match(/ch-(s\d|c-\w+|one|other)/) || [])[0]))] : [];
  });
  ok(`Reihen ohne Optionsfarbe: verschiedene Palettenfarben (${serCls.join(' ')})`, serCls.length === 3 && serCls.every((c) => /^ch-s\d$/.test(c)), serCls);
  const ringCls = await p.evaluate(() => {
    const w = [...document.querySelectorAll('.wg[data-kind="chart"]')].find((x) => /Ring/.test(x.querySelector('.wg-t') ? x.querySelector('.wg-t').textContent : ''));
    return w ? [...w.querySelectorAll('.ch-svg path.ch-f')].map((r) => (r.getAttribute('class').match(/ch-(s\d|c-\w+)/) || [])[0]) : [];
  });
  ok(`Ring ohne Optionsfarben: nicht grau, alle verschieden (${ringCls.join(' ')})`, ringCls.length === 3 && new Set(ringCls).size === 3 && !ringCls.some((c) => /gray/.test(c || '')), ringCls);
  // Quer: kurze Kategorien werden nicht gekuerzt (Rundung der Randbreite schnitt "Anna" zu "An…")
  const qLab = await p.evaluate(() => {
    const w = [...document.querySelectorAll('.wg[data-kind="chart"]')].find((x) => /Quer/.test(x.querySelector('.wg-t') ? x.querySelector('.wg-t').textContent : ''));
    return w ? [...w.querySelectorAll('.ch-svg text.ch-t')].map((t) => t.textContent).filter((t) => /^[A-Z]/.test(t)) : [];
  });
  ok(`Quer: Namen ungekuerzt (${qLab.join(', ')})`, qLab.length === 3 && qLab.every((t) => !/…/.test(t)), qLab);
  // N3: eine Reihe = eine Farbe (ohne zwei echte Optionsfarben), auch bei Balken nach Kategorie
  const oneCls = await p.evaluate(() => {
    const w = [...document.querySelectorAll('.wg[data-kind="chart"]')].find((x) => /Quer/.test(x.querySelector('.wg-t') ? x.querySelector('.wg-t').textContent : ''));
    return w ? [...w.querySelectorAll('.ch-svg rect.ch-f')].map((r) => (r.getAttribute('class').match(/ch-(s\d|c-\w+|one|other)/) || [])[0]) : [];
  });
  ok(`Eine Reihe nach Kategorie: eine Farbe (${oneCls.join(' ')})`, oneCls.length === 3 && oneCls.every((c) => c === 'ch-one'), oneCls);
  const st = await p.evaluate(() => {
    const w = [...document.querySelectorAll('.wg[data-kind="chart"]')].find((x) => /gestapelt/.test(x.textContent));
    if (!w) return null;
    const num = (s) => { const v = parseFloat(String(s).replace(/\./g, '').replace(',', '.')); return isFinite(v) ? v : 0; };
    const sums = [...w.querySelectorAll('.ch-tab tbody tr')].map((tr) => [...tr.querySelectorAll('td')].reduce((a, td) => a + num(td.textContent), 0));
    const by = {}; w.querySelectorAll('.ch-svg circle[data-top]').forEach((c) => { (by[c.getAttribute('cx')] = by[c.getAttribute('cx')] || []).push([+c.getAttribute('data-top'), +c.getAttribute('cy')]); });
    const tops = Object.keys(by).sort((a, b) => a - b).map((k) => { const g = by[k].sort((a, b) => b[0] - a[0]); return { top: g[0][0], highest: g[0][1] === Math.min(...g.map((x) => x[1])) }; });
    return { sums, tops };
  });
  ok(`Flaeche gestapelt: oberste Kurve = Summe je Monat (${JSON.stringify(st)})`, !!st && st.sums.length === 3 && st.tops.length === 3
    && st.tops.every((t, i) => Math.abs(t.top - st.sums[i]) < 1e-9 && t.highest), st);
  await p.setViewportSize({ width: 1280, height: 900 }); await sleep(400);
  const narrowTab = await p.evaluate(() => [...document.querySelectorAll('.wg[data-kind="chart"] details.ch-tab')].map((d) => {
    const s = d.querySelector('summary'), r = s.getBoundingClientRect();
    return { btn: d.classList.contains('ch-tab-btn'), w: Math.round(r.width), name: s.getAttribute('aria-label') || s.textContent };
  }));
  ok(`schmale Karten: Tabelle als kleiner Knopf mit Namen (${JSON.stringify(narrowTab[0])})`, narrowTab.length === 4 && narrowTab.every((x) => x.btn && x.w <= 28 && /Tabelle/.test(x.name)), narrowTab);
  await api('PATCH', '/api/notes/' + cid, { wide: false });
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + cid);
  await until(() => p.evaluate(() => document.querySelectorAll('.wg[data-kind="chart"] .ch-svg').length === 4), 10000); await sleep(400);
  // Spalten brechen bei ~200 px je Spalte um - Breite suchen, bei der die Diagramme schmaler als 180 px werden
  const tinyM = () => p.evaluate(() => [...document.querySelectorAll('.wg[data-kind="chart"] .chart')].map((c) => ({ w: c.clientWidth, tiny: c.classList.contains('ch-tiny'),
    num: !!c.querySelector(':scope > .ch-num'), ticks: c.querySelectorAll('text.ch-t').length })));
  let tiny = await tinyM();
  for (let vw = 1400; vw >= 900 && !tiny.some((x) => x.w < 180); vw -= 10) { await p.setViewportSize({ width: vw, height: 900 }); await sleep(250); tiny = await tinyM(); }
  ok(`unter 180 px: kompakt (Gesamtwert + Balken, keine Achsen) (${JSON.stringify(tiny[0])})`, tiny.length === 4 && tiny.every((x) => x.w >= 180 || (x.tiny && x.ticks === 0)) && tiny.some((x) => x.tiny && x.num), tiny);
  await api('DELETE', '/api/notes/' + cid); await api('DELETE', '/api/notes/' + rdb);

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
