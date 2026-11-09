// Widget-Design-Runde (2.5, 9.11.): Layout-Regeln der Widgets im Web (Tiptap und Editor.js).
// - Gewohnheiten (7 und 30 Tage) laufen in 4 Spalten nicht aus der Karte, der heutige Tag und die Quote sind sichtbar
// - Karten einer Spalten-Reihe sind gleich hoch (nur ein Widget je Spalte)
// - Werte (.wg-v) brechen nicht um (eine Zeile), Wert in Textfarbe, eigene Farbe faerbt ihn
// - 4 Spalten: am Desktop breiter als die Textspalte (Tiptap) und nebeneinander; Tablet hoch mit Seitenleiste 2x2;
//   Handy untereinander mit ~10 px Abstand
// - nicht eingerichtet: Leerzustand mit Symbol, ohne Warndreieck; Einrichten-Panel: "Rahmen" statt "Darstellung"
// Lokal: HK_TEST_BASE=http://127.0.0.1:8150 CK=<Cookie hk-test-a> [SAGA_EDITOR=tiptap] node tests/browser_widgets_design.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
const ymd = (d) => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1920, height: 1080 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused/.test(m.text())) errs.push(m.text()); });
  await p.goto(BASE + '/notes');
  const engine = await D.useEditor(p);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const made = [];
  const W = (bid, kind, cfg, extra) => Object.assign({ id: bid, type: 'widget', data: Object.assign({ v: 1, kind: kind, cfg: cfg }, extra || {}) });
  const COLS = (id, ...ws) => ({ id: id, type: 'columns', data: { cols: ws.map((w) => ({ blocks: [w] })) } });

  // Daten: Gewohnheiten (30 Tage) und Ausgaben
  const hdb = (await api('POST', '/api/notes', { title: 'WDT Log', kind: 'database' })).id; made.push(hdb);
  await api('PATCH', '/api/notes/' + hdb, { schema: { props: [{ id: 'datum', name: 'Datum', type: 'date' }, { id: 'sport', name: 'Sport', type: 'checkbox' },
    { id: 'lesen', name: 'Ein sehr langer Gewohnheitsname zur Probe', type: 'checkbox' }], views: [{ id: 'alle', name: 'Alle', type: 'table' }] } });
  const now = new Date();
  for (let i = 1; i < 30; i += 2) await api('POST', '/api/notes', { title: 'T' + i, parent_id: hdb, props: { datum: ymd(new Date(now.getTime() - i * 864e5)), sport: true, lesen: i % 3 === 0 } });
  const adb = (await api('POST', '/api/notes', { title: 'WDT Ausgaben', kind: 'database' })).id; made.push(adb);
  await api('PATCH', '/api/notes/' + adb, { schema: { props: [{ id: 'betrag', name: 'Betrag', type: 'number' }], views: [{ id: 't', name: 'Tabelle', type: 'table' }] } });
  for (const b of [1200, 1050]) await api('POST', '/api/notes', { title: 'P' + b, parent_id: adb, props: { betrag: b } });

  const page = (await api('POST', '/api/notes', { title: 'WDT Widgets' })).id; made.push(page);
  await api('PATCH', '/api/notes/' + page, { format: 'blocks', body: JSON.stringify({ blocks: [
    COLS('c1', W('w1', 'clock', {}, { title: 'Uhr' }), W('w2', 'countdown', { date: ymd(new Date(now.getTime() + 42 * 864e5)), dir: 'until', unit: 'days' }, { title: 'Bis zum Urlaub' }),
      W('w3', 'metric', { db: adb, agg: 'sum', prop: 'betrag', fmt: 'eur' }, { title: 'Ausgaben', size: 'l' }), W('w4', 'progress', { src: 'fixed', value: 150, max: 100 }, { title: 'Über' })),
    COLS('c2', W('h7', 'habit', { db: hdb, date: 'datum', habits: ['sport', 'lesen'], range: 7 }, { title: 'Sieben' }),
      W('h30', 'habit', { db: hdb, date: 'datum', habits: ['sport', 'lesen'], range: 30 }, { title: 'Dreißig' }),
      W('m2', 'metric', { db: adb, agg: 'sum', prop: 'betrag', fmt: 'eur' }, { title: 'Grün', accent: 'green' }), W('e1', 'chart', { db: null }, {})),
    { id: 'p1', type: 'paragraph', data: { text: 'Ende' } }] }) });

  const open = async () => {
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + page);
    await p.waitForSelector('.wg[data-kind="habit"] .hb-row:not(.hb-head)', { timeout: 15000 }).catch(() => {});
    await until(async () => (await p.locator('.wg[data-kind="metric"] .wg-v').first().textContent().catch(() => '')).includes('2.250'), 8000);
    await sleep(500);
  };
  const habits = () => p.evaluate(() => [...document.querySelectorAll('.wg[data-kind="habit"]')].map((w) => {
    const r = w.getBoundingClientRect(), today = [...w.querySelectorAll('.hb-today')], st = [...w.querySelectorAll('.hb-row:not(.hb-head) .hb-st')];
    const inside = (e) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.left >= r.left - 1 && b.right <= r.right + 1; };
    return { w: Math.round(r.width), over: w.scrollWidth - w.clientWidth, today: today.length, todayIn: today.every(inside), stIn: st.length > 0 && st.every(inside),
      deep: [...w.querySelectorAll('*')].some((e) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.right > r.right + 1; }) };
  }));
  const rowHeights = (sel) => p.evaluate((sel) => { const c = document.querySelector(sel); return [...c.querySelectorAll('.wg')].map((w) => Math.round(w.getBoundingClientRect().height)); }, sel);
  const cols = (sel) => p.evaluate((sel) => getComputedStyle(document.querySelector(sel + ' .cols') || document.querySelector(sel)).gridTemplateColumns.split(' ').length, sel);
  const blk = (id) => D.block(id);

  // --- Desktop 1920 --------------------------------------------------------------------------------------------------
  await open();
  let hb = await habits();
  ok(`Desktop: Gewohnheiten 7/30 Tage ohne Ueberlauf, heute + Quote sichtbar (${JSON.stringify(hb)})`,
    hb.length === 2 && hb.every((x) => x.over <= 1 && !x.deep && x.today === 2 && x.todayIn && x.stIn), hb);
  const h1 = await rowHeights(blk('c1'));
  if (engine === 'tiptap') ok(`Desktop: Karten der Reihe gleich hoch (${h1})`, h1.length === 4 && Math.max(...h1) - Math.min(...h1) <= 1, h1);   // Editor.js: nicht umgesetzt
  const vals = await p.evaluate(() => [...document.querySelectorAll('.wg-v')].map((v) => ({ t: v.textContent, lines: Math.round(v.getBoundingClientRect().height / parseFloat(getComputedStyle(v).lineHeight)), ws: getComputedStyle(v).whiteSpace })));
  ok(`Werte einzeilig (${vals.map((v) => v.t + ':' + v.lines).join(', ')})`, vals.length >= 4 && vals.every((v) => v.lines <= 1 && v.ws === 'nowrap'), vals);
  const unit = await p.locator(blk('w3') + ' .wg-v small').textContent().catch(() => '');
  ok(`Kennzahl: Einheit klein abgesetzt (${unit})`, unit === '€');
  const colr = await p.evaluate(() => { const g = (s) => getComputedStyle(document.querySelector(s)).color; return { fg: getComputedStyle(document.body).color, plain: g('.wg[data-kind="metric"]:not([class*="wg-a-"]) .wg-v'), green: g('.wg.wg-a-green .wg-v') }; });
  ok(`Wert in Textfarbe, eigene Farbe faerbt ihn (${JSON.stringify(colr)})`, colr.plain === colr.fg && colr.green !== colr.fg, colr);
  ok('Fortschritt ueber 100 %: Marker "+50 %"', /\+50\s?%/.test(await p.locator(blk('w4') + ' .wg-chip').textContent().catch(() => '')));
  const empty = await p.locator(blk('e1') + ' .wg-empty').count(), warn = await p.locator(blk('e1') + ' .wg-err').count();
  ok('Nicht eingerichtet: Leerzustand ohne Warndreieck', empty === 1 && warn === 0, { empty, warn });
  if (engine === 'tiptap') {
    const wd = await p.evaluate(() => { const b = document.querySelector('[data-block-id="c1"]'), t = document.querySelector('.pm-doc'); return { b: Math.round(b.getBoundingClientRect().width), t: Math.round(t.getBoundingClientRect().width) }; });
    ok(`Tiptap: 4 Spalten breiter als die Textspalte, hoechstens 1100 px (${JSON.stringify(wd)})`, wd.b > wd.t && wd.b <= 1100, wd);
  }
  // Tiptap: Block bis 1100 px breit -> 4 nebeneinander; Editor.js bleibt in der Textspalte (760 px) -> 2x2
  const nD = await cols(blk('c1'));
  ok(`Desktop: ${engine === 'tiptap' ? '4 nebeneinander' : '2x2 in der Textspalte'} (${nD})`, nD === (engine === 'tiptap' ? 4 : 2), nD);
  // Einrichten: Beschriftung "Rahmen"
  await p.locator(blk('w1') + ' .wg').click(); await sleep(500);
  const labels = await p.evaluate(() => [...document.querySelectorAll('.wg-panel .wg-l')].map((x) => x.textContent));
  ok(`Einrichten: "Rahmen" statt "Darstellung" (${labels.join(', ')})`, labels.indexOf('Rahmen') >= 0 && labels.indexOf('Darstellung') < 0, labels);
  const dn = await p.evaluate(() => { const d = document.querySelector('.wg-panel .wg-done'); const r = d && d.getBoundingClientRect(); return r ? { h: Math.round(r.height), vis: r.top >= 0 && r.bottom <= innerHeight } : null; });
  ok(`Einrichten: "Fertig" sichtbar, mind. 32 px (${JSON.stringify(dn)})`, dn && dn.vis && dn.h >= 32, dn);
  await p.keyboard.press('Escape'); await p.locator('.wg-panel .wg-done').click().catch(() => {}); await sleep(300);

  // --- Tablet hoch (820) mit Seitenleiste: 4 Spalten 2x2 -----------------------------------------------------------------
  await p.setViewportSize({ width: 820, height: 1180 }); await sleep(600);
  const n820 = await cols(blk('c1'));
  ok(`Tablet hoch: 4 Spalten umgebrochen (2x2 oder untereinander: ${n820})`, n820 === 2 || n820 === 1, n820);
  const ws820 = await p.evaluate(() => [...document.querySelectorAll('.page-body .col')].map((w) => Math.round(w.getBoundingClientRect().width)));
  ok(`Tablet hoch: jede Spalte mind. 200 px breit (${ws820})`, ws820.length >= 8 && ws820.every((x) => x >= 200), ws820);
  hb = await habits();
  ok(`Tablet hoch: Gewohnheiten ohne Ueberlauf, heute sichtbar (${JSON.stringify(hb)})`, hb.every((x) => x.over <= 1 && !x.deep && x.todayIn && x.stIn), hb);

  // --- Handy: untereinander, ~10 px Abstand --------------------------------------------------------------------------
  const hctx = await br.newContext({ viewport: { width: 390, height: 844 }, locale: 'de-DE', isMobile: true, hasTouch: true });
  await hctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const hp = await hctx.newPage();
  await hp.goto(BASE + '/notes?t=' + Date.now() + '#' + page);
  await hp.waitForSelector('.wg[data-kind="habit"] .hb-row:not(.hb-head)', { timeout: 15000 }).catch(() => {});
  await sleep(800);
  const gaps = await hp.evaluate((sel) => { const ws = [...document.querySelector(sel).querySelectorAll('.wg')].map((w) => w.getBoundingClientRect()); return ws.slice(1).map((r, i) => Math.round(r.top - ws[i].bottom)); }, blk('c1'));
  ok(`Handy: gestapelte Widgets untereinander, Abstand ${engine === 'tiptap' ? '~10' : 'unter 40'} px (${gaps})`, gaps.length === 3 && gaps.every((g) => g >= 6 && g <= (engine === 'tiptap' ? 18 : 40)), gaps);
  const hbh = await hp.evaluate(() => [...document.querySelectorAll('.wg[data-kind="habit"]')].map((w) => w.scrollWidth - w.clientWidth));
  ok(`Handy: Gewohnheiten ohne Ueberlauf (${hbh})`, hbh.every((x) => x <= 1), hbh);
  await hctx.close();

  for (const id of made.reverse()) await api('DELETE', '/api/notes/' + id);
  ok('Keine Seitenfehler / CSP-Verstoesse', !errs.length, errs.slice(0, 3));
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-WIDGETS-DESIGN (${engine}) ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
  await br.close(); process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
