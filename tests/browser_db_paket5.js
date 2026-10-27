// Browser-Test Datenbanken 2.5 Paket 5 (wie tests/browser_formeln.js gegen einen laufenden Test-Server):
//   SAGA_BASE=http://127.0.0.1:8220 SAGA_USER=konto SAGA_PASS=... NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium node tests/browser_db_paket5.js
// Prueft im Web: Kalender-Woche (Umschalter Woche/Monat, Heute-Knopf, 7 Tage, Uhrzeit vor dem Namen, Zeitraum ueber
// mehrere Tage, Handy: Tage untereinander), Status-Board nach Gruppen (Spalten, Symbol, Karte in "Erledigt" ziehen ->
// erste Option der Gruppe), Paritaet relativer Filter Web = Server (next_days, overdue, last_week, on_or_after, status_group)
// bei Datum mit Uhrzeit/Zeitraum, Filterdialog (Wert-Eingabe fuer Tage), Datums-Editor mit Uhrzeit (Wert mit Offset der
// Ortszeit), Unteraufgaben verschachtelt + Aufklappen + "blockiert", Tabelle: Spalten fixieren (sticky) und Zeilenumbruch,
// Board/Galerie-Karten mit bedingter Farbe, einfache Tabelle: Kopfspalte/Farbe ueber das Block-Menue, Rundreise.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core'), ENGINE = process.env.PW_BROWSER || 'chromium';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 700))); }

async function session(browser, vp) {
  const ctx = await browser.newContext({ viewport: vp || { width: 1366, height: 860 }, locale: 'de-DE', timezoneId: 'Europe/Berlin' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { console.log('SEITENFEHLER', e.message); res.push(false); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USER);
  await p.fill('input[type=password]', PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.open = async (id, sel) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(sel || '.db-row, .dbl-row, .board-card, .db-new-row, .cal'); await p.waitForTimeout(700); };
  return p;
}
async function until(p, fn, arg, ms = 15000) {      // wie waitForFunction, aber ohne eval (Saga erlaubt kein 'unsafe-eval')
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(100)) if (await p.evaluate(fn, arg)) return true;
  return false;
}
function view(p, D, vid) { return p.evaluate(([D, vid]) => { localStorage.setItem('hk-db-view-' + D, vid); }, [D, vid]); }
function ymd(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }

(async () => {
  const browser = await PW[ENGINE].launch({ headless: true, channel: ENGINE === 'chromium' ? (process.env.PW_CHANNEL || 'chrome') : undefined });
  const made = [];
  let p = null;
  try {
    p = await session(browser);
    // Heute in Berlin (Browser) - fuer Daten und today/tz der Server-Abfrage
    const now = new Date(await p.evaluate(() => Date.now()));
    const today = await p.evaluate(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); });
    const t0 = new Date(+today.slice(0, 4), +today.slice(5, 7) - 1, +today.slice(8, 10));
    const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'P5 Web' })).id; made.push(D);
    let s = (await p.api('GET', '/api/notes/' + D)).schema;
    const st = s.props.find((x) => x.id === 'status');
    s.props = [st, { id: 'd', name: 'Wann', type: 'date', time: true, range: true }, { id: 'fertig', name: 'Fertig', type: 'checkbox' }];
    s.views = [{ id: 'tab', name: 'Tabelle', type: 'table' }, { id: 'brd', name: 'Board', type: 'board', group_by: 'status', group_status: true,
      colors: [{ pid: 'd', op: 'overdue', value: '', color: 'red' }] },
    { id: 'cal', name: 'Woche', type: 'calendar', date_by: 'd', range: 'week' }, { id: 'gal', name: 'Galerie', type: 'gallery', colors: [{ pid: 'd', op: 'overdue', value: '', color: 'red' }] }];
    ok('Schema mit Status, Datum (Uhrzeit/Zeitraum) gespeichert', (await p.api('PATCH', '/api/notes/' + D, { schema: s })).ok === true);
    const vals = [['Heute 9 Uhr', { status: 'todo', d: today + 'T09:00+0' + (now.getTimezoneOffset() === -60 ? '1' : '2') + ':00' }],
      ['Gestern', { status: 'doing', d: ymd(addDays(t0, -1)) }],
      ['Reise', { status: 'todo', d: ymd(addDays(t0, -1)) + '/' + ymd(addDays(t0, 2)) }],
      ['Erledigt alt', { status: 'done', d: ymd(addDays(t0, -10)) }],
      ['In 5 Tagen', { d: ymd(addDays(t0, 5)) }],
      ['NY abends', { status: 'todo', d: today + 'T20:00-05:00' }],
      ['Ohne Datum', { status: 'todo' }]];
    const ids = {};
    for (const [t, pr] of vals) ids[t] = (await p.api('POST', '/api/notes', { parent_id: D, title: t, props: pr })).id;

    // --- Kalender Woche
    await view(p, D, 'cal'); await p.open(D, '.cal');
    ok('Kalender: Woche mit 7 Tagen, kein Wochentagskopf', await p.evaluate(() => document.querySelectorAll('.cal-grid-week .cal-day').length === 7 && !document.querySelector('.cal-grid-week .cal-wd')));
    ok('Kalender Woche: heute markiert', await p.evaluate((t) => !!document.querySelector('.cal-day.today[data-day="' + t + '"]'), today));
    const hm = await p.evaluate((t) => { const c = document.querySelector('.cal-day[data-day="' + t + '"]'); return c ? [...c.querySelectorAll('.cal-item')].map((b) => b.textContent) : null; }, today);
    ok('Uhrzeit vor dem Namen (09:00), Ortszeit fuer NY-Termin (n. Tag 02:00 bzw. 03:00 fehlt heute)', hm && hm.some((x) => /^09:00\s*Heute 9 Uhr/.test(x)) && !hm.some((x) => /NY abends/.test(x)), hm);
    const span = await p.evaluate(() => [...document.querySelectorAll('.cal-item')].filter((b) => /Reise/.test(b.textContent)).map((b) => b.className));
    ok('Zeitraum erscheint auf jedem Tag (Fortsetzung markiert)', span.length >= 2 && span.some((c) => /cal-cont/.test(c)), span);
    await p.click('.cal-nav[aria-label="Nächste Woche"]');
    const t2 = await p.evaluate(() => document.querySelector('.cal-title').textContent);
    await p.click('.cal-today');
    ok('Naechste Woche / Heute-Knopf', await until(p, (t) => !!document.querySelector('.cal-day.today[data-day="' + t + '"]'), today) && /–/.test(t2), t2);
    await p.click('.cal-range[data-range="month"]');
    ok('Umschalter Monat: Monatsraster mit Wochentagskopf', await until(p, () => !!document.querySelector('.cal-grid:not(.cal-grid-week) .cal-wd')));
    const sv = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema.views.find((v) => v.id === 'cal');
    ok('Umschalter speichert range in der Ansicht (Monat = ohne range)', sv && !sv.range, sv);
    await p.click('.cal-range[data-range="week"]');
    ok('zurueck auf Woche', await until(p, () => !!document.querySelector('.cal-grid-week')));

    // --- Status-Board nach Gruppen
    await view(p, D, 'brd'); await p.open(D, '.board-card');
    const cols = await p.evaluate(() => [...document.querySelectorAll('.board-col .board-head')].map((h) => h.textContent));
    ok('Board: Spalten nach Status-Gruppe mit Symbol (○ ◐ ●)', cols.length === 4 && /Ohne/.test(cols[0]) && /○/.test(cols[1]) && /◐/.test(cols[2]) && /●/.test(cols[3]), cols);
    const red = await p.evaluate(() => [...document.querySelectorAll('.board-card.rc-red .board-title')].map((x) => x.textContent));
    ok('Board-Karten: bedingte Farbe (ueberfaellig rot)', red.length >= 2 && red.some((x) => /Gestern/.test(x)) && !red.some((x) => /In 5 Tagen/.test(x)), red);
    // Karte "Heute 9 Uhr" in die Spalte Erledigt ziehen (hkDrop direkt)
    await p.evaluate((id) => {
      const card = [...document.querySelectorAll('.board-card')].find((c) => /Heute 9 Uhr/.test(c.textContent));
      const dt = new DataTransfer(); card.dispatchEvent(new DragEvent('dragstart', { dataTransfer: dt, bubbles: true }));
      const col = [...document.querySelectorAll('.board-col')].find((c) => /●/.test(c.querySelector('.board-head').textContent));
      col.hkDrop({ preventDefault() {}, dataTransfer: dt });
    }, ids['Heute 9 Uhr']);
    ok('Karte in "Erledigt" gezogen -> Option der Gruppe done', await until(p, () => true) && (await (async () => { for (let i = 0; i < 30; i++) { const r = await p.api('GET', '/api/notes/' + ids['Heute 9 Uhr']); if (r.props && r.props.status === 'done') return true; await p.waitForTimeout(200); } return false; })()));
    const srvG = await p.api('GET', '/api/notes/' + D + '/rows?view=brd&tz=Europe/Berlin');
    const webG = await p.evaluate(() => [...document.querySelectorAll('.board-col')].map((c) => c.querySelectorAll('.board-card').length));
    ok('Paritaet Board nach Status-Gruppe: Server-Gruppen = Web-Spalten', JSON.stringify(srvG.groups.filter((g, i) => i > 0 || g.count).map((g) => g.count)) === JSON.stringify(webG), { srv: srvG.groups, webG });

    // --- Paritaet relativer Filter
    const F = [[{ pid: 'd', op: 'next_days', value: '7' }], [{ pid: 'd', op: 'overdue', value: '' }], [{ pid: 'd', op: 'last_week', value: '' }],
      [{ pid: 'd', op: 'on_or_after', value: 'today+1' }], [{ pid: 'status', op: 'status_group', value: 'open' }], [{ pid: 'd', op: 'today', value: '' }],
      [{ pid: 'd', op: 'eq', value: ymd(addDays(t0, 1)) }], [{ pid: 'd', op: 'yesterday', value: '' }], [{ pid: 'd', op: 'this_week', value: '' }, { pid: 'status', op: 'status_group', value: 'todo' }]];
    for (const fl of F) {
      s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
      s.views.find((v) => v.id === 'tab').filters = fl;
      s.views.find((v) => v.id === 'tab').sorts = [{ pid: 'd', dir: 'asc' }];
      await p.api('PATCH', '/api/notes/' + D, { schema: s });
      await view(p, D, 'tab'); await p.open(D, '.db-new-row');
      const web = await p.evaluate(() => [...document.querySelectorAll('tr.db-row .db-title-in')].map((x) => x.value));
      const srv = await p.api('GET', '/api/notes/' + D + '/rows?view=tab&tz=Europe/Berlin&today=' + today);
      ok('Paritaet Filter ' + fl.map((f) => f.op + (f.value ? ' ' + f.value : '')).join(' + ') + ': Web = Server (' + web.length + ')',
        JSON.stringify(web) === JSON.stringify(srv.rows.map((r) => r.title)), { web, srv: srv.rows.map((r) => r.title) });
    }
    // Filterdialog: Wert-Eingabe Tage
    s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    s.views.find((v) => v.id === 'tab').filters = [{ pid: 'd', op: 'next_days', value: '' }];
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    await p.open(D, '.db-new-row');
    await p.click('.db-filter');
    ok('Filterdialog: Operator „in den nächsten … Tagen“ mit Zahlenfeld', await until(p, () => !!document.querySelector('.menu:not([hidden]) .flt-days')));
    await p.fill('.menu:not([hidden]) .flt-days', '3');
    await p.keyboard.press('Enter');
    ok('Filter wirkt (3 Tage)', await until(p, () => { const t = [...document.querySelectorAll('tr.db-row .db-title-in')].map((x) => x.value); return t.length && t.indexOf('In 5 Tagen') < 0 && t.indexOf('Reise') >= 0; }));
    await p.keyboard.press('Escape');
    s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    delete s.views.find((v) => v.id === 'tab').filters;
    await p.api('PATCH', '/api/notes/' + D, { schema: s });

    // --- Datums-Editor mit Uhrzeit
    await p.open(D, '.db-new-row');
    await p.evaluate(() => { const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'In 5 Tagen'); tr.querySelector('.val-date .val-btn').click(); });
    ok('Datums-Editor oeffnet (Beginn, Ende, Mit Uhrzeit)', await until(p, () => !!document.querySelector('.menu:not([hidden]) .dt-box')));
    await p.evaluate(() => { const b = document.querySelector('.menu:not([hidden]) .dt-box'); const cb = b.querySelectorAll('input[type=checkbox]')[0]; if (!cb.checked) cb.click(); });
    await p.fill('.menu:not([hidden]) .dt-time >> nth=0', '14:30');
    await p.click('.menu:not([hidden]) .dt-foot .btn-primary');
    let v5 = null;
    for (let i = 0; i < 30 && !(v5 && v5.length > 10); i++) { v5 = ((await p.api('GET', '/api/notes/' + ids['In 5 Tagen'])).props || {}).d; await p.waitForTimeout(200); }
    ok('gespeichert mit Uhrzeit und Offset der Ortszeit', /^\d{4}-\d{2}-\d{2}T14:30\+0[12]:00$/.test(v5 || ''), v5);
    ok('Zelle zeigt Datum und Uhrzeit', await until(p, () => [...document.querySelectorAll('tr.db-row')].some((tr) => tr.querySelector('.db-title-in').value === 'In 5 Tagen' && /14:30/.test(tr.textContent))));
    // Uhrzeit wieder abwaehlen (gleicher Tag): muss wirklich weg sein (Server behaelt sonst den alten Wert wie bei alten Apps)
    await p.evaluate(() => { const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'In 5 Tagen'); tr.querySelector('.val-date .val-btn').click(); });
    await until(p, () => !!document.querySelector('.menu:not([hidden]) .dt-box'));
    await p.evaluate(() => { const cb = document.querySelector('.menu:not([hidden]) .dt-box').querySelectorAll('input[type=checkbox]')[0]; if (cb.checked) cb.click(); });
    await p.click('.menu:not([hidden]) .dt-foot .btn-primary');
    let v6 = v5;
    for (let i = 0; i < 30 && v6 && v6.length > 10; i++) { v6 = ((await p.api('GET', '/api/notes/' + ids['In 5 Tagen'])).props || {}).d; await p.waitForTimeout(200); }
    ok('Uhrzeit abgewaehlt -> nur noch der Tag', v6 === (v5 || '').slice(0, 10), [v5, v6]);

    // --- Unteraufgaben + blockiert
    s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    s.props.push({ id: 'par', name: 'Übergeordnet', type: 'relation', target: D, two_way: true, role: 'parent' },
      { id: 'blk', name: 'Blockiert durch', type: 'relation', target: D, two_way: true, role: 'blocked_by' });
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    s.views.find((v) => v.id === 'tab').sub = { parent: 'par', mode: 'nested' };
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    await p.api('PATCH', '/api/notes/' + ids['Gestern'], { props: { par: [ids['Reise']] } });
    await p.api('PATCH', '/api/notes/' + ids['NY abends'], { props: { par: [ids['Gestern']], blk: [ids['Ohne Datum']] } });
    await p.open(D, '.db-new-row');
    const tree = await p.evaluate(() => [...document.querySelectorAll('tr.db-row')].map((tr) => [tr.querySelector('.db-title-in').value, (tr.querySelector('.sub-ind') || { style: {} }).style.width, !!tr.querySelector('.db-blocked')]));
    const iR = tree.findIndex((x) => x[0] === 'Reise');
    ok('Unteraufgaben verschachtelt: Reise > Gestern > NY abends, eingerueckt', iR >= 0 && tree[iR + 1][0] === 'Gestern' && tree[iR + 2][0] === 'NY abends' && tree[iR + 2][1] === '2.2rem', tree);
    ok('„blockiert“-Hinweis an NY abends', tree.find((x) => x[0] === 'NY abends')[2] === true, tree);
    const srvT = await p.api('GET', '/api/notes/' + D + '/rows?view=tab&tz=Europe/Berlin');
    ok('Paritaet nested: Web-Reihenfolge = Server', JSON.stringify(tree.map((x) => x[0])) === JSON.stringify(srvT.rows.map((r) => r.title)), { web: tree.map((x) => x[0]), srv: srvT.rows.map((r) => r.title) });
    await p.evaluate(() => { const tr = [...document.querySelectorAll('tr.db-row')].find((x) => x.querySelector('.db-title-in').value === 'Reise'); tr.querySelector('.sub-tg').click(); });
    ok('Zuklappen blendet Unteraufgaben aus', await until(p, () => ![...document.querySelectorAll('tr.db-row .db-title-in')].some((x) => x.value === 'Gestern')));

    // --- Tabelle: fixieren + Umbruch
    s = (await p.api('GET', '/api/notes/' + D + '/rows?limit=1')).schema;
    Object.assign(s.views.find((v) => v.id === 'tab'), { freeze: 2, wrap: true });
    delete s.views.find((v) => v.id === 'tab').sub;
    await p.api('PATCH', '/api/notes/' + D, { schema: s });
    await p.setViewportSize({ width: 600, height: 800 });
    await p.open(D, '.db-new-row');
    const fz = await p.evaluate(() => { const tr = document.querySelector('tr.db-row'); return [...tr.children].slice(0, 3).map((c) => [getComputedStyle(c).position, c.style.left]); });
    ok('Spalten fixieren: erste 2 Spalten sticky mit left, dritte nicht', fz[0][0] === 'sticky' && fz[0][1] === '0px' && fz[1][0] === 'sticky' && parseInt(fz[1][1], 10) > 100 && fz[2][0] !== 'sticky', fz);
    ok('Zeilenumbruch: Tabelle mit db-wrap', await p.evaluate(() => document.querySelector('.db-table').classList.contains('db-wrap')));
    await p.setViewportSize({ width: 1366, height: 860 });

    // --- Galerie: Kartenfarbe
    await view(p, D, 'gal'); await p.open(D, '.dbg-card');
    ok('Galerie-Karten: bedingte Farbe', await until(p, () => document.querySelectorAll('.dbg-card.rc-red').length >= 2));

    // --- Handy: Woche als Tagesliste
    const m = await session(browser, { width: 390, height: 800 });
    await view(m, D, 'cal'); await m.open(D, '.cal');
    ok('Handy: Woche als Tage untereinander (eine Spalte)', await m.evaluate(() => getComputedStyle(document.querySelector('.cal-grid-week')).gridTemplateColumns.split(' ').length === 1));
    await m.close();

    // --- einfache Tabelle: Kopfspalte + Farbe ueber das Block-Menue, Rundreise
    const P = (await p.api('POST', '/api/notes', { title: 'Tabellen-Block' })).id; made.push(P);
    await p.api('PATCH', '/api/notes/' + P, { format: 'blocks', body: JSON.stringify({ blocks: [{ type: 'table', data: { withHeadings: true, content: [['A', 'B'], ['1', '2']], zukunft: 7 } }] }) });
    await p.goto(BASE + '/notes?t=' + Date.now() + '#' + P); await p.waitForSelector('.tc-table'); await p.waitForTimeout(800);
    await p.click('.tc-row:nth-child(2) .tc-cell:nth-child(2)');
    await p.waitForTimeout(200);
    await p.hover('.tc-row:nth-child(2) .tc-cell:nth-child(2)'); await p.click('.ce-toolbar__settings-btn');
    const hasItems = await until(p, () => [...document.querySelectorAll('.ce-settings .ce-popover-item')].some((x) => /Erste Spalte als Kopf/.test(x.textContent)), null, 5000);
    ok('Block-Menue: „Erste Spalte als Kopf“, „Zeile einfärben“', hasItems && await p.evaluate(() => [...document.querySelectorAll('.ce-settings .ce-popover-item')].some((x) => /Zeile einfärben/.test(x.textContent))));
    if (hasItems) {
      await p.evaluate(() => [...document.querySelectorAll('.ce-settings .ce-popover-item')].find((x) => /Erste Spalte als Kopf/.test(x.textContent)).click());
      ok('Kopfspalte sichtbar', await until(p, () => !!document.querySelector('.hk-tbl-hcol')));
      await p.click('.tc-row:nth-child(2) .tc-cell:nth-child(2)'); await p.waitForTimeout(150);
      await p.hover('.tc-row:nth-child(2) .tc-cell:nth-child(2)'); await p.click('.ce-toolbar__settings-btn');
      await until(p, () => [...document.querySelectorAll('.ce-settings .ce-popover-item')].some((x) => /Zeile einfärben/.test(x.textContent)), null, 5000);
      await p.evaluate(() => [...document.querySelectorAll('.ce-settings .ce-popover-item')].find((x) => /Zeile einfärben/.test(x.textContent)).click());
      await p.waitForTimeout(300);
      await p.evaluate(() => { const it = [...document.querySelectorAll('.ce-settings .ce-popover-item')].find((x) => /^\s*Blau\s*$/.test(x.textContent)); if (it) it.click(); });
      ok('Zeile 2 blau eingefaerbt', await until(p, () => document.querySelectorAll('.tc-row:nth-child(2) .tbl-bg-blue').length === 2));
      let saved = null;
      for (let i = 0; i < 40; i++) { const g = await p.api('GET', '/api/notes/' + P); saved = JSON.parse(g.body).blocks[0].data; if (saved.headCol && saved.colors) break; await p.waitForTimeout(250); }
      ok('gespeichert: headCol, colors.r[1]=blue, fremdes Feld bleibt', saved && saved.headCol === true && saved.colors && saved.colors.r && saved.colors.r['1'] === 'blue' && saved.zukunft === 7, saved);
    }
  } catch (e) {
    console.log('ABBRUCH', e && e.stack); res.push(false);
  } finally {
    if (p) for (const id of made) await p.api('DELETE', '/api/notes/' + id).catch(() => {});
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\n' + n + '/' + res.length + ' ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
